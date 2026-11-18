import importlib.util
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / "bootstrap/backend-bootstrap.py"
SPEC = importlib.util.spec_from_file_location("backend_bootstrap", SOURCE)
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)


class BackendBootstrapDeadlineTests(unittest.TestCase):
    def test_native_management_launcher_does_not_require_manage_source(self):
        with patch.object(bootstrap.os, "access", return_value=True), patch.object(
            bootstrap.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "org-id\n")
        ) as run:
            self.assertEqual(bootstrap.command("signoz_integration_status", capture=True), "org-id")
        self.assertEqual(run.call_args.args[0], ["/app/manage", "signoz_integration_status"])

    def test_unresponsive_management_process_is_killed_within_shared_deadline(self):
        real_run = subprocess.run
        started = time.monotonic()
        calls = []

        def run(command, **kwargs):
            calls.append(kwargs["timeout"])
            # Exercise subprocess.run's real timeout/kill/wait behavior.
            return real_run([sys.executable, "-c", "import time; time.sleep(60)"],
                            timeout=kwargs["timeout"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        with patch.object(bootstrap.subprocess, "run", side_effect=run):
            with self.assertRaisesRegex(RuntimeError, "deadline"):
                bootstrap.command("bootstrap_signoz_integration", deadline=started + 0.2)
        self.assertEqual(len(calls), 1)
        self.assertLessEqual(calls[0], 0.2)
        self.assertLess(time.monotonic() - started, 2)

    def test_expired_shared_budget_starts_no_further_command(self):
        with patch.object(bootstrap.subprocess, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "deadline"):
                bootstrap.command("signoz_integration_status", deadline=time.monotonic() - 1)
        run.assert_not_called()

    def test_successful_identity_capture_remains_unchanged(self):
        with patch.object(bootstrap.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "org-id\n")):
            self.assertEqual(bootstrap.command("signoz_integration_status", capture=True), "org-id")


if __name__ == "__main__":
    unittest.main()
