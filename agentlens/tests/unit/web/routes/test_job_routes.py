from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


def _custom_rule_spec(metric_name: str = "quality") -> dict:
    return {
        "code": (
            "def evaluate(trace):\n"
            f"    return {{'metrics': [{{'name': '{metric_name}', "
            "'verdict': 'good', 'reason': 'ok', 'evidence': {}}]}\n"
        ),
        "metrics": [{"name": metric_name}],
    }


def _make_scoped_bindings(client, space_uuid="s", agent_uuid="a", name_suffix=""):
    """Create a real Dataset and Diagnoser in (space_uuid, agent_uuid) scope
    so Job binding validation accepts them."""
    ds = client.post(
        "/api/v1/dataset",
        json={
            "space_uuid": space_uuid,
            "agent_uuid": agent_uuid,
            "name": f"ds-for-job{name_suffix}",
            "type": "signoz",
            "spec": {
                "query": {"table": "sample_trace"},
                "runtime_type": "custom-runtime",
            },
        },
    ).json()
    dg = client.post(
        "/api/v1/diagnoser",
        json={
            "space_uuid": space_uuid,
            "agent_uuid": agent_uuid,
            "name": f"dg-for-job{name_suffix}",
            "type": "custom",
            "mode": "rule",
            "spec": _custom_rule_spec(),
            "version": "v1",
        },
    ).json()
    return ds["uuid"], dg["uuid"]


