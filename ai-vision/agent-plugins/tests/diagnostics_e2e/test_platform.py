"""Negative controls for independent diagnostic acceptance."""
from copy import deepcopy
import unittest
from unittest.mock import MagicMock, patch
import httpx


def world():
    diagnosers = {"tool_success": {"uuid": "dg-tool", "spec": {"metrics": [{"name": "tool_success_rate"}]}}}
    task = {"status": "succeeded", "stats": {"source": {"total": 1}, "diagnosers": {
        "dg-tool": {"tool_success_rate": {"total": 1, "good": 0, "bad": 1, "failed": 0}}
    }}}
    records = [{"uuid": "r-tool", "target_id": "a" * 32, "diagnoser_uuid": "dg-tool",
        "verdict": "bad", "execution": {"status": "succeeded"}, "metrics": [{
            "name": "tool_success_rate", "verdict": "bad", "reason": "Tool failed",
            "evidence": {"bad_spans": [{"span_id": "tool-span", "findings": [{"type": "tool_failure"}]}]}
        }]}]
    return task, records, diagnosers


class SessionTransportTests(unittest.TestCase):
    def test_stats_window_keeps_timezone_and_records_use_server_local_defaults(self):
        from datetime import datetime, timedelta, timezone
        from platform_client import PlatformClient
        calls = []
        def api(request):
            calls.append(request)
            if request.url.path.endswith('/records'):
                return httpx.Response(200, json={'code': 0, 'data': {'total': 1, 'items': [{'uuid': 'record'}]}})
            return httpx.Response(200, json={'code': 0, 'data': {}})
        fixed = datetime(2026, 10, 9, 2, 0, tzinfo=timezone.utc)
        clock = MagicMock()
        clock.now.return_value = fixed
        with PlatformClient('http://127.0.0.1:12345', target={'space_id': 'space'}) as client:
            client.client.close()
            client.client = httpx.Client(base_url=client.endpoint, transport=httpx.MockTransport(api))
            with patch('platform_client.datetime', clock), \
                    patch('platform_client.validate_stats', return_value={'verified': True}):
                client.dashboard_stats({'uuid': 'job'}, {'uuid': 'task'}, {})
                client.records({'uuid': 'job'}, {'uuid': 'task'}, expected_count=1)
        stats = next(request for request in calls if request.url.path.endswith('/stats'))
        self.assertEqual(datetime.fromisoformat(stats.url.params['start_time']), fixed - timedelta(hours=3))
        self.assertEqual(datetime.fromisoformat(stats.url.params['end_time']), fixed + timedelta(minutes=1))
        records = next(request for request in calls if request.url.path.endswith('/records'))
        self.assertEqual(records.url.params['task_uuid'], 'task')
        self.assertEqual(records.url.params['job_uuid'], 'job')
        self.assertNotIn('start_time', records.url.params)
        self.assertNotIn('end_time', records.url.params)

    def test_each_request_renews_rotated_session_before_sending_with_current_csrf(self):
        from platform_client import PlatformClient
        from run_suite import native_sessions
        now, renewals, calls = [0.0], [], []
        def renew(request):
            if request.url.path.endswith('/csrf'):
                return httpx.Response(200, headers={'Set-Cookie': 'csrftoken=csrf-' + str(len(renewals) + 1) + '; Path=/'})
            renewals.append(request.headers['Cookie'])
            number = str(len(renewals))
            return httpx.Response(200, json={'code': 0}, headers=[
                ('Set-Cookie', 'aivision_access=access-' + number + '; Path=/'),
                ('Set-Cookie', 'aivision_refresh=refresh-' + number + '; Path=/api/v1/auth/refresh')])
        http_client = httpx.Client
        sessions = native_sessions.DashboardSessions(clock=lambda: now[0],
            client_factory=lambda: http_client(transport=httpx.MockTransport(renew)))
        def api(request):
            calls.append(request)
            return httpx.Response(200, json={'code': 0, 'data': {}})
        original = {'endpoint': 'http://127.0.0.1:12345',
            'dashboard_headers': {'Cookie': 'aivision_refresh=original; aivision_access=expired'}}
        saved = deepcopy(original)
        transport = httpx.Client(base_url=original['endpoint'], transport=httpx.MockTransport(api))
        with patch('platform_client.httpx.Client', return_value=transport):
            with PlatformClient(original['endpoint'], target=original, dashboard_sessions=sessions) as client:
                client.request('POST', '/first')
                now[0] = 601
                client.request('POST', '/second')
        self.assertEqual(original, saved)
        self.assertEqual(len(calls), 2)
        self.assertIn('aivision_refresh=refresh-1', renewals[1])
        for number, request in enumerate(calls, 1):
            self.assertIn('aivision_access=access-' + str(number), request.headers['Cookie'])
            self.assertEqual(request.headers['X-CSRFToken'], 'csrf-' + str(number))

    def test_failed_renewal_sends_no_api_write_and_401_is_not_replayed(self):
        from platform_client import PlatformClient
        sessions = MagicMock()
        sessions.refresh.side_effect = ValueError('inert renewal failed')
        client = PlatformClient('http://127.0.0.1:12345', dashboard_sessions=sessions)
        client.client.close()

        calls = []
        client.client = httpx.Client(base_url=client.endpoint, transport=httpx.MockTransport(
            lambda request: calls.append(request) or httpx.Response(401, json={'code': 1})))
        with self.assertRaises(ValueError): client.request('POST', '/write')
        self.assertEqual(calls, [])
        sessions.refresh.side_effect = None
        sessions.refresh.return_value = {'dashboard_headers': {}}
        with self.assertRaises(RuntimeError) as caught: client.request('POST', '/write')
        self.assertEqual(caught.exception.http_status, 401)
        self.assertEqual(len(calls), 1)
        client.client.close()

    def test_task_timeout_and_poll_failure_preserve_triggered_identity(self):
        from platform_client import PlatformClient
        for failure in ('timeout', 'poll-error'):
            with self.subTest(failure=failure), PlatformClient('http://127.0.0.1:12345') as client:
                last = {'uuid': 'triggered-task', 'status': 'running'}
                replies = [{'task_uuid': 'triggered-task'}, last]
                if failure == 'poll-error': replies.append(RuntimeError('inert poll error'))
                clocks = [0, 0, 2] if failure == 'timeout' else [0, 0, .1]
                with patch.object(client, 'scoped', side_effect=replies), \
                        patch('platform_client.time.monotonic', side_effect=clocks), patch('platform_client.time.sleep'):
                    with self.assertRaises(TimeoutError if failure == 'timeout' else RuntimeError) as caught:
                        client.execute({'uuid': 'job'}, timeout=1)
                self.assertEqual(caught.exception.task, last)


