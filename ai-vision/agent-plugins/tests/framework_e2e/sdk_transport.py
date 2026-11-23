"""Capture native SDK protobuf and forward the original body to AI Vision.

Constructing/importing this module does not start a listener or contact a server.
Headers are used only in memory; evidence contains payloads, IDs and HTTP status.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socketserver import TCPServer
from typing import Any

import httpx
from google.protobuf.json_format import MessageToDict
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest, ExportTraceServiceResponse

import re
import time
from pathlib import Path
_PRIVATE = set()

def remember_private(value):
    if isinstance(value, str) and value:
        _PRIVATE.add(value)

def safe(value):
    if isinstance(value, dict):
        return {str(k): ('[REDACTED]' if re.search(r'authorization|secret|password|space_key|api_key', str(k), re.I) else safe(v)) for k,v in value.items()}
    if isinstance(value, (list, tuple)):
        return [safe(v) for v in value]
    if isinstance(value, str):
        for secret in sorted(_PRIVATE, key=len, reverse=True):
            value = value.replace(secret, '[REDACTED]')
        return value
    if isinstance(value, (int,float,bool)) or value is None:
        return value
    return safe(str(value))

def write_json(path, value):
    path=Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(safe(value),ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')

class Ledger:
    def __init__(self,path):
        self.path=Path(path); self.path.parent.mkdir(parents=True, exist_ok=True)
        self.rows=[]; self.lock=threading.Lock()
    def emit(self,kind,**fields):
        row=safe({'kind':kind,'timestamp_ns':time.time_ns(),**fields})
        with self.lock:
            self.rows.append(row)
            with self.path.open('a',encoding='utf-8') as f:
                f.write(json.dumps(row,ensure_ascii=False,allow_nan=False)+'\n')

TRACES_PATH = "/api/public/otel/v1/traces"


def any_value(value) -> Any:
    field = value.WhichOneof("value")
    if field == "array_value":
        return [any_value(item) for item in value.array_value.values]
    if field == "kvlist_value":
        return {item.key: any_value(item.value) for item in value.kvlist_value.values}
    if field == "bytes_value":
        return value.bytes_value.hex()
    return getattr(value, field) if field else None


def attrs(items) -> dict[str, Any]:
    return {item.key: any_value(item.value) for item in items}


def response_summary(payload: bytes, content_type: str) -> dict[str, Any]:
    """Record OTLP admission counters, never an upstream message/header/body."""
    result = {"response_content_type": content_type, "otlp_response_decoded": False}
    try:
        if "json" in content_type.lower() or payload.lstrip().startswith(b"{"):
            value = json.loads(payload)
            if not isinstance(value, dict):
                return result
            if not set(value).issubset({"partialSuccess", "partial_success"}):
                result["non_OTLP_application_error_present"] = bool(value.get("error") or value.get("code"))
                return result
            partial = value.get("partialSuccess", value.get("partial_success", {}))
            if not isinstance(partial, dict):
                return result
            result.update(otlp_response_decoded=True,
                          otlp_rejected_spans=int(partial.get("rejectedSpans", partial.get("rejected_spans", 0))),
                          otlp_error_message_present=bool(partial.get("errorMessage", partial.get("error_message"))))
        else:
            response = ExportTraceServiceResponse.FromString(payload)
            result.update(otlp_response_decoded=True,
                          otlp_rejected_spans=response.partial_success.rejected_spans,
                          otlp_error_message_present=bool(response.partial_success.error_message))
    except (ValueError, TypeError):
        pass
    except Exception:
        pass
    return result


def decode_spans(body: bytes, encoding: str = "") -> dict[str, Any]:
    decoded = gzip.decompress(body) if encoding.lower() == "gzip" else body
    request = ExportTraceServiceRequest.FromString(decoded)
    spans = []
    for resource_spans in request.resource_spans:
        resource = attrs(resource_spans.resource.attributes)
        for scope_spans in resource_spans.scope_spans:
            for span in scope_spans.spans:
                spans.append({
                    "trace_id": span.trace_id.hex(), "span_id": span.span_id.hex(),
                    "parent_span_id": span.parent_span_id.hex(), "name": span.name,
                    "kind": span.kind, "start_time_unix_nano": span.start_time_unix_nano,
                    "end_time_unix_nano": span.end_time_unix_nano,
                    "resource": resource, "scope_name": scope_spans.scope.name,
                    "scope_version": scope_spans.scope.version,
                    "attributes": attrs(span.attributes),
                    "status": MessageToDict(span.status, preserving_proto_field_name=True),
                    "events": [{"name": event.name, "time_unix_nano": event.time_unix_nano,
                                "attributes": attrs(event.attributes)} for event in span.events],
                })
    return {"spans": spans, "protobuf": MessageToDict(request, preserving_proto_field_name=True)}


class LoopbackHTTPServer(ThreadingHTTPServer):
    """Bind the local fixture without a reverse DNS lookup."""

    def __init__(self, handler: type[BaseHTTPRequestHandler]) -> None:
        super().__init__(("127.0.0.1", 0), handler)

    def server_bind(self) -> None:
        TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address


class CaptureForwarder:
    def __init__(self, platform_origin: str | None, ledger: Ledger):
        self.platform_origin = platform_origin.rstrip("/") if platform_origin else None
        self.ledger = ledger
        self.exports: list[dict[str, Any]] = []
        self.lock = threading.Lock()
        self.server: LoopbackHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.client: httpx.Client | None = None

    def start(self) -> str:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass  # Base handler's stderr must not contain request/header data.

            def do_POST(self):
                if self.path != TRACES_PATH:
                    self.send_error(404)
                    return
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 32 * 1024 * 1024:
                    self.send_error(413)
                    return
                body = self.rfile.read(length)
                auth = self.headers.get("Authorization", "")
                remember_private(auth)
                entry: dict[str, Any] = {
                    "wire_sha256": hashlib.sha256(body).hexdigest(),
                    "wire_bytes": len(body), "path": TRACES_PATH,
                    "content_encoding": self.headers.get("Content-Encoding", "identity"),
                    "content_type": self.headers.get("Content-Type"),
                    "sdk_version": self.headers.get("x-langfuse-sdk-version"),
                    "ingestion_version": self.headers.get("x-langfuse-ingestion-version"),
                }
                try:
                    entry.update(decode_spans(body, self.headers.get("Content-Encoding", "")))
                    headers = {name: value for name, value in self.headers.items()
                               if name.lower() in {"authorization", "content-type", "content-encoding",
                                                   "x-langfuse-sdk-name", "x-langfuse-sdk-version",
                                                   "x-langfuse-public-key", "x-langfuse-ingestion-version"}}
                    assert owner.client is not None
                    if owner.platform_origin:
                        response = owner.client.post(owner.platform_origin + TRACES_PATH,
                                                     content=body, headers=headers)
                        status, payload = response.status_code, response.content
                        content_type = response.headers.get("Content-Type", "application/x-protobuf")
                    else:
                        status, payload, content_type = 200, ExportTraceServiceResponse().SerializeToString(), "application/x-protobuf"
                    # Save only status and body length; an upstream error body may echo credentials.
                    entry.update(http_status=status, response_bytes=len(payload))
                    entry.update(response_summary(payload, content_type))
                except Exception as error:
                    entry.update(http_status=502, error_type=type(error).__name__)
                    status, payload, content_type = 502, b"capture forward failed", "text/plain"
                with owner.lock:
                    owner.exports.append(safe(entry))
                owner.ledger.emit("sdk_export", **entry)
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self.client = httpx.Client(timeout=30, trust_env=False)
        self.server = LoopbackHTTPServer(Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return f"http://127.0.0.1:{self.server.server_port}"

    def close(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
        if self.thread is not None:
            self.thread.join(timeout=5)
        if self.client is not None:
            self.client.close()

    def summary(self) -> dict[str, Any]:
        with self.lock:
            exports = list(self.exports)
        trace_ids = sorted({span["trace_id"] for export in exports for span in export.get("spans", [])})
        return {"transport": "native SDK HTTP/protobuf -> loopback capture" + (" -> platform" if self.platform_origin else ""),
                "mode": "platform" if self.platform_origin else "collector-only",
                "export_requests": len(exports), "http_statuses": [row["http_status"] for row in exports],
                "captured_spans": sum(len(row.get("spans", [])) for row in exports),
                "trace_ids": trace_ids,
                "ingestion_http_accepted": bool(exports) and all(200 <= row["http_status"] < 300 and row.get("otlp_response_decoded") and row.get("otlp_rejected_spans", 0) == 0 and not row.get("otlp_error_message_present") for row in exports),
                "platform_query_verified": False}
