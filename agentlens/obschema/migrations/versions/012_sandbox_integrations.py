"""Add optional Space sandbox profiles, binding and Task-attempt lease ownership."""

import sqlalchemy as sa
from alembic import op

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


def _audit():
    return [
        sa.Column("gmt_create", sa.DateTime(), nullable=False),
        sa.Column("gmt_modified", sa.DateTime(), nullable=False),
        sa.Column("creator", sa.String(64), nullable=True),
        sa.Column("modifier", sa.String(64), nullable=True),
    ]


def upgrade() -> None:
    op.create_table(
        "agentlens_sandbox_connection",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("uuid", sa.String(32), nullable=False),
        sa.Column("space_uuid", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("endpoint", sa.String(1024), nullable=False),
        sa.Column("api_key", sa.Text(), nullable=False),
        sa.Column("image", sa.String(512), nullable=False),
        sa.Column("cpu", sa.Integer(), nullable=False),
        sa.Column("memory", sa.String(32), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("config_revision", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        *_audit(),
        sa.UniqueConstraint("uuid", name="uk_sandbox_connection_uuid"),
        sa.UniqueConstraint("space_uuid", "name", name="uk_sandbox_connection_sn"),
    )
    op.create_index("idx_sandbox_connection_space", "agentlens_sandbox_connection", ["space_uuid"])
    op.create_table(
        "agentlens_sandbox_binding",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("space_uuid", sa.String(64), nullable=False),
        sa.Column("connection_uuid", sa.String(32), nullable=True),
        sa.Column("require_sandbox", sa.Integer(), nullable=False),
        *_audit(),
        sa.UniqueConstraint("space_uuid", name="uk_sandbox_binding_space"),
    )
    op.create_table(
        "agentlens_sandbox_lease",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("uuid", sa.String(64), nullable=False),
        sa.Column("connection_uuid", sa.String(32), nullable=False),
        sa.Column("space_uuid", sa.String(64), nullable=False),
        sa.Column("task_uuid", sa.String(64), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("diagnoser_uuid", sa.String(64), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("cleanup_verified", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        *_audit(),
        sa.UniqueConstraint("uuid", name="uk_sandbox_lease_uuid"),
    )
    op.create_index(
        "idx_sandbox_lease_connection_state",
        "agentlens_sandbox_lease",
        ["connection_uuid", "state"],
    )
    op.create_index(
        "idx_sandbox_lease_task_attempt", "agentlens_sandbox_lease", ["task_uuid", "attempt"]
    )


def downgrade() -> None:
    op.drop_table("agentlens_sandbox_lease")
    op.drop_table("agentlens_sandbox_binding")
    op.drop_table("agentlens_sandbox_connection")
