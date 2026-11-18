"""Production regression tests for Coding source traces and record identity.

1. Coding: one source trace/one CHAT turn → one record per source trace
2. Coding record identity remains deterministic
"""

from types import SimpleNamespace
from unittest.mock import patch

from agentlens.diagnoser.base import Executor
from agentlens.diagnoser.registry import ExecutorRegistry
from agentlens.domain.diagnosis_record import DiagnosisResult, Execution, Metric
from agentlens.domain.enums import (
    DiagnoserMode,
    DiagnoserType,
    Verdict,
)
from agentlens.kernel.worker.runner import (
    Runner,
    _deterministic_record_uuid,
    _RunAcc,
)
from agentlens.service.datasource.trace import Trace


def _chat_row(tid: str, sid: str, parent: str | None, name: str, start: int, end: int) -> dict:
    return {
        "span_id": sid,
        "parent_span_id": parent or "",
        "trace_id": tid,
        "name": name,
        "kind": "SPAN_KIND_SERVER",
        "start_time_unix_nano": str(start),
        "end_time_unix_nano": str(end),
        "status_code": 1,
        "attributes.gen_ai.span.kind": "CHAT",
        "attributes.gen_ai.input.messages": '[{"role":"user","content":"q"}]',
        "attributes.gen_ai.output.messages": '[{"role":"assistant","content":"a"}]',
    }


def _codefuse_trace(trace_id: str) -> list[dict]:
    """Build one source trace containing its single CHAT turn."""
    return [
        _chat_row(
            trace_id,
            f"{trace_id}-turn",
            None,
            "Turn 1",
            1716811200 * 10**9,
            1716811250 * 10**9,
        )
    ]


class _SimpleExecutor(Executor):
    def evaluate(self, trace):
        return DiagnosisResult(
            metrics=[Metric(name="test", verdict=Verdict.GOOD)],
            execution=Execution.succeeded(),
        )


def _make_runner(written: list) -> Runner:
    return Runner(
        task_table=None,
        job_table=None,
        dataset_table=None,
        diagnoser_table=None,
        trace_store=None,
        record_store=SimpleNamespace(write_record=lambda r, **_kw: written.append(r)),
        self_uuid="worker-1",
    )


def _make_diagnosers():
    from agentlens.domain.diagnoser import Diagnoser

    return [
        Diagnoser(
            uuid="dg-1",
            space_uuid="s",
            agent_uuid="",
            name="test",
            description="",
            type=DiagnoserType.CUSTOM,
            mode=DiagnoserMode.RULE,
            spec={"executor": "test", "metrics": [{"name": "test", "desc": "test"}]},
            version="v1",
            gmt_create=None,
            gmt_modified=None,
            creator=None,
            modifier=None,
        )
    ]


class TestCodeFuseTraceRunnerRegression:
    """Two source traces produce two records."""

    def test_two_source_traces_write_two_distinct_records(self):
        rows_a = _codefuse_trace("trace-a")
        rows_b = _codefuse_trace("trace-b")

        items = [
            Trace(trace_id="trace-a", runtime_type="codefuse", spans=rows_a),
            Trace(trace_id="trace-b", runtime_type="codefuse", spans=rows_b),
        ]

        written = []
        runner = _make_runner(written)
        diagnosers = _make_diagnosers()

        with patch.object(
            ExecutorRegistry, "create", staticmethod(lambda m, n, s: _SimpleExecutor(s))
        ):
            acc = _RunAcc()
            runner._evaluate_all(
                SimpleNamespace(uuid="task-1", attempt=1),
                SimpleNamespace(uuid="job-1", spec={"save_badcase_only": False}),
                diagnosers,
                items,
                acc=acc,
            )

        # One record per source trace.
        assert len(written) == 2, f"Expected 2 records, got {len(written)}"

        # Source trace target_ids and UUIDs remain distinct.
        target_ids = {r.target_id for r in written}
        assert target_ids == {"trace-a", "trace-b"}

        uuids = {r.uuid for r in written}
        assert len(uuids) == 2, f"Expected 2 distinct UUIDs, got {uuids}"


class TestCodingRuntimeRetention:
    def test_codex_projection_keeps_dataset_runtime_type(self):
        rows = _codefuse_trace("trace-a")
        written = []
        observed_runtime_types = []
        runner = _make_runner(written)

        class RuntimeExecutor(_SimpleExecutor):
            def evaluate(self, trace):
                observed_runtime_types.append(trace.runtime_type)
                return super().evaluate(trace)

        with patch.object(
            ExecutorRegistry,
            "create",
            staticmethod(lambda _m, _n, spec: RuntimeExecutor(spec)),
        ):
            runner._evaluate_all(
                SimpleNamespace(uuid="task-1", attempt=1),
                SimpleNamespace(uuid="job-1"),
                _make_diagnosers(),
                [Trace(trace_id="trace-a", runtime_type="codex", spans=rows)],
                acc=_RunAcc(),
            )

        assert observed_runtime_types == ["codex"]


class TestCodeFuseCancel:
    """Logical record identity is deterministic."""

    def test_record_identity_is_deterministic(self):
        uuid_1 = _deterministic_record_uuid("task-1", "trace", "trace-a", "dg-1")
        uuid_2 = _deterministic_record_uuid("task-1", "trace", "trace-a", "dg-1")
        assert uuid_1 == uuid_2
