# aivision-agents 开发规范

## 项目概述

aivision-agents 是 AI Agent 可观测性工程项目，为多种 AI 编程 Agent（Claude Code、Codex、OpenClaw、OpenCode 等）提供统一的遥测数据采集、转换和验证能力。

## 仓库结构

```
aivision-agents/
├── Makefile                  # 顶层入口：按 agent 分发 replay/validate；含 doctor / doctor-all
├── CAPABILITIES.md           # 顶层三件套：能力规范 + 8×22 矩阵
├── CONSTITUTION.md           # 顶层三件套：工程原则与治理
├── METHODOLOGY.md            # 顶层三件套：6 工作流 / 数据模型 / 接入指南
├── scripts/                  # 通用脚本
│   └── doctor.sh             # 跨语言接入验收（被 make doctor 调用）
├── claudecode/               # Claude Code 可观测
│   ├── cchooks/              # Rust hooks + replay 工具
│   ├── transcripts/          # 原始会话记录
│   ├── replays/              # OTLP replay 文件
│   ├── CAPABILITIES.md       # 22 项能力聚合评估
│   ├── capabilities/         # A1..D4/E1-E4 逐项评分与证据
│   └── Makefile
├── codex/                    # Codex 可观测
│   ├── cdxhooks/             # Rust hooks
│   ├── CAPABILITIES.md       # 22 项能力聚合评估
│   ├── capabilities/         # A1..D4/E1-E4 逐项评分与证据
│   └── Makefile
├── kimi/                     # Kimi 可观测
│   ├── kimihooks/            # Rust hooks
│   ├── CAPABILITIES.md       # 22 项能力聚合评估
│   └── capabilities/         # A1..D4/E1-E4 逐项评分与证据
├── hermes/                   # Hermes 可观测
│   ├── observ_hermes/        # Python observer
│   ├── CAPABILITIES.md       # 22 项能力聚合评估
│   └── capabilities/         # A1..D4/E1-E4 逐项评分与证据
├── openclaw/                 # OpenClaw 可观测
│   ├── observ-openclaw/      # TypeScript production plugin
│   ├── openclaw-replay/      # TypeScript replay 工具
│   ├── CAPABILITIES.md       # 22 项能力聚合评估
│   └── capabilities/         # A1..D4/E1-E4 逐项评分与证据
├── opencode/                 # OpenCode 可观测
│   ├── observ-opencode/      # TypeScript production plugin
│   ├── opencode-replay/      # TypeScript replay 工具
│   ├── CAPABILITIES.md      # 22 项能力聚合评估
│   └── capabilities/        # A1..D4/E1-E4 逐项评分与证据
├── pi/                       # Pi 可观测
│   ├── observ-pi/            # TypeScript production plugin
│   ├── pireplay/             # TypeScript replay 工具
│   ├── CAPABILITIES.md       # 22 项能力聚合评估
│   └── capabilities/         # A1..D4/E1-E4 逐项评分与证据
├── docs/
│   ├── semconv/              # 语义约定 YAML（属性、span、event、metric、entity）
│   └── capabilities/         # 可观测能力规范正文（A/B/C/D/E 五组共 22 项）
├── weaver/                   # 验证引擎
│   ├── weaver-guard/         # Rust CLI 验证器
│   ├── policies/             # Rego 验证策略
│   └── Makefile
└── research/                 # 调研文档
```

## 4 个标准工程能力

每个 agent 子项目必须实现以下能力：

| 能力 | 说明 | 关键约束 |
|------|------|----------|
| 0. 环境变量 | `{PREFIX}_SESSION_DIR` / `_REPLAY_DIR` / `_DEBUG` | DEBUG 是双写（HTTP + 本地文件），不是替代 |
| 1. Transcript | hooks 采集原始会话到 `~/.aivision/<component>/transcripts/` | JSONL 格式，`session_{context}_{hash}.jsonl` |
| 2. Replay | transcript → OTLP replay 转换 | **必须复用主流程 OTel 代码**，CLI: `<tool> <file> --otel <out>` |
| 3. 验证 | weaver-guard 验证 replay 合规性 | Rego 策略 + semconv 属性检查 |
| 4. Makefile | 统一操作接口 | 8 个标准 target（见下文） |

