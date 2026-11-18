---
object_key: metric-result
title: Metric Result 决策档案
scope: Per-metric diagnostic facts, status, and evidence
---

# Metric Result 决策档案

单指标诊断事实、状态和证据。本文件维护对象范围；具体决策按长期主题存放在同目录文件中。

## 当前决策索引

<!-- adr-index:start -->
| 决策主题 | 目标决策 | 当前已实施决策 |
|---|---|---|
| [evaluation-status](evaluation-status.md) | [metric-result/evaluation-status@20260902-1729](evaluation-status.md#metric-result-evaluation-status20260902-1729) | [metric-result/evaluation-status@20260902-1729](evaluation-status.md#metric-result-evaluation-status20260902-1729) |
| [evidence](evidence.md) | [metric-result/evidence@20260823-1933](evidence.md#metric-result-evidence20260823-1933) | [metric-result/evidence@20260823-1933](evidence.md#metric-result-evidence20260823-1933) |
| [fact-model](fact-model.md) | [metric-result/fact-model@20260823-1933](fact-model.md#metric-result-fact-model20260823-1933) | [metric-result/fact-model@20260823-1933](fact-model.md#metric-result-fact-model20260823-1933) |
<!-- adr-index:end -->

## 相关跨对象决策

<!-- adr-related:start -->
`A → B` 表示 A 的决策影响 B；`A ↔ B` 只表示记录相关，不表示影响方向。

| 对象关系 | 决策记录 | 标题 |
|---|---|---|
| Rule Diagnoser → Metric Result | [diagnoser-rule/high-risk-command@20260827-1603](../diagnoser-rule/high-risk-command.md#diagnoser-rule-high-risk-command20260827-1603) | 只对确认执行的高风险 Shell 动作判定 |
| Rule Diagnoser → Metric Result | [diagnoser-rule/high-risk-command@20261009-1515](../diagnoser-rule/high-risk-command.md#diagnoser-rule-high-risk-command20261009-1515) | 风险操作与授权判断分开 |
| Rule Diagnoser → Metric Result | [diagnoser-rule/rewrite-overlap@20261009-1515](../diagnoser-rule/rewrite-overlap.md#diagnoser-rule-rewrite-overlap20261009-1515) | 反复编辑配对数而非百分比 |
| Rule Diagnoser → Metric Result | [diagnoser-rule/sensitive-leakage@20260827-1603](../diagnoser-rule/sensitive-leakage.md#diagnoser-rule-sensitive-leakage20260827-1603) | 在模型可见文本中按受控预算检测可信敏感信息泄漏 |
| Rule Diagnoser → Metric Result | [diagnoser-rule/sensitive-leakage@20261009-1515](../diagnoser-rule/sensitive-leakage.md#diagnoser-rule-sensitive-leakage20261009-1515) | 按渠道保存遮蔽的凭据暴露线索 |
| Rule Diagnoser → Metric Result | [diagnoser-rule/stuck-loop@20261009-1515](../diagnoser-rule/stuck-loop.md#diagnoser-rule-stuck-loop20261009-1515) | 连续重复调用是过程线索 |
| Rule Diagnoser → Metric Result | [diagnoser-rule/test-execution@20260827-1603](../diagnoser-rule/test-execution.md#diagnoser-rule-test-execution20260827-1603) | 以有效变更后的结构化验证动作判定测试执行率 |
| Rule Diagnoser → Metric Result | [diagnoser-rule/test-execution@20261008-2311](../diagnoser-rule/test-execution.md#diagnoser-rule-test-execution20261008-2311) | 从原始 Shell 命令证明同一动作内最后修改后的验证顺序 |
| Rule Diagnoser → Metric Result | [diagnoser-rule/test-execution@20261009-1515](../diagnoser-rule/test-execution.md#diagnoser-rule-test-execution20261009-1515) | 最后修改后的真实验证结果 |
| Rule Diagnoser → Metric Result | [diagnoser-rule/tool-success@20261009-1515](../diagnoser-rule/tool-success.md#diagnoser-rule-tool-success20261009-1515) | 工具执行结果与全部已知失败 |
| Rule Diagnoser → Metric Result | [diagnoser-rule/total-latency@20261009-1515](../diagnoser-rule/total-latency.md#diagnoser-rule-total-latency20261009-1515) | 实测耗时与显式场景预算 |
| Rule Diagnoser → Metric Result | [diagnoser-rule/user-interruption@20261009-1515](../diagnoser-rule/user-interruption.md#diagnoser-rule-user-interruption20261009-1515) | 明确采集的用户中断事件 |
| Diagnoser → Metric Result | [diagnoser/core-material-policy@20261009-1945](../diagnoser/core-material-policy.md#diagnoser-core-material-policy20261009-1945) | 通用 Core 材料与裁决范围：通用 Core 与可信有界执行 |
| Diagnoser → Metric Result | [diagnoser/metric-definition@20260823-0234](../diagnoser/metric-definition.md#diagnoser-metric-definition20260823-0234) | Diagnoser metrics 是评判输出的权威定义 |
| Diagnoser → Metric Result | [diagnoser/metric-definition@20260823-1933](../diagnoser/metric-definition.md#diagnoser-metric-definition20260823-1933) | Diagnoser metrics 只声明稳定指标身份 |
| Diagnoser → Metric Result | [diagnoser/specialist-material-policy@20261010-0350](../diagnoser/specialist-material-policy.md#diagnoser-specialist-material-policy20261010-0350) | 五项可选专项的有限政策、真实材料与裁决范围 |
| Diagnoser → Metric Result | [diagnoser/specialist-material-policy@20261117-1152](../diagnoser/specialist-material-policy.md#diagnoser-specialist-material-policy20261117-1152) | Specialist configuration, evidence, and seven executable checks |
| Diagnosis Record → Metric Result | [diagnosis-record/verdict-derivation@20260823-0234](../diagnosis-record/verdict-derivation.md#diagnosis-record-verdict-derivation20260823-0234) | 从 MetricResult 列表唯一派生记录 verdict |
| Diagnosis Record → Metric Result | [diagnosis-record/verdict-derivation@20260823-1933](../diagnosis-record/verdict-derivation.md#diagnosis-record-verdict-derivation20260823-1933) | 从 Execution 与 Metric 派生记录结论和摘要 |
| Executor → Metric Result | [executor/result-parsing@20260823-0234](../executor/result-parsing.md#executor-result-parsing20260823-0234) | 统一将执行输出解析为 MetricResult 列表 |
| Executor → Metric Result | [executor/result-parsing@20260823-1933](../executor/result-parsing.md#executor-result-parsing20260823-1933) | 直接解析 Metric verdict 并统一 Trial 合同 |
| Executor → Metric Result | [executor/result-parsing@20260825-1628](../executor/result-parsing.md#executor-result-parsing20260825-1628) | 按 verdict 条件校验并补全可选 Metric 字段 |
| Executor → Metric Result | [executor/result-parsing@20260826-2015](../executor/result-parsing.md#executor-result-parsing20260826-2015) | LLM Judge 的 GOOD 与 BAD 均返回具体 reason |
| Executor → Metric Result | [executor/result-parsing@20261009-1345](../executor/result-parsing.md#executor-result-parsing20261009-1345) | Executor 结果解析：完整材料与独立跳过 |
| Projection → Metric Result | [projection/runtime-facts@20260823-0234](../projection/runtime-facts.md#projection-runtime-facts20260823-0234) | Projection 只表达可由 Trace 证实的运行时事实 |
| Projection → Metric Result | [projection/runtime-facts@20261009-1345](../projection/runtime-facts.md#projection-runtime-facts20261009-1345) | Projection 运行时事实：完整材料与独立跳过 |
| Projection → Metric Result | [projection/runtime-facts@20261010-0350](../projection/runtime-facts.md#projection-runtime-facts20261010-0350) | 完整来源事实与配置政策分离；保留验证 unknown 边界 |
| Security → Metric Result | [security/private-check-policy@20261010-0350](../security/private-check-policy.md#security-private-check-policy20261010-0350) | 私密政策内部执行冻结；公共 DTO 与新 Record 不回显 marker |
| Task Stats → Metric Result | [task-stats/aggregation-model@20260823-0234](../task-stats/aggregation-model.md#task-stats-aggregation-model20260823-0234) | TaskStats 保存 Task 级完整诊断事实树 |
| Task Stats → Metric Result | [task-stats/aggregation-model@20261009-1800](../task-stats/aggregation-model.md#task-stats-aggregation-model20261009-1800) | 以 TaskStats v3 冻结定义和保守质量策略解释历史 |
| Task Stats → Metric Result | [task-stats/metric-counters@20260823-0234](../task-stats/metric-counters.md#task-stats-metric-counters20260823-0234) | 记录指标结果计数和完整评估数量 |
| Task Stats → Metric Result | [task-stats/metric-counters@20260823-1933](../task-stats/metric-counters.md#task-stats-metric-counters20260823-1933) | 直接按 Metric verdict 累计现有指标计数 |
| Task Stats → Metric Result | [task-stats/metric-counters@20260902-1729](../task-stats/metric-counters.md#task-stats-metric-counters20260902-1729) | 以 skipped 分离诊断器局部不适用目标 |
| Diagnoser ↔ Metric Result | [diagnoser/metric-definition@20260823-1933](../diagnoser/metric-definition.md#diagnoser-metric-definition20260823-1933) | Diagnoser metrics 只声明稳定指标身份 |
| Diagnosis Record ↔ Metric Result | [diagnosis-record/query-contract@20260823-1933](../diagnosis-record/query-contract.md#diagnosis-record-query-contract20260823-1933) | 以派生列表属性和最小 Metric body 查询记录 |
| Diagnosis Record ↔ Metric Result | [diagnosis-record/query-contract@20260823-2329](../diagnosis-record/query-contract.md#diagnosis-record-query-contract20260823-2329) | 临时兼容旧 body 的最小 Metric 查询合同 |
| Diagnosis Record ↔ Metric Result | [diagnosis-record/query-contract@20261012-2310](../diagnosis-record/query-contract.md#diagnosis-record-query-contract20261012-2310) | Task 保留期后读取 Record 自身冻结定义 |
| Diagnosis Record ↔ Metric Result | [diagnosis-record/query-contract@20261028-1518](../diagnosis-record/query-contract.md#diagnosis-record-query-contract20261028-1518) | Read an exact Task batch without an implicit source-time window |
| Diagnosis Record ↔ Metric Result | [diagnosis-record/verdict-derivation@20260823-1933](../diagnosis-record/verdict-derivation.md#diagnosis-record-verdict-derivation20260823-1933) | 从 Execution 与 Metric 派生记录结论和摘要 |
| Executor ↔ Metric Result | [executor/plugin-contract@20260823-1933](../executor/plugin-contract.md#executor-plugin-contract20260823-1933) | Executor 只返回诊断事实与执行观测 |
| Executor ↔ Metric Result | [executor/pre-evaluation@20260902-1729](../executor/pre-evaluation.md#executor-pre-evaluation20260902-1729) | pre-evaluate 支持诊断器局部跳过 |
| Executor ↔ Metric Result | [executor/result-parsing@20260823-1933](../executor/result-parsing.md#executor-result-parsing20260823-1933) | 直接解析 Metric verdict 并统一 Trial 合同 |
| Executor ↔ Metric Result | [executor/result-parsing@20260825-1628](../executor/result-parsing.md#executor-result-parsing20260825-1628) | 按 verdict 条件校验并补全可选 Metric 字段 |
| Executor ↔ Metric Result | [executor/result-parsing@20260826-2015](../executor/result-parsing.md#executor-result-parsing20260826-2015) | LLM Judge 的 GOOD 与 BAD 均返回具体 reason |
| Security ↔ Metric Result | [security/private-check-policy@20261010-0350](../security/private-check-policy.md#security-private-check-policy20261010-0350) | 私密政策内部执行冻结；公共 DTO 与新 Record 不回显 marker |
| Task Stats ↔ Metric Result | [task-stats/metric-counters@20260823-1933](../task-stats/metric-counters.md#task-stats-metric-counters20260823-1933) | 直接按 Metric verdict 累计现有指标计数 |
| Task Stats ↔ Metric Result | [task-stats/metric-counters@20260902-1729](../task-stats/metric-counters.md#task-stats-metric-counters20260902-1729) | 以 skipped 分离诊断器局部不适用目标 |
<!-- adr-related:end -->
