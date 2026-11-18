import json
"""Standard default queries, complete snapshots, and managed projection lifecycle."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, TestCase, override_settings

from apps.core.tests import test_native_read_model as read_model_tests
from apps.core.trace_query.signoz.provider import SignozTraceProvider
from apps.core.trace_query.types import TraceListQuery, TraceQueryServiceError


class StandardQueryControlTests(SimpleTestCase):
    def test_only_explicit_operator_recovery_has_a_longer_http_deadline(self):
        from apps.core.trace_query.signoz.provider import _SigNozClient
        from apps.core.trace_query.signoz.read_model import PATH
        client = _SigNozClient({'endpoint': 'http://query.invalid', 'api_key': 'fixture-key'})
        response = Mock(status_code=200)
        response.json.return_value = {'supported': True}
        response.headers = {}
        response.iter_content.return_value = [json.dumps(response.json.return_value).encode()]
        with patch('apps.core.trace_query.signoz.provider.requests.post', return_value=response) as post:
            client.post(PATH + '/backfill', payload={'space_id': 'space'})
            self.assertEqual(post.call_args.kwargs['timeout'], 30)
            self.assertNotIn('timeout', post.call_args.kwargs['headers'])
            client.post(PATH + '/maintenance/resume', payload={}, headers={'X-Signoz-Ingest-Key': 'fixture-operator'})
            self.assertEqual(post.call_args.kwargs['timeout'], 245)
            self.assertEqual(post.call_args.kwargs['headers']['timeout'], '240')
            self.assertEqual(post.call_args.kwargs['headers']['X-Signoz-Ingest-Key'], 'fixture-operator')

    def test_only_auto_and_raw_are_valid(self):
        from apps.core.trace_query.signoz.query_control import query_mode
        with override_settings(AIVISION_QUERY_MODE='auto'):
            self.assertEqual(query_mode(), 'auto')
        with override_settings(AIVISION_QUERY_MODE='raw'):
            self.assertEqual(query_mode(), 'raw')
        with override_settings(AIVISION_QUERY_MODE='shadow'), self.assertRaises(TraceQueryServiceError):
            query_mode()


class SnapshotWindowTests(SimpleTestCase):
    setUp = read_model_tests.ReadModelTests.setUp

    def test_sdk_username_is_not_a_business_space_physical_subject(self):
        from apps.core.trace_query.signoz.snapshot_window import query_snapshot_window
        row = deepcopy(self.payload['data'][0])
        row['userId'] = 'sdk-task-user'
        self.client.get.return_value = self.snapshot_payload(rows=[row])
        query = TraceListQuery(self.space, None, {**self.query.query_params, 'username': 'sdk-task-user'})
        window, _ = query_snapshot_window(self.provider, query)
        self.assertNotIn('user', self.client.get.call_args.kwargs['params'])
        self.assertEqual(window.list({'username': 'sdk-task-user'})['meta']['totalItems'], 1)
    def snapshot_payload(self, *, rows=None, raw=None, mode=False):
        rows, raw = rows or [], raw or []
        return {'supported': True, 'contract': 'native-trace-window-v1', 'complete': True,
                'rows': rows, 'raw': raw, 'meta': {
                    'from': self.query.get_param('from'), 'to': self.query.get_param('to'),
                    'space_id': 'space', 'user': '', 'agent_product': 'stored-product',
                    'agent_uuid': 'agent-a', 'projection_context': self.payload['meta']['projection_context'],
                    'raw': mode, 'traceCount': len(rows) + len(raw),
                    'projectedTraceCount': len(rows), 'rawTraceCount': len(raw), 'omittedAgentTraceCount': 0,
                    'spanCount': sum(row['span_count'] for row in rows) + sum(len(item['spans']) for item in raw),
                    'rawSpanCount': sum(len(item['spans']) for item in raw)}}

    def test_hybrid_uses_full_canonical_content_and_no_per_trace_fetch(self):
        from apps.core.trace_query.signoz.snapshot_window import query_snapshot_window
        row = deepcopy(self.payload['data'][0])
        row['attributes.gen_ai.input.messages'] = 'x' * 1500 + 'needle-at-end'
        row['ioPreview']['input'] = 'x' * 1024
        self.client.get.return_value = self.snapshot_payload(rows=[row])
        with patch.object(self.provider, 'get_trace_detail') as detail:
            window, meta = query_snapshot_window(self.provider, self.query)
        self.assertEqual(window.list({'searchType': 'input', 'keyword': 'needle-at-end'})['meta']['totalItems'], 1)
        self.assertEqual(meta['queryMode'], 'hybrid_snapshot')
        detail.assert_not_called()
        self.client.get.assert_called_once()

    def test_incomplete_content_search_recovers_raw_even_with_metadata_projection(self):
        from apps.core.trace_query.native_query import NativeTraceWindow
        from apps.core.trace_query.signoz.snapshot_window import query_snapshot_window
        detail = deepcopy(self.detail)
        detail['spans'][0]['attributes'].update({
            'gen_ai.input.messages': 'question needle-at-end',
            'gen_ai.output.messages': 'answer needle-at-end',
        })
        row = deepcopy(self.payload['data'][0])
        row.update(_contentComplete=False)
        row['attributes.gen_ai.input.messages'] = None
        row['attributes.gen_ai.output.messages'] = None
        for predicate in (
            {'searchType': 'input', 'keyword': 'needle-at-end'},
            {'searchType': 'output', 'keyword': 'needle-at-end'},
            {'input_keyword': 'needle-at-end'},
            {'filters': [{'field': 'attributes.gen_ai.input.messages', 'op': 'LIKE', 'value': '%needle-at-end%'}]},
        ):
            with self.subTest(predicate=predicate):
                self.client.reset_mock()
                self.client.get.side_effect = [self.snapshot_payload(rows=[row]),
                                              self.snapshot_payload(raw=[detail], mode=True)]
                query = TraceListQuery(self.space, None, {**self.query.query_params,
                                       'fields': ['trace_id', 'usage'], **predicate})
                window, meta = query_snapshot_window(self.provider, query)
                self.assertEqual(window.list(query.query_params)['data'],
                                 NativeTraceWindow([detail], project_id='space', prices=[], include_previews=True).list(query.query_params)['data'])
                self.assertEqual(meta['queryMode'], 'raw_snapshot')
                self.assertEqual([call.kwargs['params']['raw'] for call in self.client.get.call_args_list],
                                 ['false', 'true'])

    def test_raw_mode_ignores_clean_projections_and_checks_scope(self):
        from apps.core.trace_query.signoz.snapshot_window import query_snapshot_window
        self.client.get.return_value = self.snapshot_payload(raw=[self.detail], mode=True)
        window, meta = query_snapshot_window(self.provider, self.query, raw=True)
        self.assertEqual(len(window.rows), 1)
        self.assertEqual(meta['queryMode'], 'raw_snapshot')
        self.assertEqual(self.client.get.call_args.kwargs['params']['raw'], 'true')
        bad = deepcopy(self.detail)
        bad['spans'][0]['resource_attributes']['aivision.space.id'] = 'other'
        self.client.get.return_value = self.snapshot_payload(raw=[bad], mode=True)
        with self.assertRaises(TraceQueryServiceError):
            query_snapshot_window(self.provider, self.query, raw=True)

    def test_incomplete_duplicate_and_scope_mismatch_never_become_empty_success(self):
        from apps.core.trace_query.signoz.snapshot_window import query_snapshot_window
        good = self.snapshot_payload(rows=[self.payload['data'][0]])
        for mutation in (lambda p: p.update(complete=False), lambda p: p['meta'].update(space_id='other'),
                         lambda p: p['meta'].update(traceCount=2), lambda p: p['rows'].append(p['rows'][0])):
            candidate = deepcopy(good)
            mutation(candidate)
            self.client.get.return_value = candidate
            with self.assertRaises(TraceQueryServiceError):
                query_snapshot_window(self.provider, self.query)

    def test_snapshot_beyond_old_10k_limit_keeps_all_rows(self):
        from apps.core.trace_query.signoz.snapshot_window import query_snapshot_window
        row = self.payload['data'][0]
        rows = [{**row, 'trace_id': f'{i:032x}'} for i in range(10001)]
        self.client.get.return_value = self.snapshot_payload(rows=rows)
        window, meta = query_snapshot_window(self.provider, self.query)
        self.assertEqual(len(window.rows), 10001)
        self.assertTrue(meta['complete'])

    def test_multiple_agents_count_clean_omissions_and_filter_raw_identities(self):
        from apps.core.trace_query.signoz.snapshot_window import query_snapshot_window
        row = self.payload['data'][0]
        other = deepcopy(self.detail)
        other['traceId'] = other['spans'][0]['trace_id'] = 'other-trace'
        other['spans'][0]['resource_attributes']['aivision.agent.id'] = 'other-agent'
        payload = self.snapshot_payload(rows=[row], raw=[other])
        payload['meta'].update(omittedAgentTraceCount=2, traceCount=4, projectedTraceCount=3, spanCount=4)
        self.client.get.return_value = payload
        window, meta = query_snapshot_window(self.provider, self.query)
        self.assertEqual([item['trace_id'] for item in window.rows], [row['trace_id']])
        self.assertEqual(meta['totalItems'], 1)

    def test_10k_sessions_keep_all_turns_when_one_trace_remains_raw(self):
        from apps.core.trace_query.canonical_sessions import CanonicalSessionWindow
        from apps.core.trace_query.signoz.snapshot_window import query_snapshot_window
        row = self.payload['data'][0]
        rows = [{**row, 'trace_id': f'{i:032x}', 'session_id': f'session-{i//3:05d}'} for i in range(30000)]
        raw = deepcopy(self.detail)
        raw['traceId'] = raw['spans'][0]['trace_id'] = rows.pop()['trace_id']
        raw['spans'][0]['session_id'] = 'session-09999'
        raw['spans'][0]['attributes']['langfuse.session.id'] = 'session-09999'
        self.client.get.return_value = self.snapshot_payload(rows=rows, raw=[raw])
        window, _ = query_snapshot_window(self.provider, self.query)
        result = CanonicalSessionWindow([{**item, '_traceTags': trace['tags']}
                                        for item, trace in zip(window.rows, window.traces)]).list({
            'session_semantics': 'canonical-trace-v1', 'session_id': 'session-09999'})
        self.assertEqual(result['meta']['windowTotalItems'], 10000)
        self.assertEqual(result['data'][0]['trace_count'], 3)

    def test_observation_filters_and_dynamic_sort_require_raw(self):
        from apps.core.trace_query.signoz.snapshot_window import needs_raw_observations
        self.assertTrue(needs_raw_observations({'filters': [{'field': 'attributes.gen_ai.tool.call.name', 'value': 'read'}]}))
        self.assertTrue(needs_raw_observations({'orderBy': 'attributes.custom_priority.desc'}))
        self.assertFalse(needs_raw_observations({'filters': [{'field': 'total_tokens', 'op': '>=', 'value': 5000}]}))

    @override_settings(AIVISION_QUERY_MODE='raw')
    def test_session_fallback_counts_sessions_and_keeps_all_matching_turns(self):
        from apps.core.tests.test_native_sessions import detail, usage
        from apps.core.trace_query.types import SessionListQuery
        rows = [detail('session', 0, attrs={**usage(4, 1, 5), 'langfuse.trace.input': 'needle', 'gen_ai.user.id': 'alice'}),
                detail('session', 1, attrs={**usage(6, 2, 8), 'gen_ai.user.id': 'bob'})]
        for item in rows:
            item['spans'][0]['resource_attributes']['aivision.space.id'] = 'space'
        self.client.get.return_value = self.snapshot_payload(raw=rows, mode=True)
        query = SessionListQuery(self.space, None, {**self.query.query_params, 'session_semantics': 'canonical-trace-v1',
            'username': 'alice', 'keyword': 'needle', 'searchType': 'input'})
        for field in ('fields',):
            query.query_params.pop(field, None)
        with patch.object(self.provider, 'collect_traces') as scan:
            result = self.provider.list_sessions(query)
        scan.assert_not_called()
        self.assertEqual(result['meta']['totalItems'], 1)
        self.assertEqual(result['data'][0]['trace_count'], 2)
        self.assertEqual(result['data'][0]['total_tokens'], 13)
        self.assertEqual(result['data'][0]['matched_trace_ids'], ['trace-0'])


class BatchProjectorTests(SimpleTestCase):
    setUp = read_model_tests.ReadModelTests.setUp
    snapshot = read_model_tests.ReadModelTests.snapshot

    def test_batching_reduces_two_sources_to_three_calls_and_keeps_conflicts(self):
        from apps.core.trace_query.signoz.read_model_projection import project_batch
        first, second = self.snapshot(), self.snapshot()
        for identifier, value in (('01', first), ('02', second)):
            value['trace_id'] = value['spans'][0]['trace_id'] = identifier
        client = Mock()
        client.get.side_effect = [
            {'supported': True, 'data': [{'trace_id': '01'}, {'trace_id': '02'}]},
            {'supported': True, 'contract': 'native-trace-source-batch-v1', 'data': [first, second]}]
        client.post.return_value = {'supported': True, 'contract': 'native-trace-publish-batch-v1', 'data': [
            {'trace_id': '01', 'supported': True, 'published': True, 'source_revision': 3},
            {'trace_id': '02', 'supported': False, 'reason': 'source_revision_changed'}]}
        result = project_batch(client, space_id='space', prices=[], limit=100)
        self.assertEqual((result['published'], result['conflicts']), (1, 1))
        self.assertEqual(client.get.call_count + client.post.call_count, 3)

    def test_older_backend_missing_batch_capabilities_falls_back_once(self):
        from apps.core.trace_query.signoz.read_model_projection import project_batch
        snapshot = self.snapshot()
        client = Mock()
        client.get.side_effect = [
            {'supported': True, 'data': [{'trace_id': snapshot['trace_id']}]},
            TraceQueryServiceError('missing', 3019), snapshot]
        client.post.side_effect = [TraceQueryServiceError('missing', 3019),
                                  {'supported': True, 'published': True, 'source_revision': 3}]
        result = project_batch(client, space_id='space', prices=[], limit=100)
        self.assertEqual(result['published'], 1)
        self.assertIs(client._projection_batch_sources, False)
        self.assertIs(client._projection_batch_publish, False)

    def test_wrong_receipt_order_and_database_errors_do_not_clear_dirty(self):
        from apps.core.trace_query.signoz.read_model_projection import project_batch
        client = Mock()
        client.get.side_effect = [
            {'supported': True, 'data': [{'trace_id': self.detail['traceId']}]},
            {'supported': True, 'contract': 'native-trace-source-batch-v1', 'data': [self.snapshot()]}]
        client.post.return_value = {'supported': True, 'contract': 'native-trace-publish-batch-v1', 'data': [
            {'trace_id': 'wrong', 'supported': True, 'published': True, 'source_revision': 3}]}
        with self.assertRaises(TraceQueryServiceError):
            project_batch(client, space_id='space', prices=[], limit=100)


class ManagedProjectorTests(SimpleTestCase):
    def target(self, key, client=None):
        from apps.core.trace_query.signoz.managed_projection import ProjectionTarget
        return ProjectionTarget(key=key, space_id=key, client=client or Mock())

    def test_new_routes_and_context_changes_are_discovered_each_iteration(self):
        from apps.core.trace_query.signoz.managed_projection import ManagedProjector
        first, second = self.target('first'), self.target('new-space')
        worker = ManagedProjector()
        with patch('apps.core.trace_query.signoz.managed_projection.discover_targets', side_effect=[[first], [first, second]]), \
             patch('apps.core.trace_query.signoz.managed_projection.project_batch', return_value={'afterTraceId': 'abc'}) as batch:
            worker.iteration()
            worker.iteration()
        self.assertEqual(batch.call_count, 3)
        self.assertEqual(batch.call_args_list[1].kwargs['after_trace_id'], 'abc')
        self.assertEqual(batch.call_args_list[2].kwargs['after_trace_id'], '')
        self.assertTrue(all(call.kwargs['backfill'] for call in batch.call_args_list))

    def test_one_unavailable_source_does_not_block_other_spaces_and_retries(self):
        from apps.core.trace_query.signoz.managed_projection import ManagedProjector
        worker = ManagedProjector()
        first, second = self.target('first'), self.target('second')
        with patch('apps.core.trace_query.signoz.managed_projection.discover_targets', return_value=[first, second]), \
             patch('apps.core.trace_query.signoz.managed_projection.project_batch', side_effect=[
                 TraceQueryServiceError('unavailable', 3035), {'afterTraceId': ''},
                 {'afterTraceId': ''}, {'afterTraceId': ''}]) as batch:
            result = worker.iteration()
            self.assertEqual([item['state'] for item in result], ['retry', 'ready'])
            worker.iteration()
        self.assertEqual(batch.call_count, 4)
        self.assertEqual(batch.call_args_list[2].kwargs['after_trace_id'], '')

    @override_settings(AIVISION_QUERY_MODE='raw')
    def test_raw_reader_emergency_does_not_stop_maintenance(self):
        from apps.core.trace_query.signoz.managed_projection import ManagedProjector
        with patch('apps.core.trace_query.signoz.managed_projection.discover_targets', return_value=[self.target('space')]), \
             patch('apps.core.trace_query.signoz.managed_projection.project_batch', return_value={'afterTraceId': ''}) as batch:
            ManagedProjector().iteration()
        batch.assert_called_once()

    def test_explicit_resume_needs_operator_secret_and_ordinary_iteration_never_resumes(self):
        from apps.core.trace_query.signoz.managed_projection import ManagedProjector
        worker = ManagedProjector()
        with self.assertRaises(TraceQueryServiceError):
            worker.resume_maintenance('')
        target = self.target('space')
        target.client.post.return_value = {'supported': True, 'resumed': True}
        with patch('apps.core.trace_query.signoz.managed_projection.discover_targets', return_value=[target]):
            self.assertEqual(worker.resume_maintenance('fixture-operator-key'), 1)
        self.assertEqual(target.client.post.call_args.kwargs['headers'], {'X-Signoz-Ingest-Key': 'fixture-operator-key'})


class ProjectionDiscoveryTests(TestCase):
    def setUp(self):
        from apps.core.models import AgentTypeConfig, DataSource, Space
        from apps.core.tests.helpers import create_local_user, create_test_agent
        self.user = create_local_user('projection-discovery@example.test')
        self.space = Space.objects.create(name='projection', type=2, creator=self.user)
        self.agent_type = AgentTypeConfig.objects.create(agent_name='projection', agent_product='public-product', status=1)
        self.agent = create_test_agent(space=self.space, agent_type=self.agent_type, agent_product='public-product', status=1)
        self.datasource = DataSource.objects.create(agent_type=self.agent_type, datasource_type='traces',
            source_system='signoz', enabled=1, config={'endpoint': 'http://private-query', 'api_key': 'fixture-key',
                                                     'agent_product_filter': 'stored-product'})

    def test_caption_context_upgrade_restarts_managed_cursor_for_the_same_route(self):
        import hashlib
        from apps.core.trace_query.signoz.managed_projection import ManagedProjector, discover_targets
        old_context = hashlib.sha256(
            b'{"prices":[],"semantics":"native-trace-summary-pricing-v9-publisher-price-supplements"}'
        ).hexdigest()
        with patch('apps.core.trace_query.signoz.managed_projection.projection_context', return_value=old_context):
            old_key = discover_targets()[0].key
        current = discover_targets()[0]
        self.assertNotEqual(current.key, old_key)
        worker = ManagedProjector(limit=7)
        worker.cursors[old_key] = 'old-context-cursor'
        with patch('apps.core.trace_query.signoz.managed_projection.project_batch', return_value={'afterTraceId': 'new-cursor'}) as batch:
            worker.iteration()
        self.assertEqual(batch.call_args.kwargs['after_trace_id'], '')
        self.assertEqual(batch.call_args.kwargs['limit'], 7)
        self.assertNotIn(old_key, worker.cursors)
        self.assertEqual(worker.cursors[current.key], 'new-cursor')

    def test_v10_media_context_restarts_managed_cursor_for_the_same_route(self):
        import hashlib
        from apps.core.trace_query.signoz.managed_projection import ManagedProjector, discover_targets
        old_context = hashlib.sha256(
            b'{"prices":[],"semantics":"native-trace-summary-pricing-v10-multimodal-preview-captions"}'
        ).hexdigest()
        with patch('apps.core.trace_query.signoz.managed_projection.projection_context', return_value=old_context):
            old_key = discover_targets()[0].key
        current = discover_targets()[0]
        self.assertNotEqual(current.key, old_key)
        worker = ManagedProjector(limit=7)
        worker.cursors[old_key] = 'old-context-cursor'
        with patch('apps.core.trace_query.signoz.managed_projection.project_batch', return_value={'afterTraceId': 'new-cursor'}) as batch:
            worker.iteration()
        self.assertEqual(batch.call_args.kwargs['after_trace_id'], '')
        self.assertNotIn(old_key, worker.cursors)
        self.assertEqual(worker.cursors[current.key], 'new-cursor')

    def test_enabled_routes_auto_discover_without_space_flags_and_refresh_credentials(self):
        from apps.core.trace_query.signoz.managed_projection import discover_targets
        target = discover_targets()[0]
        self.assertEqual(target.space_id, str(self.space.uuid))
        self.datasource.config['api_key'] = 'rotated-fixture-key'
        self.datasource.save(update_fields=['config'])
        self.assertNotEqual(discover_targets()[0].key, target.key)
        self.datasource.enabled = 0
        self.datasource.save(update_fields=['enabled'])
        self.assertEqual(discover_targets(), [])
