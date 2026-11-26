"""Publish only a verified release manifest; never rebuild during promotion."""

import argparse
import base64
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.parse import urlencode

import windows_release_cli

from product_registry import (
    PUBLIC_REPOSITORY,
    product_image_repositories,
    require_product_repository,
    validate_data_backend_mirror,
)
import public_registry
from release_assets import DATABASES, IMAGE_VARS, file_sha256, release_asset_names
from release_reuse import validate_build
from release_plan import (
    IMAGES,
    require_digest,
    require_draft_notes,
    require_release_environment,
)


# The public ci history predates the imported develop history. Authenticate the
# last ci Daily before allowing its rolling alias to move to this develop line.
LAST_CI_DAILY_TAG = "v0.6.0-daily.20261123.2"
LAST_CI_DAILY_SHA = "d4f81c376bc4630f51a87090e39afc11afe67135"
DEVELOP_DAILY_BASELINE = "0e8cbe356f4001fe44fd3c2d359f06cedd348e4e"


def run(*args, input=None):
    return subprocess.check_output(args, input=input, text=True).strip()


def api(path, *, data=None, missing=False, method="PATCH"):
    command = ["gh", "api", path]
    if data is not None:
        command += ["--method", method, "--input", "-"]
    elif method != "PATCH":
        command += ["--method", method]
    result = subprocess.run(
        command,
        input=json.dumps(data) if data is not None else None,
        text=True,
        capture_output=True,
    )
    if result.returncode:
        if missing and "(HTTP 404)" in result.stderr:
            return None
        raise RuntimeError(
            f"GitHub API request failed: {path}: {result.stderr.strip()}"
        )
    return json.loads(result.stdout) if result.stdout.strip() else None


def validate_bundle(directory, source_sha, build_id, *, repository=PUBLIC_REPOSITORY, base_only=False):
    repositories = product_image_repositories(repository)
    plan = json.loads((directory / "release-plan.json").read_text())
    manifest = json.loads((directory / "release-manifest.json").read_text())
    allowed_assets = release_asset_names(manifest, base_only=base_only)
    if plan.get("personal_edition", False) is not manifest.get("personal_edition", False):
        raise ValueError("release manifest Personal Edition scope does not match its plan")
    for key in ("source_sha", "product_version", "build_id", "channel"):
        if manifest[key] != plan[key]:
            raise ValueError("release manifest does not match its build plan")
    if plan["source_sha"] != source_sha or plan["build_id"] != build_id:
        raise ValueError("release source or attempt differs; rerun all jobs")
    if manifest.get("release_tag") != plan["release_tag"] or manifest.get("source_repository") != repository:
        raise ValueError("release manifest requires the official source repository and immutable release tag")
    if {path.name for path in directory.iterdir()} != allowed_assets or any(
        not path.is_file() or path.is_symlink() or not path.stat().st_size
        for path in directory.iterdir()
    ):
        raise ValueError("release directory must contain exactly the allowed product assets")
    if windows_release_cli.included(manifest):
        windows_release_cli.verify_zip(directory / windows_release_cli.ASSET)
    images = manifest["images"]
    if not isinstance(images, dict) or set(images) != set(repositories) or manifest["databases"] != DATABASES:
        raise ValueError(
            "release manifest must identify the expected service and database images"
        )
    for ref in [*images.values(), *manifest["databases"].values()]:
        require_digest(ref)
    for name, ref in images.items():
        if ref.split("@", 1)[0] != repositories[name]:
            raise ValueError("release service images must use their official component packages")
    validate_data_backend_mirror(manifest.get("data_backend_mirror"), images["signoz-ob"])
    if set(manifest.get("component_builds", {})) != set(IMAGES) or set(manifest.get("component_inputs", {})) != set(IMAGES):
        raise ValueError("release manifest requires actual builds and context inputs for all owned components")
    for name in IMAGES:
        validate_build(name, manifest["component_builds"][name])
        if not re.fullmatch(r"[a-f0-9]{64}", manifest["component_inputs"][name]):
            raise ValueError("component context fingerprint must be SHA256")
    for backend, database in DATABASES.items():
        filename = (
            "release-images.vars.yaml"
            if backend == "oceanbase"
            else "release-seekdb.vars.yaml"
        )
        values = {}
        for line in (directory / filename).read_text().splitlines():
            if line and not line.startswith("#"):
                key, value = line.split(": ", 1)
                if key in values:
                    raise ValueError("duplicate deployment variable")
                values[key] = json.loads(value)
        expected = {key: images[name] for name, key in IMAGE_VARS.items()}
        expected.update(OB_BACKEND=backend, OB_IMAGE=database,
            AIVISION_RELEASE_MANIFEST_BASE64=base64.b64encode(
                json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
            ).decode())
        if values != expected:
            raise ValueError(
                "deployment variables differ from the tested image manifest"
            )
    entries = {}
    for line in (directory / "SHA256SUMS").read_text().splitlines():
        checksum, filename = line.split("  ", 1)
        if (
            filename in entries
            or Path(filename).name != filename
            or not re.fullmatch(r"[a-f0-9]{64}", checksum)
        ):
            raise ValueError("invalid or duplicate release checksum entry")
        entries[filename] = checksum
    actual_files = {
        path.name for path in directory.iterdir() if path.name != "SHA256SUMS"
    }
    if set(entries) != actual_files:
        raise ValueError("checksums must cover every release asset exactly once")
    for filename, checksum in entries.items():
        if file_sha256(directory / filename) != checksum:
            raise ValueError(f"release checksum mismatch: {filename}")
    return plan, images


