//! OTLP span / log / metric 构建器。
//!
//! 覆盖 C1(Turn) + C2(LLM) + C3(Tool) + C6(Subagent) + C8(Compaction) + C9(Messages) + C15(截断)。
//!
//! v0.3 起 [`HookRecord`] 由本模块持有（迁出已删除的 `hook_log.rs`）。
//! 输入由 `stop.rs::synth_to_hook_records` 从内存 synth 列表直接产出，
//! 不再走旧 `hook_log_<session>.ndjson` 文件中转。
//! 与 cchooks 的关系：不复用 cchooks 的 `common/otel.rs`（它强依赖 transcript 数据结构），
//! 而是直接调用 `agentic-otel-common` 的低层 OTLP 类型，独立但与 cchooks 风格一致。

use std::collections::{HashMap, HashSet};

use serde_json::Value;

use agentic_otel_common::{
    iso_to_nanos,
    message::{OpenAIMessage, ToolCall},
    now_nanos, otel_span_id, otel_trace_id,
    otlp::{
        OtelAttribute, OtelHistogram, OtelHistogramDataPoint, OtelLogRecord, OtelMetric,
        OtelNumberDataPoint, OtelSpan, OtelStatus, OtelSum, SPAN_KIND_CLIENT, SPAN_KIND_INTERNAL,
    },
    parse_agentic_tool, serialize_messages_default, tool_span_name, truncate_value, AgenticToolInfo,
};

// re-export OtelSpan 供 kimihooks_replay 等外部 bin crate 使用
pub use agentic_otel_common::otlp::OtelSpan as OtelSpanExport;

/// Placeholder value used when a content field is disabled by log controls.
/// Always emit the attribute with this sentinel rather than skipping it,
/// so downstream consumers can distinguish "redacted" from "missing".
const REDACTED: &str = "[REDACTED]";

/// 单条 hook 记录的内存表示。
///
/// 由 `stop.rs::synth_to_hook_records` 构造，作为本模块所有 `build_*` 函数的统一输入。
/// `recorded_at_ns` 是事件时间戳（纳秒字符串），`payload` 是 hook payload 或 wire 合成事件。
#[derive(Debug, Clone)]
pub struct HookRecord {
    pub recorded_at_ns: String,
    pub payload: Value,
}

/// Kimi's public OTLP boundary: keep resource identity from resolved config
/// and emit only OTLP Resource fields (environment is an attribute, not `env`).
pub fn finalize_export_resource(mut request: Value, instance: &str) -> Value {
    for key in ["resourceSpans", "resourceLogs", "resourceMetrics"] {
        if let Some(resources) = request.get_mut(key).and_then(Value::as_array_mut) {
            for resource in resources {
                if let Some(object) = resource["resource"].as_object_mut() { object.remove("env"); }
                if let Some(attrs) = resource["resource"]["attributes"].as_array_mut() {
                    attrs.retain(|a| a["key"] != "ant.agent.instance");
                    attrs.push(serde_json::json!({"key":"ant.agent.instance", "value":{"stringValue":instance}}));
                }
            }
        }
    }
    request
}

/// 从环境变量解析当前用户标识，遵循 cchooks 同款约定。
///
/// 优先级：`AIVISION_ANT_USERNAME` > `USER` > `"unknown"`。该值同时写入 `ant.username`
/// 与 `user.id`（Langfuse userId 字段映射兼容，详见 `docs/semconv/attribute_groups.yaml`）。
pub fn resolve_username() -> String {
    if let Ok(v) = std::env::var("AIVISION_ANT_USERNAME") {
        if !v.is_empty() {
            return v;
        }
    }
    if let Ok(v) = std::env::var("USER") {
        if !v.is_empty() {
            return v;
        }
    }
    "unknown".to_string()
}

/// 把 hook_log 中的所有事件配对成 tool span 列表。
///
/// 算法（O(n) 一次遍历）：
/// 1. 按出现顺序遍历每条 record；
/// 2. 见到 PreToolUse → 暂存到 `pending<tool_call_id, PreEvt>`；
/// 3. 见到 PostToolUse/PostToolUseFailure → 与 `pending` 中同 `tool_call_id` 配对，生成 1 个 span 并移除 pending；
/// 4. 遗留在 `pending` 中的 PreToolUse 视为"被中断"——为其生成 1 个 ERROR span，
///    `agentic.tool.terminated = "true"`，end_time = pre.start_time（duration 0）。
///
/// 返回的所有 span 共享同一个 `trace_id`，并把 `parent_span_id` 设置成传入值。
/// 当 `parent_span_id` 为空字符串时表示无父（兼容旧调用方式）。
///
/// Sprint 2 增强：
/// - **支持 parent_span_id**：让 tool span 挂在 turn root span 下，形成 C1→C3 父子拓扑。
/// - **C15 截断元数据**：`gen_ai.tool.call.arguments` / `gen_ai.tool.call.result` 走
///   `agentic_otel_common::truncate_value()`，超过 20K 字符时输出截断后的字符串 +
///   `agentic.tool.input.truncated/orig_len/sha256` 元数据，**永不破坏 JSON 结构**
///   （参数若是 object 会被序列化后整体截断，下游能凭 sha256 验证完整性）。
pub fn build_tool_spans(
    records: &[HookRecord],
    trace_id: &str,
    parent_span_id: &str,
    session_id: &str,
    log_cfg: &ContentLogConfig,
) -> Vec<OtelSpan> {
    let username = resolve_username();
    let mut pending: HashMap<String, PendingPre> = HashMap::new();
    let mut spans: Vec<OtelSpan> = Vec::new();

    for rec in records {
        let event = rec
            .payload
            .get("hook_event_name")
            .and_then(|v| v.as_str())
            .unwrap_or("");
        let call_id = rec
            .payload
            .get("tool_call_id")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        if call_id.is_empty() && !matches!(event, "SessionStart" | "SessionEnd") {
            // tool_call_id 为空无法配对，直接跳过（kimi 主流程恒会写非空值，见 events.py）。
            continue;
        }

        match event {
            "PreToolUse" => {
                pending.insert(
                    call_id.clone(),
                    PendingPre {
                        start_ns: rec.recorded_at_ns.clone(),
                        turn_number: 1, // span builder 不分 turn，默认 1
                        tool_name: rec
                            .payload
                            .get("tool_name")
                            .and_then(|v| v.as_str())
                            .unwrap_or("")
                            .to_string(),
                        tool_input: rec
                            .payload
                            .get("tool_input")
                            .cloned()
                            .unwrap_or(Value::Null),
                    },
                );
            }
            "PostToolUse" => {
                if let Some(pre) = pending.remove(&call_id) {
                    spans.push(make_tool_span(
                        trace_id,
                        parent_span_id,
                        &username,
                        &call_id,
                        &pre,
                        ToolOutcome::Success {
                            output: rec
                                .payload
                                .get("tool_output")
                                .and_then(|v| v.as_str())
                                .unwrap_or("")
                                .to_string(),
                        },
                        rec.recorded_at_ns.clone(),
                        session_id,
                        log_cfg,
                    ));
                }
            }
            "PostToolUseFailure" => {
                if let Some(pre) = pending.remove(&call_id) {
                    spans.push(make_tool_span(
                        trace_id,
                        parent_span_id,
                        &username,
                        &call_id,
                        &pre,
                        ToolOutcome::Failure {
                            error: rec
                                .payload
                                .get("error")
                                .and_then(|v| v.as_str())
                                .unwrap_or("")
                                .to_string(),
                        },
                        rec.recorded_at_ns.clone(),
                        session_id,
                        log_cfg,
                    ));
                }
            }
            _ => { /* SessionStart/End 等本 Sprint 不生成 span，仅作为 hook_log 边界 */ }
        }
    }

    // 处理未配对的 Pre：标记为 terminated。
    for (call_id, pre) in pending {
        let end_ns = pre.start_ns.clone();
        spans.push(make_tool_span(
            trace_id,
            parent_span_id,
            &username,
            &call_id,
            &pre,
            ToolOutcome::Terminated,
            end_ns,
            session_id,
            log_cfg,
        ));
    }
    spans
}

/// 推断 session 的 trace 起始时间（取 hook_log 中第一条 record 的 recorded_at_ns）。
pub fn session_start_ns(records: &[HookRecord]) -> String {
    records
        .first()
        .map(|r| r.recorded_at_ns.clone())
        .unwrap_or_else(now_nanos)
}

/// 为整个 session 生成一个稳定的 trace_id（每次 replay 都会重新随机；kimi-cli 同 session
/// 没有暴露 trace_id 字段，新随机值符合"replay 是中间产物"的定位）。
pub fn new_trace_id() -> String {
    otel_trace_id()
}

#[derive(Debug)]
struct PendingPre {
    start_ns: String,
    turn_number: usize,
    tool_name: String,
    tool_input: Value,
}

impl PendingPre {
    fn tool_info(&self, call_id: &str) -> AgenticToolInfo {
        let skill_name = if self.tool_name == "Skill" {
            self.tool_input
                .get("skill")
                .and_then(Value::as_str)
                .map(str::trim)
                .filter(|name| !name.is_empty())
        } else {
            None
        };
        parse_agentic_tool(&self.tool_name, call_id, skill_name)
    }
}

enum ToolOutcome {
    Success { output: String },
    Failure { error: String },
    Terminated,
}

fn make_tool_span(
    trace_id: &str,
    parent_span_id: &str,
    username: &str,
    call_id: &str,
    pre: &PendingPre,
    outcome: ToolOutcome,
    end_ns: String,
    session_id: &str,
    log_cfg: &ContentLogConfig,
) -> OtelSpan {
    let info = pre.tool_info(call_id);
    let span_name = tool_span_name(&info);

    let mut attributes: Vec<OtelAttribute> = Vec::with_capacity(20);
    // — 必需：gen_ai 语义身份 —
    attributes.push(OtelAttribute::string("gen_ai.span.kind", "TOOL"));
    attributes.push(OtelAttribute::string(
        "gen_ai.operation.name",
        "execute_tool",
    ));
    attributes.push(OtelAttribute::string("gen_ai.tool.name", &span_name));
    attributes.push(OtelAttribute::string("gen_ai.tool.call.id", call_id));

    // — 必需：用户身份（policies/base/resource_identity.rego 强制）—
    attributes.push(OtelAttribute::string("ant.username", username));
    attributes.push(OtelAttribute::string("user.id", username));
    if !session_id.is_empty() {
        attributes.push(OtelAttribute::string("gen_ai.conversation.id", session_id));
    }

    // — 必需：agentic.tool.* 分类 —
    attributes.push(OtelAttribute::string(
        "agentic.tool.raw_name",
        &info.raw_name,
    ));
    attributes.push(OtelAttribute::string("agentic.tool.type", info.tool_type));
    attributes.push(OtelAttribute::string("agentic.tool.name", &info.name));
    attributes.push(OtelAttribute::string("agentic.tool.id", &info.id));

    // — 工具入参（C15 智能截断：>20K 字符时整体序列化后截断 + 写 sha256/orig_len 元数据）—
    // truncate_value 永远返回字符串型 Value（即使原值是 object），因此 attribute 也是 string，
    // 下游可以凭 sha256 验证原始 JSON。
    if log_cfg.spans_tool_details {
        let (truncated_input, input_meta) = truncate_value(&pre.tool_input);
        let arguments_str = match &truncated_input {
            Value::String(s) => s.clone(),
            other => serde_json::to_string(other).unwrap_or_default(),
        };
        attributes.push(OtelAttribute::string(
            "gen_ai.tool.call.arguments",
            &arguments_str,
        ));
        append_truncation_meta(&mut attributes, "input", &input_meta);
    } else {
        attributes.push(OtelAttribute::string("gen_ai.tool.call.arguments", REDACTED));
    }

    // — 时长 —
    let start_i: i128 = pre.start_ns.parse().unwrap_or(0);
    let end_i: i128 = end_ns.parse().unwrap_or(start_i);
    let duration_ms = (end_i - start_i).max(0) / 1_000_000;
    attributes.push(OtelAttribute::int(
        "agentic.tool.duration_ms",
        duration_ms as i64,
    ));

    let status = match &outcome {
        ToolOutcome::Success { output } => {
            if log_cfg.spans_tool_outputs {
                let (out_v, out_meta) = truncate_value(&Value::String(output.clone()));
                let out_str = out_v.as_str().map(|s| s.to_string()).unwrap_or_default();
                attributes.push(OtelAttribute::string("gen_ai.tool.call.result", &out_str));
                append_truncation_meta(&mut attributes, "output", &out_meta);
            } else {
                attributes.push(OtelAttribute::string("gen_ai.tool.call.result", ""));
            }
            OtelStatus::ok()
        }
        ToolOutcome::Failure { error } => {
            if log_cfg.spans_tool_outputs {
                let (out_v, out_meta) = truncate_value(&Value::String(error.clone()));
                let out_str = out_v.as_str().map(|s| s.to_string()).unwrap_or_default();
                attributes.push(OtelAttribute::string("gen_ai.tool.call.result", &out_str));
                append_truncation_meta(&mut attributes, "output", &out_meta);
            } else {
                attributes.push(OtelAttribute::string("gen_ai.tool.call.result", ""));
            }
            attributes.push(OtelAttribute::string("error.type", "tool_error"));
            OtelStatus::error(error.clone())
        }
        ToolOutcome::Terminated => {
            attributes.push(OtelAttribute::string("gen_ai.tool.call.result", ""));
            attributes.push(OtelAttribute::string("agentic.tool.terminated", "true"));
            attributes.push(OtelAttribute::string("error.type", "terminated"));
            OtelStatus::error(
                "tool call did not receive a Post* hook (process killed?)".to_string(),
            )
        }
    };

    OtelSpan {
        trace_id: trace_id.to_string(),
        span_id: otel_span_id(),
        parent_span_id: parent_span_id.to_string(),
        name: span_name,
        kind: SPAN_KIND_INTERNAL,
        start_time_unix_nano: pre.start_ns.clone(),
        end_time_unix_nano: end_ns,
        attributes,
        status,
    }
}

/// 把 `truncate_value` / `truncate_text` 返回的 metadata 写入 `agentic.tool.{input|output}.*` 属性。
///
/// `meta` 形如 `{"truncated": bool, "orig_len": usize, "kept_len"?: usize, "sha256"?: string}`。
/// 仅当 `truncated == true` 才输出元数据，避免污染未截断的正常 span。
fn append_truncation_meta(attrs: &mut Vec<OtelAttribute>, kind: &str, meta: &Value) {
    let truncated = meta
        .get("truncated")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);
    if !truncated {
        return;
    }
    attrs.push(OtelAttribute::string(
        format!("agentic.tool.{}.truncated", kind),
        "true",
    ));
    if let Some(orig) = meta.get("orig_len").and_then(|v| v.as_u64()) {
        attrs.push(OtelAttribute::int(
            format!("agentic.tool.{}.original_length", kind),
            orig as i64,
        ));
    }
    if let Some(sha) = meta.get("sha256").and_then(|v| v.as_str()) {
        attrs.push(OtelAttribute::string(
            format!("agentic.tool.{}.sha256", kind),
            sha,
        ));
    }
}

/// 把 ISO8601 时间戳转纳秒（薄封装，便于后续 sprint 复用）。
#[allow(dead_code)]
pub fn iso_ns(iso: &str) -> String {
    iso_to_nanos(iso)
}

// ───────────────────────────────────────────────────────────────────────────
// Events（OTLP LogRecords）
// 严格遵循 docs/semconv/events.yaml 的 `tool_result` 定义。
//
// 必需公共属性（policies/event/event_common.rego）：
//   event.name, gen_ai.operation.name, ant.username, ant.agent.type,
//   ant.agent.product, gen_ai.conversation.id
// 推荐公共属性：gen_ai.chat.id, agentic.runtime.name, agentic.turn.number
// 红线（forbidden）：event.sequence, prompt.id, event.timestamp
// ───────────────────────────────────────────────────────────────────────────

