import base64
import json
from types import SimpleNamespace
from unittest.mock import ANY, Mock, patch

from django.test import SimpleTestCase
from requests import ConnectionError
from rest_framework.test import APIClient, APIRequestFactory

from apps.core.models import (
    Agent,
    AgentTypeConfig,
    DataSource,
    ObservationEndpointConfig,
    Space,
    SpaceKey,
    SpaceObservationCredential,
    SpaceObservationProject,
    User,
)
from apps.core.services.credential_service import hash_secret
from apps.core.tests.helpers import (
    create_test_agent,
    create_local_user,
    SpaceKeySimpleTestCase,
    SpaceKeyTestCase,
)
from apps.core.throttling import UnifiedExternalRateThrottle
from apps.core.trace_query.types import TraceQueryServiceError
from apps.core.views.common import check_space_key_access
from apps.core.views.openapi_trace import (
    OpenAPISessionDetailView,
    OpenAPISessionIOBatchView,
    OpenAPISessionIOView,
    OpenAPISessionListView,
    OpenAPISessionUsageBatchView,
    OpenAPISpanDetailView,
    OpenAPISpanListView,
    OpenAPITraceDetailView,
    OpenAPITraceListView,
    _log_access,
)
from apps.core.views.trace_handlers import (
    merge_session_list_params,
    merge_trace_list_params,
)
from apps.core.views.trace_unified import UnifiedSessionListView


class TraceParamMergeTests(SimpleTestCase):
    def test_trace_list_merge_keeps_webapi_filters_and_multidim_params(self):
        filters = [{"field": "trace_id", "op": "=", "value": "trace-1"}]

        params = merge_trace_list_params(
            {
                "filters": filters,
                "env": "prod",
                "scene": "chat",
                "username": "100001",
                "tags": ["mcp:foo"],
                "searchType": "output",
                "keyword": "completed",
                "input_keyword": "legacy fuzzy",
                "fromTimestamp": "2026-06-30T01:00:00+08:00",
                "toTimestamp": "2026-06-30T02:00:00+08:00",
            },
            {"page": "1"},
        )

        self.assertEqual(params["filters"][0], filters[0])
        self.assertNotIn(
            "resource.attributes.ant.agent.env",
            [item["field"] for item in params["filters"]],
        )
        self.assertIn(
            {
                "field": "resource.attributes.ant.agent.scene",
                "op": "=",
                "value": "chat",
            },
            params["filters"],
        )
        self.assertNotIn("attributes.ant.username", [item["field"] for item in params["filters"]])
        self.assertEqual(params["env"], "prod")
        self.assertEqual(params["scene"], "chat")
        self.assertEqual(params["username"], "100001")
        self.assertEqual(params["tags"], ["mcp:foo"])
        self.assertEqual(params["searchType"], "output")
        self.assertEqual(params["keyword"], "completed")
        self.assertEqual(params["input_keyword"], "legacy fuzzy")
        self.assertEqual(params["fromTimestamp"], "2026-06-30T01:00:00+08:00")
        self.assertEqual(params["toTimestamp"], "2026-06-30T02:00:00+08:00")

    def test_trace_list_merge_accepts_openapi_filter_map(self):
        params = merge_trace_list_params(
            {"filters": {"trace_id": "trace-1", "attributes.ant.username": "100001"}},
            {},
        )

        self.assertEqual(
            params["filters"],
            [
                {"field": "trace_id", "op": "=", "value": "trace-1"},
                {"field": "attributes.ant.username", "op": "=", "value": "100001"},
            ],
        )

    def test_session_list_merge_keeps_webapi_filters_and_multidim_params(self):
        params = merge_session_list_params(
            {
                "filters": [{"field": "attributes.session.id", "value": "s-1"}],
                "env": "prod",
                "scene": "chat",
                "username": "100001",
                "fromTimestamp": "2026-06-30T01:00:00+08:00",
                "toTimestamp": "2026-06-30T02:00:00+08:00",
                "sessionId": "s-1",
            },
            {"page": "1"},
        )

        self.assertEqual(params["sessionId"], "s-1")
        self.assertEqual(params["fromTimestamp"], "2026-06-30T01:00:00+08:00")
        self.assertEqual(params["toTimestamp"], "2026-06-30T02:00:00+08:00")
        self.assertIn(
            {
                "field": "resource.attributes.ant.agent.env",
                "op": "=",
                "value": "prod",
            },
            params["filters"],
        )
        self.assertIn(
            {"field": "attributes.ant.username", "op": "=", "value": "100001"},
            params["filters"],
        )


