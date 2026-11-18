---
object_key: task-stats
topic: metric-counters
title: Task Stats 指标计数
---

# Task Stats 指标计数

## task-stats-metric-counters@20260823-0234

**记录指标结果计数和完整评估数量**

```yaml
record_id: task-stats/metric-counters@20260823-0234
decision_status: superseded
implementation_status: retired
proposed_at: 2026-08-23T02:34+08:00
decided_at: 2026-08-23T02:34+08:00
implemented_at: 2026-08-23T02:34+08:00
supersedes: []
affected_objects:
  - executor
  - task-runner
  - metric-result
canonical_doc: docs/api.md
```

### 背景

两阶段执行需要区分请求进入完整评估的数量与最终 GOOD/BAD/FAILED 结果。

### 决策

- 每个 metric 保存 total、good、bad、failed 等原子结果计数。
- 可选 `evaluate` 记录进入完整诊断的目标数，不参与质量算分；当前没有独立的 `pre_evaluate`
  计数。
- pre-evaluate 直接终结和 evaluate 产生的最终结果，无论是否保存明细，都进入 total 及对应
  GOOD/BAD/FAILED 计数。
- Selector 无效和未解析目标不进入诊断 metric 分母。

### 保持不变

计数不通过明细数量或 saved_records 推导。

### 影响与风险

evaluate 计数用于完整评估成本与漏斗分析；消费者不得把它当作结果总数或 pre-evaluate 处理量。

### 验证

由 pre-evaluate 路由、Runner 的 `record_metric` 累计和 diagnosis-stats API 测试验证。

### 实施说明

本记录已按当前代码、测试和 `docs/api.md` 核对为已实施行为。

## task-stats-metric-counters@20260823-1933

**直接按 Metric verdict 累计现有指标计数**

```yaml
record_id: task-stats/metric-counters@20260823-1933
decision_status: superseded
implementation_status: retired
proposed_at: 2026-08-23T19:33+08:00
decided_at: 2026-08-23T19:42+08:00
implemented_at: 2026-08-23T20:59+08:00
supersedes:
  - task-stats/metric-counters@20260823-0234
related_records:
  - diagnoser/metric-definition@20260823-1933
  - metric-result/evaluation-status@20260823-1933
  - task-runner/execution-pipeline@20260823-1933
affected_objects:
  - executor
  - task-runner
  - metric-result
canonical_doc: docs/api.md
```

### 背景

TaskStats 当前根据 MetricResult status 与 passed 的组合选择 good/bad/failed 计数。Metric 改为直接
verdict 后，计数入口需要迁移，但持久化结构和历史质量趋势不应随结果字段重定义改变。

### 决策

- 每个已声明 metric 继续保存 total、good、bad、failed，直接按 `Metric.verdict` 累计。
- Executor 整体失败时，Runner 按 `spec.metrics[]` 为全部指标生成 failed，因此各指标继续覆盖完整诊断
  分母；单 Metric failed 与其他 good/bad 可以并存。
- 可选 evaluate、pre-evaluate/evaluate 全量累计、Selector 分母和明细无关性保持现有语义。
- TaskStats JSON、canonical 校验、质量分 `good / (good + bad)`、诊断器/category/global 聚合及
  diagnosis-stats API 字段均不改变。
- 不引入诊断器级 execution 计数或动态 Metric 统计；存量 TaskStats 继续按现有合同读取。

### 保持不变

计数不从 DiagnosisRecord 明细或 saved_records 推导，failed 继续不进入质量分母。

### 影响与风险

整体失败仍会在每个声明 Metric 上计一次 failed，这是保持完整分母和历史结构的有意选择，不代表各
指标分别执行后失败。调用方需要结合执行日志或保存记录的 Execution 排查根因。

### 验证

覆盖 good/bad/failed 直接累计、整体失败全指标计数、单指标部分失败、evaluate、现有 canonical 结构和
历史形状读取；不新增 DiagnosisRecord 旧字段兼容测试。

### 实施说明

已实施：TaskStats 已直接按 Metric.verdict 累计，结构和质量分公式保持不变。

## task-stats-metric-counters@20260902-1729

**以 skipped 分离诊断器局部不适用目标**

```yaml
record_id: task-stats/metric-counters@20260902-1729
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-09-02T17:29+08:00
decided_at: 2026-09-02T17:29+08:00
implemented_at: 2026-09-02T17:53+08:00
supersedes:
  - task-stats/metric-counters@20260823-1933
related_records:
  - executor/pre-evaluation@20260902-1729
  - metric-result/evaluation-status@20260902-1729
  - task-runner/execution-pipeline@20260902-1729
affected_objects:
  - executor
  - task-runner
  - metric-result
canonical_doc: docs/api.md
```

### 背景

诊断器局部输入无效既不能进入 good/bad/failed，又需要在 Task 终态统计中可观测。引入局部 skip 后，
不同 Diagnoser 的实际结果总数可以不同，但都应能与共享样本覆盖数对齐。

### 决策

- 每个 metric 保存 `total/good/bad/failed`，并允许可选 `evaluate` 和 `skipped`。
- `total = good + bad + failed`，只表示实际产生 Metric 的目标数；`skipped` 独立记录该 Diagnoser 通过
  pre_evaluate 明确跳过的目标数，不进入 total 或质量分母。
- 一次 Diagnoser skip 按该 Diagnoser 的全部声明 metric 各累计一次 skipped；`evaluate <= total`。
- 对完整处理的旧版 Trace 查询源样本，每个 metric 的 coverage 为 `total + skipped`，同一 Task 各 metric 的
  coverage 必须一致。`task_target_count()` 返回共同 coverage。
- 历史或离线数仓 TaskStats 可以不含 skipped，读取时按零处理，输出 diagnosis-stats 时显式返回
  `skipped=0`。现有 `evaluate` 可选兼容规则保持不变。
- 质量分继续使用 `good / (good + bad)`；failed 和 skipped 均不进入分母。

### 保持不变

Selector 排除项不进入 metric 统计，计数不从 DiagnosisRecord 或 saved_records 推导，整体失败仍为每个
声明 Metric 生成 failed。

### 影响与风险

新增可选持久化字段和 diagnosis-stats 响应字段；旧消费者忽略未知字段即可兼容。把 total 当作共享目标数
的消费者需迁移到 target_count 或 `total + skipped`。

### 备选方案

让 total 包含 skipped 会破坏 `total = good + bad + failed` 的既有含义，并把未产生 Metric 的目标混入
结果总数。

### 验证

覆盖累计、canonical 校验、历史缺省、coverage 一致性、target_count、owner stats、统计 API 字段和
good/bad 质量分。

### 实施说明

已实施：TaskStats 已支持可选 `skipped`，canonical 校验、coverage target_count 和 diagnosis-stats 输出已
同步，历史缺失字段按零读取。
