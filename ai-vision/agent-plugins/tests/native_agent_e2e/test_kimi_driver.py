"""Official CLI boundary contracts for the isolated Kimi Code driver."""
from dataclasses import replace
import importlib
import io
import json
import os
from pathlib import Path
import sys
import signal
import subprocess
import time
import tempfile
import tomllib
import unittest
from unittest.mock import Mock, patch

from models import CaseContext
from _process_test_helpers import assert_posix_process_stopped


class KimiDriverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cli = self.root / "kimi.py"
        self.cli.write_text('''import json, os, signal, subprocess, sys, time, tomllib
from pathlib import Path
if "--version" in sys.argv:
    print("2.1.1"); raise SystemExit(0)
home = Path(os.environ["KIMI_CODE_HOME"])
cfg = tomllib.loads((home / "config.toml").read_text())
assert cfg["telemetry"] is False and cfg["auto_session_title"] is False
assert cfg["providers"]["local"]["base_url"] == "http://127.0.0.1:12345/v1"
sid = sys.argv[sys.argv.index("-r") + 1] if "-r" in sys.argv else "session_native_fixture"
session = home / "sessions/wd_fixture" / sid
agent = session / "agents/main"
agent.mkdir(parents=True, exist_ok=True)
wire = agent / "wire.jsonl"
def append(row):
    with wire.open("a") as f: f.write(json.dumps(row) + "\\n")
if not wire.exists(): append({"type":"metadata","protocol_version":"1.5","created_at":123456})
append({"type":"turn.prompt","input":[{"type":"text","text":sys.argv[-1]}],"turnId":0,"time":123456})
def hook(event):
    payload = {"hook_event_name":event,"session_id":sid,"cwd":os.getcwd(),"client_type":"kimi_code_cli"}
    if event == "Stop": payload["stop_hook_active"] = False
    spec = next(row for row in cfg["hooks"] if row["event"] == event)
    subprocess.run(spec["command"], shell=True, input=json.dumps(payload), text=True, check=True)
def state(reason):
    (session / "state.json").write_text(json.dumps({"id":sid,"cwd":os.getcwd(),"lastTurnReason":reason,"agents":{"main":{"homedir":str(agent),"type":"main"}}}))
if "acp" in sys.argv:
    def receive(): return json.loads(sys.stdin.readline())
    def send(row): print(json.dumps(row), flush=True)
    row = receive(); assert row["method"] == "initialize"
    send({"jsonrpc":"2.0","id":row["id"],"result":{"protocolVersion":1}})
    row = receive(); assert row["method"] == "session/new"
    send({"jsonrpc":"2.0","id":row["id"],"result":{"sessionId":sid}})
    row = receive(); assert row["method"] == "session/prompt"
    prompt_id = row["id"]
    send({"jsonrpc":"2.0","method":"session/update","params":{"sessionId":sid,"update":{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":"actual streamed pending"}}}})
    row = receive(); assert row["method"] == "session/cancel" and "id" not in row
    append({"type":"turn.interrupted","reason":"cancelled","time":123459})
    state("cancelled"); hook("Interrupt")
    send({"jsonrpc":"2.0","id":prompt_id,"result":{"stopReason":"cancelled"}})
    assert sys.stdin.readline() == ""
    raise SystemExit(0)
print(json.dumps({"role":"meta","type":"system.version","version":"2.1.1"}), flush=True)
print(json.dumps({"role":"meta","type":"fixture.environment","home":os.environ["HOME"],"secret_inherited":"NVIDIA_API_KEY" in os.environ,"argv":sys.argv[1:],"content_flags":{k:v for k,v in os.environ.items() if k.startswith("KIMI_LOG_SPANS_") or k.startswith("KIMI_LOG_EVENTS_")}}), flush=True)
append({"type":"turn.end","reason":"completed","time":123458})
state("completed"); hook("Stop")
print(json.dumps({"role":"assistant","content":"receipt: real-fixture"}), flush=True)
print(json.dumps({"role":"meta","type":"session.resume_hint","session_id":sid}), flush=True)
''')
        self.hook = self.root / ("kimihooks.exe" if os.name == "nt" else "kimihooks")
        self.hook.write_text('#!' + sys.executable + '''
import json, os, sys
from pathlib import Path
if "--version" in sys.argv: print("kimihooks 0.4.0"); raise SystemExit(0)
row = json.load(sys.stdin)
Path(os.environ["KIMI_STATE_DIR"]).mkdir(parents=True, exist_ok=True)
with (Path(os.environ["KIMI_STATE_DIR"]) / "actual-hook.jsonl").open("a") as f:
    f.write(json.dumps({"payload":row,"header":os.environ["KIMI_OTLP_HEADERS"],"http_disabled":os.environ.get("KIMI_DISABLE_HTTP"),"mode":os.environ["KIMI_COLLECTION_MODE"]}) + "\\n")
''')
        version = tomllib.loads((Path(__file__).resolve().parents[4] / "ai-vision/agent-plugins/kimi/kimihooks/Cargo.toml").read_text())["package"]["version"]
        self.hook.write_text(self.hook.read_text().replace("kimihooks 0.4.0", "kimihooks " + version))
        self.hook.chmod(0o755)
        # The fixture is Python source, including on Windows; never ask the OS
        # to execute text merely because its discovery filename ends in .exe.
        hook_command = patch("drivers.kimi._hook_command", side_effect=lambda path: [sys.executable, str(path)])
        hook_command.start()
        self.addCleanup(hook_command.stop)
        self.ctx = CaseContext(repo=Path(__file__).resolve().parents[4], out=self.root / "out",
            workspace=self.root / "workspace", runtime=(sys.executable, str(self.cli)),
            provider_url="http://127.0.0.1:12345/v1", collector_url="http://127.0.0.1:12346",
            scenario="tool-loop", prompt="Read receipt.txt", runtime_version="2.1.1",
            headers={"x-space-key":"collector-test"}, resource={"ant.agent.product":"KimiCLI"}, timeout_seconds=10)

    def run_driver(self, ctx=None):
        try:
            module = importlib.import_module("drivers.kimi")
        except ModuleNotFoundError:
            self.fail("Kimi Code native E2E requires an official CLI driver")
        return module.run_case(ctx or self.ctx)

    def test_isolated_cli_calls_real_production_hook_and_preserves_native_evidence(self):
        with patch.dict(os.environ, {"NVIDIA_API_KEY":"personal-secret", "KIMI_CODE_HOME":"/personal", "KIMI_DISABLE_HTTP":"1"}):
            result = self.run_driver()
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.session_id, "session_native_fixture")
        self.assertEqual(result.version, "2.1.1")
        self.assertEqual(result.details["turns_completed"], 1)
        self.assertEqual(result.details["hook_payloads"][0]["hook_event_name"], "Stop")
        self.assertEqual(result.details["native_session_state"]["lastTurnReason"], "completed")
        self.assertEqual(result.details["kimi_wire"][0]["type"], "metadata")
        import hashlib
        self.assertEqual(hashlib.sha256(result.details["kimi_wire_text"].encode()).hexdigest(), result.details["kimi_wire_sha256"])
        dispatch = result.details["hook_dispatches"][0]
        self.assertEqual(json.loads(dispatch["stdin_text"]), result.details["hook_payloads"][0])
        self.assertEqual(hashlib.sha256(dispatch["stdin_text"].encode()).hexdigest(), dispatch["stdin_sha256"])
        self.assertEqual(dispatch["exit_code"], 0)
        env = next(row for row in result.details["cli_events"] if row.get("type") == "fixture.environment")
        self.assertFalse(env["secret_inherited"])
        self.assertEqual(env["home"], str(self.ctx.out / "kimi-home"))
        self.assertIn("--skills-dir", env["argv"])
        self.assertEqual(env["content_flags"], {f"KIMI_LOG_{surface}_{content}": "true" for surface in ("SPANS", "EVENTS") for content in ("USER_PROMPTS", "MODEL_OUTPUTS", "TOOL_DETAILS", "TOOL_OUTPUTS")})
        invoked = json.loads((self.ctx.out / "kimihooks-state/actual-hook.jsonl").read_text())
        self.assertEqual(json.loads(invoked["header"]), self.ctx.headers)
        self.assertIsNone(invoked["http_disabled"])
        self.assertEqual(invoked["mode"], "full")

    def test_multi_turn_uses_actual_resume_id_and_two_native_stops(self):
        result = self.run_driver(replace(self.ctx, scenario="multi-turn", followup_prompt="Read again"))
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.details["session_ids"], [result.session_id, result.session_id])
        self.assertEqual(result.details["turns_completed"], 2)
        self.assertEqual([row["hook_event_name"] for row in result.details["hook_payloads"]], ["Stop", "Stop"])
        rows = [row for row in result.details["cli_events"] if row.get("type") == "fixture.environment"]
        self.assertNotIn("-r", rows[0]["argv"])
        self.assertEqual(rows[1]["argv"][rows[1]["argv"].index("-r") + 1], result.session_id)

    def test_cancel_requires_native_acp_cancellation_and_interrupt(self):
        result = self.run_driver(replace(self.ctx, scenario="cancel", prompt="cancel active stream"))
        self.assertEqual(result.exit_code, 0)
        self.assertTrue(result.details["cancel_requested"])
        self.assertTrue(result.details["cancelled"])
        self.assertEqual(result.details["process_exit_codes"], [0])
        self.assertEqual(result.details["hook_payloads"][0]["hook_event_name"], "Interrupt")
        self.assertEqual(result.details["native_session_state"]["lastTurnReason"], "cancelled")
        self.assertEqual(result.details["cancellation_protocol"], "official-acp")

    def test_version_drift_stops_before_native_task(self):
        self.cli.write_text('print("2.2.0")\n')
        result = self.run_driver()
        self.assertNotEqual(result.exit_code, 0)
        self.assertIsNone(result.session_id)
        self.assertIn("2.1.1", result.details["error"])

    def test_production_hook_version_must_match_current_source_manifest(self):
        self.hook.write_text('#!' + sys.executable + '\nprint("kimihooks 0.3.9")\n')
        result = self.run_driver()
        self.assertNotEqual(result.exit_code, 0)
        self.assertIsNone(result.session_id)
        self.assertIn("kimihooks", result.details["error"])

    def test_acp_end_turn_cannot_be_treated_as_native_cancellation(self):
        self.cli.write_text(self.cli.read_text().replace('"stopReason":"cancelled"', '"stopReason":"end_turn"'))
        result = self.run_driver(replace(self.ctx, scenario="cancel"))
        self.assertNotEqual(result.exit_code, 0)
        self.assertTrue(result.details["cancel_requested"])
        self.assertFalse(result.details["cancelled"])

    def test_missing_production_hook_is_rejected(self):
        self.hook.unlink()
        with self.assertRaisesRegex(ValueError, "kimihooks"):
            self.run_driver()

    def test_failure_does_not_forge_session_or_completion(self):
        self.cli.write_text('import sys\nif "--version" in sys.argv: print("2.1.1")\nelse: raise SystemExit(17)\n')
        result = self.run_driver()
        self.assertEqual(result.exit_code, 17)
        self.assertIsNone(result.session_id)
        self.assertEqual(result.details["turns_completed"], 0)

    def test_resume_hint_cannot_replace_native_state_and_stop(self):
        self.cli.write_text('import json,sys\nif "--version" in sys.argv: print("2.1.1")\nelse: print(json.dumps({"role":"meta","type":"session.resume_hint","session_id":"pretend"}))\n')
        result = self.run_driver()
        self.assertNotEqual(result.exit_code, 0)
        self.assertIsNone(result.session_id)

    def test_windows_launcher_waits_for_containment_before_spawning_host(self):
        from drivers.kimi import _WINDOWS_BOOTSTRAP
        marker = self.root / "host-started"
        process = subprocess.Popen([sys.executable, "-c", _WINDOWS_BOOTSTRAP, sys.executable, "-c",
            "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('started')", str(marker)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            with self.assertRaises(subprocess.TimeoutExpired): process.wait(timeout=.1)
            self.assertFalse(marker.exists())
            stdout, stderr = process.communicate("x", timeout=3)
            self.assertEqual(process.returncode, 0, stdout + stderr)
            self.assertEqual(marker.read_text(), "started")
        finally:
            if process.poll() is None: process.kill(); process.communicate(timeout=2)

    def test_windows_barrier_is_unchanged_by_crlf_text_translation(self):
        from drivers.kimi import _WINDOWS_BOOTSTRAP
        for token, expected in (("\n", 125), ("x", 0)):
            marker = self.root / ("host-crlf" if token == "\n" else "host-byte")
            process = subprocess.Popen([sys.executable, "-c", _WINDOWS_BOOTSTRAP, sys.executable, "-c",
                "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('started')", str(marker)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                # Apply Windows text-mode newline translation even on POSIX.
                sender = io.TextIOWrapper(process.stdin, encoding="utf-8", newline="\r\n")
                sender.write(token)
                sender.flush()
                sender.detach()
                stdout, stderr = process.communicate(timeout=3)
                with self.subTest(token=repr(token)):
                    self.assertEqual(process.returncode, expected, stdout + stderr)
                    self.assertEqual(marker.exists(), expected == 0)
            finally:
                if process.poll() is None: process.kill(); process.communicate(timeout=2)

    def test_timeout_kills_descendants_holding_stdout_and_stderr(self):
        self._assert_inherited_pipes_bounded(parent_wait=10)

    @unittest.skipUnless(os.name == "posix", "POSIX process groups")
    def test_cleanup_signals_owned_group_only_once(self):
        from drivers.kimi import _kill_tree
        process = Mock(pid=12345)
        process._owned_group_terminated = False
        process.poll.return_value = 0
        # The second cleanup must not race Darwin's retiring process group.
        with patch("drivers.kimi.os.killpg", side_effect=[None, PermissionError]) as kill:
            _kill_tree(process, None)
            _kill_tree(process, None)
        kill.assert_called_once_with(process.pid, signal.SIGKILL)

    @unittest.skipUnless(os.name == "posix", "POSIX process groups")
    def test_cleanup_preserves_permission_errors_for_live_groups(self):
        from drivers.kimi import _kill_tree
        process = Mock(pid=12345)
        process._owned_group_terminated = False
        with patch("drivers.kimi.os.killpg", side_effect=PermissionError):
            with self.assertRaises(PermissionError):
                _kill_tree(process, None)
        self.assertFalse(process._owned_group_terminated)

    def test_naturally_exited_parent_cannot_leave_unbounded_pipe_readers(self):
        self._assert_inherited_pipes_bounded(parent_wait=0)

    def _assert_inherited_pipes_bounded(self, parent_wait):
        from drivers.kimi import _invoke
        self.ctx.workspace.mkdir()
        script = self.root / "process-tree.py"
        script.write_text("import subprocess,sys,time\n"
            "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(2)'])\n"
            "print(child.pid, flush=True)\n"
            f"time.sleep({parent_wait})\n")
        started = time.monotonic()
        code, stdout, _, _ = _invoke([sys.executable, str(script)], self.ctx, dict(os.environ), started + .25, self.root)
        elapsed = time.monotonic() - started
        child = int(stdout.strip())
        # Even a failed pre-fix assertion leaves no long-lived test child.
        try:
            self.assertEqual(code, 124)
            self.assertLess(elapsed, 1.5, "inherited pipes outlived the driver deadline")
            if os.name == "posix":
                assert_posix_process_stopped(child)
            else:
                listing = subprocess.run(["tasklist", "/FI", f"PID eq {child}", "/NH", "/FO", "CSV"], capture_output=True, text=True, timeout=2)
                self.assertNotIn(f'"{child}"', listing.stdout)
        finally:
            if os.name == "posix":
                try: os.kill(child, signal.SIGKILL)
                except ProcessLookupError: pass
            else:
                subprocess.run(["taskkill", "/PID", str(child), "/T", "/F"], capture_output=True, timeout=2)

    def test_remote_endpoints_rejected_before_launch(self):
        with self.assertRaises(ValueError):
            self.run_driver(replace(self.ctx, collector_url="https://collector.example"))
        self.assertFalse(self.ctx.out.exists())


if __name__ == "__main__":
    unittest.main()