class OpenAPIAccessFileLogTests(SimpleTestCase):
    @patch("apps.core.views.openapi_trace.external_api_calls.add")
    @patch("apps.core.views.openapi_trace._openapi_access_logger.info")
    def test_log_access_writes_metric_and_dedicated_file_logger(
        self, mock_file_info, mock_metric_add
    ):
        request = SimpleNamespace(
            auth_type="space_key",
            auth=SimpleNamespace(key_prefix="sk-aiv-test-prefix"),
            META={
                "HTTP_X_FORWARDED_FOR": "1.1.1.1, 2.2.2.2",
                "REMOTE_ADDR": "3.3.3.3",
            },
            method="GET",
            path="/openapi/v1/spaces/space-1/agents/ClaudeCode/trace/t-1/detail",
        )
        space = SimpleNamespace(uuid="space-1")

        _log_access(
            request,
            space,
            "ClaudeCode",
            "trace/t-1/detail",
            "trace/detail",
        )

        mock_metric_add.assert_called_once()
        payload = json.loads(mock_file_info.call_args.args[0])
        self.assertEqual(payload["event"], "openapi_access")
        self.assertEqual(payload["endpoint"], "trace/t-1/detail")
        self.assertEqual(payload["endpoint_group"], "trace/detail")
        self.assertEqual(payload["path"], request.path)
        self.assertEqual(payload["ip_address"], "1.1.1.1")
        self.assertEqual(payload["auth_prefix"], "sk-aiv-test-prefix")


class UnifiedExternalRateThrottleTests(SimpleTestCase):
    def test_credential_auth_uses_public_key_cache_identifier(self):
        throttle = UnifiedExternalRateThrottle()
        throttle.scope = "external_api"
        request = SimpleNamespace(
            auth_type="credential",
            auth=SimpleNamespace(public_key="pk-read-test"),
        )

        cache_key = throttle.get_cache_key(request, SimpleNamespace())

        self.assertIsNotNone(cache_key)
        self.assertIn("credential:pk-read-test", cache_key)


