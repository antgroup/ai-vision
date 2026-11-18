import json
from copy import deepcopy

import pytest

from agentlens.service.diagnosis_record_store import _list_item_from_row
from agentlens.service.execution_snapshot import snapshot_definitions
from agentlens.web.routes.diagnosis_record import _list_out
from tests.unit.service.test_core_thinking_defaults import new_quick_snapshot


def retained_row(monkeypatch):
    _, snapshot, _ = new_quick_snapshot(monkeypatch)
    definition = next(iter(snapshot_definitions(snapshot).values()))
    identity = dict(
        uuid="record",
        job_uuid="job-one",
        task_uuid="task-one",
        diagnoser_uuid=definition["uuid"],
        diagnoser_version=definition["version"],
        target_type="trace",
        target_id="trace-a",
    )
    body = {
        **identity,
        "space_uuid": "s",
        "trace_snapshot": {
            "schema_version": 3,
            "score_policy_version": "lightweight-v1",
            "diagnoser_definition": definition,
            "private_trace": "not-public",
        },
    }
    row = {
        **identity,
        "verdict": "good",
        "review_status": "pending",
        "record.body": json.dumps(body),
    }
    return row, body, definition


def test_list_projects_only_own_frozen_definition(monkeypatch):
    row, body, definition = retained_row(monkeypatch)
    definition = deepcopy(definition)
    body["trace_snapshot"]["diagnoser_definition"]["spec"] = {"prompt": "private-prompt"}
    row["record.body"] = json.dumps(body)
    result = _list_out(_list_item_from_row(row)).model_dump()
    assert result["diagnoser_definition"] == definition
    assert result["space_uuid"] == "s"
    assert "private-prompt" not in json.dumps(result, default=str)
    assert "private_trace" not in json.dumps(result, default=str)


@pytest.mark.parametrize(
    "change",
    [
        "uuid",
        "job_uuid",
        "task_uuid",
        "diagnoser_uuid",
        "diagnoser_version",
        "target_id",
        "schema",
        "version",
        "missing",
    ],
)
def test_mixed_or_unsupported_record_never_exports_definition(monkeypatch, change):
    row, body, _ = retained_row(monkeypatch)
    if change in row:
        body[change] = "other"
    elif change == "schema":
        body["trace_snapshot"]["schema_version"] = 99
    elif change == "version":
        body["trace_snapshot"]["diagnoser_definition"]["version"] = "other"
    else:
        body.pop("trace_snapshot")
    row["record.body"] = json.dumps(body)
    result = _list_out(_list_item_from_row(row)).model_dump()
    assert result["diagnoser_definition"] is None


def test_retention_then_real_record_list_and_detail_api(client, session, signoz_inmem, monkeypatch):
    from datetime import datetime, timedelta

    from agentlens.infra.antcollector import InMemoryAntcollectorClient
    from agentlens.infra.ob.models import TaskModel
    from agentlens.infra.ob.tables.task_table import TaskTable
    from agentlens.service.diagnosis_record_store import DiagnosisRecordStore
    from agentlens.web.deps import get_settings
    from tests.unit.service.test_diagnosis_record_store import _make

    _, _, definition = retained_row(monkeypatch)
    old = datetime.now() - timedelta(days=40)
    session.add(
        TaskModel(uuid="t-1", job_uuid="j-1", status="succeeded", scheduled_at=old, finished_at=old)
    )
    session.commit()
    record = _make()
    record.space_uuid = "s"
    record.diagnoser_uuid = definition["uuid"]
    record.diagnoser_version = definition["version"]
    record.trace_snapshot = {
        "schema_version": 3,
        "score_policy_version": "lightweight-v1",
        "diagnoser_definition": definition,
    }
    store = DiagnosisRecordStore(
        signoz_inmem,
        InMemoryAntcollectorClient(signoz_inmem._tables),
        table=get_settings().diagnosis_record_table,
    )
    store.write_record(record)
    assert (
        TaskTable(session).delete_expired_terminal(
            cutoff=datetime.now() - timedelta(days=30), batch_size=100
        )[0]
        == 1
    )
    assert TaskTable(session).get_by_uuid("t-1") is None
    listed = client.get(
        "/api/v1/diagnosis-records",
        params={
            "job_uuid": "j-1",
            "start_time": "2020-01-01 00:00:00",
            "end_time": "2030-01-01 00:00:00",
        },
    )
    detailed = client.get("/api/v1/diagnosis-record/r-xx")
    assert listed.status_code == detailed.status_code == 200
    assert listed.json()["items"][0]["diagnoser_definition"] == definition
    assert detailed.json()["diagnoser_definition"] == definition
    assert listed.json()["items"][0]["space_uuid"] == detailed.json()["space_uuid"] == "s"
