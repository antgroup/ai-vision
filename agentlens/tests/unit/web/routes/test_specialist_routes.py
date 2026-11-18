"""Actual ASGI route flow and offline transactions, never an alternate database."""

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentlens.infra.ob.models import DiagnoserModel, ModelConnectionModel
from agentlens.web.deps import get_session, get_trace_store
from agentlens.web.routes.specialist import router
from tests.unit.diagnoser.test_specialist_materials import trace
from tests.unit.diagnoser.test_specialist_policies import relevance
from tests.unit.diagnoser.test_specialist_sync import configuration


class OfflineSession:
    def __init__(self):
        self.rows = []
        self.models = []
        self.commits = 0
        self.rollbacks = 0
        self.flushes = 0
        self.snapshot = []

    def get_bind(self):
        return None

    def in_transaction(self):
        return False

    @contextmanager
    def begin(self):
        try:
            yield
            self.commit()
        except BaseException:
            self.rollback()
            raise

    def scalars(self, statement):
        model = statement.column_descriptions[0]["entity"]
        rows = self.rows if model is DiagnoserModel else self.models
        parameters = statement.compile().params
        return [
            row
            for row in rows
            if all(
                getattr(row, key.rsplit("_", 1)[0]) == value for key, value in parameters.items()
            )
        ]

    def scalar(self, statement):
        return next(iter(self.scalars(statement)), None)

    def add(self, row):
        self.rows.append(row)

    def flush(self):
        self.flushes += 1

    def commit(self):
        self.commits += 1
        self.snapshot = deepcopy(self.rows)

    def rollback(self):
        self.rollbacks += 1
        self.rows = deepcopy(self.snapshot)

    def expire_all(self):
        pass


def source_spec():
    return {
        "runtime_type": "langchain",
        "scope_type": "team",
        "space_filter": "space",
        "space_column": "space_id",
        "agent_product_filter": "product",
        "agent_product_column": "agent_product",
        "query": {"table": "aivision_traces"},
    }


@pytest.fixture
def platform():
    session = OfflineSession()
    reads = []

    def read(spec, ids, **kwargs):
        reads.append((deepcopy(spec), ids, kwargs))
        return trace().spans

    store = SimpleNamespace(read_explicit_trace_rows=read)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_trace_store] = lambda: store
    return TestClient(app), session, reads, store


def sync_body():
    return {
        "space_uuid": "space",
        "agent_uuid": "agent",
        "operator": "user",
        "key": "protected_content_exposure",
        "variant": "configured_exact_markers",
        "configuration": {"policy": configuration().policy.model_dump()},
    }


def trial_body():
    body = sync_body()
    body.pop("operator")
    selection = {key: body.pop(key) for key in ("key", "variant", "configuration")}
    return {**body, "dataset_spec": source_spec(), "trace_id": "t", "draft": selection}


@pytest.mark.parametrize("runtime", ["langchain", "langfuse-sdk"])
def test_catalog_seven_capabilities_three_unavailable_and_nine_variants(platform, runtime):
    client, *_ = platform
    response = client.get(
        "/api/v1/diagnosers/specialist-catalog",
        params={"space_uuid": "space", "agent_uuid": "agent", "runtime_type": runtime},
    )
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 10 and sum(item["selectable"] for item in items) == 7
    assert sum(len(item["supported_variants"]) for item in items) == 9
    coding = client.get(
        "/api/v1/diagnosers/specialist-catalog",
        params={"space_uuid": "space", "agent_uuid": "agent", "runtime_type": "codex"},
    ).json()
    tool = next(item for item in coding["items"] if item["key"] == "required_tool_execution")
    assert not tool["selectable"] and tool["unavailable_reason"] == "runtime_mapping_unavailable"
    unsupported = client.get(
        "/api/v1/diagnosers/specialist-catalog",
        params={"space_uuid": "space", "agent_uuid": "agent", "runtime_type": "other"},
    ).json()
    assert all(not item["selectable"] for item in unsupported["items"])


