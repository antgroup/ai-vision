"""Local/fake-command coverage; no native compiler, Docker or deployment run."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("native_candidate", Path(__file__).with_name("native_candidate.py"))
C = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(C)
IMAGE = "python:3.11.14-slim-bookworm@sha256:" + "a" * 64
ROOT = Path(__file__).resolve().parents[2]


class CandidateFixture:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.source = self.base / "source"
        self.source.mkdir()
        (self.source / "VERSION").write_text("0.1.0\n")
        (self.source / "tracked").write_text("frozen")
        dockerfile = self.source / "deploy/native-packaging/Dockerfile.ci-builder"
        dockerfile.parent.mkdir(parents=True)
        shutil.copyfile(ROOT / "deploy/native-packaging/Dockerfile.ci-builder", dockerfile)
        self.git("init", "-q")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "user.name", "fixture")
        self.git("remote", "add", "origin", "https://github.com/example/fork.git")
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")
        self.sha = self.git("rev-parse", "HEAD").strip()
        self.ci = self.run_record(12, ".github/workflows/ci.yml", "completed", "success")
        self.producer = self.run_record(34, ".github/workflows/native-candidate.yml", "in_progress", None)
        # A hosted test runner has its own real identity. Bind these controlled
        # builds to their fixture instead; production environment checks stay on.
        environment = patch.dict(os.environ, {
            "GITHUB_RUN_ID": "34", "GITHUB_RUN_ATTEMPT": "2",
            "GITHUB_REPOSITORY": "example/fork", "GITHUB_SHA": self.sha,
        })
        environment.start()
        self.addCleanup(environment.stop)

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.source), *args], text=True, stderr=subprocess.DEVNULL)

    def run_record(self, run_id, path, status, conclusion):
        return dict(id=run_id, run_attempt=2, path=path, head_sha=self.sha,
                    repository={"full_name": "example/fork"}, head_repository={"full_name": "example/fork"},
                    status=status, conclusion=conclusion, event="push")

    def jobs(self):
        return [{"jobs": [dict(id=56, run_id=12, run_attempt=2, name="ci-gate", head_sha=self.sha, status="completed", conclusion="success")]}]

    def plan(self):
        return C.make_plan(self.source, self.ci, self.producer, IMAGE, self.jobs())


class CandidateTests(CandidateFixture, unittest.TestCase):
    def test_plan_keeps_distinct_identities_and_root_version(self):
        result = self.plan()
        self.assertEqual(result["source_ci"]["id"], 12)
        self.assertEqual(result["producer"]["id"], 34)
        self.assertEqual(result["product_version"], "0.1.0")
        self.assertEqual(result["cli_profiles"], ["source", "native_distribution"])
        self.assertEqual(result["platform"], "linux/amd64")

    def test_identity_failures_reject(self):
        for target, key, value in (("ci", "head_sha", "f" * 40), ("ci", "path", ".github/workflows/release-images.yml"),
                                   ("ci", "conclusion", "failure"), ("ci", "status", "cancelled"),
                                   ("ci", "run_attempt", 0), ("ci", "run_attempt", True),
                                   ("ci", "repository", {"full_name": "other/repo"}),
                                   ("producer", "head_sha", "f" * 40), ("producer", "id", 12),
                                   ("producer", "path", ".github/workflows/ci.yml"),
                                   ("producer", "run_attempt", "2")):
            with self.subTest(target=target, key=key, value=value):
                ci, producer = copy.deepcopy(self.ci), copy.deepcopy(self.producer)
                (ci if target == "ci" else producer)[key] = value
                with self.assertRaises(ValueError):
                    C.make_plan(self.source, ci, producer, IMAGE, self.jobs())

    def test_source_ci_requires_real_successful_gate_and_nonsynthetic_event(self):
        for key, value in (("name", "profile-integration"), ("conclusion", "skipped"), ("status", "queued"),
                           ("run_id", 999), ("head_sha", "f" * 40), ("run_attempt", 1), ("id", 0)):
            jobs = self.jobs()
            jobs[0]["jobs"][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                C.make_plan(self.source, self.ci, self.producer, IMAGE, jobs)
        for jobs in ([], [{"jobs": []}], self.jobs() * 2):
            with self.subTest(jobs=jobs), self.assertRaises(ValueError):
                C.make_plan(self.source, self.ci, self.producer, IMAGE, jobs)
        for event in ("pull_request", "pull_request_target", "workflow_run", None):
            ci = dict(self.ci, event=event)
            with self.subTest(event=event), self.assertRaises(ValueError):
                C.make_plan(self.source, ci, self.producer, IMAGE, self.jobs())

    def test_only_pinned_official_python_image(self):
        for image in ("python:3.11-slim", IMAGE + " $(touch /tmp/unwanted)", "evil/" + IMAGE,
                      IMAGE.replace("3.11.14", "3.12.0"), IMAGE[:-1], "docker.io/library/" + IMAGE):
            with self.subTest(image=image), self.assertRaises(ValueError):
                C.make_plan(self.source, self.ci, self.producer, image, self.jobs())

    def test_dirty_checkout_rejected(self):
        (self.source / "tracked").write_text("changed")
        with self.assertRaisesRegex(ValueError, "clean"):
            self.plan()

    def test_revalidate_rejects_tampered_plan_and_source(self):
        plan = self.plan()
        for key, value in (("platform", "linux/arm64"), ("product_version", "9.9.9"), ("python_image", "evil"),
                           ("cli_profiles", ["native_distribution"]), ("source_sha", "f" * 40)):
            altered = copy.deepcopy(plan)
            altered[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                C.validate_plan(self.source, altered)
        (self.source / "tracked").write_text("changed")
        with self.assertRaises(ValueError):
            C.validate_plan(self.source, plan)

    def test_actual_producer_environment_is_bound(self):
        C.validate_plan(self.source, self.plan())
        for key, value in (("GITHUB_RUN_ID", "999"), ("GITHUB_RUN_ATTEMPT", "999"),
                           ("GITHUB_REPOSITORY", "other/repository"), ("GITHUB_SHA", "f" * 40)):
            with self.subTest(key=key), patch.dict(os.environ, {key: value}):
                with self.assertRaisesRegex(ValueError, "producer"):
                    C.validate_plan(self.source, self.plan())

    def test_paths_reject_overlap_symlinks_and_nonempty(self):
        output, work = self.base / "output", self.base / "work"
        plugin = self.base / "plugins.tar.gz"
        plugin.write_bytes(b"fixture")
        for out, private in ((self.source / "out", work), (output, output / "work"), (self.base, work)):
            with self.subTest(out=out), self.assertRaises(ValueError):
                C.validate_build_paths(self.source, plugin, out, private)
        output.mkdir()
        (output / "log.txt").write_text("private")
        with self.assertRaises(ValueError):
            C.validate_build_paths(self.source, plugin, output, work)
        shutil.rmtree(output)
        output.symlink_to(work, target_is_directory=True)
        with self.assertRaises(ValueError):
            C.validate_build_paths(self.source, plugin, output, work)

    def test_example_allowlist_is_explicit_and_rejects_new_yaml(self):
        provider = self.source / "ai-vision/agent-plugins"
        for relative in C.PUBLIC_EXAMPLES:
            path = provider / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("public fixture")
        self.assertEqual(len(C.public_examples(self.source)), 25)
        (provider / "langfuse/examples/unreviewed.yaml").write_text("new")
        with self.assertRaisesRegex(ValueError, "example"):
            C.public_examples(self.source)

    def test_plugin_proof_uses_tracked_files_excludes_build_outputs(self):
        provider = self.source / "ai-vision/agent-plugins"
        provider.mkdir(parents=True)
        (provider / "code.rs").write_text("source")
        (provider / "dist").mkdir()
        (provider / "dist/generated").write_text("artifact")
        self.git("add", ".")
        self.git("commit", "-qm", "plugin fixture")
        proof = C.plugin_inputs(self.source)
        self.assertIn("ai-vision/agent-plugins/code.rs", proof)
        self.assertNotIn("ai-vision/agent-plugins/dist/generated", proof)

    def test_upload_allowlist_rejects_raw_logs_and_symlinks(self):
        directory = self.base / "accepted"
        directory.mkdir()
        for name in C.OUTPUT_FILES:
            (directory / name).write_text("typed fixture")
        C.validate_output(directory)
        (directory / "compile.log").write_text("private")
        with self.assertRaises(ValueError):
            C.validate_output(directory)

    def test_builder_checks_actual_python_and_debian_before_apt(self):
        dockerfile = (ROOT / "deploy/native-packaging/Dockerfile.ci-builder").read_text()
        self.assertIn("sys.version_info[:3] == (3, 11, 14)", dockerfile)
        self.assertIn('. /etc/os-release && test "$VERSION_CODENAME" = bookworm', dockerfile)
        self.assertLess(dockerfile.index("sys.version_info"), dockerfile.index("apt-get"))
        self.assertLess(dockerfile.index("VERSION_CODENAME"), dockerfile.index("apt-get"))

    def test_builder_has_separate_pinned_base_and_no_source_copy(self):
        dockerfile = (ROOT / "deploy/native-packaging/Dockerfile.ci-builder").read_text()
        self.assertIn("ARG PYTHON_IMAGE", dockerfile)
        self.assertIn("FROM ${PYTHON_IMAGE}", dockerfile)
        self.assertIn("libmariadb-dev", dockerfile)
        self.assertNotIn("COPY", dockerfile)



class BuildTests(CandidateFixture, unittest.TestCase):
    """Controlled command fixture creates typed evidence; never calls build tools."""
    def prepare_build(self):
        plan = self.plan()
        plugin = self.base / "plugin-dist.tar.gz"
        plugin.write_bytes(b"fixture dist")
        plan_path = self.base / "plan.json"
        C.write_json(plan_path, plan)
        return plan, plugin, plan_path, self.base / "output", self.base / "work"

    def fake_run(self, argv, *, cwd=None, env=None):
        argv = [str(item) for item in argv]
        self.calls.append(argv)
        def flag(name):
            return Path(argv[argv.index(name) + 1])
        if argv[:2] == ["go", "version"]:
            return "go version go1.25.14 linux/amd64\n"
        if argv[:3] == ["docker", "image", "inspect"]:
            return json.dumps({"Id": "sha256:" + "b" * 64, "Os": "linux", "Architecture": "amd64"})
        if argv[:3] == ["docker", "image", "save"]:
            flag("--output").write_bytes(b"saved image fixture")
        if argv[:2] == ["go", "build"]:
            flag("-o").write_bytes(b"native fixture")
        if argv[:2] == ["docker", "run"]:
            native = self.base / "work/native-stage/native"
            native.mkdir(parents=True)
            C.write_json(native / "receipt.json", {"schema_version": 1, "target": "linux/amd64", "source_revision": self.sha,
                "inputs_stable": True, "input_sha256": {"tracked": C.file_hash(self.source / "tracked")},
                "tools": {"secret_private_absolute_path": "/private/compiler"},
                "components": {name: {"smoke": ["controlled fixture"]} for name in ("backend", "agentlens", "onboarding")}})
            if self.mutation == "missing_receipt":
                (native / "receipt.json").unlink()
            if self.mutation == "source":
                (self.source / "tracked").write_text("mutated")
            if self.mutation == "plugin":
                (self.base / "plugin-dist.tar.gz").write_bytes(b"tampered")
            if self.mutation == "receipt_identity":
                value = C.read_json(native / "receipt.json")
                value["source_revision"] = "f" * 40
                C.write_json(native / "receipt.json", value)
            if self.mutation == "receipt_hash":
                value = C.read_json(native / "receipt.json")
                value["input_sha256"]["tracked"] = "f" * 64
                C.write_json(native / "receipt.json", value)
        if "prepare" in argv:
            directory = flag("--output-dir")
            directory.mkdir()
            (directory / "context").mkdir()
            C.write_json(directory / "receipt.json", {"schema_version": 1, "component": argv[argv.index("--component")+1],
                "source_revision": self.sha, "target": "linux/amd64", "base_image_id": "sha256:" + "b" * 64})
        if "audit-image" in argv:
            C.write_json(flag("--output"), {"ok": self.mutation != "image_audit", "violations": [],
                "image_config_sha256": "sha256:" + "c" * 64, "target": "linux/amd64"})
        return ""

    def build_fixture(self, mutation=None):
        self.calls = []
        self.mutation = mutation
        plan, plugin, plan_path, output, work = self.prepare_build()
        def cli(source, plan, private, accepted, runner):
            for profile in ("source", "native"):
                (accepted / f"aiv-{profile}-linux-x64.tar.gz").write_bytes(b"controlled CLI archive")
                C.write_json(accepted / f"cli-{profile}-audit.json", {"profile": profile, "scope": "controlled fixture"})
        with patch.object(C, "public_examples", return_value={"fixture": {}}), \
             patch.object(C, "plugin_inputs", return_value={"tracked": "a" * 64}), \
             patch.object(C, "make_component_info", return_value={"fixture": "metadata"}), \
             patch.object(C, "build_clis", side_effect=cli):
            C.build(self.source, plan_path, plugin, output, work, run=self.fake_run)
        return plan, output, work

    def test_controlled_build_uses_bounded_argv_and_only_clean_outputs(self):
        plan, output, work = self.build_fixture()
        C.validate_output(output)
        receipt = C.read_json(output / "candidate-receipt.json")
        self.assertEqual(receipt["source_ci"]["id"], 12)
        self.assertEqual(receipt["producer"]["id"], 34)
        self.assertEqual(receipt["qualification"], C.QUALIFICATION)
        self.assertNotIn("/private/compiler", (output / "native-build-audit.json").read_text())
        compiler = next(argv for argv in self.calls if argv[:2] == ["docker", "run"])
        self.assertIn("--read-only", compiler)
        self.assertIn("--cpus=2", compiler)
        self.assertIn("--memory=6g", compiler)
        self.assertIn("--jobs", compiler)
        self.assertEqual(compiler[compiler.index("--jobs") + 1], "1")
        self.assertIn(str(self.source.resolve()) + ":/repo:ro", compiler)
        backend = next(argv for argv in self.calls if "prepare" in argv and "backend" in argv)
        self.assertIn("--plugin-dist-sha256", backend)
        self.assertIn("--onboarding-stage", backend)
        self.assertEqual(sum("audit-image" in argv for argv in self.calls), 2)
        self.assertFalse(any(set(argv) & {"push", "login", "manifest", "gh", "curl"} for argv in self.calls))
        self.assertTrue((work / "base-image.tar").is_file())

    def test_missing_producer_evidence_source_mutation_or_image_failure_never_uploads(self):
        for mutation in ("missing_receipt", "source", "image_audit", "plugin", "receipt_identity", "receipt_hash"):
            with self.subTest(mutation=mutation):
                # Each failure uses a fresh checkout and private staging area.
                self.setUp()
                with self.assertRaises((ValueError, OSError)):
                    self.build_fixture(mutation)
                self.assertTrue(any(argv[:2] == ["docker", "run"] for argv in self.calls))
                if mutation == "image_audit":
                    self.assertTrue(any("audit-image" in argv for argv in self.calls))
                output = self.base / "output"
                self.assertFalse(output.exists() and any(output.iterdir()))


class CLITests(CandidateFixture, unittest.TestCase):
    def metadata(self, profile="source"):
        settings = {"GOOS": "linux", "GOARCH": "amd64", "CGO_ENABLED": "0", "vcs.revision": self.sha,
                    "vcs.modified": "false", "-ldflags": f"-X {C.CLI_MODULE}/internal/cli.Version=0.1.0"}
        if profile == "native":
            settings["-tags"] = "native_distribution"
        return {"Path": C.CLI_MODULE, "Main": {"Path": C.CLI_MODULE}, "GoVersion": "go1.25.14",
                "Settings": [{"Key": key, "Value": value} for key, value in settings.items()]}

    def test_cli_metadata_rejects_wrong_version_source_profile_or_vcs(self):
        plan = self.plan()
        C.cli_metadata(self.metadata(), plan, "source")
        C.cli_metadata(self.metadata("native"), plan, "native")
        for key, value in (("vcs.modified", "true"), ("vcs.revision", "f" * 40), ("GOARCH", "arm64"),
                           ("CGO_ENABLED", "1"), ("-ldflags", "-X fake.Version=0.1.0")):
            info = self.metadata()
            next(row for row in info["Settings"] if row["Key"] == key)["Value"] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                C.cli_metadata(info, plan, "source")
        with self.assertRaises(ValueError):
            C.cli_metadata(self.metadata("native"), plan, "source")
        with self.assertRaises(ValueError):
            C.cli_metadata(self.metadata(), plan, "native")

    def test_argv_and_credential_isolation_with_real_local_process(self):
        import sys
        work = self.base / "isolated"
        work.mkdir()
        runner = C.Runner(work)
        sentinel = "$(touch /tmp/native-candidate-MUST-NOT-EXIST); literal"
        with patch.dict(os.environ, {"GITHUB_TOKEN": "fixture-secret", "AWS_SECRET_ACCESS_KEY": "fixture-secret"}):
            runner([sys.executable, "-c", "import json,os,sys; print(json.dumps([sys.argv[1:], dict(os.environ)]))", sentinel])
        arguments, environment = json.loads((work / "logs/001.log").read_text())
        self.assertEqual(arguments, [sentinel])
        self.assertNotIn("GITHUB_TOKEN", environment)
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", environment)
        self.assertEqual(environment["HOME"], str(work / "home"))

    def test_native_receipt_never_includes_private_tools_or_dependencies(self):
        plan = self.plan()
        stage = self.base / "stage"
        stage.mkdir()
        original = {"schema_version": 1, "source_revision": self.sha, "target": "linux/amd64", "inputs_stable": True,
                    "input_sha256": {"tracked": C.file_hash(self.source / "tracked")},
                    "components": {key: {"smoke": ["typed import"], "runtime": "/private/DO-NOT-UPLOAD"}
                                   for key in ("backend", "agentlens", "onboarding")},
                    "tools": {"python": "/private/DO-NOT-UPLOAD"}, "dependencies": {"token": "DO-NOT-UPLOAD"}}
        C.write_json(stage / "receipt.json", original)
        projection = C.native_audit(stage, self.source, plan)
        self.assertNotIn("DO-NOT-UPLOAD", json.dumps(projection))
        for key, value in (("inputs_stable", False), ("target", "linux/arm64"), ("source_revision", None),
                           ("input_sha256", {}), ("components", {})):
            altered = copy.deepcopy(original)
            altered[key] = value
            C.write_json(stage / "receipt.json", altered)
            with self.subTest(key=key), self.assertRaises(ValueError):
                C.native_audit(stage, self.source, plan)


if __name__ == "__main__":
    unittest.main()
