# dsh → AIVision standard mapping

dsh facts come from two buses: the session firehose (`session/event` raw
`SessionEvent` log) and the agent bus (`agent/request`, `agent/request-error`,
`agent/error`). The session log is canonical and replayable; the agent bus only
adds timing/error/retry detail that the log does not carry.

## Events → spans

| dsh source | target span | span name | kind | fixed `gen_ai.operation.name` |
|---|---|---|---|---|
| `turn/start` → `turn/end` | `span.ant.chat` | `Turn {turn}` | SERVER | `invoke_agent` |
| `step/start` → `step/end` + `request/header` | `span.ant.llm` / `span.gen_ai.inference` | `chat {model}` | CLIENT | `chat` |
| `tool/call` → `tool/result` | `span.ant.tool` / `span.gen_ai.execute_tool` | `execute_tool {name}` | INTERNAL | `execute_tool` |

## dsh fields → attributes

| dsh field | attribute | notes |
|---|---|---|
| `session.id` | `gen_ai.conversation.id`, `gen_ai.session.id` | |
| `session.header.parentSession` | `gen_ai.session.parent_id` | fork lineage |
| `session.header.seedLength` | `gen_ai.session.seed_length` | |
| `session.header.origin` | `gen_ai.agent.origin` | subagent marker |
| `session.header.agentPreset` | `agentic.agent.preset` | |
| `turn/start.data.turn` | `agentic.turn.number` | |
| `turn/end.data.reason` | `gen_ai.response.finish_reasons`, `error.type` | `completed`→`stop`, `aborted`→`aborted`, `error`→failure code |
| `request/header.header.config.provider` | `gen_ai.provider.name` | normalized to `deepseek` enum |
| `request/header.header.config.model` | `gen_ai.request.model` / `gen_ai.response.model` | |
| `request/header.header.config.temperature` | `gen_ai.request.temperature` | |
| `request/header.header.config.maxTokens` | `gen_ai.request.max_tokens` | |
| `request/header.header.config.stop` | `gen_ai.request.stop_sequences` | |
| `request/header.header.system` | `gen_ai.system_instructions` | opt-in |
| `request/header.header.tools` | `gen_ai.tool.definitions` | opt-in |
| `assistant/message.usage.inputTokens` | `gen_ai.usage.input_tokens` | normalized: includes `cacheReadTokens` + `cacheWriteTokens` |
| `assistant/message.usage.outputTokens` | `gen_ai.usage.output_tokens` | |
| `assistant/message.usage.cacheReadTokens` | `gen_ai.usage.cache_read.input_tokens` | |
| `assistant/message.usage.cacheWriteTokens` | `gen_ai.usage.cache_creation.input_tokens` | |
| `assistant/message.interrupted` | `gen_ai.response.finish_reasons=['aborted']` | |
| `tool/call.callId` | `gen_ai.tool.call.id` | |
| `tool/call.name` | `gen_ai.tool.name` + classification `agentic.tool.*` | |
| `tool/result.error` / content `isError` | `error.type` | |
| `agent/request-error.failure` | `error.type`, `agentic.error.category`, `agentic.llm.retry.count` | |

## Metrics

`agent.session.count`, `agent.token.usage`, `agent.tool_call.count`,
`agent.tool_call.error.count`, `agent.tool_call.duration`,
`agent.api_request.count`, `agent.api_request.error.count`,
`agent.api_request.duration`, `agent.cache.read_tokens`,
`agent.cache.write_tokens`, `gen_ai.client.token.usage`,
`gen_ai.client.operation.duration`.

## Known gaps

1. **Reasoning text** — `assistant/chunk` reasoning blocks are lossless in the
   raw transcript but not consumed into `gen_ai.output.messages` yet.
2. **`llm/retry` transcript events** — retries are recorded by the durable log;
   the engine currently reads only the agent-bus `agent/request-error`. Replay
   retry counts are therefore zero until this event is consumed.
3. **MCP catalog** — `dsh-tool-skill`/session catalog events exist in the
   transcript but are not yet mapped to `agentic.tool.*` provenance.
4. **Subagent topology** — v1 records `subagent*` as TOOL spans with
   `gen_ai.agent.name=dsh-subagent`; nested CHAT/AGENT parent-child linkage is
   a v2 follow-up.
