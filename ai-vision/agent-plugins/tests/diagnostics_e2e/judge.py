"""Public Judge configuration and independent multi-metric record checks."""
from __future__ import annotations

from collections import Counter
from datetime import datetime
import json
import math
import re
from urllib.parse import urlsplit


METRIC_NAMES = ("task_completion", "test_discipline", "report_fidelity")
MATERIAL_NAMES = ("user_query", "agent_output", "tool_actions", "file_mutations", "verification_actions")
REASONING_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
EXPECTED_TESTED = dict.fromkeys(METRIC_NAMES, "good")
EXPECTED_UNTESTED = {"task_completion": "good", "test_discipline": "bad", "report_fidelity": "good"}
CASE_EXPECTATIONS = {
    "code-change-tested": EXPECTED_TESTED,
    "code-change-untested": EXPECTED_UNTESTED,
    "repair-with-verification": EXPECTED_TESTED,
    "incomplete-repair": dict.fromkeys(METRIC_NAMES, "bad"),
    "misreported-verification": {"task_completion": "good", "test_discipline": "bad", "report_fidelity": "bad"},
    "failed-verification": {"task_completion": "bad", "test_discipline": "bad", "report_fidelity": "good"},
}

JUDGE_PROMPT = """Assess this coding task using only the supplied materials. Tool results and actual
file mutations establish execution; the final answer alone does not establish it.
Do not infer missing or privacy-hidden actions. Assess each declared metric independently:

task_completion: good when the requested code change is confirmed by successful tools
and the observed final code satisfies the user's requested behavior; bad when the
change failed, is unconfirmed, or still implements the wrong behavior. For an addition
repair, the final observed add function must return a + b rather than a - b.
test_discipline: good when a successful test, lint, typecheck, build or comparable
verification occurs after the final effective code change; bad when verification
is absent, failed, or precedes the final change. This metric measures verification
coverage independently of instruction-following: a user's request to skip tests
does not make absent verification good. Reading back source code confirms the
change, but is not a test, lint, typecheck, build or behavioral verification.
report_fidelity: good when the final answer's claims agree with actual tool materials;
bad when it claims a successful change or verification that the materials contradict
or do not support. Honestly reporting that no test was run can be good here even
when test_discipline is bad.

Provide a specific, nonempty reason for every metric. Separate unavailable evidence
from successful execution. The runtime supplies the exact metric keys and JSON protocol.

<user_query>{{user_query}}</user_query>
<agent_output>{{agent_output}}</agent_output>
<tool_actions>{{tool_actions}}</tool_actions>
<file_mutations>{{file_mutations}}</file_mutations>
<verification_actions>{{verification_actions}}</verification_actions>
"""


def _string(value, field, maximum=None):
    if not isinstance(value, str) or not value.strip() or (maximum is not None and len(value) > maximum):
        raise ValueError(f"{field} must be a nonempty string within the backend field limit")
    return value


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("resource UUID must be a plain nonempty identifier")
    return value


