"""Validate native aiv executables and archive metadata locally."""

import ast
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import tarfile
import tempfile
import textwrap
import zlib

ATTRIBUTIONS = ("LICENSE", "deploy/kubernetes/README.md",
                "deploy/kubernetes/dependencies/cert-manager.LICENSE",
                "deploy/kubernetes/dependencies/ob-operator.LICENSE")
CLI_MEMBERS = {"aiv", *ATTRIBUTIONS}
CLI_MODULE = "github.com/antgroup/ai-vision/aivision-cli"
CLI_VERSION_SYMBOL = CLI_MODULE + "/internal/cli.Version"
NATIVE_CLI_TAG = "native_distribution"
SOURCE_ROOT = Path(__file__).resolve().parents[2]


def cli_python_programs(root=SOURCE_ROOT):
    """Known owned programs that the native CLI must exclude at link time.

    These are source payloads, not filenames or interpreter names. The native
    Go embed/profile tests establish their compile-time exclusion; this check
    also rejects a tagged executable to which a source payload was appended.
    It is not a general detector for arbitrary encoded source in a binary.
    """
    root = Path(root)
    programs = {}
    for name in ("backend-bootstrap.py", "database-init.py", "wait-http.py"):
        relative = "deploy/bootstrap/" + name
        path = root / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError("missing trusted CLI source-boundary input: " + relative)
        data = path.read_bytes()
        if len(data) < 64:
            raise ValueError("empty or unexpected CLI source-boundary input: " + relative)
        programs[relative] = data

    relative = "deploy/kubernetes/base/database-init.yaml"
    path = root / relative
    if path.is_symlink() or not path.is_file():
        raise ValueError("missing trusted CLI source-boundary input: " + relative)
    lines = path.read_text().splitlines(keepends=True)
    blocks = []
    for index, line in enumerate(lines):
        if line.strip() != "- |":
            continue
        indent = len(line) - len(line.lstrip())
        body = []
        for following in lines[index + 1:]:
            if following.strip() and len(following) - len(following.lstrip()) <= indent:
                break
            body.append(following)
        raw = "".join(body)
        script = textwrap.dedent(raw)
        try:
            ast.parse(script)
        except SyntaxError:
            continue
        if script.lstrip().startswith(("import ", "from ")):
            blocks.append((raw.encode(), script.encode()))
    if len(blocks) != 1:
        raise ValueError("review the Kubernetes CLI Python source-boundary inventory")
    programs[relative + ":literal"] = blocks[0][0]
    programs[relative + ":dedented"] = blocks[0][1]
    programs["legacy Python version probe"] = b"import sys; sys.exit(sys.version_info < (3, 11))"
    programs["legacy Python HTTP health probe"] = b"import urllib.request; r=urllib.request.urlopen("
    return programs


def require_cli_native_payload(binary, *, root=SOURCE_ROOT):
    programs = cli_python_programs(root)
    for name, program in programs.items():
        if program in binary:
            raise ValueError("CLI executable contains first-party Python program bytes: " + name)
    return {name: {"sha256": hashlib.sha256(program).hexdigest(), "bytes": len(program)}
            for name, program in programs.items()}


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_cli_build_info(binary, *, go="go"):
    """Read foreign executable metadata with the host Go tool, never run it."""
    with tempfile.TemporaryDirectory(prefix="aivision-cli-metadata-") as temporary:
        executable = Path(temporary) / "aiv"
        executable.write_bytes(binary)
        # The input deliberately remains nonexecutable on the host.
        executable.chmod(0o600)
        return json.loads(subprocess.check_output(
            [str(go), "version", "-m", "-json", str(executable)], text=True))


def require_cli_build_info(info, *, product_version, platform, source_sha):
    if not isinstance(info, dict) or info.get("Path") != CLI_MODULE or (info.get("Main") or {}).get("Path") != CLI_MODULE:
        raise ValueError("CLI executable must identify the actual aiv module")
    rows = info.get("Settings", [])
    settings = {row["Key"]: row["Value"] for row in rows}
    if len(rows) != len(settings):
        raise ValueError("CLI executable has duplicate Go build settings")
    os_name, arch = platform.split("-", 1)
    expected_arch = "amd64" if arch == "x64" else arch
    if settings.get("GOOS") != os_name or settings.get("GOARCH") != expected_arch:
        raise ValueError("CLI executable platform differs from its selected archive")
    tag_value = settings.get("-tags", "")
    tags = re.split(r"[,\s]+", tag_value) if isinstance(tag_value, str) else []
    if NATIVE_CLI_TAG not in tags:
        raise ValueError("native CLI executable requires the native_distribution build tag")
    flags = shlex.split(settings.get("-ldflags", ""))
    assignments = []
    for index, flag in enumerate(flags):
        if flag == "-X" and index + 1 < len(flags):
            assignments.append(flags[index + 1])
        elif flag.startswith("-X="):
            assignments.append(flag[3:])
    versions = [value.partition("=")[2] for value in assignments
                if value.partition("=")[0] == CLI_VERSION_SYMBOL]
    if versions != [product_version]:
        raise ValueError("CLI executable version differs from the selected product version")
    if settings.get("vcs.modified") != "false" or settings.get("vcs.revision") != source_sha:
        raise ValueError("CLI executable VCS identity differs from the frozen product source")
    return info


