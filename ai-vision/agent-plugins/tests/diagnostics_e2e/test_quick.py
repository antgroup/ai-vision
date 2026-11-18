"""Quick HTTP intent and immutable evidence contracts, using public fixtures."""
import copy
import unittest
import uuid

import httpx

from quick import QuickError, QuickRunner

DIGEST = 'a' * 64


def uid():
    return str(uuid.uuid4())


class Client:
    def __init__(self):
        self.target = {'agent_uuid': 'agent'}
        self.calls = []
        self.queries = []
        self.receipt = {'state': 'accepted', 'request_id': uid(), 'dataset_uuid': uid(),
            'job_uuid': uid(), 'task_uuid': uid()}
        self.task_row = {'uuid': self.receipt['task_uuid'],
            'job_uuid': self.receipt['job_uuid'], 'status': 'succeeded',
            'judge_request_summary_state': 'not_enabled', 'judge_request_summary': None,
            'quick_run': {'request_id': self.receipt['request_id'],
                'agent_uuid': 'agent', 'trace_ids': ['trace-1'],
                'can_retest': True},
            'stats': {'schema_version': 3, 'source_complete': True,
                'score_policy_version': 'lightweight-v1',
                'source': {'total': 1, 'invalid': 0,
                'projection_failed': 0}, 'diagnoser_definitions': {
                    'rule-1': {'uuid': 'rule-1', 'key': 'rule_one', 'version': '1',
                        'score_role': 'signal', 'digest': DIGEST}},
                'diagnosers': {'rule-1': {'metric': {'total': 1, 'good': 0,
                    'bad': 0, 'failed': 0, 'skipped': 1}}}}}
        self.record_row = {'uuid': 'record-1', 'task_uuid': self.receipt['task_uuid'],
            'job_uuid': self.receipt['job_uuid'], 'target_id': 'trace-1',
            'diagnoser_uuid': 'rule-1',
            'definition_source': 'task_snapshot',
            'definition': {'uuid': 'rule-1', 'score_role': 'signal', 'digest': DIGEST}}
        self.options_row = {'can_execute': True,
            'scenes': [{'scene_key': 'quick_runtime', 'rule_keys': ['rule_one'],
                'allowed_core_keys': [], 'default_core': None}],
            'templates': [{'key': 'rule_one', 'variant': None, 'version': '1',
                'definition_digest': DIGEST, 'score_role': 'signal', 'mode': 'rule'}],
            'model_connections': []}

    def scoped(self, path, method='GET', *, body=None, params=None, agent=True):
        self.calls.append((method, path, copy.deepcopy(body)))
        self.queries.append((path, copy.deepcopy(params), agent))
        if path == '/quick-runs/options':
            return copy.deepcopy(self.options_row)
        if path == '/quick-runs':
            self.receipt['request_id'] = body['request_id']
            self.task_row['quick_run']['request_id'] = body['request_id']
            return copy.deepcopy(self.receipt)
        if path.startswith('/tasks/'):
            return copy.deepcopy(self.task_row)
        if path == '/records':
            return {'total': 1, 'items': [{'uuid': 'record-1'}]}
        if path == '/records/record-1':
            return copy.deepcopy(self.record_row)
        if path == '/stats':
            return {'tasks': [{'task_uuid': self.receipt['task_uuid'],
                'target_count': 1, 'schema_version': 3,
                'definition_source': 'task_snapshot',
                'score_policy_version': 'lightweight-v1',
                'source_complete': True}]}
        raise AssertionError(path)


