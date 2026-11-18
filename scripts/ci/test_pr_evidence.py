"""Exercise the Markdown checker through the same CLI used by Actions."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).with_name('pr_evidence.py')
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
    def check(self, body):
        with tempfile.TemporaryDirectory() as directory:
            event = Path(directory) / 'event.json'
            summary = Path(directory) / 'summary.md'
            event.write_text(json.dumps({'pull_request': {'body': body}}))
            result = subprocess.run(
                [sys.executable, str(SCRIPT), '--event', str(event), '--summary', str(summary)],
                text=True, capture_output=True, check=False,
            )
            return result, summary.read_text() if summary.exists() else ''

    def test_completed_markdown_and_honest_unverified_scope_are_accepted(self):
        result, summary = self.check(BODY)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('manual review', summary)
        self.assertNotIn('36 passed', summary)

    def test_each_required_section_must_be_present(self):
        for title in ('Problem and impact', 'Validation', 'Screenshots and interaction', 'Compatibility and unverified scope'):
            with self.subTest(title=title):
                result, summary = self.check(BODY.replace('## ' + title, '## Other content'))
                self.assertEqual(result.returncode, 1)
                self.assertIn(title, summary)

    def test_empty_template_comments_labels_and_checkboxes_do_not_count(self):
        # Retain one non-English placeholder to catch leftover template filler.
        for empty in ('', '<!-- Provide the actual command -->', '- Environment:\n- Command:\n- Result:', '- [ ]\n- [x]', 'TODO', 'N/A', 'passed', 'not applicable', 'fill in', '通过', '<fill in validation>', '- Command: <fill in>', '- Result: TODO'):
            with self.subTest(empty=empty):
                body = BODY.replace('macOS arm64; ran backend tests on a dirty worktree based on abc123: 36 passed. Diff attached.', empty)
                result, summary = self.check(body)
                self.assertEqual(result.returncode, 1)
                self.assertIn('Validation', summary)

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
