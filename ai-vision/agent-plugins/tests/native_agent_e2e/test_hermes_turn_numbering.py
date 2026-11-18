"""Reject wrong source identity, time, per-call usage, and turn ordinals."""
import json
from copy import deepcopy
import unittest

from hermes_turn_numbering import assess
from test_verify import hermes_multiturn_proof


def complete_call_facts(report):
    """Expand the literal transport fixture with native pre/post and API logs."""
    export = report['exports'][0]
    spans = {row['attributes']['agentic.model_call.id']: row for row in export['spans']
             if row['attributes']['gen_ai.span.kind'] == 'LLM'}
    raw = report['result']['details']['hermes_raw_callbacks']
    for wire in report['requests']:
        if not wire['request']['messages']:
            wire['request']['messages'] = [{'role': 'user', 'content': 'Read receipt.txt and return its receipt.'}]
        wire['response'].setdefault('role', 'assistant')
        wire['response'].setdefault('content', None)
        for call in wire['response'].get('tool_calls', []):
            call.setdefault('type', 'function')
    for auxiliary, hook in ((False, 'pre_api_request'), (True, 'pre_auxiliary_call')):
        before = sorted((row['rawInput'] for row in raw if row['hook'] == hook), key=lambda row: row['started_at'])
        calls = [row for row in report['requests'] if (row.get('purpose') == 'auxiliary') == auxiliary]
        for source, wire in zip(before, calls):
            attrs = spans[source['api_request_id']]['attributes']
            source['request_messages'] = deepcopy(wire['request']['messages'])
            attrs['gen_ai.input.messages'] = json.dumps(wire['request']['messages'])
            attrs['gen_ai.output.messages'] = json.dumps([wire['response']])
            attrs['gen_ai.response.finish_reasons'] = json.dumps([wire['finish_reason']])
            for row in raw:
                if row['hook'] not in ('post_api_request', 'post_auxiliary_call') or row['rawInput']['api_request_id'] != source['api_request_id']:
                    continue
                terminal = row['rawInput']
                native = deepcopy(wire['response'])
                native['tool_calls'] = [{'id': item['id'], 'name': item['function']['name'],
                    'arguments': item['function']['arguments'], 'provider_data': None} for item in native.get('tool_calls', [])]
                terminal.update(finish_reason=wire['finish_reason'], response={'assistant_message': native})
                if not auxiliary:
                    terminal['assistant_message'] = {**deepcopy(native), 'usage': wire['usage'], 'finish_reason': wire['finish_reason'],
                        'provider_data': None, 'reasoning': None}
    export['logs'] = [{'trace_id': row['trace_id'], 'span_id': row['span_id'], 'resource': row['resource'],
        'scope_name': row['scope_name'], 'attributes': {**deepcopy(row['attributes']), 'event.name': 'api_request'}}
        for row in spans.values()]


def proof():
    report = hermes_multiturn_proof(length=True)
    report['result']['version'] = '0.21.5'
    report['result']['details']['session_ids'] = ['session'] * 3
    exports = report['exports'][0]
    # Reuse the ordinary two-turn transport fixture, adding a third tool turn.
    third = json.loads(json.dumps(exports['spans'][-4:]).replace('call_native_3', 'call_native_5'))
    for row in third:
        row['trace_id'] = 't3'
        row['span_id'] += '-third'
        if row['parent_span_id']: row['parent_span_id'] += '-third'
    exports['spans'].extend(third)
    report['requests'].extend(json.loads(json.dumps(report['requests'][-2:]).replace('call_native_3', 'call_native_5')))
    raw = []
    groups = [('t', 'turn1'), ('t2', 'turn2'), ('t3', 'turn3')]
    for ordinal, (trace, turn) in enumerate(groups, 1):
        raw.append({'hook':'pre_llm_call', 'capturedAt':f'2026-10-15T00:00:0{ordinal}Z',
                    'rawInput':{'session_id':'session', 'turn_id':turn}})
        for index, row in enumerate(item for item in exports['spans'] if item['trace_id'] == trace):
            row['attributes'].update({'gen_ai.chat.id':turn, 'agentic.turn.id':turn, 'agentic.turn.number':ordinal})
            row['start_time_unix_nano'] = (ordinal * 100 + index) * 1_000_000_000
            row['end_time_unix_nano'] = row['start_time_unix_nano'] + 1_000_000_000
            if row['attributes']['gen_ai.span.kind'] == 'CHAT':
                row['name'] = f'Turn {ordinal}'
                row['end_time_unix_nano'] += 10_000_000_000
    generations = [row for row in exports['spans'] if row['attributes']['gen_ai.span.kind'] == 'LLM']
    for index, (row, wire) in enumerate(zip(generations, report['requests'])):
        wire['status'] = 200
        row['attributes']['agentic.model_call.id'] = f'api-{index}'
        # Deliberately different values: swapping calls preserves the sum but is wrong.
        row['attributes']['gen_ai.usage.input_tokens'] = wire['usage']['prompt_tokens'] = index + 11
        wire['usage']['total_tokens'] = index + 15
        shared = {'session_id':'session', 'turn_id':row['attributes']['gen_ai.chat.id'], 'api_request_id':f'api-{index}'}
        for hook, timekey, field in [('pre_api_request', 'started_at', 'start_time_unix_nano'),
                                     ('post_api_request', 'ended_at', 'end_time_unix_nano')]:
            raw.append({'hook':hook, 'capturedAt':'2026-10-15T00:00:00Z', 'rawInput':{**shared, timekey:row[field] / 1_000_000_000}})
    for metric, count in zip(exports['metrics'], (7, 98, 28)):
        metric['point']['asInt'] = str(count)
    report['result']['details']['hermes_raw_callbacks'] = raw
    complete_call_facts(report)
    return report


