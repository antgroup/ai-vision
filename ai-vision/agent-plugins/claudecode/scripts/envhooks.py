#!/usr/bin/env python3
"""Claude Code hook: log all environment variables and stdin context."""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

LOG_PATH = Path.home() / ".claude" / "logs" / "envhooks.log"


def main():
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    try:
        stdin_data = json.loads(sys.stdin.read())
    except Exception:
        stdin_data = {"_error": "failed to read stdin"}

    timestamp = datetime.now().isoformat()
    event = stdin_data.get("hook_event_name", "unknown")
    session = stdin_data.get("session_id", "unknown")

    lines = []
    lines.append(f"{'=' * 72}")
    lines.append(f"[{timestamp}] event={event} session={session}")
    lines.append("")
    lines.append("--- STDIN CONTEXT ---")
    lines.append(json.dumps(stdin_data, indent=2, ensure_ascii=False))
    lines.append("")
    lines.append("--- ENVIRONMENT VARIABLES ---")
    for key in sorted(os.environ):
        lines.append(f"  {key}={os.environ[key]}")
    lines.append("")

    trace_keys = ["TRACEPARENT", "SOFA_TRACEID", "SOFA_RPCID", "SCENE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_PROMPT_ID"]
    print(f"[envhooks] event={event} session={session}")
    for key in trace_keys:
        val = os.environ.get(key, "<not set>")
        print(f"  {key}={val}")

    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
