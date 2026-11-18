import httpx
import pytest


def discover(endpoint, key, handler):
    from agentlens.infra.llm_judge.model_discovery import discover_models

    return discover_models(endpoint, key, transport=httpx.MockTransport(handler))


def test_directory_filters_invalid_duplicate_and_non_chat_models():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "chat"},
                    {"id": "chat"},
                    {"id": 42},
                    {"id": "bad\nid"},
                    {"id": "x" * 257},
                    {"id": "embedding", "capabilities": {"completion_chat": False}},
                    {"id": "another", "capabilities": {"completion_chat": True}},
                ]
            },
        )

    result = discover("https://api.mistral.ai/v1/", "private-key", respond)
    assert result == ["chat", "another"]
    assert len(requests) == 1
    assert str(requests[0].url) == "https://api.mistral.ai/v1/models"
    assert requests[0].headers["authorization"] == "Bearer private-key"
    assert requests[0].headers["accept-encoding"] == "identity"
    assert requests[0].extensions["timeout"]["read"] <= 10


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://api.openai.com/v1",
        "https://api.openai.com.evil.test/v1",
        "https://user:secret@api.openai.com/v1",
        "https://api.openai.com/v1?target=localhost",
        "https://api.openai.com/v1#fragment",
        "https://api.openai.com:8443/v1",
        "http://127.0.0.1/v1",
        "http://169.254.169.254",
        "https://api.openai.com/v1/../admin",
    ],
)
def test_unsupported_target_never_receives_a_request(endpoint):
    from agentlens.infra.llm_judge.model_discovery import ModelDiscoveryError

    def unexpected(request):
        pytest.fail(f"Unsupported target contacted: {request.url}")

    with pytest.raises(ModelDiscoveryError) as error:
        discover(endpoint, "private-key", unexpected)
    assert error.value.code == "unsupported_endpoint"


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (301, "unavailable"),
        (401, "authentication_failed"),
        (429, "rate_limited"),
        (500, "unavailable"),
    ],
)
def test_upstream_failures_are_safe_and_never_follow_redirects(status, code):
    from agentlens.infra.llm_judge.model_discovery import ModelDiscoveryError

    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(
            status,
            headers={"location": "http://127.0.0.1/secrets"},
            text="private-key\nupstream internal detail",
        )

    with pytest.raises(ModelDiscoveryError) as error:
        discover("https://api.openai.com/v1", "private-key", respond)
    assert error.value.code == code
    assert "private-key" not in str(error.value)
    assert len(requests) == 1


@pytest.mark.parametrize(
    ("content", "headers"),
    [
        (b"not json", {}),
        (b'{"data":{}}', {}),
        (b'{"data":[]}', {"content-encoding": "gzip"}),
        (b" " * (2 * 1024 * 1024 + 1), {}),
        (b'{"data":[]}', {"content-length": str(2 * 1024 * 1024 + 1)}),
    ],
)
def test_invalid_or_oversized_directories_fail_closed(content, headers):
    from agentlens.infra.llm_judge.model_discovery import ModelDiscoveryError

    with pytest.raises(ModelDiscoveryError) as error:
        discover(
            "https://api.openai.com/v1",
            "private-key",
            lambda _: httpx.Response(200, stream=httpx.ByteStream(content), headers=headers),
        )
    assert error.value.code == "invalid_response"


def test_stream_elapsed_budget_stops_slow_catalog(monkeypatch):
    from agentlens.infra.llm_judge import model_discovery

    times = iter([0.0, 0.0, 11.0])
    monkeypatch.setattr(model_discovery.time, "monotonic", lambda: next(times))
    with pytest.raises(model_discovery.ModelDiscoveryError) as error:
        discover(
            "https://api.openai.com/v1",
            "private-key",
            lambda _: httpx.Response(200, json={"data": []}),
        )
    assert error.value.code == "unavailable"


def test_stream_budget_checks_each_small_network_chunk(monkeypatch):
    from agentlens.infra.llm_judge import model_discovery

    elapsed = 0.0
    received = 0

    class SlowStream(httpx.SyncByteStream):
        def __iter__(self):
            nonlocal elapsed, received
            for _ in range(20_000):
                elapsed += 1
                received += 1
                yield b" "

    monkeypatch.setattr(model_discovery.time, "monotonic", lambda: elapsed)
    with pytest.raises(model_discovery.ModelDiscoveryError) as error:
        discover(
            "https://api.openai.com/v1",
            "private-key",
            lambda _: httpx.Response(200, stream=SlowStream()),
        )
    assert error.value.code == "unavailable"
    assert received <= 10


