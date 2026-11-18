"""Catalog source selection uses controlled HTTP bodies and repository adapters."""
import hashlib
import json
import stat
import zipfile
from io import BytesIO, StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from django.core.management import call_command, CommandError
from django.test import TestCase
from rest_framework.test import APIClient

from apps.core.models import Space
from apps.core.services import model_pricing as pricing
from apps.core.tests.helpers import create_local_user, create_test_space_member


COMMIT = "b988aaf528f94307a4b81791220d8f2e9b820c8e"
PUBLIC = {"vendor": {"models": {"model": {"cost": {"input": "1", "output": "2"}}}}}
PROVENANCE = {"sourceType": "repository", "sourceUrl": f"https://github.com/anomalyco/models.dev/tree/{COMMIT}", "sourceRevision": COMMIT}


def response(body=b"archive", status=200):
    result = Mock()
    result.status_code = status
    result.headers = {}
    result.iter_content.return_value = [body]
    return result


def bundle_zip(body, *, member="api.json", extra=False):
    archive_bytes = BytesIO()
    with zipfile.ZipFile(archive_bytes, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(member, body, compress_type=zipfile.ZIP_DEFLATED)
        if extra:
            archive.writestr("extra.json", b"{}")
    return archive_bytes.getvalue()


class PricingSyncSourcesTests(TestCase):
    def test_repository_sync_uses_fixed_archive_and_stores_revision(self):
        with patch.object(pricing.http_requests, "get", return_value=response()) as get, patch(
            "apps.core.services.model_pricing_repository.catalog_from_archive", return_value=(PUBLIC, PROVENANCE)
        ) as load:
            pricing.sync_catalog(source="repository", revision=COMMIT)
        self.assertEqual(get.call_args.args[0], f"https://codeload.github.com/anomalyco/models.dev/tar.gz/{COMMIT}")
        self.assertFalse(get.call_args.kwargs["allow_redirects"])
        load.assert_called_once_with(b"archive", expected_revision=COMMIT)
        status = pricing.catalog_status()
        self.assertEqual((status["sourceType"], status["sourceRevision"]), ("repository", COMMIT))
        self.assertEqual(status["sourceUrl"], PROVENANCE["sourceUrl"])

    def test_auto_falls_back_from_api_403_to_repository(self):
        with patch.object(pricing.http_requests, "get", side_effect=[response(status=403), response()]) as get, patch(
            "apps.core.services.model_pricing_repository.catalog_from_archive", return_value=(PUBLIC, PROVENANCE)
        ):
            pricing.sync_catalog()
        self.assertEqual([call.args[0] for call in get.call_args_list], [pricing.CATALOG_URL, "https://codeload.github.com/anomalyco/models.dev/tar.gz/refs/heads/dev"])
        self.assertEqual(pricing.catalog_status()["sourceType"], "repository")
        self.assertEqual(pricing.catalog_status()["status"], "ready")

    def test_combined_failure_preserves_previous_snapshot_and_safe_error(self):
        old = pricing.store_catalog(PUBLIC)
        with patch.object(pricing.http_requests, "get", side_effect=[response(status=403), response(status=503)]):
            with self.assertRaises(pricing.PricingError):
                pricing.sync_catalog()
        status = pricing.catalog_status()
        self.assertEqual(status["snapshotId"], old.snapshot_id)
        self.assertEqual(status["status"], "error")
        self.assertNotIn("archive", status["lastError"])

    def test_initial_sync_downloads_once_and_leaves_existing_snapshot_untouched(self):
        with patch.object(pricing.http_requests, "get", return_value=response(json.dumps(PUBLIC).encode())) as get:
            call_command("sync_model_pricing", if_missing=True, stdout=StringIO())
            status = pricing.catalog_status()
            call_command("sync_model_pricing", if_missing=True, stdout=StringIO())
        get.assert_called_once()
        self.assertEqual(get.call_args.args[0], pricing.CATALOG_URL)
        self.assertEqual(status["status"], "ready")
        self.assertEqual(pricing.catalog_status(), status)

    def test_initial_sync_failure_can_be_retried(self):
        with patch.object(pricing.http_requests, "get", return_value=response(status=503)):
            with self.assertRaises(CommandError):
                call_command("sync_model_pricing", if_missing=True, stdout=StringIO())
        self.assertEqual(pricing.catalog_status()["status"], "error")
        with patch.object(pricing.http_requests, "get", return_value=response(json.dumps(PUBLIC).encode())):
            call_command("sync_model_pricing", if_missing=True, stdout=StringIO())
        self.assertEqual(pricing.catalog_status()["status"], "ready")

    def test_initial_sync_cannot_replace_concurrent_offline_import_or_record_stale_error(self):
        provenance = {"sourceType": "file", "sourceUrl": None, "sourceRevision": "sha256:" + "a" * 64}
        offline = {"vendor": {"models": {"offline": {"cost": {"input": "3", "output": "4"}}}}}
        for status in (200, 503):
            with self.subTest(download_status=status):
                pricing.ModelPricingCatalogState.objects.all().delete()

                def download(url, **kwargs):
                    if url == pricing.CATALOG_URL:
                        pricing.store_catalog(offline, provenance=provenance)
                    return response(json.dumps(PUBLIC).encode(), status=status)

                with patch.object(pricing.http_requests, "get", side_effect=download):
                    call_command("sync_model_pricing", if_missing=True, stdout=StringIO())
                current = pricing.catalog_status()
                self.assertEqual((current["status"], current["sourceType"]), ("ready", "file"))
                self.assertEqual(current["lastError"], "")
                self.assertEqual([row["model"] for row in pricing.search_catalog()], ["offline"])

    def test_bundled_startup_uses_local_catalog_without_network_and_keeps_existing_snapshot(self):
        body = json.dumps(PUBLIC).encode()
        with TemporaryDirectory() as directory:
            bundle = Path(directory) / "catalog.zip"
            bundle.write_bytes(bundle_zip(body))
            with patch.object(pricing, "BUNDLED_CATALOG_PATH", bundle, create=True), patch.object(pricing.http_requests, "get") as get:
                call_command("sync_model_pricing", bundled=True, if_missing=True, stdout=StringIO())
                bundled = pricing.catalog_status()
                self.assertEqual(bundled["sourceType"], "bundled")
                self.assertIsNone(bundled["sourceUrl"])
                self.assertEqual(bundled["sourceRevision"], "sha256:" + hashlib.sha256(body).hexdigest())
                self.assertEqual(bundled["modelCount"], 1)
                bundle.unlink()
                call_command("sync_model_pricing", bundled=True, if_missing=True, stdout=StringIO())
                self.assertEqual(pricing.catalog_status(), bundled)
            get.assert_not_called()

    def test_invalid_bundled_catalog_leaves_active_snapshot_and_never_fetches(self):
        old = pricing.store_catalog(PUBLIC)
        symlink = zipfile.ZipInfo("api.json")
        symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
        samples = {
            "compressed size": b"x" * 513,
            "decoded size": bundle_zip(b"x" * 513),
            "truncated": bundle_zip(b"{}")[:-2],
            "invalid JSON": bundle_zip(b"{"),
            "unexpected name": bundle_zip(b"{}", member="dir/api.json"),
            "non-regular file": bundle_zip(b"{}", member=symlink),
            "multiple members": bundle_zip(b"{}", extra=True),
        }
        with TemporaryDirectory() as directory:
            bundle = Path(directory) / "catalog.zip"
            with patch.object(pricing, "BUNDLED_CATALOG_PATH", bundle, create=True), patch.object(
                pricing, "MAX_CATALOG_BYTES", 512
            ), patch.object(pricing.http_requests, "get") as get:
                for scenario, body in samples.items():
                    with self.subTest(scenario=scenario):
                        bundle.write_bytes(body)
                        with self.assertRaises(CommandError):
                            call_command("sync_model_pricing", bundled=True, stdout=StringIO())
                        self.assertEqual(pricing.catalog_status()["snapshotId"], old.snapshot_id)
            get.assert_not_called()

    def test_invalid_source_or_revision_never_makes_request(self):
        for source, revision in (("http://private.invalid", None), ("repository", "../main"), ("api", COMMIT)):
            with self.subTest(source=source, revision=revision), patch.object(pricing.http_requests, "get") as get:
                with self.assertRaises(pricing.PricingError):
                    pricing.sync_catalog(source=source, revision=revision)
                get.assert_not_called()

    def test_same_data_different_repository_revision_has_distinct_provenance(self):
        first = pricing.store_catalog(PUBLIC, provenance=PROVENANCE)
        other = {**PROVENANCE, "sourceRevision": "a" * 40, "sourceUrl": "https://github.com/anomalyco/models.dev/tree/" + "a" * 40}
        second = pricing.store_catalog(PUBLIC, provenance=other)
        self.assertNotEqual(first.snapshot_id, second.snapshot_id)
        self.assertEqual(pricing.catalog_status()["sourceRevision"], "a" * 40)

    def test_local_repository_command_does_not_fetch_or_expose_path(self):
        provenance = {"sourceType": "local_repository", "sourceUrl": None, "sourceRevision": "sha256:" + "f" * 64}
        with TemporaryDirectory() as directory, patch.object(pricing.http_requests, "get") as get, patch(
            "apps.core.services.model_pricing_repository.catalog_from_directory", return_value=(PUBLIC, provenance)
        ) as load:
            call_command("sync_model_pricing", repository=directory, stdout=StringIO())
            load.assert_called_once_with(directory)
        get.assert_not_called()
        status = pricing.catalog_status()
        self.assertEqual(status["sourceType"], "local_repository")
        self.assertIsNone(status["sourceUrl"])

    def test_rich_unconditional_token_cache_and_reasoning_rates_are_preserved(self):
        payload = {"vendor": {"models": {"rich": {"cost": {"text": {
            "input": {"unit": "token", "price": "2", "cache": {"read": "0.2", "write": "3"}},
            "output": {"price": "8", "reasoning": "10"},
        }}}}}}
        row = pricing.normalize_catalog(payload)[0]
        self.assertTrue(row["supported"], row["unsupportedReasons"])
        self.assertEqual(row["rates"], {"input": "2", "output": "8", "cacheReadInput": "0.2", "cacheWriteInput": "3", "reasoning": "10"})
        for unsafe in ({"unit": "character"}, {"overrides": [{}]}, {"cache": {"write": {"5m": "3", "1h": "4"}}}):
            payload["vendor"]["models"]["rich"]["cost"]["text"]["input"].update(unsafe)
            self.assertFalse(pricing.normalize_catalog(payload)[0]["supported"])

    def test_repository_cache_precision_is_preserved_without_rounding(self):
        payload = {"vendor": {"models": {"precise": {"cost": {"text": {
            "input": {"price": "0.0245", "cache": {"read": "0.0024499999999999995"}},
            "output": {"price": "0.0978"},
        }}}}}}
        row = pricing.normalize_catalog(payload)[0]
        self.assertTrue(row['supported'])
        self.assertEqual(row['rates']['cacheReadInput'], '0.0024499999999999995')

    def test_mode_specific_prices_are_not_misrepresented_as_ordinary_rates(self):
        payload = {'vendor': {'models': {'mode-model': {
            'cost': {'text': {'input': {'price': '4.5'}, 'output': {'price': '22.5'}}},
            'experimental': {'modes': {'fast': {'cost': {'text': {
                'input': {'price': '30'}, 'output': {'price': '150'},
            }}}}},
        }}}}
        row = pricing.normalize_catalog(payload)[0]
        self.assertFalse(row['supported'])
        self.assertIn('unsupported_mode_pricing', row['unsupportedReasons'])

    def test_changed_normalization_cannot_reuse_old_catalog_rows(self):
        first = pricing.store_catalog(PUBLIC, provenance=PROVENANCE)
        normalized = pricing.normalize_catalog(PUBLIC)
        normalized[0].update(supported=False, unsupportedReasons=['unsupported_mode_pricing'])
        with patch.object(pricing, 'normalize_catalog', return_value=normalized):
            second = pricing.store_catalog(PUBLIC, provenance=PROVENANCE)
            stable = pricing.store_catalog(PUBLIC, provenance=PROVENANCE)
        self.assertNotEqual(first.snapshot_id, second.snapshot_id)
        self.assertEqual(second.snapshot_id, stable.snapshot_id)
        self.assertFalse(pricing.search_catalog(provider='vendor', model='model')[0]['supported'])

    def test_sync_api_accepts_explicit_repository_and_rejects_bad_options(self):
        user = create_local_user("repository-sync")
        space = Space.objects.create(name="Pricing", type=1, creator=user)
        create_test_space_member(space=space, user=user, role=1)
        client = APIClient()
        client.force_authenticate(user)
        url = f"/api/v1/spaces/{space.uuid}/model-pricing/sync"
        with patch.object(pricing, "sync_catalog") as sync:
            self.assertEqual(client.post(url, {"source": "repository"}, format="json").status_code, 200)
            sync.assert_called_once_with(source="repository", revision=None)
            self.assertEqual(client.post(url, {"source": "arbitrary"}, format="json").status_code, 400)
            self.assertEqual(client.post(url, {"url": "https://private.invalid"}, format="json").status_code, 400)
