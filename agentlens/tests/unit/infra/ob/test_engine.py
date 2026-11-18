from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import text

from agentlens.infra.ob.engine import _render_sql, make_engine, make_session_factory
from agentlens.logging import init_logging


@pytest.mark.parametrize(
    ("kwargs", "url_query", "expected"),
    [
        ({}, "", 0),
        ({"connect_args": {"autocommit": False}}, "", 0),
        ({"connect_args": {"autocommit": True}}, "", 1),
        ({"isolation_level": "AUTOCOMMIT"}, "", 1),
        ({"execution_options": {"isolation_level": "AUTOCOMMIT"}}, "", 1),
        ({"isolation_level": "READ COMMITTED", "connect_args": {"autocommit": True}}, "", 0),
        ({}, "?autocommit=true", 1),
        ({"connect_args": {"autocommit": False}}, "?autocommit=true", 0),
    ],
)
def test_mysql_physical_connection_sets_real_mode_despite_misreported_status(
    kwargs, url_query, expected
):
    callbacks = {}

    def register(_target, name):
        def capture(callback):
            callbacks[name] = callback
            return callback

        return capture

    class Connection:
        # Models an OceanBase handshake whose protocol status reports disabled
        # while SQL @@autocommit is actually enabled. Driver autocommit(False)
        # would trust this flag and skip SET; public cursor SQL must not.
        reported_autocommit = False
        sql_autocommit = 1
        statements = []
        closed = False

        def cursor(self):
            return self

        def execute(self, statement):
            self.statements.append(statement)
            self.sql_autocommit = int(statement.rsplit("=", 1)[1].strip())

        def close(self):
            self.closed = True

    with (
        patch(
            "agentlens.infra.ob.engine.create_engine",
            return_value=SimpleNamespace(dialect=SimpleNamespace(name="mysql")),
        ),
        patch("agentlens.infra.ob.engine.event.listens_for", side_effect=register),
    ):
        make_engine("mysql+pymysql://fixture/fixture" + url_query, **kwargs)
    assert set(callbacks) == {"connect"}
    connection = Connection()
    callbacks["connect"](connection, None)
    assert connection.sql_autocommit == expected
    assert connection.statements == [f"SET SESSION autocommit = {expected}"]
    assert connection.closed


def test_transaction_initialization_keeps_later_connection_autocommit_override():
    class FakeConnection:
        sql_autocommit = True
        reported_autocommit = False

        def character_set_name(self):
            return "utf8mb4"

        def cursor(self):
            return self

        def execute(self, statement):
            if statement.startswith("SET SESSION autocommit"):
                self.sql_autocommit = bool(int(statement.rsplit("=", 1)[1]))
                self.reported_autocommit = self.sql_autocommit

        def autocommit(self, enabled):
            # Reproduce the driver's status-based optimization.
            if enabled != self.reported_autocommit:
                self.execute(f"SET SESSION autocommit = {int(enabled)}")

        def rollback(self):
            pass

        def commit(self):
            pass

        def close(self):
            pass

    dbapi = FakeConnection()
    engine = make_engine("mysql+pymysql://fixture/fixture", creator=lambda: dbapi)
    engine.dialect.default_isolation_level = "READ COMMITTED"
    try:
        # Skip server discovery only; real SQLAlchemy pool/connect/option/reset events run.
        with patch.object(engine.dialect, "initialize"):
            with engine.connect() as connection:
                assert not dbapi.sql_autocommit
                connection.execution_options(isolation_level="AUTOCOMMIT")
                assert dbapi.sql_autocommit
            assert not dbapi.sql_autocommit
    finally:
        engine.dispose()


def test_engine_executes_select_1():
    eng = make_engine("sqlite:///:memory:")
    with eng.connect() as c:
        assert c.execute(text("SELECT 1")).scalar() == 1


def test_session_factory_returns_independent_sessions():
    eng = make_engine("sqlite:///:memory:")
    sf = make_session_factory(eng)
    s1 = sf()
    s2 = sf()
    try:
        assert s1 is not s2
    finally:
        s1.close()
        s2.close()


def test_render_sql_uses_driver_mogrify():
    class Cursor:
        def mogrify(self, statement, parameters):
            assert statement == "SELECT * FROM t WHERE name=%s"
            assert parameters == ("O'Reilly",)
            return "SELECT * FROM t WHERE name='O\\'Reilly'"

    sql, fallback_parameters = _render_sql(
        Cursor(),
        "SELECT * FROM t WHERE name=%s",
        ("O'Reilly",),
    )

    assert sql == "SELECT * FROM t WHERE name='O\\'Reilly'"
    assert fallback_parameters is None


def test_render_sql_falls_back_when_mogrify_fails():
    class Cursor:
        def mogrify(self, _statement, _parameters):
            raise ValueError("unsupported parameters")

    sql, fallback_parameters = _render_sql(
        Cursor(),
        "SELECT *\nFROM t WHERE name=%s",
        ("value",),
    )

    assert sql == "SELECT * FROM t WHERE name=%s"
    assert fallback_parameters == ("value",)


def test_sql_logging_logs_select_but_not_write_parameters():
    buf = StringIO()
    init_logging(stream=buf, instance_uuid="h/i", level="INFO")
    eng = make_engine("sqlite:///:memory:", sql_log_enabled=True)

    with eng.begin() as connection:
        connection.execute(text("CREATE TABLE sample (value TEXT)"))
        connection.execute(text("INSERT INTO sample(value) VALUES (:value)"), {"value": "secret"})
        assert (
            connection.execute(
                text("SELECT value FROM sample WHERE value = :value"),
                {"value": "visible"},
            ).all()
            == []
        )

    output = buf.getvalue()
    assert "ob.query" in output
    assert "SELECT value FROM sample WHERE value = ?" in output
    assert "parameters=('visible',)" in output
    assert "secret" not in output


def test_sql_logging_is_disabled_by_default():
    buf = StringIO()
    init_logging(stream=buf, instance_uuid="h/i", level="INFO")
    eng = make_engine("sqlite:///:memory:")

    with eng.connect() as connection:
        connection.execute(text("SELECT 1"))

    assert "ob.query" not in buf.getvalue()


def test_mysql_sql_logging_renders_bound_parameters(mysql_url):
    buf = StringIO()
    init_logging(stream=buf, instance_uuid="h/i", level="INFO")
    eng = make_engine(mysql_url, sql_log_enabled=True)

    try:
        with eng.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT :value AS value"),
                    {"value": "O'Reilly"},
                ).scalar()
                == "O'Reilly"
            )
    finally:
        eng.dispose()

    output = buf.getvalue()
    assert "ob.query" in output
    assert "O\\'Reilly" in output
    assert "parameters=" not in output
