"""Behavioral coverage for scope selection, complete diffs, and fail-closed gates."""

import subprocess
import tempfile
import unittest
from pathlib import Path

from plan import CHECKS, FAMILIES, changed_paths, check_results, make_plan


def full_plan(paths, **kwargs):
    return make_plan(paths, profile="full", **kwargs)


class PlanTests(unittest.TestCase):
    def test_docs_pr_avoids_runtime_even_inside_component(self):
        plan = full_plan(
            [
                "GOVERNANCE.md",
                "ai-vision/vfe/AGENTS.md",
                "agentlens/docs/api.md",
                ".github/ISSUE_TEMPLATE/bug.yml",
            ]
        )
        self.assertFalse(any(plan[key] for key in CHECKS))

    def test_frontend_does_not_duplicate_full_stack(self):
        plan = full_plan(["ai-vision/vfe/src/pages/about.tsx"])
        self.assertEqual([key for key in CHECKS if plan[key]], ["frontend"])

    def test_platform_browser_paths_select_and_enforce_actual_platform_jobs(self):
        import json
        from test_workflow_contracts import call_inputs, render, run_step, workflow

        called = workflow("framework-e2e.yml")
        gate = called["jobs"]["result"]
        caller = workflow("ci.yml")["jobs"]["framework"]
        for path in (
            "ai-vision/vfe/tests/e2e-platform/collection.spec.ts",
            "ai-vision/vfe/tests/e2e-platform/platform.fixture.ts",
            "ai-vision/vfe/playwright.platform.config.ts",
        ):
            with self.subTest(path=path):
                plan = full_plan([path])
                self.assertEqual(plan["tier"], "pr")
                self.assertEqual(
                    {key for key in CHECKS if plan[key]},
                    {"frontend", "framework_platform"},
                )
                inputs, _ = call_inputs(caller, plan)
                step = gate["steps"][0]
                selected = json.loads(render(step["env"]["SELECTED_JSON"], inputs=inputs))
                self.assertEqual(
                    {job for job, required in selected.items() if required},
                    {"frontend", "frontend-browser", "browser-platform", "docker-platform"},
                )
                good = {job: {"result": "success" if selected[job] else "skipped"}
                        for job in gate["needs"]}
                result, _ = run_step(step, inputs=inputs, needs=good)
                self.assertEqual(result.returncode, 0, result.stderr)
                for job in ("frontend-browser", "browser-platform", "docker-platform"):
                    for status in ("skipped", "failure", "cancelled", None):
                        with self.subTest(job=job, status=status):
                            needs = {**good, job: {"result": status}}
                            if status is None:
                                needs.pop(job)
                            result, _ = run_step(step, inputs=inputs, needs=needs)
                            self.assertNotEqual(result.returncode, 0)
                            self.assertIn(job, result.stderr)

    def test_backend_has_database_and_api_contracts(self):
        plan = full_plan(["ai-vision/aivision-py-be/apps/core/views/spaces.py"])
        for key in (
            "backend",
            "framework_database",
            "framework_contracts",
            "diagnostics_application",
        ):
            self.assertTrue(plan[key], key)
        self.assertFalse(plan["native_platform"])

    def test_schema_changes_expand_cross_component_validation(self):
        plan = full_plan(
            ["ai-vision/aivision-py-be/apps/core/migrations/0042_change.py"]
        )
        for key in (
            "framework_database",
            "framework_platform",
            "diagnostics_database",
            "diagnostics_platform",
            "native_source",
            "native_platform",
        ):
            self.assertTrue(plan[key], key)

    def test_agentlens_and_collector_are_not_uncovered_components(self):
        plan = full_plan(
            [
                "agentlens/src/agentlens/kernel/runner.py",
                "antcollector/processor/spaceauthprocessor/processor.go",
            ]
        )
        for key in (
            "diagnostics_database",
            "diagnostics_platform",
            "collector",
            "native_source",
        ):
            self.assertTrue(plan[key], key)

    def test_adr_prose_runs_checker_without_app_stack(self):
        plan = full_plan(["agentlens/docs/adr/trace/example.md"])
        self.assertEqual([key for key in CHECKS if plan[key]], ["adr"])

    def test_deployment_changes_require_real_smoke(self):
        for path in (
            "aivision-cli/blueprints/aivision.yaml",
            "aivision-cli/blueprints/aivision-source.yaml",
            "docs/deployment/aivision.vars.example.yaml",
            "deploy/blueprints/aivision.yaml",
            "deploy/bootstrap/seekdb.sh",
            "aivision-cli/internal/deploy/compose/lifecycle.go",
            "deploy/tests/compose_cli_smoke.py",
            "deploy/kubernetes/base/gateway.yaml",
            "deploy/components.json",
            "deploy/tests/seekdb_deploy_smoke.py",
        ):
            plan = full_plan([path])
            self.assertTrue(plan["deployment"], path)
            self.assertTrue(plan["cli"], path)

    def test_canonical_deployment_sources_use_existing_deployment_scope(self):
        canonical = full_plan(["deploy/blueprints/aivision.yaml"])
        compatibility = full_plan(["aivision-cli/blueprints/aivision.yaml"])
        self.assertEqual(
            {key for key in CHECKS if canonical[key]},
            {key for key in CHECKS if compatibility[key]},
        )
        self.assertFalse(any(full_plan(["deploy/README.md"])[key] for key in CHECKS))

    def test_cli_formatting_change_does_not_run_deployment(self):
        plan = full_plan(["aivision-cli/internal/query/render.go"])
        self.assertEqual([key for key in CHECKS if plan[key]], ["cli"])

    def test_single_file_django_models_are_also_schema_changes(self):
        self.assertTrue(
            full_plan(["ai-vision/aivision-py-be/apps/diagnose/models.py"])[
                "diagnostics_platform"
            ]
        )

    def test_requirements_are_not_skipped_as_text(self):
        plan = full_plan(
            ["ai-vision/agent-plugins/tests/framework_e2e/requirements-lf4.txt"]
        )
        self.assertTrue(plan["framework_contracts"])
        self.assertTrue(plan["framework_platform"])

    def test_shared_harness_and_plugins_select_their_consumers(self):
        for path in (
            "ai-vision/agent-plugins/tests/framework_e2e/docker_platform.py",
            "ai-vision/agent-plugins/codex/src/main.rs",
            "ai-vision/agent-plugins/agentic-otel-common/src/lib.rs",
            "ai-vision/agent-plugins/deepseek/observ-dsh/src/index.ts",
        ):
            plan = full_plan([path])
            self.assertTrue(plan["native_platform"], path)
            self.assertTrue(plan["diagnostics_platform"], path)

    def test_known_launch_metadata_does_not_start_runtime_suites(self):
        plan = full_plan(["LICENSE", "NOTICE", ".github/CODEOWNERS"])
        self.assertFalse(any(plan[key] for key in CHECKS))

    def test_unknown_paths_and_unavailable_diff_fail_conservatively(self):
        for paths, fallback in (
            (["new-service/app.py"], False),
            ([], True),
            ([".github/workflows/new-ci.yml"], False),
        ):
            plan = full_plan(paths, fallback=fallback)
            self.assertTrue(all(plan[key] for key in CHECKS))
            self.assertEqual(plan["tier"], "pr")

    def test_release_version_and_branch_escalate_even_docs(self):
        for paths, branch in (
            (["VERSION"], "main"),
            (["README.md"], "release/0.1.0"),
            ([], "hotfix/0.0.2"),
        ):
            plan = full_plan(paths, base_branch=branch)
            self.assertEqual(plan["tier"], "release")
            self.assertTrue(all(plan[key] for key in CHECKS))
        for head in ("release/0.1.0", "hotfix/0.0.2"):
            self.assertEqual(
                full_plan(["README.md"], base_branch="main", head_branch=head)["tier"],
                "release",
            )

    def test_full_tiers_never_skip_due_to_docs(self):
        for tier in ("daily", "release"):
            self.assertTrue(
                all(full_plan(["README.md"], tier=tier)[key] for key in CHECKS)
            )
        with self.assertRaises(ValueError):
            full_plan([], tier="skip")

    def test_gate_rejects_skipped_missing_cancelled_or_failed_required_jobs(self):
        plan = full_plan(["ai-vision/vfe/src/App.tsx"])
        good = {
            job: {
                "result": "success"
                if job in ("scope", "metadata", "framework")
                else "skipped"
            }
            for job in ("scope", "metadata", *FAMILIES)
        }
        self.assertEqual(check_results(plan, good), [])
        for result in ("failure", "skipped", "cancelled", None):
            needs = {**good, "framework": {"result": result}}
            self.assertTrue(check_results(plan, needs))
        self.assertTrue(check_results({}, good))
        self.assertTrue(check_results(plan, {**good, "scope": {"result": "failure"}}))
        self.assertTrue(
            check_results(
                {**plan, "framework": False},
                {**good, "framework": {"result": "skipped"}},
            )
        )


