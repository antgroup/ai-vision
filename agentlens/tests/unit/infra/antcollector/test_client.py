"""Dry-run logging must never leak sensitive DiagnosisRecord
content (Trace snapshot, rendered Prompt, evidence, feedback) — only an
explicit metadata allowlist (record/task/job/diagnoser/target/attempt/
verdict identity).

agentlens.logging uses structlog with a stdout tee, not the stdlib
logging module, so capsys (not caplog) captures the emitted lines.
"""

from datetime import datetime

from agentlens.infra.antcollector.client import HttpAntcollectorClient

_SENTINEL_PROMPT = "SENTINEL-RENDERED-PROMPT-DO-NOT-LOG-abc123"
_SENTINEL_TRACE_TEXT = "SENTINEL-TRACE-SNAPSHOT-CONTENT-xyz789"
_SENTINEL_FEEDBACK_TEXT = "SENTINEL-FEEDBACK-REMARK-qwe456"
_SENTINEL_EVIDENCE_TEXT = "SENTINEL-EVIDENCE-REASON-rty000"


def test_forward_service_header_is_passed_to_otlp_exporter(monkeypatch):
    import agentlens.infra.antcollector.client as client_module

    captured = {}

    class _Exporter:
        def __init__(self, *, endpoint, headers):
            captured.update(endpoint=endpoint, headers=headers)

    class _Provider:
        def __init__(self, **_kwargs):
            pass

        def add_span_processor(self, _processor):
            pass

        def get_tracer(self, _name):
            return object()

    monkeypatch.setattr(client_module, "OTLPSpanExporter", _Exporter)
    monkeypatch.setattr(client_module, "TracerProvider", _Provider)
    monkeypatch.setattr(client_module, "BatchSpanProcessor", lambda exporter: exporter)

    HttpAntcollectorClient(
        "https://collector.example.com/otlp",
        access_id="aid",
        access_token="atok",
        forward_service="trace-ingest-fixture",
    )

    assert captured == {
        "endpoint": "https://collector.example.com/otlp",
        "headers": {
            "X-Access-Id": "aid",
            "X-Access-Token": "atok",
            "x-antmonitor-forward-service": "trace-ingest-fixture",
        },
    }


def _record_with_sensitive_content() -> dict:
    return {
        "uuid": "r-abc12345",
        "task_uuid": "t-def67890",
        "job_uuid": "j-ghi11111",
        "diagnoser_uuid": "dg-jkl2222",
        "target_type": "trace",
        "target_id": "trace-xyz",
        "attempt": 2,
        "verdict": "bad",
        "trace_snapshot": {"question": _SENTINEL_TRACE_TEXT},
        "feedback": {"remark": _SENTINEL_FEEDBACK_TEXT},
        "execution": {
            "status": "succeeded",
            "code": "",
            "msg": "",
            "evidence": {"agent_run": {"rendered_prompt": _SENTINEL_PROMPT}},
        },
        "metrics": [
            {
                "name": "quality",
                "verdict": "bad",
                "reason": _SENTINEL_EVIDENCE_TEXT,
                "evidence": {},
            },
        ],
    }


class _CapturingSpan:
    def __init__(self):
        self.body = None
        self.end_time = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def set_attribute(self, key, value):
        if key == "record.body":
            self.body = value

    def end(self, *, end_time=None):
        self.end_time = end_time


class _CapturingTracer:
    def __init__(self):
        self.calls = []
        self.explicit_calls = []

    def start_as_current_span(self, *, name, attributes):
        span = _CapturingSpan()
        self.calls.append((name, attributes, span))
        return span

    def start_span(self, *, name, attributes, start_time=None):
        span = _CapturingSpan()
        self.explicit_calls.append((name, attributes, start_time, span))
        return span


class _NoopLog:
    def info(self, *args, **kwargs):
        pass

    def debug(self, *args, **kwargs):
        pass


class _CapturingLog:
    def __init__(self):
        self.calls = []

    def debug(self, *args, **kwargs):
        self.calls.append((args, kwargs))


