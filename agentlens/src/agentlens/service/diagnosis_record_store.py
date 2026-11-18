import json
import re
import time
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Any

from agentlens.domain.diagnosis_record import (
    DiagnosisRecord,
    DiagnosisRecordListItem,
    Execution,
    Metric,
    normalize_record_datetime,
)
from agentlens.domain.enums import ExecutionStatus, ReviewStatus, TargetType, Verdict
from agentlens.domain.errors import ExternalUnavailable
from agentlens.infra.antcollector import AntcollectorClient
from agentlens.infra.signoz.client import SignozClient, SignozQueryError
from agentlens.infra.signoz.dialect import ColumnDialect, FlatColumnDialect


def _strip_prefix(row: dict[str, Any]) -> dict[str, Any]:
    """去掉 SigNoz 返回列名中的 'attributes.' 前缀。"""
    return {k.removeprefix("attributes."): v for k, v in row.items()}


def _dict_from_record(r: DiagnosisRecord) -> dict[str, Any]:
    d = asdict(r)
    snapshot = d.get("trace_snapshot") or {}
    context = snapshot.pop("context", None)
    if isinstance(context, dict) and context:
        d["context"] = context
    d["trace_snapshot"] = snapshot
    d["target_type"] = r.target_type.value
    d["review_status"] = r.review_status.value
    d["metrics"] = [{**asdict(metric), "verdict": metric.verdict.value} for metric in r.metrics]
    d["execution"] = {
        **asdict(r.execution),
        "status": r.execution.status.value,
    }
    for field_name in ("reviewed_at", "diagnosed_at", "target_created_at"):
        value = normalize_record_datetime(d.get(field_name))
        d[field_name] = value.isoformat() if value is not None else None
    return d


def _promoted_target_created_at(value: Any) -> str:
    target_created_at = normalize_record_datetime(value)
    if target_created_at is not None and target_created_at.tzinfo is not None:
        target_created_at = target_created_at.astimezone().replace(tzinfo=None)
    return (
        target_created_at.isoformat(sep=" ", timespec="microseconds")
        if target_created_at is not None
        else ""
    )


def _promoted_attributes(record: dict[str, Any]) -> dict[str, Any]:
    metrics = [
        Metric(
            name=item["name"],
            verdict=Verdict(item["verdict"]),
            reason=item.get("reason", ""),
            evidence=item.get("evidence") or {},
        )
        for item in record.get("metrics") or []
    ]
    execution_data = record.get("execution") or {}
    execution = Execution(
        status=ExecutionStatus(execution_data["status"]),
        code=str(execution_data.get("code") or ""),
        msg=str(execution_data.get("msg") or ""),
        evidence=execution_data.get("evidence") or {},
    )
    from agentlens.domain.diagnosis_record import DiagnosisResult, derive_summary, derive_verdict

    result = DiagnosisResult(metrics=metrics, execution=execution)
    return {
        # Collector 按 span 属性 space_id 关联到遥测库 space_id 列。
        "space_id": str(record.get("space_uuid") or ""),
        "uuid": str(record.get("uuid", "")),
        "job_uuid": str(record.get("job_uuid", "")),
        "task_uuid": str(record.get("task_uuid", "")),
        "diagnoser_uuid": str(record.get("diagnoser_uuid", "")),
        "diagnoser_version": str(record.get("diagnoser_version", "")),
        "target_type": str(record.get("target_type", "")),
        "target_id": str(record.get("target_id", "")),
        "owner_id": str(record.get("owner_id") or ""),
        "exp_ver_id": str(record.get("exp_ver_id") or ""),
        "model": str(record.get("model") or ""),
        "verdict": derive_verdict(metrics).value,
        "review_status": str(record.get("review_status", "")),
        "summary": derive_summary(result),
        "attempt": int(record.get("attempt", 0)),
        "reviewed_at": record.get("reviewed_at") or "",
        "diagnosed_at": record.get("diagnosed_at") or "",
        "target_created_at": _promoted_target_created_at(record.get("target_created_at")),
    }


