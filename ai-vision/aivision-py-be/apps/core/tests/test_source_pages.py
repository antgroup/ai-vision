"""Complete reconstruction must reject gaps and stale fragments before publish."""
import base64
from copy import deepcopy
import json
from unittest.mock import Mock
from django.test import SimpleTestCase
from apps.core.trace_query.types import TraceQueryServiceError


class SourcePagesTests(SimpleTestCase):
    def pages(self):
        span = {'trace_id': 'trace', 'span_id': 'span', 'parent_span_id': '',
                'name': '模型', 'kind': 'internal', 'status_code': 2, 'status_message': 'failed',
                'start_time_unix_nano': '1000000001', 'end_time_unix_nano': '2000000002',
                'resource_attributes': json.dumps({'aivision.space.id': 'space'}),
                'attributes': json.dumps({'gen_ai.input.messages': '中文'}, ensure_ascii=False), 'payload': '{}', 'events': '[]', 'links': '[]'}
        data = json.dumps(span, ensure_ascii=False).encode()
        common = dict(supported=True, contract='native-trace-source-pages-v1', trace_id='trace',
                      source_revision=3, maintenance_generation=7, source_user_id='owner',
                      source_agent_product='product', scope_valid=True, span_count=1, source_bytes=len(data),
                      min_timestamp='1000000001', max_timestamp='1000000001',
                      page_byte_limit=2<<20, trace_byte_limit=128<<20)
        split = data.index('中'.encode()) + 1  # Split inside UTF-8, only decode after assembly.
        pages = []
        for offset, fragment in ((0, data[:split]), (split, data[split:])):
            pages.append({**common, 'complete': offset != 0, 'next_cursor': 'next' if offset == 0 else '',
                          'fragments': [{'span_id': 'span', 'offset': offset, 'total_bytes': len(data),
                                         'data_base64': base64.b64encode(fragment).decode()}]})
        return pages

    def read(self, pages):
        from apps.core.trace_query.signoz.source_pages import read_source_pages
        client = Mock()
        client.get.side_effect = pages
        return read_source_pages(client, space_id='space', trace_id='trace')

    def test_split_unicode_and_raw_columns_reconstruct_complete_source(self):
        result = self.read(self.pages())
        self.assertTrue(result['complete'])
        self.assertEqual(result['span_count'], 1)
        span = result['spans'][0]
        self.assertEqual(span['attributes']['gen_ai.input.messages'], '中文')
        self.assertEqual(span['status'], {'code': 'ERROR', 'message': 'failed'})
        self.assertEqual(span['resource'], {'aivision.space.id': 'space'})
        self.assertEqual(span['start_time_unix_nano'], '1000000001')

    def test_gap_duplicate_scope_revision_size_and_false_completion_never_publish(self):
        mutations = [lambda p: p[1].update(source_revision=4),
                     lambda p: p[1].update(maintenance_generation=8),
                     lambda p: p[1].update(source_user_id='other'),
                     lambda p: p[1]['fragments'][0].update(offset=0),
                     lambda p: p[1]['fragments'][0].update(span_id='other'),
                     lambda p: p[0].update(source_bytes=129<<20),
                     lambda p: p[1].update(span_count=2),
                     lambda p: p[1].update(next_cursor='next')]
        for mutation in mutations:
            pages = deepcopy(self.pages()); mutation(pages)
            with self.subTest(mutation=mutation), self.assertRaises(TraceQueryServiceError):
                self.read(pages)
