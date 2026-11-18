"""Offline contracts for scoped resources, frozen routing and cleanup fencing."""

import importlib.util
import json
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

import httpx
import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentlens.domain.errors import Conflict, NotFound
from agentlens.domain.sandbox import (
    SandboxBinding,
    SandboxConnection,
    SandboxError,
    SandboxExecutionContext,
    SandboxStatus,
)
from agentlens.infra.ob.models import (
    DiagnoserModel,
    SandboxBindingModel,
    SandboxConnectionModel,
    SandboxLeaseModel,
    TaskModel,
)
from agentlens.infra.ob.tables.sandbox_table import SandboxTable
from agentlens.infra.sandbox import MAX_RESPONSE_BYTES, SandboxClient
from agentlens.service.sandbox import resolve_sandbox_credentials, resolve_sandbox_profile
from agentlens.service.sandbox_execution import SandboxExecutionService
from agentlens.web.errors import install_error_handlers
from agentlens.web.routes.sandbox import get_sandbox_table, router

TOKEN = "sandbox-test-secret-never-return"


def _healthy_plugin_response():
    return httpx.Response(
        200,
        json={
            "protocol_version": 1,
            "provider": "openshell",
            "provider_version": "0.1.6",
            "ready": True,
            "capabilities": ["custom_rule"],
        },
    )


def _enrollment_payload():
    from uuid import NAMESPACE_URL, uuid5

    key = "a" * 16
    payload = {
        "space_uuid": "enrollment-space",
        "name": f"OpenShell ({key})",
        "provider": "openshell",
        "endpoint": f"http://aiv-openshell-{key}:8091",
        "api_key": TOKEN,
        "image": "registry.example/worker@sha256:" + "b" * 64,
        "creator": "operator",
        "modifier": "operator",
    }
    uuid = uuid5(NAMESPACE_URL, f"aivision:sandbox:enrollment-space:{key}").hex
    return uuid, payload


def test_enrollment_is_idempotent_verified_and_secret_free(sandbox_sf, monkeypatch):
    monkeypatch.setattr(SandboxClient, "health", lambda self: {"ready": True})
    client = _client(sandbox_sf)
    uuid, payload = _enrollment_payload()
    for _ in range(2):
        response = client.put(f"/api/v1/sandbox-enrollment/{uuid}", json=payload)
        assert response.status_code == 200, response.text
        assert response.json()["uuid"] == uuid
        assert response.json()["status"] == "ready"
        assert TOKEN not in response.text
    with sandbox_sf() as session:
        assert len(SandboxTable(session).list("enrollment-space")) == 1
    for change in (
        {"space_uuid": "other"},
        {"image": "registry.example/new@sha256:" + "c" * 64},
        {"api_key": "replacement-token"},
    ):
        response = client.put(f"/api/v1/sandbox-enrollment/{uuid}", json={**payload, **change})
        assert response.status_code in (409, 422), response.text
        assert TOKEN not in response.text
        assert "replacement-token" not in response.text


def test_enrollment_rejects_name_adoption_mutable_images_and_false_readiness(
    sandbox_sf, monkeypatch
):
    client = _client(sandbox_sf)
    uuid, payload = _enrollment_payload()
    assert (
        client.put(
            f"/api/v1/sandbox-enrollment/{uuid}", json={**payload, "image": "worker:mutable"}
        ).status_code
        == 422
    )
    assert client.put("/api/v1/sandbox-enrollment/" + "a" * 32, json=payload).status_code == 422
    existing = replace(
        _connection(uuid="other-owner", space="enrollment-space"), name=payload["name"]
    )
    _insert(sandbox_sf, existing)
    response = client.put(f"/api/v1/sandbox-enrollment/{uuid}", json=payload)
    assert response.status_code == 409
    with sandbox_sf() as session:
        assert SandboxTable(session).get_in_space(uuid, "enrollment-space") is None


def test_enrollment_retry_after_unready_recovers_same_connection(sandbox_sf, monkeypatch):
    def fail(self):
        raise SandboxError("sandbox_unavailable")

    monkeypatch.setattr(SandboxClient, "health", fail)
    client = _client(sandbox_sf)
    uuid, payload = _enrollment_payload()
    response = client.put(f"/api/v1/sandbox-enrollment/{uuid}", json=payload)
    assert response.status_code == 422
    assert TOKEN not in response.text
    monkeypatch.setattr(SandboxClient, "health", lambda self: {"ready": True})
    assert (
        client.put(f"/api/v1/sandbox-enrollment/{uuid}", json=payload).json()["status"] == "ready"
    )


def test_enrollment_enable_tests_actual_provider_and_preserves_required_policy(
    sandbox_sf, monkeypatch
):
    monkeypatch.setattr(SandboxClient, "health", lambda self: {"ready": True})
    client = _client(sandbox_sf)
    uuid, payload = _enrollment_payload()
    assert client.put(f"/api/v1/sandbox-enrollment/{uuid}", json=payload).status_code == 200
    with sandbox_sf() as session:
        SandboxTable(session).save_binding(SandboxBinding("enrollment-space", None, True))
    for _ in range(2):
        response = client.post(
            f"/api/v1/sandbox-enrollment/{uuid}/enable",
            json={"space_uuid": "enrollment-space", "modifier": "operator"},
        )
        assert response.status_code == 200, response.text
        assert response.json() == {
            "space_uuid": "enrollment-space",
            "connection_uuid": uuid,
            "require_sandbox": True,
        }
        assert TOKEN not in response.text


def test_enrollment_enable_fences_profile_change_during_actual_probe(sandbox_sf, monkeypatch):
    monkeypatch.setattr(SandboxClient, "health", lambda self: {"ready": True})
    client = _client(sandbox_sf)
    uuid, payload = _enrollment_payload()
    assert client.put(f"/api/v1/sandbox-enrollment/{uuid}", json=payload).status_code == 200

    def changed_while_probing(self):
        with sandbox_sf() as session:
            SandboxTable(session).update(uuid, "enrollment-space", {"image": "changed:approved"})
        return {"ready": True}

    monkeypatch.setattr(SandboxClient, "health", changed_while_probing)
    response = client.post(
        f"/api/v1/sandbox-enrollment/{uuid}/enable",
        json={"space_uuid": "enrollment-space", "modifier": "operator"},
    )
    assert response.status_code == 409
    with sandbox_sf() as session:
        assert SandboxTable(session).get_binding("enrollment-space").connection_uuid is None


def test_enrollment_enable_rejects_stale_pinned_revision_before_provider_request(
    sandbox_sf, monkeypatch
):
    monkeypatch.setattr(SandboxClient, "health", lambda self: {"ready": True})
    client = _client(sandbox_sf)
    uuid, payload = _enrollment_payload()
    assert client.put(f"/api/v1/sandbox-enrollment/{uuid}", json=payload).status_code == 200

    def must_not_probe(self):
        raise AssertionError("A stale plan must not contact the provider")

    monkeypatch.setattr(SandboxClient, "health", must_not_probe)
    response = client.post(
        f"/api/v1/sandbox-enrollment/{uuid}/enable",
        json={
            "space_uuid": "enrollment-space",
            "modifier": "operator",
            "expected_config_revision": 2,
        },
    )
    assert response.status_code == 409
    with sandbox_sf() as session:
        assert SandboxTable(session).get_binding("enrollment-space").connection_uuid is None