def _legacy_result_metric(result: Any, *, legacy_summary: str) -> Metric:
    if not isinstance(result, dict):
        raise ValueError("legacy diagnosis result must be an object")
    status = result.get("status")
    if status == "failed":
        verdict = Verdict.FAILED
    elif status == "evaluated":
        passed = result.get("passed")
        if passed is True:
            verdict = Verdict.GOOD
        elif passed is False:
            verdict = Verdict.BAD
        else:
            raise ValueError("legacy evaluated result requires passed")
    else:
        raise ValueError(f"invalid legacy diagnosis result status: {status!r}")

    reason = str(result.get("reason") or "")
    if verdict == Verdict.BAD and not reason.strip():
        reason = legacy_summary or "历史诊断项未通过"
    evidence = result.get("evidence")
    return Metric(
        name=str(result.get("name") or ""),
        verdict=verdict,
        reason=reason,
        evidence=evidence if isinstance(evidence, dict) else {},
    )


def _legacy_body_result(body: dict[str, Any]) -> tuple[list[Metric], Execution]:
    # Temporary read compatibility. Remove on or after 2026-09-23 once all
    # pre-migration DiagnosisRecord bodies have expired from storage.
    legacy_summary = str(body.get("summary") or "")
    raw_results = body.get("results")
    if not isinstance(raw_results, list) or not raw_results:
        raise ValueError("legacy diagnosis record requires results")
    metrics = [
        _legacy_result_metric(result, legacy_summary=legacy_summary) for result in raw_results
    ]
    raw_evidence = body.get("evidence")
    execution_evidence = raw_evidence if isinstance(raw_evidence, dict) else {}
    if all(metric.verdict == Verdict.FAILED for metric in metrics):
        msg = legacy_summary or "；".join(dict.fromkeys(metric.reason for metric in metrics))
        execution = Execution.failed(
            "legacy_execution_failed",
            msg or "历史诊断执行失败",
            execution_evidence,
        )
    else:
        execution = Execution.succeeded(execution_evidence)
    return metrics, execution


def _body_result(body: dict[str, Any]) -> tuple[list[Metric], Execution]:
    if "metrics" not in body and "execution" not in body and "results" in body:
        return _legacy_body_result(body)

    metrics = [
        Metric(
            name=metric["name"],
            verdict=Verdict(metric["verdict"]),
            reason=metric.get("reason", ""),
            evidence=metric.get("evidence") or {},
        )
        for metric in body.get("metrics") or []
    ]
    execution_data = body.get("execution") or {}
    execution = Execution(
        status=ExecutionStatus(execution_data["status"]),
        code=str(execution_data.get("code") or ""),
        msg=str(execution_data.get("msg") or ""),
        evidence=execution_data.get("evidence") or {},
    )
    return metrics, execution


def _record_from_row(row: dict[str, Any]) -> DiagnosisRecord:
    """Deserialize complete detail from record.body.

    Compact list reads use promoted attributes and never call this function.
    """
    stripped = _strip_prefix(row)
    body_raw = stripped.get("record.body")
    if not isinstance(body_raw, str) or not body_raw:
        raise ValueError("diagnosis record detail row is missing record.body")
    d = json.loads(body_raw)
    if not isinstance(d, dict):
        raise ValueError("diagnosis record body must be an object")
    metrics, execution = _body_result(d)

    trace_snapshot = d.get("trace_snapshot") or {}
    if isinstance(d.get("context"), dict):
        trace_snapshot["context"] = d["context"]
    rec = DiagnosisRecord(
        uuid=d["uuid"],
        task_uuid=d["task_uuid"],
        job_uuid=d["job_uuid"],
        diagnoser_uuid=d["diagnoser_uuid"],
        diagnoser_version=d["diagnoser_version"],
        target_type=TargetType(d["target_type"]),
        target_id=d["target_id"],
        metrics=metrics,
        execution=execution,
        trace_snapshot=trace_snapshot,
        review_status=ReviewStatus(d["review_status"]),
        feedback=d.get("feedback"),
        reviewed_by=d.get("reviewed_by"),
        reviewed_at=normalize_record_datetime(d.get("reviewed_at")),
        diagnosed_at=normalize_record_datetime(d.get("diagnosed_at")),
        target_created_at=normalize_record_datetime(d.get("target_created_at")),
        owner_id=d.get("owner_id"),
        exp_ver_id=str(d.get("exp_ver_id") or ""),
        model=str(d.get("model") or ""),
        space_uuid=str(d.get("space_uuid") or ""),
    )
    # Attempt is envelope metadata, not a Domain field.
    rec._attempt = stripped.get("attempt", d.get("attempt", 0))  # type: ignore[attr-defined]
    rec._period = stripped.get("period")  # type: ignore[attr-defined]
    return rec


