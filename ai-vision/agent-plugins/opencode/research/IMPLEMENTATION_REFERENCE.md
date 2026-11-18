# OpenCode Langfuse 插件实现参考

> **创建时间**: 2026-03-29
> **参考项目**: openclaw/observ-openclaw

---

## 一、项目结构建议

```
opencode/observ-opencode/
├── package.json                # 包配置
├── opencode.plugin.json        # OpenCode 插件配置 schema
├── tsconfig.json               # TypeScript 配置
├── src/
│   ├── index.ts                # 插件入口（导出 Plugin 函数）
│   ├── config.ts               # 配置解析（复用 openclaw 模式）
│   ├── telemetry.ts            # OTLP 初始化（复用 openclaw 模式）
│   ├── hooks.ts                # OpenCode hooks 实现
│   └── otlp/
│       ├── index.ts
│       └── semconv/            # 语义约定（复用或引用 openclaw）
│           ├── attributes.const.ts
│           ├── spans.const.ts
│           └── events.const.ts
└── README.md
```

---

## 二、依赖包

```json
{
  "name": "observ-opencode",
  "version": "0.1.0",
  "type": "module",
  "main": "dist/index.js",
  "types": "dist/index.d.ts",
  "exports": {
    ".": {
      "types": "./dist/index.d.ts",
      "import": "./dist/index.js"
    }
  },
  "peerDependencies": {
    "@opencode-ai/plugin": "^1.3.4",
    "@opencode-ai/sdk": "^1.3.4"
  },
  "dependencies": {
    "@opentelemetry/api": "^1.9.0",
    "@opentelemetry/api-logs": "^0.58.0",
    "@opentelemetry/exporter-logs-otlp-http": "^0.58.0",
    "@opentelemetry/exporter-metrics-otlp-http": "^0.58.0",
    "@opentelemetry/exporter-trace-otlp-http": "^0.58.0",
    "@opentelemetry/resources": "^1.28.0",
    "@opentelemetry/sdk-logs": "^0.58.0",
    "@opentelemetry/sdk-metrics": "^1.28.0",
    "@opentelemetry/sdk-trace-base": "^1.28.0",
    "@opentelemetry/sdk-trace-node": "^1.28.0",
    "@opentelemetry/semantic-conventions": "^1.28.0"
  },
  "devDependencies": {
    "typescript": "^5.8.0"
  }
}
```

---

## 三、插件入口实现

```typescript
// src/index.ts
import type { Plugin, PluginInput, Hooks } from "@opencode-ai/plugin";
import { parseConfig, type ObservOpencodeConfig } from "./config.js";
import { initObservabilityTelemetry, type TelemetryRuntime } from "./telemetry.js";
import { createToolSpanHooks, createMessageEventHooks } from "./hooks.js";

export type { ObservOpencodeConfig };

let telemetryRuntime: TelemetryRuntime | undefined;

/**
 * OpenCode Observability Plugin
 *
 * 配置示例 (opencode.json):
 * ```json
 * {
 *   "plugin": [["observ-opencode", {
 *     "enableTelemetry": true,
 *     "otelExporter": {
 *       "endpoint": "https://otel.example.com"
 *     },
 *     "ant": {
 *       "username": "100001",
 *       "agentType": "opencode"
 *     }
 *   }]]
 * }
 * ```
 */
export const ObservOpencodePlugin: Plugin = async (
  input: PluginInput,
  options?: Record<string, unknown>
): Promise<Hooks> => {
  const { client, project, directory } = input;

  // 解析配置
  const config = parseConfig(options);

  // 主开关检查
  if (!config.enableTelemetry) {
    console.log("[observ-opencode] Telemetry disabled by config");
    return {};
  }

  // 初始化 OTLP 组件
  telemetryRuntime = initObservabilityTelemetry(config);

  // 返回 hooks
  return {
    // 工具执行钩子 - 上报 Tool span
    "tool.execute.before": async (toolInput, _output) => {
      // TODO: 实现 tool span 开始逻辑
      console.log("[observ-opencode] tool.execute.before", toolInput);
    },

    "tool.execute.after": async (toolInput, toolOutput) => {
      // TODO: 实现 tool span 结束逻辑
      console.log("[observ-opencode] tool.execute.after", toolInput, toolOutput);
    },

    // 事件处理 - 获取 token 使用量
    event: async (event) => {
      if (event.type === "message.updated" && event.properties.info.role === "assistant") {
        // TODO: 更新 trace 的 token 使用量
        console.log("[observ-opencode] message.updated (assistant)", event.properties);
      }
    },
  };
};

export default ObservOpencodePlugin;
```

---

## 四、配置解析（复用 openclaw 模式）

配置解析逻辑可以直接复用 `openclaw/observ-openclaw/src/config.ts`，只需修改默认值：

