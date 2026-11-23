"""Independent raw-provider accounting must reject apparently successful exports."""
import copy
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import httpx

import live_python
from live_python import provider_usage, reconcile


class LiveProviderLedgerTests(unittest.TestCase):
    def test_cleanup_failures_preserve_all_evidence_and_attempt_every_close(self):
        if os.environ.get('AIVISION_TEST_CLEANUP_CHILD') != '1':
            child = subprocess.run([sys.executable, str(Path(__file__).resolve()),
                'LiveProviderLedgerTests.test_cleanup_failures_preserve_all_evidence_and_attempt_every_close'],
                env={**os.environ, 'AIVISION_TEST_CLEANUP_CHILD': '1'}, capture_output=True, text=True, timeout=30)
            self.assertEqual(child.returncode, 0, child.stdout + child.stderr)
            return
        from langfuse import Langfuse
        attempted = []
        forwarded = []
        def reply(request):
            body = json.loads(request.content)
            forwarded.append(body)
            if body['model'].startswith('aivision-invalid-model-'):
                return httpx.Response(404, json={'error': {'message': 'invalid model', 'type': 'invalid_request_error'}})
            frame = {'id': 'cleanup-fixture', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'fixture-model',
                     'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': 'OK'}, 'finish_reason': 'stop'}],
                     'usage': {'prompt_tokens': 3, 'completion_tokens': 1, 'total_tokens': 4}}
            return httpx.Response(200, content='data: ' + json.dumps(frame) + '\n\ndata: [DONE]\n\n',
                                  headers={'Content-Type': 'text/event-stream'})
        def fake_provider(proxy, base_url, key):
            proxy.base_url, proxy.key, proxy.records = base_url, key, []
            proxy.client = httpx.Client(transport=httpx.MockTransport(reply))
            proxy.request_lock = threading.Lock()
            proxy.requested_cases = set()
        def failed_cleanup(stage, original):
            def invoke(instance):
                attempted.append(stage)
                original(instance)
                raise RuntimeError('private synthetic credential must not appear in report')
            return invoke
        with tempfile.TemporaryDirectory() as temporary, \
                patch.dict(os.environ, {'AIVISION_LIVE_PROVIDER_KEY': 'synthetic-only'}), \
                patch.object(live_python.ProviderProxy, '__init__', fake_provider), \
                patch.object(Langfuse, 'flush', failed_cleanup('client.flush', Langfuse.flush)), \
                patch.object(Langfuse, 'shutdown', failed_cleanup('client.shutdown', Langfuse.shutdown)), \
                patch.object(live_python.CaptureForwarder, 'close', failed_cleanup('capture.close', live_python.CaptureForwarder.close)), \
                patch.object(live_python.ProviderProxy, 'close', failed_cleanup('proxy.close', live_python.ProviderProxy.close)), \
                patch('sys.argv', ['live_python.py', '--provider-base-url', 'https://provider.invalid/v1',
                                   '--model', 'fixture-model', '--collector-only', '--out', temporary]):
            stdout = io.StringIO()
            thrown = None
            with redirect_stdout(stdout):
                try:
                    result = live_python.main()
                except Exception as error:
                    thrown = type(error).__name__
            self.assertIsNone(thrown, 'Cleanup errors must become failed evidence, not escape before writing it')
            self.assertEqual(result, 1)
            self.assertEqual(attempted, ['client.flush', 'client.shutdown', 'capture.close', 'proxy.close'])
            root = Path(temporary)
            report = json.loads((root/'report.json').read_text())
            exports = json.loads((root/'sdk-exports.json').read_text())
            ledger = json.loads((root/'provider-ledger.json').read_text())
            self.assertEqual(report['status'], 'failed')
            self.assertEqual(report['errors'], [{'stage': stage, 'type': 'RuntimeError'} for stage in attempted])
            self.assertEqual(len(ledger), 3)
            self.assertEqual(len(forwarded), 3, 'Cleanup must never trigger additional provider calls')
            self.assertTrue(exports and any(row['spans'] for row in exports))
            self.assertLess(report['bounds']['from'], report['bounds']['to'])
            self.assertNotIn('private synthetic credential', (root/'report.json').read_text() + stdout.getvalue())

    def test_proxy_blocks_duplicate_cases_and_unbounded_requests_before_provider(self):
        forwarded = []
        proxy = live_python.ProviderProxy("https://provider.invalid/v1", "synthetic-only")
        proxy.client.close()
        proxy.client = httpx.Client(transport=httpx.MockTransport(
            lambda request: (forwarded.append(json.loads(request.content)) or httpx.Response(404, json={"error": "fixture"}))))
        with patch("socket.getfqdn", side_effect=AssertionError("Loopback bind must not use reverse DNS")):
            endpoint = proxy.start()
        bounded = {"model": "fixture", "messages": [], "stream": True,
                   "stream_options": {"include_usage": True}, "max_tokens": 32}
        try:
            with httpx.Client(trust_env=False) as client:
                statuses = []
                for case, body in [("direct-stream", bounded), ("direct-stream", bounded),
                                   ("langchain-stream", {**bounded, "max_tokens": 33}),
                                   ("provider-error", bounded)]:
                    statuses.append(client.post(endpoint + "/chat/completions", json=body,
                                                headers={"X-AIV-Test-Case": case}).status_code)
            self.assertEqual(len(forwarded), 2, "Guard must reject before any external forwarding")
            self.assertEqual(statuses, [404, 429, 400, 404])
            self.assertEqual(sum(row.get("upstream_attempted", False) for row in proxy.records), 2)
            self.assertEqual(len(proxy.records), 4, "Rejected attempts must remain auditable")
        finally:
            proxy.close()

    def test_native_canonical_usage_is_checked_against_provider_not_raw_spans(self):
        target = {"space_id": "space", "agent_uuid": "agent", "agent_product": "langfuse",
                  "authenticated_user_id": "user"}
        case = {"case": "direct-stream", "trace_id": "a" * 32}
        record = {"case": "direct-stream", "status": 200,
                  "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}}
        span = {"span_id": "1" * 16, "trace_id": case["trace_id"], "attributes": {}}
        actual = {**span, "resource_attributes": {
            "aivision.space.id": "space", "aivision.agent.id": "agent",
            "aivision.agent.product": "langfuse", "aivision.user.id": "user"}}
        data = {"meta": {"complete": True}, "spans": [actual], "semantics": {"usage": {"input": 3, "output": 1, "total": 4}}}
        with patch.object(live_python, "http_query", return_value=(200, {"code": 0, "data": data})):
            checked = live_python.verify_native(target, [case], [{"spans": [span]}], {}, [record])
            self.assertTrue(all(checked.values()))
            data["semantics"]["usage"]["input"] = 6
            checked = live_python.verify_native(target, [case], [{"spans": [span]}], {}, [record])
            self.assertTrue(checked["direct-stream.native_readback"])
            self.assertFalse(checked["direct-stream.native_provider_usage"])
            data["semantics"] = {}
            checked = live_python.verify_native(target, [case], [{"spans": [span]}], {}, [record])
            self.assertFalse(checked["direct-stream.native_provider_usage"])
            for prompt, completion, total in ((-1, 1, 0), (3, -1, 2), (3, 1, -1),
                                               (3, 1, 5), (3, 33, 36), (True, 1, 2)):
                with self.subTest(prompt=prompt, completion=completion, total=total):
                    record["usage"] = {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": total}
                    data["semantics"] = {"usage": {"input": prompt, "output": completion, "total": total}}
                    checked = live_python.verify_native(target, [case], [{"spans": [span]}], {}, [record])
                    self.assertFalse(checked["direct-stream.native_provider_usage"])

    def test_native_rejects_error_envelope_incomplete_or_wrong_identity(self):
        target, cases, exports, records, replies = self.readback_fixture()
        valid = replies['native']
        mutations = [
            ('application-error', lambda body: body.update(code=500)),
            ('missing-code', lambda body: body.pop('code')),
            ('boolean-code', lambda body: body.update(code=False)),
            ('incomplete', lambda body: body['data']['meta'].update(complete=False)),
            ('missing-complete', lambda body: body['data'].pop('meta')),
            ('wrong-space', lambda body: body['data']['spans'][0]['resource_attributes'].update({'aivision.space.id': 'other'})),
            ('wrong-agent', lambda body: body['data']['spans'][0]['resource_attributes'].update({'aivision.agent.id': 'other'})),
            ('wrong-product', lambda body: body['data']['spans'][0]['resource_attributes'].update({'aivision.agent.product': 'other'})),
            ('wrong-user', lambda body: body['data']['spans'][0]['resource_attributes'].update({'aivision.user.id': 'other'})),
        ]
        for name, mutate in mutations:
            with self.subTest(name=name):
                body = copy.deepcopy(valid); mutate(body)
                with patch.object(live_python, 'http_query', return_value=(200, body)), \
                        patch.object(live_python.time, 'monotonic', side_effect=[0, 61]):
                    checks = live_python.verify_native(target, cases, exports, {}, records)
                self.assertFalse(checks['direct-stream.native_readback'])
                self.assertFalse(checks['direct-stream.native_provider_usage'])
        with patch.object(live_python, 'http_query', return_value=(200, valid)):
            checks = live_python.verify_native(target, cases, exports, {}, records)
        for name in ('native_http', 'native_complete', 'native_identity', 'native_readback'):
            self.assertIs(checks['direct-stream.' + name], True)

    def readback_fixture(self):
        target = {'space_id': 'space', 'agent_uuid': 'agent', 'agent_product': 'langfuse',
                  'authenticated_user_id': 'owner'}
        cases = [{'case': 'direct-stream', 'trace_id': 'a' * 32}]
        records = [{'case': 'direct-stream', 'status': 200,
                    'usage': {'prompt_tokens': 3, 'completion_tokens': 1, 'total_tokens': 4}}]
        start = 1700000000000000000
        attrs = {'session.id': 'session/a', 'user.id': 'business-user',
                 'langfuse.observation.input': '"hello"', 'langfuse.observation.output': '"OK"'}
        root = {'span_id': '1' * 16, 'trace_id': cases[0]['trace_id'], 'name': 'live-root',
                'parent_span_id': None, 'start_time_unix_nano': start, 'end_time_unix_nano': start + 1000000,
                'attributes': {**attrs, 'langfuse.observation.type': 'agent'}}
        generation = {**root, 'span_id': '2' * 16, 'parent_span_id': root['span_id'], 'name': 'model',
                      'attributes': {**attrs, 'langfuse.observation.type': 'generation',
                          'langfuse.observation.usage_details': '{"input":3,"output":1,"total":4}'}}
        spans = [root, generation]
        actual = copy.deepcopy(spans)
        for item in actual:
            item['attributes'].pop('user.id')
            item['attributes'].update({'langfuse.user.id': 'business-user', 'gen_ai.user.id': 'business-user'})
            item['resource_attributes'] = {'aivision.space.id': 'space', 'aivision.agent.id': 'agent',
                                         'aivision.agent.product': 'langfuse', 'aivision.user.id': 'owner'}
        observations = []
        for item in spans:
            usage = {'input': 3, 'output': 1, 'total': 4} if item is generation else {'input': 0, 'output': 0, 'total': 0}
            observations.append({'id': item['span_id'], 'traceId': item['trace_id'], 'name': item['name'],
                'parentObservationId': item['parent_span_id'], 'type': item['attributes']['langfuse.observation.type'].upper(),
                'model': None, 'input': 'hello', 'output': 'OK', 'level': 'DEFAULT',
                'startTime': '2023-11-14T22:13:20.000000000Z', 'endTime': '2023-11-14T22:13:20.001000000Z',
                'usage': {**usage, 'unit': 'TOKENS'}, 'usageDetails': usage if item is generation else {}})
        trace = {'id': root['trace_id'], 'timestamp': '2023-11-14T22:13:20.000000000Z', 'name': 'live-root',
                 'input': 'hello', 'output': 'OK', 'sessionId': 'session/a', 'userId': 'business-user',
                 'metadata': {}, 'tags': [], 'release': None, 'version': None, 'environment': 'default', 'public': False,
                 'htmlPath': '/project/agent/traces/' + root['trace_id'], 'latency': 0.001, 'totalCost': None,
                 'scores': [], 'observations': observations}
        replies = {'native': {'code': 0, 'data': {'meta': {'complete': True}, 'spans': actual,
                    'semantics': {'usage': {'input': 3, 'output': 1, 'total': 4}}}},
                   'trace': trace, 'observations': {'data': observations, 'meta': {'page': 1, 'limit': 2, 'totalPages': 1, 'totalItems': 2}},
                   'session': {'id': 'session/a', 'projectId': 'agent', 'createdAt': trace['timestamp'], 'traces': [trace]}}
        return target, cases, [{'spans': spans}], records, replies

    def read_v1(self, target, cases, exports, records, replies):
        calls = []
        def query(_target, path, **options):
            self.assertIs(_target, target)
            self.assertIs(options.get('basic'), True)
            calls.append((path, options))
            key = 'observations' if path.endswith('/observations') else 'session' if '/sessions/' in path else 'trace'
            return 200, replies[key]
        with patch.object(live_python, 'http_query', side_effect=query):
            checks = live_python.verify_v1(target, cases, exports, records)
        return checks, calls

    def test_v1_live_checks_trace_observation_and_session_without_provider_requests(self):
        target, cases, exports, records, replies = self.readback_fixture()
        checks, calls = self.read_v1(target, cases, exports, records, replies)
        self.assertTrue(all(checks.values()), checks)
        self.assertEqual([path for path, _ in calls], ['/api/public/traces/' + cases[0]['trace_id'],
            '/api/public/observations', '/api/public/sessions/session%2Fa'])
        for name in ('v1_trace', 'v1_pagination', 'v1_observations', 'v1_provider_usage',
                     'v1_session_identity', 'v1_session_inventory', 'v1_session_traces', 'v1_session_created'):
            self.assertIs(checks['direct-stream.' + name], True)

    def test_v1_live_rejects_missing_projection_wrong_ids_types_usage_and_pagination(self):
        target, cases, exports, records, original = self.readback_fixture()
        mutations = [
            ('trace-id', lambda r: r['trace'].update(id='other')),
            ('trace-user', lambda r: r['trace'].update(userId='owner')),
            ('trace-session', lambda r: r['trace'].update(sessionId='other')),
            ('missing-observation', lambda r: r['observations']['data'].pop()),
            ('span-trace', lambda r: r['observations']['data'][1].update(traceId='other')),
            ('span-type', lambda r: r['observations']['data'][1].update(type='SPAN')),
            ('usage-count', lambda r: r['observations']['data'][1]['usageDetails'].update(input=4)),
            ('usage-type', lambda r: r['observations']['data'][1]['usageDetails'].update(output=True)),
            ('legacy-usage-type', lambda r: r['observations']['data'][1]['usage'].update(output=True)),
            ('unknown-usage', lambda r: r['observations']['data'][0].update(usageDetails={'input': 0})),
            ('missing-meta', lambda r: r['observations'].pop('meta')),
            ('wrong-page', lambda r: r['observations']['meta'].update(page=2)),
            ('wrong-count', lambda r: r['observations']['meta'].update(totalItems=3)),
            ('project', lambda r: r['session'].update(projectId='another-agent')),
            ('session', lambda r: r['session'].update(id='other')),
            ('extra-session-trace', lambda r: r['session']['traces'].append(copy.deepcopy(r['trace']))),
            ('session-created', lambda r: r['session'].update(createdAt='2023-11-15T00:00:00Z')),
        ]
        for name, mutate in mutations:
            with self.subTest(name=name):
                replies = copy.deepcopy(original); mutate(replies)
                checks, _ = self.read_v1(target, cases, exports, records, replies)
                self.assertFalse(all(checks.values()), name)

    def test_v1_live_enforces_all_pagination_pages(self):
        target, cases, exports, records, replies = self.readback_fixture()
        # Three spans force a second page, including an unmetered wrapper.
        third = copy.deepcopy(exports[0]['spans'][0]); third.update(span_id='3' * 16, parent_span_id='1' * 16)
        exports[0]['spans'].append(third)
        observation = copy.deepcopy(replies['observations']['data'][0]); observation.update(id='3' * 16, parentObservationId='1' * 16)
        replies['trace']['observations'] = replies['trace']['observations'] + [observation]
        pages = []
        def query(_target, path, **options):
            if path.endswith('/observations'):
                page = options['params']['page']; pages.append(page)
                return 200, {'data': replies['observations']['data'] if page == 1 else [observation],
                             'meta': {'page': page, 'limit': 2, 'totalPages': 2, 'totalItems': 3}}
            return 200, replies['session' if '/sessions/' in path else 'trace']
        with patch.object(live_python, 'http_query', side_effect=query):
            checks = live_python.verify_v1(target, cases, exports, records)
        self.assertEqual(pages, [1, 2])
        self.assertTrue(all(checks.values()), checks)

    def test_v1_live_unknown_error_usage_remains_unreported(self):
        target, cases, exports, records, replies = self.readback_fixture()
        cases[0]['case'] = 'provider-error'
        records[0] = {'case': 'provider-error', 'status': 404, 'usage': None}
        attrs = exports[0]['spans'][1]['attributes']
        attrs.pop('langfuse.observation.usage_details')
        attrs['langfuse.observation.level'] = 'ERROR'
        for row in replies['trace']['observations']:
            if row['type'] == 'GENERATION':
                row.update(level='ERROR', usage={'input': 0, 'output': 0, 'total': 0, 'unit': 'TOKENS'}, usageDetails={})
        checks, _ = self.read_v1(target, cases, exports, records, replies)
        self.assertTrue(all(checks.values()), checks)
        replies['observations']['data'][1]['usageDetails'] = {'input': 0, 'output': 0, 'total': 0}
        checks, _ = self.read_v1(target, cases, exports, records, replies)
        self.assertFalse(checks['provider-error.v1_provider_usage'])

    def test_v1_live_projects_explicit_sdk_zero_cost_without_inventing_usage(self):
        target, cases, exports, records, replies = self.readback_fixture()
        attrs = exports[0]['spans'][1]['attributes']
        attrs['langfuse.observation.cost_details'] = '{"input":0,"output":0,"total":0}'
        replies['trace']['totalCost'] = 0.0
        checks, _ = self.read_v1(target, cases, exports, records, replies)
        self.assertTrue(all(checks.values()), checks)
        replies['trace']['totalCost'] = None
        checks, _ = self.read_v1(target, cases, exports, records, replies)
        self.assertFalse(checks['direct-stream.v1_trace'])

    def test_v1_live_http_errors_do_not_accept_matching_payloads(self):
        target, cases, exports, records, replies = self.readback_fixture()
        for failed in ('trace', 'observations', 'session'):
            def query(_target, path, **_options):
                key = 'observations' if path.endswith('/observations') else 'session' if '/sessions/' in path else 'trace'
                return (500 if key == failed else 200), replies[key]
            with self.subTest(failed=failed), patch.object(live_python, 'http_query', side_effect=query):
                checks = live_python.verify_v1(target, cases, exports, records)
                self.assertFalse(all(checks.values()))

    def test_bounded_runner_keeps_langchain_and_openai_instrumentation_separate(self):
        selected = os.environ.get("AIVISION_TEST_TOKEN_LIMIT_FIELD")
        if selected is None:
            # The OpenAI integration patches a module globally. Each CLI mode
            # needs the same fresh process boundary as the real runner.
            for selected in ("default", "max_tokens", "max_completion_tokens"):
                with self.subTest(token_limit_field=selected):
                    child = subprocess.run(
                        [sys.executable, str(Path(__file__).resolve()),
                         "LiveProviderLedgerTests.test_bounded_runner_keeps_langchain_and_openai_instrumentation_separate"],
                        env={**os.environ, "AIVISION_TEST_TOKEN_LIMIT_FIELD": selected},
                        text=True, capture_output=True, timeout=30)
                    self.assertEqual(child.returncode, 0, child.stdout + child.stderr)
            return
        requests = []
        def reply(request):
            body = json.loads(request.content)
            requests.append(body)
            if body["model"].startswith("aivision-invalid-model-"):
                return httpx.Response(400, json={"error": {"message": "invalid model", "type": "invalid_request_error"}})
            event = {"id": "chat-fixture", "object": "chat.completion.chunk", "created": 1, "model": "fixture-model"}
            frames = [
                {**event, "choices": [{"index": 0, "delta": {"role": "assistant", "content": "OK"}, "finish_reason": None}]},
                {**event, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
                {**event, "choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}},
            ]
            content = "".join("data: " + json.dumps(frame) + "\n\n" for frame in frames) + "data: [DONE]\n\n"
            return httpx.Response(200, content=content, headers={"Content-Type": "text/event-stream"})

        def fake_provider(proxy, base_url, key):
            proxy.base_url, proxy.key, proxy.records = base_url, key, []
            proxy.client = httpx.Client(transport=httpx.MockTransport(reply))
            proxy.request_lock = threading.Lock()
            proxy.requested_cases = set()

        arguments = [] if selected == "default" else ["--langchain-token-limit-field", selected, "--disable-thinking"]
        with tempfile.TemporaryDirectory() as temporary, \
                patch.dict(os.environ, {"AIVISION_LIVE_PROVIDER_KEY": "synthetic-only"}), \
                patch.object(live_python.ProviderProxy, "__init__", fake_provider), \
                patch("sys.argv", ["live_python.py", "--provider-base-url", "https://provider.invalid/v1",
                                    "--model", "fixture-model", "--collector-only", "--out", temporary, *arguments]):
            with redirect_stdout(io.StringIO()):
                result = live_python.main()
            report = json.loads((Path(temporary) / "report.json").read_text())
            self.assertEqual(result, 0, [key for key, value in report["checks"].items() if not value])
            self.assertEqual(len(requests), 3)
            self.assertEqual([case["case"] for case in report["cases"]],
                             ["langchain-stream", "direct-stream", "provider-error"])
            expected_field = "max_tokens" if selected == "max_tokens" else "max_completion_tokens"
            self.assertEqual(set(requests[0]) & {"max_tokens", "max_completion_tokens"}, {expected_field})
            self.assertEqual(requests[0][expected_field], 32)
            for request in requests[1:]:
                self.assertEqual(set(request) & {"max_tokens", "max_completion_tokens"}, {"max_tokens"})
                self.assertEqual(request["max_tokens"], 32)
            self.assertTrue(all(request["stream"] and request["stream_options"] == {"include_usage": True}
                                for request in requests))
            for request in requests:
                if selected == "default":
                    self.assertNotIn("chat_template_kwargs", request)
                else:
                    self.assertEqual(request["chat_template_kwargs"], {"enable_thinking": False})

    def test_invalid_provider_usage_fails_even_when_sdk_matches_it(self):
        cases = [{"case": "direct-stream", "trace_id": "a" * 32, "output": "OK"}]
        for prompt, completion, total, valid in (
                (3, 1, 4, True), (0, 0, 0, True), (3, 32, 35, True),
                (-1, 1, 0, False), (3, -1, 2, False), (3, 1, -1, False),
                (3, 1, 5, False), (3, 33, 36, False), (True, 1, 2, False), (3.0, 1, 4, False)):
            with self.subTest(prompt=prompt, completion=completion, total=total):
                usage = {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": total}
                records = [{"case": "direct-stream", "status": 200, "text": "OK", "usage": usage}]
                exports = [{"spans": [{"trace_id": "a" * 32, "span_id": "1" * 16, "attributes": {
                    "langfuse.observation.type": "generation", "langfuse.observation.usage_details": json.dumps(usage)}}]}]
                self.assertEqual(reconcile(cases, records, exports)["direct-stream.provider_usage"], valid)

    def test_raw_sse_ledger_keeps_final_usage_and_provider_text(self):
        body = b'data: {"choices":[{"delta":{"content":"O"}}]}\n\n'
        body += b'data: {"choices":[{"delta":{"content":"K"}}]}\n\n'
        body += b'data: {"choices":[],"usage":{"prompt_tokens":3,"completion_tokens":1,"total_tokens":4}}\n\n'
        body += b'data: [DONE]\n\n'
        result = provider_usage(body, streamed=True)
        self.assertEqual(result["text"], "OK")
        self.assertEqual(result["usage"], {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4})
        self.assertEqual(len(result["usage_events"]), 1)
        self.assertIsNone(provider_usage(b'{"error":{"message":"bad model"}}', streamed=False)["usage"])

    def test_doubled_stream_usage_missing_generation_or_proxy_error_fails(self):
        cases = [{"case": "direct-stream", "trace_id": "a" * 32, "output": "OK"}]
        records = [{"case": "direct-stream", "status": 200, "text": "OK",
                    "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}}]
        span = {"trace_id": "a" * 32, "span_id": "1" * 16, "attributes": {
            "langfuse.observation.type": "generation",
            "langfuse.observation.usage_details": json.dumps(records[0]["usage"])}}
        exports = [{"spans": [span]}]
        self.assertTrue(all(reconcile(cases, records, exports).values()))
        wrong = copy.deepcopy(exports)
        wrong[0]["spans"][0]["attributes"]["langfuse.observation.usage_details"] = json.dumps({
            "prompt_tokens": 6, "completion_tokens": 2, "total_tokens": 8})
        self.assertFalse(reconcile(cases, records, wrong)["direct-stream.sdk_usage"])
        self.assertFalse(reconcile(cases, records, [{"spans": []}])["direct-stream.single_generation"])
        self.assertFalse(reconcile(cases, [{**records[0], "proxy_error": "ConnectionError"}], exports)["direct-stream.single_request"])


if __name__ == "__main__":
    unittest.main()
