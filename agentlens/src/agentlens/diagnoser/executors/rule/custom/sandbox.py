"""One-shot sandbox execution for Custom Rule functions."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any

_WORKER_SCRIPT = str(Path(__file__).parent / "worker_main.py")
_NATIVE_WORKER = Path(__file__).parents[5] / "agentlens-rule-worker"


def worker_command() -> list[str]:
    """Use the isolated native worker when packaged, or the source worker in development."""
    if _NATIVE_WORKER.is_file():
        return [str(_NATIVE_WORKER)]
    return [sys.executable, "-I", "-S", _WORKER_SCRIPT]


class SandboxError(RuntimeError):
    def __init__(self, message: str, failure_code: str = "rule_sandbox_error") -> None:
        super().__init__(message)
        self.failure_code = failure_code


def _encode_source_date(value: Any) -> str:
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def encode_worker_request(rule_code_hash: str, code: str, trace_dict: dict[str, Any]) -> str:
    """Encode source dates for transport without changing the in-process Trace."""
    return json.dumps(
        {"code_hash": rule_code_hash, "code": code, "trace": trace_dict},
        ensure_ascii=False,
        default=_encode_source_date,
    )


def execute_one_shot(
    compiled_code: Any,
    trace_dict: dict[str, Any],
    timeout_seconds: float = 5.0,
    *,
    code: str = "",
) -> dict[str, Any]:
    """Execute the user function in an isolated subprocess.

    Uses `python -I -S` for process-level isolation with a hard timeout.
    The subprocess is killed on timeout — no thread leak.
    `compiled_code` is ignored; `code` is sent to the worker.
    """
    if not code and compiled_code is not None:
        raise SandboxError(
            "one-shot requires code for subprocess execution",
            failure_code="rule_sandbox_error",
        )

    import hashlib

    rule_code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()[:32]

    req = encode_worker_request(rule_code_hash, code, trace_dict)

    try:
        result = subprocess.run(
            worker_command(),
            input=req + "\n",
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise SandboxError(
            f"执行超时 ({timeout_seconds}s)",
            failure_code="exec_timeout",
        ) from exc

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()[:200]
        raise SandboxError(
            f"子进程异常退出 (code={result.returncode}): {stderr}",
            failure_code="rule_sandbox_error",
        )

    stdout = (result.stdout or "").strip()
    if not stdout:
        raise SandboxError(
            "子进程无输出",
            failure_code="rule_sandbox_error",
        )

    try:
        last_line = stdout.strip().split("\n")[-1]
        resp = json.loads(last_line)
    except (json.JSONDecodeError, ValueError) as exc:
        raise SandboxError(
            f"子进程输出不可解析: {exc}",
            failure_code="rule_sandbox_error",
        ) from exc

    if resp.get("status") == "error":
        raise SandboxError(
            resp.get("reason", "unknown error"),
            failure_code=resp.get("failure_code", "rule_runtime_error"),
        )

    return resp.get("result", {})
