import json
from collections import defaultdict
from types import SimpleNamespace

import pytest

from agentlens.diagnoser.base import Executor
from agentlens.domain.diagnosis_record import DiagnosisResult, Execution, Metric
from agentlens.domain.enums import DiagnoserMode, DiagnoserType, Verdict
from agentlens.infra.antcollector import InMemoryAntcollectorClient
from agentlens.infra.signoz.client import InMemorySignozClient
from agentlens.kernel.worker.runner import Runner, _failed_result, _RunAcc
from agentlens.service.datasource.projection import project_trace
from agentlens.service.datasource.record_snapshot import build_record_snapshot
from agentlens.service.datasource.trace import ProjectionUnavailable, Trace
from agentlens.service.diagnosis_record_store import DiagnosisRecordStore


def _root(span_id, content):
    return {
        "span_id": span_id,
        "name": "request",
        "parent_span_id": "",
        "start_time_unix_nano": "1790989323000000000",
        "end_time_unix_nano": "1790989323100000000",
        "attributes.langfuse.observation.input": content,
        "attributes.langfuse.observation.output": "answer",
    }


class _SemanticExecutor(Executor):
    def evaluate(self, trace):
        trace.require_projection()
        return DiagnosisResult(
            metrics=[Metric(name="result", verdict=Verdict.GOOD)],
            execution=Execution.succeeded(),
        )


class _PayloadLimitedWriter(InMemoryAntcollectorClient):
    """The external persistence boundary rejects oversize record bodies."""

    def write_span(self, table, name, attributes, body, **kwargs):
        if len(json.dumps(body, default=str, ensure_ascii=False).encode()) > 65_536:
            raise ValueError("record body exceeds fixture ingest limit")
        super().write_span(table, name, attributes, body, **kwargs)


def test_large_projection_failure_preserves_failed_and_good_records():
    large_input = "source payload " * 700_000
    failed = Trace("failed-trace", "langchain", [_root("a", large_input), _root("b", "q")])
    good = Trace("good-trace", "langchain", [_root("c", "question")])
    storage = defaultdict(list)
    store = DiagnosisRecordStore(
        InMemorySignozClient(storage), _PayloadLimitedWriter(storage), table="records"
    )
    runner = Runner(
        task_table=None,
        job_table=None,
        dataset_table=None,
        diagnoser_table=None,
        trace_store=None,
        record_store=store,
        self_uuid="fixture-worker",
    )
    diagnosers = [
        SimpleNamespace(
            uuid=f"check-{index}",
            name=f"Check {index}",
            type=DiagnoserType.BUILTIN,
            mode=DiagnoserMode.RULE,
            version="v1",
            spec={"metrics": [{"name": "result"}]},
        )
        for index in range(2)
    ]
    acc = _RunAcc()
    count = runner._evaluate_all(
        SimpleNamespace(uuid="task", attempt=1),
        SimpleNamespace(uuid="job", space_uuid="space", spec={"save_badcase_only": False}),
        diagnosers,
        [failed, good],
        acc=acc,
        record_source_projection=True,
        prepared=[{"diagnoser": row, "executor": _SemanticExecutor({})} for row in diagnosers],
    )

    assert count == 4
    bodies = [json.loads(row["attributes.record.body"]) for row in storage["records"]]
    failed_bodies = [body for body in bodies if body["target_id"] == "failed-trace"]
    good_bodies = [body for body in bodies if body["target_id"] == "good-trace"]
    assert len(failed_bodies) == len(good_bodies) == 2
    for body in failed_bodies:
        assert body["execution"]["code"] == "projection_unavailable"
        assert body["execution"]["evidence"] == {
            "projection_failure": {"cause": "framework_root_ambiguous"}
        }
        assert "多个请求根 Span" in body["execution"]["msg"]
        assert body["metrics"][0]["verdict"] == "failed"
        assert len(json.dumps(body).encode()) < 4096
        snapshot = body["trace_snapshot"]
        assert snapshot["trace_id"] == "failed-trace"
        assert snapshot["runtime_type"] == "langchain"
        assert snapshot["projection_error"] == "ValueError: ambiguous framework request root"
        assert snapshot["source_span_count"] == 2
        assert snapshot["spans_omitted"] is True
        assert "spans" not in snapshot
    assert all(body["metrics"][0]["verdict"] == "good" for body in good_bodies)
    assert all(body["trace_snapshot"]["user_query"] == "question" for body in good_bodies)
    assert acc.stats["source"]["projection_failed"] == 1
    assert failed.spans[0]["attributes.langfuse.observation.input"] == large_input


def test_raw_custom_snapshot_is_preserved_after_supported_projection_failure():
    trace = project_trace(Trace("raw-trace", "langchain", [_root("a", "q"), _root("b", "q")]))
    assert trace.projection_error is not None
    assert build_record_snapshot(trace)["spans"] == trace.spans
    assert trace.to_python_value()["spans"] is trace.spans