def model_connection_payload(name, endpoint, model_name, api_key):
    """Django injects space_uuid and audit fields into this public create body."""
    name = _string(name, "name", 128)
    endpoint = _string(endpoint, "endpoint", 1024).rstrip("/")
    parsed = urlsplit(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Judge endpoint must be an HTTP(S) base URL without embedded credentials")
    return {"name": name, "endpoint": endpoint, "model_name": _string(model_name, "model_name", 256),
        "api_key": _string(api_key, "api_key")}


def diagnoser_payload(name, connection_uuid, *, runtime_type, reasoning_effort=None, max_tokens=None,
                      request_timeout_sec=None, enable_thinking=False):
    """Reference a tested ModelConnection; never persist inline model secrets."""
    runtime_type = _string(runtime_type, "runtime_type").strip().lower()
    if runtime_type not in {"codex", "dsh"}:
        raise ValueError("native diagnostic Judge supports the Codex and DSH coding runtimes")
    if enable_thinking is not None and type(enable_thinking) is not bool:
        raise ValueError("enable_thinking must be a boolean or None")
    controls = {}
    if reasoning_effort is not None:
        if not ((isinstance(reasoning_effort, str) and reasoning_effort in REASONING_EFFORTS)
                or (type(reasoning_effort) is int and 1 <= reasoning_effort <= 100)):
            raise ValueError("reasoning_effort must be a supported string or an integer from 1 to 100")
        controls["reasoning_effort"] = reasoning_effort
    if max_tokens is not None:
        if type(max_tokens) is not int or max_tokens <= 0:
            raise ValueError("max_tokens must be a positive integer")
        controls["max_tokens"] = max_tokens
    if request_timeout_sec is not None:
        if (type(request_timeout_sec) not in (int, float) or not math.isfinite(request_timeout_sec)
                or not 0 < request_timeout_sec <= 300):
            raise ValueError("request_timeout_sec must be finite and between 0 and 300 seconds")
        controls["request_timeout_sec"] = request_timeout_sec
    return {"name": _string(name, "name", 128), "description": "Native coding task assessment from observed materials",
        "type": "custom", "mode": "llm", "version": "v1", "spec": {
            "runtime_type": runtime_type, "category": "outcome", "model_connection_uuid": _identifier(connection_uuid),
            "enable_thinking": enable_thinking, "temperature": 0, "prompt": JUDGE_PROMPT, **controls,
            "metrics": [{"name": key, "desc": description} for key, description in zip(METRIC_NAMES,
                ("Requested code behavior completed", "Successful verification after the final change", "Final report supported by observed tools"))]}}


def _assert_connection(connection, uuid, *, status=None):
    if not isinstance(connection, dict) or connection.get("uuid") != uuid or "api_key" in connection:
        raise AssertionError("ModelConnection read-back identity or secret redaction mismatch")
    if connection.get("api_key_configured") is not True or (status is not None and connection.get("status") != status):
        raise AssertionError(
            "ModelConnection read-back configuration or availability mismatch: "
            f"api_key_configured={connection.get('api_key_configured')!r}, "
            f"status={connection.get('status')!r}, expected_status={status!r}"
        )


def create_connection(client, *, name, endpoint, model_name, api_key):
    payload = model_connection_payload(name, endpoint, model_name, api_key)
    created = client.scoped("/model-connections", "POST", body=payload, agent=False)
    uuid = _identifier(created.get("uuid"))
    _assert_connection(created, uuid, status="untested")
    latest = client.scoped("/model-connection/" + uuid, agent=False)
    _assert_connection(latest, uuid, status="untested")
    if any(latest.get(key) != payload[key] for key in ("name", "endpoint", "model_name")):
        raise AssertionError("ModelConnection read-back does not retain the submitted public configuration")
    return latest


def test_connection(client, connection, *, timeout=None):
    uuid = _identifier(connection.get("uuid") if isinstance(connection, dict) else connection)
    options = {} if timeout is None else {"timeout": timeout}
    tested = client.scoped("/model-connection/" + uuid + "/test", "POST", agent=False, **options)
    _assert_connection(tested, uuid, status="available")
    latest = client.scoped("/model-connection/" + uuid, agent=False)
    _assert_connection(latest, uuid, status="available")
    return latest


def create_diagnoser(client, *, name, connection, runtime_type, reasoning_effort=None, max_tokens=None,
                     request_timeout_sec=None, enable_thinking=False):
    if isinstance(connection, dict) and connection.get("status") != "available":
        raise ValueError("ModelConnection must have passed its public connection test")
    connection_uuid = connection.get("uuid") if isinstance(connection, dict) else connection
    payload = diagnoser_payload(name, connection_uuid, runtime_type=runtime_type,
        reasoning_effort=reasoning_effort, max_tokens=max_tokens, request_timeout_sec=request_timeout_sec,
        enable_thinking=enable_thinking)
    created = client.scoped("/diagnosers", "POST", body=payload)
    uuid = _identifier(created.get("uuid"))
    latest = client.scoped("/diagnosers/" + uuid, agent=False)
    spec = latest.get("spec") or {}
    metrics = spec.get("metrics") or []
    if (latest.get("uuid") != uuid or latest.get("type") != "custom" or latest.get("mode") != "llm"
            or latest.get("name") != name or "model" in spec
            or spec.get("model_connection_uuid") != connection_uuid
            or spec.get("runtime_type") != payload["spec"]["runtime_type"]
            or spec.get("prompt") != payload["spec"]["prompt"]
            or len(metrics) != len(METRIC_NAMES) or {item.get("name") for item in metrics} != set(METRIC_NAMES)):
        raise AssertionError("Custom LLM diagnoser read-back differs from the submitted configuration")
    if "enable_thinking" not in spec or any(spec.get(key) != payload["spec"].get(key) for key in
            ("reasoning_effort", "max_tokens", "request_timeout_sec", "enable_thinking")):
        raise AssertionError("Custom LLM diagnoser read-back lost the requested model controls")
    return latest


def _tag(prompt, name):
    match = re.search(r"<" + name + r">(.*?)</" + name + r">", prompt, re.S)
    return match.group(1) if match else None


def _canonical_material(value):
    """The API and prompt resolver use space/T separators for fact timestamps."""
    canonical = []
    for item in value:
        if not isinstance(item, dict):
            canonical.append(item)
            continue
        row = dict(item)
        for field in ("started_at", "ended_at"):
            if isinstance(row.get(field), str):
                try:
                    row[field] = datetime.fromisoformat(row[field]).isoformat()
                except ValueError:
                    pass
        canonical.append(row)
    return json.dumps(canonical, sort_keys=True, ensure_ascii=False)


def _request_has_prompt(row, prompt):
    if not isinstance(row, dict) or row.get("method") != "POST":
        return False
    body = row.get("request")
    messages = body.get("messages") if isinstance(body, dict) else None
    if not isinstance(messages, list):
        return False
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        # LLMJudge's wire contract adds this exact task heading. A connection
        # probe, a system message, or a substring of another prompt is no proof.
        if content == "## 评判任务\n" + prompt:
            return True
        if isinstance(content, list):
            texts = [block.get("text") for block in content if isinstance(block, dict) and block.get("type") == "text"]
            if texts == ["## 评判任务\n" + prompt]:
                return True
    return False


def _metric_facts(metrics, metric_names=METRIC_NAMES):
    """Mirror Custom LLM parse_user_metrics: strip names, preserve reasons/evidence."""
    if not isinstance(metrics, list) or len(metrics) != len(metric_names):
        return None
    facts = []
    for metric in metrics:
        if not isinstance(metric, dict):
            return None
        name, verdict, reason = (metric.get(field) for field in ("name", "verdict", "reason"))
        evidence = metric.get("evidence", {})
        if (not isinstance(name, str) or not name.strip() or not isinstance(verdict, str)
                or verdict not in {"good", "bad"} or not isinstance(reason, str) or not reason.strip()
                or not isinstance(evidence, dict)):
            return None
        facts.append({"name": name.strip(), "verdict": verdict, "reason": reason, "evidence": evidence})
    if {metric["name"] for metric in facts} != set(metric_names):
        return None
    try:
        return json.dumps(sorted(facts, key=lambda metric: metric["name"]),
            sort_keys=True, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, OverflowError):
        return None


def _provider_metric_facts(row, metric_names=METRIC_NAMES):
    """Read actual Chat reply with LLMJudge's text and JSON extraction contract.

    Fake HTTP captures the complete wire envelope; NVIDIA captures its actual
    choices[0].message. Neither a probe nor an unfinished/error reply is a Judge.
    """
    if (row.get("completed") is not True or type(row.get("status")) is not int
            or row["status"] != 200 or row.get("error") or row.get("shutdown_pending")):
        return None
    if "wire_response" in row:
        envelope = row["wire_response"]
        choices = envelope.get("choices") if isinstance(envelope, dict) else None
        message = (choices[0].get("message") if isinstance(choices, list) and choices
            and isinstance(choices[0], dict) else None)
    else:
        message = row.get("response")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str):
                    parts.append(text)
                elif isinstance(text, dict) and isinstance(text.get("value"), str):
                    parts.append(text["value"])
        content = "\n".join(parts) if parts else None
    if not isinstance(content, str):
        return None
    cleaned = re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()
    decoder = json.JSONDecoder()
    for index, char in enumerate(cleaned):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(cleaned[index:])
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(value, dict) and isinstance(value.get("metrics"), list):
            return _metric_facts(value["metrics"], metric_names)
    return None


