#!/usr/bin/env python3
"""Local upstream-service simulator for cdxhooks tool-context propagation.

The self-test mode needs only Python's standard library. It invokes the
cdxhooks PreToolUse hook with an upstream TRACEPARENT, executes the rewritten
Bash command, and verifies the context observed by the child process.

The run mode wraps a real command (for example ``codex exec ...``), injects a
fresh upstream TRACEPARENT, and writes or posts a matching synthetic upstream
OTLP span so the complete parent node can appear in the trace backend.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import shlex
import subprocess
import sys
import tempfile
import time
import urllib.request


FIXED_TRACE_ID = "0af7651916cd43dd8448eb211c80319c"
FIXED_UPSTREAM_SPAN_ID = "b7ad6b7169203331"
FIXED_SOFA_TRACE_ID = "0a1b2c3d161716800000010012345"
FIXED_SOFA_RPC_ID = "0.1.2.3"
SELF_TEST_SESSION_ID = "mock-upstream-session"
SELF_TEST_TOOL_USE_ID = "call-mock-upstream"


def deterministic_tool_span_id(tool_use_id: str) -> str:
    digest = hashlib.sha256(
        b"cdxhooks-tool-span-id:" + tool_use_id.encode("utf-8")
    ).digest()
    return digest[:8].hex()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def capture_child_env(output: Path) -> int:
    keys = (
        "TRACEPARENT",
        "AGENTIC_TOOL_USE_ID",
        "SOFA_TRACEID",
        "SOFA_RPCID",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        "OTEL_EXPORTER_OTLP_AUTH",
        "OTEL_EXPORTER_OTLP_HEADERS",
        "AGENTIC_SPAN_ATTRIBUTES",
        "OTEL_RESOURCE_ATTRIBUTES",
    )
    output.write_text(
        json.dumps({key: os.environ.get(key) for key in keys}, sort_keys=True),
        encoding="utf-8",
    )
    return 0


def wait_for_queue_worker_idle(hook_bin: Path, env: dict[str, str]) -> None:
    """Wait until the detached worker has crossed its queue-release barrier."""
    deadline = time.monotonic() + 5.0
    last_error = "worker status was not sampled"
    while time.monotonic() < deadline:
        status = subprocess.run(
            [
                str(hook_bin),
                "worker",
                "status",
                "--session-id",
                SELF_TEST_SESSION_ID,
            ],
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )
        if status.returncode == 0:
            try:
                worker_status = json.loads(status.stdout)["status"]
                if (
                    worker_status["pending_queue_items"] == 0
                    and worker_status["worker_starting"] == 0
                ):
                    return
                last_error = f"worker still active: {worker_status}"
            except (KeyError, TypeError, json.JSONDecodeError) as error:
                last_error = f"invalid worker status: {error}: {status.stdout!r}"
        else:
            last_error = f"worker status failed: {status.stderr.strip()}"
        time.sleep(0.02)
    raise RuntimeError(f"timed out waiting for detached worker: {last_error}")


def run_self_test(hook_bin: Path) -> int:
    require(hook_bin.is_file(), f"cdxhooks binary not found: {hook_bin}")

    expected_tool_span_id = deterministic_tool_span_id(SELF_TEST_TOOL_USE_ID)
    expected_tool_traceparent = f"00-{FIXED_TRACE_ID}-{expected_tool_span_id}-01"

    with tempfile.TemporaryDirectory(prefix="cdxhooks-mock-upstream-") as tmp:
        workspace = Path(tmp)
        state_dir = workspace / "state"
        log_dir = workspace / "logs"
        child_output = workspace / "child-env.json"
        state_dir.mkdir()
        log_dir.mkdir()

        env = os.environ.copy()
        env.update(
            {
                "CDXHOOKS_ENABLE_TELEMETRY": "true",
                "CDXHOOKS_TOOL_CONTEXT": "true",
                "CDXHOOKS_STATE_DIR": str(state_dir),
                "CDXHOOKS_LOG_DIR": str(log_dir),
                "CDXHOOKS_LANGFUSE_BASE_URL": "http://127.0.0.1:4318",
                "CDXHOOKS_LANGFUSE_PUBLIC_KEY": "mock-pk",
                "CDXHOOKS_LANGFUSE_SECRET_KEY": "mock-sk",
                "CDXHOOKS_USERNAME": "mock-user",
                "TRACEPARENT": (f"00-{FIXED_TRACE_ID}-{FIXED_UPSTREAM_SPAN_ID}-01"),
                "SOFA_TRACEID": FIXED_SOFA_TRACE_ID,
                "SOFA_RPCID": FIXED_SOFA_RPC_ID,
            }
        )

        script = Path(__file__).resolve()
        child_command = " ".join(
            shlex.quote(part)
            for part in (
                sys.executable,
                str(script),
                "_capture-env",
                str(child_output),
            )
        )
        payload = {
            "hook_event_name": "PreToolUse",
            "session_id": SELF_TEST_SESSION_ID,
            "tool_name": "Bash",
            "tool_use_id": SELF_TEST_TOOL_USE_ID,
            "tool_input": {"command": child_command},
        }

        hook = subprocess.run(
            [str(hook_bin)],
            input=json.dumps(payload) + "\n",
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )
        require(hook.returncode == 0, f"PreToolUse failed: {hook.stderr}")
        require(hook.stdout.strip(), "PreToolUse returned no updatedInput")
        response = json.loads(hook.stdout.strip().splitlines()[-1])
        updated_command = response["hookSpecificOutput"]["updatedInput"]["command"]
        require("mock-sk" not in updated_command, "secret leaked into updated command")
        require("export TRACEPARENT=" in updated_command, "TRACEPARENT export missing")
        require(
            "export OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=" in updated_command,
            "OTLP endpoint export missing",
        )
        require(
            "export OTEL_EXPORTER_OTLP_AUTH=" in updated_command,
            "OTLP auth export missing",
        )
        require("rm -f" not in updated_command, "destructive cleanup was injected")
        require("cdxhooks_tool_env" not in updated_command, "temporary env wrapper remains")

        child = subprocess.run(
            ["/bin/sh", "-c", updated_command],
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )
        require(child.returncode == 0, f"rewritten Bash command failed: {child.stderr}")
        require(child_output.is_file(), "child process did not capture its environment")

        observed = json.loads(child_output.read_text(encoding="utf-8"))
        require(
            observed["TRACEPARENT"] == expected_tool_traceparent,
            "wrong child TRACEPARENT",
        )
        require(
            observed["AGENTIC_TOOL_USE_ID"] == SELF_TEST_TOOL_USE_ID,
            "wrong agentic tool id",
        )
        require(
            observed["SOFA_TRACEID"] == FIXED_SOFA_TRACE_ID,
            "SOFA_TRACEID not propagated",
        )
        require(
            observed["SOFA_RPCID"] == FIXED_SOFA_RPC_ID, "SOFA_RPCID not propagated"
        )
        require(
            observed["OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"]
            == "http://127.0.0.1:4318/api/public/otel/v1/traces",
            "wrong child OTLP endpoint",
        )

        span_attrs = json.loads(observed["AGENTIC_SPAN_ATTRIBUTES"])
        resource_attrs = json.loads(observed["OTEL_RESOURCE_ATTRIBUTES"])
        require(
            span_attrs["gen_ai.conversation.id"] == SELF_TEST_SESSION_ID,
            "wrong session attr",
        )
        require(
            span_attrs["gen_ai.tool.call.id"] == SELF_TEST_TOOL_USE_ID,
            "wrong call attr",
        )
        require(resource_attrs["ant.agent.type"] == "codex", "wrong agent type")

        state_files: list[Path] = []
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            state_files = list(state_dir.glob("*/state_turn.json"))
            if len(state_files) == 1:
                break
            time.sleep(0.02)
        require(
            len(state_files) == 1,
            f"expected one session-local turn domain, found {state_files}",
        )
        state = json.loads(state_files[0].read_text(encoding="utf-8"))
        current = state["current_trace_context"]
        require(
            current["trace_id"] == FIXED_TRACE_ID,
            "state did not reuse upstream trace id",
        )
        require(
            current["upstream_parent_span_id"] == FIXED_UPSTREAM_SPAN_ID,
            "state did not preserve upstream parent span id",
        )
        require(
            current["traceparent"] == expected_tool_traceparent,
            "wrong state traceparent",
        )
        # The hook parent intentionally returns before its unique session
        # worker. Status takes worker.queue.lock, so observing an empty queue
        # here proves the worker crossed the same release barrier before the
        # TemporaryDirectory removes its state files.
        wait_for_queue_worker_idle(hook_bin, env)

    print("PASS: mock upstream -> PreToolUse -> Bash child")
    print(f"  upstream trace_id:       {FIXED_TRACE_ID}")
    print(f"  upstream parent span_id: {FIXED_UPSTREAM_SPAN_ID}")
    print(f"  Bash tool span_id:       {expected_tool_span_id}")
    return 0


def otlp_string(key: str, value: str) -> dict[str, object]:
    return {"key": key, "value": {"stringValue": value}}


def build_upstream_otlp(
    trace_id: str,
    span_id: str,
    start_ns: int,
    end_ns: int,
    exit_code: int,
) -> dict[str, object]:
    span: dict[str, object] = {
        "traceId": trace_id,
        "spanId": span_id,
        "name": "mock-upstream.invoke-codex",
        "kind": 3,
        "startTimeUnixNano": str(start_ns),
        "endTimeUnixNano": str(end_ns),
        "status": {"code": 1 if exit_code == 0 else 2},
    }
    if exit_code != 0:
        span["status"] = {"code": 2, "message": f"command exited with {exit_code}"}

    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        otlp_string("service.name", "mock-sofa-upstream"),
                        otlp_string("telemetry.sdk.name", "opentelemetry"),
                        otlp_string("telemetry.sdk.language", "python"),
                    ]
                },
                "scopeSpans": [
                    {
                        "scope": {"name": "cdxhooks.mock-upstream", "version": "1"},
                        "spans": [span],
                    }
                ],
            }
        ]
    }


def parse_headers(values: list[str]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for value in values:
        key, separator, header_value = value.partition("=")
        require(bool(separator) and bool(key.strip()), f"invalid header: {value}")
        headers[key.strip()] = header_value.strip()
    return headers


def post_otlp(endpoint: str, body: dict[str, object], headers: dict[str, str]) -> None:
    request_headers = {"Content-Type": "application/json", **headers}
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(body).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        require(
            200 <= response.status < 300, f"OTLP POST returned HTTP {response.status}"
        )


def resolve_live_headers(cli_headers: list[str]) -> dict[str, str]:
    headers = parse_headers(cli_headers)
    for env_name in (
        "MOCK_UPSTREAM_OTEL_HEADERS",
        "CDXHOOKS_OTEL_EXPORTER_OTLP_HEADERS",
        "OTEL_EXPORTER_OTLP_HEADERS",
    ):
        env_headers = os.environ.get(env_name, "")
        if env_headers:
            headers.update(parse_headers(env_headers.split(",")))

    auth = (
        os.environ.get("MOCK_UPSTREAM_OTEL_AUTH")
        or os.environ.get("OTEL_EXPORTER_OTLP_AUTH")
    )
    if not auth:
        public_key = os.environ.get("CDXHOOKS_LANGFUSE_PUBLIC_KEY")
        secret_key = os.environ.get("CDXHOOKS_LANGFUSE_SECRET_KEY")
        if public_key and secret_key:
            token = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
            auth = f"Basic {token}"
    if auth:
        headers["Authorization"] = auth
    return headers


def run_live(args: argparse.Namespace) -> int:
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    require(bool(command), "run mode requires a command after --")

    trace_id = args.trace_id or secrets.token_hex(16)
    span_id = args.span_id or secrets.token_hex(8)
    require(
        len(trace_id) == 32 and int(trace_id, 16) != 0,
        "trace id must be 32 non-zero hex chars",
    )
    require(
        len(span_id) == 16 and int(span_id, 16) != 0,
        "span id must be 16 non-zero hex chars",
    )

    env = os.environ.copy()
    env["TRACEPARENT"] = f"00-{trace_id}-{span_id}-01"
    env["SOFA_TRACEID"] = args.sofa_traceid
    env["SOFA_RPCID"] = args.sofa_rpcid

    start_ns = time.time_ns()
    completed = subprocess.run(command, env=env, check=False)
    end_ns = time.time_ns()
    body = build_upstream_otlp(
        trace_id, span_id, start_ns, end_ns, completed.returncode
    )

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(body, separators=(",", ":")) + "\n", encoding="utf-8")

    endpoint = (
        args.endpoint
        or os.environ.get("MOCK_UPSTREAM_OTEL_ENDPOINT")
        or os.environ.get("CDXHOOKS_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
        or os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
    )
    if endpoint:
        post_otlp(endpoint, body, resolve_live_headers(args.header))

    print(f"mock upstream trace_id={trace_id} span_id={span_id}")
    print(f"upstream OTLP replay: {output}")
    if endpoint:
        print(f"upstream span posted to: {endpoint}")
    else:
        print("upstream span not posted (set --endpoint to enable)")
    return completed.returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="mode", required=True)

    self_test = subparsers.add_parser(
        "self-test", help="run the local cdxhooks integration test"
    )
    self_test.add_argument("--hook-bin", type=Path, required=True)

    live = subparsers.add_parser(
        "run", help="run a real command as a synthetic upstream service"
    )
    live.add_argument("--trace-id")
    live.add_argument("--span-id")
    live.add_argument("--sofa-traceid", default=FIXED_SOFA_TRACE_ID)
    live.add_argument("--sofa-rpcid", default=FIXED_SOFA_RPC_ID)
    live.add_argument("--endpoint", help="optional OTLP/HTTP traces endpoint")
    live.add_argument("--header", action="append", default=[], metavar="NAME=VALUE")
    live.add_argument("--output", default="replays/mock_upstream.jsonl")
    live.add_argument("command", nargs=argparse.REMAINDER)

    capture = subparsers.add_parser("_capture-env")
    capture.add_argument("output", type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.mode == "self-test":
            return run_self_test(args.hook_bin.resolve())
        if args.mode == "run":
            return run_live(args)
        if args.mode == "_capture-env":
            return capture_child_env(args.output)
        raise RuntimeError(f"unknown mode: {args.mode}")
    except (KeyError, ValueError, RuntimeError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
