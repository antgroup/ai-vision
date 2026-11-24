"""Clean up a single diagnostics job's Docker-container builder."""

import argparse
import re
import subprocess
import sys


BUILDER_NAME = re.compile(r"aiv-diagnostics-[0-9]+-[0-9]+-[0-9]+-[0-9]+")


def resource_names(builder):
    if not BUILDER_NAME.fullmatch(builder):
        raise ValueError("Expected a job-owned diagnostics builder name")
    container = f"buildx_buildkit_{builder}0"
    return builder, container, f"{container}_state"


def docker(*arguments, timeout=30):
    return subprocess.run(
        ["docker", *arguments], capture_output=True, text=True, timeout=timeout,
    )


def remaining_resources(names):
    commands = (
        ("buildx", "ls", "--format", "{{.Name}}"),
        ("container", "ls", "--all", "--format", "{{.Names}}"),
        ("volume", "ls", "--format", "{{.Name}}"),
    )
    remaining = set()
    for name, command in zip(names, commands):
        result = docker(*command)
        if result.returncode:
            raise RuntimeError(f"Could not verify Docker state for {name}: {result.stderr.strip()}")
        if name in result.stdout.splitlines():
            remaining.add(name)
    return remaining


def verify_absent(builder):
    remaining = remaining_resources(resource_names(builder))
    if remaining:
        raise RuntimeError(f"Job-owned Buildx names already exist: {', '.join(sorted(remaining))}")


def try_remove(*command, timeout=30):
    try:
        result = docker(*command, timeout=timeout)
    except subprocess.TimeoutExpired:
        print(f"::warning::Docker {' '.join(command)} timed out; checking residual state", file=sys.stderr)
        return
    if result.returncode:
        print(f"::warning::Docker {' '.join(command)} exited {result.returncode}; checking residual state",
              file=sys.stderr)
        if result.stderr:
            print(result.stderr.strip(), file=sys.stderr)


def clean_builder(builder):
    names = resource_names(builder)
    _, container, volume = names
    remove_builder = ("buildx", "rm", "--force", "--timeout", "90s", builder)
    try_remove(*remove_builder, timeout=105)
    remaining = remaining_resources(names)
    if builder in remaining:
        try_remove(*remove_builder, timeout=105)
        remaining = remaining_resources(names)
    # Buildx can forget the builder even if Docker did not remove its volume.
    # Reconcile only resources whose exact names belong to this job.
    if remaining and builder not in remaining:
        if container in remaining:
            try_remove("container", "rm", "--force", container)
        if volume in remaining:
            try_remove("volume", "rm", volume)
        remaining = remaining_resources(names)
    if remaining:
        raise RuntimeError(f"Could not remove job-owned Buildx resources: {', '.join(sorted(remaining))}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("verify-absent", "clean"))
    parser.add_argument("builder")
    args = parser.parse_args()
    try:
        if args.operation == "verify-absent":
            verify_absent(args.builder)
        else:
            clean_builder(args.builder)
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
        parser.exit(1, f"::error::{error}\n")


if __name__ == "__main__":
    main()
