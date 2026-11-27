"""Loopback OpenAI transport backed by LangChain's fake chat model.

The model only returns a receipt observed in a native tool result. It never
reads the fixture file, so a broken tool loop cannot accidentally pass.
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler
from pathlib import Path
import re
import threading
import time

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from loopback_http import LoopbackHTTPServer


# Hermes's official output-cap continuation user message.
HERMES_LENGTH_CONTINUATION_PROMPT = (
    '[System: Your previous response was truncated by the output length limit. '
    'Continue exactly where you left off. Do not restart or repeat prior text. '
    'Finish the answer directly.]'
)
LENGTH_PARTIAL_CONTENT = 'Preparing the receipt.'


def _content(message):
    value = message.get("content", "")
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _tool_results(messages, *, continuation=False):
    boundary = len(messages)
    if continuation:
        if (len(messages) < 2 or messages[-1].get('role') != 'user'
            or _content(messages[-1]) != HERMES_LENGTH_CONTINUATION_PROMPT
            or messages[-2].get('role') != 'assistant'
            or _content(messages[-2]) != LENGTH_PARTIAL_CONTENT):
            raise ValueError('Expected the official Hermes length continuation and original partial reply')
        boundary -= 2
    user_index = max((i for i, item in enumerate(messages[:boundary]) if item.get('role') == 'user'), default=-1)
    return [item for item in messages[user_index + 1:boundary] if item.get('role') == 'tool']


def _receipt(results):
    return re.search(r'receipt:\s*([A-Za-z0-9_-]+)', '\n'.join(_content(item) for item in results))


def _read_call(tools, path, number):
    for tool in tools:
        function = tool.get("function", tool)
        name = function.get("name", "")
        parameters = function.get("parameters", {})
        properties = parameters.get("properties", {})
        if name in ("read", "read_file", "file_read", "Read"):
            key = next((key for key in ("filePath", "path", "file_path")
                        if isinstance(properties.get(key), dict) and properties[key].get('type') == 'string'), None)
            required = parameters.get('required', [])
            if key and isinstance(required, list) and not set(required) - {key}:
                return {"id": f"call_native_{number}", "name": name,
                        "args": {key: str(path)}, "type": "tool_call"}
    raise ValueError("Native request did not expose a supported file read tool")


def reply_for(body, path, scenario, number, *, truncate=False, continuation=False):
    messages = body.get("messages", [])
    results = _tool_results(messages, continuation=continuation)
    if continuation and not results:
        raise ValueError('Length continuation has no original actual tool result')
    if results:
        receipt = _receipt(results)
        if receipt:
            response = (AIMessage(content=LENGTH_PARTIAL_CONTENT,
                additional_kwargs={'reasoning_content': 'The actual tool result supplies the receipt.'},
                response_metadata={'finish_reason': 'length'}) if truncate else
                AIMessage(content="receipt: " + receipt.group(1)))
        elif scenario == "tool-error":
            response = AIMessage(content="Recover by reading the existing receipt.", tool_calls=[
                _read_call(body.get("tools", []), path, number),
            ])
        else:
            raise ValueError("Native tool result did not contain the receipt")
    else:
        target = path.with_name("missing-receipt.txt") if scenario == "tool-error" else path
        response = (AIMessage(content="Native E2E session") if not body.get("tools") else
                    AIMessage(content="", tool_calls=[_read_call(body.get("tools", []), target, number)]))
    # Exercise the actual fake model boundary; native apps consume HTTP/SSE,
    # rather than being replaced by a LangChain agent.
    return GenericFakeChatModel(messages=iter([response])).invoke("native acceptance")


def usage_for(scenario):
    if scenario in ("unknown-usage", "cancel"):
        return None
    if scenario == "zero-usage":
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    usage = {"prompt_tokens": 16, "completion_tokens": 4, "total_tokens": 20}
    if scenario == "cache-usage":
        usage.update(prompt_tokens_details={"cached_tokens": 3, "cache_write_tokens": 2},
                     completion_tokens_details={"reasoning_tokens": 1})
    return usage


class FakeProvider:
    def __init__(self, receipt_path: Path, scenario="tool-loop", *, length_continuation=False,
                 request_sized_usage=False):
        if length_continuation and scenario != 'multi-turn':
            raise ValueError('Length continuation belongs only to the multi-turn fixture')
        self.path, self.scenario = receipt_path, scenario
        self.length_continuation = length_continuation
        self.request_sized_usage = request_sized_usage
        self.length_sent = False
        self.length_pending = False
        self.retry_sent = False
        self.requests = []
        self.lock = threading.Lock()
        self.stop = threading.Event()

    def reply(self, body, number, *, truncate=False, continuation=False):
        return reply_for(body, self.path, self.scenario, number, truncate=truncate, continuation=continuation)

    def usage(self, row):
        scenario = "tool-loop" if self.scenario == "cancel" and row["purpose"] == "auxiliary" else self.scenario
        return usage_for(scenario)

    def __enter__(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                if self.path.rstrip("/") not in ("/v1/models", "/models"):
                    self.send_error(404)
                    return
                self.send_json({"object": "list", "data": [{"id": "native-e2e-chat", "object": "model"}]})

            def send_json(self, value, *, status=200, retry=False):
                encoded = json.dumps(value).encode()
                self.send_response(status)
                if retry:
                    self.send_header("Retry-After", "0")
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def do_POST(self):
                if self.path.rstrip("/") not in ("/v1/chat/completions", "/chat/completions"):
                    self.send_error(404)
                    return
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
                with owner.lock:
                    number = len(owner.requests) + 1
                    row = {"number": number, "request": body, "usage_reported": False,
                           "purpose": "agent" if body.get("tools") else "auxiliary"}
                    owner.requests.append(row)
                    retry = owner.scenario == 'api-retry' and row['purpose'] == 'agent' and not owner.retry_sent
                    if retry:
                        owner.retry_sent = True
                if retry:
                    # This is a real completed HTTP failure, never an observer
                    # callback. The official runtime must issue the next call.
                    error_response = {'error': {'message': 'Native fixture recoverable provider failure', 'type': 'rate_limit_error'}}
                    row.update(response=error_response, usage=None, status=429, completed=True)
                    self.send_json(error_response, status=429, retry=True)
                    return
                try:
                    with owner.lock:
                        continuation = owner.length_pending and row['purpose'] == 'agent'
                        truncate = (owner.length_continuation and not owner.length_sent and
                                    row['purpose'] == 'agent' and _receipt(_tool_results(body.get('messages', []))) is not None)
                        if truncate:
                            owner.length_sent = owner.length_pending = True
                        elif continuation:
                            owner.length_pending = False
                    message = owner.reply(body, number, truncate=truncate, continuation=continuation)
                except ValueError as error:
                    self.send_error(422, str(error))
                    row["error"] = str(error)
                    return
                # Auxiliary title/summary requests should finish normally while
                # the real agent request remains active for native cancellation.
                usage = owner.usage(row)
                if owner.request_sized_usage and usage is not None:
                    # A compression fixture needs usage to reflect the actual
                    # long request before compaction and the short one after.
                    # This is provider-side evidence, never host/plugin state.
                    usage['prompt_tokens'] = max(1, len(json.dumps(body.get('messages', []), ensure_ascii=False)) // 4)
                    usage['total_tokens'] = usage['prompt_tokens'] + usage['completion_tokens']
                if truncate and usage is not None:
                    usage['completion_tokens_details'] = {'reasoning_tokens': 1}
                calls = [{"id": call["id"], "type": "function", "function": {
                    "name": call["name"], "arguments": json.dumps(call["args"]),
                }} for call in message.tool_calls]
                response_message = {"role": "assistant", "content": message.content or None}
                reasoning = message.additional_kwargs.get('reasoning_content')
                if isinstance(reasoning, str):
                    response_message['reasoning_content'] = reasoning
                if calls:
                    response_message["tool_calls"] = calls
                row.update(response=response_message, usage=usage, usage_reported=usage is not None, status=200)
                envelope = {"id": f"chatcmpl_native_{number}", "created": int(time.time()),
                            "model": body.get("model", "native-e2e-chat")}
                finish = message.response_metadata.get('finish_reason') or ("tool_calls" if calls else "stop")
                if not body.get("stream"):
                    value = {**envelope, "object": "chat.completion", "choices": [
                        {"index": 0, "message": response_message, "finish_reason": finish},
                    ]}
                    if usage is not None:
                        value["usage"] = usage
                    row['finish_reason'] = finish
                    self.send_json(value)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True

                def send(delta=None, reason=None, final_usage=None):
                    value = {**envelope, "object": "chat.completion.chunk", "choices": []}
                    if final_usage is not None:
                        value["usage"] = final_usage
                    else:
                        value["choices"] = [{"index": 0, "delta": delta or {}, "finish_reason": reason}]
                    self.wfile.write(("data: " + json.dumps(value) + "\n\n").encode())
                    self.wfile.flush()

                try:
                    send({"role": "assistant", "content": ""})
                    if isinstance(reasoning, str):
                        send({'reasoning_content': reasoning})
                    if owner.scenario == "cancel" and row["purpose"] == "agent":
                        # The real driver must abort an active native request.
                        for _ in range(60):
                            if owner.stop.wait(.5):
                                return
                            send({"content": "pending "})
                    elif calls:
                        for offset in range(0, len(message.content), 16):
                            send({"content": message.content[offset:offset + 16]})
                        for index, call in enumerate(calls):
                            send({"tool_calls": [{"index": index, "id": call["id"], "type": "function",
                                                  "function": {"name": call["function"]["name"], "arguments": ""}}]})
                            send({"tool_calls": [{"index": index, "function": {
                                "arguments": call["function"]["arguments"],
                            }}]})
                    elif truncate:
                        # The length regression compares the original wire
                        # message byte for byte with its native SDK output.
                        send({'content': message.content})
                    else:
                        # SSE text must concatenate to the independently
                        # recorded HTTP response, including its whitespace.
                        for offset in range(0, len(message.content), 16):
                            send({"content": message.content[offset:offset + 16]})
                    send(reason=finish)
                    if usage is not None:
                        send(final_usage=usage)
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                    row['finish_reason'] = finish
                except (BrokenPipeError, ConnectionResetError):
                    row["client_disconnected"] = True

        self.server = LoopbackHTTPServer(Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"
        return self

    def __exit__(self, *_args):
        self.stop.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
