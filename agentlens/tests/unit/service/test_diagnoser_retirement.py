from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace

import pytest

from agentlens.service.diagnoser_retirement import (
    TOOL_VERSION,
    Ownership,
    Plan,
    RetirementError,
    RetirementMigration,
    digest,
    inventory,
)
from agentlens.service.diagnoser_retirement_journal import Journal, JournalError


def test_atomic_journal_rejects_symlink_and_is_private(tmp_path):
    from agentlens.service.diagnoser_retirement_journal import Journal, JournalError

    path = tmp_path / "manifest.json"
    journal = Journal(path)
    with journal.locked():
        journal.write({"schema_version": 1, "phases": []})
    assert path.stat().st_mode & 0o777 == 0o600
    assert journal.read()["phases"] == []
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(JournalError):
        Journal(link).read()


def test_default_inventory_is_read_only_and_never_syncs():
    from agentlens.service.diagnoser_retirement import inventory

    class ReadOnlyStore:
        def read(self, ownership, **kwargs):
            return {"jobs": {}, "datasets": {}, "diagnosers": {}, "tasks": {}, "models": {}}

    assert inventory(ReadOnlyStore(), None)["jobs"] == {}


JOB, DATASET, OLD, COMMON, CUSTOM, TARGET = (c * 32 for c in "abcdef")
TASK = "1" * 32
MODEL = "m" * 32
STAMP = "2026-10-10T10:10:10"


def row(uuid, **fields):
    return dict(
        uuid=uuid,
        space_uuid="space",
        agent_uuid="agent",
        gmt_create=STAMP,
        gmt_modified=STAMP,
        creator="owner",
        modifier="owner",
        **fields,
    )


class MemoryStore:
    """Offline domain fixture, not an alternative database backend."""

    def __init__(self, state):
        self.state = state
        self.commits = self.writes = self.transactions = 0
        self.commit_failure = False
        self.before_lock = None

    def read(self, _ownership, **kwargs):
        return deepcopy(self.state)

    @contextmanager
    def transaction(self):
        self.transactions += 1
        if self.before_lock:
            hook, self.before_lock = self.before_lock, None
            hook()
        saved = deepcopy(self.state)
        try:
            yield self
            if self.commit_failure:
                self.commit_failure = False
                raise RuntimeError("commit failed")
            self.commits += 1
        except BaseException:
            self.state = saved
            raise

    def set_job(self, _session, uuid, *, status, bindings, modified):
        self.writes += 1
        self.state["jobs"][uuid].update(
            status=status, diagnoser_uuids=list(bindings), gmt_modified=modified
        )


class FakePrimitive:
    def __init__(self, store):
        self.store, self.calls = store, 0
        self.fail = False

    def validate(self, _session, target, config, _state, _ownership):
        if digest(config) != target.configuration_digest:
            raise RetirementError("configuration digest mismatch")
        if target.definition_digest != "1" * 64:
            raise RetirementError("canonical digest mismatch")

    def sync(self, _session, target, config, ownership):
        self.calls += 1
        if self.fail:
            raise RuntimeError("sync conflict")
        self.store.state["diagnosers"][target.diagnoser_uuid] = row(
            target.diagnoser_uuid,
            type="builtin",
            mode="rule",
            version="1.0.0",
            spec={
                "builtin_key": target.builtin_key,
                "canonical_variant": target.canonical_variant,
                "specialist_policy": deepcopy(config["policy"]),
            },
        )
        return SimpleNamespace(diagnoser_uuid=target.diagnoser_uuid)


