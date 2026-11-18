from datetime import UTC, datetime, timedelta

import pytest

from agentlens.domain.enums import TaskStatus
from agentlens.domain.task import Task
from agentlens.infra.ob.tables.task_table import TaskTable
from agentlens.utils.uuid_gen import new_task_uuid


def _make(status=TaskStatus.PENDING) -> Task:
    return Task(
        uuid=new_task_uuid(),
        job_uuid="j-abcdef01",
        status=status,
        worker=None,
        scheduled_at=datetime.now(UTC),
        started_at=None,
        finished_at=None,
        attempt=0,
        msg="",
        gmt_create=None,
        gmt_modified=None,
        creator=None,
        modifier=None,
    )


def test_insert_and_get(session):
    t = TaskTable(session)
    x = _make()
    t.insert(x)
    f = t.get_by_uuid(x.uuid)
    assert f.status == TaskStatus.PENDING
    assert f.worker is None


def test_list_pending_ordered_by_scheduled_at(session):
    t = TaskTable(session)
    a = _make()
    a.scheduled_at = datetime.now(UTC) + timedelta(seconds=10)
    t.insert(a)
    b = _make()
    b.scheduled_at = datetime.now(UTC)
    t.insert(b)
    rows = t.list_pending(limit=10)
    assert [r.uuid for r in rows] == [b.uuid, a.uuid]


def test_count_assigned_by_workers_counts_running_and_dispatched(session):
    table = TaskTable(session)
    first = _make()
    table.insert(first)
    table.cas_dispatch(first.uuid, "w1/1")
    table.cas_start_running(first.uuid, "w1/1")
    second = _make()
    table.insert(second)
    table.cas_dispatch(second.uuid, "w1/1")
    table.cas_start_running(second.uuid, "w1/1")
    dispatched = _make()
    table.insert(dispatched)
    table.cas_dispatch(dispatched.uuid, "w2/2")
    pending = _make()
    pending.worker = "w1/1"
    table.insert(pending)

    for status in (TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELED):
        terminal = _make(status=status)
        terminal.worker = "w1/1"
        table.insert(terminal)
    other_worker = _make(status=TaskStatus.DISPATCHED)
    other_worker.worker = "w4/4"
    table.insert(other_worker)

    assert table.count_assigned_by_workers(["w1/1", "w2/2", "w3/3"]) == {
        "w1/1": 2,
        "w2/2": 1,
        "w3/3": 0,
    }
    assert table.count_assigned_by_workers([]) == {}


def test_cas_dispatch_only_succeeds_when_pending(session):
    t = TaskTable(session)
    x = _make()
    t.insert(x)
    assert t.cas_dispatch(x.uuid, worker="h/i") is True
    assert t.cas_dispatch(x.uuid, worker="h/i") is False  # 已 dispatched
    f = t.get_by_uuid(x.uuid)
    assert f.status == TaskStatus.DISPATCHED
    assert f.worker == "h/i"


def test_cas_start_running_bumps_attempt(session):
    t = TaskTable(session)
    x = _make()
    x.stats = {"legacy": {"partial": 1}}
    t.insert(x)
    t.cas_dispatch(x.uuid, worker="h/i")
    assert t.cas_start_running(x.uuid, worker="h/i") is True
    f = t.get_by_uuid(x.uuid)
    assert f.status == TaskStatus.RUNNING
    assert f.attempt == 1
    assert f.stats is None


def test_mark_succeeded_and_failed(session):
    t = TaskTable(session)
    x = _make()
    x.msg = "existing"
    t.insert(x)
    t.cas_dispatch(x.uuid, "h/i")
    t.cas_start_running(x.uuid, "h/i")
    t.mark_succeeded(x.uuid, worker="h/i", attempt=1, msg_append="ok n=5")
    f = t.get_by_uuid(x.uuid)
    assert f.status == TaskStatus.SUCCEEDED
    assert f.msg.startswith("existing\n[")
    assert "ok n=5" in f.msg

    y = _make()
    t.insert(y)
    t.cas_dispatch(y.uuid, "h/i")
    t.cas_start_running(y.uuid, "h/i")
    t.mark_failed(y.uuid, worker="h/i", attempt=1, msg_append="err=BOOM")
    g = t.get_by_uuid(y.uuid)
    assert g.status == TaskStatus.FAILED


