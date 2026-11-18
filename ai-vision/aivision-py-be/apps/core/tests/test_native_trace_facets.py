"""Lazy native environment facets retain the frozen list request scope."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from apps.core.tests.test_native_trace_summary import environment_trace
from apps.core.trace_query.native_query import NativeTraceWindow
from apps.core.trace_query.signoz.provider import SignozTraceProvider
from apps.core.trace_query.types import TraceListQuery
from apps.core.views.trace_handlers import merge_session_list_params, merge_trace_list_params
from apps.core.views.trace_unified import UnifiedTraceFacetsView


class NativeTraceFacetsTests(SimpleTestCase):
    def test_native_facets_http_boundary_keeps_identity_and_all_list_predicates(self):
        payload = {
            "from": "1790985600000", "to": "1791072000000", "agent_product": "public-product",
            "agent_uuid": "agent-a", "filters": [{"field": "environment", "op": "!=", "value": "dev"}],
            "searchType": "input", "keyword": "request", "input_keyword": "question", "tags": ["sdk"],
        }
        service = Mock()
        service.list_facets.return_value = {"data": {"env": []}, "meta": {"complete": True}}
        request = SimpleNamespace(data=payload, query_params={}, user=object())
        view = UnifiedTraceFacetsView()
        view.source_system = "signoz"
        with patch("apps.core.views.trace_unified._get_space_or_error", return_value=(object(), None)), \
             patch("apps.core.views.trace_unified._check_access", return_value=None), \
             patch("apps.core.views.trace_unified.get_default_trace_query_service", return_value=service):
            response = view.post(request, "space")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(service.list_facets.call_args.kwargs["query_params"], payload)
        self.assertEqual(service.list_facets.call_args.kwargs["source_system_override"], "signoz")

    def test_trace_env_shortcut_does_not_gain_a_raw_predicate_and_session_retains_it(self):
        detail = environment_trace({"langfuse.environment": "local"}, {"ant.agent.env": "prod"})
        params = merge_trace_list_params({"env": "local"}, {})
        self.assertEqual(NativeTraceWindow([detail], project_id="space").list(params)["meta"]["totalItems"], 1)
        self.assertNotIn("filters", params)
        self.assertEqual(merge_session_list_params({"env": "local"}, {})["filters"], [
            {"field": "resource.attributes.ant.agent.env", "op": "=", "value": "local"},
        ])

    def test_standalone_facets_identify_canonical_complete_environment(self):
        provider = SignozTraceProvider()
        query = TraceListQuery(SimpleNamespace(uuid="space", type=2), None, {"agent_uuid": "agent-a"})
        window = NativeTraceWindow([environment_trace({"langfuse.environment": "local"})], project_id="space")
        # Negotiate an old storage server at the HTTP boundary, then exercise
        # the existing complete-window facet aggregation below it.
        with (
            patch("apps.core.trace_query.signoz.provider.resolve_signoz_runtime_config",
                  return_value={"endpoint": "http://signoz-query.invalid"}),
            patch("apps.core.trace_query.signoz.provider.requests.get", return_value=Mock(status_code=404)),
            patch.object(provider, "_window", return_value=(window, {"complete": True, "from": 1000, "to": 2000})),
        ):
            result = provider.list_facets(query)
        self.assertIs(result["meta"].get("environmentFacetsComplete"), True)
        self.assertEqual(result["meta"].get("environmentSemantics"), "root-span-resource-v1")
        self.assertEqual(result["meta"]["from"], 1000)
        self.assertEqual(result["meta"]["to"], 2000)
