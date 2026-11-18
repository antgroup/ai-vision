from __future__ import annotations

import unittest
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "observ_hermes" / "pyproject.toml"
REPLAY_PYPROJECT = ROOT / "hermes-replay" / "pyproject.toml"


class PackagingTests(unittest.TestCase):
    def test_wheel_keeps_modules_under_observ_hermes_namespace(self) -> None:
        config = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
        wheel = config["tool"]["hatch"]["build"]["targets"]["wheel"]

        self.assertNotIn("packages", wheel)
        self.assertEqual(wheel["sources"], {"": "observ_hermes"})
        self.assertIn("*.py", wheel["include"])
        self.assertIn("hooks/*.py", wheel["include"])
        self.assertIn("exporters/*.py", wheel["include"])

    def test_production_has_only_its_plugin_entry_point_and_no_console_scripts(
        self,
    ) -> None:
        config = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
        project = config["project"]
        self.assertNotIn("scripts", project)
        self.assertEqual(
            project["entry-points"]["hermes_agent.plugins"],
            {"observ-hermes": "observ_hermes:register"},
        )

    def test_replay_owns_only_replay_plugin_and_three_console_commands(self) -> None:
        config = tomllib.loads(REPLAY_PYPROJECT.read_text(encoding="utf-8"))
        project = config["project"]
        self.assertEqual(project["dependencies"], ["aivision-hermes-core==0.2.2"])
        self.assertEqual(
            project["entry-points"]["hermes_agent.plugins"],
            {"hermes-replay": "hermes_replay:register"},
        )
        self.assertEqual(
            project["scripts"],
            {
                "hermes-replay": "hermes_replay.replay:main",
                "observ-hermes-replay": "hermes_replay.replay:main",
                "observ-hermes-report": "hermes_replay.report:main",
            },
        )

    def test_both_plugin_manifests_record_the_exact_core_cohort(self) -> None:
        for path in (
            ROOT / "observ_hermes/plugin.yaml",
            ROOT / "hermes-replay/hermes_replay/plugin.yaml",
        ):
            text = path.read_text()
            self.assertIn('aivision-hermes-core: "0.2.2"', text, str(path))


if __name__ == "__main__":
    unittest.main()
