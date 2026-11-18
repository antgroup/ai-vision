"""Isolated provider collaborators; no optional SDK or database is required."""

import json
import threading
from types import SimpleNamespace

import pytest

from agentlens.sandbox_plugin.config import PluginSettings
from agentlens.sandbox_plugin.runtime import LeaseManager


class FakeProvider:
    version = "0.1.6"

    def __init__(self):
        self.sandboxes = {}
        self.calls = []
        self.fail_create = False
        self.fail_ready = False
        self.fail_delete = False
        self.fail_wait_deleted = False
        self.exec_error = False
        self.result = SimpleNamespace(
            exit_code=0, stdout='{"status":"ok","result":{}}\n', stderr="", truncated=False
        )
        self.entered = threading.Event()
        self.release = None
        self.stdin = None
        self.command = None
        self.deleted_processes = False

    def ready(self):
        return True

    def validate_input(self, name, command, stdin, timeout_seconds):
        return {"transport": "unary", "rpc_bytes": len(stdin) + 90}

    def create(self, name, request, labels):
        self.calls.append("create")
        ref = SimpleNamespace(id="immutable-" + name, name=name, labels=labels)
        self.sandboxes[name] = ref
        if self.fail_create:
            raise RuntimeError("provider credential SECRET")
        return ref

    def find(self, name):
        self.calls.append("find")
        return self.sandboxes.get(name)

    def wait_ready(self, name, timeout_seconds):
        self.calls.append("wait_ready")
        if self.fail_ready:
            raise RuntimeError("not ready SECRET")
        return self.sandboxes[name]

    def execute(self, name, command, stdin, timeout_seconds, max_output_bytes):
        self.calls.append("execute")
        self.stdin, self.command = stdin, command
        self.entered.set()
        if self.release:
            self.release.wait(5)
        if self.exec_error:
            raise RuntimeError("transport credentials SECRET")
        return self.result

    def delete(self, name, sandbox_id):
        self.calls.append("delete")
        if self.fail_delete:
            raise RuntimeError("delete credential SECRET")
        self.sandboxes.pop(name, None)
        self.deleted_processes = True

    def wait_deleted(self, name, sandbox_id, timeout_seconds, *, labels):
        self.calls.append("wait_deleted")
        if self.fail_wait_deleted:
            raise RuntimeError("wait credential SECRET")

    def close(self):
        pass


class FakeVerifier:
    def __init__(self):
        self.clear = True
        self.checked = []
        self.daemon_id = "test-daemon"
        self.present = True

    def ready(self):
        return True

    def observe_presence(self, sandbox_id, name, *, deadline):
        return self.daemon_id if self.present else None

    def verify_absent(self, sandbox_id, name, *, daemon_id=None, deadline=None):
        self.checked.append((sandbox_id, name))
        return self.clear and daemon_id == self.daemon_id

    def close(self):
        pass


@pytest.fixture
def setup(tmp_path):
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
    settings = PluginSettings(
        token="test-token",
        journal_path=tmp_path / "leases.sqlite3",
        image_allowlist=("test/worker:v1",),
        policy_path=policy,
        docker_socket="/test/docker.sock",
        gateway="127.0.0.1:9999",
        max_active=1,
        janitor_interval_seconds=100,
    )
    provider, verifier = FakeProvider(), FakeVerifier()
    manager = LeaseManager(settings, provider, verifier)
    yield settings, provider, verifier, manager
    manager.close()
