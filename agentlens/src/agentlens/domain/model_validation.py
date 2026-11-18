"""Finite, secret-free basic diagnostic validation contract."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Reason = Literal[
    "authentication_failed",
    "permission_denied",
    "model_not_found",
    "rate_limited",
    "quota_exceeded",
    "timeout",
    "connection_failed",
    "invalid_request",
    "unavailable",
    "invalid_diagnostic_output",
    "unexpected_verdict",
    "output_limit_reached",
    "missing_required_evidence",
    "budget_exhausted",
]
ReasoningEffort = (
    Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"]
    | Annotated[int, Field(ge=1, le=100)]
)


class SafeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class ValidationOptions(SafeModel):
    max_tokens: int = Field(default=4096, ge=256, le=8192)
    enable_thinking: bool | None = None
    temperature: float = Field(default=0, ge=0, le=2)
    reasoning_effort: ReasoningEffort | None = None
    request_timeout_sec: float = Field(default=45, ge=10, le=60)


class EffectiveValidationOptions(SafeModel):
    # null records an option omitted by the official Anthropic compatibility path.
    max_tokens: int = Field(ge=256, le=8192)
    enable_thinking: bool | None
    temperature: float | None = Field(ge=0, le=2)
    reasoning_effort: ReasoningEffort | None
    request_timeout_sec: float = Field(ge=10, le=60)


class ValidationProfile(SafeModel):
    builtin_key: Literal["task_completion"]
    canonical_variant: Literal["text_response"]
    definition_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    generation_options: EffectiveValidationOptions


class ValidationCase(SafeModel):
    name: Literal["positive", "negative"]
    status: Literal["passed", "failed", "incomplete", "not_run"]
    reason: Reason | None
    expected: Literal["good", "bad"]
    actual: Literal["good", "bad"] | None

    @model_validator(mode="after")
    def consistent(self):
        if self.expected != ("good" if self.name == "positive" else "bad"):
            raise ValueError("invalid case expectation")
        if self.status == "passed" and (self.actual != self.expected or self.reason is not None):
            raise ValueError("invalid passed case")
        if self.status != "passed" and self.reason is None:
            raise ValueError("failure reason required")
        return self


class ValidationUsage(SafeModel):
    requests: int = Field(ge=0, le=2)
    input_tokens: int | None = Field(ge=0, le=2**64 - 2)
    output_tokens: int | None = Field(ge=0, le=2**64 - 2)
    total_tokens: int | None = Field(ge=0, le=2**64 - 2)


class DiagnosticValidation(SafeModel):
    status: Literal["passed", "failed", "incomplete"]
    reason: Reason | None
    validated_at: str = Field(max_length=40)
    profile: ValidationProfile
    cases: list[ValidationCase] = Field(min_length=2, max_length=2)
    usage: ValidationUsage

    @field_validator("validated_at")
    @classmethod
    def valid_timestamp(cls, value):
        datetime.fromisoformat(value)
        return value

    @model_validator(mode="after")
    def consistent(self):
        if [case.name for case in self.cases] != ["positive", "negative"]:
            raise ValueError("two ordered canonical cases required")
        expected = (
            "incomplete"
            if any(c.status in {"incomplete", "not_run"} for c in self.cases)
            else "failed"
            if any(c.status == "failed" for c in self.cases)
            else "passed"
        )
        if self.status != expected or (self.status == "passed") != (self.reason is None):
            raise ValueError("inconsistent validation summary")
        if self.status == "passed" and self.usage.requests != 2:
            raise ValueError("passed validation requires both requests")
        return self
