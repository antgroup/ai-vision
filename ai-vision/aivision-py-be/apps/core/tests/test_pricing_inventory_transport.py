"""Exercise inventory errors at the streamed SigNoz HTTP boundary."""
import json
from unittest.mock import Mock, patch

from django.test import TestCase, override_settings
from urllib3.exceptions import ProtocolError, ReadTimeoutError

from apps.core.tests import test_pricing_inventory_snapshot as fixtures
from apps.core.tests.test_trace_query_memory import response


@override_settings(AIVISION_QUERY_MODE='auto')
class PricingInventoryTransportTests(TestCase):
    def setUp(self):
        fixtures.PricingInventorySnapshotTests.setUp(self)

    def assert_inventory_error(self, wire, *, status, message):
        self.get.reset_mock()
        self.get.side_effect = lambda *args, **kwargs: wire
        self.client.raise_request_exception = False
        with patch.object(wire, 'close', wraps=wire.close) as close:
            result = self.client.get(self.base + '/inventory', self.params)
            close.assert_called_once()
        self.get.assert_called_once()
        self.legacy.assert_not_called()
        self.assertEqual(result.status_code, status, result.content[:500])
        self.assertEqual(result.data['code'], 3035)
        self.assertEqual(result.data['message'], message)

    def test_malformed_json_yields_sanitized_error_and_closes_response(self):
        self.assert_inventory_error(
            response(b'{"data":"private upstream"'), status=422, message='Invalid SigNoz response',
        )

    def test_decoded_object_without_snapshot_contract_is_rejected(self):
        self.assert_inventory_error(
            response(b'{"data":[["private upstream"]]}'), status=422,
            message='Invalid complete Trace snapshot: missing complete snapshot contract',
        )

    def test_json_recursion_error_yields_sanitized_error_and_closes_response(self):
        # The decoder's nesting tolerance differs across supported runtimes.
        # Inject its exception rather than assuming a specific depth triggers it.
        for upstream_status, status, message, method in (
            (200, 422, 'Invalid SigNoz response', 'load'),
            (400, 502, 'Native pricing inventory query failed; retry shortly', 'loads'),
        ):
            with self.subTest(upstream_status=upstream_status):
                with patch('apps.core.trace_query.signoz.provider.json', wraps=json) as decoder:
                    failing_method = getattr(decoder, method)
                    failing_method.side_effect = RecursionError('private upstream content')
                    self.assert_inventory_error(response(b'{"data":[]}', status=upstream_status),
                                                status=status, message=message)
                    failing_method.assert_called_once()

    def test_streamed_timeout_is_distinct_from_connection_failure(self):
        for error, status, message in (
            (ReadTimeoutError(None, '/private-endpoint', 'private upstream'),
             504, 'Native pricing inventory query timed out; retry shortly'),
            (ProtocolError('private upstream disconnected'),
             502, 'Native pricing inventory query failed; retry shortly'),
        ):
            with self.subTest(error=type(error).__name__):
                def stream(*_args, **_kwargs):
                    yield b'{"data":'
                    raise error

                wire = response(b'')
                wire.raw = Mock(stream=stream)
                self.assert_inventory_error(wire, status=status, message=message)
