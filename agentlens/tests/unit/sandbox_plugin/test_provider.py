"""SDK boundary, output bounds and Docker readback remain independently testable."""

import subprocess
import sys
from types import SimpleNamespace

import httpx
import pytest

from agentlens.sandbox_plugin.provider import DockerCleanupVerifier, OpenShellProvider


def test_importing_standalone_plugin_never_initializes_main_app_or_sdk():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import agentlens.sandbox_plugin.main; "
            "assert 'openshell' not in sys.modules; "
            "assert 'agentlens.main' not in sys.modules; print('standalone')",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "standalone"


@pytest.mark.parametrize("leftover", ["workload", "supervisor", "volume", "unlabeled-volume"])
def test_docker_readback_detects_all_sandbox_resources_and_only_uses_get(setup, leftover):
    settings, _, _, _ = setup
    calls = []
    containers, volumes = [], []
    resource = {"Labels": {"openshell.ai/sandbox-id": "immutable-id"}}
    if leftover in {"workload", "supervisor"}:
        resource["Labels"]["openshell.ai/isolation-role"] = leftover
        containers.append(resource)
    elif leftover == "volume":
        volumes.append(resource)
    else:
        volumes.append({"Name": "openshell-immutable-id-private"})

    def reply(request):
        calls.append((request.method, request.url.path))
        body = {
            "/info": {"ID": "test-daemon"},
            "/containers/json": containers,
            "/volumes": {"Volumes": volumes},
        }[request.url.path]
        return httpx.Response(200, json=body)

    verifier = DockerCleanupVerifier(settings, transport=httpx.MockTransport(reply))
    try:
        assert not verifier.verify_absent("immutable-id", "al-name", daemon_id="test-daemon")
        assert calls == [("GET", "/info"), ("GET", "/containers/json"), ("GET", "/volumes")]
    finally:
        verifier.close()


@pytest.mark.parametrize("volume_body", [{"Volumes": None}, {"Volumes": []}])
def test_docker_readback_verifies_absence_only_with_complete_responses(setup, volume_body):
    settings, _, _, _ = setup
    verifier = DockerCleanupVerifier(
        settings,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"ID": "test-daemon"}
                if request.url.path == "/info"
                else ([] if request.url.path == "/containers/json" else volume_body),
            )
        ),
    )
    try:
        assert verifier.ready()
        assert verifier.verify_absent("immutable-id", "al-name", daemon_id="test-daemon")
    finally:
        verifier.close()


@pytest.mark.parametrize(
    "volume_body", [{}, {"Volumes": [], "Warnings": ["partial result"]}, {"Volumes": "invalid"}]
)
def test_docker_incomplete_readback_cannot_release_capacity(setup, volume_body):
    settings, _, _, _ = setup
    verifier = DockerCleanupVerifier(
        settings,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"ID": "test-daemon"}
                if request.url.path == "/info"
                else ([] if request.url.path == "/containers/json" else volume_body),
            )
        ),
    )
    try:
        assert not verifier.verify_absent("immutable-id", "al-name", daemon_id="test-daemon")
    finally:
        verifier.close()


def test_sdk_output_limit_stops_consuming_and_closes_stream(setup):
    settings, _, _, _ = setup
    provider = OpenShellProvider(settings)
    seen, closed, kwargs = [], [], []

    def stream(*args, **options):
        kwargs.append((args, options))
        try:
            yield SimpleNamespace(stream="stdout", data=b"a" * 8)
            seen.append("first")
            yield SimpleNamespace(stream="stderr", data=b"b" * 8)
            seen.append("unbounded producer resumed")
            while True:
                yield SimpleNamespace(stream="stdout", data=b"a" * 1000)
        finally:
            closed.append(True)

    provider._execution_stream = stream
    result = provider.execute("al-name", ["/worker", "-I"], b"sensitive-code\n", 1, 10)
    assert result.truncated and result.stdout == "a" * 8 and result.stderr == "b" * 2
    assert seen == ["first"] and closed == [True]
    assert kwargs[0][0] == ("al-name", ["/worker", "-I"])
    assert kwargs[0][1]["stdin"] == b"sensitive-code\n"
    assert kwargs[0][1]["no_login_shell"] is True


