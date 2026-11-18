import json
from dataclasses import replace
from datetime import datetime

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentlens.domain.enums import ModelConnectionStatus
from agentlens.domain.model_connection import ModelConnection
from agentlens.infra.llm_judge import JudgeUnavailable, LLMJudge
from agentlens.web.deps import get_model_connection_table

_API_KEY = "sk-model-connection-test-secret"


def _create(client, *, space_uuid="s", name="judge"):
    response = client.post(
        "/api/v1/model-connection",
        json={
            "space_uuid": space_uuid,
            "name": name,
            "endpoint": "http://judge/v1",
            "model_name": "judge-model",
            "api_key": _API_KEY,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_model_connection_crud_status_and_secret_redaction(client, monkeypatch):
    created = _create(client)
    uuid = created["uuid"]
    assert uuid.startswith("m-")
    assert created["status"] == "untested"
    assert created["api_key_configured"] is True
    assert _API_KEY not in str(created)

    listed = client.get("/api/v1/model-connections", params={"space_uuid": "s"})
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert _API_KEY not in listed.text

    class FakeJudge:
        def probe(self):
            return None

        def close(self):
            return None

    monkeypatch.setattr(
        "agentlens.web.routes.model_connection.LLMJudge",
        lambda *_args, **_kwargs: FakeJudge(),
    )
    tested = client.post(f"/api/v1/model-connection/{uuid}/test")
    assert tested.status_code == 200
    assert tested.json()["status"] == "available"
    assert tested.json()["test_error"] is None
    assert _API_KEY not in tested.text

    updated = client.patch(
        f"/api/v1/model-connection/{uuid}",
        json={"endpoint": "http://judge-new/v1", "api_key": _API_KEY},
    )
    assert updated.status_code == 200
    assert updated.json()["status"] == "untested"
    assert updated.json()["endpoint"] == "http://judge-new/v1"


def test_model_connection_failed_test_marks_unavailable(client, monkeypatch):
    connection = _create(client, name="failed-judge")

    class FakeJudge:
        def probe(self):
            raise JudgeUnavailable("down")

        def close(self):
            return None

    monkeypatch.setattr(
        "agentlens.web.routes.model_connection.LLMJudge",
        lambda *_args, **_kwargs: FakeJudge(),
    )
    tested = client.post(f"/api/v1/model-connection/{connection['uuid']}/test")
    assert tested.status_code == 200
    assert tested.json()["status"] == "unavailable"
    assert tested.json()["test_error"] == "unavailable"


def test_referenced_model_connection_cannot_be_deleted(client):
    connection = _create(client, name="referenced-judge")
    diagnoser = client.post(
        "/api/v1/diagnoser",
        json={
            "space_uuid": "s",
            "agent_uuid": "a",
            "name": "uses-judge",
            "type": "custom",
            "mode": "llm",
            "spec": {
                "prompt": "判断",
                "metrics": [{"name": "quality"}],
                "model_connection_uuid": connection["uuid"],
            },
        },
    )
    assert diagnoser.status_code == 201, diagnoser.text

    references = client.get(f"/api/v1/model-connection/{connection['uuid']}/references")
    assert references.status_code == 200
    assert references.json()["items"][0]["name"] == "uses-judge"

    deleted = client.delete(f"/api/v1/model-connection/{connection['uuid']}")
    assert deleted.status_code == 409
    assert "uses-judge" in deleted.json()["msg"]


def test_unreferenced_model_connection_can_be_deleted(client):
    connection = _create(client, name="deletable-judge")
    deleted = client.delete(f"/api/v1/model-connection/{connection['uuid']}")
    assert deleted.status_code == 204
    assert client.get(f"/api/v1/model-connection/{connection['uuid']}").status_code == 404


@pytest.mark.parametrize(
    ("result", "test_error"),
    [
        (200, None),
        (400, "invalid_request"),
        (401, "authentication_failed"),
        (403, "permission_denied"),
        (404, "model_not_found"),
        (402, "quota_exceeded"),
        (429, "rate_limited"),
        (500, "unavailable"),
        ("timeout", "timeout"),
        ("connection", "connection_failed"),
    ],
)
def test_bounded_probe_uses_one_real_judge_request_and_preserves_status(
    result, test_error, monkeypatch
):
    from agentlens.web.routes import model_connection

    connection = ModelConnection(
        uuid="m-probe",
        space_uuid="s",
        name="probe",
        endpoint="http://judge.test/v1",
        model_name="judge-model",
        api_key=_API_KEY,
        status=ModelConnectionStatus.UNTESTED,
        gmt_create=datetime.now(),
        gmt_modified=datetime.now(),
        creator=None,
        modifier=None,
    )

    class Table:
        def get_by_uuid(self, uuid):
            assert uuid == connection.uuid
            return connection

        def connectivity_snapshot(self, uuid):
            return self.get_by_uuid(uuid)

        def save_test_status(self, snapshot, status):
            nonlocal connection
            assert snapshot == connection
            connection = replace(connection, status=status)
            return True

    requests = []
    judges = []

    def transport(request):
        requests.append(request)
        if result == 200 and "enable_thinking" in json.loads(request.content):
            return httpx.Response(
                400,
                json={
                    "error": {
                        "message": "Validation: Unsupported parameter(s): `enable_thinking`",
                        "type": "Bad Request",
                        "code": 400,
                    }
                },
            )
        if result == "timeout":
            raise httpx.ReadTimeout(_API_KEY, request=request)
        if result == "connection":
            raise httpx.ConnectError(_API_KEY, request=request)
        body = (
            {"choices": [{"message": {"content": "pong"}}]}
            if result == 200
            else {"error": {"message": _API_KEY}}
        )
        return httpx.Response(result, json=body)

    def build_judge(*args, **kwargs):
        judge = LLMJudge(*args, **kwargs, backoff_sec=0, transport=httpx.MockTransport(transport))
        judges.append(judge)
        return judge

    monkeypatch.setattr(model_connection, "LLMJudge", build_judge)
    app = FastAPI()
    app.include_router(model_connection.router)
    app.dependency_overrides[get_model_connection_table] = Table
    api = TestClient(app)
    response = api.post("/api/v1/model-connection/m-probe/test")
    assert response.status_code == 200
    assert response.json()["status"] == ("available" if result == 200 else "unavailable")
    assert response.json()["test_error"] == test_error
    assert "test_error" not in api.get("/api/v1/model-connection/m-probe").json()
    assert _API_KEY not in response.text
    assert len(requests) == 1
    assert requests[0].extensions["timeout"] == dict.fromkeys(
        ("connect", "read", "write", "pool"), 180
    )
    assert requests[0].method == "POST"
    assert str(requests[0].url) == "http://judge.test/v1/chat/completions"
    assert json.loads(requests[0].content) == {
        "model": "judge-model",
        "messages": [{"role": "user", "content": "ping"}],
        "temperature": 0,
        "max_tokens": 1,
    }
    assert judges[0]._client.is_closed
    assert judges[0].enable_thinking is None


def test_incomplete_saved_connection_returns_safe_reason_without_request(monkeypatch):
    from agentlens.web.routes import model_connection

    connection = ModelConnection(
        uuid="m-incomplete",
        space_uuid="s",
        name="incomplete",
        endpoint="https://judge.test/v1",
        model_name="model",
        api_key="",
        status=ModelConnectionStatus.UNTESTED,
        gmt_create=datetime.now(),
        gmt_modified=datetime.now(),
        creator=None,
        modifier=None,
    )

    class Table:
        def get_by_uuid(self, uuid):
            return connection

        def connectivity_snapshot(self, uuid):
            return self.get_by_uuid(uuid)

        def save_test_status(self, snapshot, status):
            nonlocal connection
            assert snapshot == connection
            assert status == ModelConnectionStatus.UNAVAILABLE
            connection = replace(connection, status=status)
            return True

    def no_judge(*args, **kwargs):
        pytest.fail("Incomplete saved configuration must not construct a provider client")

    monkeypatch.setattr(model_connection, "LLMJudge", no_judge)
    result = model_connection.test_model_connection("m-incomplete", Table())
    assert result.status == "unavailable"
    assert result.test_error == "invalid_request"
