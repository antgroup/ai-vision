"""Exercise the manual candidate workflow's input and artifact boundaries."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / '.github/workflows/native-candidate.yml'


class CandidateWorkflowTests(unittest.TestCase):
    def document(self):
        self.assertTrue(PATH.is_file(), 'The manual native candidate workflow is missing')
        return yaml.safe_load(PATH.read_text())

    def run_step(self, step, values):
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            tools = temporary / 'tools'
            tools.mkdir()
            calls = temporary / 'calls.jsonl'
            program = (f'#!{sys.executable}\nimport json,os,sys\n'
                       'with open(os.environ["CAPTURE"],"a") as stream:\n'
                       ' stream.write(json.dumps([os.path.basename(sys.argv[0]),*sys.argv[1:]])+"\\n")\n'
                       'print("{}")\n')
            for name in ('gh', 'python3'):
                executable = tools / name
                executable.write_text(program)
                executable.chmod(0o700)
            environment = dict(os.environ, PATH=str(tools) + os.pathsep + os.environ['PATH'],
                               CAPTURE=str(calls), RUNNER_TEMP=directory,
                               GITHUB_WORKSPACE=str(ROOT), GITHUB_REPOSITORY='outside/public-fork',
                               GITHUB_RUN_ID='987', GITHUB_RUN_ATTEMPT='3',
                               SOURCE_CI_RUN_ID='123', SOURCE_CI_ATTEMPT='2',
                               PYTHON_IMAGE='python:3.11.14-slim-bookworm@sha256:' + 'a' * 64)
            environment.update(values)
            result = subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', step['run']],
                                    env=environment, cwd=ROOT, text=True, capture_output=True,
                                    timeout=10, check=False)
            commands = [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []
            return result, commands

    def test_candidate_is_manual_read_only_and_repository_independent(self):
        document = self.document()
        triggers = document.get('on', document.get(True))
        self.assertEqual(set(triggers), {'workflow_dispatch'})
        self.assertEqual(document['permissions'], {'contents': 'read', 'actions': 'read'})
        for name in ('source_ci_run_id', 'source_ci_attempt', 'python_image'):
            self.assertEqual(triggers['workflow_dispatch']['inputs'][name]['type'], 'string')
        raw = PATH.read_text()
        for forbidden in ('ob-labs/', 'ghcr.io/', 'secrets.', 'secrets: inherit', 'packages: write',
                          'delivery_publish.py', 'delivery_assets.py', 'docker/login-action'):
            self.assertNotIn(forbidden, raw)

    def test_metadata_uses_same_repository_and_explicit_attempts(self):
        step = next(step for step in self.document()['jobs']['plan']['steps'] if step.get('id') == 'metadata')
        self.assertNotIn('${{', step['run'])
        result, commands = self.run_step(step, {})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([command for command in commands if command[0] == 'gh'], [
            ['gh', 'api', 'repos/outside/public-fork/actions/runs/123/attempts/2'],
            ['gh', 'api', '--paginate', '--slurp', 'repos/outside/public-fork/actions/runs/123/attempts/2/jobs?per_page=100'],
            ['gh', 'api', 'repos/outside/public-fork/actions/runs/987/attempts/3'],
        ])
        plan = next(command for command in commands if command[0] == 'python3')
        self.assertEqual(plan[1:3], ['scripts/ci/native_candidate.py', 'plan'])
        self.assertEqual(plan[plan.index('--python-image') + 1], 'python:3.11.14-slim-bookworm@sha256:' + 'a' * 64)

    def test_bad_metadata_identifiers_reject_before_api_or_build(self):
        step = next(step for step in self.document()['jobs']['plan']['steps'] if step.get('id') == 'metadata')
        for key in ('SOURCE_CI_RUN_ID', 'SOURCE_CI_ATTEMPT'):
            for bad in ('', '0', '01', '123/attempts/99', '123; printf injected', '$(printf injected)'):
                with self.subTest(key=key, value=bad):
                    result, commands = self.run_step(step, {key: bad})
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(commands, [])

    def test_build_receives_plan_and_plugin_archive_as_argv(self):
        jobs = self.document()['jobs']
        self.assertIs(jobs['plugins']['with']['linux_amd64_only'], True)
        self.assertEqual(jobs['plugins']['uses'], './.github/workflows/release-plugins.yml')
        step = next(step for step in jobs['build']['steps'] if step.get('id') == 'candidate')
        self.assertNotIn('${{', step['run'])
        result, commands = self.run_step(step, {})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(commands), 1)
        build = commands[0]
        self.assertEqual(build[:3], ['python3', 'scripts/ci/native_candidate.py', 'build'])
        self.assertTrue(build[build.index('--plugin-dist') + 1].endswith('/plugin-release/plugin-dist.tar.gz'))
        self.assertTrue(build[build.index('--plan') + 1].endswith('/candidate-plan/plan.json'))
        self.assertTrue(build[build.index('--output') + 1].endswith('/native-candidate-output'))

    def test_only_successful_allowlisted_output_is_uploaded(self):
        document = self.document()
        build = document['jobs']['build']
        self.assertEqual(build['permissions'], {'contents': 'read'})
        self.assertLessEqual(build['timeout-minutes'], 180)
        uploads = [step for step in build['steps'] if step.get('uses', '').startswith('actions/upload-artifact@')]
        self.assertEqual(len(uploads), 1)
        upload = uploads[0]
        self.assertNotIn('always()', upload.get('if', ''))
        self.assertEqual(upload['with']['path'], '${{ runner.temp }}/native-candidate-output/')
        self.assertEqual(upload['with']['if-no-files-found'], 'error')
        self.assertLessEqual(upload['with']['retention-days'], 7)
        downloads = [step['with']['name'] for step in build['steps']
                     if step.get('uses', '').startswith('actions/download-artifact@')]
        self.assertEqual(downloads, ['candidate-plan-${{ github.run_attempt }}', 'plugin-release-${{ github.run_attempt }}'])

    def test_selected_go_satisfies_native_driver_toolchain(self):
        setup = next(step for step in self.document()['jobs']['build']['steps']
                     if step.get('uses', '').startswith('actions/setup-go@'))['with']
        selected = setup.get('go-version')
        if not selected:
            module = (ROOT / setup['go-version-file']).read_text()
            selected = re.search(r'^go (\S+)$', module, re.MULTILINE)[1]
        # The module minimum is intentionally lower than the qualified builder.
        # Test the actual workflow selection, not a fixture's hard-coded Go value.
        self.assertRegex(selected, r'^1\.25\.\d+$', 'Native candidate driver requires pinned Go 1.25')

    def test_old_release_remains_explicit_without_nightly_execution(self):
        document = yaml.safe_load((ROOT / '.github/workflows/release-images.yml').read_text())
        triggers = document.get('on', document.get(True))
        self.assertNotIn('schedule', triggers)
        self.assertIn('workflow_dispatch', triggers)
        self.assertEqual(triggers['push'], {'tags': ['v*']})


if __name__ == '__main__':
    unittest.main()
