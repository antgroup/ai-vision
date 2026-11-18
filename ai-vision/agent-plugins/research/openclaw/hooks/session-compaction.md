# Session & Compaction Hooks

> 这组 hooks 覆盖了会话的创建、结束、上下文压缩和重置。

## 触发顺序

```
session_start → [多轮对话] → before_compaction → after_compaction → ... → before_reset → session_end
```

---

## 1. `session_start`

### 触发阶段
新会话开始时触发。可能是全新会话，也可能是从之前的会话恢复。这是一个**纯观测 hook**。

### 签名
```typescript
handler: (
  event: PluginHookSessionStartEvent,
  ctx: PluginHookSessionContext
) => void
```

### Event 参数类型
```typescript
type PluginHookSessionStartEvent = {
  sessionId: string;       // 新会话的 UUID
  sessionKey?: string;     // 会话唯一键
  resumedFrom?: string;    // 如果是恢复的会话，原始会话 ID
};
```

### Context 类型
```typescript
type PluginHookSessionContext = {
  agentId?: string;
  sessionId: string;
  sessionKey?: string;
};
```

### 示例
```typescript
api.on('session_start', (event, ctx) => {
  console.log(`Session started: id=${event.sessionId}`);
  if (event.resumedFrom) {
    console.log(`  Resumed from: ${event.resumedFrom}`);
  }
  // 可用于初始化会话级别的状态、计数器等
}, { priority: 90 });
```

### Event 示例数据
```json
{
  "sessionId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "sessionKey": "agent:main:openai-user:{\"channel\":\"web\"}"
}
```

### 恢复会话的 Event 示例
```json
{
  "sessionId": "new-uuid-1234",
  "sessionKey": "agent:main:openai-user:{\"channel\":\"web\"}",
  "resumedFrom": "old-uuid-5678"
}
```

---

## 2. `session_end`

### 触发阶段
会话结束时触发。包含会话的统计信息。这是一个**纯观测 hook**。

### 签名
```typescript
handler: (
  event: PluginHookSessionEndEvent,
  ctx: PluginHookSessionContext
) => void
```

### Event 参数类型
```typescript
type PluginHookSessionEndEvent = {
  sessionId: string;       // 会话 UUID
  sessionKey?: string;     // 会话唯一键
  messageCount: number;    // 会话中的总消息数
  durationMs?: number;     // 会话持续时长（毫秒）
};
```

### 示例
```typescript
api.on('session_end', (event, ctx) => {
  console.log(`Session ended: id=${event.sessionId}`);
  console.log(`  Messages: ${event.messageCount}`);
  console.log(`  Duration: ${event.durationMs}ms`);
});
```

### Event 示例数据
```json
{
  "sessionId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "sessionKey": "agent:main:openai-user:{\"channel\":\"web\"}",
  "messageCount": 24,
  "durationMs": 180000
}
```

---

## 3. `before_compaction`

### 触发阶段
当会话上下文过长需要压缩时，在压缩 LLM 调用**之前**触发。此时消息尚未被截断。这是一个**纯观测 hook**。

### 签名
```typescript
handler: (
  event: PluginHookBeforeCompactionEvent,
  ctx: PluginHookAgentContext
) => void
```

### Event 参数类型
```typescript
type PluginHookBeforeCompactionEvent = {
  messageCount: number;       // 压缩前的总消息数
  compactingCount?: number;   // 将被送入压缩 LLM 的消息数（经过 history-limit 截断后）
  tokenCount?: number;        // 当前 token 数
  messages?: unknown[];       // 消息列表
  sessionFile?: string;       // 会话 JSONL 文件路径（所有消息已写入磁盘）
};
```

### 示例
```typescript
api.on('before_compaction', (event, ctx) => {
  console.log(`Compaction starting: ${event.messageCount} messages, ~${event.tokenCount} tokens`);
  console.log(`  Compacting ${event.compactingCount} messages`);
  
  // 可以异步读取 sessionFile 进行自定义处理
  if (event.sessionFile) {
    console.log(`  Session file: ${event.sessionFile}`);
  }
});
```

### Event 示例数据
```json
{
  "messageCount": 150,
  "compactingCount": 120,
  "tokenCount": 95000,
  "sessionFile": "/home/user/.openclaw/sessions/agent-main/abc123.jsonl"
}
```

---

## 4. `after_compaction`

### 触发阶段
上下文压缩完成**之后**触发。包含压缩后的统计信息。这是一个**纯观测 hook**。

