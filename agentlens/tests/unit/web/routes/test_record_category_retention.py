"""Historical categories survive the independent retention of Task control rows."""

from collections import defaultdict
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentlens.domain.diagnosis_record import DiagnosisRecord, Execution, Metric
from agentlens.domain.enums import ReviewStatus, TargetType, Verdict
from agentlens.infra.antcollector import InMemoryAntcollectorClient
from agentlens.infra.signoz.client import InMemorySignozClient
from agentlens.service.diagnosis_record_store import DiagnosisRecordStore
from agentlens.web.deps import get_diagnoser_table, get_record_store
from agentlens.web.routes.diagnosis_record import get_record, list_records, router


def _store():
    transport = defaultdict(list)
    return DiagnosisRecordStore(
        InMemorySignozClient(transport),
        InMemoryAntcollectorClient(transport),
        table="agentlens_trace",
    )


def _definition(category, uuid="dg-1"):
    return {
        "uuid": uuid,
        "key": "historical-check",
        "name": "Historical check",
        "category": category,
        "weight": 1,
        "score_role": "quality",
        "metrics": [{"name": "quality", "desc": "Historical metric"}],
        "version": "v1",
        "revision": "0.9.0",
        "digest": "d" * 64,
    }


def _record(uuid, *, category=None, task="removed-task", job="j-1", diagnoser="dg-1"):
    now = datetime.now()
    snapshot = {}
    if category is not None:
        snapshot = {
            "schema_version": 3,
            "score_policy_version": "lightweight-v1",
            "diagnoser_definition": _definition(category, diagnoser),
        }
    return DiagnosisRecord(
        uuid=uuid,
        task_uuid=task,
        job_uuid=job,
        diagnoser_uuid=diagnoser,
        diagnoser_version="v1",
        target_type=TargetType.TRACE,
        target_id="trace-" + uuid,
        metrics=[Metric(name="quality", verdict=Verdict.BAD, reason="Historical reason")],
        execution=Execution.succeeded(),
        trace_snapshot=snapshot,
        review_status=ReviewStatus.PENDING,
        feedback=None,
        reviewed_by=None,
        reviewed_at=None,
        diagnosed_at=now,
        target_created_at=now,
        space_uuid="space-1",
    )


def _task(uuid, category):
    definition = _definition(category)
    return SimpleNamespace(
        uuid=uuid,
        job_uuid="j-1",
        execution_snapshot={
            "schema_version": 1,
            "score_policy_version": "lightweight-v1",
            "executor_revision": "0.9.0",
            "diagnosers": [
                {
                    **{
                        key: definition[key]
                        for key in ("uuid", "key", "name", "version", "revision", "digest")
                    },
                    "spec": {
                        key: definition[key]
                        for key in ("category", "weight", "score_role", "metrics")
                    },
                }
            ],
        },
    )


def _repository(*tasks, current="process"):
    # Only the OB transport is replaced; production snapshot parsing stays real.
    return SimpleNamespace(
        s=SimpleNamespace(scalars=lambda _statement: list(tasks)),
        list=lambda: [SimpleNamespace(uuid="dg-1", spec={"category": current})],
    )


def _list(store, repository, category=None, **kwargs):
    return list_records(
        store=store,
        diagnosers=repository,
        job_uuid=kwargs.pop("job_uuid", ["j-1"]),
        category=category,
        limit=kwargs.pop("limit", 50),
        offset=kwargs.pop("offset", 0),
        **kwargs,
    )


@pytest.mark.parametrize("surviving_tasks", [False, True])
def test_saved_category_remains_queryable_after_task_retention(surviving_tasks):
    store = _store()
    store.write_record(_record("historical", category="outcome"))
    tasks = [_task("unrelated-task", "process")] if surviving_tasks else []
    repository = _repository(*tasks, current="process")

    assert _list(store, repository).total == 1
    assert get_record("historical", store).trace_snapshot["diagnoser_definition"] == _definition(
        "outcome"
    )
    result = _list(store, repository, "outcome")
    assert result.total == 1
    assert [item.uuid for item in result.items] == ["historical"]
    assert _list(store, repository, "process").total == 0
    assert _list(store, repository, "unknown").total == 0


def test_saved_category_wins_over_contradictory_task_and_current_catalog():
    store = _store()
    store.write_record(_record("historical", category="outcome", task="surviving-task"))
    repository = _repository(_task("surviving-task", "safety"), current="process")

    assert _list(store, repository, "outcome").total == 1
    assert _list(store, repository, "safety").total == 0
    assert _list(store, repository, "process").total == 0


def test_category_counts_and_pages_one_global_scope_after_latest_attempt_wins():
    store = _store()
    for record in (
        _record("a", category="outcome"),
        _record("b", category="outcome", task="surviving-task", job="j-2"),
        _record("c", category="process"),
        _record("d", category="outcome", diagnoser="dg-2"),
        _record("foreign-job", category="outcome", job="not-requested"),
    ):
        store.write_record(record)
    store.write_record(_record("changed-attempt", category="outcome"), attempt=0)
    store.write_record(_record("changed-attempt", category="process"), attempt=1)
    repository = _repository(_task("surviving-task", "safety"), current="process")
    args = dict(
        job_uuid=["j-1", "j-2"], diagnoser_uuid="dg-1", sort_by="uuid", sort_order="asc", limit=1
    )

    first = _list(store, repository, "outcome", offset=0, **args)
    second = _list(store, repository, "outcome", offset=1, **args)
    beyond = _list(store, repository, "outcome", offset=2, **args)
    assert first.total == second.total == beyond.total == 2
    assert [item.uuid for item in first.items + second.items] == ["a", "b"]
    assert beyond.items == []
    assert _list(store, repository, "outcome", diagnoser_uuid="dg-2").total == 1
    assert _list(store, repository, "outcome", diagnoser_uuid="not-selected").total == 0


