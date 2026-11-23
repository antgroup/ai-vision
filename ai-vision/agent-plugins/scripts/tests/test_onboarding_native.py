"""Native onboarding must retain config rollback without installing owned Python."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from test_onboarding import config, installer


class NativeBundleTests(unittest.TestCase):
    def test_bundle_root_is_the_executed_binary_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / 'connect' / 'connect'
            executable.parent.mkdir()
            executable.write_bytes(b'\x7fELFfixture')
            with patch.object(sys, 'argv', [str(executable), '--agent', 'codex']):
                self.assertEqual(installer.native_bundle_root(), executable.parent.resolve())

    def test_persistent_bundle_survives_download_cleanup_and_detects_corruption(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = root / 'download/connect'
            bundle.mkdir(parents=True)
            (bundle / 'connect').write_bytes(b'\x7fELFfixture')
            (bundle / 'connect').chmod(0o755)
            (bundle / 'libpython.so').write_bytes(b'\x7fELFdependency')
            managed = root / 'config/aivision/codex'
            runtime, created = installer.persist_native_runtime(bundle, managed)
            self.assertTrue(created)
            repeated, created_again = installer.persist_native_runtime(bundle, managed)
            self.assertEqual(repeated, runtime)
            self.assertFalse(created_again)
            (bundle / 'connect').unlink()
            (bundle / 'libpython.so').unlink()
            bundle.rmdir()
            self.assertEqual(runtime.read_bytes(), b'\x7fELFfixture')
            self.assertTrue(os.access(runtime, os.X_OK))
            (runtime.parent / 'libpython.so').write_bytes(b'damaged')
            self.assertFalse(installer.native_runtime_valid(runtime, runtime.parent.name, managed))

    def test_native_registration_uses_persistent_binary_and_status_verifies_bundle(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            bundle = root / 'download/connect'
            bundle.mkdir(parents=True)
            (bundle / 'connect').write_bytes(b'\x7fELFfixture')
            (bundle / 'connect').chmod(0o755)
            managed = root / 'config/aivision/codex'
            runtime, unused = installer.persist_native_runtime(bundle, managed)
            host_root = root / 'config'
            hook = managed / 'cdxhooks'
            hook.write_bytes(b'\x7fELFhook')
            hook.chmod(0o755)
            plugin = {'key': 'codex', 'binary': 'cdxhooks', 'host': 'codex'}
            args = SimpleNamespace(config_dir=str(host_root))
            with patch.object(installer, 'NATIVE_BUILD', True), patch.object(installer, 'run', return_value='hooks enabled true'):
                with config.ConfigTransaction() as transaction:
                    installer.register_native(args, plugin, host_root, hook, managed, root, {}, transaction, config, runtime=runtime)
            doc = json.loads((host_root / 'hooks.json').read_text())
            command = doc['hooks']['Stop'][0]['hooks'][0]['command']
            expected = [str(runtime), '--hook-runtime', '--config', str(managed / 'ingest.json'), '--binary', str(hook)]
            if os.name == 'nt':
                self.assertEqual(command, subprocess.list2cmdline(expected))
            else:
                self.assertEqual(shlex.split(command), expected)
            receipt = {'runtime_format': 'native-tar-v1', 'runtime_path': str(runtime), 'runtime_sha256': runtime.parent.name}
            self.assertTrue(installer.native_registered('codex', host_root, hook, managed, args, receipt))
            runtime.write_bytes(b'corrupt')
            self.assertFalse(installer.native_registered('codex', host_root, hook, managed, args, receipt))

    def test_windows_native_registration_preserves_backslashes_and_spaced_paths(self):
        for profile in (r'runner\agent', r'CI Agent\profile'):
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve() / profile
                bundle = root / 'download/connect'
                bundle.mkdir(parents=True)
                (bundle / 'connect').write_bytes(b'\x7fELFfixture')
                (bundle / 'connect').chmod(0o755)
                managed = root / 'config/aivision/codex'
                runtime, _ = installer.persist_native_runtime(bundle, managed)
                host_root = root / 'config'
                hook = managed / 'cdxhooks'
                hook.write_bytes(b'\x7fELFhook')
                hook.chmod(0o755)
                args = SimpleNamespace(config_dir=str(host_root))
                windows_os = SimpleNamespace(name='nt', environ=os.environ, access=os.access, X_OK=os.X_OK)
                plugin = {'key': 'codex', 'binary': 'cdxhooks', 'host': 'codex'}
                receipt = {'runtime_format': 'native-tar-v1', 'runtime_path': str(runtime), 'runtime_sha256': runtime.parent.name}
                with patch.object(installer, 'os', windows_os), patch.object(installer, 'NATIVE_BUILD', True), patch.object(installer, 'run', return_value='hooks enabled true'):
                    with config.ConfigTransaction() as transaction:
                        installer.register_native(args, plugin, host_root, hook, managed, root, {}, transaction, config, runtime=runtime)
                    self.assertTrue(installer.native_registered('codex', host_root, hook, managed, args, receipt))
                    doc = json.loads((host_root / 'hooks.json').read_text())
                    command = doc['hooks']['Stop'][0]['hooks'][0]['command']
                    doc['hooks']['Stop'][0]['hooks'][0]['command'] = command + ' --unexpected'
                    (host_root / 'hooks.json').write_text(json.dumps(doc))
                    self.assertFalse(installer.native_registered('codex', host_root, hook, managed, args, receipt))


    def test_windows_source_registration_checks_exact_hook_arguments(self):
        with tempfile.TemporaryDirectory() as temporary:
            host_root = Path(temporary).resolve() / r"Agent home\profile"
            managed = host_root / 'aivision/codex'
            managed.mkdir(parents=True)
            hook = managed / 'cdxhooks.exe'
            hook.write_bytes(b'windows test artifact')
            hook.chmod(0o755)
            (managed / 'hook_runtime.py').touch()
            args = SimpleNamespace(config_dir=str(host_root))
            plugin = {'key': 'codex', 'binary': 'cdxhooks', 'host': 'codex'}
            windows_os = SimpleNamespace(name='nt', environ=os.environ, access=os.access, X_OK=os.X_OK)
            with patch.object(installer, 'os', windows_os), patch.object(installer, 'run', return_value='hooks enabled true'):
                with patch.object(installer.sys, 'executable', r'C:\Tools\Python 3.12\python.exe'):
                    with config.ConfigTransaction() as transaction:
                        installer.register_native(args, plugin, host_root, hook, managed, host_root, {}, transaction, config)
                self.assertTrue(installer.native_registered('codex', host_root, hook, managed, args, {}))
                hooks_path = host_root / 'hooks.json'
                doc = json.loads(hooks_path.read_text(encoding='utf-8'))
                doc['hooks']['Stop'][0]['hooks'][0]['command'] += ' --unexpected'
                hooks_path.write_text(json.dumps(doc), encoding='utf-8')
                self.assertFalse(installer.native_registered('codex', host_root, hook, managed, args, {}))


class NativeTransactionTests(unittest.TestCase):
    def test_source_cleanup_never_removes_a_symlink_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outside = root / 'user-code.py'
            outside.write_text('user code')
            legacy = root / 'hook_runtime.py'
            legacy.symlink_to(outside)
            with self.assertRaises(ValueError):
                with config.ConfigTransaction() as transaction:
                    transaction.remove(legacy)
            self.assertEqual(outside.read_text(), 'user code')
            self.assertTrue(legacy.is_symlink())

    def test_binary_install_and_source_removal_roll_back_together(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            native = root / 'connect'
            source = root / 'hook_runtime.py'
            source.write_text('old owned helper')
            with self.assertRaisesRegex(RuntimeError, 'confirmation failed'):
                with config.ConfigTransaction() as transaction:
                    transaction.write(native, 'native-entrypoint-config', private=True)
                    transaction.remove(source)
                    raise RuntimeError('confirmation failed')
            self.assertFalse(native.exists())
            self.assertEqual(source.read_text(), 'old owned helper')
            with config.ConfigTransaction() as transaction:
                transaction.write(native, 'native-entrypoint-config', private=True)
                transaction.remove(source)
            self.assertEqual(native.read_text(), 'native-entrypoint-config')
            # Windows chmod has no POSIX owner/group permission contract.
            if os.name != 'nt':
                self.assertEqual(native.stat().st_mode & 0o777, 0o600)
            self.assertFalse(source.exists())
            self.assertFalse(source.with_name(source.name + '.aivision-backup').exists())


if __name__ == '__main__':
    unittest.main()
