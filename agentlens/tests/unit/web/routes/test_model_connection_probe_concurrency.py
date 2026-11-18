"""Probe a real SQL-backed connection while its configuration changes."""

from datetime import datetime

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from agentlens.domain.enums import ModelConnectionStatus
from agentlens.domain.errors import Conflict
from agentlens.infra.ob.models import ModelConnectionModel
from agentlens.infra.ob.tables.model_connection_table import ModelConnectionTable
from agentlens.web.routes import model_connection


@pytest.fixture
def connection_table():
    engine = sa.create_engine("sqlite://")
    metadata = ModelConnectionModel.__table__.to_metadata(sa.MetaData())
    metadata.c.id.type = sa.Integer()
    metadata.create(engine)
    session = Session(engine)
    session.add(
        ModelConnectionModel(
            uuid="m-probe-race",
            space_uuid="space",
            name="probe",
            endpoint="https://judge.test/v1",
            model_name="model",
            api_key="inert-probe-key",
            status="untested",
            gmt_create=datetime.now(),
            gmt_modified=datetime.now(),
        )
    )
    session.commit()
    yield ModelConnectionTable(session)
    session.close()
    engine.dispose()


def install_probe(monkeypatch, callback):
    closed = []

    class Judge:
        def probe(self):
            callback()

        def close(self):
            closed.append(True)

    monkeypatch.setattr(model_connection, "LLMJudge", lambda *args, **kwargs: Judge())
    return closed


def test_probe_releases_sql_transaction_before_provider_io(connection_table, monkeypatch):
    table = connection_table

    def during_probe():
        assert not table.s.in_transaction(), "provider I/O must not hold a SQL transaction"

    closed = install_probe(monkeypatch, during_probe)
    result = model_connection.test_model_connection("m-probe-race", table)
    assert result.status == "available"
    assert result.test_error is None
    assert closed == [True]


@pytest.mark.parametrize("change", ["model", "endpoint", "key", "aba", "delete"])
def test_probe_cannot_overwrite_an_edited_or_deleted_connection(
    change, connection_table, monkeypatch
):
    table = connection_table

    def during_probe():
        if change == "model":
            table.update_by_uuid("m-probe-race", {"model_name": "replacement"})
        elif change == "endpoint":
            table.update_by_uuid("m-probe-race", {"endpoint": "https://new.test/v1"})
        elif change in {"key", "aba"}:
            table.update_by_uuid("m-probe-race", {"api_key": "replacement"})
            if change == "aba":
                table.update_by_uuid("m-probe-race", {"api_key": "inert-probe-key"})
        else:
            table.delete_by_uuid("m-probe-race")

    closed = install_probe(monkeypatch, during_probe)
    with pytest.raises(Conflict, match="changed during"):
        model_connection.test_model_connection("m-probe-race", table)
    current = table.get_by_uuid("m-probe-race")
    assert current is None or current.status == ModelConnectionStatus.UNTESTED
    assert closed == [True]


@pytest.mark.parametrize(
    "key",
    ["bad\nkey", "密钥", "bad key", "x" * 8193],
    ids=["newline", "unicode", "space", "too-long"],
)
def test_probe_rejects_malformed_saved_credentials_without_client(
    key, connection_table, monkeypatch
):
    connection_table.update_by_uuid("m-probe-race", {"api_key": key})

    def no_client(*args, **kwargs):
        pytest.fail("malformed keys must not create a provider client")

    monkeypatch.setattr(model_connection, "LLMJudge", no_client)
    result = model_connection.test_model_connection("m-probe-race", connection_table)
    assert result.status == "unavailable"
    assert result.test_error == "invalid_request"
    assert key not in str(result.model_dump())
