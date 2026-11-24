"""Loopback OTLP three-signal capture; optional forwarding keeps original bytes."""
from __future__ import annotations

import base64
from copy import deepcopy
import gzip
from http.server import BaseHTTPRequestHandler
from io import BytesIO
import json
import threading
import time

import httpx
from google.protobuf.json_format import MessageToDict, ParseDict, ParseError
from google.protobuf.message import DecodeError
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest, ExportTraceServiceResponse
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest, ExportLogsServiceResponse
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest, ExportMetricsServiceResponse

from loopback_http import LoopbackHTTPServer

REQUESTS = {"traces": ExportTraceServiceRequest, "logs": ExportLogsServiceRequest, "metrics": ExportMetricsServiceRequest}
RESPONSES = {"traces": ExportTraceServiceResponse, "logs": ExportLogsServiceResponse, "metrics": ExportMetricsServiceResponse}
MAX_BODY = 8 * 1024 * 1024


def read_body(stream, headers):
    """Decode bounded HTTP chunk framing used by the native Node exporter."""
    transfer = headers.get("Transfer-Encoding", "").lower()
    length = headers.get("Content-Length")
    if transfer:
        if transfer != "chunked" or length is not None:
            raise ValueError("Ambiguous or unsupported body framing")
        chunks, total = [], 0
        while True:
            line = stream.readline(128)
            if not line.endswith(b"\r\n"):
                raise ValueError("Invalid chunk size")
            size = int(line[:-2].split(b";", 1)[0], 16)
            if size < 0 or total + size > MAX_BODY:
                raise ValueError("Export exceeds body limit")
            if size == 0:
                trailer_size = 0
                while True:
                    trailer = stream.readline(1024)
                    trailer_size += len(trailer)
                    if not trailer.endswith(b"\r\n") or trailer_size > 8192:
                        raise ValueError("Invalid chunk trailer")
                    if trailer == b"\r\n":
                        return b"".join(chunks)
            chunk = stream.read(size)
            if len(chunk) != size or stream.read(2) != b"\r\n":
                raise ValueError("Truncated chunk")
            chunks.append(chunk)
            total += size
    size = int(length or 0)
    if not 0 <= size <= MAX_BODY:
        raise ValueError("Export exceeds body limit")
    body = stream.read(size)
    if len(body) != size:
        raise ValueError("Truncated export")
    return body


def get(value, key, default=None):
    parts = key.split("_")
    camel = parts[0] + "".join(part.title() for part in parts[1:])
    return value.get(key, value.get(camel, default))


def any_value(value):
    for key in ("string_value", "bool_value", "double_value", "int_value", "bytes_value"):
        raw = get(value, key)
        if raw is not None:
            return int(raw) if key == "int_value" else raw
    if get(value, "array_value") is not None:
        return [any_value(item) for item in get(value, "array_value").get("values", [])]
    if get(value, "kvlist_value") is not None:
        return attrs(get(value, "kvlist_value").get("values", []))
    return None


def attrs(items):
    return {item["key"]: any_value(item.get("value", {})) for item in items}


def decode_export(signal, body, content_type, encoding=""):
    if len(body) > MAX_BODY:
        raise ValueError("Export exceeds body limit")
    if encoding.lower() == "gzip":
        # Read at most one byte beyond the decompressed limit, never the whole
        # expansion of a small compressed payload.
        with gzip.GzipFile(fileobj=BytesIO(body)) as compressed:
            body = compressed.read(MAX_BODY + 1)
        if len(body) > MAX_BODY:
            raise ValueError("Decompressed export exceeds body limit")
    elif encoding:
        raise ValueError("Unsupported OTLP content encoding")
    is_json = "json" in content_type.lower()
    payload = json.loads(body) if is_json else MessageToDict(
        REQUESTS[signal].FromString(body))
    key = {"traces": "resource_spans", "logs": "resource_logs", "metrics": "resource_metrics"}[signal]
    if not isinstance(payload, dict) or not isinstance(get(payload, key, []), list):
        raise ValueError("Invalid OTLP resource groups")

    def identifier(value):
        if not value:
            return ""
        return value.lower() if is_json else base64.b64decode(value).hex()

    result = {"signal": signal, "otlp": payload, "spans": [], "logs": [], "metrics": []}
    scope_key = {"traces": "scope_spans", "logs": "scope_logs", "metrics": "scope_metrics"}[signal]
    for resource_group in get(payload, key, []):
        resource = attrs(resource_group.get("resource", {}).get("attributes", []))
        for scope_group in get(resource_group, scope_key, []):
            scope = scope_group.get("scope", {})
            common = {"resource": resource, "scope_name": scope.get("name", ""), "scope_version": scope.get("version", "")}
            if signal == "traces":
                for span in scope_group.get("spans", []):
                    result["spans"].append({**common, "name": span.get("name", ""),
                        "trace_id": identifier(get(span, "trace_id")), "span_id": identifier(get(span, "span_id")),
                        "parent_span_id": identifier(get(span, "parent_span_id")),
                        "start_time_unix_nano": int(get(span, "start_time_unix_nano", 0)),
                        "end_time_unix_nano": int(get(span, "end_time_unix_nano", 0)),
                        "attributes": attrs(span.get("attributes", [])), "status": span.get("status", {}),
                        "events": [{"name": item.get("name"), "attributes": attrs(item.get("attributes", []))}
                                   for item in span.get("events", [])]})
            elif signal == "logs":
                for log in get(scope_group, "log_records", []):
                    result["logs"].append({**common, "body": any_value(log.get("body", {})),
                        "trace_id": identifier(get(log, "trace_id")), "span_id": identifier(get(log, "span_id")),
                        "attributes": attrs(log.get("attributes", [])),
                        "time_unix_nano": int(get(log, "time_unix_nano", 0))})
            else:
                for metric in scope_group.get("metrics", []):
                    for kind in ("sum", "gauge", "histogram", "exponential_histogram", "summary"):
                        data = get(metric, kind)
                        if data is not None:
                            for point in get(data, "data_points", []):
                                result["metrics"].append({**common, "name": metric.get("name"), "kind": kind,
                                    "attributes": attrs(point.get("attributes", [])), "point": point,
                                    "temporality": get(data, "aggregation_temporality")})
    return result


