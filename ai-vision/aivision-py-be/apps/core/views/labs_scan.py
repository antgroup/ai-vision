"""Shared bounded scans and latency aggregation for observability views."""

import math
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context

from django.db import connections

from ..services.trace_query_service import TraceQueryServiceError
from ..trace_query.semantics.tool_outcome import tool_span_outcome as tool_span_outcome

MAX_SCAN_WORKERS = 4


def call_status_stats(statuses: Iterable[str]) -> dict:
    """Error rate is complete only when every observed outcome is reported."""
    values = list(statuses)
    reported = sum(status in {"ok", "error"} for status in values)
    unknown = len(values) - reported
    errors = sum(status == "error" for status in values)
    return {
        "errors": errors,
        "reportedStatusCalls": reported,
        "unknownStatusCalls": unknown,
        "errorRate": (
            None if unknown else round(errors / len(values), 4) if values else 0
        ),
    }


def latency_stats(values: list[int], empty: int | None = 0) -> dict:
    if not values:
        return {"p50Ms": empty, "p95Ms": empty}
    ordered = sorted(values)
    return {
        "p50Ms": ordered[math.ceil(len(ordered) * 0.5) - 1],
        "p95Ms": ordered[math.ceil(len(ordered) * 0.95) - 1],
    }


def collect_span_window(
    fetch_page,
    *,
    space,
    user,
    query_params: dict,
    label: str,
    page_size: int,
    max_spans: int,
    limit_code: int,
    allow_legacy_single_page: bool = False,
    max_workers: int = MAX_SCAN_WORKERS,
) -> dict:
    """Refuse partial, duplicated or changing storage windows."""
    spans: list[dict] = []
    seen: set[tuple[str, str]] = set()
    expected = None
    page = 1

    def fetch(number):
        return fetch_page(
            space=space, user=user, query_params={**query_params, "page": number}
        )

    def fetch_in_worker(number):
        try:
            return fetch(number)
        finally:
            # Worker threads own their Django connections, including on errors.
            connections.close_all()

    pending = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        while True:
            scan = (
                fetch(page)
                if page == 1 or max_workers == 1
                else pending.pop(page).result()
            )
            if isinstance(scan, dict) and scan.get("supported") is False:
                raise TraceQueryServiceError(
                    f"{label[0].upper() + label[1:]} span queries are not supported by this trace data source",
                    3011,
                )
            items = scan.get("spans") if isinstance(scan, dict) else None
            meta = scan.get("spanMeta") if isinstance(scan, dict) else None
            if not isinstance(items, list) or not isinstance(meta, dict):
                raise TraceQueryServiceError(
                    f"Incomplete {label} pagination metadata", 3035
                )
            total = meta.get("totalItems")
            limit = meta.get("limit", page_size)
            pages = meta.get("totalPages")
            # MCP's legacy providers may omit totalPages for complete windows.
            if allow_legacy_single_page and pages is None and total == len(items) and page == 1:
                pages = 1
            if (
                any(
                    isinstance(value, bool) or not isinstance(value, int)
                    for value in (total, limit, pages)
                )
                or total < 0
                or limit < 1
                or pages < 0
                or meta.get("page", page) != page
                or (
                    pages != max(1, math.ceil(total / limit))
                    and not (total == 0 and pages == 0)
                )
            ):
                raise TraceQueryServiceError(
                    f"Incomplete {label} pagination metadata", 3035
                )
            if total > max_spans:
                raise TraceQueryServiceError(
                    f"{label[0].upper() + label[1:]} scan limit is {max_spans} spans; narrow the time range",
                    limit_code,
                )
            window = (total, pages, limit)
            if expected is None:
                expected = window
            elif expected != window:
                raise TraceQueryServiceError(
                    f"Changed {label} window during pagination; retry", 3035
                )
            for item in items:
                trace_id = item.get("trace_id") if isinstance(item, dict) else None
                span_id = item.get("span_id") if isinstance(item, dict) else None
                identity = (trace_id, span_id)
                if (
                    not isinstance(trace_id, str)
                    or not trace_id
                    or not isinstance(span_id, str)
                    or not span_id
                    or identity in seen
                ):
                    raise TraceQueryServiceError(
                        f"Duplicate or missing {label} span ID; retry", 3035
                    )
                seen.add(identity)
                spans.append(item)
            if page >= pages:
                break
            if len(items) != limit:
                raise TraceQueryServiceError(
                    f"Incomplete {label} span page; retry", 3035
                )
            page += 1
            if max_workers > 1 and not pending:
                # Validate each batch before requesting another bounded group.
                pending = {
                    number: pool.submit(copy_context().run, fetch_in_worker, number)
                    for number in range(page, min(page + max_workers, pages + 1))
                }
    if len(spans) != expected[0]:
        raise TraceQueryServiceError(f"Incomplete {label} span window; retry", 3035)
    return {"spans": spans, "spanMeta": {"totalItems": len(spans)}}
