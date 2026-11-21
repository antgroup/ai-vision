"""Private canonical aggregation facts, without messages or telemetry attributes.

Keep ancestors as well as billable calls: dropping wrappers would lose the
nested-generation ambiguity guard shared by totals, model groups and buckets.
"""
from copy import deepcopy
from decimal import Decimal, DecimalException
import math

from .semantics.observations import timestamp_ns
from .semantics.usage import FIELDS, cost_coverage

VERSION = 'native-trace-metrics-v1'
ROW_FIELDS = frozenset({
    'trace_id', 'name', 'userId', 'session_id', 'environment', 'environmentSource',
    'start_time_unix_nano', 'end_time_unix_nano', 'duration_ms', 'status_code', 'span_count',
    'usage', 'coverage', 'costCoverage', 'reportedUsage', 'rootObservationId', 'rootSelectionReason', 'missingParentIds',
    'input_tokens', 'output_tokens', 'total_tokens', 'attributes.gen_ai.usage.input_tokens',
    'attributes.gen_ai.usage.output_tokens', 'attributes.gen_ai.usage.total_tokens',
    'attributes.langfuse.trace.tags', 'resource.attributes.aivision.space.id',
    'resource.attributes.aivision.agent.id', '_traceTags', '_sceneValues', '_metrics', '_operation_previews',
})
FACT_FIELDS = ('id', 'traceId', 'parentObservationId', 'type', 'startTime', 'model', 'provider')


def _validate_cost(cost):
    if not isinstance(cost, dict) or not {'total', 'input', 'output', 'currency'} <= cost.keys():
        raise ValueError('missing metrics cost')
    for field in ('currency', 'source', 'pricingSource', 'reason'):
        if cost.get(field) is not None and not isinstance(cost[field], str):
            raise ValueError('invalid metrics cost evidence')
    if cost.get('knownSubtotal') is not None and (
            cost['total'] is not None or cost.get('reason') != 'price_bucket_missing'):
        raise ValueError('invalid metrics cost subtotal')
    for field in ('total', 'input', 'output', 'knownSubtotal'):
        value = cost.get(field)
        if value is None:
            continue
        if type(value) not in (str, int, float):
            raise ValueError('invalid metrics cost')
        try:
            amount = Decimal(str(value))
        except (ValueError, DecimalException) as exc:
            raise ValueError('invalid metrics cost') from exc
        if not amount.is_finite() or amount < 0:
            raise ValueError('invalid metrics cost')


def _finite_nonnegative(value):
    try:
        return type(value) in (int, float) and math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def _same_duration_ms(expected, actual):
    if expected is None or actual is None:
        return expected is None and actual is None
    if not _finite_nonnegative(expected) or not _finite_nonnegative(actual):
        return False
    if expected == actual:
        return True
    if type(expected) is int and type(actual) is int:
        return False
    # Generated JSON projections can round a stored double independently of
    # latency. Allow only two adjacent float steps, including at powers of two
    # where a symmetric ULP-based epsilon would admit extra lower-side values.
    lower = upper = expected
    for _ in range(2):
        lower = math.nextafter(lower, -math.inf)
        upper = math.nextafter(upper, math.inf)
    return lower <= actual <= upper


def project_metrics(trace):
    from .native_query import _status
    observations = []
    for item in trace['observations']:
        usage = item['usage']
        # Pricing provenance and subscription equivalents are required by
        # canonical costCoverage; amounts alone cannot reconstruct that evidence.
        cost = deepcopy(usage['cost'])
        currency = cost['currency']
        cost['currency'] = currency if isinstance(currency, str) else None
        observations.append({
            **{field: item[field] for field in FACT_FIELDS},
            'otelStatus': {'code': _status(item)},
            'usage': {**{field: usage[field] for field in FIELDS}, 'cost': cost},
        })
    result = {'version': VERSION, 'latency': trace['latency'], 'observations': observations}
    sessions = trace.get('sessionIds')
    if isinstance(sessions, list) and all(isinstance(value, str) and value for value in sessions):
        # Preserve canonical per-observation membership, including child-only
        # sessions and SDK aliases that differ from physical storage columns.
        result['sessionIds'] = sessions
    return result


