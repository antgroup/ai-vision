# Codex 下游工具串联方案

## 背景

### 当前架构

cdxhooks 是 Codex 的可观测性 hook 插件，在 Stop 事件时将会话的完整轨迹构建为 OTel span 并上报到 Langfuse（OTLP 后端）。当前链路：

```
Codex CLI
  └─ transcript JSONL（持久化文件）
       │
       │ Stop hook 触发
       ▼
  cdxhooks（Rust）
       │ 增量读取 transcript
       │ 解析 Turn → 构建 OTel Span
       │ POST to Langfuse
       ▼
  Langfuse → AI Vision 看板
```

### 核心问题

当 Codex 调用 Bash 工具执行用户脚本（如 `python train.py`、`./deploy.sh`）时，**脚本内部的操作无法关联到 cdxhooks 构建的 trace 链路中**。

```
Codex 主进程
├── fork → cdxhooks（hook 进程，旁观者）
└── fork → python train.py（工具子进程，独立运行）
```

cdxhooks 和工具子进程是 Codex 主进程的**兄弟子进程**，无法直接通信。hook 进程无法修改主进程的环境变量，也无法直接向工具进程传递 trace context。

### 实际场景

| 场景 | 描述 | 典型用例 |
| --- | --- | --- |
| 场景 1 | 用户直接使用 Codex，无上游服务 | Codex 运行 `python train.py`，期望看到训练脚本内部的耗时分布 |
| 场景 2 | 上游 SOFA/Java 服务启动 Codex，传递了 TRACEPARENT | 业务服务调用 Codex 执行代码审查，期望整个链路从服务 → Codex → 脚本串联 |

### 解决思路

借鉴 Claude Code cchooks 的工具串联方案，利用 Codex PreToolUse hook 的 `updatedInput` 能力，在 Bash 工具命令前注入环境变量，使工具子进程自动继承 trace context。

**关键依据：** OpenAI Codex 官方文档（2026年6月）已正式支持 PreToolUse 的 `updatedInput`：

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "allow",
    "updatedInput": {
      "command": "export TRACEPARENT=...; python train.py"
    }
  }
}
```

## 方案设计

### 整体架构

```
┌──────────────────────────────────────────────────────────────────┐
│ Codex 主进程                                                      │
│                                                                  │
│  UserPromptSubmit ──┐                                            │
│    turn_id, prompt   │ stdin(json)                                │
│                     ▼                                             │
│              ┌──────────────┐                                     │
│              │   cdxhooks    │                                     │
│              │ ToolContext   │                                     │
│              │  Handler      │                                     │
│              │               │                                     │
│              │ 轮转 trace    │  ← 解决 ESC 中断后恢复               │
│              │ context 到    │                                     │
│              │ history 列表  │                                     │
│              └──────────────┘                                     │
│                                                                  │
│  PreToolUse(Bash) ──┐                                            │
│    tool_use_id       │ stdin(json)                                │
│    tool_name         ▼                                            │
│    tool_input {      ┌──────────────┐                             │
│      command: "..."   │   cdxhooks    │                             │
│    }                 │ ToolContext   │                             │
│                      │  Handler      │                             │
│                      │               │                             │
│                      │ 1. 快照 env:  │                             │
│                      │   TRACEPARENT │                             │
│                      │   SOFA_*      │                             │
│                      │ 2. trace_id:  │                             │
│                      │   state 已有  │                             │
│                      │   || env 解析 │                             │
│                      │   || 随机生成 │                             │
│                      │ 3. span_id:   │                             │
│                      │   hash(       │                             │
│                      │   tool_use_id │                             │
│                      │   )           │                             │
│                      │ 4. 存入 state │                             │
│                      │   trace_id +  │                             │
│                      │   上游 parent │                             │
│                      │ 5. 返回       │                             │
│                      │  updatedInput │                             │
│                      └──────┬───────┘                             │
│                      stdout: hookSpecificOutput                   │
│                             { updatedInput: { command: "..." } }  │
│                             ▼                                     │
│  Codex 用 updatedInput 替换原始命令                               │
│                     │                                            │
│                     ▼                                            │
│  fork+exec:                                                      │
│  "export TRACEPARENT=00-{trace_id}-{span_id}-01;                 │
│   export AGENTIC_TOOL_USE_ID={tool_use_id};                      │
│   export SOFA_TRACEID=...;                                       │
│   export SOFA_RPCID=...;                                         │
│   export OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=...;                 │
│   export OTEL_EXPORTER_OTLP_AUTH=...;                            │
│   export OTEL_EXPORTER_OTLP_HEADERS=...;                         │
│   export AGENTIC_SPAN_ATTRIBUTES=...;                            │
│   export OTEL_RESOURCE_ATTRIBUTES=...;                           │
│   python train.py"                                               │
│              ┌──────┴───────┐                                     │
│              │ python train  │                                     │
│              │ 读取环境变量   │                                     │
│              │ 构建 child    │                                     │
│              │ span 并上报   │                                     │
│              └──────────────┘                                     │
│                                                                  │
│  Stop → cdxhooks 构建 trace                                      │
│         trace_id = state 或 TRACEPARENT                          │
│         root parentSpanId = 上游 TRACEPARENT parent_span_id       │
│         root attrs += _internal.sofa.*                           │
│         tool span_id = hash(tool_use_id) ← 与 PreToolUse 一致    │
│         清除 current_trace_context + trace_context_history        │
└──────────────────────────────────────────────────────────────────┘
```

### 三个核心技巧

#### 技巧 1：`updatedInput` 注入环境变量

利用 Codex PreToolUse 的 `updatedInput`，在原始命令前直接注入经过 `shell_escape()` 转义的 `export` 语句；工具子进程 fork+exec 时自动继承这些环境变量。这是 Codex 官方支持的**唯一通道**让 hook 修改工具输入。

#### 技巧 2：`span_id = hash(tool_use_id)` 确定性生成

PreToolUse 时注入的 `TRACEPARENT` 中的 `parent_span_id` 必须和 Stop 时构建的 tool span 的 `spanId` 一致，否则工具脚本的 child span 无法挂到正确的父 span 上。

cdxhooks 在两个阶段使用**相同的确定性哈希**：

```
span_id = sha256("cdxhooks-tool-span-id:" + tool_use_id)[0..8 bytes]  // 16 hex chars
```

- **PreToolUse**（ToolContextHandler）：生成 span_id → 注入到 `TRACEPARENT` 的 `parent_span_id` 位置
- **Stop**（LangfuseHandler → `build_tool_span()`）：用同样的 `hash(tool_use_id)` 生成 span_id

两阶段独立计算、数学收敛，无需进程间通信。

#### 技巧 3：UserPromptSubmit 轮转 trace context

解决 ESC 中断（turn_aborted）场景：用户按 ESC 中断后，state 中残留上一次的 `current_trace_context`，下一个 turn 如果不处理，会错误复用上一个 turn 的 trace_id。

```
Turn 1: PreToolUse → trace_id=A → ESC（无 Stop）→ state 残留
Turn 2: UserPromptSubmit → push current 到 history
       → state: current=null, history=[{trace_id:A}]
       PreToolUse → 生成 trace_id=B
       → state: current={trace_id:B}, history=[{trace_id:A}]
       Stop → Turn 1 用 trace_id=A, Turn 2 用 trace_id=B ✅
