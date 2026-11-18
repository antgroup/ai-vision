from urllib.parse import parse_qs, urlparse

from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import AgentTypeConfig, DataSource, ObservationSubjectBinding, Space
from apps.core.tests.helpers import (
    create_local_user,
    create_test_agent,
    create_test_space_member,
)
from apps.core.utils.share_signing import parse_and_verify_share
from apps.core.views.share import GenerateShareLinkView


@override_settings(SHARE_LINK_SIGNING_KEY="test-personal-share-key")
class PersonalShareLinkTests(TestCase):
    def setUp(self):
        self.user = create_local_user("share-owner")
        self.space = Space.objects.create(name="Personal", type=1, creator=self.user)
        create_test_space_member(space=self.space, user=self.user, role=1)
        agent_type = AgentTypeConfig.objects.create(
            agent_name="codex", agent_product="codex", runtime_type="codex"
        )
        self.agent = create_test_agent(
            space=self.space, agent_type=agent_type, agent_product="codex", name="Codex"
        )
        binding = self.agent.observation_subject_binding
        binding.subject_id = "collector-subject"
        binding.save(update_fields=["subject_id"])

    def generate(self, resource_type="trace", protocol="signoz", user=None):
        request = APIRequestFactory().post(
            "/share-links/generate",
            {
                "resource_type": resource_type,
                "resource_id": "resource-1",
                "agent_product": "codex",
                "protocol": protocol,
            },
            format="json",
        )
        force_authenticate(request, user=user or self.user)
        return GenerateShareLinkView.as_view()(request, space_id=self.space.uuid)

    def claims(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["code"], 0)
        query = parse_qs(urlparse(response.data["data"]["url"]).query)
        return parse_and_verify_share(query["payload"][0], query["sig"][0])

    def test_personal_trace_and_session_use_agent_collector_identity(self):
        for resource_type in ("trace", "session"):
            with self.subTest(resource_type=resource_type):
                claims = self.claims(self.generate(resource_type))
                self.assertEqual(claims["sub"], "collector-subject")
                self.assertEqual(claims["t"], resource_type)
                self.assertEqual(claims["s"], self.space.id)

    def test_pending_agent_can_share_existing_data(self):
        self.agent.status = 2
        self.agent.save(update_fields=["status"])
        self.assertEqual(self.claims(self.generate())["sub"], "collector-subject")

    def test_invalid_agent_binding_cannot_generate_link(self):
        binding = self.agent.observation_subject_binding
        binding.status = 0
        binding.save(update_fields=["status"])
        response = self.generate()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["message"], "SUBJECT_BINDING_MISSING")

    def test_langfuse_keeps_its_own_identity(self):
        DataSource.objects.create(
            agent_type=self.agent.agent_type, datasource_type="traces",
            source_system="langfuse", enabled=1, is_default=1,
        )
        ObservationSubjectBinding.objects.create(
            space=self.space, user=self.user, source_system="langfuse",
            subject_id="langfuse-subject", binding_source="test", created_by=self.user,
        )
        self.assertEqual(
            self.claims(self.generate(protocol="lf"))["sub"], "langfuse-subject"
        )

    def test_nonmember_cannot_generate_link(self):
        response = self.generate(user=create_local_user("outsider"))
        self.assertEqual(response.data["code"], 3002)

    def test_business_space_does_not_require_personal_binding(self):
        self.space.type = 2
        self.space.save(update_fields=["type"])
        self.assertEqual(self.claims(self.generate())["sub"], self.user.uuid)
