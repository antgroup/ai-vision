# OpenCode Plugin API 调研报告

> **调研时间**: 2026-03-29
> **SDK 版本**: @opencode-ai/sdk@1.3.4
> **Plugin 版本**: @opencode-ai/plugin@1.3.4
> **文档来源**: https://opencode.ai/docs/plugins/

---

## 一、OpenCode Plugin 架构概述

### 1.1 插件运行模式

OpenCode 插件是 **TypeScript/JavaScript 模块**，运行在 OpenCode 进程内。这与 cchooks/cdxhooks 的外部二进制模式不同：

| 特性 | OpenCode Plugin | cchooks/cdxhooks |
|------|-----------------|------------------|
| 运行方式 | 进程内模块 | 外部二进制 |
| 语言 | TypeScript/JavaScript | Rust |
| 数据获取 | SDK API 调用 | 解析 transcript 文件 |
| 通信方式 | 直接函数调用 | stdin/stdout |

### 1.2 插件加载路径

按优先级加载：
1. 全局配置: `~/.config/opencode/opencode.json` 中的 `"plugin"` 数组
2. 项目配置: `opencode.json` 中的 `"plugin"` 数组
3. 全局插件目录: `~/.config/opencode/plugins/`
4. 项目插件目录: `.opencode/plugins/`

npm 包加载方式：
```json
{
  "plugin": ["opencode-helicone-session", "@my-org/custom-plugin"]
}
```

带选项加载：
```json
{
  "plugin": [["observ-opencode", { "enableTelemetry": true }]]
}
```

---

## 二、Plugin 类型定义

### 2.1 核心类型

```typescript
// 插件函数签名
type Plugin = (input: PluginInput, options?: PluginOptions) => Promise<Hooks>;

// 插件输入参数
type PluginInput = {
    client: OpencodeClient;      // SDK 客户端
    project: Project;            // 当前项目信息
    directory: string;           // 工作目录
    worktree: string;            // Git worktree 路径
    serverUrl: URL;              // OpenCode Server URL
    $: BunShell;                 // Bun Shell API
};

// 插件选项（用户配置）
type PluginOptions = Record<string, unknown>;
```

### 2.2 插件模块结构

```typescript
// 服务端插件（后台运行）
type PluginModule = {
    id?: string;
    server: Plugin;              // 必填：插件主函数
    tui?: never;                 // 服务端插件不能有 TUI
};

// TUI 插件（界面扩展）
type TuiPluginModule = {
    id?: string;
    tui: TuiPlugin;              // 必填：TUI 扩展函数
    server?: never;              // TUI 插件不能有 server
};
```

### 2.3 基础插件示例

```typescript
import type { Plugin } from "@opencode-ai/plugin";

export const ObservOpencodePlugin: Plugin = async (input, options) => {
  const { client, project, directory } = input;

  return {
    // 返回各种 hooks
  };
};
```

---

## 三、Hooks 接口完整列表

### 3.1 Hooks 类型定义

```typescript
type Hooks = {
    // 事件处理
    event?: (event: Event) => void | Promise<void>;

    // 配置处理
    config?: (config: Config) => Config | Promise<Config>;

    // 自定义工具定义
    tool?: Record<string, ToolDefinition>;

    // 认证钩子
    auth?: AuthHook;

    // 消息处理
    "chat.message"?: (message: { sessionID: string; }) => void | Promise<void>;
    "chat.params"?: (params: ChatParams) => ChatParams | Promise<ChatParams>;
    "chat.headers"?: (headers: Headers) => Headers | Promise<Headers>;

    // 权限处理
    "permission.ask"?: (request: PermissionRequest) => PermissionAction | Promise<PermissionAction>;

    // 命令执行钩子
    "command.execute.before"?: (input: CommandInput, output: CommandOutput) => void | Promise<void>;

    // 工具执行钩子 ⭐ 重要
    "tool.execute.before"?: (input: ToolExecuteInput, output: ToolExecuteOutput) => void | Promise<void>;
    "tool.execute.after"?: (input: ToolExecuteInput, output: ToolExecuteOutput) => void | Promise<void>;

    // Shell 环境设置
    "shell.env"?: () => Record<string, string> | Promise<Record<string, string>>;

    // 实验性钩子
    "experimental.chat.messages.transform"?: (messages: Message[]) => Message[] | Promise<Message[]>;
    "experimental.chat.system.transform"?: (system: string) => string | Promise<string>;
    "experimental.session.compacting"?: (context: CompactContext) => void;
    "experimental.text.complete"?: (input: TextCompleteInput) => TextCompleteResult;

    // 工具定义修改
    "tool.definition"?: (tool: ToolDefinition) => ToolDefinition | Promise<ToolDefinition>;
};
```