def test_schedule_insert_is_idempotent(session):
    t = TaskTable(session)
    x = _make()
    x.scheduled_at = datetime(2026, 7, 23, 10, 5, 0)
    assert t.insert_for_schedule(x) is True
    duplicate = _make()
    duplicate.job_uuid = x.job_uuid
    duplicate.scheduled_at = x.scheduled_at
    assert t.insert_for_schedule(duplicate) is False
    assert len(t.list_pending(limit=10)) == 1


def test_latest_scheduled_by_jobs_uses_upper_bound(session):
    table = TaskTable(session)
    before = datetime(2026, 7, 23, 10, 10, tzinfo=UTC)
    first = _make()
    first.job_uuid = "j-first"
    first.scheduled_at = before - timedelta(minutes=5)
    table.insert(first)
    future = _make()
    future.job_uuid = "j-first"
    future.scheduled_at = before + timedelta(minutes=5)
    table.insert(future)
    second = _make()
    second.job_uuid = "j-second"
    second.scheduled_at = before
    table.insert(second)

    assert table.latest_scheduled_by_jobs(["j-first", "j-second"], before=before) == {
        "j-first": (before - timedelta(minutes=5)).replace(tzinfo=None),
        "j-second": before.replace(tzinfo=None),
    }


def test_cas_cancel_only_from_pending_and_dispatched(session):
    t = TaskTable(session)
    # PENDING → CANCELED OK
    x = _make()
    t.insert(x)
    assert t.cas_cancel(x.uuid) is True
    assert t.get_by_uuid(x.uuid).status == TaskStatus.CANCELED
    assert "canceled" in t.get_by_uuid(x.uuid).msg

    # DISPATCHED → CANCELED OK
    y = _make()
    t.insert(y)
    t.cas_dispatch(y.uuid, "h/i")
    assert t.cas_cancel(y.uuid) is True

    # RUNNING is non-preemptive and cannot be canceled.
    z = _make()
    t.insert(z)
    t.cas_dispatch(z.uuid, "h/i")
    t.cas_start_running(z.uuid, "h/i")
    assert t.cas_cancel(z.uuid) is False
    assert t.get_by_uuid(z.uuid).status == TaskStatus.RUNNING
    assert t.get_by_uuid(z.uuid).finished_at is None

    # Terminal statuses → not cancelable
    s = _make(status=TaskStatus.SUCCEEDED)
    t.insert(s)
    assert t.cas_cancel(s.uuid) is False

    # Unknown uuid → False
    assert t.cas_cancel("t-deadbeef") is False


def test_mark_succeeded_fails_if_not_running(session):
    """CAS guard: can't mark succeeded if task was already reset by rescue."""
    t = TaskTable(session)
    x = _make(status=TaskStatus.PENDING)  # not RUNNING
    t.insert(x)
    assert t.mark_succeeded(x.uuid, worker="h/i", attempt=0, msg_append="should fail") is False
    assert t.get_by_uuid(x.uuid).status == TaskStatus.PENDING


def test_mark_succeeded_rejects_wrong_worker(session):
    """Fence: stale worker cannot mark succeeded."""
    t = TaskTable(session)
    x = _make()
    t.insert(x)
    t.cas_dispatch(x.uuid, "h/i")
    t.cas_start_running(x.uuid, "h/i")
    assert t.mark_succeeded(x.uuid, worker="stale-worker", attempt=1) is False
    assert t.get_by_uuid(x.uuid).status == TaskStatus.RUNNING