@pytest.fixture
def sandbox_sf():
    engine = sa.create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    metadata = sa.MetaData()
    for model in (
        SandboxConnectionModel,
        SandboxBindingModel,
        SandboxLeaseModel,
        DiagnoserModel,
        TaskModel,
    ):
        table = model.__table__.to_metadata(metadata)
        table.c.id.type = sa.Integer()
    metadata.create_all(engine)
    yield sessionmaker(engine, expire_on_commit=False)
    engine.dispose()


def _connection(uuid="sb-one", space="s", **kwargs):
    now = datetime.now()
    return SandboxConnection(
        uuid=uuid,
        space_uuid=space,
        name=uuid,
        provider="openshell",
        endpoint="http://plugin",
        api_key=TOKEN,
        image="worker:approved",
        cpu=1,
        memory="512Mi",
        timeout_seconds=5,
        config_revision=1,
        status=SandboxStatus.READY,
        gmt_create=now,
        gmt_modified=now,
        **kwargs,
    )


def _insert(sf, connection=None):
    connection = connection or _connection()
    with sf() as session:
        SandboxTable(session).insert(connection)
    return connection


def _client(sf, enhanced_timeout=30):
    from agentlens.config import Settings
    from agentlens.web.deps import get_settings

    app = FastAPI()
    app.include_router(router)
    install_error_handlers(app)

    def table():
        with sf() as session:
            yield SandboxTable(session)

    app.dependency_overrides[get_sandbox_table] = table
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None,
        AGENTLENS_TOKEN="fixture",
        SIGNOZ_OB_URL="fixture",
        SIGNOZ_ORG_ID="fixture",
        ANTCOLLECTOR_ENDPOINT="fixture",
        ENHANCED_EXEC_TIMEOUT=enhanced_timeout,
    )
    return TestClient(app)


def test_scoped_crud_write_only_token_and_destination_guard(sandbox_sf):
    client = _client(sandbox_sf)
    created = client.post(
        "/api/v1/sandbox-connection",
        json={
            "space_uuid": "s",
            "name": "plugin",
            "endpoint": "http://plugin/",
            "api_key": TOKEN,
            "image": "worker:approved",
        },
    )
    assert created.status_code == 201, created.text
    uuid = created.json()["uuid"]
    assert created.json()["api_key_configured"] is True
    assert TOKEN not in created.text
    assert client.get("/api/v1/sandbox-connections?space_uuid=elsewhere").json()["total"] == 0
    for method, body in (
        ("get", None),
        ("delete", None),
        ("patch", {"space_uuid": "other", "name": "bad"}),
        ("post", {"space_uuid": "other"}),
    ):
        url = f"/api/v1/sandbox-connection/{uuid}" + ("/test" if method == "post" else "")
        response = getattr(client, method)(
            url, **({"json": body} if body else {"params": {"space_uuid": "other"}})
        )
        assert response.status_code == 404
        assert TOKEN not in response.text
    response = client.patch(
        f"/api/v1/sandbox-connection/{uuid}", json={"space_uuid": "s", "endpoint": "http://other"}
    )
    assert response.status_code == 422
    assert TOKEN not in response.text
    response = client.patch(
        f"/api/v1/sandbox-connection/{uuid}", json={"space_uuid": "s", "status": "ready"}
    )
    assert response.status_code == 422
    assert (
        client.get(f"/api/v1/sandbox-connection/{uuid}", params={"space_uuid": "s"}).json()[
            "config_revision"
        ]
        == 1
    )


def test_binding_defaults_profile_revision_and_rotation(sandbox_sf):
    one, two = _insert(sandbox_sf), _insert(sandbox_sf, _connection("sb-two"))
    with sandbox_sf() as session:
        table = SandboxTable(session)
        assert resolve_sandbox_profile(table, "s") == {"mode": "local"}
        table.save_binding(SandboxBinding("s", one.uuid))
        frozen = resolve_sandbox_profile(table, "s")
        assert TOKEN not in str(frozen)
        table.save_binding(SandboxBinding("s", two.uuid))
        assert resolve_sandbox_profile(table, "s")["connection_uuid"] == two.uuid
        assert resolve_sandbox_credentials(table, frozen).uuid == one.uuid
        table.update(one.uuid, "s", {"api_key": "rotated-secret"})
        assert resolve_sandbox_credentials(table, frozen).api_key == "rotated-secret"
        table.update(one.uuid, "s", {"image": "worker:changed"})
        changed = table.get_in_space(one.uuid, "s")
        assert changed.config_revision == 2
        assert changed.status == SandboxStatus.UNTESTED
        with pytest.raises(SandboxError, match="frozen identity"):
            resolve_sandbox_credentials(table, frozen)
        table.save_binding(SandboxBinding("s", two.uuid, True))
        with pytest.raises(SandboxError) as raised:
            resolve_sandbox_profile(table, "s", {"mode": "local"})
        assert raised.value.code == "sandbox_required"
        assert resolve_sandbox_profile(table, "other") == {"mode": "local"}
        with pytest.raises(NotFound):
            table.save_binding(SandboxBinding("other", two.uuid))
        with pytest.raises(SandboxError) as raised:
            resolve_sandbox_profile(
                table, "other", {"mode": "required", "connection_uuid": one.uuid}
            )
        assert raised.value.code == "sandbox_connection_missing"
        table.save_binding(SandboxBinding("s", None, True))
        with pytest.raises(SandboxError):
            resolve_sandbox_profile(table, "s")


def test_selected_unavailable_and_missing_never_use_local(sandbox_sf):
    one = _insert(sandbox_sf)
    with sandbox_sf() as session:
        table = SandboxTable(session)
        table.save_binding(SandboxBinding("s", one.uuid))
        table.update(one.uuid, "s", {"status": "untested"})
        profile = resolve_sandbox_profile(table, "s")
        assert profile["mode"] == "sandbox"
        with pytest.raises(SandboxError) as raised:
            resolve_sandbox_credentials(table, profile)
        assert raised.value.code == "sandbox_unavailable"
        with pytest.raises(SandboxError) as raised:
            resolve_sandbox_profile(table, "s", {"connection_uuid": "explicit-missing"})
        assert raised.value.code == "sandbox_connection_missing"


