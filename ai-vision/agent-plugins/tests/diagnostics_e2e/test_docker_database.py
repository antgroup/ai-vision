"""Owned Docker database guard and failure cleanup controls; no Docker daemon."""
import json
import os
import signal
import sys
import tempfile
import unittest
from pathlib import Path
from importlib.util import module_from_spec, spec_from_file_location
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import patch

from docker_database import (LINUX_PROC_TEST, OwnedDatabase, _child,
    _django_zero_skips, _unit_test_selection, main, validate_image)

_state_spec = spec_from_file_location('diagnostics_owned_process_state',
    Path(__file__).resolve().parents[1] / 'native_agent_e2e/_process_test_helpers.py')
_state = module_from_spec(_state_spec)
_state_spec.loader.exec_module(_state)

OCEAN = 'oceanbase/oceanbase-ce@sha256:' + 'a' * 64
SEEK = 'oceanbase/seekdb@sha256:' + 'b' * 64


class Commands:
    def __init__(self, *, fail_run=False, bad_port=False, image_platform='linux/amd64',
                 manifest_platform=None, containerd=False):
        self.calls = []
        self.fail_run, self.bad_port = fail_run, bad_port
        self.image_platform = image_platform
        self.manifest_platform = manifest_platform
        self.containerd = containerd

    def __call__(self, *args, timeout=90, check=True):
        self.calls.append(args)
        if args[:3] == ('docker', 'container', 'inspect'):
            container = {'Image': 'sha256:' + 'c' * 64}
            if self.manifest_platform:
                os_name, arch = self.manifest_platform.split('/')
                container['ImageManifestDescriptor'] = {
                    'mediaType': 'application/vnd.oci.image.manifest.v1+json',
                    'digest': 'sha256:' + 'd' * 64,
                    'platform': {'os': os_name, 'architecture': arch},
                }
            return json.dumps([container])
        if args[:2] == ('docker', 'info'):
            return json.dumps([['driver-type', 'io.containerd.snapshotter.v1']]
                              if self.containerd else [['Backing Filesystem', 'extfs']])
        if args[:3] == ('docker', 'image', 'inspect'):
            assert args[-1] == 'sha256:' + 'c' * 64
            return self.image_platform
        if args[:4] == ('docker', 'inspect', '--format', '{{.Image}}'):
            return 'sha256:' + 'c' * 64
        if args[:2] == ('docker', 'run') and self.fail_run:
            raise TimeoutError('inert startup timeout')
        if args[:2] == ('docker', 'port'):
            return '0.0.0.0:2881' if self.bad_port else '127.0.0.1:49123'
        return ''


class Connector:
    class Error(Exception):
        pass


