"""Real HTTP contracts for the diagnostic Responses/Chat carrier."""
import importlib
from http.server import BaseHTTPRequestHandler
import json
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, ProxyHandler, build_opener

from loopback_http import LoopbackHTTPServer


class CodexProviderTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.message = {"role": "assistant", "content": " receipt: actual\n"}
        self.usage = {"prompt_tokens": 16, "completion_tokens": 4, "total_tokens": 20,
                      "prompt_tokens_details": {"cached_tokens": 3, "cache_write_tokens": 2},
                      "completion_tokens_details": {"reasoning_tokens": 1}}
        self.status = 200
        self.stream_done = True
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                owner.requests.append((self.path, body, self.headers.get("Authorization")))
                self.send_response(owner.status)
                self.send_header("Content-Type", "text/event-stream" if body.get("stream") and owner.status == 200 else "application/json")
                self.send_header("Connection", "close")
                self.end_headers()
                if owner.status != 200:
                    self.wfile.write(b'{"error":{"message":"actual failure","type":"rate_limit_error"}}')
                elif body.get("stream"):
                    def send(delta=None, finish=None, usage=None):
                        row = {"id": "chat_real", "model": body["model"], "choices": [] if usage is not None else [{"index": 0, "delta": delta or {}, "finish_reason": finish}]}
                        if usage is not None: row["usage"] = usage
                        self.wfile.write(("data: " + json.dumps(row) + "\n\n").encode())
                        self.wfile.flush()
                    send({"role": "assistant"})
                    if owner.message.get("content"):
                        send({"content": owner.message["content"][:5]})
                        send({"content": owner.message["content"][5:]})
                    for index, call in enumerate(owner.message.get("tool_calls", [])):
                        send({"tool_calls": [{"index": index, "id": call["id"], "type": "function", "function": {"name": call["function"]["name"], "arguments": ""}}]})
                        send({"tool_calls": [{"index": index, "function": {"arguments": call["function"]["arguments"]}}]})
                    send(finish="tool_calls" if owner.message.get("tool_calls") else "stop")
                    if owner.usage is not None: send(usage=owner.usage)
                    if owner.stream_done: self.wfile.write(b"data: [DONE]\n\n")
                else:
                    row = {"id": "chat_real", "model": body["model"], "choices": [{"index": 0, "message": owner.message, "finish_reason": "stop"}]}
                    if owner.usage is not None: row["usage"] = owner.usage
                    self.wfile.write(json.dumps(row).encode())
                self.close_connection = True
        self.server = LoopbackHTTPServer(Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join, 2)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def carrier(self, **options):
        try: module = importlib.import_module("codex_provider")
        except ModuleNotFoundError: self.fail("diagnostic Codex requires a Responses/Chat carrier")
        return module.CodexProvider(f"http://127.0.0.1:{self.server.server_port}/v1", timeout=5, **options)

    def post(self, provider, body):
        request = Request(provider.url + "/responses", json.dumps(body).encode(),
                          {"Content-Type": "application/json", "Authorization": "Bearer private-native-placeholder"})
        with build_opener(ProxyHandler({})).open(request, timeout=5) as response:
            text = response.read().decode()
        return ([json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")] if body.get("stream") else json.loads(text))

    def test_native_messages_and_tools_forward_to_real_chat_http_without_credentials(self):
        body = {"model": "diagnostic-model", "instructions": "Inspect the actual file", "input": [
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Read receipt.txt"}]},
            {"type": "function_call", "call_id": "call_1", "name": "exec_command", "arguments": '{"cmd":"cat receipt.txt"}'},
            {"type": "function_call_output", "call_id": "call_1", "output": "receipt: actual"}],
            "tools": [{"type": "function", "name": "exec_command", "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}}}}]}
        with self.carrier() as carrier:
            response = self.post(carrier, body)
            self.assertEqual(response["output"][0]["content"][0]["text"], " receipt: actual\n")
            self.assertEqual(carrier.requests[0]["request"], body)
            self.assertEqual(carrier.requests[0]["response"], self.message)
            self.assertEqual(carrier.requests[0]["usage"], self.usage)
        path, forwarded, authorization = self.requests[0]
        self.assertEqual(path, "/v1/chat/completions")
        self.assertIsNone(authorization)
        self.assertEqual(forwarded["messages"], [
            {"role": "system", "content": "Inspect the actual file"},
            {"role": "user", "content": "Read receipt.txt"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "exec_command", "arguments": '{"cmd":"cat receipt.txt"}'}}]},
            {"role": "tool", "tool_call_id": "call_1", "content": "receipt: actual"}])
        self.assertEqual(forwarded["tools"][0]["function"]["name"], "exec_command")

    def test_native_reasoning_effort_is_forwarded_without_changing_content(self):
        for streaming in (False, True):
            with self.subTest(streaming=streaming), self.carrier() as carrier:
                native = {"model": "diagnostic-model", "input": "Read the original request", "stream": streaming,
                          "reasoning": {"effort": "low", "summary": "auto"}}
                self.post(carrier, native)
                self.assertEqual(self.requests[-1][1]["reasoning_effort"], "low")
                self.assertEqual(self.requests[-1][1]["messages"],
                                 [{"role": "user", "content": "Read the original request"}])
                self.assertEqual(carrier.requests[0]["request"], native)
                self.assertEqual(carrier.requests[0]["chat_request"]["reasoning_effort"], "low")

    def test_explicit_default_max_tokens_fills_only_a_missing_native_limit(self):
        for streaming in (False, True):
            with self.subTest(streaming=streaming), self.carrier(default_max_tokens=4096) as carrier:
                native = {"model": "diagnostic-model", "input": "Read", "stream": streaming}
                self.post(carrier, native)
                self.assertEqual(self.requests[-1][1]["max_tokens"], 4096)
                self.assertEqual(carrier.requests[0]["request"], native)
                self.assertNotIn("max_output_tokens", native)
                self.assertEqual(carrier.requests[0]["chat_request"]["max_tokens"], 4096)
                explicit = {**native, "max_output_tokens": 1024}
                self.post(carrier, explicit)
                self.assertEqual(self.requests[-1][1]["max_tokens"], 1024)
                self.assertEqual(carrier.requests[1]["request"], explicit)

        with self.carrier() as carrier:
            self.post(carrier, {"model": "diagnostic-model", "input": "Read"})
            self.assertNotIn("max_tokens", self.requests[-1][1])

    def test_invalid_explicit_default_max_tokens_are_rejected(self):
        for value in (True, 0, -1, 1.5, "4096"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.carrier(default_max_tokens=value)

    def test_omitted_or_null_reasoning_effort_keeps_provider_default(self):
        for reasoning in (None, {}, {"summary": "auto"}, {"effort": None}):
            with self.subTest(reasoning=reasoning), self.carrier() as carrier:
                self.post(carrier, {"model": "diagnostic-model", "input": "Read", "reasoning": reasoning})
                self.assertNotIn("reasoning_effort", self.requests[-1][1])

    def test_invalid_native_reasoning_is_rejected_before_forwarding(self):
        for reasoning in ("low", [], {"effort": True}, {"effort": 3}, {"effort": "invalid"}):
            with self.subTest(reasoning=reasoning), self.carrier() as carrier:
                before = len(self.requests)
                with self.assertRaises(HTTPError) as error:
                    self.post(carrier, {"model": "diagnostic-model", "input": "Read", "reasoning": reasoning})
                self.assertEqual(error.exception.code, 422)
                self.assertEqual(len(self.requests), before)

    def test_cancel_readiness_requires_an_actual_upstream_request(self):
        with self.carrier() as carrier:
            self.assertFalse(carrier.request_started.is_set())
            with self.assertRaises(HTTPError):
                self.post(carrier, {"model":"diagnostic-model", "input":[], "tools":[{"type":"unknown"}]})
            self.assertFalse(carrier.request_started.is_set())
            self.assertEqual(self.requests, [])
            self.post(carrier, {"model":"diagnostic-model", "input":"Read"})
            self.assertTrue(carrier.request_started.is_set())
            self.assertEqual(len(self.requests), 1)
            self.assertGreaterEqual(carrier.requests[-1]["upstream_started_ns"], carrier.requests[-1]["http_started_ns"])

    def test_streamed_tool_call_preserves_arguments_and_reported_cache_reasoning_usage(self):
        self.message = {"role": "assistant", "content": None, "tool_calls": [{"id": "call_read", "type": "function", "function": {"name": "exec_command", "arguments": '{"cmd":"cat receipt.txt","workdir":"/tmp/fixture"}'}}]}
        with self.carrier() as carrier:
            events = self.post(carrier, {"model": "diagnostic-model", "input": "Read", "stream": True})
            item = next(row["item"] for row in events if row["type"] == "response.output_item.done")
            self.assertEqual((item["type"], item["call_id"], item["name"]), ("function_call", "call_read", "exec_command"))
            self.assertEqual(item["arguments"], self.message["tool_calls"][0]["function"]["arguments"])
            self.assertEqual(events[-1]["type"], "response.completed")
            self.assertEqual(events[-1]["response"]["usage"], {"input_tokens": 16, "output_tokens": 4, "total_tokens": 20, "input_tokens_details": {"cached_tokens": 3, "cache_write_tokens": 2}, "output_tokens_details": {"reasoning_tokens": 1}})
            self.assertTrue(carrier.requests[0]["stream_done"])

    def test_absent_usage_stays_absent_on_json_and_sse(self):
        self.usage = None
        with self.carrier() as carrier:
            response = self.post(carrier, {"model": "diagnostic-model", "input": "Read"})
            self.assertNotIn("usage", response)
            events = self.post(carrier, {"model": "diagnostic-model", "input": "Read", "stream": True})
            self.assertNotIn("usage", events[-1]["response"])
            self.assertFalse(carrier.requests[-1]["usage_reported"])

    def test_stream_text_is_exact_and_custom_patch_roundtrips(self):
        patch = "*** Begin Patch\n*** End Patch"
        self.message = {"role": "assistant", "content": " exact \n", "tool_calls": [{"id": "patch_1", "type": "function", "function": {"name": "apply_patch", "arguments": json.dumps({"input": patch})}}]}
        with self.carrier() as carrier:
            events = self.post(carrier, {"model": "diagnostic-model", "input": "Edit", "stream": True, "tools": [{"type": "custom", "name": "apply_patch", "description": "Patch", "format": {"type": "grammar", "syntax": "lark", "definition": "irrelevant"}}]})
            text = "".join(row["delta"] for row in events if row["type"] == "response.output_text.delta")
            self.assertEqual(text, " exact \n")
            item = next(row["item"] for row in events if row["type"] == "response.output_item.done" and row["item"]["type"] == "custom_tool_call")
            self.assertEqual(item["input"], patch)

    def test_actual_upstream_error_is_forwarded_without_completion_or_usage(self):
        self.status = 429
        with self.carrier() as carrier:
            with self.assertRaises(HTTPError) as error:
                self.post(carrier, {"model": "diagnostic-model", "input": "Read", "stream": True})
            self.assertEqual(error.exception.code, 429)
            self.assertIn("actual failure", error.exception.read().decode())
            self.assertFalse(carrier.requests[0]["usage_reported"])
            self.assertEqual(carrier.requests[0]["status"], 429)

    def test_explicit_native_tool_choice_preserves_selected_chat_function(self):
        with self.carrier() as carrier:
            self.post(carrier, {"model":"diagnostic-model","input":"Read","tool_choice":{"type":"function","name":"exec_command"}})
        self.assertEqual(self.requests[0][1]["tool_choice"], {"type":"function","function":{"name":"exec_command"}})

    def test_unsupported_tool_choice_is_rejected_before_sampling(self):
        with self.carrier() as carrier:
            for choice in ("unknown", 7, {"type":"custom", "name":"unsupported"}):
                with self.subTest(choice=choice):
                    with self.assertRaises(HTTPError) as error:
                        self.post(carrier, {"model":"diagnostic-model", "input":"Read", "tool_choice":choice})
                    self.assertEqual(error.exception.code, 422)
            self.assertEqual(self.requests, [])

    def test_unsupported_native_content_is_rejected_before_chat_forward(self):
        with self.carrier() as carrier:
            with self.assertRaises(HTTPError) as error:
                self.post(carrier, {"model": "diagnostic-model", "input": [{"type": "computer_call", "call_id": "unsafe"}]})
            self.assertEqual(error.exception.code, 422)
            self.assertEqual(self.requests, [])

    def test_remote_carrier_target_is_rejected(self):
        module = importlib.import_module("codex_provider")
        with self.assertRaises(ValueError): module.CodexProvider("https://example.com/v1")

    def test_incomplete_chat_stream_never_emits_successful_native_completion(self):
        self.stream_done = False
        with self.carrier() as carrier:
            events = self.post(carrier, {"model":"diagnostic-model","input":"Read","stream":True})
            self.assertFalse(any(row["type"] == "response.completed" for row in events))
            self.assertFalse(carrier.requests[0]["completed"])
            self.assertEqual(carrier.requests[0]["error"], "incomplete Chat stream")


if __name__ == "__main__": unittest.main()
