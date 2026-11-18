"""HTTP contract regression against an isolated Langfuse test server (not a live service)."""

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.core.models import (
    DataSource,
    AgentTypeConfig,
    ObservationEndpointConfig,
    Space,
    SpaceObservationProject,
)
from apps.core.services.credential_provider import LangfuseCredentialProvider
from apps.core.services.credential_service import (
    create_credential_records,
    decrypt_secret,
    regenerate_credential,
    verify_secret,
)
from apps.core.tests.helpers import (
    create_local_user,
    create_test_agent,
    create_test_space_member,
)
from apps.core.trace_query.dataset_spec import (
    build_default_dataset,
    UnsupportedDataSourceError,
)


@override_settings(
    CREDENTIAL_ENCRYPTION_KEY="http-contract-test",
    SHARE_LINK_SIGNING_KEY="http-share-contract",
    SITE_URL="http://testserver",
)
class LangfuseHTTPContractTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.calls = []
        cls.key_counter = 0
        trace = {
            "id": "trace-1",
            "sessionId": "session-1",
            "name": "sample",
            "timestamp": "2026-09-16T00:00:00Z",
            "input": "hello",
            "output": "world",
            "observations": [],
        }

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def respond(self, payload, status=200):
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(payload).encode())

            def do_GET(self):
                cls.calls.append(("GET", self.path, self.headers.get("Authorization")))
                path = urlparse(self.path).path
                if path.endswith("/missing"):
                    self.respond({"message": "not found"}, 404)
                elif path == "/api/public/traces/trace-1":
                    self.respond(trace)
                elif path == "/api/public/traces":
                    self.respond(
                        {
                            "data": [trace],
                            "meta": {
                                "page": 1,
                                "limit": 20,
                                "totalItems": 1,
                                "totalPages": 1,
                            },
                        }
                    )
                else:
                    self.respond({"data": [], "meta": {"totalPages": 1}})

            def do_POST(self):
                cls.calls.append(("POST", self.path, self.headers.get("Authorization")))
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                if self.path == "/api/public/projects":
                    self.respond({"id": "project-1", "name": "test-project"})
                elif self.path.endswith("/apiKeys"):
                    cls.key_counter += 1
                    self.respond(
                        {
                            "publicKey": f"pk-{cls.key_counter}",
                            "secretKey": f"sk-{cls.key_counter}",
                        }
                    )
                else:
                    self.respond({"error": "unexpected route"}, 404)

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.host = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        super().tearDownClass()

    def setUp(self):
        self.calls.clear()
        self.user = create_local_user("lf-contract", name="LF", status=1)
        self.space = Space.objects.create(
            name="Langfuse HTTP", type=2, creator=self.user
        )
        create_test_space_member(space=self.space, user=self.user, role=1)
        self.agent_type = AgentTypeConfig.objects.create(
            agent_name="HTTP_CODEX",
            agent_product="Codex",
            runtime_type="codex",
            status=1,
        )
        create_test_agent(
            space=self.space,
            agent_type=self.agent_type,
            agent_product="Codex",
            name="Codex",
            status=1,
        )
        self.datasource = DataSource.objects.create(
            agent_type=self.agent_type,
            datasource_type="traces",
            source_system="langfuse",
            enabled=1,
            is_default=1,
            config={
                "langfuse_host": self.host,
                "langfuse_public_key": "pk-test",
                "langfuse_secret_key": "sk-test",
                "response_format": "langfuse",
            },
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_generic_queries_and_missing_trace_use_langfuse_basic_auth(self):
        for suffix in ("traces", "traces/trace-1", "sessions", "sessions/session-1"):
            response = self.client.get(
                f"/api/v1/spaces/{self.space.uuid}/{suffix}", {"agent_product": "Codex"}
            )
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data["code"], 0, response.data)
        missing = self.client.get(
            f"/api/v1/spaces/{self.space.uuid}/traces/missing",
            {"agent_product": "Codex"},
        )
        self.assertEqual(missing.data["code"], 3019)
        expected = "Basic " + base64.b64encode(b"pk-test:sk-test").decode()
        self.assertTrue(self.calls)
        self.assertTrue(all(auth == expected for _, _, auth in self.calls))
        self.assertTrue(
            all(path.startswith("/api/public/") for _, path, _ in self.calls)
        )

    def test_langfuse_share_roundtrip_and_signature_rejection(self):
        created = self.client.post(
            f"/api/v1/spaces/{self.space.uuid}/share-links/generate",
            {
                "resource_type": "trace",
                "resource_id": "trace-1",
                "agent_product": "Codex",
                "protocol": "lf",
            },
            format="json",
        )
        self.assertEqual(created.status_code, 200, created.data)
        query = {
            k: v[0]
            for k, v in parse_qs(urlparse(created.data["data"]["url"]).query).items()
        }
        self.client.force_authenticate(None)
        response = self.client.get("/api/v1/share/data", query)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["code"], 0, response.data)
        count = len(self.calls)
        query["sig"] = "invalid"
        self.assertEqual(self.client.get("/api/v1/share/data", query).status_code, 401)
        self.assertEqual(len(self.calls), count)

    def test_source_config_and_diagnosis_restriction_do_not_call_signoz(self):
        response = self.client.get(
            f"/api/v2/spaces/{self.space.uuid}/agent-products/Codex/datasource-config"
        )
        self.assertEqual(response.data["data"]["traceSources"], ["langfuse"])
        self.assertEqual(response.data["data"]["eventSources"], [])
        with self.assertRaisesRegex(UnsupportedDataSourceError, "SigNoz"):
            build_default_dataset(self.space, self.user, "Codex")
        self.assertEqual(self.calls, [])

    def test_cross_space_agent_fails_before_upstream_request(self):
        other = Space.objects.create(name="Other", type=2, creator=self.user)
        create_test_space_member(space=other, user=self.user, role=1)
        response = self.client.get(
            f"/api/v1/spaces/{other.uuid}/traces", {"agent_product": "Codex"}
        )
        self.assertNotEqual(response.data["code"], 0)
        self.assertEqual(self.calls, [])

    def test_project_credentials_and_rotation_use_http_basic_auth(self):
        provider = LangfuseCredentialProvider(self.host, "admin-pk", "admin-sk")
        project_info, read_pair, write_pair = provider.create_project_and_credentials(
            "HTTP project"
        )
        endpoint = ObservationEndpointConfig.objects.create(
            name="http", base_url=self.host, backend_type="langfuse"
        )
        project = SpaceObservationProject.objects.create(
            space=self.space, endpoint_config=endpoint, **project_info
        )
        read, write = create_credential_records(project, read_pair, write_pair)
        self.assertTrue(verify_secret(read_pair.secret_key, read.secret_hash))
        self.assertEqual(decrypt_secret(write.secret_ciphertext), write_pair.secret_key)
        rotated = regenerate_credential(read, provider)
        self.assertNotEqual(rotated["public_key"], read_pair.public_key)
        read.refresh_from_db()
        self.assertEqual(decrypt_secret(read.secret_ciphertext), rotated["secret_key"])
        expected = "Basic " + base64.b64encode(b"admin-pk:admin-sk").decode()
        self.assertEqual(len(self.calls), 4)
        self.assertTrue(all(auth == expected for _, _, auth in self.calls))
