#!/usr/bin/env python3
"""Exercise the released-image deployment path with a supplied or locally built aiv CLI.

Requires Python 3.10+ and Docker (also Go when --aiv is omitted); no host Python
packages are needed. The default backend is seekdb, the image tag is dev and
cached images are reused.
Use --backend oceanbase for OceanBase CE. Set SEEKDB_IMAGE / OCEANBASE_IMAGE or
AIVISION_SEEKDB_SMOKE_IMAGES_JSON (signoz/gateway/backend/agentlens/frontend)
to use other images. This checks deployment, SQL/authentication, one real OTLP
trace with API readback, and persistence; it is not full Agent/framework acceptance.

  python3 deploy/tests/seekdb_deploy_smoke.py --out /tmp/seekdb-evidence

Release jobs must supply --aiv, --require-pinned-images, and
--require-native-memory, with all five service image overrides and the database
pinned by sha256 digest. The native memory canary verifies one real OTLP
operation and its hit count through the authenticated Labs API on both casts.
This candidate gate requires every running image to be Linux amd64 during both
initial deployment and recast; other image builds do not establish qualification.
Set AIVISION_RELEASE_MANIFEST_BASE64 to forward the verified release record and
check the installed product and responding backend identities during both casts.
When that verified record includes plugins.openshell, the supplied packaged CLI
must also pass automatic bootstrap/enrollment, explicit default binding, a real
sandbox Rule Trial, and independently checked cleanup after the final recast.
This deploys the CLI's embedded image blueprint, without source mounts or builds.
Source CI alone may select --source-baseline-legacy-health for older pinned
fixtures. That exports a private custom blueprint with four legacy HTTP probes,
and reads traces through their authenticated SigNoz v1 API, without strict
product application shutdown checks. It cannot be combined with
--require-pinned-images and is not product lifecycle acceptance.

Raw state and credentials live in a private temporary directory, never in the
evidence directory. --runtime-base must be shared with the Docker daemon.
"""

import argparse
import base64
import binascii
from datetime import datetime, timezone
import http.cookiejar
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid

from native_memory_canary import NativeMemoryCanary


SEEKDB_IMAGE = 'oceanbase/seekdb@sha256:500910bda197c0bc5f8bbada10c6ad727807308826818c0ea0391844dedc5cc2'
OCEANBASE_IMAGE = 'oceanbase/oceanbase-ce@sha256:eaa2c3b3513fa21e4a658f197c1deb93c9d6b6eb742cfa5ef0f085f82b9cec54'
CANDIDATE_PLATFORM = 'linux/amd64'
COMPONENTS = ('database', 'data-backend', 'ingest-gateway', 'backend', 'diagnosis', 'frontend')
CONTAINER_VARS = ('OB_CONTAINER', 'SIGNOZ_CONTAINER', 'GATEWAY_CONTAINER',
                  'BE_CONTAINER', 'AGENTLENS_CONTAINER', 'FE_CONTAINER')
PORT_VARS = ('OB_PORT', 'SIGNOZ_PORT', 'GATEWAY_GRPC_PORT', 'GATEWAY_HTTP_PORT',
             'GATEWAY_HEALTH_PORT', 'BE_PORT', 'AGENTLENS_PORT', 'FE_PORT')
IMAGE_VARS = dict(zip(('signoz', 'gateway', 'backend', 'agentlens', 'frontend'),
                      ('SIGNOZ_IMAGE', 'GATEWAY_IMAGE', 'BE_IMAGE', 'AGENTLENS_IMAGE', 'FE_IMAGE')))
SECRET_VARS = ('OB_TENANT_PASSWORD', 'OB_SYS_PASSWORD', 'SIGNOZ_INGEST_INTERNAL_KEY',
               'AIVISION_INGEST_SECRET', 'SECRET_KEY', 'AGENTLENS_TOKEN')

# Run from the application network, using the same installed driver as AgentLens.
# The password travels on stdin, never in docker's command-line arguments.
SQL_CHECK = '''import json, sys
import pymysql
options = json.load(sys.stdin)
connection = dict(host=options['host'], port=options.get('port', 2881), user=options.get('user', 'root'),
                  password=options['password'], charset='utf8mb4', connect_timeout=10)
with pymysql.connect(**connection, autocommit=True) as db:
    with db.cursor() as cursor:
        cursor.execute('SHOW DATABASES')
        databases = {row[0] for row in cursor.fetchall()}
        assert {'aivision', 'signoz', 'agentlens'} <= databases, 'application database missing'
        if options['write']:
            cursor.execute('CREATE TABLE IF NOT EXISTS aivision.aiv_seekdb_deploy_canary '
                           '(id VARCHAR(64) PRIMARY KEY, value VARCHAR(64) NOT NULL)')
            cursor.execute('INSERT INTO aivision.aiv_seekdb_deploy_canary (id, value) VALUES (%s, %s)',
                           (options['canary'], options['canary']))
        cursor.execute('SELECT value FROM aivision.aiv_seekdb_deploy_canary WHERE id=%s',
                       (options['canary'],))
        assert cursor.fetchone() == (options['canary'],), 'canary was lost'
try:
    empty = pymysql.connect(**dict(connection, password=''))
except pymysql.err.OperationalError as error:
    assert error.args[0] == 1045, 'empty password failed for an unexpected reason'
else:
    empty.close()
    raise AssertionError('empty root password was accepted')
print(json.dumps({'databases': ['agentlens', 'aivision', 'signoz'],
                  'password_login': True, 'empty_password_error': 1045, 'canary': True}))
'''


def ports():
    held = []
    try:
        for _ in PORT_VARS:
            sock = socket.socket()
            sock.bind(('127.0.0.1', 0))
            held.append(sock)
        return {key: str(sock.getsockname()[1]) for key, sock in zip(PORT_VARS, held)}
    finally:
        for sock in held:
            sock.close()


def private_vars(path, values):
    # JSON string scalars are valid YAML; no PyYAML dependency is required.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        stream.write(''.join(f'{key}: {json.dumps(str(value))}\n' for key, value in values.items()))


def legacy_stop_evidence(output, container_id):
    """Verify the exact old database, independent of the CLI's log verbosity."""
    events = [json.loads(line) for line in output.splitlines() if line.strip()]
    killed, exited = False, False
    for event in events:
        actor = event.get('Actor', {})
        if actor.get('ID', event.get('id')) != container_id:
            continue
        action = event.get('Action', event.get('status'))
        attributes = actor.get('Attributes', {})
        if action == 'kill' and str(attributes.get('signal')) in ('10', 'USR1', 'SIGUSR1'):
            killed = True
        if action == 'die' and killed and str(attributes.get('exitCode')) == '0':
            exited = True
    if not killed or not exited:
        raise AssertionError('old database events do not show SIGUSR1 and exit 0')
    return {'container_id': container_id, 'signal': 'SIGUSR1', 'exit_code': 0,
            'evidence': 'legacy-stop-events.jsonl'}


def database_config(backend, namespace):
    if backend == 'seekdb':
        return {'OB_IMAGE': os.environ.get('SEEKDB_IMAGE', SEEKDB_IMAGE), 'OB_USER': 'root'}, [
            'aivforge-seekdb-data-' + namespace]
    if backend == 'oceanbase':
        return {'OB_IMAGE': os.environ.get('OCEANBASE_IMAGE', OCEANBASE_IMAGE),
                'OB_TENANT': 'aivision', 'OB_USER': 'root@aivision'}, [
            'aivforge-ob-data-' + namespace, 'aivforge-ob-config-' + namespace]
    raise ValueError('backend must be oceanbase or seekdb')


