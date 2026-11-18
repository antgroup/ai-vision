#!/usr/bin/env python3
"""Check replay content modes, legacy numeric attributes and historical defaults."""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import subprocess
import tempfile

from privacy_hook_probe import fixture, MARKER, ROOT


def statistics(value):
    """Retain structural/usage evidence, excluding random IDs and content."""
    result = Counter()
    if isinstance(value, dict):
        key, data = value.get('key', ''), value.get('value')
        if isinstance(data, dict) and (key.startswith('gen_ai.usage.') or key in (
                'gen_ai.span.kind', 'gen_ai.operation.name', 'agentic.tool.count',
                'agentic.tool.duration_ms', 'agentic.llm.duration_ms', 'agentic.turn.trigger')):
            result[(key, json.dumps(data, sort_keys=True))] += 1
        if 'spanId' in value and 'startTimeUnixNano' in value:
            result[('span', 'count')] += 1
            result[('status', json.dumps(value.get('status', {}).get('code')))] += 1
        for child in value.values():
            result.update(statistics(child))
    elif isinstance(value, list):
        for child in value:
            result.update(statistics(child))
    return result


def probe(root, validate=False):
    for agent, family, binary, short in (
            ('codex', 'codex', 'cdxhooks', 'cdx'),
            ('claude-code', 'claudecode', 'cchooks', 'cc'),
            ('kimi', 'kimi', 'kimihooks', 'km')):
        folder = root / agent
        folder.mkdir(parents=True, exist_ok=True)
        source = fixture(agent, folder, 'replay-privacy-fixture')['transcript_path']
        env = {key: value for key, value in os.environ.items() if not key.startswith(
            ('AIVISION_', 'OTEL_', 'CCHOOKS_', 'CDXHOOKS_', 'KIMI_', 'KIMIHOOKS_', 'LANGFUSE_'))}
        summaries = []
        for mode in ('full', 'privacy'):
            output = folder / (mode + '.jsonl')
            argv = [str(ROOT / family / binary / 'target/debug' / (binary + '-replay')),
                    source, '--otel', str(output)]
            # Historical replay defaults to Full even in a Privacy shell.
            if mode == 'privacy':
                argv += ['--collection-mode', 'Privacy']
            result = subprocess.run(argv, env={**env, 'AIVISION_COLLECTION_MODE': 'privacy' if mode == 'full' else 'full'},
                                    capture_output=True, text=True, timeout=30)
            assert result.returncode == 0, result.stderr
            text = output.read_text()
            assert (MARKER in text) == (mode == 'full'), f'{agent}/{mode}: replay content boundary'
            summaries.append(statistics([json.loads(line) for line in text.splitlines() if line.strip()]))
            if validate:
                result = subprocess.run(['make', 'validate-one', short, 'FILE=' + str(output), 'FORMAT=json'],
                                        cwd=ROOT, capture_output=True, text=True)
                (folder / (mode + '-validation.log')).write_text(result.stdout + result.stderr)
                assert result.returncode == 0, f'{agent}/{mode}: see {folder / (mode + "-validation.log")}'
        assert summaries[0] == summaries[1], f'{agent}: Privacy changed structure, status or usage: {summaries[0] - summaries[1]}'
        print(f'{agent}: replay defaults, content filtering, topology, status and usage match', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='Retain fixture and validation evidence')
    parser.add_argument('--validate', action='store_true', help='Also run the built weaver-guard')
    args = parser.parse_args()
    if args.output:
        probe(args.output.resolve(), args.validate)
    else:
        with tempfile.TemporaryDirectory(prefix='aiv-privacy-replay-') as directory:
            probe(Path(directory), args.validate)
