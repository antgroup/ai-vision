"""Model-backed native decisions must participate in canonical call usage."""

from copy import deepcopy
import unittest

from apps.core.trace_query.native_query import NativeTraceWindow
from apps.core.trace_query.semantics.observations import project_trace


def routing_trace(provider="jev"):
    # Minimal shapes and measured counts from the real dev-image run on Oct 16.
    model, input_tokens, output_tokens = {
        "jev": ("jev-latest", 356, 34),
        "kev": ("kev-4b", 67, 51),
    }[provider]
    trace_id = "routing-" + provider

    def span(identifier, parent, attrs):
        return {
            "trace_id": trace_id,
            "span_id": identifier,
            "parent_span_id": parent,
            "name": identifier,
            "start_time_unix_nano": "2026-10-16T14:35:32Z",
            "end_time_unix_nano": "2026-10-16T14:35:33Z",
            "attributes": attrs,
        }

    return {"traceId": trace_id, "spans": [
        span("root", "", {"gen_ai.span.kind": "AGENT"}),
        span("decision", "root", {
            "agentic.decision.type": "model_routing",
            "agentic.decision.model": model,
            "agentic.decision.choice": "Qwen/Qwen2.5-7B-Instruct",
            "gen_ai.request.model": model,
            "gen_ai.provider.name": provider,
            "gen_ai.usage.input_tokens": input_tokens,
            "gen_ai.usage.output_tokens": output_tokens,
        }),
        span("response", "root", {
            "gen_ai.span.kind": "LLM",
            "gen_ai.request.model": "Qwen/Qwen2.5-7B-Instruct",
            "gen_ai.provider.name": "siliconflow",
            "gen_ai.usage.input_tokens": 70,
            "gen_ai.usage.output_tokens": 20,
            "gen_ai.usage.total_tokens": 90,
        }),
    ]}


