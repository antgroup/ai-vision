"""Run AgentLens contracts on an owned, empty OceanBase or seekdb database.

Only Linux/macOS Docker with digest-pinned official images is supported. All
container names, databases and credentials belong to this invocation.
"""
from __future__ import annotations

import argparse
import ast
import builtins
from importlib.util import module_from_spec, spec_from_file_location
import json
import os
import platform
import re
import secrets
import subprocess
import tempfile
import time
import uuid
from pathlib import Path


# Load the standalone stdlib process owner without changing application paths.
_process_spec = spec_from_file_location('aivision_owned_e2e_process',
    Path(__file__).resolve().parents[1] / 'native_agent_e2e/drivers/_process.py')
_process_helper = module_from_spec(_process_spec)
_process_spec.loader.exec_module(_process_helper)
_run_owned = _process_helper._run

REPOSITORIES = {'oceanbase': 'oceanbase/oceanbase-ce', 'seekdb': 'oceanbase/seekdb'}
LINUX_PROC_TEST = ('tests/unit/diagnoser/rule_custom/test_enhanced_worker.py'
    '::test_group_permission_fallback_kills_real_descendants')
PY_ENTRY = ("import runpy,sys; import agentlens.config as c; "
    "c._resolve_env_file=lambda: ''; "
    "sys.argv=sys.argv[1:]; runpy.run_module(sys.argv[0],run_name='__main__')")


def _framework_db_adapter(repo):
    path = repo / 'ai-vision/agent-plugins/tests/framework_e2e/docker_database.py'
    spec = spec_from_file_location('framework_db_adapter', path)
    helper = module_from_spec(spec)
    spec.loader.exec_module(helper)
    return helper


def validate_image(backend, image):
    if backend not in REPOSITORIES or not re.fullmatch(
            re.escape(REPOSITORIES[backend]) + r'@sha256:[0-9a-f]{64}', image or ''):
        raise ValueError('Official backend image must be pinned by sha256 digest')
    return image


def _private_json(path, value):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
        json.dump(value, stream, sort_keys=True)


def _outside_checkout(path, repo):
    path, repo = Path(path).resolve(), Path(repo).resolve()
    if (path == repo or path.is_relative_to(repo) or repo.is_relative_to(path)
            or any((parent / '.git').exists() for parent in (path, *path.parents))):
        raise ValueError('Output must be outside every Git checkout')
    return path


def _sanitized_env(options, database, backend, config_file):
    blocked = ('DB_', 'AGENTLENS_', 'SIGNOZ_', 'NVIDIA_', 'OPENAI_', 'ANTHROPIC_',
        'AZURE_', 'LANGFUSE_', 'AIVISION_', 'OTEL_', 'PYTHONPATH')
    env = {key: value for key, value in os.environ.items()
        if not key.startswith(blocked) and key not in {'SECRET_KEY', 'DEBUG'}}
    env.update(DB_ENGINE='mysql+pymysql', DB_HOST=options['host'],
        DB_PORT=str(options['port']), DB_USER=options['user'],
        DB_PASSWORD=options['password'], DB_NAME=database,
        AGENTLENS_TOKEN='owned-offline-contract',
        SIGNOZ_OB_URL='mysql+pymysql://127.0.0.1/unused',
        SIGNOZ_ORG_ID='owned-offline-contract',
        ANTCOLLECTOR_ENDPOINT='http://127.0.0.1:9',
        AGENTLENS_TEST_DB_BACKEND=backend,
        AGENTLENS_TEST_DB_CONFIG_FILE=str(config_file),
        OTEL_SDK_DISABLED='true', PYTHONDONTWRITEBYTECODE='1',
        NO_PROXY='127.0.0.1,localhost', no_proxy='127.0.0.1,localhost')
    return env


