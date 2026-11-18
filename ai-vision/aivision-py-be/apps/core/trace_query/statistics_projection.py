"""Versioned typed contributions derived once from the complete canonical Trace.

The receipt lives inside the existing row document so old publishers naturally
invalidate typed statistics on every overwrite, including same-revision writes.
"""
import hashlib
import json

from .semantics.observations import timestamp_ns
from .semantics.usage import FIELDS, ambiguous_call_ids

VERSION = 'native-trace-statistics-v1'
OPERATION_TYPES = frozenset({'GENERATION', 'EMBEDDING', 'TOOL'})


def project_statistics(trace):
    from .native_query import _status
    observations = trace['observations']
    root = next(item for item in observations if item['id'] == trace['rootObservationId'])
    ambiguous = ambiguous_call_ids(observations)
    calls = []
    for item in observations:
        if item['type'] not in OPERATION_TYPES:
            continue
        usage = item['usage']
        calls.append({
            'id': item['id'], 'type': item['type'],
            'start_ns': str(timestamp_ns(item['startTime'])),
            'model': item['model'], 'provider': item['provider'],
            'ambiguous': (item['traceId'], item['id']) in ambiguous,
            **{field: usage[field] for field in FIELDS},
            'cost': {**{field: usage['cost'][field] for field in ('total', 'input', 'output')},
                     'currency': usage['cost']['currency'] if isinstance(usage['cost']['currency'], str) else None},
        })
    result = {
        'version': VERSION,
        'trace': {
            'span_count': len(observations),
            'generation_count': sum(item['type'] == 'GENERATION' for item in observations),
            'tool_count': sum(item['type'] == 'TOOL' for item in observations),
            'latency_ms': trace['latency'] * 1000 if trace['latency'] is not None else None,
            'error_count': int(_status(root) == 2),
        },
        'calls': calls,
    }
    return _with_receipt(result)


def decline_statistics(facts):
    """Keep a small durable acknowledgement when optional facts exceed budget."""
    return _with_receipt({
        'version': VERSION, 'trace': facts['trace'], 'calls': [],
        'declined': 'document_outside_typed_bounds',
    })


def _with_receipt(result):
    result['receipt'] = hashlib.sha256(json.dumps(
        result, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False,
    ).encode()).hexdigest()
    return result