class PersistedClient(Client):
    """HTTP boundary with two persisted Tasks and separately addressed Records."""

    def __init__(self, before_ids, after_ids):
        super().__init__()
        self.receipt.update(zip(('dataset_uuid', 'job_uuid', 'task_uuid'), before_ids))
        self.after_receipt = {**self.receipt, **dict(zip(
            ('dataset_uuid', 'job_uuid', 'task_uuid'), after_ids))}
        self.tasks = {}
        self.records = {}
        self.polls = {}

    def scoped(self, path, method='GET', *, body=None, params=None, agent=True):
        if path == '/quick-runs/options':
            return super().scoped(path, method, body=body, params=params, agent=agent)
        self.calls.append((method, path, copy.deepcopy(body)))
        self.queries.append((path, copy.deepcopy(params), agent))
        if path == '/quick-runs':
            receipt = copy.deepcopy(self.after_receipt if 'before_task_uuid' in body
                else self.receipt)
            receipt['request_id'] = body['request_id']
            task = copy.deepcopy(self.task_row)
            task.update(uuid=receipt['task_uuid'], job_uuid=receipt['job_uuid'])
            task['quick_run'].update(request_id=body['request_id'], trace_ids=body['trace_ids'])
            if 'before_task_uuid' in body:
                if body['before_task_uuid'] != self.receipt['task_uuid']:
                    raise AssertionError('Retest changed the persisted before ID')
                task['quick_run']['comparison'] = {
                    'before_task_uuid': self.receipt['task_uuid'],
                    'before_trace_ids': ['trace-1'], 'kind': 'new_traces',
                    'checking_conditions': 'same', 'sampling_conditions': 'changed_selection',
                    'overlap_count': 0, 'new_count': 1, 'before_count': 1}
            self.tasks[task['uuid']] = task
            record = copy.deepcopy(self.record_row)
            record.update(uuid='record-' + task['uuid'], task_uuid=task['uuid'],
                job_uuid=task['job_uuid'], target_id=body['trace_ids'][0])
            self.records[record['uuid']] = record
            return receipt
        if path.startswith('/tasks/'):
            task_id = path.removeprefix('/tasks/')
            result = copy.deepcopy(self.tasks[task_id])
            self.polls[task_id] = self.polls.get(task_id, 0) + 1
            if self.polls[task_id] == 1:
                result['status'] = 'running'
            return result
        if path in ('/records', '/stats'):
            task = self.tasks[params['task_uuid']]
            if params['job_uuid'] != task['job_uuid'] or agent is not False:
                raise AssertionError('Query changed persisted Task/Job scope')
            if path == '/records':
                return {'total': 1, 'items': [{'uuid': 'record-' + task['uuid']}]}
            if params['agent_uuid'] != 'agent':
                raise AssertionError('Stats changed owned Agent scope')
            return {'tasks': [{'task_uuid': task['uuid'], 'score': None,
                'target_count': 1, 'schema_version': 3, 'definition_source': 'task_snapshot',
                'score_policy_version': 'lightweight-v1', 'source_complete': True}]}
        if path.startswith('/records/'):
            return copy.deepcopy(self.records[path.removeprefix('/records/')])
        raise AssertionError(path)


