import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import minimax_oci_acceptance as acceptance
import seekdb_deploy_smoke as deployment


class LocalImageIdentityTest(unittest.TestCase):
    def test_matching_refs_cannot_hide_a_retagged_image_or_wrong_architecture(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            values = {name: 'fixture' for name in (*deployment.SECRET_VARS, 'SIGNOZ_USER_ROOT_PASSWORD')}
            values.update({key: 'container-' + key for key in deployment.CONTAINER_VARS})
            refs = ['fixture/' + component + ':candidate' for component in deployment.COMPONENTS]
            values.update(zip(('OB_IMAGE', *deployment.IMAGE_VARS.values()), refs))
            ids = {component: 'sha256:' + str(index) * 64
                   for index, component in enumerate(deployment.COMPONENTS)}
            found = list(ids.values())
            platforms = [f'linux/amd64 {image}' for image in found]
            smoke = deployment.Smoke(runtime, runtime, values)

            def run(label, command):
                output = {
                    'images': [' '.join(pair) for pair in zip(found, refs)], 'images-platforms': platforms,
                    'images-expected': found, 'images-mounts': ['[]'] * 6,
                }[label]
                return subprocess.CompletedProcess(command, 0, '\n'.join(output))

            smoke.run = run
            receipt = smoke.images('images', expected_local_ids=ids)
            self.assertTrue(receipt['local_content_verified'])
            self.assertFalse(receipt['pinned_content_verified'])
            platforms[0] = f'linux/arm64 {found[0]}'
            with self.assertRaisesRegex(AssertionError, 'platform or identity'):
                smoke.images('images', expected_local_ids=ids)
            found[0] = 'sha256:' + 'f' * 64
            platforms[0] = f'linux/amd64 {found[0]}'
            with self.assertRaisesRegex(AssertionError, 'prepared local candidate'):
                smoke.images('images', expected_local_ids=ids)

    def test_formal_pinned_gate_still_rejects_local_tags(self):
        with self.assertRaisesRegex(ValueError, 'digest references'):
            deployment.image_overrides(json.dumps({key: 'fixture/' + key + ':candidate'
                                                   for key in deployment.IMAGE_VARS}),
                                       deployment.OCEANBASE_IMAGE, require_pinned=True)


class CandidateInputTest(unittest.TestCase):
    def test_rejects_archive_symlink_even_if_archive_checksum_matches(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'aiv-linux-amd64.tar.gz'
            with tarfile.open(path, 'w:gz') as archive:
                for name in sorted(acceptance.CLI_MEMBERS):
                    item = tarfile.TarInfo(name)
                    if name == 'aiv':
                        item.type, item.linkname = tarfile.SYMTYPE, '/usr/bin/false'
                    archive.addfile(item)
            with self.assertRaisesRegex(ValueError, 'unexpected file inventory'):
                acceptance.prepare_cli(path, root, {'cli_sha256': acceptance.digest(path)})
            self.assertFalse((root / 'aiv').exists())

    def test_local_model_key_is_forwarded_only_to_phase_command(self):
        expected_hash = '1' * 64
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            account = root / 'minimax/account.json'
            smoke = SimpleNamespace(runtime=root, values={'SIGNOZ_USER_ROOT_PASSWORD': 'fixture-secret'}, env={})
            trace = SimpleNamespace(name='fixture', space_id='test-space', origin='http://127.0.0.1:8060')

            def run(label, command, *, timeout):
                self.assertEqual(smoke.env, {'AIVISION_MINIMAX_LIVE_KEY_FILE': '/private/model-config'})
                self.assertEqual(account.stat().st_mode & 0o777, 0o600)
                self.assertEqual(json.loads(account.read_text()), {'email': 'fixture@example.invalid',
                                 'space_id': 'test-space', 'password': 'fixture-secret'})
                self.assertNotIn('/private/model-config', command)
                self.assertNotIn('fixture-secret', command)
                phase = label.removeprefix('minimax-')
                return SimpleNamespace(stdout=json.dumps({'status': 'passed', 'phase': phase,
                    'model_mode': 'openai-byok', 'artifact_sha256': expected_hash,
                    'trace_count': 4, 'traces': [{}] * 4}))

            smoke.run = Mock(side_effect=run)
            for phase in ('initial', 'recast'):
                self.assertEqual(acceptance.live_minimax(phase, smoke, trace, root / 'aiv', mcode=root / 'mcode',
                    plugin_sha=expected_hash, live_key_file='/private/model-config')['phase'], phase)
                self.assertEqual(smoke.env, {})
            self.assertIn('--artifact-sha256', smoke.run.call_args_list[0].args[1])
            self.assertNotIn('--artifact-sha256', smoke.run.call_args_list[1].args[1])

    def test_hosted_lane_rejects_key_configuration_before_any_deployment(self):
        with patch.dict(os.environ, {'GITHUB_ACTIONS': 'true', 'AIVISION_MINIMAX_LIVE_KEY_FILE': '/private/key'}), \
                patch('sys.stderr'), self.assertRaises(SystemExit) as caught:
            acceptance.main(['--inputs', 'unused', '--aiv-archive', 'unused', '--plugin-dist', 'unused',
                             '--mcode', 'unused', '--out', 'unused', '--local-aiv', 'unused'])
        self.assertEqual(caught.exception.code, 2)


if __name__ == '__main__':
    unittest.main()
