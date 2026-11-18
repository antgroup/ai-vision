"""Hermes v0.18.2 observer callback to canonical-event normalization."""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from datetime import datetime, timezone
from typing import Any, Mapping

from .canonical import (
    CANONICAL_SCHEMA_VERSION,
    GENESIS_CHECKSUM,
    CanonicalEvent,
    canonical_checksum,
)
from .privacy import ProjectionResult, hide_content, sanitize
from .sdk_objects import assistant_message, callback_objects

SUPPORTED_SOURCE_SCHEMA = "hermes.observer.v1"

HOOK_FIELDS: dict[str, tuple[str, ...]] = {
    "on_session_start": ("model", "platform"),
    "on_session_end": ("completed", "interrupted", "model", "platform"),
    "on_session_finalize": ("platform", "reason"),
    "on_session_reset": ("platform", "reason"),
    "pre_llm_call": (
        "conversation_history",
        "is_first_turn",
        "model",
        "platform",
        "user_message",
    ),
    "post_llm_call": (
        "assistant_response",
        "conversation_history",
        "model",
        "platform",
        "user_message",
    ),
    "pre_api_request": (
        "api_call_count",
        "api_mode",
        "approx_input_tokens",
        "base_url",
        "max_tokens",
        "message_count",
        "model",
        "platform",
        "provider",
        "request",
        "request_char_count",
        "request_messages",
        "started_at",
        "tool_count",
        "user_message",
    ),
    "post_api_request": (
        "api_call_count",
        "api_duration",
        "api_mode",
        "assistant_content_chars",
        "assistant_message",
        "assistant_tool_call_count",
        "base_url",
        "ended_at",
        "finish_reason",
        "message_count",
        "model",
        "platform",
        "provider",
        "response",
        "response_model",
        "started_at",
        "usage",
    ),
    "api_request_error": (
        "api_call_count",
        "api_duration",
        "api_mode",
        "base_url",
        "ended_at",
        "error",
        "max_retries",
        "model",
        "platform",
        "provider",
        "reason",
        "request",
        "retry_count",
        "retryable",
        "started_at",
        "status_code",
    ),
    "pre_tool_call": ("args", "tool_name"),
    "post_tool_call": (
        "args",
        "duration_ms",
        "error_message",
        "error_type",
        "result",
        "status",
        "tool_name",
    ),
    "subagent_start": ("child_goal", "child_role"),
    "subagent_stop": ("child_role", "child_status", "child_summary", "duration_ms"),
}
HOOK_FIELDS["pre_auxiliary_call"] = HOOK_FIELDS["pre_api_request"] + (
    "aux_task", "retry_count", "streaming", "system_prompt",
)
HOOK_FIELDS["post_auxiliary_call"] = HOOK_FIELDS["post_api_request"] + (
    "aux_task", "retry_count", "streaming", "error", "error_type",
)

CORRELATION_FIELDS = (
    "session_id",
    "turn_id",
    "task_id",
    "api_request_id",
    "api_call_count",
    "tool_call_id",
    "parent_session_id",
    "parent_turn_id",
    "parent_subagent_id",
    "child_session_id",
    "child_subagent_id",
    "sender_id",
    "retry_count",
)

CONTENT_FIELDS: dict[str, frozenset[str]] = {
    "pre_llm_call": frozenset({"conversation_history", "user_message"}),
    "post_llm_call": frozenset(
        {"assistant_response", "conversation_history", "user_message"}
    ),
    "pre_api_request": frozenset({"request", "request_messages", "user_message"}),
    "post_api_request": frozenset({"assistant_message", "response"}),
    "api_request_error": frozenset({"request"}),
    "pre_tool_call": frozenset({"args"}),
    "post_tool_call": frozenset({"args", "result"}),
    "subagent_start": frozenset({"child_goal"}),
    "subagent_stop": frozenset({"child_summary"}),
}
CONTENT_FIELDS["pre_auxiliary_call"] = CONTENT_FIELDS["pre_api_request"] | {"system_prompt"}
CONTENT_FIELDS["post_auxiliary_call"] = CONTENT_FIELDS["post_api_request"] | {"error"}