def restore_metrics(row):
    value = row.get('_metrics')
    if not isinstance(value, dict) or value.get('version') != VERSION:
        raise ValueError('missing canonical metrics version')
    observations = value.get('observations')
    latency = value.get('latency')
    if latency is not None and not _finite_nonnegative(latency):
        raise ValueError('invalid metrics latency')
    if not _same_duration_ms(latency * 1000 if latency is not None else None, row['duration_ms']):
        raise ValueError('metrics latency differs from canonical summary')
    if not isinstance(observations, list) or len(observations) != row['span_count']:
        raise ValueError('incomplete metrics observations')
    seen = set()
    for item in observations:
        if not isinstance(item, dict) or not (set(FACT_FIELDS) | {'usage', 'otelStatus'}) <= set(item):
            raise ValueError('incomplete metrics fact')
        identifier = item['id']
        if (not isinstance(identifier, str) or not identifier or identifier in seen
                or item['traceId'] != row['trace_id']
                or item['parentObservationId'] is not None and not isinstance(item['parentObservationId'], str)
                or not isinstance(item['type'], str) or not item['type']
                or timestamp_ns(item['startTime']) is None
                # The canonical path retains finite scalar telemetry dimensions
                # as reported. Typed SQL can decline them, but its compact
                # fallback must preserve numeric/bool keys without coercion.
                or any(item[field] is not None and (
                    type(item[field]) not in (str, int, float, bool)
                    or type(item[field]) is float and not math.isfinite(item[field])
                ) for field in ('model', 'provider'))
                or not isinstance(item['otelStatus'], dict)
                or type(item['otelStatus'].get('code')) is not int or item['otelStatus']['code'] not in (0, 1, 2)):
            raise ValueError('invalid metrics observation identity or dimensions')
        seen.add(identifier)
        usage = item['usage']
        if not isinstance(usage, dict) or any(field not in usage for field in (*FIELDS, 'cost')):
            raise ValueError('missing metrics usage')
        if any(usage[field] is not None and (type(usage[field]) is not int or usage[field] < 0) for field in FIELDS):
            raise ValueError('invalid metrics usage')
        cost = usage['cost']
        _validate_cost(cost)
        mode = cost.get('billingMode')
        if (mode not in (None, 'pay_as_you_go', 'subscription')
                or 'estimateListPrice' in cost and type(cost['estimateListPrice']) is not bool):
            raise ValueError('invalid metrics billing evidence')
        equivalent = cost.get('listPriceEquivalent')
        if equivalent is not None or mode == 'subscription':
            _validate_cost(equivalent)
    if row['rootObservationId'] not in seen:
        raise ValueError('missing metrics root')
    root = next(item for item in observations if item['id'] == row['rootObservationId'])
    if root['otelStatus']['code'] != row['status_code']:
        raise ValueError('metrics root status differs from canonical summary')
    # The projector writes both the summary and all per-call facts from the
    # same trace. A complete Span count alone cannot prove complete billing.
    try:
        observed_coverage = cost_coverage(observations)
    except DecimalException as exc:
        raise ValueError('invalid metrics cost coverage') from exc
    if (observed_coverage != row['usage'].get('costCoverage')
            or 'costCoverage' in row and observed_coverage != row['costCoverage']):
        raise ValueError('metrics cost coverage differs from canonical summary')
    result = {'id': row['trace_id'], 'rootObservationId': row['rootObservationId'],
              'tags': row['_traceTags'], 'latency': latency, 'observations': observations}
    if 'sessionIds' in value:
        sessions = value['sessionIds']
        if (not isinstance(sessions, list) or any(not isinstance(item, str) or not item for item in sessions)
                or len(set(sessions)) != len(sessions)):
            raise ValueError('invalid metrics session membership')
        result['sessionIds'] = sessions
    return result
