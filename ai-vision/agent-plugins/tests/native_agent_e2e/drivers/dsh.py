"""Official DSH Web Remote API with production observ-dsh and private profiles.

Both supported releases use the official HTTP unary /api/<namespace>/<method>
and WebSocket /api/remote.mux carriers. No observer hooks are invoked by this
driver. Evidence retains raw host replies and physical canonical JSONL.
"""
from __future__ import annotations

from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
import queue
import re
import signal
import socket
import subprocess
import threading
import time
from urllib.parse import urlsplit
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener
import uuid

from websockets.sync.client import connect

from models import AgentResult, CaseContext


SUPPORTED = {
    '0.1.7-rc.2': '477b4f420553e8a52c2fbccc464d7561b239c443',
    '0.2.0-rc.2': '639ed015397290b3745d163aafe02ffee4aa3f84',
}


def _loopback(value):
    parsed = urlsplit(value)
    if (parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', 'localhost', '::1'}
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError('native DSH endpoints must use an HTTP loopback service')
    return value.rstrip('/')


def _prepare(ctx):
    provider, collector = _loopback(ctx.provider_url), _loopback(ctx.collector_url).removesuffix('/v1')
    plugin = ctx.repo / 'ai-vision/agent-plugins/deepseek/observ-dsh/dist/index.js'
    if not plugin.is_file():
        raise ValueError('build the production observ-dsh plugin before native cases')
    home = ctx.out / 'home'
    profile = home / '.dsh/profiles/web'
    temporary = ctx.out / 'temp'
    for path in (home, profile, temporary):
        path.mkdir(parents=True, exist_ok=True)
    config_file = ctx.out / 'observ-dsh.json'
    config = {'enableTelemetry': True, 'serviceName': 'dsh', 'dynamicUsername': False, 'dynamicUid': False,
        'collectionMode': 'privacy' if ctx.scenario == 'privacy' else 'full',
        'otelExporter': {'endpoint': collector, 'protocol': 'http/json', 'headers': ctx.headers,
            'attributes': {key: value for key, value in ctx.resource.items() if key in {'ant.username', 'ant.uid'}},
            'resourceAttributes': ctx.resource, 'metricsTemporalityPreference': 'delta'},
        'metricExportInterval': 1000, 'logsExportInterval': 1000}
    for signal_name, field in (('traces', 'tracesConfig'), ('metrics', 'metricsConfig'), ('logs', 'eventsConfig')):
        config[field] = {'endpoint': collector + '/v1/' + signal_name, 'headers': ctx.headers, 'enabled': True}
    config_file.write_text(json.dumps(config, indent=2) + '\n', encoding='utf-8')
    model = {'id': ctx.model, 'name': ctx.model, 'contextWindow': 32768, 'maxTokens': 4096,
        'reasoning': False, 'input': ['text'], 'compat': {'supportsUsageInStreaming': True}}
    if ctx.mode == 'nvidia' and ctx.model.startswith('deepseek-ai/'):
        model.update(reasoning=True, compat={'supportsUsageInStreaming': True, 'thinkingFormat': 'chat-template',
            'chatTemplateKwargs': {'enable_thinking': False}})
    reasoning = {}
    if ctx.scenario == 'long-latency':
        # DSH's pi-ai watchdog observes semantic chunks rather than empty SSE
        # keepalives. This explicit test waits 1501 real seconds before a tool.
        reasoning['streamIdleTimeoutMs'] = ctx.timeout_seconds * 1000
    if ctx.mode == 'nvidia' and ctx.model in {'z-ai/glm-5.3', 'z-ai/glm-5.3-flash'}:
        model.update(reasoning=True, reasoningEfforts={'low': 'low', 'high': 'high', 'max': 'max'},
            compat={'supportsUsageInStreaming': True, 'supportsReasoningEffort': True, 'thinkingFormat': 'openai',
                'chatTemplateKwargs': {'clear_thinking': True}})
        reasoning['reasoning'] = 'low'
    rows = [
        {'id': 'llm-pi-ai', 'config': {'providers': {'native-e2e': {'api': 'openai-completions',
            'apiKeyEnv': 'NATIVE_E2E_DSH_API_KEY', 'baseURL': provider, 'models': [model],
            'retryPolicy': {'mode': 'normal', 'maxRetries': 2}, 'timeoutMs': ctx.timeout_seconds * 1000,
            **reasoning}}}},
        {'id': 'agent-default-model', 'config': {'provider': 'native-e2e', 'model': ctx.model}},
        {'id': 'session-persistence-jsonl', 'config': {'root': str(home / '.dsh/sessions'), 'compression': 'none'}},
        {'id': 'session-telemetry-otel', 'disabled': True},
        {'id': 'session-log-deepseek', 'disabled': True},
        {'id': 'llm-deepseek', 'disabled': True},
        {'id': 'llm-deepseek-account', 'disabled': True},
        {'id': 'session-title-llm', 'disabled': True},
        {'insert': [{'id': 'observ-dsh', 'name': str(plugin.resolve()), 'config': {'configFile': str(config_file)}}]},
    ]
    # JSON is a YAML subset accepted by Cordis; all mounts remain real plugins.
    (profile / 'cordis.patch.yml').write_text(json.dumps(rows, indent=2) + '\n', encoding='utf-8')
    (profile / 'package.json').write_text(json.dumps({'name': 'dsh-profile-web', 'private': True,
        'dependencies': {}, 'dsh': {'profile': {'bundles': ['@deepseek-ai/dsh-base', '@deepseek-ai/dsh-web-app']}}}) + '\n')
    keep = {'PATH', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT'}
    env = {key: os.environ[key] for key in os.environ if key.upper() in keep}
    env.update(HOME=str(home), USERPROFILE=str(home), DSH_HOME=str(home / '.dsh'),
        XDG_CONFIG_HOME=str(home / '.config'), XDG_CACHE_HOME=str(home / '.cache'),
        XDG_DATA_HOME=str(home / '.local/share'), XDG_STATE_HOME=str(home / '.local/state'),
        TEMP=str(temporary), TMP=str(temporary), TMPDIR=str(temporary),
        NO_PROXY='127.0.0.1,localhost,::1', no_proxy='127.0.0.1,localhost,::1',
        NATIVE_E2E_DSH_API_KEY='native-e2e-placeholder', DSH_PERMISSION_MODE='danger-full-access')
    return env, profile, config_file


class _Gateway:
    def __init__(self, url, deadline, exchanges):
        self.url, self.deadline, self.exchanges = url, deadline, exchanges
        self.cookies = CookieJar()
        self.opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(self.cookies))

    @property
    def cookie(self):
        return '; '.join(cookie.name + '=' + cookie.value for cookie in self.cookies)

    def authenticate(self, launched_url):
        # Exchange the official process token for its authority-bound cookie.
        with self.opener.open(launched_url, timeout=max(.1, min(5, self.deadline - time.monotonic()))) as response:
            response.read(1024)
        self.exchanges.append({'method': 'GET', 'endpoint': '/', 'status': response.status,
            'purpose': 'official-launch-token-exchange'})

    def call(self, endpoint, request=None):
        rpc_id = 'native-' + uuid.uuid4().hex
        body = {'type': 'client-request', 'rpcId': rpc_id, 'method': endpoint,
            'payload': {'args': {} if request is None else {'request': request}}}
        encoded = json.dumps(body).encode()
        operation = Request(self.url + '/api/' + endpoint, data=encoded,
            headers={'Content-Type': 'application/json'}, method='POST')
        with self.opener.open(operation, timeout=max(.1, min(20, self.deadline - time.monotonic()))) as response:
            value = json.loads(response.read())
        self.exchanges.append({'method': 'POST', 'endpoint': '/api/' + endpoint,
            'request': body, 'response': value, 'status': response.status})
        result = value.get('result', {})
        if value.get('type') != 'server-response' or value.get('rpcId') != rpc_id or result.get('ok') is not True:
            raise RuntimeError(f'DSH Remote {endpoint} failed: {result.get("error", value)}')
        return result.get('value')


class _Feed:
    def __init__(self, url, cookie, session_id, deadline, events):
        self.url, self.cookie, self.session_id = url, cookie, session_id
        self.deadline, self.events = deadline, events

    def __enter__(self):
        self.connection = connect(self.url.replace('http://', 'ws://', 1) + '/api/remote.mux',
            additional_headers={'Cookie': self.cookie} if self.cookie else {}, proxy=None,
            open_timeout=max(.1, min(10, self.deadline - time.monotonic())), close_timeout=2, max_size=16 * 1024 * 1024)
        self.socket = self.connection.__enter__()
        self.socket.send(json.dumps({'type': 'open', 'streamId': 'native-session', 'endpoint': 'session/follow',
            'payload': {'args': {'request': {'address': {'kind': 'session', 'sessionId': self.session_id},
                'assistantStream': True, 'maxMessages': 1000}}}}))
        return self

    def next(self):
        raw = self.socket.recv(timeout=max(.1, self.deadline - time.monotonic()))
        value = json.loads(raw)
        self.events.append({'raw': raw, 'frame': value, 'received_at': time.time()})
        if value.get('type') != 'item' or value.get('streamId') != 'native-session':
            raise RuntimeError(f'DSH follow carrier ended: {value}')
        return value.get('value')

    def __exit__(self, *_args):
        try:
            self.socket.send(json.dumps({'type': 'cancel', 'streamId': 'native-session'}))
        except Exception:
            pass
        self.connection.__exit__(*_args)


def _stop(process):
    if process.poll() is None:
        if os.name == 'posix':
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            if os.name == 'posix':
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            process.wait(timeout=3)


class _Server:
    def __init__(self, ctx, env, deadline, launches, exchanges):
        self.ctx, self.env, self.deadline, self.launches, self.exchanges = ctx, env, deadline, launches, exchanges

    def __enter__(self):
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            port = reservation.getsockname()[1]
        self.url = f'http://127.0.0.1:{port}'
        command = [*self.ctx.runtime, 'web', '--no-open', '--port', str(port)]
        self.process = subprocess.Popen(command, cwd=self.ctx.workspace, env=self.env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace',
            start_new_session=os.name == 'posix', bufsize=1)
        self.row = {'pid': self.process.pid, 'command': command, 'started_at': time.time(), 'startup_output': []}
        self.launches.append(self.row)
        pending = queue.Queue()
        def read():
            for line in self.process.stdout:
                # A fresh local browser launch token never belongs in evidence.
                self.row['startup_output'].append(re.sub(r'([?&]token=)[^\s\x1b]+', r'\1[redacted]', line))
                pending.put(line)
            pending.put(None)
        self.reader = threading.Thread(target=read, daemon=True)
        self.reader.start()
        try:
            while time.monotonic() < self.deadline:
                line = pending.get(timeout=max(.1, self.deadline - time.monotonic()))
                if line is None:
                    raise RuntimeError('official DSH Web host exited before readiness')
                launched = re.search(re.escape(self.url) + r'[^\s\x1b]*', line)
                if launched and 'token=' in launched.group(0):
                    self.gateway = _Gateway(self.url, self.deadline, self.exchanges)
                    self.gateway.authenticate(launched.group(0))
                    self.gateway.call('session/modelCatalog')
                    self.row['ready_at'] = time.time()
                    return self
            raise TimeoutError('official DSH Web host exceeded readiness deadline')
        except Exception:
            self.__exit__()
            raise

    def __exit__(self, *_args):
        _stop(self.process)
        self.reader.join(2)
        self.row.update(exit_code=self.process.returncode, stopped_at=time.time())
        if self.process.stdout:
            self.process.stdout.close()


def _records(value):
    if value.get('type') == 'snapshot':
        return [item['event'] for item in value.get('records', []) if item.get('type') == 'event']
    return [value['event']] if value.get('type') == 'event' else []


def _active_text(value):
    frame = value.get('frame', {}) if value.get('type') == 'assistant-stream' else {}
    chunk = frame.get('chunk', {}) if frame.get('type') == 'chunk' else {}
    return chunk.get('type') == 'text-delta' and bool(chunk.get('text'))


def _transcript(root, session_id):
    for path in root.rglob('*.jsonl'):
        rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
        if rows and rows[0].get('type') == 'session' and rows[0].get('id') == session_id:
            return path, rows[0], rows[1:]
    raise RuntimeError('official DSH did not persist the physical Session JSONL')


def run_case(ctx):
    _loopback(ctx.provider_url)
    _loopback(ctx.collector_url)
    if ctx.mode not in {'fake', 'nvidia'} or ctx.runtime_version not in SUPPORTED:
        raise ValueError('DSH requires a supported pinned release and fake or nvidia mode')
    if not ctx.runtime or not 0 < ctx.timeout_seconds <= 1800:
        raise ValueError('DSH requires a runtime and bounded positive timeout')
    env, profile, config_file = _prepare(ctx)
    deadline = time.monotonic() + ctx.timeout_seconds
    version_process = subprocess.run([*ctx.runtime, '--version'], cwd=ctx.workspace, env=env,
        capture_output=True, text=True, encoding='utf-8', timeout=min(30, ctx.timeout_seconds))
    executable = subprocess.run([ctx.runtime[0], '--version'], cwd=ctx.workspace, env=env,
        capture_output=True, text=True, encoding='utf-8', timeout=min(10, ctx.timeout_seconds))
    version = version_process.stdout.strip().removeprefix('v')
    details = {'runtime_command': list(ctx.runtime), 'official_commit': SUPPORTED[ctx.runtime_version],
        'executable_version': executable.stdout.strip(),
        'transport': 'official-http-websocket', 'profile_dir': str(profile), 'observer_config': str(config_file),
        'launches': [], 'api_exchanges': [], 'native_events': [], 'history_snapshots': [],
        'turns_completed': 0, 'processes': 0, 'cancelled': False, 'native_abort': False,
        'resumed_session_id': None, 'timed_out': False}
    for argument in ctx.runtime:
        runtime_path = Path(argument)
        if not runtime_path.is_file() or 'node_modules' not in runtime_path.parts:
            continue
        inventory = next((ancestor / 'dsh-pins.json' for ancestor in list(runtime_path.resolve().parents)[:5]
            if (ancestor / 'dsh-pins.json').is_file()), None)
        if inventory is not None:
            details['runtime_pins'] = json.loads(inventory.read_text())
            break
    if version_process.returncode or version != ctx.runtime_version:
        details.update(phase='version', version_stdout=version_process.stdout, version_stderr=version_process.stderr)
        return AgentResult(version_process.returncode or 1, None, version, '', details)
    session_id = None
    events = {}
    code = 0
    try:
        for prompt in (ctx.prompt, ctx.followup_prompt):
            if not prompt:
                continue
            with _Server(ctx, env, deadline, details['launches'], details['api_exchanges']) as server:
                details['processes'] += 1
                if session_id is None:
                    created = server.gateway.call('session/create', {'cwd': str(ctx.workspace), 'agentPreset': 'standard'})
                    session_id = created['sessionId']
                    server.gateway.call('session/selectModel', {'sessionId': session_id, 'provider': 'native-e2e', 'model': ctx.model})
                else:
                    details['resumed_session_id'] = session_id
                    before = server.gateway.call('session/page', {'address': {'kind': 'session', 'sessionId': session_id},
                        'throughSeq': max(events), 'maxMessages': 1000})
                    details['history_snapshots'].append({'phase': 'cold-before-resume', 'session_id': session_id, 'response': before})
                prior_turns = {event['seq'] for event in events.values() if event['type'] == 'turn/end'}
                # In a fresh second process this operation invokes agents.resume.
                server.gateway.call('session/prompt', {'sessionId': session_id, 'requestId': 'native-' + uuid.uuid4().hex,
                    'mode': 'queue', 'content': [{'type': 'text', 'text': prompt}]})
                with _Feed(server.url, server.gateway.cookie, session_id, deadline, details['native_events']) as feed:
                    requested_cancel = False
                    while True:
                        value = feed.next()
                        if value.get('type') == 'snapshot':
                            details['history_snapshots'].append({'phase': 'follow-opening', 'session_id': session_id, 'response': value})
                        for event in _records(value):
                            events[event['seq']] = event
                        # Wait for a real active streamed text chunk before abort.
                        if ctx.scenario == 'cancel' and not requested_cancel and _active_text(value):
                            server.gateway.call('session/cancel', {'sessionId': session_id})
                            requested_cancel = True
                        ended = [event for event in events.values() if event['type'] == 'turn/end' and event['seq'] not in prior_turns]
                        if ended:
                            reason = ended[-1].get('data', {}).get('reason', {}).get('kind')
                            if ctx.scenario == 'cancel':
                                if not requested_cancel or reason != 'aborted':
                                    raise RuntimeError(f'official DSH did not cancel its active turn: {reason}')
                                details.update(cancelled=True, native_abort=True)
                            elif reason != 'completed':
                                raise RuntimeError(f'official DSH turn failed: {ended[-1]}')
                            else:
                                details['turns_completed'] += 1
                            break
                history = server.gateway.call('session/page', {'address': {'kind': 'session', 'sessionId': session_id},
                    'throughSeq': max(events), 'maxMessages': 1000})
                details['history_snapshots'].append({'phase': 'after-turn', 'session_id': session_id, 'response': history})
                # Give the production 2s trace batch and 1s metric/log periods
                # time to export already-ended turns on every OS. Windows
                # TerminateProcess cannot deliver the CLI's SIGTERM drain.
                time.sleep(min(2.25, max(0, deadline - time.monotonic())))
            if ctx.scenario == 'cancel':
                break
    except Exception as error:
        code = 1
        details.update(error=str(error), phase='native-api', timed_out=isinstance(error, (TimeoutError, queue.Empty)))
    if session_id:
        try:
            transcript, header, physical = _transcript(ctx.out / 'home/.dsh/sessions', session_id)
            details.update(transcript_path=str(transcript), canonical_header=header, canonical_events=physical)
        except (OSError, ValueError, RuntimeError) as error:
            code = 1
            details['transcript_error'] = str(error)
    details['logical_events'] = sorted(events.values(), key=lambda event: event['seq'])
    answers = []
    for event in details['logical_events']:
        if event['type'] == 'assistant/message':
            content = event.get('data', {}).get('message', {}).get('content', [])
            answers.extend(block['text'] for block in content if block.get('type') == 'text' and isinstance(block.get('text'), str))
    return AgentResult(code, session_id, version, '\n'.join(answers), details)