```

### State 结构

```json
{
  "session-id": {
    "current_trace_context": {
      "trace_id": "abc123...",
      "traceparent": "00-abc123...-def456...-01",
      "upstream_parent_span_id": "b7ad6b7169203331",
      "sofa_traceid": "0a1b2c...",
      "sofa_rpcid": "0.1.2"
    },
    "trace_context_history": [
      {"trace_id": "...", "traceparent": "...", "upstream_parent_span_id": "...", "sofa_traceid": "...", "sofa_rpcid": "..."}
    ]
  }
}
```

### 注入的环境变量

| 环境变量 | 内容 | 用途 |
| --- | --- | --- |
| `TRACEPARENT` | `00-{trace_id}-{span_id}-01` | 标准 W3C traceparent，工具脚本据此构建 child span |
| `AGENTIC_TOOL_USE_ID` | Codex 的 tool_use_id | 通用 agentic 工具调用标识 |
| `SOFA_TRACEID` | 上游 SOFA_TRACEID（如有） | 透传给工具脚本及其子进程 |
| `SOFA_RPCID` | 上游 SOFA_RPCID（如有） | 透传给工具脚本及其子进程 |
| `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` | OTLP traces endpoint | 工具脚本上报地址 |
| `OTEL_EXPORTER_OTLP_AUTH` | Base64 Basic auth | 工具脚本上报认证 |
| `OTEL_EXPORTER_OTLP_HEADERS` | JSON 自定义 headers | 如 `x-antmonitor-forward-service` |
| `AGENTIC_SPAN_ATTRIBUTES` | JSON span 级属性 | `user.id`, `ant.username`, `gen_ai.conversation.id` 等 |
| `OTEL_RESOURCE_ATTRIBUTES` | JSON resource 级属性 | `ant.agent.product`, `ant.agent.type` 等 |

所有环境变量值都通过 `shell_escape()` 转义后直接写入 `updatedInput.command`。这保证原始命令无需修改即可继承完整 OTel 上下文；同时也意味着 OTLP 鉴权信息会出现在 hook 改写后的命令中，调用方不得记录或展示完整的 `updatedInput.command`。为降低 Bash 展示侵入，cdxhooks 只注入与 cchooks 对齐的通用字段，不再注入 `CDXHOOKS_TOOL_*` 专用副本。

### 上游 TRACEPARENT / SOFA 处理

与已上线的 cchooks 对齐：

- `TRACEPARENT` 控制 OTel 树结构：cdxhooks 复用上游 `trace_id`，并把 turn root span 的 `parentSpanId` 设置为上游 `parent_span_id`。
- `SOFA_TRACEID` / `SOFA_RPCID` 只作为关联属性写到 turn root span：`_internal.sofa.trace_id` / `_internal.sofa.span_id`，不改变 OTel 树结构。
- Bash `PreToolUse` 会将上游 `parent_span_id` 保存到 state；即使没有触发 Bash tool context，Stop 构建 turn 时也会回退读取当前进程环境里的 `TRACEPARENT` / `SOFA_*`。

### Hook 事件订阅

ToolContextHandler 订阅两个事件：

| 事件 | 用途 | matcher |
| --- | --- | --- |
| `PreToolUse` | 注入 trace context 到 Bash 工具命令 | `Bash` |
| `UserPromptSubmit` | 在新 prompt 开始时轮转 trace context（ESC 中断恢复） | 不适用（Codex 不支持 matcher） |

### 运行时控制

| 控制方式 | 名称 | 默认值 | 说明 |
| --- | --- | --- | --- |
| 编译时 feature | `tool_context` | 启用 | 未启用时所有相关代码不编译 |
| 运行时环境变量 | `CDXHOOKS_TOOL_CONTEXT` | `false` | 显式设为 `true` / `1` / `yes` 才注册 handler 并改写 Bash 命令 |

## 代码变更清单

### 新增文件

| 文件 | 说明 |
| --- | --- |
| `cdxhooks/src/hooks/tool_context.rs` | ToolContextHandler：PreToolUse + UserPromptSubmit 处理 |

### 修改文件

| 文件 | 改动 |
| --- | --- |
| `cdxhooks/Cargo.toml` | 新增 `tool_context` feature |
| `cdxhooks/src/hooks/mod.rs` | `#[cfg(feature = "tool_context")]` 注册模块 |
| `cdxhooks/src/main.rs` | `#[cfg]` 守护 + `CDXHOOKS_TOOL_CONTEXT` 运行时开关注册 handler |
| `cdxhooks/src/common/otel.rs` | 新增 `deterministic_tool_span_id()`；`build_tool_span()` 支持确定性 span_id；`build_otel_export_request_for_turn()` 支持上游 trace id、parent span id、SOFA root attrs |
| `cdxhooks/src/hooks/langfuse/langfuse_handler.rs` | Stop 时从 state / env 构建 `TurnReplayOpts`；Stop 后清除 trace state |
| `cdxhooks/src/hooks/langfuse/state.rs` | 新增 `current_trace_context` / `trace_context_history` 字段的读写函数，保存上游 parent span id 和 SOFA 变量 |

