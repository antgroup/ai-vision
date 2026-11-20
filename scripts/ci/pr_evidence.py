#!/usr/bin/env python3
"""Check PR naming and Markdown completeness from inert event data."""
import argparse
import json
from pathlib import Path
import re

SECTION_HINTS = {
    'Problem and impact': 'State the problem, change, and affected users or components.',
    'Validation': 'Give a named check and its actual result, or explain what blocked it.',
    'Screenshots and interaction': 'Link runtime evidence or explain the lack of visual or interaction impact.',
    'Compatibility and unverified scope': 'Describe compatibility impact and any relevant unverified scope.',
}
# Reject standalone placeholders while allowing sentences with specific evidence or limitations.
PLACEHOLDERS = {
    'todo', 'tbd', 'n/a', 'na', 'none', 'unknown', 'fill in', 'not applicable',
    'passed', 'tests passed', 'all tests passed', 'see ci', 'ci pending', 'see above',
    'screenshots attached', '待填写', '待填', '请填写', '无', '不适用', '通过', '测试通过', '详见 ci',
}
FIELD_LABELS = {
    'actual result', 'ci', 'command', 'environment', 'evidence', 'expected result',
    'result', 'screenshots', 'test', 'tests', 'validation', '版本', '命令', '环境', '结果', '验证',
}


WORK_TYPES = {
    'feature': 'feat', 'cicd': 'ci', **{name: name for name in (
        'fix', 'docs', 'build', 'test', 'refactor', 'perf', 'style', 'chore', 'revert',
    )},
}
SLUG = r'[a-z0-9]+(?:-[a-z0-9]+)*'
VERSION_PART = r'(?:0|[1-9][0-9]*)'
VERSION = rf'{VERSION_PART}\.{VERSION_PART}\.{VERSION_PART}'
WORK_BRANCH = re.compile(rf'({"|".join(WORK_TYPES)})/{SLUG}')
RELEASE_BRANCH = re.compile(rf'(release|hotfix)/{VERSION}')
TITLE_TYPES = '|'.join((*WORK_TYPES.values(), 'release'))
PR_TITLE = re.compile(rf'({TITLE_TYPES})(?:\({SLUG}\))?!?: (\S[^\r\n]*)')


def validate_naming(branch, title, *, base=None, same_repo=False):
    errors = []
    expected_type = None
    work = WORK_BRANCH.fullmatch(branch) if isinstance(branch, str) else None
    release = RELEASE_BRANCH.fullmatch(branch) if isinstance(branch, str) else None
    if work:
        expected_type = WORK_TYPES[work[1]]
    elif release and same_repo and base == 'main':
        expected_type = 'release' if release[1] == 'release' else 'fix'
    elif branch == 'main' and same_repo and (
        base == 'develop' or (isinstance(base, str) and re.fullmatch(rf'release/{VERSION}', base))
    ):
        expected_type = 'release'
    elif release or branch == 'main':
        errors.append('Release, hotfix and backmerge PRs must use the documented target and come from this repository.')
    else:
        errors.append('Branch name must use a work type and lowercase kebab-case slug, e.g. feature/trace-export or cicd/cache-tests. See CONTRIBUTING.md.')

    match = PR_TITLE.fullmatch(title) if isinstance(title, str) else None
    if not match or match[2] != match[2].rstrip():
        errors.append('PR title must use type[(scope)][!]: summary, e.g. feat(cli): Add trace export. See CONTRIBUTING.md.')
    elif expected_type and match[1] != expected_type:
        errors.append(f'PR title type must match the branch prefix or release flow. Use {expected_type}: as the title type.')
    return errors


def validate_event_naming(pr):
    head = pr['head']
    base = pr['base']
    branch = head['ref']
    user = pr['user']
    if (user['type'] == 'Bot' and user['login'] == 'dependabot[bot]'
            and isinstance(branch, str) and branch.startswith('dependabot/')):
        return [], 'Dependabot naming exception applied.'
    # Repository IDs are immutable. A fork's main is never a release backmerge.
    head_id = (head.get('repo') or {}).get('id')
    base_id = (base.get('repo') or {}).get('id')
    same_repo = type(head_id) is int and head_id == base_id
    return validate_naming(branch, pr.get('title'), base=base['ref'], same_repo=same_repo), None


