"""Operator-only configuration for the optional OpenShell process."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentlens.domain.sandbox import DEFAULT_INPUT_BYTES, MAX_INPUT_BYTES

PREFIX = "AGENTLENS_SANDBOX_PLUGIN_"


@dataclass(frozen=True)
class PluginSettings:
    token: str = field(repr=False)
    journal_path: Path
    image_allowlist: tuple[str, ...]
    policy_path: Path
    docker_socket: str
    gateway: str
    workspace: str = "default"
    worker_command: tuple[str, ...] = ("/app/src/agentlens-rule-worker",)
    gateway_token: str = field(default="", repr=False)
    enhanced_image: str = ""
    runtime_manifest_path: Path = Path("/app/rule-runtime/manifest.json")
    tls_ca: str = ""
    tls_cert: str = ""
    tls_key: str = field(default="", repr=False)
    max_active: int = 4
    lease_ttl_seconds: float = 300
    janitor_interval_seconds: float = 10
    operation_timeout_seconds: float = 10
    ready_timeout_seconds: float = 60
    cleanup_timeout_seconds: float = 30
    max_input_bytes: int = DEFAULT_INPUT_BYTES
    max_output_bytes: int = 1024 * 1024
    max_timeout_seconds: float = 300
    max_cpu: int = 64
    max_memory_bytes: int = 64 * 1024**3

    def __post_init__(self) -> None:
        import math

        if (
            not isinstance(self.token, str)
            or not self.token
            or len(self.token) > 8192
            or any(not 33 <= ord(char) <= 126 for char in self.token)
        ):
            raise ValueError("Plugin access token is required")
        if not self.gateway or not self.workspace or not self.docker_socket.startswith("/"):
            raise ValueError("Operator gateway, workspace and Docker socket are required")
        if not self.journal_path.is_absolute() or not self.policy_path.is_absolute():
            raise ValueError("Operator journal and policy paths must be absolute")
        if not self.image_allowlist or len(self.image_allowlist) > 128:
            raise ValueError("Operator workload image allowlist is required")
        if any(not isinstance(x, str) or not x or len(x) > 512 for x in self.image_allowlist):
            raise ValueError("Invalid operator image allowlist")
        if (
            not self.worker_command
            or len(self.worker_command) > 32
            or not self.worker_command[0].startswith("/")
            or any(not isinstance(x, str) or not x or len(x) > 4096 for x in self.worker_command)
        ):
            raise ValueError("Operator worker command must be a bounded absolute argv array")
        if self.enhanced_image:
            import re

            if self.enhanced_image not in self.image_allowlist or not re.fullmatch(
                r"(?:[^\s@]+@)?sha256:[a-f0-9]{64}", self.enhanced_image
            ):
                raise ValueError("Enhanced image must be an explicitly allowed immutable digest")
        if not self.runtime_manifest_path.is_absolute():
            raise ValueError("Runtime manifest path must be image-owned and absolute")
        for value in (
            self.lease_ttl_seconds,
            self.janitor_interval_seconds,
            self.operation_timeout_seconds,
            self.ready_timeout_seconds,
            self.cleanup_timeout_seconds,
            self.max_timeout_seconds,
        ):
            if not math.isfinite(value) or value <= 0 or value > 3600:
                raise ValueError("Invalid operator deadline")
        if not 1 <= self.max_active <= 256:
            raise ValueError("Invalid operator concurrency limit")
        if (
            type(self.max_input_bytes) is not int
            or not 1024 <= self.max_input_bytes <= MAX_INPUT_BYTES
        ):
            raise ValueError("Invalid operator input limit")
        if not 1024 <= self.max_output_bytes <= 16 * 1024**2:
            raise ValueError("Invalid operator output limit")
        if bool(self.tls_cert) != bool(self.tls_key):
            raise ValueError("Operator TLS certificate and key must be configured together")

    @classmethod
    def from_env(cls) -> PluginSettings:
        def required(key: str) -> str:
            value = os.environ.get(PREFIX + key, "")
            if not value:
                raise ValueError("Required plugin operator configuration is missing")
            return value

        def secret(key: str, *, mandatory: bool) -> str:
            raw = os.environ.get(PREFIX + key, "")
            filename = os.environ.get(PREFIX + key + "_FILE", "")
            if raw and filename:
                raise ValueError
            if filename:
                path = Path(filename)
                if not path.is_absolute() or not path.is_file():
                    raise ValueError
                with path.open("rb") as stream:
                    value = stream.read(8195)
                if len(value) > 8194:
                    raise ValueError
                raw = value.rstrip(b"\r\n").decode("ascii")
            if mandatory and not raw:
                raise ValueError
            if len(raw) > 8192 or any(not 33 <= ord(char) <= 126 for char in raw):
                raise ValueError
            return raw

        try:
            return cls(
                token=secret("TOKEN", mandatory=True),
                journal_path=Path(required("JOURNAL_PATH")),
                image_allowlist=tuple(json.loads(required("IMAGE_ALLOWLIST"))),
                policy_path=Path(required("POLICY_PATH")),
                docker_socket=required("DOCKER_SOCKET"),
                gateway=required("GATEWAY"),
                workspace=os.environ.get(PREFIX + "WORKSPACE", "default"),
                worker_command=tuple(
                    json.loads(
                        os.environ.get(
                            PREFIX + "WORKER_COMMAND", '["/app/src/agentlens-rule-worker"]'
                        )
                    )
                ),
                gateway_token=secret("GATEWAY_TOKEN", mandatory=False),
                enhanced_image=os.environ.get("AIV_SANDBOX_ENHANCED_IMAGE", ""),
                tls_ca=os.environ.get(PREFIX + "TLS_CA", ""),
                tls_cert=os.environ.get(PREFIX + "TLS_CERT", ""),
                tls_key=os.environ.get(PREFIX + "TLS_KEY", ""),
                **{
                    key: float(os.environ.get(PREFIX + key.upper(), str(default)))
                    for key, default in {
                        "lease_ttl_seconds": 300,
                        "janitor_interval_seconds": 10,
                        "operation_timeout_seconds": 10,
                        "ready_timeout_seconds": 60,
                        "cleanup_timeout_seconds": 30,
                        "max_timeout_seconds": 300,
                    }.items()
                },
                **{
                    key: int(os.environ.get(PREFIX + key.upper(), str(default)))
                    for key, default in {
                        "max_active": 4,
                        "max_input_bytes": DEFAULT_INPUT_BYTES,
                        "max_output_bytes": 1024**2,
                    }.items()
                },
            )
        except (ValueError, TypeError, KeyError, OSError):
            raise ValueError("Invalid plugin operator configuration") from None

    def load_policy(self) -> dict[str, Any]:
        """Accept an operator file, never a policy supplied over the HTTP API."""
        try:
            with self.policy_path.open("rb") as stream:
                data = stream.read(65537)
            if len(data) > 65536:
                raise ValueError
            try:
                policy = json.loads(data)
            except (ValueError, UnicodeDecodeError):
                import yaml  # Optional plugin dependency; base AgentLens does not load it.

                policy = yaml.safe_load(data)
            if not isinstance(policy, dict) or policy.get("version") != 1:
                raise ValueError
            allowed = {"version", "filesystem_policy", "landlock", "process", "network_policies"}
            if set(policy) - allowed or policy.get("network_policies") != {}:
                raise ValueError
            process = policy["process"]
            if set(process) != {"run_as_user", "run_as_group"}:
                raise ValueError
            for key in ("run_as_user", "run_as_group"):
                value = process[key]
                if (
                    not isinstance(value, str)
                    or not value.isdecimal()
                    or not 0 < int(value) < 65535
                ):
                    raise ValueError
            if policy.get("landlock") != {"compatibility": "hard_requirement"}:
                raise ValueError
            filesystem = policy["filesystem_policy"]
            if not isinstance(filesystem, dict) or set(filesystem) - {
                "include_workdir",
                "read_only",
                "read_write",
            }:
                raise ValueError
            for key in ("read_only", "read_write"):
                paths = filesystem.get(key, [])
                if not isinstance(paths, list) or len(paths) > 128:
                    raise ValueError
                if any(not isinstance(x, str) or not x.startswith("/") or x == "/" for x in paths):
                    raise ValueError
            return policy
        except Exception:
            raise ValueError(
                "Operator policy must enforce non-root deny-network isolation"
            ) from None
