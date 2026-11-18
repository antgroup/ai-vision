from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import unittest
from unittest.mock import patch

from calibrate import (CalibrationError, CredentialLease, AttemptBudget, frozen_config,
    run_calibration, main, HttpTrialExecutor, trial_result)
from corpus import stable_hash
from test_corpus import public_source, provenance, labels


def package():
    return {'schema_version': 1, 'labels': labels(), 'labels_sha256': stable_hash(labels()),
        'cases': [{'case_id': 'public1', 'provenance': provenance(), 'evidence': public_source(),
            'review': {'invalidated_metrics': []}}]}


def config():
    return {'model': 'protocol-fixture', 'config_revision': 'config1', 'code_revision': 'code1',
        'parameters': {'temperature': 0}, 'templates': {'completion': {'identity': 'template1',
            'revision': 'rev1', 'prompt': 'Public protocol prompt', 'core': True}}}


class CalibrationTests(unittest.TestCase):
    def test_duplicate_metric_variant_config_is_rejected_before_binding(self):
        from calibrate import read_frozen_config
        path = Path(self.tmp.name)/'duplicate.json'
        path.write_text('{"templates":{"task_completion":{"identity":"task_completion/text_response"},'
                        '"task_completion":{"identity":"task_completion/executed_artifact"}}}')
        with self.assertRaises(CalibrationError):
            read_frozen_config(path)

    def test_complete_cli_adapter_uses_one_lease_and_does_not_probe_model(self):
        from corpus import export_case
        from test_core_trial import template
        from test_corpus import review
        root = Path(self.tmp.name)
        source = root/'source.json'; source.write_text(json.dumps(public_source()))
        truth = labels(); truth['labels'][0]['metric_key'] = 'task_completion'
        export_case(source, labels=truth, provenance=provenance(), review=review(), out=root/'package')
        target = root/'targets.json'
        target.write_text(json.dumps({'codex':{'endpoint':'http://127.0.0.1:1000',
            'space_id':'public','agent_uuid':'public-agent','dashboard_headers':{}}}))
        target.chmod(0o600)
        frozen = config()
        frozen['templates'] = {'task_completion':template()}
        frozen['adapter'] = {'kind':'core_trial_v1','app':'codex','provider_host':'host.orb.internal',
                             'stability_cases':[]}
        configuration = root/'config.json'; configuration.write_text(json.dumps(frozen))
        invocation = []
        def execute(package, **kwargs):
            invocation.append(kwargs)
            self.assertIs(kwargs['budget'], self.lease)
            self.assertTrue(self.lease.active)
            return {'stage':'executed','passed':False,'mode':'real','boundary':'public adapter control'}
        with patch.dict('os.environ', {'NVIDIA_API_KEY':'public fixture identity'}, clear=True), \
                patch('calibrate.nvidia_calibration', side_effect=execute):
            code = main(['--corpus',str(root/'package/manifest.json'),'--targets',str(target),
                '--config',str(configuration),'--out',str(root/'run')], budget=self.lease)
        self.assertEqual(code, 1)
        self.assertEqual(len(invocation), 1)
        self.assertEqual(json.loads((root/'run/report.json').read_text())['stage'], 'executed')

    def test_injected_source_lease_requires_same_key_and_remains_owned(self):
        from calibrate import provider_lease
        key = 'public fixture identity'
        with provider_lease(key, self.lease) as active:
            self.assertIs(active, self.lease)
            active.reserve()
        self.assertTrue(self.lease.active)
        with self.assertRaises(CalibrationError), provider_lease('different public key', self.lease):
            pass
        self.lease.__exit__(None, None, None)
        with self.assertRaises(CalibrationError), provider_lease(key, self.lease):
            pass

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.key_id = hashlib.sha256(b'public fixture identity').hexdigest()
        self.lease = CredentialLease(self.key_id, Path(self.tmp.name) / 'locks')
        self.lease.__enter__()

    def tearDown(self):
        self.lease.__exit__(None, None, None)
        self.tmp.cleanup()

    def run_case(self, execute, **kwargs):
        return run_calibration(package(), execute=execute, budget=self.lease,
            frozen=config(), mode=kwargs.pop('mode', 'real'), **kwargs)

    def executor(self, result):
        def execute(evidence, metric_key, *, context):
            self.assertNotIn('expected', context)
            self.assertNotIn('label', context)
            context['budget'].reserve()
            return {'metric_key': metric_key, **deepcopy(result)}
        return execute

    def test_independent_truth_mismatch_is_counterexample_and_frozen(self):
        original = package()
        report = run_calibration(original, execute=self.executor({'predicted': 'good', 'usage': {'tokens': 0}}),
            budget=self.lease, frozen=config(), mode='real')
        self.assertEqual(report['cases'][0]['expected'], 'bad')
        self.assertTrue(report['cases'][0]['false_negative'])
        self.assertFalse(report['passed'])
        self.assertEqual(report['labels_sha256'], stable_hash(labels()))
        self.assertEqual(original, package())

    def test_disputed_visible_excluded_and_agent_label_coverage(self):
        value = package()
        value['labels']['labels'][0].update(disputed=True, labeler={'id': 'agent1', 'kind': 'agent'})
        value['labels_sha256'] = stable_hash(value['labels'])
        report = run_calibration(value, execute=self.executor({'predicted': 'bad'}), budget=self.lease,
            frozen=config(), mode='real')
        self.assertEqual(report['summary']['accuracy_denominator'], 0)
        self.assertEqual(report['summary']['disputed'], 1)
        self.assertEqual(report['summary']['labelers']['agent'], 1)
        self.assertFalse(report['passed'])

    def test_all_fail_cannot_pass_and_retry_bound_shared_attempts(self):
        def fail(_evidence, _key, *, context):
            context['budget'].reserve()
            raise RuntimeError('private payload must not be logged')
        report = self.run_case(fail)
        self.assertEqual(report['cases'][0]['predicted'], 'failed')
        self.assertEqual(report['cases'][0]['attempts'], 3)
        self.assertEqual(self.lease.attempts, 3)
        self.assertNotIn('private payload', json.dumps(report))
        self.assertFalse(report['passed'])

    def test_budget_before_first_attempt_skip_after_request_failed(self):
        def skipped(_evidence, _key, *, context):
            context['budget'].stop.set()
            if context['budget'].reserve() is None:
                raise CalibrationError('budget unavailable')
        self.assertEqual(self.run_case(skipped)['cases'][0]['predicted'], 'skip')
        def failed(_evidence, _key, *, context):
            context['budget'].reserve()
            context['budget'].stop.set()
            raise CalibrationError('budget unavailable')
        self.assertEqual(self.run_case(failed)['cases'][0]['predicted'], 'failed')

    def test_unknown_vs_zero_usage_and_fake_never_semantic_accuracy(self):
        unknown = self.run_case(self.executor({'predicted': 'bad'}), mode='fake')
        zero = self.run_case(self.executor({'predicted': 'bad', 'usage': {'tokens': 0}}), mode='fake')
        self.assertIsNone(unknown['cases'][0]['usage'][0])
        self.assertEqual(zero['cases'][0]['usage'][0], {'tokens': 0})
        self.assertIsNone(zero['summary']['accuracy'])
        self.assertTrue(zero['protocol_valid'])
        self.assertFalse(zero['passed'])

    def test_nested_reported_usage_and_empty_usage_remain_distinct(self):
        usage = {'prompt_tokens': 0, 'completion_tokens': 0, 'prompt_tokens_details': {'cached_tokens': 0}}
        known = self.run_case(self.executor({'predicted': 'bad', 'usage': usage, 'reported_cost': 0}), mode='fake')
        self.assertEqual(known['cases'][0]['usage'], [usage])
        self.assertEqual(known['cases'][0]['reported_cost'], [0])
        unknown = self.run_case(self.executor({'predicted': 'bad', 'usage': {}}), mode='fake')
        self.assertEqual(unknown['cases'][0]['usage'], [None])

    def test_known_key_missing_output_and_mutated_truth_fail_closed(self):
        with self.assertRaises(CalibrationError):
            run_calibration(package(), execute=lambda *a, **k: {}, budget=self.lease,
                frozen={**config(), 'templates': {}}, mode='real')
        report = self.run_case(self.executor({'usage': {}}))
        self.assertEqual(report['cases'][0]['predicted'], 'failed')
        value = package()
        value['labels']['labels'][0]['expected'] = 'good'
        with self.assertRaises(CalibrationError):
            run_calibration(value, execute=lambda *a, **k: {}, budget=self.lease, frozen=config(), mode='real')

    def test_attempt_scope_counts_probe_failures_and_max_three(self):
        scoped = AttemptBudget(self.lease)
        with scoped.case():
            self.assertIsNotNone(scoped.reserve())
            self.assertIsNotNone(scoped.reserve())
            self.assertIsNotNone(scoped.reserve())
            self.assertIsNone(scoped.reserve())
            self.assertEqual(scoped.started, 3)
        self.assertEqual(self.lease.attempts, 3)

    def test_replaced_lease_and_inactive_ownership_fail_closed(self):
        # Simulate an external owner replacing the file; this lease must neither
        # reserve another provider attempt nor remove the new owner's lock.
        self.lease.path.unlink()
        self.lease.path.write_text('new owner')
        with self.assertRaises(CalibrationError): self.lease.reserve()

    def test_lock_contention_hash_only_and_unowned_budget_rejected(self):
        with self.assertRaises(CalibrationError):
            with CredentialLease(self.key_id, Path(self.tmp.name) / 'locks'): pass
        names = [p.name for p in (Path(self.tmp.name) / 'locks').iterdir()]
        self.assertEqual(names, [self.key_id + '.lock'])
        with self.assertRaises(CalibrationError):
            run_calibration(package(), execute=lambda *a, **k: {}, budget=object(), frozen=config(), mode='real')

    def test_five_by_three_stability_and_critical_flip_gate(self):
        value = package()
        value['cases'] = []
        value['labels']['labels'] = []
        for i in range(5):
            case_id = 'public' + str(i)
            case = deepcopy(package()['cases'][0])
            case.update(case_id=case_id)
            case['provenance']['case_id'] = case_id
            value['cases'].append(case)
            label = deepcopy(labels()['labels'][0])
            label['case_id'] = case_id
            value['labels']['labels'].append(label)
        value['labels_sha256'] = stable_hash(value['labels'])
        report = run_calibration(value, execute=self.executor({'predicted': 'bad'}), budget=self.lease,
            frozen=config(), mode='real', repeats=3, stability_cases=[case['case_id'] for case in value['cases']])
        self.assertTrue(report['passed'])
        self.assertEqual(report['core']['completion']['denominator'], 5)
        self.assertEqual(len(report['cases']), 15)
        # A fresh shared budget avoids spending this test's next 15 reservations
        # in the same real rolling window. No second live process is created.
        self.lease.rate_budget = type(self.lease.rate_budget)(clock=lambda: 1000, sleep=lambda _: None)
        calls = [0]
        def flip(evidence, key, *, context):
            context['budget'].reserve()
            calls[0] += 1
            return {'metric_key': key, 'predicted': 'good' if calls[0] == 2 else 'bad'}
        flipped = run_calibration(value, execute=flip, budget=self.lease, frozen=config(), mode='real',
            repeats=3, stability_cases=[case['case_id'] for case in value['cases']])
        self.assertFalse(flipped['passed'])
        self.assertTrue(flipped['stability']['critical_bad_to_good'])
        self.assertEqual(sum(case['consistent'] for case in flipped['stability']['cases']), 4)

    def test_masking_is_skip_with_visible_coverage_not_accuracy(self):
        value = package()
        value['cases'][0]['review']['invalidated_metrics'] = ['completion']
        value['cases'][0]['review']['invalidation_reasons'] = {'completion': 'Public fixture masks completion evidence'}
        report = run_calibration(value, execute=lambda *a, **k: self.fail('masked material reached Judge'),
            budget=self.lease, frozen=config(), mode='real')
        self.assertEqual(report['cases'][0]['predicted'], 'skip')
        self.assertTrue(report['cases'][0]['invalidated_by_masking'])
        self.assertEqual(report['summary']['accuracy_denominator'], 0)
        self.assertFalse(report['passed'])

    def test_missing_or_wrong_metric_key_fails_even_with_good_prediction(self):
        report = self.run_case(self.executor({'metric_key': 'unrelated', 'predicted': 'good'}))
        self.assertEqual(report['cases'][0]['predicted'], 'failed')

    def test_trial_result_validates_skip_failure_and_requested_metric(self):
        skipped = {'skipped': True, 'skip_reason': 'missing_material', 'verdict': None,
            'metrics': [], 'execution': {'status': 'succeeded'}}
        self.assertEqual(trial_result(skipped, 'completion')['predicted'], 'skip')
        good = {'skipped': False, 'verdict': 'good', 'metrics': [{'name': 'completion', 'verdict': 'good'}],
            'execution': {'status': 'succeeded'}, 'usage': None}
        self.assertEqual(trial_result(good, 'completion')['predicted'], 'good')
        with self.assertRaises(CalibrationError): trial_result({**good, 'metrics': []}, 'completion')
        failed = {**good, 'verdict': 'failed', 'metrics': [{'name': 'completion', 'verdict': 'failed'}],
            'execution': {'status': 'failed'}}
        self.assertEqual(trial_result(failed, 'completion')['predicted'], 'failed')
        with self.assertRaises(CalibrationError): trial_result({**skipped, 'metrics': good['metrics']}, 'completion')

    def test_injected_trial_dto_uses_real_loopback_http_envelope(self):
        from platform_client import PlatformClient
        observed = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args): pass
            def do_POST(self):
                observed.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                data = json.dumps({'code': 0, 'data': {'skipped': True, 'skip_reason': 'missing_material',
                    'verdict': None, 'metrics': [], 'execution': {'status': 'succeeded'}}}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with PlatformClient('http://127.0.0.1:' + str(server.server_port)) as client:
                execute = HttpTrialExecutor(client, path='/api/v1/public/trial',
                    request_builder=lambda evidence, key, context: {'spec': {'public': key}, 'test_input': evidence},
                    result_parser=trial_result)
                result = execute(public_source(), 'completion', context={})
                self.assertEqual(result['predicted'], 'skip')
                self.assertEqual(observed[0]['test_input'], public_source())
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_executor_cannot_rewrite_frozen_configuration(self):
        def execute(_evidence, key, *, context):
            context['budget'].reserve()
            context['frozen']['model'] = 'altered'
            return {'metric_key': key, 'predicted': 'bad'}
        report = self.run_case(execute)
        self.assertEqual(report['cases'][0]['predicted'], 'failed')
        self.assertEqual(report['frozen']['model'], config()['model'])

    def test_returned_skip_after_request_or_swallowed_cap_refusal_is_failed(self):
        for count in (1, 4):
            def execute(_evidence, key, *, context):
                for _ in range(count): context['budget'].reserve()
                return {'metric_key': key, 'predicted': 'skip'}
            report = self.run_case(execute)
            self.assertEqual(report['cases'][0]['predicted'], 'failed')
            self.assertEqual(report['summary']['skipped'], 0)
        no_request = self.run_case(lambda evidence, key, **kwargs: {'metric_key': key, 'predicted': 'skip'})
        self.assertEqual(no_request['cases'][0]['predicted'], 'skip')

    def test_internal_retries_retain_unknown_attempt_usage_and_cost(self):
        def execute(_evidence, key, *, context):
            for _ in range(3): context['budget'].reserve()
            return {'metric_key': key, 'predicted': 'bad', 'usage': {'tokens': 0}, 'reported_cost': 0}
        report = self.run_case(execute)
        self.assertEqual(report['cases'][0]['usage'], [None, None, {'tokens': 0}])
        self.assertEqual(report['cases'][0]['reported_cost'], [None, None, 0])
        self.assertEqual(report['summary']['usage_unknown'], 2)
        self.assertEqual(report['summary']['cost_unknown'], 2)

    def test_explicit_attempt_ledger_and_aggregate_need_actual_coverage_ids(self):
        def execute(_evidence, key, *, context):
            ids = [context['budget'].reserve() for _ in range(3)]
            return {'metric_key': key, 'predicted': 'bad', 'attempt_ledger': [
                {'attempt_number': ids[0], 'usage': {'tokens': 2}, 'reported_cost': .2},
                {'attempt_number': ids[2], 'usage': {'tokens': 0}, 'reported_cost': 0}]}
        report = self.run_case(execute)
        self.assertEqual(report['cases'][0]['usage'], [{'tokens': 2}, None, {'tokens': 0}])
        self.assertEqual(report['summary']['usage_unknown'], 1)
        def unrelated(_evidence, key, *, context):
            context['budget'].reserve()
            return {'metric_key': key, 'predicted': 'bad', 'usage': {'tokens': 0},
                'usage_attempt_numbers': [999999]}
        self.assertEqual(self.run_case(unrelated)['cases'][0]['predicted'], 'failed')
        def aggregate(_evidence, key, *, context):
            ids = [context['budget'].reserve() for _ in range(2)]
            return {'metric_key': key, 'predicted': 'bad', 'usage': {'tokens': 4}, 'reported_cost': .4,
                'usage_attempt_numbers': ids}
        known = self.run_case(aggregate)['cases'][0]
        self.assertEqual(known['usage'], [None, None])
        self.assertEqual(known['usage_aggregates'][0]['usage'], {'tokens': 4})
        self.assertEqual(known['usage_aggregates'][0]['attempt_numbers'],
            [item['attempt_number'] for item in known['attempt_ledger']])

    def test_cli_pending_adapter_and_private_targets(self):
        from corpus import export_case
        root = Path(self.tmp.name)
        source = root / 'source.json'
        source.write_text(json.dumps(public_source()))
        from test_corpus import review
        export_case(source, labels=labels(), provenance=provenance(), review=review(), out=root / 'corpus')
        target = root / 'targets.json'
        target.write_text(json.dumps({'endpoint': 'http://127.0.0.1:8000'}))
        target.chmod(0o600)
        conf = root / 'config.json'
        conf.write_text(json.dumps(config()))
        self.assertEqual(main(['--corpus', str(root / 'corpus' / 'manifest.json'), '--targets', str(target),
            '--config', str(conf), '--out', str(root / 'report')], budget=self.lease), 2)
        report = json.loads((root / 'report' / 'report.json').read_text())
        self.assertEqual(report['stage'], 'adapter_pending')
        self.assertFalse(report['passed'])
        self.assertEqual(self.lease.attempts, 0)


if __name__ == '__main__':
    unittest.main()
