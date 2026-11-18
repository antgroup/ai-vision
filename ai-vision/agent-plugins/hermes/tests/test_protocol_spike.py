from __future__ import annotations

import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TypedDict

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from observ_hermes.exporters.http_json import HttpJsonSink
from observ_hermes.semantic import SemanticObservation
from observ_hermes.telemetry import init_telemetry
from tests.test_telemetry import config_for, emit_error_scenario


class CapturedRequest(TypedDict):
    path: str
    content_type: str
    headers: dict[str, str]
    body: bytes


class ProtocolCaptureHandler(BaseHTTPRequestHandler):
    records: list[CapturedRequest] = []
    response_status = 200

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length", "0"))
        type(self).records.append(
            {
                "path": self.path,
                "content_type": self.headers.get("content-type", ""),
                "headers": {
                    key.casefold(): value for key, value in self.headers.items()
                },
                "body": self.rfile.read(length),
            }
        )
        self.send_response(type(self).response_status)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        pass


class ProtocolCaptureServer:
    def __init__(self, response_status: int = 200) -> None:
        self.response_status = response_status

    def __enter__(self) -> str:
        ProtocolCaptureHandler.records = []
        ProtocolCaptureHandler.response_status = self.response_status
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), ProtocolCaptureHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return f"http://127.0.0.1:{self.server.server_port}"

    def __exit__(self, *_args: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def sample_observations() -> list[SemanticObservation]:
    start = "2025-06-15T15:06:40+00:00"
    end = "2025-06-15T15:06:41+00:00"
    return [
        SemanticObservation(
            type="span.start",
            name="turn",
            key="turn-1",
            span_kind="CHAT",
            timestamp=start,
            attributes={"gen_ai.span.kind": "CHAT"},
        ),
        SemanticObservation(
            type="event",
            name="agent.test",
            key="turn-1",
            timestamp=start,
            attributes={"event.sequence": 1},
        ),
        SemanticObservation(
            type="metric",
            name="agent.test.count",
            timestamp=start,
            metric_kind="counter",
            unit="{count}",
            value=1,
            attributes={"agentic.collection.mode": "full"},
        ),
        SemanticObservation(
            type="span.end",
            name="turn",
            key="turn-1",
            timestamp=end,
            status="OK",
            attributes={"gen_ai.operation.name": "chat"},
        ),
    ]


class ProtocolSpikeTests(unittest.TestCase):
    @staticmethod
    def json_temporalities(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "aggregationTemporality":
                    yield int(child)
                else:
                    yield from ProtocolSpikeTests.json_temporalities(child)
        elif isinstance(value, list):
            for child in value:
                yield from ProtocolSpikeTests.json_temporalities(child)

    def test_runtime_protocol_selection_uses_json_transport_for_all_signals(
        self,
    ) -> None:
        with ProtocolCaptureServer() as base:
            config = config_for(base, Path("/unused"), debug=True)
            config.otel_exporter.protocol = "http/json"
            config.otel_exporter.metrics_temporality_preference = "delta"
            runtime = init_telemetry(config)
            emit_error_scenario(runtime)
            runtime.shutdown()

        expected_roots = {
            "/v1/traces": "resourceSpans",
            "/v1/metrics": "resourceMetrics",
            "/v1/logs": "resourceLogs",
        }
        seen: set[str] = set()
        for record in ProtocolCaptureHandler.records:
            path = str(record["path"])
            seen.add(path)
            self.assertEqual(record["content_type"], "application/json")
            body = json.loads(bytes(record["body"]).decode("utf-8"))
            self.assertIn(expected_roots[path], body)
        self.assertEqual(seen, set(expected_roots))

    def test_json_delta_http_points_chain_real_source_intervals(self) -> None:
        from dataclasses import replace
        from observ_hermes.exporters.otlp_json import unix_nano

        start, event, metric, terminal = sample_observations()
        first_metric = replace(metric, timestamp=terminal.timestamp)
        second_metric = replace(metric, timestamp="2025-06-15T15:06:43+00:00")
        with ProtocolCaptureServer() as base:
            sink = HttpJsonSink({}, {"metrics": base + "/v1/metrics"},
                                aggregation_temporality=1)
            for observation in (start, event, first_metric, second_metric):
                sink(observation)
            sink.shutdown()
        points = []
        for record in ProtocolCaptureHandler.records:
            payload = json.loads(record["body"])
            metric_sum = payload["resourceMetrics"][0]["scopeMetrics"][0]["metrics"][0]["sum"]
            self.assertEqual(metric_sum["aggregationTemporality"], 1)
            points.extend(metric_sum["dataPoints"])
        self.assertEqual(len(points), 2)
        self.assertEqual(points[0]["startTimeUnixNano"], unix_nano(start.timestamp))
        self.assertEqual(points[1]["startTimeUnixNano"], points[0]["timeUnixNano"])
        for point in points:
            self.assertLess(int(point["startTimeUnixNano"]), int(point["timeUnixNano"]))
            self.assertEqual(point["asInt"], "1")

    def test_json_live_creation_clock_covers_immediate_session_counter(self) -> None:
        from dataclasses import replace
        from unittest.mock import patch
        from observ_hermes.exporters.otlp_json import unix_nano

        created = int(unix_nano("2025-06-15T15:06:39+00:00"))
        metric = replace(sample_observations()[2], name="agent.session.count")
        with ProtocolCaptureServer() as base:
            with patch("observ_hermes.exporters.http_json.time.time_ns", return_value=created):
                sink = HttpJsonSink({}, {"metrics": base + "/v1/metrics"},
                                    aggregation_temporality=1)
            sink(metric)
            sink.shutdown()
        payload = json.loads(ProtocolCaptureHandler.records[0]["body"])
        point = payload["resourceMetrics"][0]["scopeMetrics"][0]["metrics"][0]["sum"]["dataPoints"][0]
        self.assertEqual(point["startTimeUnixNano"], str(created))
        self.assertEqual(point["timeUnixNano"], unix_nano(metric.timestamp))
        self.assertEqual(point["asInt"], "1")

    def test_http_json_emits_expected_paths_content_type_and_shapes(self) -> None:
        with ProtocolCaptureServer() as base:
            sink = HttpJsonSink(
                {"service.name": "hermes-spike"},
                {
                    "traces": f"{base}/v1/traces",
                    "metrics": f"{base}/v1/metrics",
                    "logs": f"{base}/v1/logs",
                },
            )
            for observation in sample_observations():
                sink(observation)
            sink.force_flush()
            sink.shutdown()
            sink.shutdown()
            sink(sample_observations()[1])

        records = {
            str(record["path"]): record for record in ProtocolCaptureHandler.records
        }
        self.assertEqual(set(records), {"/v1/traces", "/v1/metrics", "/v1/logs"})
        expected_roots = {
            "/v1/traces": "resourceSpans",
            "/v1/metrics": "resourceMetrics",
            "/v1/logs": "resourceLogs",
        }
        for path, root in expected_roots.items():
            self.assertEqual(records[path]["content_type"], "application/json")
            body = json.loads(bytes(records[path]["body"]).decode("utf-8"))
            self.assertTrue(body[root])

    def test_http_json_error_is_isolated_and_does_not_expose_headers(self) -> None:
        secret = "Bearer protocol-spike-secret"
        with ProtocolCaptureServer(response_status=503) as base:
            sink = HttpJsonSink(
                {"service.name": "hermes-spike"},
                {"logs": f"{base}/v1/logs"},
                {"logs": {"authorization": secret}},
            )
            sink(sample_observations()[1])
            sink.shutdown()

        self.assertEqual(sink.errors, [{"signal": "logs", "status": 503}])
        self.assertNotIn(secret, repr(sink.errors))

    def test_http_protobuf_sdk_emits_parseable_requests_for_all_signals(self) -> None:
        from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import (
            ExportLogsServiceRequest,
        )
        from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import (
            ExportMetricsServiceRequest,
        )
        from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
            ExportTraceServiceRequest,
        )

        with ProtocolCaptureServer() as base:
            config = config_for(base, Path("/unused"), debug=False)
            config.otel_exporter.protocol = "http/protobuf"
            runtime = init_telemetry(config)
            emit_error_scenario(runtime)
            runtime.shutdown()

        records = {
            str(record["path"]): record for record in ProtocolCaptureHandler.records
        }
        parsers = {
            "/v1/traces": ExportTraceServiceRequest,
            "/v1/metrics": ExportMetricsServiceRequest,
            "/v1/logs": ExportLogsServiceRequest,
        }
        self.assertEqual(set(records), set(parsers))
        for path, parser_type in parsers.items():
            self.assertEqual(records[path]["content_type"], "application/x-protobuf")
            parsed = parser_type()
            parsed.ParseFromString(bytes(records[path]["body"]))
            self.assertTrue(parsed.ListFields())

    def test_signal_enablement_suppresses_only_the_selected_signal_for_both_protocols(
        self,
    ) -> None:
        signal_config = {
            "traces": "traces_config",
            "metrics": "metrics_config",
            "logs": "events_config",
        }
        for protocol in ("http/json", "http/protobuf"):
            for disabled, attribute in signal_config.items():
                with (
                    self.subTest(protocol=protocol, disabled=disabled),
                    ProtocolCaptureServer() as base,
                ):
                    config = config_for(base, Path("/unused"), debug=False)
                    config.otel_exporter.protocol = protocol
                    getattr(config, attribute).enabled = False
                    runtime = init_telemetry(config)
                    emit_error_scenario(runtime)
                    runtime.shutdown()
                paths = {
                    str(record["path"]) for record in ProtocolCaptureHandler.records
                }
                expected = {
                    f"/v1/{signal}" for signal in signal_config if signal != disabled
                }
                self.assertEqual(paths, expected)

    def test_configured_headers_and_attributes_reach_every_json_signal(self) -> None:
        with ProtocolCaptureServer() as base:
            config = config_for(base, Path("/unused"), debug=False)
            config.otel_exporter.protocol = "http/json"
            config.otel_exporter.headers = {
                "global-only": "global",
                "collision": "global",
            }
            config.otel_exporter.attributes = {"custom.signal": "signal-value"}
            config.otel_exporter.resource_attributes = {
                "custom.resource": "resource-value"
            }
            for signal, signal_config in (
                ("traces", config.traces_config),
                ("metrics", config.metrics_config),
                ("logs", config.events_config),
            ):
                signal_config.headers = {
                    "signal-only": signal,
                    "collision": signal,
                }
            runtime = init_telemetry(config)
            emit_error_scenario(runtime)
            runtime.shutdown()

        self.assertEqual(
            {str(record["path"]) for record in ProtocolCaptureHandler.records},
            {"/v1/traces", "/v1/metrics", "/v1/logs"},
        )
        for record in ProtocolCaptureHandler.records:
            signal = str(record["path"]).rsplit("/", 1)[-1]
            headers = record["headers"]
            self.assertEqual(headers["global-only"], "global")
            self.assertEqual(headers["signal-only"], signal)
            self.assertEqual(headers["collision"], signal)
            rendered = bytes(record["body"]).decode("utf-8")
            self.assertIn("custom.signal", rendered)
            self.assertIn("signal-value", rendered)
            self.assertIn("custom.resource", rendered)
            self.assertIn("resource-value", rendered)

    def test_metric_temporality_preference_is_serialized_for_both_protocols(
        self,
    ) -> None:
        from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import (
            ExportMetricsServiceRequest,
        )

        for protocol in ("http/json", "http/protobuf"):
            for preference, expected in (("delta", 1), ("cumulative", 2)):
                with (
                    self.subTest(protocol=protocol, preference=preference),
                    ProtocolCaptureServer() as base,
                ):
                    config = config_for(base, Path("/unused"), debug=False)
                    config.otel_exporter.protocol = protocol
                    config.otel_exporter.metrics_temporality_preference = preference
                    runtime = init_telemetry(config)
                    emit_error_scenario(runtime)
                    runtime.shutdown()
                record = next(
                    item
                    for item in ProtocolCaptureHandler.records
                    if item["path"] == "/v1/metrics"
                )
                if protocol == "http/json":
                    body = json.loads(bytes(record["body"]).decode("utf-8"))
                    temporalities = set(self.json_temporalities(body))
                else:
                    parsed = ExportMetricsServiceRequest()
                    parsed.ParseFromString(bytes(record["body"]))
                    temporalities = {
                        metric.sum.aggregation_temporality
                        for resource in parsed.resource_metrics
                        for scope in resource.scope_metrics
                        for metric in scope.metrics
                        if metric.HasField("sum")
                    }
                self.assertEqual(temporalities, {expected})


if __name__ == "__main__":
    unittest.main()
