import json
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import resolve
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.tests.helpers import (
    create_test_space_member,
    create_test_agent,
    create_local_user,
)

from apps.core.models import (
    Agent,
    AgentTypeConfig,
    DataSource,
    ObservationEndpointConfig,
    Space,
    SpaceDatasourceBinding,
    SpaceMember,
    SpaceObservationCredential,
    SpaceObservationProject,
    User,
)
from apps.core.services.credential_service import encrypt_secret, hash_secret
from apps.core.trace_query.routing import resolve_trace_source_system
from apps.core.trace_query.types import TraceListQuery, TraceQueryServiceError
from apps.core.views import (
    SessionDetailView,
    SessionListView,
    TraceDetailView,
    TraceListView,
    build_langfuse_trace_filter,
)
from apps.core.views.langfuse import get_space_observation_runtime_config


class TraceRouteResolutionTests(SimpleTestCase):
    def test_specific_trace_session_routes_are_not_captured_by_trace_detail(self):
        routes = {
            "/api/v1/spaces/space-1/lftraces/sessions": "lf_session_list",
            "/api/v1/spaces/space-1/lftraces/sessions/session-123": "lf_session_detail",
            "/api/v1/spaces/space-1/lftraces/trace-123": "lf_trace_detail",
        }

        for route, expected_url_name in routes.items():
            with self.subTest(route=route):
                self.assertEqual(resolve(route).url_name, expected_url_name)


