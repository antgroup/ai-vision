"""Plan conservative reuse from a verified published product Release."""

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from product_registry import (
    PUBLIC_REPOSITORY,
    product_image_repositories,
    require_product_repository,
    validate_data_backend_mirror,
)
from release_plan import IMAGES, VERSION, require_digest, require_platforms

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "build"))
from build_info import make_info

CONTEXTS = {
    "aivision-be": "ai-vision",
    "aivision-fe": "ai-vision/vfe",
    "antcollector-gw": "antcollector",
    "agentlens": "agentlens",
}


def run(*args, cwd=None):
    return subprocess.check_output(args, cwd=cwd, text=True).strip()


def fingerprint(root, component, source="HEAD"):
    # Hash Git modes, paths and blobs, not the worktree or generated plugin dist.
    # The whole tracked Docker context intentionally over-invalidates builds.
    paths = [CONTEXTS[component], "scripts/build/build_info.py",
             ".github/workflows/release-images.yml", ".github/workflows/release-image-build.yml",
             "scripts/ci/release_reuse.py"]
    if component == "aivision-be":
        paths.append(".github/workflows/release-plugins.yml")
    entries = subprocess.check_output(
        ["git", "ls-tree", "-rz", "--full-tree", source, "--", *paths], cwd=root
    )
    if not entries:
        raise ValueError("component source context is empty")
    return hashlib.sha256(b"aivision-context-v1\0" + entries).hexdigest()


def validate_build(component, value, *, allow_local_native=False):
    if not isinstance(value, dict) or type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        raise ValueError("component build metadata requires schema_version 1")
    if not {"schema_version", "component", "product_version", "component_version", "source_sha", "build_id", "built_at", "target_channel", "dirty"} <= value.keys():
        raise ValueError("component build metadata is incomplete")
    if value.get("component") != component or value.get("dirty") is not False:
        raise ValueError("component build metadata must identify a clean actual build")
    if not isinstance(value.get("source_sha"), str) or not re.fullmatch(r"[a-f0-9]{40}", value["source_sha"]):
        raise ValueError("component build source must be a full SHA")
    if not isinstance(value.get("product_version"), str) or not VERSION.fullmatch(value["product_version"]) or (
        value.get("component_version") is not None and (
            not isinstance(value["component_version"], str) or not VERSION.fullmatch(value["component_version"])
        )
    ):
        raise ValueError("component build versions are invalid")
    # Only the private delivery's validated local-native provenance enables this
    # alternative. Ordinary release planning and reuse stay CI-only by default.
    build_pattern = r"local-[0-9]{8}T[0-9]{6}Z" if allow_local_native else r"[1-9][0-9]*\.[1-9][0-9]*"
    if not isinstance(value.get("build_id"), str) or not re.fullmatch(build_pattern, value["build_id"]):
        raise ValueError("component build ID must identify a workflow attempt")
    if not isinstance(value.get("target_channel"), str) or value["target_channel"] not in {"stable", "rc", "daily", "dev"}:
        raise ValueError("component build channel is invalid")
    if not isinstance(value.get("built_at"), str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?"
        r"(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)", value["built_at"]
    ):
        raise ValueError("component build time must use RFC3339")
    timestamp = datetime.fromisoformat(value["built_at"].replace("Z", "+00:00"))
    if timestamp.utcoffset() is None:
        raise ValueError("component build time must include a timezone")
    return value


def latest_release(*, repository=PUBLIC_REPOSITORY):
    require_product_repository(repository)
    pages = json.loads(run("gh", "api", "--paginate", "--slurp",
                           f"repos/{repository}/releases?per_page=100"))
    releases = [release for page in pages for release in page
                if release.get("draft") is False and release.get("published_at")
                and re.fullmatch(r"v" + VERSION.pattern, release.get("tag_name", ""))]
    return max(releases, key=lambda release: release["published_at"])["tag_name"] if releases else ""