def test_runner_keeps_successful_custom_raw_record_after_projection_failure():
    class RawExecutor(Executor):
        def evaluate(self, trace):
            assert len(trace.to_python_value()["spans"]) == 2
            return DiagnosisResult(
                metrics=[Metric(name="result", verdict=Verdict.GOOD)],
                execution=Execution.succeeded(),
            )

    trace = Trace("raw-trace", "langchain", [_root("a", "q"), _root("b", "q")])
    records = []
    runner = Runner(
        task_table=None,
        job_table=None,
        dataset_table=None,
        diagnoser_table=None,
        trace_store=None,
        record_store=SimpleNamespace(write_record=lambda record, **_kwargs: records.append(record)),
        self_uuid="fixture-worker",
    )
    diagnoser = SimpleNamespace(
        uuid="custom",
        name="Custom raw",
        type=DiagnoserType.CUSTOM,
        mode=DiagnoserMode.RULE,
        version="v1",
        spec={"metrics": [{"name": "result"}]},
    )
    count = runner._evaluate_all(
        SimpleNamespace(uuid="task", attempt=1),
        SimpleNamespace(uuid="job", space_uuid="space", spec={"save_badcase_only": False}),
        [diagnoser],
        [trace],
        acc=_RunAcc(),
        prepared=[{"diagnoser": diagnoser, "executor": RawExecutor({})}],
    )
    assert count == 1
    assert records[0].metrics[0].verdict is Verdict.GOOD
    assert records[0].trace_snapshot["spans"] == trace.spans
    assert "spans_omitted" not in records[0].trace_snapshot


@pytest.mark.parametrize("runtime", ["unknown", "legacy"])
def test_unknown_runtime_raw_snapshot_is_preserved_for_failed_records(runtime):
    trace = project_trace(Trace("raw-trace", runtime, [_root("a", "q")]))
    assert build_record_snapshot(trace, projection_failed=True)["spans"] == trace.spans


def test_projection_failure_message_is_bounded_without_changing_source_error():
    trace = Trace("failed-trace", "langchain", [], projection_error="x" * 50_000)
    snapshot = build_record_snapshot(trace, projection_failed=True)
    assert len(json.dumps(snapshot).encode()) < 4096
    assert snapshot["projection_error"].endswith("…(truncated)")
    assert trace.projection_error == "x" * 50_000


@pytest.mark.parametrize(
    "rows,cause,explanation",
    [
        (
            [{**_root("child", "private-input"), "parent_span_id": "missing-parent"}],
            "framework_root_missing",
            "缺少请求根 Span",
        ),
        (
            [_root("a", "private-input"), _root("b", "private-input")],
            "framework_root_ambiguous",
            "多个请求根 Span",
        ),
        ([], "source_spans_empty", "没有可用的 Span"),
    ],
)
def test_known_projection_failure_has_safe_specific_reason(rows, cause, explanation):
    trace = project_trace(Trace("failed-trace", "langfuse-sdk", rows))
    assert trace.projection is None
    with pytest.raises(ProjectionUnavailable) as failure:
        trace.require_projection()
    definition = SimpleNamespace(spec={"metrics": [{"name": "latency_decomposition"}]})
    result = _failed_result(definition, failure.value)
    assert result.execution.status.value == "failed"
    assert result.execution.code == "projection_unavailable"
    assert result.execution.evidence == {"projection_failure": {"cause": cause}}
    assert explanation in result.execution.msg
    assert result.metrics[0].verdict is Verdict.FAILED
    assert result.metrics[0].reason == result.execution.msg
    assert "private-input" not in json.dumps(result.execution.evidence)
    assert "private-input" not in result.summary
    if rows:
        # The diagnostic string remains compatible with historical compact snapshots.
        assert trace.projection_error == "ValueError: ambiguous framework request root"
    else:
        assert trace.projection_error == "trace has no source spans"


def test_unknown_projection_failure_keeps_private_parser_error_out_of_result():
    trace = project_trace(
        Trace(
            "failed-trace",
            "langchain",
            [
                {
                    **_root("root", "private-input"),
                    "attributes.aivision.request.input": "private-parser-input",
                    "attributes.aivision.request.input.encoding": "json",
                }
            ],
        )
    )
    assert trace.projection_error is not None
    with pytest.raises(ProjectionUnavailable) as failure:
        trace.require_projection()
    result = _failed_result(SimpleNamespace(spec={"metrics": [{"name": "result"}]}), failure.value)
    assert result.execution.code == "projection_unavailable"
    assert result.execution.msg == "轨迹投影不可用"
    assert result.execution.evidence == {}
    assert "private" not in result.summary


def test_unknown_projection_cause_is_not_public_evidence():
    result = _failed_result(
        SimpleNamespace(spec={"metrics": [{"name": "result"}]}),
        ProjectionUnavailable("private-parser-input", cause="private-parser-cause"),
    )
    assert result.execution.evidence == {}
    assert result.summary == "轨迹投影不可用"


def test_coding_chat_anchor_keeps_its_existing_projection_without_request_root():
    row = {
        **_root("turn", "question"),
        "parent_span_id": "unobserved-parent",
        "attributes.gen_ai.span.kind": "CHAT",
    }
    trace = project_trace(Trace("coding-trace", "codex", [row]))
    assert trace.projection is not None
    assert trace.projection_failure is None
    assert trace.require_projection().outcome.total_latency_ms == 100
    assert trace.spans == [row]
