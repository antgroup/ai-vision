from agentlens.infra.ob.tables.heartbeat_table import HeartbeatTable


def test_list_instances(client, session):
    HeartbeatTable(session).upsert("n1", "1.1.1.1")
    HeartbeatTable(session).upsert("n2", "2.2.2.2")
    r = client.get("/api/v1/cluster/instances")
    body = r.json()
    assert {it["uuid"] for it in body["items"]} == {"n1/1.1.1.1", "n2/2.2.2.2"}


def test_leader_info_returns_seed_bootstrap(client):
    r = client.get("/api/v1/cluster/leader")
    assert r.status_code == 200
    assert "holder_uuid" in r.json()
