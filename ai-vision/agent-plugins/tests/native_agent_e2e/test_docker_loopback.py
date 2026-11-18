import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from docker_loopback import docker_loopback


class DockerLoopbackTests(unittest.TestCase):
    def test_native_linux_needs_no_ssh(self):
        with patch.dict(os.environ, {}, clear=True), patch(
            "docker_loopback.subprocess.run"
        ) as run:
            with docker_loopback("http://127.0.0.1:123/v1") as urls:
                self.assertEqual(urls, ("http://127.0.0.1:123/v1",))
        run.assert_not_called()

    def test_vm_loopback_keeps_paths_and_closes_on_driver_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "ssh.conf"
            config.touch()
            with patch.dict(
                os.environ, {"AIVISION_E2E_DOCKER_SSH_CONFIG": str(config)}
            ), patch(
                "docker_loopback.subprocess.run",
                side_effect=[
                    SimpleNamespace(stdout=""),
                    SimpleNamespace(stdout="321\n"),
                    SimpleNamespace(stdout="322\n"),
                    SimpleNamespace(stdout=""),
                ],
            ) as run:
                with self.assertRaisesRegex(RuntimeError, "driver failed"):
                    with docker_loopback(
                        "http://127.0.0.1:123/v1", "http://localhost:124"
                    ) as urls:
                        self.assertEqual(
                            urls, ("http://127.0.0.1:321/v1", "http://127.0.0.1:322")
                        )
                        raise RuntimeError("driver failed")
                self.assertEqual(
                    run.call_args_list[0].kwargs["stdout"], subprocess.DEVNULL
                )
                self.assertEqual(
                    run.call_args_list[0].kwargs["stderr"], subprocess.DEVNULL
                )
                commands = [call.args[0] for call in run.call_args_list]
                self.assertIn("127.0.0.1:0:127.0.0.1:123", commands[1])
                self.assertEqual(commands[-1][-3:], ["-O", "exit", "colima"])
                self.assertFalse(
                    Path(commands[0][commands[0].index("-S") + 1]).exists()
                )

    def test_rejects_non_loopback_before_starting_ssh(self):
        with tempfile.NamedTemporaryFile() as config:
            with patch.dict(
                os.environ, {"AIVISION_E2E_DOCKER_SSH_CONFIG": config.name}
            ), patch("docker_loopback.subprocess.run") as run:
                with self.assertRaises(ValueError):
                    with docker_loopback("http://external.example:80"):
                        pass
                run.assert_not_called()

    def test_cleanup_failure_preserves_primary_failure_and_interrupt(self):
        for primary in (
            None,
            PermissionError("driver denied"),
            KeyboardInterrupt("cancelled"),
        ):
            with self.subTest(primary=primary), tempfile.NamedTemporaryFile() as config:
                cleanup = subprocess.CalledProcessError(
                    255, ["ssh", "-O", "exit", "colima"]
                )
                with (
                    patch.dict(
                        os.environ, {"AIVISION_E2E_DOCKER_SSH_CONFIG": config.name}
                    ),
                    patch(
                        "docker_loopback.subprocess.run",
                        side_effect=[
                            SimpleNamespace(stdout=""),
                            SimpleNamespace(stdout="321\n"),
                            cleanup,
                        ],
                    ) as run,
                ):
                    expected = (
                        KeyboardInterrupt
                        if isinstance(primary, KeyboardInterrupt)
                        else (
                            ExceptionGroup if primary else subprocess.CalledProcessError
                        )
                    )
                    with self.assertRaises(expected) as caught:
                        with docker_loopback("http://127.0.0.1:123/v1"):
                            if primary is not None:
                                raise primary
                    if isinstance(primary, Exception):
                        self.assertEqual(
                            caught.exception.exceptions, (primary, cleanup)
                        )
                        self.assertIn("driver denied", str(caught.exception))
                        self.assertIn("CalledProcessError", str(caught.exception))
                    elif primary is not None:
                        self.assertIs(caught.exception, primary)
                        self.assertIs(caught.exception.__cause__, cleanup)
                        self.assertIn("CalledProcessError", " ".join(primary.__notes__))
                    else:
                        self.assertIs(caught.exception, cleanup)
                    self.assertEqual(
                        run.call_args.args[0][-3:], ["-O", "exit", "colima"]
                    )
