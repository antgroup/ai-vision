"""Validate through the real projector, Core executor, Judge and citation checks."""

import json
from copy import deepcopy
from dataclasses import replace
from datetime import datetime

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentlens.domain.enums import ModelConnectionStatus
from agentlens.domain.model_connection import ModelConnection
from agentlens.web.deps import get_model_connection_table
from agentlens.web.routes import model_connection

KEY = "inert-validation-secret"


class MemoryTable:
    def __init__(self):
        self.row = ModelConnection(
            "m-test",
            "space",
            "test",
            "https://judge.test/v1",
            "judge",
            KEY,
            ModelConnectionStatus.UNTESTED,
            datetime.now(),
            datetime.now(),
            None,
            None,
        )
        self.row.config_revision = 1
        self.row.diagnostic_validation = None
        self.conflict = False

    def get_in_space(self, uuid, space_uuid):
        return (
            deepcopy(self.row)
            if (uuid, space_uuid) == (self.row.uuid, self.row.space_uuid)
            else None
        )

    def validation_snapshot(self, uuid, space_uuid):
        return self.get_in_space(uuid, space_uuid)

    def get_by_uuid(self, uuid):
        return deepcopy(self.row)

    def save_validation(self, snapshot, report, status):
        if self.conflict:
            return False
        self.row.diagnostic_validation = deepcopy(report)
        self.row.status = status
        return True


@pytest.fixture
def validation_client(monkeypatch):
    table = MemoryTable()
    app = FastAPI()
    app.include_router(model_connection.router)
    from agentlens.web.errors import install_error_handlers

    install_error_handlers(app)
    app.dependency_overrides[get_model_connection_table] = lambda: table
    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        positive = len(requests) == 1
        content = {
            "metrics": [
                {
                    "name": "task_completion",
                    "verdict": "good" if positive else "bad",
                    "reason": "synthetic response",
                    "evidence": {
                        "citations": [
                            {"source_id": "request", "quote": "Return exactly READY."},
                            {"source_id": "final", "quote": "READY" if positive else "BUSY"},
                        ]
                    },
                }
            ]
        }
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps(content)}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
            request=request,
        )

    monkeypatch.setattr(
        httpx.HTTPTransport, "handle_request", lambda self, request: respond(request)
    )
    return TestClient(app), table, requests


def test_validation_runs_both_real_core_paths_and_persists_safe_result(validation_client):
    client, table, requests = validation_client
    response = client.post("/api/v1/model-connection/m-test/validate", json={"space_uuid": "space"})
    assert response.status_code == 200, response.text
    result = response.json()
    report = result["diagnostic_validation"]
    assert result["status"] == "available"
    assert report["status"] == "passed"
    assert [case["actual"] for case in report["cases"]] == ["good", "bad"]
    assert report["usage"] == {
        "requests": 2,
        "input_tokens": 20,
        "output_tokens": 10,
        "total_tokens": None,
    }
    assert len(requests) == 2
    assert all(body["max_tokens"] == 4096 and "ping" not in str(body) for body in requests)
    assert all("evidence_bundle" in str(body) and "citations" in str(body) for body in requests)
    assert report == table.row.diagnostic_validation
    assert client.get("/api/v1/model-connection/m-test").json()["diagnostic_validation"] == report
    assert KEY not in response.text and "synthetic response" not in response.text


