from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from apps.core.event_query.signoz import SignozEventProvider, _filter_log, _canonical_log, _SigNozEventClient
from apps.core.event_query.filters import FilterSpec, FilterOperator
from apps.core.event_query.types import EventListQuery, EventMetadataQuery, EventFacetQuery
from apps.core.trace_query.types import TraceQueryServiceError


class EventFieldContractTests(SimpleTestCase):
    def test_like_wildcards_escaping_and_repeated_wildcard_failure(self):
        for body, pattern, expected in (("abc", "a_c", True), ("a\nlongc", "a%c", True),
                ("a%c", r"a\%c", True), ("a_c", r"a\_c", True), ("a[c]*?", "a[c]*?", True),
                ("abc", "a[bc]", False), ("a" * 10000, "%a" * 100 + "b", False)):
            self.assertEqual(_filter_log({"body": body}, FilterSpec("body", FilterOperator.LIKE, pattern)), expected)

    def test_canonical_json_text_fields_do_not_change_type(self):
        self.assertEqual(_canonical_log(self.row(attributes={"status_code": 0}))["status"], "0")

    @patch("apps.core.event_query.signoz._SigNozEventClient.post")
    def test_invalid_filters_fail_even_before_an_empty_scan(self, post):
        for field, op, value in (("body", "LIKE", 42), ("body", "IN", []), ("body", "=", {}),
                ("time", ">=", "2026-99-99T00:00:00Z"), ("time", ">=", "1e30")):
            with self.assertRaises(TraceQueryServiceError):
                self.provider.list_events(self.query(filters=[{"field": field, "op": op, "value": value}]))
        for field in ([], {}, "unapproved_field"):
            with self.assertRaises(TraceQueryServiceError):
                self.provider.list_events(self.query(filters=[{"field": field, "op": "=", "value": "x"}]))
        post.assert_not_called()

    @patch("apps.core.event_query.signoz._SigNozEventClient.post")
    def test_facets_share_builder_field_filter_and_null_semantics(self, post):
        post.side_effect = [self.count(2), self.page([self.row(), self.row("b", severity_text=None)]), self.count(2)]
        result = self.provider.list_facets(EventFacetQuery(self.space, None, {
            "from": 1788930000000, "to": 1788930060000, "dimensions": ["severity_text"],
            "filters": [{"field": "body", "op": "=", "value": "failed"}]}))
        self.assertEqual({row["value"] for row in result["data"]["severity_text"]}, {"ERROR", None})
        self.assertTrue(result["meta"]["complete"])

    @patch("apps.core.event_query.signoz._SigNozEventClient.post")
    @patch("apps.core.event_query.signoz.require_agent_subject", return_value="target-subject")
    @patch("apps.core.event_query.signoz.resolve_space_agent_for_query")
    def test_personal_agent_subject_and_every_scope_dimension_are_verified(self, resolve, subject, post):
        self.space.type = 1
        resolve.return_value = SimpleNamespace(uuid="target-agent")
        good = self.row(user_id="target-subject", resource_attributes={"aivision.space.id": "space-1", "aivision.agent.id": "target-agent"})
        post.side_effect = [self.count(1), self.page([good]), self.count(1)]
        self.provider.list_events(self.query(agent_uuid="target-agent", agent_product="framework"))
        expression = post.call_args.kwargs["payload"]["compositeQuery"]["queries"][0]["spec"]["filter"]["expression"]
        self.assertIn("user_id = 'target-subject'", expression)
        self.assertIn("resource.aivision.agent.id = 'target-agent'", expression)
        self.assertEqual(resolve.call_args.kwargs["active_statuses"], (1, 2))
        for bad in ({**good, "user_id": "other"}, {**good, "agent_product": "other"},
                    {**good, "resource_attributes": {"aivision.agent.id": "other"}}):
            post.side_effect = [self.count(1), self.page([bad])]
            with self.assertRaises(TraceQueryServiceError):
                self.provider.list_events(self.query(agent_uuid="target-agent", agent_product="framework"))

    @patch("apps.core.event_query.signoz._SigNozEventClient.post")
    def test_repeated_cursor_storage_warning_and_error_do_not_become_empty_success(self, post):
        post.side_effect = [self.count(3), self.page([self.row("a")], "same"), self.page([self.row("b")], "same")]
        with self.assertRaises(TraceQueryServiceError):
            self.provider.list_events(self.query())
        post.side_effect = [{**self.count(0), "warning": {"warnings": [{"message": "truncated"}]}}]
        with self.assertRaises(TraceQueryServiceError):
            self.provider.list_events(self.query())
        response = Mock(status_code=200)
        response.json.return_value = {"status": "error", "data": {"results": []}}
        with self.assertRaises(TraceQueryServiceError):
            _SigNozEventClient._decode_response(response)

    def setUp(self):
        self.provider = SignozEventProvider(SimpleNamespace(
            config={"endpoint": "http://signoz", "agent_product_filter": "framework"},
            source_system="signoz", is_default=1))
        self.space = SimpleNamespace(uuid="space-1", type=2)

    @patch("apps.core.event_query.signoz._SigNozEventClient.post")
    def test_body_and_severity_can_be_projected_and_filtered(self, post):
        post.side_effect = [self.count(1), self.page([self.row()]), self.count(1)]
        result = self.provider.list_events(EventListQuery(self.space, None, {
            "from": 1788930000000, "to": 1788930060000,
            "fields": ["body", "severity_text"],
            "filters": [{"field": "severity_text", "op": "=", "value": "ERROR"}],
        }))
        self.assertEqual(result["data"][0]["body"], "failed")
        self.assertEqual(post.call_args_list[1].args[0], "/api/v5/query_range")
        expression = post.call_args.kwargs["payload"]["compositeQuery"]["queries"][0]["spec"]["filter"]["expression"]
        self.assertIn("space_id = 'space-1'", expression)
        self.assertIn("agent_product = 'framework'", expression)
        self.assertTrue(result["meta"]["complete"])

    @staticmethod
    def count(value):
        return {"type": "scalar", "data": {"results": [{"queryName": "events", "data": [[value]]}]}}

    @staticmethod
    def page(rows, cursor=""):
        return {"type": "raw", "data": {"results": [{"queryName": "events", "rows": [{"data": row} for row in rows], "nextCursor": cursor}]}}

    @staticmethod
    def row(identifier="log-1", **extra):
        return {"log_id": identifier, "space_id": "space-1", "agent_product": "framework", "user_id": "subject",
                "timestamp": 1788930000000000000, "body": "failed", "severity_text": "ERROR",
                "event_name": "synthetic", "attributes": {}, "resource_attributes": {"aivision.space.id": "space-1"}, **extra}

    def query(self, **params):
        return EventListQuery(self.space, None, {"from": 1788930000000, "to": 1788930060000,
            "fields": ["body", "severity_text"], **params})

    @patch("apps.core.event_query.signoz._SigNozEventClient.post")
    def test_builder_reads_all_pages_before_filter_and_public_pagination(self, post):
        post.side_effect = [self.count(3), self.page([self.row("a")], "next"),
                            self.page([self.row("b", severity_text="INFO"), self.row("c")]), self.count(3)]
        result = self.provider.list_events(self.query(limit=1, page=2,
            filters=[{"field": "severity_text", "op": "=", "value": "ERROR"}]))
        self.assertEqual(result["meta"]["totalItems"], 2)
        self.assertEqual(len(result["data"]), 1)
        spec = post.call_args_list[2].kwargs["payload"]["compositeQuery"]["queries"][0]["spec"]
        self.assertEqual(spec["cursor"], "next")
        self.assertEqual(spec["offset"], 1)

    @patch("apps.core.event_query.signoz._SigNozEventClient.post")
    def test_builder_rejects_wrong_scope_repeated_ids_and_changed_counts(self, post):
        for replies in ([self.count(1), self.page([self.row(space_id="other")])],
                        [self.count(2), self.page([self.row(), self.row()])],
                        [self.count(1), self.page([self.row()]), self.count(2)],
                        [self.count(10001)],
                        [self.count(1), {"type": "raw", "data": {"results": []}}]):
            post.side_effect = replies
            with self.assertRaises(TraceQueryServiceError):
                self.provider.list_events(self.query())

    @patch("apps.core.event_query.signoz._SigNozEventClient.post")
    def test_builder_preserves_null_severity_and_inclusive_event_time_boundary(self, post):
        post.side_effect = [self.count(2), self.page([self.row(severity_text=None), self.row("last", timestamp=1788930060000000000)]), self.count(2)]
        result = self.provider.list_events(self.query())
        self.assertEqual(result["meta"]["totalItems"], 2)
        self.assertIsNone(result["data"][1]["severity_text"])

    @patch("apps.core.event_query.signoz._SigNozEventClient.post")
    def test_closed_event_window_excludes_submillisecond_envelope_and_rejects_zero_width(self, post):
        end = 1788930060000
        post.side_effect = [self.count(2), self.page([
            self.row("edge", timestamp=end * 1000000),
            self.row("after", timestamp=end * 1000000 + 1),
        ]), self.count(2)]
        result = self.provider.list_events(self.query(**{"from": end - 1, "to": end}))
        self.assertEqual(result["meta"]["totalItems"], 1)
        for call in post.call_args_list:
            self.assertEqual(call.kwargs["payload"]["end"], end + 1)
        post.reset_mock()
        post.side_effect = [self.count(0), self.count(0)]
        with self.assertRaises(TraceQueryServiceError):
            self.provider.list_events(self.query(**{"from": end, "to": end}))
        post.assert_not_called()

    @patch("apps.core.event_query.signoz._SigNozEventClient.post")
    @patch("apps.core.event_query.signoz.resolve_space_agent_for_query")
    def test_foreign_agent_is_rejected_before_any_storage_query(self, resolve, post):
        resolve.return_value = None
        with self.assertRaises(TraceQueryServiceError):
            self.provider.list_events(self.query(agent_uuid="other", agent_product="framework"))
        post.assert_not_called()

    @patch("apps.core.event_query.signoz.requests.get")
    def test_public_metadata_does_not_advertise_unqueryable_storage_columns(self, get):
        get.return_value = Mock(status_code=200)
        get.return_value.json.return_value = {"canonicalFields": [
            {"key": "body", "type": "string"}, {"key": "unsupported_storage_column", "type": "json"}
        ], "fields": [{"name": "body", "type": "text"}, {"name": "severity_text", "type": "string"}]}
        result = self.provider.get_metadata(EventMetadataQuery(self.space, None, {}))
        keys = {field["key"] for field in result["eventSchema"]["fields"]}
        self.assertIn("body", keys)
        self.assertIn("severity_text", keys)
        self.assertNotIn("unsupported_storage_column", keys)

    @patch("apps.core.event_query.signoz.requests.post")
    def test_malformed_filter_member_is_rejected_before_query(self, post):
        post.return_value = Mock(status_code=200)
        post.return_value.json.return_value = {"data": []}
        with self.assertRaises(TraceQueryServiceError):
            self.provider.list_events(EventListQuery(self.space, None, {
                "from": 1788930000000, "to": 1788930060000,
                "filters": ["status=ERROR"],
            }))
        post.assert_not_called()
