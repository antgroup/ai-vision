import time
from contextvars import ContextVar
from threading import Lock
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.core.services.trace_query_service import TraceQueryServiceError
from apps.core.views.labs_scan import collect_span_window


class LabsScanTests(SimpleTestCase):
    def test_pages_overlap_with_bounded_workers_preserving_order_and_context(self):
        context = ContextVar("labs_scan_test", default="missing")
        token = context.set("request-context")
        lock = Lock()
        active = 0
        peak = 0
        requested = []
        scopes = []
        space, user = object(), object()
        params = {"page": 1, "limit": 1, "agent_uuid": "agent-a"}

        def fetch_page(**kwargs):
            nonlocal active, peak
            page = kwargs["query_params"]["page"]
            with lock:
                requested.append(page)
                scopes.append((kwargs["space"], kwargs["user"], context.get()))
                active += 1
                peak = max(peak, active)
            time.sleep(0.01)
            with lock:
                active -= 1
            return {
                "spans": [{"trace_id": "trace", "span_id": str(page)}],
                "spanMeta": {
                    "page": page,
                    "limit": 1,
                    "totalPages": 9,
                    "totalItems": 9,
                },
            }

        try:
            with patch("apps.core.views.labs_scan.connections.close_all") as close:
                result = collect_span_window(
                    fetch_page,
                    space=space,
                    user=user,
                    query_params=params,
                    label="memory",
                    page_size=1,
                    max_spans=10000,
                    limit_code=3202,
                )
            self.assertEqual(close.call_count, 8)
            self.assertGreater(peak, 1)
            self.assertLessEqual(peak, 4)
            self.assertEqual(sorted(requested), list(range(1, 10)))
            self.assertEqual(
                [span["span_id"] for span in result["spans"]],
                [str(i) for i in range(1, 10)],
            )
            self.assertTrue(
                all(scope == (space, user, "request-context") for scope in scopes)
            )
            self.assertEqual(params["page"], 1)
        finally:
            context.reset(token)

    def test_inconsistent_batch_is_rejected_before_requesting_more_pages(self):
        requested = []

        def fetch_page(**kwargs):
            page = kwargs["query_params"]["page"]
            requested.append(page)
            return {
                "spans": [{"trace_id": "trace", "span_id": str(page)}],
                "spanMeta": {
                    "page": page,
                    "limit": 1,
                    "totalPages": 7 if page == 3 else 6,
                    "totalItems": 7 if page == 3 else 6,
                },
            }

        with self.assertRaises(TraceQueryServiceError) as error:
            collect_span_window(
                fetch_page,
                space=None,
                user=None,
                query_params={},
                label="skill",
                page_size=1,
                max_spans=10000,
                limit_code=3027,
            )
        self.assertEqual(error.exception.code, 3035)
        self.assertNotIn(6, requested)

    def test_worker_failure_closes_connections_without_returning_partial_results(self):
        def fetch_page(**kwargs):
            page = kwargs["query_params"]["page"]
            if page == 2:
                raise TraceQueryServiceError("Storage unavailable", 3011)
            return {
                "spans": [{"trace_id": "trace", "span_id": str(page)}],
                "spanMeta": {
                    "page": page,
                    "limit": 1,
                    "totalPages": 2,
                    "totalItems": 2,
                },
            }

        with patch("apps.core.views.labs_scan.connections.close_all") as close:
            with self.assertRaises(TraceQueryServiceError) as error:
                collect_span_window(
                    fetch_page,
                    space=None,
                    user=None,
                    query_params={},
                    label="memory",
                    page_size=1,
                    max_spans=10000,
                    limit_code=3202,
                )
        self.assertEqual(error.exception.code, 3011)
        close.assert_called_once()
