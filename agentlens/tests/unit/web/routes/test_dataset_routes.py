def test_dataset_crud(client):
    payload = {
        "space_uuid": "s",
        "agent_uuid": "a",
        "name": "ds1",
        "type": "signoz",
        "spec": {
            "query": {"table": "sample_trace", "time_range": "10m"},
            "runtime_type": "custom-runtime",
        },
    }
    r = client.post("/api/v1/dataset", json=payload)
    assert r.status_code == 201
    uuid = r.json()["uuid"]
    assert uuid.startswith("ds-")
    assert r.json()["type"] == "signoz"
    assert r.json()["spec"]["query"]["table"] == "sample_trace"

    assert client.get(f"/api/v1/dataset/{uuid}").json()["name"] == "ds1"

    body = client.get("/api/v1/datasets", params={"space_uuid": "s", "agent_uuid": "a"}).json()
    assert body["total"] == 1

    r = client.patch(f"/api/v1/dataset/{uuid}", json={"description": "x"})
    assert r.json()["description"] == "x"

    assert client.delete(f"/api/v1/dataset/{uuid}").status_code == 204
    assert client.get(f"/api/v1/dataset/{uuid}").status_code == 404


def test_dataset_list_includes_only_selected_shared_spaces(client):
    def create(space_uuid: str, agent_uuid: str, name: str) -> None:
        response = client.post(
            "/api/v1/dataset",
            json={
                "space_uuid": space_uuid,
                "agent_uuid": agent_uuid,
                "name": name,
                "type": "signoz",
                "spec": {
                    "query": {"table": "trace"},
                    "runtime_type": "custom-runtime",
                },
            },
        )
        assert response.status_code == 201, response.text

    create("s", "a", "local")
    create("s-user_shared", "a", "user")
    create("s-biz_shared", "a", "biz")
    create("s-shard", "a", "shared")
    create("s-biz_shared", "other-agent", "hidden")

    response = client.get(
        "/api/v1/datasets",
        params={
            "space_uuid": "s",
            "agent_uuid": "a",
            "include_biz_shared": True,
            "include_shared": True,
        },
    )

    assert response.status_code == 200, response.text
    assert [(item["space_uuid"], item["name"]) for item in response.json()["items"]] == [
        ("s", "local"),
        ("s-biz_shared", "biz"),
        ("s-shard", "shared"),
    ]


def test_referenced_dataset_cannot_be_deleted(client):
    dataset = client.post(
        "/api/v1/dataset",
        json={
            "space_uuid": "s",
            "agent_uuid": "a",
            "name": "referenced-dataset",
            "type": "signoz",
            "spec": {
                "query": {"table": "trace"},
                "runtime_type": "custom-runtime",
            },
        },
    )
    assert dataset.status_code == 201, dataset.text
    diagnoser = client.post(
        "/api/v1/diagnoser",
        json={
            "space_uuid": "s",
            "agent_uuid": "a",
            "name": "dataset-delete-diagnoser",
            "type": "custom",
            "mode": "rule",
            "spec": {
                "code": (
                    "def evaluate(trace):\n"
                    "    return {'metrics': [{'name': 'quality', "
                    "'verdict': 'good', 'reason': 'ok', 'evidence': {}}]}\n"
                ),
                "metrics": [{"name": "quality"}],
            },
        },
    )
    assert diagnoser.status_code == 201, diagnoser.text
    job = client.post(
        "/api/v1/job",
        json={
            "space_uuid": "s",
            "agent_uuid": "a",
            "name": "dataset-delete-job",
            "trigger_type": "manual",
            "status": "enabled",
            "dataset_uuid": dataset.json()["uuid"],
            "diagnoser_uuids": [diagnoser.json()["uuid"]],
        },
    )
    assert job.status_code == 201, job.text

    response = client.delete(f"/api/v1/dataset/{dataset.json()['uuid']}")

    assert response.status_code == 409
    assert client.get(f"/api/v1/dataset/{dataset.json()['uuid']}").status_code == 200


def test_dataset_duplicate_409(client):
    p = {
        "space_uuid": "s",
        "agent_uuid": "a",
        "name": "dup",
        "type": "signoz",
        "spec": {
            "query": {"table": "t"},
            "runtime_type": "custom-runtime",
        },
    }
    assert client.post("/api/v1/dataset", json=p).status_code == 201
    assert client.post("/api/v1/dataset", json=p).status_code == 409