### 3.2 工具执行钩子详细结构

⚠️ **注意**: 官方文档和类型定义中，`tool.execute.before/after` 的具体参数结构没有完全公开。根据事件系统推断：

```typescript
// 推断的工具执行输入
interface ToolExecuteInput {
    sessionID: string;
    messageID: string;
    tool: string;           // 工具名称
    callID: string;         // 工具调用 ID
    input: Record<string, unknown>;  // 工具输入参数
}

// 推断的工具执行输出（after 钩子）
interface ToolExecuteOutput {
    sessionID: string;
    messageID: string;
    tool: string;
    callID: string;
    input: Record<string, unknown>;
    output?: string;        // 工具执行结果
    state: "completed" | "error" | "pending" | "running";
    time: {
        start: number;
        end: number;
    };
}
```

---

## 四、Event 事件系统

### 4.1 订阅事件的方式

**方式一：通过 Hooks 的 `event` 字段**

```typescript
export const MyPlugin: Plugin = async (input) => {
  return {
    event: (event) => {
      console.log(event.type, event.properties);
    }
  };
};
```

**方式二：通过 SDK 的 `event.subscribe()`**

```typescript
const events = await client.event.subscribe();
for await (const event of events.stream) {
  console.log(event.type, event.properties);
}
```

### 4.2 完整事件列表（45 种）

#### Session 会话事件

| 事件类型 | properties 结构 | 说明 |
|---------|----------------|------|
| `session.created` | `{ sessionID: string; info: Session; }` | 会话创建 |
| `session.updated` | `{ sessionID: string; info: Session; }` | 会话更新 |
| `session.deleted` | `{ sessionID: string; info: Session; }` | 会话删除 |
| `session.status` | `{ sessionID: string; status: SessionStatus; }` | 会话状态变化 |
| `session.idle` | `{ sessionID: string; }` | 会话空闲 |
| `session.compacted` | `{ sessionID: string; }` | 会话压缩 |
| `session.diff` | `{ sessionID: string; diff: Array<FileDiff>; }` | 文件变更 |
| `session.error` | `{ sessionID?: string; error?: Error; }` | 会话错误 |

#### Message 消息事件

| 事件类型 | properties 结构 | 说明 |
|---------|----------------|------|
| `message.updated` | `{ sessionID: string; info: Message; }` | 消息更新 |
| `message.removed` | `{ sessionID: string; messageID: string; }` | 消息删除 |
| `message.part.updated` | `{ sessionID: string; part: Part; time: number; }` | 消息部分更新 |
| `message.part.removed` | `{ sessionID: string; messageID: string; partID: string; }` | 消息部分删除 |
| `message.part.delta` | `{ sessionID: string; messageID: string; partID: string; field: string; delta: string; }` | 增量更新 |

#### Tool 工具事件 ⚠️ 不存在

**重要发现**：OpenCode 的事件系统中 **没有** `tool.execute.before` 或 `tool.execute.after` 事件。

工具执行钩子存在于 **Hooks 接口**，而不在事件系统中。这意味着：
- 事件系统用于 UI 状态同步
- Hooks 系统用于插件干预行为

#### 其他重要事件

| 事件类型 | 说明 |
|---------|------|
| `file.edited` | 文件编辑 |
| `command.executed` | 命令执行完成 |
| `permission.asked` | 权限请求 |
| `permission.replied` | 权限响应 |

---

## 五、核心类型详细定义

### 5.1 Session 类型

