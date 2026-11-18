"""Guard the repeatable build boundary without requiring a local C compiler."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("native_workflow_build", Path(__file__).with_name("build.py"))
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


class NativeWorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "repository"
        self.source.mkdir()

    def arguments(self, **overrides):
        values = {
            "target": "linux/arm64",
            "component": "backend",
            "source_root": self.source,
            "work_dir": self.root / "work",
            "output_dir": self.root / "output",
        }
        values.update(overrides)
        argv = []
        for name, value in values.items():
            argv.extend(["--" + name.replace("_", "-"), str(value)])
        return builder.parse_args(argv)

    def write(self, root, relative, content="fixture"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def test_paths_inside_or_around_inputs_are_rejected_before_writes(self):
        for overrides in (
            {"work_dir": self.source / "generated"},
            {"output_dir": self.source / "runtime"},
            {"work_dir": self.root},
            {"output_dir": self.root},
            {"work_dir": self.root / "output" / "generated"},
            {"output_dir": self.root / "work" / "runtime"},
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaises(ValueError):
                    builder.validate_paths(self.arguments(**overrides))
                self.assertEqual(list(self.source.iterdir()), [])
                self.assertFalse((self.root / "work").exists())
                self.assertFalse((self.root / "output").exists())

    def test_existing_output_is_preserved_instead_of_replaced(self):
        sentinel = self.write(self.root / "output", "keep.txt", "customer artifact")
        with self.assertRaises(ValueError):
            builder.validate_paths(self.arguments())
        self.assertEqual(sentinel.read_text(), "customer artifact")

    def test_symlinked_output_cannot_hide_an_input_overlap(self):
        (self.root / "output").symlink_to(self.source, target_is_directory=True)
        with self.assertRaises(ValueError):
            builder.validate_paths(self.arguments())
        self.assertEqual(list(self.source.iterdir()), [])

    def test_dependency_copy_removes_owned_install_from_all_site_package_trees(self):
        dependency = self.root / "dependencies"
        for library in ("lib", "lib64"):
            site = f"{library}/python3.11/site-packages/"
            for relative in (
                "agentlens/__init__.py",
                "agentlens-0.1.0.dist-info/METADATA",
                "__editable__.agentlens-0.1.0.pth",
                "__editable___agentlens_0_1_0_finder.py",
                "__pycache__/__editable___agentlens_0_1_0_finder.cpython-311.pyc",
            ):
                self.write(dependency, site + relative, "owned source")
            self.write(dependency, site + "upstream/__init__.py", "allowed upstream")
        output = self.root / "staged"
        builder.copy_dependencies(dependency, output, "agentlens")
        self.assertFalse(any("agentlens" in path.name for path in output.rglob("*")))
        for library in ("lib", "lib64"):
            self.assertEqual(
                (output / library / "python3.11/site-packages/upstream/__init__.py").read_text(),
                "allowed upstream",
            )
        self.assertTrue((dependency / "lib/python3.11/site-packages/agentlens/__init__.py").exists())

    def test_dependency_copy_rejects_overlapping_source_without_mutation(self):
        dependency = self.root / "dependencies"
        sentinel = self.write(dependency, "keep.txt", "original")
        with self.assertRaises(ValueError):
            builder.copy_dependencies(dependency, dependency / "copy", "agentlens")
        self.assertEqual(sentinel.read_text(), "original")
        self.assertFalse((dependency / "copy").exists())

    def test_backend_dependency_copy_removes_owned_namespaces_and_distribution_metadata(self):
        dependency = self.root / "dependencies"
        site = "lib/python3.11/site-packages/"
        for relative in (
            "apps/core/models/agent.py",
            "aivision_backend/settings.py",
            "config/__init__.py",
            "aivision-0.1.0.dist-info/METADATA",
            "__editable__.aivision-0.1.0.pth",
            "__editable___aivision_0_1_0_finder.py",
        ):
            self.write(dependency, site + relative, "owned source")
        self.write(dependency, site + "django/__init__.py", "allowed upstream")
        output = self.root / "staged"
        builder.copy_dependencies(dependency, output, "backend")
        staged_site = output / site
        self.assertEqual(sorted(path.name for path in staged_site.iterdir()), ["django"])
        self.assertEqual((staged_site / "django/__init__.py").read_text(), "allowed upstream")

    def test_backend_snapshot_binds_runtime_data_and_catalog_attribution(self):
        source = Path(__file__).resolve().parents[2]
        backend = source / "ai-vision/aivision-py-be"
        spec = importlib.util.spec_from_file_location("backend_native_build", backend / "scripts/native_build.py")
        native_build = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(native_build)
        inputs = builder.snapshot_inputs(source, ["backend"])
        identity = native_build.input_identity(backend, source / "deploy/bootstrap")
        output = self.root / "runtime"
        native_build.copy_runtime_data(backend, output)
        for relative in (
            "apps/core/fixtures/onboarding_demo.otlp",
            "apps/core/data/models-dev-api.zip",
            "apps/core/data/models-dev-LICENSE",
            "apps/core/data/models-dev-NOTICE.txt",
        ):
            with self.subTest(relative=relative):
                digest = builder.digest(backend / relative)
                self.assertEqual(inputs[f"ai-vision/aivision-py-be/{relative}"], digest)
                self.assertEqual(identity[f"backend/{relative}"], digest)
                self.assertEqual(builder.digest(output / relative), digest)

    def test_backend_dependency_copy_skips_top_level_lib64_alias(self):
        dependency = self.root / "dependencies"
        upstream = self.write(dependency, "lib/python3.11/site-packages/upstream/lib64/__init__.py")
        (dependency / "lib64").symlink_to("lib", target_is_directory=True)
        output = self.root / "staged"

        with patch.object(builder.sys, "platlibdir", "lib"):
            builder.copy_dependencies(dependency, output, "backend")

        self.assertEqual((output / upstream.relative_to(dependency)).read_bytes(), upstream.read_bytes())
        self.assertNotIn("lib64", {path.name for path in output.iterdir()})
        self.assertTrue((dependency / "lib64").is_symlink())

    def test_runtime_must_match_python_abi_operating_system_and_target(self):
        valid = {"python": [3, 11, 14], "system": "Linux", "machine": "aarch64"}
        builder.check_runtime(valid, "linux/arm64", "fixture")
        for changed in (
            {"python": [3, 12, 0]},
            {"python": [3, 11, 13]},
            {"system": "Darwin"},
            {"machine": "x86_64"},
        ):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                builder.check_runtime({**valid, **changed}, "linux/arm64", "fixture")

    def test_prepared_dependencies_reject_wrong_lock_or_abi(self):
        dependency = self.root / "dependencies"
        self.write(dependency, "bin/python", "fixture interpreter")
        lock = self.write(self.root, "resolved.lock", "unchanged locked packages")
        expected = self.write(self.root, "expected.lock", "unchanged locked packages")
        probe = {"python": [3, 11, 14], "system": "Linux", "machine": "aarch64"}
        with patch.object(builder, "probe_python", return_value=probe):
            builder.validate_dependency(dependency, lock, expected, "linux/arm64")
            lock.write_text("different dependencies")
            with self.assertRaises(ValueError):
                builder.validate_dependency(dependency, lock, expected, "linux/arm64")
            lock.write_text(expected.read_text())
        with patch.object(builder, "probe_python", return_value={**probe, "machine": "x86_64"}):
            with self.assertRaises(ValueError):
                builder.validate_dependency(dependency, lock, expected, "linux/arm64")

    def test_failed_subprocess_is_reported_with_its_log(self):
        log = self.root / "compiler.log"
        with self.assertRaises(subprocess.CalledProcessError):
            builder.run(
                [sys.executable, "-c", "print('compile failed', flush=True); raise SystemExit(17)"],
                log=log,
            )
        self.assertIn("compile failed", log.read_text())

    def test_runtime_audit_permits_upstream_source_and_rejects_owned_module_alias(self):
        auditor = Path(__file__).resolve().parents[2] / "scripts/build/native_artifacts.py"
        destination = self.source / "scripts/build/native_artifacts.py"
        destination.parent.mkdir(parents=True)
        shutil.copy2(auditor, destination)
        runtime, evidence = self.root / "runtime", self.root / "evidence"
        evidence.mkdir()
        site = ".venv/lib/python3.11/site-packages/"
        self.write(runtime, site + "upstream/__init__.py", "def upstream_function(): pass\n")
        header = bytearray(64)
        header[:6] = b"\x7fELF\x02\x01"
        header[18:20] = (183).to_bytes(2, "little")
        (runtime / "compiled.so").write_bytes(header)
        result = builder.audit_runtime(self.source, runtime, evidence, "linux/arm64")
        self.assertEqual(result["files_checked"], 2)
        self.assertEqual(result["elf_files"], 1)
        self.write(runtime, site + "agentlens.py", "def owned_business_function(): pass\n")
        with self.assertRaisesRegex(ValueError, "runtime source-content audit failed"):
            builder.audit_runtime(self.source, runtime, evidence, "linux/arm64")

    def test_backend_smoke_imports_helpers_without_running_database_initializer(self):
        arguments = self.arguments()
        work, output = arguments.work_dir, arguments.output_dir
        work.mkdir()
        output.mkdir()
        dependency = self.root / "dependencies"
        self.write(dependency, "bin/python", "upstream interpreter fixture")
        commands = []

        def execute(command, **kwargs):
            commands.append([str(part) for part in command])
            if str(command[1]).endswith("/scripts/native_build.py"):
                self.write(output, "backend/runtime/native-backend-manifest.json", "{}")

        with patch.object(builder, "run", side_effect=execute), \
             patch.object(builder, "audit_runtime", return_value={"files_checked": 1}):
            result = builder.build_component(
                "backend", arguments, self.source, work, output,
                {"service": {"python": "/fixture/compiler-python"}},
                {"backend": {"venv": str(dependency)}},
            )
        self.assertEqual(commands[1], [str(output / "backend/runtime/manage"), "check"])
        self.assertEqual(
            commands[2],
            [str(output / "backend/runtime/.venv/bin/python"), "-c", "import backend_bootstrap, wait_http"],
        )
        self.assertFalse(any(Path(command[0]).name == "database-init" for command in commands))
        self.assertEqual(result["smoke"], ["manage check", "import backend_bootstrap, wait_http"])

    def onboarding_fixture(self):
        for relative in (
            "deploy/native-packaging/build.py",
            "deploy/native-packaging/requirements-build.txt",
            "scripts/build/native_artifacts.py",
            "scripts/build/build_native_onboarding.py",
            "ai-vision/agent-plugins/scripts/connect",
            "ai-vision/agent-plugins/scripts/native_config.py",
            "ai-vision/agent-plugins/scripts/hook_runtime.py",
        ):
            self.write(self.source, relative, "fixed compiler input")
        return self.source / "ai-vision/agent-plugins/scripts/connect"

    def run_fixture_build(self, component_action):
        probe = {"python": [3, 11, 14], "system": "Linux", "machine": "aarch64"}
        with patch.object(builder, "probe_python", return_value=probe), \
             patch.object(builder.shutil, "which", return_value="/fixture/compiler"), \
             patch.object(builder, "prepare_tools", return_value={}), \
             patch.object(builder, "prepare_dependencies", return_value={}), \
             patch.object(builder, "build_component", side_effect=component_action):
            return builder.build(self.arguments(component="onboarding"))

    def test_compiler_failure_leaves_no_success_receipt(self):
        self.onboarding_fixture()

        def failed_compile(component, args, source, work, output, tools, deps):
            self.write(output, "partial-artifact", "incomplete")
            raise subprocess.CalledProcessError(17, ["fixture-compiler"])

        with self.assertRaises(subprocess.CalledProcessError):
            self.run_fixture_build(failed_compile)
        self.assertTrue((self.root / "output/partial-artifact").exists())
        self.assertFalse((self.root / "output/receipt.json").exists())
        self.assertFalse((self.root / "output/.receipt.json.pending").exists())

    def test_source_edit_during_compilation_rejects_success_receipt(self):
        source = self.onboarding_fixture()

        def changed_input(component, args, source_root, work, output, tools, deps):
            source.write_text("edited during compilation")
            self.write(output, "partial-artifact", "compiled stale bytes")
            return {"archive": "partial-artifact"}

        with self.assertRaisesRegex(ValueError, "inputs changed"):
            self.run_fixture_build(changed_input)
        self.assertFalse((self.root / "output/receipt.json").exists())

    def test_success_receipt_identifies_actual_fixed_source_bytes(self):
        self.onboarding_fixture()

        def finished_compile(component, args, source, work, output, tools, deps):
            self.write(output, "fixture-archive", "accepted bytes")
            return {"archive": "fixture-archive", "sha256": "fixture-produced-hash"}

        self.run_fixture_build(finished_compile)
        receipt = json.loads((self.root / "output/receipt.json").read_text())
        self.assertTrue(receipt["inputs_stable"])
        self.assertEqual(receipt["target"], "linux/arm64")
        self.assertEqual(set(receipt["components"]), {"onboarding"})
        # SHA-256 of the literal fixture content, independent of production hashing.
        self.assertEqual(
            receipt["input_sha256"]["ai-vision/agent-plugins/scripts/connect"],
            "440cd9063d3e9b61f44aa91f18a12d9271ea354bb7721352a209e64def3b398d",
        )
        self.assertFalse((self.root / "output/.receipt.json.pending").exists())


if __name__ == "__main__":
    unittest.main()