def test_http_write_promotes_compact_attributes_separately_from_detail_body():
    client = HttpAntcollectorClient.__new__(HttpAntcollectorClient)
    client.otlp_endpoint = "http://collector"
    client._tracer = _CapturingTracer()
    client._log = _NoopLog()
    record = {
        **_record_with_sensitive_content(),
        "diagnoser_version": "v2",
        "review_status": "pending",
        "summary": "compact",
        "diagnosed_at": "2026-07-19T10:00:00",
        "reviewed_at": None,
        "target_created_at": None,
    }

    attributes = {
        "uuid": record["uuid"],
        "task_uuid": record["task_uuid"],
        "job_uuid": record["job_uuid"],
        "diagnoser_version": "v2",
        "verdict": "bad",
        "diagnosed_at": "2026-07-19T10:00:00",
        "reviewed_at": "",
        "target_created_at": "",
    }
    client.write_span("agentlens_trace", "diagnosis_record", attributes, record)
    name, captured_attributes, span = client._tracer.calls[0]

    assert name == "diagnosis_record"
    assert captured_attributes["diagnoser_version"] == "v2"
    assert captured_attributes["verdict"] == "bad"
    assert captured_attributes["diagnosed_at"] == "2026-07-19T10:00:00"
    assert captured_attributes["reviewed_at"] == ""
    assert captured_attributes["target_created_at"] == ""
    assert "record.body" not in captured_attributes
    assert _SENTINEL_TRACE_TEXT in span.body

    attributes["attempt"] = 0
    client.write_span("agentlens_trace", "diagnosis_record", attributes, record)
    assert client._tracer.calls[1][1]["attempt"] == 0


def test_http_write_debug_logs_enqueued_instead_of_success():
    client = HttpAntcollectorClient.__new__(HttpAntcollectorClient)
    client.otlp_endpoint = "http://collector"
    client._tracer = _CapturingTracer()
    client._log = _CapturingLog()

    record = _record_with_sensitive_content()
    client.write_span(
        "agentlens_trace",
        "diagnosis_record",
        {key: record[key] for key in ("uuid", "task_uuid", "job_uuid", "attempt")},
        record,
    )

    assert client._log.calls == [
        (
            ("antcollector.write_enqueued",),
            {
                "table": "agentlens_trace",
                "name": "diagnosis_record",
                "uuid": "r-abc12345",
                "task_uuid": "t-def67890",
            },
        )
    ]


def test_http_write_accepts_explicit_span_times():
    client = HttpAntcollectorClient.__new__(HttpAntcollectorClient)
    client.otlp_endpoint = "http://collector"
    client._tracer = _CapturingTracer()
    client._log = _NoopLog()
    started_at = datetime(2026, 8, 7, 9, 2, 30)
    ended_at = datetime(2026, 8, 7, 10, 0, 0)

    client.write_span(
        "agentlens_trace",
        "task_stats",
        {"task_uuid": "t-1"},
        {"task_uuid": "t-1"},
        start_time=started_at,
        end_time=ended_at,
    )

    _, attributes, start_ns, span = client._tracer.explicit_calls[0]
    assert start_ns == int(started_at.timestamp() * 1_000_000_000)
    assert span.end_time == int(ended_at.timestamp() * 1_000_000_000)


def test_close_shuts_down_provider_once():
    class _Provider:
        def __init__(self):
            self.calls = 0

        def shutdown(self):
            self.calls += 1

    provider = _Provider()
    client = HttpAntcollectorClient.__new__(HttpAntcollectorClient)
    client._tracer_provider = provider

    client.close()
    client.close()

    assert provider.calls == 1


def test_force_flush_forwards_timeout_to_provider():
    class _Provider:
        timeout_millis = None

        def force_flush(self, *, timeout_millis):
            self.timeout_millis = timeout_millis
            return True

    provider = _Provider()
    client = HttpAntcollectorClient.__new__(HttpAntcollectorClient)
    client._tracer_provider = provider

    assert client.force_flush(timeout_millis=3_000) is True
    assert provider.timeout_millis == 3_000


def test_dry_run_write_does_not_log_sensitive_content(capsys):
    client = HttpAntcollectorClient(otlp_endpoint="")  # empty endpoint -> dry-run path
    record = _record_with_sensitive_content()

    client.write_span(
        "agentlens_trace",
        "diagnosis_record",
        {key: record[key] for key in ("uuid", "task_uuid", "job_uuid", "attempt")},
        record,
    )

    log_text = capsys.readouterr().out + capsys.readouterr().err
    assert _SENTINEL_PROMPT not in log_text
    assert _SENTINEL_TRACE_TEXT not in log_text
    assert _SENTINEL_FEEDBACK_TEXT not in log_text
    assert _SENTINEL_EVIDENCE_TEXT not in log_text


def test_dry_run_write_logs_allowlisted_metadata_only(capsys):
    client = HttpAntcollectorClient(otlp_endpoint="")
    record = _record_with_sensitive_content()

    client.write_span(
        "agentlens_trace",
        "diagnosis_record",
        {key: record[key] for key in ("uuid", "task_uuid", "job_uuid", "attempt")},
        record,
    )

    log_text = capsys.readouterr().out
    for expected in ("r-abc12345", "t-def67890", "j-ghi11111"):
        assert expected in log_text
