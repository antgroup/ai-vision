---
object_key: task-runner
title: Task Runner 决策档案
scope: Worker-side task execution orchestration and terminal-state finalization
---

# Task Runner 决策档案

Worker 侧任务执行编排与终态收口。本文件维护对象范围；具体决策按长期主题存放在同目录文件中。

## 当前决策索引

<!-- adr-index:start -->
| 决策主题 | 目标决策 | 当前已实施决策 |
|---|---|---|
| [execution-pipeline](execution-pipeline.md) | [task-runner/execution-pipeline@20261009-1800](execution-pipeline.md#task-runner-execution-pipeline20261009-1800) | [task-runner/execution-pipeline@20261009-1800](execution-pipeline.md#task-runner-execution-pipeline20261009-1800) |
| [execution-result](execution-result.md) | [task-runner/execution-result@20260823-0234](execution-result.md#task-runner-execution-result20260823-0234) | [task-runner/execution-result@20260823-0234](execution-result.md#task-runner-execution-result20260823-0234) |
<!-- adr-index:end -->

## 相关跨对象决策

<!-- adr-related:start -->
`A → B` 表示 A 的决策影响 B；`A ↔ B` 只表示记录相关，不表示影响方向。

| 对象关系 | 决策记录 | 标题 |
|---|---|---|
| Dataset → Task Runner | [dataset/source-contract@20260916-1130](../dataset/source-contract.md#dataset-source-contract20260916-1130) | 诊断数据源统一采用 SigNoz |
| Dataset → Task Runner | [dataset/target-selection@20260916-1130](../dataset/target-selection.md#dataset-target-selection20260916-1130) | 移除退役后端专属 Selector，保留共享选择合同 |
| Dataset → Task Runner | [dataset/target-selection@20261009-2200](../dataset/target-selection.md#dataset-target-selection20261009-2200) | 保留 Selector 合同并增加可信固定 Trace 选择 |
| Rule Diagnoser → Task Runner | [diagnoser-rule/sensitive-leakage@20260827-1603](../diagnoser-rule/sensitive-leakage.md#diagnoser-rule-sensitive-leakage20260827-1603) | 在模型可见文本中按受控预算检测可信敏感信息泄漏 |
| Diagnoser → Task Runner | [diagnoser/builtin-definition-source@20261009-1515](../diagnoser/builtin-definition-source.md#diagnoser-builtin-definition-source20261009-1515) | 八 Rule 的受管身份、版本与有限用户策略 |
| Diagnoser → Task Runner | [diagnoser/builtin-definition-source@20261009-1945](../diagnoser/builtin-definition-source.md#diagnoser-builtin-definition-source20261009-1945) | 内置诊断器定义来源：通用 Core 与可信有界执行 |
| Diagnoser → Task Runner | [diagnoser/model-reference@20261009-1945](../diagnoser/model-reference.md#diagnoser-model-reference20261009-1945) | Diagnoser 模型引用：通用 Core 与可信有界执行 |
| Diagnosis Record → Task Runner | [diagnosis-record/persistence-policy@20260823-0234](../diagnosis-record/persistence-policy.md#diagnosis-record-persistence-policy20260823-0234) | 按 Job 策略稀疏保存 evaluate 明细 |
| Executor → Task Runner | [executor/applicability@20261009-1345](../executor/applicability.md#executor-applicability20261009-1345) | Executor 完整轨迹适用性：完整材料与独立跳过 |
| Executor → Task Runner | [executor/plugin-contract@20260823-0234](../executor/plugin-contract.md#executor-plugin-contract20260823-0234) | Executor 通过注册表实现统一插件合同 |
| Executor → Task Runner | [executor/plugin-contract@20260823-1933](../executor/plugin-contract.md#executor-plugin-contract20260823-1933) | Executor 只返回诊断事实与执行观测 |
| Executor → Task Runner | [executor/pre-evaluation@20260823-0234](../executor/pre-evaluation.md#executor-pre-evaluation20260823-0234) | pre-evaluate 只决定轻量终结或继续完整评估 |
| Executor → Task Runner | [executor/pre-evaluation@20260823-1933](../executor/pre-evaluation.md#executor-pre-evaluation20260823-1933) | pre-evaluate 以 DiagnosisResult 返回轻量终态 |
| Executor → Task Runner | [executor/pre-evaluation@20260902-1729](../executor/pre-evaluation.md#executor-pre-evaluation20260902-1729) | pre-evaluate 支持诊断器局部跳过 |
| Executor → Task Runner | [executor/pre-evaluation@20261010-0350](../executor/pre-evaluation.md#executor-pre-evaluation20261010-0350) | 保留局部预评估与完整材料门禁 |
| Executor → Task Runner | [executor/sandbox-backend@20261102-1700](../executor/sandbox-backend.md#executor-sandbox-backend20261102-1700) | Custom Rule Sandbox Backend contract |
| LLM Judge → Task Runner | [llm-judge/request-attempt-hooks@20261009-1945](../llm-judge/request-attempt-hooks.md#llm-judge-request-attempt-hooks20261009-1945) | LLM Judge 请求尝试 hooks：通用 Core 与可信有界执行 |
| Sandbox Execution → Task Runner | [sandbox-execution/lifecycle@20261102-1700](../sandbox-execution/lifecycle.md#sandbox-execution-lifecycle20261102-1700) | Sandbox Execution contract |
| Sandbox Execution → Task Runner | [sandbox-execution/lifecycle@20261102-1833](../sandbox-execution/lifecycle.md#sandbox-execution-lifecycle20261102-1833) | Clarify OpenShell workspace ownership and delete preconditions |
| Scheduler → Task Runner | [scheduler/leader-election@20260823-0234](../scheduler/leader-election.md#scheduler-leader-election20260823-0234) | 以数据库租约和递增 fenced token 选主 |
| Scheduler → Task Runner | [scheduler/task-dispatch@20260823-0234](../scheduler/task-dispatch.md#scheduler-task-dispatch20260823-0234) | Master 扫描到期 Task 并以 CAS 派发 |
| Scheduler → Task Runner | [scheduler/task-dispatch@20260909-1639](../scheduler/task-dispatch.md#scheduler-task-dispatch20260909-1639) | 以已分配 Task 负载和逐次容量检查进行派单 |
| Scheduler → Task Runner | [scheduler/task-recovery@20260823-0234](../scheduler/task-recovery.md#scheduler-task-recovery20260823-0234) | 按超时、心跳和 attempt 上限恢复异常 Task |
| Security → Task Runner | [security/service-authentication@20260823-0234](../security/service-authentication.md#security-service-authentication20260823-0234) | HTTP 服务使用预共享 token 和执行 fence |
| Security → Task Runner | [security/service-authentication@20261020-1127](../security/service-authentication.md#security-service-authentication20261020-1127) | 匿名存活与数据库就绪探针，业务接口保留服务认证 |
| Task Stats → Task Runner | [task-stats/aggregation-model@20260823-0234](../task-stats/aggregation-model.md#task-stats-aggregation-model20260823-0234) | TaskStats 保存 Task 级完整诊断事实树 |
| Task Stats → Task Runner | [task-stats/aggregation-model@20261009-1800](../task-stats/aggregation-model.md#task-stats-aggregation-model20261009-1800) | 以 TaskStats v3 冻结定义和保守质量策略解释历史 |
| Task Stats → Task Runner | [task-stats/business-time@20260823-0234](../task-stats/business-time.md#task-stats-business-time20260823-0234) | 统计时间使用诊断数据窗口而非执行时间 |
| Task Stats → Task Runner | [task-stats/business-time@20261009-2240](../task-stats/business-time.md#task-stats-business-time20261009-2240) | 按选择模式固定统计业务时间 |
| Task Stats → Task Runner | [task-stats/metric-counters@20260823-0234](../task-stats/metric-counters.md#task-stats-metric-counters20260823-0234) | 记录指标结果计数和完整评估数量 |
| Task Stats → Task Runner | [task-stats/metric-counters@20260823-1933](../task-stats/metric-counters.md#task-stats-metric-counters20260823-1933) | 直接按 Metric verdict 累计现有指标计数 |
| Task Stats → Task Runner | [task-stats/metric-counters@20260902-1729](../task-stats/metric-counters.md#task-stats-metric-counters20260902-1729) | 以 skipped 分离诊断器局部不适用目标 |
| Task Stats → Task Runner | [task-stats/persistence@20260823-0234](../task-stats/persistence.md#task-stats-persistence20260823-0234) | 只持久化赢得终态 fence 的完整统计快照 |
| Task → Task Runner | [task/attempt-ownership@20260823-0234](../task/attempt-ownership.md#task-attempt-ownership20260823-0234) | 以 worker、attempt 和状态 fence 决定终态所有权 |
| Task → Task Runner | [task/attempt-ownership@20261009-1945](../task/attempt-ownership.md#task-attempt-ownership20261009-1945) | Task Attempt 所有权：通用 Core 与可信有界执行 |
| Task → Task Runner | [task/execution-snapshot@20261009-1800](../task/execution-snapshot.md#task-execution-snapshot20261009-1800) | 创建 Task 时冻结有效执行定义与语义版本 |
| Task → Task Runner | [task/execution-snapshot@20261009-1945](../task/execution-snapshot.md#task-execution-snapshot20261009-1945) | 创建 Task 时冻结有效执行定义与语义版本：通用 Core 与可信有界执行 |
| Task → Task Runner | [task/execution-snapshot@20261010-0350](../task/execution-snapshot.md#task-execution-snapshot20261010-0350) | ABI1.4 专项冻结；安全历史读取与全 pool 物理发布门禁 |
| Task → Task Runner | [task/execution-snapshot@20261028-1611](../task/execution-snapshot.md#task-execution-snapshot20261028-1611) | Freeze one saved check for an authorized Trace selection |
| Task → Task Runner | [task/execution-snapshot@20261102-1700](../task/execution-snapshot.md#task-execution-snapshot20261102-1700) | Freeze optional sandbox routing and retain the 1.4 local execution path |
| Task → Task Runner | [task/judge-request-budget@20261009-1945](../task/judge-request-budget.md#task-judge-request-budget20261009-1945) | Task Judge 请求预算：通用 Core 与可信有界执行 |
| Task → Task Runner | [task/judge-request-budget@20261028-1611](../task/judge-request-budget.md#task-judge-request-budget20261028-1611) | Reuse the durable request budget for one saved diagnoser |
| Task → Task Runner | [task/lifecycle@20260823-0234](../task/lifecycle.md#task-lifecycle20260823-0234) | Task 使用明确状态机和非抢占式执行 |
| Trace Retrieval → Task Runner | [trace-retrieval/candidate-hydration@20260823-0234](../trace-retrieval/candidate-hydration.md#trace-retrieval-candidate-hydration20260823-0234) | 按 pre-evaluate 候选并集补拉完整 Trace |
| Trace Retrieval → Task Runner | [trace-retrieval/candidate-hydration@20261028-1728](../trace-retrieval/candidate-hydration.md#trace-retrieval-candidate-hydration20261028-1728) | Limit diagnosis candidates by distinct Trace count before reading lightweight fields |
| Trace Retrieval → Task Runner | [trace-retrieval/detail-replay@20260916-1130](../trace-retrieval/detail-replay.md#trace-retrieval-detail-replay20260916-1130) | 通过 Job 保存的 SigNoz 数据集回放详情 |
| Trace Retrieval → Task Runner | [trace-retrieval/query-window@20260823-0234](../trace-retrieval/query-window.md#trace-retrieval-query-window20260823-0234) | 在读取前固定数据源查询窗口与业务时间 |
| Trace Retrieval → Task Runner | [trace-retrieval/query-window@20260916-1130](../trace-retrieval/query-window.md#trace-retrieval-query-window20260916-1130) | 保留 SigNoz 半开窗口与业务时间 |
| Diagnosis Record ↔ Task Runner | [diagnosis-record/verdict-derivation@20260823-1933](../diagnosis-record/verdict-derivation.md#diagnosis-record-verdict-derivation20260823-1933) | 从 Execution 与 Metric 派生记录结论和摘要 |
| Executor ↔ Task Runner | [executor/plugin-contract@20260823-1933](../executor/plugin-contract.md#executor-plugin-contract20260823-1933) | Executor 只返回诊断事实与执行观测 |
| Executor ↔ Task Runner | [executor/pre-evaluation@20260823-1933](../executor/pre-evaluation.md#executor-pre-evaluation20260823-1933) | pre-evaluate 以 DiagnosisResult 返回轻量终态 |
| Executor ↔ Task Runner | [executor/pre-evaluation@20260902-1729](../executor/pre-evaluation.md#executor-pre-evaluation20260902-1729) | pre-evaluate 支持诊断器局部跳过 |
| Executor ↔ Task Runner | [executor/pre-evaluation@20261010-0350](../executor/pre-evaluation.md#executor-pre-evaluation20261010-0350) | 保留局部预评估与完整材料门禁 |
| Task Stats ↔ Task Runner | [task-stats/metric-counters@20260823-1933](../task-stats/metric-counters.md#task-stats-metric-counters20260823-1933) | 直接按 Metric verdict 累计现有指标计数 |
| Task Stats ↔ Task Runner | [task-stats/metric-counters@20260902-1729](../task-stats/metric-counters.md#task-stats-metric-counters20260902-1729) | 以 skipped 分离诊断器局部不适用目标 |
| Task ↔ Task Runner | [task/execution-snapshot@20261009-1800](../task/execution-snapshot.md#task-execution-snapshot20261009-1800) | 创建 Task 时冻结有效执行定义与语义版本 |
<!-- adr-related:end -->
