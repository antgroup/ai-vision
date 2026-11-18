from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI, Response
from fastapi.testclient import TestClient

from agentlens.domain.diagnosis_record import (
    DiagnosisRecord,
    DiagnosisRecordListItem,
    Execution,
    Metric,
)
from agentlens.domain.enums import ReviewStatus, TargetType, Verdict
from agentlens.infra.signoz.client import SignozQueryError
from agentlens.web.deps import get_diagnoser_table, get_record_store
from agentlens.web.errors import install_error_handlers
from agentlens.web.routes.diagnosis_record import (
    _list_out,
    _out,
    _parse_verdicts,
    list_records,
    router,
    submit_feedback,
)
from agentlens.web.schemas.feedback import FeedbackSubmit


def test_parse_multiple_verdicts():
    assert _parse_verdicts("good,failed") == ["good", "failed"]
    assert _parse_verdicts(None) is None


def _list_item(uuid: str = "r-1") -> DiagnosisRecordListItem:
    return DiagnosisRecordListItem(
        uuid=uuid,
        task_uuid="t-1",
        job_uuid="j-1",
        diagnoser_uuid="dg-1",
        diagnoser_version="v1",
        target_type=TargetType.TRACE,
        target_id=f"trace-{uuid}",
        verdict=Verdict.BAD,
        review_status=ReviewStatus.PENDING,
        summary="bad",
        reviewed_at=None,
        diagnosed_at="2026-07-19 10:00:00",
        target_created_at=None,
    )