def inspect_digest(ref, *, missing=False):
    result = subprocess.run(
        ["docker", "buildx", "imagetools", "inspect", ref,
         "--format", "{{json .Manifest}}"],
        text=True,
        capture_output=True,
    )
    if result.returncode:
        message = (result.stdout + result.stderr).lower()
        if missing and not any(
            marker in message
            for marker in ("unauthorized", "denied", "forbidden", "timeout")
        ) and any(marker in message for marker in ("manifest unknown", "not found")):
            return None
        raise RuntimeError(f"could not inspect {ref}: {message.strip()}")
    digest = json.loads(result.stdout).get("digest", "")
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
        raise ValueError(f"registry returned an invalid image digest: {ref}")
    return digest


def version_targets(plan, images, *, repository=PUBLIC_REPOSITORY):
    repositories = product_image_repositories(repository)
    for name, package in repositories.items():
        if require_digest(images[name]).split("@", 1)[0] != package:
            raise ValueError("release service images must use their official component packages")
    return {f"{package}:{plan['product_version']}": images[name]
            for name, package in repositories.items()}


def check_version_tags(plan, images, *, repository=PUBLIC_REPOSITORY):
    """Read every immutable destination before any publication write."""
    missing = []
    for target, expected in version_targets(plan, images, repository=repository).items():
        digest = inspect_digest(target, missing=True)
        if digest is None:
            missing.append(target)
        elif digest != expected.split("@", 1)[1]:
            raise ValueError(f"refusing to overwrite conflicting release image: {target}")
    return missing


def publish_version_tags(plan, images, *, repository=PUBLIC_REPOSITORY):
    targets = version_targets(plan, images, repository=repository)
    missing = check_version_tags(plan, images, repository=repository)
    for target in missing:
        # Another publisher may have reconciled this target since preflight.
        digest = inspect_digest(target, missing=True)
        expected = targets[target]
        if digest is None:
            run("docker", "buildx", "imagetools", "create", "--tag", target, expected)
        elif digest != expected.split("@", 1)[1]:
            raise ValueError(f"refusing to overwrite conflicting release image: {target}")
        public_registry.verify_public_tag(target, expected)
    # Existing identical tags are a no-op, but all images are read back before
    # any rolling alias or published Release can identify this version.
    for target, expected in targets.items():
        public_registry.verify_public_tag(target, expected)


def require_newer_rolling(existing, build_id, *, allow_same=False):
    if existing.get("immutable"):
        raise ValueError("rolling Release is immutable")
    markers = re.findall(
        r"<!-- aivision-build: ([1-9][0-9]*)\.([1-9][0-9]*) -->",
        existing.get("body") or "",
    )
    if len(markers) != 1:
        raise ValueError("rolling Release has unknown or ambiguous build provenance")
    current = tuple(map(int, markers[0]))
    incoming = tuple(map(int, build_id.split(".")))
    if incoming < current or (incoming == current and not allow_same):
        raise ValueError("refusing to replace a newer rolling workflow attempt")
    return incoming == current


def require_live_tag(repository, tag, source_sha):
    require_product_repository(repository)
    target = api(f"repos/{repository}/git/ref/tags/{tag}")["object"]
    # Lightweight and annotated tags must resolve to the event's exact commit.
    for _ in range(8):
        if target.get("type") == "commit":
            if target.get("sha") != source_sha:
                raise ValueError("release tag moved away from the tested source commit")
            return
        if target.get("type") != "tag" or not re.fullmatch(
            r"[a-f0-9]{40}", target.get("sha", "")
        ):
            raise ValueError("release tag does not identify a commit")
        target = api(f"repos/{repository}/git/tags/{target['sha']}")["object"]
    raise ValueError("release tag has excessive annotated-tag nesting")


def preflight(plan, images, repository):
    require_product_repository(repository)
    require_release_environment(api(f"repos/{repository}/environments/release"))
    require_live_tag(repository, plan["release_tag"], plan["source_sha"])
    if plan["channel"] == "stable":
        latest = api(f"repos/{repository}/releases/latest", missing=True)
        if latest:
            tag = latest["tag_name"]
            if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", tag):
                raise ValueError(
                    "latest Release has an unrecognized version; reconcile it before publishing"
                )
            if tuple(map(int, tag[1:].split("."))) >= tuple(
                map(int, plan["product_version"].split("."))
            ):
                raise ValueError("refusing to replace a newer or equal stable Release")
    check_version_tags(plan, images, repository=repository)


