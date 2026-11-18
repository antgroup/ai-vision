"""Saved checks use exact selections without copying their private configuration into receipts."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agentlens.domain import execution_policy
from agentlens.domain.enums import ModelConnectionStatus
from agentlens.domain.errors import Conflict, Validation
from agentlens.service import quick_runs as service
from agentlens.service.execution_snapshot import (
    build_execution_snapshot,
    restore_execution_snapshot,
)
from tests.unit.diagnoser.test_core_execution_policy import inputs
from tests.unit.diagnoser.test_diagnoser_lifecycle import specialist
from tests.unit.diagnoser.test_rule_identity_v3 import task_inputs
from tests.unit.service.test_quick_runs import scoped_payload


def saved_payload(row):
    payload = scoped_payload()
    payload.pop("builtin_keys")
    payload.pop("scene_key")
    payload.update(space_uuid=row.space_uuid, agent_uuid=row.agent_uuid, diagnoser_uuid=row.uuid)
    payload["dataset_spec"].update(space_filter=row.space_uuid, runtime_type="langchain")
    return payload


def source():
    reader = Mock()
    reader.read_explicit_trace_rows.return_value = [{"trace_id": "trace-a"}]
    return reader


def table_for(monkeypatch, row):
    table = Mock()
    table.get_by_uuid_for_update.return_value = row
    table.list_exact_scope.return_value = [row] if row else []
    monkeypatch.setattr(service, "DiagnoserTable", lambda _: table)
    return table


def test_specialist_options_and_preflight_never_export_private_configuration(monkeypatch):
    row = specialist()
    table_for(monkeypatch, row)
    payload = saved_payload(row)
    session = Mock()

    options = service.saved_options(session, payload)
    prepared = service.preflight(session, source(), payload)

    assert options["diagnosers"][0]["uuid"] == row.uuid
    assert options["diagnosers"][0]["selectable"] is True
    assert options["diagnosers"][0]["is_specialist"] is True
    assert options["diagnosers"][0]["builtin_key"] == "protected_content_exposure"
    assert prepared["plan"]["saved_diagnoser"]["uuid"] == row.uuid
    assert len(prepared["plan"]["saved_diagnoser"]["digest"]) == 64
    for public in (options, prepared):
        serialized = json.dumps(public)
        assert "private-fixture" not in serialized
        assert '"specialist_policy"' not in serialized
        assert '"prompt"' not in serialized
        assert '"snapshot"' not in serialized
    session.add.assert_not_called()
    session.commit.assert_not_called()


def test_personal_core_options_provision_then_require_available_model(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from agentlens.diagnoser.definitions.catalog import load_core_definitions
    from agentlens.web.deps import get_diagnoser_table, get_session, get_settings, get_trace_store
    from agentlens.web.errors import install_error_handlers
    from agentlens.web.routes.quick_runs import diagnosis_runs_router
    from tests.unit.web.test_llm_generation_options import _Table

    table = _Table()
    session = Mock()
    row, _, _, model = inputs()
    payload = saved_payload(row)
    options_input = {key: payload[key] for key in ("space_uuid", "agent_uuid", "dataset_spec")}
    models = Mock()
    models.get_in_space_for_update.side_effect = lambda model_uuid, space_uuid: (
        model if (model_uuid, space_uuid) == (model.uuid, model.space_uuid) else None
    )
    monkeypatch.setattr(service, "DiagnoserTable", lambda _: table)
    monkeypatch.setattr(service, "ModelConnectionTable", lambda _: models)
    app = FastAPI()
    app.include_router(diagnosis_runs_router)
    install_error_handlers(app)
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_diagnoser_table] = lambda: table
    app.dependency_overrides[get_settings] = lambda: SimpleNamespace(
        default_builtin_scenario_list=("common", "coding")
    )
    app.dependency_overrides[get_trace_store] = source
    with TestClient(app) as client:
        otherwise = client.post("/api/v1/runs/options", json=options_input)
        assert otherwise.status_code == 200, otherwise.text
        assert otherwise.json()["diagnosers"] == []
        preset_options = {**options_input, "ensure_core_presets": True}
        provisioned = client.post("/api/v1/runs/options", json=preset_options)
        assert provisioned.status_code == 200, provisioned.text
        saved_choices = provisioned.json()["diagnosers"]
        assert len([choice for choice in saved_choices if choice["mode"] == "rule"]) == 8
        choices = [choice for choice in saved_choices if choice["mode"] == "llm"]
        assert {(choice["builtin_key"], choice["canonical_variant"]) for choice in choices} == {
            (definition["key"], definition["variant"]) for definition in load_core_definitions()
        }
        assert all(not choice["selectable"] for choice in choices)
        assert {choice["unavailable_reason"] for choice in choices} == {"QUICK_MODEL_UNAVAILABLE"}

        target = choices[0]["uuid"]
        table.get_by_uuid(target).spec["model_connection_uuid"] = model.uuid
        unavailable = client.post("/api/v1/runs/options", json=preset_options).json()["diagnosers"]
        assert all(not choice["selectable"] for choice in unavailable if choice["mode"] == "llm")
        model.status = ModelConnectionStatus.AVAILABLE
        run_choices = client.post("/api/v1/runs/options", json=preset_options).json()["diagnosers"]
        assert [
            choice["uuid"]
            for choice in run_choices
            if choice["mode"] == "llm" and choice["selectable"]
        ] == [target]
        assert "prompt" not in json.dumps(run_choices)
        preflight_input = {**payload, "diagnoser_uuid": target}
        with_option = client.post(
            "/api/v1/runs/preflight", json={**preflight_input, "ensure_core_presets": True}
        )
        assert with_option.status_code == 422, with_option.text
        prepared = client.post("/api/v1/runs/preflight", json=preflight_input)
        assert prepared.status_code == 200, prepared.text
        assert prepared.json()["plan"]["saved_diagnoser"]["uuid"] == target
        assert prepared.json()["plan"]["schema_version"] == 2

        unmapped = {"dataset_spec": {**payload["dataset_spec"], "runtime_type": "unmapped"}}
        unsupported = client.post("/api/v1/runs/options", json={**preset_options, **unmapped})
        assert unsupported.status_code == 200, unsupported.text
        core_choices = [row for row in unsupported.json()["diagnosers"] if row["mode"] == "llm"]
        assert len(core_choices) == 4
        assert all(not row["selectable"] for row in core_choices)
        assert {row["unavailable_reason"] for row in core_choices} == {
            "DIAGNOSIS_RUN_DIAGNOSER_UNAVAILABLE"
        }
        blocked = client.post("/api/v1/runs/preflight", json={**preflight_input, **unmapped})
        assert blocked.status_code == 422, blocked.text
        assert blocked.json()["msg"] == "DIAGNOSIS_RUN_DIAGNOSER_UNAVAILABLE"


def test_saved_definition_drift_rejects_unaccepted_plan(monkeypatch):
    row = specialist()
    table_for(monkeypatch, row)
    plan = service.preflight(Mock(), source(), saved_payload(row))["plan"]
    row.description += " changed"
    with pytest.raises(Conflict, match="QUICK_PLAN_CHANGED"):
        service.validate_plan_current(Mock(), source(), plan)


@pytest.mark.parametrize("field", ["space_uuid", "agent_uuid"])
def test_saved_selection_rejects_foreign_definition_before_writes(monkeypatch, field):
    row = specialist()
    payload = saved_payload(row)
    setattr(row, field, "foreign")
    table_for(monkeypatch, row)
    session = Mock()
    with pytest.raises(Validation, match="DIAGNOSIS_RUN_DIAGNOSER_UNAVAILABLE"):
        service.preflight(session, source(), payload)
    session.add.assert_not_called()
    session.commit.assert_not_called()


def test_saved_policy_freezes_specialist_without_weakening_old_core_policy():
    row = specialist()
    job, dataset = task_inputs(row)
    dataset.spec["runtime_type"] = "langchain"
    policy = execution_policy.bounded_diagnosis_execution_policy(["trace-a"])
    frozen = build_execution_snapshot(job, dataset, [row], execution_policy=policy)
    assert frozen["execution_policy"]["request_budget"] == {"total": 30, "per_case": 3}
    assert frozen["diagnosers"][0]["spec"]["specialist_policy"] == row.spec["specialist_policy"]
    assert restore_execution_snapshot(frozen)[2][0].uuid == row.uuid
    with pytest.raises(Validation, match="Specialist"):
        build_execution_snapshot(
            job,
            dataset,
            [row],
            execution_policy=execution_policy.bounded_core_execution_policy(["trace-a"]),
        )


def test_saved_policy_requires_available_model_and_keeps_runtime_status_gate():
    from agentlens.kernel.worker.runner import Runner

    row, job, dataset, connection = inputs()
    policy = execution_policy.bounded_diagnosis_execution_policy(["trace-a"])
    with pytest.raises(Validation, match="available"):
        build_execution_snapshot(
            job,
            dataset,
            [row],
            model_connections={"connection": connection},
            execution_policy=policy,
        )
    connection.status = ModelConnectionStatus.AVAILABLE
    frozen = build_execution_snapshot(
        job, dataset, [row], model_connections={"connection": connection}, execution_policy=policy
    )
    connection.status = ModelConnectionStatus.UNAVAILABLE
    runner = Runner(
        task_table=None,
        job_table=None,
        dataset_table=None,
        diagnoser_table=None,
        trace_store=None,
        record_store=None,
        self_uuid="w",
        model_connection_table=SimpleNamespace(get_in_space=lambda *_: connection),
    )
    runner._execution_snapshot = frozen
    assert "model" not in runner._runtime_spec(row)


def test_accepted_saved_receipt_replay_does_not_reread_deleted_definition(monkeypatch):
    row = specialist()
    table = table_for(monkeypatch, row)
    prepared = service.preflight(Mock(), source(), saved_payload(row))
    identity = {
        key: key
        for key in (
            "receipt_uuid",
            "request_identity",
            "input_digest",
            "plan_digest",
            "dataset_uuid",
            "job_uuid",
            "task_uuid",
        )
    }
    identity["plan_digest"] = prepared["plan_digest"]
    job = SimpleNamespace(
        uuid="job_uuid",
        space_uuid=row.space_uuid,
        agent_uuid=row.agent_uuid,
        spec={"quick_receipt": identity},
    )
    jobs = SimpleNamespace(get_by_uuid=lambda _: job, get_by_uuid_for_update=lambda _: job)
    monkeypatch.setattr(service, "JobTable", lambda _: jobs)
    monkeypatch.setattr(
        service, "TaskTable", lambda _: SimpleNamespace(get_by_uuid=lambda _: object())
    )
    table.get_by_uuid_for_update.side_effect = AssertionError(
        "accepted replay cannot read live definition"
    )
    result = service.run_quick(Mock(), source(), {**identity, "plan": prepared["plan"]})
    assert result == {key: identity[key] for key in ("dataset_uuid", "job_uuid", "task_uuid")}


def test_legacy_retest_cannot_export_saved_specialist_snapshot(monkeypatch):
    row = specialist()
    job, dataset = task_inputs(row)
    dataset.spec["runtime_type"] = "langchain"
    frozen = build_execution_snapshot(
        job,
        dataset,
        [row],
        execution_policy=execution_policy.bounded_diagnosis_execution_policy(["trace-a"]),
    )
    monkeypatch.setattr(
        service,
        "TaskTable",
        lambda _: SimpleNamespace(get_by_uuid=lambda _: SimpleNamespace(execution_snapshot=frozen)),
    )
    payload = saved_payload(row)
    payload.pop("diagnoser_uuid")
    payload["before_task_uuid"] = "old"
    with pytest.raises(Validation, match="QUICK_BEFORE_UNSUPPORTED") as error:
        service.preflight(Mock(), source(), payload)
    assert "private-fixture" not in str(error.value)


def test_saved_task_context_exposes_safe_identity_without_retest_or_private_policy(monkeypatch):
    row = specialist()
    job, dataset = task_inputs(row)
    dataset.spec["runtime_type"] = "langchain"
    frozen = build_execution_snapshot(
        job,
        dataset,
        [row],
        execution_policy=execution_policy.bounded_diagnosis_execution_policy(["trace-a"]),
    )
    monkeypatch.setattr(
        service,
        "TaskTable",
        lambda _: SimpleNamespace(
            get_by_uuid=lambda _: SimpleNamespace(
                uuid="task", job_uuid=job.uuid, execution_snapshot=frozen
            )
        ),
    )
    monkeypatch.setattr(service, "JobTable", lambda _: SimpleNamespace(get_by_uuid=lambda _: job))
    context = service.task_context(Mock(), "task")
    assert context["trace_ids"] == ["trace-a"]
    assert context["can_retest"] is False
    assert context["retest_denial_reason"] is None
    assert "private-fixture" not in json.dumps(context)


def test_saved_custom_rule_snapshot_executes_the_saved_code():
    from dataclasses import replace

    from agentlens.diagnoser.registry import ExecutorRegistry
    from agentlens.domain.enums import DiagnoserType
    from agentlens.service.datasource.trace import Trace
    from tests.unit.diagnoser.test_rule_identity_v3 import existing

    row = replace(
        existing(),
        type=DiagnoserType.CUSTOM,
        version="v1",
        spec={
            "code": (
                "def evaluate(trace):\n"
                "    return {'metrics': [{'name': 'quality', 'verdict': 'good', 'reason': ''}]}\n"
            ),
            "metrics": [{"name": "quality", "description": "Saved custom result"}],
        },
    )
    job, dataset = task_inputs(row)
    frozen = build_execution_snapshot(
        job,
        dataset,
        [row],
        execution_policy=execution_policy.bounded_diagnosis_execution_policy(["trace-a"]),
    )
    restored = restore_execution_snapshot(frozen)[2][0]
    executor = ExecutorRegistry.create("rule", "rule_custom", restored.spec)
    try:
        result = executor.evaluate(Trace("trace-a", "unknown", []))
        assert result.metrics[0].verdict.value == "good"
    finally:
        executor.close()


def test_saved_preflight_http_rejects_configuration_overrides(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from agentlens.web import deps
    from agentlens.web.routes.quick_runs import diagnosis_runs_router

    row = specialist()
    table_for(monkeypatch, row)
    app = FastAPI()
    app.include_router(diagnosis_runs_router)
    app.dependency_overrides[deps.get_session] = lambda: Mock()
    app.dependency_overrides[deps.get_trace_store] = source
    with TestClient(app) as client:
        payload = saved_payload(row)
        response = client.post("/api/v1/runs/preflight", json=payload)
        assert response.status_code == 200
        assert "private-fixture" not in response.text
        for extra in (
            {"spec": {}},
            {"model_connection_uuid": "foreign"},
            {"before_task_uuid": "old"},
        ):
            assert (
                client.post("/api/v1/runs/preflight", json={**payload, **extra}).status_code == 422
            )


def test_saved_custom_llm_uses_frozen_prompt_and_scoped_request_hooks():
    from dataclasses import replace

    from agentlens.domain.enums import DiagnoserType
    from agentlens.infra.llm_judge.attempts import AttemptAuditError, TrialAttempts
    from agentlens.kernel.worker.runner import Runner
    from agentlens.service.datasource.trace import Trace

    row, job, dataset, connection = inputs()
    row = replace(
        row,
        type=DiagnoserType.CUSTOM,
        version="v1",
        spec={
            "prompt": "saved prompt {{trace_id}}",
            "required_vars": ["trace_id"],
            "model_connection_uuid": "connection",
            "metrics": [{"name": "quality"}],
        },
    )
    connection.status = ModelConnectionStatus.AVAILABLE
    frozen = build_execution_snapshot(
        job,
        dataset,
        [row],
        model_connections={"connection": connection},
        execution_policy=execution_policy.bounded_diagnosis_execution_policy(["trace-a"]),
    )
    row.spec["prompt"] = "changed live prompt"
    restored = restore_execution_snapshot(frozen)[2]
    runner = Runner(
        task_table=None,
        job_table=None,
        dataset_table=None,
        diagnoser_table=None,
        trace_store=None,
        record_store=None,
        self_uuid="worker",
        model_connection_table=SimpleNamespace(get_in_space=lambda *_: connection),
    )
    runner._execution_snapshot = frozen
    attempts = TrialAttempts()
    runner._request_ledger = Mock()
    runner._request_ledger.bind.return_value = attempts
    executor = runner._prepare_executors(restored, task=SimpleNamespace(uuid="task", attempt=1))[0][
        "executor"
    ]
    prompts = []

    class Judge:
        def judge(self, prompt, *, attempts):
            attempts.claim()
            prompts.append(prompt)
            return {"metrics": [{"name": "quality", "verdict": "good", "reason": ""}]}

        def close(self):
            pass

    executor._judge = Judge()
    try:
        assert executor.evaluate(Trace("trace-a", "unknown", [])).metrics[0].verdict.value == "good"
        assert prompts[0].startswith("saved prompt trace-a")
        assert attempts.reserved == 1
        assert runner._request_ledger.bind.call_args.args[:3] == ("task", "worker", 1)
        with pytest.raises(AttemptAuditError):
            executor.attempt_hooks_for("unselected-trace")
    finally:
        executor.close()