```typescript
// src/config.ts - 关键修改点

export interface ObservOpencodeConfig {
  // ... 与 openclaw 相同的字段 ...

  ant: AntIdentityConfig;
  // ...
}

// 默认值修改
ant: {
  username: (typeof antInput.username === 'string' ? antInput.username : '')
    || envString('AIVISION_ANT_USERNAME', ''),
  agentType: (typeof antInput.agentType === 'string' ? antInput.agentType : '')
    || envString('AIVISION_ANT_AGENT_TYPE', 'opencode'),  // ← 修改默认值
  agentProduct: (typeof antInput.agentProduct === 'string' ? antInput.agentProduct : '')
    || envString('AIVISION_ANT_AGENT_PRODUCT', 'opencode'),  // ← 修改默认值
  // ...
},

serviceName: (typeof input.serviceName === 'string' ? input.serviceName : undefined)
  || envString('OTEL_SERVICE_NAME', 'opencode-agent'),  // ← 修改默认值
```

---

## 五、Hooks 实现策略

### 5.1 数据流

```
┌───────────────────────────────────────────────────────────────┐
│                    OpenCode Plugin Lifecycle                   │
├───────────────────────────────────────────────────────────────┤
│                                                               │
│  session.created                                              │
│       │                                                       │
│       ▼                                                       │
│  message.updated (user) ────► 创建 User 事件                  │
│       │                                                       │
│       ▼                                                       │
│  tool.execute.before ───────► 开始 Tool span                  │
│       │                                                       │
│       ▼                                                       │
│  tool.execute.after ────────► 结束 Tool span                  │
│       │                   (上报 tool input/output)             │
│       ▼                                                       │
│  [... 可能多次 tool 调用 ...]                                  │
│       │                                                       │
│       ▼                                                       │
│  message.updated (assistant) ─► 结束 Agent span               │
│                               (上报 tokens, cost, model)       │
│       │                                                       │
│       ▼                                                       │
│  session.idle ──────────────► Flush traces                    │
│                                                               │
└───────────────────────────────────────────────────────────────┘
```

### 5.2 Span 层级结构

```
openclaw.chat (root span)
├── agentic.turn (turn span)
│   ├── gen_ai.tool.bash
│   ├── gen_ai.tool.read
│   └── gen_ai.tool.edit
└── (end)
```

### 5.3 关键实现代码

```typescript
// src/hooks.ts

import { context, trace, SpanStatusCode, type Span, type Context } from '@opentelemetry/api';
import type { TelemetryRuntime } from './telemetry.js';
import type { AssistantMessage, ToolStateCompleted } from '@opencode-ai/sdk';

// Session 级别的 span 存储
const sessionSpans = new Map<string, {
  rootSpan: Span;
  turnSpan: Span;
  toolSpans: Map<string, Span>;
}>();

export function createHooks(runtime: TelemetryRuntime, config: ObservOpencodeConfig) {
  const { tracer, logger, tracesEnabled, eventsEnabled } = runtime;

  return {
    "tool.execute.before": async (input: ToolExecuteInput, _output: unknown) => {
      if (!tracesEnabled) return;

      const { sessionID, tool, callID } = input;
      const sessionCtx = sessionSpans.get(sessionID);
      if (!sessionCtx) return;

      // 创建 tool span
      const toolSpan = tracer.startSpan(
        `gen_ai.tool.${tool}`,
        {
          kind: SpanKind.INTERNAL,
          attributes: {
            'gen_ai.tool.name': tool,
            'gen_ai.tool.call.id': callID,
          }
        },
        trace.setSpan(context.active(), sessionCtx.turnSpan)
      );

      sessionCtx.toolSpans.set(callID, toolSpan);
    },

    "tool.execute.after": async (input: ToolExecuteInput, output: ToolExecuteOutput) => {
      if (!tracesEnabled) return;

      const { sessionID, tool, callID } = input;
      const sessionCtx = sessionSpans.get(sessionID);
      if (!sessionCtx) return;

      const toolSpan = sessionCtx.toolSpans.get(callID);
      if (!toolSpan) return;

      // 记录工具执行结果
      if (output.state === 'completed') {
        toolSpan.setStatus({ code: SpanStatusCode.OK });
        if (config.log.spans.toolOutputs && output.output) {
          toolSpan.setAttribute('gen_ai.tool.output', truncate(output.output, 1000));
        }
      } else if (output.state === 'error') {
        toolSpan.setStatus({ code: SpanStatusCode.ERROR, message: output.error });
      }

      toolSpan.end();
      sessionCtx.toolSpans.delete(callID);
    },

    event: async (event: Event) => {
      // 处理消息更新，获取 token 使用量
      if (event.type === 'message.updated') {
        const { sessionID, info } = event.properties;

        if (info.role === 'assistant') {
          const msg = info as AssistantMessage;

          // 上报 token metrics
          if (runtime.metricsEnabled) {
            // TODO: 记录 token 使用量 metrics
            console.log(`[observ-opencode] tokens: input=${msg.tokens.input}, output=${msg.tokens.output}`);
          }

          // 结束 turn span
          const sessionCtx = sessionSpans.get(sessionID);
          if (sessionCtx?.turnSpan) {
            // 设置 token 属性
            sessionCtx.turnSpan.setAttribute('gen_ai.usage.input_tokens', msg.tokens.input);
            sessionCtx.turnSpan.setAttribute('gen_ai.usage.output_tokens', msg.tokens.output);
            sessionCtx.turnSpan.setAttribute('gen_ai.usage.total_tokens', msg.tokens.total || msg.tokens.input + msg.tokens.output);
            sessionCtx.turnSpan.setAttribute('gen_ai.response.model', msg.modelID);
            sessionCtx.turnSpan.end();
          }
        }
      }

      // 处理会话创建
      if (event.type === 'session.created') {
        const { sessionID, info } = event.properties;

        if (tracesEnabled) {
          // 创建 root span
          const rootSpan = tracer.startSpan('openclaw.chat', {
            kind: SpanKind.SERVER,
            attributes: {
              'gen_ai.conversation.id': sessionID,
              'ant.agent.type': config.ant.agentType,
              'ant.username': config.ant.username,
            }
          });

          // 创建 turn span
          const turnSpan = tracer.startSpan('agentic.turn', {
            kind: SpanKind.INTERNAL,
          }, trace.setSpan(context.active(), rootSpan));

          sessionSpans.set(sessionID, {
            rootSpan,
            turnSpan,
            toolSpans: new Map(),
          });
        }
      }

      // 处理会话空闲 - 清理
      if (event.type === 'session.idle') {
        const { sessionID } = event.properties;
        const sessionCtx = sessionSpans.get(sessionID);

        if (sessionCtx) {
          // 结束所有未完成的 tool spans
          for (const toolSpan of sessionCtx.toolSpans.values()) {
            toolSpan.end();
          }

          // 结束 root span
          sessionCtx.rootSpan.end();
          sessionSpans.delete(sessionID);
        }
      }
    },
  };
}
```

