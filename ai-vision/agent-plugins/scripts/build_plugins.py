#!/usr/bin/env python3
"""Build production-only, common-edition artifacts for this AI Vision deployment."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib

ROOT = Path(__file__).resolve().parents[1]
CATALOG = json.loads((ROOT / "agents.json").read_text())


def run(argv, cwd, **env):
    subprocess.run(argv, cwd=cwd, env={**os.environ, **env}, check=True)


def target_platform(target):
    if target:
        system = ("windows" if "windows" in target else "darwin" if "apple" in target
                  else "linux-musl" if "musl" in target else "linux-gnu")
        arch = "arm64" if target.startswith("aarch64") else "x64" if target.startswith("x86_64") else ""
    else:
        system = platform.system().lower()
        if system == "linux":
            system = "linux-musl" if platform.libc_ver()[0] == "musl" else "linux-gnu"
        arch = {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64", "arm64": "arm64"}.get(platform.machine().lower(), "")
    if system not in {"windows", "darwin", "linux-gnu", "linux-musl"} or not arch:
        raise ValueError("Supported builds: macOS/Linux/Windows, x64/arm64")
    return system, arch


def build(plugin, output, target, install_deps):
    source = ROOT / plugin["source"]
    if plugin.get("binary"):
        version = tomllib.loads((source / "Cargo.toml").read_text())["package"]["version"]
        system, arch = target_platform(target)
        command = ["cargo", "build", "--release", "--no-default-features", "--bin", plugin["binary"]]
        if plugin["features"]:
            command += ["--features", plugin["features"]]
        if target:
            command += ["--target", target]
        if (source / "Cargo.lock").exists():
            command.append("--locked")
        run(command, source, CARGO_TARGET_DIR=str(source / "target"))
        ext = ".exe" if system == "windows" else ""
        binary_name = plugin["binary"] + ext
        compiled = source / "target" / (target or "") / "release" / binary_name
        filename = f"{plugin['binary']}-{version}-{system}-{arch}{ext}"
    else:
        manifest = json.loads((source / "package.json").read_text())
        version = manifest["version"]
        if install_deps:
            run(["npm", "install", "--package-lock=false", "--ignore-scripts", "--workspaces=false"], source)
        run(["npm", "run", "build", "--workspaces=false"], source, EDITION="common")
        system, arch = "all", "all"
        filename = f"{plugin['package']}-{version}.tgz"
    destination = output / plugin["key"] / version
    destination.mkdir(parents=True, exist_ok=True)
    final = destination / filename
    with tempfile.TemporaryDirectory(dir=destination) as staging:
        staged = Path(staging) / filename
        if plugin.get("binary"):
            shutil.copy2(compiled, staged)
        else:
            package = Path(staging) / "package"
            package.mkdir()
            shutil.copytree(source / "dist", package / "dist")
            for name in ("index.js", "openclaw.plugin.json", "opencode.plugin.json", "dsh.plugin.json"):
                if (source / name).is_file():
                    shutil.copy2(source / name, package / name)
            # Bundles contain their runtime dependencies; clients never build source.
            for field in ("dependencies", "devDependencies", "scripts", "publishConfig", "repository"):
                manifest.pop(field, None)
            (package / "package.json").write_text(json.dumps(manifest, indent=2) + "\n")
            with tarfile.open(staged, "w:gz") as archive:
                archive.add(package, arcname="package")
        checksum = hashlib.sha256(staged.read_bytes()).hexdigest()
        os.replace(staged, final)
    index = output / plugin["key"] / "latest.json"
    latest = json.loads(index.read_text()) if index.exists() else {}
    downloads = latest.get("downloads", []) if latest.get("version") == version else []
    downloads = [d for d in downloads if (d["platform"], d["arch"]) != (system, arch)]
    downloads.append({"filename": filename, "platform": system, "arch": arch,
                      "collection_modes": ["full", "privacy"],
                      "label": f"{system} {arch}", "sha256": checksum,
                      "target_filename": binary_name if plugin.get("binary") else f"{plugin['package']}.tgz"})
    temporary = index.with_suffix(".tmp")
    temporary.write_text(json.dumps({"version": version, "downloads": downloads}, indent=2) + "\n")
    os.replace(temporary, index)
    print(f"Ready: {plugin['name']} {version} {system}/{arch}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", default="all", choices=["all"] + [p["key"] for p in CATALOG])
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    parser.add_argument("--target", help="Rust target triple; cross linker must already be installed")
    parser.add_argument("--install-deps", action="store_true")
    args = parser.parse_args()
    # Best-effort: a plugin needs a toolchain the build host may not have (cargo
    # for the Rust hooks, npm for the Node packages). One agent's missing
    # toolchain must not block the others or abort the deploy — skip it with a
    # warning so the rest stay installable.
    built, skipped = [], []
    for plugin in CATALOG:
        if args.agent not in {"all", plugin["key"]}:
            continue
        if plugin.get("preview") and args.agent == "all":
            continue
        if plugin.get("source_install"):
            if args.agent != "all":
                parser.error(f"{plugin['name']} uses its documented source installer")
            continue
        try:
            build(plugin, args.output.resolve(), args.target, args.install_deps)
        except Exception as exc:  # noqa: BLE001 - surface and continue, never abort siblings
            skipped.append(plugin["key"])
            reason = exc.stderr.strip().splitlines()[-1] if isinstance(
                exc, subprocess.CalledProcessError) and exc.stderr else str(exc)
            print(f"warn: skipping {plugin['name']} build: {reason}", file=sys.stderr)
        else:
            built.append(plugin["key"])
    # When a single agent was explicitly requested, honour its failure as a
    # non-zero exit. For the default "all", missing agents are only a warning.
    if args.agent != "all" and skipped:
        raise SystemExit(1)
    if skipped:
        print(f"Built {len(built)} plugin(s); skipped {len(skipped)} (no artifact): "
              + ", ".join(skipped), file=sys.stderr)


if __name__ == "__main__":
    main()
