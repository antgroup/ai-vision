# Kimi 可观测开发 Gap Analysis

> 基于 `docs/standard/` + `docs/semconv/` 规范 v4.0，对照 Kimi (`kimihooks`) 当前实现，梳理差异与补齐建议。
>
> 参考实现：`claudecode/cchooks`（Rust）、`hermes/observ_hermes`（Python）

---

## 一、总体架构对比

| 维度 | 规范要求 | Kimi 当前实现 | 状态 |
|------|---------|-------------|------|
| **Traces (Spans)** | C1 Root + C2 LLM + C3 Tool + C6 Subagent + C8 Compaction | 全部已实现 | ✅ 完整 |
| **Events (LogRecords)** | 4 种事件 + compaction_result | 已实现 5 种 | ✅ 基本完整 |
| **Metrics (OTLP)** | 7 业务指标 + 3 自监控 + 2 OTel 标准 | 已实现 7 业务 + 2 自监控 | ⚠️ 有缺失 |
| **Identity (身份标识)** | username/agent.type/agent.product/agent.instance | 已实现 | ⚠️ 需注册 |
| **MetricsCollector 模式** | main.rs 级 panic-safe 自监控 | 未使用 | ⚠️ 缺失 |

---

## 二、Traces (Spans) — ✅ 完整

Kimi 的 span 构建已覆盖规范要求的所有 span 类型：

| Span 类型 | gen_ai.span.kind | gen_ai.operation.name | 实现函数 | 状态 |
|-----------|-----------------|----------------------|---------|------|
| C1 Root (Turn) | `CHAT` | `invoke_agent` | `build_root_span_numbered()` | ✅ |
| C2 LLM (Generation) | `LLM` | `chat` | `build_llm_spans()` | ✅ |
| C3 Tool | `TOOL` | `execute_tool` | `build_tool_spans()` | ✅ |
| C6 Subagent | `AGENT` | `invoke_agent` | `build_subagent_spans()` | ✅ |
| C8 Compaction | `TOOL`(builtin) | `execute_tool` | `build_compaction_spans()` | ✅ |

### Span 属性合规性

| 属性 | 规范要求 | Kimi 实现 | 状态 |
|------|---------|----------|------|
| `gen_ai.span.kind` | Required | ✅ 所有 span 类型正确设置 | ✅ |
| `gen_ai.operation.name` | Required | ✅ invoke_agent / chat / execute_tool | ✅ |
| `gen_ai.provider.name` | Required | ✅ 从 model 提取或默认 `moonshot` | ✅ |
| `gen_ai.request.model` | Conditionally Required | ⚠️ 硬编码 `"unknown"`（wire.jsonl 不暴露） | ⚠️ |
| `gen_ai.conversation.id` | Conditionally Required | ✅ session_id | ✅ |
| `gen_ai.chat.id` | Recommended | ✅ trace_id | ✅ |
| `gen_ai.usage.input_tokens` | Recommended | ✅ | ✅ |
| `gen_ai.usage.output_tokens` | Recommended | ✅ | ✅ |
| `gen_ai.input.messages` | Opt-In | ✅ 受 `ContentLogConfig` 控制 | ✅ |
| `gen_ai.output.messages` | Opt-In | ✅ | ✅ |
| `agentic.turn.number` | Recommended | ✅ | ✅ |
| `agentic.tool.type` | Recommended | ✅ builtin/mcp/skill/subagent | ✅ |

**已知局限**：`gen_ai.request.model` 始终为 `"unknown"`，因 kimi-cli 的 wire.jsonl 不暴露具体模型名称。这是数据源限制，非代码缺陷。

---

## 三、Events (LogRecords) — ✅ 基本完整

### 3.1 事件覆盖度

| 事件名称 | 规范要求 | Kimi 实现 | 状态 |
|---------|---------|----------|------|
| `user_prompt` | v4.0 Required | `build_user_prompt_events()` | ✅ |
| `api_request` | v4.0 Required (替代 v3 llm_request + llm_error) | `build_api_request_events()` | ✅ |
| `tool_result` | v4.0 Required | `build_tool_events()` | ✅ |
| `tool_decision` | v4.0 Required | `build_tool_decision_events()` | ✅ |
| `compaction_result` | 扩展事件（hermes 定义） | `build_compaction_events()` | ✅ |
| `session_start` | hermes 扩展（非 v4.0 标准） | 未实现 | ℹ️ 可选 |
| `session_end` | hermes 扩展（非 v4.0 标准） | 未实现 | ℹ️ 可选 |

