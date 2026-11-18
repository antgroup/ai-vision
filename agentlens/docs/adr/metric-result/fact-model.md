---
object_key: metric-result
topic: fact-model
title: Metric Result 事实模型
---

# Metric Result 事实模型

## metric-result-fact-model@20260823-0234

**MetricResult 是单指标评判的唯一事实**

```yaml
record_id: metric-result/fact-model@20260823-0234
decision_status: superseded
implementation_status: retired
proposed_at: 2026-08-23T02:34+08:00
decided_at: 2026-08-23T02:34+08:00
implemented_at: 2026-08-23T02:34+08:00
supersedes: []
affected_objects:
  - diagnosis-record
  - task-stats
canonical_doc: docs/architecture.md
```

### 背景

诊断分数、通过状态和失败原因若在记录、统计和 Executor 间重复推导，会产生不一致。

### 决策

- 每次指标评判产生一个 MetricResult，包含 `name`、`status`、`score`、`raw_value`、`threshold`、
  `passed`、`reason` 和 `evidence`；failure code 不是 MetricResult 的独立字段，具体 Executor 可在记录级
  或指标级 evidence 中保存失败分类。
- DiagnosisRecord 的总体结论和 TaskStats 计数都从 MetricResult 列表派生。
- Projection、Diagnoser Spec 和 TaskStats 不保存平行的单次评判事实。

### 保持不变

Diagnoser metrics 定义指标元数据；MetricResult 只保存某次目标上的实际结果。

### 影响与风险

任何结果字段语义变更会同时影响明细、统计和 API，必须作为本对象合同评审。

### 验证

由领域模型、结果解析、DiagnosisRecord 构造和 TaskStats 累计测试验证。

### 实施说明

本记录已按当前代码、测试和 `docs/architecture.md` 核对为已实施行为。

## metric-result-fact-model@20260823-1933

**以 Metric 保存最小单指标诊断事实**

```yaml
record_id: metric-result/fact-model@20260823-1933
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-08-23T19:33+08:00
decided_at: 2026-08-23T19:42+08:00
implemented_at: 2026-08-23T20:59+08:00
supersedes:
  - metric-result/fact-model@20260823-0234
related_records:
  - metric-result/evaluation-status@20260823-1933
  - metric-result/evidence@20260823-1933
  - diagnoser/metric-definition@20260823-1933
  - executor/result-parsing@20260823-1933
  - diagnosis-record/verdict-derivation@20260823-1933
affected_objects:
  - executor
  - diagnosis-record
  - task-stats
  - http-api
canonical_doc: docs/architecture.md
```

### 背景

当前 MetricResult 同时保存评估状态、归一化分数、原始值、阈值和通过状态。大量二元诊断只能制造
`0/1 score + threshold + passed`，而数值规则与模型分数又复用同一个 threshold 合同，导致结果事实、
判断配置和展示字段耦合。

### 决策

- 运行时和持久化的单指标对象从 `MetricResult` 改名为 `Metric`，只包含 `name`、`verdict`、`reason`
  和 `evidence`。
- DiagnosisRecord、Executor 输出和 Trial 的单次结果集合统一命名为 `metrics`；`results` 不再作为同义
  字段保留。
- 删除 MetricResultStatus 以及单次结果的 `status`、`score`、`raw_value`、`threshold`、`passed`。
- `name` 必须是非空字符串，同一 DiagnosisResult 内唯一，并与 Diagnoser `spec.metrics[]` 的声明一一
  对应；返回顺序不要求与定义顺序相同。
- `reason` 始终是字符串，`evidence` 始终是可 JSON 序列化对象；业务测量值或诊断器专属数据如需保留，
  由诊断器选择写入 reason 或 evidence，不建立通用测量合同。
- Metric 是单次指标结论的唯一事实；DiagnosisRecord verdict、summary 和 TaskStats 继续从 Metric 派生，
  不保存平行的单指标结果字段。

### 保持不变

Diagnoser 仍通过 `spec.metrics[]` 声明稳定指标身份和展示说明；TaskStats 的持久化结构和质量分公式保持
不变。

### 影响与风险

单次分数展示、异常程度排序和结构化阈值审计能力退出；需要这些能力的未来诊断器应通过专属参数和
证据合同单独设计，不能恢复为所有 Metric 的强制字段。本变更直接替换现有 HTTP 和持久化结构，不提供
旧 DiagnosisRecord 读取适配。

### 验证

验证 Metric 字段约束、Diagnoser 声明匹配、DiagnosisRecord 派生、TaskStats 累计以及新版 HTTP/Trial
输出；不增加旧 MetricResult 格式的兼容性测试。

### 实施说明

已实施：领域对象已替换为最小 `Metric`，旧结果字段和兼容层已删除。
