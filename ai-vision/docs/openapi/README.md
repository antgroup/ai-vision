# AI Vision OpenAPI

[查询 OpenAPI](query/README.md) 使用 SpaceKey，在所属空间内查询 Agent 的轨迹、会话、事件与元数据。通用轨迹接口按配置路由到 SigNoz 或 Langfuse；事件查询使用 SigNoz。

网页账号、Agent 市场与安装授权通过会话认证接口提供。插件通过一次性安装授权兑换 Agent 专属凭据，再续期短期 OTLP token。

外部平台同步管理接口已移除。Langfuse 独立项目开通和凭证管理仍由空间管理流程提供，详见 [架构](../architecture-overview.md)。
