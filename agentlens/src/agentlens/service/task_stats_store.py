"""Terminal TaskStats snapshots stored as typed telemetry spans."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from agentlens.domain.errors import InvalidTimeRange
from agentlens.domain.task_stats import TaskStats, canonical_task_stats
from agentlens.infra.antcollector import AntcollectorClient
from agentlens.infra.signoz.client import SignozClient
from agentlens.infra.signoz.dialect import ColumnDialect, FlatColumnDialect
from agentlens.logging import get_logger

TASK_STATS_SPAN_NAME = "task_stats"
TASK_STATS_DEFAULT_WINDOW = timedelta(hours=6)
TASK_STATS_MAX_WINDOW = timedelta(days=7)
TASK_STATS_OWNER_BATCH_SIZE = 500
_TERMINAL_STATUSES = {"succeeded", "failed", "canceled"}
_SAFE_ID = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")


@dataclass(frozen=True)
class TaskStatsSnapshot:
    task_uuid: str
    job_uuid: str
    attempt: int
    status: str
    stats_scope: str
    owner_id: str
    stats: TaskStats
    period: datetime | None = None
    end_time: datetime | None = None
    # Job 所属 Space；仅作为 span 属性 space_id 上报，不参与统计口径。
    space_uuid: str = ""


def _esc(value: str) -> str:
    if not _SAFE_ID.fullmatch(value):
        raise ValueError(f"unsafe SQL literal: {value!r}")
    return value


def _parse_time(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise InvalidTimeRange(f"{field} must be an ISO datetime string") from exc
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def resolve_task_stats_window(
    start_time: str | None,
    end_time: str | None,
    *,
    now: datetime | None = None,
) -> tuple[datetime, datetime]:
    now = now or datetime.now()
    end = _parse_time(end_time, "end_time") if end_time else now
    start = _parse_time(start_time, "start_time") if start_time else end - TASK_STATS_DEFAULT_WINDOW
    if start >= end:
        raise InvalidTimeRange("start_time must be earlier than end_time")
    if end - start > TASK_STATS_MAX_WINDOW:
        raise InvalidTimeRange("time range must not exceed 7 days")
    return start, end


def _datetime_from_value(raw: Any) -> datetime | None:
    if isinstance(raw, datetime):
        value = raw
    elif isinstance(raw, (int, float)) or (
        isinstance(raw, str) and raw.strip().replace(".", "", 1).isdigit()
    ):
        try:
            numeric = float(raw)
            magnitude = abs(numeric)
            if magnitude >= 1e17:
                numeric /= 1e9
            elif magnitude >= 1e14:
                numeric /= 1e6
            elif magnitude >= 1e11:
                numeric /= 1e3
            value = datetime.fromtimestamp(numeric)
        except (OSError, OverflowError, TypeError, ValueError):
            return None
    elif isinstance(raw, str) and raw:
        try:
            value = datetime.fromisoformat(raw)
        except ValueError:
            return None
    else:
        return None
    if value.tzinfo is not None:
        value = value.astimezone().replace(tzinfo=None)
    return value


def _period_from_row(row: dict[str, Any]) -> datetime | None:
    return _datetime_from_value(row.get("period"))


def _end_time_from_row(row: dict[str, Any]) -> datetime | None:
    return _datetime_from_value(row.get("end_time_unix_nano"))


def _row_value(row: dict[str, Any], key: str) -> Any:
    return row.get(f"attributes.{key}")


def _integer(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError as exc:
            raise ValueError(f"{field} must be an integer") from exc
    raise ValueError(f"{field} must be an integer")


def _row_rank(row: dict[str, Any]) -> tuple[int, datetime]:
    try:
        attempt = int(_row_value(row, "attempt"))
    except (TypeError, ValueError):
        attempt = -1
    return attempt, _end_time_from_row(row) or datetime.min


class TaskStatsStore:
    def __init__(
        self,
        reader: SignozClient,
        writer: AntcollectorClient,
        *,
        table: str,
        dialect: ColumnDialect | None = None,
    ) -> None:
        self.reader = reader
        self.writer = writer
        self.table = table
        self.dialect = dialect or FlatColumnDialect()
        self._log = get_logger("task_stats_store")

    def write_snapshot(self, snapshot: TaskStatsSnapshot) -> None:
        stats = canonical_task_stats(snapshot.stats)
        if stats is None:
            raise ValueError("task stats are not canonical")
        if snapshot.status not in _TERMINAL_STATUSES:
            raise ValueError(f"invalid terminal task status: {snapshot.status!r}")
        if snapshot.stats_scope == "global":
            if snapshot.owner_id:
                raise ValueError("global task stats require an empty owner_id")
        elif snapshot.stats_scope == "owner":
            if not snapshot.owner_id:
                raise ValueError("owner task stats require owner_id")
            if stats["source"]:
                raise ValueError("owner task stats require an empty source")
            if "selector" in stats:
                raise ValueError("owner task stats must not include selector stats")
        else:
            raise ValueError(f"invalid task stats scope: {snapshot.stats_scope!r}")
        if snapshot.period is None:
            raise ValueError("task stats require a business period")
        body = {
            "task_uuid": snapshot.task_uuid,
            "job_uuid": snapshot.job_uuid,
            "attempt": snapshot.attempt,
            "status": snapshot.status,
            "stats_scope": snapshot.stats_scope,
            "owner_id": snapshot.owner_id,
            "stats": stats,
        }
        attributes = {key: body[key] for key in body if key != "stats"}
        if snapshot.space_uuid:
            attributes["space_id"] = snapshot.space_uuid
        self.writer.write_span(
            self.table,
            TASK_STATS_SPAN_NAME,
            attributes,
            body,
            start_time=snapshot.period,
            end_time=snapshot.end_time or datetime.now(),
        )

    def write_terminal_snapshots(
        self,
        *,
        task_uuid: str,
        job_uuid: str,
        attempt: int,
        status: str,
        stats: TaskStats,
        business_time: datetime,
        owner_stats: dict[str, TaskStats] | None = None,
        space_uuid: str = "",
    ) -> None:
        owners = sorted((owner_stats or {}).items())
        planned = len(owners) + 1
        written = 0

        def enqueue(snapshot: TaskStatsSnapshot) -> None:
            nonlocal written
            try:
                self.write_snapshot(snapshot)
                written += 1
            except Exception as exc:  # noqa: BLE001
                self._log.warning(
                    "task_stats.write_failed",
                    task_uuid=task_uuid,
                    job_uuid=job_uuid,
                    attempt=attempt,
                    status=status,
                    stats_scope=snapshot.stats_scope,
                    owner_id=snapshot.owner_id,
                    planned=planned,
                    written=written,
                    error=str(exc),
                )

        enqueue(
            TaskStatsSnapshot(
                task_uuid=task_uuid,
                job_uuid=job_uuid,
                attempt=attempt,
                status=status,
                stats_scope="global",
                owner_id="",
                stats=stats,
                period=business_time,
                space_uuid=space_uuid,
            )
        )
        batches = [
            owners[index : index + TASK_STATS_OWNER_BATCH_SIZE]
            for index in range(0, len(owners), TASK_STATS_OWNER_BATCH_SIZE)
        ] or [[]]
        for batch in batches:
            for owner_id, owner_snapshot_stats in batch:
                enqueue(
                    TaskStatsSnapshot(
                        task_uuid=task_uuid,
                        job_uuid=job_uuid,
                        attempt=attempt,
                        status=status,
                        stats_scope="owner",
                        owner_id=owner_id,
                        stats=owner_snapshot_stats,
                        period=business_time,
                        space_uuid=space_uuid,
                    )
                )
            try:
                if not self.writer.force_flush():
                    raise RuntimeError("Antcollector force_flush returned false")
            except Exception as exc:  # noqa: BLE001
                self._log.warning(
                    "task_stats.flush_failed",
                    task_uuid=task_uuid,
                    job_uuid=job_uuid,
                    attempt=attempt,
                    status=status,
                    planned=planned,
                    written=written,
                    error=str(exc),
                )
        self._log.info(
            "task_stats.write_finished",
            task_uuid=task_uuid,
            job_uuid=job_uuid,
            attempt=attempt,
            status=status,
            planned=planned,
            written=written,
        )

    def query_succeeded(
        self,
        *,
        job_uuid: str,
        task_uuid: str | None = None,
        owner_id: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        limit: int | None = None,
    ) -> list[TaskStatsSnapshot]:
        scope = "owner" if owner_id is not None else "global"
        resolved_owner_id = owner_id or ""
        escaped_owner_id = _esc(resolved_owner_id) if resolved_owner_id else ""
        col = self.dialect.col
        where = [
            *self.dialect.base_predicates(),
            f"{col('name')} = '{TASK_STATS_SPAN_NAME}'",
            f"{col('attributes.job_uuid')} = '{_esc(job_uuid)}'",
            f"{col('attributes.status')} = 'succeeded'",
            f"{col('attributes.stats_scope')} = '{scope}'",
            f"{col('attributes.owner_id')} = '{escaped_owner_id}'",
        ]
        if task_uuid:
            where.append(f"{col('attributes.task_uuid')} = '{_esc(task_uuid)}'")
        else:
            start, end = resolve_task_stats_window(start_time, end_time)
            where.extend(
                [
                    self.dialect.time_ge(
                        "period", start.isoformat(sep=" ", timespec="microseconds")
                    ),
                    self.dialect.time_lt("period", end.isoformat(sep=" ", timespec="microseconds")),
                ]
            )
        sql = (
            f"SELECT * FROM {self.table} WHERE "
            + " AND ".join(where)
            + f" ORDER BY {self.dialect.int_col('attributes.attempt')} DESC,"
            f" {col('end_time_unix_nano')} DESC"
        )
        rows = self.reader.query(sql)
        snapshots = self._latest_logical_snapshots(rows)
        if task_uuid:
            snapshots = [snapshot for snapshot in snapshots if snapshot.task_uuid == task_uuid]
        snapshots = self._latest_business_snapshots(snapshots)
        snapshots.sort(key=lambda snapshot: snapshot.period or datetime.min, reverse=True)
        return snapshots[:limit] if limit is not None else snapshots

    def latest_succeeded(self, **kwargs: Any) -> TaskStatsSnapshot | None:
        snapshots = self.query_succeeded(limit=1, **kwargs)
        return snapshots[0] if snapshots else None

    def _latest_logical_snapshots(self, rows: list[dict[str, Any]]) -> list[TaskStatsSnapshot]:
        latest: dict[tuple[str, str, str], dict[str, Any]] = {}
        for row in rows:
            key = (
                str(_row_value(row, "task_uuid") or ""),
                str(_row_value(row, "stats_scope") or ""),
                str(_row_value(row, "owner_id") or ""),
            )
            if not key[0]:
                self._warn_invalid(row, "missing promoted task_uuid")
                continue
            if key not in latest or _row_rank(row) > _row_rank(latest[key]):
                latest[key] = row

        snapshots: list[TaskStatsSnapshot] = []
        for row in latest.values():
            try:
                snapshots.append(self._snapshot_from_row(row))
            except Exception as exc:  # noqa: BLE001
                self._warn_invalid(row, str(exc))
        return snapshots

    @staticmethod
    def _latest_business_snapshots(
        snapshots: list[TaskStatsSnapshot],
    ) -> list[TaskStatsSnapshot]:
        latest: dict[datetime, TaskStatsSnapshot] = {}
        for snapshot in snapshots:
            if snapshot.period is None:
                continue
            current = latest.get(snapshot.period)
            candidate_rank = (snapshot.end_time or datetime.min, snapshot.task_uuid)
            current_rank = (
                (current.end_time or datetime.min, current.task_uuid)
                if current is not None
                else None
            )
            if current_rank is None or candidate_rank > current_rank:
                latest[snapshot.period] = snapshot
        return list(latest.values())

    def _snapshot_from_row(self, row: dict[str, Any]) -> TaskStatsSnapshot:
        body_raw = row.get("attributes.record.body")
        if not isinstance(body_raw, str) or not body_raw:
            raise ValueError("missing record.body")
        body = json.loads(body_raw)
        if not isinstance(body, dict):
            raise ValueError("record.body must be an object")
        task_uuid = str(_row_value(row, "task_uuid") or "")
        job_uuid = str(_row_value(row, "job_uuid") or "")
        attempt = _integer(_row_value(row, "attempt"), "promoted attempt")
        status = str(_row_value(row, "status") or "")
        stats_scope = str(_row_value(row, "stats_scope") or "")
        owner_id = str(_row_value(row, "owner_id") or "")
        promoted = (
            ("task_uuid", task_uuid),
            ("job_uuid", job_uuid),
            ("attempt", attempt),
            ("status", status),
            ("stats_scope", stats_scope),
            ("owner_id", owner_id),
        )
        for key, promoted_value in promoted:
            body_value = body.get(key)
            if key == "attempt":
                body_value = _integer(body_value, "body attempt")
            else:
                body_value = str(body_value or "")
            if body_value != promoted_value:
                raise ValueError(f"promoted {key} does not match body")
        stats = canonical_task_stats(body.get("stats"))
        if stats is None:
            raise ValueError("body stats are not canonical")
        snapshot = TaskStatsSnapshot(
            task_uuid=task_uuid,
            job_uuid=job_uuid,
            attempt=attempt,
            status=status,
            stats_scope=stats_scope,
            owner_id=owner_id,
            stats=stats,
            period=_period_from_row(row),
            end_time=_end_time_from_row(row),
        )
        if snapshot.status not in _TERMINAL_STATUSES:
            raise ValueError("invalid terminal status")
        if snapshot.stats_scope == "global":
            if snapshot.owner_id:
                raise ValueError("global snapshot owner_id must be empty")
        elif snapshot.stats_scope == "owner":
            if not snapshot.owner_id or snapshot.stats["source"] or "selector" in snapshot.stats:
                raise ValueError("owner snapshot requires owner_id and empty source")
        else:
            raise ValueError("invalid stats_scope")
        if snapshot.period is None:
            raise ValueError("invalid period")
        if snapshot.end_time is None:
            raise ValueError("invalid end_time_unix_nano")
        return snapshot

    def _warn_invalid(self, row: dict[str, Any], error: str) -> None:
        self._log.warning(
            "task_stats.invalid_row",
            task_uuid=str(_row_value(row, "task_uuid") or ""),
            job_uuid=str(_row_value(row, "job_uuid") or ""),
            attempt=_row_value(row, "attempt"),
            stats_scope=str(_row_value(row, "stats_scope") or ""),
            owner_id=str(_row_value(row, "owner_id") or ""),
            period=str(row.get("period") or ""),
            end_time=str(row.get("end_time_unix_nano") or ""),
            error=error,
        )