@pytest.fixture
def migration(tmp_path):
    state = {
        "jobs": {
            JOB: row(
                JOB,
                status="enabled",
                trigger_type="schedule",
                schedule_cron="*/5 * * * *",
                dataset_uuid=DATASET,
                diagnoser_uuids=[COMMON, OLD, CUSTOM],
                spec={"save_badcase_only": False},
            )
        },
        "datasets": {
            DATASET: row(
                DATASET,
                type="signoz",
                spec={"runtime_type": "codex", "query": {"table": "signoz_traces"}},
            )
        },
        "diagnosers": {
            OLD: row(
                OLD,
                type="builtin",
                mode="llm",
                version="old",
                spec={
                    "scenario": "legacy",
                    "executor": "legacy_default",
                    "prompt": "OLD SECRET PROMPT",
                },
            ),
            COMMON: row(
                COMMON,
                type="builtin",
                mode="rule",
                version="3.0.0",
                spec={"builtin_key": "tool_success"},
            ),
            CUSTOM: row(
                CUSTOM,
                type="custom",
                mode="llm",
                version="custom",
                spec={"prompt": "plain", "model_connection_uuid": MODEL},
            ),
        },
        "tasks": {},
        "models": {
            MODEL: row(
                MODEL,
                endpoint="https://model.invalid/v1",
                model_name="fixture-model",
                api_key="PRIVATE-MODEL-CREDENTIAL",
                status="available",
            )
        },
    }
    ownership = Ownership.model_validate(
        dict(
            schema_version=1,
            environment_sha256="0" * 64,
            authorization_ref="owned-grant/sealed",
            space_uuid="space",
            agent_uuid="agent",
            job_uuids=[JOB],
            dataset_uuids=[DATASET],
            diagnoser_uuids=[OLD, COMMON, CUSTOM, TARGET],
        )
    )
    build = dict(
        source_sha256="2" * 64,
        build_sha256="3" * 64,
        catalog_sha256="4" * 64,
        schema_sha256="5" * 64,
        evidence_ref="sealed/build",
    )
    config = {
        "policy": {
            "kind": "protected_content_exposure_v1",
            "scope": "final_visible_text",
            "match": "literal_exact",
            "markers": [
                {"id": "secret", "value": "PRIVATE-MARKER-LITERAL", "visibility": "protected"}
            ],
        }
    }
    plan = Plan.model_validate(
        dict(
            schema_version=1,
            tool_version=TOOL_VERSION,
            migration_id="9" * 32,
            ownership_sha256=digest(ownership.model_dump()),
            source_build={**build, "abi": "1.3.0"},
            target_build={**build, "abi": "1.4.0"},
            mappings=[
                {
                    "job_uuid": JOB,
                    "purpose_ref": "reviewed/protected-output",
                    "substitutions": [{"old_uuid": OLD, "target_uuid": TARGET}],
                }
            ],
            targets=[
                {
                    "diagnoser_uuid": TARGET,
                    "builtin_key": "protected_content_exposure",
                    "canonical_variant": "configured_exact_markers",
                    "version": "1.0.0",
                    "definition_digest": "1" * 64,
                    "configuration_digest": digest(config),
                    "configuration_ref": "private-config/policy",
                }
            ],
        )
    )
    store = MemoryStore(state)
    adapter = FakePrimitive(store)
    service = RetirementMigration(
        store,
        ownership,
        plan,
        Journal(tmp_path / "journal.json"),
        configuration_loader=lambda ref: deepcopy(config),
        specialist_adapter=adapter,
    )
    service.fixture_adapter = adapter
    return service


def apply_phase(migration, phase):
    preview = migration.preview(phase)
    return migration.apply(preview, expected_preview_sha256=preview["preview_sha256"], apply=True)


def task(status="running", snapshot=None):
    return row(
        TASK,
        job_uuid=JOB,
        status=status,
        worker="worker",
        scheduled_at=STAMP,
        started_at=STAMP,
        finished_at=None,
        attempt=1,
        msg="",
        stats=None,
        execution_snapshot=snapshot,
        judge_request_ledger={"private": "LEDGER"},
    )


def advance_remap(migration):
    apply_phase(migration, "pause")
    apply_phase(migration, "ready")
    apply_phase(migration, "remap")


def test_preview_and_apply_false_are_zero_writes_and_private(migration):
    initial = deepcopy(migration.store.state)
    preview = migration.preview()
    assert (
        migration.apply(preview, expected_preview_sha256=preview["preview_sha256"])["applied"]
        is False
    )
    assert inventory(migration.store, migration.ownership)["jobs"][JOB]["status"] == "enabled"
    assert migration.store.state == initial
    assert migration.store.writes == migration.store.commits == migration.fixture_adapter.calls == 0
    assert not migration.journal.path.exists()
    assert "OLD SECRET PROMPT" not in str(preview)
    assert "PRIVATE-MARKER-LITERAL" not in str(preview)


def test_legacy_plan_version_stays_in_preview_digest(migration):
    raw = migration.plan.model_dump()
    raw["tool_version"] = "legacy-retirement-v1"
    migration.plan = Plan.model_validate(raw)
    preview = migration.preview()
    unsigned = {key: value for key, value in preview.items() if key != "preview_sha256"}
    assert preview["tool_version"] == "legacy-retirement-v1"
    assert preview["plan_sha256"] == digest(migration.plan.model_dump())
    assert preview["preview_sha256"] == digest(unsigned)


@pytest.mark.parametrize(
    "version",
    [
        "other-v1",
        "legacy-retirement-v2",
        "legacy-retirement-v1\n",
        " legacy-retirement-v1",
        "../legacy-retirement-v1",
        "a" * 60 + "-retirement-v1",
        1,
    ],
)
def test_plan_rejects_unrelated_or_malformed_tool_versions(migration, version):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Plan.model_validate(migration.plan.model_dump() | {"tool_version": version})


@pytest.mark.parametrize("field", ["scenario", "runtime_type"])
def test_unknown_custom_projection_dependency_requires_review_without_rewriting(migration, field):
    migration.store.state["diagnosers"][CUSTOM]["spec"][field] = "legacy-runtime"
    before = deepcopy(migration.store.state["diagnosers"][CUSTOM])
    advance_remap(migration)
    assert migration.store.state["jobs"][JOB]["status"] == "disabled"
    assert migration.store.state["jobs"][JOB]["diagnoser_uuids"] == [COMMON, OLD, CUSTOM]
    assert migration.store.state["diagnosers"][CUSTOM] == before
    reasons = migration.journal.read()["phases"][-1]["preview"]["dispositions"][JOB]["reasons"]
    assert reasons == ["custom_dependency_manual_review"]


