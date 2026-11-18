# Token 用量统计口径

本文说明 AI Vision 事件表与轨迹表中的 Token 统计口径。Token 用量表示模型处理的 Token 数，不等同于实际费用；不同 Agent 的原始输入字段是否包含缓存并不一致，计算时不能统一把缓存字段再次相加。

字段映射见 [Token 用量归一化](../semconv/mapping/token-normalizer.md)。字段归一化后通常带有
`attributes.` 前缀，具体列名以各 Agent 的事件字段文档和表结构为准。

## 事件表：按 Agent 保留原始展示口径

事件表用于数据看板。看板保留各 Agent 的原始字段语义，因此“输入 Tokens”
是否包含缓存取决于 Agent；但 Token 总量都会按对应 Agent 的口径计算，
避免重复累加缓存。

### Claude Code

Claude Code 的 `input_tokens` 表示本次请求的新增输入，**不包含缓存**。

| 展示项 | 原始字段 | 与总量的关系 |
| --- | --- | --- |
| 输入 Tokens | `input_tokens` | 新增输入，不含缓存 |
| 缓存命中 Tokens | `cache_read_input_tokens` | 需要计入总输入 |
| 缓存写入 Tokens | `cache_creation_input_tokens` | 需要计入总输入 |
| 输出 Tokens | `output_tokens` | 计入总量；包含可见回复和 thinking |

```text
总输入 = input_tokens
       + cache_read_input_tokens
       + cache_creation_input_tokens

Token 总量 = 总输入 + output_tokens
```

### Codex

Codex 的 `input_token_count` 表示本次请求的**总输入，已经包含缓存命中部分**。

| 展示项 | 原始字段 | 与总量的关系 |
| --- | --- | --- |
| 输入 Tokens（含缓存） | `input_token_count` | 直接作为总输入 |
| 输出 Tokens | `output_token_count` | 计入总量；包含可见回复和 reasoning |
| 缓存命中 Tokens | `cached_token_count` | 输入 Tokens 的子集，不再累加 |
| 推理 Tokens | `reasoning_token_count` | 输出 Tokens 的子集，不再累加 |
| 工具 Tokens | `tool_token_count` | 输入与输出的拆分维度，不再累加 |

```text
Token 总量 = input_token_count + output_token_count
```

### OpenClaw / OpenCode / Pi

这三类 Agent 在数据看板中的 `input_tokens` 表示本次请求的**总输入，已经包含缓存命中部分**。

| 展示项 | 原始字段 | 与总量的关系 |
| --- | --- | --- |
| 输入 Tokens（含缓存） | `input_tokens` | 直接作为总输入 |
| 输出 Tokens | `output_tokens` | 计入总量；包含可见回复和 reasoning |
| 缓存命中 Tokens | `cached_tokens` | 输入 Tokens 的子集，不再累加 |

```text
Token 总量 = input_tokens + output_tokens
```

不同 Agent 或版本落到 OTel 事件时，缓存明细也可能表现为
`gen_ai.usage.cache_read.input_tokens`、`agentic.usage.cache_read_tokens`
等标准化字段。无论字段名如何，缓存命中量都是输入量的拆分项，
不应在已含缓存的输入上重复累加。

## 轨迹表：统一为 OpenTelemetry GenAI 口径

轨迹详情页遵循 OpenTelemetry GenAI 语义规范，使用
`gen_ai.usage.input_tokens` 表示**总输入，含缓存**，从而让不同 Agent
的输入规模可以直接比较：

| 轨迹字段 | 统一含义 |
| --- | --- |
| `gen_ai.usage.input_tokens` | 单次 LLM 调用的总输入，含缓存读取和缓存写入 |
| `gen_ai.usage.output_tokens` | 单次 LLM 调用的总输出 |
| `gen_ai.usage.total_tokens` | 总输入与总输出之和 |

归一化时：

- Claude Code 将原始 `input_tokens`、`cache_read_input_tokens` 和
  `cache_creation_input_tokens` 相加，得到 `gen_ai.usage.input_tokens`。
- Codex、OpenClaw、OpenCode 和 Pi 的原始输入已经包含缓存，直接映射为
  `gen_ai.usage.input_tokens`；缓存、推理和工具 Token 仅作为明细拆分，
  不重复计数。

## 数据看板与轨迹详情的差异

| 维度 | 数据看板 | 轨迹详情页 |
| --- | --- | --- |
| 数据来源 | 事件表 | 轨迹表 |
| 输入 Tokens | 保留各 Agent 的原始语义，可能含缓存，也可能不含 | 统一归一化为总输入，始终含缓存 |
| 缓存明细 | 单独展示，便于分析缓存命中和写入 | 已计入总输入，可通过明细字段进一步分析 |
| Token 总量 | 按各 Agent 原始字段正确归一化计算 | 按统一后的输入与输出汇总 |

因此，两个页面的 **Token 总量应保持一致**；可能不同的是“输入 Tokens”
子项的展示值。数据看板适合查看 Agent 原始字段与缓存构成，轨迹详情页
适合跨 Agent 比较总体输入规模。

例如 Claude Code 某次请求的新增输入为 5,000、缓存命中为 40,000、
缓存写入为 5,000、输出为 3,000：数据看板的“输入 Tokens”显示 5,000，
轨迹详情页的 `gen_ai.usage.input_tokens` 显示 50,000，但两处 Token 总量
均为 53,000。
