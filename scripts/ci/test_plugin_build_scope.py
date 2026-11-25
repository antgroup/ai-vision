"""Exercise plugin scope selection and real build/merge shell construction.

Controlled producers/verifiers replace compilation and artifact inspection;
these checks do not build plugins or establish runtime compatibility.
"""

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
WORKFLOW = ROOT / '.github/workflows/release-plugins.yml'
EXPRESSION = re.compile(r'\$\{\{\s*(.*?)\s*\}\}', re.DOTALL)
DEFAULT_ROWS = [
    dict(runner='ubuntu-22.04', target='x86_64-unknown-linux-gnu',
         platform='linux-gnu/x64', container='quay.io/pypa/manylinux_2_28_x86_64', glibc='2.28'),
    dict(runner='ubuntu-22.04-arm', target='aarch64-unknown-linux-gnu',
         platform='linux-gnu/arm64', container='quay.io/pypa/manylinux_2_28_aarch64', glibc='2.28'),
    dict(runner='macos-15', target='aarch64-apple-darwin',
         platform='darwin/arm64', container='', glibc=''),
    dict(runner='macos-15-intel', target='x86_64-apple-darwin',
         platform='darwin/x64', container='', glibc=''),
]


def workflow():
    return yaml.safe_load(WORKFLOW.read_text())


def scope_value(expression, selected):
    """Evaluate the limited Actions input/JSON syntax used by this workflow."""
    if type(selected) is not bool:
        raise AssertionError('Scope input must be a typed boolean')
    if expression == 'inputs.linux_amd64_only':
        return selected
    choice = re.fullmatch(
        r"fromJSON\(inputs\.linux_amd64_only\s*&&\s*'([^']+)'\s*\|\|\s*'([^']+)'\)",
        expression, re.DOTALL)
    if not choice:
        raise AssertionError('Unexpected scope expression: ' + expression)
    return json.loads(choice[1] if selected else choice[2])


def matrix_rows(selected):
    value = workflow()['jobs']['rust-hooks']['strategy']['matrix']['include']
    if isinstance(value, list):
        return value
    match = EXPRESSION.fullmatch(value)
    if not match:
        raise AssertionError('Matrix must be a complete typed expression')
    return scope_value(match[1], selected)


def render(value, selected):
    def replace(match):
        if match[1] == 'matrix.target':
            return 'x86_64-unknown-linux-gnu'
        resolved = scope_value(match[1], selected)
        return json.dumps(resolved) if isinstance(resolved, bool) else resolved
    return EXPRESSION.sub(replace, value)


def step_named(job, name):
    return next(step for step in workflow()['jobs'][job]['steps'] if step.get('name') == name)


def shell_step(step, root, selected, **environment):
    env = dict(os.environ, **environment)
    env['PATH'] = str(Path(sys.executable).parent) + os.pathsep + env['PATH']
    env.update({key: render(value, selected) for key, value in step.get('env', {}).items()})
    return subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', render(step['run'], selected)],
                          cwd=root, env=env, text=True, capture_output=True, timeout=10, check=False)


