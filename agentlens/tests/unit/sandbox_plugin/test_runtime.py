"""Task-owned leases fail closed across retries, timeouts, and restarts."""

import json
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from agentlens.sandbox_plugin.main import build_plugin_app
from agentlens.sandbox_plugin.runtime import LeaseManager, PluginError


def create_request(**changes):
    data = dict(
        lease_id="a" * 64,
        connection_uuid="connection-a",
        space_uuid="space-a",
        task_uuid="task-a",
        attempt=0,
        diagnoser_uuid="diagnoser-a",
        image="test/worker:v1",
        cpu=1,
        memory="512Mi",
        timeout_seconds=5,
    )
    return data | changes


def test_idempotent_create_and_fingerprint_ownership(setup):
    _, provider, _, manager = setup
    first = manager.create(create_request())
    assert first["state"] == "ready"
    assert manager.create(create_request()) == first
    assert provider.calls.count("create") == 1
    for field, value in [("space_uuid", "other"), ("attempt", 1), ("image", "other")]:
        with pytest.raises(PluginError):
            manager.create(create_request(**{field: value}))
    assert len(manager.get("a" * 64)["sandbox_name"]) <= 19


def test_timeout124_always_tears_down_whole_sandbox_before_return(setup):
    _, provider, verifier, manager = setup
    provider.result = SimpleNamespace(exit_code=124, stdout="partial", stderr="", truncated=False)
    manager.create(create_request())
    result = manager.execute(
        "a" * 64, {"code": "def evaluate(trace): return {}", "trace": {}, "timeout_seconds": 1}
    )
    assert result["timed_out"] is True
    assert result["cleanup_verified"] is True
    assert provider.deleted_processes and verifier.checked
    assert provider.calls[-2:] == ["delete", "wait_deleted"]
    assert manager.get("a" * 64)["state"] == "closed"
    with pytest.raises(PluginError):
        manager.execute("a" * 64, {"code": "pass", "trace": {}, "timeout_seconds": 1})
    with pytest.raises(PluginError):
        manager.create(create_request())


def test_worker_input_only_stdin_operator_command_and_no_journal_payload(setup):
    settings, provider, _, manager = setup
    manager.create(create_request())
    code = "def evaluate(trace): return {'private': trace['private']}"
    manager.execute(
        "a" * 64, {"code": code, "trace": {"private": "sensitive-value"}, "timeout_seconds": 1.5}
    )
    assert provider.command == list(settings.worker_command)
    payload = json.loads(provider.stdin)
    assert payload["code"] == code and payload["trace"] == {"private": "sensitive-value"}
    assert b"sensitive-value" not in settings.journal_path.read_bytes()
    assert b"def evaluate" not in settings.journal_path.read_bytes()


@pytest.mark.parametrize("failure", ["exec_error", "fail_ready", "fail_wait_deleted"])
def test_errors_never_expose_provider_detail_and_teardown_is_attempted(setup, failure):
    _, provider, _, manager = setup
    setattr(provider, failure, True)
    if failure == "fail_ready":
        with pytest.raises(PluginError) as exc:
            manager.create(create_request())
    else:
        manager.create(create_request())
        if failure == "exec_error":
            with pytest.raises(PluginError) as exc:
                manager.execute("a" * 64, {"code": "pass", "trace": {}, "timeout_seconds": 1})
        else:
            result = manager.execute("a" * 64, {"code": "pass", "trace": {}, "timeout_seconds": 1})
            assert not result["cleanup_verified"]
    assert "delete" in provider.calls
    if failure != "fail_wait_deleted":
        assert "SECRET" not in str(exc.value)


def test_cleanup_pending_holds_capacity_until_independent_readback(setup):
    _, provider, verifier, manager = setup
    manager.create(create_request())
    verifier.clear = False
    assert manager.delete("a" * 64) == {
        "lease_id": "a" * 64,
        "state": "cleanup_pending",
        "cleanup_verified": False,
    }
    with pytest.raises(PluginError) as exc:
        manager.create(create_request(lease_id="b" * 64))
    assert exc.value.code == "SANDBOX_CAPACITY_EXHAUSTED"
    verifier.clear = True
    manager.janitor_once()
    assert manager.get("a" * 64)["state"] == "closed"
    manager.create(create_request(lease_id="b" * 64))
    assert provider.calls.count("create") == 2


def test_lost_create_response_recovers_immutable_owned_id_then_cleans(setup):
    _, provider, verifier, manager = setup
    provider.fail_create = True
    with pytest.raises(PluginError):
        manager.create(create_request())
    assert provider.deleted_processes and verifier.checked
    assert manager.get("a" * 64)["sandbox_id"].startswith("immutable-")
    assert manager.get("a" * 64)["state"] == "closed"