@pytest.mark.parametrize("runtime", ["other", "raw", " OTHER ", " RAW ", "codex", "langfuse-sdk"])
def test_supported_custom_runtime_allows_plain_prompt_migration_without_rewriting(
    migration, runtime
):
    from agentlens.service.diagnoser_retirement import custom_dependency

    definition = migration.store.state["diagnosers"][CUSTOM]
    definition["spec"]["runtime_type"] = runtime
    before = deepcopy(definition)
    assert custom_dependency(definition) == "unchanged_raw_custom"
    advance_remap(migration)
    assert migration.store.state["jobs"][JOB]["diagnoser_uuids"] == [COMMON, TARGET, CUSTOM]
    assert migration.store.state["diagnosers"][CUSTOM] == before


@pytest.mark.parametrize("runtime", ["other", "raw"])
@pytest.mark.parametrize("dependency", ["dynamic_prompt", "code", "rule", "unknown_scenario"])
def test_raw_custom_default_does_not_bypass_dependency_review(migration, runtime, dependency):
    from agentlens.service.diagnoser_retirement import custom_dependency

    definition = migration.store.state["diagnosers"][CUSTOM]
    definition["spec"]["runtime_type"] = runtime
    if dependency == "dynamic_prompt":
        definition["spec"]["prompt"] = "{{spans}}"
    elif dependency == "code":
        definition["spec"]["code"] = "opaque custom code"
    elif dependency == "rule":
        definition["mode"] = "rule"
    else:
        definition["spec"]["scenario"] = "unknown-scenario"
    before = deepcopy(definition)
    assert custom_dependency(definition) == "manual_review"
    assert definition == before


def test_atomic_remap_preserves_common_custom_order_and_every_old_fact(migration):
    before = deepcopy(migration.store.state)
    advance_remap(migration)
    assert migration.store.state["jobs"][JOB]["diagnoser_uuids"] == [COMMON, TARGET, CUSTOM]
    assert migration.store.state["jobs"][JOB]["status"] == "disabled"
    assert migration.store.state["jobs"][JOB]["schedule_cron"] == "*/5 * * * *"
    for kind in ("datasets", "tasks", "models"):
        assert migration.store.state[kind] == before[kind]
    for uuid in (OLD, COMMON, CUSTOM):
        assert migration.store.state["diagnosers"][uuid] == before["diagnosers"][uuid]
    assert migration.fixture_adapter.calls == 1
    assert "OLD SECRET PROMPT" not in migration.journal.path.read_text()
    assert "PRIVATE-MARKER-LITERAL" not in migration.journal.path.read_text()


@pytest.mark.parametrize("status", ["pending", "dispatched", "running"])
@pytest.mark.parametrize(
    "snapshot", [None, {"executor_revision": "1.3.0", "diagnosers": ["unbound-old"]}]
)
def test_all_activity_and_legacy_or_old_frozen_tasks_must_drain(migration, status, snapshot):
    migration.store.state["tasks"][TASK] = task(status, snapshot)
    migration.store.state["tasks"]["2" * 32] = {**task("pending"), "uuid": "2" * 32}
    apply_phase(migration, "pause")
    assert len(migration.wait_for_drain(timeout_sec=0)["activities"]) == 2
    with pytest.raises(RetirementError, match="all pending"):
        migration.preview("ready")
    original_task = deepcopy(migration.store.state["tasks"][TASK])
    assert migration.store.state["jobs"][JOB]["status"] == "disabled"
    assert migration.store.state["tasks"][TASK] == original_task
    assert migration.fixture_adapter.calls == 0


def test_only_observed_legal_terminal_transition_permits_ready(migration):
    migration.store.state["tasks"][TASK] = task("running", {"executor_revision": "1.3.0"})
    apply_phase(migration, "pause")
    migration.store.state["tasks"][TASK].update(
        status="succeeded", finished_at=STAMP, stats={"good": 1}
    )
    apply_phase(migration, "ready")
    assert migration.journal.read()["phases"][-1]["status"] == "committed"
    assert migration.store.state["tasks"][TASK]["stats"] == {"good": 1}


def test_task_snapshot_drift_during_drain_is_not_reinterpreted(migration):
    migration.store.state["tasks"][TASK] = task()
    apply_phase(migration, "pause")
    migration.store.state["tasks"][TASK].update(
        status="succeeded", execution_snapshot={"changed": True}
    )
    with pytest.raises(RetirementError, match="immutable"):
        migration.preview("ready")


def test_unowned_reference_or_cross_scope_is_rejected_without_writes(migration):
    migration.store.state["diagnosers"][OLD]["space_uuid"] = "foreign"
    with pytest.raises(RetirementError, match="cross-scope"):
        migration.preview()
    assert migration.store.writes == 0


@pytest.mark.parametrize("scope", ["default", "shared", "global", "0"])
def test_shared_default_scope_never_granted_by_creator_or_builtin(migration, scope):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Ownership.model_validate({**migration.ownership.model_dump(), "space_uuid": scope})


def test_hash_tamper_stops_before_journal_and_resource_writes(migration):
    preview = migration.preview()
    preview["actions"][JOB]["status"] = "enabled"
    with pytest.raises(RetirementError, match="SHA256"):
        migration.apply(preview, expected_preview_sha256=preview["preview_sha256"], apply=True)
    assert not migration.journal.path.exists() and migration.store.writes == 0


