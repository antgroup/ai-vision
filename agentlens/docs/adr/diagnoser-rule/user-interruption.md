---
object_key: diagnoser-rule
topic: user-interruption
title: 明确采集的用户中断事件
---

# 明确采集的用户中断事件

## diagnoser-rule-user-interruption@20261009-1515

**明确采集的用户中断事件**

```yaml
record_id: diagnoser-rule/user-interruption@20261009-1515
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-10-09T15:15+08:00
decided_at: 2026-10-09T15:15+08:00
implemented_at: 2026-10-09T15:15+08:00
supersedes: []
related_records:
  - executor/applicability@20261009-1345
affected_objects:
  - projection
  - metric-result
canonical_doc: docs/diagnoser/rule/README.md
```

### 背景

轻量诊断需区分质量裁决和事实线索，避免成功数量、缺失材料或定位信息制造假通过。当前用户已批准本记录的窄范围实施。

### 决策

- 唯一 knownness 来源是 projection.observed_user_interruption。True 为取消事件 BAD 信号，False 为明确未取消 GOOD，None 为未采集 SKIP。
- 不从 legacy outcome.interrupted 默认 False 推断未取消，不由 runtime 名称或任务质量猜测。取消信号不进入质量平均。

### 保持不变

稳定 Executor 和 metric key、原始 Trace/ToolAction 身份、共享外层 SKIP/FAILED 状态及未涉及规则合同保持不变。历史接受记录不改写；旧 Task 的名称、版本和评分角色不重解释。

### 影响与风险

Common/Coding 八项 Rule 定义版本为 `3.0.0`，执行 ABI 为 `1.2.0`。发布需显式同步受管定义，并排空旧 ABI Task 或保留兼容 Worker；观测缺失和不支持的命令子集仍可能使检查 SKIP，不能解释为安全或修复成功。

### 验证

以独立失败反例、实际 unittest 失败后真实修复通过、正常/未知/遮蔽/无定位控制和受影响回归验证。同步持久化另用显式隔离 OceanBase/seekdb fixture；真实平台/API/GUI 验收由主控单独完成，本文不宣称其已完成。

### 实施说明

已按本记录实现生产规则、版本边界和窄验证派生；准确命令、计数与待补数据库节点保留在本次交付报告。
