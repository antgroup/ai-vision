"""Identical concurrent dashboard sections share only their active snapshot."""
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Barrier, Event, Lock
from unittest.mock import patch
from types import SimpleNamespace

from django.test import SimpleTestCase, override_settings


class SnapshotInflightTests(SimpleTestCase):
    def test_every_authorization_and_window_dimension_is_part_of_the_key(self):
        from apps.core.trace_query.signoz.snapshot_inflight import scope_key
        client = SimpleNamespace(endpoint='http://storage.invalid', api_key='secret-fixture')
        wire = {'space_id': 'space', 'user': 'subject', 'agent_product': 'physical-product',
                'agent_uuid': 'agent', 'from': 1000, 'to': 2000, 'projection_context': 'context', 'raw': 'false'}
        expected = scope_key(client, wire)
        self.assertNotIn(client.api_key, expected)
        for field in wire:
            self.assertNotEqual(scope_key(client, {**wire, field: str(wire[field])+'x'}), expected)
        self.assertNotEqual(scope_key(SimpleNamespace(endpoint=client.endpoint, api_key='other'), wire), expected)
        self.assertNotEqual(scope_key(SimpleNamespace(endpoint='http://other.invalid', api_key=client.api_key), wire), expected)

    def test_failure_does_not_poison_a_later_request(self):
        from apps.core.trace_query.signoz.snapshot_inflight import read_once
        def fail():
            raise RuntimeError('storage unavailable')
        with self.assertRaisesRegex(RuntimeError, 'storage unavailable'):
            read_once('failed-authorized-window', fail)
        self.assertEqual(read_once('failed-authorized-window', lambda: 'fresh-read'), 'fresh-read')

    def test_concurrent_readers_share_work_but_next_request_reads_again(self):
        from apps.core.trace_query.signoz.snapshot_inflight import read_once
        barrier, release, entered, joined = Barrier(4), Event(), Event(), Event()
        lock = Lock()
        calls = []
        waiters = []
        original_result = Future.result

        def waiting(future, *args, **kwargs):
            with lock:
                waiters.append(1)
                if len(waiters) == 2:
                    joined.set()
            return original_result(future, *args, **kwargs)

        def work():
            with lock:
                calls.append(1)
            entered.set()
            self.assertTrue(release.wait(5))
            return {'complete': True}

        def request():
            barrier.wait(5)
            return read_once('same-authorized-window', work)

        with patch.object(Future, 'result', waiting), ThreadPoolExecutor(max_workers=3) as pool:
            tasks = [pool.submit(request) for _ in range(3)]
            barrier.wait(5)
            self.assertTrue(entered.wait(5))
            # Wait until both other threads have joined the active read.
            self.assertTrue(joined.wait(5))
            release.set()
            results = [task.result(5) for task in tasks]
        self.assertEqual(len(calls), 1)
        self.assertTrue(all(value is results[0] for value in results))
        read_once('same-authorized-window', work)
        self.assertEqual(len(calls), 2)

    @override_settings(AIVISION_TRACE_QUERY_CONCURRENCY=2, AIVISION_QUERY_MODE='auto')
    def test_filtered_metrics_followers_hold_their_own_admission_until_close(self):
        from django.http import HttpResponse

        from apps.core.tests.test_trace_query_memory import response
        from apps.core.trace_query.memory_budget import QueryMemoryBudget, TraceQueryMemoryMiddleware
        from apps.core.trace_query.signoz.provider import SignozTraceProvider, _SigNozClient
        from apps.core.trace_query.types import BaseTraceQuery, TraceQueryServiceError

        entered, joined, release = Event(), Event(), Event()
        original_result = Future.result
        provider = SignozTraceProvider()
        client = _SigNozClient({'endpoint': 'https://fixture.invalid', 'api_key': 'fixture'})
        query = BaseTraceQuery(SimpleNamespace(uuid='space', type=2), None, {
            'from': 1000, 'to': 2000, 'agent_uuid': 'agent', 'agent_product': 'product',
            # Canonical filters take the compact window without first calling
            # the aggregate endpoint (which would independently admit a query).
            'filters': [{'field': 'status_code', 'op': '=', 'value': 1}],
        })

        def storage(_url, *, params, **_kwargs):
            import json

            entered.set()
            if not release.wait(5):
                raise TimeoutError('snapshot read was not released')
            return response(json.dumps({
                'supported': True, 'complete': True,
                'contract': 'native-trace-metrics-window-v1', 'rows': [], 'raw': [],
                'meta': {**params, 'raw': False, 'representation': 'metrics-v1',
                         'omittedAgentTraceCount': 0, 'traceCount': 0,
                         'projectedTraceCount': 0, 'rawTraceCount': 0,
                         'spanCount': 0, 'rawSpanCount': 0},
            }).encode())

        def waiting(future, *args, **kwargs):
            joined.set()
            return original_result(future, *args, **kwargs)

        def view(_request):
            result = provider.query_metrics(query)
            self.assertEqual(result['traceCount'], 0)
            return HttpResponse('complete')

        responses = []
        probes = [QueryMemoryBudget(), QueryMemoryBudget()]
        middleware = TraceQueryMemoryMiddleware(view)
        with patch.object(provider, '_context', return_value=(client, {})), \
             patch('apps.core.trace_query.signoz.snapshot_window.get_space_model_prices', return_value=[]), \
             patch('apps.core.trace_query.signoz.provider.requests.get', side_effect=storage), \
             patch.object(Future, 'result', waiting), ThreadPoolExecutor(max_workers=2) as pool:
            try:
                owner = pool.submit(middleware, None)
                self.assertTrue(entered.wait(5))
                follower = pool.submit(middleware, None)
                self.assertTrue(joined.wait(5))
                release.set()
                responses.extend([original_result(owner, timeout=5), original_result(follower, timeout=5)])

                with self.assertRaises(TraceQueryServiceError) as busy:
                    probes[0].admit()
                self.assertEqual(busy.exception.code, 3037)

                responses[0].close()
                probes[0].admit()
                with self.assertRaises(TraceQueryServiceError) as busy:
                    probes[1].admit()
                self.assertEqual(busy.exception.code, 3037)
            finally:
                release.set()
                for item in responses:
                    item.close()
                for item in probes:
                    item.close()
