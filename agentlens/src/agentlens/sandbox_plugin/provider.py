"""Lazy OpenShell 0.1.6 adapter and GET-only Docker cleanup readback."""

from __future__ import annotations

import contextlib
import importlib.metadata
import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from agentlens.sandbox_plugin.config import PluginSettings


@dataclass(frozen=True)
class ExecutionOutput:
    exit_code: int
    stdout: str
    stderr: str
    truncated: bool = False


class OpenShellProvider:
    """Provider credentials, workspace, worker argv and policy are operator-controlled."""

    def __init__(self, settings: PluginSettings):
        self.settings = settings
        self.policy = settings.load_policy()
        self._client = None
        self._lock = threading.Lock()
        self._cleanup_local = threading.local()

    @property
    def version(self) -> str:
        try:
            return importlib.metadata.version("openshell")
        except importlib.metadata.PackageNotFoundError:
            return "unavailable"

    def _new_client(self, timeout: float):
        from openshell import SandboxClient, TlsConfig

        if self.version != "0.1.6":
            raise ValueError("Unsupported optional provider version")
        settings = self.settings
        tls = None
        if settings.tls_ca or settings.tls_cert:
            tls = TlsConfig(
                ca_path=Path(settings.tls_ca) if settings.tls_ca else None,
                cert_path=Path(settings.tls_cert) if settings.tls_cert else None,
                key_path=Path(settings.tls_key) if settings.tls_key else None,
            )
        return SandboxClient(
            settings.gateway,
            tls=tls,
            bearer_token=settings.gateway_token or None,
            timeout=timeout,
        )

    def _get_client(self):
        cleanup_client = getattr(self._cleanup_local, "client", None)
        if cleanup_client is not None:
            remaining = self._cleanup_local.deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            # SDK 0.1.6 has one per-client RPC timeout. This client belongs only to
            # the cleanup thread, so tightening it cannot affect a live execution.
            cleanup_client._timeout = min(self.settings.operation_timeout_seconds, remaining)
            return cleanup_client
        if self._client is None:
            with self._lock:
                if self._client is None:
                    self._client = self._new_client(self.settings.operation_timeout_seconds)
        return self._client

    @contextlib.contextmanager
    def cleanup_budget(self, deadline: float):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        client = self._new_client(min(self.settings.operation_timeout_seconds, remaining))
        self._cleanup_local.client, self._cleanup_local.deadline = client, deadline
        try:
            yield
        finally:
            del self._cleanup_local.client
            del self._cleanup_local.deadline
            client.close()

    def ready(self) -> bool:
        # Read one authenticated page; unauthenticated gateway Health alone is insufficient.
        next(iter(self._get_client().list(workspace=self.settings.workspace, page_size=1)))
        return True

    def create(self, name: str, request: dict, labels: dict):
        from openshell._proto import openshell_pb2, sandbox_pb2

        client = self._get_client()
        templates = client.sandbox_templates()
        # Deterministic per-lease templates are never reused across Tasks.
        try:
            template = templates.get(name, workspace=self.settings.workspace)
        except Exception as error:
            if not self._not_found(error):
                raise
            template = templates.create(
                workspace=self.settings.workspace,
                name=name,
                image=request["image"],
                cpu=str(request["cpu"]),
                memory=request["memory"],
                labels=labels,
            )
        self._validate_template(template, name, request, labels)
        policy = self.policy
        sandbox_policy = sandbox_pb2.SandboxPolicy(
            version=1,
            filesystem=sandbox_pb2.FilesystemPolicy(**policy["filesystem_policy"]),
            landlock=sandbox_pb2.LandlockPolicy(**policy["landlock"]),
            process=sandbox_pb2.ProcessPolicy(**policy["process"]),
        )
        return client.create_from_template(
            workspace=self.settings.workspace,
            workload_template=name,
            name=name,
            labels=labels,
            spec=openshell_pb2.SandboxSpec(
                command=["/bin/sleep", "infinity"],
                policy=sandbox_policy,
            ),
        )

    @staticmethod
    def _validate_template(template: Any, name: str, request: dict, labels: dict) -> None:
        workload = template.spec.workload
        if (
            template.metadata.name != name
            or any(template.metadata.labels.get(key) != value for key, value in labels.items())
            or workload.image != request["image"]
            or workload.resources.cpu != str(request["cpu"])
            or workload.resources.memory != request["memory"]
            or dict(workload.environment)
            or dict(template.spec.driver_config)
        ):
            raise ValueError("Provider template identity is invalid")

    @staticmethod
    def _not_found(error: Exception) -> bool:
        import grpc

        return isinstance(error, grpc.RpcError) and error.code() == grpc.StatusCode.NOT_FOUND

    def find(self, name: str):
        try:
            return self._get_client().get(name, workspace=self.settings.workspace)
        except Exception as error:
            if self._not_found(error):
                return None
            raise

    def wait_ready(self, name: str, timeout_seconds: float):
        return self._get_client().wait_ready(
            name,
            workspace=self.settings.workspace,
            timeout_seconds=timeout_seconds,
        )

    def execute(
        self,
        name: str,
        command: list[str],
        stdin: bytes,
        timeout_seconds: int,
        max_output_bytes: int,
    ) -> ExecutionOutput:
        """Stop consuming before SDK 0.1.6 accumulates unbounded stdout/stderr."""
        stream = self._execution_stream(
            name,
            command,
            workspace=self.settings.workspace,
            stdin=stdin,
            timeout_seconds=timeout_seconds,
            no_login_shell=True,
        )
        stdout, stderr = bytearray(), bytearray()
        try:
            for item in stream:
                if hasattr(item, "stream"):
                    target = stdout if item.stream == "stdout" else stderr
                    remaining = max_output_bytes - len(stdout) - len(stderr)
                    target.extend(item.data[:remaining])
                    if len(item.data) > remaining:
                        return ExecutionOutput(
                            125,
                            stdout.decode("utf-8", errors="ignore"),
                            stderr.decode("utf-8", errors="ignore"),
                            truncated=True,
                        )
                elif type(item.exit_code) is int:
                    return ExecutionOutput(
                        item.exit_code,
                        stdout.decode("utf-8", errors="replace"),
                        stderr.decode("utf-8", errors="replace"),
                    )
            raise ValueError("Provider execution did not complete")
        finally:
            stream.close()

    def validate_input(self, name, command, stdin, timeout_seconds) -> dict:
        from agentlens.sandbox_plugin.openshell_exec import prepare

        _, info = prepare(
            name, command, stdin, workspace=self.settings.workspace, timeout_seconds=timeout_seconds
        )
        return info

    def _execution_stream(self, *args, **kwargs):
        from agentlens.sandbox_plugin.openshell_exec import stream

        return stream(self._get_client(), *args, **kwargs)

    def delete(self, name: str, sandbox_id: str) -> None:
        client = self._get_client()
        current = self.find(name)
        if current is not None and current.id != sandbox_id:
            raise ValueError("Provider identity changed")
        deletion = client.delete(name, workspace=self.settings.workspace, allow_missing=True)
        if deletion.sandbox_id is not None and deletion.sandbox_id != sandbox_id:
            raise ValueError("Provider deletion identity changed")

    def wait_deleted(
        self,
        name: str,
        sandbox_id: str,
        timeout_seconds: float,
        *,
        labels: dict[str, str],
    ) -> None:
        deadline = min(
            time.monotonic() + timeout_seconds,
            getattr(self._cleanup_local, "deadline", float("inf")),
        )
        # SDK 0.1.6 keeps one fixed RPC timeout inside its polling helper and
        # sleeps a full second after each lookup. Refresh the cleanup client's
        # remaining budget on every lookup instead of resetting it per poll.
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError
            current = self.find(name)
            if current is None or current.id != sandbox_id:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            time.sleep(min(1, remaining))
        templates = self._get_client().sandbox_templates()
        try:
            template = templates.get(name, workspace=self.settings.workspace)
        except Exception as error:
            if self._not_found(error):
                return
            raise
        if template.metadata.name != name or any(
            template.metadata.labels.get(key) != value for key, value in labels.items()
        ):
            raise ValueError("Provider template ownership is unknown")
        self._get_client().sandbox_templates().delete(
            name,
            workspace=self.settings.workspace,
            allow_missing=True,
        )

    def close(self) -> None:
        if self._client is not None:
            self._client.close()


