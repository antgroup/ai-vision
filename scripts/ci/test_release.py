"""Release guard regressions: fail closed before publishing, and bind exact inputs."""

import copy
from contextlib import contextmanager
import base64
import hashlib
import io
import json
import os
import sys
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import tarfile

import yaml

from product_registry import PUBLIC_REPOSITORY, UPSTREAM_SIGNOZ, product_image_repositories
from release_assets import assemble, DATABASES
from release_image import check_identity
from release_plan import (
    make_plan,
    require_digest,
    require_draft_notes,
    require_platforms,
    require_native_digests,
    require_release_environment,
)
from release_publish import (
    check_version_tags,
    inspect_digest,
    publish_version_tags,
    preflight,
    require_newer_rolling,
    require_live_tag,
    validate_bundle,
    main as publish_main,
)

SHA = "a" * 40
PACKAGES = product_image_repositories()
DIGEST = "sha256:" + "b" * 64
PUBLIC_IMAGES = {name: f"{package}@{DIGEST}" for name, package in PACKAGES.items()}
LIVE_REPOSITORY = {"full_name": PUBLIC_REPOSITORY, "visibility": "public"}
REF = PUBLIC_IMAGES["aivision-be"]


@contextmanager
def mock_inline_acceptance(directory):
    with tempfile.TemporaryDirectory() as root:
        acceptance = Path(root) / "candidate-acceptance.json"
        acceptance.write_text("{}")
        with patch.object(sys, "argv", ["release_publish.py", "publish", "--directory", str(directory),
                                       "--acceptance", str(acceptance)]), patch("release_candidate.verify_frozen"):
            yield


def plan(**changes):
    args = dict(
        version="0.0.1",
        event="schedule",
        ref="refs/heads/main",
        default_branch="main",
        tag="dev",
        run_id="123",
        run_number="7",
        attempt="2",
        source_sha=SHA,
        date="20261019",
    )
    args.update(changes)
    return make_plan(**args)


