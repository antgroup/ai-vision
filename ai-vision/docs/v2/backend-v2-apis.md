# V2 看板查询 API

所有空间路由使用网页会话认证，并验证空间成员与 Agent 归属。

- `GET /api/v2/spaces/{spaceId}/agent-products/{agentProduct}/datasource-config`：返回真实启用的数据源、默认源及 `traceSources/eventSources`。
- `GET /api/v2/spaces/{spaceId}/agent-products/{agentProduct}/query-config`：返回查询能力信息，不返回 SQL 模板。
- `POST /api/v2/spaces/{spaceId}/agent-products/{agentProduct}/query`：执行 SigNoz 看板查询。
- `POST /api/v2/spaces/{spaceId}/agent-products/{agentProduct}/query_batch`：批量查询。

查询请求包含 `queryKey`、`scope`、`timeRange`、`params`。后端校验查询键、时间和参数，从认证上下文补齐可信身份，然后调用 SigNoz 的结构化看板 API。前端不构建或提交 SQL。

Langfuse 的通用轨迹、会话、详情、分享和导出继续走原有 Provider / Adapter；轨迹页在两种来源均配置时显示 SigNoz / Langfuse 切换。仅配置 Langfuse 时不请求 SigNoz 看板或诊断。
