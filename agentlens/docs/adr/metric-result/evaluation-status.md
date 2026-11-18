---
object_key: metric-result
topic: evaluation-status
title: Metric Result 评估状态
---

# Metric Result 评估状态

## metric-result-evaluation-status@20260823-0234

**评估状态区分已评判与执行失败**

```yaml
record_id: metric-result/evaluation-status@20260823-0234
decision_status: superseded
implementation_status: retired
proposed_at: 2026-08-23T02:34+08:00
decided_at: 2026-08-23T02:34+08:00
implemented_at: 2026-08-23T02:34+08:00
supersedes: []
affected_objects:
  - diagnosis-record
  - task-stats
canonical_doc: docs/api.md
```

### 背景

规则不适用、正常通过和执行失败需要稳定映射到质量分母，不能靠缺失值或自造 SKIPPED 表达。

### 决策

- 当前结果使用 EVALUATED 和 FAILED 两类评估状态。
- 无材料或规则不适用按 EVALUATED 满分 GOOD 处理，不新增 SKIPPED。
- 解析失败、依赖不可用或诊断异常产生 FAILED，`reason` 必填；failure code 如需保留，由具体
  Executor 写入 evidence，通常使用记录级 `evidence.failure_code`。
- FAILED 可保存为明细但不进入质量分母；GOOD/BAD 参与分数计算。

### 保持不变

verdict 是 DiagnosisRecord 的派生读模型，不替代 MetricResult evaluation status。

### 影响与风险

不适用按 GOOD 会计入分母；新增状态会改变统计算法，必须同时修改消费者合同。

### 验证

由内置规则、LLM 异常、verdict 派生和统计分母测试验证。

### 实施说明

本记录已按当前代码、测试和 `docs/api.md` 核对为已实施行为。

## metric-result-evaluation-status@20260823-1933

**以 Metric verdict 统一表达单指标结论**

```yaml
record_id: metric-result/evaluation-status@20260823-1933
decision_status: superseded
implementation_status: retired
proposed_at: 2026-08-23T19:33+08:00
decided_at: 2026-08-23T19:42+08:00
implemented_at: 2026-08-23T20:59+08:00
supersedes:
  - metric-result/evaluation-status@20260823-0234
related_records:
  - metric-result/fact-model@20260823-1933
  - executor/plugin-contract@20260823-1933
  - diagnosis-record/verdict-derivation@20260823-1933
  - task-stats/metric-counters@20260823-1933
affected_objects:
  - executor
  - diagnosis-record
  - task-stats
canonical_doc: docs/api.md
```

### 背景

`status=EVALUATED/FAILED` 与可空 `passed` 共同表达 GOOD/BAD/FAILED，需要跨字段解释并允许不一致组合。
执行失败、单指标无法评判和正常质量结论需要一套直接可统计的单字段语义。

### 决策

- Metric 使用 `verdict=good|bad|failed`，不再使用 evaluation status 与 passed 的组合。
- `good` 表示已完成评判且明确没有问题；`bad` 表示已完成评判且明确存在问题；`failed` 表示该指标未能
  产生有效结论。
- `bad` 和 `failed` 的 reason 去除空白后必须非空；`good` 的 reason 可以是空字符串。
- LLM 和 Custom Rule 的正常用户协议只允许返回 good/bad；failed 只能由可信 Executor 或 Runner 生成。
- Executor 整体失败时，Runner 为 `spec.metrics[]` 中全部声明指标生成 failed；可信 Executor 可以在
  整体执行成功时保留其他有效 Metric，并为单个无法评判的 Metric 生成 failed。
- 无材料或不适用目标优先由 Dataset/Selector 在分母前排除；已进入诊断但无法判断时记 failed，只有明确
  完成判断并确认没有问题时才记 good，不新增 skipped。
- failed 不进入质量分母；good/bad 继续进入现有质量分计算。

### 保持不变

DiagnosisRecord 顶层仍使用 good/bad/failed，且 bad 优先于 failed；TaskStats 仍保存每指标
total/good/bad/failed。

### 影响与风险

原先按 GOOD 计数的无材料或不适用路径必须迁移到 Selector 排除、明确 GOOD 或 FAILED，可能改变诊断
分母。部分失败继续被保留，消费者不能把 `execution.status=succeeded` 误解为所有 Metric 都已评判。

### 验证

覆盖 good/bad/failed 字段约束、整体执行失败、单 Metric 失败、bad 与 failed 并存、Selector 排除和
TaskStats 分母。

### 实施说明

已实施：Metric 直接使用 verdict，用户结果解析和可信失败路径已按决策收口。

## metric-result-evaluation-status@20260902-1729

**诊断器跳过不产生 Metric verdict**

```yaml
record_id: metric-result/evaluation-status@20260902-1729
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-09-02T17:29+08:00
decided_at: 2026-09-02T17:29+08:00
implemented_at: 2026-09-02T17:53+08:00
supersedes:
  - metric-result/evaluation-status@20260823-1933
related_records:
  - executor/pre-evaluation@20260902-1729
  - task-stats/metric-counters@20260902-1729
affected_objects:
  - executor
  - diagnosis-record
  - task-stats
canonical_doc: docs/api.md
```

### 背景

旧版 Trace 查询源的 pre_evaluate 需要表达某个诊断器的输入不适用，同时不能把该目标记为质量 GOOD、执行 FAILED，
也不能通过共享 Dataset Selector 排除其他诊断器。

### 决策

- Metric verdict 仍只有 `good|bad|failed`；不增加 `skipped` verdict。
- `good`、`bad`、`failed` 的字段约束、可信来源和质量分语义保持现状。
- Executor 明确 skip 时不生成 `DiagnosisResult`、Metric 或 DiagnosisRecord；跳过数量只进入 TaskStats 的
  独立 `skipped` 计数。
- 无材料或不适用目标优先由共享 Dataset Selector 排除，仅诊断器局部且可由旧版 Trace 查询源第一阶段确定的
  输入无效条件可使用 pre_evaluate skip。已经进入 evaluate 但无法判断时仍为 failed。

### 保持不变

DiagnosisRecord 顶层 verdict 派生、failed 明细策略、`good / (good + bad)` 质量分母和用户协议不得输出
failed 的约束保持不变。

### 影响与风险

API 消费者不能从 DiagnosisRecord 找到跳过项，也不能把 `skipped` 当作 Metric 结论；需要结合 TaskStats
观察覆盖率。

### 备选方案

增加第四种 Metric verdict 会扩大记录、查询、反馈和全部消费者的状态机，且错误表达“没有执行指标”。

### 验证

覆盖 verdict 枚举不变、skip 不生成 Metric/记录、TaskStats 分离计数及质量分不包含 skipped。

### 实施说明

已实施：Metric verdict 保持三态，skip 不生成 Metric 或 DiagnosisRecord，只通过 TaskStats 暴露。
