#!/usr/bin/env python3
"""POC: Read trace context injected by cchooks ToolContextHandler,
construct a child OTLP span with standard attributes, and upload it.

Env vars set by cchooks PreToolUse updatedInput:
  CCHOOKS_TOOL_TRACEPARENT       W3C traceparent
  CCHOOKS_TOOL_USE_ID            Claude Code tool_use_id
  CCHOOKS_TOOL_OTEL_ENDPOINT     OTLP traces endpoint URL
  CCHOOKS_TOOL_OTEL_AUTH         Base64 Basic auth
  CCHOOKS_TOOL_OTEL_HEADERS      JSON custom headers
  CCHOOKS_TOOL_SPAN_ATTRS        JSON span-level attributes
  CCHOOKS_TOOL_RESOURCE_ATTRS    JSON resource-level attributes
"""

import json
import os
import random
import sys
import time
import urllib.request


def parse_traceparent(tp):
    parts = tp.split("-")
    if len(parts) != 4:
        return None
    return {"trace_id": parts[1], "parent_span_id": parts[2]}


def random_span_id():
    return format(random.getrandbits(64), "016x")


def now_nanos():
    return int(time.time() * 1_000_000_000)


def json_env(key):
    raw = os.environ.get(key, "")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def to_otel_attrs(d):
    attrs = []
    for k, v in d.items():
        if isinstance(v, (int, float)):
            attrs.append({"key": k, "value": {"intValue": str(int(v))}})
        else:
            attrs.append({"key": k, "value": {"stringValue": str(v)}})
    return attrs


def build_otlp_request(trace_id, parent_span_id, tool_use_id, span_attrs, resource_attrs):
    span_id = random_span_id()
    start = now_nanos()
    time.sleep(0.01)
    end = now_nanos()

    # Merge standard span attributes with script-specific ones
    merged_span_attrs = dict(span_attrs)
    merged_span_attrs.update({
        "gen_ai.span.kind": "TOOL",
        "gen_ai.operation.name": "execute_tool",
        "gen_ai.tool.name": "tool-script-poc",
        "gen_ai.tool.call.id": tool_use_id,
        "tool.script": "tool_context.py",
    })

    # Merge standard resource attributes with script-specific ones
    merged_resource_attrs = dict(resource_attrs)
    merged_resource_attrs.setdefault("service.name", "tool-script-poc")
    merged_resource_attrs["telemetry.sdk.language"] = "python"
    merged_resource_attrs["telemetry.sdk.name"] = "tool_context.py"
    merged_resource_attrs["telemetry.sdk.version"] = "0.2.0"

    return {
        "resourceSpans": [{
            "resource": {"attributes": to_otel_attrs(merged_resource_attrs)},
            "scopeSpans": [{
                "scope": {"name": "tool_context.py", "version": "0.2.0"},
                "spans": [{
                    "traceId": trace_id,
                    "spanId": span_id,
                    "parentSpanId": parent_span_id,
                    "name": "tool-script-child-span",
                    "kind": 1,
                    "startTimeUnixNano": str(start),
                    "endTimeUnixNano": str(end),
                    "attributes": to_otel_attrs(merged_span_attrs),
                    "status": {"code": 1},
                }],
            }],
        }],
    }, span_id


def send_otlp(endpoint, body, auth="", headers_json=""):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(endpoint, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if auth:
        req.add_header("Authorization", f"Basic {auth}")
    if headers_json:
        try:
            for k, v in json.loads(headers_json).items():
                req.add_header(k, v)
        except json.JSONDecodeError:
            pass
    try:
        import ssl
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:
        return 0, str(e)


def main():
    tp_str = os.environ.get("CCHOOKS_TOOL_TRACEPARENT", "")
    tool_use_id = os.environ.get("CCHOOKS_TOOL_USE_ID", "")
    endpoint = os.environ.get("CCHOOKS_TOOL_OTEL_ENDPOINT", "")
    auth = os.environ.get("CCHOOKS_TOOL_OTEL_AUTH", "")
    headers_json = os.environ.get("CCHOOKS_TOOL_OTEL_HEADERS", "")
    span_attrs = json_env("CCHOOKS_TOOL_SPAN_ATTRS")
    resource_attrs = json_env("CCHOOKS_TOOL_RESOURCE_ATTRS")

    print(f"[tool_context] TRACEPARENT={tp_str}")
    print(f"[tool_context] TOOL_USE_ID={tool_use_id}")
    print(f"[tool_context] ENDPOINT={endpoint[:60]}..." if len(endpoint) > 60 else f"[tool_context] ENDPOINT={endpoint}")
    print(f"[tool_context] AUTH={'***' if auth else '<not set>'}")
    print(f"[tool_context] SPAN_ATTRS={json.dumps(span_attrs)}")
    print(f"[tool_context] RESOURCE_ATTRS={json.dumps(resource_attrs)}")

    if not tp_str:
        print("[tool_context] ERROR: CCHOOKS_TOOL_TRACEPARENT not set")
        sys.exit(1)

    tp = parse_traceparent(tp_str)
    if not tp:
        print(f"[tool_context] ERROR: invalid traceparent: {tp_str}")
        sys.exit(1)

    print(f"[tool_context] trace_id={tp['trace_id']} parent_span_id={tp['parent_span_id']}")

    body, child_span_id = build_otlp_request(
        tp["trace_id"], tp["parent_span_id"], tool_use_id, span_attrs, resource_attrs
    )
    print(f"[tool_context] child_span_id={child_span_id}")

    if not endpoint:
        print("[tool_context] WARN: no endpoint, body only:")
        print(json.dumps(body, indent=2))
        return

    print(f"[tool_context] sending...")
    status, resp = send_otlp(endpoint, body, auth, headers_json)
    print(f"[tool_context] response: status={status} body={resp[:200]}")
    if 200 <= status < 300:
        print("[tool_context] SUCCESS")
    else:
        print(f"[tool_context] FAILED: HTTP {status}")


if __name__ == "__main__":
    main()