@pytest.mark.parametrize(
    "api_key",
    [
        "inert-中文",
        "inert-‘key’",
        "inert-\nkey",
        "inert-\x00key",
        "inert-\x7fkey",
        "inert key",
        "",
        "x" * 8193,
    ],
    ids=["unicode", "smart-quotes", "newline", "nul", "del", "space", "empty", "oversized"],
)
def test_invalid_saved_key_persists_safe_report_without_provider_dispatch(
    validation_client, api_key
):
    client, table, requests = validation_client
    table.row.api_key = api_key
    client = TestClient(client.app, raise_server_exceptions=False)
    response = client.post("/api/v1/model-connection/m-test/validate", json={"space_uuid": "space"})
    assert response.status_code == 200, response.text
    result = response.json()
    report = result["diagnostic_validation"]
    assert result["status"] == "unavailable"
    assert report["status"] == "incomplete"
    assert report["reason"] == "invalid_request"
    assert report["cases"] == [
        {
            "name": "positive",
            "status": "incomplete",
            "reason": "invalid_request",
            "expected": "good",
            "actual": None,
        },
        {
            "name": "negative",
            "status": "not_run",
            "reason": "invalid_request",
            "expected": "bad",
            "actual": None,
        },
    ]
    assert report["usage"] == {
        "requests": 0,
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
    }
    assert requests == []
    assert "api_key" not in result
    if api_key:
        assert api_key not in json.dumps(report, ensure_ascii=False)
    assert report == table.row.diagnostic_validation
    assert client.get("/api/v1/model-connection/m-test").json()["diagnostic_validation"] == report


@pytest.mark.parametrize("api_key", ["inert-key_+/=.!~", "k" * 8192], ids=["ascii", "max-length"])
def test_valid_saved_key_still_dispatches_both_diagnostic_cases(validation_client, api_key):
    client, table, requests = validation_client
    table.row.api_key = api_key
    response = client.post("/api/v1/model-connection/m-test/validate", json={"space_uuid": "space"})
    assert response.status_code == 200, response.text
    assert response.json()["diagnostic_validation"]["status"] == "passed"
    assert response.json()["diagnostic_validation"]["usage"]["requests"] == 2
    assert len(requests) == 2


@pytest.mark.parametrize(
    "options",
    [
        {"max_tokens": 1},
        {"temperature": True},
        {"request_timeout_sec": 61},
        {"reasoning_effort": "secret"},
        {"unknown": "secret"},
    ],
)
def test_validation_rejects_unsafe_or_unbounded_options_before_request(validation_client, options):
    client, _, requests = validation_client
    response = client.post(
        "/api/v1/model-connection/m-test/validate",
        json={"space_uuid": "space", "generation_options": options},
    )
    assert response.status_code == 422
    assert not requests


def test_validation_other_space_cannot_invoke_saved_key(validation_client):
    client, _, requests = validation_client
    response = client.post("/api/v1/model-connection/m-test/validate", json={"space_uuid": "other"})
    assert response.status_code == 404
    assert not requests


def test_validation_conflict_cannot_persist_old_result(validation_client):
    client, table, _ = validation_client
    table.conflict = True
    response = client.post("/api/v1/model-connection/m-test/validate", json={"space_uuid": "space"})
    assert response.status_code == 409
    assert table.row.diagnostic_validation is None


