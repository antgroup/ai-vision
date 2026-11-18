import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.request import Request, ProxyHandler, build_opener
from urllib.error import HTTPError

from fake_provider import FakeProvider, reply_for


class FakeProviderTests(unittest.TestCase):
    def post(self, provider, messages, *, stream=False, tools=True):
        body = {'model': 'native-e2e-chat', 'messages': messages, 'stream': stream}
        if tools:
            body['tools'] = self.tools
        request = Request(provider.url + '/chat/completions', json.dumps(body).encode(),
                          {'Content-Type': 'application/json'})
        with build_opener(ProxyHandler({})).open(request, timeout=5) as response:
            value = response.read().decode()
        if not stream:
            payload = json.loads(value)
            return payload['choices'][0]['message'], payload['choices'][0]['finish_reason'], payload.get('usage')
        chunks = [json.loads(line[6:]) for line in value.splitlines()
                  if line.startswith('data: ') and '[DONE]' not in line]
        content = ''.join(choice['delta'].get('content', '') for chunk in chunks for choice in chunk['choices'])
        reasoning = ''.join(choice['delta'].get('reasoning_content', '') for chunk in chunks for choice in chunk['choices'])
        finish = [choice['finish_reason'] for chunk in chunks for choice in chunk['choices'] if choice['finish_reason']]
        usage = [chunk['usage'] for chunk in chunks if 'usage' in chunk]
        return {'role': 'assistant', 'content': content, 'reasoning_content': reasoning}, finish[-1], usage[-1]

    def test_request_sized_usage_is_opt_in_and_matches_actual_http_messages(self):
        messages = [{'role': 'user', 'content': 'Historical background. ' * 100}]
        for stream in (False, True):
            with self.subTest(stream=stream), FakeProvider(self.path, 'multi-turn', request_sized_usage=True) as provider:
                _, _, long_usage = self.post(provider, messages, stream=stream)
                _, _, short_usage = self.post(provider, [{'role': 'user', 'content': 'Read receipt'}], stream=stream)
                for recorded, received in zip(provider.requests, (long_usage, short_usage)):
                    self.assertEqual(received, recorded['usage'])
                    self.assertEqual(received['prompt_tokens'], max(1, len(json.dumps(recorded['request']['messages'], ensure_ascii=False)) // 4))
                    self.assertEqual(received['total_tokens'], received['prompt_tokens'] + received['completion_tokens'])
                self.assertGreater(long_usage['prompt_tokens'], short_usage['prompt_tokens'])
            with FakeProvider(self.path, 'multi-turn') as provider:
                _, _, long_usage = self.post(provider, messages, stream=stream)
                _, _, short_usage = self.post(provider, [{'role': 'user', 'content': 'Read receipt'}], stream=stream)
                self.assertEqual(long_usage, short_usage)

    def test_stream_content_matches_recorded_response_without_changing_whitespace(self):
        from langchain_core.messages import AIMessage
        for content in ('receipt: abc123', '  leading  spaces\n尾\t ', ''):
            with self.subTest(content=content), patch('fake_provider.reply_for', return_value=AIMessage(content=content)):
                with FakeProvider(self.path, 'tool-loop') as provider:
                    answer, finish, _ = self.post(provider, [{'role': 'user', 'content': 'Read receipt'}], stream=True)
                    self.assertEqual(answer['content'], content)
                    self.assertEqual(answer['content'], provider.requests[0]['response']['content'] or '')
                    self.assertEqual(finish, 'stop')

    def test_stream_tool_call_content_matches_actual_wire_exactly(self):
        from langchain_core.messages import AIMessage
        for content in ('Recover by reading the existing receipt.', ' \n正文\t ', ''):
            message = AIMessage(content=content, tool_calls=[{'id':'native-tool','name':'read','args':{'filePath':'receipt.txt'}}])
            with self.subTest(content=content), patch('fake_provider.reply_for', return_value=message):
                with FakeProvider(self.path, 'tool-error') as provider:
                    answer, finish, _ = self.post(provider, [{'role':'user','content':'Read'}], stream=True)
                    self.assertEqual(answer['content'], content)
                    self.assertEqual(answer['content'], provider.requests[0]['response']['content'] or '')
                    self.assertEqual(finish, 'tool_calls')

    def test_api_retry_fails_only_first_primary_wire_attempt_then_uses_actual_tool_result(self):
        for stream in (False, True):
            with self.subTest(stream=stream), FakeProvider(self.path, 'api-retry') as provider:
                self.post(provider, [{'role': 'user', 'content': 'title'}], tools=False)
                user = {'role': 'user', 'content': 'Read the receipt'}
                with self.assertRaises(HTTPError) as failure:
                    self.post(provider, [user], stream=stream)
                self.assertEqual(failure.exception.code, 429)
                self.assertEqual(failure.exception.headers['Retry-After'], '0')
                self.assertNotIn('usage', json.loads(failure.exception.read()))
                self.assertEqual(self.post(provider, [user])[1], 'tool_calls')
                answer, finish, _ = self.post(provider, [user, {'role': 'tool', 'content': 'receipt: changed-at-runtime'}], stream=stream)
                self.assertEqual((answer['content'], finish), ('receipt: changed-at-runtime', 'stop'))
                primary = [row for row in provider.requests if row['purpose'] == 'agent']
                self.assertEqual([row['status'] for row in primary], [429, 200, 200])
                self.assertTrue(primary[0]['completed'])
                self.assertIsNone(primary[0]['usage'])
                self.assertFalse(primary[0]['usage_reported'])
                self.assertEqual(primary[0]['request'], primary[1]['request'] | {'stream': stream})

    def test_opt_in_length_is_a_real_wire_response_then_continues_once(self):
        from fake_provider import HERMES_LENGTH_CONTINUATION_PROMPT
        for stream in (False, True):
            with self.subTest(stream=stream), FakeProvider(self.path, 'multi-turn', length_continuation=True) as provider:
                self.assertEqual(self.post(provider, [{'role': 'user', 'content': 'title'}], tools=False)[1], 'stop')
                user = {'role': 'user', 'content': 'Read the receipt'}
                tool = {'role': 'tool', 'tool_call_id': 'call_1', 'content': 'receipt: changed-at-runtime'}
                self.assertEqual(self.post(provider, [user])[1], 'tool_calls')
                partial, finish, usage = self.post(provider, [user, tool], stream=stream)
                self.assertEqual(finish, 'length')
                self.assertTrue(partial['reasoning_content'])
                self.assertEqual(usage['completion_tokens_details']['reasoning_tokens'], 1)
                continuation = {'role': 'user', 'content': HERMES_LENGTH_CONTINUATION_PROMPT}
                final, finish, _ = self.post(provider, [user, tool, partial, continuation], stream=stream)
                self.assertEqual(finish, 'stop')
                self.assertEqual(final['content'], 'receipt: changed-at-runtime')
                second = {'role': 'user', 'content': 'Read the receipt again'}
                history = [user, tool, partial, continuation, final, second]
                self.assertEqual(self.post(provider, history)[1], 'tool_calls', 'resume must perform a new real tool read')
                self.assertEqual(self.post(provider, history + [tool])[1], 'stop', 'length is injected only once')
                self.assertEqual([row['finish_reason'] for row in provider.requests],
                                 ['stop', 'tool_calls', 'length', 'stop', 'tool_calls', 'stop'])

    def test_length_continuation_cannot_answer_from_cached_receipt_without_tool_input(self):
        from fake_provider import HERMES_LENGTH_CONTINUATION_PROMPT
        with FakeProvider(self.path, 'multi-turn', length_continuation=True) as provider:
            user = {'role': 'user', 'content': 'Read the receipt'}
            partial, _, _ = self.post(provider, [user, {'role': 'tool', 'content': 'receipt: changed-at-runtime'}])
            with self.assertRaises(HTTPError) as error:
                self.post(provider, [user, partial, {'role': 'user', 'content': HERMES_LENGTH_CONTINUATION_PROMPT}])
            self.assertEqual(error.exception.code, 422)

    def test_length_injection_is_explicit_and_does_not_change_other_scenarios(self):
        with self.assertRaises(ValueError):
            FakeProvider(self.path, 'zero-usage', length_continuation=True)
        with FakeProvider(self.path, 'multi-turn') as provider:
            result, finish, usage = self.post(provider, [{'role': 'tool', 'content': 'receipt: changed-at-runtime'}])
            self.assertEqual(finish, 'stop')
            self.assertNotIn('reasoning_content', result)
            self.assertNotIn('completion_tokens_details', usage)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "receipt.txt"
        self.path.write_text("receipt: random-actual-tool-result\n", encoding="utf-8")
        self.tools = [{"type": "function", "function": {
            "name": "read", "parameters": {"type": "object", "properties": {
                "filePath": {"type": "string"},
            }},
        }}]

    def tearDown(self):
        self.tmp.cleanup()

    def test_answer_depends_on_actual_tool_result_not_the_fixture_file(self):
        first = reply_for({"tools": self.tools, "messages": [
            {"role": "user", "content": "Read the receipt"},
        ]}, self.path, "tool-loop", 1)
        self.assertEqual(first.tool_calls[0]["name"], "read")
        self.assertEqual(first.tool_calls[0]["args"], {"filePath": str(self.path)})
        self.assertNotIn("random-actual-tool-result", first.content)
        second = reply_for({"messages": [
            {"role": "tool", "tool_call_id": "call_1", "content": "receipt: changed-at-runtime"},
        ]}, self.path, "tool-loop", 2)
        self.assertEqual(second.content, "receipt: changed-at-runtime")

    def test_fake_cannot_answer_without_a_successful_tool_read(self):
        with self.assertRaisesRegex(ValueError, "tool result"):
            reply_for({"messages": [{"role": "tool", "content": "empty"}]}, self.path, "tool-loop", 2)

    def test_dsh_read_schema_requires_a_string_file_path_and_complete_required_args(self):
        tools = [{'type': 'function', 'function': {'name': 'read', 'parameters': {
            'type': 'object', 'properties': {'file_path': {'type': 'string'}, 'offset': {'type': 'integer'}},
            'required': ['file_path'],
        }}}]
        message = reply_for({'tools': tools, 'messages': [{'role': 'user', 'content': 'Read receipt.txt'}]},
                            self.path, 'tool-loop', 1)
        self.assertEqual(message.tool_calls[0]['args'], {'file_path': str(self.path)})
        for invalid in (
            {'type': 'object', 'properties': {'file_path': {'type': 'array'}}, 'required': ['file_path']},
            {'type': 'object', 'properties': {'file_path': {'type': 'string'}, 'mode': {'type': 'string'}},
             'required': ['file_path', 'mode']},
        ):
            with self.subTest(parameters=invalid), self.assertRaisesRegex(ValueError, 'supported file read tool'):
                reply_for({'tools': [{'type': 'function', 'function': {'name': 'read', 'parameters': invalid}}]},
                          self.path, 'tool-loop', 1)

    def test_kimi_read_tool_preserves_native_name_and_required_path(self):
        tools = [{'type':'function','function':{'name':'Read','parameters':{
            'type':'object','properties':{'path':{'type':'string'}},'required':['path']}}}]
        message = reply_for({'tools':tools,'messages':[{'role':'user','content':'Read receipt.txt'}]},
                            self.path,'tool-loop',1)
        self.assertEqual(message.tool_calls[0]['name'],'Read')
        self.assertEqual(message.tool_calls[0]['args'],{'path':str(self.path)})

    def test_error_case_uses_real_error_result_before_recovery(self):
        message = reply_for({"tools": self.tools, "messages": [
            {"role": "tool", "tool_call_id": "call_1", "content": "file not found"},
        ]}, self.path, "tool-error", 2)
        self.assertEqual(message.tool_calls[0]["args"]["filePath"], str(self.path))

    def test_stream_final_usage_and_missing_usage_are_distinct(self):
        for scenario, has_usage in (("stream", True), ("unknown-usage", False), ("zero-usage", True)):
            with self.subTest(scenario=scenario), FakeProvider(self.path, scenario) as provider:
                request = Request(provider.url + "/chat/completions", json.dumps({
                    "model": "native-e2e-chat", "stream": True, "tools": self.tools,
                    "messages": [{"role": "user", "content": "Read the receipt"}],
                }).encode(), {"Content-Type": "application/json"})
                with build_opener(ProxyHandler({})).open(request, timeout=5) as response:
                    chunks = [json.loads(line[6:]) for line in response.read().decode().splitlines()
                              if line.startswith("data: ") and "[DONE]" not in line]
                usage = [chunk for chunk in chunks if "usage" in chunk]
                self.assertEqual(bool(usage), has_usage)
                if has_usage:
                    self.assertEqual(usage[-1]["choices"], [])
                    self.assertEqual(usage[-1]["usage"]["total_tokens"],
                                     0 if scenario == "zero-usage" else 20)
                self.assertEqual(len(provider.requests), 1)
                self.assertEqual(provider.requests[0]["usage_reported"], has_usage)

    def test_cancellation_holds_the_agent_stream_but_finishes_auxiliary_calls(self):
        with FakeProvider(self.path, "cancel") as provider:
            start = time.monotonic()
            request = Request(provider.url + "/chat/completions", json.dumps({
                "model": "native-e2e-chat", "stream": True,
                "messages": [{"role": "user", "content": "Generate a session title"}],
            }).encode(), {"Content-Type": "application/json"})
            with build_opener(ProxyHandler({})).open(request, timeout=5) as response:
                self.assertIn("[DONE]", response.read().decode())
            self.assertLess(time.monotonic() - start, 5)
            self.assertEqual(provider.requests[0]["purpose"], "auxiliary")
            self.assertTrue(provider.requests[0]["usage_reported"])


if __name__ == "__main__":
    unittest.main()
