---
object_key: model-connection
title: Model Connection 决策档案
scope: Model connection resource contracts and credential handling
---

# Model Connection 决策档案

模型连接资源合同与凭证处理。本文件维护对象范围；具体决策按长期主题存放在同目录文件中。

## 当前决策索引

<!-- adr-index:start -->
| 决策主题 | 目标决策 | 当前已实施决策 |
|---|---|---|
| [connectivity-probe](connectivity-probe.md) | [model-connection/connectivity-probe@20261023-1225](connectivity-probe.md#model-connection-connectivity-probe20261023-1225) | [model-connection/connectivity-probe@20261023-1225](connectivity-probe.md#model-connection-connectivity-probe20261023-1225) |
| [credential-handling](credential-handling.md) | [model-connection/credential-handling@20261021-0005](credential-handling.md#model-connection-credential-handling20261021-0005) | [model-connection/credential-handling@20261021-0005](credential-handling.md#model-connection-credential-handling20261021-0005) |
| [diagnostic-validation](diagnostic-validation.md) | [model-connection/diagnostic-validation@20261021-0500](diagnostic-validation.md#model-connection-diagnostic-validation20261021-0500) | [model-connection/diagnostic-validation@20261021-0500](diagnostic-validation.md#model-connection-diagnostic-validation20261021-0500) |
| [model-discovery](model-discovery.md) | [model-connection/model-discovery@20261021-0005](model-discovery.md#model-connection-model-discovery20261021-0005) | [model-connection/model-discovery@20261021-0005](model-discovery.md#model-connection-model-discovery20261021-0005) |
| [resource-contract](resource-contract.md) | [model-connection/resource-contract@20260823-0234](resource-contract.md#model-connection-resource-contract20260823-0234) | [model-connection/resource-contract@20260823-0234](resource-contract.md#model-connection-resource-contract20260823-0234) |
<!-- adr-index:end -->

## 相关跨对象决策

<!-- adr-related:start -->
`A → B` 表示 A 的决策影响 B；`A ↔ B` 只表示记录相关，不表示影响方向。

| 对象关系 | 决策记录 | 标题 |
|---|---|---|
| Diagnoser → Model Connection | [diagnoser/model-reference@20260823-0234](../diagnoser/model-reference.md#diagnoser-model-reference20260823-0234) | LLM Diagnoser 通过模型连接 UUID 引用模型 |
| Diagnoser → Model Connection | [diagnoser/model-reference@20261009-1945](../diagnoser/model-reference.md#diagnoser-model-reference20261009-1945) | Diagnoser 模型引用：通用 Core 与可信有界执行 |
| Diagnoser → Model Connection | [diagnoser/trial-execution@20261012-2310](../diagnoser/trial-execution.md#diagnoser-trial-execution20261012-2310) | 专项 Trial 复制一致配置后释放锁再执行外部 IO |
| LLM Judge → Model Connection | [llm-judge/request-attempt-hooks@20261021-0500](../llm-judge/request-attempt-hooks.md#llm-judge-request-attempt-hooks20261021-0500) | Expose finite failure and truncation observations for model validation |
| Scope → Model Connection | [scope/reference-consistency@20260823-0234](../scope/reference-consistency.md#scope-reference-consistency20260823-0234) | Job 只绑定完全相同 Scope 的资源 |
| Scope → Model Connection | [scope/resource-identity@20260823-0234](../scope/resource-identity.md#scope-resource-identity20260823-0234) | 资源以精确 Scope 和业务 UUID 标识 |
| Task → Model Connection | [task/execution-snapshot@20261009-1800](../task/execution-snapshot.md#task-execution-snapshot20261009-1800) | 创建 Task 时冻结有效执行定义与语义版本 |
| Task → Model Connection | [task/execution-snapshot@20261009-1945](../task/execution-snapshot.md#task-execution-snapshot20261009-1945) | 创建 Task 时冻结有效执行定义与语义版本：通用 Core 与可信有界执行 |
| Diagnoser ↔ Model Connection | [diagnoser/llm-request-limits@20261008-2225](../diagnoser/llm-request-limits.md#diagnoser-llm-request-limits20261008-2225) | 按诊断器配置推理预算和 HTTP 等待上限 |
| Diagnoser ↔ Model Connection | [diagnoser/llm-temperature@20261021-0023](../diagnoser/llm-temperature.md#diagnoser-llm-temperature20261021-0023) | Preserve sampling settings with an official Anthropic transport exception |
| LLM Judge ↔ Model Connection | [llm-judge/request-attempt-hooks@20261009-1945](../llm-judge/request-attempt-hooks.md#llm-judge-request-attempt-hooks20261009-1945) | LLM Judge 请求尝试 hooks：通用 Core 与可信有界执行 |
| LLM Judge ↔ Model Connection | [llm-judge/request-attempt-hooks@20261021-0500](../llm-judge/request-attempt-hooks.md#llm-judge-request-attempt-hooks20261021-0500) | Expose finite failure and truncation observations for model validation |
| LLM Judge ↔ Model Connection | [llm-judge/thinking-mode@20261008-2255](../llm-judge/thinking-mode.md#llm-judge-thinking-mode20261008-2255) | 保留默认 Thinking 控制并允许显式省略 vendor 参数 |
| LLM Judge ↔ Model Connection | [llm-judge/thinking-mode@20261021-0023](../llm-judge/thinking-mode.md#llm-judge-thinking-mode20261021-0023) | Keep existing thinking settings outside the official Anthropic compatibility endpoint |
<!-- adr-related:end -->
