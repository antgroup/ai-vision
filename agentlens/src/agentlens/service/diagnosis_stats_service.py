"""Task-oriented quality aggregation over one TaskStats snapshot query."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from agentlens.domain.metric_definition import MetricDefinition, metric_definitions
from agentlens.domain.task_stats import metric_counters, task_target_count
from agentlens.service.task_stats_store import TaskStatsSnapshot, TaskStatsStore

_CATEGORIES = ("outcome", "process", "safety", "system")
_CATEGORY_LABELS = {
    "outcome": "结果质量",
    "process": "过程质量",
    "safety": "安全合规",
    "system": "系统性能",
}


def _normalize_category(value: Any) -> str:
    category = str(value or "outcome")
    return category if category in _CATEGORIES else "outcome"


def _weight(diagnoser) -> float:
    value = (diagnoser.spec or {}).get("weight", 0)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


def _score(ratio: float | None) -> float | None:
    return round(ratio * 100, 1) if ratio is not None else None


@dataclass(frozen=True)
class _DiagnoserDefinition:
    diagnoser: Any
    category: str
    weight: float
    metrics: list[MetricDefinition]
    score_role: str = "quality"
    metadata: dict | None = None


class DiagnosisStatsService:
    def __init__(self, diagnosers, task_stats: TaskStatsStore) -> None:
        self.diagnosers = diagnosers
        self.task_stats = task_stats

    def list_stats(
        self,
        *,
        job,
        task_uuid: str | None = None,
        owner_id: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
    ) -> dict[str, Any]:
        point_filter = {"task_uuid": task_uuid} if task_uuid is not None else {}
        snapshots = self.task_stats.query_succeeded(
            job_uuid=job.uuid,
            owner_id=owner_id,
            start_time=start_time,
            end_time=end_time,
            **point_filter,
        )
        snapshots.sort(key=lambda snapshot: snapshot.period)
        return {
            "categories": self._category_skeleton(),
            "tasks": [
                self._task_point(
                    snapshot=snapshot, definitions=self._snapshot_definitions(snapshot, job)
                )
                for snapshot in snapshots
            ],
        }

    @staticmethod
    def _category_skeleton() -> list[dict[str, Any]]:
        return [
            {
                "id": category,
                "name": _CATEGORY_LABELS[category],
            }
            for category in _CATEGORIES
        ]

    def _snapshot_definitions(self, snapshot: TaskStatsSnapshot, job) -> list[_DiagnoserDefinition]:
        if snapshot.stats.get("schema_version") != 3:
            return self._definitions(job.diagnoser_uuids or [])
        definitions = []
        for uuid, entry in snapshot.stats["diagnoser_definitions"].items():
            definitions.append(
                _DiagnoserDefinition(
                    diagnoser=SimpleNamespace(uuid=uuid, name=entry["name"]),
                    category=_normalize_category(entry["category"]),
                    weight=float(entry["weight"]),
                    metrics=metric_definitions({"metrics": entry["metrics"]}),
                    score_role=entry["score_role"],
                    metadata=entry,
                )
            )
        return definitions

    def _definitions(self, diagnoser_uuids: list[str]) -> list[_DiagnoserDefinition]:
        definitions = []
        for uuid in diagnoser_uuids:
            diagnoser = self.diagnosers.get_by_uuid(uuid)
            if diagnoser is None:
                continue
            metrics = metric_definitions(diagnoser.spec or {})
            if not metrics:
                continue
            definitions.append(
                _DiagnoserDefinition(
                    diagnoser=diagnoser,
                    category=_normalize_category((diagnoser.spec or {}).get("category")),
                    weight=_weight(diagnoser),
                    metrics=metrics,
                )
            )
        return definitions

    def _task_point(
        self,
        *,
        snapshot: TaskStatsSnapshot,
        definitions: list[_DiagnoserDefinition],
    ) -> dict[str, Any]:
        category_nodes = []
        category_ratios = []
        for category in _CATEGORIES:
            node, ratio = self._category_node(
                category=category,
                snapshot=snapshot,
                definitions=definitions,
            )
            category_nodes.append(node)
            if ratio is not None:
                category_ratios.append(ratio)
        global_ratio = sum(category_ratios) / len(category_ratios) if category_ratios else None
        if snapshot.stats.get("schema_version") == 3:
            quality_categories = {d.category for d in definitions if d.score_role == "quality"}
            if not self._source_complete(snapshot) or any(
                node["score"] is None for node in category_nodes if node["id"] in quality_categories
            ):
                global_ratio = None
        if snapshot.period is None:
            raise ValueError("TaskStats snapshot period is required")
        return {
            "task_uuid": snapshot.task_uuid,
            "time": snapshot.period,
            "target_type": "trace",
            "target_count": task_target_count(snapshot.stats),
            "selector": snapshot.stats.get("selector"),
            "schema_version": snapshot.stats.get("schema_version"),
            "source_complete": snapshot.stats.get("source_complete"),
            "score_policy_version": snapshot.stats.get("score_policy_version"),
            "definition_source": "task_snapshot"
            if snapshot.stats.get("schema_version") == 3
            else "legacy_current_definition",
            "skip_reasons": snapshot.stats.get("skip_reasons", {}),
            "global": {
                "score": _score(global_ratio),
                "categories": category_nodes,
            },
        }

    def _category_node(
        self,
        *,
        category: str,
        snapshot: TaskStatsSnapshot,
        definitions: list[_DiagnoserDefinition],
    ) -> tuple[dict[str, Any], float | None]:
        diagnoser_nodes = []
        scored: list[tuple[float, float]] = []
        quality_incomplete = False
        for definition in definitions:
            if definition.category != category:
                continue
            node, ratio = self._diagnoser_node(snapshot=snapshot, definition=definition)
            diagnoser_nodes.append(node)
            if snapshot.stats.get("schema_version") != 3 or definition.score_role == "quality":
                if ratio is not None:
                    scored.append((ratio, definition.weight))
                else:
                    quality_incomplete = True

        ratio = None
        if scored and (
            snapshot.stats.get("schema_version") != 3
            or (not quality_incomplete and self._source_complete(snapshot))
        ):
            total_weight = sum(weight for _, weight in scored)
            if total_weight > 0:
                ratio = sum(value * weight for value, weight in scored) / total_weight
            else:
                ratio = sum(value for value, _ in scored) / len(scored)
        return (
            {
                "id": category,
                "name": _CATEGORY_LABELS[category],
                "score": _score(ratio),
                "diagnosers": diagnoser_nodes,
            },
            ratio,
        )

    def _diagnoser_node(
        self,
        *,
        snapshot: TaskStatsSnapshot,
        definition: _DiagnoserDefinition,
    ) -> tuple[dict[str, Any], float | None]:
        metric_nodes = []
        metric_ratios = []
        for metric in definition.metrics:
            node, ratio = self._metric_node(
                snapshot=snapshot,
                diagnoser_uuid=definition.diagnoser.uuid,
                metric=metric,
            )
            metric_nodes.append(node)
            if ratio is not None:
                metric_ratios.append(ratio)
        ratio = sum(metric_ratios) / len(metric_ratios) if metric_ratios else None
        if snapshot.stats.get("schema_version") == 3:
            if (
                definition.score_role != "quality"
                or len(metric_ratios) != len(definition.metrics)
                or not self._source_complete(snapshot)
            ):
                ratio = None
        return (
            {
                "uuid": definition.diagnoser.uuid,
                "name": definition.diagnoser.name,
                "weight": definition.weight,
                "score": _score(ratio),
                "metrics": metric_nodes,
                **(
                    {
                        key: definition.metadata[key]
                        for key in ("score_role", "version", "revision", "digest", "key")
                    }
                    if definition.metadata
                    else {}
                ),
            },
            ratio,
        )

    @staticmethod
    def _source_complete(snapshot: TaskStatsSnapshot) -> bool:
        source = snapshot.stats["source"]
        selector = snapshot.stats.get("selector", {})
        return (
            snapshot.stats.get("source_complete") is True
            and not source.get("invalid", 0)
            and not source.get("projection_failed", 0)
            and selector.get("status", "complete") == "complete"
        )

    @staticmethod
    def _metric_node(
        *,
        snapshot: TaskStatsSnapshot,
        diagnoser_uuid: str,
        metric: MetricDefinition,
    ) -> tuple[dict[str, Any], float | None]:
        stored = metric_counters(snapshot.stats, diagnoser_uuid, metric.name)
        counters = stored or {
            "total": 0,
            "good": 0,
            "bad": 0,
            "failed": 0,
        }
        judged = counters["good"] + counters["bad"]
        ratio = counters["good"] / judged if judged > 0 else None
        if snapshot.stats.get("schema_version") == 3 and (
            stored is None
            or counters["failed"]
            or counters.get("skipped", 0)
            or counters["total"] != task_target_count(snapshot.stats)
        ):
            ratio = None
        return (
            {
                "id": metric.name,
                "name": metric.display_name,
                "score": _score(ratio),
                "total": counters["total"],
                "good": counters["good"],
                "bad": counters["bad"],
                "failed": counters["failed"],
                "skipped": counters.get("skipped", 0),
            },
            ratio,
        )
