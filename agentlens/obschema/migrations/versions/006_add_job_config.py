"""add job config

Revision ID: 006
Revises: 005
Create Date: 2026-08-06

"""

import sqlalchemy as sa
from alembic import op

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agentlens_job",
        sa.Column("config", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agentlens_job", "config")
