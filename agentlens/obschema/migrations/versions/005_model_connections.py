"""add space-scoped LLM connections and migrate diagnoser model specs

Revision ID: 005
Revises: 004
Create Date: 2026-08-05

"""

import json
import secrets
import string
from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def _new_uuid(existing: set[str]) -> str:
    alphabet = string.ascii_lowercase + string.digits
    while True:
        value = "m-" + "".join(secrets.choice(alphabet) for _ in range(8))
        if value not in existing:
            existing.add(value)
            return value


def _as_dict(value):
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _unique_name(connection, space_uuid: str, base: str, uuid: str) -> str:
    name = base[:128] or "model"
    existing = connection.execute(
        sa.text(
            "SELECT 1 FROM agentlens_model_connection "
            "WHERE space_uuid=:space_uuid AND name=:name LIMIT 1"
        ),
        {"space_uuid": space_uuid, "name": name},
    ).first()
    if existing is None:
        return name
    suffix = "-" + uuid[-8:]
    return (base[: 128 - len(suffix)] or "model") + suffix


def upgrade() -> None:
    op.create_table(
        "agentlens_model_connection",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("uuid", sa.String(32), nullable=False),
        sa.Column("space_uuid", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("endpoint", sa.String(1024), nullable=False),
        sa.Column("model_name", sa.String(256), nullable=False),
        sa.Column("api_key", sa.Text, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("gmt_create", sa.DateTime, nullable=False),
        sa.Column("gmt_modified", sa.DateTime, nullable=False),
        sa.Column("creator", sa.String(64), nullable=True),
        sa.Column("modifier", sa.String(64), nullable=True),
        sa.UniqueConstraint("uuid", name="uk_model_connection_uuid"),
        sa.UniqueConstraint("space_uuid", "name", name="uk_model_connection_sn"),
    )
    op.create_index(
        "idx_model_connection_space",
        "agentlens_model_connection",
        ["space_uuid"],
    )

    bind = op.get_bind()
    diagnoser = sa.table(
        "agentlens_diagnoser",
        sa.column("uuid", sa.String(32)),
        sa.column("space_uuid", sa.String(64)),
        sa.column("type", sa.String(16)),
        sa.column("mode", sa.String(16)),
        sa.column("spec", sa.JSON),
        sa.column("gmt_modified", sa.DateTime),
    )
    connection = sa.table(
        "agentlens_model_connection",
        sa.column("uuid", sa.String(32)),
        sa.column("space_uuid", sa.String(64)),
        sa.column("name", sa.String(128)),
        sa.column("endpoint", sa.String(1024)),
        sa.column("model_name", sa.String(256)),
        sa.column("api_key", sa.Text),
        sa.column("status", sa.String(16)),
        sa.column("gmt_create", sa.DateTime),
        sa.column("gmt_modified", sa.DateTime),
    )
    rows = bind.execute(sa.select(diagnoser)).mappings().all()
    existing_uuids: set[str] = set()
    config_to_uuid: dict[tuple[str, str, str, str], str] = {}
    now = datetime.now()

    for row in rows:
        spec = _as_dict(row["spec"])
        model = spec.get("model")
        if not isinstance(model, dict):
            continue
        # Only known builtin scenes can migrate automatically. Keep the inventory
        # local so replaying this migration never depends on a later code registry.
        scenario = spec.get("scenario")
        if (
            row["type"] == "builtin"
            and row["mode"] == "llm"
            and isinstance(scenario, str)
            and scenario
            and scenario not in {"common", "coding", "openclaw", "specialist"}
        ):
            continue
        endpoint = str(model.get("endpoint") or "")
        model_name = str(model.get("model_id") or "")
        api_key = str(model.get("api_key") or "")
        key = (row["space_uuid"], endpoint, model_name, api_key)
        connection_uuid = config_to_uuid.get(key)
        if connection_uuid is None:
            connection_uuid = _new_uuid(existing_uuids)
            config_to_uuid[key] = connection_uuid
            bind.execute(
                connection.insert().values(
                    uuid=connection_uuid,
                    space_uuid=row["space_uuid"],
                    name=_unique_name(bind, row["space_uuid"], model_name, connection_uuid),
                    endpoint=endpoint,
                    model_name=model_name,
                    api_key=api_key,
                    status="untested",
                    gmt_create=now,
                    gmt_modified=now,
                )
            )
        next_spec = dict(spec)
        next_spec.pop("model", None)
        next_spec["model_connection_uuid"] = connection_uuid
        bind.execute(
            diagnoser.update()
            .where(diagnoser.c.uuid == row["uuid"])
            .values(spec=next_spec, gmt_modified=now)
        )


def downgrade() -> None:
    bind = op.get_bind()
    diagnoser = sa.table(
        "agentlens_diagnoser",
        sa.column("uuid", sa.String(32)),
        sa.column("spec", sa.JSON),
    )
    connection = sa.table(
        "agentlens_model_connection",
        sa.column("uuid", sa.String(32)),
        sa.column("endpoint", sa.String(1024)),
        sa.column("model_name", sa.String(256)),
        sa.column("api_key", sa.Text),
    )
    connection_rows = {
        row["uuid"]: row for row in bind.execute(sa.select(connection)).mappings().all()
    }
    now = datetime.now()
    for row in bind.execute(sa.select(diagnoser)).mappings().all():
        spec = _as_dict(row["spec"])
        connection_uuid = spec.get("model_connection_uuid")
        model = connection_rows.get(connection_uuid)
        if model is None:
            continue
        next_spec = dict(spec)
        next_spec.pop("model_connection_uuid", None)
        next_spec["model"] = {
            "endpoint": model["endpoint"],
            "model_id": model["model_name"],
            "api_key": model["api_key"],
        }
        bind.execute(
            diagnoser.update()
            .where(diagnoser.c.uuid == row["uuid"])
            .values(spec=next_spec, gmt_modified=now)
        )
    op.drop_index("idx_model_connection_space", table_name="agentlens_model_connection")
    op.drop_table("agentlens_model_connection")
