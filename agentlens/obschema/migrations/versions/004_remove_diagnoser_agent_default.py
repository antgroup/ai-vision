"""remove diagnoser agent default

Revision ID: 004
Revises: 003
Create Date: 2026-08-05

"""

import sqlalchemy as sa
from alembic import op

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "agentlens_diagnoser",
        "agent_uuid",
        existing_type=sa.String(64),
        server_default=None,
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "agentlens_diagnoser",
        "agent_uuid",
        existing_type=sa.String(64),
        server_default="default",
        existing_nullable=False,
    )
