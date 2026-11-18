"""Framework panels must not perform a detail/count round trip per Trace."""
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from apps.core.query.framework import query_framework
from apps.core.trace_query.metrics_projection import project_metrics
from apps.core.trace_query.native_query import NativeTraceWindow
from apps.core.trace_query.signoz.provider import SignozTraceProvider
from apps.core.trace_query.types import TraceQueryServiceError


class FrameworkSnapshotTests(SimpleTestCase):
    def setUp(self):
        self.enterContext(patch('apps.core.trace_query.signoz.statistics_aggregate.query_metrics_aggregate', return_value=None))
        if not getattr(self, 'typed_operation_page', False):
            self.enterContext(patch('apps.core.trace_query.signoz.operation_page.query_operation_page', return_value=None))
        self.endpoint = 'http://fixture.invalid'
        self.api_key = 'fixture-only'
        self.context = {"space": SimpleNamespace(uuid="space", type=2), "user": None,
                        "agent": SimpleNamespace(uuid="agent")}
        self.prepared = {"query_key": "framework.overview.event_stream", "from_ms": 1000,
                         "to_ms": 5000, "params": {"agent_uuid": "agent", "limit": 1}}
        self.details = [{"traceId": f"trace-{i}", "spans": [{
            "trace_id": f"trace-{i}", "span_id": f"span-{i}", "name": f"call-{i}",
            "start_time_unix_nano": str((1000+i)*1000000),
            "end_time_unix_nano": str((1001+i)*1000000),
            "resource": {"aivision.space.id": "space", "aivision.agent.id": "agent"},
            "attributes": {"langfuse.observation.type": "TOOL" if i == 1 else "GENERATION",
                           "gen_ai.usage.input_tokens": 0 if i == 0 else None,
                           "gen_ai.input.messages": "input", "gen_ai.output.messages": "output"},
        }]} for i in range(3)]
        self.calls = []
        self.snapshot_mode = "ready"
        self.use_summaries = False
        self.snapshot_previews = False
        self.dirty_ids = set()
        self.batch_mode = None
        self.source_user = "owner"
        self.window_generation = 1
        self.context_patch = self.enterContext(patch.object(SignozTraceProvider, "_context", return_value=(self, {})))

    def get(self, path, *, params):
        self.calls.append((path, deepcopy(params)))
        if path.endswith(("/read-model/window", "/read-model/metrics-window")):
            if self.snapshot_mode == "unsupported":
                return {"supported": False, "reason": "capability_unavailable"}
            if self.snapshot_mode == "error":
                raise TraceQueryServiceError("storage unavailable", 3012)
            count = sum(len(detail["spans"]) for detail in self.details)
            meta = {**params, "raw": True, "omittedAgentTraceCount": 0,
                    "traceCount": len(self.details), "projectedTraceCount": 0, "rawTraceCount": len(self.details),
                    "spanCount": count, "rawSpanCount": count}
            if self.snapshot_mode == "foreign_subject":
                meta["user"] = "another-subject"
            rows, raw = [], deepcopy(self.details)
            if self.use_summaries and params["raw"] == "false":
                window = NativeTraceWindow([detail for detail in raw if detail["traceId"] not in self.dirty_ids],
                                           project_id="space", prices=[], include_previews=True)
                rows = [{**row, "_traceTags": trace["tags"], "_sceneValues": [],
                         "_metrics": project_metrics(trace), "sourceRevision": 1}
                        for row, trace in zip(window.rows, window.traces)]
                if self.snapshot_previews:
                    for row, trace in zip(rows, window.traces):
                        row['_operation_previews'] = {item['id']: dict(name=item['name'], latency=item['latency'],
                            status=0, input='{"content":"input"}', output='{"content":"output"}',
                            input_truncated=False, output_truncated=False) for item in trace['observations']}
                raw = [detail for detail in raw if detail["traceId"] in self.dirty_ids]
                meta.update(raw=False, projectedTraceCount=len(rows), rawTraceCount=len(raw),
                            rawSpanCount=sum(len(detail["spans"]) for detail in raw))
                if self.window_generation is not None:
                    meta['maintenance_generation'] = self.window_generation
            compact = path.endswith('/metrics-window')
            if compact:
                from apps.core.trace_query.metrics_projection import ROW_FIELDS
                rows = [{key: value for key, value in row.items() if key in ROW_FIELDS or key == 'sourceRevision'} for row in rows]
                meta['representation'] = 'metrics-v1'
            return {"supported": True, "complete": True, "contract": "native-trace-metrics-window-v1" if compact else "native-trace-window-v1",
                    "meta": meta, "rows": rows, "raw": raw}
        if path.endswith("/read-model/batch-snapshot"):
            if self.batch_mode == "unsupported":
                return {"supported": False, "reason": "maintenance_suspended"}
            if self.batch_mode == "error":
                raise TraceQueryServiceError("snapshot transport failed", 3012)
            if self.batch_mode == "auth":
                raise TraceQueryServiceError("snapshot authentication failed", 3017)
            identifiers = json.loads(params["trace_ids"])
            source = {detail["traceId"]: detail for detail in self.details}
            if self.batch_mode == "changed":
                for identifier in identifiers:
                    source[identifier]["spans"][0]["attributes"]["gen_ai.input.messages"] = "updated-input"
            data = [{"supported": True, "complete": True, "contract": "native-trace-source-v1",
                     "trace_id": identifier, "source_revision": 2 if self.batch_mode == "changed" else 1,
                     "maintenance_generation": 1, "scope_valid": True,
                     "source_user_id": self.source_user, "source_agent_product": "langfuse",
                     "span_count": len(source[identifier]["spans"]),
                     "spans": deepcopy(source[identifier]["spans"])} for identifier in identifiers]
            if self.batch_mode in ('generation_aba', 'mixed_generation'):
                for item in (data if self.batch_mode == 'generation_aba' else data[-1:]):
                    item['maintenance_generation'] = 2
                    item['spans'][0]['attributes']['gen_ai.input.messages'] = 'after-reset'
                for detail in self.details:
                    detail['spans'][0]['attributes']['gen_ai.input.messages'] = 'after-reset'
                self.details[0]['spans'][0]['start_time_unix_nano'] = '4000000000'
                self.details[0]['spans'][0]['end_time_unix_nano'] = '4001000000'
            if self.batch_mode == "foreign":
                data[0]["spans"][0]["resource"]["aivision.agent.id"] = "foreign-agent"
            elif self.batch_mode == "malformed":
                data[0]["span_count"] += 1
            elif self.batch_mode == "incomplete":
                data[0]["complete"] = False
            elif self.batch_mode == "product":
                data[0]["source_agent_product"] = "other-product"
            elif self.batch_mode == "subject":
                data[0]["source_user_id"] = "other-subject"
            elif self.batch_mode in ("count_drift", "outside_window"):
                child = deepcopy(data[0]["spans"][0])
                child.update(span_id="unexpected", parent_span_id=data[0]["spans"][0]["span_id"])
                if self.batch_mode == "outside_window":
                    child["start_time_unix_nano"] = "9000000000"
                data[0]["spans"].append(child)
                data[0]["span_count"] += 1
            return {"supported": True, "contract": "native-trace-source-batch-v1", "data": data}
        if path == "/api/v1/ai-vision/traces":
            rows = [{"trace_id": detail["traceId"], "span_count": 1} for detail in self.details
                    if not params.get("trace_id") or params["trace_id"] == detail["traceId"]]
            return {"data": rows, "meta": {"page": 1, "limit": 200,
                                            "totalItems": len(rows), "totalPages": 1}}
        return deepcopy(next(detail for detail in self.details if path.endswith("/" + detail["traceId"])))

    def test_stream_and_trend_use_one_verified_snapshot_each(self):
        stream = query_framework(self.context, self.prepared, "langfuse")
        self.assertEqual([row["span_id"] for row in stream["rows"]], ["span-2"])
        self.assertEqual(stream["rows"][0]["input"], "input")
        self.assertIsNone(stream["rows"][0]["input_tokens"])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][1]["agent_uuid"], "agent")
        self.calls.clear()
        trend = query_framework(self.context, {**self.prepared, "query_key": "framework.events.timeseries"}, "langfuse")
        self.assertEqual({row["kind"]: row["count"] for row in trend["rows"]}, {"llm": 2, "tool": 1})
        self.assertEqual(len(self.calls), 1)

    def test_unsupported_snapshot_keeps_verified_legacy_results(self):
        self.snapshot_mode = "unsupported"
        result = query_framework(self.context, self.prepared, "langfuse")
        self.assertEqual([row["span_id"] for row in result["rows"]], ["span-2"])
        self.assertEqual(len(self.calls), 9)  # Compact + raw capability, list, detail/count for 3 Traces.

    @override_settings(AIVISION_QUERY_MODE="auto")
    def test_trend_uses_compact_facts_without_message_or_detail_reads(self):
        self.use_summaries = True
        result = query_framework(self.context, {**self.prepared, "query_key": "framework.events.timeseries"}, "langfuse")
        self.assertEqual({row["kind"]: row["count"] for row in result["rows"]}, {"llm": 2, "tool": 1})
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][1]["raw"], "false")

    @override_settings(AIVISION_QUERY_MODE="auto")
    def test_unavailable_typed_engine_uses_compact_snapshot_previews_without_source_hydration(self):
        self.use_summaries = True
        self.snapshot_previews = True
        result = query_framework(self.context, self.prepared, 'langfuse')
        self.assertEqual([row['span_id'] for row in result['rows']], ['span-2'])
        self.assertEqual(result['rows'][0]['input'], '{"content":"input"}')
        self.assertEqual(result['rows'][0]['content_mode'], 'preview')
        self.assertEqual([path.rsplit('/', 1)[-1] for path, _ in self.calls], ['metrics-window'])

    @override_settings(AIVISION_QUERY_MODE="raw")
    def test_trend_respects_raw_recovery_control(self):
        self.use_summaries = True
        result = query_framework(self.context, {**self.prepared, "query_key": "framework.events.timeseries"}, "langfuse")
        self.assertEqual({row["kind"]: row["count"] for row in result["rows"]}, {"llm": 2, "tool": 1})
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][1]["raw"], "true")

    def test_storage_error_is_not_retried_through_legacy_scan(self):
        self.snapshot_mode = "error"
        with self.assertRaisesRegex(TraceQueryServiceError, "storage unavailable"):
            query_framework(self.context, self.prepared, "langfuse")
        self.assertEqual(len(self.calls), 1)

    def test_personal_business_scope_binds_and_verifies_the_selected_subject(self):
        selected = SimpleNamespace(space_id=1, observation_subject_binding=SimpleNamespace(
            space_id=1, status=1, gmt_deleted=None, subject_id="selected-subject",
            user=SimpleNamespace(status=1, gmt_deleted=None)))
        with patch("apps.core.query.framework.resolve_space_agent_for_query", return_value=selected):
            result = query_framework(self.context, {**self.prepared, "scope": "personal"}, "langfuse")
            self.assertEqual([row["span_id"] for row in result["rows"]], ["span-2"])
            self.assertEqual(len(self.calls), 1)
            self.assertEqual(self.calls[0][1]["user"], "selected-subject")
            self.calls.clear()
            self.snapshot_mode = "foreign_subject"
            with self.assertRaisesRegex(TraceQueryServiceError, "authorized scope"):
                query_framework(self.context, {**self.prepared, "scope": "personal"}, "langfuse")
            self.assertEqual(len(self.calls), 1)


