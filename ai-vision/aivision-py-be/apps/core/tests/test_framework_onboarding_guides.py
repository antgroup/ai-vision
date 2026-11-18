from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from apps.core.management.commands.configure_third_party_agents import plugin_config_guide
from apps.core.services.plugin_artifacts import plugin_catalog, plugins_root


class FrameworkGuideTests(SimpleTestCase):
    @override_settings(AIVISION_PUBLIC_BASE_URL="https://vision.example/")
    def test_hermes_native_relay_guide_uses_application_identity_without_source_install_steps(self):
        plugin = next(p for p in plugin_catalog() if p["key"] == "hermes")
        guide = plugin_config_guide(plugin)
        self.assertEqual(
            guide["native_relay_onboarding"]["enabled"],
            plugin["native_relay_onboarding"]["enabled"],
        )
        self.assertEqual(plugin["product"], "Hermes")
        self.assertEqual(plugin["runtime"], "hermes")
        self.assertEqual(plugin["setup_mode"], "OTHER")
        self.assertTrue(plugin["source_install"])
        self.assertEqual(guide["schema"], "v2")
        self.assertEqual(guide["native_relay_onboarding"]["integrations"], ["hermes-relay"])
        self.assertNotIn("integration", guide)
        self.assertNotIn("plugin_key", guide)
        self.assertEqual(
            guide["native_relay_onboarding"]["otlp_endpoint"],
            "https://vision.example/api/public/otel/v1/traces",
        )
        self.assertEqual(guide["sections"], [])

    @override_settings(AIVISION_PUBLIC_BASE_URL="")
    def test_hermes_relay_endpoint_uses_dialog_origin_when_public_origin_is_unset(self):
        guide = plugin_config_guide({
            "key": "hermes", "source_install": True,
            "native_relay_onboarding": {"enabled": True},
        })
        self.assertEqual(
            guide["native_relay_onboarding"]["otlp_endpoint"],
            "{baseUrl}/api/public/otel/v1/traces",
        )

    def test_integration_metadata_is_served_for_catalog_defined_sdks(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "example-sdk/examples").mkdir(parents=True)
            (root / "example-sdk/examples/example.yaml").write_text(
                "id: sample\npipExample: print('sample')\n"
            )
            with patch(
                "apps.core.management.commands.configure_third_party_agents.plugins_root",
                return_value=root,
            ):
                guide = plugin_config_guide({
                    "key": "example-sdk",
                    "examples_dir": "example-sdk/examples",
                    "integration": {
                        "kind": "observability_sdk",
                        "onboardingPath": "/access/example-onboarding",
                        "docsUrl": "https://docs.example.com/sdk",
                        "verifiedVersions": ["example-sdk==1.0"],
                    },
                })
            self.assertEqual(guide.get("integration"), {
                "kind": "observability_sdk",
                "onboardingPath": "/access/example-onboarding",
                "docsUrl": "https://docs.example.com/sdk",
                "verifiedVersions": ["example-sdk==1.0"],
            })
            self.assertEqual(guide["frameworks"][0]["pipExample"], "print('sample')")

    def test_relay_download_and_copy_use_the_same_runtime(self):
        plugin = next(p for p in plugin_catalog() if p["key"] == "nvidia-nemo-relay")
        examples = plugin_config_guide(plugin)["frameworks"]
        self.assertEqual(
            {example["id"] for example in examples},
            {"langchain-agent-middleware", "langgraph", "deep-agents", "nodejs"},
        )
        runtime = (plugins_root() / "nvidia-nemo-relay/relay_tracing.py").read_text()
        for example in examples:
            if example.get("language") == "typescript":
                self.assertNotIn("bootstrapExample", example)
                self.assertTrue(example["npmExample"])
                self.assertEqual(example["exampleFilename"], "relay-nodejs.ts")
                self.assertEqual(example["runCommand"], "node relay-nodejs.ts")
                self.assertFalse(example["requiresModel"])
                continue
            self.assertEqual(example.get("bootstrapExample"), runtime)
            self.assertTrue(example["pipExample"].startswith(runtime + "\n\n"))
            compile(example["pipExample"], example["id"], "exec")

    def test_langfuse_javascript_guides_keep_runtime_and_language_metadata(self):
        plugin = next(p for p in plugin_catalog() if p["key"] == "langfuse")
        examples = plugin_config_guide(plugin)["frameworks"]
        javascript = [item for item in examples if item.get("language") == "typescript"]
        self.assertEqual({item["id"] for item in javascript}, {
            "langchain-js", "langgraph-js", "openai-js", "manual-js",
        })
        for example in javascript:
            self.assertTrue(example["npmInstall"].startswith("npm install "))
            self.assertTrue(example["npmExample"])
            self.assertTrue(example["existingExample"])
            self.assertTrue(example["exampleFilename"].endswith(".ts"))
            self.assertIn(example["exampleFilename"], example["runCommand"])
            self.assertIn("Node.js", example["runtime"])
            self.assertNotIn("pipInstall", example)

    def test_bootstrap_cannot_read_outside_its_plugin(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "relay/examples").mkdir(parents=True)
            (root / "private.py").write_text("PRIVATE")
            (root / "relay/linked.py").symlink_to(root / "private.py")
            for name in ("../private.py", str(root / "private.py"), "linked.py"):
                with self.subTest(name=name):
                    (root / "relay/examples/example.yaml").write_text(
                        f"id: relay\nbootstrapFile: {name}\npipExample: pass\n"
                    )
                    with patch(
                        "apps.core.management.commands.configure_third_party_agents.plugins_root",
                        return_value=root,
                    ), self.assertRaises(CommandError):
                        plugin_config_guide({"key": "relay", "examples_dir": "relay/examples"})