def read_draft(repository, tag, release_id):
    release = api(f"repos/{repository}/releases/{release_id}")
    if (not isinstance(release, dict)
            or type(release.get("id")) is not int or release["id"] != release_id
            or release.get("tag_name") != tag or release.get("draft") is not True
            or release.get("immutable")):
        raise ValueError("draft Release identity differs from the reviewed version")
    return release


def find_release_draft(repository, tag):
    require_product_repository(repository)
    # The tag lookup is for published Releases. The protected publisher has
    # push access and can see drafts in the repository's paginated listing.
    matches = []
    page = 1
    while True:
        releases = api(f"repos/{repository}/releases?per_page=100&page={page}")
        if not isinstance(releases, list):
            raise ValueError("repository releases response must be a list")
        matches.extend(release for release in releases if release.get("tag_name") == tag)
        if len(releases) < 100:
            break
        page += 1
    if (len(matches) != 1 or matches[0].get("draft") is not True
            or type(matches[0].get("id")) is not int or matches[0]["id"] <= 0):
        raise ValueError(f"expected exactly one draft Release for {tag}")
    draft = read_draft(repository, tag, matches[0]["id"])
    if draft.get("assets") != []:
        raise ValueError("prepare a draft Release with no assets")
    require_draft_notes(draft)
    return draft


def upload_draft_assets(repository, release_id, assets):
    for asset in assets:
        query = urlencode({"name": Path(asset).name})
        run("gh", "api",
            f"https://uploads.github.com/repos/{repository}/releases/{release_id}/assets?{query}",
            "--method", "POST", "--input", asset, "-H", "Content-Type: application/octet-stream")


def rolling_versioned_tag(existing, repository):
    previous = re.findall(
        rf"\[Versioned assets and exact image manifest\]"
        rf"\(https://github\.com/{re.escape(repository)}/releases/tag/(v[0-9][A-Za-z0-9_.-]*)\)",
        existing.get("body") or "",
    )
    if len(previous) != 1:
        raise ValueError("rolling notes require one previous versioned-asset link; reconcile it first")
    return previous[0]


def require_same_dev_candidate(existing, plan, repository):
    """An equal build marker alone cannot authorize clobbering rolling assets."""
    sources = [value.rstrip() for value in re.findall(r"^Source: (.*)$", existing.get("body") or "", re.MULTILINE)]
    if (existing.get("tag_name") != "dev" or existing.get("draft") is not False
            or type(existing.get("id")) is not int or existing["id"] <= 0
            or rolling_versioned_tag(existing, repository) != plan["release_tag"]
            or sources != [f"`{plan['source_sha']}`"]):
        raise ValueError("Dev recovery provenance differs from the frozen candidate")
    require_live_tag(repository, "dev", plan["source_sha"])


def require_unchanged_dev_release(existing, repository):
    require_product_repository(repository)
    current = api(f"repos/{repository}/releases/tags/dev")
    identity = ("id", "tag_name", "body", "draft", "immutable")
    if not current or any(current.get(key) != existing.get(key) for key in identity):
        raise ValueError("Dev recovery provenance changed during publication")
    return current


def generate_rolling_notes(plan, repository, existing, *, daily_history_cutover=False):
    require_product_repository(repository)
    previous_tag = rolling_versioned_tag(existing, repository) if existing else None
    if daily_history_cutover:
        if plan.get("channel") != "daily" or plan.get("alias") != "daily" or previous_tag != LAST_CI_DAILY_TAG:
            raise ValueError("Daily history migration requires the verified legacy Daily release")
        return (
            "## Daily history migration\n\n"
            "This Daily begins the release history from `develop`. "
            f"[Download the last Daily from `ci`](https://github.com/{repository}/releases/tag/{previous_tag})."
        )
    payload = {"tag_name": plan["release_tag"], "target_commitish": plan["source_sha"]}
    if previous_tag:
        # A mutable daily/dev tag would make the generated changelog link
        # change its range after promotion. Use its versioned asset release.
        payload["previous_tag_name"] = previous_tag
    generated = api(f"repos/{repository}/releases/generate-notes", data=payload, method="POST")
    body = generated.get("body") if isinstance(generated, dict) else None
    if not isinstance(body, str) or not body.strip():
        raise ValueError("GitHub generated release notes are empty")
    return body.strip()


def remote_release_assets(repository, release):
    require_product_repository(repository)
    assets = []
    page = 1
    while True:
        batch = api(f"repos/{repository}/releases/{release['id']}/assets?per_page=100&page={page}")
        if not isinstance(batch, list):
            raise ValueError("remote release assets response must be a list")
        assets.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return assets