class OpenAPITraceViewTests(SpaceKeySimpleTestCase):
    def setUp(self):
        super().setUp()
        self.factory = APIRequestFactory()
        self.user = SimpleNamespace(observation_subject_id="100001")
        self.space = SimpleNamespace(uuid="space-1", type=2, creator=self.user)

    @patch("apps.core.views.openapi_trace._log_access")
    @patch("apps.core.views.trace_handlers.query_trace_io_batch")
    @patch("apps.core.views.openapi_trace.query_trace_list")
    @patch("apps.core.views.openapi_trace.check_space_key_access")
    def test_trace_list_uses_shared_query_and_view_level_io_enrichment(
        self,
        mock_check_access,
        mock_query_trace_list,
        mock_query_trace_io_batch,
        _mock_log_access,
    ):
        mock_check_access.return_value = (self.space, None)
        mock_query_trace_list.return_value = {"data": [{"trace_id": "trace-1"}]}
        mock_query_trace_io_batch.return_value = {
            "data": [{"id": "trace-1", "input": "hello", "output": "world"}]
        }

        request = SimpleNamespace(
            data={
                "page": 2,
                "limit": 10,
                "env": "prod",
                "scene": "chat",
                "username": "100001",
                "filters": [{"field": "trace_id", "op": "=", "value": "trace-1"}],
            },
            query_params={"io": "true"},
            auth_type="space_key",
            auth=SimpleNamespace(creator=self.user),
        )
        response = OpenAPITraceListView().post(
            request, space_id="space-1", agent_product="ClaudeCode"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["data"][0]["input"], "hello")
        _, query_user, query_params, source_system = (
            mock_query_trace_list.call_args.args
        )
        self.assertIs(query_user, self.user)
        self.assertIsNone(source_system)
        self.assertEqual(query_params["agent_product"], "ClaudeCode")
        self.assertEqual(query_params["env"], "prod")
        self.assertEqual(query_params["scene"], "chat")
        self.assertEqual(query_params["username"], "100001")
        self.assertNotIn("agent_product", request.data)
        self.assertEqual(query_params["filters"][0]["field"], "trace_id")
        self.assertNotIn(
            "resource.attributes.ant.agent.env",
            [item["field"] for item in query_params["filters"]],
        )
        mock_query_trace_io_batch.assert_called_once_with(
            self.space,
            self.user,
            ["trace-1"],
            query_params,
            None,
        )

    @patch("apps.core.views.openapi_trace.query_trace_list")
    @patch("apps.core.views.openapi_trace.check_space_key_access")
    def test_trace_list_filter_validation_error_returns_json_400(
        self,
        mock_check_access,
        mock_query_trace_list,
    ):
        mock_check_access.return_value = (self.space, None)
        mock_query_trace_list.side_effect = TraceQueryServiceError(
            "filters[0].value must be an array for IN",
            3027,
        )
        request = SimpleNamespace(
            data={
                "filters": [
                    {
                        "field": "resource.attributes.ant.agent.env",
                        "op": "IN",
                        "value": "Default",
                    }
                ],
            },
            query_params={},
            auth_type="space_key",
            auth=SimpleNamespace(creator=self.user),
        )

        response = OpenAPITraceListView().post(
            request, space_id="space-1", agent_product="ClaudeCode"
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], 3027)
        self.assertEqual(
            response.data["message"],
            "filters[0].value must be an array for IN",
        )
        self.assertIsNone(response.data["data"])

    @patch("apps.core.views.openapi_trace.query_trace_list")
    @patch("apps.core.views.openapi_trace.check_space_key_access")
    def test_trace_list_hides_unexpected_provider_details(
        self, mock_check_access, mock_query_trace_list
    ):
        detail = "fixture-only provider diagnostic"
        mock_check_access.return_value = (self.space, None)
        mock_query_trace_list.side_effect = ConnectionError(detail)
        request = SimpleNamespace(
            data={},
            query_params={},
            auth_type="space_key",
            auth=SimpleNamespace(creator=self.user),
        )

        with self.assertLogs("apps.core.views.trace_handlers", level="ERROR") as logs:
            response = OpenAPITraceListView().post(
                request, space_id="space-1", agent_product="ClaudeCode"
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data,
            {"code": 3012, "message": "Failed to query traces", "data": None},
        )
        self.assertIn("ConnectionError", "\n".join(logs.output))
        self.assertNotIn(detail, "\n".join(logs.output))

    @patch("apps.core.views.openapi_trace._log_access")
    @patch("apps.core.views.openapi_trace.query_trace_list")
    @patch("apps.core.views.openapi_trace.check_space_key_access")
    def test_trace_list_accepts_explicit_signoz_source(
        self,
        mock_check_access,
        mock_query_trace_list,
        _mock_log_access,
    ):
        mock_check_access.return_value = (self.space, None)
        mock_query_trace_list.return_value = {"data": []}
        request = SimpleNamespace(
            data={"page": 1, "sourceSystem": "signoz"},
            query_params={},
            auth_type="space_key",
            auth=SimpleNamespace(creator=self.user),
        )

        response = OpenAPITraceListView().post(
            request,
            space_id="space-1",
            agent_product="ClaudeCode",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_query_trace_list.call_args.args[3], "signoz")

    @patch("apps.core.views.openapi_trace.query_trace_list")
    @patch("apps.core.views.openapi_trace.check_space_key_access")
    def test_trace_list_rejects_unknown_explicit_source(
        self,
        mock_check_access,
        mock_query_trace_list,
    ):
        mock_check_access.return_value = (self.space, None)
        request = SimpleNamespace(
            data={"sourceSystem": "unknown-backend"},
            query_params={},
            auth_type="space_key",
            auth=SimpleNamespace(creator=self.user),
        )

        response = OpenAPITraceListView().post(
            request,
            space_id="space-1",
            agent_product="ClaudeCode",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], 3027)
        mock_query_trace_list.assert_not_called()

    @patch("apps.core.views.openapi_trace._log_access")
    @patch("apps.core.views.trace_handlers.query_trace_io_batch")
    @patch("apps.core.views.openapi_trace.query_trace_detail")
    @patch("apps.core.views.openapi_trace.check_space_key_access")
    def test_trace_detail_io_enrichment_uses_resolved_trace_id(
        self,
        mock_check_access,
        mock_query_trace_detail,
        mock_query_trace_io_batch,
        _mock_log_access,
    ):
        mock_check_access.return_value = (self.space, None)
        mock_query_trace_detail.return_value = {
            "traceId": "otel-trace-1",
            "sofaTraceId": "sofa-trace-1",
        }
        mock_query_trace_io_batch.return_value = {
            "data": [{"id": "otel-trace-1", "input": "in", "output": "out"}]
        }

        request = self.factory.get(
            "/openapi/v1/spaces/space-1/agents/ClaudeCode/trace/sofa-trace-1/detail?io=true"
        )
        request.auth_type = "space_key"
        request.auth = SimpleNamespace(creator=self.user)
        response = OpenAPITraceDetailView.as_view(authentication_classes=[])(
            request,
            space_id="space-1",
            agent_product="ClaudeCode",
            trace_id="sofa-trace-1",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["input"], "in")
        self.assertIs(mock_query_trace_detail.call_args.args[1], self.user)
        query_params = mock_query_trace_detail.call_args.args[3]
        mock_query_trace_io_batch.assert_called_once_with(
            self.space,
            self.user,
            ["otel-trace-1"],
            query_params,
            None,
        )


