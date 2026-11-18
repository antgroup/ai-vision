import copy
import unittest
from unittest.mock import patch

from openshell_catalog import assemble, native_child, validate


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.ref = "ghcr.io/ob-labs/aivision-sandbox-openshell@sha256:" + "a" * 64
        self.catalog = {
            "schema_version": 1,
            "product_version": "0.0.1",
            "provider_version": "0.1.6",
            "protocol_version": 1,
            "images": {
                "linux/arm64": {
                    key: self.ref
                    for key in ("plugin", "worker", "gateway", "runtime", "supervisor")
                }
            },
        }

    def test_mutable_missing_or_unknown_artifacts_are_rejected(self):
        for change in (
            lambda c: c["images"]["linux/arm64"].update(plugin="example/plugin:latest"),
            lambda c: c["images"]["linux/arm64"].pop("supervisor"),
            lambda c: c["images"].update({"darwin/arm64": {}}),
            lambda c: c.update(protocol_version=True),
            lambda c: c.update(command="arbitrary"),
        ):
            with self.subTest(change=change):
                catalog = copy.deepcopy(self.catalog)
                change(catalog)
                with self.assertRaises(ValueError):
                    validate(catalog)

    def test_catalog_must_match_product_release(self):
        with self.assertRaises(ValueError):
            validate(self.catalog, product_version="0.0.2")

    @patch("openshell_catalog.native_child")
    def test_only_selected_platform_is_resolved_and_worker_matches_plugin(
        self, resolve
    ):
        resolve.return_value = self.ref
        result = assemble({"linux/arm64": self.ref}, "0.0.1")
        self.assertEqual(result["images"]["linux/arm64"]["worker"], self.ref)
        self.assertEqual(resolve.call_count, 3)
        self.assertTrue(
            all(call.args[1] == "linux/arm64" for call in resolve.call_args_list)
        )

    @patch("openshell_catalog.native_child")
    def test_enhanced_worker_has_its_own_immutable_image(self, resolve):
        resolve.return_value = self.ref
        worker = "ghcr.io/ob-labs/aivision-rule-worker@sha256:" + "b" * 64
        result = assemble(
            {"linux/arm64": self.ref}, "0.0.1", worker_refs={"linux/arm64": worker}
        )
        self.assertEqual(result["images"]["linux/arm64"]["plugin"], self.ref)
        self.assertEqual(result["images"]["linux/arm64"]["worker"], worker)

    @patch("openshell_catalog.native_child")
    def test_worker_platforms_and_digests_must_match_selection(self, resolve):
        for workers in (
            {},
            {"linux/amd64": self.ref},
            {"linux/arm64": "example/worker:latest"},
        ):
            with self.subTest(workers=workers), self.assertRaises(ValueError):
                assemble({"linux/arm64": self.ref}, "0.0.1", worker_refs=workers)
        resolve.assert_not_called()

    @patch("openshell_catalog.subprocess.run")
    def test_native_resolution_rejects_duplicate_children(self, run):
        import json

        child = {
            "platform": {"os": "linux", "architecture": "arm64"},
            "digest": "sha256:" + "b" * 64,
        }
        run.return_value.stdout = json.dumps({"manifests": [child, child]})
        with self.assertRaises(ValueError):
            native_child("ghcr.io/nvidia/openshell/gateway:0.1.6", "linux/arm64")


if __name__ == "__main__":
    unittest.main()
