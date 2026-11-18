"""Synthetic AgentLens responses exercise the real Django authorization boundary."""

from types import SimpleNamespace
from unittest.mock import patch

import requests
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.core.models import Space
from apps.core.tests.helpers import create_local_user, create_test_space_member


def remote(data, status=200):
    return SimpleNamespace(status_code=status, ok=status < 400, json=lambda: data)


@override_settings(
    AGENTLENS_BASE_URL="https://agentlens.invalid",
    AGENTLENS_TOKEN="internal-test-token",
)
class SandboxProxyTests(TestCase):
    def setUp(self):
        self.owner = create_local_user("sandbox-owner")
        self.space = Space.objects.create(name="Sandbox", type=2, creator=self.owner)
        create_test_space_member(space=self.space, user=self.owner, role=1)
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.base = f"/api/v1/spaces/{self.space.uuid}/integrations"
        self.connection = {
            "uuid": "connection-one",
            "space_uuid": str(self.space.uuid),
            "name": "OpenShell",
            "provider": "openshell",
            "endpoint": "https://plugin.invalid",
            "api_key_configured": True,
            "api_key": "private-token",
            "token": "another-secret",
            "status": "untested",
            "image": "rules:v1",
            "cpu": 1,
            "memory": "512Mi",
            "timeout_seconds": 5,
            "config_revision": 1,
            "provider_error": "provider trace private-token",
        }

    @patch("apps.core.views.sandbox_proxy.requests.get")
    def test_read_permissions_do_not_grant_management(self, get):
        reader = create_local_user("sandbox-reader")
        create_test_space_member(space=self.space, user=reader, role=3)
        self.client.force_authenticate(reader)
        get.return_value = remote({"items": [self.connection], "total": 1})
        response = self.client.get(self.base + "/sandbox-connections")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("api_key", response.data["data"]["items"][0])
        self.assertNotIn("private-token", str(response.data))
        get.reset_mock()
        for method, suffix, body in (
            ("post", "/sandbox-connections", {}),
            ("patch", "/sandbox-connection/connection-one", {}),
            ("post", "/sandbox-connection/connection-one/test", {}),
            ("delete", "/sandbox-connection/connection-one", None),
            ("patch", "/sandbox-binding", {"connection_uuid": None}),
        ):
            response = getattr(self.client, method)(
                self.base + suffix, body, format="json"
            )
            self.assertEqual(response.status_code, 403, (method, response.data))
        get.assert_not_called()

    @patch("apps.core.views.sandbox_proxy.requests.post")
    def test_developer_can_create_and_scope_and_audit_are_authoritative(self, post):
        developer = create_local_user("sandbox-developer")
        create_test_space_member(space=self.space, user=developer, role=2)
        self.client.force_authenticate(developer)
        post.return_value = remote(self.connection)
        response = self.client.post(
            self.base + "/sandbox-connections",
            {
                "name": "Sandbox",
                "space_uuid": "other",
                "creator": "forged",
                "modifier": "forged",
                "api_key": "write-only",
                "gateway_endpoint": "grpc://forged",
                "config_revision": 999,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["space_uuid"], str(self.space.uuid))
        self.assertEqual(body["creator"], str(developer.uuid))
        self.assertEqual(body["modifier"], str(developer.uuid))
        self.assertEqual(body["api_key"], "write-only")
        self.assertNotIn("gateway_endpoint", body)
        self.assertNotIn("config_revision", body)
        self.assertNotIn("write-only", str(response.data))
        self.assertFalse(post.call_args.kwargs["allow_redirects"])

    @patch("apps.core.views.sandbox_proxy.requests.patch")
    @patch("apps.core.views.sandbox_proxy.requests.post")
    @patch("apps.core.views.sandbox_proxy.requests.delete")
    @patch("apps.core.views.sandbox_proxy.requests.get")
    def test_cross_space_resources_cannot_be_read_mutated_tested_or_bound(
        self, get, delete, post, patch_remote
    ):
        get.return_value = remote({**self.connection, "space_uuid": "another-space"})
        for method, suffix, body in (
            ("get", "/sandbox-connection/connection-one", None),
            ("get", "/sandbox-connection/connection-one/runtime", None),
            ("patch", "/sandbox-connection/connection-one", {"name": "changed"}),
            ("post", "/sandbox-connection/connection-one/test", {}),
            ("delete", "/sandbox-connection/connection-one", None),
            (
                "patch",
                "/sandbox-binding",
                {"connection_uuid": "connection-one", "require_sandbox": True},
            ),
        ):
            response = getattr(self.client, method)(
                self.base + suffix, body, format="json"
            )
            self.assertEqual(response.status_code, 404, response.data)
        delete.assert_not_called()
        post.assert_not_called()
        patch_remote.assert_not_called()
        self.assertEqual(
            get.call_args.kwargs["params"], {"space_uuid": str(self.space.uuid)}
        )

    @patch("apps.core.views.sandbox_proxy.requests.get")
    def test_list_rejects_a_foreign_space_row_and_outsider_makes_no_remote_call(
        self, get
    ):
        get.return_value = remote(
            {
                "items": [self.connection, {**self.connection, "space_uuid": "other"}],
                "total": 2,
            }
        )
        self.assertEqual(
            self.client.get(self.base + "/sandbox-connections").status_code, 404
        )
        self.client.force_authenticate(create_local_user("sandbox-outsider"))
        get.reset_mock()
        self.assertIn(
            self.client.get(self.base + "/sandbox-connections").status_code, [403, 404]
        )
        get.assert_not_called()

    @patch("apps.core.views.sandbox_proxy.requests.patch")
    @patch("apps.core.views.sandbox_proxy.requests.get")
    def test_patch_keeps_identity_authoritative_and_redacts_response(
        self, get, patch_remote
    ):
        get.return_value = remote(self.connection)
        patch_remote.return_value = remote({**self.connection, "status": "ready"})
        response = self.client.patch(
            self.base + "/sandbox-connection/connection-one",
            {
                "space_uuid": "forged",
                "creator": "forged",
                "modifier": "forged",
                "api_key": "rotated",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            patch_remote.call_args.kwargs["json"],
            {
                "space_uuid": str(self.space.uuid),
                "modifier": str(self.owner.uuid),
                "api_key": "rotated",
            },
        )
        self.assertNotIn("private-token", str(response.data))

    @patch("apps.core.views.sandbox_proxy.requests.post")
    @patch("apps.core.views.sandbox_proxy.requests.get")
    def test_test_overwrites_scope_and_allows_only_safe_error_reason(self, get, post):
        get.return_value = remote(self.connection)
        post.return_value = remote(
            {
                **self.connection,
                "status": "unavailable",
                "test_error": "token=private-token",
            }
        )
        response = self.client.post(
            self.base + "/sandbox-connection/connection-one/test",
            {"space_uuid": "forged"},
            format="json",
        )
        self.assertEqual(
            post.call_args.kwargs["json"], {"space_uuid": str(self.space.uuid)}
        )
        self.assertEqual(response.data["data"]["test_error"], "unavailable")
        self.assertNotIn("private-token", str(response.data))
        post.return_value = remote(
            {**self.connection, "test_error": "sandbox_authentication_failed"}
        )
        response = self.client.post(
            self.base + "/sandbox-connection/connection-one/test", {}, format="json"
        )
        self.assertEqual(
            response.data["data"]["test_error"], "sandbox_authentication_failed"
        )

    @patch("apps.core.views.sandbox_proxy.requests.get")
    def test_transport_and_provider_errors_do_not_expose_credential_or_raw_message(
        self, get
    ):
        get.return_value = remote(
            {"code": "private-token", "msg": "private-token"}, 500
        )
        response = self.client.get(self.base + "/sandbox-connections")
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("private-token", str(response.data))
        get.return_value = remote({"code": {"api_key": "private-token"}}, 422)
        response = self.client.get(self.base + "/sandbox-connections")
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("private-token", str(response.data))
        get.side_effect = requests.ConnectionError(
            "https://user:private-token@plugin.invalid"
        )
        response = self.client.get(self.base + "/sandbox-connections")
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("private-token", str(response.data))

    @patch("apps.core.views.sandbox_proxy.requests.patch")
    @patch("apps.core.views.sandbox_proxy.requests.get")
    def test_binding_can_explicitly_clear_default_and_enforce_policy(
        self, get, patch_remote
    ):
        patch_remote.return_value = remote(
            {
                "space_uuid": str(self.space.uuid),
                "connection_uuid": None,
                "require_sandbox": True,
                "api_key": "private-token",
            }
        )
        response = self.client.patch(
            self.base + "/sandbox-binding",
            {
                "space_uuid": "forged",
                "connection_uuid": None,
                "require_sandbox": True,
                "modifier": "forged",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            patch_remote.call_args.kwargs["json"],
            {
                "space_uuid": str(self.space.uuid),
                "connection_uuid": None,
                "require_sandbox": True,
                "modifier": str(self.owner.uuid),
            },
        )
        get.assert_not_called()
        self.assertNotIn("private-token", str(response.data))

    @patch("apps.core.views.agentlens_proxy._forward_post")
    def test_rule_trial_injects_authorized_space(self, post):
        from rest_framework.response import Response

        post.return_value = Response({"code": 0, "data": {}})
        response = self.client.post(
            f"/api/v1/spaces/{self.space.uuid}/diagnose/diagnosers/rule/trial",
            {
                "space_uuid": "forged",
                "spec": {
                    "code": "def evaluate(trace): return {}",
                    "sandbox": {"mode": "inherit"},
                },
                "test_input": {},
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(post.call_args.args[1]["space_uuid"], str(self.space.uuid))

    @patch("apps.core.views.agentlens_proxy._forward_post")
    def test_input_preview_uses_authorized_space_and_requires_execution_permission(self, post):
        from rest_framework.response import Response

        post.return_value = Response({"code": 0, "data": {"valid": True}})
        url = f"/api/v1/spaces/{self.space.uuid}/diagnose/diagnosers/rule/input-preview"
        response = self.client.post(url, {"space_uuid": "forged", "spec": {}, "test_input": {}}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(post.call_args.args[0], "/api/v1/diagnoser/rule-input-preview")
        self.assertEqual(post.call_args.args[1]["space_uuid"], str(self.space.uuid))
        post.reset_mock()
        reader = create_local_user("input-budget-reader")
        create_test_space_member(space=self.space, user=reader, role=3)
        self.client.force_authenticate(reader)
        self.assertEqual(self.client.post(url, {}, format="json").status_code, 403)
        post.assert_not_called()

    @patch("apps.core.views.sandbox_proxy.requests.get")
    def test_runtime_read_is_scoped_readonly_and_redacted(self, get):
        reader = create_local_user("sandbox-runtime-reader")
        create_test_space_member(space=self.space, user=reader, role=3)
        self.client.force_authenticate(reader)
        runtime = {
            "connection_uuid": "connection-one",
            "config_revision": 1,
            "image": "rules:v1",
            "ready": True,
            "capabilities": ["custom_rule"],
            "runtime_manifests": [],
            "effective_timeout_seconds": 10.0,
            "api_key": "private-token",
        }
        get.side_effect = [remote(self.connection), remote(runtime)]
        response = self.client.get(
            self.base + "/sandbox-connection/connection-one/runtime"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"]["runtime_manifests"], [])
        self.assertEqual(response.data["data"]["effective_timeout_seconds"], 10.0)
        self.assertNotIn("private-token", str(response.data))
        self.assertEqual(
            get.call_args.kwargs["params"], {"space_uuid": str(self.space.uuid)}
        )
        self.assertEqual(
            self.client.post(
                self.base + "/sandbox-connection/connection-one/runtime",
                {},
                format="json",
            ).status_code,
            405,
        )

    @patch("apps.core.views.sandbox_proxy.requests.patch")
    @patch("apps.core.views.sandbox_proxy.requests.get")
    def test_recommended_patch_forwards_expected_revision(self, get, patch_remote):
        get.return_value = remote(self.connection)
        patch_remote.return_value = remote(
            {
                **self.connection,
                "config_revision": 2,
                "memory": "1Gi",
                "timeout_seconds": 30,
            }
        )
        response = self.client.patch(
            self.base + "/sandbox-connection/connection-one",
            {"expected_config_revision": 1, "memory": "1Gi", "timeout_seconds": 30},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            patch_remote.call_args.kwargs["json"]["expected_config_revision"], 1
        )
