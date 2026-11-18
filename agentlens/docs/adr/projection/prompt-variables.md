---
object_key: projection
topic: prompt-variables
title: Projection Prompt 变量
---

# Projection Prompt 变量

## projection-prompt-variables@20260823-0234

**按变量根统一解析 Prompt 变量**

```yaml
record_id: projection/prompt-variables@20260823-0234
decision_status: superseded
implementation_status: implemented
proposed_at: 2026-08-23T02:34+08:00
decided_at: 2026-08-23T02:34+08:00
implemented_at: 2026-08-23T02:34+08:00
supersedes: []
affected_objects:
  - diagnoser
  - executor
canonical_doc: docs/projection/trace-contract.md
```

### 背景

Trial 与正式执行必须使用相同变量合同，否则相同 Prompt 会因入口不同得到不同输入。

### 决策

- `spans` 根使用 Trace 原始表达式解析；`trace_id`、`runtime_type`、`source_type` 和 `source_meta` 来自外层 Trace。
- 简单语义变量只能使用 runtime 对应 Projector 的 `available_vars`；带点号或列表索引的表达式由
  Projector 在公开、有限的 Projection 序列化结果上解析，不读取原始对象内部字段。
- dict/list 渲染为 JSON，其余值渲染为字符串；`owner_id` 不暴露给 Prompt。
- Trial 和正式 Executor 复用同一变量解析入口。

### 保持不变

各 Projector 继续拥有自己的语义变量集合，不建立跨 runtime 的伪通用变量。

### 影响与风险

可用简单变量、公开序列化字段或解析语义变化都属于 Projection 合同变更，并会同时影响 Trial 和正式诊断。

### 验证

由变量解析、LLM Trial 和自定义 LLM Executor 测试验证根路由、白名单和渲染。

### 实施说明

本记录已按当前代码、测试和 `docs/projection/trace-contract.md` 核对为已实施行为。

## projection-prompt-variables@20261010-0350

**保留 raw 表达式与当前注册的公开变量**

```yaml
record_id: projection/prompt-variables@20261010-0350
decision_status: accepted
implementation_status: not_started
proposed_at: 2026-10-10T03:50+08:00
decided_at: 2026-10-10T09:06+08:00
implemented_at: null
supersedes:
  - projection/prompt-variables@20260823-0234
related_records:
  - projection/projector-architecture@20261010-0350
  - projection/runtime-facts@20261010-0350
  - executor/llm-input-attachments@20260826-1714
affected_objects:
  - diagnoser
  - executor
canonical_doc: docs/projection/trace-contract.md
```

### 背景

变量白名单必须与当前实际 producer 对齐，不能声称不存在的语义材料可用。

### 决策

`spans` 根仍读取原始 Trace；`trace_id/runtime_type/source_type/source_meta` 来自外层 Trace。
简单语义根仅使用当前 Projector 的 `available_vars`，dot/index 只读取公开、有界序列化结果，不穿透内部对象。
dict/list 渲染为 JSON，其余转字符串；`owner_id` 不进入 Prompt。Formal/Trial 共用解析入口。
退役与未知 runtime 无 typed 根，保留原 runtime 和 raw 表达式；CUSTOM 不自动改 Prompt，依赖退役变量的配置
需人工检查。旧保存 JSON 不依赖当前 producer。
`image_vars` 使用独立注册的简单根，禁止模板渲染、重复、wildcard 和路径；错误类型或无注册根保持 FAILED，
非空多模态故障不进行纯文本重试。无 producer 不预注册图片变量；专项政策与材料不从浏览器变量注入。

### 保持不变

raw 和公共安全表达式、CUSTOM 变量入口、owner 隔离及通用附件接口保持。

### 影响与风险

raw 可执行不证明退役语义 Prompt 可复现；需人工检查，不补空值后判 GOOD。

### 验证

验证未知 runtime、简单根、dot/index、内部越界、JSON、退役变量、附件根和历史读取。

### 实施说明

目标合同见当前 Trace 契约；实施状态由记录元数据维护。
