"""Independent literal facts for the single bounded Hermes auxiliary loss."""
from copy import deepcopy
import hashlib
import json
import unittest

from native_capture_fixture import captured_native
from hermes_source import assess_hermes_source, bounded_hermes_row


def rechain(report):
    previous = "0" * 64
    for number, row in enumerate(report["result"]["details"]["hermes_raw_callbacks"], 1):
        row["integrity"] = {"sequence": number, "previousChecksum": previous}
        previous = hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False,
            separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        row["integrity"]["checksum"] = previous


def recapture(report):
    flat = {key: [row for export in report["exports"] for row in export[key]]
            for key in ("spans", "logs", "metrics")}
    report["exports"] = captured_native(flat)


def proof():
    # No production normalizer, recorder, source artifact, or runtime required.
    session, turn, task = "session", "turn", "task"
    model = "native-e2e-chat"
    zero = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    normalized = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0,
                  "reasoning_tokens": 0, "request_count": 1, "prompt_tokens": 0, "total_tokens": 0}
    resource = {"ant.agent.type": "hermes", "ant.agent.product": "Hermes"}
    tool = {"id": "read-1", "type": "function", "function": {"name": "read_file", "arguments": '{"path":"receipt.txt"}'}}
    messages = [[{"role": "user", "content": "Read receipt.txt"}],
                [{"role": "tool", "tool_call_id": "read-1", "content": "receipt: abc123"}],
                [{"role": "system", "content": "Name this session"}, {"role": "user", "content": "Read receipt.txt"}]]
    responses = [{"role": "assistant", "content": None, "tool_calls": [tool]},
                 {"role": "assistant", "content": "receipt: abc123"},
                 {"role": "assistant", "content": "Native E2E session"}]
    requests = [{"number": i + 1, "purpose": "auxiliary" if i == 2 else "agent", "status": 200,
                 "usage_reported": True, "finish_reason": "tool_calls" if i == 0 else "stop",
                 "request": {"model": model, "messages": messages[i]},
                 "response": responses[i], "usage": deepcopy(zero)} for i in range(3)]
    common = {"gen_ai.conversation.id": session, "agentic.turn.id": turn, "agentic.turn.number": 1}
    def span(identifier, kind, attrs, start=1750000000000000000, end=1750000001000000000):
        return {"trace_id": "a" * 32, "span_id": identifier * 16, "parent_span_id": "" if kind == "CHAT" else "1" * 16,
                "start_time_unix_nano": start, "end_time_unix_nano": end,
                "scope_name": "observ-hermes", "resource": resource, "status": {"code": 1},
                "attributes": {**common, "gen_ai.span.kind": kind, **attrs}}
    spans = [span("1", "CHAT", {})]
    logs = []
    for i, native_id in enumerate(("api-1", "api-2", "aux-1")):
        attrs = {"agentic.model_call.id": native_id, "gen_ai.request.model": model, "gen_ai.response.model": model,
                 "gen_ai.provider.name": "custom", "gen_ai.input.messages": json.dumps(messages[i]),
                 "gen_ai.output.messages": json.dumps([responses[i]]),
                 "gen_ai.response.finish_reasons": json.dumps([requests[i]["finish_reason"]])}
        if i < 2:
            attrs.update({"gen_ai.usage.input_tokens": 0, "gen_ai.usage.output_tokens": 0})
        else:
            attrs.update({"gen_ai.agent.name": "title_generation", "agentic.llm.retry_count": 0,
                          "agentic.llm.duration_ms": 1000.0, "gen_ai.system_instructions": json.dumps("Name this session"),
                          "agentic.usage.input_tokens.missing": "true", "agentic.usage.output_tokens.missing": "true"})
            attrs["gen_ai.output.messages"] = json.dumps([{**responses[i], "tool_calls": []}])
        item = span(str(i + 2), "LLM", attrs, 1750000000000000000 + i * 2000000000,
                    1750000001000000000 + i * 2000000000)
        spans.append(item)
        log_attrs = {**common, **attrs, "event.name": "api_request"}
        if i == 2: log_attrs["duration_ms"] = 1000.0
        log_attrs.pop("gen_ai.agent.name", None)
        if i < 2:
            log_attrs.update({"gen_ai.usage.total_tokens": 0, "agentic.usage.fresh_input_tokens": 0})
        logs.append({"trace_id": item["trace_id"], "span_id": item["span_id"], "resource": resource,
                     "scope_name": "observ-hermes", "attributes": log_attrs})
    spans.append(span("5", "TOOL", {"gen_ai.tool.name": "read_file", "gen_ai.tool.call.id": "read-1",
                                      "gen_ai.tool.call.arguments": '{"path":"receipt.txt"}', "gen_ai.tool.call.result": "receipt: abc123"}))
    flat = {"spans": spans, "logs": logs, "metrics": [{"name": "agent.api_request.count", "kind": "sum",
            "temporality": 2, "resource": resource, "attributes": {}, "point": {"asInt": "3"}}]}
    base = {"session_id": session, "task_id": task, "turn_id": turn, "model": model, "platform": "cli"}
    native = [("on_session_start", {"session_id": session, "model": model}),
              ("pre_llm_call", {**base, "sender_id": "", "user_message": "Read receipt.txt"})]
    for i in range(2):
        pair = {**base, "api_request_id": f"api-{i+1}"}
        source_message = {"content": responses[i]["content"], "finish_reason": requests[i]["finish_reason"],
            "tool_calls": [{"id": "read-1", "name": "read_file", "arguments": '{"path":"receipt.txt"}', "provider_data": None}] if i == 0 else None,
            "reasoning": None, "provider_data": None, "usage": {**zero, "cached_tokens": 0}}
        native += [("pre_api_request", {**pair, "request_messages": messages[i]}),
                   ("post_api_request", {**pair, "finish_reason": requests[i]["finish_reason"], "response_model": model,
                    "response": {"model": model, "finish_reason": requests[i]["finish_reason"], "usage": deepcopy(normalized),
                                 "assistant_message": {"role": "assistant", "content": responses[i]["content"], "tool_calls": source_message["tool_calls"] or []}},
                    "assistant_message": source_message, "usage": deepcopy(normalized)})]
        if i == 0:
            native += [("pre_tool_call", {**pair, "tool_call_id": "read-1", "tool_name": "read_file"}),
                       ("post_tool_call", {**pair, "tool_call_id": "read-1", "tool_name": "read_file", "result": "receipt: abc123",
                                           "error_message": None, "error_type": None, "status": "ok"})]
            for _, raw in native[-2:]: raw["args"] = {"path": "receipt.txt"}
    native += [("post_llm_call", {**base, "assistant_response": "receipt: abc123"}),
               ("on_session_end", {**base, "completed": True, "interrupted": False})]
    aux = {**base, "api_request_id": "aux-1", "aux_task": "title_generation", "provider": "custom",
           "api_mode": "chat_completions", "base_url": "http://127.0.0.1:12345/v1/", "streaming": False,
           "retry_count": 0, "api_call_count": 1, "started_at": 1750000004.0, "message_count": 2}
    native += [("pre_auxiliary_call", {**aux, "request_messages": messages[2], "system_prompt": "Name this session",
                                       "tool_count": 0, "request": {"method": "POST", "body": requests[2]["request"]}}),
               ("post_auxiliary_call", {**aux, "ended_at": 1750000005.0, "api_duration": 1.0,
                   "error": None, "error_type": None, "finish_reason": "stop", "response_model": model,
                   "assistant_content_chars": 18, "assistant_tool_call_count": 0, "usage": normalized,
                   "response": {"model": model, "finish_reason": "stop", "usage": None,
                                "assistant_message": {**responses[2], "tool_calls": []}}})]
    envelopes = []
    for hook, fields in native:
        fields = {"telemetry_schema_version": "hermes.observer.v1", **fields}
        correlation_keys = {"session_id"}
        if hook != "on_session_start": correlation_keys |= {"task_id", "turn_id"}
        if hook == "pre_llm_call": correlation_keys.add("sender_id")
        if "api_request_id" in fields: correlation_keys.add("api_request_id")
        if "tool_call_id" in fields: correlation_keys.add("tool_call_id")
        output = fields.get({"post_api_request": "response", "post_auxiliary_call": "response",
                             "post_llm_call": "assistant_response", "post_tool_call": "result"}.get(hook))
        error = fields.get("error" if hook == "post_auxiliary_call" else "error_message") if hook in {"post_auxiliary_call", "post_tool_call"} else None
        envelopes.append({"schemaVersion": "hermes.replay.raw.v1", "agent": "hermes", "plugin": "hermes-replay",
            "hook": hook, "capturedAt": "2025-06-15T15:06:40Z", "sessionId": session,
            "correlation": {key: fields[key] for key in correlation_keys}, "rawInput": fields,
            "rawContext": {}, "rawOutput": output, "rawError": error,
            "redaction": {"collectionMode": "full", "contentRedacted": False, "credentialsRedacted": False}})
    report = {"app": "hermes", "case": "hermes-zero-usage", "scenario": "zero-usage", "mode": "fake",
              "version": "0.21.5", "model": model, "receipt": "abc123", "expected_resource": {},
              "result": {"version": "0.21.5", "exit_code": 0, "session_id": session, "output": "receipt: abc123",
                         "details": {"hermes_raw_callbacks": envelopes}}, "requests": requests, "exports": captured_native(flat)}
    rechain(report)
    return json.loads(json.dumps(report))


