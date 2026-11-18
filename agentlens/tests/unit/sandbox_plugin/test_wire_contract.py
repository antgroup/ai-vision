"""Exercise the actual AgentLens HTTP client against the standalone plugin app."""

import json
from datetime import datetime
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from agentlens.domain.sandbox import SandboxConnection, SandboxStatus
from agentlens.infra.sandbox import SandboxClient
from agentlens.sandbox_plugin.main import build_plugin_app


@pytest.mark.parametrize("exit_code,cleanup_clear", [(0, True), (124, True), (0, False)])
def test_core_client_and_plugin_share_auth_lifecycle_and_cleanup_contract(
    setup, exit_code, cleanup_clear
):
    settings, provider, verifier, manager = setup
    manager.close()
    provider.result = SimpleNamespace(
        exit_code=exit_code,
        stdout='{"status":"ok","result":{"metrics":[]}}\n',
        stderr="",
        truncated=False,
    )
    verifier.clear = cleanup_clear
    now = datetime.now()
    connection = SandboxConnection(
        uuid="connection-a",
        space_uuid="space-a",
        name="Synthetic contract",
        provider="openshell",
        endpoint="http://plugin",
        api_key=settings.token,
        image="test/worker:v1",
        cpu=1,
        memory="512Mi",
        timeout_seconds=5,
        config_revision=1,
        status=SandboxStatus.READY,
        gmt_create=now,
        gmt_modified=now,
    )
    application = build_plugin_app(settings, provider=provider, verifier=verifier)
    with TestClient(application) as server:

        def bridge(request):
            response = server.request(
                request.method,
                request.url.path,
                content=request.content,
                headers=dict(request.headers),
            )
            return httpx.Response(
                response.status_code, content=response.content, headers=response.headers
            )

        client = SandboxClient(connection, transport=httpx.MockTransport(bridge))
        try:
            assert client.health()["protocol_version"] == 1
            created = client.create(
                {
                    "lease_id": "synthetic-wire-lease",
                    "connection_uuid": connection.uuid,
                    "space_uuid": connection.space_uuid,
                    "task_uuid": "trial-synthetic-task",
                    "attempt": 0,
                    "diagnoser_uuid": "diagnoser-a",
                    "image": connection.image,
                    "cpu": connection.cpu,
                    "memory": connection.memory,
                    "timeout_seconds": connection.timeout_seconds,
                }
            )
            assert created["sandbox_id"].startswith("immutable-")
            result = client.execute(
                "synthetic-wire-lease",
                {
                    "code": "def evaluate(trace): return {'metrics': []}",
                    "trace": {},
                    "timeout_seconds": 1.5,
                },
            )
            assert result["timed_out"] is (exit_code == 124)
            assert result["cleanup_verified"] is cleanup_clear
            assert json.loads(result["stdout"])["result"] == {"metrics": []}
            assert client.delete("synthetic-wire-lease") is cleanup_clear
            if not cleanup_clear:
                verifier.clear = True
                assert client.delete("synthetic-wire-lease") is True
            assert provider.calls.count("execute") == 1
        finally:
            client.close()
