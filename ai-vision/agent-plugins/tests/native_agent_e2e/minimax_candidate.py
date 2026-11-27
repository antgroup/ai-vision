"""Exercise the installed MiniMax artifact with a real mcode and a fake model."""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import platform
import re
from runpy import run_path
import shutil
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

from fake_provider import FakeProvider
from windows_codex_onboarding import PUBLIC, SourceOnboardingServer


MODEL = "custom_provider:ai-vision-fixture/native-e2e-chat"


class MiniMaxOnboardingServer(SourceOnboardingServer):
    """Use the existing loopback installer protocol with MiniMax artifact bytes."""

    def __init__(self, plugin_root, artifact_root):
        manifest_file = artifact_root / "minimax-code/latest.json"
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        self.download, = manifest["downloads"]
        version = manifest["version"]
        name = self.download["filename"]
        artifact = manifest_file.parent / version / name
        super().__init__(plugin_root, artifact)
        self.version = version
        self.artifact_path = f"{PUBLIC}/plugins/minimax-code/{version}/{name}"
        self.plugin = next(agent for agent in json.loads((plugin_root / "agents.json").read_text())
                           if agent["key"] == "minimax-code")
        if self.plugin.get("preview") is not True:
            raise RuntimeError("Expected the explicit MiniMax preview catalog entry")
        claims = {"v": 2, "sp": self.space_id, "aid": self.agent_uuid,
                  "ap": "MiniMaxCode", "u": "fixture-observer"}
        encoded = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
        self.ingest_token = f"{encoded}.loopback-fixture"

    def _catalog(self):
        catalog = super()._catalog()
        catalog["agents"] = [{
            "key": "minimax-code", "version": self.version, "plugin": self.plugin,
            "downloads": [{**self.download, "url": self.endpoint + self.artifact_path}],
        }]
        return catalog

    def _token(self):
        return {**super()._token(), "agent_product": "MiniMaxCode"}


def run(command, *, env, cwd, timeout=45):
    result = subprocess.run(command, cwd=cwd, env=env, encoding="utf-8", capture_output=True,
                            timeout=timeout, stdin=subprocess.DEVNULL)
    if result.returncode:
        # The fixture has no external credentials. Avoid raw model/tools or
        # scoped fixture authorization strings in workflow console output.
        raise RuntimeError(f"{Path(command[0]).name} returned {result.returncode}")
    return result


def check_turn(launcher, env, task, provider, server, *, mode, session=None):
    before = len(provider.requests)
    command = [*launcher, "--cwd", str(task), "--permission", "full", "--max-steps", "5", "--model", MODEL]
    if session:
        command.extend(("--session", session))
    command.append("Read the fixture file and return the receipt.")
    result = run(command, env={**env, "AIVISION_COLLECTION_MODE": mode}, cwd=task, timeout=45)
    events = [json.loads(line) for line in result.stdout.splitlines()]
    start, = (event for event in events if event["type"] == "turn.started")
    end, = (event for event in events if event["type"] == "exec.completed")
    if end["result"]["status"] != "succeeded" or (session and start["sessionId"] != session):
        raise RuntimeError("Native MiniMax Code turn did not finish or resume")
    trace = re.search(r"AI Vision Trace ([0-9a-f]{32}) \(succeeded\)", result.stderr)
    if trace is None:
        raise RuntimeError("No exported trace ID was reported")
    rows = [span for span in server.spans if span["trace_id"] == trace[1]]
    kinds = {}
    for row in rows:
        attrs = row["attributes"]
        if attrs["session.id"] != start["sessionId"] or attrs["agentic.collection.mode"] != mode:
            raise RuntimeError("Trace session or collection mode did not match its native turn")
        if attrs["user.id"] != "fixture-observer":
            raise RuntimeError("Scoped observation subject was not used")
        kinds.setdefault(attrs["gen_ai.span.kind"], []).append(row)
    root, = kinds["CHAT"]
    tool, = kinds["TOOL"]
    model_spans = kinds["LLM"]
    if len(model_spans) != 2 or any(item["parent_span_id"] != root["span_id"] for item in [tool, *model_spans]):
        raise RuntimeError("Unexpected tool and model invocation hierarchy")
    calls = [request for request in provider.requests[before:] if request["purpose"] == "agent"]
    expected = [(entry["usage"]["prompt_tokens"], entry["usage"]["completion_tokens"]) for entry in calls]
    actual = [(item["attributes"]["gen_ai.usage.input_tokens"],
               item["attributes"]["gen_ai.usage.output_tokens"]) for item in model_spans]
    if expected != actual:
        raise RuntimeError("Per-request token usage did not match the model fixture")
    receipt = (task / "receipt.txt").read_text(encoding="utf-8").split()[-1]
    if receipt not in result.stdout or (mode == "privacy") == (receipt in json.dumps(rows)):
        raise RuntimeError("Native read tool or privacy assertion failed")
    return start["sessionId"], {"mode": mode, "spans": len(rows), "model_calls": len(calls)}


