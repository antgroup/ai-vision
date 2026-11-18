---
object_key: diagnoser-rule
title: Rule Diagnoser 决策档案
scope: Input facts, evaluation rules, evidence, and execution boundaries of built-in Rule diagnosers
---

# Rule Diagnoser 决策档案

内置 Rule 诊断器的长期业务合同。每个内置 Rule 诊断器作为一个独立主题，主题内完整记录该诊断器的
输入事实、判定规则、证据、预算和失败边界；尚无实际 ADR 的诊断器不预建空主题。

## 当前决策索引

<!-- adr-index:start -->
| 决策主题 | 目标决策 | 当前已实施决策 |
|---|---|---|
| [high-risk-command](high-risk-command.md) | [diagnoser-rule/high-risk-command@20261009-1515](high-risk-command.md#diagnoser-rule-high-risk-command20261009-1515) | [diagnoser-rule/high-risk-command@20261009-1515](high-risk-command.md#diagnoser-rule-high-risk-command20261009-1515) |
| [rewrite-overlap](rewrite-overlap.md) | [diagnoser-rule/rewrite-overlap@20261009-1515](rewrite-overlap.md#diagnoser-rule-rewrite-overlap20261009-1515) | [diagnoser-rule/rewrite-overlap@20261009-1515](rewrite-overlap.md#diagnoser-rule-rewrite-overlap20261009-1515) |
| [sensitive-leakage](sensitive-leakage.md) | [diagnoser-rule/sensitive-leakage@20261009-1515](sensitive-leakage.md#diagnoser-rule-sensitive-leakage20261009-1515) | [diagnoser-rule/sensitive-leakage@20261009-1515](sensitive-leakage.md#diagnoser-rule-sensitive-leakage20261009-1515) |
| [stuck-loop](stuck-loop.md) | [diagnoser-rule/stuck-loop@20261009-1515](stuck-loop.md#diagnoser-rule-stuck-loop20261009-1515) | [diagnoser-rule/stuck-loop@20261009-1515](stuck-loop.md#diagnoser-rule-stuck-loop20261009-1515) |
| [test-execution](test-execution.md) | [diagnoser-rule/test-execution@20261009-1515](test-execution.md#diagnoser-rule-test-execution20261009-1515) | [diagnoser-rule/test-execution@20261009-1515](test-execution.md#diagnoser-rule-test-execution20261009-1515) |
| [tool-success](tool-success.md) | [diagnoser-rule/tool-success@20261009-1515](tool-success.md#diagnoser-rule-tool-success20261009-1515) | [diagnoser-rule/tool-success@20261009-1515](tool-success.md#diagnoser-rule-tool-success20261009-1515) |
| [total-latency](total-latency.md) | [diagnoser-rule/total-latency@20261009-1515](total-latency.md#diagnoser-rule-total-latency20261009-1515) | [diagnoser-rule/total-latency@20261009-1515](total-latency.md#diagnoser-rule-total-latency20261009-1515) |
| [user-interruption](user-interruption.md) | [diagnoser-rule/user-interruption@20261009-1515](user-interruption.md#diagnoser-rule-user-interruption20261009-1515) | [diagnoser-rule/user-interruption@20261009-1515](user-interruption.md#diagnoser-rule-user-interruption20261009-1515) |
<!-- adr-index:end -->

## 相关跨对象决策

<!-- adr-related:start -->
`A → B` 表示 A 的决策影响 B；`A ↔ B` 只表示记录相关，不表示影响方向。

| 对象关系 | 决策记录 | 标题 |
|---|---|---|
| Projection → Rule Diagnoser | [projection/runtime-facts@20261009-1515](../projection/runtime-facts.md#projection-runtime-facts20261009-1515) | 验证派生保留明确未知与复合命令边界 |
| Projection → Rule Diagnoser | [projection/runtime-facts@20261010-0350](../projection/runtime-facts.md#projection-runtime-facts20261010-0350) | 完整来源事实与配置政策分离；保留验证 unknown 边界 |
| Diagnoser ↔ Rule Diagnoser | [diagnoser/builtin-definition-source@20261009-1515](../diagnoser/builtin-definition-source.md#diagnoser-builtin-definition-source20261009-1515) | 八 Rule 的受管身份、版本与有限用户策略 |
| Executor ↔ Rule Diagnoser | [executor/applicability@20261009-1345](../executor/applicability.md#executor-applicability20261009-1345) | Executor 完整轨迹适用性：完整材料与独立跳过 |
| Projection ↔ Rule Diagnoser | [projection/runtime-facts@20261009-1515](../projection/runtime-facts.md#projection-runtime-facts20261009-1515) | 验证派生保留明确未知与复合命令边界 |
| Projection ↔ Rule Diagnoser | [projection/runtime-facts@20261010-0350](../projection/runtime-facts.md#projection-runtime-facts20261010-0350) | 完整来源事实与配置政策分离；保留验证 unknown 边界 |
<!-- adr-related:end -->
