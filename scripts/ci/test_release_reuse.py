"""Reuse requires matching tracked build inputs and historical native identity."""

import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from product_registry import PUBLIC_REPOSITORY, UPSTREAM_SIGNOZ, product_image_repositories
from release_plan import IMAGES, make_plan
from release_reuse import CONTEXTS, fingerprint, latest_release, main, native_reuse, prepare, published_manifest, validate_build

ROOT = Path(__file__).resolve().parents[2]


class ReuseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "source"
        self.root.mkdir()
        self.directory = Path(self.temp.name) / "plan"
        self.directory.mkdir()
        (self.root / "VERSION").write_text("0.0.1\n")
        (self.root / "scripts/build").mkdir(parents=True)
        shutil.copyfile(ROOT / "scripts/build/build_info.py", self.root / "scripts/build/build_info.py")
        for context in CONTEXTS.values():
            path = self.root / context
            path.mkdir(parents=True, exist_ok=True)
            (path / "Dockerfile").write_text("FROM scratch\n")
            (path / ".dockerignore").write_text("dist\n")
        self.git("init", "-q")
        self.commit()
        self.write_plan("123", "7")
        original = prepare(self.root, self.directory)
        self.prior = dict(
            source_sha=self.sha(),
            source_repository=PUBLIC_REPOSITORY,
            component_inputs={name: value["context_sha256"] for name, value in original.items()},
            component_builds={name: value["identity"] for name, value in original.items()},
            images={name: product_image_repositories()[name] + "@sha256:" + "b" * 64 for name in IMAGES},
        )
        (self.root / "README.md").write_text("documentation only\n")
        self.commit()
        self.write_plan("124", "8")

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.root, text=True).strip()

    def sha(self):
        return self.git("rev-parse", "HEAD")

    def commit(self):
        self.git("add", ".")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture")

    def write_plan(self, run_id, run_number):
        plan = make_plan("0.0.1", "schedule", "refs/heads/main", "main", "daily",
                         run_id, run_number, "1", self.sha(), "20261021")
        (self.directory / "release-plan.json").write_text(json.dumps(plan))

    def prepare_coverage(self, *, prior, force=False):
        output = self.directory / "github-output"
        argv = ["release_reuse.py", "prepare", "--directory", str(self.directory), "--root", str(self.root)]
        if force:
            argv.append("--force")
        with (patch.object(sys, "argv", argv),
              patch.dict(os.environ, GITHUB_REPOSITORY=PUBLIC_REPOSITORY, GITHUB_OUTPUT=str(output)),
              patch("release_reuse.latest_release", return_value="v0.0.1-daily.20261020"),
              patch("release_reuse.published_manifest", return_value=prior)):
            main()
        plan = json.loads((self.directory / "release-plan.json").read_text())
        return plan, output.read_text() if output.exists() else ""

    def with_matching_signoz(self):
        upstream = f"{UPSTREAM_SIGNOZ}@sha256:" + "b" * 64
        (self.directory / "signoz-image.txt").write_text(upstream + "\n")
        mirror = product_image_repositories()["signoz-ob"] + "@sha256:" + "b" * 64
        return {**self.prior, "images": {**self.prior["images"], "signoz-ob": mirror},
                "data_backend_mirror": {"upstream_ref": upstream, "mirror_ref": mirror}}

    def test_daily_prose_and_ui_with_exact_published_baseline_uses_smoke(self):
        ui = self.root / "ai-vision/vfe/src/components/Example.tsx"
        ui.parent.mkdir(parents=True)
        ui.write_text("export default function Example() { return null; }\n")
        self.commit()
        self.write_plan("125", "9")
        plan, output = self.prepare_coverage(prior=self.with_matching_signoz())
        self.assertEqual(plan.get("sdk_coverage"), "smoke")
        self.assertIn("sdk_coverage=smoke\n", output)
        self.assertEqual(plan["reuse_release"], "v0.0.1-daily.20261020")

    def test_moved_sensitive_path_is_seen_even_with_rename_detection_enabled(self):
        (self.root / "docs").mkdir()
        self.git("config", "diff.renames", "true")
        self.git("mv", "antcollector/Dockerfile", "docs/notes.md")
        self.commit()
        self.write_plan("125", "9")
        plan, _ = self.prepare_coverage(prior=self.with_matching_signoz())
        self.assertEqual(plan.get("sdk_coverage"), "full")

    def test_docs_with_executable_mode_are_not_safe_prose(self):
        (self.root / "README.md").chmod(0o755)
        self.commit()
        self.write_plan("125", "9")
        plan, output = self.prepare_coverage(prior=self.with_matching_signoz())
        self.assertEqual(plan.get("sdk_coverage"), "full")
        self.assertIn("sdk_coverage_reason=source-diff-requires-compatibility\n", output)

    def test_unproven_or_non_daily_candidates_keep_full_coverage(self):
        prior = self.with_matching_signoz()
        for options in (dict(prior=None), dict(prior=prior, force=True), dict(prior=self.prior)):
            with self.subTest(options=options):
                plan, _ = self.prepare_coverage(**options)
                self.assertEqual(plan.get("sdk_coverage"), "full")
        release = make_plan("0.0.1", "workflow_dispatch", "refs/heads/main", "main", "dev",
                            "126", "10", "1", self.sha(), "20261021", release_preview=True)
        (self.directory / "release-plan.json").write_text(json.dumps(release))
        plan, _ = self.prepare_coverage(prior=prior)
        self.assertEqual(plan.get("sdk_coverage"), "full")

    def test_upstream_digest_change_and_unreadable_diff_keep_full_coverage(self):
        prior = self.with_matching_signoz()
        (self.directory / "signoz-image.txt").write_text(f"{UPSTREAM_SIGNOZ}@sha256:" + "c" * 64)
        plan, _ = self.prepare_coverage(prior=prior)
        self.assertEqual(plan.get("sdk_coverage"), "full")
        self.with_matching_signoz()
        original = subprocess.check_output

        def unreadable(args, **kwargs):
            if args[:2] == ["git", "diff"]:
                raise subprocess.CalledProcessError(128, args)
            return original(args, **kwargs)

        with patch("release_reuse.subprocess.check_output", side_effect=unreadable):
            plan, output = self.prepare_coverage(prior=prior)
        self.assertEqual(plan.get("sdk_coverage"), "full")
        self.assertIn("sdk_coverage=full\n", output)

    def test_unchanged_context_reuses_digest_and_complete_old_identity(self):
        result = prepare(self.root, self.directory, self.prior)
        for name in IMAGES:
            self.assertEqual(result[name]["ref"], self.prior["images"][name])
            self.assertEqual(result[name]["identity"], self.prior["component_builds"][name])
            self.assertNotEqual(result[name]["identity"]["source_sha"], self.sha())

    def test_changed_context_rebuilds_all_affected_components(self):
        (self.root / "ai-vision/vfe/Dockerfile").write_text("FROM scratch\nLABEL changed=true\n")
        self.commit()
        self.write_plan("125", "9")
        result = prepare(self.root, self.directory, self.prior)
        for name in IMAGES:
            self.assertEqual(result[name]["ref"] is None, name in {"aivision-fe", "aivision-be"})
        self.assertEqual(result["aivision-fe"]["identity"]["source_sha"], self.sha())

    def test_plugin_build_workflow_is_a_transitive_backend_input(self):
        (self.root / ".github/workflows").mkdir(parents=True)
        (self.root / ".github/workflows/release-plugins.yml").write_text("name: changed plugin toolchain\n")
        self.commit()
        self.write_plan("125", "9")
        result = prepare(self.root, self.directory, self.prior)
        self.assertIsNone(result["aivision-be"]["ref"])
        self.assertTrue(all(result[name]["ref"] is not None for name in IMAGES if name != "aivision-be"))

    def test_shared_image_workflow_is_an_input_to_all_owned_images(self):
        workflow = self.root / ".github/workflows/release-image-build.yml"
        workflow.parent.mkdir(parents=True)
        workflow.write_text("name: changed shared image builder\n")
        self.commit()
        self.write_plan("125", "9")
        result = prepare(self.root, self.directory, self.prior)
        self.assertTrue(all(value["ref"] is None for value in result.values()))

    def test_force_and_legacy_metadata_rebuild_instead_of_claiming_reuse(self):
        for prior, force in ((self.prior, True), ({"images": self.prior["images"], "source_repository": PUBLIC_REPOSITORY}, False)):
            result = prepare(self.root, self.directory, prior, force=force)
            self.assertTrue(all(value["ref"] is None for value in result.values()))
            self.assertEqual(len({value["identity"]["built_at"] for value in result.values()}), 1)

    def test_changed_modes_and_dockerignore_invalidate_context(self):
        name = "antcollector-gw"
        old = fingerprint(self.root, name)
        self.git("update-index", "--chmod=+x", "antcollector/Dockerfile")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "mode")
        self.assertNotEqual(fingerprint(self.root, name), old)
        old = fingerprint(self.root, name)
        (self.root / "antcollector/.dockerignore").write_text("other\n")
        self.commit()
        self.assertNotEqual(fingerprint(self.root, name), old)

    def test_tampered_fingerprint_and_build_metadata_are_rejected(self):
        prior = copy.deepcopy(self.prior)
        prior["component_inputs"]["agentlens"] = "c" * 64
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            prepare(self.root, self.directory, prior)
        for key in ("source_sha", "built_at", "component_version", "dirty"):
            identity = copy.deepcopy(self.prior["component_builds"]["agentlens"])
            identity.pop(key)
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_build("agentlens", identity)

    def test_latest_release_excludes_rolling_aliases_drafts_and_unversioned_tags(self):
        releases = [[
            dict(tag_name="daily", draft=False, published_at="2026-10-22T00:00:00Z"),
            dict(tag_name="v0.0.2", draft=True, published_at="2026-10-22T00:00:00Z"),
            dict(tag_name="v0.0.1", draft=False, published_at="2026-10-20T00:00:00Z"),
        ], [dict(tag_name="v0.0.1-dev.20261021.7", draft=False,
                 published_at="2026-10-21T00:00:00Z")]]
        with patch("release_reuse.run", return_value=json.dumps(releases)) as call:
            self.assertEqual(latest_release(), "v0.0.1-dev.20261021.7")
            self.assertIn(f"repos/{PUBLIC_REPOSITORY}/releases?per_page=100", call.call_args.args)
        with patch("release_reuse.run", return_value="[]"):
            self.assertEqual(latest_release(), "")

    def test_published_reuse_rejects_bad_checksums_and_wrong_tag_source(self):
        tag = "v0.0.1-dev.20261021.7"
        manifest = dict(self.prior, product_version=tag[1:], release_tag=tag)
        release = dict(tag_name=tag, draft=False, published_at="2026-10-21T00:00:00Z",
                       assets=[dict(name=name) for name in ("release-manifest.json", "SHA256SUMS")])
        payload = json.dumps(manifest).encode()

        def gh(*args, cwd=None):
            if args[:2] == ("gh", "api"):
                return json.dumps(release)
            directory = Path(args[-1])
            (directory / "release-manifest.json").write_bytes(payload)
            (directory / "SHA256SUMS").write_text(checksum + "  release-manifest.json\n")
            return ""

        checksum = "f" * 64
        with patch("release_reuse.run", side_effect=gh), patch("release_publish.require_live_tag") as source:
            with self.assertRaisesRegex(ValueError, "checksum"):
                published_manifest(self.root, tag)
            source.assert_not_called()
        checksum = hashlib.sha256(payload).hexdigest()
        with patch("release_reuse.run", side_effect=gh), patch("release_publish.require_live_tag", side_effect=ValueError("release tag moved")):
            with self.assertRaisesRegex(ValueError, "moved"):
                published_manifest(self.root, tag)
        with patch("release_reuse.run", side_effect=gh), patch("release_publish.require_live_tag") as source:
            self.assertEqual(published_manifest(self.root, tag), manifest)
            source.assert_called_once_with(PUBLIC_REPOSITORY, tag, self.prior["source_sha"])
        release["assets"] = []
        with patch("release_reuse.run", side_effect=gh):
            self.assertIsNone(published_manifest(self.root, tag))

    def test_component_identity_uses_the_runtime_strict_schema_and_timestamp(self):
        good = self.prior["component_builds"]["agentlens"]
        for key, value in (
            ("schema_version", True), ("schema_version", "1"),
            ("built_at", "2026-10-21 00:00:00+00:00"),
            ("built_at", "2026-10-21T00:00:00.1234567Z"),
            ("built_at", "2026-02-30T00:00:00Z"),
            ("built_at", "2026-10-21T00:00:00+25:00"),
            ("built_at", None), ("source_sha", None), ("build_id", None),
            ("target_channel", []),
        ):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                validate_build("agentlens", dict(good, **{key: value}))

    def test_native_reuse_verifies_the_matching_child_with_the_old_identity(self):
        name = "agentlens"
        ref = self.prior["images"][name]
        identity = self.prior["component_builds"][name]
        manifest = dict(digest="sha256:" + "b" * 64, manifests=[
            dict(digest="sha256:" + value * 64,
                 platform=dict(os="linux", architecture=arch))
            for arch, value in (("amd64", "c"), ("arm64", "d"))])
        with patch("release_reuse.run", return_value=json.dumps(manifest)), patch("release_reuse.subprocess.run") as check:
            self.assertEqual(native_reuse(name, ref, identity, "amd64"), "sha256:" + "c" * 64)
            argv = check.call_args.args[0]
            self.assertIn(ref.split("@", 1)[0] + "@sha256:" + "c" * 64, argv)
            import base64
            self.assertEqual(json.loads(base64.b64decode(argv[-1])), identity)
        manifest["digest"] = "sha256:" + "f" * 64
        with patch("release_reuse.run", return_value=json.dumps(manifest)), patch("release_reuse.subprocess.run") as check:
            with self.assertRaisesRegex(ValueError, "index digest"):
                native_reuse(name, ref, identity, "amd64")
            check.assert_not_called()


if __name__ == "__main__":
    unittest.main()
