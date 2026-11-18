def test_lifespan_starts_runtime(client):
    r = client.get("/internal/healthz")
    assert r.status_code == 200
