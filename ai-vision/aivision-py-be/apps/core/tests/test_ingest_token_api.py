from django.test import override_settings
from rest_framework.test import APIClient

from apps.core.models import (
    Agent,
    ObservationSubjectBinding,
    Space,
    SpaceKey,
    SpaceMember,
    User,
)
from apps.core.services.ingest_token import verify_ingest_token
from apps.core.tests.helpers import create_test_agent, SpaceKeyTestCase


@override_settings(
    AIVISION_INGEST_SECRET="test-ingest-secret",
    AIVISION_INGEST_TOKEN_TTL_SECONDS=900,
)
class IngestTokenAPITests(SpaceKeyTestCase):
    def setUp(self):
        super().setUp()
        self.admin = self._user("admin@example.com")
        self.developer = self._user("developer@example.com")
        self.member = self._user("member@example.com")
        self.space = Space.objects.create(
            name="Business", type=2, scope=1, creator=self.admin, status=1
        )
        for user, role in (
            (self.admin, 1),
            (self.developer, 2),
            (self.member, 3),
        ):
            SpaceMember.objects.create(space=self.space, user=user, role=role)
        self.binding = ObservationSubjectBinding.objects.create(
            space=self.space,
            user=self.developer,
            source_system="collector",
            subject_id="subject-developer",
            binding_source="test",
            created_by=self.admin,
        )
        self.agent = create_test_agent(
            space=self.space,
            agent_product="Codex",
            name="Codex",
            observation_subject_binding=self.binding,
            status=1,
        )
        self.raw_key, key_prefix, key_hash = SpaceKey.generate_key()
        self.space_key = SpaceKey.objects.create(
            space=self.space,
            key_prefix=key_prefix,
            key_hash=key_hash,
            key_alias="collector",
            creator=self.admin,
        )
        self.client = APIClient()

    @staticmethod
    def _user(email):
        return User.objects.create(
            name=email.split("@", 1)[0],
            email=email,
            normalized_email=email,
            status=1,
        )

    def test_product_api_allows_admin_and_developer_but_rejects_member(self):
        url = (
            f"/api/v1/spaces/{self.space.uuid}/space-keys/"
            f"{self.space_key.id}/ingest-token"
        )
        for user in (self.admin, self.developer):
            self.client.force_authenticate(user=user)
            response = self.client.post(
                url, {"agent_uuid": self.agent.uuid}, format="json"
            )
            self.assertEqual(response.status_code, 200, response.data)
            claims = verify_ingest_token(
                response.data["data"]["token"], secret=b"test-ingest-secret"
            )
            self.assertEqual(claims["sp"], self.space.uuid)
            self.assertEqual(claims["u"], self.binding.subject_id)
            self.assertEqual(claims["aid"], self.agent.uuid)

        self.client.force_authenticate(user=self.member)
        denied = self.client.post(url, {"agent_uuid": self.agent.uuid}, format="json")
        self.assertEqual(denied.status_code, 403)

    def test_openapi_uses_header_space_key_and_rejects_raw_key_in_body(self):
        response = self.client.post(
            "/openapi/v1/ingest-tokens",
            {"agent_uuid": self.agent.uuid},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {self.raw_key}",
        )
        self.assertEqual(response.status_code, 200, response.data)

        self.reset_key_cache()
        rejected = self.client.post(
            "/openapi/v1/ingest-tokens",
            {"space_key": self.raw_key, "agent_uuid": self.agent.uuid},
            format="json",
        )
        self.assertEqual(rejected.status_code, 401)

    def test_cross_space_agent_and_excessive_ttl_are_rejected(self):
        other = Space.objects.create(
            name="Other", type=2, scope=1, creator=self.admin, status=1
        )
        other_binding = ObservationSubjectBinding.objects.create(
            space=other,
            user=self.admin,
            source_system="collector",
            subject_id="other-subject",
            binding_source="test",
            created_by=self.admin,
        )
        other_agent = create_test_agent(
            space=other,
            agent_product="ClaudeCode",
            name="ClaudeCode",
            observation_subject_binding=other_binding,
            status=1,
        )
        headers = {"HTTP_AUTHORIZATION": f"Bearer {self.raw_key}"}
        cross_space = self.client.post(
            "/openapi/v1/ingest-tokens",
            {"agent_uuid": other_agent.uuid},
            format="json",
            **headers,
        )
        self.assertEqual(cross_space.status_code, 404)

        excessive = self.client.post(
            "/openapi/v1/ingest-tokens",
            {"agent_uuid": self.agent.uuid, "ttl_seconds": 901},
            format="json",
            **headers,
        )
        self.assertEqual(excessive.status_code, 422)