class NativeDecisionUsageTests(unittest.TestCase):
    def test_real_routing_counts_reach_list_v1_and_all_native_metric_consumers(self):
        from apps.core.trace_query.langfuse.native import _column, observation_wire

        for provider, expected in (("jev", (426, 54, 480)), ("kev", (137, 71, 208))):
            with self.subTest(provider=provider):
                detail = routing_trace(provider)
                before = deepcopy(detail)
                window = NativeTraceWindow([detail], project_id="space")
                row = window.list({})["data"][0]
                self.assertEqual(
                    tuple(row[field] for field in ("input_tokens", "output_tokens", "total_tokens")),
                    expected,
                )
                trace = window.traces[0]
                decision = next(o for o in trace["observations"] if o["id"] == "decision")
                self.assertEqual(decision["type"], "GENERATION")
                self.assertEqual(observation_wire(decision)["type"], "GENERATION")
                self.assertEqual(_column(trace, "totalTokens"), expected[2])
                metrics = window.metrics({})
                self.assertEqual(metrics["generationCount"], 2)
                self.assertEqual(metrics["coverage"], {
                    "billableCalls": 2, "knownUsageCalls": 2,
                    "knownTokens": expected[2], "knownCostCalls": 0,
                })
                self.assertEqual(metrics["reportedUsage"]["total"], {
                    "value": expected[2], "reportedCalls": 2,
                })
                self.assertEqual(metrics["usageByType"][0]["usage"]["total"], expected[2])
                self.assertEqual(sum(g["usage"]["total"] for g in metrics["groups"]), expected[2])
                self.assertEqual(metrics["timeseries"][0]["usage"]["total"], expected[2])
                self.assertEqual(detail, before)

    def test_native_model_fallback_retains_standard_and_langfuse_precedence(self):
        names = (
            "gen_ai.request.model", "gen_ai.response.model",
            "langfuse.observation.model.name", "agentic.decision.model",
        )
        for offset, expected_model in enumerate(("request", "response", "langfuse", "native")):
            with self.subTest(expected_model=expected_model):
                detail = routing_trace()
                attrs = detail["spans"][1]["attributes"]
                for name in names:
                    attrs.pop(name, None)
                attrs.update(dict(zip(names[offset:], ("request", "response", "langfuse", "native")[offset:])))
                attrs["gen_ai.usage.input_tokens"] = 0
                attrs["gen_ai.usage.output_tokens"] = 0
                trace = project_trace(detail, project_id="space")
                decision = next(o for o in trace["observations"] if o["id"] == "decision")
                self.assertEqual(decision["model"], expected_model)
                self.assertEqual(decision["type"], "GENERATION")
                self.assertEqual(decision["usage"]["total"], 0)
                self.assertEqual(trace["usage"]["observationCount"], 2)

    def test_native_invalid_request_uses_response_before_optional_native_model(self):
        for invalid in ("", " \t", 7, True, {}, []):
            for native_model in (None, "native-model"):
                with self.subTest(invalid=invalid, native_model=native_model):
                    detail = routing_trace()
                    attrs = detail["spans"][1]["attributes"]
                    attrs.update({
                        "gen_ai.request.model": invalid,
                        "gen_ai.response.model": "response-model",
                        "gen_ai.usage.input_tokens": 0,
                        "gen_ai.usage.output_tokens": 0,
                    })
                    if native_model is None:
                        attrs.pop("agentic.decision.model")
                    else:
                        attrs["agentic.decision.model"] = native_model
                    trace = project_trace(detail, project_id="space")
                    decision = next(o for o in trace["observations"] if o["id"] == "decision")
                    self.assertEqual(decision["model"], "response-model")
                    self.assertEqual(decision["type"], "GENERATION")
                    self.assertEqual(decision["usage"]["total"], 0)
                    self.assertEqual(trace["usage"]["observationCount"], 2)

    def test_native_invalid_model_fields_preserve_langfuse_llm_native_order(self):
        for langfuse_model, llm_model, expected in (
            ("langfuse-model", "llm-model", "langfuse-model"),
            (" ", "llm-model", "llm-model"),
            ({}, False, "native-model"),
        ):
            with self.subTest(expected=expected):
                detail = routing_trace()
                attrs = detail["spans"][1]["attributes"]
                attrs.update({
                    "gen_ai.request.model": " ",
                    "gen_ai.response.model": [],
                    "langfuse.observation.model.name": langfuse_model,
                    "llm.model_name": llm_model,
                    "agentic.decision.model": "native-model",
                })
                trace = project_trace(detail, project_id="space")
                decision = next(o for o in trace["observations"] if o["id"] == "decision")
                self.assertEqual(decision["model"], expected)
                self.assertEqual(trace["usage"]["total"], 480)

    def test_non_native_model_precedence_is_unchanged(self):
        detail = routing_trace()
        attrs = detail["spans"][1]["attributes"]
        attrs.pop("agentic.decision.type")
        attrs.update({
            "gen_ai.span.kind": "LLM",
            "gen_ai.request.model": "",
            "gen_ai.response.model": "response-model",
        })
        trace = project_trace(detail, project_id="space")
        decision = next(o for o in trace["observations"] if o["id"] == "decision")
        self.assertEqual(decision["model"], "")
        self.assertEqual(trace["usage"]["total"], 480)

    def test_policy_and_parent_rollups_are_not_promoted_to_model_calls(self):
        cases = (
            {"agentic.decision.type": "model_routing", "agentic.decision.model": "jev"},
            {"agentic.decision.type": "model_routing", "gen_ai.usage.total_tokens": 390},
            {"gen_ai.request.model": "jev", "gen_ai.usage.total_tokens": 390},
            {"agentic.decision.type": "heuristic_rule", "gen_ai.request.model": "jev", "gen_ai.usage.total_tokens": 390},
            {"agentic.decision.type": "model_routing", "gen_ai.span.kind": "AGENT", "gen_ai.request.model": "jev", "gen_ai.usage.total_tokens": 390},
            {"agentic.decision.type": "tool_approval", "gen_ai.span.kind": "TOOL", "gen_ai.request.model": "jev", "gen_ai.usage.total_tokens": 390},
        )
        for attrs in cases:
            with self.subTest(attrs=attrs):
                detail = routing_trace()
                detail["spans"][0]["attributes"]["gen_ai.usage.total_tokens"] = 480
                detail["spans"][1]["attributes"] = attrs
                trace = project_trace(detail, project_id="space")
                decision = next(o for o in trace["observations"] if o["id"] == "decision")
                self.assertEqual(decision["type"], attrs.get("gen_ai.span.kind", "SPAN"))
                self.assertEqual(trace["usage"]["total"], 90)
                self.assertEqual(trace["usage"]["observationCount"], 1)

    def test_native_tool_approval_keeps_total_only_usage_known(self):
        for explicit_kind in (None, "DECISION"):
            with self.subTest(explicit_kind=explicit_kind):
                detail = routing_trace()
                attrs = {
                    "agentic.decision.type": "tool_approval",
                    "agentic.decision.model": "jev-latest",
                    "gen_ai.usage.total_tokens": 7,
                }
                if explicit_kind:
                    attrs["gen_ai.span.kind"] = explicit_kind
                detail["spans"][1]["attributes"] = attrs
                metrics = NativeTraceWindow([detail], project_id="space").metrics({})
                self.assertEqual(metrics["generationCount"], 2)
                self.assertEqual(metrics["usage"]["total"], 97)
                self.assertIsNone(metrics["usage"]["input"])
                self.assertEqual(metrics["coverage"]["knownTokens"], 97)

    def test_missing_placeholders_do_not_become_known_zero_calls(self):
        detail = routing_trace()
        attrs = detail["spans"][1]["attributes"]
        attrs.update({
            "gen_ai.usage.input_tokens": 0, "gen_ai.usage.output_tokens": 0,
            "agentic.usage.input_tokens.missing": True,
            "agentic.usage.output_tokens.missing": True,
        })
        trace = project_trace(detail, project_id="space")
        self.assertEqual(trace["usage"]["observationCount"], 1)
        self.assertEqual(trace["usage"]["total"], 90)
        attrs["gen_ai.usage.total_tokens"] = 0
        trace = project_trace(detail, project_id="space")
        self.assertEqual(trace["usage"]["observationCount"], 2)
        self.assertEqual(trace["usage"]["total"], 90)
        self.assertIsNone(trace["usage"]["input"])

    def test_nested_model_decisions_keep_ambiguity_even_in_grouped_metrics(self):
        detail = routing_trace()
        detail["spans"][2]["parent_span_id"] = "decision"
        metrics = NativeTraceWindow([detail], project_id="space").metrics({})
        self.assertIsNone(metrics["usage"]["total"])
        self.assertEqual(metrics["usage"]["unknownReason"], "ambiguous_nested_generations")
        self.assertEqual(metrics["reportedUsage"]["total"], {"value": 480, "reportedCalls": 2})
        self.assertEqual(metrics["coverage"]["knownTokens"], 480)
        for group in metrics["groups"]:
            self.assertIsNone(group["usage"]["total"])
            self.assertEqual(group["usage"]["unknownReason"], "ambiguous_nested_generations")

    def test_filtered_metrics_only_include_native_calls_in_selected_trace(self):
        window = NativeTraceWindow([routing_trace("jev"), routing_trace("kev")], project_id="space")
        result = window.metrics({"trace_id": "routing-kev"})
        self.assertEqual(result["traceCount"], 1)
        self.assertEqual(result["generationCount"], 2)
        self.assertEqual(result["usage"]["total"], 208)
        self.assertEqual(result["coverage"]["knownTokens"], 208)
        self.assertEqual(result["reportedUsage"]["total"], {"value": 208, "reportedCalls": 2})
