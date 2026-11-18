# Claude Code 工具调用 Trace 串联方案

## 背景

cchooks 是 Claude Code 的可观测性 hook 插件，在 Stop 事件时将会话的完整轨迹构建为 OTel span 并上报到 OTLP 后端。但当 Claude Code 调用 Bash 工具执行用户脚本（如 `python hello.py`）时，脚本内部的操作无法关联到 cchooks 构建的 trace 链路中。

### 核心问题

cchooks（hook 进程）和工具脚本（tool 子进程）是 Claude Code 主进程的**兄弟子进程**，无法直接通信：

```
Claude Code 主进程
├── fork → cchooks（hook 进程，旁观者）
└── fork → python hello.py（工具子进程）
```

hook 进程无法修改主进程的环境变量或内存，也无法直接向工具进程传递信息。

### 两个场景

| 场景 | 描述 | trace_id 来源 |
|------|------|-------------|
| 场景 1 | 上游**不传递** TRACEPARENT 环境变量 | UserPromptSubmit 轮转旧 context → history；首次 PreToolUse 时生成新 trace_id，存入 state |
| 场景 2 | 上游**传递**了 TRACEPARENT 环境变量 | UserPromptSubmit 轮转旧 context → history；首次 PreToolUse 时从 env 快照 TRACEPARENT + SOFA_* 到 state |

### 核心难点

#### 难点 1：trace_id 生成时机前置

cchooks 的原有设计中，trace_id 在 **Stop hook 触发时才生成**（`collect_turn_spans()` → `otel_trace_id()`）。这对纯旁观式采集没有问题，但工具调用串联要求在工具执行**之前**就确定 trace_id——工具脚本需要在运行时读取 TRACEPARENT 并据此构建 child span。

如果 trace_id 在 Stop 时才生成，工具脚本执行时无法获取未来的 trace_id，child span 与最终 trace 无法关联。

**解决方案**：将 trace_id 的生成时机从 Stop 前置到 PreToolUse。首次 PreToolUse 时生成 trace_id 并存入 cchooks state 文件，后续同一 turn 内的 PreToolUse 复用。Stop 时从 state 读取预生成的 trace_id 而非重新随机生成，保证两阶段使用同一个 trace_id。通过 UserPromptSubmit hook 在每个新 prompt 开始时轮转旧 trace context 到 history 列表，确保不同 turn 之间 trace_id 独立，同时支持 ESC 中断后的多 turn 恢复。

#### 难点 2：tool span_id 的父子对齐

工具脚本上报的 child span 需要通过 `parent_span_id` 指向 cchooks 构建的 tool span。但 cchooks 的 tool span_id 原来是在 Stop 时**随机生成**的（`otel_span_id()`），工具脚本在执行时无法预知这个随机值。

直接传递 span_id 存在进程隔离问题——hook 进程和工具进程是 Claude Code 的兄弟子进程，无法直接通信。最初考虑的"文件中介"方案被否定，因为它只解决信息传递，不解决如何让两侧独立生成一致的 ID。

**解决方案**：将 tool span_id 从随机生成改为**确定性哈希**——`span_id = hash(tool_use_id)`。tool_use_id 是 Claude API 为每次工具调用分配的唯一标识符，在 PreToolUse payload 中可获取。cchooks 在 PreToolUse 注入 TRACEPARENT 时用 `hash(tool_use_id)` 生成 span_id，在 Stop 构建 trace 时的 `build_tool_spans()` 也用同样的 `deterministic_tool_span_id(tool_use_id)` 生成 span_id。两阶段独立计算、数学收敛，无需进程间通信即可保证 parent_span_id 与 span_id 一致。

#### 难点 3：有无上游 TRACEPARENT 的统一适配

两种场景的 trace_id 来源完全不同，但工具脚本侧的行为应保持一致（读取 `TRACEPARENT` 即可）：

- **有上游 TRACEPARENT**（场景 2）：trace_id 来自环境变量中已有的 W3C traceparent，工具脚本的 child span 需要挂到上游 trace 下
- **无上游 TRACEPARENT**（场景 1）：trace_id 需要自行生成，并与 Stop 时构建的 trace 保持一致

两者不能简单用 `if/else` 切换——还需要考虑上游同时传递 SOFA_TRACEID / SOFA_RPCID 等非 W3C 标准的 trace context，以及 ESC 中断后恢复时需要还原当时的上游上下文。

