"""Linux/macOS real Docker platform smoke, using current backend source.

Requires Docker, Go, Make and six explicit official image digests in
AIVISION_E2E_PLATFORM_IMAGES_JSON. No existing platform credentials are read.
Raw deployment state stays in a private temporary runtime directory under HOME,
outside the artifact directory; only sanitized logs and reconciliation remain.
"""
import argparse
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import re
import secrets
import select
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import uuid

import httpx
import yaml

from suite import scenarios
from signoz_precision import build_candidate, remove_candidate


IMAGE_REPOS = {
    'oceanbase': ('oceanbase/oceanbase-ce',),
    'signoz': ('ghcr.io/ob-labs/signoz-ob', 'ghcr.io/ob-labs/signoz'),
    'gateway': ('ghcr.io/ob-labs/antcollector-gw',),
    'backend': ('ghcr.io/ob-labs/aivision-be',),
    'agentlens': ('ghcr.io/ob-labs/agentlens',),
    'frontend': ('ghcr.io/ob-labs/aivision-fe',),
}
PUBLIC_SERVICE_REPOS = {
    'gateway': 'ghcr.io/antgroup/ai-vision/ingest-gateway',
    'backend': 'ghcr.io/antgroup/ai-vision/backend',
    'agentlens': 'ghcr.io/antgroup/ai-vision/diagnosis',
    'frontend': 'ghcr.io/antgroup/ai-vision/frontend',
}
IMAGE_VARS = dict(zip(IMAGE_REPOS, ('OB_IMAGE', 'SIGNOZ_IMAGE', 'GATEWAY_IMAGE',
                                  'BE_IMAGE', 'AGENTLENS_IMAGE', 'FE_IMAGE')))
CONTAINER_VARS = ('OB_CONTAINER', 'SIGNOZ_CONTAINER', 'GATEWAY_CONTAINER',
                  'BE_CONTAINER', 'AGENTLENS_CONTAINER', 'FE_CONTAINER')
SECRET_VARS = ('OB_TENANT_PASSWORD', 'OB_SYS_PASSWORD', 'SIGNOZ_INGEST_INTERNAL_KEY',
               'AIVISION_INGEST_SECRET', 'SECRET_KEY', 'AGENTLENS_TOKEN', 'SIGNOZ_USER_ROOT_PASSWORD')
PORT_VARS = ('OB_PORT', 'SIGNOZ_PORT', 'GATEWAY_GRPC_PORT', 'GATEWAY_HTTP_PORT',
             'GATEWAY_HEALTH_PORT', 'BE_PORT', 'AGENTLENS_PORT', 'FE_PORT')


def deployment_passwords():
    passwords = {key: secrets.token_hex(24) for key in SECRET_VARS}
    # SigNoz requires upper/lower case, a digit and a special character.
    passwords['SIGNOZ_USER_ROOT_PASSWORD'] = 'Ci-9' + secrets.token_hex(24) + '!'
    return passwords


def system_tmp_roots():
    return tuple(Path(path).resolve() for path in ("/tmp", "/var/tmp", tempfile.gettempdir()))


def runtime_base(system=None, *, base=None, repo=None):
    if (system or platform.system()) not in ('Linux', 'Darwin'):
        raise ValueError('Docker platform deployment is supported here only on Linux/macOS')
    # Colima shares HOME by default. macOS TMPDIR (/var/folders/...) is not
    # shared, so bind mounts there can be empty or fail inside its Linux VM.
    if base is None:
        return Path.home()
    base = Path(base)
    if not base.is_absolute() or any(path.is_symlink() for path in (base, *base.parents)):
        raise ValueError('Private runtime base must be an absolute directory without symlinks')
    base = base.resolve()
    if not base.is_dir() or base == Path(base.anchor):
        raise ValueError('Private runtime base must be an existing validation directory')
    if (repo is not None and base.is_relative_to(Path(repo).resolve())) or any(
            (parent / '.git').exists() for parent in (base, *base.parents)):
        raise ValueError('Private runtime base must be outside Git')
    if any(base.is_relative_to(root) for root in system_tmp_roots()):
        raise ValueError('Private runtime source workspaces must be outside system tmp')
    return base


@contextmanager
def private_runtime(namespace, *, base=None, repo=None):
    with tempfile.TemporaryDirectory(prefix='.' + namespace + '-', dir=runtime_base(base=base, repo=repo)) as temporary:
        runtime = Path(temporary)
        runtime.chmod(0o700)
        # Container processes write files here as root. Own every log directory
        # before startup so the host can unlink the files without sudo/chown.
        for component in ('be-runtime', 'agentlens'):
            (runtime/'work'/namespace/'logs'/component).mkdir(parents=True, mode=0o700)
        yield runtime