class ProfileTests(unittest.TestCase):
    BASIC = {
        "frontend", "backend", "cli", "framework_contracts", "framework_database",
        "diagnostics_portable", "diagnostics_application", "diagnostics_database",
        "native_units", "collector", "adr",
    }
    INTEGRATION = {
        "framework_platform", "diagnostics_platform", "native_source",
        "native_platform", "deployment",
    }

    def selected(self, plan):
        return {key for key in CHECKS if plan[key]}

    def good_results(self, plan):
        return {job: {"result": "success" if job in ("scope", "metadata") or plan.get(job) else "skipped"}
                for job in ("scope", "metadata", *FAMILIES)}

    def test_default_basic_defers_real_native_runs_but_keeps_native_units(self):
        plan = make_plan(["ai-vision/agent-plugins/pi/observ-pi/src/index.ts"])
        self.assertEqual(plan["profile"], "basic")
        self.assertEqual(plan["tier"], "pr")
        self.assertEqual(self.selected(plan), {"native_units"})
        self.assertEqual(plan["deferred_checks"], ["native_source", "native_platform"])
        self.assertTrue(plan["native"])
        self.assertEqual(check_results(plan, self.good_results(plan)), [])

    def test_basic_keeps_frontend_browser_fixtures_and_defers_only_platform(self):
        plan = make_plan(["ai-vision/vfe/tests/e2e-platform/collection.spec.ts"])
        self.assertEqual(self.selected(plan), {"frontend"})
        self.assertEqual(plan["deferred_checks"], ["framework_platform"])
        self.assertTrue(plan["framework"])

    def test_unknown_ci_config_and_missing_diff_select_all_basic_not_empty_green(self):
        for paths, fallback in ((["new-service/app.py"], False),
                                ([".github/workflows/ci.yml"], False), ([], True)):
            with self.subTest(paths=paths, fallback=fallback):
                plan = make_plan(paths, fallback=fallback)
                self.assertEqual(self.selected(plan), self.BASIC)
                self.assertEqual(set(plan["deferred_checks"]), self.INTEGRATION)
                self.assertEqual(check_results(plan, self.good_results(plan)), [])

    def test_integration_selects_only_deployment_platform_and_real_native(self):
        plan = make_plan([".github/workflows/ci.yml"], profile="integration")
        self.assertEqual(self.selected(plan), self.INTEGRATION)
        self.assertEqual(set(plan["deferred_checks"]), self.BASIC)
        self.assertEqual(check_results(plan, self.good_results(plan)), [])
        native = make_plan(["ai-vision/agent-plugins/pi/src/index.ts"], profile="integration")
        self.assertEqual(self.selected(native), {"native_source", "native_platform"})
        self.assertEqual(native["deferred_checks"], ["native_units"])

    def test_full_preserves_requested_scope_and_requests_units_for_native(self):
        plan = make_plan(["ai-vision/agent-plugins/pi/src/index.ts"], profile="full")
        self.assertEqual(self.selected(plan), {"native_units", "native_source", "native_platform"})
        self.assertEqual(plan["deferred_checks"], [])
        self.assertEqual(check_results(plan, self.good_results(plan)), [])

    def test_basic_version_and_release_branches_do_not_upgrade_to_release(self):
        for branch in ("main", "release/0.1.0", "hotfix/0.0.2"):
            with self.subTest(branch=branch):
                plan = make_plan(["VERSION"], base_branch=branch)
                self.assertEqual(plan["tier"], "pr")
                self.assertEqual(self.selected(plan), self.BASIC)
                self.assertEqual(set(plan["deferred_checks"]), self.INTEGRATION)

    def test_non_full_profiles_reject_daily_and_release_instead_of_running_matrices(self):
        for profile in ("basic", "integration"):
            for tier in ("daily", "release"):
                with self.subTest(profile=profile, tier=tier):
                    with self.assertRaisesRegex(ValueError, "pr"):
                        make_plan([], profile=profile, tier=tier)
        with self.assertRaises(ValueError):
            make_plan([], profile="unknown")

    def test_required_profile_jobs_fail_on_skip_missing_cancel_and_failure(self):
        for profile in ("basic", "integration", "full"):
            plan = make_plan([".github/workflows/ci.yml"], profile=profile)
            good = self.good_results(plan)
            for job in FAMILIES:
                if not plan[job]:
                    continue
                for status in ("skipped", "failure", "cancelled", None):
                    with self.subTest(profile=profile, job=job, status=status):
                        bad = {**good, job: {"result": status}}
                        if status is None:
                            bad.pop(job)
                        self.assertTrue(check_results(plan, bad))

    def test_gate_rejects_checks_outside_profile_even_if_family_succeeded(self):
        for profile, check in (("basic", "framework_platform"),
                               ("integration", "native_units")):
            plan = make_plan([".github/workflows/ci.yml"], profile=profile)
            bad = {**plan, check: True}
            self.assertTrue(check_results(bad, self.good_results(bad)))
        plan = make_plan([], profile="full")
        for patch in ({"profile": "unknown"}, {"profile": None},
                      {"deferred_checks": ["native_source"]},
                      {"deferred_checks": "native_source"},
                      {"native_units": 1}):
            with self.subTest(patch=patch):
                self.assertTrue(check_results({**plan, **patch}, self.good_results(plan)))

    def test_gate_rejects_invalid_deferred_and_profile_tier_combinations(self):
        plan = make_plan([".github/workflows/ci.yml"])
        for deferred in (None, ["missing_check"], ["frontend"],
                         ["native_source", "native_source"]):
            with self.subTest(deferred=deferred):
                self.assertTrue(check_results({**plan, "deferred_checks": deferred}, self.good_results(plan)))
        self.assertTrue(check_results({**plan, "tier": "release"}, self.good_results(plan)))

    def test_cli_gate_identity_rejects_an_integration_plan_in_a_basic_required_check(self):
        import json
        import os
        import sys
        command = [sys.executable, str(Path(__file__).with_name("plan.py")), "--check-results"]
        for expected, profile, succeeds in (
            (("basic", "full"), "basic", True),
            (("basic", "full"), "full", True),
            (("basic", "full"), "integration", False),
            (("integration",), "integration", True),
            (("integration",), "basic", False),
            (("integration",), "full", False),
        ):
            with self.subTest(expected=expected, profile=profile):
                plan = make_plan([".github/workflows/ci.yml"], profile=profile)
                flags = [item for value in expected for item in ("--expected-profile", value)]
                result = subprocess.run([*command, *flags],
                    env={**os.environ, "CI_PLAN": json.dumps(plan),
                         "CI_NEEDS": json.dumps(self.good_results(plan))},
                    text=True, capture_output=True)
                self.assertEqual(result.returncode == 0, succeeds, result.stdout + result.stderr)
                if not succeeds:
                    self.assertIn("profile", result.stdout.lower())

    def test_cli_defaults_to_basic_and_rejects_non_pr_before_reading_event(self):
        import json
        import os
        import sys
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            event = root / "event.json"
            event.write_text(json.dumps({"ref": "refs/heads/feature/basic"}))
            env = {**os.environ, "GITHUB_EVENT_NAME": "workflow_dispatch",
                   "GITHUB_OUTPUT": str(root / "output"),
                   "GITHUB_STEP_SUMMARY": str(root / "summary")}
            command = [sys.executable, str(Path(__file__).with_name("plan.py"))]
            result = subprocess.run([*command, "--event", str(event)], env=env,
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            plan = json.loads(result.stdout)
            self.assertEqual(plan["profile"], "basic")
            self.assertEqual(self.selected(plan), self.BASIC)
            self.assertEqual(set(plan["deferred_checks"]), self.INTEGRATION)
            summary = (root / "summary").read_text()
            self.assertIn("Deferred", summary)
            self.assertIn("- native_source: deferred (not run by this profile)", summary)
            self.assertNotIn("native_source: not applicable", summary)
            for profile, tier, selected, deferred in (
                ("integration", "pr", self.INTEGRATION, self.BASIC),
                ("full", "release", self.BASIC | self.INTEGRATION, set()),
            ):
                with self.subTest(profile=profile, tier=tier):
                    explicit = subprocess.run(
                        [*command, "--profile", profile, "--tier", tier,
                         "--event", str(event)],
                        env=env, text=True, capture_output=True,
                    )
                    self.assertEqual(explicit.returncode, 0, explicit.stderr)
                    actual = json.loads(explicit.stdout)
                    self.assertEqual(actual["profile"], profile)
                    self.assertEqual(actual["tier"], tier)
                    self.assertEqual(self.selected(actual), selected)
                    self.assertEqual(set(actual["deferred_checks"]), deferred)
            bad = subprocess.run([*command, "--profile", "basic", "--tier", "daily",
                                  "--event", str(root / "absent.json")],
                                 env=env, text=True, capture_output=True)
            self.assertNotEqual(bad.returncode, 0)
            self.assertIn("pr", bad.stderr)
            self.assertNotIn("FileNotFoundError", bad.stderr)


class GitDiffTests(unittest.TestCase):
    def test_complete_diff_keeps_removed_runtime_path_after_move_to_docs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            def git(*args):
                return subprocess.check_output(
                    ["git", "-C", str(root), *args], text=True
                ).strip()

            git("init", "-q")
            git("config", "user.name", "Test")
            git("config", "user.email", "test@example.invalid")
            source = root / "ai-vision/vfe/src/app.ts"
            source.parent.mkdir(parents=True)
            source.write_text("export const version = 1\n")
            git("add", ".")
            git("commit", "-qm", "baseline")
            before = git("rev-parse", "HEAD")
            (root / "docs").mkdir()
            source.rename(root / "docs/archived.ts")
            # More than GitHub's path-filter limit must not conceal code changes.
            for number in range(350):
                (root / f"docs/{number}.md").write_text("Documentation\n")
            git("add", "-A")
            git("commit", "-qm", "move and docs")
            after = git("rev-parse", "HEAD")
            for event, name in (
                ({"before": before, "after": after}, "push"),
                (
                    {"pull_request": {"base": {"sha": before}, "head": {"sha": after}}},
                    "pull_request",
                ),
            ):
                paths, fallback = changed_paths(event, name, root=root)
                self.assertFalse(fallback)
                self.assertGreater(len(paths), 300)
                self.assertTrue(make_plan(paths)["frontend"])

    def test_missing_or_invalid_sha_requests_conservative_coverage(self):
        self.assertEqual(
            changed_paths({"before": "0" * 40, "after": "a" * 40}, "push"), ([], True)
        )
        self.assertEqual(
            changed_paths({"before": "--help", "after": "a" * 40}, "push"), ([], True)
        )


if __name__ == "__main__":
    unittest.main()
