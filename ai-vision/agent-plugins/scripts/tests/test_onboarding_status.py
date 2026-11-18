import hashlib
import json
import os
from pathlib import Path
import shlex
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from test_onboarding import installer, config
from onboarding_test_helpers import executable_fixture
from verify_plugin_dist import verify


class StatusTests(unittest.TestCase):
    def test_status_checks_scoped_credentials_and_registration_without_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            # Match the installer's canonical paths, including macOS temp-dir symlinks.
            root = Path(temp).resolve()
            managed = root / 'aivision/codex'
            managed.mkdir(parents=True)
            binary = managed / 'cdxhooks'
            self.enterContext(executable_fixture(binary, 'print("cdxhooks test")\n'))
            (managed / 'hook_runtime.py').write_text('runtime')
            saved = {'base_url': 'https://vision.test', 'space_id': 'space', 'agent_uuid': 'uuid', 'agent': 'codex',
                     'product': 'Codex', 'agent_credential': 'aiv-agent-secret'}
            (managed / 'ingest.json').write_text(json.dumps(saved))
            (managed / 'installation.json').write_text(json.dumps({'agent': 'codex', 'installed_path': str(binary), 'version': 'test',
                                                                 'sha256': hashlib.sha256(binary.read_bytes()).hexdigest()}))
            (root / 'config.toml').write_text('[features]\nhooks = true\n')
            command = shlex.join(['python', str(managed / 'hook_runtime.py'), '--config', str(managed / 'ingest.json'), '--binary', str(binary)])
            hooks = config.merge_hooks({}, command, 'cdxhooks', ['UserPromptSubmit', 'PreToolUse', 'PostToolUse', 'Stop', 'SessionEnd'])
            (root / 'hooks.json').write_text(json.dumps(hooks))
            args = SimpleNamespace(agent='codex', config_dir=str(root), base_url='https://vision.test', space_id='space', agent_uuid='uuid')
            before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob('*') if p.is_file()}
            token = {'space_id': 'space', 'agent_uuid': 'uuid', 'agent_product': 'Codex', 'token': 'short-secret'}
            with patch.object(installer, 'request_data', return_value=token) as request:
                result = installer.installation_status(args)
                self.assertTrue(result['installed'])
                self.assertEqual(result['registration'], 'verified')
                self.assertEqual(result['credential'], 'valid')
                request.assert_called_once_with('https://vision.test/openapi/v1/ingest-tokens', {'agent_uuid': 'uuid'}, {'Authorization': 'Bearer aiv-agent-secret'})
                self.assertNotIn('secret', json.dumps(result))
            self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob('*') if p.is_file()})
            with patch.object(installer, 'request_data', return_value=token), patch.object(installer, 'check_binary_runtime', side_effect=installer.ConnectError('GLIBC_2.34 unavailable')):
                result = installer.installation_status(args)
                self.assertFalse(result['installed'])
                self.assertEqual(result['registration'], 'verified')
                self.assertEqual(result['credential'], 'valid')
                self.assertIn('GLIBC_2.34 unavailable', result['issues'])
            self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob('*') if p.is_file()})
            args.space_id = 'other'
            with patch.object(installer, 'request_data') as request:
                self.assertEqual(installer.installation_status(args)['credential'], 'scope_mismatch')
                request.assert_not_called()
            args.space_id = 'space'
            args.base_url = 'https://other.test'
            with patch.object(installer, 'DEFAULT_BASE_URL', args.base_url), patch.object(installer, 'request_data') as request:
                self.assertEqual(installer.installation_status(args)['credential'], 'scope_mismatch')
                request.assert_not_called()
            args.base_url = 'https://vision.test'
            with patch.object(installer, 'request_data', side_effect=installer.ConnectError('revoked', status=401)):
                self.assertEqual(installer.installation_status(args)['credential'], 'invalid')
            (root / 'config.toml').write_text('[features]\nhooks = false\n')
            with patch.object(installer, 'request_data', return_value=token):
                self.assertEqual(installer.installation_status(args)['registration'], 'missing')
                binary.write_text('damaged')
                self.assertFalse(installer.installation_status(args)['installed'])

    def test_host_directory_environment_is_shared_with_status(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'CODEX_HOME': temp}):
            args = SimpleNamespace(agent='cdx', config_dir=None, base_url=None, space_id=None, agent_uuid=None)
            result = installer.installation_status(args)
            self.assertEqual(result['config_dir'], str(Path(temp).resolve()))
            self.assertFalse(result['installed'])
            self.assertEqual(list(Path(temp).iterdir()), [])


class ReleaseArtifactTests(unittest.TestCase):
    def test_missing_architecture_and_corrupt_binary_fail_release_gate(self):
        with tempfile.TemporaryDirectory() as temp:
            dist = Path(temp)
            root = dist / 'codex'
            (root / 'test').mkdir(parents=True)
            header = bytearray(32)
            header[:6] = b'\x7fELF\x02\x01'
            header[18:20] = (62).to_bytes(2, 'little')
            path = root / 'test/cdxhooks-linux-x64'
            path.write_bytes(header)
            item = {'filename': path.name, 'platform': 'linux-gnu', 'arch': 'x64', 'target_filename': 'cdxhooks', 'sha256': hashlib.sha256(header).hexdigest()}
            (root / 'latest.json').write_text(json.dumps({'version': 'test', 'downloads': [item]}))
            catalog = [{'key': 'codex', 'binary': 'cdxhooks'}]
            verify(dist, catalog, {('linux-gnu', 'x64')})
            with self.assertRaisesRegex(ValueError, 'missing release platforms'):
                verify(dist, catalog, {('linux-gnu', 'arm64')})
            path.write_bytes(header + b'changed')
            with self.assertRaisesRegex(ValueError, 'SHA-256 mismatch'):
                verify(dist, catalog, {('linux-gnu', 'x64')})
            item['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
            item['arch'] = 'arm64'
            (root / 'latest.json').write_text(json.dumps({'version': 'test', 'downloads': [item]}))
            with self.assertRaisesRegex(ValueError, 'does not match'):
                verify(dist, catalog, {('linux-gnu', 'arm64')})