def test_replacement_with_same_name_is_not_deleted(setup):
    _, provider, verifier, manager = setup
    first = manager.create(create_request())
    ref = next(iter(provider.sandboxes.values()))
    ref.id = "another-owner-id"
    result = manager.delete("a" * 64)
    assert not result["cleanup_verified"] and result["state"] == "cleanup_pending"
    assert "delete" not in provider.calls
    assert not verifier.checked
    assert manager.get("a" * 64)["sandbox_id"] == first["sandbox_id"]


def test_restart_reclaims_ready_and_interrupted_leases(setup):
    settings, provider, verifier, manager = setup
    manager.create(create_request())
    manager.close()
    replacement = LeaseManager(settings, provider, verifier)
    try:
        replacement.recover_startup()
        assert replacement.get("a" * 64)["state"] == "closed"
        assert provider.deleted_processes
    finally:
        replacement.close()


def test_concurrent_execution_and_janitor_do_not_delete_active_attempt(setup):
    _, provider, _, manager = setup
    provider.release = threading.Event()
    manager.create(create_request())
    result = []
    worker = threading.Thread(
        target=lambda: result.append(
            manager.execute("a" * 64, {"code": "pass", "trace": {}, "timeout_seconds": 1})
        )
    )
    worker.start()
    assert provider.entered.wait(2)
    manager.janitor_once()
    assert "delete" not in provider.calls
    with pytest.raises(PluginError):
        manager.execute("a" * 64, {"code": "pass", "trace": {}, "timeout_seconds": 1})
    provider.release.set()
    worker.join(2)
    assert result[0]["cleanup_verified"] is True


def test_http_auth_body_limits_safe_errors_and_liveness(setup):
    settings, provider, verifier, manager = setup
    manager.close()
    app = build_plugin_app(settings, provider=provider, verifier=verifier)
    with TestClient(app) as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/v1/health").status_code == 401
        client.headers["Authorization"] = "Bearer test-token"
        health = client.get("/v1/health").json()
        assert health["ready"] and "custom_rule" in health["capabilities"]
        bad = client.post("/v1/sandboxes", json=create_request(unknown="SECRET"))
        assert bad.status_code == 422 and "SECRET" not in bad.text
        created = client.post("/v1/sandboxes", json=create_request())
        assert created.status_code == 200
        provider.exec_error = True
        failed = client.post(
            "/v1/sandboxes/" + "a" * 64 + "/execute",
            json={"code": "private-code", "trace": {}, "timeout_seconds": 1},
        )
        assert failed.status_code == 502 and "SECRET" not in failed.text
        assert client.delete("/v1/sandboxes/" + "a" * 64).json()["cleanup_verified"]
        assert client.get("/v1/sandboxes/" + "a" * 64).json()["state"] == "closed"


def test_settings_reject_root_network_policy_and_empty_operator_token(setup):
    settings, _, _, _ = setup
    with pytest.raises(ValueError):
        replace(settings, token="")
    settings.policy_path.write_text(
        json.dumps(
            {
                "version": 1,
                "process": {"run_as_user": "0", "run_as_group": "0"},
                "network_policies": {"allow": {}},
            }
        )
    )
    with pytest.raises(ValueError):
        settings.load_policy()


def test_concurrent_create_never_allocates_beyond_capacity(setup):
    _, provider, _, manager = setup
    barrier = threading.Barrier(3)
    results = []

    def allocate(lease_id):
        barrier.wait()
        try:
            results.append(manager.create(create_request(lease_id=lease_id)))
        except PluginError as error:
            results.append(error.code)

    threads = [
        threading.Thread(target=allocate, args=(lease_id,)) for lease_id in ("a" * 64, "b" * 64)
    ]
    for worker in threads:
        worker.start()
    barrier.wait()
    for worker in threads:
        worker.join(3)
    assert provider.calls.count("create") == 1
    assert "SANDBOX_CAPACITY_EXHAUSTED" in results


def test_unknown_immutable_id_is_pending_and_retains_slot(setup):
    _, provider, verifier, manager = setup

    def lost(*args):
        raise RuntimeError("response lost before immutable identity known")

    provider.create = lost
    with pytest.raises(PluginError):
        manager.create(create_request())
    assert manager.get("a" * 64)["state"] == "cleanup_pending"
    assert not verifier.checked
    manager.janitor_once()
    with pytest.raises(PluginError) as error:
        manager.create(create_request(lease_id="b" * 64))
    assert error.value.code == "SANDBOX_CAPACITY_EXHAUSTED"