**解决方案**：PreToolUse 时统一快照当前所有 trace 相关环境变量（TRACEPARENT、SOFA_TRACEID、SOFA_RPCID）到 state 的 `current_trace_context` 对象中。无论场景 1 还是场景 2，都走相同的 state 存储和读取路径。trace_id 的具体来源在 PreToolUse 内部通过优先级链解析（state 已有 → TRACEPARENT env → 随机生成），对下游的工具脚本和 Stop 时的 trace 构建完全透明。ESC 中断时，UserPromptSubmit 将完整的 context 对象（含上游 SOFA 信息）推入 history 列表，Stop 处理多 turn 批次时按倒序匹配，每个 turn 都能恢复到其执行时的上游上下文。

## 方案

### 核心机制：PreToolUse `updatedInput` 注入

利用 Claude Code PreToolUse hook 的 `hookSpecificOutput.updatedInput` 能力，在 Bash 工具命令前注入环境变量，使工具子进程自动继承 trace context。

这是 Claude Code 提供的**唯一官方通道**让 hook 修改工具输入。

### Hook 事件订阅

ToolContextHandler 订阅两个事件：

| 事件 | 用途 |
|------|------|
| `PreToolUse` | 注入 trace context 到 Bash 工具命令 |
| `UserPromptSubmit` | 在新 prompt 开始时轮转 trace context（解决 ESC 中断问题） |

### 数据流

```
┌──────────────────────────────────────────────────────────┐
│ Claude Code 主进程                                        │
│                                                          │
│  UserPromptSubmit ──┐                                    │
│                     │ stdin(json)                        │
│                     ▼                                    │
│              ┌─────────────┐                             │
│              │   cchooks    │                             │
│              │ ToolContext  │                             │
│              │  Handler     │                             │
│              │              │                             │
│              │ current_trace│                             │
│              │ _context     │                             │
│              │ → push to    │                             │
│              │ history list │                             │
│              └─────────────┘                             │
│                                                          │
│  PreToolUse(Bash) ──┐                                    │
│    tool_use_id      │ stdin(json)                        │
│    tool_name        ▼                                    │
│              ┌─────────────┐                             │
│              │   cchooks    │                             │
│              │ ToolContext  │                             │
│              │  Handler     │                             │
│              │              │                             │
│              │ 1. 快照env:  │                             │
│              │   TRACEPARENT│                             │
│              │   SOFA_*     │                             │
│              │ 2. trace_id: │                             │
│              │   env/state  │                             │
│              │ 3. span_id:  │                             │
│              │   hash(      │                             │
│              │   tool_use_id│                             │
│              │   )          │                             │
│              │ 4. 存state:  │                             │
│              │  current_    │                             │
│              │  trace_      │                             │
│              │  context     │                             │
│              │ 5. 返回      │                             │
│              │  updatedInput│                             │
│              └──────┬───────┘                             │
│                     │ stdout: hookSpecificOutput          │
│                     ▼                                    │
│  Claude Code 用 updatedInput 替换原始命令                  │
│                     │                                    │
│                     ▼                                    │
│  fork+exec:                                              │
│  "export TRACEPARENT=...                    │
│   AGENTIC_TOOL_USE_ID=...                                │
│   SOFA_TRACEID=...                          │
│   SOFA_RPCID=...                            │
│   OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=...                         │
│   OTEL_EXPORTER_OTLP_AUTH=...                             │
│   OTEL_EXPORTER_OTLP_HEADERS=...                          │
│   AGENTIC_SPAN_ATTRIBUTES=...                            │
│   OTEL_RESOURCE_ATTRIBUTES=...;                       │
│   python hello.py"                                       │
│              ┌──────┴───────┐                             │
│              │ python hello  │                             │
│              │ 读取环境变量   │                             │
│              │ 构建child span│                             │
│              │ 上传到同一后端 │                             │
│              └──────────────┘                             │
│                                                          │
│  Stop → cchooks 构建 trace                                │
│         trace_id = state 中的 trace_context_history       │
│         tool span_id = hash(tool_use_id) ← 与注入一致     │
│         清除 current_trace_context + trace_context_history│
└──────────────────────────────────────────────────────────┘
```

### 注入的环境变量

| 环境变量 | 内容 | 用途 |
|---------|------|------|
| `TRACEPARENT` | `00-{trace_id}-{span_id}-01` | W3C traceparent，工具脚本据此构建 child span |
| `AGENTIC_TOOL_USE_ID` | Claude Code 的 tool_use_id | 工具调用唯一标识 |
| `SOFA_TRACEID` | 上游 SOFA_TRACEID（如有） | SOFA 链路追踪关联 |
| `SOFA_RPCID` | 上游 SOFA_RPCID（如有） | SOFA RPC 调用链关联 |
| `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` | OTLP traces endpoint URL | 工具脚本上报地址 |
| `OTEL_EXPORTER_OTLP_AUTH` | Base64 Basic auth | 上报认证 |
| `OTEL_EXPORTER_OTLP_HEADERS` | JSON 自定义 headers | 如 `x-antmonitor-forward-service` |
| `AGENTIC_SPAN_ATTRIBUTES` | JSON span 级属性 | `user.id`, `ant.username`, `gen_ai.conversation.id` 等 |
| `OTEL_RESOURCE_ATTRIBUTES` | JSON resource 级属性 | `service.name`, `ant.agent.product`, `ant.agent.type` 等 |

