# Subagent Hooks

> 这组 hooks 覆盖了子 Agent 的完整生命周期：创建、消息投递目标确定、启动完成和结束。

## 触发顺序

```
subagent_spawning → subagent_delivery_target → subagent_spawned → [子 Agent 执行] → subagent_ended
```

---

## 1. `subagent_spawning`

### 触发阶段
子 Agent 即将被创建时触发。可以返回状态信息，也可以返回错误阻止创建。

### 签名
```typescript
handler: (
  event: PluginHookSubagentSpawningEvent,
  ctx: PluginHookSubagentContext
) => PluginHookSubagentSpawningResult | void
```

### Event 参数类型
```typescript
type PluginHookSubagentSpawningEvent = {
  childSessionKey: string;     // 子 Agent 的会话键
  agentId: string;             // 子 Agent 的 ID
  label?: string;              // 子 Agent 的标签/描述
  mode: 'run' | 'session';    // 运行模式：单次运行 or 持久会话
  requester?: {                // 请求者信息
    channel?: string;          // 渠道
    accountId?: string;        // 账号 ID
    to?: string;               // 目标
    threadId?: string | number; // 线程 ID
  };
  threadRequested: boolean;    // 是否请求了线程绑定
};
```

### Context 类型
```typescript
type PluginHookSubagentContext = {
  runId?: string;
  childSessionKey?: string;
  requesterSessionKey?: string;
};
```

### 返回值类型
```typescript
type PluginHookSubagentSpawningResult =
  | {
      status: 'ok';
      threadBindingReady?: boolean;  // 线程绑定是否已就绪
    }
  | {
      status: 'error';
      error: string;                 // 错误信息，阻止子 Agent 创建
    };
```

### 示例
```typescript
api.on('subagent_spawning', (event, ctx) => {
  console.log(`Subagent spawning: ${event.agentId}, mode=${event.mode}`);
  
  // 限制并发子 Agent 数量
  if (activeSubagentCount >= MAX_SUBAGENTS) {
    return {
      status: 'error',
      error: `Too many concurrent subagents (max: ${MAX_SUBAGENTS})`,
    };
  }

  return { status: 'ok' };
});
```

### Event 示例数据
```json
{
  "childSessionKey": "agent:research:anthropic-subagent:uuid-123",
  "agentId": "research",
  "label": "Research Assistant",
  "mode": "run",
  "requester": {
    "channel": "discord",
    "accountId": "bot_456",
    "to": "channel_789",
    "threadId": "thread_001"
  },
  "threadRequested": true
}
```

---

## 2. `subagent_delivery_target`

### 触发阶段
确定子 Agent 完成后消息投递目标时触发。可以覆盖投递目标（如重定向到不同的渠道或线程）。

### 签名
```typescript
handler: (
  event: PluginHookSubagentDeliveryTargetEvent,
  ctx: PluginHookSubagentContext
) => PluginHookSubagentDeliveryTargetResult | void
```

### Event 参数类型
```typescript
type PluginHookSubagentDeliveryTargetEvent = {
  childSessionKey: string;          // 子 Agent 会话键
  requesterSessionKey: string;      // 请求者会话键
  requesterOrigin?: {               // 请求者的原始来源
    channel?: string;
    accountId?: string;
    to?: string;
    threadId?: string | number;
  };
  childRunId?: string;              // 子 Agent 运行 ID
  spawnMode?: 'run' | 'session';    // 运行模式
  expectsCompletionMessage: boolean; // 是否期望完成消息
};
```

### 返回值类型
```typescript
type PluginHookSubagentDeliveryTargetResult = {
  origin?: {                        // 覆盖投递目标
    channel?: string;
    accountId?: string;
    to?: string;
    threadId?: string | number;
  };
};
```

### 示例
```typescript
api.on('subagent_delivery_target', (event, ctx) => {
  // 将子 Agent 的结果重定向到特定线程
  if (event.requesterOrigin?.channel === 'discord') {
    return {
      origin: {
        ...event.requesterOrigin,
        threadId: 'results-thread-001',
      },
    };
  }
});
```

