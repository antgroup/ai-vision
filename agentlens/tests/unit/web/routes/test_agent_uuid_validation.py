import pytest


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        (
            "/api/v1/job",
            {
                "name": "empty-agent-job",
                "trigger_type": "manual",
                "dataset_uuid": "ds-unused",
                "diagnoser_uuids": [],
            },
        ),
        (
            "/api/v1/dataset",
            {
                "name": "empty-agent-dataset",
                "type": "signoz",
                "spec": {
                    "query": {"table": "trace"},
                    "runtime_type": "custom-runtime",
                },
            },
        ),
        (
            "/api/v1/diagnoser",
            {
                "name": "empty-agent-diagnoser",
                "type": "builtin",
                "mode": "rule",
                "spec": {
                    "executor": "test_fixed",
                    "metrics": [{"name": "quality"}],
                },
            },
        ),
    ],
)
@pytest.mark.parametrize("field", ["space_uuid", "agent_uuid"])
def test_create_routes_reject_empty_scope_uuid(client, path, payload, field):
    body = {**payload, "space_uuid": "s", "agent_uuid": "a", field: ""}

    response = client.post(path, json=body)

    assert response.status_code == 422


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/jobs",
        "/api/v1/datasets",
        "/api/v1/diagnosers",
    ],
)
@pytest.mark.parametrize("field", ["space_uuid", "agent_uuid"])
def test_list_routes_reject_empty_scope_uuid(client, path, field):
    params = {"space_uuid": "s", "agent_uuid": "a", field: ""}

    response = client.get(path, params=params)

    assert response.status_code == 422


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/diagnosers/sync-core-builtins",
        "/api/v1/diagnosers/sync-common-builtins",
    ],
)
@pytest.mark.parametrize("field", ["space_uuid", "agent_uuid"])
def test_builtin_sync_routes_reject_empty_scope_uuid(client, path, field):
    body = {"space_uuid": "s-shard", "agent_uuid": "a", field: ""}

    response = client.post(path, json=body)

    assert response.status_code == 422


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        (
            "/api/v1/job",
            {
                "space_uuid": "s",
                "name": "job",
                "trigger_type": "manual",
                "dataset_uuid": "ds-unused",
                "diagnoser_uuids": [],
            },
        ),
        (
            "/api/v1/dataset",
            {
                "space_uuid": "s",
                "name": "dataset",
                "type": "signoz",
                "spec": {
                    "query": {"table": "trace"},
                    "runtime_type": "custom-runtime",
                },
            },
        ),
        (
            "/api/v1/diagnoser",
            {
                "space_uuid": "s",
                "name": "diagnoser",
                "type": "custom",
                "mode": "rule",
                "spec": {
                    "code": "def evaluate(trace):\n    return {'metrics': []}\n",
                    "metrics": [{"name": "quality"}],
                },
            },
        ),
        (
            "/api/v1/diagnosers/sync-core-builtins",
            {"space_uuid": "s-shard"},
        ),
        (
            "/api/v1/diagnosers/sync-common-builtins",
            {"space_uuid": "s-shard"},
        ),
    ],
)
def test_resource_create_routes_require_concrete_agent_uuid(client, path, payload):
    missing = client.post(path, json=payload)
    reserved = client.post(path, json={**payload, "agent_uuid": "default"})

    assert missing.status_code == 422
    assert reserved.status_code == 422


def test_resource_list_openapi_exposes_only_current_shared_parameters(client):
    paths = client.get("/openapi.json").json()["paths"]
    diagnoser_params = {item["name"] for item in paths["/api/v1/diagnosers"]["get"]["parameters"]}
    dataset_params = {item["name"] for item in paths["/api/v1/datasets"]["get"]["parameters"]}
    job_params = {item["name"] for item in paths["/api/v1/jobs"]["get"]["parameters"]}

    shared_params = {"include_user_shared", "include_biz_shared", "include_shared"}
    assert shared_params <= diagnoser_params
    assert shared_params <= dataset_params
    assert job_params & shared_params == {"include_user_shared"}
    assert "default" not in diagnoser_params | dataset_params | job_params