所有环境变量值均通过 `shell_escape()` 函数进行单引号转义，防止 shell 注入。

### span_id 一致性

cchooks 在两个阶段使用**相同的确定性 hash** 生成 tool span_id：

```
span_id = hash(tool_use_id)   // DefaultHasher, 截取为 16 hex chars
```

- **PreToolUse**（ToolContextHandler）：生成 span_id → 注入到 TRACEPARENT
- **Stop**（LangfuseHandler → `build_tool_spans`）：用同样的 `deterministic_tool_span_id(tool_use_id)` 生成 span_id

这保证了工具脚本的 child span 的 `parent_span_id` 与 cchooks 最终构建的 tool span 的 `spanId` 一致。

### trace_id 一致性与 ESC 中断恢复

trace context 通过 state 文件管理，支持 ESC 中断后跨 turn 恢复。

#### State 中的 trace context 结构

```json
{
  "session-id": {
    "current_trace_context": {
      "trace_id": "abc123...",
      "traceparent": "00-abc123...-def456...-01",
      "sofa_traceid": "0a1b2c...",
      "sofa_rpcid": "0.1.2"
    },
    "trace_context_history": [
      {"trace_id": "...", "traceparent": "...", "sofa_traceid": "...", "sofa_rpcid": "..."},
      {"trace_id": "...", "traceparent": null, "sofa_traceid": null, "sofa_rpcid": null}
    ]
  }
}
```

#### 正常流程（无 ESC）

```
Turn 1:
  PreToolUse① → state 无 current_trace_context
    → 快照 env(TRACEPARENT, SOFA_*) + 生成 trace_id → 存 state
  PreToolUse② → state 有 current_trace_context → 复用
  Stop → 从 state 读 trace_id → 构建 trace → 清除 current + history
```

#### ESC 中断流程

```
Turn 1:
  PreToolUse → 生成 trace_id_A → state: current={trace_id:A, ...}, history=[]
  用户按 ESC → 无 Stop → state 残留

Turn 2:
  UserPromptSubmit → 将 current 推入 history
    → state: current=null, history=[{trace_id:A, ...}]
  PreToolUse → 生成 trace_id_B
    → state: current={trace_id:B, ...}, history=[{trace_id:A, ...}]
  Stop → 处理 2 个 turn:
    合并 history: [trace_id_A, trace_id_B]
    Turn 1 → trace_id_A ✅（与 Turn 1 工具脚本的 TRACEPARENT 一致）
    Turn 2 → trace_id_B ✅
  cleanup → 清除 current + history
```

#### 多次 ESC 中断流程

```
Turn 1: PreToolUse → trace_id_A → ESC（无 Stop）
Turn 2: UserPromptSubmit → push A → PreToolUse → trace_id_B → ESC
Turn 3: UserPromptSubmit → push B → PreToolUse → trace_id_C → Stop
  history = [A, B, C]
  Turn 1 → trace_id_A ✅
  Turn 2 → trace_id_B ✅
  Turn 3 → trace_id_C ✅
```

### Feature Flag 与运行时开关

| 控制方式 | 名称 | 默认值 | 说明 |
|---------|------|--------|------|
| 编译时 feature | `tool_context` | 启用（包含在 `common` 中，所有 edition 默认启用） | 未启用时所有相关代码不编译 |
| 运行时环境变量 | `CCHOOKS_TOOL_CONTEXT` | `false` | 设为 `true` 启用 handler 注册 |

## 代码结构

### 新增/修改的文件

| 文件 | 改动 |
|------|------|
| `cchooks/Cargo.toml` | 新增 `tool_context` feature，`common` 包含它 |
| `cchooks/src/hooks/tool_context.rs` | **新增** ToolContextHandler（PreToolUse + UserPromptSubmit） |
| `cchooks/src/hooks/mod.rs` | `#[cfg(feature = "tool_context")]` 注册模块 |
| `cchooks/src/main.rs` | `#[cfg]` 守护 + `CCHOOKS_TOOL_CONTEXT` 运行时开关注册 handler |
| `cchooks/src/common/otel.rs` | `deterministic_tool_span_id()`；`collect_turn_spans` 支持 `pregenerated_trace_id`；`build_otel_export_request_with_subagent_timing` 支持 `trace_id_history` |
| `cchooks/src/hooks/timing.rs` | `cleanup_timing()` 清除 `current_trace_context` + `trace_context_history` |
| `cchooks/src/hooks/langfuse/langfuse_handler.rs` | Stop 时从 state 构建 `trace_id_history`；StopFailure 时清理 trace state |
| `scripts/tool_context.py` | POC Python 脚本，读取环境变量构建 child span 并上报 |

