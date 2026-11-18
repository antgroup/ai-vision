"""Tests for POST /api/v1/diagnoser/rule-trial."""

VALID_CODE = """
def evaluate(trace):
    output = trace.get("agent_output", "")
    if not output:
        return {"metrics": [
            {"name": "test_metric", "verdict": "bad", "reason": "no output", "evidence": {}}
        ]}
    return {"metrics": [
        {"name": "test_metric", "verdict": "good", "reason": "has output", "evidence": {}}
    ]}
"""


ERROR_CODE = """
def evaluate(trace):
    return 1 / 0
"""

INVALID_AST_CODE = """
import os
def evaluate(trace):
    return {"metrics": []}
"""


def _trial_request(code: str, test_input: dict | None = None):
    return {
        "spec": {
            "code": code,
            "metrics": [{"name": "test_metric", "desc": "Test"}],
        },
        "test_input": test_input
        or {
            "runtime_type": "codex",
            "trace_id": "t-1",
            "spans": [
                {
                    "trace_id": "t-1",
                    "span_id": "chat-1",
                    "parent_span_id": "",
                    "name": "Turn 1",
                    "kind": "SPAN_KIND_SERVER",
                    "status_code": 1,
                    "attributes.gen_ai.span.kind": "CHAT",
                    "attributes.gen_ai.input.messages": ('[{"role":"user","content":"test"}]'),
                    "attributes.gen_ai.output.messages": (
                        '[{"role":"assistant","content":"hello world"}]'
                    ),
                }
            ],
        },
    }


def test_successful_trial(client):
    resp = client.post("/api/v1/diagnoser/rule-trial", json=_trial_request(VALID_CODE))
    assert resp.status_code == 200
    body = resp.json()
    assert body["verdict"] == "good"
    assert body["summary"] == "has output"
    assert len(body["metrics"]) == 1
    assert body["metrics"][0]["name"] == "test_metric"
    assert body["metrics"][0]["verdict"] == "good"
    assert body["execution"]["status"] == "succeeded"


def test_runtime_error_trial(client):
    resp = client.post("/api/v1/diagnoser/rule-trial", json=_trial_request(ERROR_CODE))
    body = resp.json()
    assert body["verdict"] == "failed"
    assert body["execution"]["status"] == "failed"
    assert body["execution"]["code"] == "rule_runtime_error"


def test_input_preview_does_not_execute_user_code(monkeypatch):
    from agentlens.diagnoser.executors.rule.custom.executor import RuleCustomExecutor

    monkeypatch.setattr(
        RuleCustomExecutor,
        "evaluate",
        lambda *_: (_ for _ in ()).throw(AssertionError("preview executed code")),
    )
    from agentlens.web.routes.diagnoser import rule_input_preview
    from agentlens.web.schemas.diagnoser import RuleTrialRequest

    response = rule_input_preview(RuleTrialRequest(**_trial_request(ERROR_CODE)))
    assert response.model_dump() == {
        "valid": True,
        "mode": "local",
        "request_bytes": None,
        "worker_input_bytes": None,
        "max_input_bytes": None,
        "transport": None,
        "code": "",
        "details": {},
        "validation_message": "",
        "validation_line": None,
    }


def test_invalid_ast_rejected(client):
    resp = client.post("/api/v1/diagnoser/rule-trial", json=_trial_request(INVALID_AST_CODE))
    body = resp.json()
    assert body["verdict"] == "failed"
    assert body["execution"]["status"] == "failed"
    assert body["execution"]["msg"]


def test_missing_code_rejected(client):
    resp = client.post(
        "/api/v1/diagnoser/rule-trial",
        json={
            "spec": {"metrics": [{"name": "m"}]},
            "test_input": {"trace_id": "t-1"},
        },
    )
    body = resp.json()
    assert body["verdict"] == "failed"
    assert "code" in body["execution"]["msg"]


