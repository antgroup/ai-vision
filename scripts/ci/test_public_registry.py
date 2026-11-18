"""Check public image verification and copy scope without contacting a registry."""

import hashlib
from io import BytesIO
import json
import os
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlsplit

from product_registry import PUBLIC_REPOSITORY, UPSTREAM_SIGNOZ, product_image_repositories
import public_registry as registry


def payload(value):
    data = json.dumps(value, indent=1).encode()
    return data, "sha256:" + hashlib.sha256(data).hexdigest()


class Response(BytesIO):
    pass


class PublicRegistryTests(unittest.TestCase):
    def setUp(self):
        self.repositories = product_image_repositories(PUBLIC_REPOSITORY)
        self.package = self.repositories["signoz-ob"]
        self.objects = {}
        descriptors = []
        for arch in ("amd64", "arm64", "unknown"):
            system = "linux" if arch != "unknown" else "unknown"
            config, config_digest = payload(dict(os=system, architecture=arch))
            self.objects["blobs/" + config_digest] = config
            manifest, digest = payload(dict(
                schemaVersion=2,
                mediaType="application/vnd.oci.image.manifest.v1+json",
                config=dict(digest=config_digest, size=len(config)),
            ))
            self.objects["manifests/" + digest] = manifest
            descriptors.append(dict(
                digest=digest, size=len(manifest), platform=dict(os=system, architecture=arch),
            ))
        self.index, self.digest = payload(dict(
            schemaVersion=2, mediaType="application/vnd.oci.image.index.v1+json",
            manifests=descriptors,
        ))
        self.objects["manifests/" + self.digest] = self.index
        self.objects["manifests/candidate-42-1"] = self.index
        self.ref = self.package + "@" + self.digest
        self.images = {name: repository + "@" + self.digest
                       for name, repository in self.repositories.items()}

    def urlopen(self, request, *, timeout):
        url = urlsplit(request.full_url)
        self.assertEqual((url.scheme, url.netloc), ("https", "ghcr.io"))
        if url.path == "/token":
            self.assertIsNone(request.get_header("Authorization"))
            self.assertIsNone(request.get_header("Cookie"))
            return Response(b'{"token":"anonymous-test-token"}')
        self.assertEqual(request.get_header("Authorization"), "Bearer anonymous-test-token")
        key = url.path.split("/v2/antgroup/ai-vision/", 1)[1].split("/", 1)[1]
        return Response(self.objects[key])

    def test_exact_public_package_index_and_native_configs(self):
        with patch.object(registry, "urlopen", side_effect=self.urlopen) as fetch:
            registry.verify_public_images(self.images)
            registry.verify_public_tag(self.package + ":candidate-42-1", self.ref)
        urls = [entry.args[0].full_url for entry in fetch.call_args_list]
        for repository in self.repositories.values():
            path = repository.removeprefix("ghcr.io/")
            self.assertEqual(sum(url.startswith(f"https://ghcr.io/v2/{path}/blobs/") for url in urls), 2)

    def test_source_fixtures_use_current_owned_candidates_and_precision_base(self):
        database = "oceanbase/oceanbase-ce@" + self.digest
        release = {"source_repository": PUBLIC_REPOSITORY, "images": self.images,
                   "databases": {"oceanbase": database}}
        self.assertEqual(registry.source_fixtures(release), {
            "oceanbase": database, "signoz": registry.SOURCE_SIGNOZ_FIXTURE,
            "frontend": self.images["aivision-fe"], "backend": self.images["aivision-be"],
            "gateway": self.images["antcollector-gw"], "agentlens": self.images["agentlens"],
        })
        with self.assertRaisesRegex(ValueError, "five approved packages"):
            registry.source_fixtures({**release, "images": {
                **self.images, "aivision-fe": "ghcr.io/ob-labs/aivision-fe@" + self.digest}})

    def test_native_source_fixtures_use_the_verified_candidate_data_backend(self):
        release = {"source_repository": PUBLIC_REPOSITORY, "images": self.images,
                   "databases": {"oceanbase": "oceanbase/oceanbase-ce@" + self.digest},
                   "data_backend_mirror": {"upstream_ref": UPSTREAM_SIGNOZ + "@" + self.digest,
                                           "mirror_ref": self.ref}}
        self.assertEqual(registry.source_fixtures(release, use_data_backend=True),
                         {**registry.source_fixtures(release), "signoz": self.ref})
        with self.assertRaisesRegex(ValueError, "mirror provenance"):
            registry.source_fixtures({**release, "data_backend_mirror": None}, use_data_backend=True)
        with self.assertRaisesRegex(ValueError, "changed the upstream index digest"):
            registry.source_fixtures({**release, "data_backend_mirror": {
                "upstream_ref": UPSTREAM_SIGNOZ + "@sha256:" + "0" * 64,
                "mirror_ref": self.ref,
            }}, use_data_backend=True)

    def test_rejects_reencoded_index_and_altered_child(self):
        cases = [("manifests/" + self.digest, self.index),
                 next((key, data) for key, data in self.objects.items() if key.startswith("blobs/")),
                 next((key, data) for key, data in self.objects.items()
                      if key.startswith("manifests/sha256:") and key != "manifests/" + self.digest)]
        for key, data in cases:
            with self.subTest(key=key), patch.dict(self.objects, {key: data + b" "}):
                with patch.object(registry, "urlopen", side_effect=self.urlopen):
                    with self.assertRaisesRegex(ValueError, "digest"):
                        registry.verify_public_images(self.images)

    def test_rejects_package_scope_drift_without_network(self):
        invalid = {**self.images, "signoz-ob": UPSTREAM_SIGNOZ + "@" + self.digest}
        with patch.object(registry, "urlopen") as fetch:
            with self.assertRaises(ValueError):
                registry.verify_public_images(invalid)
            fetch.assert_not_called()

    def test_reports_every_anonymous_access_failure(self):
        denied = HTTPError("https://ghcr.io/token", 401, "Unauthorized", None, None)
        with patch.object(registry, "urlopen", side_effect=denied) as fetch:
            with self.assertRaises(ValueError) as failure:
                registry.verify_public_images(self.images)
        self.assertEqual(fetch.call_count, 5)
        for repository in self.repositories.values():
            self.assertIn(f"{repository}: HTTP Error 401", str(failure.exception))

    def test_mirror_limits_writes_to_the_exact_source_and_candidate_tag(self):
        upstream = UPSTREAM_SIGNOZ + "@" + self.digest
        with patch.dict(os.environ, {
            "GITHUB_REPOSITORY": PUBLIC_REPOSITORY, "GITHUB_RUN_ID": "42",
            "GITHUB_RUN_ATTEMPT": "1",
        }), patch.object(registry, "_regctl") as regctl:
            regctl.side_effect = lambda *args, **kwargs: (
                self.index if args[:2] == ("manifest", "get")
                else b"null" if args[:2] == ("artifact", "list") else b""
            )
            result = registry.mirror_data_backend(upstream)
            self.assertEqual(result, {"upstream_ref": upstream, "mirror_ref": self.ref})
            writes = [call.args for call in regctl.call_args_list if call.args[:2] == ("image", "copy")]
            self.assertEqual(writes, [
                ("image", "copy", "--force-recursive", "--referrers",
                 upstream, self.package + ":candidate-42-1"),
            ])
            for bad in (UPSTREAM_SIGNOZ + ":dev", self.ref):
                regctl.reset_mock()
                with self.assertRaises(ValueError):
                    registry.mirror_data_backend(bad)
                regctl.assert_not_called()

    def test_mirror_conflicting_candidate_fails_before_writing(self):
        upstream = UPSTREAM_SIGNOZ + "@" + self.digest
        with patch.dict(os.environ, {
            "GITHUB_REPOSITORY": PUBLIC_REPOSITORY, "GITHUB_RUN_ID": "42",
            "GITHUB_RUN_ATTEMPT": "1",
        }), patch.object(registry, "_regctl") as regctl:
            regctl.side_effect = lambda *args, **kwargs: (
                self.index if args[:2] == ("manifest", "get")
                else b"sha256:" + b"a" * 64
            )
            with self.assertRaisesRegex(ValueError, "conflicting candidate"):
                registry.mirror_data_backend(upstream)
            self.assertFalse(any(call.args[:2] == ("image", "copy")
                                 for call in regctl.call_args_list))


if __name__ == "__main__":
    unittest.main()
