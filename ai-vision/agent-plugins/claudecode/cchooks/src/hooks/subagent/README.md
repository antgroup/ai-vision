# SubAgent 识别与可观测方案

## 概述

为 cchooks 增加 SubAgent（子Agent）的完整可观测能力，支持识别 Claude Code 中的同步/异步 SubAgent 调用，采集其内部执行链路（generation/tool spans），并在 Langfuse 中正确展示。

## 最低版本要求

- **Claude Code >= 2.1.98**（SubagentStop hook 事件从此版本开始支持）
- **cchooks >= 0.6.0**（包含 SubagentHandler + expand_subagent_spans）

## 数据来源

| 数据 | 来源 | 路径 |
|------|------|------|
| SubAgent transcript | Claude Code 自动写入 | `{projects}/{session_id}/subagents/agent-{id}.jsonl` |
| SubAgent 元数据 | Claude Code 自动写入 | `{projects}/{session_id}/subagents/agent-{id}.meta.json` |
| SubagentStop 事件 | Claude Code hook 回调 | stdin payload（需在 settings.json 注册） |
| run_in_background | Parent transcript 中 Agent tool_use 的 input 字段 | `"run_in_background": true/false` |

### SubAgent Transcript 格式

`.meta.json`:
```json
{"agentType": "Explore", "description": "search auth code"}
```

`.jsonl` 每行一个 JSON 对象，包含字段：
- `isSidechain: true` — 标记为子 agent 消息
- `agentId` — 子 agent 唯一标识（hex string）
- `sessionId` — 父 session ID（与主 session 共享）
- `type` — `user` / `assistant` / `progress` / `system`
- `message` — 消息内容（含 role、content、usage、tool_use 等）

## 核心设计：同步 vs 异步 SubAgent

### 同步 SubAgent（`run_in_background=false`，默认）

- Agent TOOL span 标注 `agentic.subagent.mode=sync`
- **展开子 span**：解析 subagent transcript，构建 generation/tool spans 嵌套在 Agent TOOL span 下
- SubagentStop 事件到达时 → **忽略**（已有子 span）
- 子 span 包含完整的 `gen_ai.input.messages` / `gen_ai.output.messages` / token usage

```
Turn N (parent)
├── chat GLM-5.1           <- parent LLM（决定调用 Agent）
├── subagent Agent         <- Agent TOOL span [mode=sync]
│   ├── chat GLM-5.1      <- subagent generation [subagent:Explore]
│   ├── tool Read          <- subagent tool call
│   └── chat GLM-5.1      <- subagent final response
└── chat GLM-5.1           <- parent 继续
```

### 异步 SubAgent（`run_in_background=true`）

- Agent TOOL span 标注 `agentic.subagent.mode=async`
- **不展开子 span**（transcript 在 parent Stop 时可能不完整）
- SubagentStop 事件到达时 → **创建独立 Turn(AsyncSubAgent) trace** 上报 Langfuse
- 独立 trace 包含完整的 generation/tool span 树 + input/output messages

```
Turn N (parent)                              Turn(AsyncSubAgent) general-purpose
├── chat GLM-5.1                             ├── chat GLM-5.1
├── subagent Agent [mode=async]              ├── tool Bash
└── chat GLM-5.1                             └── chat GLM-5.1
```

## 事件时序与双向状态检查

SubagentStop 事件的到达时序**不确定**——可能在 parent Stop 之前或之后。采用 registry 文件做跨事件状态传递：

```
SubagentStop 到达时:
  ├── parent 已 Stop + is_background=true  → 立即创建 Turn(AsyncSubAgent)
  ├── parent 已 Stop + is_background=false → 忽略（同步，已有子 span）
  └── parent 未 Stop → 暂存到 registry，等 parent Stop 时处理

Parent Stop 到达时:
  ├── 扫描 parent transcript → 找到所有 Agent tool_use
  ├── 提取 agent_id + run_in_background → 写入 registry
  ├── 检查 registry 中已到达的 SubagentStop:
  │   ├── is_background=true 且未处理 → 立即创建 Turn(AsyncSubAgent)
  │   └── is_background=false → 标记已处理
  ├── 标记 parent_stopped=true
  └── 清理同步和已成功上报的 background；保留运行中、待重试及尚未分类的记录

下一次 UserPromptSubmit 到达时:
  ├── 重置 parent_stopped=false / parent_stop_time=null
  └── 清理已处理记录，保留尚未分类或仍需重试的跨 turn agent
```