def test_mark_succeeded_rejects_wrong_attempt(session):
    """Fence: old attempt cannot mark succeeded after newer attempt starts."""
    t = TaskTable(session)
    x = _make()
    t.insert(x)
    t.cas_dispatch(x.uuid, "h/i")
    t.cas_start_running(x.uuid, "h/i")
    # attempt is now 1; try with attempt 0 (stale)
    assert t.mark_succeeded(x.uuid, worker="h/i", attempt=0) is False


def test_reset_orphaned_by_worker_uses_attempt_limit(session):
    table = TaskTable(session)
    rescued = _make()
    rescued.worker = "h/i"
    rescued.status = TaskStatus.RUNNING
    rescued.attempt = 1
    rescued.stats = {"legacy": {"partial": 1}}
    table.insert(rescued)
    exhausted = _make()
    exhausted.worker = "h/i"
    exhausted.status = TaskStatus.DISPATCHED
    exhausted.attempt = 2
    exhausted.stats = {"legacy": {"partial": 2}}
    table.insert(exhausted)

    recovered_count, failed_count = table.reset_orphaned_by_worker(
        "h/i",
        max_task_attempts=2,
    )

    recovered = table.get_by_uuid(rescued.uuid)
    failed = table.get_by_uuid(exhausted.uuid)
    assert (recovered_count, failed_count) == (1, 1)
    assert recovered.status == TaskStatus.PENDING
    assert recovered.worker is None
    assert recovered.attempt == 1
    assert recovered.stats is None
    assert failed.status == TaskStatus.FAILED
    assert failed.worker == "h/i"
    assert failed.attempt == 2
    assert failed.stats is None
    assert failed.finished_at is not None


def test_delete_expired_terminal_uses_cutoff_status_and_batch_limit(session):
    table = TaskTable(session)
    now = datetime.now()

    oldest = _make(TaskStatus.SUCCEEDED)
    oldest.finished_at = now - timedelta(days=40)
    table.insert(oldest)
    second = _make(TaskStatus.FAILED)
    second.finished_at = now - timedelta(days=39)
    table.insert(second)
    third = _make(TaskStatus.CANCELED)
    third.finished_at = now - timedelta(days=38)
    table.insert(third)
    recent = _make(TaskStatus.SUCCEEDED)
    recent.finished_at = now - timedelta(days=10)
    table.insert(recent)
    active = _make(TaskStatus.RUNNING)
    active.finished_at = now - timedelta(days=40)
    table.insert(active)
    missing_finished_at = _make(TaskStatus.FAILED)
    table.insert(missing_finished_at)

    assert table.delete_expired_terminal(
        cutoff=now - timedelta(days=30),
        batch_size=2,
    ) == (2, 2)
    assert table.get_by_uuid(oldest.uuid) is None
    assert table.get_by_uuid(second.uuid) is None
    assert table.get_by_uuid(third.uuid) is not None
    assert table.get_by_uuid(recent.uuid) is not None
    assert table.get_by_uuid(active.uuid) is not None
    assert table.get_by_uuid(missing_finished_at.uuid) is not None


def test_delete_expired_terminal_excludes_cutoff_boundary(session):
    table = TaskTable(session)
    cutoff = datetime.now().replace(microsecond=0)
    boundary = _make(TaskStatus.SUCCEEDED)
    boundary.finished_at = cutoff
    table.insert(boundary)

    assert table.delete_expired_terminal(cutoff=cutoff, batch_size=1000) == (0, 0)
    assert table.get_by_uuid(boundary.uuid) is not None


# ── Task.stats ──


def test_stats_default_none(session):
    t = TaskTable(session)
    x = _make()
    t.insert(x)
    f = t.get_by_uuid(x.uuid)
    assert f.stats is None


