import tempfile
import unittest
import zipfile
from pathlib import Path

import windows_release_cli


def pe_header(machine):
    return (b"MZ" + b"\0" * 58 + (64).to_bytes(4, "little")
            + b"PE\0\0" + machine.to_bytes(2, "little"))


class WindowsReleaseCliTests(unittest.TestCase):
    def test_package_contains_x64_executable_and_attribution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = root / "aiv.exe"
            license_path = root / "LICENSE"
            archive = root / windows_release_cli.ASSET
            binary.write_bytes(pe_header(0x8664))
            license_path.write_text("license\n", encoding="utf-8")
            windows_release_cli.pack(binary, license_path, archive)
            windows_release_cli.verify_zip(archive)
            with zipfile.ZipFile(archive) as package:
                self.assertEqual(package.namelist(), ["aiv.exe", "LICENSE"])
                self.assertEqual(package.read("LICENSE"), b"license\n")
                self.assertEqual(package.read("aiv.exe"), binary.read_bytes())

    def test_reject_wrong_architecture(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = root / "aiv.exe"
            license_path = root / "LICENSE"
            binary.write_bytes(pe_header(0xAA64))
            license_path.write_text("license\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Windows x64 executable"):
                windows_release_cli.pack(binary, license_path, root / windows_release_cli.ASSET)

    def test_reject_manifest_with_unknown_cli_platforms(self):
        self.assertFalse(windows_release_cli.included({}))
        self.assertTrue(windows_release_cli.included({"cli_platforms": windows_release_cli.PLATFORMS}))
        with self.assertRaisesRegex(ValueError, "CLI platform set"):
            windows_release_cli.included({"cli_platforms": ["windows/x64"]})
