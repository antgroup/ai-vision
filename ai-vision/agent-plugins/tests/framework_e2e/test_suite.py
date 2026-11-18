"""CI cannot turn an empty, skipped or failed matrix into successful acceptance."""
import unittest
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
