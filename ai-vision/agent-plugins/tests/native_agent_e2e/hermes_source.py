"""Independent, exact-source acceptance for Hermes 0.21.5 auxiliary zero loss.

The SDK collapses omitted and reported-zero auxiliary usage buckets. A native
raw callback chain proves that loss; it does not restore the missing presence
fact. Only the auxiliary expected usage is projected to unknown for a second
run of the ordinary oracle. Original evidence and strict results stay intact.
"""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import re

from evidence_integrity import exports_match_raw
from verify import decoded, kind, verify_case

VERSION = "0.21.5"
LIMITATION = "hermes.host.auxiliary_zero_reportedness_unavailable"
MODEL = "native-e2e-chat"
ZERO = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
NORMALIZED_ZERO = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
    "cache_write_tokens": 0, "reasoning_tokens": 0, "request_count": 1, "prompt_tokens": 0, "total_tokens": 0}
ENVELOPE_KEYS = {"schemaVersion", "agent", "plugin", "hook", "capturedAt", "sessionId", "correlation",
    "rawInput", "rawContext", "rawOutput", "rawError", "redaction", "integrity"}
COUNTS = {"on_session_start": 1, "pre_llm_call": 1, "post_llm_call": 1, "on_session_end": 1,
    "pre_api_request": 2, "post_api_request": 2, "pre_tool_call": 1, "post_tool_call": 1,
    "pre_auxiliary_call": 1, "post_auxiliary_call": 1}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _same(left, right):
    return _json(left) == _json(right)


def _require(condition):
    if not condition:
        raise ValueError("native source facts do not match the bounded Hermes loss")


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _canonical_time(value):
    # Both native OTLP and replay encode the canonical ISO timestamp, whose
    # datetime representation has microsecond precision. Reconstruct that
    # exact representation; do not accept an arbitrary timing tolerance.
    return int(datetime.fromtimestamp(value, timezone.utc).timestamp() * 1_000_000_000)


def _message(value, *, native_sdk=False):
    """Compare the literal SDK and wire shapes without production conversion."""
    _require(isinstance(value, dict) and "content" in value
        and (value.get("role") == "assistant" or native_sdk and "role" not in value and "finish_reason" in value))
    tools = value.get("tool_calls") or []
    _require(isinstance(tools, list))
    calls = []
    for tool in tools:
        _require(isinstance(tool, dict) and isinstance(tool.get("id"), str) and bool(tool["id"])
            and tool.get("type", "function") == "function" and tool.get("provider_data") is None)
        if not native_sdk:
            _require(tool.get("type") == "function" and isinstance(tool.get("function"), dict))
        function = tool.get("function", tool)
        _require(isinstance(function, dict) and isinstance(function.get("name"), str) and bool(function["name"])
            and isinstance(function.get("arguments"), str))
        arguments = json.loads(function["arguments"])
        _require(isinstance(arguments, dict))
        calls.append({"id": tool["id"], "name": function["name"], "arguments": arguments})
    for field in ("reasoning", "reasoning_content", "reasoning_details", "provider_data"):
        _require(value.get(field) is None)
    return {"role": "assistant", "content": value["content"], "tool_calls": calls}


def _arguments(report, requests=None):
    return dict(app=report["app"], scenario=report["scenario"], model=report.get("model", MODEL),
        receipt=report["receipt"], result=report["result"], requests=report["requests"] if requests is None else requests,
        exports=report["exports"], expected_resource=report.get("expected_resource"), mode=report.get("mode", "fake"))


