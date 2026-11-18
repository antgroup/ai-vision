from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, TestCase

from apps.core.query.errors import AgentQueryInvalidPayloadError
from apps.core.query.framework import query_framework, FRAMEWORK_QUERY_KEYS
from apps.core.trace_query.types import TraceQueryServiceError
from apps.core.views.v2_query import AgentDashboardQueryView


def native_window(count=3):
    spans = []
    for index in range(count):
        spans.append({
            "span_id": str(index), "trace_id": "t", "name": f"operation-{index}",
            "start_time_unix_nano": str((1000 + index) * 1000000),
            "end_time_unix_nano": str((1001 + index) * 1000000),
            "attributes": {"langfuse.observation.type": "TOOL" if index % 2 else "GENERATION",
                           "gen_ai.usage.input_tokens": 0 if index == 0 else None,
                           "gen_ai.request.model": "test-model"},
        })
    spans.append({"span_id": "root", "name": "wrapper", "start_time_unix_nano": "1000000000", "attributes": {}})
    return {"data": [{"traceId": "t", "spans": spans}], "meta": {"complete": True}}


class FrameworkNativeDashboardTests(SimpleTestCase):
    def setUp(self):
        # These cases retain coverage of the old-server compatibility path.
        # Complete snapshot transport and subject validation are exercised in
        # test_framework_snapshot with the actual provider implementation.
        self.enterContext(patch("apps.core.query.framework.query_snapshot_window", return_value=None))
        self.enterContext(patch('apps.core.trace_query.signoz.statistics_aggregate.query_metrics_aggregate', return_value=None))
        self.enterContext(patch('apps.core.trace_query.signoz.operation_page.query_operation_page', return_value=None))
        self.context = {"space": SimpleNamespace(uuid="space", type=2), "user": object(),
                        "agent": SimpleNamespace(uuid="agent"), "agent_type": SimpleNamespace(display_scope="ALL"),
                        "ds_config": {}, "user_id": "subject"}
        self.prepared = {"query_key": "framework.overview.event_stream", "from_ms": 1000,
                         "to_ms": 5000, "params": {"agent_uuid": "agent", "limit": 200, "time_step": "PT1H"}}

    @patch("apps.core.query.framework.SignozTraceProvider.collect_traces")
    def test_stream_uses_canonical_spans_and_preserves_zero_unknown_and_scope(self, window):
        window.return_value = native_window()
        result = query_framework(self.context, self.prepared, "langfuse")
        self.assertEqual([r["span_id"] for r in result["rows"]], ["2", "1", "0"])
        self.assertEqual(result["rows"][0]["kind"], "llm")
        self.assertIsNone(result["rows"][0]["input_tokens"])
        self.assertEqual(result["rows"][-1]["input_tokens"], 0)
        self.assertIn({"key": "time", "type": "string"}, result["columns"])
        query = window.call_args.args[0]
        self.assertIs(query.space, self.context["space"])
        self.assertIs(query.user, self.context["user"])
        self.assertEqual(query.query_params, {"from": 1000, "to": 5000, "agent_product": "langfuse", "agent_uuid": "agent"})

    @patch("apps.core.query.framework.SignozTraceProvider.collect_traces")
    def test_timeseries_counts_full_window_before_stream_limit(self, window):
        window.return_value = native_window(251)
        stream = query_framework(self.context, self.prepared, "langfuse")
        trend = query_framework(self.context, {**self.prepared, "query_key": "framework.events.timeseries"}, "langfuse")
        self.assertEqual(len(stream["rows"]), 200)
        self.assertEqual(sum(r["count"] for r in trend["rows"]), 251)
        self.assertEqual({r["kind"] for r in trend["rows"]}, {"llm", "tool"})

    @patch("apps.core.query.framework.SignozTraceProvider.collect_traces")
    def test_incomplete_scan_and_bad_parameters_fail_instead_of_empty_success(self, window):
        window.return_value = {**native_window(), "meta": {"complete": False}}
        with self.assertRaisesRegex(TraceQueryServiceError, "[Ii]ncomplete"):
            query_framework(self.context, self.prepared, "langfuse")
        for params in ({"limit": 201}, {"time_step": "garbage"}, {"time_step": []}, {"rawSql": "SELECT 1"}):
            with self.assertRaises(AgentQueryInvalidPayloadError):
                query_framework(self.context, {**self.prepared, "params": params}, "langfuse")

    @patch("apps.core.query.framework.SignozTraceProvider.collect_traces")
    def test_span_timestamps_obey_inclusive_start_exclusive_end(self, window):
        window.return_value = native_window()
        result = query_framework(self.context, {**self.prepared, "to_ms": 1002}, "langfuse")
        self.assertEqual([r["span_id"] for r in result["rows"]], ["1", "0"])

    @patch("apps.core.query.framework.SignozTraceProvider.collect_traces")
    def test_personal_scope_keeps_agent_subject_even_in_business_space(self, window):
        window.return_value = native_window()
        agent = SimpleNamespace(uuid="agent", agent_product="langfuse", space_id=1,
            agent_type=SimpleNamespace(agent_product="langfuse", agent_name="langfuse"),
            observation_subject_binding=SimpleNamespace(space_id=1, status=1, gmt_deleted=None,
                subject_id="subject", user=SimpleNamespace(status=1, gmt_deleted=None)))
        with patch("apps.core.services.agent_routing.Agent.objects.select_related") as database:
            database.return_value.filter.return_value.first.return_value = agent
            query_framework(self.context, {**self.prepared, "scope": "personal"}, "langfuse")
        query = window.call_args.args[0]
        self.assertEqual(query.query_params["username"], "subject")
        self.assertEqual(query.query_params["agent_uuid"], "agent")

    @patch("apps.core.query.framework.SignozTraceProvider.collect_traces")
    def test_scan_capacity_error_is_visible_through_v2(self, window):
        from apps.core.query.errors import AgentQueryExecutionFailedError
        window.side_effect = TraceQueryServiceError("Trace scan limit is 10000; narrow the time range", 3027)
        with self.assertRaisesRegex(AgentQueryExecutionFailedError, "10000"):
            AgentDashboardQueryView._run_query_payload(
                payload={"scope": "space", "queryKey": self.prepared["query_key"],
                         "timeRange": {"type": "absolute", "from": 1000, "to": 5000}},
                context=self.context, agent_product="langfuse", space_id="space", is_encrypted=False,
            )

    @patch.object(AgentDashboardQueryView, "_execute_query")
    @patch("apps.core.query.framework.SignozTraceProvider.collect_traces")
    def test_v2_dispatch_bypasses_unsupported_upstream_named_queries(self, window, upstream):
        window.return_value = native_window()
        for key in FRAMEWORK_QUERY_KEYS:
            result = AgentDashboardQueryView._run_query_payload(
                payload={"scope": "space", "queryKey": key, "timeRange": {"type": "absolute", "from": 1000, "to": 5000},
                         "params": self.prepared["params"]}, context=self.context, agent_product="langfuse",
                space_id="space", is_encrypted=False,
            )
            self.assertTrue(result["rows"])
        upstream.assert_not_called()


