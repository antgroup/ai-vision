"""Selection and fail-closed contracts for scoped native production units."""

import importlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from plan import CHECKS, FAMILIES, check_results, make_plan


ROOT = Path(__file__).resolve().parents[2]
PLUGIN_ROOT = "ai-vision/agent-plugins/"
GROUPS = (
    "codex", "claudecode", "pi", "opencode", "deepseek", "hermes", "kimi",
    "onboarding", "harness",
)


class NativeUnitScopeTests(unittest.TestCase):
    def scope(self):
        # Report the missing implementation as an intentional red assertion,
        # without preventing discovery of the independent planner/gate tests.
        self.assertIsNotNone(
            importlib.util.find_spec("native_unit_scope"),
            "native_unit_scope implementation must provide the scoped unit contract",
        )
        return importlib.import_module("native_unit_scope")

    def test_canonical_groups_are_separate_from_boolean_check_schema(self):
        self.assertEqual(self.scope().UNIT_GROUPS, GROUPS)
        self.assertNotIn("native_unit_groups", CHECKS)
        self.assertFalse(any("native_unit_groups" in checks for checks in FAMILIES.values()))

    def test_each_plugin_selects_itself_and_shared_support_contracts(self):
        scope = self.scope()
        for group in GROUPS[:7]:
            with self.subTest(group=group):
                self.assertEqual(
                    scope.select_unit_groups([PLUGIN_ROOT + group + "/src/example.py"]),
                    [group, "onboarding", "harness"],
                )

    def test_replay_paths_keep_their_production_family(self):
        scope = self.scope()
        for path, group in (
            ("pi/pireplay/src/replay.ts", "pi"),
            ("deepseek/dsh-replay/src/replay.ts", "deepseek"),
            ("kimi/kimihooks/src/bin/kimihooks_replay.rs", "kimi"),
            ("opencode/opencode-replay/src/replay.ts", "opencode"),
        ):
            with self.subTest(path=path):
                self.assertEqual(scope.select_unit_groups([PLUGIN_ROOT + path]),
                                 [group, "onboarding", "harness"])

    def test_mixed_plugin_selection_is_canonical_unique_union(self):
        self.assertEqual(self.scope().select_unit_groups([
            PLUGIN_ROOT + "kimi/kimihooks/src/lib.rs",
            PLUGIN_ROOT + "pi/observ-pi/src/config.ts",
            PLUGIN_ROOT + "codex/cdxhooks/src/lib.rs",
            PLUGIN_ROOT + "pi/pireplay/src/replay.ts",
            "README.md",
        ]), ["codex", "pi", "kimi", "onboarding", "harness"])

    def test_shared_runtime_and_metadata_paths_are_full_scope(self):
        scope = self.scope()
        for path in (
            "shared/collection-mode.mjs", "agentic-otel-common/src/lib.rs",
            "scripts/connect", "scripts/hook_runtime.py", "scripts/build_plugins.py",
            "tests/native_agent_e2e/test_runner.py", "agents.json", "Makefile",
            "package.json", "package-lock.json", "docs/config/common.schema.json",
            "docs/semconv/attribute_groups.yaml",
            "pi/observ-pi/configs/common.config.json",
            "opencode/observ-opencode/configs/cowork.metadata.json",
            "openclaw/observ-openclaw/configs/common.config.json",
        ):
            with self.subTest(path=path):
                self.assertEqual(scope.select_unit_groups([PLUGIN_ROOT + path]), list(GROUPS))

    def test_unknown_ci_and_empty_conservative_scope_run_every_group(self):
        scope = self.scope()
        for paths in (
            ["new-service/app.py"], [".github/workflows/ci.yml"],
            ["scripts/ci/plan.py"], [PLUGIN_ROOT + "new-plugin/src/index.ts"], [],
        ):
            with self.subTest(paths=paths):
                self.assertEqual(scope.select_unit_groups(paths), list(GROUPS))
        self.assertEqual(scope.select_unit_groups([PLUGIN_ROOT + "pi/src/index.ts"],
                                                  force_all=True), list(GROUPS))

    def test_prose_does_not_broaden_a_recognized_runtime_scope(self):
        self.assertEqual(self.scope().select_unit_groups([
            "README.md", PLUGIN_ROOT + "shared/README.md",
            PLUGIN_ROOT + "codex/cdxhooks/src/lib.rs",
        ]), ["codex", "onboarding", "harness"])

    def test_legacy_all_default_and_partial_resolver_are_canonical(self):
        scope = self.scope()
        self.assertEqual(scope.resolve_unit_groups(), list(GROUPS))
        self.assertEqual(scope.resolve_unit_groups("all"), list(GROUPS))
        self.assertEqual(scope.resolve_unit_groups('["kimi", "codex", "harness"]'),
                         ["codex", "kimi", "harness"])
        for group in GROUPS:
            with self.subTest(group=group):
                self.assertEqual(scope.resolve_unit_groups(json.dumps([group])), [group])

    def test_source_daily_and_release_expand_valid_partial_scope(self):
        scope = self.scope()
        for tier, source in (("pr", True), ("daily", False), ("release", False)):
            with self.subTest(tier=tier, source=source):
                self.assertEqual(scope.resolve_unit_groups('["pi"]', tier=tier, source=source),
                                 list(GROUPS))

    def test_invalid_scope_rejected_before_full_scope_override(self):
        scope = self.scope()
        for encoded in (
            "", "[", "null", "false", "1", '"pi"', "{}", "[]",
            '["missing"]', '["pi", "missing"]', '["pi", "pi"]',
            '["pi", 1]', '["pi", null]', '["pi", {}]',
        ):
            for tier, source in (("pr", False), ("pr", True), ("daily", False)):
                with self.subTest(encoded=encoded, tier=tier, source=source):
                    with self.assertRaises(ValueError):
                        scope.resolve_unit_groups(encoded, tier=tier, source=source)
        with self.assertRaises(ValueError):
            scope.resolve_unit_groups("all", tier="unknown")

    def run_cli(self, encoded, *, tier="pr", source=False):
        self.scope()
        with tempfile.TemporaryDirectory(prefix="aiv-native-scope-") as directory:
            output = Path(directory) / "output"
            summary = Path(directory) / "summary"
            env = dict(os.environ, NATIVE_UNIT_SCOPE=encoded, NATIVE_TIER=tier,
                       NATIVE_SOURCE=str(source).lower(), GITHUB_OUTPUT=str(output),
                       GITHUB_STEP_SUMMARY=str(summary))
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts/ci/native_unit_scope.py")],
                cwd=ROOT, env=env, capture_output=True, text=True, timeout=10,
            )
            return result, output.read_text() if output.exists() else "", (
                summary.read_text() if summary.exists() else "")

    def test_cli_exports_validated_group_and_tool_flags(self):
        result, output, summary = self.run_cli('["pi", "harness"]')
        self.assertEqual(result.returncode, 0, result.stderr)
        actual = dict(line.split("=", 1) for line in output.splitlines())
        for group in GROUPS:
            self.assertEqual(actual[group], str(group in {"pi", "harness"}).lower())
        self.assertEqual(actual["node"], "true")
        self.assertEqual(actual["rust"], "false")
        self.assertIn("pi", summary)
        self.assertIn("harness", summary)

    def test_cli_invalid_scope_cannot_emit_successful_selection(self):
        for encoded in ("[]", '["pi", "pi"]', '["missing"]', "{"):
            with self.subTest(encoded=encoded):
                result, output, _ = self.run_cli(encoded)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("pi=true", output)
                self.assertNotIn("harness=true", output)


