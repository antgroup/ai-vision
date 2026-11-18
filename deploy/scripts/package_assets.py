#!/usr/bin/env python3
"""Synchronize canonical deploy/ assets into the CLI's compatibility paths.

Run without flags after changing deployment sources; --check never writes.
The generated manifest has no timestamps or host paths. Upstream distributions
and their checksums are copied verbatim, including their license files.
"""

import argparse
import hashlib
import json
from pathlib import Path
import sys


REPOSITORY = Path(__file__).resolve().parents[2]
MANIFEST = Path("aivision-cli/deploy/assets-manifest.json")
GROUPS = (
    ("deploy/blueprints", "aivision-cli/blueprints", ("*.yaml",)),
    ("deploy/kubernetes/base", "aivision-cli/deploy/kubernetes", ("*.yaml",)),
    ("deploy/kubernetes/dependencies", "aivision-cli/deploy/kubernetes/dependencies", ("*",)),
    ("deploy/bootstrap", "aivision-cli/internal/deploy/assets/bootstrap", ("*.py", "*.sh")),
)
FILES = (
    ("deploy/kubernetes/README.md", "aivision-cli/deploy/kubernetes/README.md"),
    ("deploy/kubernetes/lifecycle.md", "aivision-cli/deploy/kubernetes/lifecycle.md"),
    ("deploy/kubernetes/helm-roadmap.md", "aivision-cli/deploy/kubernetes/helm-roadmap.md"),
    ("deploy/config/aivision.vars.example.yaml", "docs/deployment/aivision.vars.example.yaml"),
    ("deploy/components.json", "aivision-cli/internal/deploy/assets/components.json"),
)
NATIVE_KUBERNETES = ("bootstrap.yaml", "database-init.yaml")

OWNERSHIP_FILES = {
    "aivision-cli/blueprints/GENERATED.md": "The YAML files in this directory",
    "aivision-cli/deploy/kubernetes/GENERATED.md": "The YAML files, README.md, lifecycle.md, helm-roadmap.md and dependencies/ in this directory",
    "aivision-cli/internal/deploy/assets/GENERATED.md": "The JSON files and bootstrap/ in this directory",
}


def checked_file(root, relative):
    path = root / relative
    if any(part.is_symlink() for part in (path, *path.parents) if part != root.parent):
        raise ValueError(f"asset path must not contain a symlink: {relative}")
    if not path.is_file():
        raise ValueError(f"missing canonical asset: {relative}")
    return path


def native_template(data):
    """Select only the explicit native branch; unknown template shapes fail closed."""
    lines, branch, count = [], None, 0
    for line in data.decode().splitlines(keepends=True):
        marker = line.strip()
        if marker == '{{if eq .NATIVE_PYTHON_RUNTIME "true"}}':
            if branch is not None:
                raise ValueError("nested native template condition")
            branch, count = "native", count + 1
        elif branch is not None and marker == "{{else}}":
            if branch != "native":
                raise ValueError("duplicate native template else")
            branch = "source"
        elif branch is not None and marker == "{{end}}":
            if branch != "source":
                raise ValueError("native template needs an explicit source branch")
            branch = None
        elif branch is not None and marker.startswith("{{if "):
            raise ValueError("nested native template conditions are unsupported")
        elif branch != "source":
            lines.append(line)
    if branch is not None or count != 1:
        raise ValueError("native template requires exactly one complete native/source selection")
    return "".join(lines).encode()


