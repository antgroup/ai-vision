"""V2 Agent Query security, template, encryption, and permission tests.

Covers:
  A. Template variable validation
  B. Template rendering
  C. Time range validation
  D. Encryption/decryption (crypto module)
  E. View integration (AgentDashboardQueryView)
  F. Log safety
"""

import base64
import json
import os
from unittest.mock import MagicMock, patch

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.core.crypto.agent_query_crypto import (
    base64url_decode,
    decrypt_agent_query_payload,
    is_encryption_enabled,
    is_plain_allowed,
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
)
from apps.core.query.time_range import validate_time_range
from apps.core.views.v2_query import AgentDashboardQueryView
from apps.core.trace_query.types import TraceQueryServiceError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _generate_aes_key() -> bytes:
    """Generate a fresh 256-bit AES key for tests."""
    return AESGCM.generate_key(bit_length=256)


def _base64url_encode(data: bytes) -> str:
    """Encode bytes to base64url string (no padding)."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _encrypt_payload(payload_dict: dict, key: bytes, kid: str = "test-kid") -> dict:
    """Encrypt a dict using AES-GCM and return the wire format."""
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


def _default_template_kwargs(**overrides):
    """Return default kwargs for build_template_variables."""
    defaults = {
        "table_name": "aivision_test_table",
        "time_field": "timestamp_ms",
        "user_column": "user_id",
        "scope": "personal",
        "user_id": "user-123",
        "time_range": {"type": "relative", "range": "1h"},
        "agent_product": "my-agent",
    }
    defaults.update(overrides)
    return defaults


# ---------------------------------------------------------------------------
# A. Template variable validation tests
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# B. Template rendering tests
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# C. Time range validation tests
# ---------------------------------------------------------------------------


class ValidateTimeRangeTests(TestCase):
    """template_renderer.validate_time_range"""

    def test_absolute_time_range_valid(self):
        from_ms, to_ms = validate_time_range(
            {
                "type": "absolute",
                "from": 1700000000000,
                "to": 1700086400000,
            }
        )
        self.assertEqual(from_ms, 1700000000000)
        self.assertEqual(to_ms, 1700086400000)

    def test_absolute_time_range_missing_from(self):
        with self.assertRaises(AgentQueryInvalidPayloadError):
            validate_time_range({"type": "absolute", "to": 1700086400000})

    def test_absolute_time_range_missing_to(self):
        with self.assertRaises(AgentQueryInvalidPayloadError):
            validate_time_range({"type": "absolute", "from": 1700000000000})

    def test_relative_time_range_valid(self):
        from_ms, to_ms = validate_time_range({"type": "relative", "range": "1h"})
        self.assertGreater(to_ms, from_ms)
        # Approximately 1 hour apart
        delta = to_ms - from_ms
        self.assertAlmostEqual(delta, 3600000, delta=1000)

    def test_relative_time_range_invalid_value(self):
        with self.assertRaises(AgentQueryInvalidPayloadError) as ctx:
            validate_time_range({"type": "relative", "range": "99d"})
        self.assertIn("range", ctx.exception.details)

    def test_time_range_exceeding_90_days(self):
        ninety_one_days_ms = 91 * 24 * 3600 * 1000
        with self.assertRaises(AgentQueryInvalidPayloadError) as ctx:
            validate_time_range(
                {
                    "type": "absolute",
                    "from": 1700000000000,
                    "to": 1700000000000 + ninety_one_days_ms,
                }
            )
        self.assertIn("span_days", ctx.exception.details)

    def test_from_greater_than_to(self):
        with self.assertRaises(AgentQueryInvalidPayloadError):
            validate_time_range(
                {
                    "type": "absolute",
                    "from": 1700086400000,
                    "to": 1700000000000,
                }
            )


# ---------------------------------------------------------------------------
# D. Encryption/decryption tests
# ---------------------------------------------------------------------------


class EncryptionDecryptionTests(TestCase):
    """crypto.agent_query_crypto"""

    @override_settings(AGENT_QUERY_ENCRYPTION_KEYS={"test-kid": None})
    def setUp(self):
        # Generate a fresh key and override settings with the real key
        self.key = _generate_aes_key()
        self.key_b64 = _base64url_encode(self.key)
        self.kid = "test-kid"

    def _patch_keys(self):
        """Return an override_settings context with the test key."""
        return override_settings(AGENT_QUERY_ENCRYPTION_KEYS={self.kid: self.key_b64})

    def test_encrypt_decrypt_roundtrip(self):
        payload = {
            "scope": "personal",
            "queryKey": "sessions",
            "template": "SELECT 1",
            "timeRange": {"type": "relative", "range": "1h"},
        }
        encrypted = _encrypt_payload(payload, self.key, kid=self.kid)
        with self._patch_keys():
            decrypted = decrypt_agent_query_payload(encrypted)
        self.assertEqual(decrypted, payload)

    def test_decryption_with_wrong_key_fails(self):
        payload = {"scope": "personal", "queryKey": "sessions"}
        encrypted = _encrypt_payload(payload, self.key, kid=self.kid)
        wrong_key = _generate_aes_key()
        wrong_b64 = _base64url_encode(wrong_key)
        with override_settings(AGENT_QUERY_ENCRYPTION_KEYS={self.kid: wrong_b64}):
            with self.assertRaises(AgentQueryDecryptFailedError):
                decrypt_agent_query_payload(encrypted)

    def test_invalid_payload_version(self):
        encrypted = _encrypt_payload({"scope": "x"}, self.key, kid=self.kid)
        encrypted["payloadVersion"] = "v2"
        with self._patch_keys():
            with self.assertRaises(AgentQueryInvalidPayloadError) as ctx:
                decrypt_agent_query_payload(encrypted)
            self.assertIn("payloadVersion", ctx.exception.details)

    def test_missing_kid(self):
        payload = {"scope": "x"}
        encrypted = _encrypt_payload(payload, self.key, kid=self.kid)
        del encrypted["kid"]
        with self._patch_keys():
            with self.assertRaises(AgentQueryInvalidPayloadError):
                decrypt_agent_query_payload(encrypted)

    def test_unknown_kid(self):
        payload = {"scope": "x"}
        encrypted = _encrypt_payload(payload, self.key, kid="unknown-kid")
        with self._patch_keys():
            with self.assertRaises(AgentQueryDecryptFailedError):
                decrypt_agent_query_payload(encrypted)

    def test_malformed_iv_wrong_length(self):
        payload = {"scope": "x"}
        encrypted = _encrypt_payload(payload, self.key, kid=self.kid)
        encrypted["iv"] = _base64url_encode(b"short")
        with self._patch_keys():
            with self.assertRaises(AgentQueryDecryptFailedError) as ctx:
                decrypt_agent_query_payload(encrypted)
            # Error details should NOT leak crypto implementation info (e.g. iv_length)
            self.assertEqual(ctx.exception.type_str, "AGENT_QUERY_DECRYPT_FAILED")

    def test_tampered_ciphertext_fails(self):
        payload = {"scope": "x"}
        encrypted = _encrypt_payload(payload, self.key, kid=self.kid)
        # Tamper with the ciphertext
        ct_bytes = base64.urlsafe_b64decode(encrypted["encryptedPayload"] + "==")
        tampered = bytes([ct_bytes[0] ^ 0xFF]) + ct_bytes[1:]
        encrypted["encryptedPayload"] = _base64url_encode(tampered)
        with self._patch_keys():
            with self.assertRaises(AgentQueryDecryptFailedError):
                decrypt_agent_query_payload(encrypted)

    def test_invalid_json_after_decryption(self):
        aesgcm = AESGCM(self.key)
        iv = os.urandom(12)
        plaintext = b"this is not json"
        ciphertext = aesgcm.encrypt(iv, plaintext, None)
        encrypted = {
            "payloadVersion": "v1",
            "kid": self.kid,
            "iv": _base64url_encode(iv),
            "encryptedPayload": _base64url_encode(ciphertext),
        }
        with self._patch_keys():
            with self.assertRaises(AgentQueryInvalidPayloadError):
                decrypt_agent_query_payload(encrypted)

    def test_non_dict_after_decryption(self):
        aesgcm = AESGCM(self.key)
        iv = os.urandom(12)
        plaintext = json.dumps([1, 2, 3]).encode("utf-8")
        ciphertext = aesgcm.encrypt(iv, plaintext, None)
        encrypted = {
            "payloadVersion": "v1",
            "kid": self.kid,
            "iv": _base64url_encode(iv),
            "encryptedPayload": _base64url_encode(ciphertext),
        }
        with self._patch_keys():
            with self.assertRaises(AgentQueryInvalidPayloadError):
                decrypt_agent_query_payload(encrypted)

    def test_is_encryption_enabled(self):
        with override_settings(AGENT_QUERY_ENCRYPTION_ENABLED=True):
            self.assertTrue(is_encryption_enabled())
        with override_settings(AGENT_QUERY_ENCRYPTION_ENABLED=False):
            self.assertFalse(is_encryption_enabled())

    def test_is_plain_allowed(self):
        with override_settings(AGENT_QUERY_ALLOW_PLAIN=True):
            self.assertTrue(is_plain_allowed())
        with override_settings(AGENT_QUERY_ALLOW_PLAIN=False):
            self.assertFalse(is_plain_allowed())

    def test_base64url_decode(self):
        # Standard base64url with padding
        self.assertEqual(base64url_decode("Zg=="), b"f")
        # Without padding
        self.assertEqual(base64url_decode("Zg"), b"f")
        # URL-safe characters
        self.assertEqual(base64url_decode("_-4="), base64.urlsafe_b64decode("_-4="))


# ---------------------------------------------------------------------------
# E. View integration tests
# ---------------------------------------------------------------------------


class AgentDashboardQueryViewTests(TestCase):
    """AgentDashboardQueryView integration tests via APIClient."""

    def setUp(self):
        self.client = APIClient()
        self.user = create_local_user("u001", name="Test User", status=1)
        self.space = Space.objects.create(
            name="Test Space", type=2, scope=1, creator=self.user, status=1
        )
        create_test_space_member(space=self.space, user=self.user, role=1)

        self.agent_type = AgentTypeConfig.objects.create(
            agent_name="TEST_AGENT",
            agent_product="test-agent",
            category="test",
            status=1,
        )
        self.agent = create_test_agent(
            space=self.space,
            agent_type=self.agent_type,
            agent_product="test-agent",
            name="Test Agent",
            status=1,
        )
        self.datasource = DataSource.objects.create(
            agent_type=self.agent_type,
            source_system="signoz",
            datasource_type="logs",
            enabled=1,
            config={
                "endpoint": "http://signoz.test",
                "access_id": "test-id",
                "access_token": "test-token",
                "table_name": "aivision_test_table",
                "time_field": "timestamp_ms",
                "user_column": "user_id",
                "agent_product_filter": "TestAgent",
                "supports_personal_scope": True,
                "supports_space_scope": True,
            },
        )
        self.url = f"/api/v2/spaces/{self.space.uuid}/agent-products/test-agent/query"
        self.batch_url = (
            f"/api/v2/spaces/{self.space.uuid}/agent-products/test-agent/query_batch"
        )

    def _plain_payload(self, **overrides):
        defaults = {
            "scope": "personal",
            "queryKey": "sessions",
            "template": "SELECT * FROM {{table}} WHERE {{time_filter}} AND {{scope_filter}}",
            "timeRange": {"type": "relative", "range": "1h"},
        }
        defaults.update(overrides)
        return defaults

    def _plain_batch_payload(self, queries=None, **overrides):
        defaults = {
            "scope": "personal",
            "timeRange": {"type": "relative", "range": "1h"},
            "queries": queries
            or [
                {
                    "queryKey": "sessions",
                    "template": "SELECT * FROM {{table}} WHERE {{time_filter}} AND {{scope_filter}}",
                },
                {
                    "queryKey": "tokens",
                    "template": "SELECT COUNT(*) AS count FROM {{table}} WHERE {{time_filter}} AND {{scope_filter}}",
                },
            ],
        }
        defaults.update(overrides)
        return defaults

    @override_settings(AGENT_QUERY_ALLOW_PLAIN=True)
    def test_framework_capacity_preserves_single_query_error_contract(self):
        self.client.force_authenticate(user=self.user)
        for code, status, kind in (
            (3036, 413, "AGENT_QUERY_RESOURCE_LIMIT"),
            (3037, 429, "AGENT_QUERY_BUSY"),
            (3035, 500, "AGENT_QUERY_EXECUTION_FAILED"),
        ):
            with self.subTest(code=code), patch(
                "apps.core.query.framework.query_framework",
                side_effect=TraceQueryServiceError("native query rejected", code),
            ):
                response = self.client.post(
                    self.url + "?e=0",
                    self._plain_payload(queryKey="framework.overview.event_stream"),
                    format="json",
                )
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.data["code"], status)
                self.assertEqual(response.data["message"], "native query rejected")
                self.assertEqual(response.data["error"]["type"], kind)
                self.assertEqual(
                    response.data["error"]["details"],
                    {"trace_query_code": code} if code in (3036, 3037) else {},
                )

    @override_settings(AGENT_QUERY_ALLOW_PLAIN=True)
    def test_framework_capacity_keeps_batch_successes_and_item_errors(self):
        self.client.force_authenticate(user=self.user)
        failed_key = "framework.overview.event_stream"
        success_key = "framework.events.timeseries"
        for code, status, kind in (
            (3036, 413, "AGENT_QUERY_RESOURCE_LIMIT"),
            (3037, 429, "AGENT_QUERY_BUSY"),
        ):
            with self.subTest(code=code), patch(
                "apps.core.query.framework.query_framework",
                side_effect=[
                    TraceQueryServiceError("native query rejected", code),
                    {"columns": [{"key": "count", "type": "number"}], "rows": [{"count": 7}]},
                ],
            ):
                response = self.client.post(
                    self.batch_url + "?e=0",
                    self._plain_batch_payload(queries=[{"queryKey": failed_key}, {"queryKey": success_key}]),
                    format="json",
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data["code"], 0)
                data = response.data["data"]
                self.assertEqual(set(data["results"]), {success_key})
                self.assertEqual(data["results"][success_key]["rows"], [{"count": 7}])
                self.assertEqual(data["errors"], {failed_key: {
                    "type": kind, "message": "native query rejected",
                    "details": {"trace_query_code": code}, "status": status,
                }})
                self.assertEqual(data["meta"]["success_count"], 1)
                self.assertEqual(data["meta"]["error_count"], 1)

    @override_settings(AGENT_QUERY_ALLOW_PLAIN=True)
    def test_framework_all_capacity_failures_keep_batch_envelope(self):
        self.client.force_authenticate(user=self.user)
        keys = ["framework.overview.event_stream", "framework.events.timeseries"]
        with patch("apps.core.query.framework.query_framework", side_effect=[
            TraceQueryServiceError("range too large", 3036),
            TraceQueryServiceError("retry shortly", 3037),
        ]):
            response = self.client.post(
                self.batch_url + "?e=0",
                self._plain_batch_payload(queries=[{"queryKey": key} for key in keys]),
                format="json",
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["code"], 0)
        data = response.data["data"]
        self.assertEqual(data["results"], {})
        self.assertEqual(data["errors"][keys[0]]["status"], 413)
        self.assertEqual(data["errors"][keys[1]]["status"], 429)
        self.assertEqual(data["meta"]["success_count"], 0)
        self.assertEqual(data["meta"]["error_count"], 2)

    @override_settings(AGENT_QUERY_ALLOW_PLAIN=True)
    @patch("apps.core.views.v2_query.requests.post")
    def test_user_not_in_space_gets_permission_denied(self, mock_post):
        other_user = create_local_user("u002", name="Other", status=1)
        self.client.force_authenticate(user=other_user)
        resp = self.client.post(
            self.url + "?e=0",
            data=self._plain_payload(),
            format="json",
        )
        # check_space_access_or_error returns code 3002
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data.get("code"), 3002)

    @override_settings(AGENT_QUERY_ALLOW_PLAIN=True)
    def test_unauthenticated_user_gets_permission_error(self):
        """Unauthenticated users are rejected (403 in DRF with IsAuthenticated)."""
        resp = self.client.post(
            self.url + "?e=0",
            data=self._plain_payload(),
            format="json",
        )
        self.assertIn(resp.status_code, [401, 403])

    @override_settings(AGENT_QUERY_ALLOW_PLAIN=True)
    @patch("apps.core.views.v2_query.requests.post")
    def test_nonexistent_agent_product_returns_config_not_found(self, mock_post):
        url = f"/api/v2/spaces/{self.space.uuid}/agent-products/nonexistent/query"
        self.client.force_authenticate(user=self.user)
        resp = self.client.post(
            url + "?e=0",
            data=self._plain_payload(),
            format="json",
        )
        self.assertEqual(resp.status_code, 404)
        error = resp.data.get("error", {})
        self.assertEqual(error.get("type"), "AGENT_QUERY_CONFIG_NOT_FOUND")

    @override_settings(AGENT_QUERY_ALLOW_PLAIN=True)
    @patch("apps.core.views.v2_query.requests.post")
    def test_personal_space_pending_agent_can_query(self, mock_post):
        self.space.type = 1
        self.space.save(update_fields=["type", "gmt_modified"])
        self.agent.status = 2
        self.agent.save(update_fields=["status", "gmt_modified"])
        mock_post.return_value = MagicMock(
            status_code=200,
            json=MagicMock(
                return_value={
                    "success": True,
                    "data": {"headers": ["value"], "data": [[1]]},
                }
            ),
        )

        self.client.force_authenticate(user=self.user)
        resp = self.client.post(
            self.url + "?e=0",
            data=self._plain_payload(),
            format="json",
        )

        self.assertEqual(resp.status_code, 200)
        mock_post.assert_called_once()

    @override_settings(AGENT_QUERY_ALLOW_PLAIN=True)
    @patch("apps.core.views.v2_query.requests.post")
    def test_business_space_pending_agent_can_query(self, mock_post):
        self.agent.status = 2
        self.agent.save(update_fields=["status", "gmt_modified"])
        mock_post.return_value = MagicMock(
            status_code=200,
            json=MagicMock(
                return_value={
                    "success": True,
                    "data": {"headers": ["value"], "data": [[1]]},
                }
            ),
        )

        self.client.force_authenticate(user=self.user)
        resp = self.client.post(
            self.url + "?e=0",
            data=self._plain_payload(),
            format="json",
        )

        self.assertEqual(resp.status_code, 200)
        mock_post.assert_called_once()

    @override_settings(AGENT_QUERY_ALLOW_PLAIN=True)
    @patch("apps.core.views.v2_query.requests.post")
    def test_disabled_agent_is_not_query_authorized(self, mock_post):
        self.agent.status = 0
        self.agent.save(update_fields=["status", "gmt_modified"])

        self.client.force_authenticate(user=self.user)
        resp = self.client.post(
            self.url + "?e=0",
            data=self._plain_payload(),
            format="json",
        )

        self.assertEqual(resp.status_code, 404)
        self.assertEqual(
            resp.data.get("error", {}).get("type"),
            "AGENT_QUERY_CONFIG_NOT_FOUND",
        )
        mock_post.assert_not_called()

    @override_settings(AGENT_QUERY_ALLOW_PLAIN=True)
    @patch("apps.core.views.v2_query.requests.post")
    def test_disabled_config_returns_not_found(self, mock_post):
        self.agent_type.status = 0
        self.agent_type.save()
        self.client.force_authenticate(user=self.user)
        resp = self.client.post(
            self.url + "?e=0",
            data=self._plain_payload(),
            format="json",
        )
        self.assertEqual(resp.status_code, 404)
        error = resp.data.get("error", {})
        self.assertEqual(error.get("type"), "AGENT_QUERY_CONFIG_NOT_FOUND")

    @override_settings(AGENT_QUERY_ALLOW_PLAIN=True)
    @patch("apps.core.views.v2_query.requests.post")
    def test_internal_agent_name_resolves_own_datasource(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=MagicMock(
                return_value={
                    "success": True,
                    "data": {"headers": ["value"], "data": [[1]]},
                }
            ),
        )

        self.client.force_authenticate(user=self.user)
        url = f"/api/v2/spaces/{self.space.uuid}/agent-products/TEST_AGENT/query"
        resp = self.client.post(
            url + "?e=0",
            data=self._plain_payload(),
            format="json",
        )

        self.assertEqual(resp.status_code, 200)
        body = mock_post.call_args.kwargs["json"]
        self.assertNotIn("simpleSql", body)
        self.assertEqual(body["user"], self.user.uuid)

    @override_settings(AGENT_QUERY_ALLOW_PLAIN=True)
    @patch("apps.core.views.v2_query.requests.post")
    def test_legacy_agent_without_type_is_not_query_authorized(self, mock_post):
        self.agent.delete()
        create_test_agent(
            space=self.space,
            agent_type=None,
            agent_product="test-agent",
            name="Legacy Test Agent",
            status=1,
        )
        self.client.force_authenticate(user=self.user)
        url = f"/api/v2/spaces/{self.space.uuid}/agent-products/TEST_AGENT/query"
        resp = self.client.post(
            url + "?e=0",
            data=self._plain_payload(),
            format="json",
        )

        self.assertEqual(resp.status_code, 404)
        error = resp.data.get("error", {})
        self.assertEqual(error.get("type"), "AGENT_QUERY_CONFIG_NOT_FOUND")
        self.assertEqual(resp.data.get("message"), "Agent query config not found")
        mock_post.assert_not_called()

    def test_dashboard_query_uses_datasource_agent_product_filter_key(self):
        config = dict(self.datasource.config)
        config["agent_product_filter"] = "ClaudeCode"
        config["agent_product"] = "Codex"
        config["agent_prodct"] = "Codex"
        context = {
            "ds_config": config,
            "agent_type": self.agent_type,
            "user_id": self.user.uuid,
            "source_system": self.datasource.source_system,
            "datasource": self.datasource,
        }

        rendered = AgentDashboardQueryView._render_query_payload(
            payload=self._plain_payload(
                template=(
                    "SELECT * FROM {{table}} WHERE {{time_filter}} "
                    "AND `resource.attributes.ant.agent.product` = '{{agent_product}}'"
                ),
            ),
            context=context,
            agent_product="test-agent",
        )

        self.assertNotIn("rendered_query", rendered)
        self.assertEqual(rendered["scope"], "personal")

    def test_query_config_internal_agent_name_returns_public_product(self):
        self.client.force_authenticate(user=self.user)
        url = "/api/v2/agent-products/TEST_AGENT/query-config"
        resp = self.client.get(url)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["data"]["agent_product"], "test-agent")
        self.assertTrue(
            resp.data["data"]["capabilities"]["supports_personal_dashboard"]
        )

    def test_space_query_config_returns_payload(self):
        self.client.force_authenticate(user=self.user)
        url = f"/api/v2/spaces/{self.space.uuid}/agent-products/test-agent/query-config"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["data"]["agent_product"], "test-agent")
        self.assertIn("capabilities", resp.data["data"])

    def test_space_datasource_config_returns_payload(self):
        self.client.force_authenticate(user=self.user)
        url = (
            f"/api/v2/spaces/{self.space.uuid}"
            "/agent-products/test-agent/datasource-config"
        )
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        data = resp.data["data"]
        self.assertIn("traceSources", data)
        self.assertIn("eventSources", data)
        self.assertIn("defaultEventSource", data)

    def test_space_query_config_user_not_member_returns_permission_denied(self):
        other_user = create_local_user("u-qc-1", name="Other", status=1)
        self.client.force_authenticate(user=other_user)
        url = f"/api/v2/spaces/{self.space.uuid}/agent-products/test-agent/query-config"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data.get("code"), 3002)

    def test_space_query_config_unauthenticated_returns_401_or_403(self):
        url = f"/api/v2/spaces/{self.space.uuid}/agent-products/test-agent/query-config"
        resp = self.client.get(url)
        self.assertIn(resp.status_code, [401, 403])

    def test_space_query_config_nonexistent_space_returns_404(self):
        self.client.force_authenticate(user=self.user)
        url = "/api/v2/spaces/nonexistent-uuid/agent-products/test-agent/query-config"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.data.get("code"), 3001)

    def test_space_query_config_no_routable_agent_returns_404(self):
        """Space-scoped path does NOT fall back — surfaces config missing."""
        self.agent.delete()
        self.client.force_authenticate(user=self.user)
        url = f"/api/v2/spaces/{self.space.uuid}/agent-products/test-agent/query-config"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 404)
        error = resp.data.get("error", {})
        self.assertEqual(error.get("type"), "AGENT_QUERY_CONFIG_NOT_FOUND")

    def test_space_datasource_config_no_routable_agent_returns_404(self):
        self.agent.delete()
        self.client.force_authenticate(user=self.user)
        url = (
            f"/api/v2/spaces/{self.space.uuid}"
            "/agent-products/test-agent/datasource-config"
        )
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 404)
        error = resp.data.get("error", {})
        self.assertEqual(error.get("type"), "AGENT_QUERY_CONFIG_NOT_FOUND")

    def test_legacy_query_config_has_deprecation_header(self):
        self.client.force_authenticate(user=self.user)
        url = "/api/v2/agent-products/TEST_AGENT/query-config"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Deprecation"], "true")

    def test_legacy_datasource_config_has_deprecation_header(self):
        self.client.force_authenticate(user=self.user)
        url = "/api/v2/agent-products/TEST_AGENT/datasource-config"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Deprecation"], "true")

    @override_settings(AGENT_QUERY_ALLOW_PLAIN=True)
    @patch("apps.core.views.v2_query.requests.post")
    def test_unknown_scope_returns_unsupported_scope(self, mock_post):
        self.client.force_authenticate(user=self.user)
        payload = self._plain_payload(scope="invalid_scope")
        resp = self.client.post(
            self.url + "?e=0",
            data=payload,
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        error = resp.data.get("error", {})
        self.assertEqual(error.get("type"), "AGENT_QUERY_UNSUPPORTED_SCOPE")

    @override_settings(AGENT_QUERY_ALLOW_PLAIN=False)
    def test_encrypted_0_when_plain_disabled_returns_403(self):
        self.client.force_authenticate(user=self.user)
        resp = self.client.post(
            self.url + "?e=0",
            data=self._plain_payload(),
            format="json",
        )
        self.assertEqual(resp.status_code, 403)
        error = resp.data.get("error", {})
        self.assertEqual(error.get("type"), "AGENT_QUERY_PLAIN_DISABLED")

    @override_settings(AGENT_QUERY_ALLOW_PLAIN=True)
    @patch("apps.core.views.v2_query.requests.post")
    def test_missing_required_payload_fields_returns_invalid_payload(self, mock_post):
        self.client.force_authenticate(user=self.user)
        resp = self.client.post(
            self.url + "?e=0",
            data={"scope": "personal"},  # missing queryKey, template, timeRange
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        error = resp.data.get("error", {})
        self.assertEqual(error.get("type"), "AGENT_QUERY_INVALID_PAYLOAD")

    @override_settings(AGENT_QUERY_ALLOW_PLAIN=True)
    @patch("apps.core.views.v2_query.requests.post")
    def test_error_response_follows_v2_envelope_format(self, mock_post):
        """Error responses must have {code, message, error: {type, details}}."""
        self.client.force_authenticate(user=self.user)
        resp = self.client.post(
            self.url + "?e=0",
            data={"scope": "invalid_scope"},
            format="json",
        )
        self.assertIn("code", resp.data)
        self.assertIn("message", resp.data)
        self.assertIn("error", resp.data)
        error = resp.data["error"]
        self.assertIn("type", error)
        self.assertIn("details", error)

    @override_settings(AGENT_QUERY_ALLOW_PLAIN=True)
    @patch("apps.core.views.v2_query.requests.post")
    def test_personal_scope_not_supported_returns_unsupported_scope(self, mock_post):
        self.agent_type.display_scope = "BUSINESS"
        self.agent_type.save(update_fields=["display_scope"])
        self.client.force_authenticate(user=self.user)
        resp = self.client.post(
            self.url + "?e=0",
            data=self._plain_payload(scope="personal"),
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        error = resp.data.get("error", {})
        self.assertEqual(error.get("type"), "AGENT_QUERY_UNSUPPORTED_SCOPE")

    @override_settings(AGENT_QUERY_ALLOW_PLAIN=True)
    @patch("apps.core.views.v2_query.requests.post")
    def test_space_scope_not_supported_returns_unsupported_scope(self, mock_post):
        config = self.datasource.config.copy()
        config["supports_space_scope"] = False
        self.datasource.config = config
        self.datasource.save()
        self.client.force_authenticate(user=self.user)
        resp = self.client.post(
            self.url + "?e=0",
            data=self._plain_payload(scope="space"),
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        error = resp.data.get("error", {})
        self.assertEqual(error.get("type"), "AGENT_QUERY_UNSUPPORTED_SCOPE")

    @override_settings(AGENT_QUERY_ALLOW_PLAIN=True)
    @patch("apps.core.views.v2_query.requests.post")
    def test_batch_query_returns_results_keyed_by_query_key(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=MagicMock(
                return_value={
                    "success": True,
                    "data": {"headers": ["value"], "data": [[1]]},
                }
            ),
        )

        self.client.force_authenticate(user=self.user)
        resp = self.client.post(
            self.batch_url + "?e=0",
            data=self._plain_batch_payload(),
            format="json",
        )

        self.assertEqual(resp.status_code, 200)
        data = resp.data["data"]
        self.assertEqual(set(data["results"].keys()), {"sessions", "tokens"})
        self.assertEqual(data["errors"], {})
        self.assertEqual(data["meta"]["query_count"], 2)
        self.assertEqual(mock_post.call_count, 2)

    @override_settings(AGENT_QUERY_ALLOW_PLAIN=True)
    @patch("apps.core.views.v2_query.requests.post")
    def test_batch_query_individual_failure_does_not_fail_batch(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=MagicMock(
                return_value={
                    "success": True,
                    "data": {"headers": ["value"], "data": [[1]]},
                }
            ),
        )
        mock_post.side_effect = [mock_post.return_value, ValueError("query failed")]
        payload = self._plain_batch_payload(
            queries=[
                {
                    "queryKey": "sessions",
                    "template": "SELECT * FROM {{table}} WHERE {{time_filter}} AND {{scope_filter}}",
                },
                {
                    "queryKey": "bad-template",
                    "template": "SELECT * FROM {{raw_table}}",
                },
            ]
        )

        self.client.force_authenticate(user=self.user)
        resp = self.client.post(
            self.batch_url + "?e=0",
            data=payload,
            format="json",
        )

        self.assertEqual(resp.status_code, 200)
        data = resp.data["data"]
        self.assertIn("sessions", data["results"])
        self.assertNotIn("bad-template", data["results"])
        self.assertEqual(
            data["errors"]["bad-template"]["type"],
            "AGENT_QUERY_EXECUTION_FAILED",
        )
        self.assertEqual(mock_post.call_count, 2)

    @override_settings(AGENT_QUERY_ALLOW_PLAIN=True)
    @patch("apps.core.views.v2_query.requests.post")
    def test_batch_query_scope_error_fails_whole_batch(self, mock_post):
        self.client.force_authenticate(user=self.user)
        resp = self.client.post(
            self.batch_url + "?e=0",
            data=self._plain_batch_payload(scope="invalid_scope"),
            format="json",
        )

        self.assertEqual(resp.status_code, 400)
        error = resp.data.get("error", {})
        self.assertEqual(error.get("type"), "AGENT_QUERY_UNSUPPORTED_SCOPE")
        mock_post.assert_not_called()


# ---------------------------------------------------------------------------
# F. Log safety tests
# ---------------------------------------------------------------------------


class LogSafetyTests(TestCase):
    """Verify that sensitive data does not appear in log output."""

    def setUp(self):
        self.user = create_local_user("u-log", name="Log User", status=1)
        self.space = Space.objects.create(
            name="Log Space", type=2, scope=1, creator=self.user, status=1
        )
        create_test_space_member(space=self.space, user=self.user, role=1)
        self.agent_type = AgentTypeConfig.objects.create(
            agent_name="LOG_AGENT",
            agent_product="log-agent",
            category="test",
            status=1,
        )
        create_test_agent(
            space=self.space,
            agent_type=self.agent_type,
            agent_product="log-agent",
            name="Log Agent",
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
                "table_name": "aivision_log_table",
                "time_field": "timestamp_ms",
                "user_column": "user_id",
                "agent_product_filter": "LogAgent",
                "supports_personal_scope": True,
                "supports_space_scope": True,
            },
        )
        self.url = f"/api/v2/spaces/{self.space.uuid}/agent-products/log-agent/query"
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    @override_settings(AGENT_QUERY_ALLOW_PLAIN=True)
    @patch("apps.core.views.v2_query.requests.post")
    def test_log_does_not_contain_encrypted_payload(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=MagicMock(
                return_value={
                    "success": True,
                    "data": {"headers": ["col"], "data": [["val"]]},
                }
            ),
        )
        # This will fail at decryption (no key configured), but let's check logging path anyway
        # For the successful path, we check logs explicitly
        with self.assertLogs("apps.core.views.v2_query", level="INFO") as cm:
            # Use plain request to get a successful path
            plain = {
                "scope": "personal",
                "queryKey": "sessions",
                "template": "SELECT * FROM {{table}} WHERE {{time_filter}} AND {{scope_filter}}",
                "timeRange": {"type": "relative", "range": "1h"},
            }
            self.client.post(self.url + "?e=0", data=plain, format="json")

        log_output = "\n".join(cm.output)
        # Should not contain encryption key material
        self.assertNotIn("SECRET_ENCRYPTED_BLOB", log_output)
        # Should not contain rendered UCS/SQL
        # The log line only contains metadata, not the rendered template
        self.assertNotIn("SELECT * FROM", log_output)

    @override_settings(
        AGENT_QUERY_ALLOW_PLAIN=True, AGENT_QUERY_ENCRYPTION_KEYS={"test-kid": None}
    )
    @patch("apps.core.views.v2_query.requests.post")
    def test_log_does_not_contain_encryption_key(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=MagicMock(
                return_value={
                    "success": True,
                    "data": {"headers": ["col"], "data": [["val"]]},
                }
            ),
        )
        test_key = _generate_aes_key()
        key_b64 = _base64url_encode(test_key)
        encrypted = _encrypt_payload(
            {
                "scope": "personal",
                "queryKey": "sessions",
                "template": "SELECT 1",
                "timeRange": {"type": "relative", "range": "1h"},
            },
            test_key,
            kid="test-kid",
        )
        with override_settings(AGENT_QUERY_ENCRYPTION_KEYS={"test-kid": key_b64}):
            with self.assertLogs("apps.core.views.v2_query", level="INFO") as cm:
                self.client.post(self.url + "?e=1", data=encrypted, format="json")

        log_output = "\n".join(cm.output)
        # The raw base64 key should NOT appear in logs
        self.assertNotIn(key_b64, log_output)

    @override_settings(AGENT_QUERY_ALLOW_PLAIN=True)
    @patch("apps.core.views.v2_query.requests.post")
    def test_log_does_not_contain_decrypted_template(self, mock_post):
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
            self.client.post(self.url + "?e=0", data=plain, format="json")

        log_output = "\n".join(cm.output)
        # The rendered SQL should NOT appear in logs
        self.assertNotIn("SELECT * FROM", log_output)
        self.assertNotIn("aivision_log_table", log_output)