def test_mark_succeeded_writes_stats(session):
    t = TaskTable(session)
    x = _make()
    t.insert(x)
    t.cas_dispatch(x.uuid, "h/i")
    t.cas_start_running(x.uuid, "h/i")
    stats = {"dg-aaa": {"total": 95, "good": 92, "bad": 3}}
    assert t.mark_succeeded(x.uuid, worker="h/i", attempt=1, msg_append="n=95", stats=stats) is True
    f = t.get_by_uuid(x.uuid)
    assert f.status == TaskStatus.SUCCEEDED
    assert f.stats == stats


def test_mark_failed_writes_stats(session):
    t = TaskTable(session)
    x = _make()
    x.msg = "existing"
    t.insert(x)
    t.cas_dispatch(x.uuid, "h/i")
    t.cas_start_running(x.uuid, "h/i")
    stats = {"source": {"total": 3}, "diagnosers": {}}

    assert t.mark_failed(
        x.uuid,
        worker="h/i",
        attempt=1,
        msg_append="err=BOOM",
        stats=stats,
    )

    task = t.get_by_uuid(x.uuid)
    assert task.status == TaskStatus.FAILED
    assert task.stats == stats
    assert task.msg.startswith("existing\n[")
    assert "failed err=BOOM" in task.msg


def test_task_snapshot_persists_through_retry_and_terminal_fence(session):
    from tests.unit.service.test_diagnosis_stats_service import _v3_stats

    table = TaskTable(session)
    task = _make()
    task.execution_snapshot = {"schema_version": 1, "executor_revision": "1.0.0", "diagnosers": []}
    table.insert(task)
    assert table.get_by_uuid(task.uuid).execution_snapshot == task.execution_snapshot
    assert table.cas_dispatch(task.uuid, "worker")
    assert table.cas_start_running(task.uuid, "worker")
    assert table.reset_orphaned_by_worker("worker") == (1, 0)
    assert table.get_by_uuid(task.uuid).execution_snapshot == task.execution_snapshot
    assert table.cas_dispatch(task.uuid, "worker")
    assert table.cas_start_running(task.uuid, "worker")
    assert not table.mark_succeeded(task.uuid, "worker", 1, stats=_v3_stats())
    assert table.mark_succeeded(task.uuid, "worker", 2, stats=_v3_stats())
    terminal = table.get_by_uuid(task.uuid)
    assert terminal.stats == _v3_stats()
    assert terminal.execution_snapshot == task.execution_snapshot