PREFIX 命名：`CCHOOKS`（claudecode）、`CDXHOOKS`（codex）、`OPENCLAW`（openclaw）、`OPENCODE`（opencode）

详细说明见 [AGENTS.md](AGENTS.md)。

## 可观测能力规范（Capability Spec）

能力规范定义了 Agent 可观测数据应捕获的 22 项能力，是 semconv + Rego 策略的上游依据，也是评估每个 agent 实现完成度的评分卡。正文在 `docs/capabilities/`；每个 agent 的聚合评估在 `<agent>/CAPABILITIES.md`，每项能力的代码/replay/source 证据在 `<agent>/capabilities/<ID>.md`。E1 的 VCS 字段注册说明见 [`docs/semconv/vcs.md`](docs/semconv/vcs.md)；E2 的 TRACEPARENT 传播规范见 [`docs/capabilities/E-upstream-trace-context.md`](docs/capabilities/E-upstream-trace-context.md)；E3 的触发归因规范见 [`docs/capabilities/E-meta-trigger.md`](docs/capabilities/E-meta-trigger.md)。

> **顶层三件套**：[CAPABILITIES.md](CAPABILITIES.md)（要捕获什么） + [CONSTITUTION.md](CONSTITUTION.md)（怎么决定捕获） + [METHODOLOGY.md](METHODOLOGY.md)（怎么做）。详细责任表见任一文件头部。

### 22 项能力总览（A1 / B1-B6 / C1-C7 / D1-D4 / E1-E4）

**A 组：会话与拓扑（1 项）**

| ID | 能力 | 核心约束 |
|----|------|----------|
| A1 | 多轮会话 + chain 拓扑 | 唯一 root span（CHAT + invoke_agent），其下 generation / tool / compaction 为 sibling |

**B 组：工具调用语义（6 项）**

| ID | 能力 | 核心约束 |
|----|------|----------|
| B1 | 工具调用精确耗时 | `agentic.tool.duration_ms` 非负，对应单次真实调用 |
| B2 | 工具调用错误识别 | 失败时 `status.code=ERROR + error.type` 同时存在，挂自身 span |
| B3 | 工具调用输入参数识别 | `gen_ai.tool.call.arguments`（JSON 序列化字符串） |
| B4 | 工具调用输出内容识别 | `gen_ai.tool.call.result`，截断显式标记 |
| B5 | MCP 调用识别 | `agentic.tool.type=mcp`，与 builtin / skill / subagent 区分 |
| B6 | Skill 调用识别 | `agentic.tool.type=skill`，区分命令型 / 工具型 |

**C 组：LLM 调用语义（7 项）**

| ID | 能力 | 核心约束 |
|----|------|----------|
| C1 | LLM 精确耗时 | `agentic.llm.duration_ms` 非负 |
| C2 | LLM 错误识别 | 失败时 `status.code=ERROR + error.type`，挂自身 span |
| C3 | LLM 用户输入内容 | `gen_ai.input.messages`，OpenAI 兼容 JSON array |
| C4 | LLM 系统提示词 | `gen_ai.system_instructions`（transcript 通常拿不到，多数 agent ⚪） |
| C5 | LLM 模型输出 | `gen_ai.output.messages`，结构化保留 tool_calls / finish_reason |
| C6 | LLM thinking 内容 | output.messages 中 `reasoning_content` 字段，与 content 分离 |
| C7 | LLM Token usage | `gen_ai.usage.input_tokens` / `output_tokens`，**累计 usage 必须先差分**，turn 层不重复上报 |

**D 组：高阶语义（4 项）**

| ID | 能力 | 核心约束 |
|----|------|----------|
| D1 | Compact 识别 | 独立 TOOL span，`agentic.compaction.*` 扩展属性 |
| D2 | 同步子 agent 调用 | `agentic.tool.type=subagent`，挂父 turn 下 |
| D3 | 异步子 agent 调用 | 跨 trace 关联（同 trace / span link / 属性映射），多数 agent ⚪ |
| D4 | Cancel / Retry 错误恢复 | retry 多 span 不压平；cancel 显式 `error.type` |

