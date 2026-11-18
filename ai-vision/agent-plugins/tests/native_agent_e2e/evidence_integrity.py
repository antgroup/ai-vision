"""Bind stored normalized facts to the original captured OTLP payloads.

Capture preserves Node OTLP/JSON hex IDs and protobuf JSON base64 IDs. Re-read
both forms without using a saved normalized ID to infer the original encoding.
Artifact consumers must validate this binding before trusting either view.
"""
from __future__ import annotations

import base64
from copy import deepcopy
import json
import math
import re

from google.protobuf.json_format import ParseDict, ParseError

from capture import REQUESTS, decode_export, get

SIGNALS = {
    'traces': ('resource_spans', 'scope_spans', 'spans'),
    'logs': ('resource_logs', 'scope_logs', 'log_records'),
    'metrics': ('resource_metrics', 'scope_metrics', 'metrics'),
}


def _camel(key):
    first, *parts = key.split('_')
    return first + ''.join(part.title() for part in parts)


def _hex_id(value, size):
    if not isinstance(value, str):
        raise ValueError('OTLP IDs must be strings')
    if not value:
        return value
    if len(value) == size * 2 and re.fullmatch('[0-9a-fA-F]+', value):
        return value.lower()
    decoded = base64.b64decode(value, validate=True)
    if len(decoded) != size:
        raise ValueError('Invalid OTLP ID length')
    return decoded.hex()


def _validate_values(value):
    if isinstance(value, list):
        for item in value:
            _validate_values(item)
        return
    if not isinstance(value, dict):
        return
    kinds = {key: field for field in ('string_value', 'bool_value', 'int_value', 'double_value',
             'bytes_value', 'array_value', 'kvlist_value') for key in (field, _camel(field))}
    fields = set(value).intersection(kinds)
    if fields:
        if len(fields) != 1 or len(value) != 1:
            raise ValueError('AnyValue must have one unambiguous value')
        key = next(iter(fields)); field, raw = kinds[key], value[key]
        if field == 'int_value':
            if (type(raw) is not int and not (isinstance(raw, str) and re.fullmatch('-?[0-9]+', raw))
                    or not -(2 ** 63) <= int(raw) < 2 ** 63):
                raise ValueError('Invalid AnyValue integer')
        elif field == 'bool_value' and type(raw) is not bool:
            raise ValueError('Invalid AnyValue boolean')
        elif field == 'double_value' and (type(raw) not in (int, float) or not math.isfinite(raw)):
            raise ValueError('Invalid AnyValue number')
        elif field in ('string_value', 'bytes_value') and not isinstance(raw, str):
            raise ValueError('Invalid AnyValue string')
    for item in value.values():
        _validate_values(item)


def _validate_fields(value, descriptor):
    if not isinstance(value, dict):
        raise ValueError('OTLP message must be an object')
    for field in descriptor.fields:
        keys = set((field.name, field.json_name)).intersection(value)
        if len(keys) > 1:
            raise ValueError('Ambiguous OTLP field alias')
        if not keys:
            continue
        raw = value[next(iter(keys))]
        rows = raw if field.is_repeated else [raw]
        if not isinstance(rows, list):
            raise ValueError('Repeated OTLP field must be an array')
        for row in rows:
            if field.message_type is not None:
                _validate_fields(row, field.message_type)
            elif field.type in (field.TYPE_DOUBLE, field.TYPE_FLOAT, field.TYPE_INT64, field.TYPE_UINT64,
                    field.TYPE_INT32, field.TYPE_FIXED64, field.TYPE_FIXED32, field.TYPE_UINT32,
                    field.TYPE_ENUM, field.TYPE_SFIXED32, field.TYPE_SFIXED64, field.TYPE_SINT32, field.TYPE_SINT64):
                if type(row) is bool:
                    raise ValueError('OTLP numeric field cannot be a boolean')


def _decode_saved(export):
    signal = export['signal']
    root, scope, records = SIGNALS[signal]
    payload = deepcopy(export['otlp'])
    if not isinstance(payload, dict):
        raise ValueError('Original OTLP must be an object')
    roots = {key for name, _, _ in SIGNALS.values() for key in (name, _camel(name))}
    present = set(payload).intersection(roots)
    if len(present) != 1 or not present.issubset({root, _camel(root)}):
        raise ValueError('Original OTLP has the wrong signal')
    _validate_fields(payload, REQUESTS[signal].DESCRIPTOR)
    _validate_values(payload)
    if signal in ('traces', 'logs'):
        for resource in get(payload, root, []):
            for group in get(resource, scope, []):
                for row in get(group, records, []):
                    for field, size in (('trace_id', 16), ('span_id', 8), ('parent_span_id', 8)):
                        keys = [key for key in (field, _camel(field)) if key in row]
                        if len(keys) > 1:
                            raise ValueError('Ambiguous OTLP ID')
                        if keys:
                            row[keys[0]] = _hex_id(row[keys[0]], size)
    # The capture decoder intentionally accepts HTTP exporter variants. A
    # persisted proof additionally requires the actual protobuf schema, with
    # unknown fields, invalid oneofs, and invalid field shapes rejected.
    protobuf_payload = deepcopy(payload)
    if signal in ('traces', 'logs'):
        for resource in get(protobuf_payload, root, []):
            for group in get(resource, scope, []):
                for row in get(group, records, []):
                    for field in ('trace_id', 'span_id', 'parent_span_id'):
                        for key in (field, _camel(field)):
                            if key in row:
                                row[key] = base64.b64encode(bytes.fromhex(row[key])).decode()
    ParseDict(protobuf_payload, REQUESTS[signal](), ignore_unknown_fields=False)
    return decode_export(signal, json.dumps(payload, allow_nan=False).encode(), 'application/json')


def exports_match_raw(exports):
    """Fail closed if any signal's original payload differs from its facts."""
    if not isinstance(exports, list) or not exports:
        return False
    try:
        for export in exports:
            decoded = _decode_saved(export)
            for key in ('spans', 'logs', 'metrics'):
                # JSON comparison also distinguishes booleans from integers.
                actual = json.dumps(export[key], sort_keys=True, allow_nan=False)
                expected = json.dumps(decoded[key], sort_keys=True, allow_nan=False)
                if actual != expected:
                    return False
        return True
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError, ParseError):
        return False