@override_settings(
    CREDENTIAL_ENCRYPTION_KEY="test-credential-key",
)
class SpaceTraceApiTests(TestCase):
    def setUp(self):
        self.user = create_local_user("100003", name="Trace User", status=1)
        self.endpoint = ObservationEndpointConfig.objects.create(
            name="default-langfuse",
            base_url="https://langfuse.example.com",
            tenant_name="tenant-a",
            backend_type="langfuse",
            is_default=1,
            status=1,
        )
        self.space = Space.objects.create(
            name="Business Trace Space",
            type=2,
            scope=1,
            creator=self.user,
            status=1,
        )
        create_test_space_member(space=self.space, user=self.user, role=1)
        self.project = SpaceObservationProject.objects.create(
            space=self.space,
            endpoint_config=self.endpoint,
            tenant_name="tenant-a",
            upstream_project_id="proj_xxx",
            upstream_project_name="Business Trace Space",
            status=1,
            provision_status=1,
        )
        self.trace_binding = SpaceDatasourceBinding.objects.create(
            space_observation_project=self.project,
            datasource_type="traces",
            source_system="langfuse",
            enabled=1,
            is_default=1,
            config={},
        )
        SpaceObservationCredential.objects.create(
            space_observation_project=self.project,
            scope="READ",
            public_key="pk-read-test",
            secret_hash=hash_secret("sk-read-test"),
            secret_ciphertext=encrypt_secret("sk-read-test"),
            name="default-read",
            status=1,
        )
        self.agent_type = AgentTypeConfig.objects.create(
            agent_name="eddy-trace",
            agent_product="eddy",
            status=1,
        )
        create_test_agent(
            space=self.space,
            agent_type=self.agent_type,
            agent_product="eddy",
            name="Eddy Trace Agent",
            status=1,
        )
        self.langfuse_datasource = DataSource.objects.create(
            agent_type=self.agent_type,
            datasource_type="traces",
            source_system="langfuse",
            enabled=1,
            is_default=1,
            config={},
        )

    def configure_langfuse_datasource(self):
        self.langfuse_datasource.config = {
            "langfuse_host": "https://langfuse.example.com",
            "langfuse_public_key": "pk-agent-test",
            "langfuse_secret_key": "sk-agent-test",
            "langfuse_project_id": "proj-agent-test",
        }
        self.langfuse_datasource.save(update_fields=["config"])

    @patch("apps.core.trace_query.langfuse.provider.http_requests.get")
    def test_trace_list_hides_langfuse_link_by_default_for_space_project(
        self, mock_get
    ):
        mock_response = Mock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "data": [],
            "meta": {"page": 1, "limit": 20, "totalPages": 1},
        }
        mock_get.return_value = mock_response

        request = APIRequestFactory().get(
            f"/api/v1/spaces/{self.space.uuid}/traces",
            {"page": 1, "limit": 20},
        )
        force_authenticate(request, user=self.user)

        response = TraceListView.as_view()(request, space_id=str(self.space.uuid))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data["data"]["langfuse_config"]["show_langfuse_link"],
            False,
        )
        self.assertEqual(
            response.data["data"]["langfuse_config"]["link_scope"],
            "project",
        )

    @patch("apps.core.trace_query.langfuse.provider.http_requests.get")
    def test_trace_list_can_enable_langfuse_link_from_binding_config(self, mock_get):
        self.trace_binding.config = {"show_langfuse_link": True}
        self.trace_binding.save(update_fields=["config"])
        mock_response = Mock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "data": [],
            "meta": {"page": 1, "limit": 20, "totalPages": 1},
        }
        mock_get.return_value = mock_response

        request = APIRequestFactory().get(
            f"/api/v1/spaces/{self.space.uuid}/traces",
            {"page": 1, "limit": 20},
        )
        force_authenticate(request, user=self.user)

        response = TraceListView.as_view()(request, space_id=str(self.space.uuid))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data["data"]["langfuse_config"]["show_langfuse_link"],
            True,
        )
        self.assertEqual(
            response.data["data"]["langfuse_config"]["link_scope"],
            "project",
        )

    @patch("apps.core.views.http_requests.get")
    @patch("apps.core.services.credential_service.decrypt_secret")
    def test_trace_list_returns_explicit_error_when_read_credential_cannot_be_decrypted(
        self,
        mock_decrypt,
        mock_get,
    ):
        mock_decrypt.side_effect = ValueError("invalid token")

        request = APIRequestFactory().get(
            f"/api/v1/spaces/{self.space.uuid}/traces",
            {"page": 1, "limit": 20},
        )
        force_authenticate(request, user=self.user)

        response = TraceListView.as_view()(request, space_id=str(self.space.uuid))

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], 3017)
        self.assertEqual(
            response.data["message"],
            "Failed to decrypt Langfuse READ credential for this space",
        )
        mock_get.assert_not_called()

    @patch("apps.core.views.http_requests.get")
    def test_trace_list_filters_by_tag_before_paginating(self, mock_get):
        mock_response = Mock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "data": [
                {
                    "id": "trace-1",
                    "input": {"text": "alpha"},
                    "tags": ["sdk:anthropic", "mcp:chrome_devtools"],
                },
                {
                    "id": "trace-2",
                    "input": {"text": "beta"},
                    "tags": ["sdk:anthropic"],
                },
                {
                    "id": "trace-3",
                    "input": {"text": "gamma"},
                    "tags": ["mcp:chrome_devtools"],
                },
            ],
            "meta": {"page": 1, "limit": 100, "totalPages": 1},
        }
        mock_get.return_value = mock_response

        request = APIRequestFactory().get(
            f"/api/v1/spaces/{self.space.uuid}/traces",
            {"page": 1, "limit": 1, "tag": "mcp:chrome_devtools"},
        )
        force_authenticate(request, user=self.user)

        response = TraceListView.as_view()(request, space_id=str(self.space.uuid))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["code"], 0)
        self.assertEqual(
            [item["id"] for item in response.data["data"]["data"]],
            ["trace-1"],
        )
        self.assertEqual(response.data["data"]["meta"]["totalItems"], 2)
        self.assertEqual(response.data["data"]["meta"]["totalPages"], 2)

        _, kwargs = mock_get.call_args
        self.assertEqual(kwargs["params"]["page"], 1)
        self.assertEqual(kwargs["params"]["limit"], 100)

    def test_build_langfuse_trace_filter_uses_business_space_agent_product_metadata_key(
        self,
    ):
        trace_filter = json.loads(build_langfuse_trace_filter(agent_product="eddy"))

        self.assertEqual(
            trace_filter,
            [
                {
                    "type": "stringObject",
                    "column": "metadata",
                    "key": "resourceAttributes.ant.agent.product",
                    "operator": "=",
                    "value": "eddy",
                }
            ],
        )

    @patch("apps.core.trace_query.langfuse.provider.http_requests.get")
    def test_trace_list_filters_business_space_by_selected_agent_product(
        self, mock_get
    ):
        self.configure_langfuse_datasource()
        mock_response = Mock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "data": [],
            "meta": {"totalPages": 1},
        }
        mock_get.return_value = mock_response

        request = APIRequestFactory().get(
            f"/api/v1/spaces/{self.space.uuid}/traces",
            {"page": 1, "limit": 20, "agent_product": "eddy"},
        )
        force_authenticate(request, user=self.user)

        response = TraceListView.as_view()(request, space_id=str(self.space.uuid))

        self.assertEqual(response.status_code, 200)
        _, kwargs = mock_get.call_args
        self.assertEqual(
            json.loads(kwargs["params"]["filter"]),
            [
                {
                    "type": "stringObject",
                    "column": "metadata",
                    "key": "resourceAttributes.ant.agent.product",
                    "operator": "=",
                    "value": "eddy",
                },
            ],
        )

    @patch("apps.core.views.http_requests.get")
    def test_session_list_filters_business_space_by_selected_agent_product(
        self, mock_get
    ):
        self.configure_langfuse_datasource()
        mock_response = Mock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "data": [],
            "meta": {"totalPages": 1},
        }
        mock_get.return_value = mock_response

        request = APIRequestFactory().get(
            f"/api/v1/spaces/{self.space.uuid}/sessions",
            {"page": 1, "limit": 20, "agent_product": "eddy"},
        )
        force_authenticate(request, user=self.user)

        response = SessionListView.as_view()(request, space_id=str(self.space.uuid))

        self.assertEqual(response.status_code, 200)
        _, kwargs = mock_get.call_args
        self.assertEqual(
            json.loads(kwargs["params"]["filter"]),
            [
                {
                    "type": "stringObject",
                    "column": "metadata",
                    "key": "resourceAttributes.ant.agent.product",
                    "operator": "=",
                    "value": "eddy",
                },
            ],
        )

    @patch("apps.core.views.http_requests.get")
    def test_session_detail_filters_business_space_by_selected_agent_product(
        self, mock_get
    ):
        self.configure_langfuse_datasource()
        mock_response = Mock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "data": [],
            "meta": {"totalPages": 1},
        }
        mock_get.return_value = mock_response

        request = APIRequestFactory().get(
            f"/api/v1/spaces/{self.space.uuid}/sessions/session-123",
            {"agent_product": "eddy"},
        )
        force_authenticate(request, user=self.user)

        response = SessionDetailView.as_view()(
            request,
            space_id=str(self.space.uuid),
            session_id="session-123",
        )

        self.assertEqual(response.status_code, 400)
        _, kwargs = mock_get.call_args
        self.assertEqual(
            json.loads(kwargs["params"]["filter"]),
            [
                {
                    "type": "stringObject",
                    "column": "metadata",
                    "key": "resourceAttributes.ant.agent.product",
                    "operator": "=",
                    "value": "eddy",
                },
                {
                    "type": "string",
                    "column": "sessionId",
                    "operator": "=",
                    "value": "session-123",
                },
            ],
        )

    def test_explicit_agent_requires_langfuse_datasource_before_project_fallback(self):
        agent_type = AgentTypeConfig.objects.create(
            agent_name="native-only",
            agent_product="NativeOnly",
            status=1,
        )
        create_test_agent(
            space=self.space,
            agent_type=agent_type,
            agent_product="NativeOnly",
            name="Native Only Agent",
            status=1,
        )
        DataSource.objects.create(
            agent_type=agent_type,
            datasource_type="traces",
            source_system="signoz",
            enabled=1,
            is_default=1,
            config={},
        )

        with self.assertRaises(TraceQueryServiceError) as ctx:
            get_space_observation_runtime_config(
                self.space,
                agent_product="NativeOnly",
            )

        self.assertEqual(ctx.exception.code, 3011)
        self.assertEqual(ctx.exception.message, "Agent query route not found")

    def test_explicit_agent_with_incomplete_langfuse_datasource_fails_closed(self):
        with self.assertRaises(TraceQueryServiceError) as ctx:
            get_space_observation_runtime_config(
                self.space,
                agent_product="eddy",
            )

        self.assertEqual(ctx.exception.code, 3011)
        self.assertEqual(ctx.exception.message, "Agent query route not found")

    def test_productless_runtime_config_falls_back_to_space_project_when_langfuse_route_is_ambiguous(
        self,
    ):
        self.configure_langfuse_datasource()
        self.langfuse_datasource.is_default = 0
        self.langfuse_datasource.save(update_fields=["is_default"])
        other_agent_type = AgentTypeConfig.objects.create(
            agent_name="other-langfuse",
            agent_product="OtherLangfuse",
            status=1,
        )
        create_test_agent(
            space=self.space,
            agent_type=other_agent_type,
            agent_product="OtherLangfuse",
            name="Other Langfuse Agent",
            status=1,
        )
        DataSource.objects.create(
            agent_type=other_agent_type,
            datasource_type="traces",
            source_system="langfuse",
            enabled=1,
            is_default=0,
            config={
                "langfuse_host": "https://other-langfuse.example.com",
                "langfuse_public_key": "pk-other",
                "langfuse_secret_key": "sk-other",
            },
        )

        config = get_space_observation_runtime_config(self.space)

        self.assertEqual(config["source"], "space_project")
        self.assertEqual(config["public_key"], "pk-read-test")
        self.assertEqual(config["host"], "https://langfuse.example.com")

    def test_productless_source_resolution_skips_ambiguous_source_fallback(self):
        self.langfuse_datasource.is_default = 0
        self.langfuse_datasource.save(update_fields=["is_default"])
        for index in range(2):
            agent_type = AgentTypeConfig.objects.create(
                agent_name=f"ambiguous-stargazer-{index}",
                agent_product=f"AmbiguousStargazer{index}",
                status=1,
            )
            create_test_agent(
                space=self.space,
                agent_type=agent_type,
                agent_product=agent_type.agent_product,
                name=agent_type.agent_product,
                status=1,
            )
            DataSource.objects.create(
                agent_type=agent_type,
                datasource_type="traces",
                source_system="stargazer",
                enabled=1,
                is_default=0,
                config={},
            )

        self.assertEqual(resolve_trace_source_system(self.space), "langfuse")

    def test_productless_source_resolution_prefers_configured_default_signoz(self):
        self.langfuse_datasource.is_default = 0
        self.langfuse_datasource.save(update_fields=["is_default"])
        signoz_agent_type = AgentTypeConfig.objects.create(
            agent_name="default-signoz",
            agent_product="DefaultSignoz",
            status=1,
        )
        create_test_agent(
            space=self.space,
            agent_type=signoz_agent_type,
            agent_product=signoz_agent_type.agent_product,
            name=signoz_agent_type.agent_product,
            status=1,
        )
        DataSource.objects.create(
            agent_type=signoz_agent_type,
            datasource_type="traces",
            source_system="signoz",
            enabled=1,
            is_default=1,
            config={},
        )

        self.assertEqual(resolve_trace_source_system(self.space), "signoz")

    def test_productless_ambiguous_source_resolution_considers_signoz_first(self):
        self.langfuse_datasource.is_default = 0
        self.langfuse_datasource.save(update_fields=["is_default"])
        signoz_agent_type = AgentTypeConfig.objects.create(
            agent_name="fallback-signoz",
            agent_product="FallbackSignoz",
            status=1,
        )
        create_test_agent(
            space=self.space,
            agent_type=signoz_agent_type,
            agent_product=signoz_agent_type.agent_product,
            name=signoz_agent_type.agent_product,
            status=1,
        )
        DataSource.objects.create(
            agent_type=signoz_agent_type,
            datasource_type="traces",
            source_system="signoz",
            enabled=1,
            is_default=0,
            config={},
        )

        self.assertEqual(resolve_trace_source_system(self.space), "signoz")

    # --- TraceDetailView: not-found error handling ---

    @patch("apps.core.views.trace.get_default_trace_query_service")
    def test_trace_detail_returns_200_with_code_3019_when_trace_not_found(
        self, mock_svc
    ):
        mock_svc.return_value.get_trace_detail.side_effect = TraceQueryServiceError(
            "Trace not found", 3019
        )

        request = APIRequestFactory().get(
            f"/api/v1/spaces/{self.space.uuid}/traces/nonexistent-trace-id",
        )
        force_authenticate(request, user=self.user)

        response = TraceDetailView.as_view()(
            request,
            space_id=str(self.space.uuid),
            trace_id="nonexistent-trace-id",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["code"], 3019)
        self.assertEqual(response.data["message"], "Trace not found")
        self.assertIsNone(response.data["data"])

    @patch("apps.core.views.trace.get_default_trace_query_service")
    def test_trace_detail_returns_400_for_non_3019_service_errors(self, mock_svc):
        mock_svc.return_value.get_trace_detail.side_effect = TraceQueryServiceError(
            "Stargazer query failed", 3011
        )

        request = APIRequestFactory().get(
            f"/api/v1/spaces/{self.space.uuid}/traces/some-trace-id",
        )
        force_authenticate(request, user=self.user)

        response = TraceDetailView.as_view()(
            request,
            space_id=str(self.space.uuid),
            trace_id="some-trace-id",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], 3011)

    @patch("apps.core.views.trace.get_default_trace_query_service")
    def test_trace_detail_returns_200_with_data_when_trace_exists(self, mock_svc):
        mock_trace = {
            "id": "existing-trace-id",
            "name": "test-trace",
            "observations": [],
            "scores": [],
        }
        mock_svc.return_value.get_trace_detail.return_value = mock_trace

        request = APIRequestFactory().get(
            f"/api/v1/spaces/{self.space.uuid}/traces/existing-trace-id",
        )
        force_authenticate(request, user=self.user)

        response = TraceDetailView.as_view()(
            request,
            space_id=str(self.space.uuid),
            trace_id="existing-trace-id",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["code"], 0)
        self.assertEqual(response.data["data"]["id"], "existing-trace-id")

    @patch("apps.core.trace_query.langfuse.provider.http_requests.get")
    def test_trace_detail_langfuse_404_returns_200_with_code_3019(self, mock_get):
        mock_response = Mock()
        mock_response.status_code = 404
        mock_get.return_value = mock_response

        request = APIRequestFactory().get(
            f"/api/v1/spaces/{self.space.uuid}/traces/nonexistent-trace-id",
        )
        force_authenticate(request, user=self.user)

        response = TraceDetailView.as_view()(
            request,
            space_id=str(self.space.uuid),
            trace_id="nonexistent-trace-id",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["code"], 3019)
        self.assertEqual(response.data["message"], "Trace not found")
        self.assertIsNone(response.data["data"])