/// 上下文用于把 hook 流转换成 events / metrics 时填充必需的 resource-级属性。
///
/// 字段命名严格对齐 semconv：
/// - `agent_product` → `ant.agent.product`（如 `kimi-cli`）
/// - `agent_type`    → `ant.agent.type`（如 `kimicli`，必须命中 valid_agent_types）
/// - `runtime_name`  → `agentic.runtime.name`（如 `kimi-cli`，命中 valid_runtime_names）
pub struct EmissionContext<'a> {
    pub agent_product: &'a str,
    pub agent_type: &'a str,
    pub runtime_name: &'a str,
    /// 内容采集开关（替代旧的 `KIMIHOOKS_EMIT_TOOL_CONTENT` 环境变量）
    pub log: &'a ContentLogConfig,
}

/// 内容采集控制，从 `EditionConfig` 解析后传入。
///
/// - `spans_tool_details`: 控制 span 属性中 `gen_ai.tool.call.arguments` / `gen_ai.tool.call.result`
/// - `events_tool_details`: 控制 event 属性中 `gen_ai.tool.call.arguments` / `gen_ai.tool.call.result`
/// - `events_user_prompts`: 控制 event 属性中 `gen_ai.input.messages`
/// - `spans_user_prompts`: 控制 span 属性中 `gen_ai.input.messages`（turn root / LLM span）
/// - `spans_model_outputs`: 控制 span 属性中 `gen_ai.output.messages`
/// - `events_model_outputs`: 控制 event 属性中 `gen_ai.output.messages`
/// - `spans_tool_outputs` / `events_tool_outputs`: 控制工具输出是否记录（细粒度，暂未使用）
#[derive(Debug, Clone, Default)]
pub struct ContentLogConfig {
    pub spans_user_prompts: bool,
    pub spans_model_outputs: bool,
    pub spans_tool_details: bool,
    pub spans_tool_outputs: bool,
    pub events_user_prompts: bool,
    pub events_model_outputs: bool,
    pub events_tool_details: bool,
    pub events_tool_outputs: bool,
}

impl From<&crate::common::edition::EditionConfig> for ContentLogConfig {
    fn from(cfg: &crate::common::edition::EditionConfig) -> Self {
        if agentic_otel_common::privacy::resolve_mode("KIMI_COLLECTION_MODE") != Ok("full") { return Self::default(); }
        Self {
            spans_user_prompts: cfg.log_spans_user_prompts,
            spans_model_outputs: cfg.log_spans_model_outputs,
            spans_tool_details: cfg.log_spans_tool_details,
            spans_tool_outputs: cfg.log_spans_tool_outputs,
            events_user_prompts: cfg.log_events_user_prompts,
            events_model_outputs: cfg.log_events_model_outputs,
            events_tool_details: cfg.log_events_tool_details,
            events_tool_outputs: cfg.log_events_tool_outputs,
        }
    }
}

/// 把 hook_log 中的所有 tool 配对转换成 `tool_result` events。
///
/// 一次工具调用 → 一条 LogRecord。失败/中断也走同一事件（single-event model，
/// 通过 `error.type` 区分），与 events.yaml 的设计一致。
///
/// `agentic.turn.number` 从 TurnBegin 序号推导（每遇一个 TurnBegin 递增），
/// 保证 events 与 traces 的 turn 编号一致。
pub fn build_tool_events(
    records: &[HookRecord],
    session_id: &str,
    ctx: &EmissionContext,
) -> Vec<OtelLogRecord> {
    let username = resolve_username();
    let mut pending: HashMap<String, PendingPre> = HashMap::new();
    let mut events: Vec<OtelLogRecord> = Vec::new();
    let mut turn_number: usize = 0;

    for rec in records {
        let event = rec
            .payload
            .get("hook_event_name")
            .and_then(|v| v.as_str())
            .unwrap_or("");
        let call_id = rec
            .payload
            .get("tool_call_id")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        if call_id.is_empty() && !matches!(event, "SessionStart" | "SessionEnd" | "TurnBegin") {
            continue;
        }

        // 跟踪 turn 编号
        if event == "TurnBegin" {
            turn_number += 1;
        }

        match event {
            "PreToolUse" => {
                pending.insert(
                    call_id.clone(),
                    PendingPre {
                        start_ns: rec.recorded_at_ns.clone(),
                        turn_number,
                        tool_name: rec
                            .payload
                            .get("tool_name")
                            .and_then(|v| v.as_str())
                            .unwrap_or("")
                            .to_string(),
                        tool_input: rec
                            .payload
                            .get("tool_input")
                            .cloned()
                            .unwrap_or(Value::Null),
                    },
                );
            }
            "PostToolUse" => {
                if let Some(pre) = pending.remove(&call_id) {
                    events.push(make_tool_result_event(
                        session_id,
                        &username,
                        ctx,
                        &call_id,
                        &pre,
                        ToolEventOutcome::Success,
                        rec.recorded_at_ns.clone(),
                        rec.payload
                            .get("tool_output")
                            .and_then(|v| v.as_str())
                            .unwrap_or("")
                            .to_string(),
                    ));
                }
            }
            "PostToolUseFailure" => {
                if let Some(pre) = pending.remove(&call_id) {
                    let err = rec
                        .payload
                        .get("error")
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .to_string();
                    events.push(make_tool_result_event(
                        session_id,
                        &username,
                        ctx,
                        &call_id,
                        &pre,
                        ToolEventOutcome::Failure {
                            error_message: err.clone(),
                        },
                        rec.recorded_at_ns.clone(),
                        err,
                    ));
                }
            }
            _ => {}
        }
    }

    // 未配对的 Pre：按 terminated 输出（同一事件，error.type=terminated）
    for (call_id, pre) in pending {
        let end_ns = pre.start_ns.clone();
        events.push(make_tool_result_event(
            session_id,
            &username,
            ctx,
            &call_id,
            &pre,
            ToolEventOutcome::Terminated,
            end_ns,
            String::new(),
        ));
    }

    events
}

enum ToolEventOutcome {
    Success,
    Failure { error_message: String },
    Terminated,
}

fn make_tool_result_event(
    session_id: &str,
    username: &str,
    ctx: &EmissionContext,
    call_id: &str,
    pre: &PendingPre,
    outcome: ToolEventOutcome,
    end_ns: String,
    result_payload: String,
) -> OtelLogRecord {
    let info = pre.tool_info(call_id);
    let span_name = tool_span_name(&info);

    let start_i: i128 = pre.start_ns.parse().unwrap_or(0);
    let end_i: i128 = end_ns.parse().unwrap_or(start_i);
    let duration_ms = ((end_i - start_i).max(0) / 1_000_000) as i64;

    let mut attrs: Vec<OtelAttribute> = Vec::with_capacity(20);
    // ── 必需公共属性 ──
    attrs.push(OtelAttribute::string("event.name", "tool_result"));
    attrs.push(OtelAttribute::string(
        "gen_ai.operation.name",
        "execute_tool",
    ));
    attrs.push(OtelAttribute::string("ant.username", username));
    attrs.push(OtelAttribute::string("ant.agent.type", ctx.agent_type));
    attrs.push(OtelAttribute::string(
        "ant.agent.product",
        ctx.agent_product,
    ));
    attrs.push(OtelAttribute::string("gen_ai.conversation.id", session_id));

    // ── 推荐公共属性 ──
    attrs.push(OtelAttribute::string("gen_ai.chat.id", session_id));
    attrs.push(OtelAttribute::string(
        "agentic.runtime.name",
        ctx.runtime_name,
    ));
    attrs.push(OtelAttribute::int("agentic.turn.number", pre.turn_number as i64));

    // ── tool_result 必需 ──
    // 字段名严格对齐 events.yaml 的 `- ref: duration_ms`（attribute_groups.yaml::registry.ant_event
    // 注册的裸字段）。policy 已在前缀白名单中放行 duration_ms / attempt 这两个 ref。
    attrs.push(OtelAttribute::string("gen_ai.tool.name", &span_name));
    attrs.push(OtelAttribute::int("duration_ms", duration_ms));

    // ── tool 分类（conditionally required）──
    attrs.push(OtelAttribute::string("agentic.tool.type", info.tool_type));
    attrs.push(OtelAttribute::string("agentic.tool.name", &info.name));
    attrs.push(OtelAttribute::string("agentic.tool.id", &info.id));

    // ── 推荐 ──
    attrs.push(OtelAttribute::string("gen_ai.tool.call.id", call_id));

    let legacy_content = agentic_otel_common::privacy::resolve_mode("KIMI_COLLECTION_MODE") == Ok("full")
        && std::env::var("KIMIHOOKS_EMIT_TOOL_CONTENT").map(|v| v == "1" || v.eq_ignore_ascii_case("true")).unwrap_or(false);
    let arguments_json = serde_json::to_string(&pre.tool_input).unwrap_or_default();
    attrs.push(OtelAttribute::string("gen_ai.tool.call.arguments", if ctx.log.events_tool_details || legacy_content { &arguments_json } else { REDACTED }));
    attrs.push(OtelAttribute::string("gen_ai.tool.call.result", if ctx.log.events_tool_outputs || legacy_content { &result_payload } else { REDACTED }));

    // ── 错误路径 ──
    match outcome {
        ToolEventOutcome::Success => {}
        ToolEventOutcome::Failure { error_message } => {
            attrs.push(OtelAttribute::string("error.type", "tool_error"));
            if !error_message.is_empty() {
                attrs.push(OtelAttribute::string("error.message", &error_message));
            }
        }
        ToolEventOutcome::Terminated => {
            attrs.push(OtelAttribute::string("error.type", "terminated"));
            attrs.push(OtelAttribute::string(
                "error.message",
                "tool call did not receive a Post* hook (process killed?)",
            ));
        }
    }

    OtelLogRecord::event("tool_result", &end_ns, attrs)
}

// ───────────────────────────────────────────────────────────────────────────
// tool_decision events（OTLP LogRecords）
// 对齐 docs/semconv/events.yaml v4.0 的 `tool_decision` 定义。
//
// 当前限制：Stop handler replay 路径只能捕获 `allow` 决策（工具正常执行）。
// `deny` 决策（PreToolUse hook 返回 permissionDecision=deny）发生在实时 hook 路径，
// wire.jsonl 不记录此信号。未来需在 PreToolUse handler 中实时写入 tool_decision 事件。
//
// 事件类型映射：
//   - PreToolUse 配对成功 → allow（source=auto，kimi 默认允许）
//   - 未配对的 terminated → 不产生 tool_decision（terminated ≠ deny）
//   - deny（future）      → 需实时 hook 捕获
// ───────────────────────────────────────────────────────────────────────────

/// 从配对成功的 tool 调用生成 `tool_decision` 事件（allow 路径）。
///
/// 每个成功配对的 Pre+Post → 1 条 tool_decision(allow) 事件。
/// deny 路径需要实时 hook 支持（当前未实现）。
pub fn build_tool_decision_events(
    records: &[HookRecord],
    session_id: &str,
    ctx: &EmissionContext,
) -> Vec<OtelLogRecord> {
    let username = resolve_username();
    let mut events: Vec<OtelLogRecord> = Vec::new();
    let mut turn_number: usize = 0;
    let mut pending: HashMap<String, PendingPre> = HashMap::new();

    for rec in records {
        let event = rec
            .payload
            .get("hook_event_name")
            .and_then(|v| v.as_str())
            .unwrap_or("");
        let call_id = rec
            .payload
            .get("tool_call_id")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        if call_id.is_empty() {
            continue;
        }

        if event == "TurnBegin" {
            turn_number += 1;
        }

        match event {
            "PreToolUse" => {
                pending.insert(
                    call_id.clone(),
                    PendingPre {
                        start_ns: rec.recorded_at_ns.clone(),
                        turn_number,
                        tool_name: rec
                            .payload
                            .get("tool_name")
                            .and_then(|v| v.as_str())
                            .unwrap_or("")
                            .to_string(),
                        tool_input: rec
                            .payload
                            .get("tool_input")
                            .cloned()
                            .unwrap_or(Value::Null),
                    },
                );
            }
            "PostToolUse" | "PostToolUseFailure" => {
                if let Some(pre) = pending.remove(&call_id) {
                    let info = pre.tool_info(&call_id);
                    let span_name = tool_span_name(&info);

                    let mut attrs: Vec<OtelAttribute> = Vec::with_capacity(16);

                    // ── 必需公共属性 ──
                    attrs.push(OtelAttribute::string("event.name", "tool_decision"));
                    attrs.push(OtelAttribute::string(
                        "gen_ai.operation.name",
                        "execute_tool",
                    ));
                    attrs.push(OtelAttribute::string("ant.username", &username));
                    attrs.push(OtelAttribute::string("ant.agent.type", ctx.agent_type));
                    attrs.push(OtelAttribute::string(
                        "ant.agent.product",
                        ctx.agent_product,
                    ));
                    attrs.push(OtelAttribute::string("gen_ai.conversation.id", session_id));

                    // ── 推荐公共属性 ──
                    attrs.push(OtelAttribute::int("agentic.turn.number", turn_number as i64));
                    attrs.push(OtelAttribute::string("gen_ai.chat.id", session_id));
                    attrs.push(OtelAttribute::string(
                        "agentic.runtime.name",
                        ctx.runtime_name,
                    ));
                    attrs.push(OtelAttribute::int(
                        "agentic.turn.number",
                        pre.turn_number as i64,
                    ));

                    // ── tool_decision Required ──
                    attrs.push(OtelAttribute::string("gen_ai.tool.name", &span_name));
                    // allow: 工具被允许执行（kimi 默认行为）
                    // deny: 工具被拒绝（需实时 hook 捕获，当前未实现）
                    attrs
                        .push(OtelAttribute::string("agentic.tool.decision", "allow"));
                    // source: auto=kimi 默认允许, user=用户手动, hook=hook 拒绝
                    attrs.push(OtelAttribute::string(
                        "agentic.tool.decision.source",
                        "auto",
                    ));

                    // ── tool 分类（conditionally required）──
                    attrs.push(OtelAttribute::string("agentic.tool.type", info.tool_type));
                    attrs.push(OtelAttribute::string("agentic.tool.name", &info.name));

                    // ── recommended ──
                    attrs.push(OtelAttribute::string("gen_ai.tool.call.id", &call_id));

                    events.push(OtelLogRecord::event(
                        "tool_decision",
                        &rec.recorded_at_ns,
                        attrs,
                    ));
                }
            }
            _ => {}
        }
    }

    // 注意：terminated 的 PreToolUse 不产生 tool_decision（terminated ≠ deny）
    events
}

// ───────────────────────────────────────────────────────────────────────────
// api_request events（OTLP LogRecords）
// 对齐 docs/semconv/events.yaml v4.0 的 `api_request` 定义。
//
// 单事件模型：成功和失败共用同一事件类型，通过 error.type 区分。
// 数据来源：LLMStep 合成记录（Stop handler 从 wire.jsonl 重建）。
//
// 三层 Token 映射（kimi kosong TokenUsage → semconv）：
//   L1 gen_ai.usage.input_tokens          = input_other + input_cache_read + input_cache_creation
//   L1 gen_ai.usage.output_tokens         = output
//   L2 agentic.usage.fresh_input_tokens   = input_other
//   L2 agentic.usage.cache_read_tokens    = input_cache_read
//   L2 agentic.usage.cache_write_tokens   = input_cache_creation
//   L2 agentic.usage.reasoning_tokens     = ❌ kosong 无此字段
// ───────────────────────────────────────────────────────────────────────────