**E 组：开发环境与上游调用上下文（3 项）**

| E1 | Git 仓库信息识别 | 可信 worktree/cwd 上识别 remote URL 与 HEAD 引用，并按条件写入 Resource |
| E2 | 上游调用 TRACEPARENT 串联 | 消费有效 W3C TRACEPARENT，将当前 Turn 与上游 trace/span 串联，并向下游传播 child context |
| E3 | 非真实用户 / 外部触发归因识别 | 通过 `AIVISION_META_TRIGGER` 或 `meta.trigger` 记录任务归因，并在 Turn root 记录宿主观察到的触发机制 |
| E4 | SESSION 级别隐私模式设置 | 支持 `AIVISION_COLLECTION_MODE=privacy` 覆盖配置，并一致保护 session 的内容型遥测 |

### 四态判定（每项能力的评分使用以下四态）

- ✅ **完全**：必要属性齐全，replay 字段正确
- 🟡 **部分**：实现了但缺属性 / 拓扑错 / 仅部分场景覆盖
- ❌ **未实现**：数据源**有**数据，实现没采集（工程缺口，可补）
- ⚪ **不适用**：数据源**根本拿不到**（不可补）

### Span 拓扑规则（A1）

```
Turn / Root Span (CHAT, invoke_agent)
├── Generation Span (LLM, chat)           # 与 Tool 同级，不是父子
├── Tool Span (TOOL, execute_tool)        # sibling
├── Tool Span (TOOL, execute_tool)        # 并发工具共享 parallel_group
├── Compaction Span (TOOL, execute_tool)  # compaction 也是 sibling
├── API Error Span (CLIENT)               # retry 错误也是 sibling
└── Generation Span (LLM, chat)           # 多次 LLM 调用
```

### Messages 构建规则（C3 / C5）

- **Turn root span**: user 消息在数组**末尾**（Langfuse 取最后一条作为 Input 展示）
- **Generation span**: user 消息在数组**开头**（自然时序，反映 LLM API 实际输入）
- **软截断**: 超过 4MB（4×1024×1024 字符）时从头部逐条截断 content（保留前 100 字符 + `...[TRUNCATED]`），消息数量和结构不变
- **Protected tail**: Turn root 保护末尾 1 条（user），Generation 保护增量消息数

### Token 规则（C7）

- LLM span 承载原始 token usage，必须一一对应到真实单次调用
- 累计 usage 必须先做差分再映射到当前 span
- Turn / root span **不重复上报**聚合 token（避免下游重复累加）

### 能力规范导航

> 各 agent 的 `CAPABILITIES.md` 是聚合页；每项能力的评分与获取证据位于对应的 `<agent>/capabilities/<ID>.md`，例如 `claudecode/capabilities/C7.md`。

| 文档 | 路径 |
|------|------|
| 总纲 + 8×22 矩阵 | `CAPABILITIES.md` |
| 工程方法论（6 工作流 / 数据模型 / 接入） | `METHODOLOGY.md` |
| A 组（A1） | `docs/capabilities/A-session-topology.md` |
| B 组（B1-B6） | `docs/capabilities/B-tool-semantics.md` |
| C 组（C1-C7） | `docs/capabilities/C-llm-semantics.md` |
| D 组（D1-D4） | `docs/capabilities/D-advanced-semantics.md` |
| E 组（E1-E4） | `docs/capabilities/E-repository-context.md`、`docs/capabilities/E-upstream-trace-context.md`、`docs/capabilities/E-meta-trigger.md`、`docs/capabilities/E-session-privacy.md` |
| claudecode 评估/证据 | `claudecode/CAPABILITIES.md` / `claudecode/capabilities/` |
| codex 评估/证据 | `codex/CAPABILITIES.md` / `codex/capabilities/` |
| kimi 评估/证据 | `kimi/CAPABILITIES.md` / `kimi/capabilities/` |
| openclaw 评估/证据 | `openclaw/CAPABILITIES.md` / `openclaw/capabilities/` |
| opencode 评估/证据 | `opencode/CAPABILITIES.md` / `opencode/capabilities/` |
| pi 评估/证据 | `pi/CAPABILITIES.md` / `pi/capabilities/` |
| hermes 评估/证据 | `hermes/CAPABILITIES.md` / `hermes/capabilities/` |
| 工程原则与治理 | `CONSTITUTION.md` |

