"""Bounded recent operation facts and explicit previews, fenced by source proof."""
import math
from apps.core.services.model_pricing import get_space_model_prices

from ..native_query import NativeTraceWindow
from ..types import BaseTraceQuery, TraceQueryServiceError
from ..semantics.observations import iso_timestamp
from .query_control import query_mode
from .read_model import PATH, projection_context

CONTRACT = 'native-trace-operation-page-v1'
KINDS = {'GENERATION': 'llm', 'EMBEDDING': 'llm', 'TOOL': 'tool'}


def invalid(message):
    raise TraceQueryServiceError('Invalid recent operation page: ' + message, 3035)


def _observation(row, identifier, kind, at):
    facts, preview = row.get('facts'), row.get('preview')
    if facts is None and preview is None:
        return {'id': identifier, 'type': kind}
    if not isinstance(facts, dict) or not isinstance(preview, dict):
        invalid('partial operation facts or preview')
    facts = dict(facts)
    fields = ('input', 'output', 'total', 'cacheReadInput', 'cacheWriteInput', 'reasoning')
    for key in fields:
        value = facts.get(key)
        if isinstance(value, str) and value.isascii() and value.isdigit() and len(value) <= 100:
            facts[key] = int(value)
    if (any(key not in facts or facts[key] is not None and (type(facts[key]) is not int or facts[key] < 0) for key in fields)
            or type(facts.get('ambiguous')) is not bool
            or any(facts.get(key) is not None and not isinstance(facts[key], str) for key in ('model', 'provider'))):
        invalid('invalid typed usage facts')
    latency = preview.get('latency')
    if (not isinstance(preview.get('name'), str) or len(preview['name']) > 1024
            or latency is not None and (type(latency) not in (int, float) or not math.isfinite(latency) or latency < 0)
            or type(preview.get('status')) is not int or preview['status'] not in (0, 1, 2)
            or any(key not in preview or preview[key] is not None and
                   (not isinstance(preview[key], str) or len(preview[key]) > 1024) for key in ('input', 'output'))
            or any(type(preview.get(key + '_truncated')) is not bool for key in ('input', 'output'))):
        invalid('invalid bounded operation preview')
    return {'id': identifier, 'type': kind, 'startTime': iso_timestamp(at), 'name': preview['name'],
            'latency': latency, 'model': facts.get('model'), 'provider': facts.get('provider'),
            'otelStatus': {'code': preview['status']}, 'usage': {key: facts[key] for key in fields},
            'input': preview['input'], 'output': preview['output'], '_eventPreview': True,
            'input_truncated': preview['input_truncated'], 'output_truncated': preview['output_truncated']}


def query_operation_page(provider, query, *, limit, physical_user=None, _prices=None):
    if query_mode() == 'raw':
        return None
    frozen = {key: value for key, value in provider._freeze_window_params(query).items() if key != 'username'}
    client, config = provider._context(query)
    scope = provider._params(BaseTraceQuery(query.space, query.user, frozen), config, agent_filter=False)
    wire = {key: value for key, value in scope.items() if key in {'space_id', 'user', 'agent_product', 'from', 'to'}}
    if physical_user is not None:
        wire['user'] = physical_user
    prices = get_space_model_prices(query.space) if _prices is None else _prices
    wire.update(agent_uuid=query.agent_uuid or '', projection_context=projection_context(prices), limit=limit)
    from .performance_capabilities import performance_capabilities
    if performance_capabilities(client).get('operation_facts') is True:
        wire['include_facts'] = 'true'
    try:
        payload = client.get(PATH + '/operation-page', params=wire)
    except TraceQueryServiceError as exc:
        if exc.code == 3019:
            return None
        raise
    if isinstance(payload, dict) and payload.get('supported') is False:
        if not isinstance(payload.get('reason'), str) or not payload['reason']:
            invalid('unsupported response needs a reason')
        return None
    if (not isinstance(payload, dict) or payload.get('supported') is not True or payload.get('complete') is not True
            or payload.get('contract') != CONTRACT):
        invalid('missing complete versioned contract')
    meta = payload.get('meta')
    if not isinstance(meta, dict) or meta.get('complete') is not True or meta.get('representation') != 'operations-v1':
        invalid('missing complete operation representation')
    generation = meta.get('maintenance_generation')
    if type(generation) is not int or not 1 <= generation < 2**63:
        invalid('invalid maintenance generation')
    for key in ('space_id', 'user', 'agent_product', 'agent_uuid', 'projection_context'):
        value = meta.get(key)
        if value is not None and not isinstance(value, str) or (value or '') != (wire.get(key) or ''):
            invalid('authorized scope or context differs: ' + key)
    for key in ('from', 'to', 'limit'):
        if type(meta.get(key)) is not int or meta[key] != wire[key]:
            invalid('query bounds differ: ' + key)
    rows, more = payload.get('data'), payload.get('has_more')
    if (not isinstance(rows, list) or len(rows) > limit or type(more) is not bool
            or more and len(rows) != limit):
        invalid('inconsistent page length or continuation')
    window = NativeTraceWindow([], project_id=str(query.space.uuid))
    selected, summaries, seen, previous = [], {}, set(), None
    for row in rows:
        if not isinstance(row, dict):
            invalid('operation key is not an object')
        trace, observation, kind = row.get('trace_id'), row.get('observation_id'), row.get('type')
        revision, count, at = row.get('source_revision'), row.get('span_count'), row.get('start_ns')
        if (not isinstance(trace, str) or not trace or not isinstance(observation, str) or not observation
                or not isinstance(kind, str) or kind not in KINDS
                or type(revision) is not int or not 1 <= revision < 2**63
                or type(count) is not int or count < 1
                or not isinstance(at, str) or not at.isascii() or not at.isdigit() or len(at) > 20):
            invalid('invalid operation identity, revision, count or timestamp')
        at = int(at)
        if not wire['from'] * 1000000 <= at < wire['to'] * 1000000:
            invalid('operation outside selected time window')
        identity, ordered = (trace, observation), (at, trace, observation)
        if identity in seen or previous is not None and ordered >= previous:
            invalid('duplicate or unordered operation keys')
        seen.add(identity)
        previous = ordered
        summary = {'trace_id': trace, 'sourceRevision': revision, 'span_count': count}
        if trace in summaries and summaries[trace] != summary:
            invalid('same Trace has inconsistent source proof')
        summaries[trace] = summary
        selected.append(({'id': trace}, _observation(row, observation, kind, at), KINDS[kind], at))
    if selected and any(item[1].get('_eventPreview') for item in selected) and not all(item[1].get('_eventPreview') for item in selected):
        invalid('partial operation preview page')
    window.rows = list(summaries.values())
    window.maintenance_generation = generation
    return window, selected
