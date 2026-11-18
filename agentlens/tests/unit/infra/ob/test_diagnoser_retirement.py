"""Real owned OceanBase/seekdb phase transactions; absent explicit DB config skips.

These tests use the repository's isolated engine/session fixture. They never
start services, use SQLite, or infer production ownership from names/creators.
"""

import hashlib
import json
from copy import deepcopy
from datetime import datetime

import pytest
from sqlalchemy import select

from agentlens.diagnoser.definitions.specialists import get_specialist_definition
from agentlens.domain.specialist_policy import (
    configuration_digest,
    normalize_specialist_configuration,
)
from agentlens.infra.ob.engine import make_session_factory
from agentlens.infra.ob.models import (
    DatasetModel,
    DiagnoserModel,
    JobModel,
    ModelConnectionModel,
    TaskModel,
)
from agentlens.service.diagnoser_retirement import (
    TOOL_VERSION,
    FrozenSpecialistAdapter,
    Ownership,
    Plan,
    RetirementError,
    RetirementMigration,
    RetirementStore,
    digest,
    safe_image,
)
from agentlens.service.diagnoser_retirement_evidence import LocalEvidenceVerifier
from agentlens.service.diagnoser_retirement_journal import Journal, JournalError
from agentlens.utils.uuid_gen import (
    new_dataset_uuid,
    new_diagnoser_uuid,
    new_job_uuid,
    new_model_connection_uuid,
    new_task_uuid,
)


@pytest.fixture
def owned_migration(engine, session, tmp_path):
    factory = make_session_factory(engine)
    old, common, custom, target = [new_diagnoser_uuid() for _ in range(4)]
    dataset, job = new_dataset_uuid(), new_job_uuid()
    now = datetime.now().replace(microsecond=0)
    shared = dict(
        space_uuid="retirement-space",
        agent_uuid="retirement-agent",
        gmt_create=now,
        gmt_modified=now,
        creator=None,
        modifier=None,
    )
    session.add_all(
        [
            DiagnoserModel(
                uuid=old,
                name="retired-original",
                description="preserved",
                type="builtin",
                mode="llm",
                spec={
                    "scenario": "legacy",
                    "executor": "legacy_default",
                    "prompt": "PRIVATE-OLD-PROMPT",
                },
                version="legacy",
                **shared,
            ),
            DiagnoserModel(
                uuid=common,
                name="common-original",
                type="builtin",
                mode="rule",
                spec={"builtin_key": "tool_success"},
                version="3.0.0",
                **shared,
            ),
            DiagnoserModel(
                uuid=custom,
                name="custom-original",
                type="custom",
                mode="llm",
                spec={"prompt": "plain raw custom"},
                version="custom",
                **shared,
            ),
            DatasetModel(
                uuid=dataset,
                name="source-original",
                type="signoz",
                spec={"runtime_type": "codex", "query": {"table": "signoz_traces"}},
                **shared,
            ),
            JobModel(
                uuid=job,
                name="owned-original",
                trigger_type="schedule",
                schedule_cron="*/5 * * * *",
                status="enabled",
                dataset_uuid=dataset,
                diagnoser_uuids=[common, old, custom],
                spec={"save_badcase_only": False},
                **shared,
            ),
        ]
    )
    session.commit()
    environment = digest(
        {
            "backend": engine.url.drivername,
            "host": engine.url.host,
            "port": engine.url.port,
            "database": engine.url.database,
            "test_schema": "isolated-Base-metadata",
        }
    )
    ownership = Ownership.model_validate(
        dict(
            schema_version=1,
            environment_sha256=environment,
            authorization_ref="isolated/owned-fixture",
            space_uuid=shared["space_uuid"],
            agent_uuid=shared["agent_uuid"],
            job_uuids=[job],
            dataset_uuids=[dataset],
            diagnoser_uuids=[old, common, custom, target],
        )
    )
    config = {
        "policy": {
            "kind": "protected_content_exposure_v1",
            "scope": "final_visible_text",
            "match": "literal_exact",
            "markers": [
                {"id": "credential", "value": "PRIVATE-TARGET-MARKER", "visibility": "protected"}
            ],
        }
    }
    normalized = normalize_specialist_configuration(
        "protected_content_exposure", "configured_exact_markers", config
    )
    definition = get_specialist_definition("protected_content_exposure", "configured_exact_markers")
    build = dict(
        source_sha256="2" * 64,
        build_sha256="3" * 64,
        catalog_sha256="4" * 64,
        schema_sha256="5" * 64,
        evidence_ref="isolated/build-pending",
    )
    plan = Plan.model_validate(
        dict(
            schema_version=1,
            tool_version=TOOL_VERSION,
            migration_id="migration-" + job,
            ownership_sha256=digest(ownership.model_dump()),
            source_build={**build, "abi": "1.3.0"},
            target_build={**build, "abi": "1.4.0"},
            mappings=[
                {
                    "job_uuid": job,
                    "purpose_ref": "fixture/exact-protected-output",
                    "substitutions": [{"old_uuid": old, "target_uuid": target}],
                }
            ],
            targets=[
                {
                    "diagnoser_uuid": target,
                    "builtin_key": definition["key"],
                    "canonical_variant": definition["variant"],
                    "version": definition["version"],
                    "definition_digest": definition["definition_digest"],
                    "configuration_digest": configuration_digest(normalized),
                    "configuration_ref": "private/configuration",
                }
            ],
        )
    )
    store = RetirementStore(factory, environment_sha256=environment)
    service = RetirementMigration(
        store,
        ownership,
        plan,
        Journal(tmp_path / "manifest.json"),
        configuration_loader=lambda _: deepcopy(config),
        specialist_adapter=FrozenSpecialistAdapter(factory),
    )
    yield (
        service,
        factory,
        dict(old=old, common=common, custom=custom, target=target, dataset=dataset, job=job),
    )