```typescript
type Session = {
    id: string;
    slug: string;
    projectID: string;
    workspaceID?: string;
    directory: string;
    parentID?: string;
    summary?: {
        additions: number;
        deletions: number;
        files: number;
        diffs?: Array<FileDiff>;
    };
    share?: { url: string; };
    title: string;
    version: string;
    time: {
        created: number;      // Unix timestamp
        updated: number;
        compacting?: number;
        archived?: number;
    };
    permission?: PermissionRuleset;
    revert?: {
        messageID: string;
        partID?: string;
        snapshot?: string;
        diff?: string;
    };
};
```

### 5.2 Message 类型

```typescript
// 用户消息
type UserMessage = {
    id: string;
    sessionID: string;
    role: "user";
    time: { created: number; };
    format?: OutputFormat;
    summary?: { title?: string; body?: string; diffs: Array<FileDiff>; };
    agent: string;
    model: { providerID: string; modelID: string; };
    system?: string;
    tools?: { [key: string]: boolean; };
    variant?: string;
};

// 助手消息
type AssistantMessage = {
    id: string;
    sessionID: string;
    role: "assistant";
    time: { created: number; completed?: number; };
    error?: Error;
    parentID: string;
    modelID: string;
    providerID: string;
    mode: string;
    agent: string;
    path: { cwd: string; root: string; };
    summary?: boolean;

    // ⭐ Token 使用量
    cost: number;
    tokens: {
        total?: number;
        input: number;
        output: number;
        reasoning: number;
        cache: { read: number; write: number; };
    };

    structured?: unknown;
    variant?: string;
    finish?: string;
};
```

### 5.3 Part 类型（消息内容）

```typescript
// 文本部分
type TextPart = {
    id: string;
    type: "text";
    text: string;
};

// 工具部分
type ToolPart = {
    id: string;
    type: "tool";
    callID: string;
    tool: string;
    state: ToolState;
};

// 工具状态（已完成）
type ToolStateCompleted = {
    status: "completed";
    input: { [key: string]: unknown; };
    output: string;
    title: string;
    metadata: { [key: string]: unknown; };
    time: { start: number; end: number; compacted?: number; };
    attachments?: Array<FilePart>;
};

// 工具状态（运行中）
type ToolStateRunning = {
    status: "running";
    input: { [key: string]: unknown; };
    time: { start: number };
};

// 工具状态（错误）
type ToolStateError = {
    status: "error";
    input: { [key: string]: unknown; };
    output?: string;
    error: string;
    time: { start: number; end: number; };
};
```

---

## 六、SDK 客户端 API

### 6.1 创建客户端

```typescript
import { createOpencode, createOpencodeClient } from "@opencode-ai/sdk";

// 方式一：创建完整实例（启动 server）
const { client, server } = await createOpencode({
  hostname: "127.0.0.1",
  port: 4096,
});

// 方式二：仅创建客户端（连接已有 server）
const client = createOpencodeClient({
  baseUrl: "http://localhost:4096"
});
```

### 6.2 Session API

```typescript
// 列出所有会话
const sessions = await client.session.list();

// 获取特定会话
const session = await client.session.get({ path: { id: "session-id" } });

// 创建会话
const newSession = await client.session.create({ body: { title: "New Session" } });

// 获取会话消息
const messages = await client.session.messages({ path: { id: "session-id" } });

// 发送 prompt
const response = await client.session.prompt({
  path: { id: "session-id" },
  body: {
    parts: [{ type: "text", text: "Hello" }],
    model: { providerID: "anthropic", modelID: "claude-sonnet-4-6" }
  }
});
```

### 6.3 其他 API

```typescript
// 文件操作
await client.file.read({ query: { path: "/src/main.ts" } });
await client.find.text({ query: { pattern: "TODO" } });

// 配置
const config = await client.config.get();
const providers = await client.config.providers();

// 项目
const project = await client.project.current();
const projects = await client.project.list();
```

---

## 七、Token 使用量获取方式

### 7.1 从 AssistantMessage 获取

当收到 `message.updated` 事件且 `role === "assistant"` 时：

