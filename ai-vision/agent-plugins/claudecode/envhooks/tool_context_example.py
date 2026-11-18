#!/usr/bin/env python3
"""Example: using tool_context_pure to record tool operations.

This script demonstrates how a tool script called by Claude Code
can record its operations as OTel child spans, linked to the
parent cchooks trace. No otel SDK needed.

Run via Claude Code (CCHOOKS_TOOL_CONTEXT=true):
    python3 claudecode/envhooks/tool_context_example.py

The CCHOOKS_TOOL_* env vars are injected automatically by cchooks
ToolContextHandler. The script itself needs no trace configuration.
"""

import sys
import os
import time

# Add parent dir to path for import
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tool_context_pure import ToolTraceContext, record_tool_span


def do_work():
    """Simulate some tool work — this is your normal script logic."""
    time.sleep(0.05)
    return {"result": "processed 42 items", "status": "success"}


def main():
    # 1. Initialize trace context (no-op if not running under cchooks)
    ctx = ToolTraceContext.from_env()

    if not ctx.is_active():
        print("[example] Not running under cchooks trace context, executing normally.")
        result = do_work()
        print(f"[example] Result: {result}")
        return

    print(f"[example] Trace context active: trace_id={ctx.trace_id[:16]}...")

    # 2. Record a tool operation with input/output
    start = int(time.time() * 1_000_000_000)
    result = do_work()
    end = int(time.time() * 1_000_000_000)

    resp = record_tool_span(
        ctx,
        name="data-processing",
        input={"items": 42, "mode": "batch"},
        output=result,
        status="ok",
        start_time_ns=start,
        end_time_ns=end,
        attributes={
            "tool.script": "tool_context_example.py",
            "processing.item_count": 42,
        },
    )

    if resp:
        status_code, body = resp
        print(f"[example] Span uploaded: status={status_code} body={body[:100]}")
    else:
        print("[example] Span recording skipped (context not active)")


if __name__ == "__main__":
    main()
