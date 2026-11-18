"""The fixed CI Quick source must produce evaluated, positive rule evidence."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_quick import Client
from validate_local import LocalError, ci_owned_fake_after_acceptance


TRACE = 'a' * 32


class CIQuickClient(Client):
    """API boundary for the actual Quick runner, with one successful read."""

    def __init__(self, endpoint, *, target, **kwargs):
        super().__init__()
        self.target = deepcopy(target)
        self.options_row['scenes'][0]['rule_keys'] = ['tool_success']
        self.options_row['templates'][0]['key'] = 'tool_success'
        self.task_row['quick_run'].update(agent_uuid=target['agent_uuid'], trace_ids=[TRACE])
        self.task_row['stats']['diagnoser_definitions']['rule-1']['key'] = 'tool_success'
        self.task_row['stats']['diagnosers']['rule-1'] = {'tool_success_rate': {
            'total': 1, 'good': 1, 'bad': 0, 'failed': 0, 'skipped': 0}}
        self.record_row.update(target_id=TRACE, verdict='good',
            execution={'status': 'succeeded'}, metrics=[{
                'name': 'tool_success_rate', 'verdict': 'good',
                'evidence': {'counts': {'success': 1, 'failure': 0, 'unknown': 0},
                    'bad_spans': []}}])
        self.listed_records = [self.record_row]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def scoped(self, path, *args, **kwargs):
        if path == '/records':
            return {'total': len(self.listed_records),
                'items': [{'uuid': row['uuid']} for row in self.listed_records]}
        if path == '/stats':
            result = super().scoped(path, *args, **kwargs)
            result['tasks'][0]['target_count'] = self.task_row['stats']['source']['total']
            return result
        if path.startswith('/records/'):
            return deepcopy(next(row for row in self.listed_records
                if path == '/records/' + row['uuid']))
        return super().scoped(path, *args, **kwargs)


class CIQuickAcceptanceTests(unittest.TestCase):
    def run_callback(self, mutate=lambda _client: None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'cases/codex-read-only/source.json'
            source.parent.mkdir(parents=True)
            source.write_text(json.dumps({'source': {'verified': True},
                'exports': [{'spans': [{'trace_id': TRACE}]}]}))
            targets = {app: {'endpoint': 'http://127.0.0.1:8001',
                'agent_uuid': app + '-owned', 'space_id': 'owned-space'}
                for app in ('codex', 'dsh02')}
            registered = []

            def client_factory(*args, **kwargs):
                client = CIQuickClient(*args, **kwargs)
                client.target['session_marker'] = 'current-owned-session'
                mutate(client)
                return client

            try:
                with patch('validate_local.PlatformClient', client_factory):
                    return ci_owned_fake_after_acceptance(out=root, runtime=root,
                        targets=targets, register_private_targets=registered.append,
                        result={'verified': True, 'expected_cases': 34, 'executed_cases': 34})
            finally:
                self.assertFalse((root / 'ci-quick-targets.json').exists())
                self.assertEqual(registered[-1]['codex']['session_marker'],
                    'current-owned-session')

    def test_expected_read_reaches_real_quick_runner_and_is_verified(self):
        result = self.run_callback()
        self.assertTrue(result['verified'])
        self.assertEqual(result['quick']['snapshot']['counts']['known'], 1)

    def test_all_skipped_rule_is_not_ci_acceptance(self):
        def skipped(client):
            client.task_row['stats']['diagnosers']['rule-1']['tool_success_rate'].update(
                total=0, good=0, skipped=1)
            client.task_row['stats']['skip_reasons'] = {'rule-1': {'not_applicable': 1}}
            client.listed_records = []
        with self.assertRaises(LocalError):
            self.run_callback(skipped)

    def test_failed_rule_is_not_ci_acceptance(self):
        def failed(client):
            client.task_row['stats']['diagnosers']['rule-1']['tool_success_rate'].update(
                good=0, failed=1)
            client.record_row.update(verdict='failed', execution={'status': 'failed'})
            client.record_row['metrics'][0].update(verdict='failed', reason='fixture failure')
        with self.assertRaises(LocalError):
            self.run_callback(failed)

    def test_empty_quick_selection_is_not_ci_acceptance(self):
        def empty(client):
            client.task_row['stats']['source']['total'] = 0
            client.task_row['stats']['diagnosers'] = {}
            client.listed_records = []
        with self.assertRaises(LocalError):
            self.run_callback(empty)

    def test_nonempty_known_but_bad_result_is_not_ci_acceptance(self):
        def bad(client):
            client.task_row['stats']['diagnosers']['rule-1']['tool_success_rate'].update(
                good=0, bad=1)
            client.record_row['verdict'] = 'bad'
            client.record_row['metrics'][0].update(verdict='bad', reason='tool failed')
        with self.assertRaises(LocalError):
            self.run_callback(bad)

    def test_positive_label_without_expected_source_counts_is_rejected(self):
        def missing_tool(client):
            client.record_row['metrics'][0]['evidence']['counts']['success'] = 0
        with self.assertRaises(LocalError):
            self.run_callback(missing_tool)

    def test_incomplete_source_is_not_ci_acceptance(self):
        def incomplete(client):
            client.task_row['stats']['source_complete'] = False
            original = client.scoped
            def scoped(path, *args, **kwargs):
                result = original(path, *args, **kwargs)
                if path == '/stats':
                    result['tasks'][0]['source_complete'] = False
                return result
            client.scoped = scoped
        with self.assertRaises(LocalError):
            self.run_callback(incomplete)

    def test_good_counters_cannot_hide_failed_record_execution(self):
        def failed_record(client):
            client.record_row['execution']['status'] = 'failed'
        with self.assertRaises(LocalError):
            self.run_callback(failed_record)

    def test_good_counters_cannot_replace_the_expected_metric(self):
        def empty_metrics(client):
            client.record_row['metrics'] = []
        with self.assertRaises(LocalError):
            self.run_callback(empty_metrics)

    def test_record_changes_after_the_snapshot_are_rejected(self):
        def changed_record(client):
            original = client.scoped
            reads = 0
            def scoped(path, *args, **kwargs):
                nonlocal reads
                result = original(path, *args, **kwargs)
                if path.startswith('/records/'):
                    reads += 1
                    if reads > 1:
                        result['metrics'][0]['evidence']['counts']['success'] = 2
                return result
            client.scoped = scoped
        with self.assertRaises(LocalError):
            self.run_callback(changed_record)


if __name__ == '__main__':
    unittest.main()
