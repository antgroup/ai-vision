import hashlib
import json
from importlib import import_module
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import override_settings
from rest_framework.test import APIClient

from apps.core.models import (
    Agent, AgentTypeConfig, DataSource, ObservationSubjectBinding, Space, SpaceKey,
)
from apps.core.tests.helpers import (
    SpaceKeyTestCase,
    create_local_user,
    create_test_agent,
    create_test_space_member,
)
from apps.core.trace_query.routing import get_trace_datasource
from apps.core.trace_query.signoz.provider import SignozTraceProvider
from apps.core.trace_query.types import TraceListQuery


class ThirdPartyOnboardingTests(SpaceKeyTestCase):
    def setUp(self):
        super().setUp()
        self.user = create_local_user("onboard-user", name="Plugin Tester")
        self.space = Space.objects.create(
            name="Onboarding", type=1, scope=1, creator=self.user, status=1
        )
        create_test_space_member(space=self.space, user=self.user, role=1)
        self.kind = AgentTypeConfig.objects.create(
            agent_name="CODEX",
            agent_product="Codex",
            setup_mode="CLI",
            category="CUSTOM",
            config_guide={"schema": "v2", "plugin_key": "codex", "sections": []},
            status=1,
        )
        self.agent = create_test_agent(
            space=self.space,
            agent_type=self.kind,
            agent_product="Codex",
            name="Codex",
            status=2,
        )

    def test_cli_rename_migrates_saved_guides_without_replacing_agent_config(self):
        migration = import_module("apps.core.migrations.0025_rename_cli_to_aiv")
        historical_apps = MigrationExecutor(connection).loader.project_state(
            [("core", "0024_neutral_agent_category")]
        ).apps
        original_guide = {
            "schema": "v2",
            "plugin_key": "codex",
            "sections": [
                {"items": [{"value": "已安装 aivision-cli（aivforge）"}]},
                {"steps": [{"command": "aivforge connect codex --space-id '{spaceId}'"}]},
            ],
            "custom": {"enabled": True, "limit": 7, "command": "my-aivforge-wrapper"},
        }
        self.kind.config_guide = original_guide
        self.kind.cli_command = "aivforge connect codex --agent-uuid {agent_uuid}"
        self.kind.save()
        original_command = self.kind.cli_command
        original_modified = self.kind.gmt_modified

        schema_editor = connection.schema_editor()
        for _ in range(2):
            migration.forwards(historical_apps, schema_editor)
        self.kind.refresh_from_db()
        self.agent.refresh_from_db()
        self.assertEqual(
            self.kind.cli_command, "aiv connect codex --agent-uuid {agent_uuid}"
        )
        self.assertEqual(
            self.kind.config_guide["sections"][1]["steps"][0]["command"],
            "aiv connect codex --space-id '{spaceId}'",
        )
        self.assertEqual(
            self.kind.config_guide["sections"][0]["items"][0]["value"],
            "已安装 aivision-cli（aiv）",
        )
        self.assertEqual(self.kind.config_guide["custom"], original_guide["custom"])
        self.assertEqual(self.kind.agent_product, "Codex")
        self.assertEqual(self.kind.gmt_modified, original_modified)
        self.assertEqual(self.agent.agent_type_id, self.kind.pk)
        self.assertEqual(self.agent.status, 2)

        migration.backwards(historical_apps, schema_editor)
        self.kind.refresh_from_db()
        self.assertEqual(self.kind.cli_command, original_command)
        self.assertEqual(self.kind.config_guide, original_guide)

    @override_settings(AIVISION_PUBLIC_BASE_URL="")
    @patch.dict("os.environ", {"SIGNOZ_API_KEY": "test-placeholder"})
    def test_catalog_api_distinguishes_sdk_integrations_from_agent_plugins(self):
        call_command(
            "configure_third_party_agents",
            endpoint="http://query.test",
            stdout=StringIO(),
        )
        client = APIClient()
        client.force_authenticate(user=self.user)
        response = client.get("/api/v1/agent-types")
        self.assertEqual(response.status_code, 200)
        guides = {
            item["agent_product"]: item["config_guide"]
            for item in response.data["data"]
        }
        self.assertEqual(
            guides["langfuse"].get("integration"),
            {
                "kind": "observability_sdk",
                "onboardingPath": "/access/langfuse-onboarding",
                "docsUrl": "https://langfuse.com/docs/observability/sdk/overview",
                "verifiedVersions": [
                    "langfuse==3.15.0",
                    "langfuse==4.16.0",
                    "langfuse==5.0.2",
                    "@langfuse/*@5.14.0",
                    "@langfuse/*@6.0.0",
                ],
            },
        )
        self.assertEqual(
            guides["nvidia-nemo-relay"].get("integration"),
            {
                "kind": "partner_runtime",
                "onboardingPath": "/access/relay-onboarding",
                "docsUrl": "https://docs.nvidia.com/nemo/relay/about-nemo-relay/overview",
                "verifiedVersions": ["nemo-relay==0.9.3"],
            },
        )
        self.assertNotIn("integration", guides["Codex"])
        self.assertNotIn("langsmith", guides)

    @override_settings(AIVISION_PUBLIC_BASE_URL="")
    @patch.dict(
        "os.environ", {"SIGNOZ_API_KEY": "test-placeholder"}
    )
    def test_catalog_setup_reuses_existing_guides_and_keeps_history(self):
        previous_routes = [
            DataSource.objects.create(
                agent_type=self.kind,
                datasource_type=signal,
                source_system="stargazer_native",
                enabled=1,
                is_default=1,
                config={"endpoint": "http://existing-query.test"},
            )
            for signal in ("traces", "logs", "metrics")
        ]
        for _ in range(2):
            call_command(
                "configure_third_party_agents",
                endpoint="http://query.test",
                stdout=StringIO(),
            )
        catalog = AgentTypeConfig.objects.active_shared_runtime().filter(
            category="CUSTOM"
        )
        self.assertEqual(catalog.count(), 10)
        self.assertEqual(
            DataSource.objects.filter(
                agent_type__category="CUSTOM",
                source_system="signoz",
                enabled=1,
            ).count(),
            30,
        )
        self.agent.refresh_from_db()
        self.assertEqual(self.agent.status, 2)
        self.assertEqual(self.agent.agent_type_id, self.kind.id)
        for kind in catalog:
            guide = kind.config_guide
            if kind.setup_mode == "LANGFUSE":
                self.assertEqual(kind.agent_product, "langfuse")
                self.assertEqual(kind.runtime_type, "langfuse-sdk")
                # Each YAML under the catalog examples_dir is served unchanged.
                self.assertIsInstance(guide, dict)
                self.assertIn("frameworks", guide)
                frameworks = {item["id"]: item for item in guide["frameworks"]}
                self.assertTrue({"langchain", "langgraph", "langchain-deepagents", "eino"}.issubset(
                    frameworks
                ))
                for item in guide["frameworks"]:
                    with self.subTest(framework=item["id"]):
                        if item.get("language") == "go":
                            self.assertIsInstance(item.get("code"), str)
                            self.assertTrue(item["code"].strip())
                        else:
                            self.assertTrue(item.get("pipExample") or item.get("npmExample"))
                eino = frameworks["eino"]
                self.assertEqual(eino["frameworkId"], "eino")
                self.assertEqual(eino["language"], "go")
                self.assertEqual(eino["exampleFilename"], "main.go")
                self.assertEqual(eino["runCommand"], "go run main.go")
                self.assertIs(eino["requiresModel"], True)
                self.assertTrue(eino["code"].startswith("package main\n"))
                self.assertIn("github.com/cloudwego/eino-ext/callbacks/langfuse/v2", eino["code"])
                continue
            if kind.setup_mode == "OTHER":
                if kind.agent_product == "Hermes":
                    self.assertEqual(kind.runtime_type, "hermes")
                    self.assertEqual(guide["schema"], "v2")
                    self.assertNotIn("plugin_key", guide)
                    self.assertEqual(guide["sections"], [])
                    self.assertEqual(guide["native_relay_onboarding"]["integrations"], ["hermes-relay"])
                    continue
                self.assertEqual(kind.agent_product, "nvidia-nemo-relay")
                self.assertIn("frameworks", guide)
                self.assertEqual(
                    {item["id"] for item in guide["frameworks"]},
                    {"langchain-agent-middleware", "langgraph", "deep-agents", "nodejs"},
                )
                self.assertEqual(
                    sorted(
                        item["frontendSort"] for item in guide["frameworks"]
                    ),
                    [1, 2, 3, 4],
                )
                self.assertTrue(
                    all(item["id"] and item["label"]
                        for item in guide["frameworks"])
                )
                continue
            self.assertEqual(guide["schema"], "v2")
            steps = next(
                section
                for section in guide["sections"]
                if section["type"] == "setup_steps"
            )
            self.assertEqual(set(steps["platforms"]), {"macOS/Linux"})
            command = steps["platforms"]["macOS/Linux"]["modes"]["cli"]["steps"][0]["command"]
            self.assertIn(f"aiv connect {guide['plugin_key']}", command)
            self.assertIn("--base-url '{baseUrl}'", command)
            self.assertIn("--space-id '{spaceId}'", command)
            self.assertIn("--agent-uuid '{agentUuid}'", command)
            self.assertIn("--installation-token '{installationToken}'", command)
            self.assertNotIn("install.sh", command)
            self.assertNotIn("Invoke-WebRequest", command)
            self.assertNotIn("SpaceKey", str(guide))
            self.assertEqual(kind.category, "CUSTOM")
            self.assertIn(kind.display_scope, {"ALL", "HIDDEN"})
        for route in previous_routes:
            route.refresh_from_db()
            self.assertEqual(route.enabled, 1)
            self.assertEqual(route.is_default, 0)
            self.assertEqual(route.config, {"endpoint": "http://existing-query.test"})
        self.assertEqual(
            get_trace_datasource(self.space, agent_product="Codex").source_system,
            "signoz",
        )

    @override_settings(AIVISION_INGEST_SECRET="test-signing-secret-at-least-32-characters")
    @patch.dict("os.environ", {"SIGNOZ_API_KEY": "test-placeholder"})
    def test_minimax_code_preview_requires_a_served_artifact(self):
        with TemporaryDirectory() as temp, override_settings(
            AIVISION_PLUGIN_ARTIFACTS_DIR=temp
        ):
            call_command(
                "configure_third_party_agents", endpoint="http://query.test", stdout=StringIO()
            )
            self.assertFalse(
                AgentTypeConfig.objects.filter(agent_product="MiniMaxCode").exists()
            )

            plugin = Path(temp) / "minimax-code"
            version = "0.1.0"
            filename = f"observ-minimax-code-{version}.tgz"
            archive = b"preview artifact"
            (plugin / version).mkdir(parents=True)
            artifact = plugin / version / filename
            artifact.write_bytes(archive)
            (plugin / "latest.json").write_text(
                json.dumps(
                    {
                        "version": version,
                        "downloads": [
                            {
                                "filename": filename,
                                "sha256": hashlib.sha256(archive).hexdigest(),
                                "target_filename": "observ-minimax-code.tgz",
                                "label": "all all",
                                "platform": "all",
                                "arch": "all",
                                "collection_modes": ["full", "privacy"],
                            }
                        ],
                    }
                )
            )
            call_command(
                "configure_third_party_agents", endpoint="http://query.test", stdout=StringIO()
            )

            kind = AgentTypeConfig.objects.active_shared_runtime().get(
                agent_product="MiniMaxCode"
            )
            self.assertEqual(
                (kind.runtime_type, kind.setup_mode), ("minimax-code", "CLI")
            )
            guide = kind.config_guide
            setup = next(
                section for section in guide["sections"] if section["type"] == "setup_steps"
            )
            steps = setup["platforms"]["macOS/Linux"]["modes"]["cli"]["steps"]
            command = steps[0]["command"]
            self.assertIn("aiv connect minimax-code", command)
            self.assertIn("--installation-token '{installationToken}'", command)
            browser = APIClient()
            browser.force_authenticate(user=self.user)
            prefix = f"/api/v1/spaces/{self.space.uuid}/agents"
            space_catalog = f"/api/v2/spaces/{self.space.uuid}/agents"
            registration = {"agent_product": "MiniMaxCode", "name": "MiniMax Code"}
            created = browser.post(prefix + "/create", registration, format="json")
            self.assertEqual(created.data["code"], 0, created.data)
            agent = created.data["data"]["agent_uuid"]
            grant_path = f"{prefix}/{agent}/installation-tokens"
            grant = browser.post(grant_path, {}, format="json")
            self.assertEqual(grant.status_code, 200, grant.data)
            exchanged = APIClient().post(
                "/openapi/v1/agent-installations/exchange",
                {"space_id": self.space.uuid, "agent_uuid": agent},
                format="json",
                HTTP_AUTHORIZATION=f'Bearer {grant.data["data"]["installation_token"]}',
            )
            self.assertEqual(exchanged.status_code, 200, exchanged.data)
            authorized = exchanged.data["data"]
            self.assertEqual(
                tuple(authorized[key] for key in ("space_id", "agent_uuid", "agent_product")),
                (self.space.uuid, agent, "MiniMaxCode"),
            )
            auth = {"HTTP_AUTHORIZATION": f'Bearer {authorized["agent_credential"]}'}
            confirmed = APIClient().post(
                f"/openapi/v1/agents/{agent}/installation-complete", {}, format="json", **auth
            )
            self.assertEqual(confirmed.data["data"]["status"], 1)
            route = get_trace_datasource(self.space, agent_product="MiniMaxCode")
            self.assertEqual(route.source_system, "signoz")

            def market_scope(entries):
                return next(
                    entry["display_scope"]
                    for entry in entries
                    if entry["agent_product"] == "MiniMaxCode"
                )

            def market_scope_v1():
                return market_scope(browser.get("/api/v1/agent-types").data["data"])

            def market_scope_v2():
                return market_scope(browser.get(space_catalog).data["data"]["agent_types"])

            self.assertEqual(market_scope_v2(), "ALL")
            self.assertEqual(market_scope_v1(), "ALL")

            artifact.unlink()
            withdrawn = browser.get(space_catalog).data["data"]
            self.assertEqual(market_scope(withdrawn["agent_types"]), "HIDDEN")
            self.assertEqual(market_scope_v1(), "HIDDEN")
            self.assertTrue(
                any(row["uuid"] == agent and row["status"] == 1 for row in withdrawn["agents"])
            )
            blocked = browser.post(prefix + "/create", registration, format="json")
            self.assertEqual(blocked.data["code"], 1006)
            self.assertEqual(browser.post(grant_path, {}, format="json").status_code, 404)
            refreshed = APIClient().post(
                "/openapi/v1/ingest-tokens", {"agent_uuid": agent}, format="json", **auth
            )
            self.assertEqual(refreshed.data["code"], 0, refreshed.data)
            self.assertEqual(
                get_trace_datasource(self.space, agent_product="MiniMaxCode").pk, route.pk
            )

            artifact.write_bytes(archive)
            self.assertEqual(market_scope_v2(), "ALL")

    @override_settings(AIVISION_PUBLIC_BASE_URL="http://127.0.0.1:8001/")
    @patch.dict("os.environ", {"SIGNOZ_API_KEY": "test-placeholder"})
    def test_baked_base_url_when_public_base_pinned(self):
        # When the deployment pins AIVISION_PUBLIC_BASE_URL, the install command
        # must carry that exact origin (not the {baseUrl} placeholder): the
        # onboarding installer enforces a same-origin check against the artifact
        # download URL, which the backend builds from this same setting. The
        # trailing slash is normalised away so the flag stays a valid origin.
        call_command(
            "configure_third_party_agents",
            endpoint="http://query.test",
            stdout=StringIO(),
        )
        for kind in AgentTypeConfig.objects.active_shared_runtime().filter(
            category="CUSTOM"
        ):
            if kind.setup_mode != "CLI":
                continue
            command = next(
                section
                for section in kind.config_guide["sections"]
                if section["type"] == "setup_steps"
            )["platforms"]["macOS/Linux"]["modes"]["cli"]["steps"][0]["command"]
            self.assertIn("--base-url 'http://127.0.0.1:8001'", command)
            self.assertNotIn("{baseUrl}", command)

    @override_settings(
        AIVISION_INGEST_SECRET="test-signing-secret-at-least-32-characters",
        AIVISION_OTLP_INGEST_ENDPOINT="http://gateway.test",
    )
    def test_removed_agent_cannot_mint_ingest_token(self):
        raw, key_prefix, key_hash = SpaceKey.generate_key()
        SpaceKey.objects.create(
            space=self.space,
            creator=self.user,
            key_hash=key_hash,
            key_alias="test-key",
            key_prefix=key_prefix,
            enabled=True,
        )
        self.agent.status = 0
        self.agent.save()
        response = APIClient().post(
            "/openapi/v1/ingest-tokens",
            {"agent_uuid": self.agent.uuid},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {raw}",
        )
        self.assertEqual(response.status_code, 404, response.content)
        self.agent.refresh_from_db()
        self.assertEqual(self.agent.status, 0)
        self.assertEqual(Agent.objects.filter(space=self.space).count(), 1)

    def _key_client(self, space=None):
        raw, prefix, hashed = SpaceKey.generate_key()
        SpaceKey.objects.create(
            space=space or self.space, creator=self.user,
            key_hash=hashed, key_prefix=prefix, enabled=True,
        )
        self.reset_key_cache()
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
        return client

    def test_team_registration_is_idempotent_and_requires_management_permission(self):
        team = Space.objects.create(name="Team", type=2, creator=self.user)
        developer = create_local_user("onboard-developer")
        observer = create_local_user("onboard-observer")
        create_test_space_member(space=team, user=developer, role=2)
        create_test_space_member(space=team, user=observer, role=3)
        client = APIClient()
        url = f"/api/v1/spaces/{team.uuid}/agents/create"
        client.force_authenticate(user=observer)
        response = client.post(
            url, {"agent_product": "Codex", "name": "Codex"}, format="json"
        )
        self.assertEqual(response.status_code, 403, response.data)
        self.assertFalse(Agent.objects.filter(space=team).exists())
        client.force_authenticate(user=developer)
        payload = {"agent_product": "Codex", "name": "Codex"}
        first = client.post(url, payload, format="json")
        second = client.post(url, payload, format="json")
        self.assertEqual(first.data["code"], 0, first.data)
        self.assertEqual(second.data["code"], 0, second.data)
        self.assertEqual(first.data["data"]["agent_uuid"], second.data["data"]["agent_uuid"])
        agent = Agent.objects.get(space=team)
        self.assertEqual(agent.status, 2)
        self.assertEqual(agent.observation_subject_binding.user, developer)

    @override_settings(AIVISION_INGEST_SECRET="test-signing-secret-at-least-32-characters")
    def test_install_confirm_and_remove_lifecycle_in_both_space_types(self):
        for space_type in (1, 2):
            with self.subTest(space_type=space_type):
                self.space.type = space_type
                self.space.save()
                self.agent.status = 2
                self.agent.save()
                client = self._key_client()
                confirm = f"/openapi/v1/agents/{self.agent.uuid}/installation-complete"
                mint = lambda: client.post(
                    "/openapi/v1/ingest-tokens", {"agent_uuid": self.agent.uuid}, format="json"
                )
                self.assertEqual(mint().status_code, 200)
                self.agent.refresh_from_db()
                self.assertEqual(self.agent.status, 2)
                for _ in range(2):
                    response = client.post(confirm, {}, format="json")
                    self.assertEqual(response.status_code, 200, response.data)
                    self.assertEqual(response.data["data"]["status"], 1)
                browser = APIClient()
                browser.force_authenticate(user=self.user)
                path = f"/api/v1/spaces/{self.space.uuid}/agents/{self.agent.uuid}"
                removed = browser.post(path + "/disable")
                self.assertEqual(removed.data["code"], 0, removed.data)
                self.assertEqual(mint().status_code, 404)
                self.assertEqual(client.post(confirm, {}, format="json").status_code, 404)
                self.agent.refresh_from_db()
                self.assertEqual(self.agent.status, 0)
                restored = browser.post(path + "/enable")
                self.assertEqual(restored.data["code"], 0, restored.data)
                self.assertEqual(mint().status_code, 200)
                self.agent.refresh_from_db()
                self.assertEqual(self.agent.status, 2)

    def test_confirmation_requires_matching_space_key_and_live_subject(self):
        url = f"/openapi/v1/agents/{self.agent.uuid}/installation-complete"
        self.assertEqual(APIClient().post(url, {}, format="json").status_code, 401)
        other = Space.objects.create(name="Other", type=2, creator=self.user)
        self.assertEqual(self._key_client(other).post(url, {}, format="json").status_code, 404)
        client = self._key_client()
        binding = self.agent.observation_subject_binding
        binding.space = other
        binding.save()
        self.assertEqual(client.post(url, {}, format="json").status_code, 409)
        binding.space = self.space
        binding.status = 0
        binding.save()
        self.assertEqual(client.post(url, {}, format="json").status_code, 404)
        self.agent.refresh_from_db()
        self.assertEqual(self.agent.status, 2)

    @override_settings(AIVISION_INGEST_SECRET="test-signing-secret-at-least-32-characters")
    def test_cached_revoked_key_cannot_renew_or_confirm_installation(self):
        client = self._key_client()
        url = "/openapi/v1/ingest-tokens"
        body = {"agent_uuid": self.agent.uuid}
        self.assertEqual(client.post(url, body, format="json").status_code, 200)
        # Keep the authentication cache warm, as with a multi-worker deployment.
        SpaceKey.objects.filter(space=self.space).update(enabled=False)
        self.assertEqual(client.post(url, body, format="json").status_code, 401)
        confirmation = client.post(
            f"/openapi/v1/agents/{self.agent.uuid}/installation-complete", {}, format="json"
        )
        self.assertEqual(confirmation.status_code, 401)
        self.agent.refresh_from_db()
        self.assertEqual(self.agent.status, 2)

    def test_personal_query_uses_the_same_subject_as_market_ingestion(self):
        binding = self.agent.observation_subject_binding
        binding.subject_id = "explicit-collector-subject"
        binding.save()
        query = TraceListQuery(self.space, self.user, {"agent_product": "Codex", "username": "other"})
        params = SignozTraceProvider._params(query, {"agent_product_filter": "Codex"})
        self.assertEqual(params["user"], binding.subject_id)
        self.assertFalse(self.user.observation_subject_bindings.filter(source_system="signoz").exists())

    def test_personal_diagnosis_uses_agent_subject_without_signoz_binding(self):
        DataSource.objects.create(
            agent_type=self.kind, datasource_type="traces", source_system="signoz",
            enabled=1, is_default=1, config={"agent_product_filter": "Codex"},
        )
        binding = self.agent.observation_subject_binding
        binding.subject_id = "explicit-collector-subject"
        binding.save()
        client = APIClient()
        client.force_authenticate(user=self.user)
        url = f"/api/v1/spaces/{self.space.uuid}/diagnose/dataset-default-spec"

        for status in (2, 1):
            with self.subTest(agent_status=status):
                self.agent.status = status
                self.agent.save()
                response = client.get(url, {"agent_uuid": self.agent.uuid})
                self.assertEqual(response.status_code, 200, response.data)
                spec = response.data["data"]["spec"]
                self.assertEqual(spec["username"], binding.subject_id)
                self.assertEqual(spec["username_column"], "user_id")
                self.assertEqual(spec["space_filter"], self.space.uuid)
                self.assertEqual(spec["agent_product_filter"], "Codex")
                self.assertEqual(spec["scope_type"], "personal")
        self.assertFalse(
            self.user.observation_subject_bindings.filter(source_system="signoz").exists()
        )

    def test_personal_diagnosis_does_not_fall_back_from_invalid_agent_binding(self):
        DataSource.objects.create(
            agent_type=self.kind, datasource_type="traces", source_system="signoz",
            enabled=1, is_default=1, config={"agent_product_filter": "Codex"},
        )
        ObservationSubjectBinding.objects.create(
            space=self.space, user=self.user, source_system="signoz",
            subject_id="different-signoz-subject", binding_source="test_fixture",
        )
        client = APIClient()
        client.force_authenticate(user=self.user)
        url = f"/api/v1/spaces/{self.space.uuid}/diagnose/dataset-default-spec"
        response = client.get(url, {"agent_uuid": self.agent.uuid})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            response.data["data"]["spec"]["username"],
            self.agent.observation_subject_binding.subject_id,
        )

        binding = self.agent.observation_subject_binding
        binding.status = 0
        binding.save()
        response = client.get(url, {"agent_uuid": self.agent.uuid})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], 4220)
        self.assertIsNone(response.data["data"])
