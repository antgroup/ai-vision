"""Exercise native units/source selection and execute the real result gate."""
import ast
import itertools
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
WORKFLOW = ROOT / '.github/workflows/native-agent-e2e.yml'
EXPRESSIONS = re.compile(r'\$\{\{\s*(.*?)\s*\}\}')
UNIT_STEPS = {
    'Codex production common-edition contracts',
    'Claude production common-edition contracts',
    'Native production onboarding contracts',
    'Build and test production Pi and Pireplay plugins',
    'Build and test production OpenCode plugin',
    'Build and test production DSH and replay plugins',
    'Hermes production contracts',
    'Kimi production and native transcript contracts',
    'Native harness negative controls',
}
SOURCE_STEPS = {
    'Shared state-lock deadline contract',
    'Install official pinned isolated Agent runtimes',
    'Real native processes with local LangChain fake provider',
    'Hermes turn numbering across three native processes',
    'Hermes native compression across resumed processes',
    'Preserve sanitized source evidence',
}


def workflow():
    return yaml.safe_load(WORKFLOW.read_text())


def predicate(expression, inputs, outputs=None):
    if expression == 'always()':
        return True
    if expression.startswith('!cancelled()'):
        values = {**{'inputs.' + key: value for key, value in inputs.items()},
                  **{'steps.unit-scope.outputs.' + key: value for key, value in (outputs or {}).items()},
                  'steps.codex-contracts.outcome': 'success'}
        return evaluate_condition(expression, values)
    if expression == "inputs.source && inputs.tier == 'release'":
        return evaluate_condition(expression, {
            'inputs.source': inputs['source'], 'inputs.tier': inputs['tier'],
        })
    scoped = re.fullmatch(r"steps\.unit-scope\.outputs\.([a-z_]+) == 'true'", expression)
    if scoped:
        return (outputs or {}).get(scoped[1]) == 'true'
    expression = expression.removeprefix('always() && ')
    names = re.fullmatch(r'inputs\.([a-z_]+)(?: \|\| inputs\.([a-z_]+))?', expression)
    if not names:
        raise AssertionError('Unexpected selector: ' + expression)
    values = [inputs[key] for key in names.groups() if key]
    if any(type(value) is not bool for value in values):
        raise AssertionError('Native selectors must be typed booleans')
    return any(values)


def render(value, inputs, needs, temporary):
    def replace(match):
        expression = match[1]
        if expression == 'toJSON(needs)':
            return json.dumps(needs)
        if expression == 'inputs.tier':
            return inputs['tier']
        if expression == 'runner.temp':
            return temporary
        if expression in ('steps.codex-contracts.outcome', 'steps.claude-contracts.outcome',
                          'steps.hook-latency.outcome'):
            return 'success'
        if 'runner.os' in expression:
            values = {'inputs.source': inputs['source'], 'runner.os': inputs.get('runner_os', 'Linux')}
            return json.dumps(evaluate_condition(expression, values))
        if expression.startswith('inputs.'):
            if expression == 'inputs.unit_scope':
                return inputs.get('unit_scope', 'all')
            return json.dumps(predicate(expression, inputs))
        raise AssertionError('Unexpected gate data expression: ' + expression)
    return EXPRESSIONS.sub(replace, value)


def platform_condition(inputs, status, *, cancelled=False, fork=False):
    """Evaluate only the boolean operators/data references in the real job if."""
    expression = workflow()['jobs']['docker-platform']['if']
    values = {**{'inputs.' + key: value for key, value in inputs.items()},
        'needs.native-source.result': status,
        'github.event_name': 'pull_request' if fork else 'workflow_dispatch',
        'github.event.pull_request.head.repo.full_name': 'outside/fork' if fork else 'antgroup/ai-vision',
        'github.repository': 'antgroup/ai-vision'}
    return evaluate_condition(expression, values, cancelled=cancelled)


