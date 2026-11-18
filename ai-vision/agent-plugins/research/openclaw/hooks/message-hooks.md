# Message Hooks

> 这组 hooks 覆盖了消息从外部渠道接收到发送回复的完整流程。主要用于多渠道（Telegram、Discord、Slack 等）场景。

## 触发顺序

```
inbound_claim → message_received → [Agent 处理] → message_sending → message_sent
```

---

## 1. `inbound_claim`

### 触发阶段
外部渠道收到新消息时触发。插件可以"认领"这条消息（返回 `handled: true`），阻止其他处理器继续处理。用于实现自定义消息路由或过滤。

### 签名
```typescript
handler: (
  event: PluginHookInboundClaimEvent,
  ctx: PluginHookInboundClaimContext
) => PluginHookInboundClaimResult | void
```

### Event 参数类型
```typescript
type PluginHookInboundClaimEvent = {
  content: string;                    // 消息文本内容
  body?: string;                      // 原始消息体
  bodyForAgent?: string;              // 给 Agent 的消息体（可能经过处理）
  transcript?: string;                // 语音转文字的文本
  timestamp?: number;                 // 消息时间戳
  channel: string;                    // 渠道标识: "telegram", "discord" 等
  accountId?: string;                 // 账号 ID
  conversationId?: string;            // 会话 ID
  parentConversationId?: string;      // 父会话 ID（线程场景）
  senderId?: string;                  // 发送者 ID
  senderName?: string;                // 发送者名称
  senderUsername?: string;            // 发送者用户名
  threadId?: string | number;         // 线程 ID
  messageId?: string;                 // 消息 ID
  isGroup: boolean;                   // 是否群组消息
  commandAuthorized?: boolean;        // 命令是否已授权
  wasMentioned?: boolean;             // 是否被 @提及
  metadata?: Record<string, unknown>; // 额外元数据
};
```

### Context 类型
```typescript
type PluginHookInboundClaimContext = PluginHookMessageContext & {
  parentConversationId?: string;
  senderId?: string;
  messageId?: string;
};

// 其中 PluginHookMessageContext:
type PluginHookMessageContext = {
  channelId: string;
  accountId?: string;
  conversationId?: string;
};
```

### 返回值类型
```typescript
type PluginHookInboundClaimResult = {
  handled: boolean;  // 如果为 true，消息被认领，不再传递给其他处理器
};
```

### 示例
```typescript
api.on('inbound_claim', (event, ctx) => {
  // 过滤群组中未被 @提及的消息
  if (event.isGroup && !event.wasMentioned) {
    return { handled: true }; // 静默忽略
  }

  // 自定义命令处理
  if (event.content.startsWith('/status')) {
    // 自行处理 /status 命令
    return { handled: true };
  }

  return undefined; // 不认领，继续正常处理
});
```

### Event 示例数据
```json
{
  "content": "帮我查一下天气",
  "body": "帮我查一下天气",
  "timestamp": 1710900000000,
  "channel": "telegram",
  "accountId": "bot_123",
  "conversationId": "chat_456",
  "senderId": "user_789",
  "senderName": "张三",
  "senderUsername": "zhangsan",
  "threadId": null,
  "messageId": "msg_001",
  "isGroup": true,
  "commandAuthorized": true,
  "wasMentioned": true,
  "metadata": {
    "chatType": "supergroup",
    "chatTitle": "技术讨论群"
  }
}
```

---

## 2. `message_received`

### 触发阶段
消息被接收并准备交给 Agent 处理时触发。在 `inbound_claim` 之后、Agent 开始处理之前。这是一个**纯观测 hook**。

### 签名
```typescript
handler: (
  event: PluginHookMessageReceivedEvent,
  ctx: PluginHookMessageContext
) => void
```

### Event 参数类型
```typescript
type PluginHookMessageReceivedEvent = {
  from: string;                        // 发送者标识
  content: string;                     // 消息内容
  timestamp?: number;                  // 时间戳
  metadata?: Record<string, unknown>;  // 额外元数据
};
```

