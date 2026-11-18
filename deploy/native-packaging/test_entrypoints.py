"""Exercise native entrypoint dispatch and signal handling with real subprocesses."""
from __future__ import annotations

import os
from pathlib import Path
import shlex
import signal
import subprocess
import tempfile
import time
import unittest


HELPER = r'''#!/bin/bash
set -eu
case "${0##*/}" in
  manage) stage="$1" ;;
  agentlens-migrate)
    if [ "$1" = read-org-id-file ]; then stage=org-read; else stage=migrate; fi ;;
  sleep) stage=retry-sleep ;;
  python) stage=serve ;;
  *) stage=custom ;;
esac
printf '%s\t%s\t%s\t%s\n' "$stage" "$$" "${SIGNOZ_ORG_ID:-}" "$*" >> "$FIXTURE/calls"
if [ "$stage" = "${BLOCK_STAGE:-}" ]; then
  trap 'printf TERM > "$FIXTURE/stopped"; exit 0' TERM
  trap 'printf INT > "$FIXTURE/stopped"; exit 0' INT
  printf '%s' "$$" > "$FIXTURE/ready"
  # Builtins keep readiness independent of a nested shell child's signal policy.
  while :; do :; done
fi
if [ "$stage" = "${FAIL_STAGE:-}" ]; then exit 29; fi
if [ "$stage" = migrate ] && [ "${FAIL_MIGRATIONS:-0}" -gt 0 ]; then
  count=0
  if [ -f "$FIXTURE/attempt" ]; then read -r count < "$FIXTURE/attempt"; fi
  count=$((count + 1))
  printf '%s\n' "$count" > "$FIXTURE/attempt"
  if [ "$count" -le "$FAIL_MIGRATIONS" ]; then exit 31; fi
fi
if [ "$stage" = org-read ]; then printf 'org-123\n'; fi
if [ "$stage" = custom ]; then exit 27; fi
'''