def test_trigger_race_before_job_lock_stops_pause_without_task_status_forgery(migration):
    preview = migration.preview()
    migration.store.before_lock = lambda: migration.store.state["tasks"].update(
        {TASK: task("pending")}
    )
    with pytest.raises(RetirementError, match="under Job locks"):
        migration.apply(preview, expected_preview_sha256=preview["preview_sha256"], apply=True)
    assert migration.store.state["jobs"][JOB]["status"] == "enabled"
    assert migration.store.state["tasks"][TASK]["status"] == "pending"
    assert migration.store.writes == 0


def test_unwritable_first_manifest_means_zero_db_writes(migration, monkeypatch):
    preview = migration.preview()
    monkeypatch.setattr(
        migration.journal, "write", lambda value: (_ for _ in ()).throw(JournalError("disk"))
    )
    with pytest.raises(JournalError):
        migration.apply(preview, expected_preview_sha256=preview["preview_sha256"], apply=True)
    assert migration.store.writes == migration.store.transactions == 0


def test_sync_failure_rolls_back_target_and_complete_binding_transaction(migration):
    apply_phase(migration, "pause")
    apply_phase(migration, "ready")
    migration.fixture_adapter.fail = True
    before = deepcopy(migration.store.state)
    with pytest.raises(RuntimeError, match="sync conflict"):
        apply_phase(migration, "remap")
    assert migration.store.state == before and TARGET not in migration.store.state["diagnosers"]
    assert migration.store.state["jobs"][JOB]["status"] == "disabled"


def test_commit_failure_same_hash_retry_never_changes_planned_uuid(migration):
    preview = migration.preview()
    migration.store.commit_failure = True
    with pytest.raises(RuntimeError, match="commit failed"):
        migration.apply(preview, expected_preview_sha256=preview["preview_sha256"], apply=True)
    assert migration.store.state["jobs"][JOB]["status"] == "enabled"
    result = migration.apply(preview, expected_preview_sha256=preview["preview_sha256"], apply=True)
    assert result["applied"] and migration.store.state["jobs"][JOB]["status"] == "disabled"


def test_commit_then_receipt_crash_recovers_without_double_db_mutation(migration, monkeypatch):
    preview = migration.preview()
    actual_write = migration.journal.write
    calls = 0

    def crashing_write(value):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise JournalError("receipt disk failure")
        actual_write(value)

    monkeypatch.setattr(migration.journal, "write", crashing_write)
    with pytest.raises(JournalError):
        migration.apply(preview, expected_preview_sha256=preview["preview_sha256"], apply=True)
    assert migration.store.state["jobs"][JOB]["status"] == "disabled"
    writes = migration.store.writes
    monkeypatch.setattr(migration.journal, "write", actual_write)
    result = migration.apply(preview, expected_preview_sha256=preview["preview_sha256"], apply=True)
    assert result["recovered"] and migration.store.writes == writes


def test_second_apply_is_idempotent_but_after_image_user_drift_conflicts(migration):
    preview = migration.preview()
    migration.apply(preview, expected_preview_sha256=preview["preview_sha256"], apply=True)
    writes = migration.store.writes
    migration.apply(preview, expected_preview_sha256=preview["preview_sha256"], apply=True)
    assert migration.store.writes == writes
    migration.store.state["jobs"][JOB]["schedule_cron"] = "0 * * * *"
    with pytest.raises(RetirementError, match="after-image drifted"):
        migration.apply(preview, expected_preview_sha256=preview["preview_sha256"], apply=True)


def test_unknown_custom_code_and_legacy_selector_keep_job_disabled(migration):
    migration.store.state["diagnosers"][CUSTOM]["mode"] = "rule"
    migration.store.state["diagnosers"][CUSTOM]["spec"] = {"code": "opaque custom code"}
    migration.store.state["datasets"][DATASET]["spec"]["selector"] = {"type": "darwin"}
    advance_remap(migration)
    assert migration.store.state["jobs"][JOB]["diagnoser_uuids"] == [COMMON, OLD, CUSTOM]
    assert migration.store.state["jobs"][JOB]["status"] == "disabled"
    assert migration.fixture_adapter.calls == 0
    reasons = migration.journal.read()["phases"][-1]["preview"]["dispositions"][JOB]["reasons"]
    assert reasons == ["custom_dependency_manual_review", "legacy_selector_requires_manual_review"]


def test_missing_build_proof_never_restores_original_enabled(migration):
    advance_remap(migration)
    apply_phase(migration, "resume")
    assert migration.store.state["jobs"][JOB]["status"] == "disabled"
    apply_phase(migration, "rollback_pause")
    apply_phase(migration, "rollback_restore")
    assert migration.store.state["jobs"][JOB]["diagnoser_uuids"] == [COMMON, OLD, CUSTOM]
    assert migration.store.state["jobs"][JOB]["status"] == "disabled"
    assert TARGET in migration.store.state["diagnosers"]


def test_rollback_resource_drift_stops_instead_of_overwriting_user_changes(migration):
    advance_remap(migration)
    migration.store.state["jobs"][JOB]["spec"] = {"user_change": True}
    with pytest.raises(RetirementError, match="exact migration after-image"):
        migration.preview("rollback_pause")


