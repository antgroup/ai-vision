"""Canonical session filtering keeps complete turns and consistent measurements."""
from types import SimpleNamespace
from copy import deepcopy
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from apps.core.tests.test_native_sessions import detail, span, usage
from apps.core.trace_query.signoz.provider import SignozTraceProvider
from apps.core.trace_query.types import SessionListQuery, SessionDetailQuery, SessionUsageBatchQuery, TraceQueryServiceError
from apps.core.trace_query.canonical_sessions import CanonicalSessionWindow, prepare_params
from apps.core.trace_query.native_query import NativeTraceWindow
from apps.core.trace_query.signoz.read_model import projection_context
from apps.core.trace_query.signoz.session_read_model import CONTRACT


class CanonicalSessionTests(SimpleTestCase):
    def setUp(self):
        self.provider = SignozTraceProvider()
        snapshot = patch('apps.core.trace_query.signoz.snapshot_window.query_snapshot_window', return_value=None)
        snapshot.start()
        self.addCleanup(snapshot.stop)
        self.space = SimpleNamespace(uuid="space-a", type=2)
        self.params = {"session_semantics": "canonical-trace-v1", "agent_uuid": "agent-a",
                       "agent_product": "sdk-agent", "from": 1790980000000, "to": 1790990000000,
                       "page": 1, "limit": 20}

    def read(self, details, **params):
        with override_settings(AIVISION_QUERY_MODE="raw"), patch.object(
            self.provider, "collect_traces", return_value={"data": details, "meta": {"complete": True}}
        ):
            try:
                return self.provider.list_sessions(SessionListQuery(self.space, None, {**self.params, **params}))
            except TraceQueryServiceError as exc:
                self.fail(f"Canonical session query failed: {exc}")

    def test_selecting_one_matching_turn_keeps_every_turn_and_usage(self):
        first = detail("conversation", 0, attrs={**usage(4, 1, 5), "gen_ai.user.id": "alice",
                         "langfuse.trace.input": "needle", "langfuse.trace.tags": ["review"]})
        second = detail("conversation", 1, attrs={**usage(6, 2, 8), "gen_ai.user.id": "bob",
                          "langfuse.trace.input": "other", "langfuse.trace.output": "last answer"})
        second["spans"][0]["status_code"] = 0
        result = self.read([first, second], username="alice", keyword="NEEDLE", searchType="input")
        self.assertEqual(result["meta"]["sessionSemantics"], "canonical-trace-v1")
        self.assertEqual(len(result["data"]), 1)
        row = result["data"][0]
        self.assertEqual((row["trace_count"], row["span_count"], row["total_tokens"]), (2, 2, 13))
        self.assertEqual(row["matched_trace_ids"], ["trace-0"])
        self.assertEqual(row["user_ids"], ["alice", "bob"])
        self.assertEqual(row["tags"], ["review"])
        self.assertEqual(row.get("trace_status_counts"), {"unset": 1, "ok": 1, "error": 0})
        self.assertEqual(row["session_status"], "unset")

    def test_trace_status_counts_preserve_each_root_status_and_aggregate_precedence(self):
        cases = [
            ([0, 0], "unset", {"unset": 2, "ok": 0, "error": 0}),
            ([1, 1], "ok", {"unset": 0, "ok": 2, "error": 0}),
            ([2, 0], "error", {"unset": 1, "ok": 0, "error": 1}),
            ([1, 0], "unset", {"unset": 1, "ok": 1, "error": 0}),
        ]
        for codes, status, counts in cases:
            traces = [detail("conversation", index) for index in range(len(codes))]
            for trace, code in zip(traces, codes):
                trace["spans"][0]["status_code"] = code
            with self.subTest(codes=codes):
                row = self.read(traces)["data"][0]
                self.assertEqual(row.get("trace_status_counts"), counts)
                self.assertEqual(row["session_status"], status)
                self.assertEqual(sum(row["trace_status_counts"].values()), row["trace_count"])

    def test_trace_status_counts_exclude_failed_child_retries(self):
        trace = detail("conversation")
        child = span("trace-0", "conversation", "retry", parent="span-0", kind="TOOL")
        child["status_code"] = 2
        trace["spans"].append(child)
        row = self.read([trace])["data"][0]
        self.assertEqual(row.get("trace_status_counts"), {"unset": 0, "ok": 1, "error": 0})
        self.assertEqual((row["session_status"], row["trace_count"], row["span_count"]), ("ok", 1, 2))

    def test_conjunction_is_one_trace_but_aggregate_filters_use_full_session(self):
        first = detail("conversation", 0, attrs={**usage(40, 10, 50), "gen_ai.user.id": "alice", "langfuse.trace.tags": ["review"],
                                                 "langfuse.trace.input": "needle"})
        second = detail("conversation", 1, attrs={**usage(60, 20, 80), "gen_ai.user.id": "bob", "langfuse.trace.tags": ["deploy"]})
        second["spans"][0]["status_code"] = 2
        selected = self.read([first, second], username="alice", tags=["review"], min_trace_count=2,
                             min_total_tokens=130, min_duration_ms=2000, session_status="error")
        self.assertEqual(len(selected["data"]), 1)
        self.assertEqual(selected["data"][0]["matched_trace_ids"], ["trace-0"])
        self.assertEqual(self.read([first, second], username="alice", tags=["deploy"])["data"], [])
        self.assertEqual(self.read([first, second], min_total_tokens=131)["data"], [])
        self.assertEqual(self.read([first, second], session_status="ok")["data"], [])

    def test_unknown_usage_and_unfinished_time_do_not_match_positive_threshold(self):
        known = detail("mixed", 0, attrs=usage(0, 0, 0))
        missing = detail("mixed", 1, end=False)
        row = self.read([known, missing])["data"][0]
        self.assertIsNone(row["total_tokens"])
        self.assertIsNone(row["duration_ms"])
        self.assertEqual(row["coverage"]["knownTokens"], 0)
        self.assertEqual(row["last_activity"], "2026-10-03T00:00:01.000000000Z")
        self.assertEqual(self.read([known, missing], min_total_tokens=100)["data"], [])
        self.assertEqual(self.read([known, missing], min_duration_ms=1)["data"], [])
        self.assertEqual(self.read([known])["data"][0]["total_tokens"], 0)

    def test_tool_only_trace_does_not_invalidate_measured_billable_usage(self):
        known = detail("mixed", 0, attrs=usage(80, 20, 100))
        tool = detail("mixed", 1, kind="TOOL")
        row = self.read([known, tool], min_total_tokens=100)["data"][0]
        self.assertEqual(row["total_tokens"], 100)
        self.assertEqual(row["trace_count"], 2)
        self.assertEqual(self.read([tool], min_total_tokens=100)["data"], [])

    def test_literal_search_preserves_percent_underscore_unicode_and_beyond_preview(self):
        row = detail("Session_%", attrs={"langfuse.trace.input": "中" * 3000 + " İ_%\\ needle"})
        self.assertEqual(len(self.read([row], keyword="İ_%\\ NEEDLE", searchType="input")["data"]), 1)
        self.assertEqual(self.read([row], keyword="any", searchType="input")["data"], [])
        self.assertEqual(len(self.read([row], session_keyword="Session_%")["data"]), 1)
        self.assertEqual(self.read([row], session_keyword="session_%")["data"], [])
        self.assertLessEqual(len(self.read([row])["data"][0]["ioPreview"]["input"]), 1024)

    def test_recorded_dimensions_preserve_whitespace_when_selecting_facets(self):
        row = detail(" session ", attrs={"gen_ai.user.id": " sdk-user ", "langfuse.environment": " staging "})
        result = self.read([row])
        self.assertEqual(result["facets"]["username"], [{"value": " sdk-user ", "count": 1}])
        for field, value in (("username", " sdk-user "), ("env", " staging "),
                             ("session_id", " session "), ("sessionId", " session ")):
            with self.subTest(field=field):
                selected = self.read([row], **{field: value})
                self.assertEqual([item["session_id"] for item in selected["data"]], [" session "])

    def test_facets_count_sessions_and_exclude_only_own_dimension(self):
        traces = [detail("one", 0, attrs={"gen_ai.user.id": "alice", "langfuse.trace.tags": ["a"]}),
                  detail("one", 1, attrs={"gen_ai.user.id": "bob", "langfuse.trace.tags": ["b"]}),
                  detail("two", 2, attrs={"gen_ai.user.id": "alice", "langfuse.trace.tags": ["a"]})]
        result = self.read(traces, username="alice", min_trace_count=2)
        self.assertEqual(result["facets"]["username"], [{"value": "alice", "count": 1}, {"value": "bob", "count": 1}])
        self.assertEqual(result["facets"]["tags"], [{"value": "a", "count": 1}])

    def test_missing_session_is_not_invented_and_ties_are_binary_stable(self):
        first, second, missing = detail("a", 0), detail("z", 1), detail(None, 2)
        second["spans"][0]["start_time_unix_nano"] = first["spans"][0]["start_time_unix_nano"]
        second["spans"][0]["end_time_unix_nano"] = first["spans"][0]["end_time_unix_nano"]
        rows = self.read([second, first, missing])["data"]
        self.assertEqual([row["session_id"] for row in rows], ["a", "z"])

    def test_new_route_rejects_unsupported_raw_filters_and_invalid_numbers(self):
        for extra in ({"filters": []}, {"scene": "old"}, {"min_total_tokens": True}, {"min_duration_ms": "nan"},
                      {"limit": 51}, {"min_trace_count": 0}, {"session_status": "bad"}, {"searchType": "traceId"},
                      {"min_total_tokens": "1.5"}, {"tags": "bad"}, {"orderBy": "trace_count.desc"}):
            with self.subTest(extra=extra), self.assertRaises(TraceQueryServiceError):
                prepare_params({**self.params, **extra})

    def test_http_merge_keeps_marker_and_does_not_append_old_identity_filter(self):
        from apps.core.views.trace_handlers import merge_session_list_params, merge_batch_params
        payload = {"session_semantics": "canonical-trace-v1", "username": "sdk-user", "min_trace_count": 2,
                   "keyword": "needle", "searchType": "output"}
        merged = merge_session_list_params(payload, {})
        self.assertEqual(merged, payload)
        self.assertEqual(merge_batch_params(payload, {})["session_semantics"], "canonical-trace-v1")
        self.assertNotIn("io", merge_session_list_params(payload, {"io": "true", "usage": "true"}))
        self.assertIn("typo", merge_session_list_params({**payload, "typo": True}, {}))

    def test_canonical_detail_groups_by_same_trace_session_and_keeps_original_window(self):
        first, second = detail("session", 0, attrs=usage(4, 1, 5)), detail("other", 1, attrs=usage(6, 2, 8))
        # Child-only membership stays supported by old callers but must not
        # pull an unrelated canonical Trace into the new session list/detail.
        from apps.core.tests.test_native_sessions import span
        second["spans"].append(span("trace-1", "session", "child", second=1, parent="span-1", kind="TOOL"))
        window = NativeTraceWindow([first, second], project_id="space-a")
        with patch.object(self.provider, "_window", return_value=(window, {"from": self.params["from"], "to": self.params["to"]})):
            old = self.provider.get_session_detail(SessionDetailQuery(self.space, None, {}, "session"))
            canonical = self.provider.get_session_detail(SessionDetailQuery(self.space, None, self.params, "session"))
            self.assertEqual(old["traceCount"], 2)
            self.assertEqual(canonical["traceCount"], 1)
            self.assertEqual(canonical["timeRange"], {"from": str(self.params["from"]), "to": str(self.params["to"])})
            usage_result = self.provider.get_session_usage_batch(SessionUsageBatchQuery(self.space, None, self.params, ["session"]))
            self.assertEqual(usage_result["data"][0]["totalTokens"], 5)


