"""Public build metadata remains bounded, local and independent of the database."""

import base64
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import Client, SimpleTestCase

UNKNOWN_BUILD = {
    "schema_version": 1,
    "component": "aivision-be",
    "product_version": None,
    "component_version": None,
    "source_sha": None,
    "build_id": None,
    "built_at": None,
    "target_channel": "unknown",
    "dirty": None,
}

VALID_BUILD = {
    "schema_version": 1,
    "component": "aivision-be",
    "product_version": "0.0.1-daily.20261016",
    "component_version": "0.1.0",
    "source_sha": "a" * 40,
    "build_id": "github-12345-2",
    "built_at": "2026-10-16T07:30:00Z",
    "target_channel": "daily",
    "dirty": False,
}


def release_manifest():
    """A newer release reuses four independently identified image builds."""
    components = {
        name: {**VALID_BUILD, "component": name, "source_sha": character * 40}
        for name, character in (
            ("aivision-be", "a"),
            ("aivision-fe", "b"),
            ("antcollector-gw", "c"),
            ("agentlens", "d"),
        )
    }
    return {
        "product_version": "0.1.0",
        "channel": "stable",
        "source_sha": "e" * 40,
        "build_id": "release-54321-1",
        "images": {
            name: f"ghcr.io/ob-labs/{name}@sha256:{'f' * 64}"
            for name in (*components, "signoz-ob")
        },
        "databases": {
            "oceanbase": f"oceanbase/oceanbase-ce@sha256:{'1' * 64}",
            "seekdb": f"oceanbase/seekdb@sha256:{'2' * 64}",
        },
        "component_builds": components,
    }


def encode_manifest(manifest):
    return base64.b64encode(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    ).decode()


def public_release_manifest():
    manifest = release_manifest()
    digest = "sha256:" + "f" * 64
    manifest["source_repository"] = "antgroup/ai-vision"
    manifest["images"] = {
        "aivision-fe": f"ghcr.io/antgroup/ai-vision/frontend@{digest}",
        "aivision-be": f"ghcr.io/antgroup/ai-vision/backend@{digest}",
        "antcollector-gw": f"ghcr.io/antgroup/ai-vision/ingest-gateway@{digest}",
        "agentlens": f"ghcr.io/antgroup/ai-vision/diagnosis@{digest}",
        "signoz-ob": f"ghcr.io/antgroup/ai-vision/data-backend@{digest}",
    }
    manifest["data_backend_mirror"] = {
        "upstream_ref": f"ghcr.io/ob-labs/signoz-ob@{digest}",
        "mirror_ref": manifest["images"]["signoz-ob"],
    }
    return manifest


def delivery_manifest():
    manifest = release_manifest()
    manifest.update(
        product_version="0.0.1-daily.20261028", channel="daily",
        source_repository="ob-labs/ai-vision-ob",
        delivery={"repository": "ob-labs/ai-vision-skills",
                  "release_tag": "delivery-v0.0.1-daily.20261028", "catalog_sha": "c" * 40},
        image_platforms=["linux/amd64"], cli_platforms=["linux-x64"],
    )
    manifest["images"] = {
        name: f"ghcr.io/ob-labs/ai-vision-skills/{name}@sha256:{'f' * 64}"
        for name in manifest["images"]
    }
    return manifest


