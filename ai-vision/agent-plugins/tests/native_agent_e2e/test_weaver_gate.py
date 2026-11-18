import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from weaver_gate import DEFAULT_CASES, SIGNALS, VERSIONS, check_report, collect_matrix


class WeaverGateTests(unittest.TestCase):
    def test_known_limitation_matrix_rechecks_original_facts_and_rejects_forged_green(self):
        from copy import deepcopy
        from source_acceptance import assess_source
        from test_dsh_acceptance import host_loss
        from verify import verify_case as real_verify
        names=sorted(app+'-'+scenario for app,scenario in DEFAULT_CASES)
        known_name='dsh01-unknown-usage'
        report=host_loss(); report.update(assess_source(report)); report['verified']=False
        row={key:report[key] for key in ('case','version','accepted','status','canonical_verified','provider_reconciled','known_limitations','verified')}; row['source_verified']=False
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)
            summary={'status':'passed_with_known_limitations','mode':'fake','scope':'full','expected_cases':49,'executed_cases':49,
                'verified_cases':48,'known_limitation_cases':1,'failed_cases':0,
                'cases':[row if name==known_name else {'case':name,'source_verified':True,'verified':True} for name in names]}
            (path/'summary.json').write_text(json.dumps(summary))
            for name in names:
                app,scenario=name.split('-',1)
                exports=[{'admitted':True,'signal':signal,'otlp':{root:[{scope:[{rows:[{}]}]}]}} for signal,(root,scope,rows) in SIGNALS.items()]
                case={'case':name,'app':app,'scenario':scenario,'mode':'fake','version':VERSIONS[app],'receipt':'unit','result':{},'requests':[],'exports':exports,'expected_resource':{}}
                if name==known_name:
                    case=deepcopy(report)
                (path/name).mkdir(); (path/name/'evidence.json').write_text(json.dumps(case))
            def verify(**kwargs): return real_verify(**kwargs) if kwargs['app']=='dsh01' and kwargs['scenario']=='unknown-usage' else {'verified':True}
            with patch('weaver_gate.exports_match_raw',side_effect=lambda exports: True if not exports[0].get('spans') else __import__('evidence_integrity').exports_match_raw(exports)), patch('weaver_gate.verify_case',side_effect=verify): collect_matrix(path)
            casepath=path/known_name/'evidence.json'; case=json.loads(casepath.read_text()); case['exports'][0]['spans'][1]['attributes']['gen_ai.usage.total_tokens']=99
            casepath.write_text(json.dumps(case))
            with patch('weaver_gate.exports_match_raw',side_effect=lambda exports: True if not exports[0].get('spans') else __import__('evidence_integrity').exports_match_raw(exports)), patch('weaver_gate.verify_case',side_effect=verify), self.assertRaisesRegex(ValueError,'Original OTLP'): collect_matrix(path)

    def test_source_reverification_forwards_fake_mode_and_cannot_bypass_it(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            names = sorted(app + '-' + scenario for app,scenario in DEFAULT_CASES)
            (path / 'summary.json').write_text(json.dumps({'status': 'passed', 'mode': 'fake', 'scope': 'full',
                'expected_cases': len(names), 'executed_cases': len(names),
                'cases': [{'case': name, 'source_verified': True} for name in names]}))
            for name in names:
                app, scenario = name.split('-', 1)
                exports = [{'admitted': True, 'signal': signal,
                    'otlp': {root: [{scope: [{rows: [{}]}]}]}}
                    for signal, (root, scope, rows) in SIGNALS.items()]
                (path / name).mkdir()
                (path / name / 'evidence.json').write_text(json.dumps({'case': name, 'app': app,
                    'scenario': scenario, 'mode': 'fake', 'version': VERSIONS[app], 'receipt': 'unit',
                    'result': {}, 'requests': [], 'exports': exports, 'expected_resource': {}}))
            with patch('weaver_gate.exports_match_raw',return_value=True), patch('weaver_gate.verify_case', return_value={'verified': True}) as source:
                collect_matrix(path)
                self.assertEqual(source.call_count, 49)
                self.assertTrue(all(call.kwargs['mode'] == 'fake' for call in source.call_args_list))
            case_path = path / names[0] / 'evidence.json'
            case = json.loads(case_path.read_text())
            case['mode'] = 'nvidia'
            case_path.write_text(json.dumps(case))
            with patch('weaver_gate.exports_match_raw',return_value=True), patch('weaver_gate.verify_case', return_value={'verified': True}) as source:
                with self.assertRaisesRegex(ValueError, 'identity'):
                    collect_matrix(path)
                source.assert_not_called()

    def test_empty_or_partial_matrices_are_rejected_before_validator_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            for cases, count in (([], 0), ([{"case": "pi-tool-loop", "source_verified": True}], 1)):
                (path / "summary.json").write_text(json.dumps({"status": "passed", "mode": "fake", "scope": "full",
                    "expected_cases": count, "executed_cases": count, "cases": cases}))
                with self.assertRaises(ValueError):
                    collect_matrix(path)

    def test_former_fake25_cannot_claim_full_inventory_after_dsh_is_added(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            names = [app + '-' + scenario for app, scenario in DEFAULT_CASES
                     if app in ('pi', 'hermes', 'opencode')]
            self.assertEqual(len(names), 25)
            (path / 'summary.json').write_text(json.dumps({'status': 'passed', 'mode': 'fake', 'scope': 'full',
                'expected_cases': 25, 'executed_cases': 25,
                'cases': [{'case': name, 'source_verified': True} for name in names]}))
            with self.assertRaisesRegex(ValueError, 'complete'):
                collect_matrix(path)

    def test_former_fake41_cannot_omit_kimi_and_claim_the_complete_matrix(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)
            names=[app+'-'+scenario for app,scenario in DEFAULT_CASES if app!='kimi']
            self.assertEqual(len(names),41)
            (path/'summary.json').write_text(json.dumps({'status':'passed','mode':'fake','scope':'full',
                'expected_cases':41,'executed_cases':41,
                'cases':[{'case':name,'source_verified':True} for name in names]}))
            with self.assertRaisesRegex(ValueError,'complete'):
                collect_matrix(path)

    def test_validator_false_successes_and_incomplete_sample_counts_fail(self):
        valid = {"total_signals": 3, "total_samples": 4, "violations": 0, "semconv_issues": 0, "pass": True}
        check_report({"summary": valid}, "", 0, signals=3, samples=4)
        for changes, stderr, code in (
            ({"total_signals": 0, "total_samples": 0}, "No signals found", 0),
            ({}, "ERROR Validation error: engine failed", 0),
            ({}, "Failed to parse JSON line, skipping", 0),
            ({"total_samples": 3}, "", 0),
            ({"total_signals": 2}, "", 0),
            ({"violations": 1}, "", 0),
            ({"violations": False}, "", 0),
            ({"semconv_issues": 1}, "", 0),
            ({"pass": False}, "", 0),
            ({}, "", 1),
        ):
            with self.subTest(changes=changes, stderr=stderr, code=code), self.assertRaises(ValueError):
                check_report({"summary": {**valid, **changes}}, stderr, code, signals=3, samples=4)


if __name__ == "__main__":
    unittest.main()
