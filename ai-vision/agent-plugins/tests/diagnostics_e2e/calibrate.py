"""Frozen-label calibration contracts; platform Trial semantics stay in the platform.

An injected executor must use the shared NvidiaProvider for actual model HTTP
attempts. The CLI requires an explicit frozen Core adapter, otherwise returns
adapter_pending; it never clones AgentLens Judge logic.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import argparse
import hashlib
import hmac
import getpass
import json
import math
import os
from pathlib import Path
import secrets
import stat
import sys
import threading
import tempfile
import time
import uuid
from urllib.parse import urlsplit

from corpus import (CorpusError, _outside_git, _write, load_package, scan_secrets,
    stable_hash, validate_labels)

NATIVE = Path(__file__).resolve().parent.parent / 'native_agent_e2e'
if str(NATIVE) not in sys.path:
    sys.path.insert(0, str(NATIVE))
from nvidia_provider import NvidiaProvider, RateBudget

DIRECTORY = Path(__file__).resolve().parent
if not sys.path or sys.path[0] != str(DIRECTORY):
    sys.path.insert(0, str(DIRECTORY))
if __name__ == '__main__':
    # Adapter imports must share the CLI's lease classes, not load a second copy.
    sys.modules['calibrate'] = sys.modules[__name__]


class CalibrationError(ValueError):
    pass


def _require(condition, message):
    if not condition:
        raise CalibrationError(message)


def _private_owner(metadata):
    return os.name != 'posix' or (
        metadata.st_uid == os.geteuid() and metadata.st_mode & 0o077 == 0
    )


def _lease_key(root):
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    metadata = root.lstat()
    _require(stat.S_ISDIR(metadata.st_mode) and _private_owner(metadata),
        'Credential leases require a private directory owned by this user')
    path = root / '.lease-key'

    def read_key():
        flags = (os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
            | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0))
        with os.fdopen(os.open(path, flags), 'rb') as stream:
            metadata = os.fstat(stream.fileno())
            _require(stat.S_ISREG(metadata.st_mode) and _private_owner(metadata),
                'Credential lease key must be a private regular file owned by this user')
            key = stream.read(33)
        _require(len(key) == 32, 'Credential lease key requires owner review')
        return key

    try:
        return read_key()
    except FileNotFoundError:
        if any(root.glob('*.lock')):
            # A peer may have initialized the key and acquired a lock while
            # this process was checking the directory.
            try:
                return read_key()
            except FileNotFoundError:
                raise CalibrationError('Existing credential locks require owner review before key initialization') from None

    descriptor, temporary = tempfile.mkstemp(prefix='.lease-key-', dir=root)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(secrets.token_bytes(32))
            stream.flush()
            os.fsync(stream.fileno())
        # Publish the complete key atomically. Never replace a peer's key.
        try:
            os.link(temporary, path)
        except FileExistsError:
            pass
    finally:
        os.unlink(temporary)
    return read_key()


class CredentialLease:
    """Portable exclusive credential lock. Crash leftovers fail closed.

    The identifier is an HMAC of the credential under a private lease-directory
    key. No credential or environment contents enter lock files.
    All orchestrated source/probe/Judge work must retain this same lease/budget.
    Never unlink a competing owner's file or automatically break stale locks.
    """
    def __init__(self, credential, lock_dir=None, *, budget=None):
        _require(isinstance(credential, str) and bool(credential), 'Private provider credential is required')
        _require(budget is None or isinstance(budget, RateBudget), 'Reuse the existing RateBudget')
        # A stable per-user location makes standalone invocations contend.
        user_id = hashlib.sha256(getpass.getuser().encode()).hexdigest()[:20]
        root = Path(lock_dir) if lock_dir is not None else Path(tempfile.gettempdir()) / ('aiv-provider-leases-' + user_id)
        self._key = _lease_key(root)
        self.path = root / self._lock_name(credential)
        self.rate_budget = budget if budget is not None else RateBudget()
        self.active = False
        self._descriptor = None

    def __enter__(self):
        _require(not self.active, 'Credential lease is already active')
        try:
            self._descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            raise CalibrationError('Credential is already exclusively owned; stale lock requires owner review') from None
        self._identity = os.fstat(self._descriptor)
        self.active = True
        return self

    def __exit__(self, *_args):
        if self._descriptor is None:
            return
        self.active = False
        # Windows cannot unlink an open file. Close first, retaining the inode
        # identity so a replaced file is never removed by this owner.
        os.close(self._descriptor)
        self._descriptor = None
        if self._owns_path():
            self.path.unlink()

    def _owns_path(self):
        try:
            current = self.path.lstat()
        except OSError:
            return False
        return (current.st_dev, current.st_ino) == (self._identity.st_dev, self._identity.st_ino)

    def _lock_name(self, credential):
        return hmac.digest(self._key, credential.encode(), 'sha256').hex() + '.lock'

    def matches_credential(self, credential):
        return hmac.compare_digest(self.path.name, self._lock_name(credential))

    @property
    def attempts(self):
        return self.rate_budget.attempts

    def reserve(self, stop=None):
        _require(self.active and self._owns_path(), 'Exclusive credential ownership is required')
        return self.rate_budget.reserve(stop)


def _owned(budget):
    _require(isinstance(budget, CredentialLease) and budget.active and budget._owns_path(),
        'Orchestrator must supply an active exclusive credential lease')


@contextmanager
def provider_lease(key, budget=None):
    """Standalone and orchestrated calls contend on the same credential lock."""
    _require(isinstance(key, str) and bool(key), 'Private provider credential is required')
    if budget is not None:
        _owned(budget)
        _require(budget.matches_credential(key), 'Provider credential differs from exclusive ownership')
        yield budget
    else:
        with CredentialLease(key) as lease:
            yield lease


class AttemptBudget:
    """Per-case attempt cap around the one existing rolling RateBudget.

    This adds no rolling-quota algorithm. Probes and provider retries call the
    same reserve boundary, so at most three actual attempts belong to one case.
    """
    def __init__(self, owner):
        _owned(owner)
        self.owner = owner
        self.guard = threading.Lock()
        self.scope_active = False
        self.started = 0
        self.identities = []
        self.refused = False
        self.stop = threading.Event()

    @contextmanager
    def case(self):
        with self.guard:
            _require(not self.scope_active, 'Calibration executions must be sequential')
            self.scope_active, self.started, self.refused = True, 0, False
            self.stop = threading.Event()
            self.identities = []
        try:
            yield self
        finally:
            with self.guard:
                self.scope_active = False

    def reserve(self, stop=None):
        with self.guard:
            _require(self.scope_active, 'Provider attempt has no active labelled case')
            if self.stop.is_set() or (stop is not None and stop.is_set()) or self.started >= 3:
                self.refused = True
                return None
            ordinal = self.owner.reserve(stop or self.stop)
            if ordinal is None:
                self.refused = True
                return None
            self.started += 1
            self.identities.append(ordinal)
            return ordinal

    @property
    def attempts(self):
        return self.owner.attempts


def frozen_config(value):
    _require(isinstance(value, dict), 'Calibration config must be explicit')
    result = deepcopy(value)
    for field in ('model', 'config_revision', 'code_revision'):
        _require(isinstance(result.get(field), str) and result[field].strip(), 'Missing frozen ' + field)
    _require(isinstance(result.get('parameters'), dict) and isinstance(result.get('templates'), dict)
        and result['templates'], 'Frozen parameters and template catalogue required')
    for key, template in result['templates'].items():
        _require(isinstance(key, str) and key and isinstance(template, dict), 'Invalid known template key')
        for field in ('identity', 'revision', 'prompt'):
            _require(isinstance(template.get(field), str) and template[field].strip(), 'Missing frozen template ' + field)
        _require(type(template.get('core')) is bool, 'Explicit Core template designation required')
    scan_secrets(result)
    return result


def read_frozen_config(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            _require(key not in result, 'Duplicate frozen configuration key')
            result[key] = value
        return result
    return frozen_config(json.loads(Path(path).read_text(), object_pairs_hook=unique))


def trial_result(value, metric_key):
    """Validate the current outer Trial result, including diagnoser-level SKIP.

    This reads platform verdicts only. It performs no evaluation of source
    materials, prompts or semantic truth. Requested metric absence fails closed.
    """
    _require(isinstance(value, dict) and type(value.get('skipped', False)) is bool,
        'Invalid Trial result shape')
    metrics = value.get('metrics')
    execution = value.get('execution')
    _require(isinstance(metrics, list) and isinstance(execution, dict), 'Incomplete Trial result')
    status = execution.get('status')
    if value.get('skipped', False):
        _require(not metrics and value.get('verdict') is None and status == 'succeeded'
            and isinstance(value.get('skip_reason'), str) and value['skip_reason'], 'Invalid diagnoser SKIP')
        return {'metric_key': metric_key, 'predicted': 'skip', 'usage': None}
    _require(status in {'succeeded', 'failed'} and value.get('verdict') in {'good', 'bad', 'failed'},
        'Invalid Trial execution or verdict')
    _require(all(isinstance(metric, dict) and isinstance(metric.get('name'), str) for metric in metrics),
        'Invalid Trial metric shape')
    names = [metric['name'] for metric in metrics]
    _require(len(set(names)) == len(names) and metric_key in names, 'Missing or ambiguous requested metric key')
    metric = next(metric for metric in metrics if metric['name'] == metric_key)
    predicted = metric.get('verdict')
    _require(predicted in {'good', 'bad', 'failed'} and
        (status != 'failed' or (predicted == 'failed' and value['verdict'] == 'failed')),
        'Failed Trial cannot become content GOOD or SKIP')
    return {'metric_key': metric_key, 'predicted': predicted, 'usage': value.get('usage')}


class HttpTrialExecutor:
    """HTTP transport for an externally supplied current Trial request/result DTO.

    Callers must provide the current route, body builder and result parser. No
    legacy route or template/output schema is guessed here. PlatformClient owns
    loopback restriction, CSRF and response-envelope validation.
    """
    def __init__(self, client, *, path, request_builder, result_parser, timeout=60):
        _require(isinstance(path, str) and path.startswith('/api/') and '?' not in path and '#' not in path,
            'An explicit current Trial API path is required')
        self.client, self.path = client, path
        self.request_builder, self.result_parser = request_builder, result_parser
        _require(type(timeout) in (int, float) and math.isfinite(timeout) and 0 < timeout <= 933,
            'Trial transport timeout must be finite and bounded')
        self.timeout = timeout

    def __call__(self, evidence, metric_key, *, context):
        body = self.request_builder(evidence, metric_key, context)
        _require(isinstance(body, dict), 'Trial request builder returned no DTO')
        response = self.client.request('POST', self.path, body=body, timeout=self.timeout)
        result = self.result_parser(response, metric_key)
        _require(isinstance(result, dict), 'Trial result parser returned no result')
        return result


def _usage_cost(value):
    usage = value.get('usage')
    _require(usage is None or isinstance(usage, dict), 'Invalid usage object')
    if usage == {}:
        usage = None
    def valid(item):
        if isinstance(item, dict):
            return all(valid(part) for part in item.values())
        return item is None or (type(item) in (int, float) and math.isfinite(item) and item >= 0)
    _require(usage is None or valid(usage), 'Invalid reported usage')
    cost = value.get('reported_cost')
    _require(cost is None or (type(cost) in (int, float) and math.isfinite(cost) and cost >= 0),
        'Invalid reported cost')
    return deepcopy(usage), cost


def _attempt_facts(result, identities, new_identities):
    """Only reserved global attempt IDs can own usage/cost facts.

    Legacy top-level usage/cost describes the final attempt of this invocation,
    leaving earlier attempts unknown. Explicit aggregate facts retain their
    covered IDs separately; they never invent per-attempt values or a total.
    """
    ledger = result.get('attempt_ledger', [])
    _require(isinstance(ledger, list), 'Invalid per-attempt usage ledger')
    facts, aggregates = {}, []
    for item in ledger:
        _require(isinstance(item, dict), 'Invalid attempt usage entry')
        identity = item.get('attempt_number')
        _require(type(identity) is int and identity in identities and identity not in facts,
            'Usage references an absent or duplicate provider attempt')
        usage, cost = _usage_cost(item)
        facts[identity] = {'attempt_number': identity, 'usage': usage, 'reported_cost': cost}
    usage, cost = _usage_cost(result)
    coverage = result.get('usage_attempt_numbers')
    if coverage is not None:
        _require(isinstance(coverage, list) and coverage and all(type(identity) is int for identity in coverage)
            and len(set(coverage)) == len(coverage) and set(coverage) <= set(identities),
            'Aggregate usage requires actual provider-attempt coverage identities')
        aggregates.append({'attempt_numbers': list(coverage), 'usage': usage, 'reported_cost': cost})
    elif usage is not None or cost is not None:
        _require(bool(new_identities), 'Final usage has no provider attempt in this invocation')
        identity = new_identities[-1]
        _require(identity not in facts, 'Ambiguous final usage and per-attempt ledger')
        facts[identity] = {'attempt_number': identity, 'usage': usage, 'reported_cost': cost}
    return facts, aggregates


def _row(label, case, repeat, scope, execute, frozen, provider_url):
    invalidated = label['metric_key'] in case.get('review', {}).get('invalidated_metrics', [])
    row = {'case_id': label['case_id'], 'metric_key': label['metric_key'], 'repeat': repeat,
        'origin': case['provenance']['origin'], 'model_execution': case['provenance']['model_execution'],
        'expected': label['expected'], 'labeler': deepcopy(label['labeler']), 'disputed': label['disputed'],
        'references': deepcopy(label['references']), 'rationale': label['rationale'],
        'critical': label.get('critical', False), 'invalidated_by_masking': invalidated,
        'usage': [], 'reported_cost': [], 'attempt_ledger': [], 'usage_aggregates': [],
        'attempts': 0, 'latency_seconds': None}
    started = time.monotonic()
    predicted, reason = 'failed', None
    if invalidated:
        detail = case['review'].get('invalidation_reasons', {}).get(label['metric_key'])
        _require(isinstance(detail, str) and detail.strip(), 'Masked metric lacks an independent invalidation reason')
        row['skip_reason_detail'] = detail
        predicted, reason = 'skip', 'masking_invalidates_metric'
    else:
        facts = {}
        with scope.case():
            context = {'budget': scope, 'frozen': deepcopy(frozen), 'provider_url': provider_url}
            for invocation in range(3):
                before = len(scope.identities)
                try:
                    result = execute(deepcopy(case['evidence']), label['metric_key'], context=context)
                    _require(stable_hash(context['frozen']) == stable_hash(frozen),
                        'Executor changed frozen calibration configuration')
                    _require(isinstance(result, dict) and result.get('metric_key') == label['metric_key']
                        and result.get('predicted') in {'good', 'bad', 'skip', 'failed'},
                        'Missing or unknown requested metric result')
                    if 'diagnosis' in result:
                        _require(isinstance(result['diagnosis'], dict), 'Incomplete platform diagnosis')
                        scan_secrets(result['diagnosis'])
                        row['diagnosis'] = deepcopy(result['diagnosis'])
                    predicted = result['predicted']
                    new_identities = scope.identities[before:]
                    reported, aggregates = _attempt_facts(result, scope.identities, new_identities)
                    _require(all(identity not in facts or facts[identity] == item
                        for identity, item in reported.items()), 'Conflicting provider-attempt facts')
                    facts.update(reported)
                    row['usage_aggregates'].extend(aggregates)
                    if predicted == 'skip' and scope.started:
                        predicted, reason = 'failed', 'skip_after_provider_request'
                    elif (scope.refused or scope.stop.is_set()) and scope.started:
                        predicted, reason = 'failed', 'budget_unavailable_after_request'
                    else:
                        _require(predicted not in {'good', 'bad'} or bool(new_identities),
                            'No provider attempt backs semantic prediction')
                        reason = 'execution_failed' if predicted == 'failed' else None
                    break
                except Exception as error:
                    predicted, reason = 'failed', type(error).__name__
                    if scope.refused or scope.stop.is_set():
                        if scope.started == 0:
                            predicted, reason = 'skip', 'budget_unavailable_before_request'
                        break
                    if scope.started >= 3 or invocation == 2:
                        break
            row['attempts'] = scope.started
            row['attempt_ledger'] = [facts.get(identity,
                {'attempt_number': identity, 'usage': None, 'reported_cost': None}) for identity in scope.identities]
            row['usage'] = [item['usage'] for item in row['attempt_ledger']]
            row['reported_cost'] = [item['reported_cost'] for item in row['attempt_ledger']]
    row.update(predicted=predicted, reason=reason, latency_seconds=time.monotonic() - started,
        agreement=predicted == label['expected'],
        false_positive=label['expected'] == 'good' and predicted == 'bad',
        false_negative=label['expected'] == 'bad' and predicted == 'good')
    return row


def run_calibration(package, *, execute, budget, frozen, mode='real', repeats=1,
                    stability_cases=(), core_threshold=.90, provider_url=None, provider_budget=None):
    """Run independent labels through an injected current-platform Trial executor.

    For NVIDIA, create AttemptBudget(budget), pass it to NvidiaProvider, and pass
    that exact same wrapper as provider_budget here. The executor configures the
    platform to use provider.url. Source, probe, retries and Judge retain budget.
    The executor receives evidence and a key, never truth labels or rationales.
    """
    _owned(budget)
    _require(mode in {'real', 'fake'} and type(repeats) is int and 1 <= repeats <= 3,
        'Explicit model mode and one to three repetitions required')
    _require(type(core_threshold) in (int, float) and math.isfinite(core_threshold)
        and 0 < core_threshold <= 1, 'Invalid Core threshold')
    frozen = frozen_config(frozen)
    truth = deepcopy(package['labels'])
    _require(stable_hash(truth) == package['labels_sha256'] and
        package.get('original_labels_sha256', package['labels_sha256']) == package['labels_sha256'],
        'Original frozen labels hash mismatch')
    truth_hash = stable_hash(truth)
    cases = {case['case_id']: case for case in package['cases']}
    _require(len(cases) == len(package['cases']), 'Duplicate case identity')
    for case_id, case in cases.items():
        selected = {**truth, 'labels': [label for label in truth['labels'] if label['case_id'] == case_id]}
        validate_labels(selected, case['evidence'], case_id=case_id, origin=case['provenance']['origin'])
    _require(all(label['case_id'] in cases and label['metric_key'] in frozen['templates'] for label in truth['labels']),
        'Unknown labelled case or missing current template key')
    scope = provider_budget or AttemptBudget(budget)
    _require(isinstance(scope, AttemptBudget) and scope.owner is budget, 'Provider must reuse exclusive budget')
    selected_stability = set(stability_cases)
    _require(selected_stability <= set(cases), 'Unknown stability case')
    rows = []
    for label in truth['labels']:
        count = repeats if label['case_id'] in selected_stability else 1
        for repeat in range(1, count + 1):
            rows.append(_row(label, cases[label['case_id']], repeat, scope, execute, frozen, provider_url))
            _require(stable_hash(package['labels']) == truth_hash and stable_hash(truth) == truth_hash,
                'Truth labels changed during execution')
    first = [row for row in rows if row['repeat'] == 1]
    eligible = [row for row in first if not row['disputed'] and not row['invalidated_by_masking']
        and row['labeler']['kind'] == 'human' and row['expected'] in {'good', 'bad'}
        and row['predicted'] in {'good', 'bad'}]
    core = {}
    for key, template in frozen['templates'].items():
        if template['core']:
            subset = [row for row in eligible if row['metric_key'] == key]
            expected = [row for row in first if row['metric_key'] == key and not row['disputed']
                and not row['invalidated_by_masking'] and row['labeler']['kind'] == 'human'
                and row['expected'] in {'good', 'bad'}]
            accuracy = sum(row['agreement'] for row in subset) / len(subset) if subset and mode == 'real' else None
            core[key] = {'denominator': len(subset), 'labelled_applicable': len(expected), 'accuracy': accuracy,
                'passed': bool(accuracy is not None and accuracy >= core_threshold and len(subset) == len(expected))}
    stability = []
    for case_id in sorted(selected_stability):
        groups = {}
        for row in rows:
            if row['case_id'] == case_id:
                groups.setdefault(row['metric_key'], []).append(row)
        comparable = bool(groups) and all(len(group) == 3 and all(row['predicted'] in {'good', 'bad', 'skip'}
            and not row['disputed'] and not row['invalidated_by_masking'] for row in group) for group in groups.values())
        consistent = comparable and all(len({row['predicted'] for row in group}) == 1 for group in groups.values())
        stability.append({'case_id': case_id, 'comparable': comparable, 'consistent': consistent})
    critical_flip = any(row['critical'] and row['expected'] == 'bad' and row['predicted'] == 'good' for row in rows)
    stability_passed = len(stability) == 5 and sum(row['consistent'] for row in stability) >= 4 and not critical_flip
    summary = {'accuracy_denominator': len(eligible),
        'accuracy': sum(row['agreement'] for row in eligible) / len(eligible) if eligible and mode == 'real' else None,
        'disputed': sum(row['disputed'] for row in first),
        'labelers': {kind: sum(row['labeler']['kind'] == kind for row in first) for kind in ('human', 'agent')},
        'origins': {origin: sum(row['origin'] == origin for row in first) for origin in ('real', 'synthetic', 'counterfactual')},
        'failed': sum(row['predicted'] == 'failed' for row in rows), 'skipped': sum(row['predicted'] == 'skip' for row in rows),
        'false_positive': sum(row['false_positive'] for row in first),
        'false_negative': sum(row['false_negative'] for row in first),
        'usage_unknown': sum(value is None for row in rows for value in row['usage']),
        'cost_unknown': sum(value is None for row in rows for value in row['reported_cost']),
        'coverage': {key: {'good_labels': sum(row['metric_key'] == key and row['expected'] == 'good' for row in first),
            'bad_labels': sum(row['metric_key'] == key and row['expected'] == 'bad' for row in first),
            'skip_labels': sum(row['metric_key'] == key and row['expected'] == 'skip' for row in first)}
            for key in frozen['templates']}}
    return {'schema_version': 1, 'stage': 'executed', 'mode': mode,
        'scope': 'Only this frozen labelled corpus; no universal accuracy claim',
        'labels_sha256': truth_hash, 'original_labels_sha256': truth_hash, 'frozen': frozen, 'frozen_sha256': stable_hash(frozen),
        'cases': rows, 'summary': summary, 'core': core, 'core_threshold': core_threshold,
        'stability': {'required_cases': 5, 'required_repeats': 3, 'minimum_consistent_cases': 4,
            'cases': stability, 'passed': stability_passed, 'critical_bad_to_good': critical_flip},
        'protocol_valid': bool(rows) and not summary['failed'],
        'passed': bool(mode == 'real' and core and all(value['passed'] for value in core.values())
            and not summary['failed'] and stability_passed)}


def nvidia_calibration(package, *, key, budget, execute_factory, frozen, provider_timeout=300, **kwargs):
    """One forwarding provider for an actual-platform adapter, with no extra quota."""
    _owned(budget)
    _require(isinstance(key, str) and bool(key), 'Missing privately supplied provider credential')
    _require(budget.matches_credential(key),
        'Provider credential does not match exclusive lease')
    scoped = AttemptBudget(budget)
    _require(type(provider_timeout) in (int, float) and math.isfinite(provider_timeout)
        and 0 < provider_timeout <= 86400, 'Finite calibration provider lifetime required')
    with NvidiaProvider(key, scoped, timeout=provider_timeout) as provider:
        return run_calibration(package, execute=execute_factory(provider), budget=budget,
            frozen=frozen, mode='real', provider_url=provider.url, provider_budget=scoped, **kwargs)


def _targets(path):
    _require(path.is_file() and (path.stat().st_mode & 0o777) == 0o600, 'Current targets must be a private 0600 file')
    _outside_git(path)
    value = json.loads(path.read_text())
    targets = [value] if 'endpoint' in value else list(value.values())
    _require(bool(targets), 'No current targets')
    for target in targets:
        endpoint = urlsplit(target.get('endpoint', ''))
        _require(endpoint.scheme == 'http' and endpoint.hostname in {'127.0.0.1', 'localhost', '::1'}
            and not endpoint.username and not endpoint.password and not endpoint.query and not endpoint.fragment,
            'Current targets must use credential-free loopback endpoints')
    return value


def main(argv=None, *, budget=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ('corpus', 'targets', 'config', 'out'):
        parser.add_argument('--' + flag, type=Path, required=True)
    parser.add_argument('--repeats', type=int, choices=(1, 2, 3), default=3)
    args = parser.parse_args(argv)
    try:
        package = load_package(args.corpus)
        frozen = read_frozen_config(args.config)
        targets = _targets(args.targets)
        if budget is not None:
            _owned(budget)
        _require(all(label['metric_key'] in frozen['templates'] for label in package['labels']['labels']),
            'Missing current template key')
        out = _outside_git(args.out)
        out.mkdir(parents=True, exist_ok=False, mode=0o700)
        adapter = frozen.get('adapter')
        if adapter is not None:
            _require(isinstance(adapter, dict) and set(adapter) == {'kind','app','provider_host','stability_cases'}
                and adapter['kind'] == 'core_trial_v1' and adapter['app'] in {'codex','dsh02'}
                and adapter['provider_host'] in {'127.0.0.1','host.orb.internal','host.docker.internal'}
                and isinstance(adapter['stability_cases'], list), 'Invalid explicit Core adapter configuration')
            from core_trial import CoreTrialExecutor, core_binding, transport_timeout
            from judge import create_connection
            from platform_client import PlatformClient
            from run_suite import native_sessions
            for metric_key, template in frozen['templates'].items():
                core_binding(metric_key, template)
            target = targets[adapter['app']]
            key = os.environ.get('NVIDIA_API_KEY')
            _require(bool(key) and not os.environ.get('GITHUB_ACTIONS'), 'Real calibration requires private local credentials')
            sessions = native_sessions.DashboardSessions()
            def factory(provider):
                # No connection test/probe; canonical Core configuration is advisory.
                endpoint = provider.url.replace('127.0.0.1', adapter['provider_host'])
                connection = create_connection(client, name='Frozen Core corpus ' + uuid.uuid4().hex[:12], endpoint=endpoint,
                    model_name=frozen['model'], api_key='diagnostic-loopback-placeholder')
                return CoreTrialExecutor(client, connection_uuid=connection['uuid'], provider=provider)
            with provider_lease(key, budget) as lease, PlatformClient(target['endpoint'], target=target,
                    dashboard_sessions=sessions) as client:
                invocations = sum(args.repeats if label['case_id'] in adapter['stability_cases'] else 1
                                  for label in package['labels']['labels'])
                report = nvidia_calibration(package, key=key, budget=lease, execute_factory=factory,
                    frozen=frozen, repeats=args.repeats, stability_cases=adapter['stability_cases'],
                    provider_timeout=transport_timeout(frozen['parameters']) * max(1, invocations))
            _write(out/'report.json', report)
            return 0 if report['passed'] else 1
        _write(out / 'report.json', {'schema_version': 1, 'stage': 'adapter_pending', 'passed': False,
            'provider_requests': 0, 'labels_sha256': package['labels_sha256'],
            'frozen': frozen, 'frozen_sha256': stable_hash(frozen),
            'pending': 'Current generic template catalogue and Trial request/result DTO adapter'})
    except (CalibrationError, CorpusError, OSError, ValueError, TypeError, KeyError, AttributeError):
        parser.exit(1, 'Calibration rejected; inspect private corpus, configuration and ownership contracts.\n')
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