def _make_model_connection(client, *, name: str) -> str:
    response = client.post(
        "/api/v1/model-connection",
        json={
            "space_uuid": "s",
            "name": name,
            "endpoint": "http://judge/v1",
            "model_name": "judge-model",
            "api_key": "test-key",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["uuid"]


def _make_llm_diagnoser(client, *, name: str, model_connection_uuid: str | None = None):
    response = client.post(
        "/api/v1/diagnoser",
        json={
            "space_uuid": "s",
            "agent_uuid": "a",
            "name": name,
            "type": "custom",
            "mode": "llm",
            "spec": {
                "prompt": "Judge the trace",
                "metrics": [{"name": "quality"}],
                **(
                    {"model_connection_uuid": model_connection_uuid}
                    if model_connection_uuid
                    else {}
                ),
            },
            "version": "v1",
        },
    )
    assert response.status_code == 201
    return response.json()["uuid"]


def _make_job_payload(client, name="j1", space_uuid="s", agent_uuid="a"):
    dataset_uuid, diagnoser_uuid = _make_scoped_bindings(
        client, space_uuid, agent_uuid, name_suffix=name
    )
    return {
        "space_uuid": space_uuid,
        "agent_uuid": agent_uuid,
        "name": name,
        "trigger_type": "schedule",
        "schedule_cron": "0 * * * *",
        "status": "enabled",
        "dataset_uuid": dataset_uuid,
        "diagnoser_uuids": [diagnoser_uuid],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["disabled", "deleted"])
async def test_manual_trigger_rechecks_job_after_lock(monkeypatch, change):
    from types import SimpleNamespace

    from agentlens.domain.enums import JobStatus
    from agentlens.domain.errors import NotFound, Validation
    from agentlens.web.routes import job as job_routes

    original = SimpleNamespace(
        uuid="j",
        spec={},
        status=JobStatus.ENABLED,
        space_uuid="s",
        agent_uuid="a",
        dataset_uuid="ds",
        diagnoser_uuids=["dg"],
    )
    locked = (
        SimpleNamespace(**{**vars(original), "status": JobStatus.DISABLED})
        if change == "disabled"
        else None
    )
    jobs = SimpleNamespace(get_by_uuid=lambda _: original, get_by_uuid_for_update=lambda _: locked)
    tasks = SimpleNamespace(
        s=SimpleNamespace(execute=lambda *a: None), find_active_by_job=lambda _: None
    )
    monkeypatch.setattr(job_routes, "_validate_job_bindings", lambda *a: None)
    monkeypatch.setattr(
        job_routes,
        "snapshot_from_tables",
        lambda *a: pytest.fail("ineligible Job cannot create a Task"),
    )
    with pytest.raises(Validation if change == "disabled" else NotFound):
        await job_routes.trigger_job("j", jobs, tasks, None, None, None)


@pytest.mark.parametrize("change", ["disabled", "deleted"])
def test_manual_trigger_changed_by_another_transaction_does_not_insert(
    client, session, engine, monkeypatch, change
):
    from agentlens.infra.ob.engine import make_session_factory
    from agentlens.infra.ob.tables.job_table import JobTable
    from agentlens.infra.ob.tables.task_table import TaskTable

    response = client.post("/api/v1/job", json=_make_job_payload(client))
    assert response.status_code == 201
    uuid = response.json()["uuid"]
    original = JobTable.get_by_uuid

    def read_then_change(table, requested_uuid):
        value = original(table, requested_uuid)
        if requested_uuid == uuid:
            with make_session_factory(engine)() as writer:
                jobs = JobTable(writer)
                if change == "deleted":
                    jobs.delete_by_uuid(uuid)
                else:
                    jobs.update_by_uuid(uuid, {"status": "disabled"})
        return value

    monkeypatch.setattr(JobTable, "get_by_uuid", read_then_change)
    response = client.post(f"/api/v1/job/{uuid}/trigger")
    assert response.status_code == (422 if change == "disabled" else 404)
    assert TaskTable(session).find_active_by_job(uuid) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [6235, 1064])
async def test_manual_trigger_serialization_conflict_is_safe_and_narrow(monkeypatch, code):
    from types import SimpleNamespace

    from sqlalchemy.exc import OperationalError

    from agentlens.domain.enums import JobStatus
    from agentlens.domain.errors import Conflict
    from agentlens.web.routes import job as job_routes

    original = SimpleNamespace(
        uuid="j",
        spec={},
        status=JobStatus.ENABLED,
        space_uuid="s",
        agent_uuid="a",
        dataset_uuid="ds",
        diagnoser_uuids=["dg"],
    )
    error = OperationalError("private-sql", {}, Exception(code, "private-db-detail"))
    rollbacks = []

    def lock(*args):
        raise error

    jobs = SimpleNamespace(get_by_uuid=lambda _: original)
    tasks = SimpleNamespace(
        s=SimpleNamespace(execute=lock, rollback=lambda: rollbacks.append(True))
    )
    monkeypatch.setattr(job_routes, "_validate_job_bindings", lambda *args: None)
    with pytest.raises(Conflict if code == 6235 else OperationalError) as caught:
        await job_routes.trigger_job("j", jobs, tasks, None, None, None)
    assert rollbacks == [True]
    if code == 6235:
        assert str(caught.value) == "configuration changed while creating task; retry"
        assert "private" not in str(caught.value)
    else:
        assert caught.value is error


def test_job_crud_and_trigger(client):
    r = client.post("/api/v1/job", json=_make_job_payload(client))
    assert r.status_code == 201
    job_uuid = r.json()["uuid"]
    assert job_uuid.startswith("j-")
    assert r.json()["spec"] == {"save_badcase_only": True}
    assert "config" not in r.json()

    assert (
        client.get(f"/api/v1/job/{job_uuid}", params={"space_uuid": "s", "agent_uuid": "a"}).json()[
            "status"
        ]
        == "enabled"
    )

    r = client.patch(
        f"/api/v1/job/{job_uuid}",
        json={"name": "j1-renamed", "status": "disabled"},
        params={"space_uuid": "s", "agent_uuid": "a"},
    )
    assert r.json()["name"] == "j1-renamed"

    # disabled job 禁止手动触发
    r = client.post(
        f"/api/v1/job/{job_uuid}/trigger", params={"space_uuid": "s", "agent_uuid": "a"}
    )
    assert r.status_code == 422


@pytest.mark.parametrize(
    "schedule_cron",
    [None, "not a cron", "0 0 1 * *"],
)
def test_create_schedule_job_rejects_missing_invalid_or_long_cron(client, schedule_cron):
    payload = _make_job_payload(client, name=f"invalid-cron-{str(schedule_cron)}")
    payload["schedule_cron"] = schedule_cron

    response = client.post("/api/v1/job", json=payload)

    assert response.status_code == 422


def test_patch_validates_merged_schedule_state(client, session):
    created = client.post(
        "/api/v1/job",
        json=_make_job_payload(client, name="historical-long-cron"),
    )
    job_uuid = created.json()["uuid"]
    from agentlens.infra.ob.tables.job_table import JobTable

    JobTable(session).update_by_uuid(job_uuid, {"schedule_cron": "0 0 1 * *"})

    response = client.patch(f"/api/v1/job/{job_uuid}", json={"description": "edited"})

    assert response.status_code == 422


def test_patch_manual_job_to_schedule_requires_cron(client):
    payload = _make_job_payload(client, name="manual-to-schedule")
    payload.update(trigger_type="manual", schedule_cron=None)
    created = client.post("/api/v1/job", json=payload)

    response = client.patch(
        f"/api/v1/job/{created.json()['uuid']}",
        json={"trigger_type": "schedule"},
    )

    assert response.status_code == 422


def test_job_spec_preserves_unknown_fields_and_patch_shallow_merges(client):
    payload = {
        **_make_job_payload(client, name="spec-merge"),
        "spec": {
            "save_badcase_only": False,
            "future_option": {"mode": "audit", "level": 1},
        },
    }
    created = client.post("/api/v1/job", json=payload)
    assert created.status_code == 201, created.text
    job_uuid = created.json()["uuid"]

    updated = client.patch(
        f"/api/v1/job/{job_uuid}",
        json={"spec": {"future_option": {"mode": "compact"}}},
    )

    assert updated.status_code == 200, updated.text
    assert updated.json()["spec"] == {
        "save_badcase_only": False,
        "future_option": {"mode": "compact"},
    }
    listed = client.get(
        "/api/v1/jobs",
        params={"space_uuid": "s", "agent_uuid": "a"},
    )
    assert listed.json()["items"][0]["spec"] == updated.json()["spec"]


def test_job_spec_empty_patch_keeps_existing_spec(client):
    payload = {
        **_make_job_payload(client, name="spec-empty-patch"),
        "spec": {"save_badcase_only": False, "future_option": "keep"},
    }
    created = client.post("/api/v1/job", json=payload)
    job_uuid = created.json()["uuid"]

    updated = client.patch(f"/api/v1/job/{job_uuid}", json={"spec": {}})

    assert updated.status_code == 200
    assert updated.json()["spec"] == payload["spec"]


def test_job_legacy_config_field_is_ignored(client):
    payload = {
        **_make_job_payload(client, name="legacy-config-ignored"),
        "config": {"save_badcase_only": False},
    }

    created = client.post("/api/v1/job", json=payload)

    assert created.status_code == 201
    assert created.json()["spec"] == {"save_badcase_only": True}
    assert "config" not in created.json()


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("post", "/api/v1/job", {"spec": None}),
        ("patch", "/api/v1/job/{uuid}", {"spec": None}),
        (
            "post",
            "/api/v1/job",
            {"spec": {"save_badcase_only": "true"}},
        ),
        (
            "patch",
            "/api/v1/job/{uuid}",
            {"spec": {"save_badcase_only": 1}},
        ),
    ],
)
def test_job_spec_rejects_null_and_non_bool_known_value(client, method, path, payload):
    base = _make_job_payload(client, name=f"spec-invalid-{method}-{len(str(payload))}")
    if method == "post":
        response = client.post(path, json={**base, **payload})
    else:
        created = client.post("/api/v1/job", json=base)
        response = client.patch(path.format(uuid=created.json()["uuid"]), json=payload)

    assert response.status_code == 422


