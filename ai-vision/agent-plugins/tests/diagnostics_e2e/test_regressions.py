"""Negative controls for real repair history and cross-run identity."""
from copy import deepcopy
from contextlib import ExitStack
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


class FailureEvidenceTests(unittest.TestCase):
    class Provider:
        def __init__(self):
            self.url, self.mode = 'http://127.0.0.1:12345/v1', 'normal'
            self.requests = [{'completed': True, 'status': 200}]
        def __enter__(self): return self
        def __exit__(self, *_): self.requests.append({'shutdown_complete': True})

    def dependencies(self, stack, module):
        for name, value in [('FakeJudge', self.Provider),
                ('create_connection', lambda *a, **k: {'uuid': 'connection'}),
                ('test_connection', lambda *a, **k: {'uuid': 'connection'}),
                ('_group', lambda *a, metrics=module.METRIC_NAMES, **k:
                    {'uuid': 'diag', 'spec': {'metrics': [{'name': name} for name in metrics]}})]:
            stack.enter_context(patch.object(module, name, value))
        client = Mock()
        client.builtin_rules.return_value = {'test_execution': {'uuid': 'rule'}}
        client.create_dataset.return_value = {'uuid': 'dataset'}
        client.create_job.return_value = {'uuid': 'job'}
        client.scoped.return_value = {'uuid': 'dataset', 'spec': {'query': {'where': []}}}
        source = ({'source': {'source_trace_ids': ['a' * 32], 'source_span_ids': ['b' * 16],
            'verified': True}, 'workspace': '/private/tmp/inert-existing'}, SimpleNamespace())
        return client, source

    def test_matrix_timeout_preserves_closed_provider_attempts_and_partial_summary(self):
        import regressions as module
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            client, source = self.dependencies(stack, module)
            out = Path(directory) / 'matrix'
            with patch.object(module, '_execute', side_effect=TimeoutError('inert task timeout')):
                with self.assertRaises(TimeoutError):
                    module.quality_matrix(client, {'incomplete-repair': source}, 'codex', out)
            attempts = json.loads((out / 'provider-attempts.json').read_text())
            self.assertTrue(all(rows[-1]['shutdown_complete'] for rows in attempts.values()))
            summary = json.loads((out / 'summary.json').read_text())
            self.assertFalse(summary['verified'])
            self.assertEqual(summary['expected'], 10)
            self.assertLess(summary['completed'], 10)
            failed = json.loads((out / 'score-0.json').read_text())
            self.assertEqual(failed['job']['uuid'], 'job')
            self.assertEqual(failed['error_type'], 'TimeoutError')

    def test_after_timeout_keeps_before_and_after_failure_with_closed_provider_ledger(self):
        import regressions as module
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            client, source = self.dependencies(stack, module)
            out = Path(directory) / 'repair'
            old = {'verified': True, 'failures': [], 'point': {'global': {'score': 0}},
                'task': {'uuid': 'old'}, 'records': []}
            repaired = {'source': {'verified': True, 'source_trace_ids': ['c' * 32]}}
            with patch.object(module, '_execute', side_effect=[old, TimeoutError('inert after timeout')]), \
                    patch.object(module, 'native_case', return_value=repaired), patch.object(module.time, 'sleep'):
                with self.assertRaises(TimeoutError):
                    module.repair_workflow(client, before=source, repair=SimpleNamespace(), app='codex',
                        repo=Path(directory), out=out, runtime=('codex',), artifacts=Path(directory))
            self.assertEqual(json.loads((out / 'before.json').read_text()), old)
            failed = json.loads((out / 'comparison.json').read_text())
            self.assertFalse(failed['verified'])
            self.assertEqual(failed['before']['task']['uuid'], 'old')
            self.assertEqual(failed['error_type'], 'TimeoutError')
            self.assertEqual(failed['phase'], 'after_diagnosis')
            self.assertTrue(failed['provider_requests'][-1]['shutdown_complete'])

    def test_reused_output_is_rejected_without_overwriting_prior_evidence(self):
        import regressions as module
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            client, source = self.dependencies(stack, module)
            out = Path(directory)
            for name in ('summary.json', 'provider-attempts.json', 'comparison.json'):
                (out / name).write_text('prior evidence')
            for call in (
                    lambda: module.quality_matrix(client, {}, 'codex', out),
                    lambda: module.repair_workflow(client, before=source, repair=SimpleNamespace(), app='codex',
                        repo=out, out=out, runtime=('codex',), artifacts=out)):
                with self.assertRaises(FileExistsError): call()
                for name in ('summary.json', 'provider-attempts.json', 'comparison.json'):
                    self.assertEqual((out / name).read_text(), 'prior evidence')

    def test_failed_connection_probe_keeps_closed_provider_ledger(self):
        import regressions as module
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            client, _ = self.dependencies(stack, module)
            out = Path(directory) / 'matrix'
            with patch.object(module, 'test_connection', side_effect=RuntimeError('inert unavailable probe')):
                with self.assertRaises(RuntimeError): module.quality_matrix(client, {}, 'codex', out)
            attempts = json.loads((out / 'provider-attempts.json').read_text())
            self.assertEqual(set(attempts), {'normal'})
            self.assertTrue(attempts['normal'][-1]['shutdown_complete'])
            summary = json.loads((out / 'summary.json').read_text())
            self.assertFalse(summary['verified'])
            self.assertEqual(summary['completed'], 0)

    def test_execution_error_carries_partial_projection_and_triggered_task(self):
        import regressions as module
        client = Mock()
        error = TimeoutError('inert timed out task')
        error.task = {'uuid': 'triggered-task', 'status': 'running'}
        client.execute.side_effect = error
        report = {'source': {'source_trace_ids': ['a' * 32], 'source_span_ids': ['b' * 16], 'verified': True}}
        projection = {'a' * 32: {'trace_id': 'a' * 32}}
        with patch.object(module, '_projection', return_value=projection):
            with self.assertRaises(TimeoutError) as caught:
                module._execute(client, {'uuid': 'job'}, {}, [(report, SimpleNamespace())], {})
        saved = caught.exception.diagnostic_evidence
        self.assertEqual(saved['job']['uuid'], 'job')
        self.assertEqual(saved['task']['uuid'], 'triggered-task')
        self.assertEqual(saved['projections'], projection)
        self.assertEqual(saved['phase'], 'task_execution')

    def test_latest_session_export_is_private_and_preserves_existing_file(self):
        import regressions as module
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); export = root / "current.json"
            sessions = Mock(); sessions.refresh.side_effect = lambda target: {**target, "dashboard_headers": {"Cookie": "latest"}}
            module.export_current_targets(export, {"codex": {"space_id": "team"}}, sessions)
            self.assertEqual(export.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(export.read_text())["codex"]["dashboard_headers"]["Cookie"], "latest")
            original = export.read_bytes()
            with self.assertRaises(FileExistsError): module.export_current_targets(export, {}, sessions)
            self.assertEqual(export.read_bytes(), original)

    def test_runner_exception_persists_false_partial_six_result_summary(self):
        import regressions as module
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            targets = root / 'targets.json'; targets.write_text(json.dumps({'codex': {'endpoint': 'http://127.0.0.1:12345'}}))
            for name in ('incomplete-repair', 'misreported-verification', 'code-change-untested',
                    'code-change-tested', 'repair-with-verification', 'rewrite-overlap'):
                path = root / 'cases' / ('codex-' + name) / 'source.json'
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps({'app': 'codex'}))
            sessions = Mock(); sessions.refresh.side_effect = lambda target: target
            with patch.object(module, 'validate_targets', lambda target, apps: target), \
                    patch.object(module.native_sessions, 'DashboardSessions', return_value=sessions), \
                    patch.object(module, 'PlatformClient'), \
                    patch.object(module, 'quality_matrix', side_effect=TimeoutError('inert matrix timeout')):
                with self.assertRaises(TimeoutError):
                    module.main(['--repo', str(root), '--out', str(root / 'out'), '--cases', str(root / 'cases'),
                        '--targets', str(targets), '--artifacts', str(root)])
            summary = json.loads((root / 'out' / 'summary.json').read_text())
            self.assertEqual(summary['expected'], 6)
            self.assertEqual(summary['completed'], 0)
            self.assertFalse(summary['verified'])