Registry 通过 session composite transaction API 管理，运行时写入父会话 shard 的
`{state_dir}/{session_key}/state_subagents.json`，字段名为 `subagent_registry`；仅在新
domain 文件不存在时兼容读取 legacy `state.json`。它与 timing/checkpoint 共享事务
边界，但不会把新状态写回 legacy 文件。

## OTel 属性规范

### Agent TOOL span 属性

| 属性 | 值 | 说明 |
|------|-----|------|
| `agentic.tool.type` | `subagent` | 已有（parse_agentic_tool） |
| `agentic.subagent.mode` | `sync` / `async` | 同步/异步标记 |
| `agentic.subagent.type` | `Explore` / `general-purpose` / `Plan` 等 | 子 agent 类型 |
| `agentic.subagent.description` | 任务描述文本 | 从 .meta.json 读取 |

### SubAgent 子 span 属性（sync 模式）

| 属性 | 值 | 说明 |
|------|-----|------|
| `agentic.subagent` | `true` | 标记为 subagent span |
| `agentic.subagent.type` | agent 类型 | 继承自 parent |
| `gen_ai.input.messages` | OpenAI 格式 JSON | 输入消息 |
| `gen_ai.output.messages` | OpenAI 格式 JSON | 输出消息 |
| `gen_ai.usage.input_tokens` | token 数 | 输入 token |
| `gen_ai.usage.output_tokens` | token 数 | 输出 token |

### Turn(AsyncSubAgent) 独立 trace 属性

| 属性 | 值 | 说明 |
|------|-----|------|
| `agentic.subagent` | `true` | 标记为 subagent trace |
| `agentic.subagent.mode` | `async` | 异步模式 |
| `agentic.subagent.id` | agent hex ID | 关联标识 |
| `agentic.subagent.type` | agent 类型 | |
| `agentic.subagent.description` | 任务描述 | |

## 实现模块

| 模块 | 文件 | 职责 |
|------|------|------|
| expand_subagent_spans | `src/common/otel.rs` | parent Stop 时：sync agent 展开子 span，async agent 标注不展开 |
| build_subagent_child_spans | `src/common/otel.rs` | 解析 subagent transcript 构建 generation/tool 子 span |
| extract_agent_id_from_result | `src/common/otel.rs` | 从 Agent tool result 中提取 agentId |
| SubagentHandler | `src/hooks/subagent/subagent_handler.rs` | SubagentStop 事件处理 + parent Stop transcript 扫描 |
| SubagentState | `src/hooks/subagent/subagent_state.rs` | registry 状态管理（跨事件状态传递） |

### 数据流

```
LangfuseHandler (Stop)
  → collect_turn_spans()
    → build_tool_spans()          // 构建 Agent TOOL span（已有）
    → expand_subagent_spans()     // 新增：扫描 subagents/ 目录
      → sync: build_subagent_child_spans() → 子 span 嵌套
      → async: 标注 mode=async，跳过子 span

SubagentHandler (SubagentStop)
  → handle_subagent_stop()
    → 查 registry: is_background?
      → true: 事务内取得 timing/VCS 快照
        → build_and_upload_async_turn() → 按 1 MiB 上限构建稳定 ID 的 OTLP chunks
        → 累计成功 chunk 的 turn acknowledgement，并在本轮结束/失败时一次性 checkpoint
        → 失败仅保留未确认部分供后续 Stop 重试；重发沿用稳定 trace/span ID
      → false: skip

SubagentHandler (Stop)
  → handle_parent_stop()
    → scan_transcript_for_agent_background()
    → 分类事务：写 is_background + 批量取得 background 快照
    → 锁外处理已到达的 background SubagentStop HTTP
    → finalizer 事务（锁竞争有界重试）：批量保存进度 + 清理终态；失败项保持未处理
```

## 部署配置

settings.json 需注册 SubagentStop（Stop 已有）：

```json
{
  "hooks": {
    "SubagentStop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "<hooks_path>/cchooks"
          }
        ]
      }
    ]
  }
}
```

## Replay 支持

`cchooks-replay` 同样支持 subagent 展开，前提是 transcript 目录结构保持：

```
transcripts/
├── session_xxx.jsonl
└── session_xxx/
    └── subagents/
        ├── agent-abc123.jsonl
        └── agent-abc123.meta.json
```

## 宪章对齐

- **C6（Subagent 委派）**：`agentic.tool.type=subagent`，子 span 嵌套在 TOOL span 下
- **C9（Input/Output Messages）**：subagent generation span 包含 `gen_ai.input.messages` / `gen_ai.output.messages`
- **C11（Token 语义）**：subagent generation span 承载原始 token usage
- **C13（并发工具调用）**：subagent 内部并发工具共享 `parallel_group`
