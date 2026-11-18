#!/usr/bin/env python3
"""Exercise shipped production hooks against synthetic host files and local OTLP.

These are exporter integration fixtures, not real host/model task evidence.
"""
import argparse
from datetime import datetime, timezone
import gzip
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time

from hook_runtime import hook_env, write_private
from build_plugins import target_platform

ROOT = Path(__file__).resolve().parents[1]
MARKER = 'PRIVACY_CANARY_INPUT_OUTPUT_THINKING_TOOL_ERROR_9247'


def write_lines(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(json.dumps(row) + '\n' for row in rows))


def fixture(agent, root, session):
    path = root / 'host-session.jsonl'
    stamp = datetime.now(timezone.utc).isoformat()
    if agent == 'codex':
        rows = [json.loads(line) for line in (ROOT / 'codex/cdxhooks/tests/fixtures/codex_realtime_multi_write_stdin.jsonl').read_text().splitlines()]
        for row in rows:
            payload = row['payload']
            row['timestamp'] = stamp
            if row['type'] == 'session_meta':
                payload['id'] = session
            if 'message' in payload:
                payload['message'] = MARKER
            if 'arguments' in payload:
                payload['arguments'] = json.dumps({'cmd': MARKER})
            if 'output' in payload:
                payload['output'] = MARKER
            if 'last_agent_message' in payload:
                payload['last_agent_message'] = MARKER
        write_lines(path, rows)
    elif agent == 'claude-code':
        rows = [
            {'type': 'user', 'uuid': 'prompt-one', 'timestamp': stamp, 'message': {'role': 'user', 'content': MARKER}},
            {'type': 'assistant', 'timestamp': stamp, 'message': {'id': 'msg-one', 'role': 'assistant', 'model': 'fixture-model', 'usage': {'input_tokens': 12, 'output_tokens': 7}, 'content': [
                {'type': 'thinking', 'thinking': MARKER}, {'type': 'text', 'text': MARKER},
                {'type': 'tool_use', 'id': 'tool-one', 'name': 'Bash', 'input': {'command': MARKER}}], 'stop_reason': 'tool_use'}},
            {'type': 'user', 'timestamp': stamp, 'message': {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'tool-one', 'content': MARKER, 'is_error': True}]}},
            {'type': 'assistant', 'timestamp': stamp, 'message': {'id': 'msg-two', 'role': 'assistant', 'model': 'fixture-model', 'usage': {'input_tokens': 15, 'output_tokens': 9}, 'content': [{'type': 'text', 'text': MARKER}], 'stop_reason': 'end_turn'}},
        ]
        write_lines(path, rows)
    else:
        path = root / 'sessions' / hashlib.md5(str(root).encode()).hexdigest() / session / 'wire.jsonl'
        events = [
            ('TurnBegin', {'user_input': [{'type': 'text', 'text': MARKER}]}), ('StepBegin', {'n': 1}),
            ('ContentPart', {'type': 'think', 'think': MARKER}), ('ContentPart', {'type': 'text', 'text': MARKER}),
            ('StatusUpdate', {'token_usage': {'input_other': 12, 'output': 7, 'input_cache_read': 0, 'input_cache_creation': 0}, 'message_id': 'msg-one'}),
            ('ToolCall', {'id': 'tool-one', 'function': {'name': 'Shell', 'arguments': json.dumps({'cmd': MARKER})}}),
            ('ToolResult', {'tool_call_id': 'tool-one', 'return_value': {'is_error': True, 'output': MARKER, 'message': MARKER}}),
            ('TurnEnd', {}),
        ]
        write_lines(path, [{'timestamp': time.time() + i / 10, 'message': {'type': kind, 'payload': data}} for i, (kind, data) in enumerate(events)])
    return {'hook_event_name': 'Stop', 'session_id': session, 'cwd': str(root), 'transcript_path': str(path), 'prompt': MARKER}