@pytest.mark.parametrize(
    ("status", "body", "reason", "expected_status"),
    [
        (401, {"error": {"message": KEY}}, "authentication_failed", "unavailable"),
        (403, {}, "permission_denied", "unavailable"),
        (404, {}, "model_not_found", "unavailable"),
        (
            429,
            {"error": {"code": "insufficient_quota", "message": KEY}},
            "quota_exceeded",
            "unavailable",
        ),
        (429, {}, "rate_limited", "unavailable"),
        (200, {"error": {"code": "insufficient_quota"}}, "quota_exceeded", "unavailable"),
        (
            200,
            {"choices": [{"finish_reason": "length", "message": {"content": KEY}}]},
            "output_limit_reached",
            "available",
        ),
        (
            200,
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "metrics": [
                                        {
                                            "name": "task_completion",
                                            "verdict": "good",
                                            "reason": KEY,
                                        }
                                    ]
                                }
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 7, "completion_tokens": 9},
            },
            "invalid_diagnostic_output",
            "available",
        ),
    ],
)
def test_validation_classifies_failure_and_retains_usage(
    validation_client, monkeypatch, status, body, reason, expected_status
):
    client, table, _ = validation_client
    calls = []

    def handler(self, request):
        calls.append(request)
        return httpx.Response(status, json=body, request=request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", handler)
    response = client.post("/api/v1/model-connection/m-test/validate", json={"space_uuid": "space"})
    assert response.status_code == 200
    result = response.json()
    report = result["diagnostic_validation"]
    assert result["status"] == expected_status
    assert report["reason"] == reason
    assert report["status"] == (
        "failed"
        if expected_status == "available" and reason != "output_limit_reached"
        else "incomplete"
    )
    assert len(calls) == (
        2 if expected_status == "available" and reason != "output_limit_reached" else 1
    )
    assert report["usage"]["requests"] == len(calls)
    if "usage" in body:
        assert report["usage"]["input_tokens"] == 14
        assert report["usage"]["output_tokens"] == 18
    assert KEY not in response.text and KEY not in json.dumps(table.row.diagnostic_validation)


@pytest.mark.parametrize(
    ("exception", "reason"),
    [
        (httpx.ReadTimeout, "timeout"),
        (httpx.ConnectError, "connection_failed"),
        (httpx.UnsupportedProtocol, "invalid_request"),
    ],
)
def test_validation_request_error_no_retry_or_raw_exception(
    validation_client, monkeypatch, exception, reason
):
    client, _, _ = validation_client
    calls = []

    def handler(self, request):
        calls.append(request)
        raise exception(KEY, request=request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", handler)
    response = client.post("/api/v1/model-connection/m-test/validate", json={"space_uuid": "space"})
    assert response.status_code == 200
    report = response.json()["diagnostic_validation"]
    assert report["status"] == "incomplete" and report["reason"] == reason
    assert report["usage"] == {
        "requests": 1,
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
    }
    assert len(calls) == 1 and KEY not in response.text


def test_missing_evidence_is_offline_and_never_calls_provider(validation_client, monkeypatch):
    from agentlens.service import model_validation

    client, _, requests = validation_client
    original = model_validation._case_trace

    def missing(name):
        trace = original(name)
        spans = deepcopy(trace.spans)
        spans[0].pop("attributes.aivision.request.output")
        from agentlens.service.datasource.projection.service import project_trace

        return project_trace(replace(trace, spans=spans, projection=None))

    monkeypatch.setattr(model_validation, "_case_trace", missing)
    report = client.post(
        "/api/v1/model-connection/m-test/validate", json={"space_uuid": "space"}
    ).json()["diagnostic_validation"]
    assert report["reason"] == "missing_required_evidence"
    assert report["usage"]["requests"] == 0 and not requests


@pytest.mark.parametrize(
    "mutation", ["extra", "stale", "wrong_expected", "fake_pass", "missing_option"]
)
def test_get_rejects_unsafe_or_stale_saved_report(validation_client, mutation):
    client, table, _ = validation_client
    client.post("/api/v1/model-connection/m-test/validate", json={"space_uuid": "space"})
    report = table.row.diagnostic_validation
    if mutation == "extra":
        report["raw_body"] = KEY
    elif mutation == "stale":
        report["profile"]["definition_digest"] = "0" * 64
    elif mutation == "wrong_expected":
        report["cases"][1]["expected"] = "good"
    elif mutation == "missing_option":
        report["profile"]["generation_options"].pop("request_timeout_sec")
    else:
        report["usage"]["requests"] = 0
    response = client.get("/api/v1/model-connection/m-test")
    assert response.json()["diagnostic_validation"] is None
    assert KEY not in response.text


def test_validation_bounds_response_size(validation_client, monkeypatch):
    client, _, _ = validation_client
    monkeypatch.setattr(
        httpx.HTTPTransport,
        "handle_request",
        lambda self, request: httpx.Response(200, content=b"x" * (512 * 1024), request=request),
    )
    response = client.post("/api/v1/model-connection/m-test/validate", json={"space_uuid": "space"})
    assert response.json()["diagnostic_validation"]["reason"] == "output_limit_reached"


def test_official_anthropic_report_records_only_effective_options(validation_client):
    client, table, requests = validation_client
    table.row.endpoint = "https://api.anthropic.com/v1"
    report = client.post(
        "/api/v1/model-connection/m-test/validate",
        json={
            "space_uuid": "space",
            "generation_options": {
                "temperature": 0.5,
                "enable_thinking": True,
                "reasoning_effort": "high",
            },
        },
    ).json()["diagnostic_validation"]
    for key in ("temperature", "enable_thinking", "reasoning_effort"):
        assert report["profile"]["generation_options"][key] is None
        assert key not in requests[0]


def test_deeply_nested_provider_json_is_safe_failure(validation_client, monkeypatch):
    client, table, _ = validation_client
    monkeypatch.setattr(
        httpx.HTTPTransport,
        "handle_request",
        lambda self, request: httpx.Response(
            200, content=b"[" * 20000 + b"0" + b"]" * 20000, request=request
        ),
    )
    response = client.post("/api/v1/model-connection/m-test/validate", json={"space_uuid": "space"})
    assert response.status_code == 200
    assert response.json()["status"] == "available"
    assert response.json()["diagnostic_validation"]["reason"] == "invalid_diagnostic_output"
    assert table.row.diagnostic_validation is not None


def test_large_explicit_error_envelope_never_claims_connected(validation_client, monkeypatch):
    client, _, _ = validation_client
    calls = []

    def handler(self, request):
        calls.append(request)
        return httpx.Response(
            200,
            json={"error": {"code": "insufficient_quota", "message": "x" * 70000}},
            request=request,
        )

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", handler)
    response = client.post("/api/v1/model-connection/m-test/validate", json={"space_uuid": "space"})
    assert response.json()["status"] == "unavailable"
    assert response.json()["diagnostic_validation"]["reason"] == "quota_exceeded"
    assert len(calls) == 1


def test_negative_case_must_not_pass_with_positive_verdict(validation_client, monkeypatch):
    client, _, _ = validation_client
    original = httpx.HTTPTransport.handle_request

    def handler(self, request):
        response = original(self, request)
        body = response.json()
        content = json.loads(body["choices"][0]["message"]["content"])
        content["metrics"][0]["verdict"] = "good"
        body["choices"][0]["message"]["content"] = json.dumps(content)
        return httpx.Response(200, json=body, request=request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", handler)
    response = client.post("/api/v1/model-connection/m-test/validate", json={"space_uuid": "space"})
    report = response.json()["diagnostic_validation"]
    assert report["status"] == "failed" and report["reason"] == "unexpected_verdict"
    assert report["cases"][0]["status"] == "passed"
    assert report["cases"][1]["actual"] == "good"
    assert report["usage"]["requests"] == 2


def test_elapsed_dispatch_budget_does_not_send_second_case(validation_client, monkeypatch):
    from agentlens.service import model_validation

    client, _, requests = validation_client
    clock = iter([0, 0, 91])
    monkeypatch.setattr(model_validation, "monotonic", lambda: next(clock))
    response = client.post("/api/v1/model-connection/m-test/validate", json={"space_uuid": "space"})
    report = response.json()["diagnostic_validation"]
    assert report["status"] == "incomplete" and report["reason"] == "budget_exhausted"
    assert report["cases"][1]["status"] == "not_run"
    assert report["usage"]["requests"] == 1 and len(requests) == 1
    assert response.json()["status"] == "available"


def test_second_case_transport_error_keeps_connection_proof_and_unknown_usage(
    validation_client, monkeypatch
):
    client, _, requests = validation_client
    original = httpx.HTTPTransport.handle_request

    def handler(self, request):
        if requests:
            raise httpx.ReadTimeout(KEY, request=request)
        return original(self, request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", handler)
    response = client.post("/api/v1/model-connection/m-test/validate", json={"space_uuid": "space"})
    report = response.json()["diagnostic_validation"]
    assert response.json()["status"] == "available"
    assert report["status"] == "incomplete" and report["reason"] == "timeout"
    assert report["cases"][0]["status"] == "passed"
    assert report["usage"] == {
        "requests": 2,
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
    }