> `session_start` / `session_end` 不在 v4.0 semconv 标准事件定义中，是 hermes 的扩展实现。Kimi 不实现不影响合规性，但对 session 级别分析有用。

### 3.2 事件公共属性合规性 (v4.0 semconv)

| 属性 | 级别 | Kimi 实现 | 状态 |
|------|------|----------|------|
| `event.name` | Required | ✅ 每个事件均设置 | ✅ |
| `gen_ai.operation.name` | Required | ✅ chat / execute_tool | ✅ |
| `ant.username` | Required | ✅ 从环境变量解析 | ✅ |
| `ant.agent.type` | Required | ✅ `"kimicli"` | ✅ |
| `ant.agent.product` | Required | ✅ `"kimi-cli"` | ✅ |
| `gen_ai.conversation.id` | Required | ✅ session_id | ✅ |
| `event.sequence` | Recommended | ❌ 注释标记为 forbidden | ❌ 需评估 |
| `gen_ai.chat.id` | Recommended | ✅ | ✅ |
| `agentic.runtime.name` | Recommended | ✅ `"kimi-cli"` | ✅ |
| `agentic.turn.number` | Recommended | ✅ 从 TurnBegin 推导 | ✅ |

### 3.3 api_request 事件属性 (RED 完整性)

| 属性 | 级别 | Kimi 实现 | 状态 |
|------|------|----------|------|
| `gen_ai.request.model` | Required | ⚠️ 硬编码 `"unknown"` | ⚠️ 数据源限制 |
| `duration_ms` | Required (v4 升级) | ✅ `agentic.llm.duration_ms` | ✅ |
| `error.type` | Conditionally Required | ❌ 未实现错误路径 | ❌ 需补齐 |
| `error.message` | Conditionally Required | ❌ 未实现 | ❌ 需补齐 |
| `gen_ai.usage.input_tokens` | Conditionally Required | ✅ | ✅ |
| `gen_ai.usage.output_tokens` | Conditionally Required | ✅ | ✅ |
| `gen_ai.system` | Recommended | ✅ `moonshot` | ✅ |
| `gen_ai.usage.cache_read.input_tokens` | Recommended | ✅ | ✅ |
| `gen_ai.usage.cache_creation.input_tokens` | Recommended | ✅ | ✅ |
| L2 `agentic.usage.fresh_input_tokens` | Recommended | ❌ 未实现 | ❌ 可选补齐 |
| L2 `agentic.usage.cache_read_tokens` | Recommended | ❌ 未实现 | ❌ 可选补齐 |
| L2 `agentic.usage.cache_write_tokens` | Recommended | ❌ 未实现 | ❌ 可选补齐 |
| L2 `agentic.usage.reasoning_tokens` | Recommended | ❌ 未实现 | ❌ 可选补齐 |

### 3.4 tool_result 事件属性

| 属性 | 级别 | Kimi 实现 | 状态 |
|------|------|----------|------|
| `gen_ai.tool.name` | Required | ✅ | ✅ |
| `duration_ms` | Required (v4 升级) | ✅ | ✅ |
| `error.type` | Conditionally Required | ✅ 从 PostToolUseFailure 提取 | ✅ |
| `error.message` | Conditionally Required | ✅ | ✅ |
| `agentic.tool.type` | Conditionally Required | ✅ builtin/mcp/skill | ✅ |
| `agentic.tool.name` | Conditionally Required | ✅ | ✅ |
| `gen_ai.tool.call.id` | Recommended | ✅ | ✅ |

---

## 四、Metrics — ⚠️ 有缺失

### 4.1 业务指标覆盖度

| 指标名称 | 类型 | 规范要求 | Kimi 实现 | 状态 |
|---------|------|---------|----------|------|
| `agent.session.count` | Counter | ✅ Required | `build_session_token_metrics()` | ✅ |
| `agent.token.usage` | Counter | ✅ Required | `build_session_token_metrics()` | ✅ |
| `agent.tool_call.count` | Counter | ✅ Required | `build_tool_metrics()` | ✅ |
| `agent.tool_call.duration` | Histogram | ✅ Required | `build_tool_duration_metrics()` | ✅ |
| `agent.llm_request.count` | Counter | ✅ Required | `build_llm_metrics()` | ✅ |
| `agent.llm_request.duration` | Histogram | ✅ Required | `build_llm_metrics()` | ✅ |
| `agent.cache.read_tokens` | Counter | ✅ Required | `build_session_token_metrics()` | ✅ |
| `agent.cache.write_tokens` | Counter | ✅ Required | `build_session_token_metrics()` | ✅ |

