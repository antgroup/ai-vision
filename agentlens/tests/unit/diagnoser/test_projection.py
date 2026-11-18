"""Shared projection service boundaries use supported runtime facts."""

from agentlens.diagnoser.registry import ExecutorRegistry
from agentlens.service.datasource.projection import Projection, project_trace
from agentlens.service.datasource.projection.models import TraceOutcome
from agentlens.service.datasource.trace import Trace
from tests.unit.diagnoser.test_shared_evidence_projection import row


def test_project_trace_empty_records_projection_error():
    trace = project_trace(Trace(trace_id="t", runtime_type="codex", spans=[]))
    assert trace.projection is None
    assert trace.projection_error == "trace has no source spans"


def test_project_trace_reuses_prepared_projection():
    projection = Projection(
        timestamp=None,
        user_query="q",
        agent_output="a",
        outcome=TraceOutcome(False, None, None, False),
        labels={},
    )
    trace = Trace("t", "codex", [], projection=projection)
    assert project_trace(trace) is trace


def test_projection_executor_evaluates_typed_outcome_from_source():
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
    trace = project_trace(Trace("t", "codex", spans))
    executor = ExecutorRegistry.create(
        "rule", "total_latency", {"metrics": [{"name": "latency_decomposition"}]}
    )
    assert executor.evaluate(trace).metric("latency_decomposition") is not None


def test_regular_error_is_not_user_interruption():
    span = row("root", "CHAT")
    span.update(status_code=2, status_message="request timeout")
    assert (
        project_trace(Trace("t", "codex", [span])).require_projection().outcome.interrupted is False
    )


def test_cancelled_turn_retains_observed_interruption():
    span = row("root", "CHAT")
    span["name"] = "Turn (Cancelled)"
    assert (
        project_trace(Trace("t", "codex", [span])).require_projection().outcome.interrupted is True
    )
