import hashlib
import importlib.machinery
import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import sys

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import native_config as config

loader = importlib.machinery.SourceFileLoader("connect_minimax", str(SCRIPTS / "connect"))
installer = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
loader.exec_module(installer)


class MiniMaxWindowsTests(unittest.TestCase):
    def test_resolves_the_official_release_pointer_and_uses_its_node_runtime(self):
        with tempfile.TemporaryDirectory() as folder:
            prefix = Path(folder) / "a path"
            release = prefix / "releases" / "0.7.2-win-x64"
            entry = release / "node_modules" / "@minimax-ai" / "code" / "cli.js"
            entry.parent.mkdir(parents=True)
            entry.write_text("throw Error('not executed');", encoding="utf8")
            node = prefix / "node.exe"
            node.touch()
            shim = prefix / "mcode.cmd"
            shim.write_text(
                'SET /P MCODE_RELEASE=<"%~dp0current"\n'
                'CALL "%~dp0releases\\%MCODE_RELEASE%\\.mcode-launcher.cmd" %*\n', encoding="utf8",
            )
            (prefix / "current").write_text(release.name + "\n", encoding="utf8")
            (release / ".mcode-launcher.cmd").write_text(
                f'"{node}" "%~dp0node_modules\\@minimax-ai\\code\\cli.js" %*\n', encoding="utf8",
            )
            self.assertEqual(installer.minimax_windows_command(shim), (str(node.resolve()), str(entry.resolve())))
            (prefix / "current").write_text("..\\outside", encoding="utf8")
            with self.assertRaises(installer.ConnectError):
                installer.minimax_windows_command(shim)

    def test_resolves_the_npm_shim_with_a_node_executable(self):
        with tempfile.TemporaryDirectory() as folder:
            prefix = Path(folder)
            shim = prefix / "mcode.cmd"
            shim.write_text('"%_prog%" "%dp0%\\node_modules\\@minimax-ai\\code\\cli.js" %*', encoding="utf8")
            entry = prefix / "node_modules" / "@minimax-ai" / "code" / "cli.js"
            entry.parent.mkdir(parents=True)
            entry.touch()
            node = prefix / "node.exe"
            node.touch()
            self.assertEqual(installer.minimax_windows_command(shim), (str(node.resolve()), str(entry.resolve())))
            shim.write_text('cmd.exe /c something-else', encoding="utf8")
            with self.assertRaises(installer.ConnectError):
                installer.minimax_windows_command(shim)

    def test_registers_and_verifies_managed_windows_powershell_launcher(self):
        with tempfile.TemporaryDirectory(prefix="aiv-中文 O'Brien-") as folder:
            root = Path(folder)
            managed = root / "aivision" / "minimax-code"
            managed.mkdir(parents=True)
            installed = managed / "installed"
            plugin = {"key": "minimax-code", "host": str(root / "node.exe"), "node": str(root / "node.exe"),
                      "host_entry": str(root / "cli.js")}
            args = SimpleNamespace(agent="minimax-code")
            with patch.object(installer.platform, "system", return_value="Windows"):
                with config.ConfigTransaction() as tx:
                    installer.register_native(args, plugin, root, installed, managed, managed,
                                              None, tx, config)
                launcher = managed / "run.ps1"
                content = launcher.read_text(encoding="utf-8-sig")
                self.assertIn("--profile-dir", content)
                self.assertIn("O''Brien", content)
                self.assertIn("--mcode-entry", content)
                self.assertIn("@args", content)
                receipt = {"registration_files": {str(launcher): hashlib.sha256(launcher.read_bytes()).hexdigest()}}
                self.assertTrue(installer.native_registered("minimax-code", root, installed, managed, args, receipt))
                launcher.write_text("changed", encoding="utf8")
                self.assertIsNone(installer.native_registered("minimax-code", root, installed, managed, args, receipt))


if __name__ == "__main__":
    unittest.main()
