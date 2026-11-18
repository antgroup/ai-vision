#!/usr/bin/env python3
"""Codex hook: log environment variables visible to cdxhooks."""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

LOG_PATH = Path.home() / ".codex" / "logs" / "cdx-envhooks.log"

SECRET_KEYS = {"CDXHOOKS_LANGFUSE_SECRET_KEY", "LANGFUSE_SECRET_KEY",
                  "CDXHOOKS_LANGFUSE_PUBLIC_KEY", "LANGFUSE_PUBLIC_KEY"}

CDXHOOKS_VARS = [
    # ── Langfuse keys ──
    "CDXHOOKS_LANGFUSE_PUBLIC_KEY",
    "CDXHOOKS_LANGFUSE_SECRET_KEY",
    "CDXHOOKS_LANGFUSE_BASE_URL",
    "CDXHOOKS_LANGFUSE_ENV",
    "CDXHOOKS_LANGFUSE_HEADERS",
    "CDXHOOKS_LANGFUSE_METADATA",
    "CDXHOOKS_LANGFUSE_TAGS",
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "LANGFUSE_BASE_URL",
    "LANGFUSE_ENV",
    "LANGFUSE_HEADERS",
    "LANGFUSE_METADATA",
    "LANGFUSE_TAGS",
    # ── Agent identity ──
    "CDXHOOKS_AGENT_PRODUCT",
    "CDXHOOKS_AGENT_TYPE",
    "CDXHOOKS_AGENT_ENV",
    "CDXHOOKS_AGENT_SCENE",
    "AIVISION_AGENT_PRODUCT",
    "AIVISION_AGENT_TYPE",
    "AIVISION_AGENT_ENV",
    "AIVISION_AGENT_SCENE",
    # ── Username ──
    "CDXHOOKS_USERNAME",
    "AIVISION_ANT_USERNAME",
    "LANGFUSE_ATTR_ANT_USERNAME",
    "LANGFUSE_ATTR_USERNAME",
    "USERNAME",
    "USER",
    # ── OTel Resource Attributes ──
    "OTEL_RESOURCE_ATTRIBUTES",
    # ── Debug / paths ──
    "CDXHOOKS_DEBUG",
    "CDXHOOKS_LOG_DIR",
    "CDXHOOKS_INPUT_FORMAT",
    "CDXHOOKS_DYNAMIC_UPLOAD_URL",
    "CDXHOOKS_METRICS_ENDPOINT",
    "CODEX_SESSIONS_DIR",
    "CODEX_PLUGIN_ROOT",
    # ── System ──
    "HOSTNAME",
    "HOME",
    "ANT_GRAY",
    "AIVISION_TLS_NOVERIFY",
]


def main():
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    try:
        stdin_data = json.loads(sys.stdin.read())
    except Exception:
        stdin_data = {}

    event = stdin_data.get("hook_event_name", stdin_data.get("event", "unknown"))
    session = stdin_data.get("session_id", "unknown")
    timestamp = datetime.now().isoformat()

    lines = []
    lines.append(f"{'=' * 72}")
    lines.append(f"[{timestamp}] event={event} session={session}")
    lines.append("")

    lines.append("── cdxhooks environment variables ──")
    for key in CDXHOOKS_VARS:
        raw_val = os.environ.get(key)
        val = (raw_val[:4] + "***") if raw_val and key in SECRET_KEYS else raw_val
        if val is not None:
            lines.append(f"  {key}={val}")
        else:
            lines.append(f"  {key}=<not set>")

    lines.append("")
    lines.append("── stdin payload keys ──")
    if isinstance(stdin_data, dict):
        for k in sorted(stdin_data.keys()):
            v = stdin_data[k]
            if isinstance(v, str) and len(v) > 80:
                v = v[:60] + "..."
            lines.append(f"  {k}={v}")
    lines.append("")

    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    # stderr summary (visible in Codex hook output)
    print(f"[cdx-envhooks] event={event} logged to {LOG_PATH}", file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[cdx-envhooks] error: {e}", file=sys.stderr)
