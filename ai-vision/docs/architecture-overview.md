# AI Vision 当前架构

平台提供账号、空间、Agent 接入、观测与诊断。现役前端为 `vfe/`，后端为 `aivision-py-be/`。

## 数据源边界

| 能力 | SigNoz | Langfuse |
|---|---|---|
| 轨迹、会话、分享、导出 | 支持 | 保留现有能力 |
| 看板 | 结构化 queryKey API、metrics 查询 | 保留原查询适配器与公共模板 |
| 项目与凭证 | 平台采集授权，网关校验 | 保留创建、绑定、Basic 认证、凭证生成与轮换 |
| 诊断 | AgentLens 读取 SigNoz 的 OceanBase 数据 | 当前不作为诊断数据源 |

`DataSource.source_system` 为 `signoz` 或 `langfuse`。通用轨迹查询按照配置的默认源路由；前端两者均配置时显示名称明确的切换入口。缓存按空间、Agent、数据源隔离。仅配置 Langfuse 时仍可查询轨迹、会话与分享；诊断提示配置 SigNoz。

## 数据流

```text
网页安装授权 → Agent 插件 → OTLP 网关 → OceanBase ← SigNoz 查询服务
                                              ↑             ↑
                                       AgentLens 诊断     Django → 网页
                                              ↓
                                         OTLP 诊断结果

Agent 插件 Langfuse exporter → 已有 Langfuse 服务 ← Django → 网页
```

AgentLens 保留任务、调度、算法、投影、列转换和空间隔离；数据集统一为 `type="signoz"`，查询对象为 `TraceQuerySpec`。详情按原 Job 和 Dataset 回放。

Langfuse 的 `ObservationEndpointConfig`、`SpaceObservationProject`、`SpaceObservationCredential`、`SpaceDatasourceBinding` 和身份绑定继续保留。应用级 `PlatformConfig` 继续管理站点配置。
