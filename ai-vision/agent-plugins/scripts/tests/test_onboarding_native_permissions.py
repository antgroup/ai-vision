import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_onboarding import config, installer


class NativeRegistrationTests(unittest.TestCase):
    def test_openclaw_grants_only_observer_conversation_hooks_idempotently(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            managed = root / 'aivision/openclaw'
            installed = managed / 'versions/test/observ-openclaw'
            installed.mkdir(parents=True)
            (installed / 'openclaw.plugin.json').write_text('{"id":"observ-openclaw"}')
            other = {'enabled': True, 'hooks': {'allowConversationAccess': False}}
            doc = {'plugins': {'allow': ['other'], 'entries': {'other': other,
                   'observ-openclaw': {'hooks': {'allowPromptInjection': False}}}}}
            path = root / 'openclaw.json'
            path.write_text(json.dumps(doc))
            args = SimpleNamespace(agent='openclaw', config_dir=str(root), collection_mode='privacy')
            token = {'endpoint': 'https://vision.test/v1/traces', 'product': 'OpenClaw'}
            for _ in range(2):
                with config.ConfigTransaction() as tx:
                    installer.register_native(args, {'key': 'openclaw'}, root, installed, managed, root, token, tx, config)
            saved = json.loads(path.read_text())
            entry = saved['plugins']['entries']['observ-openclaw']
            self.assertEqual(entry['hooks'], {'allowPromptInjection': False, 'allowConversationAccess': True})
            self.assertEqual(saved['plugins']['entries']['other'], other)
            self.assertEqual(saved['plugins']['load']['paths'], [str(installed)])
            self.assertTrue(installer.native_registered('openclaw', root, installed, managed, args, {}))
            for permission in (None, False):
                blocked = copy.deepcopy(saved)
                hooks = blocked['plugins']['entries']['observ-openclaw']['hooks']
                hooks.pop('allowConversationAccess') if permission is None else hooks.update(allowConversationAccess=False)
                path.write_text(json.dumps(blocked))
                self.assertFalse(installer.native_registered('openclaw', root, installed, managed, args, {}))

    def test_openclaw_explicit_denial_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            installed = root / 'plugin'
            installed.mkdir()
            (installed / 'openclaw.plugin.json').write_text('{"id":"observ-openclaw"}')
            path = root / 'openclaw.json'
            original = '{"plugins":{"entries":{"observ-openclaw":{"hooks":{"allowConversationAccess":false}}}}}'
            path.write_text(original)
            args = SimpleNamespace(agent='openclaw', config_dir=str(root), collection_mode='full')
            with self.assertRaisesRegex(installer.ConnectError, 'allowConversationAccess'):
                with config.ConfigTransaction() as tx:
                    installer.register_native(args, {'key': 'openclaw'}, root, installed, root, root,
                                              {'endpoint': 'https://vision.test', 'product': 'OpenClaw'}, tx, config)
            self.assertEqual(path.read_text(), original)

    def test_pi_recognizes_native_relative_and_object_package_sources_without_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            managed = root / 'aivision/pi'
            installed = managed / 'versions/test/observ-pi'
            installed.mkdir(parents=True)
            (root / 'observ-pi.json').write_text(json.dumps({'enableTelemetry': True, 'ingestConfig': str(managed / 'ingest.json')}))
            args = SimpleNamespace(config_dir=str(root))
            relative = str(installed.relative_to(root))
            for source in (str(installed), relative, './' + relative, {'source': relative, 'extensions': ['dist/index.js']}):
                with self.subTest(source=source):
                    path = root / 'settings.json'
                    path.write_text(json.dumps({'packages': ['npm:unrelated', source]}))
                    before = (path.read_bytes(), path.stat().st_mtime_ns)
                    self.assertTrue(installer.native_registered('pi', root, installed, managed, args, {}))
                    self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)
            (root / 'settings.json').write_text('{"packages":["npm:observ-pi", "https://example.test/observ-pi", {}]}')
            self.assertFalse(installer.native_registered('pi', root, installed, managed, args, {}))

    def test_pi_reinstall_removes_old_relative_managed_source_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            managed = root / 'aivision/pi'
            installed = managed / 'versions/new/observ-pi'
            installed.mkdir(parents=True)
            unrelated = ['npm:other', {'source': '../custom-plugin', 'extensions': ['index.ts']}]
            path = root / 'settings.json'
            path.write_text(json.dumps({'packages': [*unrelated, 'aivision/pi/versions/old/observ-pi',
                                                     {'source': str(managed / 'versions/older/observ-pi')}]}))
            def host(command, env):
                if command[1] == 'install':
                    doc = json.loads(path.read_text())
                    self.assertEqual(doc['packages'], unrelated)
                    doc['packages'].append(str(installed.relative_to(root)))
                    path.write_text(json.dumps(doc))
                return ''
            args = SimpleNamespace(config_dir=str(root), collection_mode='full')
            with patch.object(installer, 'run', side_effect=host):
                for _ in range(2):
                    with config.ConfigTransaction() as tx:
                        installer.register_native(args, {'key': 'pi', 'host': 'pi'}, root, installed, managed, root,
                                                  {'endpoint': 'https://vision.test', 'product': 'Pi'}, tx, config)
            self.assertEqual(json.loads(path.read_text())['packages'], [*unrelated, str(installed.relative_to(root))])

    def test_openclaw_status_explains_missing_permission_without_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            managed = root / 'aivision/openclaw'
            installed = managed / 'versions/test/observ-openclaw'
            (installed / 'dist').mkdir(parents=True)
            for name in ('package.json', 'dist/index.js'):
                (installed / name).write_text('{}')
            (installed / 'openclaw.plugin.json').write_text('{"id":"observ-openclaw"}')
            (managed / 'installation.json').write_text(json.dumps({'agent': 'openclaw', 'installed_path': str(installed), 'version': 'test'}))
            saved = {'agent': 'openclaw', 'base_url': 'https://vision.test', 'space_id': 'space',
                     'agent_uuid': 'agent', 'product': 'OpenClaw', 'agent_credential': 'test-credential', 'collection_mode': 'full'}
            (managed / 'ingest.json').write_text(json.dumps(saved))
            entry = {'enabled': True, 'config': {'enableTelemetry': True, 'ingestConfig': str(managed / 'ingest.json')}}
            (root / 'openclaw.json').write_text(json.dumps({'plugins': {'load': {'paths': [str(installed)]},
                                                        'entries': {'observ-openclaw': entry}}}))
            args = SimpleNamespace(agent='openclaw', config_dir=str(root), base_url='https://vision.test', space_id='space', agent_uuid='agent')
            before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob('*') if p.is_file()}
            with patch.object(installer, 'request_data', return_value={'space_id': 'space', 'agent_uuid': 'agent', 'agent_product': 'OpenClaw'}):
                status = installer.installation_status(args)
            self.assertEqual(status['registration'], 'missing')
            self.assertIn('allowConversationAccess=true', status['issues'][0])
            self.assertNotIn('test-credential', json.dumps(status))
            self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob('*') if p.is_file()})
