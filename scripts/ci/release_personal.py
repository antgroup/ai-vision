"""Collect both qualified Personal Edition builds into the same release."""

import argparse
import json
from pathlib import Path
import re

from release_assets import (
    PERSONAL_ARCHITECTURES,
    PERSONAL_ATTACHMENTS,
    PERSONAL_MAX_ATTACHMENT_SIZE,
    file_sha256,
    personal_files,
    write_checksums,
)

SHA256 = re.compile(r"[0-9a-f]{64}")
REQUIRED_CHECKS = ("startup", "signup", "trace", "diagnosis", "shutdown", "recreation", "cleanup")


def edition_identity(inventory, metadata, product, architecture):
    platform = f"linux/{architecture}"
    manifest, gzip_name, zstd_name = personal_files(architecture)
    cli = f"aiv-linux-{'x64' if architecture == 'amd64' else 'arm64'}.tar.gz"
    source = {key: product[key] for key in ("source_repository", "source_sha", "release_tag", "build_id")}
    expected = {
        "schema_version": 1,
        "profile": "all-in-one",
        "platform": platform,
        "release_tag": product["release_tag"],
        "base_release": product["release_tag"],
        "source_candidate": source,
        "archive": gzip_name,
        "archive_bytes": inventory[gzip_name]["size"],
        "archive_sha256": inventory[gzip_name]["sha256"],
        "zstd_archive": zstd_name,
        "zstd_archive_bytes": inventory[zstd_name]["size"],
        "zstd_archive_sha256": inventory[zstd_name]["sha256"],
    }
    if any(metadata.get(key) != value for key, value in expected.items()):
        raise ValueError(f"Personal Edition manifest differs from the {platform} candidate bytes or identity")
    if any(not 0 < inventory[name]["size"] < PERSONAL_MAX_ATTACHMENT_SIZE for name in (gzip_name, zstd_name)):
        raise ValueError(f"{platform} Personal Edition exceeds the release attachment limit")
    agent = metadata.get("agent_cli", {})
    if (agent.get("archive") != cli or agent.get("version") != product["product_version"]
            or agent.get("archive_bytes") != inventory[cli]["size"]
            or agent.get("archive_sha256") != inventory[cli]["sha256"]
            or not SHA256.fullmatch(agent.get("binary_sha256", ""))):
        raise ValueError(f"Personal Edition must use the frozen {platform} CLI")
    if (not re.fullmatch(r"sha256:[0-9a-f]{64}", metadata.get("image_id", ""))
            or not re.fullmatch(r"aiv-all-in-one:[a-z0-9][a-z0-9._-]*", metadata.get("image_tag", ""))
            or not SHA256.fullmatch(metadata.get("tar_sha256", ""))):
        raise ValueError(f"invalid {platform} Personal Edition image identity")
    return {
        "schema_version": 1,
        "status": "passed",
        "platform": platform,
        "source_sha": source["source_sha"],
        "build_id": source["build_id"],
        "release_tag": source["release_tag"],
        "manifest_sha256": inventory[manifest]["sha256"],
        "archive": gzip_name,
        "archive_sha256": inventory[gzip_name]["sha256"],
        "zstd_archive_sha256": inventory[zstd_name]["sha256"],
        "tar_sha256": metadata["tar_sha256"],
        "image_id": metadata["image_id"],
        "checks": dict.fromkeys(REQUIRED_CHECKS, True),
    }


def expected_receipts(directory, inventory, product):
    return {
        architecture: edition_identity(
            inventory,
            json.loads((directory / personal_files(architecture)[0]).read_text()),
            product,
            architecture,
        )
        for architecture in PERSONAL_ARCHITECTURES
    }


def attach(directory, personal):
    for architecture in PERSONAL_ARCHITECTURES:
        bundle = personal / f"personal-assets-{architecture}"
        for name in personal_files(architecture):
            source = bundle / name
            if not source.is_file() or source.is_symlink() or (directory / name).exists():
                raise ValueError(f"missing or duplicate {architecture} asset: {name}")
            source.rename(directory / name)
    first, second = (personal / f"personal-assets-{architecture}" for architecture in PERSONAL_ARCHITECTURES)
    for name in sorted(PERSONAL_ATTACHMENTS):
        source = first / name
        if (not source.is_file() or source.is_symlink() or (second / name).is_symlink()
                or (directory / name).exists() or file_sha256(source) != file_sha256(second / name)):
            raise ValueError(f"Personal Edition acceptance used different {name} bytes")
        source.rename(directory / name)
    write_checksums(directory)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--personal-assets", type=Path, required=True)
    args = parser.parse_args()
    attach(args.directory, args.personal_assets)


if __name__ == "__main__":
    main()
