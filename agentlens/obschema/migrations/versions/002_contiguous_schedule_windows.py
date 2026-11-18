"""index task schedule lookup

Revision ID: 002
Revises: 001
Create Date: 2026-07-23

"""
from alembic import op

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "idx_task_job_sched",
        "agentlens_task",
        ["job_uuid", "scheduled_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_task_job_sched",
        "agentlens_task",
    )
