"""SDK -> disposable platform -> current-source browser acceptance.

Default mode owns and removes a fresh Docker stack. --targets/--auth-file uses
an explicitly supplied loopback test deployment and never destroys its data.
Only synthetic prompts enter the opt-in NVIDIA path; no provider configuration
is read in fake mode. All child commands execute once, without suite retries.
"""
import argparse
import base64
from http.cookiejar import Cookie
import json
import os
from pathlib import Path
import re
import signal
import stat
import sys
import tempfile
from urllib.parse import urlsplit

import httpx

import docker_platform as shared


NVIDIA_BASE_URL = 'https://integrate.api.nvidia.com/v1'
NVIDIA_MODEL = 'nvidia/nemotron-3.5-lightning-30b-a3b'
SDK_VERSIONS = {'js5': '5.14.0', 'js6': '6.0.0'}
HERE = Path(__file__).resolve().parent


def install_signal_handlers():
    # GitHub cancellation sends SIGTERM. Convert it into stack unwinding so
    # Smoke kills its current process group and the owner removes only its stack.
    def terminate(_number, _frame):
        raise KeyboardInterrupt('Browser acceptance terminated')
    signal.signal(signal.SIGTERM, terminate)


def child_environment(source):
    env = shared.clean_environment(source)
    return {key: value for key, value in env.items()
            if not key.startswith(('NVIDIA_', 'SILICONFLOW_', 'AZURE_OPENAI_', 'GOOGLE_API_'))}


def provider_key(source, env):
    if source == 'fake':
        return None
    key = env.get('NVIDIA_API_KEY', '')
    if not key or re.search(r'[\s\x00]', key):
        raise ValueError('NVIDIA mode requires an explicit single-line NVIDIA_API_KEY')
    return key


def loopback_origin(value):
    url = urlsplit(value)
    if (url.scheme != 'http' or url.hostname not in ('127.0.0.1', 'localhost', '::1')
            or url.username or url.password or url.query or url.fragment or url.path not in ('', '/')):
        raise ValueError('Use an explicit HTTP loopback test origin without credentials')
    return value.rstrip('/')


def validate_target(targets):
    target = targets.get('langfuse')
    if not isinstance(target, dict):
        raise ValueError('An explicit Langfuse test target is required')
    for field in ('space_id', 'agent_uuid', 'agent_product', 'space_key'):
        if not isinstance(target.get(field), str) or not target[field]:
            raise ValueError('Test target is missing ' + field)
    loopback_origin(target.get('endpoint', ''))
    if target.get('data_backend') != 'oceanbase':
        raise ValueError('Browser matrix requires the declared OceanBase test backend')
    return target


def public_target(target):
    return {'spaceId': target['space_id'], 'agentUuid': target['agent_uuid'],
            'agentProduct': target['agent_product']}


def read_private_json(path, out):
    path = Path(path)
    if (path.is_symlink() or not path.is_file() or not stat.S_ISREG(path.stat().st_mode)
            or path.resolve().is_relative_to(Path(out).resolve())
            or stat.S_IMODE(path.stat().st_mode) & 0o077):
        raise ValueError('Private inputs must be regular 0600 files outside artifacts')
    return json.loads(path.read_text(encoding='utf-8'))


def write_private(path, value):
    with open(path, 'x', encoding='utf-8', opener=lambda name, flags: os.open(name, flags, 0o600)) as file:
        file.write(value)


def client_state(client, origin, private, expected_user=None):
    private.update(cookie.value for cookie in client.cookies.jar)
    me = client.get('/api/v1/auth/me')
    body = me.json() if me.status_code == 200 else {}
    user = body.get('data', {}).get('user', {}).get('user_uuid')
    if body.get('code') != 0 or not user or expected_user is not None and user != expected_user:
        raise RuntimeError('Browser authentication did not identify the expected test user')
    cookies = []
    for cookie in client.cookies.jar:
        private.add(cookie.value)
        cookies.append({'name': cookie.name, 'value': cookie.value,
            'domain': urlsplit(origin).hostname, 'path': cookie.path or '/',
            'expires': cookie.expires if cookie.expires is not None else -1,
            'httpOnly': cookie.has_nonstandard_attr('HttpOnly'), 'secure': cookie.secure,
            'sameSite': 'Lax'})
    if not cookies:
        raise RuntimeError('Browser authentication returned no cookies')
    return {'cookies': cookies, 'origins': []}


