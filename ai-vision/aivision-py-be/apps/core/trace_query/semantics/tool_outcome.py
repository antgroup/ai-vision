"""Read explicit tool outcomes from native span status and result metadata."""

import json
import math
import re


_CODEX_EXIT_HEADER = re.compile(
    r"\AChunk ID: [A-Za-z0-9_-]+\r?\n"
    r"Wall time: [0-9]+(?:\.[0-9]+)?[ \t]+(?:seconds?|s)\r?\n"
    r"Process exited with code (-?[0-9]{1,20})(?:\r?\n|\Z)"
)
_CODEX_COMPACT_EXIT_HEADER = re.compile(
    r"\AExit code: (-?[0-9]{1,20})\r?\nOutput:(?:\r?\n|\Z)"
)


def _reject_json_constant(value: str):
    # Match JSON.parse: NaN/Infinity tokens are not valid JSON evidence.
    raise ValueError(f"Invalid JSON constant: {value}")


def tool_span_outcome(span: dict, *, codex_shell: bool = False) -> str:
    """Return the outcome asserted by the span, including a known shell header."""
    attributes = span.get("attributes") or {}
    status = span.get("status") if isinstance(span.get("status"), dict) else {}
    code = str(status.get("code", "")).upper()
    if (
        code in {"2", "ERROR", "STATUS_CODE_ERROR"}
        or attributes.get("langfuse.observation.level") == "ERROR"
    ):
        return "error"
    output = attributes.get("gen_ai.tool.call.result")
    if output is None:
        output = attributes.get("langfuse.observation.output")
    for _ in range(2):
        if not isinstance(output, str):
            break
        try:
            output = json.loads(output, parse_constant=_reject_json_constant)
        except ValueError:
            break
    if isinstance(output, dict):
        exit_code = output.get("exit_code")
        try:
            finite_exit = (
                not isinstance(exit_code, bool)
                and isinstance(exit_code, (int, float))
                and math.isfinite(exit_code)
            )
        except OverflowError:
            finite_exit = False
        error = output.get("error")
        if (
            (isinstance(error, str) and error)
            or isinstance(error, dict)
            or output.get("is_error") is True
            or output.get("isError") is True
            or output.get("status") in ("failed", "error")
            or (finite_exit and exit_code != 0)
        ):
            return "error"
        if (finite_exit and exit_code == 0) or output.get("status") in (
            "completed", "success"
        ):
            return "ok"
    elif codex_shell and isinstance(output, str):
        # Only inspect the transport prefix. Command stdout can contain either
        # header string without expressing this shell tool's exit code.
        match = _CODEX_EXIT_HEADER.match(output) or _CODEX_COMPACT_EXIT_HEADER.match(output)
        if match:
            return "error" if int(match[1]) else "ok"
    return "ok" if code in {"1", "OK", "STATUS_CODE_OK"} else "unknown"
