from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.test import override_settings
from rest_framework.test import APIClient

from apps.core.models import (
    Agent,
    AgentTypeConfig,
    DataSource,
    ObservationSubjectBinding,
    Space,
    SpaceKey,
    User,
)
from apps.core.tests.helpers import create_test_agent, SpaceKeyTestCase
from apps.core.trace_query.types import TraceQueryServiceError
from apps.core.utils.share_signing import parse_and_verify_share


@override_settings(
    SITE_URL="https://aivision.example.com",
    SHARE_LINK_SIGNING_KEY="test-share-signing-key",
    SHARE_LINK_TTL_SECONDS=3600,
)
class ExternalSessionShareApiTests(SpaceKeyTestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.owner = User.objects.create(
            name="Owner",
            email="owner@example.com",
            normalized_email="owner@example.com",
            password="!",
            status=1,
        )
        self.space = Space.objects.create(
            name="黑卡用户技术部",
            type=2,
            scope=1,
            creator=self.owner,
            status=1,
        )
        self.agent_type = AgentTypeConfig.objects.create(
            agent_name="autopilot-v3-prod",
            agent_product="autopilot-v3-prod",
            runtime_type="other",
            display_name="Autopilot V3",
            status=1,
        )
        subject_binding = ObservationSubjectBinding.objects.create(
            space=self.space,
            user=self.owner,
            source_system="signoz",
            subject_id="owner-subject",
            binding_source="test",
            created_by=self.owner,
        )
        create_test_agent(
            space=self.space,
            agent_type=self.agent_type,
            agent_product="autopilot-v3-prod",
            observation_subject_binding=subject_binding,
            name="Autopilot V3",
            status=1,
        )
        self.datasource = DataSource.objects.create(
            agent_type=self.agent_type,
            datasource_type="traces",
            source_system="signoz",
            enabled=1,
            is_default=1,
            config={"table_name": "aivision_trace"},
        )
        self.raw_space_key = self._create_space_key(self.space, self.owner)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.raw_space_key}")
        self.url = (
            f"/openapi/v1/spaces/{self.space.uuid}"
            "/agents/autopilot-v3-prod/session-share-links"
        )

    @staticmethod
    def _create_space_key(space, creator, *, enabled=True):
        raw_key, key_prefix, key_hash = SpaceKey.generate_key()
        SpaceKey.objects.create(
            space=space,
            creator=creator,
            key_prefix=key_prefix,
            key_hash=key_hash,
            enabled=enabled,
            name="session share integration",
        )
        return raw_key

    @patch(
        "apps.core.services.session_share_link_service.get_default_trace_query_service"
    )
    def test_generates_signoz_session_share_link(self, mock_get_service):
        query_service = mock_get_service.return_value
        query_service.get_session_detail.return_value = {
            "sessionId": "session-1",
            "traceCount": 2,
            "traceIds": ["trace-1", "trace-2"],
            "timeRange": {
                "from": "2026-07-28T10:00:00+08:00",
                "to": "2026-07-28T12:00:00+08:00",
            },
        }

        response = self.client.post(
            self.url,
            {"session_id": " session-1 "},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["code"], 0)
        self.assertEqual(response.data["message"], "success")
        self.assertEqual(
            set(response.data["data"]),
            {"url", "expires_at"},
        )

        share_url = response.data["data"]["url"]
        parsed_url = urlparse(share_url)
        self.assertEqual(parsed_url.scheme, "https")
        self.assertEqual(parsed_url.netloc, "aivision.example.com")
        self.assertEqual(parsed_url.path, "/share/session")
        query = parse_qs(parsed_url.query)
        claims = parse_and_verify_share(query["payload"][0], query["sig"][0])
        self.assertEqual(claims["t"], "session")
        self.assertEqual(claims["id"], "session-1")
        self.assertEqual(claims["s"], self.space.id)
        self.assertEqual(claims["a"], "autopilot-v3-prod")
        self.assertEqual(claims["p"], "signoz")
        self.assertEqual(claims["uid"], self.owner.uuid)
        self.assertEqual(claims["sub"], self.owner.uuid)
        self.assertEqual(claims["qf"], "2026-07-28T10:00:00+08:00")
        self.assertEqual(claims["qt"], "2026-07-28T12:00:00+08:00")

        call = query_service.get_session_detail.call_args.kwargs
        self.assertEqual(call["space"], self.space)
        self.assertEqual(call["session_id"], "session-1")
        self.assertEqual(call["query_params"], {"agent_product": "autopilot-v3-prod"})
        self.assertEqual(call["source_system_override"], "signoz")

    @patch(
        "apps.core.services.session_share_link_service.get_default_trace_query_service"
    )
    def test_generates_signoz_session_share_link(self, mock_get_service):
        self.datasource.source_system = "signoz"
        self.datasource.save(update_fields=["source_system"])
        mock_get_service.return_value.get_session_detail.return_value = {
            "sessionId": "session-1",
            "traceIds": ["trace-1"],
            "timeRange": {
                "from": "2026-07-28T10:00:00+08:00",
                "to": "2026-07-28T12:00:00+08:00",
            },
        }
        mock_get_service.return_value.list_traces.return_value = {"data": []}
        mock_get_service.return_value.get_trace_io_batch.return_value = {"data": []}

        response = self.client.post(
            self.url,
            {"session_id": "session-1"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        parsed_url = urlparse(response.data["data"]["url"])
        query = parse_qs(parsed_url.query)
        claims = parse_and_verify_share(query["payload"][0], query["sig"][0])
        self.assertEqual(claims["p"], "signoz")
        call = mock_get_service.return_value.get_session_detail.call_args.kwargs
        self.assertEqual(call["source_system_override"], "signoz")
        self.assertEqual(call["user"], self.owner)

    @patch(
        "apps.core.services.session_share_link_service.get_default_trace_query_service"
    )
    def test_resolves_agent_through_agent_type_product(self, mock_get_service):
        Agent.objects.filter(space=self.space).update(
            agent_product="legacy-agent-product"
        )
        mock_get_service.return_value.get_session_detail.return_value = {
            "sessionId": "session-1",
            "traceCount": 1,
            "traceIds": ["trace-1"],
            "timeRange": {
                "from": "2026-07-28T10:00:00+08:00",
                "to": "2026-07-28T12:00:00+08:00",
            },
        }

        response = self.client.post(
            self.url,
            {"session_id": "session-1"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["code"], 0)
        call = mock_get_service.return_value.get_session_detail.call_args.kwargs
        self.assertEqual(call["query_params"], {"agent_product": "autopilot-v3-prod"})
        share_url = response.data["data"]["url"]
        parsed_url = urlparse(share_url)
        query = parse_qs(parsed_url.query)
        claims = parse_and_verify_share(query["payload"][0], query["sig"][0])
        self.assertEqual(claims["a"], "autopilot-v3-prod")

    def test_missing_session_id_returns_standard_error_envelope(self):
        response = self.client.post(self.url, {}, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data,
            {
                "code": 4000,
                "message": "session_id is required",
                "data": None,
            },
        )

    def test_rejects_caller_supplied_time_or_protocol(self):
        response = self.client.post(
            self.url,
            {
                "session_id": "session-1",
                "from": "2026-07-28T10:00:00+08:00",
                "to": "2026-07-28T12:00:00+08:00",
                "protocol": "signoz",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], 4000)
        self.assertEqual(response.data["message"], "Only session_id is supported")
        self.assertIsNone(response.data["data"])

    def test_missing_space_key_returns_standard_error_envelope(self):
        client = APIClient()
        response = client.post(
            self.url,
            {"session_id": "session-1"},
            format="json",
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.data,
            {
                "code": 4001,
                "message": "Invalid or missing Space Key",
                "data": None,
            },
        )

    def test_fixed_external_business_space_token_is_not_accepted(self):
        client = APIClient()
        response = client.post(
            self.url,
            {"session_id": "session-1"},
            format="json",
            HTTP_AUTHORIZATION="Bearer test-external-token",
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.data["code"], 4001)
        self.assertIsNone(response.data["data"])

    @patch(
        "apps.core.services.session_share_link_service.get_default_trace_query_service"
    )
    def test_space_key_cannot_access_another_space(self, mock_get_service):
        other_space = Space.objects.create(
            name="另一个业务空间",
            type=2,
            scope=1,
            creator=self.owner,
            status=1,
        )
        other_key = self._create_space_key(other_space, self.owner)
        self.reset_key_cache()
        client = APIClient()
        response = client.post(
            self.url,
            {"session_id": "session-1"},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {other_key}",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["code"], 4003)
        self.assertEqual(
            response.data["message"], "Space Key does not match the requested space"
        )
        mock_get_service.assert_not_called()

    def test_disabled_space_key_is_rejected(self):
        disabled_key = self._create_space_key(self.space, self.owner, enabled=False)
        self.reset_key_cache()
        client = APIClient()
        response = client.post(
            self.url,
            {"session_id": "session-1"},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {disabled_key}",
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.data["code"], 4001)
        self.assertIsNone(response.data["data"])

    @patch(
        "apps.core.services.session_share_link_service.get_default_trace_query_service"
    )
    def test_rejects_non_native_datasource(self, mock_get_service):
        self.datasource.delete()
        DataSource.objects.create(
            agent_type=self.agent_type,
            datasource_type="traces",
            source_system="langfuse",
            enabled=1,
            is_default=1,
            config={"langfuse_host": "http://langfuse.test"},
        )

        response = self.client.post(
            self.url,
            {"session_id": "session-1"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], 3011)
        self.assertIsNone(response.data["data"])
        mock_get_service.assert_not_called()

    @patch(
        "apps.core.services.session_share_link_service.get_default_trace_query_service"
    )
    def test_empty_session_does_not_generate_link(self, mock_get_service):
        mock_get_service.return_value.get_session_detail.return_value = {
            "sessionId": "session-1",
            "traceCount": 0,
            "traceIds": [],
            "timeRange": {
                "from": "2026-07-28T10:00:00+08:00",
                "to": "2026-07-28T12:00:00+08:00",
            },
        }

        response = self.client.post(
            self.url,
            {"session_id": "session-1"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], 3016)
        self.assertEqual(response.data["message"], "Session not found")
        self.assertIsNone(response.data["data"])

    @patch(
        "apps.core.services.session_share_link_service.get_default_trace_query_service"
    )
    def test_missing_time_boundary_does_not_generate_link(self, mock_get_service):
        mock_get_service.return_value.get_session_detail.return_value = {
            "sessionId": "session-1",
            "traceCount": 1,
            "traceIds": ["trace-1"],
            "timeRange": {"from": "2026-07-28T10:00:00+08:00"},
        }

        response = self.client.post(
            self.url,
            {"session_id": "session-1"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], 3016)
        self.assertEqual(response.data["message"], "Session time range not found")

    @patch(
        "apps.core.services.session_share_link_service.get_default_trace_query_service"
    )
    def test_preserves_trace_query_error_code(self, mock_get_service):
        mock_get_service.return_value.get_session_detail.side_effect = (
            TraceQueryServiceError("Stargazer scan limit exceeded", 3012)
        )

        response = self.client.post(
            self.url,
            {"session_id": "session-1"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], 3012)
        self.assertEqual(response.data["message"], "Stargazer scan limit exceeded")
        self.assertIsNone(response.data["data"])

    @patch(
        "apps.core.services.session_share_link_service.get_default_trace_query_service"
    )
    def test_rejects_agent_not_bound_to_space(self, mock_get_service):
        Agent.objects.filter(space=self.space).delete()

        response = self.client.post(
            self.url,
            {"session_id": "session-1"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], 1006)
        self.assertIsNone(response.data["data"])
        mock_get_service.assert_not_called()

    @override_settings(SITE_URL="")
    @patch(
        "apps.core.services.session_share_link_service.get_default_trace_query_service"
    )
    def test_requires_absolute_site_url(self, mock_get_service):
        mock_get_service.return_value.get_session_detail.return_value = {
            "sessionId": "session-1",
            "traceCount": 1,
            "traceIds": ["trace-1"],
            "timeRange": {
                "from": "2026-07-28T10:00:00+08:00",
                "to": "2026-07-28T12:00:00+08:00",
            },
        }

        response = self.client.post(
            self.url,
            {"session_id": "session-1"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], 3011)
        self.assertEqual(response.data["message"], "SITE_URL is not configured")