def test_unfrozen_production_primitive_never_calls_old_sync(migration):
    migration.specialist_adapter = None
    apply_phase(migration, "pause")
    apply_phase(migration, "ready")
    with pytest.raises(RetirementError, match="not frozen"):
        migration.preview("remap")
    assert migration.fixture_adapter.calls == 0


def test_rollback_pauses_and_drains_new_tasks_created_after_resume(migration):
    advance_remap(migration)
    migration.readiness_verifier = lambda *_: {
        JOB: {
            "ready": True,
            "reasons": [],
            "abi": "1.4.0",
            "build_sha256": "3" * 64,
            "evidence_sha256": "8" * 64,
            "materials_sha256": "7" * 64,
        }
    }
    apply_phase(migration, "resume")
    assert migration.store.state["jobs"][JOB]["status"] == "enabled"
    migration.store.state["tasks"][TASK] = task("running", {"executor_revision": "1.4.0"})
    apply_phase(migration, "rollback_pause")
    assert migration.store.state["jobs"][JOB]["status"] == "disabled"
    with pytest.raises(RetirementError, match="all pending"):
        migration.preview("rollback_restore")
    migration.store.state["tasks"][TASK].update(status="succeeded", finished_at=STAMP)
    apply_phase(migration, "rollback_restore")
    assert migration.store.state["jobs"][JOB]["diagnoser_uuids"] == [COMMON, OLD, CUSTOM]
    assert migration.store.state["jobs"][JOB]["status"] == "disabled"


def test_task_completes_between_pause_commit_and_receipt_is_observed_not_rewritten(
    migration, monkeypatch
):
    migration.store.state["tasks"][TASK] = task("running", {"executor_revision": "1.3.0"})
    actual_read = migration.store.read
    reads_after_pause_intent = 0

    def read_with_completion(ownership, **kwargs):
        nonlocal reads_after_pause_intent
        if migration.journal.path.exists():
            reads_after_pause_intent += 1
            if reads_after_pause_intent == 3:
                migration.store.state["tasks"][TASK].update(status="succeeded", finished_at=STAMP)
        return actual_read(ownership, **kwargs)

    monkeypatch.setattr(migration.store, "read", read_with_completion)
    apply_phase(migration, "pause")
    assert migration.store.state["tasks"][TASK]["status"] == "succeeded"
    apply_phase(migration, "ready")


def test_all_retired_job_retires_binding_and_remains_disabled(migration):
    migration.store.state["jobs"][JOB]["diagnoser_uuids"] = [OLD]
    raw = migration.plan.model_dump()
    raw["mappings"][0]["substitutions"][0]["target_uuid"] = None
    raw["targets"] = []
    migration.plan = Plan.model_validate(raw)
    advance_remap(migration)
    assert migration.store.state["jobs"][JOB]["diagnoser_uuids"] == []
    assert migration.store.state["jobs"][JOB]["status"] == "disabled"
    assert (
        migration.journal.read()["phases"][-1]["preview"]["dispositions"][JOB]["disposition"]
        == "retire_unbound"
    )
    apply_phase(migration, "resume")
    assert migration.store.state["jobs"][JOB]["status"] == "disabled"


def test_arbitrary_ready_boolean_does_not_enable_job(migration):
    advance_remap(migration)
    migration.readiness_verifier = lambda *_: {JOB: {"ready": True}}
    with pytest.raises(RetirementError, match="sealed"):
        migration.preview("resume")


def test_sealed_source_build_proof_restores_original_enabled_not_original_disabled(migration):
    advance_remap(migration)
    apply_phase(migration, "rollback_pause")
    migration.source_build_verifier = lambda *_: {
        JOB: {
            "ready": True,
            "abi": "1.3.0",
            "build_sha256": "3" * 64,
            "evidence_sha256": "8" * 64,
            "materials_sha256": "7" * 64,
        }
    }
    apply_phase(migration, "rollback_restore")
    assert migration.store.state["jobs"][JOB]["status"] == "enabled"


def test_original_disabled_never_resumes_even_with_target_build_evidence(migration):
    migration.store.state["jobs"][JOB]["status"] = "disabled"
    advance_remap(migration)
    migration.readiness_verifier = lambda *_: {
        JOB: {
            "ready": True,
            "reasons": [],
            "abi": "1.4.0",
            "build_sha256": "3" * 64,
            "evidence_sha256": "8" * 64,
            "materials_sha256": "7" * 64,
        }
    }
    apply_phase(migration, "resume")
    assert migration.store.state["jobs"][JOB]["status"] == "disabled"


def test_prefixed_business_uuids_fit_real_resource_grammar(migration):
    value = migration.ownership.model_dump()
    value.update(
        job_uuids=["j-example1"], dataset_uuids=["ds-example1"], diagnoser_uuids=["dg-example1"]
    )
    assert Ownership.model_validate(value).diagnoser_uuids == ["dg-example1"]


def test_committed_manifest_corruption_stops_phase_before_resource_write(migration):
    apply_phase(migration, "pause")
    value = migration.journal.read()
    value["original"]["jobs"][JOB]["status"] = "disabled"
    with migration.journal.locked():
        migration.journal.write(value)
    writes = migration.store.writes
    with pytest.raises(RetirementError, match="original"):
        migration.preview("ready")
    assert migration.store.writes == writes


