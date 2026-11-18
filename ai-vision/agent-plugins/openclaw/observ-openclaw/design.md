## observ-openclaw Hooks 设计文档

### 整体架构

observ-openclaw 是一个 OpenClaw 插件，通过注册 agent 生命周期 hooks，将 agent 运行时事件转化为 OpenTelemetry 遥测数据（Spans、Metrics、Events）。

#### 三层状态架构

```
SessionState (轻量级，跨 turn 存活，无 span)
  └── TurnState (每轮用户消息→回复，拥有 root span + agent span)
        ├── LlmRunState (每次 LLM 调用，支持重试/多次调用)
        └── ToolRunState (每次工具调用)
```

#### Span 树结构

```
openclaw.chat (root span — requestSpan)
  └── agent.{agentId} (agent span — agentSpan)
        ├── llm_request:{model} (LLM span — llmRun.span)
        │     └── tool_call:{toolName} (tool span — toolRun.span, 如果有 parentRunKey)
        └── tool_call:{toolName} (tool span — 无 LLM 父级时挂在 agent 下)
```

#### Turn 生命周期状态机

```
active ──(agent_end)──▶ awaiting_llm_output ──(all runs done / 5s timeout)──▶ finalized
```

---

### 全局注册表

| 注册表 | 类型 | 用途 |
|--------|------|------|
| `sessionMap` | `Map<string, SessionState>` | sessionKey → 轻量级会话状态 |
| `turnMap` | `Map<string, TurnState>` | turnId → 每轮状态（拥有 spans） |
| `runRegistry` | `Map<string, LlmRunState>` | runKey → LLM run 状态（全局，llm_output 可直接按 runKey 查找） |

---

### Hooks 触发顺序与数据流

```
before_model_resolve (1次)
  → before_agent_start (第1次, messages=0)
    → before_prompt_build (1次, 带 messages)
      → before_agent_start (第2次, 带 messages, 幂等)
        → llm_input
          → before_tool_call
            → tool_result_persist
              → after_tool_call
          → llm_output
        → agent_end
```

---

### 各 Hook 详细说明

#### 1. `before_model_resolve` — Turn 创建锚点

**触发时机**：每轮恰好触发一次，是 turn 的创建点。

**读取的 event 数据**：
| 字段 | 类型 | 用途 |
|------|------|------|
| `sessionKey` | `string` | 会话标识（event 或 ctx） |
| `prompt` | `string` | 用户输入的 prompt |

**执行的操作**：
- 创建或复用 `SessionState`（首次创建时记录 `agent.session.count` metric）
- 创建新的 `TurnState`，包含 `requestSpan`（root span `openclaw.chat`）
- 设置 `session.activeTurnId` 指向新 turn
- 发射 `user_prompt` event

**写入 span 的属性**：
| 属性 | 值来源 |
|------|--------|
| `gen_ai.conversation_id` | sessionKey |
| `gen_ai.input_messages` | prompt（受 config 控制是否 redact） |
| `openclaw.prompt.length` | prompt.length |
| `ant.username` | resolveUsername() |

---

#### 2. `before_prompt_build` — 填充 assistant 计数 + system 快照

**触发时机**：prompt 构建前，带完整 messages 数组。

**读取的 event 数据**：
| 字段 | 类型 | 用途 |
|------|------|------|
| `sessionKey` | `string` | 会话标识 |
| `messages` | `unknown[]` | 完整消息历史 |

**执行的操作**：
- 统计 `assistant` 角色消息数量，更新 `turn.initialAssistantMessageCount`（取 MAX）
- 提取 `system` 角色消息内容，存入 `turn.lastSystemInstructions`

**不创建 span**，仅更新 turn 状态。

---

#### 3. `before_agent_start` — 幂等 Agent Span 创建

**触发时机**：可能触发两次（第一次无 messages，第二次有 messages），幂等处理。

**读取的 event 数据**：
| 字段 | 类型 | 用途 |
|------|------|------|
| `sessionKey` | `string` | 会话标识 |
| `agentId` | `string` | agent 标识 |
| `model` | `string` | 模型名称 |
| `messages` / `historyMessages` | `unknown[]` | 消息历史（可选） |

**执行的操作**：
- 更新 `initialAssistantMessageCount`（如有 messages）
- 幂等创建 `agentSpan`（`agent.{agentId}`），作为 `requestSpan` 的子 span
- 设置 `turn.agentContext`

**写入 span 的属性**：
| 属性 | 值来源 |
|------|--------|
| `agentic.turn_id` | sessionKey |
| `gen_ai.agent.name` | agentId |
| `openclaw.agent.model` | model |
| `ant.username` | resolveUsername() |

---

#### 4. `llm_input` — LLM Run 创建

**触发时机**：每次 LLM 调用前。

**读取的 event 数据**：
| 字段 | 类型 | 用途 |
|------|------|------|
| `runId` | `string` | 框架提供的 run 标识（可能为空） |
| `sessionId` | `string` | 会话 ID |
| `provider` | `string` | LLM 提供商 |
| `model` | `string` | 模型名称 |
| `prompt` | `string` | 用户 prompt |
| `systemPrompt` | `string` | system prompt |
| `historyMessages` | `unknown[]` | 历史消息 |
| `imagesCount` | `number` | 图片数量 |