class OwnedTests(unittest.TestCase):
    def test_platform_reaches_both_pull_and_run_and_failure_keeps_cleanup(self):
        for backend, image in (('oceanbase', OCEAN), ('seekdb', SEEK)):
            for image_platform in ('linux/amd64', 'linux/arm64'):
                with self.subTest(backend=backend, platform=image_platform):
                    commands = Commands(bad_port=True, image_platform=image_platform)
                    runner = OwnedDatabase(backend, image, command=commands,
                        connector=Connector(), image_platform=image_platform)
                    with patch('docker_database.platform.system', return_value='Linux'), \
                            self.assertRaisesRegex(RuntimeError, 'loopback'), runner:
                        self.fail('invalid port must fail after platform verification')
                    for action in ('pull', 'run'):
                        command = next(c for c in commands.calls if c[:2] == ('docker', action))
                        self.assertEqual(command[command.index('--platform') + 1], image_platform)
                    self.assertEqual(runner.actual_image_platform, image_platform)
                    self.assertTrue(runner.cleanup['container_removed'])

    def test_container_manifest_wins_over_image_index_default(self):
        commands = Commands(bad_port=True, image_platform='linux/arm64',
            manifest_platform='linux/amd64', containerd=True)
        runner = OwnedDatabase('seekdb', SEEK, command=commands, connector=Connector())
        with patch('docker_database.platform.system', return_value='Linux'), \
                self.assertRaisesRegex(RuntimeError, 'loopback'), runner:
            self.fail('invalid port must fail after platform verification')
        self.assertEqual(runner.actual_image_platform, 'linux/amd64')
        self.assertFalse(any(c[:3] == ('docker', 'image', 'inspect') for c in commands.calls))
        self.assertTrue(runner.cleanup['container_removed'])

    def test_wrong_architecture_is_rejected_before_sql_and_container_is_removed(self):
        commands = Commands(image_platform='linux/arm64',
            manifest_platform='linux/amd64', containerd=True)
        runner = OwnedDatabase('seekdb', SEEK, command=commands,
            connector=Connector(), image_platform='linux/arm64')
        with patch('docker_database.platform.system', return_value='Linux'), \
                self.assertRaisesRegex(RuntimeError, 'platform'), runner:
            self.fail('wrong platform must not run')
        self.assertFalse(any(c[:2] == ('docker', 'port') for c in commands.calls))
        self.assertTrue(runner.cleanup['container_removed'])

    def test_containerd_without_manifest_cannot_use_index_default(self):
        commands = Commands(containerd=True, bad_port=True)
        runner = OwnedDatabase('seekdb', SEEK, command=commands, connector=Connector())
        with patch('docker_database.platform.system', return_value='Linux'), \
                self.assertRaisesRegex(RuntimeError, 'manifest'), runner:
            self.fail('an unverified index must not run')
        self.assertTrue(runner.cleanup['container_removed'])
        self.assertFalse(any(c[:2] == ('docker', 'port') for c in commands.calls))
        self.assertFalse(any(c[:3] == ('docker', 'image', 'inspect') for c in commands.calls))

    def test_unsupported_platform_rejected_without_docker(self):
        commands = Commands()
        with self.assertRaises(ValueError):
            OwnedDatabase('seekdb', SEEK, command=commands, image_platform='linux/arm/v7')
        self.assertEqual(commands.calls, [])

    def test_linux_keeps_real_proc_contract_and_mac_excludes_only_that_node(self):
        linux_args, linux_exclusions = _unit_test_selection('Linux')
        self.assertEqual(linux_exclusions, [])
        self.assertNotIn('--deselect', linux_args)
        mac_args, mac_exclusions = _unit_test_selection('Darwin')
        self.assertEqual(mac_exclusions, [LINUX_PROC_TEST])
        self.assertEqual(mac_args, [*linux_args, '--deselect', LINUX_PROC_TEST])

    def test_child_preserves_unexpected_skips_and_counts_platform_deselections(self):
        completed = SimpleNamespace(returncode=0,
            stdout='12 passed, 1 skipped, 1 deselected in 0.1s\n', stderr='')
        with patch('docker_database._run_owned', return_value=completed):
            result = _child(sys.executable, 'pytest', ['tests/unit'],
                cwd=Path.cwd(), env={}, timeout=1)
        self.assertEqual(result['skipped'], 1)
        self.assertEqual(result['deselected'], 1)

    def test_failed_child_keeps_source_locations_without_private_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'tests/unit/test_owned.py'
            source.parent.mkdir(parents=True)
            source.write_text('def test_owned():\n    assert False\n')
            private = 'owned-db-password'
            log = (
                f'E   AssertionError: mysql+pymysql://root:{private}@localhost/db\n'
                '--------------------------- Captured stdout call ---------------------------\n'
                'FAILED tests/unit/test_owned.py::private_trace_value\n'
                'FAILED tests/unit/private_trace_value/../test_owned.py::test_owned\n'
                'FAILED tests/unit/test_owned.py::test_owned::private_trace_value\n'
                'tests/unit/test_owned.py:2: privateTraceValueError\n'
                'tests/unit/test_owned.py:123456789: AssertionError\n'
                'tests/unit/test_owned.py:2: AssertionError\n'
                f'FAILED tests/unit/test_owned.py::test_owned[{private}] - AssertionError\n'
                'FAILED tests/unit/test_missing.py::test_untrusted - RuntimeError\n'
                '1 failed, 2 passed in 0.1s\n'
            )
            completed = SimpleNamespace(returncode=1, stdout=log, stderr='')
            with patch('docker_database._run_owned', return_value=completed):
                result = _child(sys.executable, 'pytest', ['tests/unit'],
                    cwd=root, env={}, timeout=1)
            self.assertEqual(result['failed'], 1)
            self.assertEqual(result['failure_evidence'], {
                'tests': ['tests/unit/test_owned.py::test_owned'],
                'locations': [{'path': 'tests/unit/test_owned.py', 'line': 2,
                    'exception': 'AssertionError'}],
            })
            self.assertNotIn(private, json.dumps(result))
            self.assertNotIn('mysql+pymysql', json.dumps(result))
            self.assertNotIn('private_trace_value', json.dumps(result))
            self.assertNotIn('privateTraceValueError', json.dumps(result))

    @unittest.skipUnless(os.name == 'posix', 'database runners support Linux/macOS')
    def test_child_timeout_stops_descendants_and_records_failure_without_raw_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / 'agentlens'
            package.mkdir()
            (package / '__init__.py').touch()
            (package / 'config.py').touch()
            receipt = root / 'tree.json'
            (root / 'owned_child.py').write_text(
                'import json,os,subprocess,sys,time\nfrom pathlib import Path\n'
                "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(10)'])\n"
                f'Path({str(receipt)!r}).write_text(json.dumps([os.getpid(),child.pid]))\n'
                'print("private-trace-value",flush=True)\ntime.sleep(10)\n')
            try:
                result = _child(sys.executable, 'owned_child', [], cwd=root, env={}, timeout=.5)
                self.assertEqual(result['exit_code'], 124)
                self.assertTrue(result['timed_out'])
                self.assertNotIn('private-trace-value', json.dumps(result))
                self.assertTrue(receipt.exists())
                for pid in json.loads(receipt.read_text()):
                    _state.assert_posix_process_stopped(pid, timeout=.5)
            finally:
                if receipt.exists():
                    for pid in json.loads(receipt.read_text()):
                        try:
                            os.kill(pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass

    def test_only_official_digest_pins(self):
        self.assertEqual(validate_image('oceanbase', OCEAN), OCEAN)
        self.assertEqual(validate_image('seekdb', SEEK), SEEK)
        for image in ('oceanbase/oceanbase-ce:latest',
                      'mysql/mysql-server@sha256:' + 'a' * 64, SEEK):
            with self.assertRaises(ValueError):
                validate_image('oceanbase', image)

    def test_boot_failure_attempts_exact_owned_cleanup(self):
        commands = Commands(fail_run=True)
        runner = OwnedDatabase('oceanbase', OCEAN, command=commands, connector=Connector())
        with patch('docker_database.platform.system', return_value='Linux'), \
                self.assertRaises(TimeoutError), runner:
            self.fail('should not start')
        rm = [call for call in commands.calls if call[:2] == ('docker', 'rm')]
        self.assertEqual(len(rm), 1)
        self.assertEqual(rm[0][-1], runner.name)
        self.assertTrue(runner.name.startswith('aiv_diag_unit_'))
        self.assertNotIn(runner.password, repr(commands.calls))

    def test_non_loopback_port_fails_and_cleans_owned_name(self):
        commands = Commands(bad_port=True)
        runner = OwnedDatabase('seekdb', SEEK, command=commands, connector=Connector())
        with patch('docker_database.platform.system', return_value='Linux'), \
                self.assertRaises(RuntimeError), runner:
            self.fail('should not start')
        self.assertTrue(any(call[:2] == ('docker', 'rm') and call[-1] == runner.name
            for call in commands.calls))

    def test_outside_checkout_required(self):
        from pathlib import Path

        from docker_database import _outside_checkout
        with self.assertRaises(ValueError):
            _outside_checkout('/tmp/public/worktree/result', '/tmp/public/worktree')
        self.assertEqual(_outside_checkout('/tmp/private/result', '/tmp/public/worktree'),
            Path('/tmp/private/result').resolve())

    def test_windows_database_service_is_unsupported_without_docker(self):
        commands = Commands()
        runner = OwnedDatabase('seekdb', SEEK, command=commands, connector=Connector())
        with patch('docker_database.platform.system', return_value='Windows'), \
                self.assertRaisesRegex(RuntimeError, 'unsupported'):
            runner.__enter__()
        self.assertEqual(commands.calls, [])

    def test_django_skip_count_requires_final_success_summary(self):
        self.assertEqual(_django_zero_skips('Ran 18 tests in 1.2s\n\nOK\n', 18), 0)
        for log in ('Ran 18 tests in 1.2s\n\nOK (skipped=1)\n',
                    '18 tests complete\n', 'Ran 17 tests in 1.2s\n\nOK\n'):
            with self.subTest(log=log), self.assertRaises(ValueError):
                _django_zero_skips(log, 18)

    def test_entry_failure_keeps_cleanup_receipt_in_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / 'repo'
            (repo / 'agentlens/obschema').mkdir(parents=True)
            (repo / 'agentlens/obschema/alembic.ini').write_text('[alembic]')
            out = root / 'result'
            class FailedEntry:
                cleanup: ClassVar[dict] = {'container_name': 'aiv_diag_unit_owned',
                    'container_removed': True}
                def __init__(self, *_, **kwargs):
                    self.image_platform = kwargs['image_platform']
                def __enter__(self): raise TimeoutError('inert boot failure')
                def __exit__(self, *_): pass
            with patch('docker_database.OwnedDatabase', FailedEntry):
                status = main(['--backend', 'seekdb', '--image', SEEK,
                    '--repo', str(repo), '--out', str(out), '--python', sys.executable])
            summary = json.loads((out / 'summary.json').read_text())
            self.assertEqual(status, 1)
            self.assertEqual(summary['cleanup'], FailedEntry.cleanup)
            self.assertEqual(summary['error']['type'], 'TimeoutError')
            self.assertEqual(summary['platform'], 'linux/amd64')


if __name__ == '__main__':
    unittest.main()
