"""Normalize versioned Rule inputs and qualify the selected sandbox image."""

import copy
import re

from agentlens.diagnoser.executors.rule.custom.enhanced_contract import (
    CAPABILITY,
    RUNTIME_ID,
    normalize_materials,
    validate_runtime_manifest,
)
from agentlens.domain.sandbox import SandboxError

RUNTIME_FIELDS = frozenset({"rule_runtime", "rule_runtime_manifest", "materials"})


def normalize_rule_runtime(spec: dict) -> dict:
    normalized = copy.deepcopy(spec)
    if not RUNTIME_FIELDS.intersection(spec):
        return normalized
    if spec.get("rule_runtime") != RUNTIME_ID:
        raise ValueError("Unsupported rule_runtime; materials require python-eval-v1")
    digest = spec.get("rule_runtime_manifest")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("rule_runtime_manifest must be a SHA256 digest")
    normalized["materials"] = normalize_materials(spec.get("materials"))
    return normalized


def qualified_runtime_manifests(health: dict, image: str) -> list[dict]:
    """Only the explicitly selected immutable image can advertise capabilities."""
    if not isinstance(image, str) or not re.search(r"@sha256:[0-9a-f]{64}$", image):
        return []
    if health.get("ready") is not True or CAPABILITY not in health.get("capabilities", []):
        return []
    values = health.get("runtime_manifests", [])
    if not isinstance(values, list):
        raise SandboxError("sandbox_protocol_error")
    qualified = []
    for value in values:
        if not isinstance(value, dict) or value.get("image") != image:
            continue
        try:
            manifest = validate_runtime_manifest({k: v for k, v in value.items() if k != "image"})
        except ValueError as exc:
            raise SandboxError("sandbox_protocol_error") from exc
        qualified.append({**manifest, "image": image})
    return qualified


def require_rule_runtime(spec: dict, profile: dict, health: dict | None = None) -> None:
    if spec.get("rule_runtime") is None:
        return
    if profile.get("mode") != "sandbox":
        raise SandboxError("sandbox_runtime_required")
    if health is None or not any(
        value["manifest_sha256"] == spec.get("rule_runtime_manifest")
        for value in qualified_runtime_manifests(health, profile.get("image"))
    ):
        raise SandboxError("sandbox_runtime_unavailable")
