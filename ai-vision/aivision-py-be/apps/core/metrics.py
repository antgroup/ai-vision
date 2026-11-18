"""
Custom OpenTelemetry business metrics for AI Vision.

All instruments are created eagerly at import time via the global
``MeterProvider``.  When OTel is **not** enabled the default no-op provider
is used, so recording calls become zero-cost stubs – no ``if`` guards needed
at call sites.

Usage::

    from apps.core.metrics import external_call_timer

    with external_call_timer("langfuse", "list_traces"):
        resp = requests.get(...)
"""

from __future__ import annotations

import time
from collections.abc import Generator
from contextlib import contextmanager

from opentelemetry import metrics

meter = metrics.get_meter("aivision.core")

# ── Trace query performance ──────────────────────────────────────────────

trace_query_duration = meter.create_histogram(
    name="aivision.trace_query.duration",
    description="Trace query duration",
    unit="s",
)

trace_query_count = meter.create_counter(
    name="aivision.trace_query.count",
    description="Number of trace queries executed",
)

# ── Query adapter performance ────────────────────────────────────────────

adapter_duration = meter.create_histogram(
    name="aivision.adapter.duration",
    description="Query adapter execution duration",
    unit="s",
)

adapter_errors = meter.create_counter(
    name="aivision.adapter.errors",
    description="Number of query adapter errors",
)

# ── V2 dashboard query performance ──────────────────────────────────────

v2_query_duration = meter.create_histogram(
    name="aivision.v2_query.duration",
    description="V2 dashboard query duration (single query)",
    unit="s",
)

v2_query_count = meter.create_counter(
    name="aivision.v2_query.count",
    description="Number of V2 dashboard queries executed",
)

v2_query_errors = meter.create_counter(
    name="aivision.v2_query.errors",
    description="Number of V2 dashboard query errors",
)

v2_batch_duration = meter.create_histogram(
    name="aivision.v2_batch_query.duration",
    description="V2 dashboard batch query total duration",
    unit="s",
)

# ── Authentication events ────────────────────────────────────────────────

auth_events = meter.create_counter(
    name="aivision.auth.events",
    description="Authentication events (login, refresh, failure)",
)

# ── External API call tracking ───────────────────────────────────────

external_api_calls = meter.create_counter(
    name="aivision.external_api.calls",
    unit="1",
    description="External API call count by space and auth type",
)

# ── External service call performance ───────────────────────────────────

external_call_duration = meter.create_histogram(
    name="aivision.external_call.duration",
    description="Duration of calls to external services",
    unit="s",
)

external_call_errors = meter.create_counter(
    name="aivision.external_call.errors",
    description="Number of failed external service calls",
)

# ── Cloud Native control-plane reconciliation ───────────────────────────


@contextmanager
def external_call_timer(service: str, operation: str) -> Generator[None, None, None]:
    attrs = {"service": service, "operation": operation}
    start = time.monotonic()
    try:
        yield
        external_call_duration.record(
            time.monotonic() - start, {**attrs, "status": "success"}
        )
    except Exception:
        external_call_duration.record(
            time.monotonic() - start, {**attrs, "status": "error"}
        )
        external_call_errors.add(1, attrs)
        raise
