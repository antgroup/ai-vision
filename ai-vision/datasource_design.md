# 观测数据源配置

| 来源 | 观测能力 | 配置与认证 |
|---|---|---|
| SigNoz | 指标、事件、轨迹、诊断结果 | endpoint、查询 API Key、Agent 过滤条件；通过管理命令配置 |
| Langfuse | 轨迹、会话、观察详情、分享与导出 | endpoint/host、public_key、secret_key、project_id；Basic 认证 |

`DataSource` 按 Agent 类型配置；空间项目通过 `SpaceDatasourceBinding` 绑定数据源。`enabled` 控制可用性，`is_default` 决定默认轨迹来源。配置解析接受已有 Langfuse 嵌套 `connection` 配置。切换默认源不会关闭另一个来源。

Langfuse 的 `ObservationEndpointConfig` 保存管理面地址，`SpaceObservationProject` 保存空间项目关系，`SpaceObservationCredential` 保存加密凭据。项目开通、绑定和轮换由独立空间流程完成。

SigNoz 的 logs/traces `response_format` 使用 `table`，Langfuse 使用既有格式；公共查询模板只服务仍依赖它的适配器。SigNoz V2 看板使用结构化查询键。

诊断数据集使用 SigNoz，只有 Langfuse 配置时返回明确的诊断限制。完整数据流见 [架构](docs/architecture-overview.md)。