def _chain(report):
    """Validate JSON/hash/projections independently of the production recorder."""
    rows = report["result"]["details"]["hermes_raw_callbacks"]
    session = report["result"]["session_id"]
    _require(isinstance(rows, list) and rows and isinstance(session, str) and bool(session))
    previous = "0" * 64
    hooks = {}
    for sequence, envelope in enumerate(rows, 1):
        _require(isinstance(envelope, dict) and set(envelope) == ENVELOPE_KEYS)
        hook = envelope["hook"]
        _require(hook in COUNTS or hook == "on_session_finalize")
        _require(envelope["schemaVersion"] == "hermes.replay.raw.v1" and envelope["agent"] == "hermes"
                 and envelope["plugin"] == "hermes-replay" and envelope["sessionId"] == session)
        captured = datetime.fromisoformat(envelope["capturedAt"].replace("Z", "+00:00"))
        _require(captured.tzinfo is not None)
        redaction = envelope["redaction"]
        _require(isinstance(redaction, dict) and set(redaction) == {"collectionMode", "contentRedacted", "credentialsRedacted"}
            and redaction["collectionMode"] == "full" and redaction["contentRedacted"] is False
            and type(redaction["credentialsRedacted"]) is bool)
        integrity = envelope["integrity"]
        _require(isinstance(integrity, dict) and set(integrity) == {"sequence", "previousChecksum", "checksum"}
            and type(integrity["sequence"]) is int and integrity["sequence"] == sequence
            and integrity["previousChecksum"] == previous and isinstance(integrity["checksum"], str)
            and re.fullmatch("[0-9a-f]{64}", integrity["checksum"]) is not None)
        unsigned = deepcopy(envelope)
        unsigned["integrity"].pop("checksum")
        _require(hashlib.sha256(_json(unsigned).encode()).hexdigest() == integrity["checksum"])
        previous = integrity["checksum"]
        raw = envelope["rawInput"]
        _require(isinstance(raw, dict) and raw.get("session_id") == session
            and raw.get("telemetry_schema_version") == "hermes.observer.v1")
        fields = {"session_id"}
        if hook not in {"on_session_start", "on_session_finalize"}:
            fields |= {"turn_id", "task_id"}
        if hook == "pre_llm_call": fields.add("sender_id")
        if hook in {"pre_api_request", "post_api_request", "pre_auxiliary_call", "post_auxiliary_call", "pre_tool_call", "post_tool_call"}:
            fields.add("api_request_id")
        if hook in {"pre_tool_call", "post_tool_call"}: fields.add("tool_call_id")
        _require(_same(envelope["correlation"], {key: raw[key] for key in fields if key in raw}))
        output_field = {"post_api_request": "response", "post_auxiliary_call": "response",
                        "post_llm_call": "assistant_response", "post_tool_call": "result"}.get(hook)
        error_field = {"post_auxiliary_call": "error", "post_tool_call": "error_message"}.get(hook)
        _require(_same(envelope["rawContext"], raw.get("context", {}))
            and _same(envelope["rawOutput"], raw.get(output_field))
            and _same(envelope["rawError"], raw.get(error_field)))
        hooks.setdefault(hook, []).append((sequence, raw))
    counts = Counter(row["hook"] for row in rows)
    _require(counts.pop("on_session_finalize", 0) in (0, 1) and counts == COUNTS)
    _require(rows[0]["hook"] == "on_session_start")
    native = hooks["pre_llm_call"][0][1]
    identity = {key: native.get(key) for key in ("session_id", "task_id", "turn_id")}
    _require(all(isinstance(value, str) and value for value in identity.values()))
    for hook, entries in hooks.items():
        if hook in {"on_session_start", "on_session_finalize"}: continue
        _require(all(all(raw.get(key) == value for key, value in identity.items()) for _, raw in entries))
    end = hooks["on_session_end"][0][1]
    _require(end.get("completed") is True and end.get("interrupted") is False)
    _require(hooks["pre_llm_call"][0][0] < hooks["post_llm_call"][0][0] < hooks["on_session_end"][0][0])
    _require(hooks["post_llm_call"][0][1].get("assistant_response") == "receipt: " + report["receipt"])
    return hooks, identity


