"""Strip debug data from reviewed third-party AIO files before image assembly."""

import base64
import copy
import csv
import gzip
import hashlib
import io
import re
import subprocess
import tarfile
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory

from elf_strip import strip_debug


POLICY = "gnu-strip-debug-v1"
WHEELS = frozenset(("cffi", "greenlet", "httptools", "markupsafe", "mysql_connector_python",
                    "pyyaml", "sqlalchemy", "uvloop", "websockets", "wrapt"))
SHARED_LIBRARY = re.compile(r"\.so(?:\.[0-9]+)*$")
CPYTHON_EXTENSIONS = frozenset("""
    _blake2 _bz2 _codecs_cn _codecs_hk _codecs_iso2022 _codecs_jp _codecs_kr
    _codecs_tw _contextvars _csv _datetime _decimal _hashlib _heapq _lzma _md5
    _multibytecodec _opcode _pickle _posixsubprocess _random _sha1 _sha256
    _sha3 _sha512 _socket _ssl _statistics _struct _typing _uuid array
    binascii fcntl grp math select termios unicodedata zlib
""".split())
X64_PROVIDER = "agent-plugins/native-onboarding/linux-gnu-x64/connect.tar.gz"
PYTHON_SITE = ".venv/lib/python3.11/site-packages"


def record_digest(data):
    encoded = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")
    return "sha256=" + encoded


def delta(name, before, after):
    return {"path": name, "before_sha256": hashlib.sha256(before).hexdigest(), "before_bytes": len(before),
            "after_sha256": hashlib.sha256(after).hexdigest(), "after_bytes": len(after)}


def strip_wheels(site, architecture, tool):
    """Use the installed wheel's RECORD to bound the transformation and refresh its file identity."""
    changes = []
    for record in sorted(site.glob("*.dist-info/RECORD")):
        if record.parent.name.partition("-")[0].lower() not in WHEELS:
            continue
        with record.open(encoding="utf-8", newline="") as source:
            rows = list(csv.reader(source))
        updates = []
        for row in rows:
            if len(row) != 3:
                raise ValueError(f"invalid wheel RECORD row in {record}")
            name, digest, size = row
            relative = PurePosixPath(name)
            # RECORD entries outside site-packages can be wheel-installed scripts.
            if relative.is_absolute() or ".." in relative.parts or not SHARED_LIBRARY.search(relative.name):
                continue
            path = site / relative
            before = path.read_bytes()
            if not before.startswith(b"\x7fELF"):
                continue
            if digest != record_digest(before) or size != str(len(before)):
                raise ValueError(f"wheel RECORD does not match {path}")
            after = strip_debug(before, architecture, tool)
            if after != before:
                updates.append((path, after))
                row[1:] = [record_digest(after), str(len(after))]
                changes.append(delta(name, before, after))
        if updates:
            for path, data in updates:
                path.write_bytes(data)
            with record.open("w", encoding="utf-8", newline="") as output:
                csv.writer(output, lineterminator="\n").writerows(rows)
    return changes


def provider_runtime_member(name):
    parent, basename = name.rsplit("/", 1) if "/" in name else ("", name)
    return parent == "connect" and (
        basename == "libpython3.11.so.1.0"
        or (basename.endswith(".so") and basename.removesuffix(".so") in CPYTHON_EXTENSIONS)
    )


def strip_x64_provider(bundle, tool):
    """Keep the nested tar layout and the owned compiled installer byte for byte."""
    before_sha = hashlib.sha256(bundle.read_bytes()).hexdigest()
    before_size = bundle.stat().st_size
    changes = []
    with TemporaryDirectory(prefix="aiv-x64-provider-", dir=bundle.parent) as directory:
        staged = Path(directory) / bundle.name
        with tarfile.open(bundle, "r|gz") as source, staged.open("xb") as binary:
            with gzip.GzipFile(fileobj=binary, filename="", mode="wb", compresslevel=9, mtime=0) as compressed:
                with tarfile.open(fileobj=compressed, mode="w|") as writer:
                    for member in source:
                        payload = source.extractfile(member) if member.isreg() else None
                        if provider_runtime_member(member.name):
                            if payload is None:
                                raise ValueError(f"unexpected x64 CPython archive member: {member.name}")
                            before = payload.read()
                            if not before.startswith(b"\x7fELF"):
                                raise ValueError(f"expected an x64 CPython ELF: {member.name}")
                            after = strip_debug(before, "amd64", tool)
                            if after != before:
                                changes.append(delta(member.name, before, after))
                                member = copy.copy(member)
                                member.size = len(after)
                                member.pax_headers = {k: v for k, v in member.pax_headers.items() if k != "size"}
                            payload = io.BytesIO(after)
                        writer.addfile(member, payload)
        if changes:
            staged.chmod(bundle.stat().st_mode & 0o777)
            staged.replace(bundle)
    return {"path": X64_PROVIDER, "before_sha256": before_sha, "before_bytes": before_size,
            "after_sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(), "after_bytes": bundle.stat().st_size,
            "libraries": changes}


def slim_rootfs(rootfs, architecture, tools):
    versions = {}
    for arch in sorted({architecture, "amd64"}):
        version = subprocess.check_output([tools[arch], "--version"], text=True).splitlines()[0]
        if not version.startswith("GNU strip "):
            raise ValueError(f"expected a GNU strip tool for linux/{arch}")
        versions[arch] = version
    services = {}
    for root in ("opt/aivision/backend", "app"):
        services[root] = strip_wheels(rootfs / root / PYTHON_SITE, architecture, tools[architecture])
    return {"policy": POLICY, "tool_versions": versions, "service_libraries": services,
            "x64_provider": strip_x64_provider(rootfs / X64_PROVIDER, tools["amd64"])}