def check_windows_cancellation(host_command, env, task, provider, server, console_test):
    """Interrupt a real mcode stream on its own console using the installed collector."""
    send_ctrl_c = run_path(str(console_test))["SEND_CTRL_C"]
    provider.scenario = "cancel"
    before = len(provider.requests)
    profile = Path(env["MINIMAX_DATA_DIR"])
    managed = profile / "aivision/minimax-code"
    installation = json.loads((managed / "installation.json").read_text(encoding="utf-8"))
    wrapper = Path(installation["installed_path"]) / "dist/index.js"
    node, entry = host_command
    # Use the installed wrapper directly to observe its exit code and stream.
    # The independent console fixture covers the enclosing PowerShell process.
    command = [node, str(wrapper), "--ingest-config", str(managed / "ingest.json"),
               "--mcode", node, "--mcode-entry", entry, "--profile-dir", str(profile), "--",
               "--cwd", str(task), "--permission", "full", "--model", MODEL, "Start a streaming reply."]
    output, diagnostics = task.parent / "cancel.jsonl", task.parent / "cancel.stderr"

    def read_events():
        return [json.loads(line) for line in output.read_bytes().split(b"\n")[:-1]]

    with output.open("wb") as stdout, diagnostics.open("wb") as stderr:
        process = subprocess.Popen(command, cwd=task, env={**env, "AIVISION_COLLECTION_MODE": "privacy"},
                                   stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                   creationflags=subprocess.CREATE_NEW_CONSOLE)
        try:
            deadline = time.monotonic() + 20
            while process.poll() is None and time.monotonic() < deadline:
                if any(event.get("item", {}).get("type") == "agent_message" and
                       "pending" in event["item"].get("contentDelta", "") for event in read_events()):
                    break
                time.sleep(.05)
            else:
                raise RuntimeError("Native MiniMax Code did not start streaming cancellation fixture output")
            subprocess.run([sys.executable, "-c", send_ctrl_c, str(process.pid)],
                           creationflags=subprocess.DETACHED_PROCESS, capture_output=True, check=True, timeout=10)
            code = process.wait(timeout=75)
        finally:
            if process.poll() is None:
                taskkill = Path(env["SystemRoot"]) / "System32/taskkill.exe"
                subprocess.run([str(taskkill), "/PID", str(process.pid), "/T", "/F"],
                               creationflags=subprocess.CREATE_NO_WINDOW, capture_output=True, timeout=8)
                process.wait(timeout=5)

    events = read_events()
    start, = (event for event in events if event["type"] == "turn.started")
    end, = (event for event in events if event["type"] == "exec.completed")
    trace = re.search(r"AI Vision Trace ([0-9a-f]{32}) \(cancelled\)", diagnostics.read_text(encoding="utf-8"))
    if code != 130 or end["result"]["status"] != "cancelled" or trace is None:
        raise RuntimeError("Ctrl-C did not cancel the native run and export its Trace")
    spans = [span for span in server.spans if span["trace_id"] == trace[1]]
    root, = (span for span in spans if span["attributes"].get("gen_ai.span.kind") == "CHAT")
    model, = (span for span in spans if span["attributes"].get("gen_ai.span.kind") == "LLM")
    if (len(spans) != 2 or model["parent_span_id"] != root["span_id"] or
        any(span["attributes"].get("error.type") != "cancelled" or
            span["attributes"].get("session.id") != start["sessionId"] or
            span["attributes"].get("agentic.collection.mode") != "privacy" for span in spans)):
        raise RuntimeError("Cancelled model and turn did not retain the expected Trace hierarchy and scope")
    call, = (request for request in provider.requests[before:] if request["purpose"] == "agent")
    usage = model["attributes"]
    if not call["request"].get("stream") or call["usage_reported"] or any(
        usage.get(f"agentic.usage.{field}.missing") != "true" or f"gen_ai.usage.{field}" in usage
        for field in ("input_tokens", "output_tokens")
    ):
        raise RuntimeError("Cancelled model request did not preserve missing usage")
    return {"status": "cancelled", "exit_code": code, "model_usage": "missing", "spans": len(spans)}