def apply(service, phase):
    preview = service.preview(phase)
    return service.apply(preview, expected_preview_sha256=preview["preview_sha256"], apply=True)


def remap(service):
    apply(service, "pause")
    apply(service, "ready")
    apply(service, "remap")


def configure_bound_model(factory, ids, *, defect, source=False):
    """Actual DB dependencies; .invalid credentials are never used for requests."""
    model_uuid = new_model_connection_uuid()
    with factory() as session:
        with session.begin():
            custom = session.scalar(
                select(DiagnoserModel).where(DiagnoserModel.uuid == ids["custom"])
            )
            spec = {**custom.spec, "model_connection_uuid": model_uuid}
            if defect == "core_advisory":
                from agentlens.diagnoser.definitions.catalog import get_core_definition

                core = get_core_definition("task_completion", "text_response")
                spec = {**core["spec"], "model_connection_uuid": model_uuid}
                custom.type, custom.version = "builtin", core["version"]
            custom.spec = spec
            if source:
                old = session.scalar(
                    select(DiagnoserModel).where(DiagnoserModel.uuid == ids["old"])
                )
                old.spec = {**old.spec, "model_connection_uuid": model_uuid}
            if defect != "missing":
                session.add(
                    ModelConnectionModel(
                        uuid=model_uuid,
                        space_uuid="foreign-space" if defect == "foreign" else custom.space_uuid,
                        name="fixture-model-" + model_uuid,
                        endpoint="https://user:PRIVATE@model.invalid/v1"
                        if defect == "endpoint"
                        else "https://model.invalid/v1",
                        model_name="   " if defect == "model_name" else "fixture-model",
                        api_key="   " if defect == "credential" else "PRIVATE-MODEL-CREDENTIAL",
                        status="unavailable"
                        if defect in {"unavailable", "core_advisory"}
                        else "available",
                        gmt_create=datetime.now(),
                        gmt_modified=datetime.now(),
                        creator=None,
                        modifier=None,
                    )
                )
    return model_uuid


