"""Stable scoped builtin identity and canonical algorithm version boundaries."""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime

import pytest

from agentlens.diagnoser.executors.rule.builtin.registry import BuiltinRuleRegistry
from agentlens.domain.diagnoser import Diagnoser
from agentlens.domain.enums import DiagnoserMode, DiagnoserType
from agentlens.domain.errors import Validation
from agentlens.web.routes.diagnoser import _sync_builtin


class MemoryTable:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def list(self, **kwargs):
        return [
            row for row in self.rows if row.space_uuid == kwargs.get("space_uuid", row.space_uuid)
        ]

    def get_by_uuid(self, uuid):
        return next((row for row in self.rows if row.uuid == uuid), None)

    def insert(self, row):
        self.rows.append(row)

    def update_by_uuid(self, uuid, fields):
        row = self.get_by_uuid(uuid)
        for key, value in fields.items():
            if key == "type":
                value = DiagnoserType(value)
            if key == "mode":
                value = DiagnoserMode(value)
            setattr(row, key, value)


def existing(
    key="tool_success",
    *,
    uuid="managed",
    name="legacy name",
    version="v2",
    type=DiagnoserType.BUILTIN,
):
    definition = BuiltinRuleRegistry.get(key).to_builtin_definition()
    return Diagnoser(
        uuid,
        "space",
        "agent",
        name,
        "old description",
        type,
        DiagnoserMode.RULE,
        deepcopy(definition["spec"]),
        version,
        datetime.now(),
        datetime.now(),
        None,
        None,
    )


def test_all_eight_definitions_have_algorithm_versions_and_explicit_roles():
    definitions = BuiltinRuleRegistry.list("common", "coding")
    assert len(definitions) == 8
    names = {
        "tool_success": "工具执行结果",
        "stuck_loop": "重复调用线索",
        "sensitive_leakage": "凭据暴露线索",
        "high_risk_command": "风险操作提醒",
        "total_latency": "运行耗时",
        "user_interruption": "用户中断事件",
        "test_execution": "修改后验证",
        "rewrite_overlap": "反复编辑线索",
    }
    for definition in definitions:
        exported = definition.to_builtin_definition()
        assert exported["name"] == names[definition.key]
        assert exported["version"] == "3.0.0"
        assert exported["spec"]["score_role"] == (
            "quality" if definition.key == "test_execution" else "signal"
        )


def test_sync_renames_updates_version_and_preserves_uuid_in_scope():
    row = existing()
    table = MemoryTable([row, replace(existing(uuid="other"), agent_uuid="other-agent")])
    definition = BuiltinRuleRegistry.get("tool_success").to_builtin_definition()
    synced, operation = _sync_builtin(table, "space", "agent", definition)
    assert operation == "updated"
    assert synced.uuid == "managed"
    assert synced.name == definition["name"]
    assert synced.version == definition["version"]
    assert len(table.rows) == 2
    assert table.rows[1].name == "legacy name"


def test_duplicate_builtin_key_is_explicitly_rejected_without_writes():
    table = MemoryTable([existing(), existing(uuid="duplicate")])
    before = deepcopy(table.rows)
    with pytest.raises(Validation, match="duplicate|重复"):
        _sync_builtin(
            table, "space", "agent", BuiltinRuleRegistry.get("tool_success").to_builtin_definition()
        )
    assert table.rows == before


def test_custom_name_collision_is_never_overwritten():
    definition = BuiltinRuleRegistry.get("tool_success").to_builtin_definition()
    row = existing(name=definition["name"], type=DiagnoserType.CUSTOM)
    table = MemoryTable([row])
    before = deepcopy(row)
    with pytest.raises(Validation, match="collision|冲突"):
        _sync_builtin(table, "space", "agent", definition)
    assert row == before


def task_inputs(row):
    from agentlens.domain.dataset import Dataset
    from agentlens.domain.enums import DatasetType, JobStatus, JobTriggerType
    from agentlens.domain.job import Job

    common = dict(
        gmt_create=datetime.now(), gmt_modified=datetime.now(), creator=None, modifier=None
    )
    dataset = Dataset(
        uuid="dataset",
        space_uuid="space",
        agent_uuid="agent",
        name="fixture",
        description="",
        type=DatasetType.SIGNOZ,
        spec={"runtime_type": "codex", "query": {}},
        **common,
    )
    job = Job(
        uuid="job",
        space_uuid="space",
        agent_uuid="agent",
        name="fixture",
        description="",
        trigger_type=JobTriggerType.MANUAL,
        schedule_cron=None,
        status=JobStatus.ENABLED,
        dataset_uuid=dataset.uuid,
        diagnoser_uuids=[row.uuid],
        **common,
    )
    return job, dataset


