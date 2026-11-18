from copy import deepcopy
import json
import unittest

from verify import verify_case, acceptance_status, metric_count


def proof():
    resource = {"ant.agent.type": "pi", "ant.agent.product": "Pi"}
    def span(identifier, parent, kind, attributes):
        return {"trace_id": "t", "span_id": identifier, "parent_span_id": parent,
                "start_time_unix_nano": 1, "end_time_unix_nano": 2,
                "resource": resource, "scope_name": "observ-pi", "status": {"code": 1},
                "attributes": {"gen_ai.span.kind": kind, "gen_ai.conversation.id": "session", **attributes}}
    spans = [span("root", "", "CHAT", {}),
        span("llm1", "root", "LLM", {"gen_ai.request.model": "native-e2e-chat", "gen_ai.usage.input_tokens": 16, "gen_ai.usage.output_tokens": 4,
            "gen_ai.output.messages": '[{"role":"assistant","tool_calls":[{"id":"call_native_1","function":{"name":"read","arguments":"{\\"path\\":\\"receipt.txt\\"}"}}]}]'}),
        span("tool", "root", "TOOL", {"gen_ai.tool.call.id": "call_native_1", "gen_ai.tool.call.arguments": '{"path":"receipt.txt"}',
            "gen_ai.tool.call.result": "receipt: abc123"}),
        span("llm2", "root", "LLM", {"gen_ai.request.model": "native-e2e-chat", "gen_ai.usage.input_tokens": 16, "gen_ai.usage.output_tokens": 4,
            "gen_ai.input.messages": '[{"role":"tool","content":"receipt: abc123","tool_call_id":"call_native_1"}]',
            "gen_ai.output.messages": '[{"role":"assistant","content":"receipt: abc123"}]'})]
    requests = [{"request": {"model": "native-e2e-chat", "tools": [1], "messages": []},
                 "response": {"tool_calls": [{"id": "call_native_1", "function": {"name": "read", "arguments": '{"path":"receipt.txt"}'}}]},
                 "usage": {"prompt_tokens": 16, "completion_tokens": 4, "total_tokens": 20}},
                {"request": {"model": "native-e2e-chat", "tools": [1], "messages": [{"role": "tool", "tool_call_id": "call_native_1", "content": "receipt: abc123"}]},
                 "response": {"content": "receipt: abc123"}, "usage": {"prompt_tokens": 16, "completion_tokens": 4, "total_tokens": 20}}]
    exports = [{"admitted": True, "spans": spans, "logs": [{"resource": resource, "attributes": {"event.name": "api_request"}}],
                "metrics": [{"name": "agent.api_request.count", "kind": "sum", "temporality": 2, "attributes": {},
                             "resource": resource, "point": {"asInt": "2"}}]}]
    for direction, count in (("input", 32), ("output", 8)):
        exports[0]["metrics"].append({"name": "agent.token.usage", "kind": "sum", "temporality": 2,
            "attributes": {"type": direction}, "resource": resource, "point": {"asInt": str(count)}})
    return {"app": "pi", "scenario": "tool-loop", "model": "native-e2e-chat", "receipt": "abc123",
            "result": {"exit_code": 0, "session_id": "session", "output": "receipt: abc123", "details": {}},
            "requests": requests, "exports": exports}


def multi_turn_proof():
    value = proof()
    value["scenario"] = "multi-turn"
    value["result"]["details"]["resumed_session_id"] = "session"
    spans = value["exports"][0]["spans"]
    spans[1]["attributes"]["gen_ai.output.messages"] = json.dumps([
        {"role": "assistant", "tool_calls": value["requests"][0]["response"]["tool_calls"]}])
    second = deepcopy(spans)
    ids = {"root": "root2", "llm1": "llm3", "tool": "tool2", "llm2": "llm4"}
    for span in second:
        span.update(trace_id="t2", span_id=ids[span["span_id"]],
                    parent_span_id=ids.get(span["parent_span_id"], ""),
                    start_time_unix_nano=3, end_time_unix_nano=4)
        for key, field in list(span["attributes"].items()):
            if isinstance(field, str):
                span["attributes"][key] = field.replace("call_native_1", "call_native_3")
    spans.extend(second)
    requests = json.loads(json.dumps(value["requests"]).replace("call_native_1", "call_native_3"))
    value["requests"].extend(requests)
    for metric, count in zip(value["exports"][0]["metrics"], (4, 64, 16)):
        metric["point"]["asInt"] = str(count)
    return value


