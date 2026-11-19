import re
from datetime import datetime, timedelta

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


def _where_suffix(spec: TraceQuerySpec, dialect: ColumnDialect) -> str:
    return f" AND ({dialect.rewrite_where(spec.where)})" if spec.where else ""


def _base_suffix(dialect: ColumnDialect) -> str:
    predicates = dialect.base_predicates()
    return "".join(f" AND {predicate}" for predicate in predicates)


def _parse_time_range(value: str) -> timedelta:
    try:
        return parse_duration(value)
    except ValueError as exc:
        raise ValueError(
            f"invalid time_range: {value!r}, expected format like 1h/6h/1d/7d"
        ) from exc


def build_sql(
    spec: TraceQuerySpec,
    *,
    dialect: ColumnDialect | None = None,
    required_conditions: tuple[str, ...] = (),
    include_limit: bool = True,
) -> str:
    """Render the bounded source scan for one Dataset spec.

    ``spec.where`` 与 ``required_conditions`` 均按 SigNoz 逻辑列（反引号包裹）书写，
    由 ``dialect.rewrite_where`` 翻译为物理列；时间边界与租户谓词由 dialect 直接渲染。
    """
    dialect = dialect or FlatColumnDialect()
    now = datetime.now()

    if spec.start_time and spec.end_time:
        since = spec.start_time
        until = spec.end_time
    else:
        until = spec.end_time or now.strftime("%Y-%m-%d %H:%M:%S")
        since = spec.start_time or (now - _parse_time_range(spec.time_range)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

    conditions = [
        *dialect.base_predicates(),
        f"{dialect.time_ge('period', since)} AND {dialect.time_lt('period', until)}",
    ]

    if spec.where:
        conditions.append(f"({dialect.rewrite_where(spec.where)})")
    conditions.extend(dialect.rewrite_where(condition) for condition in required_conditions)

    limit_clause = f" LIMIT {spec.limit}" if include_limit else ""
    return f"SELECT * FROM {spec.table} WHERE {' AND '.join(conditions)}{limit_clause}"


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
            conditions.append(f"{self.dialect.col(column)} = :scope_{i}")
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

    def _sql(
        self,
        spec: TraceQuerySpec,
        *,
        required_conditions: tuple[str, ...] = (),
        include_limit: bool = True,
    ) -> str:
        return build_sql(
            spec,
            dialect=self.dialect,
            required_conditions=required_conditions,
            include_limit=include_limit,
        )

    def _trace_window_sql(
        self,
        base_spec: TraceQuerySpec,
        trace_predicate: str,
        start_time: str,
        end_time: str,
    ) -> str:
        d = self.dialect
        return (
            f"SELECT * FROM {base_spec.table}"
            f" WHERE {d.rewrite_where(trace_predicate)}"
            f" AND {d.time_ge('period', start_time)}"
            f" AND {d.time_lt('period', end_time)}"
            f"{_where_suffix(base_spec, d)}{_base_suffix(d)}"
        )

    def read_trace_rows_by_id(
        self,
        spec: TraceQuerySpec,
        trace_id: str,
    ) -> list[dict]:
        """Read one trace from the caller's bounded datasource scope."""
        normalized_trace_id = trace_id.strip()
        if not normalized_trace_id:
            return []
        sql = (
            self._sql(
                spec,
                required_conditions=(
                    "`trace_id` IS NOT NULL",
                    f"`trace_id` = {quote_literal(normalized_trace_id)}",
                ),
                include_limit=False,
            )
            + f" ORDER BY {self.dialect.col('period')} ASC"
        )
        return self.client.query(
            sql,
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
        latest_sql = (
            self._sql(
                spec,
                required_conditions=(
                    "`trace_id` IS NOT NULL",
                    "`trace_id` != ''",
                ),
                include_limit=False,
            ).replace(
                "SELECT *",
                f"SELECT {d.col('trace_id')}, MAX({d.col('period')}) AS `period`",
                1,
            )
            + f" GROUP BY {d.col('trace_id')} ORDER BY `period` DESC LIMIT {max(1, count)}"
        )
        candidates = self.client.query(
            latest_sql,
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

        trace_id_sql = ", ".join(quote_literal(trace_id) for trace_id in trace_ids)
        rows_sql = (
            self._sql(
                rows_spec,
                required_conditions=(f"`trace_id` IN ({trace_id_sql})",),
                include_limit=False,
            )
            + f" ORDER BY {d.col('period')} ASC"
        )
        rows = self.client.query(rows_sql)
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
        selected = ["trace_id", "start_time_unix_nano", "name", *sorted(fields)]
        select_clause = ", ".join(
            self.dialect.select_col(field) for field in dict.fromkeys(selected)
        )
        required_conditions = [
            "`trace_id` IS NOT NULL",
            "`trace_id` != ''",
        ]
        if not spec.where and span_names:
            names = ", ".join(quote_literal(name) for name in sorted(span_names))
            required_conditions.append(f"`name` IN ({names})")
        # Render once so relative windows cannot drift between the two reads.
        base_sql = self._sql(
            spec,
            required_conditions=tuple(required_conditions),
            include_limit=False,
        )
        trace_id_col = self.dialect.col("trace_id")
        candidate_sql = base_sql.replace("SELECT *", f"SELECT DISTINCT {trace_id_col}", 1)
        candidates = self.client.query(
            candidate_sql + f" ORDER BY {trace_id_col} ASC LIMIT {spec.limit}"
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

        trace_ids = ", ".join(quote_literal(key) for key in keys)
        fields_sql = (
            base_sql.replace("SELECT *", f"SELECT {select_clause}", 1)
            + f" AND {trace_id_col} IN ({trace_ids})"
            + f" ORDER BY {trace_id_col} ASC, {self.dialect.col('start_time_unix_nano')} ASC, "
            + f"{self.dialect.col('span_id')} ASC"
        )
        rows = self.client.query(fields_sql)
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
        sql = self._trace_window_sql(
            base_spec,
            f"`trace_id` = {quote_literal(trace_id)}",
            start_time,
            end_time,
        )

        return self.client.query(sql)

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
        in_clause = ", ".join(quote_literal(value) for value in trace_ids)
        sql = self._trace_window_sql(
            base_spec, f"`trace_id` IN ({in_clause})", start_time, end_time
        )

        return self.client.query(sql)
