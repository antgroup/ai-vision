"""Background projection admission must cover all retained source/payload data."""
import gc
import json
import threading
import tracemalloc
import weakref
from concurrent.futures import ThreadPoolExecutor
from contextvars import Context
from unittest.mock import patch

from django.http import HttpResponse
from django.test import SimpleTestCase, override_settings
import requests

from apps.core.tests.test_native_query import trace
from apps.core.trace_query.memory_budget import (
    QueryMemoryBudget, TraceQueryMemoryMiddleware, admit_query, consume_bytes, query_memory_scope,
)
from apps.core.trace_query.signoz import read_model_projection as projection
from apps.core.trace_query.types import TraceQueryServiceError


class Snapshot(dict):
    pass


def source(identifier):
    detail = trace(1)
    detail['spans'][0]['trace_id'] = identifier
    detail['spans'][0]['resource_attributes'] = {
        'aivision.space.id': 'space', 'aivision.agent.id': 'agent-a',
    }
    return Snapshot(supported=True, contract='native-trace-source-v1', complete=True,
                    trace_id=identifier, source_revision=3, maintenance_generation=7,
                    span_count=1, spans=detail['spans'], scope_valid=True,
                    source_user_id='collector', source_agent_product='product')


class Client:
    def __init__(self, identifiers, on_source=lambda: None, on_publish=lambda: None):
        self.identifiers = identifiers
        self.on_source = on_source
        self.on_publish = on_publish
        self.sources = []
        self.published = []

    def get(self, path, *, params):
        if path.endswith('/dirty'):
            return {'supported': True, 'data': [{'trace_id': i} for i in self.identifiers]}
        self.on_source()
        snapshots = [source(i) for i in json.loads(params['trace_ids'])]
        self.sources.extend(weakref.ref(item) for item in snapshots)
        return {'supported': True, 'contract': 'native-trace-source-batch-v1', 'data': snapshots}

    def post(self, path, *, payload):
        self.on_publish()
        self.published.extend(p['trace_id'] for p in payload['projections'])
        return {'supported': True, 'contract': 'native-trace-publish-batch-v1', 'data': [
            {'trace_id': p['trace_id'], 'supported': True, 'published': True, 'source_revision': p.get('source_revision', 3)}
            for p in payload['projections']]}


@override_settings(AIVISION_TRACE_QUERY_CONCURRENCY=1)
class ProjectionMemoryLifetimeTests(SimpleTestCase):
    def assert_slot_busy(self):
        other = QueryMemoryBudget()
        try:
            with self.assertRaises(TraceQueryServiceError) as raised:
                other.admit()
            self.assertEqual(raised.exception.code, 3037)
        finally:
            other.close()

    def assert_slot_free(self):
        other = QueryMemoryBudget()
        try:
            other.admit()
        finally:
            other.close()

    def test_slot_covers_build_and_publication(self):
        client = Client(['a'], on_source=self.assert_slot_busy, on_publish=self.assert_slot_busy)
        original = projection.build_projection
        def build(*args, **kwargs):
            self.assert_slot_busy()
            return original(*args, **kwargs)
        with patch.object(projection, 'build_projection', new=build):
            result = projection.project_batch(client, space_id='space', prices=[])
        self.assertEqual(result['published'], 1)
        self.assert_slot_free()

    def test_generator_close_releases_admission(self):
        chunks = projection._bounded_snapshot_chunks(Client(['a']), 'space', ['a'])
        try:
            next(chunks)
            self.assert_slot_busy()
        finally:
            chunks.close()
        self.assert_slot_free()

    def test_slow_publication_holds_admission_until_worker_finishes(self):
        publishing, resume = threading.Event(), threading.Event()
        def slow_publish():
            publishing.set()
            if not resume.wait(5):
                raise TimeoutError('publication was not resumed')
        with ThreadPoolExecutor(max_workers=1) as executor:
            work = executor.submit(projection.project_batch, Client(['a'], on_publish=slow_publish),
                                   space_id='space', prices=[])
            try:
                self.assertTrue(publishing.wait(5))
                self.assert_slot_busy()
            finally:
                resume.set()
            self.assertEqual(work.result(timeout=5)['published'], 1)
        self.assert_slot_free()

    def test_publication_failure_and_cancellation_release_admission(self):
        for failure in (RuntimeError('publish failed'), KeyboardInterrupt()):
            with self.subTest(failure=type(failure).__name__):
                def fail():
                    self.assert_slot_busy()
                    raise failure
                with self.assertRaises(type(failure)):
                    projection.project_batch(Client(['a'], on_publish=fail), space_id='space', prices=[])
                self.assert_slot_free()

    def test_old_sources_and_payloads_are_released_before_next_batch_load(self):
        payload_refs = []
        client = Client([f'{i:03}' for i in range(101)])
        def verify_release():
            gc.collect()
            self.assertTrue(all(item() is None for item in client.sources), 'previous source batch remains live')
            self.assertTrue(all(item() is None for item in payload_refs), 'previous projection payload remains live')
        client.on_source = verify_release
        original = projection.build_projection
        def build(*args, **kwargs):
            payload = Snapshot(original(*args, **kwargs))
            payload_refs.append(weakref.ref(payload))
            return payload
        with patch.object(projection, 'build_projection', new=build):
            result = projection.project_batch(client, space_id='space', prices=[], limit=101)
        self.assertEqual(result['published'], 101)
        verify_release()

    @override_settings(AIVISION_TRACE_REQUEST_MAX_BYTES=100)
    def test_http_parent_stays_admitted_and_keeps_its_byte_count(self):
        def view(_request):
            admit_query()
            consume_bytes(30)
            projection.project_batch(Client(['a'], on_source=lambda: consume_bytes(40)), space_id='space', prices=[])
            self.assert_slot_busy()
            with self.assertRaises(TraceQueryServiceError) as raised:
                consume_bytes(31)
            self.assertEqual(raised.exception.code, 3036)
            return HttpResponse('ok')
        response = TraceQueryMemoryMiddleware(view)(object())
        try:
            Context().run(self.assert_slot_busy)
        finally:
            response.close()
        self.assert_slot_free()

    def test_cancelled_http_parent_releases_admission(self):
        from apps.core.trace_query import memory_budget
        budgets = []
        def view(_request):
            budgets.append(memory_budget._current.get())
            admit_query()
            raise KeyboardInterrupt()
        try:
            with self.assertRaises(KeyboardInterrupt):
                TraceQueryMemoryMiddleware(view)(object())
            self.assert_slot_free()
        finally:
            for budget in budgets:
                budget.close()