def test_rollback_does_not_ignore_user_rewrites_of_old_terminal_task(migration):
    migration.store.state["tasks"][TASK] = task("succeeded", {"executor_revision": "1.3.0"})
    advance_remap(migration)
    migration.store.state["tasks"][TASK]["stats"] = {"user_rewrite": True}
    with pytest.raises(RetirementError, match="terminal Task"):
        migration.preview("rollback_pause")


def test_terminal_state_during_commit_receipt_crash_can_recover_pause(migration, monkeypatch):
    migration.store.state["tasks"][TASK] = task("running", {"executor_revision": "1.3.0"})
    preview = migration.preview()
    original_write, count = migration.journal.write, 0

    def broken_write(value):
        nonlocal count
        count += 1
        if count == 3:
            raise JournalError("receipt failure")
        original_write(value)

    monkeypatch.setattr(migration.journal, "write", broken_write)
    with pytest.raises(JournalError):
        migration.apply(preview, expected_preview_sha256=preview["preview_sha256"], apply=True)
    migration.store.state["tasks"][TASK].update(status="succeeded", finished_at=STAMP)
    monkeypatch.setattr(migration.journal, "write", original_write)
    receipt = migration.apply(
        preview, expected_preview_sha256=preview["preview_sha256"], apply=True
    )
    assert receipt["recovered"] and migration.store.state["tasks"][TASK]["status"] == "succeeded"
    apply_phase(migration, "ready")


def evidence_bundle(migration, tmp_path):
    import hashlib

    from agentlens.service.diagnoser_retirement import safe_image
    from agentlens.service.diagnoser_retirement_evidence import LocalEvidenceVerifier

    artifacts = []
    hashes = {}
    for role in ("worker_build", "source_manifest", "catalog", "schema", "materials"):
        path = tmp_path / (role + ".proof")
        payload = ("actual-owned-artifact-" + role).encode()
        path.write_bytes(payload)
        hashes[role] = hashlib.sha256(payload).hexdigest()
        artifacts.append({"role": role, "path": str(path.resolve()), "sha256": hashes[role]})
    raw_plan = migration.plan.model_dump()
    for identity in ("source_build", "target_build"):
        raw_plan[identity].update(
            source_sha256=hashes["source_manifest"],
            build_sha256=hashes["worker_build"],
            catalog_sha256=hashes["catalog"],
            schema_sha256=hashes["schema"],
        )
    migration.plan = Plan.model_validate(raw_plan)
    advance_remap(migration)
    state = migration.store.state
    image = safe_image(state)
    bundle = {
        "schema_version": 1,
        "environment_sha256": migration.ownership.environment_sha256,
        "ownership_sha256": migration.plan.ownership_sha256,
        "plan_sha256": digest(migration.plan.model_dump()),
        "abi": "1.4.0",
        "source_sha256": hashes["source_manifest"],
        "build_sha256": hashes["worker_build"],
        "catalog_sha256": hashes["catalog"],
        "schema_sha256": hashes["schema"],
        "artifacts": artifacts,
        "jobs": [
            {
                "job_uuid": JOB,
                "dataset_digest": image["datasets"][DATASET]["row_digest"],
                "binding_digest": digest(state["jobs"][JOB]["diagnoser_uuids"]),
                "configuration_digests": {TARGET: migration.plan.targets[0].configuration_digest},
                "model_digests": (
                    {MODEL: image["models"][MODEL]["row_digest"]}
                    if MODEL in image["models"]
                    else {}
                ),
                "runtime_type": "codex",
                "selector_digest": digest(None),
                "materials_ref": "sealed/actual-material-mapping",
                "materials_sha256": hashes["materials"],
            }
        ],
    }
    path = tmp_path / "readiness.json"
    with Journal(path).locked() as journal:
        journal.write(bundle)
    verifier = LocalEvidenceVerifier(path, expected_sha256=digest(bundle))
    return verifier, bundle, path


def test_actual_artifact_material_fingerprints_gate_resume(migration, tmp_path):
    verifier, _, _ = evidence_bundle(migration, tmp_path)
    migration.readiness_verifier = verifier
    apply_phase(migration, "resume")
    assert migration.store.state["jobs"][JOB]["status"] == "enabled"


def readiness_model(migration, *, defect=None):
    migration.store.state["diagnosers"][CUSTOM]["spec"]["model_connection_uuid"] = MODEL
    model = row(
        MODEL,
        endpoint="https://model.invalid/v1",
        model_name="fixture-model",
        api_key="PRIVATE-MODEL-CREDENTIAL",
        status="available",
    )
    if defect == "missing":
        migration.store.state["models"].pop(MODEL, None)
        return
    if defect == "foreign":
        model["space_uuid"] = "foreign-space"
    elif defect == "unavailable":
        model["status"] = "unavailable"
    elif defect == "endpoint":
        model["endpoint"] = "https://user:PRIVATE@model.invalid/v1"
    elif defect == "model_name":
        model["model_name"] = "   "
    elif defect == "credential":
        model["api_key"] = "   "
    migration.store.state["models"][MODEL] = model


