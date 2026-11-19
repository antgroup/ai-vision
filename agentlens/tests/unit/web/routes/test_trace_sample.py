"""Tests for the unified custom-diagnoser Trace sample endpoint."""

from datetime import datetime, timedelta

import pytest

from agentlens.domain.dataset import TraceQuerySpec
from agentlens.service.datasource.projection.models import TraceOutcome
from agentlens.service.datasource.projection.projectors.coding.models import CodingProjection
from agentlens.service.datasource.trace import Trace
from agentlens.web.routes.diagnoser import (
    _sample_trace_query_spec,
)


def _request(runtime_type="antigravity"):
    return {
        "dataset_type": "signoz",
        "spec": {
            "runtime_type": runtime_type,
            "username_column": "attributes.ant.username",
            "username": "w123",
            "agent_product_column": "resource.attributes.ant.agent.product",
            "agent_product_filter": "Antigravity",
            "query": {"table": "sample_rows", "time_range": "1h"},
        },
    }


def test_sample_returns_scoped_source_spans_for_unknown_runtime(client, signoz_inmem):
    signoz_inmem.seed(
        "sample_rows",
        [
            {
                "trace_id": "trace-other-user",
                "span_id": "span-other-user",
                "period": "2026-07-24 10:00:00",
                "attributes.ant.username": "someone-else",
                "resource.attributes.ant.agent.product": "Antigravity",
            },
            {
                "trace_id": "trace-current-user",
                "span_id": "span-current-user",
                "period": "2026-07-24 10:01:00",
                "attributes.ant.username": "w123",
                "resource.attributes.ant.agent.product": "Antigravity",
                "source_only": {"preserve": True},
            },
        ],
    )

    response = client.post("/api/v1/diagnoser/trace-sample", json=_request())

    assert response.status_code == 200
    sample = response.json()["test_input"]
    assert sample["runtime_type"] == "antigravity"
    assert sample["spans"] == response.json()["samples"][0]["test_input"]["spans"]
    assert sample["spans"][0]["span_id"] == "span-current-user"
    assert sample["spans"][0]["source_only"] == {"preserve": True}
    assert "trace-other-user" not in str(sample)


