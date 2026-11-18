# OpenClaw Hooks 研究文档

> 基于 OpenClaw 源码 `src/plugins/types.ts` 中的 `PluginHookName` 类型定义分析。

## 概述

OpenClaw 提供了 **26 个 Typed Plugin Hook** + **多个 Internal Hook**，覆盖了 Agent 的完整生命周期。

- **Typed Hook**：通过 `api.on(hookName, handler, opts?)` 注册，有完整类型推导，支持返回值修改行为
- **Internal Hook**：通过 `api.registerHook(events, handler, opts?)` 注册，使用 `type:action` 格式的事件名，payload 为 `{ type, action, context, sessionKey, timestamp, messages }`

## Hook 注册方式

```typescript
// Typed Hook（推荐）— 有完整的类型推导
api.on('before_agent_start', (event, ctx) => {
  // event: PluginHookBeforeAgentStartEvent
  // ctx: PluginHookAgentContext
  return { modelOverride: 'gpt-4' };
}, { priority: 90 });

// Internal Hook — 用于自定义事件名
api.registerHook(
  ['command:new', 'command:reset'],
  async (event) => { /* ... */ },
  { name: 'my-hook', description: '...' }
);
```

## Hook 生命周期流程图

```
┌─────────────────────────────────────────────────────────────────┐
│                     Gateway Lifecycle                            │
│  gateway_start ──────────────────────────────────── gateway_stop │
└─────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────┐
│                     Session Lifecycle                            │
│  session_start ─────────────────────────────────── session_end  │
└─────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────┐
│                  Message Processing Pipeline                     │
│  inbound_claim → message_received                               │
│                       │                                          │
│                       ▼                                          │
│              before_model_resolve                                │
│                       │                                          │
│                       ▼                                          │
│              before_prompt_build                                 │
│                       │                                          │
│                       ▼                                          │
│              before_agent_start                                  │
│                       │                                          │
│                       ▼                                          │
│  ┌──────────── Agent Turn Loop ────────────┐                    │
│  │  llm_input                              │                    │
│  │     │                                   │                    │
│  │     ▼                                   │                    │
│  │  llm_output                             │                    │
│  │     │                                   │                    │
│  │     ▼                                   │                    │
│  │  before_tool_call → [tool exec]         │                    │
│  │     │                                   │                    │
│  │     ▼                                   │                    │
│  │  after_tool_call                        │                    │
│  │     │                                   │                    │
│  │     ▼                                   │                    │
│  │  tool_result_persist                    │                    │
│  │     │                                   │                    │
│  │     ▼                                   │                    │
│  │  before_message_write                   │                    │
│  │     │                                   │                    │
│  │     └──── (loop if more tool calls) ────┘                    │
│  │                                                              │
│  └──────────────────────────────────────────                    │
│                       │                                          │
│                       ▼                                          │
│                  agent_end                                       │
│                       │                                          │
│                       ▼                                          │
│  message_sending → message_sent                                 │
│                                                                  │
│  ┌── Compaction (when context too long) ──┐                     │
│  │  before_compaction → after_compaction   │                    │
│  └────────────────────────────────────────┘                     │
│                                                                  │
│  ┌── Reset (/new or /reset) ──┐                                 │
│  │  before_reset               │                                │
│  └─────────────────────────────┘                                │
│                                                                  │
│  ┌── Subagent Lifecycle ──────────────────┐                     │
│  │  subagent_spawning                      │                    │
│  │     → subagent_delivery_target          │                    │
│  │     → subagent_spawned                  │                    │
│  │     → subagent_ended                    │                    │
│  └─────────────────────────────────────────┘                    │
└─────────────────────────────────────────────────────────────────┘
```

## Hook 分类总览

| 分类 | Hook 名称 | 可返回结果 | 文档 |
|------|-----------|-----------|------|
| **Agent 生命周期** | `before_model_resolve` | ✅ `modelOverride`, `providerOverride` | [agent-lifecycle.md](./agent-lifecycle.md) |
| | `before_prompt_build` | ✅ `systemPrompt`, `prependContext` 等 | [agent-lifecycle.md](./agent-lifecycle.md) |
| | `before_agent_start` | ✅ 合并了上面两者 (legacy) | [agent-lifecycle.md](./agent-lifecycle.md) |
| | `llm_input` | ❌ 仅观测 | [agent-lifecycle.md](./agent-lifecycle.md) |
| | `llm_output` | ❌ 仅观测 | [agent-lifecycle.md](./agent-lifecycle.md) |
| | `agent_end` | ❌ 仅观测 | [agent-lifecycle.md](./agent-lifecycle.md) |
| **Session 管理** | `session_start` | ❌ 仅观测 | [session-compaction.md](./session-compaction.md) |
| | `session_end` | ❌ 仅观测 | [session-compaction.md](./session-compaction.md) |
| | `before_compaction` | ❌ 仅观测 | [session-compaction.md](./session-compaction.md) |
| | `after_compaction` | ❌ 仅观测 | [session-compaction.md](./session-compaction.md) |
| | `before_reset` | ❌ 仅观测 | [session-compaction.md](./session-compaction.md) |
| **Tool 调用** | `before_tool_call` | ✅ `params`, `block`, `blockReason` | [tool-hooks.md](./tool-hooks.md) |
| | `after_tool_call` | ❌ 仅观测 | [tool-hooks.md](./tool-hooks.md) |
| | `tool_result_persist` | ✅ `message` (修改持久化消息) | [tool-hooks.md](./tool-hooks.md) |
| | `before_message_write` | ✅ `block`, `message` | [tool-hooks.md](./tool-hooks.md) |
| **消息处理** | `inbound_claim` | ✅ `handled` | [message-hooks.md](./message-hooks.md) |
| | `message_received` | ❌ 仅观测 | [message-hooks.md](./message-hooks.md) |
| | `message_sending` | ✅ `content`, `cancel` | [message-hooks.md](./message-hooks.md) |
| | `message_sent` | ❌ 仅观测 | [message-hooks.md](./message-hooks.md) |
| **Subagent** | `subagent_spawning` | ✅ `status`, `error` | [subagent-hooks.md](./subagent-hooks.md) |
| | `subagent_delivery_target` | ✅ `origin` | [subagent-hooks.md](./subagent-hooks.md) |
| | `subagent_spawned` | ❌ 仅观测 | [subagent-hooks.md](./subagent-hooks.md) |
| | `subagent_ended` | ❌ 仅观测 | [subagent-hooks.md](./subagent-hooks.md) |
| **Gateway** | `gateway_start` | ❌ 仅观测 | [gateway-hooks.md](./gateway-hooks.md) |
| | `gateway_stop` | ❌ 仅观测 | [gateway-hooks.md](./gateway-hooks.md) |

