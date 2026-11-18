"""The list boundary must read one complete page, independent of window size."""

from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from apps.core.tests.test_native_query import trace
from apps.core.trace_query.native_query import NativeTraceWindow
from apps.core.trace_query.signoz.pagination import CONTRACT
from apps.core.trace_query.signoz.provider import SignozTraceProvider
from apps.core.trace_query.types import TraceListQuery, TraceQueryServiceError


class NativeTracePaginationTests(SimpleTestCase):
    def setUp(self):
        self.provider = SignozTraceProvider()
        snapshot = patch('apps.core.trace_query.signoz.snapshot_window.query_snapshot_window', return_value=None)
        snapshot.start()
        self.addCleanup(snapshot.stop)
        self.space = SimpleNamespace(uuid="space", type=2)
        self.params = {
            "from": 1790985600000, "to": 1791072000000,
            "agent_product": "public-product", "agent_uuid": "agent-a",
            "page": 1, "limit": 20,
        }
        self.client = Mock()
        context = patch.object(SignozTraceProvider, "_context", return_value=(
            self.client, {"agent_product_filter": "stored-product"},
        ))
        context.start()
        self.addCleanup(context.stop)

    def query(self, **overrides):
        return TraceListQuery(self.space, None, {**self.params, **overrides})

    def detail(self, index, *, usage=True):
        value = trace(index, usage=usage)
        value["traceId"] = value["spans"][0]["trace_id"] = f"trace-{index:06d}"
        value["spans"][0]["resource"] = {
            "aivision.space.id": "space", "aivision.agent.id": "agent-a",
            "ant.agent.env": "prod",
        }
        return {**value, "spanCount": len(value["spans"])}

    def payload(self, total=211, *, page=1, limit=20):
        indices = list(range(total - 1, max(-1, total - page * limit - 1), -1))
        indices = indices[(page - 1) * limit:]
        return {
            "data": [self.detail(index) for index in indices],
            "meta": {
                "contract": CONTRACT, "complete": True,
                "environmentSemantics": "root-span-resource-v1",
                "page": page, "limit": limit, "totalItems": total,
                "totalPages": (total + limit - 1) // limit,
                "from": self.params["from"], "to": self.params["to"],
                "space_id": "space", "agent_product": "stored-product",
                "agent_uuid": "agent-a", "user": "",
            },
            "facets": {"env": [{"value": "prod", "count": total}] if total else [],
                       "scene": [], "username": [], "tags": []},
        }

    def assert_canonical_page(self, result, details, query, expected_ids, mode):
        window = NativeTraceWindow(details, project_id="space", include_previews=True)
        expected = window.list(query.query_params)
        self.assertEqual([row["trace_id"] for row in result["data"]], expected_ids)
        self.assertEqual(result["data"], expected["data"])
        expected_facets = window.facets(query.query_params)
        if mode == "storage":
            # The fixture models a legacy storage server without public SDK
            # identity facets. Canonical candidates are fetched independently.
            expected_facets.pop("username")
        self.assertEqual(result["facets"], expected_facets)
        for key, value in expected["meta"].items():
            self.assertEqual(result["meta"][key], value, key)
        self.assertEqual(result["meta"]["totalItems"], len(expected_ids))
        self.assertEqual(result["meta"]["queryMode"], mode)
        self.assertEqual(result["meta"]["filters"], query.get_param("filters", []))
        self.assertEqual(result["meta"]["from"], self.params["from"])
        self.assertEqual(result["meta"]["to"], self.params["to"])

    def test_stale_projection_uses_page_before_oversized_window_recovery(self):
        self.params["to"] = self.params["from"] + 15 * 24 * 60 * 60 * 1000
        self.client.get.return_value = self.payload(total=72, limit=1)
        query = self.query(limit=1, fields=["trace_id"])
        with patch("apps.core.trace_query.signoz.read_model.query_read_model",
                   return_value=(None, {"state": "fallback", "reason": "projection_coverage_stale"})), \
             patch("apps.core.trace_query.signoz.snapshot_window.query_snapshot_window",
                   side_effect=TraceQueryServiceError("window exceeds 512 MiB", 3012)) as snapshot:
            result = self.provider.query_traces(query)
        self.assertEqual(result["meta"]["queryMode"], "storage")
        self.assertEqual(result["meta"]["totalItems"], 72)
        self.assertEqual(len(result["data"]), 1)
        self.assertEqual(result["meta"]["readModel"]["reason"], "projection_coverage_stale")
        snapshot.assert_not_called()

    def test_stale_projection_status_filters_keep_root_status_semantics(self):
        detail = self.detail(0)
        child = deepcopy(detail["spans"][0])
        child.update(span_id="failed-tool", parent_span_id=child["span_id"], status={"code": "2"})
        detail["spans"].append(child)
        detail["spanCount"] = 2
        window = NativeTraceWindow([detail], project_id="space", include_previews=True)
        for filters, storage_matches in (
            ([{"field": "status_code", "op": "=", "value": 1}], False),
            ({"status_code": "OK"}, False),
            ({"field": "status_code", "operator": "eq", "value": "ERROR"}, True),
        ):
            with self.subTest(filters=filters):
                # Legacy storage status is MAX(any span), so it omits this
                # successful root from OK and includes it in ERROR results.
                payload = self.payload(total=int(storage_matches))
                payload["data"] = [deepcopy(detail)] if storage_matches else []
                self.client.get.return_value = payload
                query = self.query(filters=filters, fields=["trace_id", "status_code"])
                with patch("apps.core.trace_query.signoz.read_model.query_read_model",
                           return_value=(None, {"state": "fallback", "reason": "projection_coverage_stale"})), \
                     patch("apps.core.trace_query.signoz.snapshot_window.query_snapshot_window",
                           return_value=(window, {"queryMode": "hybrid_snapshot"})):
                    result = self.provider.query_traces(query)
                expected = window.list(query.query_params)
                self.assertEqual(result["data"], expected["data"])
                self.assertEqual(result["meta"]["totalItems"], expected["meta"]["totalItems"])
                self.assertEqual(result["facets"], window.facets(query.query_params))

    def test_stale_projection_status_sort_keeps_global_root_status_order(self):
        failed, recovered = self.detail(0), self.detail(1)
        failed["spans"][0]["status"] = {"code": "2"}
        child = deepcopy(recovered["spans"][0])
        child.update(span_id="failed-tool", parent_span_id=child["span_id"], status={"code": "2"})
        recovered["spans"].append(child)
        recovered["spanCount"] = 2
        window = NativeTraceWindow([failed, recovered], project_id="space", include_previews=True)
        for order in ("status_code.desc", "-status_code", "status_code.asc", "status_code"):
            for page in (1, 2):
                with self.subTest(order=order, page=page):
                    descending = order.startswith("-") or order.endswith(".desc")
                    # Both storage statuses are ERROR; its ID tie-break can
                    # return the wrong global page despite valid local order.
                    storage_order = [recovered, failed] if descending else [failed, recovered]
                    payload = self.payload(total=2, limit=1, page=page)
                    payload["data"] = [deepcopy(storage_order[page - 1])]
                    self.client.get.return_value = payload
                    query = self.query(orderBy=order, page=page, limit=1, fields=["trace_id", "status_code"])
                    with patch("apps.core.trace_query.signoz.read_model.query_read_model",
                               return_value=(None, {"state": "fallback", "reason": "projection_coverage_stale"})), \
                         patch("apps.core.trace_query.signoz.snapshot_window.query_snapshot_window",
                               return_value=(window, {"queryMode": "hybrid_snapshot"})):
                        result = self.provider.query_traces(query)
                    expected = window.list(query.query_params)
                    self.assertEqual(result["data"], expected["data"])
                    self.assertEqual(result["meta"]["totalItems"], 2)

    def test_token_threshold_and_order_use_canonical_totals_across_pages(self):
        details = [self.detail(index, usage=False) for index in (4, 1, 5, 2, 0, 3)]
        for detail, total, seconds in zip(details, (100, 150, None, 99, 0, 150), (2, 0, 5, 4, 3, 1)):
            span = detail["spans"][0]
            if total is not None:
                span["attributes"]["gen_ai.usage.total_tokens"] = total
            span["end_time_unix_nano"] = str(int(span["start_time_unix_nano"]) + seconds * 10**9)
        window = NativeTraceWindow(details, project_id="space", include_previews=True)
        fields = ["trace_id", "total_tokens"]
        threshold = [{"field": "total_tokens", "op": ">=", "value": 100}]

        with patch("apps.core.trace_query.signoz.read_model.query_read_model",
                   return_value=(None, {"state": "fallback", "reason": "projection_coverage_stale"})), \
             patch("apps.core.trace_query.signoz.snapshot_window.query_snapshot_window",
                   return_value=(window, {"queryMode": "hybrid_snapshot"})) as snapshot:
            for direction, expected in (
                ("asc", [(4, 100), (1, 150), (3, 150)]),
                ("desc", [(3, 150), (1, 150), (4, 100)]),
            ):
                with self.subTest(direction=direction):
                    pages = [self.provider.query_traces(self.query(
                        orderBy=f"total_tokens.{direction}", page=page, limit=2,
                        fields=fields, filters=threshold,
                    )) for page in (1, 2)]
                    self.assertEqual(
                        [(row["trace_id"], row["total_tokens"]) for page in pages for row in page["data"]],
                        [(f"trace-{index:06d}", total) for index, total in expected],
                    )
                    for page in pages:
                        self.assertEqual(page["meta"]["totalItems"], 3)
                        self.assertEqual(page["meta"]["totalPages"], 2)
                        self.assertEqual(page["facets"]["env"], [{"value": "prod", "count": 3}])

            for op, value, expected_id in (("=", 0, "trace-000000"), ("IS_NULL", None, "trace-000005")):
                with self.subTest(op=op):
                    result = self.provider.query_traces(self.query(
                        fields=fields, filters=[{"field": "total_tokens", "op": op, "value": value}],
                    ))
                    self.assertEqual(result["meta"]["totalItems"], 1)
                    self.assertEqual(
                        [(row["trace_id"], row["total_tokens"]) for row in result["data"]],
                        [(expected_id, value)],
                    )

            for order, expected_totals in (
                ("total_tokens.asc", [0, 99, 100, 150, 150, None]),
                ("total_tokens.desc", [150, 150, 100, 99, 0, None]),
                ("attributes.gen_ai.usage.total_tokens.desc", [150, 150, 100, 99, 0, None]),
            ):
                with self.subTest(order=order):
                    pages = [self.provider.query_traces(self.query(
                        orderBy=order, page=page, limit=3, fields=fields,
                    )) for page in (1, 2)]
                    self.assertEqual(
                        [row["total_tokens"] for page in pages for row in page["data"]],
                        expected_totals,
                    )

            combined = [*threshold, {"field": "duration_ms", "op": ">=", "value": 1000}]
            for direction, expected_durations in (("asc", [1000, 2000]), ("desc", [2000, 1000])):
                with self.subTest(duration_order=direction):
                    pages = [self.provider.query_traces(self.query(
                        orderBy=f"duration_ms.{direction}", page=page, limit=1,
                        fields=[*fields, "duration_ms"], filters=combined,
                    )) for page in (1, 2)]
                    self.assertEqual([page["data"][0]["duration_ms"] for page in pages], expected_durations)
                    for page in pages:
                        self.assertEqual(page["meta"]["totalItems"], 2)
                        self.assertEqual(page["facets"]["env"], [{"value": "prod", "count": 2}])

            with self.assertRaisesMessage(TraceQueryServiceError, "total_tokens minimum must be from 100"):
                self.provider.query_traces(self.query(
                    filters=[{"field": "total_tokens", "op": ">=", "value": 99}],
                ))

        self.assertEqual(snapshot.call_count, 16)
        self.client.get.assert_not_called()

    def test_root_status_projection_still_uses_page_before_window_recovery(self):
        payload = self.payload(total=1, limit=1)
        root = payload["data"][0]["spans"][0]
        child = deepcopy(root)
        child.update(span_id="failed-tool", parent_span_id=root["span_id"], status={"code": "2"})
        payload["data"][0]["spans"].append(child)
        payload["data"][0]["spanCount"] = 2
        self.client.get.return_value = payload
        with patch("apps.core.trace_query.signoz.read_model.query_read_model",
                   return_value=(None, {"state": "fallback", "reason": "projection_coverage_stale"})), \
             patch("apps.core.trace_query.signoz.snapshot_window.query_snapshot_window",
                   side_effect=TraceQueryServiceError("window exceeds 512 MiB", 3036)) as snapshot:
            result = self.provider.query_traces(self.query(limit=1, fields=["trace_id", "status_code"]))
        self.assertEqual(result["data"][0]["status_code"], 1)
        self.assertEqual(result["meta"]["queryMode"], "storage")
        snapshot.assert_not_called()

    def test_explicit_raw_control_still_prefers_complete_window(self):
        window = NativeTraceWindow([self.detail(0)], project_id="space", include_previews=True)
        with override_settings(AIVISION_QUERY_MODE="raw"), \
             patch("apps.core.trace_query.signoz.snapshot_window.query_snapshot_window",
                   return_value=(window, {"queryMode": "raw_snapshot"})) as snapshot, \
             patch.object(self.provider, "_query_trace_page") as page:
            result = self.provider._query_traces_legacy(self.query())
        page.assert_not_called()
        self.assertTrue(snapshot.call_args.kwargs["raw"])
        self.assertEqual(result["meta"]["queryMode"], "raw_snapshot")

    def test_unsupported_page_retains_window_recovery_without_retry(self):
        self.client.get.return_value = {"supported": False, "reason": "unsupported_query"}
        window = NativeTraceWindow([self.detail(0)], project_id="space", include_previews=True)
        with patch("apps.core.trace_query.signoz.snapshot_window.query_snapshot_window",
                   return_value=(window, {"queryMode": "hybrid_snapshot"})) as snapshot:
            result = self.provider._query_traces_legacy(self.query())
        self.client.get.assert_called_once()
        snapshot.assert_called_once()
        self.assertEqual(result["meta"]["totalItems"], 1)
        self.assertEqual(result["meta"]["queryMode"], "hybrid_snapshot")

    def test_page_transport_failure_is_not_hidden_by_window_recovery(self):
        self.client.get.side_effect = TraceQueryServiceError("forbidden", 3012)
        with patch("apps.core.trace_query.signoz.snapshot_window.query_snapshot_window") as snapshot:
            with self.assertRaisesRegex(TraceQueryServiceError, "forbidden"):
                self.provider._query_traces_legacy(self.query())
        snapshot.assert_not_called()

    def test_page_capacity_can_recover_from_clean_summary_and_small_raw_window(self):
        window = NativeTraceWindow([self.detail(0)], project_id="space", include_previews=True)
        self.client.get.side_effect = TraceQueryServiceError("selected page exceeds capacity", 3036)
        with patch("apps.core.trace_query.signoz.snapshot_window.query_snapshot_window",
                   return_value=(window, {"queryMode": "hybrid_snapshot"})):
            result = self.provider._query_traces_legacy(self.query())
        self.assertEqual(result["data"], window.list(self.query().query_params)["data"])
        self.assertEqual(result["meta"]["totalItems"], 1)
        self.assertEqual(result["meta"]["queryMode"], "hybrid_snapshot")
        self.assertEqual(result["facets"], window.facets(self.query().query_params))

    def test_page_capacity_without_verified_snapshot_keeps_original_error(self):
        capacity = TraceQueryServiceError("selected page exceeds capacity", 3036)
        self.client.get.side_effect = capacity
        with patch.object(self.provider, "collect_traces") as scan, \
             self.assertRaises(TraceQueryServiceError) as raised:
            self.provider._query_traces_legacy(self.query())
        self.assertIs(raised.exception, capacity)
        self.client.get.assert_called_once()
        scan.assert_not_called()

    def test_falsey_keyword_does_not_filter_but_string_zero_does(self):
        details = [self.detail(1), self.detail(0)]
        for detail, trace_id in zip(details, ("trace-a", "trace-0")):
            detail["traceId"] = detail["spans"][0]["trace_id"] = trace_id
        for keyword, selected, expected_ids in (
            (False, details, ["trace-a", "trace-0"]),
            (0, details, ["trace-a", "trace-0"]),
            ("0", details[1:], ["trace-0"]),
        ):
            with self.subTest(keyword=keyword):
                self.client.reset_mock()
                payload = self.payload(len(selected))
                payload["data"] = deepcopy(selected)
                self.client.get.return_value = payload
                query = self.query(keyword=keyword)
                original = deepcopy(query.query_params)
                with patch.object(self.provider, "collect_traces") as scan:
                    result = self.provider._query_traces_legacy(query)
                self.assert_canonical_page(result, details, query, expected_ids, "storage")
                scan.assert_not_called()
                self.client.get.assert_called_once()
                sent = self.client.get.call_args.kwargs["params"]
                if keyword == "0":
                    self.assertEqual(sent["keyword"], "0")
                else:
                    # The wire interprets False and 0 as nonempty strings.
                    self.assertNotIn("keyword", sent)
                self.assertEqual(query.query_params, original)

    def test_legacy_storage_identity_facets_are_not_claimed_as_public_user_ids(self):
        payload = self.payload(1)
        payload["facets"]["username"] = [{"value": "collector-subject", "count": 1}]
        payload["data"][0]["spans"][0]["attributes"]["gen_ai.user.id"] = "customer-42"
        self.client.get.return_value = payload
        with patch.object(self.provider, "collect_traces") as scan:
            result = self.provider._query_traces_legacy(self.query())
        self.assertNotIn("username", result["facets"])
        self.assertIs(result["meta"].get("userIdentityFacetsComplete"), False)
        self.assertEqual(result["data"][0]["userId"], "customer-42")
        scan.assert_not_called()

    def test_falsey_trace_alias_neither_filters_nor_shadows_a_valid_alias(self):
        details = [self.detail(1), self.detail(0)]
        for detail, trace_id in zip(details, ("trace-a", "0")):
            detail["traceId"] = detail["spans"][0]["trace_id"] = trace_id
        cases = []
        for falsey in (False, 0):
            for alias, other in (("trace_id", "traceId"), ("traceId", "trace_id")):
                cases.extend((
                    ({alias: falsey}, details, ["trace-a", "0"], None),
                    ({alias: falsey, other: "trace-a"}, details[:1], ["trace-a"], "trace-a"),
                ))
            cases.append(({"trace_id": falsey, "traceId": falsey}, details, ["trace-a", "0"], None))
        cases.extend(({alias: "0"}, details[1:], ["0"], "0") for alias in ("trace_id", "traceId"))
        for overrides, selected, expected_ids, expected_trace_id in cases:
            with self.subTest(overrides=overrides):
                self.client.reset_mock()
                payload = self.payload(len(selected))
                payload["data"] = deepcopy(selected)
                self.client.get.return_value = payload
                query = self.query(**overrides)
                original = deepcopy(query.query_params)
                with patch.object(self.provider, "collect_traces") as scan:
                    result = self.provider._query_traces_legacy(query)
                self.assert_canonical_page(result, details, query, expected_ids, "storage")
                scan.assert_not_called()
                self.client.get.assert_called_once()
                sent = self.client.get.call_args.kwargs["params"]
                if expected_trace_id is None:
                    self.assertNotIn("trace_id", sent)
                else:
                    self.assertEqual(sent["trace_id"], expected_trace_id)
                self.assertEqual(query.query_params, original)

    def test_out_of_int64_filters_preserve_native_results_without_calling_storage_page(self):
        details = [self.detail(1), self.detail(0)]
        both = ["trace-000001", "trace-000000"]
        cases = []
        for outside in (-(2 ** 63) - 1, 2 ** 63):
            for value in (outside, str(outside)):
                cases.extend((
                    ("span_count", "=", value, []),
                    ("status_code", "!=", value, both),
                    ("start_time_unix_nano", ">", value, both if outside < 0 else []),
                    ("end_time_unix_nano", "<", value, [] if outside < 0 else both),
                    ("span_count", "in", [1, value], both),
                    ("span_count", "not_in", [1, value], []),
                    ("span_count", "in", [None, 1, value], both),
                    ("span_count", "not_in", [None, value], both),
                ))
        for field, op, value, expected_ids in cases:
            with self.subTest(field=field, op=op, value=value):
                self.client.reset_mock()
                query = self.query(filters=[{"field": field, "op": op, "value": value}])
                original = deepcopy(query.query_params)
                # An unsupported integer is a query capability decision, not a
                # request to let the upstream coerce or reject its JSON number.
                self.client.get.side_effect = AssertionError("Storage page must not receive an out-of-int64 filter")
                with patch.object(self.provider, "collect_traces", return_value={
                    "data": details, "meta": {"complete": True, "from": self.params["from"], "to": self.params["to"]},
                }) as scan:
                    result = self.provider._query_traces_legacy(query)
                self.assert_canonical_page(result, details, query, expected_ids, "compatibility_scan")
                self.client.get.assert_not_called()
                scan.assert_called_once()
                self.assertEqual(query.query_params, original)

    def test_int64_endpoints_and_members_keep_adapter_eligibility(self):
        details = [self.detail(1), self.detail(0)]
        both = ["trace-000001", "trace-000000"]
        cases = []
        for field in ("span_count", "status_code", "start_time_unix_nano", "end_time_unix_nano"):
            # Public count lower bounds must now be positive. Keep the exact
            # int64 lower endpoint on a valid upper-bound comparison instead.
            cases.extend(((field, "<=" if field == "span_count" else ">=", -(2 ** 63), [] if field == "span_count" else both),
                          (field, "<=", 2 ** 63 - 1, both)))
        cases.extend((
            ("span_count", "in", [-(2 ** 63), 1, 2 ** 63 - 1], both),
            ("span_count", "not_in", [-(2 ** 63), 1, 2 ** 63 - 1], []),
            ("span_count", "IS_NULL", 2 ** 63, []),
            ("span_count", "IS_NOT_NULL", 2 ** 63, both),
            ("duration_ms", "<", 2 ** 63, both),
        ))
        for field, op, value, expected_ids in cases:
            with self.subTest(field=field, op=op, value=value):
                self.client.reset_mock()
                self.client.get.return_value = self.payload(len(expected_ids))
                query = self.query(filters=[{"field": field, "op": op, "value": value}])
                if field == "status_code":
                    # Valid numeric status predicates need canonical root
                    # semantics, which this storage-page contract cannot prove.
                    self.assertIsNone(self.provider._query_trace_page(query))
                    self.client.get.assert_not_called()
                    continue
                with patch.object(self.provider, "collect_traces") as scan:
                    result = self.provider._query_traces_legacy(query)
                self.assert_canonical_page(result, details, query, expected_ids, "storage")
                scan.assert_not_called()
                self.client.get.assert_called_once()
                sent = json.loads(self.client.get.call_args.kwargs["params"]["filters"])
                self.assertEqual(sent, [{"field": field, "op": op, "value": value}])

    def test_invalid_integer_filters_still_fail_instead_of_falling_back(self):
        cases = [(op, value) for op in ("=", ">") for value in (False, True, 1.5, "1.5")]
        cases.extend((op, [1, bad]) for op in ("in", "not_in") for bad in (False, True, 1.5, "1.5"))
        for field in ("span_count", "status_code", "start_time_unix_nano", "end_time_unix_nano"):
            for op, value in cases:
                with self.subTest(field=field, op=op, value=value):
                    with patch.object(self.provider, "collect_traces") as scan:
                        with self.assertRaises(TraceQueryServiceError) as caught:
                            self.provider._query_traces_legacy(self.query(filters=[{"field": field, "op": op, "value": value}]))
                    self.assertEqual(caught.exception.code, 3027)
                    scan.assert_not_called()
                    self.client.get.assert_not_called()
        wide_filter = {"field": "span_count", "op": "<", "value": 2 ** 63}
        for overrides in (
            {"filters": [wide_filter], "page": 0},
            {"filters": [wide_filter], "fields": []},
            {"filters": [wide_filter, {"field": "status_code", "value": False}]},
        ):
            with self.subTest(invalid_with_wide_integer=overrides):
                with patch.object(self.provider, "collect_traces") as scan:
                    with self.assertRaises(TraceQueryServiceError) as caught:
                        self.provider._query_traces_legacy(self.query(**overrides))
                self.assertEqual(caught.exception.code, 3027)
                scan.assert_not_called()
                self.client.get.assert_not_called()

    def test_window_growth_does_not_increase_http_calls_or_projected_rows(self):
        for total in (211, 1000, 10001):
            for page, limit in ((1, 1), (1, 20), (2, 20)):
                with self.subTest(total=total, page=page, limit=limit):
                    self.client.reset_mock()
                    payload = self.payload(total, page=page, limit=limit)
                    self.client.get.return_value = payload
                    with patch.object(self.provider, "collect_traces") as scan:
                        result = self.provider._query_traces_legacy(self.query(page=page, limit=limit))
                    scan.assert_not_called()
                    self.client.get.assert_called_once()
                    self.assertEqual(self.client.get.call_args.args[0], "/api/v1/ai-vision/traces/page")
                    self.assertEqual(len(result["data"]), limit)
                    self.assertEqual(result["meta"]["totalItems"], total)
                    self.assertEqual(result["meta"]["page"], page)
                    self.assertEqual(result["meta"]["queryMode"], "storage")
                    self.assertEqual(result["facets"]["env"][0]["count"], total)
                    self.assertEqual(result["data"][0]["trace_id"], payload["data"][0]["traceId"])

    def test_partial_last_page_and_empty_pages_keep_global_totals(self):
        for total, page, expected in ((211, 11, 11), (211, 12, 0), (0, 1, 0)):
            with self.subTest(total=total, page=page):
                self.client.get.return_value = self.payload(total, page=page)
                result = self.provider._query_traces_legacy(self.query(page=page))
                self.assertEqual(len(result["data"]), expected)
                self.assertEqual(result["meta"]["totalItems"], total)
                self.assertTrue(result["meta"]["complete"])

    def test_page_keeps_canonical_unknown_zero_cache_and_nested_usage(self):
        payload = self.payload(4, limit=20)
        spans = [item["spans"][0] for item in payload["data"]]
        spans[0]["attributes"].pop("gen_ai.usage.output_tokens")
        spans[0]["attributes"].pop("gen_ai.usage.total_tokens")
        spans[1]["attributes"].update({"gen_ai.usage.input_tokens": 0,
                                        "gen_ai.usage.output_tokens": 0, "gen_ai.usage.total_tokens": 0})
        spans[2]["attributes"]["gen_ai.usage.cache_read.input_tokens"] = 5
        child = deepcopy(spans[3])
        child.update(span_id="child", parent_span_id=spans[3]["span_id"])
        payload["data"][3]["spans"].append(child)
        payload["data"][3]["spanCount"] = 2
        expected = NativeTraceWindow(payload["data"], project_id="space", include_previews=True).list(self.params)
        self.client.get.return_value = payload
        actual = self.provider._query_traces_legacy(self.query())
        self.assertEqual(actual["data"], expected["data"])
        self.assertIsNone(actual["data"][0]["usage"]["total"])
        self.assertEqual(actual["data"][1]["usage"]["total"], 0)

    def test_authorized_product_and_username_filter_have_separate_roles(self):
        detail = self.detail(1)
        detail["spans"][0]["resource"]["ant.username"] = "collector-subject"
        detail["spans"][0]["attributes"]["user.id"] = "alice"
        with patch.object(self.provider, "collect_traces", return_value={
            "data": [detail], "meta": {"complete": True},
        }) as scan:
            result = self.provider._query_traces_legacy(self.query(username="alice", fields=["trace_id"]))
        self.assertEqual([row["trace_id"] for row in result["data"]], ["trace-000001"])
        self.assertTrue(result["data"][0]["ioPreview"]["complete"])
        self.assertEqual(result["meta"]["queryMode"], "compatibility_scan")
        candidate_query = scan.call_args.args[0]
        self.assertEqual(candidate_query.agent_uuid, "agent-a")
        sent = self.provider._params(candidate_query, {"agent_product_filter": "stored-product"}, agent_filter=False)
        self.assertEqual(sent["agent_product"], "stored-product")
        self.assertEqual(sent["space_id"], "space")
        self.assertNotIn("username", sent)
        self.assertNotIn("user", sent)
        self.assertNotIn("fields", sent)

    def test_personal_subject_is_preserved(self):
        self.space.type = 1
        payload = self.payload(1)
        payload["meta"]["user"] = "collector-subject"
        self.client.get.return_value = payload
        with patch("apps.core.trace_query.signoz.provider.require_default_subject", return_value="collector-subject"):
            self.provider._query_traces_legacy(self.query())
        self.assertEqual(self.client.get.call_args.kwargs["params"]["user"], "collector-subject")

    def test_filter_aliases_and_nanosecond_integers_are_preserved(self):
        payload = self.payload(1)
        self.client.get.return_value = payload
        self.provider._query_traces_legacy(self.query(filters={
            "field": "start_time_unix_nano", "operator": ">=", "value": "1790989322999999999",
        }))
        sent = json.loads(self.client.get.call_args.kwargs["params"]["filters"])
        self.assertEqual(sent, [{"field": "start_time_unix_nano", "op": ">=", "value": 1790989322999999999}])

    def test_unsupported_semantics_use_verified_compatibility_path(self):
        for overrides in (
            {"orderBy": "total_tokens.desc"}, {"searchType": "input", "keyword": "hello"},
            {"purpose": "diagnosis_input"}, {"filters": {"total_tokens": 12}},
            {"fields": ["attributes.custom.dynamic"]},
            {"trace_id": "one", "traceId": "two"},
        ):
            with self.subTest(overrides=overrides):
                self.client.reset_mock()
                self.assertIsNone(self.provider._query_trace_page(self.query(**overrides)))
                self.client.get.assert_not_called()

    def test_old_server_and_explicit_unsupported_fall_back_but_failures_do_not(self):
        for response in (TraceQueryServiceError("Not found", 3019), {"supported": False, "reason": "complex tags"}):
            with self.subTest(response=response):
                self.client.get.side_effect = response if isinstance(response, Exception) else None
                self.client.get.return_value = response
                with patch.object(self.provider, "collect_traces", return_value={
                    "data": [self.detail(0)], "meta": {"complete": True},
                }) as scan:
                    result = self.provider._query_traces_legacy(self.query())
                scan.assert_called_once()
                self.assertEqual(result["meta"]["queryMode"], "compatibility_scan")
        for overrides in ({}, {"keyword": False}, {"filters": [{
            "field": "span_count", "op": "<=", "value": 2 ** 63 - 1,
        }]}):
            with self.subTest(network_failure=overrides):
                self.client.get.side_effect = TimeoutError("upstream timed out")
                with patch.object(self.provider, "collect_traces") as scan:
                    with self.assertRaises(TimeoutError):
                        self.provider._query_traces_legacy(self.query(**overrides))
                scan.assert_not_called()

    def test_invalid_requests_fail_before_network(self):
        for overrides in ({"page": 0}, {"limit": 201}, {"limit": True}, {"fields": []},
                          {"purpose": "invalid"}, {"filters": "broken"}):
            with self.subTest(overrides=overrides), self.assertRaises(TraceQueryServiceError) as caught:
                self.provider._query_traces_legacy(self.query(**overrides))
            self.assertEqual(caught.exception.code, 3027)
        self.client.get.assert_not_called()

    def test_malformed_or_incomplete_pages_never_fall_back(self):
        mutations = {
            "contract": lambda p: p["meta"].update(contract="unverified"),
            "complete": lambda p: p["meta"].update(complete=False),
            "count-type": lambda p: p["meta"].update(totalItems=True),
            "total-pages": lambda p: p["meta"].update(totalPages=99),
            "page": lambda p: p["meta"].update(page=2),
            "window": lambda p: p["meta"].update(to=0),
            "scope": lambda p: p["meta"].update(space_id="other"),
            "product": lambda p: p["meta"].update(agent_product="wrong"),
            "missing-trace": lambda p: p["data"].pop(),
            "duplicate-trace": lambda p: p["data"].__setitem__(1, deepcopy(p["data"][0])),
            "span-count": lambda p: p["data"][0].update(spanCount=2),
            "span-id": lambda p: p["data"][0]["spans"][0].update(span_id=""),
            "foreign-child": lambda p: p["data"][0]["spans"][0]["resource"].update({"aivision.space.id": "other"}),
            "foreign-agent": lambda p: p["data"][0]["spans"][0]["resource"].update({"aivision.agent.id": "other"}),
            "wrong-filter": lambda p: p["data"][0]["spans"][0]["resource"].update({"ant.agent.env": "dev"}),
            "order": lambda p: p["data"].reverse(),
            "facets": lambda p: p.pop("facets"),
            "facet-count": lambda p: p["facets"]["env"][0].update(count=999),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                payload = self.payload(2)
                mutate(payload)
                self.client.get.return_value = payload
                with patch.object(self.provider, "collect_traces") as scan:
                    with self.assertRaises(TraceQueryServiceError):
                        self.provider._query_traces_legacy(self.query(env="prod"))
                scan.assert_not_called()

    def test_storage_page_summaries_reuse_one_page_without_per_row_detail_reads(self):
        from apps.core.tests.test_native_trace_summary import trace_with_calls

        payload = self.payload(total=5)
        cases = (([12] * 11, False), ([12] * 10 + [None], False),
                 ([None, None], False), ([0, 0], False), ([12, 12], True))
        for index, (totals, nested) in enumerate(cases):
            detail = trace_with_calls(totals, nested=nested)
            identifier = payload["data"][index]["traceId"]
            detail["traceId"] = identifier
            detail["spanCount"] = len(detail["spans"])
            for span in detail["spans"]:
                span["trace_id"] = identifier
                span["resource"] = {"aivision.space.id": "space", "aivision.agent.id": "agent-a", "ant.agent.env": "prod"}
            payload["data"][index] = detail
        self.client.get.return_value = payload
        with patch.object(self.provider, "get_trace_detail") as details, patch.object(self.provider, "collect_traces") as scan:
            result = self.provider._query_traces_legacy(self.query())
        self.client.get.assert_called_once()
        details.assert_not_called()
        scan.assert_not_called()
        expected = NativeTraceWindow(payload["data"], project_id="space", include_previews=True).list({})["data"]
        self.assertEqual(result["data"], expected)
        self.assertEqual([row["reportedUsage"]["total"]["value"] for row in result["data"]], [132, 120, None, 0, 24])
        self.assertEqual(result["data"][-1]["usage"]["unknownReason"], "ambiguous_nested_generations")

    def test_legacy_page_keeps_fast_rows_but_never_returns_raw_environment_facets(self):
        payload = self.payload(total=10001)
        payload["meta"].pop("environmentSemantics", None)
        payload["data"][0]["spans"][0]["attributes"]["langfuse.environment"] = "local"
        self.client.get.return_value = payload
        with patch.object(self.provider, "collect_traces") as scan:
            result = self.provider._query_traces_legacy(self.query())
        scan.assert_not_called()
        self.client.get.assert_called_once()
        self.assertEqual(result["meta"]["queryMode"], "storage")
        self.assertIs(result["meta"].get("environmentFacetsComplete"), False)
        self.assertNotIn("env", result["facets"])
        self.assertEqual(result["data"][0]["environment"], "local")
        self.assertEqual(result["meta"]["totalItems"], 10001)

    def test_empty_legacy_env_page_cannot_prove_no_canonical_matches(self):
        payload = self.payload(total=0)
        payload["meta"].pop("environmentSemantics", None)
        self.client.get.return_value = payload
        detail = self.detail(0)
        detail["spans"][0]["attributes"]["langfuse.environment"] = "local"
        with patch.object(self.provider, "collect_traces", return_value={
            "data": [detail], "meta": {"complete": True, "from": self.params["from"], "to": self.params["to"]},
        }) as scan:
            result = self.provider._query_traces_legacy(self.query(env="local"))
        scan.assert_called_once()
        self.assertEqual(result["meta"]["queryMode"], "compatibility_scan")
        self.assertEqual(result["meta"]["totalItems"], 1)
        self.assertEqual(result["facets"]["env"], [{"value": "local", "count": 1}])
        self.assertIs(result["meta"].get("environmentFacetsComplete"), True)

    def test_legacy_env_fallback_preserves_verified_scan_limit_error(self):
        payload = self.payload(total=0)
        payload["meta"].pop("environmentSemantics", None)
        self.client.get.return_value = payload
        with patch.object(self.provider, "collect_traces", side_effect=TraceQueryServiceError("Trace window exceeds scan limit", 3035)):
            with self.assertRaisesRegex(TraceQueryServiceError, "scan limit"):
                self.provider._query_traces_legacy(self.query(env="local"))

    def test_declared_canonical_environment_facets_and_shortcut_keep_storage_path(self):
        payload = self.payload(total=1)
        payload["meta"]["environmentSemantics"] = "root-span-resource-v1"
        payload["data"][0]["spans"][0]["attributes"]["langfuse.environment"] = "local"
        payload["facets"]["env"] = [{"value": "local", "count": 1}]
        self.client.get.return_value = payload
        with patch.object(self.provider, "collect_traces") as scan:
            result = self.provider._query_traces_legacy(self.query(env="local"))
        scan.assert_not_called()
        self.assertEqual(result["facets"]["env"], [{"value": "local", "count": 1}])
        self.assertIs(result["meta"].get("environmentFacetsComplete"), True)
        self.assertEqual(result["meta"].get("environmentSemantics"), "root-span-resource-v1")

    def test_invalid_legacy_env_pages_fail_instead_of_hiding_errors_in_a_scan(self):
        for mutation in (
            lambda payload: payload["data"][0]["spans"][0].update(start_time_unix_nano="invalid"),
            lambda payload: payload["data"].reverse(),
            lambda payload: payload["data"][0]["spans"][0]["resource"].update({"ant.agent.env": "wrong"}),
        ):
            payload = self.payload(total=2)
            payload["meta"].pop("environmentSemantics", None)
            mutation(payload)
            self.client.get.return_value = payload
            with patch.object(self.provider, "collect_traces") as scan:
                with self.assertRaises(TraceQueryServiceError):
                    self.provider._query_traces_legacy(self.query(env="prod"))
            scan.assert_not_called()
