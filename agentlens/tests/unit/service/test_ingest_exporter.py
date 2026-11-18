from contextlib import nullcontext
from types import SimpleNamespace

import httpx
import pytest
from opentelemetry.sdk.trace.export import SpanExportResult

from agentlens.service import ingest_exporter as module


@pytest.fixture
def env(monkeypatch):
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    jobs = {
        "j-a": SimpleNamespace(space_uuid="s-a", agent_uuid="a-a"),
        "j-b": SimpleNamespace(space_uuid="s-b", agent_uuid="a-b"),
    }
    monkeypatch.setattr(module, "JobTable", lambda _: SimpleNamespace(get_by_uuid=jobs.get))
    issued, exported = [], []
    now = [0.0]
    monkeypatch.setattr(module, "monotonic", lambda: now[0])

    def post(url, *, json, headers, timeout, follow_redirects):
        assert headers == {"x-antmonitor-token": "service-secret"}
        assert follow_redirects is False
        issued.append(json)
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "code": 0,
                "data": {
                    "space_id": json["space_uuid"],
                    "agent_uuid": json["agent_uuid"],
                    "token": "token-" + json["space_uuid"],
                    "token_type": "Bearer",
                    "expires_in": 300,
                },
            },
        )

    class Exporter:
        fail = False

        def __init__(self, *, endpoint, headers, timeout):
            self.headers = headers

        def export(self, spans):
            exported.append((self.headers, list(spans)))
            return SpanExportResult.FAILURE if self.fail else SpanExportResult.SUCCESS

        def shutdown(self):
            pass

    monkeypatch.setattr(module.httpx, "post", post)
    monkeypatch.setattr(module, "OTLPSpanExporter", Exporter)
    client = module.ScopedIngestExporter(
        endpoint="http://collector/v1/traces",
        token_url="http://platform/ingest-tokens",
        service_token="service-secret",
        session_factory=lambda: nullcontext(None),
    )
    return SimpleNamespace(
        client=client, issued=issued, exported=exported, now=now, exporter=Exporter
    )


def span(job="j-a", space="s-a", name="diagnosis_record"):
    return SimpleNamespace(name=name, attributes={"job_uuid": job, "space_id": space})


def test_mixed_scope_records_and_stats_are_exported_with_separate_tokens(env):
    a, b, stats = span(), span("j-b", "s-b"), span(name="task_stats")
    assert env.client.export([a, b, stats]) is SpanExportResult.SUCCESS
    assert env.exported == [
        ({"Authorization": "Bearer token-s-a"}, [a, stats]),
        ({"Authorization": "Bearer token-s-b"}, [b]),
    ]


def test_token_is_reused_then_renewed_before_expiry(env):
    env.client.export([span()])
    env.now[0] = 239
    env.client.export([span()])
    assert len(env.issued) == 1
    env.now[0] = 241
    env.client.export([span()])
    assert len(env.issued) == 2


@pytest.mark.parametrize(
    "bad_span", [span("missing"), span(space="s-b"), SimpleNamespace(attributes={})]
)
def test_missing_job_or_spoofed_scope_never_reaches_gateway(env, bad_span):
    assert env.client.export([bad_span]) is SpanExportResult.FAILURE
    assert env.issued == []
    assert env.exported == []
    assert env.client.force_flush() is False


def test_bad_token_response_does_not_send_any_telemetry(env, monkeypatch, capsys):
    def post(url, **_kwargs):
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "code": 0,
                "data": {"token": "SECRET-MUST-NOT-LOG", "space_id": "wrong"},
            },
        )

    monkeypatch.setattr(module.httpx, "post", post)
    assert env.client.export([span()]) is SpanExportResult.FAILURE
    assert env.exported == []
    assert "SECRET-MUST-NOT-LOG" not in capsys.readouterr().out


def test_failure_is_reported_at_flush_and_credentials_renew_on_next_export(env):
    env.exporter.fail = True
    assert env.client.export([span()]) is SpanExportResult.FAILURE
    assert env.client.force_flush() is False
    env.exporter.fail = False
    assert env.client.export([span()]) is SpanExportResult.SUCCESS
    assert env.client.force_flush() is True
    assert len(env.issued) == 2


def test_legacy_mode_does_not_construct_scoped_exporter():
    assert module.make_ingest_exporter(SimpleNamespace(aivision_ingest_token_url=""), None) is None


def test_sdk_flush_reports_gateway_failure_instead_of_queue_completion(env):
    from agentlens.infra.antcollector.client import HttpAntcollectorClient

    client = HttpAntcollectorClient("http://collector/v1/traces", span_exporter=env.client)
    try:
        env.exporter.fail = True
        client.write_span("traces", "diagnosis_record", span().attributes, {})
        assert client.force_flush() is False
        env.exporter.fail = False
        client.write_span("traces", "task_stats", span().attributes, {})
        assert client.force_flush() is True
        assert len(env.issued) == 2
    finally:
        client.close()
