"""Loopback Responses/Chat carrier for native Codex diagnostic acceptance.

The carrier records both protocols. It forwards every sampling attempt to the
selected Chat service and never manufactures model content, tool results, usage
or successful completion for an incomplete upstream stream.
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler
import json
import threading
import time
from urllib.parse import urlsplit
import uuid

import httpx

from loopback_http import LoopbackHTTPServer

MAX_BODY = 8 * 1024 * 1024


def loopback(url):
    parsed = urlsplit(url)
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("Codex diagnostic endpoints require credential-free HTTP loopback")
    return url.rstrip("/")


def _text(content):
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        raise ValueError("unsupported Responses message content")
    texts = []
    for part in content:
        if part.get("type") not in {"input_text", "output_text"} or not isinstance(part.get("text"), str):
            raise ValueError("unsupported Responses content part")
        texts.append(part["text"])
    return "".join(texts)


def translate_request(body):
    messages, customs = [], set()
    if body.get("previous_response_id"):
        raise ValueError("stateless carrier requires complete native input history")
    if body.get("instructions"):
        messages.append({"role": "system", "content": _text(body["instructions"])})
    items = body.get("input", [])
    if isinstance(items, str):
        items = [{"type": "message", "role": "user", "content": items}]
    if not isinstance(items, list):
        raise ValueError("Responses input must be text or items")
    tools = []
    for tool in body.get("tools", []):
        kind = tool.get("type")
        if kind == "function":
            function = {key: tool[key] for key in ("name", "description", "parameters", "strict") if key in tool}
        elif kind == "custom" and tool.get("name") == "apply_patch":
            customs.add(tool["name"])
            function = {"name": tool["name"], "description": tool.get("description", "Apply a patch"),
                "parameters": {"type": "object", "properties": {"input": {"type": "string"}},
                               "required": ["input"], "additionalProperties": False}}
        else:
            raise ValueError("unsupported Responses tool " + str(kind))
        tools.append({"type": "function", "function": function})
    for item in items:
        kind = item.get("type", "message")
        if kind == "message":
            role = item.get("role")
            if role not in {"system", "developer", "user", "assistant"}:
                raise ValueError("unsupported Responses message role")
            messages.append({"role": "system" if role == "developer" else role, "content": _text(item.get("content", []))})
        elif kind in {"function_call", "custom_tool_call"}:
            arguments = item.get("arguments") if kind == "function_call" else json.dumps({"input": item["input"]})
            if kind == "custom_tool_call" and item.get("name") != "apply_patch":
                raise ValueError("unsupported custom tool call")
            if not isinstance(arguments, str):
                raise ValueError("tool arguments must be text")
            call = {"id": item["call_id"], "type": "function", "function": {"name": item["name"], "arguments": arguments}}
            if messages and messages[-1]["role"] == "assistant":
                messages[-1].setdefault("tool_calls", []).append(call)
            else:
                messages.append({"role": "assistant", "content": None, "tool_calls": [call]})
        elif kind in {"function_call_output", "custom_tool_call_output"}:
            messages.append({"role": "tool", "tool_call_id": item["call_id"], "content": _text(item["output"])})
        elif kind == "reasoning":
            # Only summaries exposed on our Chat boundary can round-trip. An
            # opaque encrypted item cannot be interpreted as model reasoning.
            if item.get("encrypted_content"):
                raise ValueError("encrypted reasoning cannot be translated to Chat")
            summary = _text([{"type": "output_text", "text": part["text"]} for part in item.get("summary", [])])
            if summary:
                messages.append({"role": "assistant", "content": None, "reasoning_content": summary})
        else:
            raise ValueError("unsupported Responses input item " + str(kind))
    result = {"model": body["model"], "messages": messages, "stream": bool(body.get("stream"))}
    if tools:
        result["tools"] = tools
    for key in ("temperature", "top_p", "parallel_tool_calls"):
        if key in body:
            result[key] = body[key]
    if "max_output_tokens" in body:
        result["max_tokens"] = body["max_output_tokens"]
    reasoning = body.get("reasoning")
    if reasoning is not None:
        if not isinstance(reasoning, dict):
            raise ValueError("Responses reasoning must be an object")
        effort = reasoning.get("effort")
        if effort is not None:
            if not isinstance(effort, str) or effort not in {"none", "minimal", "low", "medium", "high", "xhigh", "max"}:
                raise ValueError("unsupported Responses reasoning effort")
            result["reasoning_effort"] = effort
    if isinstance(body.get("tool_choice"), str) and body["tool_choice"] in {"auto", "none", "required"}:
        result["tool_choice"] = body["tool_choice"]
    elif isinstance(body.get("tool_choice"), dict):
        choice = body["tool_choice"]
        if (choice.get("type") not in {"function", "custom"} or not choice.get("name")
                or (choice["type"] == "custom" and choice["name"] not in customs)):
            raise ValueError("unsupported Responses tool choice")
        result["tool_choice"] = {"type": "function", "function": {"name": choice["name"]}}
    elif "tool_choice" in body:
        raise ValueError("unsupported Responses tool choice")
    if result["stream"]:
        result["stream_options"] = {"include_usage": True}
    return result, customs


def translate_usage(usage):
    if usage is None:
        return None
    result = {}
    for native, chat in (("input_tokens", "prompt_tokens"), ("output_tokens", "completion_tokens"), ("total_tokens", "total_tokens")):
        value = usage.get(chat)
        if type(value) is not int or value < 0:
            raise ValueError("provider usage lacks valid " + chat)
        result[native] = value
    for native, chat in (("input_tokens_details", "prompt_tokens_details"), ("output_tokens_details", "completion_tokens_details")):
        if isinstance(usage.get(chat), dict):
            result[native] = dict(usage[chat])
    return result


class _Translation:
    def __init__(self, model, customs, emit):
        self.response = {"id": "resp_" + uuid.uuid4().hex, "object": "response", "created_at": int(time.time()),
                         "model": model, "status": "in_progress", "output": []}
        self.customs, self.emit = customs, emit
        self.message = {"role": "assistant", "content": None}
        self.items, self.tools = [], {}
        self.text_item = self.reasoning_item = None
        self.usage = None
        self.finish = None
        self.done = False

    def start(self):
        self.emit("response.created", response=dict(self.response))

    def add(self, item):
        index = len(self.items)
        self.items.append(item)
        self.emit("response.output_item.added", output_index=index, item=dict(item))
        return index

    def feed(self, payload):
        if not isinstance(payload, dict) or "error" in payload:
            raise ValueError("provider returned an invalid Chat stream event")
        if isinstance(payload.get("usage"), dict):
            self.usage = payload["usage"]
        for choice in payload.get("choices", []):
            if choice.get("index", 0) != 0:
                raise ValueError("carrier supports one Chat choice")
            self.finish = choice.get("finish_reason") or self.finish
            delta = choice.get("delta", {})
            content = delta.get("content")
            if content:
                if self.text_item is None:
                    self.text_item = self.add({"id": "msg_" + uuid.uuid4().hex, "type": "message", "status": "in_progress",
                        "role": "assistant", "content": [{"type": "output_text", "text": "", "annotations": []}]})
                    self.emit("response.content_part.added", output_index=self.text_item, content_index=0,
                              item_id=self.items[self.text_item]["id"], part={"type": "output_text", "text": "", "annotations": []})
                self.items[self.text_item]["content"][0]["text"] += content
                self.message["content"] = (self.message["content"] or "") + content
                self.emit("response.output_text.delta", output_index=self.text_item, content_index=0,
                          item_id=self.items[self.text_item]["id"], delta=content)
            reasoning = delta.get("reasoning_content") or delta.get("reasoning")
            if reasoning:
                if self.reasoning_item is None:
                    self.reasoning_item = self.add({"id": "rs_" + uuid.uuid4().hex, "type": "reasoning", "summary": [{"type": "summary_text", "text": ""}]})
                self.items[self.reasoning_item]["summary"][0]["text"] += reasoning
                self.message["reasoning_content"] = self.message.get("reasoning_content", "") + reasoning
                self.emit("response.reasoning_summary_text.delta", output_index=self.reasoning_item, summary_index=0,
                          item_id=self.items[self.reasoning_item]["id"], delta=reasoning)
            for call in delta.get("tool_calls", []):
                index = call.get("index", 0)
                if index not in self.tools:
                    self.tools[index] = {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
                target = self.tools[index]
                if call.get("id"):
                    target["id"] = call["id"]
                for key in ("name", "arguments"):
                    target["function"][key] += call.get("function", {}).get(key) or ""

    def complete(self):
        if not self.finish:
            raise ValueError("provider stream lacks finish_reason")
        for call in (self.tools[index] for index in sorted(self.tools)):
            if not call["id"] or not call["function"]["name"]:
                raise ValueError("provider tool call lacks identity")
            item = {"id": "fc_" + uuid.uuid4().hex, "call_id": call["id"], "name": call["function"]["name"], "status": "completed"}
            if item["name"] in self.customs:
                args = json.loads(call["function"]["arguments"])
                if set(args) != {"input"} or not isinstance(args["input"], str):
                    raise ValueError("custom apply_patch requires exactly one text input")
                item.update(type="custom_tool_call", input=args["input"])
            else:
                item.update(type="function_call", arguments=call["function"]["arguments"])
            self.add(item)
        if self.tools:
            self.message["tool_calls"] = [self.tools[index] for index in sorted(self.tools)]
        for index, item in enumerate(self.items):
            if item["type"] == "message":
                item["status"] = "completed"
                self.emit("response.output_text.done", output_index=index, content_index=0, item_id=item["id"], text=item["content"][0]["text"])
            self.emit("response.output_item.done", output_index=index, item=item)
        self.response.update(status="incomplete" if self.finish == "length" else "completed", output=self.items)
        usage = translate_usage(self.usage)
        if usage is not None:
            self.response["usage"] = usage
        if self.finish == "length":
            self.response["incomplete_details"] = {"reason": "max_output_tokens"}
        self.emit("response.completed", response=self.response)


class CodexProvider:
    """Forward native requests; an explicit fallback cap never replaces a native cap."""

    def __init__(self, chat_url, *, timeout=300, default_max_tokens=None):
        self.chat_url = loopback(chat_url)
        if not 0 < timeout <= 1800:
            raise ValueError("carrier timeout must be positive and at most 1800 seconds")
        if default_max_tokens is not None and (type(default_max_tokens) is not int or default_max_tokens <= 0):
            raise ValueError("default_max_tokens must be a positive integer")
        self.default_max_tokens = default_max_tokens
        self.timeout, self.requests = timeout, []
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.request_started = threading.Event()
        self.responses, self.workers = set(), set()

    def __enter__(self):
        owner = self
        self.client = httpx.Client(timeout=httpx.Timeout(self.timeout, connect=min(10, self.timeout)), trust_env=False,
                                   transport=httpx.HTTPTransport(retries=0))
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def send_json(self, value, status=200, headers=None):
                raw = json.dumps(value).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                for key, value in (headers or {}).items(): self.send_header(key, value)
                self.end_headers()
                self.wfile.write(raw)
            def do_POST(self):
                if self.path.rstrip("/") != "/v1/responses":
                    self.send_error(404)
                    return
                streaming = False
                row = {"number": 0, "usage": None, "usage_reported": False, "purpose": "agent", "completed": False}
                with owner.lock:
                    row["number"] = len(owner.requests) + 1
                    owner.requests.append(row)
                    owner.workers.add(threading.current_thread())
                row["http_started_ns"] = time.time_ns()
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= MAX_BODY:
                        raise ValueError("Responses body exceeds limit or is empty")
                    body = json.loads(self.rfile.read(length))
                    row["request"] = body
                    chat, customs = translate_request(body)
                    if owner.default_max_tokens is not None:
                        chat.setdefault("max_tokens", owner.default_max_tokens)
                    row["chat_request"] = chat
                    url = owner.chat_url + ("/chat/completions" if owner.chat_url.endswith("/v1") else "/v1/chat/completions")
                    with owner.client.stream("POST", url, json=chat) as upstream:
                        row["upstream_started_ns"] = time.time_ns()
                        owner.request_started.set()
                        with owner.lock: owner.responses.add(upstream)
                        row["status"] = upstream.status_code
                        if upstream.status_code != 200:
                            raw = upstream.read()
                            if len(raw) > MAX_BODY: raise ValueError("upstream error exceeds limit")
                            value = json.loads(raw)
                            row.update(response=value, error="Chat HTTP " + str(upstream.status_code), completed=True)
                            retry = {"Retry-After": upstream.headers["Retry-After"]} if "Retry-After" in upstream.headers else None
                            self.send_json(value, upstream.status_code, retry)
                            return
                        events = []
                        def emit(kind, **values):
                            event = {"type": kind, "sequence_number": len(events), **values}
                            # Snapshot now: later output deltas must not rewrite
                            # the creation event in our evidence ledger.
                            event = json.loads(json.dumps(event))
                            events.append(event)
                            if streaming:
                                self.wfile.write(("event: " + kind + "\ndata: " + json.dumps(event) + "\n\n").encode())
                                self.wfile.flush()
                        translation = _Translation(chat["model"], customs, emit)
                        if chat["stream"]:
                            streaming = True
                            self.send_response(200)
                            self.send_header("Content-Type", "text/event-stream")
                            self.send_header("Connection", "close")
                            self.end_headers()
                            self.close_connection = True
                            translation.start()
                            size, data = 0, []
                            for line in upstream.iter_lines():
                                if owner.stop.is_set(): raise ValueError("carrier stopped during upstream stream")
                                size += len(line.encode())
                                if size > MAX_BODY: raise ValueError("upstream stream exceeds limit")
                                if line.startswith("data:"):
                                    data.append(line[5:].lstrip())
                                elif not line and data:
                                    value = "\n".join(data)
                                    data = []
                                    if value == "[DONE]":
                                        translation.done = True
                                        break
                                    translation.feed(json.loads(value))
                            if not translation.done:
                                raise ValueError("incomplete Chat stream")
                        else:
                            raw = upstream.read()
                            if len(raw) > MAX_BODY: raise ValueError("upstream JSON exceeds limit")
                            payload = json.loads(raw)
                            choice = payload["choices"][0]
                            delta = dict(choice["message"])
                            delta["tool_calls"] = [{"index": index, **call} for index, call in enumerate(delta.get("tool_calls", []))]
                            translation.feed({"choices": [{"delta": delta, "finish_reason": choice["finish_reason"]}], "usage": payload.get("usage")})
                        translation.complete()
                        row.update(response=translation.message, responses_response=translation.response,
                            responses_events=events, usage=translation.usage, usage_reported=translation.usage is not None,
                            finish_reason=translation.finish, stream_done=translation.done, completed=True)
                        if not streaming: self.send_json(translation.response)
                except (ValueError, KeyError, TypeError, AttributeError, httpx.HTTPError, OSError) as error:
                    row["error"] = str(error)
                    if not streaming:
                        row["status"] = 422 if isinstance(error, (ValueError, KeyError, TypeError, AttributeError)) else 502
                        try: self.send_json({"error": {"message": str(error), "type": "carrier_error"}}, row["status"])
                        except OSError: pass
                    row["client_disconnected"] = isinstance(error, (BrokenPipeError, ConnectionResetError))
                finally:
                    row["http_ended_ns"] = time.time_ns()
                    with owner.lock:
                        if "upstream" in locals(): owner.responses.discard(upstream)
                        owner.workers.discard(threading.current_thread())
        self.server = LoopbackHTTPServer(Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"
        return self

    def __exit__(self, *_):
        self.stop.set()
        with self.lock: responses, workers = list(self.responses), list(self.workers)
        for response in responses:
            try: response.close()
            except (OSError, httpx.HTTPError): pass
        self.client.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        deadline = time.monotonic() + 3
        for worker in workers: worker.join(timeout=max(0, deadline - time.monotonic()))
        for row in self.requests:
            if not row["completed"] and not row.get("error"):
                row["error"] = "carrier attempt incomplete at shutdown"
