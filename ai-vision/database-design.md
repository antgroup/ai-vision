# AI Vision 数据存储

Django [模型](aivision-py-be/apps/core/models/)和[迁移](aivision-py-be/apps/core/migrations/)是业务数据库结构的唯一维护来源。初始化和升级执行 `manage.py migrate`；现有数据库先备份，不能用旧参考 DDL 覆盖。

## 账号与空间

- `User`：UUID、邮箱、密码摘要和账号状态；不依赖组织人员目录。
- `AuthSession`：会话、刷新凭据轮换和撤销状态。
- `Space` / `SpaceMember`：个人或团队空间、成员关系与角色。
- 空间邀请：通过邮箱邀请，再绑定已认证账号的 UUID。

## Agent 与数据源

- `AgentTypeConfig`：Agent 产品及运行时配置；`Agent`：空间中的接入实例。
- `DataSource` / `SpaceDatasourceBinding`：SigNoz 或 Langfuse 数据源及空间绑定。
- 业务元数据保存在 OceanBase 的业务库；遥测通过查询接口读取，不复制为 Django 用户或空间记录。
- 查询先检查空间权限，再解析当前空间与 Agent 的数据源配置。

## Langfuse

`ObservationEndpointConfig`、`SpaceObservationProject`、`SpaceObservationCredential` 保留项目绑定、开通和凭证管理；远端身份映射将本地账号关联到 Langfuse 用户标识。凭证加密存储，Basic 认证与轮换流程继续使用这些表。

## 诊断

AgentLens 管理数据集、诊断器、任务与调度，诊断数据源为 SigNoz。诊断记录和统计经 OTLP 网关写回遥测存储；质量看板通过空间权限校验后的接口读取。

部署见 [Docker 部署](https://aivision.mintlify.site/zh-CN/deployment/docker)，查询契约见 [OpenAPI 查询文档](docs/openapi/query/README.md)。

## 升级与回滚

升级前先在业务数据库副本验证迁移。停止应用服务和诊断 Worker 后，备份业务库、SigNoz metastore 和部署配置，记录当前镜像版本，并保留遥测数据。

使用同一 namespace 和配置更新镜像；通过 `aiv` 镜像部署时，后端与 AgentLens 会在启动时执行迁移。升级后按 [诊断联调](docs/diagnose/integration-checklist.md) 验证服务、采集、查询和诊断结果。

涉及数据库结构变化的回滚，应先停止服务，恢复相匹配的数据库备份、镜像版本和配置，再重新启动。反向迁移不能恢复已删除的数据。