def test_job_trigger_unknown_404(client):
    assert (
        client.post(
            "/api/v1/job/j-deadbeef/trigger", params={"space_uuid": "s", "agent_uuid": "a"}
        ).status_code
        == 404
    )


def test_unprojected_runtime_allows_job_binding_without_prechecking_projection(client):
    dataset = client.post(
        "/api/v1/dataset",
        json={
            "space_uuid": "s",
            "agent_uuid": "a",
            "name": "raw-runtime-dataset",
            "type": "signoz",
            "spec": {
                "query": {"table": "raw_trace"},
                "runtime_type": "antigravity",
            },
        },
    ).json()
    _, builtin_uuid = _make_scoped_bindings(client, name_suffix="-raw-runtime")

    builtin_job = client.post(
        "/api/v1/job",
        json={
            "space_uuid": "s",
            "agent_uuid": "a",
            "name": "raw-runtime-builtin",
            "trigger_type": "manual",
            "status": "enabled",
            "dataset_uuid": dataset["uuid"],
            "diagnoser_uuids": [builtin_uuid],
        },
    )

    assert builtin_job.status_code == 201, builtin_job.text

    custom_rule = client.post(
        "/api/v1/diagnoser",
        json={
            "space_uuid": "s",
            "agent_uuid": "a",
            "name": "raw-custom-rule",
            "type": "custom",
            "mode": "rule",
            "spec": {
                "code": (
                    "def evaluate(trace):\n"
                    '    return {"metrics": [{"name": "raw_quality", '
                    '"verdict": "good", "reason": "ok", "evidence": {}}]}\n'
                ),
                "metrics": [
                    {
                        "name": "raw_quality",
                        "desc": "Raw",
                    }
                ],
            },
            "version": "v1",
        },
    )
    assert custom_rule.status_code == 201, custom_rule.text

    created = client.post(
        "/api/v1/job",
        json={
            "space_uuid": "s",
            "agent_uuid": "a",
            "name": "raw-runtime-custom-rule",
            "trigger_type": "manual",
            "status": "enabled",
            "dataset_uuid": dataset["uuid"],
            "diagnoser_uuids": [custom_rule.json()["uuid"]],
        },
    )
    assert created.status_code == 201, created.text

    triggered = client.post(
        f"/api/v1/job/{created.json()['uuid']}/trigger",
        params={"space_uuid": "s", "agent_uuid": "a"},
    )
    assert triggered.status_code == 202, triggered.text


