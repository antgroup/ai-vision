"""Browser orchestration security/lifecycle tests; no Docker or providers."""
import json
import os
from pathlib import Path
import stat
import signal
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import httpx

import browser_platform as browser


class BrowserPlatformTests(unittest.TestCase):
    @unittest.skipIf(os.name == 'nt', 'POSIX private state permissions')
    def test_refresh_uses_public_csrf_flow_and_private_copy_without_overwriting_input(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); runtime = root / 'runtime'; runtime.mkdir()
            auth = root / 'auth.json'
            auth.write_text(json.dumps({'cookies': [
                {'name': 'aivision_access', 'value': 'expired-access', 'domain': '127.0.0.1', 'path': '/', 'expires': 1},
                {'name': 'aivision_refresh', 'value': 'old-refresh', 'domain': '127.0.0.1', 'path': '/api/v1/auth/refresh', 'expires': -1}], 'origins': []}))
            auth.chmod(0o600); original = auth.read_bytes(); calls = []; private = set()
            def reply(request):
                calls.append(request.url.path)
                if request.url.path.endswith('/csrf'):
                    return httpx.Response(200, json={'code': 0}, headers={'set-cookie': 'csrftoken=csrf-new; Path=/'})
                if request.url.path.endswith('/refresh'):
                    self.assertEqual(request.headers['origin'], 'http://127.0.0.1:8000')
                    self.assertEqual(request.headers['x-csrftoken'], 'csrf-new')
                    self.assertIn('aivision_refresh=old-refresh', request.headers['cookie'])
                    return httpx.Response(200, json={'code': 0}, headers=[
                        ('set-cookie', 'aivision_access=new-access; Path=/; HttpOnly'),
                        ('set-cookie', 'aivision_refresh=new-refresh; Path=/api/v1/auth/refresh; HttpOnly')])
                return httpx.Response(200, json={'code': 0, 'data': {'user': {'user_uuid': 'test-user'}}})
            client = httpx.Client
            with patch.object(browser.httpx, 'Client', side_effect=lambda **kwargs: client(**kwargs, transport=httpx.MockTransport(reply))):
                fresh = browser.prepare_browser_auth('http://127.0.0.1:8000', runtime, private, auth_file=auth,
                                                     out=root / 'artifacts', expected_user='test-user')
            self.assertEqual(calls, ['/api/v1/auth/csrf', '/api/v1/auth/refresh', '/api/v1/auth/me'])
            self.assertEqual(auth.read_bytes(), original)
            self.assertEqual(stat.S_IMODE(fresh.stat().st_mode), 0o600)
            values = {row['name']: row['value'] for row in json.loads(fresh.read_text())['cookies']}
            self.assertEqual(values['aivision_access'], 'new-access')
            self.assertEqual(values['aivision_refresh'], 'new-refresh')
            self.assertTrue({'old-refresh', 'new-refresh', 'new-access', 'csrf-new'} <= private)

    @unittest.skipIf(os.name == 'nt', 'POSIX private state permissions')
    def test_rejected_refresh_creates_no_browser_auth_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); auth = root / 'input.json'
            auth.write_text('{"cookies":[{"name":"aivision_refresh","value":"expired","domain":"127.0.0.1","path":"/"}],"origins":[]}')
            auth.chmod(0o600)
            client = httpx.Client
            def reply(request):
                return httpx.Response(200 if request.url.path.endswith('/csrf') else 401,
                                      json={'code': 0 if request.url.path.endswith('/csrf') else 401})
            with patch.object(browser.httpx, 'Client', side_effect=lambda **kwargs: client(**kwargs, transport=httpx.MockTransport(reply))):
                with self.assertRaises(RuntimeError):
                    browser.prepare_browser_auth('http://127.0.0.1:8000', root, set(), auth_file=auth, out=root / 'artifacts')
            self.assertFalse((root / 'browser-auth.json').exists())

    def test_termination_handler_unwinds_owned_process_and_runtime_cleanup(self):
        callbacks = {}
        with patch.object(browser.signal, 'signal', side_effect=lambda number, callback: callbacks.update({number: callback})):
            browser.install_signal_handlers()
        self.assertIn(signal.SIGTERM, callbacks)
        with self.assertRaises(KeyboardInterrupt): callbacks[signal.SIGTERM](signal.SIGTERM, None)

    def test_fake_environment_does_not_inherit_provider_or_telemetry_credentials(self):
        env = browser.child_environment({"PATH": "/tools", "NVIDIA_API_KEY": "nv-private",
            "AIVISION_LIVE_PROVIDER_KEY": "live-private", "OPENAI_API_KEY": "open-private",
            "LANGFUSE_SECRET_KEY": "lf-private", "AIVISION_BROWSER_AUTH_FILE": "stale",
            "HTTPS_PROXY": "http://proxy"})
        self.assertEqual(env["PATH"], "/tools")
        self.assertEqual(env["HTTPS_PROXY"], "http://proxy")
        self.assertFalse(any("private" in value or value == "stale" for value in env.values()))

    def test_provider_is_explicit_and_nvidia_selection_requires_secret_before_deploy(self):
        self.assertIsNone(browser.provider_key("fake", {"NVIDIA_API_KEY": "unused"}))
        with self.assertRaisesRegex(ValueError, "NVIDIA_API_KEY"):
            browser.provider_key("nvidia", {})
        with self.assertRaises(ValueError):
            browser.provider_key("nvidia", {"NVIDIA_API_KEY": "key\nOTHER=bad"})

    def test_target_is_loopback_and_public_projection_cannot_leak_keys(self):
        target = {"endpoint": "http://127.0.0.1:8000", "space_id": "space", "agent_uuid": "agent",
                  "agent_product": "langfuse", "space_key": "secret", "data_backend": "oceanbase"}
        self.assertEqual(browser.validate_target({"langfuse": target}), target)
        self.assertEqual(browser.public_target(target),
                         {"spaceId": "space", "agentUuid": "agent", "agentProduct": "langfuse"})
        for endpoint in ("https://production.invalid", "http://127.0.0.1:8000?secret=x", "http://user:pass@localhost"):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                browser.validate_target({"langfuse": {**target, "endpoint": endpoint}})

    @unittest.skipIf(os.name == 'nt', 'POSIX permissions; real browser platform runs on Linux')
    def test_private_inputs_must_be_private_regular_files_outside_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); out = root / "out"; out.mkdir()
            private = root / "auth.json"; private.write_text('{"cookies": []}'); private.chmod(0o600)
            self.assertEqual(browser.read_private_json(private, out), {"cookies": []})
            private.chmod(0o644)
            with self.assertRaises(ValueError): browser.read_private_json(private, out)
            private.chmod(0o600)
            alias = root / "alias"; alias.symlink_to(private)
            with self.assertRaises(ValueError): browser.read_private_json(alias, out)
            inside = out / "private.json"; inside.write_text('{}'); inside.chmod(0o600)
            with self.assertRaises(ValueError): browser.read_private_json(inside, out)

    def test_artifact_audit_redacts_text_and_rejects_auth_network_archives(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "run.log").write_text("failure with super-private-secret")
            (root / "trace.zip").write_bytes(b"opaque-network-session")
            (root / "safe.json").write_text('{"checks": 12}')
            result = browser.audit_artifacts(root, {"super-private-secret"})
            self.assertFalse(result["passed"])
            self.assertNotIn("super-private-secret", (root / "run.log").read_text())
            self.assertFalse((root / "trace.zip").exists())
            self.assertTrue((root / "safe.json").exists())

    def test_fake_coverage_rejects_partial_and_wrong_sdk_profile(self):
        rows = [{"case": f"{framework}-langfuse-{scenario}", "execute_exit": 0, "verify_exit": 0,
                 "source_verified": True, "platform_verified": True}
                for framework in ("langchain", "deepagents") for scenario in browser.shared.scenarios()]
        summary = {"status": "passed", "mode": "ci", "data_backend": "oceanbase", "sdks": ["langfuse"],
                   "langfuse_version": "5.0.2", "expected_cases": 22, "executed_cases": 22, "cases": rows}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / "python5").mkdir()
            (root / "python5/summary.json").write_text(json.dumps(summary))
            for name, version in (("js5", "5.14.0"), ("js6", "6.0.0")):
                (root / name).mkdir()
                (root / name / "platform-readback.json").write_text(json.dumps({"passed": True, "traces": 33, "failures": []}))
                (root / name / "ledger.json").write_text(json.dumps([{"sdkVersion": version}]))
                (root / name / "ledger.json.lifecycle.json").write_text(json.dumps([{"sdkVersion": version}]))
            self.assertEqual(browser.verify_collection(root, "fake")["python_cases"], 22)
            (root / "js6/platform-readback.json").write_text('{"passed":true,"traces":32,"failures":[]}')
            with self.assertRaises(AssertionError): browser.verify_collection(root, "fake")

    def test_live_commands_have_fixed_endpoint_caps_and_no_key_in_argv(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commands = browser.collection_commands(Path('/repo'), root, root / 'private', source='nvidia',
                                                   python='/python', node='/node')
            self.assertEqual(len(commands), 3)
            self.assertEqual(sum(item['requests'] for item in commands), 7)
            self.assertEqual([item['requests'] for item in commands], [3, 2, 2])
            for command in commands:
                self.assertIn(browser.NVIDIA_MODEL, command['argv'])
                self.assertIn(browser.NVIDIA_BASE_URL, command['argv'])
                self.assertIn('--disable-thinking', command['argv'])
                self.assertNotIn('NVIDIA_API_KEY=', ' '.join(command['argv']))

    def test_browser_command_uses_dedicated_config_and_private_auth(self):
        env = browser.browser_environment({}, Path('/safe/manifest.json'), Path('/private/auth.json'),
                                          Path('/safe/browser'), 'http://127.0.0.1:41966', 'http://127.0.0.1:8000')
        self.assertEqual(env['AIVISION_BROWSER_AUTH_FILE'], str(Path('/private/auth.json')))
        self.assertEqual(env['VITE_DEV_API_TARGET'], 'http://127.0.0.1:8000')
        self.assertEqual(env['VITE_DEV_API_PRESERVE_HOST'], '1')
        self.assertEqual(env['AIVISION_BROWSER_OUTPUT_DIR'], str(Path('/safe/browser')))

    def test_browser_report_rejects_empty_skipped_failed_and_wrong_case_counts(self):
        tests = [{'status': 'expected', 'results': [{'status': 'passed'}]} for _ in range(24)]
        report = {'stats': {'expected': 24, 'unexpected': 0, 'flaky': 0, 'skipped': 0},
                  'suites': [{'specs': [{'tests': tests}]}]}
        self.assertEqual(browser.verify_browser_report(report, 'fake'), 24)
        for invalid in ({**report, 'suites': []}, {**report, 'stats': {**report['stats'], 'skipped': 1}},
                        {**report, 'stats': {**report['stats'], 'expected': 0}}):
            with self.assertRaises(AssertionError): browser.verify_browser_report(invalid, 'fake')
        tests[0]['results'][0]['status'] = 'failed'
        with self.assertRaises(AssertionError): browser.verify_browser_report(report, 'fake')

    @unittest.skipIf(os.name == 'nt', 'POSIX permissions; real browser platform runs on Linux')
    def test_failed_child_is_not_retried_and_preserves_failed_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); out = root / 'out'; out.mkdir(); runtime = root / 'private'; runtime.mkdir()
            target = {"endpoint": "http://127.0.0.1:8000", "space_id": "space", "agent_uuid": "agent",
                      "agent_product": "langfuse", "space_key": "test-space-key", "data_backend": "oceanbase"}
            auth = runtime / 'auth.json'; auth.write_text('{"cookies": [{"value":"cookie-value"}], "origins":[]}'); auth.chmod(0o600)
            smoke = browser.shared.Smoke(out, browser.child_environment({"PATH": os.environ.get('PATH', '')}), {'test-space-key'})
            with patch.object(smoke, 'run', return_value=subprocess.CompletedProcess([], 9, '', '')) as run:
                with self.assertRaisesRegex(RuntimeError, 'python5'):
                    browser.run_acceptance(repo=Path('/repo'), out=out, runtime=runtime, smoke=smoke,
                        targets={'langfuse': target}, auth_file=auth, source='fake', python='/python', node='/node')
            self.assertEqual(run.call_count, 1)
            summary = json.loads((out / 'browser-summary.json').read_text())
            self.assertEqual(summary['status'], 'failed')
            self.assertEqual(summary['completed_stages'], [])
            self.assertTrue(summary['artifact_audit']['passed'])
            self.assertFalse((runtime / 'targets.json').exists())

    @unittest.skipIf(os.name == 'nt', 'POSIX permissions; real browser platform runs on Linux')
    def test_existing_mode_never_calls_docker_owner_and_preserves_supplied_auth(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = {'langfuse': {'endpoint': 'http://127.0.0.1:8000', 'space_id': 'space',
                'agent_uuid': 'agent', 'agent_product': 'langfuse', 'space_key': 'private-key', 'data_backend': 'oceanbase'}}
            targets = root / 'targets.json'; targets.write_text(json.dumps(target)); targets.chmod(0o600)
            auth = root / 'auth.json'; auth.write_text('{"cookies":[{"value":"cookie"}],"origins":[]}'); auth.chmod(0o600)
            runtime_paths = []
            def accepted(**kwargs):
                runtime_paths.append(kwargs['runtime'])
                return {'verified': True, 'browser_acceptance': {'status': 'passed'}}
            with patch.object(browser.shared, 'main') as deploy, patch.object(browser, 'run_acceptance', side_effect=accepted):
                code = browser.main(['--repo', str(root), '--out', str(root / 'out'), '--targets', str(targets), '--auth-file', str(auth)])
            self.assertEqual(code, 0)
            deploy.assert_not_called()
            self.assertTrue(auth.exists())
            self.assertTrue(targets.exists())
            self.assertFalse(runtime_paths[0].exists())

    def test_fresh_deployment_does_not_inherit_nvidia_key(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'NVIDIA_API_KEY': 'nv-private'}):
            def deploy(*_args, **_kwargs):
                self.assertNotIn('NVIDIA_API_KEY', os.environ)
                return 9
            with patch.object(browser.shared, 'main', side_effect=deploy):
                code = browser.main(['--repo', directory, '--out', str(Path(directory) / 'out'), '--source', 'nvidia'])
            self.assertEqual(code, 9)
            self.assertEqual(os.environ['NVIDIA_API_KEY'], 'nv-private')

    @unittest.skipIf(os.name == 'nt', 'POSIX permissions; real browser platform runs on Linux')
    def test_live_success_executes_three_collections_once_and_never_gives_browser_key(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); out = root / 'out'; out.mkdir(); runtime = root / 'private'; runtime.mkdir()
            auth = runtime / 'auth.json'; auth.write_text('{"cookies":[{"value":"cookie-private"}],"origins":[]}'); auth.chmod(0o600)
            target = {'endpoint': 'http://127.0.0.1:8000', 'space_id': 'space', 'agent_uuid': 'agent',
                      'agent_product': 'langfuse', 'space_key': 'private-space-key', 'data_backend': 'oceanbase'}
            smoke = browser.shared.Smoke(out, {}, set())
            calls = []
            def run(name, command, **kwargs):
                calls.append((name, command, kwargs.get('env', {})))
                self.assertTrue((runtime / 'provider.env').exists())
                self.assertEqual(stat.S_IMODE((runtime / 'provider.env').stat().st_mode), 0o600)
                if name == 'browser':
                    path = out / 'browser'; path.mkdir()
                    rows = [{'status': 'expected', 'results': [{'status': 'passed'}]} for _ in range(11)]
                    (path / 'results.json').write_text(json.dumps({'stats': {'expected': 11, 'unexpected': 0, 'flaky': 0, 'skipped': 0},
                        'suites': [{'specs': [{'tests': rows}]}]}))
                return subprocess.CompletedProcess(command, 0, '', '')
            def fresh_auth(*_args, **_kwargs):
                self.assertEqual([row[0] for row in calls], ['python5', 'js5', 'js6', 'manifest'])
                path = runtime / 'browser-auth.json'
                browser.write_private(path, auth.read_text())
                return path
            with patch.object(smoke, 'run', side_effect=run), \
                    patch.object(browser, 'prepare_browser_auth', side_effect=fresh_auth), \
                    patch.object(browser, 'verify_collection', return_value={'provider_request_budget': 7}):
                result = browser.run_acceptance(repo=Path('/repo'), out=out, runtime=runtime, smoke=smoke,
                    targets={'langfuse': target}, auth_file=auth, source='nvidia', key='nv-private')
            self.assertTrue(result['verified'])
            self.assertEqual([row[0] for row in calls], ['python5', 'js5', 'js6', 'manifest', 'browser'])
            self.assertEqual(calls[0][2]['AIVISION_LIVE_PROVIDER_KEY'], 'nv-private')
            for name, command, env in calls[1:]:
                self.assertNotIn('nv-private', json.dumps(command))
                self.assertNotIn('nv-private', json.dumps(env))
            self.assertFalse((runtime / 'provider.env').exists())
            self.assertFalse((runtime / 'targets.json').exists())
            self.assertFalse((runtime / 'browser-auth.json').exists())
            self.assertEqual(calls[-1][1][-3:], ['--config', 'playwright.platform.config.ts', r'collection\.spec\.ts$'])
            self.assertEqual(calls[-1][2]['AIVISION_BROWSER_AUTH_FILE'], str(runtime / 'browser-auth.json'))


if __name__ == '__main__': unittest.main()