/// 把 LLMStep 记录转换成 `api_request` events。
///
/// 每条 `LLMStep` → 1 条 api_request LogRecord。
/// 包含 L1 + L2 token 属性，duration_ms，model 等必需字段。
pub fn build_api_request_events(
    records: &[HookRecord],
    session_id: &str,
    ctx: &EmissionContext,
) -> Vec<OtelLogRecord> {
    let username = resolve_username();
    let mut events: Vec<OtelLogRecord> = Vec::new();
    let mut turn_number: usize = 0;

    for rec in records {
        let event = rec
            .payload
            .get("hook_event_name")
            .and_then(|v| v.as_str())
            .unwrap_or("");

        if event == "TurnBegin" {
            turn_number += 1;
        }

        if event != "LLMStep" {
            continue;
        }
        let p = &rec.payload;

        // 时间
        let start_ts = p.get("_start_ts").and_then(|v| v.as_f64());
        let end_ts = p.get("_end_ts").and_then(|v| v.as_f64());
        let end_ns = end_ts
            .map(secs_to_nanos_str)
            .unwrap_or_else(|| rec.recorded_at_ns.clone());
        let duration_ms = p.get("_duration_ms").and_then(Value::as_i64).unwrap_or_else(|| match (start_ts, end_ts) {
            (Some(a), Some(b)) => ((b - a).max(0.0) * 1000.0) as i64,
            _ => 0,
        });

        let model_name = p
            .get("model")
            .and_then(|v| v.as_str())
            .unwrap_or("unknown");
        let provider = model_call_system(p, model_name);

        // token usage（三层映射）
        let tu = p.get("token_usage");
        let input_other = tu
            .and_then(|t| t.get("input_other"))
            .and_then(|v| v.as_i64())
            .unwrap_or(0);
        let output_tokens = tu
            .and_then(|t| t.get("output"))
            .and_then(|v| v.as_i64())
            .unwrap_or(0);
        let cache_read = tu
            .and_then(|t| t.get("input_cache_read"))
            .and_then(|v| v.as_i64())
            .unwrap_or(0);
        let cache_creation = tu
            .and_then(|t| t.get("input_cache_creation"))
            .and_then(|v| v.as_i64())
            .unwrap_or(0);
        let input_tokens = input_other + cache_read + cache_creation;
        let total_tokens = input_tokens + output_tokens;

        let mut attrs: Vec<OtelAttribute> = Vec::with_capacity(24);

        // ── 必需公共属性 ──
        attrs.push(OtelAttribute::string("event.name", "api_request"));
        attrs.push(OtelAttribute::string("gen_ai.operation.name", "chat"));
        attrs.push(OtelAttribute::string("ant.username", &username));
        attrs.push(OtelAttribute::string("ant.agent.type", ctx.agent_type));
        attrs.push(OtelAttribute::string("ant.agent.product", ctx.agent_product));
        attrs.push(OtelAttribute::string("gen_ai.conversation.id", session_id));

        if let Some(reason) = p["finish_reason"].as_str() {
            let error = match reason { "error" => Some("provider_error"), "interrupted" => Some("cancelled"), _ => None };
            if let Some(error) = error {
                attrs.push(OtelAttribute::string("error.type", error));
                // Preserve the native termination reason without inventing provider error details.
                attrs.push(OtelAttribute::string("error.message", reason));
            }
        }
        if let Some(id) = p["request_id"].as_str().filter(|s| !s.is_empty()) {
            attrs.push(OtelAttribute::string("agentic.model_call.id", id));
        }
        if let Some(id) = p["message_id"].as_str().filter(|s| !s.is_empty()) {
            attrs.push(OtelAttribute::string("gen_ai.response.id", id));
        }
        // ── 推荐公共属性 ──
        attrs.push(OtelAttribute::int("agentic.turn.number", turn_number as i64));
        attrs.push(OtelAttribute::string("gen_ai.chat.id", session_id));
        attrs.push(OtelAttribute::string("agentic.runtime.name", ctx.runtime_name));
        attrs.push(OtelAttribute::int("agentic.turn.number", turn_number as i64));

        // ── RED: Required ──
        attrs.push(OtelAttribute::string("gen_ai.request.model", model_name));
        attrs.push(OtelAttribute::int("duration_ms", duration_ms));
        attrs.push(OtelAttribute::string("gen_ai.response.model", "unknown"));
        attrs.push(OtelAttribute::string("gen_ai.input.messages", if ctx.log.events_user_prompts {
            p["_input_messages_json"].as_str().filter(|s| !s.is_empty()).unwrap_or("[]")
        } else { REDACTED }));
        let output_json = llm_output_messages(p);
        attrs.push(OtelAttribute::string("gen_ai.output.messages", if ctx.log.events_model_outputs { &output_json } else { REDACTED }));
        attrs.push(OtelAttribute::string("gen_ai.system_instructions", if ctx.log.events_user_prompts { p["_system_instructions_json"].as_str().filter(|s| !s.is_empty()).unwrap_or("\"[UNAVAILABLE]\"") } else { REDACTED }));
        append_code_usage_boundaries(&mut attrs, p);


        // ── Token L1: Normalized（success path） ──
        let code_wire = p["source"] == "kimi-code";
        let input_known = tu.and_then(|t| t.get("input_other")).and_then(Value::as_i64).is_some();
        let output_known = tu.and_then(|t| t.get("output")).and_then(Value::as_i64).is_some();
        if (code_wire && input_known) || (!code_wire && (input_tokens > 0 || output_tokens > 0)) {
            attrs.push(OtelAttribute::int(
                "gen_ai.usage.input_tokens",
                input_tokens,
            ));
        }
        if (code_wire && output_known) || (!code_wire && (input_tokens > 0 || output_tokens > 0)) {
            attrs.push(OtelAttribute::int(
                "gen_ai.usage.output_tokens",
                output_tokens,
            ));
        }

        // ── Recommended ──
        attrs.push(OtelAttribute::string("gen_ai.system", provider));
        attrs.push(OtelAttribute::string("gen_ai.provider.name", model_call_provider(p, model_name, provider)));
        if (code_wire && input_known && output_known) || (!code_wire && total_tokens > 0) {
            attrs.push(OtelAttribute::int(
                "gen_ai.usage.total_tokens",
                total_tokens,
            ));
        }
        if code_wire {
            for (known, name) in [(input_known, "input_tokens"), (output_known, "output_tokens")] {
                if !known { attrs.push(OtelAttribute::string(&format!("agentic.usage.{}.missing", name), "true")); }
            }
        }
        if cache_read > 0 || (code_wire && p["_provider"] != "openai" && tu.and_then(|t| t.get("input_cache_read")).and_then(Value::as_i64).is_some()) {
            attrs.push(OtelAttribute::int(
                "gen_ai.usage.cache_read.input_tokens",
                cache_read,
            ));
        }
        if cache_creation > 0 {
            attrs.push(OtelAttribute::int(
                "gen_ai.usage.cache_creation.input_tokens",
                cache_creation,
            ));
        }

        // ── Token L2: Breakdown ──
        if input_other > 0 || (code_wire && input_known) {
            attrs.push(OtelAttribute::int(
                "agentic.usage.fresh_input_tokens",
                input_other,
            ));
        }
        if cache_read > 0 || (code_wire && p["_provider"] != "openai" && tu.and_then(|t| t.get("input_cache_read")).and_then(Value::as_i64).is_some()) {
            attrs.push(OtelAttribute::int(
                "agentic.usage.cache_read_tokens",
                cache_read,
            ));
        }
        if cache_creation > 0 {
            attrs.push(OtelAttribute::int(
                "agentic.usage.cache_write_tokens",
                cache_creation,
            ));
        }

        events.push(OtelLogRecord::event("api_request", &end_ns, attrs));
    }

    events
}

// ───────────────────────────────────────────────────────────────────────────
// user_prompt events（OTLP LogRecords）
// 对齐 docs/semconv/events.yaml v4.0 的 `user_prompt` 定义。
//
// 每个 TurnBegin → 1 条 user_prompt LogRecord。
// gen_ai.input.messages 为 opt-in（由 ctx.log.events_user_prompts 控制，向后兼容 KIMIHOOKS_EMIT_TOOL_CONTENT）。
// ───────────────────────────────────────────────────────────────────────────

/// 把 TurnBegin 记录转换成 `user_prompt` events。
///
/// 每个 TurnBegin → 1 条 user_prompt LogRecord。
pub fn build_user_prompt_events(
    records: &[HookRecord],
    session_id: &str,
    ctx: &EmissionContext,
) -> Vec<OtelLogRecord> {
    let username = resolve_username();
    let mut events: Vec<OtelLogRecord> = Vec::new();
    let mut turn_number: usize = 0;

    for rec in records {
        let event = rec
            .payload
            .get("hook_event_name")
            .and_then(|v| v.as_str())
            .unwrap_or("");

        if event != "TurnBegin" {
            continue;
        }

        turn_number += 1;

        let ts = rec
            .payload
            .get("_source_timestamp")
            .and_then(|v| v.as_f64());
        let time_ns = ts
            .map(secs_to_nanos_str)
            .unwrap_or_else(|| rec.recorded_at_ns.clone());

        let user_input = rec
            .payload
            .get("user_input")
            .and_then(|v| v.as_str())
            .unwrap_or("");

        let mut attrs: Vec<OtelAttribute> = Vec::with_capacity(16);

        // ── 必需公共属性 ──
        attrs.push(OtelAttribute::string("event.name", "user_prompt"));
        attrs.push(OtelAttribute::string("gen_ai.operation.name", "chat"));
        attrs.push(OtelAttribute::string("ant.username", &username));
        attrs.push(OtelAttribute::string("ant.agent.type", ctx.agent_type));
        attrs.push(OtelAttribute::string("ant.agent.product", ctx.agent_product));
        attrs.push(OtelAttribute::string("gen_ai.conversation.id", session_id));

        // ── 推荐公共属性 ──
        attrs.push(OtelAttribute::int("agentic.turn.number", turn_number as i64));
        attrs.push(OtelAttribute::string("gen_ai.chat.id", session_id));
        attrs.push(OtelAttribute::string("agentic.runtime.name", ctx.runtime_name));
        attrs.push(OtelAttribute::int("agentic.turn.number", turn_number as i64));

        // gen_ai.input.messages — always present, [REDACTED] when off
        {
            let enabled = ctx.log.events_user_prompts
                || (if agentic_otel_common::privacy::resolve_mode("KIMI_COLLECTION_MODE") == Ok("full") { std::env::var("KIMIHOOKS_EMIT_TOOL_CONTENT") } else { Err(std::env::VarError::NotPresent) })
                    .map(|v| v == "1" || v.eq_ignore_ascii_case("true"))
                    .unwrap_or(false);
            let value = if enabled {
                let msgs = vec![OpenAIMessage::user(user_input)];
                serialize_messages_default(&msgs, 1)
            } else {
                REDACTED.to_string()
            };
            attrs.push(OtelAttribute::string("gen_ai.input.messages", &value));
        }

        events.push(OtelLogRecord::event("user_prompt", &time_ns, attrs));
    }

    events
}

// ───────────────────────────────────────────────────────────────────────────
// Metrics（OTLP Sums）
// 严格遵循 docs/semconv/metrics.yaml。产出 counter 类指标：
//   - agent.tool_call.count          按 (agentic.tool.type, error.type?) 拆分
//   - aivision.agentic.up            heartbeat（每次 replay +1）
//   - aivision.agentic.exec_error    ��个未配对 Pre（terminated）+1
//
// agent.tool_call.duration 是 histogram，agentic-otel-common 尚无 OtelHistogram 类型，
// 待 common 层就绪后补齐。
// ───────────────────────────────────────────────────────────────────────────

pub fn build_tool_metrics(records: &[HookRecord], session_id: &str, ctx: &EmissionContext) -> Vec<OtelMetric> {
    let username = resolve_username();
    let now = now_nanos();

    // 按 (agentic.tool.type, error.type 是否存在) 聚合
    // key = (tool_type, Option<error_type>)
    let mut counts: HashMap<(String, Option<String>), i64> = HashMap::new();
    let mut up_count: i64 = 1; // 至少一次 replay
    let mut exec_error_count: HashMap<String, i64> = HashMap::new();

    // 还原配对：与 build_tool_events 同算法但只计数
    let mut pending: HashMap<String, String> = HashMap::new(); // call_id -> tool_name
    for rec in records {
        let event = rec
            .payload
            .get("hook_event_name")
            .and_then(|v| v.as_str())
            .unwrap_or("");
        let call_id = rec
            .payload
            .get("tool_call_id")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        if call_id.is_empty() {
            continue;
        }

        match event {
            "PreToolUse" => {
                let tool_name = rec
                    .payload
                    .get("tool_name")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
                pending.insert(call_id, tool_name);
            }
            "PostToolUse" => {
                if let Some(tool_name) = pending.remove(&call_id) {
                    let info = parse_agentic_tool(&tool_name, &call_id, None);
                    *counts
                        .entry((info.tool_type.to_string(), None))
                        .or_insert(0) += 1;
                    up_count += 1;
                }
            }
            "PostToolUseFailure" => {
                if let Some(tool_name) = pending.remove(&call_id) {
                    let info = parse_agentic_tool(&tool_name, &call_id, None);
                    *counts
                        .entry((info.tool_type.to_string(), Some("tool_error".to_string())))
                        .or_insert(0) += 1;
                    up_count += 1;
                }
            }
            _ => {}
        }
    }
    // 未配对：算 terminated → tool_call.count(error=terminated) + exec_error
    for (call_id, tool_name) in pending {
        let info = parse_agentic_tool(&tool_name, &call_id, None);
        *counts
            .entry((info.tool_type.to_string(), Some("terminated".to_string())))
            .or_insert(0) += 1;
        *exec_error_count
            .entry("terminated".to_string())
            .or_insert(0) += 1;
    }

    let mut metrics: Vec<OtelMetric> = Vec::new();

    // ── agent.tool_call.count（counter）──
    if !counts.is_empty() {
        let mut data_points: Vec<OtelNumberDataPoint> = Vec::new();
        for ((tool_type, err_opt), cnt) in counts {
            let mut attrs = vec![
                OtelAttribute::string("agentic.tool.type", &tool_type),
                OtelAttribute::string("ant.agent.type", ctx.agent_type),
                OtelAttribute::string("ant.username", &username),
            ];
            if !session_id.is_empty() {
                attrs.push(OtelAttribute::string("gen_ai.conversation.id", session_id));
            }
            if let Some(err) = err_opt {
                attrs.push(OtelAttribute::string("agentic.tool.error_type", err));
            }
            data_points.push(OtelNumberDataPoint {
                attributes: attrs,
                time_unix_nano: now.clone(),
                start_time_unix_nano: now.clone(),
                as_int: cnt,
            });
        }
        metrics.push(OtelMetric::counter(
            "agent.tool_call.count",
            "Tool invocation count.",
            "{count}",
            OtelSum {
                data_points,
                aggregation_temporality: 2, // CUMULATIVE
                is_monotonic: true,
            },
        ));
    }

    // ── aivision.agentic.up（counter, heartbeat）──
    {
        let hostname = std::env::var("HOSTNAME")
            .or_else(|_| agentic_otel_common::hostname_from_system())
            .unwrap_or_else(|_| "unknown".to_string());
        let attrs = vec![
            OtelAttribute::string("ant.username", &username),
            OtelAttribute::string("ant.agent.product", ctx.agent_product),
            OtelAttribute::string("ant.agent.type", ctx.agent_type),
            OtelAttribute::string("ant.agent.instance", &hostname),
            OtelAttribute::string("telemetry.sdk.version", env!("CARGO_PKG_VERSION")),
        ];
        metrics.push(OtelMetric::counter(
            "aivision.agentic.up",
            "Component invocation count (heartbeat).",
            "{count}",
            OtelSum {
                data_points: vec![OtelNumberDataPoint {
                    attributes: attrs,
                    time_unix_nano: now.clone(),
                    start_time_unix_nano: now.clone(),
                    as_int: up_count,
                }],
                aggregation_temporality: 2,
                is_monotonic: true,
            },
        ));
    }

    // ── aivision.agentic.exec_error（counter, 仅在有 terminated 时输出）──
    if !exec_error_count.is_empty() {
        let hostname = std::env::var("HOSTNAME")
            .or_else(|_| agentic_otel_common::hostname_from_system())
            .unwrap_or_else(|_| "unknown".to_string());
        let mut data_points: Vec<OtelNumberDataPoint> = Vec::new();
        for (err_type, cnt) in exec_error_count {
            let attrs = vec![
                OtelAttribute::string("ant.username", &username),
                OtelAttribute::string("ant.agent.product", ctx.agent_product),
                OtelAttribute::string("ant.agent.type", ctx.agent_type),
                OtelAttribute::string("ant.agent.instance", &hostname),
                OtelAttribute::string("telemetry.sdk.version", env!("CARGO_PKG_VERSION")),
                OtelAttribute::string("_internal.handler.name", "stop_rebuild"),
                OtelAttribute::string("agentic.error_type", &err_type),
            ];
            data_points.push(OtelNumberDataPoint {
                attributes: attrs,
                time_unix_nano: now.clone(),
                start_time_unix_nano: now.clone(),
                as_int: cnt,
            });
        }
        metrics.push(OtelMetric::counter(
            "aivision.agentic.exec_error",
            "Execution error count.",
            "{count}",
            OtelSum {
                data_points,
                aggregation_temporality: 2,
                is_monotonic: true,
            },
        ));
    }

    metrics
}

