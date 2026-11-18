"""Build identity must describe the checkout, never a deploy-time version hint."""

import base64
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from build_info import make_info


class BuildInfoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "VERSION").write_text("0.0.1\n")
        self.git("init", "-q")
        self.git("add", "VERSION")
        self.git(
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "fixture",
        )
        self.sha = self.git("rev-parse", "HEAD").strip()

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args], text=True)

    def info(self, **kwargs):
        return make_info(
            self.root, "aivision-fe", built_at="2026-10-16T00:00:00Z", **kwargs
        )

    def test_clean_build_records_exact_checkout_and_product_version(self):
        info = self.info(channel="stable", source_sha=self.sha, build_id="123.1")
        self.assertEqual(
            info,
            {
                "schema_version": 1,
                "component": "aivision-fe",
                "product_version": "0.0.1",
                "component_version": "0.0.1",
                "source_sha": self.sha,
                "build_id": "123.1",
                "built_at": "2026-10-16T00:00:00Z",
                "target_channel": "stable",
                "dirty": False,
            },
        )

    def test_untracked_files_mark_local_build_dirty(self):
        (self.root / "feature.ts").write_text("export const changed = true\n")
        self.assertTrue(self.info()["dirty"])
        with self.assertRaisesRegex(ValueError, "clean"):
            self.info(channel="stable")

    def test_daily_version_is_derived_without_mutating_the_checkout(self):
        try:
            info = self.info(channel="daily", product_version="0.0.1-daily.20261021", build_id="123.2", run_number="7")
        except ValueError as error:
            self.fail(f"A clean Daily snapshot must accept its date-based version: {error}")
        self.assertEqual(info["product_version"], "0.0.1-daily.20261021")
        self.assertEqual(info["build_id"], "123.2")
        self.assertEqual(info["component_version"], info["product_version"])
        self.assertFalse(info["dirty"])
        self.assertEqual((self.root / "VERSION").read_text(), "0.0.1\n")
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_first_attempt_has_a_short_version_and_retains_the_full_build_id(self):
        info = self.info(
            channel="dev",
            product_version="0.0.1-dev.20261019.7",
            build_id="123.1",
            run_number="7",
        )
        self.assertEqual(info["product_version"], "0.0.1-dev.20261019.7")
        self.assertEqual(info["build_id"], "123.1")

    def test_derived_versions_require_the_run_number_and_matching_attempt(self):
        valid = dict(
            channel="dev",
            product_version="0.0.1-dev.20261019.7.2",
            build_id="123.2",
            run_number="7",
        )
        cases = [
            dict(run_number=value) for value in (None, "", "0", "01", "-1", "x", 7)
        ]
        cases += [dict(run_number="8")]
        cases += [
            dict(build_id=value)
            for value in (None, "123", "123.1", "0.2", "123.0", "123.02")
        ]
        cases += [
            dict(product_version="0.0.1-dev.20261019.7", build_id="123.2"),
            dict(product_version="0.0.1-dev.20261019.7.1", build_id="123.1"),
            dict(product_version="0.0.1-dev.20261019.7.3"),
            dict(product_version="0.0.1-dev.20261019.07.2"),
        ]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.info(**(valid | changes))

    def test_daily_version_uses_the_selected_revision_without_run_or_attempt_suffixes(self):
        for run, number in (("123.1", "7"), ("123.2", "7"), ("124.1", "8")):
            with self.subTest(run=run):
                info = self.info(channel="daily", product_version="0.0.1-daily.20261021",
                                 snapshot_date="20261021", daily_revision="1", build_id=run, run_number=number)
                self.assertEqual(info["product_version"], "0.0.1-daily.20261021")
                self.assertEqual(info["build_id"], run)
        second = self.info(channel="daily", product_version="0.0.1-daily.20261021.2",
                           snapshot_date="20261021", daily_revision="2", build_id="124.1")
        self.assertEqual(second["product_version"], "0.0.1-daily.20261021.2")

    def test_daily_identity_rejects_mismatched_snapshot_revision_and_channel(self):
        valid = dict(channel="daily", product_version="0.0.1-daily.20261021", snapshot_date="20261021",
                     daily_revision="1", build_id="123.2")
        changes = [dict(snapshot_date=value) for value in ("20260230", "2026102", "20261022", 20261021)]
        changes += [dict(daily_revision=value) for value in ("0", "01", "2", "", None, 1)]
        changes += [dict(build_id=value) for value in (None, "123", "123.0", "123.01")]
        changes += [dict(product_version=value) for value in
                    ("0.0.1-daily.20261021.1", "0.0.1-daily.20261021.2", "0.0.1-daily.20260230", "0.1.0-daily.20261021")]
        changes += [dict(channel=value) for value in ("dev", "rc", "stable")]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.info(**(valid | change))

    def test_version_override_cannot_claim_another_base_or_release_channel(self):
        cases = [
            dict(product_version="0.1.0-dev.20261019.7.2"),
            dict(product_version="0.0.1-dev.20260230.7.2"),
            dict(product_version="0.0.1-dev.20261019.7.2", build_id="123.1"),
            dict(product_version="0.0.1-dev.20261019.7.2", channel="stable"),
            dict(product_version="0.1.0"),
        ]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.info(**(dict(build_id="123.2", run_number="7") | changes))

    def test_declared_sha_cannot_differ_from_checkout(self):
        with self.assertRaisesRegex(ValueError, "checkout"):
            self.info(source_sha="a" * 40)

    def test_invalid_version_is_rejected(self):
        (self.root / "VERSION").write_text("latest\n")
        with self.assertRaisesRegex(ValueError, "VERSION"):
            self.info()

    def test_invalid_timestamp_separator_is_rejected(self):
        for value in ("2026-10-16X00:00:00Z", "2026-10-16T00:00:00+00:60"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "time"):
                make_info(self.root, "aivision-be", built_at=value)

    def test_non_git_source_does_not_claim_clean_or_verified_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "VERSION").write_text("0.0.1\n")
            info = make_info(root, "aivision-be")
            self.assertIsNone(info["source_sha"])
            self.assertIsNone(info["dirty"])
            with self.assertRaises(ValueError):
                make_info(root, "aivision-be", channel="stable")

    def test_cli_produces_json_and_same_base64_payload(self):
        result = subprocess.run(
            [
                "python3",
                str(Path(__file__).with_name("build_info.py")),
                "--root",
                str(self.root),
                "--component",
                "aivision-be",
                "--format",
                "base64",
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        info = json.loads(base64.b64decode(result.stdout))
        self.assertEqual(info["component"], "aivision-be")
        self.assertEqual(info["source_sha"], self.sha)
        self.assertEqual(info["product_version"], "0.0.1")


if __name__ == "__main__":
    unittest.main()
