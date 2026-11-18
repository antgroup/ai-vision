import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentlens.main import build_app


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("AGENTLENS_OB_URL", "x")
    monkeypatch.setenv("AGENTLENS_TOKEN", "secret-test")
    monkeypatch.setenv("AGENTLENS_HOSTNAME", "h")
    monkeypatch.setenv("AGENTLENS_IP", "i")
    monkeypatch.setenv("SIGNOZ_OB_URL", "x")
    monkeypatch.setenv("SIGNOZ_ORG_ID", "x")
    monkeypatch.setenv("ANTCOLLECTOR_ENDPOINT", "x")
    return build_app()


@pytest.fixture
def client(app):
    return TestClient(app)


def test_internal_healthz_returns_200_without_auth(client):
    r = client.get("/internal/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_internal_readyz_reads_database_without_auth(client, monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    statements = []
    event.listen(
        engine,
        "before_cursor_execute",
        lambda _conn, _cursor, statement, *_args: statements.append(statement),
    )
    monkeypatch.setattr("agentlens.main._engine_factory", lambda: sessionmaker(engine))
    try:
        response = client.get("/internal/readyz")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        assert statements == ["SELECT 1"]
    finally:
        engine.dispose()


def test_unavailable_database_fails_readiness_without_affecting_liveness(client, monkeypatch):
    def unavailable():
        raise OperationalError("SELECT 1", {}, RuntimeError("secret-database-password"))

    monkeypatch.setattr("agentlens.main._engine_factory", unavailable)
    response = client.get("/internal/readyz")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
    assert client.get("/internal/healthz").status_code == 200
    assert client.get("/internal/config").status_code == 401
    assert client.get("/internal/readyz/extra").status_code == 401


def test_unknown_path_returns_404(client):
    # 404 happens BEFORE auth middleware filters; but Starlette runs middleware first
    # So with no token, unknown path -> 401, not 404
    r = client.get("/some/garbage", headers={"x-antmonitor-token": "secret-test"})
    assert r.status_code == 404