def test_additive_snapshot_migration_preserves_existing_task(session, engine):
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text

    task = _make()
    TaskTable(session).insert(task)
    session.expunge_all()
    session.rollback()
    path = Path("obschema/migrations/versions/009_task_execution_snapshot.py")
    spec = importlib.util.spec_from_file_location("migration009", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE agentlens_task DROP COLUMN execution_snapshot"))
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
    legacy = TaskTable(session).get_by_uuid(task.uuid)
    assert legacy.uuid == task.uuid
    assert legacy.execution_snapshot is None
    assert legacy.attempt == 0


def test_old_and_new_tasks_read_back_their_creation_definitions(session, signoz_inmem, tmp_path):
    """Persisted old/new tasks remain distinguishable after the live definition changes."""
    import json
    from dataclasses import replace

    from sqlalchemy import update

    from agentlens.infra.ob.models import TaskModel
    from agentlens.infra.ob.tables.dataset_table import DatasetTable
    from agentlens.infra.ob.tables.diagnoser_table import DiagnoserTable
    from agentlens.infra.ob.tables.job_table import JobTable
    from agentlens.infra.ob.tables.model_connection_table import ModelConnectionTable
    from agentlens.service.execution_snapshot import snapshot_definitions, snapshot_from_tables
    from tests.unit.kernel.worker.test_runner import _seed_world

    old_task = _seed_world(session, signoz_inmem)
    jobs = JobTable(session)
    datasets = DatasetTable(session)
    diagnosers = DiagnoserTable(session)
    connections = ModelConnectionTable(session)
    job = jobs.get_by_uuid(old_task.job_uuid)
    diagnoser_uuid = job.diagnoser_uuids[0]
    old_snapshot = snapshot_from_tables(job, datasets, diagnosers, connections)
    session.execute(
        update(TaskModel)
        .where(TaskModel.uuid == old_task.uuid)
        .values(execution_snapshot=old_snapshot)
    )
    session.commit()

    diagnosers.update_by_uuid(
        diagnoser_uuid,
        {
            "name": "updated-definition",
            "version": "v2",
            "spec": {
                "executor": "test_fixed",
                "weight": 3,
                "metrics": [{"name": "quality", "desc": "Updated quality description"}],
            },
        },
    )
    new_snapshot = snapshot_from_tables(job, datasets, diagnosers, connections)
    new_task = replace(old_task, uuid=new_task_uuid(), execution_snapshot=new_snapshot)
    tasks = TaskTable(session)
    tasks.insert(new_task)
    session.expunge_all()

    old_definitions = snapshot_definitions(tasks.get_by_uuid(old_task.uuid).execution_snapshot)
    new_definitions = snapshot_definitions(tasks.get_by_uuid(new_task.uuid).execution_snapshot)
    old_definition = old_definitions[diagnoser_uuid]
    new_definition = new_definitions[diagnoser_uuid]
    assert (old_definition["name"], old_definition["version"], old_definition["weight"]) == (
        "test-fixed",
        "v1",
        0,
    )
    assert (new_definition["name"], new_definition["version"], new_definition["weight"]) == (
        "updated-definition",
        "v2",
        3,
    )
    assert old_definition["metrics"] == [{"name": "quality", "desc": ""}]
    assert new_definition["metrics"][0]["desc"] == "Updated quality description"
    assert old_definition["digest"] != new_definition["digest"]
    # Review evidence contains public definition metadata only, never the execution spec.
    (tmp_path / "task-definition-readback.json").write_text(
        json.dumps(
            {
                "old_task": {"uuid": old_task.uuid, "diagnoser_definitions": old_definitions},
                "new_task": {"uuid": new_task.uuid, "diagnoser_definitions": new_definitions},
            },
            indent=2,
        )
    )


@pytest.mark.parametrize("new_connection", [False, True])
@pytest.mark.parametrize("isolation_level", ["REPEATABLE READ", "READ COMMITTED"])
def test_snapshot_reads_current_configuration_after_repeatable_read_view(
    session, engine, signoz_inmem, new_connection, isolation_level
):
    import json

    from sqlalchemy import select, update
    from sqlalchemy.exc import OperationalError

    from agentlens.domain.enums import DiagnoserMode, DiagnoserType, ModelConnectionStatus
    from agentlens.domain.model_connection import ModelConnection
    from agentlens.infra.ob.engine import make_session_factory
    from agentlens.infra.ob.models import DatasetModel, DiagnoserModel, ModelConnectionModel
    from agentlens.infra.ob.tables.dataset_table import DatasetTable
    from agentlens.infra.ob.tables.diagnoser_table import DiagnoserTable
    from agentlens.infra.ob.tables.job_table import JobTable
    from agentlens.infra.ob.tables.model_connection_table import ModelConnectionTable
    from agentlens.service.execution_snapshot import snapshot_from_tables
    from tests.unit.kernel.worker.test_runner import _seed_world

    task = _seed_world(session, signoz_inmem)
    job = JobTable(session).get_by_uuid(task.job_uuid)
    diagnoser_uuid = job.diagnoser_uuids[0]
    llm_spec = {
        "model_connection_uuid": "mc-old",
        "prompt": "judge",
        "metrics": [{"name": "quality"}],
    }
    DiagnoserTable(session).update_by_uuid(
        diagnoser_uuid,
        {"type": DiagnoserType.CUSTOM.value, "mode": DiagnoserMode.LLM.value, "spec": llm_spec},
    )
    ModelConnectionTable(session).insert(
        ModelConnection(
            uuid="mc-old",
            space_uuid="s",
            name="old",
            endpoint="https://old.example/v1",
            model_name="old-model",
            api_key="private-test-key",
            status=ModelConnectionStatus.AVAILABLE,
            gmt_create=datetime.now(),
            gmt_modified=datetime.now(),
            creator=None,
            modifier=None,
        )
    )
    session.connection(execution_options={"isolation_level": isolation_level})
    # Establish an old consistent read view and retain ORM identities, so both
    # database current-read and populate_existing semantics are observable.
    held_model = session.scalar(
        select(ModelConnectionModel).where(ModelConnectionModel.uuid == "mc-old")
    )
    held_diagnoser = session.scalar(
        select(DiagnoserModel).where(DiagnoserModel.uuid == diagnoser_uuid)
    )
    held_dataset = session.scalar(select(DatasetModel).where(DatasetModel.uuid == job.dataset_uuid))
    target_uuid = "mc-new" if new_connection else "mc-old"
    with make_session_factory(engine)() as writer:
        if new_connection:
            writer.add(
                ModelConnectionModel(
                    uuid=target_uuid,
                    space_uuid="s",
                    name="new",
                    endpoint="https://current.example/v1",
                    model_name="current-model",
                    api_key="private-test-key",
                    status=ModelConnectionStatus.AVAILABLE.value,
                )
            )
        else:
            writer.execute(
                update(ModelConnectionModel)
                .where(ModelConnectionModel.uuid == target_uuid)
                .values(endpoint="https://current.example/v1", model_name="current-model")
            )
        writer.execute(
            update(DiagnoserModel)
            .where(DiagnoserModel.uuid == diagnoser_uuid)
            .values(version="v2", spec={**llm_spec, "model_connection_uuid": target_uuid})
        )
        writer.execute(
            update(DatasetModel)
            .where(DatasetModel.uuid == job.dataset_uuid)
            .values(spec={"runtime_type": "custom-runtime", "query": {"table": "current-trace"}})
        )
        writer.commit()

    connections = ModelConnectionTable(session)
    ordinary = connections.get_in_space(target_uuid, "s")
    if new_connection and isolation_level == "REPEATABLE READ":
        assert ordinary is None  # the new row is invisible to the original RR view
    elif new_connection:
        assert ordinary.model_name == "current-model"
    else:
        assert ordinary.model_name == "old-model"
    try:
        snapshot = snapshot_from_tables(
            job, DatasetTable(session), DiagnoserTable(session), connections
        )
    except OperationalError as exc:
        # OceanBase RR rejects a changed row instead of returning a MySQL-style
        # current read. No partial Task may be inserted; fresh reads must agree.
        assert isolation_level == "REPEATABLE READ"
        assert exc.orig.args[0] == 6235
        session.rollback()
        assert TaskTable(session).get_by_uuid(task.uuid).execution_snapshot is None
        assert len(TaskTable(session).list_tasks(job_uuid=job.uuid)) == 1
        snapshot = snapshot_from_tables(
            job, DatasetTable(session), DiagnoserTable(session), connections
        )
    definition = snapshot["diagnosers"][0]
    assert definition["version"] == "v2"
    assert definition["model_connection"] == {
        "uuid": target_uuid,
        "endpoint": "https://current.example/v1",
        "model_name": "current-model",
    }
    assert snapshot["dataset"]["spec"]["query"]["table"] == "current-trace"
    assert held_diagnoser.version == "v2"
    assert held_dataset.spec["query"]["table"] == "current-trace"
    if not new_connection:
        assert held_model.model_name == "current-model"
    assert connections.get_in_space_for_update(target_uuid, "other-space") is None
    assert "private-test-key" not in json.dumps(snapshot)
