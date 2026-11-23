"""Pure deployment-contract tests; never start Docker or contact a platform."""
from contextlib import contextmanager, nullcontext, redirect_stdout
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import httpx
import yaml

import docker_platform as platform


ROOT = Path(__file__).resolve().parents[4]
IMAGE_REPOS = {
    'oceanbase': 'oceanbase/oceanbase-ce', 'signoz': 'ghcr.io/ob-labs/signoz-ob',
    'gateway': 'ghcr.io/ob-labs/antcollector-gw', 'backend': 'ghcr.io/ob-labs/aivision-be',
    'agentlens': 'ghcr.io/ob-labs/agentlens', 'frontend': 'ghcr.io/ob-labs/aivision-fe',
}
IMAGES = {key: repo + '@sha256:' + 'a' * 64 for key, repo in IMAGE_REPOS.items()}
PASSWORDS = {key: 'synthetic-secret-' + key for key in (
    'OB_TENANT_PASSWORD', 'OB_SYS_PASSWORD', 'SIGNOZ_INGEST_INTERNAL_KEY',
    'AIVISION_INGEST_SECRET', 'SECRET_KEY', 'AGENTLENS_TOKEN', 'SIGNOZ_USER_ROOT_PASSWORD')}
PORTS = {key: str(20000 + i) for i, key in enumerate((
    'OB_PORT', 'SIGNOZ_PORT', 'GATEWAY_GRPC_PORT', 'GATEWAY_HTTP_PORT',
    'GATEWAY_HEALTH_PORT', 'BE_PORT', 'AGENTLENS_PORT', 'FE_PORT'))}