def _list_item_from_row(row: dict[str, Any]) -> DiagnosisRecordListItem:
    d = _strip_prefix(row)
    from agentlens.service.execution_snapshot import record_snapshot_definition

    try:
        body = json.loads(d.get("record.body", ""))
    except (ValueError, TypeError):
        body = {}
    if not isinstance(body, dict) or any(
        body.get(key) != d.get(key)
        for key in (
            "uuid",
            "job_uuid",
            "task_uuid",
            "diagnoser_uuid",
            "diagnoser_version",
            "target_type",
            "target_id",
        )
    ):
        body = {}
    definition = record_snapshot_definition(
        body.get("trace_snapshot"), d["diagnoser_uuid"], d["diagnoser_version"]
    )
    return DiagnosisRecordListItem(
        space_uuid=str(body.get("space_uuid") or ""),
        diagnoser_definition=definition,
        uuid=str(d["uuid"]),
        task_uuid=str(d["task_uuid"]),
        job_uuid=str(d["job_uuid"]),
        diagnoser_uuid=str(d["diagnoser_uuid"]),
        diagnoser_version=str(d["diagnoser_version"]),
        target_type=TargetType(d["target_type"]),
        target_id=str(d["target_id"]),
        verdict=Verdict(d["verdict"]),
        review_status=ReviewStatus(d["review_status"]),
        summary=str(d.get("summary") or ""),
        reviewed_at=d.get("reviewed_at"),
        diagnosed_at=d.get("diagnosed_at"),
        target_created_at=d.get("target_created_at"),
        owner_id=str(d["owner_id"]) if d.get("owner_id") not in (None, "") else None,
        exp_ver_id=str(d.get("exp_ver_id") or ""),
        model=str(d.get("model") or ""),
    )


_SAFE_ID = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")
_SAFE_TS = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}$")
_SAFE_VERSION_TS = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:[+-]\d{2}:\d{2})?$"
)
_RECORD_VISIBILITY_FLUSH_TIMEOUT_MILLIS = 3_000
_RECORD_VISIBILITY_TIMEOUT_SECONDS = 5.0
_RECORD_VISIBILITY_POLL_INTERVAL_SECONDS = 0.2


def _esc(val: str, pattern: re.Pattern = _SAFE_ID) -> str:
    if not pattern.match(val):
        raise ValueError(f"unsafe SQL literal: {val!r}")
    return val


def _dedup_latest(records: list[DiagnosisRecord]) -> list[DiagnosisRecord]:
    """按 uuid 去重,保留最新版本。

    优先级：相同 uuid → 最高 attempt 优先 → 同 attempt 内列表序号靠前
    （列表已按 period DESC 排序,首次出现即该 attempt 的最新物理写入）。
    """
    best: dict[str, DiagnosisRecord] = {}
    best_rank: dict[str, tuple[int, float]] = {}
    for record in records:
        rank = (
            int(getattr(record, "_attempt", 0) or 0),
            _datetime_rank(getattr(record, "_period", None)),
        )
        if record.uuid not in best or rank > best_rank[record.uuid]:
            best[record.uuid] = record
            best_rank[record.uuid] = rank
    return list(best.values())


_LIST_COLUMNS = (
    "period",
    "attributes.uuid",
    "attributes.task_uuid",
    "attributes.job_uuid",
    "attributes.diagnoser_uuid",
    "attributes.diagnoser_version",
    "attributes.target_type",
    "attributes.target_id",
    "attributes.owner_id",
    "attributes.exp_ver_id",
    "attributes.model",
    "attributes.verdict",
    "attributes.review_status",
    "attributes.summary",
    "attributes.reviewed_at",
    "attributes.diagnosed_at",
    "attributes.target_created_at",
    "attributes.attempt",
)