class NativeEntrypointTests(unittest.TestCase):
    def fixture(self, component, **environment):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        source = Path(__file__).with_name(f"native-entrypoint-{component}.sh").read_text()
        entrypoint = root / "entrypoint"
        # Relocate only the copied fixture; production paths remain fixed at /app.
        entrypoint.write_text(source.replace("cd /app\n", f"cd {shlex.quote(str(root))}\n"))
        for name in ("manage", "agentlens-migrate", "sleep", "python", "custom"):
            helper = root / name
            helper.write_text(HELPER)
            helper.chmod(0o755)
        env = {key: value for key, value in os.environ.items() if not key.startswith(("SIGNOZ_", "AGENTLENS_"))}
        env.update(FIXTURE=str(root), PATH=f"{root}:{os.environ['PATH']}", TMPDIR=str(root))
        env.update(environment)
        return root, entrypoint, env

    def start(self, entrypoint, env, *arguments):
        process = subprocess.Popen(["/bin/bash", str(entrypoint), *arguments], env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        self.addCleanup(self.cleanup_process, process, Path(env["FIXTURE"]))
        return process

    @staticmethod
    def cleanup_process(process, root):
        pids = [process.pid]
        if (root / "calls").exists():
            pids += [int(row.split("\t")[1]) for row in (root / "calls").read_text().splitlines()]
        for pid in pids:
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.communicate(timeout=3)

    def wait_for(self, path):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if path.exists() and path.read_text():
                return path.read_text()
            time.sleep(0.01)
        self.fail(f"entrypoint did not reach {path.name}")

    @staticmethod
    def calls(root):
        return [line.split("\t") for line in (root / "calls").read_text().splitlines()]

    def test_startup_forwards_term_and_int_and_reaps_each_active_child(self):
        stages = (("backend", "migrate"), ("backend", "ensure_platform_config"),
                  ("backend", "seed_data"), ("agentlens", "org-read"),
                  ("agentlens", "migrate"), ("agentlens", "retry-sleep"))
        for component, stage in stages:
            for number, name in ((signal.SIGTERM, "TERM"), (signal.SIGINT, "INT")):
                with self.subTest(component=component, stage=stage, signal=name):
                    root, entrypoint, env = self.fixture(
                        component, BLOCK_STAGE=stage, SIGNOZ_ORG_ID_FILE="fixture-org-file",
                        FAIL_MIGRATIONS="1" if stage == "retry-sleep" else "0")
                    process = self.start(entrypoint, env)
                    child = int(self.wait_for(root / "ready"))
                    process.send_signal(number)
                    try:
                        _, stderr = process.communicate(timeout=3)
                    except subprocess.TimeoutExpired:
                        self.fail("entrypoint did not stop its startup child after the signal")
                    self.assertTrue((root / "stopped").exists(),
                                    f"startup child never received the signal: exit={process.returncode}, stderr={stderr!r}")
                    self.assertEqual((root / "stopped").read_text(), name)
                    with self.assertRaises(ProcessLookupError, msg="startup child was not reaped"):
                        os.kill(child, 0)
                    self.assertEqual(process.returncode, 128 + number)
                    self.assertFalse(any(root.glob("tmp.*")), "org-id capture file was not cleaned")

    def test_backend_preserves_boot_order_and_execs_default_or_custom_server(self):
        for custom in (False, True):
            with self.subTest(custom=custom):
                root, entrypoint, env = self.fixture("backend", BE_PORT="9012")
                arguments = [str(root / "custom"), "two words"] if custom else []
                process = self.start(entrypoint, env, *arguments)
                process.communicate(timeout=3)
                calls = self.calls(root)
                self.assertEqual([call[0] for call in calls],
                                 ["migrate", "ensure_platform_config", "seed_data", "custom" if custom else "serve_with_projector"])
                self.assertEqual(calls[0][3], "migrate --noinput")
                self.assertEqual(calls[-1][3], "two words" if custom else "serve_with_projector --address 0.0.0.0:9012")
                self.assertEqual(int(calls[-1][1]), process.pid, "server must replace PID 1")
                self.assertEqual(process.returncode, 27 if custom else 0)

    def test_backend_stops_on_each_bootstrap_failure_with_original_status(self):
        order = ["migrate", "ensure_platform_config", "seed_data"]
        for index, stage in enumerate(order):
            with self.subTest(stage=stage):
                root, entrypoint, env = self.fixture("backend", FAIL_STAGE=stage)
                process = self.start(entrypoint, env)
                process.communicate(timeout=3)
                self.assertEqual(process.returncode, 29)
                self.assertEqual([call[0] for call in self.calls(root)], order[:index + 1])

    def test_agentlens_captures_org_id_retries_and_execs_server(self):
        for custom in (False, True):
            with self.subTest(custom=custom):
                root, entrypoint, env = self.fixture("agentlens", SIGNOZ_ORG_ID_FILE="fixture-org-file",
                                                     FAIL_MIGRATIONS="1", AGENTLENS_PORT="9013")
                process = self.start(entrypoint, env, *([str(root / "custom")] if custom else []))
                process.communicate(timeout=3)
                calls = self.calls(root)
                self.assertEqual([call[0] for call in calls],
                                 ["org-read", "migrate", "retry-sleep", "migrate", "custom" if custom else "serve"])
                self.assertTrue(all(call[2] == "org-123" for call in calls[1:]))
                self.assertEqual(calls[1][3], "-c obschema/alembic.ini upgrade head")
                if not custom:
                    self.assertEqual(calls[-1][3], "-m uvicorn --factory agentlens.main:build_app --host 0.0.0.0 --port 9013")
                self.assertEqual(int(calls[-1][1]), process.pid)
                self.assertEqual(process.returncode, 27 if custom else 0)
                self.assertFalse(any(root.glob("tmp.*")))

    def test_agentlens_preserves_supplied_org_id_and_retry_limit(self):
        root, entrypoint, env = self.fixture("agentlens", SIGNOZ_ORG_ID="existing-org",
                                             SIGNOZ_ORG_ID_FILE="ignored", FAIL_MIGRATIONS="3",
                                             AGENTLENS_MIGRATE_ATTEMPTS="2")
        process = self.start(entrypoint, env)
        process.communicate(timeout=3)
        calls = self.calls(root)
        self.assertEqual(process.returncode, 1)
        self.assertEqual([call[0] for call in calls], ["migrate", "retry-sleep", "migrate"])
        self.assertTrue(all(call[2] == "existing-org" for call in calls))

    def test_agentlens_stops_on_org_id_failure_and_preserves_status(self):
        root, entrypoint, env = self.fixture("agentlens", SIGNOZ_ORG_ID_FILE="fixture-org-file", FAIL_STAGE="org-read")
        process = self.start(entrypoint, env)
        process.communicate(timeout=3)
        self.assertEqual(process.returncode, 29)
        self.assertEqual([call[0] for call in self.calls(root)], ["org-read"])
        self.assertFalse(any(root.glob("tmp.*")))


if __name__ == "__main__":
    unittest.main()
