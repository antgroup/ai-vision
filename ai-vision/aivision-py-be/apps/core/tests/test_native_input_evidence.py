"""Original input evidence is projected only from verified complete observations."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from apps.core.tests.test_native_query import trace
from apps.core.trace_query.native_query import NativeTraceWindow
from apps.core.trace_query.signoz.pagination import CONTRACT
from apps.core.trace_query.signoz.provider import SignozTraceProvider
from apps.core.trace_query.signoz.snapshot_window import needs_raw_observations
from apps.core.trace_query.types import TraceListQuery, TraceQueryServiceError


class NativeInputEvidenceTests(SimpleTestCase):
    def test_explicit_trace_input_has_independent_context_when_equal_to_derived_messages(self):
        detail = trace(1)
        messages = [{"role": "user", "content": [0, False, "[REDACTED]"]}]
        attrs = detail["spans"][0]["attributes"]
        attrs.update({
            "gen_ai.input.messages": messages,
            "langfuse.trace.input": messages,
            "gen_ai.operation.name": "chat",
            "aivision.normalization.sources": {"gen_ai.input.messages": "sdk"},
        })
        row = NativeTraceWindow([detail], project_id="space").list(
            {"fields": ["trace_id", "inputEvidence"]}
        )["data"][0]
        observation_source, trace_source = row["inputEvidence"]
        self.assertEqual(observation_source["attributes"]["gen_ai.input.messages"], messages)
        self.assertEqual(
            observation_source["attributes"]["aivision.normalization.sources"],
            attrs["aivision.normalization.sources"],
        )
        self.assertEqual(trace_source["input"], messages)
        self.assertEqual(trace_source["inputSource"], "langfuse.trace.input")
        self.assertNotIn("gen_ai.input.messages", trace_source["attributes"])
        self.assertEqual(trace_source["attributes"]["gen_ai.operation.name"], "chat")
        self.assertEqual(
            trace_source["attributes"]["aivision.normalization.sources"],
            attrs["aivision.normalization.sources"],
        )

    def test_stable_optional_evidence_keeps_original_payload_and_kind_without_polluting_summaries(self):
        detail = trace(1)
        attrs = detail["spans"][0]["attributes"]
        attrs.update({"gen_ai.operation.name": "execute_tool", "gen_ai.input.messages": [{"role": "user", "content": "derived"}],
                      "langfuse.observation.input": {"args": [0, False, "[REDACTED]"]},
                      "aivision.normalization.sources": {"gen_ai.input.messages": "sdk"}})
        window = NativeTraceWindow([detail], project_id="space")
        self.assertNotIn("inputEvidence", window.rows[0])
        self.assertNotIn("inputEvidence", window.list({})["data"][0])
        row = window.list({"fields": ["trace_id", "inputEvidence"]})["data"][0]
        source = row["inputEvidence"][0]
        self.assertEqual(source["input"], attrs["langfuse.observation.input"])
        self.assertEqual(source["attributes"]["aivision.normalization.sources"], attrs["aivision.normalization.sources"])
        self.assertEqual(source["attributes"]["gen_ai.operation.name"], "execute_tool")
        self.assertEqual(source["inputSource"], "langfuse.observation.input")
        self.assertEqual(NativeTraceWindow([], project_id="s").list({"fields": ["inputEvidence"]})["data"], [])
        self.assertTrue(needs_raw_observations({"fields": ["inputEvidence"]}))
        with self.assertRaisesRegex(TraceQueryServiceError, "projection only"):
            window.list({"filters": [{"field": "inputEvidence", "op": "IS_NOT_NULL"}]})

    @override_settings(AIVISION_QUERY_MODE="auto")
    def test_evidence_bypasses_summary_and_reads_only_the_existing_complete_trace_page(self):
        provider = SignozTraceProvider()
        space = SimpleNamespace(uuid="space", type=2)
        params = {"from": 1790985600000, "to": 1791072000000, "agent_product": "public-product",
                  "agent_uuid": "agent-a", "page": 1, "limit": 20, "fields": ["trace_id", "inputEvidence"]}
        detail = trace(1)
        detail["spans"][0]["resource"] = {"aivision.space.id": "space", "aivision.agent.id": "agent-a"}
        detail["spans"][0]["attributes"]["gen_ai.input.messages"] = [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "[REDACTED]"}}]}]
        payload = {"data": [{**deepcopy(detail), "spanCount": 1}], "facets": {"env": [{"value": "default", "count": 1001}], "scene": [], "username": [], "tags": []},
                   "meta": {"contract": CONTRACT, "complete": True, "environmentSemantics": "root-span-resource-v1",
                            "userIdentitySemantics": "trace-sdk-user-id-v1", "page": 1, "limit": 20, "totalItems": 1001,
                            "totalPages": 51, "from": params["from"], "to": params["to"], "space_id": "space",
                            "agent_product": "stored-product", "agent_uuid": "agent-a", "user": ""}}
        # The contract requires a full page except the last one.
        params.update(page=51)
        payload["meta"].update(page=51)
        client = Mock()
        client.get.return_value = payload
        with patch.object(provider, "_context", return_value=(client, {"agent_product_filter": "stored-product"})), \
             patch("apps.core.trace_query.signoz.snapshot_window.query_snapshot_window") as snapshot, \
             patch.object(provider, "collect_traces") as scan:
            result = provider.query_traces(TraceListQuery(space, None, params))
        snapshot.assert_not_called()
        scan.assert_not_called()
        client.get.assert_called_once()
        self.assertEqual(client.get.call_args.args[0], "/api/v1/ai-vision/traces/page")
        self.assertNotIn("fields", client.get.call_args.kwargs["params"])
        self.assertEqual(result["meta"]["totalItems"], 1001)
        self.assertEqual(result["meta"]["readModel"]["reason"], "unsupported_query")
        self.assertEqual(result["data"][0]["inputEvidence"][0]["input"], detail["spans"][0]["attributes"]["gen_ai.input.messages"])