@pytest.mark.parametrize("key", ["key\nheader", "key\x00", "key 中文", "k" * 8193])
def test_invalid_header_values_never_reach_provider(key):
    from agentlens.infra.llm_judge.model_discovery import ModelDiscoveryError

    def unexpected(request):
        pytest.fail("Invalid credential reached provider")

    with pytest.raises(ModelDiscoveryError) as error:
        discover("https://api.openai.com/v1", key, unexpected)
    assert error.value.code == "invalid_request"
    assert key not in str(error.value)


def test_timeout_never_echoes_request_credentials():
    from agentlens.infra.llm_judge.model_discovery import ModelDiscoveryError

    def timeout(request):
        raise httpx.ReadTimeout("private-key", request=request)

    with pytest.raises(ModelDiscoveryError) as error:
        discover("https://api.openai.com/v1", "private-key", timeout)
    assert error.value.code == "unavailable"
    assert "private-key" not in str(error.value)


def test_qwen_uses_native_paged_catalog_and_same_region_key():
    requests = []

    def respond(request):
        requests.append(request)
        page = int(request.url.params["page_no"])
        return httpx.Response(
            200,
            json={
                "success": True,
                "output": {
                    "total": 2,
                    "models": [{"model": f"qwen-{page}", "capabilities": ["TG"]}],
                },
            },
        )

    result = discover(
        "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "private-key",
        respond,
    )
    assert result == ["qwen-1", "qwen-2"]
    assert len(requests) == 2
    assert requests[0].url.host == "dashscope-intl.aliyuncs.com"
    assert requests[0].url.path == "/api/v1/models"
    assert requests[0].url.params["capabilities"] == "TG"
    assert requests[0].url.params["supports"] == "inference"
    assert "private-key" not in str(requests[0].url)


def test_directory_bounds_models_and_rejects_deep_json():
    from agentlens.infra.llm_judge.model_discovery import ModelDiscoveryError

    result = discover(
        "https://api.openai.com/v1",
        "private-key",
        lambda _: httpx.Response(200, json={"data": [{"id": str(i)} for i in range(2001)]}),
    )
    assert len(result) == 2000
    content = b"[" * 2000 + b"]" * 2000
    with pytest.raises(ModelDiscoveryError) as error:
        discover(
            "https://api.openai.com/v1",
            "private-key",
            lambda _: httpx.Response(200, content=content),
        )
    assert error.value.code == "invalid_response"


def test_nvidia_uses_verified_hosted_catalog_with_bearer_auth():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={"object": "list", "data": [{"id": "nvidia/chat-fixture", "object": "model"}]},
        )

    result = discover("https://integrate.api.nvidia.com/v1", "private-key", respond)
    assert result == ["nvidia/chat-fixture"]
    assert str(requests[0].url) == "https://integrate.api.nvidia.com/v1/models"
    assert requests[0].headers["authorization"] == "Bearer private-key"


def test_anthropic_uses_native_catalog_headers_and_bounded_cursor_pages():
    requests = []

    def respond(request):
        requests.append(request)
        page = 2 if request.url.params.get("after_id") == "claude-first" else 1
        return httpx.Response(
            200,
            json={
                "data": [{"id": "claude-first" if page == 1 else "claude-second"}],
                "has_more": page == 1,
                "last_id": "claude-first" if page == 1 else "claude-second",
            },
        )

    result = discover("https://api.anthropic.com/v1/", "private-key", respond)
    assert result == ["claude-first", "claude-second"]
    assert len(requests) == 2
    assert requests[0].url.path == "/v1/models"
    assert requests[0].url.params["limit"] == "100"
    assert "after_id" not in requests[0].url.params
    assert requests[1].url.params["after_id"] == "claude-first"
    assert requests[0].headers["x-api-key"] == "private-key"
    assert requests[0].headers["anthropic-version"] == "2023-06-01"
    assert "authorization" not in requests[0].headers
    assert "private-key" not in str(requests[0].url)


@pytest.mark.parametrize("cursor", [None, "", "private-key", "bad\ncursor", "x" * 257])
def test_anthropic_rejects_invalid_or_sensitive_cursor(cursor):
    from agentlens.infra.llm_judge.model_discovery import ModelDiscoveryError

    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={"data": [{"id": "claude-fixture"}], "has_more": True, "last_id": cursor},
        )

    with pytest.raises(ModelDiscoveryError) as error:
        discover("https://api.anthropic.com/v1", "private-key", respond)
    assert error.value.code == "invalid_response"
    assert len(requests) == 1


def test_anthropic_stops_repeated_cursor_without_spending_full_page_budget():
    from agentlens.infra.llm_judge.model_discovery import ModelDiscoveryError

    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={"data": [{"id": "claude-fixture"}], "has_more": True, "last_id": "same-cursor"},
        )

    with pytest.raises(ModelDiscoveryError) as error:
        discover("https://api.anthropic.com/v1", "private-key", respond)
    assert error.value.code == "invalid_response"
    assert len(requests) == 2
