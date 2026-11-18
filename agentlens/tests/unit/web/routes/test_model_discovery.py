"""Model discovery uses real routes/HTTP parsing with inert storage and transport."""

from datetime import datetime

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentlens.domain.enums import ModelConnectionStatus
from agentlens.domain.model_connection import ModelConnection
from agentlens.web.deps import get_model_connection_table
from agentlens.web.errors import install_error_handlers
from agentlens.web.routes import model_connection

KEY = "sk-discovery-private-fixture"
ENDPOINT = "https://api.openai.com/v1"


class Table:
    def __init__(self):
        self.connection = ModelConnection(
            uuid="m-saved",
            space_uuid="space-owned",
            name="Saved connection",
            endpoint=ENDPOINT,
            model_name="original-model",
            api_key=KEY,
            status=ModelConnectionStatus.AVAILABLE,
            gmt_create=datetime.now(),
            gmt_modified=datetime.now(),
            creator=None,
            modifier=None,
        )
        self.writes = []

    def get_in_space(self, uuid, space_uuid):
        if uuid == self.connection.uuid and space_uuid == self.connection.space_uuid:
            return self.connection
        return None

    def get_by_uuid_for_update(self, uuid):
        return self.connection if uuid == self.connection.uuid else None

    def get_by_uuid(self, uuid):
        return self.get_by_uuid_for_update(uuid)

    def update_by_uuid(self, uuid, fields):
        self.writes.append((uuid, fields))


@pytest.fixture
def route_client():
    table = Table()
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(model_connection.router)
    app.dependency_overrides[get_model_connection_table] = lambda: table
    with TestClient(app) as client:
        yield client, table


def test_discovery_rejects_missing_key_without_saving_a_connection(route_client):
    client, table = route_client
    response = client.post(
        "/api/v1/model-connections/discover",
        json={"space_uuid": "space-owned", "endpoint": ENDPOINT},
    )
    assert response.status_code == 422
    assert response.json()["discovery_error"] == "missing_api_key"
    assert table.writes == []


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({"space_uuid": "other-space", "connection_uuid": "m-saved"}, 404),
        (
            {
                "space_uuid": "space-owned",
                "connection_uuid": "m-saved",
                "endpoint": "https://api.deepseek.com",
            },
            422,
        ),
    ],
)
def test_saved_key_cannot_cross_space_or_endpoint(route_client, body, status):
    client, table = route_client
    response = client.post(
        "/api/v1/model-connections/discover",
        json={"endpoint": ENDPOINT, **body},
    )
    assert response.status_code == status
    assert KEY not in response.text
    assert table.writes == []


def test_saved_key_is_used_only_for_same_endpoint_without_status_change(route_client, monkeypatch):
    from agentlens.infra.llm_judge.model_discovery import discover_models

    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"data": [{"id": "chat-fixture"}]})

    monkeypatch.setattr(
        model_connection,
        "discover_models",
        lambda endpoint, api_key: discover_models(
            endpoint, api_key, transport=httpx.MockTransport(respond)
        ),
    )
    client, table = route_client
    response = client.post(
        "/api/v1/model-connections/discover",
        json={
            "space_uuid": "space-owned",
            "connection_uuid": "m-saved",
            "endpoint": f" {ENDPOINT}/ ",
        },
    )
    assert response.status_code == 200
    assert response.json() == {"items": [{"id": "chat-fixture"}], "total": 1}
    assert requests[0].headers["authorization"] == f"Bearer {KEY}"
    assert KEY not in response.text
    assert table.writes == []
    assert table.connection.status == ModelConnectionStatus.AVAILABLE


@pytest.mark.parametrize("api_key", [None, ""])
def test_endpoint_change_requires_replacement_key_on_save(route_client, api_key):
    client, table = route_client
    response = client.patch(
        "/api/v1/model-connection/m-saved",
        json={"endpoint": "http://custom.example/v1", "api_key": api_key},
    )
    assert response.status_code == 422
    assert response.json()["discovery_error"] == "missing_api_key"
    assert table.writes == []


@pytest.mark.parametrize(
    "body",
    [
        {"endpoint": f"{ENDPOINT}/"},
        {"endpoint": "http://custom.example/v1", "api_key": "replacement-fixture"},
    ],
)
def test_equivalent_endpoint_or_replacement_key_remains_editable(route_client, body):
    client, table = route_client
    response = client.patch("/api/v1/model-connection/m-saved", json=body)
    assert response.status_code == 200
    assert len(table.writes) == 1
