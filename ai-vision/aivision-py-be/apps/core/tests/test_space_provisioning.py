from unittest.mock import Mock, patch

import requests as http_requests
from django.contrib import admin as django_admin
from django.test import RequestFactory, TestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.admin import UserAdmin
from apps.core.tests.helpers import create_test_space_member, create_local_user

from apps.core.models import (
    Agent,
    AgentTypeConfig,
    ApiKey,
    ObservationEndpointConfig,
    Space,
    SpaceDatasourceBinding,
    SpaceMember,
    SpaceObservationCredential,
    SpaceObservationProject,
    User,
)
from apps.core.services.credential_service import (
    decrypt_secret,
    encrypt_secret,
    hash_secret,
)
from apps.core.services.space_observation_provisioning import (
    build_idempotency_key,
    build_project_name,
    provision_space_observation_project,
)
from apps.core.views import (
    SpaceAgentsCreateView,
)


@override_settings(
    LANGFUSE_INTERNAL_AUTH_VALUE="YWl2aXNpb246YW50bGFuZ2Z1c2U=",
    LANGFUSE_INTERNAL_API_TIMEOUT=5,
    CREDENTIAL_ENCRYPTION_KEY="test-credential-key",
)
class SpaceObservationProvisioningTests(TestCase):
    def setUp(self):
        self.user = create_local_user("100001", name="Tester", status=1)
        self.endpoint = ObservationEndpointConfig.objects.create(
            name="default-Langfuse",
            base_url="https://langfuse.example.com",
            tenant_name="tenant-a",
            backend_type="langfuse",
            is_default=1,
            status=1,
            config={
                "internal_api_base_url": "https://langfuse-internal.example.com",
                "organization_id": "org_xxx",
            },
        )
        self.space = Space.objects.create(
            name="Agent Ops",
            type=2,
            scope=1,
            creator=self.user,
            status=1,
        )
        create_test_space_member(space=self.space, user=self.user, role=1)
        self.codex_agent_type = AgentTypeConfig.objects.create(
            agent_name="CODEX",
            agent_product="codex",
            runtime_type="codex",
            display_name="Codex",
            display_scope="ALL",
            status=1,
        )
        self.claude_agent_type = AgentTypeConfig.objects.create(
            agent_name="CLAUDE_CODE",
            agent_product="claude-code",
            runtime_type="claudecode",
            display_name="Claude Code",
            display_scope="ALL",
            status=1,
        )

    @patch("apps.core.services.space_observation_provisioning.http_requests.post")
    def test_provision_business_space_successfully(self, mock_post):
        mock_response = Mock()
        mock_response.json.return_value = {
            "project": {
                "id": "proj_xxx",
                "name": build_project_name(self.space),
                "organizationId": "org_xxx",
            },
            "credentials": {
                "read": {
                    "id": "key_read_xxx",
                    "publicKey": "pk-read-xxx",
                    "secretKey": "sk-read-xxx",
                },
                "write": {
                    "id": "key_write_xxx",
                    "publicKey": "pk-write-xxx",
                    "secretKey": "sk-write-xxx",
                },
            },
            "idempotent": False,
        }
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        project = provision_space_observation_project(self.space)

        self.assertEqual(project.provision_status, 1)
        self.assertEqual(project.upstream_project_id, "proj_xxx")
        self.assertEqual(project.upstream_project_name, build_project_name(self.space))
        self.assertEqual(project.credentials.count(), 2)
        self.assertTrue(
            SpaceObservationCredential.objects.filter(
                space_observation_project=project,
                scope="READ",
                public_key="pk-read-xxx",
            ).exists()
        )
        self.assertTrue(
            SpaceObservationCredential.objects.filter(
                space_observation_project=project,
                scope="WRITE",
                public_key="pk-write-xxx",
            ).exists()
        )
        self.assertTrue(
            SpaceDatasourceBinding.objects.filter(
                space_observation_project=project,
                datasource_type="traces",
                source_system="langfuse",
                enabled=1,
                is_default=1,
                gmt_deleted__isnull=True,
            ).exists()
        )

        _, kwargs = mock_post.call_args
        self.assertEqual(
            kwargs["headers"]["X-AIVISION-AUTH"],
            "YWl2aXNpb246YW50bGFuZ2Z1c2U=",
        )
        self.assertEqual(
            kwargs["json"]["idempotencyKey"],
            build_idempotency_key(self.space, self.endpoint),
        )
        self.assertEqual(kwargs["json"]["organizationId"], "org_xxx")

    @patch("apps.core.services.space_observation_provisioning.http_requests.post")
    def test_provision_creates_default_trace_datasource_binding(self, mock_post):
        mock_response = Mock()
        mock_response.json.return_value = {
            "project": {
                "id": "proj_binding_xxx",
                "name": build_project_name(self.space),
                "organizationId": "org_xxx",
            },
            "credentials": {
                "read": {
                    "id": "key_read_binding_xxx",
                    "publicKey": "pk-read-binding",
                    "secretKey": "sk-read-binding",
                },
                "write": {
                    "id": "key_write_binding_xxx",
                    "publicKey": "pk-write-binding",
                    "secretKey": "sk-write-binding",
                },
            },
        }
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        project = provision_space_observation_project(self.space)

        binding = SpaceDatasourceBinding.objects.get(
            space_observation_project=project,
            datasource_type="traces",
        )
        self.assertEqual(binding.source_system, "langfuse")
        self.assertEqual(binding.enabled, 1)
        self.assertEqual(binding.is_default, 1)

    @patch("apps.core.services.space_observation_provisioning.http_requests.post")
    def test_provision_accepts_wrapped_success_response(self, mock_post):
        mock_response = Mock()
        mock_response.json.return_value = {
            "code": 0,
            "message": "success",
            "data": {
                "project": {
                    "id": "proj_wrapped_xxx",
                    "name": build_project_name(self.space),
                    "organizationId": "org_xxx",
                },
                "credentials": {
                    "read": {
                        "id": "key_read_wrapped_xxx",
                        "publicKey": "pk-read-wrapped",
                        "secretKey": "sk-read-wrapped",
                    },
                    "write": {
                        "id": "key_write_wrapped_xxx",
                        "publicKey": "pk-write-wrapped",
                        "secretKey": "sk-write-wrapped",
                    },
                },
            },
        }
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        project = provision_space_observation_project(self.space)

        self.assertEqual(project.provision_status, 1)
        self.assertEqual(project.upstream_project_id, "proj_wrapped_xxx")

    @patch("apps.core.services.space_observation_provisioning.http_requests.post")
    def test_provision_accepts_double_wrapped_success_response(self, mock_post):
        mock_response = Mock()
        mock_response.json.return_value = {
            "code": 0,
            "message": "success",
            "data": {
                "data": {
                    "project": {
                        "id": "proj_double_wrapped_xxx",
                        "name": build_project_name(self.space),
                        "organizationId": "org_xxx",
                    },
                    "credentials": {
                        "read": {
                            "id": "key_read_double_wrapped_xxx",
                            "publicKey": "pk-read-double-wrapped",
                            "secretKey": "sk-read-double-wrapped",
                        },
                        "write": {
                            "id": "key_write_double_wrapped_xxx",
                            "publicKey": "pk-write-double-wrapped",
                            "secretKey": "sk-write-double-wrapped",
                        },
                    },
                }
            },
        }
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        project = provision_space_observation_project(self.space)

        self.assertEqual(project.provision_status, 1)
        self.assertEqual(project.upstream_project_id, "proj_double_wrapped_xxx")

    @patch("apps.core.services.space_observation_provisioning.http_requests.get")
    @patch("apps.core.services.space_observation_provisioning.http_requests.post")
    def test_provision_falls_back_to_query_by_idempotency_key_on_timeout(
        self,
        mock_post,
        mock_get,
    ):
        mock_post.side_effect = http_requests.Timeout()

        mock_get_response = Mock()
        mock_get_response.json.return_value = {
            "found": True,
            "project": {
                "id": "proj_timeout_xxx",
                "name": build_project_name(self.space),
                "organizationId": "org_xxx",
            },
            "credentials": {
                "read": {
                    "id": "key_read_timeout_xxx",
                    "publicKey": "pk-read-timeout",
                    "secretKey": "sk-read-timeout",
                },
                "write": {
                    "id": "key_write_timeout_xxx",
                    "publicKey": "pk-write-timeout",
                    "secretKey": "sk-write-timeout",
                },
            },
        }
        mock_get_response.raise_for_status.return_value = None
        mock_get.return_value = mock_get_response

        project = provision_space_observation_project(self.space)

        self.assertEqual(project.provision_status, 1)
        self.assertEqual(project.upstream_project_id, "proj_timeout_xxx")
        _, kwargs = mock_get.call_args
        self.assertEqual(
            kwargs["params"]["idempotencyKey"],
            build_idempotency_key(self.space, self.endpoint),
        )

    def test_business_space_agent_create_does_not_generate_api_key(self):
        request = APIRequestFactory().post(
            f"/api/v1/spaces/{self.space.uuid}/agents",
            {"agent_product": "codex", "name": "Codex"},
            format="json",
        )
        force_authenticate(request, user=self.user)

        response = SpaceAgentsCreateView.as_view()(
            request, space_id=str(self.space.uuid)
        )

        self.assertEqual(response.status_code, 200)
        agent = Agent.objects.get(space=self.space, agent_product="codex")
        self.assertIsNone(agent.api_key_id)
        self.assertFalse(
            ApiKey.objects.filter(
                space=self.space, user=self.user, gmt_deleted__isnull=True
            ).exists()
        )

    def test_personal_space_agent_create_does_not_generate_api_key(self):
        personal_space = Space.objects.create(
            name="Tester的空间",
            type=1,
            scope=1,
            creator=self.user,
            status=1,
        )
        create_test_space_member(space=personal_space, user=self.user, role=1)

        request = APIRequestFactory().post(
            f"/api/v1/spaces/{personal_space.uuid}/agents",
            {"agent_product": "claude-code", "name": "Claude Code"},
            format="json",
        )
        force_authenticate(request, user=self.user)

        response = SpaceAgentsCreateView.as_view()(
            request, space_id=str(personal_space.uuid)
        )

        self.assertEqual(response.status_code, 200)
        agent = Agent.objects.get(space=personal_space, agent_product="claude-code")
        self.assertIsNone(agent.api_key_id)
        self.assertFalse(
            ApiKey.objects.filter(
                space=personal_space,
                user=self.user,
                gmt_deleted__isnull=True,
            ).exists()
        )