def test_sync_then_configuration_preserve_and_replace_private_policy_cas(platform):
    client, session, *_ = platform
    response = client.post("/api/v1/diagnosers/sync-specialist-builtins", json=sync_body())
    assert response.status_code == 200, response.text
    first = response.json()
    assert first["action"] == "created" and "private-fixture" not in response.text
    assert first["diagnoser"]["creator"] == "user" and session.commits == 1
    uuid, digest = first["diagnoser"]["uuid"], first["specialist"]["configuration_digest"]
    patch = {
        "space_uuid": "space",
        "agent_uuid": "agent",
        "operator": "reviewer",
        "expected_configuration_digest": digest,
        "configuration": {"policy": {"kind": "preserve_existing_policy"}},
    }
    preserved = client.patch(f"/api/v1/diagnosers/{uuid}/specialist-configuration", json=patch)
    assert preserved.status_code == 200 and preserved.json()["action"] == "unchanged"
    patch["configuration"]["policy"] = configuration("replacement-secret").policy.model_dump()
    changed = client.patch(f"/api/v1/diagnosers/{uuid}/specialist-configuration", json=patch)
    assert changed.status_code == 200 and changed.json()["action"] == "updated"
    assert "replacement-secret" not in changed.text
    assert session.rows[0].spec["specialist_policy"]["markers"][0]["value"] == "replacement-secret"
    stale = client.patch(f"/api/v1/diagnosers/{uuid}/specialist-configuration", json=patch)
    assert stale.status_code == 409 and stale.json()["code"] == "SPECIALIST_CONFIGURATION_CONFLICT"


@pytest.mark.parametrize("runtime", ["langchain", "langfuse-sdk"])
def test_rule_trial_uses_full_server_rows_zero_judge_no_resource_write(platform, runtime):
    client, session, reads, _ = platform
    body = trial_body()
    body["dataset_spec"]["runtime_type"] = runtime
    response = client.post("/api/v1/diagnoser/specialist-trial", json=body)
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["verdict"] == "good" and value["request_attempts"] == {"reserved": 0, "max": 0}
    assert reads[0][1:] == (["t"], {"for_diagnosis_input": True})
    assert session.rows == [] and session.flushes == 0 and session.commits == 1
    assert "private-fixture" not in response.text


@pytest.mark.parametrize(
    "extra",
    [
        {"test_input": {}},
        {"spans": []},
        {"complete": True},
        {"authoritative": True},
        {"sources": []},
        {"sql": "SELECT 1"},
    ],
)
def test_browser_materials_or_source_claims_are_rejected_before_read(platform, extra):
    client, session, reads, _ = platform
    response = client.post("/api/v1/diagnoser/specialist-trial", json={**trial_body(), **extra})
    assert response.status_code == 422 and response.json()["code"] == "SPECIALIST_INPUT_INVALID"
    assert reads == [] and session.rows == []


@pytest.mark.parametrize(
    "selection",
    [{}, {"diagnoser_uuid": "a" * 32, "draft": {}}, {"diagnoser_uuid": None}, {"draft": None}],
)
def test_saved_xor_draft_is_strict_before_any_read(platform, selection):
    client, _, reads, _ = platform
    body = trial_body()
    body.pop("draft")
    response = client.post("/api/v1/diagnoser/specialist-trial", json={**body, **selection})
    assert response.status_code == 422 and reads == []


def test_malformed_private_policy_cannot_echo_in_validation_errors(platform):
    client, *_ = platform
    body = sync_body()
    body["configuration"]["policy"]["private-fixture"] = "private-fixture"
    response = client.post("/api/v1/diagnosers/sync-specialist-builtins", json=body)
    assert response.status_code == 422 and "private-fixture" not in response.text


def test_unavailable_mapping_cannot_sync_or_trial(platform):
    client, session, reads, _ = platform
    body = sync_body()
    body["key"] = "subject_consistency"
    response = client.post("/api/v1/diagnosers/sync-specialist-builtins", json=body)
    assert response.status_code == 422 and response.json()["code"] == "SPECIALIST_UNAVAILABLE"
    assert session.rows == [] and reads == []


def test_tool_trial_on_coding_runtime_is_unavailable_before_source_read(platform):
    client, _, reads, _ = platform
    body = trial_body()
    body["dataset_spec"]["runtime_type"] = "codex"
    body["draft"] = {
        "key": "required_tool_execution",
        "variant": "observed_tool_completion",
        "configuration": {"policy": {"kind": "required_tool_execution_v1", "tool_name": "read"}},
    }
    response = client.post("/api/v1/diagnoser/specialist-trial", json=body)
    assert response.status_code == 422 and response.json()["code"] == "SPECIALIST_UNAVAILABLE"
    assert reads == []