class OpenAPISessionViewTests(SpaceKeySimpleTestCase):
    def setUp(self):
        super().setUp()
        self.factory = APIRequestFactory()
        self.user = SimpleNamespace(observation_subject_id="100001")
        self.space = SimpleNamespace(uuid="space-1", type=2, creator=self.user)

    @patch("apps.core.views.openapi_trace._log_access")
    @patch("apps.core.views.trace_handlers.query_session_usage_batch")
    @patch("apps.core.views.trace_handlers.query_session_io_batch")
    @patch("apps.core.views.openapi_trace.query_session_list")
    @patch("apps.core.views.openapi_trace.check_space_key_access")
    def test_session_list_uses_shared_query_and_view_level_enrichment(
        self,
        mock_check_access,
        mock_query_session_list,
        mock_query_session_io_batch,
        mock_query_session_usage_batch,
        mock_log_access,
    ):
        mock_check_access.return_value = (self.space, None)
        mock_query_session_list.return_value = {"data": [{"session_id": "s-1"}]}
        mock_query_session_io_batch.return_value = {
            "data": [{"id": "s-1", "input": "hello", "output": "world"}]
        }
        mock_query_session_usage_batch.return_value = {
            "data": [{"id": "s-1", "usage": {"total_tokens": 3}}]
        }

        request = SimpleNamespace(
            data={
                "page": 2,
                "limit": 10,
                "env": "prod",
                "sessionId": "s-1",
                "filters": [
                    {"field": "attributes.session.id", "op": "=", "value": "s-1"}
                ],
            },
            query_params={"io": "true", "usage": "true"},
            auth_type="space_key",
            auth=SimpleNamespace(creator=self.user),
        )
        response = OpenAPISessionListView().post(
            request, space_id="space-1", agent_product="ClaudeCode"
        )

        self.assertEqual(response.status_code, 200)
        item = response.data["data"]["data"][0]
        self.assertEqual(item["input"], "hello")
        self.assertEqual(item["output"], "world")
        self.assertEqual(item["usage"], {"total_tokens": 3})
        _, query_user, query_params, source_system = (
            mock_query_session_list.call_args.args
        )
        self.assertIs(query_user, self.user)
        self.assertIsNone(source_system)
        self.assertEqual(query_params["agent_product"], "ClaudeCode")
        self.assertEqual(query_params["env"], "prod")
        self.assertEqual(query_params["sessionId"], "s-1")
        self.assertNotIn("agent_product", request.data)
        mock_query_session_io_batch.assert_called_once_with(
            self.space,
            self.user,
            ["s-1"],
            query_params,
            None,
        )
        mock_query_session_usage_batch.assert_called_once_with(
            self.space,
            self.user,
            ["s-1"],
            query_params,
            None,
        )
        mock_log_access.assert_called_once_with(
            ANY, self.space, "ClaudeCode", "sessions/list"
        )

    @patch("apps.core.views.openapi_trace._log_access")
    @patch("apps.core.views.trace_handlers.query_session_io")
    @patch("apps.core.views.openapi_trace.query_session_detail")
    @patch("apps.core.views.openapi_trace.check_space_key_access")
    def test_session_detail_io_enrichment_uses_resolved_session_id(
        self,
        mock_check_access,
        mock_query_session_detail,
        mock_query_session_io,
        _mock_log_access,
    ):
        mock_check_access.return_value = (self.space, None)
        mock_query_session_detail.return_value = {"sessionId": "resolved-session"}
        mock_query_session_io.return_value = {
            "id": "resolved-session",
            "input": "in",
            "output": "out",
        }

        request = self.factory.get(
            "/openapi/v1/spaces/space-1/agents/ClaudeCode/session/s-1/detail" "?io=true"
        )
        request.auth_type = "space_key"
        request.auth = SimpleNamespace(creator=self.user)
        response = OpenAPISessionDetailView.as_view(authentication_classes=[])(
            request,
            space_id="space-1",
            agent_product="ClaudeCode",
            session_id="s-1",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["input"], "in")
        self.assertIs(mock_query_session_detail.call_args.args[1], self.user)
        query_params = mock_query_session_detail.call_args.args[3]
        mock_query_session_io.assert_called_once_with(
            self.space,
            self.user,
            "resolved-session",
            query_params,
            None,
        )

    @patch("apps.core.views.openapi_trace._log_access")
    @patch("apps.core.views.openapi_trace.query_session_io_batch")
    @patch("apps.core.views.openapi_trace.check_space_key_access")
    def test_session_io_batch_validates_and_calls_shared_query(
        self,
        mock_check_access,
        mock_query_session_io_batch,
        _mock_log_access,
    ):
        mock_check_access.return_value = (self.space, None)
        mock_query_session_io_batch.return_value = {"data": []}

        request = self.factory.post(
            "/openapi/v1/spaces/space-1/agents/ClaudeCode/sessions/io",
            {"sessionIds": ["s-1"], "fromTimestamp": "2026-06-30T01:00:00+08:00"},
            format="json",
        )
        response = OpenAPISessionIOBatchView.as_view(authentication_classes=[])(
            request, space_id="space-1", agent_product="ClaudeCode"
        )

        self.assertEqual(response.status_code, 200)
        params = mock_query_session_io_batch.call_args.args[3]
        self.assertEqual(params["agent_product"], "ClaudeCode")
        self.assertEqual(params["fromTimestamp"], "2026-06-30T01:00:00+08:00")

    @patch("apps.core.views.openapi_trace._log_access")
    @patch("apps.core.views.openapi_trace.query_session_list")
    @patch("apps.core.views.openapi_trace.check_space_key_access")
    def test_session_list_accepts_explicit_signoz_source(
        self,
        mock_check_access,
        mock_query_session_list,
        _mock_log_access,
    ):
        mock_check_access.return_value = (self.space, None)
        mock_query_session_list.return_value = {"data": []}
        request = SimpleNamespace(
            data={"source_system": "signoz"},
            query_params={},
            auth_type="space_key",
            auth=SimpleNamespace(creator=self.user),
        )

        response = OpenAPISessionListView().post(
            request,
            space_id="space-1",
            agent_product="ClaudeCode",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_query_session_list.call_args.args[3], "signoz")

    def test_session_views_keep_space_key_throttle(self):
        for view in (
            OpenAPISessionListView,
            OpenAPISessionIOBatchView,
            OpenAPISessionUsageBatchView,
            OpenAPISessionDetailView,
            OpenAPISessionIOView,
        ):
            self.assertIn(UnifiedExternalRateThrottle, view.throttle_classes)
            self.assertEqual(view.throttle_scope, "external_api")


