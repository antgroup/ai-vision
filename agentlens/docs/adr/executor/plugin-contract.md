---
object_key: executor
topic: plugin-contract
title: Executor 插件合同
---

# Executor 插件合同

## executor-plugin-contract@20260823-0234

**Executor 通过注册表实现统一插件合同**

```yaml
record_id: executor/plugin-contract@20260823-0234
decision_status: superseded
implementation_status: retired
proposed_at: 2026-08-23T02:34+08:00
decided_at: 2026-08-23T02:34+08:00
implemented_at: 2026-08-23T02:34+08:00
supersedes: []
affected_objects:
  - diagnoser
  - task-runner
canonical_doc: docs/diagnoser/README.md
```

### 背景

Runner 需要以统一方式执行 Rule、LLM、builtin 和 custom 诊断，而不内置每种业务分支。

### 决策

- Executor 实现按 `(mode, name)` 注册到 `ExecutorRegistry`；Diagnoser 的 type/mode 和 builtin Spec
  先解析出该 name。
- 生命周期包含 Task 范围构造与复用、`pre_evaluate`、`evaluate(trace)` 和统一 `close`。
- 自定义 Rule code 只允许一个受 AST 校验的顶层 `evaluate(trace)` 入口。

### 保持不变

Runner 拥有调度、数据读取、样本门禁、记录元数据回填和持久化职责。LLM Executor 从 Runner 注入的
运行时 Spec 延迟构造并关闭 LLMJudge。

### 影响与风险

插件合同隔离业务实现；注册冲突、签名不合法或缺失实现必须在创建或执行前明确失败。

### 验证

由 Registry、Custom Rule 校验、Runner 生命周期和各 Executor 测试验证。

### 实施说明

本记录已按当前代码、测试和 `docs/diagnoser/README.md` 核对为已实施行为。

## executor-plugin-contract@20260823-1933

**Executor 只返回诊断事实与执行观测**

```yaml
record_id: executor/plugin-contract@20260823-1933
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-08-23T19:33+08:00
decided_at: 2026-08-23T19:42+08:00
implemented_at: 2026-08-23T20:59+08:00
supersedes:
  - executor/plugin-contract@20260823-0234
related_records:
  - executor/pre-evaluation@20260823-1933
  - executor/result-parsing@20260823-1933
  - metric-result/evaluation-status@20260823-1933
  - task-runner/execution-pipeline@20260823-1933
affected_objects:
  - diagnoser
  - diagnosis-record
  - task-runner
canonical_doc: docs/diagnoser/README.md
```

### 背景

当前 Executor 直接构造带空 UUID、空 Job/Task/Diagnoser 字段和 review 默认值的 DiagnosisRecord，Runner
随后回填，导致执行插件持有不属于自己的记录生命周期职责。

### 决策

- `evaluate(trace)` 返回 `DiagnosisResult`，其字段只有 `metrics: list[Metric]` 和 `execution: Execution`；
  Executor 不构造 DiagnosisRecord。
- Execution 固定包含 `status=succeeded|failed`、`code`、`msg`、`evidence`。整体成功时 code/msg 为空；
  整体失败时 code/msg 非空。
- Custom Rule 用户函数和 LLM 模型只输出 metrics，不能控制 execution、顶层 verdict/summary、snapshot、
  review、身份或时间字段。可信 Executor adapter 负责成功 execution 及执行观测。
- Custom Rule Diagnoser spec 不提供单次执行超时；执行超时和每进程 worker 数量分别由全局
  `EXEC_TIMEOUT`、`CUSTOM_RULE_WORKER_POOL_SIZE` 配置。
- 可预期的沙箱、依赖和协议失败转换为携带 code/msg/evidence 的结构化 ExecutorFailure；Runner 捕获后
  构造失败 Execution 和全部声明的 failed Metric。未预期异常由 Runner 使用安全的通用失败兜底。
- Runner 持有完整 Trace，统一构造 trace snapshot、DiagnosisRecord 身份、时间和 review 初始状态。

### 保持不变

Executor 仍按 `(mode, name)` 注册，在 Task 范围复用并统一 close；自定义 Rule 仍只有受 AST 校验的
`evaluate(trace)` 入口。

### 影响与风险

所有 Executor、pre-evaluate、Runner 和 Trial 的返回类型都需迁移；异常边界不完整会丢失 execution
evidence 或把底层异常暴露为 msg。Runner 成为 DiagnosisRecord 的唯一构造方，必须覆盖所有入口。

### 验证

覆盖 Registry、Task 范围生命周期、DiagnosisResult/Execution 约束、结构化 ExecutorFailure、Runner
兜底和 snapshot 构造。

### 实施说明

已实施：Executor 返回 DiagnosisResult，Execution 与 ExecutorFailure 合同已落地。
