---
object_key: task-runner
topic: execution-pipeline
title: Task Runner 执行流水线
---

# Task Runner 执行流水线

## task-runner-execution-pipeline@20260823-0234

**Runner 按固定流水线共享数据与 Executor**

```yaml
record_id: task-runner/execution-pipeline@20260823-0234
decision_status: superseded
implementation_status: retired
proposed_at: 2026-08-23T02:34+08:00
decided_at: 2026-08-23T02:34+08:00
implemented_at: 2026-08-23T02:34+08:00
supersedes: []
affected_objects:
  - trace-retrieval
  - projection
  - executor
  - diagnosis-record
  - task-stats
canonical_doc: docs/architecture.md
```

### 背景

一次 Task 绑定多个 Diagnoser；重复读取、投影或构造 Executor 会增加成本并造成同一目标事实不一致。

### 决策

- Runner 先加载 Task、Job、Dataset 和全部 Diagnoser，再确定查询窗口和 Executor 集合。
- Executor 在 Task 范围创建，在旧版 Trace 查询源的各批次间复用，并在 Task 结束时统一 close。
- 同一目标完成 Selector、pre-evaluate 和必要补拉后只构造一份 Trace、投影一次，并并发执行全部绑定 Diagnoser。
- 每条 Trace 的全部诊断结束后再处理下一条；统计先累计，明细再按 Job 策略筛选。

### 保持不变

Runner 负责编排，不吸收数据源通信原语、Projection 业务解析或具体诊断算法。

### 影响与风险

共享流水线保证同一 Task 内事实一致；单目标慢诊断会限制后续目标推进。

### 验证

由 Runner 多 Diagnoser、Executor 生命周期、跨 batch、Projection 次数和关闭异常测试验证。

### 实施说明

本记录已按当前代码、测试和 `docs/architecture.md` 核对为已实施行为。

## task-runner-execution-pipeline@20260823-1933

**Runner 统一收口 DiagnosisResult 与记录生命周期**

```yaml
record_id: task-runner/execution-pipeline@20260823-1933
decision_status: superseded
implementation_status: retired
proposed_at: 2026-08-23T19:33+08:00
decided_at: 2026-08-23T19:42+08:00
implemented_at: 2026-08-23T20:59+08:00
supersedes:
  - task-runner/execution-pipeline@20260823-0234
related_records:
  - executor/plugin-contract@20260823-1933
  - executor/pre-evaluation@20260823-1933
  - diagnosis-record/verdict-derivation@20260823-1933
affected_objects:
  - trace-retrieval
  - projection
  - executor
  - diagnosis-record
  - task-stats
canonical_doc: docs/architecture.md
```

### 背景

Executor 当前构造不完整 DiagnosisRecord，Runner 再回填身份和时间；失败路径又由 Runner 单独构造
MetricResult 和记录，导致正常、预评估和异常入口的记录职责分散。

### 决策

- Runner 接收 Executor 的 DiagnosisResult，并成为 DiagnosisRecord 的唯一构造方；统一填充 UUID、
  Task/Job/Diagnoser 身份、target、owner、实验组、诊断时间、对象时间和 review 初始状态。
- Runner 从持有的完整 Trace 统一构造 trace snapshot；DiagnosisResult 不携带 snapshot。pre-evaluate
  final 不保存明细，因此不构造 snapshot 或持久化记录。
- 结构化 ExecutorFailure 由 Runner 转换为失败 Execution 和全部声明的 failed Metric；未预期异常使用
  安全通用 code/msg 兜底，不把原始异常直接写入可展示字段。
- Runner 在明细保存前按 Metric verdict 累计现有 TaskStats，并继续应用 cancellation/attempt fence、
  pre-evaluate 不保存和 Job 稀疏保存策略。
- verdict/summary 由领域派生；Runner 和 Executor 均不得输入平行结论。

### 保持不变

Task 范围 Executor 复用、共享 Trace/Projection、逐目标收口、统计先于明细筛选以及终态 fence 保持
不变。

### 影响与风险

Runner 构造职责扩大但仍不吸收业务算法；必须确保所有正常、部分失败、整体失败和 pre-evaluate 路径
只经过一个收口入口，避免漏计统计或重复构造记录。

### 验证

覆盖多 Diagnoser、正常/部分/整体失败、pre-evaluate、snapshot、身份回填、统计顺序、保存策略、取消和
stale attempt。

### 实施说明

已实施：Runner 已成为 DiagnosisRecord 唯一构造方并统一收口正常、失败和预评估路径。

## task-runner-execution-pipeline@20260902-1729

**Runner 独立汇流诊断结果与局部跳过**

