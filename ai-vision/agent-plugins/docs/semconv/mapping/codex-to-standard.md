# Codex → Standard Semconv 映射表

> 来源: [developers.openai.com/codex/config-advanced](https://developers.openai.com/codex/config-advanced)
> 采集日期: 2026-04-19
> 标准 semconv 版本: v4.0

## 事件映射

| Codex 事件 | 标准事件 | 覆盖度 | 说明 |
|------------|---------|:------:|------|
| `codex.api_request` | `api_request` | 完全对齐 | |
| `codex.tool_result` | `tool_result` | 完全对齐 | |
| `codex.tool_approval` | `tool_decision` | 近似 | Codex result 值域: approved/approved_with_amendment/rejected/auto_approved → 标准: allow/deny/skip/unknown |
| `codex.conversation_starts` | `user_prompt` | 近似 | Codex 是 session 级事件，标准是 per-prompt 事件 |

## 事件字段映射

### api_request

| Codex 字段 | 标准字段 | 覆盖度 |
|-----------|---------|:------:|
| `attempt` | `attempt` | 完全对齐 |
| `status` | `http.response.status_code` | 近似 | Codex 用 status 维度，标准用 http.response.status_code |
| `success` | 通过 `error.type` 是否存在推导 | 近似 | Codex 用 bool success，标准用 error.type |
| `latency` / `duration_ms` | `duration_ms` | 完全对齐 |
| `model` | `gen_ai.request.model` | 完全对齐 |
| `conversation_id` | `gen_ai.conversation.id` | 完全对齐 |

### tool_result

| Codex 字段 | 标准字段 | 覆盖度 |
|-----------|---------|:------:|
| `tool` (name) | `gen_ai.tool.name` | 完全对齐 |
| `arguments` | `gen_ai.tool.call.arguments` (opt-in) | 完全对齐 |
| `output` | `gen_ai.tool.call.result` (opt-in) | 完全对齐 |
| `success` | 通过 `error.type` 是否存在推导 | 近似 |

### tool_approval → tool_decision

| Codex 字段 | 标准字段 | 覆盖度 |
|-----------|---------|:------:|
| `result` = approved | `agentic.tool.decision` = allow | 近似 |
| `result` = approved_with_amendment | `agentic.tool.decision` = allow | 近似 | 标准无 amendment 区分 |
| `result` = rejected | `agentic.tool.decision` = deny | 近似 |
| `result` = auto_approved | `agentic.tool.decision` = allow + `agentic.tool.decision.source` = auto | 近似 |

## 指标映射

| Codex 指标 | 标准指标 | 覆盖度 |
|-----------|---------|:------:|
| `codex.api_request` (counter) | `agent.llm_request.count` | 近似 | Codex 维度: status/success → 标准: gen_ai.request.model + error.type |
| `codex.api_request.duration_ms` (histogram) | `agent.llm_request.duration` | 完全对齐 |
| `codex.tool.call` (counter) | `agent.tool_call.count` | 完全对齐 | Codex 维度: tool/success → 标准: agentic.tool.type + error.type |
| `codex.tool.call.duration_ms` (histogram) | `agent.tool_call.duration` | 完全对齐 |
| `codex.sse_event` (counter) | — | 缺失 | 传输层指标，标准 semconv 不覆盖 |
| `codex.sse_event.duration_ms` (histogram) | — | 缺失 | 同上 |
| `codex.websocket.request` (counter) | — | 缺失 | 传输层指标 |
| `codex.websocket.request.duration_ms` (histogram) | — | 缺失 | 同上 |
| `codex.websocket.event` (counter) | — | 缺失 | 传输层指标 |
| `codex.websocket.event.duration_ms` (histogram) | — | 缺失 | 同上 |
| `codex.responses_api_overhead` (histogram) | — | 缺失 | Codex 特有 |

## Token Normalizer (OpenAI)

```
# OpenAI → 标准 L1 Normalized (直接映射)
gen_ai.usage.input_tokens  = Codex.input_token_count   # 已含缓存
gen_ai.usage.output_tokens = Codex.output_token_count
gen_ai.usage.total_tokens  = gen_ai.usage.input_tokens + gen_ai.usage.output_tokens

# → 标准 L2 Breakdown
agentic.usage.fresh_input_tokens = Codex.input_token_count - cached_tokens  # 如 cached_tokens 可用
                                  # 否则 = null（Codex CLI 未暴露 cached_tokens）
agentic.usage.cache_read_tokens  = cached_tokens  # 如可用，否则 null
agentic.usage.cache_write_tokens = null            # OpenAI 无 cache write 概念
agentic.usage.reasoning_tokens   = Codex.reasoning_token_count
agentic.usage.tool_tokens        = Codex.tool_token_count

# → 标准 L3 Raw
agentic.usage.raw.input_token_count     = Codex.input_token_count
agentic.usage.raw.output_token_count    = Codex.output_token_count
agentic.usage.raw.reasoning_token_count = Codex.reasoning_token_count
agentic.usage.raw.tool_token_count      = Codex.tool_token_count
```

## 覆盖度统计

| 类别 | 完全对齐 | 近似 | 缺失 |
|------|:-------:|:----:|:----:|
| 事件 (4) | 2 | 2 | 0 |
| api_request 字段 (6) | 3 | 3 | 0 |
| tool_result 字段 (4) | 3 | 1 | 0 |
| 指标 (11) | 3 | 1 | 7 |