def _source_calls(report):
    _require(report.get("app") == "hermes" and report.get("case") == "hermes-zero-usage"
        and report.get("scenario") == "zero-usage" and report.get("mode") == "fake"
        and report.get("version") == VERSION and report["result"].get("version") == VERSION
        and report.get("model", MODEL) == MODEL and type(report["result"].get("exit_code")) is int
        and report["result"]["exit_code"] == 0)
    calls = report["requests"]
    _require(isinstance(calls, list) and len(calls) == 3
        and [call.get("purpose") for call in calls] == ["agent", "agent", "auxiliary"]
        and [call.get("finish_reason") for call in calls] == ["tool_calls", "stop", "stop"])
    for index, call in enumerate(calls, 1):
        _require(type(call.get("status")) is int and call["status"] == 200 and not call.get("error")
            and call.get("usage_reported") is True and _same(call.get("usage"), ZERO)
            and type(call.get("number")) is int and call["number"] == index
            and call.get("method") in (None, "POST") and call.get("request", {}).get("model") == MODEL
            and ("completed" not in call or call["completed"] is True)
            and ("client_disconnected" not in call or call["client_disconnected"] is False))
    hooks, identity = _chain(report)
    first, second = [_message(call["response"]) for call in calls[:2]]
    _require(first["content"] is None and len(first["tool_calls"]) == 1
        and first["tool_calls"][0]["name"] == "read_file"
        and second["content"] == "receipt: " + report["receipt"] and second["tool_calls"] == [])
    start_sequence, pre = hooks["pre_auxiliary_call"][0]
    stop_sequence, post = hooks["post_auxiliary_call"][0]
    _require(start_sequence < stop_sequence)
    shared = ("session_id", "task_id", "turn_id", "api_request_id", "aux_task", "model", "provider", "api_mode",
              "base_url", "streaming", "retry_count", "api_call_count", "started_at", "message_count")
    _require(all(key in pre and key in post and _same(pre[key], post[key]) for key in shared))
    _require(pre["aux_task"] == "title_generation" and pre["model"] == MODEL and pre["provider"] == "custom"
        and pre["api_mode"] == "chat_completions" and pre["streaming"] is False
        and type(pre["retry_count"]) is int and pre["retry_count"] == 0
        and type(pre["api_call_count"]) is int and pre["api_call_count"] == 1
        and isinstance(pre["api_request_id"], str) and bool(pre["api_request_id"])
        and isinstance(pre["base_url"], str) and pre["base_url"].startswith("http://127.0.0.1:"))
    _require(_number(pre["started_at"]) and pre["started_at"] > 0
        and _number(post.get("ended_at")) and post["ended_at"] >= pre["started_at"]
        and _number(post.get("api_duration")) and post["api_duration"] >= 0
        and math.isclose(post["api_duration"], post["ended_at"] - pre["started_at"], abs_tol=1e-9))
    _require("error" in post and post["error"] is None and "error_type" in post and post["error_type"] is None
        and post.get("finish_reason") == "stop" and post.get("response_model") == MODEL
        and "reported_usage" not in pre and "reported_usage" not in post and _same(post.get("usage"), NORMALIZED_ZERO))
    response = post["response"]
    _require(isinstance(response, dict) and response.get("model") == MODEL and response.get("finish_reason") == "stop"
        and "usage" in response and response["usage"] is None and "reported_usage" not in response)
    messages = pre["request_messages"]
    _require(isinstance(messages, list) and messages and _same(messages, calls[2]["request"].get("messages"))
        and type(pre["message_count"]) is int and pre["message_count"] == len(messages)
        and type(pre.get("tool_count")) is int and pre["tool_count"] == 0
        and isinstance(pre.get("system_prompt"), str) and bool(pre["system_prompt"]))
    _require(pre["system_prompt"] == "\n".join(message["content"] for message in messages
        if message.get("role") in {"system", "developer"}))
    _require(pre["request"].get("method") == "POST")
    body = deepcopy(pre["request"]["body"])
    extra = body.pop("extra_body", {})
    _require(isinstance(extra, dict))
    body.update(extra)
    _require(_same(body, calls[2]["request"]) and calls[2]["request"].get("stream", False) is False)
    assistant = response["assistant_message"]
    wire = calls[2]["response"]
    _require(isinstance(assistant, dict) and assistant.get("role") == wire.get("role") == "assistant"
        and isinstance(assistant.get("content"), str) and assistant["content"] == wire.get("content")
        and assistant.get("tool_calls") == [] and not wire.get("tool_calls")
        and type(post.get("assistant_content_chars")) is int and post["assistant_content_chars"] == len(assistant["content"])
        and type(post.get("assistant_tool_call_count")) is int and post["assistant_tool_call_count"] == 0)
    return hooks, identity, pre, post


