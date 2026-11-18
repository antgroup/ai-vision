#!/usr/bin/env python3
"""Check native backend dependencies in a built image without external services.

Usage: python3 docker/smoke.py IMAGE [--baseline UNSTRIPPED_IMAGE] [--output FILE]
The baseline must use the same architecture, Python version and dependency lock.
Database migrations and end-to-end behavior require separate deployment tests.
"""
import argparse
import json
from pathlib import Path
import subprocess

NATIVE_PROBE = r'''
"""Exercise BE native extensions after stripping, using only candidate packages."""
import importlib
import json
import ssl
import sys
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import grpc
from google.protobuf.struct_pb2 import Struct
from mysql.connector import connection_cext

modules = ("_mysql_connector", "mysql.connector", "cryptography.hazmat.bindings._rust", "grpc._cython.cygrpc", "google._upb._message", "yaml._yaml", "_cffi_backend", "django", "gunicorn")
loaded = []
for name in modules:
    try:
        loaded.append(importlib.import_module(name).__file__)
    except ModuleNotFoundError:
        if name != "_cffi_backend":
            raise
key = Ed25519PrivateKey.generate()
message = b"aivision-native-strip-regression"
key.public_key().verify(key.sign(message), message)
aes = AESGCM(AESGCM.generate_key(bit_length=128))
nonce = b"\x01" * 12
assert aes.decrypt(nonce, aes.encrypt(nonce, message, b"aivision"), b"aivision") == message
payload = Struct()
payload.update({"ok": True, "value": 123.5})
assert Struct.FromString(payload.SerializeToString()) == payload
server = grpc.server(__import__("concurrent.futures").futures.ThreadPoolExecutor(max_workers=1))
assert server.add_insecure_port("127.0.0.1:0") > 0
server.start()
server.stop(0).wait()
assert connection_cext.CMySQLConnection is not None
print(json.dumps({"python": sys.version, "executable": sys.executable, "openssl": ssl.OPENSSL_VERSION,
                  "loaded_extensions": loaded, "ed25519": "PASS", "aesgcm": "PASS", "protobuf": "PASS", "grpc_server": "PASS"}))
'''

NATIVE_MANIFEST = r'''
"""Record native library execution sections separately from debug metadata."""
import hashlib
import json
from pathlib import Path
import struct

rows = {}
for path in sorted(Path('/app/.venv').rglob('*')):
    if not path.is_file() or path.is_symlink() or '.so' not in path.name:
        continue
    data = path.read_bytes()
    if data[:5] != b'\x7fELF\x02':
        continue
    endian = '<' if data[5] == 1 else '>'
    shoff = struct.unpack_from(endian + 'Q', data, 40)[0]
    size, count, names_index = struct.unpack_from(endian + 'HHH', data, 58)
    sections = [struct.unpack_from(endian + 'IIQQQQIIQQ', data, shoff + i * size) for i in range(count)]
    names_header = sections[names_index]
    names = data[names_header[4]:names_header[4] + names_header[5]]
    allocated, debug = {}, 0
    for header in sections:
        name = names[header[0]:].split(b'\0', 1)[0].decode()
        if name.startswith(('.debug', '.zdebug')):
            debug += header[5]
        if header[2] & 2:
            payload = b'' if header[1] == 8 else data[header[4]:header[4] + header[5]]
            allocated[name] = {'type': header[1], 'flags': header[2], 'size': header[5],
                               'sha256': hashlib.sha256(payload).hexdigest()}
    rows[str(path)] = {'file_bytes': len(data), 'debug_bytes': debug, 'allocated_sections': allocated}
print(json.dumps({'libraries': rows, 'total_file_bytes': sum(x['file_bytes'] for x in rows.values()),
                  'total_debug_bytes': sum(x['debug_bytes'] for x in rows.values())}, sort_keys=True))
'''

def inspect(image):
    return json.loads(subprocess.check_output(["docker", "image", "inspect", image]))[0]


def python(image, source):
    result = subprocess.run(
        ["docker", "run", "--rm", "-i", "--network", "none", "--read-only",
         "--tmpfs", "/tmp:rw,nosuid,nodev", "-e", "PYTHONDONTWRITEBYTECODE=1",
         "--entrypoint", "/app/.venv/bin/python", image, "-"],
        input=source, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip())
    return json.loads(result.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image")
    parser.add_argument("--baseline")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    identity = inspect(args.image)
    image_id = identity["Id"]
    runtime = python(image_id, NATIVE_PROBE)
    python(image_id, "import json, pathlib, shutil; "
           "assert shutil.which('uv') is None and shutil.which('uvx') is None, 'uv/uvx must be absent'; "
           "assert shutil.which('strip') is None, 'binutils must remain build-only'; "
           "assert pathlib.Path('/etc/ssl/certs/ca-certificates.crt').is_file(), 'CA bundle missing'; "
           "print(json.dumps({'runtime_tools': 'absent', 'ca_bundle': True}))")
    native = python(image_id, NATIVE_MANIFEST)
    assert native["libraries"], "No native libraries were checked"
    assert native["total_debug_bytes"] == 0, native["total_debug_bytes"]
    baseline_id = None
    if args.baseline:
        baseline = inspect(args.baseline)
        baseline_id = baseline["Id"]
        assert identity["Architecture"] == baseline["Architecture"], "Architecture mismatch"
        before = python(baseline_id, NATIVE_MANIFEST)
        assert native["libraries"].keys() == before["libraries"].keys(), "Native library removed"
        for path, row in native["libraries"].items():
            assert row["allocated_sections"] == before["libraries"][path]["allocated_sections"], path
    receipt = {"image": args.image, "image_id": identity["Id"],
               "architecture": identity["Architecture"], "rootfs": identity["RootFS"],
               "runtime": runtime, "native_library_count": len(native["libraries"]),
               "debug_bytes": native["total_debug_bytes"],
               "baseline_image_id": baseline_id,
               "allocated_sections_match_baseline": True if args.baseline else None,
               "scope": "native operations and ELF execution sections; no external database"}
    if args.output:
        args.output.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
