"""Column dialects that render SigNoz-style logical columns into physical SQL.

AgentLens 的 store 层（TraceStore / DiagnosisRecordStore / TaskStatsStore）以及
Projector 都按 SigNoz 时代的"扁平行"形状工作：

* ``name`` / ``period`` / ``trace_id`` / ``start_time_unix_nano`` ... 为顶层列；
* span 属性以 ``attributes.<key>`` 列出现，资源属性以 ``resource.attributes.<key>`` 出现。

SigNoz 改造后的 OceanBase ``signoz_traces`` 表则把属性收敛进 JSON 列。为了让 store
与 Projector 不感知存储差异，所有 SQL 中的列引用统一经 :class:`ColumnDialect`
渲染，读出的行再由 client 层（见 ``ob_client.py``）展平回逻辑列形状。

* :class:`FlatColumnDialect` —— 逻辑列即物理列（内存测试 client 使用）。
* :class:`SignozObDialect` —— OceanBase ``signoz_traces`` 物理表。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy import ColumnElement, column, func
from sqlalchemy.sql import quoted_name

_BACKTICK_IDENT = re.compile(r"`((?:[^`]|``)+)`")

ATTRIBUTES_PREFIX = "attributes."
RESOURCE_ATTRIBUTES_PREFIX = "resource.attributes."

# 逻辑列 -> signoz_traces 物理列
LOGICAL_TO_PHYSICAL: dict[str, str] = {
    "name": "span_name",
    "period": "timestamp",
    "kind": "span_kind",
}
PHYSICAL_TO_LOGICAL: dict[str, str] = {v: k for k, v in LOGICAL_TO_PHYSICAL.items()}

_PERIOD_FORMATS = (
    "%Y-%m-%dT%H:%M:%S.%f%z",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%d %H:%M:%S.%f%z",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
)


def quote_identifier(value: str) -> str:
    if not value:
        raise ValueError("SQL identifier must not be empty")
    return f"`{value.replace('`', '``')}`"


def quote_literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


def parse_period(value: str) -> datetime:
    """Parse a SigNoz-style period literal into a naive local datetime."""
    text = value.strip()
    for fmt in _PERIOD_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone().replace(tzinfo=None)
        return parsed
    raise ValueError(f"cannot parse period: {value!r}")


def period_to_unix_nano(value: str) -> int:
    """Convert a period literal (local naive time) into epoch nanoseconds."""
    parsed = parse_period(value)
    return int(parsed.timestamp()) * 1_000_000_000 + parsed.microsecond * 1_000


def unix_nano_to_period(value: int | float) -> str:
    """Render epoch nanoseconds as the period text shape the stores expect."""
    nanos = int(value)
    seconds, remainder = divmod(nanos, 1_000_000_000)
    parsed = datetime.fromtimestamp(seconds).replace(microsecond=remainder // 1_000)
    return parsed.isoformat(sep=" ", timespec="microseconds")


class ColumnDialect(Protocol):
    def column(self, name: str) -> ColumnElement[Any]:
        """Build a SQLAlchemy expression for a logical column."""

    def tenant_conditions(self) -> tuple[ColumnElement[bool], ...]:
        """Build the tenant predicates for a SQLAlchemy query."""

    def col(self, name: str, *, parameters: dict[str, Any] | None = None) -> str:
        """Render one logical column as a SQL expression usable in WHERE / ORDER BY."""

    def select_col(self, name: str, *, parameters: dict[str, Any] | None = None) -> str:
        """Render one logical column for a SELECT list (aliased back to its logical name)."""

    def int_col(self, name: str) -> str:
        """Render one logical column as an integer-typed expression (for ORDER BY / compare)."""

    def time_ge(self, name: str, period: str) -> str:
        """Render ``<time column> >= <period literal>``."""

    def time_lt(self, name: str, period: str) -> str:
        """Render ``<time column> < <period literal>``."""

    def period_value(self, period: str) -> str | int:
        """Convert a logical period to the column's parameter type."""

    def rewrite_where(self, where: str) -> str:
        """Rewrite a caller-provided WHERE fragment whose identifiers are backtick-quoted."""

    def base_predicates(self, *, parameters: dict[str, Any] | None = None) -> tuple[str, ...]:
        """Predicates that must be appended to every query (tenant isolation etc.)."""


class FlatColumnDialect:
    """Logical columns are physical columns (SigNoz-shaped flat tables)."""

    def column(self, name: str) -> ColumnElement[Any]:
        return column(quoted_name(name, True))

    def tenant_conditions(self) -> tuple[ColumnElement[bool], ...]:
        return ()

    def col(self, name: str, *, parameters: dict[str, Any] | None = None) -> str:
        return quote_identifier(name)

    def select_col(self, name: str, *, parameters: dict[str, Any] | None = None) -> str:
        return quote_identifier(name)

    def int_col(self, name: str) -> str:
        return quote_identifier(name)

    def time_ge(self, name: str, period: str) -> str:
        return f"{self.col(name)} >= {quote_literal(period)}"

    def time_lt(self, name: str, period: str) -> str:
        return f"{self.col(name)} < {quote_literal(period)}"

    def period_value(self, period: str) -> str:
        return period

    def rewrite_where(self, where: str) -> str:
        return where

    def base_predicates(self, *, parameters: dict[str, Any] | None = None) -> tuple[str, ...]:
        return ()


