# Tool Hooks

> 这组 hooks 覆盖了工具调用的完整生命周期：调用前拦截、调用后观测、结果持久化和消息写入。

## 触发顺序

```
before_tool_call → [工具执行] → after_tool_call → tool_result_persist → before_message_write
```

---

## 1. `before_tool_call`

### 触发阶段
工具被实际执行**之前**触发。可以修改工具参数，也可以完全阻止工具执行。

### 签名
```typescript
handler: (
  event: PluginHookBeforeToolCallEvent,
  ctx: PluginHookToolContext
) => PluginHookBeforeToolCallResult | void
```

### Event 参数类型
```typescript
type PluginHookBeforeToolCallEvent = {
  toolName: string;                    // 工具名称
  params: Record<string, unknown>;     // 工具调用参数
  runId?: string;                      // 本次 agent 调用的运行 ID
  toolCallId?: string;                 // Provider 特定的工具调用 ID
};
```

### Context 类型
```typescript
type PluginHookToolContext = {
  agentId?: string;
  sessionKey?: string;
  sessionId?: string;       // 临时会话 UUID（/new 和 /reset 后重新生成）
  runId?: string;            // 本次 agent 调用的稳定运行标识
  toolName: string;
  toolCallId?: string;       // Provider 特定的工具调用 ID
};
```

### 返回值类型
```typescript
type PluginHookBeforeToolCallResult = {
  params?: Record<string, unknown>;  // 修改后的参数（替换原始参数）
  block?: boolean;                   // 是否阻止工具执行
  blockReason?: string;              // 阻止原因（会返回给 LLM）
};
```

### 示例
```typescript
api.on('before_tool_call', (event, ctx) => {
  // 阻止危险的 shell 命令
  if (event.toolName === 'shell' && event.params.command?.toString().includes('rm -rf')) {
    return {
      block: true,
      blockReason: '安全策略：禁止执行 rm -rf 命令',
    };
  }

  // 修改文件读取路径
  if (event.toolName === 'read_file' && event.params.path) {
    return {
      params: {
        ...event.params,
        path: event.params.path.toString().replace('/tmp/', '/safe/'),
      },
    };
  }
});
```

### Event 示例数据
```json
{
  "toolName": "shell",
  "params": {
    "command": "ls -la /home/user/project",
    "timeout": 30000
  },
  "runId": "run_abc123",
  "toolCallId": "toolu_01ABC"
}
```

### Context 示例数据
```json
{
  "agentId": "main",
  "sessionKey": "agent:main:anthropic-user:{\"channel\":\"web\"}",
  "sessionId": "sess_xyz789",
  "runId": "run_abc123",
  "toolName": "shell",
  "toolCallId": "toolu_01ABC"
}
```

---

## 2. `after_tool_call`

### 触发阶段
工具执行**完成之后**触发（无论成功或失败）。包含工具的执行结果和耗时。这是一个**纯观测 hook**。

### 签名
```typescript
handler: (
  event: PluginHookAfterToolCallEvent,
  ctx: PluginHookToolContext
) => void
```

### Event 参数类型
```typescript
type PluginHookAfterToolCallEvent = {
  toolName: string;                    // 工具名称
  params: Record<string, unknown>;     // 工具调用参数
  runId?: string;
  toolCallId?: string;
  result?: unknown;                    // 工具执行结果
  error?: string;                      // 错误信息（如果执行失败）
  durationMs?: number;                 // 执行耗时（毫秒）
};
```

### 示例
```typescript
api.on('after_tool_call', (event, ctx) => {
  const status = event.error ? '❌' : '✅';
  console.log(`${status} Tool ${event.toolName}: ${event.durationMs}ms`);
  if (event.error) {
    console.error(`  Error: ${event.error}`);
  }
});
```