def test_job_create_rejects_builtin_outside_projection_family(client):
    dataset = client.post(
        "/api/v1/dataset",
        json={
            "space_uuid": "s",
            "agent_uuid": "a",
            "name": "framework-dataset",
            "type": "signoz",
            "spec": {
                "query": {"table": "framework_trace"},
                "runtime_type": "langchain",
            },
        },
    ).json()
    builtin = client.post(
        "/api/v1/diagnoser",
        json={
            "space_uuid": "s",
            "agent_uuid": "a",
            "name": "修改后验证",
            "type": "builtin",
            "mode": "rule",
            "spec": {},
            "version": "3.0.0",
        },
    ).json()

    rejected = client.post(
        "/api/v1/job",
        json={
            "space_uuid": "s",
            "agent_uuid": "a",
            "name": "invalid-builtin-job",
            "trigger_type": "manual",
            "status": "enabled",
            "dataset_uuid": dataset["uuid"],
            "diagnoser_uuids": [builtin["uuid"]],
        },
    )

    assert rejected.status_code == 422
    assert "不适用内置诊断器" in rejected.json()["msg"]


def test_job_create_allows_llm_diagnoser_without_model_connection(client):
    dataset_uuid, _ = _make_scoped_bindings(client, name_suffix="-llm-missing-model")
    diagnoser_uuid = _make_llm_diagnoser(
        client,
        name="llm-missing-model",
    )

    response = client.post(
        "/api/v1/job",
        json={
            "space_uuid": "s",
            "agent_uuid": "a",
            "name": "job-missing-model",
            "trigger_type": "manual",
            "status": "enabled",
            "dataset_uuid": dataset_uuid,
            "diagnoser_uuids": [diagnoser_uuid],
        },
    )

    assert response.status_code == 201, response.text


def test_job_create_accepts_llm_diagnoser_with_configured_model(client):
    dataset_uuid, _ = _make_scoped_bindings(client, name_suffix="-llm-model-ready")
    model_connection_uuid = _make_model_connection(client, name="job-model-ready")
    diagnoser_uuid = _make_llm_diagnoser(
        client,
        name="llm-model-ready",
        model_connection_uuid=model_connection_uuid,
    )

    response = client.post(
        "/api/v1/job",
        json={
            "space_uuid": "s",
            "agent_uuid": "a",
            "name": "job-model-ready",
            "trigger_type": "manual",
            "status": "enabled",
            "dataset_uuid": dataset_uuid,
            "diagnoser_uuids": [diagnoser_uuid],
        },
    )

    assert response.status_code == 201


def test_job_cannot_bind_custom_diagnoser_from_another_agent_context(client):
    dataset_uuid, _ = _make_scoped_bindings(client, agent_uuid="agent-b", name_suffix="-scoped")
    custom = client.post(
        "/api/v1/diagnoser",
        json={
            "space_uuid": "s",
            "agent_uuid": "agent-a",
            "name": "agent-rule",
            "type": "custom",
            "mode": "rule",
            "spec": _custom_rule_spec(),
        },
    )
    assert custom.status_code == 201, custom.text

    response = client.post(
        "/api/v1/job",
        json={
            "space_uuid": "s",
            "agent_uuid": "agent-b",
            "name": "scoped-diagnoser-job",
            "trigger_type": "manual",
            "status": "enabled",
            "dataset_uuid": dataset_uuid,
            "diagnoser_uuids": [custom.json()["uuid"]],
        },
    )
    assert response.status_code == 422


def test_job_cannot_bind_builtin_from_another_agent_context(client):
    dataset_uuid, _ = _make_scoped_bindings(
        client,
        agent_uuid="agent-b",
        name_suffix="-builtin-scoped",
    )
    diagnoser = client.post(
        "/api/v1/diagnoser",
        json={
            "space_uuid": "s",
            "agent_uuid": "agent-a",
            "name": "重复调用线索",
            "type": "builtin",
            "mode": "rule",
            "spec": {},
        },
    )
    assert diagnoser.status_code == 201, diagnoser.text

    response = client.post(
        "/api/v1/job",
        json={
            "space_uuid": "s",
            "agent_uuid": "agent-b",
            "name": "cross-agent-builtin-job",
            "trigger_type": "manual",
            "status": "enabled",
            "dataset_uuid": dataset_uuid,
            "diagnoser_uuids": [diagnoser.json()["uuid"]],
        },
    )

    assert response.status_code == 422


