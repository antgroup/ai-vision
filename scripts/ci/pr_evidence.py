#!/usr/bin/env python3
"""Check PR Markdown completeness only; never execute or authenticate evidence."""
import argparse
import json
from pathlib import Path
import re

SECTIONS = ('Problem and impact', 'Validation', 'Screenshots and interaction', 'Compatibility and unverified scope')
# Keep translated and legacy placeholders invalid; diagnostics are always English.
PLACEHOLDERS = {'todo', 'tbd', 'n/a', 'na', 'none', 'unknown', 'fill in', 'not applicable', 'passed', '待填写', '待填', '请填写', '无', '不适用', '通过'}


def has_content(lines):
    for line in lines:
        value = re.sub(r'^\s*(?:[-*+]\s*|\d+[.)]\s*)?(?:\[[ xX]\]\s*)?', '', line).strip()
        value = value.strip('`*_ \t')
        if not value or value.startswith('#') or value.endswith((':', '：')):
            continue
        if re.fullmatch(r'<[^>]*>|[-—_.…]+', value):
            continue
        # Empty template labels with a placeholder after the colon are not evidence.
        tail = re.split(r'[:：]', value, maxsplit=1)[-1].strip().strip('`*_ ')
        if value.lower() in PLACEHOLDERS or tail.lower() in PLACEHOLDERS or re.fullmatch(r'<[^>]*>', tail):
            continue
        return True
    return False


def validate_body(body):
    if not isinstance(body, str) or not body.strip() or len(body.encode('utf-8')) > 262144:
        return ['The PR body is empty, is not text, or exceeds the size limit. Complete the template.']
    body = re.sub(r'<!--.*?-->', '', body, flags=re.DOTALL)
    sections = {title: [] for title in SECTIONS}
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
    for title, entries in sections.items():
        if not entries:
            errors.append(f'Missing section: {title}.')
        elif len(entries) != 1:
            errors.append(f'Duplicate section: {title}. Keep exactly one.')
        elif not has_content(entries[0]):
            errors.append(f'Empty section: {title}. Provide actual information or explain why it does not apply.')
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--event', required=True, type=Path, help='GitHub pull_request event JSON')
    parser.add_argument('--summary', type=Path, help='Append a fixed, body-free Actions summary')
    args = parser.parse_args()
    try:
        event = json.loads(args.event.read_text(encoding='utf-8'))
        errors = validate_body(event['pull_request'].get('body'))
    except (OSError, ValueError, KeyError, AttributeError, TypeError):
        errors = ['Cannot read the PR event body. Check the event file and rerun.']
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
