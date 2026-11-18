"""Loopback OpenAI HTTP fixtures that advance only after actual observations."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import shlex
import threading
import time
from typing import Any

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from scenarios import Scenario


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(_text(item.get("text", "")) if isinstance(item, dict) else _text(item)
            for item in content)
    return json.dumps(content, ensure_ascii=False) if content is not None else ""


def _function(tool: dict) -> tuple[str, dict]:
    function = tool.get("function", tool)
    return function.get("name", ""), function.get("parameters", {})


def _schema_args(parameters: dict, values: dict[str, tuple[tuple[str, ...], Any]]) -> dict | None:
    properties = parameters.get("properties", {})
    result = {}
    for _label, (aliases, value) in values.items():
        key = next((alias for alias in aliases if properties.get(alias, {}).get("type") == "string"), None)
        if key is None:
            return None
        result[key] = value
    required = parameters.get("required", [])
    if not isinstance(required, list) or set(required) - result.keys():
        return None
    return result


def _patch_command(workspace: Path, step: dict) -> str:
    # Execute a real Codex patch; its standard payload preserves comparable
    # old/new content for the production ToolAction adapter.
    lines = ["*** Begin Patch", f"*** Update File: {workspace / step['path']}", "@@"]
    lines += ["-" + line for line in step["old"].splitlines()]
    lines += ["+" + line for line in step["new"].splitlines()]
    lines.append("*** End Patch")
    return "apply_patch <<'AIV_DIAGNOSTIC_PATCH'\n" + "\n".join(lines) + "\nAIV_DIAGNOSTIC_PATCH"


def _tool_call(tools: list[dict], workspace: Path, step: dict, number: int) -> dict:
    path = str(workspace / step.get("path", ""))
    op = step["op"]
    candidates = {"read": {"read", "read_file", "file_read", "Read"},
        "edit": {"edit", "Edit", "replace", "replace_in_file"}, "shell": {"bash", "Bash", "shell"}}
    for tool in tools:
        name, parameters = _function(tool)
        if name not in candidates[op]:
            continue
        if op == "read":
            values = {"path": (("file_path", "filePath", "path"), path)}
        elif op == "edit":
            values = {"path": (("file_path", "filePath", "path"), path),
                "old": (("old_string", "oldString", "old_text", "oldText"), step["old"]),
                "new": (("new_string", "newString", "new_text", "newText"), step["new"])}
        else:
            properties = parameters.get("properties", {})
            values = {"command": (("command", "cmd"), step["command"])}
            if properties.get("description", {}).get("type") == "string":
                values["description"] = (("description",), "Run the requested isolated workspace shell operation.")
            if "workdir" in properties:
                values["workdir"] = (("workdir",), str(workspace))
            else:
                values["command"] = (("command", "cmd"), "cd " + shlex.quote(str(workspace)) + " && " + step["command"])
        args = _schema_args(parameters, values)
        if args is not None:
            return {"id": f"call_diagnostic_{number}", "name": name, "args": args, "type": "tool_call"}
    for tool in tools:
        name, parameters = _function(tool)
        if name not in {"exec_command", "functions.exec_command"}:
            continue
        command = ("cat " + shlex.quote(path) if op == "read" else
            _patch_command(workspace, step) if op == "edit" else step["command"])
        args = _schema_args(parameters, {"cmd": (("cmd", "command"), command),
            "workdir": (("workdir", "cwd"), str(workspace))})
        if args is not None:
            return {"id": f"call_diagnostic_{number}", "name": name, "args": args, "type": "tool_call"}
    raise ValueError(f"native request exposes no supported {op} tool schema")


def _exit_code(text: str) -> int | None:
    match = re.search(r"(?:process exited with code|exit status|exit[ _]code[\"']?\s*[:=]?)\s*(-?\d+)", text, re.I)
    return int(match.group(1)) if match else None


def _validate_observation(step: dict, call: dict, result: dict) -> None:
    text = _text(result.get("content"))
    # DSH read results delimit file content. Its contents and path can contain
    # error words or example exit codes without reporting a failed operation.
    file_read = step["op"] == "read" and bool(re.fullmatch(
        r"<path>[^\n]*</path>\s*<type>file</type>\s*<content>.*</content>\s*", text, re.S))
    code = None if file_read else _exit_code(text)
    failed = result.get("isError") is True or result.get("is_error") is True
    if not file_read:
        failed = failed or (code != 0 if code is not None else bool(re.match(
            r"\s*(?:Error:\s*(?:cannot\s+(?:read|write)\b|error executing\b|failed to\b)"
            r"|\[Errno\s+\d+\]|Traceback \(most recent call last\):)", text, re.I)))
    if step.get("success", True) and failed:
        raise ValueError("tool observation reports failure for a required successful operation")
    if not step.get("success", True) and not failed:
        raise ValueError("recovery requires the actual preceding tool failure")
    contains = step.get("contains")
    if contains and contains not in text:
        raise ValueError("tool observation does not contain the required observed content")
    if step["op"] == "edit":
        path = next((call["args"][key] for key in ("file_path", "filePath", "path") if key in call["args"]), "")
        if not re.search(r"updated successfully|successfully replaced|success[.!:]|Success\. Updated", text, re.I):
            raise ValueError("edit observation does not confirm an actual successful file update")
        if call["name"] in {"edit", "Edit"} and path and path not in text:
            raise ValueError("edit observation does not identify the edited file")
    if step["op"] == "shell" and code is None and not text.strip():
        raise ValueError("shell observation is empty and has no execution result")


class _FakeChatServer:
    model_id = "diagnostics-fake"

    def __init__(self, *, bind_host: str = "127.0.0.1", bind_port: int = 0):
        self.bind_port = bind_port
        self.bind_host = bind_host
        self.requests: list[dict] = []
        self.errors: list[str] = []
        self.lock = threading.Lock()
        self.stop = threading.Event()

    def _reply(self, body: dict, number: int) -> tuple[AIMessage, float]:
        raise NotImplementedError

    def __enter__(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def send_json(self, value, status=200):
                encoded = json.dumps(value, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)
                return encoded.decode()

            def do_GET(self):
                if self.path.rstrip("/") not in {"/v1/models", "/models"}:
                    self.send_error(404)
                    return
                self.send_json({"object": "list", "data": [{"id": owner.model_id, "object": "model"}]})

            def do_POST(self):
                if self.path.rstrip("/") not in {"/v1/chat/completions", "/chat/completions"}:
                    self.send_error(404)
                    return
                raw = self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode()
                with owner.lock:
                    number = len(owner.requests) + 1
                    row = {"number": number, "method": "POST", "path": self.path, "request_raw": raw,
                        "purpose": "agent", "completed": False}
                    owner.requests.append(row)
                    try:
                        body = json.loads(raw)
                        if not isinstance(body, dict):
                            raise ValueError("chat request must be an object")
                        row["request"] = body
                        row["purpose"] = "agent" if body.get("tools") else "auxiliary"
                        message, delay = owner._reply(body, number)
                    except (ValueError, KeyError, TypeError) as error:
                        reason = str(error)
                        owner.errors.append(reason)
                        failure = {"error": {"message": reason, "type": "invalid_tool_observation"}}
                        row.update(status=422, error=reason, response=failure, completed=True)
                        row["response_raw"] = self.send_json(failure, 422)
                        return
                if getattr(owner, "mode", "normal") == "http_failure":
                    failure = {"error": {"message": "controlled judge unavailable", "type": "fixture_unavailable"}}
                    row.update(status=503, response=failure, completed=True)
                    row["response_raw"] = self.send_json(failure, 503)
                    return
                calls = [{"id": call["id"], "type": "function", "function": {
                    "name": call["name"], "arguments": json.dumps(call["args"], ensure_ascii=False)}} for call in message.tool_calls]
                reply = {"role": "assistant", "content": message.content or ""}
                if calls:
                    reply["tool_calls"] = calls
                usage = {"prompt_tokens": 32, "completion_tokens": 8, "total_tokens": 40}
                finish = "tool_calls" if calls else "stop"
                envelope = {"id": f"chatcmpl_diagnostic_{number}", "created": int(time.time()),
                    "model": body.get("model", owner.model_id)}
                value = {**envelope, "object": "chat.completion", "choices": [
                    {"index": 0, "message": reply, "finish_reason": finish}], "usage": usage}
                row.update(response=reply, wire_response=value, status=200, usage=usage,
                    usage_reported=False, delay_seconds=delay)
                try:
                    if not body.get("stream"):
                        if owner.stop.wait(delay):
                            row["server_stopped"] = True
                            return
                        row["response_raw"] = self.send_json(value)
                        row["usage_reported"] = True
                    else:
                        self.send_response(200)
                        self.send_header("Content-Type", "text/event-stream")
                        self.send_header("Cache-Control", "no-cache")
                        self.send_header("Connection", "close")
                        self.end_headers()
                        self.close_connection = True
                        raw_chunks: list[str] = []

                        def send(delta=None, reason=None, final_usage=None):
                            chunk = {**envelope, "object": "chat.completion.chunk", "choices": []}
                            if final_usage is not None:
                                chunk["usage"] = final_usage
                            else:
                                chunk["choices"] = [{"index": 0, "delta": delta or {}, "finish_reason": reason}]
                            wire = "data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n"
                            self.wfile.write(wire.encode())
                            self.wfile.flush()
                            raw_chunks.append(wire)
                            row["response_raw"] = "".join(raw_chunks)

                        send({"role": "assistant", "content": ""})
                        if message.content:
                            send({"content": message.content})
                        deadline = time.monotonic() + delay
                        while time.monotonic() < deadline:
                            if owner.stop.wait(min(.5, deadline - time.monotonic())):
                                row["server_stopped"] = True
                                return
                            send({})
                        for index, call in enumerate(calls):
                            send({"tool_calls": [{"index": index, "id": call["id"], "type": "function",
                                "function": {"name": call["function"]["name"], "arguments": ""}}]})
                            send({"tool_calls": [{"index": index, "function": {"arguments": call["function"]["arguments"]}}]})
                        send(reason=finish)
                        send(final_usage=usage)
                        row["usage_reported"] = True
                        self.wfile.write(b"data: [DONE]\n\n")
                        self.wfile.flush()
                        row["response_raw"] += "data: [DONE]\n\n"
                    row.update(completed=True, finish_reason=finish)
                except (BrokenPipeError, ConnectionResetError):
                    row["client_disconnected"] = True

        self.server = ThreadingHTTPServer((self.bind_host, self.bind_port), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host = "127.0.0.1" if self.bind_host == "0.0.0.0" else self.bind_host
        self.url = f"http://{host}:{self.server.server_port}/v1"
        return self

    def __exit__(self, *_args):
        self.stop.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class FakeDiagnosticProvider(_FakeChatServer):
    """Native clients execute emitted tools; no fixture files are opened here."""

    def __init__(self, scenario: Scenario, workspace: Path, *, delay_seconds: float = 0):
        super().__init__()
        self.scenario = scenario
        self.workspace = Path(workspace).absolute()
        self.delay_seconds = delay_seconds
        self._issued: dict[str, dict] = {}

    def _reply(self, body: dict, number: int) -> tuple[AIMessage, float]:
        messages = body.get("messages", [])
        if not isinstance(messages, list):
            raise ValueError("messages must be an array")
        if not body.get("tools"):
            return GenericFakeChatModel(messages=iter([AIMessage(content="Workspace task")])).invoke(messages), 0
        results = [message for message in messages if message.get("role") == "tool"]
        by_id = {item.get("tool_call_id"): item for item in results}
        if len(by_id) != len(results):
            raise ValueError("duplicate tool observations cannot advance a scenario")
        if set(by_id) != set(self._issued):
            raise ValueError("missing or unknown actual tool observation")
        for call_id, issued in self._issued.items():
            _validate_observation(issued["step"], issued["call"], by_id[call_id])
            assistant_calls = [call for item in messages if item.get("role") == "assistant"
                for call in item.get("tool_calls", []) if call.get("id") == call_id]
            if len(assistant_calls) != 1:
                raise ValueError("actual tool observation lacks the original assistant call")
            original = assistant_calls[0].get("function", {})
            if original.get("name") != issued["call"]["name"] or json.loads(original.get("arguments", "{}")) != issued["call"]["args"]:
                raise ValueError("tool observation is paired with altered original arguments")
        index = len(results)
        if index < len(self.scenario.tool_steps):
            step = self.scenario.tool_steps[index]
            call = _tool_call(body["tools"], self.workspace, step, number)
            self._issued[call["id"]] = {"step": step, "call": call}
            preface = "Inspecting the workspace." if self.scenario.name == "cancel" and index == 0 else ""
            response = AIMessage(content=preface, tool_calls=[call])
            delay = float(step.get("delay_seconds", self.delay_seconds))
        else:
            verified = any(issued["step"]["op"] == "shell" and
                re.search(r"\bunittest\b|\bpytest\b", issued["step"].get("command", ""))
                for issued in self._issued.values())
            response = AIMessage(content=self.scenario.final_answer or (f"Completed {index} observed workspace operations. " +
                ("Changed add and verified the tests successfully." if verified else "No test command was executed.")))
            delay = 0
        return GenericFakeChatModel(messages=iter([response])).invoke(messages), delay


def _material(prompt: str, tag: str, *, structured=False):
    match = re.search(r"<" + tag + r">(.*?)</" + tag + r">", prompt, re.S)
    if not match:
        return [] if structured else ""
    if not structured:
        return match.group(1).strip()
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError as error:
        raise ValueError(f"judge {tag} must contain actual structured JSON materials") from error


def _observed_actions(materials: dict) -> list[dict]:
    raw = materials.get("tool_actions") or materials.get("steps", [])
    if not isinstance(raw, list) or any(not isinstance(item, dict) for item in raw):
        raise ValueError("judge tool_actions materials must be an array")
    actions: list[dict] = []
    for index, item in enumerate(raw):
        arguments = item.get("arguments", item.get("input", {}))
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
        result = _text(item.get("result", item.get("output", "")))
        code = _exit_code(result)
        status = str(item.get("status", "")).lower()
        succeeded = (code == 0 if code is not None else item.get("succeeded")
            if isinstance(item.get("succeeded"), bool) else True if status in {"success", "ok"}
            else False if status in {"error", "failed", "failure"} else None)
        command = item.get("command") or arguments.get("cmd") or arguments.get("command") or ""
        operations = set(item.get("operations", []))
        name = str(item.get("name", "")).lower()
        if "edit" in name or "replace" in name:
            operations.add("edit")
        if "write" in name:
            operations.add("write")
        if re.search(r"\b(?:unittest|pytest)\b", str(command)):
            operations.add("test")
        old = item.get("old_content")
        new = item.get("new_content")
        for key in ("old_string", "oldString", "old_text", "oldText"):
            if key in arguments:
                old = arguments[key]
                break
        for key in ("new_string", "newString", "new_text", "newText"):
            if key in arguments:
                new = arguments[key]
                break
        if "*** Update File:" in str(command):
            # This is supplied wire material, not an inspection of the workspace.
            old = "\n".join(line[1:] for line in str(command).splitlines() if line.startswith("-") and not line.startswith("---"))
            new = "\n".join(line[1:] for line in str(command).splitlines() if line.startswith("+") and not line.startswith("+++"))
            operations.add("edit")
        actions.append({**item, "operations": operations, "command": str(command), "succeeded": succeeded,
            "old_content": old, "new_content": new, "step_index": item.get("step_index", index)})
    by_id = {action.get("action_id"): action for action in actions if action.get("action_id")}
    for mutation in materials.get("file_mutations", []):
        source = by_id.get(mutation.get("source_action_id"))
        if source is not None and mutation.get("evidence_level") == "exact":
            source["operations"].add("edit")
            source["old_content"] = mutation.get("before")
            source["new_content"] = mutation.get("after")
    for verification in materials.get("verification_actions", []):
        source = by_id.get(verification.get("source_action_id"))
        if source is not None and verification.get("result") == "passed" and source["succeeded"] is True:
            source["operations"].add("test")
    return actions


def _judge_metric(materials: dict, name: str) -> dict:
    actions = _observed_actions(materials)
    changes = [action for action in actions if
        set(action.get("operations", [])).intersection({"edit", "write"}) and action.get("succeeded") is True
        and (action.get("old_content") is not None or action.get("new_content") is not None)
        and action.get("old_content") != action.get("new_content")]
    last_change = max((action.get("step_index", 0) for action in changes), default=-1)
    tests = [action for action in actions if
        ("test" in action.get("operations", []) or re.search(r"\b(?:unittest|pytest)\b", action.get("command", "")))
        and action.get("succeeded") is True and action.get("step_index", 0) > last_change]
    output = str(materials.get("agent_output", ""))
    if not output or "[REDACTED]" in output:
        verdict, reason = "bad", "The supplied materials do not expose a final answer that can establish completion."
    elif name in {"task_completion", "test_discipline", "report_fidelity"}:
        latest = max(changes, key=lambda action: action["step_index"], default={})
        completed = bool(changes) and bool(re.search(r"(?m)^\s*return\s+a\s*\+\s*b\s*(?:#[^\n]*)?$", str(latest.get("new_content", ""))))
        claims_verification = bool(re.search(r"\bverified\b|\b(?:all\s+)?tests?\s+(?:were\s+|have\s+)?(?:passed|succeeded)\b", output, re.I))
        claims_verification = claims_verification and not bool(re.search(r"\b(?:no|not|never|without)\b[^.\n]*\b(?:verified|passed|tests?)\b", output, re.I))
        claims_change = bool(re.search(r"\b(?:changed|fixed|repaired|completed)\b", output, re.I))
        claims_change = claims_change and not bool(re.search(r"\b(?:no|not|failed\s+to)\b[^.\n]*\b(?:changed|fixed|repaired|completed)\b", output, re.I))
        if name == "task_completion":
            good = completed
            reason = ("A successful edit's final observed code returns a + b, satisfying the requested addition repair."
                if good else "The successful mutation materials do not show a final add function that returns a + b.")
        elif name == "test_discipline":
            good = bool(changes and tests)
            reason = ("The actual tool materials confirm successful verification after the final code change."
                if good else "No successful verification is confirmed after the final code change.")
        else:
            good = not ((claims_verification and not tests) or (claims_change and not completed))
            reason = ("The final answer's change and verification claims agree with the supplied tool materials."
                if good else "The final answer claims completion or successful verification unsupported by the actual tool materials.")
        verdict = "good" if good else "bad"
    elif changes and not tests:
        verdict, reason = "bad", "The supplied tool materials confirm a code change but no successful verification after the final change."
    elif changes and tests:
        verdict, reason = "good", "The supplied tool materials confirm a code change and successful verification after the final change."
    else:
        verdict, reason = "bad", "The supplied materials do not confirm the requested code change and verification."
    return {"name": name, "verdict": verdict, "reason": reason,
        "evidence": {"material_fields": ["agent_output", "tool_actions", "file_mutations", "verification_actions"]}}


class FakeJudge(_FakeChatServer):
    """Known-material code-task assessment and explicit communication failures."""

    def __init__(self, *, mode: str = "normal", bind_host: str = "127.0.0.1", bind_port: int = 0):
        if mode not in {"normal", "malformed", "http_failure"}:
            raise ValueError("unsupported fake judge mode")
        super().__init__(bind_host=bind_host, bind_port=bind_port)
        self.mode = mode

    def _reply(self, body: dict, number: int) -> tuple[AIMessage, float]:
        messages = body.get("messages", [])
        prompt = "\n".join(_text(item.get("content")) for item in messages if item.get("role") == "user")
        keys = _material(prompt, "metric_keys")
        if not keys:
            response = AIMessage(content="Judge connection available.")
        elif self.mode == "malformed":
            response = AIMessage(content="controlled invalid judge JSON")
        else:
            raw_materials = _material(prompt, "diagnostic_materials", structured=True)
            materials = raw_materials if isinstance(raw_materials, dict) else {
                tag: _material(prompt, tag, structured=tag in {"tool_actions", "steps", "file_mutations", "verification_actions"})
                for tag in ("user_query", "agent_output", "tool_actions", "steps", "file_mutations", "verification_actions")}
            names = [name.strip() for name in keys.split(",") if name.strip()]
            if not names or len(set(names)) != len(names):
                raise ValueError("judge metric keys must be nonempty and unique")
            response = AIMessage(content=json.dumps({"metrics": [_judge_metric(materials, name) for name in names]}, ensure_ascii=False))
        return GenericFakeChatModel(messages=iter([response])).invoke(messages), 0