def test_profile_race_and_referenced_deletion(sandbox_sf):
    one = _insert(sandbox_sf)
    with sandbox_sf() as session:
        table = SandboxTable(session)
        original = table.probe_snapshot(one.uuid, "s")
        table.update(one.uuid, "s", {"cpu": 2})
        assert not table.save_test_status(original, SandboxStatus.READY)
        table.save_binding(SandboxBinding("s", one.uuid))
        with pytest.raises(Conflict):
            table.delete(one.uuid, "s")
        table.save_binding(SandboxBinding("s"))
        table.reserve_lease(
            dict(
                uuid="lease",
                connection_uuid=one.uuid,
                space_uuid="s",
                task_uuid="trial-task",
                attempt=0,
                diagnoser_uuid="trial",
                request_fingerprint="f" * 64,
            )
        )
        with pytest.raises(Conflict):
            table.delete(one.uuid, "s")
        table.finish_lease("lease", cleanup_verified=True)
        table.delete(one.uuid, "s")
        assert table.get_in_space(one.uuid, "s") is None


class FakePlugin:
    calls = []
    cleanup = True
    failure = None
    response = None

    def __init__(self, connection):
        self.connection = connection

    def health(self):
        return {"ready": True, "max_input_bytes": 2097152}

    def validate_execution(self, create, execute):
        if self.failure == "input":
            raise SandboxError("sandbox_input_limit")
        return {
            "valid": True,
            "input_bytes": len(json.dumps(execute, default=str).encode()),
            "max_input_bytes": 2097152,
            "transport": "unary",
        }

    def create(self, payload):
        self.calls.append(("create", payload))
        if self.failure == "create":
            raise SandboxError("sandbox_transport_error")
        return {}

    def execute(self, lease_id, payload):
        from tests.unit.service.test_enhanced_rule_runtime import manifest

        self.calls.append(("execute", payload))
        if self.failure == "execute":
            raise SandboxError("sandbox_transport_error")
        return self.response or {
            "exit_code": 0,
            "stdout": json.dumps(
                {
                    "status": "ok",
                    "result": {"metrics": [{"name": "x", "verdict": "good"}]},
                    **(
                        {
                            "runtime_manifest": manifest(),
                            "captured_stdout": "observed output",
                        }
                        if payload.get("rule_runtime")
                        else {}
                    ),
                }
            ),
            "stderr": "",
            "timed_out": False,
            "truncated": False,
            "cleanup_verified": False,
        }

    def delete(self, lease_id):
        self.calls.append(("delete", lease_id))
        return self.cleanup

    def close(self):
        pass


@pytest.fixture
def plugin():
    FakePlugin.calls = []
    FakePlugin.cleanup = True
    FakePlugin.failure = None
    FakePlugin.response = None
    return FakePlugin


def _context(connection, **kwargs):
    return SandboxExecutionContext(
        connection.frozen_profile(),
        connection.space_uuid,
        "trial-task",
        0,
        "trial",
        "trace-1",
        **kwargs,
    )


def test_input_preflight_rejection_never_reserves_or_creates_a_lease(sandbox_sf, plugin):
    one = _insert(sandbox_sf)
    plugin.failure = "input"
    service = SandboxExecutionService(sandbox_sf, client_factory=plugin)
    with pytest.raises(SandboxError) as raised:
        service.execute("def evaluate(trace): pass", {}, _context(one))
    assert raised.value.code == "sandbox_input_limit"
    assert plugin.calls == []
    with sandbox_sf() as session:
        assert session.scalar(sa.select(sa.func.count()).select_from(SandboxLeaseModel)) == 0


def test_input_preview_respects_client_limit_after_worker_normalization(
    sandbox_sf, plugin, monkeypatch
):
    monkeypatch.setenv("SANDBOX_MAX_INPUT_BYTES", "4096")
    monkeypatch.setattr(
        plugin,
        "validate_execution",
        lambda *args: {
            "valid": True,
            "input_bytes": 4097,
            "max_input_bytes": 2097152,
            "transport": "unary",
        },
    )
    one = _insert(sandbox_sf)
    service = SandboxExecutionService(sandbox_sf, client_factory=plugin)
    with pytest.raises(SandboxError) as raised:
        service.preview("def evaluate(trace): pass", {}, _context(one))
    assert raised.value.code == "sandbox_input_limit"
    assert raised.value.details == {
        "stage": "worker_stdin",
        "actual_bytes": 4097,
        "allowed_bytes": 4096,
    }
    assert plugin.calls == []
    with sandbox_sf() as session:
        assert session.scalar(sa.select(sa.func.count()).select_from(SandboxLeaseModel)) == 0


def test_input_preview_measures_complete_request_without_admission(sandbox_sf, plugin):
    one = _insert(sandbox_sf)
    service = SandboxExecutionService(sandbox_sf, client_factory=plugin)
    result = service.preview("def evaluate(trace): pass", {"payload": "中文🙂"}, _context(one))
    assert result["valid"] is True
    assert result["request_bytes"] > result["worker_input_bytes"] > 10
    assert result["max_input_bytes"] == 2097152
    assert plugin.calls == []
    with sandbox_sf() as session:
        assert session.scalar(sa.select(sa.func.count()).select_from(SandboxLeaseModel)) == 0


@pytest.mark.parametrize(
    "status,code,expected",
    [
        (413, "SANDBOX_INPUT_TOO_LARGE", "sandbox_input_limit"),
        (422, "SANDBOX_RUNTIME_UNAVAILABLE", "sandbox_runtime_unavailable"),
        (502, "SANDBOX_PROVIDER_UNAVAILABLE", "sandbox_transport_error"),
    ],
)
def test_plugin_errors_preserve_safe_classification_and_size(status, code, expected):
    payload = {
        "code": code,
        "msg": TOKEN,
        "details": {
            "stage": "worker_stdin",
            "actual_bytes": 2097153,
            "allowed_bytes": 2097152,
            "secret": TOKEN,
        },
    }
    client = SandboxClient(
        _connection(),
        transport=httpx.MockTransport(lambda request: httpx.Response(status, json=payload)),
    )
    try:
        with pytest.raises(SandboxError) as raised:
            client.execute("test", {"timeout_seconds": 1})
        assert raised.value.code == expected
        assert TOKEN not in str(raised.value)
        if expected == "sandbox_input_limit":
            assert raised.value.details == {
                "stage": "worker_stdin",
                "actual_bytes": 2097153,
                "allowed_bytes": 2097152,
            }
            assert "2097153" in str(raised.value) and "2097152" in str(raised.value)
    finally:
        client.close()


@pytest.mark.parametrize(
    "details",
    [
        None,
        [],
        {"stage": []},
        {
            "stage": "worker_stdin",
            "actual_bytes": TOKEN,
            "allowed_bytes": 1024,
        },
    ],
)
def test_input_limit_discards_untrusted_detail_fields(details):
    client = SandboxClient(
        _connection(),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                413, json={"code": "SANDBOX_INPUT_TOO_LARGE", "details": details, "msg": TOKEN}
            )
        ),
    )
    try:
        with pytest.raises(SandboxError) as raised:
            client.execute("test", {"timeout_seconds": 1})
        assert raised.value.code == "sandbox_input_limit"
        assert raised.value.details == {}
        assert TOKEN not in str(raised.value)
    finally:
        client.close()


