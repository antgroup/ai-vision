"""Sandbox choices are Task inputs, not mutable runtime routing hints."""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime

import pytest

from agentlens.diagnoser.definitions.specialists import get_specialist_definition, specialist_spec
from agentlens.domain.dataset import Dataset
from agentlens.domain.diagnoser import Diagnoser
from agentlens.domain.enums import (
    DatasetType,
    DiagnoserMode,
    DiagnoserType,
    JobStatus,
    JobTriggerType,
)
from agentlens.domain.errors import Validation
from agentlens.domain.job import Job
from agentlens.service.execution_snapshot import (
    build_execution_snapshot,
    restore_execution_snapshot,
)

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
    "config_revision": 2,
}


def inputs(sandbox=None):
    common = dict(
        gmt_create=datetime.now(), gmt_modified=datetime.now(), creator=None, modifier=None
    )
    d = Diagnoser(
        uuid="custom",
        space_uuid="space",
        agent_uuid="agent",
        name="rule",
        description="",
        type=DiagnoserType.CUSTOM,
        mode=DiagnoserMode.RULE,
        version="v1",
        spec={
            "code": 'def evaluate(trace):\n    return {"metrics": [{"name":"q","verdict":"good"}]}',
            "metrics": [{"name": "q"}],
            **({"sandbox": sandbox} if sandbox is not None else {}),
        },
        **common,
    )
    ds = Dataset(
        uuid="dataset",
        space_uuid="space",
        agent_uuid="agent",
        name="fixture",
        description="",
        type=DatasetType.SIGNOZ,
        spec={"runtime_type": "other", "query": {}},
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
        dataset_uuid=ds.uuid,
        diagnoser_uuids=[d.uuid],
        **common,
    )
    return job, ds, [d]


def test_freezes_resolved_identity_without_secret_and_restores_it():
    profile = {**PROFILE, "api_key": "never-freeze-this"}
    snapshot = build_execution_snapshot(
        *inputs({"mode": "required"}), sandbox_profiles={"custom": profile}
    )
    assert snapshot["executor_revision"] == "1.6.0"
    assert snapshot["diagnosers"][0]["spec"]["sandbox"] == PROFILE
    profile["endpoint"] = "http://replacement"
    assert restore_execution_snapshot(snapshot)[2][0].spec["sandbox"] == PROFILE
    assert "never-freeze-this" not in str(snapshot)


def test_implicit_local_is_explicit_in_new_snapshot():
    snapshot = build_execution_snapshot(*inputs())
    assert restore_execution_snapshot(snapshot)[2][0].spec["sandbox"] == {"mode": "local"}


def test_existing_scheduled_specialist_and_new_tool_use_the_same_snapshot_revision():
    job, dataset, [base] = inputs()
    scheduled = replace(job, trigger_type=JobTriggerType.SCHEDULE, schedule_cron="0 * * * *")
    coding = replace(dataset, spec={**dataset.spec, "runtime_type": "codex"})
    framework = replace(dataset, spec={**dataset.spec, "runtime_type": "langfuse-sdk"})
    marker = get_specialist_definition("protected_content_exposure", "configured_exact_markers")
    marker_spec = specialist_spec(
        marker,
        {
            "policy": {
                "kind": "protected_content_exposure_v1",
                "scope": "final_visible_text",
                "match": "literal_exact",
                "markers": [{"id": "test", "value": "fixture", "visibility": "protected"}],
            }
        },
    )
    protected = replace(base, type=DiagnoserType.BUILTIN, version="1.0.0", spec=marker_spec)
    snapshot = build_execution_snapshot(scheduled, coding, [protected])
    assert snapshot["executor_revision"] == "1.6.0" and snapshot.get("execution_policy") is None
    assert snapshot == build_execution_snapshot(job, coding, [protected])
    assert restore_execution_snapshot(snapshot)[2][0].spec == marker_spec

    tool = get_specialist_definition("required_tool_execution", "observed_tool_completion")
    tool_spec = specialist_spec(
        tool, {"policy": {"kind": "required_tool_execution_v1", "tool_name": "retrieve_policy"}}
    )
    tool_diagnoser = replace(protected, spec=tool_spec)
    with pytest.raises(Validation, match="Specialist runtime mapping is unavailable"):
        build_execution_snapshot(scheduled, coding, [tool_diagnoser])
    snapshot = build_execution_snapshot(scheduled, framework, [tool_diagnoser])
    assert snapshot["executor_revision"] == "1.6.0" and snapshot.get("execution_policy") is None
    assert restore_execution_snapshot(snapshot)[2][0].spec == tool_spec


