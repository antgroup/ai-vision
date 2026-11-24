"""Exercise installer and hook credential HTTP routing with inherited proxies."""
import contextlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import unittest
from unittest import mock
import urllib.error

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import hook_runtime as runtime

loader = importlib.machinery.SourceFileLoader('connect_http_test', str(SCRIPTS / 'connect'))
spec = importlib.util.spec_from_loader(loader.name, loader)
installer = importlib.util.module_from_spec(spec)
loader.exec_module(installer)

SOCKS_ENV = {
    'HTTP_PROXY': 'socks5h://127.0.0.1:9',
    'HTTPS_PROXY': 'socks5h://127.0.0.1:9',
    'ALL_PROXY': 'socks5h://127.0.0.1:9',
    'NO_PROXY': '', 'no_proxy': '',
}


class OnboardingHttpTest(unittest.TestCase):
    def start_receiver(self, host='127.0.0.1', *, proxy=False):
        requests = []

        class Receiver(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                self.respond()

            def do_POST(self):
                self.respond()

            def respond(self):
                requests.append((self.path, self.headers.get('Authorization')))
                if self.path in ('/redirect', '/redirect-base/openapi/v1/ingest-tokens'):
                    self.send_response(302)
                    self.send_header('Location', '/unexpected-redirect-target')
                    self.end_headers()
                    return
                if self.path.endswith('/ingest-tokens') or proxy:
                    self.rfile.read(int(self.headers.get('Content-Length', 0)))
                    payload = {'code': 0, 'data': {
                        'space_id': 'fixture-space', 'agent_uuid': 'fixture-agent',
                        'endpoint': 'http://127.0.0.1:4318',
                        'token': 'fixture-short-token', 'expires_in': 900,
                    }}
                else:
                    payload = {'code': 0, 'data': 'fixture-catalog'}
                data = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        class Server(ThreadingHTTPServer):
            address_family = socket.AF_INET6 if ':' in host else socket.AF_INET

        server = Server((host, 0), Receiver)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def stop():
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

        self.addCleanup(stop)
        authority = f'[{host}]' if ':' in host else host
        return f'http://{authority}:{server.server_port}', requests

    def config(self, root, base):
        path = root / 'ingest.json'
        path.write_text(json.dumps({
            'space_id': 'fixture-space', 'agent_uuid': 'fixture-agent',
            'endpoint': 'http://127.0.0.1:4318', 'base_url': base,
            'agent_credential': 'fixture-agent-credential',
        }))
        return path

    def assert_loopback_works(self, base, requests):
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.dict(os.environ, SOCKS_ENV, clear=True):
            payload = installer.request_data(base + '/catalog')
            self.assertEqual(payload, 'fixture-catalog')
            path = self.config(Path(directory), base)
            _, token = runtime.token_for(path)
            self.assertEqual(token['token'], 'fixture-short-token')
            self.assertTrue(path.with_name('ingest-token.json').is_file())
        self.assertEqual(requests, [('/catalog', None),
                                   ('/openapi/v1/ingest-tokens', 'Bearer fixture-agent-credential')])

    def test_loopback_installer_and_refresh_ignore_socks_proxy(self):
        base, requests = self.start_receiver()
        self.assert_loopback_works(base, requests)

    def test_localhost_installer_and_refresh_ignore_socks_proxy(self):
        base, requests = self.start_receiver()
        self.assert_loopback_works(base.replace('127.0.0.1', 'LOCALHOST'), requests)

    def test_ipv6_loopback_installer_and_refresh_ignore_socks_proxy(self):
        try:
            base, requests = self.start_receiver('::1')
        except OSError as exc:
            self.skipTest(f'IPv6 loopback unavailable: {exc}')
        self.assert_loopback_works(base, requests)

    def test_remote_requests_still_use_configured_proxy(self):
        base, requests = self.start_receiver(proxy=True)
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.dict(os.environ, {'http_proxy': base, 'no_proxy': ''}, clear=True), \
                mock.patch('socket.getaddrinfo', wraps=socket.getaddrinfo) as resolve:
            self.assertEqual(installer.request_data('http://vision.invalid/catalog')['token'],
                             'fixture-short-token')
            path = self.config(Path(directory), 'http://vision.invalid')
            self.assertEqual(runtime.token_for(path)[1]['token'], 'fixture-short-token')
        self.assertEqual([item[0] for item in requests], [
            'http://vision.invalid/catalog', 'http://vision.invalid/openapi/v1/ingest-tokens'])
        self.assertTrue(all(call.args[0] == '127.0.0.1' for call in resolve.call_args_list))

    def test_loopback_redirects_remain_blocked(self):
        base, requests = self.start_receiver()
        with mock.patch.dict(os.environ, SOCKS_ENV, clear=True):
            with self.assertRaises(installer.ConnectError) as error:
                installer.request_data(base + '/redirect', headers={'Authorization': 'fixture-secret'})
            self.assertEqual(error.exception.status, 302)
            with tempfile.TemporaryDirectory() as directory:
                path = self.config(Path(directory), base + '/redirect-base')
                with self.assertRaises(urllib.error.HTTPError) as error:
                    runtime.token_for(path)
                self.assertEqual(error.exception.code, 302)
        self.assertEqual([item[0] for item in requests],
                         ['/redirect', '/redirect-base/openapi/v1/ingest-tokens'])

    def test_exporter_bypasses_only_its_loopback_target_and_preserves_parent(self):
        bypass_names = ('NO_PROXY', 'no_proxy')
        for agent, prefix in [('codex', 'CDXHOOKS'), ('claude-code', 'CCHOOKS'), ('kimi', 'KIMI')]:
            for endpoint in ('http://127.0.0.2:4318', 'http://localhost:4318', 'http://[::1]:4318'):
                with self.subTest(agent=agent, endpoint=endpoint), \
                        mock.patch.dict(os.environ, {**SOCKS_ENV, 'NO_PROXY': 'one.test',
                                                     'no_proxy': 'two.test'}, clear=True):
                    before = dict(os.environ)
                    env = runtime.hook_env({'agent': agent, 'product': 'Fixture',
                                            'config_dir': '/fixture', 'endpoint': endpoint},
                                           {'token': 'fixture'})
                    host = runtime.urllib.parse.urlsplit(endpoint).hostname
                    self.assertEqual(env['NO_PROXY'], env['no_proxy'])
                    # Windows folds environment names to uppercase. Preserve
                    # the aliases and values actually present on this host.
                    expected = {before[name] for name in bypass_names if before.get(name)}
                    expected.add(host)
                    if ':' in host:
                        # ureq matches http::Uri::host(), which retains IPv6 brackets.
                        expected.add(f'[{host}]')
                    self.assertEqual(set(env['no_proxy'].split(',')), expected)
                    self.assertEqual(env['HTTP_PROXY'], SOCKS_ENV['HTTP_PROXY'])
                    self.assertEqual(dict(os.environ), before)
                    self.assertEqual(env[prefix + '_COLLECTION_MODE'], 'full')
        with mock.patch.dict(os.environ, SOCKS_ENV, clear=True):
            before = dict(os.environ)
            expected = {name: before[name] for name in bypass_names if name in before}
            for endpoint in ('http://vision.invalid', 'http://localhost.example', 'http://127.0.0.1.example',
                             'http://10.0.0.1:4318'):
                env = runtime.hook_env({'agent': 'codex', 'product': 'Fixture',
                                        'config_dir': '/fixture', 'endpoint': endpoint}, {'token': 'fixture'})
                self.assertEqual({name: env[name] for name in bypass_names if name in env}, expected)
                self.assertEqual(dict(os.environ), before)

    def test_credential_network_failure_is_actionable_and_redacted(self):
        secret = 'fixture-secret-never-print'
        error = urllib.error.URLError('socks5h://user:' + secret + '@proxy.invalid')
        with mock.patch.object(sys, 'argv', ['hook_runtime', '--config', '/unused', '--binary', '/unused']), \
                mock.patch.object(runtime, 'token_for', side_effect=error), \
                mock.patch.object(runtime.subprocess, 'run') as binary, \
                contextlib.redirect_stderr(io.StringIO()) as stderr:
            self.assertEqual(runtime.main(), 0)
        self.assertIn('ingest credentials', stderr.getvalue())
        self.assertIn('proxy', stderr.getvalue())
        self.assertNotIn(secret, stderr.getvalue())
        binary.assert_not_called()


if __name__ == '__main__':
    unittest.main()
