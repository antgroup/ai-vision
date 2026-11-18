"""Browser-safe contract for the built-in model diagnostic validation."""

import math
import re
from datetime import datetime

VALIDATION_REASONS = frozenset(
    {
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
    }
)
_GENERATION_FIELDS = frozenset(
    {
        "max_tokens",
        "enable_thinking",
        "temperature",
        "reasoning_effort",
        "request_timeout_sec",
    }
)
_REASONING_EFFORTS = frozenset(
    {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
)


def validation_generation_options(value):
    """Validate only supplied fields; AgentLens owns defaults and persistence."""
    if not isinstance(value, dict) or value.keys() - _GENERATION_FIELDS:
        raise ValueError("invalid_request")
    for key, option in value.items():
        if key == "max_tokens":
            valid = type(option) is int and 256 <= option <= 8192
        elif key == "enable_thinking":
            valid = option is None or type(option) is bool
        elif key == "reasoning_effort":
            valid = (
                option is None
                or (type(option) is str and option in _REASONING_EFFORTS)
                or (type(option) is int and 1 <= option <= 100)
            )
        else:
            lower, upper = (0, 2) if key == "temperature" else (10, 60)
            valid = (
                type(option) in (int, float)
                and lower <= option <= upper
                and math.isfinite(option)
            )
        if not valid:
            raise ValueError("invalid_request")
    return dict(value)


def _safe_reason(value):
    if value is None:
        return None
    return (
        value
        if isinstance(value, str) and value in VALIDATION_REASONS
        else "unavailable"
    )


def clean_diagnostic_validation(value):
    """Discard malformed reports and exclude free-form provider output at every level."""
    if not isinstance(value, dict) or value.get("status") not in (
        "passed",
        "failed",
        "incomplete",
    ):
        return None
    timestamp = value.get("validated_at")
    if not isinstance(timestamp, str) or len(timestamp) > 64:
        return None
    try:
        datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    profile = value.get("profile")
    if (
        not isinstance(profile, dict)
        or profile.get("builtin_key") != "task_completion"
        or profile.get("canonical_variant") != "text_response"
        or not isinstance(profile.get("definition_digest"), str)
        or not re.fullmatch(r"[a-f0-9]{64}", profile["definition_digest"])
    ):
        return None
    options = profile.get("generation_options")
    if not isinstance(options, dict) or not _GENERATION_FIELDS <= options.keys():
        return None
    options = {key: options[key] for key in _GENERATION_FIELDS}
    # The official Anthropic compatibility path omits temperature. Reports
    # describe that effective value; requests still require a numeric value.
    temperature_omitted = options["temperature"] is None
    if temperature_omitted:
        options.pop("temperature")
    try:
        options = validation_generation_options(options)
    except ValueError:
        return None
    if temperature_omitted:
        options["temperature"] = None
    cases = value.get("cases")
    if not isinstance(cases, list) or len(cases) != 2:
        return None
    clean_cases = []
    for case, name, expected in zip(
        cases, ("positive", "negative"), ("good", "bad"), strict=True
    ):
        if (
            not isinstance(case, dict)
            or case.get("name") != name
            or case.get("expected") != expected
            or case.get("status") not in ("passed", "failed", "incomplete", "not_run")
            or case.get("actual") not in ("good", "bad", None)
        ):
            return None
        clean_cases.append(
            {
                "name": name,
                "status": case["status"],
                "reason": _safe_reason(case.get("reason")),
                "expected": expected,
                "actual": case.get("actual"),
            }
        )
    usage = value.get("usage")
    if (
        not isinstance(usage, dict)
        or type(usage.get("requests")) is not int
        or not 0 <= usage["requests"] <= 2
    ):
        return None
    clean_usage = {"requests": usage["requests"]}
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        count = usage.get(key)
        if count is not None and (type(count) is not int or count < 0):
            return None
        clean_usage[key] = count
    return {
        "status": value["status"],
        "reason": _safe_reason(value.get("reason")),
        "validated_at": timestamp,
        "profile": {
            "builtin_key": profile["builtin_key"],
            "canonical_variant": profile["canonical_variant"],
            "definition_digest": profile["definition_digest"],
            "generation_options": options,
        },
        "cases": clean_cases,
        "usage": clean_usage,
    }
