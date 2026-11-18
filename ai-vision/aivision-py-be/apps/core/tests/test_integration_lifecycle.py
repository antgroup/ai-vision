from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.core.models import Agent, AgentTypeConfig, Space
from apps.core.tests.helpers import (
    create_local_user,
    create_test_agent,
    create_test_space_member,
)


class IntegrationLifecycleTests(TestCase):
    def setUp(self):
        self.user = create_local_user("integration-owner")
        self.space = Space.objects.create(
            name="Integration projects", type=2, creator=self.user
        )
        create_test_space_member(space=self.space, user=self.user, role=1)
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.create_url = f"/api/v1/spaces/{self.space.uuid}/agents/create"

    def config(self, product, kind=None, setup_mode="OTHER"):
        return AgentTypeConfig.objects.create(
            agent_name=product,
            agent_product=product,
            setup_mode=setup_mode,
            category="CUSTOM",
            config_guide={"integration": {"kind": kind}} if kind else {},
            status=1,
        )

    def agent(self, config, status=2):
        return create_test_agent(
            space=self.space,
            agent_type=config,
            agent_product=config.agent_product,
            name="Billing Assistant",
            custom_name="Billing Assistant",
            metadata={"framework": "langgraph", "owner": "billing"},
            status=status,
        )

    def endpoint(self, agent, action="mark-connected"):
        return f"/api/v1/spaces/{self.space.uuid}/agents/{agent.uuid}/{action}"

    def test_catalog_integrations_require_name_and_allow_multiple_pending_projects(self):
        for product, kind in (
            ("future-sdk", "observability_sdk"),
            ("future-runtime", "partner_runtime"),
        ):
            with self.subTest(product=product):
                # The integration contract determines project lifecycle even
                # when a future catalog entry omits a manual setup mode.
                config = self.config(product, kind, setup_mode="AUTO")
                missing_name = self.client.post(
                    self.create_url, {"agent_product": product}, format="json"
                )
                self.assertEqual(missing_name.data["code"], 1002, missing_name.data)
                self.assertFalse(Agent.objects.filter(agent_type=config).exists())
                ids = []
                for name in ("Billing", "Support", "Billing"):
                    response = self.client.post(
                        self.create_url,
                        {"agent_product": product, "name": name},
                        format="json",
                    )
                    self.assertEqual(response.data["code"], 0, response.data)
                    ids.append(response.data["data"]["agent_uuid"])
                self.assertEqual(len(set(ids)), 3)
                self.assertEqual(
                    list(
                        Agent.objects.filter(agent_type=config).values_list(
                            "status", flat=True
                        )
                    ),
                    [2, 2, 2],
                )

    def test_v2_agent_list_restores_saved_project_framework_metadata(self):
        expected = {}
        for product, framework in (
            ("langfuse", "langgraph-js"),
            ("nvidia-nemo-relay", "nodejs"),
        ):
            self.config(product)
            response = self.client.post(
                self.create_url,
                {
                    "agent_product": product,
                    "name": "JavaScript project",
                    "metadata": {"framework": framework, "owner": "billing"},
                },
                format="json",
            )
            self.assertEqual(response.data["code"], 0, response.data)
            expected[response.data["data"]["agent_uuid"]] = {
                "framework": framework,
                "owner": "billing",
            }

        response = self.client.get(f"/api/v2/spaces/{self.space.uuid}/agents")
        self.assertEqual(response.data["code"], 0, response.data)
        actual = {
            agent["uuid"]: agent.get("metadata")
            for agent in response.data["data"]["agents"]
        }
        self.assertEqual(actual, expected)

    def test_legacy_relay_allows_multiple_projects_and_trace_confirmation(self):
        config = self.config("nvidia-nemo-relay")
        first = self.client.post(
            self.create_url,
            {"agent_product": config.agent_product, "name": "First"},
            format="json",
        )
        second = self.client.post(
            self.create_url,
            {"agent_product": config.agent_product, "name": "Second"},
            format="json",
        )
        self.assertEqual(first.data["code"], 0, first.data)
        self.assertEqual(second.data["code"], 0, second.data)
        self.assertNotEqual(
            first.data["data"]["agent_uuid"], second.data["data"]["agent_uuid"]
        )
        agent = Agent.objects.get(uuid=second.data["data"]["agent_uuid"])
        response = self.client.post(self.endpoint(agent), {}, format="json")
        self.assertEqual(response.data["code"], 0, response.data)
        agent.refresh_from_db()
        self.assertEqual(agent.status, 1)
        agent.status = 0
        agent.save(update_fields=["status", "gmt_modified"])
        third = self.client.post(
            self.create_url,
            {"agent_product": config.agent_product, "name": "Second"},
            format="json",
        )
        self.assertEqual(third.data["code"], 0, third.data)
        self.assertNotEqual(third.data["data"]["agent_uuid"], agent.uuid)
        agent.refresh_from_db()
        self.assertEqual(agent.status, 0)

    def test_confirmation_is_idempotent_and_keeps_existing_metadata(self):
        for product, kind in (
            ("langfuse", None),
            ("nvidia-nemo-relay", None),
            ("future-sdk", "observability_sdk"),
            ("future-runtime", "partner_runtime"),
        ):
            with self.subTest(product=product):
                agent = self.agent(self.config(product, kind))
                first = self.client.post(self.endpoint(agent), {}, format="json")
                self.assertEqual(first.data["code"], 0, first.data)
                agent.refresh_from_db()
                modified = agent.gmt_modified
                second = self.client.post(self.endpoint(agent), {}, format="json")
                self.assertEqual(second.data["code"], 0, second.data)
                agent.refresh_from_db()
                self.assertEqual(agent.status, 1)
                self.assertEqual(
                    agent.metadata, {"framework": "langgraph", "owner": "billing"}
                )
                self.assertEqual(agent.gmt_modified, modified)

    def test_explicit_metadata_update_merges_for_connected_integration(self):
        agent = self.agent(self.config("future-sdk", "observability_sdk"), status=1)
        response = self.client.post(
            self.endpoint(agent), {"metadata": {"framework": "langchain"}}, format="json"
        )
        self.assertEqual(response.data["code"], 0, response.data)
        agent.refresh_from_db()
        self.assertEqual(agent.metadata, {"framework": "langchain", "owner": "billing"})

    def test_confirmation_rejects_disabled_integration_and_nonintegration_plugin(self):
        disabled = self.agent(self.config("future-sdk", "observability_sdk"), status=0)
        plugin = self.agent(self.config("Codex", setup_mode="CLI"))
        for agent, code in ((disabled, 1013), (plugin, 1012)):
            with self.subTest(product=agent.agent_product):
                previous_status = agent.status
                response = self.client.post(self.endpoint(agent), {}, format="json")
                self.assertEqual(response.data["code"], code, response.data)
                agent.refresh_from_db()
                self.assertEqual(agent.status, previous_status)

    def test_team_integrations_can_be_removed_and_restored_to_pending(self):
        for product, kind in (
            ("nvidia-nemo-relay", None),
            ("future-sdk", "observability_sdk"),
        ):
            with self.subTest(product=product):
                agent = self.agent(self.config(product, kind, setup_mode="AUTO"), status=1)
                removed = self.client.post(
                    self.endpoint(agent, "disable"), {}, format="json"
                )
                self.assertEqual(removed.data["code"], 0, removed.data)
                restored = self.client.post(
                    self.endpoint(agent, "enable"), {}, format="json"
                )
                self.assertEqual(restored.data["code"], 0, restored.data)
                agent.refresh_from_db()
                self.assertEqual(agent.status, 2)

    def test_team_source_plugins_can_restore_but_cannot_use_trace_confirmation(self):
        # Hermes now has a dedicated native Relay contract, covered separately.
        # Keep this case focused on the generic manual source-plugin lifecycle.
        config = self.config("future-source-plugin")
        config.config_guide = {
            "schema": "v2",
            "sections": [{"type": "setup_steps", "steps": []}],
        }
        config.save(update_fields=["config_guide"])
        agent = self.agent(config, status=1)
        removed = self.client.post(self.endpoint(agent, "disable"), {}, format="json")
        self.assertEqual(removed.data["code"], 0, removed.data)
        restored = self.client.post(self.endpoint(agent, "enable"), {}, format="json")
        self.assertEqual(restored.data["code"], 0, restored.data)
        agent.refresh_from_db()
        self.assertEqual(agent.status, 2)
        confirmed = self.client.post(self.endpoint(agent), {}, format="json")
        self.assertEqual(confirmed.data["code"], 1012, confirmed.data)
        token = self.client.post(
            self.endpoint(agent, "installation-tokens"), {}, format="json"
        )
        self.assertEqual(token.status_code, 404, token.data)
        agent.refresh_from_db()
        self.assertEqual(agent.status, 2)

    def test_team_manual_origin_does_not_include_builtin_or_empty_guides(self):
        for product, category, sections in (
            ("builtin", "BUILT_IN", [{"type": "setup_steps"}]),
            ("unconfigured", "CUSTOM", []),
            ("CUSTOM", "CUSTOM", [{"type": "setup_steps"}]),
        ):
            with self.subTest(product=product):
                config = self.config(product)
                config.category = category
                config.config_guide = {"schema": "v2", "sections": sections}
                config.save(update_fields=["category", "config_guide"])
                agent = self.agent(config, status=1)
                removed = self.client.post(
                    self.endpoint(agent, "disable"), {}, format="json"
                )
                self.assertEqual(removed.data["code"], 1010, removed.data)
                agent.refresh_from_db()
                self.assertEqual(agent.status, 1)

    def test_confirmation_preserves_management_permission_and_space_scope(self):
        agent = self.agent(self.config("future-sdk", "observability_sdk"))
        observer = create_local_user("integration-observer")
        create_test_space_member(space=self.space, user=observer, role=3)
        self.client.force_authenticate(user=observer)
        response = self.client.post(self.endpoint(agent), {}, format="json")
        self.assertEqual(response.status_code, 403, response.data)
        self.client.force_authenticate(user=self.user)
        other_space = Space.objects.create(name="Other", type=2, creator=self.user)
        create_test_space_member(space=other_space, user=self.user, role=1)
        response = self.client.post(
            f"/api/v1/spaces/{other_space.uuid}/agents/{agent.uuid}/mark-connected",
            {},
            format="json",
        )
        self.assertEqual(response.data["code"], 1006, response.data)
        agent.refresh_from_db()
        self.assertEqual(agent.status, 2)

    @override_settings(AIVISION_PUBLIC_BASE_URL="")
    @patch.dict("os.environ", {"SIGNOZ_API_KEY": "test-placeholder"})
    def test_catalog_describes_runtime_as_framework_integration(self):
        call_command(
            "configure_third_party_agents",
            endpoint="http://query.test",
            stdout=StringIO(),
        )
        relay = AgentTypeConfig.objects.get(agent_product="nvidia-nemo-relay")
        self.assertIn("框架", relay.description)
        self.assertNotIn("安装采集插件", relay.description)
        langfuse = AgentTypeConfig.objects.get(agent_product="langfuse")
        self.assertIn("SDK", langfuse.description)
        codex = AgentTypeConfig.objects.get(agent_product="Codex")
        self.assertIn("安装采集插件", codex.description)