def test_sample_backfills_three_recent_traces(client, monkeypatch):
    batches = [
        [[{"trace_id": "trace-1", "span_id": "span-1", "name": "first"}]],
        [
            [{"trace_id": "trace-2", "span_id": "span-2", "name": "second"}],
            [{"trace_id": "trace-3", "span_id": "span-3", "name": "third"}],
        ],
    ]

    where = "`name` = 'first' OR `name` = 'second'"

    def read_recent(_self, spec, *, trace_spec=None, count=3, window_min=10):
        assert count == 12
        assert window_min == 10
        assert spec.where.startswith(f"({where}) AND ")

        return batches.pop(0)

    monkeypatch.setattr(
        "agentlens.web.routes.diagnoser._sample_trace_query_specs", lambda spec: [spec, spec]
    )
    monkeypatch.setattr(
        "agentlens.service.datasource.signoz.TraceStore.read_recent_trace_rows", read_recent
    )
    payload = _request()
    payload["spec"]["query"]["where"] = where
    payload["recent_limit"] = 3

    response = client.post("/api/v1/diagnoser/trace-sample", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert [sample["trace_id"] for sample in body["samples"]] == ["trace-1", "trace-2", "trace-3"]
    assert body["test_input"] == body["samples"][0]["test_input"]


def test_sample_loads_an_explicit_trace_id_with_the_same_custom_input_contract(
    client,
    monkeypatch,
):
    rows = [{"trace_id": "trace-entered", "span_id": "span-1", "name": "selected"}]

    def read_by_id(_self, spec, trace_id):
        assert trace_id == "trace-entered"
        assert spec.where == (
            "`attributes.ant.username` = 'w123' AND "
            "`resource.attributes.ant.agent.product` = 'Antigravity'"
        )
        assert spec.time_range == "7d"
        return rows

    monkeypatch.setattr(
        "agentlens.service.datasource.signoz.TraceStore.read_trace_rows_by_id",
        read_by_id,
    )
    payload = _request()
    payload["trace_id"] = "trace-entered"

    response = client.post("/api/v1/diagnoser/trace-sample", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert [sample["trace_id"] for sample in body["samples"]] == ["trace-entered"]
    assert body["samples"][0]["test_input"]["spans"] == rows


def test_sample_returns_empty_spans_when_no_trace_exists(client):
    response = client.post("/api/v1/diagnoser/trace-sample", json=_request())

    assert response.status_code == 200
    assert response.json()["test_input"] == {"runtime_type": "antigravity", "spans": []}


def test_sample_fallback_does_not_invent_dedicated_semantics(client):
    response = client.post("/api/v1/diagnoser/trace-sample", json=_request("custom-runtime"))

    assert response.status_code == 200
    assert response.json()["test_input"] == {"runtime_type": "custom-runtime", "spans": []}


def test_sample_keeps_semantics_and_spans_for_dedicated_runtime(client, signoz_inmem, monkeypatch):
    signoz_inmem.seed(
        "sample_rows",
        [
            {
                "trace_id": "trace-coding",
                "span_id": "span-coding",
                "period": "2026-07-24 10:01:00",
                "attributes.ant.username": "w123",
                "resource.attributes.ant.agent.product": "Antigravity",
                "source_only": {"preserve": True},
            }
        ],
    )
    projection = CodingProjection(
        timestamp=None,
        user_query="real question",
        agent_output="real answer",
        outcome=TraceOutcome(False, "SUCCESS", 1, False),
        labels={},
    )
    monkeypatch.setattr(
        "agentlens.web.routes.diagnoser.project_trace",
        lambda trace: Trace(
            trace_id=trace.trace_id,
            runtime_type=trace.runtime_type,
            spans=trace.spans,
            source_type=trace.source_type,
            projection=projection,
        ),
    )

    response = client.post("/api/v1/diagnoser/trace-sample", json=_request("codex"))

    assert response.status_code == 200
    sample = response.json()["test_input"]
    assert sample["user_query"] == "real question"
    assert sample["spans"][0]["source_only"] == {"preserve": True}


def test_rule_trial_receives_source_spans(client):
    response = client.post(
        "/api/v1/diagnoser/rule-trial",
        json={
            "spec": {
                "metrics": [{"name": "rule_score"}],
                "code": (
                    "def evaluate(trace):\n"
                    "    return {'metrics': [{"
                    "'name': 'rule_score', "
                    "'verdict': 'good', "
                    "'reason': trace['spans'][0]['name'], "
                    "'evidence': {}}]}\n"
                ),
            },
            "test_input": {
                "runtime_type": "other",
                "spans": [{"name": "raw span", "trace_id": "trial"}],
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["metrics"][0]["reason"] == "raw span"


def test_llm_trial_renders_a_spans_expression_without_persisting(client, monkeypatch):
    from types import SimpleNamespace

    from agentlens.domain.enums import ModelConnectionStatus

    class FakeJudge:
        def judge(self, prompt):
            assert "sample node" in prompt
            assert "<metric_keys>quality, safety</metric_keys>" in prompt
            return {
                "metrics": [
                    {
                        "name": "quality",
                        "verdict": "good",
                        "reason": "evidence is sufficient",
                        "evidence": {},
                    },
                    {
                        "name": "safety",
                        "verdict": "bad",
                        "reason": "safety issue",
                        "evidence": {},
                    },
                ]
            }

        def close(self):
            pass

    constructor = {}

    def fake_judge(*_args, **kwargs):
        constructor.update(kwargs)
        return FakeJudge()

    monkeypatch.setattr("agentlens.diagnoser.executors.llm.base.LLMJudge", fake_judge)
    monkeypatch.setattr(
        "agentlens.infra.ob.tables.model_connection_table.ModelConnectionTable.get_in_space",
        lambda _self, _uuid, _space: SimpleNamespace(
            endpoint="https://example.test/v1",
            api_key="test-key",
            model_name="judge",
            status=ModelConnectionStatus.AVAILABLE,
        ),
    )
    response = client.post(
        "/api/v1/diagnoser/llm-trial",
        json={
            "space_uuid": "s",
            "spec": {
                "prompt": "Check this: {{spans[0].name}}",
                "metrics": [
                    {"name": "quality"},
                    {"name": "safety"},
                ],
                "model_connection_uuid": "m-test",
                "enable_thinking": True,
            },
            "test_input": {"runtime_type": "other", "spans": [{"name": "sample node"}]},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert [item["name"] for item in body["metrics"]] == ["quality", "safety"]
    assert body["metrics"][0]["verdict"] == "good"
    assert body["metrics"][1]["verdict"] == "bad"
    assert body["verdict"] == "bad"
    assert body["execution"]["status"] == "succeeded"
    assert constructor["enable_thinking"] is True


def test_llm_trial_reads_flat_error_field_with_prefix_conflict(client, monkeypatch):
    from types import SimpleNamespace

    from agentlens.domain.enums import ModelConnectionStatus

    class FakeJudge:
        def judge(self, prompt):
            assert "Errors: 10007" in prompt
            return {
                "metrics": [
                    {
                        "name": "quality",
                        "verdict": "bad",
                        "reason": "error found",
                        "evidence": {},
                    }
                ]
            }

        def close(self):
            pass

    monkeypatch.setattr(
        "agentlens.diagnoser.executors.llm.base.LLMJudge", lambda *_a, **_kw: FakeJudge()
    )
    monkeypatch.setattr(
        "agentlens.infra.ob.tables.model_connection_table.ModelConnectionTable.get_in_space",
        lambda _self, _uuid, _space: SimpleNamespace(
            endpoint="https://example.test/v1",
            api_key="test-key",
            model_name="judge",
            status=ModelConnectionStatus.AVAILABLE,
        ),
    )
    response = client.post(
        "/api/v1/diagnoser/llm-trial",
        json={
            "space_uuid": "s",
            "spec": {
                "prompt": "Errors: {{spans[*].attributes.error.type}}",
                "metrics": [{"name": "quality"}],
                "model_connection_uuid": "m-test",
            },
            "test_input": {
                "runtime_type": "other",
                "spans": [
                    {
                        "attributes.mmflow.dag.node": None,
                        "attributes.mmflow.dag.node.class": None,
                        "attributes.error.type": None,
                    },
                    {
                        "attributes.mmflow.dag.node.class": "ImageNode",
                        "attributes.mmflow.dag.node": "generate",
                        "attributes.error.type": "10007",
                    },
                ],
            },
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["verdict"] == "bad"
    assert body["metrics"][0]["verdict"] == "bad"


def test_llm_trial_uses_declared_single_metric_name(client, monkeypatch):
    from types import SimpleNamespace

    from agentlens.domain.enums import ModelConnectionStatus

    class FakeJudge:
        def judge(self, _prompt):
            return {
                "metrics": [
                    {
                        "name": "quality",
                        "verdict": "good",
                        "reason": "相关",
                        "evidence": {},
                    }
                ]
            }

        def close(self):
            pass

    constructor = {}

    def fake_judge(*_args, **kwargs):
        constructor.update(kwargs)
        return FakeJudge()

    monkeypatch.setattr("agentlens.diagnoser.executors.llm.base.LLMJudge", fake_judge)
    monkeypatch.setattr(
        "agentlens.infra.ob.tables.model_connection_table.ModelConnectionTable.get_in_space",
        lambda _self, _uuid, _space: SimpleNamespace(
            endpoint="https://example.test/v1",
            api_key="test-key",
            model_name="judge",
            status=ModelConnectionStatus.AVAILABLE,
        ),
    )
    response = client.post(
        "/api/v1/diagnoser/llm-trial",
        json={
            "space_uuid": "s",
            "name": "test",
            "spec": {
                "prompt": "Check {{spans[0].name}}",
                "metrics": [{"name": "quality"}],
                "model_connection_uuid": "m-test",
            },
            "test_input": {
                "runtime_type": "other",
                "spans": [{"name": "sample node"}],
            },
        },
    )

    assert response.status_code == 200
    assert constructor["enable_thinking"] is False
    body = response.json()
    assert body["metrics"][0]["name"] == "quality"
    assert body["metrics"][0]["verdict"] == "good"


def test_llm_trial_rejects_retired_image_mapping_before_model_or_judge(monkeypatch):
    from agentlens.domain.errors import Validation
    from agentlens.web.routes.diagnoser import llm_trial
    from agentlens.web.schemas.diagnoser import LlmTrialRequest

    class NoModelLookup:
        def get_in_space(self, *_args):
            raise AssertionError("unsupported image input reached model lookup")

    def no_judge(*_args, **_kwargs):
        raise AssertionError("unsupported image input reached Judge")

    monkeypatch.setattr("agentlens.diagnoser.executors.llm.base.LLMJudge", no_judge)
    payload = LlmTrialRequest(
        space_uuid="s",
        spec={
            "prompt": "{{spans[0].name}}",
            "image_vars": ["user_images"],
            "metrics": [{"name": "quality"}],
            "model_connection_uuid": "m-test",
        },
        test_input={
            "runtime_type": "legacy",
            "spans": [
                {
                    "name": "raw image request",
                    "attributes": {"image_url": "https://example.invalid/image.png"},
                }
            ],
        },
    )
    with pytest.raises(Validation, match="不支持图片变量") as raised:
        llm_trial(payload, model_connections=NoModelLookup())
    assert "image.png" not in str(raised.value)


def test_trace_sample_uses_recent_stable_window_not_job_window():
    now = datetime(2026, 7, 27, 12, 0, 0)
    base = TraceQuerySpec(table="sample_rows", time_range="2m")
    sample = _sample_trace_query_spec(base, now=now)

    assert sample.start_time == "2026-07-27 10:55:00"
    assert sample.end_time == "2026-07-27 11:55:00"
    assert datetime.strptime(sample.end_time, "%Y-%m-%d %H:%M:%S") == now - timedelta(minutes=5)