class QuickTests(unittest.TestCase):
    def test_persisted_hex_and_hyphenated_ids_survive_run_poll_snapshot_and_retest(self):
        formats = (
            (('1' * 32, '2' * 32, '3' * 32), ('4' * 32, '5' * 32, '6' * 32)),
            (('11111111-1111-1111-1111-111111111111',
              '22222222-2222-2222-2222-222222222222',
              '33333333-3333-3333-3333-333333333333'),
             ('44444444-4444-4444-4444-444444444444',
              '55555555-5555-5555-5555-555555555555',
              '66666666-6666-6666-6666-666666666666')),
        )
        for before_ids, after_ids in formats:
            with self.subTest(before_ids=before_ids):
                client = PersistedClient(before_ids, after_ids)
                runner = QuickRunner(client, clock=lambda: 0, pause=lambda _: None)
                try:
                    before = runner.run(['trace-1'], ['rule_one'])
                    old_task = copy.deepcopy(client.tasks[before_ids[2]])
                    proof = {'verified': True, 'commands': ['pytest tests/public.py'],
                        'materials': ['synthetic verification'], 'provenance': 'public fixture'}
                    result = runner.retest(before['receipt'], ['trace-2'], verification=proof)
                except QuickError as error:
                    self.fail(str(error))
                keys = ('dataset_uuid', 'job_uuid', 'task_uuid')
                self.assertEqual(tuple(before['receipt'][key] for key in keys), before_ids)
                self.assertEqual(tuple(result['receipt'][key] for key in keys), after_ids)
                self.assertEqual(before['snapshot']['task_uuid'], before_ids[2])
                self.assertEqual(result['after']['task_uuid'], after_ids[2])
                self.assertEqual(result['before']['hashes'], before['snapshot']['hashes'])
                self.assertEqual(client.tasks[before_ids[2]], old_task)
                self.assertEqual(result['comparison']['before_task_uuid'], before_ids[2])
                self.assertEqual(result['comparison']['kind'], 'new_traces')
                self.assertEqual({path for _, path, _ in client.calls if path.startswith('/tasks/')},
                    {'/tasks/' + before_ids[2], '/tasks/' + after_ids[2]})
                self.assertGreaterEqual(client.polls[before_ids[2]], 2)
                self.assertGreaterEqual(client.polls[after_ids[2]], 2)
                posts = [body for method, path, body in client.calls
                    if method == 'POST' and path == '/quick-runs']
                self.assertEqual(set(posts[1]), {'request_id', 'trace_ids', 'before_task_uuid'})
                self.assertEqual(posts[1]['before_task_uuid'], before_ids[2])
                self.assertEqual(result['after']['counts']['skipped'], 1)
                self.assertEqual(result['after']['counts']['known'], 0)
                self.assertNotIn('score', result['after'])
                self.assertFalse(result['application_repair_proven'])

    def test_resource_id_formats_can_be_mixed_without_normalization(self):
        client = Client()
        client.receipt.update(dataset_uuid='ABCDEF0123456789ABCDEF0123456789',
            job_uuid='22222222-2222-2222-2222-222222222222', task_uuid='3' * 32)
        try:
            receipt = QuickRunner(client).submit({'request_id': uid()})
        except QuickError as error:
            self.fail(str(error))
        self.assertEqual(receipt['dataset_uuid'], 'ABCDEF0123456789ABCDEF0123456789')
        self.assertEqual(receipt['job_uuid'], '22222222-2222-2222-2222-222222222222')
        self.assertEqual(receipt['task_uuid'], '3' * 32)

    def test_invalid_persisted_resource_ids_are_rejected_before_polling(self):
        invalid = (None, True, 1, uuid.UUID(int=1), '', '1' * 31, '1' * 33,
            'g' * 32, ' ' + '1' * 32, '1' * 32 + '\n',
            '{11111111-1111-1111-1111-111111111111}',
            'urn:uuid:11111111-1111-1111-1111-111111111111',
            '111111111-111-1111-1111-111111111111',
            '11111111-1111-1111-1111-111111111111 ')
        for key in ('dataset_uuid', 'job_uuid', 'task_uuid'):
            for value in invalid:
                with self.subTest(key=key, value=value):
                    client = Client()
                    client.receipt[key] = value
                    with self.assertRaisesRegex(QuickError, 'Accepted receipt lacks persisted IDs'):
                        QuickRunner(client).run(['trace-1'], ['rule_one'])
                    self.assertFalse(any(path.startswith('/tasks/') for _, path, _ in client.calls))

    def test_hex_request_id_and_malformed_request_ids_rejected_before_write(self):
        for request_id in ('1' * 32, None, True, 1, '',
                ' 11111111-1111-1111-1111-111111111111',
                '11111111-1111-1111-1111-111111111111\n',
                '{11111111-1111-1111-1111-111111111111}'):
            with self.subTest(request_id=request_id):
                client = Client()
                with self.assertRaisesRegex(QuickError, 'Quick request ID must be a UUID'):
                    QuickRunner(client).submit({'request_id': request_id})
                self.assertEqual(client.calls, [])

    def test_exact_stats_point_requires_current_owned_agent_selector(self):
        client = Client()
        QuickRunner(client).snapshot(client.receipt, client.task_row)
        query = next((params, agent) for path, params, agent in client.queries
            if path == '/stats')
        self.assertEqual(query, ({'job_uuid': client.receipt['job_uuid'],
            'task_uuid': client.receipt['task_uuid'], 'agent_uuid': 'agent'}, False))
        client.target.pop('agent_uuid')
        before = len([path for path, *_ in client.queries if path == '/stats'])
        with self.assertRaises(QuickError):
            QuickRunner(client).snapshot(client.receipt, client.task_row)
        self.assertEqual(len([path for path, *_ in client.queries if path == '/stats']), before)

    def test_stats_hash_is_for_accepted_task_not_mutable_job_rollup(self):
        client = Client()
        runner = QuickRunner(client)
        before = runner.snapshot(client.receipt, client.task_row)
        original = client.scoped
        def scoped(path, method='GET', **kwargs):
            response = original(path, method, **kwargs)
            if path == '/stats':
                response['tasks'].append({'task_uuid': 'later-retest-task',
                    'target_count': 9})
            return response
        client.scoped = scoped
        after = runner.snapshot(client.receipt, client.task_row)
        self.assertEqual(before['hashes'], after['hashes'])

    def test_provisioning_and_503_retry_exact_same_intent(self):
        client = Client()
        bodies = []
        original = client.scoped

        def scoped(path, method='GET', **kwargs):
            if path == '/quick-runs':
                bodies.append(copy.deepcopy(kwargs['body']))
                if len(bodies) == 1:
                    return {'state': 'provisioning', 'request_id': bodies[0]['request_id'],
                        'retry_after_seconds': 2}
                if len(bodies) == 2:
                    error = RuntimeError('unavailable')
                    error.http_status = 503
                    raise error
            return original(path, method, **kwargs)

        client.scoped = scoped
        runner = QuickRunner(client, clock=lambda: 0, pause=lambda _: None)
        body = {'request_id': uid(), 'trace_ids': ['trace-1'], 'builtin_keys': ['rule_one']}
        accepted = runner.submit(body)
        self.assertEqual(accepted['task_uuid'], client.receipt['task_uuid'])
        self.assertEqual(bodies, [body, body, body])

    def test_conflict_terminal_and_no_new_request_id(self):
        client = Client()
        def conflict(*_args, **_kwargs):
            error = RuntimeError('conflict')
            error.http_status = 409
            raise error
        client.scoped = conflict
        with self.assertRaises(RuntimeError):
            QuickRunner(client).submit({'request_id': uid(), 'trace_ids': ['trace-1']})

    def test_lost_response_replays_same_body(self):
        client = Client()
        original = client.scoped
        bodies = []
        def scoped(path, method='GET', **kwargs):
            if path == '/quick-runs':
                bodies.append(copy.deepcopy(kwargs['body']))
                if len(bodies) == 1:
                    raise httpx.ReadError('connection closed after submit')
            return original(path, method, **kwargs)
        client.scoped = scoped
        body = {'request_id': uid(), 'trace_ids': ['trace-1'], 'builtin_keys': ['rule_one']}
        QuickRunner(client, clock=lambda: 0, pause=lambda _: None).submit(body)
        self.assertEqual(bodies, [body, body])

    def test_unrelated_receipt_task_and_stats_are_rejected(self):
        for mutation in ('job', 'request', 'agent', 'trace', 'stats'):
            with self.subTest(mutation=mutation):
                client = Client()
                original = client.scoped
                if mutation == 'job':
                    client.task_row['job_uuid'] = uid()
                elif mutation == 'request':
                    def scoped(path, *args, original=original, **kwargs):
                        result = original(path, *args, **kwargs)
                        if path.startswith('/tasks/'):
                            result['quick_run']['request_id'] = uid()
                        return result
                    client.scoped = scoped
                elif mutation == 'agent':
                    client.task_row['quick_run']['agent_uuid'] = 'other-agent'
                elif mutation == 'trace':
                    client.task_row['quick_run']['trace_ids'] = ['other-trace']
                else:
                    def scoped(path, *args, original=original, **kwargs):
                        result = original(path, *args, **kwargs)
                        if path == '/stats':
                            result['tasks'][0]['task_uuid'] = uid()
                        return result
                    client.scoped = scoped
                with self.assertRaises(QuickError):
                    QuickRunner(client).run(['trace-1'], ['rule_one'])

    def test_extra_frozen_definition_and_wrong_record_digest_rejected(self):
        for mutation in ('extra_definition', 'wrong_record'):
            with self.subTest(mutation=mutation):
                client = Client()
                if mutation == 'extra_definition':
                    client.task_row['stats']['diagnoser_definitions']['extra'] = {
                        'uuid': 'extra', 'key': 'unexpected', 'version': '1',
                        'score_role': 'signal',
                        'digest': 'b' * 64}
                else:
                    client.record_row['definition']['digest'] = 'b' * 64
                with self.assertRaises(QuickError):
                    QuickRunner(client).run(['trace-1'], ['rule_one'])

    def test_rule_outside_scene_is_rejected_before_post(self):
        client = Client()
        client.options_row['templates'].append({'key': 'wrong_rule', 'variant': None,
            'version': '1', 'definition_digest': DIGEST, 'score_role': 'signal', 'mode': 'rule'})
        with self.assertRaises(QuickError):
            QuickRunner(client).run(['trace-1'], ['wrong_rule'])
        self.assertFalse(any(path == '/quick-runs' for _, path, _ in client.calls))

    def test_skip_count_and_frozen_identity_do_not_invent_score(self):
        client = Client()
        result = QuickRunner(client).run(['trace-1'], ['rule_one'])
        self.assertEqual(result['snapshot']['counts']['skipped'], 1)
        self.assertEqual(result['snapshot']['counts']['known'], 0)
        self.assertNotIn('score', result['snapshot'])
        self.assertEqual(result['receipt']['task_uuid'], client.receipt['task_uuid'])

    def test_ledger_preserves_known_zero_and_unknown_usage(self):
        client = Client()
        client.task_row['judge_request_summary_state'] = 'available'
        client.task_row['judge_request_summary'] = {
            'reserved_slots': 2, 'response_confirmed_attempts': 1,
            'unknown_attempts': 1, 'request_error_attempts': 1,
            'total_cap': 30, 'per_case_cap': 3, 'input_tokens': None,
            'output_tokens': None, 'total_tokens': None,
            'usage_known_attempts': {'input_tokens': 1, 'output_tokens': 1,
                'total_tokens': 1},
            'known_token_totals': {'input_tokens': 0, 'output_tokens': 0,
                'total_tokens': 0}}
        result = QuickRunner(client).run(['trace-1'], ['rule_one'])
        ledger = result['snapshot']['judge_requests']['summary']
        self.assertEqual(ledger['known_token_totals']['input_tokens'], 0)
        self.assertIsNone(ledger['input_tokens'])
        self.assertEqual(ledger['unknown_attempts'], 1)

    def test_retest_requires_independent_verification_before_write(self):
        client = Client()
        with self.assertRaises(QuickError):
            QuickRunner(client).retest(client.receipt, ['trace-2'], verification={})
        self.assertFalse(any(path == '/quick-runs' for _, path, _ in client.calls))

    def test_retest_preserves_original_and_posts_before_only(self):
        client = Client()
        before_receipt = copy.deepcopy(client.receipt)
        before_task = copy.deepcopy(client.task_row)
        after_receipt = {**before_receipt, 'request_id': '', 'dataset_uuid': uid(),
            'job_uuid': uid(), 'task_uuid': uid()}
        after_task = copy.deepcopy(before_task)
        after_task['uuid'] = after_receipt['task_uuid']
        after_task['job_uuid'] = after_receipt['job_uuid']
        after_task['quick_run'] = {'request_id': '', 'agent_uuid': 'agent',
            'trace_ids': ['trace-2'], 'comparison': {
            'before_task_uuid': before_task['uuid'], 'before_trace_ids': ['trace-1'],
            'kind': 'new_traces', 'checking_conditions': 'same',
            'sampling_conditions': 'changed_selection', 'overlap_count': 0,
            'new_count': 1, 'before_count': 1}}
        def scoped(path, method='GET', *, body=None, params=None, agent=True):
            client.calls.append((method, path, copy.deepcopy(body)))
            if path == '/quick-runs':
                after_receipt['request_id'] = body['request_id']
                after_task['quick_run']['request_id'] = body['request_id']
                return copy.deepcopy(after_receipt)
            if path.startswith('/tasks/'):
                return copy.deepcopy(before_task if path.endswith(before_task['uuid']) else after_task)
            if path == '/records':
                return {'total': 1, 'items': [{'uuid': 'record-1'}]}
            if path == '/records/record-1':
                row = copy.deepcopy(client.record_row)
                # The fixture's record URL is shared; select by the preceding listing.
                row['task_uuid'] = client.last_listed_task
                row['job_uuid'] = (before_receipt['job_uuid'] if client.last_listed_task ==
                    before_task['uuid'] else after_receipt['job_uuid'])
                row['target_id'] = ('trace-1' if client.last_listed_task == before_task['uuid']
                    else 'trace-2')
                return row
            if path == '/stats':
                return {'tasks': [{'task_uuid': params['task_uuid'], 'score': None,
                    'target_count': 1, 'schema_version': 3,
                    'definition_source': 'task_snapshot',
                    'score_policy_version': 'lightweight-v1', 'source_complete': True}]}
            raise AssertionError(path)
        def wrapped(path, method='GET', *, body=None, params=None, agent=True):
            if path == '/records':
                client.last_listed_task = params['task_uuid']
            return scoped(path, method, body=body, params=params, agent=agent)
        client.scoped = wrapped
        proof = {'verified': True, 'commands': ['pytest tests/public.py'],
            'materials': ['new trace and command output'], 'provenance': 'owned local run'}
        result = QuickRunner(client).retest(before_receipt, ['trace-2'], verification=proof)
        post = [row[2] for row in client.calls if row[1] == '/quick-runs']
        self.assertEqual(len(post), 1)
        self.assertEqual(set(post[0]), {'request_id', 'trace_ids', 'before_task_uuid'})
        self.assertEqual(result['comparison']['kind'], 'new_traces')
        self.assertEqual(result['before']['hashes']['task'],
            QuickRunner(client).snapshot(before_receipt, before_task)['hashes']['task'])
        self.assertFalse(result['application_repair_proven'])


