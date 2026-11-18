"""add task cleanup index

Revision ID: 007
Revises: 006
Create Date: 2026-08-08

"""

from alembic import op

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "idx_task_status_finished_id",
        "agentlens_task",
        ["status", "finished_at", "id"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_task_status_finished_id",
        table_name="agentlens_task",
    )
