import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_onboarding import installer, runtime


class CollectionModeTests(unittest.TestCase):
    def test_kimi_content_flags_follow_final_session_mode(self):
        flags = ['KIMI_LOG_' + signal + '_' + content
                 for signal in ('SPANS', 'EVENTS')
                 for content in ('USER_PROMPTS', 'MODEL_OUTPUTS', 'TOOL_DETAILS', 'TOOL_OUTPUTS')]
        for initial, following in (('full', 'privacy'), ('privacy', 'full')):
            with self.subTest(initial=initial), tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {}, clear=True):
                config = {'endpoint': 'https://vision.test', 'agent': 'kimi', 'config_dir': temp,
                          'product': 'KimiCLI', 'collection_mode': initial}
                # A new launcher environment per hook, just like the native host.
                env = runtime.hook_env(config, {'token': 'test'})
                runtime.pin_session_mode(config, env, 'same-session')
                runtime.configure_content_logging(config, env)
                self.assertTrue(all(env[k] == ('true' if initial == 'full' else 'false') for k in flags))
                config['collection_mode'] = following
                env = runtime.hook_env(config, {'token': 'test'})
                runtime.pin_session_mode(config, env, 'same-session')
                runtime.configure_content_logging(config, env)
                self.assertTrue(all(env[k] == ('true' if initial == 'full' else 'false') for k in flags))
                self.assertEqual(env['CCHOOKS_TRANSCRIPT_CAPTURE'], 'false')

    def test_kimi_explicit_content_opt_out_is_preserved_and_privacy_wins(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'KIMI_LOG_SPANS_USER_PROMPTS': 'false',
                                                                        'KIMI_LOG_EVENTS_MODEL_OUTPUTS': 'true'}, clear=True):
            config = {'endpoint': 'https://vision.test', 'agent': 'kimi', 'config_dir': temp,
                      'product': 'KimiCLI', 'collection_mode': 'full'}
            env = runtime.hook_env(config, {'token': 'test'})
            runtime.configure_content_logging(config, env)
            self.assertEqual(env['KIMI_LOG_SPANS_USER_PROMPTS'], 'false')
            self.assertEqual(env['KIMI_LOG_SPANS_MODEL_OUTPUTS'], 'true')
            config['collection_mode'] = 'privacy'
            env = runtime.hook_env(config, {'token': 'test'})
            runtime.configure_content_logging(config, env)
            self.assertEqual(env['KIMI_LOG_EVENTS_MODEL_OUTPUTS'], 'false')

    def test_native_migration_and_mode_isolation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            args = SimpleNamespace(config_dir=str(root))
            self.assertEqual(installer.saved_collection_mode('codex', root, args), ('full', 'default'))
            (root / 'observ-pi.json').write_text(json.dumps({'collectionMode': ' Privacy ', 'unrelated': 42}))
            self.assertEqual(installer.saved_collection_mode('pi', root, args), ('privacy', 'native_config'))
            self.assertEqual(installer.saved_collection_mode('codex', root, args), ('full', 'default'))
            (root / 'observ-pi.json').write_text('{invalid')
            with self.assertRaises(installer.ConnectError):
                installer.saved_collection_mode('pi', root, args)

    def test_preflight_rejects_unsupported_privacy_without_registration_or_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            args = SimpleNamespace(agent='codex', base_url='https://example.test', config_dir=temp, collection_mode='privacy')
            entry = {'key': 'codex', 'plugin': {'host': 'codex', 'name': 'Codex', 'binary': 'cdxhooks'}, 'downloads': []}
            with patch.object(installer, 'request_data', return_value={'agents': [entry]}) as request, patch.object(installer, 'platform_download', return_value={}):
                with self.assertRaisesRegex(installer.ConnectError, 'does not support'):
                    installer.preflight(args)
                self.assertEqual(request.call_count, 1)
                self.assertEqual(request.call_args.args, ('https://example.test/api/public/onboarding/catalog.json',))
                self.assertEqual(list(Path(temp).iterdir()), [])

    def test_launcher_priority_invalid_modes_and_session_stability(self):
        with tempfile.TemporaryDirectory() as temp:
            config = {'endpoint': 'https://example.test', 'agent': 'codex', 'config_dir': temp, 'product': 'Codex', 'collection_mode': 'privacy'}
            with patch.dict(os.environ, {}, clear=True):
                env = runtime.hook_env(config, {'token': 'test'})
                self.assertEqual(env['CDXHOOKS_COLLECTION_MODE'], 'privacy')
                runtime.pin_session_mode(config, env, 'session-one')
                config['collection_mode'] = 'full'
                new_env = runtime.hook_env(config, {'token': 'test'})
                runtime.pin_session_mode(config, new_env, 'session-one')
                self.assertEqual(new_env['AIVISION_COLLECTION_MODE'], 'privacy')
                next_env = runtime.hook_env(config, {'token': 'test'})
                runtime.pin_session_mode(config, next_env, 'session-two')
                self.assertEqual(next_env['AIVISION_COLLECTION_MODE'], 'full')
                os.environ['CDXHOOKS_COLLECTION_MODE'] = 'full'
                os.environ['AIVISION_COLLECTION_MODE'] = ' Privacy '
                self.assertEqual(runtime.hook_env(config, {'token': 'test'})['CDXHOOKS_COLLECTION_MODE'], 'privacy')
                os.environ['AIVISION_COLLECTION_MODE'] = 'invalid'
                with self.assertRaises(ValueError):
                    runtime.hook_env(config, {'token': 'test'})
            # Session pinning/privacy above applies on every platform; mode bits
            # are a POSIX filesystem guarantee, not a Windows ACL assertion.
            if os.name != 'nt':
                for path in Path(temp).rglob('*.json'):
                    self.assertEqual(path.stat().st_mode & 0o077, 0)

    def test_native_settings_keep_unrelated_configuration(self):
        result = installer.plugin_config({'collectionMode': 'full', 'custom': 42}, Path('/config'), {'endpoint': 'https://example.test', 'product': 'Pi'}, 'privacy')
        self.assertEqual(result['collectionMode'], 'privacy')
        self.assertEqual(result['custom'], 42)
        self.assertTrue(result['enableTelemetry'])
