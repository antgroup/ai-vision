"""The plugin rejects the final input before admitting any resources."""

import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from agentlens.sandbox_plugin.main import build_plugin_app
from agentlens.sandbox_plugin.runtime import PluginError
from tests.unit.sandbox_plugin.test_enhanced_runtime import IMAGE, enhanced_manager
from tests.unit.sandbox_plugin.test_runtime import create_request


@pytest.mark.parametrize("count,status", [(100, 200), (700, 413)])
def test_preflight_counts_utf8_bytes_without_creating_resources(setup, count, status):
    settings, provider, verifier, manager = setup
    manager.close()
    settings = replace(settings, max_input_bytes=2048)
    app = build_plugin_app(settings, provider=provider, verifier=verifier)
    payload = {
        "create": create_request(),
        "execute": {
            "code": "def evaluate(trace): return {}",
            "trace": {"text": "中" * count},
            "timeout_seconds": 1,
        },
    }
    with TestClient(app) as client:
        response = client.post(
            "/v1/executions/validate",
            json=payload,
            headers={"Authorization": "Bearer " + settings.token},
        )
        assert response.status_code == status
        if status == 200:
            assert response.json()["valid"] is True
            assert response.json()["input_bytes"] > count * 3
        else:
            assert response.json()["code"] == "SANDBOX_INPUT_TOO_LARGE"
            assert response.json()["details"]["actual_bytes"] > 2048
            assert response.json()["details"]["allowed_bytes"] == 2048
        assert app.state.manager.lease_status()["active_leases"] == 0
        assert provider.calls == []


def test_normalized_material_expansion_cannot_exceed_worker_reader(setup, tmp_path):
    manager, provider, _, manifest = enhanced_manager(setup, tmp_path)
    runtime = {
        "rule_runtime": "python-eval-v1",
        "rule_runtime_manifest": manifest["manifest_sha256"],
    }
    payload = {
        "create": create_request(image=IMAGE, **runtime),
        "execute": {
            **runtime,
            "code": "def evaluate(trace, context): return {}",
            "timeout_seconds": 1,
            "trace": {"padding": "x" * 2094400},
            "materials": {
                "files": [
                    {"path": f"{i}.txt", "encoding": "utf-8", "content": "x"} for i in range(20)
                ]
            },
        },
    }
    assert len(json.dumps(payload).encode()) < 2097152
    try:
        with pytest.raises(PluginError) as raised:
            manager.validate_execution(payload)
        assert raised.value.code == "SANDBOX_INPUT_TOO_LARGE"
        assert raised.value.details["stage"] == "worker_stdin"
        assert raised.value.details["actual_bytes"] > 2097152
        assert manager.lease_status()["active_leases"] == 0
        assert provider.calls == []
    finally:
        manager.close()
