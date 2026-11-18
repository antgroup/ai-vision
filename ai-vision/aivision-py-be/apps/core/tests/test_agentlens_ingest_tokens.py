from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.core.models import Agent, PlatformConfig, Space, SpaceKey
from apps.core.services.ingest_token import verify_ingest_token
from apps.core.tests.helpers import create_local_user, create_test_agent


@override_settings(
    AGENTLENS_TOKEN="service-secret-for-test",
    AIVISION_INGEST_SECRET="signing-secret-for-test",
    AIVISION_INGEST_ORG_ID="org-test",
    AIVISION_INGEST_TOKEN_TTL_SECONDS=900,
)
class AgentLensIngestTokenTests(TestCase):
    url = "/api/internal/agentlens/ingest-tokens"

    def setUp(self):
        self.user = create_local_user("ingest-token-test")
        self.spaces = [
            Space.objects.create(name=f"Scope {kind}", type=kind, creator=self.user)
            for kind in (1, 2)
        ]
        self.agents = [
            create_test_agent(space=space, agent_product="Codex", name="Codex")
            for space in self.spaces
        ]
        self.client = APIClient()

    def mint(self, space=None, agent=None, token="service-secret-for-test", **extra):
        return self.client.post(
            self.url,
            {
                "space_uuid": (space or self.spaces[0]).uuid,
                "agent_uuid": (agent or self.agents[0]).uuid,
                **extra,
            },
            format="json",
            HTTP_X_ANTMONITOR_TOKEN=token,
        )

    def test_service_mints_scoped_tokens_without_space_keys(self):
        for space, agent in zip(self.spaces, self.agents, strict=True):
            response = self.mint(
                space, agent, subject_id="spoofed", agent_product="spoofed"
            )
            self.assertEqual(response.status_code, 200, response.data)
            data = response.data["data"]
            claims = verify_ingest_token(data["token"])
            self.assertEqual(claims["sp"], space.uuid)
            self.assertEqual(claims["aid"], agent.uuid)
            self.assertEqual(claims["u"], self.user.uuid)
            self.assertEqual(claims["ap"], "Codex")
            self.assertEqual(claims["kid"], "agentlens")
            self.assertEqual(claims["org"], "org-test")
            self.assertEqual(claims["exp"] - claims["iat"], 300)
            self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(SpaceKey.objects.count(), 0)

    def test_org_claim_falls_back_to_bootstrapped_integration_org(self):
        # With no env override, the org stamped into the token is the system-
        # produced org persisted by bootstrap_signoz_integration (never hardcoded).
        PlatformConfig.objects.update_or_create(
            platform_code="signoz_integration",
            defaults={
                "platform_name": "SigNoz Integration",
                "config": {"configured": True, "organization_id": "org-bootstrapped"},
                "status": 1,
            },
        )
        with override_settings(AIVISION_INGEST_ORG_ID=""):
            response = self.mint()
        self.assertEqual(response.status_code, 200, response.data)
        claims = verify_ingest_token(response.data["data"]["token"])
        self.assertEqual(claims["org"], "org-bootstrapped")

    def test_session_missing_or_wrong_service_secret_does_not_authorize_mint(self):
        self.client.force_authenticate(user=self.user)
        for token in ("", "incorrect", "非 ASCII 密钥"):
            response = self.mint(token=token)
            self.assertEqual(response.status_code, 401)
            self.assertNotIn("service-secret-for-test", str(response.data))
        with override_settings(AGENTLENS_TOKEN=""):
            self.assertEqual(self.mint(token="").status_code, 401)

    def test_cross_space_and_removed_agents_are_rejected(self):
        self.assertEqual(self.mint(self.spaces[0], self.agents[1]).status_code, 404)
        Agent.objects.filter(pk=self.agents[0].pk).update(status=0)
        self.assertEqual(self.mint().status_code, 404)

    def test_revoked_subject_or_disabled_space_are_rejected(self):
        binding = self.agents[0].observation_subject_binding
        binding.status = 0
        binding.save()
        self.assertEqual(self.mint().status_code, 403)
        binding.status = 1
        binding.save()
        self.spaces[0].status = 0
        self.spaces[0].save()
        self.assertEqual(self.mint().status_code, 404)

    def test_binding_from_another_space_is_rejected(self):
        Agent.objects.filter(pk=self.agents[0].pk).update(
            observation_subject_binding=self.agents[1].observation_subject_binding
        )
        self.assertEqual(self.mint().status_code, 403)

    def test_invalid_scope_or_missing_signer_fail_closed(self):
        self.assertEqual(self.mint(agent_uuid=[]).status_code, 422)
        with override_settings(AIVISION_INGEST_SECRET=""):
            self.assertEqual(self.mint().status_code, 503)
