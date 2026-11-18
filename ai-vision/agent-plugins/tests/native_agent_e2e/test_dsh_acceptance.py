"""Bounded host loss never becomes a fully verified provider reconciliation."""
from copy import deepcopy
import json
import unittest

from source_acceptance import assess_source, bounded_row
from test_dsh_oracle import dsh_proof
from verify import acceptance_status
from native_capture_fixture import captured_native


def host_loss(scenario='unknown-usage', app='dsh01'):
    value = dsh_proof(app)
    value['scenario'] = scenario
    value['result']['version'] = {'dsh01': '0.1.7-rc.2', 'dsh02': '0.2.0-rc.2'}[app]
    export = value['exports'][0]
    unknown = scenario == 'unknown-usage'
    native = ({'inputTokens': 0, 'outputTokens': 0, 'totalTokens': 0} if unknown else
              {'inputTokens': 11, 'outputTokens': 4, 'totalTokens': 20, 'cacheReadTokens': 3, 'cacheWriteTokens': 2})
    for i, call in enumerate(value['requests']):
        call.update(status=200, completed=True, finish_reason='tool_calls' if i == 0 else 'stop')
        call['usage'] = None if unknown else {'prompt_tokens': 16, 'completion_tokens': 4, 'total_tokens': 20,
            'prompt_tokens_details': {'cached_tokens': 3, 'cache_write_tokens': 2},
            'completion_tokens_details': {'reasoning_tokens': 1}}
    for row in value['result']['details']['canonical_events']:
        if row['type'] == 'assistant/message': row['data']['usage'] = deepcopy(native)
    for row in export['spans'] + export['logs']:
        a = row['attributes']
        if a.get('gen_ai.span.kind') == 'LLM' or a.get('event.name') == 'api_request':
            a.update({'gen_ai.usage.input_tokens': 0 if unknown else 16,
                'gen_ai.usage.output_tokens': native['outputTokens'], 'gen_ai.usage.total_tokens': native['totalTokens'],
                'agentic.usage.fresh_input_tokens': native['inputTokens'],
                'agentic.usage.input_tokens.missing': 'false', 'agentic.usage.output_tokens.missing': 'false'})
            if not unknown: a.update({'gen_ai.usage.cache_read.input_tokens': 3, 'agentic.usage.cache_read_tokens': 3,
                'gen_ai.usage.cache_creation.input_tokens': 2, 'agentic.usage.cache_write_tokens': 2})
    export['metrics'][1]['point']['sum'] = 0 if unknown else 22
    export['metrics'][2]['point']['sum'] = 0 if unknown else 8
    export['metrics'][3]['point']['sum']=0 if unknown else 32
    export['metrics'][4]['point']['sum']=0 if unknown else 8
    if not unknown:
        for name, count in (('agent.cache.read_tokens', 6), ('agent.cache.write_tokens', 4)):
            export['metrics'].append({'name': name, 'kind': 'sum', 'temporality': 2,
                'resource': export['metrics'][0]['resource'], 'attributes': {}, 'point': {'asInt': str(count)}})
    return {'app': app, 'case': app + '-' + scenario, 'scenario': scenario, 'mode': 'fake',
            'version': value['result']['version'], 'receipt': value['receipt'], 'expected_resource': {},
            'result': value['result'], 'requests': value['requests'], 'exports': captured_native(export)}


