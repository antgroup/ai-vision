#!/usr/bin/env python3
"""Verify an explicit PR body reference to an existing issue in the target repository."""
import argparse
import json
import os
from pathlib import Path
import re
from urllib.error import HTTPError
from urllib.request import Request, urlopen

LIST_MARKER = r'(?:(?:[-*+]|[0-9]+[.)])[ \t]+)?'
FENCE = re.compile(rf'^[ \t]*{LIST_MARKER}(`{{3,}}|~{{3,}})(.*)$')
REFERENCE_KEYWORD = r'(?:issue[ \t]*:|(?:refs|close[sd]?|fix(?:es|ed)?|resolve[sd]?)[ \t]*:?)'
ISSUE_NUMBER = r'([1-9][0-9]{0,9})'
ISSUE_HINT = 'Add `Issue: #123` or a full link to an existing issue in this repository to the PR description.'
MAX_ISSUE_LOOKUPS = 20


def issue_references(body, repository, server_url):
    if not isinstance(body, str):
        return []
    body = re.sub(r'<!--.*?(?:-->|$)', '', body, flags=re.DOTALL)
    visible = []
    fence = None
    for line in body.splitlines():
        if line.lstrip().startswith('>'):
            continue
        marker = FENCE.match(line)
        if marker:
            delimiter, suffix = marker.groups()
            if fence is None:
                fence = delimiter
            elif delimiter.startswith(fence) and not suffix.strip():
                fence = None
        elif fence is None and not line.startswith(('    ', '\t')):
            visible.append(line)

    text = re.sub(r'(?<!`)(`+)(?!`)[\s\S]*?(?<!`)\1(?!`)', '', '\n'.join(visible))
    url_prefix = re.escape(f'{server_url.rstrip("/")}/{repository}/issues/')
    reference = re.compile(
        rf'^ {{0,3}}{LIST_MARKER}{REFERENCE_KEYWORD}[ \t]+#{ISSUE_NUMBER}(?![\w#/-])'
        rf'|(?<![\w./:=\\]){url_prefix}{ISSUE_NUMBER}(?=$|[\s<>()\[\],!\"\'?#]|\.(?:\s|$))',
        re.IGNORECASE | re.MULTILINE,
    )
    return list(dict.fromkeys(int(match[1] or match[2]) for match in reference.finditer(text)))


def github_json(url, token):
    request = Request(url, headers={
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
    })
    request.add_unredirected_header('Authorization', f'Bearer {token}')
    with urlopen(request, timeout=10) as response:
        return json.load(response)


def validate_linked_issue(event_pr, server_url, api_url, token):
    repository = event_pr['base']['repo']['full_name']
    pr_number = event_pr['number']
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository) or type(pr_number) is not int or pr_number < 1:
        raise ValueError('Invalid pull request identity')
    if not token:
        return 'The linked issue check requires a GitHub token with read access to pull requests and issues. Configure it and rerun the check.'

    repository_url = f'{api_url.rstrip("/")}/repos/{repository}'
    # GitHub keeps the original event payload when a job is rerun. Read the
    # current description to account for edits that leave the head SHA intact.
    try:
        current_pr = github_json(f'{repository_url}/pulls/{pr_number}', token)
        same_pull_request = (
            current_pr['number'] == pr_number
            and current_pr['base']['repo']['full_name'].casefold() == repository.casefold()
            and current_pr['base']['ref'] == event_pr['base']['ref']
            and current_pr['head']['sha'] == event_pr['head']['sha']
        )
        body = current_pr['body']
    except HTTPError as error:
        return f'GitHub could not read the current pull request (HTTP {error.code}). Check API access and rerun the check.'
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return 'GitHub could not read the current pull request. Check API availability and rerun the check.'
    if not same_pull_request:
        return 'The pull request head or target branch changed. Use the latest run for this pull request.'

    numbers = issue_references(body, repository, server_url)
    if not numbers:
        return ISSUE_HINT
    for number in numbers[:MAX_ISSUE_LOOKUPS]:
        try:
            issue = github_json(f'{repository_url}/issues/{number}', token)
        except HTTPError as error:
            if error.code in (404, 410):
                continue
            return f'GitHub could not verify the linked issue (HTTP {error.code}). Check API access and rerun the check.'
        except (OSError, ValueError):
            return 'GitHub could not verify the linked issue. Check API availability and rerun the check.'

        if not isinstance(issue, dict) or not isinstance(issue.get('repository_url'), str):
            return 'GitHub returned an unexpected issue response. Check API availability and rerun the check.'
        if ('pull_request' not in issue and issue.get('number') == number
                and issue['repository_url'].casefold() == repository_url.casefold()):
            return None

    if len(numbers) > MAX_ISSUE_LOOKUPS:
        return f'Move a reference to an existing issue into the first {MAX_ISSUE_LOOKUPS} issue references in the PR description.'
    return f'No existing issue was found among the references checked. {ISSUE_HINT}'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--event', required=True, type=Path, help='GitHub pull_request event JSON')
    parser.add_argument('--summary', type=Path, help='Append the linked issue validation result')
    args = parser.parse_args()
    try:
        event = json.loads(args.event.read_text(encoding='utf-8'))
        error = validate_linked_issue(
            event['pull_request'], os.environ['GITHUB_SERVER_URL'], os.environ['GITHUB_API_URL'],
            os.environ.get('GH_TOKEN'),
        )
    except (OSError, ValueError, KeyError, AttributeError, TypeError):
        error = 'Cannot read the PR event or GitHub repository configuration. Check the workflow inputs and rerun the check.'

    summary = f'\n## Linked issue check\n\n{error or "The PR description references an existing issue in the target repository."}\n'
    if args.summary:
        with args.summary.open('a', encoding='utf-8') as output:
            output.write(summary)
    print(summary)
    return 1 if error else 0


if __name__ == '__main__':
    raise SystemExit(main())
