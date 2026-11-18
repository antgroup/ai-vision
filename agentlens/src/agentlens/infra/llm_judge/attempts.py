"""Communication-only request hooks and safe per-response usage."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Protocol

from agentlens.domain.enums import ModelConnectionTestError


class AttemptRefused(RuntimeError):
    def __init__(self, historical_attempts: int):
        super().__init__("request attempt budget exhausted")
        self.historical_attempts = historical_attempts


class AttemptAuditError(RuntimeError):
    """No durable request permission or request accounting could be established."""


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    known_fields: tuple[str, ...] = ()

    @classmethod
    def from_response(cls, body: Any) -> TokenUsage:
        raw = body.get("usage") if isinstance(body, dict) else None
        raw = raw if isinstance(raw, dict) else {}
        values = {}
        for field, aliases in (
            ("input_tokens", ("prompt_tokens", "input_tokens")),
            ("output_tokens", ("completion_tokens", "output_tokens")),
            ("total_tokens", ("total_tokens",)),
        ):
            value = next((raw[k] for k in aliases if k in raw), None)
            if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 2**63 - 1:
                values[field] = value
        return cls(**values, known_fields=tuple(values))


@dataclass(frozen=True)
class AttemptObservation:
    outcome: str
    http_status: int | None = None
    failure_kind: str | None = None
    usage: TokenUsage = TokenUsage()
    error_code: ModelConnectionTestError | None = None
    output_limit_reached: bool = False

    def to_dict(self) -> dict:
        value = asdict(self)
        value["usage"]["known_fields"] = list(self.usage.known_fields)
        if self.error_code is None:
            value.pop("error_code")
        if not self.output_limit_reached:
            value.pop("output_limit_reached")
        return value


class AttemptHooks(Protocol):
    def claim(self) -> str: ...
    def dispatch(self, ticket: str) -> None: ...
    def finish(self, ticket: str, observation: AttemptObservation) -> None: ...


class TrialAttempts:
    """One bounded trial invocation; never creates or impersonates a Task."""

    def __init__(self) -> None:
        self.observations: list[AttemptObservation] = []
        self.reserved = 0

    def claim(self) -> str:
        if self.reserved >= 3:
            raise AttemptRefused(self.reserved)
        self.reserved += 1
        return str(self.reserved)

    def dispatch(self, ticket: str) -> None:
        return None

    def finish(self, ticket: str, observation: AttemptObservation) -> None:
        self.observations.append(observation)
