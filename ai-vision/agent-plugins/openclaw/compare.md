# hooks.jsonl vs hooks_issue.jsonl 差异分析

## 一、Hook 类型对比

| Hook 类型 | `hooks.jsonl` | `hooks_issue.jsonl` | 差异说明 |
|---|:---:|:---:|---|
| `plugin:init` | ✅ (6) | ✅ (27) | 两者都有，issue 版本数量更多 |
| `gateway_start` | ❌ | ✅ (1) | **issue 独有**，网关启动事件 |
| `message_received` | ✅ (6) | ✅ (4) | 两者都有，结构一致 |
| `before_model_resolve` | ❌ | ✅ (4) | **issue 独有**，模型解析前 |
| `before_agent_start` | ✅ (32) | ✅ (8) | 两者都有，但结构有差异 |
| `before_prompt_build` | ❌ | ✅ (4) | **issue 独有**，prompt 构建前 |
| `llm_input` | ❌ | ✅ (4) | **issue 独有**，LLM 输入 |
| `llm_output` | ❌ | ✅ (4) | **issue 独有**，LLM 输出 |
| `before_message_write` | ❌ | ✅ (12) | **issue 独有**，消息写入前 |
| `before_tool_call` | ✅ (17) | ✅ (2) | 两者都有，但结构有差异 |
| `after_tool_call` | ❌ | ✅ (2) | **issue 独有**，工具调用后 |
| `tool_result_persist` | ✅ (17) | ✅ (2) | 两者都有，结构一致 |
| `agent_end` | ✅ (32) | ✅ (4) | 两者都有，ctx 有差异 |

> **hooks_issue.jsonl 新增了 7 种 hook 类型**：`gateway_start`、`before_model_resolve`、`before_prompt_build`、`llm_input`、`llm_output`、`before_message_write`、`after_tool_call`

## 二、共有 Hook 的 Event 结构差异

| Hook 类型 | `hooks.jsonl` event keys | `hooks_issue.jsonl` event keys | 差异 |
|---|---|---|---|
| `before_agent_start` | `messages`, `prompt` | `prompt`（首次可能无 messages） | issue 版首次调用可能只有 `prompt` |
| `before_tool_call` | `params`, `toolName` | `params`, `runId`, `toolCallId`, `toolName` | issue 版**新增** `runId`、`toolCallId` |
| `agent_end` | `durationMs`, `messages`, `success` | 同左 | **一致** |
| `tool_result_persist` | `isSynthetic`, `message`, `toolCallId`, `toolName` | 同左 | **一致** |
| `message_received` | `content`, `from`, `metadata` | 同左 | **一致** |

## 三、共有 Hook 的 Ctx 结构差异

| Hook 类型 | `hooks.jsonl` ctx keys | `hooks_issue.jsonl` ctx keys | 差异 |
|---|---|---|---|
| `before_agent_start` | `agentId`, `messageProvider`, `sessionKey`, `workspaceDir` | `agentId`, `channelId`, `messageProvider`, `sessionId`, `sessionKey`, `trigger`, `workspaceDir` | issue 版**新增** `channelId`、`sessionId`、`trigger` |
| `agent_end` | `agentId`, `messageProvider`, `sessionKey`, `workspaceDir` | `agentId`, `channelId`, `messageProvider`, `sessionId`, `sessionKey`, `trigger`, `workspaceDir` | 同上 |
| `before_tool_call` | `agentId`, `sessionKey`, `toolName` | `agentId`, `runId`, `sessionId`, `sessionKey`, `toolCallId`, `toolName` | issue 版**新增** `runId`、`sessionId`、`toolCallId` |
| `tool_result_persist` | `agentId`, `sessionKey`, `toolCallId`, `toolName` | 同左 | **一致** |
| `message_received` | `channelId` | 同左 | **一致** |

## 四、Issue 独有 Hook 的 Event/Ctx 结构

| Hook 类型 | Event keys | Ctx keys |
|---|---|---|
| **`gateway_start`** | `port` | `port` |
| **`before_model_resolve`** | `prompt` | `agentId`, `channelId`, `messageProvider`, `sessionId`, `sessionKey`, `trigger`, `workspaceDir` |
| **`before_prompt_build`** | `messages`, `prompt` | 同上 |
| **`llm_input`** | `historyMessages`, `imagesCount`, `model`, `prompt`, `provider`, `runId`, `sessionId`, `systemPrompt` | 同上 |
| **`llm_output`** | `assistantTexts`, `lastAssistant`, `model`, `provider`, `runId`, `sessionId`, `usage` | 同上 |
| **`before_message_write`** | `message` | `agentId`, `sessionKey` |
| **`after_tool_call`** | `durationMs`, `params`, `result`, `runId`, `toolCallId`, `toolName` | `agentId`, `runId`, `sessionId`, `sessionKey`, `toolCallId`, `toolName` |

## 五、核心差异总结

- **`hooks_issue.jsonl` 是更完整的版本**，覆盖了 Agent 完整生命周期：消息接收 → 模型解析 → prompt 构建 → LLM 输入/输出 → 工具调用前后 → 消息写入 → Agent 结束
- **`hooks.jsonl` 是精简版本**，只覆盖了核心节点：Agent 开始/结束、工具调用、消息接收
- **Ctx 上下文更丰富**：issue 版在大多数 hook 中增加了 `sessionId`、`channelId`、`trigger` 等字段，便于链路追踪和来源识别
- **工具调用链路更完整**：issue 版新增了 `after_tool_call` hook，并在 `before_tool_call` 中增加了 `runId`、`toolCallId`，形成完整的调用追踪闭环
- **LLM 可观测性**：issue 版新增 `llm_input`/`llm_output` 两个 hook，包含模型、provider、usage 等关键指标，支持 LLM 调用的监控和计费
