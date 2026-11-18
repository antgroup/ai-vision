---
object_key: diagnoser
topic: type-and-mode
title: Diagnoser 类型与模式
---

# Diagnoser 类型与模式

## diagnoser-type-and-mode@20260823-0234

**以 type、mode 与系统 Executor key 定位执行能力**

```yaml
record_id: diagnoser/type-and-mode@20260823-0234
decision_status: superseded
implementation_status: retired
proposed_at: 2026-08-23T02:34+08:00
decided_at: 2026-08-23T02:34+08:00
implemented_at: 2026-08-23T02:34+08:00
supersedes: []
affected_objects:
  - executor
  - http-api
canonical_doc: docs/diagnoser/README.md
```

### 背景

诊断器定义需要稳定区分来源和执行方式，同时允许新增实现而不改变 Runner 主流程。

### 决策

- `type` 区分 builtin 与 custom，`mode` 区分 rule 与 llm。
- `type` 与 `mode` 先解析系统拥有的 Executor name：custom 按 mode 固定解析为 `rule_custom` 或
  `llm_custom`，builtin 从 Spec 读取平台写入的 `executor` key。
- `ExecutorRegistry` 的实际注册和查找键是 `(mode, resolved executor name)`；type 只参与 name 解析，
  不直接进入 Registry key。
- 新增模式或类型必须同时定义配置校验、Executor 合同和读取输出，不通过目录命名隐式生效。

### 保持不变

具体业务 Diagnoser 的算法决策归属其独立对象；通用类型和模式只管理共享合同。

### 影响与风险

type/mode 到 Executor name 的解析规则及 Registry 组合键共同构成插件兼容边界，变化会影响已有数据库
定义和任务执行。

### 验证

由 Diagnoser CRUD、Registry 和 Runner 选择 Executor 测试验证合法组合与未知组合。

### 实施说明

本记录已按当前代码、测试和 `docs/diagnoser/README.md` 核对为已实施行为。

## diagnoser-type-and-mode@20261117-1056

**Preserve builtin Rule execution when creating a custom copy**

```yaml
record_id: diagnoser/type-and-mode@20261117-1056
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-11-17T10:56+08:00
decided_at: 2026-11-17T10:56+08:00
implemented_at: 2026-11-17T10:56+08:00
supersedes:
  - diagnoser/type-and-mode@20260823-0234
related_records:
  - task/execution-snapshot@20261028-1611
affected_objects:
  - executor
  - http-api
  - task
canonical_doc: docs/diagnoser/rule/README.md
```

### Background

Personal and team spaces need independent copies of preset rules. Recreating a preset with Python
would change the available projected facts, applicability and result capabilities even before a
user edits the rule.

### Decision

- `type` records builtin or custom ownership, while `mode` identifies Rule or LLM. Builtins use
  their system-owned executor. Ordinary custom rules use `rule_custom`, and custom LLMs use
  `llm_custom`. The registry continues to use `(mode, resolved executor name)`.
- The copy endpoint locks and reads an active Common or Coding builtin Rule in the supplied exact
  Space and Agent. It creates a custom Rule with a new UUID, preserves the effective source spec,
  and pins the system-owned executor and definition version with `builtin_rule_source`.
  Generic writes and trials reject client-supplied provenance. AgentLens keeps service-token
  authentication and AI Vision authorizes the user before forwarding.
- A copy supports independent name, description and applicable threshold changes. PATCH can clear
  the threshold with `null` on both the builtin and the copy. Submitting valid Python code converts
  the copy to an ordinary custom Rule under current sandbox policy, with its scoring role preserved.
- A copy uses the same applicability, evidence, metrics, and score role as its versioned source.
  The existing selected-Trace flow can run the saved copy. Snapshots containing a copy specify ABI
  `1.7.0`, including pinned provenance and effective thresholds. Other snapshots retain their
  existing ABI. A changed or unavailable source definition fails closed on execution.

### Unchanged

The existing execution paths and sandbox policies remain available for ordinary custom Rules and
builtins. Job bindings and existing Task snapshots retain their recorded configuration.

### Impact and risks

The copy has its own name, UUID and configuration. Source synchronization does not modify it.
Deploy compatible workers before enabling copied Rules. Drain affected Tasks or retain compatible
workers before rollback. No schema migration is required.

### Validation

Exercise the copy, threshold reset, and Python conversion APIs in an exact scope. Compare rule
outcomes and missing-evidence skips against the source through a restored selected-Trace snapshot.
Reject forged provenance and cross-scope copies.

### Implementation notes

Persist provenance in the existing Diagnoser spec. Freeze it in copied Rule Task snapshots and
validate it before selecting the registered executor.
