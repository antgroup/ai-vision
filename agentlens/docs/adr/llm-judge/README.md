---
object_key: llm-judge
title: LLM Judge 决策档案
scope: LLM evaluation requests and reasoning modes
---

# LLM Judge 决策档案

LLM 评判请求和推理模式。本文件维护对象范围；具体决策按长期主题存放在同目录文件中。

## 当前决策索引

<!-- adr-index:start -->
| 决策主题 | 目标决策 | 当前已实施决策 |
|---|---|---|
| [multimodal-message](multimodal-message.md) | [llm-judge/multimodal-message@20260826-1714](multimodal-message.md#llm-judge-multimodal-message20260826-1714) | [llm-judge/multimodal-message@20260826-1714](multimodal-message.md#llm-judge-multimodal-message20260826-1714) |
| [request-attempt-hooks](request-attempt-hooks.md) | [llm-judge/request-attempt-hooks@20261021-0500](request-attempt-hooks.md#llm-judge-request-attempt-hooks20261021-0500) | [llm-judge/request-attempt-hooks@20261021-0500](request-attempt-hooks.md#llm-judge-request-attempt-hooks20261021-0500) |
| [thinking-mode](thinking-mode.md) | [llm-judge/thinking-mode@20261021-0023](thinking-mode.md#llm-judge-thinking-mode20261021-0023) | [llm-judge/thinking-mode@20261021-0023](thinking-mode.md#llm-judge-thinking-mode20261021-0023) |
<!-- adr-index:end -->

## 相关跨对象决策

<!-- adr-related:start -->
`A → B` 表示 A 的决策影响 B；`A ↔ B` 只表示记录相关，不表示影响方向。

| 对象关系 | 决策记录 | 标题 |
|---|---|---|
| Diagnoser → LLM Judge | [diagnoser/llm-request-limits@20261008-2207](../diagnoser/llm-request-limits.md#diagnoser-llm-request-limits20261008-2207) | 按诊断器配置可选推理强度与输出上限 |
| Diagnoser → LLM Judge | [diagnoser/llm-request-limits@20261008-2220](../diagnoser/llm-request-limits.md#diagnoser-llm-request-limits20261008-2220) | 保留字符串预算并支持数值推理强度 |
| Diagnoser → LLM Judge | [diagnoser/llm-request-limits@20261008-2225](../diagnoser/llm-request-limits.md#diagnoser-llm-request-limits20261008-2225) | 按诊断器配置推理预算和 HTTP 等待上限 |
| Diagnoser → LLM Judge | [diagnoser/llm-temperature@20260902-2044](../diagnoser/llm-temperature.md#diagnoser-llm-temperature20260902-2044) | 以顶层 Spec 字段配置单诊断器采样温度 |
| Diagnoser → LLM Judge | [diagnoser/llm-temperature@20261021-0023](../diagnoser/llm-temperature.md#diagnoser-llm-temperature20261021-0023) | Preserve sampling settings with an official Anthropic transport exception |
| Diagnoser → LLM Judge | [diagnoser/model-reference@20260823-0234](../diagnoser/model-reference.md#diagnoser-model-reference20260823-0234) | LLM Diagnoser 通过模型连接 UUID 引用模型 |
| Diagnoser → LLM Judge | [diagnoser/model-reference@20261009-1945](../diagnoser/model-reference.md#diagnoser-model-reference20261009-1945) | Diagnoser 模型引用：通用 Core 与可信有界执行 |
| Diagnoser → LLM Judge | [diagnoser/specialist-material-policy@20261010-0350](../diagnoser/specialist-material-policy.md#diagnoser-specialist-material-policy20261010-0350) | 五项可选专项的有限政策、真实材料与裁决范围 |
| Diagnoser → LLM Judge | [diagnoser/specialist-material-policy@20261117-1152](../diagnoser/specialist-material-policy.md#diagnoser-specialist-material-policy20261117-1152) | Specialist configuration, evidence, and seven executable checks |
| Executor → LLM Judge | [executor/llm-history-window@20261010-0350](../executor/llm-history-window.md#executor-llm-history-window20261010-0350) | 专项只消费同一实际来源的有界历史 |
| Executor → LLM Judge | [executor/llm-input-attachments@20260826-1714](../executor/llm-input-attachments.md#executor-llm-input-attachments20260826-1714) | 以 image_vars 声明一次性 LLM 图片输入 |
| Executor → LLM Judge | [executor/result-parsing@20260825-1628](../executor/result-parsing.md#executor-result-parsing20260825-1628) | 按 verdict 条件校验并补全可选 Metric 字段 |
| Executor → LLM Judge | [executor/result-parsing@20260826-2015](../executor/result-parsing.md#executor-result-parsing20260826-2015) | LLM Judge 的 GOOD 与 BAD 均返回具体 reason |
| Executor → LLM Judge | [executor/result-parsing@20261009-1345](../executor/result-parsing.md#executor-result-parsing20261009-1345) | Executor 结果解析：完整材料与独立跳过 |
| Model Connection → LLM Judge | [model-connection/connectivity-probe@20261008-2225](../model-connection/connectivity-probe.md#model-connection-connectivity-probe20261008-2225) | 显式连接检测使用单次 180 秒 HTTP 等待 |
| Model Connection → LLM Judge | [model-connection/connectivity-probe@20261008-2255](../model-connection/connectivity-probe.md#model-connection-connectivity-probe20261008-2255) | 连通性检测省略 provider 特有的 Thinking 控制 |
| Model Connection → LLM Judge | [model-connection/connectivity-probe@20261021-0023](../model-connection/connectivity-probe.md#model-connection-connectivity-probe20261021-0023) | Use provider-default sampling for the official Anthropic connectivity probe |
| Model Connection → LLM Judge | [model-connection/connectivity-probe@20261021-0051](../model-connection/connectivity-probe.md#model-connection-connectivity-probe20261021-0051) | Return bounded, credential-safe reasons for explicit connection test failures |
| Model Connection → LLM Judge | [model-connection/connectivity-probe@20261023-1225](../model-connection/connectivity-probe.md#model-connection-connectivity-probe20261023-1225) | Persist probe status only for the configuration actually tested |
| Model Connection → LLM Judge | [model-connection/credential-handling@20260823-0234](../model-connection/credential-handling.md#model-connection-credential-handling20260823-0234) | 模型凭证作为只写 API 字段并限制传播 |
| Model Connection → LLM Judge | [model-connection/credential-handling@20261012-2310](../model-connection/credential-handling.md#model-connection-credential-handling20261012-2310) | 冻结模型身份限定运行时凭证绑定 |
| Model Connection → LLM Judge | [model-connection/credential-handling@20261021-0005](../model-connection/credential-handling.md#model-connection-credential-handling20261021-0005) | Bind saved credentials to their connection endpoint during setup |
| Model Connection → LLM Judge | [model-connection/diagnostic-validation@20261021-0500](../model-connection/diagnostic-validation.md#model-connection-diagnostic-validation20261021-0500) | Verify two bounded canonical text cases before choosing a diagnostic model |
| Model Connection → LLM Judge | [model-connection/resource-contract@20260823-0234](../model-connection/resource-contract.md#model-connection-resource-contract20260823-0234) | 模型连接是独立的 Scope 资源 |
| Task → LLM Judge | [task/attempt-ownership@20261009-1945](../task/attempt-ownership.md#task-attempt-ownership20261009-1945) | Task Attempt 所有权：通用 Core 与可信有界执行 |
| Task → LLM Judge | [task/judge-request-budget@20261009-1945](../task/judge-request-budget.md#task-judge-request-budget20261009-1945) | Task Judge 请求预算：通用 Core 与可信有界执行 |
| Task → LLM Judge | [task/judge-request-budget@20261028-1611](../task/judge-request-budget.md#task-judge-request-budget20261028-1611) | Reuse the durable request budget for one saved diagnoser |
| Diagnoser ↔ LLM Judge | [diagnoser/llm-request-limits@20261008-2207](../diagnoser/llm-request-limits.md#diagnoser-llm-request-limits20261008-2207) | 按诊断器配置可选推理强度与输出上限 |
| Diagnoser ↔ LLM Judge | [diagnoser/llm-request-limits@20261008-2220](../diagnoser/llm-request-limits.md#diagnoser-llm-request-limits20261008-2220) | 保留字符串预算并支持数值推理强度 |
| Diagnoser ↔ LLM Judge | [diagnoser/llm-request-limits@20261008-2225](../diagnoser/llm-request-limits.md#diagnoser-llm-request-limits20261008-2225) | 按诊断器配置推理预算和 HTTP 等待上限 |
| Diagnoser ↔ LLM Judge | [diagnoser/llm-temperature@20260902-2044](../diagnoser/llm-temperature.md#diagnoser-llm-temperature20260902-2044) | 以顶层 Spec 字段配置单诊断器采样温度 |
| Diagnoser ↔ LLM Judge | [diagnoser/llm-temperature@20261021-0023](../diagnoser/llm-temperature.md#diagnoser-llm-temperature20261021-0023) | Preserve sampling settings with an official Anthropic transport exception |
| Executor ↔ LLM Judge | [executor/llm-input-attachments@20260826-1714](../executor/llm-input-attachments.md#executor-llm-input-attachments20260826-1714) | 以 image_vars 声明一次性 LLM 图片输入 |
| Model Connection ↔ LLM Judge | [model-connection/connectivity-probe@20261008-2255](../model-connection/connectivity-probe.md#model-connection-connectivity-probe20261008-2255) | 连通性检测省略 provider 特有的 Thinking 控制 |
| Model Connection ↔ LLM Judge | [model-connection/connectivity-probe@20261021-0023](../model-connection/connectivity-probe.md#model-connection-connectivity-probe20261021-0023) | Use provider-default sampling for the official Anthropic connectivity probe |
| Model Connection ↔ LLM Judge | [model-connection/connectivity-probe@20261021-0051](../model-connection/connectivity-probe.md#model-connection-connectivity-probe20261021-0051) | Return bounded, credential-safe reasons for explicit connection test failures |
| Model Connection ↔ LLM Judge | [model-connection/connectivity-probe@20261023-1225](../model-connection/connectivity-probe.md#model-connection-connectivity-probe20261023-1225) | Persist probe status only for the configuration actually tested |
| Model Connection ↔ LLM Judge | [model-connection/diagnostic-validation@20261021-0500](../model-connection/diagnostic-validation.md#model-connection-diagnostic-validation20261021-0500) | Verify two bounded canonical text cases before choosing a diagnostic model |
<!-- adr-related:end -->
