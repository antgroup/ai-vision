"""Pricing tests use public catalog fixtures, never live vendor/invoice data."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from io import StringIO
from unittest.mock import patch
from uuid import NAMESPACE_URL, uuid5
from django.core.management import call_command, CommandError
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from apps.core.models import Space, SpaceMember, User
from apps.core.services import model_pricing as pricing
from apps.core.tests.helpers import create_test_agent
from apps.core.trace_query.native_query import NativeTraceWindow


def catalog(rate=1):
    return {'openai': {'id': 'openai', 'models': {
        'same': {'id': 'same', 'cost': {'input': rate, 'output': 0}},
        'unknown': {'id': 'unknown', 'cost': {'input': 0}},
        'tiered': {'id': 'tiered', 'cost': {'input': 1, 'output': 2, 'tiers': [{}]}},
    }}, 'other-host': {'id': 'other-host', 'models': {'same': {'id': 'same', 'cost': {'input': 9, 'output': 9}}}}}


class PricingCatalogTests(TestCase):
    def test_missing_price_is_null_free_price_zero_and_conditions_unsupported(self):
        rows = {(r['provider'], r['model']): r for r in pricing.normalize_catalog(catalog())}
        self.assertEqual(rows['openai', 'same']['rates']['output'], '0')
        self.assertIsNone(rows['openai', 'unknown']['rates']['output'])
        self.assertFalse(rows['openai', 'tiered']['supported'])
        self.assertIn('tiers', rows['openai', 'tiered']['unsupportedReasons'][0])

    def test_rich_cost_conditions_are_never_flattened(self):
        source = catalog()
        source['openai']['models']['rich'] = {'id': 'rich', 'schema': 2, 'cost': {'text': {'input': {'price': 3, 'cache': {'read': 0.3, 'write': {'5m': 3.75, '1h': 6}}}, 'output': {'price': 15}}, 'multipliers': [{'factor': {'text': 1.1}}]}}
        rich = next(r for r in pricing.normalize_catalog(source) if r['model'] == 'rich')
        self.assertFalse(rich['supported'])
        self.assertTrue(rich['unsupportedReasons'])

    def test_invalid_rates_reject_whole_catalog(self):
        for value in (-1, True, 'NaN', 'Infinity', {}, []):
            with self.subTest(value=value), self.assertRaises(pricing.PricingError):
                pricing.normalize_catalog(catalog(value))

    def test_offline_import_preserves_decimal_precision(self):
        material = json.dumps(catalog()).replace('"input": 1', '"input": 0.123456789012345678')
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'catalog.json'
            path.write_text(material)
            call_command('sync_model_pricing', file=str(path), stdout=StringIO())
        self.assertEqual(pricing.search_catalog(provider='openai', model='same')[0]['rates']['input'], '0.123456789012345678')

    def test_network_and_offline_import_reject_lossy_or_nonfinite_rates(self):
        first = pricing.store_catalog(catalog())
        for token in ('0.1234567890123456789012345', 'NaN', 'Infinity'):
            material = json.dumps(catalog()).replace('"input": 1', '"input": ' + token)
            with self.subTest(token=token), TemporaryDirectory() as directory:
                path = Path(directory) / 'catalog.json'
                path.write_text(material)
                with self.assertRaises(CommandError):
                    call_command('sync_model_pricing', file=str(path), stdout=StringIO())
                self.assertEqual(pricing.catalog_status()['snapshotId'], first.snapshot_id)
            with patch.object(pricing.http_requests, 'get') as get:
                get.return_value.status_code = 200
                get.return_value.headers = {}
                get.return_value.iter_content.return_value = [material.encode()]
                with self.assertRaises(pricing.PricingError):
                    pricing.sync_catalog(source='api')
                self.assertEqual(pricing.catalog_status()['snapshotId'], first.snapshot_id)

    def test_candidate_billing_provider_strict_reference_explicit(self):
        pricing.store_catalog(catalog())
        exact = pricing.search_catalog(provider='openai', model='same')
        self.assertEqual(exact[0]['provider'], 'openai')
        self.assertTrue(all(row['provider'] == 'openai' for row in exact))
        self.assertEqual(pricing.search_catalog(provider='private', model='same'), [])
        reference = pricing.search_catalog(provider='private', model='same', reference=True)
        self.assertTrue(reference)
        self.assertTrue(all('reference_only' in row['reasons'] for row in reference))

    def test_failed_sync_retains_snapshot_and_sanitizes_response(self):
        first = pricing.store_catalog(catalog())
        with patch.object(pricing.http_requests, 'get') as get:
            get.return_value.status_code = 200
            get.return_value.headers = {}
            get.return_value.iter_content.return_value = [b'{bad secret upstream body']
            with self.assertRaises(pricing.PricingError) as raised:
                pricing.sync_catalog(source='api')
            self.assertNotIn('secret', str(raised.exception))
            self.assertEqual(pricing.catalog_status()['snapshotId'], first.snapshot_id)
            self.assertEqual(pricing.search_catalog(provider='openai', model='same')[0]['rates']['input'], '1')
            self.assertEqual(get.call_args.args[0], 'https://models.dev/api.json')
            self.assertFalse(get.call_args.kwargs['allow_redirects'])
            self.assertEqual(get.call_args.kwargs['timeout'], (5, 20))

    def test_redirects_and_oversized_responses_cannot_replace_snapshot(self):
        first = pricing.store_catalog(catalog())
        for status, headers, chunks in ((302, {}, []), (200, {'Content-Length': str(pricing.MAX_CATALOG_BYTES + 1)}, []), (200, {}, [b'x' * (pricing.MAX_CATALOG_BYTES + 1)])):
            with self.subTest(status=status, headers=headers), patch.object(pricing.http_requests, 'get') as get:
                get.return_value.status_code = status
                get.return_value.headers = headers
                get.return_value.iter_content.return_value = chunks
                with self.assertRaises(pricing.PricingError):
                    pricing.sync_catalog(source='api')
                self.assertEqual(pricing.catalog_status()['snapshotId'], first.snapshot_id)


class PricingApiTests(TestCase):
    def setUp(self):
        self.users = [User.objects.create(name=n, email=f'{n}@example.com', normalized_email=f'{n}@example.com', status=1) for n in ('admin', 'developer', 'member', 'outsider')]
        self.space = Space.objects.create(name='Pricing', type=2, creator=self.users[0], status=1)
        self.other = Space.objects.create(name='Other', type=2, creator=self.users[3], status=1)
        for index, user in enumerate(self.users[:3]):
            SpaceMember.objects.create(space=self.space, user=user, role=index + 1)
        SpaceMember.objects.create(space=self.other, user=self.users[3], role=1)
        self.client = APIClient()
        self.client.force_authenticate(self.users[0])
        self.base = f'/api/v1/spaces/{self.space.uuid}/model-pricing'
        self.body = {'provider': 'private-route', 'model': 'my-model', 'kind': 'manual', 'effectiveFrom': '2026-10-26T00:00:00Z', 'rates': {'input': '0', 'output': '2'}}

    def test_members_read_only_admin_manages_outsiders_rejected(self):
        for index in (1, 2):
            self.client.force_authenticate(self.users[index])
            response = self.client.get(self.base)
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.data['data']['canManage'])
            self.assertEqual(self.client.post(self.base + '/rules', self.body, format='json').status_code, 403)
            with patch.object(pricing.http_requests, 'get') as get:
                self.assertEqual(self.client.post(self.base + '/sync', {}, format='json').status_code, 403)
                get.assert_not_called()
        self.client.force_authenticate(self.users[3])
        self.assertEqual(self.client.get(self.base).status_code, 404)

    def test_manual_requires_explicit_aware_time_and_valid_decimal_rates(self):
        for changes in ({'effectiveFrom': None}, {'effectiveFrom': '2026-10-26T01:00:00'}, {'rates': {'input': '-1', 'output': '2'}}, {'rates': {'input': 'NaN', 'output': '2'}}, {'rates': {'input': True, 'output': '2'}}, {'rates': {'input': '1', 'output': '2', 'tiers': []}}):
            response = self.client.post(self.base + '/rules', {**self.body, **changes}, format='json')
            self.assertEqual(response.status_code, 400, response.data)
        response = self.client.post(self.base + '/rules', self.body, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['data']['rates']['input'], '0')

    def test_versions_freeze_catalog_and_delete_is_space_scoped(self):
        pricing.store_catalog(catalog())
        alias = {**self.body, 'provider': 'openai', 'kind': 'alias', 'catalogProvider': 'openai', 'catalogModel': 'same'}
        first = self.client.post(self.base + '/rules', alias, format='json')
        self.assertEqual(first.status_code, 200, first.data)
        second = self.client.post(self.base + '/rules', self.body, format='json')
        self.assertEqual(second.status_code, 200, second.data)
        pricing.store_catalog(catalog(7))
        rules = self.client.get(self.base).data['data']['rules']
        self.assertEqual(next(r for r in rules if r['uuid'] == first.data['data']['uuid'])['rates']['input'], '1')
        self.assertNotEqual(first.data['data']['version'], second.data['data']['version'])
        self.client.force_authenticate(self.users[3])
        other_url = f'/api/v1/spaces/{self.other.uuid}/model-pricing/rules/{first.data["data"]["uuid"]}'
        self.assertEqual(self.client.delete(other_url).status_code, 404)
        self.client.force_authenticate(self.users[0])
        self.assertEqual(self.client.delete(self.base + '/rules/' + first.data['data']['uuid']).status_code, 200)
        self.assertEqual(len(self.client.get(self.base).data['data']['rules']), 1)

    def test_alias_rejects_another_host_or_unsupported_tariff_reference_explicit(self):
        pricing.store_catalog(catalog())
        for changes in ({'catalogProvider': 'openai', 'catalogModel': 'same'}, {'provider': 'openai', 'catalogProvider': 'openai', 'catalogModel': 'tiered'}):
            response = self.client.post(self.base + '/rules', {**self.body, 'kind': 'alias', **changes}, format='json')
            self.assertEqual(response.status_code, 400, response.data)
        response = self.client.post(self.base + '/rules', {**self.body, 'kind': 'reference', 'catalogProvider': 'openai', 'catalogModel': 'same'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)

    @override_settings(AIVISION_MODEL_PRICES=[{'model': 'same', 'provider': 'openai', 'input': '8', 'output': '8'}])
    def test_price_list_keeps_legacy_and_scoped_provenance(self):
        pricing.store_catalog(catalog())
        self.client.post(self.base + '/rules', self.body, format='json')
        rows = pricing.get_space_model_prices(self.space)
        legacy = next(r for r in rows if r.get('input') == '8')
        auto = next(r for r in rows if r.get('pricingSource') == 'catalog' and r['provider'] == 'openai' and r['model'] == 'same')
        manual = next(r for r in rows if r.get('pricingSource') == 'manual')
        self.assertGreater(legacy['pricingPriority'], auto['pricingPriority'])
        self.assertEqual(auto['valuationBasis'], 'current_catalog')
        self.assertNotIn('effectiveFrom', auto)
        self.assertEqual(manual['effectiveFrom'], '2026-10-26T00:00:00+00:00')
        self.assertFalse(any(r.get('pricingSource') == 'manual' for r in pricing.get_space_model_prices(self.other)))

    def test_inventory_refuses_unbounded_unknown_scope_or_incomplete_window(self):
        self.assertEqual(self.client.get(self.base + '/inventory').status_code, 400)
        bounded = {'from': '2026-10-25T00:00:00Z', 'to': '2026-10-26T00:00:00Z'}
        self.assertEqual(self.client.get(self.base + '/inventory', {**bounded, 'agent_uuid': 'foreign'}).status_code, 404)
        with patch('apps.core.services.model_pricing.resolve_signoz_runtime_config', return_value={}):
            with patch('apps.core.trace_query.signoz.provider.SignozTraceProvider._window', return_value=(NativeTraceWindow([], project_id=str(self.space.uuid)), {'complete': False})):
                response = self.client.get(self.base + '/inventory', bounded)
                self.assertEqual(response.status_code, 422)
                self.assertNotIn('models', response.data.get('data') or {})

    def test_inventory_returns_complete_empty_native_scope_without_invented_models(self):
        bounded = {'from': '2026-10-25T00:00:00Z', 'to': '2026-10-26T00:00:00Z'}
        meta = {'complete': True, 'totalItems': 0, 'from': 1792886400000, 'to': 1792972800000}
        with patch('apps.core.services.model_pricing.resolve_signoz_runtime_config', return_value={}):
            with patch('apps.core.trace_query.signoz.provider.SignozTraceProvider._window', return_value=(NativeTraceWindow([], project_id=str(self.space.uuid)), meta)):
                response = self.client.get(self.base + '/inventory', bounded)
                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual(response.data['data']['models'], [])
                self.assertTrue(response.data['data']['complete'])

    def test_inventory_counts_only_calls_starting_inside_window(self):
        pricing.store_catalog(catalog())
        lower, upper = 1792886400000, 1792972800000
        spans = []
        for label, stamp in [('before', lower - 1), ('inside', lower + 1), ('after', upper)]:
            spans.append({'span_id': label, 'trace_id': 'trace', 'name': label,
                'start_time_unix_nano': str(stamp * 1000000), 'end_time_unix_nano': str(stamp * 1000000 + 1000),
                'attributes': {'langfuse.observation.type': 'GENERATION', 'gen_ai.request.model': 'same',
                    'gen_ai.provider.name': 'openai', 'gen_ai.usage.input_tokens': 10, 'gen_ai.usage.output_tokens': 2}})
        payload = {'data': [{'traceId': 'trace', 'spans': spans}],
                   'meta': {'complete': True, 'totalItems': 1, 'from': lower, 'to': upper}}
        with patch('apps.core.services.model_pricing.resolve_signoz_runtime_config', return_value={}):
            with patch('apps.core.trace_query.signoz.provider.SignozTraceProvider._window', return_value=(NativeTraceWindow(payload['data'], project_id=str(self.space.uuid), prices=pricing.get_space_model_prices(self.space)), payload['meta'])):
                response = self.client.get(self.base + '/inventory', {'from': str(lower), 'to': str(upper)})
        self.assertEqual(response.status_code, 200, response.data)
        data = response.data['data']
        self.assertEqual(data['costCoverage']['billableCalls'], 1)
        self.assertEqual(data['models'][0]['calls'], 1)
        self.assertEqual(data['costCoverage']['knownSubtotal'], '0.00001')

    def test_pricing_inventory_excludes_only_the_reserved_demo_agent_at_broad_scope(self):
        lower, upper = 1792886400000, 1792972800000
        bounds = {'from': str(lower), 'to': str(upper)}
        marker = {'onboarding_demo': {'sample_digest': 'sample-digest'}}
        agent_fields = {'space': self.space, 'agent_product': 'langfuse'}
        ordinary = create_test_agent(name='Production', metadata=marker, **agent_fields)
        demo_uuid = str(uuid5(NAMESPACE_URL, f'urn:ai-vision:onboarding-demo:{self.space.uuid}'))
        demo = create_test_agent(uuid=demo_uuid, name='Example', **agent_fields)

        def trace(trace_id, model, amount, agent_uuid):
            span = {
                'span_id': trace_id + '-call',
                'trace_id': trace_id,
                'name': model,
                'start_time_unix_nano': str((lower + 1) * 1000000),
                'end_time_unix_nano': str((lower + 2) * 1000000),
                'resource_attributes': {
                    'aivision.space.id': self.space.uuid,
                    'aivision.agent.id': agent_uuid,
                },
                'attributes': {
                    'aivision.agent.id': demo_uuid,
                    'langfuse.observation.type': 'GENERATION',
                    'langfuse.observation.cost_details': {'total': amount},
                    'gen_ai.request.model': model,
                    'gen_ai.provider.name': 'openai',
                },
            }
            return {'traceId': trace_id, 'spans': [span]}

        ordinary_trace = trace('ordinary', 'production-model', '0.125', ordinary.uuid)
        demo_trace = trace('demo', 'demo-model', '0.875', demo.uuid)

        def inventory_window(query, *, _metrics):
            details = [demo_trace] if query.agent_uuid else [ordinary_trace, demo_trace]
            return (
                NativeTraceWindow(details, project_id=str(self.space.uuid)),
                {'complete': True, 'totalItems': len(details), 'from': lower, 'to': upper},
            )

        def inventory(**scope):
            response = self.client.get(self.base + '/inventory', {**bounds, **scope})
            self.assertEqual(response.status_code, 200, response.data)
            return response.data['data']

        with (
            patch.object(pricing, 'resolve_signoz_runtime_config', return_value={}),
            patch('apps.core.trace_query.signoz.provider.SignozTraceProvider._window', side_effect=inventory_window),
        ):
            self.assertEqual(inventory()['costCoverage']['knownSubtotal'], '1')
            demo.metadata = marker
            demo.save(update_fields=['metadata'])
            for scope in ({}, {'agent_product': 'langfuse'}):
                with self.subTest(scope=scope):
                    data = inventory(**scope)
                    self.assertEqual([row['model'] for row in data['models']], ['production-model'])
                    self.assertEqual(data['costCoverage']['knownSubtotal'], '0.125')
                    self.assertEqual(data['costCoverage']['billableCalls'], 1)
                    self.assertEqual(data['costCoverage']['reportedCalls'], 1)
            data = inventory(agent_uuid=demo.uuid)
            self.assertEqual([row['model'] for row in data['models']], ['demo-model'])
            self.assertEqual(data['costCoverage']['knownSubtotal'], '0.875')

            demo.gmt_deleted = timezone.now()
            demo.save(update_fields=['gmt_deleted'])
            self.assertEqual(inventory()['costCoverage']['knownSubtotal'], '0.125')

    def test_manual_service_prices_apply_without_optional_cache_or_reasoning_prices(self):
        self.client.post(self.base + '/rules', self.body, format='json')
        from apps.core.trace_query.semantics.usage import normalize_usage
        usage = normalize_usage({'gen_ai.usage.input_tokens': 10, 'gen_ai.usage.output_tokens': 2},
            model='my-model', provider='private-route', observed_at='2026-10-26T01:00:00Z',
            prices=pricing.get_space_model_prices(self.space))
        self.assertEqual(usage['cost']['total'], '0.000004')
        self.assertEqual(usage['cost']['pricingSource'], 'manual')

    def test_same_effective_rule_is_rejected_until_old_version_deactivated(self):
        first = self.client.post(self.base + '/rules', self.body, format='json')
        second = self.client.post(self.base + '/rules', {**self.body, 'rates': {'input': '3', 'output': '4'}}, format='json')
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 400)
        self.assertIn('Deactivate', second.data['message'])
        self.client.delete(self.base + '/rules/' + first.data['data']['uuid'])
        replacement = self.client.post(self.base + '/rules', self.body, format='json')
        self.assertEqual(replacement.status_code, 200)
        self.assertNotEqual(first.data['data']['version'], replacement.data['data']['version'])

    def test_sync_failure_keeps_the_catalog_without_returning_internal_detail(self):
        snapshot = pricing.store_catalog(catalog())
        failure = pricing.PricingError('Internal catalog fixture detail')
        with patch.object(pricing, 'sync_catalog', side_effect=failure):
            response = self.client.post(self.base + '/sync', {'source': 'api'}, format='json')
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.data['code'], 1001)
        self.assertEqual(
            response.data['message'],
            'The models.dev pricing catalog could not be refreshed. Try again later.',
        )
        self.assertEqual(response.data['data']['catalog']['snapshotId'], snapshot.snapshot_id)

    def test_inventory_without_signoz_is_explicitly_unsupported(self):
        from apps.core.trace_query.types import TraceProviderNotConfiguredError
        with patch('apps.core.services.model_pricing.resolve_signoz_runtime_config', side_effect=TraceProviderNotConfiguredError('not configured', 3011)):
            with patch('apps.core.trace_query.signoz.provider.SignozTraceProvider.collect_traces') as collect:
                response = self.client.get(self.base + '/inventory', {'from': '2026-10-25T00:00:00Z', 'to': '2026-10-26T00:00:00Z'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('Langfuse', response.data['message'])
        collect.assert_not_called()

    def test_stale_selection_does_not_freeze_new_catalog_silently(self):
        old = pricing.store_catalog(catalog())
        pricing.store_catalog(catalog(7))
        response = self.client.post(self.base + '/rules', {**self.body, 'kind': 'reference',
            'catalogProvider': 'openai', 'catalogModel': 'same', 'catalogSnapshot': old.snapshot_id}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get(self.base).data['data']['rules'], [])
