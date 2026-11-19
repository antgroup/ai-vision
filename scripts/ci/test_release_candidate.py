"""Frozen candidate acceptance and authenticated promotion provenance regressions."""
import copy
import importlib
import importlib.util
import json
from pathlib import Path
import unittest

import test_release as fixtures

SHA = fixtures.SHA

REPOSITORY = fixtures.PUBLIC_REPOSITORY


class CandidateTests(unittest.TestCase):
    setUp = fixtures.AssetTests.setUp
    assemble = fixtures.AssetTests.assemble

    def module(self):
        self.assertIsNotNone(importlib.util.find_spec("release_candidate"), "candidate acceptance helper is missing")
        return importlib.import_module("release_candidate")

    def smoke_summary(self, backend):
        from product_registry import IMAGE_ROLES

        references = {role: fixtures.PUBLIC_IMAGES[component] for component, role in IMAGE_ROLES.items()}
        references["database"] = fixtures.DATABASES[backend]
        images = {"pinned_content_verified": True, "references": references, "platforms": {
            component: f"linux/amd64 sha256:{index:064x}"
            for index, component in enumerate(("database", "data-backend", "ingest-gateway",
                                                "backend", "diagnosis", "frontend"), 1)}}
        return {"status": "passed", "backend": backend,
                "initial_images": copy.deepcopy(images), "recast_images": copy.deepcopy(images),
                "runtime_log": "PRIVATE", "config": {"password": "PRIVATE"}}

    def candidate(self):
        module = self.module()
        self.assemble()
        receipts = {}
        for backend in ("oceanbase", "seekdb"):
            summary = self.smoke_summary(backend)
            receipts[backend] = module.sanitize_smoke(summary, self.directory, backend)
        acceptance = module.freeze(self.directory, receipts, repository=REPOSITORY, source_sha=SHA, build_id="123.2")
        return module, receipts, acceptance

    def api(self, path, **kwargs):
        run = {"id": 123, "run_attempt": 2, "head_sha": SHA, "head_branch": "main",
               "path": ".github/workflows/release-images.yml", "event": "workflow_dispatch",
               "repository": {"full_name": REPOSITORY}, "head_repository": {"full_name": REPOSITORY},
               "status": "completed", "conclusion": "success"}
        if path == "repos/" + REPOSITORY:
            return {**fixtures.LIVE_REPOSITORY, "default_branch": "main"}
        if "/commits/" in path:
            return {"sha": SHA}
        if "/jobs?" in path:
            return {"total_count": 4, "jobs": [dict(id=i, run_id=123, run_attempt=2,
                    head_sha=SHA, status="completed", conclusion="success", name=name)
                    for i, name in enumerate(("Linux amd64 candidate deployment (oceanbase)",
                    "Linux amd64 candidate deployment (seekdb)", "freeze qualified candidate acceptance",
                    "verify public candidate indexes anonymously"), 1)]}
        return run

    def test_freeze_binds_all_asset_bytes_and_strips_runtime_data(self):
        module, receipts, acceptance = self.candidate()
        self.assertEqual(acceptance["build_id"], "123.2")
        self.assertEqual(set(acceptance["assets"]), {p.name for p in self.directory.iterdir()})
        self.assertNotIn("PRIVATE", json.dumps(acceptance))
        module.verify_frozen(self.directory, acceptance, repository=REPOSITORY, source_sha=SHA, build_id="123.2")
        (self.directory / "aiv-linux-x64.tar.gz").write_bytes(b"mutated")
        with self.assertRaises(ValueError):
            module.verify_frozen(self.directory, acceptance, repository=REPOSITORY, source_sha=SHA, build_id="123.2")

    def test_windows_candidate_requires_exact_qualification_and_official_job(self):
        from release_assets import assemble
        from test_windows_release_cli import pe_header
        from windows_release_cli import ASSET, PLATFORMS, pack, receipt_identity

        binary = self.directory.parent / "aiv.exe"
        license_path = self.directory.parent / "LICENSE"
        binary.write_bytes(pe_header(0x8664))
        license_path.write_text("attribution\n")
        archive = self.directory / ASSET
        pack(binary, license_path, archive)
        assemble(self.directory, self.refs, repository=REPOSITORY, source_sha=SHA,
                 build_id="123.2", require_windows=True)
        manifest = json.loads((self.directory / "release-manifest.json").read_text())
        self.assertEqual(manifest["cli_platforms"], PLATFORMS)
        module = self.module()
        receipts = {backend: module.sanitize_smoke(self.smoke_summary(backend), self.directory, backend)
                    for backend in module.BACKENDS}
        build = dict(source_sha=SHA, build_id="123.2", product_version=self.plan["product_version"])
        proof = receipt_identity(archive, **build)
        options = dict(repository=REPOSITORY, source_sha=SHA, build_id="123.2")
        for wrong in (None, dict(proof, build_id="123.1"),
                      dict(proof, checks=dict(proof["checks"], install=1))):
            with self.subTest(receipt=wrong), self.assertRaisesRegex(ValueError, "Windows x64 installation"):
                module.freeze(self.directory, receipts, windows_cli=wrong, **options)
        acceptance = module.freeze(self.directory, receipts, windows_cli=proof, **options)
        self.assertEqual(acceptance["windows_cli"], proof)
        module.verify_frozen(self.directory, acceptance, **options)

        def api(path, include_windows):
            value = self.api(path)
            if include_windows and "/jobs?" in path:
                value["jobs"].append(dict(value["jobs"][0], id=5, name="cli / qualify Windows x64"))
                value["total_count"] += 1
            return value

        for included in [False, True]:
            def verify():
                return module.verify_remote_candidate(self.directory, acceptance,
                    repository=REPOSITORY, source_sha=SHA, candidate_run_id="123",
                    candidate_run_attempt="2", api_fn=lambda path: api(path, included))
            if included:
                self.assertEqual(verify()["candidate_build_id"], "123.2")
            else:
                with self.assertRaisesRegex(ValueError, "qualify Windows x64"):
                    verify()

    def test_partial_or_mismatched_smokes_never_freeze_acceptance(self):
        module, receipts, acceptance = self.candidate()
        for mutation in ({"seekdb": None}, {"seekdb": dict(receipts["seekdb"], build_id="123.1")},
                         {"seekdb": dict(receipts["seekdb"], status="failed")}):
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                module.freeze(self.directory, receipts | mutation, repository=REPOSITORY, source_sha=SHA, build_id="123.2")
        for summary in ({"status": "failed"}, {"status": "passed", "backend": "seekdb"}):
            with self.assertRaises(ValueError):
                module.sanitize_smoke(summary, self.directory, "seekdb")

    def test_smoke_receipts_reject_platform_drift_in_any_component_or_cast(self):
        module, _, _ = self.candidate()
        for backend in ("oceanbase", "seekdb"):
            for phase in ("initial_images", "recast_images"):
                for component in self.smoke_summary(backend)[phase]["platforms"]:
                    summary = self.smoke_summary(backend)
                    summary[phase]["platforms"][component] = "linux/arm64 sha256:" + "a" * 64
                    with self.subTest(backend=backend, phase=phase, component=component):
                        with self.assertRaisesRegex(ValueError, "platform"):
                            module.sanitize_smoke(summary, self.directory, backend)

    def test_smoke_receipts_require_complete_well_formed_platform_evidence(self):
        module, _, _ = self.candidate()
        valid = self.smoke_summary("seekdb")["initial_images"]["platforms"]
        invalid = [None, [], {}, {key: value for key, value in valid.items() if key != "database"},
                   dict(valid, extra=valid["backend"]), dict(valid, backend=None),
                   dict(valid, backend="linux/amd64"), dict(valid, backend="linux/amd64 not-an-image-id"),
                   dict(valid, backend="linux/amd64 sha256:" + "a" * 64 + " extra")]
        for phase in ("initial_images", "recast_images"):
            for platforms in invalid:
                summary = self.smoke_summary("seekdb")
                summary[phase]["platforms"] = platforms
                with self.subTest(phase=phase, platforms=platforms):
                    with self.assertRaisesRegex(ValueError, "platform"):
                        module.sanitize_smoke(summary, self.directory, "seekdb")
            summary = self.smoke_summary("seekdb")
            del summary[phase]["platforms"]
            with self.subTest(phase=phase, platforms="missing"):
                with self.assertRaisesRegex(ValueError, "platform"):
                    module.sanitize_smoke(summary, self.directory, "seekdb")

    def test_exact_successful_official_default_branch_attempt_is_accepted(self):
        module, _, acceptance = self.candidate()
        receipt = module.verify_remote_candidate(self.directory, acceptance, repository=REPOSITORY,
             source_sha=SHA, candidate_run_id="123", candidate_run_attempt="2", api_fn=self.api)
        self.assertEqual(receipt["candidate_build_id"], "123.2")

    def test_failed_dev_publisher_can_recover_only_after_exact_successful_qualification(self):
        module = self.module()
        # Reassemble a Dev candidate so a failed Daily cannot enter this exception.
        self.plan = fixtures.plan(event="workflow_dispatch", tag="dev")
        (self.directory / "release-plan.json").write_text(json.dumps(self.plan))
        components = json.loads((self.directory / "component-plan.json").read_text())
        for name, value in components.items():
            value["identity"].update(product_version=self.plan["product_version"],
                component_version=self.plan["product_version"], target_channel="dev")
            (self.refs / f"{name}.build-info.json").write_text(json.dumps(value["identity"]))
        (self.directory / "component-plan.json").write_text(json.dumps(components))
        module, _, acceptance = self.candidate()

        def api(path, **kwargs):
            value = self.api(path, **kwargs)
            if "/jobs?" in path:
                publisher = dict(id=99, run_id=123, run_attempt=2, head_sha=SHA, status="completed",
                    conclusion="failure", name="publish verified development artifacts")
                return {"total_count": len(value["jobs"]) + 1, "jobs": [*value["jobs"], publisher]}
            if "/actions/runs/" in path:
                return value | {"conclusion": "failure"}
            return value

        receipt = module.verify_remote_candidate(self.directory, acceptance, repository=REPOSITORY,
            source_sha=SHA, candidate_run_id="123", candidate_run_attempt="2", api_fn=api,
            allow_dev_recovery=True)
        self.assertTrue(receipt["publication_recovery"])
        with self.assertRaises(ValueError):
            module.verify_remote_candidate(self.directory, acceptance, repository=REPOSITORY,
                source_sha=SHA, candidate_run_id="123", candidate_run_attempt="2", api_fn=api)

        for mutation in ({"head_sha": "c" * 40}, {"run_attempt": 3}, {"conclusion": "cancelled"},
                         {"head_repository": {"full_name": "attacker/fork"}}):
            def changed(path, **kwargs):
                value = api(path, **kwargs)
                return value | mutation if "/actions/runs/" in path and "/jobs?" not in path else value
            with self.subTest(run=mutation), self.assertRaises(ValueError):
                module.verify_remote_candidate(self.directory, acceptance, repository=REPOSITORY,
                    source_sha=SHA, candidate_run_id="123", candidate_run_attempt="2", api_fn=changed,
                    allow_dev_recovery=True)

        for name, conclusion in (("Linux amd64 candidate deployment (oceanbase)", "failure"),
                ("Linux amd64 candidate deployment (seekdb)", "cancelled"),
                ("freeze qualified candidate acceptance", "failure"),
                ("publish verified development artifacts", "cancelled"),
                ("source checks", "failure"), ("source checks", "cancelled"),
                ("build images", "failure"), ("build images", "cancelled")):
            def changed(path, **kwargs):
                value = copy.deepcopy(api(path, **kwargs))
                if "/jobs?" in path:
                    found = False
                    for job in value["jobs"]:
                        if job["name"] == name:
                            job["conclusion"], found = conclusion, True
                    if not found:
                        value["jobs"].append(dict(name=name, conclusion=conclusion, run_id=123,
                            run_attempt=2, head_sha=SHA, status="completed"))
                    value["total_count"] = len(value["jobs"])
                return value
            with self.subTest(job=name, conclusion=conclusion), self.assertRaises(ValueError):
                module.verify_remote_candidate(self.directory, acceptance, repository=REPOSITORY,
                    source_sha=SHA, candidate_run_id="123", candidate_run_attempt="2", api_fn=changed,
                    allow_dev_recovery=True)
        acceptance["assets"]["install-aiv.sh"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "acceptance"):
            module.verify_remote_candidate(self.directory, acceptance, repository=REPOSITORY,
                source_sha=SHA, candidate_run_id="123", candidate_run_attempt="2", api_fn=api,
                allow_dev_recovery=True)

    def test_failed_daily_cannot_use_the_dev_publication_recovery_exception(self):
        module, _, acceptance = self.candidate()
        def api(path, **kwargs):
            value = self.api(path, **kwargs)
            if "/jobs?" in path:
                return {"total_count": 4, "jobs": [*value["jobs"], dict(name="publish verified development artifacts",
                    run_id=123, run_attempt=2, head_sha=SHA, status="completed", conclusion="failure")]}
            return value | {"conclusion": "failure"} if "/actions/runs/" in path else value
        with self.assertRaises(ValueError):
            module.verify_remote_candidate(self.directory, acceptance, repository=REPOSITORY,
                source_sha=SHA, candidate_run_id="123", candidate_run_attempt="2", api_fn=api,
                allow_dev_recovery=True)

    def test_standard_candidate_rejects_optional_release_manifest(self):
        module, receipts, _ = self.candidate()
        manifest = self.directory / "release-manifest.json"
        manifest.write_text(json.dumps({**json.loads(manifest.read_text()), "plugins": {"openshell": {}}}))
        with self.assertRaisesRegex(ValueError, "optional plugin catalogs"):
            module.freeze(self.directory, receipts, repository=REPOSITORY, source_sha=SHA, build_id="123.2")

    def test_foreign_stale_failed_wrong_workflow_and_superseded_attempt_fail_closed(self):
        module, _, acceptance = self.candidate()
        mutations = [{"repository": {"full_name": "attacker/fork"}}, {"head_repository": {"full_name": "attacker/fork"}},
                     {"head_branch": "feature"}, {"head_sha": "c" * 40}, {"run_attempt": 3},
                     {"conclusion": "failure"}, {"status": "in_progress"}, {"event": "pull_request"},
                     {"path": ".github/workflows/other.yml"}]
        for mutation in mutations:
            def api(path, **kwargs):
                value = self.api(path, **kwargs)
                return value | mutation if "/actions/runs/" in path and "/jobs?" not in path else value
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                module.verify_remote_candidate(self.directory, acceptance, repository=REPOSITORY,
                    source_sha=SHA, candidate_run_id="123", candidate_run_attempt="2", api_fn=api)
        for api in (lambda path, **kw: {"sha": "c" * 40} if "/commits/" in path else self.api(path, **kw),
                    lambda path, **kw: {"total_count": 0, "jobs": []} if "/jobs?" in path else self.api(path, **kw)):
            with self.assertRaises(ValueError):
                module.verify_remote_candidate(self.directory, acceptance, repository=REPOSITORY,
                    source_sha=SHA, candidate_run_id="123", candidate_run_attempt="2", api_fn=api)

    def test_stable_rc_and_trial_alias_cannot_use_manual_promotion(self):
        module, _, acceptance = self.candidate()
        for changes in ({"channel": "stable"}, {"channel": "rc"}, {"alias": "trial-123"}):
            data = json.loads((self.directory / "release-plan.json").read_text()) | changes
            (self.directory / "release-plan.json").write_text(json.dumps(data))
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "Daily/Dev"):
                module.verify_remote_candidate(self.directory, acceptance, repository=REPOSITORY,
                    source_sha=SHA, candidate_run_id="123", candidate_run_attempt="2", api_fn=self.api)


if __name__ == "__main__":
    unittest.main()