def test_job_cannot_bind_shared_builtin_from_another_scope(client):
    dataset_uuid, _ = _make_scoped_bindings(client, name_suffix="-shared-scope")
    shared_diagnoser = client.post(
        "/api/v1/diagnoser",
        json={
            "space_uuid": "s-shard",
            "agent_uuid": "a",
            "name": "重复调用线索",
            "type": "builtin",
            "mode": "rule",
            "spec": {},
        },
    )
    assert shared_diagnoser.status_code == 201, shared_diagnoser.text

    response = client.post(
        "/api/v1/job",
        json={
            "space_uuid": "s",
            "agent_uuid": "a",
            "name": "shared-diagnoser-job",
            "trigger_type": "manual",
            "status": "enabled",
            "dataset_uuid": dataset_uuid,
            "diagnoser_uuids": [shared_diagnoser.json()["uuid"]],
        },
    )

    assert response.status_code == 422, response.text


def test_job_create_allows_each_reserved_shared_space(client):
    for index, space_uuid in enumerate(("s-user_shared", "s-biz_shared", "s-shard")):
        payload = _make_job_payload(
            client,
            name=f"shared-job-{index}",
            space_uuid=space_uuid,
            agent_uuid="a",
        )

        response = client.post("/api/v1/job", json=payload)

        assert response.status_code == 201, response.text


def test_job_create_rejects_unknown_dataset(client):
    """Job creation validates that its Dataset binding exists."""
    _, diagnoser_uuid = _make_scoped_bindings(client, name_suffix="-unknown-ds")
    r = client.post(
        "/api/v1/job",
        json={
            "space_uuid": "s",
            "agent_uuid": "a",
            "name": "bad-ds-job",
            "trigger_type": "manual",
            "status": "enabled",
            "dataset_uuid": "ds-does-not-exist",
            "diagnoser_uuids": [diagnoser_uuid],
        },
    )
    assert r.status_code == 422


def test_job_create_rejects_unknown_diagnoser(client):
    dataset_uuid, _ = _make_scoped_bindings(client, name_suffix="-unknown-dg")
    r = client.post(
        "/api/v1/job",
        json={
            "space_uuid": "s",
            "agent_uuid": "a",
            "name": "bad-dg-job",
            "trigger_type": "manual",
            "status": "enabled",
            "dataset_uuid": dataset_uuid,
            "diagnoser_uuids": ["dg-does-not-exist"],
        },
    )
    assert r.status_code == 422


def test_job_create_rejects_out_of_scope_dataset(client):
    """Dataset created in a different (space, agent) scope than the Job."""
    other_dataset_uuid, _ = _make_scoped_bindings(
        client, space_uuid="other-space", name_suffix="-cross"
    )
    _, diagnoser_uuid = _make_scoped_bindings(client, name_suffix="-cross2")
    r = client.post(
        "/api/v1/job",
        json={
            "space_uuid": "s",
            "agent_uuid": "a",
            "name": "cross-scope-job",
            "trigger_type": "manual",
            "status": "enabled",
            "dataset_uuid": other_dataset_uuid,
            "diagnoser_uuids": [diagnoser_uuid],
        },
    )
    assert r.status_code == 422


def test_job_update_rejects_rebinding_to_out_of_scope_dataset(client):
    job_uuid = client.post(
        "/api/v1/job", json=_make_job_payload(client, name="rebind-target")
    ).json()["uuid"]
    other_dataset_uuid, _ = _make_scoped_bindings(
        client, space_uuid="other-space", name_suffix="-rebind"
    )
    r = client.patch(
        f"/api/v1/job/{job_uuid}",
        json={"dataset_uuid": other_dataset_uuid},
        params={"space_uuid": "s", "agent_uuid": "a"},
    )
    assert r.status_code == 422


def test_job_update_rejects_rebinding_to_out_of_scope_diagnoser(client):
    job_uuid = client.post(
        "/api/v1/job", json=_make_job_payload(client, name="rebind-diagnoser-target")
    ).json()["uuid"]
    _, other_diagnoser_uuid = _make_scoped_bindings(
        client,
        space_uuid="other-space",
        name_suffix="-rebind-diagnoser",
    )

    response = client.patch(
        f"/api/v1/job/{job_uuid}",
        json={"diagnoser_uuids": [other_diagnoser_uuid]},
    )

    assert response.status_code == 422