class OpenAPISpanViewTests(SpaceKeySimpleTestCase):
    def setUp(self):
        super().setUp()
        self.user = SimpleNamespace(observation_subject_id="100001")
        self.space = SimpleNamespace(uuid="space-1", type=2, creator=self.user)

    @patch("apps.core.views.openapi_trace._log_access")
    @patch("apps.core.views.openapi_trace.query_span_batch")
    @patch("apps.core.views.openapi_trace.check_space_key_access")
    def test_span_list_accepts_explicit_signoz_source(
        self,
        mock_check_access,
        mock_query_span_batch,
        _mock_log_access,
    ):
        mock_check_access.return_value = (self.space, None)
        mock_query_span_batch.return_value = {"spans": []}
        request = SimpleNamespace(
            data={"spanIds": ["span-1"], "sourceSystem": "signoz"},
            query_params={},
            auth_type="space_key",
            auth=SimpleNamespace(creator=self.user),
        )

        response = OpenAPISpanListView().post(
            request,
            space_id="space-1",
            agent_product="ClaudeCode",
            trace_id="trace-1",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_query_span_batch.call_args.args[5], "signoz")

    @patch("apps.core.views.openapi_trace._log_access")
    @patch("apps.core.views.openapi_trace.query_span_detail")
    @patch("apps.core.views.openapi_trace.check_space_key_access")
    def test_span_detail_accepts_signoz_query_selector(
        self,
        mock_check_access,
        mock_query_span_detail,
        _mock_log_access,
    ):
        mock_check_access.return_value = (self.space, None)
        mock_query_span_detail.return_value = {"span_id": "span-1"}
        request = SimpleNamespace(
            query_params={"sourceSystem": "signoz"},
            auth_type="space_key",
            auth=SimpleNamespace(creator=self.user),
        )

        response = OpenAPISpanDetailView().get(
            request,
            space_id="space-1",
            agent_product="ClaudeCode",
            trace_id="trace-1",
            span_id="span-1",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_query_span_detail.call_args.args[5], "signoz")


