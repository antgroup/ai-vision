"""Add bounded Judge request accounting without rewriting legacy Tasks."""

import sqlalchemy as sa
from alembic import op

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agentlens_task", sa.Column("judge_request_ledger", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("agentlens_task", "judge_request_ledger")
