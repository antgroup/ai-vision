import json
"""Session list totals must describe the complete canonical billable calls."""

from copy import deepcopy
from unittest.mock import Mock, patch

from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import AgentTypeConfig, DataSource, Space
from apps.core.tests.helpers import create_local_user, create_test_agent
from apps.core.trace_query.signoz.provider import SignozTraceProvider
from apps.core.trace_query.types import (
    BaseTraceQuery,
    SessionListQuery,
    SessionUsageBatchQuery,
    TraceQueryServiceError,
)
from apps.core.views.trace_handlers import (
    enrich_list_with_session_usage,
    merge_session_list_params,
)
from apps.core.views.trace_unified import UnifiedSessionUsageBatchView


def span(trace_id, span_id, session_id, attrs=None, *, kind="GENERATION", parent=None):
    return {
        "trace_id": trace_id,
        "span_id": span_id,
        "parent_span_id": parent,
        "session_id": session_id,
        "name": "fixture call",
        "space_id": "session-list-space",
        "agent_product": "sdk-agent",
        "user_id": "alice",
        "start_time_unix_nano": "1790985600000000000",
        "end_time_unix_nano": "1790985600100000000",
        "status": {"code": 1},
        "resource": {
            "aivision.space.id": "session-list-space",
            "aivision.agent.id": "session-list-agent",
            "ant.agent.product": "sdk-agent",
            "ant.agent.env": "prod",
            "ant.agent.scene": "coding",
        },
        "attributes": {
            "langfuse.observation.type": kind,
            "langfuse.session.id": session_id,
            "gen_ai.request.model": "fixture-model",
            "gen_ai.provider.name": "fixture-provider",
            "langfuse.trace.tags": ["sdk"],
            **(attrs or {}),
        },
        "events": [],
        "links": [],
    }


def trace(trace_id, session_id, attrs=None, *, kind="GENERATION"):
    return {
        "traceId": trace_id,
        "sessionId": session_id,
        "spans": [span(trace_id, trace_id + "-call", session_id, attrs, kind=kind)],
    }


def known_usage(input_tokens=338, output_tokens=95, total_tokens=433):
    return {
        "gen_ai.usage.input_tokens": input_tokens,
        "gen_ai.usage.output_tokens": output_tokens,
        "gen_ai.usage.total_tokens": total_tokens,
    }


