"""Finite developer check requirements, separate from observed Trace facts."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class StrictPolicyModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class AnswerRelevancePolicy(StrictPolicyModel):
    kind: Literal["answer_relevance_v1"]
    context_mode: Literal["explicit_current_text", "recorded_conversation"]


class FollowupPolicy(StrictPolicyModel):
    kind: Literal["followup_reasonableness_v1"]
    context_mode: Literal["recorded_conversation"]


class ExpressionSelector(StrictPolicyModel):
    field: Literal["current_request"]
    prefix: str = Field(max_length=128)
    suffix: str = Field(max_length=128)


class ResultSelector(StrictPolicyModel):
    field: Literal["final_output"]
    prefix: str = Field(max_length=128)
    suffix: str = Field(max_length=128)


class ExactPrecision(StrictPolicyModel):
    mode: Literal["exact"]


class QuantizedPrecision(StrictPolicyModel):
    mode: Literal["quantized"]
    scale: int = Field(ge=0, le=12)
    rounding: Literal["ROUND_HALF_EVEN", "ROUND_HALF_UP", "ROUND_DOWN"]


class NumericCheck(StrictPolicyModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    expression: ExpressionSelector
    result: ResultSelector
    precision: Annotated[ExactPrecision | QuantizedPrecision, Field(discriminator="mode")]
    unit: str | None

    @model_validator(mode="after")
    def validate_unit(self):
        if self.unit is not None and (
            not 1 <= len(self.unit) <= 16 or any(c.isspace() for c in self.unit)
        ):
            raise ValueError("invalid numeric unit")
        return self


class NumericPolicy(StrictPolicyModel):
    kind: Literal["numeric_consistency_v1"]
    checks: list[NumericCheck] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def unique_checks(self):
        if len({check.id for check in self.checks}) != len(self.checks):
            raise ValueError("duplicate numeric check id")
        return self


class LanguagePolicy(StrictPolicyModel):
    kind: Literal["response_language_v1"]
    required_language: Literal["zh-CN", "zh-TW", "en", "ja", "ko", "fr", "de", "es"]
    scope: Literal["final_natural_language"]
    allowed_terms: list[Annotated[str, Field(min_length=1, max_length=80)]] = Field(max_length=32)

    @model_validator(mode="after")
    def bounded_terms(self):
        if (
            len(set(self.allowed_terms)) != len(self.allowed_terms)
            or sum(len(term) for term in self.allowed_terms) > 2048
        ):
            raise ValueError("duplicate or oversized allowed terms")
        return self


class ProtectedMarker(StrictPolicyModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    value: str = Field(min_length=1, max_length=256, repr=False)
    visibility: Literal["protected", "public"]


class ProtectedPolicy(StrictPolicyModel):
    kind: Literal["protected_content_exposure_v1"]
    scope: Literal["final_visible_text"]
    match: Literal["literal_exact"]
    markers: list[ProtectedMarker] = Field(min_length=1, max_length=64, repr=False)

    @model_validator(mode="after")
    def validate_markers(self):
        if len({m.id for m in self.markers}) != len(self.markers) or len(
            {m.value for m in self.markers}
        ) != len(self.markers):
            raise ValueError("duplicate marker identity")
        if (
            any(not m.value.strip() for m in self.markers)
            or sum(len(m.value) for m in self.markers) > 8192
        ):
            raise ValueError("invalid marker size")
        protected = [m.value for m in self.markers if m.visibility == "protected"]
        public = [m.value for m in self.markers if m.visibility == "public"]
        if not protected or any(a in b or b in a for a in protected for b in public):
            raise ValueError("invalid protected/public boundary")
        return self


class RequiredJsonField(StrictPolicyModel):
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")
    type: Literal["string", "number", "integer", "boolean", "object", "array", "null"]


class StructuredOutputPolicy(StrictPolicyModel):
    kind: Literal["structured_output_contract_v1"]
    fields: list[RequiredJsonField] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def unique_fields(self):
        if len({field.name for field in self.fields}) != len(self.fields):
            raise ValueError("duplicate required JSON field")
        return self


class RequiredToolPolicy(StrictPolicyModel):
    kind: Literal["required_tool_execution_v1"]
    tool_name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")


SpecialistPolicy = Annotated[
    AnswerRelevancePolicy
    | FollowupPolicy
    | NumericPolicy
    | LanguagePolicy
    | ProtectedPolicy
    | StructuredOutputPolicy
    | RequiredToolPolicy,
    Field(discriminator="kind"),
]


class SpecialistLlmOptions(StrictPolicyModel):
    enable_thinking: bool | None = False
    temperature: int | float = 0
    reasoning_effort: str | int | None = None
    max_tokens: int = Field(default=4096, ge=1, le=8192)
    request_timeout_sec: int | float = 60

    @model_validator(mode="after")
    def finite_options(self):
        from agentlens.domain.llm_options import REASONING_EFFORTS

        if (
            type(self.temperature) not in (int, float)
            or not math.isfinite(self.temperature)
            or not 0 <= self.temperature <= 2
        ):
            raise ValueError("invalid temperature")
        if (
            type(self.request_timeout_sec) not in (int, float)
            or not math.isfinite(self.request_timeout_sec)
            or not 0 < self.request_timeout_sec <= 300
        ):
            raise ValueError("invalid request timeout")
        if "reasoning_effort" in self.model_fields_set:
            effort = self.reasoning_effort
            if not (
                isinstance(effort, str)
                and effort in REASONING_EFFORTS
                or type(effort) is int
                and 1 <= effort <= 100
            ):
                raise ValueError("invalid reasoning effort")
        return self


class NormalizedSpecialistConfiguration(StrictPolicyModel):
    policy: SpecialistPolicy = Field(repr=False)
    model_connection_uuid: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    llm_options: SpecialistLlmOptions | None = None


def canonical_json(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def canonical_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _depth(value: Any) -> int:
    if isinstance(value, dict):
        return 1 + max((_depth(v) for v in value.values()), default=0)
    if isinstance(value, list):
        return 1 + max((_depth(v) for v in value), default=0)
    return 0


def configuration_dict(configuration: NormalizedSpecialistConfiguration) -> dict:
    value = {"policy": configuration.policy.model_dump()}
    if configuration.model_connection_uuid is not None:
        value["model_connection_uuid"] = configuration.model_connection_uuid
        options = configuration.llm_options.model_dump(exclude_none=True)
        # Explicit null thinking means omit provider controls, not use the default.
        options["enable_thinking"] = configuration.llm_options.enable_thinking
        value["llm_options"] = options
    return value


def normalize_specialist_configuration(
    builtin_key: str, canonical_variant: str, raw: dict | NormalizedSpecialistConfiguration
) -> NormalizedSpecialistConfiguration:
    value = configuration_dict(raw) if isinstance(raw, NormalizedSpecialistConfiguration) else raw
    if (
        not isinstance(value, dict)
        or _depth(value) > 6
        or len(canonical_json(value).encode("utf-8")) > 32768
    ):
        raise ValueError("invalid specialist configuration shape or size")
    try:
        configuration = NormalizedSpecialistConfiguration.model_validate(value)
    except ValidationError:
        raise ValueError("invalid specialist configuration") from None
    expected_kind = f"{builtin_key}_v1"
    if configuration.policy.kind != expected_kind:
        raise ValueError("specialist policy identity mismatch")
    if builtin_key in {"answer_relevance", "followup_reasonableness"}:
        if configuration.policy.context_mode != canonical_variant:
            raise ValueError("specialist context variant mismatch")
    elif builtin_key == "numeric_consistency":
        if canonical_variant not in {"decimal_binary", "decimal_binary_same_unit"} or any(
            (check.unit is None) != (canonical_variant == "decimal_binary")
            for check in configuration.policy.checks
        ):
            raise ValueError("specialist numeric variant mismatch")
    elif (builtin_key, canonical_variant) not in {
        ("response_language", "configured_final_text"),
        ("protected_content_exposure", "configured_exact_markers"),
        ("structured_output_contract", "json_object_fields"),
        ("required_tool_execution", "observed_tool_completion"),
    }:
        raise ValueError("unknown specialist identity")
    if builtin_key in {
        "numeric_consistency",
        "protected_content_exposure",
        "structured_output_contract",
        "required_tool_execution",
    }:
        if "model_connection_uuid" in value or "llm_options" in value:
            raise ValueError("deterministic specialist forbids model configuration")
    else:
        if not configuration.model_connection_uuid:
            raise ValueError("specialist model connection is required")
        if "llm_options" in value and value["llm_options"] is None:
            raise ValueError("specialist llm_options must be an object")
        if configuration.llm_options is None:
            configuration.llm_options = SpecialistLlmOptions()
    return configuration


def configuration_digest(configuration: NormalizedSpecialistConfiguration) -> str:
    return canonical_digest(configuration_dict(configuration))


def policy_summary(policy: dict) -> dict:
    """Private marker literals never occur in this public policy representation."""
    if policy.get("kind") != "protected_content_exposure_v1":
        return json.loads(canonical_json(policy))
    markers = policy.get("markers")
    markers = markers if isinstance(markers, list) else []
    digest = policy.get("policy_digest")
    if not (
        isinstance(digest, str)
        and len(digest) == 64
        and all(c in "0123456789abcdef" for c in digest)
    ):
        digest = canonical_digest(policy)
    return {
        "kind": policy["kind"],
        "scope": policy.get("scope"),
        "match": policy.get("match"),
        "policy_digest": digest,
        "markers": [
            {
                "id": m.get("id"),
                "visibility": m.get("visibility"),
                "value_configured": bool(m["value"])
                if isinstance(m.get("value"), str)
                else m.get("value_configured") is True,
                "value_length": len(m["value"])
                if isinstance(m.get("value"), str)
                else (
                    m["value_length"]
                    if type(m.get("value_length")) is int and 0 <= m["value_length"] <= 256
                    else 0
                ),
            }
            for m in markers
            if isinstance(m, dict)
        ],
    }