def login_state(origin, email, password, private, expected_user=None):
    """Public auth only; tokens remain in process/private storageState."""
    origin = loopback_origin(origin)
    with httpx.Client(base_url=origin, timeout=30, trust_env=False) as client:
        csrf = client.get('/api/v1/auth/csrf')
        csrf.raise_for_status()
        response = client.post('/api/v1/auth/login', json={'email': email, 'password': password},
            headers={'Origin': origin, 'Referer': origin + '/', 'X-CSRFToken': client.cookies.get('csrftoken', '')})
        if response.status_code != 200 or response.json().get('code') != 0:
            raise RuntimeError('Browser public login failed with HTTP ' + str(response.status_code))
        private.add(password)
        return client_state(client, origin, private, expected_user)


def prepare_browser_auth(origin, runtime, private, *, auth_file=None, login_credentials=None, out, expected_user=None):
    """Obtain fresh access immediately before pages; never rewrite supplied state."""
    origin = loopback_origin(origin)
    if login_credentials is not None:
        state = login_state(origin, login_credentials['email'], login_credentials['password'], private, expected_user)
    else:
        supplied = read_private_json(auth_file, out)
        with httpx.Client(base_url=origin, timeout=30, trust_env=False) as client:
            for value in supplied.get('cookies', []):
                domain = value.get('domain', '')
                if domain.lstrip('.') != urlsplit(origin).hostname:
                    raise ValueError('Browser cookie domain must match the explicit loopback platform')
                private.add(value['value'])
                expires = value.get('expires', -1)
                client.cookies.jar.set_cookie(Cookie(version=0, name=value['name'], value=value['value'],
                    port=None, port_specified=False, domain=domain, domain_specified=domain.startswith('.'),
                    domain_initial_dot=domain.startswith('.'), path=value.get('path', '/'), path_specified=True,
                    secure=value.get('secure', False), expires=int(expires) if expires > 0 else None,
                    discard=expires <= 0, comment=None, comment_url=None,
                    rest={'HttpOnly': None} if value.get('httpOnly') else {}, rfc2109=False))
            csrf = client.get('/api/v1/auth/csrf')
            csrf.raise_for_status()
            private.update(cookie.value for cookie in client.cookies.jar)
            response = client.post('/api/v1/auth/refresh', headers={'Origin': origin, 'Referer': origin + '/',
                'X-CSRFToken': client.cookies.get('csrftoken', '')})
            private.update(cookie.value for cookie in client.cookies.jar)
            if response.status_code != 200 or response.json().get('code') != 0:
                raise RuntimeError('Browser public session refresh failed with HTTP ' + str(response.status_code))
            state = client_state(client, origin, private, expected_user)
    path = runtime / 'browser-auth.json'
    write_private(path, json.dumps(state))
    return path


def audit_artifacts(out, private):
    """Fail closed and remove forbidden files before the workflow can upload."""
    redacted, removed = [], []
    secrets = sorted((value for value in private if isinstance(value, str) and value), key=len, reverse=True)
    for path in sorted(Path(out).rglob('*')):
        if path.is_dir() and not path.is_symlink():
            continue
        relative = str(path.relative_to(out))
        allowed = path.suffix.lower() in ('.json', '.jsonl', '.log', '.png') or path.name == 'blueprint.redacted.yaml'
        if path.is_symlink() or not allowed:
            path.unlink()
            removed.append(relative)
            continue
        data = path.read_bytes()
        sensitive = any(value.encode() in data for value in secrets)
        if path.suffix.lower() == '.png':
            if sensitive:
                path.unlink(); removed.append(relative)
            continue
        try:
            content = data.decode('utf-8')
        except UnicodeDecodeError:
            path.unlink(); removed.append(relative)
            continue
        clean = content
        for secret in secrets:
            clean = clean.replace(secret, '[REDACTED]')
        clean = re.sub(r'(?:nvapi-|sk-aiv-)[A-Za-z0-9_-]+', '[REDACTED]', clean)
        if clean != content:
            path.write_text(clean, encoding='utf-8')
            redacted.append(relative)
    return {'passed': not redacted and not removed, 'redacted_files': redacted, 'removed_files': removed}