```yaml
record_id: task-runner/execution-pipeline@20260902-1729
decision_status: superseded
implementation_status: retired
proposed_at: 2026-09-02T17:29+08:00
decided_at: 2026-09-02T17:29+08:00
implemented_at: 2026-09-02T17:53+08:00
supersedes:
  - task-runner/execution-pipeline@20260823-1933
related_records:
  - executor/pre-evaluation@20260902-1729
  - task-stats/metric-counters@20260902-1729
affected_objects:
  - trace-retrieval
  - projection
  - executor
  - diagnosis-record
  - task-stats
canonical_doc: docs/architecture.md
```

### 背景

原流水线假定每个 target 与 Diagnoser 都产生 DiagnosisResult，缺失结果会转为 FAILED。pre_evaluate
局部 skip 需要成为显式计划项，避免被误判失败，同时不能阻止同一 target 的其他 Diagnoser 继续执行。

### 决策

- 旧版 Trace 查询源的 pre_evaluate 计划分别保存 final result、skip 和 evaluate Diagnoser 集合，三者按同一
  target-diagnoser 互斥。
- hydration 仍按至少一个 Diagnoser 请求 evaluate 的 target 并集执行；全部诊断器 final/skip 的 target
  不补拉完整 Trace、不投影。
- Runner 汇流时先为 skip 的 Diagnoser 累计全部声明指标的 skipped 并结束该分支；final 与 evaluate
  结果沿用统一结果校验和记录流程。
- skip 不触发 missing_result FAILED、不写 DiagnosisRecord、不影响同 target 的其他 Diagnoser，也不
  改变 Dataset Selector 的共享样本与 target metadata。
- 离线数仓流程继续对完整 Trace 直接 evaluate，由数据源处理输入跳过。

### 保持不变

Task 范围 Executor 复用、共享 Trace/Projection、逐目标并发、失败收口、统计先于明细筛选、记录唯一
构造权和终态 fence 保持不变。

### 影响与风险

Runner 计划状态增加后必须验证每个 Diagnoser 恰好走 skip/final/evaluate 一条路径；否则可能漏计或
错误生成 FAILED。

### 备选方案

把 skip 包装成特殊 DiagnosisResult 会污染 Metric 状态和记录派生合同。

### 验证

覆盖单/多 Diagnoser、全跳过免 hydration、混合路径共享 hydration、无记录、无 missing_result、统计和
Selector 共存。

### 实施说明

已实施：Runner 的 target 计划和汇流已区分 final、skip 与 evaluate，并保持同 target 多 Diagnoser、
Selector 和记录策略相互独立。

## task-runner-execution-pipeline@20261009-1800

**从 Task 快照执行并维持结果、跳过与终态汇流**

```yaml
record_id: task-runner/execution-pipeline@20261009-1800
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-10-09T18:00+08:00
decided_at: 2026-10-09T18:00+08:00
implemented_at: 2026-10-09T18:00+08:00
supersedes:
  - task-runner/execution-pipeline@20260902-1729
related_records:
  - task/execution-snapshot@20261009-1800
affected_objects:
  - trace-retrieval
  - projection
  - executor
  - diagnosis-record
  - task-stats
canonical_doc: docs/architecture.md
```

### 背景

Task 配置需要跨等待与重试保持一致，同时原流水线的共享读取、skip 和 terminal fence 不得改变。

### 决策

- CAS start_running 后优先恢复 Task 执行快照，并先校验执行语义版本；无快照的旧 Task 按明确
  legacy 合同读取 Job/Dataset/Diagnosers。运行时只解析模型连接凭证，模型身份与生成参数取快照。
- 未支持的 snapshot schema/revision/score policy 在恢复前拒绝；失败路径不再次解析被拒绝的
  metadata，使用安全空计数标 FAILED，不假称已按当前 score policy 执行。
- 每个 Task 创建并复用 Executor，共享 phase1、补拉 Trace 和 Projection；各 target-diagnoser
  互斥走 pre_evaluate final、skip 或 evaluate。skip 按所有声明指标累计 skipped 与原因次数，不产
  Metric/Record，也不影响同 target 的其他 Diagnoser。
- Runner 统一校验结果，构造记录身份/对象时间/脱敏定义；所有 final/evaluate 在明细筛选前累计。
  evaluate 明细仍由冻结 Job.save_badcase_only 控制；final 与 skip 不保存 Record。
- 读取或执行异常保持 FAILED，部分统计保留并将 source_complete=false；不以 SKIP 隐藏故障。
- 成功或失败通过 worker/attempt/status CAS 一次写 OB Stats，只有赢得 CAS 的 attempt 上报 TaskStats。

### 保持不变

trace 级两阶段读取、Projection 按 runtime 路由、逐目标并发、record 去重、非抢占运行及取消合同不变。

### 影响与风险

语义版本不支持时在读取 Trace 或请求 Judge 前失败；发布时必须保留兼容 Worker 或排空待执行版本。

### 验证

Runner 冻结定义、retry、skip、failed、stale attempt、稀疏记录与原有取消/恢复测试。

### 实施说明

Runner 快照入口和 metadata 传播已实现，原终态 fence 与 Record 选择路径保留。
