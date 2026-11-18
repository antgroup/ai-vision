"""Bounded lossless source transport for oversized projection inputs.

Only a complete source with unchanged revision, scope and generation can reach
publication. An old data plane may decline; malformed success is never fallback.
"""
import base64
import binascii
import json

from ..types import TraceQueryServiceError
from .read_model import PATH

PAGE_BYTES = 2 << 20
TRACE_BYTES = 128 << 20


def invalid(message):
    raise TraceQueryServiceError('Invalid paged Trace source: ' + message, 3035)


def _decode_span(data, identifier, trace_id):
    try:
        span = json.loads(data)
        if not isinstance(span, dict) or span.get('span_id') != identifier or span.get('trace_id') != trace_id:
            invalid('span identity differs')
        for key in ('resource_attributes', 'attributes', 'events', 'links', 'payload'):
            if isinstance(span.get(key), str):
                span[key] = json.loads(span[key])
    except (UnicodeDecodeError, ValueError) as exc:
        raise TraceQueryServiceError('Invalid paged Trace JSON', 3035) from exc
    span['resource'] = span.get('resource_attributes')
    code = span.pop('status_code', None)
    if type(code) is not int or code not in (0, 1, 2):
        invalid('invalid span status')
    span['status'] = {'code': ('UNSET', 'OK', 'ERROR')[code], 'message': span.pop('status_message', '')}
    kinds = {'unspecified': '0', 'internal': '1', 'server': '2', 'client': '3', 'producer': '4', 'consumer': '5'}
    span['kind'] = kinds.get(str(span.get('kind', '')).strip().lower(), span.get('kind'))
    return span


def read_source_pages(client, *, space_id, trace_id):
    cursor, cursors, baseline, spans = '', set(), None, []
    current, total, buffer, consumed = None, 0, bytearray(), 0
    while True:
        params = {'space_id': str(space_id), 'trace_id': trace_id}
        if cursor:
            params['cursor'] = cursor
        try:
            page = client.get(PATH + '/source-page', params=params)
        except TraceQueryServiceError as exc:
            if exc.code == 3019:
                return {'supported': False, 'reason': 'source_pages_unavailable', 'trace_id': trace_id}
            raise
        if isinstance(page, dict) and page.get('supported') is False:
            if not isinstance(page.get('reason'), str) or not page['reason']:
                invalid('unsupported response missing reason')
            return {**page, 'trace_id': trace_id}
        if (not isinstance(page, dict) or page.get('supported') is not True
                or page.get('contract') != 'native-trace-source-pages-v1' or page.get('trace_id') != trace_id
                or page.get('page_byte_limit') != PAGE_BYTES or page.get('trace_byte_limit') != TRACE_BYTES
                or len(json.dumps(page, ensure_ascii=False).encode()) > PAGE_BYTES):
            invalid('invalid page envelope or bounds')
        proof = {key: page.get(key) for key in ('trace_id', 'source_revision', 'maintenance_generation',
                 'source_user_id', 'source_agent_product', 'scope_valid', 'span_count', 'source_bytes',
                 'min_timestamp', 'max_timestamp')}
        if baseline is None:
            if (any(type(proof[key]) is not int or proof[key] < 1 for key in
                    ('source_revision', 'maintenance_generation', 'span_count', 'source_bytes'))
                    or proof['source_revision'] >= 2**63 or proof['maintenance_generation'] >= 2**63
                    or proof['span_count'] > 100000 or proof['source_bytes'] > TRACE_BYTES
                    or proof['scope_valid'] is not True
                    or any(not isinstance(proof[key], str) or not proof[key] for key in
                           ('source_user_id', 'source_agent_product', 'min_timestamp', 'max_timestamp'))):
                invalid('invalid source proof or source budget')
            baseline = proof
        elif proof != baseline:
            invalid('source proof changed between fragments')
        fragments = page.get('fragments')
        if not isinstance(fragments, list) or not fragments or len(fragments) > 128:
            invalid('missing or excessive fragments')
        for fragment in fragments:
            if not isinstance(fragment, dict):
                invalid('invalid fragment')
            identifier, offset, size = (fragment.get(key) for key in ('span_id', 'offset', 'total_bytes'))
            if (not isinstance(identifier, str) or not identifier or type(offset) is not int or offset < 0
                    or type(size) is not int or not 1 <= size <= baseline['source_bytes']):
                invalid('invalid fragment identity or size')
            if current is None:
                if spans and identifier <= spans[-1]['span_id'] or offset != 0:
                    invalid('duplicate or unordered spans')
                current, total = identifier, size
            if identifier != current or offset != len(buffer) or size != total:
                invalid('fragment gap, overlap or size drift')
            try:
                data = base64.b64decode(fragment.get('data_base64', ''), validate=True)
            except (binascii.Error, ValueError, TypeError) as exc:
                raise TraceQueryServiceError('Invalid source fragment encoding', 3035) from exc
            if not data or len(buffer) + len(data) > total or consumed + len(data) > baseline['source_bytes']:
                invalid('fragment exceeds complete source size')
            buffer.extend(data); consumed += len(data)
            if len(buffer) == total:
                spans.append(_decode_span(buffer, current, trace_id))
                current, total, buffer = None, 0, bytearray()
                if len(spans) > baseline['span_count']:
                    invalid('source count exceeded')
        complete, cursor = page.get('complete'), page.get('next_cursor')
        if type(complete) is not bool or not isinstance(cursor, str) or len(cursor) > 4096:
            invalid('invalid completion state')
        if complete:
            if cursor or current is not None or len(spans) != baseline['span_count'] or consumed != baseline['source_bytes']:
                invalid('false source completion')
            return {**baseline, 'supported': True, 'contract': 'native-trace-source-v1', 'complete': True, 'spans': spans}
        if not cursor or cursor in cursors:
            invalid('non-progressing continuation')
        cursors.add(cursor)
