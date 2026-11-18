---
object_key: executor
topic: pre-evaluation
title: Executor 预评估
---

# Executor 预评估

## executor-pre-evaluation@20260823-0234

**pre-evaluate 只决定轻量终结或继续完整评估**

```yaml
record_id: executor/pre-evaluation@20260823-0234
decision_status: superseded
implementation_status: retired
proposed_at: 2026-08-23T02:34+08:00
decided_at: 2026-08-23T02:34+08:00
implemented_at: 2026-08-23T02:34+08:00
supersedes: []
affected_objects:
  - dataset
  - trace-retrieval
  - task-runner
  - task-stats
  - diagnosis-record
canonical_doc: docs/diagnoser/executor/pre-evaluation.md
```

### 背景

轻量预评估需要降低完整 Trace 和 LLM 成本，但不能同时承担样本门禁或产生语义不明的中间状态。

### 决策

- Executor 用 exact span name 与字段声明 phase1 输入，默认声明为空并继续完整评估。
- `pre_evaluate` 只返回继续评估，或携带最终 DiagnosisRecord 的 `final`。
- final 的 GOOD、BAD、FAILED 只计入 TaskStats，不持久化 DiagnosisRecord。
- 单个 pre_evaluate 异常回退该 Diagnoser 的完整 evaluate；明确输入歧义可以返回最终 FAILED。

### 保持不变

Dataset Selector 先决定样本集合；pre-evaluate 不能排除统计分母，也不接收数据源 Client。

### 影响与风险

预评估结果参与完整质量统计但不出现在明细，调用方必须以 TaskStats 为完整分母。

### 验证

由 PreEvaluateDecision、字段过滤、异常回退、Runner 统计和明细保存测试验证。

### 实施说明

本记录已按当前代码、测试和 `docs/diagnoser/executor/pre-evaluation.md` 核对为已实施行为。

## executor-pre-evaluation@20260823-1933

**pre-evaluate 以 DiagnosisResult 返回轻量终态**

```yaml
record_id: executor/pre-evaluation@20260823-1933
decision_status: superseded
implementation_status: retired
proposed_at: 2026-08-23T19:33+08:00
decided_at: 2026-08-23T19:42+08:00
implemented_at: 2026-08-23T20:59+08:00
supersedes:
  - executor/pre-evaluation@20260823-0234
related_records:
  - executor/plugin-contract@20260823-1933
  - task-runner/execution-pipeline@20260823-1933
affected_objects:
  - dataset
  - trace-retrieval
  - task-runner
  - task-stats
  - diagnosis-record
canonical_doc: docs/diagnoser/executor/pre-evaluation.md
```

### 背景

Executor 不再构造 DiagnosisRecord 后，pre-evaluate final 也不能继续携带带空生命周期字段的记录；其
轻量终结只需要输出诊断事实并参与统计。

### 决策

- `PreEvaluateDecision.final` 携带 DiagnosisResult，不携带 DiagnosisRecord。
- final 的 metrics 仍必须覆盖 `spec.metrics[]`；其 good/bad/failed 全部进入 TaskStats，但不构造或持久化
  DiagnosisRecord，也不生成 trace snapshot。
- 单个 pre-evaluate 异常继续保守回退该 Diagnoser 的完整 evaluate；明确完成轻量判断时才返回 final。
- pre-evaluate final 与完整 evaluate 复用 Metric、Execution 和 verdict/summary 派生语义，不新增中间
  状态。

### 保持不变

Dataset Selector 先决定样本集合；pre-evaluate 不排除统计分母、不接收数据源 Client，phase1 字段声明
和候选补拉流程保持不变。

### 影响与风险

Runner 的 pre-evaluate 缓存和统计入口需从 DiagnosisRecord 改为 DiagnosisResult；任何仍构造记录的
旁路都会重新引入生命周期职责重复。

### 验证

覆盖 final/continue 约束、异常回退、字段过滤、TaskStats 累计和 pre-evaluate 不保存明细。

### 实施说明

已实施：pre-evaluate final 已携带 DiagnosisResult，并保持只统计不保存。

## executor-pre-evaluation@20260902-1729

**pre-evaluate 支持诊断器局部跳过**

```yaml
record_id: executor/pre-evaluation@20260902-1729
decision_status: superseded
implementation_status: retired
proposed_at: 2026-09-02T17:29+08:00
decided_at: 2026-09-02T17:29+08:00
implemented_at: 2026-09-02T17:53+08:00
supersedes:
  - executor/pre-evaluation@20260823-1933
related_records:
  - metric-result/evaluation-status@20260902-1729
  - task-runner/execution-pipeline@20260902-1729
  - task-stats/metric-counters@20260902-1729
affected_objects:
  - dataset
  - trace-retrieval
  - task-runner
  - task-stats
  - diagnosis-record
canonical_doc: docs/diagnoser/executor/pre-evaluation.md
```

