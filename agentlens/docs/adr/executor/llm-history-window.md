---
object_key: executor
topic: llm-history-window
title: LLM Executor 历史窗口
---

# LLM Executor 历史窗口

## executor-llm-history-window@20261010-0350

**专项只消费同一实际来源的有界历史**

```yaml
record_id: executor/llm-history-window@20261010-0350
decision_status: accepted
implementation_status: not_started
proposed_at: 2026-10-10T03:50+08:00
decided_at: 2026-10-10T09:06+08:00
implemented_at: null
supersedes: []
related_records:
  - diagnoser/specialist-material-policy@20261010-0350
  - projection/runtime-facts@20261010-0350
affected_objects:
  - diagnoser
  - projection
  - llm-judge
canonical_doc: docs/diagnoser/README.md
```

### 背景

多轮专项需要明确历史来源与完整性边界，公共 Executor 不应统一注入或截取不属于当前材料政策的历史。

### 决策

`recorded_conversation` 从同一真实 SDK 容器中读取当前请求之前的原 index、order 与 source，不按文本去重，
不跨 span 拼会话；至少需要一条非空 user/assistant 消息。缺失、遮蔽、来源不支持或超界均零 Judge SKIP。
历史最多 40 条消息、单条最多 8 KiB，包含 request/final/policy 的 bundle 最多 96 KiB；超界明确跳过，
不截取末尾后继续评判。历史不能替代当前请求；查询、解码或投影故障保持 FAILED。CUSTOM 与 Core 使用各自材料政策。

### 保持不变

旧记录中的已保存历史不重算或截短；原始 Trace、通用 Judge 与 attempt hooks 保持。

### 影响与风险

有界历史只证明本次模型可见材料，不证明完整会话已经采集。

### 验证

验证来源与顺序、重复文本、跨 span、空值、40/41 条边界、字节和 bundle 上限、CUSTOM/Core 独立材料政策，
以及历史读取不依赖当前 Executor。

### 实施说明

目标合同见专项材料政策；实施状态由记录元数据与当前行为文档维护。
