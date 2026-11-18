"""Both callers use a gate that never disguises faults as skipped metrics."""

import pytest

from agentlens.diagnoser.base import Executor, execute_with_applicability
from agentlens.diagnoser.executors.llm.custom.executor import LlmCustomExecutor
from agentlens.service.datasource.projection.service import project_trace
from agentlens.service.datasource.trace import Trace


@pytest.mark.parametrize("reason", ["", "arbitrary_reason", 42])
def test_invalid_gate_result_is_a_failure_not_skip(reason):
    class InvalidGate(Executor):
        def applicability_skip_reason(self, trace):
            return reason

        def evaluate(self, trace):
            pytest.fail("invalid gate cannot evaluate")

    with pytest.raises(ValueError):
        execute_with_applicability(InvalidGate({}), Trace("t", "other", []))


def test_gate_exception_remains_a_failure():
    class BrokenGate(Executor):
        def applicability_skip_reason(self, trace):
            raise RuntimeError("projection/input failure")

        def evaluate(self, trace):
            pytest.fail("failed gate cannot evaluate")

    with pytest.raises(RuntimeError, match="projection/input"):
        execute_with_applicability(BrokenGate({}), Trace("t", "other", []))


def test_custom_raw_spans_supports_unknown_runtime_without_projection():
    executor = LlmCustomExecutor(
        {"prompt": "{{spans}}", "required_vars": ["spans"], "metrics": [{"name": "quality"}]}
    )
    assert (
        executor.applicability_skip_reason(
            project_trace(Trace("t", "my-unknown-runtime", [{"name": "raw"}]))
        )
        is None
    )


def test_custom_semantic_input_projection_failure_is_not_missing_evidence_skip():
    executor = LlmCustomExecutor(
        {
            "prompt": "{{user_query}}",
            "required_vars": ["user_query"],
            "metrics": [{"name": "quality"}],
        }
    )
    trace = Trace("t", "codex", [{"name": "bad"}], projection_error="invalid serialized messages")
    with pytest.raises(Exception, match="invalid serialized"):
        executor.applicability_skip_reason(trace)


@pytest.mark.parametrize("mode", ["rule", "llm"])
def test_known_runtime_unsupported_specialty_is_unavailable(mode, monkeypatch):
    from agentlens.diagnoser.executors.llm.builtin.one_shot import LlmBuiltinExecutor
    from agentlens.diagnoser.executors.rule.builtin.coding.test_execution import (
        TestExecutionExecutor,
    )
    from tests.unit.diagnoser.projection_factory import projection_from_dict

    trace = Trace("t", "deepagents", [], projection=projection_from_dict({}))
    executor = (
        TestExecutionExecutor({"metrics": [{"name": "test_execution_rate"}]})
        if mode == "rule"
        else LlmBuiltinExecutor(
            {"scenario": "coding", "prompt": "check", "metrics": [{"name": "quality"}]}
        )
    )
    if mode == "rule":
        monkeypatch.setattr(
            executor, "measure", lambda *args: pytest.fail("unsupported rule cannot measure")
        )
    assert executor.applicability_skip_reason(trace).startswith("not_applicable:")