def merge_fixture(selected, *, reject=False):
    with tempfile.TemporaryDirectory(prefix='aiv-plugin-merge-') as directory:
        root = Path(directory)
        scripts = root / 'plugins/scripts'
        scripts.mkdir(parents=True)
        (root / 'fragments/fixture').mkdir(parents=True)
        # The fake merge models complete platform fragments; verification must
        # select the same scope, rather than silently accepting partial output.
        (scripts / 'merge_plugin_dist.py').write_text(
            'import json,os\nfrom pathlib import Path\n'
            'Path("dist").mkdir()\n'
            'Path("dist/platforms.json").write_text(os.environ["AVAILABLE_PLATFORMS"])\n')
        (scripts / 'verify_plugin_dist.py').write_text(
            'import argparse,json,os\nfrom pathlib import Path\n'
            'p=argparse.ArgumentParser()\n'
            'p.add_argument("--dist", required=True)\n'
            'p.add_argument("--require-privacy", action="store_true", required=True)\n'
            'p.add_argument("--max-glibc", choices=["2.28"], required=True)\n'
            'p.add_argument("--platform", choices=["linux-gnu/x64","linux-gnu/arm64","darwin/arm64","darwin/x64"])\n'
            'a=p.parse_args()\n'
            'expected=[a.platform] if a.platform else ["linux-gnu/x64","linux-gnu/arm64","darwin/arm64","darwin/x64"]\n'
            'available=json.loads((Path(a.dist)/"platforms.json").read_text())\n'
            'if expected != available or os.environ["REJECT_VERIFICATION"] == "true":\n'
            '    raise SystemExit("incomplete or invalid plugin scope")\n'
            '(Path(a.dist)/"verified.json").write_text(json.dumps(expected))\n')
        platforms = ['linux-gnu/x64'] if selected else [
            'linux-gnu/x64', 'linux-gnu/arm64', 'darwin/arm64', 'darwin/x64']
        result = shell_step(step_named('merge', 'Merge fragments'), root, selected,
                            PLUGINS_DIR='plugins', AVAILABLE_PLATFORMS=json.dumps(platforms),
                            REJECT_VERIFICATION=json.dumps(reject))
        verified = root / 'dist/verified.json'
        return result, json.loads(verified.read_text()) if verified.exists() else None, (root / 'plugin-dist.tar.gz').exists()


def build_fixture(job, *, fail_agent=''):
    with tempfile.TemporaryDirectory(prefix='aiv-plugin-build-') as directory:
        root = Path(directory)
        (root / 'scripts').mkdir()
        log = root / 'commands.jsonl'
        (root / 'scripts/build_plugins.py').write_text(
            'import json,os,sys\n'
            'with open(os.environ["COMMAND_LOG"], "a") as output:\n'
            '    output.write(json.dumps(sys.argv[1:])+"\\n")\n'
            'agent=sys.argv[sys.argv.index("--agent")+1]\n'
            'if agent == os.environ["FAIL_AGENT"]: raise SystemExit(9)\n')
        name = 'Build Rust hooks' if job == 'rust-hooks' else 'Build Node plugins'
        result = shell_step(step_named(job, name), root, True,
                            COMMAND_LOG=str(log), FAIL_AGENT=fail_agent)
        return result, [json.loads(line) for line in log.read_text().splitlines()]