def test_late_create_is_recovered_and_ownership_is_checked_on_retry(setup):
    _, provider, verifier, manager = setup
    create = provider.create

    def late(*args):
        raise RuntimeError("response lost")

    provider.create = late
    with pytest.raises(PluginError):
        manager.create(create_request())
    row = manager._row("a" * 64)
    ref = create(row["sandbox_name"], create_request(), manager._labels(row))
    ref.labels = {"aivision.lease": "other-owner", "aivision.fingerprint": row["fingerprint"]}
    manager.janitor_once()
    assert "delete" not in provider.calls and not verifier.checked
    ref.labels = manager._labels(row)
    manager.janitor_once()
    assert manager.get("a" * 64)["state"] == "closed"


def test_janitor_retries_delete_failure_but_does_not_reexecute(setup):
    _, provider, _, manager = setup
    manager.create(create_request())
    provider.fail_delete = True
    result = manager.execute("a" * 64, {"code": "pass", "trace": {}, "timeout_seconds": 1})
    assert not result["cleanup_verified"]
    provider.fail_delete = False
    manager.janitor_once()
    assert manager.get("a" * 64)["state"] == "closed"
    assert provider.calls.count("execute") == 1


def test_janitor_reclaims_expired_ready_but_respects_retry_deadline(setup):
    _, provider, verifier, manager = setup
    manager.create(create_request())
    manager._update("a" * 64, expires_at=0)
    verifier.clear = False
    manager.janitor_once(due_only=True)
    count = provider.calls.count("delete")
    assert count == 1
    manager.janitor_once(due_only=True)
    assert provider.calls.count("delete") == count
    verifier.clear = True
    manager._update("a" * 64, retry_at=0)
    manager.janitor_once(due_only=True)
    assert manager.get("a" * 64)["state"] == "closed"


def test_second_process_owner_cannot_recover_live_journal(setup):
    settings, provider, verifier, manager = setup
    manager.create(create_request())
    with pytest.raises(ValueError, match="active owner"):
        LeaseManager(settings, provider, verifier)
    assert "delete" not in provider.calls


def test_response_output_is_bounded_and_cleanup_happens_for_malformed_worker_output(setup):
    settings, provider, _, manager = setup
    provider.result = SimpleNamespace(
        exit_code=0, stdout="x" * (settings.max_output_bytes + 1), stderr="private", truncated=False
    )
    manager.create(create_request())
    response = manager.execute("a" * 64, {"code": "pass", "trace": {}, "timeout_seconds": 1})
    assert response["truncated"] and response["cleanup_verified"]
    assert (
        len(response["stdout"].encode()) + len(response["stderr"].encode())
        <= settings.max_output_bytes
    )


def test_http_input_bound_and_invalid_json_are_constant_safe_errors(setup):
    settings, provider, verifier, manager = setup
    manager.close()
    settings = replace(settings, max_input_bytes=1024)
    with TestClient(build_plugin_app(settings, provider=provider, verifier=verifier)) as client:
        client.headers["Authorization"] = "Bearer test-token"
        invalid = client.post("/v1/sandboxes", content=b'{"PRIVATE":')
        assert invalid.status_code == 422 and "PRIVATE" not in invalid.text
        large = client.post("/v1/sandboxes", content=b"PRIVATE" * 200)
        assert large.status_code == 413 and "PRIVATE" not in large.text


@pytest.mark.parametrize(
    "field,value",
    [
        ("cpu", True),
        ("attempt", True),
        ("timeout_seconds", float("nan")),
        ("timeout_seconds", float("inf")),
        ("memory", "1024"),
        ("worker_command", ["/evil"]),
        ("image", "unapproved:v1"),
    ],
)
def test_create_rejects_unsafe_profile_without_provider_call(setup, field, value):
    _, provider, _, manager = setup
    with pytest.raises(PluginError):
        manager.create(create_request(**{field: value}))
    assert "create" not in provider.calls


def test_cleanup_total_deadline_cannot_be_reset_by_suboperations(setup, monkeypatch):
    _, provider, verifier, manager = setup
    manager.create(create_request())
    clock = [100.0]
    monkeypatch.setattr("agentlens.sandbox_plugin.runtime.time.monotonic", lambda: clock[0])
    original_delete = provider.delete

    def slow_delete(name, sandbox_id):
        original_delete(name, sandbox_id)
        clock[0] += manager.settings.cleanup_timeout_seconds + 1

    provider.delete = slow_delete
    result = manager.delete("a" * 64)
    assert not result["cleanup_verified"] and result["state"] == "cleanup_pending"
    assert "wait_deleted" not in provider.calls and not verifier.checked