class VisibilityClock:
    def __init__(self):
        self.now = 0
        self.pauses = []

    def clock(self):
        return self.now

    def pause(self, seconds):
        self.pauses.append(seconds)
        self.now += seconds


class StatsVisibilityClient(Client):
    """Read boundary with successful Task and delayed external Stats/Records."""

    def __init__(self, phases, *, clock=None, record_barrier=None):
        super().__init__()
        self.phases = copy.deepcopy(phases)
        self.stats_reads = 0
        self.clock = clock
        self.record_barrier = record_barrier
        self.visible_response = None

    def scoped(self, path, method='GET', *, body=None, params=None, agent=True):
        value = super().scoped(path, method, body=body, params=params, agent=agent)
        if (path == '/records' and self.record_barrier is not None
                and self.stats_reads < self.record_barrier):
            return {'total': 0, 'items': []}
        if path == '/stats':
            phase = self.phases[min(self.stats_reads, len(self.phases) - 1)]
            self.stats_reads += 1
            if isinstance(phase, Exception):
                raise phase
            if phase == 'visible':
                self.visible_response = copy.deepcopy(value)
                return value
            if phase == 'duplicate':
                value['tasks'].append(copy.deepcopy(value['tasks'][0]))
                return value
            if isinstance(phase, tuple) and phase[0] == 'late_visible':
                self.clock.now = phase[1]
                return value
            if isinstance(phase, tuple):
                value['tasks'][0].update(phase[1])
                return value
            return copy.deepcopy(phase)
        return value


