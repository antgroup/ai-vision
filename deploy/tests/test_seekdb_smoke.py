import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from native_memory_canary import MemoryCanaryError, NativeMemoryCanary
from seekdb_deploy_smoke import SECRET_VARS, Smoke, legacy_stop_evidence


class IdentityTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        values = {key: 'fixture-secret' for key in (*SECRET_VARS, 'SIGNOZ_USER_ROOT_PASSWORD')}
        values.update(NAMESPACE='fixture', BE_CONTAINER='fixture-backend', OB_CONTAINER='fixture-database')
        self.smoke = Smoke(Path(self.temp.name), Path(self.temp.name), values)
        self.org = '12345678-1234-1234-1234-123456789abc'
        self.mounts = [{'Destination': '/var/lib/seekdb', 'Name': 'aivforge-seekdb-data-fixture'}]
        self.smoke.run = self.run_fixture

    def run_fixture(self, label, command):
        output = 'startup warning\n' + self.org + '\n' if label.endswith('-org') else json.dumps(self.mounts)
        return subprocess.CompletedProcess(command, 0, output)

    def test_new_readonly_lifecycle_mount_does_not_change_data_identity(self):
        before = self.smoke.database_identity('before')
        self.mounts.append({'Destination': '/opt/aivision/seekdb.sh', 'Source': '/private/seekdb.sh', 'RW': False})
        self.assertEqual(before, self.smoke.database_identity('after'))

    def test_different_persistent_volume_is_rejected(self):
        self.mounts[0]['Name'] = 'another-volume'
        with self.assertRaisesRegex(AssertionError, 'original named volume'):
            self.smoke.database_identity('after')

    def test_missing_database_mount_is_rejected(self):
        self.mounts = []
        with self.assertRaisesRegex(AssertionError, 'original named volume'):
            self.smoke.database_identity('after')

    def test_oceanbase_requires_both_original_persistent_volumes(self):
        self.smoke.values['OB_BACKEND'] = 'oceanbase'
        self.mounts = [
            {'Destination': '/root/ob', 'Name': 'aivforge-ob-data-fixture'},
            {'Destination': '/root/.obd/cluster', 'Name': 'aivforge-ob-config-fixture'},
            {'Destination': '/opt/aivision/oceanbase.sh', 'Source': '/private/oceanbase.sh', 'RW': False},
        ]
        self.assertEqual(len(self.smoke.database_identity('before')['database_mounts']), 2)
        self.mounts[1]['Name'] = 'different-config-volume'
        with self.assertRaisesRegex(AssertionError, 'original named volume'):
            self.smoke.database_identity('after')

    def test_ambiguous_organization_is_rejected(self):
        self.org += '\n87654321-1234-1234-1234-123456789abc'
        with self.assertRaisesRegex(AssertionError, 'unambiguous UUID'):
            self.smoke.database_identity('after')


class StopEventsTest(unittest.TestCase):
    def events(self, signal='10', code='0', container='old-database'):
        return '\n'.join(json.dumps({'Action': action, 'Actor': {'ID': container, 'Attributes': attributes}})
                         for action, attributes in [('kill', {'signal': signal}), ('die', {'exitCode': code})])

    def test_exact_old_container_usr1_and_exit_zero_are_required(self):
        self.assertEqual(legacy_stop_evidence(self.events(), 'old-database')['signal'], 'SIGUSR1')
        for output in (self.events(signal='15'), self.events(code='137'), self.events(container='new-database'), ''):
            with self.assertRaises(AssertionError):
                legacy_stop_evidence(output, 'old-database')