---

## 六、待验证问题清单

### 6.1 必须验证

| 问题 | 验证方法 | 影响 |
|------|----------|------|
| `tool.execute.before/after` 实际参数结构 | 编写调试插件打印 | Hook 实现 |
| 并发工具调用处理 | 触发多个并发工具调用 | Span 层级 |
| 错误场景数据结构 | 触发工具执行失败 | 错误属性 |

### 6.2 建议验证

| 问题 | 验证方法 | 影响 |
|------|----------|------|
| Tool 执行时间戳精度 | 对比 `time.start/end` | 性能统计 |
| Model ID 格式 | 打印 `AssistantMessage.modelID` | 语义约定 |
| Provider ID 格式 | 打印 `AssistantMessage.providerID` | 语义约定 |

### 6.3 调试插件代码

```typescript
// debug-plugin.ts
import type { Plugin } from "@opencode-ai/plugin";

export const DebugPlugin: Plugin = async () => {
  return {
    "tool.execute.before": (i, o) => {
      console.log("[DEBUG] tool.execute.before INPUT:", JSON.stringify(i, null, 2));
      console.log("[DEBUG] tool.execute.before OUTPUT:", JSON.stringify(o, null, 2));
    },
    "tool.execute.after": (i, o) => {
      console.log("[DEBUG] tool.execute.after INPUT:", JSON.stringify(i, null, 2));
      console.log("[DEBUG] tool.execute.after OUTPUT:", JSON.stringify(o, null, 2));
    },
    event: (e) => {
      if (e.type.startsWith('session.') || e.type.startsWith('message.')) {
        console.log(`[DEBUG] ${e.type}:`, JSON.stringify(e.properties, null, 2));
      }
    },
  };
};
```

---

## 七、与 openclaw 的差异

| 维度 | openclaw | observ-opencode |
|------|----------|-----------------|
| 事件来源 | 自定义 event emitter | OpenCode Plugin hooks/events |
| Session 管理 | 自己生成 | OpenCode 提供 session.id |
| Token 获取 | 从 LLM response 解析 | 从 `AssistantMessage.tokens` 获取 |
| Tool span | `before_tool_call`/`tool_result` | `tool.execute.before/after` |
| 进程模型 | 进程内插件 | 进程内插件（相同） |

---

## 八、下一步行动

1. **运行调试插件验证参数结构**
   ```bash
   cd opencode/observ-opencode
   # 创建调试插件，运行 OpenCode 验证
   ```

2. **创建最小可用版本**
   - 复制 openclaw/observ-openclaw 的 config.ts 和 telemetry.ts
   - 实现基本的 hooks.ts
   - 测试 OTLP 上报

3. **完善功能**
   - 完善错误处理
   - 添加配置文档
   - 对齐 Ant Spec v2.0 语义约定