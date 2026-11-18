"""Pure, standard-library contracts shared by admission and the isolated worker.

Validation compiles source but never imports or executes user code. Full Python's
security boundary is the disposable sandbox, not this entry-point check.
"""

from __future__ import annotations

import ast
import base64
import binascii
import hashlib
import json
import math
import re
from pathlib import PurePosixPath
from typing import Any

RUNTIME_ID = "python-eval-v1"
CAPABILITY = "python_eval_v1"
MAX_CODE_BYTES = 64 * 1024
MAX_MATERIAL_FILES = 20
MAX_MATERIAL_FILE_BYTES = 256 * 1024
MAX_MATERIAL_TOTAL_BYTES = 512 * 1024
MAX_EXPECTED_JSON_DEPTH = 64
REQUIRED_PACKAGES = frozenset({"pytest", "numpy", "pandas", "pydantic", "jsonschema", "openpyxl"})
_SHA256 = re.compile(r"^[a-f0-9]{64}$")


class EnhancedValidationError(ValueError):
    def __init__(self, message: str, failure_code: str = "rule_validation_error"):
        super().__init__(message)
        self.failure_code = failure_code


def validate_enhanced_code(code: str) -> Any:
    if not isinstance(code, str) or not code.strip():
        raise EnhancedValidationError("code must be non-empty Python source")
    if len(code.encode("utf-8")) > MAX_CODE_BYTES:
        raise EnhancedValidationError(f"code exceeds {MAX_CODE_BYTES} bytes")
    try:
        tree = ast.parse(code, filename="<enhanced_rule>")
        entries = [
            n
            for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "evaluate"
        ]
        if len(entries) != 1 or not isinstance(entries[0], ast.FunctionDef):
            raise EnhancedValidationError(
                "define exactly one synchronous evaluate(trace[, context])"
            )
        entry = entries[0]
        args = entry.args
        names = [a.arg for a in [*args.posonlyargs, *args.args]]
        if (
            names not in (["trace"], ["trace", "context"])
            or args.defaults
            or args.kwonlyargs
            or args.vararg
            or args.kwarg
            or entry.decorator_list
        ):
            raise EnhancedValidationError(
                "evaluate must accept trace or trace, context without defaults or decorators"
            )
        return compile(tree, "<enhanced_rule>", "exec")
    except (SyntaxError, RecursionError, ValueError) as exc:
        if isinstance(exc, EnhancedValidationError):
            raise
        raise EnhancedValidationError(f"invalid enhanced source: {exc}") from exc