### Event 示例数据
```json
{
  "toolName": "shell",
  "params": { "command": "ls -la" },
  "runId": "run_abc123",
  "toolCallId": "toolu_01ABC",
  "result": "total 48\ndrwxr-xr-x  12 user  staff  384 Mar 20 10:00 .\n...",
  "durationMs": 125
}
```

### 失败时的 Event 示例
```json
{
  "toolName": "shell",
  "params": { "command": "invalid_command" },
  "runId": "run_abc123",
  "toolCallId": "toolu_01ABC",
  "error": "Command not found: invalid_command",
  "durationMs": 50
}
```

---

## 3. `tool_result_persist`

### 触发阶段
工具结果即将被写入会话 transcript（JSONL 文件）**之前**触发。可以修改即将持久化的消息内容（如删除敏感信息）。这是一个**同步 hook**。

### 签名
```typescript
handler: (
  event: PluginHookToolResultPersistEvent,
  ctx: PluginHookToolResultPersistContext
) => PluginHookToolResultPersistResult | void
```

### Event 参数类型
```typescript
type PluginHookToolResultPersistEvent = {
  toolName?: string;
  toolCallId?: string;
  message: AgentMessage;     // 即将写入的 toolResult 消息
  isSynthetic?: boolean;     // 是否由 guard/repair 步骤合成的结果
};
```

### Context 类型
```typescript
type PluginHookToolResultPersistContext = {
  agentId?: string;
  sessionKey?: string;
  toolName?: string;
  toolCallId?: string;
};
```

### 返回值类型
```typescript
type PluginHookToolResultPersistResult = {
  message?: AgentMessage;    // 修改后的消息（替换原始消息）
};
```

### 示例
```typescript
api.on('tool_result_persist', (event, ctx) => {
  // 脱敏：移除工具结果中的敏感信息
  if (event.toolName === 'shell') {
    const msg = { ...event.message };
    if (typeof msg.content === 'string') {
      msg.content = msg.content.replace(/password=\S+/g, 'password=***');
    }
    return { message: msg };
  }
});
```

### Event 示例数据
```json
{
  "toolName": "read_file",
  "toolCallId": "toolu_01ABC",
  "message": {
    "role": "tool",
    "content": [{ "type": "text", "text": "file content here..." }],
    "tool_use_id": "toolu_01ABC"
  },
  "isSynthetic": false
}
```

---

## 4. `before_message_write`

### 触发阶段
任何消息即将被写入会话 JSONL 文件**之前**触发。可以阻止消息写入或修改消息内容。这是一个**同步 hook**。

### 签名
```typescript
handler: (
  event: PluginHookBeforeMessageWriteEvent,
  ctx: { agentId?: string; sessionKey?: string }
) => PluginHookBeforeMessageWriteResult | void
```

### Event 参数类型
```typescript
type PluginHookBeforeMessageWriteEvent = {
  message: AgentMessage;     // 即将写入的消息
  sessionKey?: string;
  agentId?: string;
};
```

### 返回值类型
```typescript
type PluginHookBeforeMessageWriteResult = {
  block?: boolean;           // 如果为 true，消息不会被写入 JSONL
  message?: AgentMessage;    // 修改后的消息（替换原始消息）
};
```

### 示例
```typescript
api.on('before_message_write', (event, ctx) => {
  // 阻止写入包含敏感信息的消息
  if (typeof event.message.content === 'string' && 
      event.message.content.includes('SECRET_KEY')) {
    return { block: true };
  }

  // 给所有消息添加时间戳元数据
  return {
    message: {
      ...event.message,
      metadata: {
        ...(event.message as Record<string, unknown>).metadata,
        writtenAt: new Date().toISOString(),
      },
    },
  };
});
```

### Event 示例数据
```json
{
  "message": {
    "role": "assistant",
    "content": [{ "type": "text", "text": "这是我的回复..." }],
    "model": "claude-3-sonnet"
  },
  "sessionKey": "agent:main:anthropic-user:{\"channel\":\"web\"}",
  "agentId": "main"
}
```
