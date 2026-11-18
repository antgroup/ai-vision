"""Zero-dependency trace context library for Claude Code tool scripts.

Reads CCHOOKS_TOOL_* environment variables injected by cchooks ToolContextHandler,
constructs OTel-compatible spans, and uploads them via HTTP. No opentelemetry SDK required.

Usage:
    from tool_context_pure import ToolTraceContext, record_tool_span

    ctx = ToolTraceContext.from_env()
    if ctx.is_active():
        record_tool_span(ctx, name="my-operation", input="...", output="...", status="ok")
"""

import json
import os
import random
import time
import urllib.request

__all__ = ["ToolTraceContext", "record_tool_span", "create_child_span", "send_spans"]


class ToolTraceContext:
    """Trace context parsed from cchooks-injected environment variables."""

    def __init__(self):
        self.traceparent = ""
        self.trace_id = ""
        self.parent_span_id = ""
        self.tool_use_id = ""
        self.sofa_traceid = ""
        self.sofa_rpcid = ""
        self.endpoint = ""
        self.auth = ""
        self.headers = {}
        self.span_attrs = {}
        self.resource_attrs = {}

    @classmethod
    def from_env(cls):
        """Build context from CCHOOKS_TOOL_* env vars. Safe to call even when not injected."""
        ctx = cls()
        ctx.traceparent = os.environ.get("CCHOOKS_TOOL_TRACEPARENT", "")
        ctx.tool_use_id = os.environ.get("CCHOOKS_TOOL_USE_ID", "")
        ctx.sofa_traceid = os.environ.get("CCHOOKS_TOOL_SOFA_TRACEID", "")
        ctx.sofa_rpcid = os.environ.get("CCHOOKS_TOOL_SOFA_RPCID", "")
        ctx.endpoint = os.environ.get("CCHOOKS_TOOL_OTEL_ENDPOINT", "")
        ctx.auth = os.environ.get("CCHOOKS_TOOL_OTEL_AUTH", "")
        ctx.headers = _json_env("CCHOOKS_TOOL_OTEL_HEADERS")
        ctx.span_attrs = _json_env("CCHOOKS_TOOL_SPAN_ATTRS")
        ctx.resource_attrs = _json_env("CCHOOKS_TOOL_RESOURCE_ATTRS")

        tp = _parse_traceparent(ctx.traceparent)
        if tp:
            ctx.trace_id = tp["trace_id"]
            ctx.parent_span_id = tp["parent_span_id"]
        return ctx

    def is_active(self):
        """Returns True if trace context was injected (CCHOOKS_TOOL_TRACEPARENT is set)."""
        return bool(self.trace_id)


