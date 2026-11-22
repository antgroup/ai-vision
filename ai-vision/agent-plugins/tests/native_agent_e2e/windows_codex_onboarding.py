"""Serve repository source installers and one real Codex hook on loopback."""
from __future__ import annotations

import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
import tomllib
from urllib.parse import urlsplit
from uuid import uuid4

from capture import REQUESTS, decode_export, read_body


PUBLIC = '/api/public/onboarding'
EXCHANGE = '/openapi/v1/agent-installations/exchange'
INGEST_TOKENS = '/openapi/v1/ingest-tokens'
INGEST = '/api/public/otel/v1/'


class SourceOnboardingServer:
    """Exercise the explicit installation grant path without a real account."""

    def __init__(self, plugin_root: Path, hook_binary: Path):
        self.space_id = str(uuid4())
        self.agent_uuid = str(uuid4())
        self.installation_token = 'aiv-install-' + secrets.token_hex(12)
        self.agent_credential = 'aiv-agent-' + secrets.token_hex(12)
        self.ingest_token = 'fixture-ingest-' + secrets.token_hex(12)
        self._counts = dict(exchanges=0, refreshes=0, confirmations=0, reports=0)
        self._spans = []
        self._lock = threading.Lock()
        scripts = plugin_root / 'scripts'
        self.source = (scripts / 'connect').read_bytes()
        self.support_files = {
            name: (scripts / name).read_bytes() for name in ('native_config.py', 'hook_runtime.py')
        }
        self.hook = hook_binary.read_bytes()
        manifest = plugin_root / 'codex/cdxhooks/Cargo.toml'
        self.version = tomllib.loads(manifest.read_text(encoding='utf-8'))['package']['version']
        self.artifact_path = f'{PUBLIC}/plugins/codex/{self.version}/cdxhooks-windows-x64.exe'
        self.confirm_path = f'/openapi/v1/agents/{self.agent_uuid}/installation-complete'

    @property
    def counts(self):
        with self._lock:
            return dict(self._counts)

    @property
    def spans(self):
        with self._lock:
            return list(self._spans)

    def _catalog(self):
        files = [
            {'name': name, 'url': f'{self.endpoint}{PUBLIC}/support/{name}',
             'sha256': hashlib.sha256(content).hexdigest()}
            for name, content in self.support_files.items()
        ]
        download = {
            'platform': 'windows', 'arch': 'x64', 'url': self.endpoint + self.artifact_path,
            'target_filename': 'cdxhooks.exe', 'sha256': hashlib.sha256(self.hook).hexdigest(),
            'collection_modes': ['full', 'privacy'],
        }
        agent = {'key': 'codex', 'name': 'Codex', 'product': 'Codex', 'host': 'codex', 'binary': 'cdxhooks'}
        return {'support_files': files, 'agents': [
            {'key': 'codex', 'version': self.version, 'downloads': [download], 'plugin': agent}
        ]}

    def _token(self):
        return {'space_id': self.space_id, 'agent_uuid': self.agent_uuid, 'agent_product': 'Codex',
                'endpoint': self.endpoint + INGEST + 'traces', 'expires_in': 3600, 'token': self.ingest_token}

    def __enter__(self):
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def respond(self, content, content_type):
                self.send_response(200)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(content)))
                self.end_headers()
                self.wfile.write(content)

            def data(self, data):
                self.respond(json.dumps({'code': 0, 'data': data}).encode('utf-8'), 'application/json')

            def do_GET(self):
                path = urlsplit(self.path).path
                if path == PUBLIC + '/scripts/connect':
                    source = fixture.source.replace(b'"__AIVISION_BASE_URL__"',
                                                    json.dumps(fixture.endpoint).encode('utf-8'))
                    self.respond(source.replace(b'"__AIVISION_AGENT__"', b'""'), 'text/plain; charset=utf-8')
                elif path == PUBLIC + '/catalog.json':
                    self.data(fixture._catalog())
                elif path == fixture.artifact_path:
                    self.respond(fixture.hook, 'application/octet-stream')
                elif path.startswith(PUBLIC + '/support/') and (name := path.rsplit('/', 1)[-1]) in fixture.support_files:
                    self.respond(fixture.support_files[name], 'application/octet-stream')
                else:
                    self.send_error(404)

            def do_POST(self):
                self.connection.settimeout(10)
                path = urlsplit(self.path).path
                try:
                    body = read_body(self.rfile, self.headers)
                    if path.startswith(INGEST):
                        self.capture_report(path.removeprefix(INGEST), body)
                        return
                    request = json.loads(body)
                except (OSError, ValueError):
                    self.send_error(400)
                    return
                authorization = self.headers.get('Authorization')
                if path == EXCHANGE:
                    if (authorization != 'Bearer ' + fixture.installation_token
                            or request != {'agent_uuid': fixture.agent_uuid, 'space_id': fixture.space_id}):
                        self.send_error(403)
                        return
                    with fixture._lock:
                        consumed = fixture._counts['exchanges'] > 0
                        if not consumed:
                            fixture._counts['exchanges'] += 1
                    if consumed:
                        self.send_error(403)
                        return
                    self.data({**fixture._token(), 'agent_credential': fixture.agent_credential})
                elif path in {INGEST_TOKENS, fixture.confirm_path}:
                    valid_body = {} if path == fixture.confirm_path else {'agent_uuid': fixture.agent_uuid}
                    if authorization != 'Bearer ' + fixture.agent_credential or request != valid_body:
                        self.send_error(403)
                        return
                    with fixture._lock:
                        if path == fixture.confirm_path:
                            fixture._counts['confirmations'] += 1
                        else:
                            fixture._counts['refreshes'] += 1
                    if path == fixture.confirm_path:
                        self.data({'agent_uuid': fixture.agent_uuid, 'space_id': fixture.space_id, 'status': 1})
                    else:
                        self.data(fixture._token())
                else:
                    self.send_error(404)

            def capture_report(self, signal, body):
                if signal not in REQUESTS:
                    self.send_error(404)
                    return
                if self.headers.get('Authorization') != 'Bearer ' + fixture.ingest_token:
                    self.send_error(403)
                    return
                content_type = self.headers.get('Content-Type', 'application/x-protobuf')
                try:
                    report = decode_export(signal, body, content_type, self.headers.get('Content-Encoding', ''))
                except Exception:
                    self.send_error(400, 'Invalid OTLP export')
                    return
                if not any(report[signal] for signal in ('spans', 'metrics', 'logs')):
                    self.send_error(400, 'Empty OTLP export')
                    return
                with fixture._lock:
                    fixture._counts['reports'] += 1
                    fixture._spans.extend(report['spans'])
                if 'json' in content_type.lower():
                    self.respond(b'{}', 'application/json')
                else:
                    self.respond(b'', 'application/x-protobuf')

        self._server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.endpoint = f'http://127.0.0.1:{self._server.server_port}'
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_args):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)
