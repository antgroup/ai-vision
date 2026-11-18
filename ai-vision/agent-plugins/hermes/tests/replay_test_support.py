from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

HERMES_ROOT = Path(__file__).resolve().parents[1]
for root in (HERMES_ROOT / "hermes-replay", HERMES_ROOT / "hermes-core", HERMES_ROOT):
    value = str(root)
    if value not in sys.path:
        sys.path.insert(0, value)

from hermes_replay.capture import RawEnvelopeWriter  # noqa: E402


def write_raw_transcript(
    directory: Path,
    session_id: str,
    hooks: Iterable[tuple[str, Mapping[str, Any]]],
    *,
    collection_mode: str = "full",
) -> Path:
    writer = RawEnvelopeWriter(
        directory,
        session_id,
        collection_mode=collection_mode,
    )
    for hook, payload in hooks:
        writer.append(hook, payload)
    writer.close()
    return writer.path


def representative_hooks(session_id: str) -> list[tuple[str, dict[str, Any]]]:
    schema = {
        "telemetry_schema_version": "hermes.observer.v1",
        "session_id": session_id,
    }
    return [
        ("on_session_start", {**schema, "model": "model", "platform": "cli"}),
        ("pre_llm_call", {**schema, "turn_id": "turn-1", "user_message": "hello"}),
        (
            "pre_api_request",
            {
                **schema,
                "turn_id": "turn-1",
                "api_request_id": "api-1",
                "model": "model",
                "provider": "anthropic",
            },
        ),
        (
            "post_api_request",
            {
                **schema,
                "turn_id": "turn-1",
                "api_request_id": "api-1",
                "model": "model",
                "provider": "anthropic",
                "api_duration": 0.01,
                "usage": {"input_tokens": 2, "output_tokens": 1},
            },
        ),
        (
            "pre_tool_call",
            {
                **schema,
                "turn_id": "turn-1",
                "tool_name": "Read",
                "args": {"path": "README.md"},
            },
        ),
        (
            "post_tool_call",
            {
                **schema,
                "turn_id": "turn-1",
                "tool_name": "Read",
                "result": "ok",
                "duration_ms": 1,
            },
        ),
        ("on_session_end", {**schema, "turn_id": "turn-1", "completed": True}),
        ("on_session_finalize", {**schema, "reason": "shutdown"}),
    ]
