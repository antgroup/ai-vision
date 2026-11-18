from __future__ import annotations

from collections.abc import Mapping, Sequence

from .normalizers import (
    build_metadata,
    coerce_datetime,
    coerce_float,
    coerce_int,
    coerce_string,
    unix_nano_to_iso8601,
)

SEMANTIC_FIELD_CANDIDATES: dict[str, tuple[str, ...]] = {
    # Observation subject: prefer the current OTel field; keep the physical
    # ant.username column as a storage-compatibility fallback.
    "observation_subject_id": (
        "attributes.gen_ai.user.id",
        "attributes.ant.username",
    ),
    "session_id": ("attributes.gen_ai.conversation.id",),
    "input_messages": (
        "attributes.langfuse.observation.input",
        "attributes.gen_ai.input.messages",
    ),
    "output_messages": (
        "attributes.langfuse.observation.output",
        "attributes.gen_ai.output.messages",
    ),
    "model": (
        "attributes.gen_ai.response.model",
        "attributes.gen_ai.request.model",
    ),
    "environment": (),
    "release": (),
    "version": (),
    "tags": (),
}


def get_semantic_field_candidates(field_name: str) -> tuple[str, ...]:
    return SEMANTIC_FIELD_CANDIDATES.get(field_name, ())


def _first_non_empty(row: Mapping[str, object], candidates: tuple[str, ...]) -> object | None:
    for key in candidates:
        value = row.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def resolve_semantic_value(row: Mapping[str, object], field_name: str) -> object | None:
    direct_value = row.get(field_name)
    if direct_value not in (None, "", [], {}):
        return direct_value
    return _first_non_empty(row, get_semantic_field_candidates(field_name))


def _timestamp_sort_key(value: object) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    dt = coerce_datetime(value)
    if dt is None:
        return float("inf")
    return dt.timestamp()


def _trace_start_value(row: Mapping[str, object]) -> object | None:
    start_time = row.get("start_time_unix_nano")
    if start_time not in (None, "", [], {}):
        return start_time
    return row.get("period")


def _coerce_upper_string(value: object) -> str | None:
    normalized = coerce_string(value)
    if normalized is None:
        return None
    stripped = normalized.strip()
    return stripped.upper() if stripped else None


def build_observation_type(row: Mapping[str, object]) -> str:
    langfuse_type = _coerce_upper_string(row.get("attributes.langfuse.observation.type"))
    if langfuse_type:
        return langfuse_type

    span_kind = coerce_string(row.get("attributes.gen_ai.span.kind"))
    if span_kind:
        normalized_kind = span_kind.upper()
        span_kind_mapping = {
            "CHAT": "SPAN",
            "AGENT": "AGENT",
            "LLM": "GENERATION",
            "TOOL": "TOOL",
            "MCP": "TOOL",
            "RETRIEVER": "RETRIEVER",
            "RERANKER": "CHAIN",
            "EMBEDDING": "EMBEDDING",
            "GUARDRAIL": "GUARDRAIL",
        }
        mapped = span_kind_mapping.get(normalized_kind)
        if mapped:
            return mapped

    operation_name = coerce_string(row.get("attributes.gen_ai.operation.name"))
    if operation_name:
        normalized_operation = operation_name.lower()
        if normalized_operation in {"invoke_agent", "create_agent"}:
            return "AGENT"
        if normalized_operation == "execute_tool":
            return "TOOL"
        if normalized_operation == "retrieval":
            return "RETRIEVER"
        if normalized_operation == "embeddings":
            return "EMBEDDING"
        return "GENERATION"

    return "SPAN"


def build_observation_level(row: Mapping[str, object]) -> str:
    langfuse_level = _coerce_upper_string(row.get("attributes.langfuse.observation.level"))
    if langfuse_level:
        return langfuse_level

    status_code = row.get("status.code", row.get("status_code"))
    return "ERROR" if coerce_int(status_code, default=0) == 2 else "DEFAULT"


def build_model_parameters(row: Mapping[str, object]) -> dict[str, object] | None:
    parameters: dict[str, object] = {}
    for key, value in row.items():
        if not isinstance(key, str):
            continue
        if not key.startswith("attributes.gen_ai.request."):
            continue
        parameter_name = key[len("attributes.gen_ai.request.") :]
        parameters[parameter_name] = value
    return parameters or None


