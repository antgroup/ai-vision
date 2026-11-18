from __future__ import annotations

import hashlib
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.hermes_cohort import Artifact, CohortError, CohortManager

HERMES_ROOT = Path(__file__).resolve().parents[1]


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    if not root.exists():
        return digest.hexdigest()
    for path in sorted(root.rglob("*"), key=lambda item: str(item.relative_to(root))):
        relative = str(path.relative_to(root)).encode()
        digest.update(relative)
        digest.update(b"d" if path.is_dir() else b"f")
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()


def versioned_copy(source: Path, destination: Path, version: str) -> Path:
    shutil.copytree(source, destination)
    pyproject = destination / "pyproject.toml"
    text = pyproject.read_text()
    text = text.replace('version = "0.2.2"', f'version = "{version}"', 1)
    text = text.replace(
        "aivision-hermes-core==0.2.2",
        f"aivision-hermes-core=={version}",
    )
    pyproject.write_text(text)
    return destination


class CohortTests(unittest.TestCase):
    def artifacts(self):
        return (
            Artifact.load(HERMES_ROOT / "observ_hermes"),
            Artifact.load(HERMES_ROOT / "hermes-replay"),
            Artifact.load(HERMES_ROOT / "hermes-core", core=True),
        )

    def test_artifacts_declare_the_same_exact_core_version(self) -> None:
        production, replay, core = self.artifacts()
        self.assertEqual(production.distribution, "observ-hermes")
        self.assertEqual(replay.distribution, "hermes-replay")
        self.assertEqual(production.required_core, "0.2.2")
        self.assertEqual(replay.required_core, "0.2.2")
        self.assertEqual(core.version, "0.2.2")

    def test_independent_install_and_last_consumer_uninstall(self) -> None:
        production, replay, core = self.artifacts()
        with tempfile.TemporaryDirectory() as tmp:
            manager = CohortManager(Path(tmp) / "cohort")
            manager.install_product("production", production, core)
            self.assertEqual(set(manager.state()["products"]), {"production"})
            self.assertTrue((manager.current / "core").is_dir())
            self.assertFalse((manager.current / "replay").exists())

            manager.install_product("replay", replay, core)
            self.assertEqual(set(manager.state()["products"]), {"production", "replay"})
            manager.uninstall_product("replay")
            self.assertEqual(set(manager.state()["products"]), {"production"})
            self.assertTrue((manager.current / "core").is_dir())
            manager.uninstall_product("production")
            self.assertEqual(manager.state()["products"], {})
            self.assertIsNone(manager.state()["core"])
            self.assertFalse((manager.current / "core").exists())

    def test_incompatible_one_sided_upgrade_and_rollback_fail_before_mutation(
        self,
    ) -> None:
        production, replay, core = self.artifacts()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = CohortManager(root / "cohort")
            manager.install_cohort(production, replay, core)
            with tempfile.TemporaryDirectory() as artifacts_tmp:
                artifact_root = Path(artifacts_tmp)
                production_v3 = Artifact.load(
                    versioned_copy(
                        HERMES_ROOT / "observ_hermes",
                        artifact_root / "production",
                        "0.3.0",
                    )
                )
                replay_v3 = Artifact.load(
                    versioned_copy(
                        HERMES_ROOT / "hermes-replay", artifact_root / "replay", "0.3.0"
                    )
                )
                core_v3 = Artifact.load(
                    versioned_copy(
                        HERMES_ROOT / "hermes-core", artifact_root / "core", "0.3.0"
                    ),
                    core=True,
                )
                before = tree_digest(manager.root)
                with self.assertRaisesRegex(
                    CohortError, "cannot change production alone"
                ):
                    manager.install_product("production", production_v3, core_v3)
                self.assertEqual(tree_digest(manager.root), before)

                manager.install_cohort(production_v3, replay_v3, core_v3)
                self.assertEqual(manager.state()["core"]["version"], "0.3.0")
                before = tree_digest(manager.root)
                with self.assertRaisesRegex(
                    CohortError, "cannot roll back production alone"
                ):
                    manager.rollback_product("production")
                self.assertEqual(tree_digest(manager.root), before)

                manager.rollback_cohort()
                self.assertEqual(manager.state()["core"]["version"], "0.2.2")

    def test_cohort_failure_injection_restores_current_and_backup_byte_for_byte(
        self,
    ) -> None:
        production, replay, core = self.artifacts()
        for fail_phase in ("before-swap", "during-swap", "after-swap"):
            with (
                self.subTest(fail_phase=fail_phase),
                tempfile.TemporaryDirectory() as tmp,
            ):
                manager = CohortManager(Path(tmp) / "cohort")
                manager.install_product("production", production, core)
                before = tree_digest(manager.root)
                with self.assertRaises(CohortError):
                    manager.install_cohort(
                        production,
                        replay,
                        core,
                        fail_phase=fail_phase,
                    )
                self.assertEqual(tree_digest(manager.root), before)

    def test_single_product_rollback_can_remove_it_while_retaining_sibling_core(
        self,
    ) -> None:
        production, replay, core = self.artifacts()
        with tempfile.TemporaryDirectory() as tmp:
            manager = CohortManager(Path(tmp) / "cohort")
            manager.install_product("replay", replay, core)
            manager.install_product("production", production, core)
            manager.rollback_product("production")
            self.assertEqual(set(manager.state()["products"]), {"replay"})
            self.assertTrue((manager.current / "core").is_dir())
            self.assertTrue((manager.current / "replay").is_dir())
            self.assertFalse((manager.current / "production").exists())


if __name__ == "__main__":
    unittest.main()
