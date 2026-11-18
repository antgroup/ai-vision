---
object_key: executor
topic: applicability
title: Executor 完整轨迹适用性
---

# Executor 完整轨迹适用性

## executor-applicability@20261009-1345

**Executor 完整轨迹适用性：完整材料与独立跳过**

```yaml
record_id: executor/applicability@20261009-1345
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-10-09T13:45+08:00
decided_at: 2026-10-09T13:45+08:00
implemented_at: 2026-10-09T13:45+08:00
supersedes: []
related_records:
  - projection/runtime-facts@20261009-1345
  - executor/result-parsing@20261009-1345
affected_objects:
  - task-runner
  - task-stats
  - http-api
canonical_doc: docs/diagnoser/executor/pre-evaluation.md
```

### 背景

已接受的轻量诊断设计要求材料不足、不适用与执行故障分别呈现，并保留冻结任务和历史事实。

### 决策

- Executor 默认 `applicability_skip_reason(trace) -> str | None` 为 None；完整共享投影完成后、evaluate 前执行。
- Formal 与 Trial 调用同一 `execute_with_applicability`。仅可信 ApplicabilitySkipped 或合法门禁原因产生诊断器级跳过；其他异常和非法原因均 FAILED。
- 稳定 code 为 not_applicable、missing_required_evidence、privacy_redacted、budget_exhausted；内部原因可带具体详情，统计按稳定 code 聚合。
- Builtin Rule 的既有 exempt 不再生成 GOOD，而发出显式 skip 信号。门禁只读扫描同一 measure；可见异常优先于部分遮蔽，evaluate 的同一显式信号也由 adapter 收口。
- LLM 先校验输入，再检查 required_vars；缺材料跳过并且不创建 Judge。多指标 one-shot 只在共享条件全部满足后执行，不能以缺变量补 GOOD。
- skip 仅影响当前 target/diagnoser，不产生 Metric 或 Record，各声明指标的 total+skipped 保持目标分母。
- 已知 runtime 未支持的 builtin scenario 返回 not_applicable；不改写 runtime 或用其他 family 绕过。
- CUSTOM 原始 spans 可读取合法未知 runtime；依赖语义时已知 projector 的输入/投影错误不能改成材料不足。

### 保持不变

原始 Trace spans 不改写；Metric verdict 仍只有 good/bad/failed，历史记录不重算。

### 影响与风险

新执行语义使用 Executor revision 1.1.0；旧 revision 的排队任务需兼容 Worker 或先排空，当前恢复入口明确拒绝。已知 schema 的安全历史定义仍可只读显示。

### 验证

离线来源正例、缺失与错误反例、Formal/Trial、多诊断器局部及全部跳过、隐私遮蔽与可见异常回归；不以离线协议测试代表真实 Judge 准确性。

### 实施说明

共享门禁、来源模型、SDK 投影和 Trial 外层合同已实现；具体边界与验证见 canonical_doc。
