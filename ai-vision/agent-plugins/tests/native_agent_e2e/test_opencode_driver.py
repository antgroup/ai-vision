"""Driver boundary checks; the suite separately exercises the official runtime."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
from textwrap import indent
import threading
from types import SimpleNamespace
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
from models import CaseContext
from _process_test_helpers import assert_posix_process_stopped


# HTTPServer.server_bind resolves a hostname before listen. The fixtures only
# use numeric loopback, and reverse DNS can stall on hosted macOS runners.
LOOPBACK_HTTP_SERVER = """\
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import TCPServer

class LoopbackHTTPServer(HTTPServer):
    def server_bind(self):
        TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address
"""


class OpenCodeProcessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name)

    def invoke(self, script, **kwargs):
        from drivers.opencode import _invoke
        return _invoke([sys.executable, "-c", script], dict(os.environ), self.workspace, **kwargs)

    def assert_stopped(self, pid):
        if os.name == "posix":
            assert_posix_process_stopped(pid)
        else:
            listing = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
                                     capture_output=True, text=True, timeout=2)
            self.assertNotIn(f'"{pid}"', listing.stdout)

    def kill_fixture(self, pid):
        if os.name == "posix":
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=2)

    def test_deadline_covers_inherited_stdout_after_parent_exit(self):
        self.check_inherited_stdout(parent_wait=0)

    def test_timeout_kills_signal_ignoring_descendant_after_live_parent_exits(self):
        self.check_inherited_stdout(parent_wait=10)

    def check_inherited_stdout(self, parent_wait):
        # Natural fixture lifetime bounds failures in the pre-fix implementation.
        child = "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(2)"
        script = ("import subprocess,sys,time\n"
                  f"child=subprocess.Popen([sys.executable,'-c',{child!r}])\n"
                  "print(child.pid,flush=True)\n"
                  f"time.sleep({parent_wait})\n")
        started = time.monotonic()
        code, output, timed_out, cancelled = self.invoke(script, timeout=.25)
        pid = int(output.strip())
        try:
            self.assertTrue(timed_out)
            self.assertFalse(cancelled)
            self.assertLess(time.monotonic() - started, 1.5)
            if parent_wait == 0:
                self.assertEqual(code, 0, "retain the exited parent's actual status")
            else:
                self.assertNotEqual(code, 0)
            self.assert_stopped(pid)
        finally:
            self.kill_fixture(pid)

    def test_output_and_nonzero_exit_are_preserved(self):
        script = "import os,sys; os.write(1,b'first\\r\\n\\xe4\\xb8\\xad\\xfftail'); sys.exit(7)"
        self.assertEqual(self.invoke(script, timeout=2), (7, "first\n中\ufffdtail", False, False))

    @unittest.skipUnless(os.name == "posix", "POSIX interrupt contract")
    def test_cancel_sends_interrupt_and_preserves_handler_output_and_status(self):
        script = ("import signal,sys,time\n"
                  "def stop(*args):\n print('interrupted',flush=True); sys.exit(23)\n"
                  "signal.signal(signal.SIGINT,stop)\n"
                  "print('{\"type\":\"step_start\",\"sessionID\":\"synthetic\"}',flush=True)\n"
                  "time.sleep(2)\n")
        code, output, timed_out, cancelled = self.invoke(script, timeout=1, cancel=True)
        self.assertEqual(code, 23)
        self.assertIn("interrupted\n", output)
        self.assertFalse(timed_out)
        self.assertTrue(cancelled)

    @unittest.skipUnless(os.name == "posix", "POSIX graceful interrupt contract")
    def test_cancel_preserves_delayed_handler_flush_and_exit_status(self):
        script = ("import signal,sys,time\n"
                  "def stop(*args):\n time.sleep(.75); print('flush-complete',flush=True); sys.exit(23)\n"
                  "signal.signal(signal.SIGINT,stop)\n"
                  "print('{\"type\":\"step_start\",\"sessionID\":\"synthetic\"}',flush=True)\n"
                  "time.sleep(2)\n")
        code, output, timed_out, cancelled = self.invoke(script, timeout=2, cancel=True)
        self.assertEqual(code, 23)
        self.assertIn("flush-complete\n", output)
        self.assertFalse(timed_out)
        self.assertTrue(cancelled)

    @unittest.skipUnless(os.name == "posix", "POSIX graceful interrupt deadline")
    def test_cancel_handler_grace_cannot_outlive_invocation_deadline(self):
        script = ("import signal,sys,time\n"
                  "def stop(*args):\n time.sleep(.75); print('too-late',flush=True); sys.exit(23)\n"
                  "signal.signal(signal.SIGINT,stop)\n"
                  "print('{\"type\":\"step_start\",\"sessionID\":\"synthetic\"}',flush=True)\n"
                  "time.sleep(2)\n")
        started = time.monotonic()
        code, output, timed_out, cancelled = self.invoke(script, timeout=.25, cancel=True)
        self.assertEqual(code, -signal.SIGKILL)
        self.assertNotIn("too-late", output)
        self.assertTrue(timed_out)
        self.assertTrue(cancelled)
        self.assertLess(time.monotonic() - started, 1.5)

    @unittest.skipUnless(os.name == "posix", "POSIX escaped inherited stdout contract")
    def test_cancel_deadline_stops_reader_when_escaped_child_retains_stdout(self):
        marker = self.workspace / "escaped-child"
        script = ("import signal,subprocess,sys,time\n"
                  "from pathlib import Path\n"
                  "def stop(*args):\n time.sleep(.75); sys.exit(23)\n"
                  "signal.signal(signal.SIGINT,stop)\n"
                  "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(2)'],start_new_session=True)\n"
                  f"Path({str(marker)!r}).write_text(str(child.pid))\n"
                  "print('{\"type\":\"step_start\",\"sessionID\":\"synthetic\"}',flush=True)\n"
                  "time.sleep(2)\n")
        started = time.monotonic()
        try:
            code, output, timed_out, cancelled = self.invoke(script, timeout=.25, cancel=True)
            self.assertEqual(code, -signal.SIGKILL)
            self.assertIn('"sessionID":"synthetic"', output)
            self.assertTrue(timed_out)
            self.assertTrue(cancelled)
            self.assertLess(time.monotonic() - started, 1.5, "reader must not wait for the escaped child's EOF")
            self.assertFalse(any(thread.name == "opencode-stdout" for thread in threading.enumerate()))
            # The escaped child is outside the driver's group. The fixture
            # owns its cleanup, independently of bounded reader shutdown.
            os.kill(int(marker.read_text()), 0)
        finally:
            if marker.exists():
                self.kill_fixture(int(marker.read_text()))

    @unittest.skipUnless(os.name == "posix", "POSIX graceful attached-server teardown")
    def test_attached_server_allows_delayed_termination_flush(self):
        from drivers.opencode import _attached_server
        marker = self.workspace / "server-flush"
        script = LOOPBACK_HTTP_SERVER + (
                  "import signal,sys,time\n"
                  "from pathlib import Path\n"
                  "def stop(*args):\n time.sleep(.75); "
                  f"Path({str(marker)!r}).write_text('flush-complete'); sys.exit(23)\n"
                  "signal.signal(signal.SIGTERM,stop)\n"
                  "class Handler(BaseHTTPRequestHandler):\n"
                  " def log_message(self,*args): pass\n"
                  " def do_GET(self):\n  self.send_response(200); self.end_headers()\n"
                  "LoopbackHTTPServer(('127.0.0.1',int(sys.argv[sys.argv.index('--port')+1])),Handler).serve_forever()\n")
        started = time.monotonic()
        with _attached_server([sys.executable, "-c", script], dict(os.environ), self.workspace, 2, True):
            self.assertFalse(marker.exists())
        self.assertTrue(marker.exists(), "attached server must finish its graceful flush")
        self.assertEqual(marker.read_text(), "flush-complete")
        self.assertLess(time.monotonic() - started, 4)

    @unittest.skipUnless(os.name == "posix", "POSIX permission contract")
    def test_real_cleanup_error_propagates_without_blocking_reader_close(self):
        from drivers import opencode
        marker = self.workspace / "pid"
        script = ("import os,pathlib,time\n"
                  f"pathlib.Path({str(marker)!r}).write_text(str(os.getpid()))\n"
                  "time.sleep(2)\n")
        started = time.monotonic()
        owned = []
        launch = opencode._launch
        def record_launch(*args, **kwargs):
            result = launch(*args, **kwargs)
            owned.append(result[0])
            return result
        try:
            with patch.object(opencode, "_launch", side_effect=record_launch), patch.object(
                    opencode.os, "killpg", side_effect=PermissionError("synthetic cleanup denied")):
                with self.assertRaisesRegex(PermissionError, "cleanup denied"):
                    self.invoke(script, timeout=.25)
            self.assertLess(time.monotonic() - started, 1.5)
            self.assertFalse(any(thread.name == "opencode-stdout" for thread in threading.enumerate()))
        finally:
            if marker.exists():
                self.kill_fixture(int(marker.read_text()))
            for process in owned:
                process.wait(timeout=2)

    def test_windows_launch_barrier_runs_host_only_after_ownership(self):
        self.check_windows_launch(assignment_error=False)

    def test_windows_launch_assignment_error_never_runs_unowned_host(self):
        self.check_windows_launch(assignment_error=True)

    def check_windows_launch(self, assignment_error):
        from drivers import _process
        marker = self.workspace / "windows-host-started"
        script = f"from pathlib import Path; Path({str(marker)!r}).write_text('started')"
        owned = []
        def assign(process):
            owned.append(process)
            self.assertFalse(marker.exists(), "the barrier must precede ownership assignment")
            if assignment_error:
                raise OSError("synthetic Windows assignment failure")
            return object()
        # Exercise the real bootstrap on POSIX; only the unavailable Win32
        # assignment and creation flag are substituted.
        portable_subprocess = SimpleNamespace(Popen=subprocess.Popen, PIPE=subprocess.PIPE,
                                             CREATE_NEW_PROCESS_GROUP=0)
        try:
            with patch.object(_process, "os", SimpleNamespace(name="nt")), patch.object(
                    _process, "subprocess", portable_subprocess), patch.object(_process, "_WindowsJob", side_effect=assign):
                if assignment_error:
                    with self.assertRaisesRegex(OSError, "assignment failure"):
                        _process._launch([sys.executable, "-c", script], stdout=subprocess.PIPE)
                else:
                    process, _ = _process._launch([sys.executable, "-c", script], stdout=subprocess.PIPE)
                    process.communicate(timeout=2)
                    self.assertEqual(process.returncode, 0)
            self.assertEqual(marker.exists(), not assignment_error)
            self.assertTrue(all(process.poll() is not None for process in owned))
            if assignment_error:
                self.assertTrue(all(process.stdout.closed for process in owned))
        finally:
            for process in owned:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=2)
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream and not stream.closed:
                        stream.close()

    def test_windows_job_close_reports_real_cleanup_failure(self):
        from drivers._process import _WindowsJob
        job = _WindowsJob.__new__(_WindowsJob)
        job.handle = 123
        job.kernel = SimpleNamespace(CloseHandle=lambda handle: False)
        with patch("ctypes.WinError", create=True, side_effect=lambda code: OSError("synthetic close denied")), patch(
                "ctypes.get_last_error", create=True, return_value=5):
            with self.assertRaisesRegex(OSError, "close denied"):
                job.close()


HOST = r'''
import json, os, pathlib, sys
if '--version' in sys.argv:
 print('1.18.34'); raise SystemExit(0)
if 'serve' in sys.argv:
''' + indent(LOOPBACK_HTTP_SERVER, ' ') + r'''
 class Handler(BaseHTTPRequestHandler):
  def log_message(self,*args): pass
  def do_GET(self):
   import time
   started=pathlib.Path(os.environ['HOME'])/'title-started'
   title='New session - 2026-10-04T00:00:00.000Z'
   if started.exists() and not (pathlib.Path(os.environ['HOME'])/'never-title').exists() and time.time()-float(started.read_text())>.4:
    title='Generated native title'
   count=len((pathlib.Path(os.environ['HOME'])/'calls.jsonl').read_text().splitlines()) if (pathlib.Path(os.environ['HOME'])/'calls.jsonl').exists() else 1
   value={'healthy':True} if self.path=='/global/health' else {'id':'session-native','title':title}
   if self.path.startswith('/session/session-native/message?'):
    selected=json.loads((pathlib.Path(os.environ['XDG_CONFIG_HOME'])/'opencode/opencode.json').read_text())['model']
    provider,_,model=selected.partition('/')
    value=[{'info':{'id':f'user-{count}','role':'user','sessionID':'session-native','time':{'created':count}}},
     {'info':{'id':f'assistant-{count}','role':'assistant','sessionID':'session-native','parentID':f'user-{count}',
      'finish':'stop','time':{'completed':count+1},'modelID':model,'providerID':provider},
      'parts':[{'type':'text','text':'receipt: official-final-answer'}]}]
    if (pathlib.Path(os.environ['HOME'])/'wrong-completion').exists(): value[-1]['info']['parentID']='wrong-user'
   self.send_response(200);self.end_headers();self.wfile.write(json.dumps(value).encode())
  def do_POST(self):
   assert self.path.startswith('/session/session-native/abort')
   (pathlib.Path(os.environ['HOME'])/'abort-requested').write_text('official abort')
   # Model the server's already-ended request being exported on its next batch.
   # This is a process-lifetime marker, not fabricated acceptance telemetry.
   import threading
   pending=threading.Timer(.35,lambda: (pathlib.Path(os.environ['HOME'])/'post-abort-export').write_text('exported'))
   pending.daemon=True
   pending.start()
   self.send_response(200);self.end_headers();self.wfile.write(b'true')
 LoopbackHTTPServer(('127.0.0.1',int(sys.argv[sys.argv.index('--port')+1])),Handler).serve_forever()
root = pathlib.Path(os.environ['XDG_CONFIG_HOME']) / 'opencode'
config = json.loads((root / 'opencode.json').read_text())
plugin = json.loads((root / 'observ-opencode.json').read_text())
dependency = json.loads((root / 'node_modules/@opencode-ai/plugin/package.json').read_text())
assert dependency['version'] == '1.18.34'
assert (root / 'node_modules/@opencode-ai/plugin/index.js').read_text() == 'official-package-fixture'
record = {'args':sys.argv[1:], 'cwd':os.getcwd(), 'config':config, 'plugin':plugin,
 'dependency_version':dependency['version'],
 'home':os.environ['HOME'], 'profiles': {k:os.environ.get(k) for k in ('USERPROFILE','XDG_DATA_HOME','XDG_CACHE_HOME','XDG_STATE_HOME')},
 'native_home_override':os.environ.get('OPENCODE_TEST_HOME'),
 'external_skills_disabled':os.environ.get('OPENCODE_DISABLE_EXTERNAL_SKILLS'),
 'has_external_secret': 'OPENAI_API_KEY' in os.environ or 'NVIDIA_API_KEY' in os.environ,
 'no_proxy':os.environ.get('NO_PROXY'), 'capture':os.environ.get('OPENCODE_REPLAY_CAPTURE_ENABLED')}
with (pathlib.Path(os.environ['HOME']) / 'calls.jsonl').open('a') as f:
 f.write(json.dumps(record)+'\n')
if '--attach' in sys.argv:
 import time
 (pathlib.Path(os.environ['HOME'])/'title-started').write_text(str(time.time()))
 if 'never title' in sys.argv: (pathlib.Path(os.environ['HOME'])/'never-title').write_text('pending')
 if 'wrong completion' in sys.argv: (pathlib.Path(os.environ['HOME'])/'wrong-completion').write_text('invalid')
if 'cancel native' in sys.argv:
 import time
 print(json.dumps({'type':'step_start','sessionID':'session-native'}),flush=True)
 deadline=time.monotonic()+5
 while not (pathlib.Path(os.environ['HOME'])/'abort-requested').exists() and time.monotonic()<deadline:
  time.sleep(.05)
 if not (pathlib.Path(os.environ['HOME'])/'abort-requested').exists(): raise SystemExit(4)
 print(json.dumps({'type':'error','sessionID':'session-native','error':{'name':'MessageAbortedError'}}),flush=True)
 raise SystemExit(0)
if 'wait forever' in sys.argv:
 import time, subprocess
 subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(5)'])
 print(json.dumps({'type':'step_start','sessionID':'session-native'}), flush=True)
 time.sleep(60)
print(json.dumps({'type':'step_finish','sessionID':'session-native','part':{'reason':'tool-calls' if '--attach' in sys.argv else 'stop'}}), flush=True)
print(json.dumps({'type':'text','sessionID':'session-native','part':{'text':'synthetic output'}}), flush=True)
'''


class OpenCodeDriverTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.repo = self.base / "repo"
        plugin = self.repo / "ai-vision/agent-plugins/opencode/observ-opencode/dist/index.js"
        plugin.parent.mkdir(parents=True)
        plugin.write_text("export default () => ({});\n")
        self.host = self.base / "host.py"
        self.host.write_text(HOST)
        self.seed = self.base / "opencode-config-seed"
        for package in ("plugin", "sdk"):
            root = self.seed / "node_modules/@opencode-ai" / package
            root.mkdir(parents=True)
            (root / "package.json").write_text(json.dumps({"name": "@opencode-ai/" + package, "version": "1.18.34"}))
            (root / "index.js").write_text("official-package-fixture")
        dependencies = {"@opencode-ai/plugin": "1.18.34"}
        (self.seed / "package.json").write_text(json.dumps({"dependencies": dependencies}))
        (self.seed / "package-lock.json").write_text(json.dumps({"lockfileVersion": 3, "packages": {"": {"dependencies": dependencies}}}))
        self.workspace = self.base / "workspace"
        self.workspace.mkdir()

    def context(self, **kwargs):
        values = dict(repo=self.repo, out=self.base / "case", workspace=self.workspace,
                      runtime=(sys.executable, str(self.host)),
                      provider_url="http://127.0.0.1:28001/v1", collector_url="http://127.0.0.1:28002",
                      scenario="tool-loop", prompt="synthetic prompt", model="fake-chat",
                      resource={"ant.agent.product": "OpenCode", "ant.username": "synthetic-user"}, timeout_seconds=5)
        values.update(kwargs)
        return CaseContext(**values)

    def run_driver(self, **kwargs):
        from drivers.opencode import run_case
        return run_case(self.context(**kwargs))

    def test_isolates_credentials_and_routes_main_small_and_all_signals_locally(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "synthetic-not-a-secret", "NVIDIA_API_KEY": "synthetic-not-a-secret",
                                    "OPENCODE_REPLAY_CAPTURE_ENABLED": "true", "OTEL_EXPORTER_OTLP_ENDPOINT": "https://invalid.example"}):
            result = self.run_driver()
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.session_id, "session-native")
        self.assertEqual(result.version, "1.18.34")
        calls = [json.loads(line) for line in (self.base / "case/home/calls.jsonl").read_text().splitlines()]
        self.assertEqual(len(calls), 1)
        actual = calls[0]
        self.assertFalse(actual["has_external_secret"])
        self.assertNotEqual(actual["capture"], "true")
        self.assertEqual(Path(actual["cwd"]).resolve(), self.workspace.resolve())
        self.assertEqual(actual["profiles"]["USERPROFILE"], actual["home"])
        self.assertEqual(actual["dependency_version"], "1.18.34")
        self.assertEqual(actual["native_home_override"], actual["home"])
        self.assertEqual(actual["external_skills_disabled"], "true")
        self.assertEqual(actual["config"]["model"], "native-e2e/fake-chat")
        self.assertEqual(actual["config"]["small_model"], "native-e2e/fake-chat")
        self.assertEqual(actual["config"]["enabled_providers"], ["native-e2e"])
        self.assertEqual(actual["config"]["provider"]["native-e2e"]["options"]["baseURL"], "http://127.0.0.1:28001/v1")
        self.assertEqual(len(actual["config"]["plugin"]), 1)
        for signal, config_name in (("traces", "tracesConfig"), ("logs", "eventsConfig"), ("metrics", "metricsConfig")):
            self.assertEqual(actual["plugin"][config_name]["endpoint"], "http://127.0.0.1:28002/v1/" + signal)
            self.assertTrue(actual["plugin"][config_name]["enabled"])
        self.assertEqual(actual["plugin"]["otelExporter"]["metricsTemporalityPreference"], "delta")
        self.assertIn("127.0.0.1", actual["no_proxy"])

    def test_dependency_seed_is_copied_without_mutating_installed_packages(self):
        result = self.run_driver()
        self.assertEqual(result.exit_code, 0)
        copy = self.base / "case/home/.config/opencode/node_modules/@opencode-ai/plugin/index.js"
        copy.write_text("case-local-change")
        self.assertEqual((self.seed / "node_modules/@opencode-ai/plugin/index.js").read_text(), "official-package-fixture")
        for name in ("package.json", "package-lock.json"):
            self.assertEqual((self.base / "case/home/.config/opencode" / name).read_bytes(), (self.seed / name).read_bytes())

    def test_incomplete_or_wrong_version_seed_is_rejected_before_launch(self):
        for name, contents in (("package-lock.json", "{}"),
                               ("node_modules/@opencode-ai/plugin/package.json", '{"version":"1.18.33"}')):
            with self.subTest(name=name):
                path = self.seed / name
                original = path.read_bytes()
                path.write_text(contents)
                try:
                    with self.assertRaisesRegex(ValueError, "dependency seed"):
                        self.run_driver()
                finally:
                    path.write_bytes(original)
                self.assertFalse((self.base / "case/home/calls.jsonl").exists())

    def test_followup_uses_the_actual_first_session(self):
        result = self.run_driver(followup_prompt="followup receipt")
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.session_id, "session-native")
        calls = [json.loads(line) for line in (self.base / "case/home/calls.jsonl").read_text().splitlines()]
        self.assertEqual(len(calls), 2)
        self.assertIn("--session", calls[1]["args"])
        self.assertEqual(calls[1]["args"][calls[1]["args"].index("--session") + 1], "session-native")
        self.assertEqual(result.details["resumed_session_id"], "session-native")
        self.assertEqual(result.details["turns_completed"], 2)

    def test_remote_endpoint_is_rejected_before_launch(self):
        with self.assertRaisesRegex(ValueError, "loopback"):
            self.run_driver(provider_url="https://integrate.api.nvidia.com/v1")
        self.assertFalse((self.base / "case/home/calls.jsonl").exists())

    def test_nvidia_mode_uses_named_provider_through_local_forwarder(self):
        result = self.run_driver(mode="nvidia", model="z-ai/glm-5.3-flash")
        self.assertEqual(result.exit_code, 0)
        calls = [json.loads(line) for line in (self.base / "case/home/calls.jsonl").read_text().splitlines()]
        config = calls[0]["config"]
        self.assertEqual(config["model"], "nvidia/z-ai/glm-5.3-flash")
        self.assertEqual(config["small_model"], config["model"])
        self.assertEqual(config["enabled_providers"], ["nvidia"])
        self.assertEqual(config["provider"]["nvidia"]["options"]["baseURL"], "http://127.0.0.1:28001/v1")
        self.assertEqual(config["provider"]["nvidia"]["npm"], "@ai-sdk/openai-compatible")
        self.assertEqual(result.details["provider_id"], "nvidia")

    def test_nvidia_mode_keeps_official_server_alive_until_title_finishes(self):
        started = time.monotonic()
        result = self.run_driver(mode="nvidia")
        self.assertEqual(result.exit_code, 0)
        self.assertTrue(result.details["title_completed"])
        self.assertTrue(result.details["native_attach"])
        self.assertEqual(result.details["turns_completed"], 1)
        self.assertEqual(result.details["completed_message_ids"], ["assistant-1"])
        self.assertGreaterEqual(time.monotonic() - started, .4)
        calls = [json.loads(line) for line in (self.base / "case/home/calls.jsonl").read_text().splitlines()]
        self.assertIn("--attach", calls[0]["args"])
        self.assertIn("--dir", calls[0]["args"])

    def test_fake_mode_keeps_the_native_server_alive_through_completion_and_export(self):
        result = self.run_driver()
        self.assertEqual(result.exit_code, 0)
        self.assertTrue(result.details["native_attach"])
        self.assertTrue(result.details["title_completed"])
        self.assertEqual(result.details["completed_message_ids"], ["assistant-1"])
        self.assertEqual(result.details["turns_completed"], 1)
        self.assertEqual(result.details["final_answers"][0]["source"], "official-session-api")
        self.assertIn("synthetic output", result.output)
        self.assertIn("receipt: official-final-answer", result.output)

    def test_fake_attach_cannot_accept_completion_for_another_user_message(self):
        result = self.run_driver(prompt="wrong completion", timeout_seconds=1)
        self.assertNotEqual(result.exit_code, 0)
        self.assertTrue(result.details["timed_out"])
        self.assertEqual(result.details["phase"], "background_completion")
        self.assertEqual(result.details["turns_completed"], 0)

    def test_fake_attach_cannot_stop_before_the_native_title_completes(self):
        result = self.run_driver(prompt="never title", timeout_seconds=1)
        self.assertNotEqual(result.exit_code, 0)
        self.assertFalse(result.details["title_completed"])
        self.assertEqual(result.details["phase"], "background_title")

    def test_nvidia_output_is_the_official_completed_assistant_text(self):
        result = self.run_driver(mode='nvidia')
        self.assertEqual(result.output, 'receipt: official-final-answer')
        self.assertIn('synthetic output', result.details['native_output'])
        self.assertEqual(result.details['final_answers'][0], {
            'source': 'official-session-api', 'message_id': 'assistant-1',
            'parent_message_id': 'user-1', 'session_id': 'session-native',
            'model': 'fake-chat', 'provider_id': 'nvidia', 'text': result.output,
        })

    def test_nvidia_title_timeout_is_a_bounded_failure(self):
        started = time.monotonic()
        result = self.run_driver(mode="nvidia", prompt="never title", timeout_seconds=1)
        self.assertNotEqual(result.exit_code, 0)
        self.assertFalse(result.details["title_completed"])
        self.assertTrue(result.details["timed_out"])
        self.assertEqual(result.details["phase"], "background_title")
        self.assertLess(time.monotonic() - started, 5)

    def test_nvidia_attach_completion_is_correlated_to_the_latest_real_user(self):
        result = self.run_driver(mode="nvidia", prompt="wrong completion", timeout_seconds=1)
        self.assertNotEqual(result.exit_code, 0)
        self.assertTrue(result.details["timed_out"])
        self.assertEqual(result.details["phase"], "background_completion")
        self.assertEqual(result.details["turns_completed"], 0)
        self.assertIn("synthetic output", result.details["native_output"])

    def test_unknown_mode_is_rejected_before_launch(self):
        with self.assertRaisesRegex(ValueError, "mode"):
            self.run_driver(mode="invalid")
        self.assertFalse((self.base / "case/home/calls.jsonl").exists())

    def test_timeout_reaps_the_process_and_preserves_partial_session(self):
        started = time.monotonic()
        result = self.run_driver(prompt="wait forever", timeout_seconds=1)
        self.assertNotEqual(result.exit_code, 0)
        self.assertEqual(result.session_id, "session-native")
        self.assertTrue(result.details["timed_out"])
        self.assertLess(time.monotonic() - started, 4, "children must not retain the native stdout pipe")

    def test_cancel_requests_official_abort_before_reaping_server(self):
        result = self.run_driver(scenario="cancel", prompt="cancel native")
        self.assertEqual(result.exit_code, 0)
        self.assertTrue(result.details["cancelled"])
        self.assertTrue(result.details["native_abort"])
        self.assertEqual(result.details["turns_completed"], 0)
        self.assertEqual(result.session_id, "session-native")
        self.assertTrue((self.base / "case/home/abort-requested").exists())
        calls=[json.loads(line) for line in (self.base / "case/home/calls.jsonl").read_text().splitlines()]
        self.assertIn("--attach",calls[0]["args"])

    def test_cancel_retains_the_server_for_pending_post_abort_exports(self):
        result = self.run_driver(scenario="cancel", prompt="cancel native")
        self.assertEqual(result.exit_code, 0)
        self.assertTrue(result.details["native_abort"])
        self.assertEqual(result.details["turns_completed"], 0)
        self.assertTrue((self.base / "case/home/post-abort-export").exists(),
                        "the attached server must survive its post-abort export batch")


if __name__ == "__main__":
    unittest.main()