def evaluate_condition(expression, values, *, cancelled=False, success=True):
    expression = (expression.replace('always()', 'True').replace('cancelled()', repr(cancelled))
                  .replace('success()', repr(success)))
    expression = re.sub(r'(?:inputs|needs|github|steps|runner)\.[A-Za-z0-9_.-]+',
                        lambda match: repr(values[match[0]]), expression)
    expression = expression.replace('&&', ' and ').replace('||', ' or ')
    expression = re.sub(r'!(?!=)', ' not ', expression)
    tree = ast.parse(expression.strip(), mode='eval')
    allowed = (ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp,
               ast.Not, ast.Compare, ast.Eq, ast.NotEq, ast.Constant)
    if any(not isinstance(node, allowed) for node in ast.walk(tree)):
        raise AssertionError('Unexpected native workflow condition syntax')
    return eval(compile(tree, '<native-workflow-if>', 'eval'), {'__builtins__': {}})


def run_gate(inputs, needs):
    step = workflow()['jobs']['result']['steps'][0]
    with tempfile.TemporaryDirectory(prefix='aiv-native-gate-') as directory:
        summary = Path(directory)/'summary.md'
        env = dict(os.environ, GITHUB_STEP_SUMMARY=str(summary))
        env['PATH'] = str(Path(sys.executable).parent) + os.pathsep + env['PATH']
        env.update({name: render(value, inputs, needs, directory)
                    for name, value in step['env'].items()})
        result = subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', step['run']],
            cwd=ROOT, env=env, text=True, capture_output=True, timeout=10, check=False)
        selected = json.loads(env['SELECTED_JSON'])
        return result, selected, summary.read_text() if summary.exists() else ''


def source_commands(inputs):
    """Execute selected shell construction; replace only expensive executables."""
    job = workflow()['jobs']['native-source']
    if not predicate(job['if'], inputs):
        return [], []
    with tempfile.TemporaryDirectory(prefix='aiv-native-selection-') as directory:
        temporary = Path(directory)
        log = temporary/'commands.jsonl'
        for name in ('python', 'npm', 'cargo', 'make'):
            shim = temporary/name
            shim.write_text('#!' + sys.executable + '\n'
                'import json,os,sys\n'
                'from pathlib import Path\n'
                'if len(sys.argv) > 1 and sys.argv[1] == "scripts/ci/native_unit_scope.py":\n'
                '    os.execv(sys.executable, [sys.executable, *sys.argv[1:]])\n'
                'with open(os.environ["COMMAND_LOG"], "a") as output:\n'
                '    output.write(json.dumps([Path(sys.argv[0]).name,*sys.argv[1:]])+"\\n")\n'
                'if Path(sys.argv[0]).name == "cargo" and "--nocapture" in sys.argv:\n'
                '    print("test result: ok. 1 passed; 0 failed; 0 ignored;")\n')
            shim.chmod(0o700)
        env = dict(os.environ, COMMAND_LOG=str(log),
                   PATH=directory + os.pathsep + os.environ['PATH'],
                   GITHUB_OUTPUT=str(temporary/'output'), GITHUB_STEP_SUMMARY=str(temporary/'summary'),
                   RUNNER_TEMP=directory)
        selected = []
        outputs = {}
        for step in job['steps']:
            if step.get('if') and not predicate(step['if'], inputs, outputs):
                continue
            if 'name' in step:
                selected.append(step['name'])
            if 'run' not in step:
                continue
            command = render(step['run'], inputs, {}, directory)
            step_env = {**env, **{name: render(value, inputs, {}, directory)
                                 for name, value in step.get('env', {}).items()}}
            completed = subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', command],
                cwd=ROOT/step.get('working-directory', ''), env=step_env,
                text=True, capture_output=True, timeout=10, check=False)
            if completed.returncode:
                raise AssertionError(completed.stderr)
            if step.get('id') == 'unit-scope':
                outputs.update(line.split('=', 1) for line in (temporary/'output').read_text().splitlines())
        return selected, [json.loads(line) for line in log.read_text().splitlines()]


