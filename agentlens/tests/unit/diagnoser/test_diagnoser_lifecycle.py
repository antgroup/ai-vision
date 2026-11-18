"""New execution rejects retired definitions while raw CUSTOM and history remain usable."""

from copy import deepcopy
from dataclasses import replace

import pytest

from agentlens.domain.enums import DiagnoserMode, DiagnoserType, ModelConnectionStatus
from agentlens.domain.errors import Validation
from agentlens.service.execution_snapshot import (
    build_execution_snapshot,
    restore_execution_snapshot,
    snapshot_definitions,
)
from tests.unit.diagnoser.test_rule_identity_v3 import existing, task_inputs


@pytest.mark.parametrize(
    "spec",
    [
        {"scenario": "legacy"},
        {"runtime_type": "legacy"},
        {"executor": "legacy_instruction_leakage"},
        {"executor": "legacy_default"},
        {"scenario": "legacy", "executor": "llm_builtin"},
        {"runtime_type": "legacy", "executor": "llm_builtin"},
        {"scenario": "common", "executor": "unregistered_extension"},
    ],
)
def test_retired_identity_is_type_owned_and_cannot_bypass_via_one_shot(spec):
    from agentlens.diagnoser.lifecycle import is_retired_builtin

    assert is_retired_builtin(DiagnoserType.BUILTIN, spec)
    assert not is_retired_builtin(DiagnoserType.CUSTOM, spec)


def test_all_current_definitions_and_projector_aliases_remain_supported():
    from agentlens.diagnoser.definitions.catalog import load_builtin_definitions
    from agentlens.diagnoser.definitions.specialists import load_specialist_definitions
    from agentlens.diagnoser.lifecycle import is_retired_builtin
    from agentlens.diagnoser.registry import resolve_executor_name
    from agentlens.service.datasource.projection.registry import BUILTIN_PROJECTORS

    definitions = load_builtin_definitions() + load_specialist_definitions()
    definitions.append({"mode": "llm", "spec": {"executor": "llm_builtin"}})
    for definition in definitions:
        spec = definition["spec"]
        assert not is_retired_builtin(DiagnoserType.BUILTIN, spec)
        assert (
            resolve_executor_name(DiagnoserType.BUILTIN, DiagnoserMode(definition["mode"]), spec)
            == spec["executor"]
        )
        for projector in BUILTIN_PROJECTORS:
            for runtime in projector.runtime_aliases | {projector.canonical_runtime}:
                assert not is_retired_builtin(
                    DiagnoserType.BUILTIN, spec | {"runtime_type": f" {runtime.upper()} "}
                )


def test_registered_extension_executor_and_rule_scenario_remain_supported(monkeypatch):
    from agentlens.diagnoser.base import Executor
    from agentlens.diagnoser.executors.rule.builtin.registry import (
        BuiltinRuleDefinition,
        BuiltinRuleRegistry,
    )
    from agentlens.diagnoser.lifecycle import is_retired_builtin
    from agentlens.diagnoser.registry import ExecutorRegistry, resolve_executor_name

    monkeypatch.setattr(ExecutorRegistry, "_registry", dict(ExecutorRegistry._registry))
    monkeypatch.setattr(BuiltinRuleRegistry, "_definitions", dict(BuiltinRuleRegistry._definitions))
    monkeypatch.setattr(BuiltinRuleRegistry, "_names", dict(BuiltinRuleRegistry._names))

    @ExecutorRegistry.register("llm", "registered_extension")
    class Extension(Executor):
        def evaluate(self, trace): ...

    spec = {"executor": "registered_extension"}
    assert not is_retired_builtin(DiagnoserType.BUILTIN, spec)
    assert resolve_executor_name(DiagnoserType.BUILTIN, DiagnoserMode.LLM, spec) == spec["executor"]
    definition = BuiltinRuleDefinition(
        key="registered_rule",
        name="Registered rule",
        description="",
        scenario="extension",
        category="test",
        metrics=({"name": "quality"},),
    )
    BuiltinRuleRegistry.register(definition)(Extension)
    spec = definition.to_builtin_definition()["spec"]
    assert not is_retired_builtin(DiagnoserType.BUILTIN, spec)
    assert (
        resolve_executor_name(DiagnoserType.BUILTIN, DiagnoserMode.RULE, spec) == spec["executor"]
    )


