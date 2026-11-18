from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from observ_hermes.config import (
    AntIdentity,
    ObservHermesConfig,
    OtelExporterConfig,
    SignalEndpointConfig,
)
from observ_hermes.metrics import create_metrics
from observ_hermes.pipeline import get_pipeline
from observ_hermes.telemetry import (
    build_resource_attributes,
    init_telemetry,
    normalize_signal_endpoint,
)


def attr_map(items):
    result = {}
    for item in items:
        value = item["value"]
        if "stringValue" in value:
            parsed = value["stringValue"]
        elif "intValue" in value:
            parsed = int(value["intValue"])
        elif "doubleValue" in value:
            parsed = value["doubleValue"]
        elif "boolValue" in value:
            parsed = value["boolValue"]
        else:
            parsed = value
        result[item["key"]] = parsed
    return result


def json_lines(path: Path):
    return [json.loads(line) for line in path.read_text().splitlines() if line]


class CaptureHandler(BaseHTTPRequestHandler):
    paths: ClassVar[list[str]] = []
    response_status: int = 200

    def do_POST(self):
        length = int(self.headers.get("content-length", "0"))
        self.rfile.read(length)
        type(self).paths.append(self.path)
        self.send_response(type(self).response_status)
        self.send_header("content-type", "application/x-protobuf")
        self.end_headers()
        self.wfile.write(b"")

    def log_message(self, format, *args):
        del format, args


class CaptureServer:
    def __init__(self, response_status: int = 200):
        self.response_status = response_status

    def __enter__(self):
        CaptureHandler.paths = []
        CaptureHandler.response_status = self.response_status
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), CaptureHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return f"http://127.0.0.1:{self.server.server_port}"

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def config_for(base: str, root: Path, *, debug: bool) -> ObservHermesConfig:
    return ObservHermesConfig(
        enable_telemetry=True,
        service_name="hermes-test",
        ant=AntIdentity(
            username="tester",
            uid="user-1",
            agent_instance="instance-1",
            agent_env="test",
            agent_scene="golden",
        ),
        otel_exporter=OtelExporterConfig(protocol="http/protobuf"),
        traces_config=SignalEndpointConfig(endpoint=base, enabled=True),
        metrics_config=SignalEndpointConfig(endpoint=base, enabled=True),
        events_config=SignalEndpointConfig(endpoint=base, enabled=True),
        metric_export_interval=60_000,
        logs_export_interval=10,
        debug=debug,
    )


def emit_error_scenario(runtime) -> None:
    pipeline = get_pipeline(runtime)
    schema = {
        "telemetry_schema_version": "hermes.observer.v1",
        "session_id": "session-1",
    }
    pipeline.process(
        "on_session_start", {**schema, "model": "model", "platform": "cli"}
    )
    pipeline.process(
        "pre_llm_call",
        {
            **schema,
            "turn_id": "turn-1",
            "task_id": "task-1",
            "model": "model",
            "user_message": "hello",
            "conversation_history": [],
        },
    )
    pipeline.process(
        "pre_api_request",
        {
            **schema,
            "turn_id": "turn-1",
            "task_id": "task-1",
            "api_request_id": "api-1",
            "model": "model",
            "provider": "anthropic",
            "started_at": 1_750_000_000.0,
        },
    )
    pipeline.process(
        "api_request_error",
        {
            **schema,
            "turn_id": "turn-1",
            "task_id": "task-1",
            "api_request_id": "api-1",
            "model": "model",
            "provider": "anthropic",
            "api_duration": 0.2,
            "status_code": 429,
            "reason": "rate_limit",
            "error": {"type": "RateLimitError", "message": "slow down"},
        },
    )
    pipeline.process(
        "pre_tool_call",
        {
            **schema,
            "turn_id": "turn-1",
            "task_id": "task-1",
            "tool_call_id": "tool-1",
            "tool_name": "mcp__server__read",
            "args": {"path": "README.md"},
        },
    )
    pipeline.process(
        "post_tool_call",
        {
            **schema,
            "turn_id": "turn-1",
            "task_id": "task-1",
            "tool_call_id": "tool-1",
            "tool_name": "mcp__server__read",
            "duration_ms": 5,
            "status": "error",
            "error_type": "ToolError",
            "error_message": "failed",
            "result": {"error": "failed"},
        },
    )
    pipeline.process(
        "on_session_end",
        {
            **schema,
            "turn_id": "turn-1",
            "task_id": "task-1",
            "completed": True,
        },
    )
    pipeline.process(
        "on_session_finalize", {**schema, "reason": "shutdown", "platform": "cli"}
    )