def hermes_multiturn_proof(length=False):
    from fake_provider import HERMES_LENGTH_CONTINUATION_PROMPT, LENGTH_PARTIAL_CONTENT
    value = multi_turn_proof()
    value['app'] = 'hermes'
    for export in value['exports']:
        for row in export['spans'] + export['logs'] + export['metrics']:
            row['resource'].update({'ant.agent.type': 'hermes', 'ant.agent.product': 'Hermes'})
            if 'scope_name' in row:
                row['scope_name'] = 'observ-hermes'
    for row in value['requests']:
        row['finish_reason'] = 'tool_calls' if row['response'].get('tool_calls') else 'stop'
    if length:
        truncated = deepcopy(value['requests'][1])
        truncated['finish_reason'] = 'length'
        truncated['response'] = {'role': 'assistant', 'content': LENGTH_PARTIAL_CONTENT,
                                 'reasoning_content': 'The actual tool result supplies the receipt.'}
        truncated['usage']['completion_tokens_details'] = {'reasoning_tokens': 1}
        value['requests'].insert(1, truncated)
        value['requests'][2]['request']['messages'] += [deepcopy(truncated['response']),
            {'role': 'user', 'content': HERMES_LENGTH_CONTINUATION_PROMPT}]
        spans = value['exports'][0]['spans']
        generation = deepcopy(spans[3])
        generation['span_id'] = 'length-generation'
        generation['attributes'].update({'gen_ai.response.finish_reasons': '["length"]',
            'gen_ai.usage.reasoning.output_tokens': 1,
            'gen_ai.input.messages': json.dumps(truncated['request']['messages']),
            'gen_ai.output.messages': json.dumps([truncated['response']])})
        spans[3]['attributes']['gen_ai.input.messages'] = json.dumps(value['requests'][2]['request']['messages'])
        spans[3]['attributes']['gen_ai.response.finish_reasons'] = '["stop"]'
        spans.insert(3, generation)
        for metric, count in zip(value['exports'][0]['metrics'], (5, 80, 20)):
            metric['point']['asInt'] = str(count)
    return value


def opencode_retry_proof():
    value = proof()
    value.update(app='opencode', scenario='api-retry')
    export = value['exports'][0]
    for row in export['spans'] + export['logs'] + export['metrics']:
        row['resource'].update({'ant.agent.type': 'opencode', 'ant.agent.product': 'OpenCode'})
        if 'scope_name' in row:
            row['scope_name'] = 'observ-opencode'
    prompt = [{'role': 'user', 'content': 'Read receipt.txt using your file read tool.'}]
    export['spans'][0]['attributes']['gen_ai.input.messages'] = json.dumps(prompt)
    export['spans'][1]['attributes']['gen_ai.input.messages'] = json.dumps(prompt)
    value['requests'][0]['request']['messages'] = [{'role': 'system', 'content': 'native system'},
        {'role': 'user', 'content': prompt[0]['content'] + '\n\n<system-reminder>native context</system-reminder>'}]
    for row in value['requests']:
        row['status'] = 200
    failed = deepcopy(value['requests'][0])
    failed.update(response={'error': {'message': 'recoverable fixture error'}}, usage=None, status=429, completed=True)
    value['requests'].insert(0, failed)
    generation = deepcopy(export['spans'][1])
    generation.update(span_id='failed-wire', status={'code': 2})
    generation['attributes'].update({'gen_ai.response.id': 'assistant1', 'error.type': '429',
        'gen_ai.usage.input_tokens': 0, 'gen_ai.usage.output_tokens': 0,
        'agentic.usage.input_tokens.missing': 'true', 'agentic.usage.output_tokens.missing': 'true',
        'gen_ai.output.messages': '[REDACTED]'})
    export['spans'][1]['attributes']['gen_ai.response.id'] = 'assistant1'
    export['spans'].insert(1, generation)
    export['spans'][0]['end_time_unix_nano'] = 10
    for index, span in enumerate(export['spans'][1:], 1):
        span['start_time_unix_nano'] = index * 2
        span['end_time_unix_nano'] = index * 2 + 1
    export['logs'] = [{'resource': generation['resource'], 'trace_id': span['trace_id'], 'span_id': span['span_id'],
        'attributes': {'event.name': 'api_request', **({'error.type': '429'} if span is generation else {})}}
        for span in export['spans'] if span['attributes']['gen_ai.span.kind'] == 'LLM']
    export['metrics'][0]['point']['asInt'] = '3'
    export['metrics'].append({'name': 'agent.api_request.error.count', 'kind': 'sum', 'temporality': 2,
        'resource': generation['resource'], 'attributes': {}, 'point': {'asInt': '1'}})
    return value


