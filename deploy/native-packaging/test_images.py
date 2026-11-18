"""Exercise sanitization and every-layer checks with small real file archives."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import tarfile
import tempfile
import unittest


SPEC = importlib.util.spec_from_file_location("native_images", Path(__file__).with_name("images.py"))
images = importlib.util.module_from_spec(SPEC)
if SPEC.loader and Path(SPEC.origin).exists():
    SPEC.loader.exec_module(images)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def tar_bytes(files, member_pax=None, global_pax=None, member_linkname="", raw_name=None):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", pax_headers=global_pax) as archive:
        for name, content in files.items():
            info = tarfile.TarInfo(raw_name or name)
            info.pax_headers = dict(member_pax or {})
            if raw_name:
                info.pax_headers["path"] = name
            info.linkname = member_linkname
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    return output.getvalue()


class ImageBoundaryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def write(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value if isinstance(value, bytes) else value.encode())
        return path

    def test_native_image_healthchecks_follow_configured_service_ports(self):
        probe = self.write("probe", '#!/bin/sh\nprintf "%s\\n" "$@" > "$PROBE_CAPTURE"\nexit "$PROBE_STATUS"\n')
        probe.chmod(0o755)
        capture = self.root / "probe-arguments"
        for component, variable, default, endpoint in (
            ("backend", "BE_PORT", "8001", "/api/v1/ready"),
            ("agentlens", "AGENTLENS_PORT", "8081", "/internal/readyz"),
        ):
            dockerfile = Path(__file__).with_name("Dockerfile.native-" + component).read_text()
            health = next(line for line in dockerfile.splitlines() if line.startswith("HEALTHCHECK "))
            command = health.split(" CMD ", 1)[1]
            if command.startswith("["):
                arguments = json.loads(command)
                arguments[0] = str(probe)
            else:
                arguments = ["/bin/sh", "-c", command.replace("/usr/local/bin/aiv-http-probe", shlex.quote(str(probe)))]
            for port in (None, "", "32109"):
                for status in (0, 27):
                    with self.subTest(component=component, configured_port=port, probe_status=status):
                        environment = {key: value for key, value in os.environ.items()
                                       if key not in ("BE_PORT", "AGENTLENS_PORT")}
                        environment.update(PROBE_CAPTURE=str(capture), PROBE_STATUS=str(status))
                        if port is not None:
                            environment[variable] = port
                        result = subprocess.run(arguments, env=environment, capture_output=True, timeout=3)
                        self.assertEqual(capture.read_text().splitlines(),
                                         ["127.0.0.1", port or default, endpoint, "3"])
                        self.assertEqual(result.returncode, status)

    def test_input_binding_rejects_changed_and_new_compilation_inputs(self):
        source = self.root / "source"
        self.write("source/owned.py", "original")
        expected = {"owned.py": digest(b"original")}
        images.verify_input_hashes(source, expected, expected)
        self.write("source/owned.py", "changed")
        with self.assertRaisesRegex(ValueError, "input.*changed"):
            images.verify_input_hashes(source, expected, expected)
        self.write("source/owned.py", "original")
        with self.assertRaisesRegex(ValueError, "input.*set"):
            images.verify_input_hashes(source, expected, {**expected, "added.py": "0" * 64})

    def test_metadata_rejects_wrong_component_revision_or_dirty_identity(self):
        metadata = {"schema_version": 1, "component": "aivision-be", "source_sha": "a" * 40,
                    "dirty": False, "product_version": "0.0.1-daily.20261028", "component_version": "0.0.1-daily.20261028",
                    "build_id": "20261028.1", "built_at": "2026-10-28T12:00:00Z", "target_channel": "daily"}
        images.validate_build_info(metadata, "backend", "a" * 40)
        for field, value in (("component", "agentlens"), ("source_sha", "b" * 40), ("dirty", True)):
            with self.subTest(field=field), self.assertRaises(ValueError):
                images.validate_build_info({**metadata, field: value}, "backend", "a" * 40)

    def test_safe_archive_extraction_rejects_traversal_and_links(self):
        for name in ("../owned.py", "/owned.py"):
            archive = self.write("bad.tar", tar_bytes({name: b"bad"}))
            with self.assertRaises(ValueError):
                images.extract_reviewed_tar(archive, self.root / "extract")
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w") as archive:
            info = tarfile.TarInfo("link")
            info.type = tarfile.SYMTYPE
            info.linkname = "/source"
            archive.addfile(info)
        archive = self.write("bad.tar", output.getvalue())
        with self.assertRaisesRegex(ValueError, "link|regular"):
            images.extract_reviewed_tar(archive, self.root / "extract")

    def test_owned_archive_audit_detects_embedded_python_and_c_source(self):
        for name, value in (("renamed", b"python -c 'print(1)'"), ("launcher.c", b"int main(void) { return 0; }"),
                            ("bundle.tgz", tar_bytes({"hidden.py": b"secret = 1"}))):
            with self.subTest(name=name):
                self.assertTrue(images.owned_issues(name, value))
        self.assertEqual(images.owned_issues("native", b"\x7fELF" + b"\0" * 60), [])

    def test_nested_archive_c_source_is_excluded(self):
        nested = tar_bytes({"bundle.tgz": tar_bytes({"source.c": b"int main(void) { return 0; }"})})
        self.assertTrue(images.owned_issues("outer.tgz", nested))

    def test_renamed_compressed_archive_and_interpreter_flags_cannot_hide_source(self):
        import gzip
        payload = gzip.compress(tar_bytes({"secret.py": b"secret = 1"}))
        self.assertTrue(images.owned_issues("renamed-data", payload))
        for value in (b"python -I -S -c 'secret()'", b'["python", "-I", "-S", "-c", "secret()"]',
                      b"python -I -S <<'PY'\nsecret()\nPY\n"):
            with self.subTest(value=value):
                self.assertTrue(images.owned_issues("launcher.txt", value))

    def test_installer_is_published_at_the_native_provider_contract_path(self):
        destination = images.native_installer_destination(self.root, "linux-gnu-x64")
        self.assertEqual(destination, self.root / "provider/native-onboarding/linux-gnu-x64/connect.tar.gz")

    def test_prepare_agentlens_injects_metadata_as_data_and_sanitizes_entrypoint(self):
        source = self.root / "source"
        required = ("deploy/native-packaging/build.py", "deploy/native-packaging/requirements-build.txt", "scripts/build/native_artifacts.py",
                    "agentlens/native/requirements-build.txt", "agentlens/docker/selfhost-entrypoint.sh", "agentlens/obschema/alembic.ini",
                    "agentlens/env/env.dev", "agentlens/env/env.prod", "agentlens/uv.lock", "agentlens/pyproject.toml",
                    "deploy/bootstrap/database-init.py", "agentlens/src/agentlens/main.py")
        for relative in required:
            self.write("source/" + relative, "fixture compilation input")
        elf = bytearray(b"\x7fELF\x02\x01" + b"\0" * 54)
        elf[18:20] = (62).to_bytes(2, "little")
        self.write("stage/agentlens/runtime/src/agentlens/main.so", bytes(elf))
        self.write("stage/agentlens/runtime/.venv/upstream.py", "import os\n")
        self.write("stage/agentlens/runtime/agentlens-entrypoint", "#!/bin/bash\nexec python -m uvicorn\n")
        runtime = self.root / "stage/agentlens/runtime"
        manifest = images.AUDITOR.make_manifest(runtime, self_owned_roots=["."], dependency_roots={".venv": "Upstream fixture dependency"})
        report = images.AUDITOR.audit_artifact(runtime, manifest)
        images.write_json(self.root / "stage/agentlens/manifest.json", manifest)
        images.write_json(self.root / "stage/agentlens/source-audit.json", report)
        receipt = {"schema_version": 1, "target": "linux/amd64", "inputs_stable": True, "source_revision": None,
                   "input_sha256": images.PRODUCER.snapshot_inputs(source, ["agentlens"]),
                   "components": {"agentlens": {"runtime": "agentlens/runtime", "manifest_sha256": images.file_hash(self.root / "stage/agentlens/manifest.json"),
                                                "source_audit_sha256": images.file_hash(self.root / "stage/agentlens/source-audit.json")}}}
        images.write_json(self.root / "stage/receipt.json", receipt)
        metadata = {"schema_version": 1, "component": "agentlens", "source_sha": "a" * 40, "dirty": False,
                    "product_version": "0.0.1-daily.20261028", "component_version": "0.0.1-daily.20261028", "target_channel": "daily",
                    "build_id": "fixture.1", "built_at": "2026-10-28T12:00:00Z"}
        info = self.write("metadata.json", json.dumps(metadata))
        probe = self.write("probe", bytes(elf))
        args = images.parse_args(["prepare", "--component", "agentlens", "--target", "linux/amd64", "--source-root", str(source),
                                  "--source-revision", "a" * 40, "--stage-dir", str(self.root / "stage"), "--build-info", str(info),
                                  "--health-probe", str(probe), "--base-image", "pristine-python:fixture", "--base-image-id", "sha256:" + "b" * 64,
                                  "--output-dir", str(self.root / "output")])
        result = images.prepare_context(args)
        context = self.root / "output/context"
        self.assertEqual((context / "metadata/build-info.json").read_bytes(), info.read_bytes())
        self.assertEqual(result["build_info"], metadata)
        self.assertIn("./agentlens-migrate", (context / "runtime/agentlens-entrypoint").read_text())
        self.assertFalse((context / "runtime/src/agentlens/main.py").exists())
        self.assertFalse((context / "receipt.json").exists())
        self.assertEqual(result["image_files"]["app/.venv/upstream.py"]["owner"], "dependency")
        self.assertIn("PYTHONDONTWRITEBYTECODE=1", (context / "Dockerfile").read_text())

    def test_runtime_copy_does_not_accept_artifacts_changed_after_stage_audit(self):
        runtime = self.root / "runtime"
        self.write("runtime/manage", b"\x7fELF" + b"\0" * 60)
        manifest = images.AUDITOR.make_manifest(runtime, self_owned_roots=["."], dependency_roots={})
        self.write("runtime/manage", b"\x7fELF" + b"\1" * 60)
        with self.assertRaisesRegex(ValueError, "copied.*changed"):
            images.verify_copied_runtime(runtime, manifest, set())

    def test_public_example_approval_binds_exact_bytes(self):
        source = self.write("provider/guide.yaml", "example: approved")
        approvals = {"guide.yaml": {"sha256": digest(source.read_bytes()), "reason": "Approved public SDK guide"}}
        images.verify_public_examples(self.root / "provider", approvals, {"guide.yaml"})
        self.write("provider/guide.yaml", "example: changed")
        with self.assertRaisesRegex(ValueError, "example.*changed"):
            images.verify_public_examples(self.root / "provider", approvals, {"guide.yaml"})
        with self.assertRaisesRegex(ValueError, "exact"):
            images.verify_public_examples(self.root / "provider", approvals, {"guide.yaml", "other.py"})

    def save_image(self, name, layers, history=None, config_values=None, config_fields=None,
                   envelope_files=None, envelope_options=None, config_suffix=b""):
        layer_names = [f"layer-{index}/layer.tar" for index in range(len(layers))]
        config = {"architecture": "amd64", "os": "linux", "rootfs": {"type": "layers", "diff_ids": ["sha256:" + digest(data) for data in layers]},
                  "config": config_values or {}, "history": history or []}
        config.update(config_fields or {})
        config_bytes = json.dumps(config).encode()
        if config_suffix:
            config_bytes = config_bytes[:-1] + b", " + config_suffix + b"}"
        config_name = digest(config_bytes) + ".json"
        files = {config_name: config_bytes,
                 "manifest.json": json.dumps([{"Config": config_name, "RepoTags": ["fixture:latest"], "Layers": layer_names}]).encode(),
                 **dict(zip(layer_names, layers))}
        files.update(envelope_files or {})
        return self.write(name, tar_bytes(files, **(envelope_options or {}))), "sha256:" + digest(config_bytes)

    def receipt(self, base_id, files):
        return {"schema_version": 1, "component": "backend", "target": "linux/amd64", "base_image_id": base_id,
                "base_image": "python@sha256:" + "b" * 64, "public_examples": {}, "dependency_roots": ["app/.venv"],
                "owned_roots": ["app", "agent-plugins", "etc/aivision", "usr/local/bin/aiv-http-probe"],
                "image_files": {name: {"sha256": digest(data), "size": len(data), "owner": "self"} for name, data in files.items()}}

    def test_every_layer_audit_accepts_upstream_base_and_exact_native_files(self):
        base_layer = tar_bytes({"usr/local/lib/python3.11/os.py": b"import sys\n"})
        base, base_id = self.save_image("base.tar", [base_layer])
        files = {"app/manage": b"\x7fELF" + b"\0" * 60, "app/.venv/upstream.py": b"import sys\n"}
        image, _ = self.save_image("image.tar", [base_layer, tar_bytes(files)])
        receipt = self.receipt(base_id, files)
        receipt["image_files"]["app/.venv/upstream.py"]["owner"] = "dependency"
        report = images.audit_image_save(image, base, receipt)
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["base_layers"], 1)
        self.assertEqual(report["product_layers"], 1)

    def test_every_layer_audit_catches_source_deleted_by_later_layer(self):
        base_layer = tar_bytes({"usr/local/lib/python3.11/os.py": b"import sys\n"})
        base, base_id = self.save_image("base.tar", [base_layer])
        image, _ = self.save_image("image.tar", [base_layer, tar_bytes({"app/secret.py": b"secret = 1"}),
                                                tar_bytes({"app/.wh.secret.py": b""})])
        report = images.audit_image_save(image, base, self.receipt(base_id, {}))
        self.assertFalse(report["ok"])
        self.assertTrue(any("secret.py" in issue["path"] for issue in report["violations"]))

    def test_every_layer_audit_rejects_foreign_base_and_inline_history(self):
        base_layer = tar_bytes({"upstream": b"base"})
        base, base_id = self.save_image("base.tar", [base_layer])
        changed, _ = self.save_image("changed.tar", [tar_bytes({"copied-product.py": b"owned = 1"})])
        self.assertFalse(images.audit_image_save(changed, base, self.receipt(base_id, {}))["ok"])
        image, _ = self.save_image("image.tar", [base_layer], [{"created_by": "RUN python -c 'secret()'"}])
        self.assertFalse(images.audit_image_save(image, base, self.receipt(base_id, {}))["ok"])

    def test_image_history_must_retain_the_exact_base_prefix(self):
        base_layer = tar_bytes({"upstream": b"base"})
        history = [{"created_by": "upstream build", "empty_layer": True}]
        base, base_id = self.save_image("base.tar", [base_layer], history)
        image, _ = self.save_image("image.tar", [base_layer], [{"created_by": "RUN python -c 'private_logic()'", "empty_layer": True}])
        report = images.audit_image_save(image, base, self.receipt(base_id, {}))
        self.assertFalse(report["ok"], report)

    def test_image_history_decodes_strings_before_source_checks(self):
        base_layer = tar_bytes({"upstream": b"base"})
        base, base_id = self.save_image("base.tar", [base_layer])
        command = 'RUN echo "import private_logic\nprivate_logic.main()"'
        image, _ = self.save_image("image.tar", [base_layer], [{"created_by": command}])
        report = images.audit_image_save(image, base, self.receipt(base_id, {}))
        self.assertFalse(report["ok"], report)
        image, _ = self.save_image("labels.tar", [base_layer], config_values={"Labels": {"nested": "import private_logic\nprivate_logic.main()"}})
        self.assertFalse(images.audit_image_save(image, base, self.receipt(base_id, {}))["ok"])

    def test_every_top_level_config_field_and_nested_key_is_checked(self):
        base_layer = tar_bytes({"upstream": b"base"})
        base, base_id = self.save_image("base.tar", [base_layer])
        source = "import private_logic\nprivate_logic.main()"
        for metadata in ({"author": source}, {"container_config": {"Cmd": [source]}},
                         {"extensions": {"deep": {source: "metadata"}}},
                         {source: "top-level key"}):
            with self.subTest(metadata=metadata):
                image, _ = self.save_image("image.tar", [base_layer], config_fields=metadata)
                report = images.audit_image_save(image, base, self.receipt(base_id, {}))
                self.assertFalse(report["ok"], report)
                self.assertTrue(any(item["code"] == "embedded-python" for item in report["violations"]), report)

    def test_only_exact_upstream_history_prefix_is_exempt_from_config_source_check(self):
        base_layer = tar_bytes({"upstream.py": b"import upstream_runtime\n"})
        history = [{"created_by": "RUN python -c 'import upstream_runtime'", "empty_layer": True}]
        base, base_id = self.save_image("base.tar", [base_layer], history)
        files = {"app/manage": b"\x7fELF" + b"\0" * 60}
        image, _ = self.save_image("image.tar", [base_layer, tar_bytes(files)], history)
        self.assertTrue(images.audit_image_save(image, base, self.receipt(base_id, files))["ok"])
        image, _ = self.save_image("changed.tar", [base_layer, tar_bytes(files)],
                                  history + [{"created_by": "RUN python -c 'private_logic()'", "empty_layer": True}])
        self.assertFalse(images.audit_image_save(image, base, self.receipt(base_id, files))["ok"])

    def test_duplicate_config_keys_cannot_hide_source_before_json_decoding(self):
        base_layer = tar_bytes({"upstream": b"base"})
        base, base_id = self.save_image("base.tar", [base_layer])
        image, _ = self.save_image("image.tar", [base_layer],
            config_fields={"author": "import private_logic\nprivate_logic.main()"},
            config_suffix=b'"author": "overridden metadata"')
        report = images.audit_image_save(image, base, self.receipt(base_id, {}))
        self.assertFalse(report["ok"], report)

    def test_product_layer_raw_headers_and_trailer_cannot_carry_source(self):
        base_layer = tar_bytes({"upstream": b"base"})
        base, base_id = self.save_image("base.tar", [base_layer])
        files = {"app/manage": b"\x7fELF" + b"\0" * 60}
        source = "import private_logic\nprivate_logic.main()"
        layers = (tar_bytes(files, member_linkname=source),
                  tar_bytes(files, raw_name=source), tar_bytes(files) + source.encode())
        for layer in layers:
            with self.subTest(layer_sha256=digest(layer)):
                image, _ = self.save_image("image.tar", [base_layer, layer])
                report = images.audit_image_save(image, base, self.receipt(base_id, files))
                self.assertFalse(report["ok"], report)

    def test_outer_save_raw_metadata_and_trailer_cannot_carry_source(self):
        base_layer = tar_bytes({"upstream": b"base"})
        base, base_id = self.save_image("base.tar", [base_layer])
        source = "import private_logic\nprivate_logic.main()"
        for options in ({"member_linkname": source}, {"raw_name": source},
                        {"global_pax": {"comment": source}}):
            with self.subTest(options=options):
                image, _ = self.save_image("image.tar", [base_layer], envelope_options=options)
                self.assertFalse(images.audit_image_save(image, base, self.receipt(base_id, {}))["ok"])
        image, _ = self.save_image("trailer.tar", [base_layer])
        image.write_bytes(image.read_bytes() + source.encode())
        self.assertFalse(images.audit_image_save(image, base, self.receipt(base_id, {}))["ok"])

    def test_outer_save_additional_metadata_is_checked_with_exact_base_content_exemption(self):
        base_layer = tar_bytes({"upstream": b"base"})
        upstream = {"legacy.json": b'{"upstream_recipe": "import upstream_runtime\\nupstream_runtime.main()"}'}
        base, base_id = self.save_image("base.tar", [base_layer], envelope_files=upstream)
        image, _ = self.save_image("same-upstream.tar", [base_layer], envelope_files=upstream)
        self.assertTrue(images.audit_image_save(image, base, self.receipt(base_id, {}))["ok"])
        source = "import private_logic\nprivate_logic.main()"
        for metadata in ({"added.json": json.dumps({"program": source}).encode()},
                         {"legacy.json": json.dumps({"program": source}).encode()},
                         {"nested.tar": tar_bytes({"private.py": source.encode()})}):
            with self.subTest(metadata=tuple(metadata)):
                image, _ = self.save_image("image.tar", [base_layer], envelope_files=metadata)
                self.assertFalse(images.audit_image_save(image, base, self.receipt(base_id, {}))["ok"])

    def test_product_layer_metadata_cannot_carry_source(self):
        base_layer = tar_bytes({"upstream": b"base"})
        base, base_id = self.save_image("base.tar", [base_layer])
        files = {"app/manage": b"\x7fELF" + b"\0" * 60}
        source = "#!/usr/bin/python\nimport private_logic\nprivate_logic.main()"
        for metadata in ({"member_pax": {"comment": source}}, {"global_pax": {"comment": source}}):
            with self.subTest(metadata=metadata):
                image, _ = self.save_image("image.tar", [base_layer, tar_bytes(files, **metadata)])
                report = images.audit_image_save(image, base, self.receipt(base_id, files))
                self.assertFalse(report["ok"], report)

    def test_owned_nested_archive_metadata_cannot_carry_source(self):
        nested = tar_bytes({"api.so": b"\x7fELF" + b"\0" * 60}, member_pax={"comment": "import private_logic\nprivate_logic.main()"})
        self.assertTrue(images.owned_issues("bundle.tar", tar_bytes({"nested.tar": nested})))


if __name__ == "__main__":
    unittest.main()
