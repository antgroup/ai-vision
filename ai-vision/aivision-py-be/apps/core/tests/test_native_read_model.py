"""Read-model modes, contract validation and idempotent canonical projection."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from apps.core.tests.test_native_query import trace
from apps.core.trace_query.native_query import NativeTraceWindow
from apps.core.trace_query.signoz.provider import SignozTraceProvider
from apps.core.trace_query.signoz.read_model import CONTRACT, projection_context, query_read_model
from apps.core.trace_query.signoz.read_model_projection import ProjectionDeferred, build_projection, project_batch
from apps.core.trace_query.types import TraceListQuery, TraceQueryServiceError


class ReadModelTests(SimpleTestCase):
    def setUp(self):
        self.provider = SignozTraceProvider()
        self.space = SimpleNamespace(uuid='space', type=2)
        self.query = TraceListQuery(self.space, None, {'agent_uuid': 'agent-a', 'agent_product': 'public-product',
            'from': 1790980000000, 'to': 1790990000000, 'fields': ['trace_id', 'usage'], 'page': 1, 'limit': 20})
        self.detail = trace(1)
        self.detail['spans'][0]['resource_attributes'] = {'aivision.space.id': 'space', 'aivision.agent.id': 'agent-a'}
        window = NativeTraceWindow([self.detail], project_id='space', prices=[], include_previews=True)
        row = {**window.rows[0], '_traceTags': window.traces[0]['tags'], '_sceneValues': [], 'sourceRevision': 1}
        self.payload = {'supported': True, 'data': [row], 'facets': window.facets({}), 'meta': {
            'contract': CONTRACT, 'queryContract': CONTRACT, 'queryMode': 'read_model', 'complete': True,
            'environmentSemantics': 'root-span-resource-v1', 'userIdentitySemantics': 'trace-sdk-user-id-v1',
            'from': self.query.get_param('from'), 'to': self.query.get_param('to'), 'space_id': 'space',
            'agent_uuid': 'agent-a', 'agent_product': 'stored-product', 'user': '', 'page': 1, 'limit': 20,
            'totalItems': 1, 'totalPages': 1, 'projection_context': projection_context([])}}
        self.client = Mock()
        self.client.get.return_value = self.payload
        patcher = patch.object(self.provider, '_context', return_value=(self.client, {'agent_product_filter': 'stored-product'}))
        patcher.start()
        self.addCleanup(patcher.stop)

    def snapshot(self):
        return {'supported': True, 'contract': 'native-trace-source-v1', 'complete': True, 'trace_id': self.detail['traceId'],
                'source_revision': 3, 'maintenance_generation': 7, 'span_count': 1, 'spans': deepcopy(self.detail['spans']), 'scope_valid': True,
                'source_user_id': 'collector-subject', 'source_agent_product': 'stored-product'}

    def test_verified_page_uses_no_legacy_span_reads_and_keeps_previews(self):
        with override_settings(AIVISION_QUERY_MODE='auto'), patch.object(self.provider, '_query_traces_legacy') as legacy:
            result = self.provider.query_traces(self.query)
        legacy.assert_not_called()
        self.assertEqual(result['meta']['queryMode'], 'read_model')
        self.assertEqual(result['data'][0]['trace_id'], self.detail['traceId'])
        self.assertNotIn('_traceTags', result['data'][0])
        self.assertTrue(result['data'][0]['ioPreview']['complete'])
        sent = self.client.get.call_args.kwargs['params']
        self.assertEqual(sent['agent_product'], 'stored-product')
        self.assertEqual(sent['projection_context'], projection_context([]))
        self.assertNotIn('fields', sent)

    def test_projection_requires_and_preserves_shared_maintenance_generation(self):
        snapshot = self.snapshot()
        payload = build_projection(snapshot, space_id='space', prices=[])
        self.assertEqual(payload['maintenance_generation'], 7)
        for value in (None, True, 0, -1, '7'):
            with self.subTest(value=value):
                snapshot['maintenance_generation'] = value
                with self.assertRaises(TraceQueryServiceError):
                    build_projection(snapshot, space_id='space', prices=[])

    def test_generation_change_is_a_retryable_publication_conflict(self):
        client = Mock()
        client._projection_batch_sources = False
        client._projection_batch_publish = False
        snapshot = self.snapshot()
        client.get.side_effect = [{'supported': True, 'data': [{'trace_id': snapshot['trace_id']}]}, snapshot]
        client.post.return_value = {'supported': False, 'reason': 'maintenance_generation_changed'}
        result = project_batch(client, space_id='space', prices=[], limit=10)
        self.assertEqual(result['conflicts'], 1)
        self.assertEqual(result['published'], 0)

    @override_settings(AIVISION_QUERY_MODE='raw')
    def test_legacy_kill_switch_never_calls_new_api(self):
        with patch.object(self.provider, '_query_traces_legacy', return_value={'marker': True}) as legacy:
            self.assertEqual(self.provider.query_traces(self.query), {'marker': True})
        legacy.assert_called_once_with(self.query)
        self.client.get.assert_not_called()

    @override_settings(AIVISION_QUERY_MODE='auto')
    def test_explicit_stale_disabled_and_old_api_fall_back(self):
        for reason in ('read_model_disabled', 'coverage_incomplete', 'source_revision_changed', 'boundary_trace'):
            self.client.get.return_value = {'supported': False, 'reason': reason}
            with patch.object(self.provider, '_query_traces_legacy', return_value={'data': [], 'meta': {}}) as legacy:
                result = self.provider.query_traces(self.query)
            legacy.assert_called_once()
            self.assertEqual(result['meta']['readModel']['reason'], reason)
        self.client.get.side_effect = TraceQueryServiceError('not found', 3019)
        with patch.object(self.provider, '_query_traces_legacy', return_value={'data': [], 'meta': {}}):
            self.assertEqual(self.provider.query_traces(self.query)['meta']['readModel']['reason'], 'capability_unavailable')

    @override_settings(AIVISION_QUERY_MODE='auto')
    def test_malformed_success_and_auth_errors_never_hide_in_fallback(self):
        mutations = [lambda p: p['meta'].update(space_id='other'), lambda p: p['meta'].update(projection_context='0' * 64),
            lambda p: p['data'][0].update(sourceRevision=True), lambda p: p['data'][0].update(status_code=True),
            lambda p: p['data'][0].update({'resource.attributes.aivision.agent.id': 'other'}),
            lambda p: p['data'][0].pop('_traceTags'), lambda p: p['data'][0].pop('usage'),
            lambda p: p['data'][0].update(end_time_unix_nano='not-a-time'), lambda p: p['data'].clear(),
            lambda p: p['data'][0]['ioPreview'].update(complete=False), lambda p: p['meta'].update(totalItems=2),
            lambda p: p['facets']['env'][0].update(count=2), lambda p: p['data'][0].update(start_time_unix_nano='not-a-time')]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                payload = deepcopy(self.payload)
                mutation(payload)
                self.client.get.return_value = payload
                with patch.object(self.provider, '_query_traces_legacy') as legacy, self.assertRaises(TraceQueryServiceError) as error:
                    self.provider.query_traces(self.query)
                self.assertEqual(error.exception.code, 3035)
                legacy.assert_not_called()
        self.client.get.side_effect = TraceQueryServiceError('permission denied', 3013)
        with patch.object(self.provider, '_query_traces_legacy') as legacy, self.assertRaises(TraceQueryServiceError):
            self.provider.query_traces(self.query)
        legacy.assert_not_called()

    def test_metadata_page_accepts_bounded_storage_row_without_full_io_aliases(self):
        from apps.core.trace_query.types import TraceListQuery
        query = TraceListQuery(self.space, None, {**self.query.query_params, 'fields': ['trace_id', 'usage']})
        self.client.get.return_value = deepcopy(self.payload)
        for field in ('attributes.gen_ai.input.messages', 'attributes.gen_ai.output.messages'):
            self.client.get.return_value['data'][0].pop(field)
        result = self.provider.query_traces(query)
        self.assertEqual(result['data'][0]['trace_id'], self.detail['traceId'])
        self.assertEqual(result['data'][0]['usage']['total'], 12)
        self.assertEqual(self.client.get.call_args.kwargs['params']['metadata_only'], 'true')
        self.assertTrue(result['data'][0]['ioPreview']['complete'])

    def test_content_predicates_keep_full_evidence_for_local_revalidation(self):
        window = NativeTraceWindow([self.detail], project_id='space', prices=[], include_previews=True)
        payload = deepcopy(self.payload)
        payload['data'][0].update(window.rows[0])
        payload['data'][0]['attributes.gen_ai.input.messages'] = 'needle input'
        payload['data'][0]['attributes.gen_ai.output.messages'] = 'needle output'
        def storage(_path, *, params):
            result = deepcopy(payload)
            if params.get('metadata_only') == 'true':
                for key in ('attributes.gen_ai.input.messages', 'attributes.gen_ai.output.messages'):
                    result['data'][0][key] = None
            return result
        self.client.get.side_effect = storage
        for predicate in ({'keyword': 'needle', 'searchType': 'input'},
                          {'keyword': 'needle', 'search_type': 'output'}, {'input_keyword': 'needle'},
                          {'filters': [{'field': 'attributes.gen_ai.input.messages', 'op': 'LIKE', 'value': '%needle%'}]}):
            query = TraceListQuery(self.space, None, {**self.query.query_params, 'fields': ['trace_id', 'usage'], **predicate})
            with self.subTest(predicate=predicate):
                result, _ = query_read_model(self.provider, query)
                self.assertEqual(result['data'][0]['trace_id'], self.detail['traceId'])
                self.assertNotEqual(self.client.get.call_args.kwargs['params'].get('metadata_only'), 'true')

    def test_bounded_previews_are_standard(self):
        result = self.provider.query_traces(self.query)
        self.assertTrue(result['data'][0]['ioPreview']['complete'])
        self.assertTrue(result['meta']['ioPreviewComplete'])

    def test_keyword_normalization_is_python_literal_not_sql_wildcards(self):
        query = TraceListQuery(self.space, None, {**self.query.query_params, 'searchType': 'input', 'keyword': 'İ_%\\'})
        self.client.get.return_value = {'supported': False, 'reason': 'test'}
        query_read_model(self.provider, query)
        self.assertEqual(self.client.get.call_args.kwargs['params']['keyword'], 'İ_%\\'.lower())

    @override_settings(AIVISION_QUERY_MODE='auto')
    def test_conflicting_trace_aliases_keep_legacy_conjunction(self):
        query = TraceListQuery(self.space, None, {**self.query.query_params,
            'trace_id': self.detail['traceId'], 'traceId': 'another-trace'})
        expected = NativeTraceWindow([self.detail], project_id='space', prices=[], include_previews=True).list(query.query_params)
        with patch.object(self.provider, '_query_traces_legacy', return_value=expected) as legacy:
            result = self.provider.query_traces(query)
        self.assertEqual(result['meta']['totalItems'], 0)
        self.assertEqual(result['meta']['readModel']['reason'], 'unsupported_query')
        legacy.assert_called_once()
        self.client.get.assert_not_called()

    def test_only_exact_authorized_agent_equality_is_removed_for_old_page_clients(self):
        from apps.core.trace_query.signoz.read_model import _prepared_params
        duplicate = {'field': 'resource.attributes.aivision.agent.id', 'op': '=', 'value': 'agent-a'}
        query = TraceListQuery(self.space, None, {**self.query.query_params, 'filters': [duplicate]})
        prepared = _prepared_params(query)
        self.assertIsNotNone(prepared)
        self.assertEqual(prepared['filters'], [])
        for predicate in ({**duplicate, 'value': 'agent-b'}, {**duplicate, 'op': '!='},
                          {**duplicate, 'op': 'in', 'value': ['agent-a']}):
            params = {**self.query.query_params, 'filters': [predicate]}
            self.assertIsNone(_prepared_params(TraceListQuery(self.space, None, params)))

    def test_dynamic_fields_any_observation_filters_and_wide_integer_keep_fallback(self):
        for extra in ({'fields': ['attributes.custom']}, {'filters': [{'field': 'resource.attributes.aivision.agent.id', 'value': 'agent-b'}]},
                      {'filters': [{'field': 'span_count', 'op': '<=', 'value': 2**63}]}):
            result, diagnostic = query_read_model(self.provider, TraceListQuery(self.space, None, {**self.query.query_params, **extra}))
            self.assertIsNone(result)
            self.assertEqual(diagnostic['state'], 'fallback')
        self.client.get.assert_not_called()

    def test_projector_reuses_canonical_identity_usage_and_full_search(self):
        snapshot = self.snapshot()
        snapshot['spans'][0]['attributes'].update({'langfuse.trace.input': '中' * 2048 + 'END', 'gen_ai.user.id': None})
        payload = build_projection(snapshot, space_id='space', prices=[])
        self.assertEqual(payload['username'], None)
        self.assertEqual(payload['user_id'], 'collector-subject')
        self.assertEqual(payload['input_text'], ('中' * 2048 + 'END').lower())
        self.assertLessEqual(len(payload['preview']['input']), 1024)
        self.assertEqual(payload['row']['coverage'], NativeTraceWindow([{'traceId': snapshot['trace_id'], 'spans': snapshot['spans']}], project_id='space').rows[0]['coverage'])
        self.assertEqual(payload['source_revision'], 3)

    def test_large_root_content_keeps_metadata_and_preview_without_claiming_full_content(self):
        snapshot = self.snapshot()
        snapshot['spans'][0]['attributes']['langfuse.trace.input'] = 'x' * (17 << 20) + 'END'
        payload = build_projection(snapshot, space_id='space', prices=[])
        self.assertFalse(payload['row']['_contentComplete'])
        self.assertIsNone(payload['row']['attributes.gen_ai.input.messages'])
        self.assertLessEqual(len(payload['input_text']), 1024)
        self.assertTrue(payload['preview']['inputTruncated'])
        self.assertEqual(payload['node_count'], 1)
        self.assertIn('_metrics', payload['row'])
        self.assertIn('_operation_previews', payload['row'])

    def test_giant_custom_attribute_cannot_block_metadata_projection(self):
        snapshot = self.snapshot()
        snapshot['spans'][0]['attributes']['large.custom'] = 'x' * (17 << 20)
        payload = build_projection(snapshot, space_id='space', prices=[])
        self.assertEqual(payload['node_count'], 1)
        self.assertFalse(payload['row']['_contentComplete'])
        self.assertIsNone(payload['row']['attributes.large.custom'])
        self.assertIn('_statistics', payload['row'])

    def test_many_operation_previews_cannot_block_complete_numeric_projection(self):
        snapshot = self.snapshot()
        root = snapshot['spans'][0]
        spans = []
        for index in range(7500):
            span = deepcopy(root)
            span['span_id'] = f'operation-{index:05}'
            if index:
                span['parent_span_id'] = 'operation-00000'
            start = 1790989323000000000 + index * 1000000
            span.update(start_time_unix_nano=str(start), end_time_unix_nano=str(start + 100000000))
            span['attributes'].update({'gen_ai.input.messages': 'x' * 1024,
                                       'gen_ai.output.messages': 'y' * 1024})
            spans.append(span)
        snapshot.update(spans=spans, span_count=len(spans))
        payload = build_projection(snapshot, space_id='space', prices=[])
        previews = payload['row']['_operation_previews']
        self.assertEqual(set(previews), {f'operation-{index:05}' for index in range(7300, 7500)})
        self.assertEqual(len(payload['row']['_metrics']['observations']), 7500)
        self.assertEqual(len(payload['row']['_statistics']['calls']), 7500)
        self.assertEqual(payload['node_count'], 7500)

    def test_long_child_name_cannot_make_optional_preview_reject_metadata_publication(self):
        snapshot = self.snapshot()
        child = deepcopy(snapshot['spans'][0])
        child.update(span_id='long-child', parent_span_id=snapshot['spans'][0]['span_id'], name='x' * 513)
        snapshot['spans'].append(child)
        snapshot['span_count'] = 2
        payload = build_projection(snapshot, space_id='space', prices=[])
        self.assertNotIn('long-child', payload['row']['_operation_previews'])
        self.assertEqual(payload['name'], 'call')
        self.assertEqual(len(payload['row']['_statistics']['calls']), 2)

    def test_multibyte_child_name_within_preview_character_limit_stays_available(self):
        snapshot = self.snapshot()
        child = deepcopy(snapshot['spans'][0])
        child.update(span_id='unicode-child', parent_span_id=snapshot['spans'][0]['span_id'], name='模型' * 200)
        snapshot['spans'].append(child)
        snapshot['span_count'] = 2
        payload = build_projection(snapshot, space_id='space', prices=[])
        self.assertEqual(payload['row']['_operation_previews']['unicode-child']['name'], child['name'])

    def test_old_storage_cannot_publish_partial_content_without_capability_proof(self):
        client = Mock()
        client._projection_batch_sources = False
        snapshot = self.snapshot()
        snapshot['spans'][0]['attributes']['langfuse.trace.input'] = 'x' * (3 << 20)
        client.get.side_effect = [{'supported': True, 'data': [{'trace_id': snapshot['trace_id']}]},
                                  snapshot, TraceQueryServiceError('old endpoint', 3019)]
        result = project_batch(client, space_id='space', prices=[], limit=10)
        self.assertEqual(result['published'], 0)
        self.assertEqual(result['deferred'], 1)
        client.post.assert_not_called()

    def test_oversized_requested_metadata_is_deferred_instead_of_published_as_null(self):
        snapshot = self.snapshot()
        snapshot['spans'][0]['attributes']['gen_ai.tool.call.name'] = 'x' * (2 << 20)
        with self.assertRaises(ProjectionDeferred):
            build_projection(snapshot, space_id='space', prices=[])

    def test_snapshot_size_decline_retries_paged_source_before_deferring(self):
        from apps.core.trace_query.signoz.read_model_projection import _snapshot_chunk
        client = Mock()
        client.get.return_value = {'supported': True, 'contract': 'native-trace-source-batch-v1',
                                  'data': [{'trace_id': 'abc', 'supported': False, 'reason': 'source_snapshot_payload_limit'}]}
        with patch('apps.core.trace_query.signoz.source_pages.read_source_pages', return_value=self.snapshot()) as pages:
            self.assertEqual(_snapshot_chunk(client, 'space', ['abc']), [self.snapshot()])
        pages.assert_called_once_with(client, space_id='space', trace_id='abc')

    def test_projector_refuses_partial_and_mixed_sources_without_false_ready(self):
        snapshot = self.snapshot()
        for key, value in (('complete', False), ('scope_valid', False)):
            bad = deepcopy(snapshot)
            bad[key] = value
            with self.assertRaises(ProjectionDeferred):
                build_projection(bad, space_id='space', prices=[])
        bad = deepcopy(snapshot)
        child = deepcopy(bad['spans'][0])
        child['span_id'] = 'other'
        child['resource_attributes']['aivision.agent.id'] = 'other-agent'
        bad['spans'].append(child)
        bad['span_count'] = 2
        with self.assertRaises(ProjectionDeferred):
            build_projection(bad, space_id='space', prices=[])
        snapshot['span_count'] = 2
        with self.assertRaises(TraceQueryServiceError):
            build_projection(snapshot, space_id='space', prices=[])

    def test_publication_conflict_keeps_dirty_and_advances_batch(self):
        snapshot = self.snapshot()
        client = Mock()
        client._projection_batch_sources = False
        client._projection_batch_publish = False
        client.get.side_effect = [{'supported': True, 'data': [{'trace_id': snapshot['trace_id'], 'source_revision': 3}]}, snapshot]
        client.post.return_value = {'supported': False, 'reason': 'source_revision_changed'}
        result = project_batch(client, space_id='space', prices=[], limit=1)
        self.assertEqual(result['conflicts'], 1)
        self.assertEqual(result['published'], 0)
        self.assertEqual(result['afterTraceId'], snapshot['trace_id'])

    def test_old_v9_caption_previews_cannot_be_accepted_as_current(self):
        import hashlib
        old_context = hashlib.sha256(
            b'{"prices":[],"semantics":"native-trace-summary-pricing-v9-publisher-price-supplements"}'
        ).hexdigest()
        self.payload['meta']['projection_context'] = old_context
        with self.assertRaises(TraceQueryServiceError) as error:
            query_read_model(self.provider, self.query)
        self.assertEqual(error.exception.code, 3035)

    def test_caption_upgrade_enrolls_old_context_for_bounded_reprojection(self):
        import hashlib
        old_context = hashlib.sha256(
            b'{"prices":[],"semantics":"native-trace-summary-pricing-v9-publisher-price-supplements"}'
        ).hexdigest()
        client = Mock()
        client.get.return_value = {'supported': True, 'data': []}
        project_batch(client, space_id='space', prices=[], limit=7)
        sent = client.get.call_args.kwargs['params']
        self.assertNotEqual(sent['projection_context'], old_context)
        self.assertEqual(sent['limit'], 7)
        self.assertEqual(sent['projection_context'], projection_context([]))

    def test_caption_projection_preserves_original_io_and_full_search_body(self):
        import json
        snapshot = self.snapshot()
        value = [{'role': 'user', 'content': [
            {'type': 'image_url', 'image_url': 'https://private.invalid/image'},
            {'type': 'text', 'text': 'caption before'},
            {'type': 'aivision_attachment', 'id': 'private-media-reference'},
            {'type': 'output_text', 'text': 'caption after needle-at-end'},
        ]}]
        raw = json.dumps(value, ensure_ascii=False)
        snapshot['spans'][0]['attributes']['langfuse.trace.input'] = raw
        before = deepcopy(snapshot)
        payload = build_projection(snapshot, space_id='space', prices=[])
        self.assertEqual(json.loads(payload['preview']['input'])['content'], 'caption before\ncaption after needle-at-end')
        self.assertEqual(payload['row']['attributes.gen_ai.input.messages'], raw)
        self.assertEqual(payload['input_text'], raw.lower())
        self.assertEqual(snapshot, before)

    def test_projection_filters_direct_media_in_trace_and_operation_previews_only(self):
        import json
        from apps.core.trace_query.media_visibility import redact_trace_media
        url = "https://media.invalid/private.png?signature=fixture-secret"
        attachment_id = "a" * 64
        value = [{"type": "image_url", "image_url": {"url": url}},
                 {"type": "aivision_attachment", "id": attachment_id, "mimeType": "image/png"},
                 {"type": "text", "text": "caption before " * 1024}]
        raw = json.dumps(value)
        snapshot = self.snapshot()
        snapshot["spans"][0]["attributes"].update({
            "langfuse.trace.input": raw, "langfuse.trace.output": raw,
            "langfuse.observation.input": raw, "langfuse.observation.output": raw,
        })
        before = deepcopy(snapshot)
        payload = build_projection(snapshot, space_id="space", prices=[])
        previews = [payload["preview"], *payload["row"]["_operation_previews"].values()]
        self.assertEqual(len(previews), 2)
        for preview in previews:
            for field in ("input", "output"):
                self.assertLessEqual(len(preview[field]), 1024)
                self.assertNotIn(url, preview[field])
                self.assertNotIn(attachment_id, preview[field])
            self.assertNotIn(url, json.dumps(redact_trace_media(preview)))
        for field in ("input", "output"):
            self.assertEqual(payload["row"][f"attributes.gen_ai.{field}.messages"], raw)
            self.assertEqual(payload[field + "_text"], raw.lower())
        self.assertEqual(snapshot, before)

    def test_v10_media_previews_are_rejected_and_enrolled_for_reprojection(self):
        import hashlib
        old_context = hashlib.sha256(
            b'{"prices":[],"semantics":"native-trace-summary-pricing-v10-multimodal-preview-captions"}'
        ).hexdigest()
        self.payload["meta"]["projection_context"] = old_context
        with self.assertRaises(TraceQueryServiceError) as error:
            query_read_model(self.provider, self.query)
        self.assertEqual(error.exception.code, 3035)
        client = Mock()
        client.get.return_value = {"supported": True, "data": []}
        project_batch(client, space_id="space", prices=[], limit=7)
        sent = client.get.call_args.kwargs["params"]
        self.assertNotEqual(sent["projection_context"], old_context)
        self.assertEqual(sent["projection_context"], projection_context([]))
        self.assertEqual(sent["limit"], 7)

    def test_arbitrary_json_part_type_cannot_strand_projection(self):
        import json
        for kind in ([], {"const": "object"}):
            snapshot = self.snapshot()
            value = {"role": "tool", "content": [{"type": kind, "value": 0}]}
            snapshot['spans'][0]['attributes']['langfuse.trace.output'] = json.dumps(value)
            with self.subTest(kind=kind):
                payload = build_projection(snapshot, space_id='space', prices=[])
                self.assertEqual(payload['row']['attributes.gen_ai.output.messages'], json.dumps(value))
                self.assertIn('value', payload['preview']['output'])

    def test_projection_context_changes_with_price_catalog(self):
        self.assertEqual(projection_context([]), projection_context([]))
        self.assertNotEqual(projection_context([]), projection_context([{'model': 'fixture', 'input': 1}]))

    def test_old_subscription_projection_cannot_hide_equivalent_failure_reasons(self):
        import hashlib
        # The old projection could not carry separate equivalent failure
        # evidence; a page published under that context must be rejected.
        old_context = hashlib.sha256(
            b'{"prices":[],"semantics":"native-trace-summary-pricing-v3-subscription"}'
        ).hexdigest()
        self.payload['meta']['projection_context'] = old_context
        with self.assertRaises(TraceQueryServiceError) as error:
            query_read_model(self.provider, self.query)
        self.assertEqual(error.exception.code, 3035)

    def test_old_projection_cannot_hide_successful_equivalent_price_sources(self):
        import hashlib
        old_context = hashlib.sha256(
            b'{"prices":[],"semantics":"native-trace-summary-pricing-v4-subscription-equivalent-reasons"}'
        ).hexdigest()
        self.payload['meta']['projection_context'] = old_context
        with self.assertRaises(TraceQueryServiceError) as error:
            query_read_model(self.provider, self.query)
        self.assertEqual(error.exception.code, 3035)

    def test_status_aliases_are_normalized_before_storage_validation(self):
        for value in ('OK', 'ERROR', 'UNSET'):
            self.client.get.return_value = {'supported': False, 'reason': 'test'}
            query_read_model(self.provider, TraceListQuery(self.space, None, {**self.query.query_params,
                'filters': [{'field': 'status_code', 'op': 'eq', 'value': value}]}))
            import json
            self.assertEqual(json.loads(self.client.get.call_args.kwargs['params']['filters'])[0]['value'],
                             {'OK': 1, 'ERROR': 2, 'UNSET': 0}[value])

    def test_dynamic_order_keeps_the_complete_window_path(self):
        result, diagnostic = query_read_model(self.provider, TraceListQuery(self.space, None,
            {**self.query.query_params, 'orderBy': 'attributes.custom_priority.desc'}))
        self.assertIsNone(result)
        self.assertEqual(diagnostic['state'], 'fallback')
        self.client.get.assert_not_called()

    def test_token_order_requests_nulls_last_and_preserves_older_server_fallback(self):
        self.client.get.return_value = {'supported': False, 'reason': 'unsupported_order'}
        minimum = {'field': 'total_tokens', 'op': '>=', 'value': 100}
        params = {**self.query.query_params, 'filters': [minimum]}
        query_read_model(self.provider, TraceListQuery(self.space, None, {**params, 'orderBy': 'duration_ms.desc'}))
        self.client.get.assert_called_once()
        self.assertEqual(self.client.get.call_args.kwargs['params']['orderBy'], 'duration_ms.desc')
        self.assertNotIn('token_nulls_last', self.client.get.call_args.kwargs['params'])
        for field in ('total_tokens', 'attributes.gen_ai.usage.total_tokens'):
            for direction in ('asc', 'desc'):
                with self.subTest(field=field, direction=direction):
                    order = f'{field}.{direction}'
                    self.client.reset_mock()
                    result, diagnostic = query_read_model(self.provider, TraceListQuery(
                        self.space, None, {**params, 'order_by': order},
                    ))
                    self.assertIsNone(result)
                    self.assertEqual(diagnostic, {'state': 'fallback', 'reason': 'unsupported_order'})
                    self.client.get.assert_called_once()
                    sent = self.client.get.call_args.kwargs['params']
                    self.assertEqual(sent['orderBy'], order)
                    self.assertEqual(sent['token_nulls_last'], 'true')
        query_read_model(self.provider, TraceListQuery(self.space, None, {**params, 'orderBy': '-total_tokens.asc'}))
        self.assertEqual(self.client.get.call_args.kwargs['params']['orderBy'], 'total_tokens.desc')

    def test_token_order_accepts_verified_storage_page_and_requires_null_order_receipt(self):
        details = [trace(1, usage=False), trace(2), trace(3)]
        details[1]['spans'][0]['attributes'].update({
            'gen_ai.usage.input_tokens': 0, 'gen_ai.usage.output_tokens': 0, 'gen_ai.usage.total_tokens': 0,
        })
        for detail in details:
            detail['spans'][0]['resource_attributes'] = {'aivision.space.id': 'space', 'aivision.agent.id': 'agent-a'}
        window = NativeTraceWindow(details, project_id='space', prices=[], include_previews=True)
        rows = {row['trace_id']: {**row, '_traceTags': [], '_sceneValues': [], 'sourceRevision': 1}
                for row in window.rows}
        self.assertEqual([row['usage']['total'] for row in rows.values()], [None, 0, 12])

        for direction, expected in (
            ('asc', ['trace-0002', 'trace-0003', 'trace-0001']),
            ('desc', ['trace-0003', 'trace-0002', 'trace-0001']),
        ):
            with self.subTest(direction=direction):
                query = TraceListQuery(self.space, None, {**self.query.query_params,
                    'orderBy': f'total_tokens.{direction}'})
                payload = deepcopy(self.payload)
                payload.update(data=[rows[identifier] for identifier in expected], facets=window.facets({}))
                payload['meta'].update(totalItems=3, tokenNullsLast=True)
                self.client.get.return_value = payload
                result, diagnostic = query_read_model(self.provider, query)
                self.assertEqual(diagnostic['state'], 'ready')
                self.assertEqual([row['trace_id'] for row in result['data']], expected)
                for receipt in (None, False, 'true'):
                    payload['meta']['tokenNullsLast'] = receipt
                    with self.assertRaises(TraceQueryServiceError) as error:
                        query_read_model(self.provider, query)
                    self.assertEqual(error.exception.code, 3035)

    def test_unprojectable_resource_scope_does_not_starve_later_dirty_traces(self):
        first, second = self.snapshot(), self.snapshot()
        first["trace_id"] = "01"
        first["spans"][0]["trace_id"] = "01"
        first["spans"][0]["resource_attributes"]["aivision.space.id"] = "other"
        second["trace_id"] = "02"
        second["spans"][0]["trace_id"] = "02"
        client = Mock()
        client._projection_batch_sources = False
        client._projection_batch_publish = False
        client.get.side_effect = [{"supported": True, "data": [{"trace_id": "01"}, {"trace_id": "02"}]}, first, second]
        client.post.return_value = {"supported": True, "published": True, "source_revision": 3}
        result = project_batch(client, space_id="space", prices=[], limit=10)
        self.assertEqual(result["deferred"], 1)
        self.assertEqual(result["published"], 1)
        self.assertEqual(client.get.call_count, 3)

    def test_oversized_sdk_dimension_is_deferred_and_later_trace_publishes(self):
        first, second = self.snapshot(), self.snapshot()
        for value, identifier in ((first, '01'), (second, '02')):
            value['trace_id'] = value['spans'][0]['trace_id'] = identifier
        first['spans'][0]['attributes']['gen_ai.user.id'] = 'long-user-' * 100
        client = Mock()
        client._projection_batch_sources = False
        client._projection_batch_publish = False
        client.get.side_effect = [{'supported': True, 'data': [{'trace_id': '01'}, {'trace_id': '02'}]}, first, second]
        client.post.return_value = {'supported': True, 'published': True, 'source_revision': 3}
        result = project_batch(client, space_id='space', prices=[], limit=10)
        self.assertEqual(result['deferred'], 1)
        self.assertEqual(result['published'], 1)
        self.assertEqual(client.post.call_count, 1)

    @override_settings(AIVISION_QUERY_MODE='auto')
    def test_facet_fallback_does_not_run_an_unused_legacy_list(self):
        self.client.get.return_value = {'supported': False, 'reason': 'read_model_disabled'}
        with patch.object(self.provider, '_query_traces_legacy') as listing, \
             patch.object(self.provider, '_list_facets_legacy', return_value={'data': {'env': []}, 'meta': {}}) as facets:
            result = self.provider.list_facets(self.query)
        listing.assert_not_called()
        facets.assert_called_once()
        self.assertEqual(result['data'], {'env': []})

    @override_settings(AIVISION_QUERY_MODE='auto')
    def test_facet_metadata_keeps_window_count_without_witness_pagination(self):
        self.payload['meta']['windowTotalItems'] = 7
        self.payload['meta']['limit'] = 1
        result = self.provider.list_facets(self.query)
        self.assertEqual(result['meta']['totalItems'], 7)
        for key in ('page', 'limit', 'totalPages', 'ioPreviewComplete'):
            self.assertNotIn(key, result['meta'])
        self.assertEqual(result['data'], self.payload['facets'])
