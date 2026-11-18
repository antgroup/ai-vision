"""Read-only DSH canonical source oracle, independent of the plugin/replay engine."""
from __future__ import annotations

import json


def _json(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def _count(value):
    return type(value) is int and value >= 0


def _flag(value, wanted):
    return (type(value) is bool and value is wanted) or (type(value) is str and value == ('true' if wanted else 'false'))


def _same(a,b):
    return json.dumps(a,sort_keys=True,allow_nan=False)==json.dumps(b,sort_keys=True,allow_nan=False)


def _text(content):
    if not isinstance(content, list):
        return None
    return ''.join(block['text'] for block in content if isinstance(block, dict)
                   and block.get('type') == 'text' and isinstance(block.get('text'), str))


def _usage(native, wire):
    if wire is None:
        return native is None
    if not isinstance(native, dict) or not isinstance(wire, dict):
        return False
    details = wire.get('prompt_tokens_details') or {}
    read, write = details.get('cached_tokens'), details.get('cache_write_tokens')
    input_tokens, output_tokens = wire.get('prompt_tokens'), wire.get('completion_tokens')
    if not _count(input_tokens) or not _count(output_tokens):
        return False
    fresh = input_tokens - (read or 0) - (write or 0)
    if not _count(fresh) or not _count(native.get('inputTokens')) or not _count(native.get('outputTokens')) or native['inputTokens'] != fresh or native['outputTokens'] != output_tokens:
        return False
    for key, value in (('cacheReadTokens', read), ('cacheWriteTokens', write),
                       ('reasoningTokens', (wire.get('completion_tokens_details') or {}).get('reasoning_tokens'))):
        if value is None:
            if key in native:
                return False
        elif not _count(value) or not _count(native.get(key)) or native[key] != value:
            return False
    return 'totalTokens' not in native or (_count(native['totalTokens']) and native['totalTokens'] == wire.get('total_tokens'))


def canonical_matches(result, calls, spans, *, scenario, receipt):
    """Bind host-owned ordered JSONL events to physical responses and native roots."""
    details = result.get('details') or {}
    header, events = details.get('canonical_header'), details.get('canonical_events')
    if (not isinstance(header, dict) or header.get('id') != result.get('session_id')
            or header.get('version') not in (2, 4) or not isinstance(events, list) or not events):
        return False
    if isinstance(events[0], dict) and events[0].get('type') == 'session':
        if events[0] != header:
            return False
        events = events[1:]
    if not events or any(not isinstance(row, dict) or not _count(row.get('seq')) or row['seq'] != seq
            or not _count(row.get('time')) or not isinstance(row.get('data'), dict)
            for seq, row in enumerate(events)):
        return False
    starts = [row['data']['turn'] for row in events if row.get('type') == 'turn/start' and _count(row['data'].get('turn'))]
    ends = [row['data'] for row in events if row.get('type') == 'turn/end']
    expected_turns = 2 if scenario == 'multi-turn' else 1
    if len(starts) != expected_turns or len(set(starts)) != expected_turns or [end.get('turn') for end in ends] != starts:
        return False
    reasons = {'aborted'} if scenario == 'cancel' else {'completed'}
    if any((end.get('reason') or {}).get('kind') not in reasons for end in ends):
        return False
    if scenario == 'cancel' and any(((end.get('reason') or {}).get('reason') or {}).get('kind') != 'user' for end in ends):
        return False
    roots = [span for span in spans if str(span.get('attributes', {}).get('gen_ai.span.kind', '')).upper() == 'CHAT'
             or span.get('attributes', {}).get('gen_ai.operation.name') == 'invoke_agent']
    root_turns = [span.get('attributes', {}).get('agentic.turn.number') for span in roots]
    if len(roots) != len(starts) or any(not _count(turn) for turn in root_turns) or set(root_turns) != set(starts):
        return False
    steps = [(row['data'].get('turn'), row['data'].get('step')) for row in events if row.get('type') == 'step/start']
    closed_steps = [(row['data'].get('turn'), row['data'].get('step')) for row in events if row.get('type') == 'step/end']
    primary = [call for call in calls if call.get('purpose') != 'auxiliary']
    if any(not _count(turn) or not _count(step) for turn,step in steps) or len(steps) != len(primary) or len(set(steps)) != len(steps) or set(steps) != set(closed_steps):
        return False
    assistants = [row['data'] for row in events if row.get('type') == 'assistant/message']
    if scenario == 'cancel':
        return not any(data.get('usage') is not None for data in assistants)
    if len(assistants) != len(primary):
        return False
    advertised_steps = {}
    for data, call, step in zip(assistants, primary, steps):
        message, response = data.get('message') or {}, call.get('response') or {}
        if (not _count(data.get('turn')) or not _count(data.get('step')) or (data.get('turn'), data.get('step')) != step or message.get('role') != 'assistant'
                or (message.get('source') or {}).get('model') != call.get('request', {}).get('model')
                or not _usage(data.get('usage'), call.get('usage'))):
            return False
        blocks = message.get('content')
        if not isinstance(blocks, list):
            return False
        native_calls = [block for block in blocks if isinstance(block, dict) and block.get('type') == 'tool-call']
        wire_calls = response.get('tool_calls') or []
        if len(native_calls) != len(wire_calls):
            return False
        for native, wire in zip(native_calls, wire_calls):
            function = wire.get('function') or {}
            if (native.get('id') != wire.get('id') or native.get('name') != function.get('name')
                    or not _same(_json(native.get('arguments')), _json(function.get('arguments')))):
                return False
            advertised_steps[native.get('id')] = step
        # A tool call may also contain assistant text. Bind that text for every
        # completed response; only absent text and the host's empty text agree.
        wire_text = response.get('content')
        if wire_text is None:
            wire_text = ''
        if not isinstance(wire_text, str) or _text(blocks) != wire_text:
            return False
    advertised = [tool for call in primary for tool in (call.get('response') or {}).get('tool_calls', [])]
    native_calls = [row['data'] for row in events if row.get('type') == 'tool/call']
    if len(native_calls) != len(advertised):
        return False
    for native, wire in zip(native_calls, advertised):
        function = wire.get('function') or {}
        if (native.get('callId') != wire.get('id') or native.get('name') != function.get('name')
                or not _same(_json(native.get('arguments')), _json(function.get('arguments')))
                or (native.get('turn'), native.get('step')) != advertised_steps.get(wire.get('id'))):
            return False
    results = []
    for row in events:
        if row.get('type') != 'tool/result':
            continue
        message = row['data'].get('message') or {}
        if message.get('role') != 'tool' or not isinstance(message.get('content'), list):
            return False
        if isinstance(message.get('toolCallId'), str):
            results.append({'toolCallId': message['toolCallId'], 'content': message['content'],
                            'isError': message.get('isError')})
        else:
            results.extend(block for block in message['content'] if isinstance(block, dict) and block.get('type') == 'tool-result')
    by_id = {block.get('toolCallId'): block for block in results}
    if len(results) != len(by_id) or set(by_id) != {tool.get('id') for tool in advertised}:
        return False
    tools = {span.get('attributes', {}).get('gen_ai.tool.call.id'): span for span in spans
             if span.get('attributes', {}).get('gen_ai.span.kind') == 'TOOL'
             or span.get('attributes', {}).get('gen_ai.operation.name') == 'execute_tool'}
    for call_id, block in by_id.items():
        text = _text(block.get('content'))
        native = tools.get(call_id, {}).get('attributes', {})
        if text is None or native.get('gen_ai.tool.call.result') != text:
            return False
        wire_results = [item for call in primary for item in call.get('request', {}).get('messages', [])
                        if item.get('role') == 'tool' and item.get('tool_call_id') == call_id]
        if not wire_results or not any(item.get('content') == text for item in wire_results):
            return False
    token = 'receipt: ' + receipt
    return any(token in (_text(block.get('content')) or '') and any(
        item.get('role') == 'tool' and item.get('tool_call_id') == call_id and token in str(item.get('content', ''))
        for call in primary for item in call.get('request', {}).get('messages', []))
        for call_id, block in by_id.items())


def _messages(value):
    """Compare message facts; compact JSON arguments and empty tool text are equivalent."""
    value = _json(value)
    if not isinstance(value, list): return None
    out = []
    for message in value:
        if not isinstance(message, dict): return None
        item = dict(message)
        if item.get('role') == 'assistant' and item.get('tool_calls') and item.get('content') is None:
            item['content'] = ''
        if 'tool_calls' in item:
            calls = []
            for call in item['tool_calls']:
                if not isinstance(call, dict) or not isinstance(call.get('function'), dict): return None
                calls.append({'id': call.get('id'), 'type': call.get('type'), 'function': {'name': call['function'].get('name'),
                    'arguments': _json(call['function'].get('arguments'))}})
            item['tool_calls'] = calls
        out.append(item)
    return out


def _input_matches(attrs, call):
    messages = call.get('request', {}).get('messages')
    if not isinstance(messages, list) or not _same(_messages(attrs.get('gen_ai.input.messages')), _messages(messages)): return False
    request=call.get('request') or {}
    for field,value in (('gen_ai.request.max_tokens',request.get('max_completion_tokens',request.get('max_tokens'))),
                         ('gen_ai.request.temperature',request.get('temperature'))):
        if value is None:
            if field in attrs: return False
        elif isinstance(value,bool) or type(attrs.get(field)) is not type(value) or attrs[field]!=value: return False
    stop=request.get('stop')
    if stop is not None:
        if _json(attrs.get('gen_ai.request.stop_sequences'))!=stop: return False
    elif 'gen_ai.request.stop_sequences' in attrs: return False
    systems = [m.get('content') for m in messages if m.get('role') == 'system']
    return all(isinstance(text, str) for text in systems) and _json(attrs.get('gen_ai.system_instructions')) == ('\n\n'.join(systems) if systems else None)


def native_result_output_matches(result):
    """AgentResult joins received logical text blocks; resume can expose a tail."""
    details=result.get('details') or {}; canonical=details.get('canonical_events')
    if not isinstance(canonical,list): return False
    rows=[row for row in canonical if row.get('type')=='assistant/message']
    logical=details.get('logical_events')
    if logical is not None:
        if not isinstance(logical,list): return False
        selected=[row for row in logical if row.get('type')=='assistant/message']
        position=-1
        for row in selected:
            matches=[i for i,event in enumerate(rows) if _same(row,event)]
            if len(matches)!=1 or matches[0]<=position: return False
            position=matches[0]
        rows=selected
    texts=[block['text'] for row in rows for block in row.get('data',{}).get('message',{}).get('content',[])
           if block.get('type')=='text' and isinstance(block.get('text'),str)]
    return type(result.get('output')) is str and result['output']=='\n'.join(texts)


def physical_transport_matches(calls, *, cancelled=False):
    """Fake HTTP terminal facts are independent of native host completion."""
    if not calls: return False
    for call in calls:
        if type(call.get('status')) is not int or call['status']!=200: return False
        if 'client_disconnected' in call and type(call['client_disconnected']) is not bool: return False
        if cancelled:
            if call.get('finish_reason') is not None or ('completed' in call and call['completed'] is not False): return False
        else:
            # The fixture records this field only after the actual terminal
            # response write/flush succeeds. Missing finish is never stop.
            expected='tool_calls' if (call.get('response') or {}).get('tool_calls') else 'stop'
            if (call.get('finish_reason')!=expected or ('completed' in call and call['completed'] is not True)
                    or call.get('client_disconnected',False) is not False): return False
    return True


def _unavailable_output(attrs):
    return ('gen_ai.output.messages' not in attrs or attrs['gen_ai.output.messages']=='[REDACTED]'
            or _json(attrs['gen_ai.output.messages'])==[])


def native_io_matches(result, calls, spans, *, cancelled=False):
    """Bind every native generation's full available IO and identity to its sources."""
    events = (result.get('details') or {}).get('canonical_events')
    if not isinstance(events, list): return False
    assistants = [r.get('data') or {} for r in events if r.get('type') == 'assistant/message']
    generations = sorted([s for s in spans if s.get('attributes', {}).get('gen_ai.span.kind') == 'LLM'],
                         key=lambda s: s.get('start_time_unix_nano', 0))
    if len(generations) != len(calls) or (not cancelled and len(assistants) != len(calls)): return False
    if cancelled:
        steps = [r.get('data') or {} for r in events if r.get('type') == 'step/start']
        if len(steps) != len(generations): return False
        for step, call, span in zip(steps, calls, generations):
            attrs = span.get('attributes') or {}
            if not _count(attrs.get('agentic.turn.number')) or attrs.get('agentic.turn.number') != step.get('turn') or not _input_matches(attrs, call): return False
            if call.get('finish_reason') is not None or _json(attrs.get('gen_ai.response.finish_reasons'))!=['aborted']: return False
            output=_json(attrs.get('gen_ai.output.messages'))
            if output is not None and (not isinstance(output,list) or any(not isinstance(message,dict)
                    or ('finish_reason' in message and message['finish_reason']!='aborted') for message in output)): return False
            available=any(a.get('turn')==step.get('turn') and a.get('step')==step.get('step') for a in assistants)
            if not available and not _unavailable_output(attrs): return False
    for native, call, span in zip(assistants, calls, generations):
        attrs = span.get('attributes') or {}; message = native.get('message') or {}; source = message.get('source') or {}
        response = (source.get('replayState') or {}).get('response') or {}
        response_model = response.get('responseModel') or source.get('model')
        if attrs.get('gen_ai.response.model') != response_model or not _count(attrs.get('agentic.turn.number')) or attrs.get('agentic.turn.number') != native.get('turn'): return False
        if response.get('responseId') and attrs.get('gen_ai.response.id') != response['responseId']: return False
        if not _input_matches(attrs, call): return False
        output = _json(attrs.get('gen_ai.output.messages'))
        if not isinstance(output, list) or len(output) != 1 or output[0].get('role') != 'assistant': return False
        actual = dict(output[0]); actual.pop('finish_reason', None)
        blocks = message.get('content') or []
        expected = {'role': 'assistant', 'content': _text(blocks) or ''}
        tools = [b for b in blocks if b.get('type') == 'tool-call']
        if tools: expected['tool_calls'] = [{'id': b.get('id'), 'type': 'function', 'function': {'name': b.get('name'), 'arguments': _json(b.get('arguments'))}} for b in tools]
        reasoning = ''.join(b.get('text', '') for b in blocks if b.get('type') == 'reasoning')
        if reasoning: expected['reasoning_content'] = reasoning
        if not _same(_messages([actual]), _messages([expected])): return False
        if cancelled:
            # The provider stream was interrupted before a terminal finish.
            # Native user-abort is an observed host terminal, never provider stop.
            if native.get('interrupted') is not True or call.get('finish_reason') is not None: return False
            reason='aborted'
        else:
            reason = call.get('finish_reason') or ('tool_calls' if tools else 'stop')
        if response.get('stopReason') and {'toolUse': 'tool_calls'}.get(response['stopReason'], response['stopReason']) != reason: return False
        if _json(attrs.get('gen_ai.response.finish_reasons')) != [reason]: return False
        if 'finish_reason' in output[0] and output[0]['finish_reason'] != reason: return False
    if not native_result_output_matches(result): return False
    for span in spans:
        attrs = span.get('attributes') or {}
        if attrs.get('gen_ai.span.kind') == 'TOOL':
            tool = next((r.get('data') for r in events if r.get('type') == 'tool/call' and r.get('data', {}).get('callId') == attrs.get('gen_ai.tool.call.id')), None)
            if not _count(attrs.get('agentic.turn.number')) or not tool or attrs.get('gen_ai.tool.name') != tool.get('name') or attrs.get('agentic.turn.number') != tool.get('turn'): return False
    # Human messages are distinct from host-injected runtime-context snapshots.
    active = None; users = {}
    for row in events:
        data = row.get('data') or {}
        if row.get('type') == 'turn/start': active = data.get('turn')
        if row.get('type') == 'user/message' and (data.get('source') or {}).get('kind') == 'user': users.setdefault(active, []).append({'role': 'user', 'content': _text(data.get('content'))})
    for root in [s for s in spans if s.get('attributes', {}).get('gen_ai.span.kind') == 'CHAT']:
        attrs = root.get('attributes') or {}; turn = attrs.get('agentic.turn.number')
        if not _count(turn): return False
        if users.get(turn) and _json(attrs.get('gen_ai.input.messages')) != users[turn]: return False
        local = [a for a in assistants if a.get('turn') == turn]
        if local and _json(attrs.get('gen_ai.output.messages')) != _json(next(s['attributes']['gen_ai.output.messages'] for a, s in zip(assistants, generations) if a is local[-1])): return False
        if cancelled and not local and not _unavailable_output(attrs): return False
    return True


def canonical_signal_usage_matches(result, spans, logs, *, cancelled=False):
    """Native canonical reportedness and every emitted usage alias must agree."""
    events=(result.get('details') or {}).get('canonical_events')
    if not isinstance(events,list): return False
    assistants=[r.get('data') or {} for r in events if r.get('type')=='assistant/message']
    generations=sorted([s for s in spans if s.get('attributes',{}).get('gen_ai.span.kind')=='LLM'],key=lambda s:s.get('start_time_unix_nano',0))
    steps=[r.get('data') or {} for r in events if r.get('type')=='step/start']
    sources=steps if cancelled and not assistants else assistants
    if len(sources)!=len(generations): return False
    by_id={(s.get('trace_id'),s.get('span_id')):source for s,source in zip(generations,sources)}
    api=[r for r in logs if r.get('attributes',{}).get('event.name')=='api_request']
    for row in generations+api:
        attrs=row.get('attributes') or {}; source=by_id.get((row.get('trace_id'),row.get('span_id')))
        if not source or not _count(attrs.get('agentic.turn.number')) or attrs.get('agentic.turn.number')!=source.get('turn'): return False
        message=source.get('message') or {}; origin=message.get('source') or {}; response=(origin.get('replayState') or {}).get('response') or {}
        if origin and attrs.get('gen_ai.response.model')!=(response.get('responseModel') or origin.get('model')): return False
        native=source.get('usage')
        fields={'gen_ai.usage.input_tokens':None,'gen_ai.usage.output_tokens':None,'gen_ai.usage.total_tokens':None,
            'agentic.usage.fresh_input_tokens':None,'gen_ai.usage.cache_read.input_tokens':None,'agentic.usage.cache_read_tokens':None,
            'gen_ai.usage.cache_creation.input_tokens':None,'agentic.usage.cache_write_tokens':None,
            'gen_ai.usage.reasoning.output_tokens':None,'agentic.usage.reasoning_tokens':None}
        if native is not None:
            if not isinstance(native,dict) or not _count(native.get('inputTokens')) or not _count(native.get('outputTokens')): return False
            fields.update({'gen_ai.usage.input_tokens':native['inputTokens']+native.get('cacheReadTokens',0)+native.get('cacheWriteTokens',0),
                'gen_ai.usage.output_tokens':native['outputTokens'],'gen_ai.usage.total_tokens':native.get('totalTokens',native['inputTokens']+native['outputTokens']+native.get('cacheReadTokens',0)+native.get('cacheWriteTokens',0)),
                'agentic.usage.fresh_input_tokens':native['inputTokens']})
            for key,aliases in (('cacheReadTokens',('gen_ai.usage.cache_read.input_tokens','agentic.usage.cache_read_tokens')),
                                ('cacheWriteTokens',('gen_ai.usage.cache_creation.input_tokens','agentic.usage.cache_write_tokens')),
                                ('reasoningTokens',('gen_ai.usage.reasoning.output_tokens','agentic.usage.reasoning_tokens'))):
                if key in native:
                    if not _count(native[key]): return False
                    for alias in aliases: fields[alias]=native[key]
        for field,value in fields.items():
            if value is None:
                if field in attrs and not (native is None and field in ('gen_ai.usage.input_tokens','gen_ai.usage.output_tokens') and type(attrs[field]) is int and attrs[field]==0): return False
            elif not _count(attrs.get(field)) or attrs[field]!=value: return False
        for direction in ('input','output'):
            flag=attrs.get('agentic.usage.'+direction+'_tokens.missing')
            if not _flag(flag,native is None): return False
    return True


def canonical_token_metrics_match(result, metrics):
    """Require each reported DSH direction's actual samples, including zeros."""
    from copy import deepcopy
    from verify import token_metric_checks
    events=(result.get('details') or {}).get('canonical_events')
    if not isinstance(events,list): return False
    calls=[]
    for row in events:
        if row.get('type')!='assistant/message': continue
        native=row.get('data',{}).get('usage')
        if native is None:
            calls.append({'usage':None}); continue
        if not isinstance(native,dict) or not _count(native.get('inputTokens')) or not _count(native.get('outputTokens')): return False
        cache={wire:native[field] for wire,field in (('cached_tokens','cacheReadTokens'),('cache_write_tokens','cacheWriteTokens')) if field in native}
        source={'prompt_tokens':native['inputTokens']+native.get('cacheReadTokens',0)+native.get('cacheWriteTokens',0),'completion_tokens':native['outputTokens']}
        if cache: source['prompt_tokens_details']=cache
        calls.append({'usage':source})
    directions={'input','output'} if any(call['usage'] is not None for call in calls) else set()
    for name,inclusive in (('agent.token.usage',False),('gen_ai.client.token.usage',True)):
        rows=[m for m in metrics if m.get('name')==name]
        if {m.get('attributes',{}).get('gen_ai.token.type',m.get('attributes',{}).get('type')) for m in rows}!=directions: return False
        if any(m.get('kind')!='histogram' for m in rows): return False
        selected=[dict(m,name='agent.token.usage') for m in rows]
        expected=deepcopy(calls)
        if inclusive:
            for call in expected:
                if call['usage'] is not None: call['usage'].pop('prompt_tokens_details',None)
        else: selected += [m for m in metrics if m.get('name') in ('agent.cache.read_tokens','agent.cache.write_tokens')]
        if not token_metric_checks(selected,expected): return False
    return True