class PluginBuildScopeTests(unittest.TestCase):
    def test_manual_and_reusable_scope_are_optional_typed_booleans(self):
        document = workflow()
        triggers = document.get('on', document.get(True))
        for event in ('workflow_dispatch', 'workflow_call'):
            with self.subTest(event=event):
                inputs = (triggers[event] or {}).get('inputs', {})
                self.assertIn('linux_amd64_only', inputs)
                scope = inputs['linux_amd64_only']
                self.assertEqual(scope['type'], 'boolean')
                self.assertIs(scope['default'], False)
                self.assertFalse(scope.get('required', False))

    def test_selected_scope_preserves_exact_linux_x64_baseline(self):
        self.assertEqual(matrix_rows(True), [DEFAULT_ROWS[0]])

    def test_existing_callers_still_receive_all_four_platforms(self):
        self.assertEqual(matrix_rows(False), DEFAULT_ROWS)
        caller = yaml.safe_load((ROOT / '.github/workflows/release-images.yml').read_text())
        plugin_job = caller['jobs']['plugins']
        self.assertEqual(plugin_job['uses'], './.github/workflows/release-plugins.yml')
        self.assertNotIn('linux_amd64_only', plugin_job.get('with', {}))

    def test_formal_release_can_route_only_arm64_macos_packaging_to_macos_26(self):
        workflows = ROOT / '.github/workflows'
        caller_jobs = yaml.safe_load((workflows / 'release-images.yml').read_text())['jobs']
        option = 'use_macos_26_arm64'
        self.assertEqual(
            {job for job, definition in caller_jobs.items() if option in definition.get('with', {})},
            {'plugins', 'cli'},
        )
        for caller_job, filename, build_job, platform_key in (
            ('plugins', 'release-plugins.yml', 'rust-hooks', 'platform'),
            ('cli', 'release-cli.yml', 'build', 'hook_platform'),
        ):
            with self.subTest(workflow=filename):
                document = yaml.safe_load((workflows / filename).read_text())
                events = document.get('on', document.get(True))
                declaration = events['workflow_call']['inputs'][option]
                self.assertEqual(declaration['type'], 'boolean')
                self.assertIs(declaration['default'], False)
                self.assertFalse(declaration.get('required', False))
                self.assertNotIn(option, (events['workflow_dispatch'] or {}).get('inputs', {}))
                self.assertEqual(caller_jobs[caller_job]['uses'], './.github/workflows/' + filename)
                self.assertEqual(caller_jobs[caller_job]['with'][option],
                                 "${{ needs.prepare.outputs.tier == 'release' }}")
                self.assertEqual(
                    document['jobs'][build_job]['runs-on'],
                    "${{ matrix." + platform_key + " == 'darwin/arm64' && "
                    "inputs.use_macos_26_arm64 && 'macos-26' || matrix.runner }}",
                )

    def test_real_merge_shell_verifies_selected_or_complete_scope_before_archiving(self):
        for selected, expected in ((True, ['linux-gnu/x64']), (False, [
                'linux-gnu/x64', 'linux-gnu/arm64', 'darwin/arm64', 'darwin/x64'])):
            with self.subTest(selected=selected):
                result, verified, archive = merge_fixture(selected)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(verified, expected)
                self.assertTrue(archive)

    def test_verifier_failure_cannot_produce_archive(self):
        for selected in (False, True):
            with self.subTest(selected=selected):
                result, verified, archive = merge_fixture(selected, reject=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIsNone(verified)
                self.assertFalse(archive)

    def test_all_agents_build_individually_and_first_failure_stops_each_loop(self):
        for job, agents in (('rust-hooks', ['claude-code', 'codex', 'kimi']),
                            ('node-plugins', ['opencode', 'openclaw', 'pi', 'dsh'])):
            with self.subTest(job=job):
                result, commands = build_fixture(job)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual([command[command.index('--agent') + 1] for command in commands], agents)
                for command in commands:
                    self.assertEqual(command[-2:], ['--output', 'dist'])
                    if job == 'rust-hooks':
                        self.assertEqual(command[2:4], ['--target', 'x86_64-unknown-linux-gnu'])
                    else:
                        self.assertIn('--install-deps', command)
                result, commands = build_fixture(job, fail_agent=agents[1])
                self.assertEqual(result.returncode, 9)
                self.assertEqual([command[command.index('--agent') + 1] for command in commands], agents[:2])

    def test_checkouts_and_artifacts_need_no_secrets_or_private_fetch(self):
        document = workflow()
        self.assertEqual(document['permissions'], {'contents': 'read'})
        self.assertNotIn('secrets.', WORKFLOW.read_text())
        for job in document['jobs'].values():
            for step in job['steps']:
                if step.get('uses', '').startswith('actions/checkout@'):
                    self.assertIs(step.get('with', {}).get('persist-credentials'), False)
                    self.assertNotIn('repository', step.get('with', {}))
        uploads = [step['with']['name'] for job in document['jobs'].values()
                   for step in job['steps'] if step.get('uses', '').startswith('actions/upload-artifact@')]
        self.assertEqual(uploads, [
            'plugin-dist-${{ github.run_attempt }}-${{ matrix.target }}',
            'plugin-dist-${{ github.run_attempt }}-node',
            'plugin-dist-${{ github.run_attempt }}',
            'plugin-release-${{ github.run_attempt }}'])


if __name__ == '__main__':
    unittest.main()
