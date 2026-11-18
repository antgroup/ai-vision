"""Explicitly reviewed, provenance-bound offline diagnosis evidence packages.

No source location implies real provenance. A scanner is a backstop; a named
reviewer must also review arbitrary personal data. Exported packages stay outside
Git until a separate publication review. This module never discovers raw traces.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re

SCHEMA_VERSION = 1
ORIGINS = {'real', 'synthetic', 'counterfactual'}
IDENTITY_FIELDS = {'trace_id', 'span_id', 'parent_span_id', 'traceId', 'spanId', 'parentSpanId',
    'tool_call_id', 'call_id', 'source_action_id', 'gen_ai.tool.call.id'}
_SECRET = re.compile(r'(?i)(?:\bBearer\s+[^\s"\']+|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|'
    r'\b(?:sk-|nvapi-)[A-Za-z0-9_-]{12,}|\bAKIA[A-Z0-9]{16}|'
    r'\b(?:password|api[_-]?key|access[_-]?token|client[_-]?secret)\s*[=:]\s*[^\s,;"\']+)')
_SECRET_KEYS = re.compile(r'(?i)^(?:authorization|cookie|set-cookie|password|api[_-]?key|'
    r'access[_-]?token|client[_-]?secret)$')


class CorpusError(ValueError):
    pass


def canonical_bytes(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()


def stable_hash(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _require(condition, message):
    if not condition:
        raise CorpusError(message)


def scan_secrets(value):
    """Reject known secret forms without returning the suspect text or its path."""
    if isinstance(value, str):
        _require(not _SECRET.search(value), 'Secret scan rejected package content')
        # Many OTLP attributes contain JSON strings; inspect sensitive keys too.
        try:
            encoded = json.loads(value)
        except (ValueError, TypeError):
            encoded = None
        if isinstance(encoded, (dict, list)):
            scan_secrets(encoded)
    elif isinstance(value, dict):
        if 'key' in value and isinstance(value['key'], str) and _SECRET_KEYS.fullmatch(value['key']):
            _require(value.get('value') in (None, '', '[REDACTED]') or
                value.get('value') == {'stringValue': '[REDACTED]'}, 'Secret scan rejected sensitive attribute')
        for key, item in value.items():
            if _SECRET_KEYS.fullmatch(str(key)):
                _require(item in (None, '', '[REDACTED]'), 'Secret scan rejected sensitive field')
            scan_secrets(item)
    elif isinstance(value, list):
        for item in value:
            scan_secrets(item)


def validate_evidence(evidence):
    """Validate normalized capture identities and references, never invent I/O."""
    _require(isinstance(evidence, dict) and isinstance(evidence.get('exports'), list), 'Missing source exports')
    spans, calls = {}, set()
    starts = []
    for export in evidence['exports']:
        _require(isinstance(export, dict) and type(export.get('admitted')) is bool, 'Explicit admission required')
        for span in export.get('spans', []):
            _require(isinstance(span, dict), 'Invalid span')
            trace, identity = span.get('trace_id'), span.get('span_id')
            _require(_text(trace) and _text(identity), 'Missing physical span identity')
            key = (trace, identity)
            _require(key not in spans, 'Duplicate span identity is ambiguous')
            spans[key] = span
            parent = span.get('parent_span_id', '')
            _require(isinstance(parent, str), 'Invalid parent identity')
            for field in ('start_time_unix_nano', 'end_time_unix_nano'):
                if field in span:
                    _require(type(span[field]) is int and span[field] >= 0, 'Invalid span clock')
            if 'start_time_unix_nano' in span:
                starts.append(span['start_time_unix_nano'])
            if all(field in span for field in ('start_time_unix_nano', 'end_time_unix_nano')):
                _require(span['end_time_unix_nano'] >= span['start_time_unix_nano'], 'Negative span duration')
            call = span.get('attributes', {}).get('gen_ai.tool.call.id')
            if call is not None:
                _require(_text(call) and call not in calls, 'Ambiguous tool call identity')
                calls.add(call)
    _require(bool(spans), 'Corpus has no physical spans')
    for (trace, identity), span in spans.items():
        parent, seen = span.get('parent_span_id', ''), {identity}
        while parent:
            _require((trace, parent) in spans, 'Parent is outside trace')
            _require(parent not in seen, 'Cyclic span tree')
            seen.add(parent)
            parent = spans[(trace, parent)].get('parent_span_id', '')
    requests = evidence.get('requests')
    _require(isinstance(requests, list), 'Missing source request ledger')
    declared = set()
    references = []
    for row in requests:
        _require(isinstance(row, dict), 'Invalid request ledger')
        for call in (row.get('response') or {}).get('tool_calls', []):
            identity = call.get('id')
            _require(_text(identity) and identity not in declared, 'Duplicate provider call identity')
            declared.add(identity)
        for message in (row.get('request') or {}).get('messages', []):
            if message.get('role') == 'tool':
                references.append(message.get('tool_call_id'))
    _require(all(_text(ref) and ref in declared and ref in calls for ref in references), 'Unbound tool result reference')
    return {'spans': spans, 'calls': calls | declared, 'relative_time_origin_ns': min(starts) if starts else None}


def validate_labels(labels, evidence, *, case_id, origin):
    index = validate_evidence(evidence)
    _require(isinstance(labels, dict) and labels.get('schema_version') == SCHEMA_VERSION and
        labels.get('independent_of_judge') is True and isinstance(labels.get('labels'), list),
        'Labels must be independently frozen before Judge execution')
    identities = set()
    for label in labels['labels']:
        _require(isinstance(label, dict) and label.get('case_id') == case_id and label.get('origin') == origin,
            'Label source provenance mismatch')
        identity = (case_id, label.get('metric_key'))
        _require(_text(identity[1]) and identity not in identities, 'Ambiguous label identity')
        identities.add(identity)
        _require(label.get('expected') in {'good', 'bad', 'skip'} and _text(label.get('rationale')),
            'Label requires expected result and rationale')
        labeler = label.get('labeler') or {}
        _require(isinstance(labeler, dict) and _text(labeler.get('id')) and labeler.get('kind') in {'human', 'agent'},
            'Labeler provenance required')
        _require(type(label.get('disputed')) is bool and type(label.get('critical', False)) is bool,
            'Dispute and critical flags must be explicit booleans')
        refs = label.get('references')
        _require(isinstance(refs, list) and bool(refs), 'Label requires physical evidence references')
        for ref in refs:
            _require(isinstance(ref, dict), 'Invalid label reference')
            if set(ref) == {'trace_id', 'span_id'}:
                _require((ref['trace_id'], ref['span_id']) in index['spans'], 'Label references absent span')
            elif set(ref) == {'call_id'}:
                _require(ref['call_id'] in index['calls'], 'Label references absent call')
            else:
                raise CorpusError('Unknown label reference form')
    _require(bool(identities), 'No independently labelled cases')
    return index


def _outside_git(path):
    resolved = path.resolve()
    _require(not any((parent / '.git').exists() for parent in (resolved, *resolved.parents)),
        'Unreviewed evidence packages must remain outside Git')
    return resolved


def _sanitize(value, review, *, protected_ids=()):
    _require(isinstance(review, dict) and _text(review.get('reviewer')) and
        review.get('personal_data_reviewed') is True and review.get('secret_reviewed') is True,
        'Explicit personal-data and secret review is required')
    rules, invalidated = review.get('replacements'), review.get('invalidated_metrics')
    _require(isinstance(rules, list) and isinstance(invalidated, list) and all(_text(x) for x in invalidated),
        'Explicit replacement scopes and invalidated metric keys required')
    reasons = review.get('invalidation_reasons', {})
    _require(isinstance(reasons, dict) and set(reasons) == set(invalidated) and all(_text(reason) for reason in reasons.values()),
        'Every invalidated metric requires an explicit review reason')
    _require(not rules or bool(invalidated), 'Masking must disclose invalidated metrics')
    for rule in rules:
        _require(isinstance(rule, dict) and set(rule) == {'scope', 'from', 'to'} and
            isinstance(rule['scope'], str) and (not rule['scope'] or rule['scope'].startswith('/')) and
            _text(rule['from']) and _text(rule['to']), 'Invalid scoped replacement rule')
    changed = []
    def visit(item, path='', field='', immutable=False):
        if isinstance(item, dict):
            return {key: visit(val, path + '/' + str(key).replace('~', '~0').replace('/', '~1'), key,
                    immutable or key in {'labels', 'frozen', 'frozen_config'})
                for key, val in item.items()}
        if isinstance(item, list):
            return [visit(val, path + '/' + str(i), field, immutable) for i, val in enumerate(item)]
        if isinstance(item, str):
            for rule in rules:
                scope = rule['scope']
                if (not scope or path == scope or path.startswith(scope + '/')) and rule['from'] in item:
                    _require(not immutable, 'Frozen labels and configuration cannot be redacted')
                    _require(field not in IDENTITY_FIELDS and not (field == 'id' and '/tool_calls/' in path),
                        'Identity masking is forbidden; preserve tree and call references')
                    replaced = item.replace(rule['from'], rule['to'])
                    _require(all(item.count(identity) == replaced.count(identity) for identity in protected_ids),
                        'Masking changed encoded physical identities or references')
                    item = replaced
                    changed.append(path)
        return item
    result = visit(deepcopy(value))
    # IDs encoded inside OTLP or JSON strings are checked against the original
    # identity index by callers too. Report paths alone, never replacement text.
    return result, sorted(set(changed))


def _validate_provenance(provenance, source):
    _require(isinstance(provenance, dict) and provenance.get('origin') in ORIGINS, 'Explicit fixture origin required')
    for field in ('case_id', 'runtime', 'sdk', 'version', 'model', 'collection_method'):
        _require(_text(provenance.get(field)), 'Missing source provenance: ' + field)
    _require(provenance.get('model_execution') in {'real', 'fake', 'not_applicable'}, 'Model execution provenance required')
    _require(isinstance(provenance.get('allowed_channels'), list) and provenance['allowed_channels'] and
        all(channel in {'traces', 'metrics', 'logs', 'requests', 'result', 'responses_bridge'}
            for channel in provenance['allowed_channels']), 'Explicit allowed channels required')
    channels = set(provenance['allowed_channels'])
    _require(all(not export.get(field) or signal in channels for export in source.get('exports', [])
        for field, signal in (('spans', 'traces'), ('metrics', 'metrics'), ('logs', 'logs'))) and
        all(not source.get(field) or field in channels for field in ('requests', 'result', 'responses_bridge')),
        'Exported content uses an undeclared channel')
    if source.get('mode') == 'fake':
        _require(provenance['model_execution'] == 'fake' and provenance['origin'] != 'real',
            'Native execution with fake model is synthetic protocol evidence')
    if provenance['origin'] == 'real':
        _require(source.get('mode') == 'nvidia' and provenance['model_execution'] == 'real',
            'Real origin requires an explicitly real model source')
    if provenance['origin'] == 'counterfactual':
        _require(isinstance(provenance.get('derived_from_sha256'), str) and
            re.fullmatch('[0-9a-f]{64}', provenance['derived_from_sha256']) and
            isinstance(provenance.get('changes'), list) and provenance['changes'] and
            all(_text(change) for change in provenance['changes']), 'Counterfactual requires parent hash and edit ledger')
    _require(source.get('model') == provenance['model'], 'Source model provenance mismatch')


def _write(path, value):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def export_case(source_path, *, labels, provenance, review, out):
    """Export one source.json; all policies are explicit objects, never inferred."""
    source_path, out = Path(source_path), _outside_git(Path(out))
    raw = source_path.read_bytes()
    source = json.loads(raw)
    _validate_provenance(provenance, source)
    original = validate_labels(labels, source, case_id=provenance['case_id'], origin=provenance['origin'])
    protected_ids = original['calls'] | {identity for pair in original['spans'] for identity in pair}
    evidence, paths = _sanitize(source, review, protected_ids=protected_ids)
    sanitized_labels, label_paths = _sanitize(labels, review, protected_ids=protected_ids)
    sanitized_provenance, provenance_paths = _sanitize(provenance, review)
    _require(stable_hash(sanitized_labels) == stable_hash(labels), 'Redaction cannot change independent truth labels')
    _require(stable_hash(sanitized_provenance) == stable_hash(provenance), 'Redaction cannot change source provenance')
    _validate_provenance(sanitized_provenance, evidence)
    index = validate_labels(sanitized_labels, evidence, case_id=provenance['case_id'], origin=provenance['origin'])
    _require(set(original['spans']) == set(index['spans']) and original['calls'] == index['calls'], 'Masking changed identities')
    scan_secrets(evidence)
    scan_secrets(sanitized_labels)
    scan_secrets(sanitized_provenance)
    case = {'case_id': provenance['case_id'], 'provenance': sanitized_provenance,
        'evidence_file': 'evidence.json', 'evidence_sha256': stable_hash(evidence),
        'original_source_sha256': hashlib.sha256(raw).hexdigest(),
        'source_exports_sha256': stable_hash(source['exports']),
        'admitted_exports_sha256': stable_hash([e for e in source['exports'] if e['admitted']]),
        'source_otlp_sha256': stable_hash([e.get('otlp') for e in source['exports']]),
        'admitted_otlp_sha256': stable_hash([e.get('otlp') for e in source['exports'] if e['admitted']]),
        'relative_time_origin_ns': index['relative_time_origin_ns'],
        'review': {'reviewer': review['reviewer'], 'personal_data_reviewed': True, 'secret_reviewed': True,
            'changed_paths': paths, 'label_changed_paths': label_paths, 'provenance_changed_paths': provenance_paths,
            'invalidated_metrics': review['invalidated_metrics'],
            'invalidation_reasons': deepcopy(review.get('invalidation_reasons', {}))}}
    manifest = {'schema_version': SCHEMA_VERSION, 'publication_review': 'pending',
        'labels_file': 'labels.json', 'labels_sha256': stable_hash(sanitized_labels),
        'original_labels_sha256': stable_hash(labels), 'cases': [case]}
    scan_secrets(manifest)
    out.mkdir(parents=True, exist_ok=False, mode=0o700)
    _write(out / 'evidence.json', evidence)
    _write(out / 'labels.json', sanitized_labels)
    _write(out / 'manifest.json', manifest)
    return manifest


def load_package(manifest_path):
    """Read a package with content hashes and source/reference contracts checked."""
    path = Path(manifest_path)
    manifest = json.loads(path.read_text())
    _require(manifest.get('schema_version') == SCHEMA_VERSION and isinstance(manifest.get('cases'), list),
        'Unsupported corpus manifest')
    def read(relative, expected):
        _require(isinstance(relative, str), 'Missing package file')
        target = (path.parent / relative).resolve()
        _require(target.is_relative_to(path.parent.resolve()), 'Package path escapes root')
        value = json.loads(target.read_text())
        _require(stable_hash(value) == expected, 'Package content hash mismatch')
        scan_secrets(value)
        return value
    _require(manifest.get('original_labels_sha256') == manifest.get('labels_sha256'),
        'Exported labels differ from original frozen truth')
    labels = read(manifest.get('labels_file'), manifest.get('labels_sha256'))
    cases, seen = [], set()
    for case in manifest['cases']:
        _require(case.get('case_id') not in seen, 'Duplicate case identity')
        seen.add(case['case_id'])
        evidence = read(case.get('evidence_file'), case.get('evidence_sha256'))
        _validate_provenance(case.get('provenance'), evidence)
        _require(case['case_id'] == case['provenance']['case_id'], 'Case provenance mismatch')
        review = case.get('review')
        _require(isinstance(review, dict) and _text(review.get('reviewer')) and
            review.get('personal_data_reviewed') is True and review.get('secret_reviewed') is True and
            isinstance(review.get('invalidated_metrics'), list) and all(_text(key) for key in review['invalidated_metrics']),
            'Missing independent redaction review')
        reasons = review.get('invalidation_reasons', {})
        _require(isinstance(reasons, dict) and set(reasons) == set(review['invalidated_metrics']) and
            all(_text(reason) for reason in reasons.values()), 'Missing masking-invalidation reasons')
        selected = {**labels, 'labels': [label for label in labels['labels'] if label.get('case_id') == case['case_id']]}
        validate_labels(selected, evidence, case_id=case['case_id'], origin=case['provenance']['origin'])
        cases.append({**case, 'evidence': evidence})
    _require(bool(cases) and all(label.get('case_id') in seen for label in labels['labels']), 'Unbound corpus labels')
    scan_secrets(manifest)
    return {**manifest, 'cases': cases, 'labels': labels}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    export = sub.add_parser('export')
    for flag in ('source', 'labels', 'provenance', 'review', 'out'):
        export.add_argument('--' + flag, type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        export_case(args.source, labels=json.loads(args.labels.read_text()),
            provenance=json.loads(args.provenance.read_text()), review=json.loads(args.review.read_text()), out=args.out)
    except (CorpusError, OSError, ValueError, TypeError, KeyError):
        parser.exit(1, 'Corpus export rejected; review source, provenance and redaction contracts privately.\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