def seal_models(migration, bundle, path, verifier, *, covered=True, source=False):
    from agentlens.service.diagnoser_retirement import safe_image

    evidence = bundle["jobs"][0]
    image = safe_image(migration.store.state)
    evidence["model_digests"] = (
        {MODEL: image["models"][MODEL]["row_digest"]}
        if covered and MODEL in image["models"]
        else {}
    )
    if source:
        bundle["abi"] = "1.3.0"
        evidence["binding_digest"] = digest([COMMON, OLD, CUSTOM])
        evidence["configuration_digests"] = {}
        verifier.source = True
    with Journal(path).locked() as journal:
        journal.write(bundle)
    verifier.expected_sha256 = digest(bundle)


@pytest.mark.parametrize(
    "defect", ["missing", "unavailable", "foreign", "endpoint", "model_name", "credential"]
)
def test_bound_llm_missing_or_invalid_model_cannot_resume(migration, tmp_path, defect):
    # Dropping CUSTOM model collection would enable this Job despite a broken binding.
    readiness_model(migration, defect=defect)
    verifier, bundle, path = evidence_bundle(migration, tmp_path)
    seal_models(migration, bundle, path, verifier, covered=False)
    migration.readiness_verifier = verifier
    with pytest.raises(RetirementError):
        apply_phase(migration, "resume")
    assert migration.store.state["jobs"][JOB]["status"] == "disabled"


@pytest.mark.parametrize("covered", [False, True])
def test_bound_llm_requires_exact_model_evidence_to_resume(migration, tmp_path, covered):
    readiness_model(migration)
    verifier, bundle, path = evidence_bundle(migration, tmp_path)
    seal_models(migration, bundle, path, verifier, covered=covered)
    migration.readiness_verifier = verifier
    if covered:
        apply_phase(migration, "resume")
        assert migration.store.state["jobs"][JOB]["status"] == "enabled"
    else:
        with pytest.raises(RetirementError):
            apply_phase(migration, "resume")
        assert migration.store.state["jobs"][JOB]["status"] == "disabled"


def test_canonical_core_advisory_status_still_requires_complete_model_proof(migration, tmp_path):
    from agentlens.diagnoser.definitions.catalog import get_core_definition

    readiness_model(migration, defect="unavailable")
    core = get_core_definition("task_completion", "text_response")
    migration.store.state["diagnosers"][CUSTOM].update(
        type="builtin",
        version=core["version"],
        spec={**core["spec"], "model_connection_uuid": MODEL},
    )
    verifier, bundle, path = evidence_bundle(migration, tmp_path)
    seal_models(migration, bundle, path, verifier)
    migration.readiness_verifier = verifier
    apply_phase(migration, "resume")
    assert migration.store.state["jobs"][JOB]["status"] == "enabled"


@pytest.mark.parametrize("defect", ["missing", "unavailable"])
def test_source_llm_dependencies_cannot_restore_enabled_without_proof(migration, tmp_path, defect):
    readiness_model(migration, defect=defect)
    migration.store.state["diagnosers"][OLD]["spec"]["model_connection_uuid"] = MODEL
    verifier, bundle, path = evidence_bundle(migration, tmp_path)
    seal_models(migration, bundle, path, verifier, covered=False, source=True)
    migration.source_build_verifier = verifier
    apply_phase(migration, "rollback_pause")
    with pytest.raises(RetirementError):
        apply_phase(migration, "rollback_restore")
    assert migration.store.state["jobs"][JOB]["status"] == "disabled"


@pytest.mark.parametrize(
    "defect", ["unavailable", "foreign", "endpoint", "model_name", "credential"]
)
def test_sealed_bound_llm_digest_does_not_authorize_unusable_connection(
    migration, tmp_path, defect
):
    readiness_model(migration, defect=defect)
    verifier, bundle, path = evidence_bundle(migration, tmp_path)
    seal_models(migration, bundle, path, verifier)
    migration.readiness_verifier = verifier
    with pytest.raises(RetirementError):
        apply_phase(migration, "resume")
    assert migration.store.state["jobs"][JOB]["status"] == "disabled"


def test_custom_cannot_claim_core_advisory_model_status(migration, tmp_path):
    from agentlens.diagnoser.definitions.catalog import get_core_definition

    readiness_model(migration, defect="unavailable")
    core = get_core_definition("task_completion", "text_response")
    migration.store.state["diagnosers"][CUSTOM]["spec"] = {
        **core["spec"],
        "prompt": "plain static CUSTOM instructions",
        "model_connection_uuid": MODEL,
    }
    verifier, bundle, path = evidence_bundle(migration, tmp_path)
    seal_models(migration, bundle, path, verifier)
    migration.readiness_verifier = verifier
    with pytest.raises(RetirementError, match="AVAILABLE"):
        apply_phase(migration, "resume")
    assert migration.store.state["jobs"][JOB]["status"] == "disabled"


def test_source_full_bound_llm_model_proof_restores_original_enabled_job(migration, tmp_path):
    readiness_model(migration)
    migration.store.state["diagnosers"][OLD]["spec"]["model_connection_uuid"] = MODEL
    verifier, bundle, path = evidence_bundle(migration, tmp_path)
    seal_models(migration, bundle, path, verifier, source=True)
    migration.source_build_verifier = verifier
    apply_phase(migration, "rollback_pause")
    apply_phase(migration, "rollback_restore")
    assert migration.store.state["jobs"][JOB]["status"] == "enabled"
    assert migration.store.state["jobs"][JOB]["diagnoser_uuids"] == [COMMON, OLD, CUSTOM]


