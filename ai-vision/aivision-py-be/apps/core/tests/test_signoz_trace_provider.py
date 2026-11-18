import json
from threading import BoundedSemaphore, Event
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from apps.core.trace_query.signoz.provider import SignozTraceProvider
from apps.core.trace_query.types import (
    ObservationDetailQuery,
    SessionIOBatchQuery,
    SessionIOQuery,
    SessionUsageBatchQuery,
    SpanBatchQuery,
    TraceDetailQuery,
    TraceIOBatchQuery,
    TraceListQuery,
    TraceQueryServiceError,
    TraceUsageBatchQuery,
)


class SigNozTraceProviderTests(SimpleTestCase):
    def setUp(self):
        self.provider = SignozTraceProvider()
        self.space = SimpleNamespace(type=1, uuid="space-1")
        self.user = SimpleNamespace(observation_subject_id="10001")
        self.config = {
            "endpoint": "http://signoz-query:8080",
            "api_key": "test-key",
            "agent_product_filter": "Codex",
        }

    @staticmethod
    def _response(payload, status_code=200):
        response = Mock(status_code=status_code)
        response.json.return_value = payload
        response.headers = {}
        response.iter_content.return_value = [json.dumps(response.json.return_value).encode()]
        response.raise_for_status.side_effect = None
        return response

    @patch("apps.core.trace_query.signoz.provider.resolve_signoz_runtime_config")
    @patch("apps.core.trace_query.signoz.provider.requests.get")
    def test_mcp_span_scan_skips_unused_tool_summaries_and_preserves_scope(
        self, get, resolve_config
    ):
        resolve_config.return_value = self.config
        payload = {
            "spans": [{"trace_id": "trace-1", "span_id": "span-1"}],
            "spanMeta": {"page": 1, "limit": 500, "totalItems": 1, "totalPages": 1},
        }
        get.return_value = self._response(payload)

        result = self.provider.list_mcp_spans(
            TraceListQuery(
                self.space,
                self.user,
                {
                    "from": "2026-10-01T00:00:00+08:00",
                    "to": "2026-10-27T23:59:00+08:00",
                    "agent_uuid": "agent-1",
                    "page": 2,
                    "limit": 500,
                },
            )
        )

        self.assertEqual(result, payload)
        get.assert_called_once()
        args, kwargs = get.call_args
        self.assertTrue(args[0].endswith("/api/v1/ai-vision/mcp/tools"))
        params = kwargs["params"]
        self.assertEqual(params["spans_only"], "true")
        self.assertEqual(params["space_id"], "space-1")
        self.assertEqual(params["user"], "10001")
        self.assertEqual(params["agent_product"], "Codex")
        self.assertEqual(params["page"], 2)
        self.assertEqual(params["limit"], 500)
        self.assertEqual(
            json.loads(params["filters"]),
            [{"field": "resource.attributes.aivision.agent.id", "op": "=", "value": "agent-1"}],
        )

    @patch("apps.core.trace_query.signoz.provider.resolve_signoz_runtime_config")
    @patch("apps.core.trace_query.signoz.provider.requests.get")
    def test_memory_span_scan_preserves_scope_without_mcp_filter(
        self, get, resolve_config
    ):
        resolve_config.return_value = self.config
        payload = {
            "spans": [{"trace_id": "trace-1", "span_id": "span-1"}],
            "spanMeta": {"page": 1, "limit": 500, "totalItems": 1, "totalPages": 1},
        }
        get.return_value = self._response(payload)

        result = self.provider.list_memory_spans(
            TraceListQuery(
                self.space,
                self.user,
                {
                    "from": "2026-10-01T00:00:00+08:00",
                    "to": "2026-10-27T23:59:00+08:00",
                    "agent_uuid": "agent-1",
                    "page": 2,
                    "limit": 500,
                },
            )
        )

        self.assertEqual(result, payload)
        get.assert_called_once()
        args, kwargs = get.call_args
        self.assertTrue(args[0].endswith("/api/v1/ai-vision/memory/operations"))
        params = kwargs["params"]
        self.assertNotIn("spans_only", params)
        self.assertEqual(params["space_id"], "space-1")
        self.assertEqual(params["user"], "10001")
        self.assertEqual(params["agent_product"], "Codex")
        self.assertEqual(params["page"], 2)
        self.assertEqual(params["limit"], 500)
        self.assertEqual(
            json.loads(params["filters"]),
            [{"field": "resource.attributes.aivision.agent.id", "op": "=", "value": "agent-1"}],
        )

    @patch("apps.core.trace_query.signoz.provider.resolve_signoz_runtime_config")
    @patch("apps.core.trace_query.signoz.provider.requests.get")
    def test_list_traces_routes_and_scopes_query(self, get, resolve_config):
        resolve_config.return_value = self.config
        get.return_value = self._response(
            {
                "data": [{"trace_id": "abc"}],
                "meta": {"page": 1, "limit": 20, "totalItems": 1, "totalPages": 1},
            }
        )

        result = self.provider.list_traces(
            TraceListQuery(
                self.space,
                self.user,
                {
                    "from": "2026-09-09T05:00:00Z",
                    "to": "2026-09-09T05:01:00+00:00",
                    "page": 1,
                    "limit": 20,
                },
            )
        )

        self.assertEqual(result["data"][0]["trace_id"], "abc")
        _args, kwargs = get.call_args
        self.assertEqual(
            kwargs["params"],
            {
                "space_id": "space-1",
                "from": 1788930000000,
                "to": 1788930060000,
                "page": 1,
                "limit": 20,
                "agent_product": "Codex",
                "user": "10001",
            },
        )
        self.assertEqual(kwargs["headers"]["SIGNOZ-API-KEY"], "test-key")

    @patch("apps.core.trace_query.signoz.provider.resolve_signoz_runtime_config")
    @patch("apps.core.trace_query.signoz.provider.requests.get")
    def test_trace_search_contract_is_forwarded(self, get, resolve_config):
        resolve_config.return_value = self.config
        get.return_value = self._response({"data": [], "meta": {}})

        self.provider.list_traces(
            TraceListQuery(
                self.space,
                self.user,
                {"searchType": "traceId", "keyword": "0123456789abcdef"},
            )
        )

        _args, kwargs = get.call_args
        self.assertEqual(kwargs["params"]["searchType"], "traceId")
        self.assertEqual(kwargs["params"]["keyword"], "0123456789abcdef")

    @patch("apps.core.trace_query.signoz.provider.resolve_signoz_runtime_config")
    @patch("apps.core.trace_query.signoz.provider.requests.get")
    def test_list_traces_forwards_filters_fields_order_and_facets_dimensions(
        self, get, resolve_config
    ):
        resolve_config.return_value = self.config
        get.return_value = self._response({"data": [], "meta": {}})
        filters = [
            {
                "field": "trace_id",
                "op": "in",
                "value": ["trace-1", "trace-2"],
            }
        ]

        self.provider.list_traces(
            TraceListQuery(
                self.space,
                self.user,
                {
                    "fields": ["trace_id", "attributes.gen_ai.request.model"],
                    "filters": filters,
                    "orderBy": "start_time_unix_nano.desc",
                    "searchType": "input",
                    "keyword": "prompt",
                    "input_keyword": "fuzzy",
                    "tags": ["sdk", "agent"],
                    "env": "prod",
                    "scene": "coding",
                    "username": "alice",
                },
            )
        )

        params = get.call_args.kwargs["params"]
        self.assertEqual(json.loads(params["fields"])[0], "trace_id")
        self.assertEqual(json.loads(params["filters"]), filters)
        self.assertEqual(json.loads(params["tags"]), ["sdk", "agent"])
        self.assertEqual(params["orderBy"], "start_time_unix_nano.desc")
        self.assertEqual(params["searchType"], "input")
        self.assertEqual(params["keyword"], "prompt")
        self.assertEqual(params["input_keyword"], "fuzzy")
        self.assertEqual(params["env"], "prod")
        self.assertEqual(params["scene"], "coding")
        self.assertEqual(params["username"], "alice")

    @patch.object(SignozTraceProvider, "collect_traces")
    def test_list_facets_uses_complete_authorized_window(self, collect):
        collect.return_value = {"data": [{"traceId": "t", "spans": [{
            "span_id": "s", "name": "root", "start_time_unix_nano": "1788930000000000000",
            "resource": {"ant.agent.env": "prod"}, "attributes": {},
        }]}], "meta": {"complete": True, "totalItems": 1}}

        # The legacy server has no optional snapshot endpoint. Keep the real
        # capability negotiation while isolating its external route and HTTP.
        with (
            patch("apps.core.trace_query.signoz.provider.resolve_signoz_runtime_config", return_value=self.config),
            patch("apps.core.trace_query.signoz.provider.requests.get", return_value=self._response({}, status_code=404)),
        ):
            result = self.provider.list_facets(
                TraceListQuery(self.space, self.user, {"env": "prod"})
            )

        self.assertEqual(result["data"]["env"], [{"value": "prod", "count": 1}])
        self.assertTrue(result["meta"]["complete"])
        self.assertEqual(collect.call_args.args[0].space, self.space)

    @patch("apps.core.trace_query.signoz.provider.resolve_signoz_runtime_config")
    @patch("apps.core.trace_query.signoz.provider.requests.get")
    def test_usage_reads_authorized_spans_and_preserves_reported_total(self, get, resolve_config):
        resolve_config.return_value = self.config
        get.return_value = self._response({"traceId": "abc", "spans": [{
            "trace_id": "abc", "span_id": "p1", "name": "call", "start_time_unix_nano": "1788930000000000000",
            "resource": {"aivision.space.id": "space-1"},
            "attributes": {"langfuse.observation.type": "GENERATION", "gen_ai.usage.input_tokens": 12,
                           "gen_ai.usage.output_tokens": 5, "gen_ai.usage.total_tokens": 20},
        }]})
        detail_response = get.return_value
        get.side_effect = [detail_response, self._response({"data": [{"trace_id": "abc", "span_count": 1}]})]
        result = self.provider.get_trace_usage_batch(TraceUsageBatchQuery(self.space, self.user,
            {"from": "1788930000000", "to": "1788930060000"}, ["abc", "abc"]))
        self.assertEqual(result["data"][0]["totalTokens"], 20)
        self.assertEqual(result["data"][0]["inputTokens"], 12)
        self.assertEqual(result["data"][0]["outputTokens"], 5)
        self.assertEqual(get.call_count, 2)
        self.assertEqual(get.call_args.kwargs["params"]["space_id"], "space-1")
        self.assertEqual(get.call_args.kwargs["params"]["from"], 1788930000000)

    @patch.object(SignozTraceProvider, "collect_traces")
    @patch.object(SignozTraceProvider, "get_trace_detail")
    def test_io_usage_session_and_observation_share_logical_root(self, detail, collect):
        value = {"traceId": "t1", "spans": [
            {"trace_id": "t1", "span_id": "root", "name": "request", "start_time_unix_nano": "1788930000000000000",
             "attributes": {"langfuse.observation.type": "AGENT", "langfuse.session.id": "s1",
                            "langfuse.observation.input": '{"question":"in"}', "langfuse.observation.output": '{"answer":"out"}'}},
            {"trace_id": "t1", "span_id": "p1", "parent_span_id": "root", "name": "child", "start_time_unix_nano": "1788930000001000000",
             "attributes": {"langfuse.observation.type": "GENERATION", "gen_ai.usage.input_tokens": 1,
                            "gen_ai.usage.output_tokens": 2, "gen_ai.usage.total_tokens": 3}},
        ]}
        detail.return_value = value
        collect.return_value = {"data": [value], "meta": {"complete": True}}
        io = self.provider.get_trace_io_batch(TraceIOBatchQuery(self.space, self.user, {}, ["t1"]))
        with (
            patch("apps.core.trace_query.signoz.provider.resolve_signoz_runtime_config", return_value=self.config),
            patch("apps.core.trace_query.signoz.provider.requests.get", return_value=self._response({}, status_code=404)),
        ):
            session_io = self.provider.get_session_io_batch(SessionIOBatchQuery(self.space, self.user, {}, ["s1"]))
            usage = self.provider.get_session_usage_batch(SessionUsageBatchQuery(self.space, self.user, {}, ["s1"]))
        self.assertEqual(json.loads(io["data"][0]["output"]), {"answer": "out"})
        self.assertEqual(session_io["data"][0]["output"], io["data"][0]["output"])
        self.assertEqual(usage["data"][0]["totalTokens"], 3)
        observation = self.provider.get_observation_detail(ObservationDetailQuery(self.space, self.user, {}, "p1", "t1"))
        self.assertEqual(observation["span_id"], "p1")

    @patch("apps.core.trace_query.signoz.provider.resolve_signoz_runtime_config")
    @patch("apps.core.trace_query.signoz.provider.requests.get")
    def test_trace_io_batch_fits_one_upstream_slot_and_preserves_order(self, get, resolve_config):
        resolve_config.return_value = self.config
        # A simultaneous Session read already occupies the other SigNoz slot.
        available = BoundedSemaphore(1)
        contended = Event()
        first = True

        def respond(url, *, params, **kwargs):
            nonlocal first
            if not available.acquire(blocking=False):
                contended.set()
                return self._response({}, status_code=429)
            if first:
                first = False
                # Hold the first read open long enough for a parallel batch to
                # compete for the remaining slot. Serial reads can finish.
                contended.wait(timeout=0.5)
            if url.endswith("/traces"):
                payload = {"data": [{"trace_id": params["trace_id"], "span_count": 1}]}
            else:
                trace_id = url.rsplit("/", 1)[-1]
                payload = {"traceId": trace_id, "spans": [{
                    "trace_id": trace_id, "span_id": "root", "name": "turn",
                    "start_time_unix_nano": "1788930000000000000",
                    "end_time_unix_nano": "1788930001000000000",
                    "resource": {"aivision.space.id": "space-1"},
                    "attributes": {
                        "langfuse.observation.type": "AGENT",
                        "langfuse.observation.input": json.dumps({"question": trace_id}),
                        "langfuse.observation.output": json.dumps({"answer": trace_id}),
                    },
                }]}
            response = self._response(payload)
            response.close.side_effect = available.release
            return response

        get.side_effect = respond
        requested = ["third", "first", "second", "first"]
        result = self.provider.get_trace_io_batch(TraceIOBatchQuery(self.space, self.user, {}, requested))

        self.assertEqual(get.call_count, 6)
        self.assertFalse(contended.is_set())
        self.assertEqual(
            [(row["id"], row["inputValue"], row["outputValue"]) for row in result["data"]],
            [(trace_id, {"question": trace_id}, {"answer": trace_id}) for trace_id in requested[:3]],
        )

    @patch("apps.core.trace_query.signoz.provider.resolve_signoz_runtime_config")
    def test_missing_space_id_is_rejected_before_network_call(self, resolve_config):
        resolve_config.return_value = self.config
        with self.assertRaisesRegex(TraceQueryServiceError, "space_id is required"):
            self.provider.list_traces(
                TraceListQuery(
                    SimpleNamespace(type=1, uuid=""),
                    self.user,
                    {},
                )
            )

    @patch("apps.core.trace_query.signoz.provider.resolve_signoz_runtime_config")
    @patch("apps.core.trace_query.signoz.provider.requests.get")
    def test_trace_detail_unwraps_signoz_success_envelope(self, get, resolve_config):
        resolve_config.return_value = self.config
        get.return_value = self._response(
            {
                "status": "success",
                "data": {"traceId": "abc", "spans": []},
            }
        )

        result = self.provider.get_trace_detail(
            TraceDetailQuery(self.space, self.user, {}, "abc")
        )

        self.assertEqual(result, {"traceId": "abc", "spans": []})

    @patch.object(SignozTraceProvider, "_context")
    def test_detail_converges_when_ingestion_advances_between_detail_and_count(self, context):
        root = {"trace_id": "abc", "span_id": "root", "name": "root",
                "start_time_unix_nano": "1788930000000000000",
                "end_time_unix_nano": "1788930001000000000",
                "resource": {"aivision.space.id": "space-1"}, "attributes": {}}
        child = {**root, "span_id": "child", "parent_span_id": "root"}
        client = Mock()
        context.return_value = client, {}
        client.get.side_effect = [
            {"traceId": "abc", "spans": [root]},
            {"data": [{"trace_id": "abc", "span_count": 2}]},
            {"traceId": "abc", "spans": [root, child]},
            {"data": [{"trace_id": "abc", "span_count": 2}]},
        ]
        result = self.provider.get_trace_detail(TraceDetailQuery(self.space, self.user, {}, "abc"))
        self.assertEqual(result["spans"], [root, child])
        self.assertTrue(result["meta"]["complete"])
        self.assertEqual(result["meta"]["expectedSpanCount"], 2)
        self.assertEqual(client.get.call_count, 4)

    @patch.object(SignozTraceProvider, "_context")
    def test_detail_growth_is_bounded_and_rejects_changes_or_bad_metadata(self, context):
        from copy import deepcopy
        root = {"trace_id": "abc", "span_id": "root", "name": "root",
                "start_time_unix_nano": "1788930000000000000",
                "resource": {"aivision.space.id": "space-1"}, "attributes": {}}
        detail = {"traceId": "abc", "spans": [root]}
        count = lambda n: {"data": [{"trace_id": "abc", "span_count": n}]}
        query = TraceDetailQuery(self.space, self.user, {}, "abc")
        for mutation in ('never_complete', 'changed_content', 'foreign_space', 'lost_span',
                         'duplicate_id', 'bad_count', 'shrinking_count', 'shrinking_after_growth', 'transport'):
            with self.subTest(mutation=mutation):
                client = Mock()
                context.return_value = client, {}
                later = deepcopy(detail)
                later['spans'].append({**deepcopy(root), 'span_id': 'child'})
                responses = [deepcopy(detail), count(2), later, count(2)]
                expected_calls = 3
                if mutation == 'never_complete':
                    responses = [deepcopy(detail), count(2)] * 3
                    expected_calls = 6
                elif mutation == 'changed_content':
                    later['spans'][0]['attributes']['gen_ai.output.messages'] = 'changed'
                elif mutation == 'foreign_space':
                    later['spans'][1]['resource'] = {'aivision.space.id': 'other-space'}
                elif mutation == 'lost_span':
                    later['spans'].pop(0)
                elif mutation == 'duplicate_id':
                    responses = [{'spans': [root, root]}, count(3)]
                    expected_calls = 2
                elif mutation == 'bad_count':
                    responses = [detail, count(True)]
                    expected_calls = 2
                elif mutation == 'shrinking_count':
                    responses = [detail, count(0)]
                    expected_calls = 2
                elif mutation == 'shrinking_after_growth':
                    responses = [detail, count(2), deepcopy(detail), count(1)]
                    expected_calls = 4
                elif mutation == 'transport':
                    responses[2] = RuntimeError('upstream unavailable')
                client.get.side_effect = responses
                with self.assertRaises((TraceQueryServiceError, RuntimeError)):
                    self.provider.get_trace_detail(query)
                self.assertEqual(client.get.call_count, expected_calls)


class MemoryCapabilityTests(SimpleTestCase):
    def test_old_data_plane_is_explicitly_unsupported(self):
        provider = SignozTraceProvider()
        client = Mock()
        client.get.side_effect = TraceQueryServiceError('not found', 3019)
        with patch.object(provider, '_context', return_value=(client, {})), patch.object(provider, '_params', return_value={}):
            with self.assertRaises(TraceQueryServiceError) as raised:
                provider.list_memory_spans(TraceListQuery(None, None, {}))
        self.assertEqual(raised.exception.code, 3011)
        self.assertIn('native memory support', str(raised.exception))