_OPTIONAL_LIST_COLUMNS = ("attributes.owner_id", "attributes.exp_ver_id", "attributes.model")
_LIST_REQUIRED_COLUMNS = tuple(
    column for column in _LIST_COLUMNS if column not in _OPTIONAL_LIST_COLUMNS
)
_MISSING_FIELD_PATTERNS = (
    re.compile(r'no field named\s+["`]([^"`]+)["`]', re.IGNORECASE),
    re.compile(r'unknown column\s+["`]?([a-zA-Z0-9_.]+)["`]?', re.IGNORECASE),
    re.compile(r'invalid column\s+["`]?([a-zA-Z0-9_.]+)["`]?', re.IGNORECASE),
    re.compile(
        r'(?:query validation failed for|column)\s+["`]?([a-zA-Z0-9_.]+)["`]?',
        re.IGNORECASE,
    ),
)


def _missing_optional_column(error: SignozQueryError) -> str | None:
    # Only inspect the field identified by the error prefix. SigNoz often
    # appends a complete list of valid fields; scanning the whole message can
    # therefore mistake a valid field (for example owner_id) for the missing
    # one when attributes.model is absent.
    message = str(error)
    for pattern in _MISSING_FIELD_PATTERNS:
        match = pattern.search(message)
        if not match:
            continue
        candidate = match.group(1)
        for column in _OPTIONAL_LIST_COLUMNS:
            if candidate.casefold() == column.casefold():
                return column
        return None
    return None


_LIST_SORT_FIELDS = {
    "uuid",
    "target_id",
    "verdict",
    "review_status",
    "reviewed_at",
    "diagnosed_at",
    "target_created_at",
}
_LIST_DATETIME_SORT_FIELDS = {
    "reviewed_at",
    "diagnosed_at",
    "target_created_at",
}
_RECORD_LIST_QUERY_LIMIT = 50_000


def _datetime_rank(value: Any) -> float:
    normalized = normalize_record_datetime(value)
    return normalized.timestamp() if normalized is not None else float("-inf")


def _normalize_exp_ver_ids(values: list[str] | None) -> list[str]:
    normalized: list[str] = []
    for raw in values or []:
        value = raw.strip() if isinstance(raw, str) else ""
        if not value.isascii() or not value.isdigit():
            raise ValueError("exp_ver_id must contain digits only")
        if value not in normalized:
            normalized.append(value)
    return normalized


def _dedup_list_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best: dict[str, dict[str, Any]] = {}
    for row in rows:
        d = _strip_prefix(row)
        uuid = str(d["uuid"])
        candidate = (int(d.get("attempt") or 0), _datetime_rank(d.get("period")))
        current = best.get(uuid)
        if current is None:
            best[uuid] = row
            continue
        current_d = _strip_prefix(current)
        current_rank = (
            int(current_d.get("attempt") or 0),
            _datetime_rank(current_d.get("period")),
        )
        if candidate > current_rank:
            best[uuid] = row
    return list(best.values())