def published_manifest(root, tag, *, allow_ineligible=False, repository=PUBLIC_REPOSITORY):
    require_product_repository(repository)
    if not re.fullmatch(r"v" + VERSION.pattern, tag):
        raise ValueError("reuse_release must be a versioned published Release tag")
    release = json.loads(run("gh", "api", f"repos/{repository}/releases/tags/{tag}"))
    if release.get("draft") is not False or release.get("tag_name") != tag or not release.get("published_at"):
        raise ValueError("reuse requires an already published versioned Release")
    if not any(asset.get("name") == "release-manifest.json" for asset in release.get("assets", [])):
        return None  # A legacy Release has no reusable image metadata.
    if not any(asset.get("name") == "SHA256SUMS" for asset in release.get("assets", [])):
        raise ValueError("prior published manifest has no checksum asset")
    with tempfile.TemporaryDirectory(prefix="aivision-reuse-") as directory:
        run("gh", "release", "download", tag, "--repo", repository,
            "--pattern", "release-manifest.json", "--pattern", "SHA256SUMS", "--dir", directory)
        payload = (Path(directory) / "release-manifest.json").read_bytes()
        if len(payload) > 64 * 1024:
            raise ValueError("prior release manifest exceeds the runtime size limit")
        matches = [line.split("  ", 1)[0] for line in
                   (Path(directory) / "SHA256SUMS").read_text().splitlines()
                   if line.endswith("  release-manifest.json")]
        if matches != [hashlib.sha256(payload).hexdigest()]:
            raise ValueError("prior published manifest checksum does not match")
        manifest = json.loads(payload)
    if manifest.get("product_version") != tag[1:]:
        raise ValueError("prior published manifest version differs from its Release")
    if manifest.get("source_repository") != repository or manifest.get("release_tag") != tag:
        raise ValueError("prior published manifest differs from the public repository or release tag")
    source = manifest.get("source_sha", "")
    if not re.fullmatch(r"[a-f0-9]{40}", source):
        raise ValueError("prior published manifest source is invalid")
    from release_publish import require_live_tag
    require_live_tag(repository, tag, source)
    ancestry = subprocess.run(["git", "merge-base", "--is-ancestor", source, "HEAD"], cwd=root)
    if allow_ineligible and ancestry.returncode == 1:
        return None  # A different branch's Release is not a reuse baseline.
    ancestry.check_returncode()
    return manifest


def sdk_smoke_path(path):
    root_prose = {b"README.md", b"README.en.md", b"README.ja.md", b"README.zh-CN.md",
                  b"README.zh-TW.md", b"CONTRIBUTING.md"}
    return (path in root_prose
            or path.startswith(b"docs/") and path.endswith((b".md", b".rst"))
            or path.startswith(b"ai-vision/vfe/src/") and path.endswith((b".tsx", b".css"))
            and not path.endswith(b".config.tsx"))