def sealed_db_readiness(service, ids, tmp_path, model_uuid, *, covered=True, source=False):
    """Local proof fixture exercises DB gates; it is not a worker/provider attestation."""
    artifacts, hashes = [], {}
    for role in ("worker_build", "source_manifest", "catalog", "schema", "materials"):
        path = tmp_path / (role + ".proof")
        path.write_bytes(("controlled-offline-fixture-" + role).encode())
        hashes[role] = hashlib.sha256(path.read_bytes()).hexdigest()
        artifacts.append(dict(role=role, path=str(path.resolve()), sha256=hashes[role]))
    raw_plan = service.plan.model_dump()
    for build in ("source_build", "target_build"):
        raw_plan[build].update(
            source_sha256=hashes["source_manifest"],
            build_sha256=hashes["worker_build"],
            catalog_sha256=hashes["catalog"],
            schema_sha256=hashes["schema"],
        )
    service.plan = Plan.model_validate(raw_plan)
    remap(service)
    state = service.store.read(service.ownership)
    image = safe_image(state)
    bindings = (
        [ids["common"], ids["old"], ids["custom"]]
        if source
        else [ids["common"], ids["target"], ids["custom"]]
    )
    bundle = dict(
        schema_version=1,
        environment_sha256=service.ownership.environment_sha256,
        ownership_sha256=service.plan.ownership_sha256,
        plan_sha256=digest(service.plan.model_dump()),
        abi="1.3.0" if source else "1.4.0",
        source_sha256=hashes["source_manifest"],
        build_sha256=hashes["worker_build"],
        catalog_sha256=hashes["catalog"],
        schema_sha256=hashes["schema"],
        artifacts=artifacts,
        jobs=[
            dict(
                job_uuid=ids["job"],
                dataset_digest=image["datasets"][ids["dataset"]]["row_digest"],
                binding_digest=digest(bindings),
                configuration_digests={}
                if source
                else {ids["target"]: service.plan.targets[0].configuration_digest},
                model_digests={model_uuid: image["models"][model_uuid]["row_digest"]}
                if covered and model_uuid in image["models"]
                else {},
                runtime_type="codex",
                selector_digest=digest(None),
                materials_ref="fixture/materials",
                materials_sha256=hashes["materials"],
            )
        ],
    )
    path = tmp_path / "readiness.json"
    with Journal(path).locked() as journal:
        journal.write(bundle)
    return LocalEvidenceVerifier(path, expected_sha256=digest(bundle), source=source)


@pytest.mark.parametrize(
    "defect",
    [
        "missing",
        "unavailable",
        "foreign",
        "credential",
        "model_name",
        "endpoint",
        "coverage",
        "valid",
        "core_advisory",
    ],
)
def test_real_db_all_bound_llm_models_gate_resume(owned_migration, tmp_path, defect):
    service, factory, ids = owned_migration
    model_uuid = configure_bound_model(factory, ids, defect=defect)
    original = service.store.read(service.ownership)
    service.readiness_verifier = sealed_db_readiness(
        service, ids, tmp_path, model_uuid, covered=defect != "coverage"
    )
    if defect in {"valid", "core_advisory"}:
        apply(service, "resume")
        expected_status = "enabled"
    else:
        with pytest.raises(RetirementError):
            apply(service, "resume")
        expected_status = "disabled"
    fresh = service.store.read(service.ownership)
    assert fresh["jobs"][ids["job"]]["status"] == expected_status
    assert fresh["jobs"][ids["job"]]["diagnoser_uuids"] == [
        ids["common"],
        ids["target"],
        ids["custom"],
    ]
    for kind in ("models", "tasks", "datasets"):
        assert fresh[kind] == original[kind]
    for uuid in (ids["old"], ids["common"], ids["custom"]):
        assert fresh["diagnosers"][uuid] == original["diagnosers"][uuid]
    assert "PRIVATE-MODEL-CREDENTIAL" not in service.journal.path.read_text()