def _matches_record_category(
    row: dict[str, Any],
    category: str,
    frozen_diagnoser_by_task: dict[str, list[str]],
    legacy_diagnoser_uuids: list[str],
) -> bool:
    d = _strip_prefix(row)
    try:
        body = json.loads(d.get("record.body", ""))
    except (ValueError, TypeError):
        return False
    if not isinstance(body, dict) or any(
        body.get(key) != d.get(key) for key in ("uuid", "job_uuid", "task_uuid", "diagnoser_uuid")
    ):
        return False
    snapshot = body.get("trace_snapshot", {})
    if not isinstance(snapshot, dict):
        return False
    if "schema_version" in snapshot and (
        type(snapshot["schema_version"]) is not int or snapshot["schema_version"] != 3
    ):
        return False
    if "score_policy_version" in snapshot and snapshot["score_policy_version"] != "lightweight-v1":
        return False
    if "diagnoser_definition" in snapshot:
        definition = snapshot["diagnoser_definition"]
        return (
            isinstance(definition, dict)
            and definition.get("uuid") == d["diagnoser_uuid"]
            and isinstance(definition.get("category"), str)
            and definition.get("category") in {"outcome", "process", "safety", "system"}
            and definition["category"] == category
        )
    if d["task_uuid"] in frozen_diagnoser_by_task:
        return d["diagnoser_uuid"] in frozen_diagnoser_by_task[d["task_uuid"]]
    # Only unstamped legacy records may use today's category. Modern missing
    # metadata cannot be reconstructed once its independent Task row expires.
    if "schema_version" in snapshot or "score_policy_version" in snapshot:
        return False
    return d["diagnoser_uuid"] in legacy_diagnoser_uuids


def _sort_list_items(
    items: list[DiagnosisRecordListItem], sort_by: str, sort_order: str
) -> list[DiagnosisRecordListItem]:
    if sort_by not in _LIST_SORT_FIELDS:
        raise ValueError(f"unsupported record list sort field: {sort_by!r}")
    if sort_order not in {"asc", "desc"}:
        raise ValueError(f"unsupported record list sort order: {sort_order!r}")
    items = sorted(items, key=lambda item: item.uuid)
    with_value = [item for item in items if getattr(item, sort_by) is not None]
    without_value = [item for item in items if getattr(item, sort_by) is None]
    if sort_by in _LIST_DATETIME_SORT_FIELDS:
        with_value.sort(
            key=lambda item: _datetime_rank(getattr(item, sort_by)),
            reverse=sort_order == "desc",
        )
    else:
        with_value.sort(key=lambda item: getattr(item, sort_by), reverse=sort_order == "desc")
    return with_value + without_value


