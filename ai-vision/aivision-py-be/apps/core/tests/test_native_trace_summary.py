"""Native trace summaries agree across display, selectors and diagnostics."""

from copy import deepcopy
import unittest

from apps.core.trace_query.native_query import NativeTraceWindow
from apps.core.trace_query.semantics.observations import project_trace


def environment_trace(attributes=None, resource=None, *, identifier="trace-1"):
    return {"traceId": identifier, "spans": [{
        "trace_id": identifier, "span_id": "root", "name": "root",
        "start_time_unix_nano": "1790989323000000000",
        "end_time_unix_nano": "1790989323100000000",
        "attributes": attributes or {}, "resource": resource or {},
    }]}


def trace_with_calls(totals, *, nested=False, duplicate=False):
    detail = environment_trace({"langfuse.observation.type": "AGENT"})
    root = detail["spans"][0]
    for index, total in enumerate(totals):
        attrs = {"langfuse.observation.type": "EMBEDDING" if index % 2 else "GENERATION"}
        if total is not None:
            attrs["gen_ai.usage.total_tokens"] = total
        detail["spans"].append({
            **root, "span_id": f"call-{index}",
            "parent_span_id": "call-0" if nested and index else "root", "attributes": attrs,
        })
    if duplicate and totals:
        detail["spans"].append(deepcopy(detail["spans"][-1]))
    return detail


class NativeEnvironmentTests(unittest.TestCase):
    def test_list_detail_shortcut_filter_and_facets_share_root_environment(self):
        cases = [
            ({"langfuse.environment": "local"}, {}, "local", "attributes.langfuse.environment"),
            ({}, {"ant.agent.env": "prod"}, "prod", "resource.attributes.ant.agent.env"),
            ({"langfuse.environment": "local"}, {"ant.agent.env": "prod"}, "local", "attributes.langfuse.environment"),
            ({"deployment.environment.name": "staging"}, {}, "staging", "attributes.deployment.environment.name"),
            ({"deployment.environment": "old-span"}, {}, "old-span", "attributes.deployment.environment"),
            ({}, {"deployment.environment.name": "new-resource"}, "new-resource", "resource.attributes.deployment.environment.name"),
            ({}, {"deployment.environment": "old-resource"}, "old-resource", "resource.attributes.deployment.environment"),
            ({"langfuse.environment": "", "deployment.environment.name": " \t"}, {"ant.agent.env": "prod"}, "prod", "resource.attributes.ant.agent.env"),
            ({"langfuse.environment": None}, {"ant.agent.env": "\t"}, "default", None),
            ({}, {}, "default", None),
            ({"langfuse.environment": False, "deployment.environment.name": 12},
             {"ant.agent.env": "prod", "deployment.environment.name": "resource-standard"},
             "prod", "resource.attributes.ant.agent.env"),
            ({}, {"ant.agent.env": " ", "deployment.environment.name": "resource-standard", "deployment.environment": "old"},
             "resource-standard", "resource.attributes.deployment.environment.name"),
        ]
        for attrs, resource, expected, source in cases:
            with self.subTest(attrs=attrs, resource=resource):
                detail = environment_trace(attrs, resource)
                before = deepcopy(detail)
                window = NativeTraceWindow([detail], project_id="space")
                row = window.list({})["data"][0]
                self.assertEqual(row.get("environment"), expected)
                self.assertEqual(window.traces[0]["environment"], expected)
                self.assertEqual(row.get("environmentSource"), source)
                self.assertEqual(window.list({"env": expected})["meta"]["totalItems"], 1)
                self.assertEqual(window.list({"filters": [{"field": "environment", "value": expected}]})["meta"]["totalItems"], 1)
                self.assertEqual(window.facets({})["env"], [{"value": expected, "count": 1}])
                self.assertEqual(detail, before)

    def test_child_environment_is_preserved_without_changing_trace_or_raw_filters(self):
        for root_env in ({}, {"langfuse.environment": "root-env"}):
            with self.subTest(root_env=root_env):
                detail = environment_trace(root_env)
                detail["spans"].append({
                    **detail["spans"][0], "span_id": "child", "parent_span_id": "root",
                    "attributes": {"langfuse.environment": "child-span"},
                    "resource": {"ant.agent.env": "child-resource"},
                })
                window = NativeTraceWindow([detail], project_id="space")
                expected = root_env.get("langfuse.environment", "default")
                self.assertEqual(window.traces[0]["environment"], expected)
                child = next(item for item in window.traces[0]["observations"] if item["id"] == "child")
                self.assertEqual(child["environment"], "child-span")
                self.assertEqual(child["resourceAttributes"]["ant.agent.env"], "child-resource")
                self.assertEqual(window.list({"env": "child-span"})["meta"]["totalItems"], 0)
                self.assertEqual(window.facets({})["env"], [{"value": expected, "count": 1}])
                for op, count in (("=", 1), ("!=", 0)):
                    self.assertEqual(window.list({"filters": [{"field": "resource.attributes.ant.agent.env", "op": op, "value": "child-resource"}]})["meta"]["totalItems"], count)
                fields = {item["key"]: item for item in window.schema()["fields"]}
                self.assertEqual(fields["environment"]["filterScope"], "trace")
                self.assertEqual(fields["resource.attributes.ant.agent.env"]["filterScope"], "anyObservationPerPredicate")

    def test_historical_resource_shapes_resolve_without_reingestion(self):
        for resource in (
            {"resource": {"attributes": {"ant.agent.env": "prod"}}},
            {"resource_attributes": {"ant.agent.env": "prod"}},
            {"resource.attributes.ant.agent.env": "prod"},
            {"payload": {"resource": {"attributes": {"ant.agent.env": "prod"}}}},
        ):
            with self.subTest(resource=resource):
                detail = environment_trace()
                detail["spans"][0].pop("resource")
                detail["spans"][0].update(resource)
                self.assertEqual(NativeTraceWindow([detail], project_id="space").traces[0]["environment"], "prod")

    def test_precedence_keeps_exact_value_and_selected_explicit_root(self):
        detail = environment_trace({"langfuse.environment": "initial-root"})
        detail["spans"].append({
            **detail["spans"][0], "span_id": "selected-root", "parent_span_id": "root",
            "attributes": {"langfuse.internal.as_root": True, "langfuse.environment": " Prod ",
                           "deployment.environment.name": "span-name", "deployment.environment": "span-old"},
            "resource": {"ant.agent.env": "resource-ant", "deployment.environment.name": "resource-name"},
        })
        window = NativeTraceWindow([detail], project_id="space")
        self.assertEqual(window.traces[0]["rootObservationId"], "selected-root")
        self.assertEqual(window.traces[0]["environment"], " Prod ")
        self.assertEqual(window.list({"env": "Prod"})["meta"]["totalItems"], 0)
        self.assertEqual(window.list({"env": " Prod "})["meta"]["totalItems"], 1)

    def test_public_v1_projection_retains_existing_environment_contract(self):
        detail = environment_trace(resource={"ant.agent.env": "prod"})
        projected = project_trace(detail, project_id="space")
        self.assertEqual(projected["environment"], "default")
        self.assertNotIn("reportedUsage", projected)
        self.assertNotIn("coverage", projected)