class QuickStatsVisibilityTests(unittest.TestCase):
    def snapshot(self, client, clock):
        return QuickRunner(client, clock=clock.clock, pause=clock.pause).snapshot(
            client.receipt, client.task_row)

    def assert_no_records_or_mutations(self, client):
        self.assertFalse(any(path.startswith('/records') for _, path, _ in client.calls))
        self.assertTrue(all(method == 'GET' for method, _, _ in client.calls))

    def test_waits_for_empty_stats_then_snapshots_actual_records_and_frozen_hashes(self):
        from quick import canonical_hash
        clock = VisibilityClock()
        client = StatsVisibilityClient([{'tasks': []}, {'tasks': []}, 'visible'], record_barrier=3)
        original_task = copy.deepcopy(client.task_row)
        try:
            result = self.snapshot(client, clock)
        except QuickError as error:
            self.fail('Delayed exact Stats should become visible: ' + str(error))
        self.assertEqual(client.stats_reads, 3)
        self.assertEqual(clock.pauses, [1, 1])
        self.assertEqual([path for _, path, _ in client.calls],
            ['/stats', '/stats', '/stats', '/records', '/records/record-1'])
        self.assertEqual(result['record_uuids'], ['record-1'])
        self.assertEqual(result['hashes'], {
            'task': canonical_hash(original_task),
            'records': canonical_hash([client.record_row]),
            'stats': canonical_hash(client.visible_response['tasks'][0])})
        self.assertEqual(client.task_row, original_task)
        scoped = [(params, agent) for path, params, agent in client.queries if path == '/stats']
        self.assertEqual(scoped, [({'job_uuid': client.receipt['job_uuid'],
            'task_uuid': client.receipt['task_uuid'], 'agent_uuid': 'agent'}, False)] * 3)
        self.assertTrue(all(method == 'GET' for method, _, _ in client.calls))

    def test_visible_stats_are_read_once_without_pause_or_task_resubmission(self):
        clock = VisibilityClock()
        client = StatsVisibilityClient(['visible'])
        self.snapshot(client, clock)
        self.assertEqual(client.stats_reads, 1)
        self.assertEqual(clock.pauses, [])
        self.assertEqual([path for _, path, _ in client.calls],
            ['/stats', '/records', '/records/record-1'])
        self.assertTrue(all(method == 'GET' for method, _, _ in client.calls))

    def test_permanently_empty_stats_expire_instead_of_hashing_empty_record_snapshot(self):
        clock = VisibilityClock()
        client = StatsVisibilityClient([{'tasks': []}])
        with self.assertRaisesRegex(QuickError, 'Stats visibility deadline expired'):
            self.snapshot(client, clock)
        self.assertEqual(clock.now, 120)
        self.assertEqual(client.stats_reads, 120)
        self.assertEqual(clock.pauses, [1] * 120)
        self.assert_no_records_or_mutations(client)

    def test_duplicate_task_uuid_fails_immediately_without_retry_or_record_read(self):
        clock = VisibilityClock()
        client = StatsVisibilityClient(['duplicate', 'visible'])
        with self.assertRaisesRegex(QuickError, 'Stats have duplicate or missing Task identities'):
            self.snapshot(client, clock)
        self.assertEqual(client.stats_reads, 1)
        self.assertEqual(clock.pauses, [])
        self.assert_no_records_or_mutations(client)

    def test_foreign_only_task_is_not_treated_as_eventual_absence(self):
        clock = VisibilityClock()
        client = StatsVisibilityClient([{'tasks': [{'task_uuid': 'foreign-task'}]}, 'visible'])
        with self.assertRaisesRegex(QuickError, 'Stats have duplicate or missing Task identities'):
            self.snapshot(client, clock)
        self.assertEqual(client.stats_reads, 1)
        self.assertEqual(clock.pauses, [])
        self.assert_no_records_or_mutations(client)

    def test_malformed_envelope_list_or_row_is_not_retried(self):
        cases = [None, [], {}, {'tasks': None}, {'tasks': {}}, {'tasks': ''},
            {'tasks': ()}, {'tasks': [None]}, {'tasks': [{}]}, {'tasks': [True]}]
        for value in cases:
            with self.subTest(value=value):
                clock = VisibilityClock()
                client = StatsVisibilityClient([value, 'visible'])
                with self.assertRaisesRegex(QuickError, 'Stats have duplicate or missing Task identities'):
                    self.snapshot(client, clock)
                self.assertEqual(client.stats_reads, 1)
                self.assertEqual(clock.pauses, [])
                self.assert_no_records_or_mutations(client)

    def test_present_wrong_frozen_metadata_never_waits_for_better_response(self):
        mutations = [{'target_count': 2}, {'schema_version': 2},
            {'definition_source': 'legacy_current_definition'},
            {'score_policy_version': 'changed'}, {'source_complete': False}]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                clock = VisibilityClock()
                client = StatsVisibilityClient([('update', mutation), 'visible'])
                with self.assertRaisesRegex(QuickError, 'Stats do not describe the accepted Quick Task'):
                    self.snapshot(client, clock)
                self.assertEqual(client.stats_reads, 1)
                self.assertEqual(clock.pauses, [])
                self.assert_no_records_or_mutations(client)

    def test_empty_then_duplicate_or_invalid_metadata_is_rejected_before_records(self):
        for response, message in [('duplicate', 'Stats have duplicate or missing Task identities'),
            (('update', {'schema_version': 2}), 'Stats do not describe the accepted Quick Task')]:
            with self.subTest(response=response):
                clock = VisibilityClock()
                client = StatsVisibilityClient([{'tasks': []}, response, 'visible'])
                with self.assertRaisesRegex(QuickError, message):
                    self.snapshot(client, clock)
                self.assertEqual(client.stats_reads, 2)
                self.assertEqual(clock.pauses, [1])
                self.assert_no_records_or_mutations(client)

    def test_http_and_transport_errors_are_not_retried(self):
        for error in [RuntimeError('actual HTTP error'), httpx.ReadError('actual transport error')]:
            with self.subTest(error=type(error).__name__):
                clock = VisibilityClock()
                client = StatsVisibilityClient([error, 'visible'])
                with self.assertRaisesRegex(type(error), str(error)):
                    self.snapshot(client, clock)
                self.assertEqual(client.stats_reads, 1)
                self.assertEqual(clock.pauses, [])
                self.assert_no_records_or_mutations(client)

    def test_correct_response_arriving_at_deadline_does_not_count_as_pass(self):
        for moment in (120, 121):
            for missing_first in (False, True):
                with self.subTest(moment=moment, missing_first=missing_first):
                    clock = VisibilityClock()
                    phases = ([{'tasks': []}] if missing_first else []) + [('late_visible', moment)]
                    client = StatsVisibilityClient(phases, clock=clock)
                    with self.assertRaisesRegex(QuickError, 'Stats visibility deadline expired'):
                        self.snapshot(client, clock)
                    self.assertEqual(client.stats_reads, len(phases))
                    self.assertEqual(clock.pauses, [1] if missing_first else [])
                    self.assert_no_records_or_mutations(client)


if __name__ == '__main__':
    unittest.main()