class SignozObDialect:
    """OceanBase ``signoz_traces`` physical table.

    * ``name`` → ``span_name``，``kind`` → ``span_kind``；
    * ``period`` → ``timestamp``（epoch 纳秒 BIGINT），时间边界在 Python 侧换算；
    * ``attributes.<k>`` → ``JSON_UNQUOTE(JSON_EXTRACT(`attributes`, '$."<k>"'))``；
    * ``resource.attributes.<k>`` → 同上，取 ``resource_attributes`` 列；
    * 每条查询追加 ``org_id = <SIGNOZ_ORG_ID>``。

    ``rewrite_where`` 只改写反引号包裹的标识符；Dataset 里裸写的列名不做处理。
    """

    def __init__(self, *, org_id: str) -> None:
        if not org_id:
            raise ValueError("SIGNOZ_ORG_ID must not be empty")
        self.org_id = org_id

    # ── 列渲染 ──────────────────────────────────────────────────────────────
    @staticmethod
    def _resolve_column(name: str) -> tuple[str, str | None]:
        if name.startswith(RESOURCE_ATTRIBUTES_PREFIX):
            physical_name = "resource_attributes"
            key = name[len(RESOURCE_ATTRIBUTES_PREFIX) :]
        elif name.startswith(ATTRIBUTES_PREFIX):
            physical_name = "attributes"
            key = name[len(ATTRIBUTES_PREFIX) :]
        else:
            return LOGICAL_TO_PHYSICAL.get(name, name), None
        escaped = key.replace("\\", "\\\\").replace('"', '\\"')
        return physical_name, f'$."{escaped}"'

    def column(self, name: str) -> ColumnElement[Any]:
        physical_name, path = self._resolve_column(name)
        physical_column = column(quoted_name(physical_name, True))
        if path is None:
            return physical_column
        return func.JSON_UNQUOTE(func.JSON_EXTRACT(physical_column, path))

    def tenant_conditions(self) -> tuple[ColumnElement[bool], ...]:
        return (self.column("org_id") == self.org_id,)

    def col(self, name: str, *, parameters: dict[str, Any] | None = None) -> str:
        physical_name, path = self._resolve_column(name)
        physical_column = quote_identifier(physical_name)
        if path is None:
            return physical_column
        if parameters is None:
            expression = quote_literal(path)
        else:
            name = f"attribute_path_{len(parameters)}"
            parameters[name] = path
            expression = f":{name}"
        return f"JSON_UNQUOTE(JSON_EXTRACT({physical_column}, {expression}))"

    def select_col(self, name: str, *, parameters: dict[str, Any] | None = None) -> str:
        rendered = self.col(name, parameters=parameters)
        quoted = quote_identifier(name)
        if rendered == quoted:
            return rendered
        return f"{rendered} AS {quoted}"

    def int_col(self, name: str) -> str:
        rendered = self.col(name)
        if name.startswith((ATTRIBUTES_PREFIX, RESOURCE_ATTRIBUTES_PREFIX)):
            return f"CAST({rendered} AS SIGNED)"
        return rendered

    def time_ge(self, name: str, period: str) -> str:
        return f"{self.col(name)} >= {period_to_unix_nano(period)}"

    def time_lt(self, name: str, period: str) -> str:
        return f"{self.col(name)} < {period_to_unix_nano(period)}"

    def period_value(self, period: str) -> int:
        return period_to_unix_nano(period)

    def rewrite_where(self, where: str) -> str:
        if not where:
            return where

        def _replace(match: re.Match[str]) -> str:
            identifier = match.group(1).replace("``", "`")
            return self.col(identifier)

        return _BACKTICK_IDENT.sub(_replace, where)

    def base_predicates(self, *, parameters: dict[str, Any] | None = None) -> tuple[str, ...]:
        if parameters is not None:
            parameters["org_id"] = self.org_id
            return ("`org_id` = :org_id",)
        return (f"`org_id` = {quote_literal(self.org_id)}",)


__all__ = [
    "ATTRIBUTES_PREFIX",
    "ColumnDialect",
    "FlatColumnDialect",
    "LOGICAL_TO_PHYSICAL",
    "PHYSICAL_TO_LOGICAL",
    "RESOURCE_ATTRIBUTES_PREFIX",
    "SignozObDialect",
    "parse_period",
    "period_to_unix_nano",
    "quote_identifier",
    "quote_literal",
    "unix_nano_to_period",
]
