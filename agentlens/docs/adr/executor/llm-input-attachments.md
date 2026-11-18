---
object_key: executor
topic: llm-input-attachments
title: Executor LLM 输入附件
---

# Executor LLM 输入附件

## executor-llm-input-attachments@20260826-1714

**以 image_vars 声明一次性 LLM 图片输入**

```yaml
record_id: executor/llm-input-attachments@20260826-1714
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-08-26T17:14+08:00
decided_at: 2026-08-26T17:14+08:00
implemented_at: 2026-08-26T17:32+08:00
supersedes: []
related_records:
  - llm-judge/multimodal-message@20260826-1714
affected_objects:
  - diagnoser
  - llm-judge
  - http-api
canonical_doc: docs/diagnoser/README.md
```

### 背景

一次性 LLM Executor 目前只解析 Prompt 文本变量。若在答非所问专用 Executor 中硬编码图片，会使
Builtin、Custom 和 Trial 形成不同协议，也无法让其他诊断器复用多模态能力。

### 决策

- 所有一次性 LLM Diagnoser Spec 可选声明 `image_vars: list[str]`；缺失等价于空列表。
- 每项必须是当前 runtime Projector 注册的简单图片根变量；拒绝空值、重复、`*`、点路径和数组表达式。
- 图片变量与普通 Prompt 变量分开注册、校验和解析，图片变量不能通过 `{{...}}` 渲染进 Prompt。
- 变量值必须是 `list[str]`；runtime 不声明变量或值类型错误时整次诊断 FAILED。
- 空列表使用纯文本请求；非空列表使用多模态请求。模型拒绝多模态或取图失败沿用 Judge unavailable 的
  FAILED 路径，不进行纯文本降级重试。
- Builtin、Custom、正式 Task 和 LLM Trial 共用同一输入解析和 Judge 调用入口。
- Execution evidence 只增加 `multimodal`、`image_count` 和去重后的 `image_hosts`，不保存 URL、path、
  query 或 fragment；现有 `agent_run.rendered_prompt` 行为保持不变。

### 保持不变

`required_vars` 仍只控制缺失文本事实的豁免；`image_vars` 是可选附件，不参与豁免。模型连接不新增
`supports_vision` 字段，前端第一版不增加配置控件。

### 影响与风险

旧 Spec 无需迁移。声明图片变量的 Spec 在不支持该变量的 runtime 上会明确失败；不限图片数量可能触发
模型上下文或网关限制。

### 验证

覆盖 Spec 校验、runtime 不支持、变量类型错误、空图片纯文本、多图请求、Builtin/Custom 共用行为、
Trial 与正式执行一致，以及 evidence 不含完整 URL。

### 实施说明

已实施：公共一次性 LLM Executor 校验并解析 `image_vars`，Trial 直接复用 Custom Executor，Execution
evidence 只保留图片数量、host 和多模态标记。
