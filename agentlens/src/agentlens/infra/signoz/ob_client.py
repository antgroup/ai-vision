"""Read-side client for the SigNoz OceanBase telemetry store.

执行由 :class:`~agentlens.infra.signoz.dialect.SignozObDialect` 渲染好的 SQL，
并把 ``signoz_traces`` 的物理行展平回 store / Projector 期望的逻辑列形状：

* ``span_name`` → ``name``，``span_kind`` → ``kind``，``timestamp`` → ``period``（本地时间文本）；
* JSON 列 ``attributes`` / ``resource_attributes`` 展开为 ``attributes.<k>`` /
  ``resource.attributes.<k>``，标量统一转成字符串（与 SigNoz STRING 列一致）；
* Other columns retain their values, except database dates become ISO-8601 text.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date
from typing import Any

from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from agentlens.infra.ob.engine import make_engine
from agentlens.infra.signoz.client import SignozQueryError
from agentlens.infra.signoz.dialect import (
    ATTRIBUTES_PREFIX,
    PHYSICAL_TO_LOGICAL,
    RESOURCE_ATTRIBUTES_PREFIX,
    unix_nano_to_period,
)
from agentlens.logging import get_logger

_JSON_COLUMNS: dict[str, str] = {
    "attributes": ATTRIBUTES_PREFIX,
    "resource_attributes": RESOURCE_ATTRIBUTES_PREFIX,
    "scope_attributes": "scope.attributes.",
}
_PERIOD_KEYS = frozenset({"timestamp", "period"})


def _scalar_text(value: Any) -> Any:
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def _load_json_object(raw: Any) -> dict[str, Any]:
    if raw is None or raw == "":
        return {}
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except ValueError:
            return {}
    else:
        parsed = raw
    return parsed if isinstance(parsed, dict) else {}


def _period_text(value: Any) -> Any:
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
        return _period_text(int(value)) if value.isdigit() else value
    try:
        return unix_nano_to_period(value)
    except (TypeError, ValueError, OverflowError, OSError):
        return str(value)


def flatten_signoz_row(
    row: Mapping[str, Any], *, preserve_attribute_types: tuple[str, ...] = ()
) -> dict[str, Any]:
    """Flatten one ``signoz_traces`` row into the SigNoz-shaped logical row."""
    flat: dict[str, Any] = {}
    for key, value in row.items():
        prefix = _JSON_COLUMNS.get(key)
        if prefix is not None:
            for attr_key, attr_value in _load_json_object(value).items():
                flat[f"{prefix}{attr_key}"] = (
                    attr_value
                    if key == "attributes" and attr_key in preserve_attribute_types
                    else _scalar_text(attr_value)
                )
            continue
        if key in _PERIOD_KEYS:
            flat["period"] = _period_text(value)
            continue
        if isinstance(value, bytes):
            value = value.decode("utf-8", errors="replace")
        elif isinstance(value, date):
            value = value.isoformat()
        flat[PHYSICAL_TO_LOGICAL.get(key, key)] = value
    return flat


class SignozObClient:
    """Production reader: SigNoz telemetry stored in OceanBase (MySQL protocol)."""

    def __init__(
        self,
        url: str,
        *,
        timeout_sec: float = 30.0,
        sql_log_enabled: bool = False,
        engine: Engine | None = None,
    ) -> None:
        self._log = get_logger("signoz.ob")
        connect_args = {"read_timeout": int(max(1.0, timeout_sec))} if engine is None else None
        self._engine = engine or make_engine(
            url,
            sql_log_enabled=sql_log_enabled,
            connect_args=connect_args or {},
        )

    @property
    def engine(self) -> Engine:
        return self._engine

    def query(
        self,
        sql: str,
        *,
        log_sql: bool = True,
        parameters: dict[str, Any] | None = None,
        preserve_attribute_types: tuple[str, ...] = (),
    ) -> list[dict[str, Any]]:
        self._log.info("signoz.query", sql=sql if log_sql else "<redacted>")
        try:
            with self._engine.connect() as conn:
                result = (
                    conn.execute(text(sql), parameters)
                    if parameters is not None
                    else conn.exec_driver_sql(sql)
                )
                rows = result.mappings().all()
        except DBAPIError as exc:
            message = str(exc.orig) if exc.orig is not None else str(exc)
            self._log.warning(
                "signoz.query_fail",
                sql=sql if log_sql else "<redacted>",
                msg=message if log_sql else "<redacted>",
            )
            raise SignozQueryError(message) from exc
        except SQLAlchemyError as exc:
            raise SignozQueryError(str(exc)) from exc
        return [
            flatten_signoz_row(row, preserve_attribute_types=preserve_attribute_types)
            for row in rows
        ]

    def close(self) -> None:
        self._engine.dispose()


__all__ = ["SignozObClient", "flatten_signoz_row"]