def record_tool_span(
    ctx,
    name="tool-operation",
    input="",
    output="",
    status="ok",
    start_time_ns=None,
    end_time_ns=None,
    attributes=None,
    auto_send=True,
):
    """Record a tool operation as an OTel child span.

    Args:
        ctx: ToolTraceContext from ToolTraceContext.from_env()
        name: Span name (e.g. "data-processing", "api-call")
        input: Tool input / arguments (string or dict, will be JSON-serialized)
        output: Tool output / result (string or dict, will be JSON-serialized)
        status: "ok" or "error"
        start_time_ns: Start time in nanoseconds (default: now minus a small delta)
        end_time_ns: End time in nanoseconds (default: now)
        attributes: Extra span attributes dict to merge
        auto_send: If True, send immediately; if False, return the request body for batching

    Returns:
        (status_code, response_body) if auto_send, else the OTLP request dict
    """
    if not ctx.is_active():
        return None

    now = _now_nanos()
    if end_time_ns is None:
        end_time_ns = now
    if start_time_ns is None:
        start_time_ns = end_time_ns - 1_000_000  # 1ms default duration

    input_str = json.dumps(input) if not isinstance(input, str) else input
    output_str = json.dumps(output) if not isinstance(output, str) else output

    # Build span attributes from context + tool I/O + user extras
    span_attrs = dict(ctx.span_attrs)
    span_attrs.update({
        "gen_ai.span.kind": "TOOL",
        "gen_ai.operation.name": "execute_tool",
        "gen_ai.tool.name": name,
        "gen_ai.tool.call.id": ctx.tool_use_id,
        "gen_ai.tool.call.arguments": input_str,
        "gen_ai.tool.call.result": output_str,
        "agentic.tool.type": "tool_script",
        "agentic.tool.duration_ms": str((end_time_ns - start_time_ns) // 1_000_000),
    })
    if status == "error":
        span_attrs["error.type"] = "tool_error"
    if ctx.sofa_traceid:
        span_attrs["_internal.sofa.trace_id"] = ctx.sofa_traceid
    if ctx.sofa_rpcid:
        span_attrs["_internal.sofa.span_id"] = ctx.sofa_rpcid
    if attributes:
        span_attrs.update(attributes)

    span = create_child_span(
        ctx,
        name=name,
        start_time_ns=start_time_ns,
        end_time_ns=end_time_ns,
        attributes=span_attrs,
        status_code=1 if status == "ok" else 2,
    )

    body = _build_otlp_request(ctx, [span])

    if auto_send:
        return send_spans(ctx, body)
    return body


def create_child_span(
    ctx,
    name="child-span",
    start_time_ns=None,
    end_time_ns=None,
    attributes=None,
    status_code=1,
):
    """Create a raw OTel span dict (for batching multiple spans before sending)."""
    now = _now_nanos()
    return {
        "traceId": ctx.trace_id,
        "spanId": _random_span_id(),
        "parentSpanId": ctx.parent_span_id,
        "name": name,
        "kind": 1,  # SPAN_KIND_INTERNAL
        "startTimeUnixNano": str(start_time_ns or now),
        "endTimeUnixNano": str(end_time_ns or now),
        "attributes": _to_otel_attrs(attributes or {}),
        "status": {"code": status_code},
    }


def send_spans(ctx, body):
    """Send an OTLP request body to the configured endpoint.

    Returns (status_code, response_body) or (0, error_message) on failure.
    """
    if not ctx.endpoint:
        return (0, "no endpoint configured")

    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(ctx.endpoint, data=data, method="POST")
    req.add_header("Content-Type", "application/json")

    if ctx.auth:
        req.add_header("Authorization", "Basic " + ctx.auth)
    for k, v in ctx.headers.items():
        req.add_header(k, v)

    try:
        import ssl
        ssl_ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, context=ssl_ctx, timeout=10) as resp:
            return (resp.status, resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        return (e.code, e.read().decode("utf-8", errors="replace"))
    except Exception as e:
        return (0, str(e))


# ── Internal helpers ──

def _parse_traceparent(tp):
    parts = tp.split("-") if tp else []
    if len(parts) != 4:
        return None
    return {"trace_id": parts[1], "parent_span_id": parts[2]}


def _json_env(key):
    raw = os.environ.get(key, "")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {}


def _random_span_id():
    return format(random.getrandbits(64), "016x")


def _now_nanos():
    return int(time.time() * 1_000_000_000)


def _to_otel_attrs(d):
    attrs = []
    for k, v in d.items():
        if isinstance(v, (int, float)):
            attrs.append({"key": k, "value": {"intValue": str(int(v))}})
        else:
            attrs.append({"key": k, "value": {"stringValue": str(v)}})
    return attrs


def _build_otlp_request(ctx, spans):
    resource_attrs = dict(ctx.resource_attrs)
    resource_attrs.setdefault("service.name", "tool-script")
    resource_attrs["telemetry.sdk.language"] = "python"
    resource_attrs["telemetry.sdk.name"] = "tool_context_pure"
    resource_attrs["telemetry.sdk.version"] = "0.1.0"

    return {
        "resourceSpans": [{
            "resource": {"attributes": _to_otel_attrs(resource_attrs)},
            "scopeSpans": [{
                "scope": {"name": "tool_context_pure", "version": "0.3.0"},
                "spans": spans,
            }],
        }],
    }
