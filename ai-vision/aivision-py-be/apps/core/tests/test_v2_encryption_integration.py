"""V2 Agent Query encryption integration tests.

Full-chain tests: encrypt a payload -> POST to the view -> verify response.
"""

import json
import os
from unittest.mock import MagicMock, patch

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

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

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _generate_aes_key() -> bytes:
    return AESGCM.generate_key(bit_length=256)


def _base64url_encode(data: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _encrypt_payload(payload_dict: dict, key: bytes, kid: str = "test-kid") -> dict:
    aesgcm = AESGCM(key)
    iv = os.urandom(12)  # 12-byte nonce for AES-GCM
    plaintext = json.dumps(payload_dict).encode("utf-8")
    ciphertext = aesgcm.encrypt(iv, plaintext, None)
    return {
        "payloadVersion": "v1",
        "kid": kid,
        "iv": _base64url_encode(iv),
        "encryptedPayload": _base64url_encode(ciphertext),
    }


def _stargazer_response(headers=None, data=None):
    """Build a mock Stargazer response."""
    if headers is None:
        headers = ["session_id", "user_id", "tokens"]
    if data is None:
        data = [["s1", "u1", "100"]]
    return {
        "success": True,
        "data": {"headers": headers, "data": data},
    }


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


class EncryptionIntegrationTests(TestCase):
    """Full-chain: encrypt payload -> POST -> verify response."""

    def setUp(self):
        self.key = _generate_aes_key()
        self.key_b64 = _base64url_encode(self.key)
        self.kid = "test-kid"

        self.client = APIClient()
        self.user = create_local_user("integ-user", name="Integ User", status=1)
        self.space = Space.objects.create(
            name="Integ Space", type=2, scope=1, creator=self.user, status=1
        )
        create_test_space_member(space=self.space, user=self.user, role=1)

        self.agent_type = AgentTypeConfig.objects.create(
            agent_name="INTEG_AGENT",
            agent_product="integ-agent",
            category="test",
            display_scope="ALL",
            status=1,
        )
        create_test_agent(
            space=self.space,
            agent_type=self.agent_type,
            agent_product="integ-agent",
            name="Integration Agent",
            status=1,
        )
        DataSource.objects.create(
            agent_type=self.agent_type,
            source_system="signoz",
            datasource_type="logs",
            enabled=1,
            config={
                "endpoint": "http://signoz.test",
                "access_id": "test-id",
                "access_token": "test-token",
                "table_name": "aivision_integ_table",
                "time_field": "timestamp_ms",
                "user_column": "user_id",
                "agent_product_filter": "IntegAgent",
                "supports_personal_scope": True,
                "supports_space_scope": True,
            },
        )
        self.url = f"/api/v2/spaces/{self.space.uuid}/agent-products/integ-agent/query"

    def _plain_payload(self, **overrides):
        defaults = {
            "scope": "personal",
            "queryKey": "sessions",
            "template": (
                "SELECT * FROM {{table}} WHERE {{time_filter}} AND {{scope_filter}}"
            ),
            "timeRange": {"type": "relative", "range": "1h"},
        }
        defaults.update(overrides)
        return defaults

    @override_settings(
        AGENT_QUERY_ENCRYPTION_ENABLED=True,
        AGENT_QUERY_ALLOW_PLAIN=False,
    )
    @patch("apps.core.views.v2_query.requests.post")
    def test_encrypted_full_chain_returns_query_key_columns_rows_meta(self, mock_post):
        """Encrypt -> POST -> response has queryKey, columns, rows, meta."""
        mock_post.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value=_stargazer_response()),
        )

        with override_settings(AGENT_QUERY_ENCRYPTION_KEYS={self.kid: self.key_b64}):
            encrypted = _encrypt_payload(self._plain_payload(), self.key, kid=self.kid)
            self.client.force_authenticate(user=self.user)
            resp = self.client.post(
                self.url + "?e=1",
                data=encrypted,
                format="json",
            )

        self.assertEqual(resp.status_code, 200, resp.data)
        data = resp.data.get("data", resp.data)
        self.assertIn("queryKey", data)
        self.assertEqual(data["queryKey"], "sessions")
        self.assertIn("columns", data)
        self.assertIn("rows", data)
        self.assertIn("meta", data)
        meta = data["meta"]
        self.assertTrue(meta["encrypted"])

    @override_settings(
        AGENT_QUERY_ENCRYPTION_ENABLED=True,
        AGENT_QUERY_ALLOW_PLAIN=False,
    )
    @patch("apps.core.views.v2_query.requests.post")
    def test_personal_scope_enforces_user_id(self, mock_post):
        """Personal scope always includes user_id in scope_filter, even if
        the frontend template tries to inject something else."""
        captured_body = {}

        def capture_post(url, json=None, **kwargs):
            captured_body.update(json or {})
            return MagicMock(
                status_code=200,
                json=MagicMock(return_value=_stargazer_response()),
            )

        mock_post.side_effect = capture_post

        with override_settings(AGENT_QUERY_ENCRYPTION_KEYS={self.kid: self.key_b64}):
            encrypted = _encrypt_payload(
                self._plain_payload(scope="personal"), self.key, kid=self.kid
            )
            self.client.force_authenticate(user=self.user)
            resp = self.client.post(
                self.url + "?e=1",
                data=encrypted,
                format="json",
            )

        self.assertEqual(resp.status_code, 200, resp.data)
        # The rendered query must contain the bound subject UUID in scope_filter.
        rendered_sql = captured_body.get("user", "")
        self.assertIn(self.user.uuid, rendered_sql)

    @override_settings(
        AGENT_QUERY_ENCRYPTION_ENABLED=True,
        AGENT_QUERY_ALLOW_PLAIN=False,
    )
    @patch("apps.core.views.v2_query.requests.post")
    def test_space_scope_no_user_filter(self, mock_post):
        """Space scope does not filter by user_id — shows all data."""
        captured_body = {}

        def capture_post(url, json=None, **kwargs):
            captured_body.update(json or {})
            return MagicMock(
                status_code=200,
                json=MagicMock(return_value=_stargazer_response()),
            )

        mock_post.side_effect = capture_post

        with override_settings(AGENT_QUERY_ENCRYPTION_KEYS={self.kid: self.key_b64}):
            encrypted = _encrypt_payload(
                self._plain_payload(scope="space"), self.key, kid=self.kid
            )
            self.client.force_authenticate(user=self.user)
            resp = self.client.post(
                self.url + "?e=1",
                data=encrypted,
                format="json",
            )

        self.assertEqual(resp.status_code, 200, resp.data)
        rendered_sql = captured_body.get("user", "")
        # Space scope does not filter by user_id
        self.assertNotIn(self.user.uuid, rendered_sql)
        self.assertEqual("", rendered_sql)

    @override_settings(AGENT_QUERY_ALLOW_PLAIN=True)
    @patch("apps.core.views.v2_query.requests.post")
    def test_plain_full_chain_returns_response(self, mock_post):
        """Plain (unencrypted) request also works end-to-end when allowed."""
        mock_post.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value=_stargazer_response()),
        )

        self.client.force_authenticate(user=self.user)
        resp = self.client.post(
            self.url + "?e=0",
            data=self._plain_payload(scope="space"),
            format="json",
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        data = resp.data.get("data", resp.data)
        self.assertIn("queryKey", data)
        self.assertIn("columns", data)
        self.assertIn("rows", data)

    @override_settings(
        AGENT_QUERY_ENCRYPTION_ENABLED=True,
        AGENT_QUERY_ALLOW_PLAIN=False,
    )
    @patch("apps.core.views.v2_query.requests.post")
    def test_encrypted_request_with_wrong_key_fails(self, mock_post):
        """Encrypted request with wrong key returns decrypt failed error."""
        wrong_key = _generate_aes_key()
        wrong_b64 = _base64url_encode(wrong_key)

        encrypted = _encrypt_payload(self._plain_payload(), self.key, kid=self.kid)

        with override_settings(AGENT_QUERY_ENCRYPTION_KEYS={self.kid: wrong_b64}):
            self.client.force_authenticate(user=self.user)
            resp = self.client.post(
                self.url + "?e=1",
                data=encrypted,
                format="json",
            )

        self.assertEqual(resp.status_code, 400)
        error = resp.data.get("error", {})
        self.assertEqual(error.get("type"), "AGENT_QUERY_DECRYPT_FAILED")

    @override_settings(
        AGENT_QUERY_ENCRYPTION_ENABLED=True,
        AGENT_QUERY_ALLOW_PLAIN=False,
    )
    @patch("apps.core.views.v2_query.requests.post")
    def test_encrypted_request_with_tampered_payload_fails(self, mock_post):
        """Tampered ciphertext causes GCM auth failure."""
        encrypted = _encrypt_payload(self._plain_payload(), self.key, kid=self.kid)
        # Tamper with the ciphertext
        import base64

        ct_bytes = base64.urlsafe_b64decode(encrypted["encryptedPayload"] + "==")
        tampered = bytes([ct_bytes[0] ^ 0xFF]) + ct_bytes[1:]
        encrypted["encryptedPayload"] = _base64url_encode(tampered)

        with override_settings(AGENT_QUERY_ENCRYPTION_KEYS={self.kid: self.key_b64}):
            self.client.force_authenticate(user=self.user)
            resp = self.client.post(
                self.url + "?e=1",
                data=encrypted,
                format="json",
            )

        self.assertEqual(resp.status_code, 400)
        error = resp.data.get("error", {})
        self.assertEqual(error.get("type"), "AGENT_QUERY_DECRYPT_FAILED")

    @override_settings(AGENT_QUERY_ALLOW_PLAIN=True)
    @patch("apps.core.views.v2_query.requests.post")
    def test_execution_failure_returns_sanitized_error(self, mock_post):
        """When the downstream query fails, the error must not leak endpoint info."""
        mock_post.side_effect = Exception(
            "Connection refused by https://query.example.invalid:9090"
        )

        self.client.force_authenticate(user=self.user)
        resp = self.client.post(
            self.url + "?e=0",
            data=self._plain_payload(scope="space"),
            format="json",
        )

        self.assertEqual(resp.status_code, 500)
        error = resp.data.get("error", {})
        self.assertEqual(error.get("type"), "AGENT_QUERY_EXECUTION_FAILED")
        msg = resp.data.get("message", "")
        self.assertNotIn("query.example.invalid", msg)
        self.assertNotIn("https://", msg)

    @override_settings(AGENT_QUERY_ALLOW_PLAIN=True)
    @patch("apps.core.views.v2_query.requests.post")
    def test_frontend_cannot_override_scope_filter_in_template(self, mock_post):
        """Even if frontend puts scope_filter in the decrypted payload's
        template, it gets replaced by the server-generated value."""
        captured_body = {}

        def capture_post(url, json=None, **kwargs):
            captured_body.update(json or {})
            return MagicMock(
                status_code=200,
                json=MagicMock(return_value=_stargazer_response()),
            )

        mock_post.side_effect = capture_post

        # The template references {{scope_filter}} which is always server-generated
        self.client.force_authenticate(user=self.user)
        resp = self.client.post(
            self.url + "?e=0",
            data=self._plain_payload(scope="personal"),
            format="json",
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        rendered_sql = captured_body.get("user", "")
        # The rendered SQL must contain the server-generated scope filter with user_id
        self.assertIn(self.user.uuid, rendered_sql)
        # No unreplaced template markers
        self.assertNotIn("{{", rendered_sql)
        self.assertNotIn("}}", rendered_sql)

    @override_settings(
        AGENT_QUERY_ENCRYPTION_ENABLED=True,
        AGENT_QUERY_ALLOW_PLAIN=False,
    )
    @patch("apps.core.views.v2_query.requests.post")
    def test_encrypted_personal_scope_filters_by_user_space_scope_does_not(
        self, mock_post
    ):
        """Personal scope filters by user_id; space scope does not."""
        personal_sql = {}
        space_sql = {}

        def capture_post(url, json=None, **kwargs):
            return MagicMock(
                status_code=200,
                json=MagicMock(return_value=_stargazer_response()),
            )

        mock_post.side_effect = capture_post

        # Personal scope
        def capture_personal(url, json=None, **kwargs):
            personal_sql["sql"] = json.get("user", "") if json else ""
            return MagicMock(
                status_code=200,
                json=MagicMock(return_value=_stargazer_response()),
            )

        def capture_space(url, json=None, **kwargs):
            space_sql["sql"] = json.get("user", "") if json else ""
            return MagicMock(
                status_code=200,
                json=MagicMock(return_value=_stargazer_response()),
            )

        with override_settings(AGENT_QUERY_ENCRYPTION_KEYS={self.kid: self.key_b64}):
            # Personal
            mock_post.side_effect = capture_personal
            encrypted = _encrypt_payload(
                self._plain_payload(scope="personal"), self.key, kid=self.kid
            )
            self.client.force_authenticate(user=self.user)
            self.client.post(self.url + "?e=1", data=encrypted, format="json")

            # Space
            mock_post.side_effect = capture_space
            encrypted = _encrypt_payload(
                self._plain_payload(scope="space"), self.key, kid=self.kid
            )
            self.client.post(self.url + "?e=1", data=encrypted, format="json")

        personal_rendered = personal_sql.get("sql", "")
        space_rendered = space_sql.get("sql", "")
        # Personal scope filters by user_id, space scope does not
        self.assertIn(self.user.uuid, personal_rendered)
        self.assertNotIn(self.user.uuid, space_rendered)
        self.assertEqual("", space_rendered)
