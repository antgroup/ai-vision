#!/usr/bin/env python3
"""Exercise genuine Windows console delivery using a synthetic MiniMax CLI host."""
from __future__ import annotations

import base64
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
from queue import Empty, Queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest


FIXTURES = Path(__file__).resolve().parent
LAUNCHER = FIXTURES.parent / "run.mjs"
HOST = FIXTURES / "fixtures" / "windows-mcode-fixture.mjs"
NODE = shutil.which("node.exe") or shutil.which("node")
POWERSHELL = shutil.which("pwsh.exe")
CLAIMS = {"v": 2, "sp": "space", "aid": "agent", "ap": "MiniMaxCode", "u": "observation"}
FIXTURE_TOKEN = base64.urlsafe_b64encode(json.dumps(CLAIMS).encode()).decode().rstrip("=") + ".synthetic"
SEND_CTRL_C = """
import ctypes
from ctypes import wintypes
import sys
import time

kernel = ctypes.WinDLL('kernel32', use_last_error=True)
kernel.AttachConsole.argtypes = [wintypes.DWORD]
kernel.SetConsoleCtrlHandler.argtypes = [ctypes.c_void_p, wintypes.BOOL]
kernel.GenerateConsoleCtrlEvent.argtypes = [wintypes.DWORD, wintypes.DWORD]
assert kernel.AttachConsole(int(sys.argv[1])), ctypes.WinError(ctypes.get_last_error())
try:
    assert kernel.SetConsoleCtrlHandler(None, True), ctypes.WinError(ctypes.get_last_error())
    # CTRL_C_EVENT addresses the selected console. This fresh console owns only this fixture.
    assert kernel.GenerateConsoleCtrlEvent(0, 0), ctypes.WinError(ctypes.get_last_error())
    time.sleep(.25)
finally:
    kernel.FreeConsole()
"""


@contextmanager
def ingest_fixture():
    requests = Queue()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/openapi/v1/ingest-tokens" and self.headers["Authorization"] == "Bearer fixture":
                value = {"code": 0, "data": {"token": FIXTURE_TOKEN, "expires_in": 300,
                         "space_id": "space", "agent_uuid": "agent", "endpoint": origin}}
            elif self.path == "/v1/traces" and self.headers["Authorization"] == "Bearer " + FIXTURE_TOKEN:
                requests.put(payload)
                value = {}
            else:
                self.send_error(401)
                return
            content = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    origin = f"http://127.0.0.1:{server.server_port}"
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        yield origin, requests
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join()


def install_script(profile, managed):
    scripts = FIXTURES.parents[1] / "scripts"
    sys.path.insert(0, str(scripts))
    import native_config

    loader = importlib.machinery.SourceFileLoader("connect_minimax_console", str(scripts / "connect"))
    installer = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(installer)
    installed = managed / "installed"
    installed.mkdir()
    shutil.copytree(LAUNCHER.parent / "dist", installed / "dist")
    shutil.copyfile(LAUNCHER.parent / "package.json", installed / "package.json")
    plugin = {"key": "minimax-code", "host": NODE, "node": NODE, "host_entry": str(HOST)}
    with native_config.ConfigTransaction() as transaction:
        installer.register_native(SimpleNamespace(agent="minimax-code"), plugin, profile, installed,
                                  managed, managed, None, transaction, native_config)
    return installer.minimax_launcher_path(managed)


def read_events(path):
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf8").split("\n")[:-1]]


