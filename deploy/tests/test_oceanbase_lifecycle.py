"""Exercise the actual signal/timeout wrapper with isolated process fixtures."""
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "bootstrap" / "oceanbase.sh"


@unittest.skipUnless(sys.platform == "linux" and all(shutil.which(cmd) for cmd in ("setsid", "timeout", "pgrep")),
                     "requires Linux lifecycle tools and /proc")
class OceanBaseLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "boot").mkdir()
        (self.root / "boot/env.sh").touch()
        (self.root / ".obd/cluster/obcluster").mkdir(parents=True)
        (self.root / ".obd/cluster/obcluster/config.yaml").touch()
        self.write("sshd", "#!/bin/sh\nexit 0\n")
        self.write("boot/start.sh", """#!/bin/sh
trap 'echo boot-stopped >> "$FIXTURE/events"; exit' TERM
printf '%s' "${OB_SERVER_IP-}" > "$FIXTURE/server-ip"
touch "$FIXTURE/.obd/cluster/obcluster/config.yaml"
echo boot-started >> "$FIXTURE/events"
while :; do sleep 0.1; done
""")
        self.write("obd", """#!/bin/sh
echo obd-stop >> "$FIXTURE/events"
grep -q boot-stopped "$FIXTURE/events" || exit 70
case "$FIXTURE_STOP" in
  fail) exit 7;;
  hang) trap '' TERM; exec sleep 60;;
  timeout) exec "$FIXTURE_PYTHON" -c 'import signal, time; signal.signal(signal.SIGTERM, signal.SIG_DFL); time.sleep(60)';;
esac
touch "$FIXTURE/obd-stopped"
exit 0
""")
        script = SCRIPT.read_text().replace("/usr/sbin/sshd", str(self.root / "sshd"))
        script = script.replace("/usr/bin/obd", str(self.root / "obd")).replace("/root", str(self.root))
        # Scale only OBD's deadline; retain real signal/process-group handling.
        script = script.replace("--kill-after=5s 100s", "--kill-after=0.2s 0.2s")
        self.write("lifecycle.sh", script)

    def write(self, name, contents):
        path = self.root / name
        path.write_text(contents)
        path.chmod(0o700)
        return path

    def stop(self, outcome, server_ip=None, before_signal=None):
        env = dict(os.environ, FIXTURE=str(self.root), FIXTURE_STOP=outcome, MODE="mini",
                   FIXTURE_PYTHON=sys.executable,
                   PATH=str(self.root) + os.pathsep + os.environ["PATH"])
        env.pop("OB_SERVER_IP", None)
        if server_ip is not None:
            env["OB_SERVER_IP"] = server_ip
        process = subprocess.Popen(["/bin/sh", str(self.root / "lifecycle.sh")],
                                   env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        self.addCleanup(lambda: self.cleanup_process(process))
        deadline = time.monotonic() + 3
        while not (self.root / "events").exists():
            if process.poll() is not None or time.monotonic() > deadline:
                self.fail("bootstrap fixture did not start")
            time.sleep(0.01)
        self.wait_for(lambda: int(next(line.split()[1] for line in
                      Path(f"/proc/{process.pid}/status").read_text().splitlines()
                      if line.startswith("SigCgt:")), 16) & (1 << (signal.SIGTERM - 1)),
                      "lifecycle wrapper did not install its TERM handler")
        if before_signal is not None:
            before_signal()
        process.send_signal(signal.SIGTERM)
        output = process.communicate(timeout=8)[0].decode()
        return process.returncode, output

    @staticmethod
    def cleanup_process(process):
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()

    def test_term_stops_boot_before_database_and_reports_success(self):
        code, output = self.stop("ok")
        self.assertEqual(code, 0, output)
        self.assertEqual((self.root / "events").read_text().splitlines(),
                         ["boot-started", "boot-stopped", "obd-stop"])
        self.assertIn("cluster stopped", output)

    def test_shutdown_verifies_after_running_script_path_is_removed(self):
        observer = self.observer(0.1)
        code, output = self.stop("ok", before_signal=(self.root / "lifecycle.sh").unlink)
        self.assertEqual(code, 0, output)
        self.assertEqual(observer.wait(timeout=1), 0)
        self.assertIn("cluster stopped", output)

    def test_verifier_does_not_require_inherited_file_descriptors(self):
        timeout = shutil.which("timeout")
        self.write("timeout", f'#!/bin/sh\nexec 9<&-\nexec "{timeout}" "$@"\n')
        observer = self.observer(0.1)
        code, output = self.stop("ok")
        self.assertEqual(code, 0, output)
        self.assertEqual(observer.wait(timeout=1), 0)
        self.assertIn("cluster stopped", output)

    def test_shutdown_uses_running_script_after_atomic_path_replacement(self):
        replacement = self.write("replacement.sh", "#!/bin/sh\ntouch \"$FIXTURE/replacement-ran\"\nexit 79\n")
        code, output = self.stop("ok", before_signal=lambda: replacement.replace(self.root / "lifecycle.sh"))
        self.assertEqual(code, 0, output)
        self.assertFalse((self.root / "replacement-ran").exists())
        self.assertEqual((self.root / "events").read_text().splitlines(),
                         ["boot-started", "boot-stopped", "obd-stop"])

    def test_removed_script_still_bounds_verification_without_signalling_observer(self):
        observer = self.observer()
        started = time.monotonic()
        code, output = self.stop("ok", before_signal=(self.root / "lifecycle.sh").unlink)
        self.assertEqual(code, 137, output)
        self.assertGreaterEqual(time.monotonic() - started, 5)
        self.assertLess(time.monotonic() - started, 7.5)
        self.assertIn(self.process_state(observer.pid), ("R", "S"))
        self.assertIn(str(observer.pid), output)
        self.assertNotIn("cluster stopped", output)

    def test_failed_database_stop_is_not_reported_as_success(self):
        code, output = self.stop("fail")
        self.assertEqual(code, 7, output)
        self.assertNotIn("cluster stopped", output)

    def test_hung_database_stop_is_bounded_and_nonzero(self):
        start = time.monotonic()
        code, output = self.stop("hang")
        self.assertEqual(code, 137, output)
        self.assertLess(time.monotonic() - start, 7)
        self.assertIn("failed or timed out", output)

    def test_database_stop_timeout_status_is_preserved(self):
        code, output = self.stop("timeout")
        self.assertEqual(code, 124, output)
        self.assertNotIn("cluster stopped", output)

    def observer(self, delay=None):
        """A real named process; the test, not the lifecycle, owns its cleanup."""
        process = subprocess.Popen([sys.executable, "-c", """
import ctypes, os, pathlib, sys, time
assert ctypes.CDLL(None).prctl(15, b'observer', 0, 0, 0) == 0
root = pathlib.Path(sys.argv[1])
(root / 'observer-ready').touch()
if sys.argv[2] == 'live':
    time.sleep(60)
else:
    while not (root / 'obd-stopped').exists():
        time.sleep(0.002)
    time.sleep(float(sys.argv[2]))
os._exit(0)
""", str(self.root), "live" if delay is None else str(delay)])
        self.addCleanup(self.cleanup_observer, process)
        self.wait_for(lambda: (self.root / "observer-ready").exists(), "observer did not start")
        self.assertIn(self.process_state(process.pid), ("R", "S"))
        return process

    @staticmethod
    def cleanup_observer(process):
        if process.poll() is None:
            process.kill()
        process.wait()

    @staticmethod
    def process_state(pid):
        try:
            # /proc is the independent oracle; never reap a zombie before inspecting it.
            return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
        except FileNotFoundError:
            return None

    def wait_for(self, condition, message):
        deadline = time.monotonic() + 3
        while not condition():
            if time.monotonic() >= deadline:
                self.fail(message)
            time.sleep(0.002)

    def test_observer_can_drain_for_100ms_after_obd_success(self):
        self.check_observer_drain(0.1)

    def test_observer_can_drain_for_500ms_after_obd_success(self):
        self.check_observer_drain(0.5)

    def check_observer_drain(self, delay):
        process = self.observer(delay)
        code, output = self.stop("ok")
        self.assertEqual(code, 0, output)
        self.assertEqual(process.wait(timeout=1), 0)
        self.assertIn(f"{process.pid}:", output)
        self.assertIn(":live", output)
        self.assertIn("cluster stopped", output)

    def test_unreaped_zombie_is_already_dead(self):
        process = self.observer(0)
        (self.root / "obd-stopped").touch()
        self.wait_for(lambda: self.process_state(process.pid) == "Z", "observer did not become a zombie")
        code, output = self.stop("ok")
        self.assertEqual(code, 0, output)
        self.assertEqual(process.wait(), 0)

    def test_live_observer_past_five_seconds_fails_without_signalling_it(self):
        process = self.observer()
        started = time.monotonic()
        code, output = self.stop("ok")
        self.assertEqual(code, 137, output)
        self.assertGreaterEqual(time.monotonic() - started, 5)
        self.assertLess(time.monotonic() - started, 7.5)
        self.assertIn(self.process_state(process.pid), ("R", "S"))
        self.assertIn(str(process.pid), output)
        self.assertIn(":live", output)

    def test_pgrep_failure_with_live_observer_fails_closed(self):
        process = self.observer()
        self.write("pgrep", "#!/bin/sh\nexit 2\n")
        code, output = self.stop("ok")
        self.assertNotEqual(code, 0, output)
        self.assertIn(self.process_state(process.pid), ("R", "S"))
        self.assertIn("pgrep", output)
        self.assertNotIn("cluster stopped", output)

    def test_hung_query_is_bounded_without_signalling_observer(self):
        process = self.observer()
        self.write("pgrep", "#!/bin/sh\nexec sleep 60\n")
        started = time.monotonic()
        code, output = self.stop("ok")
        self.assertEqual(code, 137, output)
        self.assertLess(time.monotonic() - started, 7.5)
        self.assertIn(self.process_state(process.pid), ("R", "S"))
        self.assertNotIn("cluster stopped", output)

    def test_unreadable_comm_of_present_observer_fails_closed(self):
        self.check_proc_failure("comm", "exit 1")

    def test_empty_comm_of_present_observer_fails_closed(self):
        self.check_proc_failure("comm", "exit 0")

    def test_unreadable_status_of_present_observer_fails_closed(self):
        self.check_proc_failure("status", "exit 1")

    def test_missing_state_of_present_observer_fails_closed(self):
        self.check_proc_failure("status", "echo 'Name: observer'")

    def test_unknown_state_of_present_observer_fails_closed(self):
        self.check_proc_failure("status", "echo 'State: Q (unknown)'")

    def check_proc_failure(self, filename, fault):
        process = self.observer()
        # Inject only the /proc read boundary. The actual PID remains independently live.
        self.write("cat", f'''#!/bin/sh
if [ "$1" = /proc/{process.pid}/{filename} ]; then {fault}; else exec {shutil.which("cat")} "$@"; fi
''')
        code, output = self.stop("ok")
        self.assertNotEqual(code, 0, output)
        self.assertIn(self.process_state(process.pid), ("R", "S"))
        self.assertIn(str(process.pid), output)
        self.assertNotIn("cluster stopped", output)

    def test_process_disappearing_between_query_and_read_is_safe(self):
        process = self.observer()
        self.write("pgrep", f'''#!/bin/sh
if [ ! -f "$FIXTURE/queried" ]; then
  pids=$({shutil.which("pgrep")} "$@") || exit $?
  touch "$FIXTURE/queried"
  while [ ! -f "$FIXTURE/reaped" ]; do sleep 0.002; done
  printf '%s\\n' "$pids"
else
  exec {shutil.which("pgrep")} "$@"
fi
''')
        errors = []

        def reap_after_query():
            try:
                self.wait_for(lambda: (self.root / "queried").exists(), "observer was not queried")
                process.terminate()
                process.wait(timeout=1)
                self.assertIsNone(self.process_state(process.pid))
            except Exception as error:
                errors.append(error)
            finally:
                (self.root / "reaped").touch()

        reaper = threading.Thread(target=reap_after_query)
        reaper.start()
        self.addCleanup(reaper.join)
        code, output = self.stop("ok")
        reaper.join(timeout=1)
        self.assertFalse(reaper.is_alive())
        self.assertEqual(errors, [])
        self.assertIsNone(self.process_state(process.pid))
        self.assertEqual(code, 0, output)

    def test_internal_verifier_never_reenters_bootstrap(self):
        result = subprocess.run(["/bin/sh", str(self.root / "lifecycle.sh"), "__verify_observer_exit"],
                                env=dict(os.environ, MODE="SLIM"), capture_output=True, timeout=2)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertFalse((self.root / "events").exists())

    def test_new_cluster_uses_network_independent_address(self):
        (self.root / ".obd/cluster/obcluster/config.yaml").unlink()
        code, output = self.stop("ok")
        self.assertEqual(code, 0, output)
        self.assertEqual((self.root / "server-ip").read_text(), "127.0.0.1")

    def test_existing_cluster_does_not_receive_a_new_address(self):
        code, output = self.stop("ok")
        self.assertEqual(code, 0, output)
        self.assertEqual((self.root / "server-ip").read_text(), "")

    def test_explicit_new_cluster_address_is_preserved(self):
        (self.root / ".obd/cluster/obcluster/config.yaml").unlink()
        code, output = self.stop("ok", server_ip="172.20.0.2")
        self.assertEqual(code, 0, output)
        self.assertEqual((self.root / "server-ip").read_text(), "172.20.0.2")


if __name__ == "__main__":
    unittest.main()
