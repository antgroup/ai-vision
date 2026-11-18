"""SQL accumulators must expose the exact existing canonical metrics contract."""
from copy import deepcopy
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from apps.core.tests import test_native_read_model as read_model_tests
from apps.core.trace_query.native_query import NativeTraceWindow
from apps.core.trace_query.signoz.read_model import projection_context
from apps.core.trace_query.types import BaseTraceQuery, TraceQueryServiceError


def accumulator(*, call_count=1, ambiguous=0):
    result = {'callCount': call_count, 'ambiguousCount': ambiguous, 'costCurrencyInvalid': 0}
    for field, value in [('input', 10), ('output', 2), ('total', 12),
                         ('cacheReadInput', None), ('cacheWriteInput', None), ('reasoning', None)]:
        result[field + 'Sum'] = str(value * call_count) if value is not None and call_count else None
        result[field + 'Known'] = call_count if value is not None else 0
    for field in ('Total', 'Input', 'Output'):
        result['cost' + field + 'Sum'] = None
        result['cost' + field + 'Known'] = 0
    return result


class StatisticsAggregateTests(SimpleTestCase):
    setUp = read_model_tests.ReadModelTests.setUp

    @property
    def query_params(self):
        return {key: value for key, value in self.query.query_params.items()
                if key not in {'page', 'limit', 'fields'}}

    def aggregate_payload(self):
        canonical = NativeTraceWindow([self.detail], project_id='space')
        metrics = canonical.metrics({})
        at = int(canonical.rows[0]['start_time_unix_nano'])
        start = at // 3600000000000 * 3600000000000
        call = canonical.traces[0]['observations'][0]
        return {'supported': True, 'complete': True, 'contract': 'native-trace-metrics-aggregate-v1',
                'meta': {'space_id': 'space', 'user': '', 'agent_product': 'stored-product',
                         'agent_uuid': 'agent-a', 'from': self.query.get_param('from'), 'to': self.query.get_param('to'),
                         'projection_context': projection_context([]), 'complete': True,
                         'representation': 'statistics-v1', 'bucket': 'hour', 'group_by': 'model'},
                'summary': {'traceCount': 1, 'spanCount': 1, 'generationCount': 1, 'toolCount': 0,
                            'errorCount': metrics['errorCount'], 'latencyCount': 1,
                            'latencySum': metrics['latencyMs']['mean'], 'latencyP95': metrics['latencyMs']['p95']},
                'latencies': [metrics['latencyMs']['mean']],
                'usage': accumulator(), 'usageByType': [{'type': 'GENERATION', **accumulator()}],
                'groups': [{'value': call['model'], **accumulator()}],
                'timeseries': [{'start_ns': str(start), **accumulator()}],
                'operations': [{'start_ns': str(start), 'type': 'GENERATION', 'count': 1}]}

    def metric_query(self, **params):
        return BaseTraceQuery(self.space, None, {**self.query_params, **params})

    @override_settings(AIVISION_QUERY_MODE='auto')
    def test_v1_aggregate_without_cost_provenance_keeps_canonical_window(self):
        raw = NativeTraceWindow([self.detail], project_id='space')
        expected = raw.metrics({})
        self.client.get.return_value = self.aggregate_payload()
        with patch.object(self.provider, '_window', return_value=(raw, {'complete': True})):
            result = self.provider.query_metrics(self.metric_query())
        self.assertEqual({key: value for key, value in result.items() if key != 'meta'}, expected)
        self.assertEqual(result['costCoverage']['unknownReasons'], {'price_not_configured': 1})
        self.assertTrue(self.client.get.call_args.args[0].endswith('/metrics-aggregate'))

    @override_settings(AIVISION_QUERY_MODE='auto')
    def test_numeric_only_metrics_uses_typed_facts_without_reading_source_or_claiming_cost(self):
        self.client.get.return_value = self.aggregate_payload()
        with patch.object(self.provider, '_window', side_effect=AssertionError('must not hydrate source')):
            result = self.provider.query_metrics(self.metric_query(include_cost=False))
        self.assertEqual((result['traceCount'], result['spanCount'], result['usage']['total']), (1, 1, 12))
        self.assertEqual(result['meta']['queryMode'], 'statistics_aggregate')
        self.assertFalse(result['costsIncluded'])
        for item in [result, result['usage'], result['coverage'], *[v['usage'] for v in result['timeseries']]]:
            self.assertNotIn('cost', item)
            self.assertNotIn('costCoverage', item)
            self.assertNotIn('knownCostCalls', item)

    def test_numeric_only_window_fallback_omits_cost_and_validates_flag(self):
        raw = NativeTraceWindow([self.detail], project_id='space')
        result = raw.metrics({'include_cost': 'false'})
        self.assertEqual(result['usage']['total'], 12)
        self.assertFalse(result['costsIncluded'])
        self.assertNotIn('costCoverage', result)
        self.assertNotIn('cost', result['usage'])
        for invalid in ('0', 'no', 0, [], None):
            with self.subTest(invalid=invalid), self.assertRaises(TraceQueryServiceError):
                raw.metrics({'include_cost': invalid})

    def test_accumulator_keeps_ambiguity_reported_usage_coverage_and_decimal_cost_separate(self):
        from apps.core.trace_query.signoz.statistics_aggregate import decode_usage
        values = accumulator(ambiguous=1)
        values.update(costTotalSum='0.000000000000000123', costTotalKnown=1)
        usage, reported, coverage = decode_usage(values)
        self.assertIsNone(usage['total'])
        self.assertEqual(usage['unknownReason'], 'ambiguous_nested_generations')
        self.assertIsNone(usage['cost']['total'])
        self.assertEqual(reported['total'], {'value': 12, 'reportedCalls': 1})
        self.assertEqual(coverage, {'billableCalls': 1, 'knownUsageCalls': 1, 'knownTokens': 12, 'knownCostCalls': 1})
        values['ambiguousCount'] = 0
        usage, _, _ = decode_usage(values)
        self.assertEqual(usage['cost']['total'], '0.000000000000000123')
        self.assertIsNone(usage['cost']['input'])
        self.assertIsNone(usage['cost']['reason'])

    def test_empty_accumulator_does_not_invent_zero_token_or_cost_totals(self):
        from apps.core.trace_query.signoz.statistics_aggregate import decode_usage
        usage, reported, coverage = decode_usage(accumulator(call_count=0))
        self.assertEqual(usage['observationCount'], 0)
        self.assertIsNone(usage['total'])
        self.assertEqual(usage['unknownReason'], 'usage_missing')
        self.assertIsNone(reported['total']['value'])
        self.assertEqual(coverage['knownTokens'], 0)

    def test_partial_fields_and_non_usd_cost_preserve_reported_coverage(self):
        from apps.core.trace_query.signoz.statistics_aggregate import decode_usage
        values = accumulator(call_count=2)
        values.update(inputSum='10', inputKnown=1, costTotalSum='0.3', costTotalKnown=2,
                      costInputSum='0.1', costInputKnown=2, costOutputSum='0.2', costOutputKnown=2,
                      costCurrencyInvalid=1)
        usage, reported, coverage = decode_usage(values)
        self.assertIsNone(usage['input'])
        self.assertEqual(usage['total'], 24)
        self.assertIsNone(usage['cost']['total'])
        self.assertEqual(usage['cost']['reason'], 'cost_missing')
        self.assertEqual(reported['input'], {'value': 10, 'reportedCalls': 1})
        self.assertEqual(coverage['knownCostCalls'], 2)

    @override_settings(AIVISION_QUERY_MODE='auto')
    def test_large_integer_accumulators_do_not_pass_through_decimal_context(self):
        payload = self.aggregate_payload()
        value = '1234567890123456789012345678901234567890'
        for row in [payload['usage'], *payload['usageByType'], *payload['groups'], *payload['timeseries']]:
            row['totalSum'] = value
        self.client.get.return_value = payload
        from apps.core.trace_query.signoz.statistics_aggregate import decode_aggregate
        result = decode_aggregate(payload, payload['meta'])['metrics']
        self.assertEqual(result['usage']['total'], int(value))
        self.assertEqual(result['reportedUsage']['total']['value'], int(value))

    @override_settings(AIVISION_QUERY_MODE='auto')
    def test_latency_vector_preserves_python_sorted_sum_and_exact_nearest_rank(self):
        payload = self.aggregate_payload()
        payload['summary'].update(traceCount=3, spanCount=3, latencyCount=3,
                                  latencySum=1e16, latencyP95=10000000000000000)
        payload['latencies'] = [1.0, 1.0, 1e16]
        self.client.get.return_value = payload
        from apps.core.trace_query.signoz.statistics_aggregate import decode_aggregate
        result = decode_aggregate(payload, payload['meta'])['metrics']
        self.assertEqual(result['latencyMs'], {'mean': sum([1.0, 1.0, 1e16]) / 3, 'p95': 1e16})
        self.assertIsInstance(result['latencyMs']['p95'], float)
        for vector in ([1.0, 1e16], [1.0, float('nan'), 1e16], [1e16, 1.0, 1.0]):
            payload['latencies'] = vector
            with self.assertRaises(TraceQueryServiceError):
                self.provider.query_metrics(self.metric_query())

    @override_settings(AIVISION_QUERY_MODE='auto')
    def test_content_and_sdk_identity_filters_decline_without_probing_aggregate(self):
        from apps.core.trace_query.signoz.statistics_aggregate import query_metrics_aggregate
        for params in ({'username': 'sdk-user'}, {'searchType': 'input', 'keyword': 'needle'},
                       {'filters': [{'field': 'total_tokens', 'op': '>=', 'value': 0}]}):
            self.assertIsNone(query_metrics_aggregate(self.provider, self.metric_query(**params)))
        self.client.get.assert_not_called()

    @override_settings(AIVISION_QUERY_MODE='auto')
    def test_dynamic_attribute_filters_reach_the_existing_complete_window_schema(self):
        self.detail['spans'][0]['attributes']['test.query'] = 'needle'
        raw = NativeTraceWindow([self.detail], project_id='space')
        for filters in ([{'field': 'attributes.test.query', 'value': 'needle'}],
                        {'attributes.test.query': 'needle'}):
            with self.subTest(filters=filters):
                with patch.object(self.provider, '_window', return_value=(raw, {'complete': True})) as window:
                    result = self.provider.query_metrics(self.metric_query(filters=filters))
                self.assertEqual(result['traceCount'], 1)
                window.assert_called_once()
        self.client.get.assert_not_called()

    @override_settings(AIVISION_QUERY_MODE='auto')
    def test_non_string_agent_selector_keeps_canonical_filter_instead_of_broadening_scope(self):
        raw = NativeTraceWindow([self.detail], project_id='space')
        for identifier in (123, True, ['agent-a'], {'id': 'agent-a'}):
            with self.subTest(identifier=identifier):
                payload = self.aggregate_payload()
                payload['meta']['agent_uuid'] = ''
                self.client.get.return_value = payload
                self.client.get.reset_mock()
                with patch.object(self.provider, '_window', return_value=(raw, {'complete': True})) as window:
                    result = self.provider.query_metrics(self.metric_query(agent_uuid=identifier))
                self.assertEqual(result['traceCount'], 0)
                window.assert_called_once()
                self.client.get.assert_not_called()

    @override_settings(AIVISION_QUERY_MODE='auto')
    def test_null_and_empty_dimension_sort_tie_preserves_canonical_fallback_order(self):
        root = self.detail['spans'][0]
        root['attributes']['gen_ai.request.model'] = ''
        child = deepcopy(root)
        child.update(span_id='child-generation', parent_span_id=root['span_id'],
                     start_time_unix_nano=str(int(root['start_time_unix_nano']) + 1))
        child['attributes'].pop('gen_ai.request.model')
        self.detail['spans'].append(child)
        raw = NativeTraceWindow([self.detail], project_id='space')
        expected = raw.metrics({})
        self.assertEqual([row['value'] for row in expected['groups']], ['', None])
        payload = self.aggregate_payload()
        payload['summary'].update(spanCount=2, generationCount=2)
        payload['usage'] = accumulator(call_count=2, ambiguous=2)
        payload['usageByType'] = [{'type': 'GENERATION', **accumulator(call_count=2, ambiguous=2)}]
        payload['groups'] = [{'value': value, **accumulator(ambiguous=1)} for value in (None, '')]
        payload['timeseries'][0].update(accumulator(call_count=2, ambiguous=2))
        payload['operations'][0]['count'] = 2
        self.client.get.return_value = payload
        with patch.object(self.provider, '_window', return_value=(raw, {'complete': True})) as window:
            result = self.provider.query_metrics(self.metric_query())
        self.assertEqual({key: value for key, value in result.items() if key != 'meta'}, expected)
        window.assert_called_once()

    @override_settings(AIVISION_QUERY_MODE='auto')
    def test_explicit_physical_subject_is_bound_and_rechecked_independently(self):
        from apps.core.trace_query.signoz.statistics_aggregate import query_metrics_aggregate
        payload = self.aggregate_payload()
        payload['meta']['user'] = 'collector-subject'
        self.client.get.return_value = payload
        query = self.metric_query(username='collector-subject')
        result = query_metrics_aggregate(self.provider, query, physical_user='collector-subject', operations_only=True)
        self.assertEqual(result['metrics']['traceCount'], 1)
        self.assertEqual(self.client.get.call_args.kwargs['params']['user'], 'collector-subject')
        self.assertNotIn('username', self.client.get.call_args.kwargs['params'])
        payload['meta']['user'] = 'another-subject'
        with self.assertRaises(TraceQueryServiceError):
            query_metrics_aggregate(self.provider, query, physical_user='collector-subject', operations_only=True)

    @override_settings(AIVISION_QUERY_MODE='auto')
    def test_explicit_unsupported_and_old_endpoint_preserve_existing_fallback(self):
        for response in ({'supported': False, 'reason': 'statistics_incomplete'},
                         TraceQueryServiceError('not found', 3019)):
            self.client.get.side_effect = response if isinstance(response, Exception) else None
            self.client.get.return_value = response
            raw = NativeTraceWindow([self.detail], project_id='space')
            with patch.object(self.provider, '_window', return_value=(raw, {'complete': True})) as window:
                result = self.provider.query_metrics(self.metric_query())
            window.assert_called_once()
            self.assertEqual(result['traceCount'], 1)

    @override_settings(AIVISION_QUERY_MODE='auto')
    def test_malformed_success_scope_counts_and_transport_fail_closed(self):
        mutations = [lambda p: p['meta'].update(user='foreign'),
                     lambda p: p['meta'].update(agent_uuid='foreign'),
                     lambda p: p['meta'].update(projection_context='stale'),
                     lambda p: p['summary'].update(errorCount=2),
                     lambda p: p['usage'].update(totalKnown=2),
                     lambda p: p['usage'].update(totalSum='12.2'),
                     lambda p: p['groups'].append(deepcopy(p['groups'][0])),
                     lambda p: p['timeseries'][0].update(start_ns='1'),
                     lambda p: p['operations'][0].update(count=2)]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                payload = self.aggregate_payload()
                mutation(payload)
                self.client.get.return_value = payload
                with patch.object(self.provider, '_window') as window, self.assertRaises(TraceQueryServiceError):
                    self.provider.query_metrics(self.metric_query())
                window.assert_not_called()
        self.client.get.side_effect = TraceQueryServiceError('not authorized', 3017)
        with patch.object(self.provider, '_window') as window, self.assertRaises(TraceQueryServiceError):
            self.provider.query_metrics(self.metric_query())
        window.assert_not_called()

    @override_settings(AIVISION_QUERY_MODE='raw')
    def test_raw_control_skips_aggregate_capability(self):
        raw = NativeTraceWindow([self.detail], project_id='space')
        with patch.object(self.provider, '_window', return_value=(raw, {'complete': True})):
            self.provider.query_metrics(self.metric_query())
        self.client.get.assert_not_called()

    @override_settings(AIVISION_QUERY_MODE='auto')
    def test_framework_trend_rolls_typed_minute_operations_without_reading_window(self):
        from types import SimpleNamespace
        from apps.core.query.framework import query_framework
        payload = self.aggregate_payload()
        payload['meta']['bucket'] = 'minute'
        at = int(NativeTraceWindow([self.detail], project_id='space').rows[0]['start_time_unix_nano'])
        start = at // 60000000000 * 60000000000
        payload['timeseries'][0]['start_ns'] = str(start)
        payload['operations'][0]['start_ns'] = str(start)
        self.client.get.return_value = payload
        prepared = {'query_key': 'framework.events.timeseries', 'scope': 'space',
                    'from_ms': self.query.get_param('from'), 'to_ms': self.query.get_param('to'),
                    'params': {'agent_uuid': 'agent-a', 'time_step': 'PT5M'}}
        context = {'space': self.space, 'user': None, 'agent': SimpleNamespace(uuid='agent-a')}
        with patch('apps.core.query.framework.SignozTraceProvider', return_value=self.provider), \
                patch('apps.core.query.framework._complete_window') as window:
            result = query_framework(context, prepared, 'public-product')
        window.assert_not_called()
        from apps.core.trace_query.semantics.observations import iso_timestamp
        self.assertEqual(result['rows'], [{'time': iso_timestamp(start // 300000000000 * 300000000000),
                                          'kind': 'llm', 'count': 1}])
