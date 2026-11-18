"""Migration must preserve identity and refuse genuine retired dependencies."""

import copy
import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa


def migration_module():
    path = (
        Path(__file__).resolve().parents[4]
        / "obschema/migrations/versions/008_signoz_dataset_contract.py"
    )
    spec = importlib.util.spec_from_file_location("dataset_migration_008", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def database():
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    datasets = sa.Table(
        "agentlens_dataset",
        metadata,
        sa.Column("uuid", sa.String, primary_key=True),
        sa.Column("type", sa.String),
        sa.Column("spec", sa.JSON),
        sa.Column("name", sa.String),
        sa.Column("gmt_modified", sa.String),
    )
    metadata.create_all(engine)
    with engine.begin() as connection:
        yield connection, datasets
    engine.dispose()


def dataset(uuid="d-1"):
    return {
        "uuid": uuid,
        "type": "stargazer",
        "name": "original",
        "gmt_modified": "2026-09-15 10:00:00",
        "spec": {
            "stargazer": {"db": "", "biz_domain": ""},
            "runtime_type": "codex",
            "space_filter": "s-1",
            "username": "u-1",
            "query": {
                "table": "signoz_traces",
                "where": "name = 'kept'",
                "time_range": "1h",
                "time_offset": "3h",
                "limit": 20000,
            },
        },
    }


def test_upgrade_keeps_all_dataset_fields_except_retired_contract(database):
    connection, table = database
    originals = [dataset(f"d-{index}") for index in range(4)]
    connection.execute(table.insert(), originals)
    with patch.object(migration_module().op, "get_bind", return_value=connection):
        migration_module().upgrade()
    rows = connection.execute(sa.select(table).order_by(table.c.uuid)).mappings().all()
    for original, actual in zip(originals, rows, strict=True):
        expected = copy.deepcopy(original)
        expected["type"] = "signoz"
        expected["spec"].pop("stargazer")
        assert dict(actual) == expected


@pytest.mark.parametrize(
    "bad",
    [
        {"type": "odps"},
        {"spec": {"stargazer": {"db": "real_database"}, "query": {"table": "signoz_traces"}}},
        {"spec": {"query": {"table": "sample_trace"}}},
        {"spec": {"query": "malformed"}},
        {"spec": {"query": {"table": "signoz_traces"}, "selector": {"type": "legacy_model_quota"}}},
    ],
)
def test_preflight_lists_blockers_before_changing_any_dataset(database, bad):
    connection, table = database
    original = dataset()
    retired = {**dataset("d-retired"), **bad}
    connection.execute(table.insert(), [original, retired])
    module = migration_module()
    with patch.object(module.op, "get_bind", return_value=connection):
        with pytest.raises(RuntimeError, match="d-retired"):
            module.upgrade()
    assert (
        dict(connection.execute(sa.select(table).where(table.c.uuid == "d-1")).mappings().one())
        == original
    )
