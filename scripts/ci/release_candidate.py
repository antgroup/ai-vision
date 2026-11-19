"""Freeze sanitized candidate acceptance; verify exact successful attempts before promotion."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re

import windows_release_cli

from product_registry import IMAGE_ROLES, require_product_repository
from release_assets import PERSONAL_ARCHITECTURES, file_sha256, release_asset_names
from release_personal import expected_receipts
from release_publish import api, validate_bundle

BACKENDS = ("oceanbase", "seekdb")
SMOKE_PLATFORM = "linux/amd64"
SMOKE_COMPONENTS = {"database", *IMAGE_ROLES.values()}


def asset_inventory(directory, manifest, *, base_only=False):
    if {p.name for p in directory.iterdir()} != release_asset_names(manifest, base_only=base_only) or any(
        p.is_symlink() or not p.is_file() or not p.stat().st_size for p in directory.iterdir()
    ):
        raise ValueError("candidate must contain exactly the allowed nonempty release assets")
    return {p.name: {"size": p.stat().st_size, "sha256": file_sha256(p)}
            for p in sorted(directory.iterdir())}


def smoke_identity(directory, backend):
    if backend not in BACKENDS:
        raise ValueError("candidate smoke requires a supported database")
    manifest = json.loads((directory / "release-manifest.json").read_text())
    require_product_repository(manifest.get("source_repository"))
    return {"schema_version": 1, "status": "passed", "backend": backend,
            "platform": SMOKE_PLATFORM, "source_sha": manifest["source_sha"],
            "build_id": manifest["build_id"], "product_version": manifest["product_version"],
            "manifest_sha256": hashlib.sha256((directory / "release-manifest.json").read_bytes()).hexdigest(),
            "initial_pinned_content_verified": True, "recast_pinned_content_verified": True}


def sanitize_smoke(summary, directory, backend):
    if backend not in BACKENDS:
        raise ValueError("candidate smoke requires a supported database")
    if not isinstance(summary, dict) or summary.get("status") != "passed" or summary.get("backend") != backend:
        raise ValueError("only an actual passed pinned initial/recast smoke can issue a receipt")
    manifest = json.loads((directory / "release-manifest.json").read_text())
    require_product_repository(manifest.get("source_repository"))
    expected_refs = {role: manifest["images"][component] for component, role in IMAGE_ROLES.items()}
    expected_refs["database"] = manifest["databases"][backend]
    for phase in ("initial_images", "recast_images"):
        measured_images = summary.get(phase)
        if not isinstance(measured_images, dict) or measured_images.get("pinned_content_verified") is not True:
            raise ValueError("only an actual passed pinned initial/recast smoke can issue a receipt")
        platforms = measured_images.get("platforms")
        if not isinstance(platforms, dict) or set(platforms) != SMOKE_COMPONENTS:
            raise ValueError("candidate smoke requires complete measured image platforms for " + phase)
        for measured in platforms.values():
            if not isinstance(measured, str) or not re.fullmatch(
                    re.escape(SMOKE_PLATFORM) + r" sha256:[0-9a-f]{64}", measured):
                raise ValueError("candidate smoke image platform or identity is invalid for " + phase)
        if measured_images.get("references") != expected_refs:
            raise ValueError("candidate smoke image references differ from the release manifest for " + phase)
    if summary["initial_images"]["platforms"] != summary["recast_images"]["platforms"]:
        raise ValueError("candidate smoke measured image identities changed during recast")
    # Deliberately construct an allowlist. Raw logs, paths and runtime config never
    # enter acceptance artifacts, even when the smoke summary contains them.
    return smoke_identity(directory, backend)


def freeze(directory, receipts, *, repository, source_sha, build_id, base_only=False, personal=None, windows_cli=None):
    plan, _ = validate_bundle(directory, source_sha, build_id, repository=repository, base_only=base_only)
    expected_smokes = {backend: smoke_identity(directory, backend) for backend in BACKENDS}
    if receipts != expected_smokes:
        raise ValueError("acceptance requires both exact passed candidate smoke receipts")
    manifest = json.loads((directory / "release-manifest.json").read_text())
    assets = asset_inventory(directory, manifest, base_only=base_only)
    acceptance = {"schema_version": 1, "status": "qualified", "source_repository": repository,
                  "source_sha": source_sha, "build_id": build_id,
                  "product_version": plan["product_version"], "release_tag": plan["release_tag"],
                  "assets": assets, "smokes": expected_smokes}
    if base_only:
        if not manifest.get("personal_edition") or personal is not None:
            raise ValueError("only a Personal Edition candidate can use base-only acceptance")
        acceptance["phase"] = "base"
    elif manifest.get("personal_edition"):
        expected = expected_receipts(directory, assets, manifest)
        if personal != expected:
            raise ValueError("qualification requires both exact native Personal Edition receipts")
        acceptance["personal_edition"] = expected
    elif personal is not None:
        raise ValueError("unexpected Personal Edition receipts")
    if windows_release_cli.included(manifest):
        expected_windows = windows_release_cli.receipt_identity(
            directory / windows_release_cli.ASSET, source_sha=source_sha,
            build_id=build_id, product_version=plan["product_version"],
        )
        if json.dumps(windows_cli, sort_keys=True) != json.dumps(expected_windows, sort_keys=True):
            raise ValueError("acceptance requires exact Windows x64 installation evidence")
        acceptance["windows_cli"] = expected_windows
    elif windows_cli is not None:
        raise ValueError("Windows CLI evidence requires a candidate with the Windows archive")
    return acceptance


def verify_frozen(directory, acceptance, *, repository, source_sha, build_id, base_only=False):
    if not isinstance(acceptance, dict) or acceptance != freeze(
        directory, acceptance.get("smokes"), repository=repository, source_sha=source_sha,
        build_id=build_id, base_only=base_only, personal=acceptance.get("personal_edition"),
        windows_cli=acceptance.get("windows_cli"),
    ):
        raise ValueError("acceptance differs from frozen source, attempt or asset bytes")


def verify_remote_candidate(directory, acceptance, *, repository, source_sha,
                            candidate_run_id, candidate_run_attempt, api_fn=api,
                            allow_dev_recovery=False):
    require_product_repository(repository)
    if not all(isinstance(value, str) and re.fullmatch(r"[1-9][0-9]*", value)
               for value in (candidate_run_id, candidate_run_attempt)):
        raise ValueError("candidate run and attempt must be canonical positive integers")
    build_id = f"{candidate_run_id}.{candidate_run_attempt}"
    plan = json.loads((directory / "release-plan.json").read_text())
    if plan.get("channel") not in {"daily", "dev"} or plan.get("alias") != plan["channel"]:
        raise ValueError("manual promotion accepts only default-branch Daily/Dev candidates")
    verify_frozen(directory, acceptance, repository=repository, source_sha=source_sha, build_id=build_id)
    repo = api_fn(f"repos/{repository}")
    branch = repo.get("default_branch")
    if (repo.get("full_name") != repository or repo.get("visibility") != "public"
            or not isinstance(branch, str) or not branch):
        raise ValueError("cannot identify the official public repository and current default branch")
    from urllib.parse import quote
    if api_fn(f"repos/{repository}/commits/{quote(branch, safe='')}").get("sha") != source_sha:
        raise ValueError("candidate source is stale against the current default branch head")
    base = f"repos/{repository}/actions/runs/{candidate_run_id}"
    # A newer attempt supersedes old artifacts, even when an old attempt passed.
    latest = api_fn(base)
    attempt = api_fn(f"{base}/attempts/{candidate_run_attempt}")
    publication_recovery = (allow_dev_recovery and plan["channel"] == "dev"
                            and latest.get("conclusion") == attempt.get("conclusion") == "failure")
    for run in (latest, attempt):
        if (type(run.get("id")) is not int or run["id"] != int(candidate_run_id)
                or type(run.get("run_attempt")) is not int or run["run_attempt"] != int(candidate_run_attempt)
                or run.get("head_sha") != source_sha or run.get("head_branch") != branch
                or run.get("path") != ".github/workflows/release-images.yml"
                or run.get("event") != "workflow_dispatch"
                or run.get("repository", {}).get("full_name") != repository
                or run.get("head_repository", {}).get("full_name") != repository
                or run.get("status") != "completed"
                or run.get("conclusion") != ("failure" if publication_recovery else "success")):
            raise ValueError("candidate is not the exact successful official workflow attempt")
    jobs, page = [], 1
    while True:
        result = api_fn(f"{base}/attempts/{candidate_run_attempt}/jobs?per_page=100&page={page}")
        batch = result.get("jobs")
        if not isinstance(batch, list):
            raise ValueError("candidate jobs response is invalid")
        jobs.extend(batch)
        if len(batch) < 100:
            if result.get("total_count") != len(jobs):
                raise ValueError("candidate job listing is incomplete")
            break
        page += 1
    required_jobs = [*(f"Linux amd64 candidate deployment ({backend})" for backend in BACKENDS),
                     "freeze qualified candidate acceptance", "verify public candidate indexes anonymously"]
    qualified_smokes = [f"linux/amd64/{backend}" for backend in BACKENDS]
    if "windows_cli" in acceptance:
        qualified_smokes.append("windows/amd64/cli")
        required_jobs.append("cli / qualify Windows x64")
    if acceptance.get("personal_edition"):
        required_jobs.extend(f"Personal Edition (linux/{arch})" for arch in PERSONAL_ARCHITECTURES)
        required_jobs.append("freeze qualified Personal Edition acceptance")
        qualified_smokes.extend(f"linux/{arch}/personal" for arch in PERSONAL_ARCHITECTURES)
    for name in required_jobs:
        matched = [job for job in jobs if job.get("name") == name]
        if len(matched) != 1 or any(
            job.get("run_id") != int(candidate_run_id) or job.get("run_attempt") != int(candidate_run_attempt)
            or job.get("head_sha") != source_sha or job.get("status") != "completed"
            or job.get("conclusion") != "success" for job in matched
        ):
            raise ValueError(f"candidate lacks a successful {name} job in the exact attempt")
    if publication_recovery:
        publishers = [job for job in jobs if job.get("name") == "publish verified development artifacts"]
        if (len(publishers) != 1 or publishers[0].get("conclusion") != "failure"
                or any(job.get("run_id") != int(candidate_run_id)
                       or job.get("run_attempt") != int(candidate_run_attempt)
                       or job.get("head_sha") != source_sha or job.get("status") != "completed"
                       or (job.get("conclusion") not in {"success", "skipped"} and job is not publishers[0])
                       for job in jobs)):
            raise ValueError("Dev recovery requires a publication-only failure after successful qualification")
    return {"schema_version": 1, "candidate_build_id": build_id,
            "candidate_source_sha": source_sha, "source_repository": repository,
            "publication_recovery": publication_recovery,
            "qualified_smokes": qualified_smokes}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("smoke", "freeze", "verify"))
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--backend", choices=BACKENDS)
    parser.add_argument("--receipts", type=Path)
    parser.add_argument("--base-only", action="store_true")
    parser.add_argument("--personal-receipts", type=Path)
    parser.add_argument("--acceptance", type=Path)
    parser.add_argument("--candidate-run-id")
    parser.add_argument("--candidate-run-attempt")
    parser.add_argument("--recover-dev-publication", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.command == "smoke":
        value = sanitize_smoke(json.loads(args.summary.read_text()), args.directory, args.backend)
    elif args.command == "freeze":
        receipts = {backend: json.loads((args.receipts / f"smoke-{backend}.json").read_text()) for backend in BACKENDS}
        personal = None
        if args.personal_receipts:
            personal = {arch: json.loads((args.personal_receipts / f"personal-{arch}.json").read_text())
                        for arch in PERSONAL_ARCHITECTURES}
        windows_path = args.receipts / windows_release_cli.RECEIPT
        windows_cli = json.loads(windows_path.read_text()) if windows_path.is_file() else None
        value = freeze(args.directory, receipts, base_only=args.base_only, personal=personal,
                       windows_cli=windows_cli,
                       repository=os.environ["GITHUB_REPOSITORY"],
                       source_sha=os.environ["GITHUB_SHA"],
                       build_id=f"{os.environ['GITHUB_RUN_ID']}.{os.environ['GITHUB_RUN_ATTEMPT']}")
    else:
        value = verify_remote_candidate(args.directory, json.loads(args.acceptance.read_text()),
                    repository=os.environ["GITHUB_REPOSITORY"], source_sha=os.environ["GITHUB_SHA"],
                    candidate_run_id=args.candidate_run_id, candidate_run_attempt=args.candidate_run_attempt,
                    allow_dev_recovery=args.recover_dev_publication)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(value, indent=2) + "\n")
    else:
        print(json.dumps(value, indent=2))


if __name__ == "__main__":
    main()