class ProjectionEnvelopeMemoryTests(SimpleTestCase):
    def test_size_check_does_not_materialize_a_second_full_batch(self):
        payloads = [{'trace_id': identifier, 'value': 'x' * (5 << 20)} for identifier in ('a', 'b')]
        sizes = [len(json.dumps(p, allow_nan=False).encode()) for p in payloads]
        for supplied, max_peak in ((None, 12 << 20), (sizes, 64 << 10)):
            with self.subTest(reuse=supplied is not None):
                client = Client([])
                tracemalloc.start()
                try:
                    receipts = projection._publish_chunk(client, payloads, supplied)
                    _, peak = tracemalloc.get_traced_memory()
                finally:
                    tracemalloc.stop()
                self.assertEqual([p['trace_id'] for p in receipts], ['a', 'b'])
                self.assertLess(peak, max_peak)

    def test_exact_transport_envelope_limit_and_order(self):
        for difference, expected_batches in ((-1, 1), (0, 1), (1, 2)):
            with self.subTest(difference=difference):
                payloads = [{'trace_id': identifier, 'value': '中文😀\n\t"\\',
                             'source_revision': 1790985600000000001, 'padding': ''}
                            for identifier in ('a', 'b')]
                envelope = {'projections': payloads}
                baseline = len(requests.Request('POST', 'https://fixture.invalid', json=envelope).prepare().body)
                payloads[0]['padding'] = 'x' * ((16 << 20) + difference - baseline)
                observed = []
                client = Client([], on_publish=lambda: observed.append(True))
                sizes = [len(requests.Request('POST', 'https://fixture.invalid', json=p).prepare().body)
                         for p in payloads]
                receipts = projection._publish_chunk(client, payloads, sizes)
                self.assertEqual(len(observed), expected_batches)
                self.assertEqual([item['trace_id'] for item in receipts], ['a', 'b'])
                self.assertEqual([item['source_revision'] for item in receipts], [1790985600000000001] * 2)
                self.assertEqual(client.published, ['a', 'b'])

    def test_empty_single_and_nonfinite_values(self):
        client = Client([])
        self.assertEqual(projection._publish_chunk(client, []), [])
        single = {'trace_id': 'a', 'source_revision': 7, 'value': '中文😀\n\\'}
        self.assertEqual(projection._publish_chunk(client, [single])[0]['trace_id'], 'a')
        with self.assertRaises(ValueError):
            projection._publish_chunk(client, [single, {'trace_id': 'b', 'value': float('nan')}])


@override_settings(AIVISION_TRACE_REQUEST_MAX_BYTES=64, AIVISION_TRACE_PROJECTION_MAX_BYTES=192)
class ProjectionTransportBudgetTests(SimpleTestCase):
    def test_worker_can_publish_source_larger_than_public_request_budget(self):
        from apps.core.trace_query.memory_budget import consume_bytes
        client = Client(['a'], on_source=lambda: consume_bytes(96))
        result = projection.project_batch(client, space_id='space', prices=[])
        self.assertEqual(result['published'], 1)
        self.assertEqual(result['deferred'], 0)
        with query_memory_scope():
            with self.assertRaises(TraceQueryServiceError) as raised:
                consume_bytes(96)
            self.assertEqual(raised.exception.code, 3036)

    def test_worker_still_defers_sources_over_its_own_budget(self):
        from apps.core.trace_query.memory_budget import consume_bytes
        client = Client(['a'], on_source=lambda: consume_bytes(193))
        result = projection.project_batch(client, space_id='space', prices=[])
        self.assertEqual(result['published'], 0)
        self.assertEqual(result['deferred'], 1)

    def test_nested_worker_scope_cannot_expand_http_request_budget(self):
        from apps.core.trace_query.memory_budget import consume_bytes
        with query_memory_scope():
            with query_memory_scope(max_bytes=192):
                with self.assertRaises(TraceQueryServiceError) as raised:
                    consume_bytes(96)
                self.assertEqual(raised.exception.code, 3036)
