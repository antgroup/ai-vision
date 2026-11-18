from datetime import date

import pytest

from agentlens.diagnoser.base import ExecutorFailure
from agentlens.diagnoser.executors.rule.custom.executor import RuleCustomExecutor
from agentlens.domain.enums import Verdict
from agentlens.domain.sandbox import SandboxError
from agentlens.service.datasource.trace import Trace

CODE = (
    "def evaluate(trace):\n"
    '    return {"metrics": [{"name": "q", "verdict": "bad", "reason": "local"}]}'
)
PROFILE = {
    "mode": "sandbox",
    "provider": "openshell",
    "connection_uuid": "connection",
    "timeout_seconds": 2,
}


def make_executor():
    return RuleCustomExecutor({"code": CODE, "metrics": [{"name": "q"}], "sandbox": PROFILE})


def test_sandbox_result_uses_current_rule_contract_and_never_local_worker(monkeypatch):
    from agentlens.diagnoser.executors.rule.custom import executor as module

    monkeypatch.setattr(module, "get_custom_rule_worker_pool", lambda: pytest.fail("local worker"))
    calls = []
    executor = make_executor()
    executor.configure_sandbox(
        lambda code, trace, timeout: (
            calls.append((code, trace, timeout)) or {"metrics": [{"name": "q", "verdict": "good"}]}
        ),
        PROFILE,
    )
    trace = Trace(trace_id="synthetic", runtime_type="other", spans=[{"date": date(2026, 11, 2)}])
    result = executor.evaluate(trace)
    assert result.verdict is Verdict.GOOD
    assert calls[0][0] == CODE
    assert calls[0][1]["spans"][0]["date"] == date(2026, 11, 2)
    assert 0 < calls[0][2] <= 2
    assert result.execution.evidence["backend"] == "sandbox"
    assert result.execution.evidence["provider"] == "openshell"


def test_frozen_sandbox_without_runtime_adapter_fails_closed():
    with pytest.raises(ExecutorFailure) as error:
        make_executor().evaluate(Trace(trace_id="t", runtime_type="other", spans=[]))
    assert error.value.code == "sandbox_unavailable"


def test_sandbox_cleanup_failure_is_structured_and_never_falls_back():
    executor = make_executor()

    def failure(*args):
        raise SandboxError("sandbox_cleanup_pending")

    executor.configure_sandbox(failure, PROFILE)
    with pytest.raises(ExecutorFailure) as error:
        executor.evaluate(Trace(trace_id="t", runtime_type="other", spans=[]))
    assert error.value.code == "sandbox_cleanup_pending"
    assert error.value.evidence["backend"] == "sandbox"


def test_remote_result_cannot_set_execution_or_unlisted_metrics():
    executor = make_executor()
    executor.configure_sandbox(
        lambda *args: {"metrics": [{"name": "other", "verdict": "good"}]}, PROFILE
    )
    with pytest.raises(ExecutorFailure) as error:
        executor.evaluate(Trace(trace_id="t", runtime_type="other", spans=[]))
    assert error.value.code == "invalid_result"


def test_enhanced_imports_require_remote_and_use_separate_budget(monkeypatch):
    from agentlens.diagnoser.executors.rule.custom import executor as module

    monkeypatch.setattr(module, "get_custom_rule_worker_pool", lambda: pytest.fail("local worker"))
    spec = {
        "code": (
            "import json\ndef evaluate(trace, context):\n"
            '    return {"metrics": [{"name": "q", "verdict": "good"}]}'
        ),
        "metrics": [{"name": "q"}],
        "rule_runtime": "python-eval-v1",
        "rule_runtime_manifest": "a" * 64,
    }
    local = RuleCustomExecutor(spec)
    with pytest.raises(ExecutorFailure) as failure:
        local.evaluate(Trace(trace_id="t", runtime_type="other", spans=[]))
    assert failure.value.code == "sandbox_runtime_required"
    remote = RuleCustomExecutor({**spec, "sandbox": {**PROFILE, "timeout_seconds": 60}})
    calls = []
    remote.configure_sandbox(
        lambda code, trace, timeout: (
            calls.append(timeout) or {"metrics": [{"name": "q", "verdict": "good"}]}
        ),
        {**PROFILE, "timeout_seconds": 60},
    )
    remote.evaluate(Trace(trace_id="t", runtime_type="other", spans=[]))
    assert calls == [30]
