import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


class RuntimeManifestTests(unittest.TestCase):
    def module(self):
        path = Path(__file__).parents[2] / "agentlens/docker/rule_runtime_manifest.py"
        spec = importlib.util.spec_from_file_location("rule_runtime_manifest", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_manifest_matches_locked_environment_and_changes_with_dependency(self):
        module = self.module()
        lock = Path(__file__).parents[2] / "agentlens/docker/requirements-rule-runtime.txt"
        manifest = module.create_manifest(lock)
        self.assertEqual(manifest["id"], "python-eval-v1")
        self.assertEqual(manifest["python_version"], "3.11.14")
        self.assertEqual(set(manifest["packages"]), module.PACKAGES)
        self.assertEqual(
            json.loads((lock.parent / "rule-runtime-manifest.json").read_text()), manifest
        )
        with tempfile.TemporaryDirectory() as directory:
            altered = Path(directory) / "requirements.txt"
            altered.write_text(
                lock.read_text().replace(
                    f"openpyxl=={manifest['packages']['openpyxl']}", "openpyxl==0.0.1"
                )
            )
            self.assertNotEqual(
                module.create_manifest(altered)["manifest_sha256"], manifest["manifest_sha256"]
            )

    def test_missing_and_ambiguous_required_packages_are_rejected(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory) / "requirements.txt"
            for content in ("pytest==9.2.0\n", "pytest==9.2.0\npytest==9.1.1\n"):
                lock.write_text(content)
                with self.assertRaises(ValueError):
                    module.create_manifest(lock)


if __name__ == "__main__":
    unittest.main()
