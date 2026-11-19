"""Install the exact Windows release ZIP and exercise its remote client commands."""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
from shutil import copyfile
import platform
import secrets
import subprocess
import tempfile
import threading

from windows_release_cli import ASSET, receipt_identity, sha256, verify_zip


def run(command, **kwargs):
    return subprocess.run(command, check=True, capture_output=True, text=True, timeout=60, **kwargs).stdout.strip()


def powershell(*args, **kwargs):
    return run(["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", *args], **kwargs)


def exercise_auth_and_query(executable, root):
    key = "sk-aiv-" + secrets.token_hex(16)
    sample = {"items": [{"traceId": "trace-ci-windows"}]}
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def reply(self, expected_path, data, expected_body=None):
            if self.headers.get("Authorization") != "Bearer " + key:
                self.send_error(401)
                return
            if self.path != expected_path:
                self.send_error(404)
                return
            if expected_body:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if any(body.get(name) != value for name, value in expected_body.items()):
                    self.send_error(400)
                    return
            calls.append((self.command, self.path))
            payload = json.dumps({"code": 0, "data": data}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            self.reply("/openapi/v1/spaces/ci-space/agents", [{"agentProduct": "ci-product"}])

        def do_POST(self):
            self.reply("/openapi/v1/spaces/ci-space/agents/ci-product/traces/list", sample, {"limit": 1})

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            endpoint = f"http://127.0.0.1:{server.server_port}"
            env = {name: val for name, val in os.environ.items() if not name.startswith("AIVISION_")}
            config_dir = root / "Private CLI profile"
            env.update(AIVISION_CONFIG_DIR=str(config_dir), NO_PROXY="127.0.0.1")
            result = run(
                [str(executable), "auth", "login", "--endpoint", endpoint, "--space", "ci-space",
                 "--agent-product", "ci-product"],
                env={**env, "AIVISION_SPACE_KEY": key}, cwd=root,
            )
            if "SpaceKey verified" not in result or key in result:
                raise ValueError("Windows client did not verify and mask the synthetic SpaceKey")
            if (config_dir / "env.sh").exists():
                raise ValueError("Windows client must keep profiles without writing Unix shell configuration")
            # The second process can only use the saved context.
            result = run(
                [str(executable), "query", "trace", "list", "--format", "json", "--limit", "1"],
                env=env, cwd=root,
            )
            if json.loads(result) != sample or [method for method, _ in calls] != ["GET", "POST"]:
                raise ValueError("Windows client did not read the expected synthetic Trace")
        finally:
            server.shutdown()
            thread.join()


def qualify(archive, installer, *, version):
    if platform.system() != "Windows" or platform.machine().upper() not in {"AMD64", "X86_64"}:
        raise ValueError("Windows ZIP installation qualification requires a Windows x64 runner")
    verify_zip(archive)
    with tempfile.TemporaryDirectory(prefix="AIV Windows installation ") as tmp:
        root = Path(tmp)
        assets = root / "Verified assets"
        install_dir = root / "Client path with spaces"
        assets.mkdir()
        actual = archive.resolve()
        expected = sha256(actual)
        # Keep the published ZIP at its exact name in the copied release directory.
        copyfile(actual, assets / ASSET)
        checksums = assets / "SHA256SUMS"
        previous_user_path = powershell("-Command", "[Environment]::GetEnvironmentVariable('Path', 'User')")
        try:
            command = ("-File", str(installer.resolve()), "-AssetDirectory", str(assets),
                       "-InstallDirectory", str(install_dir))
            checksums.write_text(f"{'0' * 64}  {ASSET}\n", encoding="ascii")
            try:
                powershell(*command)
            except subprocess.CalledProcessError as exc:
                if "SHA-256 verification failed" not in exc.stderr:
                    raise
            else:
                raise ValueError("Windows installer accepted a bad checksum")
            if install_dir.exists():
                raise ValueError("Windows installer changed its target before verifying the ZIP")
            checksums.write_text(f"{expected}  {ASSET}\n", encoding="ascii")
            installed = powershell(*command)
            if "Installed" not in installed:
                raise ValueError("Windows installer did not report success")
            # A separate shell resolves the executable through the persisted user PATH.
            # CI runner processes do not inherit Windows Explorer's environment broadcast.
            resolved = powershell("-Command", """
                $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
                (Get-Command aiv.exe -CommandType Application -ErrorAction Stop).Source
            """, cwd=root)
            executable = install_dir / "aiv.exe"
            if Path(resolved).resolve() != executable.resolve():
                raise ValueError("a fresh shell did not find the installed Windows client in PATH")
            if run([resolved, "--version"], cwd=root) != f"aiv {version}":
                raise ValueError("installed Windows executable does not match the release version")
            if "Windows client" not in run([resolved, "--help"], cwd=root):
                raise ValueError("Windows executable did not advertise the remote client commands")
            exercise_auth_and_query(executable, root)
        finally:
            powershell("-Command", "[Environment]::SetEnvironmentVariable('Path', $env:AIV_PREVIOUS_TEST_USER_PATH, 'User')",
                       env={**os.environ, "AIV_PREVIOUS_TEST_USER_PATH": previous_user_path})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--installer", required=True, type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--receipt", required=True, type=Path)
    args = parser.parse_args()
    identity = receipt_identity(args.archive, source_sha=os.environ["GITHUB_SHA"],
                                build_id=os.environ["GITHUB_RUN_ID"] + "." + os.environ["GITHUB_RUN_ATTEMPT"],
                                product_version=args.version)
    qualify(args.archive, args.installer, version=args.version)
    if sha256(args.archive) != identity["archive"]["sha256"]:
        raise ValueError("Windows ZIP changed during qualification")
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(identity, sort_keys=True) + "\n", encoding="utf-8")
    print("Windows x64 install, PATH, version, saved SpaceKey, and Trace query passed.")


if __name__ == "__main__":
    main()