class NativeTraceUsageSummaryTests(unittest.TestCase):
    def test_summary_distinguishes_complete_partial_unknown_zero_and_no_calls(self):
        cases = [
            ([12] * 11, 132, 132, 11, None),
            ([12] * 10 + [None], None, 120, 10, "usage_missing"),
            ([None, None], None, None, 0, "usage_missing"),
            ([0, 0], 0, 0, 2, None),
            ([0, None], None, 0, 1, "usage_missing"),
            ([], None, None, 0, "usage_missing"),
        ]
        for totals, total, subtotal, known, reason in cases:
            with self.subTest(totals=totals):
                window = NativeTraceWindow([trace_with_calls(totals)], project_id="space")
                row = window.list({})["data"][0]
                self.assertEqual(row["total_tokens"], total)
                self.assertEqual(row["usage"]["unknownReason"], reason)
                self.assertEqual(row.get("reportedUsage", {}).get("total"), {"value": subtotal, "reportedCalls": known})
                self.assertEqual(row["coverage"]["knownUsageCalls"], known)
                self.assertEqual(row["coverage"]["billableCalls"], len(totals))
                for field in ("usage", "reportedUsage", "coverage"):
                    self.assertEqual(row[field], window.traces[0][field])

    def test_duplicate_exports_deduplicate_but_independent_retries_remain_calls(self):
        detail = trace_with_calls([12, 12, None], duplicate=True)
        detail["spans"][1]["status"] = {"code": 2, "message": "failed attempt"}
        detail["spans"][3]["status"] = {"code": 2, "message": "cancelled"}
        result = NativeTraceWindow([detail], project_id="space").list({})["data"][0]
        self.assertIsNone(result["usage"]["total"])
        self.assertEqual(result.get("reportedUsage", {}).get("total"), {"value": 24, "reportedCalls": 2})
        self.assertEqual(result["coverage"]["billableCalls"], 3)

    def test_nested_full_coverage_does_not_clear_ambiguity(self):
        row = NativeTraceWindow([trace_with_calls([12, 12], nested=True)], project_id="space").list({})["data"][0]
        self.assertIsNone(row["usage"]["total"])
        self.assertEqual(row["usage"]["unknownReason"], "ambiguous_nested_generations")
        self.assertEqual(row.get("reportedUsage", {}).get("total"), {"value": 24, "reportedCalls": 2})
        self.assertEqual(row["coverage"]["knownUsageCalls"], row["coverage"]["billableCalls"])

    def test_summary_fields_are_projectable_without_new_requests(self):
        window = NativeTraceWindow([trace_with_calls([12, None])], project_id="space")
        fields = ["trace_id", "environment", "usage", "reportedUsage", "coverage"]
        row = window.list({"fields": fields})["data"][0]
        self.assertEqual(set(row), set(fields))
        self.assertEqual(row["coverage"]["billableCalls"], 2)
