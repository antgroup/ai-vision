"""Public model-discovery route, with real permission checks and mocked AgentLens."""

from types import SimpleNamespace
from unittest.mock import patch

import requests
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.core.models import Space, SpaceMember, User


@override_settings(
    AGENTLENS_BASE_URL="https://agentlens.invalid",
    AGENTLENS_TOKEN="fixture-agentlens-token",
)
class ModelConnectionDiscoveryTests(TestCase):
    def setUp(self):
        self.developer = self.user("developer@example.com")
        self.member = self.user("member@example.com")
        self.admin = self.user("admin@example.com")
        self.space = Space.objects.create(
            name="Discovery", type=2, scope=1, creator=self.admin, status=1
        )
        self.other_space = Space.objects.create(
            name="Other", type=2, scope=1, creator=self.admin, status=1
        )
        for user, role in ((self.admin, 1), (self.developer, 2), (self.member, 3)):
            SpaceMember.objects.create(space=self.space, user=user, role=role)
        self.client = APIClient()
        self.client.force_authenticate(user=self.developer)
        self.url = (
            f"/api/v1/spaces/{self.space.uuid}/diagnose/model-connections/discover"
        )
        self.payload = {
            "endpoint": "https://api.openai.com/v1",
            "api_key": "fixture-secret",
        }
        self.post = self.enterContext(
            patch("apps.core.views.agentlens_proxy.http_requests.post")
        )
        self.get = self.enterContext(
            patch("apps.core.views.agentlens_proxy.http_requests.get")
        )
        self.post.return_value = self.remote({"items": [{"id": "model-a"}], "total": 1})
        self.get.return_value = self.remote(
            {
                "uuid": "connection-1",
                "space_uuid": self.space.uuid,
                "api_key_configured": True,
            }
        )

    @staticmethod
    def user(email):
        return User.objects.create(
            name=email,
            email=email,
            normalized_email=email,
            status=1,
        )

    @staticmethod
    def remote(body, status=200):
        return SimpleNamespace(
            status_code=status,
            ok=status < 400,
            text="private upstream body",
            json=lambda: body,
        )

    def test_developer_and_admin_can_discover_with_post_body_and_fixed_timeout(self):
        for user in (self.developer, self.admin):
            with self.subTest(role=user.name):
                self.client.force_authenticate(user=user)
                self.post.reset_mock()
                response = self.client.post(self.url, self.payload, format="json")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    response.data,
                    {
                        "code": 0,
                        "message": "success",
                        "data": {"items": [{"id": "model-a"}], "total": 1},
                    },
                )
                self.post.assert_called_once_with(
                    "https://agentlens.invalid/api/v1/model-connections/discover",
                    headers={
                        "x-antmonitor-token": "fixture-agentlens-token",
                        "Content-Type": "application/json",
                    },
                    json={**self.payload, "space_uuid": self.space.uuid},
                    timeout=25,
                    allow_redirects=False,
                )
                self.get.assert_not_called()

    def test_member_cannot_trigger_provider_requests(self):
        self.client.force_authenticate(user=self.member)
        response = self.client.post(self.url, self.payload, format="json")
        self.assertEqual(response.status_code, 403)
        self.post.assert_not_called()
        self.get.assert_not_called()

    def test_user_cannot_discover_in_a_space_without_membership(self):
        url = f"/api/v1/spaces/{self.other_space.uuid}/diagnose/model-connections/discover"
        response = self.client.post(url, self.payload, format="json")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["data"]["error_key"], "SPACE_NOT_FOUND")
        self.post.assert_not_called()
        self.get.assert_not_called()

    def test_browser_cannot_override_space_or_add_unrelated_forward_fields(self):
        response = self.client.post(
            self.url,
            {
                **self.payload,
                "space_uuid": self.other_space.uuid,
                "operator": "other-user",
                "provider_url": "https://attacker.invalid",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.post.call_args.kwargs["json"],
            {
                **self.payload,
                "space_uuid": self.space.uuid,
            },
        )

    def test_saved_connection_is_checked_against_the_authorized_space(self):
        response = self.client.post(
            self.url,
            {
                "endpoint": self.payload["endpoint"],
                "connection_uuid": "connection-1",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.get.call_args.args[0],
            "https://agentlens.invalid/api/v1/model-connection/connection-1",
        )
        self.assertEqual(
            self.post.call_args.kwargs["json"],
            {
                "space_uuid": self.space.uuid,
                "endpoint": self.payload["endpoint"],
                "connection_uuid": "connection-1",
            },
        )

    def test_cross_space_saved_key_is_rejected_before_discovery(self):
        self.get.return_value = self.remote(
            {
                "uuid": "connection-1",
                "space_uuid": self.other_space.uuid,
            }
        )
        response = self.client.post(
            self.url,
            {
                "endpoint": self.payload["endpoint"],
                "connection_uuid": "connection-1",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["data"]["error_key"], "RESOURCE_NOT_IN_SPACE")
        self.get.assert_called_once()
        self.post.assert_not_called()

    def test_connection_references_are_scoped_before_fetching(self):
        references = {"items": [{"uuid": "diagnoser-1", "name": "Private diagnoser"}]}
        self.get.side_effect = [
            self.remote({"uuid": "connection-1", "space_uuid": self.space.uuid}),
            self.remote(references),
        ]

        response = self.client.get(
            f"/api/v1/spaces/{self.space.uuid}/diagnose/"
            "model-connection/connection-1/references"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["data"], references)
        self.assertEqual(
            [call.args[0] for call in self.get.call_args_list],
            [
                "https://agentlens.invalid/api/v1/model-connection/connection-1",
                "https://agentlens.invalid/api/v1/model-connection/connection-1/references",
            ],
        )

    def test_cross_space_connection_references_are_not_returned(self):
        self.get.return_value = self.remote(
            {"uuid": "connection-1", "space_uuid": self.other_space.uuid}
        )

        response = self.client.get(
            f"/api/v1/spaces/{self.space.uuid}/diagnose/"
            "model-connection/connection-1/references"
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["data"]["error_key"], "RESOURCE_NOT_IN_SPACE")
        self.get.assert_called_once()
        self.assertEqual(
            self.get.call_args.args[0],
            "https://agentlens.invalid/api/v1/model-connection/connection-1",
        )

    def test_connection_references_require_membership(self):
        response = self.client.get(
            f"/api/v1/spaces/{self.other_space.uuid}/diagnose/"
            "model-connection/connection-1/references"
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["data"]["error_key"], "SPACE_NOT_FOUND")
        self.get.assert_not_called()

    def test_invalid_request_types_and_connection_paths_never_reach_agentlens(self):
        for payload in (
            [],
            {"endpoint": 123},
            {"endpoint": ""},
            {
                **self.payload,
                "connection_uuid": "../connection-2?api_key=secret",
            },
            {**self.payload, "api_key": []},
        ):
            with self.subTest(payload=payload):
                response = self.client.post(self.url, payload, format="json")
                self.assertEqual(response.status_code, 400)
                self.assertEqual(
                    response.data["data"]["discovery_error"], "invalid_request"
                )
        self.get.assert_not_called()
        self.post.assert_not_called()

    def test_all_supported_discovery_reasons_survive_the_proxy(self):
        for reason in (
            "invalid_request",
            "unsupported_endpoint",
            "missing_api_key",
            "authentication_failed",
            "rate_limited",
            "unavailable",
            "invalid_response",
        ):
            with self.subTest(reason=reason):
                self.post.return_value = self.remote(
                    {
                        "code": "validation",
                        "msg": "safe upstream description",
                        "discovery_error": reason,
                    },
                    400,
                )
                response = self.client.post(self.url, self.payload, format="json")
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.data["data"]["discovery_error"], reason)

    def test_provider_auth_failure_cannot_trigger_platform_session_logout(self):
        self.post.return_value = self.remote(
            {
                "code": "external_unavailable",
                "msg": "provider rejected fixture-secret",
                "discovery_error": "authentication_failed",
            },
            401,
        )
        with self.assertNoLogs("apps.core.views.agentlens_proxy", level="WARNING"):
            response = self.client.post(self.url, self.payload, format="json")
        self.assertEqual(response.status_code, 502)
        self.assertEqual(
            response.data["data"]["discovery_error"], "authentication_failed"
        )
        self.assertNotIn("fixture-secret", str(response.data))

    def test_transport_errors_are_safe_and_have_a_stable_reason(self):
        for error in (requests.ReadTimeout, requests.ConnectionError):
            with self.subTest(error=error):
                self.post.side_effect = error("Authorization: Bearer fixture-secret")
                with self.assertNoLogs(
                    "apps.core.views.agentlens_proxy", level="WARNING"
                ):
                    response = self.client.post(self.url, self.payload, format="json")
                self.assertEqual(response.status_code, 502)
                self.assertEqual(
                    response.data["data"]["discovery_error"], "unavailable"
                )
                self.assertNotIn("fixture-secret", str(response.data))

    def test_malformed_success_does_not_leak_provider_response(self):
        for body in (
            [],
            {"items": "fixture-secret"},
            {"items": [{"id": 42}], "total": 1},
        ):
            with self.subTest(body=body):
                self.post.return_value = self.remote(body)
                response = self.client.post(self.url, self.payload, format="json")
                self.assertEqual(response.status_code, 502)
                self.assertEqual(
                    response.data["data"]["discovery_error"], "invalid_response"
                )
                self.assertNotIn("fixture-secret", str(response.data))

    def test_unknown_error_shapes_are_safe_and_validation_errors_are_actionable(self):
        for body, reason in (
            ({"code": "validation"}, "invalid_request"),
            ({"discovery_error": []}, "unavailable"),
            ([], "unavailable"),
        ):
            with self.subTest(body=body):
                self.post.return_value = self.remote(body, 422)
                response = self.client.post(self.url, self.payload, format="json")
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.data["data"]["discovery_error"], reason)

    @patch("apps.core.views.agentlens_proxy.http_requests.patch")
    def test_edit_missing_key_error_keeps_its_localizable_reason(
        self, patch_connection
    ):
        patch_connection.return_value = self.remote(
            {
                "code": "validation",
                "msg": "Enter an API key for this endpoint.",
                "discovery_error": "missing_api_key",
            },
            422,
        )
        response = self.client.patch(
            f"/api/v1/spaces/{self.space.uuid}/diagnose/model-connection/connection-1",
            {"endpoint": "https://changed.example/v1"},
            format="json",
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.data["data"].get("discovery_error"), "missing_api_key"
        )