def test_list_output_is_compact_and_serializes_iso_timestamps():
    output = _list_out(_list_item()).model_dump(mode="json")

    assert set(output) == {
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
    assert output["diagnosed_at"] == datetime(2026, 7, 19, 10).astimezone().isoformat()


def test_list_route_forwards_pagination_filters_and_sort_to_store():
    class Store:
        kwargs = None

        def query_record_list(self, **kwargs):
            self.kwargs = kwargs
            return [_list_item("r-b")], 7

    store = Store()
    response = list_records(
        store=store,
        diagnosers=SimpleNamespace(list=lambda: []),
        job_uuid=["j-1"],
        start_time="2026-07-01 00:00:00",
        end_time="2026-07-19 23:59:59",
        task_uuid="t-1",
        owner_id="owner-1",
        verdict="bad",
        review_status=ReviewStatus.PENDING,
        model=["model-a"],
        limit=25,
        offset=50,
        sort_by="target_created_at",
        sort_order="asc",
    )

    assert response.total == 7
    assert [item.uuid for item in response.items] == ["r-b"]
    assert store.kwargs == {
        "job_uuid": ["j-1"],
        "verdict": ["bad"],
        "task_uuid": "t-1",
        "owner_id": "owner-1",
        "start_time": "2026-07-01 00:00:00",
        "end_time": "2026-07-19 23:59:59",
        "review_status": "pending",
        "diagnoser_uuid": None,
        "exp_ver_id": [],
        "model": ["model-a"],
        "keyword": None,
        "limit": 25,
        "offset": 50,
        "sort_by": "target_created_at",
        "sort_order": "asc",
    }


def test_list_route_exposes_signoz_query_failure_message():
    class Store:
        def query_record_list(self, **kwargs):
            raise SignozQueryError("unknown column attributes.model")

    app = FastAPI()
    app.include_router(router)
    install_error_handlers(app)
    app.dependency_overrides[get_record_store] = Store
    app.dependency_overrides[get_diagnoser_table] = lambda: SimpleNamespace(list=lambda: [])
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/api/v1/diagnosis-records", params={"job_uuid": "j-1"})

    assert response.status_code == 503
    assert response.json() == {
        "code": "external_unavailable",
        "msg": "SigNoz query failed: unknown column attributes.model",
    }


def test_list_route_resolves_category_to_matching_diagnoser_uuids():
    class Store:
        kwargs = None

        def query_record_list(self, **kwargs):
            self.kwargs = kwargs
            return [], 0

    store = Store()
    diagnosers = SimpleNamespace(
        list=lambda: [
            SimpleNamespace(uuid="dg-safety", spec={"category": "safety"}),
            SimpleNamespace(uuid="dg-process", spec={"category": "process"}),
            SimpleNamespace(uuid="dg-no-category", spec={}),
        ]
    )

    list_records(
        store=store,
        diagnosers=diagnosers,
        job_uuid=["j-1"],
        task_uuid="t-1",
        category="safety",
    )

    assert store.kwargs["diagnoser_uuid"] == ["dg-safety"]


def test_list_route_passes_empty_diagnoser_set_for_unknown_category():
    class Store:
        kwargs = None

        def query_record_list(self, **kwargs):
            self.kwargs = kwargs
            return [], 0

    store = Store()
    list_records(
        store=store,
        diagnosers=SimpleNamespace(list=lambda: []),
        job_uuid=["j-1"],
        task_uuid="t-1",
        category="unknown",
    )

    assert store.kwargs["diagnoser_uuid"] == []


def test_list_route_normalizes_and_forwards_experiment_group_filters():
    class Store:
        kwargs = None

        def query_record_list(self, **kwargs):
            self.kwargs = kwargs
            return [], 0

    store = Store()
    list_records(
        store=store,
        diagnosers=SimpleNamespace(list=lambda: []),
        job_uuid=["j-1"],
        exp_ver_id=[" 1001 ", "1002", "1001"],
    )

    assert store.kwargs["exp_ver_id"] == ["1001", "1002"]


def test_list_route_rejects_non_numeric_experiment_group_filter():
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_record_store] = lambda: SimpleNamespace()
    app.dependency_overrides[get_diagnoser_table] = lambda: SimpleNamespace(list=lambda: [])
    client = TestClient(app)

    response = client.get(
        "/api/v1/diagnosis-records",
        params={"job_uuid": "j-1", "exp_ver_id": "10%_2"},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "exp_ver_id must contain digits only"


def test_list_route_http_contract_validates_and_serializes_without_detail_body():
    class Store:
        def query_record_list(self, **kwargs):
            return [_list_item()], 1

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_record_store] = Store
    app.dependency_overrides[get_diagnoser_table] = lambda: SimpleNamespace(list=lambda: [])
    client = TestClient(app)

    response = client.get(
        "/api/v1/diagnosis-records",
        params={
            "job_uuid": "j-1",
            "space_uuid": "s",
            "agent_uuid": "a",
            "verdict": "bad",
            "review_status": "pending",
            "limit": 25,
            "offset": 0,
            "sort_by": "diagnosed_at",
            "sort_order": "desc",
        },
    )

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert (
        response.json()["items"][0]["diagnosed_at"]
        == datetime(2026, 7, 19, 10).astimezone().isoformat()
    )
    assert "metrics" not in response.json()["items"][0]
    assert "execution" not in response.json()["items"][0]

    assert (
        client.get(
            "/api/v1/diagnosis-records",
            params={
                "job_uuid": "j-1",
                "space_uuid": "s",
                "agent_uuid": "a",
                "sort_by": "unsupported",
            },
        ).status_code
        == 422
    )


def test_record_routes_do_not_declare_scope_query_parameters():
    app = FastAPI()
    app.include_router(router)
    paths = app.openapi()["paths"]

    for path, method in (
        ("/api/v1/diagnosis-records", "get"),
        ("/api/v1/diagnosis-record/{uuid}", "get"),
        ("/api/v1/diagnosis-record/{uuid}/feedback", "post"),
    ):
        parameter_names = {parameter["name"] for parameter in paths[path][method]["parameters"]}
        assert "space_uuid" not in parameter_names
        assert "agent_uuid" not in parameter_names


