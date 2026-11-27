"""Run the formal release planning step against divergent Git histories."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]


class FormalTagAncestryTests(unittest.TestCase):
    def setUp(self):
        fixture = tempfile.TemporaryDirectory()
        self.addCleanup(fixture.cleanup)
        self.directory = Path(fixture.name)
        self.source = self.directory / "source"
        self.source.mkdir()
        self.git("init", "-q")
        self.git("config", "user.name", "CI fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("-c", "commit.gpgsign=false", "commit", "--allow-empty", "-qm", "common root")
        common = self.git("rev-parse", "HEAD")
        for version in ("1.0.0", "1.0.1-rc.1"):
            self.tag_version(version)
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        self.git("checkout", "--quiet", "--detach", common)
        self.tag_version("2.0.0")
        self.git("update-ref", "refs/remotes/origin/develop", "HEAD")

        scripts = self.source / "scripts/ci"
        scripts.mkdir(parents=True)
        shutil.copyfile(ROOT / "scripts/ci/release_plan.py", scripts / "release_plan.py")
        gh = self.directory / "gh"
        gh.write_text(
            '#!/bin/sh\n'
            'case "$*" in\n'
            '  "api repos/antgroup/ai-vision --jq .visibility") echo public ;;\n'
            '  "api repos/antgroup/ai-vision/actions/runs/123 --jq .created_at") echo 2026-11-25T18:23:00Z ;;\n'
            '  *) exit 91 ;;\n'
            'esac\n'
        )
        gh.chmod(0o700)

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.source, text=True).strip()

    def tag_version(self, version):
        (self.source / "VERSION").write_text(version + "\n")
        self.git("add", "VERSION")
        self.git("-c", "commit.gpgsign=false", "commit", "-qm", version)
        self.git("-c", "tag.gpgsign=false", "tag", "v" + version)

    def run_plan(self, version, *, event="push"):
        tag = "v" + version
        self.git("checkout", "--quiet", "--detach", tag)
        document = yaml.safe_load((ROOT / ".github/workflows/release-images.yml").read_text())
        step = next(step for step in document["jobs"]["prepare"]["steps"] if step.get("id") == "plan")
        output = self.directory / tag
        output.mkdir()
        env = dict(os.environ, PATH=f"{self.directory}{os.pathsep}{os.environ['PATH']}",
                   GITHUB_REPOSITORY="antgroup/ai-vision", GITHUB_SHA=self.git("rev-parse", "HEAD"),
                   GITHUB_EVENT_NAME=event, GITHUB_REF=f"refs/tags/{tag}", DEFAULT_BRANCH="develop",
                   GITHUB_RUN_ID="123", GITHUB_RUN_NUMBER="9", GITHUB_RUN_ATTEMPT="1",
                   RUNNER_TEMP=str(output), GITHUB_OUTPUT=str(output / "github-output"), PUBLISH="false")
        result = subprocess.run(["bash", "-c", step["run"]], cwd=self.source, env=env,
                                capture_output=True, text=True, timeout=10)
        return result, output / "release-plan/release-plan.json"

    def test_formal_tags_on_main_pass_with_develop_as_default(self):
        for version, event, channel in (("1.0.0", "push", "stable"), ("1.0.1-rc.1", "workflow_dispatch", "rc")):
            with self.subTest(version=version):
                result, output = self.run_plan(version, event=event)
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                self.assertEqual(json.loads(output.read_text())["channel"], channel)

    def test_formal_tags_only_on_develop_fail_before_planning(self):
        result, output = self.run_plan("2.0.0")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("reachable from main", result.stdout)
        self.assertFalse(output.exists())

    def test_missing_main_fails_closed(self):
        self.git("update-ref", "-d", "refs/remotes/origin/main")
        result, output = self.run_plan("2.0.0")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("reachable from main", result.stdout)
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
