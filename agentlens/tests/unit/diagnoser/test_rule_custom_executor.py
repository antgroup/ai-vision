from datetime import date, datetime, timedelta, timezone

import pytest

from agentlens.diagnoser.base import ExecutorFailure
from agentlens.diagnoser.executors.rule.custom.executor import RuleCustomExecutor
from agentlens.diagnoser.executors.rule.custom.pool import CustomRuleWorkerPool
from agentlens.diagnoser.executors.rule.custom.validator import ValidationError
from agentlens.domain.enums import ExecutionStatus, Verdict
from agentlens.service.datasource.trace import Trace
from tests.unit.diagnoser.projection_factory import trace_from_dict


def _projection(agent_output: str = "hello"):
    return trace_from_dict(
        {
            "trace_id": "t-1",
            "runtime_type": "codex",
            "user_query": "test question",
            "agent_output": agent_output,
            "intent": "",
            "steps": [],
            "outcome": {"total_latency_ms": 100},
        }
    )


GOOD_CODE = """
def evaluate(trace):
    output = trace.get("agent_output", "")
    if output:
        return {"metrics": [{
            "name": "test_metric", "verdict": "good",
            "reason": "has output", "evidence": {}
        }]}
    return {"metrics": [{
        "name": "test_metric", "verdict": "bad",
        "reason": "no output", "evidence": {}
    }]}
"""

BAD_CODE = """
def evaluate(trace):
    return {"metrics": [{
        "name": "test_metric", "verdict": "bad",
        "reason": "low quality", "evidence": {"signal": "missing answer"}
    }]}
"""

RUNTIME_ERROR_CODE = """
def evaluate(trace):
    return 1 / 0
"""

INVALID_RETURN_CODE = """
def evaluate(trace):
    return "not a dict"
"""


def _spec(code: str) -> dict:
    return {
        "code": code,
        "metrics": [{"name": "test_metric", "desc": "Test"}],
    }


class TestRuleCustomExecutor:
    def test_returned_names_must_match_declared_names(self):
        code = """
def evaluate(trace):
    return {"metrics": [{
        "name": "quality", "verdict": "good", "reason": "ok", "evidence": {}
    }]}
"""
        executor = RuleCustomExecutor({"code": code, "metrics": [{"name": "diagnoser-name"}]})

        with pytest.raises(ExecutorFailure, match="声明指标"):
            executor.evaluate(_projection())

    def test_good_verdict_and_success_execution(self):
        result = RuleCustomExecutor(_spec(GOOD_CODE)).evaluate(_projection("hello"))

        assert result.verdict is Verdict.GOOD
        assert result.metrics[0].name == "test_metric"
        assert result.metrics[0].verdict is Verdict.GOOD
        assert result.execution.status is ExecutionStatus.SUCCEEDED

    def test_bad_verdict_and_metric_evidence(self):
        result = RuleCustomExecutor(_spec(BAD_CODE)).evaluate(_projection())

        assert result.verdict is Verdict.BAD
        assert result.metrics[0].reason == "low quality"
        assert result.metrics[0].evidence == {"signal": "missing answer"}

    def test_runtime_error_raises_structured_failure(self):
        executor = RuleCustomExecutor(_spec(RUNTIME_ERROR_CODE))

        with pytest.raises(ExecutorFailure) as exc_info:
            executor.evaluate(_projection())

        assert exc_info.value.code == "rule_runtime_error"
        assert "ZeroDivisionError" in exc_info.value.msg
        assert "code_hash" in exc_info.value.evidence

    def test_invalid_return_raises_structured_failure(self):
        executor = RuleCustomExecutor(_spec(INVALID_RETURN_CODE))

        with pytest.raises(ExecutorFailure) as exc_info:
            executor.evaluate(_projection())

        assert exc_info.value.code == "invalid_result"

    def test_missing_code_raises_at_init(self):
        with pytest.raises(ValidationError, match="code"):
            RuleCustomExecutor({"metrics": [{"name": "m"}]})

    def test_execution_evidence_includes_code_hash_and_duration(self):
        result = RuleCustomExecutor(_spec(GOOD_CODE)).evaluate(_projection())

        assert isinstance(result.execution.evidence["code_hash"], str)
        assert result.execution.evidence["execution_ms"] >= 0