```typescript
{
  event: (event) => {
    if (event.type === "message.updated" && event.properties.info.role === "assistant") {
      const msg = event.properties.info as AssistantMessage;
      console.log("Input tokens:", msg.tokens.input);
      console.log("Output tokens:", msg.tokens.output);
      console.log("Reasoning tokens:", msg.tokens.reasoning);
      console.log("Cache read:", msg.tokens.cache.read);
      console.log("Cache write:", msg.tokens.cache.write);
      console.log("Cost:", msg.cost);
      console.log("Model:", msg.modelID);
    }
  }
}
```

### 7.2 从 ToolStateCompleted 获取

工具执行结果包含时间戳，但不包含 token 使用量。Token 使用量在 **消息级别** 统计。

### 7.3 粒度说明

| 数据 | 来源 | 粒度 |
|------|------|------|
| Token usage | `AssistantMessage.tokens` | 消息级别 |
| Cost | `AssistantMessage.cost` | 消息级别 |
| Model ID | `AssistantMessage.modelID` | 消息级别 |
| Tool execution time | `ToolStateCompleted.time` | 工具级别 |

**结论**：OpenCode 在工具级别没有单独的 token 统计，所有 token 统计在消息级别。

---

## 八、 triển khai 方案建议

### 8.1 推荐的 Hook 策略

基于调研结果，推荐以下实现方式：

```typescript
export const ObservOpencodePlugin: Plugin = async (input, options) => {
  const { client, directory } = input;

  // 从 options 获取配置
  const config = parseConfig(options);

  return {
    // 方式一：使用 tool.execute.after 钩子（实时上报）
    "tool.execute.after": async (toolInput, toolOutput) => {
      // 上报 tool span
      await reportToolSpan(toolInput, toolOutput, config);
    },

    // 方式二：使用 message.updated 事件获取 token 使用量
    event: async (event) => {
      if (event.type === "message.updated" && event.properties.info.role === "assistant") {
        const msg = event.properties.info as AssistantMessage;
        // 更新 trace 的 token 使用量
        await updateTraceTokens(event.properties.sessionID, msg);
      }
    },
  };
};
```

### 8.2 数据获取流程

```
User Prompt
    │
    ▼
message.updated (user) ──────┐
    │                         │
    ▼                         │
tool.execute.before ─────────┤
    │                         │
    ▼                         │
tool.execute.after ──────────┼──> 上报 Tool Span
    │                         │
    ▼                         │
tool.execute.before ─────────┤
    │                         │
    ▼                         │
tool.execute.after ──────────┼──> 上报 Tool Span
    │                         │
    ▼                         │
message.updated (assistant) ──┴──> 上报 Trace (含 tokens)
```

### 8.3 关键限制

1. **工具级别无 token 统计**: 需要在消息级别获取
2. **工具执行钩子参数结构未公开**: 可能需要运行时调试
3. **进程内运行**: 插件崩溃可能影响 OpenCode 主进程

---

## 九、待验证问题

### 9.1 需要运行时验证

- [ ] `tool.execute.before/after` 的实际参数结构
- [ ] Tool 执行结果内容的格式
- [ ] 并发工具调用的处理方式
- [ ] 错误场景的数据结构

### 9.2 建议的验证方法

```typescript
// 调试插件 - 打印所有事件和钩子参数
export const DebugPlugin: Plugin = async (input) => {
  return {
    "tool.execute.before": (i, o) => {
      console.log("[BEFORE]", JSON.stringify({ input: i, output: o }, null, 2));
    },
    "tool.execute.after": (i, o) => {
      console.log("[AFTER]", JSON.stringify({ input: i, output: o }, null, 2));
    },
    event: (e) => {
      console.log("[EVENT]", e.type, JSON.stringify(e.properties, null, 2));
    },
  };
};
```

---

## 十、参考资源

- **OpenCode 官方文档**: https://opencode.ai/docs/plugins/
- **SDK 类型定义**: node_modules/@opencode-ai/sdk/dist/v2/gen/types.gen.d.ts
- **Plugin 类型定义**: node_modules/@opencode-ai/plugin/dist/index.d.ts
- **GitHub 仓库**: https://github.com/anomalyco/opencode