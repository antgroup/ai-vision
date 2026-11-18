"""Real SQL persistence, migration compatibility, scope and stale-write boundaries."""

import importlib.util
from datetime import datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.orm import Session

from agentlens.domain.enums import ModelConnectionStatus
from agentlens.infra.ob.models import ModelConnectionModel
from agentlens.infra.ob.tables.model_connection_table import ModelConnectionTable
from agentlens.service.model_validation import definition_digest


def report():
    return {
        "status": "passed",
        "reason": None,
        "validated_at": datetime.now().isoformat(),
        "profile": {
            "builtin_key": "task_completion",
            "canonical_variant": "text_response",
            "definition_digest": definition_digest(),
            "generation_options": {
                "max_tokens": 4096,
                "enable_thinking": None,
                "temperature": 0,
                "reasoning_effort": None,
                "request_timeout_sec": 45,
            },
        },
        "cases": [
            {
                "name": name,
                "status": "passed",
                "reason": None,
                "expected": expected,
                "actual": expected,
            }
            for name, expected in (("positive", "good"), ("negative", "bad"))
        ],
        "usage": {"requests": 2, "input_tokens": None, "output_tokens": None, "total_tokens": None},
    }


@pytest.fixture
def storage():
    engine = sa.create_engine("sqlite://")
    table = ModelConnectionModel.__table__.to_metadata(sa.MetaData())
    table.c.id.type = sa.Integer()
    table.create(engine)
    session = Session(engine)
    session.add(
        ModelConnectionModel(
            uuid="m-test",
            space_uuid="space",
            name="test",
            endpoint="https://judge.test/v1",
            model_name="judge",
            api_key="inert",
            status="untested",
            gmt_create=datetime.now(),
            gmt_modified=datetime.now(),
        )
    )
    session.commit()
    yield ModelConnectionTable(session), engine
    session.close()
    engine.dispose()


def test_snapshot_ends_transaction_and_report_roundtrips(storage):
    table, _ = storage
    snapshot = table.validation_snapshot("m-test", "space")
    assert snapshot is not None
    assert not table.s.in_transaction()
    assert table.save_validation(snapshot, report(), ModelConnectionStatus.AVAILABLE)
    assert table.get_in_space("m-test", "space").diagnostic_validation == report() | {
        "validated_at": table.get_by_uuid("m-test").diagnostic_validation["validated_at"]
    }
    assert table.validation_snapshot("m-test", "other") is None


def test_sql_compare_and_swap_rejects_edit_and_aba_and_clears_report(storage):
    table, _ = storage
    snapshot = table.validation_snapshot("m-test", "space")
    assert table.save_validation(snapshot, report(), ModelConnectionStatus.AVAILABLE)
    table.update_by_uuid("m-test", {"api_key": "replacement"})
    table.update_by_uuid("m-test", {"api_key": "inert"})
    assert not table.save_validation(snapshot, report(), ModelConnectionStatus.AVAILABLE)
    current = table.get_by_uuid("m-test")
    assert current.config_revision == snapshot.config_revision + 2
    assert current.diagnostic_validation is None
    assert current.status == ModelConnectionStatus.UNTESTED


def test_storage_rejects_raw_report_fields_and_cross_space_snapshot(storage):
    table, _ = storage
    snapshot = table.validation_snapshot("m-test", "space")
    raw = report() | {"raw_response": "inert-secret"}
    with pytest.raises(ValueError):
        table.save_validation(snapshot, raw, ModelConnectionStatus.AVAILABLE)
    snapshot.space_uuid = "other"
    assert not table.save_validation(snapshot, report(), ModelConnectionStatus.AVAILABLE)
    assert table.get_by_uuid("m-test").diagnostic_validation is None


def test_migration_preserves_legacy_connection_and_matches_fresh_schema():
    path = (
        Path(__file__).resolve().parents[4]
        / "obschema/migrations/versions/011_model_diagnostic_validation.py"
    )
    assert path.exists(), "validation must upgrade existing deployments"
    spec = importlib.util.spec_from_file_location("validation_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    legacy = sa.Table(
        "agentlens_model_connection",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("uuid", sa.String),
        sa.Column("api_key", sa.String),
        sa.Column("status", sa.String),
    )
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            legacy.insert(), {"uuid": "old", "api_key": "inert", "status": "available"}
        )
        module.op = Operations(MigrationContext.configure(connection))
        module.upgrade()
        upgraded = sa.Table(legacy.name, sa.MetaData(), autoload_with=connection)
        row = connection.execute(sa.select(upgraded)).mappings().one()
        assert row["api_key"] == "inert" and row["status"] == "available"
        assert row["diagnostic_validation"] is None and row["config_revision"] == 1
        assert ModelConnectionModel.__table__.c.config_revision.server_default.arg.text == "1"
        assert ModelConnectionModel.__table__.c.diagnostic_validation.nullable
        module.downgrade()
        assert set(c["name"] for c in sa.inspect(connection).get_columns(legacy.name)) == set(
            legacy.c.keys()
        )
    engine.dispose()


def test_seekdb_snapshot_cas_across_independent_sessions(session):
    from sqlalchemy.orm import sessionmaker

    from agentlens.domain.model_connection import ModelConnection

    table = ModelConnectionTable(session)
    table.insert(
        ModelConnection(
            "m-cas",
            "space",
            "test",
            "https://judge.test/v1",
            "judge",
            "inert",
            ModelConnectionStatus.UNTESTED,
            datetime.now(),
            datetime.now(),
            None,
            None,
        )
    )
    snapshot = table.validation_snapshot("m-cas", "space")
    assert not session.in_transaction()
    with sessionmaker(bind=session.bind)() as other:
        writer = ModelConnectionTable(other)
        writer.update_by_uuid("m-cas", {"model_name": "edited"})
        writer.update_by_uuid("m-cas", {"model_name": "judge"})
    assert not table.save_validation(snapshot, report(), ModelConnectionStatus.AVAILABLE)
    current = table.validation_snapshot("m-cas", "space")
    assert current.config_revision == 3 and current.diagnostic_validation is None
    safe_report = report()
    assert table.save_validation(current, safe_report, ModelConnectionStatus.AVAILABLE)
    with sessionmaker(bind=session.bind)() as reader:
        reloaded = ModelConnectionTable(reader).get_in_space("m-cas", "space")
        assert reloaded.diagnostic_validation == safe_report
        assert reloaded.status == ModelConnectionStatus.AVAILABLE
    table.update_by_uuid("m-cas", {"endpoint": "https://different.test/v1", "api_key": "new"})
    assert table.get_by_uuid("m-cas").diagnostic_validation is None
