"""Qualification uses image-owned manifest plus read-only immutable image metadata."""

import hashlib
import json
from dataclasses import replace

import pytest
from test_runtime import create_request

from agentlens.sandbox_plugin.runtime import LeaseManager, PluginError

IMAGE = "test/worker@sha256:" + "a" * 64


def runtime_manifest():
    value = {
        "id": "python-eval-v1",
        "python_version": "3.11.14",
        "packages": {
            "pytest": "8.4.2",
            "numpy": "2.3.4",
            "pandas": "2.3.3",
            "pydantic": "2.12.3",
            "jsonschema": "4.25.1",
            "openpyxl": "3.1.5",
        },
    }
    value["manifest_sha256"] = hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return value


def enhanced_manager(setup, tmp_path):
    settings, provider, verifier, old = setup
    old.close()
    manifest = runtime_manifest()
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    settings = replace(
        settings, image_allowlist=(IMAGE,), enhanced_image=IMAGE, runtime_manifest_path=path
    )
    verifier.image_metadata = lambda image: {
        "RepoDigests": [IMAGE],
        "Config": {
            "Labels": {"io.aivision.rule-runtime.manifest-sha256": manifest["manifest_sha256"]}
        },
    }
    return LeaseManager(settings, provider, verifier), provider, verifier, manifest


def test_unqualified_plugin_has_no_enhanced_capability(setup):
    assert setup[3].runtime_manifests() == []


def test_qualified_manifest_and_exact_image_only(setup, tmp_path):
    manager, _, verifier, manifest = enhanced_manager(setup, tmp_path)
    try:
        assert manager.runtime_manifests() == [manifest | {"image": IMAGE}]
        verifier.image_metadata = lambda _: {
            "RepoDigests": [IMAGE],
            "Config": {"Labels": {"io.aivision.rule-runtime.manifest-sha256": "wrong"}},
        }
        assert manager.runtime_manifests() == []
        with pytest.raises(PluginError):
            manager.create(
                create_request(
                    image=IMAGE,
                    rule_runtime="python-eval-v1",
                    rule_runtime_manifest=manifest["manifest_sha256"],
                )
            )
    finally:
        manager.close()


def test_enhanced_execute_fixed_dispatch_and_lease_identity(setup, tmp_path):
    manager, provider, _, manifest = enhanced_manager(setup, tmp_path)
    try:
        manager.create(
            create_request(
                image=IMAGE,
                rule_runtime="python-eval-v1",
                rule_runtime_manifest=manifest["manifest_sha256"],
            )
        )
        request = {
            "code": "def evaluate(trace, context): return {}",
            "trace": {},
            "timeout_seconds": 1,
            "rule_runtime": "python-eval-v1",
            "rule_runtime_manifest": manifest["manifest_sha256"],
            "materials": {"files": [{"path": "test.py", "encoding": "utf-8", "content": "x=1"}]},
        }
        result = manager.execute("a" * 64, request)
        assert result["cleanup_verified"] is True
        assert provider.command == ["/app/rule-runtime/enhanced-worker"]
        assert json.loads(provider.stdin)["materials"]["files"][0]["sha256"]
    finally:
        manager.close()


def test_runtime_change_rejected_before_admission_and_exec(setup, tmp_path):
    manager, provider, _, manifest = enhanced_manager(setup, tmp_path)
    try:
        for value in ("unknown", None):
            with pytest.raises(PluginError):
                manager.create(
                    create_request(
                        image=IMAGE,
                        rule_runtime=value,
                        rule_runtime_manifest=manifest["manifest_sha256"],
                    )
                )
        assert "create" not in provider.calls
        manager.create(create_request(image=IMAGE))
        with pytest.raises(PluginError):
            manager.execute(
                "a" * 64,
                {
                    "code": "x",
                    "trace": {},
                    "timeout_seconds": 1,
                    "rule_runtime": "python-eval-v1",
                    "rule_runtime_manifest": manifest["manifest_sha256"],
                    "materials": {"files": []},
                },
            )
        assert "execute" not in provider.calls
    finally:
        manager.close()


def test_legacy_request_cannot_inject_materials(setup):
    manager = setup[3]
    manager.create(create_request())
    with pytest.raises(PluginError):
        manager.execute(
            "a" * 64, {"code": "x", "trace": {}, "timeout_seconds": 1, "materials": {"files": []}}
        )


def test_health_advertises_only_qualified_inventory_without_creating_workload(setup, tmp_path):
    from fastapi.testclient import TestClient

    from agentlens.sandbox_plugin.main import build_plugin_app

    manager, provider, verifier, manifest = enhanced_manager(setup, tmp_path)
    settings = manager.settings
    manager.close()
    with TestClient(build_plugin_app(settings, provider=provider, verifier=verifier)) as client:
        client.headers["Authorization"] = "Bearer test-token"
        health = client.get("/v1/health").json()
        assert "python_eval_v1" in health["capabilities"]
        assert health["runtime_manifests"] == [manifest | {"image": IMAGE}]
        assert "create" not in provider.calls and "execute" not in provider.calls
        verifier.image_metadata = lambda _: {
            "RepoDigests": ["wrong"],
            "Config": {
                "Labels": {"io.aivision.rule-runtime.manifest-sha256": manifest["manifest_sha256"]}
            },
        }
        health = client.get("/v1/health").json()
        assert "python_eval_v1" not in health["capabilities"]
        assert health["runtime_manifests"] == []


def test_image_inspection_is_bounded_get_on_exact_reference(setup):
    import httpx

    from agentlens.sandbox_plugin.provider import DockerCleanupVerifier

    calls = []

    def reply(request):
        calls.append(request)
        return httpx.Response(200, json={"RepoDigests": [IMAGE], "Config": {"Labels": {}}})

    verifier = DockerCleanupVerifier(setup[0], transport=httpx.MockTransport(reply))
    try:
        assert verifier.image_metadata(IMAGE)["RepoDigests"] == [IMAGE]
        assert len(calls) == 1 and calls[0].method == "GET"
        assert calls[0].url.path == "/images/" + IMAGE + "/json"
    finally:
        verifier.close()


def test_enhanced_transport_retains_trace_date_encoding(setup, tmp_path):
    from datetime import date

    manager, provider, _, manifest = enhanced_manager(setup, tmp_path)
    try:
        manager.create(
            create_request(
                image=IMAGE,
                rule_runtime="python-eval-v1",
                rule_runtime_manifest=manifest["manifest_sha256"],
            )
        )
        manager.execute(
            "a" * 64,
            {
                "code": "def evaluate(trace): return {}",
                "trace": {"date": date(2026, 11, 3)},
                "timeout_seconds": 1,
                "rule_runtime": "python-eval-v1",
                "rule_runtime_manifest": manifest["manifest_sha256"],
            },
        )
        assert json.loads(provider.stdin)["trace"]["date"] == "2026-11-03"
    finally:
        manager.close()