// ───────────────────────────────────────────────────────────────────────────
// LLM Metrics + Tool Duration Histogram
// 对齐 docs/semconv/metrics.yaml v4.0。
//
// 新增指标：
//   - agent.llm_request.count     counter(model, system, error?)
//   - agent.llm_request.duration  histogram(model, system)
//   - agent.tool_call.duration    histogram(tool_type, session_id)
//   - agent.tool_call.count       补充 gen_ai.conversation.id 推荐维度
// ───────────────────────────────────────────────────────────────────────────

/// LLM 请求计数与耗时指标。
///
/// 从 LLMStep 合成记录中提取 model / provider / duration / error 信息。
/// 返回 counter + histogram 两种指标。
pub fn build_llm_metrics(
    records: &[HookRecord],
    session_id: &str,
    ctx: &EmissionContext,
) -> Vec<OtelMetric> {
    let username = resolve_username();
    let now = now_nanos();

    // 聚合 LLM 请求数：按 (model, system, Option<error_type>) 分组
    let mut count_map: HashMap<(String, String, Option<String>), i64> = HashMap::new();
    // 聚合 LLM 耗时：按 (model, system) 分组
    let mut duration_map: HashMap<(String, String), Vec<i64>> = HashMap::new();

    for rec in records {
        let event = rec
            .payload
            .get("hook_event_name")
            .and_then(|v| v.as_str())
            .unwrap_or("");
        if event != "LLMStep" {
            continue;
        }
        let p = &rec.payload;
        let model = p
            .get("model")
            .and_then(|v| v.as_str())
            .unwrap_or("unknown")
            .to_string();
        let provider = model_call_system(p, &model).to_string();

        let start_ts = p.get("_start_ts").and_then(|v| v.as_f64());
        let end_ts = p.get("_end_ts").and_then(|v| v.as_f64());
        let duration_ms = p.get("_duration_ms").and_then(Value::as_i64).unwrap_or_else(|| match (start_ts, end_ts) {
            (Some(a), Some(b)) => ((b - a).max(0.0) * 1000.0) as i64,
            _ => 0,
        });

        let error_type = match p["finish_reason"].as_str() {
            Some("error") => Some("provider_error".to_string()),
            Some("interrupted") => Some("cancelled".to_string()),
            _ => None,
        };
        let key = (model.clone(), provider.clone(), error_type);
        *count_map.entry(key).or_insert(0) += 1;
        duration_map
            .entry((model, provider))
            .or_insert_with(Vec::new)
            .push(duration_ms);
    }

    let mut metrics: Vec<OtelMetric> = Vec::new();

    // ── agent.llm_request.count（counter）──
    if !count_map.is_empty() {
        let mut data_points: Vec<OtelNumberDataPoint> = Vec::new();
        for ((model, system, err_opt), cnt) in count_map {
            let mut attrs = vec![
                OtelAttribute::string("gen_ai.request.model", &model),
                OtelAttribute::string("gen_ai.system", &system),
                OtelAttribute::string("ant.agent.type", ctx.agent_type),
                OtelAttribute::string("ant.username", &username),
            ];
            if !session_id.is_empty() {
                attrs.push(OtelAttribute::string("gen_ai.conversation.id", session_id));
            }
            if let Some(err) = err_opt {
                attrs.push(OtelAttribute::string("agentic.llm.error_type", err));
            }
            data_points.push(OtelNumberDataPoint {
                attributes: attrs,
                time_unix_nano: now.clone(),
                start_time_unix_nano: now.clone(),
                as_int: cnt,
            });
        }
        metrics.push(OtelMetric::counter(
            "agent.llm_request.count",
            "LLM API request count.",
            "{count}",
            OtelSum {
                data_points,
                aggregation_temporality: 2,
                is_monotonic: true,
            },
        ));
    }

    // ── agent.llm_request.duration（histogram）──
    // 桶边界对齐 semconv: [500, 1000, 2000, 5000, 10000, 20000, 30000, 60000]
    const LLM_DURATION_BOUNDS: [f64; 8] =
        [500.0, 1000.0, 2000.0, 5000.0, 10000.0, 20000.0, 30000.0, 60000.0];

    if !duration_map.is_empty() {
        let mut data_points = Vec::new();
        for ((model, system), durations) in duration_map {
            let count = durations.len() as u64;
            let sum: f64 = durations.iter().map(|&d| d as f64).sum();
            let min = durations.iter().map(|&d| d as f64).fold(f64::INFINITY, f64::min);
            let max = durations.iter().map(|&d| d as f64).fold(f64::NEG_INFINITY, f64::max);

            // 计算桶计数
            let mut bucket_counts = vec![0u64; LLM_DURATION_BOUNDS.len() + 1];
            for &d in &durations {
                let mut placed = false;
                for (i, &bound) in LLM_DURATION_BOUNDS.iter().enumerate() {
                    if (d as f64) <= bound {
                        bucket_counts[i] += 1;
                        placed = true;
                        break;
                    }
                }
                if !placed {
                    *bucket_counts.last_mut().unwrap() += 1;
                }
            }

            let attrs = vec![
                OtelAttribute::string("gen_ai.request.model", &model),
                OtelAttribute::string("gen_ai.system", &system),
                OtelAttribute::string("ant.agent.type", ctx.agent_type),
                OtelAttribute::string("ant.username", &username),
            ];

            data_points.push(OtelHistogramDataPoint {
                attributes: attrs,
                start_time_unix_nano: now.clone(),
                time_unix_nano: now.clone(),
                count,
                sum,
                min: Some(min),
                max: Some(max),
                explicit_bounds: LLM_DURATION_BOUNDS.to_vec(),
                bucket_counts,
            });
        }
        metrics.push(OtelMetric::histogram(
            "agent.llm_request.duration",
            "LLM API request duration distribution in milliseconds.",
            "ms",
            OtelHistogram {
                data_points,
                aggregation_temporality: 2,
            },
        ));
    }

    // Canonical RED instruments. Retain the previous names for dashboards
    // while the current contract and native gate consume only these names.
    let canonical: Vec<_> = metrics.iter().filter_map(|metric| {
        let name = match metric.name.as_str() {
            "agent.llm_request.count" => "agent.api_request.count",
            "agent.llm_request.duration" => "agent.api_request.duration",
            _ => return None,
        };
        let mut metric = metric.clone();
        metric.name = name.to_string();
        if let Some(sum) = &mut metric.sum {
            sum.aggregation_temporality = 1; // Per-turn interval; no cumulative state is retained.
            let mut merged = std::collections::BTreeMap::<String, OtelNumberDataPoint>::new();
            for mut point in sum.data_points.drain(..) {
                point.attributes.retain(|a| a.key != "agentic.llm.error_type");
                if let Some(mut provider) = point.attributes.iter().find(|a| a.key == "gen_ai.system").cloned() {
                    provider.key = "gen_ai.provider.name".into(); point.attributes.push(provider);
                }
                let key = serde_json::to_string(&point.attributes).expect("typed OTLP attributes serialize");
                if let Some(existing) = merged.get_mut(&key) { existing.as_int += point.as_int; }
                else { merged.insert(key, point); }
            }
            sum.data_points = merged.into_values().collect();
        }
        if let Some(histogram) = &mut metric.histogram {
            histogram.aggregation_temporality = 1;
            for point in &mut histogram.data_points {
                if let Some(mut provider) = point.attributes.iter().find(|a| a.key == "gen_ai.system").cloned() {
                    provider.key = "gen_ai.provider.name".into(); point.attributes.push(provider);
                }
            }
        }
        Some(metric)
    }).collect();
    metrics.extend(canonical);
    metrics
}

/// Tool 调用耗时分布（histogram）。
///
/// 按 (agentic.tool.type, gen_ai.conversation.id) 聚合，
/// 对齐 semconv 的 `agent.tool_call.duration` 定义。
/// 桶边界: [50, 100, 200, 500, 1000, 2000, 5000, 10000, 30000]
pub fn build_tool_duration_metrics(
    records: &[HookRecord],
    session_id: &str,
    ctx: &EmissionContext,
) -> Vec<OtelMetric> {
    let username = resolve_username();
    let now = now_nanos();

    const TOOL_DURATION_BOUNDS: [f64; 9] =
        [50.0, 100.0, 200.0, 500.0, 1000.0, 2000.0, 5000.0, 10000.0, 30000.0];

    // 按 (tool_type, session_id) 聚合 duration
    let mut duration_map: HashMap<(String, String), Vec<i64>> = HashMap::new();
    let mut pending: HashMap<String, (String, String)> = HashMap::new(); // call_id -> (tool_name, start_ns)

    for rec in records {
        let event = rec
            .payload
            .get("hook_event_name")
            .and_then(|v| v.as_str())
            .unwrap_or("");
        let call_id = rec
            .payload
            .get("tool_call_id")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        if call_id.is_empty() {
            continue;
        }

        match event {
            "PreToolUse" => {
                let tool_name = rec
                    .payload
                    .get("tool_name")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
                pending.insert(call_id, (tool_name, rec.recorded_at_ns.clone()));
            }
            "PostToolUse" | "PostToolUseFailure" => {
                if let Some((tool_name, start_ns)) = pending.remove(&call_id) {
                    let start_i: i128 = start_ns.parse().unwrap_or(0);
                    let end_i: i128 = rec.recorded_at_ns.parse().unwrap_or(start_i);
                    let duration_ms = ((end_i - start_i).max(0) / 1_000_000) as i64;
                    let info = parse_agentic_tool(&tool_name, &call_id, None);
                    let key = (info.tool_type.to_string(), session_id.to_string());
                    duration_map
                        .entry(key)
                        .or_insert_with(Vec::new)
                        .push(duration_ms);
                }
            }
            _ => {}
        }
    }

    if duration_map.is_empty() {
        return Vec::new();
    }

    let mut data_points = Vec::new();
    for ((tool_type, sid), durations) in duration_map {
        let count = durations.len() as u64;
        let sum: f64 = durations.iter().map(|&d| d as f64).sum();
        let min = durations.iter().map(|&d| d as f64).fold(f64::INFINITY, f64::min);
        let max = durations.iter().map(|&d| d as f64).fold(f64::NEG_INFINITY, f64::max);

        let mut bucket_counts = vec![0u64; TOOL_DURATION_BOUNDS.len() + 1];
        for &d in &durations {
            let mut placed = false;
            for (i, &bound) in TOOL_DURATION_BOUNDS.iter().enumerate() {
                if (d as f64) <= bound {
                    bucket_counts[i] += 1;
                    placed = true;
                    break;
                }
            }
            if !placed {
                *bucket_counts.last_mut().unwrap() += 1;
            }
        }

        let mut attrs = vec![
            OtelAttribute::string("agentic.tool.type", &tool_type),
            OtelAttribute::string("ant.agent.type", ctx.agent_type),
            OtelAttribute::string("ant.username", &username),
        ];
        if !sid.is_empty() {
            attrs.push(OtelAttribute::string("gen_ai.conversation.id", &sid));
        }

        data_points.push(OtelHistogramDataPoint {
            attributes: attrs,
            start_time_unix_nano: now.clone(),
            time_unix_nano: now.clone(),
            count,
            sum,
            min: Some(min),
            max: Some(max),
            explicit_bounds: TOOL_DURATION_BOUNDS.to_vec(),
            bucket_counts,
        });
    }

    vec![OtelMetric::histogram(
        "agent.tool_call.duration",
        "Tool call duration distribution in milliseconds.",
        "ms",
        OtelHistogram {
            data_points,
            aggregation_temporality: 2,
        },
    )]
}

// ───────────────────────────────────────────────────────────────────────────
// Token & Session Metrics
// 对齐 docs/semconv/metrics.yaml v4.0。
//
//   - agent.session.count        counter(agent_type, username)
//   - agent.token.usage          counter(agent_type, type=input/output)
//   - agent.cache.read_tokens    counter(model, agent_type)
//   - agent.cache.write_tokens   counter(model, agent_type)
// ───────────────────────────────────────────────────────────────────────────

/// Session & Token 指标。
///
/// - `agent.session.count`: 1 per turn（Stop hook 每次触发 = 1 session 活跃）
/// - `agent.token.usage`: 按 input/output 分类的 token 计数
/// - `agent.cache.read_tokens` / `agent.cache.write_tokens`: 缓存 token 计数
pub fn build_session_token_metrics(
    records: &[HookRecord],
    _session_id: &str,
    ctx: &EmissionContext,
) -> Vec<OtelMetric> {
    let username = resolve_username();
    let now = now_nanos();
    let mut metrics: Vec<OtelMetric> = Vec::new();

    // ── agent.session.count ──
    metrics.push(OtelMetric::counter(
        "agent.session.count",
        "Agent session start count.",
        "{count}",
        OtelSum {
            data_points: vec![OtelNumberDataPoint {
                attributes: vec![
                    OtelAttribute::string("ant.agent.type", ctx.agent_type),
                    OtelAttribute::string("ant.username", &username),
                ],
                time_unix_nano: now.clone(),
                start_time_unix_nano: now.clone(),
                as_int: 1,
            }],
            aggregation_temporality: 2,
            is_monotonic: true,
        },
    ));

    // 聚合 token 数据
    let mut total_input: i64 = 0;
    let mut total_output: i64 = 0;
    let mut total_cache_read: i64 = 0;
    let mut total_cache_write: i64 = 0;
    let mut input_known = false;
    let mut output_known = false;
    let mut cache_read_known = false;
    let mut cache_write_known = false;
    let mut models = std::collections::HashSet::new();

    for rec in records {
        let event = rec
            .payload
            .get("hook_event_name")
            .and_then(|v| v.as_str())
            .unwrap_or("");
        if event != "LLMStep" {
            continue;
        }
        let tu = rec.payload.get("token_usage");
        if let Some(model) = rec.payload["model"].as_str().filter(|s| !s.is_empty()) { models.insert(model); }
        input_known |= tu.and_then(|t| t.get("input_other")).and_then(Value::as_i64).is_some();
        output_known |= tu.and_then(|t| t.get("output")).and_then(Value::as_i64).is_some();
        cache_read_known |= tu.and_then(|t| t.get("input_cache_read")).and_then(Value::as_i64).filter(|count| *count > 0 || rec.payload["_provider"] != "openai").is_some();
        cache_write_known |= rec.payload["_provider"] != "openai" && tu.and_then(|t| t.get("input_cache_creation")).and_then(Value::as_i64).is_some();
        let input_other = tu
            .and_then(|t| t.get("input_other"))
            .and_then(|v| v.as_i64())
            .unwrap_or(0);
        let output = tu
            .and_then(|t| t.get("output"))
            .and_then(|v| v.as_i64())
            .unwrap_or(0);
        let cache_read = tu
            .and_then(|t| t.get("input_cache_read"))
            .and_then(|v| v.as_i64())
            .unwrap_or(0);
        let cache_creation = tu
            .and_then(|t| t.get("input_cache_creation"))
            .and_then(|v| v.as_i64())
            .unwrap_or(0);

        // Counter records fresh input; LLM spans/logs retain total input including cache.
        total_input += input_other;
        total_output += output;
        total_cache_read += cache_read;
        total_cache_write += cache_creation;
    }

    let model = if models.len() == 1 { *models.iter().next().unwrap() } else { "unknown" };

    // ── agent.token.usage ──
    if input_known || output_known {
        let mut data_points = Vec::new();
        if input_known {
            data_points.push(OtelNumberDataPoint {
                attributes: vec![
                    OtelAttribute::string("ant.agent.type", ctx.agent_type),
                    OtelAttribute::string("ant.username", &username),
                    OtelAttribute::string("gen_ai.token.type", "input"),
                ],
                time_unix_nano: now.clone(),
                start_time_unix_nano: now.clone(),
                as_int: total_input,
            });
        }
        if output_known {
            data_points.push(OtelNumberDataPoint {
                attributes: vec![
                    OtelAttribute::string("ant.agent.type", ctx.agent_type),
                    OtelAttribute::string("ant.username", &username),
                    OtelAttribute::string("gen_ai.token.type", "output"),
                ],
                time_unix_nano: now.clone(),
                start_time_unix_nano: now.clone(),
                as_int: total_output,
            });
        }
        metrics.push(OtelMetric::counter(
            "agent.token.usage",
            "Token usage counter.",
            "{token}",
            OtelSum {
                data_points,
                aggregation_temporality: 2,
                is_monotonic: true,
            },
        ));
    }

    // ── agent.cache.read_tokens ──
    if cache_read_known {
        metrics.push(OtelMetric::counter(
            "agent.cache.read_tokens",
            "Cache read token count.",
            "{token}",
            OtelSum {
                data_points: vec![OtelNumberDataPoint {
                    attributes: vec![
                        OtelAttribute::string("gen_ai.request.model", model),
                        OtelAttribute::string("ant.agent.type", ctx.agent_type),
                        OtelAttribute::string("ant.username", &username),
                    ],
                    time_unix_nano: now.clone(),
                    start_time_unix_nano: now.clone(),
                    as_int: total_cache_read,
                }],
                aggregation_temporality: 2,
                is_monotonic: true,
            },
        ));
    }

    // ── agent.cache.write_tokens ──
    if cache_write_known {
        metrics.push(OtelMetric::counter(
            "agent.cache.write_tokens",
            "Cache write token count.",
            "{token}",
            OtelSum {
                data_points: vec![OtelNumberDataPoint {
                    attributes: vec![
                        OtelAttribute::string("gen_ai.request.model", model),
                        OtelAttribute::string("ant.agent.type", ctx.agent_type),
                        OtelAttribute::string("ant.username", &username),
                    ],
                    time_unix_nano: now.clone(),
                    start_time_unix_nano: now.clone(),
                    as_int: total_cache_write,
                }],
                aggregation_temporality: 2,
                is_monotonic: true,
            },
        ));
    }

    metrics
}

