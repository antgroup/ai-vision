import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentlens.web.auth import TokenAuthMiddleware


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("AGENTLENS_OB_URL", "x")
    monkeypatch.setenv("AGENTLENS_TOKEN", "secret-123")
    monkeypatch.setenv("AGENTLENS_HOSTNAME", "h")
    monkeypatch.setenv("AGENTLENS_IP", "i")
    monkeypatch.setenv("SIGNOZ_OB_URL", "x")
    monkeypatch.setenv("SIGNOZ_ORG_ID", "x")
    a = FastAPI()
    a.add_middleware(TokenAuthMiddleware, expected_token="secret-123")

    @a.get("/api/v1/ping")
    def ping():
        return {"ok": True}

    @a.get("/internal/healthz")
    def healthz():
        return {"status": "ok"}

    return a


@pytest.fixture
def client(app):
    return TestClient(app)


def test_healthz_open(client):
    assert client.get("/internal/healthz").status_code == 200


def test_api_missing_token_401(client):
    r = client.get("/api/v1/ping")
    assert r.status_code == 401
    assert r.json()["code"] == "unauthorized"
    assert r.json()["msg"] == "missing x-antmonitor-token"


def test_api_wrong_token_403(client):
    r = client.get("/api/v1/ping", headers={"x-antmonitor-token": "wrong"})
    assert r.status_code == 403


def test_api_correct_token_passes(client):
    r = client.get("/api/v1/ping", headers={"x-antmonitor-token": "secret-123"})
    assert r.status_code == 200