class OracleTests(unittest.TestCase):
    def test_kimi_requires_product_identity_native_scope_and_independent_source(self):
        from unittest.mock import patch
        value = proof()
        value['app'] = 'kimi'
        value['result']['version'] = '2.1.1'
        for export in value['exports']:
            for row in export['spans'] + export['logs'] + export['metrics']:
                row['resource'].update({'ant.agent.type': 'kimicli', 'ant.agent.product': 'KimiCLI'})
                if 'scope_name' in row: row['scope_name'] = 'kimihooks'
        with patch('kimi_source.kimi_matches', return_value={'verified': True, 'checks': {'kimi.source': True}, 'failures': []}):
            self.assertTrue(verify_case(**value)['verified'])
            corrupted = deepcopy(value)
            corrupted['exports'][0]['spans'][0]['resource']['ant.agent.product'] = 'Other'
            self.assertIn('resource.identity', verify_case(**corrupted)['failures'])
        with patch('kimi_source.kimi_matches', return_value={'verified': False, 'checks': {'kimi.source': False}, 'failures': ['kimi.source']}):
            self.assertIn('kimi.source', verify_case(**value)['failures'])

    def test_hermes_rejects_numeric_unknown_and_boolean_missing_in_both_signals(self):
        original = hermes_multiturn_proof(length=True)
        self.assertTrue(verify_case(**original)['verified'])
        for signal in ('span', 'log'):
            for marker in ('true', True, False):
                with self.subTest(signal=signal, marker=marker):
                    value = deepcopy(original)
                    row = value['exports'][0]['spans'][1] if signal == 'span' else value['exports'][0]['logs'][0]
                    row['attributes'].update({'gen_ai.usage.input_tokens': 0,
                        'agentic.usage.input_tokens.missing': marker})
                    self.assertIn('usage.hermes.missing_contract', verify_case(**value)['failures'])

    def test_hermes_object_repr_is_not_structured_tool_or_answer_evidence(self):
        value = hermes_multiturn_proof(length=True)
        self.assertTrue(verify_case(**value)['verified'])
        for signal in ('tool', 'answer'):
            altered = deepcopy(value)
            span = altered['exports'][0]['spans'][1 if signal == 'tool' else 4]
            content = ("NormalizedResponse(content=None, tool_calls=[ToolCall(id='call_native_1', name='read', arguments='{}')], finish_reason='tool_calls')"
                if signal == 'tool' else "NormalizedResponse(content='receipt: abc123', tool_calls=None, finish_reason='stop')")
            span['attributes']['gen_ai.output.messages'] = json.dumps([content])
            self.assertIn('business.receipt_chain', verify_case(**altered)['failures'])

    def test_opencode_retry_requires_real_failed_attempt_and_its_native_recovery(self):
        value = opencode_retry_proof()
        self.assertTrue(verify_case(**value)['verified'])
        for mutation in ('error_metric', 'status', 'native_id', 'event', 'request', 'no_retry'):
            with self.subTest(mutation=mutation):
                changed = deepcopy(value)
                export = changed['exports'][0]
                if mutation == 'error_metric':
                    export['metrics'][-1]['point']['asInt'] = '0'
                elif mutation == 'status':
                    export['spans'][1]['status']['code'] = 1
                elif mutation == 'native_id':
                    export['spans'][2]['attributes']['gen_ai.response.id'] = 'another-message'
                elif mutation == 'event':
                    export['logs'].append(deepcopy(export['logs'][0]))
                elif mutation == 'request':
                    changed['requests'][1]['request']['messages'] = [{'role':'user','content':'unrelated'}]
                else:
                    changed['requests'][0]['status'] = 200
                self.assertFalse(verify_case(**changed)['verified'])

    def test_opencode_retry_inputs_cannot_diverge_or_share_an_unrelated_prompt(self):
        value = opencode_retry_proof()
        self.assertTrue(verify_case(**value)['verified'])
        text_parts = deepcopy(value)
        content = json.loads(text_parts['exports'][0]['spans'][0]['attributes']['gen_ai.input.messages'])[0]['content']
        for call in text_parts['requests'][:2]:
            call['request']['messages'][-1]['content'] = [{'type': 'text', 'text': content},
                {'type': 'text', 'text': '<system-reminder>native context</system-reminder>'}]
        self.assertTrue(verify_case(**text_parts)['verified'], 'source text parts retain the actual original user prompt')
        for mutation in ('failed', 'recovered', 'both', 'all_native', 'reminder', 'empty', 'malformed'):
            with self.subTest(mutation=mutation):
                changed = deepcopy(value)
                spans = changed['exports'][0]['spans']
                unrelated = json.dumps([{'role': 'user', 'content': '<system-reminder>native context</system-reminder>' if mutation == 'reminder' else 'unrelated user request'}])
                if mutation in ('failed', 'both', 'all_native', 'reminder'):
                    spans[1]['attributes']['gen_ai.input.messages'] = unrelated
                if mutation in ('recovered', 'both', 'all_native', 'reminder'):
                    spans[2]['attributes']['gen_ai.input.messages'] = unrelated
                if mutation in ('all_native', 'reminder'):
                    spans[0]['attributes']['gen_ai.input.messages'] = unrelated
                if mutation in ('empty', 'malformed'):
                    for span in spans[:3]:
                        span['attributes']['gen_ai.input.messages'] = '[]' if mutation == 'empty' else 'invalid JSON'
                result = verify_case(**changed)
                self.assertFalse(result['verified'])
                self.assertIn('scenario.api_retry.native_attempts', result['failures'])

    def test_fake_hermes_multiturn_cannot_omit_the_original_length_response(self):
        value = hermes_multiturn_proof()
        self.assertTrue(verify_case(**value, mode='nvidia')['verified'], 'live models need not randomly truncate')
        result = verify_case(**value, mode='fake')
        self.assertFalse(result['verified'])
        self.assertIn('scenario.length_continuation.wire', result['failures'])

    def test_hermes_length_continuation_uses_primary_calls_with_deferred_title(self):
        value = hermes_multiturn_proof(length=True)
        title = deepcopy(value['exports'][0]['spans'][1])
        title.update(span_id='title-generation', start_time_unix_nano=7, end_time_unix_nano=8)
        title['attributes']['gen_ai.agent.name'] = 'title_generation'
        title['attributes']['gen_ai.output.messages'] = '[{"role":"assistant","content":"Native title"}]'
        value['exports'][0]['spans'].append(title)
        call = deepcopy(value['requests'][2])
        call.update(purpose='auxiliary', finish_reason='stop', response={'content': 'Native title'})
        value['requests'].append(call)
        for metric, count in zip(value['exports'][0]['metrics'], (6, 96, 24)):
            metric['point']['asInt'] = str(count)
        self.assertTrue(verify_case(**value)['verified'])
        title['attributes']['gen_ai.agent.name'] = 'unrelated'
        self.assertIn('scenario.length_continuation.native_terminal', verify_case(**value)['failures'])

    def test_length_continuation_requires_original_host_input_and_successful_native_terminal(self):
        value = hermes_multiturn_proof(length=True)
        self.assertTrue(verify_case(**value)['verified'])
        for mutation in ('missing_finish', 'wrong_continuation', 'cancelled', 'wrong_partial', 'reasoning'):
            with self.subTest(mutation=mutation):
                altered = deepcopy(value)
                if mutation == 'missing_finish':
                    altered['requests'][1].pop('finish_reason')
                elif mutation == 'wrong_continuation':
                    altered['requests'][2]['request']['messages'][-1]['content'] = 'new unrelated user request'
                elif mutation == 'cancelled':
                    generation = altered['exports'][0]['spans'][3]
                    generation['attributes'].update({'gen_ai.response.finish_reasons': '["cancelled"]', 'error.type': 'cancelled'})
                    generation['status']['code'] = 2
                elif mutation == 'wrong_partial':
                    altered['exports'][0]['spans'][3]['attributes']['gen_ai.output.messages'] = '[{"role":"assistant","content":"wrong"}]'
                else:
                    altered['requests'][1]['response']['reasoning_content'] = None
                self.assertFalse(verify_case(**altered)['verified'])

    def test_single_turn_requires_the_actual_tool_result_and_complete_native_chain(self):
        mutations = (
            ("metadata cannot replace tool result", lambda p: p["exports"][0]["spans"][2]["attributes"].update({"gen_ai.tool.call.result": "wrong", "debug": "receipt: abc123"})),
            ("tool-producing LLM", lambda p: p["exports"][0]["spans"][1]["attributes"].update({"gen_ai.output.messages": '[{"role":"assistant","content":"done"}]'})),
            ("raw tool-result ID", lambda p: p["requests"][1]["request"]["messages"][0].update(tool_call_id="wrong-call")),
            ("native tool-result ID", lambda p: p["exports"][0]["spans"][3]["attributes"].update({"gen_ai.input.messages": '[{"role":"tool","content":"receipt: abc123","tool_call_id":"wrong-call"}]'})),
        )
        self.assertTrue(verify_case(**proof())["verified"])
        for name, mutate in mutations:
            with self.subTest(name=name):
                value = proof()
                mutate(value)
                report = verify_case(**value)
                self.assertIn("business.receipt_chain", report["failures"])
                if name == "metadata cannot replace tool result":
                    self.assertIn("tools.actual_result", report["failures"])

    def test_an_earlier_receipt_answer_cannot_replace_the_last_primary_answer(self):
        value = multi_turn_proof()
        extra_span = deepcopy(value["exports"][0]["spans"][7])
        extra_span.update(span_id="last-answer", start_time_unix_nano=5, end_time_unix_nano=6)
        extra_span["attributes"]["gen_ai.output.messages"] = '[{"role":"assistant","content":"done"}]'
        value["exports"][0]["spans"].append(extra_span)
        value["exports"][0]["spans"][4]["end_time_unix_nano"] = 6
        extra_call = deepcopy(value["requests"][-1])
        extra_call["response"]["content"] = "done"
        value["requests"].append(extra_call)
        for metric, count in zip(value["exports"][0]["metrics"], (5, 80, 20)):
            metric["point"]["asInt"] = str(count)
        report = verify_case(**value)
        self.assertIn("transport.answer_output", report["failures"])
        self.assertIn("turns.evidence", report["failures"])
        extra_span["attributes"]["gen_ai.output.messages"] = '[{"role":"assistant","content":"receipt: abc123"}]'
        extra_call["response"]["content"] = "receipt: abc123"
        self.assertTrue(verify_case(**value)["verified"])

    def test_stream_and_cancel_require_actual_primary_streaming_requests(self):
        for scenario in ("stream", "cancel"):
            with self.subTest(scenario=scenario):
                value = proof()
                value["scenario"] = scenario
                if scenario == "cancel":
                    value["result"]["details"]["cancelled"] = True
                    value["exports"][0]["spans"][1]["status"]["code"] = 2
                for invalid in (False, None, 1, "true"):
                    for row in value["requests"]:
                        row["request"]["stream"] = invalid
                    self.assertIn("transport.streaming", verify_case(**value)["failures"])
                for row in value["requests"]:
                    row["request"]["stream"] = True
                self.assertTrue(verify_case(**value)["verified"])
                value["requests"][0]["purpose"] = "auxiliary"
                value["requests"][0]["request"]["stream"] = False
                self.assertNotIn("transport.streaming", verify_case(**value)["failures"])
                value["requests"][1]["purpose"] = "auxiliary"
                self.assertIn("transport.streaming", verify_case(**value)["failures"])

    def test_usage_named_cases_require_the_declared_provider_facts(self):
        for scenario in ("unknown-usage", "zero-usage", "cache-usage"):
            with self.subTest(scenario=scenario):
                value = proof()
                value["scenario"] = scenario
                self.assertIn("scenario.usage", verify_case(**value)["failures"])
                for row in value["requests"]:
                    if scenario == "unknown-usage":
                        row["usage"] = None
                    elif scenario == "zero-usage":
                        row["usage"] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
                    else:
                        row["usage"].update(prompt_tokens_details={"cached_tokens": 3, "cache_write_tokens": 2},
                                            completion_tokens_details={"reasoning_tokens": 1})
                self.assertNotIn("scenario.usage", verify_case(**value)["failures"])
                # Every primary call needs the scenario's facts; an earlier call
                # or auxiliary request cannot supply missing evidence later.
                value["requests"][-1]["usage"] = {"prompt_tokens": 16, "completion_tokens": 4, "total_tokens": 20}
                self.assertIn("scenario.usage", verify_case(**value)["failures"])

    def test_multi_turn_empty_resumed_chat_cannot_reuse_the_first_turn(self):
        value = proof()
        value["scenario"] = "multi-turn"
        value["result"]["details"]["resumed_session_id"] = "session"
        empty = deepcopy(value["exports"][0]["spans"][0])
        empty.update(trace_id="empty-trace", span_id="empty-resume", start_time_unix_nano=3,
                     end_time_unix_nano=4)
        value["exports"][0]["spans"].append(empty)
        report = verify_case(**value)
        self.assertTrue(report["checks"]["session.resumed"])
        self.assertIn("turns.evidence", report["failures"])

    def test_genuine_two_turn_chains_support_recursive_native_parentage(self):
        value = multi_turn_proof()
        self.assertTrue(verify_case(**value)["verified"])
        # An intermediate native span must not sever CHAT ownership. Conversely,
        # one inner CHAT cannot lend the same children to two enclosing rounds.
        spans = value["exports"][0]["spans"]
        bridge = deepcopy(spans[4])
        bridge.update(span_id="bridge", parent_span_id="root2")
        bridge["attributes"]["gen_ai.span.kind"] = "SPAN"
        spans.append(bridge)
        for span in spans[5:8]:
            span["parent_span_id"] = "bridge"
        self.assertTrue(verify_case(**value)["verified"])
        value["exports"][0]["spans"][0]["parent_span_id"] = "root2"
        value["exports"][0]["spans"][0]["trace_id"] = "t2"
        for span in spans[1:4]:
            span["trace_id"] = "t2"
        # Move every second-turn child into the first CHAT. Both CHAT nodes are
        # present but only one owns a real chain.
        bridge["parent_span_id"] = "root"
        self.assertIn("turns.evidence", verify_case(**value)["failures"])

    def test_every_resumed_turn_needs_its_own_receipt_chain(self):
        mutations = (
            ("native answer", lambda p: p["exports"][0]["spans"][7]["attributes"].update({"gen_ai.output.messages": '[{"role":"assistant","content":"done"}]'})),
            ("raw final answer", lambda p: p["requests"][3]["response"].update(content="abc123")),
            ("tool result", lambda p: p["exports"][0]["spans"][6]["attributes"].update({"gen_ai.tool.call.result": "done"})),
            ("raw same-turn result", lambda p: p["requests"][3]["request"]["messages"][0].update(tool_call_id="call_native_1")),
            ("native same-turn input", lambda p: p["exports"][0]["spans"][7]["attributes"].update({"gen_ai.input.messages": '[{"role":"tool","content":"receipt: abc123","tool_call_id":"call_native_1"}]'})),
            ("native tool-producing LLM", lambda p: p["exports"][0]["spans"][5]["attributes"].update({"gen_ai.output.messages": '[{"role":"assistant","content":"done"}]'})),
        )
        for name, mutate in mutations:
            with self.subTest(name=name):
                value = multi_turn_proof()
                mutate(value)
                report = verify_case(**value)
                self.assertTrue(report["checks"]["llm.answer_output"], "first-round output still exists")
                self.assertIn("receipt: abc123", value["requests"][1]["response"]["content"], "first-round raw answer still exists")
                if name == "raw final answer":
                    self.assertIn("transport.answer_output", report["failures"])
                else:
                    self.assertTrue(report["checks"]["transport.answer_output"])
                self.assertIn("turns.evidence", report["failures"])

    def test_auxiliary_generation_cannot_count_as_a_resumed_turn(self):
        value = multi_turn_proof()
        for span in value["exports"][0]["spans"][4:]:
            span["attributes"]["gen_ai.agent.name"] = "title"
        for request in value["requests"][2:]:
            request["purpose"] = "auxiliary"
        self.assertIn("turns.evidence", verify_case(**value)["failures"])

    def test_hermes_deferred_title_cannot_supply_main_turn_evidence(self):
        value = multi_turn_proof()
        value['app'] = 'hermes'
        for span in value['exports'][0]['spans'][4:]:
            span['attributes']['gen_ai.agent.name'] = 'title_generation'
        self.assertIn('turns.evidence', verify_case(**value)['failures'])

    def test_hermes_title_usage_pairs_with_auxiliary_http_not_timestamp(self):
        value = proof()
        value['app'] = 'hermes'
        value['requests'][0].update(purpose='auxiliary')
        value['requests'][1].update(purpose='agent', usage=None)
        spans = value['exports'][0]['spans']
        spans[1]['attributes'].update({'agentic.usage.input_tokens.missing': True,
                                     'agentic.usage.output_tokens.missing': True})
        spans[3]['attributes']['gen_ai.agent.name'] = 'title_generation'
        spans[3]['start_time_unix_nano'] = 2
        spans[3]['end_time_unix_nano'] = 3
        self.assertNotIn('usage.reportedness', verify_case(**value)['failures'])
        spans[3]['attributes']['gen_ai.agent.name'] = 'unrelated'
        self.assertIn('usage.reportedness', verify_case(**value)['failures'])

    def test_tool_output_cannot_substitute_for_the_real_final_provider_answer(self):
        value = proof()
        value['result']['output'] = json.dumps({'type': 'tool_use', 'content': 'receipt: abc123'})
        value['requests'][-1]['response']['content'] = 'abc123'
        self.assertIn('transport.answer_output', verify_case(**value)['failures'])
        value['requests'][-1]['response']['content'] = 'receipt: abc123'
        self.assertNotIn('transport.answer_output', verify_case(**value)['failures'])
        value['requests'][-1]['purpose'] = 'auxiliary'
        self.assertIn('transport.answer_output', verify_case(**value)['failures'], 'an auxiliary title is not the agent final answer')

    def test_raw_generation_request_model_must_match_selected_model(self):
        self.assertTrue(verify_case(**proof())["verified"])
        for model in (None, "wrong-model", 0, False):
            with self.subTest(model=model):
                value = proof()
                value["requests"][0]["request"]["model"] = model
                self.assertIn("model.identity", verify_case(**value)["failures"])
        value = proof()
        value["requests"][0]["request"].pop("model")
        self.assertIn("model.identity", verify_case(**value)["failures"])

    def test_catalog_requires_completed_http_200_but_is_not_a_generation(self):
        value = proof()
        value["requests"].append({"method": "GET", "request": {}, "usage": None,
                                  "status": 200, "completed": True})
        self.assertTrue(verify_case(**value)["verified"])
        value["requests"][-1]["response"] = {"data": [{"id": value["model"]}]}
        self.assertTrue(verify_case(**value)["verified"], "an actual catalog body is not an extra generation")
        mutations = (
            {}, {"status": 200}, {"completed": True},
            {"status": 200, "completed": False},
            {"status": 200, "completed": 1},
            {"status": "200", "completed": True},
            {"status": 429, "completed": True},
            {"status": 200, "completed": True, "error": "ReadTimeout"},
        )
        for fields in mutations:
            with self.subTest(fields=fields):
                broken = proof()
                broken["requests"].append({"method": "GET", "request": {}, "usage": None, **fields})
                self.assertIn("transport.calls", verify_case(**broken)["failures"])

    def test_unknown_markers_allow_omitted_measurements_but_reject_boolean_placeholders(self):
        value = proof()
        for row in value["requests"]:
            row["usage"] = None
        value["exports"][0]["metrics"] = [m for m in value["exports"][0]["metrics"] if m["name"] != "agent.token.usage"]
        for span in value["exports"][0]["spans"]:
            if span["attributes"]["gen_ai.span.kind"] == "LLM":
                for field in ("input", "output"):
                    span["attributes"].pop("gen_ai.usage." + field + "_tokens", None)
                    span["attributes"]["agentic.usage." + field + "_tokens.missing"] = "true"
        self.assertNotIn("usage.reportedness", verify_case(**value)["failures"])
        value["exports"][0]["spans"][1]["attributes"]["gen_ai.usage.input_tokens"] = False
        self.assertIn("usage.reportedness", verify_case(**value)["failures"])

    def test_independent_business_tool_transport_and_metrics_evidence(self):
        self.assertEqual(verify_case(**proof())["failures"], [])

    def test_each_broken_evidence_link_fails(self):
        mutations = [
            lambda p: p["requests"][0]["request"].update(model="wrong-model"),
            lambda p: p["result"].update(output="receipt: forged"),
            lambda p: p["exports"][0]["spans"][2]["attributes"].update({"gen_ai.tool.call.id": "wrong"}),
            lambda p: p["exports"][0]["spans"].pop(),
            lambda p: p["exports"][0]["spans"][2].update(parent_span_id="missing"),
            lambda p: p["exports"][0]["spans"].append(deepcopy(p["exports"][0]["spans"][1])),
            lambda p: p["exports"][0]["metrics"][0]["point"].update(asInt="4"),
            lambda p: p["exports"][0].update(admitted=False),
            lambda p: p["exports"][0]["spans"][1].update(scope_name="langchain"),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                value = proof()
                mutate(value)
                self.assertFalse(verify_case(**value)["verified"])

    def test_unknown_as_zero_is_rejected_and_honest_unknown_is_accepted(self):
        value = proof()
        for row in value["requests"]:
            row["usage"] = None
        self.assertIn("usage.reportedness", verify_case(**value)["failures"])
        for span in value["exports"][0]["spans"]:
            if span["attributes"]["gen_ai.span.kind"] == "LLM":
                span["attributes"].update({"gen_ai.usage.input_tokens": 0, "gen_ai.usage.output_tokens": 0,
                    "agentic.usage.input_tokens.missing": "true", "agentic.usage.output_tokens.missing": "true"})
        value["exports"][0]["metrics"] = value["exports"][0]["metrics"][:1]
        self.assertTrue(verify_case(**value)["verified"])

    def test_cache_and_reasoning_are_preserved_as_subsets_without_double_counting(self):
        value = proof()
        for row in value["requests"]:
            row["usage"].update(prompt_tokens_details={"cached_tokens": 3, "cache_write_tokens": 2},
                                completion_tokens_details={"reasoning_tokens": 1})
        self.assertIn("usage.breakdown", verify_case(**value)["failures"])
        for span in value["exports"][0]["spans"]:
            if span["attributes"]["gen_ai.span.kind"] == "LLM":
                span["attributes"].update({"gen_ai.usage.cache_read.input_tokens": 3,
                    "gen_ai.usage.cache_creation.input_tokens": 2, "gen_ai.usage.reasoning.output_tokens": 1})
        value["exports"][0]["metrics"][1]["point"]["asInt"] = "22"
        for name, count in (("agent.cache.read_tokens", 6), ("agent.cache.write_tokens", 4)):
            value["exports"][0]["metrics"].append({"name": name, "kind": "sum", "temporality": 2,
                "attributes": {"gen_ai.request.model": value["model"]}, "resource": {}, "point": {"asInt": str(count)}})
        self.assertTrue(verify_case(**value)["verified"])
        value["exports"][0]["spans"][1]["attributes"]["gen_ai.usage.output_tokens"] = 5
        self.assertIn("usage.reportedness", verify_case(**value)["failures"])

    def test_token_metrics_are_independently_reconciled_to_provider_counts(self):
        value = proof()
        value["exports"][0]["metrics"][1]["point"]["asInt"] = "31"
        self.assertIn("metrics.tokens", verify_case(**value)["failures"])
        value = proof()
        value["exports"][0]["metrics"] = value["exports"][0]["metrics"][:1]
        self.assertIn("metrics.tokens", verify_case(**value)["failures"])

    def test_unknown_zero_metric_samples_fail_but_reported_zero_is_valid(self):
        value = proof()
        for row in value["requests"]:
            row["usage"] = None
        for span in value["exports"][0]["spans"]:
            if span["attributes"]["gen_ai.span.kind"] == "LLM":
                span["attributes"].update({"gen_ai.usage.input_tokens": 0, "gen_ai.usage.output_tokens": 0,
                    "agentic.usage.input_tokens.missing": "true", "agentic.usage.output_tokens.missing": "true"})
        for metric in value["exports"][0]["metrics"][1:]:
            metric["point"]["asInt"] = "0"
        self.assertIn("metrics.tokens", verify_case(**value)["failures"])
        for row in value["requests"]:
            row["usage"] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        for span in value["exports"][0]["spans"]:
            if span["attributes"]["gen_ai.span.kind"] == "LLM":
                span["attributes"].update({"agentic.usage.input_tokens.missing": "false", "agentic.usage.output_tokens.missing": "false"})
        self.assertTrue(verify_case(**value)["verified"])
        value["exports"][0]["metrics"][1]["point"]["asInt"] = "1"
        self.assertIn("metrics.tokens", verify_case(**value)["failures"])

    def test_counter_cumulative_and_delta_intervals_are_not_double_counted(self):
        for temporality, values in ((2, [16, 32]), (1, [16, 16])):
            points = [{"name": "tokens", "kind": "sum", "temporality": temporality,
                       "resource": {}, "attributes": {}, "point": {"asInt": str(value),
                        "startTimeUnixNano": "1" if temporality == 2 else str(index + 1),
                        "timeUnixNano": str(index + 2)}} for index, value in enumerate(values)]
            self.assertEqual(metric_count(points, "tokens"), 32)
            if temporality == 1:
                self.assertEqual(metric_count(points + [deepcopy(points[-1])], "tokens"), 32)
        for invalid in (True, -1, .5, float("nan")):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                metric_count([{"name": "tokens", "kind": "sum", "temporality": 2,
                              "point": {"asDouble": invalid}}], "tokens")

    def test_cache_counters_and_inclusive_input_cannot_hide_overcount(self):
        value = proof()
        for row in value["requests"]:
            row["usage"]["prompt_tokens_details"] = {"cached_tokens": 3, "cache_write_tokens": 2}
        for span in value["exports"][0]["spans"]:
            if span["attributes"]["gen_ai.span.kind"] == "LLM":
                span["attributes"].update({"gen_ai.usage.cache_read.input_tokens": 3, "gen_ai.usage.cache_creation.input_tokens": 2})
        # Inclusive16 cannot be counted again alongside cache3+2.
        self.assertIn("metrics.tokens", verify_case(**value)["failures"])
        value["exports"][0]["metrics"][1]["point"]["asInt"] = "22"
        self.assertIn("metrics.tokens", verify_case(**value)["failures"], "reported positive caches need actual separate samples")
        for name, count in (("agent.cache.read_tokens", 6), ("agent.cache.write_tokens", 4)):
            value["exports"][0]["metrics"].append({"name": name, "kind": "sum", "temporality": 2,
                "attributes": {}, "resource": {}, "point": {"asInt": str(count)}})
        self.assertTrue(verify_case(**value)["verified"])
        value["exports"][0]["metrics"][-1]["point"]["asInt"] = "5"
        self.assertIn("metrics.tokens", verify_case(**value)["failures"])

    def test_integer_counters_do_not_lose_precision_and_cumulative_conflicts_fail(self):
        metric = {"name": "tokens", "kind": "sum", "temporality": 2, "point": {"asInt": str(2**53 + 1)}}
        self.assertEqual(metric_count([metric], "tokens"), 2**53 + 1)
        other = deepcopy(metric)
        other["point"]["asInt"] = "1"
        with self.assertRaisesRegex(ValueError, "conflicting"):
            metric_count([metric, other], "tokens")
        metric["point"].update(startTimeUnixNano="1", timeUnixNano="2", asInt="16")
        other = deepcopy(metric)
        other["point"].update(timeUnixNano="3", asInt="15")
        with self.assertRaisesRegex(ValueError, "decreased"):
            metric_count([metric, other], "tokens")

    def test_auxiliary_order_uses_native_agent_identity_not_generation_timestamp(self):
        value = proof()
        value["app"] = "opencode"
        value["scenario"] = "cancel"
        value["result"]["details"]["cancelled"] = True
        value["requests"][0].update(purpose="auxiliary")
        value["requests"][1].update(purpose="agent", usage=None)
        spans = value["exports"][0]["spans"]
        value["exports"][0]["spans"] = [spans[0], spans[1], spans[3]]
        for span in value["exports"][0]["spans"]:
            span["scope_name"] = "observ-opencode"
        # Main AssistantMessage creation can precede the actual title HTTP call.
        spans[1]["status"]["code"] = 2
        spans[1]["attributes"].update({"agentic.usage.input_tokens.missing": "true", "agentic.usage.output_tokens.missing": "true"})
        spans[3]["attributes"]["gen_ai.agent.name"] = "title"
        spans[3]["start_time_unix_nano"] = 2
        spans[3]["end_time_unix_nano"] = 3
        value["exports"][0]["metrics"][1]["point"]["asInt"] = "16"
        value["exports"][0]["metrics"][2]["point"]["asInt"] = "4"
        self.assertNotIn("usage.reportedness", verify_case(**value)["failures"])
        spans[3]["attributes"]["gen_ai.agent.name"] = "build"
        self.assertIn("usage.reportedness", verify_case(**value)["failures"])

    def test_boolean_zero_and_unreported_details_are_not_token_facts(self):
        value = proof()
        for row in value["requests"]:
            row["usage"] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        value["exports"][0]["metrics"] = value["exports"][0]["metrics"][:1]
        for span in value["exports"][0]["spans"]:
            if span["attributes"]["gen_ai.span.kind"] == "LLM":
                span["attributes"].update({"gen_ai.usage.input_tokens": 0, "gen_ai.usage.output_tokens": 0})
        self.assertTrue(verify_case(**value)["verified"])
        value["exports"][0]["spans"][1]["attributes"]["gen_ai.usage.input_tokens"] = False
        self.assertIn("usage.reportedness", verify_case(**value)["failures"])
        for attribute in ("gen_ai.usage.cache_read.input_tokens", "gen_ai.usage.cache_creation.input_tokens", "gen_ai.usage.reasoning.output_tokens"):
            value = proof()
            value["exports"][0]["spans"][1]["attributes"][attribute] = 0
            self.assertIn("usage.breakdown", verify_case(**value)["failures"])

    def test_three_signal_resource_identity_matches_the_expected_native_case(self):
        expected = {"ant.agent.product": "Pi", "ant.instance.id": "case-unique"}
        value = proof()
        for row in value["exports"][0]["spans"] + value["exports"][0]["logs"] + value["exports"][0]["metrics"]:
            row["resource"] = {**row.get("resource", {}), **expected}
        self.assertTrue(verify_case(**value, expected_resource=expected)["verified"])
        for signal in ("spans", "logs", "metrics"):
            broken = deepcopy(value)
            broken["exports"][0][signal][0]["resource"]["ant.instance.id"] = "other-case"
            self.assertIn("resource.identity", verify_case(**broken, expected_resource=expected)["failures"])
        broken = deepcopy(value)
        broken["exports"][0]["spans"][1]["resource"]["ant.agent.product"] = "Hermes"
        self.assertIn("resource.identity", verify_case(**broken, expected_resource=expected)["failures"])

    def test_signal_identity_uses_its_registered_attribute_location(self):
        value = proof()
        expected = {"ant.agent.product": "Pi", "ant.username": "native-user", "ant.uid": "native-uid"}
        for row in value["exports"][0]["spans"] + value["exports"][0]["logs"] + value["exports"][0]["metrics"]:
            row.setdefault("attributes", {}).update({"ant.username": "native-user", "ant.uid": "native-uid"})
        self.assertTrue(verify_case(**value, expected_resource=expected)["verified"])
        for signal in ("spans", "logs", "metrics"):
            broken = deepcopy(value)
            broken["exports"][0][signal][0]["attributes"]["ant.username"] = "other-user"
            self.assertIn("resource.identity", verify_case(**broken, expected_resource=expected)["failures"])
        value["exports"][0]["spans"][0]["resource"]["ant.username"] = "conflicting-user"
        self.assertIn("resource.identity", verify_case(**value, expected_resource=expected)["failures"])

    def test_empty_partial_or_duplicate_matrix_never_passes(self):
        self.assertEqual(acceptance_status([], {"pi-tool-loop"}), "failed")
        row = {"case": "pi-tool-loop", "verified": True}
        self.assertEqual(acceptance_status([row], {"pi-tool-loop"}), "passed")
        self.assertEqual(acceptance_status([row, row], {"pi-tool-loop"}), "failed")
        self.assertEqual(acceptance_status([row], {"pi-tool-loop", "hermes-tool-loop"}), "failed")


if __name__ == "__main__":
    unittest.main()