class DshAcceptanceTests(unittest.TestCase):
    def test_usage_allowance_cannot_hide_native_response_text_loss(self):
        for scenario in ('unknown-usage', 'cache-usage'):
            for mutation in ('tool_call_text', 'answer_whitespace'):
                report = host_loss(scenario)
                self.assertTrue(assess_source(report)['accepted'])
                if mutation == 'tool_call_text':
                    report['requests'][0]['response']['content'] = 'Provider text accompanying its tool call'
                else:
                    assistants = [row for row in report['result']['details']['canonical_events']
                                  if row['type'] == 'assistant/message']
                    assistants[-1]['data']['message']['content'][0]['text'] += '\n '
                    report['result']['output'] += '\n '
                    flat = {key: [row for export in report['exports'] for row in export[key]]
                            for key in ('spans', 'logs', 'metrics')}
                    for span in flat['spans']:
                        attrs = span['attributes']
                        if attrs.get('gen_ai.span.kind') not in ('CHAT', 'LLM'):
                            continue
                        output = json.loads(attrs.get('gen_ai.output.messages', '[]'))
                        if output and output[0].get('content') == 'receipt: abc123':
                            output[0]['content'] += '\n '
                            attrs['gen_ai.output.messages'] = json.dumps(output)
                    report['exports'] = captured_native(flat)
                with self.subTest(scenario=scenario, mutation=mutation):
                    result = assess_source(report)
                    self.assertFalse(result['accepted'])
                    self.assertIn('dsh.canonical_source', result['canonical']['failures'])

    def test_terminal_write_and_completed_native_lifecycle_do_not_require_an_uncaptured_flag(self):
        report=host_loss()
        for call in report['requests']: call.pop('completed',None)
        self.assertTrue(assess_source(report)['accepted'])
        for mutation in ('finish','disconnect','explicit_incomplete','step','turn'):
            changed=deepcopy(report)
            if mutation=='finish': changed['requests'][0].pop('finish_reason')
            elif mutation=='disconnect': changed['requests'][0]['client_disconnected']=True
            elif mutation=='explicit_incomplete': changed['requests'][0]['completed']=False
            elif mutation=='step': changed['result']['details']['canonical_events']=[r for r in changed['result']['details']['canonical_events'] if r['type']!='step/end']
            else: changed['result']['details']['canonical_events'][-1]['data']['reason']={'kind':'error'}
            with self.subTest(mutation=mutation): self.assertFalse(assess_source(changed)['accepted'])

    def test_numeric_flags_and_boolean_turns_are_not_native_semantic_facts(self):
        for field,value in (('agentic.usage.input_tokens.missing',0),('agentic.usage.output_tokens.missing',0),('agentic.turn.number',True),('agentic.turn.number',1.0)):
            report=host_loss()
            flat={key:[row for e in report['exports'] for row in e[key]] for key in ('spans','logs','metrics')}
            for row in flat['spans']+flat['logs']:
                attrs=row['attributes']
                if field=='agentic.turn.number' or attrs.get('gen_ai.span.kind')=='LLM' or attrs.get('event.name')=='api_request': attrs[field]=value
            report['exports']=captured_native(flat)
            with self.subTest(field=field,value=value): self.assertFalse(assess_source(report)['accepted'])

    def test_raw_intvalue_boolean_cannot_become_a_reported_zero(self):
        report=host_loss()
        rows=report['exports'][0]['otlp']['resourceSpans']
        attrs=rows[1]['scopeSpans'][0]['spans'][0]['attributes']
        next(a for a in attrs if a['key']=='gen_ai.usage.input_tokens')['value']['intValue']=False
        self.assertFalse(assess_source(report)['accepted'])

    def test_explicit_live_model_reverification_preserves_local_smoke_contract(self):
        value=dsh_proof(); model='custom-live-model'
        for call in value['requests']: call['request']['model']=model
        for row in value['exports'][0]['spans']+value['exports'][0]['logs']:
            for field in ('gen_ai.request.model','gen_ai.response.model'):
                if field in row['attributes']: row['attributes'][field]=model
        for row in value['result']['details']['canonical_events']:
            if row['type']=='assistant/message': row['data']['message']['source']['model']=model
        report={'app':'dsh01','case':'dsh01-tool-loop','scenario':'tool-loop','mode':'nvidia','model':model,
            'version':value['result']['version'],'receipt':value['receipt'],'result':value['result'],
            'requests':value['requests'],'exports':captured_native(value['exports'][0])}
        self.assertTrue(assess_source(report)['provider_reconciled'])
        report['model']='wrong-configured-model'
        self.assertFalse(assess_source(report)['accepted'])
        report=host_loss(); report['model']=model
        self.assertFalse(assess_source(report)['accepted'])

    def test_raw_and_normalized_tampering_and_missing_original_never_qualify(self):
        for signal,key in enumerate(('spans','logs','metrics')):
            for raw in (False,True):
                report=host_loss(); export=report['exports'][signal]
                if raw:
                    root,scope,rows=(('resourceSpans','scopeSpans','spans'),('resourceLogs','scopeLogs','logRecords'),('resourceMetrics','scopeMetrics','metrics'))[signal]
                    export['otlp'][root][0][scope][0][rows][0]['fabricated']='wrong'
                    # Unknown extra OTLP fields are ignored by capture; mutate a decoded fact.
                    item=export['otlp'][root][0][scope][0][rows][0]
                    if signal==0: item['name']='fabricated'
                    elif signal==1: item['body']={'stringValue':'fabricated'}
                    else: item['name']='fabricated'
                else: export[key][0]['resource']['ant.agent.product']='invented'
                with self.subTest(signal=key,raw=raw):
                    result=assess_source(report); self.assertFalse(result['accepted']); self.assertIn('source.raw_integrity',result['source']['failures'])
        report=host_loss(); report['exports'][0].pop('otlp')
        self.assertFalse(assess_source(report)['accepted'])

    def test_exact_two_pins_and_two_host_losses_preserve_strict_failure_and_raw_facts(self):
        for app in ('dsh01', 'dsh02'):
            for scenario in ('unknown-usage', 'cache-usage'):
                report = host_loss(scenario, app); before = deepcopy(report)
                result = assess_source(report)
                self.assertFalse(result['provider_reconciled']); self.assertTrue(result['canonical_verified'], result)
                self.assertTrue(result['accepted'], result); self.assertEqual(result['status'], 'passed_with_known_limitations')
                self.assertFalse(result['source']['verified']); self.assertTrue(result['source']['failures'])
                self.assertEqual(report, before)
                self.assertEqual(result['known_limitations'][0]['calls'], [0, 1])

    def test_no_other_scene_version_mode_app_or_missing_evidence_can_be_accepted(self):
        for mutation in ('app', 'version', 'result_version', 'scenario', 'mode', 'header', 'events', 'exports', 'wire'):
            report = host_loss()
            if mutation in ('header', 'events'): report['result']['details'].pop('canonical_' + mutation)
            elif mutation == 'result_version': report['result']['version'] = '0.1.0-rc.8'
            elif mutation == 'wire': report['requests'] = []
            elif mutation == 'exports': report['exports'] = []
            else: report[mutation] = {'app': 'dsh017', 'version': '0.1.0-rc.8', 'scenario': 'zero-usage', 'mode': 'nvidia'}[mutation]
            with self.subTest(mutation=mutation): self.assertFalse(assess_source(report)['accepted'])

    def test_native_usage_and_every_available_signal_count_detail_must_match(self):
        for scenario in ('unknown-usage', 'cache-usage'):
            for mutation in ('input', 'total', 'cache', 'reasoning', 'presence', 'log', 'fresh', 'metric', 'l1', 'count', 'api_count', 'native'):
                report = host_loss(scenario); export = {'spans':report['exports'][0]['spans'],'logs':report['exports'][1]['logs'],'metrics':report['exports'][2]['metrics']}; llm = export['spans'][1]['attributes']
                if mutation == 'native':
                    next(r for r in report['result']['details']['canonical_events'] if r['type']=='assistant/message')['data']['usage']['inputTokens'] += 1
                elif mutation in ('metric', 'l1', 'count', 'api_count'):
                    index = {'metric': 1, 'l1': 3, 'count': 1, 'api_count': 0}[mutation]
                    point = export['metrics'][index]['point']; point['count' if mutation == 'count' else 'asInt' if mutation=='api_count' else 'sum'] = '9'
                elif mutation == 'log': export['logs'][0]['attributes']['gen_ai.usage.total_tokens'] = 99
                else: llm[{'input': 'gen_ai.usage.input_tokens', 'total': 'gen_ai.usage.total_tokens',
                    'cache': 'gen_ai.usage.cache_read.input_tokens', 'reasoning': 'gen_ai.usage.reasoning.output_tokens',
                    'presence': 'agentic.usage.input_tokens.missing', 'fresh': 'agentic.usage.fresh_input_tokens'}[mutation]] = 'true' if mutation == 'presence' else 99
                with self.subTest(scenario=scenario, mutation=mutation): self.assertFalse(assess_source(report)['accepted'])

    def test_unregistered_wire_loss_and_corrupted_tools_io_roots_are_rejected(self):
        for mutation in ('wire_count', 'tool', 'argument', 'result', 'answer', 'root', 'response_model', 'output', 'input', 'system', 'finish', 'turn'):
            report=host_loss('cache-usage'); spans=report['exports'][0]['spans']; attrs=spans[1]['attributes']
            if mutation=='wire_count': report['requests'][0]['usage']['prompt_tokens'] = 99
            elif mutation=='tool': spans[2]['attributes']['gen_ai.tool.name']='invented'
            elif mutation=='argument': spans[2]['attributes']['gen_ai.tool.call.arguments']='{}'
            elif mutation=='result': spans[2]['attributes']['gen_ai.tool.call.result'] += ' fabricated'
            elif mutation=='answer': report['result']['output'] += ' fabricated'
            elif mutation=='root': spans[1]['parent_span_id']='missing'
            elif mutation=='response_model': attrs['gen_ai.response.model']='invented'
            elif mutation=='output': spans[3]['attributes']['gen_ai.output.messages']='[{"role":"assistant","content":"receipt: abc123 fabricated"}]'
            elif mutation=='input': attrs['gen_ai.input.messages']='[{"role":"user","content":"invented"}]'
            elif mutation=='system': attrs['gen_ai.system_instructions']='"invented"'
            elif mutation=='finish': attrs['gen_ai.response.finish_reasons']='["error"]'
            else: attrs['agentic.turn.number']=89
            with self.subTest(mutation=mutation): self.assertFalse(assess_source(report)['accepted'])

    def test_summary_distinguishes_fully_verified_and_bounded_acceptance(self):
        report=host_loss(); result=assess_source(report)
        row={**result, 'case': report['case'], 'version': report['version'], 'verified': False, 'source_verified': False}
        self.assertTrue(bounded_row(row)); self.assertEqual(acceptance_status([row], {row['case']}), 'passed_with_known_limitations')
        for field in ('canonical_verified', 'provider_reconciled', 'source_verified', 'known_limitations', 'version'):
            changed=deepcopy(row); changed[field]={'canonical_verified':False,'provider_reconciled':True,'source_verified':True,'known_limitations':[],'version':'0.2.1'}[field]
            self.assertFalse(bounded_row(changed)); self.assertEqual(acceptance_status([changed], {changed['case']}), 'failed')


if __name__ == '__main__': unittest.main()
