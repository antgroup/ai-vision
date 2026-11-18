import io
import json
from unittest.mock import patch

import requests
from urllib3.response import HTTPResponse
from django.test import SimpleTestCase, override_settings

from apps.core.trace_query.signoz.provider import _SigNozClient
from apps.core.trace_query.types import TraceQueryServiceError


def response(body, headers=None, status=200):
    result = requests.Response()
    result.status_code = status
    result.headers.update(headers or {})
    result.raw = HTTPResponse(body=io.BytesIO(body), preload_content=False)
    return result


@override_settings(AIVISION_TRACE_RESPONSE_MAX_BYTES=1024)
class TraceResponseMemoryTests(SimpleTestCase):
    def test_large_response_rejected_without_content_length(self):
        with self.assertRaises(TraceQueryServiceError) as raised:
            _SigNozClient._decode(response(json.dumps({'value': 'x' * 2048}).encode()))
        self.assertEqual(raised.exception.code, 3036)

    def test_declared_large_response_is_not_read(self):
        raw = response(b'', {'Content-Length': '2048'})
        with patch.object(raw, 'iter_content', side_effect=AssertionError('body read')):
            with self.assertRaises(TraceQueryServiceError):
                _SigNozClient._decode(raw)

    def test_small_response_keeps_exact_int64_and_closes(self):
        raw = response(b'{"status":"success","data":{"id":1790985600000000001}}')
        self.assertEqual(_SigNozClient._decode(raw), {'id': 1790985600000000001})
        self.assertTrue(raw.raw.closed)

    def test_old_provider_recovery_budget_is_capacity_error(self):
        body = {"status": "error", "error": {"code": "invalid_input", "message":
            "complete recovery window exceeds resource bound (512 MiB source); narrow the time range"}}
        raw = response(json.dumps(body).encode(), status=400)
        with self.assertRaises(TraceQueryServiceError) as raised:
            _SigNozClient._decode(raw)
        self.assertEqual(raised.exception.code, 3036)
        self.assertIn("narrow the time range", str(raised.exception))
        self.assertTrue(raw.raw.closed)

    def test_arbitrary_400_and_oversized_error_are_not_capacity_errors(self):
        for body in (b'{"error":{"code":"invalid_input","message":"invalid filter"}}',
                     b'not json', b'[]', b'x' * 9000):
            with self.subTest(body_length=len(body)):
                raw = response(body, status=400)
                with self.assertRaises(requests.HTTPError):
                    _SigNozClient._decode(raw)
                self.assertTrue(raw.raw.closed)

    def test_http_failure_closes_response(self):
        raw = response(b'error', status=500)
        with self.assertRaises(requests.HTTPError):
            _SigNozClient._decode(raw)
        self.assertTrue(raw.raw.closed)

@override_settings(AIVISION_TRACE_QUERY_CONCURRENCY=1, AIVISION_TRACE_REQUEST_MAX_BYTES=100)
class TraceAdmissionTests(SimpleTestCase):
    def test_slot_covers_rendering_until_response_close(self):
        from django.http import HttpResponse
        from apps.core.trace_query.memory_budget import TraceQueryMemoryMiddleware, admit_query

        def view(request):
            admit_query()
            return HttpResponse('rendered')

        middleware = TraceQueryMemoryMiddleware(view)
        first = middleware(object())
        try:
            with self.assertRaises(TraceQueryServiceError) as error:
                middleware(object())
            self.assertEqual(error.exception.code, 3037)
        finally:
            first.close()
        second = middleware(object())
        second.close()
        second.close()  # cleanup is idempotent

    def test_cumulative_bytes_and_failed_requests_release_slot(self):
        from apps.core.trace_query.memory_budget import TraceQueryMemoryMiddleware, admit_query, consume_bytes

        def failing_view(request):
            admit_query()
            consume_bytes(60)
            consume_bytes(60)

        for _ in range(2):
            with self.assertRaises(TraceQueryServiceError) as error:
                TraceQueryMemoryMiddleware(failing_view)(object())
            self.assertEqual(error.exception.code, 3036)

    def test_control_plane_requests_do_not_take_a_slot(self):
        from django.http import HttpResponse
        from apps.core.trace_query.memory_budget import TraceQueryMemoryMiddleware
        middleware = TraceQueryMemoryMiddleware(lambda request: HttpResponse('ok'))
        first, second = middleware(object()), middleware(object())
        first.close()
        second.close()