**执行的操作**：
- 生成 `runKey`：有 `runId` 用真实值，否则生成 synthetic key
- 创建 `LlmRunState`，注册到 `turn.llmRuns` 和全局 `runRegistry`
- 创建 LLM span（`llm_request:{model}`），作为 agent span 的子 span
- 存储 `promptSnapshot` 和 `systemSnapshot` 到 run 级别

**写入 span 的属性**：
| 属性 | 值来源 |
|------|--------|
| `gen_ai.request.model` | model |
| `gen_ai.provider.name` | provider |
| `llm.run_id` | runId |
| `llm.session_id` | sessionId |
| `llm.history_messages` | historyMessages.length |
| `llm.images_count` | imagesCount |
| `gen_ai.input_messages` | prompt（受 config 控制） |
| `gen_ai.system_instructions` | systemContent（受 config 控制） |

**关键状态字段**：
| 字段 | 说明 |
|------|------|
| `hasFrameworkRunId` | `!!runId`，区分真实 runId 和 synthetic key |
| `createdAt` | `Date.now()`，用于 FIFO 排序 |

---

#### 5. `llm_output` — LLM Run 完成

**触发时机**：LLM 调用返回后。

**读取的 event 数据**：
| 字段 | 类型 | 用途 |
|------|------|------|
| `runId` | `string` | run 标识 |
| `sessionId` | `string` | 会话 ID |
| `provider` | `string` | 提供商 |
| `model` | `string` | 模型名称 |
| `assistantTexts` | `string[]` | 模型输出文本 |
| `usage` | `Record<string, number>` | token 用量 |
| `lastAssistant` | `Record<string, unknown>` | 最后一条 assistant 消息 |
| `error` | `string` | 错误信息 |

**Run 查找策略（三级 fallback）**：

```
1. runId → runRegistry 直接查找（精确匹配）
2. 无 runId → findTurnWithActiveRuns()
   2a. 优先当前 active turn（有 synthetic active run）
   2b. fallback 到最新的非 finalized turn（有 synthetic active run）
3. 在选中的 turn 内，选 createdAt 最小的 synthetic active run（FIFO）
4. 如果 turn 内无任何 run → 创建 fallback LLM span + run
```

**关键隔离规则**：
- 有 `runId` 的 output → 只通过 `runRegistry` 精确匹配
- 无 `runId` 的 output → 只匹配 `hasFrameworkRunId === false` 的 synthetic run
- 两条路径完全隔离，不会互相干扰

**写入 span 的属性**：
| 属性 | 值来源 |
|------|--------|
| `gen_ai.response.model` | model |
| `gen_ai.usage.input_tokens` | usage.input |
| `gen_ai.usage.output_tokens` | usage.output |
| `gen_ai.usage.total_tokens` | usage.total |
| `gen_ai.output_messages` | assistantTexts（受 config 控制） |

**发射的 event**：
- 成功：`api_request` event（含 token 用量、prompt/output/system 快照）
- 失败：`api_error` event

**记录的 metric**：
- `agent.token.usage`（按 input/output/cache_read/cache_creation 分类型）

**完成后**：结束 LLM span，从 `runRegistry` 清理，尝试 `tryFinalizeTurn()`。

---

#### 6. `before_tool_call` — Tool Run 创建

**触发时机**：工具调用前。

**读取的 event 数据**：
| 字段 | 类型 | 用途 |
|------|------|------|
| `toolName` | `string` | 工具名称 |
| `toolCallId` | `string` | 工具调用 ID |
| `params` | `Record<string, unknown>` | 工具参数 |
| `sessionKey` | `string` | 会话标识（从 ctx） |

**执行的操作**：
- 查找当前 active turn
- 查找最近的 active LLM run 作为父级（`parentRunKey`）
- 创建 tool span（`tool_call:{toolName}`），挂在 LLM span 或 agent span 下
- 创建 `ToolRunState`，注册到 `turn.toolRuns`

**写入 span 的属性**：
| 属性 | 值来源 |
|------|--------|
| `code.tool.name` | toolName |
| `gen_ai.tool.name` | toolName |
| `gen_ai.tool.call_id` | toolCallId |
| `tool.params` | JSON.stringify(params)（受 config 控制） |

---

#### 7. `after_tool_call` — Tool Run 完成

**触发时机**：工具调用完成后。

**读取的 event 数据**：
| 字段 | 类型 | 用途 |
|------|------|------|
| `toolName` | `string` | 工具名称 |
| `toolCallId` | `string` | 工具调用 ID |
| `durationMs` | `number` | 执行耗时 |
| `error` | `string` | 错误信息 |
| `sessionKey` | `string` | 会话标识 |

**Turn 查找策略**：
```
1. toolCallId → findTurnByToolCallId()（按 toolCallId 反查所有非 finalized turn）
2. fallback → getActiveTurn()（当前 active turn）
```

