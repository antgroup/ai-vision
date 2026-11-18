"""CodeFuse rescue keeps old-attempt execution and deterministic record identity."""

from types import SimpleNamespace
from unittest.mock import patch

from agentlens.diagnoser.base import Executor
from agentlens.domain.diagnosis_record import DiagnosisResult, Execution, Metric
from agentlens.domain.enums import (
    DiagnoserMode,
    DiagnoserType,
    TaskStatus,
    Verdict,
)
from agentlens.kernel.worker.runner import (
    Runner,
    _deterministic_record_uuid,
    _RunAcc,
)
from agentlens.service.datasource.trace import Trace


def _chat_row(tid: str, sid: str, start: int, end: int) -> dict:
    return {
        "span_id": sid,
        "parent_span_id": "",
        "trace_id": tid,
        "name": "Turn",
        "kind": "SPAN_KIND_SERVER",
        "start_time_unix_nano": str(start),
        "end_time_unix_nano": str(end),
        "status_code": 1,
    }


def _codefuse_rows(tid: str) -> list[dict]:
    return [_chat_row(tid, f"{tid}-turn", 1716811200, 1716811250)]


class _SimpleExecutor(Executor):
    def evaluate(self, trace):
        return DiagnosisResult(
            metrics=[Metric(name="test", verdict=Verdict.GOOD)],
            execution=Execution.succeeded(),
        )


def _make_diagnoser():
    from agentlens.domain.diagnoser import Diagnoser

    return Diagnoser(
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


class _TasksStore:
    def __getattr__(self, name):
        raise AssertionError(f"unexpected Task access during evaluation: {name}")


# ── Test 1: Rescue during an old attempt ─────────────────────────────


class TestCodeFuseRescueDuringAttempt:
    def test_rescue_does_not_preempt_old_attempt_and_uuid_stays_deterministic(self):
        rows_a = _codefuse_rows("trace-a")
        rows_b = _codefuse_rows("trace-b")
        items = [
            Trace(trace_id="trace-a", runtime_type="codefuse", spans=rows_a),
            Trace(trace_id="trace-b", runtime_type="codefuse", spans=rows_b),
        ]

        written = []
        evaluated = []

        class _RescueExecutor(_SimpleExecutor):
            def evaluate(self, trace):
                rec = super().evaluate(trace)
                evaluated.append(trace.trace_id)
                if len(evaluated) == 1:
                    task.status = TaskStatus.PENDING
                return rec

        task = SimpleNamespace(
            uuid="task-1",
            attempt=1,
            status=TaskStatus.RUNNING,
            stats={},
        )
        runner = Runner(
            job_table=None,
            dataset_table=None,
            diagnoser_table=None,
            trace_store=None,
            record_store=SimpleNamespace(write_record=lambda r, **_kw: written.append(r)),
            self_uuid="worker-1",
            task_table=_TasksStore(),
        )

        with patch("agentlens.kernel.worker.runner.ExecutorRegistry") as mock_reg:
            mock_reg.create = staticmethod(lambda m, n, s: _RescueExecutor(s))

            acc = _RunAcc()
            runner._evaluate_all(
                task,
                SimpleNamespace(uuid="job-1", spec={"save_badcase_only": False}),
                [_make_diagnoser()],
                items,
                acc=acc,
            )

        assert evaluated == ["trace-a", "trace-b"]
        assert len(written) == 2
        uuid_before = written[0].uuid
        uuid_reexecution = _deterministic_record_uuid(
            "task-1",
            "trace",
            written[0].target_id,
            "dg-1",
        )
        assert uuid_before == uuid_reexecution
