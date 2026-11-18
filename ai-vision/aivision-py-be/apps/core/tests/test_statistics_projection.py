"""Typed statistics keep the canonical complete-Trace accounting contract."""
from copy import deepcopy
import hashlib
import json

from django.test import SimpleTestCase

from apps.core.tests import test_native_read_model as read_model_tests
from apps.core.trace_query.native_query import NativeTraceWindow
from apps.core.trace_query.signoz.read_model_projection import build_projection


class StatisticsProjectionTests(SimpleTestCase):
    setUp = read_model_tests.ReadModelTests.setUp
    snapshot = read_model_tests.ReadModelTests.snapshot

    def test_typed_statistics_marks_only_nested_billable_calls_and_keeps_operations(self):
        from apps.core.trace_query.statistics_projection import project_statistics
        root = self.detail['spans'][0]
        self.detail['spans'].extend([
            {**deepcopy(root), 'span_id': 'wrapper', 'parent_span_id': root['span_id'], 'attributes': {}},
            {**deepcopy(root), 'span_id': 'nested', 'parent_span_id': 'wrapper',
             'attributes': {**root['attributes'], 'gen_ai.request.model': 'other'}},
            {**deepcopy(root), 'span_id': 'independent', 'parent_span_id': None},
            {**deepcopy(root), 'span_id': 'tool', 'parent_span_id': root['span_id'],
             'attributes': {'langfuse.observation.type': 'TOOL'}},
        ])
        canonical = NativeTraceWindow([self.detail], project_id='space').traces[0]
        result = project_statistics(canonical)
        calls = {call['id']: call for call in result['calls']}
        self.assertEqual(set(calls), {root['span_id'], 'nested', 'independent', 'tool'})
        self.assertTrue(calls[root['span_id']]['ambiguous'])
        self.assertTrue(calls['nested']['ambiguous'])
        self.assertFalse(calls['independent']['ambiguous'])
        self.assertFalse(calls['tool']['ambiguous'])
        self.assertEqual(result['trace']['span_count'], 5)
        self.assertEqual(result['trace']['generation_count'], 3)
        self.assertEqual(result['trace']['tool_count'], 1)
        self.assertEqual(calls['nested']['model'], 'other')
        self.assertIsInstance(calls['nested']['start_ns'], str)
        self.assertNotIn('attributes', calls['nested'])

    def test_projection_receipt_covers_all_facts_and_preserves_base_context(self):
        result = build_projection(self.snapshot(), space_id='space', prices=[])
        facts = result['row']['_statistics']
        self.assertEqual(facts['version'], 'native-trace-statistics-v1')
        unsigned = {key: value for key, value in facts.items() if key != 'receipt'}
        expected = hashlib.sha256(json.dumps(unsigned, sort_keys=True, ensure_ascii=False,
                                            separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        self.assertEqual(facts['receipt'], expected)
        self.assertNotIn('metrics_facts', result)
        self.assertEqual(facts['trace']['latency_ms'], result['duration'])
        self.assertEqual(facts['trace']['error_count'], int(result['status'] == 2))

    def test_typed_facts_do_not_invent_unknown_usage_or_round_decimal_cost(self):
        from apps.core.trace_query.statistics_projection import project_statistics
        attrs = self.detail['spans'][0]['attributes']
        attrs.pop('gen_ai.usage.input_tokens', None)
        attrs.pop('gen_ai.usage.output_tokens', None)
        attrs['gen_ai.usage.total_tokens'] = 0
        attrs['gen_ai.usage.total_cost'] = '0.0000000000000000000123456789'
        canonical = NativeTraceWindow([self.detail], project_id='space').traces[0]
        call = project_statistics(canonical)['calls'][0]
        self.assertEqual(call['total'], 0)
        self.assertIsNone(call['input'])
        self.assertEqual(call['cost']['total'], canonical['observations'][0]['usage']['cost']['total'])

    def test_existing_compact_facts_produce_the_same_typed_receipt_as_complete_raw_trace(self):
        from apps.core.trace_query.statistics_projection import project_statistics
        for status in ('STATUS_CODE_ERROR', 'OK', 0):
            self.detail['spans'][0]['status'] = {'code': status}
            source = self.snapshot()
            source['spans'] = deepcopy(self.detail['spans'])
            payload = build_projection(source, space_id='space', prices=[])
            restored = NativeTraceWindow.from_summary_rows([payload['row']], include_metrics=True)
            self.assertEqual(project_statistics(restored.traces[0]), payload['row']['_statistics'])

    def test_full_base_document_keeps_bounded_declined_receipt_instead_of_reprojection_loop(self):
        source = self.snapshot()
        # Many bounded fields exercise total-document pressure independently
        # of the new oversized-single-attribute compaction.
        padding_keys = [f'test.padding.{index}' for index in range(64)]
        source['spans'][0]['attributes'].update({key: '' for key in padding_keys})
        initial = build_projection(source, space_id='space', prices=[])
        full_facts = initial['row'].pop('_statistics')
        ordinary_limit = 16 * 1024 * 1024 - 4096
        padding = ordinary_limit - len(json.dumps(initial, allow_nan=False).encode())
        width, remainder = divmod(padding, len(padding_keys))
        source['spans'][0]['attributes'].update({key: 'x' * (width + int(index < remainder))
                                                for index, key in enumerate(padding_keys)})
        result = build_projection(source, space_id='space', prices=[])
        facts = result['row'].get('_statistics')
        self.assertIsInstance(facts, dict, 'absence would enroll the same unchanged Trace every poll')
        self.assertEqual(facts['declined'], 'document_outside_typed_bounds')
        self.assertEqual(facts['calls'], [])
        self.assertEqual(facts['trace'], full_facts['trace'])
        self.assertNotEqual(facts['receipt'], full_facts['receipt'])
        unsigned = {key: value for key, value in facts.items() if key != 'receipt'}
        self.assertEqual(facts['receipt'], hashlib.sha256(json.dumps(
            unsigned, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False,
        ).encode()).hexdigest())
        row = dict(result['row'])
        row.pop('_statistics')
        self.assertEqual(len(json.dumps({**result, 'row': row}, allow_nan=False).encode()), ordinary_limit)
        self.assertLess(len(json.dumps({'projections': [result]}, allow_nan=False).encode()), 16 * 1024 * 1024)
