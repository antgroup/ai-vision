#!/usr/bin/env python3
"""Prepare zstd and gzip assets from a verified dual-architecture All-in-One preview."""

import argparse
import gzip
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from build import require_hash, sha256, stream_sha256, write_json


PREVIEW_TAG = re.compile(r"all-in-one-preview-v\d+\.\d+\.\d+-daily\.\d{8}(?:\.\d+)?")
CHECKSUM = re.compile(r"([a-f0-9]{64})\s+([a-zA-Z0-9_.-]+)")


def read_checksums(path):
    checksums = {}
    for line in path.read_text(encoding="ascii").splitlines():
        match = CHECKSUM.fullmatch(line)
        if not match or match[2] in checksums:
            raise ValueError(f"invalid or duplicate checksum in {path}")
        checksums[match[2]] = match[1]
    return checksums


def copy_archive(source, staged, name, metadata, checksums):
    digest = metadata["archive_sha256"]
    size = metadata["archive_bytes"]
    if metadata["archive"] != name or checksums.get(name) != digest or type(size) is not int or size <= 0:
        raise ValueError(f"manifest and SHA256SUMS do not agree on {name}")
    destination = staged / name
    shutil.copyfile(source / name, destination)
    if destination.stat().st_size != size:
        raise ValueError(f"unexpected archive size for {name}")
    require_hash(destination, digest)
    return destination


def compress(source, destination):
    digest = hashlib.sha256()
    with gzip.open(source, "rb") as original, destination.open("xb") as output:
        with subprocess.Popen(["zstd", "-q", "-T1", "-10", "-c"], stdin=subprocess.PIPE, stdout=output) as encoder:
            with encoder.stdin:
                for chunk in iter(lambda: original.read(1024 * 1024), b""):
                    digest.update(chunk)
                    encoder.stdin.write(chunk)
            if encoder.wait():
                raise ValueError(f"zstd compression failed for {source.name}")
    with subprocess.Popen(["zstd", "-qdc", destination], stdout=subprocess.PIPE) as decoder:
        restored = stream_sha256(decoder.stdout)
        if decoder.wait() or restored != digest.hexdigest():
            raise ValueError(f"the original Docker tar was not preserved for {source.name}")
    return restored


def repack(args):
    if not PREVIEW_TAG.fullmatch(args.release_tag):
        raise ValueError("specify an exact All-in-One preview tag")
    if args.output.exists():
        raise ValueError(f"use a new output directory: {args.output}")
    checksums = read_checksums(args.source / "SHA256SUMS")
    inputs = (("amd64", "all-in-one-manifest.json", "x64"), ("arm64", "all-in-one-manifest-arm64.json", "arm64"))
    manifests = []
    for arch, name, cli_arch in inputs:
        path = args.source / name
        require_hash(path, checksums.get(name))
        metadata = json.loads(path.read_text(encoding="utf-8"))
        if (metadata["schema_version"] != 1 or metadata["profile"] != "all-in-one-preview"
                or metadata["platform"] != f"linux/{arch}"
                or not PREVIEW_TAG.fullmatch(metadata["release_tag"])
                or metadata["release_tag"] == args.release_tag):
            raise ValueError(f"{name} must describe a different preview for linux/{arch}")
        if manifests and any(metadata[key] != manifests[0][3][key] for key in ("release_tag", "base_release")):
            raise ValueError("both manifests must describe the same source and base releases")
        manifests.append((arch, name, cli_arch, metadata))

    receipts = {}
    source_release = manifests[0][3]["release_tag"]
    with TemporaryDirectory(prefix=".aiv-repack-", dir=args.output.parent) as temporary:
        staged = Path(temporary) / "assets"
        staged.mkdir()
        for arch, name, cli_arch, metadata in manifests:
            stub = f"aiv-all-in-one-linux-{arch}.tar"
            original = copy_archive(args.source, staged, stub + ".gz", metadata, checksums)
            copy_archive(args.source, staged, f"aiv-linux-{cli_arch}.tar.gz", metadata["agent_cli"], checksums)
            compact = staged / (stub + ".zst")
            tar_sha256 = compress(original, compact)
            metadata.update(release_tag=args.release_tag, zstd_archive=compact.name,
                            zstd_archive_bytes=compact.stat().st_size, zstd_archive_sha256=sha256(compact))
            write_json(staged / name, metadata)
            receipts[metadata["platform"]] = {
                "image_id": metadata["image_id"], "tar_sha256": tar_sha256,
                "gzip_bytes": metadata["archive_bytes"], "zstd_bytes": metadata["zstd_archive_bytes"],
            }
        shutil.copyfile(args.installer, staged / "install-all-in-one.py")
        shutil.copyfile(args.sample_trace, staged / "sample-trace.py")
        (staged / "SHA256SUMS").write_text("".join(
            f"{sha256(path)}  {path.name}\n" for path in sorted(staged.iterdir())
        ), encoding="ascii")
        if args.output.exists():
            raise ValueError(f"use a new output directory: {args.output}")
        staged.rename(args.output)
    print(json.dumps({"source_release": source_release, "release_tag": args.release_tag,
                      "platforms": receipts}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for argument in ("source", "output", "installer", "sample-trace"):
        parser.add_argument(f"--{argument}", type=Path, required=True)
    parser.add_argument("--release-tag", required=True)
    try:
        repack(parser.parse_args())
    except (OSError, EOFError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f"{parser.prog}: {error}\n")


if __name__ == "__main__":
    main()
