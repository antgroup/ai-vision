---
object_key: task-stats
topic: aggregation-model
title: Task Stats 聚合模型
---

# Task Stats 聚合模型

## task-stats-aggregation-model@20260823-0234

**TaskStats 保存 Task 级完整诊断事实树**

```yaml
record_id: task-stats/aggregation-model@20260823-0234
decision_status: superseded
implementation_status: retired
proposed_at: 2026-08-23T02:34+08:00
decided_at: 2026-08-23T02:34+08:00
implemented_at: 2026-08-23T02:34+08:00
supersedes: []
affected_objects:
  - metric-result
  - task-runner
canonical_doc: docs/architecture.md
```

### 背景

稀疏 DiagnosisRecord 无法提供完整分母，统计必须在执行期从所有结果独立累计。

### 决策

- TaskStats 顶层保存 `source` 与按 Diagnoser/metric 组织的原子计数，配置 Selector 时增加 selector 统计。
- Runner 在明细筛选前累计每个 pre-evaluate 和 evaluate 结果。
- 统一统计接口从单次读取的 TaskStats 在内存构造 `global -> category -> diagnoser -> metric` 树。
- 分析不回退 OceanBase Stats，也不从 DiagnosisRecord 重建完整统计。

### 保持不变

OB Stats 用于 Task 终态读取，旧版遥测存储中的 TaskStats 用于诊断分析，两者来自同一内存快照。

### 影响与风险

统计模型是质量分母权威来源；新增维度需要保持原子计数可重算。

### 验证

由 Stats accumulator、Runner、序列化和 diagnosis-stats 聚合测试验证。

### 实施说明

本记录已按当前代码、测试和 `docs/architecture.md` 核对为已实施行为。

## task-stats-aggregation-model@20261009-1800

**以 TaskStats v3 冻结定义和保守质量策略解释历史**

```yaml
record_id: task-stats/aggregation-model@20261009-1800
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-10-09T18:00+08:00
decided_at: 2026-10-09T18:00+08:00
implemented_at: 2026-10-09T18:00+08:00
supersedes:
  - task-stats/aggregation-model@20260823-0234
related_records:
  - task/execution-snapshot@20261009-1800
affected_objects:
  - metric-result
  - task-runner
  - http-api
canonical_doc: docs/architecture.md
```

### 背景

当前绑定、名称和权重会改变历史分数解释，且局部 skip/failed 隐藏后可能形成看似完整的质量分。

### 决策

- legacy 顶层 source/selector/diagnosers 仍按原合同读取，原计数与质量公式保留；不回写或按新策略
  重解释旧分数。API 标记 `legacy_current_definition`，不声称定义已冻结。
- 新 Task 从执行快照产生 `schema_version=3`、`score_policy_version=lightweight-v1`、
  `diagnoser_definitions`、`skip_reasons` 与 `source_complete`。定义只包含 uuid/key/name/category/
  weight/score_role/metrics/version/revision/digest；不含 Spec、Prompt 或凭证。
- v3 历史只读本 Task 的定义，不查询当前 Job 绑定、Diagnoser 名称、版本或权重。未知 schema/policy
  拒绝；canonical 校验显式处理 v3，不能仅追加顶层字段而绕过校验。
- `score_role=quality|signal`，缺省 quality。初版只把 quality 纳入 Diagnoser/category/global 汇总。
  任一所选质量指标 missing/skip/failed、coverage 不完整或原始读取不完整使相关汇总为 null；
  category 的空 quality 汇总及全 signal、无样本的 global 都为 null。部分 quality category 无分时
  global 不忽略它；单项已判定 good/bad/failed/skipped 数量继续返回。
- 完整质量指标的 ratio 仍为 `good/(good+bad)`，Diagnoser 对指标等权，category 对 Diagnoser 按冻结
  weight 加权（全零则等权），global 对有 quality 的 category 等权。lightweight-v1 固定这些分母，
  不代表 Agent 整体能力。source invalid/projection_failed、Selector partial/failed 或未完成读取阻止汇总评分。
- `total=good+bad+failed`、`coverage=total+skipped`、evaluate 计数、明细稀疏性及 terminal fence 不变。
  v3 target_count 使用选中 source.total，owner 无 source 时使用最大已有 coverage，允许展示部分计数
  而不因不一致 coverage 丢弃整点；legacy 的共同 coverage 校验不变。
- Runner 保存 Record detail 的 `trace_snapshot.diagnoser_definition`，采用同一脱敏历史定义，并带
  schema_version/score_policy_version；旧 Record 不回填。

### 保持不变

TaskStats 由 Runner 在明细筛选前从全量结果独立累计，OB 只保存终态 fence 赢家，分析不从 Record
或 OB 反推统计；业务时间及查询去重保持原合同。

### 影响与风险

v3 将不完整质量分置空，不能与忽略 skip/failed 的 legacy 分数视为同一系列；消费者应保留版本标记。

### 验证

定义变更后的历史读回、legacy 原数值、SKIP/FAILED/缺失覆盖/全 signal、多 category 及 store roundtrip。

### 实施说明

v3 canonical、Runner metadata、Store 双合同、历史定义查询和保守评分已实现。
