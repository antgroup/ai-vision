"""Regressions for release job selection and repeated PR builds."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

import yaml

from product_registry import PUBLIC_REPOSITORY


ROOT = Path(__file__).resolve().parents[2]


def workflow(name):
    return yaml.safe_load((ROOT / '.github/workflows' / name).read_text())


class RuntimeBudgetTests(unittest.TestCase):
    def test_standard_release_builds_only_required_artifacts(self):
        document = workflow('release-images.yml')
        dispatch = document.get('on', document.get(True))['workflow_dispatch']['inputs']
        self.assertNotIn('include_openshell', dispatch)
        jobs = document['jobs']
        self.assertEqual(jobs['prepare']['if'], f"github.repository == '{PUBLIC_REPOSITORY}'")
        self.assertNotIn('openshell', jobs)
        self.assertNotIn('source-checks', jobs)
        self.assertIn('public-data-backend', jobs['assets']['needs'])
        self.assertNotIn('if', jobs['assets'])
        self.assertNotIn('openshell-catalog', str(jobs['assets']['steps']))

    def test_candidates_queue_per_ref_and_only_publishers_share_the_publication_queue(self):
        images, promote = workflow('release-images.yml'), workflow('promote-release.yml')
        jobs = images['jobs']
        publishers = (jobs['publish-stable'], jobs['publish-development'], promote['jobs']['promote'])
        gates = [job['concurrency'] for job in publishers]
        self.assertTrue(all(gate == gates[0] for gate in gates))
        gate = gates[0]
        self.assertEqual(images['concurrency'], {'group': 'release-candidate-${{ github.ref }}', 'queue': 'max'})
        self.assertEqual(gate, {'group': 'release-publication', 'queue': 'max'})
        self.assertNotEqual(promote.get('concurrency', {}).get('group'), gate['group'])
        for name in ('prepare', 'build-services', 'build-backend', 'manifest', 'artifact-smoke', 'personal'):
            self.assertNotEqual(jobs[name].get('concurrency', {}).get('group'), gate['group'])

    def test_component_builds_wait_only_for_inputs_they_use(self):
        jobs = workflow('release-images.yml')['jobs']
        services, backend = jobs['build-services'], jobs['build-backend']
        self.assertEqual(set(services['needs']), {'prepare'})
        self.assertEqual(set(backend['needs']), {'prepare', 'plugins'})
        self.assertEqual(services['uses'], './.github/workflows/release-image-build.yml')
        self.assertEqual(services['uses'], backend['uses'])
        self.assertIn('build-services', jobs['manifest']['needs'])
        self.assertIn('build-backend', jobs['manifest']['needs'])
        pipeline = workflow('release-image-build.yml')['jobs']['build']
        self.assertIn('amd64', str(pipeline['strategy']['matrix']))
        self.assertIn('arm64', str(pipeline['strategy']['matrix']))
        artifact = next(step for step in pipeline['steps'] if step.get('uses', '').startswith('actions/upload-artifact@'))
        self.assertIn('github.run_attempt', artifact['with']['name'])

    def test_release_sdk_scope_flows_from_verified_plan_to_framework(self):
        images = workflow('release-images.yml')['jobs']
        ci = workflow('ci.yml')
        declaration = ci.get('on', ci.get(True))['workflow_call']['inputs']['sdk_coverage']
        self.assertEqual(declaration['default'], 'full')
        self.assertEqual(declaration['type'], 'string')
        self.assertIn('steps.reuse.outputs.sdk_coverage', images['prepare']['outputs']['sdk_coverage'])
        self.assertIn('needs.prepare.outputs.sdk_coverage', images['public-source-checks']['with']['sdk_coverage'])
        self.assertIn('inputs.sdk_coverage', ci['jobs']['framework']['with']['sdk_coverage'])

    def test_browser_shards_consume_one_build_for_the_exact_run_and_source(self):
        jobs = workflow('framework-e2e.yml')['jobs']
        producer, browser = jobs['frontend'], jobs['frontend-browser']
        self.assertEqual(browser.get('needs'), 'frontend')
        builds = [step for step in producer['steps']
                  if step.get('run') == 'pnpm run build:fixture']
        self.assertEqual(len(builds), 1)
        self.assertEqual(builds[0]['if'], "matrix.os == 'ubuntu-24.04'")
        self.assertTrue(any(step.get('run') == 'pnpm build' for step in producer['steps']))
        frontend_scripts = json.loads((ROOT / 'ai-vision/vfe/package.json').read_text())['scripts']
        self.assertEqual(frontend_scripts['build:fixture'].split(' && ')[-1], 'vite build --mode development')
        self.assertIn('npm run build:fixture && playwright test', frontend_scripts['test:e2e'])
        uploads = [step for step in producer['steps']
                   if step.get('with', {}).get('name', '').startswith('frontend-fixture-')]
        downloads = [step for step in browser['steps']
                     if step.get('uses', '').startswith('actions/download-artifact@')]
        self.assertEqual(len(uploads), 1)
        self.assertEqual(len(downloads), 1)
        self.assertEqual(uploads[0]['with']['name'], downloads[0]['with']['name'])
        name = downloads[0]['with']['name']
        self.assertIn('github.run_id', name)
        self.assertIn('github.sha', name)
        self.assertNotIn('github.run_attempt', name)
        self.assertIs(uploads[0]['with']['overwrite'], True)
        # A failed browser retry retains the successful producer's bundle name.
        rendered = [name.replace('${{ github.run_id }}', '123')
                    .replace('${{ github.sha }}', 'a' * 40)
                    .replace('${{ github.run_attempt }}', str(attempt)) for attempt in (1, 2)]
        self.assertEqual(rendered[0], rendered[1])
        self.assertEqual(uploads[0]['with']['if-no-files-found'], 'error')
        vite = (ROOT / 'ai-vision/vfe/vite.config.ts').read_text()
        out_dir = re.search(r'outDir: path.resolve\(currentDir, "([^"]+)"\)', vite)
        self.assertIsNotNone(out_dir)
        expected = (ROOT / 'ai-vision/vfe' / out_dir[1]).resolve().relative_to(ROOT).as_posix()
        self.assertEqual(downloads[0]['with']['path'], expected)
        self.assertEqual(uploads[0]['with']['path'].rstrip('/'), expected)
        commands = '\n'.join(step.get('run', '') for step in browser['steps'])
        self.assertIn('pnpm exec playwright test --shard=', commands)
        self.assertNotIn('pnpm test:e2e', commands)
        self.assertNotIn('pnpm build', commands)

    def test_expensive_metadata_uses_plan_but_document_contracts_always_run(self):
        job = workflow('ci.yml')['jobs']['metadata']
        self.assertEqual(job.get('needs'), 'scope')
        self.assertIn('always()', job['if'])
        self.assertIn('!cancelled()', job['if'])
        contracts = next(step for step in job['steps'] if step.get('id') == 'document-contracts')
        self.assertNotIn('if', contracts)
        heavy = [step for step in job['steps']
                 if step.get('uses', '').startswith('actions/setup-go@')
                 or step.get('name') in {'Build metadata and CI policy tests', 'Validate workflow syntax'}]
        self.assertEqual(len(heavy), 3)
        for step in heavy:
            condition = step['if']
            for family in ('framework', 'diagnostics', 'native', 'collector', 'adr', 'deployment'):
                self.assertIn("needs.scope.outputs." + family + " == 'true'", condition)

    def test_compose_checks_resources_before_dependency_or_source_build(self):
        steps = workflow('compose-deploy-smoke.yml')['jobs']['lifecycle']['steps']
        self.assertEqual(steps[0].get('name'), 'Check Docker capacity before build work')
        self.assertIn('14 * 1024 ** 3', steps[0]['run'])
        self.assertIn('AIVISION_E2E_DOCKER_LINUX_RUNNER', steps[0]['run'])
        self.assertIn('timeout=30', steps[0]['run'])
        self.assertTrue(any(step.get('uses', '').startswith('actions/setup-go@') for step in steps[1:]))

    def test_actual_capacity_step_rejects_private_runner_and_accepts_sufficient_memory(self):
        step = workflow('compose-deploy-smoke.yml')['jobs']['lifecycle']['steps'][0]
        with tempfile.TemporaryDirectory(prefix='aiv-capacity-test-') as directory:
            docker = Path(directory) / 'docker'
            docker.write_text('#!' + sys.executable + '\n'
                'import json,os,sys\n'
                "assert sys.argv[1:] == ['info', '--format', '{{json .}}']\n"
                "print(json.dumps({'MemTotal': int(os.environ['TEST_DOCKER_MEMORY'])}))\n")
            docker.chmod(0o700)
            for memory, expected in ((8 * 1024 ** 3, 1), (14 * 1024 ** 3, 0), (16 * 1024 ** 3, 0)):
                with self.subTest(memory=memory):
                    env = dict(os.environ, TEST_DOCKER_MEMORY=str(memory),
                        PATH=directory + os.pathsep + str(Path(sys.executable).parent) + os.pathsep + os.environ['PATH'])
                    result = subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', step['run']],
                        env=env, capture_output=True, text=True, timeout=10, check=False)
                    self.assertEqual(result.returncode, expected, result.stderr)
                    if expected:
                        self.assertIn('AIVISION_E2E_DOCKER_LINUX_RUNNER', result.stderr)


if __name__ == '__main__':
    unittest.main()
