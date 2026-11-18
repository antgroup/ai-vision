"""Add credential-free execution snapshots without rewriting legacy Tasks.

Revision ID: 009
Revises: 008
"""

import sqlalchemy as sa
from alembic import op

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agentlens_task", sa.Column("execution_snapshot", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("agentlens_task", "execution_snapshot")