def cli_tar_bytes(path):
    """Read one gzip stream without permitting opaque optional metadata."""
    encoded = Path(path).read_bytes()
    if len(encoded) < 18 or encoded[:3] != b"\x1f\x8b\x08" or encoded[3] not in (0, 8):
        raise ValueError("CLI gzip metadata permits only a structural header and optional archive filename")
    if encoded[3] == 8:
        name, separator, _ = encoded[10:].partition(b"\0")
        expected = Path(path).name.removesuffix(".gz").encode("utf-8")
        if not separator or name != expected or not re.fullmatch(rb"[A-Za-z0-9_.-]+\.tar", name):
            raise ValueError("CLI gzip metadata filename must match its archive basename")
    try:
        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
        data = decoder.decompress(encoded) + decoder.flush()
    except zlib.error as error:
        raise ValueError("invalid CLI gzip metadata or compressed stream") from error
    if not decoder.eof or decoder.unused_data:
        raise ValueError("CLI gzip metadata forbids extra streams or trailing bytes")
    return data


def cli_archive_members(path):
    """Inspect raw tar records before extension headers can override fields.

    These five short member names need no PAX, global, GNU longname, sparse or
    link records. Reject those records entirely, including their hidden bodies.
    Structural numbers and owner names are bounded by their actual field syntax;
    unused fields, member padding and the trailer must contain only zero bytes.
    """
    data = cli_tar_bytes(path)
    members = {}
    offset = 0
    while offset + 512 <= len(data) and any(data[offset:offset + 512]):
        header = data[offset:offset + 512]
        if header[156:157] not in (tarfile.REGTYPE, tarfile.AREGTYPE):
            raise ValueError("CLI tar metadata forbids extended headers and nonregular records")
        if header[257:265] not in (tarfile.POSIX_MAGIC, tarfile.GNU_MAGIC):
            raise ValueError("CLI tar metadata requires a supported structural header")
        if any(header[157:257]) or any(header[345:]):
            raise ValueError("CLI tar metadata forbids link names, prefixes and unused header fields")
        for start, end in ((100, 108), (108, 116), (116, 124), (124, 136),
                           (136, 148), (148, 156), (329, 337), (337, 345)):
            if not re.fullmatch(rb"[0-7]*", header[start:end].strip(b"\0 ")):
                raise ValueError("CLI tar metadata requires structural octal numeric fields")
        for start, end in ((0, 100), (265, 297), (297, 329)):
            value, _, padding = header[start:end].partition(b"\0")
            if any(padding) or (start != 0 and not re.fullmatch(rb"[A-Za-z0-9_.+:-]*", value)):
                raise ValueError("CLI tar metadata contains a nonstructural name or owner field")
        try:
            member = tarfile.TarInfo.frombuf(header, "ascii", "strict")
        except (tarfile.HeaderError, UnicodeError) as error:
            raise ValueError("invalid CLI tar metadata header") from error
        if member.name not in CLI_MEMBERS or member.name in members:
            raise ValueError("CLI archive requires exactly the executable, license and Kubernetes attribution files")
        if member.mode & ~0o777 or member.devmajor or member.devminor:
            raise ValueError("CLI tar metadata contains nonregular file attributes")
        start = offset + 512
        end = start + member.size
        offset = start + (member.size + 511) // 512 * 512
        if offset > len(data) or any(data[end:offset]):
            raise ValueError("CLI tar metadata requires complete members and zero member padding")
        members[member.name] = (member, data[start:end])
    if len(data) - offset < 1024 or len(data) % 512 or any(data[offset:]):
        raise ValueError("CLI tar metadata requires a zero trailer without extra records or payloads")
    if set(members) != CLI_MEMBERS:
        raise ValueError("CLI archive requires exactly the executable, license and Kubernetes attribution files")
    return members


def require_archive(path, *, product_version, platform, source_sha, go="go"):
    members = cli_archive_members(path)
    for name in sorted(CLI_MEMBERS - {"aiv"}):
        trusted = SOURCE_ROOT / name
        if trusted.is_symlink() or not trusted.is_file() or members[name][1] != trusted.read_bytes():
            raise ValueError("CLI archive requires exact trusted source attribution bytes: " + name)
    binary, binary_bytes = members["aiv"]
    if not binary.size or binary.mode & 0o111 == 0:
        raise ValueError("CLI archive executable must be nonempty and executable")
    source_boundary = require_cli_native_payload(binary_bytes)
    info = read_cli_build_info(binary_bytes, go=go)
    require_cli_build_info(info, product_version=product_version, platform=platform, source_sha=source_sha)
    return dict(archive_sha256=sha256(path), executable_sha256=hashlib.sha256(binary_bytes).hexdigest(),
                build_info=info, source_boundary=source_boundary)
