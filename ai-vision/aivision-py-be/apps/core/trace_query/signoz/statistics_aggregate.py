"""Verified SQL aggregates over revision-bound typed canonical contributions.

Unsupported capabilities keep the complete-window path. A malformed success or
authorization/transport error is never converted into apparently valid totals.
"""
from decimal import Decimal, InvalidOperation

from apps.core.services.model_pricing import get_space_model_prices
import math

from ..native_query import NativeTraceWindow, _decode, costs_requested, without_costs, normalized_agent_params
from ..semantics.observations import iso_timestamp
from ..semantics.usage import FIELDS, _text
from ..types import BaseTraceQuery, TraceQueryServiceError
from .query_control import query_mode
from .read_model import PATH, projection_context

CONTRACT = 'native-trace-metrics-aggregate-v1'
BUCKET_SECONDS = {'minute': 60, 'hour': 3600, 'day': 86400}
MAX_ROWS = 150000
COUNT_FIELDS = ('callCount', 'ambiguousCount', 'costCurrencyInvalid',
                *(field + 'Known' for field in FIELDS),
                'costTotalKnown', 'costInputKnown', 'costOutputKnown')
SUM_FIELDS = (*(field + 'Sum' for field in FIELDS),
              'costTotalSum', 'costInputSum', 'costOutputSum')


def invalid(message):
    raise TraceQueryServiceError('Invalid statistics aggregate: ' + message, 3035)


def _count(value, name):
    if type(value) is not int or value < 0:
        invalid('invalid ' + name)
    return value


def _sum(value, known, name, *, integer=False):
    if not known:
        if value is not None:
            invalid('unknown sum must remain null: ' + name)
        return None
    if not isinstance(value, str) or not value or len(value) > 100:
        invalid('missing numeric string: ' + name)
    if integer:
        if not value.isascii() or not value.isdigit():
            invalid('invalid integer sum: ' + name)
        return int(value)
    try:
        result = Decimal(value)
    except InvalidOperation:
        invalid('invalid Decimal sum: ' + name)
    if not result.is_finite() or result < 0:
        invalid('invalid Decimal sum: ' + name)
    return result


def decode_usage(value):
    if not isinstance(value, dict):
        invalid('missing usage accumulator')
    counts = {key: _count(value.get(key), key) for key in COUNT_FIELDS}
    total, ambiguous = counts['callCount'], counts['ambiguousCount']
    if any(count > total for key, count in counts.items() if key != 'callCount'):
        invalid('usage count exceeds billable calls')
    sums = {}
    for field in FIELDS:
        sums[field] = _sum(value.get(field + 'Sum'), counts[field + 'Known'], field, integer=True)
    costs = {field: _sum(value.get('cost' + field.title() + 'Sum'),
                        counts['cost' + field.title() + 'Known'], 'cost ' + field)
             for field in ('total', 'input', 'output')}
    usage = {field: sums[field] if total and counts[field + 'Known'] == total and not ambiguous else None
             for field in FIELDS}
    reason = 'ambiguous_nested_generations' if ambiguous else 'usage_missing' if usage['total'] is None else None
    complete_cost = bool(total and counts['costTotalKnown'] == total and not counts['costCurrencyInvalid'] and not ambiguous)
    usage.update(inputIncludesCache=True if usage['input'] is not None else None,
                 source={field: 'aggregate_unique_calls' for field in FIELDS if usage[field] is not None},
                 raw={}, observationCount=total, unknownReason=reason,
                 cost={**{field: _text(costs[field]) if complete_cost and counts['cost' + field.title() + 'Known'] == total else None
                          for field in ('total', 'input', 'output')},
                       'source': 'aggregate_unique_calls', 'currency': 'USD',
                       'reason': None if complete_cost else 'cost_missing', 'pricingVersion': None})
    reported = {field: {'value': sums[field], 'reportedCalls': counts[field + 'Known']}
                for field in ('input', 'output', 'total')}
    coverage = {'billableCalls': total, 'knownUsageCalls': counts['totalKnown'],
                'knownTokens': sums['total'] if sums['total'] is not None else 0,
                'knownCostCalls': counts['costTotalKnown']}
    return usage, reported, coverage


def _rows(payload, key):
    rows = payload.get(key)
    if not isinstance(rows, list) or len(rows) > MAX_ROWS or any(not isinstance(row, dict) for row in rows):
        invalid('invalid ' + key)
    return rows


def _reconcile(rows, total):
    for key in COUNT_FIELDS:
        if sum(row[key] for row in rows) != total[key]:
            invalid('incomplete grouped count: ' + key)
    for key in SUM_FIELDS:
        numeric = Decimal if key.startswith('cost') else int
        amounts = [numeric(row[key]) for row in rows if row[key] is not None]
        result = sum(amounts, numeric(0)) if amounts else None
        expected = numeric(total[key]) if total[key] is not None else None
        if result != expected:
            invalid('incomplete grouped sum: ' + key)


