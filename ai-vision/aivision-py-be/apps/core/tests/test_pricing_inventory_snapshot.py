"""Exercise pricing's HTTP boundary against canonical snapshot wire fixtures."""
from copy import deepcopy
import json
from unittest.mock import patch

import requests
from django.test import TestCase, override_settings

from apps.core.tests import test_model_pricing as pricing_tests
from apps.core.tests.test_native_query import trace
from apps.core.tests.test_trace_query_memory import response
from apps.core.services import model_pricing as pricing
from apps.core.services.onboarding_demo import DEMO_METADATA_KEY, demo_agent_uuid
from apps.core.tests.helpers import create_test_agent
from apps.core.models import Agent, ObservationSubjectBinding
from apps.core.trace_query.metrics_projection import ROW_FIELDS
from apps.core.trace_query.native_query import NativeTraceWindow
from apps.core.trace_query.semantics.usage import aggregate_usage
from apps.core.trace_query.signoz.provider import SignozTraceProvider
from apps.core.trace_query.signoz.read_model_projection import build_projection


@override_settings(AIVISION_QUERY_MODE='auto')
class PricingInventorySnapshotTests(TestCase):
    def setUp(self):
        pricing_tests.PricingApiTests.setUp(self)
        pricing.store_catalog(pricing_tests.catalog())
        self.lower, self.upper = 1790980000000, 1790990000000
        self.params = {'from': str(self.lower), 'to': str(self.upper)}
        self.config = {'endpoint': 'http://signoz.test', 'api_key': 'fixture-key',
                       'agent_product_filter': 'stored-product'}
        self.enterContext(patch('apps.core.services.model_pricing.resolve_signoz_runtime_config', return_value=self.config))
        self.enterContext(patch('apps.core.trace_query.signoz.provider.resolve_signoz_runtime_config', return_value=self.config))
        self.legacy = self.enterContext(patch.object(SignozTraceProvider, 'collect_traces',
            side_effect=AssertionError('pricing must not scan per-Trace details on a snapshot-capable data plane')))
        self.get = self.enterContext(patch('apps.core.trace_query.signoz.provider.requests.get'))

    def detail(self, index=1, *, model='same', usage=True):
        detail = trace(index, usage=usage)
        span = detail['spans'][0]
        span['attributes'].update({'gen_ai.request.model': model, 'gen_ai.provider.name': 'openai'})
        span['resource_attributes'] = {'aivision.space.id': str(self.space.uuid), 'aivision.agent.id': 'agent-a'}
        return detail

    def payload(self, details, *, raw=False):
        rows = []
        if not raw:
            for detail in details:
                source = {'supported': True, 'complete': True, 'contract': 'native-trace-source-v1',
                          'trace_id': detail['traceId'], 'source_revision': 1, 'maintenance_generation': 1,
                          'span_count': len(detail['spans']), 'spans': detail['spans'], 'scope_valid': True,
                          'source_user_id': 'collector', 'source_agent_product': 'stored-product'}
                projected = build_projection(source, space_id=str(self.space.uuid), prices=pricing.get_space_model_prices(self.space))
                rows.append({**{key: value for key, value in projected['row'].items() if key in ROW_FIELDS}, 'sourceRevision': 1})
        from apps.core.trace_query.signoz.read_model import projection_context
        return {'supported': True, 'complete': True,
                'contract': 'native-trace-metrics-window-v1',
                'rows': rows, 'raw': details if raw else [], 'meta': {
                    'from': self.lower, 'to': self.upper, 'space_id': str(self.space.uuid),
                    'user': '', 'agent_product': 'stored-product', 'agent_uuid': '',
                    'projection_context': projection_context(pricing.get_space_model_prices(self.space)),
                    'representation': 'metrics-v1', 'raw': False,
                    'traceCount': len(details), 'projectedTraceCount': len(rows),
                    'rawTraceCount': len(details) if raw else 0, 'omittedAgentTraceCount': 0,
                    'spanCount': sum(len(detail['spans']) for detail in details),
                    'rawSpanCount': sum(len(detail['spans']) for detail in details) if raw else 0}}

    def serve(self, payload):
        self.get.side_effect = lambda *args, **kwargs: response(json.dumps(payload).encode())
        return self.client.get(self.base + '/inventory', self.params)

    def test_large_messages_do_not_enter_inventory_reads_and_model_costs_match_raw(self):
        details = [self.detail(), self.detail(2, model='unknown'), self.detail(3, model='not-priced'), self.detail(4, usage=False)]
        for detail in details:
            detail['spans'][0]['attributes']['gen_ai.input.messages'] = 'private-message' * 100000
        payload = self.payload(details)
        # The wire also carries bounded operation previews; full messages must
        # not be hydrated just to compute pricing.
        self.assertLess(len(json.dumps(payload)), 100000)
        self.assertNotIn('private-message' * 1000, json.dumps(payload))
        result = self.serve(payload)
        self.assertEqual(result.status_code, 200, result.data)
        data = result.data['data']
        raw = NativeTraceWindow(details, project_id=str(self.space.uuid), prices=pricing.get_space_model_prices(self.space))
        observations = [item for trace in raw.traces for item in trace['observations']]
        self.assertEqual(data['costCoverage'], aggregate_usage(observations)['costCoverage'])
        self.assertEqual(data['costCoverage']['billableCalls'], 4)
        models = {row['model']: row for row in data['models']}
        self.assertEqual(models['same']['calls'], 2)
        self.assertEqual(models['same']['costCoverage']['knownSubtotal'], '0.00001')
        self.assertEqual(models['not-priced']['costCoverage']['unknownReasons'], {'price_not_configured': 1})
        for model, row in models.items():
            self.assertEqual(row['costCoverage'], aggregate_usage(
                [item for item in observations if item['model'] == model], context=observations)['costCoverage'])
        self.get.assert_called_once()
        self.assertTrue(self.get.call_args.args[0].endswith('/read-model/metrics-window'))
        self.assertEqual(self.get.call_args.kwargs['params']['space_id'], str(self.space.uuid))
        self.assertEqual(self.get.call_args.kwargs['params']['agent_product'], 'stored-product')
        self.legacy.assert_not_called()

    def test_nested_calls_keep_ancestry_and_unknown_additivity(self):
        detail = self.detail()
        parent = detail['spans'][0]
        child = deepcopy(parent)
        child.update(span_id='child', parent_span_id=parent['span_id'])
        child['attributes']['gen_ai.request.model'] = 'unknown'
        detail['spans'].append(child)
        result = self.serve(self.payload([detail]))
        self.assertEqual(result.status_code, 200, result.data)
        coverage = result.data['data']['costCoverage']
        self.assertFalse(coverage['additive'])
        raw = NativeTraceWindow([detail], project_id=str(self.space.uuid), prices=pricing.get_space_model_prices(self.space))
        self.assertEqual(coverage, aggregate_usage(raw.traces[0]['observations'])['costCoverage'])
        self.assertIsNone(coverage['knownSubtotal'])
        self.assertTrue(all(not row['costCoverage']['additive'] for row in result.data['data']['models']))

    def test_unprojected_spans_use_verified_raw_snapshot(self):
        detail = self.detail()
        # A dirty/cross-boundary Trace arrives as its selected raw spans in the
        # single complete storage snapshot; no independent detail hydration.
        result = self.serve(self.payload([detail], raw=True))
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['data']['costCoverage']['billableCalls'], 1)
        self.get.assert_called_once()
        self.legacy.assert_not_called()

    def test_hybrid_inventory_uses_verified_demo_scope_and_retains_ordinary_raw_spans(self):
        demo = create_test_agent(
            uuid=demo_agent_uuid(self.space.uuid), space=self.space, name='Example',
            agent_product='langfuse', metadata={DEMO_METADATA_KEY: {'sample_digest': 'fixture'}},
        )
        production = self.detail(1, model='production-projected')
        example = self.detail(2, model='demo-projected')
        mixed = self.detail(3, model='demo-raw')
        for detail, agent_id, amount in (
            (production, 'agent-a', '0.125'),
            (example, demo.uuid, '0.875'),
            (mixed, demo.uuid, '0.5'),
        ):
            span = detail['spans'][0]
            span['resource_attributes']['aivision.agent.id'] = agent_id
            span['attributes']['langfuse.observation.cost_details'] = {'total': amount}
        raw_demo = mixed['spans'][0]
        raw_production = deepcopy(raw_demo)
        raw_production.update(span_id='production-child', parent_span_id=raw_demo['span_id'])
        raw_production['resource_attributes']['aivision.agent.id'] = 'agent-a'
        raw_production['attributes']['gen_ai.request.model'] = 'production-raw'
        raw_production['attributes']['langfuse.observation.cost_details'] = {'total': '0.25'}
        mixed['spans'].append(raw_production)

        payload = self.payload([production, example])
        payload['raw'] = [mixed]
        payload['meta'].update(traceCount=3, spanCount=4, rawTraceCount=1, rawSpanCount=2)
        result = self.serve(payload)
        self.assertEqual(result.status_code, 200, result.data)
        data = result.data['data']
        self.assertEqual([row['model'] for row in data['models']], ['production-projected', 'production-raw'])
        self.assertEqual(data['costCoverage']['knownSubtotal'], '0.375')
        self.assertEqual(data['costCoverage']['reportedCalls'], 2)
        self.get.assert_called_once()
        self.assertTrue(self.get.call_args.args[0].endswith('/read-model/metrics-window'))
        self.legacy.assert_not_called()

    def test_missing_metrics_capability_uses_one_raw_snapshot_not_detail_scan(self):
        payload = self.payload([self.detail()], raw=True)
        payload.update(contract='native-trace-window-v1')
        payload['meta']['raw'] = True
        self.get.side_effect = [response(json.dumps({'supported': False, 'reason': 'older metrics contract'}).encode()),
                                response(json.dumps(payload).encode())]
        result = self.client.get(self.base + '/inventory', self.params)
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['data']['costCoverage']['billableCalls'], 1)
        self.assertEqual(self.get.call_count, 2)
        self.assertTrue(self.get.call_args.args[0].endswith('/read-model/window'))
        self.assertEqual(self.get.call_args.kwargs['params']['raw'], 'true')
        self.legacy.assert_not_called()

    def test_old_data_plane_without_snapshot_endpoints_keeps_bounded_compatibility(self):
        detail = self.detail()
        self.get.side_effect = lambda *args, **kwargs: response(b'not found', status=404)
        self.legacy.side_effect = None
        self.legacy.return_value = {'data': [detail], 'meta': {
            'complete': True, 'totalItems': 1, 'from': self.lower, 'to': self.upper}}
        result = self.client.get(self.base + '/inventory', self.params)
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['data']['costCoverage']['billableCalls'], 1)
        self.legacy.assert_called_once()

    def test_agent_scope_preserves_public_identity_and_configured_storage_product(self):
        binding = ObservationSubjectBinding.objects.create(space=self.space, user=self.users[0],
            source_system='collector', subject_id='collector', status=1)
        agent = Agent.objects.create(space=self.space, name='Fixture', agent_product='langfuse', status=1,
            observation_subject_binding=binding)
        detail = self.detail()
        detail['spans'][0]['resource_attributes']['aivision.agent.id'] = agent.uuid
        payload = self.payload([detail])
        payload['meta']['agent_uuid'] = agent.uuid
        self.get.side_effect = lambda *args, **kwargs: response(json.dumps(payload).encode())
        result = self.client.get(self.base + '/inventory', {**self.params, 'agent_uuid': agent.uuid})
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['data']['scope'], {'agentProduct': 'langfuse', 'agentUuid': agent.uuid})
        self.assertEqual(self.get.call_args.kwargs['params']['agent_uuid'], agent.uuid)
        self.assertEqual(self.get.call_args.kwargs['params']['agent_product'], 'stored-product')

    def test_free_prices_and_subscription_equivalents_remain_separate(self):
        self.client.post(self.base + '/rules', {
            **self.body, 'provider': 'openai', 'model': 'same',
            'effectiveFrom': '2026-01-01T00:00:00Z', 'rates': {'input': '0', 'output': '0'},
        }, format='json')
        detail = self.detail()
        result = self.serve(self.payload([detail]))
        self.assertEqual(result.status_code, 200, result.data)
        coverage = result.data['data']['costCoverage']
        self.assertEqual(coverage['knownSubtotal'], '0')
        self.assertEqual(coverage['manualCalls'], 1)

        detail['spans'][0]['attributes'].update({'aivision.billing.mode': 'subscription',
                                                'gen_ai.usage.total_cost': '3.5'})
        result = self.serve(self.payload([detail]))
        self.assertEqual(result.status_code, 200, result.data)
        coverage = result.data['data']['costCoverage']
        self.assertEqual(coverage['subscriptionCalls'], 1)
        self.assertIsNone(coverage['knownSubtotal'])
        self.assertEqual(coverage['unknownReasons'], {'subscription_billing': 1})

    def test_bad_snapshot_never_becomes_partial_or_empty_success(self):
        for field, value in [('complete', False), ('space_id', 'foreign'), ('from', self.lower - 1),
                             ('traceCount', 2), ('projection_context', '0' * 64)]:
            with self.subTest(field=field):
                payload = self.payload([self.detail()])
                (payload if field == 'complete' else payload['meta'])[field] = value
                result = self.serve(payload)
                self.assertEqual(result.status_code, 422, result.data)
                self.assertEqual(result.data['code'], 3035)
                self.assertNotIn('models', result.data.get('data') or {})
        self.legacy.assert_not_called()

    def test_compact_inventory_rejects_cost_evidence_that_disagrees_with_summary(self):
        detail = self.detail()
        detail['spans'][0]['attributes']['aivision.billing.mode'] = 'subscription'
        valid = self.payload([detail])

        def drop_subscription(fact):
            fact['usage']['cost'].pop('billingMode')

        def alter_equivalent(fact):
            fact['usage']['cost']['listPriceEquivalent']['total'] = '999'

        for mutate in (
            lambda fact: fact.update(type='SPAN'),
            drop_subscription,
            alter_equivalent,
        ):
            with self.subTest(mutate=mutate):
                payload = deepcopy(valid)
                mutate(payload['rows'][0]['_metrics']['observations'][0])
                result = self.serve(payload)
                self.assertEqual(result.status_code, 422, result.data)
                self.assertEqual(result.data['code'], 3035)
        self.legacy.assert_not_called()

    def test_compact_inventory_rejects_malformed_cost_fields(self):
        detail = self.detail()
        detail['spans'][0]['attributes']['aivision.billing.mode'] = 'subscription'
        for broken in (
            {'listPriceEquivalent': 3},
            {'listPriceEquivalent': {'total': 'not-money'}},
            {'pricingSource': ['invalid']},
            {'reason': {'private': 'not a reason'}},
            {'estimateListPrice': 'yes'},
        ):
            with self.subTest(field=next(iter(broken))):
                payload = self.payload([detail])
                payload['rows'][0]['_metrics']['observations'][0]['usage']['cost'].update(broken)
                result = self.serve(payload)
                self.assertEqual(result.status_code, 422, result.data)
                self.assertEqual(result.data['code'], 3035)
        self.legacy.assert_not_called()

    def test_http_failures_are_sanitized_without_capacity_reclassification(self):
        for status in (400, 401, 403, 500, 502):
            with self.subTest(status=status):
                self.get.side_effect = lambda *args, **kwargs: response(b'private upstream error body', status=status)
                result = self.client.get(self.base + '/inventory', self.params)
                self.assertEqual(result.status_code, 502, result.data)
                self.assertEqual(result.data['code'], 3035)
                self.assertNotIn('private', result.data['message'])
                self.assertNotIn('signoz.test', result.data['message'])
        self.legacy.assert_not_called()

    def test_capacity_and_busy_failures_remain_distinct(self):
        for status, code in ((413, 3036), (429, 3037)):
            with self.subTest(status=status):
                self.get.side_effect = lambda *args, **kwargs: response(b'private', status=status)
                result = self.client.get(self.base + '/inventory', self.params)
                self.assertEqual(result.status_code, 422, result.data)
                self.assertEqual(result.data['code'], code)
        self.legacy.assert_not_called()

    def test_transport_timeouts_and_disconnects_are_structured(self):
        for error, status in ((requests.Timeout('private endpoint'), 504),
                              (requests.ConnectionError('private credentials'), 502)):
            with self.subTest(error=type(error).__name__):
                self.get.side_effect = error
                result = self.client.get(self.base + '/inventory', self.params)
                self.assertEqual(result.status_code, status, result.data)
                self.assertEqual(result.data['code'], 3035)
                self.assertNotIn('private', result.data['message'])

    def test_invalid_success_json_does_not_escape_as_500(self):
        for body in (b'<html>private error</html>', b'\xff', b'[]'):
            with self.subTest(body=body):
                self.get.side_effect = lambda *args, **kwargs: response(body)
                result = self.client.get(self.base + '/inventory', self.params)
                self.assertEqual(result.status_code, 422, result.data)
                self.assertEqual(result.data['code'], 3035)
                self.assertNotIn('private', result.data['message'])
        self.legacy.assert_not_called()

    def test_unauthorized_space_and_foreign_agent_make_no_storage_request(self):
        self.client.force_authenticate(self.users[3])
        self.assertEqual(self.client.get(self.base + '/inventory', self.params).status_code, 404)
        self.client.force_authenticate(self.users[0])
        result = self.client.get(self.base + '/inventory', {**self.params, 'agent_uuid': 'foreign'})
        self.assertEqual(result.status_code, 404)
        self.get.assert_not_called()
