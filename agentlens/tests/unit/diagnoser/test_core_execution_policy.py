"""Trusted bounded execution is explicit; legacy availability and metadata stay intact."""

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from agentlens.diagnoser.base import (
    ApplicabilitySkipped,
    ExecutorFailure,
    execute_with_applicability,
)
from agentlens.diagnoser.definitions.catalog import get_core_definition
from agentlens.domain.enums import DiagnoserMode, ModelConnectionStatus
from agentlens.domain.errors import Validation
from agentlens.domain.execution_policy import bounded_core_execution_policy
from agentlens.infra.llm_judge.attempts import AttemptRefused
from agentlens.kernel.worker.runner import Runner
from agentlens.service.execution_snapshot import (
    build_execution_snapshot,
    restore_execution_snapshot,
    snapshot_definitions,
)
from tests.unit.diagnoser.test_general_llm_templates import core, trace
from tests.unit.diagnoser.test_rule_identity_v3 import existing, task_inputs


def inputs():
    definition = get_core_definition("task_completion", "text_response")
    d = replace(
        existing(),
        mode=DiagnoserMode.LLM,
        name=definition["name"],
        spec=definition["spec"],
        version=definition["version"],
    )
    d.spec["model_connection_uuid"] = "connection"
    job, dataset = task_inputs(d)
    connection = SimpleNamespace(
        uuid="connection",
        space_uuid="space",
        endpoint="https://example.test/v1",
        model_name="m",
        api_key="dummy",
        status=ModelConnectionStatus.UNTESTED,
    )
    return d, job, dataset, connection


def test_trusted_policy_and_core_identity_are_frozen_not_job_or_browser_policy():
    d, job, dataset, connection = inputs()
    policy = bounded_core_execution_policy(["t"])
    frozen = build_execution_snapshot(
        job, dataset, [d], model_connections={"connection": connection}, execution_policy=policy
    )
    assert frozen["executor_revision"] == "1.6.0"
    assert frozen["diagnosers"][0]["spec"]["canonical_variant"] == "text_response"
    assert frozen["execution_policy"]["request_budget"] == {"total": 30, "per_case": 3}
    policy["request_budget"]["total"] = 999
    assert frozen["execution_policy"]["request_budget"]["total"] == 30
    _, _, restored = restore_execution_snapshot(frozen)
    assert restored[0].spec["required_evidence"] == d.spec["required_evidence"]
    assert "dummy" not in str(frozen)


@pytest.mark.parametrize("trusted", [False, True])
@pytest.mark.parametrize("status", list(ModelConnectionStatus))
def test_runner_runtime_model_status_is_advisory_only_under_frozen_trusted_policy(trusted, status):
    d, job, dataset, connection = inputs()
    connection.status = status
    runner = Runner(
        task_table=None,
        job_table=None,
        dataset_table=None,
        diagnoser_table=None,
        trace_store=None,
        record_store=None,
        model_connection_table=SimpleNamespace(get_in_space=lambda *a: connection),
        self_uuid="w",
    )
    runner._execution_snapshot = build_execution_snapshot(
        job,
        dataset,
        [d],
        model_connections={"connection": connection},
        execution_policy=bounded_core_execution_policy(["t"]) if trusted else None,
    )
    runtime = runner._runtime_spec(d)
    assert ("model" in runtime) is (trusted or status == ModelConnectionStatus.AVAILABLE)
    assert connection.status == status


def test_invalid_or_cross_space_policy_fails_and_historical_metadata_is_still_readable():
    d, job, dataset, connection = inputs()
    policy = bounded_core_execution_policy(["t"])
    policy["request_budget"]["total"] = 31
    with pytest.raises((Validation, ValueError)):
        build_execution_snapshot(
            job, dataset, [d], model_connections={"connection": connection}, execution_policy=policy
        )
    connection.space_uuid = "other"
    with pytest.raises(Validation):
        build_execution_snapshot(
            job,
            dataset,
            [d],
            model_connections={"connection": connection},
            execution_policy=bounded_core_execution_policy(["t"]),
        )
    connection.space_uuid = "space"
    frozen = build_execution_snapshot(
        job, dataset, [d], model_connections={"connection": connection}
    )
    old = deepcopy(frozen)
    old["executor_revision"] = old["diagnosers"][0]["revision"] = "1.2.0"
    assert snapshot_definitions(old)[d.uuid]["version"] == "1.0.0"
    with pytest.raises(Validation):
        restore_execution_snapshot(old)


@pytest.mark.parametrize("historical", [0, 1, 3])
def test_first_budget_refusal_skips_but_historical_attempt_refusal_fails(historical):
    ex = core()

    class Refusing:
        def claim(self):
            raise AttemptRefused(historical)

    class Judge:
        def judge(self, prompt, *, attempts):
            attempts.claim()

    ex._judge = Judge()
    ex.attempt_hooks_for = lambda trace_id: Refusing()
    if historical == 0:
        assert isinstance(execute_with_applicability(ex, trace()), ApplicabilitySkipped)
    else:
        with pytest.raises(ExecutorFailure, match="budget"):
            execute_with_applicability(ex, trace())


def test_core_execution_uses_saved_prompt_without_reloading_catalog(monkeypatch):
    ex = core()
    from agentlens.diagnoser.definitions import catalog

    monkeypatch.setattr(
        catalog, "load_core_definitions", lambda: pytest.fail("execution cannot reread catalog")
    )
    assert ex.applicability_skip_reason(trace()) is None
