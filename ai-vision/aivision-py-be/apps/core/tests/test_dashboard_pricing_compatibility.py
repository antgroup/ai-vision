"""Dashboard acceleration retains Space pricing evidence across public callers."""
from copy import deepcopy
from datetime import datetime, timezone
from unittest.mock import patch

from django.test import TestCase, override_settings

from apps.core.models import ModelPricingRule, Space
from apps.core.services import model_pricing
from apps.core.tests import test_dashboard_metrics_projection as metrics_tests
from apps.core.tests import test_managed_queries as managed_tests
from apps.core.tests import test_native_read_model as read_tests
from apps.core.tests.helpers import create_local_user
from apps.core.trace_query.native_query import NativeTraceWindow
from apps.core.trace_query.signoz.read_model import projection_context
from apps.core.trace_query.signoz.read_model_projection import build_projection
from apps.core.trace_query.types import BaseTraceQuery, SessionListQuery, TraceListQuery


@override_settings(AIVISION_QUERY_MODE='auto', AIVISION_MODEL_PRICES=[])
class DashboardPricingCompatibilityTests(TestCase):
    snapshot = read_tests.ReadModelTests.snapshot

    def setUp(self):
        read_tests.ReadModelTests.setUp(self)
        self.owner = create_local_user('dashboard-pricing')
        self.space = Space.objects.create(uuid='space', name='Pricing', type=2, creator=self.owner)
        self.query = TraceListQuery(self.space, None, self.query.query_params)
        self.detail['spans'][0]['attributes']['langfuse.session.id'] = 'pricing-session'

    def rule(self, kind, **values):
        return ModelPricingRule.objects.create(space=self.space, creator=self.owner,
            provider='test', model='fixture', kind=kind,
            effective_from=datetime(2026, 1, 1, tzinfo=timezone.utc), **values)

    def assert_surfaces(self, *, source=None, reason=None, subtotal=None, equivalent=None):
        prices = model_pricing.get_space_model_prices(self.space)
        context = projection_context(prices)
        self.payload['meta']['projection_context'] = context
        projection = build_projection(self.snapshot(), space_id='space', prices=prices)
        row = {**projection['row'], 'sourceRevision': 3}
        canonical = NativeTraceWindow([self.detail], project_id='space', prices=prices)
        calls = []

        def get(path, *, params):
            calls.append((path, deepcopy(params)))
            if path.endswith('/metrics-aggregate') or path.endswith('/sessions/page'):
                return {'supported': False, 'reason': 'fixture older statistics/session capability'}
            if path.endswith('/metrics-window'):
                return metrics_tests.DashboardMetricsProjectionTests.snapshot_payload(self, rows=[row])
            if path.endswith('/read-model/window'):
                return managed_tests.SnapshotWindowTests.snapshot_payload(self, rows=[row])
            if path.endswith('/read-model/page'):
                return {'supported': True, 'data': [row], 'facets': canonical.facets({}),
                        'meta': {**self.payload['meta'], 'page': params['page'], 'limit': params['limit']}}
            raise AssertionError('Unexpected external read: ' + path)

        self.client.get.side_effect = get
        params = {key: value for key, value in self.query.query_params.items()
                  if key not in {'page', 'limit', 'fields'}}
        metrics = self.provider.query_metrics(BaseTraceQuery(self.space, None, params))
        listing = self.provider.query_traces(self.query)
        session = self.provider.list_sessions(SessionListQuery(self.space, None,
            {**params, 'session_semantics': 'canonical-trace-v1'}))
        expected = canonical.metrics({})['costCoverage']
        self.assertEqual(metrics['costCoverage'], expected)
        self.assertEqual(listing['data'][0]['usage']['costCoverage'], expected)
        self.assertEqual(session['data'][0]['costCoverage'], expected)
        self.assertEqual(metrics['meta']['queryMode'], 'hybrid_snapshot')
        self.assertEqual(expected['knownSubtotal'], subtotal)
        self.assertEqual(expected['listPriceEquivalentSubtotal'], equivalent)
        if source:
            self.assertEqual(expected[source + 'Calls'], 1)
        if reason:
            self.assertEqual(expected['unknownReasons'], {reason: 1})
        if equivalent:
            self.assertEqual(expected['listPriceEquivalentSources'], {'official_catalog': 1})
        for path, wire in calls:
            self.assertEqual(wire['projection_context'], context, path)
        self.assertEqual([path.rsplit('/', 1)[-1] for path, _ in calls[:2]],
                         ['metrics-aggregate', 'metrics-window'])

    def test_default_unknown_prices_keep_evidence_through_metrics_trace_and_session(self):
        self.assert_surfaces(reason='price_not_configured')

    def test_space_manual_price_keeps_source_and_decimal_subtotal(self):
        self.rule('manual', rates={'input': '2', 'output': '4'})
        self.assert_surfaces(source='manual', subtotal='0.000028')

    def test_current_catalog_keeps_catalog_source(self):
        model_pricing.store_catalog({'test': {'id': 'test', 'models': {
            'fixture': {'id': 'fixture', 'cost': {'input': 2, 'output': 4}}}}})
        self.assert_surfaces(source='catalog', subtotal='0.000028')

    def test_subscription_marker_keeps_unknown_billed_cost(self):
        self.rule('subscription')
        self.assert_surfaces(source='subscription', reason='subscription_billing')

    def test_subscription_equivalent_does_not_enter_known_subtotal(self):
        model_pricing.store_catalog({'openai': {'id': 'openai', 'models': {
            'fixture': {'id': 'fixture', 'cost': {'input': 2, 'output': 4}}}}})
        self.rule('manual', rates={'input': '99', 'output': '99'})
        self.rule('subscription')
        self.assert_surfaces(source='subscription', reason='subscription_billing', equivalent='0.000028')
