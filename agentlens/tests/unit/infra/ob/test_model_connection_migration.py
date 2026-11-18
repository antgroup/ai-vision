"""Model migration preserves unknown builtin scenes and existing model references."""

import importlib.util
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_upgrade_preserves_retired_rows_and_migrates_supported_and_custom_models():
    path = (
        Path(__file__).resolve().parents[4]
        / "obschema/migrations/versions/005_model_connections.py"
    )
    module_spec = importlib.util.spec_from_file_location("model_connection_migration_005", path)
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    diagnosers = sa.Table(
        "agentlens_diagnoser",
        metadata,
        sa.Column("uuid", sa.String, primary_key=True),
        sa.Column("space_uuid", sa.String),
        sa.Column("type", sa.String),
        sa.Column("mode", sa.String),
        sa.Column("spec", sa.JSON),
        sa.Column("gmt_modified", sa.DateTime),
    )
    metadata.create_all(engine)
    original_time = datetime(2026, 8, 1)
    model = {"endpoint": "https://example.test/v1", "model_id": "fixture", "api_key": "inert"}
    rows = [
        {
            "uuid": name,
            "space_uuid": "space-1",
            "type": kind,
            "mode": "llm",
            "spec": {"model": model, "prompt": "preserved", **scene},
            "gmt_modified": original_time,
        }
        for name, kind, scene in [
            ("retired", "builtin", {"scenario": "retired_business"}),
            ("unknown", "builtin", {"scenario": "unregistered_scene"}),
            ("common", "builtin", {"scenario": "common"}),
            ("coding", "builtin", {"scenario": "coding"}),
            ("openclaw", "builtin", {"scenario": "openclaw"}),
            ("specialist", "builtin", {"scenario": "specialist"}),
            ("missing", "builtin", {}),
            ("null", "builtin", {"scenario": None}),
            ("empty", "builtin", {"scenario": ""}),
            ("custom", "custom", {"scenario": "retired_business"}),
        ]
    ]
    try:
        with engine.begin() as connection:
            connection.execute(diagnosers.insert(), rows)
            operations = Operations(MigrationContext.configure(connection))

            def create_table(name, *columns):
                # SQLite autoincrement needs INTEGER; production uses BIGINT.
                for column in columns:
                    if isinstance(column, sa.Column) and column.name == "id":
                        column.type = sa.Integer()
                return operations.create_table(name, *columns)

            module.op = SimpleNamespace(
                create_table=create_table,
                create_index=operations.create_index,
                get_bind=lambda: connection,
            )
            module.upgrade()
            actual = {
                row["uuid"]: dict(row)
                for row in connection.execute(sa.select(diagnosers)).mappings()
            }
            connections = sa.Table(
                "agentlens_model_connection", sa.MetaData(), autoload_with=connection
            )
            saved_model = dict(connection.execute(sa.select(connections)).mappings().one())
            assert saved_model["space_uuid"] == "space-1"
            assert saved_model["endpoint"] == model["endpoint"]
            assert saved_model["model_name"] == model["model_id"]
            assert saved_model["api_key"] == model["api_key"]
            for original in rows:
                migrated = actual[original["uuid"]]
                if original["uuid"] in {"retired", "unknown"}:
                    assert migrated == original
                    continue
                expected = dict(original["spec"])
                expected.pop("model")
                expected["model_connection_uuid"] = saved_model["uuid"]
                assert migrated["spec"] == expected
                assert migrated["gmt_modified"] != original_time
                for field in ("uuid", "space_uuid", "type", "mode"):
                    assert migrated[field] == original[field]
    finally:
        engine.dispose()
