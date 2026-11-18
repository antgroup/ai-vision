"""Judge transport budgets and failure evidence at the acceptance boundary."""

from pathlib import Path
from contextlib import nullcontext, redirect_stdout
from io import StringIO
from http.cookies import SimpleCookie
import json
import os
import subprocess
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import MagicMock, patch


class SummaryBoundaryTests(unittest.TestCase):
    def test_native_case_persists_primary_and_owned_cleanup_failures(self):
        import run_suite
        from docker_loopback import docker_loopback
        from docker_judge import create_judge, platform_judges

        for boundary in ("loopback", "judge"):
            for dual in (False, True):
                with (
                    self.subTest(boundary=boundary, dual=dual),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    root = Path(directory)
                    config = root / "ssh.conf"
                    config.touch()
                    cleanup = subprocess.CalledProcessError(
                        255, ["owned-cleanup", boundary]
                    )

                    def driver(context):
                        manager = (
                            docker_loopback("http://127.0.0.1:123/v1")
                            if boundary == "loopback"
                            else platform_judges("owned-agentlens")
                        )
                        with manager:
                            if boundary == "judge":
                                create_judge(lambda: None)
                            if dual:
                                raise PermissionError("primary-driver-denied")

                    provider = SimpleNamespace(
                        url="http://127.0.0.1:123/v1", requests=[]
                    )
                    capture = SimpleNamespace(url="http://127.0.0.1:456", exports=[])
                    with (
                        patch.dict(
                            os.environ, {"AIVISION_E2E_DOCKER_SSH_CONFIG": str(config)}
                        ),
                        patch(
                            "docker_loopback.subprocess.run",
                            side_effect=[
                                SimpleNamespace(stdout=""),
                                SimpleNamespace(stdout="321\n"),
                                cleanup,
                            ],
                        ),
                        patch(
                            "docker_judge._docker",
                            side_effect=['[{"Image":"base"}]', cleanup],
                        ),
                        patch(
                            "docker_judge._build_judge_image",
                            return_value="owned-judge",
                        ),
                        patch.object(
                            run_suite,
                            "FakeDiagnosticProvider",
                            return_value=nullcontext(provider),
                        ),
                        patch.object(
                            run_suite, "Capture", return_value=nullcontext(capture)
                        ),
                        patch.object(run_suite.dsh, "run_case", side_effect=driver),
                        patch.object(
                            run_suite,
                            "source_proof",
                            return_value={
                                "verified": False,
                                "failures": ["native_failure"],
                            },
                        ),
                        patch.object(run_suite.time, "sleep"),
                    ):
                        report = run_suite.native_case(
                            repo=root,
                            out=root / "case",
                            artifacts=root / "artifacts",
                            app="dsh02",
                            scenario=run_suite.scenarios()[0],
                            runtime=("inert-host",),
                        )
                    saved = json.loads((root / "case/source.json").read_text())
                    self.assertEqual(saved["result"], report["result"])
                    self.assertFalse(saved["source"]["verified"])
                    details = saved["result"]["details"]
                    if dual:
                        self.assertIn("primary-driver-denied", details["error"])
                        self.assertEqual(
                            [item["error_type"] for item in details["errors"]],
                            ["PermissionError", "CalledProcessError"],
                        )
                        self.assertEqual(
                            details["errors"][0]["error"], "primary-driver-denied"
                        )
                    else:
                        self.assertEqual(details["error_type"], "CalledProcessError")
                    self.assertIn("owned-cleanup", details["error"])
                    self.assertEqual(
                        (root / "case/source.json").stat().st_mode & 0o777, 0o600
                    )

    def test_capture_summary_persists_grouped_failure_members(self):
        import run_suite

        primary = PermissionError("primary-driver-denied")
        cleanup = subprocess.CalledProcessError(255, ["owned-cleanup"])
        failure = ExceptionGroup(
            "owned invocation and cleanup failed", [primary, cleanup]
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            out = root / "run"
            with (
                patch.object(run_suite, "native_case", side_effect=failure),
                redirect_stdout(StringIO()),
            ):
                self.assertEqual(
                    run_suite.main(
                        [
                            "--repo",
                            str(root),
                            "--out",
                            str(out),
                            "--capture-only",
                            "--apps",
                            "dsh02",
                            "--scenarios",
                            "read-only",
                        ]
                    ),
                    1,
                )
            summary = json.loads((out / "summary.json").read_text())
            self.assertFalse(summary["verified"])
            row = summary["cases"][0]
            self.assertEqual(row["error_type"], "ExceptionGroup")
            self.assertEqual(
                [member["error_type"] for member in row["errors"]],
                ["PermissionError", "CalledProcessError"],
            )
            self.assertEqual(row["errors"][0]["error"], "primary-driver-denied")
            self.assertIn("owned-cleanup", row["errors"][1]["error"])

    def test_shared_cached_rotation_reaches_earlier_and_unvisited_agents_without_cleanup_http(self):
        import httpx
        import run_suite
        for outcome in ('success', 'source-failure', 'interrupt'):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                now, renewals = [0.0], []
                def renew(request):
                    if request.url.path.endswith('/csrf'):
                        return httpx.Response(200, headers={'Set-Cookie': 'csrftoken=csrf; Path=/'})
                    renewals.append(request.headers['Cookie'])
                    version = str(len(renewals))
                    return httpx.Response(200, json={'code': 0}, headers=[
                        ('Set-Cookie', 'aivision_access=access-' + version + '; Path=/'),
                        ('Set-Cookie', 'aivision_refresh=refresh-' + version + '; Path=/api/v1/auth/refresh')])
                sessions = run_suite.native_sessions.DashboardSessions(clock=lambda: now[0],
                    client_factory=lambda: httpx.Client(transport=httpx.MockTransport(renew)))
                original_cookie = 'aivision_refresh=original; aivision_access=expired'
                targets = {}
                for app in ('codex', 'dsh02'):
                    targets[app] = {'endpoint': 'http://127.0.0.1:12345',
                        'collector_endpoint': 'http://127.0.0.1:23456',
                        'data_backend': 'oceanbase', 'agent_product': app,
                        'space_id': app + '-space', 'isolation_space_id': app + '-isolation',
                        'space_key': app + '-public-key', 'agent_uuid': app + '-agent',
                        'collector_headers': {'Authorization': 'public'},
                        'dashboard_headers': {'Cookie': original_cookie}}
                target_file = root / 'targets.json'
                target_file.write_text(json.dumps(targets))
                exported = root / 'current.json'
                def source(**kwargs):
                    if outcome == 'interrupt' and kwargs['app'] == 'codex':
                        raise KeyboardInterrupt()
                    if kwargs['app'] == 'codex' and outcome != 'interrupt':
                        now[0] = 901
                    if outcome == 'source-failure' and kwargs['app'] == 'dsh02':
                        raise RuntimeError('public fixture failure')
                    return {'source': {'verified': True, 'failures': []}}
                with patch.object(run_suite, 'validate_targets', side_effect=lambda value, _apps: value), \
                        patch.object(run_suite.native_sessions, 'DashboardSessions', return_value=sessions), \
                        patch.object(run_suite, 'native_case', side_effect=source), redirect_stdout(StringIO()):
                    argv = ['--repo', directory, '--out', str(root / 'run'), '--targets', str(target_file),
                        '--export-current-targets', str(exported), '--capture-only', '--apps', 'codex', 'dsh02',
                        '--scenarios', 'read-only', '--dsh-node', '/inert/node', '--dsh02', '/inert/dsh']
                    if outcome == 'interrupt':
                        with self.assertRaises(KeyboardInterrupt):
                            run_suite.main(argv)
                    else:
                        self.assertEqual(run_suite.main(argv), 0 if outcome == 'success' else 1)
                current = json.loads(exported.read_text())
                expected = 'refresh-1' if outcome == 'interrupt' else 'refresh-2'
                for target in current.values():
                    self.assertIn('aivision_refresh=' + expected, target['dashboard_headers']['Cookie'])
                self.assertEqual(len(renewals), 1 if outcome == 'interrupt' else 2)

    def test_interrupted_capture_exports_last_owned_rotation_before_reraising(self):
        import run_suite
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = {'endpoint': 'http://127.0.0.1:1000',
                'collector_endpoint': 'http://127.0.0.1:2000',
                'data_backend': 'oceanbase', 'agent_product': 'DeepSeekHarness',
                'space_id': 'owned-space', 'space_key': 'public-fixture',
                'agent_uuid': 'owned-agent', 'collector_headers': {'Authorization': 'public'},
                'dashboard_headers': {'Cookie': 'session-0'}}
            targets = root / 'targets.json'
            targets.write_text(json.dumps({'dsh02': target}))
            exported = root / 'current.json'
            class Sessions:
                def refresh(self, value):
                    return {**value, 'dashboard_headers': {'Cookie': 'session-1'}}
            with patch.object(run_suite.native_sessions, 'DashboardSessions', return_value=Sessions()), \
                    patch.object(run_suite, 'native_case', side_effect=KeyboardInterrupt), \
                    redirect_stdout(StringIO()), self.assertRaises(KeyboardInterrupt):
                run_suite.main(['--repo', str(root), '--out', str(root / 'run'),
                    '--targets', str(targets), '--export-current-targets', str(exported),
                    '--capture-only', '--apps', 'dsh02', '--scenarios', 'read-only'])
            self.assertEqual(json.loads(exported.read_text())['dsh02']['dashboard_headers']['Cookie'],
                'session-1')
            self.assertEqual(exported.stat().st_mode & 0o777, 0o600)

    def test_source_output_inside_checkout_rejected_before_capture(self):
        import run_suite
        repo = Path(__file__).resolve().parents[4]
        with tempfile.TemporaryDirectory(dir=repo) as directory, patch.object(run_suite, 'native_case', return_value={'source': {'verified': True, 'failures': []}}) as source:
            with self.assertRaises(SystemExit) as caught:
                run_suite.main(['--repo', str(repo), '--out', str(Path(directory)/'run'),
                    '--capture-only', '--apps', 'codex', '--scenarios', 'read-only'])
            self.assertEqual(caught.exception.code, 2)
            source.assert_not_called()
            self.assertFalse((Path(directory)/'run').exists())

    def test_current_target_export_rejects_another_checkout_before_capture(self):
        import run_suite
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, other = root/'repo', root/'other'
            repo.mkdir(); other.mkdir(); (other/'.git').write_text('gitdir: /public/inert')
            targets = root/'targets.json'
            targets.write_text('{}')
            with patch.object(run_suite, 'native_case', return_value={'source': {'verified': True, 'failures': []}}) as source, \
                    patch.object(run_suite, 'validate_targets', return_value={}), redirect_stdout(StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    run_suite.main(['--repo', str(repo), '--out', str(root/'run'),
                        '--targets', str(targets), '--export-current-targets', str(other/'current.json'),
                        '--capture-only', '--apps', 'codex', '--scenarios', 'read-only'])
                self.assertEqual(caught.exception.code, 2)
                source.assert_not_called()
                self.assertFalse((other/'current.json').exists())
                self.assertFalse((root/'run').exists())

    def test_live_rag_source_retains_one_credential_lease_without_judge_case_cap(self):
        import run_suite
        from calibrate import CredentialLease
        observed = []
        def source(**kwargs):
            provider = kwargs['live_factory']()
            self.assertIsInstance(provider.budget, CredentialLease)
            self.assertTrue(provider.budget.active)
            observed.append(provider.budget)
            return {'source': {'verified': True, 'failures': []}}
        class Provider:
            def __init__(self, key, budget, **_kwargs):
                self.budget = budget
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict('os.environ', {'NVIDIA_API_KEY':'public source fixture key'}, clear=True), \
                patch.object(run_suite, 'NvidiaProvider', Provider), \
                patch.object(run_suite, 'native_case', side_effect=source), redirect_stdout(StringIO()):
            code = run_suite.main(['--repo',directory,'--out',str(Path(directory)/'run'),
                '--mode','nvidia','--model','public-model','--capture-only','--apps','codex','dsh02',
                '--scenarios','rag-grounded','rag-insufficient-evidence'])
        self.assertEqual(code, 0)
        self.assertEqual(len(observed), 4)
        self.assertTrue(all(item is observed[0] for item in observed))
        self.assertFalse(observed[0].active)
        self.assertFalse(observed[0].path.exists())

    def test_capture_only_with_target_never_creates_formal_resources(self):
        import run_suite
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            targets = root / 'targets.json'
            targets.write_text(json.dumps({'dsh02': {'endpoint': 'http://127.0.0.1:1000'}}))
            with patch.object(run_suite, 'validate_targets', side_effect=lambda value, _: value), \
                    patch.object(run_suite.native_sessions, 'DashboardSessions') as sessions, \
                    patch.object(run_suite, 'native_case', return_value={'source': {'verified': True, 'failures': []}}), \
                    patch.object(run_suite, 'PlatformClient') as client, \
                    patch.object(run_suite, 'diagnose_case', return_value={'verified': True}) as diagnose, redirect_stdout(StringIO()):
                sessions.return_value.refresh.side_effect = lambda value: value
                code = run_suite.main(['--repo', directory, '--out', str(root / 'run'),
                    '--targets', str(targets), '--capture-only', '--apps', 'dsh02', '--scenarios', 'read-only'])
            self.assertEqual(code, 0)
            client.assert_not_called()
            diagnose.assert_not_called()
            row = json.loads((root / 'run/summary.json').read_text())['cases'][0]
            self.assertTrue(row['source_verified'])
            self.assertFalse(row['diagnosis_verified'])
            self.assertEqual(row['failures'], [])

    def test_core_runner_renews_session_during_one_slow_diagnosis(self):
        import httpx
        import run_suite
        from platform_client import PlatformClient
        now, renewals, calls = [0.0], [], []
        http_client = httpx.Client
        def renew(request):
            if request.url.path.endswith('/csrf'):
                return httpx.Response(200, headers={'Set-Cookie': 'csrftoken=csrf-' + str(len(renewals) + 1) + '; Path=/'})
            renewals.append(request.headers['Cookie'])
            number = str(len(renewals))
            return httpx.Response(200, json={'code': 0}, headers=[
                ('Set-Cookie', 'aivision_access=access-' + number + '; Path=/'),
                ('Set-Cookie', 'aivision_refresh=refresh-' + number + '; Path=/api/v1/auth/refresh')])
        sessions = run_suite.native_sessions.DashboardSessions(clock=lambda: now[0],
            client_factory=lambda: http_client(transport=httpx.MockTransport(renew)))
        def api(request):
            calls.append(request)
            valid = 'aivision_access=access-' + ('2' if now[0] > 900 else '1') in request.headers.get('Cookie', '')
            return httpx.Response(200 if valid else 401, json={'code': 0 if valid else 1, 'data': {}})
        def diagnose(client, *_args, **_kwargs):
            client.request('POST', '/before-slow-judge')
            now[0] = 901
            client.request('POST', '/after-slow-judge')
            return {'verified': True}
        def platform(endpoint, **kwargs):
            client = PlatformClient(endpoint, **kwargs)
            client.client.close()
            client.client = http_client(base_url=endpoint, transport=httpx.MockTransport(api))
            for name, item in SimpleCookie(client.target['dashboard_headers']['Cookie']).items():
                client.client.cookies.set(name, item.value)
            return client
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = {'endpoint': 'http://127.0.0.1:12345',
                'dashboard_headers': {'Cookie': 'aivision_refresh=original; aivision_access=expired'}}
            targets = root / 'targets.json'
            targets.write_text(json.dumps({'dsh02': target}))
            with patch.object(run_suite, 'validate_targets', side_effect=lambda value, _apps: value), \
                    patch.object(run_suite.native_sessions, 'DashboardSessions', return_value=sessions), \
                    patch.object(run_suite, 'native_case', return_value={'source': {'verified': True, 'failures': []}}), \
                    patch.object(run_suite, 'PlatformClient', side_effect=platform), \
                    patch.object(run_suite, 'diagnose_case', side_effect=diagnose), redirect_stdout(StringIO()):
                result = run_suite.main(['--repo', directory, '--out', str(root / 'run'),
                    '--targets', str(targets), '--apps', 'dsh02', '--dsh-node', '/inert/node',
                    '--dsh02', '/inert/dsh', '--scenarios', 'read-only'])
            self.assertEqual(result, 0)
        self.assertEqual(len(renewals), 2)
        self.assertEqual(len(calls), 2)
        self.assertIn('aivision_refresh=refresh-1', renewals[1])
        self.assertEqual(calls[-1].headers['X-CSRFToken'], 'csrf-2')

    def run_fake(self, directory, outcomes):
        import run_suite
        out = Path(directory) / "run"
        argv = ["--repo", directory, "--out", str(out), "--capture-only", "--apps", "dsh02",
            "--dsh-node", "/inert/node", "--dsh02", "/inert/dsh",
            "--scenarios", "read-only", "tool-error-recovery"]
        with patch.object(run_suite, "native_case", side_effect=outcomes), redirect_stdout(StringIO()):
            run_suite.main(argv)
        return out

    def test_interrupted_suite_cannot_leave_a_passing_partial_summary(self):
        report = {"source": {"verified": True, "failures": []}}
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(KeyboardInterrupt):
                self.run_fake(directory, [report, KeyboardInterrupt()])
            summary = json.loads((Path(directory) / "run/summary.json").read_text())
            self.assertEqual((summary["completed"], summary["expected"]), (1, 2))
            self.assertFalse(summary["verified"])

    def test_complete_suite_retains_explicit_capture_only_stage(self):
        report = {"source": {"verified": True, "failures": []}}
        with tempfile.TemporaryDirectory() as directory:
            out = self.run_fake(directory, [report, report])
            summary = json.loads((out / "summary.json").read_text())
            self.assertTrue(summary["verified"])
            self.assertEqual((summary["completed"], summary["expected"]), (2, 2))
            self.assertEqual(summary["stage"], "native_capture")
            self.assertTrue(all(row["diagnosis_verified"] is False for row in summary["cases"]))


class JudgeBoundaryTests(unittest.TestCase):
    def test_native_records_exclude_only_predeclared_skips(self):
        import run_suite
        from scenarios import scenarios
        case = next(case for case in scenarios() if case.name == 'read-only')
        report = {'app': 'dsh02', 'mode': 'fake', 'run_id': 'inert-run',
            'source': {'verified': True, 'source_trace_ids': ['a' * 32], 'source_span_ids': []}}
        client = MagicMock()
        client.target = {}
        client.builtin_rules.return_value = {key: {'uuid': key} for key in case.expected_rules}
        client.create_dataset.return_value = {'uuid': 'dataset'}
        client.create_job.return_value = {'uuid': 'job'}
        client.scoped.return_value = {}
        client.execute.return_value = {'uuid': 'task'}
        client.records.return_value = []
        client.dashboard_stats.return_value = {'verified': True, 'failures': []}
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(run_suite, 'validate_projection', return_value={'verified': True, 'failures': []}), \
                patch.object(run_suite, 'validate_diagnosis', return_value={'verified': True, 'failures': []}) as validate:
            result = run_suite.diagnose_case(client, report, case, Path(directory))
        self.assertEqual(client.records.call_args.kwargs['expected_count'], 4)
        self.assertEqual(validate.call_args.kwargs['expected_skips'], {
            'test_execution': 'not_applicable', 'rewrite_overlap': 'not_applicable',
            'high_risk_command': 'not_applicable', 'user_interruption': 'missing_required_evidence'})
        self.assertEqual(result['expected_skips'], validate.call_args.kwargs['expected_skips'])

    def test_core_and_repair_projection_queries_keep_the_same_aware_instant(self):
        from datetime import datetime, timezone
        import regressions
        import run_suite
        from scenarios import scenarios
        fixed = datetime(2026, 10, 9, 2, 0, tzinfo=timezone.utc)
        clock = MagicMock()
        clock.now.return_value = fixed
        report = {'app': 'dsh02', 'mode': 'fake', 'run_id': 'inert-run',
            'source': {'verified': True, 'source_trace_ids': ['a' * 32], 'source_span_ids': []}}
        scenario = scenarios()[0]
        for module in (run_suite, regressions):
            client = MagicMock()
            client.target = {}
            client.scoped.return_value = {}
            client.builtin_rules.return_value = {}
            client.create_dataset.return_value = {}
            client.create_job.return_value = {'uuid': 'job'}
            client.execute.return_value = {}
            client.records.return_value = []
            client.dashboard_stats.return_value = {'verified': True, 'failures': []}
            with self.subTest(module=module.__name__), tempfile.TemporaryDirectory() as directory, \
                    patch.object(module, 'datetime', clock), \
                    patch.object(module, 'validate_projection', return_value={'verified': True, 'failures': []}):
                if module is run_suite:
                    with patch.object(module, 'validate_diagnosis', return_value={'verified': True, 'failures': []}):
                        module.diagnose_case(client, report, scenario, Path(directory))
                else:
                    module._projection(client, report, scenario, {'uuid': 'job'})
                request = next(call for call in client.scoped.call_args_list if '/projection' in call.args[0])
                self.assertEqual(datetime.fromisoformat(request.kwargs['params']['center_time']), fixed)

    def test_slow_judge_has_room_for_probe_and_all_three_attempts(self):
        import run_suite
        provider = MagicMock()
        provider.__enter__.return_value = provider
        provider.url = "http://127.0.0.1:12345/v1"
        provider.requests = []
        factory = MagicMock(return_value=provider)
        client = MagicMock()
        client.execute.return_value = {}
        client.records.return_value = []
        client.dashboard_stats.return_value = {"verified": True}
        report = {"app": "codex", "source": {"source_trace_ids": ["a" * 32], "source_span_ids": []}}
        scenario = MagicMock(name="scenario")
        scenario.name = "code-change-tested"
        with patch.object(run_suite, "create_connection"), patch.object(run_suite, "test_connection"), \
                patch.object(run_suite, "create_diagnoser", return_value={"uuid": "dg"}), \
                patch.object(run_suite, "validate_diagnosis", return_value={"verified": True}), \
                patch.object(run_suite, "validate_judge_records", return_value={"verified": True}):
            run_suite.evaluate_judge(client, report, scenario, "slow", {}, live_factory=factory,
                judge_controls={"request_timeout_sec": 300}, projections={})
        self.assertGreaterEqual(factory.call_args.kwargs["timeout"], 210 + 3 * 300 + 3)
        self.assertGreaterEqual(client.execute.call_args.kwargs["timeout"], 3 * 300 + 3)

    def test_failed_connection_still_saves_original_wire_attempts(self):
        import run_suite
        provider = MagicMock()
        provider.__enter__.return_value = provider
        provider.url = "http://127.0.0.1:12345/v1"
        provider.requests = [{"attempt_number": 7, "request": {"model": "slow-model"}, "error": "ReadTimeout"}]
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(run_suite, "create_connection"), \
                patch.object(run_suite, "test_connection", side_effect=RuntimeError("unavailable")):
            out = Path(directory)
            with self.assertRaises(RuntimeError):
                run_suite.evaluate_judge(MagicMock(), {"app": "codex"}, MagicMock(), "failure", {},
                    live_factory=MagicMock(return_value=provider), projections={}, out=out)
            saved = json.loads((out / "provider-attempts.json").read_text())
            self.assertEqual(saved["requests"], provider.requests)
            self.assertEqual(saved["completed"], False)
            self.assertEqual(saved["error_type"], "RuntimeError")
            self.assertEqual((out / "provider-attempts.json").stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
