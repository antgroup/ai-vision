from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.core.trace_query.signoz.provider import SignozTraceProvider
from apps.core.trace_query.types import TraceListQuery, TraceDetailQuery, TraceUsageBatchQuery, TraceQueryServiceError


class NativeWindowTests(SimpleTestCase):
    def setUp(self):
        self.provider = SignozTraceProvider()
        self.query = TraceListQuery(
            SimpleNamespace(uuid="space-1", type=2), None,
            {"from": 1000, "to": 2000, "agent_product": "test", "page": 9, "limit": 1},
        )

    def test_invalid_implicit_window_is_a_query_error(self):
        for value in ("not-a-date", True):
            with self.subTest(to=value), self.assertRaises(TraceQueryServiceError) as caught:
                self.provider.collect_traces(TraceListQuery(self.query.space, None, {"to": value}))
            self.assertEqual(caught.exception.code, 3027)

    @patch.object(SignozTraceProvider, "_session_traces")
    def test_session_window_includes_unfinished_span_on_millisecond_boundary(self, session_traces):
        from apps.core.trace_query.types import SessionDetailQuery
        session_traces.return_value = ({"session": [{"id": "t", "timestamp": "2026-10-03T00:00:00Z",
            "observations": [{"startTime": "2026-10-03T00:00:00Z", "endTime": None}]}]}, {"complete": True})
        result = self.provider.get_session_detail(SessionDetailQuery(self.query.space, None, {}, "session"))
        self.assertEqual(int(result["timeRange"]["to"]) - int(result["timeRange"]["from"]), 1)

    @patch.object(SignozTraceProvider, "_session_traces")
    def test_session_detail_does_not_expand_selection_past_requested_to(self, session_traces):
        from apps.core.trace_query.types import SessionDetailQuery
        session_traces.return_value = ({"session": [{"id": "t", "timestamp": "1970-01-01T00:00:01Z",
            "observations": [{"startTime": "1970-01-01T00:00:01Z", "endTime": "1970-01-01T00:00:02Z"}]}]},
            {"complete": True, "from": 1000, "to": 1001})
        result = self.provider.get_session_detail(SessionDetailQuery(self.query.space, None, {}, "session"))
        self.assertEqual(result["timeRange"], {"from": "1000", "to": "1001"})

    @patch.object(SignozTraceProvider, "get_trace_detail")
    @patch.object(SignozTraceProvider, "list_traces")
    def test_window_reads_all_201_traces_not_first_page(self, listing, detail):
        ids = [f"trace-{i:04d}" for i in range(201)]
        listing.side_effect = [
            {"data": [{"trace_id": i, "span_count": 1} for i in ids[:200]],
             "meta": {"page": 1, "limit": 200, "totalItems": 201, "totalPages": 2}},
            {"data": [{"trace_id": ids[-1], "span_count": 1}],
             "meta": {"page": 2, "limit": 200, "totalItems": 201, "totalPages": 2}},
        ]
        detail.side_effect = lambda q, **kwargs: {"traceId": q.trace_id, "spans": [{"span_id": q.trace_id}]}
        result = self.provider.collect_traces(self.query)
        self.assertEqual([t["traceId"] for t in result["data"]], ids)
        self.assertTrue(result["meta"]["complete"])
        self.assertEqual(result["meta"]["totalItems"], 201)
        self.assertEqual(listing.call_args_list[0].args[0].get_param("page"), 1)
        self.assertEqual(listing.call_args_list[1].args[0].get_param("page"), 2)

    @patch.object(SignozTraceProvider, "get_trace_detail")
    @patch.object(SignozTraceProvider, "list_traces")
    def test_duplicate_page_cannot_be_marked_complete(self, listing, detail):
        listing.return_value = {"data": [{"trace_id": "same"}],
                                "meta": {"page": 1, "limit": 1, "totalItems": 2, "totalPages": 2}}
        detail.return_value = {"traceId": "same", "spans": []}
        with self.assertRaisesRegex(TraceQueryServiceError, "[Ii]ncomplete|[Cc]hanged|[Dd]uplicate"):
            self.provider.collect_traces(self.query)

    @patch.object(SignozTraceProvider, "list_traces")
    def test_missing_metadata_is_not_empty_success(self, listing):
        listing.return_value = {"data": [], "meta": {}}
        with self.assertRaisesRegex(TraceQueryServiceError, "[Ii]ncomplete|[Mm]etadata"):
            self.provider.collect_traces(self.query)

    @patch.object(SignozTraceProvider, "list_traces")
    def test_scan_limit_is_explicit_error(self, listing):
        listing.return_value = {"data": [{"trace_id": "a"}],
                                "meta": {"page": 1, "limit": 200, "totalItems": 10001, "totalPages": 51}}
        with self.assertRaisesRegex(TraceQueryServiceError, "limit"):
            self.provider.collect_traces(self.query)

    @patch.object(SignozTraceProvider, "_context")
    def test_detail_rejects_foreign_child_even_with_matching_root(self, context):
        from unittest.mock import Mock
        client = Mock()
        context.return_value = client, {}
        client.get.return_value = {"traceId": "t", "spans": [
            {"resource": {"aivision.space.id": "space-1", "aivision.agent.id": "agent-a"}},
            {"resource": {"aivision.space.id": "space-1", "aivision.agent.id": "agent-b"}},
        ]}
        with self.assertRaisesRegex(TraceQueryServiceError, "not found"):
            self.provider.get_trace_detail(TraceDetailQuery(self.query.space, None, {"agent_uuid": "agent-a"}, "t"))

    @patch.object(SignozTraceProvider, "get_trace_detail")
    def test_usage_uses_same_inclusive_cache_semantics(self, detail):
        detail.return_value = {"traceId": "t", "spans": [{"span_id": "s", "name": "generation",
            "start_time_unix_nano": "1790989323000000000", "attributes": {
                "langfuse.observation.type": "GENERATION", "langfuse.observation.usage_details":
                '{"input":12931,"cache_read_input_tokens":8192,"output":635,"total":21758}'}}]}
        result = self.provider.get_trace_usage_batch(TraceUsageBatchQuery(self.query.space, None, {}, ["t", "t"]))
        usage = result["data"][0]
        self.assertEqual(usage["totalTokens"], 21758)
        self.assertEqual(usage["usage"]["cacheReadInput"], 8192)
        self.assertIsNone(usage["usage"]["cost"]["total"])
        self.assertEqual(detail.call_count, 1)

    @patch.object(SignozTraceProvider, "_context")
    def test_detail_summary_matches_list_for_known_partial_zero_unknown_and_ambiguous_usage(self, context):
        from copy import deepcopy
        from unittest.mock import Mock
        from apps.core.tests.test_native_trace_summary import trace_with_calls
        from apps.core.trace_query.native_query import NativeTraceWindow

        for totals, nested in (([12] * 11, False), ([12] * 10 + [None], False),
                               ([None, None], False), ([0, 0], False), ([12, 12], True)):
            with self.subTest(totals=totals, nested=nested):
                detail = trace_with_calls(totals, nested=nested)
                for span in detail["spans"]:
                    span["resource"] = {"aivision.space.id": "space-1", "ant.agent.env": "prod"}
                detail["spans"][0]["attributes"]["langfuse.environment"] = "local"
                client = Mock()
                context.return_value = client, {}
                client.get.side_effect = [deepcopy(detail), {"data": [{
                    "trace_id": detail["traceId"], "span_count": len(detail["spans"]),
                }]}]
                result = self.provider.get_trace_detail(TraceDetailQuery(
                    self.query.space, None, {"from": 1000, "to": 2000}, detail["traceId"],
                ))
                row = NativeTraceWindow([detail], project_id="space-1").list({})["data"][0]
                for field in ("environment", "environmentSource", "usage", "reportedUsage", "coverage"):
                    self.assertEqual(result["semantics"][field], row[field])
                # Existing detail/count reconciliation is sufficient. Summaries
                # use these spans and never add a measurement-specific fetch.
                self.assertEqual(client.get.call_count, 2)
                self.assertEqual(result["spans"][0]["resource"]["ant.agent.env"], "prod")
                self.assertEqual(result["semantics"]["environment"], "local")
