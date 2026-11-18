"""Session detail/public pages hydrate selected complete Traces, not all content."""
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from apps.core.trace_query.langfuse.native import NativeLangfuseService, project_trace, select, trace_wire
from apps.core.trace_query.metrics_projection import project_metrics, ROW_FIELDS
from apps.core.trace_query.native_query import NativeTraceWindow
from apps.core.trace_query.signoz.provider import SignozTraceProvider
from apps.core.trace_query.types import SessionDetailQuery, SessionUsageBatchQuery, TraceQueryServiceError


@override_settings(AIVISION_QUERY_MODE='auto', AIVISION_NATIVE_TRACE_HISTORY_START='1970-01-01T00:00:01Z',
                   AIVISION_NATIVE_TRACE_FUTURE_SKEW_SECONDS=0)
class BoundedSelectedTraceTests(SimpleTestCase):
    def setUp(self):
        self.provider = SignozTraceProvider()
        self.space = SimpleNamespace(uuid='space', type=2)
        self.agent = SimpleNamespace(uuid='agent', agent_product='langfuse')
        self.params = {'from': 1000, 'to': 5000, 'agent_uuid': 'agent', 'agent_product': 'langfuse'}
        self.details = []
        for index in range(4):
            root = {'trace_id': f'trace-{index}', 'span_id': f'root-{index}', 'name': f'root-{index}',
                    'start_time_unix_nano': str((1000 + index) * 1000000),
                    'end_time_unix_nano': str((1001 + index) * 1000000), 'session_id': 'physical-other',
                    'resource': {'aivision.space.id': 'space', 'aivision.agent.id': 'agent'},
                    'attributes': {'langfuse.observation.type': 'span', 'langfuse.session.id': 'wanted' if index == 0 else 'other',
                                   'langfuse.observation.input': '{"text":"中文😀"}'}}
            child = deepcopy(root)
            child.update(span_id=f'child-{index}', parent_span_id=root['span_id'])
            child['attributes'].update({'langfuse.observation.type': 'generation',
                                        'langfuse.session.id': 'wanted' if index < 2 else 'other',
                                        'gen_ai.usage.input_tokens': 11, 'gen_ai.usage.output_tokens': 7})
            self.details.append({'traceId': root['trace_id'], 'spans': [root, child]})
        self.calls = []
        self.mode = None
        self.endpoint, self.api_key = 'https://fixture.invalid', 'fixture'
        self.enterContext(patch.object(self.provider, '_context', return_value=(self, {})))
        self.enterContext(patch('apps.core.trace_query.langfuse.native.time.time_ns', return_value=5000000000))

    def get(self, path, *, params):
        self.calls.append((path, deepcopy(params)))
        if path.endswith('/metrics-window'):
            window = NativeTraceWindow(deepcopy(self.details), project_id='space', prices=[], include_previews=True)
            rows = []
            for row, trace in zip(window.rows, window.traces):
                facts = project_metrics(trace)
                if self.mode == 'old_facts':
                    facts.pop('sessionIds', None)
                rows.append({key: value for key, value in {
                    **row, '_traceTags': trace['tags'], '_sceneValues': [], '_metrics': facts,
                    'sourceRevision': 1}.items() if key in ROW_FIELDS or key == 'sourceRevision'})
            return {'supported': True, 'complete': True, 'contract': 'native-trace-metrics-window-v1',
                    'rows': rows, 'raw': [], 'meta': {**params, 'raw': False, 'representation': 'metrics-v1',
                    'maintenance_generation': 1, 'omittedAgentTraceCount': 0, 'traceCount': 4,
                    'projectedTraceCount': 4, 'rawTraceCount': 0, 'spanCount': 8, 'rawSpanCount': 0}}
        if path.endswith('/batch-snapshot'):
            ids = json.loads(params['trace_ids'])
            sources = {detail['traceId']: detail for detail in self.details}
            data = [{'supported': True, 'complete': True, 'contract': 'native-trace-source-v1',
                     'trace_id': identifier, 'source_revision': 2 if self.mode == 'revision' else 1,
                     'maintenance_generation': 2 if self.mode == 'generation' else 1,
                     'scope_valid': True, 'source_user_id': 'owner', 'source_agent_product': 'langfuse',
                     'span_count': 2, 'spans': deepcopy(sources[identifier]['spans'])} for identifier in ids]
            if self.mode == 'foreign':
                data[0]['spans'][0]['resource']['aivision.agent.id'] = 'foreign'
            if self.mode == 'missing_span':
                data[0]['spans'].pop()
            return {'supported': True, 'contract': 'native-trace-source-batch-v1', 'data': data}
        raise TraceQueryServiceError('unselected full content exceeds budget', 3036)

    def source_ids(self):
        return [identifier for path, params in self.calls if path.endswith('/batch-snapshot')
                for identifier in json.loads(params['trace_ids'])]

    def test_legacy_session_keeps_child_membership_and_ignores_physical_sid(self):
        result = self.provider.get_session_detail(SessionDetailQuery(self.space, None, self.params, 'wanted'))
        self.assertEqual(result['traceIds'], ['trace-0', 'trace-1'])
        self.assertEqual(result['traceCount'], 2)
        self.assertEqual(self.source_ids(), ['trace-0', 'trace-1'])

    def test_canonical_session_usage_and_window_exclude_child_only_membership(self):
        params = {**self.params, 'session_semantics': 'canonical-trace-v1'}
        result = self.provider.get_session_detail(SessionDetailQuery(self.space, None, params, 'wanted'))
        self.assertEqual(result['traceIds'], ['trace-0'])
        self.assertEqual(result['timeRange'], {'from': '1000', 'to': '5000'})
        usage = self.provider.get_session_usage_batch(SessionUsageBatchQuery(self.space, None, params, ['wanted']))
        self.assertEqual(usage['data'][0]['totalTokens'], 18)

    def test_session_timestamp_aliases_keep_same_frozen_selection(self):
        params = {'fromTimestamp': '1970-01-01T00:00:01Z', 'toTimestamp': '1970-01-01T00:00:05Z',
                  'agent_uuid': 'agent', 'agent_product': 'langfuse'}
        actual = self.provider.get_session_detail(SessionDetailQuery(self.space, None, params, 'wanted'))
        expected = self.provider.get_session_detail(SessionDetailQuery(self.space, None, self.params, 'wanted'))
        self.assertEqual(actual, expected)

    def test_unknown_session_is_empty_without_source_fetch(self):
        with self.assertRaises(TraceQueryServiceError) as error:
            self.provider.get_session_detail(SessionDetailQuery(self.space, None, self.params, 'missing'))
        self.assertEqual(error.exception.code, 3019)
        self.assertEqual(self.source_ids(), [])

    def test_old_facts_and_changed_source_keep_bounded_fallback(self):
        for mode in ('old_facts', 'revision', 'generation'):
            self.mode = mode
            with self.subTest(mode=mode), self.assertRaises(TraceQueryServiceError) as error:
                self.provider.get_session_detail(SessionDetailQuery(self.space, None, self.params, 'wanted'))
            self.assertEqual(error.exception.code, 3036)

    def test_malformed_or_foreign_source_never_returns_selected_data(self):
        for mode in ('foreign', 'missing_span'):
            self.mode = mode
            with self.subTest(mode=mode), self.assertRaises(TraceQueryServiceError) as error:
                self.provider.get_session_detail(SessionDetailQuery(self.space, None, self.params, 'wanted'))
            self.assertEqual(error.exception.code, 3019 if mode == 'foreign' else 3035)

    def test_public_pages_match_full_oracle_and_hydrate_only_selected_traces(self):
        service = NativeLangfuseService(space=self.space, agent=self.agent, provider=self.provider, prices=[])
        full = [project_trace(detail, project_id='agent', prices=[]) for detail in self.details]
        for params in ({'page': 2, 'limit': 2}, {'limit': 1, 'orderBy': 'id.asc'},
                       {'limit': 3, 'fromTimestamp': '1970-01-01T00:00:01.002Z',
                        'toTimestamp': '1970-01-01T00:00:01.003Z'},
                       {'page': 3, 'limit': 2, 'fields': 'core'}):
            self.calls.clear()
            rows, meta = select(deepcopy(full), params, 'trace')
            from apps.core.trace_query.langfuse.native import _fields
            expected = {'data': [trace_wire(row, fields=_fields(params)) for row in rows], 'meta': meta}
            self.assertEqual(service.traces(params), expected)
            self.assertEqual(self.source_ids(), [row['id'] for row in rows])

    def test_public_unsupported_filter_keeps_existing_path(self):
        service = NativeLangfuseService(space=self.space, agent=self.agent, provider=self.provider)
        with self.assertRaises(TraceQueryServiceError) as error:
            service.traces({'name': 'root-1'})
        self.assertEqual(error.exception.code, 3036)


class QueryCapacityResponseTests(SimpleTestCase):
    def test_public_and_unified_capacity_errors_keep_actionable_http_status(self):
        from apps.core.views.langfuse_public import NativePublicAPIView
        from apps.core.views.trace_unified import _handle_error
        from apps.core.views.trace_handlers import handle_query_error
        for handler in (NativePublicAPIView().handle_exception,
                        lambda error: _handle_error(error, 'query'),
                        lambda error: handle_query_error(error, 'query')):
            for code, status in ((3036, 413), (3037, 429)):
                with self.subTest(handler=handler, code=code):
                    response = handler(TraceQueryServiceError('bounded query', code))
                    self.assertEqual(response.status_code, status)
                    self.assertEqual(response.get('Retry-After'), '1' if code == 3037 else None)
        response = NativePublicAPIView().handle_exception(TraceQueryServiceError('other query failure', 3035))
        self.assertEqual(response.status_code, 503)
