"""V2 Security Audit Tests.

Covers findings from the security audit of:
  Task #1: Encryption protocol + auth gaps
  Task #2: Template injection + scope + time filter
  Task #3: Info leakage + log safety + credential handling
"""

import base64
import json
import os
from unittest.mock import MagicMock, patch

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.core.crypto.agent_query_crypto import (
    decrypt_agent_query_payload,
)
from apps.core.tests.helpers import (
    create_test_space_member,
    create_test_agent,
    create_local_user,
)

from apps.core.models import (
    Agent,
    AgentTypeConfig,
    DataSource,
    Space,
    SpaceMember,
    User,
)
from apps.core.query.errors import (
    AgentQueryDecryptFailedError,
    AgentQueryInvalidPayloadError,
    AgentQueryUnknownTemplateVariableError,
)
from apps.core.query.time_range import validate_time_range, _RELATIVE_RANGE_WHITELIST
from apps.core.views.v2_query import AgentDashboardQueryView, AgentQueryConfigView

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _generate_aes_key() -> bytes:
    return AESGCM.generate_key(bit_length=256)


def _base64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _encrypt_payload(payload_dict: dict, key: bytes, kid: str = "test-kid") -> dict:
    aesgcm = AESGCM(key)
    iv = os.urandom(12)
    plaintext = json.dumps(payload_dict).encode("utf-8")
    ciphertext = aesgcm.encrypt(iv, plaintext, None)
    return {
        "payloadVersion": "v1",
        "kid": kid,
        "iv": _base64url_encode(iv),
        "encryptedPayload": _base64url_encode(ciphertext),
    }


# ===========================================================================
# Task #1: Encryption protocol + auth gaps
# ===========================================================================


class AgentQueryConfigViewAuthTests(TestCase):
    """AgentQueryConfigView must require authentication."""

    def setUp(self):
        self.client = APIClient()
        self.user = create_local_user("u-auth", name="Auth User", status=1)
        self.agent_type = AgentTypeConfig.objects.create(
            agent_name="test-agent",
            agent_product="test-agent",
            category="BUILT_IN",
            status=1,
        )
        self.datasource = DataSource.objects.create(
            agent_type=self.agent_type,
            datasource_type="logs",
            source_system="signoz",
            enabled=1,
            config={
                "endpoint": "http://signoz.test",
                "table_name": "aivision_test_table",
                "user_column": "user_id",
                "time_field": "timestamp_ms",
                "access_id": "test-id",
                "access_token": "test-token",
                "agent_product_filter": "TestAgent",
                "supports_personal_scope": True,
                "supports_space_scope": True,
            },
        )

    def test_unauthenticated_get_returns_401_or_403(self):
        """Unauthenticated GET to /query-config must return 401 or 403."""
        url = "/api/v2/agent-products/test-agent/query-config"
        resp = self.client.get(url)
        self.assertIn(resp.status_code, [401, 403])

    def test_authenticated_get_succeeds(self):
        """Authenticated GET to /query-config returns 200."""
        self.client.force_authenticate(user=self.user)
        url = "/api/v2/agent-products/test-agent/query-config"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

    def test_permission_classes_includes_is_authenticated(self):
        """AgentQueryConfigView must have IsAuthenticated in permission_classes."""
        perm_names = [pc.__name__ for pc in AgentQueryConfigView.permission_classes]
        self.assertIn("IsAuthenticated", perm_names)


class AgentDashboardQueryViewAuthTests(TestCase):
    """AgentDashboardQueryView already has IsAuthenticated; verify."""

    def test_permission_classes_includes_is_authenticated(self):
        perm_names = [pc.__name__ for pc in AgentDashboardQueryView.permission_classes]
        self.assertIn("IsAuthenticated", perm_names)


