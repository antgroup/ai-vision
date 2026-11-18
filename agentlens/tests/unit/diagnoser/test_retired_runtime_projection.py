"""Retired runtime routing preserves raw CUSTOM and saved history facts."""

import json
from copy import deepcopy

import pytest

from agentlens.diagnoser.executors.llm.base import _resolve_llm_inputs
from agentlens.diagnoser.executors.llm.custom.executor import LlmCustomExecutor
from agentlens.diagnoser.executors.rule.custom.executor import RuleCustomExecutor
from agentlens.domain.enums import Verdict
from agentlens.service.datasource.projection import project_trace
from agentlens.service.datasource.projection.serialization import serialize_projection
from agentlens.service.datasource.record_snapshot import build_record_snapshot
from agentlens.service.datasource.trace import Trace
from agentlens.service.datasource.variables import available_vars_for
from agentlens.service.diagnosis_record_store import _record_from_row
from tests.unit.diagnoser.test_shared_evidence_projection import row


@pytest.mark.parametrize("runtime", ["legacy", "legacy001", "legacyv2-2", "legacyagent", "unknown"])
def test_retired_and_unknown_runtime_keep_raw_custom_inputs(runtime):
    spans = [
        row(
            "root",
            "CHAT",
            **{
                "gen_ai.input.messages": [{"role": "user", "content": "q"}],
                "gen_ai.output.messages": [{"role": "assistant", "content": "a"}],
            },
        )
    ]
    original = deepcopy(spans)
    trace = project_trace(Trace("t", runtime, spans))
    assert trace.projection is None
    assert trace.runtime_type == runtime
    assert trace.spans == original
    assert "user_query" not in available_vars_for(runtime)
    assert _resolve_llm_inputs(trace, {"runtime_type", "spans[0].span_id", "history"}) == {
        "runtime_type": runtime,
        "spans[0].span_id": "root",
    }
    snapshot = build_record_snapshot(trace)
    assert snapshot["runtime_type"] == runtime
    assert snapshot["spans"] == original
    assert "context" not in snapshot

    rule = RuleCustomExecutor(
        {
            "code": (
                "def evaluate(trace):\n"
                "    good = trace['spans'][0]['span_id'] == 'root'\n"
                "    return {'metrics': [{'name': 'raw', "
                "'verdict': 'good' if good else 'bad'}]}"
            ),
            "metrics": [{"name": "raw"}],
        }
    )
    assert rule.evaluate(trace).verdict is Verdict.GOOD

    class Judge:
        def judge(self, prompt):
            assert prompt == f"{runtime}:root\n\n<metric_keys>raw</metric_keys>"
            return {"metrics": [{"name": "raw", "verdict": "good", "reason": "raw preserved"}]}

    llm = LlmCustomExecutor(
        {"prompt": "{{runtime_type}}:{{spans[0].span_id}}", "metrics": [{"name": "raw"}]}
    )
    llm._judge = Judge()
    assert llm.evaluate(trace).verdict is Verdict.GOOD


def test_coding_history_is_sourced_and_not_truncated_by_shared_llm_base():
    messages = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"message-{i}"}
        for i in range(24)
    ]
    trace = project_trace(
        Trace(
            "t",
            "codex",
            [
                row(
                    "root",
                    "CHAT",
                    **{
                        "gen_ai.input.messages": messages
                        + [{"role": "user", "content": "current"}],
                        "gen_ai.output.messages": [{"role": "assistant", "content": "final"}],
                    },
                )
            ],
        )
    )
    history = _resolve_llm_inputs(trace, {"history"})["history"]
    assert [item["content"] for item in history] == [f"message-{i}" for i in range(24)]
    assert history[0]["sources"][0]["field_path"].endswith("[0].content")
    assert trace.require_projection().current_request.content == "current"


def test_saved_legacy_json_reads_history_retrieval_graph_and_context():
    saved = {
        "runtime_type": "legacy",
        "history": {
            "turns": [{"turn_index": 1, "user_query": "old", "assistant_answer": "answer"}]
        },
        "retrieval": {"items": [{"content": "old source"}]},
        "graph": {"trajectory": {"graph_steps": [{"span_id": "old-span"}]}},
    }
    context = {"query": "old", "answer": "answer", "traceid": "old-trace"}
    body = {
        "uuid": "r-old",
        "task_uuid": "t-old",
        "job_uuid": "j-old",
        "diagnoser_uuid": "d-old",
        "diagnoser_version": "legacy",
        "target_type": "trace",
        "target_id": "old-trace",
        "trace_snapshot": saved,
        "context": context,
        "metrics": [{"name": "old-metric", "verdict": "good", "reason": "saved", "evidence": {}}],
        "execution": {"status": "succeeded", "code": "", "msg": "", "evidence": {}},
        "review_status": "pending",
        "diagnosed_at": "2026-10-01T12:00:00",
    }
    record = _record_from_row({"attributes.record.body": json.dumps(body)})
    assert record.trace_snapshot == {**saved, "context": context}
    assert record.metrics[0].name == "old-metric"
    assert record.diagnoser_version == "legacy"


@pytest.mark.parametrize(
    ("usage", "expected"),
    [
        ({}, None),
        (
            {"gen_ai.usage.input_tokens": 0, "gen_ai.usage.output_tokens": 0},
            {"input": 0.0, "output": 0.0, "total": 0.0},
        ),
    ],
)
def test_coding_usage_preserves_observed_zero_and_unobserved_null(usage, expected):
    spans = [row("llm", "LLM", **usage), row("root", "CHAT")]
    trace = project_trace(Trace("t", "codex", spans))
    projection = trace.require_projection()
    assert projection.steps[0].usage == expected
    assert serialize_projection(projection)["steps"][0]["usage"] == expected
    assert [span["span_id"] for span in trace.spans] == ["llm", "root"]
