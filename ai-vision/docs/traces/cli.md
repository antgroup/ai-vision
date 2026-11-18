# CLI 轨迹查询

CLI 使用 [查询 OpenAPI](../openapi/query/README.md)，由空间 SpaceKey 认证。通用查询依据该 Agent 的默认启用数据源路由到 SigNoz 或 Langfuse。

列表、详情、会话、观察、I/O 与 usage 的契约以 [OpenAPI schema](../openapi/query/openapi-external.yaml) 和 Provider 的能力声明为准。不支持的筛选或操作返回明确错误；不要依赖底层表或直接构建 SQL。

SigNoz 网页入口显式使用 `/api/v1/spaces/{spaceId}/signoz/`，Langfuse 使用 `/lf/`。这两个入口供已登录网页使用，不能替代 CLI 的 SpaceKey 边界。