def test_old_persisted_builtin_requires_explicit_sync_before_new_task():
    from agentlens.service.execution_snapshot import build_execution_snapshot

    row = existing()
    job, dataset = task_inputs(row)
    with pytest.raises(Validation, match="sync|同步|version"):
        build_execution_snapshot(job, dataset, [row])
    table = MemoryTable([row])
    _sync_builtin(
        table, "space", "agent", BuiltinRuleRegistry.get("tool_success").to_builtin_definition()
    )
    frozen = build_execution_snapshot(job, dataset, [row])
    assert frozen["diagnosers"][0]["uuid"] == "managed"
    assert frozen["diagnosers"][0]["version"] == "3.0.0"
    assert frozen["diagnosers"][0]["spec"]["score_role"] == "signal"
    assert frozen["executor_revision"] == "1.6.0"


def test_only_explicit_finite_rule_policy_is_frozen():
    from agentlens.service.execution_snapshot import build_execution_snapshot

    row = existing(version="3.0.0")
    row.spec.update(tool_success_min_rate=0.75, arbitrary_secret="never-copy")
    job, dataset = task_inputs(row)
    frozen = build_execution_snapshot(job, dataset, [row])
    spec = frozen["diagnosers"][0]["spec"]
    assert spec["tool_success_min_rate"] == 0.75
    assert "arbitrary_secret" not in spec


@pytest.mark.parametrize(
    "key,policy",
    [
        ("tool_success", {"tool_success_min_rate": 0.75}),
        ("total_latency", {"latency_budget_ms": 1500}),
    ],
)
def test_explicit_policy_survives_sync_reread_unchanged_and_frozen_restore(key, policy):
    from agentlens.service.execution_snapshot import (
        build_execution_snapshot,
        restore_execution_snapshot,
    )

    row = existing(key=key)
    row.spec.update(policy)
    table = MemoryTable([row])
    definition = BuiltinRuleRegistry.get(key).to_builtin_definition()
    synced, operation = _sync_builtin(table, "space", "agent", definition)
    assert operation == "updated"
    assert {field: table.get_by_uuid(synced.uuid).spec[field] for field in policy} == policy
    _, second = _sync_builtin(table, "space", "agent", definition)
    assert second == "unchanged"
    job, dataset = task_inputs(synced)
    frozen = build_execution_snapshot(job, dataset, [synced])
    _, _, restored = restore_execution_snapshot(frozen)
    assert {field: restored[0].spec[field] for field in policy} == policy


@pytest.mark.parametrize("stale_field", ["version", "score_role", "metrics"])
def test_current_abi_restore_rejects_stale_managed_rule_definition(stale_field):
    from agentlens.service.execution_snapshot import (
        _digest,
        build_execution_snapshot,
        restore_execution_snapshot,
    )

    row = existing(version="3.0.0")
    row.spec["score_role"] = "signal"
    job, dataset = task_inputs(row)
    frozen = build_execution_snapshot(job, dataset, [row])
    entry = frozen["diagnosers"][0]
    if stale_field == "version":
        entry["version"] = "v2"
    elif stale_field == "score_role":
        entry["spec"]["score_role"] = "quality"
    else:
        entry["spec"]["metrics"] = [{"name": "forged-quality"}]
    entry["digest"] = _digest({key: value for key, value in entry.items() if key != "digest"})

    with pytest.raises(Validation, match="sync|同步|version|role"):
        restore_execution_snapshot(frozen)


def test_custom_version_remains_freezable_and_restorable():
    from agentlens.service.execution_snapshot import (
        build_execution_snapshot,
        restore_execution_snapshot,
    )

    row = existing(type=DiagnoserType.CUSTOM, version="private-revision-17")
    row.spec["score_role"] = "quality"
    job, dataset = task_inputs(row)
    frozen = build_execution_snapshot(job, dataset, [row])
    _, _, restored = restore_execution_snapshot(frozen)
    assert restored[0].version == "private-revision-17"
    assert restored[0].type == DiagnoserType.CUSTOM


