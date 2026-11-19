import re
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

from agentlens.domain.dataset import TraceQuerySpec
from agentlens.domain.diagnosis_input import validate_explicit_trace_ids
from agentlens.infra.signoz.client import SignozClient
from agentlens.infra.signoz.dialect import (
    ColumnDialect,
    FlatColumnDialect,
    parse_period,
    quote_identifier,
    quote_literal,
)
from agentlens.service.datasource.dataset_filter import (
    compile_dataset_filter,
    normalize_dataset_table,
)
from agentlens.utils.duration import parse_duration

# Keep bounded trace hydration small by default. Individual datasets may opt
# into a wider context when their projector genuinely needs it.
DEFAULT_TRACE_CONTEXT_WINDOW_MIN = 10


_quote_identifier = quote_identifier
_quote_literal = quote_literal
_parse_period = parse_period

# Dataset spec 中平台托管的 scope 字段：(列名 key, 过滤值 key)。
# 列名以 SigNoz 逻辑列（反引号包裹）写入 where，最终由 dialect.rewrite_where 翻译成物理列。
DATASET_SCOPE_FIELDS: tuple[tuple[str, str], ...] = (
    ("space_column", "space_filter"),
    ("username_column", "username"),
    ("agent_product_column", "agent_product_filter"),
)


def dataset_scope_predicates(dataset_spec: dict) -> list[str]:
    """Build escaped predicates for platform-owned Dataset scope fields."""
    predicates: list[str] = []
    for column_key, value_key in DATASET_SCOPE_FIELDS:
        column = dataset_spec.get(column_key)
        value = dataset_spec.get(value_key)
        if column and value:
            predicates.append(f"{quote_identifier(str(column))} = {quote_literal(str(value))}")
    return predicates


def _parse_time_range(value: str) -> timedelta:
    try:
        return parse_duration(value)
    except ValueError as exc:
        raise ValueError(
            f"invalid time_range: {value!r}, expected format like 1h/6h/1d/7d"
        ) from exc


def _trace_limit(value: int | str) -> int:
    if isinstance(value, str) and re.fullmatch(r"[0-9]+", value):
        value = int(value)
    if type(value) is not int or value < 0:
        raise ValueError("Dataset query.limit must be a nonnegative integer")
    return value


def _trace_ids_condition(column: str, trace_ids: list[str], parameters: dict[str, Any]) -> str:
    names = [f"selected_trace_{i}" for i in range(len(trace_ids))]
    parameters.update(zip(names, trace_ids, strict=True))
    return f"{column} IN ({', '.join(':' + name for name in names)})"