def image_refs(raw):
    try:
        images = json.loads(raw)
    except (ValueError, TypeError) as error:
        raise ValueError('Supply AIVISION_E2E_PLATFORM_IMAGES_JSON with six official digests') from error
    if not isinstance(images, dict) or set(images) != set(IMAGE_REPOS):
        raise ValueError('Image keys must be oceanbase/signoz/gateway/backend/agentlens/frontend')
    public = any(isinstance(images[key], str) and images[key].startswith(repo + '@')
                 for key, repo in PUBLIC_SERVICE_REPOS.items())
    approved = IMAGE_REPOS
    if public:
        approved = {**IMAGE_REPOS, **{key: (repo,) for key, repo in PUBLIC_SERVICE_REPOS.items()}}
    for key, repositories in approved.items():
        expression = '(?:' + '|'.join(map(re.escape, repositories)) + r')@sha256:[0-9a-f]{64}'
        if not isinstance(images[key], str) or not re.fullmatch(expression, images[key]):
            raise ValueError('An explicit official sha256 image is required for ' + key)
    return images


def cache_platform_images(smoke, images):
    """Use host registry authentication without passing it to deployment containers."""
    for key, image in images.items():
        inspect = ['docker', 'image', 'inspect', '--format', '{{.Id}}', image]
        cached = smoke.run('image-cache-' + key, inspect, timeout=30, check=False)
        if cached.returncode:
            missing = re.fullmatch(r'(?:Error response from daemon: |Error: )?No such image: '
                                   + re.escape(image), cached.stderr.strip())
            if cached.returncode != 1 or cached.stdout.strip() not in ('', '[]') or not missing:
                raise RuntimeError('Cannot inspect pinned platform image cache: ' + key)
            smoke.run('image-pull-' + key, ['docker', 'pull', image], timeout=600)
            cached = smoke.run('image-cache-' + key + '-verify', inspect, timeout=30)
        if not re.fullmatch(r'sha256:[0-9a-f]{64}', cached.stdout.strip()):
            raise RuntimeError('Invalid cached platform image identity: ' + key)


def deployment_blueprint(source, repo, runtime, namespace, images, passwords, ports, history, *,
                         signoz_candidate=None, legacy_source_baseline=False):
    plan = deepcopy(source)
    if legacy_source_baseline:
        # These source fixtures predate the native healthcheck subcommands and
        # AgentLens readiness route. Use their existing HTTP health routes here;
        # released candidates must pass the unmodified embedded blueprint probes.
        probes = {
            'signoz-ob': {'http': 'http://127.0.0.1:${SIGNOZ_PORT}/api/v1/health',
                          'retries': 40, 'interval': '3s'},
            'antcollector-gw': {'http': 'http://127.0.0.1:${GATEWAY_HEALTH_PORT}/debug/pprof/',
                                'retries': 30, 'interval': '2s'},
            'agentlens': {'http': 'http://127.0.0.1:${AGENTLENS_PORT}/internal/healthz',
                          'retries': 60, 'interval': '3s'},
        }
        for component in plan['components']:
            if component['name'] in probes:
                component['health'] = probes[component['name']]
    plan['metadata'].update(name=namespace, namespace=namespace)
    plan['config'].update(workdir=str(runtime/'work'), sourceRoot=str(runtime/'source'),
                          dockerNetwork=namespace + '-network')
    variables = plan['config']['vars']
    variables.update({IMAGE_VARS[key]: value for key, value in images.items()})
    if signoz_candidate is not None:
        if signoz_candidate != 'aivision-signoz-e2e:' + namespace:
            raise ValueError('Precision candidate must belong to this deployment')
        variables['SIGNOZ_IMAGE'] = signoz_candidate
    variables.update({key: namespace + '-' + name for key, name in zip(CONTAINER_VARS, IMAGE_REPOS)})
    variables.update(passwords)
    variables.update(ports)
    variables.update(OB_MANAGED='true', IMAGE_PULL='missing', OB_MODE='mini',
                     OB_MEMORY_LIMIT='6G', OB_DATAFILE_SIZE='2G', OB_LOG_DISK_SIZE='4G',
                     BIND_ADDR='127.0.0.1', PUBLIC_HOST='127.0.0.1',
                     DOCKER_NETWORK=namespace + '-network',
                     SIGNOZ_ROOT_EMAIL=namespace + '@example.invalid', SIGNOZ_ROOT_ORG_NAME=namespace,
                     AIVISION_NATIVE_TRACE_HISTORY_START=history)
    be = next(item for item in plan['components'] if item['name'] == 'be')
    backend = repo/'ai-vision/aivision-py-be'
    # /app/.venv is inside the image. Mount only current application code,
    # retaining both the image environment and its official boot entrypoint.
    for name in ('apps', 'aivision_backend', 'manage.py'):
        if not (backend/name).exists():
            raise ValueError('Missing current backend source: ' + name)
        be['mounts'].append(f'type=bind,source={backend/name},target=/app/{name},readonly')
    plugins = repo/'ai-vision/agent-plugins'
    for name in ('agents.json', 'langfuse/examples', 'nvidia-nemo-relay/examples',
                 'nvidia-nemo-relay/relay_tracing.py'):
        if not (plugins/name).exists():
            raise ValueError('Missing current onboarding source: ' + name)
        be['mounts'].append(f'type=bind,source={plugins/name},target=/agent-plugins/{name},readonly')
    # Local HTTP credentials are used only inside this isolated smoke run.
    be['env'].update(DEBUG='true', AUTH_COOKIE_SECURE='false', PUBLIC_REGISTRATION_ENABLED='true',
                     PYTHONDONTWRITEBYTECODE='1')
    return plan