class DockerPlatformContractTests(unittest.TestCase):
    def test_full_public_candidate_service_set_and_legacy_fixtures(self):
        digest = '@sha256:' + 'a' * 64
        public = {**IMAGES, **{key: repo + digest for key, repo in platform.PUBLIC_SERVICE_REPOS.items()}}
        self.assertEqual(platform.image_refs(json.dumps(public)), public)
        self.assertEqual(platform.image_refs(json.dumps(IMAGES)), IMAGES)
        with self.assertRaisesRegex(ValueError, 'official sha256'):
            platform.image_refs(json.dumps({**public, 'gateway': IMAGES['gateway']}))

    def test_every_onboarding_bootstrap_is_available_in_candidate_backend(self):
        source = yaml.safe_load((ROOT/'deploy/blueprints/aivision.yaml').read_text(encoding='utf-8'))
        plan = platform.deployment_blueprint(source, ROOT, ROOT/'runtime-fixture', 'aiv-e2e-test',
                                             IMAGES, PASSWORDS, PORTS, '2026-10-04T00:00:00Z')
        backend = next(component for component in plan['components'] if component['name'] == 'be')
        mounted = {}
        for mount in backend['mounts']:
            parts = dict(part.split('=', 1) for part in mount.split(',') if '=' in part)
            if parts.get('target'):
                mounted[parts['target']] = parts.get('source')
        plugins = ROOT/'ai-vision/agent-plugins'
        for key in ('langfuse', 'nvidia-nemo-relay'):
            for example in (plugins/key/'examples').glob('*.yaml'):
                bootstrap = yaml.safe_load(example.read_text(encoding='utf-8')).get('bootstrapFile')
                if not bootstrap:
                    continue
                with self.subTest(example=example.name):
                    target = '/agent-plugins/' + key + '/' + bootstrap
                    self.assertEqual(mounted.get(target), str(plugins/key/bootstrap),
                                     'Pinned images must receive current onboarding helper source')
                    self.assertTrue(Path(mounted[target]).is_file())

    def test_legacy_source_baseline_has_real_http_probes_without_changing_native_blueprint(self):
        source = yaml.safe_load((ROOT/'aivision-cli/blueprints/aivision.yaml').read_text(encoding='utf-8'))
        original = {item['name']: item['health'] for item in source['components']}
        plan = platform.deployment_blueprint(source, ROOT, ROOT/'runtime-fixture', 'aiv-e2e-test',
                                             IMAGES, PASSWORDS, PORTS, '2026-10-04T00:00:00Z',
                                             legacy_source_baseline=True)
        health = {item['name']: item['health'] for item in plan['components']}
        self.assertEqual(health['signoz-ob'], {'http': 'http://127.0.0.1:${SIGNOZ_PORT}/api/v1/health',
                                               'retries': 40, 'interval': '3s'})
        self.assertEqual(health['antcollector-gw'], {'http': 'http://127.0.0.1:${GATEWAY_HEALTH_PORT}/debug/pprof/',
                                                    'retries': 30, 'interval': '2s'})
        self.assertEqual(health['agentlens'], {'http': 'http://127.0.0.1:${AGENTLENS_PORT}/internal/healthz',
                                              'retries': 60, 'interval': '3s'})
        self.assertEqual(original['agentlens']['http'],
                         'http://127.0.0.1:${AGENTLENS_PORT}/internal/readyz')
        self.assertEqual(health['be'], original['be'],
                         'Current mounted backend source must keep its real readiness probe')
        for name in ('signoz-ob', 'antcollector-gw'):
            self.assertNotIn('execArgv', health[name])
            self.assertIn('execArgv', original[name])
        unchanged = platform.deployment_blueprint(source, ROOT, ROOT/'runtime-fixture', 'aiv-e2e-test',
                                                  IMAGES, PASSWORDS, PORTS, '2026-10-04T00:00:00Z')
        self.assertEqual({item['name']: item['health'] for item in unchanged['components']}, original)
        self.assertEqual({item['name']: item['health'] for item in source['components']}, original)

    def test_candidate_blueprint_uses_owned_overlay_without_changing_official_base(self):
        self.assertIn('signoz_candidate', __import__('inspect').signature(platform.deployment_blueprint).parameters,
                      'Fresh runner deployment must use the tested precision candidate')
        source = yaml.safe_load((ROOT/'deploy/blueprints/aivision.yaml').read_text(encoding='utf-8'))
        plan = platform.deployment_blueprint(source, ROOT, ROOT/'runtime-fixture', 'aiv-e2e-test',
                                             IMAGES, PASSWORDS, PORTS, '2026-10-04T00:00:00Z',
                                             signoz_candidate='aivision-signoz-e2e:aiv-e2e-test')
        self.assertEqual(plan['config']['vars']['SIGNOZ_IMAGE'], 'aivision-signoz-e2e:aiv-e2e-test')
        self.assertEqual(IMAGES['signoz'], 'ghcr.io/ob-labs/signoz-ob@sha256:' + 'a'*64)
        self.assertEqual(platform.image_refs(json.dumps(IMAGES)), IMAGES)
        with self.assertRaises(ValueError):
            platform.deployment_blueprint(source, ROOT, ROOT/'runtime-fixture', 'aiv-e2e-test',
                                          IMAGES, PASSWORDS, PORTS, '2026-10-04T00:00:00Z',
                                          signoz_candidate='arbitrary.example/signoz:latest')

    @unittest.skipIf(os.name == 'nt', 'Windows Docker is excluded; POSIX shell contract only')
    def test_all_sdk_environments_execute_the_whole_suite_with_one_worker(self):
        self.assertIn('sdk', __import__('inspect').signature(platform.sdk_suite_command).parameters,
                      'Temporary stack must exercise both pinned Langfuse SDK environments')
        for sdk in ('lf3', 'lf4', 'lf5'):
            with self.subTest(sdk=sdk), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root/'uv').write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$SDK_TEST_LOG"\n'
                                      'if [ "$1" = venv ]; then /bin/mkdir -p "$4/bin"; '
                                      '/bin/cp "$SDK_TEST_PYTHON" "$4/bin/python"; fi\n')
                (root/'uv').chmod(0o755)
                (root/'python').write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$SDK_TEST_LOG"\n')
                (root/'python').chmod(0o755)
                command = platform.sdk_suite_command(ROOT, Path('/artifacts'), 'aiv-e2e-test', sdk=sdk)
                script = command[-1].replace('/tmp/aivision-framework-e2e', str(root/'venv'))
                env = {'PATH': str(root), 'SDK_TEST_LOG': str(root/'commands'), 'SDK_TEST_PYTHON': str(root/'python')}
                execution = subprocess.run(['/bin/sh', '-ec', script], env=env, text=True, capture_output=True)
                self.assertEqual(execution.returncode, 0, execution.stderr)
                calls = (root/'commands').read_text(encoding='utf-8').splitlines()
                self.assertIn('requirements-' + sdk + '.txt', calls[1])
                self.assertTrue(calls[2].endswith('--out /artifacts/cases --workers 1' + (' --sdks langfuse' if sdk == 'lf5' else '')))
                self.assertEqual(command[command.index('--name') + 1], 'aiv-e2e-test-sdk-' + sdk)

    def test_summary_cannot_accept_the_wrong_sdk_environment(self):
        self.assertIn('sdk', __import__('inspect').signature(platform.suite_passed).parameters,
                      'The same SDK4 suite cannot satisfy both SDK gates')
        case = {'execute_exit': 0, 'verify_exit': 0, 'source_verified': True, 'platform_verified': True}
        rows = [{**case, 'case': f'{framework}-{sdk}-{scenario}'}
                for framework in ('langchain', 'deepagents') for sdk in ('langfuse', 'nemo-relay')
                for scenario in ('success', 'no-results', 'retrieval-error', 'retry', 'changed-corpus',
                                 'zero-usage', 'unknown-usage', 'stream', 'cancel', 'concurrent', 'tool-exception')]
        summary = {'status': 'passed', 'mode': 'ci', 'sdk_coverage': 'full', 'data_backend': 'oceanbase', 'expected_cases': 44,
                   'executed_cases': 44, 'cases': rows, 'langfuse_version': '4.16.0', 'sdks': ['langfuse', 'nemo-relay']}
        self.assertTrue(platform.suite_passed(summary, sdk='lf4'))
        self.assertFalse(platform.suite_passed(summary, sdk='lf3'))
        self.assertTrue(platform.suite_passed({**summary, 'langfuse_version': '3.15.0'}, sdk='lf3'))
        lf5 = {**summary, 'langfuse_version': '5.0.2', 'sdks': ['langfuse'],
               'cases': [row for row in rows if '-langfuse-' in row['case']],
               'expected_cases': 22, 'executed_cases': 22}
        self.assertTrue(platform.suite_passed(lf5, sdk='lf5'))
        self.assertFalse(platform.suite_passed({**lf5, 'sdks': ['nemo-relay']}, sdk='lf5'))
        self.assertFalse(platform.suite_passed({**lf5, 'sdks': []}, sdk='lf5'))
        self.assertFalse(platform.suite_passed({**lf5, 'cases': lf5['cases'][:-1]}, sdk='lf5'))
        self.assertFalse(platform.suite_passed(lf5, sdk='lf4'))
        self.assertFalse(platform.suite_passed(summary, sdk='lf5'))
        self.assertFalse(platform.suite_passed({**summary, 'langfuse_version': None}, sdk='other'))

    def test_docker_smoke_uses_only_the_pinned_sdk4_environment(self):
        command = platform.sdk_suite_command(ROOT, Path('/artifacts'), 'aiv-e2e-test', coverage='smoke')
        self.assertIn('requirements-lf4.txt', command[-1])
        self.assertIn('--sdk-coverage smoke', command[-1])
        self.assertNotIn('--scenarios', command[-1])
        for sdk in ('lf3', 'lf5'):
            with self.subTest(sdk=sdk), self.assertRaises(ValueError):
                platform.sdk_suite_command(ROOT, Path('/artifacts'), 'aiv-e2e-test', sdk=sdk, coverage='smoke')

    def test_smoke_summary_requires_exact_coverage_and_success_for_all_cases(self):
        good = {'execute_exit': 0, 'verify_exit': 0, 'source_verified': True, 'platform_verified': True}
        cases = [{**good, 'case': f'{framework}-{sdk}-{scenario}'}
                 for framework in ('langchain', 'deepagents') for sdk in ('langfuse', 'nemo-relay')
                 for scenario in ('success', 'unknown-usage', 'stream', 'cancel', 'concurrent')]
        summary = {'status': 'passed', 'mode': 'ci', 'data_backend': 'oceanbase', 'expected_cases': 20,
                   'executed_cases': 20, 'cases': cases, 'langfuse_version': '4.16.0',
                   'sdks': ['langfuse', 'nemo-relay'], 'sdk_coverage': 'smoke'}
        self.assertTrue(platform.suite_passed(summary, sdk='lf4', coverage='smoke'))
        self.assertFalse(platform.suite_passed(summary, sdk='lf4'))
        self.assertFalse(platform.suite_passed(summary, sdk='lf4', coverage='unknown'))
        for invalid in ({'sdk_coverage': 'full'}, {'sdk_coverage': None}, {'langfuse_version': '5.0.2'},
                        {'data_backend': 'seekdb'}, {'cases': cases[1:] + cases[1:2]},
                        {'cases': [{**cases[0], 'case': 'langchain-langfuse-retry'}, *cases[1:]]},
                        {'cases': [{**cases[0], 'source_verified': False}, *cases[1:]]},
                        {'cases': [{**cases[0], 'platform_verified': False}, *cases[1:]]}):
            self.assertFalse(platform.suite_passed({**summary, **invalid}, sdk='lf4', coverage='smoke'))

    def test_sdk_suite_uses_pinned_linux_container_and_private_environment(self):
        self.assertTrue(hasattr(platform.Smoke, 'sdk_suite'), 'SDK acceptance must run inside Linux Docker')
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)/'artifacts'
            out.mkdir()
            targets = {'langfuse': {'space_key': 'synthetic-private-key', 'data_backend': 'oceanbase'}}
            smoke = platform.Smoke(out, {'PATH': '/tools', 'DOCKER_HOST': 'unix:///docker.sock'},
                                   {'synthetic-private-key'})
            with patch.object(smoke, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')) as run:
                execution = smoke.sdk_suite(ROOT, 'aiv-e2e-test', targets)
            command = run.call_args.args[1]
            self.assertEqual(command[:2], ['docker', 'run'])
            self.assertEqual(command[command.index('--name') + 1], 'aiv-e2e-test-sdk-lf4')
            self.assertEqual(command[command.index('--platform') + 1], 'linux/amd64')
            self.assertEqual(command[command.index('--network') + 1], 'host')
            self.assertEqual(command[command.index('--entrypoint') + 1], '/bin/sh')
            self.assertRegex(command[-3], r'^ghcr\.io/astral-sh/uv@sha256:[0-9a-f]{64}$')
            self.assertNotIn(IMAGES['backend'], command)
            self.assertIn('aivision.framework-e2e.namespace=aiv-e2e-test', command)
            mounts = [command[i + 1] for i, flag in enumerate(command) if flag == '--mount']
            self.assertEqual(mounts, [f'type=bind,source={ROOT},target=/workspace,readonly',
                                      f'type=bind,source={out / "sdk" / "lf4"},target=/artifacts'])
            forwarded = [command[i + 1] for i, flag in enumerate(command) if flag == '--env']
            self.assertEqual(forwarded, ['AIVISION_E2E_TARGETS_JSON'])
            self.assertNotIn('synthetic-private-key', json.dumps(command))
            env = run.call_args.kwargs['env']
            self.assertEqual(json.loads(env['AIVISION_E2E_TARGETS_JSON']), targets)
            self.assertEqual(env['DOCKER_HOST'], 'unix:///docker.sock')
            self.assertEqual(execution.returncode, 0)
            self.assertTrue((out/'sdk'/'lf4').is_dir())

    @unittest.skipIf(os.name == 'nt', 'Windows Docker is excluded; POSIX shell contract only')
    def test_sdk_launch_failure_reports_its_exit_and_preserves_sanitized_log(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            docker = root / 'docker'
            docker.write_text('#!/bin/sh\nprintf \"missing uv with synthetic-private-key\\n\" >&2\nexit 127\n')
            docker.chmod(0o755)
            smoke = platform.Smoke(root, {'PATH': str(root)}, {'synthetic-private-key'})
            with self.assertRaisesRegex(RuntimeError, r'api-suite-lf3 failed \(exit 127\)'):
                smoke.sdk_suite(ROOT, 'aiv-e2e-test', {}, sdk='lf3')
            self.assertFalse((root / 'sdk/lf3/cases/summary.json').exists())
            self.assertEqual((root / 'api-suite-lf3.log').read_text(), 'missing uv with [REDACTED]\n')

    @unittest.skipIf(os.name == 'nt', 'Windows Docker is excluded; POSIX shell contract only')
    def test_sdk_container_fails_before_installation_without_uv(self):
        self.assertTrue(hasattr(platform, 'sdk_suite_command'), 'SDK command requires a runtime uv precheck')
        command = platform.sdk_suite_command(ROOT, Path('/artifacts'), 'aiv-e2e-test')
        script = command[-1]
        process = subprocess.run(['/bin/sh', '-ec', script], env={'PATH': '/unavailable'},
                                 text=True, capture_output=True)
        self.assertNotEqual(process.returncode, 0)
        self.assertIn('uv', process.stderr)

    @unittest.skipIf(os.name == 'nt', 'Windows Docker is excluded; POSIX shell contract only')
    def test_sdk_container_selects_python312_and_executes_current_suite(self):
        self.assertTrue(hasattr(platform, 'sdk_suite_command'), 'SDK runner must select Python 3.12')
        command = platform.sdk_suite_command(ROOT, Path('/artifacts'), 'aiv-e2e-test')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'uv').write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$SDK_TEST_LOG"\n'
                                  'if [ "$1" = venv ]; then /bin/mkdir -p "$4/bin"; '
                                  '/bin/cp "$SDK_TEST_PYTHON" "$4/bin/python"; fi\n')
            (root/'uv').chmod(0o755)
            (root/'python').write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$SDK_TEST_LOG"\n'
                                      'printf "%s" "$AIVISION_E2E_DATA_BACKEND" > "$SDK_TEST_BACKEND"\n')
            (root/'python').chmod(0o755)
            script = command[-1].replace('/tmp/aivision-framework-e2e', str(root/'venv'))
            env = {'PATH': str(root), 'SDK_TEST_LOG': str(root/'commands'),
                   'SDK_TEST_PYTHON': str(root/'python'), 'SDK_TEST_BACKEND': str(root/'backend')}
            process = subprocess.run(['/bin/sh', '-ec', script], env=env, text=True, capture_output=True)
            self.assertEqual(process.returncode, 0, process.stderr)
            calls = (root/'commands').read_text(encoding='utf-8').splitlines()
            self.assertEqual(calls[0], f'venv --python 3.12 {root}/venv')
            self.assertEqual(calls[1], 'pip install --python ' + str(root/'venv/bin/python') +
                             ' -r /workspace/ai-vision/agent-plugins/tests/framework_e2e/requirements-lf4.txt')
            self.assertEqual(calls[2], '/workspace/ai-vision/agent-plugins/tests/framework_e2e/suite.py '
                             '--mode ci --repo /workspace --out /artifacts/cases --workers 1')
            self.assertEqual((root/'backend').read_text(encoding='utf-8'), 'oceanbase')

    def test_sdk_cleanup_checks_ownership_before_removal(self):
        with tempfile.TemporaryDirectory() as directory:
            smoke = platform.Smoke(Path(directory), {}, set())
            calls = []
            def run(label, command, **kwargs):
                calls.append(command)
                stdout = 'container-id\n' if command[:3] == ['docker', 'ps', '-aq'] else 'another-owner\n'
                return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr='')
            with patch.object(smoke, 'run', side_effect=run):
                errors = smoke.cleanup(Path(directory)/'missing', Path(directory)/'missing-plan',
                                       'aiv-e2e-test', ['aiv-e2e-test-sdk'])
            self.assertTrue(errors, 'foreign SDK containers must not be removed')
            self.assertFalse(any(command[:3] == ['docker', 'rm', '-f'] for command in calls))

    def test_sdk_cleanup_removes_owned_container_after_uncertain_run_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            smoke = platform.Smoke(Path(directory), {}, set())
            calls = []
            listings = iter(['container-id\n', ''])
            def run(label, command, **kwargs):
                calls.append(command)
                stdout = (next(listings) if command[:3] == ['docker', 'ps', '-aq'] else
                          'aiv-e2e-test\n' if command[:2] == ['docker', 'inspect'] else '')
                return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr='')
            with patch.object(smoke, 'run', side_effect=run):
                errors = smoke.cleanup(Path(directory)/'missing', Path(directory)/'missing-plan',
                                       'aiv-e2e-test', ['aiv-e2e-test-sdk'])
            self.assertEqual(errors, [])
            removal = ['docker', 'rm', '-f', '-v', 'aiv-e2e-test-sdk']
            self.assertIn(removal, calls)
            inspect_index = next(i for i, command in enumerate(calls) if command[:2] == ['docker', 'inspect'])
            self.assertLess(inspect_index, calls.index(removal))
            self.assertEqual(sum(command[:3] == ['docker', 'ps', '-aq'] for command in calls), 2)

    def test_runtime_base_is_shared_home_on_supported_deployment_platforms(self):
        home = Path('/synthetic-home')
        with patch.object(platform.Path, 'home', return_value=home):
            for system in ('Darwin', 'Linux'):
                with self.subTest(system=system):
                    self.assertEqual(platform.runtime_base(system), home)
            with self.assertRaises(ValueError):
                platform.runtime_base('Windows')

    def test_generated_signoz_password_always_satisfies_character_policy(self):
        # A hex-only draw is valid randomness but cannot satisfy this policy.
        with patch.object(platform.secrets, 'token_hex', return_value='a' * 48):
            passwords = platform.deployment_passwords()
        self.assertEqual(set(passwords), set(platform.SECRET_VARS))
        password = passwords['SIGNOZ_USER_ROOT_PASSWORD']
        self.assertGreaterEqual(len(password), 12)
        self.assertTrue(any(c.isupper() for c in password))
        self.assertTrue(any(c.islower() for c in password))
        self.assertTrue(any(c.isdigit() for c in password))
        self.assertTrue(any(not c.isalnum() for c in password))

    def test_private_runtime_stays_outside_artifacts_and_cleans_up_on_error(self):
        # Substitute a sandbox-owned home: do not create files in real HOME.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home, artifacts = root/'home', root/'artifacts'
            home.mkdir()
            artifacts.mkdir()
            with patch.object(platform.Path, 'home', return_value=home), \
                    patch.object(platform.platform, 'system', return_value='Darwin'):
                with self.assertRaisesRegex(RuntimeError, 'actual failure'):
                    with platform.private_runtime('aiv-e2e-test') as runtime:
                        self.assertEqual(runtime.parent, home)
                        self.assertFalse(runtime.is_relative_to(artifacts))
                        for component in ('be-runtime', 'agentlens'):
                            logdir = runtime/'work'/'aiv-e2e-test'/'logs'/component
                            self.assertTrue(logdir.is_dir())
                            (logdir/'service.log').write_text('container log')
                        (runtime/'secret-state').write_text('synthetic-private-value')
                        raise RuntimeError('actual failure')
                self.assertFalse(runtime.exists())
                self.assertEqual(list(artifacts.iterdir()), [])

    def test_explicit_runtime_base_preserves_parent_permissions_and_removes_owned_child(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); base = root / "validation"; base.mkdir(mode=0o755)
            before = base.stat().st_mode
            # Use a synthetic system temp root so the fixture represents a validation directory.
            with patch.object(platform.platform, 'system', return_value='Linux'), \
                    patch.object(platform, "system_tmp_roots", return_value=(root / "system-tmp",)):
                with self.assertRaisesRegex(RuntimeError, "stage failed"):
                    with platform.private_runtime("aiv-e2e-test", base=base, repo=root / "repo") as runtime:
                        self.assertEqual(runtime.parent, base)
                        (runtime / "private-state").write_text("synthetic")
                        raise RuntimeError("stage failed")
            self.assertEqual(base.stat().st_mode, before)
            self.assertFalse(runtime.exists())
            self.assertEqual(list(base.iterdir()), [])

    @unittest.skipUnless(os.name == 'posix' and hasattr(os, 'getuid'),
                         'Requires POSIX directory ownership and permission semantics')
    def test_private_runtime_directories_have_private_posix_ownership_and_modes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); base = root / 'validation'; base.mkdir(mode=0o755)
            before = base.stat().st_mode
            with patch.object(platform.platform, 'system', return_value='Linux'), \
                    patch.object(platform, 'system_tmp_roots', return_value=(root / 'system-tmp',)):
                with platform.private_runtime('aiv-e2e-test', base=base, repo=root / 'repo') as runtime:
                    paths = [runtime, *(runtime/'work'/'aiv-e2e-test'/'logs'/component
                                        for component in ('be-runtime', 'agentlens'))]
                    for path in paths:
                        self.assertEqual(path.stat().st_uid, os.getuid())
                        self.assertEqual(path.stat().st_mode & 0o777, 0o700)
            self.assertEqual(base.stat().st_mode, before)

    def test_explicit_runtime_base_rejects_repo_system_tmp_symlinks_and_non_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); repo = root / "repo"; repo.mkdir()
            (repo / ".git").touch()
            system_tmp = root / "system-tmp"; system_tmp.mkdir()
            base = root / "validation"; base.mkdir()
            link = root / "link"; link.symlink_to(base, target_is_directory=True)
            file = root / "file"; file.touch()
            for invalid in (repo, system_tmp, link, link / "child", file, root / "missing", Path("relative")):
                with self.subTest(invalid=invalid), patch.object(platform, "system_tmp_roots", return_value=(system_tmp,)):
                    with self.assertRaises(ValueError):
                        with platform.private_runtime("aiv-e2e-test", base=invalid, repo=repo):
                            self.fail("unsafe runtime was allocated")
            self.assertEqual(list(base.iterdir()), [])

    def test_every_image_is_explicit_official_digest(self):
        self.assertEqual(platform.image_refs(json.dumps(IMAGES)), IMAGES)
        for bad in ('{}', '[]', '', json.dumps({**IMAGES, 'backend': 'evil.example/be@sha256:' + 'a' * 64}),
                    json.dumps({**IMAGES, 'oceanbase': 'oceanbase/oceanbase-ce:latest'})):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                platform.image_refs(bad)

    def test_generated_blueprint_preserves_real_bootstrap_and_image_venv(self):
        source = yaml.safe_load((ROOT/'deploy/blueprints/aivision.yaml').read_text(encoding='utf-8'))
        plan = platform.deployment_blueprint(source, ROOT, ROOT/'runtime-fixture', 'aiv-e2e-abc',
                                             IMAGES, PASSWORDS, PORTS, '2026-10-04T00:00:00Z')
        self.assertEqual(source['metadata']['namespace'], 'aivision')
        self.assertEqual(plan['metadata']['namespace'], 'aiv-e2e-abc')
        components = {item['name']: item for item in plan['components']}
        self.assertEqual(set(components), {'oceanbase', 'signoz-ob', 'antcollector-gw', 'be', 'agentlens', 'fe'})
        be = components['be']
        self.assertEqual(be['postStart'], next(c for c in source['components'] if c['name'] == 'be')['postStart'])
        mounts = be['mounts']
        for name in ('apps', 'aivision_backend', 'manage.py'):
            self.assertTrue(any(f'target=/app/{name},readonly' in mount for mount in mounts))
        self.assertFalse(any('target=/app,' in mount or 'target=/app/.venv' in mount for mount in mounts))
        self.assertNotIn('command', be)
        self.assertEqual(be['env']['AUTH_COOKIE_SECURE'], 'false')
        variables = plan['config']['vars']
        self.assertEqual(variables['OB_MANAGED'], 'true')
        self.assertEqual(variables['AIVISION_NATIVE_TRACE_HISTORY_START'], '2026-10-04T00:00:00Z')
        self.assertTrue(all(variables[key].startswith('aiv-e2e-abc-') for key in
                            ('OB_CONTAINER', 'SIGNOZ_CONTAINER', 'GATEWAY_CONTAINER', 'BE_CONTAINER', 'AGENTLENS_CONTAINER', 'FE_CONTAINER')))
        self.assertEqual(variables['DOCKER_NETWORK'], plan['config']['dockerNetwork'])
        self.assertEqual({key: variables[key] for key in PASSWORDS}, PASSWORDS)

    def test_success_requires_all_44_actual_source_and_platform_checks(self):
        case = {'execute_exit': 0, 'verify_exit': 0, 'source_verified': True, 'platform_verified': True}
        scenarios = ('success', 'no-results', 'retrieval-error', 'retry', 'changed-corpus',
                     'zero-usage', 'unknown-usage', 'stream', 'cancel', 'concurrent', 'tool-exception')
        cases = [{**case, 'case': f'{framework}-{sdk}-{scenario}'}
                 for framework in ('langchain', 'deepagents') for sdk in ('langfuse', 'nemo-relay')
                 for scenario in scenarios]
        summary = {'status': 'passed', 'mode': 'ci', 'sdk_coverage': 'full', 'data_backend': 'oceanbase',
                   'expected_cases': 44, 'executed_cases': 44, 'cases': cases, 'sdks': ['langfuse', 'nemo-relay']}
        self.assertTrue(platform.suite_passed(summary))
        self.assertFalse(platform.suite_passed({**summary, 'executed_cases': 43}))
        self.assertFalse(platform.suite_passed({**summary, 'cases': [{**case, 'platform_verified': False}] + [case] * 43}))
        self.assertFalse(platform.suite_passed({**summary, 'data_backend': 'sqlite'}))

    def test_host_configuration_cannot_override_this_deployment(self):
        env = platform.clean_environment({'PATH': '/tools', 'HOME': '/user', 'DOCKER_HOST': 'unix:///docker.sock',
                                          'AIV_VAR_OB_MANAGED': 'false', 'AIVISION_E2E_TARGETS_JSON': 'private',
                                          'OPENAI_API_KEY': 'private', 'LANGFUSE_HOST': 'https://remote'})
        self.assertEqual(env['DOCKER_HOST'], 'unix:///docker.sock')
        self.assertNotIn('AIV_VAR_OB_MANAGED', env)
        self.assertNotIn('AIVISION_E2E_TARGETS_JSON', env)
        self.assertNotIn('OPENAI_API_KEY', env)
        self.assertNotIn('LANGFUSE_HOST', env)

    def test_cleanup_still_checks_every_owned_resource_after_cli_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cli, blueprint = root/'aiv', root/'blueprint.yaml'
            cli.touch()
            blueprint.touch()
            smoke = platform.Smoke(root, {}, {'private-token'})
            calls = []
            def run(label, command, **kwargs):
                calls.append(command)
                if label == 'destroy':
                    raise RuntimeError('private-token cli failure')
                return subprocess.CompletedProcess(command, 0, stdout='', stderr='')
            with patch.object(smoke, 'run', side_effect=run):
                errors = smoke.cleanup(cli, blueprint, 'aiv-e2e-test', ['aiv-e2e-test-be'])
            self.assertIn('[REDACTED]', errors[0])
            self.assertTrue(any(command[:3] == ['docker', 'rm', '-f'] for command in calls))
            self.assertTrue(any(command[:3] == ['docker', 'volume', 'rm'] for command in calls))
            self.assertTrue(any(command[:3] == ['docker', 'network', 'rm'] for command in calls))
            self.assertFalse(any('prune' in command for command in calls))

    def test_suite_requires_each_distinct_matrix_case_not_44_duplicate_successes(self):
        case = {'case': 'langchain-langfuse-success', 'execute_exit': 0, 'verify_exit': 0,
                'source_verified': True, 'platform_verified': True}
        self.assertFalse(platform.suite_passed({'status': 'passed', 'mode': 'ci', 'sdk_coverage': 'full',
                                              'data_backend': 'oceanbase', 'sdks': ['langfuse', 'nemo-relay'],
                                              'expected_cases': 44, 'executed_cases': 44, 'cases': [case]*44}))

    def test_sanitizer_removes_generated_keys_even_from_subprocess_error_text(self):
        smoke = platform.Smoke(Path('.'), {}, {'synthetic-password', 'session-cookie'})
        value = smoke.sanitize('password=synthetic-password cookie=session-cookie key=sk-aiv-unknownkey')
        self.assertNotIn('synthetic-password', value)
        self.assertNotIn('session-cookie', value)
        self.assertNotIn('sk-aiv-', value)