@pytest.mark.parametrize("value", [None, "", [], {}, 0])
def test_missing_or_malformed_identity_uses_existing_field_validation(value):
    from agentlens.diagnoser.lifecycle import is_retired_builtin
    from agentlens.diagnoser.registry import resolve_executor_name

    spec = {"executor": value, "runtime_type": value, "scenario": value}
    assert not is_retired_builtin(DiagnoserType.BUILTIN, spec)
    with pytest.raises(Validation, match="缺少系统 Executor"):
        resolve_executor_name(DiagnoserType.BUILTIN, DiagnoserMode.LLM, spec)


def test_common_is_preserved_and_retired_snapshot_cannot_run_but_history_is_readable():
    row = existing(version="3.0.0")
    job, dataset = task_inputs(row)
    frozen = build_execution_snapshot(job, dataset, [row])
    old = deepcopy(frozen)
    old["diagnosers"][0]["spec"]["scenario"] = "legacy"
    old["diagnosers"][0]["spec"]["executor"] = "llm_builtin"
    before = deepcopy(old)
    assert snapshot_definitions(old)[row.uuid]["uuid"] == row.uuid
    with pytest.raises(Validation, match="retired|退役"):
        restore_execution_snapshot(old)
    assert old == before
    row.spec["scenario"] = "legacy"
    with pytest.raises(Validation, match="retired|退役"):
        build_execution_snapshot(job, dataset, [row])


def test_previous_abi_history_is_readable_without_execution_fall_forward():
    row = existing(version="3.0.0")
    job, dataset = task_inputs(row)
    old = build_execution_snapshot(job, dataset, [row])
    old["executor_revision"] = "1.3.0"
    for entry in old["diagnosers"]:
        entry["revision"] = "1.3.0"
    before = deepcopy(old)
    assert snapshot_definitions(old)[row.uuid]["uuid"] == row.uuid
    with pytest.raises(Validation, match="revision|ABI"):
        restore_execution_snapshot(old)
    assert old == before


def test_trigger_locks_shared_definitions_and_models_consistently_preserving_binding_order():
    from types import SimpleNamespace

    from agentlens.service.execution_snapshot import snapshot_from_tables

    a = existing(uuid="a", version="3.0.0", type=DiagnoserType.CUSTOM)
    b = existing(uuid="b", version="3.0.0", type=DiagnoserType.CUSTOM)
    for row, connection in [(a, "model-z"), (b, "model-a")]:
        row.mode = DiagnoserMode.LLM
        row.spec = {
            "executor": "llm_custom",
            "model_connection_uuid": connection,
            "prompt": "{{spans}}",
            "metrics": [{"name": "quality"}],
        }
    job, dataset = task_inputs(a)
    locks = []
    definitions = {row.uuid: row for row in (a, b)}
    datasets = SimpleNamespace(
        get_by_uuid_for_update=lambda uuid: locks.append("dataset:" + uuid) or dataset
    )
    diagnosers = SimpleNamespace(
        get_by_uuid_for_update=lambda uuid: locks.append("definition:" + uuid) or definitions[uuid]
    )

    def connection(uuid, space):
        locks.append("model:" + uuid)
        return SimpleNamespace(
            uuid=uuid,
            space_uuid=space,
            endpoint="https://example.test/v1",
            model_name="fixture",
            api_key="inert-private-key",
            status=ModelConnectionStatus.AVAILABLE,
        )

    models = SimpleNamespace(get_in_space_for_update=connection)
    for order in [["b", "a"], ["a", "b"]]:
        job.diagnoser_uuids = order
        locks.clear()
        frozen = snapshot_from_tables(job, datasets, diagnosers, models)
        assert locks == [
            "dataset:dataset",
            "definition:a",
            "definition:b",
            "model:model-a",
            "model:model-z",
        ]
        assert frozen["job"]["diagnoser_uuids"] == order
        assert [row["uuid"] for row in frozen["diagnosers"]] == order