### 4.2 OTel 标准指标（缺失）

| 指标名称 | 类型 | 规范来源 | Kimi 实现 | 状态 |
|---------|------|---------|----------|------|
| `gen_ai.client.token.usage` | Histogram | OTel semconv | ❌ 未实现 | ❌ 需补齐 |
| `gen_ai.client.operation.duration` | Histogram | OTel semconv | ❌ 未实现 | ❌ 需补齐 |

> 规范说明：`agent.token.usage` (Counter) 与 `gen_ai.client.token.usage` (Histogram) 是互补关系——前者用于累计计数，后者用于分布分析。两者均应实现。

### 4.3 自监控指标

| 指标名称 | 类型 | 规范要求 | Kimi 实现 | 状态 |
|---------|------|---------|----------|------|
| `aivision.agentic.up` | Counter | ✅ Required | `build_tool_metrics()` | ⚠️ 见下 |
| `aivision.agentic.exec_error` | Counter | ✅ Required | `build_tool_metrics()` | ⚠️ 见下 |
| `aivision.agentic.export_error` | Counter | ✅ Required | ❌ 未实现 | ❌ 需补齐 |

**关键架构问题**：Kimi 的 `aivision.agentic.up` 和 `aivision.agentic.exec_error` 在 `build_tool_metrics()` 中构建，仅在 Stop handler 正常执行完毕后才上报。而 claudecode 在 `main.rs` 中使用 `MetricsCollector` + `catch_unwind` 模式，确保即使 handler panic 也能上报这两个自监控指标。

### 4.4 指标属性合规性

| 指标 | 必需属性 | Kimi 缺失的属性 | 状态 |
|------|---------|---------------|------|
| `agent.tool_call.count` | `agentic.tool.type`, `ant.agent.type`, `ant.username` | ✅ 全部具备 | ✅ |
| `agent.tool_call.count` | `gen_ai.conversation.id` (Recommended, v4 新增) | ❌ 未添加 | ❌ 需补齐 |
| `agent.llm_request.count` | `gen_ai.request.model`, `ant.agent.type`, `ant.username` | ⚠️ model="unknown" | ⚠️ |
| `agent.llm_request.duration` | `gen_ai.request.model`, `gen_ai.system` | ✅ 已有 | ✅ |
| `aivision.agentic.up` | `hook.version` | ✅ 已有 | ✅ |

### 4.5 已正确移除的指标（v4 删除）

| 指标名称 | v3 状态 | v4 状态 | Kimi | 状态 |
|---------|--------|--------|------|------|
| `agent.cost.usage` | 存在 | 已删除 | 未实现 | ✅ 正确 |
| `agent.active_time.total` | 存在 | 已删除 | 未实现 | ✅ 正确 |
| `agent.error.count` | 存在 | 已删除 | 未实现 | ✅ 正确 |

---

## 五、Identity (身份标识) — ⚠️ 需注册

### 5.1 Resource 级别属性

| 属性 | 规范要求 | Kimi 实现 | 值 | 状态 |
|------|---------|----------|-----|------|
| `service.name` | Required | ✅ `ExportContext.agent_product` | `"kimi-cli"` | ✅ |
| `service.version` | Recommended | ✅ `ExportContext.build_version` | `"0.3.0"` | ✅ |
| `ant.agent.type` | Required | ✅ | `"kimicli"` | ✅ |
| `ant.agent.product` | Required | ✅ | `"kimi-cli"` | ✅ |
| `ant.agent.instance` | Optional | ✅ hostname | 动态 | ✅ |
| `deployment.environment` | Required | ✅ `agent_env` | 环境变量 | ✅ |

### 5.2 需要在身份标识规范中注册

当前 `docs/standard/custom/observability-identity-spec.md` 的 `agent.type` 枚举值只有：
- `claudecode`
- `opencode`
- `openclaw`

**需要新增注册**：

| 属性 | 值 | 说明 |
|------|-----|------|
| `agent.type` | `kimicli` | Kimi CLI 引擎类型 |
| `agent.product` | `kimi-cli` | Kimi CLI 产品名称 |

