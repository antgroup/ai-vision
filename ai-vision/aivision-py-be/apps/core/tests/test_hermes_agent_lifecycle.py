from django.test import TestCase
from rest_framework.test import APIClient

from apps.core.models import AgentTypeConfig, Space
from apps.core.tests.helpers import create_local_user, create_test_agent, create_test_space_member


class HermesAgentLifecycleTests(TestCase):
    def setUp(self):
        self.user = create_local_user("hermes-owner")
        self.space = Space.objects.create(name="Hermes", type=2, creator=self.user)
        create_test_space_member(space=self.space, user=self.user, role=1)
        self.config = AgentTypeConfig.objects.create(
            agent_name="Hermes", agent_product="Hermes", runtime_type="hermes",
            setup_mode="OTHER", category="CUSTOM", status=1,
            config_guide={"schema": "v2", "sections": [], "native_relay_onboarding": {
                "enabled": True, "integrations": ["hermes-relay"],
            }},
        )
        self.agent = create_test_agent(
            space=self.space, agent_type=self.config, agent_product="Hermes", name="Hermes",
            metadata={"owner": "team-a", "nested": {"keep": True}}, status=2,
        )
        self.url = f"/api/v1/spaces/{self.space.uuid}/agents/{self.agent.uuid}"
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_native_relay_agent_without_source_steps_can_be_removed_and_restored_in_team_space(self):
        self.config.config_guide = {
            "schema": "v2", "sections": [],
            "native_relay_onboarding": {"enabled": True, "integrations": ["hermes-relay"]},
        }
        self.config.save(update_fields=["config_guide"])
        path = self.url

        for action, expected_status in (("disable", 0), ("enable", 2)):
            response = self.client.post(path + "/" + action, {}, format="json")
            self.assertEqual(response.data["code"], 0, response.data)
            self.agent.refresh_from_db()
            self.assertEqual(self.agent.status, expected_status)
            self.assertEqual(self.agent.metadata, {
                "owner": "team-a", "nested": {"keep": True},
            })

    def test_empty_or_unrelated_native_guides_do_not_grant_team_lifecycle_controls(self):
        native = {"enabled": True, "integrations": ["hermes-relay"]}
        for product, config in (
            ("Other", native),
            ("Hermes", {}),
            ("Hermes", {"enabled": False, "integrations": ["hermes-relay"]}),
            ("Hermes", {"enabled": True, "integrations": None}),
            ("Hermes", {"enabled": True, "integrations": "hermes-relay"}),
        ):
            with self.subTest(product=product, native=config):
                self.config.agent_product = product
                self.config.config_guide = {
                    "schema": "v2", "sections": ([{"type": "setup_steps"}] if config.get("enabled") is False else []), "native_relay_onboarding": config,
                }
                self.config.save(update_fields=["agent_product", "config_guide"])
                for action, initial_status in (("disable", 2), ("enable", 0)):
                    self.agent.status = initial_status
                    self.agent.save(update_fields=["status"])
                    path = self.url
                    response = self.client.post(path + "/" + action, {}, format="json")
                    self.assertEqual(response.data["code"], 1010, response.data)
                    self.agent.refresh_from_db()
                    self.assertEqual(self.agent.status, initial_status)
