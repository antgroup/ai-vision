# Token Normalizer 映射规则

> 标准 semconv 版本: v4.0
> 创建日期: 2026-04-19
> 设计来源: 语雀 WIP 设计文档（参见本仓库对应规范）

## 1. 设计理念

### 三层架构

| Layer | 命名空间 | 语义 | 必要性 |
|-------|---------|------|:------:|
| **L1: Normalized** | `gen_ai.usage.*` | 跨 agent 可比的标准化值 | **必填** |
| **L2: Breakdown** | `agentic.usage.*` | L1 的子集分解，用于钻取分析 | 推荐 |
| **L3: Raw** | `agentic.usage.raw.*` | 各 provider 原始字段，一字不改 | opt-in |

### 正交维度

两个正交维度贯穿所有 agent：
- **方向维度**: input vs output（所有 agent 都有）
- **性质维度**: cache / reasoning / tool / regular（各 agent 覆盖不同子集）

### 核心设计决策

| 决策 | 选择 | 原因 |
|------|------|------|
| L1 `input_tokens` 含义 | **总输入（含缓存）** | OpenAI 口径，跨 agent 可比 |
| L2 与 L1 关系 | **子集，不额外计数** | 避免重复计数 |
| Normalizer 路由维度 | `agent_type + provider` | 支持 OpenCode 等多 provider agent |
| null vs 0 | null=不支持, 0=支持但为零 | 聚合时行为不同 |

## 2. 子集约束不变量

```
# MUST hold — 违反说明 Normalizer 有 bug

# L2 input breakdown
agentic.usage.fresh_input_tokens
  + agentic.usage.cache_read_tokens
  + agentic.usage.cache_write_tokens
  = gen_ai.usage.input_tokens

# L2 output breakdown
agentic.usage.reasoning_tokens <= gen_ai.usage.output_tokens

# L2 cross-direction
agentic.usage.tool_tokens <= gen_ai.usage.input_tokens + gen_ai.usage.output_tokens
```

## 3. Provider Normalizer 映射规则

### 3.1 Anthropic Normalizer

**适用 Agent:** Claude Code, OpenClaw, OpenCode + Anthropic provider

**Provider API 版本:** Anthropic API 2024-10

```
# L1: Normalized
gen_ai.usage.input_tokens  = raw.input_tokens + raw.cache_read_input_tokens + raw.cache_creation_input_tokens
gen_ai.usage.output_tokens = raw.output_tokens
gen_ai.usage.total_tokens  = input_tokens + output_tokens

# L2: Breakdown
agentic.usage.fresh_input_tokens = raw.input_tokens
agentic.usage.cache_read_tokens  = raw.cache_read_input_tokens
agentic.usage.cache_write_tokens = raw.cache_creation_input_tokens
agentic.usage.reasoning_tokens   = null    # Anthropic thinking 含在 output 中，无单独计数
agentic.usage.tool_tokens        = null    # server_tool_use 是次数非 token

# L3: Raw
agentic.usage.raw.input_tokens                = raw.input_tokens
agentic.usage.raw.cache_read_input_tokens     = raw.cache_read_input_tokens
agentic.usage.raw.cache_creation_input_tokens = raw.cache_creation_input_tokens
agentic.usage.raw.output_tokens               = raw.output_tokens
```

**关键：** Anthropic 的 `input_tokens` **不含缓存**，需要累加 cache 字段得到 L1 `input_tokens`。

### 3.2 OpenAI Normalizer

**适用 Agent:** Codex, OpenCode + OpenAI provider

**Provider API 版本:** OpenAI API 2024-12

```
# L1: Normalized (直接映射)
gen_ai.usage.input_tokens  = raw.input_token_count    # 已含缓存
gen_ai.usage.output_tokens = raw.output_token_count
gen_ai.usage.total_tokens  = input_tokens + output_tokens

# L2: Breakdown
agentic.usage.fresh_input_tokens = raw.input_token_count - raw.cached_tokens   # 如 cached_tokens 可用
                                 # 否则 null（Codex CLI 未暴露）
agentic.usage.cache_read_tokens  = raw.cached_tokens    # 如可用，否则 null
agentic.usage.cache_write_tokens = null                  # OpenAI 无 cache write 概念
agentic.usage.reasoning_tokens   = raw.reasoning_token_count
agentic.usage.tool_tokens        = raw.tool_token_count

# L3: Raw
agentic.usage.raw.input_token_count     = raw.input_token_count
agentic.usage.raw.output_token_count    = raw.output_token_count
agentic.usage.raw.reasoning_token_count = raw.reasoning_token_count
agentic.usage.raw.tool_token_count      = raw.tool_token_count
```

**关键：** OpenAI 的 `input_token_count` **已含缓存**，直接映射到 L1。但 `cached_tokens` 在 Codex CLI 层面未暴露，L2 `fresh_input_tokens` 可能为 null。

### 3.3 Google Normalizer

**适用 Agent:** OpenCode + Google provider

**Provider API 版本:** Gemini API 2025-03