def test_old_plugin_fails_before_reservation_with_upgrade_guidance(sandbox_sf):
    one = _insert(sandbox_sf)
    requests = []

    def old_plugin(request):
        requests.append(request.url.path)
        if request.url.path == "/v1/health":
            return _healthy_plugin_response()
        return httpx.Response(404, json={"code": "SANDBOX_ROUTE_UNAVAILABLE"})

    service = SandboxExecutionService(
        sandbox_sf,
        client_factory=lambda connection: SandboxClient(
            connection, transport=httpx.MockTransport(old_plugin)
        ),
    )
    with pytest.raises(SandboxError) as raised:
        service.execute("def evaluate(trace): pass", {}, _context(one))
    assert raised.value.code == "sandbox_plugin_upgrade_required"
    assert requests == ["/v1/health", "/v1/executions/validate"]
    with sandbox_sf() as session:
        assert session.scalar(sa.select(sa.func.count()).select_from(SandboxLeaseModel)) == 0


def test_execution_uses_fresh_sessions_dates_ownership_and_never_reexecutes(sandbox_sf, plugin):
    one = _insert(sandbox_sf)
    service = SandboxExecutionService(sandbox_sf, client_factory=plugin)
    assert (
        service.execute("def evaluate(trace): pass", {"date": date(2026, 11, 2)}, _context(one))[
            "metrics"
        ][0]["verdict"]
        == "good"
    )
    assert [call[0] for call in plugin.calls] == ["create", "execute", "delete"]
    create = plugin.calls[0][1]
    assert (create["task_uuid"], create["attempt"], create["diagnoser_uuid"]) == (
        "trial-task",
        0,
        "trial",
    )
    with sandbox_sf() as session:
        lease = session.scalar(sa.select(SandboxLeaseModel))
        assert lease.cleanup_verified == 1
        assert lease.state == "closed"
        assert TOKEN not in str(lease.__dict__)
        assert "def evaluate" not in str(lease.__dict__)
    with pytest.raises(SandboxError) as raised:
        service.execute("def evaluate(trace): pass", {}, _context(one))
    assert raised.value.code == "sandbox_lease_conflict"
    assert len(plugin.calls) == 3


@pytest.mark.parametrize(
    "failure", ["create", "execute", "invalid", "timeout", "truncated", "runtime"]
)
def test_execution_failure_always_deletes_and_fails_closed(sandbox_sf, plugin, failure):
    one = _insert(sandbox_sf)
    service = SandboxExecutionService(sandbox_sf, client_factory=plugin)
    plugin.failure = failure
    if failure in {"invalid", "timeout", "truncated", "runtime"}:
        plugin.response = dict(
            exit_code=0,
            stdout="invalid",
            stderr=TOKEN,
            timed_out=failure == "timeout",
            truncated=failure == "truncated",
            cleanup_verified=False,
        )
        if failure == "runtime":
            plugin.response["stdout"] = json.dumps({"status": "error", "reason": TOKEN})
    with pytest.raises(SandboxError) as raised:
        service.execute("code", {}, _context(one))
    assert plugin.calls[-1][0] == "delete"
    assert TOKEN not in str(raised.value)
    with sandbox_sf() as session:
        assert session.scalar(sa.select(SandboxLeaseModel)).state == "closed"


@pytest.mark.parametrize("error_type", [httpx.ConnectError, httpx.ConnectTimeout])
def test_create_connection_failure_before_admission_does_not_pin_profile(sandbox_sf, error_type):
    one = _insert(sandbox_sf)
    requests = []

    def unavailable(request):
        requests.append((request.method, request.url.path))
        if request.url.path == "/v1/health":
            return _healthy_plugin_response()
        if request.url.path == "/v1/executions/validate":
            return httpx.Response(
                200,
                json={
                    "valid": True,
                    "input_bytes": 100,
                    "max_input_bytes": 2097152,
                    "transport": "unary",
                },
            )
        if request.method == "POST":
            raise error_type("synthetic connect failure", request=request)
        return httpx.Response(404, json={"code": "SANDBOX_NOT_FOUND"})

    service = SandboxExecutionService(
        sandbox_sf,
        client_factory=lambda connection: SandboxClient(
            connection, transport=httpx.MockTransport(unavailable)
        ),
    )
    with pytest.raises(SandboxError) as raised:
        service.execute("code", {}, _context(one))
    assert raised.value.code == "sandbox_transport_error"
    assert requests == [
        ("GET", "/v1/health"),
        ("POST", "/v1/executions/validate"),
        ("POST", "/v1/sandboxes"),
    ]
    assert service.reconcile_pending() == {"checked": 0, "closed": 0, "pending": 0}
    with sandbox_sf() as session:
        lease = session.scalar(sa.select(SandboxLeaseModel))
        assert (lease.state, lease.cleanup_verified) == ("closed", 1)
        SandboxTable(session).update(one.uuid, "s", {"image": "worker:changed"})


@pytest.mark.parametrize("phase", ["create_read_timeout", "execute_connect_failure"])
def test_possible_admission_still_requires_verified_cleanup(sandbox_sf, phase):
    one = _insert(sandbox_sf)
    requests = []

    def unavailable(request):
        requests.append((request.method, request.url.path))
        if request.url.path == "/v1/health":
            return _healthy_plugin_response()
        if request.url.path == "/v1/executions/validate":
            return httpx.Response(
                200,
                json={
                    "valid": True,
                    "input_bytes": 100,
                    "max_input_bytes": 2097152,
                    "transport": "unary",
                },
            )
        if request.method == "DELETE":
            return httpx.Response(404, json={"code": "SANDBOX_NOT_FOUND"})
        if request.url.path == "/v1/sandboxes":
            if phase == "create_read_timeout":
                raise httpx.ReadTimeout("synthetic lost response", request=request)
            lease_id = json.loads(request.content)["lease_id"]
            return httpx.Response(
                200,
                json={
                    "lease_id": lease_id,
                    "sandbox_id": "sandbox-fixture",
                    "state": "ready",
                    "cleanup_verified": False,
                },
            )
        raise httpx.ConnectError("synthetic execute connect failure", request=request)

    service = SandboxExecutionService(
        sandbox_sf,
        client_factory=lambda connection: SandboxClient(
            connection, transport=httpx.MockTransport(unavailable)
        ),
    )
    with pytest.raises(SandboxError) as raised:
        service.execute("code", {}, _context(one))
    assert raised.value.code == "sandbox_cleanup_pending"
    assert requests[-1][0] == "DELETE"
    assert service.reconcile_pending() == {"checked": 1, "closed": 0, "pending": 1}
    with sandbox_sf() as session:
        assert session.scalar(sa.select(SandboxLeaseModel)).state == "cleanup_pending"
        with pytest.raises(Conflict):
            SandboxTable(session).update(one.uuid, "s", {"image": "worker:changed"})


