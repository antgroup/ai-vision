"""Exercise the formal publisher against interrupted GitHub and registry writes."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from product_registry import PUBLIC_REPOSITORY, product_image_repositories
from release_publish import main
from test_release import LIVE_REPOSITORY, PUBLIC_IMAGES, SHA, plan


class FormalPublicationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.directory = root / "assets"
        self.directory.mkdir()
        self.files = {"aiv-linux-amd64.tar.gz": b"frozen archive", "SHA256SUMS": b"frozen checksum"}
        for name, content in self.files.items():
            (self.directory / name).write_bytes(content)
        self.acceptance = root / "candidate-acceptance.json"
        self.acceptance.write_text("{}")
        self.journal = root / "formal-publication.json"
        self.receipt = root / "receipt.json"
        self.package = product_image_repositories()
        self.latest = 41
        self.env_protected = True
        self.plan = plan(event="push", version="0.2.0", ref="refs/tags/v0.2.0")
        self.prior_source = "d" * 40
        self.prior_digest = "sha256:" + "e" * 64
        self.releases = {
            41: {"id": 41, "tag_name": "v0.1.0", "draft": False, "prerelease": False,
                 "published_at": "2026-11-24T00:00:00Z"},
            42: {"id": 42, "tag_name": "v0.2.0", "draft": True, "immutable": False,
                 "name": "Ant AI Vision 0.2.0", "body": "## English\nChange\n## 中文\n变更"},
        }
        prior_manifest = json.dumps({
            "source_repository": PUBLIC_REPOSITORY, "source_sha": self.prior_source,
            "channel": "stable", "release_tag": "v0.1.0", "product_version": "0.1.0",
            "images": {name: f"{package}@{self.prior_digest}" for name, package in self.package.items()},
        }).encode()
        self.attachments = {
            41: {"release-manifest.json": prior_manifest,
                 "SHA256SUMS": f"{hashlib.sha256(prior_manifest).hexdigest()}  release-manifest.json\n".encode()},
            42: {},
        }
        self.tags = {f"{package}:{tag}": self.prior_digest
                     for package in self.package.values() for tag in ("latest", "0.1.0")}
        self.uploaded_assets = {}
        self.starter_assets = set()
        self.writes = []
        self.interrupt_after = None

    def api(self, path, *, data=None, missing=False, **_):
        suffix = path.removeprefix(f"repos/{PUBLIC_REPOSITORY}")
        if suffix == "":
            return LIVE_REPOSITORY
        if suffix == "/environments/release":
            return {"can_admins_bypass": not self.env_protected,
                    "protection_rules": [{"type": "required_reviewers", "prevent_self_review": True,
                                          "reviewers": [{"id": 1}]}]}
        if suffix == "/releases/latest":
            return self.release(self.latest) if self.latest else None
        if suffix == "/releases?per_page=100&page=1":
            return [self.release(release_id) for release_id in self.releases]
        if suffix.startswith("/git/ref/tags/"):
            tag = suffix.rsplit("/", 1)[1]
            if tag not in {"v0.1.0", self.plan["release_tag"]}:
                raise AssertionError(path)
            return {"object": {"type": "commit", "sha": self.prior_source if tag == "v0.1.0" else SHA}}
        if suffix.startswith("/releases/tags/"):
            tag = suffix.rsplit("/", 1)[1]
            return next((self.release(release_id) for release_id, release in self.releases.items()
                         if release["tag_name"] == tag and not release["draft"]), None)
        if suffix.startswith("/releases/"):
            release_id = int(suffix.split("/")[2])
            if "/assets?" in suffix:
                return self.assets(release_id)
            if data is not None:
                self.writes.append(("publish", release_id))
                self.releases[release_id].update(data, published_at="2026-11-26T00:00:00Z")
                if data.get("make_latest") == "true":
                    self.latest = release_id
            return self.release(release_id)
        raise AssertionError(path)

    def assets(self, release_id):
        result = []
        for name, content in self.attachments[release_id].items():
            key = (release_id, name)
            if key not in self.uploaded_assets:
                self.uploaded_assets[key] = len(self.uploaded_assets) + 1
            result.append({"name": name, "id": self.uploaded_assets[key], "size": len(content),
                           "state": "starter" if key in self.starter_assets else "uploaded"})
        return result

    def release(self, release_id):
        return {**self.releases[release_id], "assets": self.assets(release_id)}

    def download(self, command, *, stdout, **_):
        asset_id = int(command[2].rsplit("/", 1)[1])
        release_id, name = next(key for key, value in self.uploaded_assets.items() if value == asset_id)
        stdout.write(self.attachments[release_id][name])
        return subprocess.CompletedProcess(command, 0, stderr=b"")

    def write(self, *args):
        if args[:4] == ("docker", "buildx", "imagetools", "create"):
            tag, ref = args[-2:]
            if self.interrupt_after and tag.endswith(":latest"):
                if sum(operation == "image" and name.endswith(":latest") for operation, name in self.writes) == self.interrupt_after:
                    raise RuntimeError("fixture interrupted latest updates")
            self.tags[tag] = ref.split("@")[1]
            self.writes.append(("image", tag))
            return ""
        if args[:2] == ("gh", "api"):
            endpoint = urlsplit(args[2])
            self.assertTrue(endpoint.path.endswith("/releases/42/assets"))
            name = parse_qs(endpoint.query)["name"][0]
            self.assertNotIn(name, self.attachments[42])
            self.attachments[42][name] = Path(args[args.index("--input") + 1]).read_bytes()
            self.writes.append(("upload", name))
            return ""
        raise AssertionError(args)

    def verify_tag(self, tag, ref):
        self.assertEqual(self.tags[tag], ref.split("@")[1])

    def run_publisher(self, command, *, recover=False, use_journal=True):
        run_id, attempt = ("222", "1") if recover else ("123", "2")
        arguments = ["release_publish.py", command, "--directory", str(self.directory),
                     "--acceptance", str(self.acceptance), "--receipt", str(self.receipt)]
        if use_journal:
            arguments += ["--formal-journal", str(self.journal)]
        if recover:
            arguments.append("--recover-formal")
        with (
            patch.object(sys, "argv", arguments),
            patch.dict(os.environ, GITHUB_SHA=SHA, GITHUB_RUN_ID=run_id, GITHUB_RUN_ATTEMPT=attempt,
                       GITHUB_REPOSITORY=PUBLIC_REPOSITORY, GITHUB_REF=f"refs/tags/{self.plan['release_tag']}"),
            patch("release_candidate.verify_frozen") as frozen,
            patch("release_publish.validate_bundle", return_value=(self.plan, PUBLIC_IMAGES)),
            patch("public_registry.verify_public_images"),
            patch("public_registry.verify_public_tag", side_effect=self.verify_tag),
            patch("release_publish.api", side_effect=self.api),
            patch("release_publish.subprocess.run", side_effect=self.download),
            patch("release_publish.inspect_digest", side_effect=lambda ref, **_: self.tags.get(ref)),
            patch("release_publish.run", side_effect=self.write),
        ):
            main()
            self.assertTrue(frozen.called)
            self.assertEqual({call.kwargs["build_id"] for call in frozen.call_args_list}, {"123.2"})

    def test_recovery_reuses_an_exact_partial_draft(self):
        self.run_publisher("preflight")
        self.assertFalse(self.writes)
        self.attachments[42]["SHA256SUMS"] = self.files["SHA256SUMS"]
        self.run_publisher("publish", recover=True)

        self.assertEqual(self.attachments[42], self.files)
        self.assertEqual([name for action, name in self.writes if action == "upload"], ["aiv-linux-amd64.tar.gz"])
        self.assertEqual(self.latest, 42)
        self.assertEqual(self.releases[42]["body"], "## English\nChange\n## 中文\n变更")
        receipt = json.loads(self.receipt.read_text())
        self.assertEqual(receipt["candidate_build_id"], "123.2")
        self.assertEqual(receipt["publisher_build_id"], "222.1")

    def test_stable_recovery_repairs_only_unswitched_latest_aliases(self):
        self.run_publisher("preflight")
        self.interrupt_after = 2
        with self.assertRaisesRegex(RuntimeError, "interrupted latest"):
            self.run_publisher("publish")
        self.assertEqual(self.latest, 42)
        self.interrupt_after = None
        self.writes.clear()

        self.run_publisher("publish", recover=True)

        self.assertEqual(len(self.writes), 3)
        self.assertTrue(all(action == "image" and name.endswith(":latest") for action, name in self.writes))
        for name, package in self.package.items():
            self.assertEqual(self.tags[f"{package}:latest"], PUBLIC_IMAGES[name].split("@")[1])

    def test_recovery_rejects_unknown_starter_and_mismatched_draft_assets(self):
        self.run_publisher("preflight")
        for attachments, starter_assets, error in (
            ({"unexpected": b"extra"}, set(), "asset set"),
            ({"SHA256SUMS": self.files["SHA256SUMS"]}, {(42, "SHA256SUMS")}, "size or state"),
            ({"SHA256SUMS": b"x" * len(self.files["SHA256SUMS"])}, set(), "asset bytes"),
        ):
            with self.subTest(attachments=attachments, starter_assets=starter_assets):
                self.attachments[42] = attachments
                self.starter_assets = starter_assets
                with self.assertRaisesRegex(ValueError, error):
                    self.run_publisher("publish", recover=True)
                self.assertFalse(self.writes)
                self.assertTrue(self.releases[42]["draft"])

    def test_recovery_checks_all_stable_aliases_before_writing_any(self):
        self.run_publisher("preflight")
        self.interrupt_after = 1
        with self.assertRaisesRegex(RuntimeError, "interrupted latest"):
            self.run_publisher("publish")
        self.interrupt_after = None
        self.writes.clear()
        self.tags[f"{self.package['signoz-ob']}:latest"] = "sha256:" + "f" * 64

        with self.assertRaisesRegex(ValueError, "unknown Stable alias"):
            self.run_publisher("publish", recover=True)

        self.assertFalse(self.writes)

    def test_recovery_rejects_changed_protection_reviewed_metadata_and_latest(self):
        self.run_publisher("preflight")
        self.env_protected = False
        with self.assertRaisesRegex(ValueError, "can_admins_bypass"):
            self.run_publisher("publish", recover=True)
        self.env_protected = True
        self.releases[42]["name"] = "Unreviewed title"
        with self.assertRaisesRegex(ValueError, "title or reviewed notes"):
            self.run_publisher("publish", recover=True)
        self.releases[42]["name"] = "Ant AI Vision 0.2.0"
        self.latest = 99
        self.releases[99] = {"id": 99, "tag_name": "v0.3.0", "draft": False,
                             "prerelease": False, "published_at": "2026-11-27T00:00:00Z"}
        self.attachments[99] = {}
        with self.assertRaisesRegex(ValueError, "latest Release changed"):
            self.run_publisher("publish", recover=True)
        self.assertFalse(self.writes)

    def test_recovery_accepts_an_already_published_rc_without_writing_any_alias(self):
        self.plan = plan(event="push", version="0.2.0-rc.1", ref="refs/tags/v0.2.0-rc.1")
        self.releases[42]["tag_name"] = "v0.2.0-rc.1"
        self.run_publisher("preflight")
        self.run_publisher("publish")
        self.assertEqual(self.attachments[42], self.files)
        self.assertEqual(self.releases[42]["name"], "Ant AI Vision 0.2.0")
        self.assertEqual(self.releases[42]["body"], "## English\nChange\n## 中文\n变更")
        stable_aliases = {tag: digest for tag, digest in self.tags.items() if tag.endswith(":latest")}
        self.writes.clear()

        self.run_publisher("publish", recover=True)

        self.assertFalse(self.writes)
        self.assertEqual(self.latest, 41)
        self.assertTrue(self.releases[42]["prerelease"])
        self.assertEqual({tag: digest for tag, digest in self.tags.items() if tag.endswith(":latest")},
                         stable_aliases)

    def test_formal_preflight_can_run_without_a_journal_but_publication_cannot(self):
        self.run_publisher("preflight", use_journal=False)
        self.assertFalse(self.journal.exists())

        with self.assertRaisesRegex(ValueError, "persisted formal journal"):
            self.run_publisher("publish", use_journal=False)

        self.assertFalse(self.writes)