def build_usage_details(row: Mapping[str, object]) -> dict[str, float] | None:
    usage_details: dict[str, float] = {}
    input_tokens = coerce_float(row.get("attributes.gen_ai.usage.input_tokens"))
    output_tokens = coerce_float(row.get("attributes.gen_ai.usage.output_tokens"))
    total_tokens = coerce_float(row.get("attributes.gen_ai.usage.total_tokens"))
    cache_read_input_tokens = coerce_float(
        row.get("attributes.gen_ai.usage.details.cache_read_input_tokens")
    )
    cache_creation_input_tokens = coerce_float(
        row.get("attributes.gen_ai.usage.details.cache_creation_input_tokens")
    )

    if input_tokens is not None:
        usage_details["input"] = input_tokens
    if output_tokens is not None:
        usage_details["output"] = output_tokens
    if total_tokens is not None:
        usage_details["total"] = total_tokens
    elif input_tokens is not None or output_tokens is not None:
        usage_details["total"] = (input_tokens or 0.0) + (output_tokens or 0.0)
    if cache_read_input_tokens is not None:
        usage_details["cache_read.input"] = cache_read_input_tokens
    if cache_creation_input_tokens is not None:
        usage_details["cache_creation.input"] = cache_creation_input_tokens

    return usage_details or None


def build_cost_details(row: Mapping[str, object]) -> dict[str, float] | None:
    cost_details: dict[str, float] = {}
    total_cost = coerce_float(row.get("attributes.gen_ai.usage.cost"))
    if total_cost is not None:
        cost_details["total"] = total_cost

    for key, value in row.items():
        if not isinstance(key, str):
            continue
        if not key.startswith("attributes.gen_ai.usage.cost."):
            continue
        cost_name = key[len("attributes.gen_ai.usage.cost.") :]
        coerced = coerce_float(value)
        if coerced is not None:
            cost_details[cost_name] = coerced

    return cost_details or None


def _coerce_non_empty_string(value: object) -> str | None:
    normalized = coerce_string(value)
    if normalized is None:
        return None
    stripped = normalized.strip()
    return stripped or None


def build_span_linkage(row: Mapping[str, object]) -> tuple[str | None, str | None]:
    meta_span_id = _coerce_non_empty_string(row.get("attributes.__meta__.spanId"))
    span_link_role = _coerce_non_empty_string(row.get("attributes._internal.span_link.role"))
    span_link_role = span_link_role.lower() if span_link_role else None
    sofa_span_id = _coerce_non_empty_string(row.get("attributes._internal.sofa.span_id"))

    if not meta_span_id and span_link_role == "parent":
        meta_span_id = sofa_span_id

    span_link_parent_span_id = None
    if not meta_span_id and span_link_role == "child":
        span_link_parent_span_id = sofa_span_id

    return meta_span_id, span_link_parent_span_id


def map_span_row_to_observation(row: Mapping[str, object]) -> dict[str, object]:
    start_time = _trace_start_value(row)
    start_iso = unix_nano_to_iso8601(start_time)
    end_iso = unix_nano_to_iso8601(row.get("end_time_unix_nano"))
    latency = None
    start_dt = coerce_datetime(start_time)
    end_dt = coerce_datetime(row.get("end_time_unix_nano"))
    if start_dt is not None and end_dt is not None and end_dt >= start_dt:
        latency = (end_dt - start_dt).total_seconds()

    usage_details = build_usage_details(row)
    input_tokens = coerce_int(
        usage_details.get("input") if usage_details else None,
        default=0,
    )
    output_tokens = coerce_int(
        usage_details.get("output") if usage_details else None,
        default=0,
    )
    total_tokens = coerce_int(
        usage_details.get("total") if usage_details else None,
        default=input_tokens + output_tokens,
    )
    cost_details = build_cost_details(row)
    total_cost = None
    if cost_details is not None:
        total_cost = coerce_float(cost_details.get("total"))

    model = resolve_semantic_value(row, "model")
    status_message = row.get("attributes.langfuse.observation.status_message") or row.get(
        "status.message", row.get("status_message")
    )
    meta_span_id, span_link_parent_span_id = build_span_linkage(row)
    return {
        "id": coerce_string(row.get("span_id")) or "",
        "traceId": coerce_string(row.get("trace_id")),
        "parentObservationId": coerce_string(row.get("parent_span_id")),
        "metaSpanId": meta_span_id,
        "spanLinkParentSpanId": span_link_parent_span_id,
        "name": coerce_string(row.get("name")),
        "type": build_observation_type(row),
        "environment": None,
        "startTime": start_iso,
        "endTime": end_iso,
        "input": resolve_semantic_value(row, "input_messages"),
        "output": resolve_semantic_value(row, "output_messages"),
        "metadata": build_metadata(row),
        "level": build_observation_level(row),
        "statusMessage": coerce_string(status_message),
        "model": coerce_string(model),
        "modelParameters": build_model_parameters(row),
        "usageDetails": usage_details,
        "costDetails": cost_details,
        "usage": {
            "input": input_tokens,
            "output": output_tokens,
            "total": total_tokens,
        }
        if usage_details
        else None,
        "promptTokens": input_tokens,
        "completionTokens": output_tokens,
        "totalTokens": total_tokens,
        "totalCost": total_cost,
        "latency": latency,
        "timeToFirstToken": coerce_float(row.get("attributes.gen_ai.response.time_to_first_token")),
    }


