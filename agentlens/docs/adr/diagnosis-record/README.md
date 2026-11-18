---
object_key: diagnosis-record
title: Diagnosis Record 决策档案
scope: Trace-level diagnostic records, persistence policies, and queries
---

# Diagnosis Record 决策档案

Trace 级诊断记录、保存策略与查询。本文件维护对象范围；具体决策按长期主题存放在同目录文件中。

## 当前决策索引

<!-- adr-index:start -->
| 决策主题 | 目标决策 | 当前已实施决策 |
|---|---|---|
| [persistence-policy](persistence-policy.md) | [diagnosis-record/persistence-policy@20260823-0234](persistence-policy.md#diagnosis-record-persistence-policy20260823-0234) | [diagnosis-record/persistence-policy@20260823-0234](persistence-policy.md#diagnosis-record-persistence-policy20260823-0234) |
| [query-contract](query-contract.md) | [diagnosis-record/query-contract@20261028-1518](query-contract.md#diagnosis-record-query-contract20261028-1518) | [diagnosis-record/query-contract@20261028-1518](query-contract.md#diagnosis-record-query-contract20261028-1518) |
| [verdict-derivation](verdict-derivation.md) | [diagnosis-record/verdict-derivation@20260823-1933](verdict-derivation.md#diagnosis-record-verdict-derivation20260823-1933) | [diagnosis-record/verdict-derivation@20260823-1933](verdict-derivation.md#diagnosis-record-verdict-derivation20260823-1933) |
<!-- adr-index:end -->

## 相关跨对象决策

<!-- adr-related:start -->
`A → B` 表示 A 的决策影响 B；`A ↔ B` 只表示记录相关，不表示影响方向。

| 对象关系 | 决策记录 | 标题 |
|---|---|---|
| Dataset → Diagnosis Record | [dataset/target-selection@20260823-0234](../dataset/target-selection.md#dataset-target-selection20260823-0234) | Dataset Selector 在诊断分母前选择共享目标 |
| Diagnoser → Diagnosis Record | [diagnoser/builtin-definition-source@20261010-0350](../diagnoser/builtin-definition-source.md#diagnoser-builtin-definition-source20261010-0350) | 保留通用定义源并建立独立专项定义 |
| Diagnoser → Diagnosis Record | [diagnoser/builtin-definition-source@20261117-1340](../diagnoser/builtin-definition-source.md#diagnoser-builtin-definition-source20261117-1340) | Provision four Core presets on demand in personal Spaces |
| Executor → Diagnosis Record | [executor/plugin-contract@20260823-1933](../executor/plugin-contract.md#executor-plugin-contract20260823-1933) | Executor 只返回诊断事实与执行观测 |
| Executor → Diagnosis Record | [executor/pre-evaluation@20260823-0234](../executor/pre-evaluation.md#executor-pre-evaluation20260823-0234) | pre-evaluate 只决定轻量终结或继续完整评估 |
| Executor → Diagnosis Record | [executor/pre-evaluation@20260823-1933](../executor/pre-evaluation.md#executor-pre-evaluation20260823-1933) | pre-evaluate 以 DiagnosisResult 返回轻量终态 |
| Executor → Diagnosis Record | [executor/pre-evaluation@20260902-1729](../executor/pre-evaluation.md#executor-pre-evaluation20260902-1729) | pre-evaluate 支持诊断器局部跳过 |
| Executor → Diagnosis Record | [executor/pre-evaluation@20261010-0350](../executor/pre-evaluation.md#executor-pre-evaluation20261010-0350) | 保留局部预评估与完整材料门禁 |
| Executor → Diagnosis Record | [executor/result-parsing@20260823-0234](../executor/result-parsing.md#executor-result-parsing20260823-0234) | 统一将执行输出解析为 MetricResult 列表 |
| Executor → Diagnosis Record | [executor/result-parsing@20260823-1933](../executor/result-parsing.md#executor-result-parsing20260823-1933) | 直接解析 Metric verdict 并统一 Trial 合同 |
| Metric Result → Diagnosis Record | [metric-result/evaluation-status@20260823-0234](../metric-result/evaluation-status.md#metric-result-evaluation-status20260823-0234) | 评估状态区分已评判与执行失败 |
| Metric Result → Diagnosis Record | [metric-result/evaluation-status@20260823-1933](../metric-result/evaluation-status.md#metric-result-evaluation-status20260823-1933) | 以 Metric verdict 统一表达单指标结论 |
| Metric Result → Diagnosis Record | [metric-result/evaluation-status@20260902-1729](../metric-result/evaluation-status.md#metric-result-evaluation-status20260902-1729) | 诊断器跳过不产生 Metric verdict |
| Metric Result → Diagnosis Record | [metric-result/evidence@20260823-0234](../metric-result/evidence.md#metric-result-evidence20260823-0234) | 以结构化 evidence 保存可追溯诊断依据 |
| Metric Result → Diagnosis Record | [metric-result/evidence@20260823-1933](../metric-result/evidence.md#metric-result-evidence20260823-1933) | 分离 Metric 判定证据与 Execution 执行观测 |
| Metric Result → Diagnosis Record | [metric-result/fact-model@20260823-0234](../metric-result/fact-model.md#metric-result-fact-model20260823-0234) | MetricResult 是单指标评判的唯一事实 |
| Metric Result → Diagnosis Record | [metric-result/fact-model@20260823-1933](../metric-result/fact-model.md#metric-result-fact-model20260823-1933) | 以 Metric 保存最小单指标诊断事实 |
| Projection → Diagnosis Record | [projection/projector-architecture@20261010-0350](../projection/projector-architecture.md#projection-projector-architecture20261010-0350) | 保留现役通用投影与原始 CUSTOM 路径 |
| Projection → Diagnosis Record | [projection/projector-architecture@20261010-1409](../projection/projector-architecture.md#projection-projector-architecture20261010-1409) | 显式接通 Langfuse SDK runtime 与既有 Framework 事实映射 |
| Sandbox Execution → Diagnosis Record | [sandbox-execution/input-transport@20261104-1500](../sandbox-execution/input-transport.md#sandbox-execution-input-transport20261104-1500) | Preflight complete input and stream across the provider message boundary |
| Security → Diagnosis Record | [security/authorization-boundary@20260823-0234](../security/authorization-boundary.md#security-authorization-boundary20260823-0234) | AgentLens 不承担最终用户与 owner 授权 |
| Security → Diagnosis Record | [security/private-check-policy@20261010-0350](../security/private-check-policy.md#security-private-check-policy20261010-0350) | 私密政策内部执行冻结；公共 DTO 与新 Record 不回显 marker |
| Task Runner → Diagnosis Record | [task-runner/execution-pipeline@20260823-0234](../task-runner/execution-pipeline.md#task-runner-execution-pipeline20260823-0234) | Runner 按固定流水线共享数据与 Executor |
| Task Runner → Diagnosis Record | [task-runner/execution-pipeline@20260823-1933](../task-runner/execution-pipeline.md#task-runner-execution-pipeline20260823-1933) | Runner 统一收口 DiagnosisResult 与记录生命周期 |
| Task Runner → Diagnosis Record | [task-runner/execution-pipeline@20260902-1729](../task-runner/execution-pipeline.md#task-runner-execution-pipeline20260902-1729) | Runner 独立汇流诊断结果与局部跳过 |
| Task Runner → Diagnosis Record | [task-runner/execution-pipeline@20261009-1800](../task-runner/execution-pipeline.md#task-runner-execution-pipeline20261009-1800) | 从 Task 快照执行并维持结果、跳过与终态汇流 |
| Task Runner → Diagnosis Record | [task-runner/execution-result@20260823-0234](../task-runner/execution-result.md#task-runner-execution-result20260823-0234) | 终态 CAS 成功后才发布权威执行结果 |
| Task → Diagnosis Record | [task/attempt-ownership@20260823-0234](../task/attempt-ownership.md#task-attempt-ownership20260823-0234) | 以 worker、attempt 和状态 fence 决定终态所有权 |
| Task → Diagnosis Record | [task/attempt-ownership@20261009-1945](../task/attempt-ownership.md#task-attempt-ownership20261009-1945) | Task Attempt 所有权：通用 Core 与可信有界执行 |
| Task → Diagnosis Record | [task/execution-snapshot@20261010-0350](../task/execution-snapshot.md#task-execution-snapshot20261010-0350) | ABI1.4 专项冻结；安全历史读取与全 pool 物理发布门禁 |
| Task → Diagnosis Record | [task/execution-snapshot@20261028-1611](../task/execution-snapshot.md#task-execution-snapshot20261028-1611) | Freeze one saved check for an authorized Trace selection |
| Trace Retrieval → Diagnosis Record | [trace-retrieval/detail-replay@20260823-0234](../trace-retrieval/detail-replay.md#trace-retrieval-detail-replay20260823-0234) | 通过注入的查询源回放托管数仓轨迹 |
| Executor ↔ Diagnosis Record | [executor/plugin-contract@20260823-1933](../executor/plugin-contract.md#executor-plugin-contract20260823-1933) | Executor 只返回诊断事实与执行观测 |
| Executor ↔ Diagnosis Record | [executor/result-parsing@20260823-1933](../executor/result-parsing.md#executor-result-parsing20260823-1933) | 直接解析 Metric verdict 并统一 Trial 合同 |
| Metric Result ↔ Diagnosis Record | [metric-result/evaluation-status@20260823-1933](../metric-result/evaluation-status.md#metric-result-evaluation-status20260823-1933) | 以 Metric verdict 统一表达单指标结论 |
| Metric Result ↔ Diagnosis Record | [metric-result/evidence@20260823-1933](../metric-result/evidence.md#metric-result-evidence20260823-1933) | 分离 Metric 判定证据与 Execution 执行观测 |
| Metric Result ↔ Diagnosis Record | [metric-result/fact-model@20260823-1933](../metric-result/fact-model.md#metric-result-fact-model20260823-1933) | 以 Metric 保存最小单指标诊断事实 |
| Task Runner ↔ Diagnosis Record | [task-runner/execution-pipeline@20260823-1933](../task-runner/execution-pipeline.md#task-runner-execution-pipeline20260823-1933) | Runner 统一收口 DiagnosisResult 与记录生命周期 |
<!-- adr-related:end -->
