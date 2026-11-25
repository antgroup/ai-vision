"""Test the PR issue gate against representative GitHub API responses."""
import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import pr_issue

SERVER_URL = 'https://github.com'
API_URL = 'https://api.github.com'
REPOSITORY = 'antgroup/ai-vision'
REPOSITORY_URL = f'{API_URL}/repos/{REPOSITORY}'
PULL_REQUEST_URL = f'{REPOSITORY_URL}/pulls/42'
HEAD_SHA = 'a' * 40


def pull_request(body, *, head_sha=HEAD_SHA, base_ref='main'):
    return {
        'number': 42,
        'head': {'sha': head_sha},
        'base': {'ref': base_ref, 'repo': {'full_name': REPOSITORY}},
        'body': body,
    }


class LinkedIssueTests(unittest.TestCase):
    def check(self, body, responses, *, current_pr=None):
        event_pr = pull_request(body)
        responses = {PULL_REQUEST_URL: event_pr if current_pr is None else current_pr, **responses}

        def get(request, *, timeout):
            self.assertEqual(request.get_header('Authorization'), 'Bearer test-token')
            response = responses[request.full_url]
            if isinstance(response, int):
                raise HTTPError(request.full_url, response, '', None, None)
            return io.BytesIO(json.dumps(response).encode())

        with patch('pr_issue.urlopen', side_effect=get):
            return pr_issue.validate_linked_issue(event_pr, SERVER_URL, API_URL, 'test-token')

    def test_accepts_explicit_references_to_an_open_or_closed_issue(self):
        for body, state in (
            ('Issue: #123', 'open'),
            ('- Refs #123', 'closed'),
            ('Closes: #123', 'open'),
            ('Fixes #123', 'open'),
            ('Resolves #123', 'open'),
            (f'See [tracking issue]({SERVER_URL}/{REPOSITORY}/issues/123).', 'closed'),
        ):
            with self.subTest(body=body):
                self.assertIsNone(self.check(body, {
                    f'{REPOSITORY_URL}/issues/123': {'number': 123, 'state': state, 'repository_url': REPOSITORY_URL},
                }))

    def test_ignores_hidden_or_unrelated_references(self):
        for body in (
            '',
            'This changes the configuration on line #123.',
            '<!--\nIssue: #123\n-->',
            '<!--\nIssue: #123',
            '```markdown\nIssue: #123\n```',
            f'Use `{SERVER_URL}/{REPOSITORY}/issues/123` in the example.',
            '> Issue: #123',
            f'Issue: {SERVER_URL}/another/repo/issues/123',
            f'Issue: {SERVER_URL}/{REPOSITORY}/pull/123',
        ):
            with self.subTest(body=body):
                self.assertIn('Issue: #123', self.check(body, {}))

    def test_rejects_a_pr_a_missing_issue_and_a_transferred_issue(self):
        for response in (
            {'number': 123, 'repository_url': REPOSITORY_URL, 'pull_request': {}},
            404,
            {'number': 123, 'repository_url': f'{API_URL}/repos/another/repo'},
        ):
            with self.subTest(response=response):
                error = self.check('Refs #123', {f'{REPOSITORY_URL}/issues/123': response})
                self.assertIn('existing issue', error)

    def test_one_valid_issue_is_enough(self):
        self.assertIsNone(self.check('Issue: #123\n\nRefs #456', {
            f'{REPOSITORY_URL}/issues/123': 404,
            f'{REPOSITORY_URL}/issues/456': {'number': 456, 'repository_url': REPOSITORY_URL},
        }))

    def test_api_failure_has_a_retry_diagnostic(self):
        error = self.check('Refs #123', {f'{REPOSITORY_URL}/issues/123': 403})
        self.assertIn('rerun', error)
        self.assertIn('403', error)

    def test_current_body_controls_reruns_on_the_same_commit(self):
        issue = {'number': 123, 'repository_url': REPOSITORY_URL}
        responses = {f'{REPOSITORY_URL}/issues/123': issue}
        with self.subTest('a removed issue must fail a rerun'):
            error = self.check('Refs #123', responses, current_pr=pull_request(''))
            self.assertIn('Issue: #123', error or '')
        with self.subTest('an added issue must pass a rerun'):
            self.assertIsNone(self.check('', responses, current_pr=pull_request('Refs #123')))

    def test_old_head_or_target_branch_cannot_pass(self):
        issue = {'number': 123, 'repository_url': REPOSITORY_URL}
        responses = {f'{REPOSITORY_URL}/issues/123': issue}
        for current_pr in (
            pull_request('Refs #123', head_sha='b' * 40),
            pull_request('Refs #123', base_ref='develop'),
        ):
            with self.subTest(current_pr=current_pr):
                error = self.check('Refs #123', responses, current_pr=current_pr)
                self.assertIn('latest run', error or '')

    def test_unavailable_current_pr_fails_closed(self):
        issue = {'number': 123, 'repository_url': REPOSITORY_URL}
        error = self.check('Refs #123', {PULL_REQUEST_URL: 404, f'{REPOSITORY_URL}/issues/123': issue})
        self.assertIn('current pull request', error or '')
        self.assertIn('404', error or '')


if __name__ == '__main__':
    unittest.main()