class AcceptanceTests(unittest.TestCase):
    def validate(self, task, records, diagnosers, **kwargs):
        from platform_client import validate_diagnosis
        return validate_diagnosis(task, records, diagnosers,
            source_trace_ids={"a" * 32}, source_span_ids={"tool-span", "chat-span"},
            expected={"tool_success": "bad"}, **kwargs)

    def test_bad_verdict_requires_source_evidence_and_matching_statistics(self):
        self.assertTrue(self.validate(*world())["verified"])

    def test_real_skip_requires_exact_reason_counts_and_no_record(self):
        from platform_client import validate_diagnosis
        task, records, diags = world()
        task['stats']['diagnosers']['dg-tool']['tool_success_rate'] = {
            'total': 0, 'good': 0, 'bad': 0, 'failed': 0, 'skipped': 1}
        task['stats']['skip_reasons'] = {'dg-tool': {'missing_required_evidence': 1}}
        def validate(t, rs):
            return validate_diagnosis(t, rs, diags, source_trace_ids={'a' * 32},
                source_span_ids={'tool-span'}, expected={},
                expected_skips={'tool_success': 'missing_required_evidence'})
        self.assertTrue(validate(task, [])['verified'])
        for mutation in ('missing-reason', 'wrong-reason', 'boolean-reason', 'unknown-diagnoser', 'skip-count', 'failed-count', 'extra-record'):
            changed, rows = deepcopy(task), []
            if mutation == 'missing-reason': changed['stats'].pop('skip_reasons')
            elif mutation == 'wrong-reason': changed['stats']['skip_reasons']['dg-tool'] = {'not_applicable': 1}
            elif mutation == 'boolean-reason': changed['stats']['skip_reasons']['dg-tool']['missing_required_evidence'] = True
            elif mutation == 'unknown-diagnoser': changed['stats']['skip_reasons']['unselected'] = {'not_applicable': 1}
            elif mutation == 'skip-count': changed['stats']['diagnosers']['dg-tool']['tool_success_rate']['skipped'] = 0
            elif mutation == 'failed-count': changed['stats']['diagnosers']['dg-tool']['tool_success_rate']['failed'] = 1
            else: rows = records
            with self.subTest(mutation=mutation):
                self.assertFalse(validate(changed, rows)['verified'])

    def test_failed_to_good_and_bad_without_evidence_never_pass(self):
        task, records, diags = world()
        records[0]['metrics'][0]['evidence'] = {}
        self.assertFalse(self.validate(task, records, diags)['verified'])
        records[0].update(verdict='good')
        records[0]['metrics'][0].update(verdict='good')
        task['stats']['diagnosers']['dg-tool']['tool_success_rate'].update(good=1, bad=0)
        self.assertFalse(self.validate(task, records, diags)['verified'])

    def test_actual_tool_counts_cannot_be_relabelled(self):
        task, records, diags = world()
        evidence = records[0]['metrics'][0]['evidence']
        evidence['counts'] = {'success': 3, 'failure': 1, 'unknown': 0}
        expected = {'tool_success': {'a' * 32: {'counts': dict(evidence['counts'])}}}
        self.assertTrue(self.validate(task, records, diags, expected_evidence=expected)['verified'])
        evidence['counts']['failure'] = 0
        self.assertFalse(self.validate(task, records, diags, expected_evidence=expected)['verified'])

    def test_failed_verification_evidence_cannot_claim_passed(self):
        from platform_client import validate_diagnosis
        task, records, diags = world()
        diags = {'test_execution': diags['tool_success']}
        evidence = records[0]['metrics'][0]['evidence']
        evidence['verification'] = {'result': 'failed'}
        def validate():
            return validate_diagnosis(task, records, diags, source_trace_ids={'a' * 32},
                source_span_ids={'tool-span'}, expected={'test_execution': 'bad'},
                expected_evidence={'test_execution': {'a' * 32: {'verification_result': 'failed'}}})
        self.assertTrue(validate()['verified'])
        evidence['verification']['result'] = 'passed'
        self.assertFalse(validate()['verified'])

    def test_failed_diagnostic_cannot_be_replaced_by_good_record(self):
        from platform_client import validate_diagnosis
        task, records, diags = world()
        metric = records[0]['metrics'][0]
        metric.update(verdict='good', evidence={})
        records[0].update(verdict='good')
        task['stats']['diagnosers']['dg-tool']['tool_success_rate'].update(good=1, bad=0)
        result = validate_diagnosis(task, records, diags, source_trace_ids={'a' * 32},
            source_span_ids={'tool-span'}, expected={'tool_success': 'failed'})
        self.assertFalse(result['verified'])

    def test_per_trace_skip_keeps_evaluated_coverage_strict(self):
        from platform_client import validate_diagnosis
        task, records, diags = world()
        task['stats']['source']['total'] = 2
        task['stats']['diagnosers']['dg-tool']['tool_success_rate']['skipped'] = 1
        task['stats']['skip_reasons'] = {'dg-tool': {'not_applicable': 1}}
        result = validate_diagnosis(task, records, diags,
            source_trace_ids={'a' * 32, 'b' * 32}, source_span_ids={'tool-span'},
            expected={'tool_success': 'bad'}, expected_skips={'tool_success': {'b' * 32: 'not_applicable'}})
        self.assertTrue(result['verified'], result['failures'])

    def test_green_task_does_not_hide_wrong_source_trace(self):
        task, records, diags = world()
        records[0]["target_id"] = "b" * 32
        self.assertFalse(self.validate(task, records, diags)["verified"])

    def test_bad_evidence_must_point_to_actual_source_span(self):
        task, records, diags = world()
        records[0]["metrics"][0]["evidence"]["bad_spans"][0]["span_id"] = "invented"
        self.assertFalse(self.validate(task, records, diags)["verified"])

    def test_stats_must_reconcile_with_observed_verdicts(self):
        task, records, diags = world()
        task["stats"]["diagnosers"]["dg-tool"]["tool_success_rate"].update(good=1, bad=0)
        self.assertFalse(self.validate(task, records, diags)["verified"])

    def test_applicable_good_cannot_pass_by_exemption(self):
        from platform_client import validate_diagnosis
        task, records, diags = world()
        task["stats"]["diagnosers"]["dg-tool"]["tool_success_rate"].update(good=1, bad=0)
        records[0].update(verdict="good")
        records[0]["metrics"][0].update(verdict="good", evidence={"exempted": True})
        result = validate_diagnosis(task, records, diags,
            source_trace_ids={"a" * 32}, source_span_ids={"tool-span"},
            expected={"tool_success": "good"})
        self.assertFalse(result["verified"])

    def test_duplicate_record_or_missing_declared_metric_is_rejected(self):
        task, records, diags = world()
        for changed in (records + deepcopy(records), [{**records[0], "metrics": []}]):
            with self.subTest(records=changed):
                self.assertFalse(self.validate(task, changed, diags)["verified"])

    def test_trace_filter_rejects_sql_and_filters_only_explicit_roots(self):
        from platform_client import trace_filter
        self.assertEqual(trace_filter({"a" * 32}), "`parent_span_id` = '' AND `trace_id` IN ('" + "a" * 32 + "')")
        for invalid in (set(), {"' OR 1=1"}, {"a" * 31}, {"z" * 32}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                trace_filter(invalid)


if __name__ == "__main__":
    unittest.main()

class DashboardStatsTests(unittest.TestCase):
    categories = (("outcome", "结果质量"), ("process", "过程质量"),
                  ("safety", "安全合规"), ("system", "系统性能"))

    def fixture(self, entries, *, target_count, category_scores, global_score, source_total=None):
        """Build the public schema using explicit, hand-derived scores."""
        definitions, stats, nodes = {}, {}, {category: [] for category, _ in self.categories}
        for key, category, weight, score, metrics in entries:
            definitions[key] = {"uuid": "dg-" + key, "name": "Diagnoser " + key,
                "spec": {"category": category, "weight": weight, "metrics": [
                    {"name": name, "desc": "Metric " + name} for name, *_ in metrics]}}
            stats["dg-" + key] = {}
            metric_nodes = []
            for name, good, bad, failed, skipped, metric_score in metrics:
                counters = {"total": good + bad + failed, "good": good, "bad": bad,
                    "failed": failed, "skipped": skipped}
                stats["dg-" + key][name] = counters
                metric_nodes.append({"id": name, "name": "Metric " + name,
                    "score": metric_score, **counters})
            nodes[category].append({"uuid": "dg-" + key, "name": "Diagnoser " + key,
                "weight": weight, "score": score, "metrics": metric_nodes})
        source_total = target_count if source_total is None else source_total
        task = {"uuid": "t", "status": "succeeded", "stats": {
            "source": {"total": source_total, "invalid": source_total - target_count,
                       "projection_failed": 0}, "diagnosers": stats}}
        value = {"categories": [{"id": category, "name": name} for category, name in self.categories],
            "tasks": [{"task_uuid": "t", "time": "2026-10-08T12:00:00+08:00",
                "target_type": "trace", "target_count": target_count, "selector": None,
                "global": {"score": global_score, "categories": [
                    {"id": category, "name": name, "score": category_scores.get(category),
                     "diagnosers": nodes[category]} for category, name in self.categories]}}]}
        return value, task, definitions

    def v3_fixture(self):
        fixture = self.fixture([
            ("first", "outcome", 1, 100.0, [("goal", 10, 0, 0, 0, 100.0)]),
            ("second", "outcome", 3, 0.0, [("proof", 0, 10, 0, 0, 0.0)]),
            ("signal", "safety", 999, None, [("risk", 0, 10, 0, 0, 0.0)]),
        ], target_count=10, category_scores={"outcome": 25.0}, global_score=25.0)
        value, task, current = fixture
        frozen = {}
        for key, diagnoser in current.items():
            uuid, spec = diagnoser["uuid"], diagnoser["spec"]
            frozen[uuid] = {"uuid": uuid, "key": key, "name": diagnoser["name"],
                "category": spec["category"], "weight": spec["weight"],
                "metrics": deepcopy(spec["metrics"]), "score_role": "signal" if key == "signal" else "quality",
                "version": "1.0", "revision": "1.0.0", "digest": "a" * 64}
        task["stats"].update(schema_version=3, score_policy_version="lightweight-v1",
            diagnoser_definitions=frozen, source_complete=True, skip_reasons={})
        point = value["tasks"][0]
        point.update(schema_version=3, score_policy_version="lightweight-v1", definition_source="task_snapshot",
            source_complete=True, skip_reasons={})
        for category in point["global"]["categories"]:
            for row in category["diagnosers"]:
                row.update({key: frozen[row["uuid"]][key] for key in ("score_role", "version", "revision", "digest", "key")})
        return fixture

    def test_v3_uses_frozen_definitions_and_excludes_signals_from_quality(self):
        fixture = self.v3_fixture()
        self.assertAccepted(fixture)
        fixture[2].clear()  # Current catalog can be removed or changed after execution.
        self.assertAccepted(fixture)
        for control in ("signal-score", "global-score", "weight", "frozen-name", "metadata", "unknown-counter", "version", "skip-reasons"):
            with self.subTest(control=control):
                value, task, current = deepcopy(self.v3_fixture())
                point = value["tasks"][0]; first = point["global"]["categories"][0]["diagnosers"][0]
                if control == "signal-score": point["global"]["categories"][2]["diagnosers"][0]["score"] = 0
                elif control == "global-score": point["global"]["score"] = 12.5
                elif control == "weight": first["weight"] = 999
                elif control == "frozen-name": first["name"] = "current catalog name"
                elif control == "metadata": first["digest"] = "b" * 64
                elif control == "unknown-counter": task["stats"]["diagnosers"]["dg-first"]["unknown"] = {"total": 0}
                elif control == "version": task["stats"]["schema_version"] = True
                else: point["skip_reasons"] = {"unknown": {"unavailable": 1}}
                self.assertRejected((value, task, current))

    def test_v3_accepts_bounded_optional_public_snapshot_metadata(self):
        fields = ("builtin_key", "canonical_variant", "prompt_version", "result_boundary")
        # Metadata is public and optional; it does not change UUID identity or scores.
        for mask in range(1 << len(fields)):
            for text in ("", "x" * 512):
                with self.subTest(mask=mask, length=len(text)):
                    fixture = self.v3_fixture()
                    entry = fixture[1]["stats"]["diagnoser_definitions"]["dg-first"]
                    entry.update({field: text for index, field in enumerate(fields) if mask & (1 << index)})
                    self.assertAccepted(fixture)

    def test_v3_rejects_unknown_or_invalid_optional_snapshot_metadata(self):
        for field in ("builtin_key", "canonical_variant", "prompt_version", "result_boundary"):
            for invalid in (None, False, 1, [], {}, "x" * 513):
                with self.subTest(field=field, invalid=invalid):
                    fixture = self.v3_fixture()
                    fixture[1]["stats"]["diagnoser_definitions"]["dg-first"][field] = invalid
                    self.assertRejected(fixture)
        for field in ("prompt", "model", "spec", "unknown"):
            with self.subTest(extra=field):
                fixture = self.v3_fixture()
                fixture[1]["stats"]["diagnoser_definitions"]["dg-first"][field] = "public"
                self.assertRejected(fixture)

    def test_v3_optional_metadata_keeps_all_required_definition_fields(self):
        for field in self.v3_fixture()[1]["stats"]["diagnoser_definitions"]["dg-first"]:
            with self.subTest(missing=field):
                fixture = self.v3_fixture()
                entry = fixture[1]["stats"]["diagnoser_definitions"]["dg-first"]
                entry.update(builtin_key="core_coding", canonical_variant="coding",
                             prompt_version="1.0", result_boundary="bounded")
                entry.pop(field)
                self.assertRejected(fixture)

    def test_v3_optional_metadata_does_not_relax_score_source_or_coverage_checks(self):
        for control in ("score", "source", "unknown", "coverage", "uuid", "digest"):
            with self.subTest(control=control):
                fixture = self.v3_fixture()
                value, task, _ = fixture
                entry = task["stats"]["diagnoser_definitions"]["dg-first"]
                entry.update(builtin_key="core_coding", canonical_variant="coding",
                             prompt_version="1.0", result_boundary="bounded")
                if control == "score": value["tasks"][0]["global"]["score"] = 100.0
                elif control == "source": task["stats"]["source"]["invalid"] = 1
                elif control == "unknown": task["stats"]["diagnosers"]["dg-first"]["unknown"] = {"total": 0}
                elif control == "coverage": task["stats"]["diagnosers"].pop("dg-first")
                elif control == "uuid": entry["uuid"] = "dg-second"
                else: entry["digest"] = "b" * 64
                self.assertRejected(fixture)

    def shared_key_fixture(self, profile="weighted-75", key="llm_custom"):
        value, task, current = self.v3_fixture()
        point = value["tasks"][0]
        outcome = point["global"]["categories"][0]
        for row, weight in zip(outcome["diagnosers"], (3, 1), strict=True):
            entry = task["stats"]["diagnoser_definitions"][row["uuid"]]
            entry.update(key=key, weight=weight)
            row.update(key=key, weight=weight)
        outcome["score"] = point["global"]["score"] = 75.0
        if profile != "weighted-75":
            failed_rows = outcome["diagnosers"] if profile == "failed-only" else outcome["diagnosers"][1:]
            for row in failed_rows:
                counters = task["stats"]["diagnosers"][row["uuid"]][row["metrics"][0]["id"]]
                counters.update(total=10, good=0, bad=0, failed=10)
                row["metrics"][0].update(counters, score=None)
                row["score"] = None
            outcome["score"] = point["global"]["score"] = None
        current.clear()  # Frozen UUID definitions are authoritative.
        return value, task, current

    def test_v3_shared_executor_key_retains_distinct_uuid_definitions_and_scores(self):
        for key in ("llm_custom", "general_llm"):
            for profile in ("weighted-75", "failed-only", "mixed-failure"):
                with self.subTest(key=key, profile=profile):
                    self.assertAccepted(self.shared_key_fixture(profile, key))

    def test_v3_shared_key_still_rejects_identity_metadata_counter_and_score_corruption(self):
        for control in ("snapshot-uuid", "snapshot-map", "dashboard-uuid", "key", "role", "weight",
                        "category", "metric", "counter-shape", "counter-bool", "counter-total",
                        "missing-coverage", "first-score", "global-score"):
            with self.subTest(control=control):
                value, task, current = deepcopy(self.shared_key_fixture())
                frozen = task["stats"]["diagnoser_definitions"]
                rows = value["tasks"][0]["global"]["categories"][0]["diagnosers"]
                first, second = rows
                if control == "snapshot-uuid": frozen["dg-second"]["uuid"] = "dg-first"
                elif control == "snapshot-map": frozen["other-uuid"] = frozen.pop("dg-second")
                elif control == "dashboard-uuid": second["uuid"] = first["uuid"]
                elif control == "key": second["key"] = "different-executor"
                elif control == "role": second["score_role"] = "signal"
                elif control == "weight": first["weight"] = 999
                elif control == "category": frozen["dg-second"]["category"] = "process"
                elif control == "metric": second["metrics"][0]["id"] = "unknown"
                elif control == "counter-shape": task["stats"]["diagnosers"]["dg-second"]["proof"]["unknown"] = 1
                elif control == "counter-bool": task["stats"]["diagnosers"]["dg-first"]["goal"]["good"] = True
                elif control == "counter-total": task["stats"]["diagnosers"]["dg-first"]["goal"]["total"] = 9
                elif control == "missing-coverage": task["stats"]["diagnosers"].pop("dg-second")
                elif control == "first-score": first["score"] = 0.0
                else: value["tasks"][0]["global"]["score"] = 100.0
                self.assertRejected((value, task, current))
        for profile in ("failed-only", "mixed-failure"):
            value, task, current = self.shared_key_fixture(profile)
            value["tasks"][0]["global"]["score"] = 75.0
            self.assertRejected((value, task, current))

    def test_v3_same_metric_name_in_distinct_shared_key_definitions_stays_independent(self):
        value, task, current = self.shared_key_fixture()
        task["stats"]["diagnosers"]["dg-second"]["goal"] = task["stats"]["diagnosers"]["dg-second"].pop("proof")
        task["stats"]["diagnoser_definitions"]["dg-second"]["metrics"] = [{"name": "goal", "desc": "Metric goal"}]
        second = value["tasks"][0]["global"]["categories"][0]["diagnosers"][1]
        second["metrics"][0].update(id="goal", name="Metric goal")
        self.assertAccepted((value, task, current))
        # A first-UUID failure cannot disappear under the second UUID's identical metric name.
        value["tasks"][0]["global"]["categories"][0]["diagnosers"][0]["metrics"][0]["good"] = 0
        self.assertRejected((value, task, current))

    def test_v3_incomplete_quality_keeps_individual_counters_and_null_summary(self):
        for control in ("failed", "skip", "coverage", "missing", "source", "invalid", "projection", "partial-selector"):
            with self.subTest(control=control):
                value, task, current = self.v3_fixture()
                point = value["tasks"][0]; outcome = point["global"]["categories"][0]
                second = outcome["diagnosers"][1]; metric = second["metrics"][0]
                counter = task["stats"]["diagnosers"]["dg-second"]["proof"]
                if control in ("failed", "skip", "coverage"):
                    counter.update(total=9, bad=9)
                    if control == "failed": counter.update(total=10, failed=1)
                    if control == "skip": counter["skipped"] = 1
                    metric.update(counter, score=None)
                    second["score"] = None
                elif control == "missing":
                    task["stats"]["diagnosers"].pop("dg-second")
                    metric.update(total=0, good=0, bad=0, failed=0, skipped=0, score=None)
                    second["score"] = None
                else:
                    if control == "source": task["stats"]["source_complete"] = point["source_complete"] = False
                    elif control == "invalid": task["stats"]["source"]["invalid"] = 1
                    elif control == "projection": task["stats"]["source"]["projection_failed"] = 1
                    else: task["stats"]["selector"] = point["selector"] = {"status": "partial"}
                    for row in outcome["diagnosers"]: row["score"] = None
                outcome["score"] = point["global"]["score"] = None
                self.assertAccepted((value, task, current))
                point["global"]["score"] = 100.0
                self.assertRejected((value, task, current))

    def mixed_fixture(self):
        # outcome = (5/12 + 3 * 4/5) / 4; process = (1/4 + 3/4) / 2;
        # global = (outcome + process + 2/3) / 3. Failed-only safety is excluded.
        return self.fixture([
            ("judge", "outcome", 1, 41.7, [
                ("task_completion", 1, 2, 6, 1, 33.3),
                ("test_discipline", 1, 1, 8, 0, 50.0),
                ("report_fidelity", 0, 0, 10, 0, None)]),
            ("format", "outcome", 3, 80.0, [("format", 8, 2, 0, 0, 80.0)]),
            ("zero_weight", "outcome", 0, 0.0, [("unweighted", 0, 10, 0, 0, 0.0)]),
            ("unavailable", "outcome", 999, None, [("unavailable", 0, 0, 10, 0, None)]),
            ("gate", "process", 0, 25.0, [("gate", 1, 3, 6, 0, 25.0)]),
            ("tests", "process", 0, 75.0, [("tests", 3, 1, 0, 6, 75.0)]),
            ("safety", "safety", 999, None, [("safety", 0, 0, 7, 3, None)]),
            ("latency", "system", 1, 66.7, [("latency", 2, 1, 7, 0, 66.7)]),
        ], target_count=10,
            category_scores={"outcome": 70.4, "process": 50.0, "system": 66.7}, global_score=62.4)

    def validate(self, fixture):
        from platform_client import validate_stats
        return validate_stats(*fixture)

    def assertAccepted(self, fixture):
        proof = self.validate(fixture)
        self.assertTrue(proof["verified"], proof["failures"])

    def assertRejected(self, fixture):
        proof = self.validate(fixture)
        self.assertFalse(proof["verified"], proof["checks"])
        self.assertTrue(proof["failures"])

    def test_independent_hierarchy_uses_metric_means_weights_and_valid_categories(self):
        self.assertAccepted(self.mixed_fixture())

    def test_rounding_happens_after_aggregation_at_each_level(self):
        for entries, category_scores in (
            ([("judge", "outcome", 1, 16.7,
                [("first", 1, 2, 0, 0, 33.3), ("second", 0, 3, 0, 0, 0.0)])], {"outcome": 16.7}),
            ([("first", "outcome", 1, 33.3, [("quality", 1, 2, 0, 0, 33.3)]),
              ("second", "outcome", 1, 0.0, [("quality", 0, 3, 0, 0, 0.0)])], {"outcome": 16.7}),
            ([("first", "outcome", 1, 33.3, [("quality", 1, 2, 0, 0, 33.3)]),
              ("second", "process", 1, 0.0, [("quality", 0, 3, 0, 0, 0.0)])],
             {"outcome": 33.3, "process": 0.0}),
        ):
            # (1/3 + 0) / 2 gives 16.7, while averaging displayed 33.3 gives 16.6.
            with self.subTest(entries=entries):
                self.assertAccepted(self.fixture(entries, target_count=3,
                    category_scores=category_scores, global_score=16.7))

    def test_each_judge_metric_is_checked_independently(self):
        for index in range(3):
            for field, invalid in (("score", 100.0), ("good", 9)):
                value, task, definitions = self.mixed_fixture()
                value["tasks"][0]["global"]["categories"][0]["diagnosers"][0]["metrics"][index][field] = invalid
                with self.subTest(index=index, field=field):
                    self.assertRejected((value, task, definitions))

    def test_quality_tiers_exclude_failed_and_skipped_from_the_denominator(self):
        for good, bad, failed, skipped, score in (
            (4, 0, 4, 2, 100.0), (3, 1, 4, 2, 75.0), (2, 2, 4, 2, 50.0),
            (1, 3, 4, 2, 25.0), (0, 4, 4, 2, 0.0), (1, 2, 5, 2, 33.3),
            (0, 0, 10, 0, None), (0, 0, 0, 10, None),
        ):
            with self.subTest(score=score, failed=failed, skipped=skipped):
                self.assertAccepted(self.fixture([
                    ("judge", "outcome", 1, score, [("quality", good, bad, failed, skipped, score)])
                ], target_count=10, category_scores={"outcome": score}, global_score=score))

    def test_wrong_score_at_every_level_is_rejected_even_when_metrics_match(self):
        for level in ("global", "category", "diagnoser", "metric"):
            value, task, definitions = self.mixed_fixture()
            node = value["tasks"][0]["global"]
            if level != "global":
                node = node["categories"][0]
            if level in ("diagnoser", "metric"):
                node = node["diagnosers"][0]
            if level == "metric":
                node = node["metrics"][0]
            node["score"] = 100.0
            with self.subTest(level=level):
                self.assertRejected((value, task, definitions))

    def test_zero_quality_is_valid_but_empty_or_failed_quality_must_be_null(self):
        for invalid in (0, 100, False, "null"):
            value, task, definitions = self.mixed_fixture()
            for level in ("category", "diagnoser", "metric"):
                changed = deepcopy(value)
                node = changed["tasks"][0]["global"]["categories"][2]
                if level != "category":
                    node = node["diagnosers"][0]
                if level == "metric":
                    node = node["metrics"][0]
                node["score"] = invalid
                with self.subTest(level=level, invalid=invalid):
                    self.assertRejected((changed, task, definitions))

    def test_missing_or_duplicate_categories_diagnosers_and_metrics_are_rejected(self):
        for collection in ("definitions", "categories", "diagnosers", "metrics"):
            for operation in ("missing", "duplicate", "extra"):
                value, task, definitions = self.mixed_fixture()
                if collection == "definitions":
                    rows = value["categories"]
                else:
                    rows = value["tasks"][0]["global"]["categories"]
                    if collection in ("diagnosers", "metrics"):
                        rows = rows[0]["diagnosers"]
                    if collection == "metrics":
                        rows = rows[0]["metrics"]
                if operation == "missing":
                    rows.pop()
                else:
                    duplicate = deepcopy(rows[0])
                    if operation == "extra":
                        duplicate["uuid" if collection == "diagnosers" else "id"] = "unrequested"
                    rows.append(duplicate)
                with self.subTest(collection=collection, operation=operation):
                    self.assertRejected((value, task, definitions))

    def test_category_membership_names_and_weights_are_verified(self):
        for field, invalid in (("uuid", "foreign"), ("name", "Another diagnoser"),
                               ("weight", 3), ("weight", True), ("weight", "1")):
            value, task, definitions = self.mixed_fixture()
            value["tasks"][0]["global"]["categories"][0]["diagnosers"][0][field] = invalid
            with self.subTest(field=field, invalid=invalid):
                self.assertRejected((value, task, definitions))
        for location in ("definition", "category", "metric"):
            value, task, definitions = self.mixed_fixture()
            node = value["categories"][0] if location == "definition" else value["tasks"][0]["global"]["categories"][0]
            if location == "metric":
                node = node["diagnosers"][0]["metrics"][0]
            node["name"] = "Another label"
            with self.subTest(location=location):
                self.assertRejected((value, task, definitions))
        value, task, definitions = self.mixed_fixture()
        categories = value["tasks"][0]["global"]["categories"]
        categories[1]["diagnosers"].append(categories[0]["diagnosers"].pop())
        self.assertRejected((value, task, definitions))

    def test_declared_and_stored_metric_identities_are_complete(self):
        for location in ("definitions", "stats"):
            for operation in ("missing", "duplicate", "extra"):
                value, task, definitions = self.mixed_fixture()
                if location == "definitions":
                    metrics = definitions["judge"]["spec"]["metrics"]
                    if operation == "missing":
                        metrics.pop()
                    else:
                        metrics.append({"name": "extra"} if operation == "extra" else deepcopy(metrics[0]))
                else:
                    metrics = task["stats"]["diagnosers"]["dg-judge"]
                    if operation == "missing":
                        metrics.pop("report_fidelity")
                    elif operation == "extra":
                        metrics["extra"] = deepcopy(metrics["task_completion"])
                    else:
                        definitions["alias"] = deepcopy(definitions["judge"])
                with self.subTest(location=location, operation=operation):
                    self.assertRejected((value, task, definitions))
        for operation in ("missing", "extra"):
            value, task, definitions = self.mixed_fixture()
            stored = task["stats"]["diagnosers"]
            if operation == "missing":
                stored.pop("dg-judge")
            else:
                stored["foreign"] = deepcopy(stored["dg-judge"])
            with self.subTest(diagnoser=operation):
                self.assertRejected((value, task, definitions))

    def test_counter_types_nonnegative_sum_and_common_coverage_are_checked(self):
        for field in ("total", "good", "bad", "failed", "skipped"):
            for invalid in (None, True, 1.0, -1, "1"):
                for location in ("dashboard", "stats"):
                    value, task, definitions = self.mixed_fixture()
                    counter = (task["stats"]["diagnosers"]["dg-judge"]["task_completion"] if location == "stats"
                        else value["tasks"][0]["global"]["categories"][0]["diagnosers"][0]["metrics"][0])
                    counter[field] = invalid
                    with self.subTest(field=field, invalid=invalid, location=location):
                        self.assertRejected((value, task, definitions))
        for field in ("total", "good", "bad", "failed"):
            value, task, definitions = self.mixed_fixture()
            task["stats"]["diagnosers"]["dg-judge"]["task_completion"].pop(field)
            with self.subTest(missing=field):
                self.assertRejected((value, task, definitions))
        for field in ("total", "skipped"):
            value, task, definitions = self.mixed_fixture()
            counter = task["stats"]["diagnosers"]["dg-judge"]["task_completion"]
            counter[field] += 1
            row = value["tasks"][0]["global"]["categories"][0]["diagnosers"][0]["metrics"][0]
            row[field] += 1
            with self.subTest(inconsistent=field):
                self.assertRejected((value, task, definitions))

    def test_coverage_comes_from_metrics_instead_of_source_total(self):
        fixture = self.mixed_fixture()
        fixture[1]["stats"]["source"].update(total=12, invalid=2)
        self.assertAccepted(fixture)
        for invalid in (12, True, 10.0, None, -1):
            changed = deepcopy(fixture)
            changed[0]["tasks"][0]["target_count"] = invalid
            with self.subTest(target_count=invalid):
                self.assertRejected(changed)

    def test_empty_snapshot_keeps_full_null_tree_and_zero_coverage(self):
        value, task, definitions = self.fixture([
            ("judge", "outcome", 1, None, [("quality", 0, 0, 0, 0, None)])
        ], target_count=0, category_scores={}, global_score=None)
        task["stats"] = {"source": {}, "diagnosers": {}}
        self.assertAccepted((value, task, definitions))
        value["tasks"][0]["global"]["score"] = 0
        self.assertRejected((value, task, definitions))

    def test_empty_job_returns_the_four_empty_categories_with_null_global(self):
        self.assertAccepted(self.fixture([], target_count=0, category_scores={}, global_score=None))

    def test_required_dashboard_counters_and_numeric_scores_cannot_be_omitted_or_coerced(self):
        for field in ("total", "good", "bad", "failed", "skipped"):
            value, task, definitions = self.mixed_fixture()
            value["tasks"][0]["global"]["categories"][0]["diagnosers"][0]["metrics"][0].pop(field)
            with self.subTest(missing=field):
                self.assertRejected((value, task, definitions))
        for invalid in (None, True, "33.3", float("nan"), float("inf")):
            value, task, definitions = self.mixed_fixture()
            value["tasks"][0]["global"]["categories"][0]["diagnosers"][0]["metrics"][0]["score"] = invalid
            with self.subTest(score=invalid):
                self.assertRejected((value, task, definitions))

    def test_legacy_skipped_defaults_to_zero_and_category_weight_defaults_match_contract(self):
        for category in (None, "", "unknown"):
            for weight in (None, "heavy", True):
                value, task, definitions = self.fixture([
                    ("judge", "outcome", 0, 100.0, [("quality", 1, 0, 0, 0, 100.0)])
                ], target_count=1, category_scores={"outcome": 100.0}, global_score=100.0)
                definitions["judge"]["spec"].update(category=category, weight=weight)
                task["stats"]["diagnosers"]["dg-judge"]["quality"].pop("skipped")
                with self.subTest(category=category, weight=weight):
                    self.assertAccepted((value, task, definitions))

    def test_missing_null_scores_and_malformed_payloads_return_failure(self):
        for location in ("global", "category", "diagnoser", "metric"):
            value, task, definitions = self.fixture([
                ("judge", "outcome", 1, None, [("quality", 0, 0, 1, 0, None)])
            ], target_count=1, category_scores={}, global_score=None)
            node = value["tasks"][0]["global"]
            if location != "global":
                node = node["categories"][0]
            if location in ("diagnoser", "metric"):
                node = node["diagnosers"][0]
            if location == "metric":
                node = node["metrics"][0]
            node.pop("score")
            with self.subTest(missing_score=location):
                self.assertRejected((value, task, definitions))
        value, task, definitions = self.mixed_fixture()
        for invalid in (None, {}, {"tasks": None}, {"tasks": [None]}):
            with self.subTest(payload=invalid):
                self.assertRejected((invalid, task, definitions))
        for invalid_stats in (None, {}, {"source": {}, "diagnosers": None}):
            changed = deepcopy(task)
            changed["stats"] = invalid_stats
            with self.subTest(stats=invalid_stats):
                self.assertRejected((value, changed, definitions))

    def test_missing_task_does_not_count_as_zero_success(self):
        value, task, definitions = self.mixed_fixture()
        value["tasks"] = []
        self.assertRejected((value, task, definitions))

    def test_duplicate_task_is_rejected_and_prior_task_points_are_allowed(self):
        fixture = self.mixed_fixture()
        changed = deepcopy(fixture)
        changed[0]["tasks"].append(deepcopy(changed[0]["tasks"][0]))
        self.assertRejected(changed)
        fixture[0]["tasks"].append({**deepcopy(fixture[0]["tasks"][0]), "task_uuid": "previous"})
        self.assertAccepted(fixture)


class BadcaseStorageTests(unittest.TestCase):
    def validate(self, expected, stored):
        from run_suite import verify_badcase_storage
        client = MagicMock()
        client.create_job.return_value = {"uuid": "new-job", "spec": {}}
        client.execute.return_value = {"uuid": "new-task", "stats": {"source": {"total": 1}}}
        client.dashboard_stats.return_value = {"verified": True}
        client.records.return_value = stored
        with patch("run_suite.time.sleep"):
            return verify_badcase_storage(client, "storage", {}, {},
                {"stats": {"source": {"total": 1}}}, expected)

    def test_new_task_identifiers_and_record_order_do_not_change_bad_facts(self):
        _, records, _ = world()
        stored = deepcopy(records)
        stored[0].update(uuid="new-record", task_uuid="new-task", job_uuid="new-job",
            diagnosed_at="2026-10-08T22:00:00+08:00")
        self.assertTrue(self.validate(records, stored)["verified"])
        self.assertTrue(self.validate([], [])["verified"])

    def test_default_storage_rejects_another_trace_or_changed_diagnosis(self):
        _, records, _ = world()
        for field, value in (("target_id", "f" * 32), ("metrics", []),
                ("execution", {"status": "failed"}), ("metrics", [{"name": "other", "verdict": "bad"}])):
            changed = deepcopy(records)
            changed[0][field] = value
            with self.subTest(field=field, value=value):
                self.assertFalse(self.validate(records, changed)["verified"])
        self.assertFalse(self.validate(records, records + deepcopy(records))["verified"])
        self.assertFalse(self.validate(records, [])["verified"])