// ───────────────────────────────────────────────────────────────────────────
//
//   Trace
//   └── Root span (Turn N)            [INTERNAL, gen_ai.operation.name=invoke_agent]
//       ├── LLM span (LLMStep n=1)    [INTERNAL, gen_ai.operation.name=chat]
//       ├── Tool span (Shell #1)      [INTERNAL, gen_ai.operation.name=execute_tool]
//       │   └── Subagent span         [INTERNAL, gen_ai.operation.name=invoke_agent]
//       │       ├── LLM span          (子 LLMStep)
//       │       └── Tool span         (子 ToolCall/Result)
//       ├── Compaction span (C8)      [INTERNAL, gen_ai.operation.name=execute_tool]
//       └── ...
//
// 复用策略（最大复用 otel-common）：
// - 消息构造一律走 OpenAIMessage::user/assistant/with_tool_calls/tool_result
// - messages 序列化一律走 serialize_messages_default（智能软截断 + 不破 JSON）
// - tool 参数/输出截断走 truncate_value（>20K 走 sha256 + 元数据）
// - span 结构沿用 OtelSpan / OtelStatus / OtelAttribute（typed OTLP）
// ───────────────────────────────────────────────────────────────────────────

/// 一个 turn 的所有 span（root + 各类子 span）打包返回。
///
/// `root_span_id` 暴露给调用方，便于把额外的子 span（compaction / subagent）也挂到同一个父下。
pub struct TurnSpans {
    pub root_span_id: String,
    pub spans: Vec<OtelSpan>,
}

/// 多 turn 的结果：每个 turn 一个独立 trace。
pub struct AllTurnsResult {
    /// 每个 turn 的 (trace_id, root_span_id, spans)。
    pub turns: Vec<(String, TurnSpans)>,
}

/// Build events against the exact spans exported alongside them. Never regenerate
/// random child IDs for logs, and never associate a model/tool event by timestamp.
/// `latest_only` is the Stop path: previous turns were already exported by their Stop.
pub fn build_correlated_events(
    records: &[HookRecord], session_id: &str, ctx: &EmissionContext,
    result: &AllTurnsResult, latest_only: bool,
) -> Vec<OtelLogRecord> {
    fn text_attr<'a>(attrs: &'a [OtelAttribute], key: &str) -> Option<&'a str> {
        attrs.iter().find(|a| a.key == key).and_then(|a| match &a.value {
            agentic_otel_common::otlp::OtelAnyValue::String { string_value } => Some(string_value.as_str()),
            _ => None,
        })
    }
    let slices = split_records_by_turn(records);
    let mut output = Vec::new();
    for (index, ((trace_id, turn), slice)) in result.turns.iter().zip(slices).enumerate() {
        if latest_only && index + 1 != result.turns.len() { continue; }
        let mut events = build_tool_events(slice, session_id, ctx);
        events.extend(build_api_request_events(slice, session_id, ctx));
        events.extend(build_user_prompt_events(slice, session_id, ctx));
        events.extend(build_tool_decision_events(slice, session_id, ctx));
        events.extend(build_compaction_events(slice, session_id, ctx));
        let llms: Vec<_> = turn.spans.iter().filter(|s| s.parent_span_id == turn.root_span_id && text_attr(&s.attributes, "gen_ai.span.kind") == Some("LLM")).collect();
        let mut model_index = 0;
        let mut compaction_index = 0;
        for event in &mut events {
            let name = text_attr(&event.attributes, "event.name").unwrap_or("");
            let matched = match name {
                "api_request" => {
                    let current = llms.get(model_index).copied();
                    model_index += 1;
                    match text_attr(&event.attributes, "agentic.model_call.id") {
                        Some(id) => llms.iter().copied().find(|s| text_attr(&s.attributes, "agentic.model_call.id") == Some(id)),
                        None => current, // Legacy LLMSteps and LLM spans preserve the same source order.
                    }
                }
                "tool_result" | "tool_decision" => text_attr(&event.attributes, "gen_ai.tool.call.id").and_then(|id|
                    turn.spans.iter().find(|s| span_tool_call_id(s) == Some(id) || text_attr(&s.attributes, "agentic.subagent.parent_tool_call_id") == Some(id))),
                "compaction" | "compaction_result" => {
                    let span = turn.spans.iter().filter(|s| text_attr(&s.attributes, "gen_ai.tool.name") == Some("compaction")).nth(compaction_index);
                    compaction_index += 1;
                    span
                }
                "user_prompt" => turn.spans.iter().find(|s| s.span_id == turn.root_span_id),
                _ => None,
            };
            if let Some(span) = matched {
                event.trace_id = Some(trace_id.clone());
                event.span_id = Some(span.span_id.clone());
            }
            let chat_id = matched.and_then(|span| text_attr(&span.attributes, "gen_ai.chat.id")).map(str::to_string).unwrap_or_else(|| format!("{session_id}-turn-{}", index + 1));
            event.attributes.retain(|a| !matches!(a.key.as_str(), "ant.agent.type" | "ant.agent.product" | "gen_ai.chat.id" | "agentic.turn.number"));
            event.attributes.push(OtelAttribute::string("gen_ai.chat.id", &chat_id));
            event.attributes.push(OtelAttribute::int("agentic.turn.number", (index + 1) as i64));
        }
        events.sort_by_key(|e| e.time_unix_nano.parse::<u128>().unwrap_or(0));
        for (sequence, event) in events.iter_mut().enumerate() {
            event.attributes.push(OtelAttribute::int("event.sequence", sequence as i64));
        }
        output.extend(events);
    }
    output
}

/// 按 TurnBegin 边界把 records 切分成多个 turn 的 record slice。
///
/// 规则：
/// - 每遇到一个 `TurnBegin` 就开始一个新 turn
/// - TurnBegin 之前的"无主"records（如 metadata）归入第一个 turn
/// - 没有 TurnBegin 时，所有 records 作为一个 turn（向后兼容）
pub fn split_records_by_turn(records: &[HookRecord]) -> Vec<&[HookRecord]> {
    let mut boundaries: Vec<usize> = Vec::new();
    for (i, rec) in records.iter().enumerate() {
        let event = rec
            .payload
            .get("hook_event_name")
            .and_then(|v| v.as_str())
            .unwrap_or("");
        if event == "TurnBegin" {
            boundaries.push(i);
        }
    }
    if boundaries.is_empty() {
        // 没有 TurnBegin：整个 records 作为一个 turn
        return vec![records];
    }
    let mut slices: Vec<&[HookRecord]> = Vec::new();
    for (idx, &start) in boundaries.iter().enumerate() {
        let end = if idx + 1 < boundaries.len() {
            boundaries[idx + 1]
        } else {
            records.len()
        };
        // 第一个 turn 可能要包含 TurnBegin 之前的 records（如 SessionStart）
        let actual_start = if idx == 0 { 0 } else { start };
        slices.push(&records[actual_start..end]);
    }
    slices
}

/// 顶层入口（多 turn）：按 TurnBegin/TurnEnd 边界拆分 records，
/// 每个 turn 生成独立 trace（独立 trace_id + root span）。
///
/// `session_id` 写入 `gen_ai.conversation.id`（C1 必需属性）。
/// 返回 None 当 records 为空或无任何可生成的 span。
pub fn build_all_turns(records: &[HookRecord], log_cfg: &ContentLogConfig, model_name: &str) -> Option<AllTurnsResult> {
    build_all_turns_with_session(records, "", log_cfg, model_name)
}

/// 带 session_id 的多 turn 入口。
pub fn build_all_turns_with_session(
    records: &[HookRecord],
    session_id: &str,
    log_cfg: &ContentLogConfig,
    model_name: &str,
) -> Option<AllTurnsResult> {
    let turn_slices = split_records_by_turn(records);
    let mut turns: Vec<(String, TurnSpans)> = Vec::new();
    for (turn_idx, slice) in turn_slices.iter().enumerate() {
        let turn_number = turn_idx + 1;
        // 确定性 trace_id：同一 session + turn_number 总是生成相同 trace_id，
        // 使得 Stop hook 多次触发（subagent + 主 turn）上报同一 trace_id，
        // Langfuse 自动合并而非产生重复 trace。
        let trace_id = if session_id.is_empty() {
            otel_trace_id()
        } else {
            deterministic_trace_id(session_id, turn_number)
        };
        if let Some(t) = build_turn_spans_numbered(slice, &trace_id, turn_number, session_id, log_cfg, model_name) {
            turns.push((trace_id, t));
        }
    }
    if turns.is_empty() {
        return None;
    }
    Some(AllTurnsResult { turns })
}

/// 基于 session_id + turn_number 生成确定性 trace_id（32 hex chars）。
/// 基于 session_id + turn_number 生成确定性 trace_id（32 hex chars）。
/// 同一 session 的同一 turn 永远返回同一个 trace_id，避免重复上报。
fn deterministic_trace_id(session_id: &str, turn_number: usize) -> String {
    use std::hash::{Hash, Hasher};
    let mut hasher = std::collections::hash_map::DefaultHasher::new();
    session_id.hash(&mut hasher);
    turn_number.hash(&mut hasher);
    let high = hasher.finish();
    // 追加一个 salt 再 hash 一次，得到低 8 字节
    "trace_id_low".hash(&mut hasher);
    let low = hasher.finish();
    format!("{:016x}{:016x}", high, low)
}

/// 基于 session_id + turn_number 生成确定性 span_id（16 hex chars）。
/// 与 deterministic_trace_id 配合使用：同一 turn 的 root span 始终有固定 span_id，
/// 保证 subagent Stop 和主 Stop 两次上报能被 Langfuse 幂等覆盖（而非产生两个并列的 root span）。
fn deterministic_span_id(session_id: &str, turn_number: usize) -> String {
    use std::hash::{Hash, Hasher};
    let mut hasher = std::collections::hash_map::DefaultHasher::new();
    session_id.hash(&mut hasher);
    turn_number.hash(&mut hasher);
    // 用不同 salt 与 trace_id 区分，避免低位碰撞
    "span_id_root".hash(&mut hasher);
    let h = hasher.finish();
    format!("{:016x}", h)
}

/// 单 turn 入口（向后兼容）：等同 `build_turn_spans_numbered(records, trace_id, 1)`。
pub fn build_turn_spans(records: &[HookRecord], trace_id: &str, log_cfg: &ContentLogConfig, model_name: &str) -> Option<TurnSpans> {
    build_turn_spans_numbered(records, trace_id, 1, "", log_cfg, model_name)
}

/// 把一个 turn 的 hook_log records 转换成完整的 span 树。
///
/// 内部按顺序构造：
/// 1. Root span（C1）：扫 TurnBegin/TurnEnd 取边界 + 用 OpenAIMessage 拼 input/output
/// 2. LLM spans（C2）：每条 LLMStep → 1 个 generation span，挂在 root 下
/// 3. Tool spans（C3）：复用 build_tool_spans，parent = root_span_id
/// 4. Subagent spans（C6）：每个 SubagentEvent 序列 → 1 个 subagent span
///    直接挂在 root 下；对应的 Agent tool span 折叠为 subagent span，避免双层嵌套
/// 5. Compaction spans（C8）：本期暂未在 wire.jsonl 观察到原生事件，预留接口
///
/// 当 records 内既无 TurnBegin 也无任何已知事件时返回 `None`（避免空 turn 污染）。
fn build_turn_spans_numbered(
    records: &[HookRecord],
    trace_id: &str,
    turn_number: usize,
    session_id: &str,
    log_cfg: &ContentLogConfig,
    model_name: &str,
) -> Option<TurnSpans> {
    // 先尝试构造 root span；没有 root 就直接退化为"扁平 tool span 列表"（向后兼容）
    let root = build_root_span_numbered(records, trace_id, turn_number, session_id, "", log_cfg);
    let (root_span_id, mut spans) = match root {
        Some(r) => (r.span_id.clone(), vec![r]),
        None => {
            // 无 root：兼容老路径，所有 tool span 走 parent="" 即可
            let mut tool_spans = build_tool_spans(records, trace_id, "", session_id, log_cfg);
            if tool_spans.is_empty() {
                return None;
            }
            complete_turn_identity(&mut tool_spans, session_id, turn_number);
            return Some(TurnSpans {
                root_span_id: String::new(),
                spans: tool_spans,
            });
        }
    };

    // LLM spans：parent = root
    let mut llm_spans = build_llm_spans(records, trace_id, &root_span_id, session_id, turn_number, log_cfg, model_name);
    spans.append(&mut llm_spans);

    // Tool spans（顶层 turn 内的工具，不含 subagent 内的工具）
    let mut tool_spans = build_tool_spans(records, trace_id, &root_span_id, session_id, log_cfg);
    // 有 SubagentEvent 的 Agent 调用会被折叠成 subagent root span。先记录 Agent tool
    // 的真实起止时间，再从顶层 tool 列表中移除，避免 Langfuse 展示两级 subagent。
    let subagent_parent_ids = collect_subagent_parent_call_ids(records);
    let subagent_tool_bounds: HashMap<String, (String, String)> = tool_spans
        .iter()
        .filter_map(|s| {
            s.attributes
                .iter()
                .find(|a| a.key == "gen_ai.tool.call.id")
                .and_then(|a| {
                    if let agentic_otel_common::otlp::OtelAnyValue::String { string_value } =
                        &a.value
                    {
                        if subagent_parent_ids.contains(string_value) {
                            Some((
                                string_value.clone(),
                                (s.start_time_unix_nano.clone(), s.end_time_unix_nano.clone()),
                            ))
                        } else {
                            None
                        }
                    } else {
                        None
                    }
                })
        })
        .collect();
    tool_spans.retain(|s| {
        span_tool_call_id(s)
            .map(|id| !subagent_parent_ids.contains(id))
            .unwrap_or(true)
    });
    spans.append(&mut tool_spans);

    // Subagent spans：parent = root；Agent tool span 已折叠为此 subagent span。
    let mut sub_spans = build_subagent_spans(
        records,
        trace_id,
        &root_span_id,
        &subagent_tool_bounds,
        session_id,
        turn_number,
        log_cfg,
        model_name,
    );
    spans.append(&mut sub_spans);

    // Compaction spans：parent = root
    let mut comp_spans = build_compaction_spans(records, trace_id, &root_span_id);
    spans.append(&mut comp_spans);

    // API error / retry spans (D4)：parent = root
    let mut error_spans = build_api_error_spans(records, trace_id, &root_span_id);
    spans.append(&mut error_spans);

    complete_turn_identity(&mut spans, session_id, turn_number);

    Some(TurnSpans {
        root_span_id,
        spans,
    })
}

fn complete_turn_identity(spans: &mut [OtelSpan], session_id: &str, turn_number: usize) {
    let chat_id = if session_id.is_empty() {
        format!("turn-{turn_number}")
    } else {
        format!("{session_id}-turn-{turn_number}")
    };
    for span in spans {
        if !span.attributes.iter().any(|attribute| attribute.key == "gen_ai.chat.id") {
            span.attributes.push(OtelAttribute::string("gen_ai.chat.id", &chat_id));
        }
    }
}

