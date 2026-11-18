import json
from typing import Any

from .base import BaseQueryAdapter, QueryContext, QueryResult, ResponseFormat, ValueType


class TracesQueryAdapter(BaseQueryAdapter):
    """Langfuse Trace 查询适配器"""

    def render_simple(self, query_def: dict, context: QueryContext) -> str:
        """Langfuse 的 simple 模式：将 SDK 调用参数序列化为 JSON"""
        sdk_params = query_def.get("sdk_params", {})
        param_mapping = sdk_params.get("param_mapping", {})

        template_vars = self._build_template_vars(context)
        resolved_params = {
            key: self._resolve_variable(value, template_vars)
            for key, value in param_mapping.items()
        }

        return json.dumps(
            {"method": sdk_params.get("method"), "params": resolved_params}
        )

    def _get_langfuse_config(self, agent_product: str) -> dict:
        """从 DataSource 获取 Langfuse 凭证"""
        from apps.core.models import DataSource

        ds = DataSource.objects.select_related("agent_type").get(
            agent_type__agent_name=agent_product,
            datasource_type="traces",
            source_system="langfuse",
            enabled=1,
        )
        config = ds.config or {}
        return {
            "host": config["langfuse_host"],
            "public_key": config["langfuse_public_key"],
            "secret_key": config["langfuse_secret_key"],
        }

    def execute(self, rendered_query: str, context: QueryContext) -> Any:
        """调用 Langfuse SDK"""
        from langfuse import Langfuse

        lf_config = self._get_langfuse_config(context.agent_product)

        query_params = json.loads(rendered_query)
        method_name = query_params["method"]
        params = query_params["params"]

        langfuse_client = Langfuse(
            public_key=lf_config["public_key"],
            secret_key=lf_config["secret_key"],
            host=lf_config["host"],
        )
        result = getattr(langfuse_client, method_name)(**params)
        return result

    def parse_response(
        self, raw_result: Any, query_def: dict, context: QueryContext
    ) -> QueryResult:
        """解析 Langfuse SDK 返回"""
        return QueryResult(
            success=True,
            response_format=ResponseFormat.LANGFUSE,
            value_type=ValueType.TABLE,
            data=raw_result,
        )