class FrameworkSelectedSubjectTests(SimpleTestCase):
    def setUp(self):
        self.enterContext(patch("apps.core.query.framework.query_snapshot_window", return_value=None))
        self.enterContext(patch('apps.core.trace_query.signoz.statistics_aggregate.query_metrics_aggregate', return_value=None))
        self.enterContext(patch('apps.core.trace_query.signoz.operation_page.query_operation_page', return_value=None))
        self.space = SimpleNamespace(uuid="space", pk=1, type=2)
        self.kind = SimpleNamespace(display_scope="ALL", agent_product="langfuse", agent_name="langfuse")
        def agent(identifier, subject, space_id=1):
            return SimpleNamespace(uuid=identifier, space_id=space_id, agent_product="langfuse", agent_type=self.kind,
                observation_subject_binding=SimpleNamespace(space_id=space_id, status=1, gmt_deleted=None,
                    subject_id=subject, user=SimpleNamespace(status=1, gmt_deleted=None)))
        self.a = agent("agent-a", "subject-a")
        self.b = agent("agent-b", "subject-b")
        self.foreign = agent("agent-foreign", "subject-foreign", space_id=2)
        self.ds = SimpleNamespace(config={"endpoint": "http://fixture.invalid", "agent_product_filter": "langfuse"})

    def context(self):
        from apps.core.views import v2_query as views
        with patch.object(views.Space.objects, "get", return_value=self.space), \
             patch.object(views, "check_space_access_or_error", return_value=None), \
             patch.object(views, "list_routable_space_agents", return_value=[self.a, self.b]), \
             patch.object(views, "_find_space_agent_signoz_datasource", return_value=self.ds):
            return views.AgentDashboardQueryView._resolve_query_context(
                user=object(), space_id="space", agent_product="langfuse")

    def selection(self, **filters):
        # Only the database boundary is replaced: the real shared resolver
        # still constructs Space/status/product checks and rejects bad IDs.
        self.assertIs(filters["space"], self.space)
        self.assertEqual(filters["status__in"], (1, 2))
        selected = {"agent-a": self.a, "agent-b": self.b, "agent-foreign": self.foreign, "other-product": SimpleNamespace(
            space_id=1, agent_product="codex", agent_type=SimpleNamespace(agent_product="codex", agent_name="codex"))}.get(filters["uuid"])
        if selected and selected.space_id != filters["space"].pk:
            selected = None
        return Mock(first=Mock(return_value=selected))

    def test_both_personal_queries_bind_selected_agent_at_provider_boundary(self):
        from apps.core.services.agent_routing import Agent
        from apps.core.trace_query.signoz.provider import SignozTraceProvider
        for space_type in (1, 2):
            self.space.type = space_type
            context = self.context()
            self.assertEqual(context["user_id"], "subject-a")
            for key in FRAMEWORK_QUERY_KEYS:
                captured = []
                def collect(provider, query, **kwargs):
                    captured.append(SignozTraceProvider._params(query, self.ds.config))
                    return native_window()
                with self.subTest(space_type=space_type, key=key), \
                     patch.object(Agent.objects, "select_related") as database, \
                     patch.object(SignozTraceProvider, "collect_traces", collect):
                    database.return_value.filter.side_effect = self.selection
                    query_framework(context, {"query_key": key, "scope": "personal", "from_ms": 1000,
                        "to_ms": 5000, "params": {"agent_uuid": "agent-b"}}, "langfuse")
                    self.assertEqual(captured[0]["user"], "subject-b")
                    self.assertIn('"value": "agent-b"', captured[0]["filters"])

    def test_selected_agent_invalid_binding_is_not_replaced_by_default_subject(self):
        from apps.core.services.agent_routing import Agent
        from apps.core.services.observation_subject_service import ObservationSubjectError
        from apps.core.trace_query.signoz.provider import SignozTraceProvider
        context = self.context()
        self.b.observation_subject_binding.status = 0
        with patch.object(Agent.objects, "select_related") as database, \
             patch.object(SignozTraceProvider, "collect_traces") as storage:
            database.return_value.filter.side_effect = self.selection
            with self.assertRaises(ObservationSubjectError):
                query_framework(context, {"query_key": "framework.overview.event_stream", "scope": "personal",
                    "from_ms": 1000, "to_ms": 5000, "params": {"agent_uuid": "agent-b"}}, "langfuse")
            storage.assert_not_called()

    def test_query_context_preserves_space_access_rejection(self):
        from apps.core.views import v2_query as views
        denied = object()
        with patch.object(views.Space.objects, "get", return_value=self.space), \
             patch.object(views, "check_space_access_or_error", return_value=denied), \
             patch.object(views, "list_routable_space_agents") as selection:
            self.assertIs(views.AgentDashboardQueryView._resolve_query_context(
                user=object(), space_id="space", agent_product="langfuse"), denied)
            selection.assert_not_called()

    def test_invalid_and_cross_space_selection_are_rejected_before_storage(self):
        from apps.core.services.agent_routing import Agent
        from apps.core.trace_query.signoz.provider import SignozTraceProvider
        context = self.context()
        for key in FRAMEWORK_QUERY_KEYS:
            for identifier in ("missing", "agent-foreign", "other-product"):
                with self.subTest(key=key, identifier=identifier), \
                     patch.object(Agent.objects, "select_related") as database, \
                     patch.object(SignozTraceProvider, "collect_traces", return_value=native_window()) as storage:
                    database.return_value.filter.side_effect = self.selection
                    with self.assertRaisesRegex(TraceQueryServiceError, "route not found"):
                        query_framework(context, {"query_key": key, "scope": "personal", "from_ms": 1000,
                            "to_ms": 5000, "params": {"agent_uuid": identifier}}, "langfuse")
                    storage.assert_not_called()