def test_cleanup_pending_persists_and_blocks_connection_deletion(sandbox_sf, plugin):
    one = _insert(sandbox_sf)
    plugin.cleanup = False
    with pytest.raises(SandboxError) as raised:
        SandboxExecutionService(sandbox_sf, client_factory=plugin).execute(
            "code", {}, _context(one)
        )
    assert raised.value.code == "sandbox_cleanup_pending"
    with sandbox_sf() as session:
        assert session.scalar(sa.select(SandboxLeaseModel)).state == "cleanup_pending"
        with pytest.raises(Conflict):
            SandboxTable(session).delete(one.uuid, "s")


def test_cross_space_runtime_never_calls_plugin(sandbox_sf, plugin):
    one = _insert(sandbox_sf)
    context = replace(_context(one), space_uuid="other")
    with pytest.raises(SandboxError):
        SandboxExecutionService(sandbox_sf, client_factory=plugin).execute("code", {}, context)
    assert plugin.calls == []


def test_bounded_transport_authentication_and_date_protocol():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(
            200, json={"lease_id": "lease", "state": "closed", "cleanup_verified": True}
        )

    client = SandboxClient(_connection(), transport=httpx.MockTransport(respond))
    client.execute(
        "lease", {"code": "code", "trace": {"date": date(2026, 11, 2)}, "timeout_seconds": 1}
    )
    assert requests[0].headers["Authorization"] == "Bearer " + TOKEN
    assert json.loads(requests[0].content)["trace"]["date"] == "2026-11-02"
    client.close()
    for status_code, content, expected in (
        (401, b"", "sandbox_authentication_failed"),
        (200, b"x" * (MAX_RESPONSE_BYTES + 1), "sandbox_output_limit"),
        (302, b"", "sandbox_transport_error"),
        (200, b"not-json", "sandbox_protocol_error"),
    ):
        client = SandboxClient(
            _connection(),
            transport=httpx.MockTransport(
                lambda request, status_code=status_code, content=content: httpx.Response(
                    status_code, content=content
                )
            ),
        )
        with pytest.raises(SandboxError) as raised:
            client.delete("lease")
        assert raised.value.code == expected
        assert TOKEN not in str(raised.value)
        client.close()


