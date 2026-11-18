---
object_key: llm-judge
topic: multimodal-message
title: LLM Judge 多模态消息
---

# LLM Judge 多模态消息

## llm-judge-multimodal-message@20260826-1714

**以 OpenAI 图片附件格式发送 Judge 请求**

```yaml
record_id: llm-judge/multimodal-message@20260826-1714
decision_status: accepted
implementation_status: implemented
proposed_at: 2026-08-26T17:14+08:00
decided_at: 2026-08-26T17:14+08:00
implemented_at: 2026-08-26T17:32+08:00
supersedes: []
related_records:
  - executor/llm-input-attachments@20260826-1714
affected_objects:
  - executor
canonical_doc: docs/diagnoser/README.md
```

### 背景

LLMJudge 的 user message content 当前固定为字符串，无法携带 Executor 已解析的图片附件。

### 决策

- `judge` 接受已校验的可选图片 URL 列表。
- 无图片时保持现有字符串 user content；有图片时 user content 为 OpenAI 兼容 parts 数组，首项是
  `{"type": "text", "text": "## 评判任务\n..."}`，随后每张图片追加
  `{"type": "image_url", "image_url": {"url": "..."}}`。
- system message、Metric JSON 协议、temperature、thinking 参数、超时和固定两次重试保持不变。
- 上游对图片格式、访问或模型能力的拒绝按现有 JudgeUnavailable 处理。

### 保持不变

LLMJudge 不负责发现、下载、过滤或改写图片 URL，也不感知具体业务和诊断器。现有完整失败响应日志
策略本次不调整。

### 影响与风险

不同 OpenAI 兼容服务对远程图片 URL 支持可能不同；失败将明确反映为诊断 FAILED，不静默改变评判输入。

### 验证

覆盖纯文本 payload、多图片 payload、system 协议不变、响应解析和上游错误路径。

### 实施说明

已实施：LLMJudge 在非空图片列表时发送 text + image_url parts，空列表继续使用原纯文本消息格式。