class NativeMemoryReleaseGateTest(unittest.TestCase):
    def test_required_native_memory_rejects_legacy_source_fixture(self):
        script = Path(__file__).with_name('seekdb_deploy_smoke.py')
        result = subprocess.run(
            [sys.executable, str(script), '--out', 'unused', '--require-native-memory',
             '--source-baseline-legacy-health'],
            text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn('--require-native-memory cannot be combined with --source-baseline-legacy-health',
                      result.stderr)


class NativeMemoryCanaryTest(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.result = 200, {}
        self.memory = NativeMemoryCanary(self.request, space_id='workspace',
                                         agent_product='langfuse', agent_uuid='agent')
        self.memory.ingest('http://127.0.0.1:4318', 'fixture-token')

    def request(self, path, **options):
        self.requests.append((path, options))
        return self.result

    def test_pass_requires_real_query_shape_and_matching_typed_operation(self):
        path, options = self.requests[0]
        self.assertEqual(path, '/v1/traces')
        self.assertEqual(options['origin'], 'http://127.0.0.1:4318')
        self.assertEqual(options['headers']['Authorization'], 'Bearer fixture-token')
        group = options['body']['resourceSpans'][0]['scopeSpans'][0]
        self.assertEqual(group['scope']['name'], 'powercontext.server')
        operation, search = group['spans']
        self.assertEqual(operation['traceId'], search['traceId'])
        self.assertEqual(operation['spanId'], search['parentSpanId'])
        search_attrs = {attr['key']: attr['value'] for attr in search['attributes']}
        self.assertEqual(search_attrs['powercontext.memory.search.result_count'], {'intValue': '2'})
        self.result = 200, {'code': 0, 'data': {
            'evidence': {'source': 'recorded_memory_spans', 'complete': True,
                         'agentProduct': 'langfuse', 'agentUuid': 'agent'},
            'overview': {'calls': 1, 'searchCalls': 1, 'totalHits': 2},
            'servers': [{'serverId': 'powercontext', 'recentCalls': [{
                'traceId': operation['traceId'], 'spanId': operation['spanId'],
                'toolName': 'prepare_context', 'hits': 2,
            }]}],
        }}
        self.assertEqual(self.memory.read(), {'trace_id': operation['traceId'],
                                               'span_id': operation['spanId'], 'hits': 2})
        url = urlsplit(self.requests[-1][0])
        self.assertEqual(url.path, '/api/v2/spaces/workspace/labs/memory/observations')
        params = parse_qs(url.query)
        self.assertEqual(params['agent_product'], ['langfuse'])
        self.assertEqual(params['agent_uuid'], ['agent'])
        self.assertIn('fromTimestamp', params)
        self.assertIn('toTimestamp', params)

    def test_missing_memory_endpoint_is_not_treated_as_an_empty_window(self):
        self.result = 400, {'code': 3011, 'message': 'private upstream detail'}
        with self.assertRaisesRegex(MemoryCanaryError, r'query failed \(HTTP 400, API 3011\)$'):
            self.memory.read()
        self.assertEqual(len(self.requests), 2)

    def test_successful_empty_response_cannot_issue_a_receipt(self):
        self.result = 200, {'code': 0, 'data': {'overview': {'calls': 0}, 'servers': []}}
        with patch('native_memory_canary.time.monotonic', side_effect=[0, 121]):
            with self.assertRaisesRegex(MemoryCanaryError, 'never returned its complete operation'):
                self.memory.read()


class OpenShellGateTest(unittest.TestCase):
    """Controlled CLI/API/Docker boundaries; real runtime proof is a separate job."""

    def setUp(self):
        import base64
        import types
        import seekdb_deploy_smoke as deploy
        self.deploy = deploy
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name).resolve()
        self.out, self.runtime = root / 'out', root / 'runtime'
        self.out.mkdir()
        self.runtime.mkdir(mode=0o700)
        self.catalog = {'schema_version': 1, 'product_version': '0.1.0', 'provider_version': '0.1.6',
                        'protocol_version': 1, 'images': {'linux/amd64': {
                            key: f'example/{key}@sha256:{number:064x}' for number, key in
                            enumerate(('plugin', 'worker', 'gateway', 'runtime', 'supervisor'), 1)}}}
        manifest = {'plugins': {'openshell': self.catalog}}
        values = {key: 'fixture-' + key for key in (*SECRET_VARS, 'SIGNOZ_USER_ROOT_PASSWORD')}
        values.update(BE_CONTAINER='fixture-backend', AGENTLENS_CONTAINER='fixture-diagnosis',
                      AGENTLENS_PORT='8081', FE_PORT='8000', OB_CONTAINER='fixture-database',
                      OB_USER='root', AIVISION_RELEASE_MANIFEST_BASE64=base64.b64encode(json.dumps(manifest).encode()).decode())
        self.smoke = Smoke(self.out, self.runtime, values)
        self.tokens = ['aiv-sandbox-bootstrap-fixture', 'aiv-sandbox-report-fixture', 'aiv-sandbox-enroll-fixture']
        self.connection = '1' * 32
        self.session = '2' * 32
        self.backend = 'a' * 64
        self.plan = 'b' * 64
        self.gateway = 'c' * 64
        self.plugin = 'd' * 64
        self.calls, self.api_writes = [], []
        self.trial_status = 'succeeded'
        self.enhanced_metric_valid = True
        self.trace = types.SimpleNamespace(space='3' * 32, origin='http://127.0.0.1:8000',
                                           management=self.management, request=self.request)
        self.cli = self.runtime / 'released-aiv'
        self.cli.write_text('#!/usr/bin/env python3\nimport sys\nprint("fixture packaged executable")\n')
        self.cli.chmod(0o700)
        self.smoke.run = self.run_fixture

    def management(self, path, body=None, headers=None):
        if body is not None:
            self.api_writes.append((path, body))
        if path.endswith('/sandbox-bootstrap'):
            return {'available': True, 'product_version': '0.1.0', 'provider_version': '0.1.6'}
        if path.endswith('/sandbox-installations'):
            return {'session': {'uuid': self.session}, 'bootstrap_path': '/api/public/sandbox-installations/' + self.session,
                    **dict(zip(('bootstrap_token', 'report_token', 'enrollment_token'), self.tokens))}
        if path.endswith('/enable'):
            return {'connection_uuid': self.connection, 'binding': {'connection_uuid': self.connection, 'require_sandbox': False}}
        if path.endswith('/sandbox-binding'):
            return {'connection_uuid': None if not any(p.endswith('/enable') for p, _ in self.api_writes) else self.connection,
                    'require_sandbox': False}
        if path.endswith('/runtime'):
            import hashlib
            runtime = {'id': 'python-eval-v1', 'python_version': '3.11.14',
                       'packages': {name: '1.0' for name in ('pytest', 'numpy', 'pandas', 'pydantic', 'jsonschema', 'openpyxl')}}
            runtime['manifest_sha256'] = hashlib.sha256(json.dumps(runtime, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            return {'connection_uuid': self.connection, 'ready': True, 'image': self.catalog['images']['linux/amd64']['worker'],
                    'capabilities': ['custom_rule', 'python_eval_v1'],
                    'runtime_manifests': [{**runtime, 'image': self.catalog['images']['linux/amd64']['worker']}]}
        return {'uuid': self.session, 'status': 'ready', 'registration_state': 'saved',
                'connection_uuid': self.connection, 'bootstrap_plan_id': self.plan}

    def request(self, path, **kwargs):
        self.calls.append(('http', path, kwargs))
        if kwargs.get('body', {}).get('spec', {}).get('rule_runtime'):
            return 200, {'verdict': 'good', 'skipped': False,
                         'metrics': [{'name': 'enhanced_evaluation', 'verdict': 'good',
                                      'evidence': {'tests_passed': 1, 'csv_total': 30, 'xlsx_total': 30,
                                                   'verified': self.enhanced_metric_valid}}],
                         'execution': {'status': self.trial_status, 'evidence': {'backend': 'sandbox',
                                        'provider': 'openshell', 'connection_uuid': self.connection}}}
        return 200, {'verdict': 'good', 'skipped': False, 'metrics': [{'name': 'span_count', 'verdict': 'good', 'evidence': {'value': 13}}],
                     'execution': {'status': self.trial_status, 'code': '' if self.trial_status == 'succeeded' else 'sandbox_cleanup_pending',
                                   'evidence': {'backend': 'sandbox', 'provider': 'openshell', 'connection_uuid': self.connection}}}

    def run_fixture(self, label, command, **kwargs):
        import hashlib
        self.calls.append((label, command, kwargs))
        output = ''
        if label == 'openshell-target':
            output = self.backend + ' ' + self.backend[:12]
        elif label == 'openshell-setup':
            self.assertEqual(command[0], str(self.cli))
            self.assertEqual(command[1:4], ['plugin', 'setup', 'openshell'])
            self.assertTrue(all(token in self.smoke.private for token in self.tokens), 'capabilities must be private before command logging')
            key = hashlib.sha256(self.backend[:12].encode()).hexdigest()[:16]
            owned = Path(self.smoke.env['AIVISION_CONFIG_DIR']) / 'deployments' / key / 'plugins/openshell'
            (owned / 'installation').mkdir(parents=True)
            (owned / 'setup.json').write_text(json.dumps({'plan_id': self.plan, 'gateway_id': self.gateway,
                'host': {'backend_id': self.backend}, 'gateway_port': 28888}))
            (owned / 'installation/installation.json').write_text(json.dumps({'container_id': self.plugin, 'image': self.catalog['images']['linux/amd64']['plugin']}))
            (owned / 'plugin-token').write_text('aiv-sandbox-generated-api-key')
            output = ' '.join(self.tokens + ['aiv-sandbox-generated-api-key'])
        elif label == 'openshell-leases':
            output = json.dumps({'count': 1, 'closed': 1, 'cleanup_verified': True})
        elif label == 'openshell-gateway-inventory':
            output = json.dumps({'sandboxes': 0, 'templates': 0, 'mtls': True})
        elif label == 'openshell-status':
            output = json.dumps({'installed': True, 'health': {'ready': True, 'active_leases': 0}})
        elif label == 'openshell-gateway-owner':
            key = hashlib.sha256(self.backend[:12].encode()).hexdigest()[:16]
            output = json.dumps({'Id': self.gateway, 'Config': {'Image': self.catalog['images']['linux/amd64']['gateway'],
                'Labels': {'io.aivision.sandbox.bootstrap': key, 'io.aivision.sandbox.plan': self.plan, 'io.aivision.sandbox.role': 'gateway'}}})
        elif label == 'openshell-gateway-stopped':
            output = json.dumps({'Running': False, 'ExitCode': 0, 'OOMKilled': False})
        elif label == 'openshell-gateway-removed':
            return subprocess.CompletedProcess(command, 1, '')
        (self.out / (label + '.log')).write_text(self.smoke.sanitize(output))
        return subprocess.CompletedProcess(command, 0, output)

    def test_gate_runs_packaged_setup_explicit_enable_trial_and_safe_cleanup(self):
        gate = self.deploy.OpenShellCanary(self.smoke, self.trace, self.cli, 'linux/amd64')
        result = gate.run()
        self.assertEqual(result['execution']['backend'], 'sandbox')
        self.assertEqual(result['execution']['span_count'], 13)
        self.assertEqual(result['enhanced_runtime']['id'], 'python-eval-v1')
        enhanced = [kwargs['body']['spec'] for label, _, kwargs in self.calls
                    if label == 'http' and kwargs.get('body', {}).get('spec', {}).get('rule_runtime')]
        self.assertEqual(len(enhanced), 1)
        self.assertEqual(enhanced[0]['materials']['files'][0]['path'], 'tests/test_candidate.py')
        self.assertEqual([body for path, body in self.api_writes if path.endswith('/sandbox-installations')], [{'operation': 'bootstrap'}])
        self.assertEqual([body for path, body in self.api_writes if path.endswith('/enable')], [{}])
        self.assertEqual(gate.cleanup(), [])

        setup = next(command for label, command, _ in self.calls if label == 'openshell-setup')
        self.assertNotIn('--image', setup)
        self.assertNotIn('--gateway-endpoint', setup)
        self.assertFalse(any(label == 'build-cli' for label, *_ in self.calls))
        logs = '\n'.join(path.read_text() for path in self.out.glob('*.log'))
        for secret in self.tokens + ['aiv-sandbox-generated-api-key']:
            self.assertNotIn(secret, logs)
        self.assertTrue(any(label == 'openshell-remove' and command[1:4] == ['plugin', 'remove', 'openshell'] for label, command, _ in self.calls))
        self.assertFalse(any('rm' in command and ('-f' in command or '-v' in command) for label, command, _ in self.calls if label != 'http'))

    def test_enhanced_evidence_failure_retains_runtime(self):
        self.enhanced_metric_valid = False
        gate = self.deploy.OpenShellCanary(self.smoke, self.trace, self.cli, 'linux/amd64')
        with self.assertRaisesRegex(AssertionError, 'enhanced'):
            gate.run()
        self.assertTrue(gate.preserve_runtime)
        self.assertTrue(gate.cleanup())
        self.assertFalse(any(label == 'openshell-remove' for label, *_ in self.calls))

    def test_failed_trial_retains_runtime_and_never_removes_plugin_or_gateway(self):
        self.trial_status = 'failed'
        gate = self.deploy.OpenShellCanary(self.smoke, self.trace, self.cli, 'linux/amd64')
        with self.assertRaisesRegex(AssertionError, 'Rule Trial'):
            gate.run()
        self.assertTrue(gate.cleanup())
        self.assertTrue(gate.preserve_runtime)
        self.assertFalse(any(label in ('openshell-remove', 'openshell-gateway-stop', 'openshell-gateway-remove') for label, *_ in self.calls))
        self.assertTrue(gate.config_root.exists())

    def test_manifest_without_plugin_keeps_existing_smoke_path(self):
        import base64
        self.smoke.values['AIVISION_RELEASE_MANIFEST_BASE64'] = base64.b64encode(json.dumps({'product_version': '0.1.0'}).encode()).decode()
        self.assertIsNone(self.deploy.openshell_catalog(self.smoke.values))

    def test_subprocess_evidence_redacts_capabilities_and_generated_plugin_key(self):
        import contextlib
        import io
        import sys
        self.smoke.private.extend(self.tokens)
        secret_line = ' '.join(self.tokens + ['aiv-sandbox-fresh-generated-key'])
        with contextlib.redirect_stdout(io.StringIO()):
            Smoke.run(self.smoke, 'real-child-redaction', [sys.executable, '-c', 'print(' + repr(secret_line) + ')'])
        evidence = (self.out / 'real-child-redaction.log').read_text()
        self.assertEqual(evidence.count('[REDACTED]'), 4)
        for secret in secret_line.split():
            self.assertNotIn(secret, evidence)

    def test_changed_gateway_identity_blocks_all_cleanup_mutations(self):
        gate = self.deploy.OpenShellCanary(self.smoke, self.trace, self.cli, 'linux/amd64')
        gate.run()
        original = self.run_fixture

        def changed(label, command, **kwargs):
            result = original(label, command, **kwargs)
            if label == 'openshell-gateway-owner':
                payload = json.loads(result.stdout)
                payload['Config']['Labels']['io.aivision.sandbox.plan'] = 'f' * 64
                return subprocess.CompletedProcess(command, 0, json.dumps(payload))
            return result

        self.smoke.run = changed
        self.assertTrue(gate.cleanup())
        self.assertTrue(gate.preserve_runtime)
        self.assertFalse(any(label in ('openshell-remove', 'openshell-gateway-stop', 'openshell-gateway-remove') for label, *_ in self.calls))

    def test_main_hooks_after_recast_and_preserves_stack_after_failed_trial(self):
        import base64
        import contextlib
        import io
        import sys
        import types
        from unittest.mock import patch
        deploy = self.deploy
        commands, gate_calls = [], []
        metadata = base64.b64encode(json.dumps({'plugins': {'openshell': self.catalog}}).encode()).decode()
        images = {key: f'example/{key}@sha256:{index:064x}' for index, key in enumerate(deploy.IMAGE_VARS, 1)}

        def run(smoke, label, command, **kwargs):
            commands.append((label, command))
            if label.startswith('check-absent-'):
                return subprocess.CompletedProcess(command, 1, '')
            if label in ('cast', 'recast'):
                saved = smoke.runtime / 'work' / smoke.values['NAMESPACE'] / 'state/vars.json'
                saved.parent.mkdir(parents=True, exist_ok=True)
                saved.write_text(json.dumps(smoke.values))
                saved.chmod(0o600)
            output = 'linux/amd64' if label == 'docker-info' else '[]' if label == 'initial-database-entrypoint' else ''
            return subprocess.CompletedProcess(command, 0, output)

        class FailedGate:
            preserve_runtime = True
            def __init__(inner, smoke, trace, cli, platform):
                gate_calls.append((smoke, cli, platform))
                self.assertIn('recast', [label for label, _ in commands])
                self.assertEqual(cli, self.cli)
            def run(inner):
                raise AssertionError('Rule Trial failed closed')
            def cleanup(inner):
                return ['OpenShell pending cleanup; retained']

        evidence = self.runtime / 'main-evidence'
        argv = [deploy.__file__, '--out', str(evidence), '--runtime-base', str(self.runtime),
                '--aiv', str(self.cli), '--require-pinned-images']
        with patch.object(sys, 'argv', argv), patch.object(deploy, 'ports', return_value={
                key: str(20000 + index) for index, key in enumerate(deploy.PORT_VARS)}), patch.dict(os.environ, {
                'AIVISION_RELEASE_MANIFEST_BASE64': metadata,
                'AIVISION_SEEKDB_SMOKE_IMAGES_JSON': json.dumps(images)}), \
             patch.object(deploy.Smoke, 'run', new=run), \
             patch.object(deploy.Smoke, 'images', return_value={'platforms': ['linux/amd64'] * 6}), \
             patch.object(deploy.Smoke, 'healthy', return_value=True), \
             patch.object(deploy.Smoke, 'sql', return_value={'canary': True}), \
             patch.object(deploy.Smoke, 'database_identity', return_value={'identity': 'same'}), \
             patch.object(deploy.Smoke, 'cleanup', return_value=[]) as cleanup, \
             patch.object(deploy, 'TraceCanary', return_value=types.SimpleNamespace(
                 ingest=lambda: None, read=lambda: {'trace': 'fixture'}, version=lambda: {'matched': True})), \
             patch.object(deploy, 'OpenShellCanary', FailedGate), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(deploy.main(), 1)
        summary = json.loads((evidence / 'summary.json').read_text())
        self.assertEqual(summary['status'], 'failed')
        self.assertTrue(Path(summary['private_runtime_retained']).is_dir())
        self.assertEqual(cleanup.call_count, 0)
        self.assertEqual(len(gate_calls), 1)
        self.assertFalse(any(label == 'build-cli' for label, _ in commands))

    def test_setup_restores_existing_cli_config_environment(self):
        self.smoke.env['AIVISION_CONFIG_DIR'] = '/original/private-cli-profile'
        gate = self.deploy.OpenShellCanary(self.smoke, self.trace, self.cli, 'linux/amd64')
        gate.run()
        self.assertEqual(self.smoke.env['AIVISION_CONFIG_DIR'], '/original/private-cli-profile')
