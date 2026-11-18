# 轨迹数据源路由

支持 `signoz` 和 `langfuse`，由 `trace_query/routing.py` 根据空间内实际绑定的 Agent、启用的 DataSource 和默认标记决定。

- 显式选择数据源时必须存在对应配置，不回退到另一数据源。
- 通用接口优先使用唯一默认配置；多源无默认时返回配置错误。产品未指定且路由有歧义时，按 SigNoz、Langfuse 顺序查找唯一可用配置。
- 轨迹页提供 SigNoz / Langfuse 切换，并在空间、Agent 改变后重新选择默认源。
- Langfuse 空间项目可提供查询凭证。显式 Agent 必须先有可用的 Langfuse DataSource；不得利用其他空间的项目绕过授权。
- SigNoz 看板只调用结构化 queryKey API；只有 Langfuse 配置时不发送 SigNoz 查询。
- 诊断只使用 SigNoz，即使轨迹默认源为 Langfuse。

源配置和凭证保持各自语义。Langfuse 的响应格式、公共查询模板与 Basic 认证不变。