def input_token_usage(captured):
    def attributes(value):
        if isinstance(value, dict):
            if 'key' in value and 'value' in value:
                yield value['key'], value['value']
            for child in value.values():
                yield from attributes(child)
        elif isinstance(value, list):
            for child in value:
                yield from attributes(child)

    return [float(next(iter(value.values()))) for _, data in captured
            for key, value in attributes(data) if key == 'gen_ai.usage.input_tokens']


def probe(agent, mode, dist):
    # The common Claude Code artifact does not include the optional
    # dynamic/turn_digest log exporters. Do not mistake absence for privacy.
    signals = ('traces', 'metrics') if agent == 'claude-code' else ('traces', 'logs', 'metrics')
    entry = json.loads((dist / agent / 'latest.json').read_text())
    artifact = next(item for item in entry['downloads'] if (item['platform'], item['arch']) == target_platform(None))
    binary = dist / agent / entry['version'] / artifact['filename']
    captured = []
    accepted_auth = []
    rejected_auth = []
    initial_token = 'fixture-ingest-token'
    renewed_token = 'fixture-ingest-token-renewed'
    # In-flight requests may still carry the previous, unexpired token.
    allowed_auth = {'Bearer ' + initial_token, 'Bearer ' + renewed_token}
    token = {}

    class Receiver(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            if self.headers.get('Content-Encoding') == 'gzip':
                body = gzip.decompress(body)
            status = 200
            if '/v1/' in self.path and not self.path.startswith('/openapi/'):
                authorization = self.headers.get('Authorization')
                if authorization not in allowed_auth:
                    rejected_auth.append(self.path)
                    status = 401
                    result = {'error': 'missing or invalid ingest authorization'}
                else:
                    accepted_auth.append((self.path, authorization))
                    captured.append((self.path, json.loads(body)))
                    result = {}
            else:
                result = {'code': 0, 'data': token}
            response = json.dumps(result).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(response)))
            self.end_headers()
            self.wfile.write(response)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Receiver)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix='aiv-privacy-hook-') as temp:
            root = Path(temp)
            managed = root / 'aivision' / agent
            endpoint = f'http://127.0.0.1:{server.server_port}'
            product = {'codex': 'Codex', 'claude-code': 'ClaudeCode', 'kimi': 'KimiCLI'}[agent]
            config = {'agent': agent, 'product': product, 'endpoint': endpoint, 'base_url': endpoint,
                      'agent_uuid': '05f9569b-47b9-4789-bede-f08b3f13d6f4', 'space_id': 'fixture-space',
                      'agent_credential': 'aiv-agent-fixture', 'config_dir': str(root), 'collection_mode': mode}
            token.update({'token': initial_token, 'space_id': config['space_id'], 'agent_uuid': config['agent_uuid'],
                          'agent_product': product, 'endpoint': endpoint, 'expires_in': 900, 'valid_until': time.time() + 900})
            write_private(managed / 'ingest.json', json.dumps(config))
            write_private(managed / 'ingest-token.json', json.dumps(token))
            prefix = {'codex': 'CDXHOOKS', 'claude-code': 'CCHOOKS', 'kimi': 'KIMI'}[agent]
            # The requested fixture mode must not inherit a user's runtime override.
            environment = os.environ.copy()
            environment['AIVISION_COLLECTION_MODE'] = mode
            environment[prefix + '_COLLECTION_MODE'] = mode
            env = hook_env(config, token, environ=environment)
            env[prefix + '_WORKER_MAX_RUNTIME_MS'] = '2000'
            env['KIMIHOOKS_EMIT_TOOL_CONTENT'] = '1'  # Privacy must override this legacy switch.
            if agent == 'kimi':
                write_private(managed / 'config/config.json', json.dumps({'enable_telemetry': True, 'log': {'user_prompts': True, 'model_outputs': True, 'tool_details': True, 'tool_outputs': True}}))
            payload = fixture(agent, root, 'privacy-fixture-session-' + mode)
            events = [payload]
            if agent != 'kimi':
                # Exercise durable callback queues, not just transcript export.
                tool = {'tool_name': 'Bash' if agent == 'claude-code' else 'exec_command',
                        'tool_use_id': 'tool-one', 'tool_input': {'command': MARKER}}
                events = [{**payload, 'hook_event_name': 'UserPromptSubmit'},
                          {**payload, **tool, 'hook_event_name': 'PreToolUse'},
                          {**payload, **tool, 'hook_event_name': 'PostToolUse',
                           'tool_response': json.dumps({'stdout': MARKER, 'stderr': MARKER,
                                                        'exit_code': 1, 'wall_time_seconds': 0.2})}, payload]
            for event in events:
                if agent != 'kimi' and event['hook_event_name'] == 'PostToolUse':
                    # Keep the launcher's original env: an existing worker must
                    # resolve credentials from the current private token cache.
                    token['token'] = renewed_token
                    write_private(managed / 'ingest-token.json', json.dumps(token))
                result = subprocess.run([str(binary)], input=json.dumps(event).encode(), env=env, capture_output=True, timeout=20)
                assert result.returncode == 0, result.stderr.decode(errors='replace')
                if mode == 'privacy':
                    assert MARKER.encode() not in result.stderr, f'{agent}: original content in stderr'
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline:
                if rejected_auth:
                    break
                # Detached workers can export tool traces/metrics before the
                # generation carrying usage. Receiving each signal is not yet
                # enough to evaluate the token assertion below.
                snapshot = list(captured)
                usage = input_token_usage(snapshot)
                renewed_metrics = agent == 'kimi' or any(
                    path.endswith('/metrics') and auth == 'Bearer ' + renewed_token
                    for path, auth in list(accepted_auth))
                if (all(any(path.endswith('/' + signal) for path, _ in snapshot) for signal in signals)
                        and usage and max(usage) > 0 and renewed_metrics):
                    break
                time.sleep(0.1)
            assert not rejected_auth, f'{agent}/{mode}: missing or invalid Authorization on {rejected_auth}'
            for signal in signals:
                values = [data for path, data in captured if path.endswith('/' + signal)]
                assert values, f'{agent}/{mode}: no {signal}; files: {[str(p.relative_to(root)) for p in managed.rglob("*") if p.is_file()]}'
                serialized = json.dumps(values)
                if mode == 'privacy':
                    assert MARKER not in serialized, f'{agent}: original content in {signal}'
                assert 'agentic.collection.mode' in serialized and mode in serialized, f'{agent}: mode missing in {signal}'
            usage = input_token_usage(list(captured))
            assert usage and max(usage) > 0, (
                f'{agent}/{mode}: token statistics missing or corrupted; input_tokens={usage}')
            if agent != 'kimi':
                assert any(path.endswith('/metrics') and auth == 'Bearer ' + renewed_token
                           for path, auth in accepted_auth), f'{agent}/{mode}: metrics did not use renewed ingest token'
            if mode == 'full':
                assert MARKER in json.dumps(captured), f'{agent}: Full fixture did not exercise original content'
            # Allow the bounded detached workers to exit before inspecting/cleaning.
            time.sleep(2.2)
            assert not rejected_auth, f'{agent}/{mode}: missing or invalid Authorization on {rejected_auth}'
            if mode == 'privacy':
                leaks = [str(path.relative_to(root)) for path in managed.rglob('*') if path.is_file() and MARKER.encode() in path.read_bytes()]
                assert not leaks, f'{agent}: content persisted in {leaks}'
            print(f'{agent}: {mode} production binary exported {"/".join(signals)}; authorization and local content boundary verified', flush=True)
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--agent', choices=['codex', 'claude-code', 'kimi'], required=True)
    parser.add_argument('--mode', choices=['full', 'privacy'], required=True)
    parser.add_argument('--dist', type=Path, default=ROOT / 'dist')
    args = parser.parse_args()
    probe(args.agent, args.mode, args.dist.resolve())
