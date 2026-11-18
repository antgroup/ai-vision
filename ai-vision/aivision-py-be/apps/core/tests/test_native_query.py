from pathlib import Path
import subprocess
import sys

from django.test import SimpleTestCase
from apps.core.trace_query.native_query import NativeTraceWindow
from apps.core.trace_query.types import TraceQueryServiceError


def trace(index, status=1, usage=True):
    attrs = {"langfuse.observation.type": "GENERATION", "gen_ai.request.model": "fixture", "gen_ai.provider.name": "test"}
    if usage:
        attrs.update({"gen_ai.usage.input_tokens": 10, "gen_ai.usage.output_tokens": 2, "gen_ai.usage.total_tokens": 12})
    return {"traceId": f"trace-{index:04d}", "spans": [{
        "span_id": f"span-{index}", "trace_id": f"trace-{index:04d}", "name": "call",
        "start_time_unix_nano": "1790989323000000000", "end_time_unix_nano": "1790989323100000000",
        "status": {"code": str(status)}, "attributes": attrs,
    }]}


class NativeQueryTests(SimpleTestCase):
    def setUp(self):
        self.window = NativeTraceWindow([trace(i) for i in range(205)] + [trace(205, 2), trace(206, 0, False)], project_id="space")

    def test_stable_pages_and_projected_status_filter(self):
        first = self.window.list({"page": 1, "limit": 200, "orderBy": "start_time_unix_nano.asc"})
        second = self.window.list({"page": 2, "limit": 200, "orderBy": "start_time_unix_nano.asc"})
        ids = [row["trace_id"] for row in first["data"] + second["data"]]
        self.assertEqual(ids, [f"trace-{i:04d}" for i in range(207)])
        filtered = self.window.list({"fields": ["trace_id", "status_code"], "filters": [{"field": "status_code", "op": "=", "value": "ERROR"}]})
        self.assertEqual(filtered["data"], [{"trace_id": "trace-0205", "status_code": 2}])

    def test_trace_status_follows_root_span(self):
        def detail(index, root_code, child_code):
            attrs = {"langfuse.observation.type": "GENERATION", "gen_ai.request.model": "fixture", "gen_ai.provider.name": "test"}
            spans = [{
                "span_id": f"root-{index}", "trace_id": f"trace-{index:04d}", "name": "call",
                "start_time_unix_nano": "1790989323000000000", "end_time_unix_nano": "1790989323100000000",
                "status": {"code": str(root_code)}, "attributes": attrs,
            }]
            if child_code is not None:
                spans.append({
                    "span_id": f"child-{index}", "trace_id": f"trace-{index:04d}", "parent_span_id": f"root-{index}",
                    "name": "tool", "start_time_unix_nano": "1790989323000000000",
                    "end_time_unix_nano": "1790989323100000000", "status": {"code": str(child_code)}, "attributes": attrs,
                })
            return {"traceId": f"trace-{index:04d}", "spans": spans}

        window = NativeTraceWindow([detail(1, 1, 2), detail(2, 0, 2), detail(3, 2, 1)], project_id="space")
        rows = {row["trace_id"]: row["status_code"] for row in window.list({"fields": ["trace_id", "status_code"]})["data"]}
        self.assertEqual(rows, {"trace-0001": 1, "trace-0002": 0, "trace-0003": 2})
        self.assertEqual(window.metrics({})["errorCount"], 1)

    def test_like_repeated_wildcards_finish_without_backtracking(self):
        # Keep an unsafe matcher from hanging the test runner or a CI worker.
        script = """
from apps.core.tests.test_native_query import trace
from apps.core.trace_query.native_query import NativeTraceWindow

detail = trace(1)
detail["spans"][0]["name"] = "a" * 1000
result = NativeTraceWindow([detail], project_id="space").list({
    "filters": [{"field": "name", "op": "LIKE", "value": "%a" * 35 + "b"}],
})
assert result["meta"]["totalItems"] == 0, result
print("no match")
"""
        try:
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=Path(__file__).resolve().parents[3],
                capture_output=True, text=True, timeout=5,
            )
        except subprocess.TimeoutExpired:
            self.fail("Native LIKE matching exceeded the five-second worker budget")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), "no match")

    def test_like_preserves_percent_wildcards_and_literal_special_characters(self):
        cases = [
            ("", "", True), ("", "%", True), ("", "%%", True),
            ("a", "", False), ("alpha", "al%", True), ("alpha", "%ha", True),
            ("alpha", "pha", False), ("alpha", "%ph%", True),
            ("ab", "a%%b", True), ("ab", "a%b%c", False),
            ("aaa", "a%a%a", True), ("aa", "a%a%a", False),
            ("a", "a%a", False), ("a\nb", "a%b", True),
            ("ABC", "abc", False), ("ABC", "%B%", True),
            ("a_b", "a_b", True), ("axb", "a_b", False),
            ("alpha.beta", "alpha.beta", True), ("alphaXbeta", "alpha.beta", False),
            (r"a+b*(c)?[d]^{e}$|\_", r"%+b*(c)?[d]^{e}$|\_%", True),
            ("anything", "*", False), ("x", "?", False), ("d", "[d]", False),
        ]
        for name, pattern, matched in cases:
            with self.subTest(name=name, pattern=pattern):
                detail = trace(1)
                detail["spans"][0]["name"] = name
                result = NativeTraceWindow([detail], project_id="space").list({
                    "filters": [{"field": "name", "op": "LIKE", "value": pattern}],
                })
                self.assertEqual(result["meta"]["totalItems"], int(matched))

    def test_complete_window_metrics_and_unknown_coverage(self):
        result = self.window.metrics({})
        self.assertEqual(result["traceCount"], 207)
        self.assertEqual(result["generationCount"], 207)
        self.assertEqual(result["errorCount"], 1)
        self.assertAlmostEqual(result["errorRate"], 1 / 207)
        self.assertIsNone(result["usage"]["total"])
        self.assertEqual(result["coverage"]["knownUsageCalls"], 206)
        self.assertEqual(result["coverage"]["knownTokens"], 2472)
        self.assertIsNone(result["usage"]["cost"]["total"])

    def test_bad_fields_operators_and_parameters_fail(self):
        for params in ({"fields": ["typo"]}, {"filters": [{"field": "status_code", "op": "unknown", "value": 2}]}, {"limti": 20}, {"page": 0}):
            with self.subTest(params=params), self.assertRaises(TraceQueryServiceError):
                self.window.list(params)

    def test_child_attributes_filter_and_project_real_framework_calls(self):
        detail = trace(1)
        root = detail["spans"][0]
        root["attributes"] = {"langfuse.observation.type": "AGENT"}
        for name, model in (("weather", "model-a"), ("search", "model-b")):
            detail["spans"].append({**root, "span_id": name, "parent_span_id": root["span_id"],
                "attributes": {"langfuse.observation.type": "GENERATION", "gen_ai.tool.call.name": name,
                               "gen_ai.request.model": model, "gen_ai.usage.total_tokens": 7}})
        window = NativeTraceWindow([detail], project_id="space")
        params = {"filters": [{"field": "attributes.gen_ai.request.model", "value": "model-b"}]}
        self.assertEqual(window.list(params)["meta"]["totalItems"], 1)
        self.assertEqual(window.metrics(params)["traceCount"], 1)
        self.assertEqual(window.list({"fields": ["attributes.gen_ai.tool.call.name"]})["data"],
                         [{"attributes.gen_ai.tool.call.name": "search"}])
        for op, value, count in (("IS_NULL", None, 0), ("!=", "model-a", 0), ("not_in", ["missing"], 1)):
            result = window.list({"filters": [{"field": "attributes.gen_ai.request.model", "op": op, "value": value}]})
            self.assertEqual(result["meta"]["totalItems"], count)

    def test_facets_cover_all_pages_and_follow_filters(self):
        details = [trace(i) for i in range(207)]
        for i, detail in enumerate(details):
            detail["spans"][0]["resource"] = {"ant.agent.env": "prod" if i < 200 else "test"}
        window = NativeTraceWindow(details, project_id="space")
        self.assertEqual(window.facets({"limit": 200})["env"], [{"value": "prod", "count": 200}, {"value": "test", "count": 7}])
        self.assertEqual(window.facets({"env": "test"})["env"], [{"value": "test", "count": 7}])

    def test_input_search_is_independent_and_json_equality_is_structural(self):
        detail = trace(1)
        detail["spans"][0]["attributes"]["gen_ai.input.messages"] = {"b": 2, "a": "hello"}
        window = NativeTraceWindow([detail], project_id="space")
        self.assertEqual(window.list({"searchType": "traceId", "input_keyword": "hello"})["meta"]["totalItems"], 1)
        self.assertEqual(window.list({"keyword": "trace-0001", "input_keyword": "absent"})["meta"]["totalItems"], 0)
        self.assertEqual(window.list({"filters": [{"field": "attributes.gen_ai.input.messages", "value": {"a": "hello", "b": 2}}]})["meta"]["totalItems"], 1)

    def test_grouped_metrics_keep_nested_call_ambiguity_across_models(self):
        detail = trace(1)
        root = detail["spans"][0]
        wrapper = {**root, "span_id": "wrapper", "parent_span_id": root["span_id"], "attributes": {}}
        child = {**root, "span_id": "child", "parent_span_id": "wrapper", "attributes": {**root["attributes"], "gen_ai.request.model": "other"}}
        detail["spans"] += [wrapper, child]
        result = NativeTraceWindow([detail], project_id="space").metrics({})
        for usage in [result["usage"], *[g["usage"] for g in result["groups"]], *[g["usage"] for g in result["timeseries"]]]:
            self.assertIsNone(usage["total"])
            self.assertEqual(usage["unknownReason"], "ambiguous_nested_generations")

    def test_username_resource_alias_and_multiselect_tag_or(self):
        detail = trace(1)
        detail["spans"][0]["resource"] = {"ant.username": "authenticated-subject"}
        detail["spans"][0]["attributes"]["langfuse.trace.tags"] = '["sdk"]'
        window = NativeTraceWindow([detail], project_id="space")
        params = {"username": "authenticated-subject", "tags": ["sdk", "other"]}
        self.assertEqual(window.list(params)["meta"]["totalItems"], 1)
        self.assertEqual(window.facets(params)["username"], [{"value": "authenticated-subject", "count": 1}])


