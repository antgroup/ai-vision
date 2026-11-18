from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _audit():
    return (
        mapped_column(DateTime, nullable=False, default=lambda: datetime.now()),
        mapped_column(
            DateTime,
            nullable=False,
            default=lambda: datetime.now(),
            onupdate=lambda: datetime.now(),
        ),
        mapped_column(String(64), nullable=True),
        mapped_column(String(64), nullable=True),
    )


class DiagnoserModel(Base):
    __tablename__ = "agentlens_diagnoser"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    space_uuid: Mapped[str] = mapped_column(String(64), nullable=False)
    agent_uuid: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    spec: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    gmt_create, gmt_modified, creator, modifier = _audit()
    __table_args__ = (
        UniqueConstraint("space_uuid", "agent_uuid", "name", name="uk_diagnoser_san"),
    )


class ModelConnectionModel(Base):
    __tablename__ = "agentlens_model_connection"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(String(32), nullable=False)
    space_uuid: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(1024), nullable=False)
    model_name: Mapped[str] = mapped_column(String(256), nullable=False)
    api_key: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    diagnostic_validation: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    config_revision: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=1, server_default=text("1")
    )
    gmt_create, gmt_modified, creator, modifier = _audit()
    __table_args__ = (
        UniqueConstraint("uuid", name="uk_model_connection_uuid"),
        UniqueConstraint("space_uuid", "name", name="uk_model_connection_sn"),
        Index("idx_model_connection_space", "space_uuid"),
    )


class DatasetModel(Base):
    __tablename__ = "agentlens_dataset"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    space_uuid: Mapped[str] = mapped_column(String(64), nullable=False)
    agent_uuid: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    spec: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    gmt_create, gmt_modified, creator, modifier = _audit()
    __table_args__ = (UniqueConstraint("space_uuid", "agent_uuid", "name", name="uk_dataset_san"),)


class JobModel(Base):
    __tablename__ = "agentlens_job"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    space_uuid: Mapped[str] = mapped_column(String(64), nullable=False)
    agent_uuid: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    trigger_type: Mapped[str] = mapped_column(String(16), nullable=False)
    schedule_cron: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    dataset_uuid: Mapped[str] = mapped_column(String(32), nullable=False)
    diagnoser_uuids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    spec: Mapped[dict | None] = mapped_column("config", JSON, nullable=True)
    gmt_create, gmt_modified, creator, modifier = _audit()
    __table_args__ = (UniqueConstraint("space_uuid", "agent_uuid", "name", name="uk_job_san"),)


class TaskModel(Base):
    __tablename__ = "agentlens_task"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    job_uuid: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    worker: Mapped[str | None] = mapped_column(String(200), nullable=True)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    msg: Mapped[str] = mapped_column(Text, nullable=False, default="")
    stats: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    execution_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    judge_request_ledger: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    gmt_create, gmt_modified, creator, modifier = _audit()
    __table_args__ = (
        Index("idx_task_job_sched", "job_uuid", "scheduled_at"),
        Index("idx_task_status_sched", "status", "scheduled_at"),
        Index("idx_task_status_finished_id", "status", "finished_at", "id"),
        Index("idx_task_worker_status", "worker", "status"),
    )


class HeartbeatModel(Base):
    __tablename__ = "agentlens_heartbeat"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    hostname: Mapped[str] = mapped_column(String(128), nullable=False)
    ip: Mapped[str] = mapped_column(String(64), nullable=False)
    gmt_create: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now()
    )
    gmt_modified: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(), onupdate=lambda: datetime.now()
    )
    __table_args__ = (Index("idx_heartbeat_gmt_modified", "gmt_modified"),)


class LeaderLeaseModel(Base):
    __tablename__ = "agentlens_leader_lease"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    lock_key: Mapped[str] = mapped_column(String(32), nullable=False)
    holder_uuid: Mapped[str] = mapped_column(String(200), nullable=False)
    acquired_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    fenced_token: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    gmt_create, gmt_modified, creator, modifier = _audit()
    __table_args__ = (Index("idx_leader_lease_lockkey", "lock_key"),)


class SandboxConnectionModel(Base):
    __tablename__ = "agentlens_sandbox_connection"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    space_uuid: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(1024), nullable=False)
    api_key: Mapped[str] = mapped_column(Text, nullable=False)
    image: Mapped[str] = mapped_column(String(512), nullable=False)
    cpu: Mapped[int] = mapped_column(Integer, nullable=False)
    memory: Mapped[str] = mapped_column(String(32), nullable=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    config_revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    gmt_create, gmt_modified, creator, modifier = _audit()
    __table_args__ = (
        UniqueConstraint("space_uuid", "name", name="uk_sandbox_connection_sn"),
        Index("idx_sandbox_connection_space", "space_uuid"),
    )


class SandboxBindingModel(Base):
    __tablename__ = "agentlens_sandbox_binding"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    space_uuid: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    connection_uuid: Mapped[str | None] = mapped_column(String(32), nullable=True)
    require_sandbox: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    gmt_create, gmt_modified, creator, modifier = _audit()


class SandboxLeaseModel(Base):
    __tablename__ = "agentlens_sandbox_lease"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    connection_uuid: Mapped[str] = mapped_column(String(32), nullable=False)
    space_uuid: Mapped[str] = mapped_column(String(64), nullable=False)
    task_uuid: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    diagnoser_uuid: Mapped[str] = mapped_column(String(64), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    cleanup_verified: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    gmt_create, gmt_modified, creator, modifier = _audit()
    __table_args__ = (
        Index("idx_sandbox_lease_connection_state", "connection_uuid", "state"),
        Index("idx_sandbox_lease_task_attempt", "task_uuid", "attempt"),
    )