class PlatformImageCacheTests(unittest.TestCase):
    @contextmanager
    def docker_client(self, *, cached=None, inspect_error=False, pull_denied=False):
        if os.name == 'nt':
            self.skipTest('Windows Docker is excluded; POSIX Docker-client process shim only')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state, calls = root / 'images.json', root / 'calls.jsonl'
            state.write_text(json.dumps(cached or {}))
            docker = root / 'docker'
            docker.write_text('#!' + sys.executable + '\n' + '''
import json, os, pathlib, sys
state, calls = pathlib.Path(os.environ['CACHE_STATE']), pathlib.Path(os.environ['CACHE_CALLS'])
args = sys.argv[1:]
with calls.open('a') as output:
    output.write(json.dumps({'args': args, 'home': os.environ['HOME'],
        'docker_config': os.environ['DOCKER_CONFIG']}) + '\\n')
images = json.loads(state.read_text())
if args[:2] == ['image', 'inspect']:
    if os.environ.get('CACHE_INSPECT_ERROR'):
        print('Cannot connect to the Docker daemon', file=sys.stderr)
        raise SystemExit(1)
    if args[-1] not in images:
        print('Error response from daemon: No such image: ' + args[-1], file=sys.stderr)
        raise SystemExit(1)
    print(images[args[-1]])
elif args[0] == 'pull':
    assert pathlib.Path(os.environ['DOCKER_CONFIG'], 'config.json').is_file()
    if os.environ.get('CACHE_PULL_DENIED'):
        print('unauthorized', file=sys.stderr)
        raise SystemExit(1)
    images[args[1]] = 'sha256:' + 'b' * 64
    state.write_text(json.dumps(images))
else:
    raise SystemExit(2)
''')
            docker.chmod(0o755)
            config = root / 'host-auth'
            config.mkdir()
            (config / 'config.json').write_text('{"auths":{"ghcr.io":{"auth":"synthetic-registry-secret"}}}')
            smoke = platform.Smoke(root, {'PATH': str(root), 'HOME': str(root / 'host-home'),
                'DOCKER_CONFIG': str(config), 'CACHE_STATE': str(state), 'CACHE_CALLS': str(calls),
                'CACHE_INSPECT_ERROR': '1' if inspect_error else '',
                'CACHE_PULL_DENIED': '1' if pull_denied else ''}, {'synthetic-registry-secret'})
            yield smoke, state, calls

    def test_cold_cache_uses_host_auth_without_forwarding_registry_configuration(self):
        with self.docker_client() as (smoke, state, calls):
            platform.cache_platform_images(smoke, {'backend': IMAGES['backend']})
            recorded = [json.loads(line) for line in calls.read_text().splitlines()]
            self.assertEqual(json.loads(state.read_text()), {IMAGES['backend']: 'sha256:' + 'b' * 64})
            self.assertEqual([row['args'][0] for row in recorded], ['image', 'pull', 'image'])
            for row in recorded:
                self.assertEqual(row['docker_config'], smoke.env['DOCKER_CONFIG'])
                self.assertEqual(row['home'], smoke.env['HOME'])
                self.assertNotIn('run', row['args'])
                self.assertNotIn('--mount', row['args'])
                self.assertNotIn('--env', row['args'])
                self.assertNotIn('synthetic-registry-secret', json.dumps(row))

    def test_cached_native_image_is_never_repulled_or_replaced(self):
        native_id = 'sha256:' + 'c' * 64
        with self.docker_client(cached={IMAGES['backend']: native_id}) as (smoke, state, calls):
            platform.cache_platform_images(smoke, {'backend': IMAGES['backend']})
            recorded = [json.loads(line) for line in calls.read_text().splitlines()]
            self.assertEqual(len(recorded), 1)
            self.assertEqual(recorded[0]['args'], ['image', 'inspect', '--format', '{{.Id}}', IMAGES['backend']])
            self.assertNotIn('--platform', recorded[0]['args'])
            self.assertEqual(json.loads(state.read_text()), {IMAGES['backend']: native_id})

    def test_unavailable_daemon_is_not_treated_as_a_cache_miss(self):
        with self.docker_client(inspect_error=True) as (smoke, _state, calls):
            with self.assertRaisesRegex(RuntimeError, 'inspect.*backend'):
                platform.cache_platform_images(smoke, {'backend': IMAGES['backend']})
            self.assertEqual(len(calls.read_text().splitlines()), 1)

    def test_failed_host_pull_stops_before_verification_or_later_images(self):
        with self.docker_client(pull_denied=True) as (smoke, state, calls):
            with self.assertRaisesRegex(RuntimeError, 'image-pull-backend failed'):
                platform.cache_platform_images(smoke, {'backend': IMAGES['backend'], 'frontend': IMAGES['frontend']})
            self.assertEqual(json.loads(state.read_text()), {})
            recorded = [json.loads(line) for line in calls.read_text().splitlines()]
            self.assertEqual([row['args'][0] for row in recorded], ['image', 'pull'])

    def test_malformed_cached_identity_fails_without_pulling_over_it(self):
        with self.docker_client(cached={IMAGES['backend']: 'invalid-image-id'}) as (smoke, _state, calls):
            with self.assertRaisesRegex(RuntimeError, 'Invalid cached.*backend'):
                platform.cache_platform_images(smoke, {'backend': IMAGES['backend']})
            self.assertEqual(len(calls.read_text().splitlines()), 1)

    def test_host_pulls_precede_cast_and_failed_pull_retains_candidate_cleanup(self):
        for fail_pull in (False, True):
            with self.subTest(fail_pull=fail_pull), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); runtime = root / 'runtime'; runtime.mkdir()
                calls, cached = [], set()
                class FakeSmoke(platform.Smoke):
                    def run(self, label, command, **kwargs):
                        calls.append((label, list(command)))
                        if command[:3] == ['docker', 'image', 'inspect']:
                            image = command[-1]
                            return subprocess.CompletedProcess(command, 0 if image in cached else 1,
                                'sha256:' + 'b' * 64 if image in cached else '',
                                '' if image in cached else 'Error response from daemon: No such image: ' + image)
                        if command[:2] == ['docker', 'pull']:
                            if fail_pull:
                                raise RuntimeError('host image pull denied')
                            cached.add(command[-1])
                        if label == 'cast':
                            raise RuntimeError('stop after cast boundary')
                        return subprocess.CompletedProcess(command, 0, '{}' if label == 'docker-info' else '', '')
                    def cleanup(self, *args):
                        calls.append(('cleanup', []))
                        return []
                with patch.object(platform.platform, 'system', return_value='Linux'), \
                        patch.object(platform, 'Smoke', FakeSmoke), \
                        patch.object(platform, 'private_runtime', return_value=nullcontext(runtime)), \
                        patch.object(platform, 'build_candidate', side_effect=lambda smoke, source, runtime, namespace, image:
                                     {'image': 'aivision-signoz-e2e:' + namespace}), \
                        patch.object(platform, 'remove_candidate') as remove, \
                        patch.object(platform, 'available_ports', return_value=PORTS), \
                        patch.dict(os.environ, {'AIVISION_E2E_PLATFORM_IMAGES_JSON': json.dumps(IMAGES)}), \
                        redirect_stdout(io.StringIO()):
                    code = platform.main(['--repo', str(ROOT), '--out', str(root / 'out')])
                self.assertEqual(code, 1)
                remove.assert_called_once()
                summary = json.loads((root / 'out/summary.json').read_text())
                self.assertTrue(summary['checks']['owned_candidate_image_removed'])
                if fail_pull:
                    self.assertFalse(any(label == 'cast' for label, _ in calls))
                    self.assertEqual(summary['error']['message'], 'host image pull denied')
                else:
                    cast_index = next(i for i, (label, _) in enumerate(calls) if label == 'cast')
                    pulls = [(i, command) for i, (_, command) in enumerate(calls) if command[:2] == ['docker', 'pull']]
                    self.assertEqual({command[-1] for _, command in pulls}, set(IMAGES.values()))
                    self.assertTrue(all(i < cast_index for i, _ in pulls))
                    self.assertTrue(summary['checks']['owned_resources_removed'])


