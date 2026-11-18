# Agent 生命周期 Hooks

> 这组 hooks 覆盖了 Agent 从模型选择到执行结束的完整生命周期。

## 触发顺序

```
before_model_resolve → before_prompt_build → before_agent_start → llm_input → llm_output → agent_end
```

---

## 1. `before_model_resolve`

### 触发阶段
Agent 运行的**最早阶段**，在模型和 Provider 确定之前触发。此时 session 消息尚不可用，仅有用户 prompt。

### 签名
```typescript
handler: (
  event: PluginHookBeforeModelResolveEvent,
  ctx: PluginHookAgentContext
) => PluginHookBeforeModelResolveResult | void
```

### Event 参数类型
```typescript
type PluginHookBeforeModelResolveEvent = {
  prompt: string;  // 用户本次输入的 prompt 文本
};
```

### Context 类型
```typescript
type PluginHookAgentContext = {
  agentId?: string;
  sessionKey?: string;
  sessionId?: string;
  workspaceDir?: string;
  messageProvider?: string;
  trigger?: string;       // "user" | "heartbeat" | "cron" | "memory"
  channelId?: string;     // "telegram" | "discord" 等
};
```

### 返回值类型
```typescript
type PluginHookBeforeModelResolveResult = {
  modelOverride?: string;     // 覆盖模型，如 "llama3.3:8b"
  providerOverride?: string;  // 覆盖 Provider，如 "ollama"
};
```

### 示例
```typescript
api.on('before_model_resolve', (event, ctx) => {
  // 根据 prompt 内容动态选择模型
  if (event.prompt.includes('翻译')) {
    return { modelOverride: 'gpt-4', providerOverride: 'openai' };
  }
  // 特定渠道使用轻量模型
  if (ctx.channelId === 'telegram') {
    return { modelOverride: 'claude-3-haiku' };
  }
  return undefined;
}, { priority: 90 });
```

### Event 示例数据
```json
{
  "prompt": "帮我写一个 Python 排序算法"
}
```

### Context 示例数据
```json
{
  "agentId": "main",
  "sessionKey": "agent:main:openai-user:{\"channel\":\"telegram\",\"peerId\":\"12345\"}",
  "sessionId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "workspaceDir": "/home/user/project",
  "messageProvider": "openai",
  "trigger": "user",
  "channelId": "telegram"
}
```

---

## 2. `before_prompt_build`

### 触发阶段
在模型确定之后、系统 prompt 构建之前触发。此时 session 消息已经准备好，可以注入自定义的系统 prompt 或上下文。

### 签名
```typescript
handler: (
  event: PluginHookBeforePromptBuildEvent,
  ctx: PluginHookAgentContext
) => PluginHookBeforePromptBuildResult | void
```

### Event 参数类型
```typescript
type PluginHookBeforePromptBuildEvent = {
  prompt: string;       // 用户 prompt
  messages: unknown[];  // 本次运行准备好的 session 消息列表
};
```

### 返回值类型
```typescript
type PluginHookBeforePromptBuildResult = {
  systemPrompt?: string;           // 完全覆盖系统 prompt
  prependContext?: string;         // 在用户消息前插入上下文（每轮都会消耗 token）
  prependSystemContext?: string;   // 追加到系统 prompt 前部（可被 provider 缓存）
  appendSystemContext?: string;    // 追加到系统 prompt 后部（可被 provider 缓存）
};
```

### 示例
```typescript
api.on('before_prompt_build', (event, ctx) => {
  return {
    prependSystemContext: '你是一个专业的代码审查助手。',
    prependContext: `当前工作目录: ${ctx.workspaceDir}`,
  };
});
```

### Event 示例数据
```json
{
  "prompt": "帮我 review 这段代码",
  "messages": [
    { "role": "user", "content": "你好" },
    { "role": "assistant", "content": "你好！有什么可以帮你的？" }
  ]
}
```

> **注意**: `before_prompt_build` 是 **Prompt Injection Hook**，受 `hooks.allowPromptInjection` 配置控制。当 `allowPromptInjection=false` 时，此 hook 会被完全阻止。