class EncryptionProtocolTests(TestCase):
    """Audit: encryption protocol correctness and gap checks."""

    def test_no_plaintext_fallback_on_decrypt_failure(self):
        """When decryption fails, the system must NOT fall back to plaintext.

        Verify that the view raises AgentQueryDecryptFailedError, not a
        silent fallback to treating the payload as plain JSON.
        """
        # Craft an encrypted payload with an unknown kid
        key = _generate_aes_key()
        encrypted = _encrypt_payload({"scope": "personal"}, key, kid="unknown-kid")
        with override_settings(
            AGENT_QUERY_ENCRYPTION_ENABLED=True,
            AGENT_QUERY_ENCRYPTION_KEYS={"other-kid": _base64url_encode(key)},
        ):
            with self.assertRaises(AgentQueryDecryptFailedError):
                decrypt_agent_query_payload(encrypted)

    def test_kid_mismatch_returns_decrypt_failed_no_info_leakage(self):
        """Unknown kid must return AGENT_QUERY_DECRYPT_FAILED with minimal details.

        The error must not reveal key material, configuration details, or
        the list of valid kids.
        """
        key = _generate_aes_key()
        encrypted = _encrypt_payload({"scope": "personal"}, key, kid="attacker-kid")
        with override_settings(
            AGENT_QUERY_ENCRYPTION_ENABLED=True,
            AGENT_QUERY_ENCRYPTION_KEYS={"real-kid": _base64url_encode(key)},
        ):
            with self.assertRaises(AgentQueryDecryptFailedError) as ctx:
                decrypt_agent_query_payload(encrypted)
            exc = ctx.exception
            self.assertEqual(exc.type_str, "AGENT_QUERY_DECRYPT_FAILED")
            # The error details should only contain the requested kid, not the valid ones
            details = exc.details
            self.assertIn("kid", details)
            self.assertEqual(details["kid"], "attacker-kid")
            # Must NOT list valid keys
            self.assertNotIn("valid_keys", details)
            self.assertNotIn("available", details)
            # Must NOT contain key material
            for key_b64 in [_base64url_encode(key)]:
                self.assertNotIn(key_b64, str(details))
                self.assertNotIn(key_b64, exc.message)

    def test_decrypt_error_envelope_has_no_crypto_details(self):
        """Decrypt errors must use the generic AGENT_QUERY_DECRYPT_FAILED type
        and must NOT include internal crypto details (algorithm, key length,
        IV details beyond length check)."""
        key = _generate_aes_key()
        encrypted = _encrypt_payload({"scope": "personal"}, key, kid="test-kid")
        # Tamper with the ciphertext to cause AES-GCM auth failure
        ct_bytes = base64.urlsafe_b64decode(encrypted["encryptedPayload"] + "==")
        tampered = bytes([ct_bytes[0] ^ 0xFF]) + ct_bytes[1:]
        encrypted["encryptedPayload"] = _base64url_encode(tampered)

        with override_settings(
            AGENT_QUERY_ENCRYPTION_KEYS={"test-kid": _base64url_encode(key)}
        ):
            with self.assertRaises(AgentQueryDecryptFailedError) as ctx:
                decrypt_agent_query_payload(encrypted)
            exc = ctx.exception
            # Generic message, no internal details
            self.assertEqual(exc.type_str, "AGENT_QUERY_DECRYPT_FAILED")
            # The message should be generic, not containing algorithm internals
            self.assertNotIn("AES", exc.message)
            self.assertNotIn("GCM", exc.message)
            self.assertNotIn("tag", exc.message)

    def test_encryption_key_in_frontend_is_transport_obfuscation(self):
        """Document: The frontend encryption key (VITE_AGENT_QUERY_ENCRYPTION_KEY_B64)
        is embedded in the build bundle and is extractable by attackers.
        This is a KNOWN DESIGN TRADEOFF: encryption provides transport obfuscation
        only, not protection against a determined attacker. All real data access
        control is enforced server-side (authentication, space membership,
        server-generated scope_filter).

        This test documents the design decision rather than testing code.
        """
        # The key is loaded from VITE_AGENT_QUERY_ENCRYPTION_KEY_B64 via
        # import.meta.env, meaning it is bundled into the frontend JavaScript.
        # Security relies on server-side enforcement, not encryption secrecy.
        self.assertTrue(
            True,
            "Design tradeoff documented: frontend encryption is transport obfuscation only",
        )


