"""Independent Kimi Code 2.1.1 wire / provider / OTLP reconciliation.

This deliberately reads the durable SDK vocabulary, not the plugin normalizer.
Context messages are deltas. Request hashes/counts prove only the metadata the
SDK actually records; they do not turn those deltas into complete model input.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math

from evidence_integrity import exports_match_raw
from verify import metric_count, token_metric_checks

VERSION = "2.1.1"
SDK_REVISION = "f67e6398fb3210ad8ace970e2dfd5bcc984ed61f"
USAGE_KEYS = {"inputOther", "output", "inputCacheRead", "inputCacheCreation"}
ZERO = dict.fromkeys(USAGE_KEYS, 0)


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _same(a, b):
    return json.dumps(a, sort_keys=True, allow_nan=False) == json.dumps(b, sort_keys=True, allow_nan=False)


def _require(condition, label):
    if not condition:
        raise ValueError(label)


def _count(value):
    return type(value) is int and value >= 0


def _ns(milliseconds):
    # The wire uses integer milliseconds. The native exporter transports them
    # through f64 seconds; reproduce that exact representation, without slack.
    return int((milliseconds / 1000.0) * 1_000_000_000)


def _text(content):
    if isinstance(content, str):
        return content
    _require(isinstance(content, list) and all(isinstance(p, dict) and p.get("type") == "text"
        and isinstance(p.get("text"), str) for p in content), "message.content_shape")
    return "".join(p["text"] for p in content)


def _assistant(message):
    _require(isinstance(message, dict) and message.get("role") == "assistant", "output.role")
    _require(set(message) <= {"role", "content", "tool_calls", "reasoning_content"}, "output.fields")
    content = message.get("content")
    _require(content is None or isinstance(content, str), "output.content")
    tools = message.get("tool_calls", [])
    _require(isinstance(tools, list), "output.tool_calls")
    result = []
    for tool in tools:
        _require(isinstance(tool, dict) and tool.get("type") == "function" and isinstance(tool.get("id"), str)
            and bool(tool["id"]) and isinstance(tool.get("function"), dict), "output.tool_shape")
        function = tool["function"]
        _require(isinstance(function.get("name"), str) and isinstance(function.get("arguments"), str), "output.function")
        args = json.loads(function["arguments"])
        _require(isinstance(args, dict), "output.arguments")
        result.append({"id": tool["id"], "name": function["name"], "args": args})
    return {"content": content or "", "tools": result, "thinking": message.get("reasoning_content", "")}


def _tool_model_content(result):
    # Official contextMemory/toolResultRender.ts; these native E2E cases use
    # Read with a nonempty text output (Read does not render wall time).
    output = result["output"]
    _require(isinstance(output, str) and bool(output), "tool.nonempty_read_output")
    if result.get("isError") is True:
        output = "<system>ERROR: Tool execution failed.</system>\n" + output
    if result.get("note"):
        output += "\n" + result["note"]
    return output


def _native(report):
    result = report["result"]; details = result["details"]; session = result["session_id"]
    _require(report.get("app") == "kimi" and report.get("mode") == "fake"
        and report.get("version") == result.get("version") == VERSION, "version")
    _require(isinstance(session, str) and session and type(result.get("exit_code")) is int
        and result["exit_code"] == 0, "process")
    text = details["kimi_wire_text"]
    _require(isinstance(text, str) and text.endswith("\n") and hashlib.sha256(text.encode()).hexdigest()
        == details["kimi_wire_sha256"], "wire.sha256")
    rows = [json.loads(line) for line in text.splitlines() if line]
    _require(_same(rows, details["kimi_wire"]) and rows[0].get("type") == "metadata"
        and rows[0].get("protocol_version") == "1.5", "wire.records")
    state = details["native_session_state"]
    _require(state.get("id") == session and type(state.get("version")) is int and state["version"] == 2, "session.state")
    cancel = report["scenario"] == "cancel"
    expected_turns = 2 if report["scenario"] == "multi-turn" else 1
    _require(details.get("session_ids") == [session] * expected_turns
        and details.get("turns_completed") == (0 if cancel else expected_turns)
        and details.get("cancel_requested") is cancel and details.get("cancelled") is cancel
        and state.get("lastTurnReason") == ("cancelled" if cancel else "completed"), "session.terminal")
    hooks = details["hook_payloads"]; dispatches = details["hook_dispatches"]
    _require(len(hooks) == len(dispatches) == expected_turns, "hooks.inventory")
    for hook, dispatch in zip(hooks, dispatches):
        _require(hook.get("hook_event_name") == ("Interrupt" if cancel else "Stop")
            and hook.get("session_id") == session and dispatch.get("exit_code") == 0
            and type(dispatch.get("exit_code")) is int, "hooks.identity")
        stdin = dispatch["stdin_text"]
        _require(hashlib.sha256(stdin.encode()).hexdigest() == dispatch.get("stdin_sha256")
            and _same(json.loads(stdin), hook), "hooks.dispatch")
    turns, steps, snapshots = [], [], {}
    current = None; pending = []; active_turn = None; profile_prompt = None; cancel_records = []
    for sequence, row in enumerate(rows[1:], 1):
        typ = row.get("type")
        if typ in {"turn.prompt", "turn.ended", "llm.request", "llm.tools_snapshot", "usage.record", "context.append_message", "context.append_loop_event"}:
            _require(row.get("agentId") == "main" and _count(row.get("time")), "wire.main_time")
        if typ == "profile.bind":
            _require(row.get("agentId") == "main" and isinstance(row.get("systemPrompt"), str), "profile.system_prompt")
            profile_prompt = row["systemPrompt"]
        elif typ == "turn.prompt":
            _require(current is None and active_turn is None and type(row.get("turnId")) is int
                and row["turnId"] == len(turns) and row.get("origin", {}).get("kind") == "user", "turn.begin")
            active_turn = {"begin": row, "steps": []}; turns.append(active_turn)
        elif typ == "turn.cancel":
            _require(cancel and active_turn is not None and current is not None and not cancel_records
                and row.get("agentId") == "main" and row.get("turnId") == active_turn["begin"]["turnId"]
                and row.get("target") == "active" and row.get("reason") == "user_cancelled", "cancel.native_request")
            cancel_records.append(row)
        elif typ == "turn.ended":
            _require(current is None and active_turn is not None and row.get("turnId") == active_turn["begin"]["turnId"]
                and row.get("reason") == ("cancelled" if cancel else "completed"), "turn.end")
            active_turn["end"] = row; active_turn = None
        elif typ == "context.append_message":
            message = row["message"]
            if active_turn is None:
                _require(cancel and turns and "end" in turns[-1] and message.get("role") == "user"
                    and message.get("origin") == {"kind":"injection", "variant":"interruption"}
                    and _text(message.get("content")) == "<system-reminder>\nThe previous turn was interrupted by the user before completion; any partial output shown above is incomplete. The user's next message continues the conversation.\n</system-reminder>", "cancel.unconsumed_context")
                continue
            if message.get("role") in ("user", "tool"):
                item = {"role": message["role"], "content": message["content"]}
                if "toolCallId" in message: item["tool_call_id"] = message["toolCallId"]
                pending.append(item)
        elif typ == "llm.tools_snapshot":
            _require(hashlib.sha256(_json(row["tools"]).encode()).hexdigest() == row["hash"], "request.tools_hash")
            _require(row["hash"] not in snapshots, "request.tools_duplicate")
            snapshots[row["hash"]] = row["tools"]
        elif typ == "llm.request":
            _require(current is not None and "request" not in current and row.get("kind") == "loop"
                and row.get("turnStep") == f'{current["begin"]["turnId"]}.{current["begin"]["step"]}'
                and row.get("provider") == "openai" and row.get("model") == report["model"], "request.identity")
            current["request"] = row
            current["system"] = row.get("systemPrompt", profile_prompt)
            _require(isinstance(current["system"], str) and bool(current["system"]), "request.native_system")
        elif typ == "usage.record":
            usage = row["usage"]
            _require(set(usage) == USAGE_KEYS and all(_count(value) for value in usage.values()), "usage.shape")
            _require(row.get("model") == report["model"] and row.get("usageScope") == "turn", "usage.identity")
            if current is None:
                _require(cancel and active_turn is not None and len(active_turn["steps"]) == 1, "usage.late_turn")
                terminal = active_turn["steps"][-1]
                _require(terminal.get("end", {}).get("finishReason") == "interrupted"
                    and "late_usage" not in terminal and terminal["end_time"] <= row["time"]
                    and _same(usage, ZERO), "usage.late_terminal_zero")
                middle = rows[terminal["end_sequence"]+1:sequence]
                _require(len(middle) == 3 and [item.get("type") for item in middle] ==
                    ["turn.step.interrupted", "agent.message.appended", "agent.turn.ended"], "usage.late_sequence")
                interrupted, salvaged, ended = middle
                _require(interrupted.get("agentId") == "main" and interrupted.get("turnId") == int(terminal["begin"]["turnId"])
                    and interrupted.get("step") == terminal["begin"]["step"] and interrupted.get("reason") == "aborted"
                    and ended.get("turnId") == interrupted["turnId"] and ended.get("outcome") == "aborted", "usage.late_identity")
                message = salvaged["message"]["message"]; meta = salvaged["message"]["meta"]
                _require(message.get("role") == "assistant" and _same(message.get("content"), terminal["parts"])
                    and message.get("toolCalls") == [] and meta.get("source") == "salvaged" and _same(meta.get("usage"), ZERO)
                    and meta.get("model") == {"provider":"agent-loop", "model":"agent-loop"}, "usage.late_salvage")
                terminal["late_usage"] = usage
            else:
                _require("usage" not in current and "request" in current, "usage.identity")
                current["usage"] = usage
        elif typ == "context.append_loop_event":
            event = row["event"]; kind = event["type"]
            if kind == "step.begin":
                _require(current is None and active_turn is not None and event.get("turnId") == str(active_turn["begin"]["turnId"])
                    and event.get("step") == len(active_turn["steps"]) + 1 and type(event.get("step")) is int
                    and isinstance(event.get("uuid"), str) and event["uuid"] and event["uuid"] not in {s["begin"]["uuid"] for s in steps}, "step.begin")
                if event["step"] == 1:
                    _require(any(message.get("role") == "user" and _text(message.get("content")) == _text(active_turn["begin"]["input"]) for message in pending), "turn.prompt_context")
                current = {"begin": event, "time": row["time"], "inputs": pending, "tools": [], "parts": []}
                pending = []; steps.append(current); active_turn["steps"].append(current)
            elif kind in {"tool.call", "content.part", "step.end", "tool.result"}:
                _require(current is not None and "request" in current, "step.event_order")
                begin = current["begin"]
                if kind != "tool.result":
                    _require(event.get("turnId") == begin["turnId"] and event.get("step") == begin["step"], "step.event_identity")
                if kind in {"tool.call", "content.part"}:
                    _require(event.get("stepUuid") == begin["uuid"], "step.event_parent")
                if kind == "tool.call":
                    _require(isinstance(event.get("uuid"), str) and isinstance(event.get("toolCallId"), str)
                        and isinstance(event.get("name"), str) and isinstance(event.get("args"), dict), "tool.shape")
                    current["tools"].append({"call": event, "start": row["time"]})
                elif kind == "tool.result":
                    matches = [t for t in current["tools"] if t["call"]["uuid"] == event.get("parentUuid")
                        and t["call"]["toolCallId"] == event.get("toolCallId")]
                    _require(len(matches) == 1 and "result" not in matches[0], "tool.result_identity")
                    matches[0].update(result=event["result"], end=row["time"])
                    _require(isinstance(event["result"].get("output"), str), "tool.output")
                    pending.append({"role": "tool", "tool_call_id": event["toolCallId"], "content": _tool_model_content(event["result"])})
                elif kind == "content.part":
                    _require(event.get("part", {}).get("type") in ("text", "think"), "output.part")
                    current["parts"].append(event["part"])
                else:
                    _require(event.get("uuid") == begin["uuid"] and all("result" in t for t in current["tools"]), "step.end_identity")
                    if cancel:
                        _require(event.get("finishReason") == "interrupted" and "usage" not in event and "usage" not in current, "step.interrupted")
                    else:
                        _require(_same(event.get("usage"), current.get("usage", ZERO)), "usage.end")
                        _require(event.get("finishReason") in ("tool_use", "end_turn")
                            and isinstance(event.get("messageId"), str) and bool(event["messageId"]), "step.finish")
                        _require(_count(event.get("llmFirstTokenLatencyMs")) and _count(event.get("llmStreamDurationMs")), "step.timing")
                    current.update(end=event, end_time=row["time"], end_sequence=sequence); current = None
    _require(current is None and active_turn is None and not pending and len(turns) == expected_turns
        and all("end" in turn and len(turn["steps"]) > 0 for turn in turns), "turn.inventory")
    if cancel:
        _require(len(cancel_records) == 1 and all("late_usage" in step for step in steps), "cancel.native_completion")
        events = details["cli_events"]
        _require(any(e.get("result", {}).get("stopReason") == "cancelled" for e in events), "cancel.acp_terminal")
        chunks = [e["params"]["update"]["content"]["text"] for e in events
            if e.get("method") == "session/update" and e.get("params", {}).get("sessionId") == session
            and e.get("params", {}).get("update", {}).get("sessionUpdate") == "agent_message_chunk"
            and e["params"]["update"].get("content", {}).get("type") == "text"]
        _require(chunks and "".join(chunks) == "".join(p["text"] for s in steps for p in s["parts"] if p["type"] == "text"), "cancel.acp_content")
    else:
        _require(all(any(e.get("role") == "assistant" and e.get("content") == "receipt: " + report["receipt"]
            for e in details["cli_events"]) for _ in turns), "terminal.output")
    return session, turns, steps, snapshots


def _wire_calls(report, steps, snapshots):
    calls = report["requests"]
    _require(len(calls) == len(steps), "calls.inventory")
    for index, (call, step) in enumerate(zip(calls, steps)):
        request = step["request"]; body = call["request"]
        _require(call.get("number") == index + 1 and type(call.get("number")) is int and call.get("status") == 200
            and type(call.get("status")) is int and call.get("purpose") == "agent" and body.get("model") == request["model"], "http.identity")
        if report["scenario"] != "cancel":
            _require(("completed" not in call or call["completed"] is True)
                and ("client_disconnected" not in call or call["client_disconnected"] is False), "http.completed")
        messages = body["messages"]
        systems = [m for m in messages if m.get("role") == "system"]
        _require(len(systems) == 1 and isinstance(systems[0].get("content"), str)
            and systems[0]["content"] == step["system"]
            and hashlib.sha256(step["system"].encode()).hexdigest() == request.get("systemPromptHash")
            and type(request.get("messageCount")) is int and request["messageCount"] == len(messages) - 1, "request.metadata")
        _require("systemPrompt" not in request or request["systemPrompt"] == systems[0]["content"], "request.system_prompt")
        tools = body.get("tools", [])
        _require(all(t.get("type") == "function" for t in tools)
            and _same([t["function"] for t in tools], snapshots[request["toolsHash"]]), "request.tools")
        for item in step["inputs"]:
            candidates = [m for m in messages if m.get("role") == item["role"] and
                (item["role"] != "tool" or m.get("tool_call_id") == item["tool_call_id"])]
            if item["role"] == "tool":
                source = next(t for prior in steps[:index] for t in prior["tools"] if t["call"]["toolCallId"] == item["tool_call_id"])
                output = _tool_model_content(source["result"])
            else: output = _text(item["content"])
            _require(any(_text(m.get("content")) == output for m in candidates), "request.context_delta")
        native = {"content": "".join(p["text"] for p in step["parts"] if p["type"] == "text"),
            "thinking": "".join(p["think"] for p in step["parts"] if p["type"] == "think"),
            "tools": [{"id": t["call"]["toolCallId"], "name": t["call"]["name"], "args": t["call"]["args"]} for t in step["tools"]]}
        step["output"] = native
        if report["scenario"] != "cancel":
            _require(_same(_assistant(call["response"]), native) and call.get("finish_reason") == step["end"].get("rawFinishReason"), "response.content_finish")
        else:
            _require("finish_reason" not in call and call.get("usage") is None and bool(native["content"]), "response.cancelled")
        usage = call.get("usage")
        _require(call.get("usage_reported") is (usage is not None), "usage.reportedness_record")
        if usage is None:
            _require("usage" not in step or _same(step["usage"], ZERO), "usage.absence")
        else:
            _require(isinstance(usage, dict) and all(_count(usage.get(k)) for k in ("prompt_tokens", "completion_tokens", "total_tokens")), "usage.provider_shape")
            read = (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
            _require(_count(read) and usage["prompt_tokens"] >= read and usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"], "usage.provider_total")
            expected = {"inputOther": usage["prompt_tokens"] - read, "output": usage["completion_tokens"], "inputCacheRead": read, "inputCacheCreation": 0}
            _require(_same(step.get("usage"), expected), "usage.native_projection")
            _require(expected != ZERO, "usage.sdk_zero_reportedness_unavailable")
            _require("cache_write_tokens" not in (usage.get("prompt_tokens_details") or {})
                and "reasoning_tokens" not in (usage.get("completion_tokens_details") or {}), "usage.sdk_detail_unavailable")


def _usage(attrs, step):
    usage = step.get("usage")
    if usage == ZERO: usage = None
    allowed_values = {"gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens", "gen_ai.usage.total_tokens",
        "gen_ai.usage.cache_read.input_tokens", "agentic.usage.cache_read_tokens", "agentic.usage.fresh_input_tokens"}
    for key, value in attrs.items():
        if key.startswith(("gen_ai.usage.", "agentic.usage.")):
            if key.endswith(".missing"):
                _require(key.startswith("agentic.usage.") and key.split(".")[-2] in
                    {"input_tokens", "output_tokens", "total_tokens", "fresh_input_tokens", "cache_read_tokens", "cache_write_tokens", "reasoning_tokens", "tool_tokens"}
                    and value == "true" and type(value) is str, "signal.usage_marker")
            else:
                _require(key in allowed_values and type(value) is int and value >= 0 and usage is not None, "signal.usage_numeric")
    fresh = attrs.get("agentic.usage.fresh_input_tokens")
    _require((fresh is None if usage is None else type(fresh) is int and fresh == usage["inputOther"]), "signal.fresh_input")
    for suffix, value in (("input_tokens", None if usage is None else usage["inputOther"] + usage["inputCacheRead"]),
                          ("output_tokens", None if usage is None else usage["output"]),
                          ("total_tokens", None if usage is None else usage["inputOther"] + usage["inputCacheRead"] + usage["output"])):
        key = "gen_ai.usage." + suffix
        if value is None:
            _require(key not in attrs, "signal.usage_unknown")
            if suffix != "total_tokens": _require(attrs.get("agentic.usage." + suffix + ".missing") == "true", "signal.usage_missing")
        else:
            _require(type(attrs.get(key)) is int and attrs[key] == value
                and "agentic.usage." + suffix + ".missing" not in attrs, "signal.usage_value")
    read = None if usage is None or usage["inputCacheRead"] == 0 else usage["inputCacheRead"]
    for key in ("gen_ai.usage.cache_read.input_tokens", "agentic.usage.cache_read_tokens"):
        _require((key not in attrs if read is None else type(attrs.get(key)) is int and attrs[key] == read), "signal.cache_read")
    for key in ("gen_ai.usage.cache_creation.input_tokens", "gen_ai.usage.reasoning.output_tokens", "agentic.usage.cache_write_tokens", "agentic.usage.reasoning_tokens"):
        _require(key not in attrs, "signal.unavailable_detail")
    for suffix in ("cache_write_tokens", "reasoning_tokens"):
        _require(attrs.get("agentic.usage." + suffix + ".missing") == "true", "signal.detail_missing")
    if read is None: _require(attrs.get("agentic.usage.cache_read_tokens.missing") == "true", "signal.cache_read_missing")
    else: _require("agentic.usage.cache_read_tokens.missing" not in attrs, "signal.cache_read_present")


def _duration(step):
    if step["end"]["finishReason"] != "interrupted":
        return step["end"]["llmFirstTokenLatencyMs"] + step["end"]["llmStreamDurationMs"]
    return int((step["end_time"] / 1000.0 - step["request"]["time"] / 1000.0) * 1000)


def _histograms(metrics, turns, model, session):
    def field(point, snake, camel):
        _require(not (snake in point and camel in point), "metrics.histogram_alias")
        return point.get(snake, point.get(camel))
    for name in ("agent.api_request.duration", "agent.tool_call.duration"):
        rows = [m for m in metrics if m.get("name") == name]
        samples = [[_duration(s) for s in t["steps"]] if name == "agent.api_request.duration" else
            [(_ns(tool["end"]) - _ns(tool["start"])) // 1000000 for s in t["steps"] for tool in s["tools"]] for t in turns]
        samples = [values for values in samples if values]
        _require(len(rows) == len(samples), "metrics.histogram_inventory")
        rows.sort(key=lambda m: int(field(m["point"], "time_unix_nano", "timeUnixNano") or 0))
        for row, values in zip(rows, samples):
            point = row["point"]; attrs = row["attributes"]
            temporal = (1, "AGGREGATION_TEMPORALITY_DELTA") if name == "agent.api_request.duration" else (2, "AGGREGATION_TEMPORALITY_CUMULATIVE")
            _require(row.get("kind") == "histogram" and type(row.get("temporality")) is not bool
                and row.get("temporality") in temporal, "metrics.histogram_kind")
            if name == "agent.api_request.duration":
                _require(attrs.get("gen_ai.request.model") == model and attrs.get("gen_ai.provider.name") == "openai", "metrics.histogram_identity")
            else:
                _require(attrs.get("gen_ai.conversation.id") == session and attrs.get("agentic.tool.type") == "builtin", "metrics.histogram_identity")
            bounds = field(point, "explicit_bounds", "explicitBounds")
            buckets = field(point, "bucket_counts", "bucketCounts")
            _require(isinstance(bounds, list) and bounds and all(type(b) in (int, float) and math.isfinite(b) for b in bounds)
                and all(a < b for a, b in zip(bounds, bounds[1:])) and isinstance(buckets, list) and len(buckets) == len(bounds) + 1, "metrics.histogram_shape")
            expected = [0] * (len(bounds) + 1)
            for value in values:
                expected[next((i for i, bound in enumerate(bounds) if value <= bound), len(bounds))] += 1
            _require(all(type(value) is int or isinstance(value, str) and value.isdecimal() for value in buckets)
                and [int(value) for value in buckets] == expected
                and str(point.get("count")) == str(len(values)) and type(point.get("count")) is not bool
                and type(point.get("sum")) in (int, float) and point["sum"] == sum(values)
                and type(point.get("min")) in (int, float) and point["min"] == min(values)
                and type(point.get("max")) in (int, float) and point["max"] == max(values), "metrics.histogram_values")


def _signals(report, session, turns, steps):
    exports = report["exports"]
    _require(exports_match_raw(exports), "capture.otlp")
    spans = [s for e in exports for s in e["spans"]]; logs = [s for e in exports for s in e["logs"]]
    llms = [s for s in spans if s["attributes"].get("gen_ai.span.kind") == "LLM"]
    roots = [s for s in spans if s["attributes"].get("gen_ai.span.kind") == "CHAT"]
    tools = [s for s in spans if s["attributes"].get("gen_ai.span.kind") == "TOOL"]
    apis = [s for s in logs if s["attributes"].get("event.name") == "api_request"]
    tool_logs = [s for s in logs if s["attributes"].get("event.name") == "tool_result"]
    _require(len(tool_logs) == len(tools), "signal.tool_logs_inventory")
    _require(len(llms) == len(apis) == len(steps) and len(roots) == len(turns)
        and len(tools) == sum(len(s["tools"]) for s in steps), "signal.inventory")
    for number, turn in enumerate(turns):
        matches = [s for s in roots if s["attributes"].get("gen_ai.chat.id") == f"{session}-turn-{number+1}"]
        _require(len(matches) == 1, "signal.root_inventory")
        root = matches[0]; attrs = root["attributes"]
        # Official onDidFinishStep awaits Stop before endTurn dispatches
        # turn.ended. The same step.end supplies the exported response ID and
        # usage, proving it was visible to the hook. Interrupt is dispatched after
        # TurnEnded, so that path can report the actual terminal turn time.
        root_end = turn["end"]["time"] if turn["end"]["reason"] == "cancelled" else turn["steps"][-1]["end_time"]
        _require(attrs.get("gen_ai.conversation.id") == session and type(attrs.get("agentic.turn.number")) is int
            and attrs["agentic.turn.number"] == number+1 and not root.get("parent_span_id")
            and root.get("start_time_unix_nano") == _ns(turn["begin"]["time"])
            and root.get("end_time_unix_nano") == _ns(root_end), "signal.root_identity_time")
        _require(_same(json.loads(attrs["gen_ai.input.messages"]), [{"role": "user", "content": _text(turn["begin"]["input"])}]), "signal.root_input")
        expected_output = [{"content": step["output"]["content"], "thinking": step["output"]["thinking"], "tools": []}
            for step in turn["steps"] if step["output"]["content"] or step["output"]["thinking"]]
        _require(_same([_assistant(message) for message in json.loads(attrs["gen_ai.output.messages"])], expected_output), "signal.root_output")
        failed = turn["end"]["reason"] == "cancelled" or any(tool["result"].get("isError") for step in turn["steps"] for tool in step["tools"])
        _require(root.get("status", {}).get("code") == (2 if failed else 1), "signal.root_status")
    for step in steps:
        begin = step["begin"]; number = int(begin["turnId"])
        root = [s for s in roots if s["attributes"].get("gen_ai.chat.id") == f"{session}-turn-{number+1}"]
        span = [s for s in llms if s["attributes"].get("agentic.model_call.id") == begin["uuid"]]
        _require(len(root) == len(span) == 1, "signal.identity")
        root, span = root[0], span[0]
        api = [s for s in apis if s.get("trace_id") == span["trace_id"] and s.get("span_id") == span["span_id"]]
        _require(len(api) == 1 and span["trace_id"] == root["trace_id"] and span.get("parent_span_id") == root["span_id"], "signal.parent")
        start_ms = step["request"]["time"]
        complete = step["end"]["finishReason"] != "interrupted"
        duration = _duration(step)
        end_ns = int((start_ms / 1000.0 + duration / 1000.0) * 1_000_000_000) if complete else _ns(step["end_time"])
        _require(span.get("start_time_unix_nano") == _ns(start_ms) and span.get("end_time_unix_nano") == end_ns
            and span.get("status", {}).get("code") == (1 if complete else 2), "signal.timing_status")
        for row in (span, api[0]):
            attrs = row["attributes"]
            _require(all(key not in attrs or type(attrs[key]) is int and attrs[key] == value for key, value in
                (("agentic.turn.number", number+1), ("agentic.turn.index", number), ("agentic.response.index", begin["step"]-1), ("agentic.llm.step", begin["step"]))), "signal.explicit_turn_identity")
            _require(attrs.get("agentic.model_call.id") == begin["uuid"] and attrs.get("gen_ai.conversation.id") == session
                and attrs.get("gen_ai.chat.id") == f"{session}-turn-{number+1}"
                and attrs.get("gen_ai.request.model") == step["request"]["model"]
                and attrs.get("gen_ai.provider.name") == step["request"]["provider"]
                and attrs.get("gen_ai.response.model") == "unknown", "signal.request_identity")
            _require(json.loads(attrs["gen_ai.system_instructions"]) == step["system"], "signal.system_source")
            _require(_same(json.loads(attrs["gen_ai.input.messages"]), step["inputs"]), "signal.input_delta")
            output = json.loads(attrs["gen_ai.output.messages"])
            _require(isinstance(output, list) and len(output) == 1 and _same(_assistant(output[0]), step["output"]), "signal.output")
            duration_key = "agentic.llm.duration_ms" if row is span else "duration_ms"
            _require(type(attrs.get(duration_key)) is int and attrs[duration_key] == duration, "signal.duration")
            if complete:
                _require("error.type" not in attrs and "error.message" not in attrs, "signal.success_error")
                _require(attrs.get("gen_ai.response.id") == step["end"]["messageId"], "signal.response_id")
            else:
                _require("gen_ai.response.id" not in attrs and attrs.get("error.type") == "cancelled", "signal.cancel")
                if row is not span:
                    _require(attrs.get("error.message") == step["end"]["finishReason"], "signal.cancel_error_message")
            _usage(attrs, step)
        attrs = span["attributes"]
        _require(attrs.get("agentic.turn.index") == number and attrs.get("agentic.response.index") == begin["step"] - 1
            and attrs.get("agentic.llm.step") == begin["step"]
            and json.loads(attrs["gen_ai.response.finish_reasons"]) == [step["end"]["finishReason"]], "signal.finish_identity")
        for tool in step["tools"]:
            call = tool["call"]; result = tool["result"]
            matches = [s for s in tools if s["attributes"].get("gen_ai.tool.call.id") == call["toolCallId"]]
            _require(len(matches) == 1, "signal.tool_inventory")
            tool_span = matches[0]; attrs = tool_span["attributes"]
            _require(attrs.get("error.type") == ("tool_error" if result.get("isError") else None), "signal.tool_error")
            _require(tool_span["trace_id"] == span["trace_id"] and tool_span.get("parent_span_id") == root["span_id"]
                and attrs.get("gen_ai.conversation.id") == session and attrs.get("gen_ai.chat.id") == f"{session}-turn-{number+1}"
                and attrs.get("gen_ai.tool.name") == "tool " + call["name"]
                and _same(json.loads(attrs["gen_ai.tool.call.arguments"]), call["args"])
                and attrs.get("gen_ai.tool.call.result") == result["output"], "signal.tool_content")
            _require(tool_span.get("start_time_unix_nano") == _ns(tool["start"])
                and tool_span.get("end_time_unix_nano") == _ns(tool["end"])
                and attrs.get("agentic.tool.duration_ms") == (_ns(tool["end"]) - _ns(tool["start"])) // 1000000
                and tool_span.get("status", {}).get("code") == (2 if result.get("isError") else 1), "signal.tool_status_time")
            matching_logs = [log for log in tool_logs if log.get("trace_id") == tool_span["trace_id"] and log.get("span_id") == tool_span["span_id"]]
            _require(len(matching_logs) == 1, "signal.tool_log_identity")
            event = matching_logs[0]["attributes"]
            for key in ("gen_ai.conversation.id", "gen_ai.chat.id", "gen_ai.tool.call.id", "gen_ai.tool.name", "gen_ai.tool.call.result"):
                _require(event.get(key) == attrs.get(key), "signal.tool_log_content")
            _require(_same(json.loads(event["gen_ai.tool.call.arguments"]), call["args"])
                and event.get("duration_ms") == attrs["agentic.tool.duration_ms"]
                and event.get("error.type") == ("tool_error" if result.get("isError") else None), "signal.tool_log_status")
    metrics = [m for e in exports for m in e["metrics"]]
    api_counts = [row for row in metrics if row.get("name") == "agent.api_request.count"]
    _require(bool(api_counts) and all(row.get("kind") == "sum" and type(row.get("temporality")) is not bool
        and row.get("temporality") in (1, "AGGREGATION_TEMPORALITY_DELTA") for row in api_counts), "metrics.calls_delta")
    _require(metric_count(metrics, "agent.api_request.count") == len(steps), "metrics.calls")
    _require(token_metric_checks(metrics, report["requests"]), "metrics.usage")
    _require(metric_count(metrics, "agent.tool_call.count") == len(tools), "metrics.tools")
    _histograms(metrics, turns, report["model"], session)


def kimi_matches(report):
    """Return precise failed source checks; never normalize away a mismatch."""
    checks = {}; failure = None
    try:
        session, turns, steps, snapshots = _native(report)
        checks["kimi.source.native"] = True
        _wire_calls(report, steps, snapshots)
        checks["kimi.source.provider"] = True
        _signals(report, session, turns, steps)
        checks["kimi.source.signals"] = True
    except (KeyError, ValueError, TypeError, AttributeError, IndexError, StopIteration, OverflowError) as error:
        failure = str(error) if isinstance(error, ValueError) else type(error).__name__ + ":" + str(error)
    if failure:
        checks["kimi.source." + failure] = False
    return {"verified": failure is None, "checks": checks, "failures": [key for key, ok in checks.items() if not ok]}


LIMITATIONS = {
    "zero-usage": "kimi.host.zero_usage_reportedness_unavailable",
    "cache-usage": "kimi.host.cache_write_reasoning_unavailable",
}


def _arguments(report, requests=None):
    return {"app": report["app"], "scenario": report["scenario"], "model": report["model"],
        "receipt": report["receipt"], "result": report["result"],
        "requests": report["requests"] if requests is None else requests,
        "exports": report["exports"], "expected_resource": report.get("expected_resource"), "mode": report["mode"]}


def _limitation(scenario):
    return {"id": LIMITATIONS[scenario], "app": "kimi", "version": VERSION, "scenario": scenario, "calls": [0, 1]}


def assess_kimi_source(report, strict_result=None):
    """Bound only experimentally demonstrated SDK losses; keep strict false.

    The original provider facts, raw transcript and OTLP remain untouched.
    A canonical copy changes only usage fields the pinned SDK cannot expose,
    then repeats every shared and independent source check on that copy.
    """
    from verify import verify_case
    try:
        strict = deepcopy(strict_result) if strict_result is not None else verify_case(**_arguments(report))
    except (KeyError, TypeError, ValueError, AttributeError):
        strict = {"verified": False, "checks": {}, "failures": ["source.missing_or_invalid_evidence"]}
    answer = {"source": strict, "provider_reconciled": False, "canonical_verified": False,
        "known_limitations": [], "accepted": False, "status": "failed"}
    try:
        scenario = report["scenario"]
        _require(strict.get("verified") is False and scenario in LIMITATIONS and report.get("case") == "kimi-" + scenario
            and report.get("model") == "native-e2e-chat" and report.get("mode") == "fake"
            and report.get("version") == report["result"].get("version") == VERSION, "bounded.scope")
        session, turns, steps, snapshots = _native(report)
        _require(len(report["requests"]) == len(steps) == 2 and len(turns) == 1
            and len(steps[0]["tools"]) == 1 and not steps[1]["tools"]
            and steps[0]["tools"][0]["call"]["name"] == "Read"
            and [s["end"]["finishReason"] for s in steps] == ["tool_use", "end_turn"], "bounded.inventory")
        expected = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0} if scenario == "zero-usage" else {
            "prompt_tokens": 16, "completion_tokens": 4, "total_tokens": 20,
            "prompt_tokens_details": {"cached_tokens": 3, "cache_write_tokens": 2},
            "completion_tokens_details": {"reasoning_tokens": 1}}
        expected_native = ZERO if scenario == "zero-usage" else {"inputOther": 13, "output": 4, "inputCacheRead": 3, "inputCacheCreation": 0}
        for call, step in zip(report["requests"], steps):
            _require(_same(call.get("usage"), expected) and call.get("usage_reported") is True
                and _same(step.get("usage"), expected_native) and _same(step["end"].get("usage"), expected_native), "bounded.raw_usage")
        projected = deepcopy(report["requests"])
        for call in projected:
            if scenario == "zero-usage":
                call["usage"] = None
                call["usage_reported"] = False
            else:
                del call["usage"]["prompt_tokens_details"]["cache_write_tokens"]
                del call["usage"]["completion_tokens_details"]
        canonical_report = {**report, "requests": projected}
        native = kimi_matches(canonical_report)
        arguments = _arguments(report, projected)
        arguments["scenario"] = "tool-loop"
        canonical = verify_case(**arguments)
        canonical["checks"].update(native["checks"])
        canonical["failures"] = [name for name, passed in canonical["checks"].items() if not passed]
        canonical["verified"] = not canonical["failures"]
        answer.update(canonical=canonical, canonical_verified=canonical["verified"])
        if canonical["verified"]:
            answer.update(accepted=True, status="passed_with_known_limitations", known_limitations=[_limitation(scenario)])
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError, IndexError) as error:
        answer["source_diagnostic"] = str(error)
    return answer


def bounded_kimi_row(row):
    scenario = row.get("case", "").removeprefix("kimi-")
    return (scenario in LIMITATIONS and row.get("case") == "kimi-" + scenario and row.get("version") == VERSION
        and row.get("status") == "passed_with_known_limitations" and row.get("accepted") is True
        and row.get("verified") is False and row.get("source_verified") is False
        and row.get("provider_reconciled") is False and row.get("canonical_verified") is True
        and _same(row.get("known_limitations"), [_limitation(scenario)]))
