"""Release checks degrade normally and never expose deployment identity upstream."""

import http.client
import io
import json
import os
import time
import urllib.error
from datetime import datetime, timedelta
from email.message import Message
from unittest.mock import patch
from urllib.response import addinfourl

from django.core.cache import cache
from django.test import Client, SimpleTestCase

from .test_build_info import delivery_manifest, encode_manifest


PUBLISHED_RELEASE = {
    "tag_name": "v0.1.0",
    "name": "AI Vision 0.1.0",
    "body": "## Changes\n\n- Platform improvements\n- AgentLens fixes",
    "html_url": "https://github.com/antgroup/ai-vision/releases/tag/v0.1.0",
    "published_at": "2026-10-31T08:00:00Z",
    "draft": False,
    "prerelease": False,
    "author": {"login": "release-maintainer"},
    "assets": [],
}


class ReleaseHTTPTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        environment = patch.dict(os.environ, {"AIVISION_UPDATE_CHECK_ENABLED": "true"})
        environment.start()
        self.addCleanup(environment.stop)
        self.open_patch = patch("urllib.request.OpenerDirector.open", autospec=True)
        self.open = self.open_patch.start()
        self.addCleanup(self.open_patch.stop)
        self.reply(PUBLISHED_RELEASE)

    def reply(self, value=None, raw=None, status=200):
        response = io.BytesIO(raw if raw is not None else json.dumps(value).encode())
        response.status = status
        self.open.return_value = response
        self.open.side_effect = None

    def get_data(self, query=""):
        response = self.client.get(f"/api/v1/releases{query}")
        self.assertEqual(response.status_code, 200)
        return response.json()["data"]

    def test_public_get_returns_published_release_and_allowlisted_fields(self):
        response = Client(enforce_csrf_checks=True).get("/api/v1/releases")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertIn("no-store", response["Cache-Control"])
        payload = response.json()
        self.assertEqual(payload["code"], 0)
        self.assertEqual(payload["message"], "success")
        self.assertEqual(set(payload["data"]), {"status", "latest", "checked_at"})
        self.assertEqual(payload["data"]["status"], "available")
        self.assertEqual(
            payload["data"]["latest"],
            {
                "version": "0.1.0",
                "tag_name": "v0.1.0",
                "name": "AI Vision 0.1.0",
                "body": "## Changes\n\n- Platform improvements\n- AgentLens fixes",
                "url": "https://github.com/antgroup/ai-vision/releases/tag/v0.1.0",
                "published_at": "2026-10-31T08:00:00Z",
            },
        )
        checked = datetime.fromisoformat(payload["data"]["checked_at"])
        self.assertEqual(checked.utcoffset(), timedelta(0))

    def test_only_get_is_allowed_and_other_methods_do_not_check_upstream(self):
        client = Client(enforce_csrf_checks=True)
        for method in ("HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
            with self.subTest(method=method):
                response = client.generic(method, "/api/v1/releases")
                self.assertEqual(response.status_code, 405)
                self.assertEqual(response["Allow"], "GET")
        self.open.assert_not_called()

    def test_request_has_fixed_destination_timeout_and_no_deployment_identity(self):
        with patch.dict(os.environ, {"GITHUB_TOKEN": "private-token"}):
            data = self.get_data(
                "?url=https://example.org&token=private-token&version=9.9.9"
            )
        self.assertEqual(data["status"], "available")
        request = self.open.call_args.args[1]
        self.assertEqual(
            request.full_url,
            "https://api.github.com/repos/antgroup/ai-vision/releases/latest",
        )
        self.assertEqual(request.get_method(), "GET")
        self.assertIsNone(request.data)
        self.assertEqual(
            {key.lower() for key, value in request.header_items()},
            {"accept", "user-agent", "x-github-api-version"},
        )
        self.assertNotIn("private-token", repr(request.header_items()))
        self.assertGreater(self.open.call_args.kwargs["timeout"], 0)
        self.assertLessEqual(self.open.call_args.kwargs["timeout"], 5)

    def test_new_repository_check_ignores_the_old_repository_cache(self):
        cache.set("aivision:latest-release:v1", {"status": "no_release"}, 300)
        self.assertEqual(self.get_data()["status"], "available")

    def test_historical_installation_uses_public_update_check(self):
        installed = delivery_manifest()
        with patch.dict(
            os.environ,
            {"AIVISION_RELEASE_MANIFEST_BASE64": encode_manifest(installed)},
        ):
            version = self.client.get("/api/v1/version").json()["data"]["expected"]
            self.assertEqual(version["source_sha"], installed["source_sha"])
            self.open.assert_not_called()
            release = self.get_data()["latest"]

        self.open.assert_called_once()
        self.assertEqual(
            self.open.call_args.args[1].full_url,
            "https://api.github.com/repos/antgroup/ai-vision/releases/latest",
        )
        self.assertEqual(
            release["url"], "https://github.com/antgroup/ai-vision/releases/tag/v0.1.0"
        )

    def test_missing_or_private_release_is_a_normal_no_release_result(self):
        self.open.side_effect = urllib.error.HTTPError(
            "https://api.github.com/", 404, "Not Found", {}, None
        )
        data = self.get_data()
        self.assertEqual(data["status"], "no_release")
        self.assertIsNone(data["latest"])
        self.assertIsNotNone(data["checked_at"])

    def test_failures_do_not_claim_latest_or_expose_error_details(self):
        for error in (
            TimeoutError("private host details"),
            http.client.IncompleteRead(b"partial json"),
            urllib.error.URLError("private host details"),
            urllib.error.HTTPError(
                "https://api.github.com/", 403, "rate limit", {}, None
            ),
            urllib.error.HTTPError(
                "https://api.github.com/", 500, "upstream", {}, None
            ),
        ):
            with self.subTest(error=error):
                cache.clear()
                self.open.side_effect = error
                data = self.get_data()
                self.assertEqual(data["status"], "unavailable")
                self.assertIsNone(data["latest"])
                self.assertIsNotNone(data["checked_at"])
                self.assertNotIn("private", json.dumps(data))

    def test_disabled_check_bypasses_network_and_cached_release(self):
        self.assertEqual(self.get_data()["status"], "available")
        self.open.reset_mock()
        for disabled in ("false", "False", "0", "off", "no"):
            with (
                self.subTest(disabled=disabled),
                patch.dict(os.environ, {"AIVISION_UPDATE_CHECK_ENABLED": disabled}),
            ):
                self.assertEqual(
                    self.get_data(),
                    {"status": "disabled", "latest": None, "checked_at": None},
                )
        self.open.assert_not_called()

    def test_default_check_is_enabled(self):
        with patch.dict(os.environ):
            os.environ.pop("AIVISION_UPDATE_CHECK_ENABLED")
            self.assertEqual(self.get_data()["status"], "available")

    def test_success_and_404_are_cached_for_five_minutes(self):
        now = time.time()
        for upstream_missing in (False, True):
            with self.subTest(upstream_missing=upstream_missing):
                cache.clear()
                self.open.reset_mock()
                self.reply(PUBLISHED_RELEASE)
                if upstream_missing:
                    self.open.side_effect = urllib.error.HTTPError(
                        "https://api.github.com/", 404, "Not Found", {}, None
                    )
                with patch("time.time", return_value=now):
                    first = self.get_data()
                with patch("time.time", return_value=now + 299):
                    self.assertEqual(self.get_data(), first)
                self.assertEqual(self.open.call_count, 1)
                self.reply({**PUBLISHED_RELEASE, "body": "Fresh notes"})
                with patch("time.time", return_value=now + 301):
                    refreshed = self.get_data()
                self.assertEqual(refreshed["latest"]["body"], "Fresh notes")
                self.assertEqual(self.open.call_count, 2)

    def test_failed_check_is_retried_after_one_minute(self):
        now = time.time()
        self.open.side_effect = TimeoutError("offline")
        with patch("time.time", return_value=now):
            self.assertEqual(self.get_data()["status"], "unavailable")
        self.reply(PUBLISHED_RELEASE)
        with patch("time.time", return_value=now + 59):
            self.assertEqual(self.get_data()["status"], "unavailable")
        with patch("time.time", return_value=now + 61):
            self.assertEqual(self.get_data()["status"], "available")
        self.assertEqual(self.open.call_count, 2)

    def test_drafts_prereleases_invalid_tags_and_incomplete_release_are_unavailable(
        self,
    ):
        for changes in (
            {"draft": True},
            {"prerelease": True},
            {"tag_name": "v0.1.0-rc.1"},
            {"tag_name": "daily"},
            {"tag_name": "v00.1.0"},
            {"tag_name": "v1.0"},
            {"tag_name": "v1.0.0+build"},
            {"published_at": None},
            {"published_at": "2026-02-30T08:00:00Z"},
            {"published_at": "2026-10-31T08:00:00"},
            {"published_at": "2026-10-31T08:00:00+00:60"},
            {"body": []},
        ):
            with self.subTest(changes=changes):
                cache.clear()
                self.reply({**PUBLISHED_RELEASE, **changes})
                data = self.get_data()
                self.assertEqual(data["status"], "unavailable")
                self.assertIsNone(data["latest"])

    def test_optional_title_and_notes_can_be_empty(self):
        self.reply({**PUBLISHED_RELEASE, "name": None, "body": None})
        latest = self.get_data()["latest"]
        self.assertEqual(latest["name"], "v0.1.0")
        self.assertEqual(latest["body"], "")

    def test_plain_numeric_release_tag_is_supported(self):
        self.reply({**PUBLISHED_RELEASE, "tag_name": "1.2.3"})
        latest = self.get_data()["latest"]
        self.assertEqual(latest["version"], "1.2.3")
        self.assertEqual(
            latest["url"], "https://github.com/antgroup/ai-vision/releases/tag/1.2.3"
        )

    def test_canonical_release_url_and_allowlist_ignore_external_or_sensitive_fields(
        self,
    ):
        self.reply(
            {
                **PUBLISHED_RELEASE,
                "html_url": "https://example.org/evil",
                "token": "secret-value",
                "author": {"private_email": "secret@example.org"},
                "assets": [{"browser_download_url": "https://example.org/evil"}],
            }
        )
        data = self.get_data()
        self.assertEqual(
            data["latest"]["url"],
            "https://github.com/antgroup/ai-vision/releases/tag/v0.1.0",
        )
        self.assertNotIn("secret", json.dumps(data))
        self.assertNotIn("example.org", json.dumps(data))

    def test_invalid_or_oversized_upstream_response_is_unavailable(self):
        for raw in (
            b"{",
            b"\xff\xfe",
            b"[]",
            b"null",
            b"x" * (256 * 1024 + 1),
            json.dumps({**PUBLISHED_RELEASE, "body": "x" * (64 * 1024 + 1)}).encode(),
        ):
            with self.subTest(length=len(raw)):
                cache.clear()
                self.reply(raw=raw)
                self.assertEqual(self.get_data()["status"], "unavailable")

    def test_redirect_is_not_followed_even_to_another_github_host(self):
        self.open_patch.stop()
        headers = Message()
        headers["Location"] = "https://example.org/redirect-target"
        response = addinfourl(
            io.BytesIO(b""), headers, "https://api.github.com/", code=302
        )
        response.msg = "Found"
        with patch(
            "urllib.request.AbstractHTTPHandler.do_open", return_value=response
        ) as transport:
            self.assertEqual(self.get_data()["status"], "unavailable")
        self.assertEqual(transport.call_count, 1)
