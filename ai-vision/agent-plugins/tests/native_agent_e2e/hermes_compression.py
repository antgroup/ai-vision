"""Host automatic compaction with three user turns across two native ACP processes."""
from dataclasses import asdict
import argparse
import json
from pathlib import Path
import re

from hermes_turn_numbering import assess as assess_numbering
from models import CaseContext


def assess(report):
    from evidence_integrity import exports_match_raw
    result = assess_numbering(report)
    checks = result['checks']
    checks['native.exports_match_raw'] = exports_match_raw(report.get('exports'))
    details = report['result']['details']
    raw = details.get('hermes_raw_callbacks', [])
    native = [row['rawInput'] for row in sorted(raw, key=lambda row: row['capturedAt'])
              if row['hook'] == 'pre_llm_call']
    expected = report.get('prompts', [])
    processes = details.get('acp_processes', [])
    checks['compression.native_processes'] = (len(processes) == 2
        and len({row.get('pid') for row in processes}) == 2
        and all(type(row.get('pid')) is int and row['pid'] > 0 and row.get('exit_code') == 0 for row in processes)
        and [row.get('turns') for row in processes] == [[1, 2], [3]])
    checks['compression.only_user_turns'] = (len(expected) == 3
        and [row.get('user_message') for row in native] == expected)
    compression = details.get('compression', {})
    match = re.search(r'Conversation: (\d+) messages', compression.get('context_before', ''))
    before = int(match.group(1)) if match else 0
    settings = compression.get('config', {})
    checks['compression.native_trigger'] = (compression.get('trigger') == 'threshold'
        and len(processes) == 2 and compression.get('process_pid') == processes[1].get('pid')
        and settings.get('enabled') is True and settings.get('in_place') is True
        and settings.get('threshold_tokens') == 3000
        and before > 0)
    terminal = [row['rawInput'] for row in raw if row['hook'] == 'post_llm_call'
                and native and row['rawInput'].get('turn_id') == native[-1].get('turn_id')]
    history = terminal[0].get('conversation_history', []) if len(terminal) == 1 else []
    summary = [row for row in history if row.get('_compressed_summary') is True]
    original_users = [row.get('content') for row in history
                      if row.get('role') == 'user' and row.get('_compressed_summary') is not True]
    previous = native[-2].get('conversation_history', []) if len(native) > 1 else []
    previous_users = [row.get('content') for row in previous if row.get('role') == 'user']
    removed = [text for text in expected[:2] if text in previous_users and text not in original_users]
    checks['compression.raw_history_shortened'] = (bool(summary) and 0 < len(history) < before + 1
        and bool(removed) and len(expected) == 3 and expected[0] in removed
        and expected[2] in original_users)
    third_turn = native[-1].get('turn_id') if len(native) == 3 else None
    api = sorted((row['rawInput'] for row in raw if row['hook'] == 'pre_api_request'
                  and third_turn and row['rawInput'].get('turn_id') == third_turn),
                 key=lambda row: row.get('started_at', 0))
    pre_summary = [row['rawInput'] for row in raw if row['hook'] == 'pre_auxiliary_call'
                   and row['rawInput'].get('aux_task') == 'compression']
    post_summary = [row['rawInput'] for row in raw if row['hook'] == 'post_auxiliary_call'
                    and row['rawInput'].get('aux_task') == 'compression']
    scoped = (len(pre_summary) == len(post_summary) == 1 and bool(third_turn)
        and all(row.get('session_id') == report['result']['session_id'] and row.get('turn_id') == third_turn
                for row in pre_summary + post_summary)
        and bool(pre_summary[0].get('api_request_id'))
        and pre_summary[0].get('api_request_id') == post_summary[0].get('api_request_id'))
    checks['compression.scoped_summary'] = bool(scoped)
    before_api = api[0].get('request_messages', []) if api else []
    after_api = api[-1].get('request_messages', []) if api else []
    before_users = [row.get('content') for row in before_api if row.get('role') == 'user']
    after_users = [row.get('content') for row in after_api if row.get('role') == 'user']
    checks['compression.raw_api_history_shortened'] = (scoped and len(api) >= 2 and len(expected) == 3
        and 0 < len(after_api) < len(before_api) and expected[0] in before_users
        and expected[0] not in after_users and expected[2] in after_users
        and any(row.get('content') in after_users for row in summary)
        and api[0].get('started_at', 0) < pre_summary[0].get('started_at', 0)
        <= post_summary[0].get('ended_at', 0) <= api[-1].get('started_at', 0))
    spans = [row for export in report['exports'] for row in export.get('spans', [])]
    ordinals = {row.get('turn_id'): index for index, row in enumerate(native, 1)}
    children = [row['attributes'] for row in spans
                if row['attributes'].get('gen_ai.span.kind') in ('LLM', 'TOOL')]
    checks['compression.child_ordinals'] = bool(children) and all(
        type(row.get('agentic.turn.number')) is int
        and row.get('agentic.turn.number') == ordinals.get(row.get('gen_ai.chat.id')) for row in children)
    result.update(passed=all(checks.values()), compressed_history='official automatic threshold compaction and native raw hook history',
        compression_history={'previous_turn_messages': before, 'before_messages': before + 1,
            'final_turn_messages': len(history), 'summary_messages': len(summary), 'removed_user_prompts': removed,
            'before_api_messages': len(before_api), 'after_api_messages': len(after_api)})
    return result


def main():
    from capture import Capture
    from drivers.hermes_compression import run_case
    from fake_provider import FakeProvider
    from run_suite import load_runtimes
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtimes', type=Path, required=True)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[4])
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    runtime = load_runtimes(args.runtimes, ['hermes'])['hermes']
    args.out.mkdir(parents=True, exist_ok=False)
    workspace = args.out / 'workspace'
    workspace.mkdir()
    receipt = workspace / 'receipt.txt'
    receipt.write_text('receipt: native-compression\n')
    background = ('Historical background item for the later summary; no additional action is required.\n' * 90).rstrip()
    prompts = ['Read receipt.txt and return its receipt.\n\nBackground:\n' + background, 'Read receipt.txt again and return its receipt.',
               'Read receipt.txt a third time and return its receipt.']
    with Capture() as collector, FakeProvider(receipt, 'multi-turn', length_continuation=True, request_sized_usage=True) as provider:
        context = CaseContext(repo=args.repo.resolve(), out=args.out / 'run', workspace=workspace,
            runtime=tuple(runtime), runtime_version='0.21.5', scenario='multi-turn',
            provider_url=provider.url, collector_url=collector.url, prompt=prompts[0],
            followup_prompt=prompts[1], timeout_seconds=180,
            resource={'ant.agent.type': 'hermes', 'ant.agent.product': 'Hermes', 'ant.agent.instance': 'compression-test'})
        native = run_case(context, third_prompt=prompts[2])
    report = {'result': asdict(native), 'requests': provider.requests, 'exports': collector.exports,
              'receipt': 'native-compression', 'model': context.model, 'expected_resource': context.resource,
              'prompts': prompts}
    report['verification'] = assess(report)
    (args.out / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report['verification'], indent=2))
    return 0 if report['verification']['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