class OpenAPISigNozDatasourceRoutingTests(SpaceKeyTestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.user = create_local_user("100001", name="SigNoz Owner")
        self.space = Space.objects.create(
            name="SigNoz OpenAPI Space",
            type=2,
            scope=1,
            creator=self.user,
            status=1,
        )
        raw_key, key_prefix, key_hash = SpaceKey.generate_key()
        SpaceKey.objects.create(
            space=self.space,
            creator=self.user,
            key_prefix=key_prefix,
            key_hash=key_hash,
            enabled=True,
            name="signoz-openapi",
        )
        self.raw_key = raw_key
        self.agent_type = AgentTypeConfig.objects.create(
            agent_name="signoz-openapi-agent",
            agent_product="Codex",
            status=1,
        )
        create_test_agent(
            space=self.space,
            agent_type=self.agent_type,
            agent_product="Codex",
            name="SigNoz OpenAPI Agent",
            status=1,
        )
        self.datasource = DataSource.objects.create(
            agent_type=self.agent_type,
            datasource_type="traces",
            source_system="signoz",
            enabled=1,
            is_default=1,
            config={
                "endpoint": "http://signoz-query:8080",
                "api_key": "signoz-key",
                "agent_product_filter": "CodexTelemetry",
                "response_format": "stargazer",
            },
        )

    @patch("apps.core.trace_query.signoz.snapshot_window.query_snapshot_window", return_value=None)
    @patch("apps.core.trace_query.signoz.provider.requests.get")
    def test_default_signoz_datasource_reaches_oceanbase_query_contract(self, mock_get, _snapshot):
        backend_response = Mock(status_code=200)
        backend_response.json.return_value = {
            "status": "success",
            "data": {
                "data": [{"traceId": "trace-1", "spanCount": 1, "spans": [{
                    "span_id": "root", "trace_id": "trace-1", "name": "request",
                    "start_time_unix_nano": "1782864000000000000",
                    "resource": {"aivision.space.id": str(self.space.uuid)},
                }]}],
                "meta": {
                    "contract": "native-trace-page-v1", "complete": True,
                    "page": 1, "limit": 20, "totalItems": 1, "totalPages": 1,
                    "from": 1782864000000, "to": 1782867600000,
                    "space_id": str(self.space.uuid), "agent_product": "CodexTelemetry",
                    "agent_uuid": "", "user": "",
                },
                "facets": {"env": [], "scene": [], "username": [], "tags": []},
            },
        }
        backend_response.headers = {}
        backend_response.iter_content.return_value = [json.dumps(backend_response.json.return_value).encode()]
        mock_get.return_value = backend_response

        response = self.client.post(
            f"/openapi/v1/spaces/{self.space.uuid}/agents/Codex/traces/list",
            {
                "from": "2026-07-01T00:00:00Z",
                "to": "2026-07-01T01:00:00Z",
                "page": 1,
                "limit": 20,
            },
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {self.raw_key}",
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["data"]["data"][0]["trace_id"], "trace-1")
        url = mock_get.call_args_list[0].args[0]
        params = mock_get.call_args_list[0].kwargs["params"]
        self.assertTrue(url.endswith("/api/v1/ai-vision/traces/page"))
        mock_get.assert_called_once()
        self.assertEqual(response.json()["data"]["meta"]["queryMode"], "storage")
        self.assertEqual(params["space_id"], str(self.space.uuid))
        self.assertEqual(params["agent_product"], "CodexTelemetry")
        self.assertNotIn("user", params)


class UnifiedInternalSessionListViewTests(SimpleTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.user = SimpleNamespace(observation_subject_id="100001")
        self.space = SimpleNamespace(uuid="space-1", type=2, creator=self.user)

    @patch("apps.core.views.trace_unified.get_default_trace_query_service")
    @patch("apps.core.views.trace_unified._check_access")
    @patch("apps.core.views.trace_unified._get_space_or_error")
    def test_session_list_uses_shared_merge_params(
        self,
        mock_get_space,
        mock_check_access,
        mock_service_factory,
    ):
        service = Mock()
        service.list_sessions.return_value = {"data": []}
        mock_service_factory.return_value = service
        mock_get_space.return_value = (self.space, None)
        mock_check_access.return_value = None

        request = SimpleNamespace(
            data={
                "env": "prod",
                "scene": "chat",
                "username": "100001",
                "tags": ["tag-a"],
                "fromTimestamp": "2026-07-01T00:00:00+08:00",
                "toTimestamp": "2026-07-01T01:00:00+08:00",
                "filters": [],
            },
            query_params={},
            user=self.user,
        )

        view = UnifiedSessionListView()
        view.source_system = "stargazer_native"
        response = view.post(
            request,
            space_id="space-1",
        )

        self.assertEqual(response.status_code, 200)
        query_params = service.list_sessions.call_args.kwargs["query_params"]
        self.assertEqual(query_params["env"], "prod")
        self.assertEqual(query_params["scene"], "chat")
        self.assertEqual(query_params["username"], "100001")
        self.assertEqual(query_params["tags"], ["tag-a"])
        self.assertEqual(query_params["fromTimestamp"], "2026-07-01T00:00:00+08:00")
        self.assertEqual(query_params["toTimestamp"], "2026-07-01T01:00:00+08:00")
        self.assertIn(
            {
                "field": "resource.attributes.ant.agent.env",
                "op": "=",
                "value": "prod",
            },
            query_params["filters"],
        )
        self.assertIn(
            {
                "field": "resource.attributes.ant.agent.scene",
                "op": "=",
                "value": "chat",
            },
            query_params["filters"],
        )


class OpenAPISpaceKeyRouteTests(SpaceKeyTestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.user = create_local_user("100001", name="User 1")
        self.other_user = create_local_user("100002", name="User 2")
        self.space = Space.objects.create(
            name="Space 1", type=1, scope=1, creator=self.user, status=1
        )
        self.other_space = Space.objects.create(
            name="Space 2", type=1, scope=1, creator=self.other_user, status=1
        )
        self.raw_key = self._create_space_key(self.space, self.user)
        self.other_raw_key = self._create_space_key(self.other_space, self.other_user)
        agent_type = AgentTypeConfig.objects.create(
            agent_name="OPENAPI_SPACE_KEY_CLAUDE",
            agent_product="ClaudeCode",
            status=1,
        )
        create_test_agent(
            space=self.space,
            agent_type=agent_type,
            agent_product="ClaudeCode",
            name="Claude Code",
            status=1,
        )
        self.url = (
            f"/openapi/v1/spaces/{self.space.uuid}/agents/ClaudeCode/sessions/list"
        )

    def _create_space_key(self, space, creator, enabled=True):
        raw_key, key_prefix, key_hash = SpaceKey.generate_key()
        SpaceKey.objects.create(
            space=space,
            creator=creator,
            key_prefix=key_prefix,
            key_hash=key_hash,
            enabled=enabled,
            name="test",
        )
        return raw_key

    def _create_read_credential(self, space, public_key):
        secret = f"sk-{public_key}"
        endpoint = ObservationEndpointConfig.objects.create(
            name=f"local-{public_key}",
            base_url="https://langfuse.example.com",
            tenant_name="local",
            status=1,
        )
        project = SpaceObservationProject.objects.create(
            space=space,
            endpoint_config=endpoint,
            tenant_name="local",
            status=1,
            provision_status=1,
        )
        credential = SpaceObservationCredential.objects.create(
            space_observation_project=project,
            scope="READ",
            public_key=public_key,
            secret_hash=hash_secret(secret),
            secret_ciphertext="unused-in-auth",
            status=1,
        )
        token = base64.b64encode(f"{credential.public_key}:{secret}".encode()).decode()
        return credential, token

    @patch("apps.core.views.openapi_trace.query_session_list")
    def test_authorization_bearer_authenticates_session_route_and_logs(
        self, mock_query_session_list
    ):
        mock_query_session_list.return_value = {"data": [{"session_id": "s-1"}]}

        response = self.client.post(
            self.url,
            {"page": 1},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {self.raw_key}",
        )

        self.assertEqual(response.status_code, 200, response.content)
        _, query_user, query_params, source_system = (
            mock_query_session_list.call_args.args
        )
        self.assertEqual(query_user.observation_subject_id, self.user.uuid)
        self.assertEqual(query_params["agent_product"], "ClaudeCode")
        self.assertIsNone(source_system)

    @patch("apps.core.views.openapi_trace.query_session_list")
    def test_authorization_basic_credential_is_rejected_for_personal_space(
        self, mock_query_session_list
    ):
        mock_query_session_list.return_value = {"data": []}
        _, token = self._create_read_credential(self.space, "pk-personal-read")

        response = self.client.post(
            self.url,
            {},
            format="json",
            HTTP_AUTHORIZATION=f"Basic {token}",
        )

        self.assertEqual(response.status_code, 401, response.content)
        mock_query_session_list.assert_not_called()

    @patch("apps.core.views.openapi_trace.query_session_list")
    def test_authorization_basic_credential_is_rejected_for_business_space(
        self, mock_query_session_list
    ):
        mock_query_session_list.return_value = {"data": []}
        business_space = Space.objects.create(
            name="Business Space",
            type=2,
            scope=1,
            creator=self.user,
            status=1,
        )
        _, token = self._create_read_credential(business_space, "pk-business-read")
        url = (
            f"/openapi/v1/spaces/{business_space.uuid}"
            "/agents/ClaudeCode/sessions/list"
        )

        response = self.client.post(
            url,
            {},
            format="json",
            HTTP_AUTHORIZATION=f"Basic {token}",
        )

        self.assertEqual(response.status_code, 401, response.content)
        mock_query_session_list.assert_not_called()

    @patch("apps.core.views.openapi_trace.query_session_list")
    def test_space_key_headers_are_not_supported(self, mock_query_session_list):
        response = self.client.post(
            self.url,
            {},
            format="json",
            HTTP_X_SPACE_KEY=self.raw_key,
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], 4001)
        mock_query_session_list.assert_not_called()

    def test_aivision_key_header_is_not_supported(self):
        response = self.client.post(
            self.url,
            {},
            format="json",
            HTTP_X_AIVISION_KEY=self.raw_key,
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], 4001)

    def test_space_key_access_requires_explicit_space_key_auth_type(self):
        request = SimpleNamespace(auth=SimpleNamespace(space=self.space))

        space, err = check_space_key_access(request, str(self.space.uuid))

        self.assertIsNone(space)
        self.assertEqual(err.status_code, 401)
        self.assertEqual(err.data["code"], 4001)

    def test_credential_space_access_requires_auth_object(self):
        request = SimpleNamespace(auth_type="credential", auth=None)

        space, err = check_space_key_access(request, str(self.space.uuid))

        self.assertIsNone(space)
        self.assertEqual(err.status_code, 401)
        self.assertEqual(err.data["code"], 4001)

    def test_authorization_bearer_space_key_is_accepted(self):
        response = self.client.post(
            self.url,
            {},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {self.raw_key}",
        )

        self.assertNotEqual(response.status_code, 401)
        self.assertNotEqual(response.status_code, 403)

    def test_wrong_space_and_disabled_key_are_rejected(self):
        disabled_key = self._create_space_key(self.space, self.user, enabled=False)

        wrong_space = self.client.post(
            self.url,
            {},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {self.other_raw_key}",
        )
        disabled = self.client.post(
            self.url,
            {},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {disabled_key}",
        )

        self.assertEqual(wrong_space.status_code, 403)
        self.assertEqual(wrong_space.json()["code"], 4003)
        self.assertEqual(disabled.status_code, 401)
        self.assertEqual(disabled.json()["code"], 4001)