def _signals(report, hooks, identity, pre, post):
    spans = [row for export in report["exports"] for row in export.get("spans", [])]
    logs = [row for export in report["exports"] for row in export.get("logs", []) if row.get("attributes", {}).get("event.name") == "api_request"]
    generations = [row for row in spans if kind(row) == "LLM"]
    roots = [row for row in spans if kind(row) == "CHAT"]
    _require(len(roots) == 1 and len(generations) == 3 and len(logs) == 3)
    root = roots[0]
    _require(root["attributes"].get("agentic.turn.id") == identity["turn_id"]
        and type(root["attributes"].get("agentic.turn.number")) is int and root["attributes"]["agentic.turn.number"] == 1)
    native_ids = []
    by_id = {}
    for wire, (start, before), (end, after) in zip(report["requests"][:2], hooks["pre_api_request"], hooks["post_api_request"]):
        _require(start < end and before.get("api_request_id") == after.get("api_request_id")
                 and before.get("model") == after.get("model") == MODEL)
        _require(_same(before.get("request_messages"), wire["request"].get("messages"))
            and _same(after.get("usage"), NORMALIZED_ZERO)
            and ("reported_usage" not in after or _same(after["reported_usage"], ZERO))
            and after.get("finish_reason") == wire["finish_reason"] and after.get("response_model") == MODEL)
        response = after["response"]
        _require(isinstance(response, dict) and response.get("model") == MODEL
            and response.get("finish_reason") == wire["finish_reason"] and _same(response.get("usage"), NORMALIZED_ZERO))
        expected = _message(wire["response"])
        _require(_same(_message(after["assistant_message"], native_sdk=True), expected)
            and _same(_message(response["assistant_message"], native_sdk=True), expected))
        native_message = after["assistant_message"]
        if "finish_reason" in native_message:
            _require(native_message["finish_reason"] == wire["finish_reason"])
        if "usage" in native_message:
            _require(_same(native_message["usage"], {**ZERO, "cached_tokens": 0}))
        by_id[before["api_request_id"]] = wire
        native_ids.append(before["api_request_id"])
    native_ids.append(pre["api_request_id"])
    _require(all(isinstance(value, str) and value for value in native_ids) and len(set(native_ids)) == 3)
    _require({row["attributes"].get("agentic.model_call.id") for row in generations} == set(native_ids))
    _require(len({(row.get("trace_id"), row.get("span_id")) for row in logs}) == 3)
    tool_start, tool_pre = hooks["pre_tool_call"][0]
    tool_end, tool_post = hooks["post_tool_call"][0]
    wire_tool = _message(report["requests"][0]["response"])["tool_calls"][0]
    _require(hooks["post_api_request"][0][0] < tool_start < tool_end < hooks["pre_api_request"][1][0])
    for raw in (tool_pre, tool_post):
        _require(raw.get("api_request_id") == native_ids[0] and raw.get("tool_call_id") == wire_tool["id"]
            and raw.get("tool_name") == wire_tool["name"] and _same(raw.get("args"), wire_tool["arguments"]))
    _require(tool_post.get("status") == "ok" and tool_post.get("error_type") is None and tool_post.get("error_message") is None)
    tools = [row for row in spans if kind(row) == "TOOL"]
    _require(len(tools) == 1)
    attrs = tools[0]["attributes"]
    _require(attrs.get("gen_ai.tool.call.id") == wire_tool["id"] and attrs.get("gen_ai.tool.name") == wire_tool["name"]
        and _same(decoded(attrs.get("gen_ai.tool.call.arguments")), wire_tool["arguments"])
        and _same(decoded(attrs.get("gen_ai.tool.call.result")), tool_post.get("result")))
    for span in generations:
        attrs = span["attributes"]
        auxiliary = attrs["agentic.model_call.id"] == pre["api_request_id"]
        matches = [row for row in logs if (row.get("trace_id"), row.get("span_id")) == (span.get("trace_id"), span.get("span_id"))]
        _require(len(matches) == 1)
        event = matches[0]["attributes"]
        _require(span.get("parent_span_id") == root.get("span_id") and span.get("trace_id") == root.get("trace_id")
            and type(span.get("status", {}).get("code")) is int and span["status"]["code"] == 1)
        for a in (attrs, event):
            _require(a.get("agentic.model_call.id") == attrs["agentic.model_call.id"]
                and a.get("agentic.turn.id") == identity["turn_id"] and a.get("gen_ai.conversation.id") == identity["session_id"]
                and type(a.get("agentic.turn.number")) is int and a["agentic.turn.number"] == 1
                and a.get("gen_ai.request.model") == a.get("gen_ai.response.model") == MODEL
                and a.get("gen_ai.provider.name") == "custom" and not a.get("error.type"))
            for direction in ("input", "output"):
                key = f"gen_ai.usage.{direction}_tokens"
                missing = a.get(f"agentic.usage.{direction}_tokens.missing")
                _require((key not in a and missing == "true") if auxiliary else
                    (type(a.get(key)) is int and a[key] == 0 and missing in (None, "false")))
            if auxiliary:
                _require(not any(key.startswith("gen_ai.usage.") or key.startswith("agentic.usage.") and not key.endswith(".missing") for key in a))
                _require(_same(decoded(a.get("gen_ai.input.messages")), pre["request_messages"])
                    and _same(decoded(a.get("gen_ai.output.messages")), [post["response"]["assistant_message"]]))
            else:
                wire = by_id[attrs["agentic.model_call.id"]]
                output = decoded(a.get("gen_ai.output.messages"))
                _require(_same(decoded(a.get("gen_ai.input.messages")), wire["request"]["messages"])
                    and isinstance(output, list) and len(output) == 1
                    and _same(_message(output[0]), _message(wire["response"])))
                for key in a:
                    if key.startswith(("gen_ai.usage.", "agentic.usage.")) and not key.endswith(".missing"):
                        _require(key in {"gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens", "gen_ai.usage.total_tokens", "agentic.usage.fresh_input_tokens"}
                            and type(a[key]) is int and a[key] == 0)
        if auxiliary:
            _require(attrs.get("gen_ai.agent.name") == "title_generation" and type(attrs.get("agentic.llm.retry_count")) is int
                and attrs["agentic.llm.retry_count"] == 0 and decoded(attrs.get("gen_ai.response.finish_reasons")) == ["stop"]
                and _same(decoded(attrs.get("gen_ai.system_instructions")), pre["system_prompt"])
                and span.get("start_time_unix_nano") == _canonical_time(pre["started_at"])
                and span.get("end_time_unix_nano") == _canonical_time(post["ended_at"]))
            for a, key in ((attrs, "agentic.llm.duration_ms"), (event, "duration_ms")):
                _require(_number(a.get(key)) and math.isclose(a[key], post["api_duration"] * 1000, rel_tol=1e-9, abs_tol=1e-6))
        else:
            _require(attrs.get("gen_ai.agent.name") not in {"title", "summary", "compaction", "title_generation"})
            _require(type(event.get("gen_ai.usage.total_tokens")) is int and event["gen_ai.usage.total_tokens"] == 0)
            _require(decoded(attrs.get("gen_ai.response.finish_reasons")) == [by_id[attrs["agentic.model_call.id"]]["finish_reason"]])


