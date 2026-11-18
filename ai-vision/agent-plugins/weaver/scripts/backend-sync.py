#!/usr/bin/env python3
"""Sync latest Weaver Guard backend archives to OSS."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from backend_assets import (  # noqa: E402
    archive_policy_error,
    download,
    first_existing_candidate,
    repack_binary_only,
    verify_archive,
)


DEFAULT_OSS_PREFIX = os.environ.get("WEAVER_GUARD_OSS_PREFIX", "")
DEFAULT_REMOTE_PREFIX = "aivision/signed/weaver-guard"
DEFAULT_DEST_DIR = "dist/weaver-guard/backend-assets"
DEFAULT_OSS_MANIFEST = "../../aivision-setup/crates/aivision-oss/Cargo.toml"
DEFAULT_OSS_ENV_FILE = "../../aivision-setup/.env"
NO_PROXY_HOST = os.environ.get("AIVISION_OSS_NO_PROXY_HOST", "")
DEFAULT_TARGETS = [
    ("macos", "amd64"),
    ("macos", "arm64"),
    ("linux", "amd64"),
    ("linux", "arm64"),
]

BACKENDS = {
    "jaeger": {
        "repo": "jaegertracing/jaeger",
        "binary": "jaeger-all-in-one",
        "package": "binary_only",
    },
    "vmetrics": {
        "repo": "VictoriaMetrics/VictoriaMetrics",
        "binary": "victoria-metrics-prod",
        "prefix": "victoria-metrics",
        "script_component": "metrics",
    },
    "vlogs": {
        "repo": "VictoriaMetrics/VictoriaLogs",
        "binary": "victoria-logs-prod",
        "prefix": "victoria-logs",
        "script_component": "logs",
    },
}

PLATFORM_ORDER = {
    "macos": 0,
    "linux": 1,
    "freebsd": 2,
    "openbsd": 3,
}
ARCH_ORDER = {
    "amd64": 0,
    "arm64": 1,
    "386": 2,
    "arm": 3,
    "ppc64le": 4,
    "s390x": 5,
    "riscv64": 6,
}


def github_json(url: str) -> object:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "weaver-guard-backend-sync",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def latest_release(repo: str) -> dict:
    return github_json(f"https://api.github.com/repos/{repo}/releases/latest")


def latest_jaeger_v1_release(repo: str) -> dict:
    releases = github_json(f"https://api.github.com/repos/{repo}/releases?per_page=50")
    for release in releases:
        tag = release.get("tag_name", "")
        if release.get("draft") or release.get("prerelease"):
            continue
        if re.fullmatch(r"v1\.\d+\.\d+", tag):
            return release
    raise SystemExit("failed to find a Jaeger v1 release with jaeger-all-in-one")


def platform_name(upstream_os: str) -> str:
    if upstream_os == "darwin":
        return "macos"
    return upstream_os


def sort_key(item: tuple[str, str]) -> tuple[int, int, str, str]:
    platform, arch = item
    return (
        PLATFORM_ORDER.get(platform, 100),
        ARCH_ORDER.get(arch, 100),
        platform,
        arch,
    )


def parse_jaeger_assets(release: dict) -> dict[tuple[str, str], dict]:
    tag = release["tag_name"]
    version = tag.removeprefix("v")
    pattern = re.compile(
        rf"^jaeger-{re.escape(version)}-(?P<os>darwin|linux)-"
        r"(?P<arch>amd64|arm64|ppc64le|s390x|riscv64)\.tar\.gz$"
    )
    assets: dict[tuple[str, str], dict] = {}
    for asset in release.get("assets", []):
        name = asset["name"]
        match = pattern.fullmatch(name)
        if not match:
            continue
        upstream_os = match.group("os")
        arch = match.group("arch")
        platform = platform_name(upstream_os)
        archive = f"jaeger-all-in-one-{version}-{upstream_os}-{arch}.tar.gz"
        assets[(platform, arch)] = {
            "version": tag,
            "archive": archive,
            "upstream_archive": name,
            "binary": BACKENDS["jaeger"]["binary"],
            "package": BACKENDS["jaeger"]["package"],
            "source_platform": upstream_os,
            "source_arch": arch,
            "source_url": asset["browser_download_url"],
            "source_size": asset.get("size"),
            "source_updated_at": asset.get("updated_at"),
            "local_candidates": [
                f".backend-downloads/{name}",
                f".jaeger/downloads/{name}",
            ],
        }
    return assets


def parse_victoria_assets(release: dict, backend_name: str) -> dict[tuple[str, str], dict]:
    tag = release["tag_name"]
    prefix = BACKENDS[backend_name]["prefix"]
    pattern = re.compile(
        rf"^{re.escape(prefix)}-(?P<os>darwin|linux|freebsd|openbsd)-"
        rf"(?P<arch>386|amd64|arm|arm64|s390x)-{re.escape(tag)}\.tar\.gz$"
    )
    assets: dict[tuple[str, str], dict] = {}
    for asset in release.get("assets", []):
        name = asset["name"]
        match = pattern.fullmatch(name)
        if not match:
            continue
        upstream_os = match.group("os")
        arch = match.group("arch")
        platform = platform_name(upstream_os)
        assets[(platform, arch)] = {
            "version": tag,
            "archive": name,
            "upstream_archive": name,
            "binary": BACKENDS[backend_name]["binary"],
            "script_component": BACKENDS[backend_name]["script_component"],
            "source_platform": upstream_os,
            "source_arch": arch,
            "source_url": asset["browser_download_url"],
            "source_size": asset.get("size"),
            "source_updated_at": asset.get("updated_at"),
            "local_candidates": [
                f".backend-downloads/{name}",
                f".victoria/downloads/{name}",
            ],
        }
    return assets


def parse_targets(value: str) -> list[tuple[str, str]]:
    targets: list[tuple[str, str]] = []
    for raw_item in value.split(","):
        item = raw_item.strip()
        if not item:
            continue
        if "-" not in item:
            raise SystemExit(f"invalid target {item!r}; expected platform-arch")
        platform, arch = item.split("-", 1)
        targets.append((platform, arch))
    return targets


def discover_assets(targets: list[tuple[str, str]]) -> tuple[dict, dict[str, str]]:
    jaeger_release = latest_jaeger_v1_release(BACKENDS["jaeger"]["repo"])
    metrics_release = latest_release(BACKENDS["vmetrics"]["repo"])
    logs_release = latest_release(BACKENDS["vlogs"]["repo"])
    by_backend = {
        "jaeger": parse_jaeger_assets(jaeger_release),
        "vmetrics": parse_victoria_assets(metrics_release, "vmetrics"),
        "vlogs": parse_victoria_assets(logs_release, "vlogs"),
    }
    common = set.intersection(*(set(value) for value in by_backend.values()))
    if not common:
        raise SystemExit("no common platform/arch set found for all backend assets")
    selected = set(targets)
    missing = selected - common
    if missing:
        formatted = ", ".join(f"{platform}-{arch}" for platform, arch in sorted(missing))
        raise SystemExit(f"requested target is not available for all backends: {formatted}")

    platforms: dict[str, dict[str, dict]] = {}
    for platform, arch in sorted(common & selected, key=sort_key):
        platform_assets = platforms.setdefault(platform, {})
        platform_assets[arch] = {}
        for backend, assets in by_backend.items():
            item = dict(assets[(platform, arch)])
            item["oss_path"] = (
                f"backends/{platform}-{arch}/{backend}/"
                f"{item['version']}/{item['archive']}"
            )
            item["local_candidates"].insert(
                0,
                f"{DEFAULT_DEST_DIR}/{item['oss_path']}",
            )
            platform_assets[arch][backend] = item

    versions = {
        "jaeger": jaeger_release["tag_name"],
        "vmetrics": metrics_release["tag_name"],
        "vlogs": logs_release["tag_name"],
    }
    return platforms, versions


def write_catalog(
    path: Path,
    oss_prefix: str,
    remote_prefix: str,
    targets: list[tuple[str, str]],
) -> dict:
    platforms, versions = discover_assets(targets)
    catalog = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "oss_prefix": oss_prefix.rstrip("/") + "/",
        "oss_key_prefix": remote_prefix.strip("/"),
        "sources": {
            "jaeger": {
                "repo": BACKENDS["jaeger"]["repo"],
                "version": versions["jaeger"],
                "selection": "latest v1.x release with jaeger-all-in-one",
            },
            "vmetrics": {
                "repo": BACKENDS["vmetrics"]["repo"],
                "version": versions["vmetrics"],
                "selection": "latest community release",
            },
            "vlogs": {
                "repo": BACKENDS["vlogs"]["repo"],
                "version": versions["vlogs"],
                "selection": "latest community release",
            },
        },
        "platforms": platforms,
    }
    path.write_text(json.dumps(catalog, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    return catalog


def replace_default_version(path: Path, variable: str, version: str) -> None:
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(rf'({re.escape(variable)}="\$\{{{re.escape(variable)}:-)[^}}]+(\}}")')
    updated, count = pattern.subn(rf"\g<1>{version}\g<2>", text, count=1)
    if count != 1:
        raise SystemExit(f"failed to update {variable} in {path}")
    if updated != text:
        path.write_text(updated, encoding="utf-8")
        print(f"[backend-sync] updated {path}: {variable}={version}")


def update_runtime_scripts(versions: dict[str, str]) -> None:
    replace_default_version(SCRIPT_DIR / "jaeger-macos-arm64.sh", "JAEGER_VERSION", versions["jaeger"])
    replace_default_version(SCRIPT_DIR / "victoria-macos-arm64.sh", "VM_VERSION", versions["vmetrics"])
    replace_default_version(SCRIPT_DIR / "victoria-macos-arm64.sh", "VL_VERSION", versions["vlogs"])


def iter_catalog_assets(catalog: dict) -> list[tuple[str, str, str, dict]]:
    assets: list[tuple[str, str, str, dict]] = []
    for platform, arches in sorted(catalog["platforms"].items()):
        for arch, backends in sorted(arches.items()):
            for name, asset in sorted(backends.items()):
                assets.append((platform, arch, name, asset))
    return assets


def stage_asset(catalog_path: Path, dest_root: Path, name: str, asset: dict) -> Path:
    dest = dest_root / asset["oss_path"]
    if dest.is_file():
        error = archive_policy_error(dest, name, asset)
        if error is None:
            print(f"[backend-sync] cached {name}: {dest}")
            return dest
        print(f"[backend-sync] rebuilding {name}: {error}")
        dest.unlink()

    candidate = first_existing_candidate(catalog_path, asset)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if candidate is not None:
        if asset.get("package") == "binary_only":
            repack_binary_only(candidate, dest, asset["binary"])
        else:
            verify_archive(candidate, name, asset)
            dest.write_bytes(candidate.read_bytes())
    else:
        if asset.get("package") == "binary_only":
            source = dest.parent / f".source-{asset['upstream_archive']}"
            try:
                download(asset["source_url"], source)
                repack_binary_only(source, dest, asset["binary"])
            finally:
                if source.exists():
                    source.unlink()
        else:
            download(asset["source_url"], dest)

    verify_archive(dest, name, asset)
    print(f"[backend-sync] staged {name}: {dest}")
    return dest


def stage_all(catalog_path: Path, dest_root: Path, catalog: dict) -> list[tuple[Path, str]]:
    staged: list[tuple[Path, str]] = []
    for platform, arch, name, asset in iter_catalog_assets(catalog):
        local = stage_asset(catalog_path, dest_root, name, asset)
        remote = f"{catalog['oss_key_prefix'].strip('/')}/{asset['oss_path']}"
        staged.append((local, remote))
        print(f"[backend-sync] ready {platform}/{arch}/{name}: {remote}")
    return staged


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        try:
            values[key] = shlex.split(value, comments=False, posix=True)[0]
        except IndexError:
            values[key] = ""
        except ValueError:
            values[key] = value.strip("'\"")
    return values


def upload(local: Path, remote: str, args: argparse.Namespace, env: dict[str, str]) -> None:
    cmd = [
        "cargo",
        "run",
        "--manifest-path",
        args.aivision_oss_manifest,
        "--",
        "upload",
        str(local),
        remote,
    ]
    print(f"[backend-sync] upload {local} -> {remote}")
    subprocess.run(cmd, check=True, env=env)


def upload_all(catalog_path: Path, staged: list[tuple[Path, str]], args: argparse.Namespace) -> None:
    env = os.environ.copy()
    env.update(load_env_file(Path(args.env_file)))
    env["HTTPS_PROXY"] = ""
    env["HTTP_PROXY"] = ""
    env["ALL_PROXY"] = ""
    env["NO_PROXY"] = NO_PROXY_HOST
    for local, remote in staged:
        upload(local, remote, args, env)
    upload(
        catalog_path,
        f"{args.remote_prefix.strip('/')}/{catalog_path.name}",
        args,
        env,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", default="backend-assets.json")
    parser.add_argument("--dest-dir", default=DEFAULT_DEST_DIR)
    parser.add_argument("--oss-prefix", default=DEFAULT_OSS_PREFIX)
    parser.add_argument("--remote-prefix", default=DEFAULT_REMOTE_PREFIX)
    parser.add_argument("--upload", action="store_true")
    parser.add_argument("--env-file", default=DEFAULT_OSS_ENV_FILE)
    parser.add_argument("--aivision-oss-manifest", default=DEFAULT_OSS_MANIFEST)
    parser.add_argument(
        "--targets",
        default=",".join(f"{platform}-{arch}" for platform, arch in DEFAULT_TARGETS),
        help="comma-separated platform-arch targets",
    )
    parser.add_argument("--no-update-runtime-scripts", action="store_true")
    args = parser.parse_args()
    if not args.oss_prefix.strip():
        parser.error("set --oss-prefix or WEAVER_GUARD_OSS_PREFIX to your asset host")

    catalog_path = Path(args.catalog)
    dest_root = Path(args.dest_dir)
    catalog = write_catalog(
        catalog_path,
        args.oss_prefix,
        args.remote_prefix,
        parse_targets(args.targets),
    )
    if not args.no_update_runtime_scripts:
        update_runtime_scripts(
            {
                name: source["version"]
                for name, source in catalog["sources"].items()
            }
        )
    staged = stage_all(catalog_path, dest_root, catalog)
    if args.upload:
        upload_all(catalog_path, staged, args)
    print(
        "[backend-sync] complete: "
        f"{len(staged)} assets, {len(catalog['platforms'])} platforms"
    )


if __name__ == "__main__":
    main()