def suite_passed(summary, sdk=None):
    cases = summary.get('cases', [])
    sdks = ['langfuse'] if sdk == 'lf5' else ['langfuse', 'nemo-relay']
    expected = {f'{framework}-{integration}-{scenario}' for framework in ('langchain', 'deepagents')
                for integration in sdks for scenario in scenarios()}
    return (summary.get('status') == 'passed' and summary.get('mode') == 'ci'
            and summary.get('data_backend') == 'oceanbase' and summary.get('sdks') == sdks
            and (sdk is None or sdk in ('lf3', 'lf4', 'lf5') and
                 summary.get('langfuse_version') == {'lf3': '3.15.0', 'lf4': '4.16.0', 'lf5': '5.0.2'}[sdk])
            and summary.get('expected_cases') == summary.get('executed_cases') == len(cases) == len(expected)
            and {case.get('case') for case in cases} == expected
            and all(case.get('execute_exit') == case.get('verify_exit') == 0
                    and case.get('source_verified') is True and case.get('platform_verified') is True
                    for case in cases))


def sdk_suite_command(repo, out, namespace, image, sdk='lf4'):
    """Run current SDK acceptance in the already pinned Linux backend image."""
    if sdk not in ('lf3', 'lf4', 'lf5'):
        raise ValueError('Choose the pinned lf3, lf4 or lf5 SDK environment')
    script = '''command -v uv >/dev/null 2>&1 || { echo "SDK acceptance image requires uv" >&2; exit 127; }
export AIVISION_E2E_DATA_BACKEND=oceanbase PYTHONDONTWRITEBYTECODE=1
export NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost UV_PYTHON_DOWNLOADS=automatic
uv venv --python 3.12 /tmp/aivision-framework-e2e
uv pip install --python /tmp/aivision-framework-e2e/bin/python -r /workspace/ai-vision/agent-plugins/tests/framework_e2e/requirements-lf4.txt
exec /tmp/aivision-framework-e2e/bin/python /workspace/ai-vision/agent-plugins/tests/framework_e2e/suite.py --mode ci --repo /workspace --out /artifacts/cases --workers 1'''
    script = script.replace('requirements-lf4.txt', 'requirements-' + sdk + '.txt')
    if sdk == 'lf5':
        script += ' --sdks langfuse'
    return ['docker', 'run', '--name', namespace + '-sdk-' + sdk,
            '--label', 'aivision.framework-e2e.namespace=' + namespace,
            '--platform', 'linux/amd64', '--network', 'host', '--workdir', '/tmp',
            '--env', 'AIVISION_E2E_TARGETS_JSON',
            '--mount', f'type=bind,source={repo},target=/workspace,readonly',
            '--mount', f'type=bind,source={out},target=/artifacts',
            '--entrypoint', '/bin/sh', image, '-ec', script]


def clean_environment(source):
    prefixes = ('AIV_VAR_', 'AIVISION_', 'AIV_', 'OTEL_', 'LANGFUSE_', 'LANGCHAIN_',
                'LANGSMITH_', 'OPENAI_', 'ANTHROPIC_')
    env = {key: value for key, value in source.items() if not key.startswith(prefixes)}
    env.update(NO_PROXY='127.0.0.1,localhost', no_proxy='127.0.0.1,localhost',
               PYTHONUNBUFFERED='1', DOCKER_DEFAULT_PLATFORM='linux/amd64')
    return env