### Event 示例数据
```json
{
  "childSessionKey": "agent:research:anthropic-subagent:uuid-123",
  "requesterSessionKey": "agent:main:anthropic-user:{\"channel\":\"discord\"}",
  "requesterOrigin": {
    "channel": "discord",
    "accountId": "bot_456",
    "to": "channel_789",
    "threadId": "thread_001"
  },
  "childRunId": "run_sub_001",
  "spawnMode": "run",
  "expectsCompletionMessage": true
}
```

---

## 3. `subagent_spawned`

### 触发阶段
子 Agent 成功创建并开始运行**之后**触发。这是一个**纯观测 hook**。

### 签名
```typescript
handler: (
  event: PluginHookSubagentSpawnedEvent,
  ctx: PluginHookSubagentContext
) => void
```

### Event 参数类型
```typescript
// 继承自 PluginHookSubagentSpawnBase，额外增加 runId
type PluginHookSubagentSpawnedEvent = {
  childSessionKey: string;
  agentId: string;
  label?: string;
  mode: 'run' | 'session';
  requester?: {
    channel?: string;
    accountId?: string;
    to?: string;
    threadId?: string | number;
  };
  threadRequested: boolean;
  runId: string;              // 子 Agent 的运行 ID
};
```

### 示例
```typescript
api.on('subagent_spawned', (event, ctx) => {
  console.log(`Subagent spawned: ${event.agentId}, runId=${event.runId}`);
  console.log(`  Mode: ${event.mode}`);
  console.log(`  Session: ${event.childSessionKey}`);
});
```

### Event 示例数据
```json
{
  "childSessionKey": "agent:research:anthropic-subagent:uuid-123",
  "agentId": "research",
  "label": "Research Assistant",
  "mode": "run",
  "requester": {
    "channel": "discord",
    "accountId": "bot_456",
    "to": "channel_789"
  },
  "threadRequested": false,
  "runId": "run_sub_001"
}
```

---

## 4. `subagent_ended`

### 触发阶段
子 Agent 运行结束时触发（无论成功、失败、超时或被终止）。这是一个**纯观测 hook**。

### 签名
```typescript
handler: (
  event: PluginHookSubagentEndedEvent,
  ctx: PluginHookSubagentContext
) => void
```

### Event 参数类型
```typescript
type PluginHookSubagentEndedEvent = {
  targetSessionKey: string;                    // 子 Agent 会话键
  targetKind: 'subagent' | 'acp';             // 目标类型
  reason: string;                              // 结束原因
  sendFarewell?: boolean;                      // 是否发送告别消息
  accountId?: string;
  runId?: string;
  endedAt?: number;                            // 结束时间戳
  outcome?: 'ok' | 'error' | 'timeout' | 'killed' | 'reset' | 'deleted';
  error?: string;                              // 错误信息
};
```

### 示例
```typescript
api.on('subagent_ended', (event, ctx) => {
  const emoji = event.outcome === 'ok' ? '✅' : '❌';
  console.log(`${emoji} Subagent ended: ${event.targetSessionKey}`);
  console.log(`  Kind: ${event.targetKind}`);
  console.log(`  Outcome: ${event.outcome}`);
  console.log(`  Reason: ${event.reason}`);
  if (event.error) {
    console.error(`  Error: ${event.error}`);
  }
});
```

### Event 示例数据（成功）
```json
{
  "targetSessionKey": "agent:research:anthropic-subagent:uuid-123",
  "targetKind": "subagent",
  "reason": "completed",
  "sendFarewell": true,
  "runId": "run_sub_001",
  "endedAt": 1710900060000,
  "outcome": "ok"
}
```

### Event 示例数据（超时）
```json
{
  "targetSessionKey": "agent:research:anthropic-subagent:uuid-123",
  "targetKind": "subagent",
  "reason": "timeout",
  "sendFarewell": false,
  "runId": "run_sub_001",
  "endedAt": 1710900120000,
  "outcome": "timeout",
  "error": "Subagent execution exceeded 60s timeout"
}
```