def decode_aggregate(payload, wire):
    if not isinstance(payload, dict) or payload.get('supported') is not True or payload.get('complete') is not True:
        invalid('incomplete capability success')
    meta = payload.get('meta')
    if payload.get('contract') != CONTRACT or not isinstance(meta, dict) or meta.get('complete') is not True:
        invalid('missing version or completeness')
    if meta.get('representation') != 'statistics-v1':
        invalid('unexpected representation')
    for key in ('space_id', 'user', 'agent_product', 'agent_uuid', 'projection_context', 'bucket', 'group_by'):
        if (meta.get(key) or '') != (wire.get(key) or ''):
            invalid('scope or query differs: ' + key)
    for key in ('from', 'to'):
        if type(meta.get(key)) is not int or meta[key] != wire[key]:
            invalid('time window differs: ' + key)
    summary = payload.get('summary')
    if not isinstance(summary, dict):
        invalid('missing Trace summary')
    counts = {key: _count(summary.get(key), key) for key in
              ('traceCount', 'spanCount', 'generationCount', 'toolCount', 'errorCount', 'latencyCount')}
    traces, spans, calls = counts['traceCount'], counts['spanCount'], payload.get('usage')
    usage, reported, coverage = decode_usage(calls)
    billable = coverage['billableCalls']
    if (traces > 50000 or traces > spans or counts['errorCount'] > traces or counts['latencyCount'] > traces
            or counts['generationCount'] > billable or billable + counts['toolCount'] > spans
            or not traces and any(counts.values())):
        invalid('inconsistent Trace/Span/call counts')
    latency_count = counts['latencyCount']
    latency_sum, p95 = summary.get('latencySum'), summary.get('latencyP95')
    for value in (latency_sum, p95):
        if latency_count:
            if type(value) not in (float, int) or not math.isfinite(value) or value < 0:
                invalid('invalid latency result')
        elif value is not None:
            invalid('empty latency must remain null')
    if latency_count and p95 > latency_sum:
        invalid('P95 exceeds latency sum')
    latencies = payload.get('latencies')
    if not isinstance(latencies, list) or len(latencies) != latency_count:
        invalid('incomplete exact latency vector')
    previous = -1.0
    for value in latencies:
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0 or value < previous:
            invalid('invalid or unsorted latency vector')
        previous = value
    # The canonical path sorts Python float latencies before summing. Database
    # SUM order can differ by an ULP, so retain this tiny, bounded scalar vector.
    latencies = [float(value) for value in latencies]
    if latencies and latencies[math.ceil(len(latencies) * .95) - 1] != p95:
        invalid('P95 differs from the exact nearest rank')
    by_type, groups, series = (_rows(payload, key) for key in ('usageByType', 'groups', 'timeseries'))
    decoded_types, decoded_groups, decoded_series = [], [], []
    seen = set()
    for row in by_type:
        kind = row.get('type')
        if kind not in {'GENERATION', 'EMBEDDING'} or kind in seen:
            invalid('duplicate or invalid usage type')
        seen.add(kind)
        own, own_reported, _ = decode_usage(row)
        if not own['observationCount']:
            invalid('empty usage type group')
        if kind == 'GENERATION' and own['observationCount'] != counts['generationCount']:
            invalid('inconsistent generation type count')
        decoded_types.append({'type': kind, 'callCount': own['observationCount'], 'usage': own, 'reportedUsage': own_reported})
    if counts['generationCount'] and 'GENERATION' not in seen:
        invalid('missing generation type group')
    seen = set()
    for row in groups:
        key = row.get('value')
        if key is not None and not isinstance(key, str) or key in seen:
            invalid('duplicate or invalid grouping dimension')
        seen.add(key)
        own, _, _ = decode_usage(row)
        if not own['observationCount']:
            invalid('empty grouping dimension')
        decoded_groups.append({'dimension': wire['group_by'], 'value': key, 'usage': own, 'callCount': own['observationCount']})
    width = BUCKET_SECONDS[wire['bucket']] * 1000000000
    seen = set()
    for row in series:
        start = _bucket(row.get('start_ns'), wire, width)
        if start in seen:
            invalid('duplicate timeseries bucket')
        seen.add(start)
        own, _, _ = decode_usage(row)
        if not own['observationCount']:
            invalid('empty timeseries bucket')
        decoded_series.append((start, {'timestamp': iso_timestamp(start), 'usage': own, 'callCount': own['observationCount']}))
    for rows in (by_type, groups, series):
        _reconcile(rows, calls)
    operations, seen, operation_counts = [], set(), {'GENERATION': 0, 'EMBEDDING': 0, 'TOOL': 0}
    for row in _rows(payload, 'operations'):
        start, kind = _bucket(row.get('start_ns'), wire, width), row.get('type')
        count = _count(row.get('count'), 'operation count')
        if kind not in operation_counts or (start, kind) in seen or not count:
            invalid('invalid operation bucket')
        seen.add((start, kind))
        operation_counts[kind] += count
        operations.append({'start_ns': start, 'type': kind, 'count': count})
    if (operation_counts['GENERATION'] != counts['generationCount'] or operation_counts['TOOL'] != counts['toolCount']
            or operation_counts['GENERATION'] + operation_counts['EMBEDDING'] != billable):
        invalid('incomplete operation buckets')
    result = {key: counts[key] for key in ('traceCount', 'spanCount', 'generationCount', 'toolCount', 'errorCount')}
    result.update(errorRate=counts['errorCount'] / traces if traces else None,
                  usage=usage, reportedUsage=reported, coverage=coverage,
                  usageByType=sorted(decoded_types, key=lambda row: row['type'] != 'GENERATION'),
                  groups=sorted(decoded_groups, key=lambda row: str(row['value'] or '')),
                  timeseries=[row for _, row in sorted(decoded_series)],
                  latencyMs={'mean': sum(latencies) / latency_count if latency_count else None,
                             'p95': float(p95) if p95 is not None else None},
                  meta={'complete': True, 'totalItems': traces, 'from': wire['from'], 'to': wire['to'],
                        'queryMode': 'statistics_aggregate', 'queryContract': CONTRACT,
                        'projectedTraceCount': traces, 'rawTraceCount': 0,
                        'timeBoundary': 'fromInclusiveToExclusive'})
    return {'metrics': result, 'operations': operations}