def desired_assets(root):
    pairs = list(FILES)
    for source, destination, patterns in GROUPS:
        paths = sorted({path for pattern in patterns for path in (root / source).glob(pattern)})
        if not paths:
            raise ValueError(f"canonical asset group is empty: {source}")
        for path in paths:
            if not path.is_file():
                raise ValueError(f"unexpected canonical asset: {path.relative_to(root)}")
            pairs.append((str(path.relative_to(root)), f"{destination}/{path.name}"))
    generated = {}
    entries = []
    for source, destination in sorted(pairs):
        data = checked_file(root, source).read_bytes()
        generated[Path(destination)] = data
        entries.append({
            "source": source,
            "destination": destination,
            "sha256": hashlib.sha256(data).hexdigest(),
        })
    for name in NATIVE_KUBERNETES:
        source = f"deploy/kubernetes/base/{name}"
        destination = f"aivision-cli/deploy/kubernetes/native/{name}"
        original = checked_file(root, source).read_bytes()
        data = native_template(original)
        generated[Path(destination)] = data
        entries.append({"source": source, "destination": destination,
                        "source_sha256": hashlib.sha256(original).hexdigest(),
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "transform": "select NATIVE_PYTHON_RUNTIME=true template branch"})
    for path, description in OWNERSHIP_FILES.items():
        generated[Path(path)] = (
            "# Generated deployment assets\n\n"
            f"{description} are generated compatibility copies of the authoritative\n"
            "`deploy/` sources. Do not edit the copies directly.\n\n"
            "From the repository root, run:\n\n"
            "```sh\npython3 deploy/scripts/package_assets.py\n"
            "python3 deploy/scripts/package_assets.py --check\n```\n\n"
            "The complete source mapping and checksums are recorded in\n"
            "`aivision-cli/deploy/assets-manifest.json`.\n"
        ).encode()
    generated[MANIFEST] = (json.dumps({
        "schema_version": 1,
        "generated_by": "deploy/scripts/package_assets.py",
        "ownership": "Generated compatibility assets; edit the canonical deploy/ sources.",
        "assets": entries,
    }, indent=2, sort_keys=True) + "\n").encode()
    return generated


def existing_outputs(root):
    paths = {Path(destination) for _, destination in FILES}
    paths.update(Path(path) for path in OWNERSHIP_FILES)
    paths.add(MANIFEST)
    paths.update(Path("aivision-cli/deploy/kubernetes/native") / name for name in NATIVE_KUBERNETES)
    paths.update(path.relative_to(root) for path in (root / "aivision-cli/deploy/kubernetes/native").glob("*.yaml"))
    for _, destination, patterns in GROUPS:
        for pattern in patterns:
            paths.update(path.relative_to(root) for path in (root / destination).glob(pattern))
    paths.update(path.relative_to(root) for path in (root / "aivision-cli/internal/deploy/assets").glob("*.json"))
    return paths


def package(root, check):
    generated = desired_assets(root)
    unexpected = sorted(existing_outputs(root) - generated.keys())
    for relative in existing_outputs(root):
        path = root / relative
        if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
            raise ValueError(f"generated asset path must not contain a symlink: {relative}")
    changed = sorted(path for path, data in generated.items()
                     if not (root / path).is_file() or (root / path).read_bytes() != data)
    if check:
        for path in changed:
            print(f"missing or stale generated asset: {path}", file=sys.stderr)
        for path in unexpected:
            print(f"unexpected generated asset: {path}", file=sys.stderr)
        if changed or unexpected:
            print("Run python3 deploy/scripts/package_assets.py and review the generated diff.", file=sys.stderr)
            return 1
        print(f"Deployment assets are current ({len(generated)} generated files).")
        return 0
    # Only these documented generated paths are writable; Go source and other
    # hand-maintained files beside embedded resources remain untouched.
    for relative in [*changed, *unexpected]:
        path = root / relative
        if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
            raise ValueError(f"refusing to modify a symlink asset: {relative}")
        if path.exists() and not path.is_file():
            raise ValueError(f"refusing to replace a non-file asset: {relative}")
    for relative in unexpected:
        (root / relative).unlink()
    for relative in changed:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(generated[relative])
    print(f"Deployment assets synchronized ({len(changed)} updated, {len(unexpected)} removed).")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail on missing, stale or extra generated assets")
    args = parser.parse_args()
    try:
        return package(REPOSITORY, args.check)
    except (OSError, ValueError) as error:
        print(f"deployment asset packaging failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
