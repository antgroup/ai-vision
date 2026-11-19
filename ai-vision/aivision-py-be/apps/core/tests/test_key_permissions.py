"""Permission regressions for space query keys and observation credentials."""

from unittest.mock import patch

from rest_framework.test import APIClient

from apps.core.models import (
    ObservationEndpointConfig,
    Space,
    SpaceKey,
    SpaceMember,
    SpaceObservationCredential,
    SpaceObservationProject,
    User,
)
from apps.core.tests.helpers import (
    create_test_space_member,
    create_local_user,
    SpaceKeyTestCase,
)


class KeyPermissionTests(SpaceKeyTestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.admin = create_local_user("admin-1", name="Admin")
        self.developer = create_local_user("developer-1", name="Developer")
        self.member = create_local_user("member-1", name="Member")
        self.space = Space.objects.create(
            name="Permission Space",
            type=2,
            scope=1,
            creator=self.admin,
            status=1,
        )
        create_test_space_member(space=self.space, user=self.admin, role=1)
        create_test_space_member(space=self.space, user=self.developer, role=2)
        create_test_space_member(space=self.space, user=self.member, role=3)

        self.space_key = SpaceKey.objects.create(
            space=self.space,
            key_prefix="sk-aiv-existing",
            key_hash="a" * 64,
            name="Existing Key",
            creator=self.admin,
        )
        endpoint = ObservationEndpointConfig.objects.create(
            name="Test Endpoint",
            base_url="https://example.test",
            tenant_name="test-tenant",
            status=1,
        )
        project = SpaceObservationProject.objects.create(
            space=self.space,
            endpoint_config=endpoint,
            tenant_name="test-tenant",
            status=1,
            provision_status=1,
        )
        self.credential = SpaceObservationCredential.objects.create(
            space_observation_project=project,
            scope="READ",
            public_key="pk-test",
            secret_hash="hash",
            secret_ciphertext="ciphertext",
            status=1,
        )

        self.list_url = f"/api/v1/spaces/{self.space.uuid}/space-keys"
        self.detail_url = f"{self.list_url}/{self.space_key.id}"
        self.reveal_url = (
            f"/api/v1/spaces/{self.space.uuid}/credentials/"
            f"{self.credential.id}/reveal"
        )
        self.regenerate_url = (
            f"/api/v1/spaces/{self.space.uuid}/credentials/"
            f"{self.credential.id}/regenerate"
        )

    def authenticate(self, user):
        self.client.force_authenticate(user=user)

    def test_non_admins_can_only_list_masked_space_keys(self):
        for user, expected_role in ((self.developer, 2), (self.member, 3)):
            with self.subTest(role=expected_role):
                self.authenticate(user)
                response = self.client.get(self.list_url)

                self.assertEqual(response.status_code, 200)
                body = response.json()
                self.assertEqual(body["code"], 0)
                self.assertEqual(body["data"]["role"], expected_role)
                item = body["data"]["items"][0]
                self.assertEqual(item["key_prefix"], self.space_key.key_prefix)
                self.assertNotIn("key", item)
                self.assertNotIn("key_hash", item)

    def test_non_admins_cannot_manage_space_keys(self):
        for user in (self.developer, self.member):
            with self.subTest(user=user.uuid):
                self.authenticate(user)

                toggle_response = self.client.put(
                    self.detail_url, {"enabled": False}, format="json"
                )
                delete_response = self.client.delete(self.detail_url)

                for response in (toggle_response, delete_response):
                    self.assertEqual(response.status_code, 403)
                    self.assertEqual(response.json()["code"], 4003)
                    self.assertEqual(
                        response.json()["data"]["error_key"],
                        "SPACE_PERMISSION_DENIED",
                    )

                self.space_key.refresh_from_db()
                self.assertTrue(self.space_key.enabled)
                self.assertIsNone(self.space_key.gmt_deleted)
                self.assertEqual(SpaceKey.objects.filter(space=self.space).count(), 1)

    def test_developer_can_create_space_key_for_ingest(self):
        self.authenticate(self.developer)

        created = self.client.post(
            self.list_url, {"name": "Agent Framework Used"}, format="json"
        )

        self.assertEqual(created.status_code, 200, created.content)
        self.assertEqual(created.json()["data"]["key"].startswith("sk-aiv-"), True)
        self.assertEqual(SpaceKey.objects.filter(space=self.space).count(), 2)

        self.authenticate(self.member)
        denied = self.client.post(self.list_url, {"name": "Member"}, format="json")
        self.assertEqual(denied.status_code, 403)

    def test_non_admins_cannot_reveal_or_regenerate_credentials(self):
        for user in (self.developer, self.member):
            with self.subTest(user=user.uuid):
                self.authenticate(user)

                reveal_response = self.client.post(self.reveal_url)
                regenerate_response = self.client.post(self.regenerate_url)

                for response in (reveal_response, regenerate_response):
                    self.assertEqual(response.status_code, 403)
                    self.assertEqual(response.json()["code"], 3020)
                    self.assertIsNone(response.json()["data"])

    @patch("apps.core.services.credential_service.reveal_credential")
    def test_admin_can_reveal_credentials(self, mock_reveal):
        mock_reveal.return_value = {
            "public_key": "pk-test",
            "secret_key": "secret-test",
            "scope": "READ",
        }
        self.authenticate(self.admin)

        response = self.client.post(self.reveal_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["code"], 0)
        self.assertEqual(response.json()["data"]["secret_key"], "secret-test")
        mock_reveal.assert_called_once_with(self.credential)

    @patch("apps.core.services.credential_service.regenerate_credential")
    def test_admin_regeneration_failure_keeps_provider_details_private(self, regenerate):
        provider_detail = "fixture-only regeneration diagnostic"
        regenerate.side_effect = RuntimeError(provider_detail)
        self.authenticate(self.admin)

        with self.assertLogs("apps.core.views.credential", level="WARNING") as logs:
            response = self.client.post(self.regenerate_url)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], 3022)
        self.assertEqual(
            response.json()["message"],
            "无法重新生成凭证，请检查观测服务配置后重试",
        )
        self.assertNotContains(response, provider_detail, status_code=400)
        self.assertIn("RuntimeError", "\n".join(logs.output))
        self.assertNotIn(provider_detail, "\n".join(logs.output))
        regenerate.assert_called_once_with(self.credential, None)