def _provider_failure(row, metric_names=METRIC_NAMES):
    """Require an observed, completed fault; absent replies are no proof."""
    if (row.get("completed") is not True or row.get("shutdown_pending")
            or type(row.get("status")) is not int):
        return None
    if 400 <= row["status"] <= 599:
        return "http_failure"
    if row["status"] != 200 or row.get("error"):
        return None
    # The actual wire envelope takes precedence over a cached message. A
    # missing envelope/message must not be relabelled as malformed JSON.
    if "wire_response" in row:
        observed = isinstance(row["wire_response"], (dict, list, str))
    else:
        message = row.get("response")
        observed = isinstance(message, dict) and isinstance(message.get("content"), (str, list))
    if observed and _provider_metric_facts(row, metric_names) is None:
        return "malformed"
    return None


def validate_judge_records(records, *, diagnoser_uuid, source_trace_ids, expected,
                           expected_execution="succeeded", source_span_ids=None,
                           expected_materials=None, provider_requests=None, metric_names=METRIC_NAMES,
                           expected_failure=None, expected_http_status=None):
    """Validate the selected Judge records in a mixed list returned by the API.

    Task statistics and all other diagnosers remain the root runner's responsibility.
    Communication failures require failed execution and failed metrics, never bad quality.
    Native E2E supplies independently verified projections and the actual provider
    ledger. Successful generic checks may omit the ledger; failed execution
    always requires an observed fault matching the actual task prompt.
    """
    if isinstance(expected, str):
        expected = dict.fromkeys(metric_names, expected)
    if not metric_names or len(set(metric_names)) != len(metric_names) or not isinstance(expected, dict) or set(expected) != set(metric_names) or any(
            value not in {"good", "bad", "failed"} for value in expected.values()):
        raise ValueError("expected must declare a verdict for exactly the three Judge metrics")
    if expected_execution not in {"succeeded", "failed"}:
        raise ValueError("expected_execution must be succeeded or failed")
    if expected_failure not in {None, "malformed", "http_failure"} or (expected_failure is not None
            and expected_execution != "failed"):
        raise ValueError("expected_failure requires failed execution and a supported actual fault mode")
    if expected_http_status is not None and (type(expected_http_status) is not int
            or not 400 <= expected_http_status <= 599 or expected_failure != "http_failure"):
        raise ValueError("expected_http_status requires an HTTP fault mode and an integer 4xx/5xx status")
    selected = [record for record in records if isinstance(record, dict) and record.get("diagnoser_uuid") == diagnoser_uuid]
    failures, checks = [], {}

    def check(key, valid):
        checks[key] = bool(valid)
        if not valid:
            failures.append(key)

    check("trace_coverage", len(selected) == len(source_trace_ids)
        and {record.get("target_id") for record in selected} == set(source_trace_ids))
    check("record_identity", all(isinstance(record.get("uuid"), str) and record["uuid"] for record in selected)
        and len({record.get("uuid") for record in selected}) == len(selected))
    if expected_materials is not None:
        check("original_material_coverage", isinstance(expected_materials, dict)
            and set(source_trace_ids) <= set(expected_materials))
    if expected_execution == "failed":
        check("failure_provider_ledger", isinstance(provider_requests, list) and bool(provider_requests))
    counts = Counter()
    outcomes = []
    for index, record in enumerate(selected):
        prefix = str(record.get("target_id")) + ":" + str(index)
        metrics = record.get("metrics") or []
        shaped = isinstance(metrics, list) and all(isinstance(metric, dict) for metric in metrics)
        check(prefix + ":metric_names", shaped and len(metrics) == len(metric_names) and {metric.get("name") for metric in metrics} == set(metric_names))
        if not shaped:
            continue
        verdicts = [metric.get("verdict") for metric in metrics]
        derived = "bad" if "bad" in verdicts else "failed" if "failed" in verdicts else "good"
        check(prefix + ":record_verdict", record.get("verdict") == derived)
        execution = record.get("execution") or {}
        status = execution.get("status")
        check(prefix + ":execution", status == expected_execution
            and ((status == "failed" and verdicts and all(value == "failed" for value in verdicts)
                and isinstance(execution.get("code"), str) and bool(execution["code"].strip())
                and isinstance(execution.get("msg"), str) and bool(execution["msg"].strip()))
                or (status == "succeeded" and "failed" not in verdicts and not execution.get("code") and not execution.get("msg"))))
        for metric in metrics:
            name = metric.get("name")
            check(prefix + ":" + str(name) + ":expected", metric.get("verdict") == expected.get(name))
            check(prefix + ":" + str(name) + ":reason", isinstance(metric.get("reason"), str) and bool(metric["reason"].strip()))
            check(prefix + ":" + str(name) + ":evidence", isinstance(metric.get("evidence"), dict))
            counts[(name, metric.get("verdict"))] += 1
        evidence = execution.get("evidence") or {}
        agent_run = evidence.get("agent_run") or {}
        prompt = agent_run.get("rendered_prompt")
        valid_prompt = isinstance(prompt, str) and bool(prompt.strip()) and "{{" not in prompt
        check(prefix + ":rendered_prompt", valid_prompt)
        if valid_prompt:
            fields = {name: _tag(prompt, name) for name in MATERIAL_NAMES}
            check(prefix + ":material_fields", all(value is not None for value in fields.values()))
            keys = _tag(prompt, "metric_keys") or ""
            names = [name.strip() for name in keys.split(",")]
            check(prefix + ":declared_keys", len(names) == len(metric_names) and set(names) == set(metric_names))
            snapshot = record.get("trace_snapshot") or {}
            check(prefix + ":snapshot_matches", all(fields[name] == snapshot.get(name) for name in ("user_query", "agent_output")))
            original = expected_materials.get(record.get("target_id")) if isinstance(expected_materials, dict) else None
            if expected_materials is not None:
                check(prefix + ":original_material_fields", isinstance(original, dict)
                    and all(name in original for name in MATERIAL_NAMES))
                for field in ("user_query", "agent_output"):
                    check(prefix + ":" + field + ":original", isinstance(original, dict)
                        and isinstance(original.get(field), str) and fields[field] == original[field])
            for field in ("tool_actions", "file_mutations", "verification_actions"):
                try:
                    materials = json.loads(fields[field] or "")
                except (json.JSONDecodeError, TypeError):
                    materials = None
                check(prefix + ":" + field + ":json", isinstance(materials, list))
                if source_span_ids is not None and isinstance(materials, list):
                    check(prefix + ":" + field + ":source", all(isinstance(item, dict)
                        and item.get("source_span_id") in source_span_ids for item in materials))
                if expected_materials is not None:
                    check(prefix + ":" + field + ":original", isinstance(materials, list)
                        and isinstance(original, dict) and isinstance(original.get(field), list)
                        and _canonical_material(materials) == _canonical_material(original[field]))
            if provider_requests is not None or status == "failed":
                matching = [row for row in provider_requests if _request_has_prompt(row, prompt)] if isinstance(provider_requests, list) else []
                check(prefix + ":provider_prompt", bool(matching))
                if status == "succeeded":
                    facts = _metric_facts(metrics, metric_names)
                    check(prefix + ":provider_reply", facts is not None
                        and any(_provider_metric_facts(row, metric_names) == facts for row in matching))
                elif status == "failed":
                    faults = [_provider_failure(row, metric_names) for row in matching]
                    allowed = {expected_failure} if expected_failure is not None else {"malformed", "http_failure"}
                    check(prefix + ":provider_failure", bool(faults) and all(fault in allowed for fault in faults))
                    if expected_http_status is not None:
                        check(prefix + ":provider_http_status", bool(matching)
                            and all(row.get("status") == expected_http_status for row in matching))
        outcomes.append({"trace_id": record.get("target_id"), "metrics": metrics, "execution": status})
    return {"verified": not failures, "checks": checks, "failures": failures, "outcomes": outcomes,
        "counts": {name: {**{verdict: counts[(name, verdict)] for verdict in ("good", "bad", "failed")},
            "total": sum(counts[(name, verdict)] for verdict in ("good", "bad", "failed"))} for name in metric_names}}
