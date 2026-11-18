"""The owned Judge must expose a container URL and preserve failure evidence."""

import json
import subprocess
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from docker_judge import (
    DockerJudge,
    create_judge,
    platform_judges,
    _write,
    _build_judge_image,
)


class DockerJudgeTests(unittest.TestCase):
    def test_factory_cleanup_preserves_primary_failure_and_interrupt(self):
        def local():
            return "local"
        for primary in (
            None,
            PermissionError("acceptance denied"),
            KeyboardInterrupt("cancelled"),
        ):
            with self.subTest(primary=primary):
                cleanup = subprocess.CalledProcessError(
                    1, ["docker", "image", "rm", "owned-judge"]
                )
                with (
                    patch(
                        "docker_judge._docker",
                        side_effect=['[{"Image":"owned"}]', cleanup],
                    ) as docker,
                    patch(
                        "docker_judge._build_judge_image", return_value="owned-judge"
                    ),
                ):
                    expected = (
                        KeyboardInterrupt
                        if isinstance(primary, KeyboardInterrupt)
                        else (
                            ExceptionGroup if primary else subprocess.CalledProcessError
                        )
                    )
                    with self.assertRaises(expected) as caught:
                        with platform_judges("owned-agentlens"):
                            create_judge(local)
                            if primary is not None:
                                raise primary
                    if isinstance(primary, Exception):
                        self.assertEqual(
                            caught.exception.exceptions, (primary, cleanup)
                        )
                        self.assertIn("acceptance denied", str(caught.exception))
                        self.assertIn("CalledProcessError", str(caught.exception))
                    elif primary is not None:
                        self.assertIs(caught.exception, primary)
                        self.assertIs(caught.exception.__cause__, cleanup)
                        self.assertIn("CalledProcessError", " ".join(primary.__notes__))
                    else:
                        self.assertIs(caught.exception, cleanup)
                    self.assertEqual(
                        docker.call_args.args, ("image", "rm", "owned-judge")
                    )
                self.assertEqual(create_judge(local), "local")

    def test_build_downloads_matching_wheels_and_installs_without_network(self):
        captured = {}

        def docker(*args, **kwargs):
            if args[:2] == ("image", "inspect"):
                return "amd64"
            if args[0] == "run":
                self.assertIn("none", args)
                return "311"
            if args[0] == "build":
                captured["dockerfile"] = (Path(args[-1]) / "Dockerfile").read_text()
                captured["context"] = Path(args[-1])
            return ""

        with patch("docker_judge._docker", side_effect=docker), patch(
            "docker_judge.subprocess.run"
        ) as download:
            image = _build_judge_image("sha256:owned")
        command = download.call_args.args[0]
        self.assertIn("https://pypi.org/simple", command)
        self.assertNotIn("PIP_INDEX_URL", download.call_args.kwargs["env"])
        self.assertIn("cp311", command)
        self.assertIn("manylinux2014_x86_64", command)
        self.assertIn("langchain-core==1.6.6", command)
        self.assertIn("--no-index", captured["dockerfile"])
        self.assertFalse(captured["context"].exists())
        self.assertTrue(image.startswith("aiv-e2e-judge:"))

    def test_factory_is_scoped_and_local_factory_remains_default(self):
        local = lambda: "local"
        self.assertEqual(create_judge(local), "local")
        with patch(
            "docker_judge._docker", return_value=json.dumps([{"Image": "owned"}])
        ), patch("docker_judge._build_judge_image", return_value="owned-judge"):
            with platform_judges("owned-agentlens"):
                provider = create_judge(local)
                self.assertEqual(provider.platform_container, "owned-agentlens")
        self.assertEqual(create_judge(local), "local")

    def test_failed_preparation_is_not_retried_for_every_case(self):
        with patch(
            "docker_judge._docker", return_value=json.dumps([{"Image": "owned"}])
        ), patch(
            "docker_judge._build_judge_image",
            side_effect=ValueError("unavailable dependency"),
        ) as build:
            with platform_judges("owned-agentlens"):
                with self.assertRaisesRegex(ValueError, "unavailable dependency"):
                    create_judge(lambda: None)
                with self.assertRaisesRegex(RuntimeError, "already failed"):
                    create_judge(lambda: None)
        build.assert_called_once_with("owned")

    def test_owned_network_without_host_alias_or_published_port(self):
        calls = []
        provider = DockerJudge("owned-agentlens", image="owned-judge")

        def docker(*args):
            calls.append(args)
            if args[0] == "inspect":
                return json.dumps(
                    [
                        {
                            "Image": "sha256:owned",
                            "NetworkSettings": {"Networks": {"owned-network": {}}},
                        }
                    ]
                )
            if args[0] == "exec":
                return json.dumps(
                    {"ready": True, "mode": "normal", "requests": [], "errors": []}
                )
            if args[0] == "cp":
                _write(
                    provider.directory / "state.json",
                    {"ready": False, "requests": [{"completed": True}], "errors": []},
                )
            return ""

        with patch("docker_judge._docker", side_effect=docker), patch(
            "docker_judge.os.getuid", return_value=1000, create=True
        ), patch("docker_judge.os.getgid", return_value=1000, create=True):
            with provider:
                self.assertEqual(provider.url, f"http://{provider.name}:8080/v1")
                run = next(row for row in calls if row[0] == "run")
                self.assertEqual(run[run.index("--network") + 1], "owned-network")
                self.assertNotIn("--publish", run)
                self.assertNotIn("--add-host", run)
            self.assertEqual(provider.requests, [{"completed": True}])
        self.assertFalse(provider.directory.exists())
        self.assertEqual(calls[-1], ("rm", "--force", provider.name))

    def test_startup_failure_preserves_error_and_removes_owned_container(self):
        calls = []
        provider = DockerJudge("owned-agentlens", image="owned-judge")

        def docker(*args):
            calls.append(args)
            if args[0] == "inspect":
                return json.dumps(
                    [
                        {
                            "Image": "base",
                            "NetworkSettings": {"Networks": {"owned-network": {}}},
                        }
                    ]
                )
            if args[0] == "cp":
                raise RuntimeError("missing state after startup failure")
            return ""

        with patch("docker_judge._docker", side_effect=docker), patch(
            "docker_judge.os.getuid", return_value=1000, create=True
        ), patch(
            "docker_judge.os.getgid", return_value=1000, create=True
        ), patch.object(
            provider, "_wait", side_effect=ValueError("startup failure")
        ):
            with self.assertRaisesRegex(ValueError, "startup failure"):
                provider.__enter__()
        self.assertEqual(calls[-1], ("rm", "--force", provider.name))
        self.assertFalse(provider.directory.exists())
        self.assertEqual(provider.errors, ["RuntimeError"])

    def test_mode_waits_for_server_acknowledgement(self):
        provider = DockerJudge("owned-agentlens")
        with tempfile.TemporaryDirectory() as directory:
            provider.directory = Path(directory)

            def wait(ready):
                self.assertFalse(ready())
                mode = json.loads((provider.directory / "mode.json").read_text())
                _write(
                    provider.directory / "state.json",
                    {"mode": mode, "requests": [], "errors": []},
                )
                self.assertTrue(ready())

            def control(*args):
                _write(provider.directory / "mode.json", args[-1])
                return ""

            with patch.object(provider, "_wait", side_effect=wait), patch(
                "docker_judge._docker", side_effect=control
            ):
                provider.mode = "http_failure"
            self.assertEqual(provider.mode, "http_failure")
            with self.assertRaises(ValueError):
                provider.mode = "unknown"