def test_list_route_accepts_repeated_job_uuid_and_queries_one_global_page():
    class Store:
        kwargs = None

        def query_record_list(self, **kwargs):
            self.kwargs = kwargs
            return [_list_item()], 3

    store = Store()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_record_store] = lambda: store
    app.dependency_overrides[get_diagnoser_table] = lambda: SimpleNamespace(list=lambda: [])
    client = TestClient(app)

    response = client.get(
        "/api/v1/diagnosis-records",
        params=[
            ("job_uuid", "j-1"),
            ("job_uuid", "j-2"),
            ("space_uuid", "s"),
            ("agent_uuid", "a"),
            ("limit", "2"),
            ("offset", "0"),
        ],
    )

    assert response.status_code == 200
    assert response.json()["total"] == 3
    assert store.kwargs["job_uuid"] == ["j-1", "j-2"]
    assert store.kwargs["limit"] == 2


def test_submit_feedback_preserves_record_attempt():
    record = SimpleNamespace(
        job_uuid="j-1",
        review_status=ReviewStatus.PENDING,
        feedback=None,
        reviewed_by=None,
        reviewed_at=None,
        _attempt=3,
    )

    class Store:
        written_attempt = None

        def get_record(self, uuid):
            return record

        def write_record_and_wait_visible(self, updated, *, attempt=0):
            self.written_attempt = attempt
            return updated

    store = Store()
    with patch("agentlens.web.routes.diagnosis_record._out", return_value=object()):
        submit_feedback(
            "r-1",
            FeedbackSubmit(
                review_status=ReviewStatus.CONFIRMED,
                feedback={"remark": "confirmed by reviewer"},
                reviewed_by="alice",
            ),
            Response(),
            store,
        )

    assert store.written_attempt == 3
    assert record.review_status == ReviewStatus.CONFIRMED
    assert record.feedback == {"remark": "confirmed by reviewer"}


def test_submit_feedback_returns_accepted_when_written_version_is_not_visible():
    record = SimpleNamespace(
        job_uuid="j-1",
        review_status=ReviewStatus.PENDING,
        feedback=None,
        reviewed_by=None,
        reviewed_at=None,
        _attempt=1,
    )

    class Store:
        def get_record(self, uuid):
            return record

        def write_record_and_wait_visible(self, updated, *, attempt=0):
            return None

    response = Response()
    accepted = object()
    with patch("agentlens.web.routes.diagnosis_record._out", return_value=accepted):
        result = submit_feedback(
            "r-1",
            FeedbackSubmit(
                review_status=ReviewStatus.CONFIRMED,
                reviewed_by="alice",
            ),
            response,
            Store(),
        )

    assert response.status_code == 202
    assert result is accepted
    assert record.review_status == ReviewStatus.CONFIRMED
    assert record.reviewed_by == "alice"


def _seed_record(
    signoz_inmem,
    uuid="r-1",
    verdict=Verdict.BAD,
    job_uuid="j-1",
    metrics=("tool_correctness",),
    attempt=0,
):
    from agentlens.infra.antcollector import InMemoryAntcollectorClient
    from agentlens.service.diagnosis_record_store import DiagnosisRecordStore
    from agentlens.web import deps

    settings = deps.get_settings()
    s = DiagnosisRecordStore(
        signoz_inmem,
        InMemoryAntcollectorClient(signoz_inmem._tables),
        table=settings.diagnosis_record_table,
    )
    now = datetime.now()
    s.write_record(
        DiagnosisRecord(
            uuid=uuid,
            task_uuid="t-1",
            job_uuid=job_uuid,
            diagnoser_uuid="dg-1",
            diagnoser_version="v1",
            target_type=TargetType.TRACE,
            target_id="trace-x",
            metrics=[
                Metric(
                    name=metric,
                    verdict=verdict,
                    reason=(
                        "not applicable"
                        if verdict == Verdict.GOOD
                        else "executor failed"
                        if verdict == Verdict.FAILED
                        else "bad metric"
                    ),
                )
                for metric in metrics
            ],
            execution=(
                Execution.failed("test_failure", "executor failed")
                if verdict == Verdict.FAILED
                else Execution.succeeded()
            ),
            trace_snapshot={},
            review_status=ReviewStatus.PENDING,
            feedback=None,
            reviewed_by=None,
            reviewed_at=None,
            diagnosed_at=now,
            target_created_at="",
        ),
        attempt=attempt,
    )


