"""Lossless trace projections shared by native and compatibility consumers."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from .skill_recognition import trace_skill_tags
from .sdk_metadata import langfuse_metadata_is_json
from .usage import aggregate_usage, indexed_model_prices, normalize_usage, reported_usage, usage_coverage

_TIME = re.compile(
    r"^(\d{4}-\d\d-\d\d[T ]\d\d:\d\d:\d\d)(?:\.(\d{1,9}))?(Z|[+-]\d\d:\d\d)?$"
)
_OPERATIONS = {
    "chat": "GENERATION",
    "text_completion": "GENERATION",
    "embeddings": "EMBEDDING",
    "embedding": "EMBEDDING",
    "execute_tool": "TOOL",
    "invoke_agent": "AGENT",
    "retrieval": "RETRIEVER",
}
_MODEL_FIELDS = (
    "gen_ai.request.model",
    "gen_ai.response.model",
    "langfuse.observation.model.name",
    "llm.model_name",
)


def timestamp_ns(value):
    if value is None or value == "" or isinstance(value, bool):
        return None
    if isinstance(value, str):
        match = _TIME.match(value)
        if match:
            whole, fraction, offset = match.groups()
            parsed = datetime.fromisoformat(
                whole + (offset or "+00:00").replace("Z", "+00:00")
            )
            epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
            delta = parsed.astimezone(timezone.utc) - epoch
            return (delta.days * 86400 + delta.seconds) * 1000000000 + int(
                (fraction or "").ljust(9, "0")
            )
    try:
        number = Decimal(str(value))
        if not number.is_finite():
            return None
        return int(number)
    except (ValueError, TypeError, InvalidOperation):
        return None


def iso_timestamp(value):
    nano = timestamp_ns(value)
    if nano is None:
        return None
    seconds, fraction = divmod(nano, 1000000000)
    return (
        datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        + f".{fraction:09d}Z"
    )


def _json(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            pass
    return value


def _first(mapping, *keys, default=None):
    return next((mapping[key] for key in keys if mapping.get(key) is not None), default)


def _metadata(attrs, prefix):
    base = _json(attrs.get(prefix))
    result = dict(base) if isinstance(base, dict) else {}
    # Newly ingested legacy SDK values retain their actual OTLP type. Rows
    # without provenance keep the established historical projection; their
    # original SDK version/type cannot be recovered from the stored string.
    decode = langfuse_metadata_is_json(attrs) is not False
    result.update(
        {
            key[len(prefix) + 1 :]: _json(value) if decode else value
            for key, value in attrs.items()
            if key.startswith(prefix + ".")
        }
    )
    return result


def io_attribute(attrs, field):
    """Name the actual IO source, sharing precedence with its value projection."""
    standard = f"gen_ai.{field}.messages"
    sources = _json(attrs.get("aivision.normalization.sources"))
    sources = sources if isinstance(sources, dict) else {}
    # Derived chat messages are a presentation projection; the original SDK
    # value still carries arbitrary structured payloads. Explicit OTel wins.
    if standard in attrs and standard not in sources:
        return standard
    return next(
        (key for key in (
            f"langfuse.observation.{field}",
            f"{field}.value",
            standard,
            (
                "gen_ai.tool.call.arguments"
                if field == "input"
                else "gen_ai.tool.call.result"
            ),
            f"aivision.request.{field}",
        ) if attrs.get(key) is not None),
        None,
    )


def _io(attrs, field):
    return _json(attrs.get(io_attribute(attrs, field)))


def project_observation(span, *, project_id, trace_id=None, prices=None):
    payload = span.get("payload") or {}
    if isinstance(payload, str):
        payload = _json(payload)
    payload = payload if isinstance(payload, dict) else {}
    merged = {**payload, **span}
    attrs = dict(payload.get("attributes") or {})
    attrs.update(span.get("attributes") or {})
    attrs.update(
        {
            key[len("attributes.") :]: value
            for key, value in span.items()
            if key.startswith("attributes.")
        }
    )
    resource = merged.get("resource") or {}
    resource = (
        dict(resource.get("attributes", resource)) if isinstance(resource, dict) else {}
    )
    resource.update(merged.get("resource_attributes") or {})
    resource.update(
        {
            key[len("resource.attributes.") :]: value
            for key, value in merged.items()
            if key.startswith("resource.attributes.")
        }
    )
    start = _first(merged, "start_time_unix_nano", "startTime", "startTimeUnixNano")
    end = _first(merged, "end_time_unix_nano", "endTime", "endTimeUnixNano")
    start_ns, end_ns = timestamp_ns(start), timestamp_ns(end)
    if start_ns is None:
        raise ValueError("Observation start time is missing or invalid")
    identifier = _first(merged, "span_id", "spanId", "id")
    if not identifier:
        raise ValueError("Observation id is missing")
    parent = _first(merged, "parent_span_id", "parentSpanId", "parentObservationId")
    parent = None if not parent or set(str(parent)) == {"0"} else str(parent)
    kind = _first(
        attrs,
        "langfuse.observation.type",
        "gen_ai.span.kind",
        "openinference.span.kind",
    )
    sources = _json(attrs.get("aivision.normalization.sources"))
    explicit_operation = (
        attrs.get("gen_ai.operation.name")
        if not isinstance(sources, dict) or "gen_ai.operation.name" not in sources
        else None
    )
    kind = _OPERATIONS.get(explicit_operation) or (
        str(kind).upper()
        if kind
        else _OPERATIONS.get(attrs.get("gen_ai.operation.name"), "SPAN")
    )
    if kind == "LLM":
        kind = "GENERATION"
    elif kind == "CHAT":
        kind = "AGENT"
    model = _first(attrs, *_MODEL_FIELDS)
    native_decision = attrs.get("agentic.decision.type") in (
        "model_routing", "tool_approval",
    )
    if native_decision and not (isinstance(model, str) and model.strip()):
        model = next((
            attrs[key] for key in (*_MODEL_FIELDS, "agentic.decision.model")
            if isinstance(attrs.get(key), str) and attrs[key].strip()
        ), None)
    provider = _first(attrs, "gen_ai.provider.name", "gen_ai.system")
    usage = normalize_usage(
        attrs,
        model=model,
        provider=provider,
        prices=prices,
        observed_at=iso_timestamp(start_ns),
    )
    # Explicit model-backed decisions are calls, not generic parent rollups.
    # Reuse GENERATION so every usage consumer retains its dedup/ancestry rules.
    if (
        kind in {"SPAN", "DECISION"}
        and native_decision
        and isinstance(model, str) and model.strip()
        and any(usage[field] is not None for field in ("input", "output", "total"))
    ):
        kind = "GENERATION"
    metadata = _metadata(attrs, "langfuse.observation.metadata")
    trace_metadata = _metadata(attrs, "langfuse.trace.metadata")
    level = str(attrs.get("langfuse.observation.level") or "DEFAULT").upper()
    if level not in {"DEBUG", "DEFAULT", "WARNING", "ERROR"}:
        level = "DEFAULT"
    status = merged.get("status") or {}
    if not isinstance(status, dict):
        status = {"code": status}
    status = dict(status)
    if "code" not in status and merged.get("status_code") is not None:
        status["code"] = merged["status_code"]
    if "message" not in status and merged.get("status_message") is not None:
        status["message"] = merged["status_message"]
    completion = _json(attrs.get("langfuse.observation.completion_start_time"))
    completion_ns = timestamp_ns(completion)
    result = {
        "id": str(identifier),
        "traceId": str(_first(merged, "trace_id", "traceId", default=trace_id) or ""),
        "projectId": str(project_id),
        "parentObservationId": parent,
        "type": kind,
        "name": merged.get("name"),
        "startTime": iso_timestamp(start_ns),
        "endTime": iso_timestamp(end_ns),
        "completionStartTime": iso_timestamp(completion_ns),
        "latency": (
            float(Decimal(end_ns - start_ns) / Decimal(1000000000))
            if end_ns is not None
            else None
        ),
        "timeToFirstToken": (
            float(Decimal(completion_ns - start_ns) / Decimal(1000000000))
            if completion_ns is not None
            else None
        ),
        "model": model,
        "provider": provider,
        "modelParameters": _json(attrs.get("langfuse.observation.model.parameters")),
        "input": _io(attrs, "input"),
        "output": _io(attrs, "output"),
        "metadata": metadata,
        "traceMetadata": trace_metadata,
        "version": attrs.get("langfuse.version"),
        "level": level,
        "statusMessage": attrs.get("langfuse.observation.status_message"),
        "otelStatus": status,
        "environment": _first(
            attrs,
            "langfuse.environment",
            "deployment.environment.name",
            "deployment.environment",
            default="default",
        ),
        "sessionId": _first(
            attrs,
            "gen_ai.conversation.id",
            "langfuse.session.id",
            "session.id",
            default=merged.get("session_id"),
        ),
        "userId": _first(attrs, "gen_ai.user.id", "langfuse.user.id", "user.id"),
        "tags": _json(attrs.get("langfuse.trace.tags")) or [],
        "release": attrs.get("langfuse.release"),
        "promptName": attrs.get("langfuse.observation.prompt.name"),
        "promptVersion": _json(attrs.get("langfuse.observation.prompt.version")),
        "usage": usage,
        "attributes": attrs,
        "resourceAttributes": resource,
        "events": merged.get("events") or [],
        "links": merged.get("links") or [],
    }
    if isinstance(result["tags"], tuple):
        result["tags"] = list(result["tags"])
    if not isinstance(result["tags"], list):
        result["tags"] = [str(result["tags"])]
    return result


def project_trace(detail, *, project_id, prices=None):
    prices = indexed_model_prices(prices)
    trace_id = str(_first(detail, "traceId", "trace_id", "id") or "")
    observations = [
        project_observation(
            item, project_id=project_id, trace_id=trace_id, prices=prices
        )
        for item in detail.get("spans", [])
    ]
    unique = {item["id"]: item for item in observations}
    observations = sorted(
        unique.values(), key=lambda item: (item["startTime"], item["id"])
    )
    if not observations:
        raise ValueError("Trace has no observations")
    if any(item["traceId"] != trace_id for item in observations):
        raise ValueError("Observation belongs to another trace")
    roots = [item for item in observations if not item["parentObservationId"]]
    explicit = [
        item
        for item in observations
        if item["attributes"].get("langfuse.internal.is_app_root")
        in (True, "true", "1")
        or item["attributes"].get("langfuse.internal.as_root") in (True, "true", "1")
    ]
    orphaned = [
        item
        for item in observations
        if item["parentObservationId"] and item["parentObservationId"] not in unique
    ]
    candidates = explicit or roots or orphaned or observations
    root = candidates[0]
    reason = (
        "explicit"
        if explicit
        else "root" if roots else "missing_parent" if orphaned else "cycle"
    )
    if len(candidates) > 1:
        reason = "multiple_" + reason
    # Collector trace tags only cover natively reported tool types; derived
    # skill usage (shell reads of SKILL.md, host invocation links) never
    # reaches langfuse.trace.tags, so merge it in here with the same rules
    # the labs scanner uses. Every observation already carrying the key and
    # the selected root (as the tags consumer's fallback) get the union;
    # update-latest selection and downstream JSON-text projections keep
    # working unchanged.
    skill_tags = trace_skill_tags(detail.get("spans") or [])
    if skill_tags:
        for observation in observations:
            attrs = observation["attributes"]
            existing = attrs.get("langfuse.trace.tags")
            if existing is None and observation["id"] != root["id"]:
                continue
            current = _json(existing)
            current = (
                []
                if current is None
                else list(current)
                if isinstance(current, (list, tuple))
                else [str(current)]
            )
            attrs["langfuse.trace.tags"] = list(
                dict.fromkeys([*current, *skill_tags])
            )
            # The observation-level tags field was snapshotted during
            # projection; keep it identical to the merged attributes value.
            observation["tags"] = attrs["langfuse.trace.tags"]
    start = min(item["startTime"] for item in observations)
    ends = [item["endTime"] for item in observations if item["endTime"] is not None]
    end = max(ends) if ends else None
    session_ids = list(
        dict.fromkeys(item["sessionId"] for item in observations if item["sessionId"])
    )
    # SDK trace updates live on the observation receiving the update, not
    # necessarily the selected root. Actual update timestamps are unavailable;
    # use completion (or start), start, then span ID as a stable ordering proxy.
    # Resolve each scalar independently and merge metadata keys in that order.
    trace_values = {}
    trace_metadata = {}
    has_trace_metadata = False
    for observation in sorted(observations, key=lambda item: (
        item["endTime"] or item["startTime"], item["startTime"], item["id"],
    )):
        update = observation["attributes"]
        for field, keys in {
            "name": ("langfuse.trace.name",),
            "input": ("langfuse.trace.input",),
            "output": ("langfuse.trace.output",),
            "userId": ("gen_ai.user.id", "langfuse.user.id", "user.id"),
            "tags": ("langfuse.trace.tags",),
        }.items():
            for key in keys:
                if key in update:
                    trace_values[field] = _json(update[key]) if field in {"input", "output", "tags"} else update[key]
                    break
        if any(key == "langfuse.trace.metadata" or key.startswith("langfuse.trace.metadata.") for key in update):
            has_trace_metadata = True
            trace_metadata.update(observation["traceMetadata"])
    tags = trace_values.get("tags", root["tags"])
    tags = [] if tags is None else list(tags) if isinstance(tags, (list, tuple)) else [str(tags)]
    return {
        "id": trace_id,
        "projectId": str(project_id),
        "timestamp": start,
        "name": trace_values.get("name", root["name"]),
        "input": trace_values.get("input", root["input"]),
        "output": trace_values.get("output", root["output"]),
        "sessionId": root["sessionId"]
        or (session_ids[0] if len(session_ids) == 1 else None),
        "userId": trace_values.get("userId", root["userId"]),
        "metadata": trace_metadata if has_trace_metadata else root["metadata"],
        "tags": tags,
        "release": root["release"],
        "version": root["version"],
        "environment": root["environment"],
        "public": False,
        "observations": observations,
        "scores": [],
        "latency": (
            float(
                Decimal(timestamp_ns(end) - timestamp_ns(start)) / Decimal(1000000000)
            )
            if end
            else None
        ),
        "usage": aggregate_usage(observations, prices=prices),
        "rootObservationId": root["id"],
        "rootSelectionReason": reason,
        "missingParentIds": sorted({item["parentObservationId"] for item in orphaned}),
        "sessionIds": session_ids,
    }


def project_native_trace(detail, *, project_id, prices=None):
    """Add native summaries without changing the public Langfuse v1 facade.

    Environment belongs to the selected root. Children resolve their own
    environment and never supply a trace fallback. Keep raw aliases intact so
    observation predicates and provenance remain inspectable.
    """
    trace = project_trace(detail, project_id=project_id, prices=prices)
    for observation in trace["observations"]:
        environment, source = "default", None
        for prefix, values, keys in (
            ("attributes.", observation["attributes"], (
                "langfuse.environment", "deployment.environment.name", "deployment.environment",
            )),
            ("resource.attributes.", observation["resourceAttributes"], (
                "ant.agent.env", "deployment.environment.name", "deployment.environment",
            )),
        ):
            for key in keys:
                value = values.get(key)
                if isinstance(value, str) and value.strip():
                    environment, source = value, prefix + key
                    break
            if source is not None:
                break
        observation.update(environment=environment, environmentSource=source)
    root = next(item for item in trace["observations"] if item["id"] == trace["rootObservationId"])
    trace.update(
        environment=root["environment"], environmentSource=root["environmentSource"],
        reportedUsage=reported_usage(trace["observations"]),
        coverage=usage_coverage(trace["observations"]),
        costCoverage=trace["usage"]["costCoverage"],
    )
    return trace