def available_ports():
    sockets = []
    try:
        for _key in PORT_VARS:
            sock = socket.socket()
            sock.bind(('127.0.0.1', 0))
            sockets.append(sock)
        return {key: str(sock.getsockname()[1]) for key, sock in zip(PORT_VARS, sockets)}
    finally:
        for sock in sockets:
            sock.close()


def bootstrap_status(smoke, names):
    """Project container state into fixed, non-payload failure evidence."""
    template = ('{"status":{{json .State.Status}},"running":{{json .State.Running}},'
                '"oomKilled":{{json .State.OOMKilled}},"exitCode":{{json .State.ExitCode}},'
                '"healthStatus":{{if .State.Health}}{{json .State.Health.Status}}{{else}}"none"{{end}}}')
    result = {role: {'inspection': 'unavailable', 'reason': 'name_unavailable'} for role in IMAGE_REPOS}
    for role, name in zip(IMAGE_REPOS, names):
        try:
            inspected = smoke.run('bootstrap-status-' + role,
                                  ['docker', 'container', 'inspect', '--format', template, name],
                                  timeout=10, check=False)
        except Exception:
            result[role] = {'inspection': 'failed', 'reason': 'inspection_error'}
            continue
        code = inspected.returncode
        if type(code) is not int or not -255 <= code <= 255:
            result[role] = {'inspection': 'failed', 'reason': 'invalid_response'}
            continue
        if code:
            result[role] = {'inspection': 'failed', 'reason': 'inspect_failed', 'exitCode': code}
            continue
        try:
            state = json.loads(inspected.stdout)
        except (TypeError, ValueError):
            state = None
        if (not isinstance(state, dict)
                or state.get('status') not in ('created', 'restarting', 'running', 'removing', 'paused', 'exited', 'dead')
                or type(state.get('running')) is not bool or type(state.get('oomKilled')) is not bool
                or type(state.get('exitCode')) is not int or not 0 <= state['exitCode'] <= 255
                or state.get('healthStatus') not in ('none', 'starting', 'healthy', 'unhealthy')):
            result[role] = {'inspection': 'failed', 'reason': 'invalid_response'}
            continue
        result[role] = {'inspection': 'ok', 'exists': True, 'status': state['status'],
                        'running': state['running'], 'oomKilled': state['oomKilled'],
                        'exitCode': state['exitCode'], 'healthStatus': state['healthStatus']}
    return result


def disk_free_bytes(workspace, docker_root):
    """Report capacity only; daemon paths may not exist in a Docker VM client."""
    result = {'workspace': None, 'docker_root': None}
    for key, value in (('workspace', workspace), ('docker_root', docker_root)):
        try:
            if value and Path(value).is_absolute() and Path(value).is_dir():
                free = shutil.disk_usage(value).free
                if type(free) is int and free >= 0:
                    result[key] = free
        except (OSError, TypeError, ValueError):
            pass
    return result


def classify_oceanbase_bootstrap(body):
    """Recognize public upstream/helper lines, never copy log-derived text."""
    stages = {'do normal boot': 'deploying', 'obcluster deployed': 'deployed',
              'Start observer ok': 'observer_started',
              'observer program health check ok': 'observer_healthy',
              'Connect to observer ok': 'observer_connected',
              'oceanbase bootstrap ok': 'database_bootstrapped',
              'tenant is connectable': 'tenant_connected', 'boot success!': 'ready'}
    errors = {
        'managed OceanBase lifecycle requires the standard obcluster layout; SLIM/demo is unsupported': 'unsupported_mode',
        'OceanBase image lacks boot/env.sh': 'missing_boot_environment',
        'OceanBase bootstrap exited unexpectedly': 'bootstrap_exited',
        '[ERROR] OBD-1006: Failed to connect to oceanbase-ce': 'observer_connection_failed',
        'boot failed!': 'upstream_boot_failed',
    }
    result = {'stage': 'unknown', 'errors': []}
    for raw in body.splitlines(keepends=True):
        # A byte-capped read can end inside a line. It is not a complete marker.
        if not raw.endswith(('\n', '\r')):
            continue
        line = raw.strip()
        if line in stages:
            result['stage'] = stages[line]
        error = errors.get(line)
        if re.fullmatch(
                r'\[ERROR\] OBD-2003: \([0-9a-fA-F:.]+\) /\S* not enough disk space\. '
                r'\(Avail: [0-9]+(?:\.[0-9]+)?(?:[KMGTPE]B?|B), '
                r'Need: [0-9]+(?:\.[0-9]+)?(?:[KMGTPE]B?|B)\)'
                r'(?:, Please reduce the `datafile_size` or `datafile_disk_percentage`)?', line):
            error = 'insufficient_disk'
        elif re.fullmatch(r'OceanBase bootstrap failed \(exit [0-9]{1,3}\)', line):
            error = 'bootstrap_failed'
        elif line in ('OceanBase image lacks required lifecycle executable: /usr/sbin/sshd',
                      'OceanBase image lacks required lifecycle executable: /root/boot/start.sh',
                      'OceanBase image lacks required lifecycle executable: /usr/bin/obd'):
            error = 'missing_lifecycle_executable'
        elif line in ('OceanBase image lacks required lifecycle tool: setsid',
                      'OceanBase image lacks required lifecycle tool: timeout',
                      'OceanBase image lacks required lifecycle tool: pgrep'):
            error = 'missing_lifecycle_tool'
        if error and error not in result['errors']:
            result['errors'].append(error)
    return result


