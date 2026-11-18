from types import SimpleNamespace

import pytest

from agentlens.diagnoser.base import ExecutorFailure
from agentlens.domain.enums import DiagnoserMode, DiagnoserType, Verdict
from agentlens.kernel.worker.runner import Runner
from agentlens.service.datasource.trace import Trace

PROFILE = {
    "mode": "sandbox",
    "protocol_version": 1,
    "connection_uuid": "sandbox-a",
    "space_uuid": "space",
    "provider": "openshell",
    "endpoint": "http://plugin:8092",
    "image": "worker:fixed",
    "cpu": 1,
    "memory": "512Mi",
    "timeout_seconds": 5,
    "config_revision": 1,
}


def definition():
    return SimpleNamespace(
        uuid="diag",
        space_uuid="space",
        type=DiagnoserType.CUSTOM,
        mode=DiagnoserMode.RULE,
        spec={
            "code": 'def evaluate(trace):\n    return {"metrics":[{"name":"q","verdict":"bad"}]}',
            "metrics": [{"name": "q"}],
            "sandbox": PROFILE,
        },
    )


def runner(session_factory=None):
    return Runner(
        task_table=None,
        job_table=None,
        dataset_table=None,
        diagnoser_table=None,
        trace_store=None,
        record_store=None,
        self_uuid="worker",
        session_factory=session_factory,
    )


def test_frozen_custom_rule_routes_with_task_attempt_and_space(monkeypatch):
    import agentlens.kernel.worker.runner as module

    calls = []

    class Service:
        def __init__(self, sf):
            pass

        def execute(self, code, trace_dict, context):
            calls.append(context)
            return {"metrics": [{"name": "q", "verdict": "good"}]}

    monkeypatch.setattr(module, "SandboxExecutionService", Service)
    r = runner(lambda: None)
    r._execution_snapshot = {"diagnosers": []}
    prepared = r._prepare_executors([definition()], task=SimpleNamespace(uuid="task", attempt=3))
    result = prepared[0]["executor"].evaluate(
        Trace(trace_id="trace", runtime_type="other", spans=[])
    )
    assert result.verdict is Verdict.GOOD
    assert (calls[0].space_uuid, calls[0].task_uuid, calls[0].attempt, calls[0].diagnoser_uuid) == (
        "space",
        "task",
        3,
        "diag",
    )
    assert calls[0].frozen_profile == PROFILE


def test_frozen_sandbox_without_independent_sessions_fails_closed():
    r = runner()
    r._execution_snapshot = {"diagnosers": []}
    executor = r._prepare_executors([definition()], task=SimpleNamespace(uuid="task", attempt=1))[
        0
    ]["executor"]
    with pytest.raises(ExecutorFailure) as error:
        executor.evaluate(Trace(trace_id="trace", runtime_type="other", spans=[]))
    assert error.value.code == "sandbox_unavailable"


def test_legacy_task_without_snapshot_keeps_local_policy():
    r = runner()
    assert r._runtime_spec(definition())["sandbox"] == {"mode": "local"}