def build_sql(
    spec: TraceQuerySpec,
    *,
    dialect: ColumnDialect | None = None,
    required_conditions: tuple[str, ...] = (),
    parameters: dict[str, Any] | None = None,
    include_limit: bool = True,
) -> tuple[str, dict[str, Any]]:
    """Build a bounded scan. Extra predicates are authored by TraceStore."""
    dialect = dialect or FlatColumnDialect()
    table = ".".join(
        quote_identifier(part) for part in normalize_dataset_table(spec.table).split(".")
    )

    now = datetime.now()
    until = spec.end_time or now.strftime("%Y-%m-%d %H:%M:%S")
    since = spec.start_time or (now - _parse_time_range(spec.time_range)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    bound = dict(parameters or {})
    bound.update(
        window_start=dialect.period_value(since),
        window_end=dialect.period_value(until),
    )
    period = dialect.col("period")
    conditions = [
        *dialect.base_predicates(parameters=bound),
        f"{period} >= :window_start",
        f"{period} < :window_end",
    ]

    where = compile_dataset_filter(spec.where, dialect, bound)
    if where:
        conditions.append(f"({where})")
    conditions.extend(required_conditions)
    if include_limit:
        bound["trace_limit"] = _trace_limit(spec.limit)
    limit_clause = " LIMIT :trace_limit" if include_limit else ""
    return f"SELECT * FROM {table} WHERE {' AND '.join(conditions)}{limit_clause}", bound


class TraceStore:
    def __init__(self, client: SignozClient, *, dialect: ColumnDialect | None = None) -> None:
        self.client = client
        self.dialect = dialect or FlatColumnDialect()

    def read_explicit_trace_rows(
        self, dataset_spec: dict, trace_ids: list[str], *, for_diagnosis_input: bool = False
    ) -> list[dict]:
        """Complete exact IDs in a server-owned scope, without a relative time cutoff."""
        validate_explicit_trace_ids(trace_ids)
        table = dataset_spec.get("query", {}).get("table", "")
        if not isinstance(table, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table):
            raise ValueError("invalid explicit trace table")
        params = {f"trace_{i}": value for i, value in enumerate(trace_ids)}
        conditions = [
            *self.dialect.base_predicates(parameters=params),
            self.dialect.col("trace_id")
            + " IN ("
            + ", ".join(f":trace_{i}" for i in range(len(trace_ids)))
            + ")",
        ]
        for i, (column_key, value_key) in enumerate(DATASET_SCOPE_FIELDS):
            column, value = dataset_spec.get(column_key), dataset_spec.get(value_key)
            if not column or not value:
                if (
                    value_key in {"space_filter", "agent_product_filter"}
                    or dataset_spec.get("scope_type") == "personal"
                ):
                    raise ValueError("explicit selection requires complete scope")
                continue
            if not isinstance(column, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", column):
                raise ValueError("invalid scope column")
            params[f"scope_{i}"] = str(value)
            scoped_column = self.dialect.col(column, parameters=params)
            conditions.append(f"{scoped_column} = :scope_{i}")
        sql = (
            f"SELECT * FROM {quote_identifier(table)} WHERE {' AND '.join(conditions)}"
            f" ORDER BY {self.dialect.col('period')} ASC"
        )
        if for_diagnosis_input:
            # Only ephemeral preflight material retains these original marker types.
            # Runner/CUSTOM/Core continue to receive the default logical strings.
            return self.client.query(
                sql,
                parameters=params,
                log_sql=False,
                preserve_attribute_types=("record.body", "job_uuid", "task_uuid"),
            )
        return self.client.query(sql, parameters=params, log_sql=False)

    def read_trace_rows_by_id(
        self,
        spec: TraceQuerySpec,
        trace_id: str,
    ) -> list[dict]:
        """Read one trace from the caller's bounded datasource scope."""
        normalized_trace_id = trace_id.strip()
        if not normalized_trace_id:
            return []
        d = self.dialect
        trace = d.col("trace_id")
        sql, params = build_sql(
            spec,
            dialect=d,
            required_conditions=(f"{trace} IS NOT NULL", f"{trace} = :trace_id"),
            parameters={"trace_id": normalized_trace_id},
            include_limit=False,
        )
        return self.client.query(
            sql + f" ORDER BY {d.col('period')} ASC",
            parameters=params,
        )

    def read_recent_trace_rows(
        self,
        spec: TraceQuerySpec,
        *,
        trace_spec: TraceQuerySpec | None = None,
        count: int = 3,
        window_min: int = DEFAULT_TRACE_CONTEXT_WINDOW_MIN,
    ) -> list[list[dict]]:
        """Read recent complete traces using the Runner's bounded hydration shape."""

        d = self.dialect
        trace = d.col("trace_id")
        latest_sql, latest_params = build_sql(
            spec,
            dialect=d,
            required_conditions=(f"{trace} IS NOT NULL", f"{trace} != ''"),
            parameters={"recent_count": max(1, count)},
            include_limit=False,
        )
        candidates = self.client.query(
            latest_sql.replace("SELECT *", f"SELECT {trace}, MAX({d.col('period')}) AS `period`", 1)
            + f" GROUP BY {trace} ORDER BY `period` DESC LIMIT :recent_count",
            parameters=latest_params,
        )
        trace_ids = list(
            dict.fromkeys(
                str(candidate.get("trace_id") or "")
                for candidate in candidates
                if candidate.get("trace_id")
            )
        )
        if not trace_ids:
            return []

        base_trace_spec = trace_spec or spec
        try:
            if not spec.start_time or not spec.end_time:
                raise ValueError("sample query window is not bounded")
            start = _parse_period(spec.start_time) - timedelta(minutes=window_min)
            end = _parse_period(spec.end_time) + timedelta(minutes=window_min)
            rows_spec = TraceQuerySpec(
                table=base_trace_spec.table,
                where=base_trace_spec.where,
                time_range=base_trace_spec.time_range,
                start_time=start.strftime("%Y-%m-%d %H:%M:%S"),
                end_time=end.strftime("%Y-%m-%d %H:%M:%S"),
                limit=base_trace_spec.limit,
            )
        except ValueError:
            rows_spec = base_trace_spec

        selected: dict[str, Any] = {}
        rows_sql, params = build_sql(
            rows_spec,
            dialect=d,
            required_conditions=(_trace_ids_condition(trace, trace_ids, selected),),
            parameters=selected,
            include_limit=False,
        )
        rows = self.client.query(
            rows_sql + f" ORDER BY {d.col('period')} ASC",
            parameters=params,
        )
        grouped: dict[str, list[dict]] = {}
        for row in rows:
            trace_id = str(row.get("trace_id") or "")
            if trace_id in trace_ids:
                grouped.setdefault(trace_id, []).append(row)
        return [grouped[trace_id] for trace_id in trace_ids if trace_id in grouped]

    def read_trace_targets(
        self,
        spec: TraceQuerySpec,
        fields: set[str],
        span_names: set[str],
    ) -> tuple[list[str], list[dict]]:
        """Limit distinct targets, then read all matching lightweight Span rows."""
        d = self.dialect
        trace = d.col("trace_id")
        required_conditions = [f"{trace} IS NOT NULL", f"{trace} != ''"]
        name_params: dict[str, Any] = {}
        where_is_empty = spec.where is None or (
            isinstance(spec.where, str) and not spec.where.strip()
        )
        if where_is_empty and span_names:
            names = [f"required_name_{i}" for i in range(len(span_names))]
            name_params.update(zip(names, sorted(span_names), strict=True))
            placeholders = ", ".join(f":{name}" for name in names)
            required_conditions.append(f"{d.col('name')} IN ({placeholders})")
        # Render once so relative windows cannot drift between the two reads.
        base_sql, base_params = build_sql(
            spec,
            dialect=d,
            required_conditions=tuple(required_conditions),
            parameters=name_params,
            include_limit=False,
        )
        candidate_sql = base_sql.replace("SELECT *", f"SELECT DISTINCT {trace}", 1)
        candidates = self.client.query(
            candidate_sql + f" ORDER BY {trace} ASC LIMIT :trace_limit",
            parameters={**base_params, "trace_limit": _trace_limit(spec.limit)},
        )
        seen: set[str] = set()
        keys: list[str] = []
        for row in candidates:
            trace_id = row.get("trace_id")
            if trace_id and trace_id not in seen:
                seen.add(trace_id)
                keys.append(trace_id)
        if not keys:
            return [], []

        fields_params = base_params.copy()
        selected = ["trace_id", "start_time_unix_nano", "name", *sorted(fields)]
        select_clause = ", ".join(
            d.select_col(field, parameters=fields_params) for field in dict.fromkeys(selected)
        )
        fields_sql = (
            base_sql.replace("SELECT *", f"SELECT {select_clause}", 1)
            + f" AND {_trace_ids_condition(trace, keys, fields_params)}"
            + f" ORDER BY {trace} ASC, {d.col('start_time_unix_nano')} ASC, "
            + f"{d.col('span_id')} ASC"
        )
        rows = self.client.query(fields_sql, parameters=fields_params)
        return keys, rows

    def read_trace_spans(
        self,
        base_spec: TraceQuerySpec,
        trace_id: str,
        center_time: str,
        window_min: int = DEFAULT_TRACE_CONTEXT_WINDOW_MIN,
    ) -> list[dict]:
        center = parse_period(center_time)
        start = (center - timedelta(minutes=window_min)).strftime("%Y-%m-%d %H:%M:%S")
        end = (center + timedelta(minutes=window_min)).strftime("%Y-%m-%d %H:%M:%S")
        return self.read_trace_spans_range(base_spec, trace_id, start, end)

    def read_trace_spans_range(
        self,
        base_spec: TraceQuerySpec,
        trace_id: str,
        start_time: str,
        end_time: str,
    ) -> list[dict]:
        """Read one known trace inside a caller-provided bounded source window."""
        return self.read_trace_spans_batch_range(base_spec, [trace_id], start_time, end_time)

    def read_trace_spans_batch(
        self,
        base_spec: TraceQuerySpec,
        trace_ids: list[str],
        center_time: str,
        window_min: int = DEFAULT_TRACE_CONTEXT_WINDOW_MIN,
    ) -> list[dict]:
        if not trace_ids:
            return []
        center = parse_period(center_time)
        start = (center - timedelta(minutes=window_min)).strftime("%Y-%m-%d %H:%M:%S")
        end = (center + timedelta(minutes=window_min)).strftime("%Y-%m-%d %H:%M:%S")
        return self.read_trace_spans_batch_range(base_spec, trace_ids, start, end)

    def read_trace_spans_batch_range(
        self, base_spec: TraceQuerySpec, trace_ids: list[str], start_time: str, end_time: str
    ) -> list[dict]:
        if not trace_ids:
            return []
        if not start_time or not end_time:
            raise ValueError("Trace hydration requires a bounded time window")
        d = self.dialect
        selected: dict[str, Any] = {}
        sql, params = build_sql(
            replace(base_spec, start_time=start_time, end_time=end_time),
            dialect=d,
            required_conditions=(_trace_ids_condition(d.col("trace_id"), trace_ids, selected),),
            parameters=selected,
            include_limit=False,
        )
        return self.client.query(sql, parameters=params)
