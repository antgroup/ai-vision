"""Environment health uses the workload kernel, never a parent host guess."""

import json

import pytest

from agentlens.sandbox_plugin import environment


@pytest.mark.parametrize(
    "version,kernel,local,abi,code",
    [
        ("26.1.3", "5.10", "5.10", 0, "docker_version_unsupported"),
        ("29.9.3", "6.8", "6.8", 0, "landlock_unavailable"),
        ("28.0.0", "6.1", "6.1", 2, "landlock_abi_unsupported"),
        ("29.9.3", "6.8", "5.10", 0, "environment_probe_failed"),
        ("29.9.3", "6.8", "", 4, "environment_probe_failed"),
        ("28.0.0", "5.10-backport", "5.10-backport", 3, None),
    ],
)
def test_actual_daemon_kernel_and_active_abi(monkeypatch, version, kernel, local, abi, code):
    monkeypatch.setattr(
        environment,
        "_daemon_info",
        lambda _: {"ServerVersion": version, "KernelVersion": kernel, "OSType": "linux"},
    )
    monkeypatch.setattr(environment, "_local_capability", lambda: (local, abi))
    result = environment.probe_environment("/not-exposed/docker.sock")
    if code:
        assert result["status"] == "failed"
        assert result["diagnostic"]["code"] == code
    else:
        assert result == {"status": "passed", "docker_version": version, "landlock_abi": abi}


def test_probe_failure_never_returns_exception_or_paths(monkeypatch):
    def broken(_):
        raise RuntimeError("credential SECRET /private/docker.sock")

    monkeypatch.setattr(environment, "_daemon_info", broken)
    result = environment.probe_environment("/private/docker.sock")
    assert result == {"status": "failed", "diagnostic": {"code": "environment_probe_failed"}}
    assert "SECRET" not in json.dumps(result)


def test_authenticated_health_adds_prerequisite_facts_without_claiming_execution(
    setup, monkeypatch
):
    from fastapi.testclient import TestClient

    from agentlens.sandbox_plugin.main import build_plugin_app

    settings, provider, verifier, manager = setup
    manager.close()
    facts = {
        "status": "failed",
        "diagnostic": {"code": "landlock_unavailable", "actual": "0", "required": "3"},
    }
    monkeypatch.setattr("agentlens.sandbox_plugin.main.probe_environment", lambda _: facts)
    with TestClient(build_plugin_app(settings, provider=provider, verifier=verifier)) as client:
        assert client.get("/v1/health").status_code == 401
        client.headers["Authorization"] = "Bearer test-token"
        health = client.get("/v1/health").json()
        assert health["environment"] == facts
        assert health["ready"] is True
        assert provider.calls == []
        assert health["active_leases"] == 0
