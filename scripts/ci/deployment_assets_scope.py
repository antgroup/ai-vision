"""Skip heavy asset checks only for existing, unpackaged deployment prose."""

import argparse
import fnmatch
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys

from plan import changed_paths


REPOSITORY = Path(__file__).resolve().parents[2]


def packaged_prose(paths, root):
    """Read the canonical copy rules, including paths removed from the checkout."""
    spec = importlib.util.spec_from_file_location(
        "deployment_asset_inventory", root / "deploy/scripts/package_assets.py")
    inventory = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(inventory)
    explicit = {path for pair in inventory.FILES for path in pair}
    explicit.update(inventory.OWNERSHIP_FILES)
    explicit.add(str(inventory.MANIFEST))
    for path in paths:
        if path in explicit:
            return True
        for source, destination, patterns in inventory.GROUPS:
            for directory in (source, destination):
                try:
                    relative = PurePosixPath(path).relative_to(directory).as_posix()
                except ValueError:
                    continue
                if any(fnmatch.fnmatchcase(relative, pattern) for pattern in patterns):
                    return True
    return False


def prose_file_modes(root, revision):
    """Inspect tree entries so removed files and symlink changes stay visible."""
    try:
        output = subprocess.check_output(
            ["git", "-C", str(root), "ls-tree", "-r", "-z", revision,
             "--", "docs/deployment/"], stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError):
        return None
    modes = {}
    for entry in output.split(b"\0"):
        if entry:
            metadata, path = entry.split(b"\t", 1)
            modes[path.decode("utf-8", errors="surrogateescape")] = metadata.split(b" ", 1)[0]
    return modes


def make_scope(paths, *, root, event_name, fallback=False, base_sha="", head_sha=""):
    paths = set(paths)
    decision = {"heavy_checks": True, "changed_files": len(paths)}
    if event_name != "pull_request":
        reason = "Non-PR events require complete deployment asset validation"
    elif fallback:
        reason = "Diff unavailable: conservative deployment asset validation"
    elif not paths:
        reason = "Empty diff: conservative deployment asset validation"
    elif not all(path.startswith("docs/deployment/") and path.endswith((".md", ".rst"))
                 for path in paths):
        reason = "Runtime, mixed or unclassified paths changed"
    elif packaged_prose(paths, root):
        reason = "Canonical packaged prose changed"
    else:
        before, after = (prose_file_modes(root, sha) for sha in (base_sha, head_sha))
        if before is None or after is None:
            reason = "Prose inventory unavailable: conservative deployment asset validation"
        elif any(before.get(path) != b"100644" or after.get(path, b"100644") != b"100644"
                 for path in paths):
            reason = "New, renamed or non-regular prose needs complete validation"
        else:
            decision["heavy_checks"] = False
            reason = "Only existing, unpackaged deployment prose changed"
    return dict(decision, reason=reason)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=REPOSITORY)
    args = parser.parse_args()
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    try:
        try:
            event = json.loads(args.event.read_text())
            paths, fallback = changed_paths(event, event_name, root=args.root)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            event, paths, fallback = {}, [], True
        base_sha, head_sha = "", ""
        if event_name == "pull_request" and not fallback:
            base_sha = event["pull_request"]["base"]["sha"]
            head_sha = event["pull_request"]["head"]["sha"]
        decision = make_scope(paths, root=args.root, event_name=event_name,
                              fallback=fallback, base_sha=base_sha, head_sha=head_sha)
        if summary_path := os.environ.get("GITHUB_STEP_SUMMARY"):
            status = ("Heavy deployment asset checks are required."
                      if decision["heavy_checks"]
                      else "Heavy setup, package, Go, schema and Compose checks were not run.")
            with open(summary_path, "a") as summary:
                summary.write("## Deployment asset scope\n\n" + status + "\n\n")
                summary.write(f"{decision['reason']}; {decision['changed_files']} changed paths.\n")
        if output_path := os.environ.get("GITHUB_OUTPUT"):
            with open(output_path, "a") as output:
                output.write(f"heavy_checks={str(decision['heavy_checks']).lower()}\n")
        print(json.dumps(decision, sort_keys=True))
        return 0
    except Exception as error:
        print(f"deployment asset scope failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