def read_remote_metadata(repository, asset):
    """Download small provenance files with the same authenticated asset API."""
    require_product_repository(repository)
    if (type(asset.get("id")) is not int or asset["id"] <= 0
            or asset.get("state") != "uploaded" or type(asset.get("size")) is not int
            or not 0 < asset["size"] <= 8 * 1024 * 1024):
        raise ValueError("remote provenance asset identity, size or state is invalid")
    with tempfile.TemporaryFile() as downloaded:
        result = subprocess.run(["gh", "api", f"repos/{repository}/releases/assets/{asset['id']}",
            "-H", "Accept: application/octet-stream"], stdout=downloaded, stderr=subprocess.PIPE)
        if result.returncode:
            raise RuntimeError(f"authenticated provenance download failed: {asset['name']}")
        downloaded.seek(0)
        content = downloaded.read(8 * 1024 * 1024 + 1)
    if len(content) != asset["size"]:
        raise ValueError("remote provenance asset bytes differ from its recorded size")
    return content


def daily_version_order(version):
    """Order canonical Daily versions by numeric base, snapshot and revision."""
    match = re.fullmatch(
        r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
        r"-daily\.([0-9]{8})(?:\.([2-9]|[1-9][0-9]+))?", version
    ) if isinstance(version, str) else None
    if not match:
        raise ValueError("published Daily has an unrecognized version")
    major, minor, patch, snapshot, revision = match.groups()
    datetime.strptime(snapshot, "%Y%m%d")
    return tuple(map(int, (major, minor, patch, snapshot, revision or "1")))


def require_newer_daily(existing, plan, repository, *, rolling_source_sha=None):
    """Return the authenticated previous Daily version tag and source commit."""
    require_product_repository(repository)
    if (existing.get("tag_name") != "daily" or existing.get("draft") is not False
            or type(existing.get("id")) is not int or existing["id"] <= 0):
        raise ValueError("Daily requires known published rolling Release provenance")
    tag = rolling_versioned_tag(existing, repository)
    current_order = daily_version_order(tag[1:])
    previous = api(f"repos/{repository}/releases/tags/{tag}", missing=True)
    if (not previous or previous.get("tag_name") != tag or previous.get("draft") is not False
            or not previous.get("published_at") or type(previous.get("id")) is not int
            or previous["id"] <= 0):
        raise ValueError("Daily requires its published versioned Release provenance")
    assets = remote_release_assets(repository, previous)
    by_name = {asset.get("name"): asset for asset in assets}
    if len(by_name) != len(assets) or not {"release-manifest.json", "SHA256SUMS"} <= set(by_name):
        raise ValueError("Daily versioned manifest provenance is missing or ambiguous")
    payload = read_remote_metadata(repository, by_name["release-manifest.json"])
    checksums = read_remote_metadata(repository, by_name["SHA256SUMS"]).decode().splitlines()
    matches = [line.split("  ", 1)[0] for line in checksums if line.endswith("  release-manifest.json")]
    if matches != [hashlib.sha256(payload).hexdigest()]:
        raise ValueError("Daily versioned manifest checksum does not match")
    manifest = json.loads(payload)
    if (not isinstance(manifest, dict) or manifest.get("source_repository") != repository
            or manifest.get("release_tag") != tag or manifest.get("product_version") != tag[1:]
            or manifest.get("channel") != "daily"
            or not isinstance(manifest.get("source_sha"), str)
            or not re.fullmatch(r"[a-f0-9]{40}", manifest["source_sha"])
            or not isinstance(manifest.get("build_id"), str)
            or not re.fullmatch(r"[1-9][0-9]*\.[1-9][0-9]*", manifest["build_id"])
            or not require_newer_rolling(existing, manifest["build_id"], allow_same=True)):
        raise ValueError("Daily versioned manifest differs from its rolling provenance")
    require_live_tag(repository, tag, manifest["source_sha"])
    require_live_tag(repository, "daily", rolling_source_sha or manifest["source_sha"])
    if daily_version_order(plan["product_version"]) < current_order:
        raise ValueError("refusing to replace a newer published Daily version")
    return tag, manifest["source_sha"]


def require_known_dev_assets(directory, repository, existing):
    names = [asset.get("name") for asset in remote_release_assets(repository, existing)]
    if len(names) != len(set(names)) or not set(names) <= {path.name for path in directory.iterdir()}:
        raise ValueError("Dev has duplicate or unknown assets outside the frozen candidate")