class BootstrapStatusTests(unittest.TestCase):
    def test_oceanbase_disk_precheck_reports_only_the_fixed_category(self):
        for mount in ('/root/ob', '/private/customer-data'):
            line = ('[ERROR] OBD-2003: (127.0.0.1) ' + mount
                    + ' not enough disk space. (Avail: 5G, Need: 10G), '
                    'Please reduce the `datafile_size` or `datafile_disk_percentage`\n')
            result = platform.classify_oceanbase_bootstrap('obcluster deployed\n' + line)
            self.assertEqual(result, {'stage': 'deployed', 'errors': ['insufficient_disk']})
            self.assertNotIn(mount, json.dumps(result))
            self.assertNotIn('5G', json.dumps(result))
            self.assertNotIn('10G', json.dumps(result))

    def test_oceanbase_disk_precheck_rejects_incomplete_or_unrelated_messages(self):
        line = ('[ERROR] OBD-2003: (127.0.0.1) /root/ob not enough disk space. '
                '(Avail: 5G, Need: 10G), Please reduce the `datafile_size` or `datafile_disk_percentage`')
        for body in (line, 'prompt=' + line + '\n', line.replace('2003', '2000') + '\n',
                     '[ERROR] OBD-2003: privateValueError\n', line + ' private-cookie\n',
                     line.replace('5G', 'private-secret') + '\n'):
            with self.subTest(body=body):
                self.assertEqual(platform.classify_oceanbase_bootstrap(body),
                                 {'stage': 'unknown', 'errors': []})

    def test_oceanbase_bootstrap_classifies_only_complete_public_markers(self):
        observed = platform.classify_oceanbase_bootstrap(
            'do normal boot\nobcluster deployed\nStart observer ok\n'
            'observer program health check ok\nboot failed!\n'
            'OceanBase bootstrap failed (exit 1)\n')
        self.assertEqual(observed, {'stage': 'observer_healthy',
                                   'errors': ['upstream_boot_failed', 'bootstrap_failed']})
        self.assertEqual(platform.classify_oceanbase_bootstrap('tenant is connectable\nboot success!\n'),
                         {'stage': 'ready', 'errors': []})
        self.assertEqual(platform.classify_oceanbase_bootstrap(
            '[ERROR] OBD-1006: Failed to connect to oceanbase-ce\n'),
            {'stage': 'unknown', 'errors': ['observer_connection_failed']})

    def test_bootstrap_classification_cannot_publish_payloads_or_arbitrary_error_labels(self):
        private = ['eyJhbGciOiJIUzI1NiJ9.unregistered.signature', 'sessionid=private-cookie',
                   'prompt=private-customer-question', 'SELECT private_column FROM private_table',
                   'privateValueError', 'OBD-private-secret', 'private-stage-label']
        body = '\n'.join(private + [
            '[ERROR] privateValueError: private-secret',
            'OceanBase image lacks required lifecycle executable: /private/secret',
            'OceanBase bootstrap failed (exit private-secret)',
            'prompt=boot success!', 'stage=observer program health check ok',
            '{"stage":"private-stage-label","errors":["privateValueError"]}'])
        result = platform.classify_oceanbase_bootstrap(body)
        self.assertEqual(result, {'stage': 'unknown', 'errors': []})
        for secret in private:
            self.assertNotIn(secret, json.dumps(result))

    @unittest.skipIf(os.name == 'nt', 'Docker bootstrap log pipes require Linux/macOS')
    def test_bootstrap_log_reader_bounds_bytes_and_timeout_without_writing_logs(self):
        body, scan = platform.read_bootstrap_log([sys.executable, '-c',
            'import sys; print("boot success!"); print("unregistered-cookie", file=sys.stderr)'],
            os.environ, byte_limit=1024, timeout=2)
        self.assertEqual(scan, 'complete')
        self.assertEqual(platform.classify_oceanbase_bootstrap(body.decode()), {'stage': 'ready', 'errors': []})
        script = 'import sys,time; sys.stdout.write("x" * 1000000); sys.stdout.flush(); time.sleep(30)'
        body, scan = platform.read_bootstrap_log([sys.executable, '-c', script], os.environ,
                                                 byte_limit=1024, timeout=1)
        self.assertEqual(len(body), 1024)
        self.assertEqual(scan, 'byte_limit')
        body, scan = platform.read_bootstrap_log(
            [sys.executable, '-c', 'import time; time.sleep(30)'], os.environ,
            byte_limit=1024, timeout=0.03)
        self.assertEqual((body, scan), (b'', 'timeout'))

    def test_oceanbase_bootstrap_never_uses_artifact_logging_or_exception_messages(self):
        with tempfile.TemporaryDirectory() as directory:
            smoke = platform.Smoke(Path(directory), {}, set())
            with patch.object(platform, 'read_bootstrap_log',
                    return_value=(b'boot failed!\n', 'complete')) as read, \
                    patch.object(smoke, 'run', side_effect=AssertionError('must not log raw boot output')), \
                    patch.object(smoke, 'write', side_effect=AssertionError('must not write raw boot output')):
                self.assertEqual(platform.oceanbase_bootstrap(smoke, 'owned-ob'),
                                 {'scan': 'complete', 'stage': 'unknown', 'errors': ['upstream_boot_failed']})
            self.assertEqual(read.call_args.args[0], ['docker', 'logs', '--tail', '200', 'owned-ob'])
            self.assertEqual(list(Path(directory).iterdir()), [])
            with patch.object(platform, 'read_bootstrap_log', side_effect=OSError('private-cookie secret')):
                self.assertEqual(platform.oceanbase_bootstrap(smoke, 'owned-ob'),
                                 {'scan': 'unavailable', 'stage': 'unknown', 'errors': []})

    def test_filesystem_evidence_contains_only_numeric_free_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            result = platform.disk_free_bytes(Path(directory), directory)
            self.assertEqual(set(result), {'workspace', 'docker_root'})
            self.assertTrue(all(type(value) is int and value >= 0 for value in result.values()))
            self.assertNotIn(directory, json.dumps(result))
            with patch.object(platform.shutil, 'disk_usage', side_effect=OSError('private-disk-path')):
                self.assertEqual(platform.disk_free_bytes(Path(directory), directory),
                                 {'workspace': None, 'docker_root': None})
            self.assertIsNone(platform.disk_free_bytes(Path(directory), '/missing-private-docker-root')['docker_root'])

    def test_log_classification_runs_only_for_an_exited_database_during_failed_cast(self):
        for stage, status in (('cast', 'exited'), ('cast', 'running'), ('acceptance', 'exited')):
            with self.subTest(stage=stage, status=status), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); runtime = root / 'runtime'; runtime.mkdir()
                class FakeSmoke(platform.Smoke):
                    def run(self, label, command, **kwargs):
                        if label == 'cast' and stage == 'cast':
                            raise RuntimeError('primary cast failure')
                        if label.startswith('bootstrap-status-'):
                            state = {'status': status, 'running': status == 'running',
                                     'oomKilled': False, 'exitCode': 1, 'healthStatus': 'none'}
                            return subprocess.CompletedProcess(command, 0, json.dumps(state), '')
                        output = ('{}' if label == 'docker-info' else
                                  'sha256:' + 'a' * 64 if label.startswith(('image-', 'container-image-')) else
                                  json.dumps({'vendor': 'mysql', 'migration_count': 1, 'version': 'OceanBase'})
                                  if label == 'sql-bootstrap' else '')
                        return subprocess.CompletedProcess(command, 0, output, '')
                    def cleanup(self, *args):
                        return []
                def acceptance(**kwargs):
                    raise RuntimeError('primary acceptance failure')
                with patch.object(platform.platform, 'system', return_value='Linux'), \
                        patch.object(platform, 'Smoke', FakeSmoke), \
                        patch.object(platform, 'private_runtime', return_value=nullcontext(runtime)), \
                        patch.object(platform, 'build_candidate', side_effect=lambda smoke, source, runtime, namespace, image:
                                     {'image': 'aivision-signoz-e2e:' + namespace}), \
                        patch.object(platform, 'remove_candidate'), \
                        patch.object(platform, 'available_ports', return_value=PORTS), \
                        patch.object(platform, 'oceanbase_bootstrap', return_value={
                            'scan': 'complete', 'stage': 'unknown', 'errors': []}) as scan, \
                        patch.dict(os.environ, {'AIVISION_E2E_PLATFORM_IMAGES_JSON': json.dumps(IMAGES)}), \
                        redirect_stdout(io.StringIO()):
                    code = platform.main(['--repo', str(ROOT), '--out', str(root / 'out')],
                        target_factory=lambda **kwargs: {}, acceptance=acceptance)
                summary = json.loads((root / 'out/summary.json').read_text())
                self.assertEqual(code, 1)
                expected = stage == 'cast' and status == 'exited'
                self.assertEqual(scan.call_count, int(expected))
                self.assertEqual('oceanbase_bootstrap' in summary, expected)
                self.assertTrue(summary['checks']['owned_resources_removed'])

    def test_only_selected_container_state_fields_reach_the_summary(self):
        names = ['owned-' + role for role in platform.IMAGE_REPOS]
        names.append('sdk-container-must-not-be-inspected')
        healthy = {'status': 'running', 'running': True, 'oomKilled': False,
                   'exitCode': 0, 'healthStatus': 'healthy'}
        stopped = {**healthy, 'status': 'exited', 'running': False,
                   'oomKilled': True, 'exitCode': 137, 'healthStatus': 'none'}
        with tempfile.TemporaryDirectory() as directory:
            smoke = platform.Smoke(Path(directory), {}, set())
            def inspect(label, command, **kwargs):
                state = stopped if command[-1] == names[0] else healthy
                extra = {'Env': ['TOKEN=unregistered-secret'], 'Args': ['raw-prompt'],
                         'Health': {'Log': [{'Output': 'raw-model-output'}]},
                         'arbitrary': 'unregistered-secret'}
                return subprocess.CompletedProcess(command, 0, json.dumps({**state, **extra}), 'private-stderr')
            with patch.object(smoke, 'run', side_effect=inspect) as run:
                result = platform.bootstrap_status(smoke, names)
            self.assertEqual(set(result), set(platform.IMAGE_REPOS))
            for role in platform.IMAGE_REPOS:
                expected = stopped if role == 'oceanbase' else healthy
                self.assertEqual(result[role], {'inspection': 'ok', 'exists': True, **expected})
            published = json.dumps(result)
            for private in ('unregistered-secret', 'private-stderr', 'raw-prompt', 'raw-model-output', 'arbitrary'):
                self.assertNotIn(private, published)
            self.assertEqual(run.call_count, len(platform.IMAGE_REPOS))
            for call in run.call_args_list:
                command = call.args[1]
                self.assertEqual(command[:3], ['docker', 'container', 'inspect'])
                template = command[command.index('--format') + 1]
                self.assertEqual(set(re.findall(r'\.State(?:\.[A-Za-z]+)*', template)),
                                 {'.State.Status', '.State.Running', '.State.OOMKilled',
                                  '.State.ExitCode', '.State.Health', '.State.Health.Status'})
                for forbidden in ('{{json .State}}', '.Config', '.Args', '.Health.Log', '.Error'):
                    self.assertNotIn(forbidden, template)
                self.assertEqual(call.kwargs['check'], False)

    def test_malformed_daemon_data_never_becomes_published_evidence(self):
        healthy = {'status': 'running', 'running': True, 'oomKilled': False,
                   'exitCode': 0, 'healthStatus': 'healthy'}
        invalid = ['private non-JSON output', '[]', 'null', '{}']
        for key, value in (('status', 'private-status'), ('running', 'true'), ('oomKilled', 1),
                           ('exitCode', True), ('exitCode', -1), ('exitCode', 256),
                           ('exitCode', 1.0), ('healthStatus', 'private-health-output')):
            invalid.append(json.dumps({**healthy, key: value}))
        with tempfile.TemporaryDirectory() as directory:
            smoke = platform.Smoke(Path(directory), {}, set())
            for output in invalid:
                with self.subTest(output=output), patch.object(smoke, 'run',
                        return_value=subprocess.CompletedProcess([], 0, output, 'private-stderr')):
                    result = platform.bootstrap_status(smoke, ['owned'] * 6)
                    self.assertEqual(result, {role: {'inspection': 'failed', 'reason': 'invalid_response'}
                                              for role in platform.IMAGE_REPOS})

    def test_inspection_failure_records_only_bounded_exit_or_a_closed_reason(self):
        with tempfile.TemporaryDirectory() as directory:
            smoke = platform.Smoke(Path(directory), {}, set())
            for code in (1, -9, 256, True):
                with self.subTest(code=code), patch.object(smoke, 'run',
                        return_value=subprocess.CompletedProcess([], code, 'private-output', 'private-stderr')):
                    result = platform.bootstrap_status(smoke, ['owned'] * 6)
                    expected = ({'inspection': 'failed', 'reason': 'inspect_failed', 'exitCode': code}
                                if type(code) is int and -255 <= code <= 255 else
                                {'inspection': 'failed', 'reason': 'invalid_response'})
                    self.assertEqual(result, {role: expected for role in platform.IMAGE_REPOS})
            with patch.object(smoke, 'run', side_effect=RuntimeError('unregistered-secret exception')):
                self.assertEqual(platform.bootstrap_status(smoke, ['owned'] * 6),
                                 {role: {'inspection': 'failed', 'reason': 'inspection_error'}
                                  for role in platform.IMAGE_REPOS})

    def test_failed_cast_or_acceptance_keeps_cleanup_when_every_inspection_raises(self):
        for stage in ('cast', 'acceptance'):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); runtime = root / 'runtime'; runtime.mkdir()
                calls = []
                class FakeSmoke(platform.Smoke):
                    def run(self, label, command, **kwargs):
                        calls.append(label)
                        if label.startswith('bootstrap-status-'):
                            raise RuntimeError('unregistered-secret inspection failure')
                        if label == 'cast' and stage == 'cast':
                            raise RuntimeError('primary cast failure')
                        output = ('{}' if label == 'docker-info' else
                                  'sha256:' + 'a' * 64 if label.startswith(('image-', 'container-image-')) else
                                  json.dumps({'vendor': 'mysql', 'migration_count': 1, 'version': 'OceanBase'})
                                  if label == 'sql-bootstrap' else '')
                        return subprocess.CompletedProcess(command, 0, output, '')
                    def cleanup(self, *args):
                        calls.append('cleanup')
                        return []
                def acceptance(**kwargs):
                    raise RuntimeError('primary acceptance failure')
                with patch.object(platform.platform, 'system', return_value='Linux'), \
                        patch.object(platform, 'Smoke', FakeSmoke), \
                        patch.object(platform, 'private_runtime', return_value=nullcontext(runtime)), \
                        patch.object(platform, 'build_candidate', side_effect=lambda smoke, source, runtime, namespace, image:
                                     {'image': 'aivision-signoz-e2e:' + namespace}), \
                        patch.object(platform, 'remove_candidate'), \
                        patch.object(platform, 'available_ports', return_value=PORTS), \
                        patch.dict(os.environ, {'AIVISION_E2E_PLATFORM_IMAGES_JSON': json.dumps(IMAGES)}), \
                        redirect_stdout(io.StringIO()):
                    code = platform.main(['--repo', str(ROOT), '--out', str(root / 'out')],
                                         target_factory=lambda **kwargs: {}, acceptance=acceptance)
                summary = json.loads((root / 'out/summary.json').read_text())
                self.assertEqual(code, 1)
                self.assertEqual(summary['error']['message'], 'primary ' + stage + ' failure')
                self.assertTrue(summary['checks']['owned_resources_removed'])
                self.assertEqual(summary['bootstrap_status'],
                                 {role: {'inspection': 'failed', 'reason': 'inspection_error'}
                                  for role in platform.IMAGE_REPOS})
                self.assertLess(calls.index('bootstrap-status-frontend'), calls.index('cleanup'))
                self.assertNotIn('unregistered-secret', json.dumps(summary))