```
# L1: Normalized (直接映射)
gen_ai.usage.input_tokens  = raw.promptTokenCount     # 已含缓存
gen_ai.usage.output_tokens = raw.candidatesTokenCount
gen_ai.usage.total_tokens  = input_tokens + output_tokens
                           # 可与 raw.totalTokenCount 交叉校验

# L2: Breakdown
agentic.usage.fresh_input_tokens = raw.promptTokenCount - raw.cachedContentTokenCount  # 如可用
agentic.usage.cache_read_tokens  = raw.cachedContentTokenCount   # 如可用，否则 null
agentic.usage.cache_write_tokens = null                          # Google 无 cache write 概念
agentic.usage.reasoning_tokens   = raw.thoughtsTokenCount        # 如可用，否则 null
agentic.usage.tool_tokens        = null                          # Google 无 tool token 计数

# L3: Raw
agentic.usage.raw.prompt_token_count          = raw.promptTokenCount
agentic.usage.raw.candidates_token_count      = raw.candidatesTokenCount
agentic.usage.raw.cached_content_token_count  = raw.cachedContentTokenCount
agentic.usage.raw.thoughts_token_count        = raw.thoughtsTokenCount
```

## 4. null vs 0 语义

| 值 | 含义 | 聚合行为 |
|----|------|---------|
| `null` | 该 agent/provider **不支持**此字段 | 跳过，不参与计算 |
| `0` | 该 agent/provider 支持此字段，本次请求值为 0 | 参与计算（计入均值分母等） |

**示例：**
- Anthropic `reasoning_tokens` = null → Anthropic 无单独推理计数能力
- Codex `reasoning_token_count` = 0 → 使用非 o 系列模型时，推理 token 为 0

## 5. effective_tokens 计算规则

`effective_tokens` 是从 L2 Breakdown 派生的指标，不在事件属性中：

```
# 当 cache_read_tokens != null 时：
effective_input_tokens = gen_ai.usage.input_tokens - agentic.usage.cache_read_tokens
effective_tokens       = effective_input_tokens + gen_ai.usage.output_tokens

# 当 cache_read_tokens == null 时：
effective_input_tokens = null   # 无法计算
effective_tokens       = null   # 不要退化为 total_tokens，避免虚假可比性
```

**为什么 null 不退化为 total？** 如果 Codex 的 effective = total = 900,000，而 Claude 的 effective = 60,000，用户会错误地认为 Codex 消耗是 Claude 的 15 倍。宁可显示"不可用"，也不给出误导性数字。

## 6. Normalizer 路由架构

```
                    ┌─────────────────┐
                    │  Raw Usage JSON  │
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │   Dispatcher     │
                    │  (ant.agent.type │
                    │  + provider.name)│
                    └────────┬────────┘
                             │
        ┌────────────┬───────┴───────┬────────────┐
        ▼            ▼               ▼            ▼
  ┌──────────┐ ┌──────────┐  ┌──────────┐ ┌──────────┐
  │Anthropic │ │ OpenAI   │  │ Google   │ │ Custom   │
  │Normalizer│ │Normalizer│  │Normalizer│ │Normalizer│
  └────┬─────┘ └────┬─────┘  └────┬─────┘ └────┬─────┘
       │             │             │             │
       └─────────────┴──────┬──────┴─────────────┘
                            ▼
                   ┌─────────────────┐
                   │  Unified Token   │
                   │  L1 + L2 + L3    │
                   └─────────────────┘
```

**路由维度:** `ant.agent.type` + `gen_ai.provider.name`（legacy replay 可 fallback 到 `gen_ai.system`）

**多 provider agent:** OpenCode 使用 Anthropic 模型时走 Anthropic Normalizer，使用 OpenAI 模型时走 OpenAI Normalizer。路由由 `gen_ai.provider.name` 决定，旧数据缺失时才 fallback 到 `gen_ai.system`。

## 7. 新 Agent 接入流程

1. 分析该 agent 的 raw usage JSON 结构
2. 确定其底层 provider（优先 `gen_ai.provider.name`，旧数据 fallback 到 `gen_ai.system`）
3. 如果是已有 provider（anthropic/openai/google），复用现有 Normalizer
4. 如果是新 provider，实现新的 Normalizer：
   a. 确认 `input_tokens` 是否含缓存
   b. 确认有哪些 breakdown 字段可用
   c. 实现 raw → L1/L2/L3 映射
   d. 添加 L3 raw 字段到 `registry.agentic.usage` 属性组
5. 最低要求：能算出 `gen_ai.usage.input_tokens`（总输入，含缓存）和 `gen_ai.usage.output_tokens`（总输出）

## 8. 各 Provider 覆盖度矩阵

| L2 字段 | Anthropic | OpenAI | Google |
|---------|:---------:|:------:|:------:|
| `fresh_input_tokens` | ✅ 精确 | ⚠️ CLI 无 cache 数据 | ✅ 精确 |
| `cache_read_tokens` | ✅ | ⚠️ API 有但 CLI 未暴露 | ✅ |
| `cache_write_tokens` | ✅ | ❌ 无此概念 | ❌ 无此概念 |
| `reasoning_tokens` | ❌ 含在 output | ✅ | ✅ |
| `tool_tokens` | ❌ 次数非 token | ✅ | ❌ |
