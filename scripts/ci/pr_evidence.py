#!/usr/bin/env python3
"""Check PR Markdown completeness only; never execute or authenticate evidence."""
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
    parser.add_argument('--summary', type=Path, help='Append a fixed, body-free Actions summary')
    args = parser.parse_args()
    try:
        if args.body_file is not None:
            body = args.body_file.read_text(encoding='utf-8')
        else:
            event = json.loads(args.event.read_text(encoding='utf-8'))
            body = event['pull_request'].get('body')
    except (OSError, ValueError, KeyError, AttributeError, TypeError):
        errors = ['Cannot read the PR body. Check the input file and rerun.']
    else:
        errors = validate_body(body)
    lines = ['## PR material completeness check', '']
    lines += ['Information required:', '', *('- ' + error for error in errors)] if errors else ['All four required sections contain content.']
    lines += ['', 'This only checks that material is filled in. It does not authenticate tests, screenshots, tested SHAs, or business results; maintainers must still perform manual review.',
              'This result applies to the body snapshot in the triggering event; it does not provide an atomic gate between body edits and merging.', '']
    summary = '\n'.join(lines)
    if args.summary:
        with args.summary.open('a', encoding='utf-8') as output:
            output.write(summary)
    # Never copy arbitrary PR text, commands, or links into workflow logs.
    print(summary)
    return 1 if errors else 0


if __name__ == '__main__':
    raise SystemExit(main())
