import re
from collections import defaultdict
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy import Select
from sqlalchemy.dialects import mysql

_SQL_QUOTED = r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|`(?:``|[^`])*`"
_CONJUNCTION_GROUPING = re.compile(
    rf"({_SQL_QUOTED}|\bIN\s*\((?:{_SQL_QUOTED}|[^()'\"`])*\))|[()]",
    re.IGNORECASE,
)


class SignozQueryError(RuntimeError):
    """The telemetry store accepted the request but rejected / failed the SQL."""


class SignozClient(Protocol):
    def query(
        self,
        sql: str,
        *,
        log_sql: bool = True,
        parameters: dict[str, Any] | None = None,
        preserve_attribute_types: tuple[str, ...] = (),
    ) -> list[dict[str, Any]]: ...

    def query_select(
        self,
        statement: Select[Any],
        *,
        log_sql: bool = True,
        preserve_attribute_types: tuple[str, ...] = (),
    ) -> list[dict[str, Any]]: ...


class InMemorySignozClient:
    """Test/local adapter with simple AND predicates and an optional shared store.

    Use a SQL-backed fixture for OR expressions; this adapter is not a full SQL engine.
    """

    def __init__(self, store: dict[str, list[dict]] | None = None) -> None:
        self._tables: dict[str, list[dict]] = store if store is not None else defaultdict(list)

    def query(
        self,
        sql: str,
        *,
        log_sql: bool = True,
        parameters: dict[str, Any] | None = None,
        preserve_attribute_types: tuple[str, ...] = (),
    ) -> list[dict[str, Any]]:
        if parameters is not None:
            # Test adapter only; production uses SQLAlchemy bound parameters.
            from agentlens.infra.signoz.dialect import quote_literal

            def render_parameter(match: re.Match[str]) -> str:
                value = parameters[match[1]]
                if type(value) is int:
                    return str(value)
                return quote_literal(str(value))

            sql = re.sub(r":([A-Za-z_][A-Za-z0-9_]*)", render_parameter, sql)
        table, where, where_in, where_not, where_not_null, where_gte, where_lt = _parse_simple_sql(
            sql
        )
        rows = self._tables.get(table, [])
        result = []
        for r in rows:
            if not all(_compare_value(r.get(k), v) == 0 for k, v in where.items()):
                continue
            if not all(r.get(k) in vs for k, vs in where_in.items()):
                continue
            if not all(r.get(k) != v for k, v in where_not.items()):
                continue
            if not all(r.get(k) is not None for k in where_not_null):
                continue
            # Existing record-store tests treat this client as a predicate
            # stub. TaskStats specifically depends on period ranges, so only
            # enforce those ranges here.
            if where.get("name") == "task_stats":
                if not all(_compare_value(r.get(k), v) >= 0 for k, v in where_gte.items()):
                    continue
                if not all(_compare_value(r.get(k), v) < 0 for k, v in where_lt.items()):
                    continue
            result.append(r)
        order_sql = sql.replace("`", "")
        if re.match(r"SELECT\s+DISTINCT\s+trace_id\s+FROM\b", order_sql.lstrip(), re.IGNORECASE):
            result = [
                {"trace_id": key} for key in dict.fromkeys(row.get("trace_id") for row in result)
            ]
        if "ORDER BY attributes.attempt DESC, end_time_unix_nano DESC" in order_sql:
            result.sort(
                key=lambda row: (
                    int(row.get("attributes.attempt") or 0),
                    str(row.get("end_time_unix_nano") or ""),
                ),
                reverse=True,
            )
        elif "ORDER BY attributes.attempt DESC, period DESC" in order_sql:
            result.sort(
                key=lambda row: (
                    int(row.get("attributes.attempt") or 0),
                    str(row.get("period") or ""),
                ),
                reverse=True,
            )
        elif "ORDER BY period DESC" in order_sql:
            result.sort(key=lambda row: str(row.get("period") or ""), reverse=True)
        elif "ORDER BY period ASC" in order_sql:
            result.sort(key=lambda row: str(row.get("period") or ""))
        elif "ORDER BY trace_id ASC" in order_sql:
            result.sort(
                key=lambda row: (
                    str(row.get("trace_id") or ""),
                    str(row.get("start_time_unix_nano") or ""),
                    str(row.get("span_id") or ""),
                )
            )
        limit_match = re.search(r"\bLIMIT\s+(\d+)", sql, flags=re.IGNORECASE)
        if limit_match:
            result = result[: int(limit_match.group(1))]
        return result

    def seed(self, table: str, records: list[dict]) -> None:
        self._tables[table].extend(records)

    def query_select(
        self,
        statement: Select[Any],
        *,
        log_sql: bool = True,
        preserve_attribute_types: tuple[str, ...] = (),
    ) -> list[dict[str, Any]]:
        compiled = statement.compile(
            dialect=mysql.dialect(paramstyle="named"),
            compile_kwargs={"render_postcompile": True},
        )
        return self.query(
            str(compiled),
            log_sql=log_sql,
            parameters=compiled.params,
            preserve_attribute_types=preserve_attribute_types,
        )


