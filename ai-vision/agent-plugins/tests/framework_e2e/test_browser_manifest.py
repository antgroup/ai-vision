"""Browser evidence cannot turn incomplete collection into passing page coverage."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import browser_manifest


HERE = Path(__file__).resolve().parent
TARGET = {'spaceId': 'space', 'agentUuid': 'agent', 'agentProduct': 'langfuse'}
START = 1792399000000000000
JS_CORE_CHECKS = ('native-http', 'complete', 'inventory', 'parents', 'error-status', 'raw-io',
                  'timestamps', 'independent-usage', 'v1-http', 'v1-inventory', 'session',
                  'sdk-generation-usage', 'native-generation-usage', 'usage-provenance',
                  'native-usage-details', 'v1-generation-usage')


def span(sid='root', parent='', kind='agent', usage=None):
    attrs = {'langfuse.observation.type': kind, 'session.id': 'session',
             'langfuse.observation.input': '{"question":"hello"}',
             'langfuse.observation.output': '"42"',
             'langfuse.observation.metadata.numeric_string': '"42"',
             'langfuse.observation.metadata.number': '42'}
    if usage is not None:
        attrs['langfuse.observation.usage_details'] = json.dumps(usage)
    return {'trace_id': 'trace', 'span_id': sid, 'parent_span_id': parent,
            'name': sid, 'attributes': attrs, 'status': {},
            'start_time_unix_nano': START, 'end_time_unix_nano': START + 1000000,
            'scope_name': 'langfuse-sdk', 'scope_version': '5.0.2',
            'resource': {'telemetry.sdk.language': 'python'}}


class BrowserManifestTests(unittest.TestCase):
    def test_original_io_keeps_the_sdk_envelope_separate_from_decoded_values(self):
        for original, decoded in [(' {"x": 1} ', {'x': 1}), ('"{\\"x\\":1}"', '{"x":1}'),
                                  ('false', False), ('null', None), ('""', '')]:
            with self.subTest(original=original):
                source = span()
                source['attributes']['langfuse.observation.input'] = original
                projected = browser_manifest.python_span(source)
                self.assertEqual(projected.get('inputWire'), original)
                self.assertEqual(projected['input'], decoded)
                self.assertTrue(projected['inputPresent'])

    def test_source_io_presence_is_independent_of_decoded_null_and_keeps_precedence(self):
        cases = [({}, (None, False)),
                 ({'langfuse.observation.input': 'null'}, (None, True)),
                 ({'langfuse.observation.input': None}, (None, False)),
                 ({'gen_ai.input.messages': None, 'langfuse.observation.input': '"fallback"'}, (None, True)),
                 ({'langfuse.observation.input': None, 'input.value': '"fallback"'}, ('fallback', True)),
                 ({'input.value': False}, (False, True)),
                 ({'input.value': 0}, (0, True)),
                 ({'input.value': ''}, ('', True)),
                 ({'gen_ai.input.messages': None, 'aivision.normalization.sources': '{"gen_ai.input.messages":"sdk"}'}, (None, False)),
                 ({'gen_ai.input.messages': '["derived"]', 'langfuse.observation.input': '"original"',
                   'aivision.normalization.sources': {'gen_ai.input.messages': 'sdk'}}, ('original', True))]
        for attrs, expected in cases:
            with self.subTest(attrs=attrs):
                self.assertEqual(browser_manifest.source_io(attrs, 'input'), expected)

    def test_js_presence_distinguishes_omitted_io_from_serialized_null(self):
        js = self.js_case()
        self.mutate(js/'ledger.json', lambda rows: rows[0]['inventory'][0].pop('input'))
        self.mutate(js/'ledger.json', lambda rows: rows[0]['inventory'][0].update(output='null'))
        manifest = self.run_builder('--js-root', js)
        trace = manifest['traces'][0]
        self.assertIsNone(trace['input']); self.assertIsNone(trace['output'])
        self.assertFalse(trace['inputPresent']); self.assertTrue(trace['outputPresent'])
        self.assertFalse(trace['spans'][0]['inputPresent']); self.assertTrue(trace['spans'][0]['outputPresent'])
        self.assertFalse(manifest['sessions'][0]['turns'][0]['inputPresent'])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.target = self.root/'target.json'
        self.write(self.target, TARGET)

    def write(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))

    def python_case(self):
        directory = self.root/'python'
        exports = [{'spans': [span(), span('generation', 'root', 'generation', {'input': 0, 'output': 0, 'total': 0}),
                               span('embedding', 'root', 'embedding')]}]
        report = {'status': 'passed', 'framework': 'langchain', 'sdk': 'langfuse', 'scenario': 'zero-usage',
                  'versions': {'langfuse': '5.0.2'}, 'bounds': {'from_ms': START//1000000, 'to_ms': START//1000000+2},
                  'identity': {'space_id': 'space', 'agent_uuid': 'agent', 'agent_product': 'langfuse'},
                  'sessions': [{'session_id': 'session', 'roots': [{'trace_id': 'trace', 'input': {'question': 'hello'},
                      'output': '42', 'application_status': 'completed'}]}]}
        verification = {'passed': True, 'source_verified': True, 'platform_query_verified': True,
                        'checks': {'source': True, 'platform': True}, 'failures': []}
        for name in ('report_schema', 'framework_run_completed', 'sdk_ingestion_accepted', 'declared_roots_unique',
                     'exact_trace_inventory', 'span_ids_unique', 'parent_graph_same_trace_acyclic',
                     'platform.exact_window', 'platform.source_traces_present', 'platform.session_usage_http',
                     'platform.session_io_http', 'platform.session_inventory'):
            verification['checks'][name] = True
        for suffix in ('one_root', 'business_user', 'status', 'application_status', 'actual_io'):
            verification['checks']['root.trace.' + suffix] = True
        for prefix, suffixes in [('platform.native.0.', ('http', 'exact_spans', 'source_fields', 'business_user')),
                                 ('platform.v1.0.', ('trace', 'pagination', 'exact_observations')),
                                 ('platform.session.0.', ('usage', 'io')),
                                 ('platform.v1.session.0.', ('identity', 'trace_inventory', 'business_user', 'trace_fields', 'created'))]:
            for suffix in suffixes: verification['checks'][prefix + suffix] = True
        for name, data in [('report.json', report), ('sdk-exports.json', exports), ('verification.json', verification)]:
            self.write(directory/name, data)
        return directory

    def js_case(self, live=False):
        directory = self.root/('live-js' if live else 'js')
        inventory = [{'id': 'js-root', 'parentId': None, 'name': 'success', 'type': 'span',
                      'input': '{"question":"hello"}', 'output': '"42"', 'status': 0,
                      'startTimeUnixNano': str(START), 'endTimeUnixNano': str(START+1000000)},
                     {'id': 'js-generation', 'parentId': 'js-root', 'name': 'model', 'type': 'generation',
                      'input': 'hello', 'output': '"42"', 'status': 0,
                      'startTimeUnixNano': str(START), 'endTimeUnixNano': str(START+1000000)}]
        record = {'scenario': 'live-stream' if live else 'success', 'sdkVersion': '6.0.0', 'traceId': 'js-trace',
                  'sessionId': 'js-session', 'inventory': inventory, 'expectedUsage': {'input': 2, 'output': 1, 'total': 3},
                  'provider': 'integrate.api.nvidia.com' if live else 'local-simulated-openai'}
        if live:
            record['providerReference'] = {'httpStatus': 200, 'usage': {'input': 2, 'output': 1, 'total': 3}, 'usageEvents': [{'prompt_tokens': 2, 'completion_tokens': 1, 'total_tokens': 3}]}
        proof = {'passed': True, 'traces': 1, 'checks': len(JS_CORE_CHECKS), 'failures': [], 'identity': TARGET,
                 'results': [{'name': f'{record["scenario"]}:js-trace:{check}', 'passed': True} for check in JS_CORE_CHECKS]}
        self.write(directory/('live-ledger.json' if live else 'ledger.json'), [record])
        self.write(directory/('live-readback.json' if live else 'platform-readback.json'), proof)
        if not live:
            self.write(directory/'ledger.json.lifecycle.json', [])
        return directory

    def run_builder(self, *args, source='fake', passed=True):
        out = self.root/'manifest.json'
        out.unlink(missing_ok=True)
        result = subprocess.run([sys.executable, str(HERE/'browser_manifest.py'), '--source', source,
                                 '--target-public', str(self.target), '--out', str(out), *map(str, args)],
                                capture_output=True, text=True)
        if passed:
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
            return json.loads(out.read_text())
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(out.exists(), 'Failed evidence must never leave an acceptable manifest')

    def mutate(self, path, edit):
        value = json.loads(path.read_text()); edit(value); self.write(path, value)

    def test_verified_python_preserves_zero_unknown_and_single_decoding(self):
        directory = self.python_case()
        manifest = self.run_builder('--python-root', directory)
        trace = manifest['traces'][0]
        self.assertEqual(trace['output'], '42')
        self.assertEqual(trace['usage'], {'input': None, 'output': None, 'total': None})
        self.assertEqual(trace['spans'][1]['usage'], {'input': 0, 'output': 0, 'total': 0})
        self.assertEqual(trace['spans'][0]['metadata'], {'numeric_string': '42', 'number': 42})
        self.assertEqual(manifest['sessions'][0]['turns'], [{'traceId': 'trace', 'input': {'question': 'hello'}, 'output': '42',
                                                          'inputPresent': True, 'outputPresent': True}])

    def test_mixed_sources_preserve_js_usage_and_null_session(self):
        py = self.python_case(); js = self.js_case()
        self.mutate(js/'ledger.json', lambda rows: rows[0].pop('sessionId'))
        manifest = self.run_builder('--python-root', py, '--js-root', js)
        self.assertEqual(len(manifest['traces']), 2)
        trace = next(t for t in manifest['traces'] if t['sdkLanguage'] == 'javascript')
        self.assertIsNone(trace['sessionId'])
        self.assertEqual(trace['spans'][1]['usage'], {'input': 2, 'output': 1, 'total': 3})
        self.assertEqual(len(manifest['sessions']), 1)

    def test_live_js_requires_provider_and_matching_usage_proof(self):
        js = self.js_case(live=True)
        self.assertEqual(self.run_builder('--js-root', js, source='nvidia')['source'], 'nvidia')
        self.mutate(js/'live-ledger.json', lambda rows: rows[0]['providerReference']['usage'].update(total=99))
        self.run_builder('--js-root', js, source='nvidia', passed=False)

    def test_js_rejects_wrong_or_missing_identity_and_each_missing_readback_check(self):
        js = self.js_case()
        original = json.loads((js/'platform-readback.json').read_text())
        for identity in (None, {**TARGET, 'spaceId': 'another-space'}, {**TARGET, 'agentUuid': 'another-agent'},
                         {**TARGET, 'agentProduct': 'another-product'}, {**TARGET, 'space_key': 'never-allow'}):
            value = copy.deepcopy(original); value['identity'] = identity
            self.write(js/'platform-readback.json', value)
            self.run_builder('--js-root', js, passed=False)
        for check in JS_CORE_CHECKS:
            value = copy.deepcopy(original)
            value['results'] = [row for row in value['results'] if not row['name'].endswith(':' + check)]
            value['checks'] = len(value['results'])
            self.write(js/'platform-readback.json', value)
            self.run_builder('--js-root', js, passed=False)

    def test_python_rejects_missing_native_v1_root_io_and_metadata_proof(self):
        py = self.python_case()
        original = json.loads((py/'verification.json').read_text())
        for check in ('platform.native.0.source_fields', 'platform.v1.0.exact_observations', 'root.trace.actual_io',
                      'platform.session.0.io'):
            value = copy.deepcopy(original); value['checks'].pop(check)
            self.write(py/'verification.json', value)
            self.run_builder('--python-root', py, passed=False)
        self.write(py/'verification.json', original)
        self.mutate(py/'report.json', lambda value: value.update(metadata_contract='python-sdk-json-types-v1'))
        self.run_builder('--python-root', py, passed=False)

    def test_rejects_empty_failed_or_unverified_python(self):
        py = self.python_case()
        for name, field, bad in [('report.json', 'status', 'failed'), ('verification.json', 'source_verified', False),
                                 ('verification.json', 'platform_query_verified', False), ('verification.json', 'checks', {}),
                                 ('verification.json', 'failures', ['mismatch'])]:
            with self.subTest(field=field):
                original = json.loads((py/name).read_text())
                self.mutate(py/name, lambda value: value.update({field: bad}))
                self.run_builder('--python-root', py, passed=False)
                self.write(py/name, original)

    def test_rejects_duplicate_trace_and_span_ids(self):
        py = self.python_case()
        self.run_builder('--python-root', py, '--python-root', py, passed=False)
        self.mutate(py/'sdk-exports.json', lambda rows: rows[0]['spans'].append(copy.deepcopy(rows[0]['spans'][0])))
        self.run_builder('--python-root', py, passed=False)

    def test_rejects_missing_root_parent_cycles_and_reversed_times(self):
        py = self.python_case()
        original = json.loads((py/'sdk-exports.json').read_text())
        for mutate in [lambda spans: spans[1].update(parent_span_id='missing'),
                       lambda spans: spans[0].update(parent_span_id='generation'),
                       lambda spans: spans[1].update(end_time_unix_nano=START-1)]:
            value = copy.deepcopy(original); mutate(value[0]['spans']); self.write(py/'sdk-exports.json', value)
            self.run_builder('--python-root', py, passed=False)

    def test_rejects_extra_trace_outside_report_or_identity_mismatch(self):
        py = self.python_case()
        self.mutate(py/'report.json', lambda value: value['identity'].update(agent_uuid='different'))
        self.run_builder('--python-root', py, passed=False)

    def test_rejects_sdk_time_outside_report_and_session_mismatch(self):
        py = self.python_case()
        original = json.loads((py/'report.json').read_text())
        self.mutate(py/'report.json', lambda value: value['bounds'].update(from_ms=START//1000000+1))
        self.run_builder('--python-root', py, passed=False)
        self.write(py/'report.json', original)
        self.mutate(py/'report.json', lambda value: value['sessions'][0].update(session_id='wrong-session'))
        self.run_builder('--python-root', py, passed=False)

    def test_suite_uses_case_names_and_rejects_partial_matrix(self):
        py = self.python_case()
        case = {'case': 'python', 'execute_exit': 0, 'verify_exit': 0, 'source_verified': True, 'platform_verified': True,
                'report': '/container/path/not/a/host/path/report.json'}
        self.write(self.root/'summary.json', {'status': 'passed', 'mode': 'ci', 'executed_cases': 1,
                   'expected_cases': 1, 'cases': [case]})
        self.assertEqual(len(self.run_builder('--python-root', self.root)['traces']), 1)
        self.mutate(self.root/'summary.json', lambda value: value.update(expected_cases=2))
        self.run_builder('--python-root', self.root, passed=False)

    def test_live_python_uses_provider_ledger_and_requires_each_readback(self):
        py = self.python_case()
        self.write(py/'sdk-exports.json', [{'spans': [span(), span('generation', 'root', 'generation', {'input': 2, 'output': 1, 'total': 3})]}])
        report = {'status': 'passed', 'mode': 'platform', 'provider_base_url': 'https://integrate.api.nvidia.com/v1',
                  'identity': TARGET,
                  'versions': {'langfuse': '5.0.2'}, 'cases': [{'case': 'langchain-stream', 'trace_id': 'trace'}],
                  'bounds': {'from': START//1000000, 'to': START//1000000+2},
                  'checks': {f'langchain-stream.{name}': True for name in ['single_request', 'single_generation',
                             'unique_spans', 'actual_output', 'provider_usage', 'sdk_usage',
                             'native_readback', 'native_provider_usage', 'native_http', 'native_complete', 'native_identity',
                             'v1_trace', 'v1_pagination', 'v1_observations', 'v1_provider_usage',
                             'v1_session_identity', 'v1_session_inventory', 'v1_session_traces', 'v1_session_created']}}
        self.write(py/'report.json', report)
        self.write(py/'provider-ledger.json', [{'case': 'langchain-stream', 'status': 200,
                   'usage': {'prompt_tokens': 2, 'completion_tokens': 1, 'total_tokens': 3}}])
        self.assertEqual(self.run_builder('--python-root', py, source='nvidia')['traces'][0]['usage'], {'input': 2, 'output': 1, 'total': 3})
        original = json.loads((py/'report.json').read_text())
        for check in ('native_readback', 'native_provider_usage', 'native_http', 'native_complete', 'native_identity',
                      'v1_trace', 'v1_pagination', 'v1_observations', 'v1_provider_usage', 'v1_session_identity',
                      'v1_session_inventory', 'v1_session_traces', 'v1_session_created'):
            with self.subTest(check=check):
                value = copy.deepcopy(original)
                value['checks'].pop('langchain-stream.' + check)
                self.write(py/'report.json', value)
                self.run_builder('--python-root', py, source='nvidia', passed=False)

    def test_live_python_rejects_other_target_identity(self):
        py = self.python_case()
        self.write(py/'report.json', {'status': 'passed', 'mode': 'platform',
            'provider_base_url': 'https://integrate.api.nvidia.com/v1', 'checks': {'stub': True},
            'identity': {**TARGET, 'spaceId': 'another-space'}})
        self.run_builder('--python-root', py, source='nvidia', passed=False)

    def test_metadata_strings_are_not_decoded_twice_for_legacy_python(self):
        py = self.python_case()
        self.mutate(py/'sdk-exports.json', lambda rows: [item.update(scope_version='4.16.0') for item in rows[0]['spans']])
        self.mutate(py/'report.json', lambda value: value['versions'].update(langfuse='4.16.0'))
        manifest = self.run_builder('--python-root', py)
        self.assertEqual(manifest['traces'][0]['spans'][0]['metadata'], {'numeric_string': '"42"', 'number': '42'})

    def test_rejects_partial_js_readback_and_missing_lifecycle_ledger(self):
        js = self.js_case()
        self.mutate(js/'platform-readback.json', lambda value: value.update(traces=2))
        self.run_builder('--js-root', js, passed=False)
        self.mutate(js/'platform-readback.json', lambda value: value.update(traces=1))
        (js/'ledger.json.lifecycle.json').unlink()
        self.run_builder('--js-root', js, passed=False)

    def test_rejects_private_target_and_ambiguous_discovery(self):
        py = self.python_case()
        self.write(self.target, {**TARGET, 'space_key': 'secret-must-not-copy'})
        self.run_builder('--python-root', py, passed=False)
        self.write(self.target, TARGET)
        self.run_builder('--python-root', self.root/'absent', passed=False)
        self.run_builder(passed=False)


if __name__ == '__main__':
    unittest.main()