class NativeWorkflowScopeTests(unittest.TestCase):
    def test_scope_validator_uses_the_configured_python_before_toolchain_setup(self):
        steps = workflow()['jobs']['native-source']['steps']
        validator = next(index for index, step in enumerate(steps) if step.get('id') == 'unit-scope')
        self.assertEqual(steps[validator]['run'], 'python scripts/ci/native_unit_scope.py')
        for index, step in enumerate(steps):
            if step.get('uses', '').startswith(('actions/setup-node@', 'dtolnay/rust-toolchain@')):
                self.assertLess(validator, index)

    def test_plugin_only_scope_runs_its_actual_commands_without_other_compilers(self):
        cases = {
            'pi': ('ai-vision/agent-plugins/pi/observ-pi', 'ai-vision/agent-plugins/pi/pireplay'),
            'opencode': ('ai-vision/agent-plugins/opencode/observ-opencode',),
            'deepseek': ('ai-vision/agent-plugins/deepseek',),
        }
        for group, prefixes in cases.items():
            inputs = dict(tier='pr', units=True, source=False, platform=False,
                          unit_scope=json.dumps([group, 'onboarding', 'harness']))
            with self.subTest(group=group):
                steps, commands = source_commands(inputs)
                self.assertFalse(any(command[0] == 'cargo' for command in commands))
                npm = [command for command in commands if command[0] == 'npm']
                self.assertEqual({prefix for command in npm for prefix in cases['pi'] + cases['opencode'] + cases['deepseek']
                                  if prefix in command}, set(prefixes))
                self.assertNotIn('Hermes production contracts', steps)
                self.assertIn('Native production onboarding contracts', steps)
                self.assertIn('Native harness negative controls', steps)
                self.assertTrue(npm)

    def test_hermes_only_scope_omits_node_and_rust_toolchain_setup(self):
        inputs = dict(tier='pr', units=True, source=False, platform=False,
                      unit_scope='["hermes","onboarding","harness"]')
        steps, commands = source_commands(inputs)
        self.assertIn('Hermes production contracts', steps)
        self.assertFalse(any(command[0] in ('cargo', 'npm') for command in commands))
        job = workflow()['jobs']['native-source']
        for step in job['steps']:
            if step.get('uses', '').startswith(('actions/setup-node@', 'dtolnay/rust-toolchain@')):
                self.assertFalse(predicate(step['if'], inputs, {'node': 'false', 'rust': 'false'}))

    def test_units_is_opt_in_and_manual_source_keeps_its_existing_default(self):
        document = workflow()
        triggers = document.get('on', document.get(True))
        for event in ('workflow_call', 'workflow_dispatch'):
            unit = triggers[event]['inputs'].get('units')
            self.assertIsNotNone(unit, 'units-only callers need an explicit typed selector')
            self.assertEqual(unit['type'], 'boolean')
            self.assertIs(unit['default'], False)
        self.assertIs(triggers['workflow_dispatch']['inputs']['source']['default'], True)
        self.assertIs(triggers['workflow_dispatch']['inputs']['platform']['default'], True)
        self.assertIn('Native unit contracts', document['jobs']['native-source']['name'])

    def test_units_only_runs_real_unit_command_construction_without_source_receipts(self):
        inputs = dict(tier='pr', units=True, source=False, platform=False)
        steps, commands = source_commands(inputs)
        self.assertTrue(UNIT_STEPS.issubset(steps), 'units-only must run all production contracts')
        self.assertFalse(SOURCE_STEPS.intersection(steps), 'units-only has no native source artifacts')
        self.assertTrue(any(command[:2] == ['cargo', 'test'] for command in commands))
        self.assertTrue(any(command[:4] == ['python', '-m', 'unittest', 'discover'] for command in commands))
        self.assertTrue(any('tests.test_live_replay_parity' in command for command in commands))
        self.assertTrue(any(command[0] == 'npm' and command[-1] == 'test' for command in commands))
        self.assertFalse(any(any(Path(argument).name in ('install_runtimes.py', 'run_suite.py',
            'hermes_turn_numbering.py', 'hermes_compression.py') for argument in command) for command in commands))
        self.assertFalse(predicate(workflow()['jobs']['native-semantics']['if'], inputs))

    def test_units_compile_and_test_each_production_hook_and_run_installer_contracts(self):
        """Missing hook builds/tests or onboarding must fail units-only selection."""
        _, commands = source_commands(dict(tier='pr', units=True, source=False, platform=False))
        for source in ('codex/cdxhooks', 'claudecode/cchooks'):
            manifest = 'ai-vision/agent-plugins/' + source + '/Cargo.toml'
            for operation in ('build', 'test'):
                with self.subTest(source=source, operation=operation):
                    matching = [command for command in commands
                                if command[:2] == ['cargo', operation] and manifest in command]
                    self.assertEqual(len(matching), 1, (manifest, operation))
                    command = matching[0]
                    self.assertIn('--locked', command)
                    self.assertIn('--no-default-features', command)
                    self.assertEqual(command[command.index('--features') + 1], 'common')
                    self.assertIn('--bins', command)
                    self.assertNotIn('--all-targets', command)
                    self.assertNotIn('--no-fail-fast', command)
                    self.assertNotIn('--release', command)
                    if operation == 'test':
                        self.assertIn('--lib', command)
        self.assertIn(['make', '-C', 'ai-vision/agent-plugins', 'test-onboarding',
                       'PLUGIN_PYTHON=python'], commands)

    def test_failed_codex_source_does_not_hide_claude_failure_evidence(self):
        steps = workflow()['jobs']['native-source']['steps']
        claude = next(step for step in steps if step.get('id') == 'claude-contracts')
        for source, cancelled, outcome in itertools.product((False, True), (False, True), ('failure', 'skipped')):
            values = {'inputs.source': source, 'steps.unit-scope.outputs.claudecode': 'true',
                      'steps.codex-contracts.outcome': outcome}
            with self.subTest(source=source, cancelled=cancelled, outcome=outcome):
                self.assertEqual(evaluate_condition(claude['if'], values, cancelled=cancelled, success=False),
                                 source and not cancelled and outcome == 'failure')

    def test_release_runtime_evidence_precedes_fail_closed_rust_gate(self):
        job = workflow()['jobs']['native-source']
        rust_steps = [step for step in job['steps']
                      if step.get('id') in ('codex-contracts', 'claude-contracts')]
        self.assertEqual(len(rust_steps), 2)
        gate = next(step for step in job['steps']
                    if step.get('name') == 'Require the release Rust and performance checks')
        for tier, source in itertools.product(('pr', 'daily', 'release'), (False, True)):
            values = {'inputs.source': source, 'inputs.tier': tier}
            keep_running = source and tier == 'release'
            with self.subTest(tier=tier, source=source):
                for step in rust_steps:
                    expression = EXPRESSIONS.fullmatch(step['continue-on-error'])[1]
                    self.assertEqual(evaluate_condition(expression, values), keep_running)
                self.assertEqual(evaluate_condition(gate['if'], values), keep_running)
                self.assertFalse(evaluate_condition(gate['if'], values, cancelled=True))

        steps, _ = source_commands(dict(tier='release', units=False, source=True, platform=False))
        self.assertLess(steps.index('Preserve sanitized source evidence'), steps.index(gate['name']))
        self.assertEqual(gate['env'], {
            'CODEX_CONTRACTS': '${{ steps.codex-contracts.outcome }}',
            'CLAUDE_CONTRACTS': '${{ steps.claude-contracts.outcome }}',
            'HOOK_LATENCY': '${{ steps.hook-latency.outcome }}',
        })
        statuses = ('success', 'failure', 'cancelled', 'skipped', '')
        for codex, claude, benchmark in itertools.product(statuses, repeat=3):
            with self.subTest(codex=codex, claude=claude, benchmark=benchmark):
                result = subprocess.run(
                    ['bash', '-e', '-c', gate['run']],
                    env=dict(os.environ, CODEX_CONTRACTS=codex, CLAUDE_CONTRACTS=claude,
                             HOOK_LATENCY=benchmark),
                    capture_output=True, text=True, check=False, timeout=10,
                )
                self.assertEqual(result.returncode == 0, codex == claude == benchmark == 'success')

    def test_release_source_scopes_hook_performance_budgets_by_os(self):
        steps = workflow()['jobs']['native-source']['steps']
        for variable, selected_os in (
            ('AIVISION_WINDOWS_HOOK_P95_REPORT_ONLY', 'Windows'),
            ('AIVISION_WINDOWS_LARGE_POSTTOOL_REPORT_ONLY', 'Windows'),
            ('AIVISION_WINDOWS_RELEASE_PROMPT_SEED_BUDGET', 'Windows'),
            ('AIVISION_MACOS_RELEASE_HOOK_BUDGET', 'macOS'),
        ):
            selected = [step for step in steps if variable in step.get('env', {})]
            self.assertEqual([step['id'] for step in selected], ['hook-latency'])
            step, = selected
            for tier, source, runner_os in itertools.product(
                ('pr', 'daily', 'release'), (False, True), ('Linux', 'macOS', 'Windows'),
            ):
                inputs = dict(tier=tier, source=source, runner_os=runner_os)
                with self.subTest(variable=variable, **inputs):
                    self.assertEqual(predicate(step['if'], inputs), source and tier == 'release')
                    self.assertEqual(json.loads(render(step['env'][variable], inputs, {}, '')),
                                     runner_os == selected_os)

    def test_windows_manual_performance_keeps_the_strict_hook_limits(self):
        document = yaml.safe_load((WORKFLOW.parent / 'windows-native-performance.yml').read_text())
        self.assertEqual(set(document.get('on', document.get(True))), {'workflow_dispatch'})
        job = document['jobs']['performance']
        self.assertEqual(job['runs-on'], 'windows-2025')
        rows = job['strategy']['matrix']['include']
        self.assertEqual({row['test'] for row in rows}, {
            'pretool_parent_timing_p95_stays_below_50_ms',
            'prompt_seed_is_reused_by_pretool_control_response',
            'pretooluse_parent_latency_stays_under_fifty_milliseconds',
            'large_posttool_payload_handoff_stays_below_parent_budget',
        })
        large, = [row for row in rows if row['test'] == 'large_posttool_payload_handoff_stays_below_parent_budget']
        self.assertIs(large['release'], True)
        benchmark, = [step for step in job['steps'] if 'TEST_NAME' in step.get('env', {})]
        self.assertEqual(benchmark['env']['AIVISION_WINDOWS_LARGE_POSTTOOL_REPORT_ONLY'], 'false')
        self.assertEqual(benchmark['env']['AIVISION_WINDOWS_HOOK_P95_REPORT_ONLY'], 'false')
        self.assertEqual(benchmark['env']['AIVISION_WINDOWS_RELEASE_PROMPT_SEED_BUDGET'], 'false')

    def test_release_measures_hook_latency_before_the_remaining_rust_suites(self):
        _, commands = source_commands(dict(tier='release', units=False, source=True, platform=False))
        benchmarks = [
            ('codex/cdxhooks', 'async_parent_integration', [
                'pretool_parent_timing_p95_stays_below_50_ms',
                'prompt_seed_is_reused_by_pretool_control_response',
                'delayed_worker_start_does_not_delay_parent_or_strand_the_queue',
                'one_second_collector_does_not_delay_post_parent',
            ]),
            ('claudecode/cchooks', 'async_hook_contract', [
                'pretooluse_parent_latency_stays_under_fifty_milliseconds',
                'large_posttool_payload_handoff_stays_below_parent_budget',
            ]),
        ]
        full_suites = [command for command in commands if '--all-targets' in command]
        self.assertEqual(len(full_suites), 2)
        for (crate, target, tests), full in zip(benchmarks, full_suites):
            manifest = f'ai-vision/agent-plugins/{crate}/Cargo.toml'
            self.assertIn(manifest, full)
            expected_filters = ['--', '--exact']
            for test in tests:
                early, = [command for command in commands
                          if command[-4:] == [test, '--', '--exact', '--nocapture'] and manifest in command]
                self.assertEqual(early[-6:], ['--test', target, test, '--', '--exact', '--nocapture'])
                self.assertLess(commands.index(early), commands.index(full_suites[0]))
                self.assertEqual('--release' in early, crate == 'claudecode/cchooks')
                expected_filters.extend(['--skip', test])
            self.assertEqual(full[-len(expected_filters):], expected_filters)

    def test_source_retains_all_hook_integration_targets(self):
        for units in (False, True):
            _, commands = source_commands(dict(tier='pr', units=units, source=True, platform=False))
            self.assertIn(['cargo', 'test', '--locked', '--manifest-path',
                'ai-vision/agent-plugins/agentic-otel-common/Cargo.toml', '--target-dir',
                'ai-vision/agent-plugins/codex/cdxhooks/target', '--lib',
                'state::tests::state_lock_retry_delay_respects_deadline', '--jobs', '2', '--', '--exact'],
                commands)
            for source in ('codex/cdxhooks', 'claudecode/cchooks'):
                manifest = 'ai-vision/agent-plugins/' + source + '/Cargo.toml'
                with self.subTest(units=units, source=source):
                    matching = [command for command in commands
                                if command[0] == 'cargo' and manifest in command]
                    self.assertEqual([command[1] for command in matching], ['build', 'test'])
                    self.assertIn('--all-targets', matching[1])
                    self.assertIn('--no-fail-fast', matching[1])
                    self.assertNotIn('--lib', matching[1])
                    for command in matching:
                        self.assertEqual('--release' in command, source == 'claudecode/cchooks')

    def test_source_only_and_combined_keep_native_execution_with_one_unit_pass(self):
        for units in (False, True):
            with self.subTest(units=units):
                inputs = dict(tier='pr', units=units, source=True, platform=False)
                steps, commands = source_commands(inputs)
                for name in UNIT_STEPS | SOURCE_STEPS:
                    self.assertEqual(steps.count(name), 1)
                for script in ('install_runtimes.py', 'run_suite.py',
                               'hermes_turn_numbering.py', 'hermes_compression.py'):
                    self.assertEqual(sum(any(Path(arg).name == script for arg in cmd) for cmd in commands), 1)
                self.assertTrue(predicate(workflow()['jobs']['native-semantics']['if'], inputs))

    def test_platform_requires_success_for_selected_units_or_source(self):
        for units, source, platform in itertools.product((False, True), repeat=3):
            inputs = dict(units=units, source=source, platform=platform)
            for status in ('success', 'failure', 'cancelled', 'skipped', None):
                with self.subTest(inputs=inputs, status=status):
                    expected = platform and (not (units or source) or status == 'success')
                    self.assertEqual(platform_condition(inputs, status), expected)
            self.assertFalse(platform_condition(inputs, 'success', cancelled=True))
            self.assertFalse(platform_condition(inputs, 'success', fork=True))

    def test_actual_gate_requires_exactly_each_selected_scope_and_rejects_missing_or_bad_results(self):
        cases = (
            (False, False, False, set()),
            (True, False, False, {'native-source'}),
            (False, True, False, {'native-source', 'native-semantics'}),
            (True, True, False, {'native-source', 'native-semantics'}),
            (False, False, True, {'docker-platform'}),
            (True, False, True, {'native-source', 'docker-platform'}),
            (False, True, True, {'native-source', 'native-semantics', 'docker-platform'}),
            (True, True, True, {'native-source', 'native-semantics', 'docker-platform'}),
        )
        jobs = workflow()['jobs']['result']['needs']
        for tier, (units, source, platform, required) in itertools.product(('pr', 'daily', 'release'), cases):
            with self.subTest(tier=tier, units=units, source=source, platform=platform):
                inputs = dict(tier=tier, units=units, source=source, platform=platform)
                good = {job: {'result': 'success' if job in required else 'skipped'} for job in jobs}
                result, selected, summary = run_gate(inputs, good)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual({job for job, value in selected.items() if value}, required)
                self.assertIn('Selected checks', summary)
                for job in required:
                    for status in ('failure', 'skipped', 'cancelled', None):
                        broken = {**good, job: {'result': status}}
                        if status is None:
                            broken.pop(job)
                        failed, _, _ = run_gate(inputs, broken)
                        self.assertNotEqual(failed.returncode, 0, (job, status))
                        self.assertIn(job, failed.stderr)
        result, _, _ = run_gate(dict(tier='unknown', units=True, source=False, platform=False),
            {'native-source': {'result': 'success'}})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Unknown test tier', result.stderr)


if __name__ == '__main__':
    unittest.main()