def release_metadata_vars():
    encoded = os.environ.get('AIVISION_RELEASE_MANIFEST_BASE64', '')
    if not encoded:
        return {}
    try:
        if len(encoded) > 4 * ((65536 + 2) // 3):
            raise ValueError('oversized metadata')
        raw = base64.b64decode(encoded, validate=True)
        if (len(raw) > 65536 or base64.b64encode(raw).decode() != encoded
                or not isinstance(json.loads(raw), dict)):
            raise ValueError('invalid metadata')
    except (binascii.Error, UnicodeError, ValueError, RecursionError) as error:
        raise ValueError('release metadata must encode a bounded JSON object') from error
    return {'AIVISION_RELEASE_MANIFEST_BASE64': encoded}


def image_overrides(raw, database_image, *, require_pinned=False):
    images = json.loads(raw)
    if not isinstance(images, dict) or set(images) - IMAGE_VARS.keys() or any(
            not isinstance(value, str) or not value for value in images.values()):
        raise ValueError('image overrides must map signoz/gateway/backend/agentlens/frontend to image references')
    if require_pinned:
        if set(images) != IMAGE_VARS.keys():
            raise ValueError('release smoke requires all five service image overrides')
        if any(not re.fullmatch(r'[^\s@]+@sha256:[0-9a-f]{64}', image)
               for image in [database_image, *images.values()]):
            raise ValueError('release smoke requires sha256 digest references for all six images')
    return images


class Smoke:
    def __init__(self, out, runtime, values):
        self.out, self.runtime, self.values = out, runtime, values
        self.private = [values[key] for key in (*SECRET_VARS, 'SIGNOZ_USER_ROOT_PASSWORD')]
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(('AIV_VAR_', 'AIVISION_', 'AIV_'))}
        self.env.update(NO_COLOR='1',
                        NO_PROXY='127.0.0.1,localhost', no_proxy='127.0.0.1,localhost')

    def sanitize(self, value):
        value = str(value)
        for secret in self.private:
            value = value.replace(secret, '[REDACTED]')
        value = re.sub(r'sk-aiv-[A-Za-z0-9_-]+', '[REDACTED]', value)
        value = re.sub(r'aiv-sandbox-[A-Za-z0-9_-]+', '[REDACTED]', value)
        return re.sub(r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', '[REDACTED]', value)

    def run(self, label, command, *, timeout=120, check=True, stdin=None, cwd=None):
        print(f'[{label}]', flush=True)
        process = subprocess.Popen(command, cwd=cwd or self.runtime, env=self.env, text=True,
                                   stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            output, _ = process.communicate(stdin, timeout=timeout)
        except BaseException:
            # Terminate cast and its children before cleaning up its resources.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            output, _ = process.communicate()
            (self.out / (label + '.log')).write_text(self.sanitize(output), encoding='utf-8')
            raise
        (self.out / (label + '.log')).write_text(self.sanitize(output), encoding='utf-8')
        if check and process.returncode:
            raise RuntimeError(f'{label} failed (exit {process.returncode}); see {label}.log')
        return subprocess.CompletedProcess(command, process.returncode, output)

    def healthy(self, command, label):
        result = self.run(label, command)
        rows = {parts[0]: parts[1:4] for line in result.stdout.splitlines()
                if len(parts := line.split()) >= 4 and parts[0] in COMPONENTS}
        if rows != {name: ['container', 'running', 'ok'] for name in COMPONENTS}:
            raise AssertionError('aiv status did not report all six components running and healthy')
        state = self.run(label + '-containers', ['docker', 'inspect', '--format',
                         '{{json .State}}', *(self.values[key] for key in CONTAINER_VARS)])
        states = [json.loads(line) for line in state.stdout.splitlines()]
        if len(states) != 6 or any(not row['Running'] or row['OOMKilled'] for row in states):
            raise AssertionError('a container stopped or was OOM killed')
        url = 'http://127.0.0.1:' + self.values['FE_PORT'] + '/'
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url, timeout=15) as response:
            if response.status != 200:
                raise AssertionError('Web endpoint did not return HTTP 200')
        return {'components': list(COMPONENTS), 'web_http_status': 200, 'oom_killed': False}

    def sql(self, label, *, write=False):
        result = self.run(label, ['docker', 'exec', '-i', self.values['AGENTLENS_CONTAINER'],
                                'python', '-c', SQL_CHECK], stdin=json.dumps({
            'host': self.values.get('OB_HOST', self.values['OB_CONTAINER']),
            'port': int(self.values.get('OB_HOST_PORT', '2881')),
            'user': self.values.get('OB_USER', 'root'),
            'password': self.values['OB_TENANT_PASSWORD'],
            'canary': self.values['DOCKER_NETWORK'], 'write': write}))
        return json.loads(result.stdout)

    def database_identity(self, label):
        org_output = self.run(label + '-org', ['docker', 'exec', self.values['BE_CONTAINER'],
                              '/bin/sh', '-ec', 'if [ -x /app/manage ]; then exec /app/manage "$@"; fi; exec python manage.py "$@"', 'aiv-manage', 'signoz_integration_status',
                              '--require', '--print-org']).stdout
        # The command's final stdout value is a UUID; merged startup warnings
        # must never become deployment identity.
        candidates = [line.strip() for line in org_output.splitlines()
                      if re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', line.strip())]
        if len(set(candidates)) != 1:
            raise AssertionError('organization command did not return one unambiguous UUID')
        org = str(uuid.UUID(candidates[0]))
        mounts = json.loads(self.run(label + '-mounts', ['docker', 'inspect', '--format',
                            '{{json .Mounts}}', self.values['OB_CONTAINER']]).stdout)
        # Upgrading the CLI intentionally adds a read-only embedded lifecycle
        # script. Compare persistent database storage independently of code binds.
        namespace = self.values['NAMESPACE']
        expected = {'/var/lib/seekdb': 'aivforge-seekdb-data-' + namespace}
        if self.values.get('OB_BACKEND', 'seekdb') == 'oceanbase':
            expected = {'/root/ob': 'aivforge-ob-data-' + namespace,
                        '/root/.obd/cluster': 'aivforge-ob-config-' + namespace}
        persistent = sorted((mount['Destination'], mount.get('Name') or mount['Source'])
                            for mount in mounts if mount['Destination'] in expected)
        if persistent != sorted(expected.items()):
            raise AssertionError('database is not using the original named volume')
        return {'organization_id': org, 'database_mounts': persistent}

    def images(self, label, *, require_pinned=False, expected_local_ids=None):
        names = [self.values[key] for key in CONTAINER_VARS]
        deployed = self.run(label, ['docker', 'inspect', '--format',
                            '{{.Image}} {{.Config.Image}}', *names]).stdout.splitlines()
        if len(deployed) != len(COMPONENTS):
            raise AssertionError('missing deployed image identity')
        image_ids, refs = zip(*(line.split() for line in deployed))
        platforms = self.run(label + '-platforms', ['docker', 'image', 'inspect', '--format',
                             '{{.Os}}/{{.Architecture}} {{.Id}}', *image_ids]).stdout.splitlines()
        if len(platforms) != len(COMPONENTS):
            raise AssertionError('missing deployed image platform')
        if require_pinned or expected_local_ids is not None:
            for component, image_id, measured in zip(COMPONENTS, image_ids, platforms):
                if measured.split() != [CANDIDATE_PLATFORM, image_id]:
                    raise AssertionError('candidate image platform or identity differs for ' + component)
            expected = [self.values[key] for key in ('OB_IMAGE', *IMAGE_VARS.values())]
            if list(refs) != expected:
                raise AssertionError('deployed image reference differs from the candidate')
            resolved = self.run(label + '-expected', ['docker', 'image', 'inspect', '--format',
                                '{{.Id}}', *expected]).stdout.splitlines()
            if list(image_ids) != resolved:
                raise AssertionError('running image content differs from the selected references')
            if expected_local_ids is not None and dict(zip(COMPONENTS, image_ids)) != expected_local_ids:
                raise AssertionError('running image content differs from the prepared local candidate')
            mounts = self.run(label + '-mounts', ['docker', 'inspect', '--format',
                              '{{json .Mounts}}', *names]).stdout.splitlines()
            if len(mounts) != len(COMPONENTS):
                raise AssertionError('missing deployed mount information')
            for line in mounts:
                for mount in json.loads(line):
                    if mount['Type'] == 'bind' and not Path(mount['Source']).resolve().is_relative_to(self.runtime):
                        raise AssertionError('candidate smoke found a bind mount outside its private runtime')
        result = {'platforms': dict(zip(COMPONENTS, platforms)),
                  'references': dict(zip(COMPONENTS, refs)), 'pinned_content_verified': require_pinned}
        if expected_local_ids is not None:
            result['local_content_verified'] = True
        return result

    def cleanup(self, command, names, volumes, networks):
        errors = []
        try:
            result = self.run('destroy', command, timeout=240, check=False)
            if result.returncode:
                errors.append('CLI destroy failed; inspect destroy.log before treating cleanup as graceful')
        except Exception as error:
            errors.append(self.sanitize(error))
        # A failed cast may not have written state. The exact names below were
        # generated here and checked absent before cast; never use global prune.
        resources = ([('container', name) for name in names] + [('volume', volume) for volume in volumes]
                     + [('network', network) for network in networks])
        for kind, name in resources:
            try:
                remove = ['docker', 'rm', '-f', '-v', name] if kind == 'container' else ['docker', kind, 'rm', name]
                self.run('remove-' + name, remove, check=False)
                listing = ['docker', kind, 'ls', '-q', '--filter', 'name=^' + ('/' if kind == 'container' else '') + name + '$']
                if kind == 'container':
                    listing.insert(3, '-a')
                if self.run('check-removed-' + name, listing).stdout.strip():
                    errors.append(kind + ' still exists: ' + name)
            except Exception as error:
                errors.append(self.sanitize(error))
        return errors


class TraceCanary:
    """One real token-authenticated OTLP write and independent v1 API read."""

    def __init__(self, smoke, namespace, *, legacy_source=False):
        self.smoke, self.name = smoke, namespace
        self.legacy_source = legacy_source
        self.origin = 'http://127.0.0.1:' + smoke.values['FE_PORT']
        self.cookies = http.cookiejar.CookieJar()
        self.client = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                                  urllib.request.HTTPCookieProcessor(self.cookies))
        self.trace_id, self.span_id = secrets.token_hex(16), secrets.token_hex(8)

    def request(self, path, *, body=None, headers=None, origin=None, timeout=30):
        request_headers = {'Accept': 'application/json', 'Origin': self.origin, 'Referer': self.origin + '/'}
        if body is not None:
            request_headers.update({'Content-Type': 'application/json',
                                    'X-CSRFToken': next((c.value for c in self.cookies if c.name == 'csrftoken'), '')})
        request_headers.update(headers or {})
        request = urllib.request.Request((origin or self.origin) + path,
                                         data=json.dumps(body).encode() if body is not None else None,
                                         headers=request_headers, method='POST' if body is not None else 'GET')
        try:
            with self.client.open(request, timeout=timeout) as response:
                status, payload = response.status, json.loads(response.read() or b'{}')
        except urllib.error.HTTPError as error:
            # Never include arbitrary server response text or cookie values in evidence.
            with error:
                try:
                    problem = json.loads(error.read(4096))
                except (OSError, ValueError):
                    problem = None
            code = problem.get('code') if isinstance(problem, dict) else None
            return error.code, {'code': code} if type(code) is int else {}
        finally:
            self.smoke.private.extend(cookie.value for cookie in self.cookies)
        return status, payload

    def management(self, path, body=None, headers=None):
        status, payload = self.request(path, body=body, headers=headers)
        if not 200 <= status < 300 or payload.get('code') != 0:
            raise RuntimeError(f'management {path} failed (HTTP {status})')
        return payload['data']

    def version(self):
        """Check configured release and responding backend only, not container inventory."""
        manifest = json.loads(base64.b64decode(self.smoke.values['AIVISION_RELEASE_MANIFEST_BASE64']))
        expected = {key: manifest[key] for key in ('product_version', 'channel', 'source_sha', 'build_id')}
        fields = {'schema_version', 'component', 'product_version', 'component_version',
                  'source_sha', 'build_id', 'built_at', 'target_channel', 'dirty'}
        expected['components'] = {
            name: {key: value for key, value in manifest['component_builds'][name].items() if key in fields}
            for name in ('aivision-fe', 'aivision-be')}
        actual = self.management('/api/v1/version')
        if actual.get('expected') != expected:
            raise AssertionError('installed release API record does not match the selected manifest')
        if actual.get('backend') != expected['components']['aivision-be']:
            raise AssertionError('responding backend build does not match its selected image identity')
        return {'product_version': expected['product_version'], 'channel': expected['channel'],
                'installed_release': 'matched', 'responding_backend': 'matched',
                'scope': 'version API; frontend runtime and other containers not checked here'}

    def ingest(self, span_count=1):
        print('[register-and-ingest-trace]', flush=True)
        self.management('/api/v1/auth/csrf')
        password = self.smoke.values['SIGNOZ_USER_ROOT_PASSWORD']
        account = self.management('/api/v1/auth/register', {
            'email': self.name + '@example.invalid', 'name': 'AI Vision deployment smoke',
            'password': password, 'password_confirm': password})
        space = account['personal_space']['space_id']
        self.space = space
        self.management('/api/v1/auth/csrf')
        key = self.management(f'/api/v1/spaces/{space}/space-keys', {'name': self.name})['key']
        self.smoke.private.append(key)
        agent = self.management(f'/api/v1/spaces/{space}/agents/create',
                                {'agent_product': 'langfuse', 'name': self.name})
        self.space_id, self.agent_uuid = space, agent['agent_uuid']
        minted = self.management('/openapi/v1/ingest-tokens', {'agent_uuid': agent['agent_uuid']},
                                 {'Authorization': 'Bearer ' + key})
        if minted.get('agent_uuid') != agent['agent_uuid'] or minted.get('agent_product') != 'langfuse':
            raise AssertionError('ingest token has unexpected Agent identity')
        self.ingest_token = minted['token']
        self.smoke.private.append(self.ingest_token)
        self.basic = base64.b64encode((agent['agent_uuid'] + ':' + key).encode()).decode()
        self.smoke.private.append(self.basic)
        started = time.time_ns() - 2_000_000_000
        attributes = {
            'gen_ai.span.kind': 'CHAT', 'gen_ai.operation.name': 'invoke_agent',
            'gen_ai.conversation.id': self.name, 'langfuse.trace.name': self.name,
            'langfuse.observation.input': json.dumps({'request': 'AI Vision deployment canary'}),
            'langfuse.observation.output': json.dumps({'result': 'stored'})}
        payload = {'resourceSpans': [{
            'resource': {'attributes': [{'key': 'service.name', 'value': {'stringValue': self.name}}]},
            'scopeSpans': [{'scope': {'name': 'aiv-deploy-smoke'}, 'spans': [{
                'traceId': self.trace_id, 'spanId': self.span_id, 'name': self.name, 'kind': 2,
                'startTimeUnixNano': str(started), 'endTimeUnixNano': str(started + 1_000_000),
                'status': {'code': 1},
                'attributes': [{'key': key, 'value': {'stringValue': value}}
                               for key, value in attributes.items()]}]}]}]}
        spans = payload['resourceSpans'][0]['scopeSpans'][0]['spans']
        self.expected_spans = {self.span_id}
        for _ in range(span_count - 1):
            span_id = secrets.token_hex(8)
            self.expected_spans.add(span_id)
            spans.append(dict(spans[0], spanId=span_id, parentSpanId=self.span_id))
        status, response = self.request('/v1/traces', body=payload,
                                        headers={'Authorization': 'Bearer ' + self.ingest_token},
                                        origin='http://127.0.0.1:' + self.smoke.values['GATEWAY_HTTP_PORT'])
        if status != 200 or int(response.get('partialSuccess', {}).get('rejectedSpans', 0)):
            raise AssertionError(f'OTLP canary ingestion failed (HTTP {status})')

    def read(self):
        print('[read-trace-canary]', flush=True)
        deadline = time.monotonic() + 120
        while True:
            if self.legacy_source:
                # The fixed October 1 source fixture predates native Langfuse
                # reads. Use its existing session-authenticated platform API;
                # release candidates must still pass the native public API.
                status, payload = self.request(
                    f'/api/v1/spaces/{self.space_id}/signoz/trace/{self.trace_id}/detail'
                    f'?agent_product=langfuse&agent_uuid={self.agent_uuid}')
                trace = {}
                if status == 200:
                    if payload.get('code') != 0:
                        raise RuntimeError('legacy trace readback returned an API error')
                    data = payload.get('data') or {}
                    spans = data.get('spans', [])
                    root = next((span for span in spans if span.get('span_id') == self.span_id), None)
                    if root is None:
                        status = 404
                    else:
                        trace = {'id': data.get('traceId'), 'name': root.get('name'),
                                 'observations': [{'id': span.get('span_id')} for span in spans]}
            else:
                status, trace = self.request('/api/public/traces/' + self.trace_id,
                                             headers={'Authorization': 'Basic ' + self.basic})
            if status == 200:
                if trace.get('id') != self.trace_id or trace.get('name') != self.name:
                    raise AssertionError('trace API returned unexpected canary identity')
                if len(self.expected_spans) > 1:
                    observed = {observation['id'] for observation in trace.get('observations', [])}
                    if not self.expected_spans <= observed:
                        if time.monotonic() >= deadline:
                            raise AssertionError('trace readback lost spans from the authenticated burst')
                        time.sleep(2)
                        continue
                return {'trace_id': self.trace_id, 'span_id': self.span_id,
                        'verified_spans': len(self.expected_spans),
                        'ingest': 'gateway OTLP JSON',
                        'readback': 'SigNoz v1 (legacy source fixture)' if self.legacy_source else 'Langfuse v1',
                        'http_status': 200}
            if status not in (404, 500, 502, 503) or time.monotonic() >= deadline:
                raise RuntimeError(f'trace readback failed (HTTP {status})')
            time.sleep(2)


def openshell_catalog(values):
    """Optional verified release capability; absence preserves five-service smoke."""
    encoded = values.get('AIVISION_RELEASE_MANIFEST_BASE64')
    if not encoded:
        return None
    manifest = json.loads(base64.b64decode(encoded, validate=True))
    plugins = manifest.get('plugins', {})
    if not isinstance(plugins, dict):
        raise ValueError('release plugin metadata is invalid')
    if 'openshell' not in plugins:
        return None
    catalog = plugins['openshell']
    if (not isinstance(catalog, dict) or set(catalog) != {
            'schema_version', 'product_version', 'provider_version', 'protocol_version', 'images'}
            or type(catalog['schema_version']) is not int or catalog['schema_version'] != 1
            or type(catalog['protocol_version']) is not int or catalog['protocol_version'] != 1
            or not isinstance(catalog['product_version'], str) or not catalog['product_version']
            or catalog['provider_version'] != '0.1.6' or not isinstance(catalog['images'], dict)
            or not catalog['images'] or len(json.dumps(catalog)) > 16384):
        raise ValueError('release OpenShell catalog is unavailable or invalid')
    roles = {'plugin', 'worker', 'gateway', 'runtime', 'supervisor'}
    for platform, images in catalog['images'].items():
        if (platform not in ('linux/amd64', 'linux/arm64') or not isinstance(images, dict)
                or set(images) != roles or any(not isinstance(image, str) or not re.fullmatch(
                    r'[^\s@]+@sha256:[0-9a-f]{64}', image) for image in images.values())):
            raise ValueError('release OpenShell images must be platform-specific immutable references')
    return catalog


OPENSHELL_LEASE_CHECK = '''import json, sys
import pymysql
options = json.load(sys.stdin)
with pymysql.connect(host=options['host'], port=options['port'], user=options['user'],
                     password=options['password'], charset='utf8mb4', connect_timeout=10) as db:
    with db.cursor() as cursor:
        cursor.execute('SELECT state, cleanup_verified, task_uuid, diagnoser_uuid '
                       'FROM agentlens.agentlens_sandbox_lease '
                       'WHERE connection_uuid=%s AND space_uuid=%s',
                       (options['connection_uuid'], options['space_uuid']))
        rows = cursor.fetchall()
assert rows, 'Rule Trial did not create a persisted Sandbox lease'
assert all(state == 'closed' and verified == 1 and task.startswith('trial-') and diagnoser == 'trial'
           for state, verified, task, diagnoser in rows), 'Sandbox lease cleanup is not verified'
print(json.dumps({'count': len(rows), 'closed': len(rows), 'cleanup_verified': True}))
'''

OPENSHELL_INVENTORY = '''import json
from agentlens.sandbox_plugin.config import PluginSettings
from agentlens.sandbox_plugin.provider import OpenShellProvider
try:
    settings = PluginSettings.from_env()
    assert settings.tls_ca and settings.tls_cert and settings.tls_key
    provider = OpenShellProvider(settings)
    try:
        client = provider._get_client()
        sandboxes = client.list_all(workspace=settings.workspace)
        templates = client.sandbox_templates().list_all(workspace=settings.workspace)
        assert not sandboxes and not templates
        print(json.dumps({'sandboxes': 0, 'templates': 0, 'mtls': True}))
    finally:
        provider.close()
except Exception:
    raise SystemExit('Authenticated gateway inventory is not empty or could not be verified') from None
'''


class OpenShellCanary:
    """Released CLI -> real enrollment/default -> isolated Rule Trial -> guarded cleanup."""

    def __init__(self, smoke, trace, cli, platform):
        self.smoke, self.trace, self.cli = smoke, trace, Path(cli)
        self.catalog = openshell_catalog(smoke.values)
        platform = platform.replace('/aarch64', '/arm64').replace('/x86_64', '/amd64')
        if self.catalog is None or platform not in self.catalog['images']:
            raise AssertionError('release OpenShell catalog has no image set for the actual daemon')
        self.images = self.catalog['images'][platform]
        self.started, self.verified, self.cleaned = False, False, False
        self.preserve_runtime = False
        self.connection_uuid = self.session_uuid = self.plan_id = None
        target = smoke.run('openshell-target', ['docker', 'container', 'inspect', '--format',
                            '{{.Id}} {{.Config.Hostname}}', smoke.values['BE_CONTAINER']]).stdout.split()
        if len(target) != 2 or not re.fullmatch('[0-9a-f]{64}', target[0]) or not re.fullmatch('[0-9a-f]{12,64}', target[1]):
            raise AssertionError('OpenShell smoke requires the actual post-recast backend identity')
        self.backend_id = target[0]
        self.deployment_key = hashlib.sha256(target[1].encode()).hexdigest()[:16]
        self.service_name = 'aiv-openshell-' + self.deployment_key
        self.gateway_name = self.service_name + '-gateway'
        self.config_root = smoke.runtime / 'openshell-cli'
        self.owned = self.config_root / 'deployments' / self.deployment_key / 'plugins/openshell'
        self.state_dir = self.owned / 'installation'
        self.base = f'/api/v1/spaces/{trace.space}/integrations'

    def _key_private(self):
        path = self.owned / 'plugin-token'
        if path.is_file() and not path.is_symlink():
            key = path.read_text(encoding='utf-8').strip()
            if key and len(key) <= 8192:
                self.smoke.private.append(key)

    def _owned_state(self):
        state = json.loads((self.owned / 'setup.json').read_text())
        plugin = json.loads((self.state_dir / 'installation.json').read_text())
        if (state.get('plan_id') != self.plan_id or state.get('host', {}).get('backend_id') != self.backend_id
                or plugin.get('image') != self.images['plugin']
                or any(not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value)
                       for value in (state.get('gateway_id'), plugin.get('container_id')))):
            raise AssertionError('OpenShell owned state does not match the released candidate and target')
        self.gateway_id, self.plugin_id = state['gateway_id'], plugin['container_id']

    def run(self):
        capability = self.trace.management(self.base + '/sandbox-bootstrap')
        if (capability.get('available') is not True
                or capability.get('product_version') != self.catalog['product_version']
                or capability.get('provider_version') != self.catalog['provider_version']):
            raise AssertionError('released backend did not advertise its verified OpenShell catalog')
        before = self.trace.management(self.base + '/sandbox-binding')
        if before.get('connection_uuid') is not None or type(before.get('require_sandbox')) is not bool:
            raise AssertionError('OpenShell smoke must use its fresh unbound canary Space')
        grant = self.trace.management(self.base + '/sandbox-installations', {'operation': 'bootstrap'})
        tokens = [grant.get(key) for key in ('bootstrap_token', 'report_token', 'enrollment_token')]
        # Register every capability before any subprocess or command evidence.
        self.smoke.private.extend(token for token in tokens if isinstance(token, str) and token)
        if any(not isinstance(token, str) or not token or len(token) > 4096 for token in tokens):
            raise AssertionError('bootstrap API did not return three bounded purpose-separated capabilities')
        self.session_uuid = grant.get('session', {}).get('uuid')
        if not isinstance(self.session_uuid, str) or not re.fullmatch('[0-9a-f-]{32,36}', self.session_uuid):
            raise AssertionError('bootstrap API returned an invalid session identity')
        path = '/api/public/sandbox-installations/' + self.session_uuid
        if grant.get('bootstrap_path') != path:
            raise AssertionError('bootstrap API returned a different machine-session path')
        previous_config = self.smoke.env.get('AIVISION_CONFIG_DIR')
        self.smoke.env['AIVISION_CONFIG_DIR'] = str(self.config_root)
        self.started, self.preserve_runtime = True, True
        try:
            self.smoke.run('openshell-setup', [str(self.cli), 'plugin', 'setup', 'openshell',
                           '--session-url', self.trace.origin + path,
                           '--bootstrap-token', tokens[0], '--progress-token', tokens[1],
                           '--enrollment-token', tokens[2]], timeout=1800)
        finally:
            self._key_private()
            if previous_config is None:
                self.smoke.env.pop('AIVISION_CONFIG_DIR', None)
            else:
                self.smoke.env['AIVISION_CONFIG_DIR'] = previous_config
        session = self.trace.management(self.base + '/sandbox-installation/' + self.session_uuid)
        self.connection_uuid, self.plan_id = session.get('connection_uuid'), session.get('bootstrap_plan_id')
        if (session.get('status') != 'ready' or session.get('registration_state') != 'saved'
                or not isinstance(self.connection_uuid, str) or not re.fullmatch('[0-9a-f]{32}', self.connection_uuid)
                or not isinstance(self.plan_id, str) or not re.fullmatch('[0-9a-f]{64}', self.plan_id)):
            raise AssertionError('released CLI did not complete real provider readiness and connection enrollment')
        self._owned_state()
        enabled = self.trace.management(self.base + '/sandbox-installation/' + self.session_uuid + '/enable', {})
        after = self.trace.management(self.base + '/sandbox-binding')
        if (enabled.get('connection_uuid') != self.connection_uuid
                or enabled.get('binding', {}).get('connection_uuid') != self.connection_uuid
                or after.get('connection_uuid') != self.connection_uuid
                or after.get('require_sandbox') != before['require_sandbox']):
            raise AssertionError('explicit OpenShell default binding did not read back or changed require_sandbox')
        code = ('def evaluate(trace):\n'
                '    return {"metrics": [{"name": "span_count", "verdict": "good", '
                '"reason": "isolated release worker completed", "evidence": {"value": len(trace["spans"])}}]}')
        trial = {'space_uuid': self.trace.space,
                 'spec': {'code': code, 'metrics': [{'name': 'span_count'}], 'sandbox': {'mode': 'inherit'}},
                 'test_input': {'trace_id': 'openshell-release-smoke', 'runtime_type': 'other',
                                'spans': [{'span_id': str(index), 'name': 'release-canary', 'attributes': {}}
                                          for index in range(13)]}}
        status, response = self.trace.request('/api/v1/diagnoser/rule-trial', body=trial,
            headers={'x-antmonitor-token': self.smoke.values['AGENTLENS_TOKEN']},
            origin='http://127.0.0.1:' + self.smoke.values['AGENTLENS_PORT'], timeout=180)
        execution = response.get('execution', {})
        evidence = execution.get('evidence', {})
        metrics = response.get('metrics', [])
        if (status != 200 or response.get('skipped') is not False or response.get('verdict') != 'good'
                or execution.get('status') != 'succeeded' or execution.get('code') not in ('', None)
                or evidence.get('backend') != 'sandbox' or evidence.get('provider') != 'openshell'
                or evidence.get('connection_uuid') != self.connection_uuid or len(metrics) != 1
                or metrics[0].get('name') != 'span_count' or metrics[0].get('verdict') != 'good'
                or metrics[0].get('evidence', {}).get('value') != 13):
            raise AssertionError('actual OpenShell Rule Trial failed or did not use the enrolled sandbox worker')
        enhanced = self._enhanced_trial()
        self._closed_resources()
        self.verified = True
        return {'status': 'passed', 'session_uuid': self.session_uuid, 'connection_uuid': self.connection_uuid,
                'plan_id': self.plan_id, 'references': self.images,
                'registration': 'saved', 'explicit_default_readback': True,
                'require_sandbox_preserved': True,
                'execution': {'backend': 'sandbox', 'provider': 'openshell', 'span_count': 13},
                'enhanced_runtime': enhanced,
                'cleanup_verified': True, 'scope': 'actual packaged CLI, HTTP APIs, Rule Trial and scoped cleanup'}

    def _enhanced_trial(self):
        runtime = self.trace.management(self.base + '/sandbox-connection/' + self.connection_uuid + '/runtime')
        manifests = runtime.get('runtime_manifests', [])
        candidates = [value for value in manifests if isinstance(value, dict)
                      and value.get('id') == 'python-eval-v1' and value.get('image') == self.images['worker']]
        if (runtime.get('ready') is not True or runtime.get('connection_uuid') != self.connection_uuid
                or 'python_eval_v1' not in runtime.get('capabilities', []) or len(candidates) != 1):
            raise AssertionError('actual enhanced worker did not advertise its qualified image inventory')
        manifest = candidates[0]
        packages = manifest.get('packages')
        canonical = {key: manifest.get(key) for key in ('id', 'python_version', 'packages')}
        digest = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
        if (manifest.get('manifest_sha256') != digest or not isinstance(packages, dict)
                or not {'pytest', 'numpy', 'pandas', 'pydantic', 'jsonschema', 'openpyxl'} <= packages.keys()):
            raise AssertionError('actual enhanced runtime manifest does not match its library inventory')
        code = '''import io
import json
from pathlib import Path
import numpy as np
import pandas as pd
import jsonschema
from pydantic import BaseModel
from openpyxl import Workbook, load_workbook

class Total(BaseModel):
    total: float

def evaluate(trace, context):
    tests = context.run_python_tests("def add(a, b):\\n    return a + b\\n", "tests/test_candidate.py")
    csv_total = float(pd.read_csv(io.StringIO(context.read_text("sales.csv")))["amount"].sum())
    value = Total.model_validate({"total": csv_total})
    jsonschema.validate({"total": value.total}, json.loads(context.read_text("schema.json")))
    path = Path(context.work_dir) / "result.xlsx"
    book = Workbook()
    book.active["B2"] = value.total
    book.save(path)
    book.close()
    book = load_workbook(path, read_only=True, data_only=True)
    try:
        xlsx_total = book.active["B2"].value
    finally:
        book.close()
    verified = tests["verdict"] == "GOOD" and tests["passed"] == 1 and bool(np.isfinite(csv_total)) and csv_total == xlsx_total == 30
    return {"metrics": [{"name": "enhanced_evaluation", "verdict": "good" if verified else "bad", "reason": "Reference tests and offline data verification", "evidence": {"tests_passed": tests["passed"], "csv_total": csv_total, "xlsx_total": xlsx_total, "verified": verified}}]}
'''
        files = [
            {'path': 'tests/test_candidate.py', 'encoding': 'utf-8',
             'content': 'from candidate import add\n\ndef test_add():\n    assert add(1, 2) == 3\n'},
            {'path': 'sales.csv', 'encoding': 'utf-8', 'content': 'amount\n10\n20\n'},
            {'path': 'schema.json', 'encoding': 'utf-8',
             'content': '{"type":"object","required":["total"],"properties":{"total":{"type":"number"}}}'},
        ]
        trial = {'space_uuid': self.trace.space,
                 'spec': {'code': code, 'metrics': [{'name': 'enhanced_evaluation'}],
                          'sandbox': {'mode': 'inherit'}, 'rule_runtime': 'python-eval-v1',
                          'rule_runtime_manifest': digest, 'materials': {'files': files}},
                 'test_input': {'trace_id': 'enhanced-release-smoke', 'runtime_type': 'other', 'spans': []}}
        status, response = self.trace.request('/api/v1/diagnoser/rule-trial', body=trial,
            headers={'x-antmonitor-token': self.smoke.values['AGENTLENS_TOKEN']},
            origin='http://127.0.0.1:' + self.smoke.values['AGENTLENS_PORT'], timeout=180)
        execution = response.get('execution', {})
        evidence = execution.get('evidence', {})
        metrics = response.get('metrics', [])
        if (status != 200 or response.get('verdict') != 'good' or execution.get('status') != 'succeeded'
                or evidence.get('backend') != 'sandbox' or evidence.get('provider') != 'openshell'
                or evidence.get('connection_uuid') != self.connection_uuid or len(metrics) != 1
                or metrics[0].get('name') != 'enhanced_evaluation' or metrics[0].get('verdict') != 'good'
                or metrics[0].get('evidence', {}).get('verified') is not True
                or metrics[0].get('evidence', {}).get('tests_passed') != 1
                or metrics[0].get('evidence', {}).get('csv_total') != 30
                or metrics[0].get('evidence', {}).get('xlsx_total') != 30):
            raise AssertionError('actual enhanced Rule Trial failed code/library/data verification')
        return {'id': manifest['id'], 'manifest_sha256': digest, 'packages': packages,
                'reference_tests': 'passed', 'json_csv_xlsx': 'passed'}

    def _closed_resources(self):
        status = json.loads(self.smoke.run('openshell-status', [str(self.cli), 'plugin', 'status', 'openshell',
                               '--state-dir', str(self.state_dir), '--format', 'json']).stdout)
        health = status.get('health') or {}
        if status.get('installed') is not True or health.get('ready') is not True or health.get('active_leases') != 0:
            raise AssertionError('OpenShell service has active or unverifiable leases')
        options = {'host': self.smoke.values.get('OB_HOST', self.smoke.values['OB_CONTAINER']),
                   'port': int(self.smoke.values.get('OB_HOST_PORT', '2881')),
                   'user': self.smoke.values.get('OB_USER', 'root'),
                   'password': self.smoke.values['OB_TENANT_PASSWORD'],
                   'connection_uuid': self.connection_uuid, 'space_uuid': self.trace.space}
        leases = json.loads(self.smoke.run('openshell-leases', ['docker', 'exec', '-i',
            self.smoke.values['AGENTLENS_CONTAINER'], 'python', '-c', OPENSHELL_LEASE_CHECK],
            stdin=json.dumps(options)).stdout)
        if (type(leases.get('count')) is not int or leases['count'] < 1
                or leases.get('closed') != leases['count'] or leases.get('cleanup_verified') is not True):
            raise AssertionError('actual persisted Rule Trial leases did not verify closed cleanup')
        inventory = json.loads(self.smoke.run('openshell-gateway-inventory', ['docker', 'exec', self.plugin_id,
            '/app/.venv/bin/python', '-c', OPENSHELL_INVENTORY], timeout=60).stdout)
        if inventory != {'sandboxes': 0, 'templates': 0, 'mtls': True}:
            raise AssertionError('authenticated OpenShell gateway resources remain')
        for kind in ('container', 'volume'):
            command = ['docker', kind, 'ls', '--quiet', '--filter', 'label=openshell.sandbox_namespace=' + self.service_name]
            if kind == 'container':
                command.insert(3, '--all')
            if self.smoke.run('openshell-' + kind + '-resources', command).stdout.strip():
                raise AssertionError('actual owned OpenShell Docker resources remain after Rule Trial')

    def _gateway_owner(self):
        template = ('{"Id":{{json .Id}},"Config":{"Image":{{json .Config.Image}},'
                    '"Labels":{{json .Config.Labels}}}}')
        gateway = json.loads(self.smoke.run('openshell-gateway-owner', ['docker', 'container', 'inspect',
                             '--format', template, self.gateway_id]).stdout)
        labels = gateway.get('Config', {}).get('Labels', {})
        if (gateway.get('Id') != self.gateway_id or gateway.get('Config', {}).get('Image') != self.images['gateway']
                or labels.get('io.aivision.sandbox.bootstrap') != self.deployment_key
                or labels.get('io.aivision.sandbox.plan') != self.plan_id
                or labels.get('io.aivision.sandbox.role') != 'gateway'):
            raise AssertionError('owned OpenShell gateway identity changed; refusing cleanup')

    def cleanup(self):
        if not self.started or self.cleaned:
            return []
        if not self.verified:
            return ['OpenShell acceptance incomplete; owned resources and private runtime retained']
        try:
            self._closed_resources()
            self._gateway_owner()
            self.smoke.run('openshell-remove', [str(self.cli), 'plugin', 'remove', 'openshell',
                           '--state-dir', str(self.state_dir)], timeout=180)
            self._gateway_owner()
            self.smoke.run('openshell-gateway-stop', ['docker', 'stop', '--timeout', '30', self.gateway_id], timeout=45)
            stopped = json.loads(self.smoke.run('openshell-gateway-stopped', ['docker', 'container', 'inspect',
                                 '--format', '{{json .State}}', self.gateway_id]).stdout)
            if stopped.get('Running') is not False or stopped.get('ExitCode') != 0 or stopped.get('OOMKilled') is not False:
                raise AssertionError('owned OpenShell gateway did not stop cleanly; preserving it')
            self.smoke.run('openshell-gateway-remove', ['docker', 'rm', self.gateway_id])
            if self.smoke.run('openshell-gateway-removed', ['docker', 'container', 'ls', '--all', '--quiet',
                              '--filter', 'id=' + self.gateway_id]).stdout.strip():
                raise AssertionError('owned OpenShell gateway still exists after removal')
        except Exception as error:
            return ['OpenShell cleanup failed: ' + self.smoke.sanitize(error)]
        self.cleaned, self.preserve_runtime = True, False
        return []


