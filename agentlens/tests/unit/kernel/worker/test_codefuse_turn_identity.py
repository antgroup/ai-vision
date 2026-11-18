"""Record identity is one source trace plus one diagnoser."""

from agentlens.kernel.worker.runner import _deterministic_record_uuid


def _uuid(trace_id: str, diagnoser: str) -> str:
    return _deterministic_record_uuid(
        task_uuid="task-1",
        target_type="trace",
        target_id=trace_id,
        diagnoser_uuid=diagnoser,
    )


class TestTraceUuid:
    def test_same_trace_and_diagnoser_is_deterministic(self):
        assert _uuid("trace-x", "dg-1") == _uuid("trace-x", "dg-1")

    def test_different_traces_are_distinct(self):
        assert _uuid("trace-a", "dg-1") != _uuid("trace-b", "dg-1")

    def test_different_diagnosers_are_distinct(self):
        assert _uuid("trace-x", "dg-1") != _uuid("trace-x", "dg-2")
