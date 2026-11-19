"""Keep the base candidate and Personal Edition publication boundaries distinct."""
import json
import unittest

import test_release_candidate as fixtures


class PersonalReleaseTests(unittest.TestCase):
    setUp = fixtures.CandidateTests.setUp
    assemble = fixtures.CandidateTests.assemble
    module = fixtures.CandidateTests.module
    smoke_summary = fixtures.CandidateTests.smoke_summary

    def test_base_receipt_cannot_qualify_personal_publication(self):
        self.plan["personal_edition"] = True
        (self.directory / "release-plan.json").write_text(json.dumps(self.plan))
        self.assemble()
        module = self.module()
        receipts = {backend: module.smoke_identity(self.directory, backend) for backend in module.BACKENDS}
        candidate = dict(repository=fixtures.REPOSITORY, source_sha=fixtures.SHA, build_id="123.2")
        base = module.freeze(self.directory, receipts, base_only=True, **candidate)
        self.assertEqual(base["phase"], "base")
        module.verify_frozen(self.directory, base, base_only=True, **candidate)
        with self.assertRaises(ValueError):
            module.verify_frozen(self.directory, base, **candidate)

    def test_final_receipt_requires_both_architectures_and_qualified_archive_bytes(self):
        from release_assets import PERSONAL_ARCHITECTURES, PERSONAL_ATTACHMENTS, personal_files, write_checksums
        from release_personal import expected_receipts

        self.plan["personal_edition"] = True
        (self.directory / "release-plan.json").write_text(json.dumps(self.plan))
        self.assemble()
        product = json.loads((self.directory / "release-manifest.json").read_text())
        module = self.module()
        smokes = {backend: module.smoke_identity(self.directory, backend) for backend in module.BACKENDS}
        source = {key: product[key] for key in ("source_repository", "source_sha", "release_tag", "build_id")}
        for name in PERSONAL_ATTACHMENTS:
            (self.directory / name).write_text("Public personal deployment helper\n")
        for arch in PERSONAL_ARCHITECTURES:
            name, archive, compact = personal_files(arch)
            for filename in (name, archive, compact):
                (self.directory / filename).write_text(filename)
        write_checksums(self.directory)
        initial = module.asset_inventory(self.directory, product)
        for arch in PERSONAL_ARCHITECTURES:
            name, archive, compact = personal_files(arch)
            cli = f"aiv-linux-{'x64' if arch == 'amd64' else 'arm64'}.tar.gz"
            meta = {
                "schema_version": 1, "profile": "all-in-one", "platform": f"linux/{arch}",
                "release_tag": source["release_tag"], "base_release": source["release_tag"],
                "source_candidate": source, "image_id": f"sha256:{'a' * 64}",
                "image_tag": f"aiv-all-in-one:fixture-{arch}", "tar_sha256": "b" * 64,
                "archive": archive, "archive_sha256": initial[archive]["sha256"],
                "archive_bytes": initial[archive]["size"], "zstd_archive": compact,
                "zstd_archive_sha256": initial[compact]["sha256"], "zstd_archive_bytes": initial[compact]["size"],
                "agent_cli": {"archive": cli, "version": product["product_version"],
                              "archive_sha256": initial[cli]["sha256"], "archive_bytes": initial[cli]["size"],
                              "binary_sha256": "c" * 64},
            }
            (self.directory / name).write_text(json.dumps(meta))
        write_checksums(self.directory)
        receipts = expected_receipts(self.directory, module.asset_inventory(self.directory, product), product)
        candidate = dict(repository=fixtures.REPOSITORY, source_sha=fixtures.SHA, build_id="123.2")
        qualified = module.freeze(self.directory, smokes, personal=receipts, **candidate)
        module.verify_frozen(self.directory, qualified, **candidate)
        with self.assertRaisesRegex(ValueError, "both exact native"):
            module.freeze(self.directory, smokes, personal={"amd64": receipts["amd64"]}, **candidate)
        (self.directory / personal_files("arm64")[2]).write_text("different bytes")
        write_checksums(self.directory)
        with self.assertRaisesRegex(ValueError, "candidate bytes or identity"):
            module.freeze(self.directory, smokes, personal=receipts, **candidate)