fn collect_subagent_parent_call_ids(records: &[HookRecord]) -> std::collections::HashSet<String> {
    records
        .iter()
        .filter(|rec| {
            rec.payload.get("hook_event_name").and_then(|v| v.as_str()) == Some("SubagentEvent")
        })
        .filter_map(|rec| {
            rec.payload
                .get("parent_tool_call_id")
                .and_then(|v| v.as_str())
                .filter(|s| !s.is_empty())
                .map(|s| s.to_string())
        })
        .collect()
}

fn span_tool_call_id(span: &OtelSpan) -> Option<&str> {
    span.attributes
        .iter()
        .find(|a| a.key == "gen_ai.tool.call.id")
        .and_then(|a| match &a.value {
            agentic_otel_common::otlp::OtelAnyValue::String { string_value } => {
                Some(string_value.as_str())
            }
            _ => None,
        })
}

/// 构造 C1 turn root span。
///
/// - 时间边界：优先 TurnBegin / TurnEnd 的 `_source_timestamp`（秒，需 ×1e9 转纳秒），
///   缺失则退化到 records 首/尾的 `recorded_at_ns`。
/// - input messages：所有 TurnBegin.user_input 拼成 user 消息，**user 在末尾**（C9 root 规则），
///   protected_tail=1 保证 user 不被截。
/// - output messages：所有 LLMStep.output_text → assistant 消息（按出现顺序）。
/// - 失败汇总：如果有任何 ToolFailure / Terminated → 写 status=ERROR。
pub fn build_root_span(records: &[HookRecord], trace_id: &str, log_cfg: &ContentLogConfig) -> Option<OtelSpan> {
    build_root_span_numbered(records, trace_id, 1, "", "", log_cfg)
}

/// 构造 C1 turn root span（带 turn 编号）。
fn build_root_span_numbered(
    records: &[HookRecord],
    trace_id: &str,
    turn_number: usize,
    session_id: &str,
    cwd: &str,
    log_cfg: &ContentLogConfig,
) -> Option<OtelSpan> {
    let username = resolve_username();

    // 提取 turn 边界
    let mut turn_begin_ts: Option<f64> = None;
    let mut turn_end_ts: Option<f64> = None;
    let mut user_inputs: Vec<String> = Vec::new();
    let mut user_content: Vec<Value> = Vec::new();
    let mut llm_steps: Vec<&Value> = Vec::new();
    let mut turn_end = None;
    let mut pending_tools = HashSet::new();
    let mut kimi_code = false;
    let mut tool_failed = false;

    let mut saw_any_event = false;
    for rec in records {
        let event = rec
            .payload
            .get("hook_event_name")
            .and_then(|v| v.as_str())
            .unwrap_or("");
        let ts = rec
            .payload
            .get("_source_timestamp")
            .and_then(|v| v.as_f64());
        kimi_code |= rec.payload["source"] == "kimi-code";
        match event {
            "TurnBegin" => {
                saw_any_event = true;
                if turn_begin_ts.is_none() {
                    turn_begin_ts = ts;
                }
                if let Some(blocks) = rec.payload.get("user_content").or_else(|| rec.payload.get("user_input")).and_then(agentic_otel_common::media::content_blocks) { user_content.extend(blocks); }
                if let Some(s) = rec.payload.get("user_input").and_then(|v| v.as_str()) {
                    if !s.is_empty() {
                        user_inputs.push(s.to_string());
                    }
                }
            }
            "TurnEnd" => {
                saw_any_event = true;
                turn_end_ts = ts;
                turn_end = Some(&rec.payload);
            }
            "LLMStep" => {
                saw_any_event = true;
                llm_steps.push(&rec.payload);
            }
            "PreToolUse" => {
                if let Some(id) = rec.payload["tool_call_id"].as_str().filter(|id| !id.is_empty()) {
                    pending_tools.insert(id);
                }
            }
            "PostToolUse" | "PostToolUseFailure" => {
                tool_failed |= event == "PostToolUseFailure";
                if let Some(id) = rec.payload["tool_call_id"].as_str() {
                    pending_tools.remove(id);
                }
            }
            _ => {}
        }
    }

    if !saw_any_event {
        return None;
    }

    // 时间戳：优先 wire.jsonl 原始秒级浮点 → 纳秒
    let start_ns = turn_begin_ts
        .map(secs_to_nanos_str)
        .or_else(|| records.first().map(|r| r.recorded_at_ns.clone()))
        .unwrap_or_else(now_nanos);
    let end_ns = turn_end_ts
        .or_else(|| records.iter().filter_map(|r| r.payload["_step_end_ts"].as_f64()).reduce(f64::max))
        .map(secs_to_nanos_str)
        .or_else(|| records.last().map(|r| r.recorded_at_ns.clone()))
        .unwrap_or_else(now_nanos);

    // input messages（C9 root 规则：user 在末尾，protected_tail=1）
    let mut input_msgs: Vec<OpenAIMessage> = Vec::new();
    let combined_user = user_inputs.join("\n");
    if !combined_user.is_empty() || !user_content.is_empty() {
        input_msgs.push(OpenAIMessage::user(&combined_user).with_content(&Value::Array(user_content)));
    }
    let input_json = serialize_messages_default(&input_msgs, 1);

    // output messages：按 LLMStep 顺序拼 assistant message
    let mut output_msgs: Vec<OpenAIMessage> = Vec::new();
    for step in &llm_steps {
        let text = step
            .get("output_text")
            .and_then(|v| v.as_str())
            .unwrap_or("");
        let thinking = step.get("thinking").and_then(|v| v.as_str()).unwrap_or("");
        if text.is_empty() && thinking.is_empty() && agentic_otel_common::media::content_blocks(&step["output_content"]).is_none() {
            continue;
        }
        let mut m = OpenAIMessage::assistant(text).with_content(&step["output_content"]);
        if !thinking.is_empty() {
            m.reasoning_content = Some(thinking.to_string());
        }
        output_msgs.push(m);
    }
    let output_json = serialize_messages_default(&output_msgs, 0);

    let mut attrs: Vec<OtelAttribute> = Vec::with_capacity(20);
    attrs.push(OtelAttribute::string("gen_ai.span.kind", "CHAT"));
    attrs.push(OtelAttribute::string(
        "gen_ai.operation.name",
        "invoke_agent",
    ));
    attrs.push(OtelAttribute::string("ant.username", &username));
    attrs.push(OtelAttribute::string("user.id", &username));
    // gen_ai.input/output.messages — always present, [REDACTED] when off
    attrs.push(OtelAttribute::string(
        "gen_ai.input.messages",
        if log_cfg.spans_user_prompts { &input_json } else { REDACTED },
    ));
    attrs.push(OtelAttribute::string(
        "gen_ai.output.messages",
        if log_cfg.spans_model_outputs { &output_json } else { REDACTED },
    ));
    attrs.push(OtelAttribute::int(
        "agentic.turn.number",
        turn_number as i64,
    ));
    // C1 必需属性
    attrs.push(OtelAttribute::string("agentic.runtime.name", "kimi-cli"));
    if !session_id.is_empty() {
        attrs.push(OtelAttribute::string("gen_ai.conversation.id", session_id));
    }
    // gen_ai.chat.id：稳定标识，与 cchooks 对齐（{session_id}-turn-{n}）
    let chat_id = if !session_id.is_empty() {
        format!("{}-turn-{}", session_id, turn_number)
    } else {
        format!("turn-{}", turn_number)
    };
    attrs.push(OtelAttribute::string("gen_ai.chat.id", &chat_id));
    // agentic.runtime.cwd：工作目录（与 cchooks 对齐）
    // 优先用调用方传入的 cwd，其次从 records 中提取
    let effective_cwd = if !cwd.is_empty() {
        cwd.to_string()
    } else {
        records.iter()
            .find_map(|r| r.payload.get("cwd").and_then(|v| v.as_str()).filter(|s| !s.is_empty()))
            .unwrap_or("")
            .to_string()
    };
    if !effective_cwd.is_empty() {
        attrs.push(OtelAttribute::string("agentic.runtime.cwd", &effective_cwd));
    }

    // Kimi Code has terminal evidence separate from the tool results.
    let reason = turn_end.and_then(|end| end["reason"].as_str());
    let status = match reason {
        Some("completed") => OtelStatus::ok(),
        Some(reason @ ("cancelled" | "failed" | "blocked")) => {
            let error = if reason == "failed" { "turn_error" } else { reason };
            attrs.push(OtelAttribute::string("error.type", error));
            OtelStatus::error(error)
        }
        // Legacy Python snapshots may omit the outcome even after StopFailure.
        // Preserve their historical classification until that hook can be bound to a turn.
        _ if !kimi_code && tool_failed => OtelStatus::error("turn contained tool failure"),
        _ if !kimi_code => OtelStatus::ok(),
        // Kimi Code's ordinary Stop runs before turn.ended is appended. Use
        // the latest finished step and receipts, never the presence of output.
        None if turn_end.is_none() && pending_tools.is_empty() && llm_steps.last().is_some_and(|step| {
            step["source"] == "kimi-code"
                && step["finish_reason"] == "end_turn"
                && step["_step_end_ts"].is_number()
                && step["_turn_snapshot_incomplete"] != true
        }) => OtelStatus::ok(),
        _ => OtelStatus::unset(),
    };

    // 使用确定性 span_id：同一 session + turn_number 始终产生相同的 root span_id。
    // 配合 deterministic_trace_id，保证 subagent Stop 和主 Stop 两次上报
    // 的 root span 完全相同（trace_id + span_id 均固定），Langfuse 幂等覆盖。
    let span_id = if !session_id.is_empty() {
        deterministic_span_id(session_id, turn_number)
    } else {
        otel_span_id()
    };

    Some(OtelSpan {
        trace_id: trace_id.to_string(),
        span_id,
        parent_span_id: String::new(),
        name: format!("Turn {}", turn_number),
        kind: SPAN_KIND_INTERNAL,
        start_time_unix_nano: start_ns,
        end_time_unix_nano: end_ns,
        attributes: attrs,
        status,
    })
}

fn llm_output_messages(p: &Value) -> String {
    let calls: Vec<ToolCall> = p.get("tool_calls").and_then(Value::as_array).into_iter().flatten()
        .filter_map(|call| serde_json::from_value(call.clone()).ok()).collect();
    let mut message = OpenAIMessage::assistant_with_tool_calls(p["output_text"].as_str(), calls).with_content(&p["output_content"]);
    if let Some(thinking) = p["thinking"].as_str().filter(|s| !s.is_empty()) { message.reasoning_content = Some(thinking.to_string()); }
    serialize_messages_default(&[message], 0)
}

fn model_call_system<'a>(p: &'a Value, model: &'a str) -> &'a str {
    if let Some(provider) = p["_provider"].as_str().filter(|value| !value.is_empty()) {
        return provider;
    }
    if p["source"] == "kimi-code" {
        // Keep the full routed model ID and use its first namespace as the
        // provider identifier, not the upstream model publisher.
        agentic_otel_common::extract_provider_name(model).unwrap_or("unknown")
    } else {
        "moonshot"
    }
}

fn model_call_provider<'a>(p: &Value, model: &'a str, system: &'a str) -> &'a str {
    if p["source"] == "kimi-code" || p["_provider"].as_str().is_some_and(|value| !value.is_empty()) {
        system
    } else {
        agentic_otel_common::extract_provider_name(model).unwrap_or(system)
    }
}

fn append_code_usage_boundaries(attrs: &mut Vec<OtelAttribute>, p: &Value) {
    if p["source"] != "kimi-code" { return; }
    // OpenAI adapter discards provider cache-write and reasoning counts. Its
    // fixed cacheCreation=0 is not a reported measurement of that bucket.
    for bucket in ["reasoning_tokens", "tool_tokens"] {
        attrs.push(OtelAttribute::string(&format!("agentic.usage.{bucket}.missing"), "true"));
    }
    if p["_provider"] == "openai" {
        if p["token_usage"]["input_cache_read"].as_i64().unwrap_or(0) == 0 { attrs.push(OtelAttribute::string("agentic.usage.cache_read_tokens.missing", "true")); }
        attrs.push(OtelAttribute::string("agentic.usage.cache_write_tokens.missing", "true"));
    }
}

