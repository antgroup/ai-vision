"""Buildx cleanup checks against a simulated Docker daemon."""

import contextlib
import io
import subprocess
import unittest
from unittest.mock import patch

from buildx_cleanup import clean_builder, verify_absent


BUILDER = "aiv-diagnostics-12345-1-123-456"
CONTAINER = f"buildx_buildkit_{BUILDER}0"
VOLUME = f"{CONTAINER}_state"


class DockerFixture:
    def __init__(self, scenario):
        self.scenario = scenario
        self.resources = {BUILDER, CONTAINER, VOLUME}
        self.calls = []

    def __call__(self, args, *, capture_output, text, timeout):
        self.calls.append(tuple(args))
        if not (capture_output and text and 0 < timeout <= 120):
            raise AssertionError("Docker commands must be bounded and captured")
        command = tuple(args[1:3])
        if command == ("buildx", "rm"):
            self.resources -= {BUILDER, CONTAINER}
            if self.scenario in {"success", "timeout-after-removal"}:
                self.resources.discard(VOLUME)
            if self.scenario == "timeout-after-removal":
                raise subprocess.TimeoutExpired(args, timeout)
            if self.scenario != "success":
                return subprocess.CompletedProcess(args, 1, "", "context deadline exceeded")
        elif command == ("volume", "rm"):
            if args[-1] != VOLUME:
                raise AssertionError("must only remove this job's volume")
            if self.scenario == "residual":
                return subprocess.CompletedProcess(args, 1, "", "still in use")
            self.resources.discard(VOLUME)
        elif command in {("buildx", "ls"), ("container", "ls"), ("volume", "ls")}:
            resource = dict(zip(
                (("buildx", "ls"), ("container", "ls"), ("volume", "ls")),
                (BUILDER, CONTAINER, VOLUME),
            ))[command]
            stdout = "unrelated-job\n" + (resource + "\n" if resource in self.resources else "")
            return subprocess.CompletedProcess(args, 0, stdout, "")
        else:
            raise AssertionError(f"unexpected Docker command: {args}")
        return subprocess.CompletedProcess(args, 0, "", "")


class BuildxCleanupTests(unittest.TestCase):
    def run_cleanup(self, fixture):
        with patch("buildx_cleanup.subprocess.run", side_effect=fixture), contextlib.redirect_stderr(io.StringIO()):
            clean_builder(BUILDER)

    def test_success_rechecks_builder_container_and_volume(self):
        fixture = DockerFixture("success")
        self.run_cleanup(fixture)
        self.assertFalse(fixture.resources)
        self.assertEqual([call[1:3] for call in fixture.calls],
                         [("buildx", "rm"), ("buildx", "ls"), ("container", "ls"), ("volume", "ls")])

    def test_timeout_after_removal_requires_no_second_teardown(self):
        fixture = DockerFixture("timeout-after-removal")
        self.run_cleanup(fixture)
        self.assertFalse(fixture.resources)
        self.assertEqual([call[1:3] for call in fixture.calls].count(("buildx", "rm")), 1)

    def test_removes_remaining_owned_volume_without_builder_metadata(self):
        fixture = DockerFixture("volume-only")
        self.run_cleanup(fixture)
        self.assertFalse(fixture.resources)
        self.assertIn(("docker", "volume", "rm", VOLUME), fixture.calls)

    def test_fails_if_owned_volume_is_still_present_after_fallback(self):
        fixture = DockerFixture("residual")
        with self.assertRaisesRegex(RuntimeError, VOLUME):
            self.run_cleanup(fixture)
        self.assertEqual(fixture.resources, {VOLUME})
        self.assertIn(("docker", "volume", "rm", VOLUME), fixture.calls)

    def test_preflight_refuses_existing_name_without_writing(self):
        fixture = DockerFixture("success")
        with patch("buildx_cleanup.subprocess.run", side_effect=fixture):
            with self.assertRaisesRegex(RuntimeError, "already exist"):
                verify_absent(BUILDER)
        self.assertEqual(fixture.resources, {BUILDER, CONTAINER, VOLUME})
        self.assertFalse(any(call[2] == "rm" for call in fixture.calls))
