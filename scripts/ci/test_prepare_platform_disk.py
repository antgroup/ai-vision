"""Exercise the hosted-only SDK cleanup with real files and a fake sudo."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import yaml

import prepare_platform_disk as disk


class PreparePlatformDiskTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='aiv-sdk-disk-')
        self.addCleanup(self.temporary.cleanup)
        disk_usage = patch.object(disk.shutil, 'disk_usage',
                                  return_value=SimpleNamespace(free=64 * 1024**3 - 1))
        self.disk_usage = disk_usage.start()
        self.addCleanup(disk_usage.stop)
        self.root = Path(self.temporary.name).resolve()
        self.sdk = self.root / 'android' / 'sdk'
        self.sdk.mkdir(parents=True)
        (self.sdk / 'unused-ndk').write_bytes(b'x' * 16384)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.calls = self.root / 'sudo-calls.jsonl'
        sudo = self.bin / 'sudo'
        sudo.write_text('#!' + sys.executable + '\n' + '''import json, os, shutil, sys
from pathlib import Path
args = sys.argv[1:]
with open(os.environ['TEST_SUDO_CALLS'], 'a') as stream:
    stream.write(json.dumps(args) + '\\n')
expected = ['-n', 'rm', '--recursive', '--force', '--one-file-system', '--', os.environ['TEST_SDK_TARGET']]
if args != expected:
    sys.exit(91)
mode = os.environ.get('TEST_SUDO_MODE', 'remove')
if mode == 'fail':
    print('private-cookie-and-path', file=sys.stderr)
    sys.exit(37)
if mode != 'lie':
    shutil.rmtree(args[-1])
if mode == 'remove_then_fail':
    sys.exit(37)
''')
        sudo.chmod(0o700)
        self.env = {'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': 'github-hosted',
                    'RUNNER_OS': 'Linux', 'RUNNER_ARCH': 'X64',
                    'ANDROID_HOME': str(self.sdk), 'ANDROID_SDK_ROOT': str(self.sdk),
                    'TEST_SUDO_CALLS': str(self.calls), 'TEST_SDK_TARGET': str(self.sdk),
                    'PATH': str(self.bin) + os.pathsep + os.environ['PATH']}

    def prepare(self, **overrides):
        with patch.object(disk, 'SDK_ROOT', self.sdk), patch.dict(
                os.environ, {**self.env, **overrides}, clear=True):
            return disk.prepare()

    def test_eligible_cleanup_removes_only_the_real_fixture_sdk(self):
        outside = self.root / 'retained-file'
        outside.write_text('keep')
        (self.sdk / 'external-link').symlink_to(outside)
        result = self.prepare()
        self.assertEqual(result['status'], 'removed')
        self.assertFalse(self.sdk.exists())
        self.assertEqual(outside.read_text(), 'keep')
        self.assertGreaterEqual(result['sdk_bytes_removed'], 16384)
        self.assertIsInstance(result['free_bytes_before'], int)
        self.assertIsInstance(result['free_bytes_after'], int)
        self.assertGreaterEqual(result['reclaimed_bytes'], 0)
        self.assertEqual(len(self.calls.read_text().splitlines()), 1)
        self.assertNotIn(str(self.sdk), json.dumps(result))

    def test_runner_with_disk_reserve_does_not_traverse_or_remove_existing_sdk(self):
        reserve = 64 * 1024**3
        for free_bytes in (reserve, 94_754_873_344):
            with self.subTest(free_bytes=free_bytes):
                self.disk_usage.return_value = SimpleNamespace(free=free_bytes)
                with patch.object(disk.subprocess, 'run') as subprocess_run:
                    result = self.prepare()
                subprocess_run.assert_not_called()
                self.assertTrue((self.sdk / 'unused-ndk').is_file())
                self.assertEqual(result, {
                    'status': 'retained', 'reason': 'sufficient_disk_space',
                    'free_bytes_before': free_bytes, 'free_bytes_after': free_bytes,
                    'sdk_bytes_removed': 0, 'reclaimed_bytes': 0,
                })

    def test_non_hosted_or_wrong_platform_never_invokes_sudo(self):
        for key, value in [('GITHUB_ACTIONS', ''), ('GITHUB_ACTIONS', 'false'),
                           ('RUNNER_ENVIRONMENT', 'self-hosted'), ('RUNNER_ENVIRONMENT', ''),
                           ('RUNNER_OS', 'macOS'), ('RUNNER_OS', 'Windows'),
                           ('RUNNER_ARCH', 'ARM64'), ('RUNNER_ARCH', 'X86')]:
            with self.subTest(key=key, value=value):
                self.assertEqual(self.prepare(**{key: value}),
                                 {'status': 'skipped', 'reason': 'runner_not_eligible'})
                self.assertTrue((self.sdk / 'unused-ndk').is_file())
                self.assertFalse(self.calls.exists())

    def test_unset_sdk_environment_is_allowed(self):
        env = dict(self.env)
        del env['ANDROID_HOME'], env['ANDROID_SDK_ROOT']
        with patch.object(disk, 'SDK_ROOT', self.sdk), patch.dict(os.environ, env, clear=True):
            self.assertEqual(disk.prepare()['status'], 'removed')

    def test_unexpected_sdk_environment_fails_without_removal_or_payloads(self):
        for key in ('ANDROID_HOME', 'ANDROID_SDK_ROOT'):
            for value in ('', '/private/customer-sdk', str(self.sdk) + '/'):
                with self.subTest(key=key, value=value):
                    result = self.prepare(**{key: value})
                    self.assertEqual(result['status'], 'failed')
                    self.assertEqual(result['reason'], 'unexpected_sdk_environment')
                    self.assertNotIn(value or 'private-cookie', json.dumps(result))
                    self.assertTrue(self.sdk.is_dir())
                    self.assertFalse(self.calls.exists())

    def test_sdk_root_symlink_is_refused(self):
        real = self.sdk.with_name('actual-sdk')
        self.sdk.rename(real)
        self.sdk.symlink_to(real, target_is_directory=True)
        self.assertEqual(self.prepare()['reason'], 'unsafe_sdk_path')
        self.assertTrue((real / 'unused-ndk').is_file())
        self.assertFalse(self.calls.exists())

    def test_sdk_parent_symlink_is_refused(self):
        parent = self.sdk.parent
        real = parent.with_name('actual-android')
        parent.rename(real)
        parent.symlink_to(real, target_is_directory=True)
        self.assertEqual(self.prepare()['reason'], 'unsafe_sdk_path')
        self.assertTrue((real / 'sdk' / 'unused-ndk').is_file())
        self.assertFalse(self.calls.exists())

    def test_non_directory_sdk_is_refused(self):
        (self.sdk / 'unused-ndk').unlink()
        self.sdk.rmdir()
        self.sdk.write_text('keep')
        self.assertEqual(self.prepare()['reason'], 'unsafe_sdk_path')
        self.assertEqual(self.sdk.read_text(), 'keep')
        self.assertFalse(self.calls.exists())

    def test_absent_sdk_is_an_idempotent_noop(self):
        self.assertEqual(self.prepare()['status'], 'removed')
        self.calls.unlink()
        result = self.prepare()
        self.assertEqual(result['status'], 'absent')
        self.assertEqual(result['sdk_bytes_removed'], 0)
        self.assertFalse(self.calls.exists())

    def test_sudo_failure_is_not_accepted_and_stderr_is_not_exported(self):
        result = self.prepare(TEST_SUDO_MODE='fail')
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['reason'], 'sdk_removal_failed')
        self.assertTrue(self.sdk.is_dir())
        self.assertNotIn('private-cookie-and-path', json.dumps(result))

    def test_success_exit_without_actual_removal_fails_closed(self):
        self.assertEqual(self.prepare(TEST_SUDO_MODE='lie')['reason'], 'sdk_still_present')
        self.assertTrue(self.sdk.is_dir())

    def test_removal_followed_by_failed_command_still_fails(self):
        result = self.prepare(TEST_SUDO_MODE='remove_then_fail')
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['reason'], 'sdk_removal_failed')
        self.assertFalse(self.sdk.exists())
        self.assertIsNone(result['sdk_bytes_removed'])

    def test_cli_fails_on_removal_error_with_only_fixed_json(self):
        out = io.StringIO()
        with patch.object(disk, 'SDK_ROOT', self.sdk), patch.dict(
                os.environ, {**self.env, 'TEST_SUDO_MODE': 'fail'}, clear=True), contextlib.redirect_stdout(out):
            self.assertEqual(disk.main([]), 1)
        result = json.loads(out.getvalue())
        self.assertEqual(result['reason'], 'sdk_removal_failed')
        self.assertNotIn('private-cookie-and-path', out.getvalue())

    def test_failed_size_measurement_prevents_removal_and_hides_output(self):
        du = self.bin / 'du'
        du.write_text('#!/bin/sh\necho private-cookie-and-path\nexit 1\n')
        du.chmod(0o700)
        result = self.prepare()
        self.assertEqual(result['reason'], 'sdk_size_measurement_failed')
        self.assertTrue(self.sdk.is_dir())
        self.assertFalse(self.calls.exists())
        self.assertNotIn('private-cookie-and-path', json.dumps(result))

    def test_cli_refuses_alternative_paths_without_touching_the_fixture(self):
        out = io.StringIO()
        with patch.object(disk, 'SDK_ROOT', self.sdk), patch.dict(
                os.environ, self.env, clear=True), contextlib.redirect_stdout(out):
            self.assertEqual(disk.main(['--path', '/private/customer-sdk']), 1)
        self.assertEqual(json.loads(out.getvalue()),
                         {'status': 'failed', 'reason': 'unexpected_arguments'})
        self.assertTrue(self.sdk.is_dir())
        self.assertFalse(self.calls.exists())


class PlatformDiskWorkflowTests(unittest.TestCase):
    def test_only_selected_platform_and_candidate_jobs_prepare_sdk_capacity(self):
        root = Path(__file__).resolve().parents[2]
        expected = {'diagnostics-e2e.yml': {'owned-platform-quick'},
                    'framework-e2e.yml': {'docker-platform', 'browser-platform', 'browser-platform-nvidia'},
                    'minimax-code-oci-candidate.yml': {'qualify'},
                    'native-agent-e2e.yml': {'docker-platform'},
                    'native-candidate.yml': {'build'},
                    'release-images.yml': {'artifact-smoke'}}
        count = 0
        for filename, selected in expected.items():
            doc = yaml.safe_load((root / '.github/workflows' / filename).read_text())
            found = set()
            for name, job in doc['jobs'].items():
                steps = job.get('steps', [])
                matches = [step for step in steps if 'prepare_platform_disk.py' in step.get('run', '')]
                if not matches:
                    continue
                found.add(name)
                self.assertEqual(len(matches), 1)
                step = matches[0]
                self.assertEqual(step['if'], "runner.environment == 'github-hosted' && runner.os == 'Linux' && runner.arch == 'X64'")
                self.assertEqual(step['run'], 'python scripts/ci/prepare_platform_disk.py')
                python_index = next(i for i, s in enumerate(steps) if s.get('uses') == 'actions/setup-python@v5')
                self.assertEqual(steps.index(step), python_index + 1)
                self.assertGreater(python_index, next(i for i, s in enumerate(steps) if s.get('uses') == 'actions/checkout@v4'))
                count += 1
            self.assertEqual(found, selected)
        self.assertEqual(count, 8)
        for path in (root / '.github/workflows').glob('*.yml'):
            if path.name not in expected:
                self.assertNotIn('prepare_platform_disk.py', path.read_text())


if __name__ == '__main__':
    unittest.main()
