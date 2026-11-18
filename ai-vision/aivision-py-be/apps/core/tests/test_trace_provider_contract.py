from django.test import SimpleTestCase

from apps.core.trace_query.defaults import get_default_trace_query_service


class TraceProviderContractTests(SimpleTestCase):
    def test_registered_trace_providers_implement_common_methods(self):
        service = get_default_trace_query_service()
        required_methods = (
            "list_traces",
            "get_trace_io_batch",
            "get_trace_usage_batch",
            "get_trace_detail",
            "list_sessions",
            "get_session_detail",
            "get_session_io_batch",
            "get_session_io",
            "get_session_usage_batch",
            "get_span_batch",
            "get_observation_detail",
        )

        for source_system in ("langfuse", "signoz"):
            _name, provider = service._resolve_provider_by_source_system(source_system)
            missing = [
                method
                for method in required_methods
                if not callable(getattr(provider, method, None))
            ]
            self.assertEqual(missing, [], f"{source_system} missing {missing}")
