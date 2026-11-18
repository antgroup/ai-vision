"""Canonical Task statistics for source ingestion and Diagnoser metrics."""

from __future__ import annotations

import copy
import math
import re
from typing import Any

from agentlens.domain.diagnosis_record import Metric

MetricCounters = dict[str, int]
DiagnoserStats = dict[str, MetricCounters]
SourceStats = dict[str, int]
TaskStats = dict[str, Any]

_SELECTOR_COUNT_FIELDS = {
    "scanned_targets",
    "scanned_users",
    "selected_targets",
    "selected_users",
    "rejected_targets",
    "rejected_users",
    "invalid_targets",
    "unresolved_targets",
    "unresolved_users",
    "total_batches",
    "failed_batches",
}
_METRIC_COUNTER_FIELDS = {"total", "good", "bad", "failed"}
_OPTIONAL_METRIC_COUNTER_FIELDS = {"evaluate", "skipped"}


def new_task_stats(*, definitions: dict | None = None) -> TaskStats:
    stats = {"source": {}, "diagnosers": {}}
    if definitions is not None:
        stats.update(
            schema_version=3,
            score_policy_version="lightweight-v1",
            diagnoser_definitions=copy.deepcopy(definitions),
            skip_reasons={},
            source_complete=True,
        )
    return stats


def _new_counters() -> MetricCounters:
    return {"total": 0, "good": 0, "bad": 0, "failed": 0}


def record_source(stats: TaskStats, key: str, amount: int = 1) -> None:
    if not key or amount < 0:
        raise ValueError("source stat key and non-negative amount are required")
    source = stats.setdefault("source", {})
    source[key] = int(source.get(key, 0)) + amount


def record_metric(
    stats: TaskStats,
    diagnoser_uuid: str,
    result: Metric,
    *,
    evaluate: bool | None = None,
) -> None:
    """Accumulate one metric result under its full identity."""
    if not diagnoser_uuid:
        raise ValueError("diagnoser_uuid is required for task stats")
    diagnosers = stats.setdefault("diagnosers", {})
    counters = diagnosers.setdefault(diagnoser_uuid, {}).setdefault(result.name, _new_counters())
    counters["total"] += 1
    if evaluate is not None:
        counters.setdefault("evaluate", 0)
        if evaluate:
            counters["evaluate"] += 1
    counters[result.verdict.value] += 1


def record_metric_skip(
    stats: TaskStats,
    diagnoser_uuid: str,
    metric_name: str,
) -> None:
    """Count one Diagnoser-local skip without creating a Metric result."""
    if not diagnoser_uuid or not metric_name:
        raise ValueError("diagnoser_uuid and metric_name are required for skipped task stats")
    diagnosers = stats.setdefault("diagnosers", {})
    counters = diagnosers.setdefault(diagnoser_uuid, {}).setdefault(metric_name, _new_counters())
    counters["skipped"] = int(counters.get("skipped", 0)) + 1


def metric_counters(
    stats: dict[str, Any] | None,
    diagnoser_uuid: str,
    metric_name: str,
) -> MetricCounters | None:
    if not stats:
        return None
    diagnosers = stats.get("diagnosers")
    if not isinstance(diagnosers, dict):
        return None
    diagnoser_stats = diagnosers.get(diagnoser_uuid)
    if not isinstance(diagnoser_stats, dict):
        return None
    counters = diagnoser_stats.get(metric_name)
    return counters if isinstance(counters, dict) else None


def task_target_count(stats: dict[str, Any] | None) -> int:
    """Return the common per-metric target total from canonical task stats."""
    if not stats:
        return 0
    diagnosers = stats.get("diagnosers")
    if not isinstance(diagnosers, dict):
        return 0
    totals: set[int] = set()
    for diagnoser_stats in diagnosers.values():
        if not isinstance(diagnoser_stats, dict):
            raise ValueError("invalid task stats: diagnoser entry must be an object")
        for counters in diagnoser_stats.values():
            if not isinstance(counters, dict) or not isinstance(counters.get("total"), int):
                raise ValueError("invalid task stats: metric total is required")
            totals.add(counters["total"] + int(counters.get("skipped", 0)))
    if stats.get("schema_version") == 3:
        source_total = (stats.get("source") or {}).get("total")
        if isinstance(source_total, int):
            return source_total
        return max(totals, default=0)
    if not totals:
        return 0
    if len(totals) != 1:
        raise ValueError(f"inconsistent metric totals: {sorted(totals)}")
    return totals.pop()


