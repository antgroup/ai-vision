#!/usr/bin/env python3
"""Stage and describe Weaver Guard backend binary assets."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path


REQUIRED_DIRECTORIES = ["policies", "semconv", "scripts"]
REQUIRED_FILES = [
    "scripts/victoria-macos-arm64.sh",
    "scripts/jaeger-macos-arm64.sh",
]


def load_catalog(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def selected_assets(catalog: dict, platform: str, arch: str) -> dict:
    try:
        return catalog["platforms"][platform][arch]
    except KeyError as exc:
        raise SystemExit(f"catalog has no backend assets for {platform}/{arch}") from exc


def normalized_prefix(prefix: str) -> str:
    if not prefix.strip():
        raise SystemExit("set --oss-prefix to your asset host")
    return prefix.rstrip("/") + "/"


def asset_url(prefix: str, asset: dict) -> str:
    return normalized_prefix(prefix) + asset["oss_path"].lstrip("/")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def archive_contains_binary(path: Path, binary: str) -> bool:
    with tarfile.open(path, "r:gz") as archive:
        return any(Path(member.name).name == binary for member in archive.getmembers())


def archive_policy_error(path: Path, asset_name: str, asset: dict) -> str | None:
    binary = asset["binary"]
    if not path.is_file() or path.stat().st_size == 0:
        return f"{asset_name} archive is missing or empty: {path}"
    with tarfile.open(path, "r:gz") as archive:
        file_members = [member for member in archive.getmembers() if member.isfile()]
    if not any(Path(member.name).name == binary for member in file_members):
        return f"{asset_name} archive {path} does not contain {binary}"
    if asset.get("package") == "binary_only":
        binary_members = [member for member in file_members if Path(member.name).name == binary]
        if len(file_members) != 1 or len(binary_members) != 1:
            return f"{asset_name} archive {path} must contain only {binary}"
    return None


def verify_archive(path: Path, asset_name: str, asset: dict) -> None:
    error = archive_policy_error(path, asset_name, asset)
    if error is not None:
        raise SystemExit(error)


def repack_binary_only(source: Path, dest: Path, binary: str) -> None:
    with tarfile.open(source, "r:gz") as archive:
        member = next(
            (
                item
                for item in archive.getmembers()
                if item.isfile() and Path(item.name).name == binary
            ),
            None,
        )
        if member is None:
            raise SystemExit(f"source archive {source} does not contain {binary}")
        extracted = archive.extractfile(member)
        if extracted is None:
            raise SystemExit(f"failed to extract {binary} from {source}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=dest.parent, delete=False) as fh:
            tmp = Path(fh.name)
        try:
            with tarfile.open(tmp, "w:gz") as output:
                info = tarfile.TarInfo(binary)
                info.size = member.size
                info.mode = member.mode | 0o755
                info.mtime = member.mtime
                output.addfile(info, extracted)
            tmp.replace(dest)
        finally:
            if tmp.exists():
                tmp.unlink()


def first_existing_candidate(catalog_path: Path, asset: dict) -> Path | None:
    catalog_dir = catalog_path.parent
    if "oss_path" in asset:
        synced = catalog_dir / "dist/weaver-guard/backend-assets" / asset["oss_path"]
        if synced.is_file():
            return synced
    for value in asset.get("local_candidates", []):
        path = Path(value)
        if not path.is_absolute():
            path = catalog_dir / path
        if path.is_file():
            return path
    return None


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=dest.parent, delete=False) as fh:
        tmp = Path(fh.name)
    try:
        curl = shutil.which("curl")
        if curl is not None:
            subprocess.run(
                [
                    curl,
                    "-fsSL",
                    "--connect-timeout",
                    "30",
                    "--retry",
                    "5",
                    "--retry-delay",
                    "2",
                    "-o",
                    str(tmp),
                    url,
                ],
                check=True,
            )
        else:
            request = urllib.request.Request(url, headers={"User-Agent": "weaver-guard-release"})
            with urllib.request.urlopen(request, timeout=120) as response, tmp.open("wb") as out:
                shutil.copyfileobj(response, out)
        tmp.replace(dest)
    finally:
        if tmp.exists():
            tmp.unlink()


def stage_assets(args: argparse.Namespace) -> None:
    catalog_path = Path(args.catalog)
    catalog = load_catalog(catalog_path)
    assets = selected_assets(catalog, args.platform, args.arch)
    dest_root = Path(args.dest_dir)
    for name, asset in assets.items():
        dest = dest_root / asset["oss_path"]
        component_root = dest.parent.parent
        if component_root.is_dir():
            for stale in component_root.glob("*/*.tar.gz"):
                if stale != dest:
                    stale.unlink()
        if dest.is_file():
            error = archive_policy_error(dest, name, asset)
            if error is None:
                print(f"[backend-assets] cached {name}: {dest}")
                continue
            if asset.get("package") != "binary_only":
                raise SystemExit(error)
            print(f"[backend-assets] rebuilding {name}: {error}")
            dest.unlink()
        candidate = first_existing_candidate(catalog_path, asset)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if candidate is not None:
            if asset.get("package") == "binary_only":
                repack_binary_only(candidate, dest, asset["binary"])
            else:
                verify_archive(candidate, name, asset)
                shutil.copy2(candidate, dest)
        else:
            if asset.get("package") == "binary_only":
                with tempfile.TemporaryDirectory(dir=dest.parent) as tmp_dir:
                    source = Path(tmp_dir) / f"source-{asset['archive']}"
                    download(asset["source_url"], source)
                    repack_binary_only(source, dest, asset["binary"])
            else:
                download(asset["source_url"], dest)
        verify_archive(dest, name, asset)
        print(f"[backend-assets] staged {name}: {dest}")


def manifest(args: argparse.Namespace) -> None:
    catalog_path = Path(args.catalog)
    catalog = load_catalog(catalog_path)
    assets = selected_assets(catalog, args.platform, args.arch)
    oss_prefix = args.oss_prefix or catalog["oss_prefix"]
    backend_assets = {}
    for name, asset in assets.items():
        backend_assets[name] = {
            "version": asset["version"],
            "archive": asset["archive"],
            "binary": asset["binary"],
            "oss_path": asset["oss_path"],
            "url": asset_url(oss_prefix, asset),
        }
        if "script_component" in asset:
            backend_assets[name]["script_component"] = asset["script_component"]
        if "package" in asset:
            backend_assets[name]["package"] = asset["package"]
    output = {
        "version": args.guard_version,
        "asset_kind": "weaver-guard-runtime",
        "required_directories": REQUIRED_DIRECTORIES,
        "required_files": REQUIRED_FILES,
        "backend_catalog": {
            "schema_version": catalog["schema_version"],
            "oss_path": "backend-assets.json",
            "url": normalized_prefix(oss_prefix) + "backend-assets.json",
        },
        "backend_assets": {
            "platform": args.platform,
            "arch": args.arch,
            "items": backend_assets,
        },
    }
    json.dump(output, sys.stdout, indent=2, sort_keys=False)
    sys.stdout.write("\n")


def upload_list(args: argparse.Namespace) -> None:
    catalog_path = Path(args.catalog)
    catalog = load_catalog(catalog_path)
    assets = selected_assets(catalog, args.platform, args.arch)
    dest_root = Path(args.dest_dir)
    remote_prefix = args.remote_prefix.strip("/")
    for _name, asset in assets.items():
        local = dest_root / asset["oss_path"]
        if args.with_sha256:
            sha_path = local.with_suffix(local.suffix + ".sha256")
            sha_path.write_text(f"{sha256_file(local)}  {local.name}\n", encoding="utf-8")
            print(f"{sha_path}\t{remote_prefix}/{asset['oss_path']}.sha256")
        print(f"{local}\t{remote_prefix}/{asset['oss_path']}")
    print(f"{catalog_path}\t{remote_prefix}/backend-assets.json")


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument("--catalog", default="backend-assets.json")
        subparser.add_argument("--platform", default="macos")
        subparser.add_argument("--arch", default="arm64")

    stage = subparsers.add_parser("stage")
    add_common(stage)
    stage.add_argument("--dest-dir", required=True)
    stage.set_defaults(func=stage_assets)

    manifest_parser = subparsers.add_parser("manifest")
    add_common(manifest_parser)
    manifest_parser.add_argument("--guard-version", required=True)
    manifest_parser.add_argument("--oss-prefix")
    manifest_parser.set_defaults(func=manifest)

    upload = subparsers.add_parser("upload-list")
    add_common(upload)
    upload.add_argument("--dest-dir", required=True)
    upload.add_argument("--remote-prefix", required=True)
    upload.add_argument("--with-sha256", action="store_true")
    upload.set_defaults(func=upload_list)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
