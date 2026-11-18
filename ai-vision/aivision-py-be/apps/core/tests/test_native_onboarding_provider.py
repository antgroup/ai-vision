"""Native installer selection must never fall back to readable helpers."""
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import SimpleTestCase, override_settings

from apps.core.services.plugin_artifacts import latest_plugin, support_files


class NativeOnboardingProviderTests(SimpleTestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.plugin = {"key": "codex", "name": "Codex", "binary": "cdxhooks"}
        (self.root / "agents.json").write_text(json.dumps([self.plugin]))
        (self.root / "scripts").mkdir()
        (self.root / "scripts/connect").write_text('BASE = "__AIVISION_BASE_URL__"\n')
        settings = override_settings(
            AIVISION_AGENT_PLUGINS_DIR=str(self.root),
            AIVISION_OBSERV_AGENTS_DIR="",
            AIVISION_PLUGIN_ARTIFACTS_DIR="",
            AIVISION_PUBLIC_BASE_URL="https://aivision.example",
        )
        settings.enable()
        self.addCleanup(settings.disable)

    def bundle(self, platform="linux-gnu", arch="arm64", content=b"native-archive-fixture"):
        path = self.root / "native-onboarding" / f"{platform}-{arch}" / "connect.tar.gz"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def test_native_bundle_has_explicit_format_and_content_hash(self):
        path = self.bundle()
        response = self.client.get(
            "/api/public/onboarding/scripts/connect?platform=linux-gnu&arch=arm64"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-AIV-Installer-Format"], "native-tar-v1")
        self.assertEqual(response["X-AIV-Installer-SHA256"], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(response["Content-Type"], "application/gzip")
        self.assertEqual(b"".join(response.streaming_content), path.read_bytes())

    def test_platform_and_arch_select_exact_bundle(self):
        for platform in ("linux-gnu", "darwin"):
            for arch in ("arm64", "x64"):
                content = f"{platform}/{arch}".encode()
                self.bundle(platform, arch, content)
                with self.subTest(platform=platform, arch=arch):
                    response = self.client.get(
                        "/api/public/onboarding/scripts/connect",
                        {"platform": platform, "arch": arch},
                    )
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(b"".join(response.streaming_content), content)

    def test_native_mode_requires_supported_target(self):
        self.bundle()
        for query in ({}, {"platform": "linux-gnu"},
                      {"platform": "windows", "arch": "arm64"},
                      {"platform": "../linux-gnu", "arch": "arm64"},
                      {"platform": "linux-gnu", "arch": "../../private"}):
            with self.subTest(query=query):
                response = self.client.get("/api/public/onboarding/scripts/connect", query)
                self.assertEqual(response.status_code, 400)

    def test_missing_native_bundle_does_not_return_source(self):
        self.bundle()
        response = self.client.get(
            "/api/public/onboarding/scripts/connect?platform=darwin&arch=x64"
        )
        self.assertEqual(response.status_code, 404)
        self.assertNotIn(b"BASE =", response.content)

    def test_empty_native_root_also_disables_source_fallback(self):
        (self.root / "native-onboarding").mkdir()
        response = self.client.get(
            "/api/public/onboarding/scripts/connect?platform=linux-gnu&arch=arm64"
        )
        self.assertEqual(response.status_code, 404)

    def test_bundle_symlink_cannot_escape_native_root(self):
        path = self.bundle()
        path.unlink()
        path.symlink_to(self.root / "scripts/connect")
        response = self.client.get(
            "/api/public/onboarding/scripts/connect?platform=linux-gnu&arch=arm64"
        )
        self.assertEqual(response.status_code, 404)

    def test_source_checkout_preserves_text_installer(self):
        response = self.client.get("/api/public/onboarding/scripts/connect")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'https://aivision.example', response.content)
        self.assertNotIn("X-AIV-Installer-Format", response)

    def test_native_legacy_python_endpoint_returns_actionable_error(self):
        self.bundle()
        response = self.client.get("/api/public/onboarding/install-codex.py")
        self.assertEqual(response.status_code, 409)
        self.assertIn("aiv connect codex", response.json()["message"])
        self.assertIsNone(latest_plugin(self.plugin, "https://aivision.example")["install_url"])

    def test_native_shell_bootstrap_returns_actionable_error(self):
        self.bundle()
        (self.root / "scripts/install.sh").write_text("#!/bin/sh\npython connect.py\n")
        response = self.client.get("/api/public/onboarding/install.sh")
        self.assertEqual(response.status_code, 409)
        self.assertIn("aiv connect", response.json()["message"])

    def test_missing_helpers_are_omitted_and_downloads_return_404(self):
        present = self.root / "scripts/native_config.py"
        present.write_text("# fixture source checkout helper\n")
        entries = support_files("https://aivision.example")
        self.assertEqual([entry["name"] for entry in entries], ["native_config.py"])
        self.assertEqual(entries[0]["sha256"], hashlib.sha256(present.read_bytes()).hexdigest())
        response = self.client.get("/api/public/onboarding/support/hook_runtime.py")
        self.assertEqual(response.status_code, 404)

    def test_existing_helper_keeps_file_download(self):
        content = b"# fixture helper\n"
        (self.root / "scripts/hook_runtime.py").write_bytes(content)
        response = self.client.get("/api/public/onboarding/support/hook_runtime.py")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), content)

    def test_native_mode_does_not_publish_leftover_python_helpers(self):
        self.bundle()
        (self.root / "scripts/hook_runtime.py").write_text("# leftover source\n")
        self.assertEqual(support_files("https://aivision.example"), [])
        response = self.client.get("/api/public/onboarding/support/hook_runtime.py")
        self.assertEqual(response.status_code, 404)
