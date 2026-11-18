"""Daemon-side ownership must not be inferred from a forwarded host socket."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import docker_platform as platform


class DockerSocketTests(unittest.TestCase):
    def test_vm_socket_ownership_is_probed_on_daemon(self):
        for system, endpoint, source in (
            ("Darwin", "unix:///Users/fixture/.colima/owned/docker.sock", "/var/run/docker.sock"),
            ("Darwin", "unix:///Users/fixture/.orbstack/run/docker.sock", "/var/run/docker.sock"),
            ("Linux", "unix:///run/user/1001/docker.sock", "/run/user/1001/docker.sock"),
        ):
            with self.subTest(system=system, endpoint=endpoint), \
                    patch.object(platform.platform, "system", return_value=system), \
                    patch.object(platform.subprocess, "run", side_effect=lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "991\n" if "run" in argv else "", "")) as run, \
                    patch.object(Path, "stat", side_effect=AssertionError("host stat is not daemon ownership")):
                self.assertEqual(platform.daemon_socket(endpoint), (Path(source), 991))
                probe = run.call_args_list[0].args[0]
                self.assertEqual(probe[:3], ["docker", "--host", endpoint])
                self.assertIn(f"type=bind,source={source},target=/probe/docker.sock,readonly", probe)
                self.assertIn("--user", probe)
                self.assertIn("--network=none", probe)
                self.assertIn('test -S /probe/docker.sock', probe[-1])
                name = probe[probe.index("--name") + 1]
                self.assertEqual(run.call_args_list[-2].args[0], ["docker", "--host", endpoint, "rm", "--force", name])

    def test_failed_missing_or_invalid_probe_never_falls_back_to_root(self):
        for result in (subprocess.CompletedProcess([], 1, "", "permission denied"),
                       subprocess.CompletedProcess([], 1, "", "missing socket"),
                       subprocess.CompletedProcess([], 0, "", ""),
                       subprocess.CompletedProcess([], 0, "unknown", "")):
            with self.subTest(result=result), patch.object(platform.subprocess, "run", side_effect=lambda argv, **kw: result if "run" in argv else subprocess.CompletedProcess(argv, 0, "", "")) as run:
                with self.assertRaisesRegex(RuntimeError, "daemon Docker socket"):
                    platform.daemon_socket("unix:///var/run/docker.sock")
                self.assertIn("rm", run.call_args_list[-2].args[0])
        with patch.object(platform.subprocess, "run", side_effect=[subprocess.TimeoutExpired("probe", 20), subprocess.CompletedProcess([], 0), subprocess.CompletedProcess([], 0, "", "")]) as run:
            with self.assertRaisesRegex(RuntimeError, "daemon Docker socket"):
                platform.daemon_socket("unix:///var/run/docker.sock")
            self.assertIn("rm", run.call_args_list[-2].args[0])

    def test_invalid_endpoint_rejected_before_running_probe(self):
        for endpoint in ("tcp://remote:2376", "unix://remote/run/docker.sock", "unix:///run/docker.sock?q=1"):
            with self.subTest(endpoint=endpoint), patch.object(platform.subprocess, "run") as run:
                with self.assertRaises(ValueError): platform.daemon_socket(endpoint)
                run.assert_not_called()

    def test_cli_uses_verified_daemon_gid_and_keeps_host_user(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(platform.os, "getuid", return_value=1001):
            command = platform.cli_command(["docker", "version"], repo=Path("/repo"),
                runtime=Path(directory), socket_path=Path("/var/run/docker.sock"), socket_gid=991)
            self.assertEqual(command[command.index("--group-add") + 1], "991")
            self.assertNotEqual(command[command.index("--user") + 1].split(":")[0], "0")
            self.assertIn("DOCKER_HOST=unix:///var/run/docker.sock", command)

    def test_cleanup_requires_readback_and_accepts_auto_removed_container(self):
        prefix, name = ["docker", "--host", "unix:///owned.sock"], "aiv-diagnostics-socket-owned"
        for listing in (subprocess.CompletedProcess([], 0, "", ""),
                        subprocess.CompletedProcess([], 0, "remaining-id", ""),
                        subprocess.CompletedProcess([], 1, "", "daemon unavailable")):
            with self.subTest(listing=listing), patch.object(platform.subprocess, "run", side_effect=[
                    subprocess.CompletedProcess([], 1, "", "No such container"), listing]) as run:
                if listing.returncode or listing.stdout:
                    with self.assertRaisesRegex(RuntimeError, "cleanup"):
                        platform.remove_socket_container(prefix, name, {})
                else:
                    platform.remove_socket_container(prefix, name, {})
                self.assertEqual(run.call_args.args[0][-1], "name=^/" + name + "$")

    def test_smoke_cannot_report_verified_when_client_or_cleanup_fails(self):
        import cli_socket_smoke as smoke
        for outcome in ("passed", "client-failed", "cleanup-failed"):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as directory, \
                    patch.object(smoke.os, "getuid", return_value=1001):
                root = Path(directory)
                result = subprocess.CompletedProcess([], 0, str(platform.os.getuid()) + "\n29.5.2\n", "")
                with patch.object(smoke, "daemon_socket", return_value=(Path("/var/run/docker.sock"), 991)), \
                        patch.object(smoke.subprocess, "run", return_value=result,
                            side_effect=subprocess.CalledProcessError(1, "client") if outcome == "client-failed" else None), \
                        patch.object(smoke, "remove_socket_container",
                            side_effect=RuntimeError("cleanup") if outcome == "cleanup-failed" else None) as cleanup:
                    kwargs = dict(repo=root / "repo", runtime=root / "runtime", endpoint="unix:///owned.sock")
                    if outcome == "passed": self.assertTrue(smoke.connection_smoke(**kwargs)["verified"])
                    else:
                        with self.assertRaises(RuntimeError if outcome == "cleanup-failed" else subprocess.CalledProcessError):
                            smoke.connection_smoke(**kwargs)
                    cleanup.assert_called_once()
