"""Independent Agentic RAG acceptance. Business constants never use fixture.expected.

Only query endpoints are called. Secrets are read into memory and never emitted.
Embedding calls are observed but unreported billing stays unknown.
"""
import argparse
import base64
from collections import Counter
import datetime
import json
import os
from pathlib import Path
import re
import uuid
import time
import urllib.error
import urllib.parse
import urllib.request


def decoded(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def status_code(value):
    if isinstance(value, dict):
        value = value.get("code", 0)
    if isinstance(value, int):
        return value
    return {"OK": 1, "ERROR": 2, "UNSET": 0, "STATUS_CODE_OK": 1,
            "STATUS_CODE_ERROR": 2, "STATUS_CODE_UNSET": 0, "0": 0, "1": 1, "2": 2}.get(str(value), -1)


def observation_type(span):
    attrs = span.get("attributes", {})
    explicit = attrs.get("langfuse.observation.type")
    if explicit:
        return str(explicit).upper()
    operation = str(attrs.get("gen_ai.operation.name", "")).lower()
    if operation in ("chat", "completion", "text_completion"):
        return "GENERATION"
    if operation in ("embedding", "embeddings"):
        return "EMBEDDING"
    if operation in ("execute_tool", "tool") or attrs.get("gen_ai.tool.name"):
        return "TOOL"
    if operation == "invoke_agent":
        return "AGENT"
    if operation == "retrieval":
        return "RETRIEVER"
    kind = str(attrs.get("gen_ai.span.kind", attrs.get("openinference.span.kind", "SPAN"))).upper()
    if kind == "LLM":
        return "GENERATION"
    if kind != "SPAN":
        return kind
    return "SPAN"


def span_usage(span):
    attrs = span.get("attributes", {})
    usage = decoded(attrs.get("langfuse.observation.usage_details"))
    if isinstance(usage, dict):
        # Official OpenAI wrappers retain provider field names. Their prompt
        # counts already include cached tokens; Langfuse's `input` excludes them.
        inp = next((usage[key] for key in ("input", "prompt_tokens", "input_tokens") if usage.get(key) is not None), None)
        if usage.get("input") is not None:
            inp += sum(usage.get(k, 0) or 0 for k in ("input_cache_read", "input_cache_creation"))
        output = next((usage[key] for key in ("output", "completion_tokens", "output_tokens") if usage.get(key) is not None), None)
        total = next((usage[key] for key in ("total", "total_tokens") if usage.get(key) is not None), None)
    else:
        inp, output, total = (attrs.get("gen_ai.usage." + key) for key in ("input_tokens", "output_tokens", "total_tokens"))
    # Relay reports the standard inclusive input/output without redundant
    # total. Derive only from two reported measurements; absent stays unknown.
    if total is None and inp is not None and output is not None:
        total = inp + output
    return {"input": inp, "output": output, "total": total}


def usage_totals(spans):
    answer = {}
    for label, kind in (("chat", "GENERATION"), ("embedding", "EMBEDDING")):
        calls = [span_usage(s) for s in spans if observation_type(s) == kind]
        answer[label] = {k: sum(c[k] for c in calls) if calls and all(c[k] is not None for c in calls) else None
                         for k in ("input", "output", "total")}
        answer[label]["calls"] = len(calls)
    return answer


def compare_native_spans(expected, actual):
    failures = []
    ids = [s.get("span_id") for s in actual]
    wanted = [s.get("span_id") for s in expected]
    if not wanted or not ids or len(ids) != len(set(ids)) or len(wanted) != len(set(wanted)) or set(ids) != set(wanted):
        failures.append("span_inventory")
    index = {s.get("span_id"): s for s in actual}
    for source in expected:
        live = index.get(source.get("span_id"))
        if live is None:
            continue
        for field in ("span_id", "trace_id", "name"):
            if source.get(field) != live.get(field):
                failures.append(field + ":" + str(source.get("span_id")))
        if (source.get("parent_span_id") or None) != (live.get("parent_span_id") or None):
            failures.append("parent_span_id:" + source["span_id"])
        if status_code(source.get("status")) != status_code(live.get("status")):
            failures.append("status:" + source["span_id"])
        if span_usage(source) != span_usage(live):
            failures.append("usage:" + source["span_id"])
        if observation_type(source) != observation_type(live):
            failures.append("type:" + source["span_id"])
        for field in ("start_time_unix_nano", "end_time_unix_nano"):
            if field in source:
                observed = (live.get("payload") or {}).get(field, live.get(field))
                if str(source[field]) != str(observed):
                    failures.append(field + ":" + source["span_id"])
    return failures


def verify_inventory(spans):
    kinds = Counter(observation_type(s) for s in spans)
    return ["missing_" + kind.lower() for kind in ("EMBEDDING", "GENERATION", "TOOL") if not kinds[kind]]


def verify_business(result, ledger, phase):
    """Check literals plus actual retrieve→ToolMessage→calculate→ToolMessage→final."""
    failures = []
    result = decoded(result)
    days, storage = (21, 210) if phase == "changed-corpus" else (14, 140)
    if not isinstance(result, dict):
        return ["final_not_json_object"]
    for field, expected in (("tenant", "orion"), ("retention_days", days), ("daily_gib", 10), ("storage_gib", storage)):
        if result.get(field) != expected:
            failures.append("final_" + field)
    citation = result.get("citations")
    if citation != ["policy-orion"]:
        failures.append("final_citation")
    tools = ledger.get("tools", [])
    retrievals = [t for t in tools if "retriev" in t.get("name", "").lower()]
    calculations = [t for t in tools if "calculat" in t.get("name", "").lower()]
    if not retrievals or not calculations:
        return failures + ["missing_dependency_tools"]
    retrieval, calculation = retrievals[-1], calculations[-1]
    retrieved = decoded(retrieval.get("output", retrieval.get("result"))) or {}
    calculated = decoded(calculation.get("output", calculation.get("result"))) or {}
    docs = retrieved.get("docs", retrieved.get("documents", []))
    def valid_document(doc):
        if not isinstance(doc, dict) or doc.get("id", doc.get("document_id")) != "policy-orion":
            return False
        if "text" in doc:
            return bool(re.search(r"Retention: " + str(days) + r" days", doc["text"]) and
                        re.search(r"Daily storage: 10 GiB", doc["text"]))
        return doc.get("retention_days") == days and doc.get("daily_gib") == 10
    if not isinstance(docs, list) or not any(valid_document(d) for d in docs):
        failures.append("retrieved_document")
    receipt = retrieved.get("receipt")
    if not isinstance(receipt, str) or not receipt or result.get("receipt") != receipt:
        failures.append("receipt_from_retrieval")
    args = decoded(calculation.get("arguments", {}))
    if not isinstance(args, dict) or any(args.get(k) != v for k, v in (("retention_days", days), ("daily_gib", 10), ("receipt", receipt))):
        failures.append("calculator_retrieval_dependency")
    if not isinstance(calculated, dict) or calculated.get("storage_gib") != storage:
        failures.append("calculator_result")
    messages = named_messages(ledger.get("messages", []))
    tool_positions = []
    for tool in (retrieval, calculation):
        matches = [(i, m) for i, m in enumerate(messages) if str(m.get("type", "")).lower() in ("tool", "toolmessage") and m.get("name") == tool["name"]]
        target = decoded(tool.get("output", tool.get("result")))
        matches = [(i, m) for i, m in matches if decoded(m.get("content")) == target and (not tool.get("tool_call_id") or m.get("tool_call_id") == tool["tool_call_id"])]
        matches = [(i, m) for i, m in matches if any(
            call.get("id") == m.get("tool_call_id") and call.get("name") == tool["name"] and decoded(call.get("args")) == decoded(tool.get("arguments"))
            for previous in messages[:i] for call in previous.get("tool_calls", []) or [])]
        if not matches:
            failures.append("missing_real_toolmessage:" + tool["name"])
        else:
            tool_positions.append(matches[-1][0])
    finals = [(i, m) for i, m in enumerate(messages) if str(m.get("type", "")).lower() in ("ai", "aimessage", "assistant") and not m.get("tool_calls") and decoded(m.get("content")) == result]
    if len(tool_positions) != 2 or not finals or not tool_positions[0] < tool_positions[1] < finals[-1][0]:
        failures.append("retrieve_calculate_final_order")
    return failures


def export_spans(exports):
    rows = exports.get("exports", []) if isinstance(exports, dict) else exports
    return [s for row in rows for s in row.get("spans", [])] if isinstance(rows, list) else []


def span_io(span, direction):
    attrs = span.get("attributes", {})
    keys = (("gen_ai.tool.call.arguments", "langfuse.observation.metadata.inputs", "langfuse.observation.input", "aivision.embedding.input", "aivision.request.input")
            if direction == "input" else ("gen_ai.tool.call.result", "langfuse.observation.output", "aivision.embedding.output", "aivision.request.output"))
    value = next((decoded(attrs[k]) for k in keys if k in attrs), None)
    if direction == "output" and isinstance(value, dict) and value.get("__nv_dataclass__") == "langgraph.types.Command":
        value = value.get("data")
    if direction == "output" and isinstance(value, dict) and "goto" in value and isinstance(value.get("update"), dict):
        messages = value["update"].get("messages", [])
        tools = [m for m in messages if isinstance(m, dict) and m.get("type") == "tool"]
        if len(tools) == 1:
            value = tools[0]
    if isinstance(value, dict) and value.get("__nv_pydantic__") == "langchain_core.messages.tool.ToolMessage":
        value = value.get("data")
    if direction == "output" and isinstance(value, dict) and value.get("type") in ("tool", "ToolMessage") and "content" in value:
        value = decoded(value["content"])
        if isinstance(value, str) and value.startswith("{\\\""):
            try:
                value = json.loads(json.loads('"' + value + '"'))
            except ValueError:
                pass
    return value


def model_identity(span):
    attrs = span.get("attributes", {})
    return attrs.get("gen_ai.request.model", attrs.get("langfuse.observation.model.name", ""))


def chat_output(span):
    attrs = span.get("attributes", {})
    if "langfuse.observation.output" in attrs:
        value = decoded(attrs["langfuse.observation.output"])
        return value if isinstance(value, dict) else None
    messages = decoded(attrs.get("gen_ai.output.messages"))
    if not isinstance(messages, list):
        return None
    messages = [m for m in messages if m.get("role") == "assistant"]
    if len(messages) != 1:
        return None
    message = messages[0]
    if "parts" not in message:
        return message
    parts = message["parts"]
    return {"content": "".join(str(p.get("content", "")) for p in parts if p.get("type") == "text"),
            "tool_calls": [{"id": p.get("id"), "name": p.get("name"), "args": p.get("arguments")} for p in parts if p.get("type") == "tool_call"]}


def output_signature(value):
    if not isinstance(value, dict):
        return None
    calls = []
    for call in value.get("tool_calls", []) or []:
        function = call.get("function", {})
        calls.append({"id": call.get("id"), "name": call.get("name", function.get("name")),
                      "args": decoded(call.get("args", call.get("arguments", function.get("arguments"))))})
    return json.dumps({"content": decoded(value.get("content")), "tool_calls": calls}, sort_keys=True, ensure_ascii=False)


def normalized_inputs(messages, *, sdk=False):
    """Normalize the two public serializers, preserving tool-call identity/IO."""
    if not isinstance(messages, list):
        return None
    answer = []
    for message in messages:
        if not isinstance(message, dict):
            return None
        role = message.get("role", message.get("type"))
        role = {"human": "user", "ai": "assistant", "toolmessage": "tool"}.get(role, role)
        content = message.get("content", "")
        calls = message.get("tool_calls", []) or []
        tool_id = message.get("tool_call_id")
        if "parts" in message:
            parts = message["parts"]
            if not isinstance(parts, list):
                return None
            responses = [part for part in parts if part.get("type") == "tool_call_response"]
            if responses:
                if len(responses) != 1:
                    return None
                content, tool_id = responses[0].get("response"), responses[0].get("id")
            else:
                content = "".join(str(part.get("content", "")) for part in parts if part.get("type") == "text")
                calls = [{"id": part.get("id"), "name": part.get("name"), "args": part.get("arguments")} for part in parts if part.get("type") == "tool_call"]
        content = decoded(content)
        # Langfuse appends bound function definitions as role=tool messages.
        # These are not ToolMessages and have no tool_call_id. Their distinct
        # public SDK serializer shape is the only exclusion.
        if sdk and role == "tool" and not tool_id and isinstance(content, dict) and content.get("type") == "function" and isinstance(content.get("function"), dict) and "parameters" in content["function"]:
            continue
        normalized = {"role": role, "content": content, "tool_calls": json.loads(output_signature({"content": "", "tool_calls": calls}))["tool_calls"]}
        if role == "tool":
            normalized["tool_call_id"] = tool_id
        answer.append(normalized)
    return answer


def generation_input(span):
    attrs = span.get("attributes", {})
    value = next((decoded(attrs[k]) for k in ("langfuse.observation.input", "gen_ai.input.messages") if k in attrs), None)
    return normalized_inputs(value, sdk=True)


def _authenticated_target(target):
    """Require an explicit current-user scope without assuming UUID4 syntax."""
    fields = ('space_id', 'agent_uuid', 'agent_product', 'authenticated_user_id')
    return (
        isinstance(target, dict)
        and all(type(target.get(key)) is str and target[key]
                and target[key].strip() == target[key] for key in fields)
        and len(target['authenticated_user_id']) <= 36
    )


def compare_source_fields(expected, actual, target=None):
    """Original business fields plus documented authenticated identity mapping."""
    failures = []
    attrs = actual.get("attributes", {})
    for key, value in expected.get("attributes", {}).items():
        if key == "user.id":
            # user.id is an untrusted authentication claim. Ingestion preserves
            # the SDK application user in both distinct semantic aliases.
            if attrs.get("langfuse.user.id") != value or attrs.get("gen_ai.user.id") != value:
                failures.append("application_user_alias")
        elif attrs.get(key) != value:
            failures.append("attribute:" + key)
    resource = actual.get("resource_attributes", actual.get("resource", {}))
    if target is not None and not isinstance(resource, dict):
        return failures + ["authenticated_resource"]
    identity_keys = {"space_id", "space.id", "aivision.space.id", "ai.vision.space.id", "ant.space.id", "user_id", "user.id", "aivision.user.id", "agent_id", "agent.id", "aivision.agent.id", "agent_product", "agent.product", "aivision.agent.product", "aivision.ingest.key_id"}
    for key, value in expected.get("resource", {}).items():
        if key not in identity_keys and resource.get(key) != value:
            failures.append("resource:" + key)
    if target is not None:
        if not _authenticated_target(target):
            return failures + ["authenticated_target"]
        if "user.id" in attrs:
            failures.append("authenticated_attribute:user.id")
        for key, value in (("aivision.space.id", target["space_id"]), ("aivision.agent.id", target["agent_uuid"]), ("aivision.agent.product", target["agent_product"]), ("aivision.user.id", target["authenticated_user_id"])):
            if type(resource.get(key)) is not str or resource[key] != value:
                failures.append("authenticated_resource:" + key)
    return failures


def is_chat_call(span):
    # Framework chain wrappers may carry a model-name attribute. Only actual
    # SDK generation observations are billable model calls.
    return observation_type(span) == "GENERATION"


def _message_history(session):
    messages = []
    for row in session.get("fixture_ledger", []):
        if row.get("kind") == "model_started":
            messages.extend(row.get("input_messages", []))
    for result in session.get("business_results", []):
        messages.extend(result.get("messages", []))
    return named_messages(messages)


def named_messages(messages):
    """Names come only from actual preceding AI tool-call IDs, never content."""
    names, answer = {}, []
    for message in messages:
        for call in message.get("tool_calls", []) or []:
            if call.get("id") and call.get("name"):
                names[call["id"]] = call["name"]
        item = dict(message)
        if item.get("type") in ("tool", "toolmessage") and not item.get("name"):
            item["name"] = names.get(item.get("tool_call_id"))
        answer.append(item)
    return answer


def _retrieval_scenario_path(session, spans, scenario):
    """Require the actual empty/error/retry path, not only its final error code."""
    error = "Controlled retrieval failure after actual vector search"
    rows = [row for row in session.get("fixture_ledger", [])
            if row.get("kind") in ("retrieval", "tool_result", "tool_error")]
    expected = ([('retrieval', None), ('tool_error', 'retrieve_policy'),
                 ('retrieval', None), ('tool_result', 'retrieve_policy'),
                 ('tool_result', 'calculate_storage')] if scenario == "retry" else
                [('retrieval', None), ('tool_result' if scenario == "no-results" else 'tool_error', 'retrieve_policy')])
    if [(row.get("kind"), row.get("name")) for row in rows] != expected:
        return False
    retrievals = [row for row in rows if row["kind"] == "retrieval"]
    for attempt, row in enumerate(retrievals, 1):
        if (type(row.get("attempt")) is not int or row["attempt"] != attempt or
                row.get("tenant") != "orion" or
                row.get("query") != "Storage policy for tenant orion. Retention: 14 days. Daily storage: 10 GiB." or
                row.get("document_ids") != ([] if scenario == "no-results" else ["policy-orion"])):
            return False
    tools = [row for row in rows if row["kind"] in ("tool_result", "tool_error")]
    def name(span):
        return span.get("attributes", {}).get("gen_ai.tool.name") or span.get("name")
    sdk = sorted([span for span in spans if observation_type(span) == "TOOL" and
                  name(span) in ("retrieve_policy", "calculate_storage")],
                 key=lambda span: span.get("start_time_unix_nano", 0))
    if len(sdk) != len(tools):
        return False
    messages = _message_history(session)
    previous_message = -1
    for tool, span in zip(tools, sdk):
        target = tool.get("result") if tool["kind"] == "tool_result" else tool.get("error")
        if (name(span) != tool["name"] or span_io(span, "input") != tool.get("arguments") or
                span_io(span, "output") != target):
            return False
        if tool["name"] == "retrieve_policy" and tool.get("arguments") != {"tenant": "orion"}:
            return False
        if tool["kind"] == "tool_error" and target != error:
            return False
        positions = [i for i, message in enumerate(messages) if i > previous_message and
                     message.get("type") in ("tool", "toolmessage") and message.get("name") == tool["name"] and
                     bool(message.get("tool_call_id")) and decoded(message.get("content")) == target and any(
                         call.get("id") == message.get("tool_call_id") and call.get("name") == tool["name"] and
                         decoded(call.get("args")) == tool.get("arguments")
                         for previous in messages[:i] for call in previous.get("tool_calls", []) or [])]
        if not positions:
            return False
        previous_message = positions[0]
    if scenario == "retry":
        return all(left.get("end_time_unix_nano", 1) <= right.get("start_time_unix_nano", 0)
                   for left, right in zip(sdk, sdk[1:]))
    outputs = [decoded(result.get("output", result.get("final", result.get("result"))))
               for result in session.get("business_results", [])]
    if not outputs or not all(isinstance(output, dict) and output.get("tenant") == "orion" for output in outputs):
        return False
    if scenario == "retrieval-error":
        return all(output.get("message") == error for output in outputs)
    retrieved = tools[0].get("result")
    if not isinstance(retrieved, dict) or retrieved.get("tenant") != "orion":
        return False
    documents = retrieved.get("documents", retrieved.get("docs"))
    receipt = retrieved.get("receipt")
    return (documents == [] and isinstance(receipt, str) and bool(receipt) and receipt == session.get("receipt") and
            all(output.get("receipt") == receipt for output in outputs))


def _usage_tuple(value):
    if value is None:
        return (None, None, None)
    return tuple(value.get(k) for k in ("input_tokens", "output_tokens", "total_tokens"))


def _business_user_matches(span):
    attrs = span.get("attributes", {})
    values = [attrs[key] for key in ("gen_ai.user.id", "langfuse.user.id", "user.id") if attrs.get(key) is not None]
    return bool(values) and all(value == "fake-rag-user" for value in values)


def _within(span, scope):
    """Compare original SDK nanoseconds, without tolerance or time repair."""
    values = [item.get(key) for item in (scope, span) for key in ("start_time_unix_nano", "end_time_unix_nano")]
    return all(isinstance(value, int) and not isinstance(value, bool) for value in values) and values[0] <= values[2] <= values[3] <= values[1]


def _worker_task_scope(scope, task, own, index, cancelled_ids, relay_cleanup):
    siblings = scope.get("trace_id") == task.get("trace_id") and bool(scope.get("parent_span_id")) and scope.get("parent_span_id") == task.get("parent_span_id")
    activities = [span for span in own if observation_type(span) in ("GENERATION", "EMBEDDING", "TOOL") and _below(span, scope, index)]
    roots = [span for span in own if span.get("trace_id") == scope.get("trace_id") and not span.get("parent_span_id")]
    root_contained = len(roots) == 1 and all(_within(span, roots[0]) for span in [scope, task] + activities)
    worker_contained = all(_within(span, scope) for span in activities)
    if root_contained and worker_contained and (_below(scope, task, index) or siblings) and _within(scope, task) and all(_within(span, task) for span in activities):
        return True, None
    if not relay_cleanup or not siblings:
        return False, None
    orchestrator = index.get(scope.get("parent_span_id"), {})
    cancelled = [span for span in activities if span.get("span_id") in cancelled_ids]
    finished = [span for span in activities if span.get("span_id") not in cancelled_ids]
    same_error_path = (orchestrator.get("name") == "invoke_agent rag-orchestrator" and
                       all(status_code(span.get("status")) == 2 for span in [scope, task, orchestrator] + cancelled))
    orchestrator_contained = all(_within(span, orchestrator) for span in [scope, task] + activities)
    root_contained = root_contained and len(roots) == 1 and _within(orchestrator, roots[0])
    begins_in_task = all(isinstance(span.get("start_time_unix_nano"), int) and
                         span["start_time_unix_nano"] >= task.get("start_time_unix_nano", float("inf")) for span in [scope] + cancelled)
    accepted = (root_contained and worker_contained and orchestrator_contained and same_error_path and
                bool(cancelled) and begins_in_task and all(_within(span, task) for span in finished))
    tail = {"task_span_id": task.get("span_id"), "researcher_span_id": scope.get("span_id"),
            "researcher_tail_ns": scope.get("end_time_unix_nano", 0) - task.get("end_time_unix_nano", 0),
            "cancelled_generation_tails": [{"span_id": span.get("span_id"), "tail_ns": span.get("end_time_unix_nano", 0) - task.get("end_time_unix_nano", 0)} for span in cancelled],
            "orchestrator_root_contained": orchestrator_contained and root_contained, "accepted": bool(accepted)}
    return bool(accepted), tail


def verify_source_report(report, exports):
    """Reconcile real framework operations with unmodified SDK OTLP capture.

    The fixture's `expected` member is deliberately never accessed. Business
    literals and per-completed-call token constants are independent oracles.
    """
    failures, checks = [], {}
    def check(name, condition):
        checks[name] = bool(condition)
        if not condition:
            failures.append(name)
    rows = exports.get("exports", []) if isinstance(exports, dict) else exports
    spans = export_spans(exports)
    sessions = report.get("sessions", [])
    scenario = report.get("scenario", "success")
    cancelled = scenario in ("cancel", "cancellation")
    limits = [dict(value) if isinstance(value, dict) else value for value in report.get("known_limits", [])]
    generation_accounting = []
    cancel_cleanup_tails = []
    relay_cancel_limit = cancelled and "relay" in report.get("sdk", "").lower() and any(
        (limit.get("id") if isinstance(limit, dict) else limit) == "NR-002" for limit in limits)
    relay_cleanup = (relay_cancel_limit and report.get("sdk") == "nemo-relay" and scenario == "cancel" and
                     report.get("framework") == "deepagents" and report.get("versions", {}).get("nemo-relay") == "0.9.3")
    check("report_schema", report.get("schema_version") == 1 and bool(sessions))
    check("framework_run_completed", report.get("status") in ("passed", "known_limitation") and not report.get("error") and not report.get("cleanup_error") and all(report.get("checks", {}).values()))
    check("sdk_ingestion_accepted", bool(rows) and all(200 <= row.get("http_status", 0) < 300 and
        row.get("otlp_response_decoded") is True and row.get("otlp_rejected_spans", 0) == 0 and
        not row.get("otlp_error_message_present") for row in rows))
    if report.get("metadata_contract") == "python-sdk-json-types-v1":
        sdk_version = report.get("versions", {}).get("langfuse", "")
        major = int(sdk_version.split(".")[0])
        check("sdk_python_wire_version", bool(rows) and all(row.get("sdk_version") == sdk_version for row in rows))
        check("sdk_python_http_protobuf", all(row.get("content_type", "").split(";", 1)[0] == "application/x-protobuf" for row in rows))
        if major >= 5:
            check("sdk_python_default_gzip", all(row.get("content_encoding") == "gzip" for row in rows))
            check("sdk_python_ingestion_v4", all(row.get("ingestion_version") == "4" for row in rows))
    roots = [root for session in sessions for root in session.get("roots", [])]
    tids = [r.get("trace_id") for r in roots]
    check("declared_roots_unique", bool(tids) and all(tids) and len(tids) == len(set(tids)))
    check("exact_trace_inventory", bool(spans) and set(tids) == {s.get("trace_id") for s in spans})
    ids = [s.get("span_id") for s in spans]
    check("span_ids_unique", bool(ids) and all(ids) and len(ids) == len(set(ids)))
    index = {s.get("span_id"): s for s in spans}
    parent_valid = True
    for span in spans:
        seen = {span.get("span_id")}
        current = span
        while current.get("parent_span_id"):
            parent = index.get(current["parent_span_id"])
            if not parent or parent.get("trace_id") != span.get("trace_id") or parent.get("span_id") in seen:
                parent_valid = False
                break
            seen.add(parent["span_id"])
            current = parent
    check("parent_graph_same_trace_acyclic", parent_valid)
    for root in roots:
        candidates = [s for s in spans if s.get("trace_id") == root.get("trace_id") and not s.get("parent_span_id")]
        prefix = "root." + str(root.get("trace_id", "missing"))
        check(prefix + ".one_root", len(candidates) == 1)
        if candidates:
            check(prefix + ".business_user", _business_user_matches(candidates[0]))
            check(prefix + ".status", status_code(candidates[0].get("status")) == status_code(root.get("status")))
            app_status = root.get("application_status")
            expected_codes = (0, 1) if app_status == "completed" else (2,) if app_status in ("cancelled", "error") else ()
            check(prefix + ".application_status", status_code(candidates[0].get("status")) in expected_codes)
            level = candidates[0].get("attributes", {}).get("langfuse.observation.level")
            if level is not None:
                check(prefix + ".application_level", (str(level).upper() == "ERROR") == (app_status in ("cancelled", "error")))
            check(prefix + ".actual_io", span_io(candidates[0], "input") == decoded(root.get("input")) and span_io(candidates[0], "output") == decoded(root.get("output")))
            if report.get("metadata_contract") == "python-sdk-json-types-v1":
                # Literal values are independent of the producer fixture.
                expected_metadata = {
                    "text": "中文 🧪", "numeric_string": "42", "boolean_string": "true",
                    "null_string": "null", "quoted_string": '"quoted"', "empty_string": "",
                    "number": 42, "boolean": True, "object": {"nested": None, "value": "42"},
                    "array": ["x", 2, False, None],
                }
                major = int(report["versions"]["langfuse"].split(".")[0])
                original = candidates[0].get("attributes", {})
                values = {key[len("langfuse.observation.metadata."):]: value
                          for key, value in original.items() if key.startswith("langfuse.observation.metadata.")}
                expected_wire = {key: json.dumps(value, ensure_ascii=False) if major >= 5 or
                                 not isinstance(value, (str, int)) else value
                                 for key, value in expected_metadata.items()}
                # Compare decoded container content, but retain JSON quoting of
                # strings so a double-encoding or lost string type cannot pass.
                def equal_wire(actual, expected):
                    if isinstance(expected, str) and isinstance(actual, str):
                        try:
                            return json.dumps(json.loads(actual), sort_keys=True) == json.dumps(json.loads(expected), sort_keys=True)
                        except (ValueError, TypeError):
                            return actual == expected
                    return type(actual) is type(expected) and actual == expected
                check(prefix + ".metadata_wire_types", all(key in values and equal_wire(values[key], value)
                      for key, value in expected_wire.items()) and "null_value" not in values)
                if major >= 5:
                    actual_metadata = projected_metadata(original, "langfuse.observation.metadata", candidates[0])
                    check(prefix + ".metadata_json_types", all(key in actual_metadata and
                          json.dumps(actual_metadata[key], sort_keys=True) == json.dumps(value, sort_keys=True)
                          for key, value in expected_metadata.items()))
            if cancelled:
                check(prefix + ".cancel_error", status_code(candidates[0].get("status")) == 2)
            if scenario == "tool-exception":
                check(prefix + ".tool_exception_error", status_code(candidates[0].get("status")) == 2)
    for n, session in enumerate(sessions):
        label = "session." + str(n)
        own_tids = {r.get("trace_id") for r in session.get("roots", [])}
        own = [s for s in spans if s.get("trace_id") in own_tids]
        ledger = session.get("fixture_ledger", [])
        started = [r for r in ledger if r.get("kind") == "model_started"]
        start_index = {r.get("call_id"): r for r in started}
        completed = [r for r in ledger if r.get("kind") == "model_completed"]
        stopped = [r for r in ledger if r.get("kind") == "model_cancelled"]
        embeddings = [r for r in ledger if r.get("kind") == "embedding"]
        tools = [r for r in ledger if r.get("kind") in ("tool_result", "tool_error")]
        chat = [s for s in own if is_chat_call(s)]
        sdk_embeddings = [s for s in own if observation_type(s) == "EMBEDDING"]
        check(label + ".model_calls", len(chat) == len(completed) + len(stopped))
        expected_usage = (None, None, None) if scenario == "unknown-usage" else (0, 0, 0) if scenario == "zero-usage" else (13, 5, 18)
        check(label + ".literal_completed_usage", all(_usage_tuple(r.get("usage")) == expected_usage for r in completed))
        wanted_usage = Counter(_usage_tuple(r.get("usage")) for r in completed)
        wanted_usage.update([(None, None, None)] * len(stopped))
        actual_usage = Counter(tuple(span_usage(s)[k] for k in ("input", "output", "total")) for s in chat)
        check(label + ".sdk_per_call_usage", wanted_usage == actual_usage)
        remaining_chat = list(chat)
        matched_cancelled_ids = set()
        worker_scopes = [s for s in own if s.get("name") in ("researcher", "invoke_agent researcher")]
        for i, call in enumerate(completed):
            matches = [s for s in remaining_chat if model_identity(s) == call.get("model_name") and
                       output_signature(chat_output(s)) == output_signature(call) and
                       generation_input(s) is not None and generation_input(s) == normalized_inputs(start_index.get(call.get("call_id"), {}).get("input_messages")) and
                       tuple(span_usage(s)[k] for k in ("input", "output", "total")) == _usage_tuple(call.get("usage"))]
            if report.get("framework") == "deepagents":
                matches = [s for s in matches if any(_below(s, scope, index) for scope in worker_scopes) == (call.get("role") == "worker")]
            check(label + ".completed_model_io." + str(i), len(matches) > 0)
            if matches:
                remaining_chat.remove(matches[0])
        exported_cancelled = len(remaining_chat)
        generation_accounting.append({"session_index": n, "started": len(started), "completed": len(completed), "cancelled": len(stopped),
            "exported": len(chat), "exported_cancelled": exported_cancelled, "missing_cancelled": max(0, len(stopped) - exported_cancelled)})
        check(label + ".model_started_recorded", bool(started))
        if started:
            start_ids = [r.get("call_id") for r in started]
            finish_ids = [r.get("call_id") for r in completed + stopped]
            check(label + ".model_lifecycle", all(start_ids) and len(start_ids) == len(set(start_ids)) and Counter(start_ids) == Counter(finish_ids))
        if cancelled:
            check(label + ".actual_partial_cancellation", bool(stopped) and any(r.get("kind") == "model_partial" and r.get("text") and r.get("call_id") in {c.get("call_id") for c in stopped} for r in ledger))
            check(label + ".cancelled_generation_error_unknown", all(status_code(s.get("status")) == 2 and span_usage(s) == {"input": None, "output": None, "total": None} for s in remaining_chat))
            check(label + ".cancelled_generation_accounted", exported_cancelled == len(stopped))
            remaining_cancelled = list(remaining_chat)
            for i, call in enumerate(stopped):
                start = start_index.get(call.get("call_id"), {})
                matches = [s for s in remaining_cancelled if generation_input(s) is not None and generation_input(s) == normalized_inputs(start.get("input_messages")) and
                           model_identity(s) == start.get("model_name") and status_code(s.get("status")) == 2]
                check(label + ".cancelled_model_identity_input." + str(i), bool(matches))
                if matches:
                    span = matches[0]
                    matched_cancelled_ids.add(span.get("span_id"))
                    partial = "".join(row.get("text", "") for row in ledger if row.get("kind") == "model_partial" and row.get("call_id") == call.get("call_id"))
                    raw = decoded(span.get("attributes", {}).get("langfuse.observation.output"))
                    if relay_cancel_limit:
                        check(label + ".relay_cancel_usage_unknown." + str(i), span_usage(span) == {"input": None, "output": None, "total": None})
                    else:
                        check(label + ".cancel_partial_output." + str(i), isinstance(raw, dict) and raw.get("partial_text") == partial and raw.get("complete") is False)
                    remaining_cancelled.remove(span)
        check(label + ".embedding_call_count", len(embeddings) == len(sdk_embeddings))
        check(label + ".embedding_usage_unknown", all(span_usage(s) == {"input": None, "output": None, "total": None} for s in sdk_embeddings))
        check(label + ".embedding_real_vectors", all(_valid_vectors(r) for r in embeddings))
        # Compare one real SDK observation for each actual application embedding.
        remaining = list(sdk_embeddings)
        for item in embeddings:
            matches = [s for s in remaining if span_io(s, "input") == item.get("inputs") and span_io(s, "output") == item.get("output")]
            check(label + ".embedding_io." + str(len(remaining)), bool(matches))
            if matches:
                remaining.remove(matches[0])
        sdk_tools = [s for s in own if observation_type(s) == "TOOL"]
        retrieval_spans = [s for s in sdk_tools if "retriev" in (s.get("attributes", {}).get("gen_ai.tool.name") or s.get("name", ""))]
        # LangChain callback contexts make query embeddings children of the
        # real RETRIEVER observation, while indexing is under the TOOL. Both
        # must remain within this trace's validated parent graph.
        check(label + ".embedding_parent_retrieval", all(
            observation_type(index.get(s.get("parent_span_id"), {})) in ("TOOL", "RETRIEVER") or
            (index.get(s.get("parent_span_id"), {}).get("name") == "invoke_agent tools" and any(
                t.get("trace_id") == s.get("trace_id") and t.get("start_time_unix_nano", 1) <= s.get("start_time_unix_nano", 0) and
                t.get("end_time_unix_nano", 0) >= s.get("end_time_unix_nano", 1) for t in retrieval_spans))
            or (index.get(s.get("parent_span_id"), {}).get("name") == "invoke_agent researcher" and any(
                t.get("trace_id") == s.get("trace_id") and t.get("start_time_unix_nano", 1) <= s.get("start_time_unix_nano", 0) and
                t.get("end_time_unix_nano", 0) >= s.get("end_time_unix_nano", 1) for t in retrieval_spans))
            for s in sdk_embeddings))
        remaining_tools = list(sdk_tools)
        for i, tool in enumerate(tools):
            candidates = [s for s in remaining_tools if (s.get("attributes", {}).get("gen_ai.tool.name") or s.get("name")) == tool.get("name") and
                          span_io(s, "input") == tool.get("arguments")]
            if tool.get("kind") == "tool_result":
                candidates = [s for s in candidates if span_io(s, "output") == tool.get("result")]
            else:
                candidates = [s for s in candidates if str(tool.get("error", "")) in str(span_io(s, "output")) or status_code(s.get("status")) == 2]
            check(label + ".tool_io." + str(i), bool(candidates))
            if candidates:
                remaining_tools.remove(candidates[0])
        check(label + ".exact_tool_operations", all(report.get("framework") == "deepagents" and (s.get("attributes", {}).get("gen_ai.tool.name") or s.get("name")) == "task" for s in remaining_tools))
        check(label + ".real_retrieval_embedding_generation", bool(embeddings) and bool(retrieval_spans) and
              bool(chat) and any("retriev" in r.get("name", "") for r in tools))
        if scenario == "retry":
            check(label + ".retry_then_success", _retrieval_scenario_path(session, own, scenario))
        if scenario == "tool-exception":
            check(label + ".unhandled_tool_error", any(status_code(s.get("status")) == 2 for s in retrieval_spans) and
                  any(r.get("kind") == "tool_error" for r in tools) and not any("calculat" in r.get("name", "") for r in tools))
        results = session.get("business_results", [])
        history = _message_history(session)
        if scenario in ("no-results", "retrieval-error"):
            check(label + ".controlled_retrieval_path", _retrieval_scenario_path(session, own, scenario))
        successful = scenario not in ("no-results", "retrieval-error", "tool-exception", "cancel", "cancellation")
        allows_no_result = cancelled or scenario == "tool-exception"
        check(label + ".business_result_present", bool(results) or allows_no_result)
        completed_roots = [root for root in session.get("roots", []) if root.get("application_status") == "completed"]
        root_phases = Counter(root.get("phase") for root in session.get("roots", []))
        completed_phases = Counter(root.get("phase") for root in completed_roots)
        result_phases = Counter(result.get("phase") for result in results)
        check(label + ".completed_turn_result_inventory", (allows_no_result or len(completed_roots) == len(session.get("roots", []))) and
              all(count <= root_phases[phase] for phase, count in result_phases.items()) and
              all(result_phases[phase] >= count for phase, count in completed_phases.items()))
        check(label + ".completed_turn_result_output", all(any(
            result.get("phase") == root.get("phase") and decoded(result.get("output", result.get("final", result.get("result")))) not in (None, "", {}, []) and
            decoded(result.get("output", result.get("final", result.get("result")))) == decoded(root.get("output"))
            for result in results) for root in completed_roots))
        if scenario == "success":
            check(label + ".checkpoint_root_inventory", Counter(root.get("phase") for root in session.get("roots", [])) == Counter({"core": 1, "followup": 1}))
            core = next((result for result in results if result.get("phase") == "core"), {})
            followup = next((result for result in results if result.get("phase") == "followup"), {})
            followup_root = next((root for root in session.get("roots", []) if root.get("phase") == "followup"), {})
            followup_spans = [span for span in own if span.get("trace_id") == followup_root.get("trace_id")]
            followup_models = [span for span in followup_spans if is_chat_call(span)]
            prior_messages = normalized_inputs(core.get("messages", []))
            request = decoded(followup_root.get("input"))
            request = request.get("request") if isinstance(request, dict) else request
            inputs = generation_input(followup_models[0]) if len(followup_models) == 1 else None
            check(label + ".checkpoint_prior_messages", bool(prior_messages) and inputs is not None and len(inputs) >= len(prior_messages) + 1 and
                  inputs[-len(prior_messages)-1:-1] == prior_messages and inputs[-1].get("role") == "user" and inputs[-1].get("content") == request)
            check(label + ".checkpoint_no_repeated_work", bool(followup_models) and not any(observation_type(span) in ("TOOL", "EMBEDDING") for span in followup_spans))
            outputs = [decoded(result.get("output", result.get("final", result.get("result")))) for result in (core, followup)]
            receipts = [tool.get("result", {}).get("receipt") for tool in tools if tool.get("kind") == "tool_result" and tool.get("name") == "retrieve_policy"]
            check(label + ".checkpoint_same_retrieval_receipt", bool(session.get("receipt")) and session["receipt"] in receipts and
                  all(isinstance(output, dict) and output.get("receipt") == session["receipt"] for output in outputs))
        for i, result in enumerate(results):
            output = decoded(result.get("output", result.get("final", result.get("result"))))
            messages = result.get("messages", [])
            # Child histories live in actual model inputs, not parent's task-only output.
            messages = history + messages if report.get("framework") == "deepagents" else messages
            applicable = [t for t in tools if t.get("kind") == "tool_result" and any(
                m.get("name") == t.get("name") and decoded(m.get("content")) == t.get("result") for m in messages)]
            if successful:
                problems = verify_business(output, {"tools": applicable, "messages": messages}, "changed-corpus" if scenario == "changed-corpus" else result.get("phase", scenario))
                for problem in problems:
                    check(label + ".business." + str(i) + "." + problem, False)
                check(label + ".business." + str(i), not problems)
            elif scenario in ("no-results", "retrieval-error"):
                code = "no_results" if scenario == "no-results" else "retrieval_error"
                check(label + ".controlled_error." + str(i), isinstance(output, dict) and output.get("error") == code)
        if report.get("framework") == "deepagents":
            roles = {r.get("role") for r in completed + stopped}
            tasks = [s for s in sdk_tools if s.get("name") == "task" or s.get("attributes", {}).get("gen_ai.tool.name") == "task"]
            check(label + ".subagent_parent_worker_models", {"parent", "worker"}.issubset(roles))
            check(label + ".subagent_task_span", bool(tasks))
            check(label + ".subagent_embedding_worker_scope", bool(worker_scopes) and all(any(_below(s, scope, index) for scope in worker_scopes) for s in sdk_embeddings))
            check(label + ".subagent_cancelled_model_worker_scope", all(any(_below(s, scope, index) for scope in worker_scopes) for s in remaining_chat))
            if cancelled:
                requested_tasks = [call for row in completed if row.get("role") == "parent" for call in row.get("tool_calls", []) if call.get("name") == "task"]
                check(label + ".subagent_cancelled_task_request_error", bool(requested_tasks) and any(
                    status_code(task.get("status")) == 2 and span_io(task, "input") == decoded(call.get("args"))
                    for task in tasks for call in requested_tasks))
            elif scenario != "tool-exception":
                task_messages = [m for m in history if m.get("type") == "tool" and m.get("name") == "task"]
                task_io_ok = bool(task_messages) and all(any(
                    span_io(task, "output") == decoded(message.get("content")) and any(
                        call.get("id") == message.get("tool_call_id") and call.get("name") == "task" and
                        span_io(task, "input") == decoded(call.get("args"))
                        for m in history for call in m.get("tool_calls", []) or []) for task in tasks)
                    for message in task_messages)
                check(label + ".subagent_task_real_io", task_io_ok)
            elif scenario == "tool-exception":
                check(label + ".subagent_task_error", any(status_code(task.get("status")) == 2 for task in tasks))
            worker_task_valid = bool(worker_scopes)
            for scope in worker_scopes:
                matches = [_worker_task_scope(scope, task, own, index, matched_cancelled_ids, relay_cleanup) for task in tasks]
                worker_task_valid = worker_task_valid and any(valid for valid, _ in matches)
                for _, tail in matches:
                    if tail is not None:
                        cancel_cleanup_tails.append({"session_index": n, **tail})
            check(label + ".subagent_worker_task_scope", worker_task_valid)
        session_attrs = [s.get("attributes", {}).get("session.id", s.get("attributes", {}).get("langfuse.session.id")) for s in own]
        check(label + ".session_isolation", all(v is None or v == session.get("session_id") for v in session_attrs))
    if scenario == "concurrent":
        receipts = [s.get("receipt") for s in sessions]
        check("concurrent_distinct_sessions_and_receipts", len(sessions) > 1 and len({s.get("session_id") for s in sessions}) == len(sessions) and all(receipts) and len(set(receipts)) == len(receipts))
    generation_complete = all(row["missing_cancelled"] == 0 for row in generation_accounting)
    if relay_cancel_limit:
        for limit in limits:
            if isinstance(limit, dict) and limit.get("id") == "NR-002":
                limit["generation_accounting"] = generation_accounting
                limit["description"] = "The adapter now closes Relay 0.9.3 cancellation scopes; this oracle requires every started generation to be exported. Official Relay partial output, cancellation usage and TTFT remain unavailable. DeepAgents ERROR task/researcher siblings may finish cancelled-generation/researcher cleanup after task end, within the actual orchestrator and root; original timestamps and measured tail_ns are preserved."
                limit["accepted_scope"] = "Every started/completed/cancelled model generation, root, task, tool, embedding, input and parent is required. Completed generations, embeddings and business tools stay inside task time. Only nemo-relay 0.9.3 DeepAgents cancel ERROR siblings admit the reported cleanup tail; no missing generation export, other SDK/version/scenario, completed work tail, or orchestrator/root escape is accepted."
                limit["cancel_cleanup_tails"] = cancel_cleanup_tails
    return {"passed": not failures, "source_verified": not failures, "fully_verified": not failures and not limits,
            "status": "failed" if failures else "known_limitation" if limits else "passed", "platform_query_verified": False,
            "checks": checks, "failures": failures, "known_limits": limits,
            "coverage_checks": {"generation_exports_complete": generation_complete}, "generation_accounting": generation_accounting,
            "cancel_cleanup_tails": cancel_cleanup_tails,
            "counts": {"spans": len(spans), "roots": len(roots), "sessions": len(sessions),
                       "chat": sum(is_chat_call(s) for s in spans), "embedding": sum(observation_type(s) == "EMBEDDING" for s in spans)},
            "usage": usage_totals(spans)}


def _below(span, parent, index):
    current, seen = span, set()
    while current.get("parent_span_id") and current.get("span_id") not in seen:
        seen.add(current.get("span_id"))
        if current["parent_span_id"] == parent.get("span_id"):
            return True
        current = index.get(current["parent_span_id"], {})
    return False


def _valid_vectors(row):
    output = row.get("output")
    if not isinstance(output, list) or not output:
        return False
    if row.get("op") == "embed_query":
        return len(output) == row.get("dimensions") and row.get("vector_count") == 1 and all(isinstance(v, (int, float)) for v in output)
    return len(output) == row.get("vector_count") and all(isinstance(v, list) and len(v) == row.get("dimensions") and all(isinstance(x, (int, float)) for x in v) for v in output)


def http_query(target, path, *, params=None, body=None, key=None, basic=False):
    """Return response data in memory. No request/response/secret is logged."""
    endpoint = str(target.get("endpoint", target.get("base_url", ""))).rstrip("/")
    query = urllib.parse.urlencode(params or {})
    url = endpoint + path + ("?" + query if query else "")
    secret = target["space_key"] if key is None else key
    authorization = "Basic " + base64.b64encode((target["agent_uuid"] + ":" + secret).encode()).decode() if basic else "Bearer " + secret
    headers = {"Authorization": authorization, "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, headers=headers, data=None if body is None else json.dumps(body).encode("utf-8"), method="GET" if body is None else "POST")
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=20) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as response:
        try:
            value = json.loads(response.read())
        except (ValueError, UnicodeDecodeError):
            value = {}
        return response.code, value


def canonical_usage(spans):
    calls = [s for s in spans if observation_type(s) in ("GENERATION", "EMBEDDING")]
    values = [span_usage(s) for s in calls]
    return {key: sum(v[key] for v in values) if values and all(v[key] is not None for v in values) else None
            for key in ("input", "output", "total")}


def reported_usage(spans):
    """Known per-field subtotal of unique original SDK calls, never a full total."""
    calls = {(s.get("trace_id"), s.get("span_id")): s for s in spans
             if observation_type(s) in ("GENERATION", "EMBEDDING")}
    values = [span_usage(span) for span in calls.values()]
    answer = {}
    for field in ("input", "output", "total"):
        known = [usage[field] for usage in values if usage[field] is not None]
        answer[field] = {"value": sum(known) if known else None, "reportedCalls": len(known)}
    return answer


def usage_by_type(spans):
    """Type aggregates keep the complete original span ancestry for ambiguity."""
    nodes = {(s.get("trace_id"), s.get("span_id")): s for s in spans}
    calls = {key: span for key, span in nodes.items()
             if observation_type(span) in ("GENERATION", "EMBEDDING")}
    ambiguous = set()
    for key, span in calls.items():
        parent, seen = (key[0], span.get("parent_span_id")), set()
        while parent in nodes and parent not in seen:
            seen.add(parent)
            if parent in calls:
                ambiguous.update((key, parent))
            parent = (parent[0], nodes[parent].get("parent_span_id"))
    answer = []
    for kind in ("GENERATION", "EMBEDDING"):
        members = {key: span for key, span in calls.items() if observation_type(span) == kind}
        if not members:
            continue
        nested = bool(ambiguous.intersection(members))
        usage = dict.fromkeys(("input", "output", "total")) if nested else canonical_usage(members.values())
        usage["unknownReason"] = "ambiguous_nested_generations" if nested else "usage_missing" if usage["total"] is None else None
        answer.append({"type": kind, "callCount": len(members), "usage": usage,
                       "reportedUsage": reported_usage(members.values())})
    return answer


def _matches_reported_usage(actual, expected):
    if not isinstance(actual, dict):
        return False
    for field, wanted in expected.items():
        value = actual.get(field)
        if not isinstance(value, dict) or "value" not in value or "reportedCalls" not in value:
            return False
        if type(value["reportedCalls"]) is not int or value["reportedCalls"] != wanted["reportedCalls"]:
            return False
        if value["value"] is not None and type(value["value"]) not in (int, float):
            return False
        if value["value"] != wanted["value"]:
            return False
    return True


def _matches_usage_by_type(actual, expected):
    if not isinstance(actual, list) or len(actual) != len(expected):
        return False
    for value, wanted in zip(actual, expected):
        if not isinstance(value, dict) or value.get("type") != wanted["type"]:
            return False
        if type(value.get("callCount")) is not int or value["callCount"] != wanted["callCount"]:
            return False
        usage = value.get("usage")
        if not isinstance(usage, dict) or "unknownReason" not in usage or usage["unknownReason"] != wanted["usage"]["unknownReason"]:
            return False
        if not isinstance(usage, dict) or any(field not in usage or
                                            (usage[field] is not None and type(usage[field]) not in (int, float)) or usage[field] != count
                                            for field, count in wanted["usage"].items() if field != "unknownReason"):
            return False
        if not _matches_reported_usage(value.get("reportedUsage"), wanted["reportedUsage"]):
            return False
    return True


def iso_nano(value):
    if value is None:
        return None
    seconds, fraction = divmod(int(value), 1000000000)
    return datetime.datetime.fromtimestamp(seconds, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + (".%09dZ" % fraction)


def projected_io(span, direction):
    """Public native-v1 projection of original, arbitrary SDK JSON values."""
    attrs = span.get("attributes", {})
    keys = ("gen_ai." + direction + ".messages", "langfuse.observation." + direction,
            direction + ".value", "gen_ai.tool.call." + ("arguments" if direction == "input" else "result"), "aivision.request." + direction)
    return next((decoded(attrs[k]) for k in keys if k in attrs), None)


def projected_metadata(attrs, prefix, source=None):
    # Decide from the unmodified SDK export, never from platform normalization.
    source = source or {}
    language = source.get("resource", {}).get("telemetry.sdk.language")
    version = str(source.get("scope_version", "")).split(".", 1)[0]
    known = source.get("scope_name") == "langfuse-sdk" and version.isdigit()
    legacy_strings = known and ((language == "python" and int(version) < 5) or
                                (language in ("javascript", "nodejs") and int(version) < 6))
    value = decoded(attrs.get(prefix))
    answer = dict(value) if isinstance(value, dict) else {}
    answer.update({key[len(prefix)+1:]: value if legacy_strings else decoded(value)
                   for key, value in attrs.items() if key.startswith(prefix + ".")})
    return answer


def compare_v1_trace(expected, actual, target, *, core_only=False):
    """Independent public trace projection from original SDK fields."""
    roots = [span for span in expected if not span.get("parent_span_id")]
    if len(roots) != 1:
        return ["v1_trace_root"]
    root, failures = roots[0], []
    attrs = root.get("attributes", {})
    def first(*keys, default=None):
        return next((attrs[key] for key in keys if attrs.get(key) is not None), default)
    session_ids = {next((span.get("attributes", {}).get(key) for key in ("gen_ai.conversation.id", "langfuse.session.id", "session.id") if span.get("attributes", {}).get(key) is not None), None) for span in expected}
    session_ids.discard(None)
    metadata_prefix = "langfuse.trace.metadata" if any(key == "langfuse.trace.metadata" or key.startswith("langfuse.trace.metadata.") for key in attrs) else "langfuse.observation.metadata"
    tags = decoded(attrs.get("langfuse.trace.tags")) or []
    if not isinstance(tags, list): tags = [str(tags)]
    starts = [span.get("start_time_unix_nano") for span in expected]
    ends = [span.get("end_time_unix_nano") for span in expected if span.get("end_time_unix_nano") is not None]
    valid_times = all(isinstance(value, int) for value in starts + ends)
    timestamp = iso_nano(min(starts)) if valid_times and starts else None
    latency = (max(ends) - min(starts)) / 1000000000 if valid_times and starts and ends else None
    fields = {"id": root.get("trace_id"), "timestamp": timestamp,
              "name": attrs.get("langfuse.trace.name") or root.get("name"),
              "input": decoded(attrs["langfuse.trace.input"]) if "langfuse.trace.input" in attrs else projected_io(root, "input"),
              "output": decoded(attrs["langfuse.trace.output"]) if "langfuse.trace.output" in attrs else projected_io(root, "output"),
              "sessionId": first("gen_ai.conversation.id", "langfuse.session.id", "session.id") or (next(iter(session_ids)) if len(session_ids) == 1 else None),
              "userId": "fake-rag-user",
              "metadata": projected_metadata(attrs, metadata_prefix, root), "tags": tags,
              "release": attrs.get("langfuse.release"), "version": attrs.get("langfuse.version"),
              "environment": first("langfuse.environment", "deployment.environment.name", "deployment.environment", default="default"),
              "public": False, "htmlPath": "/project/" + str(target["agent_uuid"]) + "/traces/" + str(root.get("trace_id")),
              "latency": latency, "totalCost": None, "scores": []}
    if core_only:
        for key in ("htmlPath", "latency", "totalCost", "scores"):
            fields.pop(key)
    for key, value in fields.items():
        if key not in actual or actual.get(key) != value:
            failures.append("v1_trace_" + key)
    if core_only:
        return failures
    if "observations" not in actual or not isinstance(actual.get("observations"), list):
        failures.append("v1_trace_observations")
    else:
        failures.extend(compare_v1_observations(expected, actual["observations"]))
    return failures


def compare_v1_observations(expected, actual):
    failures = []
    wanted = {s["span_id"]: s for s in expected}
    ids = [s.get("id") for s in actual]
    if set(wanted) != set(ids) or len(ids) != len(set(ids)):
        failures.append("v1_observation_inventory")
    for observed in actual:
        source = wanted.get(observed.get("id"))
        if source is None:
            continue
        for field, value in (("traceId", source.get("trace_id")), ("name", source.get("name")),
                             ("parentObservationId", source.get("parent_span_id") or None),
                             ("type", observation_type(source)), ("model", model_identity(source) or None),
                             ("input", projected_io(source, "input")), ("output", projected_io(source, "output")),
                             ("level", str(source.get("attributes", {}).get("langfuse.observation.level", "DEFAULT")).upper()),
                             ("startTime", iso_nano(source.get("start_time_unix_nano"))),
                             ("endTime", iso_nano(source.get("end_time_unix_nano")))):
            if field not in observed or observed.get(field) != value:
                failures.append("v1_" + field)
        expected_usage = span_usage(source)
        legacy = observed.get("usage")
        if not isinstance(legacy, dict) or any(legacy.get(key) != (value if value is not None else 0) for key, value in expected_usage.items()) or legacy.get("unit") != "TOKENS":
            failures.append("v1_legacy_usage")
        if any(key in observed for key in ("inputUsage", "outputUsage", "totalUsage")):
            failures.append("v1_unexpected_legacy_alias")
        if all(v is None for v in expected_usage.values()):
            # v1 mandatory legacy numeric fields use compatibility zero; the
            # optional usageDetails must not invent a reported measurement.
            if observed.get("usageDetails") not in (None, {}):
                failures.append("v1_unknown_usage")
        else:
            details = observed.get("usageDetails", {})
            if any(details.get(k) != value for k, value in expected_usage.items() if value is not None):
                failures.append("v1_reported_usage")
    return failures


def verify_platform_report(report, exports, targets, *, query=http_query, poll_seconds=60):
    """Read the actual platform APIs; never write telemetry or configuration."""
    checks, failures, urls = {}, [], []
    def check(name, condition):
        checks[name] = bool(condition)
        if not condition:
            failures.append(name)
    sdk = report.get("sdk")
    target = targets.get(sdk)
    if not _authenticated_target(target) or not all(target.get(k) for k in ("space_id", "agent_product", "agent_uuid", "space_key")) or not target.get("endpoint", target.get("base_url")):
        return {"platform_query_verified": False, "checks": {"platform.target_configured": False}, "failures": ["platform.target_configured"], "ui_urls": []}
    spans = export_spans(exports)
    bounds = report.get("bounds", {})
    window = {"from": bounds.get("from_ms"), "to": bounds.get("to_ms")}
    check("platform.exact_window", all(isinstance(v, int) for v in window.values()) and window["from"] < window["to"])
    params = {**window, "detail": "1", "agent_uuid": target["agent_uuid"]}
    prefix = "/openapi/v1/spaces/" + str(target["space_id"]) + "/agents/" + target["agent_product"]
    endpoint = str(target.get("endpoint", target.get("base_url"))).rstrip("/")
    by_trace = {tid: [s for s in spans if s.get("trace_id") == tid] for tid in sorted({s.get("trace_id") for s in spans})}
    check("platform.source_traces_present", bool(by_trace))
    deadline = time.monotonic() + max(0, min(poll_seconds, 60))
    details = {}
    for n, (tid, expected) in enumerate(by_trace.items()):
        path = prefix + "/trace/" + tid + "/detail"
        expected_ids = {span["span_id"] for span in expected}
        while True:
            status, payload = query(target, path, params=params)
            data = payload.get("data", {}) if isinstance(payload, dict) else {}
            actual = data.get("spans", []) if isinstance(data, dict) else []
            actual_ids = [span.get("span_id") for span in actual]
            # A nonempty batch may still be only the first visible part of a
            # trace. Wait for exact SDK inventory and all original fields.
            # Extra/duplicate spans are failures, never transient omissions.
            invalid_inventory = len(actual_ids) != len(set(actual_ids)) or not set(actual_ids).issubset(expected_ids)
            lookup = {span.get("span_id"): span for span in actual}
            complete = (status == 200 and isinstance(payload, dict) and payload.get("code", 0) == 0 and
                        not compare_native_spans(expected, actual) and
                        all(not compare_source_fields(span, lookup.get(span["span_id"], {}), target) for span in expected))
            if complete or invalid_inventory or status in (400, 401, 403) or time.monotonic() >= deadline:
                break
            time.sleep(min(2, max(0, deadline - time.monotonic())))
        check("platform.native." + str(n) + ".http", status == 200 and payload.get("code", 0) == 0)
        differences = compare_native_spans(expected, actual)
        check("platform.native." + str(n) + ".exact_spans", not differences)
        # Captured attributes/resources are preserved even when query adds
        # canonical semantic attributes. Verify every original value.
        lookup = {s.get("span_id"): s for s in actual}
        preserved = all(not compare_source_fields(s, lookup.get(s["span_id"], {}), target) for s in expected)
        check("platform.native." + str(n) + ".source_fields", preserved)
        actual_roots = [span for span in actual if not span.get("parent_span_id")]
        check("platform.native." + str(n) + ".business_user", len(actual_roots) == 1 and _business_user_matches(actual_roots[0]))
        details[tid] = data
        status, trace = query(target, "/api/public/traces/" + tid, basic=True)
        check("platform.v1." + str(n) + ".trace", status == 200 and not compare_v1_trace(expected, trace, target))
        observations, page, complete = [], 1, False
        while page <= 100:
            status, payload = query(target, "/api/public/observations", params={"traceId": tid, "page": page, "limit": 2}, basic=True)
            if status != 200:
                break
            observations.extend(payload.get("data", []))
            meta = payload.get("meta", {})
            if page >= meta.get("totalPages", 1):
                complete = len(observations) == meta.get("totalItems", len(observations))
                break
            page += 1
        check("platform.v1." + str(n) + ".pagination", complete and page > 1 if len(expected) > 2 else complete)
        check("platform.v1." + str(n) + ".exact_observations", not compare_v1_observations(expected, observations))
        urls.append(str(target.get("ui_endpoint", endpoint)).rstrip("/") + "/trace?" + urllib.parse.urlencode({
            "spaceId": target["space_id"], "agent": target["agent_product"], "agent_uuid": target["agent_uuid"],
            "traceId": tid, "from": window["from"], "to": window["to"], "trace_from": window["from"], "trace_to": window["to"]}))
    sessions = report.get("sessions", [])
    session_ids = [s["session_id"] for s in sessions]
    body = {**window, "agent_uuid": target["agent_uuid"], "sessionIds": session_ids}
    usage_status, usage_payload = query(target, prefix + "/sessions/usage", body=body)
    io_status, io_payload = query(target, prefix + "/sessions/io", body=body)
    usage_rows = (usage_payload.get("data") or {}).get("data", []) if isinstance(usage_payload.get("data"), dict) else usage_payload.get("data", [])
    io_rows = (io_payload.get("data") or {}).get("data", []) if isinstance(io_payload.get("data"), dict) else io_payload.get("data", [])
    check("platform.session_usage_http", usage_status == 200)
    check("platform.session_io_http", io_status == 200)
    check("platform.session_inventory", {r.get("id") for r in usage_rows} == set(session_ids) and {r.get("id") for r in io_rows} == set(session_ids))
    for n, session in enumerate(sessions):
        own_tids = {r["trace_id"] for r in session.get("roots", [])}
        expected = canonical_usage([s for s in spans if s.get("trace_id") in own_tids])
        live = next((r for r in usage_rows if r.get("id") == session["session_id"]), {})
        check("platform.session." + str(n) + ".usage", all(live.get("usage", {}).get(k) == v for k, v in expected.items()))
        live = next((r for r in io_rows if r.get("id") == session["session_id"]), {})
        roots = session.get("roots", [])
        first = min(roots, key=lambda r: r.get("start_ns", 0)) if roots else {}
        last = max(roots, key=lambda r: r.get("start_ns", 0)) if roots else {}
        check("platform.session." + str(n) + ".io", decoded(live.get("inputValue", live.get("input"))) == decoded(first.get("input")) and
              decoded(live.get("outputValue", live.get("output"))) == decoded(last.get("output")) and
              live.get("inputSource", {}).get("traceId") == first.get("trace_id") and live.get("outputSource", {}).get("traceId") == last.get("trace_id"))
        status, live_session = query(target, "/api/public/sessions/" + urllib.parse.quote(session["session_id"], safe=""), basic=True)
        public_traces = live_session.get("traces", [])
        public_ids = [trace.get("id") for trace in public_traces]
        check("platform.v1.session." + str(n) + ".identity", status == 200 and live_session.get("id") == session["session_id"] and live_session.get("projectId") == str(target["agent_uuid"]))
        check("platform.v1.session." + str(n) + ".trace_inventory", len(public_ids) == len(own_tids) and set(public_ids) == own_tids)
        check("platform.v1.session." + str(n) + ".business_user", bool(public_traces) and all(trace.get("userId") == "fake-rag-user" and trace.get("sessionId") == session["session_id"] for trace in public_traces))
        check("platform.v1.session." + str(n) + ".trace_fields", all(trace.get("id") in own_tids and not compare_v1_trace(by_trace[trace["id"]], trace, target, core_only=True) for trace in public_traces))
        expected_created = iso_nano(min(span["start_time_unix_nano"] for span in spans if span.get("trace_id") in own_tids))
        check("platform.v1.session." + str(n) + ".created", live_session.get("createdAt") == expected_created)
    for dimension in ("model", "provider"):
        metrics_body = {**window, "agent_uuid": target["agent_uuid"], "filters": [{"field": "trace_id", "op": "in", "value": list(by_trace)}], "bucket": "minute", "groupBy": dimension}
        status, payload = query(target, prefix + "/traces/metrics", body=metrics_body)
        metrics = payload.get("data", {})
        if isinstance(metrics, dict) and "data" in metrics:
            metrics = metrics["data"]
        billable = [s for s in spans if observation_type(s) in ("GENERATION", "EMBEDDING")]
        known = [s for s in billable if span_usage(s)["total"] is not None]
        expected_groups = Counter((model_identity(s) or None) if dimension == "model" else
                                  (s.get("attributes", {}).get("gen_ai.provider.name", s.get("attributes", {}).get("gen_ai.system")) or None) for s in billable)
        actual_groups = {r.get("value"): r.get("callCount") for r in metrics.get("groups", [])}
        check("platform.metrics." + dimension + ".http", status == 200)
        check("platform.metrics." + dimension + ".inventory", metrics.get("traceCount") == len(by_trace) and metrics.get("spanCount") == len(spans) and
              metrics.get("generationCount") == sum(is_chat_call(s) for s in spans) and metrics.get("toolCount") == sum(observation_type(s) == "TOOL" for s in spans))
        check("platform.metrics." + dimension + ".groups", dict(expected_groups) == actual_groups and all(r.get("dimension") == dimension for r in metrics.get("groups", [])))
        check("platform.metrics." + dimension + ".usage", all(metrics.get("usage", {}).get(k) == v for k, v in canonical_usage(spans).items()))
        check("platform.metrics." + dimension + ".reported_usage", _matches_reported_usage(metrics.get("reportedUsage"), reported_usage(spans)))
        check("platform.metrics." + dimension + ".usage_by_type", _matches_usage_by_type(metrics.get("usageByType"), usage_by_type(spans)))
        coverage = metrics.get("coverage", {})
        check("platform.metrics." + dimension + ".known_subtotal", coverage.get("billableCalls") == len(billable) and coverage.get("knownUsageCalls") == len(known) and coverage.get("knownTokens") == sum(span_usage(s)["total"] for s in known))
        check("platform.metrics." + dimension + ".unknown_embedding_cost", all(span_usage(s)["total"] is None for s in billable if observation_type(s) == "EMBEDDING") and metrics.get("usage", {}).get("cost", {}).get("total") is None)
        grouped_usage_valid = True
        for group in metrics.get("groups", []):
            members = [s for s in billable if ((model_identity(s) or None) if dimension == "model" else
                (s.get("attributes", {}).get("gen_ai.provider.name", s.get("attributes", {}).get("gen_ai.system")) or None)) == group.get("value")]
            grouped_usage_valid = grouped_usage_valid and all(group.get("usage", {}).get(k) == v for k, v in canonical_usage(members).items())
        check("platform.metrics." + dimension + ".group_usage", grouped_usage_valid)
        # Trace metrics follow the root status; recovered child tool failures
        # remain mandatory in the native-span and V1 observation comparisons.
        expected_errors = sum(any(not s.get("parent_span_id") and status_code(s.get("status")) == 2
                                  for s in items) for items in by_trace.values())
        check("platform.metrics." + dimension + ".errors", metrics.get("errorCount") == expected_errors and metrics.get("errorRate") == expected_errors / len(by_trace) if by_trace else metrics.get("errorRate") is None)
        latencies = sorted((max(s["end_time_unix_nano"] for s in items) - min(s["start_time_unix_nano"] for s in items)) / 1000000
            for items in by_trace.values() if all(s.get("end_time_unix_nano") is not None and s.get("start_time_unix_nano") is not None for s in items))
        if latencies:
            expected_latency = {"mean": sum(latencies) / len(latencies), "p95": latencies[max(0, (len(latencies) * 95 + 99) // 100 - 1)]}
            actual_latency = metrics.get("latencyMs", {})
            check("platform.metrics." + dimension + ".latency", all(isinstance(actual_latency.get(k), (int, float)) and abs(actual_latency[k] - v) < 1e-6 for k, v in expected_latency.items()))
    if by_trace:
        tid = next(iter(by_trace))
        path = prefix + "/trace/" + tid + "/detail"
        other = next((v for v in targets.values() if v.get("space_id") != target["space_id"]), None)
        cases = [("invalid_auth", path, params, "sk-aiv-invalid-e2e", 401),
                 ("cross_space", "/openapi/v1/spaces/" + str(other["space_id"] if other else uuid.uuid4()) + "/agents/" + target["agent_product"] + "/trace/" + tid + "/detail", params, None, 403),
                 ("cross_agent", path, {**params, "agent_uuid": str(uuid.uuid4())}, None, 400),
                 ("wrong_window", path, {**params, "from": window["to"] + 3600000, "to": window["to"] + 3601000}, None, 404)]
        for name, path, query_params, key, expected in cases:
            status, payload = query(target, path, params=query_params, key=key)
            data = payload.get("data", {})
            check("platform.isolation." + name, status == expected and not (data.get("spans") if isinstance(data, dict) else data))
    return {"platform_query_verified": not failures, "checks": checks, "failures": failures, "ui_urls": urls}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--exports", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--platform", action="store_true")
    args = parser.parse_args(argv)
    result = {"source_verified": False, "platform_query_verified": False, "checks": {}, "known_limits": [], "failures": []}
    try:
        report = json.loads(args.report.read_text(encoding="utf-8"))
        exports = json.loads(args.exports.read_text(encoding="utf-8"))
        result = verify_source_report(report, exports)
        if args.platform and result["source_verified"]:
            targets = json.loads(os.environ.get("AIVISION_E2E_TARGETS_JSON", "{}"))
            platform_result = verify_platform_report(report, exports, targets)
            result["checks"].update(platform_result["checks"])
            result["failures"].extend(platform_result["failures"])
            result["platform_query_verified"] = platform_result["platform_query_verified"]
            result["ui_urls"] = platform_result["ui_urls"]
    except Exception as error:
        result["failures"].append("verification_exception:" + type(error).__name__)
    result["passed"] = bool(result["source_verified"]) and not result["failures"] and (not args.platform or result["platform_query_verified"])
    result["status"] = "failed" if not result["passed"] else "known_limitation" if result["known_limits"] else "passed"
    result["fully_verified"] = result["passed"] and not result["known_limits"]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print(json.dumps({"source_verified": result["source_verified"], "platform_query_verified": result["platform_query_verified"], "checks": len(result["checks"]), "failures": result["failures"]}))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
