import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from apps.core.services.plugin_artifacts import plugins_root, plugin_catalog
from apps.core.management.commands.configure_third_party_agents import plugin_config_guide


class AgentDownloadLatestTests(SimpleTestCase):
    def test_hermes_native_relay_guide_does_not_offer_an_unbuilt_cli_installer(self):
        plugin = next(item for item in plugin_catalog() if item["key"] == "hermes")
        self.assertEqual((plugin["product"], plugin["runtime"], plugin["setup_mode"]), ("Hermes", "hermes", "OTHER"))
        guide = plugin_config_guide(plugin)
        self.assertEqual(guide["schema"], "v2")
        self.assertNotIn("plugin_key", guide)
        commands = json.dumps(guide)
        self.assertEqual(guide["sections"], [])
        native = guide["native_relay_onboarding"]
        self.assertIs(native["enabled"], True)
        self.assertEqual(native["integrations"], ["hermes-relay"])
        self.assertEqual(
            native["otlp_endpoint"],
            "https://vision.example.test/api/public/otel/v1/traces",
        )
        self.assertNotIn("agent-plugins/hermes", commands)
        self.assertNotIn("aiv connect hermes", commands)
        self.assertEqual(self.client.get("/api/public/onboarding/install-hermes.py").status_code, 404)
        entry = next(item for item in self.client.get(self.url).json()["data"]["agents"] if item["key"] == "hermes")
        self.assertIsNone(entry["install_url"])

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings_override = override_settings(
            AIVISION_PLUGIN_ARTIFACTS_DIR=str(self.root),
            AIVISION_PUBLIC_BASE_URL="https://vision.example.test",
        )
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.client = APIClient()
        self.url = reverse("onboarding_catalog")

    def build(self, key="codex", filename="cdxhooks-1.0-linux-gnu-x64"):
        version = "1.0"
        path = self.root / key / version
        path.mkdir(parents=True)
        (path / filename).write_bytes(b"production artifact")
        digest = hashlib.sha256(b"production artifact").hexdigest()
        (path.parent / "latest.json").write_text(
            json.dumps(
                {
                    "version": version,
                    "downloads": [
                        {
                            "filename": filename,
                            "sha256": digest,
                            "platform": "linux-gnu",
                            "arch": "x64",
                            "label": "Linux x64",
                            "target_filename": "cdxhooks",
                        }
                    ],
                }
            )
        )
        return digest

    def test_catalog_is_public_local_and_lists_only_built_variants(self):
        digest = self.build()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        catalog = json.loads((plugins_root() / "agents.json").read_text())
        self.assertCountEqual(
            [agent["key"] for agent in data["agents"]],
            [agent["key"] for agent in catalog],
        )
        self.assertEqual(
            [agent["key"] for agent in data["agents"] if agent["downloads"]],
            ["codex"],
        )
        self.assertTrue(
            all(
                agent["version"] is None
                for agent in data["agents"]
                if agent["key"] != "codex"
            )
        )
        codex = next(a for a in data["agents"] if a["key"] == "codex")
        self.assertEqual(codex["version"], "1.0")
        self.assertEqual(len(codex["downloads"]), 1)
        download = codex["downloads"][0]
        self.assertTrue(
            download["url"].startswith("https://vision.example.test/api/public/")
        )
        self.assertEqual(download["sha256"], digest)
        self.assertEqual(download["collection_modes"], ["full"])
        self.assertEqual(
            download["url_template"].replace("{version}", "1.0"), download["url"]
        )
        self.assertNotIn("cloudideoss", response.content.decode())
        self.assertNotIn("space_key", response.content.decode())
        self.assertTrue(data["support_files"])

    def test_collection_modes_are_per_artifact_and_never_inferred_for_old_versions(self):
        self.build()
        path = self.root / "codex/latest.json"
        manifest = json.loads(path.read_text())
        manifest["downloads"][0]["collection_modes"] = ["full", "privacy"]
        legacy = {**manifest["downloads"][0], "arch": "arm64"}
        legacy.pop("collection_modes")
        manifest["downloads"].append(legacy)
        path.write_text(json.dumps(manifest))
        agents = self.client.get(self.url).json()["data"]["agents"]
        downloads = next(agent for agent in agents if agent["key"] == "codex")["downloads"]
        self.assertEqual(downloads[0]["collection_modes"], ["full", "privacy"])
        self.assertEqual(downloads[1]["collection_modes"], ["full"])

    def test_browser_download_route_keeps_local_session_boundary(self):
        self.assertEqual(
            self.client.get(reverse("agent_download_latest")).status_code, 401
        )
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_unbuilt_plugin_has_no_download(self):
        agents = self.client.get(self.url).json()["data"]["agents"]
        self.assertTrue(
            all(a["version"] is None and not a["downloads"] for a in agents)
        )

    def test_downloads_artifact_and_refuses_traversal_or_unknown_agent(self):
        self.build()
        response = self.client.get(
            "/api/public/onboarding/plugins/codex/1.0/cdxhooks-1.0-linux-gnu-x64"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"production artifact")
        for url in [
            "/api/public/onboarding/plugins/codex/../agents.json",
            "/api/public/onboarding/plugins/internal/1.0/file",
            "/api/public/onboarding/support/secret.json",
        ]:
            self.assertEqual(self.client.get(url).status_code, 404)

    def test_historical_build_names_are_downloadable_without_latest_manifest(self):
        self.build()
        historical = self.root / "codex" / "0.9.0"
        historical.mkdir()
        artifact = historical / "cdxhooks-0.9.0-windows-x64.exe"
        artifact.write_bytes(b"historical build")
        (historical / "build-notes.txt").write_text("notes for packaging")
        prefix = "/api/public/onboarding/plugins/codex/0.9.0/"
        response = self.client.get(prefix + artifact.name)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"historical build")
        self.assertEqual(self.client.get(prefix + "build-notes.txt").status_code, 404)

    def test_per_agent_scripts_embed_only_base_and_agent(self):
        for agent in [
            "claude-code",
            "codex",
            "kimi",
            "opencode",
            "openclaw",
            "pi",
            "dsh",
        ]:
            response = self.client.get(f"/api/public/onboarding/install-{agent}.py")
            self.assertEqual(response.status_code, 200)
            source = response.content.decode()
            self.assertIn(f'DEFAULT_AGENT = "{agent}"', source)
            self.assertIn('DEFAULT_BASE_URL = "https://vision.example.test"', source)
            compile(source, agent, "exec")