def test_required_or_explicit_connection_cannot_silently_freeze_local():
    for policy in ({"mode": "required"}, {"connection_uuid": "selected"}):
        with pytest.raises(Validation):
            build_execution_snapshot(*inputs(policy))


@pytest.mark.parametrize(
    "override",
    [
        {"space_uuid": "other"},
        {"protocol_version": 2},
        {"cpu": True},
        {"endpoint": "http://a/?token=x"},
    ],
)
def test_invalid_or_cross_space_profile_rejected(override):
    with pytest.raises(Validation):
        build_execution_snapshot(*inputs(), sandbox_profiles={"custom": {**PROFILE, **override}})


def test_legacy_14_retains_local_and_rejects_injected_remote_backend():
    snapshot = build_execution_snapshot(*inputs())
    snapshot["executor_revision"] = "1.4.0"
    snapshot["diagnosers"][0]["revision"] = "1.4.0"
    snapshot["diagnosers"][0]["spec"].pop("sandbox", None)
    assert restore_execution_snapshot(snapshot)[2][0].spec["sandbox"] == {"mode": "local"}
    altered = deepcopy(snapshot)
    altered["diagnosers"][0]["spec"]["sandbox"] = PROFILE
    with pytest.raises(Validation):
        restore_execution_snapshot(altered)


def test_new_frozen_local_retest_obeys_current_space_requirement(monkeypatch):
    from types import SimpleNamespace

    from agentlens.infra.ob.tables import sandbox_table
    from agentlens.service.execution_snapshot import validate_retest_sandbox_policy

    class Table:
        def __init__(self, session):
            pass

        def get_binding(self, space_uuid, *, for_update=False):
            assert space_uuid == "space" and for_update is True
            return SimpleNamespace(require_sandbox=True)

    monkeypatch.setattr(sandbox_table, "SandboxTable", Table)
    snapshot = build_execution_snapshot(*inputs())
    with pytest.raises(Validation, match="requires sandbox"):
        validate_retest_sandbox_policy(None, "space", snapshot)
    snapshot = build_execution_snapshot(*inputs(), sandbox_profiles={"custom": PROFILE})
    validate_retest_sandbox_policy(None, "space", snapshot)


def test_enhanced_material_identity_frozen_and_old_revisions_reject_it():
    import hashlib

    args = inputs()
    definition = args[2][0]
    definition.spec.update(
        rule_runtime="python-eval-v1",
        rule_runtime_manifest="a" * 64,
        materials={
            "files": [{"path": "input.json", "encoding": "utf-8", "content": "{}"}],
            "expected_json": '{"tiny":1e-7,"name":"增强"}',
        },
    )
    snapshot = build_execution_snapshot(*args, sandbox_profiles={"custom": PROFILE})
    expected = deepcopy(snapshot)
    definition.spec["materials"]["files"][0]["content"] = "changed"
    frozen = restore_execution_snapshot(snapshot)[2][0].spec
    assert frozen["materials"]["files"][0]["sha256"] == hashlib.sha256(b"{}").hexdigest()
    assert snapshot == expected
    assert frozen["materials"]["expected_json"] == '{"tiny":1e-7,"name":"增强"}'
    assert "expected" not in frozen["materials"]
    for revision in ("1.4.0", "1.5.0"):
        old = deepcopy(snapshot)
        old["executor_revision"] = old["diagnosers"][0]["revision"] = revision
        with pytest.raises(Validation, match="legacy executor revision"):
            restore_execution_snapshot(old)


def test_legacy_15_preserves_frozen_remote_profile():
    snapshot = build_execution_snapshot(*inputs(), sandbox_profiles={"custom": PROFILE})
    snapshot["executor_revision"] = snapshot["diagnosers"][0]["revision"] = "1.5.0"
    assert restore_execution_snapshot(snapshot)[2][0].spec["sandbox"] == PROFILE