def test_missing_record_definition_uses_only_the_same_persisted_task_snapshot():
    store = _store()
    for name, task in (("old", "old-task"), ("new", "new-task")):
        record = _record(name, task=task)
        record.trace_snapshot = {"schema_version": 3, "score_policy_version": "lightweight-v1"}
        store.write_record(record)
    repository = _repository(
        _task("old-task", "outcome"), _task("new-task", "safety"), current="process"
    )

    result = _list(store, repository, "outcome")
    assert result.total == 1
    assert [item.uuid for item in result.items] == ["old"]
    assert _list(store, repository, "process").total == 0


@pytest.mark.parametrize("task_exists", [False, True])
def test_true_legacy_without_frozen_metadata_keeps_explicit_current_category_fallback(task_exists):
    store = _store()
    store.write_record(_record("legacy"))
    tasks = [SimpleNamespace(uuid="removed-task", execution_snapshot=None)] if task_exists else []
    repository = _repository(*tasks, current="process")

    assert _list(store, repository, "process").total == 1
    assert _list(store, repository, "outcome").total == 0
    assert _list(store, repository, "unknown").total == 0


@pytest.mark.parametrize(
    "snapshot",
    [
        {"schema_version": 3},
        {"schema_version": "3"},
        {"schema_version": True},
        {"schema_version": None},
        {"schema_version": 999},
        {"score_policy_version": None},
        {"diagnoser_definition": None},
        {"diagnoser_definition": {"uuid": "foreign", "category": "outcome"}},
        {"diagnoser_definition": {"uuid": "dg-1"}},
        {"diagnoser_definition": {"uuid": "dg-1", "category": "unknown"}},
    ],
)
def test_missing_or_damaged_modern_metadata_never_degrades_to_current_catalog(snapshot):
    store = _store()
    record = _record("damaged")
    record.trace_snapshot = snapshot
    store.write_record(record)
    repository = _repository(current="outcome")

    assert _list(store, repository).total == 1
    assert _list(store, repository, "outcome").total == 0


@pytest.mark.parametrize(
    "definition",
    [
        None,
        {"uuid": "foreign", "category": "outcome"},
        {"uuid": "dg-1", "category": []},
        {"uuid": "dg-1", "category": 3},
        {"uuid": "dg-1", "category": "unknown"},
    ],
)
def test_damaged_saved_definition_does_not_borrow_a_matching_task_snapshot(definition):
    store = _store()
    record = _record("damaged", category="outcome", task="surviving-task")
    record.trace_snapshot["diagnoser_definition"] = definition
    store.write_record(record)
    repository = _repository(_task("surviving-task", "outcome"), current="outcome")

    assert _list(store, repository, "outcome").total == 0
    assert _list(store, repository, "unknown").total == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", "3"),
        ("schema_version", True),
        ("schema_version", None),
        ("schema_version", 999),
        ("score_policy_version", None),
        ("score_policy_version", "future-policy"),
        ("score_policy_version", []),
        ("score_policy_version", False),
    ],
)
def test_damaged_modern_stamp_cannot_borrow_a_matching_task_snapshot(field, value):
    store = _store()
    record = _record("damaged", task="surviving-task")
    record.trace_snapshot = {"schema_version": 3, "score_policy_version": "lightweight-v1"}
    record.trace_snapshot[field] = value
    store.write_record(record)
    repository = _repository(_task("surviving-task", "outcome"), current="outcome")

    assert _list(store, repository, "outcome").total == 0


@pytest.mark.parametrize("body", ["{", "[]", '{"trace_snapshot": {}}'])
def test_unparseable_or_mismatched_record_body_cannot_be_classified_as_legacy(body):
    store = _store()
    store.write_record(_record("damaged"))
    store.reader._tables[store.table][0]["attributes.record.body"] = body

    assert _list(store, _repository(current="outcome"), "outcome").total == 0


def test_category_http_response_stays_compact_after_reading_saved_definition():
    store = _store()
    record = _record("historical", category="outcome")
    record.trace_snapshot.update(
        spans=[{"content": "synthetic-private-trace"}],
        rendered_prompt="synthetic-private-prompt",
    )
    record.trace_snapshot["diagnoser_definition"]["spec"] = {
        "prompt": "synthetic-private-definition-prompt"
    }
    store.write_record(record)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_record_store] = lambda: store
    app.dependency_overrides[get_diagnoser_table] = lambda: _repository(current="process")
    response = TestClient(app).get(
        "/api/v1/diagnosis-records", params={"job_uuid": "j-1", "category": "outcome"}
    )

    assert response.status_code == 200
    assert response.json()["total"] == 1
    item = response.json()["items"][0]
    assert item["uuid"] == "historical"
    assert item["space_uuid"] == record.space_uuid == "space-1"
    assert item["diagnoser_definition"] == _definition("outcome")
    assert all(
        private_value not in response.text
        for private_value in (
            "synthetic-private-trace",
            "synthetic-private-prompt",
            "synthetic-private-definition-prompt",
        )
    )
    assert set(item) == {
        "space_uuid",
        "diagnoser_definition",
        "uuid",
        "task_uuid",
        "job_uuid",
        "diagnoser_uuid",
        "diagnoser_version",
        "target_type",
        "target_id",
        "owner_id",
        "exp_ver_id",
        "model",
        "verdict",
        "review_status",
        "summary",
        "reviewed_at",
        "diagnosed_at",
        "target_created_at",
    }