def select_root_span_row(rows: Sequence[Mapping[str, object]]) -> Mapping[str, object] | None:
    if not rows:
        return None

    root_candidates = [
        row for row in rows if row.get("parent_span_id") in (None, "", "null", "None")
    ]
    if root_candidates:
        target_rows = root_candidates
    else:
        langfuse_root_candidates = [
            row
            for row in rows
            if str(row.get("attributes.langfuse.internal.as_root", "")).strip().lower()
            in {"1", "true", "yes", "on"}
        ]
        target_rows = langfuse_root_candidates or list(rows)
    return min(
        target_rows,
        key=lambda row: _timestamp_sort_key(_trace_start_value(row)),
    )


def compute_trace_latency(
    row_or_rows: Mapping[str, object] | Sequence[Mapping[str, object]],
) -> float | None:
    if isinstance(row_or_rows, Mapping):
        start_dt = coerce_datetime(_trace_start_value(row_or_rows))
        end_dt = coerce_datetime(row_or_rows.get("end_time_unix_nano"))
        if start_dt is None or end_dt is None or end_dt < start_dt:
            return None
        return (end_dt - start_dt).total_seconds()

    starts = [
        coerce_datetime(_trace_start_value(row))
        for row in row_or_rows
        if coerce_datetime(_trace_start_value(row)) is not None
    ]
    ends = [
        coerce_datetime(row.get("end_time_unix_nano"))
        for row in row_or_rows
        if coerce_datetime(row.get("end_time_unix_nano")) is not None
    ]
    if not starts or not ends:
        return None
    start_dt = min(starts)
    end_dt = max(ends)
    if end_dt < start_dt:
        return None
    return (end_dt - start_dt).total_seconds()


def map_root_span_row_to_trace(
    row: Mapping[str, object],
    *,
    observation_ids: Sequence[str] | None = None,
    latency: float | None = None,
) -> dict[str, object]:
    usage_details = build_usage_details(row)
    input_tokens = coerce_int(
        usage_details.get("input") if usage_details else None,
        default=0,
    )
    output_tokens = coerce_int(
        usage_details.get("output") if usage_details else None,
        default=0,
    )
    total_tokens = coerce_int(
        usage_details.get("total") if usage_details else None,
        default=input_tokens + output_tokens,
    )
    cost_details = build_cost_details(row)
    total_cost = None
    if cost_details is not None:
        total_cost = coerce_float(cost_details.get("total"))

    observation_count = coerce_int(row.get("observation_count"), default=0)

    return {
        "id": coerce_string(row.get("trace_id")) or "",
        "name": coerce_string(row.get("name")),
        "timestamp": unix_nano_to_iso8601(_trace_start_value(row)) or "",
        "userId": coerce_string(resolve_semantic_value(row, "observation_subject_id")),
        "sessionId": coerce_string(resolve_semantic_value(row, "session_id")),
        "environment": None,
        "release": None,
        "version": None,
        "tags": [],
        "bookmarked": False,
        "input": resolve_semantic_value(row, "input_messages"),
        "output": resolve_semantic_value(row, "output_messages"),
        "metadata": build_metadata(row),
        "observations": list(observation_ids or []),
        "observationCount": (
            observation_count if observation_count > 0 else len(observation_ids or [])
        ),
        "scores": [],
        "latency": latency if latency is not None else compute_trace_latency(row),
        "totalCost": total_cost,
        "totalTokens": total_tokens,
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "usageDetails": usage_details,
        "htmlPath": "",
    }
