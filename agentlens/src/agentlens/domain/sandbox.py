"""Space-owned sandbox profiles and safe execution errors."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from agentlens.domain.errors import DomainError

DEFAULT_INPUT_BYTES = 8 * 1024**2
MAX_INPUT_BYTES = 16 * 1024**2
LEGACY_INPUT_BYTES = 2 * 1024**2
INPUT_LIMIT_LABEL = "io.aivision.sandbox.max-input-bytes"


class SandboxStatus(StrEnum):
    UNTESTED = "untested"
    READY = "ready"
    UNAVAILABLE = "unavailable"
    DISABLED = "disabled"


_MESSAGES = {
    "sandbox_configuration_invalid": "Sandbox configuration is invalid.",
    "sandbox_required": "This Space requires sandbox execution.",
    "sandbox_connection_missing": "Sandbox connection is missing in this Space.",
    "sandbox_identity_changed": "Sandbox connection no longer matches the frozen identity.",
    "sandbox_unavailable": "Sandbox connection is not ready.",
    "sandbox_authentication_failed": "Sandbox plugin authentication failed.",
    "sandbox_protocol_error": "Sandbox plugin returned an invalid response.",
    "sandbox_input_limit": "Sandbox input exceeds the supported limit.",
    "sandbox_output_limit": "Sandbox output exceeds the supported limit.",
    "sandbox_transport_error": "Sandbox plugin request failed.",
    "sandbox_plugin_upgrade_required": (
        "Update the sandbox plugin to support execution input preflight."
    ),
    "sandbox_cleanup_pending": "Sandbox cleanup could not be verified.",
    "sandbox_lease_conflict": "Sandbox lease cannot be executed again.",
    "sandbox_attempt_rejected": "Sandbox execution no longer owns the active Task attempt.",
    "exec_timeout": "Sandbox execution timed out.",
    "sandbox_runtime_required": "Enhanced Python requires a compatible sandbox.",
    "sandbox_runtime_unavailable": "Sandbox image or runtime manifest is incompatible.",
    "rule_test_harness_error": "Reference test configuration or report is invalid.",
    "rule_test_incomplete": "Reference verification did not complete all required tests.",
    "rule_timeout": "Enhanced Python execution timed out.",
    "rule_output_too_large": "Enhanced Python output exceeds the supported limit.",
    "rule_runtime_unavailable": "The advertised Python runtime is unavailable.",
    "rule_validation_error": "Custom Rule validation failed.",
    "rule_runtime_error": "Custom Rule execution failed.",
}


def sandbox_error_message(code: str) -> str:
    return _MESSAGES.get(code, _MESSAGES["sandbox_protocol_error"])


def input_limit_details(value: Any) -> dict[str, Any]:
    """Only bounded numeric facts and fixed stages may cross the provider boundary."""
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("stage"), str)
        or value["stage"]
        not in {
            "request",
            "worker_stdin",
            "grpc_message",
        }
    ):
        return {}
    if any(
        type(value.get(key)) is not int or not 0 <= value[key] < 2**63
        for key in ("actual_bytes", "allowed_bytes")
    ):
        return {}
    return {key: value[key] for key in ("stage", "actual_bytes", "allowed_bytes")}


class SandboxError(DomainError):
    def __init__(self, code: str, *, details: Any = None) -> None:
        self.code = code if code in _MESSAGES else "sandbox_protocol_error"
        self.failure_code = self.code
        self.details = input_limit_details(details) if self.code == "sandbox_input_limit" else {}
        message = sandbox_error_message(self.code)
        if self.details:
            message += (
                f" Received {self.details['actual_bytes']} bytes; "
                f"limit {self.details['allowed_bytes']} bytes "
                f"({self.details['stage']}). Reduce the complete execution input."
            )
        super().__init__(message)


@dataclass
class SandboxConnection:
    uuid: str
    space_uuid: str
    name: str
    provider: str
    endpoint: str
    api_key: str = field(repr=False)
    image: str
    cpu: int
    memory: str
    timeout_seconds: int
    config_revision: int
    status: SandboxStatus
    gmt_create: datetime
    gmt_modified: datetime
    creator: str | None = None
    modifier: str | None = None

    def frozen_profile(self) -> dict[str, Any]:
        return {
            "mode": "sandbox",
            "protocol_version": 1,
            "connection_uuid": self.uuid,
            **{key: getattr(self, key) for key in PROFILE_FIELDS},
        }


PROFILE_FIELDS = (
    "space_uuid",
    "provider",
    "endpoint",
    "image",
    "cpu",
    "memory",
    "timeout_seconds",
    "config_revision",
)


@dataclass
class SandboxBinding:
    space_uuid: str
    connection_uuid: str | None = None
    require_sandbox: bool = False
    modifier: str | None = None


@dataclass(frozen=True)
class SandboxExecutionContext:
    frozen_profile: dict[str, Any]
    space_uuid: str
    task_uuid: str
    attempt: int
    diagnoser_uuid: str
    execution_uuid: str | None = None
    timeout_seconds: float | None = None
    worker_uuid: str | None = None
    rule_runtime: str | None = None
    rule_runtime_manifest: str | None = None
    materials: dict[str, Any] | None = None


def validate_sandbox_policy(value: Any) -> dict[str, Any]:
    """Public policy cannot supply a frozen endpoint, resource identity or credentials."""
    if value is None:
        return {}
    if not isinstance(value, dict) or set(value) - {"mode", "connection_uuid"}:
        raise SandboxError("sandbox_configuration_invalid")
    mode, uuid = value.get("mode", "inherit"), value.get("connection_uuid")
    if (
        not isinstance(mode, str)
        or mode not in {"inherit", "required", "local"}
        or (uuid is not None and (not isinstance(uuid, str) or not uuid.strip() or len(uuid) > 128))
        or (mode == "local" and uuid is not None)
    ):
        raise SandboxError("sandbox_configuration_invalid")
    return dict(value)