class DockerCleanupVerifier:
    """No Docker mutation: only bounded GETs over the trusted gateway daemon UDS."""

    def __init__(self, settings: PluginSettings, *, transport: httpx.BaseTransport | None = None):
        self._operation_timeout = settings.operation_timeout_seconds
        self._client = httpx.Client(
            transport=transport or httpx.HTTPTransport(uds=settings.docker_socket),
            base_url="http://docker",
            timeout=settings.operation_timeout_seconds,
            trust_env=False,
            follow_redirects=False,
        )

    def _get(self, path: str, *, deadline: float | None = None) -> Any:
        deadline = deadline or time.monotonic() + self._operation_timeout
        timeout = self._operation_timeout
        if deadline is not None:
            timeout = min(timeout, deadline - time.monotonic())
            if timeout <= 0:
                raise TimeoutError
        with self._client.stream("GET", path, timeout=timeout) as response:
            response.raise_for_status()
            content = bytearray()
            for chunk in response.iter_bytes():
                if deadline is not None and deadline <= time.monotonic():
                    raise TimeoutError
                content.extend(chunk)
                if len(content) > 8 * 1024**2:
                    raise ValueError("Docker readback exceeds the bound")
        return json.loads(content)

    def image_metadata(self, image: str) -> dict:
        """Inspect the exact allowlisted reference on the same trusted Docker daemon."""
        value = self._get("/images/" + quote(image, safe="") + "/json")
        if not isinstance(value, dict):
            raise ValueError("Invalid Docker image readback")
        return value

    def _daemon_id(self, *, deadline: float) -> str | None:
        info = self._get("/info", deadline=deadline)
        identity = info.get("ID") if isinstance(info, dict) else None
        if not isinstance(identity, str) or not identity or len(identity) > 256:
            return None
        return identity

    def ready(self) -> bool:
        deadline = time.monotonic() + self._operation_timeout
        identity = self._daemon_id(deadline=deadline)
        containers = self._get("/containers/json?all=1", deadline=deadline)
        volumes = self._get("/volumes", deadline=deadline)
        return (
            bool(identity)
            and isinstance(containers, list)
            and isinstance(volumes, dict)
            and "Volumes" in volumes
        )

    def observe_presence(self, sandbox_id: str, name: str, *, deadline: float) -> str | None:
        """Bind a lease to the daemon actually hosting its immutable provider ID."""
        identity = self._daemon_id(deadline=deadline)
        if not identity or not sandbox_id or not name:
            return None
        containers = self._get("/containers/json?all=1", deadline=deadline)
        if not isinstance(containers, list):
            return None
        matching = any(
            isinstance(resource, dict)
            and isinstance(resource.get("Labels"), dict)
            and resource["Labels"].get("openshell.ai/sandbox-id") == sandbox_id
            and resource["Labels"].get("openshell.ai/sandbox-name") == name
            for resource in containers
        )
        if not matching or self._daemon_id(deadline=deadline) != identity:
            return None
        return identity

    def verify_absent(
        self,
        sandbox_id: str,
        name: str,
        *,
        daemon_id: str | None = None,
        deadline: float | None = None,
    ) -> bool:
        deadline = deadline or time.monotonic() + self._operation_timeout
        if not sandbox_id or not name or not daemon_id:
            return False
        if self._daemon_id(deadline=deadline) != daemon_id:
            return False
        containers = self._get("/containers/json?all=1", deadline=deadline)
        volumes = self._get("/volumes", deadline=deadline)
        if not isinstance(containers, list) or not isinstance(volumes, dict):
            return False
        if "Volumes" not in volumes or volumes.get("Warnings"):
            return False
        volume_rows = volumes["Volumes"]
        if volume_rows is None:
            volume_rows = []
        if not isinstance(volume_rows, list):
            return False
        for resource in containers + volume_rows:
            if not isinstance(resource, dict):
                return False
            labels = resource.get("Labels") or {}
            if not isinstance(labels, dict):
                return False
            if sandbox_id in labels.values() or name in labels.values():
                return False
            names = resource.get("Names", []) + [resource.get("Name", "")]
            if any(not isinstance(value, str) for value in names):
                return False
            if any(sandbox_id in value or name in value for value in names):
                return False
        return self._daemon_id(deadline=deadline) == daemon_id

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._client.close()