---

## 3. `before_agent_start`

### 触发阶段
Agent 开始执行之前触发。这是一个 **legacy 兼容 hook**，合并了 `before_model_resolve` 和 `before_prompt_build` 的功能。新插件应优先使用前两个更细粒度的 hook。

### 签名
```typescript
handler: (
  event: PluginHookBeforeAgentStartEvent,
  ctx: PluginHookAgentContext
) => PluginHookBeforeAgentStartResult | void
```

### Event 参数类型
```typescript
type PluginHookBeforeAgentStartEvent = {
  prompt: string;         // 用户 prompt
  messages?: unknown[];   // 可选，legacy hook 可能在 pre-session 阶段运行
};
```

### 返回值类型
```typescript
// 合并了 PromptBuild + ModelResolve 的所有字段
type PluginHookBeforeAgentStartResult = {
  systemPrompt?: string;
  prependContext?: string;
  prependSystemContext?: string;
  appendSystemContext?: string;
  modelOverride?: string;
  providerOverride?: string;
};
```

### 示例
```typescript
api.on('before_agent_start', (event, ctx) => {
  console.log(`Agent starting: session=${ctx.sessionKey}, prompt length=${event.prompt.length}`);
  return {
    modelOverride: 'claude-3-sonnet',
    prependContext: '请用中文回答。',
  };
}, { priority: 90 });
```

### Event 示例数据
```json
{
  "prompt": "[user] 帮我写一个 REST API",
  "messages": [
    { "role": "system", "content": "You are a helpful assistant." },
    { "role": "user", "content": "帮我写一个 REST API" }
  ]
}
```

> **注意**: 当 `allowPromptInjection=false` 时，prompt 相关字段（`systemPrompt`, `prependContext` 等）会被自动剥离，仅保留 `modelOverride` 和 `providerOverride`。

---

## 4. `llm_input`

### 触发阶段
每次 LLM 调用**发送请求之前**触发。一个 Agent turn 中可能有多次 LLM 调用（如工具调用后的后续推理）。这是一个**纯观测 hook**，不能修改请求。

### 签名
```typescript
handler: (
  event: PluginHookLlmInputEvent,
  ctx: PluginHookAgentContext
) => void
```

### Event 参数类型
```typescript
type PluginHookLlmInputEvent = {
  runId: string;              // 本次 LLM 调用的唯一 ID
  sessionId: string;          // 会话 ID
  provider: string;           // Provider 名称，如 "openai", "anthropic"
  model: string;              // 模型名称，如 "gpt-4", "claude-3-sonnet"
  systemPrompt?: string;      // 系统 prompt
  prompt: string;             // 用户 prompt
  historyMessages: unknown[]; // 历史消息列表
  imagesCount: number;        // 附带的图片数量
};
```

### 示例
```typescript
api.on('llm_input', (event, ctx) => {
  console.log(`LLM Request: runId=${event.runId}, model=${event.model}`);
  console.log(`  History: ${event.historyMessages.length} messages`);
  console.log(`  Images: ${event.imagesCount}`);
  // 可用于日志记录、监控、计费等
}, { priority: 80 });
```

### Event 示例数据
```json
{
  "runId": "run_abc123",
  "sessionId": "sess_xyz789",
  "provider": "anthropic",
  "model": "claude-3-sonnet-20240229",
  "systemPrompt": "You are a helpful coding assistant.",
  "prompt": "帮我写一个排序算法",
  "historyMessages": [
    { "role": "user", "content": "你好" },
    { "role": "assistant", "content": "你好！" }
  ],
  "imagesCount": 0
}
```

---

## 5. `llm_output`

### 触发阶段
每次 LLM 调用**返回响应之后**触发。包含模型输出文本和 token 用量信息。这是一个**纯观测 hook**。

### 签名
```typescript
handler: (
  event: PluginHookLlmOutputEvent,
  ctx: PluginHookAgentContext
) => void
```