@pytest.mark.parametrize(
    "kind", ["missing_build", "changed_build", "seal", "environment", "materials", "bindings"]
)
def test_readiness_drift_or_missing_artifacts_cannot_enable(migration, tmp_path, kind):
    verifier, bundle, path = evidence_bundle(migration, tmp_path)
    artifact = next(row for row in bundle["artifacts"] if row["role"] == "worker_build")
    if kind == "missing_build":
        from pathlib import Path

        Path(artifact["path"]).unlink()
    elif kind == "changed_build":
        from pathlib import Path

        Path(artifact["path"]).write_bytes(b"other build")
    elif kind == "seal":
        bundle["abi"] = "1.3.0"
    elif kind == "environment":
        bundle["environment_sha256"] = "f" * 64
    elif kind == "materials":
        bundle["jobs"][0]["materials_sha256"] = "f" * 64
    elif kind == "bindings":
        bundle["jobs"][0]["binding_digest"] = "f" * 64
    if kind not in {"missing_build", "changed_build"}:
        with Journal(path).locked() as journal:
            journal.write(bundle)
        if kind != "seal":
            verifier.expected_sha256 = digest(bundle)
    migration.readiness_verifier = verifier
    with pytest.raises(RetirementError):
        migration.preview("resume")
    assert migration.store.state["jobs"][JOB]["status"] == "disabled"


def test_source_archive_alone_is_not_a_restorable_worker_build(migration, tmp_path):
    verifier, bundle, path = evidence_bundle(migration, tmp_path)
    bundle["artifacts"] = [row for row in bundle["artifacts"] if row["role"] != "worker_build"]
    with Journal(path).locked() as journal:
        journal.write(bundle)
    verifier.expected_sha256 = digest(bundle)
    with pytest.raises(RetirementError, match="actual build artifact"):
        verifier(migration.store.state, migration.ownership, migration.plan)


def test_default_adapter_consumes_real_flush_only_primitive_and_keeps_planned_prefixed_uuid():
    from unittest.mock import Mock

    from agentlens.diagnoser.definitions.specialists import get_specialist_definition
    from agentlens.domain.specialist_policy import (
        configuration_digest,
        normalize_specialist_configuration,
    )
    from agentlens.service.diagnoser_retirement import FrozenSpecialistAdapter, Target

    configuration = {
        "policy": {
            "kind": "protected_content_exposure_v1",
            "scope": "final_visible_text",
            "match": "literal_exact",
            "markers": [{"id": "key", "value": "PRIVATE", "visibility": "protected"}],
        }
    }
    definition = get_specialist_definition("protected_content_exposure", "configured_exact_markers")
    normalized = normalize_specialist_configuration(
        definition["key"], definition["variant"], configuration
    )
    target = Target.model_validate(
        dict(
            diagnoser_uuid="dg-exact01",
            builtin_key=definition["key"],
            canonical_variant=definition["variant"],
            version="1.0.0",
            definition_digest=definition["definition_digest"],
            configuration_digest=configuration_digest(normalized),
            configuration_ref="owned/policy",
        )
    )
    session = Mock()
    session.scalars.return_value = []
    session.scalar.return_value = None
    ownership = Ownership.model_validate(
        dict(
            schema_version=1,
            environment_sha256="0" * 64,
            authorization_ref="grant/exact",
            space_uuid="space",
            agent_uuid="agent",
            job_uuids=["j-example"],
            dataset_uuids=["ds-example"],
            diagnoser_uuids=["dg-exact01"],
        )
    )
    adapter = FrozenSpecialistAdapter(Mock())
    adapter.validate(session, target, configuration, {}, ownership)
    result = adapter.sync(session, target, configuration, ownership)
    assert result.diagnoser_uuid == "dg-exact01" and result.action == "created"
    assert session.add.call_args.args[0].uuid == "dg-exact01"
    session.flush.assert_called_once()
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


def test_source_build_lost_after_sealing_restores_bindings_but_keeps_disabled(migration):
    from agentlens.service.diagnoser_retirement import BuildUnavailable

    advance_remap(migration)
    apply_phase(migration, "rollback_pause")

    def unavailable(*_):
        raise BuildUnavailable("old pinned worker artifact missing")

    migration.source_build_verifier = unavailable
    apply_phase(migration, "rollback_restore")
    assert migration.store.state["jobs"][JOB]["diagnoser_uuids"] == [COMMON, OLD, CUSTOM]
    assert migration.store.state["jobs"][JOB]["status"] == "disabled"


def test_preview_reports_missing_target_configuration_as_blocked_without_writes(migration):
    migration.configuration_loader = None
    preview = migration.preview()
    assert preview["dispositions"][JOB]["disposition"] == "blocked"
    assert preview["dispositions"][JOB]["reasons"] == ["target_configuration_unavailable"]
    assert preview["retired_diagnoser_dispositions"][OLD]["disposition"] == "blocked"
    assert migration.store.writes == migration.fixture_adapter.calls == 0


def test_schema_version_boolean_is_not_integer_one(migration):
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="integer"):
        Ownership.model_validate({**migration.ownership.model_dump(), "schema_version": True})