def _bucket(value, wire, width):
    if not isinstance(value, str) or not value.isascii() or not value.isdigit() or len(value) > 20:
        invalid('invalid bucket timestamp')
    start = int(value)
    if start % width or start >= wire['to'] * 1000000 or start + width <= wire['from'] * 1000000:
        invalid('bucket outside selected window')
    return start


def query_metrics_aggregate(provider, query, *, physical_user=None, operations_only=False, _prices=None):
    if query_mode() == 'raw':
        return None
    params = normalized_agent_params(query.query_params)
    include_cost = costs_requested(params)
    if _decode(params.get('filters') or []):
        # Dynamic attribute fields are discovered from the actual observations.
        # Let the complete window validate them against that schema; an empty
        # validation window cannot establish which custom fields exist.
        return None
    # Validate the public metrics parameters before deciding capability support.
    NativeTraceWindow([], project_id=str(query.space.uuid)).metrics(params)
    if params.get('agent_uuid') is not None and not isinstance(params['agent_uuid'], str):
        # BaseTraceQuery exposes only string identities. Preserve the canonical
        # predicate for other JSON values instead of dropping it and widening
        # an instance query into a product-wide aggregate.
        return None
    if physical_user is not None and params.get('username') == physical_user:
        params.pop('username')
    if any(key not in {'from', 'to', 'fromTimestamp', 'toTimestamp', 'agent_product', 'agent_uuid',
                       'source_system', 'sourceSystem', 'bucket', 'groupBy', 'filters', 'include_cost'} and value not in (None, '', [])
           for key, value in params.items()) or _decode(params.get('filters') or []):
        return None
    frozen = provider._freeze_window_params(BaseTraceQuery(query.space, query.user, params))
    client, config = provider._context(query)
    scope = provider._params(BaseTraceQuery(query.space, query.user, frozen), config, agent_filter=False)
    wire = {key: value for key, value in scope.items() if key in {'space_id', 'user', 'agent_product', 'from', 'to'}}
    if physical_user is not None:
        wire['user'] = physical_user
    prices = get_space_model_prices(query.space) if _prices is None else _prices
    wire.update(agent_uuid=query.agent_uuid or '', projection_context=projection_context(prices),
                bucket=params.get('bucket', 'hour'), group_by=params.get('groupBy', 'model'))
    try:
        payload = client.get(PATH + '/metrics-aggregate', params=wire)
    except TraceQueryServiceError as exc:
        if exc.code == 3019:
            return None
        raise
    if isinstance(payload, dict) and payload.get('supported') is False:
        if not isinstance(payload.get('reason'), str) or not payload['reason']:
            invalid('unsupported capability needs a reason')
        return None
    decoded = decode_aggregate(payload, wire)
    dimensions = {row['value'] for row in decoded['metrics']['groups']}
    if None in dimensions and '' in dimensions:
        # These distinct canonical keys share the same stable-sort key. The
        # existing window retains their observation insertion order, which an
        # aggregate cannot reconstruct. Keep that complete-window behavior.
        return None
    # statistics-v1 has no pricing sources, subscription equivalents or
    # unknown reasons. Its operations are complete, but its usage cannot serve
    # the expanded canonical costCoverage contract. Keep the compact window
    # until storage advertises an evidence-complete statistics contract.
    if not operations_only and include_cost:
        return None
    if not include_cost:
        decoded['metrics'] = {**without_costs(decoded['metrics']), 'costsIncluded': False}
    return decoded
