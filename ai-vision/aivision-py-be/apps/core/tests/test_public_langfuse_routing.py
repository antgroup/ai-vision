"""URL-level regressions for external Langfuse and native SDK reads."""

import base64
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings
from rest_framework.test import APIClient

from apps.core.services.credential_service import encrypt_secret, hash_secret
from apps.core.tests.test_native_langfuse_api import details


@override_settings(
    CREDENTIAL_ENCRYPTION_KEY="public-routing-test-key",
    AIVISION_NATIVE_TRACE_HISTORY_START="",
)
class PublicLangfuseRoutingTests(SimpleTestCase):
    def setUp(self):
        self.space = SimpleNamespace(uuid="space-external", type=2, name="External")
        self.endpoint = SimpleNamespace(
            base_url="https://external.langfuse.test", backend_type="langfuse",
            status=1, gmt_deleted=None,
        )
        self.project = SimpleNamespace(
            space=self.space, endpoint_config=self.endpoint,
            upstream_project_id="external-project", status=1,
            provision_status=1, gmt_deleted=None, credentials=Mock(),
        )
        self.credential = SimpleNamespace(
            id=1, public_key="pk-external", scope="READ",
            secret_hash=hash_secret("external-secret"),
            secret_ciphertext=encrypt_secret("external-secret"),
            space_observation_project=self.project,
        )
        self.project.credentials.filter.return_value.first.return_value = self.credential
        self.space_key = SimpleNamespace(id=2, space=self.space)
        self.client = APIClient()
        self.client.raise_request_exception = False
        self.start_patch("apps.core.services.key_cache.key_cache.get_credential", return_value=self.credential)
        self.start_patch("apps.core.services.key_cache.key_cache.get_space_key", return_value=self.space_key)
        self.start_patch("apps.core.services.key_cache.key_cache.mark_credential_used")
        self.start_patch("apps.core.services.key_cache.key_cache.mark_space_key_used")
        projects = self.start_patch("apps.core.models.SpaceObservationProject.objects.select_related")
        projects.return_value.filter.return_value.first.return_value = self.project
        self.agents = self.start_patch("apps.core.views.langfuse_public.active_ingest_agents")
        self.agents.return_value.__getitem__.return_value = []

    def start_patch(self, target, **kwargs):
        active = patch(target, **kwargs)
        result = active.start()
        self.addCleanup(active.stop)
        return result

    @staticmethod
    def basic(public_key, secret):
        return "Basic " + base64.b64encode(f"{public_key}:{secret}".encode()).decode()

    def test_external_reads_keep_upstream_data_and_multivalue_params_at_registered_urls(self):
        paths = ("projects", "traces", "traces/external-trace", "observations",
                 "observations/external-observation", "sessions", "sessions/external-session")
        for auth in (self.basic("pk-external", "external-secret"), "Bearer sk-aiv-test"):
            self.client.credentials(HTTP_AUTHORIZATION=auth)
            for path in paths:
                with self.subTest(auth=auth.split()[0], path=path):
                    expected = {"externalPath": path, "id": "retained-upstream-data"}
                    response = Mock(status_code=200)
                    response.json.return_value = expected
                    def upstream(url, *, params, headers, timeout):
                        self.assertEqual(url, "https://external.langfuse.test/api/public/" + path)
                        self.assertEqual(params, {"tags": ["one", "two"], "fields": "core,io"})
                        self.assertEqual(headers["Authorization"], self.basic("pk-external", "external-secret"))
                        self.assertEqual(timeout, 30)
                        return response
                    with patch("apps.core.views.observation.http_requests.get", side_effect=upstream):
                        result = self.client.get("/api/public/" + path, {"tags": ["one", "two"], "fields": "core,io"})
                    self.assertEqual(result.status_code, 200, result.content)
                    self.assertEqual(result.json(), expected)

    @override_settings(AIVISION_NATIVE_TRACE_HISTORY_START="2026-10-01T00:00:00Z")
    def test_native_sdk_credentials_keep_agent_scope_when_external_project_also_exists(self):
        agent = SimpleNamespace(
            uuid="native-agent", agent_product="langfuse", name="Native",
            observation_subject_binding=SimpleNamespace(user=SimpleNamespace(uuid="subject")),
        )
        self.agents.return_value.filter.return_value.first.return_value = agent
        self.client.credentials(HTTP_AUTHORIZATION=self.basic("native-agent", "sk-aiv-test"))
        def storage(query):
            self.assertEqual(query.space.uuid, "space-external")
            self.assertEqual(query.agent_uuid, "native-agent")
            return {"data": details()[:1], "meta": {"complete": True}}
        with patch("apps.core.trace_query.langfuse.native.time.time_ns", return_value=1791007200000000000), patch(
            "apps.core.trace_query.signoz.provider.SignozTraceProvider.collect_traces", side_effect=storage
        ), patch(
            "apps.core.trace_query.signoz.snapshot_window.query_snapshot_window", return_value=None
        ):
            result = self.client.get("/api/public/traces")
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual([row["id"] for row in result.json()["data"]], ["t0"])

    def test_external_error_is_not_replaced_by_a_native_read(self):
        self.client.credentials(HTTP_AUTHORIZATION=self.basic("pk-external", "external-secret"))
        for status in (404, 503):
            with self.subTest(status=status):
                response = Mock(status_code=status)
                response.json.return_value = {"error": "external-project-error"}
                with patch("apps.core.views.observation.http_requests.get", return_value=response):
                    result = self.client.get("/api/public/traces/external-trace")
                self.assertEqual(result.status_code, status)
                self.assertEqual(result.json(), {"error": "external-project-error"})

    def test_external_write_credential_still_cannot_read(self):
        self.credential.scope = "WRITE"
        self.client.credentials(HTTP_AUTHORIZATION=self.basic("pk-external", "external-secret"))
        result = self.client.get("/api/public/traces")
        self.assertEqual(result.status_code, 403)
