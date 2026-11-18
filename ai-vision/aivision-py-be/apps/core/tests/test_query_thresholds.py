"""Public lower bounds fail before a costly source query, without clamping."""
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from apps.core.trace_query.canonical_sessions import prepare_params
from apps.core.trace_query.native_query import NativeTraceWindow, validate_params
from apps.core.trace_query.signoz.provider import SignozTraceProvider
from apps.core.trace_query.types import TraceListQuery, TraceQueryServiceError


class QueryThresholdTests(SimpleTestCase):
    def test_session_lower_bounds_are_explicit_and_bounded(self):
        for value in (0, 1, 99, "0", "99", -1, True, "nan", 2**53):
            with self.subTest(value=value), self.assertRaises(TraceQueryServiceError):
                prepare_params({"session_semantics": "canonical-trace-v1", "min_total_tokens": value})
        self.assertEqual(prepare_params({"session_semantics": "canonical-trace-v1", "min_total_tokens": "100"})["min_total_tokens"], 100)
        for value in (0, "0", .5, 1e300):
            with self.subTest(duration=value), self.assertRaises(TraceQueryServiceError):
                prepare_params({"session_semantics": "canonical-trace-v1", "min_duration_ms": value})

    def test_trace_lower_bounds_keep_zero_status_and_equality_semantics(self):
        for field, bad, minimum in (("total_tokens", 99, 100), ("attributes.gen_ai.usage.total_tokens", 0, 100),
                                    ("duration_ms", .5, 1), ("span_count", 0, 1)):
            with self.subTest(field=field):
                with self.assertRaises(TraceQueryServiceError):
                    validate_params({"filters": [{"field": field, "op": ">=", "value": bad}]})
                validate_params({"filters": [{"field": field, "op": ">=", "value": minimum}]})
                with self.assertRaises(TraceQueryServiceError):
                    validate_params({"filters": [{"field": field, "op": ">=", "value": 2**53}]})
                with self.assertRaises(TraceQueryServiceError):
                    validate_params({"filters": [{"field": field, "op": ">=", "value": None}]})
        for field in ("total_tokens", "status_code", "duration_ms"):
            validate_params({"filters": [{"field": field, "op": "=", "value": 0}]})
        validate_params({"filters": [{"field": "status_code", "op": ">=", "value": 0}]})
        NativeTraceWindow([], project_id="space").list({"filters": [{"field": "total_tokens", "op": "IS_NULL"}]})
        validate_params({"filters": [{"field": "total_tokens", "op": "=", "value": None}]})
        self.assertNotIn("min_total_tokens", prepare_params({"session_semantics": "canonical-trace-v1", "min_total_tokens": None}))

    def test_raw_mode_rejects_low_tokens_before_source_access(self):
        provider = SignozTraceProvider()
        params = {"agent_uuid": "agent", "filters": [{"field": "total_tokens", "op": ">=", "value": 0}]}
        query = TraceListQuery(SimpleNamespace(uuid="space"), None, params)
        with override_settings(AIVISION_QUERY_MODE="raw"), patch.object(provider, "_freeze_window_params", return_value=params), patch.object(provider, "_query_traces_legacy") as source:
            with self.assertRaises(TraceQueryServiceError) as raised:
                provider.query_traces(query)
            self.assertEqual(raised.exception.code, 3027)
            source.assert_not_called()
