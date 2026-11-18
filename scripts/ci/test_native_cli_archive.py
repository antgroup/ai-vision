"""Real Go-linked CLI source-boundary regression; never executes foreign ELFs.

A dirty source checkout is expected to fail the complete native archive gate.
The payload/profile checks still run on the real linked ELF, and a clean CI or
release checkout additionally proves the entire archive is accepted.
"""
import hashlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest

from native_cli_archive import (CLI_MEMBERS, CLI_VERSION_SYMBOL, cli_python_programs,
                                read_cli_build_info, require_archive,
                                require_cli_native_payload)

ROOT = Path(__file__).resolve().parents[2]
VERSION = "0.0.0-test.native-boundary"


class RealNativeCLIArchiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.go = os.environ.get("AIV_TEST_GO") or shutil.which("go")
        if not cls.go:
            raise unittest.SkipTest("a host Go toolchain is required for actual CLI archive compilation")
        result = subprocess.run([cls.go, "version"], capture_output=True, text=True)
        if result.returncode:
            raise unittest.SkipTest("the configured host Go toolchain is unavailable")
        cls.temporary = tempfile.TemporaryDirectory(prefix="aiv-native-cli-regression-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.directory = Path(cls.temporary.name)
        cls.source_sha = subprocess.check_output(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
        cls.binaries = {}
        environment = dict(os.environ, CGO_ENABLED="0", GOOS="linux", GOARCH="amd64",
                           GOTOOLCHAIN="local", GOPROXY="off", GOMAXPROCS="2")
        for profile, tags in (("source", []), ("native", ["-tags", "native_distribution"])):
            output = cls.directory / ("aiv-" + profile)
            command = [cls.go, "build", "-mod=vendor", "-buildvcs=true", *tags,
                       "-ldflags", "-s -w -X " + CLI_VERSION_SYMBOL + "=" + VERSION,
                       "-o", str(output), "."]
            completed = subprocess.run(command, cwd=ROOT / "aivision-cli", env=environment,
                                       capture_output=True, text=True, timeout=180)
            if completed.returncode:
                raise AssertionError("Real CLI build failed for " + profile + ": " + completed.stderr)
            cls.binaries[profile] = output.read_bytes()

    def archive(self, binary, filename):
        path = self.directory / filename
        with tarfile.open(path, "w:gz") as archive:
            for name in sorted(CLI_MEMBERS):
                data = binary if name == "aiv" else (ROOT / name).read_bytes()
                member = tarfile.TarInfo(name)
                member.size = len(data)
                member.mode = 0o755 if name == "aiv" else 0o644
                archive.addfile(member, io.BytesIO(data))
        return path

    def test_default_real_cli_retains_source_compatibility_and_native_gate_rejects_it(self):
        binary = self.binaries["source"]
        self.assertEqual(binary[:4], b"\x7fELF")
        for name, source in cli_python_programs().items():
            if name.startswith("deploy/bootstrap/"):
                with self.subTest(program=name):
                    self.assertIn(source, binary)
        with self.assertRaisesRegex(ValueError, "first-party Python"):
            require_archive(self.archive(binary, "source.tar.gz"), product_version=VERSION,
                            platform="linux-x64", source_sha=self.source_sha, go=self.go)

    def test_native_real_cli_excludes_programs_and_preserves_honest_vcs_gate(self):
        binary = self.binaries["native"]
        self.assertEqual(binary[:4], b"\x7fELF")
        self.assertEqual(int.from_bytes(binary[18:20], "little"), 62)
        inventory = require_cli_native_payload(binary)
        self.assertGreaterEqual(len(inventory), 7)
        info = read_cli_build_info(binary, go=self.go)
        settings = {entry["Key"]: entry["Value"] for entry in info["Settings"]}
        self.assertEqual(settings["-tags"], "native_distribution")
        path = self.archive(binary, "native.tar.gz")
        if settings.get("vcs.revision") != self.source_sha or settings.get("vcs.modified") != "false":
            with self.assertRaisesRegex(ValueError, "VCS identity"):
                require_archive(path, product_version=VERSION, platform="linux-x64",
                                source_sha=self.source_sha, go=self.go)
        else:
            result = require_archive(path, product_version=VERSION, platform="linux-x64",
                                     source_sha=self.source_sha, go=self.go)
            self.assertEqual(result["executable_sha256"], hashlib.sha256(binary).hexdigest())
            self.assertEqual(result["source_boundary"], inventory)

    def test_native_real_elf_with_appended_owned_source_is_rejected(self):
        body = (ROOT / "deploy/bootstrap/backend-bootstrap.py").read_bytes()
        bad = self.binaries["native"] + body
        with self.assertRaisesRegex(ValueError, "first-party Python"):
            require_archive(self.archive(bad, "native-source-appended.tar.gz"),
                            product_version=VERSION, platform="linux-x64",
                            source_sha=self.source_sha, go=self.go)


if __name__ == "__main__":
    unittest.main()