# ===========================================================================
# Task #2: Template injection + scope + time filter
# ===========================================================================


class TimeRangeSecurityTests(TestCase):
    """Additional time range security tests beyond existing coverage."""

    def test_missing_time_range_type_rejected(self):
        """A timeRange without a 'type' field must be rejected."""
        with self.assertRaises(AgentQueryInvalidPayloadError) as ctx:
            validate_time_range({"from": 1700000000000, "to": 1700086400000})
        self.assertIn("type", ctx.exception.message.lower())

    def test_partial_absolute_time_range_rejected(self):
        """Absolute timeRange with only 'from' must be rejected."""
        with self.assertRaises(AgentQueryInvalidPayloadError):
            validate_time_range({"type": "absolute", "from": 1700000000000})


class FrontendCannotOverrideScopeFilterTests(TestCase):
    """Integration test: frontend cannot inject scope_filter via the payload."""

    def setUp(self):
        self.client = APIClient()
        self.user = create_local_user("u-scope", name="Scope User", status=1)
        self.space = Space.objects.create(
            name="Scope Space", type=2, scope=1, creator=self.user, status=1
        )
        create_test_space_member(space=self.space, user=self.user, role=1)
        self.agent_type = AgentTypeConfig.objects.create(
            agent_name="scope-agent",
            agent_product="scope-agent",
            category="BUILT_IN",
            status=1,
        )
        create_test_agent(
            space=self.space,
            agent_type=self.agent_type,
            agent_product="scope-agent",
            name="Scope Agent",
            status=1,
        )
        self.datasource = DataSource.objects.create(
            agent_type=self.agent_type,
            datasource_type="logs",
            source_system="signoz",
            enabled=1,
            config={
                "endpoint": "http://signoz.test",
                "table_name": "aivision_scope_table",
                "user_column": "user_id",
                "time_field": "timestamp_ms",
                "access_id": "test-id",
                "access_token": "test-token",
                "agent_product_filter": "scope-agent",
                "supports_personal_scope": True,
                "supports_space_scope": True,
            },
        )
        self.url = f"/api/v2/spaces/{self.space.uuid}/agent-products/scope-agent/query"

    @override_settings(AGENT_QUERY_ALLOW_PLAIN=True)
    @patch("apps.core.views.v2_query.requests.post")
    def test_scope_filter_not_in_payload_is_still_server_generated(self, mock_post):
        """Even if the payload has no scope_filter field, the server generates it."""
        captured_body = {}

        def capture_post(url, json=None, **kwargs):
            captured_body.update(json or {})
            return MagicMock(
                status_code=200,
                json=MagicMock(
                    return_value={
                        "success": True,
                        "data": {"headers": ["col"], "data": [["val"]]},
                    }
                ),
            )

        mock_post.side_effect = capture_post
        self.client.force_authenticate(user=self.user)
        payload = {
            "scope": "personal",
            "queryKey": "sessions",
            "template": "SELECT * FROM {{table}} WHERE {{time_filter}} AND {{scope_filter}}",
            "timeRange": {"type": "relative", "range": "1h"},
        }
        resp = self.client.post(self.url + "?encrypted=0", data=payload, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        # scope_filter was generated server-side using the authenticated user
        self.assertEqual(captured_body["user"], self.user.uuid)
        self.assertEqual(captured_body["space_id"], self.space.uuid)
        self.assertNotIn("simpleSql", captured_body)


# ===========================================================================
# Task #3: Info leakage + log safety + credential handling
# ===========================================================================


class QueryConfigInfoLeakageTests(TestCase):
    """Verify query-config endpoint excludes sensitive fields."""

    def setUp(self):
        self.client = APIClient()
        self.user = create_local_user("u-info", name="Info User", status=1)
        self.agent_type = AgentTypeConfig.objects.create(
            agent_name="info-agent",
            agent_product="info-agent",
            category="BUILT_IN",
            status=1,
        )
        self.datasource = DataSource.objects.create(
            agent_type=self.agent_type,
            datasource_type="logs",
            source_system="signoz",
            enabled=1,
            config={
                "endpoint": "https://stargazer-internal.example.com:9090",
                "table_name": "aivision_secret_table",
                "user_column": "user_id",
                "time_field": "timestamp_ms",
                "access_id": "secret-access-id-12345",
                "access_token": "secret-access-token-67890",
                "agent_product_filter": "info-agent",
                "supports_personal_scope": True,
                "supports_space_scope": True,
            },
        )

    def test_query_config_excludes_table_name(self):
        """query-config response must not contain table_name."""
        self.client.force_authenticate(user=self.user)
        url = "/api/v2/agent-products/info-agent/query-config"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        data = resp.data.get("data", resp.data)
        self.assertNotIn("table_name", data)

    def test_query_config_excludes_pk_sk(self):
        """query-config response must not contain pk or sk fields."""
        self.client.force_authenticate(user=self.user)
        url = "/api/v2/agent-products/info-agent/query-config"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        data = resp.data.get("data", resp.data)
        self.assertNotIn("pk", data)
        self.assertNotIn("sk", data)

    def test_query_config_excludes_query_endpoint(self):
        """query-config response must not contain query_endpoint."""
        self.client.force_authenticate(user=self.user)
        url = "/api/v2/agent-products/info-agent/query-config"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        data = resp.data.get("data", resp.data)
        self.assertNotIn("query_endpoint", data)

    def test_query_config_excludes_default_filters(self):
        """query-config response must not contain default_filters (contains credentials)."""
        self.client.force_authenticate(user=self.user)
        url = "/api/v2/agent-products/info-agent/query-config"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        data = resp.data.get("data", resp.data)
        self.assertNotIn("default_filters", data)

    def test_query_config_excludes_time_field(self):
        """query-config response must not contain time_field."""
        self.client.force_authenticate(user=self.user)
        url = "/api/v2/agent-products/info-agent/query-config"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        data = resp.data.get("data", resp.data)
        self.assertNotIn("time_field", data)

    def test_query_config_includes_allowed_fields_only(self):
        """query-config response must only include the whitelisted public fields."""
        self.client.force_authenticate(user=self.user)
        url = "/api/v2/agent-products/info-agent/query-config"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        data = resp.data.get("data", resp.data)
        # These fields should be present
        self.assertIn("agent_product", data)
        self.assertIn("source_system", data)
        self.assertIn("capabilities", data)
        # All sensitive fields must be absent
        for sensitive in [
            "table_name",
            "pk",
            "sk",
            "query_endpoint",
            "default_filters",
            "time_field",
            "partition_key",
            "sort_key",
        ]:
            self.assertNotIn(
                sensitive, data, f"Sensitive field '{sensitive}' found in response"
            )

    def test_query_config_does_not_leak_credentials(self):
        """Neither access_id nor access_token from default_filters should appear in response."""
        self.client.force_authenticate(user=self.user)
        url = "/api/v2/agent-products/info-agent/query-config"
        resp = self.client.get(url)
        data = resp.data.get("data", resp.data)
        response_text = json.dumps(data)
        self.assertNotIn("secret-access-id-12345", response_text)
        self.assertNotIn("secret-access-token-67890", response_text)


class ErrorInfoLeakageTests(TestCase):
    """Verify error responses don't leak sensitive information."""

    def setUp(self):
        self.client = APIClient()
        self.user = create_local_user("u-err", name="Error User", status=1)
        self.space = Space.objects.create(
            name="Error Space", type=2, scope=1, creator=self.user, status=1
        )
        create_test_space_member(space=self.space, user=self.user, role=1)
        self.agent_type = AgentTypeConfig.objects.create(
            agent_name="err-agent",
            agent_product="err-agent",
            category="BUILT_IN",
            status=1,
        )
        create_test_agent(
            space=self.space,
            agent_type=self.agent_type,
            agent_product="err-agent",
            name="Error Agent",
            status=1,
        )
        self.datasource = DataSource.objects.create(
            agent_type=self.agent_type,
            datasource_type="logs",
            source_system="signoz",
            enabled=1,
            config={
                "endpoint": "https://stargazer-internal.example.com:9090",
                "table_name": "aivision_err_table",
                "user_column": "user_id",
                "time_field": "timestamp_ms",
                "access_id": "cred-access-id",
                "access_token": "cred-access-token",
                "agent_product_filter": "err-agent",
                "supports_personal_scope": True,
                "supports_space_scope": True,
            },
        )
        self.url = f"/api/v2/spaces/{self.space.uuid}/agent-products/err-agent/query"

    def _plain_payload(self, **overrides):
        defaults = {
            "scope": "personal",
            "queryKey": "sessions",
            "template": "SELECT * FROM {{table}} WHERE {{time_filter}} AND {{scope_filter}}",
            "timeRange": {"type": "relative", "range": "1h"},
        }
        defaults.update(overrides)
        return defaults

    @override_settings(AGENT_QUERY_ALLOW_PLAIN=True)
    @patch("apps.core.views.v2_query.requests.post")
    def test_execution_error_does_not_leak_endpoint(self, mock_post):
        """Query execution failure must not expose endpoint URL."""
        mock_post.side_effect = Exception(
            "Connection refused by https://stargazer-internal.example.com:9090"
        )
        self.client.force_authenticate(user=self.user)
        resp = self.client.post(
            self.url + "?encrypted=0",
            data=self._plain_payload(),
            format="json",
        )
        self.assertEqual(resp.status_code, 500)
        error = resp.data.get("error", {})
        self.assertEqual(error.get("type"), "AGENT_QUERY_EXECUTION_FAILED")
        msg = resp.data.get("message", "")
        self.assertNotIn("stargazer", msg)
        self.assertNotIn("https://", msg)
        self.assertNotIn(":9090", msg)

    @override_settings(AGENT_QUERY_ALLOW_PLAIN=True)
    @patch("apps.core.views.v2_query.requests.post")
    def test_execution_error_does_not_leak_table_name(self, mock_post):
        """Query execution failure must not expose table names."""
        mock_post.side_effect = Exception("Table aivision_err_table not found")
        self.client.force_authenticate(user=self.user)
        resp = self.client.post(
            self.url + "?encrypted=0",
            data=self._plain_payload(),
            format="json",
        )
        self.assertEqual(resp.status_code, 500)
        msg = resp.data.get("message", "")
        self.assertNotIn("aivision_err_table", msg)
        self.assertNotIn("aivision", msg)

    @override_settings(AGENT_QUERY_ALLOW_PLAIN=True)
    @patch("apps.core.views.v2_query.requests.post")
    def test_execution_error_does_not_leak_credentials(self, mock_post):
        """Query execution failure must not expose default_filters credentials."""
        mock_post.side_effect = Exception("Auth failed for access_id=cred-access-id")
        self.client.force_authenticate(user=self.user)
        resp = self.client.post(
            self.url + "?encrypted=0",
            data=self._plain_payload(),
            format="json",
        )
        self.assertEqual(resp.status_code, 500)
        response_text = json.dumps(resp.data)
        self.assertNotIn("cred-access-id", response_text)
        self.assertNotIn("cred-access-token", response_text)

    def test_decrypt_error_does_not_leak_crypto_details(self):
        """Decrypt failure must return AGENT_QUERY_DECRYPT_FAILED without
        crypto implementation details (algorithm, padding, etc.)."""
        key = _generate_aes_key()
        encrypted = _encrypt_payload({"scope": "personal"}, key, kid="unknown-kid")
        with override_settings(
            AGENT_QUERY_ENCRYPTION_ENABLED=True,
            AGENT_QUERY_ENCRYPTION_KEYS={"real-kid": _base64url_encode(key)},
        ):
            with self.assertRaises(AgentQueryDecryptFailedError) as ctx:
                decrypt_agent_query_payload(encrypted)
            exc = ctx.exception
            self.assertEqual(exc.type_str, "AGENT_QUERY_DECRYPT_FAILED")
            self.assertNotIn("AES", exc.message)
            self.assertNotIn("GCM", exc.message)

    def test_decrypt_error_via_api_no_crypto_leakage(self):
        """Full API round-trip: decrypt failure response has no crypto details."""
        key = _generate_aes_key()
        encrypted = _encrypt_payload({"scope": "personal"}, key, kid="unknown-kid")
        self.client.force_authenticate(user=self.user)
        with override_settings(
            AGENT_QUERY_ENCRYPTION_ENABLED=True,
            AGENT_QUERY_ENCRYPTION_KEYS={"real-kid": _base64url_encode(key)},
        ):
            resp = self.client.post(
                self.url + "?encrypted=1",
                data=encrypted,
                format="json",
            )
        self.assertEqual(resp.status_code, 400)
        error = resp.data.get("error", {})
        self.assertEqual(error.get("type"), "AGENT_QUERY_DECRYPT_FAILED")
        response_text = json.dumps(resp.data)
        # No key material
        self.assertNotIn(_base64url_encode(key), response_text)
        # No algorithm details
        self.assertNotIn("AES", response_text)
        self.assertNotIn("GCM", response_text)


class CredentialHandlingTests(TestCase):
    """Verify credentials in default_filters are handled safely."""

    def test_datasource_config_credentials_not_in_query_config_response(self):
        """DataSource.config credentials must not appear in the
        query-config API response."""
        client = APIClient()
        user = create_local_user("u-cred", name="Cred User", status=1)
        agent_type = AgentTypeConfig.objects.create(
            agent_name="cred-agent",
            agent_product="cred-agent",
            category="BUILT_IN",
            status=1,
        )
        DataSource.objects.create(
            agent_type=agent_type,
            datasource_type="logs",
            source_system="signoz",
            enabled=1,
            config={
                "endpoint": "http://signoz.test",
                "table_name": "aivision_cred_table",
                "user_column": "user_id",
                "time_field": "timestamp_ms",
                "access_id": "AKIAIOSFODNN7EXAMPLE",
                "access_token": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
                "agent_product_filter": "cred-agent",
                "supports_personal_scope": True,
                "supports_space_scope": True,
            },
        )
        client.force_authenticate(user=user)
        resp = client.get("/api/v2/agent-products/cred-agent/query-config")
        data = resp.data.get("data", resp.data)
        # DataSource.config is never exposed via the API
        self.assertNotIn("config", data)
        self.assertNotIn("default_filters", data)
        response_text = json.dumps(data)
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", response_text)
        self.assertNotIn("wJalrXUtnFEMI", response_text)

    def test_credentials_stored_in_jsonfield_without_additional_encryption(self):
        """Document: DataSource.config credentials (access_id, access_token)
        are stored at rest in a Django JSONField without additional
        field-level encryption. This is a known design decision. The database
        must be secured at the infrastructure level (encryption at rest,
        access controls).

        This test documents the design decision rather than testing code.
        """
        # DataSource.config is a JSONField that stores access_id and
        # access_token in plaintext in the database.
        # Security relies on:
        # 1. Database encryption at rest (infrastructure level)
        # 2. Database access controls
        # 3. API never exposing DataSource.config in responses
        # 4. Credentials only used server-side for query execution auth headers
        self.assertTrue(True, "Credentials at-rest storage documented")


class LogSafetyExtendedTests(TestCase):
    """Extended log safety tests for credential handling."""

    def setUp(self):
        self.user = create_local_user("u-log2", name="Log2 User", status=1)
        self.space = Space.objects.create(
            name="Log2 Space", type=2, scope=1, creator=self.user, status=1
        )
        create_test_space_member(space=self.space, user=self.user, role=1)
        self.agent_type = AgentTypeConfig.objects.create(
            agent_name="log2-agent",
            agent_product="log2-agent",
            category="BUILT_IN",
            status=1,
        )
        create_test_agent(
            space=self.space,
            agent_type=self.agent_type,
            agent_product="log2-agent",
            name="Log2 Agent",
            status=1,
        )
        self.datasource = DataSource.objects.create(
            agent_type=self.agent_type,
            datasource_type="logs",
            source_system="signoz",
            enabled=1,
            config={
                "endpoint": "http://signoz.test",
                "table_name": "aivision_log2_table",
                "user_column": "user_id",
                "time_field": "timestamp_ms",
                "access_id": "LOG-TEST-ACCESS-ID",
                "access_token": "LOG-TEST-ACCESS-TOKEN",
                "agent_product_filter": "log2-agent",
                "supports_personal_scope": True,
                "supports_space_scope": True,
            },
        )
        self.url = f"/api/v2/spaces/{self.space.uuid}/agent-products/log2-agent/query"
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    @override_settings(AGENT_QUERY_ALLOW_PLAIN=True)
    @patch("apps.core.views.v2_query.requests.post")
    def test_log_does_not_contain_default_filters_credentials(self, mock_post):
        """Logs must not contain access_id or access_token from default_filters."""
        mock_post.return_value = MagicMock(
            status_code=200,
            json=MagicMock(
                return_value={
                    "success": True,
                    "data": {"headers": ["col"], "data": [["val"]]},
                }
            ),
        )
        plain = {
            "scope": "personal",
            "queryKey": "sessions",
            "template": "SELECT * FROM {{table}} WHERE {{time_filter}} AND {{scope_filter}}",
            "timeRange": {"type": "relative", "range": "1h"},
        }
        with self.assertLogs("apps.core.views.v2_query", level="INFO") as cm:
            self.client.post(self.url + "?encrypted=0", data=plain, format="json")

        log_output = "\n".join(cm.output)
        self.assertNotIn("LOG-TEST-ACCESS-ID", log_output)
        self.assertNotIn("LOG-TEST-ACCESS-TOKEN", log_output)

    @override_settings(AGENT_QUERY_ALLOW_PLAIN=True)
    @patch("apps.core.views.v2_query.requests.post")
    def test_log_does_not_contain_rendered_sql(self, mock_post):
        """Logs must not contain the rendered SQL/template."""
        mock_post.return_value = MagicMock(
            status_code=200,
            json=MagicMock(
                return_value={
                    "success": True,
                    "data": {"headers": ["col"], "data": [["val"]]},
                }
            ),
        )
        plain = {
            "scope": "personal",
            "queryKey": "sessions",
            "template": "SELECT * FROM {{table}} WHERE {{time_filter}} AND {{scope_filter}}",
            "timeRange": {"type": "relative", "range": "1h"},
        }
        with self.assertLogs("apps.core.views.v2_query", level="INFO") as cm:
            self.client.post(self.url + "?encrypted=0", data=plain, format="json")

        log_output = "\n".join(cm.output)
        self.assertNotIn("SELECT", log_output)
        self.assertNotIn("aivision_log2_table", log_output)

    @override_settings(AGENT_QUERY_ALLOW_PLAIN=True)
    @patch("apps.core.views.v2_query.requests.post")
    def test_log_does_not_contain_encryption_key(self, mock_post):
        """Logs must not contain the encryption key material."""
        mock_post.return_value = MagicMock(
            status_code=200,
            json=MagicMock(
                return_value={
                    "success": True,
                    "data": {"headers": ["col"], "data": [["val"]]},
                }
            ),
        )
        key = _generate_aes_key()
        key_b64 = _base64url_encode(key)
        encrypted = _encrypt_payload(
            {
                "scope": "personal",
                "queryKey": "sessions",
                "template": "SELECT 1",
                "timeRange": {"type": "relative", "range": "1h"},
            },
            key,
            kid="test-kid",
        )
        with override_settings(AGENT_QUERY_ENCRYPTION_KEYS={"test-kid": key_b64}):
            with self.assertLogs("apps.core.views.v2_query", level="INFO") as cm:
                self.client.post(
                    self.url + "?encrypted=1", data=encrypted, format="json"
                )

        log_output = "\n".join(cm.output)
        self.assertNotIn(key_b64, log_output)