### 示例
```typescript
api.on('message_received', (event, ctx) => {
  console.log(`Message from ${event.from}: ${event.content.slice(0, 100)}`);
  console.log(`  Channel: ${ctx.channelId}`);
});
```

### Event 示例数据
```json
{
  "from": "user_789",
  "content": "帮我查一下天气",
  "timestamp": 1710900000000,
  "metadata": { "chatType": "private" }
}
```

---

## 3. `message_sending`

### 触发阶段
Agent 回复即将发送到外部渠道**之前**触发。可以修改回复内容或取消发送。

### 签名
```typescript
handler: (
  event: PluginHookMessageSendingEvent,
  ctx: PluginHookMessageContext
) => PluginHookMessageSendingResult | void
```

### Event 参数类型
```typescript
type PluginHookMessageSendingEvent = {
  to: string;                          // 接收者标识
  content: string;                     // 即将发送的消息内容
  metadata?: Record<string, unknown>;  // 额外元数据
};
```

### 返回值类型
```typescript
type PluginHookMessageSendingResult = {
  content?: string;   // 修改后的消息内容
  cancel?: boolean;   // 如果为 true，取消发送
};
```

### 示例
```typescript
api.on('message_sending', (event, ctx) => {
  // 在回复末尾添加签名
  return {
    content: event.content + '\n\n— Powered by OpenClaw 🤖',
  };

  // 或者阻止发送包含敏感信息的回复
  if (event.content.includes('INTERNAL_SECRET')) {
    return { cancel: true };
  }
});
```

### Event 示例数据
```json
{
  "to": "chat_456",
  "content": "今天北京天气晴朗，气温 25°C。",
  "metadata": { "replyToMessageId": "msg_001" }
}
```

---

## 4. `message_sent`

### 触发阶段
消息成功发送到外部渠道**之后**触发。包含发送结果。这是一个**纯观测 hook**。

### 签名
```typescript
handler: (
  event: PluginHookMessageSentEvent,
  ctx: PluginHookMessageContext
) => void
```

### Event 参数类型
```typescript
type PluginHookMessageSentEvent = {
  to: string;          // 接收者标识
  content: string;     // 已发送的消息内容
  success: boolean;    // 是否发送成功
  error?: string;      // 错误信息（如果发送失败）
};
```

### 示例
```typescript
api.on('message_sent', (event, ctx) => {
  if (event.success) {
    console.log(`✅ Message sent to ${event.to}`);
  } else {
    console.error(`❌ Failed to send to ${event.to}: ${event.error}`);
  }
});
```

### Event 示例数据（成功）
```json
{
  "to": "chat_456",
  "content": "今天北京天气晴朗，气温 25°C。",
  "success": true
}
```

### Event 示例数据（失败）
```json
{
  "to": "chat_456",
  "content": "今天北京天气晴朗，气温 25°C。",
  "success": false,
  "error": "Telegram API error: chat not found"
}
```

---

## Internal Hook 系统中的消息事件

> 以下 hooks 通过 `api.registerHook()` 注册，与上面的 typed hooks 是**两套独立的触发机制**。
> Internal hook 的 payload 格式为 `{ type, action, context, sessionKey, timestamp, messages }`。

### 触发顺序

```
message:received → message:transcribed → message:preprocessed → [Agent 处理] → message:sent
```

---

## 5. `message:received`（Internal Hook）

### 触发阶段
消息收到时**早期**触发，此时媒体内容尚未处理。Content 中可能包含原始占位符如 `<media:audio>`。

### 注册方式
```typescript
api.registerHook(
  'message:received',
  async (event) => { /* ... */ },
  { name: 'my-hook', description: '...' }
);
```

### Event 参数类型
```typescript
{
  type: 'message';
  action: 'received';
  from: string;           // 发送者标识（手机号、用户 ID 等）
  content: string;        // 消息内容（可能含原始媒体占位符）
  timestamp?: number;     // Unix 时间戳
  channelId: string;      // 渠道（如 "whatsapp", "telegram", "discord"）
  accountId?: string;     // 多账号场景的账号 ID
  conversationId?: string;
  messageId?: string;
  metadata?: {
    to?: string;
    provider?: string;
    surface?: string;
    threadId?: string;
    senderId?: string;
    senderName?: string;
    senderUsername?: string;
    senderE164?: string;
  };
}
```

