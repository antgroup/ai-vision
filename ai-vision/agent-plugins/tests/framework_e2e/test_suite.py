"""CI cannot turn an empty, skipped or failed matrix into successful acceptance."""
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import suite
from suite import acceptance_status, scenarios, ci_backend, validate_ci_targets


class AcceptanceTests(unittest.TestCase):
    def test_all_requested_cases_and_both_evidence_layers_are_required(self):
        good = {'execute_exit': 0, 'verify_exit': 0, 'source_verified': True, 'platform_verified': True}
        self.assertEqual(acceptance_status([good, good], 2, platform=True), 'passed')
        for rows in ([], [good], [{**good, 'execute_exit': 1}, good],
                     [{**good, 'verify_exit': 1}, good], [{**good, 'platform_verified': False}, good],
                     [{**good, 'source_verified': False}, good]):
            self.assertEqual(acceptance_status(rows, 2, platform=True), 'failed')

    def test_collector_mode_cannot_claim_platform_verified(self):
        record = {'execute_exit': 0, 'verify_exit': 0, 'source_verified': True, 'platform_verified': False}
        self.assertEqual(acceptance_status([record], 1, platform=False), 'passed')
        self.assertEqual(acceptance_status([record], 1, platform=True), 'failed')

    def test_full_suite_always_includes_rag_and_lifecycle_boundaries(self):
        self.assertEqual(set(scenarios()), {'success', 'no-results', 'retrieval-error', 'retry',
                         'changed-corpus', 'zero-usage', 'unknown-usage', 'stream', 'cancel', 'concurrent', 'tool-exception'})

    def test_smoke_runs_the_fixed_matrix_and_records_coverage(self):
        def passed_case(root, out, repo, framework, sdk, scenario, platform_mode, targets):
            return {'case': f'{framework}-{sdk}-{scenario}', 'execute_exit': 0, 'verify_exit': 0,
                    'source_verified': True, 'platform_verified': platform_mode}

        targets = {sdk: {'data_backend': 'oceanbase'} for sdk in ('langfuse', 'nemo-relay')}
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            'AIVISION_E2E_DATA_BACKEND': 'oceanbase', 'AIVISION_E2E_TARGETS_JSON': json.dumps(targets),
        }), patch.object(sys, 'argv', ['suite.py', '--mode', 'ci', '--out', directory, '--sdk-coverage', 'smoke']), \
                patch.object(suite, 'run_case', side_effect=passed_case), \
                patch.object(suite.importlib.metadata, 'version', return_value='4.16.0'), redirect_stdout(StringIO()):
            self.assertEqual(suite.main(), 0)
            summary = json.loads((Path(directory) / 'summary.json').read_text(encoding='utf-8'))
        self.assertEqual(summary['sdk_coverage'], 'smoke')
        self.assertEqual(summary['expected_cases'], 20)
        self.assertEqual({row['case'] for row in summary['cases']}, {
            f'{framework}-{sdk}-{scenario}' for framework in ('langchain', 'deepagents')
            for sdk in ('langfuse', 'nemo-relay')
            for scenario in ('success', 'unknown-usage', 'stream', 'cancel', 'concurrent')
        })

    def test_smoke_rejects_collector_mode_and_arbitrary_subsets(self):
        with tempfile.TemporaryDirectory() as directory:
            for options in (['--mode', 'collector'], ['--mode', 'ci', '--scenarios=success'],
                            ['--mode', 'ci', '--sdks', 'langfuse']):
                with self.subTest(options=options), patch.object(sys, 'argv', [
                    'suite.py', '--out', directory, '--sdk-coverage', 'smoke', *options,
                ]), redirect_stderr(StringIO()) as errors, self.assertRaises(SystemExit):
                    suite.main()
                self.assertIn('CI', errors.getvalue())

    def test_ci_storage_has_no_fallback(self):
        for value in ('oceanbase', 'seekdb'):
            self.assertEqual(ci_backend(value), value)
        for value in ('', 'sqlite', 'postgres', 'fake', None):
            with self.assertRaises(ValueError):
                ci_backend(value)

    def test_selected_sdk_profile_requires_its_exact_target(self):
        validate_ci_targets('oceanbase', {'langfuse': {'data_backend': 'oceanbase'}}, ['langfuse'])
        for sdks in ([], ['other'], ['langfuse', 'langfuse']):
            with self.assertRaises(ValueError):
                validate_ci_targets('oceanbase', {'langfuse': {'data_backend': 'oceanbase'}}, sdks)

    def test_ci_rejects_another_backend_or_incomplete_target_matrix(self):
        targets = {sdk: {'data_backend': 'oceanbase'} for sdk in ('langfuse', 'nemo-relay')}
        validate_ci_targets('oceanbase', targets)
        for invalid in ({}, {'langfuse': targets['langfuse']},
                        {**targets, 'nemo-relay': {'data_backend': 'seekdb'}},
                        {**targets, 'langfuse': {}}):
            with self.assertRaises(ValueError):
                validate_ci_targets('oceanbase', invalid)


if __name__ == '__main__':
    unittest.main()