def read_bootstrap_log(command, env, *, byte_limit=65536, timeout=10):
    """Read a bounded private pipe; do not persist or print any captured bytes."""
    if os.name == 'nt':
        return b'', 'unavailable'
    process = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    body = bytearray()
    scan = 'complete'
    deadline = time.monotonic() + timeout
    try:
        while len(body) < byte_limit:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([process.stdout], [], [], max(0, remaining))[0]:
                scan = 'timeout'
                break
            chunk = os.read(process.stdout.fileno(), min(8192, byte_limit - len(body)))
            if not chunk:
                break
            body.extend(chunk)
        else:
            scan = 'byte_limit'
        if scan != 'complete':
            process.kill()
        try:
            code = process.wait(timeout=max(0.01, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            scan = 'timeout'
            process.kill()
            code = process.wait(timeout=1)
        if scan == 'complete' and code:
            return b'', 'unavailable'
        return bytes(body), scan
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=1)
        process.stdout.close()


def oceanbase_bootstrap(smoke, name):
    try:
        body, scan = read_bootstrap_log(['docker', 'logs', '--tail', '200', name], smoke.env)
        return {'scan': scan, **classify_oceanbase_bootstrap(body.decode('utf-8', errors='replace'))}
    except Exception:
        return {'scan': 'unavailable', 'stage': 'unknown', 'errors': []}


class Smoke:
    def __init__(self, out, env, private):
        self.out, self.env, self.private = out, env, private

    def sanitize(self, value):
        text = str(value)
        for secret in sorted(self.private, key=len, reverse=True):
            text = text.replace(secret, '[REDACTED]')
        return re.sub(r'sk-aiv-[A-Za-z0-9_-]+', '[REDACTED]', text)

    def write(self, filename, value):
        (self.out/filename).write_text(self.sanitize(value), encoding='utf-8')

    def sdk_suite(self, repo, namespace, image, targets, sdk='lf4'):
        out = self.out/'sdk'/sdk
        out.mkdir(parents=True)
        # Docker resolves --env NAME from this child environment. Neither the
        # command line nor a mounted file contains the generated SpaceKey.
        env = dict(self.env, AIVISION_E2E_TARGETS_JSON=json.dumps(targets))
        return self.run('api-suite-' + sdk, sdk_suite_command(repo, out, namespace, image, sdk=sdk),
                        env=env, timeout=1800, check=False)

    def run(self, label, command, *, cwd=None, env=None, timeout=120, check=True):
        process = subprocess.Popen(command, cwd=cwd, env=env or self.env, text=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            # Stop the entire cast/suite process group before resource cleanup;
            # otherwise an orphaned docker child could create resources later.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            stdout, stderr = process.communicate()
            self.write(label + '.log', stdout + stderr)
            raise RuntimeError(label + ' timed out') from None
        except BaseException:
            # Manual interruption must not leave a cast child racing finally.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            stdout, stderr = process.communicate()
            self.write(label + '.log', stdout + stderr)
            raise
        result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
        self.write(label + '.log', result.stdout + result.stderr)
        if check and result.returncode:
            raise RuntimeError(f'{label} failed (exit {result.returncode}); see sanitized log')
        return result

    def cleanup(self, cli, blueprint, namespace, names):
        errors = []
        sdk_names = [name for name in names if name in (namespace + '-sdk', namespace + '-sdk-lf3', namespace + '-sdk-lf4', namespace + '-sdk-lf5')]
        for sdk_name in sdk_names:
            try:
                listing = ['docker', 'ps', '-aq', '--filter', f'name=^/{sdk_name}$']
                existing = self.run('find-' + sdk_name, listing)
                if existing.stdout.strip():
                    owner = self.run('owner-' + sdk_name, ['docker', 'inspect', '--format',
                                     '{{ index .Config.Labels "aivision.framework-e2e.namespace" }}', sdk_name])
                    if owner.stdout.strip() != namespace:
                        raise RuntimeError('SDK container belongs to another deployment: ' + sdk_name)
                    self.run('remove-' + sdk_name, ['docker', 'rm', '-f', '-v', sdk_name], check=False)
                    if self.run('verify-removed-' + sdk_name, listing).stdout.strip():
                        raise RuntimeError('SDK container still exists: ' + sdk_name)
            except Exception as error:
                errors.append('SDK container: ' + self.sanitize(error))
        try:
            if cli.exists() and blueprint.exists():
                self.run('destroy', [str(cli), 'destroy', '--file', str(blueprint),
                                    '--namespace', namespace, '--purge', '-y'], timeout=240, check=False)
        except Exception as error:
            errors.append('CLI destroy: ' + self.sanitize(error))
        # A failed cast can create resources before writing CLI state. Fall
        # back to exact names generated by this process, never global prune.
        resources = [('container', name) for name in names if name not in sdk_names]
        resources += [('volume', f'aivforge-ob-{part}-{namespace}') for part in ('data', 'config')]
        resources += [('network', namespace + '-network')]
        for kind, name in resources:
            try:
                command = ['docker', 'rm', '-f', '-v', name] if kind == 'container' else ['docker', kind, 'rm', name]
                self.run('remove-' + name, command, check=False)
                listing = (['docker', 'ps', '-aq', '--filter', f'name=^/{name}$'] if kind == 'container'
                           else ['docker', kind, 'ls', '-q', '--filter', 'name=^' + name + '$'])
                remaining = self.run('verify-removed-' + name, listing)
                if remaining.stdout.strip():
                    raise RuntimeError('Resource still exists: ' + name)
            except Exception as error:
                errors.append(kind + ': ' + self.sanitize(error))
        return errors


def register_targets(origin, namespace, password, private, *, agent_products=None, collector_endpoint=None):
    """Use the same public management auth/CSRF flows as the CLI, in memory."""
    with httpx.Client(base_url=origin, timeout=60, trust_env=False) as client:
        def request(method, path, body=None, extra_headers=None):
            headers = {'Origin': origin, 'Referer': origin + '/'}
            if method != 'GET':
                headers['X-CSRFToken'] = client.cookies.get('csrftoken', '')
            response = client.request(method, path, json=body, headers={**headers,**(extra_headers or {})})
            for cookie in client.cookies.jar:
                private.add(cookie.value)
            if response.status_code >= 400:
                raise RuntimeError(f'Management {method} {path} returned HTTP {response.status_code}')
            value = response.json()
            if value.get('code') != 0:
                raise RuntimeError(f'Management {method} {path} returned code {value.get("code")}')
            return value['data']
        request('GET', '/api/v1/auth/csrf')
        account = request('POST', '/api/v1/auth/register', {
            'email': namespace + '@example.invalid', 'name': 'Framework CI',
            'password': password, 'password_confirm': password,
        })
        space = account['personal_space']['space_id']
        # Registration rotates auth state; ask for a fresh public CSRF cookie.
        request('GET', '/api/v1/auth/csrf')
        me = request('GET', '/api/v1/auth/me')
        user = me.get('user') if isinstance(me, dict) else None
        authenticated_user_id = user.get('user_uuid') if isinstance(user, dict) else None
        if (type(authenticated_user_id) is not str or not authenticated_user_id
                or authenticated_user_id.strip() != authenticated_user_id or len(authenticated_user_id) > 36):
            raise RuntimeError('Management API returned an invalid authenticated user identity')
        key = request('POST', f'/api/v1/spaces/{space}/space-keys', {'name': 'framework-ci'})['key']
        private.add(key)
        targets = {}
        for sdk, product in (agent_products.items() if agent_products is not None else
                             (('langfuse', 'langfuse'), ('nemo-relay', 'nvidia-nemo-relay'))):
            agent = request('POST', f'/api/v1/spaces/{space}/agents/create', {
                'agent_product': product, 'name': 'Framework CI ' + sdk,
            })
            if not agent.get('agent_uuid') or agent.get('agent_product') != product:
                raise RuntimeError('Management API returned an unexpected Agent identity')
            targets[sdk] = {'endpoint': origin, 'agent_uuid': agent['agent_uuid'],
                            'space_key': key, 'space_id': space, 'agent_product': product,
                            'data_backend': 'oceanbase', 'authenticated_user_id': authenticated_user_id}
            if collector_endpoint is not None:
                minted=request('POST','/openapi/v1/ingest-tokens',{'agent_uuid':agent['agent_uuid']},
                               {'Authorization':'Bearer '+key})
                if minted.get('agent_uuid')!=agent['agent_uuid'] or minted.get('agent_product')!=product or not minted.get('token'):
                    raise RuntimeError('Ingest token returned an unexpected Agent identity')
                private.add(minted['token'])
                cookie='; '.join(c.name+'='+c.value for c in client.cookies.jar)
                private.add(cookie)
                targets[sdk].update(collector_endpoint=collector_endpoint,
                    collector_headers={'Authorization':'Bearer '+minted['token']},
                    dashboard_headers={'Cookie':cookie},ingest_token=minted['token'])
        return targets


def main(argv=None, *, acceptance=None, target_factory=None, expected_cases=110, runtime_base=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    args.out = args.out.resolve()
    args.repo = args.repo.resolve()
    args.out.mkdir(parents=True, exist_ok=False)
    namespace = 'aiv-e2e-' + uuid.uuid4().hex[:12]
    result = {'status': 'failed', 'namespace': namespace, 'data_backend': 'oceanbase',
              'checks': {}, 'os': platform.system(), 'expected_cases': expected_cases,
              'backend_source': 'current checkout apps/aivision_backend/manage.py',
              'health_probe_mode': 'legacy-source-baseline-http',
              'other_services': 'explicit candidate image digests'}
    passwords = deployment_passwords()
    account_password = 'Ci-' + secrets.token_hex(24) + '!'
    private = set(passwords.values()) | {account_password}
    smoke = Smoke(args.out, clean_environment(os.environ), private)
    try:
        if platform.system() not in ('Linux', 'Darwin'):
            raise ValueError('Docker platform deployment is supported here only on Linux/macOS')
        images = image_refs(os.environ.get('AIVISION_E2E_PLATFORM_IMAGES_JSON'))
        result['images'] = images
        with private_runtime(namespace, base=runtime_base, repo=args.repo) as runtime:
            cli, blueprint = runtime/'aiv', runtime/'blueprint.yaml'
            plan = None
            names = []
            attempted = False
            candidate_started = False
            try:
                info = smoke.run('docker-info', ['docker', 'info', '--format', '{{json .}}'])
                docker_info = json.loads(info.stdout)
                result['resources'] = {'docker_memory_bytes': docker_info.get('MemTotal'),
                                       'docker_cpu_count': docker_info.get('NCPU'),
                                       'docker_architecture': docker_info.get('Architecture'),
                                       'free_disk_bytes_before_candidate': disk_free_bytes(args.repo, docker_info.get('DockerRootDir'))}
                candidate_started = True
                candidate = build_candidate(smoke, Path(__file__).resolve().parent, runtime, namespace, images['signoz'])
                result['signoz_precision_candidate'] = candidate
                effective_images = {**images, 'signoz': candidate['image']}
                cache_platform_images(smoke, images)
                result['checks']['pinned_images_cached'] = True
                source = yaml.safe_load((args.repo/'deploy/blueprints/aivision.yaml').read_text(encoding='utf-8'))
                history = datetime.now(timezone.utc).strftime('%Y-%m-%dT00:00:00Z')
                plan = deployment_blueprint(source, args.repo, runtime, namespace, images,
                                            passwords, available_ports(), history,
                                            signoz_candidate=candidate['image'], legacy_source_baseline=True)
                names = [plan['config']['vars'][key] for key in CONTAINER_VARS]
                names.extend(namespace + '-sdk-' + sdk for sdk in ('lf3', 'lf4', 'lf5'))
                blueprint.write_text(yaml.safe_dump(plan, sort_keys=False), encoding='utf-8')
                blueprint.chmod(0o600)
                smoke.write('blueprint.redacted.yaml', blueprint.read_text(encoding='utf-8'))
                smoke.run('build-cli', ['make', 'build', 'OUTPUT=' + str(cli)],
                          cwd=args.repo/'aivision-cli', timeout=300)
                attempted = True
                smoke.run('cast', [str(cli), 'cast', '--file', str(blueprint),
                                   '--namespace', namespace], timeout=1800)
                result['checks']['cast_bootstrap'] = True
                for key, name in zip(IMAGE_REPOS, names):
                    expected = smoke.run('image-' + key, ['docker', 'image', 'inspect',
                                         '--format', '{{.Id}}', effective_images[key]]).stdout.strip()
                    actual = smoke.run('container-image-' + key, ['docker', 'inspect',
                                       '--format', '{{.Image}}', name]).stdout.strip()
                    if not expected or actual != expected:
                        raise AssertionError('Running image does not match pinned candidate: ' + key)
                result['checks']['running_images_match'] = True
                sql = ('import os,json; os.environ.setdefault("DJANGO_SETTINGS_MODULE","aivision_backend.settings"); '
                       'import django; django.setup(); from django.db import connection; c=connection.cursor(); '
                       'c.execute("SELECT VERSION(), @@version_comment"); v=c.fetchone(); '
                       'c.execute("SELECT COUNT(*) FROM django_migrations"); n=c.fetchone()[0]; '
                       'print(json.dumps({"version":v,"migration_count":n,"vendor":connection.vendor}))')
                sql_result = smoke.run('sql-bootstrap', ['docker', 'exec', names[3], 'python', '-c', sql])
                sql_data = json.loads(sql_result.stdout)
                if (sql_data['vendor'] != 'mysql' or sql_data['migration_count'] <= 0
                        or 'oceanbase' not in str(sql_data['version']).lower()):
                    raise AssertionError('Bootstrap did not use an actual migrated OceanBase database')
                result['checks']['oceanbase_sql_and_migrations'] = True
                origin = 'http://127.0.0.1:' + plan['config']['vars']['FE_PORT']
                targets = (register_targets(origin, namespace, account_password, private) if target_factory is None else
                           target_factory(origin=origin,namespace=namespace,password=account_password,
                                          private=private,plan=plan,smoke=smoke))
                result['checks']['public_auth_onboarding'] = True
                if acceptance is None:
                    result['sdk_suites'] = {}
                    for sdk in ('lf3', 'lf4', 'lf5'):
                        execution = smoke.sdk_suite(args.repo, namespace, images['backend'], targets, sdk=sdk)
                        summary = json.loads((args.out/'sdk'/sdk/'cases/summary.json').read_text(encoding='utf-8'))
                        if execution.returncode or not suite_passed(summary, sdk=sdk):
                            raise AssertionError('Full selected SDK profile source and runner-local API reconciliation failed for ' + sdk)
                        result['sdk_suites'][sdk] = {'status': summary['status'], 'cases': summary['executed_cases'],
                                                     'langfuse_version': summary['langfuse_version']}
                    result['checks']['all_110_ingest_read_cases'] = True
                else:
                    accepted=acceptance(repo=args.repo,out=args.out,runtime=runtime,namespace=namespace,
                                        images=images,smoke=smoke,targets=targets)
                    if not isinstance(accepted,dict) or accepted.get('verified') is not True:
                        raise AssertionError('Runner-local acceptance failed')
                    result.update({k:v for k,v in accepted.items() if k!='verified'})
                    result['checks']['all_'+str(expected_cases)+'_ingest_read_cases'] = True
                result['status'] = 'passed'
            finally:
                if attempted:
                    if result['status'] != 'passed':
                        result['bootstrap_status'] = bootstrap_status(smoke, names)
                        result['resources']['free_disk_bytes_after_bootstrap'] = disk_free_bytes(args.repo, docker_info.get('DockerRootDir'))
                        ob = result['bootstrap_status']['oceanbase']
                        if (not result['checks'].get('cast_bootstrap') and names
                                and ob.get('inspection') == 'ok' and ob.get('status') == 'exited'):
                            result['oceanbase_bootstrap'] = oceanbase_bootstrap(smoke, names[0])
                    # SDK stdout/stderr is already in api-suite-lf*.log. Do not
                    # inspect its logs before the ownership check in cleanup.
                    for name in names[:len(CONTAINER_VARS)]:
                        try:
                            smoke.run('container-' + name, ['docker', 'logs', '--tail', '2000', name], check=False)
                        except Exception as error:
                            result.setdefault('evidence_errors', []).append(smoke.sanitize(error))
                    errors = smoke.cleanup(cli, blueprint, namespace, names)
                    result['checks']['owned_resources_removed'] = not errors
                    if errors:
                        result['cleanup_errors'] = errors
                        result['status'] = 'failed'
                if candidate_started:
                    try:
                        remove_candidate(smoke, namespace, 'aivision-signoz-e2e:' + namespace)
                        result['checks']['owned_candidate_image_removed'] = True
                    except Exception as error:
                        result.setdefault('cleanup_errors', []).append(smoke.sanitize(error))
                        result['checks']['owned_candidate_image_removed'] = False
                        result['status'] = 'failed'
    except Exception as error:
        result['status'] = 'failed'
        result['error'] = {'type': type(error).__name__, 'message': smoke.sanitize(error)}
    finally:
        smoke.write('summary.json', json.dumps(result, indent=2) + '\n')
    print(json.dumps({'status': result['status'], 'checks': result['checks'], 'error': result.get('error')}))
    return 0 if result['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
