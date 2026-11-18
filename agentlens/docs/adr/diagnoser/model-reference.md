---
object_key: diagnoser
topic: model-reference
title: Diagnoser 模型引用
---

# Diagnoser 模型引用

## diagnoser-model-reference@20260823-0234

**LLM Diagnoser 通过模型连接 UUID 引用模型**

```yaml
record_id: diagnoser/model-reference@20260823-0234
decision_status: superseded
implementation_status: retired
proposed_at: 2026-08-23T02:34+08:00
decided_at: 2026-08-23T02:34+08:00
implemented_at: 2026-08-23T02:34+08:00
supersedes: []
affected_objects:
  - model-connection
  - executor
  - llm-judge
canonical_doc: docs/api.md
```

### 背景

模型地址、模型名和密钥若内联在每个 Diagnoser，会重复凭证并阻碍统一更新与引用检查。

### 决策

- LLM Diagnoser 使用 `spec.model_connection_uuid` 引用相同 `space_uuid` 的 ModelConnection；连接不绑定
  `agent_uuid`。
- 创建和更新时校验引用存在且属于当前 Space；Trial 和 Task 执行也只在当前 Space 解析连接，并要求
  连接状态为 available。
- Executor 解析引用后构造 LLMJudge；旧的内联 `spec.model` 不作为运行时回退。

### 保持不变

Prompt、metrics 和 thinking 配置仍属于 Diagnoser Spec；连接资源只管理服务访问合同。

### 影响与风险

连接删除、Space 错配或状态不可用会阻止使用；同一 Space 的连接可被不同 Agent 的 Diagnoser 引用，
凭证更新可在不改 Diagnoser 的情况下生效。

### 验证

由 ModelConnection 引用查询、Diagnoser CRUD/同步和 LLM Executor 测试验证。

### 实施说明

本记录已按当前代码、测试和 `docs/api.md` 核对为已实施行为。


## diagnoser-model-reference@20261009-1945

**Diagnoser 模型引用：通用 Core 与可信有界执行**

```yaml
record_id: diagnoser/model-reference@20261009-1945
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-10-09T19:45+08:00
decided_at: 2026-10-09T19:45+08:00
implemented_at: 2026-10-09T19:45+08:00
supersedes:
  - diagnoser/model-reference@20260823-0234
affected_objects:
  - model-connection
  - executor
  - llm-judge
  - task
  - task-runner
canonical_doc: docs/api.md
```

### 背景

既有 AVAILABLE-only 作为传统运行合同；可信 bounded Core/quick 允许完整同 Space 配置进行实际请求，历史检测状态只作为提示。

### 决策

- LLM Diagnoser 以 spec.model_connection_uuid 引用同 Space 的 ModelConnection，连接不绑定 Agent。CRUD、Trial、Task 解析都核对 Space；不回退内联 spec.model，不猜模型或凭证。
- legacy advanced Task/CUSTOM Trial 继续要求 AVAILABLE。仅服务端验证并冻结 bounded_core_v1 的 Core/quick Task，以及可信 builtin Core Trial，允许 UNTESTED/UNAVAILABLE 状态的完整合法配置执行。
- 该例外不是浏览器 policy toggle，不进行预飞 probe、不自动修改连接 status。实际请求错误保持 FAILED，重试占有界预算，usage unknown 独立保存。
- Task 冻结连接 UUID/endpoint/model_name 及生成参数；当前凭证仅按原 UUID 和 Space 在运行时解析，允许合法密钥轮换；连接丢失/配置缺失失败。endpoint 不含 userinfo/query/fragment。
- Prompt、metrics、thinking 和生成参数属于 Diagnoser Spec；凭证不入 Task snapshot、预算账本或日志。

### 保持不变

T1 质量/信号分离、TaskStats total/good/bad/failed/skipped、不可变历史来源、CUSTOM/未涉及 legacy 合同，以及原始 Trace/runtime 身份保持。

### 影响与风险

ABI 1.3.0 发布前需排空旧可执行 Task 或保留兼容 Worker。reservation 是保守上界，不是账单或 provider receipt；材料和模型判断覆盖有明确限制。

### 验证

使用独立反例的 genuine RED→GREEN、mock transport 与事务边界离线测试。双引擎锁/并发、平台/API/GUI 与真实模型人工校准由主控另验收，本记录不宣称这些已通过。

### 实施说明

源实现和离线合同已实现；Task 4 报告记录精确检查、风险及待补真实 gate。