---

## 六、MetricsCollector 模式 — ❌ 缺失

### 6.1 问题描述

ClaudeCode 在 `main.rs` 中使用 `agentic-otel-common::metrics::MetricsCollector` 实现 panic-safe 的自监控上报：

```rust
// claudecode/cchooks/src/main.rs 简化版
fn main() {
    let mc = create_metrics_collector(...);
    let result = panic::catch_unwind(|| run_hook(&mc));
    if result.is_err() {
        mc.inc("aivision.agentic.exec_error", ...);  // panic 也能上报
    }
    mc.inc("aivision.agentic.up", ...);  // 始终上报心跳
    mc.flush_silent();  // 进程退出前 flush
}
```

Kimi 的 `main.rs` 没有 `MetricsCollector`，`aivision.agentic.up` / `exec_error` 在 Stop handler 内部的 `build_tool_metrics()` 中构建，如果 handler 在到达该点前 panic，这些指标将丢失。

### 6.2 影响

- Stop handler panic → `aivision.agentic.up` 心跳丢失，监控无法检测到存活
- Stop handler panic → `aivision.agentic.exec_error` 丢失，错误无法被计数
- 非 Stop 事件（如 PreCompact）完全没有自监控指标

---

## 七、补齐优先级建议

### P0 — 必须补齐（影响合规性和稳定性）

| 序号 | 缺失项 | 影响 | 工作量 |
|------|-------|------|-------|
| 1 | **main.rs MetricsCollector 模式** | panic-safe 自监控丢失 | 中 |
| 2 | **`aivision.agentic.export_error` 指标** | HTTP 上报失败无法监控 | 小 |
| 3 | **Identity 规范注册 `kimicli` / `kimi-cli`** | 身份标识不完整 | 小（文档更新） |

### P1 — 建议补齐（增强分析能力）

| 序号 | 缺失项 | 影响 | 工作量 |
|------|-------|------|-------|
| 4 | **`gen_ai.client.token.usage` Histogram** | 缺少 token 分布分析 | 小 |
| 5 | **`gen_ai.client.operation.duration` Histogram** | 缺少操作耗时分布 | 小 |
| 6 | **`agent.tool_call.count` 添加 `gen_ai.conversation.id`** | v4 新增推荐属性 | 小 |
| 7 | **api_request 事件 `error.type` / `error.message`** | LLM 错误不可见 | 中 |

### P2 — 可选补齐（完整对标）

| 序号 | 缺失项 | 影响 | 工作量 |
|------|-------|------|-------|
| 8 | **`event.sequence` 推荐属性** | 事件排序能力 | 小 |
| 9 | **L2 token 分解属性**（`agentic.usage.fresh_input_tokens` 等） | token 精细分析 | 中 |
| 10 | **`session_start` / `session_end` 事件** | session 级别分析 | 中 |

---

## 八、补齐实现方案

### 8.1 P0-1: main.rs MetricsCollector 模式

在 `kimi/kimihooks/src/main.rs` 中引入 `MetricsCollector`：

```rust
// 伪代码 — 参考 claudecode/cchooks/src/main.rs
use agentic_otel_common::metrics::{create_metrics_collector, ResourceAttrs};

fn main() {
    let mc = create_metrics_collector("KIMI_OTLP_METRICS_ENDPOINT", ResourceAttrs {
        username: resolve_username(),
        agent_product: "kimi-cli",
        agent_type: "kimicli",
        agent_instance: resolve_instance(),
        hook_version: HOOK_VERSION,
        agent_scene: "",
        agent_env: resolve_env(),
    });

    let result = panic::catch_unwind(AssertUnwindSafe(|| run()));
    if let Some(ref m) = mc {
        if result.is_err() {
            m.inc("aivision.agentic.exec_error", "hook panic count", "count",
                  vec![("error.type", "panic")]);
        }
        m.inc("aivision.agentic.up", "hook invocation count", "count", vec![]);
        m.flush_silent();
    }
    process::exit(0);
}
```

**依赖变更**：在 `Cargo.toml` 中启用 `agentic-otel-common` 的 `metrics` feature。

### 8.2 P0-2: aivision.agentic.export_error

在 `http_export.rs` 的 `post_traces` / `post_logs` / `post_metrics` 失败时递增计数器。参考 `claudecode/cchooks/src/hooks/langfuse/langfuse_handler.rs:262`。

