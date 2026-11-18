"""Reuse requires matching tracked build inputs and historical native identity."""

import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from product_registry import PUBLIC_REPOSITORY, product_image_repositories
from release_plan import IMAGES, make_plan
from release_reuse import CONTEXTS, fingerprint, latest_release, native_reuse, prepare, published_manifest, validate_build

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
