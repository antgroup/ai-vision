"""Versioned raw callback envelopes with private, chained persistence."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from aivision_hermes_core.normalize import (
    HOOK_FIELDS,
    STRUCTURAL_FIELDS,
    safe_artifact_part,
)
from aivision_hermes_core.privacy import canonical_json, hide_content, sanitize
from aivision_hermes_core.sdk_objects import callback_objects

RAW_SCHEMA_VERSION = "hermes.replay.raw.v1"
GENESIS_CHECKSUM = "0" * 64
RAW_ENVELOPE_KEYS = {
    "schemaVersion",
    "agent",
    "plugin",
    "hook",
    "capturedAt",
    "sessionId",
    "correlation",
    "rawInput",
    "rawContext",
    "rawOutput",
    "rawError",
    "redaction",
    "integrity",
}
REDACTION_KEYS = {"collectionMode", "credentialsRedacted", "contentRedacted"}
INTEGRITY_KEYS = {"sequence", "previousChecksum", "checksum"}
LOWERCASE_HEX = frozenset("0123456789abcdef")
PRIVACY_MARKER_PREFIX = "[REDACTED:sha256:"
# Captured from tests/fixtures/hooks/v0.18.2/observer_envelopes.json. Values are
# optional within each hook, but correlation fields from another hook are not.
RAW_HOOK_CORRELATION_FIELDS: dict[str, frozenset[str]] = {
    "on_session_start": frozenset({"session_id"}),
    "on_session_end": frozenset({"session_id", "task_id", "turn_id"}),
    "on_session_finalize": frozenset({"session_id"}),
    "on_session_reset": frozenset({"session_id"}),
    "pre_llm_call": frozenset({"sender_id", "session_id", "task_id", "turn_id"}),
    "post_llm_call": frozenset({"session_id", "task_id", "turn_id"}),
    "pre_api_request": frozenset(
        {
            "api_request_id",
            "session_id",
            "task_id",
            "turn_id",
        }
    ),
    "post_api_request": frozenset(
        {
            "api_request_id",
            "session_id",
            "task_id",
            "turn_id",
        }
    ),
    "api_request_error": frozenset(
        {
            "api_request_id",
            "session_id",
            "task_id",
            "turn_id",
        }
    ),
    "pre_tool_call": frozenset(
        {
            "api_request_id",
            "session_id",
            "task_id",
            "tool_call_id",
            "turn_id",
        }
    ),
    "post_tool_call": frozenset(
        {
            "api_request_id",
            "session_id",
            "task_id",
            "tool_call_id",
            "turn_id",
        }
    ),
    "subagent_start": frozenset(
        {
            "child_session_id",
            "child_subagent_id",
            "parent_session_id",
            "parent_subagent_id",
            "parent_turn_id",
        }
    ),
    "subagent_stop": frozenset(
        {
            "child_session_id",
            "parent_session_id",
            "parent_turn_id",
        }
    ),
}
RAW_HOOK_EXTRA_FIELDS: dict[str, frozenset[str]] = {
    "pre_api_request": frozenset({"conversation_history", "middleware_trace"}),
    "pre_tool_call": frozenset({"middleware_trace"}),
    "post_tool_call": frozenset({"middleware_trace"}),
}
for _aux_hook, _api_hook in (("pre_auxiliary_call", "pre_api_request"), ("post_auxiliary_call", "post_api_request")):
    RAW_HOOK_CORRELATION_FIELDS[_aux_hook] = RAW_HOOK_CORRELATION_FIELDS[_api_hook]
RAW_OUTPUT_FIELDS = {
    "post_llm_call": "assistant_response",
    "post_api_request": "response",
    "post_auxiliary_call": "response",
    "post_tool_call": "result",
}
RAW_ERROR_FIELDS = {
    "api_request_error": "error",
    "post_auxiliary_call": "error",
    "post_tool_call": "error_message",
}
RAW_STRING_FIELDS = frozenset(
    {
        "api_mode",
        "api_request_id",
        "aux_task",
        "assistant_response",
        "base_url",
        "child_goal",
        "child_role",
        "child_session_id",
        "child_status",
        "child_subagent_id",
        "child_summary",
        "error_message",
        "error_type",
        "finish_reason",
        "model",
        "parent_session_id",
        "parent_subagent_id",
        "parent_turn_id",
        "platform",
        "provider",
        "reason",
        "response_model",
        "sender_id",
        "session_id",
        "status",
        "system_prompt",
        "task_id",
        "telemetry_schema_version",
        "tool_call_id",
        "tool_name",
        "turn_id",
        "user_message",
    }
)
RAW_BOOLEAN_FIELDS = frozenset(
    {"completed", "interrupted", "is_first_turn", "retryable", "streaming"}
)
RAW_NUMBER_FIELDS = frozenset(
    {
        "api_call_count",
        "api_duration",
        "approx_input_tokens",
        "assistant_content_chars",
        "assistant_tool_call_count",
        "duration_ms",
        "ended_at",
        "max_retries",
        "max_tokens",
        "message_count",
        "request_char_count",
        "retry_count",
        "started_at",
        "status_code",
        "tool_count",
    }
)
RAW_ARRAY_FIELDS = frozenset({"conversation_history", "request_messages"})
RAW_OBJECT_FIELDS = frozenset(
    {"args", "assistant_message", "request", "response", "usage"}
)


class RawTranscriptError(ValueError):
    pass


def _timestamp() -> str:
    return datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _checksum(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _schema_error(path: Path, line_number: int, message: str) -> RawTranscriptError:
    return RawTranscriptError(f"{path}:{line_number}: {message}")


def _is_checksum(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in LOWERCASE_HEX for character in value)
    )


def _is_privacy_marker(value: Any) -> bool:
    if not isinstance(value, str) or not value.startswith(PRIVACY_MARKER_PREFIX):
        return False
    if not value.endswith("]"):
        return False
    digest = value[len(PRIVACY_MARKER_PREFIX) : -1]
    return len(digest) == 12 and all(character in LOWERCASE_HEX for character in digest)


def _allowed_raw_fields(hook: str) -> set[str]:
    return (
        set(HOOK_FIELDS[hook])
        | set(RAW_HOOK_EXTRA_FIELDS.get(hook, frozenset()))
        | set(RAW_HOOK_CORRELATION_FIELDS[hook])
        | {"telemetry_schema_version"}
    )


def _raw_session_id(raw_input: Mapping[str, Any]) -> str:
    return str(
        raw_input.get("session_id")
        or raw_input.get("parent_session_id")
        or raw_input.get("child_session_id")
        or ""
    ).strip()


def _same_json(left: Any, right: Any) -> bool:
    return canonical_json(left) == canonical_json(right)


def _raw_output(hook: str, raw_input: Mapping[str, Any]) -> Any:
    field = RAW_OUTPUT_FIELDS.get(hook)
    return raw_input.get(field) if field is not None else None


def _raw_error(hook: str, raw_input: Mapping[str, Any]) -> Any:
    field = RAW_ERROR_FIELDS.get(hook)
    return raw_input.get(field) if field is not None else None


def _validate_raw_field_types(
    hook: str,
    raw_input: Mapping[str, Any],
    path: Path,
    line_number: int,
    collection_mode: str,
) -> None:
    structural = STRUCTURAL_FIELDS.get(hook, frozenset())
    metadata = set(RAW_HOOK_CORRELATION_FIELDS[hook]) | {"telemetry_schema_version"}
    for field, value in raw_input.items():
        if value is None:
            continue
        if (
            collection_mode == "privacy"
            and field not in structural
            and field not in metadata
        ):
            if not _is_privacy_marker(value):
                raise _schema_error(
                    path,
                    line_number,
                    f"{hook}.rawInput.{field} must be a privacy redaction marker",
                )
            continue
        valid = True
        expected = ""
        if field in RAW_STRING_FIELDS:
            valid = isinstance(value, str)
            expected = "a string or null"
        elif field in RAW_BOOLEAN_FIELDS:
            valid = isinstance(value, bool)
            expected = "a boolean or null"
        elif field in RAW_NUMBER_FIELDS:
            valid = isinstance(value, (int, float)) and not isinstance(value, bool)
            expected = "a number or null"
        elif field in RAW_ARRAY_FIELDS:
            valid = isinstance(value, list)
            expected = "an array or null"
        elif field in RAW_OBJECT_FIELDS:
            valid = isinstance(value, dict)
            expected = "an object or null"
        elif field == "middleware_trace":
            valid = isinstance(value, (dict, list))
            expected = "an object, array, or null"
        elif field == "error":
            valid = isinstance(value, (dict, str))
            expected = "an object, string, or null"
        if not valid:
            raise _schema_error(
                path, line_number, f"{hook}.rawInput.{field} must be {expected}"
            )


def _validate_raw_envelope(
    envelope: dict[str, Any],
    path: Path,
    line_number: int,
    *,
    expected_session: str | None,
    expected_mode: str | None,
) -> tuple[str, str]:
    unknown = sorted(set(envelope) - RAW_ENVELOPE_KEYS)
    missing = sorted(RAW_ENVELOPE_KEYS - set(envelope))
    if unknown:
        raise _schema_error(
            path, line_number, f"unknown raw envelope field: {unknown[0]}"
        )
    if missing:
        raise _schema_error(
            path, line_number, f"missing raw envelope field: {missing[0]}"
        )
    if envelope.get("schemaVersion") != RAW_SCHEMA_VERSION:
        raise _schema_error(path, line_number, "invalid raw envelope schema")
    if envelope.get("agent") != "hermes" or envelope.get("plugin") != "hermes-replay":
        raise _schema_error(path, line_number, "invalid raw envelope identity")
    hook = envelope.get("hook")
    if not isinstance(hook, str) or hook not in HOOK_FIELDS:
        raise _schema_error(path, line_number, "unsupported raw envelope hook")
    captured_at = envelope.get("capturedAt")
    if not isinstance(captured_at, str) or not captured_at:
        raise _schema_error(path, line_number, "capturedAt must be a timestamp")
    try:
        parsed_timestamp = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise _schema_error(
            path,
            line_number,
            "capturedAt must be an ISO-8601 timestamp",
        ) from error
    if parsed_timestamp.tzinfo is None:
        raise _schema_error(path, line_number, "capturedAt must include a timezone")
    session_id = envelope.get("sessionId")
    if not isinstance(session_id, str) or not session_id.strip():
        raise _schema_error(path, line_number, "sessionId must be a non-empty string")
    if expected_session is not None and session_id != expected_session:
        raise _schema_error(path, line_number, "raw transcript changes sessionId")
    if not isinstance(envelope.get("correlation"), dict):
        raise _schema_error(path, line_number, "correlation must be an object")
    raw_input = envelope.get("rawInput")
    if not isinstance(raw_input, dict):
        raise _schema_error(path, line_number, "rawInput must be an object")
    if raw_input.get("telemetry_schema_version") != "hermes.observer.v1":
        raise _schema_error(path, line_number, "invalid Hermes observer schema")
    redaction = envelope.get("redaction")
    if not isinstance(redaction, dict) or set(redaction) != REDACTION_KEYS:
        raise _schema_error(path, line_number, "invalid redaction metadata")
    mode = redaction.get("collectionMode")
    if mode not in {"full", "privacy"}:
        raise _schema_error(path, line_number, "invalid redaction collectionMode")
    if expected_mode is not None and mode != expected_mode:
        raise _schema_error(path, line_number, "raw transcript changes collectionMode")
    if not isinstance(redaction.get("credentialsRedacted"), bool):
        raise _schema_error(path, line_number, "credentialsRedacted must be a boolean")
    if not isinstance(redaction.get("contentRedacted"), bool):
        raise _schema_error(path, line_number, "contentRedacted must be a boolean")
    if redaction["contentRedacted"] is not (mode == "privacy"):
        raise _schema_error(
            path, line_number, "contentRedacted conflicts with collectionMode"
        )
    unknown_raw = sorted(set(raw_input) - _allowed_raw_fields(hook))
    if unknown_raw:
        raise _schema_error(
            path, line_number, f"unknown {hook} rawInput field: {unknown_raw[0]}"
        )
    _validate_raw_field_types(hook, raw_input, path, line_number, mode)
    if _raw_session_id(raw_input) != session_id:
        raise _schema_error(
            path, line_number, "sessionId conflicts with rawInput session identity"
        )
    correlation = envelope.get("correlation")
    expected_correlation = {
        key: raw_input[key]
        for key in RAW_HOOK_CORRELATION_FIELDS[hook]
        if key in raw_input
    }
    if not _same_json(correlation, expected_correlation):
        raise _schema_error(path, line_number, "correlation conflicts with rawInput")
    raw_context = envelope.get("rawContext")
    if not isinstance(raw_context, dict):
        raise _schema_error(path, line_number, "rawContext must be an object")
    if not _same_json(raw_context, raw_input.get("context", {})):
        raise _schema_error(path, line_number, "rawContext conflicts with rawInput")
    if not _same_json(envelope.get("rawOutput"), _raw_output(hook, raw_input)):
        raise _schema_error(path, line_number, "rawOutput conflicts with rawInput")
    if not _same_json(envelope.get("rawError"), _raw_error(hook, raw_input)):
        raise _schema_error(path, line_number, "rawError conflicts with rawInput")
    integrity = envelope.get("integrity")
    if not isinstance(integrity, dict) or set(integrity) != INTEGRITY_KEYS:
        raise _schema_error(path, line_number, "invalid integrity metadata")
    sequence = integrity.get("sequence")
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence <= 0:
        raise _schema_error(
            path, line_number, "integrity.sequence must be a positive integer"
        )
    if not _is_checksum(integrity.get("previousChecksum")):
        raise _schema_error(
            path,
            line_number,
            "integrity.previousChecksum must be 64 lowercase hexadecimal characters",
        )
    if not _is_checksum(integrity.get("checksum")):
        raise _schema_error(
            path,
            line_number,
            "integrity.checksum must be 64 lowercase hexadecimal characters",
        )
    return session_id, str(mode)


def project_raw(
    hook: str,
    raw: Mapping[str, Any],
    collection_mode: str,
) -> tuple[dict[str, Any], bool]:
    if hook not in HOOK_FIELDS:
        raise RawTranscriptError(f"unsupported Hermes hook: {hook}")
    projected: dict[str, Any] = {}
    redacted = False
    structural = STRUCTURAL_FIELDS.get(hook, frozenset())
    allowed = _allowed_raw_fields(hook)
    metadata = set(RAW_HOOK_CORRELATION_FIELDS[hook]) | {"telemetry_schema_version"}
    for key, value in callback_objects(raw).items():
        if key not in allowed:
            continue
        if hook == "post_auxiliary_call" and key in {"error", "response"} and value is None:
            result = sanitize(None)
        elif (
            collection_mode == "privacy"
            and key not in metadata
            and key not in structural
        ):
            result = hide_content(value)
        else:
            result = sanitize(value)
        projected[key] = result.value
        redacted = redacted or result.redacted
    return projected, redacted


class RawEnvelopeWriter:
    def __init__(
        self,
        directory: Path,
        session_id: str,
        *,
        collection_mode: str,
        session_name: str = "",
    ) -> None:
        existed = directory.exists()
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name == "posix" and not existed:
            os.chmod(directory, 0o700)
        context = safe_artifact_part(session_name or session_id)
        nonce = f"{os.getpid()}:{time.time_ns()}:{id(self)}"
        digest = hashlib.sha256(f"{session_id}:{nonce}".encode("utf-8")).hexdigest()[
            :12
        ]
        self.path = directory / f"session_{context}_{digest}.jsonl"
        self.session_id = session_id
        self.collection_mode = collection_mode
        self.sequence = 0
        self.previous_checksum = GENESIS_CHECKSUM
        self._lock = threading.Lock()
        self._handle: Any = None

    def _open(self) -> None:
        if self._handle is not None:
            return
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        if os.name == "posix":
            os.chmod(self.path, 0o600)
        self._handle = os.fdopen(fd, "a", encoding="utf-8")

    def append(self, hook: str, raw: Mapping[str, Any]) -> None:
        projected, redacted = project_raw(hook, raw, self.collection_mode)
        with self._lock:
            sequence = self.sequence + 1
            captured_at = _timestamp()
            envelope: dict[str, Any] = {
                "schemaVersion": RAW_SCHEMA_VERSION,
                "agent": "hermes",
                "plugin": "hermes-replay",
                "hook": hook,
                "capturedAt": captured_at,
                "sessionId": self.session_id,
                "correlation": {
                    key: projected[key]
                    for key in RAW_HOOK_CORRELATION_FIELDS[hook]
                    if key in projected
                },
                "rawInput": projected,
                "rawContext": projected.get("context", {}),
                "rawOutput": _raw_output(hook, projected),
                "rawError": _raw_error(hook, projected),
                "redaction": {
                    "collectionMode": self.collection_mode,
                    "credentialsRedacted": redacted,
                    "contentRedacted": self.collection_mode == "privacy",
                },
                "integrity": {
                    "sequence": sequence,
                    "previousChecksum": self.previous_checksum,
                },
            }
            checksum = _checksum(envelope)
            envelope["integrity"]["checksum"] = checksum
            self._open()
            self._handle.write(
                json.dumps(envelope, ensure_ascii=False, sort_keys=True) + "\n"
            )
            self._handle.flush()
            self.sequence = sequence
            self.previous_checksum = checksum

    def close(self) -> None:
        with self._lock:
            if self._handle is not None:
                self._handle.close()
                self._handle = None


def read_raw_transcript(path: Path) -> list[dict[str, Any]]:
    content = path.read_bytes()
    if content and not content.endswith(b"\n"):
        raise RawTranscriptError(f"{path}: partial final transcript line")
    envelopes: list[dict[str, Any]] = []
    previous = GENESIS_CHECKSUM
    expected_session: str | None = None
    expected_mode: str | None = None
    try:
        lines = content.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise RawTranscriptError(f"{path}: transcript is not UTF-8") from error
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            envelope = json.loads(line)
        except json.JSONDecodeError as error:
            raise RawTranscriptError(f"{path}:{line_number}: invalid JSON") from error
        if not isinstance(envelope, dict):
            raise RawTranscriptError(
                f"{path}:{line_number}: invalid raw envelope schema"
            )
        session_id, mode = _validate_raw_envelope(
            envelope,
            path,
            line_number,
            expected_session=expected_session,
            expected_mode=expected_mode,
        )
        expected_session = expected_session or session_id
        expected_mode = expected_mode or mode
        integrity = envelope.get("integrity")
        assert isinstance(integrity, dict)
        if integrity.get("sequence") != len(envelopes) + 1:
            raise RawTranscriptError(f"{path}:{line_number}: invalid sequence")
        if integrity.get("previousChecksum") != previous:
            raise RawTranscriptError(f"{path}:{line_number}: broken checksum chain")
        actual = integrity["checksum"]
        material = dict(envelope)
        material["integrity"] = dict(integrity)
        material["integrity"].pop("checksum", None)
        expected = _checksum(material)
        if actual != expected:
            raise RawTranscriptError(f"{path}:{line_number}: checksum mismatch")
        previous = actual
        envelopes.append(envelope)
    if not envelopes:
        raise RawTranscriptError(f"{path}: transcript is empty")
    return envelopes
