"""Execute workflow gates and exercise the planner-to-workflow boundary."""

import fnmatch
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml
from plan import CHECKS, FAMILIES, make_plan

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github/workflows"
EXPRESSION = re.compile(r"\$\{\{\s*(.*?)\s*\}\}")


def workflow(name):
    return yaml.safe_load((WORKFLOWS / name).read_text())


def triggers(document):
    # PyYAML's YAML 1.1 loader treats the Actions `on` key as a boolean.
    return document.get("on", document.get(True))


def render(value, *, inputs=None, needs=None, plan=None):
    """Substitute only the Actions data expressions used by these real gates."""
    inputs, needs = inputs or {}, needs or {}

    def replace(match):
        expression = match[1]
        name_choice = re.fullmatch(r"inputs\.profile == '([^']+)' && '([^']+)' \|\| '([^']+)'", expression)
        if name_choice:
            expected, matched, other = name_choice.groups()
            return matched if inputs.get("profile", "") == expected else other
        conjunction = re.fullmatch(r"inputs\.([a-z_]+) && !inputs\.([a-z_]+)", expression)
        if conjunction:
            left, right = (inputs[key] for key in conjunction.groups())
            if type(left) is not bool or type(right) is not bool:
                raise AssertionError("Scope inputs must be typed booleans")
            return json.dumps(left and not right)
        disjunction = re.fullmatch(r"inputs\.([a-z_]+) \|\| inputs\.([a-z_]+)", expression)
        if disjunction:
            left, right = (inputs[key] for key in disjunction.groups())
            if type(left) is not bool or type(right) is not bool:
                raise AssertionError("Scope inputs must be typed booleans")
            return json.dumps(left or right)
        if expression.startswith("inputs."):
            value = inputs[expression.removeprefix("inputs.")]
            return json.dumps(value) if isinstance(value, bool) else value
        if expression == "toJSON(needs)":
            return json.dumps(needs)
        if expression == "needs.scope.outputs.plan || '{}'":
            return json.dumps(plan or {})
        result = re.fullmatch(r"needs\.([\w-]+)\.result", expression)
        if result:
            return needs.get(result[1], {}).get("result", "")
        raise AssertionError("Unhandled gate expression: " + expression)

    return EXPRESSION.sub(replace, value)


def run_step(step, *, inputs=None, needs=None, plan=None):
    with tempfile.TemporaryDirectory(prefix="aivision-workflow-contract-") as directory:
        summary = Path(directory) / "summary.md"
        env = dict(os.environ, GITHUB_STEP_SUMMARY=str(summary))
        env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env["PATH"]
        env.update({key: render(value, inputs=inputs, needs=needs, plan=plan)
                    for key, value in step.get("env", {}).items()})
        result = subprocess.run(
            ["bash", "-e", "-o", "pipefail", "-c", step["run"]],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=10, check=False,
        )
        return result, summary.read_text() if summary.exists() else ""


def call_inputs(job, plan):
    """Resolve the caller's actual input mappings against a generated plan."""
    called = workflow(Path(job["uses"]).name)
    declarations = triggers(called)["workflow_call"]["inputs"]
    inputs = {name: declaration["default"] for name, declaration in declarations.items()
              if "default" in declaration}
    keys = set()
    for name, expression in job.get("with", {}).items():
        if expression == "${{ needs.scope.outputs.tier }}":
            inputs[name] = plan["tier"]
            continue
        if name == "sdk_coverage":
            if expression != "${{ needs.scope.outputs.tier == 'daily' && inputs.sdk_coverage || 'full' }}":
                raise AssertionError("Only the Daily CI tier may select the SDK smoke profile")
            if declarations[name]["type"] != "string" or inputs.get(name) != "full":
                raise AssertionError("The SDK coverage fallback must be full")
            continue
        if expression == "${{ inputs.public_platform_images }}":
            if name != "public_platform_images" or declarations[name]["type"] != "string":
                raise AssertionError("Candidate fixture override must be a typed workflow input")
            continue
        if expression == "${{ toJSON(fromJSON(needs.scope.outputs.plan).native_unit_groups) }}":
            if declarations[name]["type"] != "string":
                raise AssertionError("Native unit scope must be a JSON string input")
            inputs[name] = json.dumps(plan["native_unit_groups"])
            continue
        match = re.fullmatch(r"\$\{\{ fromJSON\(needs\.scope\.outputs\.plan\)\.([a-z_]+) \}\}", expression)
        if not match or match[1] not in CHECKS or type(plan[match[1]]) is not bool:
            raise AssertionError("Caller must pass a typed plan boolean: " + expression)
        inputs[name] = plan[match[1]]
        keys.add(match[1])
    return inputs, keys


def caller_selected(job, plan):
    match = re.fullmatch(r"needs\.scope\.outputs\.([a-z]+) == 'true'", job["if"])
    if not match or match[1] not in FAMILIES:
        raise AssertionError("Caller must select a planned test family: " + job["if"])
    return plan[match[1]]


