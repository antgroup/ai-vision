"""DSH's real HTTP, WebSocket, and isolated process boundaries."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from websockets.sync.server import serve

from drivers import dsh
from loopback_http import LoopbackHTTPServer
from models import CaseContext


class DshDriverTests(unittest.TestCase):
    def test_long_latency_uses_real_provider_stream_budget(self):
        for scenario, budget in (("long-latency", 1620), ("normal", 180)):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as temporary:
                ctx = self.context(Path(temporary), scenario=scenario, timeout_seconds=budget)
                _, profile, _ = dsh._prepare(ctx)
                rows = json.loads((profile / "cordis.patch.yml").read_text())
                provider = next(row for row in rows if row.get("id") == "llm-pi-ai")["config"]["providers"]["native-e2e"]
                if scenario == "long-latency":
                    self.assertEqual(provider.get("streamIdleTimeoutMs"), 1620000)
                else:
                    self.assertNotIn("streamIdleTimeoutMs", provider)

    def context(self, root, **changes):
        plugin = root / 'repo/ai-vision/agent-plugins/deepseek/observ-dsh/dist/index.js'
        plugin.parent.mkdir(parents=True)
        plugin.write_text('synthetic production boundary')
        workspace = root / 'workspace'
        workspace.mkdir()
        values = dict(repo=root / 'repo', out=root / 'out', workspace=workspace,
            runtime=(sys.executable,), provider_url='http://127.0.0.1:2222/v1',
            collector_url='http://127.0.0.1:3333', scenario='tool-loop', prompt='read receipt',
            app='dsh01', runtime_version='0.1.7-rc.2', resource={'ant.username': 'fixture-user'})
        values.update(changes)
        return CaseContext(**values)

    def test_http_prompt_uses_official_named_argument_envelope_and_records_raw_response(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass
            def do_POST(self):
                raw = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                valid = self.path == '/api/session/prompt' and raw['method'] == 'session/prompt' and raw['payload'] == {
                    'args': {'request': {'sessionId': 'physical-session', 'requestId': 'receipt-request',
                        'mode': 'queue', 'content': [{'type': 'text', 'text': 'read receipt'}]}}}
                value = {'type': 'server-response', 'rpcId': raw['rpcId'], 'result': (
                    {'ok': True, 'value': {'accepted': True}} if valid else
                    {'ok': False, 'error': {'code': 'gateway/arguments-invalid', 'message': 'bad physical request', 'details': {}}})}
                body = json.dumps(value).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        server = LoopbackHTTPServer(Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            exchanges = []
            gateway = dsh._Gateway(f'http://127.0.0.1:{server.server_port}', time.monotonic() + 5, exchanges)
            value = gateway.call('session/prompt', {'sessionId': 'physical-session', 'requestId': 'receipt-request',
                'mode': 'queue', 'content': [{'type': 'text', 'text': 'read receipt'}]})
            self.assertEqual(value, {'accepted': True})
            self.assertEqual(exchanges[0]['response']['result']['value'], {'accepted': True})
            with self.assertRaisesRegex(RuntimeError, 'bad physical request'):
                gateway.call('session/prompt', {'sessionId': 'physical-session'})
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)

    def test_websocket_follow_preserves_physical_frames_and_uses_official_endpoint(self):
        snapshot = {'type': 'snapshot', 'header': {'id': 'physical-session'}, 'cursor': 0, 'records': [], 'hasMore': False, 'projections': {}}
        event = {'type': 'event', 'event': {'type': 'turn/end', 'seq': 1, 'time': 100,
            'data': {'turn': 1, 'reason': {'kind': 'completed'}}}}
        def handler(connection):
            opened = json.loads(connection.recv())
            if connection.request.path != '/api/remote.mux' or opened != {'type': 'open', 'streamId': 'native-session',
                'endpoint': 'session/follow', 'payload': {'args': {'request': {
                    'address': {'kind': 'session', 'sessionId': 'physical-session'}, 'assistantStream': True, 'maxMessages': 1000}}}}:
                connection.close(1008, 'wrong official carrier')
                return
            for value in (snapshot, event):
                connection.send(json.dumps({'type': 'item', 'streamId': 'native-session', 'value': value}))
            connection.recv()
        server = serve(handler, '127.0.0.1', 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            events = []
            port = server.socket.getsockname()[1]
            with dsh._Feed(f'http://127.0.0.1:{port}', '', 'physical-session', time.monotonic() + 5, events) as feed:
                self.assertEqual(feed.next(), snapshot)
                self.assertEqual(feed.next(), event)
            self.assertEqual(events[1]['frame']['value'], event)
            self.assertEqual(json.loads(events[0]['raw'])['value'], snapshot)
        finally:
            server.shutdown()
            thread.join(2)

    def test_runtime_config_and_child_environment_isolate_credentials_and_home(self):
        with tempfile.TemporaryDirectory() as temporary:
            ctx = self.context(Path(temporary))
            inherited = {'OPENAI_API_KEY': 'synthetic-private', 'DEEPSEEK_API_KEY': 'synthetic-private',
                'DSH_HOME': '/do-not-touch', 'DSH_OBSERV_CONFIG': '/do-not-touch',
                'OTEL_EXPORTER_OTLP_HEADERS': 'synthetic-private', 'NVIDIA_API_KEY': 'synthetic-private'}
            with patch.dict(os.environ, inherited):
                env, profile, config = dsh._prepare(ctx)
            child = subprocess.run([sys.executable, '-c', 'import os,json; print(json.dumps(dict(os.environ)))'],
                env=env, capture_output=True, text=True, check=True)
            state = json.loads(child.stdout)
            self.assertEqual(state['DSH_HOME'], str(ctx.out / 'home/.dsh'))
            self.assertEqual(state['NATIVE_E2E_DSH_API_KEY'], 'native-e2e-placeholder')
            self.assertNotIn('synthetic-private', json.dumps(state))
            patch_rows = json.loads((profile / 'cordis.patch.yml').read_text())
            provider = next(row for row in patch_rows if row.get('id') == 'llm-pi-ai')['config']['providers']['native-e2e']
            self.assertEqual(provider['baseURL'], 'http://127.0.0.1:2222/v1')
            self.assertEqual(provider['api'], 'openai-completions')
            observer = json.loads(config.read_text())
            self.assertEqual(observer['tracesConfig']['endpoint'], 'http://127.0.0.1:3333/v1/traces')
            self.assertNotIn('synthetic-private', config.read_text())

    def test_nonlocal_provider_is_rejected_before_creating_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            ctx = self.context(Path(temporary), provider_url='https://invalid.example/v1')
            with self.assertRaisesRegex(ValueError, 'loopback'):
                dsh.run_case(ctx)
            self.assertFalse(ctx.out.exists())

    def test_nvidia_glm_uses_the_offered_low_reasoning_effort(self):
        with tempfile.TemporaryDirectory() as temporary:
            ctx = self.context(Path(temporary), mode='nvidia', model='z-ai/glm-5.3-flash')
            _, profile, _ = dsh._prepare(ctx)
            rows = json.loads((profile / 'cordis.patch.yml').read_text())
            provider = next(row for row in rows if row.get('id') == 'llm-pi-ai')['config']['providers']['native-e2e']
            self.assertEqual(provider['reasoning'], 'low')
            model = provider['models'][0]
            self.assertEqual(model['reasoningEfforts'], {'low': 'low', 'high': 'high', 'max': 'max'})
            self.assertTrue(model['compat']['supportsReasoningEffort'])
            self.assertNotIn('enable_thinking', model['compat'].get('chatTemplateKwargs', {}))

    def test_nvidia_deepseek_retains_explicit_non_thinking_configuration(self):
        with tempfile.TemporaryDirectory() as temporary:
            ctx = self.context(Path(temporary), mode='nvidia', model='deepseek-ai/deepseek-v4.1-flash')
            _, profile, _ = dsh._prepare(ctx)
            rows = json.loads((profile / 'cordis.patch.yml').read_text())
            model = next(row for row in rows if row.get('id') == 'llm-pi-ai')['config']['providers']['native-e2e']['models'][0]
            self.assertEqual(model['compat']['chatTemplateKwargs'], {'enable_thinking': False})

    def test_cancel_waits_for_a_nonempty_official_text_delta(self):
        frame = {'type': 'assistant-stream', 'frame': {'type': 'chunk', 'chunk': {
            'type': 'text-delta', 'index': 0, 'text': 'pending '}}}
        self.assertTrue(dsh._active_text(frame))
        self.assertFalse(dsh._active_text({'type': 'assistant-stream', 'frame': {'type': 'chunk',
            'chunk': {'type': 'text-delta', 'index': 0, 'text': ''}}}))
        self.assertFalse(dsh._active_text({'type': 'event', 'event': {'type': 'turn/start'}}))

    def test_version_failure_retains_actual_executable_version_without_starting_a_web_host(self):
        with tempfile.TemporaryDirectory() as temporary:
            ctx = self.context(Path(temporary), runtime=(sys.executable, '-c', 'print("0.1.7-rc.2")'),
                app='dsh02', runtime_version='0.2.0-rc.2')
            result = dsh.run_case(ctx)
            self.assertNotEqual(result.exit_code, 0)
            self.assertEqual(result.details.get('executable_version'), 'Python ' + sys.version.split()[0])
            self.assertEqual(result.details['launches'], [])


if __name__ == '__main__':
    unittest.main()