def _limitation():
    return {"id": LIMITATION, "app": "hermes", "version": VERSION, "scenario": "zero-usage", "calls": [2]}


def assess_hermes_source(report, strict_result=None):
    """Recompute native evidence; a saved failure label is never authorization."""
    try:
        strict = deepcopy(strict_result) if strict_result is not None else verify_case(**_arguments(report))
    except (KeyError, TypeError, ValueError, AttributeError):
        strict = {"verified": False, "checks": {}, "failures": ["source.missing_or_invalid_evidence"]}
    answer = {"source": strict, "provider_reconciled": False, "canonical_verified": False,
              "known_limitations": [], "accepted": False, "status": "failed"}
    if not exports_match_raw(report.get("exports")):
        return answer
    try:
        # This assessor handles only the bounded loss. Fully verified cases use
        # the ordinary source acceptance path and never receive a limitation.
        _require(strict.get("verified") is False)
        hooks, identity, pre, post = _source_calls(report)
        _signals(report, hooks, identity, pre, post)
        projected = deepcopy(report["requests"])
        projected[2]["usage"] = None
        canonical = verify_case(**_arguments(report, projected))
        canonical["checks"]["hermes.raw_auxiliary_source"] = True
        answer.update(canonical=canonical, canonical_verified=canonical["verified"])
        if canonical["verified"]:
            answer.update(accepted=True, status="passed_with_known_limitations", known_limitations=[_limitation()])
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        pass
    return answer


def bounded_hermes_row(row):
    return (row.get("case") == "hermes-zero-usage" and row.get("version") == VERSION
        and row.get("status") == "passed_with_known_limitations" and row.get("accepted") is True
        and row.get("verified") is False and row.get("source_verified") is False
        and row.get("provider_reconciled") is False and row.get("canonical_verified") is True
        and _same(row.get("known_limitations"), [_limitation()]))