def _child(python, module, args, *, cwd, env, timeout):
    command = [str(python), '-c', PY_ENTRY, module, *args]
    timed_out = False
    try:
        completed = _run_owned(command, cwd=cwd, env=env, capture_output=True,
            text=True, encoding='utf-8', errors='replace', timeout=timeout, check=False)
    except subprocess.TimeoutExpired as error:
        timed_out = True
        completed = subprocess.CompletedProcess(command, 124, error.stdout or '', error.stderr or '')
    # Child output may include private trace content or connection strings.
    # Retain only counts and source-verified failure identifiers/locations.
    log = completed.stdout + completed.stderr
    tests = re.findall(r'(\d+) passed', log)
    skipped = re.findall(r'(\d+) skipped', log)
    deselected = re.findall(r'(\d+) deselected', log)
    failed = re.findall(r'(\d+) failed', log)
    result = {'exit_code': completed.returncode, 'passed': int(tests[-1]) if tests else 0,
        'skipped': int(skipped[-1]) if skipped else 0,
        'deselected': int(deselected[-1]) if deselected else 0,
        'failed': int(failed[-1]) if failed else 0}
    if timed_out:
        result.update(timed_out=True, timeout_seconds=timeout)
    if completed.returncode:
        root = Path(cwd).resolve()
        sources = {}
        exception_types = {name for name, value in vars(builtins).items()
            if isinstance(value, type) and issubclass(value, BaseException)}
        def source_details(relative):
            if relative in sources:
                return sources[relative]
            path = (root / relative).resolve()
            if (not path.is_relative_to(root) or not path.is_file()
                    or path.relative_to(root).as_posix() != relative):
                return set(), 0
            try:
                source = path.read_text(encoding='utf-8')
                tree = ast.parse(source)
            except (OSError, SyntaxError, UnicodeError):
                return set(), 0
            symbols = set()
            def collect(body, parents=()):
                for node in body:
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if node.name.startswith('test_'):
                            symbols.add('::' + '::'.join((*parents, node.name)))
                    elif isinstance(node, ast.ClassDef):
                        collect(node.body, (*parents, node.name))
            collect(tree.body)
            sources[relative] = symbols, len(source.splitlines())
            return sources[relative]
        nodes = re.findall(
            r'^(?:FAILED|ERROR) (tests/[A-Za-z0-9_./-]+\.py)'
            r'((?:::[A-Za-z_][A-Za-z0-9_]*)+)(?=\[|\s|$)', log, re.MULTILINE)
        locations = re.findall(
            r'^((?:tests|src/agentlens)/[A-Za-z0-9_./-]+\.py):([1-9]\d*):'
            r'(?: ([A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception)))?$', log, re.MULTILINE)
        result['failure_evidence'] = {
            # Parameter IDs and exception messages may contain private values.
            'tests': sorted({path + node for path, node in nodes
                if node in source_details(path)[0]}),
            'locations': [dict(path=path, line=int(line), exception=kind or None)
                for path, line, kind in sorted(set(locations))
                if 0 < int(line) <= source_details(path)[1]
                    and (not kind or kind in exception_types)],
        }
    return result


def _unit_test_selection(system):
    # The /proc fallback is exercised against real descendants on Linux. It
    # cannot run on macOS; all other skips must still fail the database gate.
    exclusions = [LINUX_PROC_TEST] if system == 'Darwin' else []
    args = ['tests/unit', '-o', 'addopts=-ra', '-q', '-ra']
    for node in exclusions:
        args.extend(['--deselect', node])
    return args, exclusions


def _django_zero_skips(log, expected_tests):
    """Require Django's final success line and an explicit zero-skip result."""
    runs = re.findall(r'^Ran (\d+) tests? in [^\n]+$', log, flags=re.MULTILINE)
    finals = re.findall(r'^OK(?: \(skipped=(\d+)\))?$', log, flags=re.MULTILINE)
    if (len(runs) != 1 or int(runs[0]) != expected_tests or expected_tests <= 0
            or len(finals) != 1 or (finals[0] and int(finals[0]) != 0)):
        raise ValueError('Django final success or zero-skip count unverified')
    return 0