## 共享 Context 类型

### PluginHookAgentContext
用于所有 Agent 生命周期 hooks 的上下文。

```typescript
type PluginHookAgentContext = {
  agentId?: string;          // Agent 标识符
  sessionKey?: string;       // 会话唯一键
  sessionId?: string;        // 会话 UUID
  workspaceDir?: string;     // 工作目录
  messageProvider?: string;  // 消息提供者
  trigger?: string;          // 触发来源: "user" | "heartbeat" | "cron" | "memory"
  channelId?: string;        // 渠道: "telegram" | "discord" | "whatsapp" 等
};
```

### PluginHookToolContext
用于 Tool 相关 hooks 的上下文。

```typescript
type PluginHookToolContext = {
  agentId?: string;
  sessionKey?: string;
  sessionId?: string;       // 临时会话 UUID（/new 和 /reset 后重新生成）
  runId?: string;            // 本次 agent 调用的稳定运行标识
  toolName: string;          // 工具名称
  toolCallId?: string;       // Provider 特定的工具调用 ID
};
```

### PluginHookMessageContext
用于消息相关 hooks 的上下文。

```typescript
type PluginHookMessageContext = {
  channelId: string;
  accountId?: string;
  conversationId?: string;
};
```

### PluginHookSessionContext
用于 Session 相关 hooks 的上下文。

```typescript
type PluginHookSessionContext = {
  agentId?: string;
  sessionId: string;
  sessionKey?: string;
};
```

### PluginHookSubagentContext
用于 Subagent 相关 hooks 的上下文。

```typescript
type PluginHookSubagentContext = {
  runId?: string;
  childSessionKey?: string;
  requesterSessionKey?: string;
};
```

### PluginHookGatewayContext
用于 Gateway 相关 hooks 的上下文。

```typescript
type PluginHookGatewayContext = {
  port?: number;
};
```

## Internal Hook 总览（`api.registerHook()`）

通过 `api.registerHook()` 注册，使用 `type:action` 格式的事件名。

| Hook 名称 | 触发时机 | 文档 |
|-----------|----------|------|
| **Gateway** | | |
| `gateway:startup` | Gateway 启动流程中（channels 启动后） | [gateway-hooks.md](./gateway-hooks.md) |
| **Agent** | | |
| `agent:bootstrap` | bootstrap 文件注入前，可 mutate `context.bootstrapFiles` | [gateway-hooks.md](./gateway-hooks.md) |
| **Command** | | |
| `command` | 所有命令事件通用监听 | [gateway-hooks.md](./gateway-hooks.md) |
| `command:new` | 用户执行 `/new` | [gateway-hooks.md](./gateway-hooks.md) |
| `command:reset` | 用户执行 `/reset` | [gateway-hooks.md](./gateway-hooks.md) |
| `command:stop` | 用户执行 `/stop` | [gateway-hooks.md](./gateway-hooks.md) |
| **Message** | | |
| `message` | 所有消息事件通用监听 | [message-hooks.md](./message-hooks.md) |
| `message:received` | 消息收到早期（媒体未处理，可能含原始占位符） | [message-hooks.md](./message-hooks.md) |
| `message:transcribed` | 音频转录完成后，`transcript` 字段可用 | [message-hooks.md](./message-hooks.md) |
| `message:preprocessed` | 所有媒体/链接理解完成，Agent 看到前的最终状态 | [message-hooks.md](./message-hooks.md) |
| `message:sent` | 出站消息成功发送后 | [message-hooks.md](./message-hooks.md) |
| **Session** | | |
| `session:compact:before` | 上下文压缩之前（internal hook 格式） | [session-compaction.md](./session-compaction.md) |
| `session:compact:after` | 上下文压缩完成后（internal hook 格式） | [session-compaction.md](./session-compaction.md) |

### Internal Hook Payload 格式

```typescript
{
  type: string;       // 事件类型，如 "message", "command", "session", "agent", "gateway"
  action: string;     // 事件动作，如 "received", "new", "compact:before", "bootstrap"
  context?: unknown;  // 事件上下文（各 hook 不同）
  sessionKey?: string;
  timestamp?: Date;
  messages?: unknown[];
}
```

## 源码参考

- **Hook 类型定义**: `src/plugins/types.ts`
- **Hook 注册系统**: `src/plugins/registry.ts`
- **Internal Hook 实现**: `src/hooks/internal-hooks.ts`
- **Hook CLI**: `src/cli/hooks-cli.ts`
- **官方文档**: `docs/automation/hooks.md`
