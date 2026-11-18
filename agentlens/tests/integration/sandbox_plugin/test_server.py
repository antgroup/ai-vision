"""Upstream Uvicorn shuts down the standalone optional app without AgentLens DB startup."""

import json
import os
import signal
import socket
import subprocess
import sys
import time

import httpx


def test_standalone_server_sigterm_completes_lifespan_cleanup(tmp_path):
    policy = tmp_path / "policy.json"
    policy.write_text(
        json.dumps(
            {
                "version": 1,
                "filesystem_policy": {"read_only": ["/usr"], "read_write": ["/tmp"]},
                "process": {"run_as_user": "1000", "run_as_group": "1000"},
                "landlock": {"compatibility": "hard_requirement"},
                "network_policies": {},
            }
        )
    )
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    environment = os.environ.copy()
    values = {
        "TOKEN": "test-server-token",
        "JOURNAL_PATH": str(tmp_path / "server.sqlite3"),
        "IMAGE_ALLOWLIST": '["synthetic-worker:v1"]',
        "POLICY_PATH": str(policy),
        "DOCKER_SOCKET": "/tmp/nonexistent-docker.sock",
        "GATEWAY": "127.0.0.1:9999",
    }
    for key in list(environment):
        if key.startswith("AGENTLENS_SANDBOX_PLUGIN_"):
            environment.pop(key)
    environment.update({"AGENTLENS_SANDBOX_PLUGIN_" + key: value for key, value in values.items()})
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "agentlens.sandbox_plugin.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        with httpx.Client(trust_env=False, timeout=0.2) as client:
            for _ in range(100):
                if process.poll() is not None:
                    raise AssertionError(process.communicate()[1].decode())
                try:
                    if client.get(f"http://127.0.0.1:{port}/health/live").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.05)
            else:
                raise AssertionError("Plugin did not start")
        process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode in {0, -signal.SIGTERM}, (stdout, stderr)
        assert b"Application shutdown complete" in stderr
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