@pytest.mark.parametrize("defect", ["unavailable", "valid"])
def test_real_db_source_rollback_requires_all_old_llm_dependencies(
    owned_migration, tmp_path, defect
):
    service, factory, ids = owned_migration
    model_uuid = configure_bound_model(factory, ids, defect=defect, source=True)
    original = service.store.read(service.ownership)
    service.source_build_verifier = sealed_db_readiness(
        service, ids, tmp_path, model_uuid, source=True
    )
    apply(service, "rollback_pause")
    if defect == "valid":
        apply(service, "rollback_restore")
        expected_bindings = [ids["common"], ids["old"], ids["custom"]]
        expected_status = "enabled"
    else:
        with pytest.raises(RetirementError):
            apply(service, "rollback_restore")
        expected_bindings = [ids["common"], ids["target"], ids["custom"]]
        expected_status = "disabled"
    fresh = service.store.read(service.ownership)
    assert fresh["jobs"][ids["job"]]["status"] == expected_status
    assert fresh["jobs"][ids["job"]]["diagnoser_uuids"] == expected_bindings
    for kind in ("models", "tasks", "datasets"):
        assert fresh[kind] == original[kind]
    for uuid in (ids["old"], ids["common"], ids["custom"]):
        assert fresh["diagnosers"][uuid] == original["diagnosers"][uuid]


def test_real_db_default_inventory_preview_are_zero_resource_writes(owned_migration):
    service, _, ids = owned_migration
    before = service.store.read(service.ownership)
    preview = service.preview()
    assert (
        service.apply(preview, expected_preview_sha256=preview["preview_sha256"])["applied"]
        is False
    )
    assert service.store.read(service.ownership) == before
    assert not service.journal.path.exists()
    assert ids["target"] not in before["diagnosers"]


@pytest.mark.parametrize("status", ["pending", "dispatched", "running"])
@pytest.mark.parametrize(
    "snapshot", [None, {"executor_revision": "1.3.0", "diagnosers": [{"uuid": "unbound-old"}]}]
)
def test_real_db_all_active_and_legacy_frozen_tasks_block_remap(owned_migration, status, snapshot):
    service, factory, ids = owned_migration
    task_uuid, second = new_task_uuid(), new_task_uuid()
    with factory() as session:
        for uuid in (task_uuid, second):
            session.add(
                TaskModel(
                    uuid=uuid,
                    job_uuid=ids["job"],
                    status=status,
                    scheduled_at=datetime.now(),
                    worker="original-worker",
                    attempt=1,
                    msg="original",
                    execution_snapshot=snapshot,
                    judge_request_ledger={"attempts": ["unchanged"]},
                )
            )
        session.commit()
    original = service.store.read(service.ownership)["tasks"]
    apply(service, "pause")
    drain = service.wait_for_drain(timeout_sec=0)
    assert drain["drained"] is False and len(drain["activities"]) == 2
    with pytest.raises(RetirementError, match="all pending"):
        service.preview("ready")
    after = service.store.read(service.ownership)
    assert after["jobs"][ids["job"]]["status"] == "disabled"
    assert after["tasks"] == original and ids["target"] not in after["diagnosers"]


def test_real_db_flush_sync_and_mixed_binding_have_one_transaction_and_fresh_receipt(
    owned_migration,
):
    service, _, ids = owned_migration
    before = service.store.read(service.ownership)
    remap(service)
    after = service.store.read(service.ownership)
    assert after["jobs"][ids["job"]]["diagnoser_uuids"] == [
        ids["common"],
        ids["target"],
        ids["custom"],
    ]
    assert after["jobs"][ids["job"]]["status"] == "disabled"
    assert after["jobs"][ids["job"]]["schedule_cron"] == "*/5 * * * *"
    for key in ("datasets", "tasks", "models"):
        assert after[key] == before[key]
    for uuid in (ids["old"], ids["common"], ids["custom"]):
        assert after["diagnosers"][uuid] == before["diagnosers"][uuid]
    manifest = service.journal.read()
    assert manifest["phases"][-1]["status"] == "committed"
    assert "PRIVATE-OLD-PROMPT" not in json.dumps(manifest)
    assert "PRIVATE-TARGET-MARKER" not in json.dumps(manifest)


