"""Reject per-call IO corruption even when totals, identity, and graph still match."""
from copy import deepcopy
import json
import unittest

from hermes_call_evidence import call_checks
from test_hermes_compression import proof as compression_proof
from test_hermes_turn_numbering import proof


class HermesCallEvidenceTests(unittest.TestCase):
    def test_title_and_summary_are_matched_by_native_auxiliary_purpose(self):
        report = compression_proof()
        for row in report['result']['details']['hermes_raw_callbacks']:
            if row['hook'] in ('pre_auxiliary_call', 'post_auxiliary_call'):
                row['rawInput']['aux_task'] = 'title_generation'
        attrs = report['exports'][0]['spans'][-1]['attributes']
        attrs['gen_ai.agent.name'] = 'title_generation'
        self.assertTrue(all(call_checks(report).values()))
        attrs['gen_ai.agent.name'] = 'compression'
        self.assertFalse(call_checks(report)['native.call_sequence'])

    def test_every_primary_length_and_auxiliary_call_has_complete_io(self):
        for build in (proof, compression_proof):
            report = build()
            self.assertTrue(all(call_checks(report).values()))
            spans = [row for export in report['exports'] for row in export['spans']
                     if row['attributes'].get('gen_ai.span.kind') == 'LLM']
            for index in range(len(spans)):
                for direction in ('input', 'output'):
                    changed = deepcopy(report)
                    generation = [row for export in changed['exports'] for row in export['spans']
                                  if row['attributes'].get('gen_ai.span.kind') == 'LLM'][index]
                    generation['attributes'][f'gen_ai.{direction}.messages'] = '[{"role":"assistant","content":"unrelated"}]'
                    with self.subTest(build=build.__module__, call=index, direction=direction):
                        self.assertFalse(call_checks(changed)[f'native.call_{direction}'])

    def test_native_pre_post_and_api_logs_are_independent_required_evidence(self):
        for mutation in ('pre-content', 'post-content', 'sdk-content', 'log-input', 'log-output', 'log-id', 'log-missing'):
            report = proof()
            raw = report['result']['details']['hermes_raw_callbacks']
            if mutation == 'pre-content':
                next(row['rawInput'] for row in raw if row['hook'] == 'pre_api_request')['request_messages'][0]['content'] = 'unrelated'
            elif mutation in ('post-content', 'sdk-content'):
                post = next(row['rawInput'] for row in raw if row['hook'] == 'post_api_request')
                message = post['response']['assistant_message'] if mutation == 'post-content' else post['assistant_message']
                message['content'] = 'unrelated'
            else:
                log = report['exports'][0]['logs'][0]
                if mutation == 'log-id': log['span_id'] = 'wrong-call'
                elif mutation == 'log-missing': report['exports'][0]['logs'].pop()
                else: log['attributes'][f'gen_ai.{mutation[4:]}.messages'] = '[]'
            with self.subTest(mutation=mutation):
                self.assertFalse(all(call_checks(report).values()))

    def test_sequence_never_pairs_by_equal_token_usage(self):
        report = proof()
        for row in report['requests']:
            row['usage'] = {'prompt_tokens': 16, 'completion_tokens': 4, 'total_tokens': 20}
        report['requests'][0], report['requests'][3] = report['requests'][3], report['requests'][0]
        self.assertFalse(call_checks(report)['native.call_output'])

    def test_semantic_output_preserves_reasoning_and_tool_call_facts(self):
        for mutation in ('reasoning', 'tool-id', 'tool-name', 'tool-args', 'answer-space'):
            report = proof()
            spans = [row for row in report['exports'][0]['spans'] if row['attributes'].get('gen_ai.span.kind') == 'LLM']
            index = 1 if mutation == 'reasoning' else 2 if mutation == 'answer-space' else 0
            attrs = spans[index]['attributes']
            output = json.loads(attrs['gen_ai.output.messages'])
            if mutation == 'reasoning': output[0].pop('reasoning_content')
            elif mutation == 'answer-space': output[0]['content'] += ' '
            elif mutation == 'tool-id': output[0]['tool_calls'][0]['id'] = 'wrong-id'
            elif mutation == 'tool-name': output[0]['tool_calls'][0]['function']['name'] = 'wrong-name'
            else: output[0]['tool_calls'][0]['function']['arguments'] = '{"path":"wrong.txt"}'
            attrs['gen_ai.output.messages'] = json.dumps(output)
            with self.subTest(mutation=mutation):
                self.assertFalse(call_checks(report)['native.call_output'])

    def test_sdk_empty_fields_and_json_argument_spacing_do_not_change_semantics(self):
        report = proof()
        for row in report['exports'][0]['spans']:
            attrs = row['attributes']
            if attrs.get('gen_ai.span.kind') != 'LLM': continue
            output = json.loads(attrs['gen_ai.output.messages'])
            message = output[0]
            if not message.get('content'): message['content'] = ''
            message.setdefault('tool_calls', [])
            message.setdefault('reasoning_content', None)
            for call in message['tool_calls']:
                call['function']['arguments'] = json.dumps(json.loads(call['function']['arguments']), indent=2)
            attrs['gen_ai.output.messages'] = json.dumps(output)
        self.assertTrue(all(call_checks(report).values()))


if __name__ == '__main__':
    unittest.main()