def specialist(key="protected_content_exposure", variant="configured_exact_markers", config=None):
    from agentlens.diagnoser.definitions.specialists import (
        get_specialist_definition,
        specialist_spec,
    )
    from tests.unit.diagnoser.test_specialist_sync import configuration

    definition = get_specialist_definition(key, variant)
    return replace(
        existing(),
        name=definition["name"],
        version=definition["version"],
        mode=DiagnoserMode(definition["mode"]),
        spec=specialist_spec(definition, config or configuration()),
    )


def test_specialist_private_policy_is_frozen_internally_and_never_in_history_metadata():
    row = specialist()
    job, dataset = task_inputs(row)
    frozen = build_execution_snapshot(job, dataset, [row])
    assert frozen.get("execution_policy") is None
    private_policy = deepcopy(row.spec["specialist_policy"])
    assert frozen["diagnosers"][0]["spec"]["specialist_policy"] == private_policy
    assert "private-fixture" not in str(snapshot_definitions(frozen))
    row.spec["specialist_policy"]["markers"][0]["value"] = "changed"
    _, _, restored = restore_execution_snapshot(frozen)
    assert restored[0].spec["specialist_policy"] == private_policy


def test_specialist_configuration_drift_or_version_and_quick_policy_are_rejected():
    from agentlens.domain.execution_policy import bounded_core_execution_policy

    row = specialist()
    job, dataset = task_inputs(row)
    with pytest.raises(Validation, match="Specialist|specialist"):
        build_execution_snapshot(
            job, dataset, [row], execution_policy=bounded_core_execution_policy(["t"])
        )
    for change in ("configuration_digest", "version"):
        stale = deepcopy(row)
        if change == "version":
            stale.version = "stale"
        else:
            stale.spec[change] = "0" * 64
        with pytest.raises(Validation, match="specialist|Specialist"):
            build_execution_snapshot(job, dataset, [stale])


def test_specialist_runtime_gate_is_checked_at_freeze_and_restore():
    row = specialist()
    job, dataset = task_inputs(row)
    frozen = build_execution_snapshot(job, dataset, [row])
    dataset.spec["runtime_type"] = "unknown-native-app"
    with pytest.raises(Validation, match="runtime"):
        build_execution_snapshot(job, dataset, [row])
    frozen["dataset"]["spec"]["runtime_type"] = "unknown-native-app"
    with pytest.raises(Validation, match="runtime"):
        restore_execution_snapshot(frozen)


@pytest.mark.parametrize(
    "status", [ModelConnectionStatus.UNTESTED, ModelConnectionStatus.UNAVAILABLE]
)
def test_specialist_llm_requires_available_model_without_changing_core_advisory_status(status):
    from types import SimpleNamespace

    row = specialist(
        "answer_relevance",
        "explicit_current_text",
        {
            "policy": {"kind": "answer_relevance_v1", "context_mode": "explicit_current_text"},
            "model_connection_uuid": "connection",
        },
    )
    job, dataset = task_inputs(row)
    connection = SimpleNamespace(
        uuid="connection",
        space_uuid="space",
        endpoint="https://example.test/v1",
        model_name="m",
        api_key="private-model-fixture",
        status=status,
    )
    with pytest.raises(Validation, match="available|AVAILABLE|可用"):
        build_execution_snapshot(job, dataset, [row], model_connections={"connection": connection})
    connection.status = ModelConnectionStatus.AVAILABLE
    frozen = build_execution_snapshot(
        job, dataset, [row], model_connections={"connection": connection}
    )
    assert "private-model-fixture" not in str(frozen)
    assert restore_execution_snapshot(frozen)[2][0].spec == row.spec