def test_real_db_remap_failure_after_flush_rolls_back_definition_and_binding_together(
    owned_migration, monkeypatch
):
    service, _, ids = owned_migration
    apply(service, "pause")
    apply(service, "ready")
    old_set_job = service.store.set_job

    def fail_after_flush(*args, **kwargs):
        old_set_job(*args, **kwargs)
        raise RuntimeError("fault after both target flush and binding flush")

    monkeypatch.setattr(service.store, "set_job", fail_after_flush)
    before = service.store.read(service.ownership)
    with pytest.raises(RuntimeError, match="fault after"):
        apply(service, "remap")
    after = service.store.read(service.ownership)
    assert after == before and ids["target"] not in after["diagnosers"]
    assert after["jobs"][ids["job"]]["status"] == "disabled"


def test_real_db_commit_receipt_crash_and_double_apply_use_exact_after_image(
    owned_migration, monkeypatch
):
    service, _, ids = owned_migration
    preview = service.preview()
    old_write, count = service.journal.write, 0

    def fail_receipt(value):
        nonlocal count
        count += 1
        if count == 3:
            raise JournalError("receipt unavailable")
        old_write(value)

    monkeypatch.setattr(service.journal, "write", fail_receipt)
    with pytest.raises(JournalError):
        service.apply(preview, expected_preview_sha256=preview["preview_sha256"], apply=True)
    assert service.store.read(service.ownership)["jobs"][ids["job"]]["status"] == "disabled"
    monkeypatch.setattr(service.journal, "write", old_write)
    first = service.apply(preview, expected_preview_sha256=preview["preview_sha256"], apply=True)
    second = service.apply(preview, expected_preview_sha256=preview["preview_sha256"], apply=True)
    assert first["recovered"] and second["recovered"]
    assert first["receipt_sha256"] == second["receipt_sha256"]


def test_real_db_rollback_without_source_build_restores_order_but_stays_disabled_and_keeps_history(
    owned_migration,
):
    service, factory, ids = owned_migration
    task_uuid = new_task_uuid()
    with factory() as session:
        session.add(
            TaskModel(
                uuid=task_uuid,
                job_uuid=ids["job"],
                status="succeeded",
                worker="original-worker",
                scheduled_at=datetime.now(),
                attempt=1,
                finished_at=datetime.now(),
                msg="historical",
                stats={"quality": 50},
                execution_snapshot={
                    "executor_revision": "1.3.0",
                    "diagnosers": [{"uuid": ids["old"]}],
                },
                judge_request_ledger={"attempts": [{"status": "confirmed"}]},
            )
        )
        session.commit()
    before = service.store.read(service.ownership)
    remap(service)
    apply(service, "resume")
    assert service.store.read(service.ownership)["jobs"][ids["job"]]["status"] == "disabled"
    apply(service, "rollback_pause")
    apply(service, "rollback_restore")
    after = service.store.read(service.ownership)
    assert (
        after["jobs"][ids["job"]]["diagnoser_uuids"]
        == before["jobs"][ids["job"]]["diagnoser_uuids"]
    )
    assert after["jobs"][ids["job"]]["status"] == "disabled"
    assert after["tasks"] == before["tasks"]
    assert after["diagnosers"][ids["old"]] == before["diagnosers"][ids["old"]]
    assert ids["target"] in after["diagnosers"]


def test_real_db_user_after_image_drift_blocks_rollback_without_overwrite(owned_migration):
    service, factory, ids = owned_migration
    remap(service)
    with factory() as session:
        job = session.scalar(select(JobModel).where(JobModel.uuid == ids["job"]))
        job.schedule_cron = "0 * * * *"
        session.commit()
    with pytest.raises(RetirementError, match="exact migration after-image"):
        service.preview("rollback_pause")
    assert service.store.read(service.ownership)["jobs"][ids["job"]]["schedule_cron"] == "0 * * * *"


