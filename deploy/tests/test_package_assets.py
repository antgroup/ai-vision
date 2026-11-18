"""Packaging regressions: drift, removed assets and compatibility ownership."""

from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest


SPEC = importlib.util.spec_from_file_location(
    "package_assets", Path(__file__).resolve().parents[1] / "scripts/package_assets.py")
packaging = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(packaging)


class PackageAssetsTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        sources = [source for source, _ in packaging.FILES]
        sources.extend(f"{source}/" + patterns[0].replace("*", "example")
                       for source, _, patterns in packaging.GROUPS)
        sources.extend(f"deploy/kubernetes/base/{name}" for name in packaging.NATIVE_KUBERNETES)
        for source in sources:
            path = self.root / source
            path.parent.mkdir(parents=True, exist_ok=True)
            if source in {f"deploy/kubernetes/base/{name}" for name in packaging.NATIVE_KUBERNETES}:
                path.write_text('{{if eq .NATIVE_PYTHON_RUNTIME "true"}}\nnative fixture\n{{else}}\nsource fixture\n{{end}}\n')
            else:
                path.write_text(f"canonical source: {source}\n")

    def run_package(self, check=False):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return packaging.package(self.root, check)


    def test_native_projection_excludes_source_program_and_preserves_source_copy(self):
        for name in ("database-init.yaml", "bootstrap.yaml"):
            source = self.root / "deploy/kubernetes/base" / name
            source.write_text('prefix\n{{if eq .NATIVE_PYTHON_RUNTIME "true"}}\n  command: [/app/native-helper]\n{{else}}\n  import confidential_initializer\n{{end}}\nsuffix\n')
        self.assertEqual(self.run_package(), 0)
        for name in ("database-init.yaml", "bootstrap.yaml"):
            native = self.root / "aivision-cli/deploy/kubernetes/native" / name
            self.assertTrue(native.is_file(), "native projection is missing")
            self.assertEqual(native.read_text(), "prefix\n  command: [/app/native-helper]\nsuffix\n")
            self.assertIn("import confidential_initializer", (self.root / "aivision-cli/deploy/kubernetes" / name).read_text())

    def test_reproducible_byte_identical_outputs(self):
        self.assertEqual(self.run_package(), 0)
        first = {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        self.assertEqual(self.run_package(), 0)
        self.assertEqual(self.run_package(check=True), 0)
        self.assertEqual(first, {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()})
        for source, destination in packaging.FILES:
            self.assertEqual((self.root / source).read_bytes(), (self.root / destination).read_bytes())

    def test_check_does_not_repair_missing_stale_or_extra_files(self):
        self.run_package()
        destination = self.root / packaging.FILES[0][1]
        destination.unlink()
        self.assertEqual(self.run_package(check=True), 1)
        self.assertFalse(destination.exists())
        self.run_package()
        destination.write_text("manual edit")
        extra = self.root / "aivision-cli/deploy/kubernetes/stale.yaml"
        extra.write_text("stale")
        self.assertEqual(self.run_package(check=True), 1)
        self.assertEqual(destination.read_text(), "manual edit")
        self.assertTrue(extra.exists())
        self.run_package()
        self.assertFalse(extra.exists())
        self.assertEqual(self.run_package(check=True), 0)

    def test_sync_removes_deleted_assets_but_preserves_go_source(self):
        self.run_package()
        canonical = self.root / "deploy/kubernetes/base/removed.yaml"
        canonical.write_text("old resource")
        self.run_package()
        canonical.unlink()
        go_source = self.root / "aivision-cli/deploy/kubernetes/assets.go"
        go_source.write_text("package manifests")
        self.assertEqual(self.run_package(check=True), 1)
        self.run_package()
        self.assertFalse((go_source.parent / "removed.yaml").exists())
        self.assertEqual(go_source.read_text(), "package manifests")

    def test_missing_source_fails_before_changing_generated_assets(self):
        self.run_package()
        manifest = (self.root / packaging.MANIFEST).read_bytes()
        (self.root / packaging.FILES[0][0]).unlink()
        with self.assertRaisesRegex(ValueError, "missing canonical asset"):
            self.run_package()
        self.assertEqual((self.root / packaging.MANIFEST).read_bytes(), manifest)

    def test_generated_symlink_cannot_write_outside_managed_paths(self):
        self.run_package()
        source, output = packaging.FILES[0]
        destination = self.root / output
        destination.unlink()
        destination.symlink_to(self.root / source)
        for check in (False, True):
            with self.assertRaisesRegex(ValueError, "symlink"):
                self.run_package(check=check)


if __name__ == "__main__":
    unittest.main()
