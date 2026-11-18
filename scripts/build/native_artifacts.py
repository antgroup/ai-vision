#!/usr/bin/env python3
"""Inventory and audit an explicitly owned compiled-artifact staging tree.

This is a packaging boundary check, not a vulnerability or reverse-engineering
scanner. The builder must supply reviewed ownership roots from its compilation
and dependency manifests. Exact public-example paths require explicit approval
and a recorded reason; they remain owned examples, never dependencies. Dependency roots permit upstream Python; nested owned
package roots still prohibit it. Never infer ownership from a filename alone.

The manifest lives outside the audited tree. Files added, removed or changed
since inventory fail audit. Source checks cover conventional Python source,
bytecode, source archives and common embedded examples; encoded/obfuscated source
and strings inside native executables are outside this check's guarantees.
"""
from __future__ import annotations

import argparse
import ast
import bz2
import hashlib
import html
import io
import json
import lzma
from pathlib import Path, PurePosixPath
import re
import struct
import tarfile
import zipfile
import zlib


PYTHON_SUFFIXES = {".py", ".pyc", ".pyo", ".pyi", ".pyw", ".pyx", ".pxd", ".pxi"}
ARCHIVE_SUFFIXES = (".zip", ".whl", ".egg", ".pyz", ".tar", ".tar.gz", ".tgz", ".tar.xz", ".tar.bz2")
# Conservative bounds: fail closed when a staged archive cannot be inspected.
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 20000
MAX_ARCHIVE_DEPTH = 4
PAX_FIELDS = frozenset({"path", "linkpath", "size", "uid", "gid", "uname", "gname", "mtime", "atime", "ctime"})
NATIVE_MAGICS = (b"\x7fELF", b"MZ", b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca")
PYTHON_MARKERS = re.compile(
    r"(?m)(?:^|[>\n])\s*(?:from\s+[\w.]+\s+import\s|import\s+[\w.]+(?:\s+as\s+\w+)?(?:\s*,\s*[\w.]+)*[ \t]*(?:$|[;#])|(?:async\s+)?def\s+\w+\s*\(|class\s+\w+\s*[:(])"
    r"|#![^\n]*\bpython(?:\d(?:\.\d+)?)?\b"
    r"|\bpython(?:\d(?:\.\d+)?)?\s+(?:-[A-Za-z]*c\b|[^\n]*<<)"
    r"|(?m:^\s*-\s*python(?:\d(?:\.\d+)?)?\s*\n\s*-\s*-c\b)"
    r"|[\"']python(?:\d(?:\.\d+)?)?[\"']\s*,\s*[\"']-c[\"']"
)


def _safe_path(value):
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("invalid relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or str(path) != value:
        raise ValueError("invalid relative path: " + value)
    return path


def _under(path, root):
    return root == "." or path == root or path.startswith(root + "/")


def _roots(owned, dependencies):
    if not isinstance(owned, list) or not owned or not isinstance(dependencies, dict):
        raise ValueError("explicit ownership roots are required")
    for root in [*owned, *dependencies]:
        _safe_path(root)
    for root, provenance in dependencies.items():
        if not isinstance(provenance, str) or not provenance.strip():
            raise ValueError("dependency roots need upstream provenance")
        if any(own != "." and _under(root, own) for own in owned):
            raise ValueError("dependency root overlap with owned package: " + root)
    return owned, dependencies


def _owner(path, owned, dependencies):
    # An explicitly named owned package inside site-packages wins over runtime.
    if any(root != "." and _under(path, root) for root in owned):
        return "self"
    if any(_under(path, root) for root in dependencies):
        return "dependency"
    if "." in owned:
        return "self"
    raise ValueError("file has no explicit ownership: " + path)


def _files(root):
    if not root.is_dir() or root.is_symlink():
        raise ValueError("artifact root must be a real directory")
    result = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise ValueError("symlink requires a reviewed materialized artifact: " + relative)
        if path.is_file():
            result[relative] = path
        elif not path.is_dir():
            raise ValueError("unsupported artifact entry: " + relative)
    return result


def _public_examples(declarations, actual, owned, dependencies):
    if not isinstance(declarations, dict):
        raise ValueError("public examples must map exact files to approval reasons")
    for path, reason in declarations.items():
        _safe_path(path)
        if path not in actual or _owner(path, owned, dependencies) != "self":
            raise ValueError("public example must name an exact owned file: " + path)
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("public example requires an explicit approval reason: " + path)
    return declarations


def make_manifest(root, *, self_owned_roots, dependency_roots, public_examples=None):
    """Record all staged files, hashes and explicit self/dependency ownership."""
    owned, dependencies = _roots(self_owned_roots, dependency_roots)
    actual = _files(Path(root))
    examples = _public_examples(public_examples or {}, actual, owned, dependencies)
    records = []
    for relative, path in actual.items():
        data = path.read_bytes()
        records.append({"path": relative, "owner": "public-example" if relative in examples else _owner(relative, owned, dependencies),
                        "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)})
    return {"schema_version": 1, "self_owned_roots": owned,
            "dependency_roots": dependencies, "public_examples": examples, "files": records}


def tar_metadata_issues(name, *, pax_headers, uname="", gname=""):
    """Allow structural tar metadata only; it must not become a source carrier."""
    issues = []
    for field, value in [*pax_headers.items(), ("uname", uname), ("gname", gname)]:
        path = name + "@metadata:" + field
        if field not in PAX_FIELDS:
            issues.append(("uninspected-archive", path, "nonstandard tar metadata is not permitted"))
            continue
        value = str(value)
        if field in {"uid", "gid", "size"} and not re.fullmatch(r"[0-9]+", value):
            issues.append(("uninspected-archive", path, "invalid numeric tar metadata"))
        elif field in {"mtime", "atime", "ctime"} and not re.fullmatch(r"-?[0-9]+(?:\.[0-9]+)?", value):
            issues.append(("uninspected-archive", path, "invalid timestamp tar metadata"))
        elif field in {"uname", "gname"} and not re.fullmatch(r"[A-Za-z0-9_.+:-]{0,128}", value):
            issues.append(("uninspected-archive", path, "invalid owner tar metadata"))
        elif field in {"path", "linkpath"} and any(ord(character) < 32 or ord(character) == 127 for character in value):
            issues.append(("uninspected-archive", path, "control characters in tar metadata"))
        issues.extend(_source_issues(path, value.encode("utf-8")))
    return issues


def _metadata_text(name, data):
    text = data.decode("utf-8")
    if any(ord(character) < 32 or ord(character) == 127 for character in text):
        raise ValueError("control characters in archive metadata: " + name)
    if _source_issues(name + "@metadata", data):
        raise ValueError("Python program in archive metadata: " + name)
    return text


def _metadata_literals(name, data):
    # Numeric/binary header fields can still contain literal ASCII Python. A
    # leading newline avoids mistaking a printable run for a compressed file.
    for literal in re.findall(rb"[\t\r\n\x20-\x7e]+", data):
        if any(_source_issues(name + "@metadata", b"\n" + candidate)
               for candidate in [literal, *literal.splitlines()] if candidate.strip()):
            raise ValueError("literal Python in archive metadata")


def _read_exact(stream, size):
    chunks = []
    while size:
        chunk = stream.read(min(size, 65536))
        if not chunk:
            raise ValueError("truncated archive")
        chunks.append(chunk)
        size -= len(chunk)
    return b"".join(chunks)


def tar_stream_issues(name, stream, *, max_members=MAX_ARCHIVE_MEMBERS,
                      max_metadata_bytes=MAX_ARCHIVE_BYTES):
    """Check a complete raw TAR stream, skipping file bodies in bounded reads.

    This checks container metadata only, including overridden/unused fields and
    physical EOF. Callers retain their own ownership and member-body policy; an
    upstream Python filename or payload is not a source violation here. No seek or
    whole-layer read is needed, so image layers may exceed MAX_ARCHIVE_BYTES.
    """
    def field(header, start, end, label):
        value, separator, padding = header[start:end].partition(b"\0")
        if separator and padding.strip(b"\0"):
            raise ValueError("nonzero TAR field padding: " + label)
        return _metadata_text(name + "@" + label, value)

    def skip(size, *, zero=False):
        while size:
            chunk = _read_exact(stream, min(size, 65536))
            if zero and chunk.strip(b"\0"):
                raise ValueError("nonzero TAR payload padding")
            size -= len(chunk)

    def number(header, start, length):
        value = header[start:start + length]
        # Base-256 numbers are binary metadata, but can still carry a literal
        # ASCII Python program. Inspect printable runs without interpreting the
        # integer as encoded source or requiring the binary field to be UTF-8.
        _metadata_literals(name, value)
        # GNU base-256 fields have no unused bytes. Octal fields may contain
        # only their digits/terminators, including bytes after the first NUL.
        if value[:1] not in {b"\x80", b"\xff"} and re.fullmatch(b"[ 0-7\0]*", value) is None:
            raise ValueError("non-numeric bytes in TAR numeric field")
        tarfile.nti(value)

    try:
        count = metadata_bytes = 0
        while True:
            header = _read_exact(stream, 512)
            if header == b"\0" * 512:
                if _read_exact(stream, 512) != b"\0" * 512:
                    raise ValueError("invalid TAR end marker")
                while True:
                    trailing = stream.read(65536)
                    if not trailing:
                        return []
                    if trailing.strip(b"\0"):
                        raise ValueError("nonzero bytes after TAR end marker")
            count += 1
            if count > max_members:
                raise ValueError("TAR member limit exceeded")
            member = tarfile.TarInfo.frombuf(header, "utf-8", "strict")
            if member.size < 0 or member.type not in {b"0", b"\0", b"1", b"2", b"3", b"4", b"5", b"6", b"x", b"g", b"L", b"K"}:
                raise ValueError("unsupported TAR entry type or size")
            for start, length in ((100, 8), (108, 8), (116, 8), (124, 12), (136, 12),
                                  (148, 8), (329, 8), (337, 8)):
                number(header, start, length)
            if member.type not in {b"0", b"\0", b"x", b"g", b"L", b"K"} and member.size:
                raise ValueError("non-file TAR entry has an uninspected payload")
            for start, end, label in ((0, 100, "name"), (157, 257, "linkname"),
                                      (265, 297, "uname"), (297, 329, "gname")):
                field(header, start, end, label)
            if member.isreg() and header[157:257].strip(b"\0"):
                raise ValueError("unused linkname on regular TAR member")
            magic = header[257:265]
            if magic == b"ustar\x0000":
                field(header, 345, 500, "prefix")
                if header[500:].strip(b"\0"):
                    raise ValueError("nonzero unused USTAR header bytes")
            elif magic == b"ustar  \0":
                # Old GNU headers use atime/ctime instead of the USTAR prefix.
                for start in (345, 357):
                    number(header, start, 12)
                if header[369:].strip(b"\0"):
                    raise ValueError("unsupported GNU sparse/unused header metadata")
            else:
                raise ValueError("unsupported TAR header format")
            if member.type in {b"x", b"g", b"L", b"K"}:
                metadata_bytes += member.size
                if metadata_bytes > max_metadata_bytes:
                    raise ValueError("TAR metadata limit exceeded")
                payload = _read_exact(stream, member.size)
                if member.type in {b"L", b"K"}:
                    if not payload.endswith(b"\0"):
                        raise ValueError("unterminated GNU long name")
                    _metadata_text(name + "@gnu-name", payload[:-1])
                else:
                    position = 0
                    while position < len(payload):
                        separator = payload.find(b" ", position)
                        if separator < 0 or not payload[position:separator].isdigit():
                            raise ValueError("invalid PAX record length")
                        length = int(payload[position:separator])
                        record = payload[separator + 1:position + length]
                        if length <= separator - position + 1 or position + length > len(payload) or not record.endswith(b"\n"):
                            raise ValueError("truncated PAX record")
                        key, equals, value = record[:-1].partition(b"=")
                        if not equals:
                            raise ValueError("invalid PAX record")
                        issues = tar_metadata_issues(name, pax_headers={key.decode("utf-8"): value.decode("utf-8")})
                        if issues:
                            return issues
                        position += length
            else:
                skip(member.size)
            skip(-member.size % 512, zero=True)
    except (OSError, ValueError, UnicodeError, tarfile.TarError) as error:
        return [("uninspected-archive", name, str(error))]


def _tar_bytes(data):
    """Decode one bounded compression stream and account for its entire envelope."""
    if len(data) > MAX_ARCHIVE_BYTES:
        raise ValueError("compressed archive inspection budget exceeded")
    if data.startswith(b"\x1f\x8b"):
        if len(data) < 18 or data[2] != 8 or data[3] & 0xE0:
            raise ValueError("unsupported gzip header")
        flags, position = data[3], 10
        if flags & 4:
            raise ValueError("gzip extra fields are not permitted")
        for flag in (8, 16):
            if flags & flag:
                end = data.find(b"\0", position)
                if end < 0:
                    raise ValueError("unterminated gzip metadata")
                _metadata_text("gzip-header", data[position:end])
                position = end + 1
        if flags & 2:
            expected = struct.unpack("<H", data[position:position + 2])[0]
            if expected != zlib.crc32(data[:position]) & 0xFFFF:
                raise ValueError("gzip header checksum mismatch")
            position += 2
        decoder = zlib.decompressobj(-15)
        result = decoder.decompress(data[position:], MAX_ARCHIVE_BYTES + 1)
        if len(result) > MAX_ARCHIVE_BYTES or not decoder.eof or len(decoder.unused_data) < 8:
            raise ValueError("gzip expansion limit or truncated stream")
        crc, size = struct.unpack("<II", decoder.unused_data[:8])
        if crc != zlib.crc32(result) or size != len(result) % (1 << 32):
            raise ValueError("gzip payload checksum mismatch")
        if decoder.unused_data[8:].strip(b"\0"):
            raise ValueError("extra gzip stream or trailing bytes")
        return result
    if data.startswith((b"\xfd7zXZ\0", b"BZh")):
        decoder = lzma.LZMADecompressor(memlimit=MAX_ARCHIVE_BYTES) if data.startswith(b"\xfd7zXZ\0") else bz2.BZ2Decompressor()
        result = decoder.decompress(data, max_length=MAX_ARCHIVE_BYTES + 1)
        if len(result) > MAX_ARCHIVE_BYTES or not decoder.eof or decoder.unused_data:
            raise ValueError("compression expansion limit, truncated stream or trailing bytes")
        return result
    return data


def _zip_payload(data, method, expanded, crc):
    if expanded > MAX_ARCHIVE_BYTES:
        raise ValueError("ZIP expansion limit exceeded")
    if method == zipfile.ZIP_STORED:
        result = data
    else:
        if method == zipfile.ZIP_DEFLATED:
            decoder = zlib.decompressobj(-15)
        elif method == zipfile.ZIP_BZIP2:
            decoder = bz2.BZ2Decompressor()
        elif method == zipfile.ZIP_LZMA:
            # ZIP stores the LZMA1 properties before its raw stream. Decode the
            # fixed five-byte properties explicitly to retain bounded output and
            # unused_data checks, which ZipFile's wrapper does not expose.
            if len(data) < 9 or data[2:4] != b"\x05\0" or data[4] >= 225:
                raise ValueError("unsupported ZIP LZMA properties")
            dictionary = int.from_bytes(data[5:9], "little")
            if dictionary > MAX_ARCHIVE_BYTES:
                raise ValueError("ZIP LZMA dictionary exceeds memory bound")
            prop = data[4]
            filters = [{"id": lzma.FILTER_LZMA1, "dict_size": dictionary,
                        "lc": prop % 9, "lp": (prop // 9) % 5, "pb": prop // 45}]
            decoder = lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=filters)
            data = data[9:]
        else:
            raise ValueError("unsupported ZIP compression method")
        result = decoder.decompress(data, MAX_ARCHIVE_BYTES + 1)
        if not decoder.eof or decoder.unused_data:
            raise ValueError("truncated ZIP compression stream or unconsumed member bytes")
    if len(result) != expanded or zlib.crc32(result) != crc:
        raise ValueError("ZIP expanded size or checksum mismatch")


def _zip_metadata(data):
    """Require contiguous local records, central records and the exact ZIP end."""
    if len(data) > MAX_ARCHIVE_BYTES:
        raise ValueError("ZIP inspection budget exceeded")
    end = data.rfind(b"PK\x05\x06")
    if end < 0 or end + 22 != len(data):
        raise ValueError("ZIP comments or trailing bytes are not permitted")
    _, disk, central_disk, count, total, size, offset, comment = struct.unpack("<4s4H2IH", data[end:])
    _metadata_literals("zip-end", data[end:])
    if disk or central_disk or comment or count != total or offset + size != end:
        raise ValueError("unsupported ZIP directory structure")
    if total > MAX_ARCHIVE_MEMBERS:
        raise ValueError("ZIP member limit exceeded")
    local = 0
    central = offset
    expanded_total = 0
    for _ in range(total):
        header = data[central:central + 46]
        if len(header) != 46 or header[:4] != b"PK\x01\x02":
            raise ValueError("invalid ZIP central header")
        _metadata_literals("zip-central-header", header)
        values = struct.unpack("<4s6H3I5H2I", header)
        flags, method, crc, compressed, expanded = values[3], values[4], values[7], values[8], values[9]
        name_size, extra_size, comment_size, member_disk, local_offset = values[10], values[11], values[12], values[13], values[16]
        if extra_size or comment_size or member_disk or flags & ~0x80E or local_offset != local:
            raise ValueError("unsupported ZIP extra metadata, flags or local offsets")
        raw_name = data[central + 46:central + 46 + name_size]
        if len(raw_name) != name_size:
            raise ValueError("truncated ZIP filename")
        _metadata_text("zip-name", raw_name.decode("utf-8" if flags & 0x800 else "cp437").encode("utf-8"))
        if raw_name.endswith(b"/") and expanded:
            raise ValueError("ZIP directory has an uninspected payload")
        central += 46 + name_size
        header = data[local:local + 30]
        if len(header) != 30 or header[:4] != b"PK\x03\x04":
            raise ValueError("invalid ZIP local header")
        _metadata_literals("zip-local-header", header)
        record = struct.unpack("<4s5H3I2H", header)
        if record[2] != flags or record[3] != method or record[9] != name_size or record[10]:
            raise ValueError("ZIP local header/extra fields differ from reviewed directory")
        if data[local + 30:local + 30 + name_size] != raw_name:
            raise ValueError("ZIP local and central names differ")
        if flags & 8:
            if record[6:9] not in ((0, 0, 0), (crc, compressed, expanded)):
                raise ValueError("invalid ZIP streaming header")
        elif record[6:9] != (crc, compressed, expanded):
            raise ValueError("ZIP local sizes or CRC differ")
        local += 30 + name_size
        expanded_total += expanded
        if expanded_total > MAX_ARCHIVE_BYTES:
            raise ValueError("ZIP cumulative expansion limit exceeded")
        _zip_payload(data[local:local + compressed], method, expanded, crc)
        local += compressed
        if flags & 8:
            if data[local:local + 4] == b"PK\x07\x08":
                local += 4
            if data[local:local + 12] != struct.pack("<III", crc, compressed, expanded):
                raise ValueError("invalid ZIP data descriptor")
            local += 12
    if local != offset or central != end:
        raise ValueError("uninspected bytes between ZIP records")


def _source_issues(name, data, depth=0, budget=None):
    """Inspect owned files only; archive members inherit the owned boundary."""
    if budget is None:
        budget = {"bytes": 0, "members": 0}
    issues = []
    suffix = PurePosixPath(name).suffix.lower()
    if suffix in PYTHON_SUFFIXES:
        return [("owned-python", name, "owned Python source, stub or bytecode")]
    # CPython bytecode starts with a version-specific magic followed by CR LF.
    if len(data) >= 16 and data[2:4] == b"\r\n" and data[4:8] in (b"\0\0\0\0", b"\1\0\0\0", b"\3\0\0\0"):
        return [("owned-python", name, "possible extensionless CPython bytecode")]
    is_zip = data.startswith((b"PK\x03\x04", b"PK\x05\x06"))
    is_archive = is_zip or name.lower().endswith(ARCHIVE_SUFFIXES) or data[257:262] == b"ustar" or data.startswith((b"\x1f\x8b", b"\xfd7zXZ\0", b"BZh"))
    if is_archive:
        if depth >= MAX_ARCHIVE_DEPTH:
            return [("uninspected-archive", name, "archive nesting limit exceeded")]
        try:
            if is_zip:
                _zip_metadata(data)
            else:
                data = _tar_bytes(data)
                raw_issues = tar_stream_issues(name, io.BytesIO(data))
                if raw_issues:
                    return raw_issues
            archive = zipfile.ZipFile(io.BytesIO(data)) if is_zip else tarfile.open(fileobj=io.BytesIO(data), mode="r:")
            with archive:
                members = archive.infolist() if is_zip else archive.getmembers()
                for member in members:
                    member_name = member.filename if is_zip else member.name
                    member_path = name + "!" + member_name
                    if (member.is_dir() if is_zip else member.isdir()):
                        continue
                    _safe_path(member_name)
                    if (is_zip and (member.external_attr >> 16) & 0o170000 == 0o120000) or (not is_zip and not member.isfile()):
                        issues.append(("uninspected-archive", member_path, "archive links or special entries are not permitted"))
                        continue
                    size = member.file_size if is_zip else member.size
                    budget["bytes"] += size
                    budget["members"] += 1
                    if budget["bytes"] > MAX_ARCHIVE_BYTES or budget["members"] > MAX_ARCHIVE_MEMBERS:
                        return issues + [("uninspected-archive", member_path, "archive inspection budget exceeded")]
                    stream = archive.open(member) if is_zip else archive.extractfile(member)
                    with stream:
                        payload = stream.read(MAX_ARCHIVE_BYTES + 1)
                    issues.extend(_source_issues(member_path, payload, depth + 1, budget))
        except (OSError, ValueError, EOFError, struct.error, zlib.error, lzma.LZMAError, tarfile.TarError, zipfile.BadZipFile, RuntimeError) as error:
            issues.append(("uninspected-archive", name, str(error)))
        return issues
    if data.startswith(NATIVE_MAGICS):
        return []
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return []
    if re.search(r"Generated by Cython\b|Generated code for Python module|created by Nuitka version", text, re.IGNORECASE):
        return [("owned-python", name, "owned Cython/Nuitka generated Python source or build output")]
    if PYTHON_MARKERS.search(text) or re.search(r"```[ \t]*python(?:3)?\b", text, re.IGNORECASE):
        return [("owned-python", name, "owned Python script or embedded Python source")]
    snippets = [text] if not suffix else []
    if suffix == ".json":
        try:
            pending = [json.loads(text)]
            while pending:
                value = pending.pop()
                if isinstance(value, str):
                    snippets.append(value)
                elif isinstance(value, dict):
                    pending.extend(value.values())
                elif isinstance(value, list):
                    pending.extend(value)
        except ValueError:
            pass
    if suffix in {".html", ".htm", ".jinja", ".jinja2", ".j2"}:
        snippets.extend(html.unescape(re.sub(r"<[^>]*>", "", match))
                        for match in re.findall(r"<(?:pre|code)\b[^>]*>(.*?)</(?:pre|code)>", text, re.DOTALL | re.IGNORECASE))
    for snippet in snippets:
        if PYTHON_MARKERS.search(snippet):
            return [("owned-python", name, "embedded Python program")]
        try:
            tree = ast.parse(snippet)
        except (SyntaxError, ValueError):
            continue
        if any(isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
               or isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"print", "exec", "eval", "compile", "__import__"}
               for node in ast.walk(tree)):
            return [("owned-python", name, "extensionless Python program")]
    return []


def audit_artifact(root, manifest):
    """Return machine-readable violations; never execute an inspected artifact."""
    violations = []

    def issue(code, path, message):
        violations.append({"code": code, "path": path, "message": message})

    try:
        if manifest.get("schema_version") != 1:
            raise ValueError("unsupported manifest schema")
        owned, dependencies = _roots(manifest["self_owned_roots"], manifest["dependency_roots"])
        actual = _files(Path(root))
        examples = _public_examples(manifest.get("public_examples", {}), actual, owned, dependencies)
        records = {}
        for record in manifest["files"]:
            relative = record["path"]
            _safe_path(relative)
            if relative in records:
                raise ValueError("duplicate manifest path: " + relative)
            expected_owner = "public-example" if relative in examples else _owner(relative, owned, dependencies)
            if record["owner"] != expected_owner:
                raise ValueError("manifest ownership does not match package roots: " + relative)
            records[relative] = record
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        issue("invalid-manifest", "", str(error))
        return {"ok": False, "files_checked": 0, "violations": violations}
    for path in sorted(actual.keys() - records.keys()):
        issue("unmanifested", path, "file was not present in reviewed inventory")
    for path in sorted(records.keys() - actual.keys()):
        issue("missing", path, "manifested file is missing")
    checked = 0
    for relative in sorted(actual.keys() & records.keys()):
        record = records[relative]
        data = actual[relative].read_bytes()
        checked += 1
        if hashlib.sha256(data).hexdigest() != record.get("sha256") or len(data) != record.get("size"):
            issue("digest-mismatch", relative, "file changed after inventory")
        if record["owner"] == "self":
            for code, path, message in _source_issues(relative, data):
                issue(code, path, message)
    return {"ok": not violations, "files_checked": checked, "violations": violations}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    inventory = commands.add_parser("inventory")
    inventory.add_argument("root", type=Path)
    inventory.add_argument("--self-owned", action="append", required=True, help="owned package path relative to staging root")
    inventory.add_argument("--dependency", action="append", default=[], metavar="ROOT=PROVENANCE")
    inventory.add_argument("--public-example", action="append", default=[], metavar="FILE=APPROVAL_REASON")
    inventory.add_argument("--output", type=Path, required=True, help="manifest path outside staging root")
    audit = commands.add_parser("audit")
    audit.add_argument("root", type=Path)
    audit.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "inventory":
            if args.output.resolve().is_relative_to(args.root.resolve()):
                raise ValueError("manifest output must be outside the audited tree")
            dependencies = {}
            for value in args.dependency:
                key, separator, provenance = value.partition("=")
                if not separator or key in dependencies:
                    raise ValueError("dependencies must be distinct ROOT=PROVENANCE entries")
                dependencies[key] = provenance
            examples = {}
            for value in args.public_example:
                key, separator, reason = value.partition("=")
                if not separator or key in examples:
                    raise ValueError("public examples must be distinct FILE=APPROVAL_REASON entries")
                examples[key] = reason
            manifest = make_manifest(args.root, self_owned_roots=args.self_owned, dependency_roots=dependencies, public_examples=examples)
            args.output.write_text(json.dumps(manifest, indent=2) + "\n")
            return 0
        report = audit_artifact(args.root, json.loads(args.manifest.read_text()))
        print(json.dumps(report, indent=2))
        return 0 if report["ok"] else 1
    except (OSError, ValueError) as error:
        print(json.dumps({"ok": False, "error": str(error)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