def test_real_db_cross_scope_allowlist_is_not_authorization(owned_migration):
    service, factory, ids = owned_migration
    with factory() as session:
        row = session.scalar(select(DiagnoserModel).where(DiagnoserModel.uuid == ids["old"]))
        row.agent_uuid = "foreign-agent"
        session.commit()
    with pytest.raises(RetirementError, match="cross-scope"):
        service.preview()
    with factory() as session:
        assert (
            session.scalar(select(JobModel).where(JobModel.uuid == ids["job"])).status == "enabled"
        )
    assert not service.journal.path.exists()


def test_real_db_custom_code_legacy_selector_are_manual_and_remain_disabled(owned_migration):
    service, factory, ids = owned_migration
    with factory() as session:
        custom = session.scalar(select(DiagnoserModel).where(DiagnoserModel.uuid == ids["custom"]))
        custom.mode, custom.spec = "rule", {"code": "opaque custom dependency"}
        dataset = session.scalar(select(DatasetModel).where(DatasetModel.uuid == ids["dataset"]))
        dataset.spec = {**dataset.spec, "selector": {"type": "darwin"}}
        session.commit()
    before = service.store.read(service.ownership)
    remap(service)
    after = service.store.read(service.ownership)
    assert after["jobs"][ids["job"]]["status"] == "disabled"
    assert (
        after["jobs"][ids["job"]]["diagnoser_uuids"]
        == before["jobs"][ids["job"]]["diagnoser_uuids"]
    )
    assert after["datasets"] == before["datasets"] and after["diagnosers"] == before["diagnosers"]


def test_real_db_after_ready_user_drift_blocks_remap_without_publishing_target(owned_migration):
    service, factory, ids = owned_migration
    apply(service, "pause")
    apply(service, "ready")
    preview = service.preview("remap")
    with factory() as session:
        row = session.scalar(select(JobModel).where(JobModel.uuid == ids["job"]))
        row.spec = {"user_change": True}
        session.commit()
    with pytest.raises(RetirementError, match="after-image drifted"):
        service.apply(preview, expected_preview_sha256=preview["preview_sha256"], apply=True)
    after = service.store.read(service.ownership)
    assert ids["target"] not in after["diagnosers"]
    assert after["jobs"][ids["job"]]["spec"] == {"user_change": True}


@pytest.mark.parametrize("contender", ["trigger", "schedule"])
def test_real_db_pause_and_trigger_schedule_share_lock_and_fresh_status(
    owned_migration, monkeypatch, contender
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from agentlens.domain.enums import JobStatus
    from agentlens.infra.ob.tables.job_table import JobTable

    service, factory, ids = owned_migration
    pause_locked, release_pause, contender_started, contender_finished = (Event() for _ in range(4))
    actual_set_job = service.store.set_job

    def pause_under_lock(*args, **kwargs):
        actual_set_job(*args, **kwargs)
        pause_locked.set()
        assert release_pause.wait(10), "test must release owned pause transaction"

    monkeypatch.setattr(service.store, "set_job", pause_under_lock)
    preview = service.preview()

    def pause_writer():
        return service.apply(preview, expected_preview_sha256=preview["preview_sha256"], apply=True)

    def competing_reader():
        contender_started.set()
        with factory() as session:
            with session.begin():
                # This is the actual shared table lock used by both trigger and scanner.
                job = JobTable(session).get_by_uuid_for_update(ids["job"])
                contender_finished.set()
                if job.status == JobStatus.ENABLED:
                    session.add(
                        TaskModel(
                            uuid=new_task_uuid(),
                            job_uuid=ids["job"],
                            status="pending",
                            scheduled_at=datetime.now(),
                            attempt=0,
                            msg=contender,
                        )
                    )
                return job.status

    with ThreadPoolExecutor(max_workers=2) as pool:
        paused = pool.submit(pause_writer)
        try:
            assert pause_locked.wait(10)
            competing = pool.submit(competing_reader)
            assert contender_started.wait(10)
            assert not contender_finished.wait(0.2)
        finally:
            release_pause.set()
        assert paused.result(timeout=10)["applied"]
        assert competing.result(timeout=10) == JobStatus.DISABLED
    after = service.store.read(service.ownership)
    assert after["tasks"] == {} and after["jobs"][ids["job"]]["status"] == "disabled"