/// 构造 C2 LLM step spans。
///
/// 每条 `LLMStep` hook 事件 → 1 个 generation span，包含：
/// - `gen_ai.span.kind = LLM` + `gen_ai.operation.name = chat`
/// - `gen_ai.input.messages`：从 `_input_messages_json` 读取（context.jsonl 注入），
///   无 context 时退化为空数组
/// - `gen_ai.output.messages`：assistant + reasoning_content
/// - token usage：`input_other` → input_tokens；`output` → output_tokens；
///   `input_cache_read/creation` → cache.read/creation_input_tokens
/// - duration：`_end_ts - _start_ts`（秒级浮点）
pub fn build_llm_spans(
    records: &[HookRecord],
    trace_id: &str,
    parent_span_id: &str,
    session_id: &str,
    turn_number: usize,
    log_cfg: &ContentLogConfig,
    model_name: &str,
) -> Vec<OtelSpan> {
    let username = resolve_username();
    let mut spans: Vec<OtelSpan> = Vec::new();

    // Pre-pass: count PreToolUse events per LLM step (events appear after the step that triggered them).
    let mut step_tool_counts: std::collections::HashMap<i64, usize> = std::collections::HashMap::new();
    let mut cur_step: i64 = -1;
    for rec in records {
        let ev = rec.payload.get("hook_event_name").and_then(|v| v.as_str()).unwrap_or("");
        match ev {
            "LLMStep" => {
                cur_step = rec.payload.get("step_n").and_then(|v| v.as_i64()).unwrap_or(cur_step + 1);
                step_tool_counts.entry(cur_step).or_insert(0);
            }
            "PreToolUse" if cur_step >= 0 => {
                *step_tool_counts.entry(cur_step).or_insert(0) += 1;
            }
            _ => {}
        }
    }

    let mut response_index: i64 = 0;

    for rec in records {
        let event = rec
            .payload
            .get("hook_event_name")
            .and_then(|v| v.as_str())
            .unwrap_or("");
        if event != "LLMStep" {
            continue;
        }
        let p = &rec.payload;
        let step_n = p.get("step_n").and_then(|v| v.as_i64()).unwrap_or(0);
        let start_ts = p.get("_start_ts").and_then(|v| v.as_f64());
        let end_ts = p.get("_end_ts").and_then(|v| v.as_f64());
        let start_ns = start_ts
            .map(secs_to_nanos_str)
            .unwrap_or_else(|| rec.recorded_at_ns.clone());
        let end_ns = end_ts
            .map(secs_to_nanos_str)
            .unwrap_or_else(|| rec.recorded_at_ns.clone());

        let thinking = p.get("thinking").and_then(|v| v.as_str()).unwrap_or("");
        let output_text = p.get("output_text").and_then(|v| v.as_str()).unwrap_or("");
        let message_id = p.get("message_id").and_then(|v| v.as_str()).unwrap_or("");

        let out_json = llm_output_messages(p);

        // input messages：优先从 context.jsonl 注入的 `_input_messages_json` 读取，
        // 退化为空数组（无 context.jsonl 时）。
        let in_json = p
            .get("_input_messages_json")
            .and_then(|v| v.as_str())
            .filter(|s| !s.is_empty())
            .unwrap_or("[]")
            .to_string();

        // token usage 提取
        let tu = p.get("token_usage");
        let input_tokens = tu
            .and_then(|t| t.get("input_other"))
            .and_then(|v| v.as_i64())
            .unwrap_or(0);
        let output_tokens = tu
            .and_then(|t| t.get("output"))
            .and_then(|v| v.as_i64())
            .unwrap_or(0);
        let cache_read = tu
            .and_then(|t| t.get("input_cache_read"))
            .and_then(|v| v.as_i64())
            .unwrap_or(0);
        let cache_creation = tu
            .and_then(|t| t.get("input_cache_creation"))
            .and_then(|v| v.as_i64())
            .unwrap_or(0);

        let code_wire = p["source"] == "kimi-code";
        let input_known = tu.and_then(|t| t.get("input_other")).and_then(Value::as_i64).is_some();
        let output_known = tu.and_then(|t| t.get("output")).and_then(Value::as_i64).is_some();
        let input_tokens = input_tokens + if code_wire { cache_read + cache_creation } else { 0 };

        let mut attrs: Vec<OtelAttribute> = Vec::with_capacity(16);
        let native_model = p.get("model").and_then(Value::as_str).filter(|s| !s.is_empty())
            .unwrap_or(if code_wire { "unknown" } else { model_name });
        let effective_model = if native_model.is_empty() { "unknown" } else { native_model };
        let native_error = match p["finish_reason"].as_str() {
            Some("error") => Some("provider_error"), Some("interrupted") => Some("cancelled"), _ => None,
        };
        let provider = model_call_system(p, effective_model);

        attrs.push(OtelAttribute::string("gen_ai.span.kind", "LLM"));
        attrs.push(OtelAttribute::string("gen_ai.operation.name", "chat"));
        attrs.push(OtelAttribute::string("gen_ai.system", provider));
        attrs.push(OtelAttribute::string("gen_ai.request.model", effective_model));
        attrs.push(OtelAttribute::string("gen_ai.response.model", "unknown"));
        attrs.push(OtelAttribute::string("gen_ai.provider.name", model_call_provider(p, effective_model, provider)));
        attrs.push(OtelAttribute::string("ant.username", &username));
        attrs.push(OtelAttribute::string("user.id", &username));
        // gen_ai.input/output.messages — always present, [REDACTED] when off
        attrs.push(OtelAttribute::string(
            "gen_ai.input.messages",
            if log_cfg.spans_user_prompts { &in_json } else { REDACTED },
        ));
        attrs.push(OtelAttribute::string(
            "gen_ai.output.messages",
            if log_cfg.spans_model_outputs { &out_json } else { REDACTED },
        ));
        attrs.push(OtelAttribute::string("gen_ai.system_instructions", if log_cfg.spans_user_prompts {
            p["_system_instructions_json"].as_str().filter(|s| !s.is_empty()).unwrap_or("\"[UNAVAILABLE]\"")
        } else { REDACTED }));
        // finish_reasons：从 output 内容推断
        let finish_reason = if let Some(reason) = p["finish_reason"].as_str().filter(|r| !r.is_empty()) {
            reason
        } else if !output_text.is_empty() || !thinking.is_empty() {
            "stop"
        } else {
            "unknown"
        };
        attrs.push(OtelAttribute::string(
            "gen_ai.response.finish_reasons",
            &format!("[\"{}\"]", finish_reason),
        ));
        if !code_wire || input_known { attrs.push(OtelAttribute::int(
            "gen_ai.usage.input_tokens",
            input_tokens,
        )); }
        if !code_wire || output_known { attrs.push(OtelAttribute::int(
            "gen_ai.usage.output_tokens",
            output_tokens,
        )); }
        if cache_read > 0 || (code_wire && p["_provider"] != "openai" && tu.and_then(|t| t.get("input_cache_read")).and_then(Value::as_i64).is_some()) {
            attrs.push(OtelAttribute::int(
                "gen_ai.usage.cache_read.input_tokens",
                cache_read,
            ));
        }
        if cache_creation > 0 {
            attrs.push(OtelAttribute::int(
                "gen_ai.usage.cache_creation.input_tokens",
                cache_creation,
            ));
        }
        if !message_id.is_empty() {
            attrs.push(OtelAttribute::string("gen_ai.response.id", message_id));
        }
        attrs.push(OtelAttribute::int("agentic.llm.step", step_n));
        // duration_ms
        let duration_ms = p.get("_duration_ms").and_then(Value::as_i64).unwrap_or_else(|| match (start_ts, end_ts) {
            (Some(a), Some(b)) => ((b - a).max(0.0) * 1000.0) as i64,
            _ => 0,
        });
        attrs.push(OtelAttribute::int(
            "agentic.llm.duration_ms",
            duration_ms,
        ));

        // — 新增属性（semconv gaps） —
        if !session_id.is_empty() {
            attrs.push(OtelAttribute::string("gen_ai.conversation.id", session_id));
        }
        if !code_wire || (input_known && output_known) {
            attrs.push(OtelAttribute::int("gen_ai.usage.total_tokens", input_tokens + output_tokens));
        }
        if code_wire {
            for (known, name) in [(input_known, "input_tokens"), (output_known, "output_tokens")] {
                if !known { attrs.push(OtelAttribute::string(&format!("agentic.usage.{}.missing", name), "true")); }
            }
        }
        if code_wire && input_known {
            attrs.push(OtelAttribute::int("agentic.usage.fresh_input_tokens", tu.and_then(|t| t.get("input_other")).and_then(Value::as_i64).unwrap()));
        }
        if code_wire {
            if let Some(count) = tu.and_then(|t| t.get("input_cache_read")).and_then(Value::as_i64).filter(|count| *count > 0 || p["_provider"] != "openai") { attrs.push(OtelAttribute::int("agentic.usage.cache_read_tokens", count)); }
            if p["_provider"] != "openai" {
                if let Some(count) = tu.and_then(|t| t.get("input_cache_creation")).and_then(Value::as_i64) { attrs.push(OtelAttribute::int("agentic.usage.cache_write_tokens", count)); }
            }
        }
        append_code_usage_boundaries(&mut attrs, p);
        if let Some(error) = native_error { attrs.push(OtelAttribute::string("error.type", error)); }
        if let Some(id) = p["request_id"].as_str().filter(|s| !s.is_empty()) {
            attrs.push(OtelAttribute::string("agentic.model_call.id", id));
        }
        attrs.push(OtelAttribute::int(
            "agentic.turn.index",
            (turn_number as i64) - 1,
        ));
        attrs.push(OtelAttribute::int(
            "agentic.response.index",
            response_index,
        ));
        let tool_count = p["tool_calls"].as_array().map(|calls| calls.len() as i64).unwrap_or_else(|| *step_tool_counts.get(&step_n).unwrap_or(&0) as i64);
        attrs.push(OtelAttribute::int("agentic.tool.count", tool_count));
        response_index += 1;

        spans.push(OtelSpan {
            trace_id: trace_id.to_string(),
            span_id: otel_span_id(),
            parent_span_id: parent_span_id.to_string(),
            name: format!("chat {}", effective_model),
            kind: SPAN_KIND_INTERNAL,
            start_time_unix_nano: start_ns,
            end_time_unix_nano: end_ns,
            attributes: attrs,
            status: native_error.map(OtelStatus::error).unwrap_or_else(OtelStatus::ok),
        });
    }

    spans
}

/// 构造 D4 API error/retry CLIENT spans。
///
/// 从合成的 `ApiRetry` 事件（由 StepRetry wire 事件生成）构造 CLIENT-kind span，
/// 表示一次失败的 LLM 调用尝试。每个 retry span 作为 turn root 的 sibling。
pub fn build_api_error_spans(
    records: &[HookRecord],
    trace_id: &str,
    parent_span_id: &str,
) -> Vec<OtelSpan> {
    let username = resolve_username();
    let mut spans: Vec<OtelSpan> = Vec::new();

    for rec in records {
        let event = rec
            .payload
            .get("hook_event_name")
            .and_then(|v| v.as_str())
            .unwrap_or("");
        if event != "ApiRetry" {
            continue;
        }
        let p = &rec.payload;
        let error_type = p.get("error_type").and_then(|v| v.as_str()).unwrap_or("unknown");
        let status_code = p.get("status_code").and_then(|v| v.as_i64()).unwrap_or(0);
        let attempt = p.get("attempt").and_then(|v| v.as_i64()).unwrap_or(1);
        let max_attempts = p.get("max_attempts").and_then(|v| v.as_i64()).unwrap_or(3);
        let wait_s = p.get("wait_s").and_then(|v| v.as_f64()).unwrap_or(0.0);

        let ts = p.get("_source_timestamp").and_then(|v| v.as_f64()).unwrap_or(0.0);
        let start_ns = secs_to_nanos_str(ts);
        let end_ns = secs_to_nanos_str(ts + wait_s);

        let attributes = vec![
            OtelAttribute::string("gen_ai.span.kind", "CLIENT"),
            OtelAttribute::string("gen_ai.operation.name", "chat"),
            OtelAttribute::string("error.type", error_type),
            OtelAttribute::int("agentic.llm.error.status_code", status_code),
            OtelAttribute::int("agentic.llm.retry.attempt", attempt),
            OtelAttribute::int("agentic.llm.retry.max", max_attempts),
            OtelAttribute::int("agentic.llm.retry.delay_ms", (wait_s * 1000.0) as i64),
            OtelAttribute::string("ant.username", &username),
            OtelAttribute::string("user.id", &username),
        ];

        spans.push(OtelSpan {
            trace_id: trace_id.to_string(),
            span_id: otel_span_id(),
            parent_span_id: parent_span_id.to_string(),
            name: format!("chat api_error"),
            kind: SPAN_KIND_CLIENT,
            start_time_unix_nano: start_ns,
            end_time_unix_nano: end_ns,
            attributes,
            status: OtelStatus::error(format!("{} ({})", error_type, status_code)),
        });
    }

    spans
}

/// 构造 C8 compaction span。
///
/// 当前 wire.jsonl 实测样本未观察到原生 compaction 事件；本函数解析 `Compaction`
/// hook 事件（schema 见 stop.rs 文档），输出 `tool.name=compaction` 的 span。
/// 找不到 Compaction 事件时返回空数组（不产生噪声）。
pub fn build_compaction_spans(
    records: &[HookRecord],
    trace_id: &str,
    parent_span_id: &str,
) -> Vec<OtelSpan> {
    let username = resolve_username();
    let mut spans: Vec<OtelSpan> = Vec::new();

    for rec in records {
        let event = rec
            .payload
            .get("hook_event_name")
            .and_then(|v| v.as_str())
            .unwrap_or("");
        if event != "Compaction" {
            continue;
        }
        let p = &rec.payload;
        let start_ts = p.get("_start_ts").and_then(|v| v.as_f64());
        let end_ts = p.get("_end_ts").and_then(|v| v.as_f64());
        let start_ns = start_ts
            .map(secs_to_nanos_str)
            .unwrap_or_else(|| rec.recorded_at_ns.clone());
        let end_ns = end_ts
            .map(secs_to_nanos_str)
            .unwrap_or_else(|| rec.recorded_at_ns.clone());

        let tokens_before = p.get("tokens_before").and_then(|v| v.as_i64()).unwrap_or(0);
        let tokens_after = p.get("tokens_after").and_then(|v| v.as_i64()).unwrap_or(0);
        let tokens_saved = (tokens_before - tokens_after).max(0);
        let trigger = p.get("trigger").and_then(|v| v.as_str()).unwrap_or("auto");

        let mut attrs: Vec<OtelAttribute> = Vec::with_capacity(12);
        attrs.push(OtelAttribute::string("gen_ai.span.kind", "TOOL"));
        attrs.push(OtelAttribute::string(
            "gen_ai.operation.name",
            "execute_tool",
        ));
        attrs.push(OtelAttribute::string("gen_ai.tool.name", "compaction"));
        attrs.push(OtelAttribute::string("agentic.tool.type", "builtin"));
        attrs.push(OtelAttribute::string("ant.username", &username));
        attrs.push(OtelAttribute::string("user.id", &username));
        attrs.push(OtelAttribute::int(
            "agentic.compaction.tokens.before",
            tokens_before,
        ));
        attrs.push(OtelAttribute::int(
            "agentic.compaction.tokens.after",
            tokens_after,
        ));
        attrs.push(OtelAttribute::int(
            "agentic.compaction.tokens.saved",
            tokens_saved,
        ));
        attrs.push(OtelAttribute::string("agentic.compaction.trigger", trigger));
        // Required tool attrs for weaver compliance
        attrs.push(OtelAttribute::string("gen_ai.tool.call.arguments", "{}"));
        attrs.push(OtelAttribute::string("gen_ai.tool.call.result", ""));
        let start_i: i128 = start_ns.parse().unwrap_or(0);
        let end_i: i128 = end_ns.parse().unwrap_or(start_i);
        let duration_ms = (end_i - start_i).max(0) / 1_000_000;
        attrs.push(OtelAttribute::int("agentic.tool.duration_ms", duration_ms as i64));

        spans.push(OtelSpan {
            trace_id: trace_id.to_string(),
            span_id: otel_span_id(),
            parent_span_id: parent_span_id.to_string(),
            name: "compaction".to_string(),
            kind: SPAN_KIND_INTERNAL,
            start_time_unix_nano: start_ns,
            end_time_unix_nano: end_ns,
            attributes: attrs,
            status: OtelStatus::ok(),
        });
    }

    spans
}

/// 构建 C8 `compaction_result` 日志事件。
///
/// 扫描 `hook_event_name == "Compaction"` 的记录，输出 `compaction_result` LogRecord。
/// 属性对齐 `docs/semconv/event.txt` 的 compaction_result 定义：
///   - `gen_ai.tool.name = "compaction"`
///   - `agentic.tool.type = "system"`
///   - `agentic.compaction.tokens_before / tokens_after / tokens_saved`
///   - `agentic.compaction.trigger`
///   - `duration_ms`
///   - `agentic.turn.number`
///   - error path: `error.type`（当 tokens_after >= tokens_before 时）
pub fn build_compaction_events(
    records: &[HookRecord],
    session_id: &str,
    ctx: &EmissionContext,
) -> Vec<OtelLogRecord> {
    let username = resolve_username();
    let mut events: Vec<OtelLogRecord> = Vec::new();
    let mut turn_number: usize = 0;

    for rec in records {
        let event = rec
            .payload
            .get("hook_event_name")
            .and_then(|v| v.as_str())
            .unwrap_or("");

        if event == "TurnBegin" {
            turn_number += 1;
        }

        if event != "Compaction" {
            continue;
        }

        let p = &rec.payload;
        let start_ts = p.get("_start_ts").and_then(|v| v.as_f64());
        let end_ts = p.get("_end_ts").and_then(|v| v.as_f64());
        let end_ns = end_ts
            .map(secs_to_nanos_str)
            .unwrap_or_else(|| rec.recorded_at_ns.clone());
        let duration_ms = match (start_ts, end_ts) {
            (Some(a), Some(b)) => ((b - a).max(0.0) * 1000.0) as i64,
            _ => 0,
        };

        let tokens_before = p.get("tokens_before").and_then(|v| v.as_i64()).unwrap_or(0);
        let tokens_after = p.get("tokens_after").and_then(|v| v.as_i64()).unwrap_or(0);
        let tokens_saved = p.get("tokens_saved").and_then(|v| v.as_i64()).unwrap_or(0);
        let trigger = p.get("trigger").and_then(|v| v.as_str()).unwrap_or("auto");

        // 是否压缩失败（tokens_after >= tokens_before 表示实际没有减少）
        let is_error = tokens_after > 0 && tokens_after >= tokens_before;

        let mut attrs: Vec<OtelAttribute> = Vec::with_capacity(12);
        attrs.push(OtelAttribute::string("event.name", "compaction_result"));
        attrs.push(OtelAttribute::string("gen_ai.operation.name", "execute_tool"));
        attrs.push(OtelAttribute::string("gen_ai.tool.name", "compaction"));
        attrs.push(OtelAttribute::string("agentic.tool.type", "system"));
        attrs.push(OtelAttribute::int("agentic.turn.number", turn_number as i64));
        attrs.push(OtelAttribute::string(
            "gen_ai.conversation.id",
            session_id,
        ));
        attrs.push(OtelAttribute::int(
            "agentic.compaction.tokens_before",
            tokens_before,
        ));
        attrs.push(OtelAttribute::int(
            "agentic.compaction.tokens_after",
            tokens_after,
        ));
        attrs.push(OtelAttribute::int(
            "agentic.compaction.tokens_saved",
            tokens_saved,
        ));
        attrs.push(OtelAttribute::string("agentic.compaction.trigger", trigger));
        if duration_ms > 0 {
            attrs.push(OtelAttribute::int("duration_ms", duration_ms));
        }
        attrs.push(OtelAttribute::string("ant.username", &username));
        attrs.push(OtelAttribute::string("ant.agent.type", ctx.agent_type));
        attrs.push(OtelAttribute::string("ant.agent.product", ctx.agent_product));

        if is_error {
            attrs.push(OtelAttribute::string(
                "error.type",
                "compaction_no_reduction",
            ));
        }

        events.push(OtelLogRecord::event("compaction_result", &end_ns, attrs));
    }

    events
}