@pytest.mark.parametrize("owned", ["retired", "specialist"])
def test_generic_patch_does_not_mutate_managed_retired_or_specialist_identity(owned):
    from types import SimpleNamespace

    from agentlens.web.routes.diagnoser import get_diagnoser, update_diagnoser
    from agentlens.web.schemas.diagnoser import DiagnoserUpdate

    row = specialist() if owned == "specialist" else existing(version="3.0.0")
    if owned == "retired":
        row.spec["scenario"] = "legacy"
    before = deepcopy(row)
    table = SimpleNamespace(
        get_by_uuid=lambda _: row,
        get_by_uuid_for_update=lambda _: row,
        update_by_uuid=lambda *args: pytest.fail("generic PATCH may not write managed identity"),
    )
    visible = get_diagnoser(row.uuid, table)
    assert visible.uuid == row.uuid
    if owned == "specialist":
        assert "private-fixture" not in str(visible.model_dump())
    with pytest.raises(Validation, match="retired|Specialist|specialist"):
        update_diagnoser(
            row.uuid,
            DiagnoserUpdate(name="renamed", spec={"model_connection_uuid": "different"}),
            table,
            None,
        )
    assert row == before


def test_disabled_retired_job_cannot_be_reenabled_by_status_only_patch():
    from types import SimpleNamespace

    from agentlens.domain.enums import JobStatus
    from agentlens.web.routes.job import update_job
    from agentlens.web.schemas.job import JobUpdate

    row = existing(version="3.0.0")
    row.spec["scenario"] = "legacy"
    job, dataset = task_inputs(row)
    job.status = JobStatus.DISABLED
    before = deepcopy(job)
    jobs = SimpleNamespace(
        get_by_uuid=lambda _: job,
        get_by_uuid_for_update=lambda _: job,
        update_by_uuid=lambda *args: pytest.fail("stale status-only Job must not resume"),
    )
    datasets = SimpleNamespace(get_by_uuid_for_update=lambda _: dataset)
    diagnosers = SimpleNamespace(get_by_uuid_for_update=lambda _: row)
    with pytest.raises(Validation, match="retired"):
        update_job(job.uuid, JobUpdate(status=JobStatus.ENABLED), jobs, datasets, diagnosers)
    assert job == before


def test_enable_validates_fresh_job_bindings_under_same_job_lock_as_migration():
    from types import SimpleNamespace

    from agentlens.domain.enums import JobStatus
    from agentlens.web.routes.job import update_job
    from agentlens.web.schemas.job import JobUpdate

    common = existing(version="3.0.0")
    retired = deepcopy(common)
    retired.uuid = "retired"
    retired.spec["scenario"] = "legacy"
    stale, dataset = task_inputs(common)
    stale.status = JobStatus.DISABLED
    current = deepcopy(stale)
    current.diagnoser_uuids = [retired.uuid]
    locks = []
    jobs = SimpleNamespace(
        get_by_uuid=lambda _: stale,
        get_by_uuid_for_update=lambda uuid: locks.append(uuid) or current,
        update_by_uuid=lambda *args: pytest.fail(
            "stale validation must not enable retired binding"
        ),
    )
    datasets = SimpleNamespace(get_by_uuid_for_update=lambda _: dataset)
    definitions = {common.uuid: common, retired.uuid: retired}
    diagnosers = SimpleNamespace(get_by_uuid_for_update=lambda uuid: definitions[uuid])
    with pytest.raises(Validation, match="retired"):
        update_job(current.uuid, JobUpdate(status=JobStatus.ENABLED), jobs, datasets, diagnosers)
    assert locks == [current.uuid]
    assert current.status == JobStatus.DISABLED
