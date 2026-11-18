---
object_key: trace
topic: runtime-input
title: Trace 运行时输入
---

# Trace 运行时输入

## trace-runtime-input@20260823-0234

**统一 Trace 运行时输入边界**

```yaml
record_id: trace/runtime-input@20260823-0234
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-08-23T02:34+08:00
decided_at: 2026-08-23T02:34+08:00
implemented_at: 2026-08-23T02:34+08:00
supersedes: []
affected_objects:
  - projection
  - executor
canonical_doc: docs/architecture.md
```

### 背景

不同数据源需要向 Runner 和 Executor 提供同一份可追溯输入，同时允许未知 runtime 或投影失败时保留原始事实。

### 决策

- 数据源统一构造 frozen dataclass `Trace`，包含 `trace_id`、`runtime_type`、原始 `spans`、可选 owner、
  来源元数据和可选 Projection；附加 Projection 时返回新的 Trace 值。
- Runner 对每条 Trace 至多投影一次；读取原始事实的 Executor 使用 `trace.spans`，依赖语义的 Executor 调用 `require_projection()`。
- `to_python_value()` 暴露语义字段与同一份原始 `spans`，不由 Diagnoser 直接查询底层数据源。

### 保持不变

未知 runtime 和投影失败不改写原始 spans，也不阻止只依赖原始输入的 Executor 执行。

### 影响与风险

统一输入减少数据源和 Executor 的组合分支；依赖 Projection 的诊断在语义不可用时明确失败。

### 验证

由 Trace、Runner、Projection 和 Executor 单元测试验证原始 spans 身份、单次投影及失败路径。

### 实施说明

本记录已按当前代码、测试和 `docs/architecture.md` 核对为已实施行为。