def sdk_coverage(root, directory, plan, prior, *, force=False):
    """Reduce only Daily coverage backed by an already verified published manifest."""
    if plan.get("channel") != "daily":
        return "full", "non-daily-channel"
    if force:
        return "full", "forced-rebuild"
    if not prior:
        return "full", "no-verified-release-baseline"
    images = prior.get("images")
    if not isinstance(images, dict):
        return "full", "data-backend-provenance-unavailable"
    try:
        mirror = validate_data_backend_mirror(prior.get("data_backend_mirror"), images.get("signoz-ob"))
        current_upstream = (directory / "signoz-image.txt").read_text().strip()
    except (OSError, ValueError):
        return "full", "data-backend-provenance-unavailable"
    if current_upstream != mirror["upstream_ref"]:
        return "full", "data-backend-changed"
    revisions = (prior.get("source_sha"), plan.get("source_sha"))
    if not all(isinstance(sha, str) and re.fullmatch(r"[a-f0-9]{40}", sha) for sha in revisions):
        return "full", "source-diff-unavailable"
    try:
        # Read both sides of renames and include file modes, including symlinks.
        raw = subprocess.check_output(
            ["git", "diff", "--raw", "--no-renames", "--no-abbrev", "--no-ext-diff",
             "--ignore-submodules=none", "-z", *revisions, "--"],
            cwd=root, stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return "full", "source-diff-unavailable"
    *entries, terminator = raw.split(b"\0")
    if terminator or len(entries) % 2:
        return "full", "source-diff-unavailable"
    header = rb":(?:000000|100644) (?:000000|100644) [0-9a-f]{40} [0-9a-f]{40} [AMD]"
    if any(not re.fullmatch(header, meta) or not sdk_smoke_path(path)
           for meta, path in zip(entries[::2], entries[1::2])):
        return "full", "source-diff-requires-compatibility"
    return "smoke", "verified-release-and-prose-or-ui-only"


def prepare(root, directory, prior=None, *, force=False, repository=PUBLIC_REPOSITORY):
    repositories = product_image_repositories(repository)
    if prior and not force:
        require_product_repository(prior.get("source_repository"))
    plan = json.loads((directory / "release-plan.json").read_text())
    if run("git", "rev-parse", "HEAD", cwd=root) != plan["source_sha"]:
        raise ValueError("reuse planner checkout differs from the release source")
    if run("git", "status", "--porcelain", "--untracked-files=normal", cwd=root):
        raise ValueError("reuse planning requires a clean source checkout")
    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    result = {}
    for name in IMAGES:
        context = fingerprint(root, name)
        identity = make_info(root, name, channel=plan["channel"],
            source_sha=plan["source_sha"], build_id=plan["build_id"],
            run_number=plan["run_number"], built_at=timestamp,
            product_version=plan["product_version"],
            snapshot_date=plan.get("snapshot_date"), daily_revision=plan.get("daily_revision", "1"))
        ref = None
        if prior and not force:
            previous = prior.get("component_inputs", {}).get(name)
            previous_build = prior.get("component_builds", {}).get(name)
            # Legacy manifests remain installable, but cannot prove reusable inputs.
            if previous and previous_build:
                old_context = fingerprint(root, name, prior["source_sha"])
                if previous != old_context:
                    raise ValueError("prior component context fingerprint differs from its source")
                validate_build(name, previous_build)
                # An old component may predate the previous product release; its
                # own source tree must prove the same context as that release.
                subprocess.run(["git", "merge-base", "--is-ancestor", previous_build["source_sha"], prior["source_sha"]], cwd=root, check=True)
                if fingerprint(root, name, previous_build["source_sha"]) != old_context:
                    raise ValueError("prior component build inputs differ from the prior release")
                if context == old_context:
                    ref = require_digest(prior["images"][name])
                    if ref.split("@", 1)[0] != repositories[name]:
                        raise ValueError("reused images must be in their official component package")
                    identity = previous_build
        validate_build(name, identity)
        result[name] = dict(ref=ref, identity=identity, context_sha256=context)
    (directory / "component-plan.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def native_reuse(component, ref, identity, architecture):
    repository = product_image_repositories()[component]
    if require_digest(ref).split("@", 1)[0] != repository:
        raise ValueError("reused images must be in their official component package")
    manifest = json.loads(run("docker", "buildx", "imagetools", "inspect", ref,
                              "--format", "{{json .Manifest}}"))
    require_platforms(manifest)
    if manifest.get("digest") != ref.split("@", 1)[1]:
        raise ValueError("reused image index digest differs from the published manifest")
    children = [item for item in manifest["manifests"]
                if item.get("platform") == {"architecture": architecture, "os": "linux"}]
    # Additional platform metadata such as variant does not change architecture.
    if not children:
        children = [item for item in manifest["manifests"] if
                    item.get("platform", {}).get("architecture") == architecture and
                    item.get("platform", {}).get("os") == "linux"]
    if len(children) != 1 or not re.fullmatch(r"sha256:[a-f0-9]{64}", children[0]["digest"]):
        raise ValueError("reused image must have one valid native child")
    expected = base64.b64encode(json.dumps(identity, sort_keys=True).encode()).decode()
    child = ref.split("@", 1)[0] + "@" + children[0]["digest"]
    subprocess.run([sys.executable, str(Path(__file__).with_name("release_image.py")),
                    "--ref", child, "--arch", architecture, "--identity", expected], check=True, stdout=sys.stderr)
    return children[0]["digest"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "native"))
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--release", default="")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--component", choices=IMAGES)
    parser.add_argument("--arch", choices=("amd64", "arm64"))
    args = parser.parse_args()
    repository = require_product_repository(os.environ.get("GITHUB_REPOSITORY"))
    if args.command == "prepare":
        selected = "" if args.force else (args.release or latest_release(repository=repository))
        prior = published_manifest(args.root, selected, allow_ineligible=not bool(args.release),
                                   repository=repository) if selected else None
        plan_path = args.directory / "release-plan.json"
        plan = json.loads(plan_path.read_text())
        plan["reuse_release"] = selected or None
        coverage, reason = sdk_coverage(args.root, args.directory, plan, prior, force=args.force)
        plan.update(sdk_coverage=coverage, sdk_coverage_reason=reason)
        plan_path.write_text(json.dumps(plan, indent=2) + "\n")
        prepare(args.root, args.directory, prior, force=args.force, repository=repository)
        if output := os.environ.get("GITHUB_OUTPUT"):
            with open(output, "a", encoding="utf-8") as stream:
                stream.write(f"sdk_coverage={coverage}\nsdk_coverage_reason={reason}\n")
    else:
        data = json.loads((args.directory / "component-plan.json").read_text())[args.component]
        if not data["ref"]:
            raise ValueError("component was not selected for reuse")
        print(native_reuse(args.component, data["ref"], data["identity"], args.arch))


if __name__ == "__main__":
    main()
