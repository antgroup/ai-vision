# sofatrace — Claude Code Trace Context 注入 Demo

演示如何在 Spawn 和 SDK 两种调用方式下，将业务上下文（SOFA TraceId/RpcId、W3C TRACEPARENT）通过环境变量注入 Claude Code 进程，使 cchooks 能读取并写入 OTel span 属性。

## 注入的环境变量

| 变量 | 用途 | 写入的 span 属性 |
|------|------|-----------------|
| `SOFA_TRACEID` | SOFA TraceId | `_internal.sofa.trace_id` |
| `SOFA_RPCID` | SOFA RpcId | `_internal.sofa.span_id` |
| `TRACEPARENT` | W3C Trace Context | 复用上游 `trace_id` + `parent_span_id` |

`TRACEPARENT` 格式（W3C 标准）：
```
{version}-{trace_id}-{parent_span_id}-{trace_flags}
00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01
```

两种协议可以共存：TRACEPARENT 控制 OTel trace 树结构（trace_id + parent_span_id），SOFA 仅添加关联属性不影响 trace 树。

## 快速开始

```bash
cd claudecode/sofatrace
npm install

# Spawn 方式
npm run spawn -- "你好"

# SDK 方式
npm run sdk -- "你好"
```

## 两种调用方式对比

| 维度 | Spawn (`spawn.ts`) | SDK (`sdk.ts`) |
|------|-------------------|----------------|
| 调用方式 | `child_process.execFile` / `spawn` | `@anthropic-ai/claude-agent-sdk` 的 `query()` |
| env 注入 | `{ env: traceEnv }` 作为子进程环境 | `options.env` 传入 SDK |
| cchooks 触发 | 自动 — 子进程是完整 CLI，settings.json hooks 全部生效 | 自动 — `settingSources: ["user"]` 加载 settings.json 中的 env + hooks + permissions |
| 适用场景 | 简单集成、脚本调用、流式输出 | 结构化输出、自定义工具（MCP）、精细控制 |

**核心要点：两种方式都能自动触发 cchooks**，无需手动注册 JS hooks。

## Spawn 方式

通过 `child_process` 启动 Claude Code 子进程，环境变量通过 `env` 选项传入，settings.json 中的 cchooks hooks 自动生效。

```typescript
const traceEnv = {
  ...process.env,
  SOFA_TRACEID: `0a1b2c3d${Date.now()}...`,
  SOFA_RPCID: "0.1.2.3",
  SCENE: "spawn",
};

// execFile — JSON 输出
execFile("claude", ["-p", prompt, "--output-format", "json"], { env: traceEnv });

// spawn — 流式输出
spawn("claude", ["-p", prompt, "--output-format", "stream-json", "--verbose"], { env: traceEnv });
```

支持三种场景：
- **场景 A**：`execFile` + JSON 输出（一次性获取完整响应）
- **场景 B**：`execFile` + `--json-schema`（结构化输出）
- **场景 C**：`spawn` + `stream-json`（流式逐行输出）

**注意事项：**
- `...process.env` 确保继承 PATH、HOME 等系统环境变量
- **禁止** `--bare` 参数 — 会跳过 settings.json 加载，cchooks hooks 全部失效
- **禁止** `--no-session-persistence` 参数 — 会阻止生成 transcript，cchooks 无法读取会话数据

## SDK 方式

通过 `@anthropic-ai/claude-agent-sdk` 的 `query()` 在当前进程内调用。关键配置是 `settingSources: ["user"]`，它会自动加载 `~/.claude/settings.json` 中的**全部配置**（env、hooks、permissions），shell hooks 和 SDK 内的逻辑并行运行。

```typescript
import { query, tool, createSdkMcpServer } from "@anthropic-ai/claude-agent-sdk";

function createTraceEnv(): Record<string, string> {
  return {
    ...Object.fromEntries(
      Object.entries(process.env).filter((e): e is [string, string] => e[1] != null),
    ),
    SOFA_TRACEID: `0a1b2c3d${Date.now()}...`,
    SOFA_RPCID: "0.1.2.3",
    SCENE: "sdk",
  };
}

for await (const message of query({
  prompt,
  options: {
    env: createTraceEnv(),
    settingSources: ["user"],  // 自动加载 settings.json（env + hooks + permissions）
    permissionMode: "dontAsk",
    maxTurns: 3,
  },
})) { /* ... */ }
```

支持三种场景：
- **场景 A**：基础调用（`query` + `for await`）
- **场景 B**：结构化输出（`outputFormat: { type: "json_schema", schema }`）
- **场景 C**：自定义工具（`tool()` + `createSdkMcpServer()`）

## TRACEPARENT 与 SOFA 属性的工作原理

cchooks 在 `Stop` 事件中处理 transcript 并生成 OTel span 时：

1. **TRACEPARENT 传播** — 解析 W3C TRACEPARENT 环境变量，非 cancelled turn 的 `traceId` 使用上游 `trace_id`，root span 的 `parentSpanId` 设为上游 `parent_span_id`。Cancelled turn（用户中断）保持随机 `traceId`，不污染上游链路。

2. **SOFA 属性注入** — 读取 `SOFA_TRACEID` 和 `SOFA_RPCID` 环境变量，写入 turn root span 的 `_internal.sofa.trace_id` 和 `_internal.sofa.span_id` 属性。

实现代码在 `cchooks/src/common/otel.rs` 的 `collect_turn_spans()` 中：

```rust
// TRACEPARENT 解析（共享 crate agentic-otel-common）
let traceparent = agentic_otel_common::parse_traceparent_env();
let (trace_id, upstream_parent_span_id) = if !turn.cancelled {
    if let Some(ref tp) = traceparent {
        (tp.trace_id.clone(), tp.parent_span_id.clone())
    } else {
        (otel_trace_id(), String::new())
    }
} else {
    (otel_trace_id(), String::new())  // cancelled turn 不使用上游 context
};

// SOFA 属性注入
if let Ok(sofa_traceid) = std::env::var("SOFA_TRACEID") {
    root_span.attributes.push(
        OtelAttribute::string("_internal.sofa.trace_id", &sofa_traceid)
    );
}
if let Ok(sofa_rpcid) = std::env::var("SOFA_RPCID") {
    root_span.attributes.push(
        OtelAttribute::string("_internal.sofa.span_id", &sofa_rpcid)
    );
}
```

## 验证

```bash
# Spawn 方式
npm run spawn -- "你好"

# SDK 方式
npm run sdk -- "你好"

# 对比测试
npx tsx src/test-both.ts

# 检查 Langfuse 输出中的 SOFA 属性
grep "_internal.sofa" ~/.aivision/cc/replays/*.jsonl

# 检查 trace_id 是否来自 TRACEPARENT
grep '"traceId"' ~/.aivision/cc/replays/*.jsonl | head -5
```

## 文件结构

```
sofatrace/
├── src/
│   ├── spawn.ts          # Spawn 方式：execFile/spawn + env 继承
│   ├── sdk.ts            # SDK 方式：query() + settingSources + env
│   ├── test-both.ts      # 两种方式对比测试
│   └── shared/
│       └── types.ts      # 共享类型定义（ClaudeResponse、analysisSchema）
├── .env.example          # 环境变量模板
├── package.json
├── tsconfig.json
└── README.md
```