def test_job_detail_uses_uuid_without_scope_params(client):
    r = client.post("/api/v1/job", json=_make_job_payload(client, name="uuid-job"))
    job_uuid = r.json()["uuid"]

    response = client.get(f"/api/v1/job/{job_uuid}")

    assert response.status_code == 200
    assert response.json()["uuid"] == job_uuid


def test_job_update_and_delete_use_uuid_without_scope_params(client):
    r = client.post("/api/v1/job", json=_make_job_payload(client, name="uuid-job-update"))
    job_uuid = r.json()["uuid"]

    updated = client.patch(f"/api/v1/job/{job_uuid}", json={"name": "updated"})
    assert updated.status_code == 200
    assert updated.json()["name"] == "updated"

    deleted = client.delete(f"/api/v1/job/{job_uuid}")
    assert deleted.status_code == 204


def test_job_trigger_uses_uuid_without_scope_params(client):
    r = client.post("/api/v1/job", json=_make_job_payload(client, name="uuid-job-trigger"))
    job_uuid = r.json()["uuid"]

    triggered = client.post(f"/api/v1/job/{job_uuid}/trigger")

    assert triggered.status_code == 202
    assert triggered.json()["task_uuid"].startswith("t-")


def test_job_trigger_rejects_stale_out_of_scope_diagnoser(client, session):
    from agentlens.infra.ob.tables.job_table import JobTable
    from agentlens.infra.ob.tables.task_table import TaskTable

    job_uuid = client.post(
        "/api/v1/job", json=_make_job_payload(client, name="stale-diagnoser-job")
    ).json()["uuid"]
    _, other_diagnoser_uuid = _make_scoped_bindings(
        client,
        space_uuid="other-space",
        name_suffix="-stale-trigger",
    )
    JobTable(session).update_by_uuid(job_uuid, {"diagnoser_uuids": [other_diagnoser_uuid]})

    response = client.post(f"/api/v1/job/{job_uuid}/trigger")

    assert response.status_code == 422
    assert TaskTable(session).find_active_by_job(job_uuid) is None


def test_job_duplicate_409(client):
    p = _make_job_payload(client, name="dup-job")
    assert client.post("/api/v1/job", json=p).status_code == 201
    assert client.post("/api/v1/job", json=p).status_code == 409


def test_trigger_disabled_job_returns_422(client):
    create = _make_job_payload(client, name="dis")
    job_uuid = client.post("/api/v1/job", json=create).json()["uuid"]
    client.patch(
        f"/api/v1/job/{job_uuid}",
        json={"status": "disabled"},
        params={"space_uuid": "s", "agent_uuid": "a"},
    )
    r = client.post(
        f"/api/v1/job/{job_uuid}/trigger", params={"space_uuid": "s", "agent_uuid": "a"}
    )
    assert r.status_code == 422


def test_job_update_allows_active_task(client):
    create = _make_job_payload(client, name="active-update")
    next_dataset_uuid, next_diagnoser_uuid = _make_scoped_bindings(
        client, name_suffix="-active-update-next"
    )
    job_uuid = client.post("/api/v1/job", json=create).json()["uuid"]

    triggered = client.post(
        f"/api/v1/job/{job_uuid}/trigger",
        params={"space_uuid": "s", "agent_uuid": "a"},
    )
    assert triggered.status_code == 202

    updated = client.patch(
        f"/api/v1/job/{job_uuid}",
        json={
            "name": "updated-while-active",
            "description": "updated description",
            "trigger_type": "manual",
            "schedule_cron": None,
            "status": "disabled",
            "dataset_uuid": next_dataset_uuid,
            "diagnoser_uuids": [next_diagnoser_uuid],
            "modifier": "active-editor",
        },
        params={"space_uuid": "s", "agent_uuid": "a"},
    )

    assert updated.status_code == 200
    body = updated.json()
    assert {
        key: body[key]
        for key in (
            "name",
            "description",
            "trigger_type",
            "schedule_cron",
            "status",
            "dataset_uuid",
            "diagnoser_uuids",
            "modifier",
        )
    } == {
        "name": "updated-while-active",
        "description": "updated description",
        "trigger_type": "manual",
        "schedule_cron": None,
        "status": "disabled",
        "dataset_uuid": next_dataset_uuid,
        "diagnoser_uuids": [next_diagnoser_uuid],
        "modifier": "active-editor",
    }