### settings.json 配置

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "CDXHOOKS_TOOL_CONTEXT=true cdxhooks",
            "timeout": 15
          }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "CDXHOOKS_TOOL_CONTEXT=true cdxhooks",
            "timeout": 5
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "cdxhooks",
            "timeout": 30
          }
        ]
      }
    ]
  }
}
```

## 工具脚本侧示例

工具脚本（如 Python）读取环境变量，构建 child span 并上报：

```python
#!/usr/bin/env python3
"""工具脚本：读取 TRACEPARENT 构建 child span 并上报到 OTLP"""
import os
import json
import time
import requests
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

# 1. 读取注入的环境变量
traceparent = os.environ.get("TRACEPARENT", "")
endpoint = os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "")
tool_use_id = os.environ.get("AGENTIC_TOOL_USE_ID", "")

if not traceparent or not endpoint:
    print("No trace context, running without observability")
    # ... 正常业务逻辑 ...
    exit(0)

# 2. 解析 TRACEPARENT 获取 trace_id 和 parent_span_id
propagator = TraceContextTextMapPropagator()
carrier = {"traceparent": traceparent}
ctx = propagator.extract(carrier=carrier)

# 3. 配置 OTLP exporter
exporter = OTLPSpanExporter(endpoint=endpoint)
processor = BatchSpanProcessor(exporter)
provider = TracerProvider()
provider.add_span_processor(processor)
trace.set_tracer_provider(provider)

