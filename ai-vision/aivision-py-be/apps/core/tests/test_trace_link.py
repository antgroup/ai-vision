import base64
import hashlib
import hmac
import json
from unittest.mock import Mock, patch

from django.db import DatabaseError
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIRequestFactory

from apps.core.tests.helpers import create_test_agent

from apps.core.models import (
    Agent,
    AgentTypeConfig,
    DataSource,
    ObservationEndpointConfig,
    ObservationSubjectBinding,
    Space,
    SpaceDatasourceBinding,
    SpaceObservationCredential,
    SpaceObservationProject,
    User,
)
from apps.core.models.model_exchange_rates import (
    ModelExchangeRateSnapshot,
    ModelExchangeRateState,
)
from apps.core.services.credential_service import encrypt_secret, hash_secret
from apps.core.views import (
    ObservationLinkDetailView,
    TraceLinkDetailView,
    TraceLinkObservationDetailView,
)


@override_settings(
    CREDENTIAL_ENCRYPTION_KEY="test-credential-key",
)
class TraceLinkApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(
            name="Trace Link User",
            email="trace-link@example.com",
            normalized_email="trace-link@example.com",
            password="!",
            status=1,
        )
        self.endpoint = ObservationEndpointConfig.objects.create(
            name="default-langfuse-trace-link",
            base_url="https://langfuse.example.com",
            tenant_name="tenant-a",
            backend_type="langfuse",
            is_default=1,
            status=1,
        )
        self.space = Space.objects.create(
            name="Business Deeplink Space",
            type=2,
            scope=1,
            creator=self.user,
            status=1,
        )
        self.project = SpaceObservationProject.objects.create(
            space=self.space,
            endpoint_config=self.endpoint,
            tenant_name="tenant-a",
            upstream_project_id="proj_trace_link",
            upstream_project_name="Business Deeplink Space",
            status=1,
            provision_status=1,
        )
        SpaceDatasourceBinding.objects.create(
            space_observation_project=self.project,
            datasource_type="traces",
            source_system="langfuse",
            enabled=1,
            is_default=1,
            config={},
        )
        self.secret_key = "sk-read-trace-link"
        SpaceObservationCredential.objects.create(
            space_observation_project=self.project,
            scope="READ",
            public_key="pk-read-trace-link",
            secret_hash=hash_secret(self.secret_key),
            secret_ciphertext=encrypt_secret(self.secret_key),
            name="trace-link-read",
            status=1,
        )
        self.agent_type = AgentTypeConfig.objects.create(
            agent_name="codex-trace-link",
            agent_product="codex",
            status=1,
        )
        subject_binding = ObservationSubjectBinding.objects.create(
            space=self.space,
            user=self.user,
            source_system="langfuse",
            subject_id="trace-link-subject",
            binding_source="test",
            created_by=self.user,
        )
        create_test_agent(
            space=self.space,
            agent_type=self.agent_type,
            agent_product="codex",
            observation_subject_binding=subject_binding,
            name="Codex Trace Link Agent",
            status=1,
        )
        self.langfuse_datasource = DataSource.objects.create(
            agent_type=self.agent_type,
            datasource_type="traces",
            source_system="langfuse",
            enabled=1,
            is_default=1,
            config={
                "langfuse_host": "https://langfuse.example.com",
                "langfuse_public_key": "pk-agent-trace-link",
                "langfuse_secret_key": "sk-agent-trace-link",
                "langfuse_project_id": "proj_trace_link",
            },
        )

    @staticmethod
    def _b64url(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode("utf-8")

    def _build_signed_query(self, **overrides):
        payload_obj = {
            "space_id": str(self.space.uuid),
            "trace_id": "trace-123",
            "agent_product": "codex",
            "iat": 1_700_000_000,
            "exp": 1_700_000_600,
            "v": 1,
        }
        payload_obj.update(overrides)
        payload = self._b64url(
            json.dumps(payload_obj, separators=(",", ":")).encode("utf-8")
        )
        sig = self._b64url(
            hmac.new(
                self.secret_key.encode("utf-8"),
                payload.encode("utf-8"),
                hashlib.sha256,
            ).digest()
        )
        return payload, sig

    @patch("apps.core.views.trace.get_default_trace_query_service")
    def test_signed_detail_includes_persisted_display_rates(self, mock_get_service):
        now = timezone.now()
        rates = {"USD": "1", "CNY": "7", "JPY": "150"}
        snapshot = ModelExchangeRateSnapshot.objects.create(
            snapshot_id="signed-link-test",
            rate_date=now.date(),
            rates=rates,
        )
        ModelExchangeRateState.objects.create(snapshot=snapshot)
        service = mock_get_service.return_value
        service.get_trace_detail.return_value = {
            "id": "trace-123",
            "observations": [{"id": "obs-1"}],
        }
        service.get_observation_detail.return_value = {"id": "obs-1"}
        payload, sig = self._build_signed_query(
            iat=int(now.timestamp()) - 1,
            exp=int(now.timestamp()) + 300,
            observation_id="obs-1",
        )
        for view, path in (
            (TraceLinkDetailView, "/api/v1/trace-link/detail"),
            (ObservationLinkDetailView, "/api/v1/observation-link/detail"),
        ):
            with self.subTest(path=path):
                response = view.as_view()(
                    APIRequestFactory().get(path, {"payload": payload, "sig": sig})
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    response.data["data"]["exchange_rates"],
                    {"rates": rates, "rateDate": now.date().isoformat(), "status": "ready"},
                )

    @patch("apps.core.views.trace.exchange_rate_status")
    @patch("apps.core.views.langfuse.timezone.now")
    @patch("apps.core.views.trace.get_default_trace_query_service")
    def test_observation_link_checks_membership_before_loading_fx(
        self, mock_get_service, mock_now, mock_fx,
    ):
        mock_now.return_value.timestamp.return_value = 1_700_000_100
        mock_get_service.return_value.get_trace_detail.return_value = {
            "id": "trace-123",
            "observations": [{"id": "obs-1"}],
        }
        payload, sig = self._build_signed_query(observation_id="outside-trace")
        response = ObservationLinkDetailView.as_view()(
            APIRequestFactory().get(
                "/api/v1/observation-link/detail", {"payload": payload, "sig": sig}
            )
        )
        self.assertEqual(response.data["code"], 3025)
        mock_fx.assert_not_called()

    @patch("apps.core.views.trace.exchange_rate_status", side_effect=DatabaseError)
    @patch("apps.core.views.langfuse.timezone.now")
    @patch("apps.core.views.trace.get_default_trace_query_service")
    def test_signed_trace_is_available_when_display_rates_cannot_be_read(
        self, mock_get_service, mock_now, mock_fx,
    ):
        mock_now.return_value.timestamp.return_value = 1_700_000_100
        mock_get_service.return_value.get_trace_detail.return_value = {"id": "trace-123"}
        payload, sig = self._build_signed_query()
        with self.assertLogs("apps.core.views.trace", level="WARNING"):
            response = TraceLinkDetailView.as_view()(
                APIRequestFactory().get(
                    "/api/v1/trace-link/detail", {"payload": payload, "sig": sig}
                )
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["trace"]["id"], "trace-123")
        self.assertIsNone(response.data["data"]["exchange_rates"])
        mock_fx.assert_called_once_with()

    @patch("apps.core.views.trace.get_default_trace_query_service")
    def test_link_routes_map_unbound_agent_product_to_3011(
        self,
        mock_get_service,
    ):
        payload, sig = self._build_signed_query(
            agent_product="unbound-agent",
            observation_id="obs-1",
        )
        cases = [
            (TraceLinkDetailView, "/api/v1/trace-link/detail", {}),
            (
                TraceLinkObservationDetailView,
                "/api/v1/trace-link/observations/obs-1",
                {"observation_id": "obs-1"},
            ),
            (ObservationLinkDetailView, "/api/v1/observation-link/detail", {}),
        ]

        with patch("apps.core.views.langfuse.timezone.now") as mock_now:
            mock_now.return_value.timestamp.return_value = 1_700_000_100
            for view, path, kwargs in cases:
                with self.subTest(path=path):
                    request = APIRequestFactory().get(
                        path,
                        {"payload": payload, "sig": sig},
                    )
                    response = view.as_view()(request, **kwargs)

                    self.assertEqual(response.status_code, 400)
                    self.assertEqual(response.data["code"], 3011)
                    self.assertEqual(
                        response.data["message"],
                        "Agent query route not found",
                    )
        mock_get_service.assert_not_called()

    @patch("apps.core.views.langfuse.timezone.now")
    @patch("apps.core.views.trace.http_requests.get")
    @patch("apps.core.views.trace.get_default_trace_query_service")
    def test_trace_link_detail_returns_trace_when_signature_is_valid(
        self,
        mock_get_service,
        mock_get,
        mock_now,
    ):
        mock_now.return_value.timestamp.return_value = 1_700_000_100
        mock_service = Mock()
        mock_service.get_trace_detail.return_value = {
            "id": "trace-123",
            "name": "demo-trace",
            "observations": [],
        }
        mock_get_service.return_value = mock_service

        payload, sig = self._build_signed_query()
        request = APIRequestFactory().get(
            "/api/v1/trace-link/detail",
            {"payload": payload, "sig": sig},
        )

        response = TraceLinkDetailView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["code"], 0)
        self.assertEqual(response.data["data"]["trace"]["id"], "trace-123")
        self.assertEqual(response.data["data"]["agent_product"], "codex")
        mock_service.get_trace_detail.assert_called_once()
        mock_get.assert_not_called()

    @patch("apps.core.views.langfuse.timezone.now")
    @patch("apps.core.views.trace.get_default_trace_query_service")
    def test_trace_link_detail_accepts_trace_id_field_from_stargazer_provider(
        self,
        mock_get_service,
        mock_now,
    ):
        mock_now.return_value.timestamp.return_value = 1_700_000_100
        mock_service = Mock()
        mock_service.get_trace_detail.return_value = {
            "traceId": "trace-123",
            "spans": [],
            "tree": [],
        }
        mock_get_service.return_value = mock_service

        payload, sig = self._build_signed_query()
        request = APIRequestFactory().get(
            "/api/v1/trace-link/detail",
            {"payload": payload, "sig": sig},
        )

        response = TraceLinkDetailView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["code"], 0)
        self.assertEqual(response.data["data"]["trace"]["traceId"], "trace-123")
        mock_service.get_trace_detail.assert_called_once()

    @patch("apps.core.views.langfuse.timezone.now")
    def test_trace_link_detail_rejects_invalid_signature(self, mock_now):
        mock_now.return_value.timestamp.return_value = 1_700_000_100
        payload, _ = self._build_signed_query()
        request = APIRequestFactory().get(
            "/api/v1/trace-link/detail",
            {"payload": payload, "sig": "invalid-signature"},
        )

        with patch("apps.core.views.trace.exchange_rate_status") as mock_fx:
            response = TraceLinkDetailView.as_view()(request)
            mock_fx.assert_not_called()

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.data["code"], 3023)
        self.assertEqual(response.data["message"], "SHARE_SIGNATURE_INVALID")

    @patch("apps.core.views.langfuse.timezone.now")
    def test_trace_link_detail_rejects_expired_link(self, mock_now):
        mock_now.return_value.timestamp.return_value = 1_700_000_601
        payload, sig = self._build_signed_query()
        request = APIRequestFactory().get(
            "/api/v1/trace-link/detail",
            {"payload": payload, "sig": sig},
        )

        response = TraceLinkDetailView.as_view()(request)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.data["code"], 3023)
        self.assertEqual(response.data["message"], "SHARE_SIGNATURE_INVALID")

    @patch("apps.core.views.langfuse.timezone.now")
    def test_trace_link_detail_rejects_future_issued_link(self, mock_now):
        mock_now.return_value.timestamp.return_value = 1_700_000_100
        payload, sig = self._build_signed_query(
            iat=1_700_000_101,
            exp=1_700_000_600,
        )

        response = TraceLinkDetailView.as_view()(
            APIRequestFactory().get(
                "/api/v1/trace-link/detail",
                {"payload": payload, "sig": sig},
            )
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.data["message"], "SHARE_SIGNATURE_INVALID")

    def test_trace_link_signature_cannot_be_replayed_in_another_space(self):
        other_space = Space.objects.create(
            name="Other Space",
            type=2,
            scope=1,
            creator=self.user,
            status=1,
        )
        other_project = SpaceObservationProject.objects.create(
            space=other_space,
            endpoint_config=self.endpoint,
            tenant_name="tenant-a",
            upstream_project_id="other-project",
            upstream_project_name="Other Space",
            status=1,
            provision_status=1,
        )
        SpaceObservationCredential.objects.create(
            space_observation_project=other_project,
            scope="READ",
            public_key="pk-other",
            secret_hash=hash_secret("different-secret"),
            secret_ciphertext=encrypt_secret("different-secret"),
            name="other-read",
            status=1,
        )
        payload, sig = self._build_signed_query(space_id=other_space.uuid)

        with patch("apps.core.views.langfuse.timezone.now") as mock_now:
            mock_now.return_value.timestamp.return_value = 1_700_000_100
            response = TraceLinkDetailView.as_view()(
                APIRequestFactory().get(
                    "/api/v1/trace-link/detail",
                    {"payload": payload, "sig": sig},
                )
            )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.data["message"], "SHARE_SIGNATURE_INVALID")

    @patch("apps.core.views.langfuse.timezone.now")
    @patch("apps.core.views.trace.http_requests.get")
    @patch("apps.core.views.trace.get_default_trace_query_service")
    def test_trace_link_observation_detail_rejects_observation_outside_trace(
        self,
        mock_get_service,
        mock_get,
        mock_now,
    ):
        mock_now.return_value.timestamp.return_value = 1_700_000_100
        mock_service = Mock()
        mock_service.get_trace_detail.return_value = {
            "id": "trace-123",
            "observations": [{"id": "obs-1"}, {"id": "obs-2"}],
        }
        mock_get_service.return_value = mock_service

        payload, sig = self._build_signed_query()
        request = APIRequestFactory().get(
            "/api/v1/trace-link/observations/obs-999",
            {"payload": payload, "sig": sig},
        )

        response = TraceLinkObservationDetailView.as_view()(
            request,
            observation_id="obs-999",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], 3025)
        self.assertEqual(
            response.data["message"],
            "Observation does not belong to the requested trace",
        )
        mock_service.get_trace_detail.assert_called_once()
        mock_service.get_observation_detail.assert_not_called()
        mock_get.assert_not_called()

    @patch("apps.core.views.langfuse.timezone.now")
    @patch("apps.core.views.trace.get_default_trace_query_service")
    def test_observation_link_detail_returns_signed_observation(
        self,
        mock_get_service,
        mock_now,
    ):
        mock_now.return_value.timestamp.return_value = 1_700_000_100
        mock_service = Mock()
        mock_service.get_trace_detail.return_value = {
            "id": "trace-123",
            "observations": [{"id": "obs-1"}, {"id": "obs-2"}],
        }
        mock_service.get_observation_detail.return_value = {
            "id": "obs-1",
            "traceId": "trace-123",
            "name": "target-observation",
        }
        mock_get_service.return_value = mock_service

        payload, sig = self._build_signed_query(observation_id="obs-1")
        request = APIRequestFactory().get(
            "/api/v1/observation-link/detail",
            {"payload": payload, "sig": sig},
        )

        response = ObservationLinkDetailView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["code"], 0)
        self.assertEqual(response.data["data"]["trace"]["id"], "trace-123")
        self.assertEqual(response.data["data"]["observation"]["id"], "obs-1")
        self.assertEqual(response.data["data"]["selected_observation_id"], "obs-1")
        mock_service.get_trace_detail.assert_called_once()
        mock_service.get_observation_detail.assert_called_once()

    @patch("apps.core.views.langfuse.timezone.now")
    def test_observation_link_detail_requires_signed_observation_id(self, mock_now):
        mock_now.return_value.timestamp.return_value = 1_700_000_100
        payload, sig = self._build_signed_query()
        request = APIRequestFactory().get(
            "/api/v1/observation-link/detail",
            {"payload": payload, "sig": sig},
        )

        response = ObservationLinkDetailView.as_view()(request)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], 3023)
        self.assertEqual(
            response.data["message"],
            "Missing required payload field: observation_id",
        )