def test_list_jobs_returns_all_types(client):
    client.post("/api/v1/job", json=_make_job_payload(client, name="sched1"))
    client.post(
        "/api/v1/job",
        json={
            **_make_job_payload(client, name="manual1"),
            "trigger_type": "manual",
            "schedule_cron": None,
        },
    )
    r = client.get(
        "/api/v1/jobs",
        params={"space_uuid": "s", "agent_uuid": "a"},
    )
    body = r.json()
    names = {it["name"] for it in body["items"]}
    assert "sched1" in names
    assert "manual1" in names


def test_list_jobs_includes_user_shared_only_when_requested(client):
    client.post("/api/v1/job", json=_make_job_payload(client, name="local"))
    client.post(
        "/api/v1/job",
        json=_make_job_payload(
            client,
            name="user-shared",
            space_uuid="s-user_shared",
        ),
    )
    client.post(
        "/api/v1/job",
        json=_make_job_payload(
            client,
            name="global-shared",
            space_uuid="s-shard",
        ),
    )

    exact = client.get(
        "/api/v1/jobs",
        params={"space_uuid": "s", "agent_uuid": "a"},
    )
    included = client.get(
        "/api/v1/jobs",
        params={
            "space_uuid": "s",
            "agent_uuid": "a",
            "include_user_shared": True,
        },
    )

    assert [item["name"] for item in exact.json()["items"]] == ["local"]
    assert [(item["space_uuid"], item["name"]) for item in included.json()["items"]] == [
        ("s", "local"),
        ("s-user_shared", "user-shared"),
    ]


def test_user_shared_job_remains_runnable(client):
    created = client.post(
        "/api/v1/job",
        json=_make_job_payload(
            client,
            name="user-shared-runnable",
            space_uuid="s-user_shared",
        ),
    )
    assert created.status_code == 201, created.text

    triggered = client.post(f"/api/v1/job/{created.json()['uuid']}/trigger")

    assert triggered.status_code == 202, triggered.text


def test_delete_job_deletes_tasks_before_job():
    from agentlens.web.routes.job import delete_job

    calls = []

    class Jobs:
        def get_by_uuid(self, uuid):
            calls.append(("get_job", uuid))
            return SimpleNamespace(space_uuid="s", agent_uuid="custom-runtime", spec={})

        def delete_by_uuid(self, uuid):
            calls.append(("delete_job", uuid))
            return True

    class Tasks:
        def find_active_by_job(self, uuid):
            return None

        def delete_by_job_uuid(self, uuid):
            calls.append(("delete_tasks", uuid))
            return 2

    delete_job("j-12345678", Jobs(), Tasks())

    assert calls == [
        ("get_job", "j-12345678"),
        ("delete_tasks", "j-12345678"),
        ("delete_job", "j-12345678"),
    ]


def test_trigger_calls_notify_master_when_real_master(client, session):
    from sqlalchemy import text

    session.execute(
        text("""
        UPDATE agentlens_leader_lease
        SET holder_uuid='node-X/9.9.9.9'
        WHERE lock_key='master'
    """)
    )
    session.commit()
    create_payload = _make_job_payload(client, name="trig1")
    job_uuid = client.post("/api/v1/job", json=create_payload).json()["uuid"]

    with patch(
        "agentlens.kernel.notify.NotifyClient.notify_master", new=AsyncMock(return_value=True)
    ) as mock:
        r = client.post(
            f"/api/v1/job/{job_uuid}/trigger", params={"space_uuid": "s", "agent_uuid": "a"}
        )
        assert r.status_code == 202
        mock.assert_called_once()
        assert mock.call_args.args[0] == "node-X/9.9.9.9"


def test_trigger_skips_notify_when_bootstrap_master(client, session):
    create_payload = _make_job_payload(client, name="trig2")
    job_uuid = client.post("/api/v1/job", json=create_payload).json()["uuid"]
    with patch(
        "agentlens.kernel.notify.NotifyClient.notify_master", new=AsyncMock(return_value=True)
    ) as mock:
        r = client.post(
            f"/api/v1/job/{job_uuid}/trigger", params={"space_uuid": "s", "agent_uuid": "a"}
        )
        assert r.status_code == 202
        mock.assert_not_called()