def collection_commands(repo, out, runtime, *, source, python, node):
    framework = repo / 'ai-vision/agent-plugins/tests/framework_e2e'
    js = repo / 'ai-vision/agent-plugins/langfuse/tests/js'
    if source == 'fake':
        commands = [{'name': 'python5', 'argv': [python, str(framework / 'suite.py'), '--mode', 'ci',
            '--repo', str(repo), '--out', str(out / 'python5'), '--sdks', 'langfuse', '--workers', '1'], 'requests': 0}]
        entry = 'run_platform.mjs'
    else:
        commands = [{'name': 'python5', 'argv': [python, str(framework / 'live_python.py'), '--platform',
            '--provider-base-url', NVIDIA_BASE_URL, '--model', NVIDIA_MODEL, '--out', str(out / 'python5'),
            '--langchain-token-limit-field', 'max_tokens', '--disable-thinking'], 'requests': 3}]
        entry = 'run_live_provider.mjs'
    for name, environment in (('js5', js), ('js6', js.parent / 'js6')):
        argv = [node, str(js / entry), '--targets', str(runtime / 'targets.json'),
                '--environment', str(environment), '--out', str(out / name)]
        if source == 'nvidia':
            argv[1:1] = ['--use-env-proxy']
            argv += ['--provider-env', str(runtime / 'provider.env'), '--key-name', 'NVIDIA_API_KEY',
                     '--base-url', NVIDIA_BASE_URL, '--model', NVIDIA_MODEL, '--disable-thinking']
        commands.append({'name': name, 'argv': argv, 'requests': 0 if source == 'fake' else 2})
    return commands


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def verify_collection(out, source):
    if source == 'fake':
        if not shared.suite_passed(read_json(out / 'python5/summary.json'), sdk='lf5'):
            raise AssertionError('Complete Python5 fake matrix was not verified')
    else:
        report = read_json(out / 'python5/report.json')
        if (report.get('status') != 'passed' or report.get('mode') != 'platform'
                or report.get('versions', {}).get('langfuse') != '5.0.2'
                or len(report.get('cases', [])) != 3 or not report.get('checks')
                or not all(value is True for value in report['checks'].values())):
            raise AssertionError('All three Python5 NVIDIA requests must reconcile')
        if len(read_json(out / 'python5/provider-ledger.json')) != 3:
            raise AssertionError('Python provider request budget mismatch')
    for name, sdk in SDK_VERSIONS.items():
        report = read_json(out / name / ('platform-readback.json' if source == 'fake' else 'live-readback.json'))
        expected = 33 if source == 'fake' else 2
        if report.get('passed') is not True or report.get('traces') != expected or report.get('failures') != []:
            raise AssertionError('Complete ' + name + ' platform matrix was not verified')
        files = ['ledger.json', 'ledger.json.lifecycle.json'] if source == 'fake' else ['live-ledger.json']
        for filename in files:
            ledger = read_json(out / name / filename)
            if not ledger or any(row.get('sdkVersion') != sdk for row in ledger):
                raise AssertionError('Unexpected SDK version in ' + name)
            if source == 'nvidia' and len(ledger) != 2:
                raise AssertionError('JS provider request budget mismatch')
    return {'python_cases': 22 if source == 'fake' else 3,
            'js_traces_per_sdk': 33 if source == 'fake' else 2,
            'provider_request_budget': 0 if source == 'fake' else 7}


def browser_environment(env, manifest, auth_file, out, browser_origin, api_origin):
    return {**child_environment(env), 'AIVISION_BROWSER_MANIFEST': str(manifest),
        'AIVISION_BROWSER_AUTH_FILE': str(auth_file), 'AIVISION_BROWSER_OUTPUT_DIR': str(out),
        'AIVISION_BROWSER_BASE_URL': loopback_origin(browser_origin),
        'VITE_DEV_API_TARGET': loopback_origin(api_origin), 'VITE_DEV_API_PRESERVE_HOST': '1'}