def test_migration_creates_and_removes_all_optional_tables():
    path = (
        Path(__file__).resolve().parents[2]
        / "obschema/migrations/versions/012_sandbox_integrations.py"
    )
    spec = importlib.util.spec_from_file_location("sandbox_migration_012", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        operations = Operations(MigrationContext.configure(connection))
        module.op = operations
        module.upgrade()
        assert set(sa.inspect(connection).get_table_names()) == {
            "agentlens_sandbox_connection",
            "agentlens_sandbox_binding",
            "agentlens_sandbox_lease",
        }
        module.downgrade()
        assert not sa.inspect(connection).get_table_names()


def test_reconciliation_keeps_active_lease_and_closure_monotonic(sandbox_sf, plugin):
    from datetime import timedelta

    one = _insert(sandbox_sf)
    with sandbox_sf() as session:
        table = SandboxTable(session)
        table.reserve_lease(
            dict(
                uuid="active",
                connection_uuid=one.uuid,
                space_uuid="s",
                task_uuid="trial-active",
                attempt=0,
                diagnoser_uuid="trial",
                request_fingerprint="a" * 64,
            )
        )
    service = SandboxExecutionService(sandbox_sf, client_factory=plugin)
    assert service.reconcile_pending() == {"checked": 0, "closed": 0, "pending": 0}
    assert plugin.calls == []
    with sandbox_sf() as session:
        table = SandboxTable(session)
        with pytest.raises(Conflict):
            table.update(one.uuid, "s", {"endpoint": "http://other", "api_key": "new-key"})
        table.update(one.uuid, "s", {"api_key": "rotated-key"})
        session.execute(
            sa.update(SandboxLeaseModel).values(expires_at=datetime.now() - timedelta(seconds=1))
        )
        session.commit()
    assert service.reconcile_pending() == {"checked": 1, "closed": 1, "pending": 0}
    with sandbox_sf() as session:
        table = SandboxTable(session)
        table.finish_lease("active", cleanup_verified=False)
        assert session.scalar(sa.select(SandboxLeaseModel)).state == "closed"
        table.update(one.uuid, "s", {"image": "worker:new"})


def test_cleanup_pending_reconciliation_retries_without_reexecution(sandbox_sf, plugin):
    one = _insert(sandbox_sf)
    plugin.cleanup = False
    service = SandboxExecutionService(sandbox_sf, client_factory=plugin)
    with pytest.raises(SandboxError):
        service.execute("code", {}, _context(one))
    assert service.reconcile_pending() == {"checked": 1, "closed": 0, "pending": 1}
    plugin.cleanup = True
    assert service.reconcile_pending() == {"checked": 1, "closed": 1, "pending": 0}
    assert [name for name, _ in plugin.calls].count("execute") == 1
    with sandbox_sf() as session:
        SandboxTable(session).delete(one.uuid, "s")


def test_deletion_blocks_current_diagnoser_and_active_frozen_task(sandbox_sf):
    one = _insert(sandbox_sf)
    now = datetime.now()
    with sandbox_sf() as session:
        table = SandboxTable(session)
        session.add(
            DiagnoserModel(
                uuid="diag",
                space_uuid="s",
                agent_uuid="a",
                name="rule",
                type="custom",
                mode="rule",
                spec={"sandbox": {"mode": "required", "connection_uuid": one.uuid}},
                version="1",
                gmt_create=now,
                gmt_modified=now,
            )
        )
        session.commit()
        with pytest.raises(Conflict):
            table.delete(one.uuid, "s")
        session.execute(sa.delete(DiagnoserModel))
        session.add(
            TaskModel(
                uuid="task",
                job_uuid="job",
                status="pending",
                scheduled_at=now,
                execution_snapshot={"diagnosers": [{"spec": {"sandbox": one.frozen_profile()}}]},
                gmt_create=now,
                gmt_modified=now,
            )
        )
        session.commit()
        with pytest.raises(Conflict):
            table.delete(one.uuid, "s")
        session.execute(sa.update(TaskModel).values(status="succeeded"))
        session.commit()
        table.delete(one.uuid, "s")


def test_readiness_probe_and_disable_do_not_expose_credentials(sandbox_sf, monkeypatch):
    one = _insert(sandbox_sf)
    client = _client(sandbox_sf)

    class Ready:
        def __init__(self, connection):
            assert connection.api_key == TOKEN

        def health(self):
            return {"ready": True}

        def close(self):
            pass

    monkeypatch.setattr("agentlens.web.routes.sandbox.SandboxClient", Ready)
    response = client.post(f"/api/v1/sandbox-connection/{one.uuid}/test", json={"space_uuid": "s"})
    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert response.json()["test_error"] is None
    assert TOKEN not in response.text
    disabled = client.patch(
        f"/api/v1/sandbox-connection/{one.uuid}", json={"space_uuid": "s", "status": "disabled"}
    )
    assert disabled.status_code == 200
    response = client.post(f"/api/v1/sandbox-connection/{one.uuid}/test", json={"space_uuid": "s"})
    assert response.status_code == 409
    assert TOKEN not in response.text
    with sandbox_sf() as session:
        table = SandboxTable(session)
        table.update(one.uuid, "s", {"image": "worker:new"})
        assert table.get_in_space(one.uuid, "s").status == SandboxStatus.DISABLED


def test_canonical_schema_tracks_complete_sandbox_migration_head():
    ddl = (Path(__file__).resolve().parents[2] / "obschema/ddl.sql").read_text()
    assert "VALUES ('012')" in ddl
    assert ddl.index("CREATE TABLE `agentlens_sandbox_lease`") < ddl.index("VALUES ('012')")
    for column in (
        "execution_snapshot",
        "judge_request_ledger",
        "diagnostic_validation",
        "config_revision",
        "expires_at",
    ):
        assert f"`{column}`" in ddl
    for table in (
        "agentlens_sandbox_connection",
        "agentlens_sandbox_binding",
        "agentlens_sandbox_lease",
    ):
        assert f"CREATE TABLE `{table}`" in ddl


@pytest.mark.parametrize(
    "change",
    [
        {"status": "canceled"},
        {"status": "pending"},
        {"attempt": 2},
        {"worker": "another-worker"},
        {"execution_snapshot": {"job": {"space_uuid": "other"}, "diagnosers": []}},
    ],
)
def test_task_attempt_reservation_rejects_stale_canceled_and_foreign_owner(
    sandbox_sf, plugin, change
):
    one = _insert(sandbox_sf)
    now = datetime.now()
    fields = dict(
        uuid="real-task",
        job_uuid="job",
        status="running",
        worker="worker-one",
        attempt=1,
        scheduled_at=now,
        gmt_create=now,
        gmt_modified=now,
        execution_snapshot={
            "job": {"space_uuid": "s"},
            "diagnosers": [{"uuid": "diag", "spec": {"sandbox": one.frozen_profile()}}],
        },
    )
    with sandbox_sf() as session:
        session.add(TaskModel(**(fields | change)))
        session.commit()
    context = SandboxExecutionContext(
        one.frozen_profile(), "s", "real-task", 1, "diag", worker_uuid="worker-one"
    )
    with pytest.raises(SandboxError) as raised:
        SandboxExecutionService(sandbox_sf, client_factory=plugin).execute("code", {}, context)
    assert raised.value.code == "sandbox_attempt_rejected"
    assert plugin.calls == []
    with sandbox_sf() as session:
        assert session.scalar(sa.select(sa.func.count()).select_from(SandboxLeaseModel)) == 0


def test_owned_task_attempt_executes_and_missing_real_task_is_rejected(sandbox_sf, plugin):
    one = _insert(sandbox_sf)
    now = datetime.now()
    with sandbox_sf() as session:
        session.add(
            TaskModel(
                uuid="real-task",
                job_uuid="job",
                status="running",
                worker="worker-one",
                attempt=1,
                scheduled_at=now,
                gmt_create=now,
                gmt_modified=now,
                execution_snapshot={
                    "job": {"space_uuid": "s"},
                    "diagnosers": [{"uuid": "diag", "spec": {"sandbox": one.frozen_profile()}}],
                },
            )
        )
        session.commit()
    service = SandboxExecutionService(sandbox_sf, client_factory=plugin)
    context = SandboxExecutionContext(
        one.frozen_profile(), "s", "real-task", 1, "diag", worker_uuid="worker-one"
    )
    assert service.execute("code", {}, context)["metrics"][0]["verdict"] == "good"
    plugin.calls.clear()
    for rejected_context in (
        replace(context, worker_uuid=None),
        replace(context, task_uuid="missing-real-task"),
        replace(context, task_uuid="trial-arbitrary", attempt=1),
        replace(context, task_uuid="trial-arbitrary", attempt=0, diagnoser_uuid="trial"),
    ):
        with pytest.raises(SandboxError) as raised:
            service.execute("code", {}, rejected_context)
        assert raised.value.code == "sandbox_attempt_rejected"
    assert plugin.calls == []


def _forbid_host_execution(monkeypatch):
    module = "agentlens.diagnoser.executors.rule.custom.executor"
    monkeypatch.setattr(
        module + ".get_custom_rule_worker_pool", lambda: pytest.fail("host worker used")
    )
    monkeypatch.setattr(
        module + ".execute_one_shot", lambda *args, **kwargs: pytest.fail("host process used")
    )


@pytest.mark.parametrize("enhanced", [False, True])
def test_persisted_task_runner_executes_frozen_profile_with_current_attempt(
    sandbox_sf, plugin, monkeypatch, enhanced
):
    from agentlens.domain.diagnoser import Diagnoser
    from agentlens.domain.enums import DiagnoserMode, DiagnoserType, Verdict
    from agentlens.infra.ob.tables.task_table import TaskTable
    from agentlens.kernel.worker.runner import Runner
    from agentlens.service.datasource.trace import Trace

    _forbid_host_execution(monkeypatch)
    one = _insert(
        sandbox_sf, replace(_connection(), image="worker@sha256:" + "e" * 64) if enhanced else None
    )
    two = _insert(sandbox_sf, _connection("sb-current-default"))
    now = datetime.now()
    code = (
        "def evaluate(trace):\n"
        '    return {"metrics": [{"name": "x", "verdict": "bad", "reason": "host forbidden"}]}'
    )
    spec = {"code": code, "metrics": [{"name": "x"}], "sandbox": one.frozen_profile()}
    if enhanced:
        from agentlens.service.rule_runtime import normalize_rule_runtime
        from tests.unit.service.test_enhanced_rule_runtime import manifest

        spec.update(
            rule_runtime="python-eval-v1",
            rule_runtime_manifest=manifest()["manifest_sha256"],
            materials={"files": [{"path": "input.json", "encoding": "utf-8", "content": "{}"}]},
        )
        spec = normalize_rule_runtime(spec)
        monkeypatch.setattr(
            plugin,
            "health",
            lambda self: {
                "ready": True,
                "capabilities": ["custom_rule", "python_eval_v1"],
                "runtime_manifests": [{**manifest(), "image": one.image}],
            },
            raising=False,
        )
    snapshot = {"job": {"space_uuid": "s"}, "diagnosers": [{"uuid": "diag", "spec": spec}]}
    with sandbox_sf() as session:
        SandboxTable(session).save_binding(SandboxBinding("s", two.uuid, True))
        session.add(
            TaskModel(
                uuid="persisted-task",
                job_uuid="job",
                status="running",
                worker="worker-one",
                attempt=2,
                scheduled_at=now,
                execution_snapshot=snapshot,
                gmt_create=now,
                gmt_modified=now,
            )
        )
        session.commit()
        task = TaskTable(session).get_by_uuid("persisted-task")
        diagnoser = Diagnoser(
            uuid="diag",
            space_uuid="s",
            agent_uuid="a",
            name="Frozen rule",
            description="",
            type=DiagnoserType.CUSTOM,
            mode=DiagnoserMode.RULE,
            spec=spec,
            version="1",
            gmt_create=now,
            gmt_modified=now,
            creator=None,
            modifier=None,
        )
        runner = Runner(
            task_table=None,
            job_table=None,
            dataset_table=None,
            diagnoser_table=None,
            trace_store=None,
            record_store=None,
            self_uuid="worker-one",
            session_factory=sandbox_sf,
        )
        runner._execution_snapshot = snapshot
        runner._sandbox_service = SandboxExecutionService(sandbox_sf, client_factory=plugin)
        prepared = runner._prepare_executors([diagnoser], task)
        executor = prepared[0]["executor"]
        result = executor.evaluate(
            Trace(trace_id="synthetic-trace", runtime_type="other", spans=[])
        )
        assert result.verdict == Verdict.GOOD
        assert result.execution.evidence["backend"] == "sandbox"
        assert plugin.calls[0][1]["connection_uuid"] == one.uuid
        assert plugin.calls[0][1]["attempt"] == 2
        if enhanced:
            assert plugin.calls[1][1]["materials"] == spec["materials"]
            assert result.execution.evidence["captured_stdout"] == "observed output"
        with pytest.raises(Exception) as duplicate:
            executor.evaluate(Trace(trace_id="synthetic-trace", runtime_type="other", spans=[]))
        assert duplicate.value.code == "sandbox_lease_conflict"
        assert [name for name, _ in plugin.calls].count("execute") == 1
        runner._close_executors()
    with sandbox_sf() as session:
        lease = session.scalar(sa.select(SandboxLeaseModel))
        assert (lease.task_uuid, lease.attempt, lease.diagnoser_uuid, lease.state) == (
            "persisted-task",
            2,
            "diag",
            "closed",
        )


def test_http_trial_uses_same_sandbox_service_and_synthetic_owner(sandbox_sf, plugin, monkeypatch):
    from agentlens.web.deps import get_session
    from agentlens.web.routes.diagnoser import router as diagnoser_router

    _forbid_host_execution(monkeypatch)
    one = _insert(sandbox_sf)
    with sandbox_sf() as session:
        SandboxTable(session).save_binding(SandboxBinding("s", one.uuid, True))
    monkeypatch.setattr(
        "agentlens.web.routes.diagnoser.SandboxExecutionService",
        lambda sf: SandboxExecutionService(sf, client_factory=plugin),
    )
    app = FastAPI()
    app.include_router(diagnoser_router)
    install_error_handlers(app)

    def owned_session():
        with sandbox_sf() as session:
            yield session

    app.dependency_overrides[get_session] = owned_session
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/diagnoser/rule-trial",
            json={
                "space_uuid": "s",
                "spec": {
                    "code": (
                        "def evaluate(trace):\n"
                        '    return {"metrics": [{"name": "x", "verdict": "good"}]}'
                    ),
                    "metrics": [{"name": "x"}],
                },
                "test_input": {"trace_id": "synthetic-trial", "runtime_type": "other", "spans": []},
            },
        )
    assert response.status_code == 200, response.text
    assert response.json()["verdict"] == "good", response.text
    assert response.json()["execution"]["evidence"]["backend"] == "sandbox"
    assert [name for name, _ in plugin.calls] == ["create", "execute", "delete"]
    with sandbox_sf() as session:
        lease = session.scalar(sa.select(SandboxLeaseModel))
        assert lease.task_uuid.startswith("trial-")
        assert (lease.attempt, lease.diagnoser_uuid, lease.state) == (0, "trial", "closed")
        assert session.scalar(sa.select(sa.func.count()).select_from(TaskModel)) == 0


