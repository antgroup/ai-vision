from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any
from enum import Enum
import time

from jinja2.sandbox import SandboxedEnvironment


class ResponseFormat(Enum):
    """返回数据格式"""

    PROMETHEUS = "prometheus"
    TABLE = "table"
    LANGFUSE = "langfuse"


class ValueType(Enum):
    """返回值类型"""

    NUMBER = "number"
    TIMESERIES = "timeseries"
    TABLE = "table"


@dataclass
class QueryContext:
    """查询上下文 — 适配器的统一输入"""

    user_id: int
    username: str

    agent_product: str
    agent_instance: str | None = None

    data_item_key: str = ""

    time_start: str = ""
    time_end: str = ""
    time_range: str = "24h"

    space_id: str | None = None

    limit: int = 100
    offset: int = 0

    extra_params: dict = field(default_factory=dict)


@dataclass
class QueryResult:
    """查询结果 — 适配器的统一输出"""

    success: bool
    error_message: str | None = None

    response_format: ResponseFormat = ResponseFormat.TABLE
    value_type: ValueType = ValueType.NUMBER

    data: Any = None

    query_rendered: str | None = None
    execution_time_ms: int | None = None


class BaseQueryAdapter(ABC):
    """
    查询适配器基类。

    职责：
    1. 接收 QueryContext + DataSource config
    2. 渲染查询（Simple 模式自动构建 / Template 模式 Jinja2 渲染）
    3. 执行查询
    4. 解析返回数据为统一的 QueryResult
    """

    def __init__(self, datasource_config: dict):
        self.datasource_config = datasource_config
        self._jinja_env = SandboxedEnvironment()

    def query(self, context: QueryContext) -> QueryResult:
        """统一查询入口（Template Method 模式）"""
        from apps.core.metrics import adapter_duration, adapter_errors

        query_def = self._get_query_definition(context.data_item_key)
        if not query_def:
            return QueryResult(
                success=False,
                error_message=f"Query definition not found: {context.data_item_key}",
            )

        adapter_type = type(self).__name__
        metric_attrs = {
            "adapter_type": adapter_type,
            "data_item_key": context.data_item_key,
        }

        try:
            rendered_query = self._render_query(query_def, context)
            start_time = time.monotonic()
            raw_result = self.execute(rendered_query, context)
            execution_time_ms = int((time.monotonic() - start_time) * 1000)

            result = self.parse_response(raw_result, query_def, context)
            result.query_rendered = rendered_query
            result.execution_time_ms = execution_time_ms
            adapter_duration.record((time.monotonic() - start_time), metric_attrs)
            return result
        except Exception as exc:
            adapter_errors.add(
                1,
                {**metric_attrs, "error_type": type(exc).__name__},
            )
            return QueryResult(
                success=False,
                error_message=str(exc),
            )

    def _get_query_definition(self, data_item_key: str) -> dict | None:
        """从 config.queries 中获取查询定义"""
        queries = self.datasource_config.get("queries", {})
        return queries.get(data_item_key)

    def _render_query(self, query_def: dict, context: QueryContext) -> str:
        """根据 mode 渲染查询"""
        mode = query_def.get("mode", "simple")

        if mode == "simple":
            return self.render_simple(query_def, context)
        elif mode == "template":
            return self.render_template(query_def, context)
        else:
            raise ValueError(f"Unknown query mode: {mode}")

    def render_template(self, query_def: dict, context: QueryContext) -> str:
        """使用 Jinja2 SandboxedEnvironment 渲染模板查询"""
        template_str = query_def.get("template", "")
        template = self._jinja_env.from_string(template_str)
        template_vars = self._build_template_vars(context)
        return template.render(**template_vars)

    @staticmethod
    def _compute_range(context: QueryContext) -> str:
        """
        Return a valid PromQL duration string for ``{{ __range }}``.

        For preset time ranges (e.g. "15m", "24h") the value is used as-is.
        For custom time ranges (``time_range == "custom"``), the duration is
        calculated from ``time_start`` / ``time_end`` so that the PromQL
        range-vector selector remains valid.
        """
        if context.time_range != "custom":
            return context.time_range

        from datetime import datetime, timezone as dt_tz

        def _parse(ts: str) -> datetime:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=dt_tz.utc)
            return dt

        try:
            delta = _parse(context.time_end) - _parse(context.time_start)
            total_seconds = max(int(delta.total_seconds()), 60)  # at least 1m
        except Exception:
            return "24h"  # safe fallback

        if total_seconds % 86400 == 0:
            return f"{total_seconds // 86400}d"
        if total_seconds % 3600 == 0:
            return f"{total_seconds // 3600}h"
        if total_seconds % 60 == 0:
            return f"{total_seconds // 60}m"
        return f"{total_seconds}s"

    def _build_template_vars(self, context: QueryContext) -> dict:

        def time_filter(column: str) -> str:
            from datetime import datetime, timezone

            def to_milliseconds(ts: str) -> int:
                dt = datetime.fromisoformat(ts)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return int(dt.timestamp() * 1000)

            start_ms = to_milliseconds(context.time_start)
            end_ms = to_milliseconds(context.time_end)
            return f"CAST(`{column}` AS BIGINT) >= {start_ms} AND CAST(`{column}` AS BIGINT) <= {end_ms}"

        variables = {
            "username": context.username,
            "agent_product": context.agent_product,
            "agent_instance": context.agent_instance or "",
            "time_start": context.time_start,
            "time_end": context.time_end,
            "__range": self._compute_range(context),
            "__timeFilter": time_filter,
            "limit": context.limit,
            "offset": context.offset,
        }

        if context.extra_params:
            variables.update(context.extra_params)

        return variables

    def _resolve_variable(self, value: str, template_vars: dict) -> Any:
        """解析 $variable 占位符"""
        if isinstance(value, str) and value.startswith("$"):
            var_name = value[1:]
            return template_vars.get(var_name, value)
        return value

    @abstractmethod
    def render_simple(self, query_def: dict, context: QueryContext) -> str:
        """子类实现：Simple 模式的查询构建"""

    @abstractmethod
    def execute(self, rendered_query: str, context: QueryContext) -> Any:
        """子类实现：执行查询"""

    @abstractmethod
    def parse_response(
        self, raw_result: Any, query_def: dict, context: QueryContext
    ) -> QueryResult:
        """子类实现：解析返回数据为统一的 QueryResult"""