class HermesAcceptanceTests(unittest.TestCase):
    def test_source_timestamps_use_exact_canonical_microsecond_representation(self):
        report = proof()
        rows = report["result"]["details"]["hermes_raw_callbacks"]
        for row in rows[-2:]: row["rawInput"]["started_at"] = 1750000004.1234562
        rows[-1]["rawInput"]["ended_at"] = 1750000005.1234562
        # Independent literals: canonical ISO is ...04.123456 / ...05.123456.
        aux = report["exports"][0]["spans"][3]
        aux["start_time_unix_nano"] = 1750000004123456000
        aux["end_time_unix_nano"] = 1750000005123456000
        rechain(report); recapture(report)
        self.assertTrue(assess_hermes_source(report)["accepted"])
        for field in ("start_time_unix_nano", "end_time_unix_nano"):
            changed = deepcopy(report)
            changed["exports"][0]["spans"][3][field] += 1
            recapture(changed)
            with self.subTest(field=field): self.assertFalse(assess_hermes_source(changed)["accepted"])

    def test_bounded_auxiliary_loss_preserves_strict_failure_and_original_evidence(self):
        report = proof(); original = deepcopy(report)
        result = assess_hermes_source(report)
        self.assertTrue(result["accepted"], result)
        self.assertFalse(result["source"]["verified"])
        self.assertFalse(result["provider_reconciled"])
        self.assertTrue(result["canonical_verified"])
        self.assertEqual(result["status"], "passed_with_known_limitations")
        self.assertEqual(result["known_limitations"][0]["calls"], [2])
        self.assertEqual(report, original)

    def test_exact_pin_scene_mode_model_and_inventory(self):
        for key, value in (("version", "0.21.6"), ("mode", "nvidia"), ("scenario", "unknown-usage"),
                           ("app", "other"), ("case", "hermes-tool-loop"), ("model", "other")):
            report = proof(); report[key] = value
            with self.subTest(key=key): self.assertFalse(assess_hermes_source(report)["accepted"])
        report = proof(); report["result"]["version"] = "0.18.2"
        self.assertFalse(assess_hermes_source(report)["accepted"])
        for mutation in ("missing", "extra", "purpose", "failed", "unfinished"):
            report = proof()
            if mutation == "missing": report["requests"].pop()
            elif mutation == "extra": report["requests"].append(deepcopy(report["requests"][-1]))
            elif mutation == "purpose": report["requests"][2]["purpose"] = "agent"
            elif mutation == "failed": report["requests"][2]["status"] = 500
            else: report["requests"][2].pop("finish_reason")
            with self.subTest(mutation=mutation): self.assertFalse(assess_hermes_source(report)["accepted"])

    def test_hashchain_and_envelope_projection_are_checked_independently(self):
        for mutation in ("missing", "reorder", "checksum", "raw", "correlation", "output", "error", "context", "schema", "redaction", "sequence"):
            report = proof(); rows = report["result"]["details"]["hermes_raw_callbacks"]; row = rows[-1]
            if mutation == "missing": rows.pop()
            elif mutation == "reorder": rows[-2:] = rows[-2:][::-1]
            elif mutation == "checksum": row["integrity"]["checksum"] = "f" * 64
            elif mutation == "raw": row["rawInput"]["usage"]["input_tokens"] = 7
            elif mutation == "sequence": row["integrity"]["sequence"] = True
            else:
                key, value = {"correlation": ("correlation", {"session_id": "wrong"}), "output": ("rawOutput", {}),
                    "error": ("rawError", "failed"), "context": ("rawContext", {"invented": True}),
                    "schema": ("schemaVersion", "hermes.replay.raw.v2"), "redaction": ("redaction", {"collectionMode": "privacy", "contentRedacted": True, "credentialsRedacted": False})}[mutation]
                row[key] = value; rechain(report)
            with self.subTest(mutation=mutation): self.assertFalse(assess_hermes_source(report)["accepted"])

    def test_rehashed_semantic_mutations_do_not_become_native_source_facts(self):
        mutations = {"session_id": "other", "turn_id": "other", "task_id": "other", "api_request_id": "other",
            "aux_task": "compression", "model": "other", "provider": "other", "retry_count": 1,
            "streaming": True, "api_call_count": 2, "finish_reason": "length", "ended_at": 1750000003.0,
            "started_at": True, "api_duration": -1, "error": "failed", "error_type": "RuntimeError",
            "response_model": "wrong", "reported_usage": {"prompt_tokens": 0, "completion_tokens": 0}}
        for key, value in mutations.items():
            report = proof(); row = report["result"]["details"]["hermes_raw_callbacks"][-1]
            row["rawInput"][key] = value
            if key in row["correlation"]: row["correlation"][key] = value
            if key == "error": row["rawError"] = value
            rechain(report)
            with self.subTest(key=key): self.assertFalse(assess_hermes_source(report)["accepted"])
        for key in ("input_tokens", "output_tokens", "reasoning_tokens", "cache_read_tokens", "request_count"):
            report = proof(); report["result"]["details"]["hermes_raw_callbacks"][-1]["rawInput"]["usage"][key] = True
            rechain(report)
            with self.subTest(key=key): self.assertFalse(assess_hermes_source(report)["accepted"])

    def test_all_signal_facts_are_rechecked_not_just_failure_names(self):
        for mutation in ("primary_unknown", "primary_log", "primary_log_output", "primary_span_tool", "primary_input",
                         "aux_zero", "aux_log_zero", "aux_id", "aux_parent", "aux_input", "aux_output", "aux_time", "aux_duration", "aux_log_duration", "metric", "tool", "log_count"):
            report = proof(); spans = report["exports"][0]["spans"]; logs = report["exports"][1]["logs"]
            if mutation == "primary_unknown": spans[1]["attributes"]["agentic.usage.input_tokens.missing"] = "true"
            elif mutation == "primary_log": logs[0]["attributes"]["gen_ai.usage.output_tokens"] = 99
            elif mutation == "primary_log_output": logs[0]["attributes"]["gen_ai.output.messages"] = "[]"
            elif mutation == "primary_span_tool": spans[1]["attributes"]["gen_ai.output.messages"] = '[{"role":"assistant","content":null,"tool_calls":[{"id":"read-1"}]}]'
            elif mutation == "primary_input": logs[0]["attributes"]["gen_ai.input.messages"] = "[]"
            elif mutation == "aux_zero": spans[3]["attributes"]["gen_ai.usage.input_tokens"] = 0
            elif mutation == "aux_log_zero": logs[2]["attributes"]["gen_ai.usage.output_tokens"] = 0
            elif mutation == "aux_id": spans[3]["attributes"]["agentic.model_call.id"] = "unrelated"
            elif mutation == "aux_parent": spans[3]["parent_span_id"] = spans[1]["span_id"]
            elif mutation == "aux_input": spans[3]["attributes"]["gen_ai.input.messages"] = "[]"
            elif mutation == "aux_output": logs[2]["attributes"]["gen_ai.output.messages"] = "[]"
            elif mutation == "aux_time": spans[3]["end_time_unix_nano"] += 1000
            elif mutation == "aux_duration": spans[3]["attributes"]["agentic.llm.duration_ms"] = 66666
            elif mutation == "aux_log_duration": logs[2]["attributes"]["duration_ms"] = 66666
            elif mutation == "metric": report["exports"][2]["metrics"][0]["point"]["asInt"] = "2"
            elif mutation == "tool": spans[4]["attributes"]["gen_ai.tool.call.result"] = "wrong"
            else: report["exports"][1]["logs"].pop()
            recapture(report)
            with self.subTest(mutation=mutation): self.assertFalse(assess_hermes_source(report, {"verified": False, "checks": {}, "failures": ["usage.reportedness"]})["accepted"])

    def test_primary_native_output_and_usage_cannot_diverge_from_transport(self):
        for mutation in ("assistant", "response", "both", "usage", "response_usage", "reported_usage", "input", "tools", "finish", "system"):
            report = proof(); rows = report["result"]["details"]["hermes_raw_callbacks"]
            row = rows[3]; raw = row["rawInput"]
            if mutation in ("assistant", "both"): raw["assistant_message"]["content"] = "wrong"
            if mutation in ("response", "both"): raw["response"]["assistant_message"]["content"] = "wrong"
            elif mutation == "usage": raw["usage"]["input_tokens"] = 999
            elif mutation == "response_usage": raw["response"]["usage"]["input_tokens"] = 999
            elif mutation == "reported_usage": raw["reported_usage"] = {"prompt_tokens": 999}
            elif mutation == "input": rows[2]["rawInput"]["request_messages"] = []
            elif mutation == "tools": raw["assistant_message"]["tool_calls"][0]["arguments"] = '{}'
            elif mutation == "finish": raw["finish_reason"] = "length"
            elif mutation == "system":
                rows[-2]["rawInput"]["system_prompt"] = "wrong system"
                for export in report["exports"]:
                    for signal in ("spans", "logs"):
                        for item in export[signal]:
                            if item["attributes"].get("agentic.model_call.id") == "aux-1":
                                item["attributes"]["gen_ai.system_instructions"] = json.dumps("wrong system")
                recapture(report)
            row["rawOutput"] = deepcopy(raw["response"])
            rechain(report)
            with self.subTest(mutation=mutation): self.assertFalse(assess_hermes_source(report)["accepted"])

    def test_rehashed_raw_tool_cannot_disagree_with_the_native_tool_span(self):
        for key, value in (("tool_call_id", "invented"), ("tool_name", "invented"), ("api_request_id", "api-2"),
                           ("args", {}), ("status", "error"), ("error_type", "RuntimeError"), ("result", "fabricated")):
            report = proof(); row = report["result"]["details"]["hermes_raw_callbacks"][5]
            row["rawInput"][key] = value
            if key in row["correlation"]: row["correlation"][key] = value
            row["rawOutput"] = row["rawInput"]["result"]
            rechain(report)
            with self.subTest(key=key): self.assertFalse(assess_hermes_source(report)["accepted"])

    def test_native_sdk_shape_is_not_an_exported_assistant_message(self):
        for signal in ("span", "log"):
            report = proof()
            if signal == "span":
                row = report["exports"][0]["spans"][1]
                output = json.loads(row["attributes"]["gen_ai.output.messages"])
                output[0]["tool_calls"] = [{"id": "read-1", "name": "read_file", "arguments": '{"path":"receipt.txt"}'}]
            else:
                row = report["exports"][1]["logs"][0]
                output = [report["result"]["details"]["hermes_raw_callbacks"][3]["rawInput"]["assistant_message"]]
            row["attributes"]["gen_ai.output.messages"] = json.dumps(output)
            recapture(report)
            with self.subTest(signal=signal): self.assertFalse(assess_hermes_source(report)["accepted"])

    def test_raw_otlp_mismatch_and_incomplete_native_lifecycle_fail(self):
        report = proof(); report["exports"][0]["spans"][1]["attributes"]["gen_ai.usage.input_tokens"] = 5
        self.assertFalse(assess_hermes_source(report)["accepted"])
        for mutation in ("completed", "interrupted", "missing_main", "missing_turn", "wrong_turn"):
            report = proof(); rows = report["result"]["details"]["hermes_raw_callbacks"]
            if mutation == "missing_main": rows.pop(3)
            elif mutation == "missing_turn": rows.pop(1)
            elif mutation == "wrong_turn": rows[1]["rawInput"]["turn_id"] = rows[1]["correlation"]["turn_id"] = "other"
            else: next(row for row in rows if row["hook"] == "on_session_end")["rawInput"][mutation] = mutation != "completed"
            rechain(report)
            with self.subTest(mutation=mutation): self.assertFalse(assess_hermes_source(report)["accepted"])

    def test_summary_claims_cannot_overstate_provider_verification(self):
        report = proof(); result = assess_hermes_source(report)
        row = {**result, "case": report["case"], "version": report["version"], "verified": False, "source_verified": False}
        self.assertTrue(bounded_hermes_row(row))
        for key in ("verified", "source_verified", "provider_reconciled"):
            changed = deepcopy(row); changed[key] = True
            self.assertFalse(bounded_hermes_row(changed))
        for key, value in (("version", "0.18.2"), ("canonical_verified", False), ("known_limitations", [])):
            changed = deepcopy(row); changed[key] = value
            self.assertFalse(bounded_hermes_row(changed))


if __name__ == "__main__": unittest.main()