def verify_remote_assets(directory, plan, repository, *, release_id=None, draft=False):
    """Authenticated download and byte verification, including the checksum file."""
    tag = plan["release_tag"]
    require_live_tag(repository, tag, plan["source_sha"])
    if draft:
        release = read_draft(repository, tag, release_id)
    else:
        release = api(f"repos/{repository}/releases/tags/{tag}")
        if (not isinstance(release, dict)
                or release.get("tag_name") != tag or type(release.get("id")) is not int
                or (release_id is not None and
                    (release["id"] != release_id or release.get("draft") is not False))):
            raise ValueError("remote release identity differs from the frozen release")
    assets = remote_release_assets(repository, release)
    expected = {path.name: path for path in directory.iterdir()}
    names = [asset.get("name") for asset in assets]
    if len(names) != len(set(names)) or set(names) != set(expected):
        raise ValueError("remote release asset set differs from frozen assets")
    verified = {}
    for asset in assets:
        path = expected[asset["name"]]
        if (type(asset.get("id")) is not int or asset["id"] <= 0
                or asset.get("state") != "uploaded"
                or type(asset.get("size")) is not int or asset["size"] != path.stat().st_size):
            raise ValueError("remote release asset size or state differs from frozen assets")
        with tempfile.TemporaryFile() as downloaded:
            command = ["gh", "api", f"repos/{repository}/releases/assets/{asset['id']}",
                       "-H", "Accept: application/octet-stream"]
            result = subprocess.run(command, stdout=downloaded, stderr=subprocess.PIPE)
            if result.returncode:
                raise RuntimeError(f"authenticated asset download failed: {asset['name']}")
            downloaded.seek(0)
            digest = hashlib.sha256()
            size = 0
            with path.open("rb") as local:
                while chunk := downloaded.read(1024 * 1024):
                    size += len(chunk)
                    digest.update(chunk)
                    if chunk != local.read(len(chunk)):
                        raise ValueError(f"remote release asset bytes differ: {asset['name']}")
                if local.read(1) or size != asset["size"]:
                    raise ValueError(f"remote release asset bytes differ: {asset['name']}")
            verified[asset["name"]] = {"size": size, "sha256": digest.hexdigest()}
    return {"release_id": release["id"], "release_tag": plan["release_tag"], "assets": verified}