### Event 参数类型
```typescript
type PluginHookLlmOutputEvent = {
  runId: string;              // 与 llm_input 对应的 runId
  sessionId: string;
  provider: string;
  model: string;
  assistantTexts: string[];   // 助手回复文本数组
  lastAssistant?: unknown;    // 最后一条助手消息的完整对象
  usage?: {
    input?: number;           // 输入 token 数
    output?: number;          // 输出 token 数
    cacheRead?: number;       // 缓存读取 token 数
    cacheWrite?: number;      // 缓存写入 token 数
    total?: number;           // 总 token 数
  };
};
```

### 示例
```typescript
api.on('llm_output', (event, ctx) => {
  console.log(`LLM Response: runId=${event.runId}, model=${event.model}`);
  if (event.usage) {
    console.log(`  Tokens: input=${event.usage.input}, output=${event.usage.output}`);
    console.log(`  Cache: read=${event.usage.cacheRead}, write=${event.usage.cacheWrite}`);
  }
  console.log(`  Response texts: ${event.assistantTexts.length} parts`);
});
```

### Event 示例数据
```json
{
  "runId": "run_abc123",
  "sessionId": "sess_xyz789",
  "provider": "anthropic",
  "model": "claude-3-sonnet-20240229",
  "assistantTexts": ["这是一个快速排序的实现：\n```python\ndef quicksort(arr):\n    ..."],
  "lastAssistant": {
    "role": "assistant",
    "content": [{ "type": "text", "text": "这是一个快速排序的实现..." }],
    "model": "claude-3-sonnet-20240229",
    "usage": { "input": 1500, "output": 350 }
  },
  "usage": {
    "input": 1500,
    "output": 350,
    "cacheRead": 800,
    "cacheWrite": 200,
    "total": 2850
  }
}
```

---

## 6. `agent_end`

### 触发阶段
Agent turn 完成后触发（无论成功或失败）。包含完整的消息列表、执行时长和成功状态。这是一个**纯观测 hook**，通常用于记录最终指标。

### 签名
```typescript
handler: (
  event: PluginHookAgentEndEvent,
  ctx: PluginHookAgentContext
) => void
```

### Event 参数类型
```typescript
type PluginHookAgentEndEvent = {
  messages: unknown[];    // 本次 turn 的完整消息列表
  success: boolean;       // 是否成功完成
  error?: string;         // 错误信息（如果失败）
  durationMs?: number;    // 执行时长（毫秒）
};
```

### 示例
```typescript
api.on('agent_end', (event, ctx) => {
  const status = event.success ? '✅ Success' : '❌ Failed';
  console.log(`Agent ended: ${status}, duration=${event.durationMs}ms`);
  console.log(`  Messages: ${event.messages.length}`);
  if (event.error) {
    console.error(`  Error: ${event.error}`);
  }

  // 统计 token 用量
  let totalInput = 0, totalOutput = 0;
  for (const msg of event.messages) {
    const m = msg as Record<string, unknown>;
    if (m?.role === 'assistant' && m?.usage) {
      const u = m.usage as Record<string, number>;
      totalInput += u.input || u.inputTokens || u.input_tokens || 0;
      totalOutput += u.output || u.outputTokens || u.output_tokens || 0;
    }
  }
  console.log(`  Total tokens: input=${totalInput}, output=${totalOutput}`);
}, { priority: -100 });
```

### Event 示例数据
```json
{
  "messages": [
    { "role": "system", "content": "You are a helpful assistant." },
    { "role": "user", "content": "帮我写排序算法" },
    {
      "role": "assistant",
      "content": [{ "type": "text", "text": "好的，这是快速排序..." }],
      "model": "claude-3-sonnet-20240229",
      "usage": { "input": 1500, "output": 350, "cacheRead": 800, "cacheWrite": 200 }
    }
  ],
  "success": true,
  "durationMs": 4523
}
```

### Context 示例数据
```json
{
  "agentId": "main",
  "sessionKey": "agent:main:anthropic-user:{\"channel\":\"web\"}",
  "sessionId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "trigger": "user"
}
```