class RepairHistoryTests(unittest.TestCase):
    def world(self):
        return dict(before_task={"uuid": "old", "stats": {"source": {"total": 1}}},
            after_task={"uuid": "new", "stats": {"source": {"total": 1}}},
            before_point={"task_uuid": "old", "time": "2026-10-08T12:00:01+00:00", "global": {"score": 0}},
            points=[{"task_uuid": "old", "time": "2026-10-08T12:00:01+00:00", "global": {"score": 0}},
                    {"task_uuid": "new", "time": "2026-10-08T12:00:03+00:00", "global": {"score": 100}}],
            before_records=[{"uuid": "old-record", "target_id": "a" * 32, "metrics": [{"verdict": "bad"}]}],
            old_records=[{"uuid": "old-record", "target_id": "a" * 32, "metrics": [{"verdict": "bad"}]}],
            old_task={"uuid": "old", "stats": {"source": {"total": 1}}},
            before_traces={"a" * 32}, after_traces={"b" * 32}, expected_after=100)

    def test_improved_score_requires_preserved_before_history_and_new_trace(self):
        from regressions import validate_repair_history
        self.assertTrue(validate_repair_history(**self.world())["verified"])
        for field, bad in (
            ("old_records", []), ("old_task", {"uuid": "old", "stats": {}}),
            ("after_traces", {"a" * 32}), ("points", [{"task_uuid": "new", "global": {"score": 100}}]),
            ("expected_after", 66.7),
        ):
            value = deepcopy(self.world()); value[field] = bad
            with self.subTest(field=field):
                self.assertFalse(validate_repair_history(**value)["verified"])

    def test_same_period_or_overwritten_historical_score_cannot_pass(self):
        from regressions import validate_repair_history
        value = self.world(); value["points"][1]["time"] = value["points"][0]["time"]
        self.assertFalse(validate_repair_history(**value)["verified"])
        value = self.world(); value["points"][0]["global"]["score"] = 100
        self.assertFalse(validate_repair_history(**value)["verified"])


if __name__ == "__main__":
    unittest.main()