def _parse_simple_sql(
    sql: str,
) -> tuple[
    str,
    dict[str, str],
    dict[str, list[str]],
    dict[str, str],
    set[str],
    dict[str, str],
    dict[str, str],
]:
    """从简单 SQL 中提取测试所需的基础条件，仅供 InMemory 测试用。"""
    sql_upper = sql.upper()
    from_idx = sql_upper.index("FROM") + 5
    rest = sql[from_idx:].strip()
    parts = rest.split()
    table = parts[0].strip("`")
    where: dict[str, str] = {}
    where_in: dict[str, list[str]] = {}
    where_not: dict[str, str] = {}
    where_not_null: set[str] = set()
    where_gte: dict[str, str] = {}
    where_lt: dict[str, str] = {}
    if "WHERE" in sql_upper:
        where_idx = sql_upper.index("WHERE") + 6
        where_clause = sql[where_idx:].strip()
        order = re.search(r"(?<=\s)ORDER\s+BY(?=\s)", where_clause, re.IGNORECASE)
        if order is not None:
            where_clause = where_clause[: order.start()]
        # Flatten conjunction groups without altering quoted literals or IN lists.
        where_clause = _CONJUNCTION_GROUPING.sub(lambda match: match.group(1) or "", where_clause)
        for token in where_clause.split(" AND "):
            token = token.strip()
            if "LIMIT" in token.upper():
                token = token[: token.upper().index("LIMIT")].strip()
            token_upper = token.upper()
            if token_upper.endswith(" IS NOT NULL"):
                key = token[: -len(" IS NOT NULL")].strip().strip("`")
                where_not_null.add(key)
            elif "!=" in token:
                key, value = token.split("!=", 1)
                where_not[key.strip().strip("`")] = value.strip().strip("'\"")
            elif ">=" in token:
                key, value = token.split(">=", 1)
                where_gte[key.strip().strip("`")] = value.strip().strip("'\"")
            elif "<" in token:
                key, value = token.split("<", 1)
                where_lt[key.strip().strip("`")] = value.strip().strip("'\"")
            elif " IN " in token_upper and "(" in token:
                in_idx = token_upper.index(" IN ")
                k = token[:in_idx].strip().strip("`")
                paren = token[token.index("(") + 1 : token.rindex(")")]
                where_in[k] = [v.strip().strip("'\"") for v in paren.split(",")]
            elif "=" in token and ">" not in token and "<" not in token:
                k, v = token.split("=", 1)
                k = k.strip().strip("`")
                v = v.strip().strip("'\"")
                where[k] = v
    return table, where, where_in, where_not, where_not_null, where_gte, where_lt


def _compare_value(left: Any, right: str) -> int:
    left_text = str(left or "")
    try:
        left_value: Any = datetime.fromisoformat(left_text)
        right_value: Any = datetime.fromisoformat(right)
    except ValueError:
        left_value = left_text
        right_value = right
    return (left_value > right_value) - (left_value < right_value)