class TurnNumberingOracleTests(unittest.TestCase):
    def test_three_turn_native_fixture_passes_shared_oracle(self):
        result = assess(proof())
        self.assertTrue(result['passed'], result)

    def test_boolean_child_ordinal_cannot_match_parent_integer(self):
        report = proof()
        for row in report['exports'][0]['spans'] + report['exports'][0]['logs']:
            attrs = row['attributes']
            if attrs.get('gen_ai.span.kind') == 'LLM' and attrs.get('agentic.turn.number') == 1:
                attrs['agentic.turn.number'] = True
        result = assess(report)
        self.assertTrue(result['checks']['observed_numbers'])
        self.assertTrue(result['checks']['native.call_api_log_io'])
        self.assertFalse(result['checks']['native_children_keep_parent'])

    def test_rejects_wrong_session_zero_time_and_swapped_call_usage(self):
        for mutation in ('session', 'time', 'usage', 'api-id', 'native-time', 'ordinal'):
            report = proof()
            spans = report['exports'][0]['spans']
            generations = [row for row in spans if row['attributes']['gen_ai.span.kind'] == 'LLM']
            if mutation == 'session':
                for row in spans: row['attributes']['gen_ai.conversation.id'] = 'wrong-session'
            elif mutation == 'time':
                for row in spans: row['start_time_unix_nano'] = row['end_time_unix_nano'] = 0
            elif mutation == 'usage':
                first, second = [row['attributes'] for row in generations[:2]]
                first['gen_ai.usage.input_tokens'], second['gen_ai.usage.input_tokens'] = second['gen_ai.usage.input_tokens'], first['gen_ai.usage.input_tokens']
            elif mutation == 'api-id': generations[0]['attributes']['agentic.model_call.id'] = 'invented'
            elif mutation == 'native-time': generations[0]['start_time_unix_nano'] += 1000
            else: spans[-4]['attributes']['agentic.turn.number'] = 1
            with self.subTest(mutation=mutation): self.assertFalse(assess(report)['passed'])

    def test_rejects_missing_duplicate_or_changed_native_terminal(self):
        for mutation in ('missing-all', 'missing-normal', 'duplicate-pre', 'duplicate-post', 'end', 'terminal-session', 'terminal-turn'):
            report = proof()
            raw = report['result']['details']['hermes_raw_callbacks']
            generations = [row for row in report['exports'][0]['spans'] if row['attributes']['gen_ai.span.kind'] == 'LLM']
            post = next(row for row in raw if row['hook'] == 'post_api_request')
            if mutation == 'missing-all':
                raw[:] = [row for row in raw if row['hook'] not in ('post_api_request', 'post_auxiliary_call')]
            elif mutation == 'missing-normal': raw.remove(post)
            elif mutation == 'duplicate-pre': raw.append(next(row for row in raw if row['hook'] == 'pre_api_request'))
            elif mutation == 'duplicate-post': raw.append(post)
            elif mutation == 'end': generations[0]['end_time_unix_nano'] += 123_000_000
            elif mutation == 'terminal-session': post['rawInput']['session_id'] = 'wrong-session'
            else: post['rawInput']['turn_id'] = 'wrong-turn'
            with self.subTest(mutation=mutation): self.assertFalse(assess(report)['passed'])

    def test_only_wire_verified_length_may_lack_native_end_and_is_reported(self):
        report = proof()
        raw = report['result']['details']['hermes_raw_callbacks']
        raw[:] = [row for row in raw if not (row['hook'] == 'post_api_request' and row['rawInput']['api_request_id'] == 'api-1')]
        result = assess(report)
        self.assertTrue(result['passed'], result)
        self.assertEqual(result['unverified_native_end_ids'], ['api-1'])
        report['result']['version'] = 'unknown'
        self.assertFalse(assess(report)['passed'])


if __name__ == '__main__':
    unittest.main()
