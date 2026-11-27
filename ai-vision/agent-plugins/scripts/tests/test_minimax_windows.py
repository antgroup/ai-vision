import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
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
    def test_acl_failure_does_not_redeem_the_installation_grant(self):
        args = SimpleNamespace(dry_run=False, agent_uuid="00000000-0000-0000-0000-000000000001")
        pin = ("minimax-code", "http://localhost", {}, {}, {"host_version": "0.7.2"}, Path.cwd(), {})
        with (patch.object(installer.platform, "system", return_value="Windows"),
              patch.object(installer, "preflight", return_value=pin),
              patch.object(installer, "prepare_minimax_windows_credentials",
                           side_effect=installer.ConnectError("ACL not available")),
              patch.object(installer, "install_authorization") as redeem):
            with self.assertRaisesRegex(installer.ConnectError, "ACL not available"):
                installer.install(args)
            redeem.assert_not_called()

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


@unittest.skipUnless(os.name == "nt" and shutil.which("pwsh.exe"), "native Windows and PowerShell 7 required")
class MiniMaxWindowsCredentialACLTests(unittest.TestCase):
    def read_acl(self, path):
        result = subprocess.run([
            shutil.which("pwsh.exe"), "-NoProfile", "-NonInteractive", "-Command", """
$ErrorActionPreference = 'Stop'
$acl = Get-Acl -LiteralPath $env:AIVISION_MINIMAX_TEST_ACL_PATH
$type = [System.Security.Principal.SecurityIdentifier]
$rules = @($acl.GetAccessRules($true, $true, $type) | ForEach-Object {
  @{sid=$_.IdentityReference.Value; access=$_.AccessControlType.ToString(); rights=$_.FileSystemRights.ToString()}
})
@{owner=$acl.GetOwner($type).Value; caller=[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value;
  protected=$acl.AreAccessRulesProtected; rules=$rules} | ConvertTo-Json -Compress -Depth 4
""",
        ], env={**os.environ, "AIVISION_MINIMAX_TEST_ACL_PATH": str(path)},
            check=True, capture_output=True, text=True, timeout=15)
        return json.loads(result.stdout)

    def assert_private(self, path):
        acl = self.read_acl(path)
        self.assertTrue(acl["protected"])
        self.assertEqual(acl["owner"], acl["caller"])
        self.assertEqual(acl["rules"], [{"sid": acl["caller"], "access": "Allow", "rights": "FullControl"}])

    def test_credential_acl_survives_atomic_writes_backups_and_rollback(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            managed = root / "aivision" / "minimax-code"
            managed.mkdir(parents=True)
            root_acl = self.read_acl(root)
            ingest = managed / "ingest.json"
            ingest.write_text('{"fixture": "old"}', encoding="utf8")
            pending = managed / "pending-installation.json"
            token = managed / "ingest-token.json"
            write = installer.prepare_minimax_windows_credentials(managed)
            self.assert_private(managed)
            self.assert_private(ingest)
            installer.save_installation_authorization(pending, {"fixture": "pending"}, write)
            with config.ConfigTransaction(atomic_writer=write) as transaction:
                transaction.json(ingest, {"fixture": "new"}, private=True)
                transaction.json(token, {"fixture": "token"}, private=True)
            backup = ingest.with_name(ingest.name + ".aivision-backup")
            with self.assertRaisesRegex(RuntimeError, "rollback"):
                with config.ConfigTransaction(atomic_writer=write) as transaction:
                    transaction.json(ingest, {"fixture": "rollback"}, private=True)
                    raise RuntimeError("rollback")
            self.assertEqual(json.loads(ingest.read_text(encoding="utf8")), {"fixture": "new"})
            self.assertEqual(json.loads(backup.read_text(encoding="utf8")), {"fixture": "old"})
            for path in (ingest, pending, token, backup):
                self.assert_private(path)
            self.assertEqual(self.read_acl(root), root_acl)

    def test_rejects_hard_linked_existing_credentials(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            managed = root / "aivision" / "minimax-code"
            managed.mkdir(parents=True)
            external = root / "other.json"
            external.write_text("unrelated", encoding="utf8")
            acl = self.read_acl(external)
            os.link(external, managed / "ingest.json")
            with self.assertRaisesRegex(installer.ConnectError, "private NTFS directory"):
                installer.prepare_minimax_windows_credentials(managed)
            self.assertEqual(self.read_acl(external), acl)

    def test_creates_private_directory_and_refuses_a_managed_junction(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            managed = root / "aivision" / "minimax-code"
            installer.prepare_minimax_windows_credentials(managed)
            self.assert_private(managed)
            managed.rmdir()
            external = root / "unrelated"
            external.mkdir()
            acl = self.read_acl(external)
            subprocess.run([
                shutil.which("pwsh.exe"), "-NoProfile", "-NonInteractive", "-Command",
                "$ErrorActionPreference = 'Stop'; "
                "New-Item -ItemType Junction -Path $env:AIVISION_MINIMAX_TEST_ACL_PATH "
                "-Target $env:AIVISION_MINIMAX_TEST_ACL_TARGET | Out-Null",
            ], env={**os.environ, "AIVISION_MINIMAX_TEST_ACL_PATH": str(managed),
                    "AIVISION_MINIMAX_TEST_ACL_TARGET": str(external)}, check=True, timeout=15)
            with self.assertRaisesRegex(installer.ConnectError, "private NTFS directory"):
                installer.prepare_minimax_windows_credentials(managed)
            self.assertEqual(self.read_acl(external), acl)


if __name__ == "__main__":
    unittest.main()
