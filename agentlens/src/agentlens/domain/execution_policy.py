"""Validated server-owned execution policy for bounded quick/Core Tasks."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from agentlens.domain.diagnosis_input import validate_explicit_trace_ids


def bounded_core_execution_policy(trace_ids: list[str]) -> dict[str, Any]:
    policy = {
        "kind": "bounded_core_v1",
        "fixed_trace_ids": list(trace_ids),
        "request_budget": {"total": 30, "per_case": 3},
        "model_availability": "configured_same_space",
    }
    return validate_execution_policy(policy)


def bounded_diagnosis_execution_policy(trace_ids: list[str]) -> dict[str, Any]:
    return validate_execution_policy(
        {
            "kind": "bounded_diagnosis_v1",
            "fixed_trace_ids": list(trace_ids),
            "request_budget": {"total": 30, "per_case": 3},
            "model_availability": "available_same_space",
        }
    )


def validate_execution_policy(policy: Any) -> dict | None:
    if policy is None:
        return None
    if (
        not isinstance(policy, dict)
        or set(policy) != {"kind", "fixed_trace_ids", "request_budget", "model_availability"}
        or policy["kind"] not in ("bounded_core_v1", "bounded_diagnosis_v1")
        or policy["model_availability"]
        != (
            "configured_same_space"
            if policy["kind"] == "bounded_core_v1"
            else "available_same_space"
        )
        or policy["request_budget"] != {"total": 30, "per_case": 3}
    ):
        raise ValueError("unsupported bounded execution policy")
    try:
        validate_explicit_trace_ids(policy["fixed_trace_ids"])
    except ValueError as exc:
        raise ValueError("bounded execution requires 1–10 distinct fixed Trace IDs") from exc
    # Reject booleans, even though bool == int in Python.
    if any(type(v) is not int for v in policy["request_budget"].values()):
        raise ValueError("invalid request budget")
    return deepcopy(policy)
