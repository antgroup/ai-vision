import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from test_onboarding import installer
from onboarding_test_helpers import executable_fixture
from verify_plugin_dist import verify_glibc, verify_native


class BinaryRuntimeTests(unittest.TestCase):
    def test_version_probe_runs_without_hook_input(self):
        with tempfile.TemporaryDirectory() as temp:
            binary = Path(temp) / 'cdxhooks'
            source = ('import sys\n'
                      'assert sys.argv[1:] == ["--version"]\n'
                      'assert sys.stdin.read() == ""\n'
                      'print("cdxhooks test")\n')
            with executable_fixture(binary, source):
                installer.check_binary_runtime(binary)

    def test_windows_hook_probe_accepts_the_executable_name_without_suffix(self):
        result = SimpleNamespace(returncode=0, stdout='cdxhooks test\n', stderr='')
        with patch.object(installer.subprocess, 'run', return_value=result):
            installer.check_binary_runtime(Path('cdxhooks.exe'))
            with self.assertRaisesRegex(installer.ConnectError, 'did not report its version'):
                installer.check_binary_runtime(Path('other.exe'))

    @unittest.skipIf(os.name == 'nt', 'POSIX shell fixture interpreter path')
    def test_version_probe_with_a_spaced_python_interpreter_path(self):
        with tempfile.TemporaryDirectory() as temp:
            interpreter = Path(temp) / 'python with spaces'
            interpreter.symlink_to(sys.executable)
            binary = Path(temp) / 'cdxhooks'
            with patch('onboarding_test_helpers.sys.executable', str(interpreter)):
                with executable_fixture(binary, 'print("cdxhooks test")\n'):
                    installer.check_binary_runtime(binary)

    def test_loader_failure_is_actionable_without_echoing_process_secrets(self):
        with tempfile.TemporaryDirectory() as temp:
            binary = Path(temp) / 'cdxhooks'
            source = 'import sys\nprint("GLIBC_2.34 not found; secret-example", file=sys.stderr)\nsys.exit(1)\n'
            with executable_fixture(binary, source), patch.object(installer.platform, 'libc_ver', return_value=('glibc', '2.32')):
                with self.assertRaises(installer.ConnectError) as error:
                    installer.check_binary_runtime(binary)
            self.assertIn('GLIBC_2.34', str(error.exception))
            self.assertIn('glibc 2.32', str(error.exception))
            self.assertNotIn('secret-example', str(error.exception))

    def test_invalid_executable_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            binary = Path(temp) / 'cdxhooks'
            binary.write_text('not an executable')
            binary.chmod(0o700)
            with self.assertRaisesRegex(installer.ConnectError, 'cannot run'):
                installer.check_binary_runtime(binary)

    def test_non_version_response_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            binary = Path(temp) / 'cdxhooks'
            with executable_fixture(binary, 'pass\n'):
                with self.assertRaisesRegex(installer.ConnectError, 'did not report its version'):
                    installer.check_binary_runtime(binary)

    def test_timeout_is_bounded_and_does_not_echo_output(self):
        with patch.object(installer.subprocess, 'run', side_effect=subprocess.TimeoutExpired('cdxhooks', 10, output='secret-example')) as run:
            with self.assertRaisesRegex(installer.ConnectError, 'runtime check timed out') as error:
                installer.check_binary_runtime(Path('/unused/cdxhooks'))
            self.assertEqual(run.call_args.kwargs['timeout'], 10)
            self.assertEqual(run.call_args.kwargs['stdin'], subprocess.DEVNULL)
            self.assertNotIn('secret-example', str(error.exception))


class GlibcReleaseTests(unittest.TestCase):
    def test_actual_symbol_versions_must_fit_baseline(self):
        # readelf emits these version-need records for linked ELF dependencies.
        for version, supported in [('2.2.5', True), ('2.17', True), ('2.28', True),
                                   ('2.32', False), ('2.34', False), ('ABI_DT_RELR', False)]:
            with self.subTest(version=version):
                result = SimpleNamespace(stdout=f'  Name: GLIBC_{version}  Flags: none  Version: 3\n')
                with patch('verify_plugin_dist.subprocess.run', return_value=result):
                    if supported:
                        verify_glibc(Path('/unused/hook'), '2.28')
                    else:
                        with self.assertRaisesRegex(ValueError, 'release baseline'):
                            verify_glibc(Path('/unused/hook'), '2.28')

    def test_failed_elf_inspection_cannot_pass(self):
        with patch('verify_plugin_dist.subprocess.run', side_effect=subprocess.CalledProcessError(1, 'readelf')):
            with self.assertRaises(subprocess.CalledProcessError):
                verify_glibc(Path('/unused/hook'), '2.28')

    def test_native_gate_checks_exit_status_and_version(self):
        with tempfile.TemporaryDirectory() as temp, patch('verify_plugin_dist.platform.system', return_value='Linux'), patch('verify_plugin_dist.platform.machine', return_value='x86_64'):
            binary = Path(temp) / 'cdxhooks-test-linux-gnu-x64'
            with executable_fixture(binary, 'print("cdxhooks test")\n'):
                verify_native(binary, 'cdxhooks', ('linux-gnu', 'x64'))
                with self.assertRaisesRegex(ValueError, 'wrong platform'):
                    verify_native(binary, 'cdxhooks', ('linux-gnu', 'arm64'))
            with executable_fixture(binary, 'import sys\nsys.exit(1)\n'):
                with self.assertRaises(subprocess.CalledProcessError):
                    verify_native(binary, 'cdxhooks', ('linux-gnu', 'x64'))
            with executable_fixture(binary, 'pass\n'):
                with self.assertRaisesRegex(ValueError, 'did not report'):
                    verify_native(binary, 'cdxhooks', ('linux-gnu', 'x64'))