### 签名
```typescript
handler: (
  event: PluginHookAfterCompactionEvent,
  ctx: PluginHookAgentContext
) => void
```

### Event 参数类型
```typescript
type PluginHookAfterCompactionEvent = {
  messageCount: number;      // 压缩后的消息数
  tokenCount?: number;       // 压缩后的 token 数
  compactedCount: number;    // 被压缩掉的消息数
  sessionFile?: string;      // 会话 JSONL 文件路径（保留了压缩前的所有消息）
};
```

### 示例
```typescript
api.on('after_compaction', (event, ctx) => {
  console.log(`Compaction done: ${event.compactedCount} messages compacted`);
  console.log(`  Remaining: ${event.messageCount} messages, ~${event.tokenCount} tokens`);
});
```

### Event 示例数据
```json
{
  "messageCount": 30,
  "tokenCount": 25000,
  "compactedCount": 120,
  "sessionFile": "/home/user/.openclaw/sessions/agent-main/abc123.jsonl"
}
```

---

## 5. `before_reset`

### 触发阶段
当用户执行 `/new` 或 `/reset` 命令清除会话时触发。在会话实际清除**之前**调用。这是一个**纯观测 hook**。

### 签名
```typescript
handler: (
  event: PluginHookBeforeResetEvent,
  ctx: PluginHookAgentContext
) => void
```

### Event 参数类型
```typescript
type PluginHookBeforeResetEvent = {
  sessionFile?: string;    // 会话 JSONL 文件路径
  messages?: unknown[];    // 当前会话的消息列表
  reason?: string;         // 重置原因
};
```

### 示例
```typescript
api.on('before_reset', (event, ctx) => {
  console.log(`Session reset: reason=${event.reason}`);
  console.log(`  Messages to be cleared: ${event.messages?.length || 0}`);
  
  // 可以在此备份会话数据
  if (event.sessionFile) {
    console.log(`  Backing up session file: ${event.sessionFile}`);
  }
});
```

### Event 示例数据
```json
{
  "sessionFile": "/home/user/.openclaw/sessions/agent-main/abc123.jsonl",
  "messages": [
    { "role": "user", "content": "你好" },
    { "role": "assistant", "content": "你好！" }
  ],
  "reason": "user_command"
}
```

---

## Internal Hook 系统中的 Session 事件

> 以下 hooks 通过 `api.registerHook()` 注册，是 internal hook runner 的事件格式。
> 与上面的 typed hooks（`before_compaction`、`after_compaction`）是**两套独立的触发机制**。

---

## 6. `session:compact:before`（Internal Hook）

### 触发阶段
上下文压缩**之前**触发，与 typed hook `before_compaction` 对应，但通过 internal hook runner 分发。

### 注册方式
```typescript
api.registerHook(
  'session:compact:before',
  async (event) => {
    const e = event as { type: string; action: string; context?: unknown };
    console.log('Compaction about to start', e.context);
  },
  { name: 'my-compact-before', description: '...' }
);
```

### Event 参数类型
```typescript
{
  type: 'session';
  action: 'compact:before';
  context?: {
    messageCount?: number;
    compactingCount?: number;
    tokenCount?: number;
  };
  sessionKey?: string;
  timestamp?: Date;
  messages?: unknown[];
}
```

### 与 typed hook `before_compaction` 的区别
| 特性 | `before_compaction` (typed) | `session:compact:before` (internal) |
|------|----------------------------|--------------------------------------|
| 注册方式 | `api.on()` | `api.registerHook()` |
| 类型安全 | ✅ 完整类型推导 | ❌ event 为 `unknown` |
| 触发机制 | Plugin hook runner | Internal hook runner |

---

## 7. `session:compact:after`（Internal Hook）

### 触发阶段
上下文压缩**完成后**触发，与 typed hook `after_compaction` 对应，但通过 internal hook runner 分发。

### 注册方式
```typescript
api.registerHook(
  'session:compact:after',
  async (event) => {
    const e = event as { type: string; action: string; context?: unknown };
    console.log('Compaction done', e.context);
  },
  { name: 'my-compact-after', description: '...' }
);
```

### Event 参数类型
```typescript
{
  type: 'session';
  action: 'compact:after';
  context?: {
    messageCount?: number;
    tokenCount?: number;
    compactedCount?: number;
  };
  sessionKey?: string;
  timestamp?: Date;
  messages?: unknown[];
}
```