def source_baseline_blueprint(smoke, cli):
    """Export a private legacy source fixture, never a product candidate."""
    source = smoke.run('export-source-blueprint', [str(cli), 'gen', '-o', '-']).stdout
    marker = r'^metadata:\n((?:  [^\n]*\n)*?)  product: aivision\n'
    source, count = re.subn(marker, lambda match: 'metadata:\n' + match[1], source, flags=re.MULTILINE)
    if count != 1 or re.search(r'^  product: aivision$', source, flags=re.MULTILINE):
        raise ValueError('Legacy source baseline requires exactly one metadata product marker')
    for executable, arguments, endpoint in (
        ('/app/signoz', ', "--ready"', 'http://127.0.0.1:${SIGNOZ_PORT}/api/v1/health'),
        ('/app/collector', ', "--ready"', 'http://127.0.0.1:${GATEWAY_HEALTH_PORT}/debug/pprof/'),
    ):
        # gen emits the embedded source verbatim. Refuse missing or ambiguous
        # commands rather than silently weakening a different future blueprint.
        pattern = (r'^( +)execArgv: \["' + re.escape(executable) + r'", "healthcheck"' +
                   re.escape(arguments) + r'\]$')
        source, count = re.subn(pattern, lambda match: match[1] + 'http: ' + endpoint,
                                source, flags=re.MULTILINE)
        if count != 1:
            raise ValueError('Legacy source baseline requires exactly one native probe for ' + executable)
    for current, legacy in (
        ('http://127.0.0.1:${BE_PORT}/api/v1/ready', 'http://127.0.0.1:${BE_PORT}/api/v1/health'),
        ('http://127.0.0.1:${AGENTLENS_PORT}/internal/readyz', 'http://127.0.0.1:${AGENTLENS_PORT}/internal/healthz'),
    ):
        source, count = re.subn(r'^( +)http: ' + re.escape(current) + r'$',
                                lambda match: match[1] + 'http: ' + legacy, source, flags=re.MULTILINE)
        if count != 1:
            raise ValueError('Legacy source baseline requires exactly one readiness probe for ' + current)
    blueprint = smoke.runtime / 'source-baseline-blueprint.yaml'
    blueprint.write_text(source)
    blueprint.chmod(0o600)
    return blueprint


