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
class ExternalTraceShareApiTests(SpaceKeyTestCase):
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
            "/agents/autopilot-v3-prod/trace-share-links"
        )
        self.payload = {
            "trace_id": "trace-1",
            "from": "2026-08-28T10:00:00+08:00",
            "to": "2026-08-28T11:00:00+08:00",
        }

    @staticmethod
    def _create_space_key(space, creator):
        raw_key, key_prefix, key_hash = SpaceKey.generate_key()
        SpaceKey.objects.create(
            space=space,
            creator=creator,
            key_prefix=key_prefix,
            key_hash=key_hash,
            enabled=True,
            name="trace share integration",
        )
        return raw_key

    @patch(
        "apps.core.services.session_share_link_service.get_default_trace_query_service"
    )
    def test_generates_signoz_trace_share_link(self, mock_get_service):
        mock_get_service.return_value.get_trace_detail.return_value = {
            "traceId": "trace-1",
            "sessionId": "session-1",
            "spans": [{"span_id": "span-1"}],
        }

        response = self.client.post(self.url, self.payload, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["code"], 0)
        self.assertEqual(response.data["message"], "success")
        self.assertEqual(set(response.data["data"]), {"url", "expires_at"})

        parsed_url = urlparse(response.data["data"]["url"])
        self.assertEqual(parsed_url.path, "/share/trace")
        query = parse_qs(parsed_url.query)
        claims = parse_and_verify_share(query["payload"][0], query["sig"][0])
        self.assertEqual(claims["t"], "trace")
        self.assertEqual(claims["id"], "trace-1")
        self.assertEqual(claims["s"], self.space.id)
        self.assertEqual(claims["a"], "autopilot-v3-prod")
        self.assertEqual(claims["p"], "signoz")
        self.assertEqual(claims["uid"], self.owner.uuid)
        self.assertEqual(claims["sub"], self.owner.uuid)
        self.assertEqual(claims["qf"], "2026-08-28T10:00:00+08:00")
        self.assertEqual(claims["qt"], "2026-08-28T11:00:00+08:00")

        call = mock_get_service.return_value.get_trace_detail.call_args.kwargs
        self.assertEqual(call["space"], self.space)
        self.assertEqual(call["user"], self.owner)
        self.assertEqual(call["trace_id"], "trace-1")
        self.assertEqual(
            call["query_params"],
            {
                "agent_product": "autopilot-v3-prod",
                "from": "2026-08-28T10:00:00+08:00",
                "to": "2026-08-28T11:00:00+08:00",
            },
        )
        self.assertEqual(call["source_system_override"], "signoz")

    @patch(
        "apps.core.services.session_share_link_service.get_default_trace_query_service"
    )
    def test_generates_signoz_trace_share_link(self, mock_get_service):
        self.datasource.source_system = "signoz"
        self.datasource.save(update_fields=["source_system"])
        mock_get_service.return_value.get_trace_detail.return_value = {
            "traceId": "trace-1",
            "spans": [{"span_id": "span-1"}],
        }

        response = self.client.post(self.url, self.payload, format="json")

        self.assertEqual(response.status_code, 200)
        parsed_url = urlparse(response.data["data"]["url"])
        query = parse_qs(parsed_url.query)
        claims = parse_and_verify_share(query["payload"][0], query["sig"][0])
        self.assertEqual(claims["p"], "signoz")
        call = mock_get_service.return_value.get_trace_detail.call_args.kwargs
        self.assertEqual(call["source_system_override"], "signoz")

    def test_requires_trace_id_and_time_boundaries(self):
        cases = [
            (
                {"from": self.payload["from"], "to": self.payload["to"]},
                "trace_id is required",
            ),
            ({"trace_id": "trace-1", "to": self.payload["to"]}, "from is required"),
            ({"trace_id": "trace-1", "from": self.payload["from"]}, "to is required"),
        ]
        for payload, message in cases:
            with self.subTest(message=message):
                response = self.client.post(self.url, payload, format="json")
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.data["code"], 4000)
                self.assertEqual(response.data["message"], message)
                self.assertIsNone(response.data["data"])

    def test_rejects_invalid_or_unsafe_time_ranges(self):
        cases = [
            (
                {**self.payload, "from": "2026-08-28T10:00:00"},
                "from must be an ISO 8601 datetime with timezone",
            ),
            (
                {**self.payload, "to": "2026-08-28T09:00:00+08:00"},
                "to must be later than from",
            ),
            (
                {**self.payload, "to": "2026-08-29T10:00:01+08:00"},
                "Trace share time range cannot exceed 24 hours",
            ),
        ]
        for payload, message in cases:
            with self.subTest(message=message):
                response = self.client.post(self.url, payload, format="json")
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.data["code"], 4000)
                self.assertEqual(response.data["message"], message)

    def test_rejects_unsupported_fields(self):
        response = self.client.post(
            self.url,
            {**self.payload, "protocol": "signoz"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], 4000)
        self.assertEqual(
            response.data["message"], "Only trace_id, from and to are supported"
        )

    def test_missing_space_key_is_rejected(self):
        response = APIClient().post(self.url, self.payload, format="json")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.data["code"], 4001)
        self.assertEqual(response.data["message"], "Invalid or missing Space Key")

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

        response = APIClient().post(
            self.url,
            self.payload,
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {other_key}",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["code"], 4003)
        self.assertEqual(
            response.data["message"], "Space Key does not match the requested space"
        )
        mock_get_service.assert_not_called()

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

        response = self.client.post(self.url, self.payload, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], 3011)
        mock_get_service.assert_not_called()

    @patch(
        "apps.core.services.session_share_link_service.get_default_trace_query_service"
    )
    def test_empty_trace_does_not_generate_link(self, mock_get_service):
        mock_get_service.return_value.get_trace_detail.return_value = {
            "traceId": "trace-1",
            "spans": [],
        }

        response = self.client.post(self.url, self.payload, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], 3019)
        self.assertEqual(response.data["message"], "Trace not found")

    @patch(
        "apps.core.services.session_share_link_service.get_default_trace_query_service"
    )
    def test_preserves_trace_query_error_code(self, mock_get_service):
        mock_get_service.return_value.get_trace_detail.side_effect = (
            TraceQueryServiceError("Stargazer scan limit exceeded", 3012)
        )

        response = self.client.post(self.url, self.payload, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], 3012)
        self.assertEqual(response.data["message"], "Stargazer scan limit exceeded")

    @patch("apps.core.views.share.get_default_trace_query_service")
    @patch(
        "apps.core.services.session_share_link_service.get_default_trace_query_service"
    )
    def test_generated_link_is_consumed_by_public_share_api(
        self, mock_get_create_service, mock_get_share_service
    ):
        trace = {
            "traceId": "trace-1",
            "sessionId": "session-1",
            "spans": [{"span_id": "span-1"}],
        }
        mock_get_create_service.return_value.get_trace_detail.return_value = trace
        mock_get_share_service.return_value.get_trace_detail.return_value = trace
        generated = self.client.post(self.url, self.payload, format="json")
        query = urlparse(generated.data["data"]["url"]).query

        response = APIClient().get(f"/api/v1/share/data?{query}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["code"], 0)
        self.assertEqual(response.data["data"]["resource_type"], "trace")
        self.assertEqual(response.data["data"]["trace"], trace)
        self.assertEqual(response.data["data"]["agent_product"], "autopilot-v3-prod")
        self.assertEqual(response.data["data"]["protocol"], "signoz")
        call = mock_get_share_service.return_value.get_trace_detail.call_args.kwargs
        self.assertEqual(call["query_params"]["from"], self.payload["from"])
        self.assertEqual(call["query_params"]["to"], self.payload["to"])
        self.assertEqual(call["source_system_override"], "signoz")
