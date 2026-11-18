"""initial schema for 6 agentlens tables + leader_lease seed row

Revision ID: 001
Revises:
Create Date: 2026-06-04

"""
from alembic import op
import sqlalchemy as sa
from datetime import datetime

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agentlens_diagnoser",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("uuid", sa.String(32), nullable=False, unique=True),
        sa.Column("space_uuid", sa.String(64), nullable=False),  # "default"=平台全局内置，所有 space 可见
        sa.Column("agent_uuid", sa.String(64), nullable=False, server_default=""),  # 自定义=所属 agent；全局内置=""
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("type", sa.String(16), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("spec", sa.JSON, nullable=False),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("gmt_create", sa.DateTime, nullable=False),
        sa.Column("gmt_modified", sa.DateTime, nullable=False),
        sa.Column("creator", sa.String(64), nullable=True),
        sa.Column("modifier", sa.String(64), nullable=True),
        sa.UniqueConstraint("space_uuid", "agent_uuid", "name", name="uk_diagnoser_san"),
    )
    op.create_table(
        "agentlens_dataset",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("uuid", sa.String(32), nullable=False, unique=True),
        sa.Column("space_uuid", sa.String(64), nullable=False),
        sa.Column("agent_uuid", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("type", sa.String(16), nullable=False),
        sa.Column("spec", sa.JSON, nullable=False),
        sa.Column("gmt_create", sa.DateTime, nullable=False),
        sa.Column("gmt_modified", sa.DateTime, nullable=False),
        sa.Column("creator", sa.String(64), nullable=True),
        sa.Column("modifier", sa.String(64), nullable=True),
        sa.UniqueConstraint("space_uuid", "agent_uuid", "name", name="uk_dataset_san"),
    )
    op.create_table(
        "agentlens_job",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("uuid", sa.String(32), nullable=False, unique=True),
        sa.Column("space_uuid", sa.String(64), nullable=False),
        sa.Column("agent_uuid", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("trigger_type", sa.String(16), nullable=False),
        sa.Column("schedule_cron", sa.String(64), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("dataset_uuid", sa.String(32), nullable=False),
        sa.Column("diagnoser_uuids", sa.JSON, nullable=False),
        sa.Column("gmt_create", sa.DateTime, nullable=False),
        sa.Column("gmt_modified", sa.DateTime, nullable=False),
        sa.Column("creator", sa.String(64), nullable=True),
        sa.Column("modifier", sa.String(64), nullable=True),
        sa.UniqueConstraint("space_uuid", "agent_uuid", "name", name="uk_job_san"),
    )
    op.create_table(
        "agentlens_task",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("uuid", sa.String(32), nullable=False, unique=True),
        sa.Column("job_uuid", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("worker", sa.String(200), nullable=True),
        sa.Column("scheduled_at", sa.DateTime, nullable=False),
        sa.Column("started_at", sa.DateTime, nullable=True),
        sa.Column("finished_at", sa.DateTime, nullable=True),
        sa.Column("attempt", sa.Integer, nullable=False, server_default="0"),
        sa.Column("msg", sa.Text, nullable=False),
        sa.Column("stats", sa.JSON, nullable=True),
        sa.Column("gmt_create", sa.DateTime, nullable=False),
        sa.Column("gmt_modified", sa.DateTime, nullable=False),
        sa.Column("creator", sa.String(64), nullable=True),
        sa.Column("modifier", sa.String(64), nullable=True),
    )
    op.create_index("idx_task_status_sched", "agentlens_task", ["status", "scheduled_at"])
    op.create_index("idx_task_worker_status", "agentlens_task", ["worker", "status"])

    op.create_table(
        "agentlens_heartbeat",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("uuid", sa.String(200), nullable=False, unique=True),
        sa.Column("hostname", sa.String(128), nullable=False),
        sa.Column("ip", sa.String(64), nullable=False),
        sa.Column("gmt_create", sa.DateTime, nullable=False),
        sa.Column("gmt_modified", sa.DateTime, nullable=False),
    )
    op.create_index("idx_heartbeat_gmt_modified", "agentlens_heartbeat", ["gmt_modified"])

    op.create_table(
        "agentlens_leader_lease",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("uuid", sa.String(32), nullable=False, unique=True),
        sa.Column("lock_key", sa.String(32), nullable=False),
        sa.Column("holder_uuid", sa.String(200), nullable=False),
        sa.Column("acquired_at", sa.DateTime, nullable=False),
        sa.Column("expires_at", sa.DateTime, nullable=False),
        sa.Column("fenced_token", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("gmt_create", sa.DateTime, nullable=False),
        sa.Column("gmt_modified", sa.DateTime, nullable=False),
        sa.Column("creator", sa.String(64), nullable=True),
        sa.Column("modifier", sa.String(64), nullable=True),
    )
    op.create_index("idx_leader_lease_lockkey", "agentlens_leader_lease", ["lock_key"])

    # seed: 单行 master lease
    # NOTE: explicit id=1 because SQLite does not auto-increment BIGINT PKs.
    now = datetime.now()  # 本地时区（CLAUDE.md：禁 UTC）
    op.bulk_insert(
        sa.table(
            "agentlens_leader_lease",
            sa.column("id", sa.BigInteger),
            sa.column("uuid", sa.String),
            sa.column("lock_key", sa.String),
            sa.column("holder_uuid", sa.String),
            sa.column("acquired_at", sa.DateTime),
            sa.column("expires_at", sa.DateTime),
            sa.column("fenced_token", sa.BigInteger),
            sa.column("gmt_create", sa.DateTime),
            sa.column("gmt_modified", sa.DateTime),
        ),
        [{
            "id": 1,
            "uuid": "lease-master-seed",
            "lock_key": "master",
            "holder_uuid": "__bootstrap__",
            "acquired_at": now,
            "expires_at": now,
            "fenced_token": 0,
            "gmt_create": now,
            "gmt_modified": now,
        }],
    )


def downgrade() -> None:
    op.drop_table("agentlens_leader_lease")
    op.drop_table("agentlens_heartbeat")
    op.drop_index("idx_task_worker_status", table_name="agentlens_task")
    op.drop_index("idx_task_status_sched", table_name="agentlens_task")
    op.drop_table("agentlens_task")
    op.drop_table("agentlens_job")
    op.drop_table("agentlens_dataset")
    op.drop_table("agentlens_diagnoser")