tracer = trace.get_tracer(__name__)

# 4. 构建 child span（继承上游 trace_id 和 parent_span_id）
with tracer.start_as_current_span("train.py", context=ctx) as span:
    span.set_attribute("agentic.tool.use_id", tool_use_id)
    # ... 业务逻辑 ...
    time.sleep(5)
    # span 自动上报
```

## 限制与约束

| 限制 | 说明 |
| --- | --- |
| **仅支持 Bash 工具** | Read/Write/WebSearch 等非 Bash 工具不执行用户脚本，无需注入 |
| **span_id 碰撞可忽略** | tool_use_id 由 Codex 保证唯一，SHA-256 前 8 字节碰撞概率可忽略 |
| **无 Bash 调用的 turn** | ToolContextHandler 不触发；若进程环境有 `TRACEPARENT` / `SOFA_*`，Stop 仍会按 cchooks 方式接入上游链路，否则回退到随机 trace_id（兼容现有行为） |
| **确定性 hash 稳定性** | SHA-256 固定前缀 + tool_use_id，跨进程、跨 Rust 版本稳定 |
| **Shell 安全** | 所有注入值通过 `shell_escape()` 转义；不得记录包含 OTLP 鉴权信息的完整 `updatedInput.command` |
| **工具脚本需引入 OTel SDK** | 脚本本身需要读取环境变量并构建 child span，不在 cdxhooks 职责范围内 |

## 验证要点

- [ ] `CDXHOOKS_TOOL_CONTEXT=true` 时 Bash `updatedInput.command` 才被改写；默认不侵入 Bash
- [ ] `TRACEPARENT` 非空
- [ ] `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` 指向正确的 OTLP 后端
- [ ] 工具脚本的 child span 上报成功（HTTP 200）
- [ ] child span 的 `parent_span_id` 与 cdxhooks trace 中 `tool Bash` span 的 `spanId` 一致
- [ ] child span 的 `trace_id` 与 cdxhooks trace 的 `trace_id` 一致
- [ ] 同一 session 不同 turn 的 trace_id 不同（场景 1：无上游 TRACEPARENT）
- [ ] 场景 2：有上游 TRACEPARENT 时，整个链路（上游服务 → Codex → 脚本）trace_id 一致
- [ ] 场景 2：turn root span 的 `parentSpanId` 等于上游 TRACEPARENT 的 `parent_span_id`
- [ ] ESC 中断后下一个 turn 的工具 span 不会混入前一个 turn 的 trace
- [ ] 有上游 SOFA_TRACEID 时，工具脚本能读到 `SOFA_TRACEID`
- [ ] 有上游 SOFA_TRACEID / SOFA_RPCID 时，turn root span 写入 `_internal.sofa.trace_id` / `_internal.sofa.span_id`

### 场景 2：本地 mock upstream 测试

场景 2 不要求搭建真实 SOFA/Java 服务。仓库提供一个仅依赖 Python 标准库的
mock upstream，它作为 Codex 的父进程注入 `TRACEPARENT` / `SOFA_*`，再验证
`PreToolUse.updatedInput` 执行后的 Bash 子进程是否收到正确上下文：

```bash
cd codex
make test-tool-context-upstream
```

该测试不调用模型、不访问网络，覆盖：

- cdxhooks 复用上游 `trace_id`
- state 保存上游 `parent_span_id`
- Bash 工具使用确定性的 tool `span_id`
- `TRACEPARENT` / `SOFA_*` / tool use id 传入子进程
- OTLP endpoint、span/resource attributes 传入子进程
- 改写命令只包含环境变量 `export` 与原始命令，不包含临时文件或删除命令

需要在看板验证完整的“上游服务 → Codex → Bash 脚本”拓扑时，用同一脚本包装
真实 Codex 命令，并把 synthetic upstream span 上报到与 cdxhooks 相同的 OTLP endpoint：

```bash
cd codex
python3 cdxhooks/tests/mock_upstream_tool_context.py run \
  --endpoint "$OTEL_EXPORTER_OTLP_TRACES_ENDPOINT" \
  --output replays/mock_upstream.jsonl \
  -- codex exec "请使用 Bash 执行 python3 train.py"
```

如果不希望 mock 脚本直接上报，可以省略 `--endpoint`，然后复用项目现有的
report 流程发送生成的 upstream replay：

```bash
make report OTEL_JSON=replays/mock_upstream.jsonl
```

live 模式生成真实且匹配的 upstream `trace_id/span_id`，因此看板中应呈现：

```text
mock-upstream.invoke-codex
└── Codex turn
    ├── generation
    └── Bash tool
        └── train.py
```
