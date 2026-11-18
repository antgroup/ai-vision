"""DSH source acceptance is derived from wire/native records, never plugin helpers."""
from copy import deepcopy
import json
import unittest

from test_verify import proof
from verify import verify_case
from dsh_source import canonical_matches, native_result_output_matches


def dsh_proof(app='dsh01'):
    value = proof()
    value['app'] = app
    value['result']['version'] = {'dsh01': '0.1.7-rc.2', 'dsh02': '0.2.0-rc.2'}[app]
    for index, call in enumerate(value['requests']):
        call.update(status=200, finish_reason='tool_calls' if index == 0 else 'stop')
    value['requests'][0]['response']['tool_calls'][0]['type'] = 'function'
    export = value['exports'][0]
    for row in export['spans'] + export['logs'] + export['metrics']:
        row['resource'].update({'ant.agent.type': 'dsh', 'ant.agent.product': 'DeepSeekHarness'})
        if 'scope_name' in row:
            row['scope_name'] = 'observ-dsh'
        if 'span_id' in row:
            row['attributes']['agentic.turn.number'] = 1
    export['logs'] = [{'resource': span['resource'], 'trace_id': span['trace_id'], 'span_id': span['span_id'],
        'attributes': {'event.name': 'api_request', 'gen_ai.request.model': 'native-e2e-chat',
                       'gen_ai.usage.input_tokens': 16, 'gen_ai.usage.output_tokens': 4}} for span in export['spans']
        if span['attributes']['gen_ai.span.kind'] == 'LLM']
    for metric in export['metrics']:
        if metric['name'] == 'agent.token.usage':
            metric['kind'] = 'histogram'
            metric['attributes'] = {'gen_ai.token.type': metric['attributes']['type']}
            metric['point'] = {'sum': int(metric['point']['asInt']), 'count': '2'}
    for metric in deepcopy(export['metrics'][1:]):
        metric['name']='gen_ai.client.token.usage'
        export['metrics'].append(metric)
    header = {'type': 'session', 'version': 4, 'id': 'session', 'createdAt': 1000,
              'isSeeded': False, 'delegationDepth': 0}
    tool = {'type': 'tool-call', 'id': 'call_native_1', 'name': 'read', 'arguments': '{"path":"receipt.txt"}'}
    events = [
        ('turn/start', {'turn': 1}), ('step/start', {'turn': 1, 'step': 1}),
        ('request/header', {'header': {'config': {'model': 'native-e2e-chat'}}}),
        ('assistant/message', {'turn': 1, 'step': 1,
            'message': {'role': 'assistant', 'content': [tool], 'source': {'kind': 'model', 'model': 'native-e2e-chat'}},
            'usage': {'inputTokens': 16, 'outputTokens': 4}}),
        ('tool/call', {'turn': 1, 'step': 1, 'callId': 'call_native_1', 'name': 'read', 'arguments': '{"path":"receipt.txt"}'}),
        ('tool/result', {'turn': 1, 'step': 1, 'message': {'role': 'tool', 'toolCallId': 'call_native_1',
            'content': [{'type': 'text', 'text': 'receipt: abc123'}],
            'source': {'kind': 'tool', 'callId': 'call_native_1'}}}),
        ('step/end', {'turn': 1, 'step': 1}), ('step/start', {'turn': 1, 'step': 2}),
        ('assistant/message', {'turn': 1, 'step': 2,
            'message': {'role': 'assistant', 'content': [{'type': 'text', 'text': 'receipt: abc123'}],
                        'source': {'kind': 'model', 'model': 'native-e2e-chat'}},
            'usage': {'inputTokens': 16, 'outputTokens': 4}}),
        ('step/end', {'turn': 1, 'step': 2}), ('turn/end', {'turn': 1, 'reason': {'kind': 'completed'}}),
    ]
    llms = [span for span in export['spans'] if span['attributes'].get('gen_ai.span.kind') == 'LLM']
    for index, span in enumerate(llms):
        call = value['requests'][index]; attrs = span['attributes']
        attrs.update({'gen_ai.usage.total_tokens':20,'agentic.usage.fresh_input_tokens':16,'agentic.usage.input_tokens.missing':'false','agentic.usage.output_tokens.missing':'false'})
        attrs.update({'gen_ai.response.model': 'native-e2e-chat',
            'gen_ai.response.finish_reasons': json.dumps(['tool_calls' if index == 0 else 'stop']),
            'gen_ai.input.messages': json.dumps(call['request']['messages'])})
        output = {'role': 'assistant', 'content': call['response'].get('content') or ''}
        if index == 0: output['tool_calls'] = call['response']['tool_calls']
        attrs['gen_ai.output.messages'] = json.dumps([output])
    for log in export['logs']:
        log['attributes'].update({'gen_ai.response.model':'native-e2e-chat','agentic.turn.number':1,'gen_ai.usage.total_tokens':20,'agentic.usage.fresh_input_tokens':16,'agentic.usage.input_tokens.missing':'false','agentic.usage.output_tokens.missing':'false'})
    export['spans'][0]['attributes']['gen_ai.output.messages'] = llms[-1]['attributes']['gen_ai.output.messages']
    export['spans'][2]['attributes']['gen_ai.tool.name'] = 'read'
    value['result']['details'].update(canonical_header=header,
        canonical_events=[{'type': kind, 'seq': seq, 'time': 1000 + seq, 'data': data}
                          for seq, (kind, data) in enumerate(events)])
    return value


