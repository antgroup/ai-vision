"""Diagnosis responses identify local timestamps without changing stored values."""

import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentlens.web.schemas.common import ListResponse
from agentlens.web.schemas.diagnosis_record import (
    DiagnosisRecordListItemOut,
    DiagnosisRecordOut,
)
from agentlens.web.schemas.diagnosis_stats import DiagnosisTaskStats
from agentlens.web.schemas.job import JobOut
from agentlens.web.schemas.task import TaskOut


def _responses(timestamp):
    record = {
        "uuid": "record-1",
        "task_uuid": "task-1",
        "job_uuid": "job-1",
        "diagnoser_uuid": "diagnoser-1",
        "diagnoser_version": "v1",
        "target_type": "trace",
        "target_id": "trace-1",
        "owner_id": None,
        "exp_ver_id": "",
        "model": "model-1",
        "verdict": "bad",
        "review_status": "pending",
        "summary": "Requires verification",
        "reviewed_at": timestamp,
        "diagnosed_at": timestamp,
        "target_created_at": timestamp,
    }
    return [
        DiagnosisRecordListItemOut(**record),
        DiagnosisRecordOut(
            **record,
            execution={"status": "succeeded", "code": "", "msg": "", "evidence": {}},
            trace_snapshot={},
            feedback=None,
            reviewed_by=None,
            metrics=[],
        ),
        TaskOut(
            uuid="task-1",
            job_uuid="job-1",
            status="succeeded",
            worker="worker-1",
            scheduled_at=timestamp,
            started_at=timestamp,
            finished_at=timestamp,
            attempt=1,
            msg="",
            gmt_create=timestamp,
            gmt_modified=timestamp,
        ),
        JobOut(
            uuid="job-1",
            space_uuid="space-1",
            agent_uuid="agent-1",
            name="Diagnosis",
            description="",
            trigger_type="manual",
            schedule_cron=None,
            status="enabled",
            dataset_uuid="dataset-1",
            diagnoser_uuids=[],
            spec={},
            gmt_create=timestamp,
            gmt_modified=timestamp,
            creator=None,
            modifier=None,
        ),
        DiagnosisTaskStats(
            task_uuid="task-1",
            time=timestamp,
            target_count=1,
            **{"global": {"score": None, "categories": []}},
        ),
    ]


@pytest.fixture(params=[("UTC", "+00:00"), ("Asia/Shanghai", "+08:00")])
def server_timezone(request, monkeypatch):
    monkeypatch.setenv("TZ", request.param[0])
    time.tzset()
    try:
        yield request.param[1]
    finally:
        monkeypatch.undo()
        time.tzset()


def _http_body(model):
    app = FastAPI()
    app.add_api_route("/response", lambda: model, response_model=type(model))
    response = TestClient(app).get("/response")
    assert response.status_code == 200
    return response.json()


def test_diagnosis_http_datetimes_include_the_servers_local_offset(server_timezone):
    # This is the real failing UTC-container timestamp, not a replacement clock.
    timestamp = datetime(2026, 10, 8, 13, 30, 38, 416525)
    expected = f"2026-10-08T13:30:38.416525{server_timezone}"
    for model in _responses(timestamp):
        stored = model.model_dump(mode="python")
        fields = [name for name, value in stored.items() if isinstance(value, datetime)]
        assert fields
        body = _http_body(model)
        for name in fields:
            assert body[name] == expected, (type(model).__name__, name, body[name])
            assert model.model_dump(mode="python")[name] == timestamp
            assert model.model_dump(mode="python")[name].tzinfo is None

    page = ListResponse[DiagnosisRecordListItemOut](items=[_responses(timestamp)[0]], total=1)
    assert _http_body(page)["items"][0]["diagnosed_at"] == expected


def test_existing_aware_offsets_are_preserved_and_nulls_stay_null(server_timezone):
    timestamp = datetime(2026, 10, 8, 9, 30, tzinfo=timezone(timedelta(hours=-4)))
    for model in _responses(timestamp):
        stored = model.model_dump(mode="python")
        body = _http_body(model)
        for name, value in stored.items():
            if isinstance(value, datetime):
                assert body[name] == "2026-10-08T09:30:00-04:00"
                assert model.model_dump(mode="python")[name] == timestamp

    record = _responses(timestamp)[0].model_copy(update={"reviewed_at": None})
    assert _http_body(record)["reviewed_at"] is None
