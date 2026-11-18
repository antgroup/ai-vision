from __future__ import annotations

import ast
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

import tomllib

HERMES_ROOT = Path(__file__).resolve().parents[1]
CORE_ROOT = HERMES_ROOT / "hermes-core"
CORE_PACKAGE = CORE_ROOT / "aivision_hermes_core"
PRODUCTION_PACKAGE = HERMES_ROOT / "observ_hermes"
REPLAY_ROOT = HERMES_ROOT / "hermes-replay"
REPLAY_PACKAGE = REPLAY_ROOT / "hermes_replay"


class CoreBoundaryTests(unittest.TestCase):
    def test_core_distribution_is_neutral_and_has_no_entry_points(self) -> None:
        metadata = tomllib.loads((CORE_ROOT / "pyproject.toml").read_text())
        project = metadata["project"]
        self.assertEqual(project["name"], "aivision-hermes-core")
        self.assertEqual(project["version"], "0.2.2")
        self.assertEqual(project["dependencies"], [])
        self.assertNotIn("scripts", project)
        self.assertNotIn("entry-points", project)
        self.assertFalse((CORE_PACKAGE / "plugin.yaml").exists())

    def test_core_has_no_product_network_config_or_persistence_imports(self) -> None:
        forbidden_import_roots = {
            "observ_hermes",
            "opentelemetry",
            "urllib",
            "requests",
            "httpx",
        }
        forbidden_names = {
            "TranscriptWriter",
            "HttpJsonSink",
            "register",
            "load_config",
            "production_config_path",
        }
        for path in sorted(CORE_PACKAGE.glob("*.py")):
            tree = ast.parse(path.read_text(), filename=str(path))
            imports: set[str] = set()
            names: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.add(node.module.split(".")[0])
                elif isinstance(
                    node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
                ):
                    names.add(node.name)
            self.assertFalse(imports & forbidden_import_roots, f"{path}: {imports}")
            self.assertFalse(names & forbidden_names, f"{path}: {names}")

    def test_production_pins_exact_core_and_semantic_implementation_is_unique(
        self,
    ) -> None:
        metadata = tomllib.loads((PRODUCTION_PACKAGE / "pyproject.toml").read_text())
        self.assertIn(
            "aivision-hermes-core==0.2.2", metadata["project"]["dependencies"]
        )
        for name in ("canonical", "normalize", "privacy", "semantic", "state"):
            shim = (PRODUCTION_PACKAGE / f"{name}.py").read_text()
            self.assertIn(f"aivision_hermes_core.{name}", shim)
            self.assertLess(len(shim.splitlines()), 12)
            self.assertGreater(
                len((CORE_PACKAGE / f"{name}.py").read_text().splitlines()), 20
            )

    def test_product_import_boundaries_are_one_way_through_core_only(self) -> None:
        self.assertFalse((PRODUCTION_PACKAGE / "replay.py").exists())
        self.assertFalse((PRODUCTION_PACKAGE / "report.py").exists())
        self.assertFalse((PRODUCTION_PACKAGE / "transcript.py").exists())
        self.assertFalse((PRODUCTION_PACKAGE / "exporters/semantic_file.py").exists())
        for path in sorted(PRODUCTION_PACKAGE.rglob("*.py")):
            source = path.read_text()
            self.assertNotIn("hermes_replay", source, str(path))
            self.assertNotIn("TranscriptWriter", source, str(path))
        for path in sorted(REPLAY_PACKAGE.rglob("*.py")):
            tree = ast.parse(path.read_text(), filename=str(path))
            modules = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    modules.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    modules.add(node.module)
            self.assertFalse(
                any(
                    module == "observ_hermes" or module.startswith("observ_hermes.")
                    for module in modules
                ),
                f"{path}: {modules}",
            )

    def test_built_wheels_contain_only_their_owned_namespaces_and_entry_points(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            wheels = {}
            for name, root in (
                ("core", CORE_ROOT),
                ("production", PRODUCTION_PACKAGE),
                ("replay", REPLAY_ROOT),
            ):
                result = subprocess.run(
                    [
                        "uv",
                        "build",
                        "--wheel",
                        "--out-dir",
                        str(output / name),
                        str(root),
                    ],
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                wheels[name] = next((output / name).glob("*.whl"))
            with zipfile.ZipFile(wheels["core"]) as archive:
                names = set(archive.namelist())
                self.assertTrue(
                    any(name.startswith("aivision_hermes_core/") for name in names)
                )
                self.assertFalse(any("entry_points.txt" in name for name in names))
                self.assertFalse(
                    any(
                        name.startswith(("observ_hermes/", "hermes_replay/"))
                        for name in names
                    )
                )
            with zipfile.ZipFile(wheels["production"]) as archive:
                names = set(archive.namelist())
                self.assertTrue(
                    any(name.startswith("observ_hermes/") for name in names)
                )
                self.assertFalse(
                    any(name.startswith("hermes_replay/") for name in names)
                )
                self.assertFalse(
                    any(
                        name.endswith(("/replay.py", "/report.py", "/transcript.py"))
                        for name in names
                    )
                )
                entry_points = archive.read(
                    next(name for name in names if name.endswith("entry_points.txt"))
                ).decode()
                self.assertIn("observ-hermes = observ_hermes:register", entry_points)
                self.assertNotIn("hermes-replay", entry_points)
            with zipfile.ZipFile(wheels["replay"]) as archive:
                names = set(archive.namelist())
                self.assertTrue(
                    any(name.startswith("hermes_replay/") for name in names)
                )
                self.assertFalse(
                    any(name.startswith("observ_hermes/") for name in names)
                )
                entry_points = archive.read(
                    next(name for name in names if name.endswith("entry_points.txt"))
                ).decode()
                for command in (
                    "hermes-replay",
                    "observ-hermes-replay",
                    "observ-hermes-report",
                ):
                    self.assertIn(command, entry_points)

    def test_each_product_imports_with_core_without_the_sibling_product(self) -> None:
        cases = (
            (
                "production",
                [PRODUCTION_PACKAGE.parent, CORE_ROOT],
                "import importlib.util; import observ_hermes; "
                "assert importlib.util.find_spec('hermes_replay') is None",
            ),
            (
                "replay",
                [REPLAY_ROOT, CORE_ROOT],
                "import importlib.util; import hermes_replay; "
                "assert importlib.util.find_spec('observ_hermes') is None",
            ),
        )
        for name, roots, code in cases:
            with self.subTest(name=name):
                result = subprocess.run(
                    [
                        sys.executable,
                        "-I",
                        "-c",
                        f"import sys; sys.path[:0] = {list(map(str, roots))!r}; {code}",
                    ],
                    env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_core_import_and_minimal_normalization_work_without_production_package(
        self,
    ) -> None:
        sys.path.insert(0, str(CORE_ROOT))
        try:
            from aivision_hermes_core.normalize import CanonicalNormalizer

            event = CanonicalNormalizer().normalize(
                "on_session_start",
                {
                    "telemetry_schema_version": "hermes.observer.v1",
                    "session_id": "core-smoke",
                    "model": "model",
                },
                timestamp=1_750_000_000.0,
            )
        finally:
            sys.path.remove(str(CORE_ROOT))
        self.assertEqual(event.session_id, "core-smoke")


if __name__ == "__main__":
    unittest.main()
