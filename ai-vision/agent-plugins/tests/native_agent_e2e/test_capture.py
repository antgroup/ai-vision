import gzip
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from unittest.mock import Mock, patch
import httpx
import json
import unittest
import threading
from copy import deepcopy
from urllib.request import Request, ProxyHandler, build_opener

from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest, ExportTraceServiceResponse
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceResponse
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest, ExportMetricsServiceResponse
from capture import Capture, decode_export, read_body, MAX_BODY


class ForwardCollector:
    """A real HTTP collector that records received transport bytes."""
    def __enter__(self):
        self.requests = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                owner.requests.append({"path": self.path, "body": body,
                    "authorization": self.headers.get("Authorization"),
                    "content_type": self.headers.get("Content-Type"),
                    "encoding": self.headers.get("Content-Encoding")})
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        return self

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class CaptureTests(unittest.TestCase):
    def test_dynamic_headers_forward_original_bytes_for_every_signal_over_http(self):
        issued = []

        def headers():
            token = "Bearer inert-renewal-" + str(len(issued) + 1)
            issued.append(token)
            return {"Authorization": token, "content-type": "ignored/type",
                "content-encoding": "ignored-encoding"}

        metrics = ExportMetricsServiceRequest()
        metric = metrics.resource_metrics.add().scope_metrics.add().metrics.add(name="agent.tool_call.count")
        metric.sum.data_points.add().as_int = 1
        exports = (
            ("traces", b'{ "resourceSpans" : [] }\n', "application/json", None),
            ("logs", gzip.compress(b'{ "resourceLogs" : [] }\n', mtime=0), "application/json", "gzip"),
            ("metrics", metrics.SerializeToString(), "application/x-protobuf", None),
        )
        with ForwardCollector() as collector, Capture(collector.url,
                {"Authorization": "Bearer inert-expired-static"},
                forward_headers_factory=headers) as capture, httpx.Client(trust_env=False) as client:
            for signal, body, media, encoding in exports:
                transport = {"Content-Type": media}
                if encoding:
                    transport["Content-Encoding"] = encoding
                response = client.post(capture.url + "/v1/" + signal, content=body, headers=transport)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content, b"{}")
            self.assertEqual(len(collector.requests), 3)
            for index, (signal, body, media, encoding) in enumerate(exports):
                received = collector.requests[index]
                self.assertEqual(received["path"], "/v1/" + signal)
                self.assertEqual(received["body"], body)
                self.assertEqual(received["content_type"], media)
                self.assertEqual(received["encoding"], encoding)
                self.assertEqual(received["authorization"], issued[index])
            self.assertEqual(issued, ["Bearer inert-renewal-1", "Bearer inert-renewal-2", "Bearer inert-renewal-3"])
            self.assertTrue(all(export["admitted"] for export in capture.exports))
            self.assertNotIn("inert-", json.dumps(capture.exports))

    def test_static_headers_still_forward_unmodified_body_over_http(self):
        body = b'{ "resourceSpans" : [] }\n'
        original = {"Authorization": "Bearer inert-static"}
        with ForwardCollector() as collector, Capture(collector.url, original) as capture:
            response = httpx.post(capture.url + "/v1/traces", content=body,
                headers={"Content-Type": "application/json"}, trust_env=False)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(collector.requests[0]["body"], body)
            self.assertEqual(collector.requests[0]["authorization"], original["Authorization"])
            self.assertTrue(capture.exports[0]["admitted"])
        self.assertEqual(original, {"Authorization": "Bearer inert-static"})

    def test_header_factory_failure_is_502_not_admitted_and_never_leaks_secrets(self):
        marker = "private-refresh-credential-must-not-be-saved"

        def failing_headers():
            raise RuntimeError(marker)

        for factory in (failing_headers, lambda: None):
            with self.subTest(factory=factory.__name__), ForwardCollector() as collector, Capture(
                    collector.url, {"Authorization": "Bearer " + marker},
                    forward_headers_factory=factory) as capture:
                response = httpx.post(capture.url + "/v1/traces", content=b'{"resourceSpans":[]}',
                    headers={"Content-Type": "application/json"}, trust_env=False)
                self.assertEqual(response.status_code, 502)
                self.assertEqual(collector.requests, [], "a failed refresh cannot forward stale credentials")
                self.assertEqual(len(capture.exports), 1)
                self.assertFalse(capture.exports[0]["admitted"])
                self.assertEqual(capture.exports[0]["forward_status"], 502)
                self.assertNotIn(marker, response.text)
                self.assertNotIn(marker, json.dumps(capture.exports))

    def test_header_factory_only_runs_after_valid_decode_and_before_forwarding(self):
        calls = []

        def headers():
            calls.append(True)
            return {}

        with ForwardCollector() as collector, Capture(collector.url,
                forward_headers_factory=headers) as capture:
            response = httpx.post(capture.url + "/v1/traces", content=b"invalid",
                headers={"Content-Type": "application/json"}, trust_env=False)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(calls, [])
            self.assertEqual(collector.requests, [])
        with Capture(forward_headers_factory=headers) as local:
            response = httpx.post(local.url + "/v1/traces", content=b'{"resourceSpans":[]}',
                headers={"Content-Type": "application/json"}, trust_env=False)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(calls, [])

    def test_pending_forwarding_cannot_disappear_or_mutate_saved_shutdown_evidence(self):
        entered,release=threading.Event(),threading.Event()
        def forward(*args,**kwargs):
            entered.set()
            release.wait(5)
            return httpx.Response(200,json={'partialSuccess':{'rejectedSpans':'1'}})
        def send(url):
            try:
                httpx.post(url+'/v1/traces',json={'resourceSpans':[]},trust_env=False,timeout=6)
            except httpx.HTTPError: pass
        with Capture('http://127.0.0.1:1',shutdown_timeout=.05) as capture:
            capture.client.post=Mock(side_effect=forward)
            caller=threading.Thread(target=send,args=(capture.url,),daemon=True)
            caller.start()
            self.assertTrue(entered.wait(2))
        try:
            saved=deepcopy(capture.exports)
            self.assertEqual(len(saved),1)
            self.assertFalse(saved[0]['admitted'])
            self.assertTrue(saved[0]['shutdown_pending'])
            release.set()
            caller.join(2)
            self.assertFalse(caller.is_alive())
            self.assertEqual(capture.exports,saved)
        finally:
            release.set()
    def test_chunked_reader_rejects_ambiguous_truncated_and_unbounded_bodies(self):
        for body, headers in (
            (b"0\r\n\r\n", {"Transfer-Encoding": "chunked", "Content-Length": "0"}),
            (b"5\r\nabc", {"Transfer-Encoding": "chunked"}),
            (f"{MAX_BODY + 1:x}\r\n".encode(), {"Transfer-Encoding": "chunked"}),
        ):
            with self.subTest(headers=headers, body=body[:20]), self.assertRaises(ValueError):
                read_body(BytesIO(body), headers)

    def test_native_node_chunked_export_is_decoded_without_content_length(self):
        with Capture() as capture:
            from urllib.parse import urlparse
            url = urlparse(capture.url)
            client = http.client.HTTPConnection(url.hostname, url.port, timeout=5)
            body = b'{"resourceSpans":[]}'
            client.request("POST", "/v1/traces", body=iter([body[:5], body[5:]]),
                           headers={"Content-Type": "application/json"}, encode_chunked=True)
            response = client.getresponse()
            self.assertEqual(response.status, 200, response.read().decode())
            client.close()
            self.assertEqual(len(capture.exports), 1)

    def test_json_and_gzip_protobuf_preserve_ids_attributes_and_scope(self):
        request = ExportTraceServiceRequest()
        group = request.resource_spans.add()
        group.resource.attributes.add(key="ant.agent.type").value.string_value = "pi"
        scope = group.scope_spans.add()
        scope.scope.name = "observ-pi"
        span = scope.spans.add(name="LLM", trace_id=bytes.fromhex("aa" * 16), span_id=bytes.fromhex("bb" * 8))
        span.attributes.add(key="gen_ai.usage.input_tokens").value.int_value = 0
        decoded = decode_export("traces", gzip.compress(request.SerializeToString()), "application/x-protobuf", "gzip")
        self.assertEqual(decoded["spans"][0]["trace_id"], "aa" * 16)
        self.assertEqual(decoded["spans"][0]["attributes"]["gen_ai.usage.input_tokens"], 0)
        self.assertEqual(decoded["spans"][0]["scope_name"], "observ-pi")
        body = {"resourceSpans": [{"resource": {"attributes": [{"key": "ant.agent.type", "value": {"stringValue": "pi"}}]},
                "scopeSpans": [{"scope": {"name": "observ-pi"}, "spans": [{
                    "name": "LLM", "traceId": "aa" * 16, "spanId": "bb" * 8,
                    "attributes": [{"key": "gen_ai.usage.input_tokens", "value": {"intValue": "0"}}],
                }]}]}]}
        other = decode_export("traces", json.dumps(body).encode(), "application/json")
        for key in ("trace_id", "span_id", "scope_name", "attributes", "resource"):
            self.assertEqual(decoded["spans"][0][key], other["spans"][0][key])

    def test_collector_rejects_invalid_body_and_captures_all_three_signals(self):
        with Capture() as capture:
            opener = build_opener(ProxyHandler({}))
            for signal, group in (("traces", "resourceSpans"), ("logs", "resourceLogs"), ("metrics", "resourceMetrics")):
                request = Request(capture.url + "/v1/" + signal, json.dumps({group: []}).encode(),
                                  {"Content-Type": "application/json"})
                with opener.open(request, timeout=5) as response:
                    self.assertEqual(response.status, 200)
            self.assertEqual({row["signal"] for row in capture.exports}, {"traces", "logs", "metrics"})
            from urllib.error import HTTPError
            with self.assertRaises(HTTPError) as raised:
                opener.open(Request(capture.url + "/v1/traces", b"not-json", {"Content-Type": "application/json"}))
            self.assertEqual(raised.exception.code, 400)
            self.assertEqual(len(capture.exports), 4)
            self.assertEqual(capture.exports[-1]['signal'], 'traces')
            self.assertFalse(capture.exports[-1]['admitted'])
            self.assertEqual(capture.exports[-1]['capture_status'], 400)
            self.assertEqual(capture.exports[-1]['error_type'], 'JSONDecodeError')

    def test_rejected_decode_framing_and_unknown_signal_keep_only_safe_failure_facts(self):
        marker='private-request-content-must-not-be-saved'
        cases=(('/v1/logs', marker.encode(), {}, 400, 'logs', 'JSONDecodeError'),
               ('/v1/metrics', b'{}', {'Content-Length': str(MAX_BODY+1)}, 400, 'metrics', 'ValueError'),
               ('/v1/'+marker, marker.encode(), {}, 404, 'unknown', 'UnknownSignal'))
        for path,body,extra,status,signal,error_type in cases:
            with self.subTest(signal=signal), Capture() as capture:
                from urllib.parse import urlparse
                url=urlparse(capture.url)
                client=http.client.HTTPConnection(url.hostname,url.port,timeout=5)
                client.request('POST',path,body=body,headers={
                    'Content-Type':'application/json','Authorization':'Bearer '+marker,
                    'Cookie':'private='+marker,**extra})
                response=client.getresponse()
                self.assertEqual(response.status,status)
                response.read();client.close()
            self.assertEqual(len(capture.exports),1)
            failure=capture.exports[0]
            self.assertEqual(failure['signal'],signal)
            self.assertEqual(failure['capture_status'],status)
            self.assertEqual(failure['error_type'],error_type)
            self.assertFalse(failure['admitted'])
            self.assertEqual(set(failure),{'signal','spans','logs','metrics','admitted',
                                           'forward_status','capture_status','error_type'})
            self.assertNotIn(marker,json.dumps(failure))
            self.assertLess(len(json.dumps(failure)),512)

    def test_late_decode_rejection_cannot_duplicate_or_mutate_shutdown_evidence(self):
        entered,release=threading.Event(),threading.Event()
        def pending_read(*args,**kwargs):
            entered.set()
            if not release.wait(5): raise AssertionError('test did not release reader')
            raise ValueError('private body must never appear in proof')
        def send(url):
            try: httpx.post(url+'/v1/traces',content=b'private',trust_env=False,timeout=6)
            except httpx.HTTPError: pass
        try:
            with patch('capture.read_body',side_effect=pending_read):
                with Capture(shutdown_timeout=.05) as capture:
                    caller=threading.Thread(target=send,args=(capture.url,),daemon=True)
                    caller.start()
                    self.assertTrue(entered.wait(2))
                saved=deepcopy(capture.exports)
                self.assertEqual(len(saved),1)
                self.assertFalse(saved[0]['admitted'])
                self.assertTrue(saved[0]['shutdown_pending'])
                release.set();caller.join(2)
                self.assertFalse(caller.is_alive())
                self.assertEqual(capture.exports,saved)
                self.assertNotIn('private body',json.dumps(capture.exports))
        finally:
            release.set()

    def test_rejected_request_cannot_disappear_from_an_otherwise_valid_source_proof(self):
        from test_verify import proof
        from verify import verify_case
        value=proof()
        self.assertTrue(verify_case(**value)['verified'])
        with Capture() as capture:
            response=httpx.post(capture.url+'/v1/traces',content=b'not-json',
                                headers={'Content-Type':'application/json'},trust_env=False,timeout=5)
            self.assertEqual(response.status_code,400)
        value['exports'].extend(deepcopy(capture.exports))
        result=verify_case(**value)
        self.assertFalse(result['verified'])
        self.assertIn('ingress.admitted',result['failures'])

    def forwarded(self, signal, content, content_type):
        with Capture("http://127.0.0.1:1") as capture:
            capture.client.post = Mock(return_value=httpx.Response(200, content=content,
                            headers={"Content-Type": content_type}))
            group = {"traces": "resourceSpans", "logs": "resourceLogs", "metrics": "resourceMetrics"}[signal]
            request = Request(capture.url + "/v1/" + signal, json.dumps({group: []}).encode(),
                              {"Content-Type": "application/json"})
            with build_opener(ProxyHandler({})).open(request, timeout=5) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.read(), content, "forwarding keeps the actual collector reply")
            return capture.exports[0]

    def test_protobuf_partial_success_is_not_admission_for_each_signal(self):
        for signal, cls, count in (("traces", ExportTraceServiceResponse, "rejected_spans"),
                                   ("logs", ExportLogsServiceResponse, "rejected_log_records"),
                                   ("metrics", ExportMetricsServiceResponse, "rejected_data_points")):
            with self.subTest(signal=signal):
                reply = cls()
                setattr(reply.partial_success, count, 1)
                self.assertFalse(self.forwarded(signal, reply.SerializeToString(), "application/x-protobuf")["admitted"])
                reply.partial_success.Clear()
                self.assertTrue(self.forwarded(signal, reply.SerializeToString(), "application/x-protobuf")["admitted"])
                reply.partial_success.error_message = "partial failure"
                self.assertFalse(self.forwarded(signal, reply.SerializeToString(), "application/x-protobuf")["admitted"])

    def test_http_200_json_error_is_not_admission(self):
        for reply in ({"error": "denied"}, {"status": "error"}, {"code": 13, "message": "error"},
                      {"partialSuccess": {"rejectedSpans": "1"}}, [], None):
            with self.subTest(reply=reply):
                self.assertFalse(self.forwarded("traces", json.dumps(reply).encode(), "application/json")["admitted"])
        self.assertTrue(self.forwarded("traces", b'{}', "application/json")["admitted"])
        self.assertTrue(self.forwarded("traces", b'{"partialSuccess":{"rejectedSpans":"0","errorMessage":""}}', "application/json")["admitted"])

    def test_gzip_expansion_and_unknown_encoding_are_bounded(self):
        compressed = gzip.compress(b" " * (MAX_BODY + 1))
        self.assertLess(len(compressed), MAX_BODY)
        with self.assertRaisesRegex(ValueError, "limit"):
            decode_export("traces", compressed, "application/json", "gzip")
        with self.assertRaisesRegex(ValueError, "encoding"):
            decode_export("traces", b'{"resourceSpans":[]}', "application/json", "br")


if __name__ == "__main__":
    unittest.main()
