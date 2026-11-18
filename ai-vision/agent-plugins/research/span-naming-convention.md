# Span Naming Convention

## 背景

各 agent 的 span name 格式不统一，需要建立一套跨 agent 的命名规范，便于在 Langfuse/Jaeger 中快速识别 span 类型和关键信息。

## 设计原则

1. **前缀即类型** — 每种 span 有唯一的前缀词，无需解析属性即可判断类型
2. **关键信息跟在前缀后** — 模型名、工具名等保持原样，不做大小写转换
3. **Turn 大写，子 span 小写** — Turn 是根 span 醒目标识，子 span 用小写保持层次感

## 命名规范

| Span 类型 | 前缀 | 格式 | 示例 |
|-----------|------|------|------|
| 会话轮次（根 span） | `Turn` | `Turn {N}` | `Turn 1`, `Turn 2` |
| LLM 调用 | `chat` | `chat {model}` | `chat o3-pro`, `chat gpt-5.4` |
| 内置工具调用 | `tool` | `tool {name}` | `tool Bash`, `tool Read` |
| MCP 工具调用 | `mcp` | `mcp {server}.{method}` | `mcp repository.query_code` |
| Skill 调用 | `skill` | `skill {skill_name}` | `skill commit`, `skill review-pr` |

## Trace 示例

```
Turn 1
  ├── chat o3-pro
  ├── tool Bash
  ├── mcp repository.query_code
  ├── tool Read
  ├── skill commit
  └── chat o3-pro
```

## Span 类型说明

### Turn（会话轮次）

- `gen_ai.span.kind`: `CHAT`
- 根 span，每轮用户输入产生一个
- 编号从 1 开始递增

### chat（LLM 调用）

- `gen_ai.span.kind`: `LLM`
- 模型名取自 `gen_ai.request.model`，保持原样
- 无模型信息时使用 `chat unknown-{N}`

### tool（内置工具）

- `gen_ai.span.kind`: `TOOL`
- Agent 自带的原子能力（Bash、Read、Write、Glob、Grep 等）
- 工具名保持原样，不做大小写转换

### mcp（MCP 工具）

- `gen_ai.span.kind`: `MCP`（待扩展，当前为 `TOOL`）
- 外部服务提供的能力，跨进程调用，有网络开销
- 原始格式 `mcp__server__method` 转换为 `server.method`（去 `mcp__` 前缀，`__` 转 `.`）
- 适合单独设置延迟/错误率告警

### skill（Skill 调用）

- `gen_ai.span.kind`: `SKILL`（待扩展，当前为 `TOOL`）
- 用户定义的复合能力，本质是预定义 prompt + 工具编排
- Claude Code 中原始 tool name 为 `Skill`，实际名称从 `turn.skill_name` 或 tool input 的 `skill` 字段提取
- 适合检查嵌套深度

## MCP 名称转换规则

```
原始:  mcp__repository__query_code_with_keyword
步骤1: 去掉 mcp__ 前缀 → repository__query_code_with_keyword
步骤2: 第一个 __ 转为 . → repository.query_code_with_keyword
结果:  mcp repository.query_code_with_keyword
```

## Weaver 验证规则

三条 naming 规则均为 `improvement` 级别（推荐但不阻断）：

| 规则 ID | 触发条件 | 建议 |
|---------|----------|------|
| `turn_span_naming_convention` | Turn span 不以 `Turn ` 开头 | `Turn {N}` |
| `llm_span_naming_convention` | LLM span 不以 `chat ` 开头 | `chat {model}` |
| `tool_span_naming_convention` | Tool span 不以 `tool ` 开头 | `tool {name}` |

待添加：

| 规则 ID | 触发条件 | 建议 |
|---------|----------|------|
| `mcp_span_naming_convention` | MCP span 不以 `mcp ` 开头 | `mcp {server}.{method}` |
| `skill_span_naming_convention` | Skill span 不以 `skill ` 开头 | `skill {name}` |

## 实施状态

| Agent | Turn | chat | tool | mcp | skill |
|-------|------|------|------|-----|-------|
| cdxhooks (Codex) | done | done | done | done | done |
| cchooks (Claude Code) | done | done | done | done | done |
| openclaw | TODO | TODO | TODO | — | — |
| opencode | TODO | TODO | TODO | — | — |

## 备选方案（已否决）

### 方案 A：统一 tool 前缀，靠名称区分

```
tool Bash
tool repository.query_code    ← 看名字带 . 猜是 mcp
tool skill:commit             ← 加 skill: 标记
```

否决原因：类型信息不够显式，需要解析名称才能判断。

### 方案 C：带类型标签

```
tool Bash
tool [mcp] repository.query_code
tool [skill] commit
```

否决原因：格式冗余，解析不便。
