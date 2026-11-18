"""Full hydrated traces retain all fields across bounded input admission."""

import json
from dataclasses import replace

import pytest

from agentlens.domain.sandbox import SandboxError
from agentlens.infra.sandbox import encode_request
from agentlens.sandbox_plugin.runtime import PluginError
from tests.unit.sandbox_plugin.test_enhanced_runtime import IMAGE, enhanced_manager
from tests.unit.sandbox_plugin.test_runtime import create_request


def test_client_accepts_complete_three_megabyte_trace_without_dropping_payload():
    content = "中" * 500_000
    value = {"trace": {"spans": [{"payload": content, "attributes.content": content}]}}
    assert json.loads(encode_request(value)) == value


def test_client_enforces_operator_budget_and_hard_ceiling(monkeypatch):
    monkeypatch.setenv("SANDBOX_MAX_INPUT_BYTES", "4096")
    with pytest.raises(SandboxError) as raised:
        encode_request({"value": "x" * 4096})
    assert raised.value.details["allowed_bytes"] == 4096
    monkeypatch.setenv("SANDBOX_MAX_INPUT_BYTES", str(16 * 1024**2 + 1))
    from agentlens.config import SandboxInputSettings

    with pytest.raises(ValueError):
        SandboxInputSettings()


@pytest.mark.parametrize("qualified", [False, True])
def test_only_qualified_worker_accepts_larger_input_without_admission(setup, tmp_path, qualified):
    manager, provider, verifier, manifest = enhanced_manager(setup, tmp_path)
    manager.settings = replace(manager.settings, max_input_bytes=8 * 1024**2)
    original = verifier.image_metadata
    if qualified:

        def metadata(image):
            value = original(image)
            value["Config"]["Labels"]["io.aivision.sandbox.max-input-bytes"] = str(16 * 1024**2)
            return value

        verifier.image_metadata = metadata
    runtime = {
        "rule_runtime": "python-eval-v1",
        "rule_runtime_manifest": manifest["manifest_sha256"],
    }
    value = {
        "create": create_request(image=IMAGE, **runtime),
        "execute": {
            "code": "def evaluate(trace): return {}",
            "trace": {"payload": "x" * 3_000_000},
            "timeout_seconds": 1,
            **runtime,
        },
    }
    try:
        if qualified:
            result = manager.validate_execution(value)
            assert result["valid"] is True
            assert result["input_bytes"] > 3_000_000
            assert result["max_input_bytes"] == 8 * 1024**2
        else:
            with pytest.raises(PluginError) as raised:
                manager.validate_execution(value)
            assert raised.value.code == "SANDBOX_INPUT_TOO_LARGE"
            assert raised.value.details["allowed_bytes"] == 2 * 1024**2
        assert manager.lease_status()["active_leases"] == 0
        assert provider.calls == []
    finally:
        manager.close()


@pytest.mark.parametrize("limit", [8 * 1024**2, 16 * 1024**2])
def test_client_exact_utf8_boundary_and_one_byte_over(limit):
    prefix = {"content": ""}
    padding = limit - len(encode_request(prefix))
    value = {"content": "x" * padding}
    assert len(encode_request(value, max_bytes=limit)) == limit
    value["content"] += "x"
    with pytest.raises(SandboxError) as raised:
        encode_request(value, max_bytes=limit)
    assert raised.value.details == {
        "stage": "request",
        "actual_bytes": limit + 1,
        "allowed_bytes": limit,
    }


@pytest.mark.parametrize("label", [None, "invalid", "16777217", "0", 16777216])
def test_invalid_reader_metadata_stays_at_legacy_limit(setup, tmp_path, label):
    manager, provider, verifier, _ = enhanced_manager(setup, tmp_path)
    verifier.image_metadata = lambda image: {
        "RepoDigests": [IMAGE],
        "Config": {"Labels": {"io.aivision.sandbox.max-input-bytes": label}},
    }
    try:
        assert manager.input_limit(IMAGE) == 2097152
        assert manager.input_limit("test/worker:mutable") == 2097152
        assert provider.calls == []
    finally:
        manager.close()


@pytest.mark.parametrize(
    "health,expected",
    [
        ({}, 2097152),
        ({"max_input_bytes": 8388608}, 2097152),
        (
            {
                "max_input_bytes": 8388608,
                "input_limits": [{"image": IMAGE, "max_input_bytes": 16777216}],
            },
            8388608,
        ),
        (
            {
                "max_input_bytes": 4096,
                "input_limits": [{"image": IMAGE, "max_input_bytes": 16777216}],
            },
            4096,
        ),
    ],
)
def test_effective_budget_preserves_old_plugins_and_lower_operator_limits(health, expected):
    from agentlens.infra.sandbox import effective_input_limit

    assert effective_input_limit(health, IMAGE, 8388608) == expected
    assert effective_input_limit(health, IMAGE, 1024) == 1024
