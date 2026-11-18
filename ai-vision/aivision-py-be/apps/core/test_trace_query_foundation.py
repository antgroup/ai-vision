import json
from datetime import datetime
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests as http_requests
from django.test import SimpleTestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.services.trace_query_service import (
    LangfuseTraceProvider,
    ObservationDetailQuery,
    SessionDetailQuery,
    SessionListQuery,
    SessionUsageBatchQuery,
    TraceDetailQuery,
    TraceIOBatchQuery,
    TraceUsageBatchQuery,
    TraceListQuery,
    TraceProviderNotConfiguredError,
    TraceProviderRegistry,
    TraceQueryService,
    TraceQueryServiceError,
)
from apps.core.trace_query.semantics.tree_linking import (
    normalize_observation_tree_links,
    normalize_trace_detail_tree_links,
)
from apps.core.trace_query.routing import _select_route_record
from apps.core.trace_query.trace_id import (
    TRACE_ID_KIND_COMPOSITE_SOFA,
    TRACE_ID_KIND_OTEL,
    TRACE_ID_KIND_SOFA,
    is_composite_sofa_trace_id_format,
    is_sofa_trace_id_format,
    normalize_trace_id_for_query,
)
from apps.core.views import (
    ObservationDetailView,
    SessionDetailView,
    SessionListView,
    TraceDetailView,
    TraceIOBatchView,
    TraceUsageBatchView,
    TraceListView,
)
from apps.core.views.trace_unified import UnifiedSessionUsageBatchView


class DummyProvider:
    def list_traces(self, query):
        return query

    def get_trace_detail(self, query):
        return query

    def get_trace_io_batch(self, query):
        return query

    def get_trace_usage_batch(self, query):
        return query

    def list_sessions(self, query):
        return query

    def get_session_detail(self, query):
        return query

    def get_observation_detail(self, query):
        return query


