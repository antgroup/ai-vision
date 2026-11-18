"""Bind every Hermes model call's complete IO to native hooks and HTTP evidence."""
from copy import deepcopy
import json
import math


def _json(value):
    return json.loads(value) if isinstance(value, str) else value


def _same(left, right):
    # Unlike Python equality, JSON distinguishes boolean and numeric facts.
    return json.dumps(left, sort_keys=True, ensure_ascii=False, allow_nan=False) == json.dumps(
        right, sort_keys=True, ensure_ascii=False, allow_nan=False)


def _assistant(value, *, sdk=False):
    """Only normalize documented empty fields and Hermes SDK tool-call shape."""
    if not isinstance(value, dict):
        raise ValueError('Assistant message must be an object')
    value = deepcopy(value)
    if sdk:
        value.setdefault('role', 'assistant')
        if value.get('provider_data') is not None:
            raise ValueError('Unexpected native message provider metadata')
        for key in ('usage', 'finish_reason', 'provider_data'):
            value.pop(key, None)
        reasoning = value.pop('reasoning', None)
        if reasoning not in (None, ''):
            if value.get('reasoning_content') not in (None, '', reasoning):
                raise ValueError('Conflicting native reasoning fields')
            value['reasoning_content'] = reasoning
    if value.get('role') != 'assistant':
        raise ValueError('Expected an assistant response')
    if value.get('content') in (None, ''):
        value['content'] = None
    calls = value.get('tool_calls')
    if calls is None:
        calls = []
    if not isinstance(calls, list):
        raise ValueError('Tool calls must be an array')
    normalized = []
    for call in calls:
        call = deepcopy(call)
        if sdk and 'function' not in call:
            provider_data = call.pop('provider_data', None)
            if provider_data is not None:
                raise ValueError('Unexpected native tool provider metadata')
            call = {'id': call.pop('id'), 'type': 'function',
                    'function': {'name': call.pop('name'), 'arguments': call.pop('arguments')}, **call}
        if call.get('type') != 'function' or not isinstance(call.get('id'), str):
            raise ValueError('Invalid function call identity')
        function = call['function']
        function['arguments'] = _json(function['arguments'])
        normalized.append(call)
    value['tool_calls'] = normalized
    for key in ('reasoning_content', 'reasoning_details'):
        if value.get(key) in (None, '', []):
            value.pop(key, None)
    return value


def _output(value):
    value = _json(value)
    if not isinstance(value, list) or len(value) != 1:
        raise ValueError('One model call must contain one assistant output')
    return _assistant(value[0])


def call_checks(report):
    names = ('sequence', 'input', 'output', 'api_log_io')
    checks = {f'native.call_{name}': True for name in names}
    try:
        raw = report['result']['details']['hermes_raw_callbacks']
        spans = [row for export in report['exports'] for row in export.get('spans', [])
                 if row['attributes'].get('gen_ai.span.kind') == 'LLM']
        logs = [row for export in report['exports'] for row in export.get('logs', [])
                if row['attributes'].get('event.name') == 'api_request']
        by_id = {row['attributes']['agentic.model_call.id']: row for row in spans}
        log_by_id = {row['attributes']['agentic.model_call.id']: row for row in logs}
        posts = {row['rawInput']['api_request_id']: row['rawInput'] for row in raw
                 if row['hook'] in ('post_api_request', 'post_auxiliary_call')}
        requests = [row for row in report['requests'] if row.get('method') != 'GET' and row.get('response')]
        pairs = []
        for auxiliary, hook in ((False, 'pre_api_request'), (True, 'pre_auxiliary_call')):
            sources = [row['rawInput'] for row in raw if row['hook'] == hook]
            if any(type(row.get('started_at')) not in (int, float)
                   or not math.isfinite(row['started_at']) or row['started_at'] <= 0 for row in sources):
                raise ValueError('Missing native call sequence')
            sources.sort(key=lambda row: row['started_at'])
            calls = [row for row in requests if (row.get('purpose') == 'auxiliary') == auxiliary]
            if len(sources) != len(calls) or len({row['started_at'] for row in sources}) != len(sources):
                raise ValueError('Native and physical call inventories differ')
            for source, wire in zip(sources, calls):
                span = by_id[source['api_request_id']]
                expected_name = source.get('aux_task') if auxiliary else None
                if (auxiliary and not expected_name) or span['attributes'].get('gen_ai.agent.name') != expected_name:
                    raise ValueError('Native primary/auxiliary purpose differs')
                pairs.append((source, wire, span))
        if (not pairs or len(pairs) != len(spans) or len(by_id) != len(spans)
                or {source['api_request_id'] for source, _, _ in pairs} != set(by_id)):
            raise ValueError('Duplicate or missing native call ID')
        checks['native.call_api_log_io'] = len(logs) == len(log_by_id) == len(by_id) and set(log_by_id) == set(by_id)
        for source, wire, span in pairs:
            attrs = span['attributes']
            messages = wire['request']['messages']
            expected = _assistant(wire['response'])
            checks['native.call_input'] &= (isinstance(messages, list) and bool(messages)
                and _same(source.get('request_messages'), messages)
                and _same(_json(attrs.get('gen_ai.input.messages')), messages))
            checks['native.call_output'] &= (_same(_output(attrs.get('gen_ai.output.messages')), expected)
                and _same(_json(attrs.get('gen_ai.response.finish_reasons')), [wire['finish_reason']]))
            post = posts.get(source['api_request_id'])
            if post:
                response = post['response']
                checks['native.call_output'] &= (post.get('finish_reason') == wire['finish_reason']
                    and _same(_assistant(response['assistant_message'], sdk=True), expected))
                if 'assistant_message' in post:
                    checks['native.call_output'] &= _same(_assistant(post['assistant_message'], sdk=True), expected)
            elif not (report['result'].get('version') == '0.21.5' and wire['finish_reason'] == 'length'):
                checks['native.call_output'] = False
            event = log_by_id.get(source['api_request_id'])
            if event:
                event_attrs = event['attributes']
                checks['native.call_api_log_io'] &= (event.get('trace_id') == span.get('trace_id')
                    and event.get('span_id') == span.get('span_id')
                    and all(_same(event_attrs.get(key), attrs.get(key)) for key in (
                        'gen_ai.conversation.id', 'gen_ai.chat.id', 'agentic.turn.id', 'agentic.turn.number',
                        'gen_ai.request.model', 'gen_ai.response.model', 'gen_ai.usage.input_tokens',
                        'gen_ai.usage.output_tokens', 'gen_ai.usage.reasoning.output_tokens'))
                    and _same(_json(event_attrs.get('gen_ai.input.messages')), messages)
                    and _same(_output(event_attrs.get('gen_ai.output.messages')), expected))
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        # Malformed or incomplete evidence must never turn into an allowance.
        return {key: False for key in checks}
    return checks