def canonical_task_stats(stats: Any) -> TaskStats | None:
    """Return stats only when they satisfy the source/diagnosers contract."""
    if not isinstance(stats, dict) or not {"source", "diagnosers"}.issubset(stats):
        return None
    allowed = {"source", "selector", "diagnosers"}
    version = stats.get("schema_version")
    if version is not None:
        if isinstance(version, bool) or version != 3:
            return None
        allowed |= {
            "schema_version",
            "score_policy_version",
            "diagnoser_definitions",
            "skip_reasons",
            "source_complete",
        }
        if stats.get("score_policy_version") != "lightweight-v1" or not isinstance(
            stats.get("source_complete"), bool
        ):
            return None
        definitions = stats.get("diagnoser_definitions")
        reasons = stats.get("skip_reasons")
        if not isinstance(definitions, dict) or not isinstance(reasons, dict):
            return None
        required = {
            "uuid",
            "key",
            "name",
            "category",
            "weight",
            "score_role",
            "metrics",
            "version",
            "revision",
            "digest",
        }
        optional_metadata = {
            "builtin_key",
            "canonical_variant",
            "prompt_version",
            "result_boundary",
        }
        for uuid, definition in definitions.items():
            if (
                not isinstance(uuid, str)
                or not isinstance(definition, dict)
                or not required.issubset(definition)
                or set(definition) - required - optional_metadata
            ):
                return None
            if any(
                not isinstance(definition[key], str) or len(definition[key]) > 512
                for key in optional_metadata & set(definition)
            ):
                return None
            if (
                definition["uuid"] != uuid
                or not isinstance(definition["score_role"], str)
                or definition["score_role"] not in {"quality", "signal"}
            ):
                return None
            if any(
                not isinstance(definition[key], str) or not definition[key]
                for key in ("uuid", "key", "name", "category", "version", "revision", "digest")
            ):
                return None
            if not re.fullmatch(r"[0-9a-f]{64}", definition["digest"]):
                return None
            weight = definition["weight"]
            if (
                isinstance(weight, bool)
                or not isinstance(weight, (int, float))
                or not math.isfinite(weight)
                or weight < 0
            ):
                return None
            metrics = definition["metrics"]
            if not isinstance(metrics, list) or not metrics:
                return None
            names = set()
            for metric in metrics:
                if not isinstance(metric, dict) or set(metric) - {"name", "desc"}:
                    return None
                name = metric.get("name")
                if (
                    not isinstance(name, str)
                    or not name
                    or name in names
                    or not isinstance(metric.get("desc", ""), str)
                ):
                    return None
                names.add(name)
        for uuid, counts in reasons.items():
            if uuid not in definitions or not isinstance(counts, dict):
                return None
            if any(
                not isinstance(reason, str)
                or not reason
                or isinstance(count, bool)
                or not isinstance(count, int)
                or count < 0
                for reason, count in counts.items()
            ):
                return None
    if set(stats) - allowed:
        return None
    source = stats.get("source")
    diagnosers = stats.get("diagnosers")
    if not isinstance(source, dict) or not isinstance(diagnosers, dict):
        return None
    if any(
        not isinstance(key, str)
        or isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        for key, value in source.items()
    ):
        return None
    if source:
        source_keys = {"total", "invalid", "projection_failed"}
        if set(source) != source_keys:
            return None
        if source["invalid"] > source["total"]:
            return None
        if source["projection_failed"] > source["total"] - source["invalid"]:
            return None
    selector = stats.get("selector")
    if selector is not None:
        if not isinstance(selector, dict):
            return None
        if set(selector) != {"type", "status", *_SELECTOR_COUNT_FIELDS}:
            return None
        if not isinstance(selector["type"], str) or not selector["type"]:
            return None
        if selector["status"] not in {"complete", "partial", "failed"}:
            return None
        if any(
            isinstance(selector[field], bool)
            or not isinstance(selector[field], int)
            or selector[field] < 0
            for field in _SELECTOR_COUNT_FIELDS
        ):
            return None
        if selector["scanned_targets"] != (
            selector["selected_targets"]
            + selector["rejected_targets"]
            + selector["invalid_targets"]
            + selector["unresolved_targets"]
        ):
            return None
        if selector["scanned_users"] != (
            selector["selected_users"] + selector["rejected_users"] + selector["unresolved_users"]
        ):
            return None
        if not source or source["total"] != selector["selected_targets"]:
            return None
        if selector["failed_batches"] > selector["total_batches"]:
            return None
        if selector["status"] == "complete" and selector["failed_batches"] != 0:
            return None
        if selector["status"] == "partial" and not (
            0 < selector["failed_batches"] < selector["total_batches"]
        ):
            return None
        if selector["status"] == "failed" and (
            selector["total_batches"] == 0
            or selector["failed_batches"] != selector["total_batches"]
        ):
            return None
    for diagnoser_uuid, diagnoser_stats in diagnosers.items():
        if not isinstance(diagnoser_uuid, str) or not isinstance(diagnoser_stats, dict):
            return None
        for metric_name, counters in diagnoser_stats.items():
            if not isinstance(metric_name, str) or not isinstance(counters, dict):
                return None
            counter_fields = set(counters)
            if not _METRIC_COUNTER_FIELDS.issubset(counter_fields) or (
                counter_fields - _METRIC_COUNTER_FIELDS - _OPTIONAL_METRIC_COUNTER_FIELDS
            ):
                return None
            if any(
                isinstance(value, bool) or not isinstance(value, int) or value < 0
                for value in counters.values()
            ):
                return None
            if counters["total"] != counters["good"] + counters["bad"] + counters["failed"]:
                return None
            evaluate = counters.get("evaluate")
            if evaluate is not None and evaluate > counters["total"]:
                return None
    if version == 3:
        for uuid, counters in diagnosers.items():
            if uuid not in definitions or set(counters) - {
                m["name"] for m in definitions[uuid]["metrics"]
            }:
                return None
    canonical = {"source": dict(source), "diagnosers": copy.deepcopy(diagnosers)}
    if version == 3:
        for key in (
            "schema_version",
            "score_policy_version",
            "diagnoser_definitions",
            "skip_reasons",
            "source_complete",
        ):
            canonical[key] = copy.deepcopy(stats[key])
    if selector is not None:
        canonical["selector"] = dict(selector)
    return canonical