def test_query_fault_is_failed_not_missing_material_or_scope_skip(platform):
    client, _, _, store = platform

    def broken(*args, **kwargs):
        raise ValueError("private-fixture decode query fault")

    store.read_explicit_trace_rows = broken
    response = client.post("/api/v1/diagnoser/specialist-trial", json=trial_body())
    assert response.status_code == 200
    result = response.json()
    assert not result["skipped"] and result["execution"]["code"] == "source_query_failed"
    assert "private-fixture" not in response.text


def test_whole_trace_foreign_scope_and_diagnosis_output_reject_no_write(platform):
    client, session, reads, store = platform
    body = trial_body()
    body["dataset_spec"]["space_filter"] = "foreign"
    response = client.post("/api/v1/diagnoser/specialist-trial", json=body)
    assert response.status_code == 422 and reads == []
    store.read_explicit_trace_rows = lambda *args, **kwargs: [
        {
            "trace_id": "t",
            "span_id": "s",
            "service_name": "agentlens",
            "name": "diagnosis_record",
            "attributes.record.body": "{}",
            "attributes.job_uuid": "job",
            "attributes.task_uuid": "task",
        }
    ]
    response = client.post("/api/v1/diagnoser/specialist-trial", json=trial_body())
    assert response.status_code == 422 and session.rows == []


def test_llm_trial_missing_history_never_binds_or_calls_model(platform):
    client, session, _, store = platform
    body = trial_body()
    body["draft"] = {
        "key": "followup_reasonableness",
        "variant": "recorded_conversation",
        "configuration": {
            "policy": {
                "kind": "followup_reasonableness_v1",
                "context_mode": "recorded_conversation",
            },
            "model_connection_uuid": "missing-model",
        },
    }
    store.read_explicit_trace_rows = lambda *args, **kwargs: trace("current", "answer").spans
    result = client.post("/api/v1/diagnoser/specialist-trial", json=body).json()
    assert result["skipped"] and result["request_attempts"] == {"reserved": 0, "max": 3}
    assert session.rows == []


@pytest.mark.parametrize("status", ["untested", "unavailable"])
def test_specialist_sync_rejects_same_space_model_that_is_not_available(platform, status):
    client, session, *_ = platform
    session.models = [ModelConnectionModel(uuid="model", space_uuid="space", status=status)]
    body = {
        **sync_body(),
        "key": "answer_relevance",
        "variant": "explicit_current_text",
        "configuration": relevance(),
    }
    response = client.post("/api/v1/diagnosers/sync-specialist-builtins", json=body)
    assert response.status_code == 422 and session.rows == []


def test_specialist_sync_rejects_foreign_model_and_patch_preserves_omitted_llm_fields(platform):
    client, session, *_ = platform
    body = {
        **sync_body(),
        "key": "answer_relevance",
        "variant": "explicit_current_text",
        "configuration": relevance({"enable_thinking": None, "temperature": 0.7, "max_tokens": 64}),
    }
    session.models = [ModelConnectionModel(uuid="model", space_uuid="foreign", status="available")]
    assert client.post("/api/v1/diagnosers/sync-specialist-builtins", json=body).status_code == 422
    session.models[0].space_uuid = "space"
    created = client.post("/api/v1/diagnosers/sync-specialist-builtins", json=body)
    assert created.status_code == 200
    saved = created.json()
    uuid = saved["diagnoser"]["uuid"]
    patch = {
        "space_uuid": "space",
        "agent_uuid": "agent",
        "operator": "user",
        "expected_configuration_digest": saved["specialist"]["configuration_digest"],
        "configuration": {"policy": {"kind": "preserve_existing_policy"}},
    }
    response = client.patch(f"/api/v1/diagnosers/{uuid}/specialist-configuration", json=patch)
    assert response.status_code == 200 and response.json()["action"] == "unchanged"
    assert (
        session.rows[0].spec["enable_thinking"] is None and session.rows[0].spec["max_tokens"] == 64
    )