class OwnedDatabase:
    def __init__(self, backend, image, *, command=None, connector=None,
            clock=time.monotonic, pause=time.sleep, image_platform='linux/amd64'):
        self.backend = backend
        self.image = validate_image(backend, image)
        if image_platform not in ('linux/amd64', 'linux/arm64'):
            raise ValueError('Unsupported database image platform')
        self.image_platform = image_platform
        self.actual_image_platform = None
        self.command = command or self._command
        self.connector = connector
        self.clock, self.pause = clock, pause
        self.name = 'aiv_diag_unit_' + uuid.uuid4().hex
        self.password = secrets.token_hex(24)
        self.port = None
        self.private = None
        self.cleanup = {'container_name': self.name, 'container_removed': False}

    @staticmethod
    def _command(*args, timeout=90, check=True):
        return _run_owned(args, capture_output=True, text=True, timeout=timeout,
            check=check).stdout.strip()

    def _run(self, *args, timeout=90, check=True):
        return self.command(*args, timeout=timeout, check=check)

    def __enter__(self):
        if platform.system() not in {'Linux', 'Darwin'}:
            raise RuntimeError('Owned database Docker is unsupported on Windows')
        repo = Path(__file__).resolve().parents[4]
        _outside_checkout(tempfile.gettempdir(), repo)
        if self.connector is None:
            import mysql.connector
            self.connector = mysql.connector
        try:
            self._run('docker', 'pull', '--platform', self.image_platform, self.image,
                timeout=600)
            settings = ({'MODE': 'mini', 'OB_TENANT_PASSWORD': self.password,
                'OB_SYS_PASSWORD': self.password, 'OB_MEMORY_LIMIT': '6G',
                'OB_DATAFILE_SIZE': '2G', 'OB_LOG_DISK_SIZE': '4G'}
                if self.backend == 'oceanbase' else
                {'ROOT_PASSWORD': self.password, 'MEMORY_LIMIT': '2G',
                 'CPU_COUNT': '2', 'DATAFILE_SIZE': '2G', 'LOG_DISK_SIZE': '2G'})
            self.private = tempfile.TemporaryDirectory(prefix='aiv_diag_unit_')
            env_file = Path(self.private.name) / 'container.env'
            descriptor = os.open(env_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
                for key, value in settings.items():
                    stream.write(key + '=' + value + '\n')
            command = ['docker', 'run', '-d', '--platform', self.image_platform,
                '--name', self.name, '--ulimit', 'nofile=65536:65536',
                '-p', '127.0.0.1::2881', '--env-file', str(env_file)]
            self._run(*command, self.image)
            helper = _framework_db_adapter(repo)
            self.actual_image_platform = helper.container_image_platform(self._run, self.name)
            if self.actual_image_platform != self.image_platform:
                raise RuntimeError('Database image platform does not match the requested platform')
            port = self._run('docker', 'port', self.name, '2881/tcp')
            match = re.fullmatch(r'127\.0\.0\.1:(\d+)', port)
            if not match or not 0 < int(match[1]) < 65536:
                raise RuntimeError('Container port is not loopback-only')
            self.port = int(match[1])
            self.options = {'host': '127.0.0.1', 'port': self.port,
                'user': 'root@test' if self.backend == 'oceanbase' else 'root',
                'password': self.password, 'connection_timeout': 5, 'use_pure': True}
            self._ready()
            return self
        except BaseException:
            self._cleanup()
            raise

    def _ready(self):
        deadline = self.clock() + 600
        while True:
            try:
                with self.connector.connect(**self.options) as connection, connection.cursor() as cursor:
                    cursor.execute('SELECT version(), @@version_comment')
                    identity = ' '.join(map(str, cursor.fetchone())).lower()
                    if 'oceanbase' not in identity and 'seekdb' not in identity:
                        raise RuntimeError('Unexpected database server identity')
                return
            except self.connector.Error as error:
                if self.backend == 'seekdb' and getattr(error, 'errno', None) == 1045:
                    self._bootstrap_seekdb()
                    return
                if self.clock() >= deadline:
                    raise TimeoutError('Owned database did not become ready') from None
                self.pause(2)

    def _bootstrap_seekdb(self):
        empty = {**self.options, 'password': ''}
        with self.connector.connect(**empty) as connection, connection.cursor() as cursor:
            cursor.execute('SELECT version(), @@version_comment')
            identity = ' '.join(map(str, cursor.fetchone())).lower()
            if 'oceanbase' not in identity and 'seekdb' not in identity:
                raise RuntimeError('Unowned empty-password endpoint')
            cursor.execute("ALTER USER 'root' IDENTIFIED BY %s", (self.password,))
            connection.commit()
        with self.connector.connect(**self.options):
            pass
        try:
            with self.connector.connect(**empty):
                pass
        except self.connector.Error as error:
            if getattr(error, 'errno', None) != 1045:
                raise RuntimeError('Empty-password rejection uncertain') from None
        else:
            raise RuntimeError('Empty root password remains accepted')

    def create_empty(self):
        database = 'aiv_diag_unit_' + uuid.uuid4().hex
        with self.connector.connect(**self.options) as connection, connection.cursor() as cursor:
            cursor.execute('CREATE DATABASE ' + database)
            cursor.execute('SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=%s',
                (database,))
            if cursor.fetchone()[0] != 0:
                raise RuntimeError('Owned database is not empty')
            connection.commit()
        return database

    def transaction(self, database):
        receipt = uuid.uuid4().hex
        with self.connector.connect(**self.options, database=database) as connection, connection.cursor() as cursor:
            cursor.execute('CREATE TABLE aiv_diag_unit_receipt (id VARCHAR(64) PRIMARY KEY, n INT)')
            connection.commit()
            cursor.execute('INSERT INTO aiv_diag_unit_receipt VALUES (%s,%s)',
                (receipt, 18))
            connection.commit()
            cursor.execute('UPDATE aiv_diag_unit_receipt SET n=%s WHERE id=%s',
                (99, receipt))
            connection.rollback()
        with self.connector.connect(**self.options, database=database) as reader, reader.cursor() as cursor:
            cursor.execute('SELECT n FROM aiv_diag_unit_receipt WHERE id=%s',
                (receipt,))
            if cursor.fetchone() != (18,):
                raise RuntimeError('Commit visibility or rollback failed')
        return True

    def _cleanup(self):
        # A timed-out docker run can still create the named container.
        try:
            self._run('docker', 'rm', '-f', '-v', self.name, timeout=60, check=False)
            present = self._run('docker', 'ps', '-aq', '--filter',
                f'name=^/{self.name}$', timeout=30)
            self.cleanup['container_removed'] = not bool(present)
        except Exception:  # noqa: BLE001 - cleanup must retain unknown daemon failures
            self.cleanup['container_removed'] = False
        finally:
            if self.private is not None:
                self.private.cleanup()
                self.private = None

    def __exit__(self, *_args):
        self._cleanup()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=tuple(REPOSITORIES), required=True)
    parser.add_argument('--image', required=True, help='Official image with sha256 digest')
    parser.add_argument('--platform', choices=('linux/amd64', 'linux/arm64'),
        default='linux/amd64', help='Docker database image platform')
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--python', type=Path, required=True,
        help='Python executable with AgentLens installed')
    parser.add_argument('--django-python', type=Path,
        help='Optional installed Django backend interpreter')
    args = parser.parse_args(argv)
    validate_image(args.backend, args.image)
    repo = args.repo.resolve()
    # Preserve a virtualenv Python symlink; resolve only its path prefix.
    args.python = Path(os.path.abspath(os.path.expanduser(str(args.python))))
    if args.django_python:
        args.django_python = Path(os.path.abspath(os.path.expanduser(str(args.django_python))))
    out = _outside_checkout(args.out, repo)
    if out.exists():
        parser.error('Use a new output directory')
    if not (repo / 'agentlens/obschema/alembic.ini').is_file():
        parser.error('Expected AI Vision monorepo')
    if not args.python.is_file():
        parser.error('AgentLens Python executable is missing')
    out.mkdir(parents=True, mode=0o700)
    os.chmod(out, 0o700)
    result = {'status': 'failed', 'backend': args.backend, 'image': args.image,
        'platform': args.platform,
        'checks': {}, 'cleanup': {}}
    try:
        with tempfile.TemporaryDirectory(prefix='aiv_diag_unit_') as private:
            owned = OwnedDatabase(args.backend, args.image, image_platform=args.platform)
            with owned:
                result['actual_image_platform'] = owned.actual_image_platform
                unit_db = owned.create_empty()
                migration_db = owned.create_empty()
                txn_db = owned.create_empty()
                result['checks']['transaction'] = owned.transaction(txn_db)
                config = Path(private) / 'owned-db.json'
                _private_json(config, {'host': '127.0.0.1', 'port': owned.port,
                    'user': owned.options['user'], 'password': owned.password,
                    'database': unit_db})
                al = repo / 'agentlens'
                env = _sanitized_env(owned.options, unit_db, args.backend, config)
                unit_args, exclusions = _unit_test_selection(platform.system())
                result['checks']['platform_exclusions'] = exclusions
                unit = _child(args.python, 'pytest', unit_args,
                    cwd=al, env=env, timeout=1200)
                result['checks']['agentlens_units'] = unit
                if (unit['exit_code'] or unit['skipped'] or not unit['passed']
                        or unit['deselected'] != len(exclusions)):
                    raise RuntimeError('AgentLens unit contracts failed or skipped')
                migrated = _child(args.python, 'alembic', ['-c', 'obschema/alembic.ini',
                    'upgrade', 'head'], cwd=al,
                    env=_sanitized_env(owned.options, migration_db, args.backend, config),
                    timeout=600)
                result['checks']['migrations'] = {'exit_code': migrated['exit_code']}
                if migrated['exit_code']:
                    raise RuntimeError('AgentLens migration failed')
                if args.django_python:
                    # Django's own owned-DB test adapter is distinct from AL's
                    # empty fixture; invoke it through the existing public helper.
                    helper = _framework_db_adapter(repo)
                    django_db = owned.create_empty()
                    # Explicit owned fixtures enable Quick receipt/authorization
                    # tests. Keep opt-in external OpenAPI smoke tests out of this
                    # zero-skip database contract; the platform gate covers APIs.
                    labels = (
                        'apps.core.tests.test_diagnosis_quick_run',
                        'apps.core.tests.test_diagnosis_quick_offline',
                        'apps.core.tests.test_diagnosis_trial_timeout',
                        'apps.core.tests.test_agentlens_proxy_scope',
                        'apps.core.tests.test_personal_diagnosis',
                        'apps.core.tests.test_dataset_spec',
                        'apps.core.tests.test_third_party_onboarding',
                    )
                    django = helper.run_backend_tests(repo, args.django_python,
                        owned.options, django_db, out, backend_name=args.backend, test_labels=labels)
                    result['checks']['django'] = {'status': django['status'],
                        'tests_run': django['tests_run'], 'skipped': 'unverified'}
                    zero_skips = _django_zero_skips(
                        (out / django['log']).read_text(), django['tests_run'])
                    result['checks']['django']['skipped'] = zero_skips
                    if django['status'] != 'passed' or django['tests_run'] <= 0:
                        raise RuntimeError('Django backend tests failed')
            result['cleanup'] = owned.cleanup
            if not owned.cleanup['container_removed']:
                raise RuntimeError('Owned container cleanup unverified')
        result['status'] = 'passed'
    except Exception as error:  # noqa: BLE001 - bounded runner records failure type and cleans up
        result['error'] = {'type': type(error).__name__}
        if 'owned' in locals():
            result['actual_image_platform'] = getattr(owned, 'actual_image_platform', None)
            result['cleanup'] = owned.cleanup
    _private_json(out / 'summary.json', result)
    print(json.dumps({'status': result['status'], 'backend': args.backend,
        'checks': result['checks'], 'cleanup': result['cleanup']}))
    return 0 if result['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