### 8.3 P0-3: Identity 规范注册

在 `docs/standard/custom/observability-identity-spec.md` 中添加：

```markdown
##### `agent.type` — Agent 类型
| 值 | 说明 |
|----|------|
| `claudecode` | 基于 Claude Code 引擎 |
| `opencode` | 基于 OpenCode 引擎 |
| `openclaw` | 基于 OpenClaw 引擎 |
| `kimicli` | 基于 Kimi CLI 引擎 |     ← 新增

##### `agent.product` — 产品类型
| 产品 | Agent 类型 | 说明 |
|------|-----------|------|
| ...existing... |
| `kimi-cli` | `kimicli` | Kimi CLI 标准版 |   ← 新增
```

### 8.4 P1-4/5: OTel 标准 Histogram 指标

在 `otel.rs` 中新增两个函数：

```rust
pub fn build_otel_standard_metrics(records: &[HookRecord], ...) -> Vec<OtelMetric> {
    // gen_ai.client.token.usage — Histogram, unit="{token}"
    // gen_ai.client.operation.duration — Histogram, unit="s"
}
```

利用已有的 LLMStep 数据（token_usage + duration_ms）构建 OTel 标准直方图。

### 8.5 P1-7: api_request 事件错误路径

在 `build_api_request_events()` 中，当 LLMStep 有错误标记时添加 `error.type` 和 `error.message` 属性。需要检查 wire.jsonl 是否有错误信号（StatusUpdate 中是否有 error 字段）。

---

## 九、与参考实现的对比矩阵

| 能力 | claudecode (Rust) | hermes (Python) | Kimi (Rust) |
|------|:-:|:-:|:-:|
| **Traces** |
| C1 Root Span | ✅ | ✅ | ✅ |
| C2 LLM Span | ✅ | ✅ | ✅ |
| C3 Tool Span | ✅ | ✅ | ✅ |
| C6 Subagent Span | ✅ | ✅ | ✅ |
| C8 Compaction Span | ✅ | ✅ | ✅ |
| **Events** |
| user_prompt | ✅ | ✅ | ✅ |
| api_request | ✅ | ✅ | ✅ |
| tool_result | ✅ | ✅ | ✅ |
| tool_decision | ✅ | ✅ | ✅ |
| compaction_result | ✅ | ✅ | ✅ |
| session_start | ❌ | ✅ | ❌ |
| session_end | ❌ | ✅ | ❌ |
| **Metrics** |
| agent.session.count | ✅¹ | ✅ | ✅ |
| agent.token.usage | ✅¹ | ✅ | ✅ |
| agent.tool_call.count | ✅² | ✅ | ✅ |
| agent.tool_call.duration | ✅² | ❌ | ✅ |
| agent.llm_request.count | ✅² | ✅ | ✅ |
| agent.llm_request.duration | ✅² | ✅ | ✅ |
| agent.cache.read_tokens | ✅¹ | ✅ | ✅ |
| agent.cache.write_tokens | ✅¹ | ✅ | ✅ |
| gen_ai.client.token.usage | ❌ | ❌ | ❌ |
| gen_ai.client.operation.duration | ❌ | ❌ | ❌ |
| **Self-Monitoring** |
| aivision.agentic.up | ✅ (main.rs) | N/A | ⚠️ (stop handler) |
| aivision.agentic.exec_error | ✅ (main.rs) | N/A | ⚠️ (stop handler) |
| aivision.agentic.export_error | ✅ | N/A | ❌ |
| **Architecture** |
| MetricsCollector panic-safe | ✅ | N/A | ❌ |
| Identity 注册 | ✅ | ✅ | ❌ 未注册 |

> ¹ claudecode 的 session/token/cache metrics 通过 MetricsCollector 上报
> ² claudecode 的 tool/llm metrics 通过 OTLP export 上报

---

## 十、总结

Kimi 的可观测实现已达到 **~85% 合规度**，核心 Traces + Events 链路完整，主要差距在：

1. **自监控健壮性**（MetricsCollector 模式 + export_error 指标）— 影响生产可靠性
2. **OTel 标准 Histogram 指标** — 影响与标准工具链兼容性
3. **Identity 注册** — 影响跨 Agent 数据聚合
4. **api_request 错误路径** — 影响 RED 监控完整性

建议按 P0 → P1 → P2 优先级逐步补齐。
