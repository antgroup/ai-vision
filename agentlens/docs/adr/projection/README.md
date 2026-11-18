---
object_key: projection
title: Projection 决策档案
scope: Semantic Trace projections, runtime facts, and Prompt variables
---

# Projection 决策档案

Trace 语义投影、运行时事实和 Prompt 变量。本文件维护对象范围；具体决策按长期主题存放在同目录文件中。

## 当前决策索引

<!-- adr-index:start -->
| 决策主题 | 目标决策 | 当前已实施决策 |
|---|---|---|
| [image-attachments](image-attachments.md) | [projection/image-attachments@20261010-0350](image-attachments.md#projection-image-attachments20261010-0350) | - |
| [projector-architecture](projector-architecture.md) | [projection/projector-architecture@20261010-1409](projector-architecture.md#projection-projector-architecture20261010-1409) | [projection/projector-architecture@20261009-1345](projector-architecture.md#projection-projector-architecture20261009-1345) |
| [prompt-variables](prompt-variables.md) | [projection/prompt-variables@20261010-0350](prompt-variables.md#projection-prompt-variables20261010-0350) | [projection/prompt-variables@20260823-0234](prompt-variables.md#projection-prompt-variables20260823-0234) |
| [runtime-facts](runtime-facts.md) | [projection/runtime-facts@20261010-0350](runtime-facts.md#projection-runtime-facts20261010-0350) | [projection/runtime-facts@20261009-1515](runtime-facts.md#projection-runtime-facts20261009-1515) |
<!-- adr-index:end -->

## 相关跨对象决策

<!-- adr-related:start -->
`A → B` 表示 A 的决策影响 B；`A ↔ B` 只表示记录相关，不表示影响方向。

| 对象关系 | 决策记录 | 标题 |
|---|---|---|
| Rule Diagnoser → Projection | [diagnoser-rule/high-risk-command@20260827-1603](../diagnoser-rule/high-risk-command.md#diagnoser-rule-high-risk-command20260827-1603) | 只对确认执行的高风险 Shell 动作判定 |
| Rule Diagnoser → Projection | [diagnoser-rule/high-risk-command@20261009-1515](../diagnoser-rule/high-risk-command.md#diagnoser-rule-high-risk-command20261009-1515) | 风险操作与授权判断分开 |
| Rule Diagnoser → Projection | [diagnoser-rule/rewrite-overlap@20261009-1515](../diagnoser-rule/rewrite-overlap.md#diagnoser-rule-rewrite-overlap20261009-1515) | 反复编辑配对数而非百分比 |
| Rule Diagnoser → Projection | [diagnoser-rule/sensitive-leakage@20260827-1603](../diagnoser-rule/sensitive-leakage.md#diagnoser-rule-sensitive-leakage20260827-1603) | 在模型可见文本中按受控预算检测可信敏感信息泄漏 |
| Rule Diagnoser → Projection | [diagnoser-rule/sensitive-leakage@20261009-1515](../diagnoser-rule/sensitive-leakage.md#diagnoser-rule-sensitive-leakage20261009-1515) | 按渠道保存遮蔽的凭据暴露线索 |
| Rule Diagnoser → Projection | [diagnoser-rule/stuck-loop@20261009-1515](../diagnoser-rule/stuck-loop.md#diagnoser-rule-stuck-loop20261009-1515) | 连续重复调用是过程线索 |
| Rule Diagnoser → Projection | [diagnoser-rule/test-execution@20260827-1603](../diagnoser-rule/test-execution.md#diagnoser-rule-test-execution20260827-1603) | 以有效变更后的结构化验证动作判定测试执行率 |
| Rule Diagnoser → Projection | [diagnoser-rule/test-execution@20261008-2311](../diagnoser-rule/test-execution.md#diagnoser-rule-test-execution20261008-2311) | 从原始 Shell 命令证明同一动作内最后修改后的验证顺序 |
| Rule Diagnoser → Projection | [diagnoser-rule/test-execution@20261009-1515](../diagnoser-rule/test-execution.md#diagnoser-rule-test-execution20261009-1515) | 最后修改后的真实验证结果 |
| Rule Diagnoser → Projection | [diagnoser-rule/tool-success@20261009-1515](../diagnoser-rule/tool-success.md#diagnoser-rule-tool-success20261009-1515) | 工具执行结果与全部已知失败 |
| Rule Diagnoser → Projection | [diagnoser-rule/total-latency@20261009-1515](../diagnoser-rule/total-latency.md#diagnoser-rule-total-latency20261009-1515) | 实测耗时与显式场景预算 |
| Rule Diagnoser → Projection | [diagnoser-rule/user-interruption@20261009-1515](../diagnoser-rule/user-interruption.md#diagnoser-rule-user-interruption20261009-1515) | 明确采集的用户中断事件 |
| Diagnoser → Projection | [diagnoser/builtin-definition-source@20261010-0350](../diagnoser/builtin-definition-source.md#diagnoser-builtin-definition-source20261010-0350) | 保留通用定义源并建立独立专项定义 |
| Diagnoser → Projection | [diagnoser/builtin-definition-source@20261117-1340](../diagnoser/builtin-definition-source.md#diagnoser-builtin-definition-source20261117-1340) | Provision four Core presets on demand in personal Spaces |
| Diagnoser → Projection | [diagnoser/core-material-policy@20261009-1945](../diagnoser/core-material-policy.md#diagnoser-core-material-policy20261009-1945) | 通用 Core 材料与裁决范围：通用 Core 与可信有界执行 |
| Diagnoser → Projection | [diagnoser/specialist-material-policy@20261010-0350](../diagnoser/specialist-material-policy.md#diagnoser-specialist-material-policy20261010-0350) | 五项可选专项的有限政策、真实材料与裁决范围 |
| Diagnoser → Projection | [diagnoser/specialist-material-policy@20261117-1152](../diagnoser/specialist-material-policy.md#diagnoser-specialist-material-policy20261117-1152) | Specialist configuration, evidence, and seven executable checks |
| Executor → Projection | [executor/llm-history-window@20261010-0350](../executor/llm-history-window.md#executor-llm-history-window20261010-0350) | 专项只消费同一实际来源的有界历史 |
| Task Runner → Projection | [task-runner/execution-pipeline@20260823-0234](../task-runner/execution-pipeline.md#task-runner-execution-pipeline20260823-0234) | Runner 按固定流水线共享数据与 Executor |
| Task Runner → Projection | [task-runner/execution-pipeline@20260823-1933](../task-runner/execution-pipeline.md#task-runner-execution-pipeline20260823-1933) | Runner 统一收口 DiagnosisResult 与记录生命周期 |
| Task Runner → Projection | [task-runner/execution-pipeline@20260902-1729](../task-runner/execution-pipeline.md#task-runner-execution-pipeline20260902-1729) | Runner 独立汇流诊断结果与局部跳过 |
| Task Runner → Projection | [task-runner/execution-pipeline@20261009-1800](../task-runner/execution-pipeline.md#task-runner-execution-pipeline20261009-1800) | 从 Task 快照执行并维持结果、跳过与终态汇流 |
| Trace → Projection | [trace/raw-expression-resolution@20260823-0234](../trace/raw-expression-resolution.md#trace-raw-expression-resolution20260823-0234) | 按 source span 真实结构解析原始表达式 |
| Trace → Projection | [trace/runtime-input@20260823-0234](../trace/runtime-input.md#trace-runtime-input20260823-0234) | 统一 Trace 运行时输入边界 |
| Rule Diagnoser ↔ Projection | [diagnoser-rule/test-execution@20261009-1515](../diagnoser-rule/test-execution.md#diagnoser-rule-test-execution20261009-1515) | 最后修改后的真实验证结果 |
| Diagnoser ↔ Projection | [diagnoser/specialist-material-policy@20261010-0350](../diagnoser/specialist-material-policy.md#diagnoser-specialist-material-policy20261010-0350) | 五项可选专项的有限政策、真实材料与裁决范围 |
| Diagnoser ↔ Projection | [diagnoser/specialist-material-policy@20261117-1152](../diagnoser/specialist-material-policy.md#diagnoser-specialist-material-policy20261117-1152) | Specialist configuration, evidence, and seven executable checks |
| Executor ↔ Projection | [executor/applicability@20261009-1345](../executor/applicability.md#executor-applicability20261009-1345) | Executor 完整轨迹适用性：完整材料与独立跳过 |
| Executor ↔ Projection | [executor/llm-history-window@20261010-0350](../executor/llm-history-window.md#executor-llm-history-window20261010-0350) | 专项只消费同一实际来源的有界历史 |
| Executor ↔ Projection | [executor/llm-input-attachments@20260826-1714](../executor/llm-input-attachments.md#executor-llm-input-attachments20260826-1714) | 以 image_vars 声明一次性 LLM 图片输入 |
| Task ↔ Projection | [task/execution-snapshot@20261010-0350](../task/execution-snapshot.md#task-execution-snapshot20261010-0350) | ABI1.4 专项冻结；安全历史读取与全 pool 物理发布门禁 |
<!-- adr-related:end -->