**Lazy creation（before_tool_call 未触发时）**：
```
1. 优先当前 active turn（current-first）
2. fallback 到最新的非 finalized turn（newest-fallback）
3. 创建 synthetic tool span + run
```

**写入 span 的属性**：
| 属性 | 值来源 |
|------|--------|
| `tool.duration_ms` | durationMs |
| `tool.error` | error |
| `tool.lazy_created` | true（仅 lazy creation 时） |

---

#### 8. `tool_result_persist` — 工具结果持久化

**触发时机**：工具结果写入消息历史时。

**读取的 event 数据**：
| 字段 | 类型 | 用途 |
|------|------|------|
| `toolName` | `string` | 工具名称 |
| `toolCallId` | `string` | 工具调用 ID |
| `result` | `string` | 工具执行结果 |
| `sessionKey` | `string` | 会话标识 |

**Turn 查找策略**：同 `after_tool_call`（先按 toolCallId 反查，再 fallback active turn）。

**写入 span 的属性**：
| 属性 | 值来源 |
|------|--------|
| `tool.result` | result（受 config 控制，截断 5000 字符） |

**发射的 event**：
- `tool_result` event（含 toolName、result 摘要）

---

#### 9. `agent_end` — Turn 状态转换（不直接 finalize）

**触发时机**：agent 处理完成时。

**读取的 event 数据**：
| 字段 | 类型 | 用途 |
|------|------|------|
| `sessionKey` | `string` | 会话标识 |
| `durationMs` | `number` | agent 执行耗时 |
| `success` | `boolean` | 是否成功 |
| `error` | `string` | 错误信息 |
| `messages` | `unknown[]` | 完整消息历史 |

**执行的操作**：
- 从 messages 中聚合 token 用量（跳过 `initialAssistantMessageCount` 之前的旧消息）
- 记录 `agent.active_time.total` metric
- 记录 fallback token/cost metrics（仅当无 llm_output 触发时）
- 结束所有 active tool runs
- 设置 agent span 和 request span 的属性（token、model、content）
- 存储 `agentEndSnapshot`
- **转换 phase 为 `awaiting_llm_output`**（不结束 span）
- 设置 5 秒 cleanup timer
- 尝试 `tryFinalizeTurn()`（如果所有 run 已完成则立即 finalize）

**写入 agent span 的属性**：
| 属性 | 值来源 |
|------|--------|
| `openclaw.agent.duration_ms` | durationMs |
| `gen_ai.usage.input_tokens` | 聚合的 totalInputTokens |
| `gen_ai.usage.output_tokens` | 聚合的 totalOutputTokens |
| `gen_ai.usage.total_tokens` | totalTokens |
| `gen_ai.response.model` | model |
| `openclaw.agent.success` | success |
| `gen_ai.input_messages` | 最后一条 user 消息 |
| `gen_ai.output_messages` | 最后一条 assistant 消息 |
| `gen_ai.system_instructions` | system 消息 |

**记录的 metrics**：
| Metric | 说明 |
|--------|------|
| `agent.active_time.total` | agent 执行时间（秒） |
| `agent.token.usage` | fallback token 用量（仅当 llm_output 未触发） |
| `agent.cost.usage` | 估算成本（基于模型定价） |

---

### Turn Finalization 机制

```
agent_end 触发
  ├── phase → awaiting_llm_output
  ├── 设置 5s cleanup timer
  └── tryFinalizeTurn()
        └── 如果所有 llmRuns + toolRuns 都完成 → finalizeTurn()

llm_output 触发（在 awaiting_llm_output 阶段）
  └── tryFinalizeTurn()
        └── 如果所有 llmRuns + toolRuns 都完成 → finalizeTurn()

5s timeout
  └── 强制结束所有 orphaned LLM runs → finalizeTurn()

finalizeTurn():
  1. 清除 cleanup timer
  2. 结束 agent span
  3. 结束 request span
  4. phase → finalized
  5. 从 turnMap 删除
  6. 清理 session.activeTurnId
```

---

### 辅助 Hook

#### `command:new` / `command:reset` / `command:stop`
- 记录 `openclaw.command.{action}` span，挂在当前 turn 的 request context 下

---

### 定时清理

每 60 秒执行一次：
- 清理超过 5 分钟的 stale turns（强制 finalize）
- 清理无 active turn 的 stale sessions

---

### 关键设计决策

1. **`before_model_resolve` 作为 turn 锚点**：每轮恰好触发一次，保证 turn 创建的确定性
2. **`before_agent_start` 幂等**：可能触发两次，只在首次创建 agent span
3. **`agent_end` 不直接 finalize**：转为 `awaiting_llm_output`，等待 llm_output 到达后再结束 span，确保 LLM span 完整
4. **`hasFrameworkRunId` 隔离**：no-runId 路径只匹配 synthetic run，不会偷走有真实 runId 的 run
5. **Current-first turn 选择**：fallback 路径优先当前 active turn，避免新旧 turn 之间的双向偏向
6. **Run 级快照**：`promptSnapshot` / `systemSnapshot` 存在 `LlmRunState` 上，确保多次 LLM 调用各自记录准确的 prompt
