"""set diagnoser default agent scope

Revision ID: 003
Revises: 002
Create Date: 2026-08-03

"""

import sqlalchemy as sa
from alembic import op

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "agentlens_diagnoser",
        "agent_uuid",
        existing_type=sa.String(64),
        server_default="default",
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "agentlens_diagnoser",
        "agent_uuid",
        existing_type=sa.String(64),
        server_default="",
        existing_nullable=False,
    )
