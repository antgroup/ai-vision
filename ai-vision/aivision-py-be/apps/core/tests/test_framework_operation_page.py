"""Recent operations read bounded keys, then hydrate revision-bound contents."""
from copy import deepcopy
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from apps.core.query.framework import query_framework
from apps.core.tests import test_framework_snapshot as snapshot_tests
from apps.core.trace_query.native_query import NativeTraceWindow
from apps.core.trace_query.semantics.observations import timestamp_ns
from apps.core.trace_query.types import TraceQueryServiceError


@override_settings(AIVISION_QUERY_MODE='auto')
class FrameworkOperationPageTests(SimpleTestCase):
    typed_operation_page = True
    def setUp(self):
        snapshot_tests.FrameworkSnapshotTests.setUp(self)
        self.page_mode = None
        self.page_mutation = None
        self.page_facts = False
        self.enterContext(patch('apps.core.trace_query.signoz.performance_capabilities.performance_capabilities',
                                side_effect=lambda _client: {'operation_facts': self.page_facts}))

    def get(self, path, *, params):
        if not path.endswith('/operation-page'):
            return snapshot_tests.FrameworkSnapshotTests.get(self, path, params=params)
        self.calls.append((path, deepcopy(params)))
        if self.page_mode == 'unsupported':
            return {'supported': False, 'reason': 'statistics_incomplete'}
        if self.page_mode == 'missing':
            raise TraceQueryServiceError('old data plane', 3019)
        if self.page_mode == 'transport':
            raise TraceQueryServiceError('transport unavailable', 3012)
        if self.page_mode == 'auth':
            raise TraceQueryServiceError('not authorized', 3017)
        window = NativeTraceWindow(self.details, project_id='space')
        data = [{'trace_id': trace['id'], 'observation_id': item['id'], 'type': item['type'],
                 'start_ns': str(timestamp_ns(item['startTime'])), 'source_revision': 1,
                 'span_count': len(trace['observations'])}
                for trace in window.traces for item in trace['observations']
                if item['type'] in {'GENERATION', 'EMBEDDING', 'TOOL'}]
        data.sort(key=lambda row: (int(row['start_ns']), row['trace_id'], row['observation_id']), reverse=True)
        payload = {'supported': True, 'complete': True, 'contract': 'native-trace-operation-page-v1',
                   'meta': {**params, 'complete': True, 'representation': 'operations-v1', 'maintenance_generation': 1},
                   'has_more': len(data) > params['limit'], 'data': data[:params['limit']]}
        if self.page_facts:
            for row in payload['data']:
                row['facts'] = dict(model='test-model', provider=None, input='10', output='2', total='12',
                                    cacheReadInput=None, cacheWriteInput=None, reasoning=None, ambiguous=False)
                row['preview'] = dict(name='selected', latency=0.25, status=1, input='{"content":"input"}',
                                      output='{"content":"output"}', input_truncated=True, output_truncated=False)
        if self.page_mutation is not None:
            self.page_mutation(payload)
        return payload

    def stream(self, **prepared):
        return query_framework(self.context, {**self.prepared, **prepared}, 'langfuse')

    def test_latest_keys_hydrate_only_selected_trace_and_skip_complete_window(self):
        with patch('apps.core.query.framework._complete_window') as full:
            result = self.stream()
        full.assert_not_called()
        self.assertEqual([row['span_id'] for row in result['rows']], ['span-2'])
        self.assertEqual(result['rows'][0]['input'], 'input')
        self.assertEqual([path.rsplit('/', 1)[-1] for path, _ in self.calls], ['operation-page', 'batch-snapshot'])
        self.assertEqual(self.calls[1][1]['trace_ids'], '["trace-2"]')

    def test_revision_bound_operation_facts_skip_every_source_hydration(self):
        self.page_facts = True
        with patch('apps.core.query.framework._complete_window', side_effect=AssertionError('no window')):
            result = self.stream()
        self.assertEqual([path.rsplit('/', 1)[-1] for path, _ in self.calls], ['operation-page'])
        row = result['rows'][0]
        self.assertEqual((row['span_id'], row['total_tokens'], row['cost_ms']), ('span-2', 12, 250))
        self.assertEqual(row['input'], '{"content":"input"}')
        self.assertTrue(row['input_truncated'])
        self.assertEqual(row['content_mode'], 'preview')

    def test_malformed_operation_fact_cannot_hide_behind_hydration(self):
        self.page_facts = True
        for mutation in (lambda p: p['data'][0]['facts'].update(total=-1),
                         lambda p: p['data'][0]['preview'].update(input_truncated='true'),
                         lambda p: p['data'][0]['preview'].update(input='x'*1025)):
            self.page_mutation = mutation
            with self.subTest(mutation=mutation), self.assertRaises(TraceQueryServiceError):
                self.stream()

    def test_old_storage_keeps_operation_keys_without_unsupported_facts_parameter(self):
        result = self.stream()
        self.assertEqual(result['rows'][0]['input'], 'input')
        self.assertNotIn('include_facts', self.calls[0][1])
        self.assertEqual([path.rsplit('/', 1)[-1] for path, _ in self.calls], ['operation-page', 'batch-snapshot'])

    def test_empty_page_never_reads_contents_or_complete_window(self):
        self.details = []
        with patch('apps.core.query.framework._complete_window') as full:
            self.assertEqual(self.stream(), {'columns': [], 'rows': []})
        full.assert_not_called()
        self.assertEqual(len(self.calls), 1)

    def test_tied_child_keys_keep_canonical_parent_and_share_one_trace_hydration(self):
        self.details = [self.details[1]]
        child = deepcopy(self.details[0]['spans'][0])
        child.update(span_id='z-child', parent_span_id='span-1')
        child['attributes']['gen_ai.input.messages'] = 'child-input'
        self.details[0]['spans'].append(child)
        result = self.stream(params={'agent_uuid': 'agent', 'limit': 2})
        self.assertEqual([row['span_id'] for row in result['rows']], ['z-child', 'span-1'])
        self.assertEqual([row['input'] for row in result['rows']], ['child-input', 'input'])
        self.assertEqual(self.calls[1][1]['trace_ids'], '["trace-1"]')

    def test_old_or_unsupported_page_keeps_current_complete_window_path(self):
        for mode in ('unsupported', 'missing'):
            with self.subTest(mode=mode):
                self.calls.clear()
                self.page_mode = mode
                result = self.stream()
                self.assertEqual([row['span_id'] for row in result['rows']], ['span-2'])
                self.assertEqual([path.rsplit('/', 1)[-1] for path, _ in self.calls], ['operation-page', 'metrics-window'])

    def test_changed_revision_abandons_page_and_reads_one_complete_raw_snapshot(self):
        self.batch_mode = 'changed'
        result = self.stream()
        self.assertEqual(result['rows'][0]['input'], 'updated-input')
        self.assertEqual([path.rsplit('/', 1)[-1] for path, _ in self.calls], ['operation-page', 'batch-snapshot', 'window'])
        self.assertEqual(self.calls[-1][1]['raw'], 'true')

    def test_generation_aba_abandons_page_even_when_revision_and_span_count_match(self):
        self.batch_mode = 'generation_aba'
        result = self.stream()
        self.assertEqual([row['span_id'] for row in result['rows']], ['span-0'])
        self.assertEqual(result['rows'][0]['input'], 'after-reset')
        self.assertEqual([path.rsplit('/', 1)[-1] for path, _ in self.calls], ['operation-page', 'batch-snapshot', 'window'])
        self.assertEqual(self.calls[-1][1]['raw'], 'true')

    def test_missing_or_invalid_page_generation_is_not_a_success(self):
        for generation in (None, True, 0, -1, '1'):
            self.page_mutation = lambda p: p['meta'].update(maintenance_generation=generation)
            with self.subTest(generation=generation), self.assertRaises(TraceQueryServiceError):
                self.stream()

    def test_mixed_generation_across_content_batches_discards_every_hydrated_event(self):
        original_get = self.get
        count = 0
        def read(path, *, params):
            nonlocal count
            if path.endswith('/batch-snapshot'):
                count += 1
                if count == 2:
                    self.batch_mode = 'mixed_generation'
            return original_get(path, params=params)
        self.get = read
        template = self.details[0]['spans'][0]
        self.details = [{'traceId': f'trace-{index:03d}', 'spans': [{**deepcopy(template),
                        'trace_id': f'trace-{index:03d}', 'span_id': f'span-{index:03d}'}]}
                        for index in range(150)]
        result = self.stream(params={'agent_uuid': 'agent', 'limit': 150})
        self.assertEqual(result['rows'][0]['span_id'], 'span-000')
        self.assertEqual({row['input'] for row in result['rows']}, {'after-reset'})
        self.assertEqual(self.calls[-1][1]['raw'], 'true')

    def test_missing_or_invalid_source_generation_is_malformed_not_a_reset(self):
        original_get = self.get
        generation = None
        def read(path, *, params):
            response = original_get(path, params=params)
            if path.endswith('/batch-snapshot'):
                response['data'][0]['maintenance_generation'] = generation
            return response
        self.get = read
        for generation in (None, True, 0, -1, '1'):
            with self.subTest(generation=generation), \
                    patch('apps.core.query.framework._complete_window') as full, self.assertRaises(TraceQueryServiceError):
                self.stream()
            full.assert_not_called()

    def test_malformed_page_identity_bounds_duplicates_and_order_fail_closed(self):
        mutations = [lambda p: p['meta'].update(user='foreign'),
                     lambda p: p['meta'].update(limit=3),
                     lambda p: p['meta'].update(projection_context='wrong'),
                     lambda p: p['meta'].update(user=[]),
                     lambda p: p['data'][0].update(source_revision=True),
                     lambda p: p['data'][0].update(type='SPAN'),
                     lambda p: p['data'][0].update(type=[]),
                     lambda p: p['data'][0].update(start_ns='5000000000'),
                     lambda p: p['data'][0].update(span_count=0),
                     lambda p: p.update(has_more='true'),
                     lambda p: p.update(data=[p['data'][0], deepcopy(p['data'][0])]),
                     lambda p: p['data'].reverse()]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                self.calls.clear()
                self.page_mutation = mutate
                with patch('apps.core.query.framework._complete_window') as full, self.assertRaises(TraceQueryServiceError):
                    self.stream(params={'agent_uuid': 'agent', 'limit': 2})
                full.assert_not_called()
                self.assertEqual(len(self.calls), 1)

    def test_hydration_type_time_missing_span_scope_and_transport_do_not_hide_in_fallback(self):
        for mode in ('product', 'foreign', 'count_drift', 'auth', 'error'):
            with self.subTest(mode=mode):
                self.calls.clear()
                self.batch_mode = mode
                with patch('apps.core.query.framework._complete_window') as full, self.assertRaises(TraceQueryServiceError):
                    self.stream()
                full.assert_not_called()
        self.batch_mode = None
        for mutation in (lambda p: p['data'][0].update(observation_id='missing-span'),
                         lambda p: p['data'][0].update(type='TOOL'),
                         lambda p: p['data'][0].update(start_ns='1002500000')):
            self.page_mutation = mutation
            with patch('apps.core.query.framework._complete_window') as full, self.assertRaises(TraceQueryServiceError):
                self.stream()
            full.assert_not_called()

    @override_settings(AIVISION_QUERY_MODE='raw')
    def test_raw_control_bypasses_page_and_uses_existing_snapshot(self):
        result = self.stream()
        self.assertEqual([row['span_id'] for row in result['rows']], ['span-2'])
        self.assertEqual([path.rsplit('/', 1)[-1] for path, _ in self.calls], ['window'])

    def test_page_auth_and_transport_errors_do_not_retry_through_another_reader(self):
        for mode in ('transport', 'auth'):
            self.calls.clear()
            self.page_mode = mode
            with patch('apps.core.query.framework._complete_window') as full, self.assertRaises(TraceQueryServiceError):
                self.stream()
            full.assert_not_called()
            self.assertEqual(len(self.calls), 1)

    def test_personal_subject_binds_page_and_hydration_without_sdk_username_predicate(self):
        from types import SimpleNamespace
        selected = SimpleNamespace(space_id=1, observation_subject_binding=SimpleNamespace(
            space_id=1, status=1, gmt_deleted=None, subject_id='selected-subject',
            user=SimpleNamespace(status=1, gmt_deleted=None)))
        self.source_user = 'selected-subject'
        with patch('apps.core.query.framework.resolve_space_agent_for_query', return_value=selected):
            result = self.stream(scope='personal')
            self.assertEqual(result['rows'][0]['input'], 'input')
            self.assertEqual(self.calls[0][1]['user'], 'selected-subject')
            self.assertNotIn('username', self.calls[0][1])
            self.batch_mode = 'subject'
            with self.assertRaisesRegex(TraceQueryServiceError, 'authorized scope'):
                self.stream(scope='personal')

    def test_contents_remain_batched_when_page_contains_150_distinct_traces(self):
        template = self.details[0]['spans'][0]
        self.details = [{'traceId': f'trace-{index:03d}', 'spans': [{**deepcopy(template),
                        'trace_id': f'trace-{index:03d}', 'span_id': f'span-{index:03d}'}]}
                        for index in range(150)]
        result = self.stream(params={'agent_uuid': 'agent', 'limit': 150})
        self.assertEqual(len(result['rows']), 150)
        self.assertEqual([row['span_id'] for row in result['rows']][:2], ['span-149', 'span-148'])
        import json
        self.assertEqual([len(json.loads(params['trace_ids'])) for path, params in self.calls
                          if path.endswith('/batch-snapshot')], [100, 50])