def test_missing_metrics_rejected(client):
    resp = client.post(
        "/api/v1/diagnoser/rule-trial",
        json={
            "spec": {"code": VALID_CODE},
            "test_input": {"trace_id": "t-1", "agent_output": "hello"},
        },
    )
    assert resp.status_code == 422
    assert "metrics" in resp.json()["msg"]


def test_bad_rule_verdict_is_returned_directly(client):
    test_input = {
        "runtime_type": "custom-runtime",
        "trace_id": "t-1",
        "agent_output": "",
    }
    req = _trial_request(VALID_CODE, test_input)
    resp = client.post("/api/v1/diagnoser/rule-trial", json=req)
    body = resp.json()
    assert body["verdict"] == "bad"
    assert body["metrics"][0]["verdict"] == "bad"
    assert body["metrics"][0]["reason"] == "no output"


def test_rule_trial_uses_shared_applicability_gate(monkeypatch):
    from agentlens.diagnoser.executors.rule.custom.executor import RuleCustomExecutor
    from agentlens.web.routes.diagnoser import rule_trial
    from agentlens.web.schemas.diagnoser import RuleTrialRequest

    monkeypatch.setattr(
        RuleCustomExecutor,
        "applicability_skip_reason",
        lambda self, trace: "missing_required_evidence: output",
        raising=False,
    )
    monkeypatch.setattr(
        RuleCustomExecutor,
        "evaluate",
        lambda *args: (_ for _ in ()).throw(AssertionError("must not execute skipped rule")),
    )
    response = rule_trial(RuleTrialRequest(**_trial_request(VALID_CODE)))
    assert response.skipped is True
    assert response.skip_reason == "missing_required_evidence"
    assert response.skip_reason_detail == "output"
    assert response.verdict is None
    assert response.metrics == []
    assert response.execution.status.value == "succeeded"


def test_malformed_trial_input_remains_failed():
    from agentlens.web.routes.diagnoser import rule_trial
    from agentlens.web.schemas.diagnoser import RuleTrialRequest

    response = rule_trial(RuleTrialRequest(**_trial_request(VALID_CODE, {"spans": ["invalid"]})))
    assert response.verdict.value == "failed"
    assert response.skipped is False


def test_llm_trial_missing_material_skips_even_without_model_request():
    from types import SimpleNamespace

    from agentlens.web.routes.diagnoser import llm_trial
    from agentlens.web.schemas.diagnoser import LlmTrialRequest

    payload = LlmTrialRequest(
        space_uuid="s",
        spec={
            "prompt": "{{spans}}",
            "required_vars": ["spans"],
            "metrics": [{"name": "quality"}],
            "model_connection_uuid": "missing",
        },
        test_input={"runtime_type": "custom", "spans": []},
    )
    response = llm_trial(payload, SimpleNamespace(get_in_space=lambda *args: None))
    assert response.skipped is True
    assert response.skip_reason == "missing_required_evidence"
    assert response.metrics == []


def test_llm_trial_malformed_input_is_failed_response():
    from types import SimpleNamespace

    from agentlens.web.routes.diagnoser import llm_trial
    from agentlens.web.schemas.diagnoser import LlmTrialRequest

    response = llm_trial(
        LlmTrialRequest(
            space_uuid="s",
            spec={"prompt": "{{spans}}", "metrics": [{"name": "quality"}]},
            test_input={"spans": "malformed"},
        ),
        SimpleNamespace(get_in_space=lambda *args: None),
    )
    assert response.verdict.value == "failed"
    assert response.skipped is False


def test_input_preview_keeps_actionable_syntax_error_without_source_snippet():
    from agentlens.web.routes.diagnoser import rule_input_preview
    from agentlens.web.schemas.diagnoser import RuleTrialRequest

    response = rule_input_preview(
        RuleTrialRequest(
            **_trial_request(
                'def evaluate(trace)\n    return {"private": "do-not-echo-this-input"}'
            )
        )
    )
    assert response.valid is False
    assert response.code == "rule_validation_error"
    assert response.validation_line == 1
    assert response.validation_message == "expected ':'"
    assert "do-not-echo-this-input" not in response.model_dump_json()
