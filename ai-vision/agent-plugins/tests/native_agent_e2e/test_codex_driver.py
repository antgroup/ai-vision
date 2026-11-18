"""Executable boundary tests for isolated native Codex diagnostics."""
from dataclasses import replace
import io
import importlib
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

from models import CaseContext
from _process_test_helpers import assert_posix_process_stopped


@unittest.skipUnless(
    os.name == "posix",
    "Codex diagnostic runtime requires POSIX host hooks and Linux containers",
)
class CodexDriverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cli = self.root / "codex.py"
        self.cli.write_text('''import json, os, subprocess, sys, tomllib
from pathlib import Path
if "--version" in sys.argv: print("codex-cli 0.161.0"); raise SystemExit(0)
home = Path(os.environ["CODEX_HOME"])
cfg = tomllib.loads((home / "config.toml").read_text())
assert cfg["model_provider"] == "native-e2e"
assert cfg["features"]["codex_hooks"] is True
assert cfg["model_providers"]["native-e2e"]["base_url"] == "http://127.0.0.1:12345/v1"
assert "--dangerously-bypass-hook-trust" in sys.argv
sid = sys.argv[sys.argv.index("resume")+1] if "resume" in sys.argv else "native-codex-session"
session = home / "sessions/2026/10/08"
session.mkdir(parents=True, exist_ok=True)
transcript = session / ("rollout-fixture-"+sid+".jsonl")
if not transcript.exists(): transcript.write_text(json.dumps({"type":"session_meta","payload":{"id":sid}})+"\\n")
print(json.dumps({"type":"thread.started","thread_id":sid}), flush=True)
print(json.dumps({"type":"fixture.environment","argv":sys.argv[1:],"home":os.environ["HOME"],"path":os.environ.get("PATH"),"secret_inherited":"NVIDIA_API_KEY" in os.environ,"headers":os.environ["CDXHOOKS_OTEL_EXPORTER_OTLP_HEADERS"],"resource":os.environ["CDXHOOKS_OTEL_RESOURCE_ATTRIBUTES"],"collection_mode":os.environ["CDXHOOKS_COLLECTION_MODE"]}), flush=True)
hooks = json.loads((home / "hooks.json").read_text())["hooks"]
for event in ("UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop", "SessionEnd"):
    payload = {"hook_event_name":event,"session_id":sid,"cwd":os.getcwd(),"transcript_path":str(transcript)}
    subprocess.run(hooks[event][0]["hooks"][0]["command"], shell=True, input=json.dumps(payload), text=True, check=True)
with transcript.open("a") as f: f.write(json.dumps({"type":"event_msg","payload":{"type":"task_complete","last_agent_message":"receipt: native"}})+"\\n")
print(json.dumps({"type":"item.completed","item":{"id":"tool1","type":"command_execution","command":"cat receipt.txt","aggregated_output":"receipt: native","exit_code":0,"status":"completed"}}), flush=True)
print(json.dumps({"type":"item.completed","item":{"id":"answer1","type":"agent_message","text":"receipt: native"}}), flush=True)
print(json.dumps({"type":"turn.completed","usage":{"input_tokens":16,"output_tokens":4}}), flush=True)
''')
        self.hook = self.root / "cdxhooks"
        self.hook.write_text('#!' + sys.executable + '''
import json, os, sys
from pathlib import Path
if "--version" in sys.argv: print("cdxhooks 0.3.18"); raise SystemExit(0)
if "info" in sys.argv: print(json.dumps({"version":"0.3.18","edition":"default"})); raise SystemExit(0)
payload = json.load(sys.stdin)
state = Path(os.environ["CDXHOOKS_STATE_DIR"])
state.mkdir(parents=True, exist_ok=True)
with (state / "actual-hook.jsonl").open("a") as f: f.write(json.dumps(payload)+"\\n")
''')
        self.hook.chmod(0o755)
        self.repo = Path(__file__).resolve().parents[4]
        self.ctx = CaseContext(repo=self.repo, out=self.root / "out", workspace=self.root / "workspace",
            runtime=(sys.executable, str(self.cli)), provider_url="http://127.0.0.1:12345/v1",
            collector_url="http://127.0.0.1:12346", scenario="diagnostic-read-edit-test", prompt="Read receipt.txt",
            runtime_version="0.161.0", headers={"x-space-key":"collector-test"},
            resource={"ant.agent.product":"Codex","test.case":"diagnostic-read"}, timeout_seconds=5)

    def run_driver(self, ctx=None):
        try: module = importlib.import_module("drivers.codex")
        except ModuleNotFoundError: self.fail("diagnostics require an isolated native Codex process driver")
        return module.run_case(ctx or self.ctx)

    def test_real_process_dispatches_vetted_hooks_and_preserves_native_transcript(self):
        with patch.dict(os.environ, {"NVIDIA_API_KEY":"personal-secret","CODEX_HOME":"/personal","CDXHOOKS_LANGFUSE_SECRET_KEY":"personal-secret"}):
            result = self.run_driver()
        self.assertEqual((result.exit_code, result.session_id, result.version), (0, "native-codex-session", "0.161.0"))
        self.assertEqual(result.details["turns_completed"], 1)
        self.assertTrue(Path(result.details["transcript_path"]).is_file())
        self.assertEqual(result.details["native_transcript"][0]["payload"]["id"], result.session_id)
        env = next(row for row in result.details["cli_events"] if row.get("type") == "fixture.environment")
        self.assertFalse(env["secret_inherited"])
        self.assertEqual(env["home"], str(self.ctx.out / "home"))
        self.assertEqual(json.loads(env["headers"]), self.ctx.headers)
        self.assertEqual(env["resource"], "ant.agent.product=Codex,test.case=diagnostic-read")
        hooks = [json.loads(line) for line in (self.ctx.out / "hook-state/actual-hook.jsonl").read_text().splitlines()]
        self.assertEqual([row["hook_event_name"] for row in hooks], ["UserPromptSubmit","PreToolUse","PostToolUse","Stop","SessionEnd"])
        self.assertEqual(result.details["tool_outcomes"][0]["exit_code"], 0)
        self.assertEqual(result.details["hook_config_provenance"]["binary"], str(self.hook.resolve()))

    def test_actual_followup_resumes_native_session(self):
        result = self.run_driver(replace(self.ctx, followup_prompt="Read again"))
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.details["turns_completed"], 2)
        envs = [row for row in result.details["cli_events"] if row.get("type") == "fixture.environment"]
        self.assertIn("resume", envs[-1]["argv"])
        self.assertEqual(envs[-1]["argv"][envs[-1]["argv"].index("resume")+1], result.session_id)

    def test_privacy_uses_production_collection_mode_without_parent_override(self):
        with patch.dict(os.environ, {"AIVISION_COLLECTION_MODE":"full","CDXHOOKS_COLLECTION_MODE":"full"}):
            result = self.run_driver(replace(self.ctx, scenario="privacy"))
        env = next(row for row in result.details["cli_events"] if row.get("type") == "fixture.environment")
        self.assertEqual(env["collection_mode"], "privacy")
        self.assertEqual(result.details["hook_config_provenance"]["collection_mode"], "privacy")

    def cancellation_fixture(self, behavior):
        self.cli.write_text('''import json, os, signal, sys, time
from pathlib import Path
if "--version" in sys.argv: print("codex-cli 0.161.0"); raise SystemExit(0)
session=Path(os.environ["CODEX_HOME"])/"sessions/2026/10/08"
session.mkdir(parents=True, exist_ok=True)
transcript=session/"actual-fixture-session.jsonl"
transcript.write_text(json.dumps({"type":"session_meta","payload":{"id":"native-codex-session"}})+"\\n")
def interrupt(sig, frame):
    with transcript.open("a") as f: f.write(json.dumps({"type":"event_msg","payload":{"type":"turn_aborted","turn_id":"actual-turn","reason":"interrupted"}})+"\\n")
    raise SystemExit(1)
''' + behavior + '''
print(json.dumps({"type":"thread.started","thread_id":"native-codex-session"}), flush=True)
print(json.dumps({"type":"turn.started"}), flush=True)
time.sleep(10)
''')

    def test_requested_native_interrupt_requires_transcript_abort_and_preserves_exit(self):
        module = importlib.import_module("drivers.codex")
        self.cancellation_fixture("signal.signal(signal.SIGINT, interrupt)\n")
        ready = threading.Event()
        timer = threading.Timer(.3, ready.set)
        timer.start()
        self.addCleanup(timer.cancel)
        result = self.run_driver(replace(module.CodexContext(**self.ctx.__dict__, hook_binary=self.hook),
            scenario="cancel", cancel_ready=ready))
        self.assertEqual(result.exit_code, 1)
        self.assertTrue(result.details["cancelled"])
        self.assertFalse(result.details["timed_out"])
        self.assertEqual(result.details["cancellation"]["signal"], "SIGINT")
        self.assertTrue(result.details["cancellation"]["native_abort_observed"])
        self.assertEqual(result.details["turns_completed"], 0)
        self.assertGreaterEqual(result.details["cancellation"]["sent_ns"], result.details["cancellation"]["turn_started_ns"])

    def test_ignored_interrupt_is_timeout_and_never_user_cancellation(self):
        self.cancellation_fixture("signal.signal(signal.SIGINT, signal.SIG_IGN)\n")
        result = self.run_driver(replace(self.ctx, scenario="cancel", timeout_seconds=2))
        self.assertEqual(result.exit_code, 124)
        self.assertTrue(result.details["timed_out"])
        self.assertFalse(result.details["cancelled"])
        self.assertTrue(result.details["cancellation"]["sent_ns"])
        self.assertFalse(result.details["cancellation"]["native_abort_observed"])

    def test_signal_request_without_native_abort_is_not_cancellation(self):
        self.cancellation_fixture("signal.signal(signal.SIGINT, lambda sig, frame: sys.exit(1))\n")
        result = self.run_driver(replace(self.ctx, scenario="cancel"))
        self.assertEqual(result.exit_code, 1)
        self.assertFalse(result.details["cancelled"])
        self.assertTrue(result.details["cancellation"]["sent_ns"])

    def test_cancel_readiness_never_signals_a_turn_that_already_completed(self):
        module = importlib.import_module("drivers.codex")
        context = replace(module.CodexContext(**self.ctx.__dict__, hook_binary=self.hook),
            scenario="cancel", cancel_ready=threading.Event())
        result = self.run_driver(context)
        self.assertFalse(result.details["cancelled"])
        self.assertIsNone(result.details["cancellation"].get("sent_ns"))

    def test_hook_queue_drain_includes_production_worker_and_stop_intent(self):
        module = importlib.import_module("drivers.codex")
        state = self.root / "queue/session"
        state.mkdir(parents=True)
        (state / "worker.queue.jsonl").write_text('{"payload":{"hook_event_name":"SessionEnd"}}\n')
        (state / "worker.queue.offset.json").write_text("0")
        self.assertFalse(module._wait_hooks(self.root / "queue", .1))
        (state / "worker.queue.offset.json").write_text(str((state / "worker.queue.jsonl").stat().st_size))
        (state / "state_stop_intent.json").write_text('{"status":"pending"}')
        self.assertFalse(module._wait_hooks(self.root / "queue", .1))
        (state / "state_stop_intent.json").unlink()
        self.assertTrue(module._wait_hooks(self.root / "queue", .1))

    def test_version_drift_fails_before_native_session(self):
        self.cli.write_text('print("codex-cli 0.162.0")\n')
        result = self.run_driver()
        self.assertNotEqual(result.exit_code, 0)
        self.assertIsNone(result.session_id)
        self.assertIn("0.161.0", result.details["error"])

    def test_exit_zero_without_native_completion_is_failure(self):
        self.cli.write_text('import sys\nif "--version" in sys.argv: print("codex-cli 0.161.0")\nelse: print("{}")\n')
        result = self.run_driver()
        self.assertNotEqual(result.exit_code, 0)
        self.assertIsNone(result.session_id)
        self.assertEqual(result.details["turns_completed"], 0)

    def test_remote_endpoints_fail_before_launch(self):
        with self.assertRaises(ValueError): self.run_driver(replace(self.ctx, collector_url="https://collector.example"))
        self.assertFalse(self.ctx.out.exists())

    def test_missing_vetted_hook_is_rejected(self):
        self.hook.unlink()
        with self.assertRaisesRegex(ValueError, "cdxhooks"): self.run_driver()

    def test_embedded_key_editions_are_rejected_before_native_task(self):
        self.hook.write_text(self.hook.read_text().replace('"edition":"default"', '"edition":"ant"'))
        result = self.run_driver()
        self.assertNotEqual(result.exit_code, 0)
        self.assertIsNone(result.session_id)
        self.assertIn("common", result.details["error"])

    def test_timeout_kills_descendants_even_after_parent_exits(self):
        self.cli.write_text('''import subprocess, sys, time
if "--version" in sys.argv: print("codex-cli 0.161.0"); raise SystemExit(0)
child=subprocess.Popen([sys.executable,"-c","import time;time.sleep(10)"])
print(child.pid, flush=True)
''')
        started = time.monotonic()
        result = self.run_driver(replace(self.ctx, timeout_seconds=1))
        self.assertEqual(result.exit_code, 124)
        self.assertLess(time.monotonic() - started, 3)
        child = int(result.output.strip())
        if os.name == "posix":
            assert_posix_process_stopped(child)

    @unittest.skipUnless(os.name == "posix", "POSIX owned process group")
    def test_successful_group_kill_is_idempotent_but_live_permission_error_survives(self):
        module = importlib.import_module("drivers.codex")
        process = SimpleNamespace(pid=12345)
        with patch.object(module.os, "killpg", side_effect=[None, PermissionError("gone group")]) as kill:
            module._kill_group(process, signal.SIGKILL)
            module._kill_group(process, signal.SIGKILL)
            kill.assert_called_once_with(process.pid, signal.SIGKILL)
        process = SimpleNamespace(pid=12346)
        with patch.object(module.os, "killpg", side_effect=PermissionError("live denied")):
            with self.assertRaisesRegex(PermissionError, "live denied"):
                module._kill_group(process, signal.SIGKILL)
        process = SimpleNamespace(pid=12347)
        with patch.object(module.os, "killpg", side_effect=ProcessLookupError) as kill:
            module._kill_group(process, signal.SIGKILL)
            module._kill_group(process, signal.SIGKILL)
            kill.assert_called_once()

    @unittest.skipUnless(os.name == "posix", "POSIX owned descendants")
    def test_parent_exit_with_live_child_repeatedly_cleans_group(self):
        module = importlib.import_module("drivers.codex")
        self.ctx.workspace.mkdir()
        program = "import subprocess,sys; p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(5)']); print(p.pid,flush=True)"
        for trial in range(10):
            with self.subTest(trial=trial):
                code, output, _, timed_out = module._invoke(
                    [sys.executable, "-c", program], dict(os.environ), self.ctx.workspace, .2)
                self.assertEqual(code, 124)
                self.assertTrue(timed_out)
                child = int(output.strip())
                assert_posix_process_stopped(child, timeout=1)

    def test_cleanup_failure_still_removes_container_and_joins_every_reader(self):
        module = importlib.import_module("drivers.codex")
        for action in ("kill", "remove", "both", "wait", "reader"):
            with self.subTest(action=action):
                process = SimpleNamespace(pid=1234, stdout=io.StringIO(""), stderr=io.StringIO(""),
                    poll=lambda: 0, returncode=0)
                readers = [Mock(), Mock()]
                for reader in readers: reader.is_alive.return_value = False
                if action == "wait":
                    process.poll = Mock(side_effect=[0, 0, None])
                    process.wait = Mock(side_effect=RuntimeError("wait failed"))
                if action == "reader": readers[0].join.side_effect = RuntimeError("reader failed")
                with patch.object(module.subprocess, "Popen", return_value=process), \
                        patch.object(module.threading, "Thread", side_effect=readers), \
                        patch.object(module, "_kill_group", side_effect=PermissionError("live denied") if action in {"kill", "both"} else None), \
                        patch.object(module, "_remove_container", side_effect=RuntimeError("remove failed") if action in {"remove", "both"} else None) as remove:
                    error = ExceptionGroup if action == "both" else (PermissionError if action == "kill" else RuntimeError)
                    with self.assertRaises(error):
                        module._invoke(["owned"], {}, self.ctx.workspace, 1)
                    remove.assert_called_once()
                    for reader in readers: reader.join.assert_called_once()

    def test_container_runtime_passes_only_explicit_environment_and_mounts_private_paths(self):
        module = importlib.import_module("drivers.codex")
        runtime = module.codex_container_runtime("vetted-image", self.ctx.repo, self.ctx.out, self.ctx.workspace,
            docker="/usr/local/bin/docker", endpoint="unix:///tmp/isolated-docker.sock")
        ctx = replace(self.ctx, runtime=runtime)
        command = module._command(ctx, {"HOME":"/isolated","NATIVE_E2E_LOOPBACK_KEY":"placeholder"}, ["--version"])
        self.assertIn("--network=host", command)
        self.assertIn("--user=" + str(os.getuid()) + ":" + str(os.getgid()), command)
        self.assertIn(str(self.ctx.repo) + ":" + str(self.ctx.repo) + ":ro", command)
        image = command.index("vetted-image")
        self.assertEqual(command[image-4:image], ["--env","HOME","--env","NATIVE_E2E_LOOPBACK_KEY"])
        self.assertEqual(command[-1], "--version")
        self.assertNotIn("placeholder", command)

    def test_container_defaults_discover_linux_docker_and_local_socket_before_isolation(self):
        module = importlib.import_module("drivers.codex")
        with patch.dict(os.environ, {"DOCKER_HOST":"unix:///run/user/1000/docker.sock"}, clear=True), \
                patch("shutil.which", return_value="/usr/bin/docker"):
            runtime = module.codex_container_runtime("vetted-image", self.ctx.repo, self.ctx.out, self.ctx.workspace)
        self.assertEqual(runtime[:3], ("/usr/bin/docker", "-H", "unix:///run/user/1000/docker.sock"))
        self.assertNotIn("/home/example-user", " ".join(runtime[:3]))

    def test_container_keeps_image_path_instead_of_forwarding_host_path(self):
        module = importlib.import_module("drivers.codex")
        runtime = module.codex_container_runtime("vetted-image", self.ctx.repo, self.ctx.out, self.ctx.workspace,
            docker="/usr/bin/docker", endpoint="unix:///tmp/fixture-docker.sock")
        env = {"PATH":"/opt/homebrew/bin:/Users/fixture/mac-tools", "HOME":"/isolated",
            "CODEX_HOME":"/isolated/.codex", "NATIVE_E2E_LOOPBACK_KEY":"placeholder"}
        command = module._command(replace(self.ctx, runtime=runtime), env, ["--version"])
        passed = [command[index+1] for index, value in enumerate(command[:-1]) if value == "--env"]
        self.assertNotIn("PATH", passed)
        self.assertEqual(passed, ["HOME", "CODEX_HOME", "NATIVE_E2E_LOOPBACK_KEY"])
        self.assertEqual(env["PATH"], "/opt/homebrew/bin:/Users/fixture/mac-tools")

    def test_host_native_process_keeps_its_host_tool_path(self):
        with patch.dict(os.environ, {"PATH":"/host-fixture-tools"}):
            result = self.run_driver()
        env = next(row for row in result.details["cli_events"] if row.get("type") == "fixture.environment")
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(env["path"], "/host-fixture-tools")

    def test_container_default_resolves_active_context_socket_without_copying_personal_home(self):
        module = importlib.import_module("drivers.codex")
        with patch.dict(os.environ, {"HOME":"/private-user-home"}, clear=True), \
                patch("shutil.which", return_value="/usr/bin/docker"), \
                patch("subprocess.run", return_value=SimpleNamespace(returncode=0,
                    stdout='"unix:///var/run/docker.sock"\n', stderr="")) as inspect:
            runtime = module.codex_container_runtime("vetted-image", self.ctx.repo, self.ctx.out, self.ctx.workspace)
        self.assertEqual(runtime[:3], ("/usr/bin/docker", "-H", "unix:///var/run/docker.sock"))
        self.assertEqual(inspect.call_args.args[0][:3], ["/usr/bin/docker", "context", "inspect"])
        self.assertNotIn("/private-user-home", runtime)

    def test_container_explicit_linux_docker_and_socket_never_inspects_host_context(self):
        module = importlib.import_module("drivers.codex")
        with patch.dict(os.environ, {"DOCKER_HOST":"tcp://personal.remote:2376"}), patch("subprocess.run") as inspect:
            runtime = module.codex_container_runtime("vetted-image", self.ctx.repo, self.ctx.out, self.ctx.workspace,
                docker="/usr/bin/docker", endpoint="unix:///tmp/fixture-docker.sock")
        self.assertEqual(runtime[:3], ("/usr/bin/docker", "-H", "unix:///tmp/fixture-docker.sock"))
        inspect.assert_not_called()
        with self.assertRaisesRegex(ValueError, "local Docker socket"):
            module.codex_container_runtime("vetted-image", self.ctx.repo, self.ctx.out, self.ctx.workspace,
                docker="/usr/bin/docker", endpoint="tcp://remote:2376")

    def test_every_native_invoke_exit_cleans_only_its_container_and_preserves_the_result(self):
        module = importlib.import_module("drivers.codex")
        argv = ["/usr/bin/docker", "-H", "unix:///tmp/fixture-docker.sock", "run",
            "--name=aiv-codex-e2e-fixture", "fixture-image", "native"]
        env = {"HOME":"/isolated"}
        for outcome in ("normal", "timeout", "interrupt", "queue_error"):
            with self.subTest(outcome=outcome):
                process = SimpleNamespace(pid=1234, stdout=io.StringIO(""), stderr=io.StringIO(""),
                    poll=lambda: 0 if outcome == "normal" else None,
                    wait=lambda **_: -9, returncode=0 if outcome == "normal" else -9)
                with patch.object(module.subprocess, "Popen", return_value=process), \
                        patch.object(module, "_kill_group") as kill, \
                        patch.object(module, "_remove_container") as remove:
                    if outcome == "timeout":
                        with patch.object(module.time, "monotonic", side_effect=(0, 2)):
                            result = module._invoke(argv, env, self.ctx.workspace, 1)
                        self.assertEqual(result, (124, "", "", True))
                    elif outcome in {"interrupt", "queue_error"}:
                        error = KeyboardInterrupt if outcome == "interrupt" else RuntimeError
                        with patch.object(module.queue.Queue, "get", side_effect=error("native queue interrupted")):
                            with self.assertRaises(error):
                                module._invoke(argv, env, self.ctx.workspace, 1)
                    else:
                        result = module._invoke(argv, env, self.ctx.workspace, 1)
                        self.assertEqual(result, (0, "", "", False))
                    remove.assert_called_once_with(argv, env)
                    kill.assert_called()

    def test_container_cleanup_never_removes_other_container_names_or_local_processes(self):
        module = importlib.import_module("drivers.codex")
        with patch.object(module.subprocess, "run") as remove:
            module._remove_container(["/usr/bin/docker", "run", "--name=other-task", "image"], {})
            module._remove_container(["/native/codex", "exec"], {})
            remove.assert_not_called()
            owned = ["/usr/bin/docker", "-H", "unix:///tmp/fixture-docker.sock", "run",
                "--name=aiv-codex-e2e-owned", "image"]
            module._remove_container(owned, {})
            self.assertEqual(remove.call_args.args[0], ["/usr/bin/docker", "-H",
                "unix:///tmp/fixture-docker.sock", "rm", "--force", "aiv-codex-e2e-owned"])


if __name__ == "__main__": unittest.main()