class NativeScopePlannerGateTests(unittest.TestCase):
    def good_results(self, plan):
        return {job: {"result": "success" if job in ("scope", "metadata") or plan.get(job)
                      else "skipped"} for job in ("scope", "metadata", *FAMILIES)}

    def test_basic_scope_uses_final_profile_selection(self):
        plan = make_plan([PLUGIN_ROOT + "pi/observ-pi/src/index.ts"])
        self.assertIs(plan["native_units"], True)
        self.assertIs(plan["native_source"], False)
        self.assertEqual(plan.get("native_unit_groups"), ["pi", "onboarding", "harness"])
        self.assertEqual(check_results(plan, self.good_results(plan)), [])

    def test_final_source_and_full_profile_keep_full_unit_scope(self):
        for profile in ("integration", "full"):
            plan = make_plan([PLUGIN_ROOT + "pi/observ-pi/src/index.ts"], profile=profile)
            self.assertIs(plan["native_source"], True)
            self.assertEqual(plan.get("native_unit_groups"), list(GROUPS))
        for tier in ("daily", "release"):
            plan = make_plan(["README.md"], tier=tier, profile="full")
            self.assertEqual(plan.get("native_unit_groups"), list(GROUPS))

    def test_no_selected_units_or_source_has_empty_scope(self):
        plan = make_plan(["README.md"])
        self.assertEqual(plan.get("native_unit_groups"), [])
        self.assertEqual(check_results(plan, self.good_results(plan)), [])

    def test_ci_unknown_and_missing_diff_remain_full_scope(self):
        for paths, fallback in (([".github/workflows/ci.yml"], False),
                                (["new-service/app.py"], False), ([], True)):
            plan = make_plan(paths, fallback=fallback)
            self.assertEqual(plan.get("native_unit_groups"), list(GROUPS))

    def test_central_gate_rejects_bad_selected_scope_even_when_jobs_succeeded(self):
        plan = make_plan([PLUGIN_ROOT + "pi/observ-pi/src/index.ts"])
        good = self.good_results(plan)
        for groups in (None, "all", [], ["missing"], ["pi", "pi"], ["pi", 1], ["pi", {}],
                       ["pi"], ["onboarding", "harness"]):
            with self.subTest(groups=groups):
                self.assertTrue(check_results({**plan, "native_unit_groups": groups}, good))
        missing = {key: value for key, value in plan.items() if key != "native_unit_groups"}
        self.assertTrue(check_results(missing, good))

    def test_central_gate_requires_all_groups_for_selected_source(self):
        plan = make_plan([PLUGIN_ROOT + "pi/observ-pi/src/index.ts"], profile="full")
        self.assertTrue(check_results({**plan, "native_unit_groups": ["pi"]},
                                      self.good_results(plan)))

    def test_existing_native_job_gate_fails_for_incomplete_selected_units(self):
        from test_workflow_contracts import run_step, workflow

        document = workflow("native-agent-e2e.yml")
        gate = document["jobs"]["result"]
        self.assertIn("native-source", gate["needs"])
        inputs = dict(tier="pr", units=True, source=False, platform=False,
                      unit_scope='["pi", "onboarding", "harness"]')
        good = {job: {"result": "success" if job == "native-source" else "skipped"}
                for job in gate["needs"]}
        result, _ = run_step(gate["steps"][0], inputs=inputs, needs=good)
        self.assertEqual(result.returncode, 0, result.stderr)
        for status in ("failure", "cancelled", "skipped", None):
            with self.subTest(status=status):
                broken = {**good, "native-source": {"result": status}}
                if status is None:
                    broken.pop("native-source")
                result, _ = run_step(gate["steps"][0], inputs=inputs, needs=broken)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("native-source", result.stderr)


if __name__ == "__main__":
    unittest.main()
