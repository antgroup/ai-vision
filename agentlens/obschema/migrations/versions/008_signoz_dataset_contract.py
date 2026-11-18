"""Rename existing SigNoz datasets without changing identity or query semantics.

Revision ID: 008
Revises: 007
"""

import copy
import os

import sqlalchemy as sa
from alembic import op

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    datasets = sa.table(
        "agentlens_dataset",
        sa.column("uuid", sa.String),
        sa.column("type", sa.String),
        sa.column("spec", sa.JSON),
    )
    rows = connection.execute(sa.select(datasets)).mappings().all()
    expected_table = os.environ.get("SIGNOZ_SPANS_TABLE", "signoz_traces")
    blocked = []
    changes = []
    for row in rows:
        spec = copy.deepcopy(row["spec"])
        if row["type"] == "signoz":
            continue
        if (
            row["type"] != "stargazer"
            or not isinstance(spec, dict)
            or not isinstance(spec.get("query"), dict)
            or spec["query"].get("table") != expected_table
            or not isinstance(spec.get("stargazer", {}), dict)
            or any(spec.get("stargazer", {}).values())
            or spec.get("odps")
            or spec.get("selector")
        ):
            blocked.append(f"{row['uuid']} (type={row['type']})")
            continue
        spec.pop("stargazer", None)
        changes.append((row["uuid"], spec))
    if blocked:
        raise RuntimeError(
            "Retired dataset dependencies; migrate these records first: " + ", ".join(blocked)
        )
    # Validate all rows before the first update, including on nontransactional DDL databases.
    for uuid, spec in changes:
        connection.execute(
            datasets.update().where(datasets.c.uuid == uuid).values(type="signoz", spec=spec)
        )


def downgrade() -> None:
    connection = op.get_bind()
    datasets = sa.table(
        "agentlens_dataset",
        sa.column("uuid", sa.String),
        sa.column("type", sa.String),
        sa.column("spec", sa.JSON),
    )
    for row in (
        connection.execute(sa.select(datasets).where(datasets.c.type == "signoz")).mappings().all()
    ):
        spec = copy.deepcopy(row["spec"])
        spec["stargazer"] = {}
        connection.execute(
            datasets.update()
            .where(datasets.c.uuid == row["uuid"])
            .values(type="stargazer", spec=spec)
        )
