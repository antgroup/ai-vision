"""Kimi CLI 1.52 / Kimi Code 2.1.1 installation boundary contracts.

New host facts: MoonshotAI/kimi-code f67e6398fb3210ad8ace970e2dfd5bcc984ed61f,
apps/kimi-code/src/cli/commands.ts and docs/en/configuration/config-files.md.
"""
import os
import json
from pathlib import Path
import shlex
import tempfile
import tomllib
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_onboarding import installer, config, runtime


class KimiHostCompatibilityTests(unittest.TestCase):
    def test_status_checks_current_host_after_upgrade_in_the_same_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            managed = root / 'aivision/kimi'
            managed.mkdir(parents=True)
            binary = managed / 'kimihooks'
            binary.write_text('test')
            binary.chmod(0o700)
            (managed / 'hook_runtime.py').write_text('test')
            command = shlex.join(['python', str(managed / 'hook_runtime.py'), '--config',
                                  str(managed / 'ingest.json'), '--binary', str(binary)])
            (root / 'config.toml').write_text(config.kimi_hooks('', command))
            (managed / 'installation.json').write_text(json.dumps({'agent': 'kimi',
                'installed_path': str(binary), 'host_version': 'kimi, version 1.52.0'}))
            args = SimpleNamespace(agent='kimi', config_dir=str(root), base_url=None, space_id=None, agent_uuid=None)
            version = SimpleNamespace(returncode=0, stdout='kimi, version 1.52.0\n', stderr='')
            with patch.object(installer, 'check_binary_runtime'), \
                    patch.object(installer.shutil, 'which', return_value='/fake/kimi'), \
                    patch.object(installer.subprocess, 'run', return_value=version):
                self.assertEqual(installer.installation_status(args)['registration'], 'verified')
                version.stdout = '2.1.1\n'
                result = installer.installation_status(args)
            self.assertEqual(result['registration'], 'missing', 'current host needs Interrupt after upgrading')

    def test_status_can_use_an_explicit_home_without_an_installed_host(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'KIMI_CODE_HOME': temp}, clear=True), \
                patch.object(installer.shutil, 'which', return_value=None):
            args = SimpleNamespace(agent='kimi', config_dir=None, base_url=None, space_id=None, agent_uuid=None)
            result = installer.installation_status(args)
            self.assertEqual(result['config_dir'], str(Path(temp).resolve()))
            self.assertFalse(result['installed'])
            self.assertEqual(list(Path(temp).iterdir()), [])

    def test_preflight_uses_explicit_directory_without_migrating_legacy_config(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {}, clear=True):
            root = Path(temp).resolve()
            legacy = root / '.kimi'
            legacy.mkdir()
            (legacy / 'config.toml').write_text('model="legacy"\n')
            chosen = root / 'chosen'
            args = SimpleNamespace(agent='kimi', base_url='https://vision.test', config_dir=str(chosen), collection_mode=None)
            entry = {'key': 'kimi', 'plugin': {'key': 'kimi', 'host': 'kimi', 'name': 'Kimi', 'binary': 'kimihooks'},
                     'downloads': [{'platform': 'all', 'arch': 'all'}]}
            result = SimpleNamespace(returncode=0, stdout='2.1.1\n', stderr='')
            with patch.object(installer, 'request_data', return_value={'agents': [entry]}), \
                    patch.object(installer.Path, 'home', return_value=root), \
                    patch.object(installer.shutil, 'which', return_value='/fake/kimi'), \
                    patch.object(installer.subprocess, 'run', return_value=result):
                _, _, _, _, plugin, selected, _ = installer.preflight(args)
                self.assertEqual(plugin['host_version'], '2.1.1')
                self.assertEqual(selected, chosen)
            self.assertEqual((legacy / 'config.toml').read_text(), 'model="legacy"\n')
            self.assertFalse(chosen.exists())

    def test_host_generation_selects_its_own_default_and_environment_override(self):
        for version, directory, key in (
            ('kimi, version 1.52.0', '.kimi', 'KIMI_SHARE_DIR'),
            ('2.1.1', '.kimi-code', 'KIMI_CODE_HOME'),
        ):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as temp:
                home = Path(temp).resolve()
                result = SimpleNamespace(returncode=0, stdout=version + '\n', stderr='')
                with patch.dict(os.environ, {}, clear=True), patch.object(installer.Path, 'home', return_value=home), \
                        patch.object(installer.shutil, 'which', return_value='/fake/kimi'), \
                        patch.object(installer.subprocess, 'run', return_value=result):
                    self.assertEqual(installer.host_directory('kimi'), home / directory)
                    other_key = 'KIMI_SHARE_DIR' if key == 'KIMI_CODE_HOME' else 'KIMI_CODE_HOME'
                    os.environ[other_key] = str(home / 'other-generation')
                    self.assertEqual(installer.host_directory('kimi'), home / directory)
                    os.environ[key] = str(home / 'chosen')
                    self.assertEqual(installer.host_directory('kimi'), home / 'chosen')
                    os.environ[key] = ''
                    self.assertEqual(installer.host_directory('kimi'), home / directory)
                    self.assertEqual(list(home.iterdir()), [])

    def test_explicit_directory_is_used_by_both_native_host_generations(self):
        with patch.dict(os.environ, {'KIMI_CODE_HOME': '/stale/new', 'KIMI_SHARE_DIR': '/stale/old'}):
            env = installer.native_env('kimi', Path('/chosen/config'))
        self.assertEqual(env['KIMI_CODE_HOME'], '/chosen/config')
        self.assertEqual(env['KIMI_SHARE_DIR'], '/chosen/config')

    def test_new_terminal_registration_is_idempotent_and_rolls_back(self):
        for version, events in (
            ('kimi, version 1.52.0', ['Stop', 'StopFailure']),
            ('2.1.1', ['Stop', 'StopFailure', 'Interrupt']),
        ):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                path = root / 'config.toml'
                original = 'model="keep"\n[[hooks]]\nevent="Stop"\ncommand="other-hook"\n'
                path.write_text(original)
                plugin = {'key': 'kimi', 'binary': 'kimihooks', 'host_version': version}
                managed = root / 'aivision/kimi'
                def register(tx):
                    installer.register_native(SimpleNamespace(), plugin, root, managed / 'kimihooks',
                                              managed, root, {}, tx, config)
                with self.assertRaisesRegex(RuntimeError, 'confirm failed'):
                    with config.ConfigTransaction() as tx:
                        register(tx)
                        hooks = tomllib.loads(path.read_text())['hooks']
                        self.assertEqual([h['event'] for h in hooks[1:]], events)
                        raise RuntimeError('confirm failed')
                self.assertEqual(path.read_text(), original)
                for _ in range(2):
                    with config.ConfigTransaction() as tx:
                        register(tx)
                doc = tomllib.loads(path.read_text())
                self.assertEqual(doc['model'], 'keep')
                self.assertEqual(doc['hooks'][0]['command'], 'other-hook')
                self.assertEqual([h['event'] for h in doc['hooks'][1:]], events)

    def test_status_requires_interrupt_only_for_new_host_installations(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            managed = root / 'aivision/kimi'
            managed.mkdir(parents=True)
            binary = managed / 'kimihooks'
            binary.write_text('test')
            binary.chmod(0o700)
            (managed / 'hook_runtime.py').write_text('test')
            command = shlex.join(['python', str(managed / 'hook_runtime.py'), '--config',
                                  str(managed / 'ingest.json'), '--binary', str(binary)])
            path = root / 'config.toml'
            path.write_text(config.kimi_hooks('', command))
            def registered(version):
                return installer.native_registered('kimi', root, binary, managed, SimpleNamespace(),
                                                   {'host_version': version})
            self.assertTrue(registered('kimi, version 1.52.0'))
            self.assertFalse(registered('2.1.1'))
            with path.open('a') as stream:
                stream.write('\n[[hooks]]\nevent="Interrupt"\ncommand=' + json.dumps(command) + '\n')
            self.assertTrue(registered('2.1.1'))

    def test_runtime_keeps_session_and_host_roots_in_the_selected_directory(self):
        saved = {'agent': 'kimi', 'product': 'Kimi', 'endpoint': 'https://vision.test',
                 'config_dir': '/chosen/kimi-code', 'collection_mode': 'privacy'}
        with patch.dict(os.environ, {'KIMI_CODE_HOME': '/stale/new', 'KIMI_SHARE_DIR': '/stale/old',
                                   'KIMI_SESSIONS_ROOT': '/stale/sessions'}, clear=True):
            env = runtime.hook_env(saved, {'token': 'test-only'})
        self.assertEqual(env['KIMI_CODE_HOME'], '/chosen/kimi-code')
        self.assertEqual(env['KIMI_SHARE_DIR'], '/chosen/kimi-code')
        self.assertEqual(env['KIMI_SESSIONS_ROOT'], '/chosen/kimi-code/sessions')
        self.assertEqual(env['KIMI_CONFIG_DIR'], '/chosen/kimi-code/aivision/kimi/config')
        self.assertEqual(env['AIVISION_COLLECTION_MODE'], 'privacy')
