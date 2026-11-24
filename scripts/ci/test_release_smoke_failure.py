import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class ReleaseSmokeFailureTests(unittest.TestCase):
    def test_reports_only_allowlisted_phases_and_categories(self):
        private = "fixture-private-variable-123"
        with tempfile.TemporaryDirectory() as root:
            output = Path(root)
            output.joinpath("cast.log").write_text(
                "==> start database (container)\n"
                "    waiting for database health…\n"
                " ok database healthy\n"
                "==> start backend (container)\n"
                "    waiting for backend health…\n"
                f'Error: component "backend" not healthy after 200 attempts: {private}\n',
                encoding="utf-8",
            )
            output.joinpath("destroy.log").write_text(
                f"Error: backend did not stop within 60s; container {private} retained\n",
                encoding="utf-8",
            )
            output.joinpath("other.log").write_text("no space left on device", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(Path(__file__).with_name("release_smoke_failure.py")), root],
                check=True, capture_output=True, text=True,
            )
        self.assertNotIn(private, result.stdout)
        self.assertEqual(json.loads(result.stdout), {
            "release_smoke_failure": {
                "cast": {
                    "last_observed_component": "backend",
                    "last_observed_deploy_phase": "health",
                    "observations": ["health_probe_exhausted"],
                },
                "destroy": {
                    "last_observed_component": None,
                    "last_observed_deploy_phase": None,
                    "observations": ["graceful_stop_failed"],
                },
            },
        })
