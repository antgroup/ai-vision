"""Offline harness checks; these do not substitute for real candidate deployment."""

import contextlib
import base64
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import seekdb_deploy_smoke as deploy


def pinned_images():
    return {key: f'example/{key}@sha256:{number:064x}'
            for number, key in enumerate(deploy.IMAGE_VARS, 1)}


def smoke_values(backend):
    database, _ = deploy.database_config(backend, 'test')
    return {**database, 'NAMESPACE': 'test', 'OB_BACKEND': backend,
            **{key: 'secret-' + key for key in deploy.SECRET_VARS},
            'SIGNOZ_USER_ROOT_PASSWORD': 'private-password', 'DOCKER_NETWORK': 'test-net',
            **{key: 'test-' + name for key, name in zip(deploy.CONTAINER_VARS, deploy.COMPONENTS)},
            **{deploy.IMAGE_VARS[key]: value for key, value in pinned_images().items()}}


class DeploymentSmokeTests(unittest.TestCase):
    def test_source_fixture_reads_its_authenticated_signoz_contract(self):
        smoke = types.SimpleNamespace(values={'FE_PORT': '18000'}, private=[])
        canary = deploy.TraceCanary(smoke, 'fixture', legacy_source=True)
        canary.space_id, canary.agent_uuid = 'space', 'agent'
        canary.expected_spans = {canary.span_id}
        payload = {'code': 0, 'data': {'traceId': canary.trace_id, 'spans': [
            {'span_id': canary.span_id, 'name': 'fixture'}]}}
        with patch.object(canary, 'request', return_value=(200, payload)) as request:
            result = canary.read()
        self.assertEqual(result['verified_spans'], 1)
        self.assertEqual(result['readback'], 'SigNoz v1 (legacy source fixture)')
        self.assertEqual(request.call_args.args[0],
                         f'/api/v1/spaces/space/signoz/trace/{canary.trace_id}/detail?agent_product=langfuse&agent_uuid=agent')
        self.assertNotIn('headers', request.call_args.kwargs)
        with patch.object(canary, 'request', return_value=(200, {
                'code': 0, 'data': {'traceId': 'another-trace', 'spans': payload['data']['spans']}})):
            with self.assertRaisesRegex(AssertionError, 'unexpected canary identity'):
                canary.read()

    def test_candidate_authentication_failure_never_uses_legacy_readback(self):
        smoke = types.SimpleNamespace(values={'FE_PORT': '18000'}, private=[])
        canary = deploy.TraceCanary(smoke, 'fixture')
        canary.basic = 'synthetic-credentials'
        with patch.object(canary, 'request', return_value=(401, {})) as request:
            with self.assertRaisesRegex(RuntimeError, 'HTTP 401'):
                canary.read()
        self.assertEqual(request.call_count, 1)
        self.assertEqual(request.call_args.args[0], '/api/public/traces/' + canary.trace_id)

    def test_optional_installed_release_record_is_passed_to_both_database_profiles(self):
        manifest = {'product_version': '0.1.0', 'channel': 'stable', 'source_sha': 'a' * 40,
                    'build_id': '123.1', 'component_builds': {'aivision-be': {}, 'aivision-fe': {}}}
        encoded = base64.b64encode(json.dumps(manifest).encode()).decode()
        for backend in ('seekdb', 'oceanbase'):
            with self.subTest(backend=backend), patch.dict(deploy.os.environ, {
                'AIVISION_RELEASE_MANIFEST_BASE64': encoded
            }):
                values = dict(deploy.database_config(backend, 'fixture')[0], **deploy.release_metadata_vars())
                self.assertEqual(values['AIVISION_RELEASE_MANIFEST_BASE64'], encoded)
        with patch.dict(deploy.os.environ, {'AIVISION_RELEASE_MANIFEST_BASE64': ''}):
            self.assertEqual(deploy.release_metadata_vars(), {})
        for invalid in ('bad-base64', 'bnVsbA==', 'x' * 90000):
            with self.subTest(invalid=invalid[:20]), patch.dict(deploy.os.environ, {
                'AIVISION_RELEASE_MANIFEST_BASE64': invalid
            }), self.assertRaises(ValueError):
                deploy.release_metadata_vars()

    def test_version_canary_compares_installed_release_and_own_backend_build(self):
        builds = {name: {'component': name, 'source_sha': character * 40}
                  for name, character in (('aivision-be', 'a'), ('aivision-fe', 'b'))}
        manifest = {'product_version': '0.1.0', 'channel': 'stable', 'source_sha': 'c' * 40,
                    'build_id': '123.1', 'component_builds': builds}
        encoded = base64.b64encode(json.dumps(manifest).encode()).decode()
        expected = {key: manifest[key] for key in ('product_version', 'channel', 'source_sha', 'build_id')}
        expected['components'] = builds
        smoke = types.SimpleNamespace(values={'FE_PORT': '18000', 'AIVISION_RELEASE_MANIFEST_BASE64': encoded})
        canary = deploy.TraceCanary(smoke, 'version-fixture')
        payload = {'backend': builds['aivision-be'], 'expected': expected}
        with patch.object(canary, 'management', return_value=payload):
            self.assertEqual(canary.version()['product_version'], '0.1.0')
        for invalid in ({**payload, 'expected': None},
                        {**payload, 'backend': builds['aivision-fe']},
                        {**payload, 'expected': {**expected, 'product_version': '0.0.1'}}):
            with self.subTest(invalid=invalid), patch.object(canary, 'management', return_value=invalid):
                with self.assertRaises(AssertionError):
                    canary.version()

    def test_backend_accounts_and_persistent_resources_are_distinct(self):
        cases = [('seekdb', 'root', ['aivforge-seekdb-data-test'], deploy.SEEKDB_IMAGE),
                 ('oceanbase', 'root@aivision', ['aivforge-ob-data-test', 'aivforge-ob-config-test'],
                  deploy.OCEANBASE_IMAGE)]
        with patch.dict(deploy.os.environ, {}, clear=True):
            for backend, user, volumes, image in cases:
                with self.subTest(backend=backend):
                    config, actual_volumes = deploy.database_config(backend, 'test')
                    self.assertEqual(config['OB_USER'], user)
                    self.assertEqual(config['OB_IMAGE'], image)
                    self.assertEqual(actual_volumes, volumes)
                    if backend == 'oceanbase':
                        self.assertEqual(config['OB_TENANT'], 'aivision')
        with patch.dict(deploy.os.environ, {'SEEKDB_IMAGE': 'seekdb:test', 'OCEANBASE_IMAGE': 'ce:test'}):
            self.assertEqual(deploy.database_config('seekdb', 'test')[0]['OB_IMAGE'], 'seekdb:test')
            self.assertEqual(deploy.database_config('oceanbase', 'test')[0]['OB_IMAGE'], 'ce:test')
        with self.assertRaises(ValueError):
            deploy.database_config('unexpected', 'test')

    def test_release_mode_rejects_missing_or_mutable_images(self):
        images = pinned_images()
        self.assertEqual(deploy.image_overrides(json.dumps(images), deploy.SEEKDB_IMAGE,
                                               require_pinned=True), images)
        for invalid, database in [({}, deploy.SEEKDB_IMAGE),
                                  ({**images, 'backend': 'example/backend:dev'}, deploy.SEEKDB_IMAGE),
                                  (images, 'oceanbase/oceanbase-ce:latest'),
                                  ({**images, 'extra': deploy.SEEKDB_IMAGE}, deploy.SEEKDB_IMAGE),
                                  ({**images, 'backend': ''}, deploy.SEEKDB_IMAGE),
                                  ([], deploy.SEEKDB_IMAGE)]:
            with self.subTest(images=invalid, database=database), self.assertRaises(ValueError):
                deploy.image_overrides(json.dumps(invalid), database, require_pinned=True)
        self.assertEqual(deploy.image_overrides('{"frontend":"local/frontend:dev"}', deploy.SEEKDB_IMAGE),
                         {'frontend': 'local/frontend:dev'})

    def test_release_mode_requires_an_existing_executable_cli_before_docker(self):
        with tempfile.TemporaryDirectory() as folder:
            binary = Path(folder) / 'aiv'
            binary.write_text('not executable')
            command = [sys.executable, deploy.__file__, '--out', str(Path(folder) / 'evidence'),
                       '--require-pinned-images']
            for extra in [[], ['--aiv', str(binary)]]:
                with self.subTest(extra=extra):
                    result = subprocess.run(command + extra, capture_output=True, text=True)
                    self.assertEqual(result.returncode, 2)
                    self.assertIn('executable release candidate', result.stderr)
                    self.assertFalse((Path(folder) / 'evidence').exists())

    def test_legacy_baseline_modes_cannot_weaken_candidate_validation(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / 'evidence'
            for legacy in (['--source-baseline-legacy-health'], ['--baseline-aiv', '/older/aiv']):
                result = subprocess.run([sys.executable, deploy.__file__, '--out', str(out),
                    *legacy, '--require-pinned-images'], capture_output=True, text=True)
                self.assertEqual(result.returncode, 2)
                self.assertIn('cannot be combined', result.stderr)
                self.assertFalse(out.exists())

    def test_legacy_baseline_export_changes_only_product_marker_and_four_known_probes(self):
        source = (Path(deploy.__file__).resolve().parents[1] / 'blueprints/aivision.yaml').read_text()
        with tempfile.TemporaryDirectory() as folder:
            runtime = Path(folder)
            smoke = deploy.Smoke(runtime, runtime, smoke_values('seekdb'))
            with patch.object(smoke, 'run', return_value=subprocess.CompletedProcess([], 0, source)) as run:
                blueprint = deploy.source_baseline_blueprint(smoke, Path('/matching/aiv'))
            self.assertEqual(run.call_args.args[1], ['/matching/aiv', 'gen', '-o', '-'])
            actual = blueprint.read_text()
            expected = source.replace('  product: aivision\n', '').replace('execArgv: ["/app/signoz", "healthcheck", "--ready"]',
                'http: http://127.0.0.1:${SIGNOZ_PORT}/api/v1/health').replace(
                'execArgv: ["/app/collector", "healthcheck", "--ready"]',
                'http: http://127.0.0.1:${GATEWAY_HEALTH_PORT}/debug/pprof/').replace(
                'http: http://127.0.0.1:${BE_PORT}/api/v1/ready',
                'http: http://127.0.0.1:${BE_PORT}/api/v1/health').replace(
                'http: http://127.0.0.1:${AGENTLENS_PORT}/internal/readyz',
                'http: http://127.0.0.1:${AGENTLENS_PORT}/internal/healthz')
            self.assertEqual(actual, expected)
            self.assertEqual(blueprint.stat().st_mode & 0o777, 0o600)
            self.assertIn('execArgv: ["/app/collector", "healthcheck", "--ready"]', source)
            for invalid in (source.replace('execArgv: ["/app/signoz", "healthcheck", "--ready"]', 'execArgv: ["changed"]'),
                            source + '\n      execArgv: ["/app/collector", "healthcheck", "--ready"]\n',
                            source.replace('/api/v1/ready', '/api/v1/changed'),
                            source + '\n      http: http://127.0.0.1:${AGENTLENS_PORT}/internal/readyz\n',
                            source.replace('  product: aivision\n', ''),
                            source.replace('metadata:\n', 'different:\n'),
                            source + '\n  product: aivision\n'):
                with self.subTest(invalid=invalid[-80:]):
                    with patch.object(smoke, 'run', return_value=subprocess.CompletedProcess([], 0, invalid)):
                        with self.assertRaises(ValueError):
                            deploy.source_baseline_blueprint(smoke, Path('/matching/aiv'))

    def test_legacy_and_candidate_runs_keep_their_own_blueprint_for_every_command(self):
        source = (Path(deploy.__file__).resolve().parents[1] / 'blueprints/aivision.yaml').read_text()
        metadata = base64.b64encode(json.dumps({'product_version': '0.1.0'}).encode()).decode()
        for backend, legacy in ((backend, legacy) for backend in ('seekdb', 'oceanbase') for legacy in (False, True)):
            with self.subTest(backend=backend, legacy=legacy), tempfile.TemporaryDirectory() as folder:
                root = Path(folder).resolve()
                binary = root / 'aiv'
                binary.write_text('synthetic CLI; commands are captured')
                binary.chmod(0o700)
                commands = []

                def run(smoke, label, command, **kwargs):
                    commands.append(command)
                    if label == 'export-source-blueprint':
                        return subprocess.CompletedProcess(command, 0, source)
                    if label.startswith('check-absent-'):
                        return subprocess.CompletedProcess(command, 1, '')
                    if label in ('cast', 'recast'):
                        self.assertEqual(smoke.values['AIVISION_RELEASE_MANIFEST_BASE64'], metadata)
                        saved = smoke.runtime / 'work' / smoke.values['DOCKER_NETWORK'].removesuffix('-net') / 'state/vars.json'
                        saved.parent.mkdir(parents=True, exist_ok=True)
                        saved.write_text(json.dumps(smoke.values))
                        saved.chmod(0o600)
                    if label == 'external-reject-wrong-password':
                        return subprocess.CompletedProcess(command, 1, '1045 access denied')
                    if label == 'initial-database-entrypoint':
                        return subprocess.CompletedProcess(command, 0, '["/bin/sh"]')
                    return subprocess.CompletedProcess(command, 0, 'linux/amd64' if label == 'docker-info' else '')

                argv = [deploy.__file__, '--out', str(root / 'evidence'), '--runtime-base', str(root),
                        '--aiv', str(binary), '--backend', backend,
                        '--source-baseline-legacy-health' if legacy else '--require-pinned-images']
                with patch('sys.argv', argv), patch.object(deploy, 'ports', return_value={key: str(20000 + i) for i, key in enumerate(deploy.PORT_VARS)}), patch.dict(deploy.os.environ, {
                    'AIVISION_SEEKDB_SMOKE_IMAGES_JSON': json.dumps(pinned_images()),
                    'AIVISION_RELEASE_MANIFEST_BASE64': metadata,
                }), patch.object(deploy.Smoke, 'run', new=run), \
                     patch.object(deploy.Smoke, 'images', return_value={'platforms': ['linux/amd64'] * 6}), \
                     patch.object(deploy.Smoke, 'healthy', return_value=True), \
                     patch.object(deploy.Smoke, 'sql', return_value={'canary': True}), \
                     patch.object(deploy.Smoke, 'database_identity', return_value={'fixture': 'unchanged'}), \
                     patch.object(deploy.Smoke, 'cleanup', return_value=[]) as cleanup, \
                     patch.object(deploy, 'TraceCanary', return_value=types.SimpleNamespace(ingest=lambda: None, read=lambda: {'trace_id': 'synthetic'}, version=lambda: {'fixture': 'matched'})), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(deploy.main(), 0)
                cli_commands = [command for command in commands if command[0] == str(binary)]
                if legacy:
                    self.assertEqual(cli_commands[0], [str(binary), 'gen', '-o', '-'])
                    cli_commands = cli_commands[1:]
                else:
                    self.assertFalse(any('gen' in command for command in cli_commands))
                cli_commands.append(cleanup.call_args.args[0])
                self.assertTrue(any(command[1] == 'gauge' for command in cli_commands))
                self.assertEqual(sum(command[1] == 'cast' for command in cli_commands), 2)
                for command in cli_commands:
                    self.assertEqual('--file' in command, legacy, command)
                    if legacy:
                        self.assertTrue(command[command.index('--file') + 1].endswith('source-baseline-blueprint.yaml'))
                summary = json.loads((root / 'evidence/summary.json').read_text())
                self.assertEqual(summary['health_probe_mode'], 'legacy-source-baseline-http' if legacy else 'native')
                self.assertEqual(summary['lifecycle_mode'], 'legacy-source-fixture' if legacy else 'product')
                self.assertEqual(summary['strict_product_shutdown_checked'], not legacy)
                self.assertEqual(summary['initial_version'], {'fixture': 'matched'})
                self.assertEqual(summary['recast_version'], {'fixture': 'matched'})

    def test_database_probe_uses_selected_account_and_rejects_empty_password(self):
        class OperationalError(Exception):
            pass

        class Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def execute(self, query, parameters=None):
                if query.startswith('INSERT'):
                    stored[parameters[0]] = parameters[1]
                if query.startswith('SELECT value'):
                    self.row = (stored.get(parameters[0]),)

            def fetchall(self):
                return [(name,) for name in ('aivision', 'signoz', 'agentlens')]

            def fetchone(self):
                return self.row

        class Connection:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def cursor(self):
                return Cursor()

            def close(self):
                pass

        for backend, expected_user in [('seekdb', 'root'), ('oceanbase', 'root@aivision')]:
            for empty_result in ['rejected', 'accepted', 'unreachable']:
                with self.subTest(backend=backend, empty_result=empty_result), tempfile.TemporaryDirectory() as folder:
                    stored, connections = {}, []
                    smoke = deploy.Smoke(Path(folder), Path(folder), smoke_values(backend))

                    def connect(**options):
                        connections.append(options)
                        self.assertEqual(options['user'], expected_user)
                        self.assertEqual(options['host'], 'test-database')
                        if not options['password'] and empty_result != 'accepted':
                            raise OperationalError(1045 if empty_result == 'rejected' else 2003, 'test rejection')
                        return Connection()

                    def run_sql(label, command, *, stdin):
                        self.assertNotIn(smoke.values['OB_TENANT_PASSWORD'], ' '.join(command))
                        output = io.StringIO()
                        module = types.SimpleNamespace(connect=connect, err=types.SimpleNamespace(OperationalError=OperationalError))
                        with patch.dict(sys.modules, {'pymysql': module}), patch('sys.stdin', io.StringIO(stdin)), contextlib.redirect_stdout(output):
                            exec(command[-1], {})
                        return subprocess.CompletedProcess(command, 0, output.getvalue())

                    with patch.object(smoke, 'run', side_effect=run_sql):
                        if empty_result == 'rejected':
                            self.assertTrue(smoke.sql('initial', write=True)['canary'])
                            self.assertTrue(smoke.sql('recast')['canary'])
                            self.assertEqual(len(connections), 4)
                        else:
                            with self.assertRaises(AssertionError):
                                smoke.sql('initial', write=True)

    def test_release_checks_running_image_content_and_private_mounts(self):
        for failure in [None, 'reference', 'content', 'source-mount', 'missing-mounts',
                        'all-arm64', 'one-arm64', 'wrong-platform-image-id', 'malformed-platform']:
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as folder:
                runtime = Path(folder).resolve()
                smoke = deploy.Smoke(runtime, runtime, smoke_values('oceanbase'))
                expected = [smoke.values[key] for key in ('OB_IMAGE', *deploy.IMAGE_VARS.values())]
                ids = [f'sha256:{index:064x}' for index in range(6)]
                refs = list(expected)
                if failure == 'reference':
                    refs[-1] = 'example/frontend:dev'
                resolved = ids[:-1] + ['unexpected'] if failure == 'content' else ids
                mount = {'Type': 'bind', 'Source': str(runtime / 'work/config.yaml')}
                if failure == 'source-mount':
                    mount['Source'] = str(runtime.parent / 'checkout/application.py')
                outputs = {
                    'images': '\n'.join(f'{identity} {ref}' for identity, ref in zip(ids, refs)),
                    'images-platforms': '\n'.join('linux/amd64 ' + identity for identity in ids),
                    'images-expected': '\n'.join(resolved),
                    'images-mounts': '\n'.join([json.dumps([mount])] * (5 if failure == 'missing-mounts' else 6)),
                }
                platforms = outputs['images-platforms'].splitlines()
                if failure == 'all-arm64':
                    platforms = [row.replace('linux/amd64', 'linux/arm64') for row in platforms]
                elif failure == 'one-arm64':
                    platforms[3] = platforms[3].replace('linux/amd64', 'linux/arm64')
                elif failure == 'wrong-platform-image-id':
                    platforms[3] = 'linux/amd64 sha256:' + 'f' * 64
                elif failure == 'malformed-platform':
                    platforms[3] = 'linux/amd64'
                outputs['images-platforms'] = '\n'.join(platforms)
                with patch.object(smoke, 'run', side_effect=lambda label, command: subprocess.CompletedProcess(command, 0, outputs[label])):
                    if failure:
                        with self.assertRaises(AssertionError):
                            smoke.images('images', require_pinned=True)
                    else:
                        result = smoke.images('images', require_pinned=True)
                        self.assertTrue(result['pinned_content_verified'])
                        self.assertEqual(result['references']['backend'], expected[3])
                        self.assertEqual(len(result['platforms']), 6)
                    if failure == 'all-arm64':
                        source_result = smoke.images('images')
                        self.assertFalse(source_result['pinned_content_verified'])
                        self.assertTrue(all(row.startswith('linux/arm64 ') for row in source_result['platforms'].values()))

    def test_candidate_main_checks_actual_platforms_during_both_casts(self):
        for backend in ('oceanbase', 'seekdb'):
            for wrong_phase in (None, 'deployed-images', 'recast-images'):
                with self.subTest(backend=backend, wrong_phase=wrong_phase), tempfile.TemporaryDirectory() as folder:
                    root = Path(folder).resolve()
                    binary = root / 'aiv'
                    binary.write_text('external CLI boundary')
                    binary.chmod(0o700)
                    ids = [f'sha256:{index:064x}' for index in range(6)]
                    labels = []

                    def run(smoke, label, command, **kwargs):
                        labels.append(label)
                        if label.startswith('check-absent-'):
                            return subprocess.CompletedProcess(command, 1, '')
                        if label in ('cast', 'recast'):
                            saved = smoke.runtime / 'work' / smoke.values['NAMESPACE'] / 'state/vars.json'
                            saved.parent.mkdir(parents=True, exist_ok=True)
                            saved.write_text(json.dumps(smoke.values))
                            saved.chmod(0o600)
                        if label in ('deployed-images', 'recast-images'):
                            refs = [smoke.values[key] for key in ('OB_IMAGE', *deploy.IMAGE_VARS.values())]
                            output = '\n'.join(f'{identity} {ref}' for identity, ref in zip(ids, refs))
                        elif label.endswith('-platforms'):
                            platforms = ['linux/amd64 ' + identity for identity in ids]
                            if wrong_phase and label == wrong_phase + '-platforms':
                                platforms[3] = 'linux/arm64 ' + ids[3]
                            output = '\n'.join(platforms)
                        elif label.endswith('-expected'):
                            output = '\n'.join(ids)
                        elif label.endswith('-mounts'):
                            output = '\n'.join(['[]'] * 6)
                        elif label == 'docker-info':
                            output = 'linux/x86_64'
                        elif label == 'initial-database-entrypoint':
                            output = '["/usr/bin/seekdb"]'
                        elif label == 'external-reject-wrong-password':
                            return subprocess.CompletedProcess(command, 1, '1045 access denied')
                        else:
                            output = ''
                        return subprocess.CompletedProcess(command, 0, output)

                    argv = [deploy.__file__, '--backend', backend, '--aiv', str(binary),
                            '--require-pinned-images', '--runtime-base', str(root), '--out', str(root / 'evidence')]
                    with patch('sys.argv', argv), patch.object(deploy, 'ports', return_value={
                            key: str(20000 + i) for i, key in enumerate(deploy.PORT_VARS)}), \
                         patch.dict(deploy.os.environ, {
                             'AIVISION_SEEKDB_SMOKE_IMAGES_JSON': json.dumps(pinned_images()),
                             'AIVISION_RELEASE_MANIFEST_BASE64': '', 'DOCKER_DEFAULT_PLATFORM': 'linux/arm64'}), \
                         patch.object(deploy.Smoke, 'run', new=run), \
                         patch.object(deploy.Smoke, 'healthy', return_value=True), \
                         patch.object(deploy.Smoke, 'sql', return_value={'canary': True}), \
                         patch.object(deploy.Smoke, 'database_identity', return_value={'identity': 'same'}), \
                         patch.object(deploy.Smoke, 'cleanup', return_value=[]) as cleanup, \
                         patch.object(deploy, 'TraceCanary', return_value=types.SimpleNamespace(
                             ingest=lambda: None, read=lambda: {'trace': 'fixture'})), \
                         contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(deploy.main(), 1 if wrong_phase else 0)
                    summary = json.loads((root / 'evidence/summary.json').read_text())
                    self.assertEqual(summary['status'], 'failed' if wrong_phase else 'passed')
                    self.assertEqual(cleanup.call_count, 1)
                    if wrong_phase:
                        self.assertIn('platform', summary['error'])
                    else:
                        for phase in ('deployed-images', 'recast-images'):
                            self.assertIn(phase + '-platforms', labels)

    def test_cleanup_checks_both_oceanbase_volumes_without_global_prune(self):
        for leftover in [None, 'aivforge-ob-config-test']:
            with self.subTest(leftover=leftover), tempfile.TemporaryDirectory() as folder:
                smoke = deploy.Smoke(Path(folder), Path(folder), smoke_values('oceanbase'))
                _, volumes = deploy.database_config('oceanbase', 'test')
                commands = []

                def run(label, command, **kwargs):
                    commands.append(command)
                    output = leftover if label == 'check-removed-' + str(leftover) else ''
                    return subprocess.CompletedProcess(command, 0, output)

                with patch.object(smoke, 'run', side_effect=run):
                    errors = smoke.cleanup(['aiv', 'destroy'], ['test-container'], volumes, ['test-network'])
                self.assertEqual(bool(errors), bool(leftover))
                for volume in volumes:
                    self.assertIn(['docker', 'volume', 'rm', volume], commands)
                self.assertFalse(any('prune' in command for command in commands))


if __name__ == '__main__':
    unittest.main()
