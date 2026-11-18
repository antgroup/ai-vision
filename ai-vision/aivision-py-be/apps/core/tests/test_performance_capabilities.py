from unittest.mock import Mock
from django.test import SimpleTestCase
from apps.core.trace_query.types import TraceQueryServiceError


class PerformanceCapabilitiesTests(SimpleTestCase):
    def test_old_endpoint_is_negative_capability_but_auth_and_transport_are_errors(self):
        from apps.core.trace_query.signoz.performance_capabilities import performance_capabilities
        client = Mock()
        client.get.side_effect = TraceQueryServiceError('not found', 3019)
        self.assertEqual(performance_capabilities(client), {})
        for code in (3013, 3012):
            client = Mock(); client.get.side_effect = TraceQueryServiceError('unavailable', code)
            with self.assertRaises(TraceQueryServiceError):
                performance_capabilities(client)

    def test_malformed_success_cannot_authorize_partial_content_publication(self):
        from apps.core.trace_query.signoz.performance_capabilities import performance_capabilities
        client = Mock()
        client.get.return_value = {'supported': True, 'contract': 'unknown'}
        with self.assertRaises(TraceQueryServiceError):
            performance_capabilities(client)

    def test_only_explicit_versioned_flags_authorize_optional_parameters(self):
        from apps.core.trace_query.signoz.performance_capabilities import performance_capabilities
        client = Mock()
        client.get.return_value = {'supported': True, 'contract': 'native-trace-performance-capabilities-v1',
                                  'content_projection': 'native-trace-content-v1', 'operation_facts': True, 'source_pages': True}
        self.assertTrue(performance_capabilities(client)['operation_facts'])
        self.assertEqual(performance_capabilities(client)['content_projection'], 'native-trace-content-v1')