### 与 typed hook `message_received` 的区别
| 特性 | `message_received` (typed) | `message:received` (internal) |
|------|---------------------------|-------------------------------|
| 注册方式 | `api.on()` | `api.registerHook()` |
| 触发时机 | 消息准备交给 Agent 处理时 | 消息收到的早期阶段 |
| 媒体状态 | 可能已处理 | 未处理（含原始占位符） |

---

## 6. `message:transcribed`（Internal Hook）

### 触发阶段
音频消息转录完成后触发。此时 `transcript` 字段包含完整的转录文本。适合需要访问语音内容的场景。

### 注册方式
```typescript
api.registerHook(
  'message:transcribed',
  async (event) => {
    const e = event as { transcript: string; channelId: string };
    console.log(`Audio transcribed: ${e.transcript}`);
  },
  { name: 'my-transcription-hook', description: '...' }
);
```

### Event 参数类型
```typescript
{
  type: 'message';
  action: 'transcribed';
  body?: string;           // 转录前的原始消息体
  bodyForAgent?: string;   // 给 Agent 的消息体（经过富化）
  transcript: string;      // 音频转录文本
  channelId: string;       // 渠道（如 "telegram", "whatsapp"）
  conversationId?: string;
  messageId?: string;
}
```

---

## 7. `message:preprocessed`（Internal Hook）

### 触发阶段
所有媒体（音频转录、图片描述）和链接理解完成后触发，是 Agent 看到消息前的**最终状态**。适合需要完整富化内容的场景。

### 注册方式
```typescript
api.registerHook(
  'message:preprocessed',
  async (event) => {
    const e = event as { bodyForAgent: string; channelId: string };
    console.log(`Preprocessed body: ${e.bodyForAgent}`);
  },
  { name: 'my-preprocess-hook', description: '...' }
);
```

### Event 参数类型
```typescript
{
  type: 'message';
  action: 'preprocessed';
  body?: string;           // 原始消息体
  bodyForAgent?: string;   // 最终富化后的消息体（Agent 将看到此内容）
  transcript?: string;     // 音频转录文本（如有）
  channelId: string;
  conversationId?: string;
  messageId?: string;
  isGroup?: boolean;
  groupId?: string;
}
```

---

## 8. `message:sent`（Internal Hook）

### 触发阶段
出站消息成功发送到外部渠道后触发。包含丰富的发送结果上下文。

### 注册方式
```typescript
api.registerHook(
  'message:sent',
  async (event) => {
    const e = event as { to: string; content: string; success: boolean; channelId: string };
    console.log(`Sent to ${e.to} via ${e.channelId}: ${e.success}`);
  },
  { name: 'my-sent-hook', description: '...' }
);
```

### Event 参数类型
```typescript
{
  type: 'message';
  action: 'sent';
  to: string;              // 接收者标识
  content: string;         // 已发送的消息内容
  success: boolean;        // 是否发送成功
  error?: string;          // 错误信息（发送失败时）
  channelId: string;
  accountId?: string;
  conversationId?: string;
  messageId?: string;      // 提供方返回的消息 ID
  isGroup?: boolean;
  groupId?: string;
}
```

---

## 9. `message`（Internal Hook，通用监听）

### 触发阶段
所有消息事件的通用监听器，覆盖 `message:received`、`message:transcribed`、`message:preprocessed`、`message:sent`。

### 注册方式
```typescript
api.registerHook(
  'message',
  async (event) => {
    const e = event as { type: string; action: string };
    console.log(`Message event: ${e.type}:${e.action}`);
  },
  { name: 'my-message-all-hook', description: '...' }
);
```

> **注意**：使用通用监听器会带来更多开销，建议在需要监听所有消息事件时才使用，否则优先使用具体的 `message:*` hook。