class FrameworkBoundedStreamTests(SimpleTestCase):
    get = FrameworkSnapshotTests.get

    def setUp(self):
        FrameworkSnapshotTests.setUp(self)
        self.use_summaries = True

    @override_settings(AIVISION_QUERY_MODE="auto")
    def test_large_window_fetches_contents_for_only_selected_operations(self):
        template = deepcopy(self.details[0]["spans"][0])
        self.details = [{"traceId": f"trace-{i:04d}", "spans": [{**deepcopy(template),
            "trace_id": f"trace-{i:04d}", "span_id": f"span-{i:04d}",
            "start_time_unix_nano": str((1000+i)*1000000),
            "end_time_unix_nano": str((1001+i)*1000000)}]} for i in range(5000)]
        result = query_framework(self.context, {**self.prepared, "to_ms": 7000,
            "params": {"agent_uuid": "agent", "limit": 2}}, "langfuse")
        self.assertEqual([row["span_id"] for row in result["rows"]], ["span-4999", "span-4998"])
        self.assertEqual([row["input"] for row in result["rows"]], ["input", "input"])
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[0][1]["raw"], "false")
        self.assertEqual(json.loads(self.calls[1][1]["trace_ids"]), ["trace-4999", "trace-4998"])

    def test_dirty_trace_and_tied_clean_operations_keep_exact_order_and_contents(self):
        self.dirty_ids = {"trace-2"}
        self.details[2]["spans"][0]["start_time_unix_nano"] = "1001000000"
        child = deepcopy(self.details[1]["spans"][0])
        child.update(span_id="z-child", parent_span_id="span-1")
        child["attributes"]["gen_ai.input.messages"] = "child-input"
        self.details[1]["spans"].append(child)
        result = query_framework(self.context, {**self.prepared, "params": {"agent_uuid": "agent", "limit": 3}}, "langfuse")
        self.assertEqual([row["span_id"] for row in result["rows"]], ["span-2", "z-child", "span-1"])
        self.assertEqual([row["input"] for row in result["rows"]], ["input", "child-input", "input"])
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(json.loads(self.calls[1][1]["trace_ids"]), ["trace-1"])

    def test_changed_revision_and_suspended_maintenance_restart_one_complete_raw_snapshot(self):
        for mode in ("changed", "unsupported"):
            with self.subTest(mode=mode):
                self.calls.clear()
                self.batch_mode = mode
                result = query_framework(self.context, self.prepared, "langfuse")
                self.assertEqual([row["span_id"] for row in result["rows"]], ["span-2"])
                self.assertEqual(len(self.calls), 3)
                self.assertEqual(self.calls[-1][1]["raw"], "true")
                if mode == "changed":
                    self.assertEqual(result["rows"][0]["input"], "updated-input")

    def test_source_scope_malformed_payload_and_transport_errors_do_not_fall_back(self):
        for mode in ("foreign", "malformed", "incomplete", "product", "error", "auth", "count_drift", "outside_window"):
            with self.subTest(mode=mode):
                self.calls.clear()
                self.batch_mode = mode
                with self.assertRaises(TraceQueryServiceError):
                    query_framework(self.context, self.prepared, "langfuse")
                self.assertEqual(len(self.calls), 2)

    def test_personal_scope_cannot_hydrate_another_physical_subject(self):
        selected = SimpleNamespace(space_id=1, observation_subject_binding=SimpleNamespace(
            space_id=1, status=1, gmt_deleted=None, subject_id="selected-subject",
            user=SimpleNamespace(status=1, gmt_deleted=None)))
        self.source_user = "selected-subject"
        with patch("apps.core.query.framework.resolve_space_agent_for_query", return_value=selected):
            result = query_framework(self.context, {**self.prepared, "scope": "personal"}, "langfuse")
            self.assertEqual(result["rows"][0]["input"], "input")
            self.assertEqual(len(self.calls), 2)
            self.calls.clear()
            self.batch_mode = "subject"
            with self.assertRaisesRegex(TraceQueryServiceError, "authorized scope"):
                query_framework(self.context, {**self.prepared, "scope": "personal"}, "langfuse")
            self.assertEqual(len(self.calls), 2)

    def test_content_batches_are_bounded_to_100_traces(self):
        template = self.details[0]["spans"][0]
        self.details = [{"traceId": f"trace-{i:03d}", "spans": [{**deepcopy(template),
            "trace_id": f"trace-{i:03d}", "span_id": f"span-{i:03d}"}]} for i in range(150)]
        result = query_framework(self.context, {**self.prepared, "params": {"agent_uuid": "agent", "limit": 150}}, "langfuse")
        self.assertEqual(len(result["rows"]), 150)
        self.assertEqual([row["span_id"] for row in result["rows"]][:2], ["span-149", "span-148"])
        self.assertEqual([len(json.loads(params["trace_ids"])) for path, params in self.calls
                          if path.endswith("/batch-snapshot")], [100, 50])

    def test_compact_generation_aba_restarts_raw_even_when_revision_and_span_count_match(self):
        self.batch_mode = 'generation_aba'
        result = query_framework(self.context, self.prepared, 'langfuse')
        self.assertEqual([row['span_id'] for row in result['rows']], ['span-0'])
        self.assertEqual(result['rows'][0]['input'], 'after-reset')
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(self.calls[-1][1]['raw'], 'true')

    def test_old_compact_backend_without_generation_uses_whole_raw_recovery(self):
        self.window_generation = None
        result = query_framework(self.context, self.prepared, 'langfuse')
        self.assertEqual([row['span_id'] for row in result['rows']], ['span-2'])
        self.assertEqual([path.rsplit('/', 1)[-1] for path, _ in self.calls], ['metrics-window', 'window'])
        self.assertEqual(self.calls[-1][1]['raw'], 'true')

    def test_invalid_compact_generation_declines_only_split_step_hydration(self):
        for generation in (True, 0, -1, '1'):
            self.calls.clear()
            self.window_generation = generation
            with self.subTest(generation=generation):
                result = query_framework(self.context, self.prepared, 'langfuse')
                self.assertEqual(result['rows'][0]['span_id'], 'span-2')
                self.assertEqual([path.rsplit('/', 1)[-1] for path, _ in self.calls], ['metrics-window', 'window'])

    def test_generation_proof_is_private_and_one_snapshot_metrics_do_not_require_it(self):
        from apps.core.trace_query.signoz.snapshot_window import query_snapshot_window
        from apps.core.trace_query.types import BaseTraceQuery
        query = BaseTraceQuery(self.context['space'], None, {'agent_uuid': 'agent', 'agent_product': 'langfuse',
                                                             'from': 1000, 'to': 5000})
        for generation in (1, None, True):
            self.window_generation = generation
            window, meta = query_snapshot_window(SignozTraceProvider(), query, include_metrics=True)
            self.assertEqual(window.metrics({})['traceCount'], 3)
            self.assertEqual(getattr(window, 'maintenance_generation', None), 1 if type(generation) is int else None)
            self.assertNotIn('maintenance_generation', meta)
            self.assertTrue(all('maintenance_generation' not in row for row in window.rows))