@override_settings(AIVISION_TRACE_RESPONSE_MAX_BYTES=1024)
class TraceStreamingEdgeTests(SimpleTestCase):
    def test_external_call_metrics_include_stream_consumption_and_failures(self):
        from apps.core import metrics

        for method in ('get', 'post'):
            for failure in (False, True):
                with self.subTest(method=method, failure=failure):
                    clock = [0.0]
                    durations, errors = [], []

                    class DelayedBody(io.BytesIO):
                        def read(self, *args, **kwargs):
                            clock[0] += 2.0
                            if failure:
                                raise OSError('upstream body interrupted')
                            return super().read(*args, **kwargs)

                    raw = response(b'{}')
                    raw.raw = HTTPResponse(body=DelayedBody(b'{}'), preload_content=False)
                    client = _SigNozClient({'endpoint': 'https://fixture.invalid'})
                    with patch(f'apps.core.trace_query.signoz.provider.requests.{method}', return_value=raw), \
                         patch.object(metrics.time, 'monotonic', side_effect=lambda: clock[0]), \
                         patch.object(metrics.external_call_duration, 'record', side_effect=lambda value, attrs: durations.append((value, attrs))), \
                         patch.object(metrics.external_call_errors, 'add', side_effect=lambda value, attrs: errors.append((value, attrs))):
                        if failure:
                            with self.assertRaises(requests.exceptions.ChunkedEncodingError):
                                getattr(client, method)('/page', **({'payload': {}} if method == 'post' else {}))
                        else:
                            self.assertEqual(getattr(client, method)('/page', **({'payload': {}} if method == 'post' else {})), {})
                    self.assertEqual(len(durations), 1)
                    self.assertGreaterEqual(durations[0][0], 2.0)
                    self.assertEqual(durations[0][1]['status'], 'error' if failure else 'success')
                    self.assertEqual(sum(value for value, _ in errors), 1 if failure else 0)
                    self.assertTrue(raw.raw.closed)

    def test_gzip_is_limited_by_decoded_bytes(self):
        import gzip
        body = gzip.compress(json.dumps({'value': 'x' * 10000}).encode())
        raw = response(body, {'Content-Encoding': 'gzip', 'Content-Length': str(len(body))})
        raw.raw.headers['Content-Encoding'] = 'gzip'
        with self.assertRaises(TraceQueryServiceError) as error:
            _SigNozClient._decode(raw)
        self.assertEqual(error.exception.code, 3036)
        self.assertTrue(raw.raw.closed)

    def test_small_declared_length_cannot_bypass_decoded_limit(self):
        with self.assertRaises(TraceQueryServiceError):
            _SigNozClient._decode(response(b'{"value":"' + b'x' * 2048 + b'"}', {'Content-Length': '10'}))

    def test_get_and_post_request_streaming(self):
        client = _SigNozClient({'endpoint': 'https://fixture.invalid'})
        for method in ('get', 'post'):
            with patch(f'apps.core.trace_query.signoz.provider.requests.{method}', return_value=response(b'{}')) as request:
                getattr(client, method)('/page', **({'payload': {}} if method == 'post' else {}))
            self.assertIs(request.call_args.kwargs['stream'], True)

