#!/usr/bin/env python3
"""Build an exact Personal Edition candidate on a native Linux Docker host."""

import argparse
import os
from pathlib import Path
import platform
import subprocess
import sys
from tempfile import TemporaryDirectory

from build import PLATFORMS, preparation_source
from daily import BASE, ROOT, candidate

MACHINES = {"amd64": "x86_64", "arm64": "aarch64"}
OWNER_BUILD = """
if ! command -v cc >/dev/null; then
    apt-get update
    apt-get install -y --no-install-recommends gcc libc6-dev
fi
cc -std=gnu11 -O2 -Wall -Wextra -Werror $(python3-config --embed --cflags) \
    /source/main.c $(python3-config --embed --ldflags) -o /out/aivision-seekdb
"""


def run(*args, **kwargs):
    subprocess.run(args, check=True, **kwargs)


def build(architecture, assets, acceptance, output):
    if platform.system() != "Linux" or platform.machine() != MACHINES[architecture]:
        raise ValueError(f"Personal Edition requires a native Linux {architecture} runner")
    target = f"linux/{architecture}"
    docker_platform = subprocess.check_output(
        ["docker", "version", "--format", "{{.Server.Os}}/{{.Server.Arch}}"], text=True
    ).strip()
    if docker_platform != target:
        raise ValueError("Docker daemon architecture differs from the host")
    manifest, _ = candidate(assets, acceptance, expected_source=preparation_source())
    wheel_platform = "manylinux_2_28_" + MACHINES[architecture]
    wheels = PLATFORMS[architecture]["wheels"]
    source = Path(__file__).resolve().parent
    with TemporaryDirectory(prefix="aiv-personal-build-", dir=output.parent) as temp:
        work = Path(temp)
        requirements = work / "requirements.txt"
        locked = []
        for filename, digest in wheels.items():
            distribution, version, _ = filename.split("-", 2)
            locked.append(f"{distribution}=={version} --hash=sha256:{digest}\n")
        requirements.write_text("".join(locked))
        run(sys.executable, "-m", "pip", "download", "--require-hashes", "--only-binary=:all:",
            "--no-deps", "--platform", wheel_platform, "--python-version", "311", "--abi", "cp311",
            "--dest", str(work), "--requirement", str(requirements))
        run("docker", "pull", "--platform", target, BASE)
        for reference in manifest["images"].values():
            run("docker", "pull", "--platform", target, reference)
        supervisor = work / "aivision-all-in-one"
        run("go", "build", "-trimpath", "-ldflags=-s -w", "-o", str(supervisor),
            str(source / "main.go"), cwd=ROOT, env={**os.environ, "CGO_ENABLED": "0"})
        run("docker", "run", "--rm", "--platform", target,
            "-v", f"{source / 'seekdb-owner'}:/source:ro", "-v", f"{work}:/out", BASE,
            "sh", "-ceu", OWNER_BUILD)
        wheel, pymysql = sorted(wheels, key=lambda name: not name.startswith("pylibseekdb-"))
        command = (sys.executable, str(source / "build.py"))
        frozen = ("--daily-assets", str(assets), "--acceptance", str(acceptance))
        context = work / "context"
        run(*command, "prepare-daily", *frozen, "--architecture", architecture, "--base", BASE,
            "--wheel", str(work / wheel), "--pymysql-wheel", str(work / pymysql),
            "--supervisor", str(supervisor), "--db-owner", str(work / "aivision-seekdb"),
            "--context", str(context))
        image_tag = f"aiv-all-in-one:{manifest['product_version'].lower()}-{architecture}"
        archive = work / "image.tar.gz"
        run(*command, "build", "--context", str(context), "--tag", image_tag, "--archive", str(archive))
        run(*command, "package-daily", *frozen, "--archive", str(archive), "--output", str(output))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--architecture", choices=MACHINES, required=True)
    parser.add_argument("--daily-assets", type=Path, required=True)
    parser.add_argument("--acceptance", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(args.architecture, args.daily_assets.resolve(), args.acceptance.resolve(), args.output.resolve())


if __name__ == "__main__":
    main()
