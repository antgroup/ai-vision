---
object_key: diagnoser
title: Diagnoser 决策档案
scope: Diagnoser types, modes, metric definitions, and runtime configuration sources
---

# Diagnoser 决策档案

诊断器类型、模式、指标定义和运行配置来源。本文件维护对象范围；具体决策按长期主题存放在同目录文件中。

## 当前决策索引

<!-- adr-index:start -->
| 决策主题 | 目标决策 | 当前已实施决策 |
|---|---|---|
| [builtin-definition-source](builtin-definition-source.md) | [diagnoser/builtin-definition-source@20261117-1340](builtin-definition-source.md#diagnoser-builtin-definition-source20261117-1340) | [diagnoser/builtin-definition-source@20261117-1340](builtin-definition-source.md#diagnoser-builtin-definition-source20261117-1340) |
| [core-material-policy](core-material-policy.md) | [diagnoser/core-material-policy@20261009-1945](core-material-policy.md#diagnoser-core-material-policy20261009-1945) | [diagnoser/core-material-policy@20261009-1945](core-material-policy.md#diagnoser-core-material-policy20261009-1945) |
| [llm-request-limits](llm-request-limits.md) | [diagnoser/llm-request-limits@20261008-2225](llm-request-limits.md#diagnoser-llm-request-limits20261008-2225) | [diagnoser/llm-request-limits@20261008-2225](llm-request-limits.md#diagnoser-llm-request-limits20261008-2225) |
| [llm-temperature](llm-temperature.md) | [diagnoser/llm-temperature@20261021-0023](llm-temperature.md#diagnoser-llm-temperature20261021-0023) | [diagnoser/llm-temperature@20261021-0023](llm-temperature.md#diagnoser-llm-temperature20261021-0023) |
| [metric-definition](metric-definition.md) | [diagnoser/metric-definition@20260823-1933](metric-definition.md#diagnoser-metric-definition20260823-1933) | [diagnoser/metric-definition@20260823-1933](metric-definition.md#diagnoser-metric-definition20260823-1933) |
| [model-reference](model-reference.md) | [diagnoser/model-reference@20261009-1945](model-reference.md#diagnoser-model-reference20261009-1945) | [diagnoser/model-reference@20261009-1945](model-reference.md#diagnoser-model-reference20261009-1945) |
| [specialist-material-policy](specialist-material-policy.md) | [diagnoser/specialist-material-policy@20261117-1152](specialist-material-policy.md#diagnoser-specialist-material-policy20261117-1152) | [diagnoser/specialist-material-policy@20261117-1152](specialist-material-policy.md#diagnoser-specialist-material-policy20261117-1152) |
| [trial-execution](trial-execution.md) | [diagnoser/trial-execution@20261012-2310](trial-execution.md#diagnoser-trial-execution20261012-2310) | [diagnoser/trial-execution@20261012-2310](trial-execution.md#diagnoser-trial-execution20261012-2310) |
| [type-and-mode](type-and-mode.md) | [diagnoser/type-and-mode@20261117-1056](type-and-mode.md#diagnoser-type-and-mode20261117-1056) | [diagnoser/type-and-mode@20261117-1056](type-and-mode.md#diagnoser-type-and-mode20261117-1056) |
<!-- adr-index:end -->

## 相关跨对象决策

<!-- adr-related:start -->
`A → B` 表示 A 的决策影响 B；`A ↔ B` 只表示记录相关，不表示影响方向。

| 对象关系 | 决策记录 | 标题 |
|---|---|---|
| Dataset → Diagnoser | [dataset/diagnosis-input-purpose@20261010-0350](../dataset/diagnosis-input-purpose.md#dataset-diagnosis-input-purpose20261010-0350) | 中性 AUTH scope exact Trace readonly lift；保留 whole-output 边界 |
| Executor → Diagnoser | [executor/llm-history-window@20261010-0350](../executor/llm-history-window.md#executor-llm-history-window20261010-0350) | 专项只消费同一实际来源的有界历史 |
| Executor → Diagnoser | [executor/llm-input-attachments@20260826-1714](../executor/llm-input-attachments.md#executor-llm-input-attachments20260826-1714) | 以 image_vars 声明一次性 LLM 图片输入 |
| Executor → Diagnoser | [executor/plugin-contract@20260823-0234](../executor/plugin-contract.md#executor-plugin-contract20260823-0234) | Executor 通过注册表实现统一插件合同 |
| Executor → Diagnoser | [executor/plugin-contract@20260823-1933](../executor/plugin-contract.md#executor-plugin-contract20260823-1933) | Executor 只返回诊断事实与执行观测 |
| Executor → Diagnoser | [executor/pre-evaluation@20261010-0350](../executor/pre-evaluation.md#executor-pre-evaluation20261010-0350) | 保留局部预评估与完整材料门禁 |
| Executor → Diagnoser | [executor/sandbox-backend@20261102-1700](../executor/sandbox-backend.md#executor-sandbox-backend20261102-1700) | Custom Rule Sandbox Backend contract |
| Job → Diagnoser | [job/retirement-migration@20261010-0350](../job/retirement-migration.md#job-retirement-migration20261010-0350) | 退役资源迁移遵守授权、排空与回读边界 |
| LLM Judge → Diagnoser | [llm-judge/thinking-mode@20260823-0234](../llm-judge/thinking-mode.md#llm-judge-thinking-mode20260823-0234) | 诊断 LLM 默认显式关闭 Thinking |
| LLM Judge → Diagnoser | [llm-judge/thinking-mode@20261008-2255](../llm-judge/thinking-mode.md#llm-judge-thinking-mode20261008-2255) | 保留默认 Thinking 控制并允许显式省略 vendor 参数 |
| LLM Judge → Diagnoser | [llm-judge/thinking-mode@20261010-2130](../llm-judge/thinking-mode.md#llm-judge-thinking-mode20261010-2130) | 新 canonical Core 显式省略 vendor 控制，保留历史与自定义默认 |
| LLM Judge → Diagnoser | [llm-judge/thinking-mode@20261021-0023](../llm-judge/thinking-mode.md#llm-judge-thinking-mode20261021-0023) | Keep existing thinking settings outside the official Anthropic compatibility endpoint |
| Model Connection → Diagnoser | [model-connection/credential-handling@20260823-0234](../model-connection/credential-handling.md#model-connection-credential-handling20260823-0234) | 模型凭证作为只写 API 字段并限制传播 |
| Model Connection → Diagnoser | [model-connection/credential-handling@20261012-2310](../model-connection/credential-handling.md#model-connection-credential-handling20261012-2310) | 冻结模型身份限定运行时凭证绑定 |
| Model Connection → Diagnoser | [model-connection/credential-handling@20261021-0005](../model-connection/credential-handling.md#model-connection-credential-handling20261021-0005) | Bind saved credentials to their connection endpoint during setup |
| Model Connection → Diagnoser | [model-connection/resource-contract@20260823-0234](../model-connection/resource-contract.md#model-connection-resource-contract20260823-0234) | 模型连接是独立的 Scope 资源 |
| Projection → Diagnoser | [projection/projector-architecture@20261010-0350](../projection/projector-architecture.md#projection-projector-architecture20261010-0350) | 保留现役通用投影与原始 CUSTOM 路径 |
| Projection → Diagnoser | [projection/projector-architecture@20261010-1409](../projection/projector-architecture.md#projection-projector-architecture20261010-1409) | 显式接通 Langfuse SDK runtime 与既有 Framework 事实映射 |
| Projection → Diagnoser | [projection/prompt-variables@20260823-0234](../projection/prompt-variables.md#projection-prompt-variables20260823-0234) | 按变量根统一解析 Prompt 变量 |
| Projection → Diagnoser | [projection/prompt-variables@20261010-0350](../projection/prompt-variables.md#projection-prompt-variables20261010-0350) | 保留 raw 表达式与当前注册的公开变量 |
| Projection → Diagnoser | [projection/runtime-facts@20260823-0234](../projection/runtime-facts.md#projection-runtime-facts20260823-0234) | Projection 只表达可由 Trace 证实的运行时事实 |
| Projection → Diagnoser | [projection/runtime-facts@20261009-1345](../projection/runtime-facts.md#projection-runtime-facts20261009-1345) | Projection 运行时事实：完整材料与独立跳过 |
| Projection → Diagnoser | [projection/runtime-facts@20261010-0350](../projection/runtime-facts.md#projection-runtime-facts20261010-0350) | 完整来源事实与配置政策分离；保留验证 unknown 边界 |
| Resource Sharing → Diagnoser | [resource-sharing/discovery@20260823-0234](../resource-sharing/discovery.md#resource-sharing-discovery20260823-0234) | 共享 Space 只扩展显式列表发现范围 |
| Resource Sharing → Diagnoser | [resource-sharing/usage-policy@20260823-0234](../resource-sharing/usage-policy.md#resource-sharing-usage-policy20260823-0234) | 共享资源必须在同 Scope 合同下使用 |
| Sandbox Connection → Diagnoser | [sandbox-connection/resource-contract@20261102-1700](../sandbox-connection/resource-contract.md#sandbox-connection-resource-contract20261102-1700) | Sandbox Connection contract |
| Scope → Diagnoser | [scope/reference-consistency@20260823-0234](../scope/reference-consistency.md#scope-reference-consistency20260823-0234) | Job 只绑定完全相同 Scope 的资源 |
| Scope → Diagnoser | [scope/resource-identity@20260823-0234](../scope/resource-identity.md#scope-resource-identity20260823-0234) | 资源以精确 Scope 和业务 UUID 标识 |
| Security → Diagnoser | [security/authorization-boundary@20261010-0350](../security/authorization-boundary.md#security-authorization-boundary20261010-0350) | 保持 Django 最终用户授权；专项与 owned CLI 显式授权 |
| Security → Diagnoser | [security/private-check-policy@20261010-0350](../security/private-check-policy.md#security-private-check-policy20261010-0350) | 私密政策内部执行冻结；公共 DTO 与新 Record 不回显 marker |
| Task → Diagnoser | [task/execution-snapshot@20261009-1945](../task/execution-snapshot.md#task-execution-snapshot20261009-1945) | 创建 Task 时冻结有效执行定义与语义版本：通用 Core 与可信有界执行 |
| Dataset ↔ Diagnoser | [dataset/diagnosis-input-purpose@20261010-0350](../dataset/diagnosis-input-purpose.md#dataset-diagnosis-input-purpose20261010-0350) | 中性 AUTH scope exact Trace readonly lift；保留 whole-output 边界 |
| Rule Diagnoser ↔ Diagnoser | [diagnoser-rule/test-execution@20261009-1515](../diagnoser-rule/test-execution.md#diagnoser-rule-test-execution20261009-1515) | 最后修改后的真实验证结果 |
| Executor ↔ Diagnoser | [executor/llm-history-window@20261010-0350](../executor/llm-history-window.md#executor-llm-history-window20261010-0350) | 专项只消费同一实际来源的有界历史 |
| Executor ↔ Diagnoser | [executor/pre-evaluation@20261010-0350](../executor/pre-evaluation.md#executor-pre-evaluation20261010-0350) | 保留局部预评估与完整材料门禁 |
| Executor ↔ Diagnoser | [executor/result-parsing@20260823-1933](../executor/result-parsing.md#executor-result-parsing20260823-1933) | 直接解析 Metric verdict 并统一 Trial 合同 |
| Job ↔ Diagnoser | [job/retirement-migration@20261010-0350](../job/retirement-migration.md#job-retirement-migration20261010-0350) | 退役资源迁移遵守授权、排空与回读边界 |
| Job ↔ Diagnoser | [job/transaction-publication@20261009-2128](../job/transaction-publication.md#job-transaction-publication20261009-2128) | Job 与 owned 资源发布依赖数据库真实事务模式 |
| LLM Judge ↔ Diagnoser | [llm-judge/thinking-mode@20260823-0234](../llm-judge/thinking-mode.md#llm-judge-thinking-mode20260823-0234) | 诊断 LLM 默认显式关闭 Thinking |
| LLM Judge ↔ Diagnoser | [llm-judge/thinking-mode@20261021-0023](../llm-judge/thinking-mode.md#llm-judge-thinking-mode20261021-0023) | Keep existing thinking settings outside the official Anthropic compatibility endpoint |
| Metric Result ↔ Diagnoser | [metric-result/fact-model@20260823-1933](../metric-result/fact-model.md#metric-result-fact-model20260823-1933) | 以 Metric 保存最小单指标诊断事实 |
| Model Connection ↔ Diagnoser | [model-connection/connectivity-probe@20261008-2225](../model-connection/connectivity-probe.md#model-connection-connectivity-probe20261008-2225) | 显式连接检测使用单次 180 秒 HTTP 等待 |
| Model Connection ↔ Diagnoser | [model-connection/connectivity-probe@20261008-2255](../model-connection/connectivity-probe.md#model-connection-connectivity-probe20261008-2255) | 连通性检测省略 provider 特有的 Thinking 控制 |
| Model Connection ↔ Diagnoser | [model-connection/connectivity-probe@20261021-0023](../model-connection/connectivity-probe.md#model-connection-connectivity-probe20261021-0023) | Use provider-default sampling for the official Anthropic connectivity probe |
| Model Connection ↔ Diagnoser | [model-connection/connectivity-probe@20261021-0051](../model-connection/connectivity-probe.md#model-connection-connectivity-probe20261021-0051) | Return bounded, credential-safe reasons for explicit connection test failures |
| Model Connection ↔ Diagnoser | [model-connection/connectivity-probe@20261023-1225](../model-connection/connectivity-probe.md#model-connection-connectivity-probe20261023-1225) | Persist probe status only for the configuration actually tested |
| Projection ↔ Diagnoser | [projection/image-attachments@20261010-0350](../projection/image-attachments.md#projection-image-attachments20261010-0350) | 图片材料必须有明确受支持的来源映射 |
| Projection ↔ Diagnoser | [projection/projector-architecture@20261010-1409](../projection/projector-architecture.md#projection-projector-architecture20261010-1409) | 显式接通 Langfuse SDK runtime 与既有 Framework 事实映射 |
| Projection ↔ Diagnoser | [projection/runtime-facts@20261010-0350](../projection/runtime-facts.md#projection-runtime-facts20261010-0350) | 完整来源事实与配置政策分离；保留验证 unknown 边界 |
| Scope ↔ Diagnoser | [scope/reference-consistency@20260823-0234](../scope/reference-consistency.md#scope-reference-consistency20260823-0234) | Job 只绑定完全相同 Scope 的资源 |
| Scope ↔ Diagnoser | [scope/resource-identity@20260823-0234](../scope/resource-identity.md#scope-resource-identity20260823-0234) | 资源以精确 Scope 和业务 UUID 标识 |
| Security ↔ Diagnoser | [security/private-check-policy@20261010-0350](../security/private-check-policy.md#security-private-check-policy20261010-0350) | 私密政策内部执行冻结；公共 DTO 与新 Record 不回显 marker |
| Task Stats ↔ Diagnoser | [task-stats/metric-counters@20260823-1933](../task-stats/metric-counters.md#task-stats-metric-counters20260823-1933) | 直接按 Metric verdict 累计现有指标计数 |
| Task ↔ Diagnoser | [task/execution-snapshot@20261009-1800](../task/execution-snapshot.md#task-execution-snapshot20261009-1800) | 创建 Task 时冻结有效执行定义与语义版本 |
| Task ↔ Diagnoser | [task/execution-snapshot@20261010-0350](../task/execution-snapshot.md#task-execution-snapshot20261010-0350) | ABI1.4 专项冻结；安全历史读取与全 pool 物理发布门禁 |
| Task ↔ Diagnoser | [task/execution-snapshot@20261028-1611](../task/execution-snapshot.md#task-execution-snapshot20261028-1611) | Freeze one saved check for an authorized Trace selection |
| Task ↔ Diagnoser | [task/judge-request-budget@20261009-1945](../task/judge-request-budget.md#task-judge-request-budget20261009-1945) | Task Judge 请求预算：通用 Core 与可信有界执行 |
| Task ↔ Diagnoser | [task/judge-request-budget@20261028-1611](../task/judge-request-budget.md#task-judge-request-budget20261028-1611) | Reuse the durable request budget for one saved diagnoser |
<!-- adr-related:end -->
