---
object_key: trace-retrieval
title: Trace Retrieval 决策档案
scope: Diagnostic candidate queries, full Trace retrieval, and detail replay
---

# Trace Retrieval 决策档案

诊断候选查询、完整 Trace 补拉与详情回放。本文件维护对象范围；具体决策按长期主题存放在同目录文件中。

## 当前决策索引

<!-- adr-index:start -->
| 决策主题 | 目标决策 | 当前已实施决策 |
|---|---|---|
| [candidate-hydration](candidate-hydration.md) | [trace-retrieval/candidate-hydration@20261028-1728](candidate-hydration.md#trace-retrieval-candidate-hydration20261028-1728) | [trace-retrieval/candidate-hydration@20261028-1728](candidate-hydration.md#trace-retrieval-candidate-hydration20261028-1728) |
| [detail-replay](detail-replay.md) | [trace-retrieval/detail-replay@20260916-1130](detail-replay.md#trace-retrieval-detail-replay20260916-1130) | [trace-retrieval/detail-replay@20260916-1130](detail-replay.md#trace-retrieval-detail-replay20260916-1130) |
| [query-window](query-window.md) | [trace-retrieval/query-window@20260916-1130](query-window.md#trace-retrieval-query-window20260916-1130) | [trace-retrieval/query-window@20260916-1130](query-window.md#trace-retrieval-query-window20260916-1130) |
<!-- adr-index:end -->

## 相关跨对象决策

<!-- adr-related:start -->
`A → B` 表示 A 的决策影响 B；`A ↔ B` 只表示记录相关，不表示影响方向。

| 对象关系 | 决策记录 | 标题 |
|---|---|---|
| Dataset → Trace Retrieval | [dataset/diagnosis-input-purpose@20261009-2240](../dataset/diagnosis-input-purpose.md#dataset-diagnosis-input-purpose20261009-2240) | 固定选择排除内部诊断输出 Trace |
| Dataset → Trace Retrieval | [dataset/diagnosis-input-purpose@20261010-0350](../dataset/diagnosis-input-purpose.md#dataset-diagnosis-input-purpose20261010-0350) | 中性 AUTH scope exact Trace readonly lift；保留 whole-output 边界 |
| Dataset → Trace Retrieval | [dataset/target-selection@20260823-0234](../dataset/target-selection.md#dataset-target-selection20260823-0234) | Dataset Selector 在诊断分母前选择共享目标 |
| Dataset → Trace Retrieval | [dataset/target-selection@20261009-2200](../dataset/target-selection.md#dataset-target-selection20261009-2200) | 保留 Selector 合同并增加可信固定 Trace 选择 |
| Diagnosis Record → Trace Retrieval | [diagnosis-record/query-contract@20260823-0234](../diagnosis-record/query-contract.md#diagnosis-record-query-contract20260823-0234) | 按逻辑记录去重并统一列表与详情职责 |
| Diagnosis Record → Trace Retrieval | [diagnosis-record/query-contract@20260823-1933](../diagnosis-record/query-contract.md#diagnosis-record-query-contract20260823-1933) | 以派生列表属性和最小 Metric body 查询记录 |
| Diagnosis Record → Trace Retrieval | [diagnosis-record/query-contract@20260823-2329](../diagnosis-record/query-contract.md#diagnosis-record-query-contract20260823-2329) | 临时兼容旧 body 的最小 Metric 查询合同 |
| Diagnosis Record → Trace Retrieval | [diagnosis-record/query-contract@20261012-2310](../diagnosis-record/query-contract.md#diagnosis-record-query-contract20261012-2310) | Task 保留期后读取 Record 自身冻结定义 |
| Diagnosis Record → Trace Retrieval | [diagnosis-record/query-contract@20261028-1518](../diagnosis-record/query-contract.md#diagnosis-record-query-contract20261028-1518) | Read an exact Task batch without an implicit source-time window |
| Executor → Trace Retrieval | [executor/pre-evaluation@20260823-0234](../executor/pre-evaluation.md#executor-pre-evaluation20260823-0234) | pre-evaluate 只决定轻量终结或继续完整评估 |
| Executor → Trace Retrieval | [executor/pre-evaluation@20260823-1933](../executor/pre-evaluation.md#executor-pre-evaluation20260823-1933) | pre-evaluate 以 DiagnosisResult 返回轻量终态 |
| Executor → Trace Retrieval | [executor/pre-evaluation@20260902-1729](../executor/pre-evaluation.md#executor-pre-evaluation20260902-1729) | pre-evaluate 支持诊断器局部跳过 |
| Projection → Trace Retrieval | [projection/projector-architecture@20261010-0350](../projection/projector-architecture.md#projection-projector-architecture20261010-0350) | 保留现役通用投影与原始 CUSTOM 路径 |
| Projection → Trace Retrieval | [projection/projector-architecture@20261010-1409](../projection/projector-architecture.md#projection-projector-architecture20261010-1409) | 显式接通 Langfuse SDK runtime 与既有 Framework 事实映射 |
| Security → Trace Retrieval | [security/authorization-boundary@20261010-0350](../security/authorization-boundary.md#security-authorization-boundary20261010-0350) | 保持 Django 最终用户授权；专项与 owned CLI 显式授权 |
| Task Runner → Trace Retrieval | [task-runner/execution-pipeline@20260823-0234](../task-runner/execution-pipeline.md#task-runner-execution-pipeline20260823-0234) | Runner 按固定流水线共享数据与 Executor |
| Task Runner → Trace Retrieval | [task-runner/execution-pipeline@20260823-1933](../task-runner/execution-pipeline.md#task-runner-execution-pipeline20260823-1933) | Runner 统一收口 DiagnosisResult 与记录生命周期 |
| Task Runner → Trace Retrieval | [task-runner/execution-pipeline@20260902-1729](../task-runner/execution-pipeline.md#task-runner-execution-pipeline20260902-1729) | Runner 独立汇流诊断结果与局部跳过 |
| Task Runner → Trace Retrieval | [task-runner/execution-pipeline@20261009-1800](../task-runner/execution-pipeline.md#task-runner-execution-pipeline20261009-1800) | 从 Task 快照执行并维持结果、跳过与终态汇流 |
| Task Stats → Trace Retrieval | [task-stats/business-time@20260823-0234](../task-stats/business-time.md#task-stats-business-time20260823-0234) | 统计时间使用诊断数据窗口而非执行时间 |
<!-- adr-related:end -->
