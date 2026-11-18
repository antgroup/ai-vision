import json
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from corpus import (CorpusError, export_case, load_package, stable_hash, validate_evidence)


def public_source():
    return {'mode': 'fake', 'model': 'protocol-fixture', 'app': 'public-runtime',
        'result': {'exit_code': 0, 'version': '1', 'output': 'read /public/project'},
        'requests': [{'request': {'messages': []}, 'response': {'tool_calls': [
            {'id': 'call1', 'function': {'arguments': '{"path":"/public/project"}'}}]}},
            {'request': {'messages': [{'role': 'tool', 'tool_call_id': 'call1', 'content': '0'}]}}],
        'exports': [{'signal': 'traces', 'admitted': True, 'otlp': {'public': 'payload'}, 'spans': [
            {'trace_id': 't1', 'span_id': 's1', 'parent_span_id': '', 'start_time_unix_nano': 10,
             'end_time_unix_nano': 30, 'attributes': {'null': None, 'zero': 0}},
            {'trace_id': 't1', 'span_id': 's2', 'parent_span_id': 's1', 'start_time_unix_nano': 11,
             'end_time_unix_nano': 20, 'attributes': {'gen_ai.tool.call.id': 'call1',
             'gen_ai.tool.call.arguments': '{"path":"/public/project"}', 'gen_ai.tool.call.result': '0'}}]}]}


def provenance():
    return {'case_id': 'public1', 'origin': 'synthetic', 'model_execution': 'fake',
        'runtime': 'public-runtime', 'sdk': 'public-sdk', 'version': '1', 'model': 'protocol-fixture',
        'collection_method': 'public protocol fixture', 'allowed_channels': ['traces', 'requests', 'result']}


def labels():
    return {'schema_version': 1, 'independent_of_judge': True, 'labels': [{'case_id': 'public1',
        'origin': 'synthetic', 'metric_key': 'completion', 'expected': 'bad',
        'references': [{'trace_id': 't1', 'span_id': 's2'}, {'call_id': 'call1'}],
        'rationale': 'Public fixture label, not a semantic model result',
        'labeler': {'id': 'reviewer1', 'kind': 'human'}, 'disputed': False, 'critical': True}]}


def review():
    return {'reviewer': 'reviewer1', 'personal_data_reviewed': True, 'secret_reviewed': True,
        'replacements': [], 'invalidated_metrics': [], 'invalidation_reasons': {}}


class CorpusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / 'source.json'
        self.source.write_text(json.dumps(public_source()))

    def tearDown(self):
        self.tmp.cleanup()

    def export(self, **kwargs):
        return export_case(self.source, labels=kwargs.pop('labels', labels()),
            provenance=kwargs.pop('provenance', provenance()), review=kwargs.pop('review', review()),
            out=self.root / 'out', **kwargs)

    def test_hashes_tree_causality_missing_and_zero(self):
        manifest = self.export()
        package = load_package(self.root / 'out' / 'manifest.json')
        value = package['cases'][0]['evidence']
        self.assertEqual(value, public_source())
        self.assertNotIn('missing', value['exports'][0]['spans'][0]['attributes'])
        self.assertIsNone(value['exports'][0]['spans'][0]['attributes']['null'])
        self.assertEqual(value['exports'][0]['spans'][0]['attributes']['zero'], 0)
        self.assertEqual(manifest['cases'][0]['evidence_sha256'], stable_hash(value))
        self.assertEqual(manifest['labels_sha256'], stable_hash(labels()))
        self.assertEqual(manifest['cases'][0]['relative_time_origin_ns'], 10)

    def test_scoped_replacement_applies_encoded_io_consistently(self):
        policy = review()
        policy.update(replacements=[{'scope': '', 'from': '/public/project', 'to': '/fixture/project'}],
            invalidated_metrics=['privacy'], invalidation_reasons={'privacy': 'Mask hides privacy evidence'})
        self.export(review=policy)
        value = load_package(self.root / 'out' / 'manifest.json')['cases'][0]['evidence']
        self.assertIn('/fixture/project', value['requests'][0]['response']['tool_calls'][0]['function']['arguments'])
        self.assertIn('/fixture/project', value['exports'][0]['spans'][1]['attributes']['gen_ai.tool.call.arguments'])

    def test_origins_cannot_be_inferred_or_fake_promoted_to_real(self):
        for policy in ({}, {**provenance(), 'origin': 'real'}, {**provenance(), 'origin': 'counterfactual'}):
            with self.subTest(policy=policy), self.assertRaises(CorpusError):
                self.export(provenance=policy)

    def test_secret_review_and_scanner_fail_closed(self):
        for policy in ({}, {**review(), 'personal_data_reviewed': False}):
            with self.assertRaises(CorpusError):
                self.export(review=policy)
        value = public_source()
        value['result']['output'] = 'Authorization: Bearer public-placeholder-credential'
        self.source.write_text(json.dumps(value))
        with self.assertRaises(CorpusError):
            self.export()
        self.assertFalse((self.root / 'out').exists())

    def test_reject_missing_refs_cross_trace_parent_duplicates_and_cycles(self):
        for mutation in ('ref', 'parent', 'duplicate', 'cycle'):
            value = public_source()
            spans = value['exports'][0]['spans']
            label = labels()
            if mutation == 'ref': label['labels'][0]['references'][0]['span_id'] = 'unknown'
            elif mutation == 'parent': spans[1]['parent_span_id'] = 'othertrace'
            elif mutation == 'duplicate': spans.append(deepcopy(spans[1]))
            else: spans[0]['parent_span_id'] = 's2'
            self.source.write_text(json.dumps(value))
            with self.subTest(mutation=mutation), self.assertRaises(CorpusError):
                self.export(labels=label)

    def test_identity_masking_review_or_integrity_mismatch_rejected(self):
        policy = {**review(), 'replacements': [{'scope': '', 'from': 's2', 'to': 'hidden'}],
            'invalidated_metrics': ['completion'], 'invalidation_reasons': {'completion': 'Masks completion evidence'}}
        with self.assertRaises(CorpusError): self.export(review=policy)
        self.export()
        path = self.root / 'out' / 'evidence.json'
        path.write_text('{}')
        with self.assertRaises(CorpusError): load_package(self.root / 'out' / 'manifest.json')

    def test_encoded_identity_replacement_and_undeclared_channels_fail_closed(self):
        value = public_source()
        value['exports'][0]['otlp'] = {'encoded': '{"spanId":"s2"}'}
        self.source.write_text(json.dumps(value))
        policy = {**review(), 'replacements': [{'scope': '/exports/0/otlp', 'from': 's2', 'to': 'hidden'}],
            'invalidated_metrics': ['completion'], 'invalidation_reasons': {'completion': 'Masks completion evidence'}}
        with self.assertRaises(CorpusError): self.export(review=policy)
        with self.assertRaises(CorpusError): self.export(provenance={**provenance(), 'allowed_channels': ['result']})

    def test_redaction_cannot_rewrite_frozen_labels_or_provenance(self):
        for field, before, after in (('expected', 'bad', 'good'), ('origin', 'synthetic', 'counterfactual'),
                ('metric_key', 'completion', 'privacy'), ('rationale', 'Public', 'Altered'),
                ('labeler/id', 'reviewer1', 'reviewer2'), ('labeler/kind', 'human', 'agent')):
            policy = {**review(), 'replacements': [{'scope': '/labels/0/' + field, 'from': before, 'to': after}],
                'invalidated_metrics': ['privacy'], 'invalidation_reasons': {'privacy': 'public mask'}}
            with self.subTest(field=field), self.assertRaises(CorpusError): self.export(review=policy)
            self.assertFalse((self.root / 'out').exists())
        self.export()
        manifest = json.loads((self.root / 'out' / 'manifest.json').read_text())
        self.assertEqual(manifest['original_labels_sha256'], stable_hash(labels()))
        self.assertEqual(manifest['original_labels_sha256'], manifest['labels_sha256'])

    def test_redaction_cannot_rewrite_frozen_configuration(self):
        value = public_source()
        value['frozen'] = {'model': 'public-original'}
        self.source.write_text(json.dumps(value))
        policy = {**review(), 'replacements': [{'scope': '/frozen/model', 'from': 'public-original', 'to': 'altered'}],
            'invalidated_metrics': ['privacy'], 'invalidation_reasons': {'privacy': 'public mask'}}
        with self.assertRaises(CorpusError): self.export(review=policy)
        self.assertFalse((self.root / 'out').exists())

    def test_invalidation_needs_named_reason_and_never_changes_truth(self):
        policy = {**review(), 'invalidated_metrics': ['completion']}
        with self.assertRaises(CorpusError): self.export(review=policy)
        policy['invalidation_reasons'] = {'completion': 'Final output intentionally masked'}
        self.export(review=policy)
        value = load_package(self.root / 'out' / 'manifest.json')
        self.assertEqual(value['labels'], labels())
        self.assertEqual(value['cases'][0]['review']['invalidation_reasons']['completion'],
            'Final output intentionally masked')

    def test_no_output_overwrite_and_no_reviewed_package_in_git(self):
        self.export()
        with self.assertRaises(FileExistsError): self.export()
        with self.assertRaises(CorpusError):
            export_case(self.source, labels=labels(), provenance=provenance(), review=review(),
                out=Path(__file__).parent / 'fixtures' / 'unreviewed-export')


if __name__ == '__main__':
    unittest.main()