def test_dataset_unknown_spec_fields_are_preserved(client):
    response = client.post(
        "/api/v1/dataset",
        json={
            "space_uuid": "s",
            "agent_uuid": "a",
            "name": "unknown-field",
            "type": "signoz",
            "spec": {
                "query": {"table": "trace", "unexpected": True},
                "runtime_type": "custom-runtime",
                "custom_extension": {"enabled": True},
            },
        },
    )
    assert response.status_code == 201, response.text
    spec = response.json()["spec"]
    assert spec["custom_extension"] == {"enabled": True}
    assert spec["query"]["unexpected"] is True

    updated = client.patch(
        f"/api/v1/dataset/{response.json()['uuid']}",
        json={
            "spec": {
                "query": {"table": "trace", "new_query_extension": ["x"]},
                "runtime_type": "custom-runtime",
                "new_extension": "kept",
            }
        },
    )
    assert updated.status_code == 200, updated.text
    updated_spec = updated.json()["spec"]
    assert updated_spec["new_extension"] == "kept"
    assert updated_spec["query"]["new_query_extension"] == ["x"]


def test_dataset_nested_maps_must_be_objects(client):
    payload = {
        "space_uuid": "s",
        "agent_uuid": "a",
        "name": "bad-map",
        "type": "signoz",
        "spec": {
            "runtime_type": "custom-runtime",
            "query": "bad",
        },
    }
    response = client.post("/api/v1/dataset", json=payload)
    assert response.status_code == 422


def test_dataset_limit_must_be_a_positive_integer(client):
    base = {
        "space_uuid": "s",
        "agent_uuid": "a",
        "name": "limit-check",
        "type": "signoz",
        "spec": {
            "query": {"table": "t"},
            "runtime_type": "custom-runtime",
        },
    }

    for limit in (0, -1, 1.5, True):
        payload = {
            **base,
            "name": f"limit-{str(limit).lower()}",
            "spec": {
                **base["spec"],
                "query": {"table": "t", "limit": limit},
            },
        }
        assert client.post("/api/v1/dataset", json=payload).status_code == 422

    payload = {
        **base,
        "spec": {
            **base["spec"],
            "query": {"table": "t", "limit": 10_001},
        },
    }
    assert client.post("/api/v1/dataset", json=payload).status_code == 201


def test_signoz_scope_fields_must_be_non_empty_pairs(client):
    base = {
        "space_uuid": "s",
        "agent_uuid": "a",
        "name": "scope-check",
        "type": "signoz",
        "spec": {
            "query": {"table": "trace"},
            "runtime_type": "custom-runtime",
        },
    }
    missing_value = {
        **base,
        "spec": {**base["spec"], "username_column": "attributes.user.id"},
    }
    assert client.post("/api/v1/dataset", json=missing_value).status_code == 422

    blank_column = {
        **base,
        "name": "scope-blank",
        "spec": {
            **base["spec"],
            "username_column": " ",
            "username": "user-1",
        },
    }
    assert client.post("/api/v1/dataset", json=blank_column).status_code == 422

    valid = {
        **base,
        "name": "scope-valid",
        "spec": {
            **base["spec"],
            "username_column": "attributes.user.id",
            "username": "user-1",
            "agent_product_column": "resource.attributes.agent.product",
            "agent_product_filter": "Code Agent",
        },
    }
    assert client.post("/api/v1/dataset", json=valid).status_code == 201


def test_dataset_runtime_type_is_required_and_preserved(client):
    base = {
        "space_uuid": "s",
        "agent_uuid": "a",
        "name": "rt-missing",
        "type": "signoz",
        "spec": {"signoz": {}, "query": {"table": "t"}},
    }
    # missing runtime_type -> 422
    assert client.post("/api/v1/dataset", json=base).status_code == 422
    blank = {
        **base,
        "name": "rt-blank",
        "spec": {**base["spec"], "runtime_type": " "},
    }
    assert client.post("/api/v1/dataset", json=blank).status_code == 422
    # mixed-case is normalized by the shared runtime validator
    p = {**base, "name": "rt-upper", "spec": {**base["spec"], "runtime_type": "CUSTOM-RUNTIME"}}
    normalized = client.post("/api/v1/dataset", json=p)
    assert normalized.status_code == 201
    assert normalized.json()["spec"]["runtime_type"] == "CUSTOM-RUNTIME"
    # Runtime registration is resolved by the projector path, not Dataset CRUD.
    p = {**base, "name": "rt-unknown", "spec": {**base["spec"], "runtime_type": "unknown"}}
    unknown = client.post("/api/v1/dataset", json=p)
    assert unknown.status_code == 201
    assert unknown.json()["spec"]["runtime_type"] == "unknown"
    # lowercase runtime -> 201
    p = {**base, "name": "rt-ok", "spec": {**base["spec"], "runtime_type": "custom-runtime"}}
    r = client.post("/api/v1/dataset", json=p)
    assert r.status_code == 201, r.text
    assert r.json()["spec"]["runtime_type"] == "custom-runtime"
    # update preserves the submitted runtime string
    uuid = r.json()["uuid"]
    bad = client.patch(
        f"/api/v1/dataset/{uuid}",
        json={
            "spec": {
                "query": {"table": "t"},
                "runtime_type": "nope",
            }
        },
    )
    assert bad.status_code == 200
    assert bad.json()["spec"]["runtime_type"] == "nope"