@unittest.skipUnless(os.name == "nt" and NODE, "native Windows console and Node.js required")
class WindowsMiniMaxConsoleTests(unittest.TestCase):
    def capture(self, scenario, via_powershell=False):
        with tempfile.TemporaryDirectory(prefix="aiv-mcode-console-") as directory, ingest_fixture() as (origin, requests):
            root = Path(directory)
            profile = root / "profile with spaces and an apostrophe '"
            managed = profile / "aivision" / "minimax-code"
            managed.mkdir(parents=True)
            config = managed / "ingest.json"
            config.write_text(json.dumps({"collection_mode": "privacy", "agent_uuid": "agent", "space_id": "space",
                              "agent_credential": "fixture", "endpoint": origin, "base_url": origin}), encoding="utf8")
            events_path = root / "fixture-events.jsonl"
            env = os.environ.copy()
            for name in ("AIVISION_COLLECTION_MODE", "MINIMAX_CODE_COLLECTION_MODE", "NODE_OPTIONS",
                         "MINIMAX_DATA_DIR", "MAVIS_DATA_DIR"):
                env.pop(name, None)
            env["AIVISION_MINIMAX_FIXTURE_EVENTS"] = str(events_path)
            if via_powershell:
                script = install_script(profile, managed)
                command = [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), scenario]
            else:
                command = [NODE, str(LAUNCHER), "--ingest-config", str(config), "--mcode", NODE,
                           "--mcode-entry", str(HOST), "--profile-dir", str(profile), "--", scenario]
            process = subprocess.Popen(
                command, creationflags=subprocess.CREATE_NEW_CONSOLE, cwd=root, env=env,
            )
            try:
                deadline = time.monotonic() + 12
                while len(events := read_events(events_path)) < 2 and time.monotonic() < deadline:
                    self.assertIsNone(process.poll(), "Launcher exited before the synthetic host started")
                    time.sleep(.05)
                self.assertGreaterEqual(len(events), 2, "Synthetic host did not start")
                ready, running = events[:2]
                self.assertEqual(ready["type"], "turn.started")
                if scenario == "tool":
                    self.assertEqual(running["item"]["toolCall"]["status"], 1)
                else:
                    self.assertEqual(running["item"]["contentDelta"], "partial output")
                subprocess.run([sys.executable, "-c", SEND_CTRL_C, str(process.pid)],
                               creationflags=subprocess.DETACHED_PROCESS, check=True,
                               timeout=10, capture_output=True)
                code = process.wait(timeout=12)
                # pwsh -File can return 0 when its own console run is interrupted.
                self.assertIn(code, {0, 130} if via_powershell else {130})
                report = requests.get(timeout=5)
                _, _, turn, execution = read_events(events_path)
                self.assertEqual(turn["status"], "cancelled")
                self.assertEqual(execution["result"]["status"], "cancelled")
                deadline = time.monotonic() + 5
                while list(managed.glob("exec-*")) and time.monotonic() < deadline:
                    time.sleep(.05)
                self.assertEqual(list(managed.glob("exec-*")), [])
                spans = report["resourceSpans"][0]["scopeSpans"][0]["spans"]
                return [{item["key"]: next(iter(item["value"].values())) for item in span["attributes"]}
                        for span in spans]
            except Empty:
                self.fail("MiniMax Code fixture did not produce its expected stream or Trace")
            finally:
                if process.poll() is None:
                    # The fixture creates its own console; a waiting failure cannot leak its child.
                    system_taskkill = Path(os.environ["SystemRoot"]) / "System32" / "taskkill.exe"
                    subprocess.run([str(system_taskkill), "/PID", str(process.pid), "/T", "/F"],
                                   creationflags=subprocess.CREATE_NO_WINDOW, capture_output=True, timeout=8)
                    process.wait(timeout=5)

    def test_cancel_model_while_streaming(self):
        root, model = self.capture("model")
        self.assertEqual(root["error.type"], "cancelled")
        self.assertEqual(model["error.type"], "cancelled")
        self.assertEqual(model["agentic.usage.input_tokens.missing"], "true")
        self.assertEqual(model["agentic.usage.output_tokens.missing"], "true")

    def test_cancel_running_tool_preserves_committed_model_usage(self):
        root, model, tool = self.capture("tool")
        self.assertEqual(root["error.type"], "cancelled")
        self.assertNotIn("error.type", model)
        self.assertEqual(model["gen_ai.usage.input_tokens"], "51")
        self.assertEqual(model["gen_ai.usage.output_tokens"], "9")
        self.assertEqual(tool["gen_ai.tool.name"], "bash")
        self.assertEqual(tool["error.type"], "cancelled")

    @unittest.skipUnless(POWERSHELL, "PowerShell 7 is required for the managed launcher test")
    def test_cancel_through_managed_powershell_launcher(self):
        root, model, tool = self.capture("tool", via_powershell=True)
        self.assertEqual(root["error.type"], "cancelled")
        self.assertEqual(model["gen_ai.usage.input_tokens"], "51")
        self.assertEqual(tool["error.type"], "cancelled")


if __name__ == "__main__":
    unittest.main()
