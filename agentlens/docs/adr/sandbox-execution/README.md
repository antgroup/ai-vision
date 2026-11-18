---
object_key: sandbox-execution
title: Sandbox Execution Decision Archive
scope: Sandbox Execution resource and execution contracts
---

# Sandbox Execution Decision Archive

## Current decision index

<!-- adr-index:start -->
| 决策主题 | 目标决策 | 当前已实施决策 |
|---|---|---|
| [input-budget](input-budget.md) | [sandbox-execution/input-budget@20261104-1710](input-budget.md#sandbox-execution-input-budget20261104-1710) | [sandbox-execution/input-budget@20261104-1710](input-budget.md#sandbox-execution-input-budget20261104-1710) |
| [input-transport](input-transport.md) | [sandbox-execution/input-transport@20261104-1500](input-transport.md#sandbox-execution-input-transport20261104-1500) | [sandbox-execution/input-transport@20261104-1500](input-transport.md#sandbox-execution-input-transport20261104-1500) |
| [lifecycle](lifecycle.md) | [sandbox-execution/lifecycle@20261103-1505](lifecycle.md#sandbox-execution-lifecycle20261103-1505) | [sandbox-execution/lifecycle@20261103-1505](lifecycle.md#sandbox-execution-lifecycle20261103-1505) |
<!-- adr-index:end -->

## Related cross-object decisions

<!-- adr-related:start -->
`A → B` 表示 A 的决策影响 B；`A ↔ B` 只表示记录相关，不表示影响方向。

| 对象关系 | 决策记录 | 标题 |
|---|---|---|
| Executor → Sandbox Execution | [executor/sandbox-backend@20261102-1700](../executor/sandbox-backend.md#executor-sandbox-backend20261102-1700) | Custom Rule Sandbox Backend contract |
| Executor → Sandbox Execution | [executor/sandbox-backend@20261103-1505](../executor/sandbox-backend.md#executor-sandbox-backend20261103-1505) | Enhanced Python runtime on the existing sandbox backend |
| Task → Sandbox Execution | [task/execution-snapshot@20261102-1700](../task/execution-snapshot.md#task-execution-snapshot20261102-1700) | Freeze optional sandbox routing and retain the 1.4 local execution path |
| Task → Sandbox Execution | [task/execution-snapshot@20261103-1505](../task/execution-snapshot.md#task-execution-snapshot20261103-1505) | Freeze enhanced runtime and materials under ABI 1.6 |
| Executor ↔ Sandbox Execution | [executor/sandbox-backend@20261103-1505](../executor/sandbox-backend.md#executor-sandbox-backend20261103-1505) | Enhanced Python runtime on the existing sandbox backend |
| Task ↔ Sandbox Execution | [task/execution-snapshot@20261103-1505](../task/execution-snapshot.md#task-execution-snapshot20261103-1505) | Freeze enhanced runtime and materials under ABI 1.6 |
<!-- adr-related:end -->