def test_trigger_accepts_dataset_runtime_type_without_projector(client, session):
    """A non-empty runtime_type does not require a registered Projector."""
    from agentlens.domain.dataset import Dataset
    from agentlens.domain.enums import DatasetType, JobStatus, JobTriggerType
    from agentlens.domain.job import Job
    from agentlens.infra.ob.tables.dataset_table import DatasetTable
    from agentlens.infra.ob.tables.job_table import JobTable
    from agentlens.infra.ob.tables.task_table import TaskTable
    from agentlens.utils.uuid_gen import new_dataset_uuid, new_job_uuid

    ds = Dataset(
        uuid=new_dataset_uuid(),
        space_uuid="s",
        agent_uuid="a",
        name="ds-bad-rt",
        description="",
        type=DatasetType.SIGNOZ,
        spec={"signoz": {}, "query": {"table": "t"}, "runtime_type": "nope"},
        gmt_create=None,
        gmt_modified=None,
        creator=None,
        modifier=None,
    )
    DatasetTable(session).insert(ds)
    _, diagnoser_uuid = _make_scoped_bindings(client, name_suffix="-bad-rt")
    job = Job(
        uuid=new_job_uuid(),
        space_uuid="s",
        agent_uuid="a",
        name="j-bad-rt",
        description="",
        trigger_type=JobTriggerType.MANUAL,
        schedule_cron=None,
        status=JobStatus.ENABLED,
        dataset_uuid=ds.uuid,
        diagnoser_uuids=[diagnoser_uuid],
        gmt_create=None,
        gmt_modified=None,
        creator=None,
        modifier=None,
    )
    JobTable(session).insert(job)

    r = client.post(
        f"/api/v1/job/{job.uuid}/trigger",
        params={"space_uuid": "s", "agent_uuid": "a"},
    )
    assert r.status_code == 202
    assert TaskTable(session).find_active_by_job(job.uuid) is not None


@pytest.mark.asyncio
async def test_dsh_builtin_job_runs_six_rules_and_writes_results(client, session, signoz_inmem):
    """Exercise registration, job binding, trigger, retrieval, runner and result storage."""
    import json

    from agentlens.domain.enums import TaskStatus
    from agentlens.infra.antcollector import InMemoryAntcollectorClient
    from agentlens.infra.ob.tables.dataset_table import DatasetTable
    from agentlens.infra.ob.tables.diagnoser_table import DiagnoserTable
    from agentlens.infra.ob.tables.job_table import JobTable
    from agentlens.infra.ob.tables.task_table import TaskTable
    from agentlens.kernel.worker.runner import Runner
    from agentlens.service.datasource.signoz import TraceStore
    from agentlens.service.diagnosis_record_store import DiagnosisRecordStore
    from tests.unit.diagnoser.test_dsh_builtin_diagnosis import EXECUTORS, dsh_trace

    scope = {"space_uuid": "s-dsh", "agent_uuid": "a-dsh"}
    diagnosers = []
    for executor in EXECUTORS:
        response = client.post(
            "/api/v1/diagnoser",
            json={
                **scope,
                "name": executor.definition.name,
                "type": "builtin",
                "mode": "rule",
            },
        )
        assert response.status_code == 201, response.text
        diagnosers.append(response.json()["uuid"])
    response = client.post(
        "/api/v1/dataset",
        json={
            **scope,
            "name": "dsh-source",
            "type": "signoz",
            "spec": {
                "runtime_type": "dsh",
                "query": {"table": "dsh_trace", "where": "`parent_span_id` = ''"},
            },
        },
    )
    assert response.status_code == 201, response.text
    dataset_uuid = response.json()["uuid"]
    response = client.post(
        "/api/v1/job",
        json={
            **scope,
            "name": "dsh-six-rules",
            "trigger_type": "manual",
            "status": "enabled",
            "dataset_uuid": dataset_uuid,
            "diagnoser_uuids": diagnosers,
            "spec": {"save_badcase_only": False},
        },
    )
    assert response.status_code == 201, response.text
    response = client.post(f"/api/v1/job/{response.json()['uuid']}/trigger")
    assert response.status_code == 202, response.text
    task_uuid = response.json()["task_uuid"]
    tasks = TaskTable(session)
    assert tasks.cas_dispatch(task_uuid, "dsh-worker")
    signoz_inmem.seed("dsh_trace", dsh_trace().spans)
    collector = InMemoryAntcollectorClient(signoz_inmem._tables)
    runner = Runner(
        task_table=tasks,
        job_table=JobTable(session),
        dataset_table=DatasetTable(session),
        diagnoser_table=DiagnoserTable(session),
        trace_store=TraceStore(signoz_inmem),
        record_store=DiagnosisRecordStore(signoz_inmem, collector, table="dsh_results"),
        self_uuid="dsh-worker",
    )
    await runner.run(task_uuid)
    assert tasks.get_by_uuid(task_uuid).status == TaskStatus.SUCCEEDED
    rows = collector.records.get("dsh_results", [])
    bodies = [json.loads(row["attributes.record.body"]) for row in rows]
    assert len(bodies) == 6
    assert {body["diagnoser_uuid"] for body in bodies} == set(diagnosers)
    assert all(body["task_uuid"] == task_uuid for body in bodies)
