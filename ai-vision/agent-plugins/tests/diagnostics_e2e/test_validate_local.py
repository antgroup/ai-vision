"""Sequential callback must retain current targets until real screenshot evidence."""
import json
import os
import sys
import tempfile
import unittest
from contextlib import nullcontext
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import patch

from validate_local import (
    LocalError,
    SharedProviderBudget,
    _calibrate_owned,
    _mounted_hook,
    _publish_target,
    _quick_connection,
    _run_capture,
    after_acceptance,
    ci_owned_fake_after_acceptance,
    ci_owned_main,
)


class LifecycleTests(unittest.TestCase):
    def test_failed_capture_registers_exported_rotation_then_removes_handoff(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            out, runtime = root / 'owned', root / 'runtime'
            out.mkdir(mode=0o700); runtime.mkdir(mode=0o700)
            hook = root / 'public-hook'
            hook.write_bytes(b'public fixture hook')
            targets = {'codex': {'endpoint': 'http://127.0.0.1:8001',
                'agent_uuid': 'owned-agent', 'space_id': 'owned-space',
                'dashboard_headers': {'Cookie': 'original'}}}
            registered = []
            def fail_capture(argv, *, budget):
                self.assertIsNotNone(budget)
                current = json.loads(Path(argv[argv.index('--targets') + 1]).read_text())
                current['codex']['dashboard_headers']['Cookie'] = 'rotated-on-failure'
                exported = Path(argv[argv.index('--export-current-targets') + 1])
                exported.write_text(json.dumps(current))
                exported.chmod(0o600)
                return 1
            config = {'runtime': {'hook_binary': str(hook), 'dsh_node': 'node',
                'dsh02': 'dsh'}, 'codex_image': 'owned-image',
                'live': {'app': 'codex', 'model': 'pinned',
                    'scenarios': ['read-only']}}
            with patch.dict('os.environ', {'NVIDIA_API_KEY': 'public-fixture-key',
                    'GITHUB_ACTIONS': ''}, clear=False), \
                    patch('validate_local.provider_lease', return_value=nullcontext(object())), \
                    patch('run_suite.main', fail_capture), \
                    self.assertRaisesRegex(LocalError, 'source runner failed'):
                after_acceptance(config, repo=root, out=out, runtime=runtime,
                    targets=targets, result={'verified': True, 'expected_cases': 34},
                    register_private_targets=lambda value: registered.append(deepcopy(value)))
            self.assertEqual(targets['codex']['dashboard_headers']['Cookie'],
                'rotated-on-failure')
            self.assertEqual(registered[-1]['codex']['dashboard_headers']['Cookie'],
                'rotated-on-failure')
            self.assertFalse((out / 'live-targets.json').exists())
            self.assertFalse((runtime / 'validate-local-targets.json').exists())

    def test_windows_permission_representation_keeps_mount_contract(self):
        real_os, real_stat = os, Path.stat
        class WindowsOS:
            name = 'nt'
            def __getattr__(self, key): return getattr(real_os, key)
        windows_os = WindowsOS()
        def windows_stat(path, *args, **kwargs):
            info = real_stat(path, *args, **kwargs)
            if path.name == 'cdxhooks' and path.parent.name == 'runtime-inputs':
                values = list(info)
                values[0] = (values[0] & ~0o777) | 0o666
                return real_os.stat_result(values)
            return info
        with patch('validate_local.os', windows_os), \
                patch('test_validate_local.os', windows_os), \
                patch.object(Path, 'stat', windows_stat):
            self.test_capture_passes_owned_mount_visible_exact_hook_copy()

    def test_capture_passes_owned_mount_visible_exact_hook_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'external/cdxhooks'
            source.parent.mkdir()
            source.write_bytes(b'public vetted hook fixture\x00v1')
            out = root / 'owned'
            out.mkdir(mode=0o700)
            targets = out / 'targets.json'
            targets.write_text('{}')
            captured = []
            def run_source(argv, *, budget):
                self.assertIs(budget, lease)
                artifacts = Path(argv[argv.index('--artifacts') + 1])
                hook = Path(argv[argv.index('--hook-binary') + 1])
                self.assertNotEqual(hook, source)
                self.assertTrue(hook.is_relative_to(artifacts))
                self.assertEqual(hook.read_bytes(), source.read_bytes())
                if os.name != 'nt':
                    self.assertEqual(hook.stat().st_mode & 0o777, 0o700)
                captured.append(hook)
                stage = Path(argv[argv.index('--out') + 1])
                stage.mkdir()
                (stage / 'summary.json').write_text(json.dumps({'verified': True,
                    'completed': 1, 'expected': 1, 'stage': 'native_capture'}))
                return 0
            lease = object()
            config = {'live': {'app': 'codex', 'model': 'pinned-model',
                'scenarios': ['read-only']}, 'codex_image': 'owned-image'}
            runtime = {'hook_binary': str(source), 'dsh_node': 'node',
                'dsh02': 'dsh'}
            with patch('run_suite.main', run_source):
                summary, _ = _run_capture(config, 'live', repo=root, out=out,
                    targets_file=targets, lease=lease, runtime=runtime)
            self.assertEqual(summary['completed'], 1)
            self.assertEqual(captured, [out / 'runtime-inputs/cdxhooks'])
            self.assertEqual(_mounted_hook(source, out), captured[0])
            source.write_bytes(b'changed public fixture')
            with self.assertRaisesRegex(LocalError, 'differs'):
                _mounted_hook(source, out)
            if os.name != 'nt':
                link = source.parent / 'link'
                link.symlink_to(source)
                with self.assertRaisesRegex(LocalError, 'symlink'):
                    _mounted_hook(link, out)

    def test_replay_uses_module_refresh_and_reaches_actual_adapter(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            targets = {'codex': {'endpoint': 'http://127.0.0.1:8001',
                'agent_uuid': 'owned-agent', 'space_id': 'owned-space',
                'agent_product': 'Codex',
                'collector_headers': {'x-space-key': 'public-fixture'}}}
            calls = []
            class Client:
                def __init__(self, _endpoint, *, target, **_kwargs):
                    self.target = deepcopy(target)
                def __enter__(self): return self
                def __exit__(self, *_): pass
                def request(self, method, path, *, allowed):
                    if (method, path, allowed) != ('GET', '/api/v1/auth/me', (200,)):
                        raise AssertionError('Unexpected replay management request')
                    return {'user': {'user_uuid': 'owned-account'}}
            def replay_package(*args, **kwargs):
                self.assertEqual(kwargs['headers_factory'](),
                    {'x-space-key': 'public-fixture'})
                calls.append((args, kwargs))
                return {'verified': True}
            with patch('validate_local.PlatformClient', Client), \
                    patch('replay.replay_package', replay_package):
                result = after_acceptance({'replay': {'app': 'codex',
                    'corpus': str(root / 'inert-corpus')}}, repo=root, out=root,
                    runtime=root, targets=targets,
                    result={'verified': True, 'expected_cases': 34},
                    register_private_targets=lambda _value: None)
            self.assertTrue(result['verified'])
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0][0], root / 'inert-corpus')

    def test_replay_scope_uses_current_me_and_preserves_cleanup_on_rejection(self):
        values = ['native-account', 'x' * 36, None, True, 1, '', ' account',
                  'account ', 'x' * 37]
        for user in values:
            with self.subTest(user=user):
                self._check_replay_scope_entry(user)

    def _check_replay_scope_entry(self, user):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            targets = {'codex': {'endpoint': 'http://127.0.0.1:8001',
                'agent_uuid': 'owned-agent', 'space_id': 'owned-space',
                'agent_product': 'Codex', 'authenticated_user_id': 'stale-user',
                'collector_headers': {'x-space-key': 'public-fixture'},
                'dashboard_headers': {'Cookie': 'original'}}}
            events, calls, clients, registered = [], [], [], []
            test = self
            class Client:
                def __init__(self, _endpoint, *, target, **_kwargs):
                    self.target = deepcopy(target)
                    self.closed = False
                    clients.append(self)
                def __enter__(self): return self
                def __exit__(self, *_): self.closed = True
                def request(self, method, path, *, allowed):
                    test.assertEqual((method, path, allowed),
                        ('GET', '/api/v1/auth/me', (200,)))
                    events.append('me')
                    self.target['dashboard_headers']['Cookie'] = 'rotated'
                    return {'user': {'user_uuid': user}}
            def invoke(*args, **kwargs):
                events.append('replay')
                self.assertIs(kwargs['platform'], clients[0])
                calls.append(kwargs)
                return {'verified': True}
            with patch.dict(sys.modules, {'run_suite': SimpleNamespace(native_sessions=SimpleNamespace(
                    DashboardSessions=object, refresh_ingest=lambda target: target))}), \
                    patch('validate_local.PlatformClient', Client), \
                    patch('replay.replay_package', invoke):
                def run():
                    return after_acceptance({'replay': {'app': 'codex',
                        'corpus': str(root / 'inert-corpus')}}, repo=root, out=root,
                        runtime=root, targets=targets,
                        result={'verified': True, 'expected_cases': 34},
                        register_private_targets=lambda value: registered.append(deepcopy(value)))
                if type(user) is str and user and user.strip() == user and len(user) <= 36:
                    self.assertTrue(run()['verified'])
                    self.assertEqual(events, ['me', 'replay'])
                    self.assertEqual(calls[0]['authenticated_scope'], {
                        'space_id': 'owned-space', 'agent_uuid': 'owned-agent',
                        'agent_product': 'Codex', 'authenticated_user_id': user})
                else:
                    with self.assertRaises(ValueError): run()
                    self.assertEqual((events, calls), (['me'], []))
            self.assertTrue(clients[0].closed)
            self.assertFalse((root / 'validate-local-targets.json').exists())
            self.assertEqual(registered[-1]['codex']['dashboard_headers']['Cookie'], 'rotated')

    def test_failed_calibration_persists_actual_report_before_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            targets = {'codex': {'endpoint': 'http://127.0.0.1:8001',
                'agent_uuid': 'owned-agent', 'space_id': 'owned-space'}}
            class Client:
                base = '/api/v1/spaces/owned-space/diagnose'
                def __init__(self, _endpoint, *, target, **_kwargs):
                    self.target = deepcopy(target)
                def __enter__(self): return self
                def __exit__(self, *_): pass
            class AttemptBudget:
                def __init__(self, owner): self.owner = owner
            class Provider:
                url = 'http://127.0.0.1:49123/v1'
            report = {'passed': False, 'summary': {'usage_unknown': 1,
                'failed': 1}, 'rows': [{'case_id': 'public-fixture',
                    'prediction': 'failed', 'usage': None}]}
            frozen = {'adapter': {'kind': 'core_trial_v1', 'app': 'codex',
                'provider_host': '127.0.0.1', 'stability_cases': []},
                'templates': {'task_completion': {}}, 'model': 'pinned-model'}
            package = {'labels': {'labels': [{'metric_key': 'task_completion'}]}}
            with patch('validate_local.PlatformClient', Client), \
                    patch('calibrate.read_frozen_config', return_value=frozen), \
                    patch('calibrate.AttemptBudget', AttemptBudget), \
                    patch('calibrate.run_calibration', return_value=report), \
                    patch('corpus.load_package', return_value=package), \
                    patch('core_trial.core_binding'), \
                    patch('core_trial.CoreTrialExecutor'), \
                    patch('judge.create_connection', return_value={'uuid': 'owned-model'}), \
                    self.assertRaisesRegex(LocalError, 'calibration failed'):
                _calibrate_owned({'corpus': str(root / 'corpus'),
                    'frozen_config': str(root / 'frozen')}, targets=targets,
                    register=lambda _value: None, target_path=root / 'targets.json',
                    out=root, lease=object(), provider=Provider(),
                    proxy_budget=SharedProviderBudget(object()))
            saved = json.loads((root / 'calibration/report.json').read_text())
            self.assertEqual(saved, report)
            self.assertEqual(saved['summary']['usage_unknown'], 1)

    def test_ci_entry_is_fixed_owned_fake_and_never_uses_real_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            argv = ['--ci-owned-fake', '--repo', str(root), '--out', str(root / 'new'),
                '--codex-image', 'owned-image', '--hook-binary', str(root / 'hook'),
                '--dsh-node', str(root / 'node'), '--dsh02', str(root / 'dsh')]
            calls = []
            def platform_main(args, **kwargs):
                calls.append((args, kwargs))
                return 0
            with patch.dict('os.environ', {'GITHUB_ACTIONS': 'true',
                    'NVIDIA_API_KEY': '', 'NVIDIA_PROXY': ''}, clear=False):
                self.assertEqual(ci_owned_main(argv, platform_main=platform_main), 0)
                self.assertTrue(calls[0][1]['ci_owned'])
                self.assertIn('--extended', calls[0][0])
                self.assertNotIn('after_acceptance', calls[0][1])
                with patch.dict('os.environ', {'NVIDIA_API_KEY': 'public-fixture-key'}), \
                        self.assertRaises(SystemExit):
                    ci_owned_main(argv, platform_main=platform_main)
            self.assertEqual(len(calls), 1)

    def test_owned_connection_uses_current_proxy_without_probe(self):
        calls = []
        class Provider:
            url = 'http://127.0.0.1:49123/v1'
        def create(_client, **kwargs):
            calls.append(kwargs)
            return {'uuid': 'owned-model'}
        with patch('judge.create_connection', create), \
                patch('judge.test_connection', side_effect=AssertionError('Quick must not probe')):
            result = _quick_connection(object(), {'builtin_keys': ['task_completion'],
                'provider_model': 'pinned-model', 'provider_host': 'host.docker.internal'}, Provider())
            self.assertEqual(result, 'owned-model')
            self.assertEqual(calls[0]['endpoint'], 'http://host.docker.internal:49123/v1')
            with self.assertRaises(LocalError):
                _quick_connection(object(), {'builtin_keys': ['task_completion'],
                    'model_connection_uuid': 'foreign'}, Provider())

    def test_shared_proxy_and_rotated_targets_cross_all_stages(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            targets = {'codex': {'endpoint': 'http://127.0.0.1:8001',
                'agent_uuid': 'owned-agent', 'space_id': 'owned-space', 'cookie': 'initial'}}
            seen = []
            registered = []
            class Lease:
                attempts = 0
                def reserve(self, _stop=None): return 1
            lease = Lease()
            class Provider:
                instances: ClassVar[list] = []
                def __init__(self, _key, budget, **_kwargs):
                    self.budget, self.active, self.requests = budget, False, []
                    self.url = 'http://127.0.0.1:49123/v1'
                    self.instances.append(self)
                def __enter__(self):
                    self.active = True
                    return self
                def __exit__(self, *_): self.active = False
            class Client:
                def __init__(self, _endpoint, *, target, **_kwargs):
                    self.target = deepcopy(target)
                    seen.append(('open', self.target['cookie']))
                def __enter__(self): return self
                def __exit__(self, *_): pass
            class Runner:
                def __init__(self, client): self.client = client
                def run(self, _ids, _keys, **kwargs):
                    self_test.assertEqual(kwargs['model'], 'owned-model')
                    self_test.assertTrue(Provider.instances[0].active)
                    self.client.target['cookie'] = 'quick'
                    return {'receipt': {'task_uuid': 'original'}}
                def retest(self, _receipt, _ids, **_kwargs):
                    self_test.assertTrue(Provider.instances[0].active)
                    self.client.target['cookie'] = 'retest'
                    return {'receipt': {'task_uuid': 'new'}}
            self_test = self
            def capture(_config, name, *, out, targets_file, **_kwargs):
                current = json.loads(targets_file.read_text())
                seen.append((name, current['codex']['cookie']))
                current['codex']['cookie'] = name
                path = out / (name + '-targets.json')
                path.write_text(json.dumps(current))
                return {'verified': True, 'completed': 1}, path
            def calibration(_stage, *, targets, register, target_path, lease,
                    provider, proxy_budget, **_kwargs):
                self.assertTrue(provider.active)
                self.assertIs(proxy_budget.lease, lease)
                self.assertIs(provider.budget, proxy_budget)
                client = Client(targets['codex']['endpoint'], target=targets['codex'])
                client.target['cookie'] = 'calibration'
                _publish_target(targets, 'codex', client, register, target_path)
                return {'verified': True}
            def register(value): registered.append(deepcopy(value))
            config = {'runtime': {'hook_binary': 'fixture', 'dsh_node': 'fixture',
                'dsh02': 'fixture'}, 'codex_image': 'fixture',
                'live': {'app': 'codex'}, 'quick': {'app': 'codex', 'case': 'live-case',
                    'builtin_keys': ['task_completion'], 'scene_key': 'quick_runtime',
                    'provider_model': 'pinned-model', 'provider_host': '127.0.0.1'},
                'calibration': {}, 'after_live': {'app': 'codex'},
                'retest': {'app': 'codex', 'case': 'after-case',
                    'independent_verification': {'verified': True}}}
            with patch.dict('os.environ', {'NVIDIA_API_KEY': 'public-fixture-key',
                    'GITHUB_ACTIONS': ''}, clear=False), \
                    patch('validate_local.provider_lease', return_value=nullcontext(lease)), \
                    patch('calibrate.NvidiaProvider', Provider), \
                    patch('validate_local.PlatformClient', Client), \
                    patch('validate_local.QuickRunner', Runner), \
                    patch('validate_local._run_capture', capture), \
                    patch('validate_local._capture_ids', return_value=['a' * 32]), \
                    patch('validate_local._quick_connection', return_value='owned-model'), \
                    patch('validate_local._calibrate_owned', calibration):
                result = after_acceptance(config, repo=root, out=root, runtime=root,
                    targets=targets, result={'verified': True, 'expected_cases': 34},
                    register_private_targets=register)
            self.assertTrue(result['verified'])
            self.assertEqual([row for row in seen if row[0] in ('live', 'after_live')],
                [('live', 'initial'), ('after_live', 'calibration')])
            self.assertIn(('open', 'live'), seen)
            self.assertIn(('open', 'after_live'), seen)
            self.assertEqual([row['codex']['cookie'] for row in registered],
                ['live', 'quick', 'calibration', 'after_live', 'retest', 'retest'])
            self.assertEqual(len(Provider.instances), 1)
            self.assertFalse(Provider.instances[0].active)
            self.assertFalse((root / 'validate-local-targets.json').exists())

    def test_quick_failure_publishes_rotated_target_before_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            targets = {'codex': {'endpoint': 'http://127.0.0.1:8001',
                'agent_uuid': 'owned-agent', 'space_id': 'owned-space',
                'cookie': 'initial'}}
            registered = []
            class Client:
                def __init__(self, _endpoint, *, target, **_kwargs):
                    self.target = deepcopy(target)
                def __enter__(self): return self
                def __exit__(self, *_): pass
            class Runner:
                def __init__(self, client): self.client = client
                def run(self, *_args, **_kwargs):
                    self.client.target['cookie'] = 'rotated-on-failure'
                    raise RuntimeError('inert Quick failure')
            def capture(_config, name, *, out, targets_file, **_kwargs):
                current = json.loads(targets_file.read_text())
                path = out / (name + '-targets.json')
                path.write_text(json.dumps(current))
                return {'verified': True}, path
            config = {'runtime': {}, 'codex_image': 'fixture',
                'live': {'app': 'codex'},
                'quick': {'app': 'codex', 'case': 'live-case',
                    'builtin_keys': ['tool_success'], 'scene_key': 'quick_runtime'}}
            with patch.dict('os.environ', {'NVIDIA_API_KEY': 'public-fixture-key',
                    'GITHUB_ACTIONS': ''}, clear=False), \
                    patch('validate_local.provider_lease', return_value=nullcontext(object())), \
                    patch('validate_local.PlatformClient', Client), \
                    patch('validate_local.QuickRunner', Runner), \
                    patch('validate_local._run_capture', capture), \
                    patch('validate_local._capture_ids', return_value=['a' * 32]), \
                    patch('validate_local._quick_connection', return_value=None), \
                    self.assertRaisesRegex(RuntimeError, 'inert Quick failure'):
                after_acceptance(config, repo=root, out=root, runtime=root,
                    targets=targets, result={'verified': True, 'expected_cases': 34},
                    register_private_targets=lambda value: registered.append(deepcopy(value)))
            self.assertEqual(targets['codex']['cookie'], 'rotated-on-failure')
            self.assertEqual(registered[-1]['codex']['cookie'], 'rotated-on-failure')
            self.assertFalse((root / 'validate-local-targets.json').exists())

    def test_ci_owned_fake_uses_current_source_and_quick_api(self):
        from test_ci_quick_acceptance import CIQuickClient
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = root / 'cases/codex-read-only'
            case.mkdir(parents=True)
            (case / 'source.json').write_text(json.dumps({'source': {'verified': True},
                'exports': [{'spans': [{'trace_id': 'a' * 32}]}]}))
            targets = {'codex': {'endpoint': 'http://127.0.0.1:8001',
                'agent_uuid': 'owned-agent', 'space_id': 'owned-space'},
                'dsh02': {'endpoint': 'http://127.0.0.1:8001',
                    'agent_uuid': 'other-owned-agent', 'space_id': 'owned-space'}}
            captured = []
            class Client(CIQuickClient):
                def __init__(self, endpoint, **kwargs):
                    super().__init__(endpoint, **kwargs)
                    captured.append((endpoint, self))
            with patch('validate_local.PlatformClient', Client):
                result = ci_owned_fake_after_acceptance(out=root, runtime=root,
                    targets=targets, register_private_targets=lambda _: None,
                    result={'verified': True, 'expected_cases': 34,
                        'executed_cases': 34})
            self.assertTrue(result['verified'])
            self.assertEqual(captured[0][0], 'http://127.0.0.1:8001')
            body = next(body for method, path, body in captured[0][1].calls
                if method == 'POST' and path == '/quick-runs')
            self.assertEqual(body['trace_ids'], ['a' * 32])
            self.assertEqual(body['builtin_keys'], ['tool_success'])
            self.assertEqual(body['scene_key'], 'quick_runtime')
            self.assertTrue(result['quick']['rule_acceptance']['verified'])
            self.assertEqual(result['provider'], 'fake_only')
            with self.assertRaisesRegex(LocalError, '34-case'):
                ci_owned_fake_after_acceptance(out=root, runtime=root,
                    targets=targets, register_private_targets=lambda _: None,
                    result={'verified': True, 'expected_cases': 20,
                        'executed_cases': 20})

    def test_screenshot_callback_sees_owned_targets_before_finally(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / 'actual-captured-image.png'
            artifact.write_bytes(b'inert fixture artifact')
            targets = {'codex': {'space_id': 'owned'}}
            observations = []

            def screenshot_callback(*, targets, out, completed):
                observations.append((targets['codex']['space_id'], completed['fake']['verified']))
                self.assertTrue((root / 'validate-local-targets.json').is_file())
                return {'verified': True, 'artifacts': [str(artifact)]}

            report = after_acceptance({'screenshots_required': True},
                screenshot_callback=screenshot_callback, repo=root, out=root,
                runtime=root, targets=targets, result={'verified': True,
                    'expected_cases': 34}, register_private_targets=lambda _: None)
            self.assertTrue(report['verified'])
            self.assertEqual(observations, [('owned', True)])
            self.assertFalse((root / 'validate-local-targets.json').exists())
            self.assertEqual(report['manual']['novice_trials'], 'pending')

    def test_missing_actual_screenshot_fails_without_claim(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(LocalError):
                after_acceptance({'screenshots_required': True}, repo=root, out=root,
                    runtime=root, targets={}, result={'verified': True,
                        'expected_cases': 34}, register_private_targets=lambda _: None)
            self.assertFalse((root / 'validate-local-targets.json').exists())


if __name__ == '__main__':
    unittest.main()
