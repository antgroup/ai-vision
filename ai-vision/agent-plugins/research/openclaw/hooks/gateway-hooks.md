# Gateway Hooks

> 这组 hooks 覆盖了 OpenClaw Gateway 服务的启动和停止。Gateway 是 OpenClaw 的核心服务进程。

## 触发顺序

```
gateway_start → [Gateway 运行中] → gateway_stop
```

---

## 1. `gateway_start`

### 触发阶段
Gateway 服务启动完成、开始监听端口时触发。这是一个**纯观测 hook**。

### 签名
```typescript
handler: (
  event: PluginHookGatewayStartEvent,
  ctx: PluginHookGatewayContext
) => void
```

### Event 参数类型
```typescript
type PluginHookGatewayStartEvent = {
  port: number;  // Gateway 监听的端口号
};
```

### Context 类型
```typescript
type PluginHookGatewayContext = {
  port?: number;
};
```

### 示例
```typescript
api.on('gateway_start', (event, ctx) => {
  console.log(`🚀 Gateway started on port ${event.port}`);
  // 可用于初始化外部连接、注册服务发现等
});
```

### Event 示例数据
```json
{
  "port": 3000
}
```

### Context 示例数据
```json
{
  "port": 3000
}
```

---

## 2. `gateway_stop`

### 触发阶段
Gateway 服务即将停止时触发。可用于清理资源、关闭连接等。这是一个**纯观测 hook**。

### 签名
```typescript
handler: (
  event: PluginHookGatewayStopEvent,
  ctx: PluginHookGatewayContext
) => void
```

### Event 参数类型
```typescript
type PluginHookGatewayStopEvent = {
  reason?: string;  // 停止原因
};
```

### 示例
```typescript
api.on('gateway_stop', (event, ctx) => {
  console.log(`🛑 Gateway stopping: ${event.reason || 'normal shutdown'}`);
  // 清理外部连接、flush 缓冲区等
});
```

### Event 示例数据
```json
{
  "reason": "SIGTERM"
}
```

### 无原因的 Event 示例
```json
{}
```

---

## 补充：`gateway:startup` Internal Hook

除了 typed hook `gateway_start` 之外，OpenClaw 还有一个通过 `registerHook` 注册的 internal hook `gateway:startup`，在 Gateway 启动时触发。

### 注册方式
```typescript
api.registerHook(
  'gateway:startup',
  async () => {
    console.log('Gateway startup event');
  },
  { name: 'my-startup-hook', description: 'Records gateway startup' }
);
```

### 与 `gateway_start` 的区别
| 特性 | `gateway_start` (typed) | `gateway:startup` (internal) |
|------|------------------------|------------------------------|
| 注册方式 | `api.on()` | `api.registerHook()` |
| 类型安全 | ✅ 完整类型推导 | ❌ event 为 `unknown` |
| 触发时机 | 端口监听就绪后 | Gateway 启动流程中 |
| 参数 | `{ port }` | 无固定参数 |

## 补充：Command Event Hooks

OpenClaw 还支持通过 `registerHook` 注册命令事件 hook：

```typescript
api.registerHook(
  ['command:new', 'command:reset', 'command:stop'],
  async (event) => {
    const e = event as Record<string, unknown>;
    console.log(`Command: ${e.action}, session=${e.sessionKey}`);
  },
  { name: 'my-command-hook', description: 'Tracks command events' }
);
```

### Command Event 参数
```typescript
{
  action: string;        // 命令动作: "new", "reset", "stop"
  sessionKey: string;    // 会话键
  context?: {
    commandSource: string;  // 命令来源
  };
}
```

---

## 补充：`command` 通用监听（Internal Hook）

除了具体的 `command:new`、`command:reset`、`command:stop` 之外，还可以用 `command` 监听所有命令事件：

```typescript
api.registerHook(
  'command',
  async (event) => {
    const e = event as { action: string; sessionKey: string };
    console.log(`Command: ${e.action}`);
  },
  { name: 'my-command-all', description: 'Listen to all command events' }
);
```

> **注意**：使用通用 `command` 监听器比具体的 `command:new` 等有更多开销，建议仅在需要监听所有命令时使用。

---

## 补充：`agent:bootstrap`（Internal Hook）

### 触发阶段
Agent 开始处理前，workspace bootstrap 文件（如 `AGENTS.md`、`TOOLS.md`）注入**之前**触发。Hook 可以 mutate `context.bootstrapFiles` 来动态注入额外文件。

### 注册方式
```typescript
api.registerHook(
  'agent:bootstrap',
  async (event) => {
    const e = event as {
      type: string;
      action: string;
      context?: { bootstrapFiles?: string[] };
    };
    // 可以向 bootstrapFiles 追加额外文件路径
    if (e.context?.bootstrapFiles) {
      e.context.bootstrapFiles.push('/path/to/extra/AGENTS.md');
    }
  },
  { name: 'my-bootstrap-hook', description: 'Inject extra bootstrap files' }
);
```

### Event 参数类型
```typescript
{
  type: 'agent';
  action: 'bootstrap';
  context?: {
    bootstrapFiles?: string[];  // 可 mutate，追加额外 bootstrap 文件路径
  };
  sessionKey?: string;
  timestamp?: Date;
}
```

### 典型用途
- 动态注入 monorepo 中各子包的 `AGENTS.md` / `TOOLS.md`
- 根据当前工作目录或环境变量注入不同的 bootstrap 文件
- 参见内置 hook `bootstrap-extra-files` 的实现

### 与 typed hook 的区别
`agent:bootstrap` 是**纯 internal hook**，没有对应的 typed hook（`api.on()` 不支持此事件）。