class WorkflowGateTests(unittest.TestCase):
    def test_daily_plan_uses_original_run_date_across_reruns_and_forwards_selection(self):
        images = workflow("release-images.yml")["jobs"]
        step = next(step for step in images["prepare"]["steps"] if step.get("id") == "plan")
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            root = parent / "source"
            (root / "scripts/ci").mkdir(parents=True)
            (root / "VERSION").write_text("0.0.2\n")
            (root / "scripts/ci/release_plan.py").write_text((ROOT / "scripts/ci/release_plan.py").read_text())
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            subprocess.run(["git", "-C", str(root), "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture"], check=True)
            sha = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
            tools = parent / "tools"
            tools.mkdir()
            gh = tools / "gh"
            gh.write_text(f"#!{sys.executable}\nimport os, sys\n"
                          "arguments = sys.argv[1:]\n"
                          "if arguments == ['api', 'repos/antgroup/ai-vision', '--jq', '.visibility']:\n"
                          "    print('public')\n"
                          "else:\n"
                          "    assert arguments == ['api', 'repos/antgroup/ai-vision/actions/runs/123', '--jq', '.created_at']\n"
                          "    print(os.environ['FIXTURE_CREATED_AT'])\n")
            gh.chmod(0o700)
            for index, (attempt, selected, revision, created, expected) in enumerate((
                ("1", "", "1", "2000-01-01T15:59:59Z", "0.0.2-daily.20000101"),
                ("2", "", "1", "2000-01-01T15:59:59Z", "0.0.2-daily.20000101"),
                ("2", "", "1", "2000-01-01T16:00:00Z", "0.0.2-daily.20000102"),
                ("2", "20261021", "2", "2000-01-01T15:59:59Z", "0.0.2-daily.20261021.2"),
            )):
                runtime = parent / str(index)
                runtime.mkdir()
                env = dict(os.environ, PATH=str(tools) + os.pathsep + str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"],
                    DEFAULT_BRANCH="main", INPUT_TAG="trial-fixture", INPUT_CANDIDATE_CHANNEL="daily",
                    INPUT_SNAPSHOT_DATE=selected, INPUT_DAILY_REVISION=revision, GH_TOKEN="fixture-token",
                    GITHUB_SHA=sha, GITHUB_EVENT_NAME="workflow_dispatch", GITHUB_REF="refs/heads/fixture",
                    GITHUB_RUN_ID="123", GITHUB_RUN_NUMBER="999", GITHUB_RUN_ATTEMPT=attempt,
                    GITHUB_REPOSITORY="antgroup/ai-vision", PUBLISH="false",
                    RUNNER_TEMP=str(runtime), GITHUB_OUTPUT=str(runtime / "outputs"),
                    FIXTURE_CREATED_AT=created)
                command = step["run"]
                result = subprocess.run(["bash", "-e", "-o", "pipefail", "-c", command], cwd=root,
                                        env=env, capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                daily = json.loads((runtime / "release-plan/release-plan.json").read_text())
                self.assertEqual(daily["product_version"], expected)
                self.assertEqual(daily["build_id"], "123." + attempt)
                self.assertEqual(daily["channel"], "daily")
            for created in ("null", "2000-01-01T15:59:59", "2000-02-30T00:00:00Z", "2000-01-01X00:00:00Z"):
                with self.subTest(created=created):
                    env.update(FIXTURE_CREATED_AT=created, INPUT_SNAPSHOT_DATE="")
                    result = subprocess.run(["bash", "-e", "-o", "pipefail", "-c", command], cwd=root,
                                            env=env, capture_output=True, text=True, timeout=10)
                    self.assertNotEqual(result.returncode, 0)

    def test_formal_release_requires_both_personal_bundles_and_final_receipt(self):
        document = workflow("release-images.yml")
        self.assertIs(triggers(document)["workflow_dispatch"]["inputs"]["release_preview"]["default"], False)
        jobs = document["jobs"]
        self.assertEqual(jobs["prepare"]["outputs"]["personal_edition"],
                         "${{ steps.plan.outputs.personal_edition }}")
        self.assertIn("needs.prepare.outputs.personal_edition == 'true'", jobs["personal"]["if"])
        self.assertNotIn("channel == 'daily'", jobs["personal"]["if"])
        self.assertEqual({row["architecture"] for row in jobs["personal"]["strategy"]["matrix"]["include"]},
                         {"amd64", "arm64"})
        self.assertIn("cli-smoke-${{ github.run_attempt }}-windows-x64", str(jobs["personal-acceptance"]["steps"]))
        self.assertIn("needs.personal.result == 'success'", jobs["personal-acceptance"]["if"])
        for name in ("preflight", "publish-stable"):
            self.assertIn("personal-acceptance", jobs[name]["needs"])
            self.assertIn("!inputs.release_preview", jobs[name]["if"])

    def test_stable_dependency_comes_from_the_versioned_lock(self):
        lock = json.loads((ROOT / "deploy/config/release-dependencies.json").read_text())
        self.assertRegex(lock["product_version"], r"^\d+\.\d+\.\d+$")
        self.assertRegex(lock["signoz"]["image"], r"^ghcr\.io/ob-labs/signoz-ob@sha256:[a-f0-9]{64}$")
        self.assertRegex(lock["signoz"]["source_sha"], r"^[a-f0-9]{40}$")
        prepare = workflow("release-images.yml")["jobs"]["prepare"]
        pin = next(step for step in prepare["steps"] if step.get("name") == "Pin SigNoz before building any product artifact")
        self.assertIn("deploy/config/release-dependencies.json", pin["run"])
        self.assertIn("AIVISION_SIGNOZ_IMAGE", pin["env"]["DEFAULT_SIGNOZ"])

    def test_public_daily_runs_full_source_checks_with_verified_candidate_images(self):
        jobs = workflow("release-images.yml")["jobs"]
        self.assertEqual(jobs["prepare"]["if"], "github.repository == 'antgroup/ai-vision'")
        self.assertNotIn("source-checks", jobs)
        self.assertNotIn("include_openshell", triggers(workflow("release-images.yml"))["workflow_dispatch"]["inputs"])
        full = jobs["public-source-checks"]
        self.assertEqual(full["needs"], ["prepare", "public-registry"])
        self.assertEqual(full["with"]["profile"], "full")
        self.assertEqual(full["with"]["public_platform_images"],
                         "${{ needs.public-registry.outputs.platform_images }}")
        self.assertEqual(full["with"]["public_deploy_images"],
                         "${{ needs.public-registry.outputs.deployment_images }}")
        self.assertIn("verify", jobs["public-registry"]["steps"][-1]["run"])
        projection = jobs["public-registry"]["steps"][-1]["run"]
        self.assertIn("source-fixtures --manifest release/release-manifest.json)", projection)
        self.assertIn("source-fixtures --manifest release/release-manifest.json --use-data-backend)", projection)
        self.assertIn("public-source-checks", jobs["acceptance"]["needs"])
        self.assertIn("needs.public-source-checks.result == 'success'", jobs["acceptance"]["if"])
        self.assertNotIn("skipped", jobs["acceptance"]["if"])
        ci = workflow("ci.yml")["jobs"]
        for family in ("framework", "diagnostics", "native"):
            with self.subTest(family=family):
                self.assertEqual(ci[family]["with"]["public_platform_images"],
                                 "${{ inputs.public_platform_images }}")
        self.assertEqual(ci["deployment"]["with"]["public_platform_images"],
                         "${{ inputs.public_deploy_images }}")

    def test_platform_workflows_require_explicit_pinned_images(self):
        expected_source = "${{ inputs.public_platform_images || vars.AIVISION_E2E_PLATFORM_IMAGES_JSON }}"
        for filename in ("framework-e2e.yml", "diagnostics-e2e.yml", "native-agent-e2e.yml"):
            jobs = workflow(filename)["jobs"]
            jobs_with_fixtures = [job for job in jobs.values()
                                  if "AIVISION_E2E_PLATFORM_IMAGES_JSON" in job.get("env", {})]
            self.assertTrue(jobs_with_fixtures, filename)
            for job in jobs_with_fixtures:
                self.assertEqual(job["env"]["AIVISION_E2E_PLATFORM_IMAGES_JSON"], expected_source)
                self.assertEqual(job["steps"][0]["if"], "env.AIVISION_E2E_PLATFORM_IMAGES_JSON == ''")
        seekdb = workflow("seekdb-deploy-smoke.yml")["jobs"]["deploy"]
        self.assertEqual(seekdb["env"]["AIVISION_E2E_DEPLOY_IMAGES_JSON"],
                         "${{ inputs.public_platform_images || vars.AIVISION_E2E_DEPLOY_IMAGES_JSON }}")
        command = seekdb["steps"][0]["run"]
        images = {key: "registry.example/" + key + "@sha256:" + "a" * 64
                  for key in ("oceanbase", "agentlens", "backend", "frontend", "gateway", "signoz")}
        with tempfile.TemporaryDirectory() as scratch:
            output = Path(scratch) / "env"
            for supplied in ("", "{}", json.dumps({**images, "gateway": "registry.example/gateway:latest"})):
                env = {**os.environ, "GITHUB_ENV": str(output), "AIVISION_E2E_DEPLOY_IMAGES_JSON": supplied}
                result = subprocess.run(["bash", "-c", command], env=env, capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0, supplied)
                self.assertFalse(output.exists())
            env = {**os.environ, "GITHUB_ENV": str(output), "AIVISION_E2E_DEPLOY_IMAGES_JSON": json.dumps(images)}
            result = subprocess.run(["bash", "-c", command], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            name, _, value = output.read_text().partition("=")
            self.assertEqual(name, "AIVISION_SEEKDB_SMOKE_IMAGES_JSON")
            self.assertEqual(json.loads(value), {key: ref for key, ref in images.items() if key != "oceanbase"})

    def test_public_release_candidates_cannot_write_aliases_without_publication(self):
        document = workflow("release-images.yml")
        jobs = document["jobs"]
        self.assertIs(triggers(document)["workflow_dispatch"]["inputs"]["publish"]["default"], False)
        self.assertEqual(jobs["publish-development"]["if"],
                         "inputs.publish == true && (needs.prepare.outputs.channel == 'dev' || "
                         "needs.prepare.outputs.channel == 'daily')")
        stable_gate = (
            "(needs.prepare.outputs.channel == 'stable' || needs.prepare.outputs.channel == 'rc') && "
            "!inputs.release_preview && (github.event_name == 'push' || inputs.publish == true)"
        )
        self.assertEqual(jobs["preflight"]["if"].strip(), stable_gate)
        self.assertEqual(jobs["publish-stable"]["if"].strip(), stable_gate)
        self.assertEqual(jobs["publish-stable"]["environment"], "release")
        for name in ("preflight", "publish-stable", "publish-development"):
            self.assertIn("acceptance", jobs[name]["needs"])
            self.assertTrue(any("--acceptance acceptance/candidate-acceptance.json" in step.get("run", "")
                                for step in jobs[name]["steps"]))
        self.assertIn("public-registry", jobs["artifact-smoke"]["needs"])
        self.assertEqual(jobs["artifact-smoke"]["strategy"]["matrix"]["backend"], ["oceanbase", "seekdb"])
        disk = next(step for step in jobs["artifact-smoke"]["steps"]
                    if "prepare_platform_disk.py" in step.get("run", ""))
        self.assertEqual(disk["if"], "runner.environment == 'github-hosted' && "
                         "runner.os == 'Linux' && runner.arch == 'X64'")
        self.assertEqual(jobs["public-data-backend"]["needs"], ["prepare"])
        self.assertIn("public-data-backend", jobs["assets"]["needs"])
        self.assertNotIn("packages", jobs["public-registry"]["permissions"])
        self.assertTrue(any("push-by-digest=true" in step.get("with", {}).get("outputs", "")
                            for step in workflow("release-image-build.yml")["jobs"]["build"]["steps"]))
        index = next(step for step in jobs["manifest"]["steps"] if "imagetools create" in step.get("run", ""))
        self.assertIn('--tag "$IMAGE_REPOSITORY:$CANDIDATE_TAG"', index["run"])
        from release_plan import make_plan
        for alias in ("dev", "daily"):
            plan = make_plan("1.2.3", "workflow_dispatch", "refs/heads/main", "main", alias,
                             "111", "42", "2", "f" * 40, "20261118")
            self.assertEqual(plan["candidate_tag"], "candidate-111-2")
            self.assertNotEqual(plan["candidate_tag"], plan["alias"])
        tagged = make_plan("1.2.3", "workflow_dispatch", "refs/tags/v1.2.3", "main", "",
                           "111", "42", "2", "f" * 40, "20261118")
        self.assertEqual(tagged["channel"], "stable")
        self.assertEqual(tagged["candidate_tag"], "candidate-111-2")

    def test_public_release_prepare_stops_private_sources_before_candidate_writes(self):
        prepare = workflow("release-images.yml")["jobs"]["prepare"]
        command = next(step["run"] for step in prepare["steps"] if step.get("id") == "plan")
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            fake_git = work / "git"
            fake_git.write_text('#!/bin/sh\nprintf "%s\\n" "$GITHUB_SHA"\n')
            fake_git.chmod(0o700)
            fake_gh = work / "gh"
            fake_gh.write_text(
                '#!/bin/sh\n'
                'if [ "$*" = "api repos/antgroup/ai-vision --jq .visibility" ]; then\n'
                '  printf "%s\\n" "$FIXTURE_VISIBILITY"\n'
                'else\n'
                '  exit 91\n'
                'fi\n'
            )
            fake_gh.chmod(0o700)
            env = dict(os.environ, PATH=str(work) + os.pathsep + os.environ["PATH"],
                       GITHUB_REPOSITORY="antgroup/ai-vision", GITHUB_SHA="f" * 40,
                       GITHUB_REF="refs/heads/main", DEFAULT_BRANCH="main", RUNNER_TEMP=str(work),
                       GITHUB_EVENT_NAME="workflow_dispatch")
            for visibility, ref, publish, diagnostic in (
                ("private", "refs/heads/main", "false", "requires a public product repository"),
                ("public", "refs/heads/pr-branch", "true", "manual candidate-only run"),
            ):
                with self.subTest(visibility=visibility, ref=ref):
                    env.update(FIXTURE_VISIBILITY=visibility, GITHUB_REF=ref, PUBLISH=publish)
                    result = subprocess.run(["bash", "-e", "-o", "pipefail", "-c", command],
                                            cwd=work, env=env, capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode, 1)
                    self.assertIn(diagnostic, result.stdout)
                    self.assertFalse((work / "release-plan").exists())
            env.update(FIXTURE_VISIBILITY="public", GITHUB_REF="refs/heads/ci", PUBLISH="false")
            checks = command.split('mkdir -p "$RUNNER_TEMP/release-plan"', 1)[0]
            result = subprocess.run(["bash", "-e", "-o", "pipefail", "-c", checks],
                                    cwd=work, env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_inline_aggregates_require_actual_success_for_every_selected_job(self):
        for name in ("framework-e2e.yml", "diagnostics-e2e.yml", "native-agent-e2e.yml"):
            document = workflow(name)
            declarations = triggers(document)["workflow_call"]["inputs"]
            inputs = {key: (True if declaration["type"] == "boolean" else declaration.get("default", ""))
                      for key, declaration in declarations.items() if key != "tier"}
            for opt_in in ("browser_only", "intel_docker"):
                if opt_in in inputs:
                    inputs[opt_in] = False
            inputs["tier"] = "pr"
            gate = document["jobs"]["result"]
            step = gate["steps"][0]
            selected = json.loads(render(step["env"]["SELECTED_JSON"], inputs=inputs))
            self.assertEqual(set(gate["needs"]), set(selected))
            good = {job: {"result": "success"} for job in selected}
            result, summary = run_step(step, inputs=inputs, needs=good)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Selected checks", summary)
            for job in selected:
                for status in ("skipped", "failure", "cancelled", None):
                    with self.subTest(workflow=name, job=job, status=status):
                        needs = {**good, job: {"result": status}}
                        if status is None:
                            needs.pop(job)
                        result, _ = run_step(step, inputs=inputs, needs=needs)
                        self.assertNotEqual(result.returncode, 0)
                        self.assertIn(job, result.stderr)
            # Non-applicable jobs may be skipped, but an unknown tier may not pass.
            inputs.update({key: False for key, declaration in declarations.items()
                           if declaration["type"] == "boolean"})
            skipped = {job: {"result": "skipped"} for job in selected}
            result, _ = run_step(step, inputs=inputs, needs=skipped)
            self.assertEqual(result.returncode, 0, result.stderr)
            inputs["tier"] = "unknown"
            result, _ = run_step(step, inputs=inputs, needs=skipped)
            self.assertNotEqual(result.returncode, 0)

    def test_browser_only_is_explicit_and_does_not_weaken_selected_integration(self):
        document = workflow("framework-e2e.yml")
        for trigger in ("workflow_call", "workflow_dispatch"):
            declaration = triggers(document)[trigger]["inputs"].get("browser_only")
            self.assertIsNotNone(declaration, trigger)
            self.assertEqual(declaration["type"], "boolean")
            self.assertIs(declaration["default"], False)
        caller = workflow("ci.yml")["jobs"]["framework"]
        self.assertNotIn("browser_only", caller["with"])
        for tier in ("pr", "daily", "release"):
            plan = make_plan([".github/workflows/framework-e2e.yml"], tier=tier, profile="full")
            inputs, _ = call_inputs(caller, plan)
            self.assertIs(inputs["browser_only"], False)
            step = document["jobs"]["result"]["steps"][0]
            selected = json.loads(render(step["env"]["SELECTED_JSON"], inputs=inputs))
            self.assertTrue(selected["browser-platform"])
            self.assertTrue(selected["docker-platform"])
        jobs = document["jobs"]
        self.assertIn("inputs.platform && !inputs.browser_only", jobs["docker-platform"]["if"])
        for job in ("browser-platform", "browser-platform-nvidia", "nvidia-result"):
            self.assertNotIn("browser_only", jobs[job]["if"])

    def test_browser_only_gate_executes_the_selected_scope_and_rejects_incomplete_browser(self):
        document = workflow("framework-e2e.yml")
        declarations = triggers(document)["workflow_call"]["inputs"]
        step = document["jobs"]["result"]["steps"][0]
        for tier in ("pr", "daily", "release"):
            for platform, browser_only in ((True, True), (True, False), (False, True)):
                with self.subTest(tier=tier, platform=platform, browser_only=browser_only):
                    inputs = {key: definition["default"] for key, definition in declarations.items()
                              if key != "tier"}
                    inputs.update(tier=tier, platform=platform, browser_only=browser_only)
                    required = {"browser-platform"} if platform else set()
                    if platform and not browser_only:
                        required.add("docker-platform")
                    needs = {job: {"result": "success" if job in required else "skipped"}
                             for job in document["jobs"]["result"]["needs"]}
                    result, summary = run_step(step, inputs=inputs, needs=needs)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("Selected checks", summary)
                    selected = json.loads(render(step["env"]["SELECTED_JSON"], inputs=inputs))
                    self.assertEqual({job for job, value in selected.items() if value}, required)
                    for job in required:
                        for status in ("failure", "skipped", "cancelled", None):
                            broken = {**needs, job: {"result": status}}
                            if status is None:
                                broken.pop(job)
                            result, _ = run_step(step, inputs=inputs, needs=broken)
                            self.assertNotEqual(result.returncode, 0, (job, status))
                            self.assertIn(job, result.stderr)
        inputs.update(tier="unknown", platform=True, browser_only=True)
        needs["browser-platform"] = {"result": "success"}
        result, _ = run_step(step, inputs=inputs, needs=needs)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unknown test tier", result.stderr)

    def test_framework_gate_rejects_conflicting_platform_modes(self):
        document = workflow("framework-e2e.yml")
        declarations = triggers(document)["workflow_call"]["inputs"]
        inputs = {key: definition["default"] for key, definition in declarations.items() if key != "tier"}
        inputs.update(tier="release", platform=True, intel_docker=True, browser_only=True)
        gate = document["jobs"]["result"]
        needs = {job: {"result": "skipped"} for job in gate["needs"]}
        result, _ = run_step(gate["steps"][0], inputs=inputs, needs=needs)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Intel Docker and browser-only are mutually exclusive for platform checks", result.stderr)

    def test_daily_sdk_smoke_requires_both_live_platform_gates(self):
        document = workflow("framework-e2e.yml")
        declarations = triggers(document)["workflow_call"]["inputs"]
        gate = document["jobs"]["result"]
        inputs = {key: definition["default"] for key, definition in declarations.items() if key != "tier"}
        inputs.update(tier="daily", sdk_coverage="smoke", platform=True)
        needs = {job: {"result": "skipped"} for job in gate["needs"]}
        required = ("browser-platform", "docker-platform")
        needs.update({job: {"result": "success"} for job in required})
        result, summary = run_step(gate["steps"][0], inputs=inputs, needs=needs)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Docker SDK coverage `smoke`", summary)
        for job in required:
            with self.subTest(missing=job):
                result, _ = run_step(gate["steps"][0], inputs=inputs,
                                     needs={**needs, job: {"result": "skipped"}})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(job, result.stderr)
        for override in ({"tier": "release"}, {"browser_only": True}, {"intel_docker": True},
                         {"sdk_coverage": "unknown"}):
            with self.subTest(override=override):
                result, _ = run_step(gate["steps"][0], inputs={**inputs, **override}, needs=needs)
                self.assertNotEqual(result.returncode, 0)

    def test_deployment_gate_does_not_accept_a_fork_skip(self):
        gate = workflow("seekdb-deploy-smoke.yml")["jobs"]["gate"]
        for status in ("success", "skipped", "failure", "cancelled", None):
            with self.subTest(status=status):
                needs = {"deploy": {"result": status}} if status else {}
                result, _ = run_step(gate["steps"][0], needs=needs)
                self.assertEqual(result.returncode == 0, status == "success")

    def test_requested_nvidia_acceptance_rejects_failure_cancellation_and_skip(self):
        # Exercise the real gate without contacting a provider or reading secrets.
        gate = workflow("framework-e2e.yml")["jobs"]["nvidia-result"]
        self.assertEqual(gate["needs"], "browser-platform-nvidia")
        for status in ("success", "skipped", "failure", "cancelled", None):
            with self.subTest(status=status):
                needs = {"browser-platform-nvidia": {"result": status}} if status else {}
                result, _ = run_step(gate["steps"][0], needs=needs)
                self.assertEqual(result.returncode == 0, status == "success")

    def test_central_gate_executes_the_real_plan_checker(self):
        step = workflow("ci.yml")["jobs"]["ci-gate"]["steps"][-1]
        for paths, tier in ((["README.md"], "pr"), (["ai-vision/vfe/src/App.tsx"], "pr"), ([], "daily"), ([], "release")):
            plan = make_plan(paths, tier=tier, profile="basic" if tier == "pr" else "full")
            required = {"scope", "metadata", *(family for family in FAMILIES if plan[family])}
            good = {job: {"result": "success" if job in required else "skipped"}
                    for job in ("scope", "metadata", *FAMILIES)}
            result, _ = run_step(step, plan=plan, needs=good)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            for job in required:
                for status in ("skipped", "failure", "cancelled", None):
                    with self.subTest(paths=paths, tier=tier, job=job, status=status):
                        needs = {**good, job: {"result": status}}
                        if status is None:
                            needs.pop(job)
                        result, _ = run_step(step, plan=plan, needs=needs)
                        self.assertNotEqual(result.returncode, 0)
                        self.assertIn(job, result.stdout)
        result, _ = run_step(step, plan={}, needs=good)
        self.assertNotEqual(result.returncode, 0)


class CallerContractTests(unittest.TestCase):
    def test_pr_basics_and_manual_integration_have_distinct_entrypoints(self):
        ci = workflow("ci.yml")
        dispatch = triggers(ci)["workflow_dispatch"]["inputs"]
        self.assertEqual(dispatch["tier"]["default"], "pr")
        self.assertEqual(dispatch["profile"]["default"], "basic")
        self.assertEqual(triggers(ci)["workflow_call"]["inputs"]["profile"]["default"], "full")
        step = ci["jobs"]["scope"]["steps"][-1]
        self.assertEqual(step["env"]["REQUESTED_PROFILE"], "${{ inputs.profile || 'basic' }}")
        self.assertIn('--profile "$REQUESTED_PROFILE"', step["run"])
        integration = workflow("integration-ci.yml")
        self.assertEqual(set(triggers(integration)), {"workflow_dispatch"})
        caller = integration["jobs"]["integration"]
        self.assertEqual(caller["uses"], "./.github/workflows/ci.yml")
        self.assertEqual(caller["with"], {"tier": "pr", "profile": "integration"})

    def test_basic_native_units_cannot_select_official_or_platform_suites(self):
        ci = workflow("ci.yml")
        plan = make_plan([".github/workflows/ci.yml"], profile="basic")
        inputs, _ = call_inputs(ci["jobs"]["native"], plan)
        self.assertIs(inputs["units"], True)
        self.assertIs(inputs["source"], False)
        self.assertIs(inputs["platform"], False)
        self.assertFalse(plan["framework_platform"])
        self.assertFalse(plan["diagnostics_platform"])
        self.assertFalse(plan["deployment"])

    def test_gate_identity_cannot_substitute_integration_for_basic_coverage(self):
        jobs = workflow("ci.yml")["jobs"]
        basic_gate, integration_gate = jobs["ci-gate"], jobs["integration-gate"]
        self.assertEqual(basic_gate["if"], "always() && inputs.profile != 'integration'")
        self.assertEqual(integration_gate["if"], "always() && inputs.profile == 'integration'")
        self.assertNotIn("needs.scope", basic_gate["name"])
        for requested in ("", "basic", "full", "integration"):
            name = render(basic_gate["name"], inputs={"profile": requested})
            self.assertEqual(name == "ci-gate", requested != "integration")
        # A skipped check can satisfy a required status on GitHub. Integration
        # must never emit the reserved basic check name, even if scope fails.
        self.assertEqual(render(basic_gate["name"], inputs={"profile": "integration"}),
                         "Integration plan guard")
        for profile in ("basic", "integration", "full"):
            plan = make_plan([".github/workflows/ci.yml"], profile=profile)
            required = {"scope", "metadata", *(family for family in FAMILIES if plan[family])}
            needs = {job: {"result": "success" if job in required else "skipped"}
                     for job in ("scope", "metadata", *FAMILIES)}
            for gate, accepted in ((basic_gate, profile != "integration"),
                                   (integration_gate, profile == "integration")):
                result, _ = run_step(gate["steps"][-1], plan=plan, needs=needs)
                self.assertEqual(result.returncode == 0, accepted, (profile, result.stdout, result.stderr))

    def test_cli_validates_the_callers_allocated_run_number(self):
        images = workflow("release-images.yml")["jobs"]
        self.assertEqual(
            images["prepare"]["outputs"]["run_number"],
            "${{ steps.plan.outputs.run_number }}",
        )
        self.assertEqual(
            images["cli"]["with"]["run_number"],
            "${{ needs.prepare.outputs.run_number }}",
        )
        cli = workflow("release-cli.yml")
        self.assertEqual(
            triggers(cli)["workflow_call"]["inputs"]["run_number"]["type"], "string"
        )
        step = next(
            step for step in cli["jobs"]["build"]["steps"]
            if step.get("name") == "Test and build"
        )
        self.assertEqual(step["env"]["RELEASE_RUN_NUMBER"], "${{ inputs.run_number }}")
        # Run the real identity command with a different callee run number. The
        # CLI must accept only the number allocated by the image workflow.
        command = re.search(
            r"python3 ../scripts/build/build_info\.py[\s\S]*?>/dev/null", step["run"]
        )[0]
        # Isolate the generator's VERSION and Git identity from future product
        # bumps, including an RC in the real checkout.
        with tempfile.TemporaryDirectory(prefix="aivision-release-number-") as directory:
            root = Path(directory)
            base_version = "0.0.2"
            (root / "VERSION").write_text(base_version + "\n")
            (root / "aivision-cli").mkdir()
            (root / "scripts/build").mkdir(parents=True)
            (root / "scripts/build/build_info.py").write_bytes(
                (ROOT / "scripts/build/build_info.py").read_bytes()
            )
            for args in (
                ("init", "-q"),
                ("add", "."),
                ("-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                 "commit", "-qm", "fixture"),
            ):
                subprocess.run(["git", "-C", str(root), *args], check=True)
            source_sha = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=root, text=True
            ).strip()
            for allocated, succeeds in (("7", True), ("8", False), ("", False)):
                with self.subTest(allocated=allocated):
                    result = subprocess.run(
                        ["bash", "-e", "-o", "pipefail", "-c", command],
                        cwd=root / "aivision-cli",
                        env=dict(
                            os.environ,
                            GITHUB_SHA=source_sha,
                            GITHUB_RUN_ID="123",
                            GITHUB_RUN_ATTEMPT="2",
                            GITHUB_RUN_NUMBER="999",
                            RELEASE_RUN_NUMBER=allocated,
                            RELEASE_CHANNEL="dev",
                            RELEASE_SNAPSHOT_DATE="",
                            RELEASE_DAILY_REVISION="1",
                            PRODUCT_VERSION=f"{base_version}-dev.20261019.7.2",
                        ),
                        capture_output=True, text=True, timeout=10, check=False,
                    )
                    self.assertEqual(result.returncode == 0, succeeds, result.stderr)
            for channel, selected, revision, version, succeeds in (
                ("daily", "20261021", "1", "0.0.2-daily.20261021", True),
                ("daily", "20261021", "2", "0.0.2-daily.20261021.2", True),
                ("daily", "20261022", "1", "0.0.2-daily.20261021", False),
                ("daily", "20261021", "1", "0.0.2-daily.20261021.2", False),
                ("dev", "20261021", "1", "0.0.2-daily.20261021", False),
            ):
                with self.subTest(channel=channel, selected=selected, revision=revision):
                    result = subprocess.run(["bash", "-e", "-o", "pipefail", "-c", command],
                        cwd=root / "aivision-cli", capture_output=True, text=True, timeout=10,
                        env=dict(os.environ, GITHUB_SHA=source_sha, GITHUB_RUN_ID="123", GITHUB_RUN_ATTEMPT="2",
                            GITHUB_RUN_NUMBER="999", RELEASE_RUN_NUMBER="18", RELEASE_CHANNEL=channel,
                            RELEASE_SNAPSHOT_DATE=selected, RELEASE_DAILY_REVISION=revision, PRODUCT_VERSION=version))
                    self.assertEqual(result.returncode == 0, succeeds, result.stderr)

    def test_contract_documents_run_required_metadata_without_runtime_suites(self):
        ci = workflow("ci.yml")
        metadata = ci["jobs"]["metadata"]
        self.assertEqual(metadata["if"], "always() && !cancelled()")
        self.assertEqual(metadata["needs"], "scope")
        step = next(step for step in metadata["steps"] if step.get("id") == "document-contracts")
        self.assertNotIn("if", step)
        required = {
            "ai-vision/agent-plugins/scripts/validate_capabilities.py",
            "ai-vision/agent-plugins/weaver/scripts/spec_sync.py",
        }
        self.assertEqual({shlex.split(line)[1] for line in step["run"].splitlines() if line.strip()}, required)
        for path in (
            "ai-vision/agent-plugins/CAPABILITIES.md",
            "ai-vision/agent-plugins/hermes/capabilities/E3.md",
            "ai-vision/agent-plugins/weaver/policies/aivision/events/README.md",
        ):
            plan = make_plan([path])
            self.assertFalse(any(plan[key] for key in CHECKS), path)
            self.assertFalse(any(caller_selected(ci["jobs"][family], plan) for family in FAMILIES), path)
        # Run the actual inexpensive validators against this checkout.
        result, _ = run_step(step)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # Either validator's failure must stop the actual metadata shell step.
        for rejected in required:
            with self.subTest(rejected=rejected), tempfile.TemporaryDirectory() as directory:
                wrapper = Path(directory) / "python"
                wrapper.write_text(
                    f"#!{sys.executable}\nimport os, subprocess, sys\n"
                    "if sys.argv[1] == os.environ['REJECT_SCRIPT']:\n"
                    "    raise SystemExit(23)\n"
                    "raise SystemExit(subprocess.call([sys.executable, *sys.argv[1:]]))\n"
                )
                wrapper.chmod(0o700)
                result = subprocess.run(
                    ["bash", "-e", "-o", "pipefail", "-c", step["run"]],
                    cwd=ROOT, env=dict(os.environ, PATH=directory + os.pathsep + os.environ["PATH"], REJECT_SCRIPT=rejected),
                    capture_output=True, text=True, check=False, timeout=10,
                )
                self.assertEqual(result.returncode, 23, result.stdout + result.stderr)

    def test_full_plans_reach_all_reusable_boolean_inputs(self):
        ci = workflow("ci.yml")
        for tier in ("daily", "release"):
            plan = make_plan(["README.md"], tier=tier, profile="full")
            covered = set()
            for family, checks in FAMILIES.items():
                caller = ci["jobs"][family]
                self.assertTrue(caller_selected(caller, plan), family)
                if not caller.get("uses") or family == "deployment":
                    self.assertEqual(checks, (family,))
                    covered.add(family)
                    continue
                called = workflow(Path(caller["uses"]).name)
                declarations = triggers(called)["workflow_call"]["inputs"]
                inputs, keys = call_inputs(caller, plan)
                self.assertEqual(keys, set(checks), family)
                self.assertEqual(set(inputs), set(declarations), family)
                self.assertEqual(inputs.pop("tier"), tier)
                self.assertEqual(inputs.pop("public_platform_images"), "")
                self.assertNotIn("public_platform_images", triggers(called)["workflow_dispatch"]["inputs"])
                for opt_in in ("browser_only", "intel_docker"):
                    if opt_in in inputs:
                        self.assertIs(inputs.pop(opt_in), False)
                if "sdk_coverage" in inputs:
                    self.assertEqual(inputs.pop("sdk_coverage"), "full")
                    self.assertEqual(declarations["sdk_coverage"]["type"], "string")
                    self.assertEqual(declarations["sdk_coverage"]["default"], "full")
                    manual_coverage = triggers(called)["workflow_dispatch"]["inputs"]["sdk_coverage"]
                    self.assertEqual(manual_coverage["default"], "full")
                if "unit_scope" in inputs:
                    from native_unit_scope import UNIT_GROUPS
                    self.assertEqual(json.loads(inputs.pop("unit_scope")), list(UNIT_GROUPS))
                    self.assertEqual(declarations["unit_scope"]["type"], "string")
                    self.assertEqual(declarations["unit_scope"]["default"], "all")
                self.assertTrue(all(type(value) is bool and value for value in inputs.values()))
                for name in inputs:
                    self.assertEqual(declarations[name]["type"], "boolean")
                    self.assertFalse(declarations[name]["default"])
                    self.assertEqual(
                        triggers(called)["workflow_dispatch"]["inputs"][name]["default"],
                        name != "units",
                    )
                covered.update(keys)
            self.assertEqual(covered, set(CHECKS))

    def test_basic_plugin_scope_reaches_the_native_workflow_as_json(self):
        plan = make_plan(["ai-vision/agent-plugins/pi/observ-pi/src/config.ts"])
        self.assertIn("native_unit_groups", plan, "the plan must carry the selected plugin unit groups")
        inputs, keys = call_inputs(workflow("ci.yml")["jobs"]["native"], plan)
        self.assertEqual(json.loads(inputs["unit_scope"]), ["pi", "onboarding", "harness"])
        self.assertTrue(inputs["units"])
        self.assertFalse(inputs["source"])
        self.assertEqual(keys, {"native_units", "native_source", "native_platform"})

    def test_targeted_plans_select_the_expected_inner_jobs(self):
        ci = workflow("ci.yml")
        scenarios = (
            (["README.md"], {}),
            (["ai-vision/vfe/src/App.tsx"], {"framework": {"frontend", "frontend-browser"}}),
            (["aivision-cli/internal/query/render.go"], {"framework": {"cli"}}),
            (["ai-vision/agent-plugins/langfuse/tests/js6/package.json"], {
                "framework": {"frontend", "frontend-browser", "backend-contracts", "contracts", "js-contracts"},
            }),
            (["ai-vision/aivision-py-be/apps/core/views/spaces.py"], {
                "framework": {"backend-contracts", "contracts", "js-contracts", "docker-database"},
                "diagnostics": {"application-contracts"},
            }),
        )
        for paths, expected in scenarios:
            plan = make_plan(paths)
            actual = {}
            for family in ("framework", "diagnostics", "native"):
                caller = ci["jobs"][family]
                if not caller_selected(caller, plan):
                    continue
                inputs, _ = call_inputs(caller, plan)
                called = workflow(Path(caller["uses"]).name)
                step = called["jobs"]["result"]["steps"][0]
                selected = json.loads(render(step["env"]["SELECTED_JSON"], inputs=inputs))
                actual[family] = {job for job, required in selected.items() if required}
            self.assertEqual(actual, expected, paths)

    def test_sdk_and_browser_additions_keep_scope_and_trust_boundaries(self):
        document = workflow("framework-e2e.yml")
        jobs = document["jobs"]
        self.assertEqual(jobs["contracts"]["strategy"]["matrix"]["sdk"], ["lf3", "lf4", "lf5"])
        self.assertEqual(jobs["js-contracts"]["strategy"]["matrix"]["sdk"], ["js", "js6"])
        self.assertEqual(jobs["js-contracts"]["if"], "inputs.contracts")
        self.assertIn("inputs.platform", jobs["browser-platform"]["if"])
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", jobs["browser-platform"]["if"])
        for name in ("browser-platform", "docker-platform", "docker-database"):
            # Public PR code must stay on disposable hosted runners, even when a
            # persistent integration runner is configured for trusted dispatches.
            self.assertIn("github.event_name != 'pull_request' && vars.AIVISION_E2E_DOCKER_LINUX_RUNNER", jobs[name]["runs-on"])
        declarations = triggers(document)
        self.assertFalse(declarations["workflow_dispatch"]["inputs"]["nvidia_browser"]["default"])
        self.assertNotIn("nvidia_browser", declarations["workflow_call"]["inputs"])
        nvidia = jobs["browser-platform-nvidia"]
        self.assertEqual(nvidia["environment"], "nvidia-e2e")
        self.assertIn("github.event_name == 'workflow_dispatch' && inputs.nvidia_browser", nvidia["if"])
        self.assertIn("github.event.repository.default_branch", nvidia["if"])
        self.assertIn("github.event_name == 'workflow_dispatch' && inputs.nvidia_browser", jobs["nvidia-result"]["if"])
        self.assertNotIn("browser-platform-nvidia", jobs["result"]["needs"])

    def test_platform_jobs_wait_for_both_selected_frontend_checks(self):
        jobs = workflow("framework-e2e.yml")["jobs"]
        frontend_guard = (
            "(!inputs.frontend || (needs.frontend.result == 'success' && "
            "needs.frontend-browser.result == 'success'))"
        )
        for name in ("browser-platform", "browser-platform-nvidia", "docker-platform"):
            with self.subTest(job=name):
                self.assertEqual(set(jobs[name]["needs"]), {"frontend", "frontend-browser"})
                self.assertIn(frontend_guard, jobs[name]["if"])

    def test_windows_relay_runtime_applies_to_the_full_collector_matrix(self):
        steps = workflow("framework-e2e.yml")["jobs"]["contracts"]["steps"]
        named = {step["name"]: step for step in steps if "name" in step}
        setup = next(step for step in steps if step.get("id") == "windows-relay-python")
        self.assertEqual(setup["with"], {"python-version": "3.13", "update-environment": False})
        lifecycle = named["Relay lifecycle and exporter regressions on Windows"]
        collector = named["Relay RAG scenarios and official SDK export on Windows"]
        self.assertEqual(collector["if"], lifecycle["if"])
        self.assertEqual(collector["if"], "runner.os == 'Windows' && matrix.sdk != 'lf5'")
        self.assertEqual(collector["env"]["RELAY_PYTHON"], "${{ steps.windows-relay-python.outputs.python-path }}")
        self.assertIn("& $env:RELAY_PYTHON ai-vision/agent-plugins/tests/framework_e2e/suite.py", collector["run"])
        self.assertIn("--sdks nemo-relay", collector["run"])
        standard = named["Fake-model RAG scenarios and official SDK export (runner-local)"]
        self.assertIn("(matrix.sdk == 'lf5' || runner.os == 'Windows') && 'langfuse' || 'langfuse nemo-relay'", standard["run"])
        self.assertTrue(standard["run"].startswith("python "))

    def test_daily_is_linux_and_release_retains_cross_platform_coverage(self):
        ci = workflow("ci.yml")
        for tier in ("pr", "daily", "release"):
            for family in ("framework", "diagnostics", "native"):
                called = workflow(Path(ci["jobs"][family]["uses"]).name)
                for job, definition in called["jobs"].items():
                    matrix = definition.get("strategy", {}).get("matrix")
                    if not matrix:
                        continue
                    if job == "frontend-browser":
                        self.assertEqual(definition["runs-on"], "ubuntu-24.04")
                        self.assertEqual(matrix, {"shard": [1, 2, 3, 4]})
                        self.assertEqual(definition["strategy"]["max-parallel"], 2)
                        continue
                    if job in {"docker-platform", "docker-database", "owned-database", "owned-platform-quick"}:
                        # Docker runner routing is tested separately from source platform coverage.
                        continue
                    match = re.fullmatch(
                        r"\$\{\{ fromJSON\(inputs\.tier == 'release' && '([^']+)'"
                        r" \|\| '([^']+)'\) \}\}",
                        matrix["os"],
                    )
                    self.assertIsNotNone(match, (family, job))
                    release, default = match.groups()
                    platforms = json.loads(release if tier == "release" else default)
                    with self.subTest(tier=tier, family=family, job=job):
                        self.assertIn("ubuntu-24.04", platforms)
                        if tier == "release":
                            self.assertTrue(any(platform.startswith("macos-") for platform in platforms))
                            if job in {"frontend", "backend-contracts", "contracts", "js-contracts", "portable-contracts", "native-source"}:
                                self.assertIn("windows-2025", platforms)
                        else:
                            self.assertEqual(platforms, ["ubuntu-24.04"])

    def test_release_macos_source_jobs_share_one_non_canceling_queue(self):
        found = set()
        for filename in ("ci.yml", "framework-e2e.yml", "diagnostics-e2e.yml", "native-agent-e2e.yml"):
            for job, definition in workflow(filename)["jobs"].items():
                platforms = definition.get("strategy", {}).get("matrix", {}).get("os", "")
                if "macos-26" not in platforms:
                    continue
                with self.subTest(workflow=filename, job=job):
                    self.assertEqual(definition["runs-on"], "${{ matrix.os }}")
                    concurrency = definition["concurrency"]
                    self.assertEqual(concurrency["queue"], "max")
                    self.assertIs(concurrency.get("cancel-in-progress", False), False)
                    self.assertIn("matrix.os == 'macos-26' && 'aivision-release-macos-arm64'", concurrency["group"])
                    fallback = f"{Path(filename).stem}-{job}-{{0}}-{{1}}-{{2}}"
                    self.assertIn(
                        f"format('{fallback}', github.run_id, github.run_attempt, strategy.job-index)",
                        concurrency["group"],
                    )
                    found.add(filename)
        self.assertEqual(found, {"ci.yml", "framework-e2e.yml", "diagnostics-e2e.yml", "native-agent-e2e.yml"})

    def test_intel_docker_is_opt_in_and_reuses_strict_selected_gates(self):
        ci = workflow("ci.yml")
        families = {
            "framework": {"docker-database", "docker-platform"},
            "diagnostics": {"owned-database", "owned-platform-quick"},
            "native": {"docker-platform"},
        }
        for family, required in families.items():
            caller = ci["jobs"][family]
            self.assertNotIn("intel_docker", caller["with"])
            called = workflow(Path(caller["uses"]).name)
            declarations = triggers(called)["workflow_call"]["inputs"]
            self.assertEqual(declarations["intel_docker"]["type"], "boolean")
            self.assertIs(declarations["intel_docker"]["default"], False)
            self.assertNotIn("intel_docker", triggers(called)["workflow_dispatch"]["inputs"])
            jobs = called["jobs"]
            for job in required:
                with self.subTest(family=family, job=job):
                    matrix = jobs[job]["strategy"]["matrix"]
                    match = re.fullmatch(
                        r"\$\{\{ fromJSON\(inputs\.intel_docker && '([^']+)'"
                        r"(?: \|\| \(inputs\.tier == 'release' \|\| inputs\.tier == 'daily'\) && '([^']+)')?"
                        r" \|\| '([^']+)'\) \}\}",
                        matrix["os"],
                    )
                    self.assertIsNotNone(match, matrix["os"])
                    intel, linux_extended, linux_default = match.groups()
                    self.assertEqual(json.loads(intel), ["macos-15-intel"])
                    self.assertEqual(json.loads(linux_default), ["ubuntu-24.04"])
                    if job.endswith("-database"):
                        self.assertEqual(json.loads(linux_extended), ["ubuntu-24.04", "ubuntu-24.04-arm"])
                        self.assertEqual(matrix["backend"], ["oceanbase", "seekdb"])
                    else:
                        self.assertIsNone(linux_extended)
            if family == "framework":
                self.assertIn("inputs.platform && !inputs.intel_docker", jobs["browser-platform"]["if"])
            gate = jobs["result"]
            step = gate["steps"][0]
            inputs = {key: definition["default"] for key, definition in declarations.items() if key != "tier"}
            inputs.update(tier="release", intel_docker=True, platform=True)
            if family != "native":
                inputs["database"] = True
            selected = json.loads(render(step["env"]["SELECTED_JSON"], inputs=inputs))
            self.assertEqual({job for job, value in selected.items() if value}, required)
            needs = {job: {"result": "success" if job in required else "skipped"} for job in gate["needs"]}
            result, _ = run_step(step, inputs=inputs, needs=needs)
            self.assertEqual(result.returncode, 0, result.stderr)
            for job in required:
                with self.subTest(family=family, job=job):
                    result, _ = run_step(step, inputs=inputs, needs={**needs, job: {"result": "skipped"}})
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(job, result.stderr)

    def test_intel_docker_wrapper_is_manual_read_only_and_requires_all_suites(self):
        document = workflow("intel-macos-docker.yml")
        self.assertEqual(set(triggers(document)), {"workflow_dispatch"})
        declarations = triggers(document)["workflow_dispatch"]["inputs"]
        self.assertEqual(set(declarations), {"release_manifest"})
        self.assertEqual(declarations["release_manifest"]["type"], "string")
        self.assertIs(declarations["release_manifest"]["required"], True)
        self.assertEqual(document["permissions"], {"contents": "read", "packages": "read"})
        jobs = document["jobs"]
        fixture_step = jobs["inputs"]["steps"][-1]["id"]
        self.assertEqual(jobs["inputs"]["outputs"], {
            "public_platform_images": f"${{{{ steps.{fixture_step}.outputs.public_platform_images }}}}",
        })
        families = {
            "framework": ("framework-e2e.yml", {"database": True}),
            "diagnostics": ("diagnostics-e2e.yml", {"database": True}),
            "native": ("native-agent-e2e.yml", {"units": False, "source": False}),
        }
        self.assertEqual(set(jobs), {"inputs", "result", *families})
        for family, (filename, selectors) in families.items():
            with self.subTest(family=family):
                self.assertEqual(jobs[family]["needs"], "inputs")
                self.assertEqual(jobs[family]["uses"], f"./.github/workflows/{filename}")
                self.assertEqual(jobs[family]["with"], {
                    "tier": "release", "intel_docker": True, "platform": True,
                    "public_platform_images": "${{ needs.inputs.outputs.public_platform_images }}", **selectors,
                })
        for job in jobs.values():
            self.assertNotIn("permissions", job)
            self.assertFalse(job.get("continue-on-error", False))
            self.assertNotIn("secrets", job)
        gate = jobs["result"]
        self.assertEqual(gate["if"], "always()")
        self.assertEqual(set(gate["needs"]), {"inputs", *families})
        good = {job: {"result": "success"} for job in gate["needs"]}
        step = gate["steps"][0]
        result, _ = run_step(step, needs=good)
        self.assertEqual(result.returncode, 0, result.stderr)
        for job in gate["needs"]:
            for status in ("failure", "skipped", "cancelled", None):
                with self.subTest(job=job, status=status):
                    needs = {**good, job: {"result": status}}
                    if status is None:
                        needs.pop(job)
                    result, _ = run_step(step, needs=needs)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(job, result.stderr)

    def test_intel_docker_wrapper_only_projects_fixtures_from_the_checked_out_candidate(self):
        from public_registry import PUBLIC_REPOSITORY, product_image_repositories, source_fixtures

        step = workflow("intel-macos-docker.yml")["jobs"]["inputs"]["steps"][-1]
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        images = {role: f"{repo}@sha256:{index:064x}"
                  for index, (role, repo) in enumerate(product_image_repositories().items(), 1)}
        manifest = {
            "source_repository": PUBLIC_REPOSITORY,
            "source_sha": sha,
            "images": images,
            "databases": {"oceanbase": "oceanbase/oceanbase-ce@sha256:" + "a" * 64},
        }
        expected = source_fixtures(manifest)

        def run_manifest(raw):
            with tempfile.TemporaryDirectory(prefix="aivision-intel-fixtures-") as directory:
                output = Path(directory) / "output"
                env = dict(os.environ, GITHUB_SHA=sha, GITHUB_OUTPUT=str(output))
                env.update({key: render(value, inputs={"release_manifest": raw})
                            for key, value in step["env"].items()})
                result = subprocess.run(["bash", "-e", "-o", "pipefail", "-c", step["run"]],
                                        cwd=ROOT, env=env, capture_output=True, text=True, timeout=10)
                return result, output.read_text() if output.exists() else ""

        result, output = run_manifest(json.dumps(manifest, indent=2))
        self.assertEqual(result.returncode, 0, result.stderr)
        name, data = output.strip().split("=", 1)
        self.assertEqual(name, "public_platform_images")
        self.assertEqual(json.loads(data), expected)
        for case, invalid in {
            "empty": "",
            "malformed": "{",
            "array": json.dumps([]),
            "missing source SHA": json.dumps({}),
            "bare fixture map": json.dumps(expected),
            "stale SHA": json.dumps({**manifest, "source_sha": "0" * 40}),
            "wrong source repository": json.dumps({**manifest, "source_repository": "example/ai-vision"}),
            "mutable image": json.dumps({**manifest, "images": {**images, "aivision-fe": images["aivision-fe"].split("@")[0] + ":latest"}}),
            "wrong image repository": json.dumps({**manifest, "images": {**images, "aivision-fe": "example.invalid/fixture@sha256:" + "a" * 64}}),
        }.items():
            with self.subTest(case=case):
                result, output = run_manifest(invalid)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(output, "")

    def test_database_architecture_reaches_helpers_and_keeps_release_image_pins(self):
        from release_assets import DATABASES

        for filename, name in (("framework-e2e.yml", "docker-database"),
                               ("diagnostics-e2e.yml", "owned-database")):
            with self.subTest(workflow=filename):
                jobs = workflow(filename)["jobs"]
                job = jobs[name]
                self.assertFalse(job.get("continue-on-error", False))
                self.assertIn(name, jobs["result"]["needs"])
                # A persistent x64 integration override must never steal the ARM job.
                self.assertTrue(job["runs-on"].startswith("${{ matrix.os == 'ubuntu-24.04' && "))
                self.assertEqual(job["env"]["DATABASE_PLATFORM"],
                    "${{ matrix.os == 'ubuntu-24.04-arm' && 'linux/arm64' || 'linux/amd64' }}")
                steps = [step for step in job["steps"] if "docker_database.py" in step.get("run", "")]
                self.assertEqual(len(steps), 1)
                self.assertIn('--platform "$DATABASE_PLATFORM"', steps[0]["run"])
                self.assertFalse(steps[0].get("continue-on-error", False))
                env = job["env"]
                images = json.loads(env["AIVISION_E2E_DB_IMAGES_JSON"]) if name == "docker-database" else {
                    "oceanbase": env["OCEANBASE_IMAGE"], "seekdb": env["SEEKDB_IMAGE"]}
                self.assertEqual(images, DATABASES)


class ArtifactContractTests(unittest.TestCase):
    def test_reusable_evidence_names_are_separate_for_each_attempt(self):
        names = {attempt: set() for attempt in (1, 2)}
        for filename in ("framework-e2e.yml", "diagnostics-e2e.yml", "native-agent-e2e.yml", "seekdb-deploy-smoke.yml"):
            for job_name, job in workflow(filename)["jobs"].items():
                for step in job.get("steps", []):
                    if not step.get("uses", "").startswith("actions/upload-artifact@"):
                        continue
                    name = step["with"]["name"]
                    if filename == "framework-e2e.yml" and job_name == "frontend":
                        # Static source bundle reuse allows a failed browser
                        # retry. Runtime/semantic evidence still stays scoped
                        # to its exact workflow attempt below.
                        self.assertEqual(name, "frontend-fixture-${{ github.run_id }}-${{ github.sha }}")
                        self.assertEqual(step["with"]["path"], "ai-vision/dist/")
                        self.assertIs(step["with"]["overwrite"], True)
                        continue
                    self.assertIn("${{ github.run_attempt }}", name, filename)
                    for attempt, attempt_names in names.items():
                        attempt_names.add(name.replace("${{ github.run_attempt }}", str(attempt)))
        self.assertTrue(names[1])
        self.assertTrue(names[1].isdisjoint(names[2]))

    def test_actual_weaver_command_consumes_only_current_attempt_artifacts(self):
        jobs = workflow("native-agent-e2e.yml")["jobs"]
        uploader = next(step["with"] for step in jobs["native-source"]["steps"]
                        if step.get("uses", "").startswith("actions/upload-artifact@"))
        upload = uploader["name"]
        download = next(step["with"]["pattern"] for step in jobs["native-semantics"]["steps"]
                        if step.get("uses", "").startswith("actions/download-artifact@"))
        step = next(step for step in jobs["native-semantics"]["steps"]
                    if "weaver_gate.py" in step.get("run", ""))
        for tier in ("pr", "daily", "release"):
            platforms = ["ubuntu-24.04"] + (["macos-26", "windows-2025"] if tier == "release" else [])
            for attempt in (1, 2):
                with self.subTest(tier=tier, attempt=attempt), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    log = root / "commands.jsonl"
                    expected = [upload.replace("${{ matrix.os }}", platform).replace("${{ github.run_attempt }}", str(attempt))
                                for platform in platforms]
                    # Multiple upload paths retain their shared ancestor. The
                    # Hermes reports are siblings of native-source, as in the
                    # actual hosted artifact; summary.json is not at its root.
                    source = root / "upload-source"
                    for relative in ("native-source/summary.json", "native-source/example/evidence.json",
                                     "hermes-turn-numbering/report.json", "hermes-compression/report.json"):
                        path = source / relative
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_text("{}")
                    selected = [path for expression in uploader["path"].splitlines()
                                for path in source.glob(expression.removeprefix("${{ runner.temp }}/")) if path.is_file()]
                    ancestor = Path(os.path.commonpath([path.parent for path in selected]))
                    for name in expected:
                        for path in selected:
                            downloaded = root / "native-source-artifacts" / name / path.relative_to(ancestor)
                            downloaded.parent.mkdir(parents=True, exist_ok=True)
                            downloaded.write_bytes(path.read_bytes())
                    # Execute the workflow's shell argument construction, while
                    # replacing heavyweight build/test commands with argv capture.
                    for executable in ("python", "make"):
                        fake = root / executable
                        fake.write_text(
                            f"#!{sys.executable}\nimport json, os, sys\nfrom pathlib import Path\n"
                            "with open(os.environ['COMMAND_LOG'], 'a') as log:\n"
                            "    log.write(json.dumps(sys.argv[1:]) + '\\n')\n"
                            "if sys.argv[1].endswith('weaver_gate.py'):\n"
                            "    for index, value in enumerate(sys.argv):\n"
                            "        if value == '--input-dir':\n"
                            "            json.loads((Path(sys.argv[index + 1]) / 'summary.json').read_text())\n"
                        )
                        fake.chmod(0o700)
                    command = step["run"].replace("${{ runner.temp }}", directory).replace("${{ github.run_attempt }}", str(attempt))
                    result = subprocess.run(
                        ["bash", "-e", "-o", "pipefail", "-c", command],
                        env=dict(os.environ, PATH=directory + os.pathsep + os.environ["PATH"], TEST_TIER=tier, COMMAND_LOG=str(log)),
                        cwd=ROOT, capture_output=True, text=True, check=False, timeout=10,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    commands = [json.loads(line) for line in log.read_text().splitlines()]
                    weaver = next(args for args in commands if args[0].endswith("weaver_gate.py"))
                    directories = [Path(weaver[index + 1]) for index, value in enumerate(weaver) if value == "--input-dir"]
                    self.assertEqual([path.parent.name for path in directories], expected)
                    self.assertTrue(all((path / "summary.json").is_file() for path in directories))
                    pattern = download.replace("${{ github.run_attempt }}", str(attempt))
                    previous = download.replace("${{ github.run_attempt }}", str(3 - attempt))
                    self.assertTrue(all(fnmatch.fnmatch(name, pattern) for name in expected))
                    self.assertFalse(any(fnmatch.fnmatch(name, previous) for name in expected))


@unittest.skipUnless(os.name == 'posix', 'Executes the Linux/macOS build shell with a POSIX Docker fixture')
class OwnedBuilderContractTests(unittest.TestCase):
    def execute_build(self, *, fail_build='', cleanup_failure=False, create_failure=False, macos=False):
        steps = workflow('diagnostics-e2e.yml')['jobs']['owned-platform-quick']['steps']
        step = next(step for step in steps
                    if step.get('name') == 'Build owned Codex and deployment CLI images')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path, calls_path = root / 'docker-state.json', root / 'docker-calls.jsonl'
            calls_path.touch()
            state_path.write_text(json.dumps({'selected': 'user-selected',
                'builders': {'user-selected': 'existing-user-cache'},
                'images': {'user-image': 'existing-user-image'},
                'containers': {'user-container': 'user-image'}, 'volumes': ['user-volume'],
                'buildx_installed': not macos}))
            (root / 'python').symlink_to(sys.executable)
            docker = root / 'docker'
            docker.write_text('#!' + sys.executable + '\n' + '''
import json, os, pathlib, sys
state_path, calls_path = pathlib.Path(os.environ['DOCKER_STATE']), pathlib.Path(os.environ['DOCKER_CALLS'])
args = sys.argv[1:]
with calls_path.open('a') as output:
    output.write(json.dumps(args) + '\\n')
state = json.loads(state_path.read_text())
def save(): state_path.write_text(json.dumps(state))
if args[0] == 'buildx' and not state['buildx_installed']: raise SystemExit(127)
if args[:2] == ['buildx', 'version']:
    print('buildx fixture')
elif args[:2] == ['buildx', 'ls']:
    assert args[2:] == ['--format', '{{.Name}}']
    print(*state['builders'], sep=chr(10))
elif args[:2] == ['container', 'ls']:
    assert args[2:] == ['--all', '--format', '{{.Names}}']
    print(*state['containers'], sep=chr(10))
elif args[:2] == ['volume', 'ls']:
    assert args[2:] == ['--format', '{{.Name}}']
    print(*state['volumes'], sep=chr(10))
elif args[:2] == ['buildx', 'create']:
    name = args[args.index('--name') + 1]
    assert '--node' not in args and '--append' not in args
    assert name not in state['builders']
    if os.environ['CREATE_FAILURE'] == '1':
        # Simulate another job claiming the name after the read-only precheck.
        state['builders'][name] = 'concurrent-user-cache'
        save()
        raise SystemExit(29)
    state['builders'][name] = 'owned-cache'
    if '--use' in args: state['selected'] = name
    save()
elif args[:2] == ['buildx', 'build']:
    builder = args[args.index('--builder') + 1]
    assert state['builders'][builder] == 'owned-cache'
    image = args[args.index('-t') + 1]
    container = 'buildx_buildkit_' + builder + '0'
    volume = container + '_state'
    state['containers'][container] = 'owned-builder-container'
    if volume not in state['volumes']:
        state['volumes'].append(volume)
    save()
    if os.environ['FAIL_BUILD'] and os.environ['FAIL_BUILD'] in image: raise SystemExit(23)
    if '--load' in args:
        state['images'][image] = 'loaded-' + image
    save()
elif args[:2] == ['buildx', 'rm']:
    name = args[-1]
    assert name != 'user-selected' and state['builders'][name] == 'owned-cache'
    if os.environ['CLEANUP_FAILURE'] == '1': raise SystemExit(17)
    del state['builders'][name]
    container = 'buildx_buildkit_' + name + '0'
    del state['containers'][container]
    state['volumes'].remove(container + '_state')
    save()
elif args[0] == 'create':
    assert args[-1] in state['images']
    state['containers']['owned-export-container'] = args[-1]
    save()
    print('owned-export-container')
elif args[0] == 'cp':
    assert args[1].split(':')[0] in state['containers']
    pathlib.Path(args[2]).write_bytes(b'production-hook')
elif args[0] == 'rm':
    assert args[-1] == 'owned-export-container'
    del state['containers'][args[-1]]
    save()
elif args[:2] == ['image', 'inspect']:
    assert all(image in state['images'] for image in args[2:])
    assert state['builders'] == {'user-selected': 'existing-user-cache'}
else:
    raise SystemExit(97)
''')
            docker.chmod(0o700)
            command = step['run'].replace('${{ runner.temp }}', directory)
            if macos:
                # Execute the real macOS package-install argv, then require its
                # plugin to be available to the actual build-step shell.
                install = next(step for step in steps if step.get('name') == 'Start local Docker on macOS Intel')
                brew_line = next(line.strip() for line in install['run'].splitlines()
                                 if line.strip().startswith('brew install '))
                brew = root / 'brew'
                brew.write_text('#!' + sys.executable + '\n' + '''
import json, os, pathlib, sys
path = pathlib.Path(os.environ['DOCKER_STATE'])
state = json.loads(path.read_text())
if 'docker-buildx' in sys.argv[2:]: state['buildx_installed'] = True
path.write_text(json.dumps(state))
''')
                brew.chmod(0o700)
                command = brew_line + '\n' + command
            completed = subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', command],
                cwd=ROOT, env=dict(os.environ, PATH=str(root) + os.pathsep + os.environ['PATH'],
                    GITHUB_RUN_ID='123', GITHUB_RUN_ATTEMPT='2', DOCKER_STATE=str(state_path),
                    DOCKER_CALLS=str(calls_path), FAIL_BUILD=fail_build,
                    CLEANUP_FAILURE='1' if cleanup_failure else '0', CREATE_FAILURE='1' if create_failure else '0'),
                capture_output=True, text=True, timeout=10)
            return completed, [json.loads(line) for line in calls_path.read_text().splitlines()], json.loads(state_path.read_text())

    def test_success_removes_only_owned_cache_and_preserves_loaded_images_and_selection(self):
        completed, calls, state = self.execute_build()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(calls[0], ['buildx', 'version'])
        self.assertEqual(state['selected'], 'user-selected')
        self.assertEqual(state['builders'], {'user-selected': 'existing-user-cache'})
        self.assertEqual(state['images']['user-image'], 'existing-user-image')
        self.assertEqual(set(state['images']), {'user-image', 'aivision-diagnostics-codex:ci',
                                               'aivision-diagnostics-docker-cli:local'})
        self.assertEqual(state['containers'], {'user-container': 'user-image'})
        self.assertEqual(state['volumes'], ['user-volume'])
        create = next(args for args in calls if args[:2] == ['buildx', 'create'])
        name = create[create.index('--name') + 1]
        self.assertTrue(name.startswith('aiv-diagnostics-123-2-'))
        self.assertEqual(create[create.index('--driver') + 1], 'docker-container')
        self.assertIn('--allow-insecure-entitlement network.host', create)
        self.assertIn('network=host', create)
        builds = [args for args in calls if args[:2] == ['buildx', 'build']]
        self.assertEqual(len(builds), 2)
        for args in builds:
            self.assertEqual(args[args.index('--builder') + 1], name)
            self.assertIn('--load', args)
        self.assertIn('--network=host', builds[0])
        self.assertEqual(builds[0][builds[0].index('--allow') + 1], 'network.host')
        removal = next(index for index, args in enumerate(calls) if args[:2] == ['buildx', 'rm'])
        inspection = next(index for index, args in enumerate(calls) if args[:2] == ['image', 'inspect'])
        self.assertLess(removal, inspection)
        self.assertFalse(any('--use' in args or 'prune' in args or args[:2] == ['buildx', 'use'] for args in calls))

    def test_build_failure_still_removes_owned_builder_and_preserves_original_exit(self):
        for image in ('codex', 'docker-cli'):
            with self.subTest(image=image):
                completed, calls, state = self.execute_build(fail_build=image)
                self.assertEqual(completed.returncode, 23, completed.stderr)
                self.assertTrue(any(args[:2] == ['buildx', 'rm'] for args in calls))
                self.assertEqual(state['builders'], {'user-selected': 'existing-user-cache'})
                self.assertEqual(state['selected'], 'user-selected')

    def test_cleanup_failure_blocks_success_without_masking_an_earlier_build_failure(self):
        for fail_build, expected in (('', 1), ('codex', 23)):
            with self.subTest(fail_build=fail_build):
                completed, calls, state = self.execute_build(fail_build=fail_build, cleanup_failure=True)
                self.assertEqual(completed.returncode, expected, completed.stderr)
                self.assertIn('Could not remove job-owned Buildx resources', completed.stderr)
                self.assertEqual(state['selected'], 'user-selected')
                self.assertFalse(any(args[:2] == ['image', 'inspect'] for args in calls))

    def test_failed_create_never_removes_or_changes_an_existing_builder(self):
        completed, calls, state = self.execute_build(create_failure=True)
        self.assertEqual(completed.returncode, 29, completed.stderr)
        self.assertFalse(any(args[0] == 'rm' or args[1:2] == ['rm'] for args in calls))
        create = next(args for args in calls if args[:2] == ['buildx', 'create'])
        name = create[create.index('--name') + 1]
        self.assertEqual(state['builders'], {
            'user-selected': 'existing-user-cache', name: 'concurrent-user-cache',
        })
        self.assertEqual(state['selected'], 'user-selected')
        self.assertEqual(state['containers'], {'user-container': 'user-image'})
        self.assertEqual(state['volumes'], ['user-volume'])

    def test_macos_installs_the_plugin_before_running_the_owned_builder_shell(self):
        completed, calls, state = self.execute_build(macos=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(state['buildx_installed'])
        self.assertEqual(state['selected'], 'user-selected')


if __name__ == "__main__":
    unittest.main()