class TraceQueryFoundationTests(SimpleTestCase):
    def test_normalize_trace_id_converts_valid_sofa_trace_id_to_otel_hex(self):
        normalized = normalize_trace_id_for_query("0ad1348f140316927500210031234")

        self.assertTrue(normalized.is_sofa_trace_id)
        self.assertEqual(normalized.kind, TRACE_ID_KIND_SOFA)
        self.assertEqual(
            normalized.query_trace_id,
            "0146b367fc7a0ad1348f000004d203eb",
        )
        self.assertEqual(
            normalized.period_from.isoformat(),
            "2014-06-19T09:14:35.002000+00:00",
        )
        self.assertEqual(
            normalized.period_to.isoformat(),
            "2014-06-19T10:14:35.002000+00:00",
        )

    def test_normalize_trace_id_accepts_31_char_sofa_trace_id(self):
        trace_id = "21b6527517853080088991120363895"

        normalized = normalize_trace_id_for_query(trace_id)

        self.assertTrue(is_sofa_trace_id_format(trace_id))
        self.assertTrue(normalized.is_sofa_trace_id)
        self.assertEqual(normalized.kind, TRACE_ID_KIND_SOFA)
        self.assertEqual(
            normalized.query_trace_id,
            "019faca651c321b6527500058d770460",
        )

    def test_sofa_trace_id_rejects_values_longer_than_31_chars(self):
        self.assertFalse(is_sofa_trace_id_format("21b65275178530800889911203638950"))

    def test_normalize_trace_id_keeps_plain_trace_id_with_no_time_window(self):
        normalized = normalize_trace_id_for_query("plain-trace-id")

        self.assertFalse(normalized.is_sofa_trace_id)
        self.assertEqual(normalized.query_trace_id, "plain-trace-id")
        self.assertIsNone(normalized.period_from)
        self.assertIsNone(normalized.period_to)

    def test_normalize_trace_id_marks_standard_otel_id_without_changing_it(self):
        trace_id = "019f5aa0685021bbce6a0000045a0e62"

        normalized = normalize_trace_id_for_query(trace_id)

        self.assertFalse(normalized.is_sofa_trace_id)
        self.assertEqual(normalized.kind, TRACE_ID_KIND_OTEL)
        self.assertEqual(normalized.query_trace_id, trace_id)
        self.assertIsNone(normalized.period_from)
        self.assertIsNone(normalized.period_to)

    def test_normalize_trace_id_parses_composite_sofa_trace_id(self):
        trace_id = "21bbce6a178393188974436821114_4_1783932020482"

        normalized = normalize_trace_id_for_query(trace_id)

        self.assertTrue(is_composite_sofa_trace_id_format(trace_id))
        self.assertTrue(normalized.is_sofa_trace_id)
        self.assertEqual(normalized.kind, TRACE_ID_KIND_COMPOSITE_SOFA)
        self.assertEqual(normalized.query_trace_id, trace_id)
        self.assertEqual(
            normalized.query_column_candidates,
            (
                "attributes._internal.sofa.trace_id",
                "attributes.__meta__.traceId",
            ),
        )
        self.assertEqual(
            normalized.base_sofa_trace_id,
            "21bbce6a178393188974436821114",
        )
        self.assertEqual(normalized.composite_round, "4")
        self.assertEqual(
            normalized.period_from.isoformat(),
            "2026-07-13T07:40:20.482000+00:00",
        )
        self.assertEqual(
            normalized.period_to.isoformat(),
            "2026-07-13T09:40:20.482000+00:00",
        )

    def test_normalize_trace_id_parses_composite_with_31_char_sofa_base(self):
        base_trace_id = "21b6527517853080088991120363895"
        trace_id = f"{base_trace_id}_2_1785308060000"

        normalized = normalize_trace_id_for_query(trace_id)

        self.assertTrue(is_composite_sofa_trace_id_format(trace_id))
        self.assertTrue(normalized.is_sofa_trace_id)
        self.assertEqual(normalized.kind, TRACE_ID_KIND_COMPOSITE_SOFA)
        self.assertEqual(normalized.base_sofa_trace_id, base_trace_id)
        self.assertEqual(normalized.composite_round, "2")

    def test_invalid_composite_sofa_trace_id_keeps_plain_behavior(self):
        invalid_values = (
            "not-sofa_4_1783932020482",
            "21bbce6a178393188974436821114_round_1783932020482",
            "21bbce6a178393188974436821114_4_178393202048",
        )

        for trace_id in invalid_values:
            with self.subTest(trace_id=trace_id):
                normalized = normalize_trace_id_for_query(trace_id)
                self.assertFalse(is_composite_sofa_trace_id_format(trace_id))
                self.assertFalse(normalized.is_sofa_trace_id)
                self.assertEqual(normalized.query_trace_id, trace_id)
                self.assertIsNone(normalized.period_from)
                self.assertIsNone(normalized.period_to)

    def test_sofa_trace_id_format_matches_pr6_shape_but_conversion_uses_python_rules(
        self,
    ):
        trace_id_without_pid = "0ad1348f14031692750021003"

        self.assertTrue(is_sofa_trace_id_format(trace_id_without_pid))
        normalized = normalize_trace_id_for_query(trace_id_without_pid)
        self.assertFalse(normalized.is_sofa_trace_id)
        self.assertEqual(normalized.query_trace_id, trace_id_without_pid)
        self.assertIn("pid part is empty", normalized.error or "")
        self.assertFalse(is_sofa_trace_id_format("a96f5468ce5132f74288426c81a324cc"))

    def test_registry_returns_registered_provider(self):
        registry = TraceProviderRegistry()
        provider = DummyProvider()

        registry.register("langfuse", provider, source_systems=("langfuse",))

        self.assertTrue(registry.has("langfuse"))
        self.assertIs(registry.get("langfuse"), provider)
        self.assertEqual(
            registry.get_descriptor("langfuse").source_systems,
            ("langfuse",),
        )
        self.assertEqual(registry.find_name_by_source_system("langfuse"), "langfuse")

    def test_route_selection_prefers_default_datasource(self):
        selected = _select_route_record(
            [
                SimpleNamespace(source_system="langfuse", is_default=0),
                SimpleNamespace(source_system="signoz", is_default=1),
            ],
            error_message="multiple routes",
        )

        self.assertEqual(selected.source_system, "signoz")

    def test_route_selection_rejects_multiple_source_without_default(self):
        with self.assertRaises(TraceProviderNotConfiguredError):
            _select_route_record(
                [
                    SimpleNamespace(source_system="langfuse", is_default=0),
                    SimpleNamespace(source_system="signoz", is_default=0),
                ],
                error_message="multiple routes",
            )

    @patch("apps.core.trace_query.service.resolve_trace_source_system")
    def test_trace_query_service_uses_resolved_source_system(
        self, mock_resolve_source_system
    ):
        registry = TraceProviderRegistry()
        langfuse_provider = Mock()
        signoz_provider = Mock()
        signoz_provider.list_traces.return_value = {"provider": "signoz"}
        registry.register(
            "langfuse",
            langfuse_provider,
            source_systems=("langfuse",),
        )
        registry.register(
            "signoz",
            signoz_provider,
            source_systems=("signoz",),
        )
        service = TraceQueryService(registry)
        mock_resolve_source_system.return_value = "signoz"

        result = service.list_traces(
            space=SimpleNamespace(type=2),
            user=SimpleNamespace(observation_subject_id="w123"),
            query_params={},
        )

        self.assertEqual(result, {"provider": "signoz"})
        signoz_provider.list_traces.assert_called_once()
        langfuse_provider.list_traces.assert_not_called()

    @patch("apps.core.trace_query.service.resolve_trace_source_system")
    def test_trace_query_service_falls_back_to_langfuse_without_datasource(
        self,
        mock_resolve_source_system,
    ):
        registry = TraceProviderRegistry()
        langfuse_provider = Mock()
        langfuse_provider.list_traces.return_value = {"provider": "langfuse"}
        registry.register(
            "langfuse",
            langfuse_provider,
            source_systems=("langfuse",),
        )
        service = TraceQueryService(registry)
        mock_resolve_source_system.return_value = None

        result = service.list_traces(
            space=SimpleNamespace(type=2),
            user=SimpleNamespace(observation_subject_id="w123"),
            query_params={},
        )

        self.assertEqual(result, {"provider": "langfuse"})
        langfuse_provider.list_traces.assert_called_once()

    @patch("apps.core.trace_query.service.resolve_trace_source_system")
    def test_trace_query_service_raises_for_unregistered_source_system(
        self,
        mock_resolve_source_system,
    ):
        registry = TraceProviderRegistry()
        registry.register("langfuse", Mock(), source_systems=("langfuse",))
        service = TraceQueryService(registry)
        mock_resolve_source_system.return_value = "signoz"

        with self.assertRaises(TraceProviderNotConfiguredError):
            service.list_traces(
                space=SimpleNamespace(type=2),
                user=SimpleNamespace(observation_subject_id="w123"),
                query_params={},
            )

    def test_trace_query_service_normalizes_trace_detail_tree_links(
        self,
    ):
        registry = TraceProviderRegistry()
        provider = Mock()
        provider.get_trace_detail.return_value = {
            "id": "trace-1",
            "observations": [
                {
                    "id": "root",
                    "parentObservationId": None,
                    "startTime": "2026-04-29T01:00:00Z",
                    "metadata": {"attributes": {"__meta__.spanId": "0"}},
                },
                {
                    "id": "child",
                    "parentObservationId": "missing-parent",
                    "startTime": "2026-04-29T01:00:01Z",
                    "metadata": {"attributes": {"__meta__.spanId": "0.1"}},
                },
            ],
        }
        registry.register("langfuse", provider, source_systems=("langfuse",))
        service = TraceQueryService(registry)

        with patch(
            "apps.core.trace_query.service.resolve_trace_source_system",
            return_value="langfuse",
        ):
            result = service.get_trace_detail(
                space=SimpleNamespace(type=2),
                user=SimpleNamespace(observation_subject_id="w123"),
                trace_id="trace-1",
                query_params={},
            )

        self.assertEqual(
            result["observations"][1]["parentObservationId"],
            "root",
        )
        provider.get_trace_detail.assert_called_once()
        provider.get_observation_detail.assert_not_called()

    def test_trace_query_service_normalizes_public_trace_detail_tree_links_for_langgraph(
        self,
    ):
        registry = TraceProviderRegistry()
        provider = Mock()
        provider.get_public_trace_detail.return_value = {
            "id": "trace-1",
            "observations": [
                {
                    "id": "entrypoint",
                    "parentObservationId": "filtered-business-parent",
                    "startTime": "2026-04-29T01:00:00Z",
                    "metaSpanId": "0.1",
                },
                {
                    "id": "chat-node",
                    "parentObservationId": "entrypoint",
                    "startTime": "2026-04-29T01:00:01Z",
                    "metaSpanId": "0.1.1",
                },
            ],
        }
        registry.register(
            "signoz",
            provider,
            source_systems=("signoz",),
        )
        service = TraceQueryService(registry)

        result = service.get_public_trace_detail(
            space=SimpleNamespace(type=2),
            user=None,
            trace_id="trace-1",
            query_params={"langgraph": "true"},
            source_system_override="signoz",
        )

        self.assertIsNone(result["observations"][0]["parentObservationId"])
        self.assertEqual(
            result["observations"][1]["parentObservationId"],
            "entrypoint",
        )
        provider.get_public_trace_detail.assert_called_once()

    def test_trace_query_service_keeps_public_trace_detail_tree_links_without_langgraph(
        self,
    ):
        registry = TraceProviderRegistry()
        provider = Mock()
        provider.get_public_trace_detail.return_value = {
            "id": "trace-1",
            "observations": [
                {
                    "id": "entrypoint",
                    "parentObservationId": "filtered-business-parent",
                    "startTime": "2026-04-29T01:00:00Z",
                    "metaSpanId": "0.1",
                },
                {
                    "id": "chat-node",
                    "parentObservationId": "entrypoint",
                    "startTime": "2026-04-29T01:00:01Z",
                    "metaSpanId": "0.1.1",
                },
            ],
        }
        registry.register(
            "signoz",
            provider,
            source_systems=("signoz",),
        )
        service = TraceQueryService(registry)

        result = service.get_public_trace_detail(
            space=SimpleNamespace(type=2),
            user=None,
            trace_id="trace-1",
            query_params={},
            source_system_override="signoz",
        )

        self.assertEqual(
            result["observations"][0]["parentObservationId"],
            "filtered-business-parent",
        )
        self.assertEqual(
            result["observations"][1]["parentObservationId"],
            "entrypoint",
        )
        provider.get_public_trace_detail.assert_called_once()

    @patch(
        "apps.core.trace_query.service.resolve_trace_source_system",
        return_value="langfuse",
    )
    @patch("apps.core.trace_query.langfuse.provider.resolve_langfuse_runtime_config")
    @patch("apps.core.trace_query.langfuse.provider.http_requests.get")
    def test_langfuse_trace_detail_normalizes_without_observation_detail_http_calls(
        self,
        mock_http_get,
        mock_runtime_config,
        mock_resolve_source_system,
    ):
        mock_runtime_config.return_value = {
            "host": "https://langfuse.example.com",
            "public_key": "pk",
            "secret_key": "sk",
        }
        mock_response = Mock()
        mock_response.json.return_value = {
            "id": "trace-1",
            "observations": [
                {
                    "id": "root",
                    "parentObservationId": None,
                    "startTime": "2026-04-29T01:00:00Z",
                    "metadata": {"attributes": {"__meta__.spanId": "0"}},
                },
                {
                    "id": "child",
                    "parentObservationId": "missing-parent",
                    "startTime": "2026-04-29T01:00:01Z",
                    "metadata": {"attributes": {"__meta__.spanId": "0.1"}},
                },
            ],
        }
        mock_response.raise_for_status.return_value = None
        mock_http_get.return_value = mock_response
        registry = TraceProviderRegistry()
        registry.register(
            "langfuse",
            LangfuseTraceProvider(),
            source_systems=("langfuse",),
        )
        service = TraceQueryService(registry)

        result = service.get_trace_detail(
            space=SimpleNamespace(type=2, uuid="space-test"),
            user=SimpleNamespace(observation_subject_id="w123"),
            trace_id="trace-1",
            query_params={},
        )

        self.assertEqual(
            result["observations"][1]["parentObservationId"],
            "root",
        )
        mock_http_get.assert_called_once()
        self.assertIn(
            "/api/public/traces/trace-1",
            mock_http_get.call_args.args[0],
        )
        self.assertNotIn(
            "/api/public/observations/",
            mock_http_get.call_args.args[0],
        )
        mock_resolve_source_system.assert_called_once()

    def test_normalize_observation_tree_links_uses_meta_span_id_ancestry(self):
        observations = [
            {
                "id": "root",
                "parentObservationId": None,
                "startTime": "2026-04-29T01:00:00Z",
                "metadata": {"attributes": {"__meta__.spanId": "0"}},
            },
            {
                "id": "middle",
                "parentObservationId": "missing-parent",
                "startTime": "2026-04-29T01:00:01Z",
                "metadata": {"attributes": {"__meta__.spanId": "0.2"}},
            },
            {
                "id": "leaf",
                "parentObservationId": "also-missing",
                "startTime": "2026-04-29T01:00:02Z",
                "metadata": {"attributes": {"__meta__.spanId": "0.2.24.207"}},
            },
        ]

        normalized = normalize_observation_tree_links(observations)

        self.assertIsNone(normalized[0]["parentObservationId"])
        self.assertEqual(normalized[1]["parentObservationId"], "root")
        self.assertEqual(normalized[2]["parentObservationId"], "middle")

    def test_normalize_observation_tree_links_keeps_valid_parent_observation_id(self):
        observations = [
            {
                "id": "explicit-parent",
                "parentObservationId": None,
                "startTime": "2026-04-29T01:00:00Z",
                "metaSpanId": "1",
            },
            {
                "id": "fallback-parent",
                "parentObservationId": None,
                "startTime": "2026-04-29T01:00:01Z",
                "metaSpanId": "0",
            },
            {
                "id": "child",
                "parentObservationId": "explicit-parent",
                "startTime": "2026-04-29T01:00:02Z",
                "metaSpanId": "0.1",
            },
        ]

        normalized = normalize_observation_tree_links(observations)

        self.assertEqual(
            normalized[2]["parentObservationId"],
            "explicit-parent",
        )

    def test_normalize_observation_tree_links_uses_span_link_parent_fallback(self):
        observations = [
            {
                "id": "root",
                "parentObservationId": None,
                "startTime": "2026-04-29T01:00:00Z",
                "metadata": {"attributes": {"__meta__.spanId": "0"}},
            },
            {
                "id": "parent",
                "parentObservationId": "missing",
                "startTime": "2026-04-29T01:00:01Z",
                "metadata": {
                    "attributes": {
                        "_internal.span_link.role": "parent",
                        "_internal.sofa.span_id": "0.2",
                    }
                },
            },
            {
                "id": "child",
                "parentObservationId": "missing",
                "startTime": "2026-04-29T01:00:02Z",
                "metadata": {
                    "attributes": {
                        "_internal.span_link.role": "child",
                        "_internal.sofa.span_id": "0.2.24.207",
                    }
                },
            },
        ]

        normalized = normalize_observation_tree_links(observations)

        self.assertEqual(normalized[1]["parentObservationId"], "root")
        self.assertEqual(normalized[2]["parentObservationId"], "parent")

    def test_normalize_trace_detail_tree_links_ignores_missing_observation_list(self):
        trace_detail = {"id": "trace-1", "observations": "not-a-list"}

        normalized = normalize_trace_detail_tree_links(trace_detail)

        self.assertEqual(normalized, trace_detail)

    @patch("apps.core.views.http_requests.get")
    @patch("apps.core.views.get_space_observation_runtime_config")
    @patch("apps.core.views.trace.check_space_access_or_error")
    @patch("apps.core.views.trace.Space.objects.get")
    @patch("apps.core.views.trace.get_default_trace_query_service", create=True)
    def test_trace_list_view_delegates_to_trace_query_service(
        self,
        mock_get_service,
        mock_space_get,
        mock_check_access,
        mock_runtime_config,
        mock_http_get,
    ):
        mock_space_get.return_value = SimpleNamespace(uuid="space-1", type=2, status=1)
        mock_check_access.return_value = None
        mock_runtime_config.return_value = {
            "host": "https://langfuse.example.com",
            "public_key": "pk",
            "secret_key": "sk",
            "show_langfuse_link": True,
            "project_url": "https://langfuse.example.com/project/proj_xxx",
        }
        mock_service = Mock()
        mock_service.list_traces.return_value = {
            "data": [],
            "meta": {"page": 1, "limit": 20, "totalItems": 0, "totalPages": 1},
        }
        mock_get_service.return_value = mock_service
        mock_http_response = Mock()
        mock_http_response.raise_for_status.return_value = None
        mock_http_response.json.return_value = {
            "data": [],
            "meta": {"page": 1, "limit": 20, "totalItems": 0, "totalPages": 1},
        }
        mock_http_get.return_value = mock_http_response

        request = APIRequestFactory().get("/api/v1/spaces/space-1/traces", {"page": 1})
        force_authenticate(
            request,
            user=SimpleNamespace(is_authenticated=True, uuid="w123"),
        )

        response = TraceListView.as_view()(request, space_id="space-1")

        self.assertEqual(response.status_code, 200)
        mock_service.list_traces.assert_called_once()
        mock_http_get.assert_not_called()

    @patch("apps.core.views.http_requests.get")
    @patch("apps.core.views.get_space_observation_runtime_config")
    @patch("apps.core.views.trace.check_space_access_or_error")
    @patch("apps.core.views.trace.Space.objects.get")
    @patch("apps.core.views.trace.get_default_trace_query_service", create=True)
    def test_trace_io_batch_view_delegates_to_trace_query_service(
        self,
        mock_get_service,
        mock_space_get,
        mock_check_access,
        mock_runtime_config,
        mock_http_get,
    ):
        mock_space_get.return_value = SimpleNamespace(uuid="space-1", type=2, status=1)
        mock_check_access.return_value = None
        mock_runtime_config.return_value = {
            "host": "https://langfuse.example.com",
            "public_key": "pk",
            "secret_key": "sk",
        }
        mock_service = Mock()
        mock_service.get_trace_io_batch.return_value = {
            "data": [{"id": "trace-1", "input": "hi", "output": "hello"}],
        }
        mock_get_service.return_value = mock_service

        request = APIRequestFactory().post(
            "/api/v1/spaces/space-1/traces/io",
            {"traceIds": ["trace-1"]},
            format="json",
        )
        force_authenticate(
            request,
            user=SimpleNamespace(is_authenticated=True, uuid="w123"),
        )

        response = TraceIOBatchView.as_view()(request, space_id="space-1")

        self.assertEqual(response.status_code, 200)
        mock_service.get_trace_io_batch.assert_called_once()
        call_kwargs = mock_service.get_trace_io_batch.call_args.kwargs
        self.assertEqual(call_kwargs["trace_ids"], ["trace-1"])
        mock_http_get.assert_not_called()

    @patch("apps.core.views.http_requests.get")
    @patch("apps.core.views.get_space_observation_runtime_config")
    @patch("apps.core.views.trace.check_space_access_or_error")
    @patch("apps.core.views.trace.Space.objects.get")
    @patch("apps.core.views.trace.get_default_trace_query_service", create=True)
    def test_trace_usage_batch_view_delegates_to_trace_query_service(
        self,
        mock_get_service,
        mock_space_get,
        mock_check_access,
        mock_runtime_config,
        mock_http_get,
    ):
        mock_space_get.return_value = SimpleNamespace(uuid="space-1", type=2, status=1)
        mock_check_access.return_value = None
        mock_runtime_config.return_value = {
            "host": "https://langfuse.example.com",
            "public_key": "pk",
            "secret_key": "sk",
        }
        mock_service = Mock()
        mock_service.get_trace_usage_batch.return_value = {
            "data": [
                {
                    "id": "trace-1",
                    "inputTokens": 3,
                    "outputTokens": 2,
                    "totalTokens": 5,
                    "usageDetails": {"input": 3, "output": 2, "total": 5},
                }
            ],
        }
        mock_get_service.return_value = mock_service

        request = APIRequestFactory().post(
            "/api/v1/spaces/space-1/traces/usage",
            {"traceIds": ["trace-1", "trace-1"], "agent_product": "custom-agent"},
            format="json",
        )
        force_authenticate(
            request,
            user=SimpleNamespace(is_authenticated=True, uuid="w123"),
        )

        response = TraceUsageBatchView.as_view()(request, space_id="space-1")

        self.assertEqual(response.status_code, 200)
        mock_service.get_trace_usage_batch.assert_called_once()
        call_kwargs = mock_service.get_trace_usage_batch.call_args.kwargs
        self.assertEqual(call_kwargs["trace_ids"], ["trace-1"])
        self.assertEqual(call_kwargs["query_params"]["agent_product"], "custom-agent")
        mock_http_get.assert_not_called()

    @patch("apps.core.views.trace_unified.check_space_access_or_error")
    @patch("apps.core.views.trace_unified.Space.objects.get")
    @patch("apps.core.views.trace_unified.get_default_trace_query_service")
    def test_unified_session_usage_batch_view_forwards_payload_time_params(
        self,
        mock_get_service,
        mock_space_get,
        mock_check_access,
    ):
        mock_space_get.return_value = SimpleNamespace(uuid="space-1", type=2, status=1)
        mock_check_access.return_value = None
        mock_service = Mock()
        mock_service.get_session_usage_batch.return_value = {"data": []}
        mock_get_service.return_value = mock_service

        request = APIRequestFactory().post(
            "/api/v1/spaces/space-1/custom-agent/sessions/usage",
            {
                "sessionIds": ["session-1", "session-1"],
                "fromTimestamp": "2026-05-29T10:00:00+08:00",
                "toTimestamp": "2026-05-29T11:00:00+08:00",
                "agent_product": "custom-agent",
            },
            format="json",
        )
        force_authenticate(
            request,
            user=SimpleNamespace(is_authenticated=True, uuid="w123"),
        )

        response = UnifiedSessionUsageBatchView.as_view(source_system="signoz")(
            request,
            space_id="space-1",
        )

        self.assertEqual(response.status_code, 200)
        mock_service.get_session_usage_batch.assert_called_once()
        call_kwargs = mock_service.get_session_usage_batch.call_args.kwargs
        self.assertEqual(call_kwargs["session_ids"], ["session-1"])
        self.assertEqual(call_kwargs["source_system_override"], "signoz")
        self.assertEqual(
            call_kwargs["query_params"]["fromTimestamp"],
            "2026-05-29T10:00:00+08:00",
        )
        self.assertEqual(
            call_kwargs["query_params"]["toTimestamp"],
            "2026-05-29T11:00:00+08:00",
        )
        self.assertEqual(call_kwargs["query_params"]["agent_product"], "custom-agent")

    @patch("apps.core.views.http_requests.get")
    @patch("apps.core.views.get_space_observation_runtime_config")
    @patch("apps.core.views.trace.check_space_access_or_error")
    @patch("apps.core.views.trace.Space.objects.get")
    @patch("apps.core.views.trace.get_default_trace_query_service", create=True)
    def test_trace_detail_view_delegates_to_trace_query_service(
        self,
        mock_get_service,
        mock_space_get,
        mock_check_access,
        mock_runtime_config,
        mock_http_get,
    ):
        mock_space_get.return_value = SimpleNamespace(uuid="space-1", type=2, status=1)
        mock_check_access.return_value = None
        mock_runtime_config.return_value = {
            "host": "https://langfuse.example.com",
            "public_key": "pk",
            "secret_key": "sk",
        }
        mock_service = Mock()
        mock_service.get_trace_detail.return_value = {
            "id": "trace-123",
            "observations": [],
        }
        mock_get_service.return_value = mock_service
        mock_http_response = Mock()
        mock_http_response.raise_for_status.return_value = None
        mock_http_response.json.return_value = {"id": "trace-123", "observations": []}
        mock_http_get.return_value = mock_http_response

        request = APIRequestFactory().get("/api/v1/spaces/space-1/traces/trace-123")
        force_authenticate(
            request,
            user=SimpleNamespace(is_authenticated=True, uuid="w123"),
        )

        response = TraceDetailView.as_view()(
            request,
            space_id="space-1",
            trace_id="trace-123",
        )

        self.assertEqual(response.status_code, 200)
        mock_service.get_trace_detail.assert_called_once()
        mock_http_get.assert_not_called()

    @patch("apps.core.views.get_user_session_summaries")
    @patch("apps.core.views.get_space_session_summaries")
    @patch("apps.core.views.trace.check_space_access_or_error")
    @patch("apps.core.views.trace.Space.objects.get")
    @patch("apps.core.views.trace.get_default_trace_query_service", create=True)
    def test_session_list_view_delegates_to_trace_query_service(
        self,
        mock_get_service,
        mock_space_get,
        mock_check_access,
        mock_space_session_summaries,
        mock_user_session_summaries,
    ):
        mock_space_get.return_value = SimpleNamespace(uuid="space-1", type=2, status=1)
        mock_check_access.return_value = None
        mock_service = Mock()
        mock_service.list_sessions.return_value = {
            "data": [],
            "meta": {"page": 1, "limit": 20, "totalItems": 0, "totalPages": 1},
        }
        mock_get_service.return_value = mock_service

        request = APIRequestFactory().get(
            "/api/v1/spaces/space-1/sessions", {"page": 1}
        )
        force_authenticate(
            request,
            user=SimpleNamespace(is_authenticated=True, uuid="w123"),
        )

        response = SessionListView.as_view()(request, space_id="space-1")

        self.assertEqual(response.status_code, 200)
        mock_service.list_sessions.assert_called_once()
        mock_space_session_summaries.assert_not_called()
        mock_user_session_summaries.assert_not_called()

    @patch("apps.core.views.get_user_session_traces")
    @patch("apps.core.views.get_space_session_traces")
    @patch("apps.core.views.trace.check_space_access_or_error")
    @patch("apps.core.views.trace.Space.objects.get")
    @patch("apps.core.views.trace.get_default_trace_query_service", create=True)
    def test_session_detail_view_delegates_to_trace_query_service(
        self,
        mock_get_service,
        mock_space_get,
        mock_check_access,
        mock_space_session_traces,
        mock_user_session_traces,
    ):
        mock_space_get.return_value = SimpleNamespace(uuid="space-1", type=2, status=1)
        mock_check_access.return_value = None
        mock_service = Mock()
        mock_service.get_session_detail.return_value = {
            "id": "session-123",
            "traces": [],
        }
        mock_get_service.return_value = mock_service

        request = APIRequestFactory().get("/api/v1/spaces/space-1/sessions/session-123")
        force_authenticate(
            request,
            user=SimpleNamespace(is_authenticated=True, uuid="w123"),
        )

        response = SessionDetailView.as_view()(
            request,
            space_id="space-1",
            session_id="session-123",
        )

        self.assertEqual(response.status_code, 200)
        mock_service.get_session_detail.assert_called_once()
        mock_space_session_traces.assert_not_called()
        mock_user_session_traces.assert_not_called()

    @patch("apps.core.views.http_requests.get")
    @patch("apps.core.views.trace.check_space_access_or_error")
    @patch("apps.core.views.trace.Space.objects.get")
    @patch("apps.core.views.trace.get_default_trace_query_service", create=True)
    def test_observation_detail_view_delegates_to_trace_query_service(
        self,
        mock_get_service,
        mock_space_get,
        mock_check_access,
        mock_http_get,
    ):
        mock_space_get.return_value = SimpleNamespace(uuid="space-1", type=2, status=1)
        mock_check_access.return_value = None
        mock_service = Mock()
        mock_service.get_observation_detail.return_value = {"id": "obs-123"}
        mock_get_service.return_value = mock_service

        request = APIRequestFactory().get("/api/v1/spaces/space-1/observations/obs-123")
        force_authenticate(
            request,
            user=SimpleNamespace(is_authenticated=True, uuid="w123"),
        )

        response = ObservationDetailView.as_view()(
            request,
            space_id="space-1",
            observation_id="obs-123",
        )

        self.assertEqual(response.status_code, 200)
        mock_service.get_observation_detail.assert_called_once()
        mock_http_get.assert_not_called()