class DiagnosisRecordStore:
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

    def _c(self, name: str) -> str:
        return self.dialect.col(name)

    def _base_where(self) -> list[str]:
        name_col = self._c("name")
        return [*self.dialect.base_predicates(), f"{name_col} = 'diagnosis_record'"]

    def _time_where(
        self, start_time: str | None, end_time: str | None, default_days: int = 7
    ) -> list[str]:
        if not end_time:
            end_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if not start_time:
            start_time = (datetime.now() - timedelta(days=default_days)).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        return [
            self.dialect.time_ge("period", _esc(start_time, _SAFE_TS)),
            self.dialect.time_lt("period", _esc(end_time, _SAFE_TS)),
        ]

    def _target_time_where(
        self,
        start_time: str | None,
        end_time: str | None,
        default_days: int = 7,
    ) -> list[str]:
        if not end_time:
            end_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if not start_time:
            start_time = (datetime.now() - timedelta(days=default_days)).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        target_created_at = self._c("attributes.target_created_at")
        return [
            f"{target_created_at} >= '{_esc(start_time, _SAFE_TS)}'",
            f"{target_created_at} < '{_esc(end_time, _SAFE_TS)}'",
        ]

    def _record_time_where(
        self,
        start_time: str | None,
        end_time: str | None,
        default_days: int = 7,
        *,
        task_uuid: str | None = None,
    ) -> list[str]:
        # An exact Task identifies its retained batch, including old source Traces.
        if task_uuid and start_time is None and end_time is None:
            return []
        # Windowed reads also bound the physical scan. Use now as its upper bound
        # so delayed writes and later feedback versions remain visible.
        return self._time_where(start_time, None, default_days) + self._target_time_where(
            start_time, end_time, default_days
        )

    def query_records(
        self,
        job_uuid: str,
        verdict: str | list[str] | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        task_uuid: str | None = None,
    ) -> list[DiagnosisRecord]:
        job_col = self._c("attributes.job_uuid")
        verdict_col = self._c("attributes.verdict")
        task_col = self._c("attributes.task_uuid")
        where = [*self._base_where(), f"{job_col} = '{_esc(job_uuid)}'"]
        if isinstance(verdict, list):
            values = ",".join(f"'{_esc(value)}'" for value in verdict)
            where.append(f"{verdict_col} IN ({values})")
        elif verdict:
            where.append(f"{verdict_col} = '{_esc(verdict)}'")
        if task_uuid:
            where.append(f"{task_col} = '{_esc(task_uuid)}'")
        where += self._record_time_where(start_time, end_time, task_uuid=task_uuid)
        sql = self._select_all(where) + self._order_by_period_desc()
        rows = self.reader.query(sql)
        return _dedup_latest([_record_from_row(r) for r in rows])

    def _select_all(self, where: list[str]) -> str:
        return f"SELECT * FROM {self.table} WHERE " + " AND ".join(where)

    def _order_by_period_desc(self) -> str:
        return f" ORDER BY {self._c('period')} DESC"

    def query_trace_records(
        self,
        *,
        job_uuid: str,
        task_uuid: str,
        trace_id: str,
    ) -> list[DiagnosisRecord]:
        job_col = self._c("attributes.job_uuid")
        task_col = self._c("attributes.task_uuid")
        target_col = self._c("attributes.target_id")
        where = [
            *self._base_where(),
            f"{job_col} = '{_esc(job_uuid)}'",
            f"{task_col} = '{_esc(task_uuid)}'",
            f"{target_col} = '{_esc(trace_id)}'",
        ]
        sql = self._select_all(where) + self._order_by_period_desc()
        rows = self.reader.query(sql)
        return _dedup_latest([_record_from_row(row) for row in rows])

    def query_record_list(
        self,
        job_uuid: str | list[str],
        verdict: str | list[str] | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        task_uuid: str | None = None,
        owner_id: str | None = None,
        review_status: str | None = None,
        diagnoser_uuid: str | list[str] | None = None,
        exp_ver_id: list[str] | None = None,
        model: str | list[str] | None = None,
        keyword: str | None = None,
        limit: int = 50,
        offset: int = 0,
        sort_by: str = "diagnosed_at",
        sort_order: str = "desc",
        frozen_diagnoser_by_task: dict[str, list[str]] | None = None,
        category: str | None = None,
        legacy_diagnoser_uuids: list[str] | None = None,
    ) -> tuple[list[DiagnosisRecordListItem], int]:
        job_uuids = [job_uuid] if isinstance(job_uuid, str) else list(dict.fromkeys(job_uuid))
        if not job_uuids:
            return [], 0
        job_col = self._c("attributes.job_uuid")
        verdict_col = self._c("attributes.verdict")
        owner_col = self._c("attributes.owner_id")
        diagnoser_col = self._c("attributes.diagnoser_uuid")
        task_col = self._c("attributes.task_uuid")
        exp_ver_col = self._c("attributes.exp_ver_id")
        model_col = self._c("attributes.model")
        if len(job_uuids) == 1:
            where = [*self._base_where(), f"{job_col} = '{_esc(job_uuids[0])}'"]
        else:
            values = ",".join(f"'{_esc(value)}'" for value in job_uuids)
            where = [*self._base_where(), f"{job_col} IN ({values})"]
        verdicts: set[str] | None = None
        if isinstance(verdict, list):
            verdicts = {_esc(value) for value in verdict}
            if not verdicts:
                return [], 0
        elif verdict:
            verdicts = {_esc(verdict)}
        if verdicts is not None:
            values = ",".join(f"'{value}'" for value in sorted(verdicts))
            where.append(f"{verdict_col} IN ({values})")
        if owner_id:
            owner_id = _esc(owner_id)
            where.append(f"{owner_col} = '{owner_id}'")
        if isinstance(diagnoser_uuid, list):
            diagnoser_uuids = list(dict.fromkeys(diagnoser_uuid))
            if not diagnoser_uuids:
                return [], 0
            if len(diagnoser_uuids) == 1:
                where.append(f"{diagnoser_col} = '{_esc(diagnoser_uuids[0])}'")
            else:
                values = ",".join(f"'{_esc(value)}'" for value in diagnoser_uuids)
                where.append(f"{diagnoser_col} IN ({values})")
        elif diagnoser_uuid:
            where.append(f"{diagnoser_col} = '{_esc(diagnoser_uuid)}'")
        if task_uuid:
            where.append(f"{task_col} = '{_esc(task_uuid)}'")
        exp_ver_ids = _normalize_exp_ver_ids(exp_ver_id)
        if exp_ver_ids:
            clauses = [f"{exp_ver_col} LIKE '%|{value}|%'" for value in exp_ver_ids]
            where.append("(" + " OR ".join(clauses) + ")")
        if isinstance(model, list):
            models = list(dict.fromkeys(_esc(value.strip()) for value in model if value.strip()))
        elif isinstance(model, str) and model.strip():
            models = [_esc(model.strip())]
        else:
            models = []
        if models:
            clauses = []
            for value in models:
                if value == "missing_model":
                    clauses.append(f"{model_col} = ''")
                else:
                    clauses.append(f"{model_col} = '{value}'")
            # Empty model is a compatibility view over rows written before the
            # promoted column existed; leave that predicate to the in-memory
            # pass so NULL/absent historical values are also included.
            if not all(value == "missing_model" for value in models):
                where.append(clauses[0] if len(clauses) == 1 else "(" + " OR ".join(clauses) + ")")
        where += self._record_time_where(start_time, end_time, task_uuid=task_uuid)

        def _query(columns: tuple[str, ...]) -> list[dict[str, Any]]:
            selected = ", ".join(self.dialect.select_col(column) for column in columns)
            sql = (
                f"SELECT {selected} FROM {self.table} WHERE "
                + " AND ".join(where)
                + f" LIMIT {_RECORD_LIST_QUERY_LIMIT}"
            )
            return self.reader.query(sql)

        columns = list(_LIST_COLUMNS)
        columns.append("attributes.record.body")
        while True:
            try:
                rows = _query(tuple(columns))
                break
            except SignozQueryError as error:
                missing = _missing_optional_column(error)
                if missing is None:
                    raise
                if exp_ver_ids and missing == "attributes.exp_ver_id":
                    return [], 0
                if models and missing == "attributes.model":
                    return [], 0
                if missing not in columns:
                    raise
                columns.remove(missing)
        rows = _dedup_list_rows(rows)
        if category is not None:
            rows = [
                row
                for row in rows
                if _matches_record_category(
                    row, category, frozen_diagnoser_by_task or {}, legacy_diagnoser_uuids or []
                )
            ]
        items = [_list_item_from_row(row) for row in rows]
        if owner_id:
            items = [item for item in items if item.owner_id == owner_id]
        if review_status is not None:
            items = [item for item in items if item.review_status.value == review_status]
        if keyword:
            normalized_keyword = keyword.casefold()
            items = [
                item
                for item in items
                if normalized_keyword in item.target_id.casefold()
                or normalized_keyword in item.summary.casefold()
            ]
        if exp_ver_ids:
            items = [
                item
                for item in items
                if any(f"|{value}|" in item.exp_ver_id for value in exp_ver_ids)
            ]
        if models:
            items = [
                item
                for item in items
                if any(
                    item.model == "" if value == "missing_model" else item.model == value
                    for value in models
                )
            ]
        if category is None and frozen_diagnoser_by_task is not None:
            items = [
                item
                for item in items
                if item.diagnoser_uuid in frozen_diagnoser_by_task.get(item.task_uuid, [])
            ]
        items = _sort_list_items(items, sort_by, sort_order)
        total = len(items)
        return items[offset : offset + limit], total

    def query_records_multi(
        self,
        job_uuids: list[str],
        verdict: str | list[str] | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        task_uuid: str | None = None,
    ) -> list[DiagnosisRecord]:
        if not job_uuids:
            return []
        esc_jobs = ",".join(f"'{_esc(j)}'" for j in job_uuids)
        job_col = self._c("attributes.job_uuid")
        verdict_col = self._c("attributes.verdict")
        task_col = self._c("attributes.task_uuid")
        where = [*self._base_where(), f"{job_col} IN ({esc_jobs})"]
        if isinstance(verdict, list):
            values = ",".join(f"'{_esc(value)}'" for value in verdict)
            where.append(f"{verdict_col} IN ({values})")
        elif verdict:
            where.append(f"{verdict_col} = '{_esc(verdict)}'")
        if task_uuid:
            where.append(f"{task_col} = '{_esc(task_uuid)}'")
        where += self._record_time_where(start_time, end_time, task_uuid=task_uuid)
        sql = self._select_all(where) + self._order_by_period_desc()
        rows = self.reader.query(sql)
        return _dedup_latest([_record_from_row(r) for r in rows])

    def get_record(self, uuid: str) -> DiagnosisRecord | None:
        end = datetime.now().isoformat(timespec="microseconds")
        start = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d %H:%M:%S")
        uuid_col = self._c("attributes.uuid")
        where = [
            *self._base_where(),
            f"{uuid_col} = '{_esc(uuid)}'",
            self.dialect.time_ge("period", _esc(start, _SAFE_TS)),
            self.dialect.time_lt("period", _esc(end, _SAFE_VERSION_TS)),
        ]
        attempt_col = self.dialect.int_col("attributes.attempt")
        sql = (
            self._select_all(where)
            + f" ORDER BY {attempt_col} DESC, {self._c('period')} DESC LIMIT 1"
        )
        rows = self.reader.query(sql)
        if not rows:
            return None
        records = _dedup_latest([_record_from_row(row) for row in rows])
        return records[0]

    def get_record_version(
        self,
        uuid: str,
        *,
        attempt: int,
        reviewed_at: datetime | None,
    ) -> DiagnosisRecord | None:
        normalized_reviewed_at = normalize_record_datetime(reviewed_at)
        if normalized_reviewed_at is None:
            return None
        reviewed_at_literal = normalized_reviewed_at.isoformat(timespec="microseconds")
        start = (normalized_reviewed_at - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
        end = (datetime.now() + timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
        uuid_col = self._c("attributes.uuid")
        attempt_col = self.dialect.int_col("attributes.attempt")
        reviewed_col = self._c("attributes.reviewed_at")
        where = [
            *self._base_where(),
            f"{uuid_col} = '{_esc(uuid)}'",
            f"{attempt_col} = {int(attempt)}",
            f"{reviewed_col} = '{_esc(reviewed_at_literal, _SAFE_VERSION_TS)}'",
            self.dialect.time_ge("period", _esc(start, _SAFE_TS)),
            self.dialect.time_lt("period", _esc(end, _SAFE_TS)),
        ]
        sql = self._select_all(where) + self._order_by_period_desc() + " LIMIT 1"
        rows = self.reader.query(sql)
        return _record_from_row(rows[0]) if rows else None

    def write_record(self, record: DiagnosisRecord, *, attempt: int = 0) -> str:
        d = _dict_from_record(record)
        d["attempt"] = attempt
        self.writer.write_span(
            self.table,
            "diagnosis_record",
            _promoted_attributes(d),
            d,
        )
        return record.uuid

    def write_record_and_wait_visible(
        self,
        record: DiagnosisRecord,
        *,
        attempt: int = 0,
        visibility_timeout_seconds: float = _RECORD_VISIBILITY_TIMEOUT_SECONDS,
        poll_interval_seconds: float = _RECORD_VISIBILITY_POLL_INTERVAL_SECONDS,
    ) -> DiagnosisRecord | None:
        """Write one feedback version and wait until SigNoz returns it."""
        self.write_record(record, attempt=attempt)
        try:
            flushed = self.writer.force_flush(
                timeout_millis=_RECORD_VISIBILITY_FLUSH_TIMEOUT_MILLIS
            )
        except Exception as exc:
            raise ExternalUnavailable("failed to flush diagnosis record") from exc
        if not flushed:
            raise ExternalUnavailable("failed to flush diagnosis record")

        deadline = time.monotonic() + max(0.0, visibility_timeout_seconds)
        while True:
            visible = self.get_record_version(
                record.uuid,
                attempt=attempt,
                reviewed_at=record.reviewed_at,
            )
            if visible is not None:
                return visible
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            time.sleep(min(max(0.0, poll_interval_seconds), remaining))