## Makefile 使用

```bash
# 根目录
make replay-all all                       # 批量 replay
make validate all                         # 批量验证
make replay cc TRANSCRIPT=/abs/path/file.jsonl
make validate cc FORMAT=json              # JSON 格式输出

# agent 目录
cd claudecode
make replay-all                           # 转换所有 transcript
make replay TRANSCRIPT=/abs/path/file.jsonl  # 支持绝对路径
make replay TRANSCRIPT=/abs/path/dir/     # 支持目录扫描
make validate                             # 验证所有 replay
make validate-one FILE=replays/xxx.jsonl  # 验证单个文件
make report TRANSCRIPT=<file>             # 上报到 Langfuse
make report-shift TRANSCRIPT=<file-or-dir>  # replay + 上报（支持目录）
```

短别名：`TS=session_xxx.jsonl`（自动加 `transcripts/` 前缀），`ED=aivision`（同 `EDITION=aivision`）

## 开发迭代流程

```
1. 采集 transcript     → ~/.aivision/<component>/transcripts/session_*.jsonl
2. 生成 replay         → make replay-all
3. 运行验证            → make validate
4. 阅读验证报告        → 识别 violations / improvements / semconv issues
5. 修改 replay 代码    → 修复字段映射、补充缺失属性
6. 重复 2-5            → 直到验证通过
```

## 语义约定（速查）

### Span 类型枚举（`gen_ai.span.kind`）

| Kind | 用途 | 命名模式 |
|------|------|---------|
| `CHAT` | Turn 根 span | `Turn {N}` |
| `LLM` | 单次 LLM 调用 | `chat {model}` |
| `TOOL` | 工具调用（含 builtin / MCP / skill / subagent / compaction） | `tool {name}` / `skill {name}` / `mcp {server.method}` / `compaction` |
| `CLIENT` | API error / retry span | `chat api_error` |
| `AGENT` | 可选子 agent 包裹（首选不创建） | `agent {name}` |

完整命名规范见 [METHODOLOGY §2.8](METHODOLOGY.md)。

### 强制约束（4 个 Resource 必填项）

⚠️ **强制约束**：所有 agent 的 Resource 必须携带以下 4 项，缺失即 violation：

- `ant.agent.env`（prod / staging / dev / default）
- `ant.agent.scene`（业务场景）
- `ant.username`（用户标识）
- `ant.agent.type`（cchooks / cdxhooks / openclaw / opencode / kimi）

详细规则见 [METHODOLOGY §2.2](METHODOLOGY.md)。其他属性规范（gen_ai.* / agentic.* 三命名空间边界、Token L1/L2/L3、错误闭集等）见 METHODOLOGY §2。

完整 semconv 定义见 `docs/semconv/`。

## 验证策略

`weaver/policies/` 按 `base/`（值验证）、`span/`（span 合规）、`event/`、`metric/` 组织。weaver-guard 递归加载所有 `.rego`，跳过 `_` 开头目录。

## 代码规范

- hooks 用 Rust 或 TypeScript，按 agent 生态选择
- Replay **必须复用主流程 OTel 代码**，禁止另写独立转换逻辑
- OTLP 输出必须是 `ExportTraceServiceRequest` JSONL
- 修改 replay 逻辑后必须 `make validate` 确认合规
- hooks 逻辑按职责拆分（事件处理、状态管理、OTel、配置、Transcript、FileExporter、Semconv）

## 新增 Agent

```bash
mkdir -p <agent>/{transcripts,replays}
# 实现 hooks → 创建 Makefile → 实现 replay → 迭代 validate
# 可选: mkdir docs/semconv-<agent>/  # agent 特有语义扩展
# 更新根 Makefile AGENTS 列表
```