def usage_trace(index, kind="GENERATION", **tokens):
    detail = trace(index, usage=False)
    detail["spans"][0]["attributes"] = {
        "langfuse.observation.type": kind,
        "gen_ai.request.model": "chat" if kind == "GENERATION" else "embedding",
        **{f"gen_ai.usage.{field}_tokens": value for field, value in tokens.items()},
    }
    return detail


class NativeReportedUsageTests(SimpleTestCase):
    def test_known_chat_subtotal_is_separate_from_incomplete_embedding_total(self):
        details = [usage_trace(i, input=13, output=5, total=18) for i in range(6)]
        details += [usage_trace(i, "EMBEDDING") for i in range(6, 10)]
        result = NativeTraceWindow(details, project_id="space").metrics({})
        self.assertEqual([result["usage"][field] for field in ("input", "output", "total")], [None, None, None])
        self.assertEqual(result["coverage"], {
            "billableCalls": 10, "knownUsageCalls": 6, "knownTokens": 108, "knownCostCalls": 0,
        })
        self.assertEqual(result["reportedUsage"], {
            "input": {"value": 78, "reportedCalls": 6},
            "output": {"value": 30, "reportedCalls": 6},
            "total": {"value": 108, "reportedCalls": 6},
        })
        generation, embedding = result["usageByType"]
        self.assertEqual((generation["type"], generation["callCount"]), ("GENERATION", 6))
        self.assertEqual((embedding["type"], embedding["callCount"]), ("EMBEDDING", 4))
        self.assertEqual(generation["usage"]["total"], 108)
        self.assertEqual(generation["reportedUsage"], result["reportedUsage"])
        self.assertIsNone(embedding["usage"]["output"])
        self.assertEqual(embedding["reportedUsage"]["output"], {"value": None, "reportedCalls": 0})

    def test_each_reported_field_has_its_own_coverage(self):
        result = NativeTraceWindow([
            usage_trace(1, input=7), usage_trace(2, output=4),
            usage_trace(3, "EMBEDDING", total=11),
        ], project_id="space").metrics({})
        self.assertEqual(result["reportedUsage"], {
            "input": {"value": 7, "reportedCalls": 1},
            "output": {"value": 4, "reportedCalls": 1},
            "total": {"value": 11, "reportedCalls": 1},
        })
        self.assertEqual([result["usage"][field] for field in ("input", "output", "total")], [None, None, None])
        self.assertEqual(result["usageByType"][0]["reportedUsage"]["total"], {"value": None, "reportedCalls": 0})

    def test_all_unknown_fields_remain_null(self):
        result = NativeTraceWindow([usage_trace(1, "EMBEDDING")], project_id="space").metrics({})
        self.assertEqual(result["reportedUsage"], {
            field: {"value": None, "reportedCalls": 0} for field in ("input", "output", "total")
        })
        self.assertEqual([item["type"] for item in result["usageByType"]], ["EMBEDDING"])
        self.assertEqual(result["usageByType"][0]["reportedUsage"], result["reportedUsage"])

    def test_explicit_zero_is_reported_while_missing_calls_remain_unknown(self):
        result = NativeTraceWindow([
            usage_trace(1, input=0, output=0, total=0), usage_trace(2),
        ], project_id="space").metrics({})
        self.assertEqual(result["reportedUsage"], {
            field: {"value": 0, "reportedCalls": 1} for field in ("input", "output", "total")
        })
        self.assertIsNone(result["usage"]["total"])

    def test_reported_calls_deduplicate_by_both_trace_and_observation_id(self):
        first = usage_trace(1, input=13, output=5, total=18)
        second = usage_trace(2, input=13, output=5, total=18)
        second["spans"][0]["span_id"] = first["spans"][0]["span_id"]
        result = NativeTraceWindow([first, first, second], project_id="space").metrics({})
        self.assertEqual(result["reportedUsage"], {
            "input": {"value": 26, "reportedCalls": 2},
            "output": {"value": 10, "reportedCalls": 2},
            "total": {"value": 36, "reportedCalls": 2},
        })
        self.assertEqual(result["usageByType"][0]["callCount"], 2)
        self.assertEqual(result["coverage"]["knownUsageCalls"], 2)

    def test_nested_calls_keep_canonical_ambiguity_in_each_type(self):
        detail = usage_trace(1, input=13, output=5, total=18)
        parent = detail["spans"][0]
        detail["spans"].append({
            **parent, "span_id": "nested-embedding", "parent_span_id": parent["span_id"],
            "attributes": {**parent["attributes"], "langfuse.observation.type": "EMBEDDING"},
        })
        result = NativeTraceWindow([detail], project_id="space").metrics({})
        self.assertEqual(result["reportedUsage"]["total"], {"value": 36, "reportedCalls": 2})
        self.assertEqual(result["coverage"]["knownTokens"], 36)
        for usage in [result["usage"], *[item["usage"] for item in result["usageByType"]]]:
            self.assertIsNone(usage["total"])
            self.assertEqual(usage["unknownReason"], "ambiguous_nested_generations")
        for item in result["usageByType"]:
            self.assertEqual(item["reportedUsage"]["total"], {"value": 18, "reportedCalls": 1})

    def test_subtotals_and_types_use_only_the_filtered_window(self):
        result = NativeTraceWindow([
            usage_trace(1, input=13, output=5, total=18), usage_trace(2, "EMBEDDING"),
        ], project_id="space").metrics({
            "filters": [{"field": "trace_id", "op": "=", "value": "trace-0001"}],
        })
        self.assertEqual(result["traceCount"], 1)
        self.assertEqual(result["reportedUsage"]["total"], {"value": 18, "reportedCalls": 1})
        self.assertEqual([(item["type"], item["callCount"]) for item in result["usageByType"]], [("GENERATION", 1)])
        self.assertEqual(result["usage"]["total"], 18)

    def test_empty_window_has_no_types_or_invented_zero_measurements(self):
        result = NativeTraceWindow([], project_id="space").metrics({})
        self.assertEqual(result["usageByType"], [])
        self.assertEqual(result["reportedUsage"], {
            field: {"value": None, "reportedCalls": 0} for field in ("input", "output", "total")
        })
