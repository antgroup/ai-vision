"""Linux/macOS Docker database compatibility; only OceanBase and seekdb.

This verifies container startup and real MySQL-protocol transactions. The full
SDK→collector→platform API acceptance is suite.py and has a separate CI job.
"""
import argparse
from importlib.util import module_from_spec, spec_from_file_location
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile
import time
import uuid


# Load the standalone stdlib process owner without changing application paths.
_process_spec = spec_from_file_location('aivision_owned_e2e_process',
    Path(__file__).resolve().parents[1] / 'native_agent_e2e/drivers/_process.py')
_process_helper = module_from_spec(_process_spec)
_process_spec.loader.exec_module(_process_helper)
_run_owned = _process_helper._run


def image_for(backend, images):
    repository = {'oceanbase': 'oceanbase/oceanbase-ce', 'seekdb': 'oceanbase/seekdb'}[backend]
    image = images.get(backend, '')
    if not re.fullmatch(re.escape(repository) + r'@sha256:[0-9a-f]{64}', image):
        raise ValueError('Configure the official ' + backend + ' image by immutable sha256 digest')
    return image


def container_image_platform(run, name):
    # Raw container inspect preserves the server's descriptor even with an older
    # CLI. It includes private config, so keep the output in memory.
    rows = json.loads(run('docker', 'container', 'inspect', name))
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise RuntimeError('Invalid container inspection for database image platform')
    container = rows[0]
    manifest = container.get('ImageManifestDescriptor')
    if manifest is not None:
        platform = manifest.get('platform') if isinstance(manifest, dict) else None
        fields = ('os', 'architecture')
        if not isinstance(platform, dict) or not all(
                isinstance(platform.get(field), str) and platform[field] for field in fields):
            raise RuntimeError('Container image manifest has no usable platform')
        return '/'.join(platform[field] for field in fields)

    # Classic Docker stores one concrete image under .Image. containerd can
    # store the entire index under that ID, so a missing descriptor cannot
    # be replaced by image inspect on that store (including pre-1.48 APIs).
    driver_status = json.loads(run('docker', 'info', '--format', '{{json .DriverStatus}}'))
    if driver_status is not None and (
            not isinstance(driver_status, list) or any(
                not isinstance(row, list) or len(row) != 2 or row[0] == 'driver-type'
                for row in driver_status)):
        raise RuntimeError('Cannot verify database image platform without a container manifest')
    image_id = container.get('Image')
    if not isinstance(image_id, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', image_id):
        raise RuntimeError('Container does not report a concrete image ID')
    return run('docker', 'image', 'inspect', '--format', '{{.Os}}/{{.Architecture}}', image_id)


def initialize_owned_seekdb(connector, options):
    """Set this freshly created local seekdb's generated password after 1045.

    The caller has just created the digest-pinned container without a data
    volume and obtained its loopback-only mapped port. This is initialization,
    not an alternate credential for the transaction/backend acceptance.
    """
    if (options['host'] != '127.0.0.1' or options['user'] != 'root'
            or not options['password'] or not 0 < options['port'] < 65536):
        raise ValueError('Credential initialization requires the owned local root endpoint')
    empty_options = {**options, 'password': ''}
    with connector.connect(**empty_options) as initializer:
        cursor = initializer.cursor()
        try:
            cursor.execute('SELECT version(), @@version_comment')
            identity = ' '.join(map(str, cursor.fetchone())).lower()
            if 'oceanbase' not in identity and 'seekdb' not in identity:
                raise AssertionError('Empty root endpoint does not identify OceanBase seekdb')
            cursor.execute("ALTER USER 'root' IDENTIFIED BY %s", (options['password'],))
            initializer.commit()
        finally:
            cursor.close()
    strict = connector.connect(**options)
    try:
        try:
            with connector.connect(**empty_options):
                pass
        except connector.Error as error:
            if getattr(error, 'errno', None) != 1045:
                raise AssertionError('Fresh empty root verification must fail with 1045') from error
        else:
            raise AssertionError('Fresh empty root password remains accepted after initialization')
    except Exception:
        strict.close()
        raise
    return strict


BACKEND_TEST_TIMEOUT_SECONDS = 900


def run_backend_tests(repo, python, options, database, out, *, backend_name=None, test_labels=("apps",)):
    """Run all Django apps against this run's actual MySQL-protocol database.

    Settings normally fall back to a local .env even with AIVISION_ENV=test.
    Disable that reader only in this child process so no developer/platform
    credentials can affect CI. The selected backend venv still owns Django.
    """
    if backend_name not in {None, 'oceanbase', 'seekdb'}:
        raise ValueError('Explicit owned database backend required')
    if not isinstance(test_labels, (list, tuple)) or not test_labels:
        raise ValueError('Nonempty explicit Django test labels required')
    backend = Path(repo).resolve() / 'ai-vision/aivision-py-be'
    result = {'status': 'failed', 'tests_run': 0, 'exit_code': None,
              'timeout_seconds': BACKEND_TEST_TIMEOUT_SECONDS, 'log': 'backend-tests.log'}
    password = options['password']
    def sanitized(value):
        if isinstance(value, bytes):
            value = value.decode('utf-8', errors='replace')
        return (value or '').replace(password, '[REDACTED]')
    prefixes = ('DB_', 'AIVISION_', 'ALIPAY_', 'OTEL_', 'LANGFUSE_', 'ANTLANGFUSE_',
                'LANGCHAIN_', 'LANGSMITH_', 'OPENAI_', 'ANTHROPIC_', 'AZURE_',
                'GOOGLE_', 'GEMINI_', 'SIGNOZ_', 'AGENTLENS_')
    removed = {'DJANGO_SETTINGS_MODULE', 'PYTHONPATH', 'DEBUG', 'SECRET_KEY',
               'CREDENTIAL_ENCRYPTION_KEY', 'SHARE_LINK_SIGNING_KEY'}
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(prefixes) and key not in removed}
    env.update(AIVISION_ENV='test', USE_SQLITE_FOR_TESTS='false',
               DB_ENGINE='aivision_backend.db_backends.mysql_connector_oceanbase', DB_HOST=options['host'],
               DB_PORT=str(options['port']), DB_USER=options['user'],
               DB_PASSWORD=password, DB_NAME=database, DEBUG='false',
               SECRET_KEY='framework-e2e-isolated-test-secret',
               OTEL_SDK_DISABLED='true', LANGFUSE_TRACING_ENABLED='false',
               NO_PROXY='127.0.0.1,localhost', no_proxy='127.0.0.1,localhost',
               PYTHONUNBUFFERED='1', PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1')
    if backend_name:
        env['AIVISION_QUICK_TEST_DB_BACKEND'] = backend_name
    entrypoint = ('import environ, runpy, sys; '
                  'environ.Env.read_env = lambda *args, **kwargs: None; '
                  'sys.argv = sys.argv[1:]; '
                  'runpy.run_path(sys.argv[0], run_name="__main__")')
    # Keep a venv executable's symlink path: resolving it would select the base
    # interpreter and discard the backend's installed Django dependencies.
    command = [os.path.abspath(os.path.expanduser(str(python))), '-c', entrypoint,
               'manage.py', 'test', *test_labels, '--noinput']
    log = ''
    try:
        if not (backend / 'manage.py').is_file():
            raise ValueError('--backend-tests must point to the AI Vision monorepo')
        # Both regular and error handlers consume this variable. Their native
        # defaults differ (/tmp versus /home/admin on Linux) and are unsuitable
        # on hosted runners. Raw application logs may contain sensitive data:
        # keep them in an owned private directory, separate from artifacts.
        with tempfile.TemporaryDirectory(prefix='aivision-backend-logs-') as log_dir:
            env['AIVISION_LOG_DIR'] = log_dir
            completed = _run_owned(command, cwd=backend, env=env, text=True,
                                       encoding='utf-8', errors='replace',
                                       capture_output=True, timeout=BACKEND_TEST_TIMEOUT_SECONDS)
        log = sanitized(completed.stdout) + sanitized(completed.stderr)
        counts = re.findall(r'^Ran (\d+) tests? in ', log, flags=re.MULTILINE)
        result.update(exit_code=completed.returncode,
                      tests_run=int(counts[-1]) if counts else 0)
        if completed.returncode == 0 and result['tests_run'] > 0:
            result['status'] = 'passed'
    except Exception as error:
        if isinstance(error, subprocess.TimeoutExpired):
            result['exit_code'] = 124
            log = sanitized(error.stdout) + sanitized(error.stderr)
        message = sanitized(str(error))
        result['error'] = {'type': type(error).__name__, 'message': message}
        log += '\n' + type(error).__name__ + ': ' + message + '\n'
    (Path(out) / 'backend-tests.log').write_text(log, encoding='utf-8')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=('oceanbase', 'seekdb'), required=True)
    parser.add_argument('--platform', choices=('linux/amd64', 'linux/arm64'),
                        default='linux/amd64', help='Docker database image platform')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--backend-tests', type=Path,
                        help='Run all Django apps tests from this checkout on the owned database')
    parser.add_argument('--backend-test-label', action='append',
                        help='Explicit Django test module; default runs all apps')
    parser.add_argument('--python', type=Path, default=Path(sys.executable),
                        help='Python executable in the installed Django backend virtualenv')
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    password = secrets.token_hex(24)
    name = 'aiv-framework-db-' + uuid.uuid4().hex[:12]
    result = {'status': 'failed', 'backend': args.backend, 'platform': args.platform,
              'checks': {}, 'container': name}
    def run(*command, check=True, timeout=90):
        completed = _run_owned(command, text=True, capture_output=True, timeout=timeout)
        if check and completed.returncode:
            raise RuntimeError((completed.stdout + completed.stderr).replace(password, '[REDACTED]'))
        return (completed.stdout + completed.stderr if command[:2] == ('docker', 'logs') else completed.stdout).strip()
    try:
        import mysql.connector
        image = image_for(args.backend, json.loads(os.environ.get('AIVISION_E2E_DB_IMAGES_JSON') or '{}'))
        result['image'] = image
        run('docker', 'pull', '--platform', args.platform, image, timeout=600)
        if args.backend == 'oceanbase':
            settings = {'MODE': 'mini', 'OB_TENANT_PASSWORD': password, 'OB_SYS_PASSWORD': password,
                        'OB_MEMORY_LIMIT': '6G', 'OB_DATAFILE_SIZE': '2G', 'OB_LOG_DISK_SIZE': '4G'}
            user = 'root@test'
        else:
            settings = {'ROOT_PASSWORD': password, 'MEMORY_LIMIT': '2G', 'CPU_COUNT': '2',
                        'DATAFILE_SIZE': '2G', 'LOG_DISK_SIZE': '2G'}
            user = 'root'
        command = ['docker', 'run', '-d', '--platform', args.platform, '--name', name,
                   '--ulimit', 'nofile=65536:65536', '-p', '127.0.0.1::2881']
        for key, value in settings.items():
            command.extend(('-e', key + '=' + value))
        run(*command, image)
        actual_platform = container_image_platform(run, name)
        result['actual_image_platform'] = actual_platform
        if actual_platform != args.platform:
            raise RuntimeError('Database image platform does not match the requested platform')
        port = int(run('docker', 'port', name, '2881/tcp').rsplit(':', 1)[1])
        options = {'host': '127.0.0.1', 'port': port, 'user': user, 'password': password,
                   'connection_timeout': 5, 'use_pure': True}
        deadline = time.monotonic() + 600
        while True:
            try:
                connection = mysql.connector.connect(**options)
                break
            except mysql.connector.Error as error:
                result.setdefault('diagnostics', {})['last_sql_error'] = {
                    'type': type(error).__name__, 'errno': getattr(error, 'errno', None),
                    'message': str(error).replace(password, '[REDACTED]')}
                if args.backend == 'seekdb' and getattr(error, 'errno', None) == 1045:
                    connection = initialize_owned_seekdb(mysql.connector, options)
                    result['checks']['credential_bootstrap'] = True
                    result['checks']['empty_password_rejected'] = True
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError('Actual database did not become SQL-ready within 600 seconds; '
                                       'last SQL error: ' + str(error)) from error
                time.sleep(2)
        result['checks']['sql_ready'] = True
        database = 'framework_e2e_' + uuid.uuid4().hex
        with connection:
            cursor = connection.cursor()
            cursor.execute('SELECT version(), @@version_comment')
            result['server_version'], result['version_comment'] = map(str, cursor.fetchone())
            if 'oceanbase' not in (result['server_version'] + result['version_comment']).lower():
                raise AssertionError('Server identity does not identify OceanBase or seekdb')
            result['checks']['actual_server_identity'] = True
            # This database name is generated locally; values use bound params.
            cursor.execute('CREATE DATABASE ' + database)
            cursor.execute('USE ' + database)
            cursor.execute('CREATE TABLE rag_receipts (id VARCHAR(64) PRIMARY KEY, tokens INT, payload TEXT)')
            receipt = uuid.uuid4().hex
            payload = json.dumps({'tenant': 'orion', 'retention_days': 14, 'storage_gib': 140}, sort_keys=True)
            cursor.execute('INSERT INTO rag_receipts VALUES (%s, %s, %s)', (receipt, 18, payload))
            connection.commit()
            cursor.execute('UPDATE rag_receipts SET tokens = %s WHERE id = %s', (99, receipt))
            connection.rollback()
            cursor.close()
        with mysql.connector.connect(**options, database=database) as reader:
            cursor = reader.cursor()
            cursor.execute('SELECT tokens, payload FROM rag_receipts WHERE id = %s', (receipt,))
            row = cursor.fetchone()
            result['checks']['commit_visible_to_new_connection'] = row == (18, payload)
            result['checks']['rollback_not_persisted'] = row[0] == 18
            cursor.close()
        if not all(result['checks'].values()):
            raise AssertionError('Real database transaction reconciliation failed')
        if getattr(args, 'backend_tests', None) is not None:
            result['backend_tests'] = run_backend_tests(
                args.backend_tests, getattr(args, 'python', sys.executable),
                options, database, args.out, backend_name=args.backend,
                test_labels=getattr(args, 'backend_test_label', None) or ('apps',))
            result['checks']['backend_tests'] = result['backend_tests']['status'] == 'passed'
            if not result['checks']['backend_tests']:
                raise AssertionError('Django backend tests failed; see backend-tests.log')
        result['status'] = 'passed'
    except Exception as error:
        result['error'] = {'type': type(error).__name__, 'message': str(error).replace(password, '[REDACTED]')}
    finally:
        # A failed/timed-out docker run can already have created its container.
        # A successful empty listing proves absence. A failed daemon query is
        # unknown, so attempt removal and retain the cleanup failure.
        # Query only this run's unpredictable name; never clean shared resources.
        result['cleanup'] = {}
        # Inspect only State, never Config/Env: preserve startup/OOM/exit evidence
        # before removal without copying credentials from the container config.
        diagnostics = result.setdefault('diagnostics', {})
        try:
            state = json.loads(run('docker', 'inspect', '--format', '{{json .State}}', name))
            fields = ('Status', 'Running', 'Paused', 'Restarting', 'OOMKilled', 'Dead',
                      'ExitCode', 'Error', 'StartedAt', 'FinishedAt')
            diagnostics['container_state'] = {
                key: value.replace(password, '[REDACTED]') if isinstance(value, str) else value
                for key, value in state.items() if key in fields}
        except Exception as error:
            diagnostics['container_state_error'] = {
                'type': type(error).__name__,
                'message': str(error).replace(password, '[REDACTED]')}
        try:
            exists = run('docker', 'ps', '-aq', '--filter', f'name=^/{name}$')
        except Exception as error:
            exists = 'unknown'
            result['cleanup']['inspect_error'] = type(error).__name__
        if exists:
            try:
                logs = run('docker', 'logs', name, check=False)
                (args.out / 'database.log').write_text(logs.replace(password, '[REDACTED]'), encoding='utf-8')
            except Exception as error:
                result['cleanup']['logs_error'] = type(error).__name__
            try:
                run('docker', 'rm', '-f', '-v', name)
                result['cleanup']['container_removed'] = True
            except Exception as error:
                result['cleanup']['remove_error'] = type(error).__name__
        if any(key.endswith('_error') for key in result['cleanup']):
            result['status'] = 'failed'
        (args.out / 'summary.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': result['status'], 'backend': args.backend, 'checks': result['checks'],
                      'backend_tests': result.get('backend_tests'), 'error': result.get('error')}))
    return 0 if result['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
