#!/usr/bin/env python3
"""Capture deterministic observer envelopes through Hermes v0.18.2 itself."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata
import inspect
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any


PINNED_VERSION = "0.18.2"
SCHEMA_VERSION = "hermes.observer.v1"
FIXTURE_DIR = Path(__file__).resolve().parents[1] / "tests/fixtures/hooks/v0.18.2"


HOOK_CASES: dict[str, dict[str, Any]] = {
    "on_session_start": {
        "source": "agent/conversation_loop.py:367-375",
        "payload": {"session_id": "session-001", "model": "test-model", "platform": "cli"},
    },
    "on_session_end": {
        "source": "agent/turn_finalizer.py:493-503",
        "payload": {
            "session_id": "session-001", "task_id": "task-001", "turn_id": "turn-001",
            "completed": True, "interrupted": False, "model": "test-model", "platform": "cli",
        },
    },
    "on_session_finalize": {
        "source": "cli.py:1145-1158",
        "payload": {"session_id": "session-001", "platform": "cli", "reason": "shutdown"},
    },
    "on_session_reset": {
        "source": "cli.py:6913-6926",
        "payload": {"session_id": "session-001", "platform": "cli", "reason": "new_session"},
    },
    "pre_llm_call": {
        "source": "agent/turn_context.py:467-481",
        "payload": {
            "session_id": "session-001", "task_id": "task-001", "turn_id": "turn-001",
            "user_message": "fixture prompt", "conversation_history": [], "is_first_turn": True,
            "model": "test-model", "platform": "cli", "sender_id": "fixture-user",
        },
    },
    "post_llm_call": {
        "source": "agent/turn_finalizer.py:367-380",
        "payload": {
            "session_id": "session-001", "task_id": "task-001", "turn_id": "turn-001",
            "user_message": "fixture prompt", "assistant_response": "fixture response",
            "conversation_history": [], "model": "test-model", "platform": "cli",
        },
    },
    "pre_api_request": {
        "source": "agent/conversation_loop.py:1197-1248",
        "payload": {
            "task_id": "task-001", "turn_id": "turn-001", "api_request_id": "turn-001:api:1",
            "session_id": "session-001", "user_message": "fixture prompt", "conversation_history": [],
            "platform": "cli", "model": "test-model", "provider": "test-provider",
            "base_url": "https://example.invalid/v1", "api_mode": "openai_chat_completions",
            "api_call_count": 1, "request_messages": [{"role": "user", "content": "fixture prompt"}],
            "message_count": 1, "tool_count": 2, "approx_input_tokens": 11,
            "request_char_count": 14, "max_tokens": 256, "started_at": 1750000000.0,
            "middleware_trace": [], "request": {"method": "POST", "body": {"model": "test-model"}},
        },
    },
    "post_api_request": {
        "source": "agent/conversation_loop.py:4256-4296",
        "payload": {
            "task_id": "task-001", "turn_id": "turn-001", "api_request_id": "turn-001:api:1",
            "session_id": "session-001", "platform": "cli", "model": "test-model",
            "provider": "test-provider", "base_url": "https://example.invalid/v1",
            "api_mode": "openai_chat_completions", "api_call_count": 1, "api_duration": 0.125,
            "started_at": 1750000000.0, "ended_at": 1750000000.125, "finish_reason": "stop",
            "message_count": 1, "response_model": "test-model",
            "response": {"model": "test-model", "finish_reason": "stop", "usage": {"input_tokens": 11, "output_tokens": 7}},
            "usage": {"input_tokens": 11, "output_tokens": 7},
            "assistant_message": {"role": "assistant", "content": "fixture response", "tool_calls": []},
            "assistant_content_chars": 16, "assistant_tool_call_count": 0,
        },
    },
    "api_request_error": {
        "source": "run_agent.py:2418-2471",
        "payload": {
            "task_id": "task-001", "turn_id": "turn-001", "api_request_id": "turn-001:api:1",
            "session_id": "session-001", "platform": "cli", "model": "test-model",
            "provider": "test-provider", "base_url": "https://example.invalid/v1",
            "api_mode": "openai_chat_completions", "api_call_count": 1, "api_duration": 0.25,
            "started_at": 1750000000.0, "ended_at": 1750000000.25, "status_code": 429,
            "retry_count": 1, "max_retries": 3, "retryable": True, "reason": "rate_limit",
            "error": {"type": "RateLimitError", "message": "fixture error"},
            "request": {"method": "POST", "body": {"model": "test-model"}},
        },
    },
    "pre_tool_call": {
        "source": "hermes_cli/plugins.py:2099-2148",
        "payload": {
            "tool_name": "terminal", "args": {"command": "printf fixture"}, "task_id": "task-001",
            "session_id": "session-001", "tool_call_id": "call-001", "turn_id": "turn-001",
            "api_request_id": "turn-001:api:1", "middleware_trace": [],
        },
    },
    "post_tool_call": {
        "source": "model_tools.py:968-1015",
        "payload": {
            "tool_name": "terminal", "args": {"command": "printf fixture"},
            "result": {"output": "fixture"}, "task_id": "task-001", "session_id": "session-001",
            "tool_call_id": "call-001", "turn_id": "turn-001", "api_request_id": "turn-001:api:1",
            "duration_ms": 4, "status": "ok", "error_type": None, "error_message": None,
            "middleware_trace": [],
        },
    },
    "subagent_start": {
        "source": "tools/delegate_tool.py:1383-1395",
        "payload": {
            "parent_session_id": "session-001", "parent_turn_id": "turn-001",
            "parent_subagent_id": None, "child_session_id": "session-child-001",
            "child_subagent_id": "sa-0-fixture", "child_role": "researcher", "child_goal": "fixture goal",
        },
    },
    "subagent_stop": {
        "source": "tools/delegate_tool.py:2717-2728",
        "payload": {
            "parent_session_id": "session-001", "parent_turn_id": "turn-001",
            "child_session_id": "session-child-001", "child_role": "researcher",
            "child_summary": "fixture summary", "child_status": "completed", "duration_ms": 25,
        },
    },
}


def _ensure_hermes_python() -> None:
    """Re-exec with the interpreter from the installed Hermes entry point."""
    try:
        importlib.metadata.version("hermes-agent")
        return
    except importlib.metadata.PackageNotFoundError:
        pass
    hermes = shutil.which("hermes")
    if not hermes:
        raise SystemExit("Hermes CLI is required to capture v0.18.2 hook fixtures")
    first_line = Path(hermes).read_text(encoding="utf-8").splitlines()[0]
    if not first_line.startswith("#!"):
        raise SystemExit(f"cannot resolve Hermes Python interpreter from {hermes}")
    interpreter = first_line[2:].strip()
    if os.environ.get("OBSERV_HERMES_CAPTURE_REEXEC") == "1":
        raise SystemExit("Hermes entry point does not expose hermes-agent package metadata")
    os.environ["OBSERV_HERMES_CAPTURE_REEXEC"] = "1"
    os.execv(interpreter, [interpreter, str(Path(__file__).resolve()), *sys.argv[1:]])


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def capture() -> dict[str, Any]:
    version = importlib.metadata.version("hermes-agent")
    if version != PINNED_VERSION:
        raise SystemExit(f"expected hermes-agent {PINNED_VERSION}, found {version}")

    plugins = importlib.import_module("hermes_cli.plugins")
    observer_schema_version = plugins.OBSERVER_SCHEMA_VERSION
    plugin_manager = plugins.PluginManager
    valid_hooks = plugins.VALID_HOOKS

    if observer_schema_version != SCHEMA_VERSION:
        raise SystemExit(f"unexpected observer schema: {observer_schema_version}")

    manager_source = Path(inspect.getsourcefile(plugin_manager) or "").resolve()
    site_packages = manager_source.parents[1]
    captures: list[dict[str, Any]] = []
    source_files: set[Path] = {manager_source}

    for hook_name, case in HOOK_CASES.items():
        if hook_name not in valid_hooks:
            raise SystemExit(f"Hermes {version} does not expose {hook_name}")
        observed: list[dict[str, Any]] = []
        manager = plugin_manager()
        manager._hooks[hook_name] = [lambda **kwargs: observed.append(kwargs)]
        manager.invoke_hook(hook_name, **case["payload"])
        if len(observed) != 1:
            raise SystemExit(f"expected exactly one captured {hook_name} callback")
        source_relative = str(case["source"]).split(":", 1)[0]
        source_path = site_packages / source_relative
        if not source_path.is_file():
            raise SystemExit(f"missing Hermes source file: {source_path}")
        source_files.add(source_path)
        captures.append({
            "hook": hook_name,
            "provenance": "faithfully minimized from the cited real v0.18.2 callback call site and passed through PluginManager.invoke_hook",
            "source": case["source"],
            "correlationKeys": sorted(k for k in observed[0] if k.endswith("_id")),
            "envelope": observed[0],
        })

    return {
        "schemaVersion": "observ-hermes-hook-fixtures.v1",
        "hermesVersion": version,
        "observerSchemaVersion": SCHEMA_VERSION,
        "generatedBy": "hermes/scripts/capture_hermes_v0182_hooks.py",
        "sourceFiles": [
            {"path": str(path.relative_to(site_packages)), "sha256": _sha256(path)}
            for path in sorted(source_files)
        ],
        "hooks": captures,
    }


def render() -> str:
    return json.dumps(capture(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> None:
    _ensure_hermes_python()
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    output = FIXTURE_DIR / "observer_envelopes.json"
    rendered = render()
    if args.check:
        if not output.is_file() or output.read_text(encoding="utf-8") != rendered:
            raise SystemExit(f"stale Hermes hook fixture: run {Path(__file__).relative_to(Path.cwd())}")
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")


if __name__ == "__main__":
    main()
