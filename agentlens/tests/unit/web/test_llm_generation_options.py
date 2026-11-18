"""Shared validation and public builtin configuration propagation."""

import json
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentlens.domain.enums import DiagnoserMode, DiagnoserType, ModelConnectionStatus
from agentlens.domain.errors import Validation
from agentlens.web.deps import get_diagnoser_table, get_model_connection_table
from agentlens.web.errors import install_error_handlers
from agentlens.web.routes.diagnoser import (
    _managed_spec_signature,
    _sync_builtin,
    _validate_executor_spec,
    router,
)


def _llm_spec(**options):
    return {"executor": "llm_builtin", "prompt": "p", "metrics": [{"name": "quality"}], **options}


@pytest.mark.parametrize(
    "effort", ["none", "minimal", "low", "medium", "high", "xhigh", "max", 1, 50, 100]
)
def test_optional_llm_controls_are_preserved_without_inventing_defaults(effort):
    old = _validate_executor_spec(_llm_spec(), DiagnoserType.BUILTIN, DiagnoserMode.LLM)
    assert "reasoning_effort" not in old
    assert "max_tokens" not in old
    assert "request_timeout_sec" not in old
    spec = _validate_executor_spec(
        _llm_spec(reasoning_effort=effort, max_tokens=1),
        DiagnoserType.BUILTIN,
        DiagnoserMode.LLM,
    )
    assert spec["reasoning_effort"] == effort
    assert type(spec["reasoning_effort"]) is type(effort)
    assert spec["max_tokens"] == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("reasoning_effort", None),
        ("reasoning_effort", True),
        ("reasoning_effort", False),
        ("reasoning_effort", 0),
        ("reasoning_effort", -1),
        ("reasoning_effort", 101),
        ("reasoning_effort", 1.0),
        ("reasoning_effort", "1"),
        ("reasoning_effort", ""),
        ("reasoning_effort", "LOW"),
        ("reasoning_effort", "unknown"),
        ("max_tokens", None),
        ("max_tokens", True),
        ("max_tokens", "1024"),
        ("max_tokens", 0),
        ("max_tokens", -1),
        ("max_tokens", 1.5),
        ("request_timeout_sec", None),
        ("request_timeout_sec", True),
        ("request_timeout_sec", False),
        ("request_timeout_sec", "180"),
        ("request_timeout_sec", 0),
        ("request_timeout_sec", -1),
        ("request_timeout_sec", 300.1),
        ("request_timeout_sec", float("nan")),
        ("request_timeout_sec", float("inf")),
        ("request_timeout_sec", float("-inf")),
    ],
)
def test_invalid_llm_controls_are_rejected_by_the_public_validator(field, value):
    with pytest.raises(Validation, match=field):
        _validate_executor_spec(
            _llm_spec(**{field: value}), DiagnoserType.BUILTIN, DiagnoserMode.LLM
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("reasoning_effort", "low"),
        ("reasoning_effort", 1),
        ("max_tokens", 1024),
        ("request_timeout_sec", 180),
    ],
)
def test_rule_diagnosers_reject_llm_controls(field, value):
    with pytest.raises(Validation, match="仅适用于 LLM"):
        _validate_executor_spec(
            {
                "executor": "total_latency",
                "metrics": [{"name": "latency_decomposition"}],
                field: value,
            },
            DiagnoserType.BUILTIN,
            DiagnoserMode.RULE,
        )


class _Table:
    def __init__(self):
        self.rows = {}

    def insert(self, row):
        self.rows[row.uuid] = row

    def get_by_uuid(self, uuid):
        return self.rows.get(uuid)

    def get_by_uuid_for_update(self, uuid):
        return self.get_by_uuid(uuid)

    def list_exact_scope(self, space_uuid, agent_uuid):
        return [
            row
            for row in self.rows.values()
            if (row.space_uuid, row.agent_uuid) == (space_uuid, agent_uuid)
        ]

    def update_by_uuid(self, uuid, fields):
        if "type" in fields:
            fields = {**fields, "type": DiagnoserType(fields["type"])}
        if "mode" in fields:
            fields = {**fields, "mode": DiagnoserMode(fields["mode"])}
        self.rows[uuid] = replace(self.rows[uuid], **fields)

    def list(self, **_):
        return list(self.rows.values())


