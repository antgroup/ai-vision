"""Package the Windows client and bind hosted installation evidence to its bytes."""

import argparse
import hashlib
from pathlib import Path
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile

ASSET = "aiv-windows-x64.zip"
RECEIPT = "windows-x64.json"
PLATFORMS = ["darwin/arm64", "darwin/x64", "linux/arm64", "linux/x64", "windows/x64"]
MEMBERS = ["aiv.exe", "LICENSE"]


def included(manifest):
    if "cli_platforms" not in manifest:
        return False
    if manifest["cli_platforms"] != PLATFORMS:
        raise ValueError("invalid release CLI platform set")
    return True


def require_amd64_pe(binary):
    dos = binary.read(64)
    if len(dos) == 64 and dos[:2] == b"MZ":
        offset = int.from_bytes(dos[60:64], "little")
        if 64 <= offset <= 4096:
            binary.read(offset - 64)
            if binary.read(6) == b"PE\0\0\x64\x86":
                return
    raise ValueError("aiv.exe must be a Windows x64 executable")


def pack(binary, license_path, archive):
    with binary.open("rb") as executable:
        require_amd64_pe(executable)
    archive.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(archive, "x", compression=ZIP_DEFLATED) as package:
        package.write(binary, MEMBERS[0])
        package.write(license_path, MEMBERS[1])
    verify_zip(archive)


def verify_zip(archive):
    try:
        with ZipFile(archive) as package:
            if sorted(package.namelist()) != sorted(MEMBERS) or any(
                not info.file_size for info in package.infolist()
            ):
                raise ValueError("Windows CLI archive requires aiv.exe and LICENSE")
            with package.open("aiv.exe") as executable:
                require_amd64_pe(executable)
    except BadZipFile as exc:
        raise ValueError("invalid Windows CLI ZIP") from exc


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def receipt_identity(archive, *, source_sha, build_id, product_version):
    return {
        "schema_version": 1,
        "status": "passed",
        "platform": "windows/amd64",
        "source_sha": source_sha,
        "build_id": build_id,
        "product_version": product_version,
        "archive": {"name": ASSET, "size": archive.stat().st_size, "sha256": sha256(archive)},
        "checks": {"install": True, "new_process_path": True, "version": True,
                   "space_key_auth": True, "trace_query": True},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--license", required=True, type=Path)
    parser.add_argument("--archive", required=True, type=Path)
    args = parser.parse_args()
    pack(args.binary, args.license, args.archive)


if __name__ == "__main__":
    main()
