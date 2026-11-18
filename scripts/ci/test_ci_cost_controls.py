"""Exercise deployment scope and the workflows that consume its cost controls."""

import itertools
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
SCOPE = ROOT / "scripts/ci/deployment_assets_scope.py"
WORKFLOWS = ROOT / ".github/workflows"


def workflow(name):
    return yaml.safe_load((WORKFLOWS / name).read_text())


class DeploymentScopeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="aiv-deployment-scope-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "repo"
        self.root.mkdir()
        self.git("init", "-q")
        self.git("config", "user.name", "Scope fixture")
        self.git("config", "user.email", "scope@example.invalid")
        inventory = self.root / "deploy/scripts/package_assets.py"
        inventory.parent.mkdir(parents=True)
        shutil.copyfile(ROOT / "deploy/scripts/package_assets.py", inventory)
        for name in (
            "docs/deployment/README.md",
            "docs/deployment/en/guide.rst",
            "docs/deployment/aivision.vars.example.yaml",
            "deploy/kubernetes/README.md",
            "deploy/kubernetes/lifecycle.md",
            "deploy/kubernetes/helm-roadmap.md",
            "aivision-cli/deploy/kubernetes/README.md",
            "aivision-cli/deploy/kubernetes/lifecycle.md",
            "aivision-cli/deploy/kubernetes/helm-roadmap.md",
        ):
            self.write(name, "baseline\n")
        self.base = self.commit("baseline")

    def git(self, *args):
        return subprocess.check_output(
            ["git", "-C", str(self.root), *args], text=True,
            stderr=subprocess.DEVNULL,
        ).strip()

    def write(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-qm", message)
        return self.git("rev-parse", "HEAD")

    def event(self, head=None):
        return {"pull_request": {"base": {"sha": self.base},
                                 "head": {"sha": head or self.commit("change")}}}

    def invoke(self, event=None, event_name="pull_request", *, raw=None):
        event_path = Path(self.temporary.name) / "event.json"
        event_path.write_text(raw if raw is not None else json.dumps(event))
        output = Path(self.temporary.name) / "output"
        summary = Path(self.temporary.name) / "summary"
        output.unlink(missing_ok=True)
        summary.unlink(missing_ok=True)
        result = subprocess.run(
            [sys.executable, str(SCOPE), "--event", str(event_path),
             "--root", str(self.root)],
            env=dict(os.environ, GITHUB_EVENT_NAME=event_name,
                     PYTHONDONTWRITEBYTECODE="1",
                     GITHUB_OUTPUT=str(output), GITHUB_STEP_SUMMARY=str(summary)),
            capture_output=True, text=True, timeout=15, check=False,
        )
        return result, output.read_text() if output.exists() else "", (
            summary.read_text() if summary.exists() else "")

    def assert_scope(self, event, heavy, *, event_name="pull_request"):
        result, output, summary = self.invoke(event, event_name)
        self.assertEqual(result.returncode, 0, result.stderr)
        decision = json.loads(result.stdout)
        self.assertIs(decision["heavy_checks"], heavy)
        self.assertIn(f"heavy_checks={str(heavy).lower()}\n", output)
        self.assertIn("Deployment asset scope", summary)
        return decision, summary

    def test_existing_unpacked_markdown_and_rst_skip_heavy_checks_with_explicit_summary(self):
        self.write("docs/deployment/README.md", "revised prose\n")
        self.write("docs/deployment/en/guide.rst", "revised prose\n")
        decision, summary = self.assert_scope(self.event(), False)
        self.assertEqual(decision["changed_files"], 2)
        self.assertIn("not run", summary)

    def test_deleted_existing_prose_is_in_the_complete_diff(self):
        (self.root / "docs/deployment/README.md").unlink()
        decision, _ = self.assert_scope(self.event(), False)
        self.assertEqual(decision["changed_files"], 1)

    def test_existing_prose_replaced_by_symlink_or_executable_remains_heavy(self):
        for kind in ("symlink", "executable"):
            with self.subTest(kind=kind):
                self.git("reset", "--hard", self.base)
                path = self.root / "docs/deployment/README.md"
                if kind == "symlink":
                    path.unlink()
                    path.symlink_to("aivision.vars.example.yaml")
                else:
                    path.chmod(0o755)
                self.assert_scope(self.event(), True)

    def test_configuration_and_other_nonprose_remain_heavy(self):
        for name in ("docs/deployment/aivision.vars.example.yaml",
                     "docs/deployment/example.sh", "docs/deployment/diagram.svg"):
            with self.subTest(path=name):
                self.git("reset", "--hard", self.base)
                self.write(name, "runtime change\n")
                self.assert_scope(self.event(), True)

    def test_packaged_kubernetes_markdown_sources_and_destinations_remain_heavy(self):
        for directory, name in itertools.product(
            ("deploy/kubernetes", "aivision-cli/deploy/kubernetes"),
            ("README.md", "lifecycle.md", "helm-roadmap.md"),
        ):
            with self.subTest(directory=directory, name=name):
                self.git("reset", "--hard", self.base)
                self.write(f"{directory}/{name}", "packaged change\n")
                self.assert_scope(self.event(), True)

    def test_canonical_inventory_protects_prose_added_to_packaging_from_either_side(self):
        # If packaging later starts copying deployment prose, the selector must
        # follow its actual inventory rather than a duplicated Markdown list.
        inventory = self.root / "deploy/scripts/package_assets.py"
        inventory.write_text(inventory.read_text() + '\nFILES += (("docs/deployment/README.md", "docs/deployment/en/guide.rst"),)\n')
        self.base = self.commit("package prose")
        for name in ("docs/deployment/README.md", "docs/deployment/en/guide.rst"):
            with self.subTest(path=name):
                self.git("reset", "--hard", self.base)
                self.write(name, "packaged prose change\n")
                self.assert_scope(self.event(), True)

    def test_canonical_inventory_group_protects_future_packaged_prose(self):
        inventory = self.root / "deploy/scripts/package_assets.py"
        inventory.write_text(inventory.read_text() + '\nGROUPS += (("docs/deployment", "compat/docs", ("*.md",)),)\n')
        self.base = self.commit("package prose group")
        self.write("docs/deployment/README.md", "packaged prose change\n")
        self.assert_scope(self.event(), True)

    def test_unknown_and_new_prose_are_conservatively_heavy(self):
        for name in ("new-service/README.md", "docs/deployment/new-guide.md"):
            with self.subTest(path=name):
                self.git("reset", "--hard", self.base)
                self.write(name, "new path\n")
                self.assert_scope(self.event(), True)

    def test_mixed_prose_and_runtime_paths_are_heavy(self):
        self.write("docs/deployment/README.md", "revised prose\n")
        self.write("deploy/docker/compose.yaml", "runtime change\n")
        decision, _ = self.assert_scope(self.event(), True)
        self.assertEqual(decision["changed_files"], 2)

    def test_rename_keeps_old_configuration_and_new_prose_paths(self):
        source = self.root / "docs/deployment/aivision.vars.example.yaml"
        source.rename(self.root / "docs/deployment/moved.md")
        decision, _ = self.assert_scope(self.event(), True)
        self.assertEqual(decision["changed_files"], 2)

    def test_renamed_existing_prose_to_new_name_remains_conservative(self):
        (self.root / "docs/deployment/README.md").rename(
            self.root / "docs/deployment/moved.md")
        decision, _ = self.assert_scope(self.event(), True)
        self.assertEqual(decision["changed_files"], 2)

    def test_manual_missing_bad_empty_and_unavailable_diffs_stay_heavy(self):
        for event, event_name in (
            ({}, "workflow_dispatch"), ({}, "pull_request"),
            ({"pull_request": {"base": {"sha": "bad"}, "head": {"sha": "bad"}}}, "pull_request"),
            ({"pull_request": {"base": {"sha": "a" * 40}, "head": {"sha": "b" * 40}}}, "pull_request"),
            (self.event(head=self.base), "pull_request"),
        ):
            with self.subTest(event=event, event_name=event_name):
                self.assert_scope(event, True, event_name=event_name)

    def test_malformed_event_falls_back_to_heavy_checks(self):
        result, output, _ = self.invoke(raw="not JSON")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("heavy_checks=true\n", output)

    def test_broken_inventory_is_a_failed_scope_without_a_success_output(self):
        self.write("docs/deployment/README.md", "revised prose\n")
        event = self.event()
        self.write("deploy/scripts/package_assets.py", "raise RuntimeError('broken inventory')\n")
        result, output, _ = self.invoke(event)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("deployment asset scope failed", result.stderr)
        self.assertEqual(output, "")


class WorkflowCostControlTests(unittest.TestCase):
    def test_deployment_assets_keeps_identity_events_and_scope_dependencies(self):
        document = workflow("deployment-assets.yml")
        self.assertEqual(set(document["jobs"]), {"assets"})
        events = document.get("on", document.get(True))
        self.assertEqual(set(events), {"workflow_dispatch", "pull_request"})
        paths = set(events["pull_request"]["paths"])
        self.assertTrue({"deploy/**", "aivision-cli/**", "docs/deployment/**",
                         "scripts/ci/deployment_assets_scope.py", "scripts/ci/plan.py",
                         "scripts/ci/test_ci_cost_controls.py"} <= paths)

    def test_scope_is_before_expensive_steps_and_failures_are_not_ignored(self):
        job = workflow("deployment-assets.yml")["jobs"]["assets"]
        self.assertNotIn("continue-on-error", job)
        steps = job["steps"]
        self.assertTrue(steps[0]["uses"].startswith("actions/checkout@"))
        self.assertEqual(steps[0].get("with", {}).get("fetch-depth"), 0)
        self.assertEqual(steps[1]["id"], "scope")
        self.assertIn("deployment_assets_scope.py", steps[1]["run"])
        self.assertNotIn("continue-on-error", steps[1])
        self.assertNotIn("if", steps[1])
        # All setup, downloads, Go, schemas and Compose consumers must obey the
        # same decision. An absent output cannot select any expensive consumer.
        for step in steps[2:]:
            with self.subTest(step=step.get("name", step.get("uses"))):
                self.assertEqual(step.get("if"), "steps.scope.outputs.heavy_checks == 'true'")

    def test_selected_assets_keep_package_schema_and_compose_validation(self):
        steps = workflow("deployment-assets.yml")["jobs"]["assets"]["steps"]
        runs = "\n".join(step.get("run", "") for step in steps)
        for command in ("package_assets.py --check", "renderproduct --check",
                        "unittest discover -s deploy/tests", "sha256sum --check",
                        "extract_crd_schemas.py", "kubeconform' -strict",
                        "--profile bootstrap config --quiet"):
            with self.subTest(command=command):
                self.assertIn(command, runs)

    def test_compose_serializes_cases_and_runs_without_cancelling_qualification(self):
        document = workflow("compose-deploy-smoke.yml")
        self.assertEqual(document.get("concurrency"), {
            "group": "product-compose-lifecycle", "cancel-in-progress": False})
        lifecycle = document["jobs"]["lifecycle"]
        self.assertEqual(lifecycle["strategy"]["max-parallel"], 1)
        self.assertIs(lifecycle["strategy"]["fail-fast"], False)
        matrix = lifecycle["strategy"]["matrix"]
        self.assertEqual(set(itertools.product(matrix["backend"], matrix["mode"])), {
            ("oceanbase", "managed"), ("oceanbase", "external"),
            ("seekdb", "managed"), ("seekdb", "external"),
        })
        self.assertEqual(lifecycle["steps"][0]["name"], "Check Docker capacity before build work")

    def test_compose_capacity_check_rejects_small_runner_before_any_lifecycle(self):
        step = workflow("compose-deploy-smoke.yml")["jobs"]["lifecycle"]["steps"][0]
        with tempfile.TemporaryDirectory(prefix="aiv-compose-capacity-") as directory:
            executable = Path(directory) / "docker"
            executable.write_text(
                "#!/usr/bin/env python3\nimport json, os, sys\n"
                "assert sys.argv[1:] == ['info', '--format', '{{json .}}']\n"
                "print(json.dumps({'MemTotal': int(os.environ['TEST_DOCKER_MEMORY'])}))\n")
            executable.chmod(0o755)
            for gib, expected in ((8, 1), (13, 1), (14, 0)):
                with self.subTest(gib=gib):
                    result = subprocess.run(
                        ["bash", "-e", "-o", "pipefail", "-c", step["run"]],
                        env=dict(os.environ, PATH=directory + os.pathsep + os.environ["PATH"],
                                 TEST_DOCKER_MEMORY=str(gib * 1024 ** 3)),
                        capture_output=True, text=True, timeout=10, check=False)
                    self.assertEqual(result.returncode, expected, result.stderr)
                    if expected:
                        self.assertIn("at least 14 GiB", result.stderr)


if __name__ == "__main__":
    unittest.main()