def has_content(lines):
    for line in lines:
        value = re.sub(r'^\s*(?:[-*+]\s*|\d+[.)]\s*)?(?:\[[ xX]\]\s*)?', '', line).strip()
        value = value.strip('`*_ \t')
        if not value or value.startswith('#') or value.endswith((':', '：')):
            continue
        if re.fullmatch(r'<[^>]*>|[-—_.…]+', value):
            continue
        if value.rstrip('.。!！').casefold() in PLACEHOLDERS:
            continue
        # Check known empty field labels and keep sentences such as "Ran unittest: tests passed."
        parts = re.split(r'[:：]', value, maxsplit=1)
        if len(parts) == 2 and parts[0].strip('`*_ ').casefold() in FIELD_LABELS:
            result = parts[1].strip('`*_ ').rstrip('.。!！')
            if result.casefold() in PLACEHOLDERS or re.fullmatch(r'<[^>]*>', result):
                continue
        return True
    return False


def validate_body(body):
    if not isinstance(body, str) or not body.strip() or len(body.encode('utf-8')) > 262144:
        return ['The PR body is empty, is not text, or exceeds the size limit. Complete the template.']
    body = re.sub(r'<!--.*?-->', '', body, flags=re.DOTALL)
    sections = {title: [] for title in SECTION_HINTS}
    current, fence = None, None
    for line in body.splitlines():
        marker = re.match(r'^\s*(`{3,}|~{3,})(.*)$', line)
        if marker:
            if fence is None:
                fence = marker[1]
            elif marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not marker[2].strip():
                fence = None
            continue
        heading = re.match(r'^ {0,3}#{1,2}\s+(.+?)\s*#*\s*$', line) if fence is None else None
        if heading:
            title = heading[1]
            current = [] if title in sections and line.lstrip().startswith('## ') else None
            if current is not None:
                sections[title].append(current)
        elif current is not None:
            current.append(line)
    errors = []
    for title, hint in SECTION_HINTS.items():
        entries = sections[title]
        if not entries:
            errors.append(f'Missing section: {title}. {hint}')
        elif len(entries) != 1:
            errors.append(f'Duplicate section: {title}. Keep exactly one.')
        elif not has_content(entries[0]):
            errors.append(f'Empty section: {title}. {hint}')
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument('--event', type=Path, help='GitHub pull_request event JSON')
    inputs.add_argument('--body-file', type=Path, help='PR Markdown before submission')
    parser.add_argument('--title', help='Ordinary PR title for --body-file preflight')
    parser.add_argument('--branch', help='Ordinary PR head branch for --body-file preflight')
    parser.add_argument('--summary', type=Path, help='Append a fixed summary without raw PR metadata')
    args = parser.parse_args()
    names_requested = args.title is not None or args.branch is not None
    if names_requested and (args.body_file is None or args.title is None or args.branch is None):
        parser.error('--title and --branch must be supplied together with --body-file')
    naming_errors, naming_note = [], None
    try:
        if args.body_file is not None:
            body = args.body_file.read_text(encoding='utf-8')
            if names_requested:
                naming_errors = validate_naming(args.branch, args.title)
        else:
            event = json.loads(args.event.read_text(encoding='utf-8'))
            pr = event['pull_request']
            body = pr.get('body')
            naming_errors, naming_note = validate_event_naming(pr)
    except (OSError, ValueError, KeyError, AttributeError, TypeError):
        errors = ['Cannot read the PR input. Check the input file and rerun.']
    else:
        errors = naming_errors + validate_body(body)
    lines = ['## PR naming and material completeness check', '']
    if naming_note:
        lines += [naming_note, '']
    lines += ['Information required:', '', *('- ' + error for error in errors)] if errors else ['All four required sections contain content.']
    if not errors and not naming_note and (args.event is not None or names_requested):
        lines.append('Branch name and PR title pass the naming check.')
    lines += ['', 'The material check does not authenticate tests, screenshots, tested SHAs, or business results; maintainers must still perform manual review.',
              'This result applies to the triggering input snapshot; it does not provide an atomic gate between metadata edits and merging.', '']
    summary = '\n'.join(lines)
    if args.summary:
        with args.summary.open('a', encoding='utf-8') as output:
            output.write(summary)
    # Never copy raw PR metadata, commands, or links into workflow logs.
    print(summary)
    return 1 if errors else 0


if __name__ == '__main__':
    raise SystemExit(main())
