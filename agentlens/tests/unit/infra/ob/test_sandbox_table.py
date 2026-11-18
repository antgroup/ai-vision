"""Repository behavior against the explicitly configured disposable OceanBase/seekdb."""

from datetime import datetime

import pytest
from sqlalchemy import delete, select

from agentlens.domain.errors import Conflict, NotFound
from agentlens.domain.sandbox import SandboxBinding, SandboxConnection, SandboxError, SandboxStatus
from agentlens.infra.ob.models import SandboxBindingModel, SandboxConnectionModel, SandboxLeaseModel
from agentlens.infra.ob.tables.sandbox_table import SandboxTable
from agentlens.service.sandbox import resolve_sandbox_credentials, resolve_sandbox_profile


def test_enrollment_enable_waits_for_policy_then_rejects_concurrent_key_change(session, engine):
    from concurrent.futures import ThreadPoolExecutor, TimeoutError
    from threading import Event
    from uuid import NAMESPACE_URL, uuid5

    from sqlalchemy import update
    from sqlalchemy.orm import sessionmaker

    key = "a" * 16
    space = "enrollment-lock-space"
    uuid = uuid5(NAMESPACE_URL, f"aivision:sandbox:{space}:{key}").hex
    now = datetime.now()
    connection = SandboxConnection(
        uuid=uuid,
        space_uuid=space,
        name=f"OpenShell ({key})",
        provider="openshell",
        endpoint=f"http://aiv-openshell-{key}:8091",
        api_key="original-owned-key",
        image="registry.example/worker@sha256:" + "b" * 64,
        cpu=1,
        memory="512Mi",
        timeout_seconds=5,
        config_revision=1,
        status=SandboxStatus.READY,
        gmt_create=now,
        gmt_modified=now,
    )
    sf = sessionmaker(engine, expire_on_commit=False)
    started = Event()
    table = SandboxTable(session)

    def enable_verified():
        with sf() as concurrent:
            started.set()
            return SandboxTable(concurrent).enable_verified_enrollment(connection, "operator")

    try:
        table.put_enrollment(connection)
        table.get_binding(space, for_update=True)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(enable_verified)
            assert started.wait(5)
            try:
                with pytest.raises(TimeoutError):
                    future.result(timeout=0.2)
            finally:
                session.execute(
                    update(SandboxConnectionModel)
                    .where(SandboxConnectionModel.uuid == uuid)
                    .values(api_key="rotated-owned-key")
                )
                session.commit()
            with pytest.raises(Conflict):
                future.result(timeout=10)
        session.expire_all()
        assert table.get_binding(space).connection_uuid is None
        assert table.get_in_space(uuid, space).api_key == "rotated-owned-key"
    finally:
        session.rollback()
        session.execute(delete(SandboxBindingModel).where(SandboxBindingModel.space_uuid == space))
        session.execute(delete(SandboxConnectionModel).where(SandboxConnectionModel.uuid == uuid))
        session.commit()


def test_sandbox_persistence_identity_scope_and_cleanup_fence(session):
    table = SandboxTable(session)
    now = datetime.now()
    connection = SandboxConnection(
        uuid="sb-disposable-db-test",
        space_uuid="sandbox-db-space",
        name="Sandbox DB test",
        provider="openshell",
        endpoint="http://sandbox-plugin.invalid",
        api_key="inert-test-token",
        image="fixture:approved",
        cpu=1,
        memory="512Mi",
        timeout_seconds=5,
        config_revision=1,
        status=SandboxStatus.READY,
        gmt_create=now,
        gmt_modified=now,
    )
    try:
        table.insert(connection)
        assert table.get_in_space(connection.uuid, "another-space") is None
        with pytest.raises(NotFound):
            table.save_binding(SandboxBinding("another-space", connection.uuid))
        session.rollback()
        table.save_binding(SandboxBinding(connection.space_uuid, connection.uuid, True))
        frozen = resolve_sandbox_profile(table, connection.space_uuid)
        table.update(connection.uuid, connection.space_uuid, {"api_key": "rotated-inert-token"})
        assert resolve_sandbox_credentials(table, frozen).api_key == "rotated-inert-token"
        table.reserve_lease(
            {
                "uuid": "sandbox-db-owned-lease",
                "connection_uuid": connection.uuid,
                "space_uuid": connection.space_uuid,
                "task_uuid": "trial-disposable-test",
                "attempt": 0,
                "diagnoser_uuid": "trial",
                "request_fingerprint": "a" * 64,
            },
            profile=frozen,
        )
        with pytest.raises(Conflict):
            table.update(connection.uuid, connection.space_uuid, {"image": "fixture:changed"})
        session.rollback()
        table.finish_lease("sandbox-db-owned-lease", cleanup_verified=False)
        assert session.scalar(select(SandboxLeaseModel)).state == "cleanup_pending"
        session.rollback()
        table.finish_lease("sandbox-db-owned-lease", cleanup_verified=True)
        table.finish_lease("sandbox-db-owned-lease", cleanup_verified=False)
        assert session.scalar(select(SandboxLeaseModel)).state == "closed"
        session.rollback()
        table.update(connection.uuid, connection.space_uuid, {"cpu": 2})
        with pytest.raises(SandboxError) as raised:
            resolve_sandbox_credentials(table, frozen)
        assert raised.value.code == "sandbox_identity_changed"
        session.rollback()
        table.save_binding(SandboxBinding(connection.space_uuid))
        table.delete(connection.uuid, connection.space_uuid)
        assert table.get_in_space(connection.uuid, connection.space_uuid) is None
    finally:
        session.rollback()
        for model in (SandboxLeaseModel, SandboxBindingModel, SandboxConnectionModel):
            session.execute(delete(model))
        session.commit()


