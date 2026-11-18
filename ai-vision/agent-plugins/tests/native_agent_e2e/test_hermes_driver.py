"""Exercise the Hermes CLI boundary with isolated child process fixtures."""
from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

try:
    from .models import CaseContext
    from ._process_test_helpers import assert_posix_process_stopped
except ImportError:
    from models import CaseContext
    from _process_test_helpers import assert_posix_process_stopped


class HermesDriverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cli = self.root / "native_cli.py"
        self.cli.write_text('''import json, os, sys
from pathlib import Path
if "--version" in sys.argv:
    print("Hermes Agent v0.18.2 (2026.7.7.2)")
    raise SystemExit(0)
home = Path(os.environ["HERMES_HOME"])
plugins = json.loads((home / "config.yaml").read_text())["plugins"]["enabled"]
assert plugins in (["observ-hermes"], ["observ-hermes", "hermes-replay"])
if "hermes-replay" in plugins:
    replay = json.loads((home / "hermes-replay.json").read_text())
    assert replay["captureEnabled"] is True
    assert (home / "plugins/hermes-replay/__init__.py").is_file()
    directory = Path(replay["sessionDir"])
    directory.mkdir(exist_ok=True)
    (directory / "session_fixture.jsonl").write_text(json.dumps({"hook": "native-fixture-hook", "rawInput": {"value": 7}}))
print(json.dumps({"argv":sys.argv[1:], "cwd":os.getcwd(), "home":str(home),
                  "secret_inherited":"NVIDIA_API_KEY" in os.environ and os.environ["NVIDIA_API_KEY"] == "inherited-secret",
                  "otlp_header_present":os.environ.get("OTEL_EXPORTER_OTLP_HEADERS") == "x-space-key=collector-test",
                  "plugin":(home / "plugins/observ-hermes/__init__.py").exists()}))
print("session_id: native-session", file=sys.stderr)
if "--usage-file" in sys.argv:
    Path(sys.argv[sys.argv.index("--usage-file") + 1]).write_text(json.dumps({"session_id":"native-session","completed":True}))
''')
        self.ctx = CaseContext(repo=Path(__file__).resolve().parents[4], out=self.root / "out",
            workspace=self.root / "workspace", runtime=(sys.executable, str(self.cli)),
            provider_url="http://127.0.0.1:12345/v1", collector_url="http://127.0.0.1:12346",
            scenario="tool-loop", prompt="Read receipt.txt", headers={"x-space-key": "collector-test"},
            resource={"ant.agent.product": "Hermes"}, timeout_seconds=10)

    def run_driver(self, ctx=None):
        try:
            module = importlib.import_module(".drivers.hermes", __package__) if __package__ else importlib.import_module("drivers.hermes")
        except ModuleNotFoundError:
            self.fail("Hermes native driver must execute the official CLI boundary")
        return module.run_case(ctx or self.ctx)

    def test_cli_loads_production_plugin_in_isolated_home_and_workspace(self):
        with patch.dict(os.environ, {"NVIDIA_API_KEY": "inherited-secret", "HERMES_HOME": "/unrelated"}):
            result = self.run_driver()
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.session_id, "native-session")
        self.assertEqual(result.version, "0.18.2")
        row = json.loads(result.output.splitlines()[0])
        self.assertEqual(Path(row["cwd"]).resolve(), self.ctx.workspace.resolve())
        self.assertEqual(row["home"], str(self.ctx.out / "hermes-home"))
        self.assertFalse(row["secret_inherited"])
        self.assertTrue(row["otlp_header_present"])
        self.assertTrue(row["plugin"])
        self.assertIn("--ignore-rules", row["argv"])
        self.assertNotIn("--safe-mode", row["argv"])
        self.assertNotIn("collector-test", (self.ctx.out / "hermes-home/observ-hermes.json").read_text())

    def test_multi_turn_resumes_actual_returned_session(self):
        from dataclasses import replace
        result = self.run_driver(replace(self.ctx, scenario="multi-turn", followup_prompt="Read again"))
        rows = [json.loads(line) for line in result.output.splitlines() if line.startswith("{")]
        self.assertEqual(len(rows), 2)
        self.assertNotIn("--resume", rows[0]["argv"])
        index = rows[1]["argv"].index("--resume")
        self.assertEqual(rows[1]["argv"][index + 1], "native-session")
        self.assertIn("Read again", rows[1]["argv"])
        self.assertEqual(result.details["session_ids"], ["native-session", "native-session"])

    def test_numbering_probe_resumes_three_real_prompts(self):
        from dataclasses import replace
        try:
            module = importlib.import_module(".drivers.hermes", __package__) if __package__ else importlib.import_module("drivers.hermes")
        except ModuleNotFoundError:
            self.fail("Hermes driver is unavailable")
        ctx = replace(self.ctx, scenario="multi-turn", followup_prompt="Read again")
        result = module.run_case(ctx, additional_prompts=("Read a third time",))
        rows = [json.loads(line) for line in result.output.splitlines() if line.startswith("{")]
        self.assertEqual(len(rows), 3)
        self.assertEqual(result.details["session_ids"], ["native-session"] * 3)
        self.assertIn("Read a third time", rows[-1]["argv"])
        self.assertEqual(rows[-1]["argv"][rows[-1]["argv"].index("--resume") + 1], "native-session")

    def test_current_official_release_and_legacy_release_match_explicit_runtime_pin(self):
        from dataclasses import replace
        original = self.cli.read_text()
        for version in ('0.18.2', '0.21.5'):
            with self.subTest(version=version):
                self.cli.write_text(original.replace('0.18.2', version))
                result = self.run_driver(replace(self.ctx, out=self.root / version, runtime_version=version))
                self.assertEqual(result.exit_code, 0)
                self.assertEqual(result.version, version)
                if version == '0.21.5':
                    row = json.loads(result.output.splitlines()[0])
                    self.assertIn('-z', row['argv'])
                    self.assertIn('--usage-file', row['argv'])
                    self.assertEqual(result.details['native_usage_reports'][0]['session_id'], 'native-session')
                    self.assertEqual(result.details['hermes_raw_callbacks'], [
                        {'hook': 'native-fixture-hook', 'rawInput': {'value': 7}}])
                else:
                    self.assertNotIn('hermes_raw_callbacks', result.details)

    def test_current_host_cannot_succeed_without_official_one_shot_session_report(self):
        from dataclasses import replace
        self.cli.write_text('import sys\nif "--version" in sys.argv: print("0.21.5")\nelse: print("session_id: pretend-session")\n')
        result = self.run_driver(replace(self.ctx, runtime_version='0.21.5'))
        self.assertNotEqual(result.exit_code, 0)
        self.assertIsNone(result.session_id)

    def test_version_drift_is_rejected_before_running_a_task(self):
        from dataclasses import replace
        result = self.run_driver(replace(self.ctx, runtime_version='0.21.5'))
        self.assertNotEqual(result.exit_code, 0)
        self.assertIsNone(result.session_id)
        self.assertIn('0.21.5', result.details['error'])

    def test_physical_metrics_use_delta_for_exact_interval_readback(self):
        result = self.run_driver()
        self.assertEqual(result.exit_code, 0)
        observer = json.loads((self.ctx.out / "hermes-home/observ-hermes.json").read_text())
        self.assertEqual(observer["otelExporter"].get("metricsTemporalityPreference"), "delta")

    def test_isolated_file_fixture_disables_unrelated_runtime_downloads(self):
        result = self.run_driver()
        self.assertEqual(result.exit_code, 0)
        config = json.loads((self.ctx.out / 'hermes-home/config.yaml').read_text())
        self.assertEqual(config['security'], {'allow_lazy_installs': False, 'tirith_enabled': False})

    def test_failure_preserves_native_exit_and_does_not_forge_session(self):
        self.cli.write_text('import sys\nif "--version" in sys.argv: print("0.18.2")\nelse: raise SystemExit(17)\n')
        result = self.run_driver()
        self.assertEqual(result.exit_code, 17)
        self.assertIsNone(result.session_id)

    def acp_fixture(self, stop_reason="cancelled", before_stop=""):
        self.cli.write_text('''import json, os, sys
from pathlib import Path
if "--version" in sys.argv:
    print("0.18.2"); raise SystemExit(0)
assert sys.argv[1:] == ["acp"]
home = Path(os.environ["HERMES_HOME"])
assert (home / "plugins/observ-hermes/__init__.py").is_file()
def read(): return json.loads(sys.stdin.readline())
def send(row): print(json.dumps(row), flush=True)
row = read(); assert row["method"] == "initialize"
send({"jsonrpc":"2.0","id":row["id"],"result":{"protocolVersion":1}})
row = read(); assert row["method"] == "session/new" and Path(row["params"]["cwd"]).resolve() == Path.cwd().resolve()
send({"jsonrpc":"2.0","id":row["id"],"result":{"sessionId":"native-acp-session"}})
row = read(); assert row["method"] == "session/prompt" and row["params"]["sessionId"] == "native-acp-session"
prompt_id = row["id"]
send({"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"native-acp-session","update":{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":"actual streamed delta"}}}})
row = read(); assert row["method"] == "session/cancel" and "id" not in row and row["params"]["sessionId"] == "native-acp-session"
''' + before_stop + '''
send({"jsonrpc":"2.0","id":prompt_id,"result":{"stopReason":''' + repr(stop_reason) + '''}})
assert sys.stdin.readline() == ""
''')

    def test_cancel_uses_official_acp_session_and_notification_after_delta(self):
        from dataclasses import replace
        self.acp_fixture()
        result = self.run_driver(replace(self.ctx, scenario="cancel"))
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.session_id, "native-acp-session")
        self.assertTrue(result.details["cancel_requested"])
        self.assertTrue(result.details["cancelled"])
        self.assertEqual(result.details.get("cancellation_protocol"), "official-acp")
        self.assertFalse((self.ctx.out / "hermes-home/state.db").exists())

    def test_acp_requires_native_cancelled_stop_reason(self):
        from dataclasses import replace
        self.acp_fixture("end_turn")
        result = self.run_driver(replace(self.ctx, scenario="cancel"))
        self.assertNotEqual(result.exit_code, 0)
        self.assertTrue(result.details["cancel_requested"])
        self.assertFalse(result.details["cancelled"])

    def test_acp_timeout_kills_descendants_holding_both_output_pipes(self):
        self.assert_acp_pipe_cleanup(parent_wait=10)

    def test_exited_acp_parent_cannot_leave_inherited_output_pipes_open(self):
        self.assert_acp_pipe_cleanup(parent_wait=0)

    @unittest.skipUnless(os.name == "posix", "POSIX escaped inherited-pipe contract")
    def test_acp_deadline_does_not_close_a_pipe_owned_by_an_active_reader(self):
        self.assert_acp_pipe_cleanup(parent_wait=10, escaped=True)

    def assert_acp_pipe_cleanup(self, parent_wait, *, escaped=False):
        from dataclasses import replace
        receipt = self.root / "acp-pids.json"
        finished = self.root / "child-finished"
        child = ("import time; from pathlib import Path; time.sleep(10); "
                 f"Path({str(finished)!r}).write_text('completed')")
        self.acp_fixture(before_stop=("import subprocess,time\n"
            f"child=subprocess.Popen([sys.executable,'-c',{child!r}], start_new_session={escaped!r})\n"
            f"Path({str(receipt)!r}).write_text(json.dumps([os.getpid(),child.pid]))\n"
            "print('ACP child inherited both output pipes',file=sys.stderr,flush=True)\n"
            f"time.sleep({parent_wait})\n"))
        outcomes = []
        def invoke():
            try:
                outcomes.append(self.run_driver(replace(self.ctx, scenario="cancel", timeout_seconds=.6)))
            except BaseException as error:
                outcomes.append(error)
        worker = threading.Thread(target=invoke, daemon=True)
        started = time.monotonic()
        worker.start()
        try:
            worker.join(timeout=2)
            self.assertTrue(receipt.exists(), "ACP must reach cancellation with a real inherited-pipe child")
            self.assertFalse(worker.is_alive(), "ACP cleanup exceeded its bounded deadline and pipe drain")
            self.assertLess(time.monotonic() - started, 1.8)
            self.assertEqual(len(outcomes), 1)
            result = outcomes[0]
            if isinstance(result, BaseException):
                raise result
            self.assertEqual(result.exit_code, 124)
            self.assertEqual(result.session_id, "native-acp-session")
            self.assertTrue(result.details["cancel_requested"])
            self.assertFalse(result.details["cancelled"])
            self.assertEqual(result.details["cancellation_protocol"], "official-acp")
            self.assertIn("actual streamed delta", result.output)
            self.assertIn("ACP child inherited both output pipes", result.output)
            self.assertFalse(finished.exists(), "cleanup must not wait for the child's natural exit")
            if not escaped:
                for pid in json.loads(receipt.read_text()):
                    if os.name == "posix":
                        assert_posix_process_stopped(pid, timeout=.5)
                    else:
                        listing = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
                            capture_output=True, text=True, timeout=2)
                        self.assertNotIn(f'"{pid}"', listing.stdout)
        finally:
            # Kill only this fixture's recorded PIDs, including an escaped child.
            # This also releases the pre-fix implementation's blocked pipe close.
            if receipt.exists():
                for pid in reversed(json.loads(receipt.read_text())):
                    if os.name == "posix":
                        try:
                            os.kill(pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    else:
                        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                            capture_output=True, timeout=2)
            worker.join(timeout=3)

    def test_non_loopback_provider_is_rejected_before_launch(self):
        from dataclasses import replace
        with self.assertRaises(ValueError):
            self.run_driver(replace(self.ctx, provider_url="https://integrate.api.nvidia.com/v1", mode="nvidia"))
        self.assertFalse(self.ctx.out.exists())


if __name__ == "__main__":
    unittest.main()