class TestTraceInput:
    """Custom Rule reads one custom Trace contract for every runtime."""

    PROJECTION_CODE = """
def evaluate(trace):
    rows = trace.get("spans", [])
    row_count = len(rows)
    if trace.get("user_query"):
        return {"metrics": [{
            "name": "test", "verdict": "good",
            "reason": "projection mode", "evidence": {}
        }]}
    return {"metrics": [{
        "name": "test", "verdict": "good", "reason": "raw mode, N rows",
        "evidence": {"row_count": row_count}
    }]}
"""

    def _executor(self):
        return RuleCustomExecutor(
            {
                "code": self.PROJECTION_CODE,
                "metrics": [{"name": "test", "desc": "Test"}],
            }
        )

    def test_semantic_projection_is_exposed_alongside_spans(self):
        value = trace_from_dict(
            {
                "trace_id": "t-1",
                "runtime_type": "codex",
                "user_query": "hello",
                "agent_output": "hi",
            }
        )

        result = self._executor().evaluate(value)

        assert result.verdict is Verdict.GOOD
        assert result.metrics[0].reason == "projection mode"

    def test_custom_input_receives_the_same_span_rows_as_llm_templates(self):
        spans = [
            {"span_id": "s1", "name": "step1", "trace_id": "t-1"},
            {"span_id": "s2", "name": "step2", "trace_id": "t-1"},
        ]
        value = Trace(trace_id="t-1", runtime_type="other", spans=spans)

        result = self._executor().evaluate(value)

        assert result.metrics[0].reason.startswith("raw mode")
        assert result.metrics[0].evidence == {"row_count": 2}

    @pytest.mark.parametrize("execution_path", ["one_shot", "pool", "pool_fallback"])
    def test_source_dates_cross_worker_boundary_without_mutating_trace(
        self, monkeypatch, execution_path
    ):
        updated_at = datetime(2026, 10, 27, 17, 40, 21, 123456)
        observed_at = updated_at.replace(tzinfo=timezone(timedelta(hours=8)))
        spans = [
            {
                "span_id": "s1",
                "updated_at": updated_at,
                "attributes": {
                    "date": date(2026, 10, 27),
                    "observations": [observed_at, "回答", True, 3, None],
                },
            }
        ]
        trace = Trace(trace_id="t-1", runtime_type="other", spans=spans)
        code = """
def evaluate(trace):
    return {"metrics": [{
        "name": "test", "verdict": "good", "evidence": {"span": trace["spans"][0]}
    }]}
"""
        pool = None if execution_path == "one_shot" else CustomRuleWorkerPool(size=1)
        if execution_path == "pool":
            pool.start()
        monkeypatch.setattr(
            "agentlens.diagnoser.executors.rule.custom.executor.get_custom_rule_worker_pool",
            lambda: pool,
        )
        try:
            result = RuleCustomExecutor({"code": code, "metrics": [{"name": "test"}]}).evaluate(
                trace
            )
        finally:
            if pool is not None:
                pool.stop()

        assert result.execution.status is ExecutionStatus.SUCCEEDED
        assert result.metrics[0].evidence == {
            "span": {
                "span_id": "s1",
                "updated_at": "2026-10-27T17:40:21.123456",
                "attributes": {
                    "date": "2026-10-27",
                    "observations": ["2026-10-27T17:40:21.123456+08:00", "回答", True, 3, None],
                },
            }
        }
        assert trace.spans is spans
        assert spans[0]["updated_at"] is updated_at
        assert spans[0]["attributes"]["observations"][0] is observed_at
