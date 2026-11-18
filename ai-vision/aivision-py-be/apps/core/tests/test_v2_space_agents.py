"""V2SpaceAgentsView tests.

Covers:
  - Agents returned with merged AgentTypeConfig metadata
  - Empty agent list returns empty agents + full agent_types catalog
  - Non-member gets permission denied
  - Space not found returns 3001
  - Pending Agents remain available in Agent listings
  - Response envelope structure
"""

from django.test import TestCase
from rest_framework.test import APIClient

from apps.core.tests.helpers import (
    create_test_space_member,
    create_test_agent,
    create_local_user,
)

from apps.core.models import (
    Agent,
    AgentTypeConfig,
    Space,
    SpaceKey,
    SpaceMember,
    User,
)


class V2SpaceAgentsViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = create_local_user("u100", name="Space User", status=1)
        self.space = Space.objects.create(
            name="Test Space", type=2, scope=1, creator=self.user, status=1
        )
        create_test_space_member(space=self.space, user=self.user, role=1)

        self.atc_claude = AgentTypeConfig.objects.create(
            agent_name="ANT_CLAUDE_CODE",
            agent_product="AntCC",
            category="BUILT_IN",
            description="Claude Code agent",
            icon="claude.svg",
            setup_mode="CLI",
            sort_order=1,
            status=1,
        )
        self.atc_codex = AgentTypeConfig.objects.create(
            agent_name="CODEX",
            agent_product="Codex",
            category="BUILT_IN",
            description="Codex agent",
            icon="codex.svg",
            setup_mode="AUTO",
            cli_command=(
                "curl install.sh | bash -s -- --space-key <key> "
                "--agent-uuid {agent_uuid} plugin install cdx"
            ),
            sort_order=2,
            status=1,
        )
        self.atc_disabled = AgentTypeConfig.objects.create(
            agent_name="DISABLED_AGENT",
            agent_product="DisabledAgent",
            category="CUSTOM",
            status=0,
        )

        self.agent1 = create_test_agent(
            space=self.space,
            agent_product="ANT_CLAUDE_CODE",
            name="claude-code-1",
            custom_name="My Claude",
            status=1,
        )
        self.agent2 = create_test_agent(
            space=self.space,
            agent_product="CODEX",
            name="codex-1",
            status=1,
        )

        self.url = f"/api/v2/spaces/{self.space.uuid}/agents"

    def test_returns_agents_with_merged_metadata(self):
        self.client.force_authenticate(user=self.user)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)

        data = resp.json()["data"]
        agents = data["agents"]
        self.assertEqual(len(agents), 2)

        claude = next(a for a in agents if a["name"] == "claude-code-1")
        self.assertEqual(claude["uuid"], self.agent1.uuid)
        self.assertEqual(claude["agent_product"], "AntCC")
        self.assertEqual(claude["category"], "BUILT_IN")
        self.assertEqual(claude["icon"], "claude.svg")
        self.assertEqual(claude["description"], "Claude Code agent")
        self.assertEqual(claude["custom_name"], "My Claude")
        self.assertEqual(claude["status"], 1)

    def test_returns_agent_types_catalog(self):
        self.client.force_authenticate(user=self.user)
        resp = self.client.get(self.url)
        data = resp.json()["data"]

        agent_types = data["agent_types"]
        type_products = {t["agent_product"] for t in agent_types}
        self.assertIn("AntCC", type_products)
        self.assertIn("Codex", type_products)
        self.assertNotIn("DisabledAgent", type_products)

        codex = next(t for t in agent_types if t["agent_product"] == "Codex")
        self.assertIn(f"--agent-uuid {self.agent2.uuid}", codex["cli_command"])
        self.assertNotIn("{agent_uuid}", codex["cli_command"])

    def test_legacy_cli_command_receives_agent_uuid(self):
        self.atc_codex.cli_command = (
            "curl install.sh | bash -s -- --space-key <key> plugin install cdx"
        )
        self.atc_codex.save(update_fields=["cli_command"])
        self.client.force_authenticate(user=self.user)

        response = self.client.get(self.url)

        codex = next(
            item
            for item in response.json()["data"]["agent_types"]
            if item["agent_product"] == "Codex"
        )
        self.assertIn(f"--agent-uuid {self.agent2.uuid}", codex["cli_command"])

    def test_openapi_agents_list_uses_shared_fallback_only(self):
        raw_key, key_prefix, key_hash = SpaceKey.generate_key()
        SpaceKey.objects.create(
            space=self.space,
            name="openapi agents",
            key_prefix=key_prefix,
            key_hash=key_hash,
            creator=self.user,
            enabled=True,
        )
        platform_config = AgentTypeConfig.objects.create(
            agent_name="OPENAPI_PRIVATE_ALIAS",
            agent_product="OpenAPIPrivateProduct",
            display_name="OpenAPI Private",
            status=1,
        )
        create_test_agent(
            space=self.space,
            agent_type=None,
            agent_product="OPENAPI_PRIVATE_ALIAS",
            name="openapi-legacy-private",
            status=1,
        )
        create_test_agent(
            space=self.space,
            agent_type=platform_config,
            agent_product=platform_config.agent_product,
            name="openapi-bound-private",
            status=1,
        )

        resp = self.client.get(
            f"/openapi/v1/spaces/{self.space.uuid}/agents",
            HTTP_AUTHORIZATION=f"Bearer {raw_key}",
        )
        self.assertEqual(resp.status_code, 200)
        agents = resp.json()["data"]

        self.assertFalse(
            any(item["agent_product"] == "OPENAPI_PRIVATE_ALIAS" for item in agents)
        )

        bound = next(
            item for item in agents if item["agent_product"] == "OpenAPIPrivateProduct"
        )
        self.assertEqual(bound["agent_name"], "OPENAPI_PRIVATE_ALIAS")
        self.assertEqual(bound["display_name"], "OpenAPI Private")

    def test_empty_agents_returns_empty_list_with_types(self):
        empty_space = Space.objects.create(
            name="Empty Space", type=2, scope=1, creator=self.user, status=1
        )
        create_test_space_member(space=empty_space, user=self.user, role=1)
        url = f"/api/v2/spaces/{empty_space.uuid}/agents"

        self.client.force_authenticate(user=self.user)
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

        data = resp.json()["data"]
        self.assertEqual(data["agents"], [])
        self.assertGreater(len(data["agent_types"]), 0)

    def test_non_member_gets_permission_denied(self):
        other_user = create_local_user("u200", name="Outsider", status=1)
        self.client.force_authenticate(user=other_user)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 400)
        self.assertNotEqual(resp.json().get("code"), 0)

    def test_space_not_found(self):
        self.client.force_authenticate(user=self.user)
        resp = self.client.get("/api/v2/spaces/nonexistent-uuid/agents")
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertEqual(body["code"], 3001)

    def test_disabled_agents_excluded(self):
        create_test_agent(
            space=self.space,
            agent_product="ANT_CLAUDE_CODE",
            name="disabled-agent",
            status=0,
        )
        self.client.force_authenticate(user=self.user)
        resp = self.client.get(self.url)
        data = resp.json()["data"]

        names = [a["name"] for a in data["agents"]]
        self.assertNotIn("disabled-agent", names)
        self.assertEqual(len(data["agents"]), 2)

    def test_pending_agents_are_returned_by_agent_lists(self):
        create_test_agent(
            space=self.space,
            agent_product="CODEX",
            name="pending-agent",
            status=2,
        )
        self.client.force_authenticate(user=self.user)

        v2_response = self.client.get(self.url)
        v1_response = self.client.get(f"/api/v1/spaces/{self.space.uuid}/agents")

        self.assertEqual(v2_response.status_code, 200)
        self.assertEqual(v1_response.status_code, 200)
        self.assertIn(
            "pending-agent",
            [agent["name"] for agent in v2_response.json()["data"]["agents"]],
        )

    def test_reporting_status_refresh_endpoint_is_removed(self):
        self.client.force_authenticate(user=self.user)

        response = self.client.post(
            f"/api/v2/spaces/{self.space.uuid}/agents/reporting-status/refresh"
        )

        self.assertEqual(response.status_code, 404)

    def test_enable_cli_agent_restores_pending_status(self):
        personal_space = Space.objects.create(
            name="Personal Space", type=1, scope=1, creator=self.user, status=1
        )
        create_test_space_member(space=personal_space, user=self.user, role=1)
        agent = create_test_agent(
            space=personal_space,
            agent_type=self.atc_claude,
            agent_product=self.atc_claude.agent_product,
            name="removed-cli-agent",
            status=0,
        )
        url = f"/api/v1/spaces/{personal_space.uuid}/agents/{agent.uuid}/enable"

        self.client.force_authenticate(user=self.user)
        resp = self.client.post(url)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"]["status"], 2)
        agent.refresh_from_db()
        self.assertEqual(agent.status, 2)

    def test_response_envelope_structure(self):
        self.client.force_authenticate(user=self.user)
        resp = self.client.get(self.url)
        body = resp.json()

        self.assertEqual(body["code"], 0)
        self.assertIn("data", body)
        self.assertIn("agents", body["data"])
        self.assertIn("agent_types", body["data"])

        agent = body["data"]["agents"][0]
        expected_fields = {
            "id",
            "agent_name",
            "agent_product",
            "name",
            "custom_name",
            "category",
            "icon",
            "description",
            "status",
            "last_active_at",
        }
        self.assertTrue(expected_fields.issubset(set(agent.keys())))

        agent_type = body["data"]["agent_types"][0]
        expected_type_fields = {
            "agent_name",
            "agent_product",
            "category",
            "description",
            "icon",
            "setup_mode",
            "capabilities",
            "sort_order",
        }
        self.assertTrue(expected_type_fields.issubset(set(agent_type.keys())))

    def test_unauthenticated_request_rejected(self):
        resp = self.client.get(self.url)
        self.assertIn(resp.status_code, (401, 403))