/// 构造 C6 subagent span 及其子 LLM/tool spans。
///
/// 步骤：
/// 1. 按 `parent_tool_call_id` 把所有 `SubagentEvent` 分组（一个 parent 一个 subagent）
/// 2. 对每组，把 `nested_events` 全部 flatten 成 `Vec<HookRecord>`
/// 3. 在 flatten 后的列表里递归调用 build_llm_spans + build_tool_spans，
///    parent = 本 subagent 的 span_id
/// 4. subagent span 自身直接挂在 turn root 下；若能找到对应 Agent tool span，
///    使用 Agent tool span 的起止时间作为 subagent 边界。
pub fn build_subagent_spans(
    records: &[HookRecord],
    trace_id: &str,
    fallback_parent_span_id: &str,
    subagent_tool_bounds: &HashMap<String, (String, String)>,
    session_id: &str,
    turn_number: usize,
    log_cfg: &ContentLogConfig,
    model_name: &str,
) -> Vec<OtelSpan> {
    use crate::common::otel::HookRecord as HR;
    let username = resolve_username();

    // 按 parent_tool_call_id 分组
    let mut groups: std::collections::BTreeMap<String, SubagentGroup> =
        std::collections::BTreeMap::new();
    for rec in records {
        let event = rec
            .payload
            .get("hook_event_name")
            .and_then(|v| v.as_str())
            .unwrap_or("");
        if event != "SubagentEvent" {
            continue;
        }
        let parent_id = rec
            .payload
            .get("parent_tool_call_id")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        if parent_id.is_empty() {
            continue;
        }
        let agent_id = rec
            .payload
            .get("agent_id")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        let subagent_type = rec
            .payload
            .get("subagent_type")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        // 优先使用 _first_ts/_last_ts（合并后的 SubagentEvent 记录了完整时间范围），
        // 退化到 _source_timestamp（向后兼容单条 SubagentEvent 的旧格式）。
        let first_ts = rec
            .payload
            .get("_first_ts")
            .and_then(|v| v.as_f64())
            .or_else(|| {
                rec.payload
                    .get("_source_timestamp")
                    .and_then(|v| v.as_f64())
            })
            .unwrap_or(0.0);
        let last_ts = rec
            .payload
            .get("_last_ts")
            .and_then(|v| v.as_f64())
            .or_else(|| {
                rec.payload
                    .get("_source_timestamp")
                    .and_then(|v| v.as_f64())
            })
            .unwrap_or(0.0);

        let group = groups
            .entry(parent_id.clone())
            .or_insert_with(|| SubagentGroup {
                agent_id: agent_id.clone(),
                subagent_type: subagent_type.clone(),
                first_ts,
                last_ts,
                nested: Vec::new(),
            });
        if first_ts < group.first_ts {
            group.first_ts = first_ts;
        }
        if last_ts > group.last_ts {
            group.last_ts = last_ts;
        }
        // 把单条 SubagentEvent 的 nested_events 拼到组里
        if let Some(arr) = rec.payload.get("nested_events").and_then(|v| v.as_array()) {
            for inner in arr {
                group.nested.push(HR {
                    recorded_at_ns: event_recorded_at_ns(inner)
                        .unwrap_or_else(|| rec.recorded_at_ns.clone()),
                    payload: inner.clone(),
                });
            }
        }
    }

    let mut all_spans: Vec<OtelSpan> = Vec::new();

    for (parent_call_id, g) in groups {
        let sub_span_id = otel_span_id();

        let mut attrs: Vec<OtelAttribute> = Vec::with_capacity(16);
        attrs.push(OtelAttribute::string("gen_ai.span.kind", "CHAT"));
        attrs.push(OtelAttribute::string(
            "gen_ai.operation.name",
            "invoke_agent",
        ));
        attrs.push(OtelAttribute::string("ant.username", &username));
        attrs.push(OtelAttribute::string("user.id", &username));
        attrs.push(OtelAttribute::string("agentic.tool.type", "subagent"));
        attrs.push(OtelAttribute::string("agentic.subagent.id", &g.agent_id));
        attrs.push(OtelAttribute::string(
            "agentic.subagent.type",
            &g.subagent_type,
        ));
        attrs.push(OtelAttribute::string(
            "agentic.subagent.parent_tool_call_id",
            &parent_call_id,
        ));
        // C1 必需属性：session_id / runtime / turn_number
        if !session_id.is_empty() {
            attrs.push(OtelAttribute::string("gen_ai.conversation.id", session_id));
            attrs.push(OtelAttribute::string("gen_ai.chat.id", session_id));
        }
        attrs.push(OtelAttribute::string("agentic.runtime.name", "kimi-cli"));
        attrs.push(OtelAttribute::int(
            "agentic.turn.number",
            turn_number as i64,
        ));

        let (start_ns, end_ns) = subagent_tool_bounds
            .get(&parent_call_id)
            .cloned()
            .unwrap_or_else(|| (secs_to_nanos_str(g.first_ts), secs_to_nanos_str(g.last_ts)));

        let sub_root = OtelSpan {
            trace_id: trace_id.to_string(),
            span_id: sub_span_id.clone(),
            parent_span_id: fallback_parent_span_id.to_string(),
            name: format!("subagent {}", g.subagent_type),
            kind: SPAN_KIND_INTERNAL,
            start_time_unix_nano: start_ns,
            end_time_unix_nano: end_ns,
            attributes: attrs,
            status: OtelStatus::ok(),
        };
        all_spans.push(sub_root);

        // 子 LLM spans + 子 tool spans，挂在本 subagent 下，并按真实开始时间排序。
        let mut child_spans = build_llm_spans(&g.nested, trace_id, &sub_span_id, session_id, turn_number, log_cfg, model_name);
        child_spans.extend(build_tool_spans(&g.nested, trace_id, &sub_span_id, session_id, log_cfg));
        child_spans.sort_by(|a, b| {
            span_start_ns(a)
                .cmp(&span_start_ns(b))
                .then_with(|| a.name.cmp(&b.name))
        });
        all_spans.extend(child_spans);
    }

    all_spans
}

struct SubagentGroup {
    agent_id: String,
    subagent_type: String,
    first_ts: f64,
    last_ts: f64,
    nested: Vec<HookRecord>,
}

/// 把 wire.jsonl 中的秒级浮点时间戳转成纳秒字符串。
///
/// 例：`1777504291.092` → `"1777504291092000000"`。
fn secs_to_nanos_str(secs: f64) -> String {
    if secs <= 0.0 {
        return now_nanos();
    }
    let ns = (secs * 1_000_000_000.0) as i128;
    ns.to_string()
}

fn event_recorded_at_ns(payload: &Value) -> Option<String> {
    payload
        .get("recorded_at_ns")
        .and_then(|x| x.as_str())
        .filter(|s| *s != "0")
        .map(|s| s.to_string())
        .or_else(|| {
            payload
                .get("_source_timestamp")
                .and_then(|x| x.as_f64())
                .filter(|ts| *ts > 0.0)
                .map(secs_to_nanos_str)
        })
}

fn span_start_ns(span: &OtelSpan) -> u128 {
    span.start_time_unix_nano.parse().unwrap_or(0)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn rec(ns: &str, payload: Value) -> HookRecord {
        HookRecord {
            recorded_at_ns: ns.to_string(),
            payload,
        }
    }

    #[test]
    fn generation_output_keeps_media_only_content() {
        let payload = serde_json::json!({"output_content":[{"type":"audio","mime_type":"audio/wav","data":"YWJj"}]});
        let messages: Value = serde_json::from_str(&llm_output_messages(&payload)).unwrap();
        assert_eq!(messages[0]["content"][0]["source"]["media_type"], "audio/wav");
    }

    #[test]
    fn pairs_pre_and_post_into_one_success_span() {
        let records = vec![
            rec(
                "1000000",
                json!({
                    "hook_event_name": "PreToolUse",
                    "session_id": "s",
                    "cwd": "/x",
                    "tool_name": "shell",
                    "tool_input": {"cmd": "echo hi"},
                    "tool_call_id": "c1"
                }),
            ),
            rec(
                "5000000",
                json!({
                    "hook_event_name": "PostToolUse",
                    "session_id": "s",
                    "cwd": "/x",
                    "tool_name": "shell",
                    "tool_input": {"cmd": "echo hi"},
                    "tool_output": "hi",
                    "tool_call_id": "c1"
                }),
            ),
        ];
        let trace = "abcdef";
        let log_cfg = ContentLogConfig { spans_tool_details: true, spans_tool_outputs: true, ..Default::default() };
        let spans = build_tool_spans(&records, trace, "", "", &log_cfg);
        assert_eq!(spans.len(), 1);
        let s = &spans[0];
        assert_eq!(s.trace_id, trace);
        assert_eq!(s.start_time_unix_nano, "1000000");
        assert_eq!(s.end_time_unix_nano, "5000000");
        // OTel status code: 1 = OK, 2 = ERROR
        assert_eq!(s.status.code, 1);
        assert!(s.attributes.iter().any(|a| a.key == "gen_ai.tool.call.id"));
        assert!(s
            .attributes
            .iter()
            .any(|a| a.key == "gen_ai.tool.call.arguments"));
        assert!(s
            .attributes
            .iter()
            .any(|a| a.key == "gen_ai.tool.call.result"));
        assert!(s.attributes.iter().any(|a| a.key == "gen_ai.span.kind"));
        assert!(s.attributes.iter().any(|a| a.key == "ant.username"));
        assert!(s.attributes.iter().any(|a| a.key == "user.id"));
    }

    #[test]
    fn pairs_pre_and_failure_into_error_span() {
        let records = vec![
            rec(
                "1000000",
                json!({
                    "hook_event_name": "PreToolUse",
                    "tool_name": "shell",
                    "tool_input": {"cmd": "boom"},
                    "tool_call_id": "c-err"
                }),
            ),
            rec(
                "2000000",
                json!({
                    "hook_event_name": "PostToolUseFailure",
                    "tool_name": "shell",
                    "tool_input": {"cmd": "boom"},
                    "error": "command failed",
                    "tool_call_id": "c-err"
                }),
            ),
        ];
        let log_cfg = ContentLogConfig { spans_tool_details: true, spans_tool_outputs: true, ..Default::default() };
        let spans = build_tool_spans(&records, "t", "", "", &log_cfg);
        assert_eq!(spans.len(), 1);
        assert_eq!(spans[0].status.code, 2);
        assert_eq!(spans[0].status.message.as_deref(), Some("command failed"));
    }

    #[test]
    fn unpaired_pre_becomes_terminated_span() {
        let records = vec![rec(
            "1000000",
            json!({
                "hook_event_name": "PreToolUse",
                "tool_name": "shell",
                "tool_input": {"cmd": "x"},
                "tool_call_id": "c-orphan"
            }),
        )];
        let log_cfg = ContentLogConfig { spans_tool_details: true, spans_tool_outputs: true, ..Default::default() };
        let spans = build_tool_spans(&records, "t", "", "", &log_cfg);
        assert_eq!(spans.len(), 1);
        assert_eq!(spans[0].status.code, 2);
        assert_eq!(spans[0].start_time_unix_nano, spans[0].end_time_unix_nano);
        assert!(spans[0]
            .attributes
            .iter()
            .any(|a| a.key == "agentic.tool.terminated"));
    }

    #[test]
    fn mcp_tool_classified_correctly() {
        let records = vec![
            rec(
                "1000000",
                json!({
                    "hook_event_name": "PreToolUse",
                    "tool_name": "mcp__code_search__list_pull_requests",
                    "tool_input": {},
                    "tool_call_id": "c-mcp"
                }),
            ),
            rec(
                "2000000",
                json!({
                    "hook_event_name": "PostToolUse",
                    "tool_name": "mcp__code_search__list_pull_requests",
                    "tool_input": {},
                    "tool_output": "[]",
                    "tool_call_id": "c-mcp"
                }),
            ),
        ];
        let log_cfg = ContentLogConfig { spans_tool_details: true, spans_tool_outputs: true, ..Default::default() };
        let spans = build_tool_spans(&records, "t", "", "", &log_cfg);
        assert_eq!(spans.len(), 1);
        let kind_attr = spans[0]
            .attributes
            .iter()
            .find(|a| a.key == "agentic.tool.type")
            .expect("agentic.tool.type missing");
        match &kind_attr.value {
            agentic_otel_common::otlp::OtelAnyValue::String { string_value } => {
                assert_eq!(string_value, "mcp");
            }
            _ => panic!("expected string value"),
        }
    }

    #[test]
    fn subagent_folds_agent_tool_and_orders_children_by_time() {
        let records = vec![
            rec(
                "1000000000",
                json!({
                    "hook_event_name": "TurnBegin",
                    "user_input": "run subagent",
                    "_source_timestamp": 1.0
                }),
            ),
            rec(
                "2000000000",
                json!({
                    "hook_event_name": "PreToolUse",
                    "tool_name": "Agent",
                    "tool_input": {"prompt": "run"},
                    "tool_call_id": "agent-call",
                    "_source_timestamp": 2.0
                }),
            ),
            rec(
                "5000000000",
                json!({
                    "hook_event_name": "PostToolUse",
                    "tool_name": "Agent",
                    "tool_call_id": "agent-call",
                    "tool_output": "done",
                    "_source_timestamp": 5.0
                }),
            ),
            rec(
                "5100000000",
                json!({
                    "hook_event_name": "SubagentEvent",
                    "parent_tool_call_id": "agent-call",
                    "agent_id": "sub-1",
                    "subagent_type": "coder",
                    "_first_ts": 2.1,
                    "_last_ts": 4.5,
                    "_source_timestamp": 4.5,
                    "nested_events": [
                        {
                            "hook_event_name": "LLMStep",
                            "step_n": 1,
                            "output_text": "calling tool",
                            "token_usage": {},
                            "_start_ts": 2.1,
                            "_end_ts": 3.0,
                            "_source_timestamp": 3.0
                        },
                        {
                            "hook_event_name": "PreToolUse",
                            "tool_name": "Shell",
                            "tool_input": {"cmd": "uname"},
                            "tool_call_id": "sub-tool",
                            "_source_timestamp": 3.1
                        },
                        {
                            "hook_event_name": "PostToolUse",
                            "tool_name": "Shell",
                            "tool_call_id": "sub-tool",
                            "tool_output": "Darwin",
                            "_source_timestamp": 4.0
                        },
                        {
                            "hook_event_name": "LLMStep",
                            "step_n": 2,
                            "output_text": "done",
                            "token_usage": {},
                            "_start_ts": 4.1,
                            "_end_ts": 4.5,
                            "_source_timestamp": 4.5
                        }
                    ]
                }),
            ),
            rec(
                "6000000000",
                json!({
                    "hook_event_name": "TurnEnd",
                    "_source_timestamp": 6.0
                }),
            ),
        ];

        let log_cfg = ContentLogConfig { spans_tool_details: true, spans_tool_outputs: true, spans_user_prompts: true, spans_model_outputs: true, ..Default::default() };
        let turn = build_turn_spans_numbered(&records, "trace", 1, "session", &log_cfg, "").unwrap();
        assert!(!turn.spans.iter().any(|s| s.name == "subagent Agent"));

        let subagent = turn
            .spans
            .iter()
            .find(|s| s.name == "subagent coder")
            .expect("subagent span missing");
        assert_eq!(subagent.parent_span_id, turn.root_span_id);
        assert_eq!(subagent.start_time_unix_nano, "2000000000");
        assert_eq!(subagent.end_time_unix_nano, "5000000000");

        let child_names: Vec<&str> = turn
            .spans
            .iter()
            .filter(|s| s.parent_span_id == subagent.span_id)
            .map(|s| s.name.as_str())
            .collect();
        assert_eq!(
            child_names,
            vec!["chat unknown", "tool Shell", "chat unknown"]
        );

        let child_tool = turn
            .spans
            .iter()
            .find(|s| s.parent_span_id == subagent.span_id && s.name == "tool Shell")
            .expect("subagent tool span missing");
        assert_eq!(child_tool.start_time_unix_nano, "3100000000");
        assert_eq!(child_tool.end_time_unix_nano, "4000000000");
    }
}
