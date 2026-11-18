# SigNoz 指标查询

`QueryAdapterFactory` 将 `source_system=signoz` 的指标查询交给 `SignozMetricsQueryAdapter`。适配器向 SigNoz v5 发送结构化指标查询，限定时间、空间、用户和 Agent，解析数值或时间序列。任意 SQL 和 PromQL 不会转发。

已有六种受控 PromQL 模板可通过 `migrate_legacy_metrics_query` 转换为结构化配置；不能识别的模板返回配置错误。新配置使用指标名、过滤条件、聚合和归约参数。配置生成入口是 `configure_signoz_datasource`。

V2 事件看板使用 `queryKey/scope/timeRange/params`，没有 SQL 模板。Langfuse 的轨迹查询继续使用 `TracesQueryAdapter` 及其公共模板能力。

实现位于 `apps/core/adapters/signoz_metrics.py`；空间身份由服务端查询上下文提供，客户端不能覆盖。