# Privacy is default-deny: only these structural fields retain their values.
# Every other callback field is treated as content-bearing even when a future
# Hermes version adds it to HOOK_FIELDS without updating a separate denylist.
STRUCTURAL_FIELDS: dict[str, frozenset[str]] = {
    "on_session_start": frozenset({"model", "platform"}),
    "on_session_end": frozenset({"completed", "interrupted", "model", "platform"}),
    "on_session_finalize": frozenset({"platform"}),
    "on_session_reset": frozenset({"platform"}),
    "pre_llm_call": frozenset({"is_first_turn", "model", "platform"}),
    "post_llm_call": frozenset({"model", "platform"}),
    "pre_api_request": frozenset(
        {
            "api_call_count",
            "api_mode",
            "approx_input_tokens",
            "max_tokens",
            "message_count",
            "model",
            "platform",
            "provider",
            "request_char_count",
            "started_at",
            "tool_count",
        }
    ),
    "post_api_request": frozenset(
        {
            "api_call_count",
            "api_duration",
            "api_mode",
            "assistant_content_chars",
            "assistant_tool_call_count",
            "ended_at",
            "finish_reason",
            "message_count",
            "model",
            "platform",
            "provider",
            "response_model",
            "started_at",
            "usage",
        }
    ),
    "api_request_error": frozenset(
        {
            "api_call_count",
            "api_duration",
            "api_mode",
            "ended_at",
            "max_retries",
            "model",
            "platform",
            "provider",
            "retry_count",
            "retryable",
            "started_at",
            "status_code",
        }
    ),
    "pre_tool_call": frozenset({"tool_name"}),
    "post_tool_call": frozenset({"duration_ms", "error_type", "status", "tool_name"}),
    "subagent_start": frozenset({"child_role"}),
    "subagent_stop": frozenset({"child_role", "child_status", "duration_ms"}),
}
STRUCTURAL_FIELDS["pre_auxiliary_call"] = STRUCTURAL_FIELDS["pre_api_request"] | {"aux_task", "retry_count", "streaming"}
STRUCTURAL_FIELDS["post_auxiliary_call"] = STRUCTURAL_FIELDS["post_api_request"] | {"aux_task", "retry_count", "streaming", "error_type"}


def classify_tool(tool_name: str) -> str:
    name = tool_name.casefold()
    if name == "skill" or name.startswith("skill:") or "skill-creator" in name:
        return "skill"
    if name.startswith("mcp__") or name.startswith("mcp:") or name.startswith("mcp_"):
        return "mcp"
    return "builtin"


def classify_error(error: Any, *, status_code: Any = None, reason: Any = None) -> str:
    text = " ".join(
        str(value) for value in (reason, status_code, error) if value is not None
    ).casefold()
    if "rate" in text and "limit" in text or str(status_code) == "429":
        return "rate_limit"
    if str(status_code) in {"401", "403"} or "auth" in text:
        return "auth"
    if "permission" in text or "denied" in text:
        return "permission"
    if "timeout" in text or "timed out" in text:
        return "timeout"
    if "cancel" in text or "interrupt" in text:
        return "cancelled"
    if "network" in text or "connection" in text or "dns" in text:
        return "network_error"
    if "validation" in text or str(status_code) in {"400", "422"}:
        return "validation"
    if error:
        return "api_error"
    return "other"


def _tool_result_failure(result: Any) -> tuple[bool, str]:
    """Read Hermes' structured tool result without inventing failure state."""
    decoded = result
    if isinstance(result, str):
        try:
            decoded = json.loads(result)
        except (json.JSONDecodeError, TypeError):
            return False, ""
    if not isinstance(decoded, Mapping):
        return False, ""
    exit_code = decoded.get("exit_code")
    failed = (
        isinstance(exit_code, int)
        and not isinstance(exit_code, bool)
        and exit_code != 0
    )
    error = decoded.get("error")
    failed = failed or error not in (None, "", False)
    if not failed:
        return False, ""
    message = (
        error
        or decoded.get("stderr")
        or decoded.get("output")
        or f"tool exited with code {exit_code}"
    )
    return True, str(message)


def _source_time(
    raw: Mapping[str, Any], supplied: str | float | int | None
) -> tuple[str, str]:
    value: str | float | int | None = supplied
    if value is None:
        value = raw.get("ended_at", raw.get("started_at"))
    if value is None:
        now = time.time()
        parsed = datetime.fromtimestamp(now, tz=timezone.utc)
        return parsed.isoformat().replace("+00:00", "Z"), str(time.time_ns())
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError("timezone is required")
            seconds = parsed.timestamp()
            return parsed.astimezone(timezone.utc).isoformat().replace(
                "+00:00", "Z"
            ), str(int(seconds * 1_000_000_000))
        except (ValueError, OverflowError, OSError) as exc:
            raise ValueError(
                "source timestamp must be timezone-aware ISO-8601"
            ) from exc
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(
            "source timestamp must be ISO-8601 or non-negative Unix seconds"
        )
    seconds = float(value)
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError("source timestamp must be finite non-negative Unix seconds")
    try:
        parsed = datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (ValueError, OverflowError, OSError) as exc:
        raise ValueError("source timestamp is outside the supported range") from exc
    return parsed.isoformat().replace("+00:00", "Z"), str(int(seconds * 1_000_000_000))