def test_partial_binding_patch_locks_before_merging_omitted_policy():
    from agentlens.web.routes.sandbox import update_binding
    from agentlens.web.schemas.sandbox import SandboxBindingUpdate

    calls = []

    class Table:
        def get_binding(self, space_uuid, *, for_update=False):
            calls.append(("read", for_update))
            return SandboxBinding(space_uuid, None, True)

        def save_binding(self, binding):
            assert calls == [("read", True)]
            assert binding.require_sandbox is True
            calls.append(("save", binding.connection_uuid))

    result = update_binding(SandboxBindingUpdate(space_uuid="s", connection_uuid=None), Table())
    assert result.require_sandbox is True


def test_enhanced_qualification_before_lease_and_frozen_payload(sandbox_sf, plugin):
    from tests.unit.service.test_enhanced_rule_runtime import manifest

    image = "worker@sha256:" + "c" * 64
    connection = _insert(sandbox_sf, replace(_connection(), image=image, timeout_seconds=30))
    health = {
        "ready": True,
        "capabilities": ["custom_rule", "python_eval_v1"],
        "runtime_manifests": [{**manifest(), "image": image}],
    }
    plugin.health = lambda self: health
    materials = {"files": [{"path": "tests.py", "encoding": "utf-8", "content": "assert True"}]}
    context = _context(
        connection,
        rule_runtime="python-eval-v1",
        rule_runtime_manifest=manifest()["manifest_sha256"],
        materials=materials,
    )
    service = SandboxExecutionService(sandbox_sf, client_factory=plugin)
    health["runtime_manifests"] = []
    with pytest.raises(SandboxError) as failure:
        service.execute("code", {}, context)
    assert failure.value.code == "sandbox_runtime_unavailable"
    assert not plugin.calls
    with sandbox_sf() as session:
        assert session.query(SandboxLeaseModel).count() == 0
    health["runtime_manifests"] = [{**manifest(), "image": image}]
    service.execute("code", {}, context)
    create, execute = plugin.calls[0][1], plugin.calls[1][1]
    assert create["rule_runtime_manifest"] == manifest()["manifest_sha256"]
    assert execute["materials"]["files"][0]["sha256"]
    materials["files"][0]["content"] = "mutation"
    assert execute["materials"]["files"][0]["content"] == "assert True"


def test_runtime_endpoint_qualified_and_does_not_probe_list(sandbox_sf, monkeypatch):
    from tests.unit.service.test_enhanced_rule_runtime import manifest

    image = "worker@sha256:" + "c" * 64
    connection = _insert(sandbox_sf, replace(_connection(), image=image))
    calls = []

    def health(self):
        calls.append(True)
        return {
            "ready": True,
            "capabilities": ["custom_rule", "python_eval_v1"],
            "runtime_manifests": [{**manifest(), "image": image}],
        }

    monkeypatch.setattr(SandboxClient, "health", health)
    client = _client(sandbox_sf)
    assert client.get("/api/v1/sandbox-connections", params={"space_uuid": "s"}).status_code == 200
    assert not calls
    response = client.get(
        f"/api/v1/sandbox-connection/{connection.uuid}/runtime", params={"space_uuid": "s"}
    )
    assert response.status_code == 200
    assert response.json()["runtime_manifests"][0]["image"] == image
    assert TOKEN not in response.text
    assert (
        client.get(
            f"/api/v1/sandbox-connection/{connection.uuid}/runtime",
            params={"space_uuid": "foreign"},
        ).status_code
        == 404
    )


