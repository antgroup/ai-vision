import pytest


@pytest.fixture
def cleanup_client():
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from agentlens.web.deps import get_diagnoser_table, get_model_connection_table, get_settings
    from agentlens.web.errors import install_error_handlers
    from agentlens.web.routes.diagnoser import router
    from tests.unit.web.test_llm_generation_options import _Table

    table = _Table()
    app = FastAPI()
    app.include_router(router)
    install_error_handlers(app)
    app.dependency_overrides[get_diagnoser_table] = lambda: table
    app.dependency_overrides[get_model_connection_table] = lambda: SimpleNamespace()
    app.dependency_overrides[get_settings] = lambda: SimpleNamespace(
        default_builtin_scenario_list=()
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, table


def test_retired_sync_api_is_removed_without_provisioning(cleanup_client):
    client, table = cleanup_client
    response = client.post(
        "/api/v1/diagnosers/sync-legacy-builtins", json={"space_uuid": "s", "agent_uuid": "a"}
    )
    assert response.status_code == 404, response.text
    assert table.rows == {}


def test_default_runtime_variables_use_other(cleanup_client):
    client, _ = cleanup_client
    response = client.get("/api/v1/diagnoser/available-vars")
    assert response.status_code == 200, response.text
    assert response.json()["runtime_type"] == "other"
    assert set(response.json()["vars"]) == {
        "trace_id",
        "runtime_type",
        "source_type",
        "source_meta",
        "spans",
    }


def test_core_presets_are_opt_in_and_preserve_existing_canonical_rows(cleanup_client):
    from copy import deepcopy

    from agentlens.diagnoser.definitions.catalog import load_core_definitions
    from agentlens.web.routes.diagnoser import _sync_builtin

    client, table = cleanup_client
    definitions = load_core_definitions()
    scope = {"space_uuid": "personal", "agent_uuid": "one-agent"}
    existing, _ = _sync_builtin(table, scope["space_uuid"], scope["agent_uuid"], definitions[0])
    existing.spec.update(model_connection_uuid="saved-judge", temperature=0.4)
    # A previous definition's frozen conditions and identity survive ordinary reads.
    existing.description = "Previously saved description"
    before = deepcopy(existing)

    ordinary = client.get("/api/v1/diagnosers", params=scope).json()["items"]
    assert [row["uuid"] for row in ordinary] == [before.uuid]

    params = {**scope, "ensure_core_presets": True}
    provisioned = client.get("/api/v1/diagnosers", params=params)
    assert provisioned.status_code == 200, provisioned.text
    rows = provisioned.json()["items"]
    assert {(row["spec"]["builtin_key"], row["spec"]["canonical_variant"]) for row in rows} == {
        (definition["key"], definition["variant"]) for definition in definitions
    }
    assert len(rows) == len({row["uuid"] for row in rows}) == 4
    assert all(row["type"] == "builtin" and row["mode"] == "llm" for row in rows)
    assert table.get_by_uuid(before.uuid) == before
    assert client.get("/api/v1/diagnosers", params=params).json()["items"] == rows
    assert table.list_exact_scope(scope["space_uuid"], "other") == []


@pytest.mark.parametrize(
    "scope",
    [
        {"space_uuid": "s-user_shared", "agent_uuid": "a"},
        {"space_uuid": "s", "agent_uuid": "default"},
    ],
)
def test_core_presets_require_concrete_scope(cleanup_client, scope):
    client, table = cleanup_client
    response = client.get("/api/v1/diagnosers", params={**scope, "ensure_core_presets": True})
    assert response.status_code == 200, response.text
    assert table.rows == {}


def test_core_preset_name_collision_keeps_custom_and_other_presets(cleanup_client):
    from agentlens.diagnoser.definitions.catalog import load_core_definitions

    client, table = cleanup_client
    scope = {"space_uuid": "personal", "agent_uuid": "agent-a"}
    definition = load_core_definitions()[0]
    created = client.post(
        "/api/v1/diagnoser",
        json={
            **scope,
            "name": definition["name"],
            "type": "custom",
            "mode": "rule",
            "spec": _custom_rule_spec(),
        },
    )
    assert created.status_code == 201, created.text
    response = client.get("/api/v1/diagnosers", params={**scope, "ensure_core_presets": True})
    assert response.status_code == 200, response.text
    rows = response.json()["items"]
    assert [row for row in rows if row["type"] == "custom"] == [created.json()]
    assert len([row for row in rows if row["type"] == "builtin"]) == 3
    assert set(table.rows) == {row["uuid"] for row in rows}


def test_core_presets_keep_concurrent_winner_and_its_model(cleanup_client, monkeypatch):
    from dataclasses import replace

    from sqlalchemy.exc import IntegrityError

    from agentlens.diagnoser.definitions.catalog import load_core_definitions

    client, table = cleanup_client
    scope = {"space_uuid": "personal", "agent_uuid": "agent-race"}
    first = load_core_definitions()[0]
    insert = table.insert

    def provision_concurrent_winner(row):
        if row.name == first["name"]:
            insert(
                replace(
                    row,
                    uuid="race-winner",
                    description="Existing description",
                    spec={**row.spec, "model_connection_uuid": "winner-model"},
                )
            )
            raise IntegrityError("insert", {}, ValueError("unique constraint"))
        insert(row)

    monkeypatch.setattr(table, "insert", provision_concurrent_winner)
    monkeypatch.setattr(table, "rollback", lambda: None, raising=False)
    response = client.get("/api/v1/diagnosers", params={**scope, "ensure_core_presets": True})
    assert response.status_code == 200, response.text
    assert len(response.json()["items"]) == 4
    winner = next(row for row in response.json()["items"] if row["name"] == first["name"])
    assert winner["uuid"] == "race-winner"
    assert winner["description"] == "Existing description"
    assert winner["spec"]["model_connection_uuid"] == "winner-model"


def test_retired_name_cannot_create_a_builtin_via_deleted_yaml(cleanup_client):
    client, table = cleanup_client
    response = client.post(
        "/api/v1/diagnoser",
        json={
            "space_uuid": "s",
            "agent_uuid": "a",
            "name": "历史辱骂诊断",
            "type": "builtin",
            "mode": "llm",
            "spec": {},
        },
    )
    assert response.status_code == 422, response.text
    assert table.rows == {}


@pytest.mark.parametrize("runtime", [None, "legacy", "unknown-custom-agent"])
def test_custom_raw_runtime_and_prompt_survive_create_patch(cleanup_client, runtime):
    client, _ = cleanup_client
    spec = {"prompt": "Inspect {{runtime_type}} and {{spans}}", "metrics": [{"name": "quality"}]}
    if runtime is not None:
        spec["runtime_type"] = runtime
    created = client.post(
        "/api/v1/diagnoser",
        json={
            "space_uuid": "s",
            "agent_uuid": "a",
            "name": "raw-runtime",
            "type": "custom",
            "mode": "llm",
            "spec": spec,
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["spec"]["prompt"] == spec["prompt"]
    assert created.json()["spec"].get("runtime_type") == runtime
    patched = client.patch(f"/api/v1/diagnoser/{created.json()['uuid']}", json={"spec": spec})
    assert patched.status_code == 200, patched.text
    assert patched.json()["spec"]["prompt"] == spec["prompt"]
    assert patched.json()["spec"].get("runtime_type") == runtime


def _custom_rule_spec(metric_name: str = "quality") -> dict:
    return {
        "code": (
            "def evaluate(trace):\n"
            f"    return {{'metrics': [{{'name': '{metric_name}', "
            "'verdict': 'good', 'reason': 'ok', 'evidence': {}}]}\n"
        ),
        "metrics": [{"name": metric_name}],
    }


def test_managed_rule_copy_preserves_execution_and_edits_independently(cleanup_client, monkeypatch):
    from agentlens.diagnoser.base import ApplicabilitySkipped, execute_with_applicability
    from agentlens.diagnoser.registry import ExecutorRegistry, resolve_executor_name
    from agentlens.domain.enums import Verdict
    from agentlens.domain.execution_policy import bounded_diagnosis_execution_policy
    from agentlens.service.execution_snapshot import (
        build_execution_snapshot,
        restore_execution_snapshot,
    )
    from agentlens.service.quick_runs import saved_options
    from tests.unit.diagnoser.test_builtin_rule_executor import _dt
    from tests.unit.diagnoser.test_rule_identity_v3 import existing, task_inputs

    client, table = cleanup_client
    source = existing("total_latency", version="3.0.0")
    source.spec["latency_budget_ms"] = 1000
    table.insert(source)
    url = f"/api/v1/diagnoser/{source.uuid}"
    scope = {"space_uuid": "space", "agent_uuid": "agent"}
    for cross_scope in (dict(scope, space_uuid="elsewhere"), dict(scope, agent_uuid="elsewhere")):
        failed = client.post(f"{url}/copy", json={**cross_scope, "name": "wrong scope"})
        assert failed.status_code == 404, failed.text
    response = client.post(f"{url}/copy", json={**scope, "name": "Latency copy"})
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["type"] == "custom"
    assert data["mode"] == "rule"
    assert data["uuid"] != source.uuid
    assert data["version"] == source.version
    assert data["spec"] == source.spec | {
        "builtin_rule_source": {"key": "total_latency", "version": "3.0.0"}
    }

    copied = table.get_by_uuid(data["uuid"])
    import agentlens.service.quick_runs as quick_runs

    monkeypatch.setattr(quick_runs, "DiagnoserTable", lambda session: table)
    options = saved_options(None, {**scope, "dataset_spec": {"runtime_type": "codex", "query": {}}})
    assert next(row for row in options["diagnosers"] if row["uuid"] == copied.uuid)["selectable"]
    job, dataset = task_inputs(copied)
    snapshot = build_execution_snapshot(
        job, dataset, [copied], execution_policy=bounded_diagnosis_execution_policy(["trace"])
    )
    assert snapshot["executor_revision"] == "1.7.0"
    assert snapshot["diagnosers"][0]["key"] == "total_latency"
    assert "sandbox" not in snapshot["diagnosers"][0]["spec"]
    _, _, [restored] = restore_execution_snapshot(snapshot)
    executor = ExecutorRegistry.create(
        restored.mode.value,
        resolve_executor_name(restored.type, restored.mode, restored.spec),
        restored.spec,
    )
    original = ExecutorRegistry.create("rule", "total_latency", source.spec)
    for time_ms, verdict in ((999, Verdict.GOOD), (1100, Verdict.BAD)):
        trace = _dt(outcome={"total_latency_ms": time_ms})
        actual = execute_with_applicability(executor, trace)
        assert actual == execute_with_applicability(original, trace)
        assert actual.metrics[0].verdict == verdict
    missing = _dt(outcome={"total_latency_ms": None})
    actual_skip = execute_with_applicability(executor, missing)
    original_skip = execute_with_applicability(original, missing)
    assert isinstance(actual_skip, ApplicabilitySkipped)
    assert isinstance(original_skip, ApplicabilitySkipped)
    assert actual_skip.reason == original_skip.reason

    copy_url = f"/api/v1/diagnoser/{copied.uuid}"
    response = client.patch(copy_url, json={"name": "My latency", "spec": {"latency_budget_ms": 0}})
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "My latency"
    assert response.json()["spec"]["latency_budget_ms"] == 0
    rejected = client.patch(copy_url, json={"spec": {"tool_success_min_rate": None}})
    assert rejected.status_code == 422, rejected.text
    rejected = client.patch(copy_url, json={"spec": {"latency_budget_ms": 10**400}})
    assert rejected.status_code == 422, rejected.text
    response = client.patch(copy_url, json={"spec": {"latency_budget_ms": None}})
    assert response.status_code == 200, response.text
    assert "latency_budget_ms" not in response.json()["spec"]
    assert table.get_by_uuid(source.uuid).spec["latency_budget_ms"] == 1000
    assert restore_execution_snapshot(snapshot)[2][0].spec["latency_budget_ms"] == 1000

    response = client.patch(copy_url, json={"spec": _custom_rule_spec("latency_decomposition")})
    assert response.status_code == 200, response.text
    converted = response.json()
    assert converted["version"] == "v1"
    assert "builtin_rule_source" not in converted["spec"]
    assert converted["spec"]["score_role"] == "signal"
    assert converted["spec"]["code"] == _custom_rule_spec("latency_decomposition")["code"]

    response = client.patch(
        url, json={"name": "cannot rename builtin", "spec": {"latency_budget_ms": None}}
    )
    assert response.status_code == 200, response.text
    assert response.json()["name"] == source.name
    assert "latency_budget_ms" not in response.json()["spec"]


def test_public_diagnoser_writes_cannot_forge_rule_copy_provenance(cleanup_client):
    from agentlens.domain.errors import Validation
    from agentlens.web.routes.diagnoser import _prepare_rule_trial
    from agentlens.web.schemas.diagnoser import RuleTrialRequest
    from tests.unit.diagnoser.test_rule_identity_v3 import existing

    client, table = cleanup_client
    source = existing("tool_success", version="3.0.0")
    table.insert(source)
    spec = {
        **_custom_rule_spec(),
        "builtin_rule_source": {"key": "tool_success", "version": "3.0.0"},
    }
    response = client.post(
        "/api/v1/diagnoser",
        json={
            "space_uuid": "space",
            "agent_uuid": "agent",
            "name": "forged",
            "type": "custom",
            "mode": "rule",
            "spec": spec,
        },
    )
    assert response.status_code == 422, response.text
    response = client.patch(f"/api/v1/diagnoser/{source.uuid}", json={"spec": spec})
    assert response.status_code == 422, response.text
    with pytest.raises(Validation, match="builtin_rule_source"):
        _prepare_rule_trial(RuleTrialRequest(spec=spec, test_input={}), None)
    assert len(table.rows) == 1


def test_old_unknown_field_does_not_change_python_rule_execution(cleanup_client):
    from dataclasses import replace

    from agentlens.domain.enums import DiagnoserType
    from agentlens.service.execution_snapshot import build_execution_snapshot
    from tests.unit.diagnoser.test_rule_identity_v3 import existing, task_inputs

    client, table = cleanup_client
    row = replace(
        existing(),
        type=DiagnoserType.CUSTOM,
        spec={**_custom_rule_spec(), "builtin_rule_source": {"historical": True}},
    )
    table.insert(row)
    response = client.get(f"/api/v1/diagnoser/{row.uuid}")
    assert response.status_code == 200, response.text
    assert "builtin_rule_source" not in response.json()["spec"]
    job, dataset = task_inputs(row)
    snapshot = build_execution_snapshot(job, dataset, [row])
    assert snapshot["executor_revision"] == "1.6.0"
    assert snapshot["diagnosers"][0]["key"] == "rule_custom"


def test_first_custom_write_also_provisions_configured_builtin_rules(cleanup_client):
    from types import SimpleNamespace

    from agentlens.web.deps import get_settings

    client, table = cleanup_client
    client.app.dependency_overrides[get_settings] = lambda: SimpleNamespace(
        default_builtin_scenario_list=("common", "coding")
    )
    scope = {"space_uuid": "space", "agent_uuid": "agent"}
    response = client.post(
        "/api/v1/diagnoser",
        json={
            **scope,
            "name": "Custom",
            "type": "custom",
            "mode": "rule",
            "spec": _custom_rule_spec(),
        },
    )
    assert response.status_code == 201, response.text
    assert len(table.list_exact_scope(**scope)) == 9


def test_executor_spec_requires_a_registered_executor():
    import pytest

    from agentlens.domain.enums import DiagnoserMode, DiagnoserType
    from agentlens.domain.errors import Validation
    from agentlens.web.routes.diagnoser import _validate_executor_spec

    with pytest.raises(Validation, match="metrics"):
        _validate_executor_spec({}, DiagnoserType.CUSTOM, DiagnoserMode.LLM)

    with pytest.raises(Validation, match="缺少系统 Executor"):
        _validate_executor_spec({}, DiagnoserType.BUILTIN, DiagnoserMode.RULE)
    with pytest.raises(Validation, match="retired builtin"):
        _validate_executor_spec({"executor": "not_real"}, DiagnoserType.BUILTIN, DiagnoserMode.RULE)


def test_create_get_list_update_delete_diagnoser(client):
    # CREATE
    payload = {
        "space_uuid": "s",
        "agent_uuid": "a",
        "name": "d1",
        "description": "",
        "type": "custom",
        "mode": "rule",
        "spec": _custom_rule_spec(),
        "version": "v1",
    }
    r = client.post("/api/v1/diagnoser", json=payload)
    assert r.status_code == 201
    uuid = r.json()["uuid"]
    assert uuid.startswith("dg-")

    # GET
    r = client.get(f"/api/v1/diagnoser/{uuid}")
    assert r.status_code == 200
    assert r.json()["name"] == "d1"

    # LIST
    r = client.get(
        "/api/v1/diagnosers",
        params={"space_uuid": "s", "agent_uuid": "a"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["uuid"] == uuid

    # UPDATE
    r = client.patch(f"/api/v1/diagnoser/{uuid}", json={"description": "new"})
    assert r.status_code == 200
    assert r.json()["description"] == "new"

    # DELETE
    r = client.delete(f"/api/v1/diagnoser/{uuid}")
    assert r.status_code == 204
    assert client.get(f"/api/v1/diagnoser/{uuid}").status_code == 404


def test_custom_create_and_update_require_explicit_non_empty_metrics(client):
    missing = client.post(
        "/api/v1/diagnoser",
        json={
            "space_uuid": "s",
            "agent_uuid": "a",
            "name": "missing-metrics",
            "type": "custom",
            "mode": "rule",
            "spec": {"code": "def evaluate(trace):\n    return {'metrics': []}\n"},
        },
    )
    assert missing.status_code == 422
    assert "至少一个指标" in missing.text

    created = client.post(
        "/api/v1/diagnoser",
        json={
            "space_uuid": "s",
            "agent_uuid": "a",
            "name": "explicit-metrics",
            "type": "custom",
            "mode": "rule",
            "spec": _custom_rule_spec(),
        },
    )
    assert created.status_code == 201, created.text

    emptied = client.patch(
        f"/api/v1/diagnoser/{created.json()['uuid']}",
        json={
            "spec": {
                "code": "def evaluate(trace):\n    return {'metrics': []}\n",
                "metrics": [],
            }
        },
    )
    assert emptied.status_code == 422
    assert "至少一个指标" in emptied.text


def test_bulk_delete_diagnosers_requires_non_empty_scope(client):
    assert client.delete("/api/v1/diagnosers").status_code == 422
    assert (
        client.delete(
            "/api/v1/diagnosers",
            params={"space_uuid": "s", "agent_uuid": ""},
        ).status_code
        == 422
    )


def test_bulk_delete_diagnosers_deletes_only_exact_scope(client):
    def create(name: str, agent_uuid: str) -> str:
        response = client.post(
            "/api/v1/diagnoser",
            json={
                "space_uuid": "s",
                "agent_uuid": agent_uuid,
                "name": name,
                "type": "custom",
                "mode": "rule",
                "spec": _custom_rule_spec(),
            },
        )
        assert response.status_code == 201, response.text
        return response.json()["uuid"]

    deleted_uuids = [create("d1", "a"), create("d2", "a")]
    retained_uuid = create("d3", "b")

    response = client.delete(
        "/api/v1/diagnosers",
        params={"space_uuid": "s", "agent_uuid": "a"},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "deleted": 2,
        "diagnoser_uuids": deleted_uuids,
    }
    for uuid in deleted_uuids:
        assert client.get(f"/api/v1/diagnoser/{uuid}").status_code == 404
    assert client.get(f"/api/v1/diagnoser/{retained_uuid}").status_code == 200


def test_referenced_diagnoser_blocks_single_and_bulk_delete(client):
    def create_diagnoser(name: str) -> str:
        response = client.post(
            "/api/v1/diagnoser",
            json={
                "space_uuid": "s",
                "agent_uuid": "a",
                "name": name,
                "type": "custom",
                "mode": "rule",
                "spec": _custom_rule_spec(),
            },
        )
        assert response.status_code == 201, response.text
        return response.json()["uuid"]

    referenced_uuid = create_diagnoser("referenced")
    unreferenced_uuid = create_diagnoser("unreferenced")
    dataset = client.post(
        "/api/v1/dataset",
        json={
            "space_uuid": "s",
            "agent_uuid": "a",
            "name": "diagnoser-delete-dataset",
            "type": "signoz",
            "spec": {
                "query": {"table": "trace"},
                "runtime_type": "custom-runtime",
            },
        },
    )
    assert dataset.status_code == 201, dataset.text
    job = client.post(
        "/api/v1/job",
        json={
            "space_uuid": "s",
            "agent_uuid": "a",
            "name": "diagnoser-delete-job",
            "trigger_type": "manual",
            "status": "enabled",
            "dataset_uuid": dataset.json()["uuid"],
            "diagnoser_uuids": [referenced_uuid],
        },
    )
    assert job.status_code == 201, job.text

    single = client.delete(f"/api/v1/diagnoser/{referenced_uuid}")
    assert single.status_code == 409

    bulk = client.delete(
        "/api/v1/diagnosers",
        params={"space_uuid": "s", "agent_uuid": "a"},
    )
    assert bulk.status_code == 409
    assert client.get(f"/api/v1/diagnoser/{referenced_uuid}").status_code == 200
    assert client.get(f"/api/v1/diagnoser/{unreferenced_uuid}").status_code == 200


def test_shared_builtin_cannot_be_bound_directly_and_can_be_deleted(client):
    shared_diagnoser = client.post(
        "/api/v1/diagnoser",
        json={
            "space_uuid": "s-shard",
            "agent_uuid": "business-agent",
            "name": "重复调用线索",
            "type": "builtin",
            "mode": "rule",
            "spec": {},
        },
    )
    assert shared_diagnoser.status_code == 201, shared_diagnoser.text
    dataset = client.post(
        "/api/v1/dataset",
        json={
            "space_uuid": "business-space",
            "agent_uuid": "business-agent",
            "name": "shared-reference-dataset",
            "type": "signoz",
            "spec": {
                "query": {"table": "trace"},
                "runtime_type": "custom-runtime",
            },
        },
    )
    assert dataset.status_code == 201, dataset.text
    job = client.post(
        "/api/v1/job",
        json={
            "space_uuid": "business-space",
            "agent_uuid": "business-agent",
            "name": "shared-reference-job",
            "trigger_type": "manual",
            "status": "enabled",
            "dataset_uuid": dataset.json()["uuid"],
            "diagnoser_uuids": [shared_diagnoser.json()["uuid"]],
        },
    )
    assert job.status_code == 422, job.text

    response = client.delete(f"/api/v1/diagnoser/{shared_diagnoser.json()['uuid']}")

    assert response.status_code == 204


def test_create_duplicate_returns_409(client):
    payload = {
        "space_uuid": "s",
        "agent_uuid": "a",
        "name": "dup",
        "type": "custom",
        "mode": "rule",
        "spec": _custom_rule_spec(),
    }
    assert client.post("/api/v1/diagnoser", json=payload).status_code == 201
    r = client.post("/api/v1/diagnoser", json=payload)
    assert r.status_code == 409


def test_custom_diagnoser_detail_operations_use_uuid_without_scope_params(client):
    r = client.post(
        "/api/v1/diagnoser",
        json={
            "space_uuid": "s",
            "agent_uuid": "a",
            "name": "scoped-custom",
            "type": "custom",
            "mode": "rule",
            "spec": _custom_rule_spec(),
        },
    )
    uuid = r.json()["uuid"]

    ok = client.get(f"/api/v1/diagnoser/{uuid}")
    assert ok.status_code == 200

    updated = client.patch(f"/api/v1/diagnoser/{uuid}", json={"description": "updated"})
    assert updated.status_code == 200
    assert updated.json()["description"] == "updated"

    deleted = client.delete(f"/api/v1/diagnoser/{uuid}")
    assert deleted.status_code == 204


def test_custom_diagnoser_list_is_scoped_but_detail_uses_uuid(client):
    created = client.post(
        "/api/v1/diagnoser",
        json={
            "space_uuid": "s",
            "agent_uuid": "agent-a",
            "name": "agent-scoped",
            "type": "custom",
            "mode": "rule",
            "spec": _custom_rule_spec(),
        },
    )
    assert created.status_code == 201, created.text
    diagnoser = created.json()
    uuid = diagnoser["uuid"]
    assert diagnoser["agent_uuid"] == "agent-a"

    listed = client.get(
        "/api/v1/diagnosers",
        params={"space_uuid": "s", "agent_uuid": "agent-b"},
    )
    assert listed.status_code == 200
    assert listed.json()["items"] == []

    updated = client.patch(f"/api/v1/diagnoser/{uuid}", json={"description": "shared update"})
    assert updated.status_code == 200


def test_diagnoser_list_includes_selected_shared_spaces_in_order(client):
    def create(space_uuid: str, agent_uuid: str, name: str) -> None:
        response = client.post(
            "/api/v1/diagnoser",
            json={
                "space_uuid": space_uuid,
                "agent_uuid": agent_uuid,
                "name": name,
                "type": "custom",
                "mode": "rule",
                "spec": _custom_rule_spec(),
            },
        )
        assert response.status_code == 201, response.text

    create("s", "a", "same-name")
    create("s-user_shared", "a", "user")
    create("s-biz_shared", "a", "biz")
    create("s-shard", "a", "same-name")
    create("s-shard", "other-agent", "hidden")

    response = client.get(
        "/api/v1/diagnosers",
        params={
            "space_uuid": "s",
            "agent_uuid": "a",
            "include_user_shared": True,
            "include_biz_shared": True,
            "include_shared": True,
        },
    )

    assert response.status_code == 200, response.text
    assert [(item["space_uuid"], item["name"]) for item in response.json()["items"]] == [
        ("s", "same-name"),
        ("s-user_shared", "user"),
        ("s-biz_shared", "biz"),
        ("s-shard", "same-name"),
    ]


_SENTINEL_API_KEY = "sk-SENTINEL-DO-NOT-LEAK-THIS-VALUE-12345"


def _create_model_connection(client, *, space_uuid="s", name="judge"):
    response = client.post(
        "/api/v1/model-connection",
        json={
            "space_uuid": space_uuid,
            "name": name,
            "endpoint": "http://judge/v1",
            "model_name": "judge-model",
            "api_key": _SENTINEL_API_KEY,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_diagnoser_uses_space_model_connection_without_leaking_secret(client):
    connection = _create_model_connection(client)
    connection_uuid = connection["uuid"]

    created = client.post(
        "/api/v1/diagnoser",
        json={
            "space_uuid": "s",
            "agent_uuid": "a",
            "name": "llm-with-connection",
            "type": "custom",
            "mode": "llm",
            "spec": {
                "prompt": "判断",
                "metrics": [{"name": "quality"}],
                "model_connection_uuid": connection_uuid,
            },
        },
    )
    assert created.status_code == 201, created.text
    diagnoser_uuid = created.json()["uuid"]

    for response in (
        created,
        client.get(f"/api/v1/diagnoser/{diagnoser_uuid}"),
        client.get("/api/v1/diagnosers", params={"space_uuid": "s", "agent_uuid": "a"}),
    ):
        assert _SENTINEL_API_KEY not in response.text
    spec = client.get(f"/api/v1/diagnoser/{diagnoser_uuid}").json()["spec"]
    assert spec["model_connection_uuid"] == connection_uuid
    assert "model" not in spec

    updated = client.patch(
        f"/api/v1/diagnoser/{diagnoser_uuid}",
        json={
            "spec": {
                "prompt": "更新判断",
                "metrics": [{"name": "quality"}],
                "model_connection_uuid": connection_uuid,
            }
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["spec"]["model_connection_uuid"] == connection_uuid


def test_diagnoser_model_connection_must_belong_to_space(client):
    connection = _create_model_connection(client, space_uuid="other", name="other-judge")
    response = client.post(
        "/api/v1/diagnoser",
        json={
            "space_uuid": "s",
            "agent_uuid": "a",
            "name": "cross-space-model",
            "type": "custom",
            "mode": "llm",
            "spec": {
                "prompt": "判断",
                "metrics": [{"name": "quality"}],
                "model_connection_uuid": connection["uuid"],
            },
        },
    )
    assert response.status_code == 422
    assert "不属于当前 space" in response.json()["msg"]


def test_builtin_accepts_model_connection_reference(client):
    connection = _create_model_connection(client, space_uuid="s", name="builtin-judge")
    created = client.post(
        "/api/v1/diagnoser",
        json={
            "space_uuid": "s",
            "agent_uuid": "a",
            "name": "文本回应检查",
            "type": "builtin",
            "mode": "llm",
            "spec": {"enable_thinking": True, "temperature": 0.1},
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["spec"]["enable_thinking"] is True
    assert created.json()["spec"]["temperature"] == 0.1

    updated = client.patch(
        f"/api/v1/diagnoser/{created.json()['uuid']}",
        json={
            "spec": {
                "model_connection_uuid": connection["uuid"],
                "enable_thinking": False,
                "temperature": 0.2,
            }
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["spec"]["model_connection_uuid"] == connection["uuid"]
    assert updated.json()["spec"]["enable_thinking"] is False
    assert updated.json()["spec"]["temperature"] == 0.2


def test_managed_spec_signature_tracks_required_vars():
    from agentlens.web.routes.diagnoser import _MANAGED_SPEC_KEYS

    assert "required_vars" in _MANAGED_SPEC_KEYS
    assert "enable_thinking" in _MANAGED_SPEC_KEYS
    assert "temperature" in _MANAGED_SPEC_KEYS


def test_common_builtins_exclude_unimplemented_agency_stub():
    from agentlens.web.routes.diagnoser import _load_common_definitions

    metric_keys = {
        m.get("name") for d in _load_common_definitions() for m in (d["spec"].get("metrics") or [])
    }
    assert "excessive_agency_muting_rate" not in metric_keys


@pytest.mark.parametrize(
    "key,variant",
    [
        ("task_completion", "text_response"),
        ("task_completion", "executed_artifact"),
        ("report_fidelity", "observed_fact_comparison"),
        ("answer_grounding", "provided_context"),
    ],
)
def test_sync_canonical_core_uses_builtin_llm_mode(key, variant):
    from agentlens.diagnoser.definitions.catalog import get_core_definition
    from agentlens.web.routes.diagnoser import _sync_builtin
    from tests.unit.diagnoser.test_rule_identity_v3 import MemoryTable

    definition = get_core_definition(key, variant)
    diagnoser, action = _sync_builtin(MemoryTable(), "s", "a", definition)
    assert action == "created"
    assert diagnoser.type is DiagnoserType.BUILTIN
    assert diagnoser.mode is DiagnoserMode.LLM
    assert diagnoser.version == "1.0.0"
    assert diagnoser.spec["category"] == "outcome"
    assert diagnoser.spec["weight"] == 1
    assert diagnoser.spec["enable_thinking"] is None
    assert diagnoser.spec["builtin_key"] == key
    assert diagnoser.spec["canonical_variant"] == variant
    assert diagnoser.spec["score_role"] == "quality"


@pytest.mark.parametrize(
    "executor",
    [
        "legacy_personalization_subject_binding",
        "legacy_personalization_memory_consumption",
        "legacy_default",
        "legacy_abuse",
        "legacy_health_data_unrecall",
        "legacy_numeric_error",
        "legacy_web_search_missed",
        "legacy_instruction_leakage",
    ],
)
def test_sync_retired_executor_rejects_provisioning_without_writes(executor):
    from agentlens.web.routes.diagnoser import _sync_builtin
    from tests.unit.diagnoser.test_rule_identity_v3 import MemoryTable

    table = MemoryTable()
    definition = {
        "name": "historical",
        "description": "history only",
        "spec": {
            "executor": executor,
            "scenario": "legacy",
            "prompt": "old",
            "metrics": [{"name": "historical"}],
        },
    }
    with pytest.raises(Validation, match="retired"):
        _sync_builtin(table, "s", "a", definition)
    assert table.rows == []


@pytest.fixture
def default_builtins_client(app, client, monkeypatch):
    """开启首访自动补齐（conftest 默认关闭）。"""
    from agentlens.web import deps

    monkeypatch.setenv("AGENTLENS_DEFAULT_BUILTIN_SCENARIOS", "common,coding")
    deps.get_settings.cache_clear()
    yield client
    deps.get_settings.cache_clear()


def _list_names(client, space_uuid: str, agent_uuid: str) -> list[str]:
    response = client.get(
        "/api/v1/diagnosers",
        params={"space_uuid": space_uuid, "agent_uuid": agent_uuid},
    )
    assert response.status_code == 200, response.text
    return [item["name"] for item in response.json()["items"]]


def test_first_list_on_empty_scope_provisions_common_and_coding_builtins(
    default_builtins_client,
):
    from agentlens.web.routes.diagnoser import _load_common_definitions

    expected = sorted(item["name"] for item in _load_common_definitions())
    names = _list_names(default_builtins_client, "s-auto", "agent-a")

    assert sorted(names) == expected
    assert len(expected) == 8
    detail = default_builtins_client.get(
        "/api/v1/diagnosers",
        params={"space_uuid": "s-auto", "agent_uuid": "agent-a", "type": "builtin"},
    ).json()
    assert all(item["type"] == "builtin" and item["mode"] == "rule" for item in detail["items"])
    # 幂等：再次列出不会重复创建
    assert sorted(_list_names(default_builtins_client, "s-auto", "agent-a")) == expected
    # 其他 agent / space 互不影响
    assert sorted(_list_names(default_builtins_client, "s-auto", "agent-b")) == expected
    assert _list_names(default_builtins_client, "s-other", "agent-a")


def test_default_builtins_skip_non_empty_scope_and_shared_scopes(
    default_builtins_client, monkeypatch
):
    from agentlens.web import deps

    # A previous deployment could have created a custom rule with provisioning disabled.
    with monkeypatch.context() as legacy:
        legacy.setenv("AGENTLENS_DEFAULT_BUILTIN_SCENARIOS", "")
        deps.get_settings.cache_clear()
        created = default_builtins_client.post(
            "/api/v1/diagnoser",
            json={
                "space_uuid": "s-has-custom",
                "agent_uuid": "agent-a",
                "name": "mine",
                "type": "custom",
                "mode": "rule",
                "spec": _custom_rule_spec(),
            },
        )
    deps.get_settings.cache_clear()
    assert created.status_code == 201, created.text

    # scope 已有诊断器（哪怕只是自定义）就不自动补齐
    assert _list_names(default_builtins_client, "s-has-custom", "agent-a") == ["mine"]
    # 共享源 space 与占位 agent 不自动补齐
    assert _list_names(default_builtins_client, "s-user_shared", "agent-a") == []
    assert _list_names(default_builtins_client, "s-auto", "default") == []


def test_default_builtins_respect_deletion(default_builtins_client):
    names = _list_names(default_builtins_client, "s-del", "agent-a")
    assert len(names) == 8
    response = default_builtins_client.delete(
        "/api/v1/diagnosers",
        params={"space_uuid": "s-del", "agent_uuid": "agent-a"},
    )
    assert response.status_code == 200, response.text
    # 用户清空后，下一次列出会重新按“空 scope”补齐；只删部分则保持用户选择
    assert len(_list_names(default_builtins_client, "s-del", "agent-a")) == 8
    listed = default_builtins_client.get(
        "/api/v1/diagnosers",
        params={"space_uuid": "s-del", "agent_uuid": "agent-a"},
    ).json()["items"]
    victim = listed[0]["uuid"]
    assert default_builtins_client.delete(f"/api/v1/diagnoser/{victim}").status_code == 204
    assert len(_list_names(default_builtins_client, "s-del", "agent-a")) == 7


def test_default_builtins_disabled_by_empty_scenarios(client):
    assert _list_names(client, "s-off", "agent-a") == []


def test_default_builtin_scenarios_setting_parses_csv(app, monkeypatch):
    from agentlens.web import deps

    monkeypatch.setenv("AGENTLENS_DEFAULT_BUILTIN_SCENARIOS", " common, coding ,common,, ")
    deps.get_settings.cache_clear()
    try:
        assert deps.get_settings().default_builtin_scenario_list == ("common", "coding")
    finally:
        deps.get_settings.cache_clear()


@pytest.mark.parametrize("value", [0, 1, 1.0, "false", [], {}])
def test_llm_spec_rejects_non_boolean_enable_thinking(value):
    with pytest.raises(Validation, match="enable_thinking"):
        _validate_executor_spec(
            {
                "executor": "llm_builtin",
                "enable_thinking": value,
                "prompt": "p",
                "metrics": [{"name": "quality"}],
            },
            DiagnoserType.BUILTIN,
            DiagnoserMode.LLM,
        )


def test_llm_spec_defaults_and_preserves_enable_thinking():
    base = {
        "executor": "llm_builtin",
        "prompt": "p",
        "metrics": [{"name": "quality"}],
    }

    assert (
        _validate_executor_spec(base, DiagnoserType.BUILTIN, DiagnoserMode.LLM)["enable_thinking"]
        is False
    )
    assert (
        _validate_executor_spec(
            {**base, "enable_thinking": True},
            DiagnoserType.BUILTIN,
            DiagnoserMode.LLM,
        )["enable_thinking"]
        is True
    )
    assert (
        _validate_executor_spec(
            {**base, "enable_thinking": None}, DiagnoserType.BUILTIN, DiagnoserMode.LLM
        )["enable_thinking"]
        is None
    )


def test_llm_spec_defaults_and_validates_temperature():
    base = {
        "executor": "llm_builtin",
        "prompt": "p",
        "metrics": [{"name": "quality"}],
    }

    assert (
        _validate_executor_spec(base, DiagnoserType.BUILTIN, DiagnoserMode.LLM)["temperature"] == 0
    )
    assert (
        _validate_executor_spec(
            {**base, "temperature": 0.1},
            DiagnoserType.BUILTIN,
            DiagnoserMode.LLM,
        )["temperature"]
        == 0.1
    )


@pytest.mark.parametrize("value", [None, True, "0.1", -0.1, 2.1])
def test_llm_spec_rejects_invalid_temperature(value):
    with pytest.raises(Validation, match="temperature"):
        _validate_executor_spec(
            {
                "executor": "llm_builtin",
                "temperature": value,
                "prompt": "p",
                "metrics": [{"name": "quality"}],
            },
            DiagnoserType.BUILTIN,
            DiagnoserMode.LLM,
        )


def test_rule_spec_rejects_enable_thinking():
    with pytest.raises(Validation, match="仅适用于 LLM"):
        _validate_executor_spec(
            {
                "executor": "total_latency",
                "enable_thinking": False,
                "metrics": [{"name": "latency_decomposition"}],
            },
            DiagnoserType.BUILTIN,
            DiagnoserMode.RULE,
        )


def test_rule_spec_rejects_temperature():
    with pytest.raises(Validation, match="temperature 仅适用于 LLM"):
        _validate_executor_spec(
            {
                "executor": "total_latency",
                "temperature": 0,
                "metrics": [{"name": "latency_decomposition"}],
            },
            DiagnoserType.BUILTIN,
            DiagnoserMode.RULE,
        )


def test_unknown_metric_fields_are_ignored_and_threshold_route_is_removed(client):
    created = client.post(
        "/api/v1/diagnoser",
        json={
            "space_uuid": "s",
            "agent_uuid": "a",
            "name": "修改后验证",
            "type": "builtin",
            "mode": "rule",
            "spec": {
                "executor": "test_execution",
                "metrics": [
                    {
                        "name": "test_execution_rate",
                        "desc": "修改后验证",
                        "future_extension": {"value": "future"},
                    }
                ],
            },
        },
    )
    assert created.status_code == 201, created.text
    uuid = created.json()["uuid"]
    assert created.json()["spec"]["metrics"] == [
        {"name": "test_execution_rate", "desc": "修改后验证"}
    ]

    updated = client.patch(
        f"/api/v1/diagnoser/{uuid}/thresholds",
        json={
            "thresholds": {"test_execution_rate": 1.0},
        },
    )
    assert updated.status_code == 404


def test_custom_diagnoser_threshold_route_is_removed(client):
    created = client.post(
        "/api/v1/diagnoser",
        json={
            "space_uuid": "s",
            "agent_uuid": "a",
            "name": "score-rule",
            "type": "custom",
            "mode": "llm",
            "spec": {
                "runtime_type": "codex",
                "prompt": "{{agent_output}}",
                "metrics": [
                    {
                        "name": "quality",
                    }
                ],
            },
        },
    )
    uuid = created.json()["uuid"]
    response = client.patch(
        f"/api/v1/diagnoser/{uuid}/thresholds",
        json={
            "thresholds": {"quality": 1.1},
        },
    )
    assert response.status_code == 404


import pytest  # noqa: E402

from agentlens.domain.enums import DiagnoserMode, DiagnoserType  # noqa: E402
from agentlens.domain.errors import Validation  # noqa: E402
from agentlens.web.routes.diagnoser import (  # noqa: E402
    _validate_executor_spec,
)


def test_validator_requires_non_empty_metrics_array():
    with pytest.raises(Validation, match="必须声明 metrics 数组"):
        _validate_executor_spec(
            {"executor": "llm_builtin"}, DiagnoserType.BUILTIN, DiagnoserMode.LLM
        )
    with pytest.raises(Validation, match="必须声明 metrics 数组"):
        _validate_executor_spec(
            {"executor": "llm_builtin", "metrics": "nope"},
            DiagnoserType.BUILTIN,
            DiagnoserMode.LLM,
        )
    with pytest.raises(Validation, match="非空 name"):
        _validate_executor_spec(
            {"executor": "llm_builtin", "metrics": [{"desc": "missing name"}]},
            DiagnoserType.BUILTIN,
            DiagnoserMode.LLM,
        )
    with pytest.raises(Validation, match="非空 name"):
        _validate_executor_spec(
            {"executor": "llm_builtin", "metrics": ["bare_string"]},
            DiagnoserType.BUILTIN,
            DiagnoserMode.LLM,
        )


def test_validator_requires_metrics_for_all_builtin_executors():
    with pytest.raises(Validation, match="必须声明 metrics 数组"):
        _validate_executor_spec(
            {"executor": "llm_builtin"}, DiagnoserType.BUILTIN, DiagnoserMode.LLM
        )
    with pytest.raises(Validation, match="必须声明 metrics 数组"):
        _validate_executor_spec(
            {"executor": "total_latency"},
            DiagnoserType.BUILTIN,
            DiagnoserMode.RULE,
        )


def test_validator_rejects_duplicate_metrics():
    with pytest.raises(Validation, match="指标名称不能重复"):
        _validate_executor_spec(
            {
                "executor": "llm_builtin",
                "metrics": [{"name": "quality"}, {"name": "quality"}],
            },
            DiagnoserType.BUILTIN,
            DiagnoserMode.LLM,
        )


def test_validator_accepts_single_metric_arrays():
    array = _validate_executor_spec(
        {"executor": "llm_builtin", "prompt": "p", "metrics": [{"name": "m"}]},
        DiagnoserType.BUILTIN,
        DiagnoserMode.LLM,
    )
    assert array["executor"] == "llm_builtin"
    builtin_rule = _validate_executor_spec(
        {
            "executor": "total_latency",
            "metrics": [{"name": "latency_decomposition"}],
        },
        DiagnoserType.BUILTIN,
        DiagnoserMode.RULE,
    )
    assert builtin_rule["executor"] == "total_latency"


def test_validator_accepts_registered_simple_image_vars_only(monkeypatch):
    from agentlens.service.datasource.projection import registry
    from tests.unit.diagnoser.test_projection_registry import FirstProjector

    class ImageProjector(FirstProjector):
        canonical_runtime = "image-fixture"
        available_image_vars = {"attachments": "controlled image roots"}

    definition = registry.ProjectorDefinition(
        name="image-fixture",
        projector_type=ImageProjector,
        canonical_runtime="image-fixture",
        runtime_aliases=frozenset(),
        builtin_scenarios=frozenset({"common"}),
    )
    _, routed, _ = registry._build_registry((definition,))
    monkeypatch.setattr(registry, "_ROUTED_PROJECTORS", {**registry._ROUTED_PROJECTORS, **routed})
    base = {"executor": "llm_builtin", "prompt": "p", "metrics": [{"name": "m"}]}
    spec = _validate_executor_spec(
        {**base, "image_vars": ["attachments"]},
        DiagnoserType.BUILTIN,
        DiagnoserMode.LLM,
        runtime_type="image-fixture",
    )
    assert spec["image_vars"] == ["attachments"]
    for image_vars in (
        "attachments",
        ["attachments", "attachments"],
        ["attachments[0]"],
        ["unknown_images"],
    ):
        with pytest.raises(Validation):
            _validate_executor_spec(
                {**base, "image_vars": image_vars},
                DiagnoserType.BUILTIN,
                DiagnoserMode.LLM,
                runtime_type="image-fixture",
            )
    for runtime in ("other", "legacy", "unknown-custom-agent"):
        with pytest.raises(Validation, match="不支持图片变量"):
            _validate_executor_spec(
                {**base, "image_vars": ["attachments"]},
                DiagnoserType.BUILTIN,
                DiagnoserMode.LLM,
                runtime_type=runtime,
            )


def test_validator_allows_multiple_metrics_for_one_shot_llm():
    spec = _validate_executor_spec(
        {
            "executor": "llm_builtin",
            "prompt": "p",
            "metrics": [
                {"name": "quality"},
                {"name": "safety"},
            ],
        },
        DiagnoserType.BUILTIN,
        DiagnoserMode.LLM,
    )

    assert [metric["name"] for metric in spec["metrics"]] == ["quality", "safety"]


def test_validator_allows_metric_name_to_match_diagnoser_name():
    spec = _validate_executor_spec(
        {
            "executor": "llm_builtin",
            "prompt": "p",
            "metrics": [
                {"name": "quality-summary"},
                {"name": "safety"},
            ],
        },
        DiagnoserType.BUILTIN,
        DiagnoserMode.LLM,
    )

    assert [metric["name"] for metric in spec["metrics"]] == ["quality-summary", "safety"]


def test_validator_requires_custom_declared_metric():
    spec = _validate_executor_spec(
        {
            "executor": "client_selected_executor",
            "prompt": "p",
            "metrics": [{"name": "quality"}],
        },
        DiagnoserType.CUSTOM,
        DiagnoserMode.LLM,
    )
    assert "executor" not in spec


def test_custom_create_does_not_persist_client_executor(client):
    created = client.post(
        "/api/v1/diagnoser",
        json={
            "space_uuid": "s",
            "agent_uuid": "a",
            "name": "custom-system-routed",
            "type": "custom",
            "mode": "llm",
            "spec": {
                "executor": "client_selected_executor",
                "prompt": "判断",
                "metrics": [{"name": "quality"}],
            },
        },
    )

    assert created.status_code == 201, created.text
    assert "executor" not in created.json()["spec"]
    assert created.json()["spec"]["metrics"][0]["name"] == "quality"
    assert created.json()["spec"]["enable_thinking"] is False
    assert created.json()["spec"]["temperature"] == 0

    updated = client.patch(
        f"/api/v1/diagnoser/{created.json()['uuid']}",
        json={
            "spec": {
                **created.json()["spec"],
                "metrics": [{"name": "answer_quality", "desc": "回答质量"}],
            }
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["spec"]["metrics"] == [{"name": "answer_quality", "desc": "回答质量"}]


def test_builtin_update_only_applies_allowed_runtime_fields_from_spec(client):
    connection = _create_model_connection(client, name="builtin-update-judge")
    created = client.post(
        "/api/v1/diagnoser",
        json={
            "space_uuid": "s",
            "agent_uuid": "a",
            "name": "文本回应检查",
            "type": "builtin",
            "mode": "llm",
            "spec": {},
        },
    )
    assert created.status_code == 201, created.text
    original_description = created.json()["description"]
    original_version = created.json()["version"]
    original = created.json()["spec"]

    updated = client.patch(
        f"/api/v1/diagnoser/{created.json()['uuid']}",
        json={
            "description": "client description",
            "version": "client-version",
            "spec": {
                "executor": "client-override",
                "prompt": "client override",
                "metrics": [
                    {
                        "name": "client_metric",
                    }
                ],
                "model_connection_uuid": connection["uuid"],
                "enable_thinking": True,
                "temperature": 0.3,
            },
        },
    )

    assert updated.status_code == 200, updated.text
    assert updated.json()["description"] == original_description
    assert updated.json()["version"] == original_version
    spec = updated.json()["spec"]
    assert spec["executor"] == original["executor"]
    assert spec["prompt"] == original["prompt"]
    assert spec["metrics"] == original["metrics"]
    assert spec["model_connection_uuid"] == connection["uuid"]
    assert spec["enable_thinking"] is True
    assert spec["temperature"] == 0.3
    assert "model" not in spec


def test_builtin_definitions_all_pass_the_public_validator():
    from agentlens.diagnoser.definitions.catalog import load_builtin_definitions

    definitions = load_builtin_definitions()
    rules = [d for d in definitions if d["mode"] == "rule"]
    cores = [d for d in definitions if d["mode"] == "llm"]
    assert len(rules) == 8
    assert {(d["key"], d["variant"]) for d in cores} == {
        ("task_completion", "text_response"),
        ("task_completion", "executed_artifact"),
        ("report_fidelity", "observed_fact_comparison"),
        ("answer_grounding", "provided_context"),
    }
    for definition in definitions:
        spec = definition["spec"]
        validated = _validate_executor_spec(
            spec, DiagnoserType.BUILTIN, DiagnoserMode(definition["mode"])
        )
        if definition["mode"] == "llm":
            assert validated["enable_thinking"] is None
            assert validated["temperature"] == 0
        else:
            assert "metric_key" not in spec
            assert "label" not in spec
            assert "threshold" not in spec


@pytest.mark.parametrize("collision", ["duplicate_identity", "custom_name"])
def test_canonical_core_sync_rejects_identity_or_name_collision(collision):
    from dataclasses import replace

    from agentlens.diagnoser.definitions.catalog import get_core_definition
    from agentlens.web.routes.diagnoser import _sync_builtin
    from tests.unit.diagnoser.test_rule_identity_v3 import MemoryTable

    definition = get_core_definition("task_completion", "text_response")
    table = MemoryTable()
    original, _ = _sync_builtin(table, "s", "a", definition)
    if collision == "duplicate_identity":
        table.rows.append(replace(original, uuid="duplicate", name="different name"))
    else:
        table.rows[0] = replace(original, type=DiagnoserType.CUSTOM)
    before = [(d.uuid, d.type, d.name) for d in table.rows]
    with pytest.raises(Validation, match="duplicate|collision"):
        _sync_builtin(table, "s", "a", definition)
    assert [(d.uuid, d.type, d.name) for d in table.rows] == before


def test_retired_sync_api_removed_and_active_sync_keeps_common_coding(client):
    retired = client.post(
        "/api/v1/diagnosers/sync-legacy-builtins", json={"space_uuid": "s", "agent_uuid": "a"}
    )
    assert retired.status_code == 404, retired.text
    response = client.post(
        "/api/v1/diagnosers/sync-common-builtins", json={"space_uuid": "s", "agent_uuid": "a"}
    )
    assert response.status_code == 200, response.text
    rows = response.json()["diagnosers"]
    assert {d["spec"]["executor"] for d in rows} == {
        "tool_success",
        "stuck_loop",
        "sensitive_leakage",
        "high_risk_command",
        "total_latency",
        "user_interruption",
        "test_execution",
        "rewrite_overlap",
    }
    assert len(rows) == 8
    assert all(
        d["type"] == "builtin" and d["mode"] == "rule" and d["version"] == "3.0.0" for d in rows
    )
    assert sum(d["spec"]["score_role"] == "quality" for d in rows) == 1
    second = client.post(
        "/api/v1/diagnosers/sync-common-builtins", json={"space_uuid": "s", "agent_uuid": "a"}
    )
    assert second.status_code == 200, second.text
    assert {d["uuid"] for d in second.json()["diagnosers"]} == {d["uuid"] for d in rows}


@pytest.mark.parametrize("name", ["文本回应检查", "实际交付检查", "执行声明核对", "资料支持检查"])
def test_core_resync_preserves_model_connection_uuid_and_temperature(client, name):
    space_uuid = "core-resync-space"
    agent_uuid = "core-resync-agent"
    initial_sync = client.post(
        "/api/v1/diagnosers/sync-core-builtins",
        json={"space_uuid": space_uuid, "agent_uuid": agent_uuid},
    )
    assert initial_sync.status_code == 200, initial_sync.text
    diagnoser = next(row for row in initial_sync.json()["diagnosers"] if row["name"] == name)
    connection = _create_model_connection(
        client,
        space_uuid=space_uuid,
        name="core-resync-judge",
    )
    patched = client.patch(
        f"/api/v1/diagnoser/{diagnoser['uuid']}",
        json={"spec": {"model_connection_uuid": connection["uuid"], "temperature": 0.4}},
    )
    assert patched.status_code == 200, patched.text

    resynced = client.post(
        "/api/v1/diagnosers/sync-core-builtins",
        json={"space_uuid": space_uuid, "agent_uuid": agent_uuid},
    )

    assert resynced.status_code == 200, resynced.text
    updated = next(row for row in resynced.json()["diagnosers"] if row["name"] == name)
    assert updated["uuid"] == diagnoser["uuid"]
    assert updated["spec"]["model_connection_uuid"] == connection["uuid"]
    assert updated["spec"]["temperature"] == 0.4