### 背景

部分活动诊断器继承自独立巡检 SQL，其输入有效性过滤只适用于该诊断器。把这类目标判为 GOOD 会污染
质量分母；把过滤条件放入 Dataset Selector 又会同时排除同一 Job 中的其他诊断器，无法表达局部不适用。

### 决策

- `PreEvaluateDecision` 支持 `continue_evaluation()`、`final(result)` 和 `skip(reason)` 三种互斥状态；
  skip 必须携带去除空白后非空的原因，且不携带 `DiagnosisResult`。
- skip 只对当前 target 与当前 Diagnoser 生效；Runner 不为它补拉完整 Trace、不调用该 Executor 的
  `evaluate()`、不构造或保存 DiagnosisRecord，也不生成 Metric。
- 仅旧版 Trace 查询源的两阶段执行会产生 skip。离线数仓的输入跳过仍由数据源负责。
- 单个 pre_evaluate 抛异常时仍保守回退当前 Diagnoser 的完整 evaluate；只有 Executor 明确返回 skip
  才排除该 Diagnoser 的结果分母。
- Dataset Selector 仍先确定所有 Diagnoser 共享的样本集合；skip 不改变 Selector 结果，不向 Executor
  暴露 Selector 配置或元数据。

### 保持不变

phase1 exact span/field 声明、候选补拉并集、`final(result)` 的 GOOD/BAD/FAILED 统计语义，以及完整
evaluate 的结果与记录合同保持不变。

### 影响与风险

同一 Task 的不同 Diagnoser 可以具有不同适用结果数；消费者必须用 `total + skipped` 比较覆盖目标数，
不能再假定每个 metric 的 `total` 相同。skip 原因只用于执行日志，不进入诊断记录或 Metric evidence。

### 备选方案

- 将无效输入判为 GOOD：会提高质量分并把未评判目标加入分母。
- 放入 Dataset Selector：会错误排除同一 Job 中仍可运行的其他诊断器。
- 增加 Metric `skipped` verdict：会把“没有 Metric”与“Metric 已产生但未评判”混在一起。

### 验证

覆盖决策状态互斥、空原因拒绝、单诊断器 skip、多诊断器独立路由、免 hydration/LLM、无记录、异常回退
以及与 Dataset Selector 的组合。

### 实施说明

已实施：`PreEvaluateDecision.skip(reason)`、旧版 Trace 查询源的局部路由、免 hydration/evaluate/记录及独立统计均已
落地，并由多 Diagnoser Runner 测试覆盖。

## executor-pre-evaluation@20261010-0350

**保留局部预评估与完整材料门禁**

```yaml
record_id: executor/pre-evaluation@20261010-0350
decision_status: accepted
implementation_status: not_started
proposed_at: 2026-10-10T03:50+08:00
decided_at: 2026-10-10T09:06+08:00
implemented_at: null
supersedes:
  - executor/pre-evaluation@20260902-1729
related_records:
  - diagnoser/builtin-definition-source@20261010-0350
  - diagnoser/specialist-material-policy@20261010-0350
  - task-runner/execution-pipeline@20261009-1800
  - executor/applicability@20261009-1345
affected_objects:
  - diagnoser
  - task-runner
  - task-stats
  - diagnosis-record
canonical_doc: docs/diagnoser/executor/pre-evaluation.md
```

### 背景

专项材料门禁应基于完整授权 Trace，而不能因 phase1 可见字段缺失提前跳过可补材料。

### 决策

`PreEvaluateDecision` 的 continue、final(result)、skip 三态互斥。skip 仅影响 target × Diagnoser，
为声明指标累计 skipped，不产生 Metric/Record，也不影响其它诊断器。final 的完整 DiagnosisResult 计入统计，
不保存 Record。Selector 先决定共享样本和分母；单个预评估异常仍回退完整 evaluate，数据源阶段合同保持。
退役 Executor 不再注册，不通过缺省 fallback 或通用 Executor 重新启用。
专项完整读取并投影后，按有限政策将缺失、不适用和超界材料记为零 Judge SKIP；查询、来源、结构、内部、
协议及非法 Spec 故障保持 FAILED。

### 保持不变

GOOD/BAD/FAILED、无 Metric 的 SKIP、`total + skipped`、Selector、稀疏保存和完整评估合同保持。

### 影响与风险

退役身份先由 eligibility/ABI 检查拒绝，不能逐目标返回 GOOD 或 SKIP 假装支持；执行故障保留 partial 统计。

### 验证

验证三态互斥、计数、异常回退、完整读取门禁、零 Judge FAILED、多诊断器路由与退役选择拒绝。

### 实施说明

目标合同见当前预评估指南；实施状态由记录元数据维护。