@override_settings(AIVISION_MODEL_PRICES=[])
class NativeSessionListUsageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = create_local_user("native-session-list")
        cls.space = Space.objects.create(
            uuid="session-list-space", name="sessions", type=2, creator=cls.user
        )
        kind = AgentTypeConfig.objects.create(
            agent_name="session-list-fixture", agent_product="langfuse"
        )
        cls.agent = create_test_agent(
            uuid="session-list-agent",
            space=cls.space,
            agent_type=kind,
            agent_product="langfuse",
            name="fixture",
        )
        DataSource.objects.create(
            agent_type=kind,
            datasource_type="traces",
            source_system="signoz",
            enabled=1,
            config={
                "endpoint": "http://signoz-query.invalid",
                "api_key": "fixture",
                "agent_product_filter": "sdk-agent",
            },
        )

    def setUp(self):
        self.provider = SignozTraceProvider()
        self.params = {
            "from": 1790985600000,
            "to": 1791072000000,
            "agent_product": "langfuse",
            "agent_uuid": self.agent.uuid,
        }

    def list_sessions(self, traces, *, params=None, scan_error=None, complete=True):
        captured = []

        def collect(query):
            captured.append(query)
            if scan_error is not None:
                raise scan_error
            return {
                "data": deepcopy(traces),
                "meta": {
                    "complete": complete,
                    "totalItems": len(traces),
                    "from": query.get_param("from"),
                    "to": query.get_param("to"),
                    "timeBoundary": "fromInclusiveToExclusive",
                },
            }

        query = SessionListQuery(
            self.space,
            self.user,
            {**self.params, "session_id": "session-a"} if params is None else params,
        )
        with (
            patch(
                "apps.core.trace_query.signoz.provider.requests.get",
                side_effect=AssertionError(
                    "session list must use the verified trace window"
                ),
            ),
            patch.object(SignozTraceProvider, "collect_traces", side_effect=collect),
        ):
            result = self.provider.list_sessions(query)
        return result, captured

    def assert_usage(self, row, totals, coverage):
        self.assertEqual(
            (row["input_tokens"], row["output_tokens"], row["total_tokens"]), totals
        )
        self.assertIn("usage", row)
        self.assertEqual(
            (row["usage"]["input"], row["usage"]["output"], row["usage"]["total"]),
            totals,
        )
        self.assertEqual(row["coverage"], coverage)

    def test_two_generations_use_full_session_totals(self):
        first = known_usage()
        first["langfuse.observation.cost_details"] = {"total": "0.02"}
        result, _ = self.list_sessions(
            [
                trace("first", "session-a", first),
                trace("second", "session-a", known_usage(162, 5, 167)),
                trace("unrelated", "other-session", known_usage(700, 30, 730)),
            ]
        )
        self.assert_usage(
            result["data"][0],
            (500, 100, 600),
            {
                "billableCalls": 2,
                "knownUsageCalls": 2,
                "knownTokens": 600,
                "knownCostCalls": 1,
            },
        )
        self.assertEqual(result["data"][0]["usage"]["observationCount"], 2)
        self.assertIsNone(result["data"][0]["usage"]["cost"]["total"])

    def test_partial_usage_keeps_known_subtotal_only_in_coverage(self):
        result, _ = self.list_sessions(
            [trace("known", "session-a", known_usage()), trace("missing", "session-a")]
        )
        self.assert_usage(
            result["data"][0],
            (None, None, None),
            {
                "billableCalls": 2,
                "knownUsageCalls": 1,
                "knownTokens": 433,
                "knownCostCalls": 0,
            },
        )
        self.assertEqual(result["data"][0]["usage"]["unknownReason"], "usage_missing")

    def test_list_usage_batch_and_metrics_share_canonical_session_identity(self):
        value = trace("identity-trace", "application-session", known_usage())
        value["spans"][0]["session_id"] = "storage-session"
        scan = {
            "data": [value],
            "meta": {
                "complete": True,
                "totalItems": 1,
                "from": 1790985600000,
                "to": 1791072000000,
                "timeBoundary": "fromInclusiveToExclusive",
            },
        }
        # This fixture exercises canonical aggregation through legacy storage;
        # optional snapshot capabilities are unavailable on that server.
        with (
            patch(
                "apps.core.trace_query.signoz.provider.requests.get",
                return_value=Mock(status_code=404),
            ),
            patch.object(SignozTraceProvider, "collect_traces", return_value=scan),
        ):
            listing = self.provider.list_sessions(
                SessionListQuery(
                    self.space,
                    self.user,
                    {**self.params, "session_id": "application-session"},
                )
            )
            batch = self.provider.get_session_usage_batch(
                SessionUsageBatchQuery(
                    self.space, self.user, self.params, ["application-session"]
                )
            )
            metrics = self.provider.query_metrics(
                BaseTraceQuery(
                    self.space,
                    self.user,
                    {
                        **self.params,
                        "filters": [
                            {
                                "field": "session_id",
                                "op": "=",
                                "value": "application-session",
                            }
                        ],
                    },
                )
            )
        self.assertEqual(value["spans"][0]["session_id"], "storage-session")
        self.assertEqual(
            [row["session_id"] for row in listing["data"]], ["application-session"]
        )
        self.assert_usage(
            listing["data"][0],
            (338, 95, 433),
            {
                "billableCalls": 1,
                "knownUsageCalls": 1,
                "knownTokens": 433,
                "knownCostCalls": 0,
            },
        )
        self.assertEqual(batch["data"][0]["id"], "application-session")
        self.assertEqual(batch["data"][0]["totalTokens"], 433)
        self.assertEqual(metrics["usage"]["total"], 433)
        self.assertEqual(
            metrics["coverage"],
            {
                "billableCalls": 1,
                "knownUsageCalls": 1,
                "knownTokens": 433,
                "knownCostCalls": 0,
            },
        )

    def test_all_missing_usage_is_unknown(self):
        result, _ = self.list_sessions(
            [trace("missing-a", "session-a"), trace("missing-b", "session-a")]
        )
        self.assert_usage(
            result["data"][0],
            (None, None, None),
            {
                "billableCalls": 2,
                "knownUsageCalls": 0,
                "knownTokens": 0,
                "knownCostCalls": 0,
            },
        )

    def test_explicit_zero_usage_is_known(self):
        result, _ = self.list_sessions(
            [trace("zero", "session-a", known_usage(0, 0, 0))]
        )
        self.assert_usage(
            result["data"][0],
            (0, 0, 0),
            {
                "billableCalls": 1,
                "knownUsageCalls": 1,
                "knownTokens": 0,
                "knownCostCalls": 0,
            },
        )
        self.assertIsNone(result["data"][0]["usage"]["unknownReason"])

    def test_session_with_no_billable_calls_is_unknown(self):
        result, _ = self.list_sessions(
            [trace("agent", "session-a", known_usage(), kind="AGENT")]
        )
        self.assert_usage(
            result["data"][0],
            (None, None, None),
            {
                "billableCalls": 0,
                "knownUsageCalls": 0,
                "knownTokens": 0,
                "knownCostCalls": 0,
            },
        )

    def test_sdk_exclusive_cache_ignores_parent_rollup_and_duplicate_spans(self):
        root = span(
            "sdk", "root", "session-a", known_usage(1000, 1000, 2000), kind="AGENT"
        )
        call = span(
            "sdk",
            "call",
            "session-a",
            {
                "langfuse.observation.usage_details": {
                    "input": 474,
                    "input_cache_read": 256,
                    "output": 62,
                    "total": 792,
                }
            },
            parent="root",
        )
        result, _ = self.list_sessions(
            [{"traceId": "sdk", "spans": [root, call, deepcopy(call)]}]
        )
        self.assert_usage(
            result["data"][0],
            (730, 62, 792),
            {
                "billableCalls": 1,
                "knownUsageCalls": 1,
                "knownTokens": 792,
                "knownCostCalls": 0,
            },
        )
        self.assertEqual(result["data"][0]["usage"]["cacheReadInput"], 256)

    def test_nested_generations_keep_ambiguity_with_known_coverage(self):
        parent = span("nested", "parent", "session-a", known_usage())
        child = span(
            "nested", "child", "session-a", known_usage(10, 3, 13), parent="parent"
        )
        result, _ = self.list_sessions(
            [{"traceId": "nested", "spans": [parent, child]}]
        )
        self.assert_usage(
            result["data"][0],
            (None, None, None),
            {
                "billableCalls": 2,
                "knownUsageCalls": 2,
                "knownTokens": 446,
                "knownCostCalls": 0,
            },
        )
        self.assertEqual(
            result["data"][0]["usage"]["unknownReason"], "ambiguous_nested_generations"
        )

    def test_page_order_and_metadata_come_from_one_verified_window(self):
        params = {**self.params, "page": 2, "limit": 2, "orderBy": "last_activity.desc"}
        details = []
        for index in range(7):
            value = trace(
                f"trace-{index}",
                f"session-{index}",
                known_usage(index + 10, 2, index + 12),
            )
            value["spans"][0].update(
                start_time_unix_nano=str(1790985600000000000 + index * 1000000000),
                end_time_unix_nano=str(1790985600000000000 + (index + 1) * 1000000000),
            )
            details.append(value)
        extra = trace("session-4-second", "session-4", known_usage(20, 3, 23))
        extra["spans"][0].update(
            start_time_unix_nano="1790985604000000000",
            end_time_unix_nano="1790985605000000000",
        )
        details.append(extra)
        result, scans = self.list_sessions(details, params=params)
        self.assertEqual(
            [row["session_id"] for row in result["data"]], ["session-4", "session-3"]
        )
        self.assertEqual(
            {
                key: result["meta"][key]
                for key in ("page", "limit", "totalItems", "totalPages")
            },
            {"page": 2, "limit": 2, "totalItems": 7, "totalPages": 4},
        )
        self.assertTrue(result["meta"].get("complete"))
        self.assertEqual(result["meta"].get("from"), 1790985600000)
        self.assertEqual(result["meta"].get("to"), 1791072000000)
        self.assertEqual(result["meta"].get("timeBoundary"), "fromInclusiveToExclusive")
        self.assertEqual(result["meta"].get("agent_uuid"), self.agent.uuid)
        self.assertEqual(result["meta"].get("agent_product"), "langfuse")
        self.assertEqual(result["facets"]["env"], [{"value": "prod", "count": 7}])
        row = result["data"][0]
        self.assertEqual((row["trace_count"], row["span_count"]), (2, 2))
        self.assertEqual(
            (row["username"], row["environment"], row["scene"], row["scenes"]),
            ("alice", "prod", "coding", ["coding"]),
        )
        self.assertEqual(
            (row["created_at"], row["last_activity"]),
            ("2026-10-03T00:00:04Z", "2026-10-03T00:00:05Z"),
        )
        self.assert_usage(
            row,
            (34, 5, 39),
            {
                "billableCalls": 2,
                "knownUsageCalls": 2,
                "knownTokens": 39,
                "knownCostCalls": 0,
            },
        )
        self.assert_usage(
            result["data"][1],
            (13, 2, 15),
            {
                "billableCalls": 1,
                "knownUsageCalls": 1,
                "knownTokens": 15,
                "knownCostCalls": 0,
            },
        )
        self.assertEqual(len(scans), 1)
        self.assertEqual(dict(scans[0].query_params), self.params)

    def test_implicit_time_bounds_are_frozen_before_verified_scan(self):
        params = {"agent_product": "langfuse", "agent_uuid": self.agent.uuid}
        result, scans = self.list_sessions(
            [trace("known", "session-a", known_usage())], params=params
        )
        self.assertEqual(len(scans), 1)
        scan_params = scans[0].query_params
        self.assertIn("from", scan_params)
        self.assertIn("to", scan_params)
        self.assertEqual(scan_params["to"] - scan_params["from"], 86400000)
        self.assertEqual(result["meta"]["from"], scan_params["from"])
        self.assertEqual(result["meta"]["to"], scan_params["to"])
        self.assertEqual(scans[0].agent_uuid, self.agent.uuid)
        self.assertEqual(scans[0].space.uuid, self.space.uuid)
        self.assertEqual(
            params, {"agent_product": "langfuse", "agent_uuid": self.agent.uuid}
        )
        self.assertEqual(result["data"][0]["total_tokens"], 433)

    def test_scan_failure_does_not_fall_back_to_storage_totals(self):
        with self.assertRaises(TraceQueryServiceError) as caught:
            self.list_sessions(
                [],
                scan_error=TraceQueryServiceError(
                    "Incomplete trace detail; retry", 3035
                ),
            )
        self.assertEqual(caught.exception.code, 3035)

    def test_listed_trace_disappearing_before_detail_is_an_incomplete_scan(self):
        trace_response = Mock(status_code=200)
        trace_response.raise_for_status.return_value = None
        trace_response.json.return_value = {
            "data": [{"trace_id": "listed-trace", "span_count": 1}],
            "meta": {"page": 1, "limit": 200, "totalItems": 1, "totalPages": 1},
        }
        trace_response.headers = {}
        trace_response.iter_content.return_value = [json.dumps(trace_response.json.return_value).encode()]
        missing_detail_response = Mock(status_code=404)
        with patch(
            "apps.core.trace_query.signoz.provider.requests.get",
            side_effect=[trace_response, missing_detail_response],
        ):
            with self.assertRaises(TraceQueryServiceError) as caught:
                self.provider.list_sessions(
                    SessionListQuery(self.space, self.user, self.params)
                )
        self.assertEqual(caught.exception.code, 3035)

    def test_incomplete_scan_does_not_publish_canonical_totals(self):
        with self.assertRaises(TraceQueryServiceError) as caught:
            self.list_sessions(
                [trace("known", "session-a", known_usage())], complete=False
            )
        self.assertEqual(caught.exception.code, 3035)

    def test_missing_requested_session_is_a_verified_empty_result(self):
        result, scans = self.list_sessions(
            [trace("other", "other-session", known_usage())]
        )
        self.assertEqual(result["data"], [])
        self.assertEqual(result["meta"]["totalItems"], 0)
        self.assertTrue(result["meta"]["complete"])
        self.assertEqual(len(scans), 1)

    def test_empty_window_is_verified_before_returning_empty_list(self):
        result, scans = self.list_sessions([])
        self.assertEqual(result["data"], [])
        self.assertEqual(
            {
                key: result["meta"][key]
                for key in ("page", "limit", "totalItems", "totalPages")
            },
            {"page": 1, "limit": 50, "totalItems": 0, "totalPages": 0},
        )
        self.assertTrue(result["meta"]["complete"])
        self.assertEqual(len(scans), 1)
        self.assertEqual(
            (result["meta"]["from"], result["meta"]["to"]),
            (1790985600000, 1791072000000),
        )

    def test_session_list_body_preserves_requested_agent_uuid(self):
        result = merge_session_list_params(
            {"agent_uuid": "requested-instance", "agent_product": "langfuse"}, {}
        )
        self.assertEqual(result.get("agent_uuid"), "requested-instance")

    def test_session_usage_view_passes_body_instance_to_service(self):
        request = APIRequestFactory().post(
            "/sessions/usage",
            {
                "sessionIds": ["session-a"],
                "agent_product": "langfuse",
                "agent_uuid": self.agent.uuid,
                "from": 1790985600000,
                "to": 1791072000000,
            },
            format="json",
        )
        force_authenticate(request, self.user)
        service = Mock()
        service.get_session_usage_batch.return_value = {"data": []}
        with (
            patch("apps.core.views.trace_unified._check_access", return_value=None),
            patch(
                "apps.core.views.trace_unified.get_default_trace_query_service",
                return_value=service,
            ),
        ):
            response = UnifiedSessionUsageBatchView.as_view(source_system="signoz")(
                request, space_id=self.space.uuid
            )
        self.assertEqual(response.data["code"], 0)
        routed = service.get_session_usage_batch.call_args.kwargs
        self.assertEqual(routed["query_params"].get("agent_uuid"), self.agent.uuid)
        self.assertEqual(routed["space"].uuid, self.space.uuid)
        self.assertEqual(routed["session_ids"], ["session-a"])

    def test_usage_enrichment_keeps_native_projection_without_second_scan(self):
        data = {
            "data": [
                {
                    "session_id": "session-a",
                    "input_tokens": None,
                    "output_tokens": None,
                    "total_tokens": None,
                    "usage": {"input": None, "output": None, "total": None},
                    "coverage": {
                        "billableCalls": 2,
                        "knownUsageCalls": 1,
                        "knownTokens": 433,
                        "knownCostCalls": 0,
                    },
                }
            ]
        }
        original = deepcopy(data)
        with patch(
            "apps.core.views.trace_handlers.query_session_usage_batch",
            side_effect=AssertionError("native canonical usage must not re-scan"),
        ):
            result = enrich_list_with_session_usage(
                data, self.space, self.user, self.params, "signoz"
            )
        self.assertEqual(result, original)