def main(argv=None, *, local_images=None, live_check=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--out', type=Path, required=True, help='new or empty directory for sanitized evidence')
    parser.add_argument('--runtime-base', type=Path, default=Path(tempfile.gettempdir()))
    parser.add_argument('--aiv', type=Path, help='use this binary instead of compiling the current checkout')
    parser.add_argument('--baseline-aiv', type=Path, help='install with an older CLI, then recast with the candidate CLI')
    parser.add_argument('--volume-root', type=Path, help='optional daemon-local directory for the temporary named volume')


    parser.add_argument('--backend', choices=('seekdb', 'oceanbase'), default='seekdb')
    parser.add_argument('--require-pinned-images', action='store_true',
                        help='require a supplied CLI and all six immutable candidate image references')
    parser.add_argument('--require-native-memory', action='store_true',
                        help='read back one OTLP native memory operation after initial cast and recast')
    parser.add_argument('--source-baseline-legacy-health', action='store_true',
                        help='source CI only: private legacy HTTP/custom lifecycle fixture; strict product shutdown is not validated')
    args = parser.parse_args(argv)
    if args.source_baseline_legacy_health and args.require_pinned_images:
        parser.error('--source-baseline-legacy-health cannot be combined with --require-pinned-images')
    if args.source_baseline_legacy_health and args.require_native_memory:
        parser.error('--require-native-memory cannot be combined with --source-baseline-legacy-health')
    if args.baseline_aiv and args.require_pinned_images:
        parser.error('--baseline-aiv cannot be combined with --require-pinned-images')
    if args.require_pinned_images and (not args.aiv or not args.aiv.is_file() or not os.access(args.aiv, os.X_OK)):
        parser.error('--require-pinned-images requires --aiv pointing to an executable release candidate')
    if local_images is not None and (args.require_pinned_images or args.baseline_aiv
                                    or args.source_baseline_legacy_health or not args.aiv
                                    or not args.aiv.is_file() or not os.access(args.aiv, os.X_OK)):
        parser.error('local candidate requires a packaged CLI and the product deployment path')
    def interrupted(signum, _frame):
        raise InterruptedError(f'interrupted by signal {signum}')
    signal.signal(signal.SIGTERM, interrupted)
    cli_source = Path(__file__).resolve().parents[2] / 'aivision-cli'
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        parser.error('--out must be empty')
    namespace = 'aiv-' + args.backend + '-smoke-' + uuid.uuid4().hex[:12]
    database, volumes = database_config(args.backend, namespace)
    expected_local_ids = None
    try:
        if local_images is None:
            raw_images = image_overrides(os.environ.get('AIVISION_SEEKDB_SMOKE_IMAGES_JSON', '{}'),
                                        database['OB_IMAGE'], require_pinned=args.require_pinned_images)
            metadata_values = release_metadata_vars()
        else:
            refs, expected_local_ids = local_images
            database['OB_IMAGE'] = refs['database']
            raw_images = {key: refs[key] for key in IMAGE_VARS}
            metadata_values = {}
        if openshell_catalog(metadata_values) is not None and not args.aiv:
            parser.error('OpenShell artifact acceptance requires --aiv pointing to the supplied packaged CLI')
    except ValueError as error:
        parser.error(str(error))
    values = dict(ports(), **database, NAMESPACE=namespace, OB_BACKEND=args.backend, OB_MANAGED='true', IMAGE_PULL='missing',
                  DOCKER_NETWORK=namespace + '-net', BIND_ADDR='127.0.0.1', PUBLIC_HOST='127.0.0.1',
                  SIGNOZ_ROOT_EMAIL=namespace + '@example.invalid', SIGNOZ_ROOT_ORG_NAME=namespace,
                  AIVISION_NATIVE_TRACE_HISTORY_START=datetime.now(timezone.utc).strftime('%Y-%m-%dT00:00:00Z'))
    values.update({key: namespace + '-' + component for key, component in zip(CONTAINER_VARS, COMPONENTS)})
    values.update({key: secrets.token_hex(24) for key in SECRET_VARS})
    values['SIGNOZ_USER_ROOT_PASSWORD'] = 'Ci-9' + secrets.token_hex(24) + '!'
    values.update({IMAGE_VARS[key]: image for key, image in raw_images.items()})
    values.update(metadata_values)
    runtime = Path(tempfile.mkdtemp(prefix=namespace + '-', dir=args.runtime_base)).resolve()
    runtime.chmod(0o700)
    smoke = Smoke(out, runtime, values)
    if local_images is not None:
        smoke.env['DOCKER_DEFAULT_PLATFORM'] = CANDIDATE_PLATFORM
    cli = args.aiv.resolve() if args.aiv else runtime / 'aiv'
    variables = runtime / 'vars.yaml'
    private_vars(variables, values)
    common = ['--namespace', namespace, '--workdir', str(runtime / 'work')]
    blueprint_flags = []
    managed = [*common, '--vars-file', str(variables)]
    names = [values[key] for key in CONTAINER_VARS]
    network = values['DOCKER_NETWORK']
    owned_names, owned_networks = list(names), [network]
    if args.backend == 'seekdb':
        owned_names.append(values['OB_CONTAINER'] + '-initialize')
        owned_networks.append(values['OB_CONTAINER'] + '-initialize-net')
    owned = False
    openshell = None
    volume_directory = None
    summary = {'status': 'failed', 'namespace': namespace, 'backend': args.backend,
               'database_image': values['OB_IMAGE'], 'image_overrides': raw_images,
               'image_tag': None if args.require_pinned_images or local_images is not None else 'dev',
               'cli_source': 'supplied binary' if args.aiv else 'local checkout',
               'health_probe_mode': 'legacy-source-baseline-http' if args.source_baseline_legacy_health else 'native',
               'lifecycle_mode': 'legacy-source-fixture' if args.source_baseline_legacy_health else 'product',
               'strict_product_shutdown_checked': not args.source_baseline_legacy_health,
               'history_start': values['AIVISION_NATIVE_TRACE_HISTORY_START'],
               'scope': ('CLI deployment, database authentication, one OTLP trace and v1 readback, recast persistence'
                         + ('; deployed MiniMax checks' if live_check else '; full Agent/framework acceptance not run'))}
    if args.backend == 'seekdb':
        summary['seekdb_image'] = values['OB_IMAGE']
    try:
        summary['docker_server_platform'] = smoke.run(
            'docker-info', ['docker', 'info', '--format', '{{.OSType}}/{{.Architecture}}']).stdout.strip()
        if not args.aiv:
            smoke.run('build-cli', ['go', 'build', '-mod=vendor', '-o', str(cli), '.'], cwd=cli_source, timeout=300)
        if args.source_baseline_legacy_health:
            blueprint_flags = ['--file', str(source_baseline_blueprint(smoke, cli))]
            common.extend(blueprint_flags)
            managed = [*common, '--vars-file', str(variables)]
        # Fail on any collision before taking ownership of the resource names.
        resources = ([('container', name) for name in owned_names] + [('volume', volume) for volume in volumes]
                     + [('network', name) for name in owned_networks])
        for kind, name in resources:
            result = smoke.run('check-absent-' + name, ['docker', kind, 'inspect', '--format', '{{.Name}}', name], check=False)
            if result.returncode == 0:
                raise RuntimeError('refusing to reuse existing ' + kind + ': ' + name)
        owned = True
        if args.volume_root:
            volume_directory = Path(tempfile.mkdtemp(prefix=namespace + '-', dir=args.volume_root)).resolve()
            for index, volume in enumerate(volumes):
                directory = volume_directory / str(index)
                directory.mkdir()
                smoke.run('create-volume-' + str(index), ['docker', 'volume', 'create', '--driver', 'local',
                          '--opt', 'type=none', '--opt', 'o=bind', '--opt', 'device=' + str(directory),
                          '--label', 'aivision.deploy-smoke=' + namespace, volume])
        smoke.run('create-network', ['docker', 'network', 'create', '--label', 'aivision.deploy-smoke=' + namespace, network])
        # Host-owned directories make deletion possible after container writes.
        for path in ('logs/be-runtime', 'logs/agentlens', 'state/signoz-metastore'):
            (runtime / 'work' / namespace / path).mkdir(parents=True, exist_ok=True)
        initial_cli = args.baseline_aiv.resolve() if args.baseline_aiv else cli
        cast = [str(initial_cli), 'cast', *managed]
        if not args.require_pinned_images and local_images is None:
            cast.extend(['--var', 'IMAGE_TAG=dev'])
        smoke.run('cast', cast, timeout=1200)
        summary['initial_images'] = smoke.images('deployed-images', require_pinned=args.require_pinned_images,
                                                 expected_local_ids=expected_local_ids)
        summary['deployed_images'] = summary['initial_images']['platforms']
        summary['initial_health'] = smoke.healthy([str(cli), 'status', *common], 'status')
        summary['initial_database'] = smoke.sql('database-canary', write=True)
        initial_entrypoint = json.loads(smoke.run('initial-database-entrypoint', [
            'docker', 'inspect', '--format', '{{json .Config.Entrypoint}}', values['OB_CONTAINER']]).stdout)
        initial_database_id = smoke.run('initial-database-id', [
            'docker', 'inspect', '--format', '{{.Id}}', values['OB_CONTAINER']]).stdout.strip()
        initial_identity = smoke.database_identity('initial')
        summary['identity'] = initial_identity
        trace = TraceCanary(smoke, namespace, legacy_source=args.source_baseline_legacy_health)
        trace.ingest()
        if metadata_values:
            summary['initial_version'] = trace.version()
        summary['initial_trace'] = trace.read()
        if args.require_native_memory:
            memory = NativeMemoryCanary(trace.request, space_id=trace.space_id,
                                        agent_product='langfuse', agent_uuid=trace.agent_uuid)
            memory.ingest('http://127.0.0.1:' + values['GATEWAY_HTTP_PORT'], trace.ingest_token)
            summary['initial_memory'] = memory.read()
        if live_check is not None:
            summary['initial_minimax'] = live_check('initial', smoke, trace, cli)
        # Recast uses persisted overrides, exercising real restart semantics.
        recast = [str(cli), 'cast', *common]
        persisted = runtime / 'work' / namespace / 'state/vars.json'
        if args.baseline_aiv:
            # Consume old saved keys, while accepting a new role key in the
            # highest-precedence layer without changing image or storage.
            recast.extend(['--var', 'DATA_BACKEND_IMAGE=' + json.loads(persisted.read_text())['SIGNOZ_IMAGE']])
        event_client = None
        old_database_events = ''
        if args.baseline_aiv and initial_entrypoint == ['/usr/bin/seekdb']:
            event_client = subprocess.Popen(
                ['docker', 'events', '--since', str(int(time.time()) - 1),
                 '--filter', 'container=' + initial_database_id,
                 '--filter', 'event=kill', '--filter', 'event=die', '--format', '{{json .}}'],
                env=smoke.env, cwd=runtime, text=True, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, start_new_session=True)
        try:
            smoke.run('recast', recast, timeout=1200)
        finally:
            if event_client is not None:
                stopped_early = event_client.poll() is not None
                try:
                    event_client.terminate()
                except ProcessLookupError:
                    pass
                try:
                    old_database_events, event_errors = event_client.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    event_client.kill()
                    old_database_events, event_errors = event_client.communicate()
                (out / 'legacy-stop-events.jsonl').write_text(smoke.sanitize(old_database_events), encoding='utf-8')
                (out / 'legacy-stop-events.stderr.log').write_text(smoke.sanitize(event_errors), encoding='utf-8')
                if stopped_early:
                    raise AssertionError('Docker event collector exited before recast completed')
        summary['recast_images'] = smoke.images('recast-images', require_pinned=args.require_pinned_images,
                                                expected_local_ids=expected_local_ids)
        summary['recast_health'] = smoke.healthy([str(cli), 'status', *common], 'recast-status')
        summary['recast_database'] = smoke.sql('recast-database')
        if metadata_values:
            summary['recast_version'] = trace.version()
        summary['recast_trace'] = trace.read()
        if args.require_native_memory:
            summary['recast_memory'] = memory.read()
        summary['recast_identity'] = smoke.database_identity('recast')
        if summary['recast_identity'] != initial_identity:
            raise AssertionError('CLI upgrade changed organization identity or database mounts')
        if live_check is not None:
            summary['recast_minimax'] = live_check('recast', smoke, trace, cli)
        if openshell_catalog(smoke.values) is not None:
            openshell = OpenShellCanary(smoke, trace, cli, summary['docker_server_platform'])
            summary['openshell'] = openshell.run()
        if args.baseline_aiv:
            legacy_stop = legacy_stop_evidence(old_database_events, initial_database_id) if event_client else None
            for selector in ('signoz', 'data-backend'):
                smoke.run('alias-logs-' + selector, [str(cli), 'logs', selector, *common, '-n', '2'])
            summary['existing_cli_compatibility'] = {
                'baseline_binary': str(initial_cli), 'legacy_saved_config': True,
                'role_config_alias': True, 'old_and_new_log_selectors': True,
                'baseline_database_entrypoint': initial_entrypoint,
                'legacy_sigusr1_shutdown': legacy_stop,
                'legacy_shutdown_boundary': 'old native PID 1 receives SIGUSR1; upgrade requires exit 0 and retained data readback',
                'organization_and_database_mounts_preserved': True}
        if persisted.stat().st_mode & 0o077:
            raise AssertionError('persisted vars are readable outside the owner')
        external = json.loads(persisted.read_text(encoding='utf-8'))
        external.update(OB_MANAGED='false', OB_HOST=values['OB_CONTAINER'], OB_HOST_PORT='2881')
        external_vars = runtime / 'external-vars.yaml'
        private_vars(external_vars, external)
        smoke.run('external-gauge', [str(cli), 'gauge', '--namespace', namespace + '-external',
                  '--workdir', str(runtime / 'work'), *blueprint_flags, '--vars-file', str(external_vars)], timeout=180)
        summary['external_gauge'] = True
        # Wrong external credentials must be rejected, never repaired by
        # changing a database that this deployment does not manage.
        wrong_password = secrets.token_hex(24)
        smoke.private.append(wrong_password)
        rejected_vars = runtime / 'external-wrong-password.yaml'
        private_vars(rejected_vars, dict(external, OB_TENANT_PASSWORD=wrong_password))
        rejected = smoke.run('external-reject-wrong-password', [str(cli), 'gauge', '--namespace',
                            namespace + '-rejected', '--workdir', str(runtime / 'work'), *blueprint_flags,
                            '--vars-file', str(rejected_vars)], timeout=180, check=False)
        if rejected.returncode == 0 or '1045' not in rejected.stdout:
            raise AssertionError('external database did not reject wrong credentials with 1045')
        summary['external_wrong_password_error'] = 1045
        summary['external_database_unchanged'] = smoke.sql('external-database')
        summary['status'] = 'passed'
    except Exception as error:
        summary['error'] = smoke.sanitize(error)
        if owned:
            for name in names:
                try:
                    smoke.run('failure-' + name, ['docker', 'logs', '--tail', '100', name], check=False)
                except Exception:
                    pass
    finally:
        openshell_errors = openshell.cleanup() if openshell is not None else []
        if openshell is not None:
            summary['openshell_cleanup_errors'] = openshell_errors
        preserve = openshell is not None and openshell.preserve_runtime
        if preserve:
            # Pending leases or unverifiable provider resources retain their DB,
            # network, journal, credentials and exact owned containers together.
            summary['cleanup_errors'] = openshell_errors
            summary['private_runtime_retained'] = str(runtime)
        else:
            summary['cleanup_errors'] = openshell_errors + (smoke.cleanup(
                [str(cli), 'destroy', *managed, '--purge', '-y'], owned_names, volumes, owned_networks) if owned else [])
            try:
                if volume_directory and not summary['cleanup_errors']:
                    smoke.run('cleanup-volume-files', ['docker', 'run', '--rm', '--network', 'none',
                              '--mount', 'type=bind,source=' + str(volume_directory) + ',target=/fixture',
                              '--entrypoint', '/bin/sh', values['OB_IMAGE'], '-c',
                              'find /fixture -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +'])
                    shutil.rmtree(volume_directory)
                shutil.rmtree(runtime)
            except OSError as error:
                summary['cleanup_errors'].append(smoke.sanitize(error))
        if summary['cleanup_errors']:
            summary['status'] = 'failed'
        (out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(summary, indent=2))
    return 0 if summary['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
