"""Owned Quick diagnosis and retest over the current platform API.

This adapter reports persisted facts. It never interprets a changed score as
proof that an application was repaired; that requires independent verification.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from copy import deepcopy

import httpx


class QuickError(RuntimeError):
    pass


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
        ensure_ascii=False, allow_nan=False).encode('utf-8')).hexdigest()


def _require(condition, message):
    if not condition:
        raise QuickError(message)


def _trace_ids(values):
    _require(isinstance(values, list) and 1 <= len(values) <= 10,
        'Select one to ten complete traces')
    _require(all(isinstance(v, str) and re.fullmatch(r'[A-Za-z0-9._:-]{1,128}', v)
        for v in values) and len(values) == len(set(values)), 'Invalid or duplicate trace ID')
    return list(values)


def _uuid(value):
    try:
        return str(uuid.UUID(value)) == value.lower()
    except (TypeError, ValueError, AttributeError):
        return False


def _resource_uuid(value):
    """Accept persisted hex UUIDs and historical hyphenated UUIDs verbatim."""
    return _uuid(value) or (
        isinstance(value, str) and re.fullmatch(r'[0-9a-fA-F]{32}', value) is not None)


def _selected_options(options, scene, keys, model):
    _require(isinstance(options, dict) and options.get('can_execute') is True,
        'Quick execution is unavailable for this owned Agent')
    scenes = {row['scene_key']: row for row in options.get('scenes', [])}
    _require(scene in scenes, 'Scene unavailable')
    rows = {(row.get('key'), row.get('variant')): row for row in options.get('templates', [])}
    _require(keys and len(keys) <= 9 and len(keys) == len(set(keys))
        and all(isinstance(key, str) for key in keys),
        'Selected template unavailable')
    selected = []
    for key in keys:
        candidates = [row for (item_key, _), row in rows.items() if item_key == key]
        default = scenes[scene].get('default_core') or {}
        if key == 'task_completion':
            candidates = [row for row in candidates if row.get('variant') == default.get('variant')]
        _require(len(candidates) == 1, 'Selected template unavailable or ambiguous')
        selected.append(candidates[0])
    _require(all(re.fullmatch(r'[0-9a-f]{64}', row.get('definition_digest', ''))
        for row in selected), 'Catalog digest unavailable')
    core = [row for row in selected if row.get('mode') == 'llm']
    _require(len(core) <= 1 and all(row['key'] in scenes[scene].get('allowed_core_keys', [])
        for row in core), 'Core selection exceeds scene contract')
    _require(all(row.get('mode') == 'llm' or row['key'] in scenes[scene].get('rule_keys', [])
        for row in selected), 'Rule selection exceeds scene contract')
    if core:
        models = {row.get('uuid'): row for row in options.get('model_connections', [])}
        _require(model in models and models[model].get('selectable') is True,
            'Selected model is unavailable')
    else:
        _require(model is None, 'Rule-only Quick must omit a model')
    return [{'key': row['key'], 'variant': row.get('variant'),
        'version': row['version'], 'definition_digest': row['definition_digest'],
        'score_role': row['score_role']} for row in selected]


def _safe_counts(task):
    stats = task.get('stats')
    _require(isinstance(stats, dict) and stats.get('schema_version') == 3,
        'Task has no v3 frozen statistics')
    definitions = stats.get('diagnoser_definitions')
    counters = stats.get('diagnosers')
    _require(isinstance(definitions, dict) and isinstance(counters, dict),
        'Frozen definitions or counters unavailable')
    result = {'known': 0, 'unknown': 0, 'partial': 0, 'failed': 0, 'skipped': 0}
    for definition in definitions.values():
        _require(definition.get('score_role') in ('quality', 'signal')
            and re.fullmatch(r'[0-9a-f]{64}', definition.get('digest', '')),
            'Unsafe frozen definition')
    for by_metric in counters.values():
        _require(isinstance(by_metric, dict), 'Invalid metric counters')
        for value in by_metric.values():
            _require(isinstance(value, dict) and all(type(value.get(k, 0)) is int
                and value.get(k, 0) >= 0 for k in ('good', 'bad', 'failed', 'skipped')),
                'Invalid metric counter')
            result['known'] += value.get('good', 0) + value.get('bad', 0)
            result['failed'] += value.get('failed', 0)
            result['skipped'] += value.get('skipped', 0)
            if value.get('failed', 0) or value.get('skipped', 0):
                result['partial'] += 1
    source = stats.get('source') or {}
    result['unknown'] = source.get('invalid', 0) + source.get('projection_failed', 0)
    _require(type(result['unknown']) is int and result['unknown'] >= 0,
        'Invalid source counts')
    return result


def _frozen_selection(task, selected):
    definitions = task['stats']['diagnoser_definitions']
    frozen = list(definitions.values())
    _require(len(frozen) == len(selected) and all(
        value.get('uuid') == key for key, value in definitions.items()),
        'Accepted Task has extra or ambiguous frozen definitions')
    matched = []
    for row in selected:
        matches = [entry for entry in frozen if
            entry.get('builtin_key', entry.get('key')) == row['key']
            and entry.get('version') == row['version']
            and entry.get('score_role') == row['score_role']
            and entry.get('canonical_variant') == row['variant']]
        _require(len(matches) == 1, 'Accepted Task changed selected frozen definition')
        matched.append(matches[0]['uuid'])
    _require(len(set(matched)) == len(frozen), 'Frozen definition identity reused')


def _task_identity(task, receipt, *, agent_uuid, trace_ids):
    metadata = task.get('quick_run') or {}
    _require(task.get('uuid') == receipt.get('task_uuid')
        and task.get('job_uuid') == receipt.get('job_uuid')
        and metadata.get('request_id') == receipt.get('request_id')
        and metadata.get('agent_uuid') == agent_uuid
        and metadata.get('trace_ids') == trace_ids,
        'Quick Task does not match accepted owner, request, job and selection')


def _safe_ledger(task):
    state = task.get('judge_request_summary_state')
    value = task.get('judge_request_summary')
    _require(state in ('not_enabled', 'available', 'unavailable'),
        'Judge request summary state unavailable')
    if state != 'available':
        _require(value is None, 'Judge request summary conflicts with state')
        return {'state': state, 'summary': None}
    _require(isinstance(value, dict), 'Judge request summary missing')
    integer_fields = ('reserved_slots', 'response_confirmed_attempts',
        'unknown_attempts', 'request_error_attempts', 'total_cap', 'per_case_cap')
    _require(all(type(value.get(k)) is int and value[k] >= 0 for k in integer_fields)
        and value['response_confirmed_attempts'] + value['unknown_attempts'] ==
            value['reserved_slots']
        and value['request_error_attempts'] <= value['unknown_attempts']
        and value['reserved_slots'] <= value['total_cap'],
        'Judge request counts inconsistent')
    for key in ('input_tokens', 'output_tokens', 'total_tokens'):
        count = (value.get('usage_known_attempts') or {}).get(key)
        known = (value.get('known_token_totals') or {}).get(key)
        aggregate = value.get(key)
        _require(type(count) is int and 0 <= count <= value['reserved_slots']
            and type(known) is int and known >= 0
            and (aggregate is None or type(aggregate) is int and aggregate >= 0)
            and (count == value['reserved_slots'] and value['reserved_slots'] > 0
                and aggregate == known if aggregate is not None else True),
            'Judge token coverage inconsistent')
    return {'state': state, 'summary': deepcopy(value)}


class QuickRunner:
    def __init__(self, client, *, clock=time.monotonic, pause=time.sleep):
        self.client, self.clock, self.pause = client, clock, pause

    def options(self):
        return self.client.scoped('/quick-runs/options')

    def submit(self, body, *, timeout=120):
        """Replay one immutable request ID and body through provisioning/503."""
        _require(_uuid(body.get('request_id')), 'Quick request ID must be a UUID')
        frozen = deepcopy(body)
        deadline = self.clock() + timeout
        while True:
            try:
                response = self.client.scoped('/quick-runs', 'POST', body=deepcopy(frozen))
            except (RuntimeError, httpx.TransportError) as error:
                if (not isinstance(error, httpx.TransportError)
                        and getattr(error, 'http_status', None) != 503) or self.clock() >= deadline:
                    raise
                response = {'state': 'provisioning', 'request_id': body['request_id'],
                    'retry_after_seconds': 2}
            _require(response.get('request_id') == body['request_id'],
                'Quick receipt identity changed')
            if response.get('state') == 'accepted':
                _require(all(_resource_uuid(response.get(k)) for k in ('dataset_uuid', 'job_uuid', 'task_uuid')),
                    'Accepted receipt lacks persisted IDs')
                return response
            _require(response.get('state') == 'provisioning', 'Unexpected Quick receipt state')
            wait = response.get('retry_after_seconds', 2)
            _require(type(wait) in (int, float) and 0 < wait <= 30,
                'Invalid provisioning retry interval')
            _require(self.clock() + wait < deadline, 'Quick provisioning deadline expired')
            self.pause(wait)

    def task(self, receipt, *, timeout=300):
        task_id = receipt['task_uuid']
        deadline = self.clock() + timeout
        while True:
            task = self.client.scoped('/tasks/' + task_id, agent=False)
            _require(task.get('uuid') == task_id, 'Quick task identity changed')
            if task.get('status') in ('succeeded', 'failed', 'canceled'):
                return task
            _require(self.clock() + 1 < deadline, 'Quick task deadline expired')
            self.pause(1)

    def snapshot(self, receipt, task):
        job_id, task_id = receipt['job_uuid'], receipt['task_uuid']
        _require(task.get('uuid') == task_id and task.get('job_uuid') == job_id,
            'Snapshot Task does not match accepted receipt')
        agent_uuid = self.client.target.get('agent_uuid')
        _require(isinstance(agent_uuid, str) and agent_uuid,
            'Exact Stats point requires the current owned Agent')
        # Terminal TaskStats travel through telemetry; flush does not imply query visibility.
        deadline = self.clock() + 120
        while True:
            _require(self.clock() < deadline, 'Quick Stats visibility deadline expired')
            stats = self.client.scoped('/stats', agent=False, params={'job_uuid': job_id,
                'task_uuid': task_id, 'agent_uuid': agent_uuid})
            _require(self.clock() < deadline, 'Quick Stats visibility deadline expired')
            points = stats.get('tasks') if isinstance(stats, dict) else None
            if points != []:
                break
            remaining = deadline - self.clock()
            _require(remaining > 0, 'Quick Stats visibility deadline expired')
            self.pause(min(1, remaining))
        source = task.get('stats', {}).get('source') or {}
        matched = [row for row in points if isinstance(row, dict)
            and row.get('task_uuid') == task_id] if isinstance(points, list) else []
        _require(len(matched) == 1 and len({row.get('task_uuid') for row in points
            if isinstance(row, dict)}) == len(points),
            'Stats have duplicate or missing Task identities')
        point = matched[0]
        _require(point.get('target_count') == source.get('total')
            and point.get('schema_version') == 3
            and point.get('definition_source') == 'task_snapshot'
            and point.get('score_policy_version') == task['stats'].get('score_policy_version')
            and point.get('source_complete') == task['stats'].get('source_complete'),
            'Stats do not describe the accepted Quick Task')
        listing = self.client.scoped('/records', agent=False,
            params={'job_uuid': job_id, 'task_uuid': task_id, 'limit': 100})
        _require(isinstance(listing, dict) and type(listing.get('total')) is int
            and listing['total'] == len(listing.get('items', [])) <= 100,
            'Incomplete Quick record page')
        listed_ids = [row.get('uuid') for row in listing['items']]
        _require(all(isinstance(value, str) and value for value in listed_ids)
            and len(set(listed_ids)) == len(listed_ids), 'Invalid Quick record listing')
        records = [self.client.scoped('/records/' + row_id, agent=False)
            for row_id in listed_ids]
        _require({row.get('uuid') for row in records} == set(listed_ids)
            and all(row.get('task_uuid') == task_id and row.get('job_uuid') == job_id
                and row.get('target_id') in (task.get('quick_run') or {}).get('trace_ids', [])
                for row in records),
            'Quick records cross task boundary')
        counts = _safe_counts(task)
        definitions = task['stats']['diagnoser_definitions']
        for record in records:
            definition = record.get('definition')
            expected = definitions.get(record.get('diagnoser_uuid'))
            _require(record.get('definition_source') == 'task_snapshot'
                and isinstance(expected, dict) and isinstance(definition, dict)
                and definition.get('uuid') == record.get('diagnoser_uuid')
                and definition.get('score_role') == expected.get('score_role')
                and definition.get('digest') == expected.get('digest'),
                'Record lacks matching frozen role or definition')
        raw = {'task': task, 'records': sorted(records, key=lambda row: row['uuid']),
            'stats': point}
        return {'task_uuid': task_id, 'record_uuids': sorted(row['uuid'] for row in records),
            'counts': counts, 'judge_requests': _safe_ledger(task),
            'hashes': {key: canonical_hash(value) for key, value in raw.items()}}

    def run(self, trace_ids, keys, *, scene='quick_runtime', model=None,
            request_id=None, timeout=300):
        selected = _selected_options(self.options(), scene, keys, model)
        body = {'request_id': request_id or str(uuid.uuid4()),
            'trace_ids': sorted(_trace_ids(trace_ids)), 'builtin_keys': sorted(row['key'] for row in selected),
            'scene_key': scene}
        if model is not None:
            body['model_connection_uuid'] = model
        receipt = self.submit(body)
        task = self.task(receipt, timeout=timeout)
        _require(task.get('status') == 'succeeded', 'Quick task did not succeed')
        _task_identity(task, receipt, agent_uuid=self.client.target.get('agent_uuid'),
            trace_ids=body['trace_ids'])
        _frozen_selection(task, selected)
        return {'receipt': receipt, 'selected_definitions': selected,
            'snapshot': self.snapshot(receipt, task)}

    def retest(self, before_receipt, new_trace_ids, *, verification, request_id=None,
            timeout=300):
        _require(isinstance(verification, dict) and verification.get('verified') is True
            and isinstance(verification.get('commands'), list) and verification['commands']
            and isinstance(verification.get('materials'), list) and verification['materials']
            and isinstance(verification.get('provenance'), str) and verification['provenance'],
            'Independent application verification is required')
        original = self.client.scoped('/tasks/' + before_receipt['task_uuid'], agent=False)
        _task_identity(original, before_receipt,
            agent_uuid=self.client.target.get('agent_uuid'),
            trace_ids=(original.get('quick_run') or {}).get('trace_ids'))
        _require(original.get('uuid') == before_receipt['task_uuid'] and
            (original.get('quick_run') or {}).get('can_retest') is True,
            'Original Quick task cannot be retested')
        before = self.snapshot(before_receipt, original)
        body = {'request_id': request_id or str(uuid.uuid4()),
            'trace_ids': sorted(_trace_ids(new_trace_ids)), 'before_task_uuid': before_receipt['task_uuid']}
        receipt = self.submit(body)
        _require(receipt['task_uuid'] != before_receipt['task_uuid'],
            'Retest reused original task')
        task = self.task(receipt, timeout=timeout)
        _require(task.get('status') == 'succeeded', 'Retest task did not succeed')
        _task_identity(task, receipt, agent_uuid=self.client.target.get('agent_uuid'),
            trace_ids=body['trace_ids'])
        comparison = (task.get('quick_run') or {}).get('comparison') or {}
        prior = set((original.get('quick_run') or {}).get('trace_ids') or [])
        incoming = set(body['trace_ids'])
        expected = ('rejudgment' if incoming == prior else 'new_traces'
            if not incoming & prior else 'mixed')
        _require(comparison.get('before_task_uuid') == original['uuid']
            and comparison.get('kind') == expected
            and comparison.get('checking_conditions') == 'same'
            and comparison.get('sampling_conditions') ==
                ('same_selection' if expected == 'rejudgment' else 'changed_selection')
            and set(comparison.get('before_trace_ids') or []) == prior
            and comparison.get('overlap_count') == len(incoming & prior)
            and comparison.get('new_count') == len(incoming - prior)
            and comparison.get('before_count') == len(prior),
            'Retest comparison or frozen conditions changed')
        _require(task.get('stats', {}).get('diagnoser_definitions') ==
            original.get('stats', {}).get('diagnoser_definitions')
            and task.get('stats', {}).get('score_policy_version') ==
            original.get('stats', {}).get('score_policy_version'),
            'Retest changed frozen checking definitions')
        after = self.snapshot(receipt, task)
        original_again = self.client.scoped('/tasks/' + original['uuid'], agent=False)
        before_again = self.snapshot(before_receipt, original_again)
        _require(before_again['hashes'] == before['hashes'], 'Original Quick evidence mutated')
        return {'receipt': receipt, 'before': before, 'after': after,
            'comparison': comparison, 'independent_verification': deepcopy(verification),
            'application_repair_proven': False}