@pytest.mark.parametrize(
    ("connection_timeout", "service_timeout", "expected"),
    [(300, 30, 30), (5, 30, 5), (300, 10, 10)],
)
def test_runtime_effective_budget_uses_configured_service_and_connection_caps(
    sandbox_sf, monkeypatch, connection_timeout, service_timeout, expected
):
    from tests.unit.service.test_enhanced_rule_runtime import manifest

    image = "worker@sha256:" + "c" * 64
    connection = _insert(
        sandbox_sf, replace(_connection(), image=image, timeout_seconds=connection_timeout)
    )
    probing_sessions = []
    probe_snapshot = SandboxTable.probe_snapshot

    def snapshot(table, *args):
        result = probe_snapshot(table, *args)
        probing_sessions.append(table.s)
        return result

    monkeypatch.setattr(SandboxTable, "probe_snapshot", snapshot)

    def health(self):
        # A read-only runtime probe must release its DB transaction before network I/O.
        assert probing_sessions and not probing_sessions[-1].in_transaction()
        return {
            "ready": True,
            "capabilities": ["custom_rule", "python_eval_v1"],
            "runtime_manifests": [{**manifest(), "image": image}],
        }

    monkeypatch.setattr(SandboxClient, "health", health)
    client = _client(sandbox_sf, enhanced_timeout=service_timeout)
    response = client.get(
        f"/api/v1/sandbox-connection/{connection.uuid}/runtime", params={"space_uuid": "s"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["effective_timeout_seconds"] == expected
    with sandbox_sf() as session:
        unchanged = SandboxTable(session).get_in_space(connection.uuid, "s")
        assert unchanged.timeout_seconds == connection_timeout
        assert unchanged.config_revision == 1


def test_enhanced_http_trial_uses_normalized_runtime_material_payload(
    sandbox_sf, plugin, monkeypatch
):
    from agentlens.web.deps import get_session
    from agentlens.web.routes.diagnoser import router as diagnoser_router
    from tests.unit.service.test_enhanced_rule_runtime import manifest

    _forbid_host_execution(monkeypatch)
    image = "worker@sha256:" + "d" * 64
    one = _insert(sandbox_sf, replace(_connection(), image=image, timeout_seconds=30))
    with sandbox_sf() as session:
        SandboxTable(session).save_binding(SandboxBinding("s", one.uuid, True))
    monkeypatch.setattr(
        plugin,
        "health",
        lambda self: {
            "ready": True,
            "capabilities": ["custom_rule", "python_eval_v1"],
            "runtime_manifests": [{**manifest(), "image": image}],
        },
        raising=False,
    )
    monkeypatch.setattr(
        "agentlens.web.routes.diagnoser.SandboxExecutionService",
        lambda sf: SandboxExecutionService(sf, client_factory=plugin),
    )
    app = FastAPI()
    app.include_router(diagnoser_router)
    install_error_handlers(app)

    def owned_session():
        with sandbox_sf() as session:
            yield session

    app.dependency_overrides[get_session] = owned_session
    materials = {"files": [{"path": "input.json", "encoding": "utf-8", "content": "{}"}]}
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/diagnoser/rule-trial",
            json={
                "space_uuid": "s",
                "spec": {
                    "code": (
                        "import json\ndef evaluate(trace, context):\n"
                        '    return {"metrics": [{"name": "x", "verdict": "good"}]}'
                    ),
                    "metrics": [{"name": "x"}],
                    "rule_runtime": "python-eval-v1",
                    "rule_runtime_manifest": manifest()["manifest_sha256"],
                    "materials": materials,
                },
                "test_input": {"trace_id": "trial", "runtime_type": "other", "spans": []},
            },
        )
    assert response.status_code == 200, response.text
    assert response.json()["execution"]["status"] == "succeeded", response.text
    execute = plugin.calls[1][1]
    assert execute["rule_runtime"] == "python-eval-v1"
    assert execute["timeout_seconds"] == 30
    assert execute["materials"]["files"][0]["sha256"]


def test_lease_admission_refuses_changed_frozen_materials(sandbox_sf):
    one = _insert(sandbox_sf)
    now = datetime.now()
    runtime = {
        "rule_runtime": "python-eval-v1",
        "rule_runtime_manifest": "a" * 64,
        "materials": {"files": []},
    }
    with sandbox_sf() as session:
        session.add(
            TaskModel(
                uuid="frozen-enhanced-task",
                job_uuid="job",
                status="running",
                worker="worker-one",
                attempt=1,
                scheduled_at=now,
                gmt_create=now,
                gmt_modified=now,
                execution_snapshot={
                    "job": {"space_uuid": "s"},
                    "diagnosers": [
                        {"uuid": "diag", "spec": {"sandbox": one.frozen_profile(), **runtime}}
                    ],
                },
            )
        )
        session.commit()
    with sandbox_sf() as session:
        table = SandboxTable(session)
        fields = {
            "uuid": "enhanced-lease",
            "connection_uuid": one.uuid,
            "space_uuid": "s",
            "task_uuid": "frozen-enhanced-task",
            "attempt": 1,
            "diagnoser_uuid": "diag",
            "request_fingerprint": "f" * 64,
        }
        with pytest.raises(SandboxError) as failure:
            table.reserve_lease(
                fields,
                profile=one.frozen_profile(),
                worker_uuid="worker-one",
                runtime_spec={**runtime, "materials": {"files": [], "expected": 1}},
            )
        assert failure.value.code == "sandbox_attempt_rejected"
        table.reserve_lease(
            fields, profile=one.frozen_profile(), worker_uuid="worker-one", runtime_spec=runtime
        )


def test_connection_patch_revision_cas_is_atomic_and_legacy_optional(sandbox_sf):
    one = _insert(sandbox_sf)
    client = _client(sandbox_sf)
    response = client.patch(
        f"/api/v1/sandbox-connection/{one.uuid}",
        json={
            "space_uuid": "s",
            "memory": "1Gi",
            "timeout_seconds": 30,
            "expected_config_revision": one.config_revision,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["config_revision"] == one.config_revision + 1
    stale = client.patch(
        f"/api/v1/sandbox-connection/{one.uuid}",
        json={
            "space_uuid": "s",
            "cpu": 4,
            "name": "stale overwrite",
            "expected_config_revision": one.config_revision,
        },
    )
    assert stale.status_code == 409, stale.text
    current = client.get(
        f"/api/v1/sandbox-connection/{one.uuid}", params={"space_uuid": "s"}
    ).json()
    assert (current["cpu"], current["name"], current["memory"], current["timeout_seconds"]) == (
        1,
        one.name,
        "1Gi",
        30,
    )
    assert (
        client.patch(
            f"/api/v1/sandbox-connection/{one.uuid}",
            json={"space_uuid": "s", "name": "legacy update"},
        ).status_code
        == 200
    )
