from types import SimpleNamespace


def test_client_authenticated_healthz(client):
    r = client.get("/internal/healthz")
    assert r.status_code == 200


def test_close_antcollector_client_shuts_down_and_clears_cache(monkeypatch):
    from agentlens.web import deps

    class _Client:
        def __init__(self):
            self.close_calls = 0

        def close(self):
            self.close_calls += 1

    client = _Client()
    deps.get_antcollector_client.cache_clear()
    monkeypatch.setattr(
        deps,
        "get_settings",
        lambda: SimpleNamespace(
            antcollector_url="http://collector",
            antcollector_access_id="",
            antcollector_access_token="",
            aivision_ingest_token_url="",
        ),
    )
    monkeypatch.setattr(deps, "HttpAntcollectorClient", lambda **_kwargs: client)

    assert deps.get_antcollector_client() is client
    deps.close_antcollector_client()
    deps.close_antcollector_client()

    assert client.close_calls == 1
    assert deps.get_antcollector_client.cache_info().currsize == 0