@pytest.mark.parametrize("created_effort,patched_effort", [("low", "high"), (1, 100)])
@pytest.mark.parametrize("thinking", [None, False, True])
def test_builtin_create_patch_and_resync_preserve_request_controls(
    created_effort, patched_effort, thinking
):
    table = _Table()
    app = FastAPI()
    app.include_router(router)
    install_error_handlers(app)
    app.dependency_overrides[get_diagnoser_table] = lambda: table
    app.dependency_overrides[get_model_connection_table] = lambda: SimpleNamespace()
    client = TestClient(app)
    created = client.post(
        "/api/v1/diagnoser",
        json={
            "space_uuid": "s",
            "agent_uuid": "a",
            "name": "文本回应检查",
            "type": "builtin",
            "mode": "llm",
            "spec": {
                "enable_thinking": thinking,
                "reasoning_effort": created_effort,
                "max_tokens": 1024,
                "request_timeout_sec": 180,
            },
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["spec"]["reasoning_effort"] == created_effort
    assert type(body["spec"]["reasoning_effort"]) is type(created_effort)
    assert body["spec"]["max_tokens"] == 1024
    assert body["spec"]["request_timeout_sec"] == 180
    assert body["spec"]["enable_thinking"] is thinking

    patched = client.patch(
        f"/api/v1/diagnoser/{body['uuid']}",
        json={
            "spec": {
                "enable_thinking": thinking,
                "reasoning_effort": patched_effort,
                "max_tokens": 2048,
                "request_timeout_sec": 300,
            }
        },
    )
    assert patched.status_code == 200, patched.text
    spec = patched.json()["spec"]
    assert spec["reasoning_effort"] == patched_effort
    assert type(spec["reasoning_effort"]) is type(patched_effort)
    assert spec["max_tokens"] == 2048
    assert spec["request_timeout_sec"] == 300
    assert spec["enable_thinking"] is thinking
    assert spec["prompt"] == body["spec"]["prompt"]

    from agentlens.web.routes.diagnoser import _find_builtin_definition

    # Force a definition refresh so preservation is verified on a write path.
    definition = {**_find_builtin_definition("文本回应检查"), "description": "refreshed definition"}
    synced, action = _sync_builtin(table, "s", "a", definition)
    assert action == "updated"
    assert synced.spec["reasoning_effort"] == patched_effort
    assert type(synced.spec["reasoning_effort"]) is type(patched_effort)
    assert synced.spec["max_tokens"] == 2048
    assert synced.spec["request_timeout_sec"] == 300
    assert synced.spec["enable_thinking"] is thinking

    for options in (
        {"reasoning_effort": True},
        {"reasoning_effort": 1.0},
        {"reasoning_effort": 0},
        {"reasoning_effort": 101},
        {"max_tokens": 0},
        {"request_timeout_sec": True},
        {"request_timeout_sec": 0},
        {"request_timeout_sec": 300.1},
    ):
        failed = client.patch(f"/api/v1/diagnoser/{body['uuid']}", json={"spec": options})
        assert failed.status_code == 422, failed.text
        assert table.get_by_uuid(body["uuid"]).spec["max_tokens"] == 2048
        assert table.get_by_uuid(body["uuid"]).spec["reasoning_effort"] == patched_effort
        assert table.get_by_uuid(body["uuid"]).spec["request_timeout_sec"] == 300

    # Explicitly resyncing a live Core row with no saved control adopts the new
    # canonical default; immutable old Task snapshots are not resynced here.
    current = table.get_by_uuid(body["uuid"])
    table.rows[current.uuid] = replace(
        current,
        spec={key: value for key, value in current.spec.items() if key != "enable_thinking"},
    )
    legacy, _ = _sync_builtin(table, "s", "a", definition)
    assert legacy.spec["enable_thinking"] is None


def test_generation_controls_participate_in_managed_sync_signature():
    spec = _llm_spec()
    assert _managed_spec_signature(spec) != _managed_spec_signature(
        {**spec, "reasoning_effort": "low"}
    )
    assert _managed_spec_signature(spec) != _managed_spec_signature({**spec, "max_tokens": 1024})
    assert _managed_spec_signature(spec) != _managed_spec_signature(
        {**spec, "request_timeout_sec": 180}
    )


@pytest.mark.parametrize("effort", ["low", 1, 100])
@pytest.mark.parametrize(
    "thinking_options",
    [{}, {"enable_thinking": None}, {"enable_thinking": False}, {"enable_thinking": True}],
    ids=["default", "null", "false", "true"],
)
def test_trial_and_formal_execution_forward_the_same_bounded_judge_request(
    monkeypatch, effort, thinking_options
):
    from agentlens.diagnoser.executors.llm import base
    from agentlens.diagnoser.executors.llm.custom.executor import LlmCustomExecutor
    from agentlens.diagnoser.executors.rule.custom.trial import project_trial_input
    from agentlens.infra.llm_judge import LLMJudge

    requests = []
    timeouts = []

    def transport(request):
        requests.append(json.loads(request.content))
        timeouts.append(request.extensions["timeout"])
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "metrics": [
                                        {
                                            "name": "quality",
                                            "verdict": "good",
                                            "reason": "actual test result",
                                            "evidence": {},
                                        }
                                    ]
                                }
                            )
                        }
                    }
                ]
            },
        )

    def build_judge(*args, **kwargs):
        return LLMJudge(*args, **kwargs, max_retries=0, transport=httpx.MockTransport(transport))

    # Replace only HTTP transport; keep validation, projection, prompt, Judge,
    # protocol parsing and Trial/formal executors real.
    monkeypatch.setattr(base, "LLMJudge", build_judge)
    connection = SimpleNamespace(
        endpoint="http://judge.test/v1",
        api_key="test-key",
        model_name="test-model",
        status=ModelConnectionStatus.AVAILABLE,
    )
    app = FastAPI()
    app.include_router(router)
    install_error_handlers(app)
    app.dependency_overrides[get_model_connection_table] = lambda: SimpleNamespace(
        get_in_space=lambda *_: connection
    )
    table = _Table()
    app.dependency_overrides[get_diagnoser_table] = lambda: table
    spec = {
        "runtime_type": "other",
        "model_connection_uuid": "model-1",
        "prompt": "Inspect the original input",
        "metrics": [{"name": "quality"}],
        "reasoning_effort": effort,
        "max_tokens": 1024,
        "request_timeout_sec": 180,
        **thinking_options,
    }
    client = TestClient(app)
    created = client.post(
        "/api/v1/diagnoser",
        json={
            "space_uuid": "s",
            "agent_uuid": "a",
            "name": "transport controls",
            "type": "custom",
            "mode": "llm",
            "spec": spec,
        },
    )
    assert created.status_code == 201, created.text
    uuid = created.json()["uuid"]
    patched = client.patch(f"/api/v1/diagnoser/{uuid}", json={"spec": spec})
    assert patched.status_code == 200, patched.text
    readback = client.get(f"/api/v1/diagnoser/{uuid}")
    assert readback.status_code == 200, readback.text
    assert readback.json()["spec"]["enable_thinking"] is thinking_options.get(
        "enable_thinking", False
    )
    test_input = {"trace_id": "trace-1", "runtime_type": "other", "spans": []}
    response = client.post(
        "/api/v1/diagnoser/llm-trial",
        json={"space_uuid": "s", "spec": spec, "test_input": test_input},
    )
    assert response.status_code == 200, response.text
    assert response.json()["execution"]["status"] == "succeeded"

    formal_spec = readback.json()["spec"]
    formal_spec["model"] = {
        "endpoint": connection.endpoint,
        "api_key": connection.api_key,
        "model_id": connection.model_name,
    }
    executor = LlmCustomExecutor(formal_spec)
    try:
        result = executor.evaluate(project_trial_input(test_input))
        assert result.execution.status.value == "succeeded"
    finally:
        executor.close()

    assert len(requests) == 2
    assert requests[0] == requests[1]
    assert requests[0]["reasoning_effort"] == effort
    assert type(requests[0]["reasoning_effort"]) is type(effort)
    assert requests[0]["max_tokens"] == 1024
    assert "request_timeout_sec" not in requests[0]
    assert timeouts == [dict.fromkeys(("connect", "read", "write", "pool"), 180)] * 2
    thinking = thinking_options.get("enable_thinking", False)
    if thinking is None:
        assert "enable_thinking" not in requests[0]
        assert "chat_template_kwargs" not in requests[0]
    else:
        assert requests[0]["enable_thinking"] is thinking
        assert requests[0]["chat_template_kwargs"] == {"enable_thinking": thinking}
    assert "<metric_keys>quality</metric_keys>" in requests[0]["messages"][1]["content"]