class DshOracleTests(unittest.TestCase):
    def test_completed_assistant_text_matches_transport_even_with_tool_calls(self):
        for index in (0, 1):
            for content in ('Provider text absent from the host', 'receipt: abc123\n ', False, []):
                value = dsh_proof()
                value['requests'][index]['response']['content'] = content
                with self.subTest(index=index, content=content):
                    result = verify_case(**value)
                    self.assertFalse(result['verified'])
                    self.assertIn('dsh.canonical_source', result['failures'])
        for empty in (None, ''):
            value = dsh_proof()
            value['requests'][0]['response']['content'] = empty
            with self.subTest(empty=empty):
                self.assertTrue(verify_case(**value)['verified'])

    def test_every_completed_fake_dsh_call_needs_physical_terminal_success(self):
        value=dsh_proof()
        self.assertTrue(verify_case(**value)['verified'])
        for mutation in ('status','boolean_status','missing_status','finish','wrong_finish','incomplete','disconnect'):
            changed=deepcopy(value); call=changed['requests'][0]
            if mutation=='status': call['status']=500
            elif mutation=='boolean_status': call['status']=True
            elif mutation=='missing_status': call.pop('status')
            elif mutation=='finish': call.pop('finish_reason')
            elif mutation=='wrong_finish': call['finish_reason']='stop'
            elif mutation=='incomplete': call['completed']=False
            else: call['client_disconnected']=True
            with self.subTest(mutation=mutation): self.assertFalse(verify_case(**changed)['verified'])

    def test_cancel_interrupted_partial_is_aborted_without_provider_finish(self):
        value=dsh_proof(); value['scenario']='cancel'; value['result']['output']='pending '
        value['result']['details'].update(cancelled=True,native_abort=True)
        value['requests']=value['requests'][:1]; value['requests'][0].update(usage=None,finish_reason=None,client_disconnected=True); value['requests'][0]['request']['stream']=True
        export=value['exports'][0]; export['spans']=export['spans'][:2]; export['logs']=export['logs'][:1]; export['metrics']=export['metrics'][:1]; export['metrics'][0]['point']['asInt']='1'
        for row in (export['spans'][1],export['logs'][0]):
            for key in list(row['attributes']):
                if key.startswith('gen_ai.usage.') or key.startswith('agentic.usage.'): row['attributes'].pop(key)
            row['attributes'].update({'agentic.usage.input_tokens.missing':'true','agentic.usage.output_tokens.missing':'true'})
        output='[{"role":"assistant","content":"pending ","finish_reason":"aborted"}]'
        export['spans'][1]['attributes'].update({'gen_ai.output.messages':output,'gen_ai.response.finish_reasons':'["aborted"]','error.type':'cancelled'})
        export['spans'][1]['status']['code']=2; export['spans'][0]['attributes']['gen_ai.output.messages']=output
        events=[('turn/start',{'turn':1}),('step/start',{'turn':1,'step':1}),('assistant/message',{'turn':1,'step':1,'interrupted':True,
            'message':{'role':'assistant','source':{'kind':'model','model':'native-e2e-chat'},'content':[{'type':'text','text':'pending '}]}}),
            ('step/end',{'turn':1,'step':1}),('turn/end',{'turn':1,'reason':{'kind':'aborted','reason':{'kind':'user'}}})]
        value['result']['details']['canonical_events']=[{'type':kind,'seq':seq,'time':1000+seq,'data':data} for seq,(kind,data) in enumerate(events)]
        self.assertTrue(verify_case(**value)['verified'],verify_case(**value)['failures'])
        for mutation in ('false_interrupted','stop','completed_provider','redacted_partial'):
            changed=deepcopy(value)
            if mutation=='false_interrupted': changed['result']['details']['canonical_events'][2]['data']['interrupted']=False
            elif mutation=='stop': changed['exports'][0]['spans'][1]['attributes']['gen_ai.response.finish_reasons']='["stop"]'
            elif mutation=='redacted_partial': changed['exports'][0]['spans'][1]['attributes']['gen_ai.output.messages']='[REDACTED]'
            else: changed['requests'][0]['finish_reason']='stop'
            with self.subTest(mutation=mutation): self.assertFalse(verify_case(**changed)['verified'])

    def test_result_output_preserves_received_text_blocks_and_binds_logical_tail(self):
        first={'type':'assistant/message','seq':1,'time':1001,'data':{'turn':1,'step':1,'message':{'role':'assistant','content':[{'type':'text','text':'receipt: abc123 '}]}}}
        second=deepcopy(first); second.update(seq=2,time=1002); second['data']['turn']=2
        result={'output':'receipt: abc123 \nreceipt: abc123 ','details':{'canonical_events':[first,second],'logical_events':[first,second]}}
        self.assertTrue(native_result_output_matches(result))
        result['details']['logical_events']=[second]; result['output']='receipt: abc123 '
        self.assertTrue(native_result_output_matches(result))
        for mutation in ('text','duplicate','order','output'):
            changed=deepcopy(result)
            if mutation=='text': changed['details']['logical_events'][0]['data']['message']['content'][0]['text']='invented'
            elif mutation=='duplicate': changed['details']['logical_events']=[second,second]
            elif mutation=='order': changed['details']['logical_events']=[second,first]
            else: changed['output']='receipt: abc123'
            with self.subTest(mutation=mutation): self.assertFalse(native_result_output_matches(changed))

    def test_reported_zero_needs_actual_native_and_l1_histogram_samples(self):
        value=dsh_proof()
        self.assertTrue(verify_case(**value)['verified'])
        for reported_zero in (False,True):
            base=deepcopy(value)
            if reported_zero:
                base['scenario']='zero-usage'
                for call in base['requests']: call['usage']={'prompt_tokens':0,'completion_tokens':0,'total_tokens':0}
                for row in base['result']['details']['canonical_events']:
                    if row['type']=='assistant/message': row['data']['usage']={'inputTokens':0,'outputTokens':0,'totalTokens':0}
                for row in base['exports'][0]['spans']+base['exports'][0]['logs']:
                    for field in ('gen_ai.usage.input_tokens','gen_ai.usage.output_tokens','gen_ai.usage.total_tokens','agentic.usage.fresh_input_tokens'):
                        if field in row['attributes']: row['attributes'][field]=0
                for metric in base['exports'][0]['metrics'][1:]: metric['point']['sum']=0
            self.assertTrue(verify_case(**base)['verified'])
            for name in ('agent.token.usage','gen_ai.client.token.usage'):
                for mutation in ('all','one_direction','count','sum'):
                    changed=deepcopy(base); metrics=changed['exports'][0]['metrics']
                    if mutation in ('all','one_direction'):
                        changed['exports'][0]['metrics']=[m for m in metrics if m['name']!=name or mutation=='one_direction' and m['attributes'].get('gen_ai.token.type')!='input']
                    else:
                        metric=next(m for m in metrics if m['name']==name)
                        metric['point'][mutation]=1
                    with self.subTest(zero=reported_zero,name=name,mutation=mutation):
                        self.assertIn('dsh.canonical_token_metrics',verify_case(**changed)['failures'])

    def test_api_event_identity_and_all_canonical_usage_fields_cannot_be_fabricated(self):
        for field in ('gen_ai.response.model','agentic.turn.number','gen_ai.usage.total_tokens','agentic.usage.fresh_input_tokens','agentic.usage.reasoning_tokens'):
            value=dsh_proof(); value['exports'][0]['logs'][0]['attributes'][field]='invented' if field.endswith('model') else 99
            with self.subTest(field=field): self.assertFalse(verify_case(**value)['verified'])

    def test_cancel_retains_exact_available_input_system_and_turn_facts(self):
        value=dsh_proof(); value['scenario']='cancel'; value['result']['details']['cancelled']=True; value['result']['output']=''
        value['requests']=value['requests'][:1]; value['requests'][0].update(usage=None,finish_reason=None); value['requests'][0]['request']['stream']=True
        export=value['exports'][0]; export['spans']=export['spans'][:2]; export['logs']=export['logs'][:1]; export['metrics']=export['metrics'][:1]; export['metrics'][0]['point']['asInt']='1'
        attrs=export['spans'][1]['attributes']; attrs.pop('gen_ai.usage.input_tokens'); attrs.pop('gen_ai.usage.output_tokens'); attrs.update({'agentic.usage.input_tokens.missing':'true','agentic.usage.output_tokens.missing':'true','error.type':'cancelled','gen_ai.response.finish_reasons':'["aborted"]','gen_ai.output.messages':'[]'}); export['spans'][1]['status']['code']=2
        for row in (export['spans'][1], export['logs'][0]):
            for key in ('gen_ai.usage.input_tokens','gen_ai.usage.output_tokens','gen_ai.usage.total_tokens','agentic.usage.fresh_input_tokens'): row['attributes'].pop(key,None)
        export['logs'][0]['attributes'].update({'agentic.usage.input_tokens.missing':'true','agentic.usage.output_tokens.missing':'true'})
        export['spans'][0]['attributes']['gen_ai.output.messages']='[]'
        rows=[('turn/start',{'turn':1}),('step/start',{'turn':1,'step':1}),('step/end',{'turn':1,'step':1}),('turn/end',{'turn':1,'reason':{'kind':'aborted','reason':{'kind':'user'}}})]
        value['result']['details']['canonical_events']=[{'type':kind,'seq':seq,'time':1000+seq,'data':data} for seq,(kind,data) in enumerate(rows)]
        self.assertTrue(verify_case(**value)['verified'],verify_case(**value)['failures'])
        unavailable=deepcopy(value)
        for span in unavailable['exports'][0]['spans']: span['attributes']['gen_ai.output.messages']='[REDACTED]'
        self.assertTrue(verify_case(**unavailable)['verified'],verify_case(**unavailable)['failures'])
        for mutation in ('input','system','turn','params','finish','output_finish','output','root_output','invalid_output','provider_finish','status'):
            changed=deepcopy(value); a=changed['exports'][0]['spans'][1]['attributes']
            if mutation=='finish': a['gen_ai.response.finish_reasons']='["tool_calls"]'
            elif mutation=='output_finish': a['gen_ai.output.messages']='[{"role":"assistant","content":"","finish_reason":"stop"}]'
            elif mutation=='output': a['gen_ai.output.messages']='[{"role":"assistant","content":"invented answer"}]'
            elif mutation=='root_output': changed['exports'][0]['spans'][0]['attributes']['gen_ai.output.messages']='[{"role":"assistant","content":"invented answer"}]'
            elif mutation=='invalid_output': a['gen_ai.output.messages']='[invented-redaction]'
            elif mutation=='provider_finish': changed['requests'][0]['finish_reason']='stop'
            elif mutation=='status': changed['requests'][0]['status']=500
            else: a[{'input':'gen_ai.input.messages','system':'gen_ai.system_instructions','turn':'agentic.turn.number','params':'gen_ai.request.max_tokens'}[mutation]]=89 if mutation in ('turn','params') else '"invented"'
            with self.subTest(mutation=mutation): self.assertFalse(verify_case(**changed)['verified'])

    def test_native_io_response_identity_finish_reason_and_turn_are_exact(self):
        for mutation in ('response_model', 'answer', 'tool_name', 'turn', 'input', 'system', 'finish'):
            value=dsh_proof(); spans=value['exports'][0]['spans']; attrs=spans[1]['attributes']
            if mutation=='response_model': attrs['gen_ai.response.model']='invented'
            elif mutation=='answer': spans[3]['attributes']['gen_ai.output.messages']='[{"role":"assistant","content":"receipt: abc123 fabricated"}]'
            elif mutation=='tool_name': attrs['gen_ai.output.messages']='[{"role":"assistant","tool_calls":[{"id":"call_native_1","function":{"name":"imaginary-tool","arguments":"{}"}}]}]'
            elif mutation=='turn': attrs['agentic.turn.number']=89
            elif mutation=='input': attrs['gen_ai.input.messages']='[{"role":"user","content":"invented"}]'
            elif mutation=='system': attrs['gen_ai.system_instructions']='"invented"'
            else: attrs['gen_ai.response.finish_reasons']='["error"]'
            with self.subTest(mutation=mutation): self.assertFalse(verify_case(**value)['verified'])

    def test_native_cancel_cannot_be_an_unrelated_failed_or_system_aborted_turn(self):
        value = dsh_proof()
        events = [('turn/start', {'turn': 1}), ('step/start', {'turn': 1, 'step': 1}),
                  ('step/end', {'turn': 1, 'step': 1}),
                  ('turn/end', {'turn': 1, 'reason': {'kind': 'aborted', 'reason': {'kind': 'user'}}})]
        value['result']['details']['canonical_events'] = [
            {'type': kind, 'seq': seq, 'time': 1000 + seq, 'data': data}
            for seq, (kind, data) in enumerate(events)]
        spans = value['exports'][0]['spans'][:2]
        self.assertTrue(canonical_matches(value['result'], value['requests'][:1], spans,
                                          scenario='cancel', receipt='abc123'))
        for reason in ({'kind': 'error'}, {'kind': 'aborted', 'reason': {'kind': 'system'}}):
            changed = deepcopy(value['result'])
            changed['details']['canonical_events'][-1]['data']['reason'] = reason
            self.assertFalse(canonical_matches(changed, value['requests'][:1], spans,
                                              scenario='cancel', receipt='abc123'))

    def test_usage_presence_zero_cache_and_reasoning_keep_their_wire_meaning(self):
        for scenario in ('unknown-usage', 'zero-usage', 'cache-usage'):
            with self.subTest(scenario=scenario):
                value = dsh_proof()
                value['scenario'] = scenario
                export = value['exports'][0]
                native_usage = ({'inputTokens': 0, 'outputTokens': 0, 'totalTokens': 0,
                    'cacheReadTokens': 0, 'cacheWriteTokens': 0, 'reasoningTokens': 0} if scenario == 'zero-usage' else
                    {'inputTokens': 11, 'outputTokens': 4, 'totalTokens': 20,
                     'cacheReadTokens': 3, 'cacheWriteTokens': 2, 'reasoningTokens': 1})
                raw_usage = ({'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0,
                    'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0},
                    'completion_tokens_details': {'reasoning_tokens': 0}} if scenario == 'zero-usage' else
                    {'prompt_tokens': 16, 'completion_tokens': 4, 'total_tokens': 20,
                     'prompt_tokens_details': {'cached_tokens': 3, 'cache_write_tokens': 2},
                     'completion_tokens_details': {'reasoning_tokens': 1}})
                for call in value['requests']:
                    call['usage'] = None if scenario == 'unknown-usage' else deepcopy(raw_usage)
                for row in value['result']['details']['canonical_events']:
                    if row['type'] == 'assistant/message':
                        if scenario == 'unknown-usage': row['data'].pop('usage')
                        else: row['data']['usage'] = deepcopy(native_usage)
                for row in export['spans'] + export['logs']:
                    attrs = row['attributes']
                    if row in export['logs'] or attrs.get('gen_ai.span.kind') == 'LLM':
                        if scenario == 'unknown-usage':
                            for key in list(attrs):
                                if key.startswith('gen_ai.usage.') or key.startswith('agentic.usage.'): attrs.pop(key)
                            for field in ('input', 'output'):
                                attrs.pop('gen_ai.usage.' + field + '_tokens',None)
                                attrs['agentic.usage.' + field + '_tokens.missing'] = 'true'
                        else:
                            attrs.update({'gen_ai.usage.input_tokens': raw_usage['prompt_tokens'],
                                'gen_ai.usage.output_tokens': raw_usage['completion_tokens'],
                                'gen_ai.usage.cache_read.input_tokens': native_usage['cacheReadTokens'],
                                'gen_ai.usage.cache_creation.input_tokens': native_usage['cacheWriteTokens'],
                                'gen_ai.usage.reasoning.output_tokens': native_usage['reasoningTokens'],
                                'agentic.usage.reasoning_tokens': native_usage['reasoningTokens'],
                                'agentic.usage.cache_read_tokens':native_usage['cacheReadTokens'],
                                'agentic.usage.cache_write_tokens':native_usage['cacheWriteTokens'],
                                'gen_ai.usage.total_tokens':native_usage['totalTokens'],
                                'agentic.usage.fresh_input_tokens':native_usage['inputTokens']})
                if scenario == 'unknown-usage':
                    export['metrics'] = export['metrics'][:1]
                else:
                    export['metrics'][1]['point']['sum'] = 0 if scenario == 'zero-usage' else 22
                    export['metrics'][2]['point']['sum'] = 0 if scenario == 'zero-usage' else 8
                    export['metrics'][3]['point']['sum'] = 0 if scenario == 'zero-usage' else 32
                    export['metrics'][4]['point']['sum'] = 0 if scenario == 'zero-usage' else 8
                    for name, count in (('agent.cache.read_tokens', 0 if scenario == 'zero-usage' else 6),
                                        ('agent.cache.write_tokens', 0 if scenario == 'zero-usage' else 4)):
                        export['metrics'].append({'name': name, 'kind': 'sum', 'temporality': 2,
                            'resource': export['metrics'][0]['resource'], 'attributes': {}, 'point': {'asInt': str(count)}})
                self.assertTrue(verify_case(**value)['verified'], verify_case(**value)['failures'])
                changed = deepcopy(value)
                message = next(row for row in changed['result']['details']['canonical_events'] if row['type'] == 'assistant/message')
                if scenario == 'unknown-usage': message['data']['usage'] = {'inputTokens': 0, 'outputTokens': 0}
                elif scenario == 'zero-usage': message['data']['usage']['cacheReadTokens'] = False
                else: message['data']['usage']['inputTokens'] = 16
                self.assertIn('dsh.canonical_source', verify_case(**changed)['failures'])

    def test_both_pins_use_dsh_scope_product_and_exact_physical_api_events(self):
        for app in ('dsh01', 'dsh02'):
            with self.subTest(app=app):
                result = verify_case(**dsh_proof(app))
                self.assertTrue(result['verified'], result['failures'])
                self.assertTrue(result['checks'].get('events.api_request_inventory'))

    def test_dsh_version_labels_cannot_replace_native_identity_or_physical_events(self):
        for mutation, failure in (('scope', 'scope.native'), ('product', 'resource.identity'),
                                  ('duplicate', 'events.api_request_inventory'), ('unrelated', 'events.api_request_inventory')):
            with self.subTest(mutation=mutation):
                value = dsh_proof()
                export = value['exports'][0]
                if mutation == 'scope':
                    export['spans'][0]['scope_name'] = 'observ-dsh01'
                elif mutation == 'product':
                    export['spans'][0]['resource']['ant.agent.product'] = 'Pi'
                elif mutation == 'duplicate':
                    export['logs'].append(deepcopy(export['logs'][0]))
                else:
                    export['logs'][0]['span_id'] = 'unrelated-generation'
                self.assertIn(failure, verify_case(**value)['failures'])

    def test_histogram_token_sum_and_reported_call_count_are_both_required(self):
        for mutation in ('sum', 'count', 'fractional', 'missing_count', 'api_histogram'):
            with self.subTest(mutation=mutation):
                value = dsh_proof()
                metrics = value['exports'][0]['metrics']
                if mutation == 'api_histogram':
                    metrics[0].update(kind='histogram', point={'sum': 2, 'count': '2'})
                elif mutation == 'missing_count':
                    metrics[1]['point'].pop('count')
                else:
                    metrics[1]['point'][{'sum': 'sum', 'count': 'count', 'fractional': 'sum'}[mutation]] = {
                        'sum': 33, 'count': '1', 'fractional': 32.5}[mutation]
                self.assertIn('metrics.physical_calls' if mutation == 'api_histogram' else 'metrics.tokens',
                              verify_case(**value)['failures'])

    def test_correlated_api_event_cannot_replace_raw_token_usage_or_model(self):
        for field, value in (('gen_ai.usage.input_tokens', 0), ('gen_ai.usage.output_tokens', 9),
                             ('gen_ai.request.model', 'another-model')):
            with self.subTest(field=field):
                source = dsh_proof()
                source['exports'][0]['logs'][0]['attributes'][field] = value
                self.assertIn('events.api_request_facts', verify_case(**source)['failures'])

    def test_canonical_header_order_usage_tools_and_topology_are_independent_facts(self):
        for mutation in ('missing', 'session', 'seq', 'boolean_seq', 'usage', 'arguments', 'result', 'answer', 'turn', 'tool_turn', 'tool_span_result', 'model'):
            with self.subTest(mutation=mutation):
                value = dsh_proof()
                details = value['result']['details']
                rows = details['canonical_events']
                if mutation == 'missing':
                    details.pop('canonical_events')
                elif mutation == 'session':
                    details['canonical_header']['id'] = 'unrelated-session'
                elif mutation == 'seq':
                    rows[2]['seq'] = rows[1]['seq']
                elif mutation == 'boolean_seq':
                    rows[0]['seq'] = False
                elif mutation == 'usage':
                    rows[3]['data']['usage']['inputTokens'] = 0
                elif mutation == 'arguments':
                    rows[4]['data']['arguments'] = '{"path":"unrelated.txt"}'
                elif mutation == 'result':
                    rows[5]['data']['message']['content'][0]['text'] = 'no actual receipt'
                elif mutation == 'answer':
                    rows[8]['data']['message']['content'][0]['text'] = 'unrelated answer'
                elif mutation == 'turn':
                    value['exports'][0]['spans'][0]['attributes']['agentic.turn.number'] = 9
                elif mutation == 'tool_turn':
                    rows[4]['data']['turn'] = 9
                elif mutation == 'tool_span_result':
                    value['exports'][0]['spans'][2]['attributes']['gen_ai.tool.call.result'] = 'receipt: abc123 extra invented content'
                else:
                    rows[3]['data']['message']['source']['model'] = 'another-model'
                self.assertIn('dsh.canonical_source', verify_case(**value)['failures'])


if __name__ == '__main__':
    unittest.main()