def test_first_freeze_serializes_binding_creation_without_implicit_commit(session, engine):
    from concurrent.futures import ThreadPoolExecutor, TimeoutError
    from threading import Event

    from sqlalchemy.orm import sessionmaker

    sf = sessionmaker(engine, expire_on_commit=False)
    space_uuid = "sandbox-first-freeze-lock-test"
    started = Event()

    def change_policy():
        with sf() as concurrent_session:
            started.set()
            SandboxTable(concurrent_session).save_binding(SandboxBinding(space_uuid, None, True))

    try:
        table = SandboxTable(session)
        assert resolve_sandbox_profile(table, space_uuid, for_update=True) == {"mode": "local"}
        assert (
            session.scalar(
                select(SandboxBindingModel).where(SandboxBindingModel.space_uuid == space_uuid)
            )
            is not None
        )
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(change_policy)
            assert started.wait(5)
            try:
                with pytest.raises(TimeoutError):
                    future.result(timeout=0.2)
            finally:
                session.commit()
            future.result(timeout=10)
        session.expire_all()
        assert table.get_binding(space_uuid).require_sandbox is True
        table.get_binding("sandbox-freeze-rollback", for_update=True)
        session.rollback()
        assert (
            session.scalar(
                select(SandboxBindingModel).where(
                    SandboxBindingModel.space_uuid == "sandbox-freeze-rollback"
                )
            )
            is None
        )
    finally:
        session.rollback()
        session.execute(delete(SandboxBindingModel))
        session.commit()


def test_real_task_attempt_is_fenced_before_lease_insert(session):
    from agentlens.infra.ob.models import TaskModel

    table = SandboxTable(session)
    now = datetime.now()
    connection = SandboxConnection(
        uuid="sb-real-attempt-db-test",
        space_uuid="sandbox-db-space",
        name="Real attempt DB test",
        provider="openshell",
        endpoint="http://sandbox-plugin.invalid",
        api_key="inert-test-token",
        image="fixture:approved",
        cpu=1,
        memory="512Mi",
        timeout_seconds=5,
        config_revision=1,
        status=SandboxStatus.READY,
        gmt_create=now,
        gmt_modified=now,
    )
    profile = connection.frozen_profile()
    fields = dict(
        uuid="sandbox-real-attempt-lease",
        connection_uuid=connection.uuid,
        space_uuid=connection.space_uuid,
        task_uuid="sandbox-real-task",
        attempt=1,
        diagnoser_uuid="fixture-rule",
        request_fingerprint="a" * 64,
    )
    try:
        table.insert(connection)
        session.add(
            TaskModel(
                uuid="sandbox-real-task",
                job_uuid="fixture-job",
                status="running",
                worker="worker-one",
                attempt=1,
                scheduled_at=now,
                gmt_create=now,
                gmt_modified=now,
                execution_snapshot={
                    "job": {"space_uuid": connection.space_uuid},
                    "diagnosers": [{"uuid": "fixture-rule", "spec": {"sandbox": profile}}],
                },
            )
        )
        session.commit()
        for request, worker in ((fields, "other-worker"), (fields | {"attempt": 2}, "worker-one")):
            with pytest.raises(SandboxError) as raised:
                table.reserve_lease(request, profile=profile, worker_uuid=worker)
            assert raised.value.code == "sandbox_attempt_rejected"
            session.rollback()
        session.execute(
            TaskModel.__table__.update()
            .where(TaskModel.uuid == "sandbox-real-task")
            .values(status="canceled")
        )
        session.commit()
        with pytest.raises(SandboxError):
            table.reserve_lease(fields, profile=profile, worker_uuid="worker-one")
        session.rollback()
        assert session.scalar(select(SandboxLeaseModel)) is None
        session.execute(
            TaskModel.__table__.update()
            .where(TaskModel.uuid == "sandbox-real-task")
            .values(status="running")
        )
        session.commit()
        table.reserve_lease(fields, profile=profile, worker_uuid="worker-one")
        assert session.scalar(select(SandboxLeaseModel)).task_uuid == "sandbox-real-task"
    finally:
        session.rollback()
        for model in (SandboxLeaseModel, SandboxBindingModel, SandboxConnectionModel):
            session.execute(delete(model))
        session.execute(delete(TaskModel).where(TaskModel.uuid == "sandbox-real-task"))
        session.commit()
