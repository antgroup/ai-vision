"""Literal native Code wire, HTTP and OTLP contracts, independent of the plugin."""
from copy import deepcopy
import hashlib
import json
import unittest

from kimi_source import assess_kimi_source, bounded_kimi_row, kimi_matches
from native_capture_fixture import captured_native


def seal(report):
    details = report["result"]["details"]
    text = "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in details["kimi_wire"])
    details["kimi_wire_text"] = text
    details["kimi_wire_sha256"] = hashlib.sha256(text.encode()).hexdigest()


def recapture(report):
    flat = {key: [row for export in report["exports"] for row in export[key]] for key in ("spans", "logs", "metrics")}
    report["exports"] = captured_native(flat)


def proof(scenario="tool-loop"):
    session = "session_native_kimi"
    model = "native-e2e-chat"
    resource = {"ant.agent.type": "kimicli", "ant.agent.product": "KimiCLI"}
    wire = [{"type": "metadata", "protocol_version": "1.5", "created_at": 1000}]
    requests, spans, logs = [], [], []
    signature = [{"name": "Read", "description": "Read file", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}]
    system = "Read the requested file."
    wire.append({"type": "profile.bind", "agentId": "main", "time": 1000, "systemPrompt": system})
    tools_hash = hashlib.sha256(json.dumps(signature, separators=(",", ":")).encode()).hexdigest()
    wire.append({"type": "llm.tools_snapshot", "agentId": "main", "time": 1001, "hash": tools_hash, "tools": signature})
    history = [{"role": "system", "content": system}]
    cancelled = scenario == "cancel"
    rounds = 2 if scenario == "multi-turn" else 1
    total_input = total_output = cache_read = 0
    for turn in range(rounds):
        root_id = f"{turn + 1:016x}"
        prompt = "Read receipt.txt" if turn == 0 else "Read receipt.txt again"
        user = {"role": "user", "content": [{"type": "text", "text": prompt}]}
        wire += [{"type": "turn.prompt", "agentId": "main", "turnId": turn, "promptId": f"prompt-{turn}",
                  "input": user["content"], "origin": {"kind": "user"}, "time": 1100 + turn * 1000},
                 {"type": "context.append_message", "agentId": "main", "message": {**user, "toolCalls": []}, "time": 1101 + turn * 1000}]
        common = {"gen_ai.conversation.id": session, "gen_ai.chat.id": f"{session}-turn-{turn+1}"}
        root = {"trace_id": f"{turn+1:032x}", "span_id": root_id, "parent_span_id": "", "scope_name": "kimihooks",
                "resource": resource, "start_time_unix_nano": (1100 + turn * 1000) * 1000000,
                "end_time_unix_nano": int(((1400 if cancelled else 1320) + turn * 1000) / 1000.0 * 1000000000), "status": {"code": 2 if cancelled or scenario == "tool-error" else 1},
                "attributes": {**common, "gen_ai.span.kind": "CHAT", "agentic.turn.number": turn + 1, "gen_ai.input.messages": json.dumps([{"role": "user", "content": prompt}]), "gen_ai.output.messages": json.dumps([{"role": "assistant", "content": "pending " if cancelled else "receipt: abc123"}])}}
        spans.append(root)
        history.append({"role": "user", "content": prompt})
        tool_id = f"tool-{turn}"
        tool_call = {"id": tool_id, "type": "function", "function": {"name": "Read", "arguments": '{"path":"receipt.txt"}'}}
        tool_output = "file not found" if scenario == "tool-error" else "1\treceipt: abc123"
        model_tool_output = ("<system>ERROR: Tool execution failed.</system>\n" if scenario == "tool-error" else "") + tool_output
        for step in range(1, 2 if cancelled else 3):
            index = len(requests); call_id = f"step-{turn}-{step}"; start_ms = 1200 + turn * 1000 + (step - 1) * 100
            wire += [{"type": "context.append_loop_event", "agentId": "main", "time": start_ms - 1,
                      "event": {"type": "step.begin", "uuid": call_id, "turnId": str(turn), "step": step}},
                     {"type": "llm.request", "agentId": "main", "time": start_ms, "kind": "loop", "provider": "openai",
                      "model": model, "turnStep": f"{turn}.{step}", "messageCount": len(history)-1,
                      "systemPromptHash": hashlib.sha256(system.encode()).hexdigest(), "toolsHash": tools_hash}]
            raw_usage = None if scenario == "unknown-usage" or cancelled else {
                "prompt_tokens": 0 if scenario == "zero-usage" else 16,
                "completion_tokens": 0 if scenario == "zero-usage" else 4,
                "total_tokens": 0 if scenario == "zero-usage" else 20}
            native_usage = {"inputOther": 0, "output": 0, "inputCacheRead": 0, "inputCacheCreation": 0}
            if raw_usage is not None:
                native_usage.update(inputOther=raw_usage["prompt_tokens"], output=raw_usage["completion_tokens"])
                if scenario == "cache-usage":
                    raw_usage["prompt_tokens_details"] = {"cached_tokens": 3, "cache_write_tokens": 2}
                    raw_usage["completion_tokens_details"] = {"reasoning_tokens": 1}
                    native_usage.update(inputOther=13, inputCacheRead=3)
                wire.append({"type": "usage.record", "agentId": "main", "time": start_ms + 10,
                    "model": model, "usageScope": "turn", "usage": deepcopy(native_usage)})
                total_input += native_usage["inputOther"]; total_output += native_usage["output"]; cache_read += native_usage["inputCacheRead"]
            if scenario == "unknown-usage":
                wire.append({"type": "usage.record", "agentId": "main", "time": start_ms+10, "model": model, "usageScope": "turn", "usage": deepcopy(native_usage)})
            response = {"role": "assistant", "content": None, "tool_calls": [tool_call]} if step == 1 else {"role": "assistant", "content": "receipt: abc123"}
            inputs = [user] if step == 1 else [{"role": "tool", "tool_call_id": tool_id, "content": model_tool_output}]
            requests.append({"number": index + 1, "status": 200, "purpose": "agent", "request": {"model": model,
                "stream": True, "messages": deepcopy(history), "tools": [{"type": "function", "function": t} for t in signature]},
                "response": response, "usage": raw_usage, "usage_reported": raw_usage is not None, **({"client_disconnected": True} if cancelled else {"finish_reason": "tool_calls" if step == 1 else "stop"})})
            if cancelled:
                wire.append({"type":"turn.cancel", "agentId":"main", "turnId":turn, "target":"active", "reason":"user_cancelled", "time":start_ms+4})
                output = "pending "
                wire.append({"type": "context.append_loop_event", "agentId": "main", "time": start_ms + 5,
                    "event": {"type": "content.part", "uuid": "partial", "turnId": str(turn), "step": step,
                              "stepUuid": call_id, "part": {"type": "text", "text": output}}})
                finish = {"type": "step.end", "uuid": call_id, "turnId": str(turn), "step": step, "finishReason": "interrupted"}
            else:
                output = "" if step == 1 else "receipt: abc123"
                if step == 1:
                    wire += [{"type": "context.append_loop_event", "agentId": "main", "time": start_ms + 11,
                              "event": {"type": "tool.call", "uuid": f"tool-event-{turn}", "turnId": str(turn), "step": step,
                                        "stepUuid": call_id, "toolCallId": tool_id, "name": "Read", "args": {"path": "receipt.txt"}}},
                             {"type": "context.append_loop_event", "agentId": "main", "time": start_ms + 15,
                              "event": {"type": "tool.result", "parentUuid": f"tool-event-{turn}", "toolCallId": tool_id,
                                        "result": {"output": tool_output, "isError": scenario == "tool-error", "message": tool_output if scenario == "tool-error" else ""}}}]
                else:
                    wire.append({"type": "context.append_loop_event", "agentId": "main", "time": start_ms + 11,
                        "event": {"type": "content.part", "uuid": f"text-{turn}", "turnId": str(turn), "step": step,
                                  "stepUuid": call_id, "part": {"type": "text", "text": output}}})
                finish = {"type": "step.end", "uuid": call_id, "turnId": str(turn), "step": step,
                    "finishReason": "tool_use" if step == 1 else "end_turn", "rawFinishReason": "tool_calls" if step == 1 else "stop",
                    "messageId": f"response-{index}", "usage": deepcopy(native_usage), "llmFirstTokenLatencyMs": 5, "llmStreamDurationMs": 5}
            wire.append({"type": "context.append_loop_event", "agentId": "main", "time": start_ms + 20, "event": finish})
            if cancelled:
                wire += [{"type":"turn.step.interrupted", "agentId":"main", "turnId":turn, "step":step, "reason":"aborted", "time":start_ms+20},
                    {"type":"agent.message.appended", "time":start_ms+19, "kind":"event", "message":{"message":{"role":"assistant", "content":[{"type":"text", "text":output}], "toolCalls":[]}, "meta":{"model":{"provider":"agent-loop", "model":"agent-loop"}, "source":"salvaged", "usage":deepcopy(native_usage)}}},
                    {"type":"agent.turn.ended", "turnId":turn, "outcome":"aborted", "time":start_ms+19, "kind":"event"},
                    {"type":"usage.record", "agentId":"main", "model":model, "usageScope":"turn", "usage":deepcopy(native_usage), "time":start_ms+21}]
            out = {"role": "assistant", "content": output}
            if not cancelled and step == 1: out["tool_calls"] = [tool_call]
            attrs = {**common, "gen_ai.span.kind": "LLM", "agentic.model_call.id": call_id, "agentic.turn.index": turn,
                "agentic.response.index": step-1, "agentic.llm.step": step, "gen_ai.request.model": model,
                "gen_ai.response.model": "unknown", "gen_ai.provider.name": "openai", "gen_ai.system_instructions": json.dumps(system), "agentic.llm.duration_ms": 20 if cancelled else 10,
                "gen_ai.input.messages": json.dumps(inputs), "gen_ai.output.messages": json.dumps([out]),
                "gen_ai.response.finish_reasons": json.dumps([finish["finishReason"]])}
            attrs.update({"agentic.usage.cache_write_tokens.missing": "true", "agentic.usage.reasoning_tokens.missing": "true"})
            if scenario != "cache-usage": attrs["agentic.usage.cache_read_tokens.missing"] = "true"
            if raw_usage is None or scenario == "zero-usage":
                attrs.update({"agentic.usage.input_tokens.missing": "true", "agentic.usage.output_tokens.missing": "true"})
            else:
                attrs.update({"gen_ai.usage.input_tokens": raw_usage["prompt_tokens"], "gen_ai.usage.output_tokens": raw_usage["completion_tokens"],
                              "gen_ai.usage.total_tokens": raw_usage["total_tokens"], "agentic.usage.fresh_input_tokens": native_usage["inputOther"]})
                if scenario == "cache-usage": attrs.update({"gen_ai.usage.cache_read.input_tokens": 3, "agentic.usage.cache_read_tokens": 3})
            if cancelled: attrs["error.type"] = "cancelled"
            else: attrs["gen_ai.response.id"] = finish["messageId"]
            span_id = f"{10+index:016x}"
            span = {"trace_id": root["trace_id"], "span_id": span_id, "parent_span_id": root_id, "resource": resource, "scope_name": "kimihooks",
                    "start_time_unix_nano": start_ms * 1000000, "end_time_unix_nano": int((start_ms / 1000.0 + (0.020 if cancelled else 0.010)) * 1000000000),
                    "status": {"code": 2 if cancelled else 1}, "attributes": attrs}
            spans.append(span)
            if not cancelled:
                history.append(deepcopy(response))
                if step == 1: history.append({"role": "tool", "tool_call_id": tool_id, "content": model_tool_output})
            logs.append({"trace_id": span["trace_id"], "span_id": span_id, "resource": resource, "scope_name": "kimihooks",
                         "attributes": {**attrs, "event.name": "api_request", "duration_ms": attrs["agentic.llm.duration_ms"],
                             **({"error.message": "interrupted"} if cancelled else {})}})
            if step == 1 and not cancelled:
                tool_attrs = {**common, "gen_ai.span.kind": "TOOL", "gen_ai.tool.name": "tool Read", "agentic.tool.raw_name": "Read",
                    "gen_ai.tool.call.id": tool_id, "gen_ai.tool.call.arguments": '{"path":"receipt.txt"}', "gen_ai.tool.call.result": tool_output,
                    "agentic.tool.duration_ms": 4}
                if scenario == "tool-error": tool_attrs["error.type"] = "tool_error"
                tool_span = {**span, "span_id": f"{30+turn:016x}", "start_time_unix_nano": (start_ms+11)*1000000,
                    "end_time_unix_nano": (start_ms+15)*1000000, "attributes": tool_attrs, "status": {"code": 2 if scenario == "tool-error" else 1}}
                spans.append(tool_span)
                logs.append({"trace_id": tool_span["trace_id"], "span_id": tool_span["span_id"], "resource": resource,
                    "scope_name": "kimihooks", "attributes": {**tool_attrs, "event.name": "tool_result", "duration_ms": 4}})
        wire.append({"type": "turn.ended", "agentId": "main", "turnId": turn, "reason": "cancelled" if cancelled else "completed", "time": 1400+turn*1000})
    metrics = [{"name": "agent.api_request.count", "kind": "sum", "temporality": 1, "resource": resource, "attributes": {}, "point": {"asInt": str(len(requests))}}]
    for direction, count in (("input", total_input), ("output", total_output)):
        if count: metrics.append({"name": "agent.token.usage", "kind": "sum", "temporality": 2, "resource": resource,
                                  "attributes": {"type": direction}, "point": {"asInt": str(count)}})
    if cache_read: metrics.append({"name": "agent.cache.read_tokens", "kind": "sum", "temporality": 2, "resource": resource, "attributes": {}, "point": {"asInt": str(cache_read)}})
    for turn in range(rounds):
        for name, bounds, values in (
            ("agent.api_request.duration", [500,1000,2000,5000,10000,20000,30000,60000], [20] if cancelled else [10,10]),
            ("agent.tool_call.duration", [10,50,100,500,1000,5000,10000,30000,60000], [] if cancelled else [4])):
            if not values: continue
            buckets = [0]*(len(bounds)+1)
            for value in values:
                buckets[next((i for i,bound in enumerate(bounds) if value <= bound), len(bounds))] += 1
            metrics.append({"name": name, "kind": "histogram", "temporality": 1 if name == "agent.api_request.duration" else 2, "resource": resource,
                "attributes": {"gen_ai.request.model": model, "gen_ai.provider.name": "openai"} if name == "agent.api_request.duration" else {"gen_ai.conversation.id": session, "agentic.tool.type": "builtin"},
                "point": {"startTimeUnixNano": str((1000+turn*1000)*1000000), "timeUnixNano": str((1500+turn*1000)*1000000),
                    "count": str(len(values)), "sum": sum(values), "min": min(values), "max": max(values),
                    "explicitBounds": bounds, "bucketCounts": [str(v) for v in buckets]}})
    metrics.append({"name": "agent.tool_call.count", "kind": "sum", "temporality": 2, "resource": resource, "attributes": {}, "point": {"asInt": str(0 if cancelled else rounds)}})
    report = {"app": "kimi", "scenario": scenario, "case": "kimi-"+scenario, "mode": "fake", "version": "2.1.1", "model": model,
        "receipt": "abc123", "result": {"version": "2.1.1", "session_id": session, "exit_code": 0, "output": "" if cancelled else "receipt: abc123",
            "details": {"kimi_wire": wire, "native_session_state": {"id": session, "version": 2, "lastTurnReason": "cancelled" if cancelled else "completed"},
                "session_ids": [session]*rounds, "turns_completed": 0 if cancelled else rounds, "cancelled": cancelled,
                "cancel_requested": cancelled, "hook_payloads": [{"hook_event_name": "Interrupt" if cancelled else "Stop", "session_id": session} for _ in range(rounds)]}},
        "requests": requests, "exports": captured_native({"spans": spans, "logs": logs, "metrics": metrics})}
    details = report["result"]["details"]
    details["hook_dispatches"] = []
    for hook in details["hook_payloads"]:
        raw = json.dumps(hook)
        details["hook_dispatches"].append({"stdin_text": raw, "stdin_sha256": hashlib.sha256(raw.encode()).hexdigest(), "exit_code": 0})
    details["cli_events"] = ([{"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": session, "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "pending "}}}}, {"jsonrpc": "2.0", "id": 3, "result": {"stopReason": "cancelled"}}] if cancelled else [{"role": "assistant", "content": "receipt: abc123"}])
    seal(report)
    return json.loads(json.dumps(report))


class KimiSourceTests(unittest.TestCase):
    def test_literal_available_native_scenes(self):
        for scenario in ("tool-loop", "stream", "multi-turn", "tool-error", "unknown-usage", "cancel"):
            with self.subTest(scenario=scenario): self.assertTrue(kimi_matches(proof(scenario))["verified"], kimi_matches(proof(scenario)))

    def test_cache_sdk_loss_does_not_become_strict_provider_reconciliation(self):
        self.assertFalse(kimi_matches(proof("cache-usage"))["verified"])

    def test_bounded_sdk_losses_keep_original_proof_and_strict_false(self):
        for scenario in ("zero-usage", "cache-usage"):
            report = proof(scenario); before = deepcopy(report)
            result = assess_kimi_source(report)
            with self.subTest(scenario=scenario):
                self.assertTrue(result["accepted"], result)
                self.assertTrue(result["canonical_verified"])
                self.assertFalse(result["source"]["verified"])
                self.assertFalse(result["provider_reconciled"])
                self.assertEqual(report, before)
                row = {**result, "case": report["case"], "version": "2.1.1", "verified": False, "source_verified": False}
                self.assertTrue(bounded_kimi_row(row))
                row["version"] = "2.1.2"
                self.assertFalse(bounded_kimi_row(row))

    def test_bounded_does_not_excuse_any_available_fact(self):
        for scenario in ("zero-usage", "cache-usage"):
            for change in ("http_usage", "native_usage", "cache_read", "output", "log_output", "tool_args", "counter", "identity", "version"):
                report = proof(scenario)
                if change == "http_usage": report["requests"][0]["usage"]["prompt_tokens"] = 17
                elif change == "native_usage":
                    next(w for w in report["result"]["details"]["kimi_wire"] if w["type"] == "usage.record")["usage"]["inputOther"] = 99
                    seal(report)
                elif change == "version": report["version"] = "2.1.2"
                elif change == "counter": report["exports"][2]["metrics"][0]["point"]["as_int"] = "99"; recapture(report)
                else:
                    span = report["exports"][0]["spans"][1]
                    if change == "log_output": report["exports"][1]["logs"][0]["attributes"]["gen_ai.output.messages"] = "[]"
                    elif change == "tool_args": report["exports"][0]["spans"][2]["attributes"]["gen_ai.tool.call.arguments"] = "{}"
                    elif change == "cache_read": span["attributes"]["gen_ai.usage.cache_read.input_tokens"] = 999
                    elif change == "identity": span["attributes"]["agentic.model_call.id"] = "wrong"
                    else: span["attributes"]["gen_ai.output.messages"] = "[]"
                    recapture(report)
                with self.subTest(scenario=scenario, change=change): self.assertFalse(assess_kimi_source(report)["accepted"])

    def test_histograms_and_tool_logs_are_native_bound(self):
        for change in ("hist_sum", "hist_bucket", "hist_min", "tool_output", "tool_log_id", "tool_count"):
            report = proof()
            if change.startswith("hist"):
                point = next(m for m in report["exports"][2]["metrics"] if m["name"] == "agent.api_request.duration")["point"]
                if change == "hist_bucket": point["bucketCounts"][0] = "1"; point["bucketCounts"][1] = "1"
                elif change == "hist_min": point["min"] = 9
                else: point["sum"] = 999
            elif change == "tool_count": next(m for m in report["exports"][2]["metrics"] if m["name"] == "agent.tool_call.count")["point"]["as_int"] = "9"
            else:
                log = next(l for l in report["exports"][1]["logs"] if l["attributes"]["event.name"] == "tool_result")
                if change == "tool_log_id": log["span_id"] = "f"*16
                else: log["attributes"]["gen_ai.tool.call.result"] = "wrong"
            recapture(report)
            with self.subTest(change=change): self.assertFalse(kimi_matches(report)["verified"])

    def test_native_metadata_and_exported_shapes_cannot_be_reinterpreted(self):
        for change in ("system_hash", "tools_hash", "message_count", "prompt", "root_output", "raw_tool_shape", "trailing_space", "missing_and_numeric", "fresh_input"):
            report = proof("cache-usage" if change == "fresh_input" else "tool-loop")
            wire = report["result"]["details"]["kimi_wire"]
            if change in ("system_hash", "tools_hash", "message_count"):
                row = next(w for w in wire if w["type"] == "llm.request")
                row[{"system_hash":"systemPromptHash", "tools_hash":"toolsHash", "message_count":"messageCount"}[change]] = 99 if change == "message_count" else "0"*64
                seal(report)
            elif change == "prompt":
                next(w for w in wire if w["type"] == "turn.prompt")["input"][0]["text"] += "wrong"; seal(report)
            else:
                span = report["exports"][0]["spans"][1]
                if change == "root_output": report["exports"][0]["spans"][0]["attributes"]["gen_ai.output.messages"] = "[]"
                elif change == "fresh_input": span["attributes"]["agentic.usage.fresh_input_tokens"] = 16
                elif change == "missing_and_numeric": span["attributes"]["agentic.usage.input_tokens.missing"] = "true"
                else:
                    out = json.loads(span["attributes"]["gen_ai.output.messages"])
                    if change == "raw_tool_shape":
                        tool = out[0]["tool_calls"][0]; out[0]["tool_calls"] = [{"id":tool["id"], **tool["function"]}]
                    else: out[0]["content"] += " "
                    span["attributes"]["gen_ai.output.messages"] = json.dumps(out)
                recapture(report)
            with self.subTest(change=change):
                result = assess_kimi_source(report)["accepted"] if change == "fresh_input" else kimi_matches(report)["verified"]
                self.assertFalse(result)

    def test_bounded_rejects_fabricated_system_and_incomplete_transport(self):
        for change in ("system", "log_system", "incomplete", "disconnect", "float_status"):
            report = proof("cache-usage")
            if change == "system":
                report["exports"][0]["spans"][1]["attributes"]["gen_ai.system_instructions"] = '\"fabricated system prompt\"'; recapture(report)
            elif change == "log_system":
                report["exports"][1]["logs"][0]["attributes"]["gen_ai.system_instructions"] = '\"fabricated system prompt\"'; recapture(report)
            elif change == "incomplete": report["requests"][0]["completed"] = False
            elif change == "disconnect": report["requests"][0]["client_disconnected"] = True
            else: report["requests"][0]["status"] = 200.0
            with self.subTest(change=change): self.assertFalse(assess_kimi_source(report)["accepted"])

    def test_explicit_reportedness_and_turn_number_cannot_conflict(self):
        report = proof("unknown-usage"); report["requests"][0]["usage_reported"] = True
        self.assertFalse(kimi_matches(report)["verified"])
        report = proof("cache-usage")
        report["exports"][0]["spans"][1]["attributes"]["agentic.turn.number"] = 999
        recapture(report)
        self.assertFalse(assess_kimi_source(report)["accepted"])

    def test_success_cannot_export_fabricated_error_classification(self):
        for target in ("llm", "api", "tool"):
            report = proof("cache-usage")
            row = report["exports"][1]["logs"][0] if target == "api" else report["exports"][0]["spans"][1 if target == "llm" else 2]
            row["attributes"]["error.type"] = "fabricated_error"
            recapture(report)
            with self.subTest(target=target): self.assertFalse(assess_kimi_source(report)["accepted"])

    def test_cancel_late_usage_record_is_only_the_adjacent_terminal_zero(self):
        report = proof("cancel"); wire = report["result"]["details"]["kimi_wire"]
        end = next(i for i,w in enumerate(wire) if w.get("event", {}).get("type") == "step.end")
        usage_index = end + 4
        self.assertEqual(wire[usage_index]["type"], "usage.record")
        seal(report)
        self.assertTrue(kimi_matches(report)["verified"], kimi_matches(report))
        for change in ("positive", "duplicate", "after_turn", "wrong_model"):
            other = deepcopy(report); rows = other["result"]["details"]["kimi_wire"]
            if change == "positive": rows[usage_index]["usage"]["output"] = 1
            elif change == "duplicate": rows.insert(usage_index+1, deepcopy(rows[usage_index]))
            elif change == "wrong_model": rows[usage_index]["model"] = "other"
            else: rows.append(rows.pop(usage_index))
            seal(other)
            with self.subTest(change=change): self.assertFalse(kimi_matches(other)["verified"])

    def test_public_profile_system_must_match_hash_http_and_both_signals(self):
        for change in ("profile", "profile_absent", "http", "span", "log"):
            report = proof()
            if change in ("profile", "profile_absent"):
                rows = report["result"]["details"]["kimi_wire"]
                row = next(w for w in rows if w["type"] == "profile.bind")
                if change == "profile_absent": rows.remove(row)
                else: row["systemPrompt"] += "wrong"
                seal(report)
            elif change == "http": report["requests"][0]["request"]["messages"][0]["content"] += "wrong"
            else:
                row = report["exports"][0]["spans"][1] if change == "span" else report["exports"][1]["logs"][0]
                row["attributes"]["gen_ai.system_instructions"] = '\"[UNAVAILABLE]\"'; recapture(report)
            with self.subTest(change=change): self.assertFalse(kimi_matches(report)["verified"])

    def test_stop_root_ends_at_visible_step_end_not_future_turn_end(self):
        report = proof()
        self.assertTrue(kimi_matches(report)["verified"], kimi_matches(report))
        native_end = report["exports"][0]["spans"][0]["end_time_unix_nano"]
        for bad_end in (native_end+1, 1400000000, 1310000000):
            other = deepcopy(report)
            other["exports"][0]["spans"][0]["end_time_unix_nano"] = bad_end
            recapture(other)
            with self.subTest(bad_end=bad_end): self.assertFalse(kimi_matches(other)["verified"])

    def test_native_api_metrics_require_delta_without_changing_tool_metrics(self):
        report = proof()
        self.assertTrue(kimi_matches(report)["verified"], kimi_matches(report))
        for name in ("agent.api_request.count", "agent.api_request.duration"):
            for temporal in (2, 0, True):
                other = deepcopy(report)
                metric = next(m for m in other["exports"][2]["metrics"] if m["name"] == name)
                metric["temporality"] = temporal
                recapture(other)
                with self.subTest(name=name, temporal=temporal): self.assertFalse(kimi_matches(other)["verified"])
        self.assertTrue(all(m["temporality"] == 2 for m in report["exports"][2]["metrics"]
            if m["name"].startswith("agent.tool_call.")))

    def test_cancel_api_error_message_preserves_exact_native_finish_reason(self):
        report = proof("cancel")
        api = next(row for row in report["exports"][1]["logs"] if row["attributes"]["event.name"] == "api_request")
        api["attributes"]["error.message"] = "interrupted"
        recapture(report)
        self.assertTrue(kimi_matches(report)["verified"], kimi_matches(report))
        for value in (None, "", "cancelled", "user_cancelled", "invented provider failure"):
            other = deepcopy(report)
            api = next(row for row in other["exports"][1]["logs"] if row["attributes"]["event.name"] == "api_request")
            if value is None: api["attributes"].pop("error.message")
            else: api["attributes"]["error.message"] = value
            recapture(other)
            with self.subTest(value=value): self.assertFalse(kimi_matches(other)["verified"])
        ordinary = proof()
        ordinary["exports"][1]["logs"][0]["attributes"]["error.message"] = "interrupted"
        recapture(ordinary)
        self.assertFalse(kimi_matches(ordinary)["verified"])

    def test_wire_text_hash_and_parsed_records_are_independent(self):
        for change in ("hash", "text", "record", "session", "end", "hook"):
            report = proof(); details = report["result"]["details"]
            if change == "hash": details["kimi_wire_sha256"] = "0"*64
            elif change == "text": details["kimi_wire_text"] += "{}\n"
            elif change == "record": next(r for r in details["kimi_wire"] if r["type"] == "context.append_message")["message"]["content"] = []
            elif change == "session": details["native_session_state"]["id"] = "other"
            elif change == "end": details["kimi_wire"][-1]["reason"] = "failed"; seal(report)
            else: details["hook_payloads"] = []
            with self.subTest(change=change): self.assertFalse(kimi_matches(report)["verified"])

    def test_resealed_native_call_and_tool_mutations_fail(self):
        for field in ("uuid", "turnId", "step", "args", "toolCallId", "output", "inputOther", "model"):
            report = proof(); wire = report["result"]["details"]["kimi_wire"]
            if field == "model": next(row for row in wire if row["type"] == "llm.request")["model"] = "wrong"
            elif field == "inputOther": next(row for row in wire if row["type"] == "usage.record")["usage"][field] = 99
            elif field == "output": next(row["event"] for row in wire if row.get("event", {}).get("type") == "tool.result")["result"][field] = "wrong"
            else: next(row["event"] for row in wire if row.get("event", {}).get("type") == "tool.call")[field] = {} if field == "args" else "wrong"
            seal(report)
            with self.subTest(field=field): self.assertFalse(kimi_matches(report)["verified"])

    def test_exported_identity_content_usage_and_duration_fail_closed(self):
        for field, value in (("agentic.model_call.id", "other"), ("gen_ai.output.messages", "[]"),
                             ("gen_ai.input.messages", "[]"), ("gen_ai.usage.input_tokens", 99),
                             ("agentic.llm.duration_ms", 99), ("gen_ai.response.id", "other")):
            report = proof(); report["exports"][0]["spans"][1]["attributes"][field] = value; recapture(report)
            with self.subTest(field=field): self.assertFalse(kimi_matches(report)["verified"])
        report = proof(); report["exports"][1]["logs"][0]["span_id"] = "f"*16; recapture(report)
        self.assertFalse(kimi_matches(report)["verified"])


if __name__ == "__main__": unittest.main()
