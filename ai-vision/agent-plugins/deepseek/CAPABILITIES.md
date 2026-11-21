# dsh (DeepSeek Harness) 可观测能力卡

> 状态：🚧 实现中（自研捕获 + traces/metrics/events + 离线 replay 已落地，能力评分与 weaver-guard 双证据待跑完整验证）
> 语义注册表：`docs/semconv/spans.yaml` / `metrics.yaml` / `events.yaml`
> 实现入口：
> - 生产插件：`observ-dsh/src/index.ts`（直接订阅 `session/created|event|disposed` + `agent/*` 总线，不依赖 `dsh-session-telemetry` seam）
> - 语义引擎：`observ-dsh/src/core.ts`（实时与 replay 共享）
> - 离线 replay：`dsh-replay/src/replay.ts`（读 host 自带 canonical session JSONL/zstd）

## 固定 Resource 标识

| key | value |
|---|---|
| `ant.agent.type` | `dsh` |
| `ant.agent.product` | `DeepSeekHarness` |
| `service.name` | `dsh`（replay 为 `dsh-replay`） |

## 18 项能力四态

| 能力 | 状态 | 实现 / 说明 |
|---|---|---|
| A1 session topology | 🟡 | `Turn N` CHAT 根 span + `gen_ai.conversation.id`；subagent lineage 已从 session header 注入，subagent 父子链路待 v2 |
| B1 tool call | ✅ | `tool/call`→`tool/result` 配对，TOOL span（INTERNAL），`gen_ai.tool.call.id` |
| B2 tool error | ✅ | `tool/result.error` / message 或 content `isError` → `error.type` + `agent.tool_call.error.count` |
| B3 tool classification | ✅ | `classifyTool`：builtin / skill / mcp / subagent + `agentic.tool.*` |
| B4 tool arguments | ✅ | `gen_ai.tool.call.arguments`（opt-in / privacy 脱敏） |
| B5 tool result | ✅ | `gen_ai.tool.call.result`（opt-in / privacy 脱敏） |
| B6 tool duration | ✅ | `agentic.tool.duration_ms` + `agent.tool_call.duration` histogram |
| C1 model identity | ✅ | `request/header` + `request/context` → request model/provider；pi 原生 replay metadata 或 assistant source → response model |
| C2 request params | ✅ | temperature / max_tokens / stop_sequences / tool definitions |
| C3 token usage | ✅ | `assistant/message.usage` → gen_ai 总 usage attrs + token metrics（agent input 非缓存，gen_ai input 含缓存）；缺失 usage 显式标记，不补零 |
| C4 finish reason | ✅ | tool-calls / interrupted / stopReason → `gen_ai.response.finish_reasons` |
| C5 cache tokens | ✅ | `cacheReadTokens` / `cacheWriteTokens` → cache attrs/metrics；字段存在时保留零值，缺失时省略 |
| C6 reasoning | 🟡 | assistant/message reasoning text 进入模型输出；原生 reasoningTokens 可用时采集。pi adapter 折入 output 后没有独立 reasoning 计数，不推算 |
| C7 retry / error | 🟡 | `llm/retry-started` → `agentic.llm.retry_count`；`agent/request-error` → error metrics 和兼容字段 `agentic.llm.retry.count` |
| D1 conversation messages | ✅ | 原生 system/developer/user/assistant/tool 角色与 tool_calls（opt-in）；单次输入不包含该调用之后的工具结果 |
| D2 system prompt | ✅ | 0.1 `request/header.header.system` / 0.2 `system/message` → `gen_ai.system_instructions`（opt-in） |
| D3 tool definitions | ✅ | `request/header.header.tools` → `gen_ai.tool.definitions`（opt-in） |
| D4 MCP / Skill | 🟡 | 名称分类已实现；MCP 会话级 catalog 事件暂未消费 |

## 验收命令

```bash
make -C deepseek test
make replay-all ds AIVISION_HOME=$HOME/.aivision
make validate ds AIVISION_HOME=$HOME/.aivision
make doctor ds AIVISION_HOME=$HOME/.aivision
```

## L0-L3 合规等级自评

- **L0**：骨架、Makefile 8 target、Resource 必填 4 项、replay 样本 —— 已具备。
- **L1/L2**：待 `make validate ds` 跑通 weaver-guard 后按 violations/improvements 升级。
- **L3**：待 subagent 父子链路、reasoning、MCP catalog 补齐后评估。