### ToolContextHandler 处理逻辑

```rust
// UserPromptSubmit: 轮转 trace context（ESC 恢复）
if event == UserPromptSubmit {
    current_trace_context → push to trace_context_history
    return;
}

// PreToolUse: 仅处理 Bash 工具
if tool_name != "Bash" { return; }

// 快照上游环境变量
let traceparent = env("TRACEPARENT");
let sofa_traceid = env("SOFA_TRACEID");
let sofa_rpcid = env("SOFA_RPCID");

// trace_id: 复用 state 中的 current，或从 TRACEPARENT 解析，或随机生成
let trace_id = state.current_trace_context?.trace_id
    ?? parse(traceparent)?.trace_id
    ?? otel_trace_id();

// 存入 state（含完整上游上下文快照）
state.current_trace_context = { trace_id, traceparent, sofa_traceid, sofa_rpcid };

// span_id 确定性生成
let span_id = hash(tool_use_id);

// 组装环境变量（shell_escape 转义）+ updatedInput
println!(hookSpecificOutput JSON);
```

### Edition 兼容

OTLP endpoint、auth、headers 的解析兼容不同 edition：

| Edition | endpoint 来源 | auth 来源 |
|---------|-------------|----------|
| aivision (默认) | 编译时注入 (`aivision.keys.json`) | 编译时注入 |
| default | 运行时环境变量 | 运行时环境变量 |

ToolContextHandler 通过 `context.langfuse_config` 获取已解析的配置，透明支持所有 edition。

### Hook 注册

`~/.claude/settings.json` 中需要注册以下事件：

```json
{
  "hooks": {
    "PreToolUse": [{"hooks": [{"command": "~/.aivision/cc/hooks/cchooks", "timeout": 15}]}],
    "UserPromptSubmit": [{"hooks": [{"command": "~/.aivision/cc/hooks/cchooks", "timeout": 5}]}],
    "PostToolUse": [...],
    "Stop": [...],
    "StopFailure": [...],
    "SessionStart": [...],
    "SubagentStop": [...]
  }
}
```

## 测试

### 场景 2（有上游 TRACEPARENT）

```bash
TRACEPARENT="00-aaaabbbbccccddddeeee111122223333-1234567890abcdef-01" \
  claude -p "运行 python3 claudecode/scripts/tool_context.py"
```

### 场景 1（无上游 TRACEPARENT）

```bash
claude -p "运行 python3 claudecode/scripts/tool_context.py"
```

### 验证要点

- [ ] `TRACEPARENT` 非空
- [ ] `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` 指向正确的 OTLP 后端
- [ ] tool_context.py 的 child span 上报成功（HTTP 200）
- [ ] child span 的 `parent_span_id` 与 cchooks trace 中 `tool Bash` span 的 `spanId` 一致
- [ ] child span 的 `trace_id` 与 cchooks trace 的 `trace_id` 一致
- [ ] 同一 session 不同 turn 的 trace_id 不同（场景 1）
- [ ] child span 包含 `user.id`、`ant.username`、`ant.agent.product` 等标准属性
- [ ] ESC 中断后下一个 turn 的工具 span 不会混入前一个 turn 的 trace
- [ ] 有上游 SOFA_TRACEID 时，工具脚本能读到 `SOFA_TRACEID`

## 约束与限制

- **仅支持 Bash 工具**：Read/Write/Grep 等内置工具不执行用户脚本，无需注入
- **碰撞可忽略**：tool_use_id 本身唯一，hash 不会碰撞
- **无 Bash 工具调用的 turn**：ToolContextHandler 不触发，`collect_turn_spans` 回退到随机 trace_id（兼容现有行为）
- **Hook 侧效**：`permissionDecision: "allow"` 会跳过权限提示，但不影响命令执行结果
- **Shell 安全**：所有注入值通过 `shell_escape()` 转义（替换 `'` 为 `'\''`）
- **DefaultHasher 稳定性**：DefaultHasher 在同一编译二进制内稳定，跨 Rust 版本不保证。当前设计在同一二进制内使用，无问题
