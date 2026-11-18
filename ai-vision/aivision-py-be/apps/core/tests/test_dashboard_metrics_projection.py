"""Dashboard aggregation must retain raw semantics without rereading messages."""
from copy import deepcopy
import hashlib
import json
import math
from unittest import skipUnless
from unittest.mock import Mock
from unittest.mock import patch
from uuid import uuid4

from django.db import connection
from django.test import SimpleTestCase, override_settings

from apps.core.tests import test_managed_queries as managed_tests
from apps.core.tests import test_native_read_model as read_model_tests
from apps.core.trace_query.native_query import NativeTraceWindow
from apps.core.trace_query.metrics_projection import restore_metrics
from apps.core.trace_query.signoz.read_model_projection import build_projection
from apps.core.trace_query.signoz.read_model import projection_context
from apps.core.trace_query.types import BaseTraceQuery, TraceQueryServiceError


class DashboardMetricsProjectionTests(SimpleTestCase):
    def setUp(self):
        read_model_tests.ReadModelTests.setUp(self)
        # These cases pin the complete metrics-window compatibility path.
        # The typed SQL capability and its failure policy have their own tests.
        self.enterContext(patch('apps.core.trace_query.signoz.statistics_aggregate.query_metrics_aggregate', return_value=None))
    snapshot = read_model_tests.ReadModelTests.snapshot
    def snapshot_payload(self, *, rows=None, raw=None, mode=False):
        from apps.core.trace_query.metrics_projection import ROW_FIELDS
        payload = managed_tests.SnapshotWindowTests.snapshot_payload(self, rows=deepcopy(rows), raw=raw, mode=mode)
        if not mode:
            payload['rows'] = [{key: value for key, value in row.items() if key in ROW_FIELDS or key == 'sourceRevision'}
                               for row in payload['rows']]
            payload['contract'] = 'native-trace-metrics-window-v1'
            payload['meta']['representation'] = 'metrics-v1'
        return payload

    def projected(self, detail=None):
        source = self.snapshot()
        source['span_count'] = len(source['spans'])
        if detail is not None:
            source.update(trace_id=detail['traceId'], spans=detail['spans'], span_count=len(detail['spans']))
        payload = build_projection(source, space_id='space', prices=[])
        return {**payload['row'], 'sourceRevision': source['source_revision']}

    def metric_query(self, **params):
        return BaseTraceQuery(self.space, None, {
            key: value for key, value in {**self.query.query_params, **params}.items()
            if key not in {'page', 'limit', 'fields'}
        })

    def test_projector_stores_compact_complete_observation_facts(self):
        self.detail['spans'][0]['attributes']['gen_ai.input.messages'] = 'private-input' * 1000
        row = self.projected()
        self.assertIn('_metrics', row.keys())
        self.assertEqual(row['_metrics']['version'], 'native-trace-metrics-v1')
        facts = row['_metrics']['observations']
        self.assertEqual(len(facts), row['span_count'])
        self.assertEqual(facts[0]['id'], row['rootObservationId'])
        for key in ('input', 'output', 'attributes', 'resourceAttributes'):
            self.assertNotIn(key, facts[0])
        self.assertNotIn('private-input', str(facts))

    def test_operation_preview_upgrade_enrolls_legacy_projection_context(self):
        from apps.core.trace_query.signoz.read_model_projection import project_batch
        previous = {'semantics': 'native-trace-summary-pricing-v5-equivalent-sources', 'prices': []}
        digest = hashlib.sha256(json.dumps(previous, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
        client = Mock()
        client.get.return_value = {'supported': True, 'data': []}
        project_batch(client, space_id='space', prices=[])
        sent_context = client.get.call_args.kwargs['params']['projection_context']
        self.assertNotEqual(sent_context, digest)
        self.assertEqual(sent_context, projection_context([]))

    def test_projector_requests_incremental_supplement_and_exposes_old_backend_support(self):
        from apps.core.trace_query.signoz.read_model_projection import project_batch
        for acknowledgement in ({'required_supplement': 'native-trace-metrics-v1'}, {}):
            client = Mock()
            client.get.return_value = {'supported': True, 'data': [], **acknowledgement}
            result = project_batch(client, space_id='space', prices=[])
            self.assertEqual(client.get.call_args.kwargs['params']['required_supplement'], 'native-trace-metrics-v1')
            self.assertEqual(result['supplementSupported'], bool(acknowledgement))

    def test_projector_rejects_wrong_supplement_acknowledgement(self):
        from apps.core.trace_query.signoz.read_model_projection import project_batch
        client = Mock()
        client.get.return_value = {'supported': True, 'data': [], 'required_supplement': 'another-version'}
        with self.assertRaises(TraceQueryServiceError):
            project_batch(client, space_id='space', prices=[])

    def test_compact_facts_preserve_unknown_nested_usage_groups_and_buckets(self):
        root = self.detail['spans'][0]
        root['attributes']['gen_ai.usage.total_cost'] = '0.012'
        self.detail['spans'].extend([
            {**deepcopy(root), 'span_id': 'wrapper', 'parent_span_id': root['span_id'], 'attributes': {}},
            {**deepcopy(root), 'span_id': 'nested', 'parent_span_id': 'wrapper',
             'attributes': {**root['attributes'], 'gen_ai.request.model': 'other'}},
            {**deepcopy(root), 'span_id': 'embedding', 'parent_span_id': 'wrapper',
             'attributes': {'langfuse.observation.type': 'EMBEDDING'}},
            {**deepcopy(root), 'span_id': 'tool', 'parent_span_id': root['span_id'],
             'attributes': {'langfuse.observation.type': 'TOOL'}},
        ])
        row = self.projected()
        raw = NativeTraceWindow([self.detail], project_id='space', prices=[])
        compact = NativeTraceWindow.from_summary_rows([row], include_metrics=True)
        for bucket in ('minute', 'hour', 'day'):
            for group in ('model', 'provider'):
                params = {'bucket': bucket, 'groupBy': group}
                self.assertEqual(compact.metrics(params), raw.metrics(params))
        self.assertEqual(compact.metrics({})['usage']['unknownReason'], 'ambiguous_nested_generations')

    @override_settings(AIVISION_QUERY_MODE='auto')
    def test_compact_metrics_accept_generated_json_duration_rounding(self):
        for duration_ns, extracted_ms in ((25871000, 25.871), (1225000, 1.225), (41999247000, 41999.247)):
            with self.subTest(duration_ns=duration_ns):
                span = self.detail['spans'][0]
                span['end_time_unix_nano'] = str(int(span['start_time_unix_nano']) + duration_ns)
                row = self.projected()
                # seekdb JSON_OBJECT/JSON_EXTRACT can shorten the stored double
                # when constructing metadata_json_v1, independently of latency.
                row['duration_ms'] = extracted_ms
                self.client.get.return_value = self.snapshot_payload(rows=[row])
                result = self.provider.query_metrics(self.metric_query())
                self.assertTrue(result['meta']['complete'])
                self.assertEqual(result['traceCount'], 1)
                self.assertEqual(result['usage']['total'], 12)
                self.assertAlmostEqual(result['latencyMs']['mean'], extracted_ms, places=10)

    def test_compact_duration_rounding_is_limited_to_two_float_steps(self):
        row = self.projected()
        row['_metrics']['latency'] = 0.001
        for direction in (-math.inf, math.inf):
            duration = 1.0
            for steps in range(1, 4):
                duration = math.nextafter(duration, direction)
                row['duration_ms'] = duration
                with self.subTest(direction=direction, steps=steps):
                    if steps <= 2:
                        self.assertEqual(restore_metrics(row)['latency'], 0.001)
                    else:
                        with self.assertRaises(ValueError):
                            restore_metrics(row)

    def test_compact_duration_preserves_null_and_finite_nonnegative_validation(self):
        row = self.projected()
        for latency, duration in ((None, None), (0, 0), (1, 1000)):
            with self.subTest(latency=latency, duration=duration):
                row['_metrics']['latency'], row['duration_ms'] = latency, duration
                self.assertEqual(restore_metrics(row)['latency'], latency)
        for latency, duration in (
            (None, 0), (0, None), (False, 0), (0, False),
            (-1, -1000), (0, -math.ulp(0.0)),
            (float('nan'), 1), (1, float('nan')),
            (float('inf'), float('inf')), (1, float('inf')),
            (1e308, 1e308), (10**309, 1000), (1, 10**309),
            (2**60, 2**60 * 1000 + 1), (1, '1000'),
        ):
            with self.subTest(latency=latency, duration=duration):
                row['_metrics']['latency'], row['duration_ms'] = latency, duration
                with self.assertRaises(ValueError):
                    restore_metrics(row)

    def test_compact_metrics_preserve_subscription_equivalent_coverage(self):
        root = self.detail['spans'][0]
        root['attributes'].update({
            'aivision.billing.mode': 'subscription',
            'gen_ai.usage.total_cost': '3.5',
        })
        from apps.core.tests.test_model_pricing_semantics import official_price
        prices = [official_price(model='fixture', input='3', output='6')]
        payload = build_projection(self.snapshot(), space_id='space', prices=prices)
        compact = NativeTraceWindow.from_summary_rows([payload['row']], include_metrics=True)
        result = compact.metrics({})
        self.assertIsNone(result['usage']['cost']['total'])
        self.assertEqual(result['costCoverage']['subscriptionCalls'], 1)
        self.assertEqual(result['costCoverage']['listPriceEquivalentSources'], {'official_catalog': 1})
        self.assertEqual(result['costCoverage']['unknownReasons'], {'subscription_billing': 1})
        self.assertEqual(result, NativeTraceWindow([self.detail], project_id='space', prices=prices).metrics({}))

    def test_compact_metrics_preserve_supported_otel_status_aliases_and_reported_cost(self):
        for code in ('STATUS_CODE_ERROR', 'STATUS_CODE_OK', 'ERROR', 'OK', '2', 0):
            with self.subTest(code=code):
                self.detail['spans'][0]['status'] = {'code': code}
                self.detail['spans'][0]['attributes'].update({
                    'gen_ai.usage.input_cost': '0.002', 'gen_ai.usage.output_cost': '0.001',
                })
                row = self.projected()
                raw = NativeTraceWindow([self.detail], project_id='space', prices=[])
                compact = NativeTraceWindow.from_summary_rows([row], include_metrics=True)
                self.assertEqual(compact.metrics({}), raw.metrics({}))

    @override_settings(AIVISION_QUERY_MODE='auto')
    def test_compact_fallback_preserves_canonical_scalar_model_and_provider_dimensions(self):
        original = deepcopy(self.detail)
        for field in ('gen_ai.request.model', 'gen_ai.provider.name'):
            for value in (123, True, 1.5, 9007199254740993):
                with self.subTest(field=field, value=value):
                    self.detail = deepcopy(original)
                    child = deepcopy(self.detail['spans'][0])
                    child.update(span_id='child-generation', parent_span_id=self.detail['spans'][0]['span_id'])
                    child['attributes'][field] = value
                    self.detail['spans'].append(child)
                    row = self.projected()
                    raw = NativeTraceWindow([self.detail], project_id='space', prices=[])
                    self.client.get.return_value = self.snapshot_payload(rows=[row])
                    for group in ('model', 'provider'):
                        self.client.get.reset_mock()
                        result = self.provider.query_metrics(self.metric_query(groupBy=group))
                        self.assertEqual({key: item for key, item in result.items() if key != 'meta'},
                                         raw.metrics({'groupBy': group}))
                        self.client.get.assert_called_once()

    def test_invalid_price_currency_keeps_unknown_cost_without_losing_token_metrics(self):
        prices = [{'model': 'fixture', 'provider': 'test', 'currency': None,
                   'version': 'fixture', 'effectiveFrom': '2020-01-01T00:00:00Z',
                   'unitTokens': 1000, 'input': '0.1', 'output': '0.2'}]
        source = self.snapshot()
        payload = build_projection(source, space_id='space', prices=prices)
        raw = NativeTraceWindow([self.detail], project_id='space', prices=prices)
        compact = NativeTraceWindow.from_summary_rows([payload['row']], include_metrics=True)
        self.assertEqual(compact.metrics({}), raw.metrics({}))
        self.assertEqual(compact.metrics({})['usage']['total'], 12)
        self.assertIsNone(compact.metrics({})['usage']['cost']['total'])

    @override_settings(AIVISION_QUERY_MODE='auto')
    def test_dashboard_metrics_use_one_hybrid_snapshot_and_no_detail_reads(self):
        self.client.get.return_value = self.snapshot_payload(rows=[self.projected()])
        with patch.object(self.provider, 'collect_traces') as collect, patch.object(self.provider, 'get_trace_detail') as detail:
            result = self.provider.query_metrics(self.metric_query())
        self.assertEqual(result['traceCount'], 1)
        self.assertEqual(result['usage']['total'], 12)
        self.assertEqual(result['meta']['queryMode'], 'hybrid_snapshot')
        self.client.get.assert_called_once()
        self.assertEqual(self.client.get.call_args.kwargs['params']['raw'], 'false')
        self.assertTrue(self.client.get.call_args.args[0].endswith('/metrics-window'))
        collect.assert_not_called()
        detail.assert_not_called()

    @override_settings(AIVISION_QUERY_MODE='auto')
    def test_older_summary_without_facts_falls_back_to_complete_raw_snapshot(self):
        row = self.projected()
        row.pop('_metrics', None)
        self.client.get.side_effect = [self.snapshot_payload(rows=[row]), self.snapshot_payload(raw=[self.detail], mode=True)]
        result = self.provider.query_metrics(self.metric_query())
        self.assertEqual(result['usage']['total'], 12)
        self.assertEqual(result['meta']['queryMode'], 'raw_snapshot')
        self.assertEqual(self.client.get.call_count, 2)

    @override_settings(AIVISION_QUERY_MODE='raw')
    def test_emergency_raw_mode_never_uses_derived_facts(self):
        self.client.get.return_value = self.snapshot_payload(raw=[self.detail], mode=True)
        result = self.provider.query_metrics(self.metric_query())
        self.assertEqual(result['usage']['total'], 12)
        self.client.get.assert_called_once()
        self.assertEqual(self.client.get.call_args.kwargs['params']['raw'], 'true')

    @override_settings(AIVISION_QUERY_MODE='auto')
    def test_missing_metrics_endpoint_falls_back_to_existing_raw_window(self):
        self.client.get.side_effect = [TraceQueryServiceError('not found', 3019),
                                      self.snapshot_payload(raw=[self.detail], mode=True)]
        result = self.provider.query_metrics(self.metric_query())
        self.assertEqual(result['usage']['total'], 12)
        self.assertEqual(self.client.get.call_count, 2)
        self.assertTrue(self.client.get.call_args_list[0].args[0].endswith('/metrics-window'))
        self.assertTrue(self.client.get.call_args_list[1].args[0].endswith('/window'))

    @override_settings(AIVISION_QUERY_MODE='auto')
    def test_content_predicates_use_full_content_instead_of_compact_facts(self):
        self.detail['spans'][0]['attributes']['gen_ai.input.messages'] = 'needle'
        for params in ({'keyword': 'needle', 'searchType': 'input'},
                       {'filters': [{'field': 'attributes.gen_ai.input.messages', 'value': 'needle'}]}):
            self.client.get.reset_mock()
            self.client.get.return_value = self.snapshot_payload(raw=[self.detail], mode=True)
            self.assertEqual(self.provider.query_metrics(self.metric_query(**params))['traceCount'], 1)
            self.assertTrue(self.client.get.call_args.args[0].endswith('/window'))
            self.assertEqual(self.client.get.call_args.kwargs['params']['raw'], 'true')

    @override_settings(AIVISION_QUERY_MODE='auto')
    def test_hybrid_dirty_trace_keeps_complete_metrics_and_scope(self):
        row = self.projected()
        dirty = deepcopy(self.detail)
        dirty['traceId'] = dirty['spans'][0]['trace_id'] = 'dirty-trace'
        dirty['spans'][0]['span_id'] = 'dirty-span'
        self.client.get.return_value = self.snapshot_payload(rows=[row], raw=[dirty])
        result = self.provider.query_metrics(self.metric_query())
        self.assertEqual(result['traceCount'], 2)
        self.assertEqual(result['usage']['total'], 24)
        self.assertEqual(result['meta']['projectedTraceCount'], 1)
        self.assertEqual(result['meta']['rawTraceCount'], 1)

    @override_settings(AIVISION_QUERY_MODE='auto')
    def test_malformed_facts_raise_instead_of_returning_partial_metrics(self):
        good = self.projected()
        for mutate in (
            lambda row: row['_metrics']['observations'].clear(),
            lambda row: row['_metrics']['observations'][0].update(traceId='another'),
            lambda row: row['_metrics']['observations'][0]['usage'].update(total=-1),
            lambda row: row['_metrics']['observations'][0].update(startTime='2000-01-01T00:00:00Z'),
            lambda row: row['_metrics']['observations'][0].update(model=['unhashable']),
            lambda row: row['_metrics']['observations'][0].update(provider={'invalid': 'object'}),
            lambda row: row['_metrics']['observations'][0].update(model=float('nan')),
            lambda row: row['_metrics']['observations'][0]['otelStatus'].update(code=2),
            lambda row: row['_metrics'].update(latency=42),
        ):
            with self.subTest(mutate=mutate):
                row = deepcopy(good)
                mutate(row)
                self.client.get.return_value = self.snapshot_payload(rows=[row])
                with self.assertRaises(TraceQueryServiceError):
                    self.provider.query_metrics(self.metric_query())

    @override_settings(AIVISION_QUERY_MODE='auto')
    def test_observation_filter_still_reads_raw_to_keep_any_observation_semantics(self):
        self.client.get.return_value = self.snapshot_payload(raw=[self.detail], mode=True)
        result = self.provider.query_metrics(self.metric_query(filters=[{
            'field': 'attributes.gen_ai.request.model', 'value': 'fixture',
        }]))
        self.assertEqual(result['traceCount'], 1)
        self.assertEqual(self.client.get.call_args.kwargs['params']['raw'], 'true')


@skipUnless(connection.vendor == 'mysql', 'requires real MySQL-compatible JSON generated columns')
@override_settings(AIVISION_QUERY_MODE='auto')
class GeneratedMetricsDatabaseTests(SimpleTestCase):
    """Real JSON conversion and provider logic; only SigNoz transport is stubbed.

    Discovered by the existing OceanBase/seekdb CI matrix; skipped on SQLite.
    """

    databases = {'default'}
    snapshot = read_model_tests.ReadModelTests.snapshot
    projected = DashboardMetricsProjectionTests.projected
    snapshot_payload = DashboardMetricsProjectionTests.snapshot_payload
    metric_query = DashboardMetricsProjectionTests.metric_query

    def setUp(self):
        read_model_tests.ReadModelTests.setUp(self)
        self.enterContext(patch('apps.core.trace_query.signoz.statistics_aggregate.query_metrics_aggregate', return_value=None))
        # These are the numeric JSON expressions of SigNoz's stored helpers.
        # OceanBase lacks MySQL temporary tables; use a unique table in Django's
        # isolated test database, and remove it even when assertions fail.
        self.table = 'aivision_test_metrics_' + uuid4().hex
        with connection.cursor() as cursor:
            cursor.execute(f'''CREATE TABLE {self.table} (
                trace_id VARCHAR(64) PRIMARY KEY,
                row_json LONGTEXT NOT NULL,
                metadata_json_v1 LONGTEXT GENERATED ALWAYS AS (
                    JSON_OBJECT('duration_ms', JSON_EXTRACT(row_json, '$."duration_ms"'))
                ) STORED,
                metrics_json_v1 LONGTEXT GENERATED ALWAYS AS (JSON_EXTRACT(row_json, '$._metrics')) STORED
            )''')
        self.addCleanup(self.drop_table)

    def drop_table(self):
        with connection.cursor() as cursor:
            cursor.execute(f'DROP TABLE {self.table}')

    def store_rows(self, rows):
        with connection.cursor() as cursor:
            cursor.execute(f'DELETE FROM {self.table}')
            cursor.executemany(
                f'INSERT INTO {self.table} (trace_id, row_json) VALUES (%s, %s)',
                [(row['trace_id'], json.dumps(row, allow_nan=False)) for row in rows],
            )

    def stored_snapshot(self, path, *, params):
        self.assertTrue(path.endswith('/metrics-window'))
        self.assertEqual(params['raw'], 'false')
        with connection.cursor() as cursor:
            cursor.execute(f'''SELECT row_json, metadata_json_v1, metrics_json_v1
                FROM {self.table} ORDER BY trace_id''')
            rows = [{**json.loads(raw), **json.loads(metadata), '_metrics': json.loads(metrics)}
                    for raw, metadata, metrics in cursor.fetchall()]
        return self.snapshot_payload(rows=rows)

    def fixture(self, *, unknown=False):
        details = []
        for index, duration_ns in enumerate((25871000, 1225000, 41999247000)):
            detail = deepcopy(self.detail)
            detail['traceId'] = f'json-duration-{index}'
            span = detail['spans'][0]
            span['trace_id'] = detail['traceId']
            span['end_time_unix_nano'] = str(int(span['start_time_unix_nano']) + duration_ns)
            if unknown and index == 2:
                for field in ('input', 'output', 'total'):
                    span['attributes'].pop(f'gen_ai.usage.{field}_tokens')
            details.append(detail)
        return details, [self.projected(detail=detail) for detail in details]

    def test_generated_json_round_trip_preserves_complete_provider_metrics(self):
        for unknown in (False, True):
            with self.subTest(unknown=unknown):
                details, rows = self.fixture(unknown=unknown)
                self.store_rows(rows)
                self.client.get.side_effect = self.stored_snapshot
                self.client.get.reset_mock()
                oracle = NativeTraceWindow(details, project_id='space', prices=[])
                with patch.object(self.provider, 'collect_traces') as collect, patch.object(self.provider, 'get_trace_detail') as detail:
                    actual = self.provider.query_metrics(self.metric_query())
                self.assertEqual({key: value for key, value in actual.items() if key != 'meta'}, oracle.metrics({}))
                self.assertTrue(actual['meta']['complete'])
                self.assertEqual(actual['meta']['projectedTraceCount'], 3)
                self.assertEqual(actual['meta']['rawTraceCount'], 0)
                self.assertEqual(actual['usage']['total'], None if unknown else 36)
                self.assertEqual(actual['usage']['unknownReason'], 'usage_missing' if unknown else None)
                self.assertEqual(actual['coverage']['knownTokens'], 24 if unknown else 36)
                self.client.get.assert_called_once()
                collect.assert_not_called()
                detail.assert_not_called()

    def test_generated_json_round_trip_rejects_true_duration_mismatch(self):
        _, rows = self.fixture()
        for row in rows:
            with self.subTest(trace_id=row['trace_id']):
                corrupted = deepcopy(row)
                corrupted['duration_ms'] += 1
                self.store_rows([corrupted])
                self.client.get.side_effect = self.stored_snapshot
                self.client.get.reset_mock()
                with patch.object(self.provider, 'collect_traces') as collect, self.assertRaises(TraceQueryServiceError) as error:
                    self.provider.query_metrics(self.metric_query())
                self.assertEqual(error.exception.code, 3035)
                self.assertIn('metrics latency differs', str(error.exception))
                self.client.get.assert_called_once()
                collect.assert_not_called()
