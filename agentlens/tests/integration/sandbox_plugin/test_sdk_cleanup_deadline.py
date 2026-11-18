"""Exercise the optional SDK boundary without a gateway or Docker mutation."""

from types import SimpleNamespace

import pytest

from agentlens.sandbox_plugin import provider as provider_module
from agentlens.sandbox_plugin.provider import OpenShellProvider

sdk = pytest.importorskip("openshell.sandbox")


def test_slow_sdk_deletion_polls_share_one_cleanup_deadline(monkeypatch):
    clock = SimpleNamespace(now=0.0)
    clock.time = clock.monotonic = lambda: clock.now
    clock.sleep = lambda seconds: setattr(clock, "now", clock.now + seconds)
    monkeypatch.setattr(sdk, "time", clock)
    monkeypatch.setattr(provider_module, "time", clock)
    calls = []

    def get(request, *, timeout):
        calls.append((clock.now, timeout))
        clock.now += timeout
        return sdk.openshell_pb2.SandboxResponse(
            sandbox=sdk.openshell_pb2.Sandbox(
                metadata=sdk.datamodel_pb2.ObjectMeta(id="immutable", name=request.name)
            )
        )

    client = object.__new__(sdk.SandboxClient)
    client._timeout = 10.0
    client._stub = SimpleNamespace(GetSandbox=get)
    client.close = lambda: None
    provider = OpenShellProvider(
        SimpleNamespace(load_policy=lambda: {}, workspace="default", operation_timeout_seconds=10)
    )
    monkeypatch.setattr(provider, "_new_client", lambda timeout: client)

    with provider.cleanup_budget(30):
        with pytest.raises((TimeoutError, sdk.SandboxError)):
            provider.wait_deleted("owned-name", "immutable", 30, labels={})

    assert clock.now == 30
    assert all(start + timeout <= 30 for start, timeout in calls)


@pytest.mark.parametrize("replacement", [False, True], ids=["not-found", "replacement"])
def test_sdk_deletion_wait_preserves_identity_and_template_budget(monkeypatch, replacement):
    clock = SimpleNamespace(now=0.0)
    clock.time = clock.monotonic = lambda: clock.now
    clock.sleep = lambda seconds: setattr(clock, "now", clock.now + seconds)
    monkeypatch.setattr(sdk, "time", clock)
    monkeypatch.setattr(provider_module, "time", clock)
    calls = []

    class NotFound(sdk.grpc.RpcError):
        def code(self):
            return sdk.grpc.StatusCode.NOT_FOUND

    def get(request, *, timeout):
        calls.append(("sandbox", clock.now, timeout))
        clock.now += 0.1
        if len(calls) > 1 and not replacement:
            raise NotFound
        identity = "replacement" if len(calls) > 1 else "immutable"
        return sdk.openshell_pb2.SandboxResponse(
            sandbox=sdk.openshell_pb2.Sandbox(
                metadata=sdk.datamodel_pb2.ObjectMeta(id=identity, name=request.name)
            )
        )

    client = object.__new__(sdk.SandboxClient)
    client._timeout = 10.0
    client._stub = SimpleNamespace(GetSandbox=get)
    client.close = lambda: None

    def template_get(*args, **kwargs):
        calls.append(("template-get", clock.now, client._timeout))
        clock.now += 0.2
        return SimpleNamespace(metadata=SimpleNamespace(name="owned-name", labels={}))

    def template_delete(*args, **kwargs):
        calls.append(("template-delete", clock.now, client._timeout))
        clock.now += 0.2

    client.sandbox_templates = lambda: SimpleNamespace(get=template_get, delete=template_delete)
    provider = OpenShellProvider(
        SimpleNamespace(load_policy=lambda: {}, workspace="default", operation_timeout_seconds=10)
    )
    monkeypatch.setattr(provider, "_new_client", lambda timeout: client)

    with provider.cleanup_budget(2.5):
        provider.wait_deleted("owned-name", "immutable", 2.5, labels={})

    assert [kind for kind, _, _ in calls] == [
        "sandbox",
        "sandbox",
        "template-get",
        "template-delete",
    ]
    assert all(start + timeout <= 2.5 for _, start, timeout in calls)
    assert clock.now == pytest.approx(1.6)