class TelemetryTests(unittest.TestCase):
    def test_signal_endpoint_is_appended_exactly_once(self) -> None:
        self.assertEqual(
            normalize_signal_endpoint("https://collector.example", "traces"),
            "https://collector.example/v1/traces",
        )
        self.assertEqual(
            normalize_signal_endpoint(
                "https://collector.example/api/v1/traces", "traces"
            ),
            "https://collector.example/api/v1/traces",
        )
        self.assertEqual(
            normalize_signal_endpoint(
                "https://collector.example/api/default/v1/metrics", "metrics"
            ),
            "https://collector.example/api/default/v1/metrics",
        )
        with self.assertRaises(ValueError):
            normalize_signal_endpoint("https://collector.example", "profiles")

    def test_resource_has_required_identity_and_no_span_only_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = config_for("http://127.0.0.1:1", Path(tmp), debug=False)
            resource = build_resource_attributes(config)
        required = {
            "service.name",
            "service.version",
            "ant.agent.type",
            "ant.agent.env",
            "ant.agent.scene",
            "agentic.collection.mode",
            "deployment.environment",
        }
        self.assertTrue(required.issubset(resource))
        self.assertTrue(all(resource[key] for key in required))
        self.assertFalse(
            {
                "gen_ai.span.kind",
                "gen_ai.operation.name",
                "gen_ai.conversation.id",
                "gen_ai.chat.id",
                "ant.username",
                "user.id",
                "agentic.turn.id",
                "agentic.turn.number",
            }
            & resource.keys()
        )
        self.assertEqual(resource["deployment.environment"], resource["ant.agent.env"])

    def test_metric_runtime_declares_all_ten_target_instruments(self) -> None:
        class FakeMeter:
            def create_counter(self, name, **options):
                return ("counter", name, options)

            def create_histogram(self, name, **options):
                return ("histogram", name, options)

        metrics = create_metrics(FakeMeter())
        self.assertEqual(
            set(metrics.instruments),
            {
                "agent.session.count",
                "agent.token.usage",
                "agent.cache.read_tokens",
                "agent.cache.write_tokens",
                "agent.api_request.count",
                "agent.api_request.error.count",
                "agent.api_request.duration",
                "agent.tool_call.count",
                "agent.tool_call.error.count",
                "agent.tool_call.duration",
            },
        )
        self.assertEqual(
            metrics.instruments["agent.api_request.duration"][0], "histogram"
        )
        self.assertEqual(
            metrics.instruments["agent.tool_call.duration"][0], "histogram"
        )
        self.assertTrue(
            all(
                instrument[0] == "counter"
                for name, instrument in metrics.instruments.items()
                if not name.endswith(".duration")
            )
        )

    def test_debug_keeps_http_and_creates_no_replay_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, CaptureServer() as base:
            root = Path(tmp)
            runtime = init_telemetry(config_for(base, root, debug=True))
            emit_error_scenario(runtime)
            runtime.force_flush()
            runtime.shutdown()
            runtime.force_flush()
            runtime.shutdown()

            self.assertIn("/v1/traces", CaptureHandler.paths)
            self.assertIn("/v1/logs", CaptureHandler.paths)
            self.assertIn("/v1/metrics", CaptureHandler.paths)
            self.assertFalse((root / "replays").exists())
            self.assertFalse((root / "transcripts").exists())

    def test_normal_mode_keeps_http_without_creating_replay_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, CaptureServer() as base:
            root = Path(tmp)
            runtime = init_telemetry(config_for(base, root, debug=False))
            emit_error_scenario(runtime)
            runtime.shutdown()
            self.assertIn("/v1/traces", CaptureHandler.paths)
            self.assertIn("/v1/logs", CaptureHandler.paths)
            self.assertIn("/v1/metrics", CaptureHandler.paths)
            self.assertFalse((root / "replays").exists())

    def test_http_export_failure_does_not_break_hooks_or_leak_headers(self) -> None:
        secret = "Bearer must-not-appear"
        with (
            tempfile.TemporaryDirectory() as tmp,
            CaptureServer(response_status=503) as base,
        ):
            root = Path(tmp)
            config = config_for(base, root, debug=True)
            for signal in (
                config.traces_config,
                config.metrics_config,
                config.events_config,
            ):
                signal.headers = {"authorization": secret}
            with self.assertLogs(level="ERROR") as captured:
                runtime = init_telemetry(config)
                emit_error_scenario(runtime)
                runtime.shutdown()

            self.assertFalse((root / "replays").exists())
            self.assertNotIn(secret, "\n".join(captured.output))


if __name__ == "__main__":
    unittest.main()
