"""Public product release provenance and publication boundary regressions."""

import base64
import copy
import json
import os
import sys
import unittest
from unittest.mock import patch

from product_registry import IMAGE_ROLES, PUBLIC_REPOSITORY, UPSTREAM_SIGNOZ, product_image_repositories
from release_assets import DATABASES, assemble
from release_candidate import freeze, sanitize_smoke, verify_remote_candidate
from release_plan import IMAGES as BUILT_COMPONENTS
from release_publish import main as publish, publish_version_tags, validate_bundle
from release_reuse import latest_release, prepare
import test_release as fixtures
import test_release_candidate as candidate_fixtures
import test_release_reuse as reuse_fixtures

SHA = fixtures.SHA
PACKAGES = product_image_repositories(PUBLIC_REPOSITORY)
IMAGES = {name: f"{package}@sha256:{index:064x}"
          for index, (name, package) in enumerate(PACKAGES.items(), 1)}
UPSTREAM_REF = UPSTREAM_SIGNOZ + "@" + IMAGES["signoz-ob"].split("@", 1)[1]
MIRROR = {"upstream_ref": UPSTREAM_REF, "mirror_ref": IMAGES["signoz-ob"]}


class PublicReleaseTests(unittest.TestCase):
    def setUp(self):
        fixtures.AssetTests.setUp(self)
        (self.directory / "signoz-image.txt").write_text(UPSTREAM_REF)
        self.mirror_path = self.directory / "data-backend-mirror.json"
        self.mirror_path.write_text(json.dumps(MIRROR))
        for name in BUILT_COMPONENTS:
            (self.refs / f"{name}.txt").write_text(IMAGES[name])

    def assemble(self):
        assemble(self.directory, self.refs, source_sha=SHA, build_id="123.2",
                 repository=PUBLIC_REPOSITORY)

    def smoke_summary(self, backend):
        refs = {role: IMAGES[name] for name, role in IMAGE_ROLES.items()}
        refs["database"] = DATABASES[backend]
        summary = candidate_fixtures.CandidateTests.smoke_summary(self, backend)
        for phase in ("initial_images", "recast_images"):
            summary[phase]["references"] = dict(refs)
        return summary

    def acceptance(self):
        self.assemble()
        receipts = {backend: sanitize_smoke(self.smoke_summary(backend), self.directory, backend)
                    for backend in DATABASES}
        return freeze(self.directory, receipts, repository=PUBLIC_REPOSITORY,
                      source_sha=SHA, build_id="123.2")

    def test_public_asset_references_and_mirror_provenance_are_checksum_covered(self):
        self.assemble()
        _, images = validate_bundle(self.directory, SHA, "123.2", repository=PUBLIC_REPOSITORY)
        self.assertEqual(images, IMAGES)
        manifest = json.loads((self.directory / "release-manifest.json").read_text())
        self.assertEqual(manifest["data_backend_mirror"], MIRROR)
        self.assertFalse(self.mirror_path.exists())
        self.assertFalse((self.directory / "signoz-image.txt").exists())
        for filename in ("release-images.vars.yaml", "release-seekdb.vars.yaml"):
            variables = dict(line.split(": ", 1) for line in
                             (self.directory / filename).read_text().splitlines()
                             if not line.startswith("#"))
            self.assertEqual(json.loads(variables["SIGNOZ_IMAGE"]), MIRROR["mirror_ref"])
            encoded = json.loads(variables["AIVISION_RELEASE_MANIFEST_BASE64"])
            self.assertEqual(json.loads(base64.b64decode(encoded)), manifest)

    def test_assembly_rejects_malformed_or_wrong_upstream_provenance(self):
        other_digest = "sha256:" + "f" * 64
        fake_refs = {name: value.split("@", 1)[0] + "@" + other_digest
                     for name, value in MIRROR.items()}
        for mirror in (None, {}, dict(MIRROR, extra="metadata"),
                       dict(MIRROR, upstream_ref=MIRROR["mirror_ref"]),
                       dict(MIRROR, mirror_ref=UPSTREAM_REF),
                       dict(MIRROR, upstream_ref=fake_refs["upstream_ref"]), fake_refs):
            with self.subTest(mirror=mirror):
                self.mirror_path.write_text(json.dumps(mirror))
                with self.assertRaisesRegex(ValueError, "(data.backend|upstream)"):
                    self.assemble()
                self.assertFalse((self.directory / "release-manifest.json").exists())

    def test_publish_validation_rejects_missing_provenance_and_legacy_package(self):
        self.assemble()
        path = self.directory / "release-manifest.json"
        original = json.loads(path.read_text())
        for mutation in ("mirror missing", "mirror differs", "legacy package"):
            with self.subTest(mutation=mutation):
                manifest = copy.deepcopy(original)
                if mutation == "mirror missing":
                    manifest.pop("data_backend_mirror")
                elif mutation == "mirror differs":
                    manifest["data_backend_mirror"]["mirror_ref"] = (
                        PACKAGES["signoz-ob"] + "@sha256:" + "f" * 64)
                else:
                    manifest["images"]["aivision-be"] = (
                        "ghcr.io/example/previous-backend" + "@sha256:" + "a" * 64)
                path.write_text(json.dumps(manifest))
                with self.assertRaisesRegex(ValueError, "(mirror|official component packages)"):
                    validate_bundle(self.directory, SHA, "123.2", repository=PUBLIC_REPOSITORY)

    def test_both_public_smokes_require_exact_database_and_service_refs_through_recast(self):
        self.assemble()
        for backend in DATABASES:
            expected = self.smoke_summary(backend)
            sanitize_smoke(expected, self.directory, backend)
            for phase in ("initial_images", "recast_images"):
                for component in expected[phase]["references"]:
                    summary = copy.deepcopy(expected)
                    summary[phase]["references"][component] = UPSTREAM_REF
                    with self.subTest(backend=backend, phase=phase, component=component):
                        with self.assertRaisesRegex(ValueError, "references differ"):
                            sanitize_smoke(summary, self.directory, backend)
            summary = copy.deepcopy(expected)
            summary["recast_images"]["platforms"]["data-backend"] = "linux/amd64 sha256:" + "f" * 64
            with self.assertRaisesRegex(ValueError, "identities changed during recast"):
                sanitize_smoke(summary, self.directory, backend)

    def test_public_remote_promotion_uses_exact_matching_repository_and_attempt(self):
        acceptance = self.acceptance()

        def api(path, anonymous_job=True, **kwargs):
            self.assertTrue(path.startswith(f"repos/{PUBLIC_REPOSITORY}"))
            value = candidate_fixtures.CandidateTests.api(self, path, **kwargs)
            if "/jobs?" in path and not anonymous_job:
                value["jobs"] = [job for job in value["jobs"]
                                 if job["name"] != "verify public candidate indexes anonymously"]
                value["total_count"] = len(value["jobs"])
            return value

        receipt = verify_remote_candidate(self.directory, acceptance, repository=PUBLIC_REPOSITORY,
            source_sha=SHA, candidate_run_id="123", candidate_run_attempt="2", api_fn=api)
        self.assertEqual(receipt["source_repository"], PUBLIC_REPOSITORY)
        with self.assertRaisesRegex(ValueError, "verify public candidate indexes anonymously"):
            verify_remote_candidate(self.directory, acceptance, repository=PUBLIC_REPOSITORY,
                source_sha=SHA, candidate_run_id="123", candidate_run_attempt="2",
                api_fn=lambda path: api(path, anonymous_job=False))
        with self.assertRaisesRegex(ValueError, "official repository"):
            verify_remote_candidate(self.directory, acceptance, repository="example/previous",
                source_sha=SHA, candidate_run_id="123", candidate_run_attempt="2", api_fn=api)

    def test_retired_repository_cannot_publish_or_query_a_reuse_baseline(self):
        retired = "ob-labs/ai-vision-ob"
        with (
            patch.object(sys, "argv", ["release_publish.py", "publish", "--directory", str(self.directory)]),
            patch.dict(os.environ, GITHUB_REPOSITORY=retired),
            patch("release_publish.api") as github,
            patch("release_publish.run") as writer,
            patch("release_reuse.run") as reuse_api,
        ):
            with self.assertRaisesRegex(ValueError, "official repository"):
                publish()
            with self.assertRaisesRegex(ValueError, "official repository"):
                latest_release(repository=retired)
            github.assert_not_called()
            writer.assert_not_called()
            reuse_api.assert_not_called()

    def test_public_inline_publisher_refuses_absent_or_wrong_attempt_acceptance_before_readback(self):
        acceptance = self.acceptance()
        path = self.refs / "candidate-acceptance.json"
        path.write_text(json.dumps(acceptance))
        for command in ("preflight", "publish"):
            for arguments, error in (([], "requires frozen candidate acceptance"),
                    (["--acceptance", str(path)], "source or attempt differs")):
                with (
                    self.subTest(command=command, arguments=arguments),
                    patch.object(sys, "argv", ["release_publish.py", command, "--directory", str(self.directory), *arguments]),
                    patch.dict(os.environ, GITHUB_SHA=SHA, GITHUB_RUN_ID="123", GITHUB_RUN_ATTEMPT="3",
                               GITHUB_REPOSITORY=PUBLIC_REPOSITORY),
                    patch("release_publish.api") as gh,
                    patch("release_publish.run") as external,
                    patch("public_registry.verify_public_images") as anonymous,
                ):
                    with self.assertRaisesRegex(ValueError, error):
                        publish()
                    gh.assert_not_called()
                    external.assert_not_called()
                    anonymous.assert_not_called()

    def test_public_version_conflict_in_data_backend_blocks_all_version_writes(self):
        existing = "sha256:" + "f" * 64
        with (
            patch("release_publish.inspect_digest", side_effect=lambda ref, **_: existing if "/data-backend:" in ref else None),
            patch("release_publish.run") as write,
        ):
            with self.assertRaisesRegex(ValueError, "conflicting release image: .*data-backend:"):
                publish_version_tags(self.plan, IMAGES, repository=PUBLIC_REPOSITORY)
            write.assert_not_called()

    def test_public_inline_publish_reads_back_five_versions_before_moving_five_aliases(self):
        acceptance = self.acceptance()
        path = self.refs / "candidate-acceptance.json"
        path.write_text(json.dumps(acceptance))
        events = []

        def api(ref, **_):
            if ref == f"repos/{PUBLIC_REPOSITORY}":
                return {"full_name": PUBLIC_REPOSITORY, "visibility": "public"}
            if ref.endswith("/generate-notes"):
                return {"body": "Changes"}
            return None

        with (
            patch.object(sys, "argv", ["release_publish.py", "publish", "--directory", str(self.directory),
                                       "--acceptance", str(path)]),
            patch.dict(os.environ, GITHUB_SHA=SHA, GITHUB_RUN_ID="123", GITHUB_RUN_ATTEMPT="2",
                       GITHUB_REPOSITORY=PUBLIC_REPOSITORY),
            patch("release_publish.api", side_effect=api),
            patch("release_publish.inspect_digest", return_value=None),
            patch("release_publish.verify_remote_assets", return_value={}),
            patch("release_publish.run", side_effect=lambda *args, **_: events.append(("write", args))),
            patch("public_registry.verify_public_images", side_effect=lambda images: events.append(("images", images))),
            patch("public_registry.verify_public_tag", side_effect=lambda ref, digest: events.append(("tag", (ref, digest)))),
        ):
            publish()
        self.assertEqual(events[0], ("images", IMAGES))
        first_alias = next(index for index, event in enumerate(events)
                           if event[0] == "write" and event[1][0] == "docker"
                           and event[1][-2].endswith(":daily"))
        versions = {(f"{package}:{self.plan['product_version']}", IMAGES[name])
                    for name, package in PACKAGES.items()}
        self.assertEqual({event[1] for event in events[:first_alias] if event[0] == "tag"}, versions)
        aliases = {(f"{package}:daily", IMAGES[name]) for name, package in PACKAGES.items()}
        self.assertEqual({event[1] for event in events[first_alias:] if event[0] == "tag"}, aliases)

    def test_public_inline_publish_requires_live_public_repositories_and_packages(self):
        acceptance = self.acceptance()
        path = self.refs / "candidate-acceptance.json"
        path.write_text(json.dumps(acceptance))
        for visibility, failure in (("public", "anonymous pull denied"),
                                    ("private", "official repository to be public")):
            with (
                self.subTest(visibility=visibility),
                patch.object(sys, "argv", ["release_publish.py", "publish", "--directory", str(self.directory),
                                           "--acceptance", str(path)]),
                patch.dict(os.environ, GITHUB_SHA=SHA, GITHUB_RUN_ID="123", GITHUB_RUN_ATTEMPT="2",
                           GITHUB_REPOSITORY=PUBLIC_REPOSITORY),
                patch("release_publish.api", return_value={"full_name": PUBLIC_REPOSITORY,
                                                           "visibility": visibility}) as gh,
                patch("release_publish.run") as write,
                patch("public_registry.verify_public_images",
                      side_effect=ValueError("anonymous pull denied")) as anonymous,
            ):
                with self.assertRaisesRegex(ValueError, failure):
                    publish()
                gh.assert_called_once_with(f"repos/{PUBLIC_REPOSITORY}")
                if visibility == "public":
                    anonymous.assert_called_once_with(IMAGES)
                else:
                    anonymous.assert_not_called()
                write.assert_not_called()


class PublicReuseTests(unittest.TestCase):
    setUp = reuse_fixtures.ReuseTests.setUp
    git = reuse_fixtures.ReuseTests.git
    commit = reuse_fixtures.ReuseTests.commit
    sha = reuse_fixtures.ReuseTests.sha
    write_plan = reuse_fixtures.ReuseTests.write_plan

    def test_public_reuse_reads_public_releases_and_keeps_legacy_build_ids(self):
        with patch("release_reuse.run", return_value="[]") as gh:
            self.assertEqual(latest_release(repository=PUBLIC_REPOSITORY), "")
            self.assertIn(f"repos/{PUBLIC_REPOSITORY}/releases?per_page=100", gh.call_args.args)
        with self.assertRaisesRegex(ValueError, "official repository"):
            prepare(self.root, self.directory, {**self.prior, "source_repository": "example/previous"},
                    repository=PUBLIC_REPOSITORY)
        prior = dict(self.prior, images=IMAGES)
        prepared = prepare(self.root, self.directory, prior, repository=PUBLIC_REPOSITORY)
        for name, data in prepared.items():
            self.assertEqual(data["identity"], self.prior["component_builds"][name])
            self.assertEqual(data["ref"], IMAGES[name])