class BackgroundProjectionMemoryTests(SimpleTestCase):
    @override_settings(AIVISION_TRACE_PROJECTION_MAX_BYTES=100)
    def test_snapshot_recovery_shares_a_background_budget(self):
        from apps.core.trace_query.memory_budget import consume_bytes
        from apps.core.trace_query.signoz.read_model_projection import project_batch
        from unittest.mock import Mock
        client = Mock()
        client._projection_batch_sources = False
        client.get.side_effect = lambda path, params: (
            {'supported': True, 'data': [{'trace_id': 'a'}, {'trace_id': 'b'}]}
            if path.endswith('/dirty') else {'trace_id': params['trace_id'], 'supported': True})
        observed = []
        def recovery(client, space, identifier, snapshot):
            consume_bytes(60)
            observed.append(identifier)
            return snapshot
        with patch('apps.core.trace_query.signoz.read_model_projection._recover_source', side_effect=recovery), \
             patch('apps.core.trace_query.signoz.read_model_projection.build_projection', side_effect=lambda *a, **k: {'trace_id': a[0]['trace_id'], 'row': {}, 'source_revision': 1}), \
             patch('apps.core.trace_query.signoz.read_model_projection._publish_chunk', side_effect=lambda c, payloads, sizes: [{'supported': True, 'published': True, 'source_revision': 1} for _ in payloads]):
            result = project_batch(client, space_id='space', prices=[])
        self.assertEqual(result['published'], 2)
        # The combined 120-byte load must stop, drop references and retry bounded
        # halves; merely limiting individual HTTP responses would yield a,b once.
        self.assertEqual(observed, ['a', 'a', 'b'])

    @override_settings(AIVISION_TRACE_PROJECTION_MAX_BYTES=100)
    def test_oversized_source_stays_dirty_without_starving_next_trace(self):
        from apps.core.trace_query.memory_budget import consume_bytes
        from apps.core.trace_query.signoz.read_model_projection import project_batch, ProjectionDeferred
        from unittest.mock import Mock
        client = Mock()
        client._projection_batch_sources = False
        client.get.side_effect = lambda path, params: (
            {'supported': True, 'data': [{'trace_id': 'a'}, {'trace_id': 'b'}]}
            if path.endswith('/dirty') else {'trace_id': params['trace_id'], 'supported': True})
        def recovery(client, space, identifier, snapshot):
            consume_bytes(120 if identifier == 'a' else 20)
            return snapshot
        def build(snapshot, **kwargs):
            if snapshot.get('supported') is False:
                raise ProjectionDeferred(snapshot['reason'])
            return {'trace_id': snapshot['trace_id'], 'row': {}, 'source_revision': 1}
        with patch('apps.core.trace_query.signoz.read_model_projection._recover_source', side_effect=recovery), \
             patch('apps.core.trace_query.signoz.read_model_projection.build_projection', side_effect=build), \
             patch('apps.core.trace_query.signoz.read_model_projection._publish_chunk', side_effect=lambda c, payloads, sizes: [{'supported': True, 'published': True, 'source_revision': 1} for _ in payloads]):
            result = project_batch(client, space_id='space', prices=[], limit=2)
        self.assertEqual((result['seen'], result['deferred'], result['published']), (2, 1, 1))
        self.assertEqual(result['afterTraceId'], 'b')

    @override_settings(AIVISION_TRACE_PROJECTION_MAX_BYTES=6500)
    def test_real_fragment_decoding_is_bounded_across_background_traces(self):
        import base64
        from apps.core.trace_query.signoz.read_model_projection import project_batch
        source_reads, published = [], []
        def get(url, *, params, **kwargs):
            if url.endswith('/dirty'):
                data = {'supported': True, 'data': [{'trace_id': 'a'}, {'trace_id': 'b'}]}
            elif url.endswith('/batch-snapshot'):
                data = {'supported': True, 'contract': 'native-trace-source-batch-v1', 'data': [
                    {'supported': False, 'reason': 'source_snapshot_outside_batch_bounds', 'trace_id': identifier}
                    for identifier in json.loads(params['trace_ids'])]}
            else:
                self.assertTrue(url.endswith('/source-page'))
                identifier = params['trace_id']
                source_reads.append(identifier)
                span = {'trace_id': identifier, 'span_id': 'span', 'name': 'root', 'kind': 'internal',
                        'status_code': 1, 'start_time_unix_nano': '1790985600000000000',
                        'end_time_unix_nano': '1790985600001000000',
                        'resource_attributes': json.dumps({'aivision.space.id': 'space', 'aivision.agent.id': 'agent'}),
                        'attributes': json.dumps({'gen_ai.input.messages': 'x' * 2000}),
                        'payload': '{}', 'events': '[]', 'links': '[]'}
                encoded = json.dumps(span).encode()
                data = dict(supported=True, contract='native-trace-source-pages-v1', trace_id=identifier,
                            source_revision=3, maintenance_generation=7, source_user_id='owner',
                            source_agent_product='product', scope_valid=True, span_count=1, source_bytes=len(encoded),
                            min_timestamp='1790985600000000000', max_timestamp='1790985600000000000',
                            page_byte_limit=2 << 20, trace_byte_limit=128 << 20, complete=True, next_cursor='',
                            fragments=[{'span_id': 'span', 'offset': 0, 'total_bytes': len(encoded),
                                        'data_base64': base64.b64encode(encoded).decode()}])
            return response(json.dumps(data).encode())
        def post(url, *, json, **kwargs):
            entries = json['projections']
            published.extend(p['trace_id'] for p in entries)
            return response(__import__('json').dumps({'supported': True, 'contract': 'native-trace-publish-batch-v1',
                'data': [{'trace_id': p['trace_id'], 'supported': True, 'published': True, 'source_revision': 3} for p in entries]}).encode())
        with patch('apps.core.trace_query.signoz.provider.requests.get', side_effect=get), \
             patch('apps.core.trace_query.signoz.provider.requests.post', side_effect=post):
            result = project_batch(_SigNozClient({'endpoint': 'https://fixture.invalid'}), space_id='space', prices=[])
        self.assertEqual(result['published'], 2)
        self.assertEqual(published, ['a', 'b'])
        self.assertEqual(source_reads, ['a', 'b', 'a', 'b'])