def test_list_records(client, signoz_inmem):
    _seed_record(signoz_inmem, uuid="r-bad", verdict=Verdict.BAD)
    _seed_record(signoz_inmem, uuid="r-good", verdict=Verdict.GOOD)
    r = client.get(
        "/api/v1/diagnosis-records",
        params={
            "job_uuid": "j-1",
            "space_uuid": "s",
            "agent_uuid": "a",
            "start_time": "2020-01-01 00:00:00",
            "end_time": "2030-01-01 00:00:00",
            "verdict": "bad",
        },
    )
    body = r.json()
    assert {it["uuid"] for it in body["items"]} == {"r-bad"}


def test_list_records_without_verdict_returns_all_cases(client, signoz_inmem):
    for verdict in Verdict:
        _seed_record(signoz_inmem, uuid=f"r-{verdict.value}", verdict=verdict)
    r = client.get(
        "/api/v1/diagnosis-records",
        params={
            "job_uuid": "j-1",
            "space_uuid": "s",
            "agent_uuid": "a",
            "start_time": "2020-01-01 00:00:00",
            "end_time": "2030-01-01 00:00:00",
        },
    )
    assert {it["verdict"] for it in r.json()["items"]} == {
        "good",
        "bad",
        "failed",
    }


def test_list_records_default_time(client, signoz_inmem):
    _seed_record(signoz_inmem, uuid="r-recent", verdict=Verdict.BAD)
    r = client.get(
        "/api/v1/diagnosis-records",
        params={"job_uuid": "j-1", "space_uuid": "s", "agent_uuid": "a"},
    )
    assert r.status_code == 200


def test_get_record_returns_only_current_record_metrics(client, signoz_inmem):
    _seed_record(signoz_inmem, uuid="r-x", metrics=("tool_success_rate",))
    _seed_record(signoz_inmem, uuid="r-sibling", metrics=("stuck_loop_health",))

    r = client.get("/api/v1/diagnosis-record/r-x")

    assert r.status_code == 200
    assert r.json()["uuid"] == "r-x"
    assert [metric["name"] for metric in r.json()["metrics"]] == ["tool_success_rate"]
    assert "trace_results" not in r.json()


def test_record_output_includes_metrics_and_execution():
    record = DiagnosisRecord(
        uuid="r-results",
        task_uuid="t-1",
        job_uuid="j-1",
        diagnoser_uuid="dg-1",
        diagnoser_version="v1",
        target_type=TargetType.TRACE,
        target_id="trace-x",
        metrics=[
            Metric(
                name="quality",
                verdict=Verdict.GOOD,
                reason="not applicable",
            )
        ],
        execution=Execution.succeeded({"duration_ms": 12}),
        trace_snapshot={},
        review_status=ReviewStatus.PENDING,
        feedback={"remark": "review note"},
        reviewed_by=None,
        reviewed_at=None,
        diagnosed_at=datetime.now(),
        target_created_at="",
    )
    output = _out(record).model_dump(mode="json")

    assert output["metrics"] == [
        {
            "name": "quality",
            "verdict": "good",
            "reason": "not applicable",
            "evidence": {},
        }
    ]
    assert output["execution"] == {
        "status": "succeeded",
        "code": "",
        "msg": "",
        "evidence": {"duration_ms": 12},
    }
    assert output["feedback"] == {"remark": "review note"}


def test_get_record_404(client):
    assert client.get("/api/v1/diagnosis-record/r-nope").status_code == 404


def test_post_feedback(client, signoz_inmem):
    _seed_record(signoz_inmem, uuid="r-1", attempt=2)
    r = client.post(
        "/api/v1/diagnosis-record/r-1/feedback",
        json={
            "review_status": "cannotJudge",
            "feedback": {"remark": "missing business context"},
            "reviewed_by": "alice",
        },
    )
    assert r.status_code == 200
    assert r.json()["review_status"] == "cannotJudge"
    assert r.json()["reviewed_by"] == "alice"

    fetched = client.get("/api/v1/diagnosis-record/r-1")
    assert fetched.status_code == 200
    assert fetched.json()["review_status"] == "cannotJudge"
    assert fetched.json()["feedback"] == {"remark": "missing business context"}