class FrameworkNativeScopeTests(TestCase):
    def test_other_space_agent_cannot_override_native_dashboard_scope(self):
        from apps.core.models import AgentTypeConfig, DataSource, Space
        from apps.core.tests.helpers import create_local_user, create_test_agent

        user = create_local_user("framework-scope")
        space = Space.objects.create(name="owned", type=2, creator=user)
        other = Space.objects.create(name="other", type=2, creator=user)
        kind = AgentTypeConfig.objects.create(agent_name="framework-scope", agent_product="langfuse")
        agent = create_test_agent(space=space, agent_type=kind, agent_product="langfuse", name="own", status=1)
        foreign = create_test_agent(space=other, agent_type=kind, agent_product="langfuse", name="other", status=1)
        DataSource.objects.create(agent_type=kind, datasource_type="traces", source_system="signoz", enabled=1,
                                  config={"endpoint": "http://must-not-contact.invalid", "api_key": "fixture"})
        context = {"space": space, "agent": agent, "user": user}
        with patch("apps.core.trace_query.signoz.provider.requests.get") as network:
            with self.assertRaisesRegex(TraceQueryServiceError, "route not found"):
                query_framework(context, {"query_key": "framework.overview.event_stream", "from_ms": 1000,
                                          "to_ms": 2000, "params": {"agent_uuid": str(foreign.uuid)}}, "langfuse")
        network.assert_not_called()
