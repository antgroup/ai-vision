"""Targeted session reads select traces without truncating their full history."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from apps.core.trace_query.langfuse.native import NativeLangfuseService
from apps.core.trace_query.semantics.observations import project_trace, timestamp_ns
from apps.core.trace_query.signoz.provider import SignozTraceProvider
from apps.core.trace_query.types import TraceQueryServiceError


@override_settings(
    AIVISION_NATIVE_TRACE_HISTORY_START="2026-09-01T00:00:00Z",
    AIVISION_NATIVE_TRACE_FUTURE_SKEW_SECONDS=0,
)
class NativeSessionSelectionTests(SimpleTestCase):
    def setUp(self):
        self.provider = SignozTraceProvider()
        self.space = SimpleNamespace(uuid="space", type=1)
        self.user = object()
        self.agent = SimpleNamespace(uuid="agent", agent_product="langfuse")
        self.service = NativeLangfuseService(
            space=self.space, user=self.user, agent=self.agent, provider=self.provider,
        )
        self.first_window = (
            timestamp_ns("2026-09-01T00:00:00Z") // 1000000,
            timestamp_ns("2026-10-02T00:00:00Z") // 1000000,
        )
        self.second_window = (
            self.first_window[1], timestamp_ns("2026-10-03T02:00:00Z") // 1000000,
        )
        self.fragments = {
            self.first_window: {
                "joined": [
                    self.span("joined", "root", "2026-09-15T00:00:00Z"),
                ],
                "unrelated": [self.span("unrelated", "other", "2026-09-16T00:00:00Z", "other")],
                "foreign-agent": [self.span("foreign-agent", "foreign", "2026-09-17T00:00:00Z", "wanted", agent="other-agent")],
            },
            self.second_window: {
                "joined": [
                    self.span("joined", "session-child", "2026-10-03T00:00:00Z", "wanted", "root"),
                    self.span("joined", "later", "2026-10-03T00:00:01Z", parent="root"),
                ],
                "second": [self.span("second", "second-root", "2026-10-03T01:00:00Z", "wanted")],
            },
        }
        self.calls = []

    @staticmethod
    def span(trace_id, span_id, start, session=None, parent="", *, agent="agent"):
        attributes = {"langfuse.observation.type": "span", "user.id": "business-user"}
        if session:
            attributes["langfuse.session.id"] = session
        return {
            "trace_id": trace_id, "span_id": span_id, "parent_span_id": parent,
            "name": span_id, "start_time_unix_nano": start,
            "end_time_unix_nano": start, "session_id": session,
            "resource": {"aivision.space.id": "space", "aivision.agent.id": agent},
            "attributes": attributes,
        }

    def storage(self, path, *, params):
        # Emulate the pinned query server's span-level session WHERE, including
        # its filtered span_count. The real provider builds every HTTP query.
        self.calls.append((path, dict(params)))
        fragments = self.fragments[(params["from"], params["to"])]
        session_id = params.get("session_id")
        if path == "/api/v1/ai-vision/traces":
            rows = []
            for trace_id, spans in fragments.items():
                if params.get("trace_id") and trace_id != params["trace_id"]:
                    continue
                selected = [span for span in spans if not session_id or span["session_id"] == session_id]
                if selected:
                    rows.append({"trace_id": trace_id, "span_count": len(selected)})
            rows.sort(key=lambda row: row["trace_id"])
            page, limit = params.get("page", 1), params.get("limit", 200)
            return {"data": rows[(page - 1) * limit:page * limit], "meta": {
                "page": page, "limit": limit, "totalItems": len(rows),
                "totalPages": (len(rows) + limit - 1) // limit,
            }}
        trace_id = path.rsplit("/", 1)[-1]
        spans = [span for span in fragments[trace_id]
                 if not session_id or span["session_id"] == session_id]
        return {"traceId": trace_id, "spans": deepcopy(spans)}

    def read(self, *, storage=None, session_id="wanted"):
        client = Mock()
        client.get.side_effect = storage or self.storage
        with (
            patch.object(self.provider, "_context", return_value=(client, {"agent_product_filter": "sdk-product"})),
            patch("apps.core.trace_query.signoz.provider.require_default_subject", return_value="bound-subject"),
            patch("apps.core.trace_query.langfuse.native.time.time_ns", return_value=self.second_window[1] * 1000000),
            patch.object(self.provider, "collect_traces", wraps=self.provider.collect_traces) as collect,
            patch("apps.core.trace_query.langfuse.native.project_trace", wraps=project_trace) as projection,
        ):
            result = self.service.sessions({}, session_id)
        self.projected = [call.args[0] for call in projection.call_args_list]
        return result, collect.call_args_list

    def test_session_id_selects_candidates_then_reads_complete_joined_trace_history(self):
        result, queries = self.read()
        selectors = [call.args[0] for call in queries if call.args[0].get_param("session_id")]
        self.assertEqual(len(selectors), 2)
        self.assertTrue(all(query.get_param("session_id") == "wanted" for query in selectors))
        self.assertEqual(result["id"], "wanted")
        self.assertEqual(timestamp_ns(result["createdAt"]), timestamp_ns("2026-09-15T00:00:00Z"))
        self.assertEqual([trace["id"] for trace in result["traces"]], ["joined", "second"])
        self.assertTrue(all(trace["userId"] == "business-user" for trace in result["traces"]))
        joined = next(trace for trace in result["traces"] if trace["id"] == "joined")
        self.assertEqual(joined["name"], "root")
        joined_detail = next(detail for detail in self.projected if detail["traceId"] == "joined")
        self.assertEqual({span["span_id"] for span in joined_detail["spans"]},
                         {"root", "session-child", "later"})
        details = [(path, params) for path, params in self.calls if not path.endswith("/traces")]
        self.assertNotIn("/api/v1/ai-vision/traces/unrelated", [path for path, _ in details])
        self.assertTrue(all("session_id" not in params for _, params in details))
        self.assertTrue(any(path.endswith("/joined") and params["from"] == self.first_window[0] for path, params in details))
        for query in (call.args[0] for call in queries):
            self.assertIs(query.space, self.space)
            self.assertIs(query.user, self.user)
            self.assertEqual(query.agent_uuid, "agent")
            self.assertEqual(query.agent_product, "langfuse")
        for _, params in self.calls:
            self.assertEqual(params["space_id"], "space")
            self.assertEqual(params["user"], "bound-subject")
            self.assertEqual(params["agent_product"], "sdk-product")
            self.assertLessEqual(params["to"] - params["from"], 31 * 86400000)
            self.assertNotIn("filters", params)

    def test_session_selection_keeps_full_trace_span_count_guard(self):
        for missing_span in ("root", "later"):
            def truncated(path, *, params):
                result = self.storage(path, params=params)
                if path.endswith("/joined"):
                    result["spans"] = [span for span in result["spans"] if span["span_id"] != missing_span]
                return result
            with self.subTest(missing_span=missing_span), self.assertRaises(TraceQueryServiceError) as caught:
                self.read(storage=truncated)
            self.assertEqual(caught.exception.code, 3035)

    def test_session_candidate_span_count_must_match_selected_spans(self):
        def changed(path, *, params):
            result = self.storage(path, params=params)
            if path.endswith("/traces") and params.get("session_id"):
                for row in result["data"]:
                    row["span_count"] += 1
            return result
        with self.assertRaises(TraceQueryServiceError) as caught:
            self.read(storage=changed)
        self.assertEqual(caught.exception.code, 3035)

    def test_session_candidate_pagination_must_be_complete(self):
        def incomplete(path, *, params):
            result = self.storage(path, params=params)
            if path.endswith("/traces") and params.get("session_id"):
                result["meta"]["totalItems"] += 1
            return result
        with self.assertRaises(TraceQueryServiceError) as caught:
            self.read(storage=incomplete)
        self.assertEqual(caught.exception.code, 3035)

    def test_session_selection_still_rejects_foreign_space_child(self):
        self.fragments[self.first_window]["joined"][0]["resource"]["aivision.space.id"] = "foreign-space"
        with self.assertRaises(TraceQueryServiceError) as caught:
            self.read()
        self.assertEqual(caught.exception.code, 3035)

    def test_session_selection_still_rejects_mixed_agent_child(self):
        self.fragments[self.second_window]["joined"][0]["resource"]["aivision.agent.id"] = "other-agent"
        with self.assertRaises(TraceQueryServiceError) as caught:
            self.read()
        self.assertEqual(caught.exception.code, 3035)

    def test_unknown_session_does_not_fetch_any_trace_details(self):
        with self.assertRaises(TraceQueryServiceError) as caught:
            self.read(session_id="missing")
        self.assertEqual(caught.exception.code, 3019)
        self.assertTrue(all(path.endswith("/traces") for path, _ in self.calls))