def test_restart_recovers_running_state_without_executing_payload_again(setup):
    settings, provider, verifier, manager = setup
    manager.create(create_request())
    manager._update("a" * 64, state="running")
    manager.close()
    restarted = LeaseManager(settings, provider, verifier)
    try:
        restarted.recover_startup()
        assert restarted.get("a" * 64)["state"] == "closed"
        assert "execute" not in provider.calls
    finally:
        restarted.close()


def test_operator_token_file_is_exclusive_and_not_present_in_errors(setup, monkeypatch, tmp_path):
    from agentlens.sandbox_plugin.config import PREFIX, PluginSettings

    settings, _, _, _ = setup
    token_file = tmp_path / "plugin-token"
    token_file.write_text("file-token-secret\n")
    values = {
        "TOKEN_FILE": str(token_file),
        "JOURNAL_PATH": str(settings.journal_path),
        "IMAGE_ALLOWLIST": json.dumps(settings.image_allowlist),
        "POLICY_PATH": str(settings.policy_path),
        "DOCKER_SOCKET": settings.docker_socket,
        "GATEWAY": settings.gateway,
    }
    monkeypatch.delenv(PREFIX + "TOKEN", raising=False)
    for key, value in values.items():
        monkeypatch.setenv(PREFIX + key, value)
    assert PluginSettings.from_env().token == "file-token-secret"
    monkeypatch.setenv(PREFIX + "TOKEN", "env-token-secret")
    with pytest.raises(ValueError) as error:
        PluginSettings.from_env()
    assert "secret" not in str(error.value)


def test_drain_atomically_stops_admission_and_reports_pending_lease_count(setup):
    settings, provider, verifier, manager = setup
    manager.close()
    with TestClient(build_plugin_app(settings, provider=provider, verifier=verifier)) as client:
        assert client.post("/v1/admin/drain").status_code == 401
        client.headers["Authorization"] = "Bearer test-token"
        assert client.get("/v1/health").json()["active_leases"] == 0
        assert client.post("/v1/sandboxes", json=create_request()).status_code == 200
        verifier.clear = False
        client.delete("/v1/sandboxes/" + "a" * 64)
        drained = client.post("/v1/admin/drain").json()
        assert drained["draining"] is True and drained["ready"] is False
        assert drained["active_leases"] == 1
        refused = client.post("/v1/sandboxes", json=create_request(lease_id="b" * 64))
        assert refused.status_code == 409
        verifier.clear = True
        assert client.delete("/v1/sandboxes/" + "a" * 64).json()["cleanup_verified"] is True
        assert client.get("/v1/health").json()["active_leases"] == 0
        assert client.get("/v1/health").json()["ready"] is False


def test_provider_ownership_labels_preserve_full_digest_within_kubernetes_limit(setup):
    import base64
    import hashlib
    import re

    _, provider, _, manager = setup
    manager.create(create_request())
    row = manager._row("a" * 64)
    labels = next(iter(provider.sandboxes.values())).labels
    assert all(
        len(value) <= 63 and re.fullmatch(r"[a-zA-Z0-9_.-]+", value) for value in labels.values()
    )
    assert (
        base64.b32decode(labels["aivision.lease"].upper() + "====")
        == hashlib.sha256(("a" * 64).encode()).digest()
    )
    assert base64.b32decode(labels["aivision.fingerprint"].upper() + "====") == bytes.fromhex(
        row["fingerprint"]
    )


def test_wrong_responsive_docker_daemon_never_marks_created_lease_ready(setup):
    _, provider, verifier, manager = setup
    verifier.present = False
    with pytest.raises(PluginError):
        manager.create(create_request())
    assert "execute" not in provider.calls
    assert "delete" in provider.calls
    state = manager.get("a" * 64)
    assert state["state"] == "cleanup_pending" and not state["cleanup_verified"]
    with pytest.raises(PluginError) as error:
        manager.create(create_request(lease_id="b" * 64))
    assert error.value.code == "SANDBOX_CAPACITY_EXHAUSTED"


def test_daemon_changed_across_restart_retains_slot_until_original_daemon_recovers(setup):
    settings, provider, verifier, manager = setup
    manager.create(create_request())
    verifier.daemon_id = "different-responsive-daemon"
    assert manager.delete("a" * 64)["cleanup_verified"] is False
    manager.close()
    restarted = LeaseManager(settings, provider, verifier)
    try:
        restarted.recover_startup()
        assert restarted.get("a" * 64)["state"] == "cleanup_pending"
        with pytest.raises(PluginError) as error:
            restarted.create(create_request(lease_id="b" * 64))
        assert error.value.code == "SANDBOX_CAPACITY_EXHAUSTED"
        verifier.daemon_id = "test-daemon"
        restarted.janitor_once()
        assert restarted.get("a" * 64)["state"] == "closed"
    finally:
        restarted.close()
