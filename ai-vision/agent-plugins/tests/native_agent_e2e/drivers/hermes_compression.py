"""Official ACP restart with host-configured automatic threshold compaction."""
from __future__ import annotations

import json
import queue
import re
import subprocess
import threading
import time

from ._process import PIPE_DRAIN_SECONDS, _join_readers, _kill_tree, _launch as _launch_owned
from .hermes import _launch, _prepare, _raw_callbacks

try:
    from ..models import AgentResult, CaseContext
except ImportError:
    from models import AgentResult, CaseContext


class ACP:
    def __init__(self, ctx, env, deadline, messages):
        self.deadline, self.messages = deadline, messages
        self.process, self.job = _launch_owned([*ctx.runtime, 'acp'], cwd=ctx.workspace, env=env,
            text=True, encoding='utf-8', stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.pending = queue.Queue()
        self.output = []
        self.identity = 0
        self.readers = [threading.Thread(target=self._read, args=(stream, protocol), daemon=True)
            for stream, protocol in ((self.process.stdout, True), (self.process.stderr, False))]
        for reader in self.readers:
            reader.start()

    def _read(self, stream, protocol):
        try:
            for line in stream:
                if protocol:
                    self.pending.put(line)
                else:
                    self.output.append(line)
        finally:
            stream.close()
            if protocol:
                self.pending.put(None)

    def request(self, method, params):
        self.identity += 1
        message = {'jsonrpc': '2.0', 'id': self.identity, 'method': method, 'params': params}
        self.messages.append({'pid': self.process.pid, 'direction': 'client', 'message': message})
        self.process.stdin.write(json.dumps(message) + '\n')
        self.process.stdin.flush()
        text = []
        while True:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('ACP response deadline expired')
            line = self.pending.get(timeout=remaining)
            if line is None:
                raise ValueError('ACP exited before its response')
            self.output.append(line)
            row = json.loads(line)
            self.messages.append({'pid': self.process.pid, 'direction': 'server', 'message': row})
            if row.get('method') == 'session/update':
                update = row.get('params', {}).get('update', {})
                content = update.get('content', {})
                if update.get('sessionUpdate') == 'agent_message_chunk' and content.get('type') == 'text':
                    text.append(content.get('text', ''))
            if row.get('id') == self.identity:
                if row.get('error'):
                    raise ValueError(f'ACP rejected {method}: {json.dumps(row["error"])}')
                return row.get('result', {}), ''.join(text)

    def prompt(self, session, text):
        result, output = self.request('session/prompt', {'sessionId': session,
            'prompt': [{'type': 'text', 'text': text}]})
        if result.get('stopReason') != 'end_turn':
            raise ValueError(f'ACP did not finish its turn: {result}')
        return output

    def close(self):
        try:
            if not self.process.stdin.closed:
                try:
                    self.process.stdin.close()
                except BrokenPipeError:
                    pass  # The native host can exit before the test closes stdin.
            try:
                code = self.process.wait(timeout=max(0, min(10, self.deadline - time.monotonic())))
                _join_readers(self.readers, min(PIPE_DRAIN_SECONDS, max(0, self.deadline - time.monotonic())))
                if any(reader.is_alive() for reader in self.readers):
                    code = 124
            except subprocess.TimeoutExpired:
                code = 124
        finally:
            try:
                _kill_tree(self.process, self.job)
            finally:
                # Readers own their streams; closing from here can block on a read lock.
                _join_readers(self.readers)
        return code


def run_case(ctx: CaseContext, *, third_prompt: str) -> AgentResult:
    if ctx.mode != 'fake' or ctx.runtime_version != '0.21.5':
        raise ValueError('Native compression probe requires pinned Hermes 0.21.5 and loopback fake transport')
    if not third_prompt or ctx.scenario != 'multi-turn':
        raise ValueError('Native compression probe requires three user prompts')
    home, env, capture_native, _ = _prepare(ctx, compression=True)
    deadline = time.monotonic() + ctx.timeout_seconds
    code, output, _ = _launch([*ctx.runtime, '--version'], ctx, env, deadline)
    match = re.search(r'(?<!\d)(\d+\.\d+\.\d+)(?![\d.])', output)
    version = match.group(1) if match else 'unknown'
    details = {'plugin_loader': 'official-directory', 'plugin_path': str(home / 'plugins/observ-hermes'),
               'session_ids': [], 'acp_processes': [], 'acp_messages': [], 'compression': {}}
    if code or version != '0.21.5':
        details['error'] = 'Native compression requires official Hermes 0.21.5'
        return AgentResult(code or 1, None, version, output, details)
    session = None
    outputs = []
    try:
        for index in range(2):
            if index:
                # This is the user's documented host config, not host/plugin
                # state. The next actual user prompt starts the compaction.
                config_path = home / 'config.yaml'
                config = json.loads(config_path.read_text())
                settings = {'enabled': True, 'in_place': True, 'threshold_tokens': 3000}
                config['compression'].update(settings)
                config_path.write_text(json.dumps(config, indent=2) + '\n')
            client = ACP(ctx, env, deadline, details['acp_messages'])
            process = {'pid': client.process.pid, 'turns': []}
            details['acp_processes'].append(process)
            try:
                client.request('initialize', {'protocolVersion': 1, 'clientCapabilities': {},
                    'clientInfo': {'name': 'native-compression-e2e', 'version': '1'}})
                params = {'cwd': str(ctx.workspace), 'mcpServers': []}
                if index == 0:
                    result, _ = client.request('session/new', params)
                    session = result.get('sessionId')
                    if not isinstance(session, str) or not session:
                        raise ValueError('ACP returned no native session identity')
                    for ordinal, prompt in enumerate((ctx.prompt, ctx.followup_prompt), 1):
                        client.prompt(session, prompt)
                        details['session_ids'].append(session)
                        process['turns'].append(ordinal)
                else:
                    loaded, _ = client.request('session/load', {**params, 'sessionId': session})
                    provenance = loaded.get('_meta', {}).get('hermes', {}).get('sessionProvenance', {})
                    if provenance.get('acpSessionId') != session or provenance.get('currentHermesSessionId') != session:
                        raise ValueError('ACP load did not confirm the original native session identity')
                    details['resumed_session_id'] = provenance['currentHermesSessionId']
                    response = client.prompt(session, '/context')
                    if not re.search(r'Conversation: \d+ messages', response):
                        raise ValueError(response or 'ACP supplied no conversation context')
                    details['compression'] = {'trigger': 'threshold', 'process_pid': client.process.pid,
                        'config': settings, 'context_before': response}
                    client.prompt(session, third_prompt)
                    details['session_ids'].append(session)
                    process['turns'].append(3)
            finally:
                process['exit_code'] = client.close()
                outputs.append(''.join(client.output))
            if process['exit_code']:
                raise ValueError(f'ACP process exited with status {process["exit_code"]}')
    except (OSError, ValueError, TimeoutError, queue.Empty) as error:
        details['error'] = str(error) or type(error).__name__
        code = 1
    if capture_native:
        details['hermes_raw_callbacks'] = _raw_callbacks(ctx.out / 'raw-callbacks')
    return AgentResult(code, session, version, '\n'.join(outputs), details)
