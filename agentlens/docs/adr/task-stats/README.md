---
object_key: task-stats
title: Task Stats 决策档案
scope: Task terminal-state statistics, metric counts, persistence, and queries
---

# Task Stats 决策档案

Task 终态统计、指标计数、持久化和查询。本文件维护对象范围；具体决策按长期主题存放在同目录文件中。

## 当前决策索引

<!-- adr-index:start -->
| 决策主题 | 目标决策 | 当前已实施决策 |
|---|---|---|
| [aggregation-model](aggregation-model.md) | [task-stats/aggregation-model@20261009-1800](aggregation-model.md#task-stats-aggregation-model20261009-1800) | [task-stats/aggregation-model@20261009-1800](aggregation-model.md#task-stats-aggregation-model20261009-1800) |
| [business-time](business-time.md) | [task-stats/business-time@20261009-2240](business-time.md#task-stats-business-time20261009-2240) | [task-stats/business-time@20261009-2240](business-time.md#task-stats-business-time20261009-2240) |
| [metric-counters](metric-counters.md) | [task-stats/metric-counters@20260902-1729](metric-counters.md#task-stats-metric-counters20260902-1729) | [task-stats/metric-counters@20260902-1729](metric-counters.md#task-stats-metric-counters20260902-1729) |
| [persistence](persistence.md) | [task-stats/persistence@20260823-0234](persistence.md#task-stats-persistence20260823-0234) | [task-stats/persistence@20260823-0234](persistence.md#task-stats-persistence20260823-0234) |
| [query-contract](query-contract.md) | [task-stats/query-contract@20261009-2321](query-contract.md#task-stats-query-contract20261009-2321) | [task-stats/query-contract@20261009-2321](query-contract.md#task-stats-query-contract20261009-2321) |
<!-- adr-index:end -->

## 相关跨对象决策

<!-- adr-related:start -->
`A → B` 表示 A 的决策影响 B；`A ↔ B` 只表示记录相关，不表示影响方向。

| 对象关系 | 决策记录 | 标题 |
|---|---|---|
| Dataset → Task Stats | [dataset/target-selection@20260823-0234](../dataset/target-selection.md#dataset-target-selection20260823-0234) | Dataset Selector 在诊断分母前选择共享目标 |
| Diagnoser → Task Stats | [diagnoser/builtin-definition-source@20261010-0350](../diagnoser/builtin-definition-source.md#diagnoser-builtin-definition-source20261010-0350) | 保留通用定义源并建立独立专项定义 |
| Diagnoser → Task Stats | [diagnoser/builtin-definition-source@20261117-1340](../diagnoser/builtin-definition-source.md#diagnoser-builtin-definition-source20261117-1340) | Provision four Core presets on demand in personal Spaces |
| Diagnoser → Task Stats | [diagnoser/metric-definition@20260823-0234](../diagnoser/metric-definition.md#diagnoser-metric-definition20260823-0234) | Diagnoser metrics 是评判输出的权威定义 |
| Diagnoser → Task Stats | [diagnoser/metric-definition@20260823-1933](../diagnoser/metric-definition.md#diagnoser-metric-definition20260823-1933) | Diagnoser metrics 只声明稳定指标身份 |
| Diagnosis Record → Task Stats | [diagnosis-record/persistence-policy@20260823-0234](../diagnosis-record/persistence-policy.md#diagnosis-record-persistence-policy20260823-0234) | 按 Job 策略稀疏保存 evaluate 明细 |
| Diagnosis Record → Task Stats | [diagnosis-record/verdict-derivation@20260823-0234](../diagnosis-record/verdict-derivation.md#diagnosis-record-verdict-derivation20260823-0234) | 从 MetricResult 列表唯一派生记录 verdict |
| Diagnosis Record → Task Stats | [diagnosis-record/verdict-derivation@20260823-1933](../diagnosis-record/verdict-derivation.md#diagnosis-record-verdict-derivation20260823-1933) | 从 Execution 与 Metric 派生记录结论和摘要 |
| Executor → Task Stats | [executor/applicability@20261009-1345](../executor/applicability.md#executor-applicability20261009-1345) | Executor 完整轨迹适用性：完整材料与独立跳过 |
| Executor → Task Stats | [executor/pre-evaluation@20260823-0234](../executor/pre-evaluation.md#executor-pre-evaluation20260823-0234) | pre-evaluate 只决定轻量终结或继续完整评估 |
| Executor → Task Stats | [executor/pre-evaluation@20260823-1933](../executor/pre-evaluation.md#executor-pre-evaluation20260823-1933) | pre-evaluate 以 DiagnosisResult 返回轻量终态 |
| Executor → Task Stats | [executor/pre-evaluation@20260902-1729](../executor/pre-evaluation.md#executor-pre-evaluation20260902-1729) | pre-evaluate 支持诊断器局部跳过 |
| Executor → Task Stats | [executor/pre-evaluation@20261010-0350](../executor/pre-evaluation.md#executor-pre-evaluation20261010-0350) | 保留局部预评估与完整材料门禁 |
| Metric Result → Task Stats | [metric-result/evaluation-status@20260823-0234](../metric-result/evaluation-status.md#metric-result-evaluation-status20260823-0234) | 评估状态区分已评判与执行失败 |
| Metric Result → Task Stats | [metric-result/evaluation-status@20260823-1933](../metric-result/evaluation-status.md#metric-result-evaluation-status20260823-1933) | 以 Metric verdict 统一表达单指标结论 |
| Metric Result → Task Stats | [metric-result/evaluation-status@20260902-1729](../metric-result/evaluation-status.md#metric-result-evaluation-status20260902-1729) | 诊断器跳过不产生 Metric verdict |
| Metric Result → Task Stats | [metric-result/fact-model@20260823-0234](../metric-result/fact-model.md#metric-result-fact-model20260823-0234) | MetricResult 是单指标评判的唯一事实 |
| Metric Result → Task Stats | [metric-result/fact-model@20260823-1933](../metric-result/fact-model.md#metric-result-fact-model20260823-1933) | 以 Metric 保存最小单指标诊断事实 |
| Security → Task Stats | [security/authorization-boundary@20260823-0234](../security/authorization-boundary.md#security-authorization-boundary20260823-0234) | AgentLens 不承担最终用户与 owner 授权 |
| Task Runner → Task Stats | [task-runner/execution-pipeline@20260823-0234](../task-runner/execution-pipeline.md#task-runner-execution-pipeline20260823-0234) | Runner 按固定流水线共享数据与 Executor |
| Task Runner → Task Stats | [task-runner/execution-pipeline@20260823-1933](../task-runner/execution-pipeline.md#task-runner-execution-pipeline20260823-1933) | Runner 统一收口 DiagnosisResult 与记录生命周期 |
| Task Runner → Task Stats | [task-runner/execution-pipeline@20260902-1729](../task-runner/execution-pipeline.md#task-runner-execution-pipeline20260902-1729) | Runner 独立汇流诊断结果与局部跳过 |
| Task Runner → Task Stats | [task-runner/execution-pipeline@20261009-1800](../task-runner/execution-pipeline.md#task-runner-execution-pipeline20261009-1800) | 从 Task 快照执行并维持结果、跳过与终态汇流 |
| Task Runner → Task Stats | [task-runner/execution-result@20260823-0234](../task-runner/execution-result.md#task-runner-execution-result20260823-0234) | 终态 CAS 成功后才发布权威执行结果 |
| Task → Task Stats | [task/attempt-ownership@20260823-0234](../task/attempt-ownership.md#task-attempt-ownership20260823-0234) | 以 worker、attempt 和状态 fence 决定终态所有权 |
| Task → Task Stats | [task/attempt-ownership@20261009-1945](../task/attempt-ownership.md#task-attempt-ownership20261009-1945) | Task Attempt 所有权：通用 Core 与可信有界执行 |
| Task → Task Stats | [task/execution-snapshot@20261009-1800](../task/execution-snapshot.md#task-execution-snapshot20261009-1800) | 创建 Task 时冻结有效执行定义与语义版本 |
| Task → Task Stats | [task/execution-snapshot@20261009-1945](../task/execution-snapshot.md#task-execution-snapshot20261009-1945) | 创建 Task 时冻结有效执行定义与语义版本：通用 Core 与可信有界执行 |
| Task → Task Stats | [task/execution-snapshot@20261010-0350](../task/execution-snapshot.md#task-execution-snapshot20261010-0350) | ABI1.4 专项冻结；安全历史读取与全 pool 物理发布门禁 |
| Task → Task Stats | [task/execution-snapshot@20261028-1611](../task/execution-snapshot.md#task-execution-snapshot20261028-1611) | Freeze one saved check for an authorized Trace selection |
| Task → Task Stats | [task/judge-request-budget@20261009-1945](../task/judge-request-budget.md#task-judge-request-budget20261009-1945) | Task Judge 请求预算：通用 Core 与可信有界执行 |
| Task → Task Stats | [task/judge-request-budget@20261028-1611](../task/judge-request-budget.md#task-judge-request-budget20261028-1611) | Reuse the durable request budget for one saved diagnoser |
| Trace Retrieval → Task Stats | [trace-retrieval/query-window@20260823-0234](../trace-retrieval/query-window.md#trace-retrieval-query-window20260823-0234) | 在读取前固定数据源查询窗口与业务时间 |
| Dataset ↔ Task Stats | [dataset/target-selection@20261009-2200](../dataset/target-selection.md#dataset-target-selection20261009-2200) | 保留 Selector 合同并增加可信固定 Trace 选择 |
| Diagnoser ↔ Task Stats | [diagnoser/metric-definition@20260823-1933](../diagnoser/metric-definition.md#diagnoser-metric-definition20260823-1933) | Diagnoser metrics 只声明稳定指标身份 |
| Executor ↔ Task Stats | [executor/pre-evaluation@20260902-1729](../executor/pre-evaluation.md#executor-pre-evaluation20260902-1729) | pre-evaluate 支持诊断器局部跳过 |
| Metric Result ↔ Task Stats | [metric-result/evaluation-status@20260823-1933](../metric-result/evaluation-status.md#metric-result-evaluation-status20260823-1933) | 以 Metric verdict 统一表达单指标结论 |
| Metric Result ↔ Task Stats | [metric-result/evaluation-status@20260902-1729](../metric-result/evaluation-status.md#metric-result-evaluation-status20260902-1729) | 诊断器跳过不产生 Metric verdict |
| Task Runner ↔ Task Stats | [task-runner/execution-pipeline@20260823-1933](../task-runner/execution-pipeline.md#task-runner-execution-pipeline20260823-1933) | Runner 统一收口 DiagnosisResult 与记录生命周期 |
| Task Runner ↔ Task Stats | [task-runner/execution-pipeline@20260902-1729](../task-runner/execution-pipeline.md#task-runner-execution-pipeline20260902-1729) | Runner 独立汇流诊断结果与局部跳过 |
| Task ↔ Task Stats | [task/execution-snapshot@20261009-1800](../task/execution-snapshot.md#task-execution-snapshot20261009-1800) | 创建 Task 时冻结有效执行定义与语义版本 |
<!-- adr-related:end -->