def check_status(aiv, env, task, server):
    # The explicit Market grant route does not sign this fixture into an account.
    config_dir = Path(env["AIVISION_CONFIG_DIR"])
    config_dir.mkdir(mode=0o700)
    context = {"uuid": server.agent_uuid, "configDir": env["MINIMAX_DATA_DIR"], "baseUrl": server.endpoint}
    profile = {"endpoint": server.endpoint, "spaceId": server.space_id,
               "spaces": {server.space_id: {"agents": {"minimax-code": context}}}}
    (config_dir / "config.json").write_text(json.dumps({"activeProfile": "fixture", "profiles": {"fixture": profile}}))
    result = run([str(aiv), "agent", "status", "minimax-code", "--format", "json"], env=env, cwd=task)
    status = json.loads(result.stdout)
    expected = {"installed": True, "registration": "verified", "credential": "valid",
                "collection_mode": "privacy", "data_status": "unverified"}
    if any(status.get(key) != value for key, value in expected.items()):
        raise RuntimeError("Installed MiniMax Code status did not match the fixture scope")
    return expected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--aiv", type=Path, required=True)
    parser.add_argument("--mcode", type=Path, required=True)
    parser.add_argument("--host-platform", required=True)
    args = parser.parse_args()
    windows = platform.system() == "Windows"
    host = f"{platform.system()}/{platform.machine()}"
    if host.lower() != args.host_platform.lower():
        raise RuntimeError("Actual runner architecture did not match the candidate matrix")
    with tempfile.TemporaryDirectory(prefix="aiv-minimax-fixture-") as directory:
        root = Path(directory).resolve()
        task = root / "task"
        task.mkdir()
        receipt = task / "receipt.txt"
        receipt.write_text(f"receipt: {uuid4().hex}\n", encoding="utf-8")
        env = {
            "PATH": os.pathsep.join((str(args.mcode.parent.absolute()), os.environ["PATH"])),
            "HOME": str(root), "MINIMAX_DATA_DIR": str(root / "profile"),
            "AIVISION_CONFIG_DIR": str(root / "aiv"), "AIVISION_PYTHON": sys.executable,
            "MCODE_DISABLE_TELEMETRY": "1", "MINIMAX_FIXTURE_API_KEY": "local-fixture-key",
        }
        host_command = [str(args.mcode)]
        launcher_path = Path(env["MINIMAX_DATA_DIR"]) / "aivision/minimax-code"
        launcher = [str(launcher_path / "run")]
        if windows:
            env.update({name: os.environ[name] for name in ("SystemRoot", "ComSpec", "PATHEXT") if name in os.environ})
            env.update(USERPROFILE=str(root), APPDATA=str(root / "AppData/Roaming"),
                       LOCALAPPDATA=str(root / "AppData/Local"), TEMP=str(root), TMP=str(root), PYTHONUTF8="1")
            node = shutil.which("node.exe", path=env["PATH"])
            pwsh = shutil.which("pwsh.exe", path=env["PATH"])
            entry = args.mcode.parent / "node_modules/@minimax-ai/code/cli.js"
            if not node or not pwsh or not entry.is_file():
                raise RuntimeError("The Windows fixture requires the npm MiniMax Code installation, Node.js and PowerShell")
            # Configure the pinned npm entry directly. Installation still discovers
            # the original npm shim and verifies it through the production resolver.
            host_command = [node, str(entry)]
            launcher = [pwsh, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
                        str(launcher_path / "run.ps1")]
        with FakeProvider(receipt, scenario="cache-usage") as provider, MiniMaxOnboardingServer(
            args.repo / "ai-vision/agent-plugins", args.dist,
        ) as server:
            # Explicit CLI source-artifact installation, including public download and grant exchange.
            run([*host_command, "provider", "add", "--name", "AI Vision Fixture",
                 "--base-url", provider.url, "--api-format", "openai-completions",
                 "--model", "native-e2e-chat", "--api-key-env", "MINIMAX_FIXTURE_API_KEY"],
                env=env, cwd=task)
            run([str(args.aiv), "connect", "minimax-code", "--base-url", server.endpoint,
                 "--space-id", server.space_id, "--agent-uuid", server.agent_uuid,
                 "--installation-token", server.installation_token, "--config-dir", env["MINIMAX_DATA_DIR"]],
                env=env, cwd=task)
            session, full = check_turn(launcher, env, task, provider, server, mode="full")
            receipt.write_text(f"receipt: {uuid4().hex}\n", encoding="utf-8")
            _, privacy = check_turn(launcher, env, task, provider, server, mode="privacy", session=session)
            cancellation = {}
            if windows:
                console_test = args.repo / "ai-vision/agent-plugins/minimax-code/tests/test_windows_minimax_console.py"
                cancellation["native_model_cancellation"] = check_windows_cancellation(
                    host_command, env, task, provider, server, console_test)
            status = check_status(args.aiv, env, task, server)
            reports = 2 + len(cancellation)
            if server.counts != {"exchanges": 1, "refreshes": reports + 1, "confirmations": 1, "reports": reports}:
                raise RuntimeError("Unexpected installation or collector request counts")
            print(json.dumps({"status": "passed", "host": host, "agent": "mcode 0.7.2",
                              "artifact_sha256": server.download["sha256"],
                              "model": "loopback fixture", "turns": [full, privacy],
                              "agent_status": status, **cancellation}))


if __name__ == "__main__":
    main()