class AuthenticatedTargetContractTests(unittest.TestCase):
    """Real in-memory HTTP sessions; the transport never opens a socket."""

    def register(self, me_data, *, products=None, collector=None, status=200, code=0):
        origin = 'http://management.invalid'
        self.requests = []
        csrf_calls = 0

        def respond(request):
            nonlocal csrf_calls
            self.requests.append(request)
            headers = {}
            path = request.url.path
            if path == '/api/v1/auth/csrf':
                csrf_calls += 1
                data = {}
                headers['set-cookie'] = f'csrftoken=synthetic-csrf-{csrf_calls}; Path=/'
            elif path == '/api/v1/auth/register':
                data = {'personal_space': {'space_id': 'synthetic-space'},
                        'user': {'user_uuid': 'untrusted-register-user'}}
                headers['set-cookie'] = 'aivision_session=synthetic-session; Path=/'
            elif path == '/api/v1/auth/me':
                return httpx.Response(status, json={'code': code, 'data': me_data})
            elif path == '/api/v1/spaces/synthetic-space/space-keys':
                data = {'key': 'synthetic-space-key'}
            elif path == '/api/v1/spaces/synthetic-space/agents/create':
                product = json.loads(request.content)['agent_product']
                data = {'agent_uuid': 'synthetic-agent-' + product, 'agent_product': product}
            elif path == '/openapi/v1/ingest-tokens':
                agent_uuid = json.loads(request.content)['agent_uuid']
                product = agent_uuid.removeprefix('synthetic-agent-')
                data = {'agent_uuid': agent_uuid, 'agent_product': product, 'token': 'synthetic-ingest-' + product}
            else:
                raise AssertionError('Unexpected synthetic HTTP path: ' + path)
            return httpx.Response(200, json={'code': 0, 'data': data}, headers=headers)

        client = httpx.Client(base_url=origin, transport=httpx.MockTransport(respond))
        private = set()
        with patch.object(platform.httpx, 'Client', return_value=client):
            targets = platform.register_targets(origin, 'synthetic-ci', 'synthetic-password', private,
                                                agent_products=products, collector_endpoint=collector)
        return targets, private

    def test_server_identity_reaches_every_sdk_target_in_the_authenticated_session(self):
        targets, private = self.register({'user': {'user_uuid': 'server-current-owner'},
                                         'user_uuid': 'wrong-data-alias', 'user_id': 'wrong-client-alias'})
        self.assertEqual(set(targets), {'langfuse', 'nemo-relay'})
        for sdk, product in (('langfuse', 'langfuse'), ('nemo-relay', 'nvidia-nemo-relay')):
            with self.subTest(sdk=sdk):
                self.assertEqual(targets[sdk].get('authenticated_user_id'), 'server-current-owner')
                self.assertEqual(targets[sdk]['agent_product'], product)
                self.assertEqual(targets[sdk]['space_id'], 'synthetic-space')
                self.assertEqual(targets[sdk]['data_backend'], 'oceanbase')
        paths = [request.url.path for request in self.requests]
        self.assertEqual(paths[:5], ['/api/v1/auth/csrf', '/api/v1/auth/register', '/api/v1/auth/csrf',
                                    '/api/v1/auth/me', '/api/v1/spaces/synthetic-space/space-keys'])
        self.assertEqual(paths.count('/api/v1/auth/me'), 1)
        me_request = self.requests[3]
        self.assertEqual(me_request.method, 'GET')
        self.assertIn('aivision_session=synthetic-session', me_request.headers['cookie'])
        self.assertIn('csrftoken=synthetic-csrf-2', me_request.headers['cookie'])
        self.assertEqual(self.requests[4].headers['x-csrftoken'], 'synthetic-csrf-2')
        self.assertTrue({'synthetic-session', 'synthetic-csrf-1', 'synthetic-csrf-2',
                         'synthetic-space-key'} <= private)

    def test_native_factory_products_keep_server_identity_and_ingest_credentials(self):
        targets, private = self.register({'user': {'user_uuid': 'native-server-owner'}},
                                         products={'pi': 'pi', 'dsh': 'deepseek-harness'},
                                         collector='http://collector.invalid')
        self.assertEqual(set(targets), {'pi', 'dsh'})
        for app, product in (('pi', 'pi'), ('dsh', 'deepseek-harness')):
            with self.subTest(app=app):
                target = targets[app]
                self.assertEqual(target.get('authenticated_user_id'), 'native-server-owner')
                self.assertEqual(target['agent_uuid'], 'synthetic-agent-' + product)
                self.assertEqual(target['agent_product'], product)
                self.assertEqual(target['collector_endpoint'], 'http://collector.invalid')
                self.assertEqual(target['collector_headers'], {'Authorization': 'Bearer synthetic-ingest-' + product})
                self.assertIn('aivision_session=synthetic-session', target['dashboard_headers']['Cookie'])
                self.assertIn('synthetic-ingest-' + product, private)
        self.assertEqual(sum(request.url.path == '/api/v1/auth/me' for request in self.requests), 1)

    def test_native_string_bounds_do_not_require_uuid4(self):
        for value in ('x', 'server-owner-not-a-uuid', 'a' * 36, '服务端用户'):
            with self.subTest(value=value):
                targets, _ = self.register({'user': {'user_uuid': value}})
                self.assertEqual([target.get('authenticated_user_id') for target in targets.values()], [value, value])

    def test_malformed_me_shape_never_falls_back_to_registration_or_aliases(self):
        for data in (None, [], 'owner', 5, {}, {'user': None}, {'user': []}, {'user': 'owner'},
                     {'user': {}}, {'user_uuid': 'wrong-alias'}, {'user': {'id': 'wrong-user-id'}}):
            with self.subTest(data=data):
                with self.assertRaisesRegex(RuntimeError, 'authenticated user identity'):
                    self.register(data)
                self.assertFalse(any(request.method == 'POST' and request.url.path != '/api/v1/auth/register'
                                     for request in self.requests))

    def test_invalid_me_user_uuid_is_rejected_before_creating_any_key_or_agent(self):
        for value in (None, False, True, 0, 17, 1.5, [], {}, '', ' ', '\t', '\n',
                      ' owner', 'owner ', '\towner', 'owner\n', '\u2003owner', 'owner\u2003', 'a' * 37):
            with self.subTest(value=value):
                with self.assertRaisesRegex(RuntimeError, 'authenticated user identity'):
                    self.register({'user': {'user_uuid': value}})
                self.assertFalse(any('/space-keys' in request.url.path or '/agents/create' in request.url.path
                                     for request in self.requests))

    def test_failed_authenticated_me_request_cannot_create_targets(self):
        for status, code in ((401, 0), (403, 0), (500, 0), (200, 1)):
            with self.subTest(status=status, code=code):
                with self.assertRaisesRegex(RuntimeError, '/api/v1/auth/me'):
                    self.register({'user': {'user_uuid': 'unusable-owner'}}, status=status, code=code)
                self.assertFalse(any('/space-keys' in request.url.path or '/agents/create' in request.url.path
                                     for request in self.requests))


if __name__ == '__main__':
    unittest.main()
