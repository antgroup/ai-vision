#!/usr/bin/env python3
"""Unit tests for the native hook launcher's Codex worker recovery.

Covers the review points on MR !17:

1. session ids must be resolved with the native key precedence, and recovery
   must be skipped when no valid session id is available;
2. the launcher's stdin read must not block past the native stdin budget;
3. recovery must still work when `state_identity.json` has not been written yet;
4. the stdin wait must not leave a blocked reader that aborts shutdown.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
HOST_RUNTIME = SCRIPTS_DIR / "hook_runtime.py"


def load_host_runtime():
    spec = importlib.util.spec_from_file_location("aivision_hook_runtime", HOST_RUNTIME)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HOST = load_host_runtime()

# Directory names cdxhooks actually produced on macOS; the session id is the
# leading UUID of the matching rollout transcript.
OBSERVED_STATE_DIRS = {
    "01a09f3e-7e01-7b20-9a39-dbb00306d350":
        "01a09f3e-7e01-7b20-9a39-dbb00306d350-7af42536485fccdb",
    "01a0a824-4151-7981-88a1-cef3d714605b":
        "01a0a824-4151-7981-88a1-cef3d714605b-c4e26d79f7a968a7",
    "01a0a90b-f6eb-78b1-8a1b-5ef87c3a272d":
        "01a0a90b-f6eb-78b1-8a1b-5ef87c3a272d-2b85359817b02073",
}


class DebugLogger:
    """`initLogger` writes to stderr; keep the test output clean."""

    def info(self, *_args, **_kwargs):
        return None


class SessionStateKeyTest(unittest.TestCase):
    def test_matches_state_directories_created_by_cdxhooks(self):
        for session_id, directory in OBSERVED_STATE_DIRS.items():
            self.assertEqual(HOST.session_state_key(session_id), directory)

    def test_blank_session_id_has_no_key(self):
        self.assertIsNone(HOST.session_state_key(""))
        self.assertIsNone(HOST.session_state_key("   "))
        self.assertIsNone(HOST.session_state_key(None))


class ExtractSessionIdTest(unittest.TestCase):
    def test_precedence_matches_native_implementation(self):
        payload = {
            "sessionId": "camel",
            "session_id": "snake",
            "session": {"id": "nested"},
        }
        self.assertEqual(HOST.extract_session_id(payload), "camel")
        self.assertEqual(HOST.extract_session_id({"session_id": "snake"}), "snake")
        self.assertEqual(HOST.extract_session_id({"session": {"id": "nested"}}), "nested")

    def test_missing_or_blank_session_id_yields_empty(self):
        for payload in ({}, {"session_id": ""}, {"session": {}}, {"session": None}, None, [], "x"):
            self.assertEqual(HOST.extract_session_id(payload), "")


class StdinShim:
    """stdin stand-in exposing a real descriptor to the launcher."""

    def __init__(self, fd):
        self._fd = fd

    def fileno(self):
        return self._fd


class StdinTimeoutTest(unittest.TestCase):
    def _pipe_with_open_writer(self, payload=b'{"sessionId":"sess-1"}'):
        read_fd, write_fd = os.pipe()
        self.addCleanup(os.close, write_fd)
        self.addCleanup(os.close, read_fd)
        os.write(write_fd, payload)  # deliberately never closed
        return read_fd

    def test_read_returns_within_budget_when_writer_stays_open(self):
        read_fd = self._pipe_with_open_writer()
        with mock.patch.object(sys, "stdin", StdinShim(read_fd)):
            started = time.monotonic()
            payload = HOST.read_stdin_with_timeout(200)
            elapsed = time.monotonic() - started

        self.assertLess(elapsed, 2.0, "stdin read must not block past its budget")
        self.assertEqual(payload, b'{"sessionId":"sess-1"}')

    def test_process_exits_cleanly_when_writer_stays_open(self):
        # A reader left blocked in sys.stdin.buffer.read() holds the buffered
        # reader's lock and aborts interpreter shutdown with
        # `_enter_buffered_busy` (SIGABRT, exit code -6).
        script = (
            "import importlib.util\n"
            f"spec = importlib.util.spec_from_file_location('h', {str(HOST_RUNTIME)!r})\n"
            "module = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(module)\n"
            "module.read_stdin_with_timeout(200)\n"
        )
        read_fd = self._pipe_with_open_writer()
        completed = subprocess.run(
            [sys.executable, "-c", script],
            stdin=read_fd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
        self.assertEqual(completed.returncode, 0, "launcher must not abort on shutdown")


class RecoverWorkerTest(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.state_root = Path(self._temp.name)
        self.env = {"CDXHOOKS_STATE_DIR": str(self.state_root)}
        self.addCleanup(self._temp.cleanup)

    def _write_marker(self, session_id, identity=True, status="starting", token="tok-1"):
        session_dir = self.state_root / HOST.session_state_key(session_id)
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "worker.starting.json").write_text(
            json.dumps({"status": status, "token": token}), encoding="utf-8")
        if identity:
            (session_dir / "state_identity.json").write_text(
                json.dumps({"session_id": session_id}), encoding="utf-8")
        return session_dir

    def test_recovers_when_identity_file_is_not_written_yet(self):
        # Review point 3: the first worker may die before writing its identity.
        self._write_marker("sess-alpha", identity=False)
        with mock.patch.object(HOST.subprocess, "Popen") as popen:
            HOST.recover_latest_detached_worker("/bin/echo", self.env, "sess-alpha")
        self.assertEqual(popen.call_count, 1)
        self.assertEqual(popen.call_args.kwargs["env"]["CDXHOOKS_WORKER_SESSION_ID"], "sess-alpha")

    def test_skips_recovery_without_a_valid_session_id(self):
        # Review point 1: never fall back to the newest marker.
        self._write_marker("sess-alpha")
        with mock.patch.object(HOST.subprocess, "Popen") as popen:
            HOST.recover_latest_detached_worker("/bin/echo", self.env, None)
            HOST.recover_latest_detached_worker("/bin/echo", self.env, "")
        self.assertEqual(popen.call_count, 0)

    def test_does_not_touch_another_sessions_marker(self):
        self._write_marker("sess-other")
        with mock.patch.object(HOST.subprocess, "Popen") as popen:
            HOST.recover_latest_detached_worker("/bin/echo", self.env, "sess-alpha")
        self.assertEqual(popen.call_count, 0)

    def test_ignores_markers_that_are_not_starting(self):
        self._write_marker("sess-alpha", status="claimed")
        with mock.patch.object(HOST.subprocess, "Popen") as popen:
            HOST.recover_latest_detached_worker("/bin/echo", self.env, "sess-alpha")
        self.assertEqual(popen.call_count, 0)


if __name__ == "__main__":
    unittest.main()