class BuildInfoHTTPTests(SimpleTestCase):
    def test_public_get_reports_only_the_responding_instance(self):
        response = self.client.get("/api/v1/version")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertIn("no-store", response["Cache-Control"])
        payload = response.json()
        self.assertEqual(payload["code"], 0)
        self.assertEqual(payload["message"], "success")
        self.assertEqual(
            set(payload["data"]), {"backend", "observed_at", "scope", "expected"}
        )
        self.assertEqual(payload["data"]["scope"], "responding_instance")
        self.assertIsNone(payload["data"]["expected"])
        self.assertEqual(payload["data"]["backend"]["schema_version"], 1)
        self.assertEqual(payload["data"]["backend"]["component"], "aivision-be")
        observed = datetime.fromisoformat(payload["data"]["observed_at"])
        self.assertEqual(observed.utcoffset(), timedelta(0))
        self.assertLess(
            abs(datetime.now(observed.tzinfo) - observed), timedelta(seconds=5)
        )

    def test_other_methods_are_rejected_without_csrf_or_login(self):
        client = Client(enforce_csrf_checks=True)
        for method in ("HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
            with self.subTest(method=method):
                response = client.generic(method, "/api/v1/version")
                self.assertEqual(response.status_code, 405)
                self.assertEqual(response["Allow"], "GET")
                self.assertIn("no-store", response["Cache-Control"])


class BuildInfoFileTests(SimpleTestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.build_path = Path(directory.name) / "build-info.json"
        fixed_file = patch(
            "aivision_backend.build_info.BUILD_INFO_PATH", self.build_path
        )
        fixed_file.start()
        self.addCleanup(fixed_file.stop)

    def write_build(self, value):
        self.build_path.write_text(json.dumps(value), encoding="utf-8")

    def get_build(self, **kwargs):
        response = self.client.get("/api/v1/version", **kwargs)
        self.assertEqual(response.status_code, 200)
        return response.json()["data"]["backend"]

    def test_valid_file_returns_build_identity_without_adding_fields(self):
        self.write_build(VALID_BUILD)
        self.assertEqual(self.get_build(), VALID_BUILD)

    def test_missing_file_returns_unknown_identity(self):
        self.assertEqual(self.get_build(), UNKNOWN_BUILD)

    def test_unreadable_file_returns_unknown_without_exposing_path(self):
        self.build_path.mkdir()
        self.assertEqual(self.get_build(), UNKNOWN_BUILD)

    def test_malformed_json_and_encoding_return_unknown(self):
        for content in (b"{", b"\xff\xfe", b"[1, 2]", b"null", b'"secret"'):
            with self.subTest(content=content):
                self.build_path.write_bytes(content)
                self.assertEqual(self.get_build(), UNKNOWN_BUILD)

    def test_excessive_file_is_not_returned_or_parsed_as_build_metadata(self):
        self.write_build({**VALID_BUILD, "extra": "x" * (16 * 1024)})
        self.assertEqual(self.get_build(), UNKNOWN_BUILD)

    def test_wrong_schema_or_component_cannot_claim_this_backend_identity(self):
        for field, invalid in (
            ("schema_version", 2),
            ("schema_version", True),
            ("schema_version", "1"),
            ("schema_version", None),
            ("component", "aivision-fe"),
            ("component", None),
        ):
            with self.subTest(field=field, invalid=invalid):
                self.write_build({**VALID_BUILD, field: invalid})
                self.assertEqual(self.get_build(), UNKNOWN_BUILD)

    def test_sensitive_and_unknown_extra_fields_never_escape(self):
        self.write_build(
            {
                **VALID_BUILD,
                "credentials": {"token": "private-token"},
                "environment": {"DATABASE_PASSWORD": "private-password"},
                "path": "/private/build/workspace",
                "expected": {"product_version": "9.9.9"},
                "frontend": {"product_version": "9.9.9"},
            }
        )
        self.assertEqual(self.get_build(), VALID_BUILD)

    def test_invalid_sha_is_null_while_40_and_64_hex_are_preserved(self):
        for value, expected in (
            ("a" * 40, "a" * 40),
            ("B" * 64, "B" * 64),
            ("a" * 39, None),
            ("a" * 41, None),
            ("g" * 40, None),
            ("/private/key", None),
            (123, None),
            (True, None),
        ):
            with self.subTest(value=value):
                self.write_build({**VALID_BUILD, "source_sha": value})
                self.assertEqual(
                    self.get_build(), {**VALID_BUILD, "source_sha": expected}
                )

    def test_versions_reject_values_outside_the_product_version_grammar(self):
        for field in ("product_version", "component_version"):
            for value in (
                "latest",
                "dev",
                "00.0.1",
                "0.01.0",
                "1.0.01",
                "0.1.0-rc..1",
                "0.1.0-rc--1",
                "0.1.0-rc.",
                "0.1.0-rc-",
                "0.1.0-",
                "0.1.0+build.123",
                "0.1.0-rc.1+build.123",
                "v0.0.1",
                "1.0",
            ):
                with self.subTest(field=field, value=value):
                    self.write_build({**VALID_BUILD, field: value})
                    self.assertEqual(self.get_build(), {**VALID_BUILD, field: None})

    def test_versions_preserve_stable_and_segmented_prerelease_values(self):
        for field in ("product_version", "component_version"):
            for value in ("0.0.1", "1.0.0", "0.1.0-rc.1", "0.0.1-daily-build.20261016"):
                with self.subTest(field=field, value=value):
                    self.write_build({**VALID_BUILD, field: value})
                    self.assertEqual(self.get_build(), {**VALID_BUILD, field: value})

    def test_build_time_requires_a_real_timestamp_with_timezone(self):
        for value, expected in (
            ("2026-10-16T07:30:00Z", "2026-10-16T07:30:00Z"),
            ("2026-10-16T15:30:00+08:00", "2026-10-16T15:30:00+08:00"),
            ("2026-02-30T07:30:00Z", None),
            ("2026-10-16", None),
            ("2026-10-16T07:30:00", None),
            ("2026-10-16X07:30:00Z", None),
            ("2026-10-16T07:30:00+00:60", None),
            ("2026-10-16T07:30:00-01:99", None),
            ("2026-10-16T07:30:00+24:00", None),
            ("2026-10-16T07:30:00+99:00", None),
            (123, None),
        ):
            with self.subTest(value=value):
                self.write_build({**VALID_BUILD, "built_at": value})
                self.assertEqual(
                    self.get_build(), {**VALID_BUILD, "built_at": expected}
                )

    def test_invalid_scalar_types_and_unsafe_strings_are_not_reflected(self):
        for field, invalid, expected in (
            ("product_version", {"token": "secret"}, None),
            ("component_version", ["0.1.0"], None),
            ("product_version", "../private/version", None),
            ("component_version", "0.1.0\nsecret", None),
            ("product_version", "", None),
            ("build_id", "https://user:secret@example.test/build", None),
            ("build_id", "x" * 129, None),
            ("target_channel", "production", "unknown"),
            ("target_channel", ["daily"], "unknown"),
            ("dirty", "false", None),
            ("dirty", 0, None),
        ):
            with self.subTest(field=field, invalid=invalid):
                self.write_build({**VALID_BUILD, field: invalid})
                self.assertEqual(self.get_build(), {**VALID_BUILD, field: expected})

    def test_supported_channels_and_dirty_values_are_preserved(self):
        for channel in ("dev", "daily", "rc", "stable", "unknown"):
            for dirty in (True, False, None):
                with self.subTest(channel=channel, dirty=dirty):
                    value = {**VALID_BUILD, "target_channel": channel, "dirty": dirty}
                    self.write_build(value)
                    self.assertEqual(self.get_build(), value)

    def test_missing_optional_fields_stay_unknown(self):
        self.write_build({"schema_version": 1, "component": "aivision-be"})
        self.assertEqual(self.get_build(), UNKNOWN_BUILD)

    def test_environment_query_and_headers_cannot_override_fixed_file(self):
        self.write_build(VALID_BUILD)
        alternate = self.build_path.with_name("alternate.json")
        alternate.write_text(json.dumps({**VALID_BUILD, "product_version": "9.9.9"}))
        with patch.dict(
            os.environ,
            {
                "APP_VERSION": "9.9.9",
                "SOURCE_SHA": "b" * 40,
                "BUILD_INFO_PATH": str(alternate),
                "BUILD_INFO_URL": "http://127.0.0.1:1",
            },
        ):
            self.assertEqual(
                self.get_build(
                    data={"path": str(alternate), "url": "http://127.0.0.1:1"},
                    HTTP_X_BUILD_INFO_PATH=str(alternate),
                ),
                VALID_BUILD,
            )
            self.build_path.unlink()
            self.assertEqual(self.get_build(), UNKNOWN_BUILD)


class InstalledReleaseTests(SimpleTestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        build_path = Path(directory.name) / "build-info.json"
        build_path.write_text(json.dumps(VALID_BUILD))
        fixed_file = patch("aivision_backend.build_info.BUILD_INFO_PATH", build_path)
        fixed_file.start()
        self.addCleanup(fixed_file.stop)
        environment = patch.dict(os.environ, {"AIVISION_RELEASE_MANIFEST_BASE64": ""})
        environment.start()
        self.addCleanup(environment.stop)

    def get_expected(self, encoded):
        # Exercise the application limit even on Windows, where the OS limit is lower.
        with patch(
            "aivision_backend.build_info.os",
            environ={"AIVISION_RELEASE_MANIFEST_BASE64": encoded},
        ):
            payload = self.client.get("/api/v1/version").json()["data"]
        self.assertEqual(payload["backend"], VALID_BUILD)
        return payload["expected"]

    def test_installed_release_preserves_reused_component_identities(self):
        manifest = release_manifest()
        expected = {
            key: manifest[key]
            for key in ("product_version", "channel", "source_sha", "build_id")
        }
        expected["components"] = {
            name: manifest["component_builds"][name]
            for name in ("aivision-fe", "aivision-be")
        }
        self.assertEqual(self.get_expected(encode_manifest(manifest)), expected)
        self.assertNotEqual(expected["product_version"], VALID_BUILD["product_version"])

    def test_expected_record_does_not_override_a_mismatched_observed_backend(self):
        manifest = release_manifest()
        manifest["component_builds"]["aivision-be"]["source_sha"] = "9" * 40
        manifest["component_builds"]["aivision-fe"]["component_version"] = None
        expected = self.get_expected(encode_manifest(manifest))
        self.assertEqual(expected["components"]["aivision-be"]["source_sha"], "9" * 40)
        self.assertIsNone(expected["components"]["aivision-fe"]["component_version"])

    def test_public_release_preserves_component_identities(self):
        manifest = public_release_manifest()
        expected = self.get_expected(encode_manifest(manifest))
        self.assertIsNotNone(expected)
        self.assertEqual(expected["product_version"], manifest["product_version"])
        self.assertEqual(
            expected["components"],
            {
                name: manifest["component_builds"][name]
                for name in ("aivision-fe", "aivision-be")
            },
        )
        self.assertNotIn("data_backend_mirror", expected)

    def test_public_release_requires_matching_digest_provenance(self):
        for upstream, mirror in (
            (f"ghcr.io/ob-labs/signoz-ob@sha256:{'a' * 64}", None),
            (f"ghcr.io/other/signoz-ob@sha256:{'f' * 64}", None),
            (None, f"ghcr.io/antgroup/ai-vision/data-backend@sha256:{'a' * 64}"),
        ):
            with self.subTest(upstream=upstream, mirror=mirror):
                manifest = public_release_manifest()
                provenance = manifest["data_backend_mirror"]
                if upstream is not None:
                    provenance["upstream_ref"] = upstream
                if mirror is not None:
                    provenance["mirror_ref"] = mirror
                self.assertIsNone(self.get_expected(encode_manifest(manifest)))
        manifest = public_release_manifest()
        del manifest["data_backend_mirror"]
        self.assertIsNone(self.get_expected(encode_manifest(manifest)))

    def test_standard_release_repositories_cannot_be_mixed(self):
        for source, manifest in (
            ("ob-labs/ai-vision-ob", public_release_manifest()),
            ("antgroup/ai-vision", release_manifest()),
            ("another/product", release_manifest()),
        ):
            with self.subTest(source=source):
                manifest["source_repository"] = source
                self.assertIsNone(self.get_expected(encode_manifest(manifest)))

    def test_private_daily_delivery_preserves_observed_and_reused_identities(self):
        manifest = delivery_manifest()
        expected = self.get_expected(encode_manifest(manifest))
        self.assertIsNotNone(expected)
        self.assertEqual(expected["product_version"], manifest["product_version"])
        self.assertEqual(expected["components"]["aivision-be"], manifest["component_builds"]["aivision-be"])

    def test_private_daily_delivery_can_pin_both_known_database_mirrors(self):
        manifest = delivery_manifest()
        manifest["databases"] = {
            "oceanbase": f"ghcr.io/ob-labs/ai-vision-skills/oceanbase-ce@sha256:{'1' * 64}",
            "seekdb": f"ghcr.io/ob-labs/ai-vision-skills/seekdb@sha256:{'2' * 64}",
        }
        self.assertIsNotNone(self.get_expected(encode_manifest(manifest)))

    def test_private_delivery_scope_and_platforms_are_bounded(self):
        for field, value in (
            ("repository", "other/skills"), ("release_tag", "delivery-v9.0.0"),
            ("catalog_sha", "not-a-commit"),
        ):
            with self.subTest(field=field):
                manifest = delivery_manifest()
                manifest["delivery"][field] = value
                self.assertIsNone(self.get_expected(encode_manifest(manifest)))
        for field, value in (
            ("source_repository", "other/product"), ("channel", "stable"),
            ("image_platforms", []), ("image_platforms", ["linux/386"]),
            ("image_platforms", ["linux/amd64", "linux/amd64"]),
            ("cli_platforms", ["darwin-x64"]), ("cli_platforms", "linux-x64"),
        ):
            with self.subTest(field=field, value=value):
                manifest = delivery_manifest()
                manifest[field] = value
                self.assertIsNone(self.get_expected(encode_manifest(manifest)))
        for field, key, value in (
            ("images", "aivision-be", f"ghcr.io/other/aivision-be@sha256:{'f' * 64}"),
            ("databases", "seekdb", f"ghcr.io/ob-labs/ai-vision-skills/unreviewed@sha256:{'f' * 64}"),
        ):
            with self.subTest(field=field, key=key):
                manifest = delivery_manifest()
                manifest[field][key] = value
                self.assertIsNone(self.get_expected(encode_manifest(manifest)))

    def test_missing_and_invalid_manifest_do_not_change_observed_backend(self):
        for encoded in (
            "", "not-base64", "e30=\n", "e30", "bnVsbA==", "W10=", "x" * 90000,
        ):
            with self.subTest(encoded=encoded[:20]):
                self.assertIsNone(self.get_expected(encoded))

    def test_manifest_must_identify_all_known_images_with_pinned_digests(self):
        for field, name, invalid in (
            ("images", "aivision-be", "ghcr.io/ob-labs/aivision-be:0.1.0"),
            ("images", "aivision-fe", f"ghcr.io/ob-labs/aivision-be@sha256:{'f' * 64}"),
            ("images", "signoz-ob", f"evil.example/signoz-ob@sha256:{'f' * 64}"),
            ("databases", "oceanbase", f"oceanbase/seekdb@sha256:{'f' * 64}"),
            ("databases", "seekdb", "oceanbase/seekdb:latest"),
        ):
            with self.subTest(field=field, name=name):
                manifest = release_manifest()
                manifest[field][name] = invalid
                self.assertIsNone(self.get_expected(encode_manifest(manifest)))
        for field, name in (
            ("images", "agentlens"),
            ("databases", "seekdb"),
            ("component_builds", "antcollector-gw"),
        ):
            with self.subTest(field=field, missing=name):
                manifest = release_manifest()
                del manifest[field][name]
                self.assertIsNone(self.get_expected(encode_manifest(manifest)))
        for field in ("images", "databases", "component_builds"):
            manifest = release_manifest()
            manifest[field]["unknown"] = "unexpected"
            self.assertIsNone(self.get_expected(encode_manifest(manifest)))

    def test_incomplete_dirty_or_misidentified_component_builds_are_rejected(self):
        for field, invalid in (
            ("schema_version", True),
            ("component", "aivision-be"),
            ("source_sha", "f" * 39),
            ("product_version", "latest"),
            ("component_version", "0.1"),
            ("build_id", None),
            ("built_at", "2026-02-30T00:00:00Z"),
            ("target_channel", "unknown"),
            ("dirty", True),
        ):
            with self.subTest(field=field):
                manifest = release_manifest()
                manifest["component_builds"]["aivision-fe"][field] = invalid
                self.assertIsNone(self.get_expected(encode_manifest(manifest)))
        manifest = release_manifest()
        del manifest["component_builds"]["aivision-fe"]["component_version"]
        self.assertIsNone(self.get_expected(encode_manifest(manifest)))

    def test_release_identity_must_be_complete_and_public(self):
        for field, invalid in (
            ("product_version", "latest"),
            ("channel", "unknown"),
            ("channel", ["stable"]),
            ("source_sha", "secret"),
            ("build_id", "https://user:secret@example.test/build"),
        ):
            with self.subTest(field=field):
                manifest = release_manifest()
                manifest[field] = invalid
                self.assertIsNone(self.get_expected(encode_manifest(manifest)))

    def test_only_allowlisted_fields_are_public(self):
        manifest = release_manifest()
        manifest["credentials"] = {"token": "secret-do-not-reflect"}
        manifest["component_builds"]["aivision-fe"]["environment"] = {
            "PASSWORD": "secret"
        }
        expected = self.get_expected(encode_manifest(manifest))
        self.assertIsNotNone(expected)
        self.assertEqual(
            set(expected),
            {"product_version", "channel", "source_sha", "build_id", "components"},
        )
        self.assertEqual(set(expected["components"]), {"aivision-fe", "aivision-be"})
        self.assertEqual(set(expected["components"]["aivision-fe"]), set(VALID_BUILD))
        self.assertNotIn("secret", json.dumps(expected))

    def test_duplicate_keys_and_excessive_json_are_rejected(self):
        manifest = release_manifest()
        encoded = json.dumps(manifest)
        duplicate = '{"product_version":"9.9.9",' + encoded[1:]
        for raw in (
            duplicate, json.dumps({**manifest, "extra": "x" * 65536}), "[" * 2000,
        ):
            self.assertIsNone(self.get_expected(base64.b64encode(raw.encode()).decode()))

    def test_runtime_headers_and_queries_cannot_supply_installed_release(self):
        encoded = encode_manifest(release_manifest())
        payload = self.client.get(
            "/api/v1/version",
            {"AIVISION_RELEASE_MANIFEST_BASE64": encoded},
            HTTP_AIVISION_RELEASE_MANIFEST_BASE64=encoded,
        ).json()["data"]
        self.assertIsNone(payload["expected"])
