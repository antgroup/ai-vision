# Claude Code → Standard Semconv 映射表

> 来源: [code.claude.com/docs/en/monitoring-usage](https://code.claude.com/docs/en/monitoring-usage)
> 采集日期: 2026-04-19
> 标准 semconv 版本: v4.0

## 事件映射

| Claude Code 事件 | 标准事件 | 覆盖度 | 说明 |
|------------------|---------|:------:|------|
| `claude_code.user_prompt` | `user_prompt` | 完全对齐 | |
| `claude_code.api_request` | `api_request` (success path) | 完全对齐 | |
| `claude_code.api_error` | `api_request` (error path) | 完全对齐 | v4 合并为单事件，error.type 条件字段区分 |
| `claude_code.tool_result` | `tool_result` | 完全对齐 | |
| `claude_code.tool_decision` | `tool_decision` | 完全对齐 | |
| `claude_code.api_request_body` | — | 缺失 | 标准 semconv 无 raw body 事件，可通过 opt-in content 字段部分覆盖 |
| `claude_code.api_response_body` | — | 缺失 | 同上 |
| `claude_code.plugin_installed` | — | 缺失 | CC 特有，标准 semconv 不覆盖插件生态事件 |
| `claude_code.skill_activated` | — | 缺失 | CC 特有，可通过 tool_result + agentic.tool.type=skill 间接表达 |

## 事件字段映射

### user_prompt

| CC 字段 | 标准字段 | 覆盖度 |
|---------|---------|:------:|
| `event.name` = `"user_prompt"` | `event.name` = `"user_prompt"` | 完全对齐 |
| `event.timestamp` | OTel LogRecord timestamp | 完全对齐 |
| `event.sequence` | `event.sequence` | 完全对齐 |
| `prompt_length` | — | 缺失 | 标准 semconv 无 prompt_length，可从 gen_ai.input.messages 派生 |
| `prompt` | `gen_ai.input.messages` (opt-in) | 近似 | CC 是纯文本，标准是 JSON array |
| `session.id` | `gen_ai.conversation.id` | 完全对齐 |
| `prompt.id` | `gen_ai.chat.id` | 完全对齐 |

### api_request (CC success) + api_error (CC failure)

| CC 字段 | 标准字段 | 覆盖度 |
|---------|---------|:------:|
| `model` | `gen_ai.request.model` | 完全对齐 |
| `cost_usd` | — | 缺失 | 标准 semconv 不含费用字段（Non-Goal） |
| `duration_ms` | `duration_ms` | 完全对齐 |
| `input_tokens` | `gen_ai.usage.input_tokens` | **近似** | CC 不含缓存，标准含缓存。需 Normalizer 转换：标准 = CC.input + CC.cache_read + CC.cache_creation |
| `output_tokens` | `gen_ai.usage.output_tokens` | 完全对齐 |
| `cache_read_tokens` | `agentic.usage.cache_read_tokens` (L2) + `agentic.usage.raw.cache_read_input_tokens` (L3) | 完全对齐 |
| `cache_creation_tokens` | `agentic.usage.cache_write_tokens` (L2) + `agentic.usage.raw.cache_creation_input_tokens` (L3) | 完全对齐 |
| `request_id` | — | 缺失 | 可扩展为 gen_ai.response.id |
| `speed` | — | 缺失 | CC 特有（fast/normal） |
| `error` (api_error) | `error.message` | 完全对齐 |
| `status_code` (api_error) | `http.response.status_code` | 完全对齐 |
| `attempt` (api_error) | `attempt` | 完全对齐 |

### tool_result

| CC 字段 | 标准字段 | 覆盖度 |
|---------|---------|:------:|
| `tool_name` | `gen_ai.tool.name` | 完全对齐 |
| `success` | 通过 `error.type` 是否存在推导 | 近似 | CC 用 bool success，标准用 error.type 条件字段 |
| `duration_ms` | `duration_ms` | 完全对齐 |
| `error` | `error.message` | 完全对齐 |
| `decision_type` | — | 缺失 | 在 tool_decision 事件中，非 tool_result |
| `decision_source` | — | 缺失 | 同上 |
| `tool_result_size_bytes` | — | 缺失 | 标准 semconv 无结果大小字段 |
| `mcp_server_scope` | — | 缺失 | 可通过 agentic.tool.type=mcp 间接表达 |
| `tool_parameters` (opt-in) | `gen_ai.tool.call.arguments` (opt-in) | 近似 | CC 有结构化子字段，标准为 JSON string |
| `tool_input` (opt-in) | `gen_ai.tool.call.arguments` (opt-in) | 近似 | |

### tool_decision

| CC 字段 | 标准字段 | 覆盖度 |
|---------|---------|:------:|
| `tool_name` | `gen_ai.tool.name` | 完全对齐 |
| `decision` = accept/reject | `agentic.tool.decision` = allow/deny/skip/unknown | 近似 | 标准值域更丰富 |
| `source` | `agentic.tool.decision.source` | 近似 | CC: config/hook/user_permanent/user_temporary/user_abort/user_reject → 标准: user/auto/policy/hook/config |

## 指标映射

| CC 指标 | 标准指标 | 覆盖度 |
|---------|---------|:------:|
| `claude_code.session.count` | `agent.session.count` | 完全对齐 |
| `claude_code.token.usage` | `agent.token.usage` | 近似 | CC type: input/output/cacheRead/cacheCreation → 标准 type: input/output |
| `claude_code.cost.usage` | — | 缺失 | 标准 semconv 不含费用指标 |
| `claude_code.active_time.total` | — | 缺失 | 标准 semconv v4 已移除 |
| `claude_code.lines_of_code.count` | — | 缺失 | CC 特有业务指标 |
| `claude_code.pull_request.count` | — | 缺失 | CC 特有业务指标 |
| `claude_code.commit.count` | — | 缺失 | CC 特有业务指标 |
| `claude_code.code_edit_tool.decision` | — | 缺失 | CC 特有，可通过 tool_decision 事件派生 |

## Token Normalizer (Anthropic)

```
# Anthropic → 标准 L1 Normalized (OpenAI 口径)
gen_ai.usage.input_tokens  = CC.input_tokens + CC.cache_read_tokens + CC.cache_creation_tokens
gen_ai.usage.output_tokens = CC.output_tokens
gen_ai.usage.total_tokens  = gen_ai.usage.input_tokens + gen_ai.usage.output_tokens

# → 标准 L2 Breakdown
agentic.usage.fresh_input_tokens = CC.input_tokens
agentic.usage.cache_read_tokens  = CC.cache_read_tokens
agentic.usage.cache_write_tokens = CC.cache_creation_tokens
agentic.usage.reasoning_tokens   = null  # Anthropic 无单独推理 token 计数
agentic.usage.tool_tokens        = null  # server_tool_use 是次数非 token

# → 标准 L3 Raw
agentic.usage.raw.input_tokens                = CC.input_tokens
agentic.usage.raw.cache_read_input_tokens     = CC.cache_read_tokens
agentic.usage.raw.cache_creation_input_tokens = CC.cache_creation_tokens
agentic.usage.raw.output_tokens               = CC.output_tokens
```

## 覆盖度统计

| 类别 | 完全对齐 | 近似 | 缺失 |
|------|:-------:|:----:|:----:|
| 事件 (9) | 5 | 0 | 4 |
| api_request 字段 (12) | 7 | 1 | 4 |
| tool_result 字段 (10) | 4 | 3 | 3 |
| 指标 (8) | 1 | 1 | 6 |
