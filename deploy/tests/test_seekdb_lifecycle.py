"""Exercise the real wrapper's signal forwarding, failures and SQL deadline."""
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "bootstrap/seekdb.sh"


@unittest.skipUnless(all(shutil.which(cmd) for cmd in ("timeout", "flock")), "requires Linux lifecycle tools")
class SeekdbLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / ".aiv-credentials-verified").touch()
        self.write("engine", """#!/usr/bin/env python3
import os, signal, time
from pathlib import Path
mode = os.environ["FIXTURE_MODE"]
root = Path(os.environ["FIXTURE"])
if mode == "drain":
    signal.signal(signal.SIGUSR1, lambda *_: exit(0))
elif mode == "hang":
    signal.signal(signal.SIGUSR1, signal.SIG_IGN)
elif mode == "term-exit":
    signal.signal(signal.SIGUSR1, lambda *_: os.kill(os.getpid(), signal.SIGTERM))
(root / "started").write_text(str(os.getpid()))
if mode == "fail":
    exit(7)
if mode == "killed":
    os.kill(os.getpid(), signal.SIGKILL)
while True:
    time.sleep(0.01)
""")
        self.write("mysql", """#!/bin/sh
trap '' TERM
echo $$ > "$FIXTURE/mysql-pid"
exec sleep 60
""")
        script = SCRIPT.read_text().replace("/var/lib/seekdb", str(self.root))
        script = script.replace("/usr/bin/seekdb", str(self.root / "engine"))
        script = script.replace("/usr/bin/mysql", str(self.root / "mysql"))
        # Retain the real signal handling; scale only the database stop budget.
        script = script.replace('"$shutdown_attempt" -lt 90', '"$shutdown_attempt" -lt 2')
        self.write("lifecycle.sh", script)

    def write(self, name, text):
        path = self.root / name
        path.write_text(text)
        path.chmod(0o700)

    def run_server(self, mode, stop=True):
        env = dict(os.environ, FIXTURE=str(self.root), FIXTURE_MODE=mode,
                   AIV_SEEKDB_PASSWORD="fixture-password")
        process = subprocess.Popen(["/bin/sh", str(self.root / "lifecycle.sh"), "serve"],
                                   env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        self.addCleanup(lambda: self.cleanup_process(process))
        deadline = time.monotonic() + 3
        while not (self.root / "started").exists():
            if process.poll() is not None or time.monotonic() > deadline:
                self.fail("database fixture did not start")
            time.sleep(0.01)
        if stop:
            process.send_signal(signal.SIGTERM)
        output = process.communicate(timeout=6)[0].decode()
        return process.returncode, output

    @staticmethod
    def cleanup_process(process):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()

    def test_term_becomes_database_usr1_and_child_is_waited_for(self):
        code, output = self.run_server("drain")
        self.assertEqual(code, 0, output)
        self.assertIn("child_exit=0 forced=false", output)

    def test_child_terminated_by_term_is_not_reported_as_success(self):
        code, output = self.run_server("term-exit")
        self.assertEqual(code, 143, output)
        self.assertIn("child_exit=143 forced=false", output)

    def test_unexpected_failure_is_preserved(self):
        code, output = self.run_server("fail", stop=False)
        self.assertEqual(code, 7, output)

    def test_unexpected_kill_is_not_reported_as_success(self):
        code, output = self.run_server("killed", stop=False)
        self.assertEqual(code, 137, output)

    def test_database_ignoring_shutdown_is_bounded_and_fails(self):
        code, output = self.run_server("hang")
        self.assertEqual(code, 137, output)
        self.assertIn("forced=true", output)

    def test_hung_health_sql_finishes_before_outer_eight_seconds(self):
        start = time.monotonic()
        result = subprocess.run(["/bin/sh", str(self.root / "lifecycle.sh"), "health"],
                                env=dict(os.environ, FIXTURE=str(self.root), AIV_SEEKDB_PASSWORD="fixture"),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=7)
        self.assertNotEqual(result.returncode, 0)
        self.assertLess(time.monotonic() - start, 7)
        pid = int((self.root / "mysql-pid").read_text())
        status = Path("/proc") / str(pid) / "status"
        # A killed child can briefly remain as a zombie before PID 1 reaps it.
        self.assertTrue(not status.exists() or "\nState:\tZ " in status.read_text())


if __name__ == "__main__":
    unittest.main()
