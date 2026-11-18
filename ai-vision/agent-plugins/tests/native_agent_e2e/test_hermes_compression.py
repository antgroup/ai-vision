"""Compression proof must reject unchanged history and invented turn identities."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

from test_hermes_turn_numbering import proof as numbering_proof, complete_call_facts


def proof():
    report = numbering_proof()
    report['prompts'] = ['Read receipt first.', 'Read receipt second.', 'Read receipt third.']
    details = report['result']['details']
    details['acp_processes'] = [
        {'pid': 101, 'exit_code': 0, 'turns': [1, 2]},
        {'pid': 102, 'exit_code': 0, 'turns': [3]},
    ]
    details['compression'] = {'trigger': 'threshold', 'process_pid': 102,
        'context_before': 'Conversation: 10 messages',
        'config': {'enabled': True, 'in_place': True, 'threshold_tokens': 3000}}
    raw = details['hermes_raw_callbacks']
    prompts = [row for row in raw if row['hook'] == 'pre_llm_call']
    first, second, third = report['prompts']
    histories = [
        [{'role': 'user', 'content': first}],
        [{'role': 'user', 'content': first}, {'role': 'assistant', 'content': 'receipt'},
         {'role': 'user', 'content': second}],
        [{'role': 'system', 'content': 'system'},
         {'role': 'user', 'content': '[CONTEXT SUMMARY]: Earlier receipt read.', '_compressed_summary': True},
         {'role': 'user', 'content': second}, {'role': 'assistant', 'content': 'receipt'},
         {'role': 'assistant', 'content': 'done'}, {'role': 'user', 'content': third}],
    ]
    for row, text, history in zip(prompts, report['prompts'], histories):
        row['rawInput'].update(user_message=text, conversation_history=history)
    uncompressed = [
        {'role': 'user', 'content': first}, {'role': 'assistant', 'content': 'calling file tool'},
        {'role': 'tool', 'content': 'receipt'}, {'role': 'assistant', 'content': 'partial'},
        {'role': 'user', 'content': '[System: continue]'}, {'role': 'assistant', 'content': 'receipt'},
        {'role': 'user', 'content': second}, {'role': 'assistant', 'content': 'calling file tool'},
        {'role': 'tool', 'content': 'receipt'}, {'role': 'assistant', 'content': 'receipt'},
        {'role': 'user', 'content': third},
    ]
    prompts[-1]['rawInput']['conversation_history'] = uncompressed
    tool_call = deepcopy(report['requests'][-2]['response'])
    tool_result = {'role': 'tool', 'tool_call_id': 'call_native_5', 'content': 'receipt: abc123'}
    compressed = histories[-1] + [tool_call, tool_result]
    report['requests'][-2]['request']['messages'] = uncompressed
    report['requests'][-1]['request']['messages'] = compressed
    raw.append({'hook': 'post_llm_call', 'capturedAt': '2026-10-15T00:00:04Z',
                'rawInput': {'turn_id': 'turn3', 'conversation_history': compressed + [report['requests'][-1]['response']]}})
    exports = report['exports'][0]
    generation = deepcopy(exports['spans'][-1])
    generation.update(span_id='summary-generation', start_time_unix_nano=301_500_000_000,
                      end_time_unix_nano=301_600_000_000)
    summary_request = [{'role': 'system', 'content': 'Summarize the earlier conversation.'}]
    summary_response = {'role': 'assistant', 'content': 'Earlier receipt read.'}
    generation['attributes'].update({'gen_ai.agent.name': 'compression', 'agentic.model_call.id': 'compression-api',
        'gen_ai.input.messages': json.dumps(summary_request), 'gen_ai.output.messages': json.dumps([summary_response]),
        'gen_ai.usage.input_tokens': 20, 'gen_ai.usage.output_tokens': 4})
    exports['spans'].append(generation)
    report['requests'].append({'purpose': 'auxiliary', 'status': 200, 'finish_reason': 'stop',
        'request': {'model': 'native-e2e-chat', 'messages': summary_request}, 'response': summary_response,
        'usage': {'prompt_tokens': 20, 'completion_tokens': 4, 'total_tokens': 24}})
    for hook, timekey, timestamp in [('pre_auxiliary_call', 'started_at', 301.5), ('post_auxiliary_call', 'ended_at', 301.6)]:
        raw.append({'hook': hook, 'capturedAt': '2026-10-15T00:00:03.5Z', 'rawInput': {
            'session_id': 'session', 'turn_id': 'turn3', 'api_request_id': 'compression-api',
            'aux_task': 'compression', timekey: timestamp}})
    for metric, count in zip(exports['metrics'], (8, 118, 32)):
        metric['point']['asInt'] = str(count)
    complete_call_facts(report)
    # Serialize the independent literal fixture through the real OTLP decoder.
    # Arbitrary fixture IDs become valid hexadecimal IDs, preserving the graph.
    from native_capture_fixture import captured_native
    for row in exports['logs']:
        row.setdefault('trace_id', exports['spans'][0]['trace_id'])
        row.setdefault('span_id', exports['spans'][0]['span_id'])
    for row in exports['spans'] + exports['logs']:
        for key, length in (('trace_id', 32), ('span_id', 16), ('parent_span_id', 16)):
            if row.get(key):
                row[key] = hashlib.sha256(row[key].encode()).hexdigest()[:length]
    report['exports'] = captured_native(exports)
    return report


class CompressionOracleTests(unittest.TestCase):
    def assess(self, report):
        try:
            from hermes_compression import assess
        except ImportError:
            self.fail('The native compression probe must independently validate shortened raw hook history')
        return assess(report)

    def test_compressed_history_across_processes_keeps_three_ordinals(self):
        result = self.assess(proof())
        self.assertTrue(result['passed'], result)
        self.assertEqual(result['observed_numbers'], [1, 2, 3])
        self.assertEqual(result['compression_history']['removed_user_prompts'], ['Read receipt first.'])

    def test_original_otlp_payload_must_match_the_normalized_evidence(self):
        for mutation in ('missing-original', 'changed-original', 'changed-normalized'):
            report = proof()
            export = report['exports'][0]
            if mutation == 'missing-original': export.pop('otlp')
            elif mutation == 'changed-original':
                export['otlp']['resourceSpans'][0]['scopeSpans'][0]['spans'][0]['name'] = 'fabricated'
            else: export['spans'][0]['attributes']['fabricated.evidence'] = 'not in OTLP'
            with self.subTest(mutation=mutation):
                self.assertFalse(self.assess(report)['passed'])

    def test_boolean_ordinal_is_rejected_even_when_raw_and_normalized_exports_agree(self):
        from evidence_integrity import _decode_saved, exports_match_raw
        report = proof()
        for export in report['exports']:
            def replace(value):
                if isinstance(value, dict):
                    if value.get('key') == 'agentic.turn.number' and value.get('value') == {'intValue': '1'}:
                        value['value'] = {'boolValue': True}
                    for item in value.values(): replace(item)
                elif isinstance(value, list):
                    for item in value: replace(item)
            replace(export['otlp'])
            decoded = _decode_saved(export)
            for key in ('spans', 'logs', 'metrics'): export[key] = decoded[key]
        self.assertTrue(exports_match_raw(report['exports']))
        result = self.assess(report)
        self.assertFalse(result['checks']['observed_numbers'])
        self.assertFalse(result['checks']['compression.child_ordinals'])

    def test_summary_complete_input_and_output_must_match_wire_even_when_otlp_is_valid(self):
        from evidence_integrity import _decode_saved, exports_match_raw
        for mutation in ('wire-input', 'wire-output', 'otlp-input', 'otlp-output'):
            report = proof()
            if mutation.startswith('wire'):
                wire = report['requests'][-1]
                if mutation.endswith('input'): wire['request']['messages'] = [{'role': 'user', 'content': 'unrelated'}]
                else: wire['response']['content'] = 'unrelated'
            else:
                export = report['exports'][0]
                field = 'gen_ai.' + ('input' if mutation.endswith('input') else 'output') + '.messages'
                attrs = export['otlp']['resourceSpans'][-1]['scopeSpans'][0]['spans'][0]['attributes']
                next(row for row in attrs if row['key'] == field)['value']['stringValue'] = '[{"role":"assistant","content":"unrelated"}]'
                decoded = _decode_saved(export)
                for key in ('spans', 'logs', 'metrics'): export[key] = decoded[key]
            with self.subTest(mutation=mutation):
                self.assertTrue(exports_match_raw(report['exports']))
                self.assertFalse(self.assess(report)['passed'])

    def test_rejects_trigger_config_without_compressed_native_history(self):
        for mutation in ('missing-summary', 'retained-first', 'unchanged-count', 'wrong-context', 'disabled'):
            report = proof()
            details = report['result']['details']
            third = next(row['rawInput']['conversation_history'] for row in details['hermes_raw_callbacks'] if row['hook'] == 'post_llm_call')
            if mutation == 'missing-summary': third[1].pop('_compressed_summary')
            elif mutation == 'retained-first': third[3] = {'role': 'user', 'content': report['prompts'][0]}
            elif mutation == 'unchanged-count': details['compression']['context_before'] = 'Conversation: 5 messages'
            elif mutation == 'wrong-context': details['compression']['context_before'] = 'Conversation unavailable'
            else: details['compression']['config']['enabled'] = False
            with self.subTest(mutation=mutation):
                self.assertFalse(self.assess(report)['passed'])

    def test_rejects_control_turn_process_reuse_and_reset_ordinal(self):
        for mutation in ('extra-turn', 'same-process', 'failed-process', 'reset-ordinal', 'wrong-user'):
            report = proof()
            details = report['result']['details']
            raw = details['hermes_raw_callbacks']
            if mutation == 'extra-turn':
                control = deepcopy(next(row for row in raw if row['hook'] == 'pre_llm_call'))
                control['rawInput'].update(turn_id='control-turn', user_message='/compress')
                raw.append(control)
            elif mutation == 'same-process': details['acp_processes'][1]['pid'] = 101
            elif mutation == 'failed-process': details['acp_processes'][1]['exit_code'] = 1
            elif mutation == 'reset-ordinal':
                next(row for row in report['exports'][0]['spans'] if row.get('name') == 'Turn 3')['attributes']['agentic.turn.number'] = 1
            else: next(row for row in raw if row['hook'] == 'pre_llm_call')['rawInput']['user_message'] = 'invented'
            with self.subTest(mutation=mutation):
                self.assertFalse(self.assess(report)['passed'])

    def test_rejects_unscoped_summary_unchanged_api_history_and_wrong_child_ordinal(self):
        for mutation in ('summary-turn', 'missing-summary-call', 'unchanged-api-history', 'child-ordinal'):
            report = proof()
            raw = report['result']['details']['hermes_raw_callbacks']
            aux = next(row for row in raw if row['hook'] == 'pre_auxiliary_call')
            if mutation == 'summary-turn': aux['rawInput']['turn_id'] = 'turn2'
            elif mutation == 'missing-summary-call': raw.remove(aux)
            elif mutation == 'child-ordinal': report['exports'][0]['spans'][-1]['attributes']['agentic.turn.number'] = 1
            else:
                calls = [row for row in raw if row['hook'] == 'pre_api_request' and row['rawInput']['turn_id'] == 'turn3']
                calls[-1]['rawInput']['request_messages'] = calls[0]['rawInput']['request_messages']
            with self.subTest(mutation=mutation):
                assessed = self.assess(report)
                self.assertFalse(assessed['passed'])
                if mutation == 'unchanged-api-history':
                    self.assertFalse(assessed['checks']['compression.raw_api_history_shortened'])
                elif mutation == 'child-ordinal':
                    self.assertFalse(assessed['checks']['compression.child_ordinals'])


class CompressionDriverTests(unittest.TestCase):
    def setUp(self):
        from models import CaseContext
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cli = self.root / 'acp.py'
        self.cli.write_text('''import json, os, sys
from pathlib import Path
if '--version' in sys.argv:
    print('Hermes Agent v0.21.5')
    raise SystemExit(0)
assert sys.argv[1:] == ['acp']
home = Path(os.environ['HERMES_HOME'])
config = json.loads((home / 'config.yaml').read_text())
assert config['plugins']['enabled'] == ['observ-hermes', 'hermes-replay']
assert config['compression']['enabled'] is (home / 'native-session').exists()
for line in sys.stdin:
    request = json.loads(line)
    result = {}
    if request['method'] == 'session/new':
        (home / 'native-session').write_text('native-session')
        result = {'sessionId': 'native-session'}
    if request['method'] == 'session/load':
        assert request['params']['sessionId'] == (home / 'native-session').read_text()
        result = {'_meta': {'hermes': {'sessionProvenance': {
            'acpSessionId': 'native-session', 'currentHermesSessionId': 'native-session'}}}}
    if request['method'] == 'session/prompt':
        assert request['params']['sessionId'] == 'native-session'
        text = request['params']['prompt'][0]['text']
        if text == '/context':
            print(json.dumps({'jsonrpc': '2.0', 'method': 'session/update', 'params': {
                'sessionId': 'native-session', 'update': {'sessionUpdate': 'agent_message_chunk',
                'content': {'type': 'text', 'text': 'Conversation: 10 messages'}}}}), flush=True)
        result = {'stopReason': 'end_turn'}
    print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], 'result': result}), flush=True)
''')
        self.ctx = CaseContext(repo=Path(__file__).resolve().parents[4], out=self.root / 'out',
            workspace=self.root / 'workspace', runtime=(sys.executable, str(self.cli)),
            runtime_version='0.21.5', scenario='multi-turn', prompt='First prompt', followup_prompt='Second prompt',
            provider_url='http://127.0.0.1:12345/v1', collector_url='http://127.0.0.1:12346', timeout_seconds=10)

    def run_driver(self):
        try:
            from drivers.hermes_compression import run_case
        except ImportError:
            self.fail('Compression driver must use official stdio requests across native process exits')
        return run_case(self.ctx, third_prompt='Third prompt')

    def test_two_processes_resume_then_compress_with_no_hidden_user_turn(self):
        result = self.run_driver()
        self.assertEqual(result.exit_code, 0, result)
        self.assertEqual(result.details['session_ids'], ['native-session'] * 3)
        self.assertEqual(result.details['resumed_session_id'], 'native-session')
        processes = result.details['acp_processes']
        self.assertEqual([row['turns'] for row in processes], [[1, 2], [3]])
        self.assertNotEqual(processes[0]['pid'], processes[1]['pid'])
        sent = [row['message'] for row in result.details['acp_messages'] if row['direction'] == 'client']
        self.assertEqual([row['method'] for row in sent], ['initialize', 'session/new', 'session/prompt',
            'session/prompt', 'initialize', 'session/load', 'session/prompt', 'session/prompt'])
        self.assertEqual([row['params']['prompt'][0]['text'] for row in sent if row['method'] == 'session/prompt'],
            ['First prompt', 'Second prompt', '/context', 'Third prompt'])
        self.assertIn('10 messages', result.details['compression']['context_before'])
        self.assertEqual(result.details['compression']['config']['threshold_tokens'], 3000)

    def test_missing_native_context_prevents_unverifiable_compression(self):
        self.cli.write_text(self.cli.read_text().replace('Conversation: 10 messages', 'Conversation unavailable'))
        result = self.run_driver()
        self.assertNotEqual(result.exit_code, 0)
        self.assertEqual(result.details['session_ids'], ['native-session'] * 2)
        self.assertIn('Conversation unavailable', result.details['error'])

    def test_native_load_failure_is_not_treated_as_resumed_history(self):
        self.cli.write_text(self.cli.read_text().replace("assert request['params']['sessionId'] == (home / 'native-session').read_text()",
            "print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], 'error': {'code': -1, 'message': 'missing session'}}), flush=True)\n        continue"))
        result = self.run_driver()
        self.assertNotEqual(result.exit_code, 0)
        self.assertEqual(result.details['session_ids'], ['native-session'] * 2)
        self.assertIn('missing session', result.details['error'])

    def test_native_load_must_confirm_the_original_session(self):
        self.cli.write_text(self.cli.read_text().replace("'currentHermesSessionId': 'native-session'", "'currentHermesSessionId': 'different-session'"))
        result = self.run_driver()
        self.assertNotEqual(result.exit_code, 0)
        self.assertNotIn('resumed_session_id', result.details)
        self.assertIn('original native session', result.details['error'])


if __name__ == '__main__':
    unittest.main()