def material_bytes(item: dict) -> bytes:
    content = item.get("content")
    if not isinstance(content, str):
        raise EnhancedValidationError("material content must be a string")
    try:
        if item.get("encoding") == "utf-8":
            return content.encode("utf-8")
        if item.get("encoding") == "base64":
            if len(content) > 4 * ((MAX_MATERIAL_FILE_BYTES + 2) // 3):
                raise EnhancedValidationError("material exceeds decoded file limit")
            return base64.b64decode(content, validate=True)
    except (ValueError, UnicodeError, binascii.Error) as exc:
        raise EnhancedValidationError("invalid material encoding") from exc
    raise EnhancedValidationError("material encoding must be utf-8 or base64")


def validate_material_path(path: Any) -> str:
    if not isinstance(path, str) or not path or len(path.encode("utf-8")) > 240:
        raise EnhancedValidationError("material path must be a bounded relative path")
    parts = path.split("/")
    archive = (".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar", ".tgz", ".whl")
    if (
        any(p in ("", ".", "..") for p in parts)
        or "\\" in path
        or ":" in path
        or any(ord(c) < 32 for c in path)
        or PurePosixPath(path).is_absolute()
        or path.lower().endswith(archive)
    ):
        raise EnhancedValidationError("material path must not contain traversal, URLs or archives")
    return path


def _parse_expected_json(encoded: str) -> Any:
    try:
        parsed = json.loads(encoded)
    except (ValueError, TypeError, RecursionError) as exc:
        raise EnhancedValidationError("expected must be bounded finite JSON") from exc
    pending = [(parsed, 1)]
    while pending:
        current, depth = pending.pop()
        if isinstance(current, (dict, list)):
            if depth > MAX_EXPECTED_JSON_DEPTH:
                raise EnhancedValidationError(
                    f"expected JSON container depth exceeds {MAX_EXPECTED_JSON_DEPTH}"
                )
            children = current.values() if isinstance(current, dict) else current
            pending.extend((child, depth + 1) for child in children)
        elif isinstance(current, float) and not math.isfinite(current):
            raise EnhancedValidationError("expected must be bounded finite JSON")
    return parsed


def normalize_materials(value: Any) -> dict:
    if value is None:
        return {"files": []}
    if not isinstance(value, dict) or set(value) - {"files", "expected", "expected_json"}:
        raise EnhancedValidationError("materials must contain files and optional expected JSON")
    if "expected" in value and "expected_json" in value:
        raise EnhancedValidationError("materials cannot contain both expected and expected_json")
    files = value.get("files", [])
    if not isinstance(files, list) or len(files) > MAX_MATERIAL_FILES:
        raise EnhancedValidationError(f"materials exceed {MAX_MATERIAL_FILES} files")
    result, paths, total = [], set(), 0
    for item in files:
        if not isinstance(item, dict) or set(item) - {"path", "encoding", "content", "sha256"}:
            raise EnhancedValidationError("invalid material fields")
        path = validate_material_path(item.get("path"))
        if path in paths or any(
            path.startswith(p + "/") or p.startswith(path + "/") for p in paths
        ):
            raise EnhancedValidationError("duplicate or overlapping material paths")
        paths.add(path)
        raw = material_bytes(item)
        total += len(raw)
        if len(raw) > MAX_MATERIAL_FILE_BYTES or total > MAX_MATERIAL_TOTAL_BYTES:
            raise EnhancedValidationError("materials exceed decoded size limit")
        digest = hashlib.sha256(raw).hexdigest()
        if "sha256" in item and item["sha256"] != digest:
            raise EnhancedValidationError("material sha256 does not match content")
        result.append(
            {
                "path": path,
                "encoding": item["encoding"],
                "content": item["content"],
                "sha256": digest,
            }
        )
    normalized = {"files": result}
    field = "expected_json" if "expected_json" in value else "expected"
    if field in value:
        try:
            if field == "expected_json":
                encoded = value[field]
                if not isinstance(encoded, str):
                    raise ValueError
            else:
                encoded = json.dumps(
                    value[field], allow_nan=False, ensure_ascii=False, separators=(",", ":")
                )
            if total + len(encoded.encode("utf-8")) > MAX_MATERIAL_TOTAL_BYTES:
                raise ValueError
        except (ValueError, TypeError, RecursionError) as exc:
            raise EnhancedValidationError("expected must be bounded finite JSON") from exc
        parsed = _parse_expected_json(encoded)
        # Raw JSON is the frozen wire identity and byte budget. Do not reserialize
        # it: JavaScript and Python format finite floating-point numbers differently.
        normalized[field] = encoded if field == "expected_json" else parsed
    return normalized


def validate_runtime_manifest(value: Any) -> dict:
    if not isinstance(value, dict) or set(value) != {
        "id",
        "manifest_sha256",
        "python_version",
        "packages",
    }:
        raise EnhancedValidationError("invalid runtime manifest fields")
    if (
        value["id"] != RUNTIME_ID
        or not isinstance(value["manifest_sha256"], str)
        or not _SHA256.fullmatch(value["manifest_sha256"])
    ):
        raise EnhancedValidationError("unsupported runtime manifest")
    if not isinstance(value["python_version"], str) or not re.fullmatch(
        r"3\.11\.\d+", value["python_version"]
    ):
        raise EnhancedValidationError("enhanced runtime requires CPython 3.11")
    packages = value["packages"]
    if (
        not isinstance(packages, dict)
        or not REQUIRED_PACKAGES <= packages.keys()
        or len(packages) > 128
        or any(
            not isinstance(k, str)
            or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", k)
            or not isinstance(v, str)
            or not re.fullmatch(r"[A-Za-z0-9_.+!-]{1,100}", v)
            for k, v in packages.items()
        )
    ):
        raise EnhancedValidationError("invalid runtime package inventory")
    canonical = {k: v for k, v in value.items() if k != "manifest_sha256"}
    digest = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    if digest != value["manifest_sha256"]:
        raise EnhancedValidationError("runtime manifest digest does not match inventory")
    return {**value, "packages": dict(packages)}