def verify_browser_report(report, source):
    stats = report.get('stats', {})
    minimum = 24 if source == 'fake' else 11
    expected = stats.get('expected', 0)
    if (type(expected) is not int or expected < minimum
            or any(stats.get(key) != 0 for key in ('unexpected', 'flaky', 'skipped'))):
        raise AssertionError('Browser report is empty, partial, skipped, flaky or failed')
    tests = []
    def visit(suites):
        for suite in suites:
            for spec in suite.get('specs', []):
                tests.extend(spec.get('tests', []))
            visit(suite.get('suites', []))
    visit(report.get('suites', []))
    if len(tests) != expected or any(test.get('status') != 'expected'
            or len(test.get('results', [])) != 1 or test['results'][0].get('status') != 'passed' for test in tests):
        raise AssertionError('Browser test results do not match the successful assertion count')
    return expected


def run_acceptance(*, repo, out, runtime, smoke, targets, auth_file, source,
                   python=sys.executable, node='node', browser_origin='http://127.0.0.1:41966', key=None,
                   login_credentials=None, **_):
    target = validate_target(targets)
    if login_credentials is None:
        auth = read_private_json(auth_file, out)
        if not isinstance(auth.get('cookies'), list) or not auth['cookies']:
            raise ValueError('Browser auth file requires a nonempty cookie jar')
        smoke.private.update(cookie['value'] for cookie in auth['cookies'] if cookie.get('value'))
    else:
        smoke.private.add(login_credentials['password'])
    smoke.private.add(target['space_key'])
    smoke.private.add(base64.b64encode((target['agent_uuid'] + ':' + target['space_key']).encode()).decode())
    if key:
        smoke.private.add(key)
    report = {'status': 'failed', 'source': source, 'completed_stages': [], 'automatic_retries': 0,
              'provider_request_budget': 0 if source == 'fake' else 7,
              'max_output_tokens_per_request': 32 if source == 'nvidia' else None,
              'frontend': 'current checkout Vite source; real API requests',
              'provider': NVIDIA_BASE_URL if source == 'nvidia' else None,
              'model': NVIDIA_MODEL if source == 'nvidia' else None}
    try:
        write_private(runtime / 'targets.json', json.dumps({'langfuse': target}))
        if source == 'nvidia':
            if not key:
                raise ValueError('NVIDIA mode requires an explicit key')
            write_private(runtime / 'provider.env', 'NVIDIA_API_KEY=' + key + '\n')
        (out / 'target-public.json').write_text(json.dumps(public_target(target)), encoding='utf-8')
        env = {**child_environment(smoke.env), 'AIVISION_E2E_TARGETS_JSON': json.dumps({'langfuse': target}),
               'AIVISION_E2E_DATA_BACKEND': 'oceanbase'}
        for command in collection_commands(repo, out, runtime, source=source, python=python, node=node):
            current = dict(env)
            if key and command['name'] == 'python5':
                current['AIVISION_LIVE_PROVIDER_KEY'] = key
            process = smoke.run(command['name'], command['argv'], env=current, timeout=2400 if source == 'fake' else 420, check=False)
            if process.returncode:
                raise RuntimeError(command['name'] + ' failed; no retry was attempted')
            report['completed_stages'].append(command['name'])
        report['collection'] = verify_collection(out, source)
        manifest = out / 'browser-manifest.json'
        command = [python, str(HERE / 'browser_manifest.py'), '--source', source,
            '--python-root', str(out / 'python5'), '--js-root', str(out / 'js5'), '--js-root', str(out / 'js6'),
            '--target-public', str(out / 'target-public.json'), '--out', str(manifest)]
        smoke.run('manifest', command, env=child_environment(smoke.env))
        report['completed_stages'].append('manifest')
        page_auth = prepare_browser_auth(target['endpoint'], runtime, smoke.private, auth_file=auth_file,
            login_credentials=login_credentials, out=out, expected_user=target.get('authenticated_user_id'))
        report['browser_auth'] = 'fresh login after collection' if login_credentials is not None else 'public refresh after collection'
        frontend = repo / 'ai-vision/vfe'
        browser_env = browser_environment(smoke.env, manifest, page_auth, out / 'browser', browser_origin, target['endpoint'])
        result = smoke.run('browser', [node, str(frontend / 'node_modules/@playwright/test/cli.js'),
            'test', '--config', 'playwright.platform.config.ts'], cwd=frontend, env=browser_env, timeout=900, check=False)
        if result.returncode:
            raise RuntimeError('Browser assertions failed; see sanitized screenshots and report')
        report['browser_tests'] = verify_browser_report(read_json(out / 'browser/results.json'), source)
        report['completed_stages'].append('browser')
        report['status'] = 'passed'
    except BaseException as error:
        report['error'] = {'type': type(error).__name__, 'message': smoke.sanitize(error)}
        raise
    finally:
        audit = audit_artifacts(out, smoke.private)
        report['artifact_audit'] = audit
        if not audit['passed']:
            report['status'] = 'failed'
        smoke.write('browser-summary.json', json.dumps(report, indent=2) + '\n')
        for name in ('targets.json', 'provider.env', 'browser-auth.json'):
            (runtime / name).unlink(missing_ok=True)
    if report['status'] != 'passed':
        raise RuntimeError('Artifact audit rejected unsafe output')
    return {'verified': True, 'browser_acceptance': report}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--source', choices=('fake', 'nvidia'), default='fake')
    parser.add_argument('--targets', type=Path, help='Private existing test target; disables deployment ownership')
    parser.add_argument('--auth-file', type=Path, help='Private existing Playwright storageState')
    parser.add_argument('--login-file', type=Path, help='Private {email,password} for existing test platform')
    parser.add_argument('--python', default=sys.executable)
    parser.add_argument('--node', default='node')
    parser.add_argument('--browser-origin', default='http://127.0.0.1:41966')
    args = parser.parse_args(argv)
    args.repo, args.out = args.repo.resolve(), args.out.resolve()
    if bool(args.targets) != bool(args.auth_file or args.login_file) or args.auth_file and args.login_file:
        parser.error('Existing mode requires --targets and exactly one of --auth-file / --login-file')
    try:
        key = provider_key(args.source, os.environ)
        loopback_origin(args.browser_origin)
        options = {'source': args.source, 'python': args.python, 'node': args.node,
                   'browser_origin': args.browser_origin, 'key': key}
        if args.targets:
            targets = read_private_json(args.targets, args.out)
            target = validate_target(targets)
            args.out.mkdir(parents=True, exist_ok=False)
            private = {target['space_key']} | ({key} if key else set())
            smoke = shared.Smoke(args.out, child_environment(os.environ), private)
            with tempfile.TemporaryDirectory(prefix='aiv-browser-private-') as temporary:
                runtime = Path(temporary); runtime.chmod(0o700)
                if args.auth_file:
                    read_private_json(args.auth_file, args.out)
                    auth_file = args.auth_file.resolve()
                    login = None
                else:
                    login = read_private_json(args.login_file, args.out)
                    auth_file = None
                result = run_acceptance(repo=args.repo, out=args.out, runtime=runtime, smoke=smoke,
                    targets=targets, auth_file=auth_file, login_credentials=login, **options)
                smoke.write('summary.json', json.dumps({'status': 'passed', 'deployment': 'existing; retained',
                    'owned_child_processes_completed': True, 'browser_acceptance': result['browser_acceptance']}, indent=2))
            return 0
        # The fresh stack account is created by public APIs; login state is
        # retained in memory until the acceptance callback receives its runtime.
        state = {}
        def factory(*, origin, namespace, password, private, **_):
            targets = shared.register_targets(origin, namespace, password, private, agent_products={'langfuse': 'langfuse'})
            state['login'] = {'email': namespace + '@example.invalid', 'password': password}
            return targets
        def acceptance(*, runtime, **kwargs):
            return run_acceptance(runtime=runtime, auth_file=None, login_credentials=state.pop('login'), **kwargs, **options)
        # Deployment/build subprocesses do not need the provider credential.
        # Keep it in the explicit closure until the bounded provider stage.
        inherited_key = os.environ.pop('NVIDIA_API_KEY', None)
        try:
            return shared.main(['--repo', str(args.repo), '--out', str(args.out)], acceptance=acceptance,
                target_factory=factory, expected_cases=88 if args.source == 'fake' else 7)
        finally:
            if inherited_key is not None:
                os.environ['NVIDIA_API_KEY'] = inherited_key
    except Exception as error:
        # No raw exception text: parsing errors may contain private input values.
        print(json.dumps({'status': 'failed', 'error_type': type(error).__name__,
                          'evidence': str(args.out / 'browser-summary.json')}))
        return 1


if __name__ == '__main__':
    install_signal_handlers()
    raise SystemExit(main())
