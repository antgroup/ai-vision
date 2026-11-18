"""Persist bounded model diagnostic reports and guard writes against configuration edits."""

import sqlalchemy as sa
from alembic import op

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agentlens_model_connection", sa.Column("diagnostic_validation", sa.JSON(), nullable=True)
    )
    op.add_column(
        "agentlens_model_connection",
        sa.Column("config_revision", sa.BigInteger(), nullable=False, server_default=sa.text("1")),
    )


def downgrade() -> None:
    op.drop_column("agentlens_model_connection", "config_revision")
    op.drop_column("agentlens_model_connection", "diagnostic_validation")
