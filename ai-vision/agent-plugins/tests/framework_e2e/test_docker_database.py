"""Database helper cleanup contracts; no Docker or SQL server is started."""
import argparse
from contextlib import redirect_stdout
import io
import json
import os
import signal
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

import docker_database
from importlib.util import module_from_spec, spec_from_file_location
_state_spec = spec_from_file_location('owned_process_state',
    Path(__file__).resolve().parents[1] / 'native_agent_e2e/_process_test_helpers.py')
_state = module_from_spec(_state_spec)
_state_spec.loader.exec_module(_state)
_assert_process_stopped = _state.assert_posix_process_stopped


class DockerDatabaseCleanupTests(unittest.TestCase):
    def run_helper(self, *, existing=True, discovery_error=False,
                   removal_error=False, database_error=False, backend_tests=False,
                   backend_exit=0, backend_timeout=False, backend_count=8,
                   sql_errno=None, backend='oceanbase', uninitialized_root=False,
                   server_identity=('4.3.5.2', 'OceanBase'),
                   empty_probe_errno=None, bootstrap_strict_errno=None,
                   empty_after_bootstrap=False, image_platform='linux/amd64',
                   actual_platform=None, manifest_platform=None, containerd=False):
        commands = []
        backend_options = []
        events = []
        connect_options = []
        sql_calls = []
        password_initialized = False
        payload = json.dumps({'tenant': 'orion', 'retention_days': 14, 'storage_gib': 140},
                             sort_keys=True)

        class Cursor:
            def execute(self, query, values=None):
                nonlocal password_initialized
                self.query = query
                events.append(('sql', query))
                sql_calls.append((query, values))
                if query.startswith('ALTER USER'):
                    password_initialized = True

            def fetchone(self):
                if self.query.startswith('SELECT version'):
                    return server_identity
                return (18, payload)

            def close(self):
                pass

        class Connection:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def cursor(self):
                return Cursor()

            def commit(self):
                pass

            def rollback(self):
                pass

            def close(self):
                pass

        mysql = types.ModuleType('mysql')
        connector = types.ModuleType('mysql.connector')
        connector.Error = type('ConnectorError', (Exception,), {})

        def connect(**options):
            connect_options.append(options.copy())
            errno = sql_errno
            if uninitialized_root:
                if options['password']:
                    errno = bootstrap_strict_errno if password_initialized else 1045
                elif not password_initialized:
                    errno = empty_probe_errno
                elif not empty_after_bootstrap:
                    errno = 1045
            if errno is not None:
                error = connector.Error('SQL not ready: synthetic-private-password')
                error.errno = errno
                raise error
            if database_error:
                raise RuntimeError('original SQL failure')
            return Connection()

        connector.connect = connect
        mysql.connector = connector

        def run(command, **options):
            commands.append(command)
            if command[0] != 'docker':
                events.append(('backend', command))
                backend_options.append(options)
                output = f'Ran {backend_count} tests in 0.1s\n' + ('OK\n' if not backend_exit else 'FAILED (failures=1)\n')
                output += 'DB_PASSWORD=synthetic-private-password\n'
                if backend_timeout:
                    raise subprocess.TimeoutExpired(command, options['timeout'],
                                                    output=output.encode(), stderr=b'timed out')
                return subprocess.CompletedProcess(command, backend_exit, '', output)
            if command[:3] == ('docker', 'container', 'inspect'):
                container = {'Image': 'sha256:' + 'c' * 64}
                if manifest_platform:
                    os_name, arch = manifest_platform.split('/')
                    container['ImageManifestDescriptor'] = {
                        'mediaType': 'application/vnd.oci.image.manifest.v1+json',
                        'digest': 'sha256:' + 'd' * 64,
                        'platform': {'os': os_name, 'architecture': arch},
                    }
                return subprocess.CompletedProcess(command, 0, json.dumps([container]), '')
            if command[:2] == ('docker', 'info'):
                status = [['driver-type', 'io.containerd.snapshotter.v1']] if containerd else None
                return subprocess.CompletedProcess(command, 0, json.dumps(status), '')
            if command[:3] == ('docker', 'image', 'inspect'):
                self.assertEqual(command[-1], 'sha256:' + 'c' * 64)
                return subprocess.CompletedProcess(command, 0, actual_platform or image_platform, '')
            if command[:4] == ('docker', 'inspect', '--format', '{{.Image}}'):
                return subprocess.CompletedProcess(command, 0, 'sha256:' + 'c' * 64, '')
            if command[:2] in (('docker', 'inspect'), ('docker', 'ps')):
                if discovery_error:
                    return subprocess.CompletedProcess(command, 1, '', 'Cannot connect to the Docker daemon')
                # An absent object causes inspect to fail; successful ps returns no IDs.
                if not existing and command[1] == 'inspect':
                    return subprocess.CompletedProcess(command, 1, '', 'Error: No such object')
                if command[1] == 'inspect':
                    return subprocess.CompletedProcess(command, 0, json.dumps({
                        'Status': 'running', 'Running': True, 'OOMKilled': False,
                        'ExitCode': 0, 'Error': 'synthetic-private-password'}), '')
                return subprocess.CompletedProcess(command, 0, 'owned-container\n' if existing else '', '')
            if command[:2] == ('docker', 'rm') and removal_error:
                return subprocess.CompletedProcess(command, 1, '', 'Removal failed')
            stdout = '127.0.0.1:31900\n' if command[:2] == ('docker', 'port') else ''
            return subprocess.CompletedProcess(command, 0, stdout, '')

        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'evidence'
            args = argparse.Namespace(backend=backend, out=out, platform=image_platform)
            repo = Path(directory) / 'checkout'
            if backend_tests:
                backend_dir = repo / 'ai-vision/aivision-py-be'
                backend_dir.mkdir(parents=True)
                (backend_dir / 'manage.py').touch()
                args.backend_tests, args.python = repo, sys.executable
            images = json.dumps({'oceanbase': 'oceanbase/oceanbase-ce@sha256:' + 'a' * 64,
                                 'seekdb': 'oceanbase/seekdb@sha256:' + 'b' * 64})
            with patch.dict(sys.modules, {'mysql': mysql, 'mysql.connector': connector}), \
                    patch.object(argparse.ArgumentParser, 'parse_args', return_value=args), \
                    patch.object(docker_database, '_run_owned', side_effect=run), \
                    patch.object(docker_database.secrets, 'token_hex', return_value='synthetic-private-password'), \
                    patch.object(docker_database.time, 'monotonic', side_effect=[0, 601]), \
                    patch.dict(docker_database.os.environ, {'AIVISION_E2E_DB_IMAGES_JSON': images}), \
                    redirect_stdout(io.StringIO()):
                exit_code = docker_database.main()
            summary = json.loads((out / 'summary.json').read_text())
            summary['_test_artifact_text'] = (out / 'summary.json').read_text()
            summary['_test_connect_options'] = connect_options
            summary['_test_sql_calls'] = sql_calls
            if backend_tests:
                summary['_test_backend_options'] = backend_options
                summary['_test_events'] = events
                summary['_test_backend_log'] = ((out / 'backend-tests.log').read_text()
                                                if (out / 'backend-tests.log').exists() else None)
        return exit_code, summary, commands

    def test_both_platforms_reach_sql_and_backend_tests_with_matching_pull_and_run(self):
        for backend in ('oceanbase', 'seekdb'):
            for image_platform in ('linux/amd64', 'linux/arm64'):
                with self.subTest(backend=backend, platform=image_platform):
                    code, summary, commands = self.run_helper(backend=backend,
                        backend_tests=True, image_platform=image_platform)
                    self.assertEqual(code, 0, summary)
                    self.assertEqual(summary['platform'], image_platform)
                    self.assertEqual(summary['actual_image_platform'], image_platform)
                    self.assertEqual(summary['backend_tests']['status'], 'passed')
                    for action in ('pull', 'run'):
                        command = next(c for c in commands if c[:2] == ('docker', action))
                        self.assertEqual(command[command.index('--platform') + 1], image_platform)

    def test_container_manifest_wins_over_image_index_default(self):
        code, summary, commands = self.run_helper(
            image_platform='linux/amd64', actual_platform='linux/arm64',
            manifest_platform='linux/amd64', containerd=True, backend_tests=True)
        self.assertEqual(code, 0, summary)
        self.assertEqual(summary['actual_image_platform'], 'linux/amd64')
        self.assertEqual(summary['backend_tests']['status'], 'passed')
        self.assertFalse(any(c[:3] == ('docker', 'image', 'inspect') for c in commands))

    def test_wrong_manifest_architecture_fails_before_sql_and_removes_container(self):
        code, summary, commands = self.run_helper(image_platform='linux/arm64',
            actual_platform='linux/arm64', manifest_platform='linux/amd64',
            containerd=True, backend_tests=True)
        self.assertEqual(code, 1)
        self.assertEqual(summary['actual_image_platform'], 'linux/amd64')
        self.assertIn('platform', summary['error']['message'])
        self.assertTrue(summary['cleanup']['container_removed'])
        self.assertEqual(summary['_test_connect_options'], [])
        self.assertEqual(summary['_test_backend_options'], [])

    def test_containerd_without_manifest_cannot_use_index_default(self):
        code, summary, commands = self.run_helper(containerd=True)
        self.assertEqual(code, 1)
        self.assertIn('manifest', summary['error']['message'])
        self.assertTrue(summary['cleanup']['container_removed'])
        self.assertEqual(summary['_test_connect_options'], [])
        self.assertFalse(any(c[:3] == ('docker', 'image', 'inspect') for c in commands))

    def test_sql_timeout_retains_last_connector_error_and_sanitized_container_state(self):
        exit_code, summary, commands = self.run_helper(sql_errno=2003)
        self.assertEqual(exit_code, 1)
        self.assertEqual(summary['error']['type'], 'TimeoutError')
        self.assertEqual(summary.get('diagnostics', {}).get('last_sql_error', {}).get('errno'), 2003)
        state = summary.get('diagnostics', {}).get('container_state', {})
        self.assertEqual(state.get('Status'), 'running')
        self.assertEqual(state.get('OOMKilled'), False)
        self.assertEqual(state.get('ExitCode'), 0)
        self.assertNotIn('synthetic-private-password', summary['_test_artifact_text'])
        self.assertIn('[REDACTED]', state.get('Error', ''))
        self.assertTrue(summary['cleanup']['container_removed'])
        inspected = next(i for i, command in enumerate(commands) if command[:2] == ('docker', 'inspect'))
        removed = next(i for i, command in enumerate(commands) if command[:2] == ('docker', 'rm'))
        self.assertLess(inspected, removed, 'diagnostic state must be captured before removing the owned container')

    def test_owned_seekdb_initializes_generated_password_then_rejects_fresh_empty_root(self):
        exit_code, summary, commands = self.run_helper(backend='seekdb', uninitialized_root=True)
        self.assertEqual(exit_code, 0, summary)
        self.assertTrue(summary['checks'].get('credential_bootstrap'))
        self.assertTrue(summary['checks'].get('empty_password_rejected'))
        self.assertTrue(summary['checks']['actual_server_identity'])
        self.assertEqual([options['password'] for options in summary['_test_connect_options']],
                         ['synthetic-private-password', '', 'synthetic-private-password', '',
                          'synthetic-private-password'])
        self.assertTrue(all(options['host'] == '127.0.0.1' and options['port'] == 31900
                            and options['user'] == 'root' for options in summary['_test_connect_options']))
        sql = summary['_test_sql_calls']
        self.assertEqual([(query, values) for query, values in sql if query.startswith('ALTER USER')],
                         [("ALTER USER 'root' IDENTIFIED BY %s", ('synthetic-private-password',))])
        self.assertTrue(sql[0][0].startswith('SELECT version'), 'verify identity before changing credentials')
        self.assertNotIn('synthetic-private-password', summary['_test_artifact_text'])
        self.assertTrue(summary['cleanup']['container_removed'])
        created = next(command for command in commands if command[:2] == ('docker', 'run'))
        self.assertIn('127.0.0.1::2881', created)

    def test_owned_databases_raise_nofile_limit_without_disabling_startup_checks(self):
        for backend in ('oceanbase', 'seekdb'):
            with self.subTest(backend=backend):
                exit_code, summary, commands = self.run_helper(backend=backend)
                self.assertEqual(exit_code, 0)
                created = next(command for command in commands if command[:2] == ('docker', 'run'))
                pairs = list(zip(created, created[1:]))
                self.assertIn(('--ulimit', 'nofile=65536:65536'), pairs)
                self.assertLess(created.index('--ulimit'), len(created) - 1)
                self.assertEqual(created[-1], summary['image'])
                self.assertFalse(any('SKIP' in argument for argument in created))

    def test_oceanbase_access_denied_never_probes_empty_root(self):
        exit_code, summary, _ = self.run_helper(uninitialized_root=True)
        self.assertEqual(exit_code, 1)
        self.assertEqual(summary['error']['type'], 'TimeoutError')
        self.assertTrue(all(options['password'] for options in summary['_test_connect_options']))
        self.assertFalse(any(query.startswith('ALTER USER') for query, _ in summary['_test_sql_calls']))

    def test_seekdb_non_authentication_error_never_probes_empty_root(self):
        exit_code, summary, _ = self.run_helper(backend='seekdb', sql_errno=2003)
        self.assertEqual(exit_code, 1)
        self.assertEqual(summary['error']['type'], 'TimeoutError')
        self.assertTrue(all(options['password'] for options in summary['_test_connect_options']))
        self.assertFalse(any(query.startswith('ALTER USER') for query, _ in summary['_test_sql_calls']))

    def test_seekdb_wrong_server_identity_cannot_change_credentials(self):
        exit_code, summary, _ = self.run_helper(backend='seekdb', uninitialized_root=True,
                                              server_identity=('8.0.1', 'MySQL Community'))
        self.assertEqual(exit_code, 1)
        self.assertEqual(summary['error']['type'], 'AssertionError')
        self.assertFalse(any(query.startswith('ALTER USER') for query, _ in summary['_test_sql_calls']))
        self.assertTrue(summary['cleanup']['container_removed'])

    def test_seekdb_rejected_empty_root_probe_cannot_change_credentials(self):
        exit_code, summary, _ = self.run_helper(backend='seekdb', uninitialized_root=True,
                                              empty_probe_errno=1045)
        self.assertEqual(exit_code, 1)
        self.assertEqual(summary['error']['type'], 'ConnectorError')
        self.assertFalse(any(query.startswith('ALTER USER') for query, _ in summary['_test_sql_calls']))
        self.assertNotIn('sql_ready', summary['checks'])

    def test_seekdb_bootstrap_requires_a_new_strict_password_connection(self):
        exit_code, summary, _ = self.run_helper(backend='seekdb', uninitialized_root=True,
                                              bootstrap_strict_errno=1045)
        self.assertEqual(exit_code, 1)
        self.assertEqual(summary['error']['type'], 'ConnectorError')
        self.assertTrue(any(query.startswith('ALTER USER') for query, _ in summary['_test_sql_calls']))
        self.assertFalse(any(query.startswith('CREATE DATABASE') for query, _ in summary['_test_sql_calls']))
        self.assertNotIn('sql_ready', summary['checks'])

    def test_seekdb_still_accepted_empty_password_cannot_satisfy_acceptance(self):
        exit_code, summary, _ = self.run_helper(backend='seekdb', uninitialized_root=True,
                                              empty_after_bootstrap=True)
        self.assertEqual(exit_code, 1)
        self.assertEqual(summary['error']['type'], 'AssertionError')
        self.assertNotIn('sql_ready', summary['checks'])
        self.assertFalse(any(query.startswith('CREATE DATABASE') for query, _ in summary['_test_sql_calls']))
        self.assertTrue(summary['cleanup']['container_removed'])

    def test_unavailable_daemon_still_attempts_removal_and_cannot_pass(self):
        exit_code, summary, commands = self.run_helper(discovery_error=True)
        self.assertEqual(exit_code, 1)
        self.assertEqual(summary['status'], 'failed')
        self.assertIn('inspect_error', summary['cleanup'])
        self.assertTrue(any(command[:2] == ('docker', 'rm') for command in commands))
        # A later successful removal does not erase the failed discovery evidence.
        self.assertTrue(summary['cleanup']['container_removed'])

    def test_confirmed_absence_does_not_attempt_removal(self):
        exit_code, summary, commands = self.run_helper(existing=False)
        self.assertEqual(exit_code, 0)
        self.assertEqual(summary['status'], 'passed')
        self.assertFalse(any(command[:2] == ('docker', 'rm') for command in commands))

    def test_removal_failure_makes_successful_database_test_fail(self):
        exit_code, summary, _ = self.run_helper(removal_error=True)
        self.assertEqual(exit_code, 1)
        self.assertEqual(summary['status'], 'failed')
        self.assertIn('remove_error', summary['cleanup'])
        self.assertNotIn('container_removed', summary['cleanup'])

    def test_cleanup_failure_preserves_original_database_failure(self):
        exit_code, summary, commands = self.run_helper(database_error=True, discovery_error=True)
        self.assertEqual(exit_code, 1)
        self.assertEqual(summary['error']['message'], 'original SQL failure')
        self.assertIn('inspect_error', summary['cleanup'])
        self.assertTrue(any(command[:2] == ('docker', 'rm') for command in commands))

    def test_existing_container_is_removed_after_successful_database_test(self):
        exit_code, summary, commands = self.run_helper()
        self.assertEqual(exit_code, 0)
        self.assertTrue(summary['cleanup']['container_removed'])
        self.assertTrue(any(command[:2] == ('docker', 'rm') for command in commands))

    def test_backend_runs_full_django_suite_on_this_owned_database_after_sql_checks(self):
        with patch.dict(docker_database.os.environ, {
                'DB_HOST': 'unrelated-server', 'DB_NAME': 'unrelated-database',
                'DB_ENGINE': 'django.db.backends.sqlite3', 'USE_SQLITE_FOR_TESTS': 'true',
                'NO_PROXY': 'unrelated.internal', 'no_proxy': 'unrelated.internal',
                'DJANGO_SETTINGS_MODULE': 'unrelated.settings', 'OPENAI_API_KEY': 'unrelated-private-key'}):
            exit_code, summary, commands = self.run_helper(backend_tests=True)
        self.assertEqual(exit_code, 0)
        self.assertEqual(summary['checks'].get('backend_tests'), True)
        self.assertEqual(summary.get('backend_tests', {}).get('tests_run'), 8)
        calls = [command for command in commands if command[0] != 'docker']
        self.assertEqual(len(calls), 1, 'optional backend acceptance must execute the real test subprocess')
        self.assertEqual(calls[0][0], sys.executable)
        self.assertEqual(calls[0][-4:], ['manage.py', 'test', 'apps', '--noinput'])
        options = summary['_test_backend_options'][0]
        self.assertEqual(options['timeout'], 900)
        self.assertEqual(options['cwd'].name, 'aivision-py-be')
        env = options['env']
        self.assertEqual({key: env[key] for key in ('AIVISION_ENV', 'USE_SQLITE_FOR_TESTS', 'DB_ENGINE',
                                                   'DB_HOST', 'DB_PORT', 'DB_USER', 'DB_PASSWORD')}, {
            'AIVISION_ENV': 'test', 'USE_SQLITE_FOR_TESTS': 'false',
            'DB_ENGINE': 'aivision_backend.db_backends.mysql_connector_oceanbase',
            'DB_HOST': '127.0.0.1', 'DB_PORT': '31900', 'DB_USER': 'root@test',
            'DB_PASSWORD': 'synthetic-private-password'})
        self.assertTrue(env['DB_NAME'].startswith('framework_e2e_'))
        self.assertNotEqual(env['DB_NAME'], 'unrelated-database')
        events = summary['_test_events']
        created = next(query.removeprefix('CREATE DATABASE ') for kind, query in events
                       if kind == 'sql' and query.startswith('CREATE DATABASE '))
        self.assertEqual(env['DB_NAME'], created)
        reread = next(i for i, (kind, value) in enumerate(events)
                      if kind == 'sql' and value.startswith('SELECT tokens, payload'))
        execution = next(i for i, (kind, value) in enumerate(events) if kind == 'backend')
        self.assertLess(reread, execution, 'backend tests must follow the real SQL identity/transaction readback')
        self.assertNotIn('DJANGO_SETTINGS_MODULE', env)
        self.assertNotIn('OPENAI_API_KEY', env)
        self.assertEqual(env['NO_PROXY'], '127.0.0.1,localhost')
        self.assertEqual(env['no_proxy'], '127.0.0.1,localhost')
        self.assertNotIn('synthetic-private-password', summary['_test_backend_log'])
        self.assertIn('[REDACTED]', summary['_test_backend_log'])
        self.assertTrue(summary['cleanup']['container_removed'])

    def test_backend_failure_preserves_successful_sql_checks_and_removes_owned_container(self):
        exit_code, summary, _ = self.run_helper(backend_tests=True, backend_exit=1)
        self.assertEqual(exit_code, 1)
        self.assertEqual(summary['status'], 'failed')
        self.assertFalse(summary['checks']['backend_tests'])
        self.assertEqual(summary['backend_tests']['exit_code'], 1)
        self.assertTrue(all(summary['checks'][key] for key in (
            'sql_ready', 'actual_server_identity', 'commit_visible_to_new_connection', 'rollback_not_persisted')))
        self.assertIn('FAILED (failures=1)', summary['_test_backend_log'])
        self.assertNotIn('synthetic-private-password', summary['_test_backend_log'])
        self.assertTrue(summary['cleanup']['container_removed'])

    def test_backend_timeout_retains_sanitized_partial_log_and_fails_after_cleanup(self):
        exit_code, summary, _ = self.run_helper(backend_tests=True, backend_timeout=True)
        self.assertEqual(exit_code, 1)
        self.assertEqual(summary['backend_tests']['error']['type'], 'TimeoutExpired')
        self.assertIn('timed out', summary['_test_backend_log'])
        self.assertNotIn('synthetic-private-password', summary['_test_backend_log'])
        self.assertTrue(summary['cleanup']['container_removed'])

    def test_backend_empty_suite_cannot_satisfy_acceptance(self):
        exit_code, summary, _ = self.run_helper(backend_tests=True, backend_count=0)
        self.assertEqual(exit_code, 1)
        self.assertFalse(summary['checks']['backend_tests'])
        self.assertEqual(summary['backend_tests']['tests_run'], 0)

    @unittest.skipUnless(os.name == 'posix', 'database runners support Linux/macOS')
    def test_backend_timeout_stops_descendants_and_retains_redacted_partial_log(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / 'checkout'
            backend = repo / 'ai-vision/aivision-py-be'
            backend.mkdir(parents=True)
            (backend / 'environ.py').write_text('class Env: pass\n')
            receipt = root / 'tree.json'
            (backend / 'manage.py').write_text(
                'import json,os,subprocess,sys,time\nfrom pathlib import Path\n'
                "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(10)'])\n"
                f'Path({str(receipt)!r}).write_text(json.dumps([os.getpid(),child.pid]))\n'
                'print("partial owned-private",flush=True)\ntime.sleep(10)\n')
            out = root / 'evidence'
            out.mkdir()
            options = {'host': '127.0.0.1', 'port': 31900, 'user': 'root', 'password': 'owned-private'}
            results = []
            thread = threading.Thread(target=lambda: results.append(docker_database.run_backend_tests(
                repo, sys.executable, options, 'owned', out)), daemon=True)
            with patch.object(docker_database, 'BACKEND_TEST_TIMEOUT_SECONDS', .5, create=True):
                try:
                    thread.start()
                    thread.join(timeout=2)
                    self.assertFalse(thread.is_alive(), 'database child timeout must bound the entire process tree')
                    self.assertEqual(results[0]['status'], 'failed')
                    self.assertEqual(results[0]['error']['type'], 'TimeoutExpired')
                    self.assertEqual(results[0]['exit_code'], 124)
                    self.assertTrue(receipt.exists())
                    for pid in json.loads(receipt.read_text()):
                        _assert_process_stopped(pid)
                    log = (out / 'backend-tests.log').read_text()
                    self.assertIn('partial [REDACTED]', log)
                    self.assertNotIn('owned-private', log)
                finally:
                    if receipt.exists():
                        for pid in json.loads(receipt.read_text()):
                            try:
                                os.kill(pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                    thread.join(timeout=5)

    def test_backend_entrypoint_never_loads_dotenv_and_executes_manage_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory) / 'checkout'
            backend = repo / 'ai-vision/aivision-py-be'
            backend.mkdir(parents=True)
            # A real subprocess with a narrow environment-reader stand-in; no
            # Docker/SQL service is needed to prove the runner's process boundary.
            (backend / 'environ.py').write_text(
                'class Env:\n'
                '    @staticmethod\n'
                '    def read_env(*args, **kwargs):\n'
                '        raise RuntimeError("unexpected local dotenv read")\n')
            (backend / 'manage.py').write_text(
                'import environ, os, sys\n'
                'environ.Env.read_env(".env")\n'
                'assert sys.argv == ["manage.py", "test", "owned.quick", "--noinput"]\n'
                'assert os.environ["AIVISION_QUICK_TEST_DB_BACKEND"] == "oceanbase"\n'
                'assert os.environ["DB_NAME"] == "framework_e2e_owned"\n'
                'assert os.environ["DB_PORT"] == "31900"\n'
                'print("Ran 3 tests in 0.01s\\nOK")\n')
            options = {'host': '127.0.0.1', 'port': 31900, 'user': 'root', 'password': 'owned-private'}
            out = Path(directory) / 'evidence'
            out.mkdir()
            self.assertTrue(hasattr(docker_database, 'run_backend_tests'),
                            'backend subprocess must have an isolated environment entrypoint')
            result = docker_database.run_backend_tests(repo, sys.executable, options,
                                                      'framework_e2e_owned', out, backend_name='oceanbase',
                                                      test_labels=('owned.quick',))
            self.assertEqual(result['status'], 'passed', result)
            self.assertEqual(result['tests_run'], 3)

    def test_backend_logging_uses_a_created_private_directory_and_removes_raw_files(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory) / 'checkout'
            backend = repo / 'ai-vision/aivision-py-be'
            backend.mkdir(parents=True)
            (backend / 'environ.py').write_text('class Env:\n    read_env = staticmethod(lambda *args, **kwargs: None)\n')
            # Exercise real file handlers in the actual child process. Missing
            # AIVISION_LOG_DIR or directory creation must fail before any tests.
            (backend / 'manage.py').write_text(
                'import json, logging, logging.handlers, os\n'
                'from pathlib import Path\n'
                'root = Path(os.environ["AIVISION_LOG_DIR"])\n'
                'assert root.is_dir()\n'
                'for name in ("aivision.log", "error.log"):\n'
                '    handler = logging.handlers.RotatingFileHandler(root / name)\n'
                '    handler.handle(logging.LogRecord("CI", 40, __file__, 1, "private runtime log", (), None))\n'
                '    handler.close()\n'
                '    assert (root / name).read_text().strip() == "private runtime log"\n'
                'print(json.dumps({"log_dir": str(root)}))\n'
                'print("Ran 1 test in 0.01s\\nOK")\n')
            options = {'host': '127.0.0.1', 'port': 31900, 'user': 'root', 'password': 'owned-private'}
            out = Path(directory) / 'evidence'
            out.mkdir()
            ambient = Path(directory) / 'unusable/logs'
            with patch.dict(docker_database.os.environ, {'AIVISION_LOG_DIR': str(ambient)}):
                result = docker_database.run_backend_tests(repo, sys.executable, options,
                                                          'framework_e2e_owned', out)
            self.assertEqual(result['status'], 'passed', result)
            log_dir = Path(json.loads((out / 'backend-tests.log').read_text().splitlines()[0])['log_dir'])
            self.assertNotEqual(log_dir, ambient)
            self.assertFalse(log_dir.is_relative_to(out), 'unredacted application logs must stay outside artifacts')
            self.assertFalse(log_dir.exists(), 'application logs must be removed when the child exits')
            self.assertFalse(ambient.exists())

    def test_backend_unicode_output_is_preserved_when_host_default_is_windows_cp1252(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory) / 'checkout'
            backend = repo / 'ai-vision/aivision-py-be'
            backend.mkdir(parents=True)
            (backend / 'environ.py').write_text('class Env:\n    read_env = staticmethod(lambda *args, **kwargs: None)\n')
            (backend / 'manage.py').write_text(
                'import sys\n'
                'sys.stdout.buffer.write("可用日志\\nRan 1 test in 0.01s\\nOK\\n".encode("utf-8"))\n',
                encoding='utf-8')
            out = Path(directory) / 'evidence'
            out.mkdir()
            options = {'host': '127.0.0.1', 'port': 31900, 'user': 'root', 'password': 'owned-private'}
            # Popen otherwise chooses the host's default decoder. This is the
            # same byte path that failed on hosted Windows, with a real child.
            with patch.object(docker_database.subprocess, '_text_encoding', return_value='cp1252'):
                result = docker_database.run_backend_tests(repo, sys.executable, options,
                                                          'framework_e2e_owned', out)
            self.assertEqual(result['status'], 'passed', result)
            self.assertIn('可用日志', (out / 'backend-tests.log').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