def _session_id(raw: Mapping[str, Any]) -> str:
    value = (
        raw.get("session_id")
        or raw.get("parent_session_id")
        or raw.get("child_session_id")
    )
    return str(value or "").strip()


class CanonicalNormalizer:
    """Stateful checksum/sequence normalizer for one canonical transcript."""

    def __init__(self, collection_mode: str = "full") -> None:
        if collection_mode not in {"full", "privacy"}:
            raise ValueError("collection_mode must be 'full' or 'privacy'")
        self.collection_mode = collection_mode
        self.sequence = 0
        self.previous_checksum = GENESIS_CHECKSUM
        self._pending_synthetic_tools: dict[tuple[str, str, str, str], list[str]] = {}
        self._synthetic_tool_ids: set[str] = set()

    def normalize(
        self,
        hook: str,
        raw: Mapping[str, Any],
        *,
        timestamp: str | float | int | None = None,
        provenance: Mapping[str, Any] | None = None,
    ) -> CanonicalEvent:
        if hook not in HOOK_FIELDS:
            raise ValueError(f"unsupported Hermes hook: {hook}")
        raw = callback_objects(raw)
        source_schema = str(
            raw.get("telemetry_schema_version") or SUPPORTED_SOURCE_SCHEMA
        )
        if source_schema != SUPPORTED_SOURCE_SCHEMA:
            raise ValueError(
                f"unsupported Hermes observer schema {source_schema!r}; "
                f"expected {SUPPORTED_SOURCE_SCHEMA!r}"
            )
        session_id = _session_id(raw)
        if not session_id:
            raise ValueError(f"{hook} requires a non-empty session identifier")

        payload: dict[str, Any] = {}
        field_status: dict[str, dict[str, bool]] = {}
        redacted = False
        truncated = False
        structural_fields = STRUCTURAL_FIELDS.get(hook, frozenset())
        # Hermes' normalized usage may contain zeros for buckets absent on the
        # wire. Prefer middleware's reported counters before privacy projection.
        if hook == "post_api_request" and "reported_usage" in raw:
            reported_usage = raw["reported_usage"]
            if isinstance(reported_usage, dict):
                reported_usage = dict(reported_usage)
                details = reported_usage.get("prompt_tokens_details")
                cached = details.get("cached_tokens") if isinstance(details, dict) else None
                if isinstance(cached, int) and not isinstance(cached, bool) and cached >= 0:
                    reported_usage.setdefault("cache_read_input_tokens", cached)
                cache_write = details.get("cache_write_tokens") if isinstance(details, dict) else None
                if isinstance(cache_write, int) and not isinstance(cache_write, bool) and cache_write >= 0:
                    reported_usage.setdefault("cache_creation_input_tokens", cache_write)
                completion_details = reported_usage.get("completion_tokens_details")
                reasoning = completion_details.get("reasoning_tokens") if isinstance(completion_details, dict) else None
                if isinstance(reasoning, int) and not isinstance(reasoning, bool) and reasoning >= 0:
                    reported_usage.setdefault("reasoning_tokens", reasoning)
            raw = {**raw, "usage": reported_usage}
        elif hook in {"post_api_request", "post_auxiliary_call"}:
            # Legacy/replay or auxiliary hooks expose normalized counters
            # without raw field presence. Positive is known; zero is ambiguous.
            usage = raw.get("usage")
            usage = {name: value for name, value in usage.items()
                     if isinstance(value, int) and not isinstance(value, bool) and value > 0} if isinstance(usage, dict) else None
            raw = {**raw, "usage": usage}
            if hook == "post_auxiliary_call":
                response = raw.get("response")
                assistant = response.get("assistant_message") if isinstance(response, dict) else None
                if isinstance(response, str) and response.startswith("[REDACTED:sha256:"):
                    assistant = response
                raw = {**raw, **({"assistant_message": assistant} if assistant is not None else {})}
        for field in HOOK_FIELDS[hook]:
            if field not in raw:
                continue
            projected: ProjectionResult
            if hook == "post_auxiliary_call" and field in {"error", "response"} and raw[field] is None:
                projected = sanitize(None)
            elif self.collection_mode == "privacy" and field not in structural_fields:
                projected = hide_content(raw[field])
            else:
                projected = sanitize(assistant_message(raw[field]) if field == "assistant_message" else raw[field])
            payload[field] = projected.value
            redacted = redacted or projected.redacted
            truncated = truncated or projected.truncated
            if projected.redacted or projected.truncated:
                field_status[field] = {
                    **({"redacted": True} if projected.redacted else {}),
                    **({"truncated": True} if projected.truncated else {}),
                }
        if hook == "post_auxiliary_call" and self.collection_mode == "privacy" and raw.get("response") is not None:
            # Raw capture hides the whole response before offline normalization.
            # Use that same marker live rather than hashing a nested message.
            payload["assistant_message"] = payload["response"]

        correlation = {
            field: sanitize(raw[field]).value
            for field in CORRELATION_FIELDS
            if field in raw and raw[field] not in (None, "")
        }
        if "tool_name" in raw:
            correlation["tool_type"] = classify_tool(str(raw.get("tool_name") or ""))
        if hook == "api_request_error" or hook == "post_auxiliary_call" and raw.get("error"):
            payload["error_category"] = classify_error(
                (raw.get("error_type") or raw.get("error")) if hook == "post_auxiliary_call" else raw.get("error"),
                status_code=raw.get("status_code"),
                reason=raw.get("reason"),
            )
        elif hook == "post_tool_call":
            failed, message = _tool_result_failure(raw.get("result"))
            explicit_error = raw.get("error_type") or raw.get("error_message")
            explicit_status = str(raw.get("status") or "").casefold()
            if (
                failed
                and not explicit_error
                and explicit_status not in {"error", "failed", "failure"}
            ):
                payload["status"] = "error"
                payload["error_type"] = "tool_error"
                payload["error_message"] = (
                    hide_content(message).value
                    if self.collection_mode == "privacy"
                    else sanitize(message).value
                )

        sequence = self.sequence + 1
        if hook in {"pre_tool_call", "post_tool_call"}:
            tool_name = str(raw.get("tool_name") or "")
            pending_key = (
                session_id,
                str(raw.get("turn_id") or ""),
                str(raw.get("task_id") or ""),
                tool_name,
            )
            native_call_id = str(raw.get("tool_call_id") or "").strip()
            if hook == "pre_tool_call" and not native_call_id:
                material = "\0".join((*pending_key, str(sequence)))
                digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]
                native_call_id = f"synthetic-{digest}"
                correlation["tool_call_id"] = native_call_id
                correlation["tool_call_id_synthetic"] = True
                self._pending_synthetic_tools.setdefault(pending_key, []).append(
                    native_call_id
                )
                self._synthetic_tool_ids.add(native_call_id)
            elif hook == "post_tool_call" and not native_call_id:
                candidates = list(self._pending_synthetic_tools.get(pending_key, []))
                if len(candidates) == 1:
                    native_call_id = candidates[0]
                    correlation["tool_call_id"] = native_call_id
                    correlation["tool_call_id_synthetic"] = True
                    self._pending_synthetic_tools.pop(pending_key, None)
                elif len(candidates) > 1:
                    correlation["tool_match_ambiguous"] = candidates
            elif native_call_id in self._synthetic_tool_ids:
                correlation["tool_call_id_synthetic"] = True

        event_timestamp, observed_at = _source_time(raw, timestamp)
        event_material = {
            "schema_version": CANONICAL_SCHEMA_VERSION,
            "source_schema_version": source_schema,
            "timestamp": event_timestamp,
            "observed_at_unix_nano": observed_at,
            "session_id": session_id,
            "event_id": "",
            "sequence": sequence,
            "previous_checksum": self.previous_checksum,
            "hook": hook,
            "correlation": correlation,
            "payload": payload,
            "provenance": sanitize(
                {
                    key: value
                    for key, value in dict(provenance or {}).items()
                    if self.collection_mode != "privacy"
                    or key in {"runtime", "observer_schema"}
                }
            ).value,
            "projection": {
                "redacted": redacted,
                "truncated": truncated,
                "fields": sorted(payload),
                **({"field_status": field_status} if field_status else {}),
            },
        }
        event_seed = f"{session_id}\0{sequence}\0{hook}\0{observed_at}"
        event_material["event_id"] = hashlib.sha256(
            event_seed.encode("utf-8")
        ).hexdigest()[:32]
        checksum = canonical_checksum(event_material)
        event = CanonicalEvent(**event_material, checksum=checksum)
        self.sequence = sequence
        self.previous_checksum = checksum
        return event


def safe_artifact_part(value: str, limit: int = 80) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "_", value).strip("_")
    return (cleaned or "session")[:limit]
