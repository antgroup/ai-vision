"""Exercise the Markdown checker through the same CLI used by Actions."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).with_name('pr_evidence.py')
TEMPLATE = SCRIPT.parents[2] / '.github' / 'PULL_REQUEST_TEMPLATE.md'
BODY = '''## Problem and impact
Fix the version page incorrectly reporting up to date when no stable release exists.

## Validation
macOS arm64; ran backend tests on a dirty worktree based on abc123: 36 passed. Diff attached.

## Screenshots and interaction
New page with no before screenshot; after screenshot and refresh-button behavior results are attached.

## Compatibility and unverified scope
No database migration. Linux and released artifacts are unverified and need checks before release.
'''


class EvidenceTests(unittest.TestCase):
    def check(self, body, *, body_file=False, metadata=None, extra_args=()):
        with tempfile.TemporaryDirectory() as directory:
            summary = Path(directory) / 'summary.md'
            if body_file:
                input_file = Path(directory) / 'body.md'
                input_file.write_text(body, encoding='utf-8')
                mode = '--body-file'
            else:
                input_file = Path(directory) / 'event.json'
                pull_request = {
                    'body': body, 'title': 'fix(cli): Report missing stable versions',
                    'head': {'ref': 'fix/stable-version', 'repo': {'id': 2}},
                    'base': {'ref': 'ci', 'repo': {'id': 1}},
                    'user': {'login': 'contributor', 'type': 'User'},
                }
                pull_request.update(metadata or {})
                input_file.write_text(json.dumps({'pull_request': pull_request}))
                mode = '--event'
            result = subprocess.run(
                [sys.executable, str(SCRIPT), mode, str(input_file), '--summary', str(summary), *extra_args],
                text=True, capture_output=True, check=False,
            )
            return result, summary.read_text() if summary.exists() else ''

    def test_completed_markdown_and_honest_unverified_scope_are_accepted(self):
        result, summary = self.check(BODY)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('manual review', summary)
        self.assertNotIn('36 passed', summary)

    def test_generated_names_and_untyped_titles_fail_independently(self):
        for branch, title, expected in (
            ('codex/stable-version', 'fix: Report missing versions', 'Branch name'),
            ('fix/stable-version', 'Report missing versions', 'PR title'),
            ('feature/cli-oauth', 'docs: Explain OAuth', 'match the branch prefix'),
            ('ci/stable-version', 'ci: Update checks', 'Branch name'),
            ('fix/a/b', 'fix: Report missing versions', 'Branch name'),
            ('fix/Upper_case', 'fix: Report missing versions', 'Branch name'),
            ('fix/stable-version', 'fix(cli): ', 'PR title'),
            ('fix/stable-version', 'fix: Update\nversions', 'PR title'),
        ):
            with self.subTest(branch=branch, title=title):
                result, summary = self.check(BODY, metadata={'head': {'ref': branch}, 'title': title})
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn(expected, summary)

    def test_release_routes_require_same_repository_and_expected_base(self):
        for branch, base, title, same_repo, expected in (
            ('release/0.6.0', 'main', 'release: Prepare 0.6.0', True, 0),
            ('hotfix/0.6.1', 'main', 'fix!: Repair stable installs', True, 0),
            ('main', 'develop', 'release: Backmerge 0.6.0', True, 0),
            ('main', 'release/0.6.1', 'release: Backport the stable fix', True, 0),
            ('release/0.6.0', 'main', 'release: Prepare 0.6.0', False, 1),
            ('release/0.6.0', 'ci', 'release: Prepare 0.6.0', True, 1),
            ('release/0.6.0', 'main', 'feat: Prepare 0.6.0', True, 1),
            ('hotfix/v0.6.1', 'main', 'fix: Repair stable installs', True, 1),
            ('main', 'develop', 'release: Backmerge 0.6.0', False, 1),
        ):
            with self.subTest(branch=branch, base=base, title=title, same_repo=same_repo):
                result, _ = self.check(BODY, metadata={
                    'title': title, 'head': {'ref': branch, 'repo': {'id': 1 if same_repo else 2}},
                    'base': {'ref': base, 'repo': {'id': 1}},
                })
                self.assertEqual(result.returncode, expected, result.stdout)

    def test_dependabot_exception_preserves_body_check(self):
        metadata = {'title': 'Bump example from 1 to 2', 'head': {'ref': 'dependabot/go_modules/example'},
                    'user': {'login': 'dependabot[bot]', 'type': 'Bot'}}
        self.assertEqual(self.check(BODY, metadata=metadata)[0].returncode, 0)
        self.assertEqual(self.check('', metadata=metadata)[0].returncode, 1)
        metadata['user']['type'] = 'User'
        self.assertEqual(self.check(BODY, metadata=metadata)[0].returncode, 1)

    def test_local_preflight_accepts_matching_feature_type_with_scope(self):
        args = ('--title', 'feat(agent-lens)!: Add streaming diagnoses', '--branch', 'feature/streaming-diagnoses')
        self.assertEqual(self.check(BODY, body_file=True, extra_args=args)[0].returncode, 0)
        self.assertEqual(self.check(BODY, body_file=True, extra_args=args[:2])[0].returncode, 2)
        args = ('--title', 'ci: Check PR names', '--branch', 'cicd/gitflow-naming')
        self.assertEqual(self.check(BODY, body_file=True, extra_args=args)[0].returncode, 0)

    def test_each_required_section_must_be_present(self):
        for title in ('Problem and impact', 'Validation', 'Screenshots and interaction', 'Compatibility and unverified scope'):
            with self.subTest(title=title):
                result, summary = self.check(BODY.replace('## ' + title, '## Other content'))
                self.assertEqual(result.returncode, 1)
                self.assertIn(title, summary)

    def test_empty_template_comments_labels_and_checkboxes_do_not_count(self):
        # Retain one non-English placeholder to catch leftover template filler.
        for empty in ('', '<!-- Provide the actual command -->', '- Environment:\n- Command:\n- Result:', '- [ ]\n- [x]', 'TODO', 'N/A', 'passed', 'not applicable', 'fill in', '通过', '<fill in validation>', '- Command: <fill in>', '- Result: TODO', 'TODO.', '- [x] Tests passed.', 'See CI.', 'Result: Tests passed.', '测试通过。'):
            with self.subTest(empty=empty):
                body = BODY.replace('macOS arm64; ran backend tests on a dirty worktree based on abc123: 36 passed. Diff attached.', empty)
                result, summary = self.check(body)
                self.assertEqual(result.returncode, 1)
                self.assertIn('Validation', summary)

    def test_body_file_rejects_the_unedited_template(self):
        result, summary = self.check(TEMPLATE.read_text(encoding='utf-8'), body_file=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('Empty section: Problem and impact.', summary)
        self.assertIn('Empty section: Validation.', summary)
        self.assertIn('Empty section: Screenshots and interaction.', summary)
        self.assertIn('Empty section: Compatibility and unverified scope.', summary)

    def test_body_file_accepts_specific_blocked_check(self):
        body = BODY.replace(
            'macOS arm64; ran backend tests on a dirty worktree based on abc123: 36 passed. Diff attached.',
            'The Compose startup check was blocked because this runner has no Docker daemon.',
        )
        result, summary = self.check(body, body_file=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('Docker daemon', summary)

    def test_specific_validation_can_end_with_a_plain_result(self):
        body = BODY.replace(
            'macOS arm64; ran backend tests on a dirty worktree based on abc123: 36 passed. Diff attached.',
            'On Linux amd64, ran `pnpm test` at abc123: tests passed.',
        )
        result, _ = self.check(body)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_explicit_no_visual_change_explanation_is_accepted(self):
        body = BODY.replace('New page with no before screenshot; after screenshot and refresh-button behavior results are attached.', 'No visual or interaction changes: only backend error-handling code changed.')
        result, _ = self.check(body)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_duplicate_section_is_reported_instead_of_ignoring_empty_copy(self):
        result, summary = self.check(BODY + '\n## Validation\n')
        self.assertEqual(result.returncode, 1)
        self.assertIn('Validation', summary)

    def test_commented_and_fenced_headings_do_not_supply_missing_sections(self):
        for wrapper in ('<!--\n{}\n-->', '```markdown\n{}\n```', '~~~markdown\n{}\n~~~'):
            result, summary = self.check(wrapper.format(BODY))
            self.assertEqual(result.returncode, 1)
            self.assertIn('Problem and impact', summary)

    def test_command_output_code_block_counts_as_self_test_content(self):
        body = BODY.replace('macOS arm64; ran backend tests on a dirty worktree based on abc123: 36 passed. Diff attached.', '```text\nRan 36 tests in 1.2s: OK\n```')
        result, _ = self.check(body)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_body_is_data_and_is_not_copied_to_logs_or_summary(self):
        marker = 'UNTRUSTED_MARKER $(exit 27) https://example.invalid/secret'
        result, summary = self.check(BODY + '\n' + marker)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(marker, result.stdout + result.stderr + summary)

    def test_missing_or_nontext_body_fails_with_actionable_summary(self):
        for body in (None, {}, ''):
            result, summary = self.check(body)
            self.assertEqual(result.returncode, 1)
            self.assertIn('body', summary)


if __name__ == '__main__':
    unittest.main()