class CanonicalSessionReadModelTests(SimpleTestCase):
    def setUp(self):
        self.provider = SignozTraceProvider()
        snapshot = patch('apps.core.trace_query.signoz.snapshot_window.query_snapshot_window', return_value=None)
        snapshot.start()
        self.addCleanup(snapshot.stop)
        self.space = SimpleNamespace(uuid="space-a", type=2)
        self.params = {"session_semantics": "canonical-trace-v1", "agent_uuid": "agent-a",
                       "agent_product": "public-product", "from": 1790980000000, "to": 1790990000000,
                       "page": 1, "limit": 20}
        self.query = SessionListQuery(self.space, None, self.params)
        self.details = [detail("session", 0, attrs=usage(4, 1, 5)), detail("session", 1, attrs=usage(6, 2, 8))]
        native = NativeTraceWindow(self.details, project_id="space-a", prices=[], include_previews=True)
        rows = [{**row, "_traceTags": trace["tags"], "_sceneValues": [], "sourceRevision": 1}
                for row, trace in zip(native.rows, native.traces)]
        canonical = CanonicalSessionWindow(rows).list(self.params)
        self.payload = {"supported": True, "data": [{"session_id": "session", "traceCount": 2, "traces": rows,
                        "matchedTraceIds": ["trace-0", "trace-1"]}], "facets": canonical["facets"],
                        "meta": {**canonical["meta"], "contract": CONTRACT, "queryMode": "read_model",
                        "from": self.params["from"], "to": self.params["to"], "space_id": "space-a", "user": "",
                        "agent_uuid": "agent-a", "agent_product": "stored-product", "projection_context": projection_context([])}}
        self.client = Mock()
        self.client._native_performance_capabilities = {'content_projection': 'native-trace-content-v1'}
        self.client.get.return_value = self.payload
        context = patch.object(self.provider, "_context", return_value=(self.client, {"agent_product_filter": "stored-product"}))
        context.start()
        self.addCleanup(context.stop)

    @override_settings(AIVISION_QUERY_MODE="auto")
    def test_verified_group_avoids_raw_scan_and_preserves_full_canonical_totals(self):
        with patch.object(self.provider, "collect_traces") as scan:
            result = self.provider.list_sessions(self.query)
        scan.assert_not_called()
        self.assertEqual(result["data"][0]["total_tokens"], 13)
        self.assertEqual(result["meta"]["readModel"]["state"], "ready")
        self.assertEqual(self.client.get.call_args.args[0], "/api/v1/ai-vision/traces/read-model/sessions/page")
        self.assertEqual(self.client.get.call_args.kwargs["params"]["agent_product"], "stored-product")

    @override_settings(AIVISION_QUERY_MODE="raw")
    def test_mode_legacy_never_calls_new_endpoint(self):
        with patch.object(self.provider, "collect_traces", return_value={"data": self.details, "meta": {"complete": True}}):
            result = self.provider.list_sessions(self.query)
        self.client.get.assert_not_called()
        self.assertEqual(result["meta"]["queryMode"], "canonical_session_scan")

    def test_trace_status_counts_match_raw_and_read_model_for_complete_filtered_session(self):
        self.details[0]["spans"][0]["attributes"]["langfuse.trace.input"] = "needle"
        self.details[1]["spans"][0]["status_code"] = 0
        native = NativeTraceWindow(self.details, project_id="space-a", prices=[], include_previews=True)
        rows = [{**row, "_traceTags": trace["tags"], "_sceneValues": [], "sourceRevision": 1}
                for row, trace in zip(native.rows, native.traces)]
        self.payload["data"][0].update(traces=rows, matchedTraceIds=["trace-0"])
        self.client.get.return_value = self.payload
        query = SessionListQuery(self.space, None, {**self.params, "keyword": "needle", "searchType": "input"})
        with override_settings(AIVISION_QUERY_MODE="auto"):
            accelerated = self.provider.list_sessions(query)
        with override_settings(AIVISION_QUERY_MODE="raw"), patch.object(
            self.provider, "collect_traces", return_value={"data": self.details, "meta": {"complete": True}}
        ):
            raw = self.provider.list_sessions(query)
        row = accelerated["data"][0]
        self.assertEqual(row.get("trace_status_counts"), {"unset": 1, "ok": 1, "error": 0})
        self.assertEqual((row["session_status"], row["trace_count"], row["matched_trace_count"]), ("unset", 2, 1))
        self.assertEqual(accelerated["data"], raw["data"])
        self.assertEqual(accelerated["meta"]["queryMode"], "read_model")
        self.assertEqual(raw["meta"]["queryMode"], "canonical_session_scan")

    @override_settings(AIVISION_QUERY_MODE="auto")
    def test_old_disabled_and_stale_api_fall_back_with_identical_semantics(self):
        with patch.object(self.provider, "collect_traces", return_value={"data": self.details, "meta": {"complete": True}}):
            for reason in ("read_model_disabled", "historical_coverage_incomplete", "projection_coverage_stale", "session_page_trace_limit"):
                self.client.get.return_value = {"supported": False, "reason": reason}
                result = self.provider.list_sessions(self.query)
                self.assertEqual(result["data"][0]["total_tokens"], 13)
                self.assertEqual(result["meta"]["readModel"]["reason"], reason)
            self.client.get.side_effect = TraceQueryServiceError("not found", 3019)
            self.assertEqual(self.provider.list_sessions(self.query)["meta"]["readModel"]["reason"], "capability_unavailable")

    @override_settings(AIVISION_QUERY_MODE="auto")
    def test_malformed_scopes_groups_metrics_and_facets_fail_explicitly(self):
        mutations = [lambda p: p["meta"].update(space_id="other"), lambda p: p["data"][0].update(traceCount=3),
                     lambda p: p["data"][0].update(matchedTraceIds=["absent"]),
                     lambda p: p["data"][0]["traces"].reverse(),
                     lambda p: p["data"][0]["traces"][0].update(session_id="other"),
                     lambda p: p["data"][0]["traces"][0].update(sourceRevision=True),
                     lambda p: p["data"][0]["traces"][0]["usage"].update(total="wrong"),
                     lambda p: p["data"][0]["traces"][0]["usage"]["cost"].update(total="NaN"),
                     lambda p: p["data"][0]["traces"][0]["coverage"].update(billableCalls=True),
                     lambda p: p["facets"]["env"][0].update(count=2),
                     lambda p: p["meta"].update(totalItems=2),
                     lambda p: p["data"][0]["traces"][0]["ioPreview"].update(complete=False)]
        for mutation in mutations:
            candidate = deepcopy(self.payload)
            mutation(candidate)
            self.client.get.return_value = candidate
            with self.subTest(mutation=mutation), patch.object(self.provider, "collect_traces") as scan, self.assertRaises(TraceQueryServiceError) as raised:
                self.provider.list_sessions(self.query)
            self.assertEqual(raised.exception.code, 3035)
            scan.assert_not_called()
        self.client.get.side_effect = TraceQueryServiceError("forbidden", 3013)
        with patch.object(self.provider, "collect_traces") as scan, self.assertRaises(TraceQueryServiceError):
            self.provider.list_sessions(self.query)
        scan.assert_not_called()

    @override_settings(AIVISION_QUERY_MODE="auto")
    def test_sdk_username_is_not_physical_scope_and_search_uses_python_lower(self):
        self.client.get.return_value = {"supported": False, "reason": "test"}
        from apps.core.trace_query.signoz.session_read_model import query_read_model
        query_read_model(self.provider, SessionListQuery(self.space, None, {**self.params, "username": "sdk-user", "keyword": "İ_%\\"}))
        sent = self.client.get.call_args.kwargs["params"]
        self.assertNotIn("user", sent)
        self.assertEqual(sent["username"], "sdk-user")
        self.assertEqual(sent["keyword"], "İ_%\\".lower())

    @override_settings(AIVISION_QUERY_MODE="auto")
    def test_valid_unicode_and_deep_pages_outside_wire_bounds_keep_canonical_scan(self):
        extras = [{field: "界" * 100} for field in ("session_id", "sessionId", "session_keyword", "env", "username")]
        extras += [{"keyword": "界" * 1400}, {"keyword": "İ" * 2000}, {"page": 1000001}]
        for extra in extras:
            field, value = next(iter(extra.items()))
            with self.subTest(field=field, length=len(value) if isinstance(value, str) else value):
                self.client.get.reset_mock()
                with patch.object(self.provider, "collect_traces", return_value={"data": self.details, "meta": {"complete": True}}) as scan:
                    result = self.provider.list_sessions(SessionListQuery(self.space, None, {**self.params, **extra}))
                self.assertEqual(result["meta"]["queryMode"], "canonical_session_scan")
                self.assertEqual(result["meta"]["readModel"]["reason"], "unsupported_wire_bounds")
                self.client.get.assert_not_called()
                scan.assert_called_once()