def test_saved_dg_resource_trial_reads_internal_policy_and_foreign_scope_cannot_borrow(platform):
    client, session, reads, _ = platform
    saved = client.post("/api/v1/diagnosers/sync-specialist-builtins", json=sync_body()).json()
    uuid = saved["diagnoser"]["uuid"]
    assert uuid.startswith("dg-")
    body = trial_body()
    body.pop("draft")
    body["diagnoser_uuid"] = uuid
    response = client.post("/api/v1/diagnoser/specialist-trial", json=body)
    assert response.status_code == 200 and response.json()["verdict"] == "good"
    count = len(reads)
    body["agent_uuid"] = "foreign-agent"
    response = client.post("/api/v1/diagnoser/specialist-trial", json=body)
    assert response.status_code == 404 and len(reads) == count and len(session.rows) == 1


def test_public_sync_recovers_unique_race_in_outer_transaction_and_revalidates_winner(
    platform, monkeypatch
):
    from sqlalchemy.exc import IntegrityError

    from agentlens.diagnoser.definitions.specialists import (
        get_specialist_definition,
        specialist_spec,
    )
    from agentlens.domain.specialist_policy import normalize_specialist_configuration

    client, session, *_ = platform
    definition = get_specialist_definition("protected_content_exposure", "configured_exact_markers")
    config = normalize_specialist_configuration(
        definition["key"], definition["variant"], sync_body()["configuration"]
    )
    now = datetime.now()
    winner = DiagnoserModel(
        uuid="dg-winner00",
        space_uuid="space",
        agent_uuid="agent",
        type="builtin",
        mode="rule",
        version="1.0.0",
        name=definition["name"],
        description=definition["description"],
        spec=specialist_spec(definition, config),
        creator="other-operator",
        modifier="other-operator",
        gmt_create=now,
        gmt_modified=now,
    )

    def racing_flush():
        session.snapshot = [winner]
        raise IntegrityError("fixture unique collision", {}, Exception("duplicate"))

    monkeypatch.setattr(session, "flush", racing_flush)
    response = client.post("/api/v1/diagnosers/sync-specialist-builtins", json=sync_body())
    assert response.status_code == 200, response.text
    assert response.json()["diagnoser"]["uuid"] == "dg-winner00"
    assert response.json()["action"] == "unchanged" and session.rollbacks == 1
    assert session.rows[0].creator == "other-operator"


@pytest.mark.parametrize(
    "kind,requests,failed", [("valid", 1, False), ("malformed", 3, True), ("403", 1, True)]
)
def test_full_http_trial_budget_has_no_probe_no_write_and_at_most_three_attempts(
    platform, monkeypatch, kind, requests, failed
):
    import json

    import httpx

    import agentlens.diagnoser.executors.llm.base as base
    from agentlens.infra.llm_judge import LLMJudge
    from tests.unit.diagnoser.test_specialist_executors import llm_result

    client, session, _, _ = platform
    session.models = [
        ModelConnectionModel(
            uuid="model",
            space_uuid="space",
            status="available",
            endpoint="https://example.invalid/v1",
            model_name="fake",
            api_key="fixture",
        )
    ]
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        if kind == "403":
            return httpx.Response(403, json={"error": {"message": "private-fixture"}})
        content = (
            "malformed" if kind == "malformed" else json.dumps(llm_result(), ensure_ascii=False)
        )
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    monkeypatch.setattr(
        base,
        "_build_judge",
        lambda spec: LLMJudge(
            "https://example.invalid/v1",
            "fixture",
            "fake",
            transport=httpx.MockTransport(handler),
            max_tokens=spec["max_tokens"],
            backoff_sec=0,
        ),
    )
    body = trial_body()
    body["draft"] = {
        "key": "answer_relevance",
        "variant": "explicit_current_text",
        "configuration": relevance(),
    }
    response = client.post("/api/v1/diagnoser/specialist-trial", json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["request_attempts"] == {"reserved": requests, "max": 3}
    assert len(calls) == requests and all(call["max_tokens"] == 4096 for call in calls)
    assert (result["execution"]["status"] == "failed") == failed and not result["skipped"]
    assert session.commits == 1 and session.flushes == 0 and session.rows == []
    assert "private-fixture" not in response.text
