from .base import BaseQueryAdapter
from .signoz_metrics import SignozMetricsQueryAdapter
from .traces import TracesQueryAdapter


class QueryAdapterFactory:
    """根据 source_system 创建对应的适配器实例"""

    ADAPTER_MAP: dict[str, type[BaseQueryAdapter]] = {
        "langfuse": TracesQueryAdapter,
        "signoz": SignozMetricsQueryAdapter,
    }

    @classmethod
    def create(cls, datasource) -> BaseQueryAdapter:
        """
        根据 DataSource model 实例创建对应的查询适配器。

        Args:
            datasource: DataSource model instance

        Returns:
            对应的 QueryAdapter 实例

        Raises:
            ValueError: 不支持的 source_system
        """
        adapter_class = cls.ADAPTER_MAP.get(datasource.source_system)
        if not adapter_class:
            raise ValueError(f"Unsupported source system: {datasource.source_system}")
        if (
            datasource.source_system == "signoz"
            and datasource.datasource_type != "metrics"
        ):
            raise ValueError("The generic SigNoz adapter only supports metrics")

        config = datasource.config or {}
        return adapter_class(datasource_config=config)