def test_sdk_timeout124_result_is_forwarded_without_retry(setup):
    settings, _, _, _ = setup
    provider = OpenShellProvider(settings)
    count = []

    def stream(*args, **kwargs):
        count.append(1)
        yield SimpleNamespace(stream="stdout", data=b"partial")
        yield SimpleNamespace(exit_code=124, stdout="partial", stderr="")

    provider._execution_stream = stream
    result = provider.execute("name", ["/worker"], b"input", 1, 100)
    assert result.exit_code == 124 and result.stdout == "partial" and count == [1]


def test_cleanup_template_checks_exact_lease_fingerprint_before_delete(setup):
    settings, _, _, _ = setup
    provider = OpenShellProvider(settings)
    calls = []
    template = SimpleNamespace(
        metadata=SimpleNamespace(
            name="al-name", labels={"aivision.lease": "wrong", "aivision.fingerprint": "wrong"}
        )
    )
    templates = SimpleNamespace(
        get=lambda *args, **kwargs: template, delete=lambda *args, **kwargs: calls.append("deleted")
    )
    provider._client = SimpleNamespace(
        get=lambda *args, **kwargs: SimpleNamespace(id="replacement-id"),
        sandbox_templates=lambda: templates,
    )
    with pytest.raises(ValueError, match="ownership"):
        provider.wait_deleted(
            "al-name",
            "immutable-id",
            1,
            labels={"aivision.lease": "expected", "aivision.fingerprint": "expected"},
        )
    assert calls == []


def test_cleanup_uses_thread_owned_sdk_deadline_without_changing_exec_client(setup, monkeypatch):
    settings, _, _, _ = setup
    provider = OpenShellProvider(settings)
    live_client = SimpleNamespace(_timeout=999)
    cleanup_client = SimpleNamespace(_timeout=10, close=lambda: None)
    provider._client = live_client
    monkeypatch.setattr(provider, "_new_client", lambda timeout: cleanup_client)
    clock = [100.0]
    monkeypatch.setattr("agentlens.sandbox_plugin.provider.time.monotonic", lambda: clock[0])
    with provider.cleanup_budget(130.0):
        assert provider._get_client() is cleanup_client
        clock[0] = 129.0
        assert provider._get_client()._timeout == 1
        assert live_client._timeout == 999
        clock[0] = 130.0
        with pytest.raises(TimeoutError):
            provider._get_client()
    assert provider._get_client() is live_client


def test_empty_wrong_daemon_cannot_verify_another_daemons_cleanup(setup):
    settings, _, _, _ = setup

    def reply(request):
        if request.url.path == "/info":
            return httpx.Response(200, json={"ID": "wrong-daemon"})
        return httpx.Response(
            200, json=[] if request.url.path == "/containers/json" else {"Volumes": []}
        )

    verifier = DockerCleanupVerifier(settings, transport=httpx.MockTransport(reply))
    try:
        assert not verifier.verify_absent("immutable-id", "al-name", daemon_id="original-daemon")
    finally:
        verifier.close()


def test_presence_binds_only_matching_immutable_id_and_name_on_stable_daemon(setup):
    import time

    settings, _, _, _ = setup
    containers = [
        {
            "Labels": {
                "openshell.ai/sandbox-id": "immutable-id",
                "openshell.ai/sandbox-name": "al-name",
            }
        }
    ]

    def reply(request):
        return httpx.Response(
            200, json={"ID": "test-daemon"} if request.url.path == "/info" else containers
        )

    verifier = DockerCleanupVerifier(settings, transport=httpx.MockTransport(reply))
    try:
        assert (
            verifier.observe_presence("immutable-id", "al-name", deadline=time.monotonic() + 1)
            == "test-daemon"
        )
        assert (
            verifier.observe_presence("another-id", "al-name", deadline=time.monotonic() + 1)
            is None
        )
        assert (
            verifier.observe_presence("immutable-id", "another-name", deadline=time.monotonic() + 1)
            is None
        )
    finally:
        verifier.close()


def test_daemon_switch_during_absence_readback_is_not_verified(setup):
    settings, _, _, _ = setup
    count = []

    def reply(request):
        if request.url.path == "/info":
            count.append(1)
            return httpx.Response(
                200, json={"ID": "test-daemon" if len(count) == 1 else "changed-daemon"}
            )
        return httpx.Response(
            200, json=[] if request.url.path == "/containers/json" else {"Volumes": []}
        )

    verifier = DockerCleanupVerifier(settings, transport=httpx.MockTransport(reply))
    try:
        assert not verifier.verify_absent("immutable-id", "al-name", daemon_id="test-daemon")
    finally:
        verifier.close()
