from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import override_settings
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied
from rest_framework.test import APIClient, APIRequestFactory

from apps.core.models import (
    ObservationEndpointConfig,
    Space,
    SpaceKey,
    SpaceDatasourceBinding,
    SpaceObservationCredential,
    SpaceObservationProject,
    User,
)
from apps.core.services.credential_service import encrypt_secret, hash_secret
from apps.core.tests.helpers import create_local_user, SpaceKeyTestCase
from apps.core.views import (
    PublicObservationDetailView,
    PublicObservationListView,
    PublicSessionDetailView,
    PublicSessionListView,
    PublicTraceDetailView,
    PublicTraceListView,
)
from apps.core.views.observation import PublicAPIView


@override_settings(
    CREDENTIAL_ENCRYPTION_KEY="test-credential-key",
)
class PublicObservationApiTests(SpaceKeyTestCase):
    def setUp(self):
        super().setUp()
        self.endpoint = ObservationEndpointConfig.objects.create(
            name="default-langfuse",
            base_url="https://langfuse.example.com",
            tenant_name="tenant-a",
            backend_type="langfuse",
            is_default=1,
            status=1,
        )
        self.user = create_local_user("100002", name="Gateway Tester", status=1)
        self.space = Space.objects.create(
            name="Trace Space",
            type=2,
            scope=1,
            creator=self.user,
            status=1,
        )
        self.project = SpaceObservationProject.objects.create(
            space=self.space,
            endpoint_config=self.endpoint,
            tenant_name="tenant-a",
            upstream_project_id="proj_xxx",
            upstream_project_name="Trace Space",
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
        self.public_key = "pk-read-test"
        self.secret_key = "sk-read-test"
        SpaceObservationCredential.objects.create(
            space_observation_project=self.project,
            scope="READ",
            public_key=self.public_key,
            secret_hash=hash_secret(self.secret_key),
            secret_ciphertext=encrypt_secret(self.secret_key),
            name="default-read",
            status=1,
        )

    @patch("apps.core.views.observation.http_requests.get")
    def test_registered_public_urls_keep_real_external_project_auth_and_routing(self, get):
        raw, prefix, digest = SpaceKey.generate_key()
        SpaceKey.objects.create(
            space=self.space, key_hash=digest, key_prefix=prefix, name="public-reader"
        )
        client = APIClient()
        auth_headers = (
            "Basic cGstcmVhZC10ZXN0OnNrLXJlYWQtdGVzdA==",
            f"Bearer {raw}",
        )
        for auth in auth_headers:
            client.credentials(HTTP_AUTHORIZATION=auth)
            for path in ("projects", "traces", "traces/external-trace", "sessions",
                         "sessions/external-session", "observations", "observations/external-observation"):
                with self.subTest(auth=auth.split()[0], path=path):
                    upstream = Mock(status_code=200)
                    upstream.json.return_value = {"retainedExternalData": path}
                    def read(url, *, params, headers, timeout):
                        self.assertEqual(url, "https://langfuse.example.com/api/public/" + path)
                        self.assertEqual(params, {"tags": ["a", "b"]})
                        self.assertEqual(headers["Authorization"], "Basic cGstcmVhZC10ZXN0OnNrLXJlYWQtdGVzdA==")
                        return upstream
                    get.side_effect = read
                    response = client.get("/api/public/" + path, {"tags": ["a", "b"]})
                    self.assertEqual(response.status_code, 200, response.content)
                    self.assertEqual(response.json(), {"retainedExternalData": path})

    def test_resolve_observation_context_rejects_missing_credential_object(self):
        request = SimpleNamespace(auth_type="credential", auth=None)

        with self.assertRaises(AuthenticationFailed):
            PublicAPIView()._resolve_observation_context(request)

    def test_resolve_observation_context_rejects_write_credential_scope(self):
        request = SimpleNamespace(
            auth_type="credential",
            auth=SimpleNamespace(scope="WRITE"),
        )

        with self.assertRaises(PermissionDenied):
            PublicAPIView()._resolve_observation_context(request)

    def test_resolve_observation_context_rejects_multiple_active_projects(self):
        request = SimpleNamespace(
            auth_type="space_key",
            auth=SimpleNamespace(space=self.space),
        )

        with (
            patch.object(
                SpaceObservationProject.objects, "select_related"
            ) as mock_select_related,
            self.assertRaises(AuthenticationFailed),
        ):
            mock_select_related.return_value.get.side_effect = (
                SpaceObservationProject.MultipleObjectsReturned
            )
            PublicAPIView()._resolve_observation_context(request)

    @patch("apps.core.views.observation.http_requests.get")
    def test_public_trace_list_passthroughs_all_query_params(self, mock_get):
        mock_response = Mock()
        mock_response.json.return_value = {"data": [], "meta": {"page": 1}}
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        request = APIRequestFactory().get(
            "/api/public/traces",
            {
                "sessionId": "session-123",
                "userId": "user-123",
                "tags": ["tag-a", "tag-b"],
                "fields": "core,metrics",
            },
            HTTP_AUTHORIZATION="Basic cGstcmVhZC10ZXN0OnNrLXJlYWQtdGVzdA==",
        )

        response = PublicTraceListView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        _, kwargs = mock_get.call_args
        self.assertEqual(
            kwargs["params"],
            {
                "sessionId": "session-123",
                "userId": "user-123",
                "tags": ["tag-a", "tag-b"],
                "fields": "core,metrics",
            },
        )

    @patch("apps.core.views.observation.http_requests.get")
    def test_public_trace_list_passes_trace_id_as_is(self, mock_get):
        mock_response = Mock()
        mock_response.json.return_value = {"data": [], "meta": {"page": 1}}
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        request = APIRequestFactory().get(
            "/api/public/traces",
            {"traceId": "0ad1348f140316927500210031234"},
            HTTP_AUTHORIZATION="Basic cGstcmVhZC10ZXN0OnNrLXJlYWQtdGVzdA==",
        )

        response = PublicTraceListView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        _, kwargs = mock_get.call_args
        self.assertEqual(
            kwargs["params"],
            {"traceId": "0ad1348f140316927500210031234"},
        )

    @patch("apps.core.views.observation.http_requests.get")
    def test_public_api_requires_basic_credentials(self, mock_get):
        request = APIRequestFactory().get("/api/public/traces")

        response = PublicTraceListView.as_view()(request)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.data,
            {"detail": "Authentication credentials were not provided."},
        )
        mock_get.assert_not_called()

    @patch("apps.core.views.observation.http_requests.get")
    def test_public_trace_detail_passthroughs_query_params(self, mock_get):
        mock_response = Mock()
        mock_response.json.return_value = {"id": "trace-123"}
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        request = APIRequestFactory().get(
            "/api/public/traces/trace-123?fields=core,metrics,io&includeTags=true",
            HTTP_AUTHORIZATION="Basic cGstcmVhZC10ZXN0OnNrLXJlYWQtdGVzdA==",
        )

        response = PublicTraceDetailView.as_view()(request, trace_id="trace-123")

        self.assertEqual(response.status_code, 200)
        _, kwargs = mock_get.call_args
        self.assertEqual(
            kwargs["params"],
            {
                "fields": "core,metrics,io",
                "includeTags": "true",
            },
        )

    @patch("apps.core.views.observation.http_requests.get")
    def test_public_trace_detail_passes_all_query_params(self, mock_get):
        mock_response = Mock()
        mock_response.json.return_value = {"id": "trace-123"}
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        request = APIRequestFactory().get(
            "/api/public/traces/trace-123?fields=query"
            "&fromTimestamp=2026-06-25T00:00:00%2B08:00"
            "&toTimestamp=2026-06-25T06:45:00%2B08:00",
            HTTP_AUTHORIZATION="Basic cGstcmVhZC10ZXN0OnNrLXJlYWQtdGVzdA==",
        )

        response = PublicTraceDetailView.as_view()(request, trace_id="trace-123")

        self.assertEqual(response.status_code, 200)
        _, kwargs = mock_get.call_args
        self.assertEqual(
            kwargs["params"],
            {
                "fields": "query",
                "fromTimestamp": "2026-06-25T00:00:00+08:00",
                "toTimestamp": "2026-06-25T06:45:00+08:00",
            },
        )

    @patch("apps.core.views.observation.http_requests.get")
    def test_public_trace_detail_passes_trace_id_as_is(self, mock_get):
        mock_response = Mock()
        mock_response.json.return_value = {"id": "trace-plain-123"}
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        request = APIRequestFactory().get(
            "/api/public/traces/trace-plain-123",
            HTTP_AUTHORIZATION="Basic cGstcmVhZC10ZXN0OnNrLXJlYWQtdGVzdA==",
        )

        response = PublicTraceDetailView.as_view()(
            request,
            trace_id="trace-plain-123",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            mock_get.call_args.args[0],
            "https://langfuse.example.com/api/public/traces/trace-plain-123",
        )

    @patch("apps.core.views.observation.http_requests.get")
    def test_public_trace_detail_post_not_allowed(self, mock_get):
        request = APIRequestFactory().post(
            "/api/public/traces/trace-plain-123",
            {},
            format="json",
            HTTP_AUTHORIZATION="Basic cGstcmVhZC10ZXN0OnNrLXJlYWQtdGVzdA==",
        )

        response = PublicTraceDetailView.as_view()(
            request,
            trace_id="trace-plain-123",
        )

        self.assertEqual(response.status_code, 405)
        mock_get.assert_not_called()

    @patch("apps.core.views.observation.http_requests.get")
    def test_public_session_list_passthroughs_query_params(self, mock_get):
        mock_response = Mock()
        mock_response.json.return_value = {"data": [], "meta": {"page": 1}}
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        request = APIRequestFactory().get(
            "/api/public/sessions",
            {
                "userId": "user-123",
                "fromTimestamp": "2026-05-01T00:00:00.000Z",
                "toTimestamp": "2026-05-07T00:00:00.000Z",
                "fields": "core,metrics",
            },
            HTTP_AUTHORIZATION="Basic cGstcmVhZC10ZXN0OnNrLXJlYWQtdGVzdA==",
        )

        response = PublicSessionListView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            mock_get.call_args.args[0],
            "https://langfuse.example.com/api/public/sessions",
        )
        _, kwargs = mock_get.call_args
        self.assertEqual(
            kwargs["params"],
            {
                "userId": "user-123",
                "fromTimestamp": "2026-05-01T00:00:00.000Z",
                "toTimestamp": "2026-05-07T00:00:00.000Z",
                "fields": "core,metrics",
            },
        )

    @patch("apps.core.views.observation.http_requests.get")
    def test_public_session_detail_passthroughs_query_params(self, mock_get):
        mock_response = Mock()
        mock_response.json.return_value = {"id": "session-123"}
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        request = APIRequestFactory().get(
            "/api/public/sessions/session-123",
            {"fields": "core,traces", "includeTraces": "true"},
            HTTP_AUTHORIZATION="Basic cGstcmVhZC10ZXN0OnNrLXJlYWQtdGVzdA==",
        )

        response = PublicSessionDetailView.as_view()(
            request,
            session_id="session-123",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            mock_get.call_args.args[0],
            "https://langfuse.example.com/api/public/sessions/session-123",
        )
        _, kwargs = mock_get.call_args
        self.assertEqual(
            kwargs["params"],
            {"fields": "core,traces", "includeTraces": "true"},
        )

    @patch("apps.core.views.observation.http_requests.get")
    def test_public_observation_list_passthroughs_query_params(self, mock_get):
        mock_response = Mock()
        mock_response.json.return_value = {"data": [], "meta": {"page": 1}}
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        request = APIRequestFactory().get(
            "/api/public/observations",
            {
                "traceId": "trace-123",
                "type": "GENERATION",
                "page": "2",
                "limit": "50",
                "fields": "core,io,metrics",
            },
            HTTP_AUTHORIZATION="Basic cGstcmVhZC10ZXN0OnNrLXJlYWQtdGVzdA==",
        )

        response = PublicObservationListView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            mock_get.call_args.args[0],
            "https://langfuse.example.com/api/public/observations",
        )
        _, kwargs = mock_get.call_args
        self.assertEqual(
            kwargs["params"],
            {
                "traceId": "trace-123",
                "type": "GENERATION",
                "page": "2",
                "limit": "50",
                "fields": "core,io,metrics",
            },
        )

    @patch("apps.core.views.observation.http_requests.get")
    def test_public_observation_detail_passthroughs_query_params(self, mock_get):
        mock_response = Mock()
        mock_response.json.return_value = {"id": "obs-123"}
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        request = APIRequestFactory().get(
            "/api/public/observations/obs-123",
            {
                "fields": "core,io",
                "expand": "trace",
            },
            HTTP_AUTHORIZATION="Basic cGstcmVhZC10ZXN0OnNrLXJlYWQtdGVzdA==",
        )

        response = PublicObservationDetailView.as_view()(
            request,
            observation_id="obs-123",
        )

        self.assertEqual(response.status_code, 200)
        _, kwargs = mock_get.call_args
        self.assertEqual(
            kwargs["params"],
            {
                "fields": "core,io",
                "expand": "trace",
            },
        )