def recover_daily_metadata(directory, plan, images, repository, verify_candidate):
    """Repair only the Release edit after the immutable and rolling tags landed."""
    root = f"repos/{repository}"
    endpoint = f"{root}/releases/tags/daily"
    existing = api(endpoint)
    if not isinstance(existing, dict):
        raise ValueError("Daily recovery requires an existing rolling Release")
    require_newer_rolling(existing, plan["build_id"])
    previous_tag, previous_sha = require_newer_daily(
        existing, plan, repository, rolling_source_sha=plan["source_sha"],
    )
    if daily_version_order(plan["product_version"]) <= daily_version_order(previous_tag[1:]):
        raise ValueError("Daily recovery must advance the published version")
    base = (DEVELOP_DAILY_BASELINE if (previous_tag, previous_sha) == (LAST_CI_DAILY_TAG, LAST_CI_DAILY_SHA)
            else previous_sha)
    if api(f"{root}/compare/{base}...{plan['source_sha']}")["status"] not in {"ahead", "identical"}:
        raise ValueError("refusing to recover a diverged Daily commit")

    versioned = api(f"{root}/releases/tags/{plan['release_tag']}")
    if (not isinstance(versioned, dict) or versioned.get("draft") is not False
            or not versioned.get("published_at") or type(versioned.get("id")) is not int
            or versioned["id"] <= 0 or not isinstance(versioned.get("body"), str)
            or not versioned["body"].strip() or "<!-- aivision-build:" in versioned["body"]):
        raise ValueError("Daily recovery requires the published versioned Release and its original notes")
    remote = verify_remote_assets(directory, plan, repository, release_id=versioned["id"])
    for name, package in product_image_repositories(repository).items():
        public_registry.verify_public_tag(f"{package}:{plan['product_version']}", images[name])
        public_registry.verify_public_tag(f"{package}:daily", images[name])

    verify_candidate()
    tag = api(f"{root}/git/ref/tags/daily")["object"]
    if tag.get("type") != "commit" or tag.get("sha") != plan["source_sha"]:
        raise ValueError("Daily tag does not identify the interrupted candidate")
    current = api(endpoint)
    identity = ("id", "tag_name", "body", "draft", "immutable")
    if not current or any(current.get(field) != existing.get(field) for field in identity):
        raise ValueError("Daily recovery provenance changed during verification")
    url = f"https://github.com/{repository}/releases/tag/{plan['release_tag']}"
    body = (f"[Versioned assets and exact image manifest]({url}).\n\n{versioned['body'].strip()}\n\n"
            f"<!-- aivision-build: {plan['build_id']} -->")
    if rolling_versioned_tag({"body": body}, repository) != plan["release_tag"]:
        raise ValueError("recovered Daily notes do not identify the frozen candidate")
    title = f"Daily: {plan['product_version']}"
    api(f"{root}/releases/{existing['id']}", data={
        "name": title, "body": body, "prerelease": True, "make_latest": "false",
    })
    current = api(endpoint)
    if (not current or current.get("id") != existing["id"] or current.get("tag_name") != "daily"
            or current.get("draft") is not False or current.get("prerelease") is not True
            or current.get("name") != title or current.get("body") != body):
        raise ValueError("recovered Daily Release metadata differs on readback")
    require_live_tag(repository, "daily", plan["source_sha"])
    return remote, {"mode": "daily_metadata", "previous_release_id": existing["id"],
                    "previous_body_sha256": hashlib.sha256(existing["body"].encode()).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("preflight", "publish"))
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--acceptance", type=Path, help="frozen acceptance from this workflow attempt")
    parser.add_argument("--candidate-acceptance", type=Path)
    parser.add_argument("--candidate-run-id")
    parser.add_argument("--candidate-run-attempt")
    recovery_modes = parser.add_mutually_exclusive_group()
    recovery_modes.add_argument("--recover-dev-publication", action="store_true")
    recovery_modes.add_argument("--recover-daily-publication", action="store_true")
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args()
    candidate_inputs = (args.candidate_acceptance, args.candidate_run_id, args.candidate_run_attempt)
    if any(candidate_inputs) and not all(candidate_inputs):
        raise ValueError("frozen candidate promotion requires acceptance, run ID and attempt together")
    if args.recover_dev_publication and not all(candidate_inputs):
        raise ValueError("Dev publication recovery requires the original frozen candidate")
    if args.recover_daily_publication and (args.command != "publish" or not all(candidate_inputs)):
        raise ValueError("Daily publication recovery requires publishing the original frozen candidate")
    if args.acceptance and args.candidate_acceptance:
        raise ValueError("select same-attempt acceptance or remote candidate promotion")
    repository = require_product_repository(os.environ.get("GITHUB_REPOSITORY"))
    acceptance_path = args.acceptance or args.candidate_acceptance
    if not acceptance_path:
        raise ValueError("public publication requires frozen candidate acceptance")
    source_sha = os.environ["GITHUB_SHA"]
    publisher_build_id = f"{os.environ['GITHUB_RUN_ID']}.{os.environ['GITHUB_RUN_ATTEMPT']}"
    build_id = publisher_build_id
    acceptance_bytes = acceptance_path.read_bytes()
    acceptance = json.loads(acceptance_bytes)
    acceptance_sha256 = hashlib.sha256(acceptance_bytes).hexdigest()
    publication_recovery = False
    def verify_candidate():
        from release_candidate import verify_remote_candidate

        if hashlib.sha256(acceptance_path.read_bytes()).hexdigest() != acceptance_sha256:
            raise ValueError("candidate acceptance changed during publication")
        return verify_remote_candidate(args.directory, acceptance,
            repository=repository, source_sha=source_sha,
            candidate_run_id=args.candidate_run_id, candidate_run_attempt=args.candidate_run_attempt, api_fn=api,
            allow_dev_recovery=args.recover_dev_publication, allow_daily_recovery=args.recover_daily_publication)

    if args.candidate_acceptance:
        if args.recover_daily_publication:
            source_sha = None
        candidate_verification = verify_candidate()
        publication_recovery = candidate_verification.get("publication_recovery", False)
        if args.recover_daily_publication:
            if not publication_recovery:
                raise ValueError("Daily publication recovery requires an interrupted publication")
            source_sha = candidate_verification["candidate_source_sha"]
        build_id = f"{args.candidate_run_id}.{args.candidate_run_attempt}"
    if args.acceptance:
        from release_candidate import verify_frozen

        verify_frozen(args.directory, acceptance, repository=repository,
                      source_sha=source_sha, build_id=build_id)
    plan, images = validate_bundle(
        args.directory,
        source_sha,
        build_id,
        repository=repository,
    )
    if plan.get("release_preview"):
        raise ValueError("a release preview cannot enter publication preflight or publish")
    stable = plan["channel"] in {"stable", "rc"}
    if not stable and plan["alias"] not in {"daily", "dev"}:
        raise ValueError("trial aliases are candidate-only and cannot publish product versions")
    live_repository = api(f"repos/{repository}")
    if (not isinstance(live_repository, dict) or live_repository.get("full_name") != repository
            or live_repository.get("visibility") != "public"):
        raise ValueError("public publication requires the official repository to be public")
    public_registry.verify_public_images(images)
    if stable:
        preflight(plan, images, repository)
    else:
        check_version_tags(plan, images, repository=repository)
    if args.command == "preflight":
        return

    def write_receipt(remote, rolling_assets=None, recovery=None):
        if args.receipt:
            receipt = {"schema_version": 1, "status": "published", "source_repository": repository,
                       "source_sha": plan["source_sha"], "candidate_build_id": plan["build_id"],
                       "publisher_build_id": publisher_build_id, "publisher_source_sha": os.environ["GITHUB_SHA"],
                       "publisher_workflow_ref": os.environ.get("GITHUB_WORKFLOW_REF", ""),
                       "release_tag": plan["release_tag"], "remote_assets": remote, "rolling_assets": rolling_assets,
                       "candidate_acceptance_sha256": acceptance_sha256, "recovery": recovery,
                       "candidate_publication_recovery": publication_recovery}
            args.receipt.parent.mkdir(parents=True, exist_ok=True)
            args.receipt.write_text(json.dumps(receipt, indent=2) + "\n")

    if args.recover_daily_publication:
        if plan["channel"] != "daily" or plan["alias"] != "daily" or live_repository.get("default_branch") != "develop":
            raise ValueError("Daily publication recovery requires a Daily candidate from develop")
        remote, recovery = recover_daily_metadata(args.directory, plan, images, repository, verify_candidate)
        write_receipt(remote, recovery=recovery)
        return

    rolling = plan["alias"] in {"dev", "daily"}
    existing = None
    recovery = None
    previous_daily = None
    daily_history_cutover = False
    if rolling:
        existing = api(
            f"repos/{repository}/releases/tags/{plan['alias']}", missing=True
        )
        if publication_recovery and (not existing or existing.get("tag_name") != "dev"
                or existing.get("draft") is not False):
            raise ValueError("publication-only recovery requires an existing published Dev Release")
        if existing:
            same = require_newer_rolling(existing, plan["build_id"],
                allow_same=plan["alias"] == "dev" and args.candidate_acceptance is not None)
            if plan["alias"] == "daily":
                previous_daily = require_newer_daily(existing, plan, repository)
            if same:
                require_same_dev_candidate(existing, plan, repository)
                recovery = {"mode": "same_frozen_candidate",
                            "previous_release_id": existing["id"],
                            "previous_body_sha256": hashlib.sha256(existing["body"].encode()).hexdigest()}
        previous = api(f"repos/{repository}/git/ref/tags/{plan['alias']}", missing=True)
        if plan["alias"] == "daily" and previous and not existing:
            raise ValueError("Daily tag has no published rolling Release provenance")
        if previous:
            comparison_base = plan["alias"]
            if (
                previous_daily == (LAST_CI_DAILY_TAG, LAST_CI_DAILY_SHA)
                and live_repository.get("default_branch") == "develop"
                and previous["object"].get("type") == "commit"
                and previous["object"].get("sha") == LAST_CI_DAILY_SHA
            ):
                comparison_base = DEVELOP_DAILY_BASELINE
                daily_history_cutover = True
            comparison = api(
                f"repos/{repository}/compare/{comparison_base}...{plan['source_sha']}"
            )
            if comparison["status"] not in {"ahead", "identical"}:
                raise ValueError(
                    "refusing to replace a newer or diverged rolling commit"
                )
    assets = [str(path) for path in sorted(args.directory.iterdir()) if path.is_file()]
    url = f"https://github.com/{repository}/releases/tag/{plan['release_tag']}"
    details = (
        "\n\n<details>\n<summary>Build details</summary>\n\n"
        f"Source: `{plan['source_sha']}`  \n"
        f"Build: `{plan['build_id']}`  \n"
        f"Publisher: `{publisher_build_id}`  \n"
        f"[Image manifest](https://github.com/{repository}/releases/download/"
        f"{plan['release_tag']}/release-manifest.json)\n\n</details>"
    )
    image_scope = (
        "The five service images share the product version tag. "
        "The manifest records the upstream data backend digest and its public mirror."
    )
    artifact_smokes = ["Distributed deployment and recreation on Linux amd64 with OceanBase CE and seekdb."]
    if "windows_cli" in acceptance:
        artifact_smokes.append("Windows x64 CLI installation and remote queries against a test server.")
    if "personal_edition" in acceptance:
        artifact_smokes.append("Personal Edition installation and data retention on Linux amd64 and arm64.")
    scope = (
        "Deploy the tested combination with `aiv cast --vars-file release-images.vars.yaml` "
        "(OceanBase CE), or `--vars-file release-seekdb.vars.yaml` (seekdb). "
        f"{image_scope}\n\n"
        f"Artifact smoke: {' '.join(artifact_smokes)} Cross-version upgrades are outside these checks."
    )
    versioned = None
    if rolling:
        versioned = api(f"repos/{repository}/releases/tags/{plan['release_tag']}", missing=True)
        if (recovery or publication_recovery) and versioned is None:
            raise ValueError("Dev recovery requires the published frozen versioned Release")
        if versioned is not None:
            if versioned.get("draft") is not False:
                raise ValueError("existing versioned release is not a published immutable candidate")
            verify_remote_assets(args.directory, plan, repository)
    if plan["alias"] == "dev" and existing:
        require_known_dev_assets(args.directory, repository, existing)
    generated = (generate_rolling_notes(plan, repository, existing, daily_history_cutover=daily_history_cutover)
                 if rolling and not recovery else None)
    release = find_release_draft(repository, plan["release_tag"]) if stable else None
    publish_version_tags(plan, images, repository=repository)
    if stable:
        # Upload through the reviewed draft's ID, never through a tag lookup.
        upload_draft_assets(repository, release["id"], assets)
    elif rolling and versioned is None:
        run(
            "gh",
            "release",
            "create",
            plan["release_tag"],
            *assets,
            "--repo",
            repository,
            "--target",
            plan["source_sha"],
            "--prerelease",
            "--latest=false",
            "--title",
            plan["product_version"],
            "--notes",
            f"{generated}\n\n## Deployment\n\n{scope}{details}",
        )
    if stable:
        verify_remote_assets(args.directory, plan, repository, release_id=release["id"], draft=True)
    else:
        verify_remote_assets(args.directory, plan, repository)
    if hashlib.sha256(acceptance_path.read_bytes()).hexdigest() != acceptance_sha256:
        raise ValueError("candidate acceptance changed during publication")
    if args.acceptance:
        verify_frozen(args.directory, acceptance, repository=repository,
                      source_sha=source_sha, build_id=build_id)
    if args.candidate_acceptance:
        # Recheck after potentially slow uploads/readback: a new default-branch
        # commit or rerun must not be promoted merely because preflight was old.
        verify_candidate()
    if recovery or publication_recovery:
        # Immutable asset readback can be slow. Do not let an equal-build retry
        # replace another publication that advanced while those bytes downloaded.
        current = require_unchanged_dev_release(existing, repository)
        if recovery:
            require_same_dev_candidate(current, plan, repository)
    if stable:
        require_live_tag(repository, plan["release_tag"], source_sha)
        draft = read_draft(repository, plan["release_tag"], release["id"])
        if draft.get("body") != release["body"]:
            raise ValueError("reviewed draft Release notes changed during publication")
        api(
            f"repos/{repository}/releases/{release['id']}",
            data={
                "draft": False,
                "prerelease": plan["channel"] == "rc",
                "make_latest": "true" if plan["channel"] == "stable" else "false",
            },
        )
        verify_remote_assets(args.directory, plan, repository, release_id=release["id"])
    # No alias is written until every required check has passed. Registry aliases
    # are not a cross-image transaction: a publishing outage may require reconciliation.
    for name, image in product_image_repositories(repository).items():
        ref = images[name]
        tags = []
        if plan["alias"] != plan["product_version"]:
            tags.append(plan["alias"])
        if plan["channel"] == "stable":
            tags.append("latest")
        for tag in tags:
            run("docker", "buildx", "imagetools", "create", "--tag", f"{image}:{tag}", ref)
            public_registry.verify_public_tag(f"{image}:{tag}", ref)
    if rolling:
        alias = plan["alias"]
        tag = api(f"repos/{repository}/git/ref/tags/{alias}", missing=True)
        if tag:
            api(
                f"repos/{repository}/git/refs/tags/{alias}",
                data={"sha": plan["source_sha"], "force": True},
            )
        else:
            run(
                "gh",
                "api",
                f"repos/{repository}/git/refs",
                "--method",
                "POST",
                "--input",
                "-",
                input=json.dumps(
                    {"ref": f"refs/tags/{alias}", "sha": plan["source_sha"]}
                ),
            )
        notes = existing["body"] if recovery else (
            f"[Versioned assets and exact image manifest]({url}).\n\n{generated}\n\n"
            f"## Deployment\n\n{scope}\n\n<!-- aivision-build: {plan['build_id']} -->{details}"
        )
        if existing:
            if alias == "dev":
                run(
                    "gh",
                    "release",
                    "upload",
                    alias,
                    *assets,
                    "--repo",
                    repository,
                    "--clobber",
                )
                require_unchanged_dev_release(existing, repository)
                # The final build marker commits the rolling asset update. A
                # failed upload/readback leaves the previous notes retryable.
                verify_remote_assets(args.directory, {**plan, "release_tag": alias}, repository)
                require_unchanged_dev_release(existing, repository)
            if not recovery:
                run(
                    "gh",
                    "release",
                    "edit",
                    alias,
                    "--repo",
                    repository,
                    "--prerelease",
                    "--latest=false",
                    "--title",
                    f"{alias.title()}: {plan['product_version']}",
                    "--notes",
                    notes,
                )
        else:
            run(
                "gh",
                "release",
                "create",
                alias,
                *(assets if alias == "dev" else []),
                "--repo",
                repository,
                "--target",
                plan["source_sha"],
                "--prerelease",
                "--latest=false",
                "--title",
                f"{alias.title()}: {plan['product_version']}",
                "--notes",
                notes,
            )

    remote = verify_remote_assets(args.directory, plan, repository)
    rolling_assets = None
    if plan["alias"] == "dev":
        rolling_assets = verify_remote_assets(args.directory, {**plan, "release_tag": "dev"}, repository)
    write_receipt(remote, rolling_assets, recovery)


if __name__ == "__main__":
    main()