class PlanningTests(unittest.TestCase):
    def test_first_attempt_omits_the_retry_suffix(self):
        daily = plan(attempt="1")
        self.assertEqual(daily["product_version"], "0.0.1-daily.20261019")
        self.assertEqual(daily["build_id"], "123.1")
        self.assertEqual(daily["candidate_tag"], "candidate-123-1")

    def test_runs_and_retries_preserve_the_daily_version_and_full_build_ids(self):
        first = plan(attempt="1")
        retry = plan(attempt="2")
        next_run = plan(run_id="124", run_number="8", attempt="1")
        self.assertEqual(
            [item["product_version"] for item in (first, retry, next_run)],
            [
                "0.0.1-daily.20261019",
                "0.0.1-daily.20261019",
                "0.0.1-daily.20261019",
            ],
        )
        self.assertEqual(
            [item["build_id"] for item in (first, retry, next_run)],
            ["123.1", "123.2", "124.1"],
        )

    def test_run_number_must_be_a_positive_integer(self):
        for value in ("0", "01", "-1", "1.1", "x", "", None, 1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                plan(run_number=value)

    def test_daily_identity_is_unique_and_tagged_release_uses_root_version(self):
        daily = plan()
        self.assertEqual(daily["product_version"], "0.0.1-daily.20261019")
        self.assertEqual(daily["candidate_tag"], "candidate-123-2")
        self.assertEqual(
            (daily["channel"], daily["tier"], daily["alias"]),
            ("daily", "daily", "daily"),
        )
        stable = plan(event="push", ref="refs/tags/v0.0.1")
        self.assertEqual(
            (stable["product_version"], stable["tier"]), ("0.0.1", "release")
        )
        rc = plan(event="push", version="0.1.0-rc.1", ref="refs/tags/v0.1.0-rc.1")
        self.assertEqual(rc["channel"], "rc")

    def test_selected_snapshot_and_revision_identify_daily_candidates(self):
        daily = plan(event="workflow_dispatch", ref="refs/heads/feature", tag="trial-abc",
                     date="20261022", snapshot_date="20261021", candidate_channel="daily")
        self.assertEqual(daily["product_version"], "0.0.1-daily.20261021")
        self.assertEqual((daily["snapshot_date"], daily["daily_revision"]), ("20261021", "1"))
        self.assertEqual((daily["alias"], daily["channel"], daily["tier"]), ("trial-abc", "daily", "daily"))
        second = plan(event="workflow_dispatch", ref="refs/heads/feature", tag="trial-second",
                      snapshot_date="20261021", daily_revision="2", candidate_channel="daily")
        self.assertEqual(second["product_version"], "0.0.1-daily.20261021.2")
        automatic = plan(event="workflow_dispatch", ref="refs/heads/feature", tag="trial-auto")
        self.assertEqual((automatic["product_version"], automatic["channel"]),
                         ("0.0.1-dev.20261019.7.2", "dev"))

    def test_invalid_daily_selection_and_stable_overrides_fail_closed(self):
        invalid = [dict(snapshot_date=value) for value in
                   ("20260230", "2026102", "2026-10-21", " 20261021", 20261021)]
        invalid += [dict(daily_revision=value) for value in ("0", "01", "-1", "x", "", None, 2)]
        invalid += [dict(candidate_channel=value) for value in ("stable", "rc", "", None)]
        invalid += [dict(tag="daily", candidate_channel="dev"), dict(tag="dev", candidate_channel="daily"),
                    dict(tag="trial", candidate_channel="dev", daily_revision="2")]
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                plan(**(dict(event="workflow_dispatch") | changes))
        with self.assertRaises(ValueError):
            plan(snapshot_date="20261021")  # A scheduled run uses its own fixed date.
        for changes in (dict(daily_revision="2"), dict(candidate_channel="daily"), dict(candidate_channel="dev")):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                plan(**changes)
        for changes in (dict(snapshot_date="20261021"), dict(daily_revision="2"), dict(candidate_channel="daily")):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                plan(**(dict(event="workflow_dispatch", ref="refs/tags/v0.0.1") | changes))

    def test_no_rolling_develop_fallback_or_reserved_tag_bypass(self):
        cases = [
            dict(ref="refs/heads/develop"),
            dict(event="push", ref="refs/tags/v0.1.0"),
            dict(date="20260230"),
            dict(source_sha="abc"),
            dict(attempt="0"),
        ]
        cases += [
            dict(event="workflow_dispatch", tag=tag)
            for tag in ("latest", "0.1", "v1.2.3", "candidate-123-2", "x\ny")
        ]
        for change in cases:
            with self.subTest(change=change), self.assertRaises(ValueError):
                plan(**change)
        self.assertEqual(
            plan(event="workflow_dispatch", ref="refs/heads/feature", tag="trial-abc")[
                "alias"
            ],
            "trial-abc",
        )

    def test_digest_and_manifest_require_exact_two_native_platforms(self):
        self.assertEqual(require_digest(REF), REF)
        for invalid in (
            REF.replace("@sha256:", ":"),
            "ghcr.io/ob-labs/component:latest",
            "",
            None,
        ):
            with self.assertRaises(ValueError):
                require_digest(invalid)
        valid = {
            "manifests": [
                {"platform": {"os": "linux", "architecture": arch}}
                for arch in ("amd64", "arm64")
            ]
        }
        require_platforms(valid)
        require_platforms(
            {
                "manifests": valid["manifests"]
                + [{"platform": {"os": "unknown", "architecture": "unknown"}}]
            }
        )
        for values in (valid["manifests"][:1], valid["manifests"] * 2):
            with self.assertRaises(ValueError):
                require_platforms({"manifests": values})

    def test_index_must_contain_exactly_the_two_verified_native_children(self):
        expected = {arch: "sha256:" + value * 64 for arch, value in
                    (("amd64", "b"), ("arm64", "c"))}
        manifest = {"manifests": [
            {"digest": digest, "platform": {"os": "linux", "architecture": arch}}
            for arch, digest in expected.items()]}
        require_native_digests(manifest, expected)
        for changed in (
            dict(expected, arm64="sha256:" + "d" * 64),
            {"amd64": expected["amd64"]},
            {"amd64": expected["arm64"], "arm64": expected["amd64"]},
        ):
            with self.subTest(changed=changed), self.assertRaisesRegex(ValueError, "verified native"):
                require_native_digests(manifest, changed)

    def test_workflow_digest_artifact_layout_binds_architecture_to_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            descriptors = []
            for arch, value in (("amd64", "b"), ("arm64", "c")):
                artifact = root / "digests" / f"digests-1-aivision-be-{arch}"
                artifact.mkdir(parents=True)
                (artifact / (value * 64)).touch()
                descriptors.append(dict(digest="sha256:" + value * 64,
                    platform=dict(os="linux", architecture=arch)))
            manifest = root / "manifest.json"
            command = [sys.executable, str(Path(__file__).with_name("release_plan.py")),
                       "platforms", "--file", str(manifest),
                       "--digests-directory", str(root / "digests")]
            manifest.write_text(json.dumps(dict(manifests=descriptors)))
            good = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(good.returncode, 0, good.stderr)
            descriptors[0]["platform"]["architecture"] = "arm64"
            descriptors[1]["platform"]["architecture"] = "amd64"
            manifest.write_text(json.dumps(dict(manifests=descriptors)))
            bad = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(bad.returncode, 0)
            self.assertIn("verified native", bad.stderr)

    def test_environment_requires_real_reviewers_and_no_self_review(self):
        rule = {
            "type": "required_reviewers",
            "prevent_self_review": True,
            "reviewers": [{"type": "Team", "reviewer": {"id": 1}}],
        }
        require_release_environment(
            {"can_admins_bypass": False, "protection_rules": [rule]}
        )
        for invalid in (
            {},
            {"protection_rules": [rule]},
            {"can_admins_bypass": True, "protection_rules": [rule]},
            {"can_admins_bypass": False, "protection_rules": []},
            {
                "can_admins_bypass": False,
                "protection_rules": [{**rule, "reviewers": []}],
            },
            {
                "can_admins_bypass": False,
                "protection_rules": [{**rule, "prevent_self_review": False}],
            },
        ):
            with self.assertRaises(ValueError):
                require_release_environment(invalid)

    def test_bilingual_draft_requires_content_but_preserves_body(self):
        body = "## English\nA documented change.\n## 中文\n已记录的变更。\n## Contributors / 贡献者\nActual credits."
        draft = {"id": 42, "draft": True, "body": body, "assets": []}
        self.assertEqual(require_draft_notes(draft), 42)
        self.assertEqual(draft["body"], body)
        for changed in (
            {"draft": False},
            {"immutable": True},
            {"assets": [{"id": 1}]},
            {"body": "## English\n<!-- pending -->\n### Highlights\n## 中文\n说明"},
            {"body": "## English\nHello"},
        ):
            with self.assertRaises(ValueError):
                require_draft_notes({**draft, **changed})


class PublicationTests(unittest.TestCase):
    def test_live_release_tag_peels_annotations_and_rejects_changes(self):
        with patch(
            "release_publish.api",
            return_value={"object": {"type": "commit", "sha": SHA}},
        ):
            require_live_tag("antgroup/ai-vision", "v0.0.1", SHA)
        responses = [
            {"object": {"type": "tag", "sha": "c" * 40}},
            {"object": {"type": "commit", "sha": SHA}},
        ]
        with patch("release_publish.api", side_effect=responses) as api:
            require_live_tag("antgroup/ai-vision", "v0.0.1", SHA)
            self.assertEqual(
                api.call_args.args[0], "repos/antgroup/ai-vision/git/tags/" + "c" * 40
            )
        with patch(
            "release_publish.api",
            return_value={"object": {"type": "commit", "sha": "d" * 40}},
        ):
            with self.assertRaisesRegex(ValueError, "moved"):
                require_live_tag("antgroup/ai-vision", "v0.0.1", SHA)
        with patch("release_publish.api", side_effect=RuntimeError("HTTP 404")):
            with self.assertRaises(RuntimeError):
                require_live_tag("antgroup/ai-vision", "v0.0.1", SHA)
        with patch(
            "release_publish.api",
            return_value={"object": {"type": "tag", "sha": "c" * 40}},
        ):
            with self.assertRaisesRegex(ValueError, "nesting"):
                require_live_tag("antgroup/ai-vision", "v0.0.1", SHA)

    def test_stable_preflight_checks_five_service_version_tags(self):
        environment = {
            "can_admins_bypass": False,
            "protection_rules": [{"type": "required_reviewers",
                "prevent_self_review": True,
                "reviewers": [{"type": "Team", "reviewer": {"id": 1}}]}],
        }
        draft = {"id": 42, "draft": True, "assets": [],
                 "body": "## English\nChange\n## 中文\n变更"}
        images = PUBLIC_IMAGES
        with (
            patch("release_publish.api", side_effect=[environment,
                  {"object": {"type": "commit", "sha": SHA}}, draft, None]),
            patch("release_publish.inspect_digest", return_value=None) as inspect,
        ):
            preflight(plan(event="push", ref="refs/tags/v0.0.1"), images,
                      "antgroup/ai-vision")
            self.assertEqual({call.args[0] for call in inspect.call_args_list},
                             {f"{package}:0.0.1" for package in PACKAGES.values()})

    def test_identical_version_tags_are_idempotent_and_conflicts_write_nothing(self):
        images = PUBLIC_IMAGES
        with (
            patch("release_publish.inspect_digest", return_value="sha256:" + "b" * 64),
            patch("public_registry.verify_public_tag") as verify,
            patch("release_publish.run") as write,
        ):
            publish_version_tags(plan(), images)
            write.assert_not_called()
            self.assertEqual(verify.call_count, 5)
        # The final component conflicts: none of the preceding absent tags may
        # be created until the entire owned-image set has been checked.
        with (
            patch("release_publish.inspect_digest", side_effect=[None] * 4 + ["sha256:" + "c" * 64]),
            patch("release_publish.run") as write,
        ):
            with self.assertRaisesRegex(ValueError, "conflicting"):
                publish_version_tags(plan(), images)
            write.assert_not_called()

    def test_absent_tags_are_created_for_five_service_images_then_verified(self):
        images = PUBLIC_IMAGES
        with (
            patch("release_publish.inspect_digest", return_value=None),
            patch("public_registry.verify_public_tag") as verify,
            patch("release_publish.run") as write,
        ):
            publish_version_tags(plan(), images)
            self.assertEqual(write.call_count, 5)
            for call in write.call_args_list:
                self.assertIn(":" + plan()["product_version"], call.args[-2])
            self.assertEqual(verify.call_count, 10)

    def test_stable_and_daily_aliases_follow_five_service_immutable_readbacks(self):
        images = PUBLIC_IMAGES
        for product_plan, alias in ((plan(), "daily"),
                                    (plan(event="push", ref="refs/tags/v0.0.1"), "latest")):
            events = []
            with (
                self.subTest(alias=alias),
                tempfile.TemporaryDirectory() as directory,
                mock_inline_acceptance(directory),
                patch.dict(os.environ, GITHUB_SHA=SHA, GITHUB_RUN_ID="123", GITHUB_RUN_ATTEMPT="2", GITHUB_REPOSITORY="antgroup/ai-vision"),
                patch("public_registry.verify_public_images"),
                patch("release_publish.validate_bundle", return_value=(product_plan, images)),
                patch("release_publish.preflight", return_value={"id": 42}),
                patch("release_publish.verify_remote_assets", side_effect=lambda *args: events.append(("remote-verified",))),
                patch("release_publish.api", side_effect=lambda path, **kwargs: {"body": "## Changes\n* Fixture"} if path.endswith("/generate-notes") else (LIVE_REPOSITORY if path == f"repos/{PUBLIC_REPOSITORY}" else None)),
                patch("release_publish.inspect_digest", return_value=None),
                patch("release_publish.run", side_effect=lambda *args, **kwargs: events.append(("write", args)) or ""),
                patch("public_registry.verify_public_tag", side_effect=lambda ref, expected: events.append(("verified", ref))),
            ):
                publish_main()
            first_alias = next(index for index, event in enumerate(events)
                               if event[0] == "write" and event[1][0] == "docker"
                               and event[1][-2].endswith(":" + alias))
            verified = {event[1] for event in events[:first_alias] if event[0] == "verified"}
            self.assertEqual(verified, {f"{package}:{product_plan['product_version']}" for package in PACKAGES.values()})
            aliases = {event[1][-2] for event in events if event[0] == "write"
                       and event[1][0] == "docker" and event[1][-2].endswith(":" + alias)}
            self.assertEqual(aliases, {f"{package}:{alias}" for package in PACKAGES.values()})

    def test_promotion_keeps_current_publisher_identity_and_validates_original_build(self):
        import release_candidate
        with tempfile.TemporaryDirectory() as root:
            acceptance = Path(root) / "acceptance.json"
            acceptance.write_text("{}")
            output = Path(root) / "receipt.json"
            images = PUBLIC_IMAGES
            arguments = ["release_publish.py", "publish", "--directory", root,
                         "--candidate-acceptance", str(acceptance), "--candidate-run-id", "123",
                         "--candidate-run-attempt", "2", "--receipt", str(output)]
            with (
                patch.object(sys, "argv", arguments),
                patch.dict(os.environ, GITHUB_SHA=SHA, GITHUB_RUN_ID="555", GITHUB_RUN_ATTEMPT="1",
                           GITHUB_REPOSITORY="antgroup/ai-vision"),
                patch("release_candidate.verify_remote_candidate", return_value={}) as gate,
                patch("public_registry.verify_public_images"),
                patch("release_publish.validate_bundle", return_value=(plan(), images)) as validate,
                patch("release_publish.check_version_tags"),
                patch("release_publish.publish_version_tags"),
                patch("release_publish.api", side_effect=lambda path, **kwargs:
                      {"body": "Changes"} if path.endswith("/generate-notes") else (LIVE_REPOSITORY if path == f"repos/{PUBLIC_REPOSITORY}" else None)),
                patch("release_publish.verify_remote_assets", return_value={}),
                patch("release_publish.run"),
                patch("public_registry.verify_public_tag"),
            ):
                publish_main()
                self.assertEqual(validate.call_args.args[2], "123.2")
                self.assertEqual(os.environ["GITHUB_RUN_ID"], "555")
                self.assertEqual(gate.call_args.kwargs["candidate_run_id"], "123")
                self.assertEqual(gate.call_count, 2)
                receipt = json.loads(output.read_text())
                self.assertEqual(receipt["publisher_build_id"], "555.1")
                self.assertEqual(receipt["candidate_build_id"], "123.2")

    def test_remote_asset_bytes_sizes_and_exact_set_are_verified(self):
        import release_publish
        import hashlib
        self.assertTrue(callable(getattr(release_publish, "verify_remote_assets", None)))
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            (directory / "SHA256SUMS").write_bytes(b"fixture checksum bytes")
            content = (directory / "SHA256SUMS").read_bytes()
            asset = {"id": 12, "name": "SHA256SUMS", "size": len(content), "state": "uploaded"}
            def api(path, **kwargs):
                if "/git/ref/" in path:
                    return {"object": {"type": "commit", "sha": SHA}}
                if "/releases/tags/" in path:
                    return {"id": 42, "tag_name": plan()["release_tag"]}
                return [asset]
            def download(command, **kwargs):
                self.assertIn("repos/antgroup/ai-vision/releases/assets/12", command)
                self.assertIn("Accept: application/octet-stream", command)
                kwargs["stdout"].write(content)
                return subprocess.CompletedProcess(command, 0, stderr=b"")
            with patch("release_publish.api", side_effect=api), patch("release_publish.subprocess.run", side_effect=download):
                receipt = release_publish.verify_remote_assets(directory, plan(), "antgroup/ai-vision")
                self.assertEqual(receipt["assets"]["SHA256SUMS"]["sha256"], hashlib.sha256(content).hexdigest())
                for mutation in (dict(size=1), dict(name="runtime.log"), dict(state="open")):
                    original = asset.copy()
                    asset.update(mutation)
                    with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                        release_publish.verify_remote_assets(directory, plan(), "antgroup/ai-vision")
                    asset.clear()
                    asset.update(original)
                (directory / "SHA256SUMS").write_bytes(b"x" * len(content))
                with self.assertRaisesRegex(ValueError, "bytes"):
                    release_publish.verify_remote_assets(directory, plan(), "antgroup/ai-vision")

    def test_remote_readback_failure_prevents_all_rolling_alias_writes(self):
        import release_publish
        self.assertTrue(callable(getattr(release_publish, "verify_remote_assets", None)))
        images = PUBLIC_IMAGES
        with (
            tempfile.TemporaryDirectory() as root,
            mock_inline_acceptance(root),
            patch.dict(os.environ, GITHUB_SHA=SHA, GITHUB_RUN_ID="123", GITHUB_RUN_ATTEMPT="2",
                       GITHUB_REPOSITORY="antgroup/ai-vision"),
            patch("public_registry.verify_public_images"),
                patch("release_publish.validate_bundle", return_value=(plan(), images)),
            patch("release_publish.check_version_tags"),
            patch("release_publish.publish_version_tags"),
            patch("release_publish.api", side_effect=lambda path, **kwargs:
                  {"body": "Changes"} if path.endswith("/generate-notes") else (LIVE_REPOSITORY if path == f"repos/{PUBLIC_REPOSITORY}" else None)),
            patch("release_publish.verify_remote_assets", side_effect=ValueError("remote bytes differ")),
            patch("release_publish.run") as writes,
        ):
            with self.assertRaisesRegex(ValueError, "remote bytes"):
                publish_main()
            self.assertFalse(any(call.args[0] == "docker" for call in writes.call_args_list))
            self.assertFalse(any("daily" in call.args for call in writes.call_args_list))

    def test_trial_alias_publication_is_rejected_before_version_writes(self):
        trial = plan(event="workflow_dispatch", ref="refs/heads/feature", tag="trial-abc")
        with (
            tempfile.TemporaryDirectory() as root,
            mock_inline_acceptance(root),
            patch.dict(os.environ, GITHUB_SHA=SHA, GITHUB_RUN_ID="123", GITHUB_RUN_ATTEMPT="2",
                       GITHUB_REPOSITORY="antgroup/ai-vision"),
            patch("public_registry.verify_public_images"),
                patch("release_publish.validate_bundle", return_value=(trial, PUBLIC_IMAGES)),
            patch("release_publish.check_version_tags"),
            patch("release_publish.publish_version_tags") as writes,
            patch("release_publish.verify_remote_assets", return_value={}),
            patch("release_publish.run"),
            patch("public_registry.verify_public_tag"),
        ):
            with self.assertRaisesRegex(ValueError, "trial"):
                publish_main()
            writes.assert_not_called()

    def test_registry_errors_are_not_treated_as_nonexistent_images(self):
        for code, message, error in [
                        (1, "unauthorized", RuntimeError),
            (1, "unauthorized: repository not found", RuntimeError),
            (1, "timeout", RuntimeError),
        ]:
            with patch(
                "release_publish.subprocess.run",
                return_value=subprocess.CompletedProcess([], code, "", message),
            ):
                with self.assertRaises(error):
                    inspect_digest("ghcr.io/example/image:0.0.1", missing=True)
        with patch(
            "release_publish.subprocess.run",
            return_value=subprocess.CompletedProcess([], 1, "", "manifest unknown"),
        ):
            inspect_digest("ghcr.io/example/image:0.0.1", missing=True)

    def test_no_external_alias_substitution(self):
        images = {**PUBLIC_IMAGES, "signoz-ob": f"{UPSTREAM_SIGNOZ}@{DIGEST}"}
        with patch("release_publish.inspect_digest") as inspect:
            with self.assertRaisesRegex(ValueError, "official component packages"):
                publish_version_tags(plan(), images)
            inspect.assert_not_called()

    def test_environment_failure_stops_before_registry_checks(self):
        with (
            patch("release_publish.api", return_value={}),
            patch("release_publish.check_version_tags") as check,
        ):
            with self.assertRaises(ValueError):
                preflight(
                    plan(event="push", ref="refs/tags/v0.0.1"),
                    {},
                    "antgroup/ai-vision",
                )
            check.assert_not_called()

    def test_rolling_immutable_and_stale_attempts_are_rejected(self):
        for release in (
            {"immutable": True},
            {"body": "<!-- aivision-build: 123.2 -->"},
            {"body": "<!-- aivision-build: 124.1 -->"},
        ):
            with self.assertRaises(ValueError):
                require_newer_rolling(release, "123.2")
        require_newer_rolling({"body": "<!-- aivision-build: 123.1 -->"}, "123.2")

    def test_image_identity_checks_actual_embedded_build(self):
        expected = dict(
            schema_version=1,
            component_version="0.0.1",
            built_at="2026-10-19T00:00:00Z",
            component="aivision-be",
            product_version="0.0.1",
            source_sha=SHA,
            build_id="123.2",
            target_channel="stable",
            dirty=False,
        )
        check_identity(expected, expected)
        for key in expected:
            with self.subTest(key=key), self.assertRaises(ValueError):
                check_identity({**expected, key: None}, expected)


class GeneratedRollingNotesTests(unittest.TestCase):
    generated = ("## What's Changed\n* Fix trace rendering by @contributor in "
                 "https://github.com/antgroup/ai-vision/pull/107\n\n"
                 "**Full Changelog**: https://github.com/antgroup/ai-vision/compare/"
                 "v0.0.1-daily.20261018...v0.0.1-daily.20261019")

    def publish(self, product_plan, *, generated=None, generator_error=None, existing=True):
        events = []
        previous_tag = "v0.0.1-daily.20261018"
        previous_body = ("[Versioned assets and exact image manifest](https://github.com/"
                         "antgroup/ai-vision/releases/tag/" + previous_tag + ").\n\n"
                         "<!-- aivision-build: 122.1 -->")
        if isinstance(existing, str):
            previous_body = existing
        previous_manifest = json.dumps({"source_repository": "antgroup/ai-vision",
            "release_tag": previous_tag, "product_version": previous_tag[1:],
            "channel": "daily", "source_sha": "b" * 40, "build_id": "122.1"}).encode()
        metadata = {10: previous_manifest, 11:
            (hashlib.sha256(previous_manifest).hexdigest() + "  release-manifest.json\n").encode()}

        def api(path, **kwargs):
            events.append(("api", path, kwargs))
            if path == f"repos/{PUBLIC_REPOSITORY}":
                return LIVE_REPOSITORY
            if path.endswith("/releases/generate-notes"):
                if generator_error:
                    raise generator_error
                return {"body": self.generated if generated is None else generated}
            if path.endswith("/releases/tags/" + product_plan["alias"]):
                return {"id": 200, "tag_name": product_plan["alias"], "draft": False, "body": previous_body} if existing else None
            if path.endswith("/releases/tags/" + previous_tag):
                return {"id": 199, "tag_name": previous_tag, "draft": False,
                        "published_at": "2026-10-18T00:00:00Z"}
            if "/releases/199/assets?" in path:
                return [{"id": asset_id, "name": name, "size": len(metadata[asset_id]), "state": "uploaded"}
                        for asset_id, name in ((10, "release-manifest.json"), (11, "SHA256SUMS"))]
            if "/assets?" in path:
                return []
            if "/git/ref/tags/" in path:
                return {"object": {"type": "commit", "sha": "b" * 40}} if existing else None
            if "/compare/" in path:
                return {"status": "ahead"}
            return None

        def download(command, **kwargs):
            kwargs["stdout"].write(metadata[int(command[2].rsplit("/", 1)[1])])
            return subprocess.CompletedProcess(command, 0, stderr=b"")

        images = PUBLIC_IMAGES
        with (
            tempfile.TemporaryDirectory() as directory,
            mock_inline_acceptance(directory),
            patch.dict(os.environ, GITHUB_SHA=SHA, GITHUB_RUN_ID="123", GITHUB_RUN_ATTEMPT="2", GITHUB_REPOSITORY="antgroup/ai-vision"),
            patch("public_registry.verify_public_images"),
                patch("release_publish.validate_bundle", return_value=(product_plan, images)),
            patch("release_publish.preflight", return_value={"id": 42}),
            patch("release_publish.verify_remote_assets", side_effect=lambda directory, value, repository: events.append(("remote-readback", value["release_tag"])) or {}),
            patch("release_publish.check_version_tags"),
            patch("release_publish.publish_version_tags", side_effect=lambda *args, **kwargs: events.append(("version-tags",))),
            patch("release_publish.api", side_effect=api),
            patch("release_publish.run", side_effect=lambda *args, **kwargs: events.append(("run", args)) or ""),
            patch("public_registry.verify_public_tag"),
            patch("release_publish.subprocess.run", side_effect=download),
        ):
            try:
                publish_main()
            except Exception as error:
                error.events = events
                raise
        return events

    def test_changes_authors_and_fixed_range_precede_deployment_and_build_details(self):
        for alias in ("daily", "dev"):
            product_plan = plan() if alias == "daily" else plan(event="workflow_dispatch", tag="dev")
            with self.subTest(alias=alias):
                events = self.publish(product_plan)
                generated = [(i, e) for i, e in enumerate(events) if e[0] == "api" and e[1].endswith("/generate-notes")]
                self.assertEqual(len(generated), 1)
                index, request = generated[0]
                self.assertEqual(request[2], {"method": "POST", "data": {
                    "tag_name": product_plan["release_tag"], "target_commitish": SHA,
                    "previous_tag_name": "v0.0.1-daily.20261018"}})
                self.assertLess(index, next(i for i, e in enumerate(events) if e[0] == "version-tags"))
                releases = [e[1] for e in events if e[0] == "run" and e[1][:2] == ("gh", "release") and "--notes" in e[1]]
                self.assertEqual(len(releases), 2)
                for args in releases:
                    notes = args[args.index("--notes") + 1]
                    self.assertEqual(notes.count(self.generated), 1)
                    self.assertLess(notes.index(self.generated), notes.index("## Deployment"))
                    self.assertLess(notes.index("## Deployment"), notes.index("<details>"))
                    self.assertTrue(notes.rstrip().endswith("</details>"))
                    title = args[args.index("--title") + 1]
                    expected = product_plan["product_version"] if args[3] == product_plan["release_tag"] else alias.title() + ": " + product_plan["product_version"]
                    self.assertEqual(title, expected)
                versioned = releases[0][releases[0].index("--notes") + 1]
                self.assertTrue(versioned.startswith(self.generated))
                pointer = releases[1][releases[1].index("--notes") + 1]
                self.assertEqual(pointer.count("[Versioned assets and exact image manifest]"), 1)
                self.assertEqual(pointer.count("<!-- aivision-build: 123.2 -->"), 1)

    def test_dev_assets_are_read_back_from_both_versioned_and_rolling_downloads(self):
        product_plan = plan(event="workflow_dispatch", tag="dev")
        events = self.publish(product_plan)
        self.assertEqual({event[1] for event in events if event[0] == "remote-readback"},
                         {product_plan["release_tag"], "dev"})

    def test_first_rolling_release_uses_exact_target_without_inventing_previous_tag(self):
        events = self.publish(plan(), existing=False)
        requests = [e for e in events if e[0] == "api" and e[1].endswith("/generate-notes")]
        self.assertEqual(len(requests), 1)
        request = requests[0]
        self.assertEqual(request[2]["data"], {"tag_name": plan()["release_tag"], "target_commitish": SHA})
        created = [e[1] for e in events if e[0] == "run" and e[1][:3] == ("gh", "release", "create")]
        self.assertEqual([args[args.index("--title") + 1] for args in created],
                         [plan()["product_version"], "Daily: " + plan()["product_version"]])

    def test_generation_failure_or_missing_previous_version_stops_before_writers(self):
        for kwargs in ({"generated": " "}, {"generator_error": RuntimeError("GitHub unavailable")},
                       {"existing": "Legacy rolling notes without a versioned asset link"}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises((ValueError, RuntimeError)) as caught:
                    self.publish(plan(), **kwargs)
                self.assertFalse(any(e[0] in {"run", "version-tags"} for e in caught.exception.events))

    def test_previous_range_uses_only_the_dedicated_versioned_asset_marker(self):
        pointer = ("[Versioned assets and exact image manifest](https://github.com/"
                   "antgroup/ai-vision/releases/tag/v0.0.1-daily.20261018)")
        unrelated = "[See an older release](https://github.com/antgroup/ai-vision/releases/tag/v0.0.1-daily.20261017)"
        try:
            events = self.publish(plan(), existing=pointer + "\n\n" + unrelated + "\n<!-- aivision-build: 122.1 -->")
        except ValueError as error:
            self.fail(f"A dedicated pointer must ignore unrelated release links: {error}")
        request = next(e for e in events if e[0] == "api" and e[1].endswith("/generate-notes"))
        self.assertEqual(request[2]["data"]["previous_tag_name"], "v0.0.1-daily.20261018")

    def test_missing_or_duplicate_dedicated_markers_stop_before_writers(self):
        pointer = ("[Versioned assets and exact image manifest](https://github.com/"
                   "antgroup/ai-vision/releases/tag/v0.0.1-daily.20261018)")
        unrelated = "[See an older release](https://github.com/antgroup/ai-vision/releases/tag/v0.0.1-daily.20261017)"
        for body in (unrelated, pointer + "\n\n" + pointer,
                     pointer + "\n\n" + pointer.replace("20261018", "20261017")):
            with self.subTest(body=body):
                with self.assertRaisesRegex(ValueError, "one previous versioned-asset link") as caught:
                    self.publish(plan(), existing=body + "\n<!-- aivision-build: 122.1 -->")
                self.assertFalse(any(e[0] in {"run", "version-tags"} for e in caught.exception.events))
                self.assertFalse(any(e[0] == "api" and e[1].endswith("/generate-notes")
                                     for e in caught.exception.events))

    def test_stable_and_rc_preserve_reviewed_notes_and_titles(self):
        for product_plan in (plan(event="push", ref="refs/tags/v0.0.1"),
                             plan(event="push", version="0.1.0-rc.1", ref="refs/tags/v0.1.0-rc.1")):
            with self.subTest(channel=product_plan["channel"]):
                events = self.publish(product_plan)
                self.assertFalse(any(e[0] == "api" and e[1].endswith("/generate-notes") for e in events))
                self.assertTrue(any(e[0] == "run" and e[1][:3] == ("gh", "release", "upload") for e in events))
                for e in events:
                    if e[0] == "run":
                        self.assertNotIn("--notes", e[1])
                        self.assertNotIn("--title", e[1])
                    if e[0] == "api" and e[2].get("data"):
                        self.assertNotIn("body", e[2]["data"])
                        self.assertNotIn("name", e[2]["data"])


class AssetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / "assets"
        self.directory.mkdir()
        self.refs = Path(self.temp.name) / "refs"
        self.refs.mkdir()
        self.plan = plan()
        (self.directory / "release-plan.json").write_text(json.dumps(self.plan))
        (self.directory / "signoz-image.txt").write_text(f"{UPSTREAM_SIGNOZ}@{DIGEST}")
        (self.directory / "data-backend-mirror.json").write_text(json.dumps({
            "upstream_ref": f"{UPSTREAM_SIGNOZ}@{DIGEST}", "mirror_ref": PUBLIC_IMAGES["signoz-ob"],
        }))
        (self.directory / "install-aiv.sh").write_text("#!/bin/sh\n")
        (self.directory / "SHA256SUMS").write_text(
            "initial checksums checked by caller\n"
        )
        names = [
            f"aiv-{os_name}-{arch}.tar.gz"
            for os_name in ("linux", "darwin")
            for arch in ("x64", "arm64")
        ]
        for name in [*names, "plugin-dist.tar.gz"]:
            with tarfile.open(self.directory / name, "w:gz") as archive:
                item = tarfile.TarInfo("fixture")
                item.size = 1
                archive.addfile(item, io.BytesIO(b"x"))
        for name in ("aivision-be", "aivision-fe", "antcollector-gw", "agentlens"):
            (self.refs / f"{name}.txt").write_text(
                PUBLIC_IMAGES[name]
            )
        component_plan = {}
        for name in ("aivision-be", "aivision-fe", "antcollector-gw", "agentlens"):
            identity = dict(schema_version=1, component=name,
                product_version=self.plan["product_version"],
                component_version=self.plan["product_version"], source_sha=SHA,
                build_id="123.2", built_at="2026-10-19T00:00:00Z",
                target_channel="daily", dirty=False)
            (self.refs / f"{name}.build-info.json").write_text(json.dumps(identity))
            component_plan[name] = dict(ref=None, identity=identity, context_sha256="c" * 64)
        (self.directory / "component-plan.json").write_text(json.dumps(component_plan))

    def assemble(self):
        assemble(
            self.directory,
            self.refs,
            source_sha=SHA,
            build_id="123.2",
            repository="antgroup/ai-vision",
        )

    def test_release_vars_and_manifest_are_the_same_smoke_inputs(self):
        self.assemble()
        manifest = json.loads((self.directory / "release-manifest.json").read_text())
        self.assertEqual(manifest["source_repository"], "antgroup/ai-vision")
        self.assertEqual(manifest["release_tag"], self.plan["release_tag"])
        _, images = validate_bundle(self.directory, SHA, "123.2")
        smoke_names = {
            "signoz": "signoz-ob",
            "gateway": "antcollector-gw",
            "backend": "aivision-be",
            "agentlens": "agentlens",
            "frontend": "aivision-fe",
        }
        smoke_vars = {
            "signoz": "SIGNOZ_IMAGE",
            "gateway": "GATEWAY_IMAGE",
            "backend": "BE_IMAGE",
            "agentlens": "AGENTLENS_IMAGE",
            "frontend": "FE_IMAGE",
        }
        for backend, filename in (
            ("oceanbase", "release-images.vars.yaml"),
            ("seekdb", "release-seekdb.vars.yaml"),
        ):
            values = {
                line.split(": ", 1)[0]: json.loads(line.split(": ", 1)[1])
                for line in (self.directory / filename).read_text().splitlines()
                if not line.startswith("#")
            }
            self.assertEqual(
                {name: values[key] for name, key in smoke_vars.items()},
                {name: images[image] for name, image in smoke_names.items()},
            )
            self.assertEqual(
                (values["OB_BACKEND"], values["OB_IMAGE"]),
                (backend, DATABASES[backend]),
            )
            self.assertEqual(json.loads(base64.b64decode(values["AIVISION_RELEASE_MANIFEST_BASE64"])),
                             json.loads((self.directory / "release-manifest.json").read_text()))

    def test_optional_catalog_is_rejected_during_assembly(self):
        (self.directory / "openshell-catalog.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "expected CLI, plugin and plan assets"):
            self.assemble()

    def test_extra_release_asset_is_rejected(self):
        self.assemble()
        (self.directory / "unexpected.txt").write_text("unexpected release asset")
        with self.assertRaisesRegex(ValueError, "allowed product assets"):
            validate_bundle(self.directory, SHA, "123.2")

    def test_release_asset_must_be_a_regular_file(self):
        self.assemble()
        asset = self.directory / "install-aiv.sh"
        target = self.refs / asset.name
        asset.rename(target)
        asset.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "allowed product assets"):
            validate_bundle(self.directory, SHA, "123.2")

    def test_reused_attempt_and_modified_deployment_inputs_fail_closed(self):
        self.assemble()
        with self.assertRaisesRegex(ValueError, "rerun all"):
            validate_bundle(self.directory, SHA, "123.3")
        path = self.directory / "release-images.vars.yaml"
        path.write_text(path.read_text().replace("b" * 64, "c" * 64))
        with self.assertRaisesRegex(ValueError, "deployment variables"):
            validate_bundle(self.directory, SHA, "123.2")

    def test_modified_archive_cannot_be_published(self):
        self.assemble()
        (self.directory / "aiv-linux-x64.tar.gz").write_bytes(b"replaced")
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            validate_bundle(self.directory, SHA, "123.2")


def frozen_fixture(directory, product_plan):
    from release_candidate import freeze, smoke_identity

    return freeze(directory, {backend: smoke_identity(directory, backend) for backend in DATABASES},
                  repository=PUBLIC_REPOSITORY, source_sha=product_plan["source_sha"],
                  build_id=product_plan["build_id"])


class DevPublicationFixture:
    """Stateful GitHub/registry boundary; publisher validation and readback stay real."""

    repository = "antgroup/ai-vision"

    def __init__(self, directory, product_plan):
        self.directory, self.plan = directory, product_plan
        self.files = {path.name: path.read_bytes() for path in directory.iterdir()}
        self.releases = {
            product_plan["release_tag"]: {"id": 100, "tag_name": product_plan["release_tag"],
                                          "draft": False},
            "dev": {"id": 200, "tag_name": "dev", "draft": False,
                    "body": self.notes(product_plan["release_tag"], "123.2", SHA)},
        }
        self.tags = {product_plan["release_tag"]: SHA, "dev": SHA}
        self.assets = {100: dict(self.files), 200: dict(self.files)}
        self.asset_ids = {}
        self.events = []
        self.fail_after_uploads = None
        self.after_upload = None
        self.after_download = None
        self.after_dev_download = None
        self.comparison_status = None
        self.workflow_conclusion = "success"

    def notes(self, version, build_id, source):
        return ("[Versioned assets and exact image manifest](https://github.com/"
                f"{self.repository}/releases/tag/{version}).\n\n"
                "## What's Changed\n* Original accepted change\n\n"
                f"<!-- aivision-build: {build_id} -->\n\n"
                "<details>\n<summary>Build details</summary>\n\n"
                f"Source: `{source}`  \nBuild: `{build_id}`  \nPublisher: `222.1`\n\n</details>")

    def api(self, path, *, data=None, missing=False, method="PATCH"):
        prefix = f"repos/{self.repository}"
        suffix = path.removeprefix(prefix)
        if data is not None:
            self.events.append(("write", path, copy.deepcopy(data)))
            if suffix == "/git/refs/tags/dev":
                self.tags["dev"] = data["sha"]
                return None
            if suffix == "/releases/generate-notes":
                return {"body": "## What's Changed\n* Newly accepted change"}
            raise AssertionError((path, data, method))
        if suffix == "":
            return {**LIVE_REPOSITORY, "default_branch": "main"}
        if suffix == "/commits/main":
            return {"sha": SHA}
        if suffix in {"/actions/runs/123", "/actions/runs/123/attempts/2"}:
            return {"id": 123, "run_attempt": 2, "head_sha": SHA, "head_branch": "main",
                    "path": ".github/workflows/release-images.yml", "event": "workflow_dispatch",
                    "repository": {"full_name": self.repository},
                    "head_repository": {"full_name": self.repository},
                    "status": "completed", "conclusion": self.workflow_conclusion}
        if suffix == "/actions/runs/123/attempts/2/jobs?per_page=100&page=1":
            names = ["Linux amd64 candidate deployment (oceanbase)",
                     "Linux amd64 candidate deployment (seekdb)", "freeze qualified candidate acceptance",
                     "verify public candidate indexes anonymously"]
            jobs = [{"name": name, "run_id": 123,
                    "run_attempt": 2, "head_sha": SHA, "status": "completed",
                    "conclusion": "success"} for name in names]
            if self.workflow_conclusion == "failure":
                jobs.append({"name": "publish verified development artifacts", "run_id": 123,
                    "run_attempt": 2, "head_sha": SHA, "status": "completed", "conclusion": "failure"})
            return {"total_count": len(jobs), "jobs": jobs}
        if suffix.startswith("/releases/tags/"):
            return copy.deepcopy(self.releases.get(suffix.removeprefix("/releases/tags/")))
        if suffix.startswith("/git/ref/tags/"):
            sha = self.tags.get(suffix.removeprefix("/git/ref/tags/"))
            return {"object": {"type": "commit", "sha": sha}} if sha else None
        if suffix.startswith("/compare/"):
            return {"status": self.comparison_status or ("identical" if self.tags["dev"] == SHA else "ahead")}
        if suffix.startswith("/releases/") and "/assets?" in suffix:
            release_id = int(suffix.split("/")[2])
            result = []
            for name, content in sorted(self.assets[release_id].items()):
                key = (release_id, name)
                asset_id = self.asset_ids.setdefault(key, len(self.asset_ids) + 1)
                result.append({"id": asset_id, "name": name, "size": len(content), "state": "uploaded"})
            return result
        raise AssertionError(path)

    def download(self, command, **kwargs):
        asset_id = int(command[2].rsplit("/", 1)[1])
        release_id, name = next(key for key, value in self.asset_ids.items() if value == asset_id)
        self.events.append(("download", release_id, name))
        kwargs["stdout"].write(self.assets[release_id][name])
        if self.after_download:
            self.after_download()
        if release_id == 200 and self.after_dev_download:
            self.after_dev_download()
        return subprocess.CompletedProcess(command, 0, stderr=b"")

    def run(self, *args, **kwargs):
        if args[:4] == ("docker", "buildx", "imagetools", "inspect"):
            return json.dumps({"digest": "sha256:" + "b" * 64})
        self.events.append(("write", args))
        if args[:3] == ("gh", "release", "upload"):
            self.assert_dev(args[3])
            for count, path in enumerate(args[4:args.index("--repo")], 1):
                self.assets[200][Path(path).name] = Path(path).read_bytes()
                if count == self.fail_after_uploads:
                    raise RuntimeError("fixture interrupted Dev asset upload")
            if self.after_upload:
                self.after_upload()
            return ""
        if args[:3] == ("gh", "release", "edit"):
            self.assert_dev(args[3])
            self.releases["dev"]["body"] = args[args.index("--notes") + 1]
            return ""
        if args[:4] == ("docker", "buildx", "imagetools", "create"):
            return ""
        raise AssertionError(args)

    @staticmethod
    def assert_dev(tag):
        if tag != "dev":
            raise AssertionError("immutable versioned Release must never be rewritten")


class DailyPublicationFixture(DevPublicationFixture):
    """Published Daily metadata and authenticated downloads, with external I/O only."""

    def __init__(self, directory, product_plan, previous):
        super().__init__(directory, product_plan)
        tag = previous.plan["release_tag"]
        self.releases.pop("dev")
        self.tags.pop("dev")
        self.releases["daily"] = {
            "id": 200, "tag_name": "daily", "draft": False,
            "body": self.notes(tag, previous.plan["build_id"], previous.plan["source_sha"]),
        }
        self.releases[tag] = {"id": 300, "tag_name": tag, "draft": False,
                              "published_at": "2026-11-17T00:00:00Z"}
        self.tags.update({"daily": previous.plan["source_sha"], tag: previous.plan["source_sha"]})
        self.assets[200] = {}
        self.assets[300] = {path.name: path.read_bytes() for path in previous.directory.iterdir()}

    def api(self, path, **kwargs):
        if kwargs.get("data") is not None and path.endswith("/git/refs/tags/daily"):
            self.events.append(("write", path, copy.deepcopy(kwargs["data"])))
            self.tags["daily"] = kwargs["data"]["sha"]
            return None
        if "/compare/" in path:
            return {"status": self.comparison_status or "ahead"}
        return super().api(path, **kwargs)

    def run(self, *args, **kwargs):
        if args[:4] == ("gh", "release", "edit", "daily"):
            self.events.append(("write", args))
            self.releases["daily"]["body"] = args[args.index("--notes") + 1]
            return ""
        return super().run(*args, **kwargs)


class DailyPublicationTests(unittest.TestCase):
    def bundle(self, **changes):
        bundle = AssetTests()
        bundle.setUp()
        self.addCleanup(bundle.doCleanups)
        arguments = dict(version="0.1.0", event="workflow_dispatch", tag="daily", date="20261117")
        arguments.update(changes)
        bundle.plan = plan(**arguments)
        (bundle.directory / "release-plan.json").write_text(json.dumps(bundle.plan))
        component_path = bundle.directory / "component-plan.json"
        components = json.loads(component_path.read_text())
        for name, component in components.items():
            component["identity"].update(product_version=bundle.plan["product_version"],
                component_version=bundle.plan["product_version"], target_channel="daily",
                source_sha=bundle.plan["source_sha"], build_id=bundle.plan["build_id"])
            (bundle.refs / f"{name}.build-info.json").write_text(json.dumps(component["identity"]))
        component_path.write_text(json.dumps(components))
        assemble(bundle.directory, bundle.refs, source_sha=bundle.plan["source_sha"],
                 build_id=bundle.plan["build_id"], repository=DevPublicationFixture.repository)
        return bundle

    def prepare(self, *, incoming=None, previous=None):
        self.bundle_incoming = self.bundle(**(incoming or {}))
        previous_args = dict(run_id="122", attempt="1", source_sha="c" * 40, daily_revision="2")
        previous_args.update(previous or {})
        self.bundle_previous = self.bundle(**previous_args)
        self.remote = DailyPublicationFixture(self.bundle_incoming.directory, self.bundle_incoming.plan,
                                              self.bundle_previous)
        self.acceptance = self.bundle_incoming.directory.parent / "candidate-acceptance.json"
        self.acceptance.write_text(json.dumps(frozen_fixture(self.bundle_incoming.directory, self.bundle_incoming.plan)))

    def publish(self):
        with (patch.object(sys, "argv", ["release_publish.py", "publish", "--directory",
                                        str(self.bundle_incoming.directory), "--acceptance", str(self.acceptance)]),
              patch.dict(os.environ, GITHUB_SHA=SHA, GITHUB_RUN_ID="123", GITHUB_RUN_ATTEMPT="2",
                         GITHUB_REPOSITORY=DevPublicationFixture.repository),
              patch("public_registry.verify_public_images"),
              patch("public_registry.verify_public_tag"),
              patch("release_publish.api", side_effect=self.remote.api),
              patch("release_publish.run", side_effect=self.remote.run),
              patch("release_publish.inspect_digest", return_value="sha256:" + "b" * 64),
              patch("release_publish.subprocess.run", side_effect=self.remote.download)):
            publish_main()

    def assert_no_publication_writes(self):
        self.assertFalse(any(event[0] == "write" for event in self.remote.events), self.remote.events)

    def update_previous_manifest(self, **changes):
        files = self.remote.assets[300]
        manifest = json.loads(files["release-manifest.json"])
        manifest.update(changes)
        files["release-manifest.json"] = json.dumps(manifest).encode()
        files["SHA256SUMS"] = "".join(
            f"{hashlib.sha256(content).hexdigest()}  {name}\n"
            for name, content in sorted(files.items()) if name != "SHA256SUMS"
        ).encode()

    def test_later_workflow_cannot_roll_back_daily_date_revision_or_base(self):
        for incoming, previous in (
            ({"snapshot_date": "20261116"}, {}),
            ({}, {}),
            ({"version": "0.0.9", "snapshot_date": "20261118"}, {}),
            ({"daily_revision": "2"}, {"daily_revision": "10"}),
            ({"version": "0.9.9"}, {"version": "0.10.0"}),
        ):
            with self.subTest(incoming=incoming, previous=previous):
                self.prepare(incoming=incoming, previous=previous)
                with self.assertRaisesRegex(ValueError, "newer.*Daily|Daily.*version"):
                    self.publish()
                self.assert_no_publication_writes()

    def test_numeric_daily_advances_publish_five_service_aliases(self):
        for incoming in ({"snapshot_date": "20261118"}, {"daily_revision": "3"},
                         {"daily_revision": "10"}, {"version": "0.2.0"}):
            with self.subTest(incoming=incoming):
                self.prepare(incoming=incoming)
                self.publish()
                writes = [event[1] for event in self.remote.events if event[0] == "write"
                          and isinstance(event[1], tuple)]
                aliases = [args[args.index("--tag") + 1] for args in writes
                           if args[:4] == ("docker", "buildx", "imagetools", "create")]
                self.assertEqual(set(aliases), {f"{package}:daily" for package in PACKAGES.values()})
                self.assertEqual(len(aliases), len(PACKAGES))
                self.assertIn(self.bundle_incoming.plan["release_tag"], self.remote.releases["daily"]["body"])

    def test_same_daily_version_does_not_allow_different_frozen_content(self):
        self.prepare(incoming={"daily_revision": "2"}, previous={"source_sha": SHA})
        with self.assertRaisesRegex(ValueError, "bytes|size"):
            self.publish()
        self.assert_no_publication_writes()

    def test_daily_display_title_does_not_replace_verified_version_order(self):
        self.prepare(incoming={"daily_revision": "3"})
        self.remote.releases["daily"]["name"] = "Daily: 99.99.99-daily.20991231.99"
        self.publish()
        self.assertIn(self.bundle_incoming.plan["release_tag"], self.remote.releases["daily"]["body"])

    def test_noncanonical_daily_pointer_versions_fail_closed(self):
        for version in ("0.1.0-daily.20261117.1", "0.1.0-daily.20260230", "0.01.0-daily.20261117",
                        "0.1.0-dev.20261117.2"):
            with self.subTest(version=version):
                self.prepare(incoming={"daily_revision": "3"})
                tag = self.bundle_previous.plan["release_tag"]
                self.remote.releases["daily"]["body"] = self.remote.releases["daily"]["body"].replace(tag, "v" + version)
                with self.assertRaises(ValueError):
                    self.publish()
                self.assert_no_publication_writes()

    def test_existing_daily_tag_without_release_fails_closed(self):
        self.prepare(incoming={"daily_revision": "3"})
        del self.remote.releases["daily"]
        with self.assertRaisesRegex(ValueError, "Daily.*provenance"):
            self.publish()
        self.assert_no_publication_writes()

    def test_daily_version_advance_preserves_build_and_source_order_guards(self):
        for previous in ({"run_id": "124"}, {"run_id": "123", "attempt": "2"}):
            with self.subTest(previous=previous):
                self.prepare(incoming={"daily_revision": "3"}, previous=previous)
                with self.assertRaisesRegex(ValueError, "newer rolling workflow attempt"):
                    self.publish()
                self.assert_no_publication_writes()
        for status in ("behind", "diverged"):
            with self.subTest(status=status):
                self.prepare(incoming={"daily_revision": "3"})
                self.remote.comparison_status = status
                with self.assertRaisesRegex(ValueError, "rolling commit"):
                    self.publish()
                self.assert_no_publication_writes()

    def test_daily_unknown_or_conflicting_published_provenance_stops_before_writes(self):
        for mutation in ("missing_release", "draft_release", "unpublished_release", "missing_manifest",
                         "duplicate_manifest", "manifest_checksum", "manifest_version", "manifest_channel",
                         "manifest_repository", "manifest_build", "manifest_null_source", "manifest_null_build",
                         "version_tag", "daily_tag"):
            with self.subTest(mutation=mutation):
                self.prepare(incoming={"daily_revision": "3"})
                tag = self.bundle_previous.plan["release_tag"]
                if mutation == "missing_release":
                    del self.remote.releases[tag]
                elif mutation == "draft_release":
                    self.remote.releases[tag]["draft"] = True
                elif mutation == "unpublished_release":
                    del self.remote.releases[tag]["published_at"]
                elif mutation == "missing_manifest":
                    del self.remote.assets[300]["release-manifest.json"]
                elif mutation == "duplicate_manifest":
                    original_api = self.remote.api
                    def duplicate(path, **kwargs):
                        result = original_api(path, **kwargs)
                        return result + [result[0]] if "/releases/300/assets?" in path else result
                    self.remote.api = duplicate
                elif mutation == "manifest_checksum":
                    self.remote.assets[300]["release-manifest.json"] += b" "
                elif mutation.startswith("manifest_"):
                    field, value = {"manifest_version": ("product_version", "0.1.0-daily.20261116"),
                        "manifest_channel": ("channel", "dev"),
                        "manifest_repository": ("source_repository", "another/repository"),
                        "manifest_build": ("build_id", "121.1"),
                        "manifest_null_source": ("source_sha", None),
                        "manifest_null_build": ("build_id", None)}[mutation]
                    self.update_previous_manifest(**{field: value})
                elif mutation == "version_tag":
                    self.remote.tags[tag] = "d" * 40
                elif mutation == "daily_tag":
                    self.remote.tags["daily"] = "d" * 40
                with self.assertRaises((ValueError, RuntimeError)):
                    self.publish()
                self.assert_no_publication_writes()


class DevRecoveryTests(unittest.TestCase):
    def bundle(self, product_plan):
        bundle = AssetTests()
        bundle.setUp()
        self.addCleanup(bundle.doCleanups)
        bundle.plan = product_plan
        (bundle.directory / "release-plan.json").write_text(json.dumps(bundle.plan))
        component_path = bundle.directory / "component-plan.json"
        components = json.loads(component_path.read_text())
        for name, component in components.items():
            component["identity"].update(product_version=bundle.plan["product_version"],
                component_version=bundle.plan["product_version"], target_channel="dev",
                source_sha=bundle.plan["source_sha"], build_id=bundle.plan["build_id"])
            (bundle.refs / f"{name}.build-info.json").write_text(json.dumps(component["identity"]))
        component_path.write_text(json.dumps(components))
        assemble(bundle.directory, bundle.refs, source_sha=bundle.plan["source_sha"],
                 build_id=bundle.plan["build_id"], repository=DevPublicationFixture.repository)
        return bundle

    def setUp(self):
        bundle = self.bundle(plan(event="workflow_dispatch", tag="dev"))
        self.directory = bundle.directory
        self.plan = bundle.plan
        self.acceptance = self.directory.parent / "candidate-acceptance.json"
        self.acceptance.write_text(json.dumps(frozen_fixture(self.directory, self.plan)))
        self.receipt = self.directory.parent / "publication-receipt.json"
        self.remote = DevPublicationFixture(self.directory, self.plan)

    def publish(self, *, publisher="555.1", acceptance=True, recover=False):
        run_id, attempt = publisher.split(".")
        arguments = ["release_publish.py", "publish", "--directory", str(self.directory),
                     "--receipt", str(self.receipt)]
        if acceptance:
            arguments += ["--candidate-acceptance", str(self.acceptance), "--candidate-run-id", "123",
                          "--candidate-run-attempt", "2"]
        else:
            arguments += ["--acceptance", str(self.acceptance)]
        if recover:
            arguments += ["--recover-dev-publication"]
        with (patch.object(sys, "argv", arguments),
              patch.dict(os.environ, GITHUB_SHA=SHA, GITHUB_RUN_ID=run_id, GITHUB_RUN_ATTEMPT=attempt,
                         GITHUB_REPOSITORY=DevPublicationFixture.repository),
              patch("public_registry.verify_public_images"),
              patch("public_registry.verify_public_tag"),
              patch("release_publish.api", side_effect=self.remote.api),
              patch("release_publish.run", side_effect=self.remote.run),
              patch("release_publish.inspect_digest", return_value="sha256:" + "b" * 64),
              patch("release_publish.subprocess.run", side_effect=self.remote.download)):
            publish_main()

    def test_same_frozen_candidate_recovers_pre_fix_mixed_dev_assets(self):
        original_notes = self.remote.releases["dev"]["body"]
        self.remote.assets[200]["install-aiv.sh"] = b"previous installer"
        self.remote.fail_after_uploads = 2
        with self.assertRaisesRegex(RuntimeError, "interrupted Dev"):
            self.publish(publisher="554.1")
        self.assertEqual(self.remote.releases["dev"]["body"], original_notes)
        self.remote.fail_after_uploads = None
        self.publish()
        self.assertEqual(self.remote.assets[200], self.remote.files)
        self.assertEqual(self.remote.assets[100], self.remote.files)
        self.assertEqual(self.remote.releases["dev"]["body"], original_notes)
        receipt = json.loads(self.receipt.read_text())
        self.assertEqual(receipt["candidate_build_id"], "123.2")
        self.assertEqual(receipt["publisher_build_id"], "555.1")
        self.assertEqual(receipt["recovery"]["mode"], "same_frozen_candidate")
        self.assertEqual(receipt["recovery"]["previous_body_sha256"],
                         hashlib.sha256(original_notes.encode()).hexdigest())
        self.assertEqual(receipt["candidate_acceptance_sha256"],
                         hashlib.sha256(self.acceptance.read_bytes()).hexdigest())
        expected = {name: {"size": len(content), "sha256": hashlib.sha256(content).hexdigest()}
                    for name, content in self.remote.files.items()}
        self.assertEqual(receipt["rolling_assets"]["assets"], expected)
        self.assertEqual(receipt["remote_assets"]["assets"], expected)
        self.assertTrue(any(event[0:2] == ("download", 100) for event in self.remote.events))
        self.assertTrue(any(event[0:2] == ("download", 200) for event in self.remote.events))
        self.publish(publisher="556.2")
        self.assertEqual(self.remote.releases["dev"]["body"], original_notes)

    def test_interrupted_upload_keeps_previous_notes_then_resumes_same_candidate(self):
        previous = self.remote.notes("v0.0.1-dev.20261018.6", "122.1", "c" * 40)
        self.remote.releases["dev"]["body"] = previous
        self.remote.tags["dev"] = "c" * 40
        self.remote.assets[200] = {name: b"previous:" + name.encode() for name in self.remote.files}
        self.remote.fail_after_uploads = 2
        with self.assertRaisesRegex(RuntimeError, "interrupted Dev"):
            self.publish()
        self.assertEqual(self.remote.releases["dev"]["body"], previous)
        self.assertEqual(self.remote.tags["dev"], SHA)
        self.assertTrue(any(content == self.remote.files[name] for name, content in self.remote.assets[200].items()))
        self.assertTrue(any(content != self.remote.files[name] for name, content in self.remote.assets[200].items()))
        self.remote.fail_after_uploads = None
        self.publish(publisher="777.2")
        self.assertEqual(self.remote.assets[200], self.remote.files)
        self.assertEqual(self.remote.assets[100], self.remote.files)
        self.assertIn("<!-- aivision-build: 123.2 -->", self.remote.releases["dev"]["body"])
        self.assertIn("Publisher: `777.2`", self.remote.releases["dev"]["body"])
        self.assertEqual(json.loads(self.receipt.read_text())["publisher_build_id"], "777.2")

    def test_interrupted_direct_publication_recovers_original_failed_dev_attempt(self):
        previous = self.remote.notes("v0.0.1-dev.20261018.6", "122.1", "c" * 40)
        self.remote.releases["dev"]["body"] = previous
        self.remote.tags["dev"] = "c" * 40
        self.remote.fail_after_uploads = 2
        with self.assertRaisesRegex(RuntimeError, "interrupted Dev"):
            self.publish(publisher="123.2", acceptance=False)
        self.remote.workflow_conclusion = "failure"
        self.remote.fail_after_uploads = None
        self.publish(recover=True)
        self.assertEqual(self.remote.assets[200], self.remote.files)
        self.assertEqual(self.remote.assets[100], self.remote.files)
        self.assertIn("<!-- aivision-build: 123.2 -->", self.remote.releases["dev"]["body"])
        receipt = json.loads(self.receipt.read_text())
        self.assertTrue(receipt["candidate_publication_recovery"])
        self.assertEqual(receipt["candidate_build_id"], "123.2")
        self.assertEqual(receipt["publisher_build_id"], "555.1")

    def test_failed_direct_publication_with_pre_fix_marker_recovers_frozen_bytes(self):
        self.remote.workflow_conclusion = "failure"
        self.remote.assets[200]["install-aiv.sh"] = b"previous installer"
        self.publish(recover=True)
        self.assertEqual(self.remote.assets[200], self.remote.files)
        receipt = json.loads(self.receipt.read_text())
        self.assertTrue(receipt["candidate_publication_recovery"])
        self.assertEqual(receipt["recovery"]["mode"], "same_frozen_candidate")

    def test_failed_direct_publication_requires_existing_dev_and_versioned_proof(self):
        self.remote.workflow_conclusion = "failure"
        for tag in ("dev", self.plan["release_tag"]):
            with self.subTest(tag=tag):
                previous = self.remote.releases.pop(tag)
                with self.assertRaisesRegex(ValueError, "requires"):
                    self.publish(recover=True)
                self.remote.releases[tag] = previous
                self.assertFalse(any(event[0] == "write" for event in self.remote.events))

    def test_equal_build_recovery_rejects_unknown_or_changed_provenance_before_writes(self):
        original = copy.deepcopy((self.remote.releases, self.remote.tags, self.remote.assets))
        mutations = [
            lambda: self.remote.releases["dev"].update(immutable=True),
            lambda: self.remote.releases["dev"].update(draft=True),
            lambda: self.remote.releases["dev"].pop("id"),
            lambda: self.remote.releases["dev"].update(body="unknown release"),
            lambda: self.remote.releases["dev"].update(body=self.remote.releases["dev"]["body"].replace("123.2", "124.1")),
            lambda: self.remote.releases["dev"].update(body=self.remote.releases["dev"]["body"].replace(SHA, "c" * 40)),
            lambda: self.remote.releases["dev"].update(body=self.remote.releases["dev"]["body"].replace(self.plan["release_tag"], "v0.0.1-dev.unknown")),
            lambda: self.remote.releases["dev"].update(body=self.remote.releases["dev"]["body"] + "\n<!-- aivision-build: 123.2 -->"),
            lambda: self.remote.releases["dev"].update(body=self.remote.releases["dev"]["body"] + "\nSource: unknown"),
            lambda: self.remote.tags.update(dev="c" * 40),
            lambda: self.remote.releases.pop(self.plan["release_tag"]),
            lambda: self.remote.releases[self.plan["release_tag"]].update(draft=True),
            lambda: self.remote.assets[100].update({"install-aiv.sh": b"changed"}),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                self.remote.releases, self.remote.tags, self.remote.assets = copy.deepcopy(original)
                self.remote.events.clear()
                mutate()
                with self.assertRaises(ValueError):
                    self.publish()
                self.assertFalse(any(event[0] == "write" for event in self.remote.events))

    def test_newer_or_diverged_rolling_commits_are_rejected_before_writes(self):
        self.remote.releases["dev"]["body"] = self.remote.notes("v0.0.1-dev.20261018.6", "122.1", "c" * 40)
        self.remote.tags["dev"] = "c" * 40
        for status in ("behind", "diverged"):
            with self.subTest(status=status):
                self.remote.events.clear()
                self.remote.comparison_status = status
                with self.assertRaisesRegex(ValueError, "newer or diverged"):
                    self.publish()
                self.assertFalse(any(event[0] == "write" for event in self.remote.events))

    def test_recovery_rechecks_dev_provenance_after_slow_immutable_readback(self):
        self.remote.after_download = lambda: self.remote.releases["dev"].update(
            body=self.remote.notes("v0.0.1-dev.20261020.8", "125.1", SHA))
        with self.assertRaisesRegex(ValueError, "provenance changed"):
            self.publish()
        self.assertFalse(any(event[0] == "write" for event in self.remote.events))

    def test_dev_readback_failure_keeps_previous_build_marker(self):
        previous = self.remote.notes("v0.0.1-dev.20261018.6", "122.1", "c" * 40)
        self.remote.releases["dev"]["body"] = previous
        self.remote.tags["dev"] = "c" * 40
        self.remote.after_upload = lambda: self.remote.assets[200].update({"install-aiv.sh": b"corrupt"})
        with self.assertRaisesRegex(ValueError, "remote release asset"):
            self.publish()
        self.assertEqual(self.remote.releases["dev"]["body"], previous)
        self.assertEqual(self.remote.assets[100], self.remote.files)
        self.assertFalse(self.receipt.exists())

    def test_equal_build_recovery_requires_unchanged_frozen_acceptance(self):
        acceptance = json.loads(self.acceptance.read_text())
        acceptance["assets"]["install-aiv.sh"]["sha256"] = "0" * 64
        self.acceptance.write_text(json.dumps(acceptance))
        with self.assertRaisesRegex(ValueError, "acceptance"):
            self.publish()
        self.assertFalse(any(event[0] == "write" for event in self.remote.events))
        with self.assertRaises(ValueError):
            self.publish(publisher="123.2", acceptance=False)
        self.assertFalse(any(event[0] == "write" for event in self.remote.events))

    def test_unexpected_dev_assets_block_publication_before_writers(self):
        self.remote.assets[200]["openshell-catalog.json"] = b"historical catalog"
        with self.assertRaisesRegex(ValueError, "unknown assets"):
            self.publish()
        self.assertFalse(any(event[0] == "write" for event in self.remote.events))

    def test_newer_dev_metadata_during_upload_blocks_recovery_without_catalog(self):
        newer_notes = self.remote.notes("v0.0.1-dev.20261020.8", "125.1", SHA)
        self.remote.after_upload = lambda: self.remote.releases["dev"].update(body=newer_notes)
        with self.assertRaisesRegex(ValueError, "provenance changed"):
            self.publish()
        self.assertEqual(self.remote.releases["dev"]["body"], newer_notes)
        self.assertFalse(self.receipt.exists())

    def test_newer_dev_metadata_during_readback_blocks_final_notes_edit(self):
        self.remote.releases["dev"]["body"] = self.remote.notes("v0.0.1-dev.20261018.6", "122.1", "c" * 40)
        self.remote.tags["dev"] = "c" * 40
        newer_notes = self.remote.notes("v0.0.1-dev.20261020.8", "125.1", SHA)
        self.remote.after_dev_download = lambda: self.remote.releases["dev"].update(body=newer_notes)
        with self.assertRaisesRegex(ValueError, "provenance changed"):
            self.publish()
        self.assertEqual(self.remote.releases["dev"]["body"], newer_notes)
        self.assertFalse(self.receipt.exists())


def require_publication_gates(jobs):
    def ancestors(name):
        pending, result = [name], set()
        while pending:
            needs = jobs[pending.pop()].get("needs", [])
            for dependency in [needs] if isinstance(needs, str) else needs:
                if dependency not in result:
                    result.add(dependency)
                    pending.append(dependency)
        return result

    publishers = [
        name
        for name, job in jobs.items()
        if job.get("permissions", {}).get("contents") == "write"
    ]
    if not publishers:
        raise ValueError("no publication job to validate")
    for name in ("plugins", "cli", "build", "public-data-backend"):
        if "prepare" not in ancestors(name):
            raise ValueError("artifact builds can start before the official release plan")
    if "public-registry" not in ancestors("artifact-smoke"):
        raise ValueError("artifact smoke can start before anonymous image verification")
    for name in publishers:
        if not {"public-source-checks", "public-registry", "artifact-smoke", "acceptance"} <= ancestors(name):
            raise ValueError("publication can bypass a source, registry, deployment or acceptance gate")
        if "always(" in jobs[name].get("if", "") or jobs[name].get("continue-on-error"):
            raise ValueError("publication can bypass a failed gate")
    stable = jobs["publish-stable"]
    if stable.get("environment") != "release" or "preflight" not in ancestors(
        "publish-stable"
    ):
        raise ValueError(
            "stable publication requires preflight and the protected environment"
        )
    if "release_publish.py preflight" not in str(jobs["preflight"].get("steps")):
        raise ValueError("preflight must verify live publication settings")
    if "release_publish.py publish" not in str(stable.get("steps")):
        raise ValueError("stable publication must repeat its live preflight")


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[2]
        cls.workflows = {
            name: yaml.safe_load((cls.root / ".github/workflows" / name).read_text())
            for name in ("release-images.yml", "release-cli.yml", "release-plugins.yml")
        }

    def test_publication_reaches_both_gates_and_cannot_skip_independent_review(self):
        jobs = self.workflows["release-images.yml"]["jobs"]
        require_publication_gates(jobs)
        for changed_job, mutation in (
            ("publish-development", {"needs": ["prepare", "assets"]}),
            ("publish-development", {"needs": ["prepare", "assets", "artifact-smoke"]}),
            ("artifact-smoke", {"needs": ["prepare", "assets"]}),
            ("plugins", {"needs": []}),
            ("build", {"needs": []}),
            ("publish-stable", {"environment": "unprotected"}),
            ("publish-development", {"if": "always()"}),
        ):
            changed = copy.deepcopy(jobs)
            changed[changed_job].update(mutation)
            with (
                self.subTest(changed_job=changed_job, mutation=mutation),
                self.assertRaises(ValueError),
            ):
                require_publication_gates(changed)
        self.assertEqual(jobs["prepare"]["if"], f"github.repository == '{PUBLIC_REPOSITORY}'")
        source_checks = jobs["public-source-checks"]
        self.assertEqual(source_checks["uses"], "./.github/workflows/ci.yml")
        self.assertIn("public-registry", source_checks["needs"])
        self.assertEqual(source_checks["with"]["profile"], "full")
        self.assertEqual(source_checks["with"]["public_platform_images"],
                         "${{ needs.public-registry.outputs.platform_images }}")
        smoke = jobs["artifact-smoke"]
        self.assertEqual(set(smoke["strategy"]["matrix"]["backend"]), set(DATABASES))
        self.assertIn("--require-pinned-images", str(smoke["steps"]))
        self.assertIn("--aiv", str(smoke["steps"]))
        self.assertIn("needs.public-registry.result == 'success'", smoke["if"])
        for name in ("preflight", "publish-stable", "publish-development"):
            with self.subTest(job=name):
                downloads = [step for step in jobs[name]["steps"]
                             if step.get("uses", "").startswith("actions/download-artifact@")
                             and step["with"].get("path") == "acceptance"]
                self.assertEqual(len(downloads), 1)
                self.assertNotIn("if", downloads[0])
                self.assertEqual(downloads[0]["with"]["name"], "release-acceptance-${{ github.run_attempt }}")
                self.assertIn("--acceptance acceptance/candidate-acceptance.json", str(jobs[name]["steps"]))
        for name in ("preflight", "publish-stable"):
            with self.subTest(tagged_job=name):
                self.assertIn("(github.event_name == 'push' || inputs.publish == true)", jobs[name]["if"])

    def test_manual_candidates_do_not_promote_by_default_and_upload_only_receipts(self):
        workflow = self.workflows["release-images.yml"]
        triggers = workflow.get("on", workflow.get(True))
        self.assertEqual(triggers["workflow_dispatch"]["inputs"]["publish"]["type"], "boolean")
        self.assertIs(triggers["workflow_dispatch"]["inputs"]["publish"]["default"], False)
        jobs = workflow["jobs"]
        self.assertIn("inputs.publish", jobs["publish-development"]["if"])
        self.assertIn("acceptance", jobs["publish-development"]["needs"])
        self.assertIn("acceptance", jobs["preflight"]["needs"])
        self.assertIn("artifact-smoke", jobs["acceptance"]["needs"])
        uploads = [step for step in jobs["artifact-smoke"]["steps"] if step.get("uses", "").startswith("actions/upload-artifact@")]
        self.assertEqual(len(uploads), 1)
        self.assertTrue(uploads[0]["with"]["path"].endswith(".json"))
        self.assertNotEqual(uploads[0].get("if"), "always()")
        promotion = yaml.safe_load((self.root / ".github/workflows/promote-release.yml").read_text())
        self.assertEqual(set(promotion.get("on", promotion.get(True))), {"workflow_dispatch"})
        text = str(promotion)
        self.assertIn("release_candidate.py verify", text)
        self.assertIn("--candidate-acceptance", text)
        self.assertNotIn("build-push-action", text)
        self.assertNotIn("GITHUB_RUN_ID:", text)
        self.assertNotIn("GITHUB_RUN_ATTEMPT:", text)

    def test_owned_artifacts_never_cross_workflow_attempts(self):
        for filename, workflow in self.workflows.items():
            for job in workflow["jobs"].values():
                for step in job.get("steps", []):
                    if step.get("uses", "").startswith(
                        ("actions/upload-artifact@", "actions/download-artifact@")
                    ):
                        artifact = step["with"].get(
                            "name", step["with"].get("pattern", "")
                        )
                        with self.subTest(filename=filename, artifact=artifact):
                            self.assertIn("github.run_attempt", artifact)


if __name__ == "__main__":
    unittest.main()