def test_old_abi_metadata_remains_readable_without_running_old_algorithm():
    from agentlens.service.execution_snapshot import (
        _digest,
        build_execution_snapshot,
        restore_execution_snapshot,
        snapshot_definitions,
    )

    row = existing(version="3.0.0")
    row.spec["score_role"] = "signal"
    job, dataset = task_inputs(row)
    frozen = build_execution_snapshot(job, dataset, [row])
    frozen["executor_revision"] = "1.1.0"
    entry = frozen["diagnosers"][0]
    entry.update(revision="1.1.0", version="v2", name="工具成功率")
    entry["spec"]["score_role"] = "quality"
    entry["digest"] = _digest({key: value for key, value in entry.items() if key != "digest"})

    metadata = snapshot_definitions(frozen)[row.uuid]
    assert (metadata["version"], metadata["revision"], metadata["name"]) == (
        "v2",
        "1.1.0",
        "工具成功率",
    )
    assert metadata["score_role"] == "quality"
    with pytest.raises(Validation, match="unsupported executor revision"):
        restore_execution_snapshot(frozen)


def test_stale_current_builtin_in_legacy_task_fails_before_source(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from agentlens.kernel.worker.runner import Runner

    row = existing()
    job, dataset = task_inputs(row)
    tasks = Mock()
    tasks.cas_start_running.return_value = True
    tasks.get_by_uuid.return_value = SimpleNamespace(
        uuid="legacy", job_uuid=job.uuid, attempt=1, execution_snapshot=None
    )
    tasks.mark_failed.return_value = False
    jobs, datasets, diagnosers = Mock(), Mock(), Mock()
    jobs.get_by_uuid.return_value = job
    datasets.get_by_uuid.return_value = dataset
    diagnosers.get_by_uuid.return_value = row
    runner = Runner(
        task_table=tasks,
        job_table=jobs,
        dataset_table=datasets,
        diagnoser_table=diagnosers,
        trace_store=None,
        record_store=None,
        self_uuid="worker",
    )
    source = Mock()
    monkeypatch.setattr(runner, "_run_signoz", source)
    runner._run_sync("legacy")
    source.assert_not_called()
    tasks.mark_failed.assert_called_once()
    assert (
        "sync" in tasks.mark_failed.call_args.kwargs["msg_append"].lower()
        or "同步" in tasks.mark_failed.call_args.kwargs["msg_append"]
    )


def persisted(table, **kwargs):
    """Use the explicit disposable OceanBase/seekdb fixture, never an implicit DB."""
    from agentlens.utils.uuid_gen import new_diagnoser_uuid

    row = existing(uuid=new_diagnoser_uuid(), **kwargs)
    table.insert(row)
    return table.get_by_uuid(row.uuid)


def test_db_sync_renamed_rule_preserves_uuid_and_exact_scope(session):
    from agentlens.infra.ob.tables.diagnoser_table import DiagnoserTable

    table = DiagnoserTable(session)
    managed = persisted(table)
    other_agent = replace(existing(uuid="other-agent-row"), agent_uuid="other-agent")
    other_space = replace(existing(uuid="other-space-row"), space_uuid="other-space")
    for row in (other_agent, other_space):
        table.insert(row)
    untouched = [table.get_by_uuid(row.uuid) for row in (other_agent, other_space)]
    definition = BuiltinRuleRegistry.get("tool_success").to_builtin_definition()

    synced, operation = _sync_builtin(table, "space", "agent", definition)

    assert operation == "updated"
    assert synced.uuid == managed.uuid
    reread = table.get_by_uuid(managed.uuid)
    assert reread.name == definition["name"]
    assert reread.version == "3.0.0"
    assert reread.spec["score_role"] == "signal"
    assert len(table.list_exact_scope("space", "agent")) == 1
    assert [table.get_by_uuid(row.uuid) for row in (other_agent, other_space)] == untouched


def test_db_custom_name_collision_rolls_back_without_overwriting(session):
    from agentlens.infra.ob.tables.diagnoser_table import DiagnoserTable

    table = DiagnoserTable(session)
    definition = BuiltinRuleRegistry.get("tool_success").to_builtin_definition()
    persisted(table)
    custom = persisted(table, name=definition["name"], type=DiagnoserType.CUSTOM)
    before = table.list_exact_scope("space", "agent")

    with pytest.raises(Validation, match="collision|冲突"):
        _sync_builtin(table, "space", "agent", definition)
    table.rollback()

    assert table.list_exact_scope("space", "agent") == before
    assert table.get_by_uuid(custom.uuid).type == DiagnoserType.CUSTOM
    # The real session remains usable after the rejected synchronization.
    inserted = persisted(table, key="total_latency", name="after rejected sync")
    assert table.get_by_uuid(inserted.uuid) is not None


def test_db_duplicate_builtin_keys_are_rejected_without_partial_update(session):
    from agentlens.infra.ob.tables.diagnoser_table import DiagnoserTable

    table = DiagnoserTable(session)
    persisted(table, name="first historical name")
    persisted(table, name="second historical name")
    before = table.list_exact_scope("space", "agent")

    with pytest.raises(Validation, match="duplicate|重复"):
        _sync_builtin(
            table, "space", "agent", BuiltinRuleRegistry.get("tool_success").to_builtin_definition()
        )
    table.rollback()

    assert table.list_exact_scope("space", "agent") == before


def test_db_name_constraint_rollback_keeps_custom_and_sync_guard_usable(session):
    from sqlalchemy.exc import IntegrityError

    from agentlens.infra.ob.tables.diagnoser_table import DiagnoserTable

    table = DiagnoserTable(session)
    definition = BuiltinRuleRegistry.get("tool_success").to_builtin_definition()
    custom = persisted(table, name=definition["name"], type=DiagnoserType.CUSTOM)
    before = table.get_by_uuid(custom.uuid)
    # Trigger the actual scoped unique constraint, then recover the same session.
    with pytest.raises(IntegrityError):
        persisted(table, name=definition["name"], type=DiagnoserType.CUSTOM)
    table.rollback()
    assert table.get_by_uuid(custom.uuid) == before

    with pytest.raises(Validation, match="collision|冲突"):
        _sync_builtin(table, "space", "agent", definition)
    table.rollback()
    assert table.list_exact_scope("space", "agent") == [before]


@pytest.mark.parametrize(
    "key,field,initial,updated",
    [
        ("tool_success", "tool_success_min_rate", 0.75, 0.8),
        ("total_latency", "latency_budget_ms", 1500, 2500),
    ],
)
def test_db_api_policy_create_patch_sync_get_and_snapshot_preserve_value(
    client, session, key, field, initial, updated
):
    from agentlens.infra.ob.tables.diagnoser_table import DiagnoserTable
    from agentlens.service.execution_snapshot import (
        build_execution_snapshot,
        restore_execution_snapshot,
    )

    definition = BuiltinRuleRegistry.get(key).to_builtin_definition()
    response = client.post(
        "/api/v1/diagnoser",
        json={
            "space_uuid": "space",
            "agent_uuid": "agent",
            "name": definition["name"],
            "type": "builtin",
            "mode": "rule",
            "version": "forged-old",
            "spec": {field: initial},
        },
    )
    assert response.status_code == 201, response.text
    row = response.json()
    assert row["version"] == "3.0.0"
    assert row["spec"][field] == initial
    uuid = row["uuid"]
    patch = client.patch(f"/api/v1/diagnoser/{uuid}", json={"spec": {field: updated}})
    assert patch.status_code == 200, patch.text
    sync = client.post(
        "/api/v1/diagnosers/sync-common-builtins",
        json={"space_uuid": "space", "agent_uuid": "agent"},
    )
    assert sync.status_code == 200, sync.text
    reread = client.get(f"/api/v1/diagnoser/{uuid}")
    assert reread.status_code == 200
    assert reread.json()["spec"][field] == updated
    managed = DiagnoserTable(session).get_by_uuid(uuid)
    job, dataset = task_inputs(managed)
    frozen = build_execution_snapshot(job, dataset, [managed])
    _, _, restored = restore_execution_snapshot(frozen)
    assert restored[0].uuid == uuid
    assert restored[0].spec[field] == updated