def response_admitted(signal, status, body, content_type):
    """Success requires the actual signal-specific OTLP response contract."""
    if not 200 <= status < 300 or len(body) > MAX_BODY:
        return False
    media = content_type.split(";", 1)[0].strip().lower()
    try:
        if media == "application/json":
            value = json.loads(body)
            if not isinstance(value, dict):
                return False
            # OTLP success is {} or a partialSuccess message, never an HTTP
            # 200 application error envelope. Reject unknown JSON fields.
            reply = ParseDict(value, RESPONSES[signal](), ignore_unknown_fields=False)
        elif media in ("application/x-protobuf", "application/protobuf"):
            reply = RESPONSES[signal].FromString(body)
        else:
            return False
        if not reply.HasField("partial_success"):
            return True
        partial = reply.partial_success
        rejected = getattr(partial, {"traces": "rejected_spans", "logs": "rejected_log_records",
                                     "metrics": "rejected_data_points"}[signal])
        return rejected == 0 and not partial.error_message
    except (ValueError, TypeError, DecodeError, ParseError):
        return False


class Capture:
    def __init__(self, forward_origin=None, forward_headers=None, *,
                 forward_headers_factory=None, shutdown_timeout=5):
        self.origin = forward_origin.rstrip("/") if forward_origin else None
        self.headers = dict(forward_headers or {})
        self.forward_headers_factory = forward_headers_factory
        self.exports = []
        self.lock = threading.Lock()
        self.workers = {}
        self.closed = False
        self.shutdown_timeout = shutdown_timeout

    def __enter__(self):
        owner = self
        self.client = httpx.Client(timeout=30, trust_env=False)

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def signal(self):
                candidate = self.path.rsplit("/", 1)[-1]
                return candidate if candidate in REQUESTS and self.path == "/v1/" + candidate else "unknown"

            def record(self, export):
                with owner.lock:
                    if not owner.closed:
                        owner.exports.append(export)
                    owner.workers.pop(threading.current_thread(), None)

            def reject(self, signal, status, error_type):
                # Never retain a malformed body, headers, URL, or exception
                # text. A rejected attempt must still fail source admission.
                self.record({"signal": signal, "spans": [], "logs": [], "metrics": [],
                             "admitted": False, "forward_status": None,
                             "capture_status": status, "error_type": error_type})

            def do_POST(self):
                worker = threading.current_thread()
                with owner.lock:
                    if owner.closed:
                        return
                    owner.workers[worker] = {"signal": self.signal(),
                                             "spans": [], "logs": [], "metrics": []}
                try:
                    self.receive()
                finally:
                    with owner.lock:
                        owner.workers.pop(worker, None)

            def receive(self):
                signal = self.signal()
                if signal == "unknown":
                    self.reject(signal, 404, "UnknownSignal")
                    self.send_error(404)
                    return
                content_type = self.headers.get("Content-Type", "application/x-protobuf")
                encoding = self.headers.get("Content-Encoding", "")
                try:
                    self.connection.settimeout(30)
                    body = read_body(self.rfile, self.headers)
                    export = decode_export(signal, body, content_type, encoding)
                    with owner.lock:
                        owner.workers[threading.current_thread()] = export
                except Exception as error:
                    self.reject(signal, 400, type(error).__name__[:64])
                    self.send_error(400, "Invalid OTLP export")
                    return
                status, response_body, response_type = 200, b"{}", "application/json"
                if owner.origin:
                    try:
                        # Resolve credentials only for an actual forward. The
                        # factory may renew transport headers, never OTLP bytes.
                        headers = httpx.Headers(dict(owner.forward_headers_factory()
                            if owner.forward_headers_factory is not None else owner.headers))
                        headers["Content-Type"] = content_type
                        if encoding:
                            headers["Content-Encoding"] = encoding
                        else:
                            headers.pop("Content-Encoding", None)
                        response = owner.client.post(owner.origin + "/v1/" + signal, content=body, headers=headers)
                        status, response_body = response.status_code, response.content
                        response_type = response.headers.get("Content-Type", "application/json")
                    except Exception:
                        # Header renewal errors can contain credentials. Keep
                        # only the same safe failure response as transport errors.
                        status, response_body = 502, b'{"error":"collector forwarding failed"}'
                elif "json" not in content_type.lower():
                    response_body, response_type = b"", "application/x-protobuf"
                export["forward_status"] = status if owner.origin else None
                export["admitted"] = response_admitted(signal, status, response_body, response_type)
                self.record(export)
                self.send_response(status)
                self.send_header("Content-Type", response_type)
                self.send_header("Content-Length", str(len(response_body)))
                self.end_headers()
                self.wfile.write(response_body)

        self.server = LoopbackHTTPServer(Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        return self

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        with self.lock:
            workers = list(self.workers)
        deadline = time.monotonic() + self.shutdown_timeout
        for worker in workers:
            worker.join(timeout=max(0, deadline - time.monotonic()))
        with self.lock:
            self.closed = True
            for export in self.workers.values():
                self.exports.append({**deepcopy(export), "admitted": False,
                                     "forward_status": None, "shutdown_pending": True,
                                     "error": "collector forwarding did not complete before capture shutdown"})
        self.client.close()
