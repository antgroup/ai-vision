from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

HERMES_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = HERMES_ROOT.parent
INVENTORY_PATH = HERMES_ROOT / "tests/fixtures/semconv_policy_inventory_v4.json"
HOOK_FIXTURE_PATH = HERMES_ROOT / "tests/fixtures/hooks/v0.18.2/observer_envelopes.json"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


inventory_generator = load_module(
    "observ_hermes_contract_inventory_generator",
    HERMES_ROOT / "scripts/generate_contract_inventory.py",
)


def installed_hermes_version() -> str:
    executable = shutil.which("hermes")
    if not executable:
        return ""
    result = subprocess.run(
        [executable, "--version"], text=True, capture_output=True, check=False
    )
    match = re.search(r"Hermes Agent v([^\s]+)", result.stdout + result.stderr)
    return match.group(1) if match else ""


class ContractInventoryTests(unittest.TestCase):
    def test_checked_in_inventory_is_current_and_deterministic(self) -> None:
        expected = inventory_generator.render_inventory()
        self.assertEqual(INVENTORY_PATH.read_text(encoding="utf-8"), expected)
        self.assertEqual(inventory_generator.render_inventory(), expected)

    def test_every_entity_specific_policy_has_a_semconv_registration(self) -> None:
        inventory = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
        reconciliation = inventory["reconciliation"]
        self.assertEqual(reconciliation["missingSemconvForPolicy"], [])
        self.assertEqual(reconciliation["missingNormativeRedMetrics"], [])
        self.assertEqual(len(reconciliation["normativeRedMetrics"]), 6)

    def test_hook_fixtures_pin_real_v0182_observer_envelopes(self) -> None:
        fixture = json.loads(HOOK_FIXTURE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(fixture["hermesVersion"], "0.18.2")
        self.assertEqual(fixture["observerSchemaVersion"], "hermes.observer.v1")
        hooks = {item["hook"]: item for item in fixture["hooks"]}
        expected_hooks = {
            "on_session_start",
            "on_session_end",
            "on_session_finalize",
            "on_session_reset",
            "pre_llm_call",
            "post_llm_call",
            "pre_api_request",
            "post_api_request",
            "api_request_error",
            "pre_tool_call",
            "post_tool_call",
            "subagent_start",
            "subagent_stop",
        }
        self.assertEqual(set(hooks), expected_hooks)
        self.assertEqual(
            hooks["post_api_request"]["envelope"]["usage"]["input_tokens"], 11
        )
        self.assertEqual(hooks["pre_tool_call"]["envelope"]["tool_call_id"], "call-001")
        for item in hooks.values():
            self.assertEqual(
                item["envelope"]["telemetry_schema_version"], "hermes.observer.v1"
            )
            self.assertIn("faithfully minimized", item["provenance"])

    @unittest.skipUnless(
        installed_hermes_version() == "0.18.2",
        "installed Hermes is not the pinned v0.18.2 contract runtime",
    )
    def test_hook_fixture_matches_installed_pinned_hermes(self) -> None:
        subprocess.run(
            [
                sys.executable,
                str(HERMES_ROOT / "scripts/capture_hermes_v0182_hooks.py"),
                "--check",
            ],
            cwd=REPO_ROOT,
            check=True,
        )


if __name__ == "__main__":
    unittest.main()
