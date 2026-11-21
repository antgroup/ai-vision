//! `Stop` handler — kimihooks 的 "一个 hook 顶 N 个" 模式。
//!
//! 设计动机
//! --------
//! 默认配置下用户需要在 `~/.kimi/config.toml` 注册 5+ 个 hook（SessionStart/End +
//! PreToolUse/PostToolUse/PostToolUseFailure 等）才能完整重建会话拓扑。这对终端用户的
//! 接入成本偏高，而且其它 codeisland-bridge 之类的 hook 同时存在时容易互相干扰。
//!
//! `Stop` hook 在 kimi-cli "一个 turn 处理完毕、即将把控制权交还用户" 时触发。
//! Kimi Code 异步追加 wire；普通 Stop 有界等待当前 step 与工具结果落盘，不等 turn.ended。
//! 本 handler 直接读 wire.jsonl 重建 hook_log，**只需注册一个 Stop hook 就能拿到
//! 完整 turn 拓扑**（C1 root + C2 LLM + C3 tool + C6 subagent + C8 compaction）。
//!
//! 与 Pre/Post hook 的关系
//! ----------------------
//! - 如果用户只注册了 Stop：本 handler 是唯一真相源
//! - 如果用户两套都注册了：Stop 重建出的合成记录会与实时 Pre/Post 记录在 hook_log 里**共存**，
//!   `build_tool_spans()` 用 `tool_call_id` 配对，重复 PreToolUse 会被同一份 PostToolUse 配对，
//!   失败记录会同时配上 Pre。当前暂不去重——优先保证有数据。
//!
//! wire.jsonl schema（来自 `kimi_cli/wire/types.py`，protocol_version=1.9）
//! ----------------------------------------------------------------------
//! 第一行：`{"type": "metadata", "protocol_version": "1.9"}`
//! 后续行：`{"timestamp": <float seconds>, "message": {"type": "<MsgType>", "payload": {...}}}`
//!
//! Sprint 2 已支持的 MsgType（实测 c100314b 会话 40 行覆盖全部）：
//! - `TurnBegin`     → 合成 `TurnBegin` hook 事件（C1 root span 开始边界）
//! - `TurnEnd`       → 合成 `TurnEnd` hook 事件（C1 root span 结束边界）
//! - `StepBegin`     → 触发 LLMStep accumulator 开启（n: 第 n 次 LLM 调用）
//! - `ContentPart`   → 累加到当前 step 的 `thinking`(type=think) / `output_text`(type=text)
//! - `StatusUpdate`  → 提供 `token_usage` + `message_id`，flush 当前 LLMStep
//! - `ToolCall`      → 合成 `PreToolUse`（C3 已支持）+ flush 当前未结的 LLMStep
//! - `ToolCallPart`  → 跳过（流式增量 arguments，ToolCall 已含完整版本）
//! - `ToolResult`    → 合成 `PostToolUse[Failure]`（C3 已支持）
//! - `SubagentEvent` → 合成 `SubagentEvent` hook 事件，`event` 字段递归用同一套 parse
//!                     处理成 `nested_events: [Value]`（C6 子 trace 的事件流）
//! - 其它 type       → 跳过 + 计数，不报错（前向兼容新版本 protocol）
//!
//! LLMStep 聚合规则
//! ----------------
//! ```text
//! StepBegin(n=1) ──┐
//!   ContentPart(think) → step.thinking += text
//!   ContentPart(text)  → step.output_text += text
//!   StatusUpdate       → step.token_usage = {...}; step.message_id = ...; flush
//! ```
//! 强制 flush 时机：下一个 StepBegin / TurnEnd / ToolCall（保证当 StatusUpdate 缺失时
//! 也能输出退化版本，避免 LLMStep 永远丢失）。

use std::fs;
use std::path::Path;

use serde_json::{json, Value};

use agentic_otel_common::{
    otlp::{wrap_logs_export, wrap_metrics_export, wrap_trace_export},
    state::StateLock,
    ExportContext,
};

use crate::common::edition::EditionConfig;
use crate::common::hook::{
    extract_cwd, extract_session_id, CommonContext, HandlerError, HookEvent, HookHandler,
};
use crate::common::http_export;
use crate::common::otel::{self as kimi_otel, HookRecord};
use crate::common::session_locator;
use crate::common::state::{KimihooksState, SessionState, DEFAULT_TTL_NS};

// 与 kimihooks-replay 完全对齐（保证两条链路上报的资源属性一致）。
const AGENT_PRODUCT: &str = "kimi-cli";
const RUNTIME_NAME: &str = "kimi-cli";
const SDK_NAME: &str = "kimihooks";
const VERSION: &str = env!("CARGO_PKG_VERSION");

pub struct StopHandler;

impl StopHandler {
    pub fn new() -> Self {
        Self
    }
}

impl Default for StopHandler {
    fn default() -> Self {
        Self::new()
    }
}

impl HookHandler for StopHandler {
    fn name(&self) -> &str {
        "stop"
    }

    fn supported_events(&self) -> &[HookEvent] {
        // Stop 与 StopFailure 都触发同样的 wire.jsonl 重建（StopFailure 时也已经写完盘）。
        const EVENTS: [HookEvent; 3] = [HookEvent::Stop, HookEvent::StopFailure, HookEvent::Interrupt];
        &EVENTS
    }

    fn handle(
        &self,
        event: &HookEvent,
        payload: &Value,
        ctx: &CommonContext,
    ) -> Result<(), HandlerError> {
        let session_id = extract_session_id(payload);
        let cwd = extract_cwd(payload);
        ctx.logger.info(&format!(
            "Stop event={:?} session={} cwd={}",
            event, session_id, cwd
        ));

        if session_id.is_empty() || cwd.is_empty() {
            ctx.logger
                .warn("Stop 缺少 session_id 或 cwd，无法定位 wire.jsonl，跳过重建");
            return Ok(());
        }

        let paths = session_locator::locate_for_client(&session_id, &cwd, payload["client_type"].as_str().unwrap_or(""));
        if !paths.wire_jsonl.exists() {
            ctx.logger.warn(&format!(
                "wire.jsonl 不存在: {}（kimi-cli 可能尚未刷盘，或 session 路径推断错误）",
                paths.wire_jsonl.display()
            ));
            return Ok(());
        }

        let body = match if *event == HookEvent::Stop && payload["client_type"] == "kimi_code_cli" {
            super::kimi_code::read_stop_snapshot(&paths.wire_jsonl, std::time::Duration::from_secs(1))
        } else {
            fs::read_to_string(&paths.wire_jsonl)
        } {
            Ok(body) => body,
            Err(e) => {
                ctx.logger.warn(&format!("解析 wire.jsonl 失败: {}", e));
                return Ok(());
            }
        };
        let mut synth = rebuild_hook_log_from_snapshot(&body, &paths.wire_jsonl, &session_id, &cwd);

        // 从 context.jsonl 注入 LLM input messages（C2/C9）
        if paths.context_jsonl.exists() {
            crate::common::context_parser::enrich_with_context(&mut synth, &paths.context_jsonl);
            ctx.logger.info(&format!(
                "context.jsonl enriched: {}",
                paths.context_jsonl.display()
            ));
        }

        // ── v0.3: 加锁读 kimihooks_state.json，合并实时 hook 缓存 ──
        // 一次性完成 (1) 读 state → (2) 合并实时事件 → (3) 上报判定 → (4) 写回新指纹。
        // 锁的释放发生在 `_lock` 离开作用域之时（本 handle() 函数末尾）。
        let _lock = StateLock::acquire(&ctx.state_file, 2000);
        let mut state = KimihooksState::load_or_default(&ctx.state_file);
        let prev_session = state
            .get_session(&session_id)
            .cloned()
            .unwrap_or_default();
        merge_with_realtime_state(&mut synth, &prev_session, &session_id, &cwd);
        synth.sort_by(|a, b| {
            source_timestamp(a)
                .partial_cmp(&source_timestamp(b))
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        ctx.logger.info(&format!(
            "Stop 合并实时缓存: user_prompts={} subagent_starts={} subagent_stops={}",
            prev_session.user_prompts.len(),
            prev_session.subagent_starts.len(),
            prev_session.subagent_stops.len(),
        ));

        ctx.logger.info(&format!(
            "Stop 重建完成 wire={} synth_records={}",
            paths.wire_jsonl.display(),
            synth.len()
        ));

        // ── Phase 1: 复制 session 全部文件到 KIMI_SESSION_DIR ──
        // 当 KIMI_SESSION_DIR 已设置时，把 wire.jsonl + context.jsonl + state.json +
        // subagents/ 全部复制到目标目录，确保离线 replay 和调试链路能拿到完整数据。
        // 支持目录模式和 .jsonl 文件模式。
        if agentic_otel_common::privacy::resolve_mode("KIMI_COLLECTION_MODE") == Ok("full") {
            snapshot_session_to_dir(&paths, &ctx.logger);
        }

        // ── Sprint 3: HTTP 上报到远端 OTLP collector（生产链路核心）──
        // 把刚刚 append 的 synth 记录转成 HookRecord，构造 traces/logs/metrics 三类信号，
        // 通过 http_export 发到 langfuse + 30.230.2.66。fail-open，永远不影响 hook 退出码。
        //
        // 幂等去重：kimi-cli 可能对同一 session 的同一 turn 触发多次 Stop 事件
        // （subagent 完成和主 turn 完成各触发一次）。每次 rebuild 会生成不同的随机
        // trace_id，导致 Langfuse 出现重复 trace。用 wire.jsonl 的文件大小作为指纹，
        // 如果两次触发之间 wire.jsonl 没有新增内容，跳过上报。
        // 幂等去重：v0.3 起用 state.sessions[sid].wire_fingerprint 替代旧 marker 文件。
        // 同一 session 的多次 Stop 触发（subagent + 主 turn）共享 fingerprint。
        let new_fingerprint = wire_fingerprint(&body);
        let prev_fingerprint = prev_session.wire_fingerprint.clone().unwrap_or_default();

        // subagent 完整性检查：如果已知有 subagent_starts 但 subagent_stops 还没跟上，
        // 说明 subagent 尚未全部完成，本次 Stop 是中间触发，跳过上报等待最终 Stop。
        // 这解决了 kimi-cli 在 subagent 执行中途多次触发 Stop 导致重复上报的问题。
        let starts_count = prev_session.subagent_starts.len();
        let stops_count = prev_session.subagent_stops.len();
        if starts_count > 0 && stops_count < starts_count {
            ctx.logger.info(&format!(
                "Stop 跳过：subagent 尚未全部完成 (starts={} stops={})，等待最终 Stop",
                starts_count, stops_count
            ));
            // 仍然更新 fingerprint，避免最终 Stop 时因指纹相同而被幂等过滤掉
            state.upsert_session(&session_id, |s| {
                s.wire_fingerprint = Some(new_fingerprint);
                s.last_seen_ns = agentic_otel_common::now_nanos();
            });
            state.prune_older_than(DEFAULT_TTL_NS);
            if let Err(e) = state.save(&ctx.state_file) {
                ctx.logger
                    .warn(&format!("Stop state.json save 失败: {}", e));
            }
        } else if new_fingerprint == prev_fingerprint && !new_fingerprint.is_empty() {
            ctx.logger.info(&format!(
                "Stop 幂等跳过：wire.jsonl 指纹未变 ({}), 跳过重复上报",
                new_fingerprint
            ));
        } else {
            let records = synth_to_hook_records(&synth);
            export_to_remote(&records, &session_id, &ctx.logger);
            // 写回新 fingerprint 到 state.json
            state.upsert_session(&session_id, |s| {
                s.wire_fingerprint = Some(new_fingerprint);
                s.last_seen_ns = agentic_otel_common::now_nanos();
            });
            state.prune_older_than(DEFAULT_TTL_NS);
            if let Err(e) = state.save(&ctx.state_file) {
                ctx.logger
                    .warn(&format!("Stop state.json save 失败: {}", e));
            }
        }

        Ok(())
    }
}

/// 把 state.json 中的实时 hook 缓存合成为 wire 等价 `hook_event_name` 注入 synth。
///
/// 输出事件均带 `_synthetic: true` 与 `_source: "kimihooks_state"`，下游
/// `build_*` 函数按 `hook_event_name` 路由消费，与 wire 重建的事件无差别。
pub(crate) fn merge_with_realtime_state(
    synth: &mut Vec<Value>,
    sess: &SessionState,
    session_id: &str,
    cwd: &str,
) {
    for entry in &sess.user_prompts {
        let cwd_field: &str = if entry.cwd.is_empty() { cwd } else { &entry.cwd };
        synth.push(json!({
            "hook_event_name": "UserPromptSubmit",
            "session_id": session_id,
            "cwd": cwd_field,
            "prompt": entry.prompt,
            "_synthetic": true,
            "_source": "kimihooks_state",
            "_source_timestamp": ns_to_secs(&entry.ts_ns),
        }));
    }
    for entry in &sess.subagent_starts {
        let cwd_field: &str = if entry.cwd.is_empty() { cwd } else { &entry.cwd };
        synth.push(json!({
            "hook_event_name": "SubagentStart",
            "session_id": session_id,
            "cwd": cwd_field,
            "agent_name": entry.agent_name,
            "prompt": entry.prompt,
            "_synthetic": true,
            "_source": "kimihooks_state",
            "_source_timestamp": ns_to_secs(&entry.ts_ns),
        }));
    }
    for entry in &sess.subagent_stops {
        let cwd_field: &str = if entry.cwd.is_empty() { cwd } else { &entry.cwd };
        synth.push(json!({
            "hook_event_name": "SubagentStop",
            "session_id": session_id,
            "cwd": cwd_field,
            "agent_name": entry.agent_name,
            "response": entry.response,
            "_synthetic": true,
            "_source": "kimihooks_state",
            "_source_timestamp": ns_to_secs(&entry.ts_ns),
        }));
    }
}

fn ns_to_secs(ns_str: &str) -> f64 {
    ns_str.parse::<u128>().unwrap_or(0) as f64 / 1_000_000_000.0
}

/// 把 `rebuild_hook_log_from_wire` 产出的 `Vec<Value>` 包装成 `HookRecord`。
///
/// 时间戳解析优先级：
/// 1. `recorded_at_ns`（纳秒字符串，hook_log 原生格式）
/// 2. `_source_timestamp`（秒浮点数，wire.jsonl 合成事件格式）→ 乘以 1e9 转纳秒
/// 3. 退化为 `"0"`
fn synth_to_hook_records(synth: &[Value]) -> Vec<HookRecord> {
    synth
        .iter()
        .map(|v| {
            let recorded_at_ns =
                if let Some(ns_str) = v.get("recorded_at_ns").and_then(|x| x.as_str()) {
                    if ns_str != "0" {
                        ns_str.to_string()
                    } else {
                        secs_to_nanos_string(v)
                    }
                } else {
                    secs_to_nanos_string(v)
                };
            HookRecord {
                recorded_at_ns,
                payload: v.clone(),
            }
        })
        .collect()
}

/// 从合成事件的 `_source_timestamp`（秒浮点数）转换为纳秒字符串。
/// wire.jsonl 的时间戳是秒单位（如 `1777540240.89`），OTel 需要纳秒。
fn secs_to_nanos_string(v: &Value) -> String {
    v.get("_source_timestamp")
        .and_then(|x| x.as_f64())
        .filter(|ts| *ts > 0.0)
        .map(|ts| format!("{}", (ts * 1_000_000_000.0) as u128))
        .unwrap_or_else(|| "0".to_string())
}

/// 把 hook_log 记录转成 OTLP 三类信号 + HTTP 上报。
///
/// 与 `kimihooks-replay` 完全对称（同一份 build_* 函数、同一份 ExportContext），
/// 保证 dev (replay) 与 prod (Stop hook) 两条链路上报的数据形状一致。
fn export_to_remote(
    records: &[HookRecord],
    session_id: &str,
    logger: &crate::common::logger::Logger,
) {
    if http_export::http_disabled() {
        logger.info("[http] export skipped: KIMI_DISABLE_HTTP=true");
        return;
    }
    let cfg = EditionConfig::resolve();
    logger.info(&format!("[http] {}", cfg.human_summary()));

    let agent_env = agentic_otel_common::env_or_default(
        "KIMIHOOKS_AGENT_ENV", "AIVISION_AGENT_ENV", "Default",
    );
    let agent_scene = agentic_otel_common::env_or_default(
        "KIMIHOOKS_AGENT_SCENE", "AIVISION_AGENT_SCENE", "",
    );
    let ctx = ExportContext {
        agent_product: cfg.agent_product.as_deref().filter(|s| !s.is_empty()).unwrap_or(AGENT_PRODUCT),
        agent_type: &cfg.agent_type,
        sdk_name: SDK_NAME,
        build_version: VERSION,
        agent_env: &agent_env,
        agent_scene: &agent_scene,
        collection_mode: agentic_otel_common::privacy::resolve_mode("KIMI_COLLECTION_MODE").unwrap_or("privacy"),
        emit_meta_trigger: false,
    };
    let log_cfg = kimi_otel::ContentLogConfig::from(&cfg);
    let emit_ctx = kimi_otel::EmissionContext {
        agent_product: cfg.agent_product.as_deref().filter(|s| !s.is_empty()).unwrap_or(AGENT_PRODUCT),
        agent_type: &cfg.agent_type,
        runtime_name: RUNTIME_NAME,
        log: &log_cfg,
    };

    // traces：多 turn 拆分——每个 turn 独立 trace。
    // 只上报最后一个 turn（当前 turn），避免历史 turn 被重复上报。
    // Stop hook 在每个 turn 结束时触发，此时 wire.jsonl 包含所有历史 turn，
    // 但前面的 turn 在前一次 Stop 触发时已经上报过了。
    let log_cfg = kimi_otel::ContentLogConfig::from(&cfg);
    let turn_result = kimi_otel::build_all_turns_with_session(records, session_id, &log_cfg, "unknown");
    if let Some(result) = &turn_result {
        if let Some((trace_id, turn)) = result.turns.last() {
            let n = turn.spans.len();
            let req = kimi_otel::finalize_export_resource(wrap_trace_export(turn.spans.clone(), &ctx), &cfg.agent_instance);
            logger.info(&format!(
                "[http] traces upload (latest turn only): spans={} trace_id={} root={}",
                n, trace_id, turn.root_span_id
            ));
            http_export::post_traces(&cfg, &req, logger);
        }
    }

    // logs：合并所有事件类型
    let all_events = turn_result.as_ref().map(|result| kimi_otel::build_correlated_events(records, session_id, &emit_ctx, result, true)).unwrap_or_default();
    if !all_events.is_empty() {
        let n = all_events.len();
        let req = kimi_otel::finalize_export_resource(wrap_logs_export(all_events, &ctx), &cfg.agent_instance);
        logger.info(&format!(
            "[http] logs upload: events={} (tool_result+api_request+user_prompt+compaction)",
            n
        ));
        http_export::post_logs(&cfg, &req, logger);
    }

    // metrics：始终上报（包含 aivision.agentic.up=1 心跳，即使 records 为空）
    let slices = kimi_otel::split_records_by_turn(records);
    let records = slices.last().copied().unwrap_or(records);
    let mut all_metrics = kimi_otel::build_tool_metrics(records, session_id, &emit_ctx);
    all_metrics.extend(kimi_otel::build_llm_metrics(records, session_id, &emit_ctx));
    all_metrics.extend(kimi_otel::build_tool_duration_metrics(records, session_id, &emit_ctx));
    all_metrics.extend(kimi_otel::build_session_token_metrics(records, session_id, &emit_ctx));
    let n = all_metrics.len();
    let req = kimi_otel::finalize_export_resource(wrap_metrics_export(all_metrics, &ctx), &cfg.agent_instance);
    logger.info(&format!("[http] metrics upload: instruments={}", n));
    http_export::post_metrics(&cfg, &req, logger);
}

/// 把 session 全部文件（wire + context + state + subagents）复制到 `KIMI_SESSION_DIR`（如已设置）。
///
/// 复用 `transcript::snapshot_to_session_dir()` 的统一迁移逻辑，支持目录模式和文件模式。
fn snapshot_session_to_dir(
    paths: &crate::common::session_locator::SessionPaths,
    logger: &crate::common::logger::Logger,
) {
    use crate::common::transcript;
    let report = transcript::snapshot_to_session_dir(paths, logger);
    if report.copied > 0 {
        logger.info(&format!(
            "session snapshot: copied {} bytes, skipped {} files",
            report.copied, report.skipped
        ));
    }
}
/// 计算 wire.jsonl 的指纹，用于幂等去重。
///
/// 指纹 = `"{file_size}:{line_count}"`。wire.jsonl 只会 append 新行（kimi-cli 不会修改
/// 已写出的行），所以文件大小 + 行数足以区分"有新内容"和"完全相同"。
fn wire_fingerprint(body: &str) -> String {
    // Fingerprint exactly the bytes parsed, never a newer append observed later.
    let size = body.len();
    let line_count = body.as_bytes().iter().filter(|&&b| b == b'\n').count();
    format!("{}:{}", size, line_count)
}

/// 从 wire.jsonl 重建出可被各 `build_*_spans()` 消费的合成 hook payload 列表。
///
/// Sprint 2 完整版：除 C3 工具链路外，额外重建：
/// - C1：`TurnBegin` / `TurnEnd`
/// - C2：`LLMStep`（StepBegin + ContentPart×N + StatusUpdate 聚合）
/// - C6：`SubagentEvent`（递归把 `event` 字段当作子事件流再 parse 一遍）
///
/// 输出顺序：严格按 wire.jsonl 行序，确保下游可以基于顺序还原 turn 内拓扑。
/// 同一 hook_event_name 的字段对齐 kimi-cli 的真实事件 schema（来源 `kimi_cli/hooks/events.py`）。
pub fn rebuild_hook_log_from_wire(
    wire_jsonl: &Path,
    session_id: &str,
    cwd: &str,
) -> Result<Vec<Value>, String> {
    let body = fs::read_to_string(wire_jsonl)
        .map_err(|e| format!("read({}) failed: {}", wire_jsonl.display(), e))?;
    Ok(rebuild_hook_log_from_snapshot(&body, wire_jsonl, session_id, cwd))
}

fn rebuild_hook_log_from_snapshot(
    body: &str,
    wire_jsonl: &Path,
    session_id: &str,
    cwd: &str,
) -> Vec<Value> {
    let mut synth = parse_wire_events(body, session_id, cwd);

    // 扫描独立 subagents 目录（kimi-cli 可能把 subagent 事件写在独立目录而非内联）。
    // 路径推导：
    //   原始路径: session_dir/wire.jsonl → session_dir/subagents/
    //   SESSION_DIR 模式: tool4.wire.jsonl → tool4.subagents/
    enrich_with_subagent_dirs(&mut synth, wire_jsonl, session_id, cwd);

    synth
}

/// 扫描独立 subagents 目录，将每个 subagent 的 wire.jsonl 解析为 SubagentEvent 注入 synth。
///
/// kimi-cli 有两种 subagent 数据格式：
/// 1. 内联模式：SubagentEvent 嵌入父 wire.jsonl（WireParser 已处理）
/// 2. 独立目录模式：`<session>/subagents/<agent_id>/wire.jsonl` + `meta.json`
///
/// 对于独立模式，需要：
/// 1. 在 synth 中查找 `PreToolUse(tool_name 含 "Agent")` 的 tool_call_id 作为 parent
/// 2. 解析 subagent wire.jsonl 为 nested_events
/// 3. 注入 SubagentEvent 合成事件
fn enrich_with_subagent_dirs(
    synth: &mut Vec<Value>,
    wire_jsonl: &Path,
    session_id: &str,
    cwd: &str,
) {
    // 推导 subagents 目录路径
    let subagents_dir = find_subagents_dir(wire_jsonl);
    let subagents_dir = match subagents_dir {
        Some(d) if d.is_dir() => d,
        _ => return,
    };

    // 如果已经有内联 SubagentEvent，不重复注入
    let has_inline = synth
        .iter()
        .any(|v| v.get("hook_event_name").and_then(|n| n.as_str()) == Some("SubagentEvent"));
    if has_inline {
        return;
    }

    // 收集所有 ToolCall(name=Agent) 的 tool_call_id → 用于关联 subagent
    let agent_call_ids: Vec<String> = synth
        .iter()
        .filter_map(|v| {
            let event = v.get("hook_event_name")?.as_str()?;
            if event != "PreToolUse" {
                return None;
            }
            let tool_name = v.get("tool_name")?.as_str()?;
            if !tool_name.contains("Agent") {
                return None;
            }
            v.get("tool_call_id")?.as_str().map(|s| s.to_string())
        })
        .collect();

    // 遍历 subagents 目录中的每个子目录
    let mut entries: Vec<_> = match fs::read_dir(&subagents_dir) {
        Ok(rd) => rd.filter_map(|e| e.ok()).collect(),
        Err(_) => return,
    };
    entries.sort_by_key(|e| e.file_name());

    for (idx, entry) in entries.iter().enumerate() {
        let sub_dir = entry.path();
        if !sub_dir.is_dir() {
            continue;
        }
        let meta_path = sub_dir.join("meta.json");
        let wire_path = sub_dir.join("wire.jsonl");
        if !wire_path.exists() {
            continue;
        }

        // 读取 meta.json 获取 agent_id + subagent_type
        let (agent_id, subagent_type) = if meta_path.exists() {
            match fs::read_to_string(&meta_path) {
                Ok(body) => {
                    let meta: Value = serde_json::from_str(&body).unwrap_or(Value::Null);
                    let aid = meta
                        .get("agent_id")
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .to_string();
                    let stype = meta
                        .get("subagent_type")
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .to_string();
                    (
                        if aid.is_empty() {
                            sub_dir
                                .file_name()
                                .unwrap_or_default()
                                .to_string_lossy()
                                .to_string()
                        } else {
                            aid
                        },
                        if stype.is_empty() {
                            "unknown".to_string()
                        } else {
                            stype
                        },
                    )
                }
                Err(_) => (
                    sub_dir
                        .file_name()
                        .unwrap_or_default()
                        .to_string_lossy()
                        .to_string(),
                    "unknown".to_string(),
                ),
            }
        } else {
            (
                sub_dir
                    .file_name()
                    .unwrap_or_default()
                    .to_string_lossy()
                    .to_string(),
                "unknown".to_string(),
            )
        };

        // 解析 subagent wire.jsonl
        let sub_body = match fs::read_to_string(&wire_path) {
            Ok(b) => b,
            Err(_) => continue,
        };
        let nested = parse_wire_events(&sub_body, session_id, cwd);
        if nested.is_empty() {
            continue;
        }

        // 确定 parent_tool_call_id：按顺序匹配 Agent ToolCall
        let parent_call_id = agent_call_ids.get(idx).cloned().unwrap_or_default();

        // 确定时间范围
        let first_ts = nested
            .first()
            .and_then(|v| v.get("_source_timestamp").and_then(|x| x.as_f64()))
            .unwrap_or(0.0);
        let last_ts = nested
            .last()
            .and_then(|v| v.get("_source_timestamp").and_then(|x| x.as_f64()))
            .unwrap_or(0.0);

        synth.push(json!({
            "hook_event_name": "SubagentEvent",
            "session_id": session_id,
            "cwd": cwd,
            "parent_tool_call_id": parent_call_id,
            "agent_id": agent_id,
            "subagent_type": subagent_type,
            "nested_events": nested,
            "_synthetic": true,
            "_source_timestamp": last_ts,
            "_first_ts": first_ts,
            "_last_ts": last_ts,
        }));
    }
}

/// 从 wire.jsonl 路径推导 subagents 目录。
///
/// 支持两种路径模式：
/// 1. 原始路径: `session_dir/wire.jsonl` → `session_dir/subagents/`
/// 2. SESSION_DIR 模式: `tool4.wire.jsonl` → `tool4.subagents/`
fn find_subagents_dir(wire_jsonl: &Path) -> Option<std::path::PathBuf> {
    let parent = wire_jsonl.parent()?;
    let stem = wire_jsonl.file_stem()?.to_string_lossy();

    // SESSION_DIR 模式: tool4.wire.jsonl → tool4.subagents/
    // 或 tool4.wire.jsonl 的 stem 是 "tool4.wire"，所以 strip ".wire"
    let base_name = stem.strip_suffix(".wire").unwrap_or(&stem);
    let session_dir_style = parent.join(format!("{}.subagents", base_name));
    if session_dir_style.is_dir() {
        return Some(session_dir_style);
    }

    // 原始路径: session_dir/wire.jsonl → session_dir/subagents/
    let native_style = parent.join("subagents");
    if native_style.is_dir() {
        return Some(native_style);
    }

    None
}

/// 解析一段 wire.jsonl 文本（多行 NDJSON）成合成 hook 事件列表。
///
/// 单独抽出便于 `SubagentEvent` 递归调用：subagent 的 `event` 字段不是 NDJSON 而是
/// 单条已解析的 message 对象，所以递归路径走 [`parse_single_wire_message`]，
/// 二者共用 [`WireParser`] 的事件聚合状态机。
fn parse_wire_events(body: &str, session_id: &str, cwd: &str) -> Vec<Value> {
    let mut parser = WireParser::new(session_id, cwd);
    let mut incomplete_turn = false;
    for line in body.lines() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        let v: Value = match serde_json::from_str(line) {
            Ok(v) => v,
            Err(_) => {
                incomplete_turn = true;
                continue;
            }
        };
        // metadata 行无 message。
        if v.get("type").and_then(|t| t.as_str()) == Some("metadata") {
            continue;
        }
        if let Some((kind, payload)) = super::kimi_code::normalize(&v) {
            if let Some(ms) = v.get("time").and_then(Value::as_f64) {
                if kind == "TurnBegin" {
                    incomplete_turn = false;
                }
                parser.feed_typed(&kind, &payload, ms / 1000.0);
            }
            continue;
        }
        let ts = v.get("timestamp").and_then(|t| t.as_f64()).unwrap_or(0.0);
        let msg = match v.get("message") {
            Some(m) => m,
            None => continue,
        };
        parser.feed(msg, ts);
    }
    let mut records = parser.finish();
    if incomplete_turn {
        if let Some(step) = records.iter_mut().rev()
            .take_while(|record| record["hook_event_name"] != "TurnBegin")
            .find(|record| record["hook_event_name"] == "LLMStep" && record["source"] == "kimi-code")
        {
            step["_turn_snapshot_incomplete"] = json!(true);
        }
    }
    records
}

/// wire.jsonl 事件聚合状态机。
///
/// 持有：
/// - `synth`：累积输出的 hook 事件
/// - `calls`：tool_call_id → 已知的 (tool_name, tool_input, ts) 映射，
///   用于 ToolResult 在没有同会话 ToolCall 时退化（孤儿 ToolResult 的极少见情况）
/// - `step`：当前未 flush 的 LLMStep accumulator（最多一个）
struct WireParser<'a> {
    session_id: &'a str,
    cwd: &'a str,
    synth: Vec<Value>,
    calls: std::collections::HashMap<String, String>, // call_id -> tool_name
    step: Option<LLMStepAccum>,
    /// Actual newly appended context and tool receipts, not a reconstructed full prompt.
    code_inputs: Vec<Value>,
    code_wire: bool,
    code_system_prompt: Option<(String, String)>,
    /// 待合并的 CompactionBegin 信息，等 CompactionEnd 到达后合成 Compaction 事件。
    compaction_pending: Option<CompactionPending>,
    /// 按 parent_tool_call_id 维护的共享 subagent parser。
    /// 同一 subagent 的所有 SubagentEvent inner events 按序喂入同一个 parser，
    /// 避免每条 SubagentEvent 独立 parse 导致 ContentPart/StatusUpdate 产生退化 LLMStep。
    subagent_parsers: std::collections::HashMap<String, SubagentAccum<'a>>,
}

/// CompactionBegin 到达后暂存，等配对的 CompactionEnd 合并为单条 Compaction 事件。
struct CompactionPending {
    trigger: String,
    tokens_before: i64,
    start_ts: f64,
}

/// Subagent 聚合状态：共享 parser + 元数据。
struct SubagentAccum<'a> {
    parser: WireParser<'a>,
    agent_id: String,
    subagent_type: String,
    first_ts: f64,
    last_ts: f64,
}

#[derive(Default)]
struct LLMStepAccum {
    step_n: i64,
    thinking: String,
    output_text: String,
    output_content: Vec<Value>,
    message_id: String,
    request_id: String,
    finish_reason: String,
    source: String,
    code_agent_id: Option<String>,
    model: String,
    provider: String,
    input_messages_json: String,
    system_instructions_json: String,
    duration_ms: Option<u64>,
    step_end_ts: Option<f64>,
    token_usage: Option<Value>,
    tool_calls: Vec<Value>,
    start_ts: f64,
    end_ts: f64,
}

fn reported_code_model(payload: &Value) -> Option<&str> {
    payload["model"].as_str()
        .map(str::trim)
        .filter(|model| !model.is_empty() && !model.eq_ignore_ascii_case("unknown"))
}

impl<'a> WireParser<'a> {
    fn new(session_id: &'a str, cwd: &'a str) -> Self {
        Self {
            session_id,
            cwd,
            synth: Vec::new(),
            calls: std::collections::HashMap::new(),
            step: None,
            code_inputs: Vec::new(),
            code_wire: false,
            code_system_prompt: None,
            compaction_pending: None,
            subagent_parsers: std::collections::HashMap::new(),
        }
    }

    /// 把当前 LLMStep accumulator flush 到输出列表。`flushed_at` 在 StatusUpdate
    /// 缺失时作为 end_ts 兜底。
    fn flush_step(&mut self, flushed_at: f64) {
        if let Some(s) = self.step.take() {
            let end_ts = if s.end_ts > 0.0 { s.end_ts } else { flushed_at };
            let mut record = json!({
                "hook_event_name": "LLMStep",
                "session_id": self.session_id,
                "cwd": self.cwd,
                "step_n": s.step_n,
                "thinking": s.thinking,
                "output_text": s.output_text,
                "output_content": s.output_content,
                "message_id": s.message_id,
                "request_id": s.request_id,
                "finish_reason": s.finish_reason,
                "source": s.source,
                "_input_messages_json": s.input_messages_json,
                "_system_instructions_json": s.system_instructions_json,
                "token_usage": s.token_usage.unwrap_or(Value::Null),
                "tool_calls": s.tool_calls,
                "_synthetic": true,
                "_start_ts": s.start_ts,
                "_end_ts": end_ts,
                "_source_timestamp": end_ts,
            });
            if let Some(id) = s.code_agent_id { record["_code_agent_id"] = json!(id); }
            if !s.model.is_empty() { record["model"] = json!(s.model); }
            if !s.provider.is_empty() { record["_provider"] = json!(s.provider); }
            if let Some(duration) = s.duration_ms { record["_duration_ms"] = json!(duration); }
            if let Some(end) = s.step_end_ts { record["_step_end_ts"] = json!(end); }
            self.synth.push(record);
        }
    }

    /// 处理 wire.jsonl 单条 message（已 unwrap 出 `{type, payload}`）。
    fn feed(&mut self, msg: &Value, ts: f64) {
        let mt = msg.get("type").and_then(|s| s.as_str()).unwrap_or("");
        let payload = msg.get("payload").cloned().unwrap_or(Value::Null);
        self.feed_typed(mt, &payload, ts);
    }

    fn feed_typed(&mut self, mt: &str, payload: &Value, ts: f64) {
        match mt {
            "CodeProfile" => {
                use sha2::{Digest, Sha256};
                if let Some(prompt) = payload.as_str() {
                    self.code_system_prompt = Some((prompt.to_string(), Sha256::digest(prompt.as_bytes()).iter().map(|byte| format!("{byte:02x}")).collect::<String>()));
                }
            }
            "CodeRequest" => {
                if let Some(acc) = self.step.as_mut() {
                    if let Some(model) = reported_code_model(payload) {
                        acc.model = model.to_owned();
                    }
                    acc.provider = payload["provider"].as_str().unwrap_or("").to_string();
                    acc.start_ts = ts;
                    if let Some((prompt, hash)) = &self.code_system_prompt {
                        if payload["system_prompt_hash"].as_str() == Some(hash.as_str()) {
                            acc.system_instructions_json = serde_json::to_string(prompt).unwrap_or_default();
                        }
                    }
                }
            }
            "CodeUsage" => {
                // step.end uses emptyUsage() even when the provider sent no usage.
                // Only durable usage.record preserves provider-usage presence.
                let mut mapped = serde_json::Map::new();
                for (source, target) in [("inputOther", "input_other"), ("output", "output"),
                    ("inputCacheRead", "input_cache_read"), ("inputCacheCreation", "input_cache_creation")] {
                    if let Some(value) = payload["usage"].get(source) { mapped.insert(target.into(), value.clone()); }
                }
                // Kimi Code writes the same fallback for absent HTTP usage and
                // explicit zero. 2.1.1 includes cache read=0 while 2.6.3 omits it.
                // Preserve other partial receipts, including their known zeros.
                let ambiguous_zero = ["input_other", "output", "input_cache_creation"]
                    .iter().all(|key| mapped.get(*key).and_then(Value::as_i64) == Some(0))
                    && mapped.get("input_cache_read").map_or(true, |value| value.as_i64() == Some(0));
                let usage = if ambiguous_zero { Value::Null } else { Value::Object(mapped) };
                let agent_id = payload["agent_id"].as_str();
                let model = reported_code_model(payload);
                if let Some(acc) = self.step.as_mut() {
                    if acc.source != "kimi-code" || acc.code_agent_id.as_deref() != agent_id {
                        return;
                    }
                    if acc.model.is_empty() {
                        acc.model = model.unwrap_or("").into();
                    }
                    acc.end_ts = ts;
                    acc.token_usage = Some(usage);
                } else if let Some(step) = self.synth.iter_mut().rev()
                    .take_while(|r| !matches!(r["hook_event_name"].as_str(), Some("TurnBegin" | "TurnEnd")))
                    .find(|r| r["hook_event_name"] == "LLMStep")
                    .filter(|r| r["source"] == "kimi-code" && r.get("_step_end_ts").is_some()
                        && r["_code_agent_id"].as_str() == agent_id) {
                    // Wire 1.1 can put the receipt just after step.end.
                    if step.get("model").is_none() {
                        if let Some(model) = model { step["model"] = json!(model); }
                    }
                    step["token_usage"] = usage;
                }
            }
            "CodeContext" => {
                if matches!(payload["role"].as_str(), Some("user" | "tool")) {
                    let mut message = json!({"role": payload["role"], "content": payload["content"]});
                    if let Some(id) = payload.get("toolCallId") { message["tool_call_id"] = id.clone(); }
                    self.code_inputs.push(message);
                }
            }
            "TurnBegin" => {
                // 任何遗留 step 先 flush（理论上不该有，防御性）
                self.flush_step(ts);
                let user_input = extract_user_input(payload);
                self.synth.push(json!({
                    "hook_event_name": "TurnBegin",
                    "session_id": self.session_id,
                    "cwd": self.cwd,
                    "user_input": user_input,
                    "user_content": agentic_otel_common::media::content_blocks(&payload["user_input"]),
                    "source": payload.get("source"),
                    "_synthetic": true,
                    "_source_timestamp": ts,
                }));
            }
            "TurnEnd" => {
                self.flush_step(ts);
                self.synth.push(json!({
                    "hook_event_name": "TurnEnd",
                    "session_id": self.session_id,
                    "cwd": self.cwd,
                    "reason": payload.get("reason"),
                    "source": payload.get("source"),
                    "_synthetic": true,
                    "_source_timestamp": ts,
                }));
            }
            "StepBegin" => {
                // 新 step 开始 → 先 flush 旧 step
                self.flush_step(ts);
                let n = payload.get("n").and_then(|v| v.as_i64()).unwrap_or(0);
                self.code_wire = payload["source"] == "kimi-code";
                self.step = Some(LLMStepAccum {
                    step_n: n,
                    request_id: payload["request_id"].as_str().unwrap_or("").to_string(),
                    source: payload["source"].as_str().unwrap_or("").to_string(),
                    code_agent_id: payload["agent_id"].as_str().map(str::to_owned),
                    input_messages_json: if self.code_wire {
                        serde_json::to_string(&std::mem::take(&mut self.code_inputs)).unwrap_or_default()
                    } else { String::new() },
                    start_ts: ts,
                    ..Default::default()
                });
            }
            "ContentPart" => {
                let part_type = payload.get("type").and_then(|v| v.as_str()).unwrap_or("");
                let acc = self.step.get_or_insert_with(|| LLMStepAccum {
                    start_ts: ts,
                    ..Default::default()
                });
                match part_type {
                    "think" => {
                        if let Some(t) = payload.get("think").and_then(|v| v.as_str()) {
                            acc.thinking.push_str(t);
                        }
                    }
                    "text" => {
                        if let Some(t) = payload.get("text").and_then(|v| v.as_str()) {
                            acc.output_text.push_str(t);
                            acc.output_content.push(json!({"type":"text","text":t}));
                        }
                    }
                    _ => { if let Some(media) = agentic_otel_common::media::media_block(payload) { acc.output_content.push(media); } }
                }
            }
            "StatusUpdate" => {
                // 这是 step 的"结束信号"——带回 token_usage + message_id 后立即 flush。
                // 如果当前没有 pending step（极端 case：StatusUpdate 先于 StepBegin），跳过。
                if let Some(acc) = self.step.as_mut() {
                    if acc.source == "kimi-code"
                        && (acc.request_id.is_empty()
                            || payload["request_id"].as_str() != Some(acc.request_id.as_str()))
                    {
                        return;
                    }
                    if let Some(mid) = payload.get("message_id").and_then(|v| v.as_str()) {
                        acc.message_id = mid.to_string();
                    }
                    if let Some(reason) = payload.get("finish_reason").and_then(Value::as_str) {
                        acc.finish_reason = reason.to_string();
                    }
                    if acc.source != "kimi-code" {
                        if let Some(tu) = payload.get("token_usage") { acc.token_usage = Some(tu.clone()); }
                    }
                    if acc.source == "kimi-code" {
                        acc.step_end_ts = Some(ts);
                        if let Some(ms) = payload["llm_duration_ms"].as_u64() {
                            acc.duration_ms = Some(ms);
                            acc.end_ts = acc.start_ts + ms as f64 / 1000.0;
                        } else if acc.end_ts == 0.0 { acc.end_ts = ts; }
                    } else { acc.end_ts = ts; }
                }
                self.flush_step(ts);
            }
            "ToolCall" => {
                // 不在 ToolCall 处 flush step——StatusUpdate 紧随 ToolCall 之后到达（<1ms），
                // 且 StatusUpdate 才带 token_usage。如果 StatusUpdate 缺失，
                // 下一个 StepBegin / TurnEnd 会兜底 flush。
                let call_id = payload
                    .get("id")
                    .and_then(|s| s.as_str())
                    .unwrap_or("")
                    .to_string();
                if call_id.is_empty() {
                    return;
                }
                let func = payload.get("function").cloned().unwrap_or(Value::Null);
                let tool_name = func
                    .get("name")
                    .and_then(|s| s.as_str())
                    .unwrap_or("")
                    .to_string();
                let args_str = func.get("arguments").and_then(|s| s.as_str()).unwrap_or("");
                let tool_input: Value = if args_str.is_empty() {
                    json!({})
                } else {
                    serde_json::from_str(args_str).unwrap_or(json!({"_raw": args_str}))
                };

                if let Some(acc) = self.step.as_mut().filter(|acc| payload["step_uuid"].as_str().map(|id| id == acc.request_id).unwrap_or(acc.source != "kimi-code")) {
                    acc.tool_calls.push(json!({"id": call_id, "type": "function", "function": {
                        "name": tool_name, "arguments": args_str}}));
                }
                self.calls.insert(call_id.clone(), tool_name.clone());

                self.synth.push(json!({
                    "hook_event_name": "PreToolUse",
                    "session_id": self.session_id,
                    "cwd": self.cwd,
                    "tool_name": tool_name,
                    "tool_input": tool_input,
                    "tool_call_id": call_id,
                    "_synthetic": true,
                    "_source_timestamp": ts,
                }));
            }
            "ToolResult" => {
                let call_id = payload
                    .get("tool_call_id")
                    .and_then(|s| s.as_str())
                    .unwrap_or("")
                    .to_string();
                if call_id.is_empty() {
                    return;
                }
                let rv = payload.get("return_value").cloned().unwrap_or(Value::Null);
                let is_error = rv
                    .get("is_error")
                    .and_then(|b| b.as_bool())
                    .unwrap_or(false);
                let output_str = rv
                    .get("output")
                    .and_then(|s| s.as_str())
                    .unwrap_or("")
                    .to_string();
                let err_msg = rv
                    .get("message")
                    .and_then(|s| s.as_str())
                    .unwrap_or("")
                    .to_string();
                let tool_name = self
                    .calls
                    .get(&call_id)
                    .cloned()
                    .unwrap_or_else(|| "unknown".to_string());

                if self.code_wire {
                    let note = rv["note"].as_str().unwrap_or("");
                    // Official contextMemory/toolResultRender.ts string output branch.
                    let mut content = if is_error {
                        if output_str.is_empty() { "<system>ERROR: Tool execution failed. Tool output is empty.</system>".to_string() }
                        else { format!("<system>ERROR: Tool execution failed.</system>\n{output_str}") }
                    } else if output_str.trim().is_empty() || output_str.trim() == "Tool output is empty." {
                        "<system>Tool output is empty.</system>".to_string()
                    } else { output_str.clone() };
                    if matches!(tool_name.as_str(), "Agent" | "AgentSwarm" | "Bash" | "FetchURL" | "Glob" | "Grep" | "WebSearch") || tool_name.starts_with("mcp__") {
                        if let Some(ms) = rv["duration_ms"].as_f64() { content = format!("Wall time: {:.3} seconds\n{content}", ms / 1000.0); }
                    }
                    if !note.is_empty() { content.push('\n'); content.push_str(note); }
                    self.code_inputs.push(json!({"role": "tool", "tool_call_id": call_id, "content": content}));
                }

                if is_error {
                    self.synth.push(json!({
                        "hook_event_name": "PostToolUseFailure",
                        "session_id": self.session_id,
                        "cwd": self.cwd,
                        "tool_name": tool_name,
                        "tool_call_id": call_id,
                        "error": if err_msg.is_empty() { output_str } else { err_msg },
                        "_synthetic": true,
                        "_source_timestamp": ts,
                    }));
                } else {
                    self.synth.push(json!({
                        "hook_event_name": "PostToolUse",
                        "session_id": self.session_id,
                        "cwd": self.cwd,
                        "tool_name": tool_name,
                        "tool_call_id": call_id,
                        "tool_output": output_str,
                        "_synthetic": true,
                        "_source_timestamp": ts,
                    }));
                }
            }
            "SubagentEvent" => {
                // payload = {parent_tool_call_id, agent_id, subagent_type, event: {type, payload}}
                // 按 parent_tool_call_id 维护共享 parser，同一 subagent 的所有 inner events
                // 按序喂入同一个 parser，保持 StepBegin → ContentPart → StatusUpdate 的状态连续性。
                // finish() 时统一 flush 所有 subagent parser 并输出 SubagentEvent 合成事件。
                let parent = payload
                    .get("parent_tool_call_id")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
                let agent_id = payload
                    .get("agent_id")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
                let subagent_type = payload
                    .get("subagent_type")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
                let inner = payload.get("event").cloned().unwrap_or(Value::Null);
                let inner_type = inner
                    .get("type")
                    .and_then(|s| s.as_str())
                    .unwrap_or("")
                    .to_string();
                let inner_payload = inner.get("payload").cloned().unwrap_or(Value::Null);

                let accum = self
                    .subagent_parsers
                    .entry(parent.clone())
                    .or_insert_with(|| SubagentAccum {
                        parser: WireParser::new(self.session_id, self.cwd),
                        agent_id: agent_id.clone(),
                        subagent_type: subagent_type.clone(),
                        first_ts: ts,
                        last_ts: ts,
                    });
                if ts < accum.first_ts {
                    accum.first_ts = ts;
                }
                if ts > accum.last_ts {
                    accum.last_ts = ts;
                }
                accum.parser.feed_typed(&inner_type, &inner_payload, ts);
            }
            // ── D4 StepRetry：API 错误恢复 → 合成 ApiRetry 事件 ──
            "StepRetry" => {
                let error_type = payload
                    .get("error_type")
                    .and_then(|v| v.as_str())
                    .unwrap_or("unknown")
                    .to_string();
                let status_code = payload
                    .get("status_code")
                    .and_then(|v| v.as_i64())
                    .unwrap_or(0);
                let attempt = payload
                    .get("next_attempt")
                    .and_then(|v| v.as_i64())
                    .unwrap_or(1);
                let max_attempts = payload
                    .get("max_attempts")
                    .and_then(|v| v.as_i64())
                    .unwrap_or(3);
                let wait_s = payload
                    .get("wait_s")
                    .and_then(|v| v.as_f64())
                    .unwrap_or(0.0);
                self.synth.push(json!({
                    "hook_event_name": "ApiRetry",
                    "session_id": self.session_id,
                    "cwd": self.cwd,
                    "error_type": error_type,
                    "status_code": status_code,
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                    "wait_s": wait_s,
                    "_synthetic": true,
                    "_source_timestamp": ts,
                }));
            }

            // ToolCallPart 是流式增量 arguments（UI 展示用），ToolCall 已含完整 arguments，
            // 跳过不会丢失数据。其它未知 type 静默忽略（前向兼容新版本 protocol）。

            // ── C8 Compaction：CompactionBegin/End 合并为单条 Compaction 事件 ──
            "CompactionBegin" => {
                let trigger = payload
                    .get("trigger")
                    .and_then(|v| v.as_str())
                    .unwrap_or("auto")
                    .to_string();
                let tokens_before = payload
                    .get("token_count")
                    .and_then(|v| v.as_i64())
                    .unwrap_or(0);
                self.compaction_pending = Some(CompactionPending {
                    trigger,
                    tokens_before,
                    start_ts: ts,
                });
            }
            "CompactionEnd" => {
                if let Some(pre) = self.compaction_pending.take() {
                    let tokens_after = payload
                        .get("estimated_token_count")
                        .and_then(|v| v.as_i64())
                        .unwrap_or(0);
                    let tokens_saved = (pre.tokens_before - tokens_after).max(0);
                    self.synth.push(json!({
                        "hook_event_name": "Compaction",
                        "session_id": self.session_id,
                        "cwd": self.cwd,
                        "trigger": pre.trigger,
                        "tokens_before": pre.tokens_before,
                        "tokens_after": tokens_after,
                        "tokens_saved": tokens_saved,
                        "_start_ts": pre.start_ts,
                        "_end_ts": ts,
                        "_synthetic": true,
                        "_source_timestamp": ts,
                    }));
                }
            }

            _ => {}
        }
    }

    fn finish(mut self) -> Vec<Value> {
        // 流末尾如果还有未 flush 的 step（极端 case：wire.jsonl 中途截断），
        // 用最后一条事件的时间戳兜底 flush 出去，避免数据丢失。
        let last_ts = self
            .synth
            .last()
            .and_then(|v| v.get("_source_timestamp").and_then(|x| x.as_f64()))
            .unwrap_or(0.0);
        self.flush_step(last_ts);

        // Flush 所有 subagent parser，输出合并后的 SubagentEvent 合成事件。
        // 使用 BTreeMap 保证 parent_tool_call_id 排序稳定（确定性输出）。
        let subagent_parsers: std::collections::BTreeMap<_, _> =
            self.subagent_parsers.into_iter().collect();
        for (parent, accum) in subagent_parsers {
            let nested = accum.parser.finish();
            if nested.is_empty() {
                continue;
            }
            self.synth.push(json!({
                "hook_event_name": "SubagentEvent",
                "session_id": self.session_id,
                "cwd": self.cwd,
                "parent_tool_call_id": parent,
                "agent_id": accum.agent_id,
                "subagent_type": accum.subagent_type,
                "nested_events": nested,
                "_synthetic": true,
                "_source_timestamp": accum.last_ts,
                "_first_ts": accum.first_ts,
                "_last_ts": accum.last_ts,
            }));
        }

        self.synth.sort_by(|a, b| {
            source_timestamp(a)
                .partial_cmp(&source_timestamp(b))
                .unwrap_or(std::cmp::Ordering::Equal)
        });

        self.synth
    }
}

fn source_timestamp(v: &Value) -> f64 {
    v.get("_source_timestamp")
        .and_then(|ts| ts.as_f64())
        .unwrap_or(0.0)
}

/// 把 TurnBegin.payload.user_input 拼成单一字符串。
///
/// kimi-cli 的 user_input 既可能是 `[{"type":"text","text":"hello"}]`（root turn）
/// 也可能直接是 `"..."` 字符串（subagent.event.TurnBegin 见 c100314b L23）。
fn extract_user_input(payload: &Value) -> String {
    if let Some(s) = payload.get("user_input").and_then(|v| v.as_str()) {
        return s.to_string();
    }
    if let Some(arr) = payload.get("user_input").and_then(|v| v.as_array()) {
        let mut buf = String::new();
        for item in arr {
            if let Some(t) = item.get("text").and_then(|v| v.as_str()) {
                if !buf.is_empty() {
                    buf.push('\n');
                }
                buf.push_str(t);
            }
        }
        return buf;
    }
    String::new()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    fn write_temp_wire(lines: &[&str]) -> std::path::PathBuf {
        let p = std::env::temp_dir().join(format!(
            "kimi-stop-test-{}-{}.jsonl",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        let mut f = std::fs::File::create(&p).unwrap();
        for l in lines {
            writeln!(f, "{}", l).unwrap();
        }
        p
    }

    #[test]
    fn wire_retains_media_input_and_output() {
        let wire = write_temp_wire(&[
            r#"{"type":"metadata","protocol_version":"1.9"}"#,
            r#"{"timestamp":1.0,"message":{"type":"TurnBegin","payload":{"user_input":[{"type":"document","source":{"type":"base64","media_type":"application/pdf","data":"YWJj"}}]}}}"#,
            r#"{"timestamp":2.0,"message":{"type":"StepBegin","payload":{"n":1}}}"#,
            r#"{"timestamp":3.0,"message":{"type":"ContentPart","payload":{"type":"audio","source":{"type":"base64","media_type":"audio/wav","data":"YWJj"}}}}"#,
            r#"{"timestamp":4.0,"message":{"type":"TurnEnd","payload":{}}}"#,
        ]);
        let records = rebuild_hook_log_from_wire(&wire, "s", "/c").unwrap();
        std::fs::remove_file(wire).unwrap();
        assert_eq!(records[0]["user_content"][0]["source"]["media_type"], "application/pdf");
        let step = records.iter().find(|r| r["hook_event_name"] == "LLMStep").unwrap();
        assert_eq!(step["output_content"][0]["source"]["media_type"], "audio/wav");
    }

    #[test]
    fn canonical_api_counter_merges_success_and_error_with_same_dimensions() {
        let cfg = kimi_otel::ContentLogConfig::default();
        let ctx = kimi_otel::EmissionContext { agent_product: "kimi-cli", agent_type: "kimicli", runtime_name: "kimi-cli", log: &cfg };
        let records: Vec<HookRecord> = ["end_turn", "interrupted"].iter().map(|reason| HookRecord {
            recorded_at_ns: "0".into(), payload: json!({"hook_event_name":"LLMStep", "source":"kimi-code", "model":"test", "_provider":"openai", "finish_reason":reason, "_duration_ms":1})
        }).collect();
        let metrics = serde_json::to_value(kimi_otel::build_llm_metrics(&records, "s", &ctx)).unwrap();
        let count = metrics.as_array().unwrap().iter().find(|m| m["name"] == "agent.api_request.count").unwrap();
        assert_eq!(count["sum"]["dataPoints"].as_array().unwrap().len(), 1, "one cumulative stream per canonical dimension set");
        assert_eq!(count["sum"]["dataPoints"][0]["asInt"], 2);
        assert_eq!(count["sum"]["aggregationTemporality"], 1, "Stop emits a fresh per-turn interval, not a retained cumulative total");
        let histogram = metrics.as_array().unwrap().iter().find(|m| m["name"] == "agent.api_request.duration").unwrap();
        assert_eq!(histogram["histogram"]["aggregationTemporality"], 1);
        let legacy = metrics.as_array().unwrap().iter().find(|m| m["name"] == "agent.llm_request.count").unwrap();
        assert_eq!(legacy["sum"]["aggregationTemporality"], 2, "preserve the existing legacy metric contract");
    }

    #[test]
    fn kimi_code_stop_snapshot_root_uses_observed_step_end_not_llm_duration() {
        let body = include_str!("../../tests/fixtures/kimi-code-2.1.1/native-tool-loop.wire.jsonl");
        // Official Stop executes before turn.ended is appended. Its snapshot can
        // close the turn only at the observed terminal step projection.
        let snapshot = body.lines().filter(|line| serde_json::from_str::<Value>(line).unwrap()["type"] != "turn.ended").collect::<Vec<_>>().join("\n");
        let values = parse_wire_events(&snapshot, "s", "/fixture");
        let records = synth_to_hook_records(&values);
        let root = kimi_otel::build_root_span(&records, "t", &kimi_otel::ContentLogConfig::default()).unwrap();
        assert_eq!(root.end_time_unix_nano, secs_to_nanos_string(&json!({"_source_timestamp":1791172328.149})));
        assert_eq!(root.status.code, 1);
        assert_eq!(values.iter().filter(|r| r["hook_event_name"] == "LLMStep").last().unwrap()["_duration_ms"], 4);
    }

    #[test]
    fn tool_failure_stays_on_tool_after_the_turn_completes() {
        let body = include_str!("../../tests/fixtures/kimi-code-2.1.1/native-tool-loop.wire.jsonl");
        let make_wire = |terminal: Option<&str>, final_step: bool, receipt: bool| {
            body.lines()
                .filter_map(|line| {
                    let mut row: Value = serde_json::from_str(line).unwrap();
                    if row["event"]["type"] == "tool.result" {
                        if !receipt {
                            return None;
                        }
                        row["event"]["result"]["isError"] = json!(true);
                        row["event"]["result"]["message"] = json!("Tool unavailable");
                    }
                    if row["event"]["finishReason"] == "end_turn" && !final_step {
                        return None;
                    }
                    if row["type"] == "turn.ended" {
                        row["reason"] = json!(terminal?);
                    }
                    Some(row.to_string())
                })
                .collect::<Vec<_>>()
                .join("\n")
        };
        let cfg = kimi_otel::ContentLogConfig {
            spans_model_outputs: true,
            ..Default::default()
        };
        for (terminal, final_step, receipt, expected) in [
            (Some("completed"), true, true, 1),
            (None, true, true, 1), // Ordinary Stop: turn.ended not yet written.
            (None, false, true, 0), // Partial assistant text is not completion.
            (None, true, false, 0), // Missing tool receipt.
            (Some(""), true, true, 0), // Missing native turn outcome.
            (Some("failed"), false, true, 2),
            (Some("blocked"), false, true, 2),
            (Some("cancelled"), false, true, 2),
        ] {
            let values = parse_wire_events(&make_wire(terminal, final_step, receipt), "s", "/fixture");
            let records = synth_to_hook_records(&values);
            let turn = kimi_otel::build_turn_spans(&records, "t", &cfg, "").unwrap();
            let root = turn.spans.iter().find(|span| span.span_id == turn.root_span_id).unwrap();
            assert_eq!(root.status.code, expected, "terminal={terminal:?}, final_step={final_step}, receipt={receipt}");
            let root_json = serde_json::to_string(root).unwrap();
            assert!(root_json.contains("KIMI_NATIVE_211_READ_OK"), "keep the recorded output for every turn status");
            if receipt {
                let tool = turn.spans.iter().find(|span| span.name == "tool Read").unwrap();
                assert_eq!(tool.parent_span_id, turn.root_span_id);
                assert_eq!(tool.status.code, 2);
                assert_eq!(tool.status.message.as_deref(), Some("Tool unavailable"));
            }
        }
    }

    #[test]
    fn legacy_turn_end_without_reason_keeps_its_existing_error_classification() {
        let wire = r#"{"timestamp":1.0,"message":{"type":"TurnBegin","payload":{"user_input":"Read a file"}}}
{"timestamp":2.0,"message":{"type":"ToolCall","payload":{"id":"call","function":{"name":"Read","arguments":"{}"}}}}
{"timestamp":3.0,"message":{"type":"ToolResult","payload":{"tool_call_id":"call","return_value":{"is_error":true,"message":"Not found"}}}}
{"timestamp":4.0,"message":{"type":"TurnEnd","payload":{}}}"#;
        let records = synth_to_hook_records(&parse_wire_events(wire, "s", "/fixture"));
        let cfg = kimi_otel::ContentLogConfig::default();
        let turn = kimi_otel::build_turn_spans(&records, "t", &cfg, "").unwrap();
        assert_eq!(turn.spans[0].status.code, 2);
        let tool = turn.spans.iter().find(|span| span.name == "tool Read").unwrap();
        assert_eq!(tool.status.code, 2);
    }

    #[test]
    fn stale_step_end_does_not_mark_an_unfinished_turn_ok() {
        let wire = r#"{"type":"turn.prompt","input":[],"time":1000}
{"type":"context.append_loop_event","event":{"type":"step.begin","uuid":"current","step":1},"time":1100}
{"type":"context.append_loop_event","event":{"type":"content.part","part":{"type":"text","text":"partial"}},"time":1200}
{"type":"context.append_loop_event","event":{"type":"step.end","uuid":"old","step":1,"finishReason":"end_turn"},"time":1300}"#;
        let values = parse_wire_events(wire, "s", "/fixture");
        let root = kimi_otel::build_root_span(&synth_to_hook_records(&values), "t", &kimi_otel::ContentLogConfig::default()).unwrap();
        assert_eq!(root.status.code, 0);

        let body = include_str!("../../tests/fixtures/kimi-code-2.1.1/native-tool-loop.wire.jsonl");
        let snapshot = body.lines().filter(|line| !line.contains("\"turn.ended\"")).collect::<Vec<_>>().join("\n");
        let truncated = format!("{snapshot}\n{{\"type\":\"context.append_loop_event\",\"event\":{{\"type\":\"step.begin\"");
        let values = parse_wire_events(&truncated, "s", "/fixture");
        let root = kimi_otel::build_root_span(&synth_to_hook_records(&values), "t", &kimi_otel::ContentLogConfig::default()).unwrap();
        assert_eq!(root.status.code, 0, "a malformed tail cannot prove the most recent step is final");
    }

    #[test]
    fn kimi_code_native_system_prompt_is_bound_to_each_request_hash() {
        let body = include_str!("../../tests/fixtures/kimi-code-2.1.1/native-system-bound.wire.jsonl");
        let rows: Vec<Value> = body.lines().map(|line| serde_json::from_str(line).unwrap()).collect();
        let source = rows.iter().find(|r| r["type"] == "profile.bind").unwrap()["systemPrompt"].as_str().unwrap();
        let values = parse_wire_events(body, "s", "/fixture");
        for step in values.iter().filter(|r| r["hook_event_name"] == "LLMStep") {
            assert_eq!(serde_json::from_str::<String>(step["_system_instructions_json"].as_str().unwrap()).unwrap(), source);
        }
        let changed = rows.into_iter().map(|mut r| { if r["type"] == "llm.request" { r["systemPromptHash"] = json!("unmatched"); } r.to_string() }).collect::<Vec<_>>().join("\n");
        for step in parse_wire_events(&changed, "s", "/fixture").iter().filter(|r| r["hook_event_name"] == "LLMStep") {
            assert_eq!(step["_system_instructions_json"], "", "stale profile must not be claimed as the actual request system");
        }
    }

    #[test]
    fn duration_histogram_upper_bounds_are_inclusive() {
        let cfg = kimi_otel::ContentLogConfig::default();
        let ctx = kimi_otel::EmissionContext { agent_product: "kimi-cli", agent_type: "kimicli", runtime_name: "kimi-cli", log: &cfg };
        let records = vec![HookRecord { recorded_at_ns: "0".into(), payload: json!({"hook_event_name":"LLMStep", "_duration_ms":500}) }];
        let metrics = serde_json::to_value(kimi_otel::build_llm_metrics(&records, "s", &ctx)).unwrap();
        let hist = metrics.as_array().unwrap().iter().find(|m| m["name"] == "agent.api_request.duration").unwrap();
        assert_eq!(hist["histogram"]["dataPoints"][0]["bucketCounts"][0], 1);
        let tools = vec![HookRecord { recorded_at_ns: "1000000000".into(), payload: json!({"hook_event_name":"PreToolUse", "tool_call_id":"c", "tool_name":"Read"}) },
            HookRecord { recorded_at_ns: "1050000000".into(), payload: json!({"hook_event_name":"PostToolUse", "tool_call_id":"c"}) }];
        let metrics = serde_json::to_value(kimi_otel::build_tool_duration_metrics(&tools, "s", &ctx)).unwrap();
        assert_eq!(metrics[0]["histogram"]["dataPoints"][0]["bucketCounts"][0], 1);
    }

    #[test]
    fn kimi_code_cache_metrics_count_fresh_input_and_physical_requests() {
        let records = synth_to_hook_records(&parse_wire_events(include_str!("../../tests/fixtures/kimi-code-2.1.1/native-cache-usage.wire.jsonl"), "s", "/fixture"));
        let cfg = kimi_otel::ContentLogConfig::default();
        let ctx = kimi_otel::EmissionContext { agent_product: "kimi-cli", agent_type: "kimicli", runtime_name: "kimi-cli", log: &cfg };
        let usage = serde_json::to_value(kimi_otel::build_session_token_metrics(&records, "s", &ctx)).unwrap();
        let token = usage.as_array().unwrap().iter().find(|m| m["name"] == "agent.token.usage").unwrap();
        let input = token["sum"]["dataPoints"].as_array().unwrap().iter().find(|point| point["attributes"].as_array().unwrap().iter().any(|a| a["key"] == "gen_ai.token.type" && a["value"]["stringValue"] == "input")).unwrap();
        assert_eq!(input["asInt"], 26, "2 native requests each have 13 fresh input + 3 cache read");
        let metrics = serde_json::to_value(kimi_otel::build_llm_metrics(&records, "s", &ctx)).unwrap();
        let count = metrics.as_array().unwrap().iter().find(|m| m["name"] == "agent.api_request.count").expect("physical API counter");
        assert_eq!(count["sum"]["dataPoints"][0]["asInt"], 2);
        assert!(metrics.as_array().unwrap().iter().any(|m| m["name"] == "agent.api_request.duration"));
    }

    #[test]
    fn kimi_code_native_unknown_and_zero_have_the_same_ambiguous_usage() {
        // Kimi Code 2.6.3 omits inputCacheRead when its usage fallback is written.
        let code_2_6_3 = r#"{"type":"turn.prompt","agentId":"main","input":[],"time":1000}
{"type":"context.append_loop_event","agentId":"main","event":{"type":"step.begin","uuid":"u","step":1},"time":1100}
{"type":"usage.record","agentId":"main","model":"gateway/route/model-a","usageScope":"turn","usage":{"inputOther":0,"output":0,"inputCacheCreation":0},"time":1190}
{"type":"context.append_loop_event","agentId":"main","event":{"type":"step.end","uuid":"u","step":1,"finishReason":"end_turn"},"time":1200}"#;
        for body in [
            include_str!("../../tests/fixtures/kimi-code-2.1.1/native-unknown-usage.wire.jsonl"),
            include_str!("../../tests/fixtures/kimi-code-2.1.1/native-zero-usage.wire.jsonl"),
            code_2_6_3,
        ] {
            let records = synth_to_hook_records(&parse_wire_events(body, "s", "/fixture"));
            let cfg = kimi_otel::ContentLogConfig::default();
            let ctx = kimi_otel::EmissionContext { agent_product: "kimi-cli", agent_type: "kimicli", runtime_name: "kimi-cli", log: &cfg };
            let spans = kimi_otel::build_llm_spans(&records, "t", "p", "s", 1, &cfg, "");
            let logs = kimi_otel::build_api_request_events(&records, "s", &ctx);
            for value in [serde_json::to_value(&spans[0]).unwrap(), serde_json::to_value(&logs[0]).unwrap()] {
                let attrs = value["attributes"].as_array().unwrap();
                for name in ["input_tokens", "output_tokens"] {
                    assert!(!attrs.iter().any(|a| a["key"] == format!("gen_ai.usage.{name}")), "SDK-generated all-zero usage is ambiguous: {value}");
                    assert!(attrs.iter().any(|a| a["key"] == format!("agentic.usage.{name}.missing") && a["value"]["stringValue"] == "true"));
                }
                assert!(!attrs.iter().any(|a| a["key"] == "gen_ai.usage.total_tokens"));
            }
            let metrics = kimi_otel::build_session_token_metrics(&records, "s", &ctx);
            assert!(!serde_json::to_string(&metrics).unwrap().contains("agent.token.usage"));
        }
    }

    #[test]
    fn kimi_code_synthesized_empty_usage_is_unknown_without_usage_record() {
        let body = r#"{"type":"context.append_loop_event","event":{"type":"step.begin","uuid":"u","step":1},"time":1100}
{"type":"context.append_loop_event","event":{"type":"step.end","uuid":"u","step":1,"finishReason":"end_turn","usage":{"inputOther":0,"output":0,"inputCacheRead":0,"inputCacheCreation":0}},"time":1200}"#;
        let values = parse_wire_events(body, "s", "/tmp/work");
        assert!(values[0]["token_usage"].is_null(), "step.end emptyUsage is not a provider-reported zero");
    }

    #[test]
    fn kimi_code_native_cli_tool_loop_keeps_requests_model_and_receipt() {
        let body = include_str!("../../tests/fixtures/kimi-code-2.1.1/native-tool-loop.wire.jsonl");
        let records = parse_wire_events(body, "native", "/fixture/work");
        let steps: Vec<_> = records.iter().filter(|r| r["hook_event_name"] == "LLMStep").collect();
        assert_eq!(steps.len(), 2, "the real provider observed exactly two requests");
        assert_eq!(steps[0]["_start_ts"], 1791172328.060);
        assert!(((steps[0]["_end_ts"].as_f64().unwrap() - steps[0]["_start_ts"].as_f64().unwrap()) * 1000.0 - 44.0).abs() < 0.01,
            "the 44 ms LLM request must exclude tool execution before step.end");
        for (index, step) in steps.iter().enumerate() {
            assert_eq!(step["model"], "native-e2e-chat");
            assert_eq!(step["_provider"], "openai");
            assert_eq!(step["message_id"], format!("chatcmpl_native_{}", index + 1));
            assert_eq!(step["token_usage"]["input_other"], 16);
            assert_eq!(step["token_usage"]["output"], 4);
        }
        let conflicting_receipt = body.lines().map(|line| {
            let mut record: Value = serde_json::from_str(line).unwrap();
            if record["type"] == "usage.record" { record["model"] = json!("other/conflicting"); }
            record.to_string()
        }).collect::<Vec<_>>().join("\n");
        for step in parse_wire_events(&conflicting_receipt, "native", "/fixture/work")
            .into_iter().filter(|r| r["hook_event_name"] == "LLMStep") {
            assert_eq!(step["model"], "native-e2e-chat");
            assert_eq!(step["_provider"], "openai");
        }
        assert_eq!(steps[0]["tool_calls"][0]["id"], "call_native_1");
        assert_eq!(steps[0]["tool_calls"][0]["function"]["name"], "Read");
        assert!(steps[0]["tool_calls"][0]["function"]["arguments"].as_str().unwrap().contains("receipt.txt"));
        assert!(steps[1]["output_text"].as_str().unwrap().contains("KIMI_NATIVE_211_READ_OK"));
        assert!(steps[1]["_input_messages_json"].as_str().unwrap().contains("call_native_1"));
        let tools: Vec<_> = records.iter().filter(|r| r["hook_event_name"] == "PostToolUse").collect();
        assert_eq!(tools.len(), 1);
        assert_eq!(tools[0]["tool_call_id"], "call_native_1");
        assert!(tools[0]["tool_output"].as_str().unwrap().contains("KIMI_NATIVE_211_READ_OK"));
    }

    #[test]
    fn legacy_wire_preserves_absent_model_provider_defaults_across_signals() {
        let body = r#"{"timestamp":1.0,"message":{"type":"StepBegin","payload":{"n":1}}}
{"timestamp":2.0,"message":{"type":"StatusUpdate","payload":{"token_usage":{"input_other":10,"output":5}}}}"#;
        let values = parse_wire_events(body, "s", "/tmp/work");
        assert!(values[0].get("model").is_none());
        assert!(values[0].get("_provider").is_none());
        let records = synth_to_hook_records(&values);
        let cfg = kimi_otel::ContentLogConfig::default();
        let ctx = kimi_otel::EmissionContext { agent_product: "kimi-cli", agent_type: "kimicli", runtime_name: "kimi-cli", log: &cfg };
        let logs = serde_json::to_value(kimi_otel::build_api_request_events(&records, "s", &ctx)).unwrap();
        assert!(logs[0]["attributes"].as_array().unwrap().iter().any(|a| a["key"] == "gen_ai.request.model" && a["value"]["stringValue"] == "unknown"));
        assert!(logs[0]["attributes"].as_array().unwrap().iter().any(|a| a["key"] == "gen_ai.system" && a["value"]["stringValue"] == "moonshot"));
        let metrics = serde_json::to_string(&kimi_otel::build_llm_metrics(&records, "s", &ctx)).unwrap();
        assert!(metrics.contains("moonshot") && metrics.contains("unknown"));
        let spans = kimi_otel::build_llm_spans(&records, "t", "p", "s", 1, &cfg, "configured-legacy");
        assert_eq!(spans[0].name, "chat configured-legacy");
    }

    #[test]
    fn kimi_code_interrupted_call_stays_error_with_unknown_usage() {
        let body = r#"{"type":"turn.prompt","input":[{"type":"text","text":"cancel me"}],"time":1000}
{"type":"context.append_loop_event","event":{"type":"step.begin","uuid":"req-1","step":1},"time":1100}
{"type":"context.append_loop_event","event":{"type":"step.end","uuid":"req-1","step":1,"finishReason":"interrupted"},"time":1200}
{"type":"turn.ended","reason":"cancelled","time":1300}"#;
        let values = parse_wire_events(body, "s", "/tmp/work");
        let records: Vec<HookRecord> = values.into_iter().map(|payload| HookRecord { payload, recorded_at_ns: "0".into() }).collect();
        let spans = kimi_otel::build_llm_spans(&records, "trace", "parent", "s", 1,
            &kimi_otel::ContentLogConfig::default(), "test-model");
        assert_eq!(spans.len(), 1);
        let span = serde_json::to_value(&spans[0]).unwrap();
        assert_eq!(span["status"]["code"], 2, "interruption cannot report an OK LLM span");
        let attrs = span["attributes"].as_array().unwrap();
        assert!(attrs.iter().any(|a| a["key"] == "agentic.usage.input_tokens.missing"));
        assert!(!attrs.iter().any(|a| a["key"] == "gen_ai.usage.input_tokens"));
        assert!(!attrs.iter().any(|a| a["key"] == "gen_ai.usage.output_tokens"));
        assert!(!attrs.iter().any(|a| a["key"] == "gen_ai.usage.total_tokens"));
        assert!(attrs.iter().any(|a| a["key"] == "agentic.model_call.id" && a["value"]["stringValue"] == "req-1"));
        assert_eq!(HookEvent::parse("Interrupt").map(|e| e.as_str()), Some("Interrupt"));
        let root = kimi_otel::build_root_span(&records, "trace", &kimi_otel::ContentLogConfig::default()).unwrap();
        assert_eq!(serde_json::to_value(root).unwrap()["status"]["code"], 2);
        let cfg = kimi_otel::ContentLogConfig::default();
        let ctx = kimi_otel::EmissionContext { agent_product: "kimi-cli", agent_type: "kimicli", runtime_name: "kimi-cli", log: &cfg };
        let log = serde_json::to_value(&kimi_otel::build_api_request_events(&records, "s", &ctx)[0]).unwrap();
        let attrs = log["attributes"].as_array().unwrap();
        for name in ["input_tokens", "output_tokens"] {
            assert!(attrs.iter().any(|a| a["key"] == format!("agentic.usage.{name}.missing") && a["value"]["stringValue"] == "true"));
            assert!(!attrs.iter().any(|a| a["key"] == format!("gen_ai.usage.{name}")));
        }
        assert!(attrs.iter().any(|a| a["key"] == "error.type" && a["value"]["stringValue"] == "cancelled"));
        assert!(attrs.iter().any(|a| a["key"] == "error.message" && a["value"]["stringValue"] == "interrupted"),
            "cancellation must retain the native step.end finishReason verbatim");
    }

    #[test]
    fn kimi_code_usage_keeps_known_zero_and_cache_totals_in_spans_and_logs() {
        for (usage, expected) in [
            // Synthetic partial wire retains field presence; native all-four-zero is covered separately.
            (json!({"inputOther": 0, "output": 0}), 0),
            (json!({"inputOther": 10, "output": 5, "inputCacheRead": 3, "inputCacheCreation": 2}), 15),
        ] {
            let body = format!("{}\n{}\n{}\n{}", json!({"type":"turn.prompt","input":[],"time":1000}),
                json!({"type":"context.append_loop_event","event":{"type":"step.begin","uuid":"u","step":1},"time":1100}),
                json!({"type":"usage.record","usageScope":"turn","usage":usage,"time":1190}),
                json!({"type":"context.append_loop_event","event":{"type":"step.end","uuid":"u","step":1,"finishReason":"end_turn","usage":usage},"time":1200}));
            let records = synth_to_hook_records(&parse_wire_events(&body, "s", "/tmp/work"));
            let cfg = kimi_otel::ContentLogConfig::default();
            let ctx = kimi_otel::EmissionContext { agent_product: "kimi-cli", agent_type: "kimicli", runtime_name: "kimi-cli", log: &cfg };
            let spans = kimi_otel::build_llm_spans(&records, "trace", "parent", "s", 1, &cfg, "test");
            let logs = kimi_otel::build_api_request_events(&records, "s", &ctx);
            for value in [serde_json::to_value(&spans[0]).unwrap(), serde_json::to_value(&logs[0]).unwrap()] {
                assert!(value["attributes"].as_array().unwrap().iter().any(|a|
                    a["key"] == "gen_ai.usage.input_tokens" && a["value"]["intValue"] == expected), "{value}");
            }
        }
    }

    #[test]
    fn kimi_code_tool_error_keeps_native_call_id_and_arguments() {
        let body = r#"{"type":"context.append_loop_event","event":{"type":"tool.call","toolCallId":"call-42","name":"Bash","args":{"command":"false"}},"time":1500}
{"type":"context.append_loop_event","event":{"type":"tool.result","toolCallId":"call-42","result":{"output":"exit 1","isError":true}},"time":1700}"#;
        let records = parse_wire_events(body, "s", "/tmp/work");
        assert_eq!(records.len(), 2);
        assert_eq!(records[0]["tool_call_id"], "call-42");
        assert_eq!(records[0]["tool_input"]["command"], "false");
        assert_eq!(records[1]["hook_event_name"], "PostToolUseFailure");
        assert_eq!(records[1]["tool_name"], "Bash");
        assert_eq!(records[1]["error"], "exit 1");
    }

    #[test]
    fn wire_skill_identity_survives_tool_argument_redaction_in_spans_and_logs() {
        let cfg = kimi_otel::ContentLogConfig::default();
        let ctx = kimi_otel::EmissionContext {
            agent_product: "kimi-cli",
            agent_type: "kimicli",
            runtime_name: "kimi-cli",
            log: &cfg,
        };
        let native = r#"{"type":"context.append_loop_event","event":{"type":"tool.call","toolCallId":"call-42","name":"Skill","args":{"skill":"update-config"}},"time":1500}
{"type":"context.append_loop_event","event":{"type":"tool.result","toolCallId":"call-42","result":{"output":"ok","isError":false}},"time":1700}"#;
        let legacy = r#"{"timestamp":1.5,"message":{"type":"ToolCall","payload":{"id":"call-42","function":{"name":"Skill","arguments":"{\"skill\":\"check-kimi-code-docs\"}"}}}}
{"timestamp":1.7,"message":{"type":"ToolResult","payload":{"tool_call_id":"call-42","return_value":{"is_error":false,"output":"ok"}}}}"#;

        for (body, tool_type, name, span_name) in [
            (native.to_owned(), "skill", "update-config", "skill update-config"),
            (legacy.to_owned(), "skill", "check-kimi-code-docs", "skill check-kimi-code-docs"),
            (native.replace(r#"{"skill":"update-config"}"#, "{}"), "skill", "unknown", "skill unknown"),
            (native.replace(r#""update-config""#, r#""  ""#), "skill", "unknown", "skill unknown"),
            (native.replace(r#""update-config""#, "42"), "skill", "unknown", "skill unknown"),
            (native.replace(r#""Skill""#, r#""Read""#), "builtin", "Read", "tool Read"),
        ] {
            let records = synth_to_hook_records(&parse_wire_events(&body, "s", "/tmp/work"));
            let turns = kimi_otel::build_all_turns_with_session(&records, "s", &cfg, "").unwrap();
            let span = turns.turns[0].1.spans.iter().find(|span| span.name == span_name)
                .unwrap_or_else(|| panic!("missing span {span_name} for {body}"));
            let span = serde_json::to_value(span).unwrap();
            let assert_attr = |record: &Value, key: &str, expected: &str| {
                let value = record["attributes"].as_array().unwrap().iter()
                    .find(|attr| attr["key"] == key)
                    .and_then(|attr| attr["value"]["stringValue"].as_str());
                assert_eq!(value, Some(expected), "{key} for {body}");
            };
            assert_attr(&span, "gen_ai.tool.call.arguments", "[REDACTED]");

            let logs = kimi_otel::build_correlated_events(&records, "s", &ctx, &turns, false);
            assert_eq!(logs.len(), 2);
            for (log, event_name) in logs.iter().zip(["tool_result", "tool_decision"]) {
                let log = serde_json::to_value(log).unwrap();
                assert_eq!(log["traceId"], span["traceId"]);
                assert_eq!(log["spanId"], span["spanId"]);
                assert_attr(&log, "event.name", event_name);
                if event_name == "tool_result" {
                    assert_attr(&log, "gen_ai.tool.call.arguments", "[REDACTED]");
                }
                assert_attr(&log, "agentic.tool.type", tool_type);
                assert_attr(&log, "agentic.tool.name", name);
                assert_attr(&log, "gen_ai.tool.call.id", "call-42");
            }
            assert_attr(&span, "agentic.tool.type", tool_type);
            assert_attr(&span, "agentic.tool.name", name);
            assert_attr(&span, "gen_ai.tool.call.id", "call-42");
        }
    }

    #[test]
    fn kimi_code_partial_usage_keeps_known_zero_without_fabricated_input_or_total() {
        let body = r#"{"type":"context.append_loop_event","event":{"type":"step.begin","uuid":"p","step":1},"time":1000}
{"type":"usage.record","usageScope":"turn","usage":{"output":0},"time":1190}
{"type":"context.append_loop_event","event":{"type":"step.end","uuid":"p","step":1,"usage":{"output":0}},"time":1200}"#;
        let records = synth_to_hook_records(&parse_wire_events(body, "s", "/tmp/work"));
        let cfg = kimi_otel::ContentLogConfig::default();
        let ctx = kimi_otel::EmissionContext { agent_product: "kimi-cli", agent_type: "kimicli", runtime_name: "kimi-cli", log: &cfg };
        let spans = kimi_otel::build_llm_spans(&records, "t", "p", "s", 1, &cfg, "");
        let logs = kimi_otel::build_api_request_events(&records, "s", &ctx);
        for value in [serde_json::to_value(&spans[0]).unwrap(), serde_json::to_value(&logs[0]).unwrap()] {
            let attrs = value["attributes"].as_array().unwrap();
            assert!(attrs.iter().any(|a| a["key"] == "gen_ai.usage.output_tokens" && a["value"]["intValue"] == 0));
            assert!(attrs.iter().any(|a| a["key"] == "agentic.usage.input_tokens.missing" && a["value"]["stringValue"] == "true"));
            assert!(!attrs.iter().any(|a| a["key"] == "gen_ai.usage.input_tokens" || a["key"] == "gen_ai.usage.total_tokens"));
            assert!(attrs.iter().any(|a| a["key"] == "gen_ai.system" && a["value"]["stringValue"] == "unknown"));
            assert!(attrs.iter().any(|a| a["key"] == "gen_ai.provider.name" && a["value"]["stringValue"] == "unknown"));
        }
    }

    #[test]
    fn kimi_code_official_wire_preserves_turn_text_usage_and_millisecond_time() {
        let body = include_str!("../../tests/fixtures/kimi-code-2.1.1/main.wire.jsonl");
        let records = parse_wire_events(body, "code-session", "/tmp/work");
        let turn = records.iter().find(|r| r["hook_event_name"] == "TurnBegin")
            .expect("Kimi Code turn.prompt must survive normalization");
        assert_eq!(turn["user_input"], "hi");
        let step = records.iter().find(|r| r["hook_event_name"] == "LLMStep")
            .expect("Kimi Code step.end must produce an LLM call");
        assert_eq!(step["output_text"], "hello");
        assert_eq!(step["model"], "kimi-k2");
        assert_eq!(step["token_usage"]["input_other"], 10);
        assert_eq!(step["token_usage"]["output"], 5);
        assert_eq!(step["_start_ts"], 1779256800.1);
        assert_eq!(step["_end_ts"], 1779256800.3);
    }

    #[test]
    fn kimi_code_usage_receipt_identifies_only_its_own_call_without_llm_request() {
        // 2.6.3 emits one turn-scoped receipt per completed step, before step.end.
        let body = r#"{"type":"turn.prompt","agentId":"main","input":[],"time":1000}
{"type":"context.append_loop_event","agentId":"main","event":{"type":"step.begin","uuid":"s1","step":1},"time":1100}
{"type":"usage.record","agentId":"main","model":" gateway/route/model-a ","usageScope":"turn","usage":{"inputOther":10,"output":5,"inputCacheRead":3,"inputCacheCreation":0},"time":1150}
{"type":"llm.request","agentId":"main","model":"unknown","kind":"loop","time":1175}
{"type":"context.append_loop_event","agentId":"main","event":{"type":"step.end","uuid":"s1","finishReason":"end_turn"},"time":1200}
{"type":"context.append_loop_event","agentId":"main","event":{"type":"step.begin","uuid":"s2","step":2},"time":1300}
{"type":"usage.record","agentId":"main","model":"other/route/model-b","usageScope":"turn","usage":{"inputOther":20,"output":7},"time":1350}
{"type":"context.append_loop_event","agentId":"main","event":{"type":"step.end","uuid":"s2","finishReason":"end_turn"},"time":1400}
{"type":"context.append_loop_event","agentId":"main","event":{"type":"step.begin","uuid":"s3","step":3},"time":1500}
{"type":"usage.record","agentId":"subagent","model":"foreign/model","usageScope":"turn","usage":{"inputOther":99,"output":99},"time":1550}
{"type":"context.append_loop_event","agentId":"main","event":{"type":"step.end","uuid":"s3","finishReason":"error"},"time":1600}
{"type":"turn.ended","agentId":"main","time":1700}"#;
        let records = synth_to_hook_records(&parse_wire_events(body, "s", "/fixture"));
        let cfg = kimi_otel::ContentLogConfig::default();
        let ctx = kimi_otel::EmissionContext { agent_product: "kimi-cli", agent_type: "kimicli", runtime_name: "kimi-cli", log: &cfg };
        let spans = kimi_otel::build_llm_spans(&records, "t", "p", "s", 1, &cfg, "wrong/configured-model");
        let logs = kimi_otel::build_api_request_events(&records, "s", &ctx);
        let attrs = |value: Value| -> std::collections::HashMap<String, Value> {
            value["attributes"].as_array().unwrap().iter()
                .map(|a| (a["key"].as_str().unwrap().to_owned(), a["value"].clone())).collect()
        };
        assert_eq!(spans.len(), 3);
        assert_eq!(logs.len(), 3);
        for (i, (model, provider, input)) in [
            ("gateway/route/model-a", "gateway", Some(13)),
            ("other/route/model-b", "other", Some(20)),
            ("unknown", "unknown", None),
        ].into_iter().enumerate() {
            for fields in [attrs(json!(spans[i])), attrs(json!(logs[i]))] {
                assert_eq!(fields["gen_ai.request.model"]["stringValue"], model);
                assert_eq!(fields["gen_ai.provider.name"]["stringValue"], provider);
                assert_eq!(fields["gen_ai.system"]["stringValue"], provider);
                assert_eq!(fields.get("gen_ai.usage.input_tokens").and_then(|value| value["intValue"].as_i64()), input);
            }
        }
        let metrics = serde_json::to_string(&kimi_otel::build_llm_metrics(&records, "s", &ctx)).unwrap();
        assert!(metrics.contains("gateway/route/model-a"));
        assert!(metrics.contains("other/route/model-b"));
        assert!(!metrics.contains("foreign/model"));
    }

    #[test]
    fn rebuild_pairs_call_and_result_success() {
        let wire = write_temp_wire(&[
            r#"{"type":"metadata","protocol_version":"1.9"}"#,
            r#"{"timestamp":1.0,"message":{"type":"TurnBegin","payload":{}}}"#,
            r#"{"timestamp":2.0,"message":{"type":"ToolCall","payload":{"id":"fc-1","function":{"name":"Shell","arguments":"{\"cmd\":\"ls\"}"}}}}"#,
            r#"{"timestamp":3.0,"message":{"type":"ToolResult","payload":{"tool_call_id":"fc-1","return_value":{"is_error":false,"output":"file1\nfile2"}}}}"#,
            r#"{"timestamp":4.0,"message":{"type":"TurnEnd","payload":{}}}"#,
        ]);
        let synth = rebuild_hook_log_from_wire(&wire, "sess-1", "/tmp").unwrap();
        let _ = std::fs::remove_file(&wire);

        // Sprint 2 起额外合成 TurnBegin/TurnEnd，事件序列：
        // TurnBegin → PreToolUse → PostToolUse → TurnEnd
        let names: Vec<&str> = synth
            .iter()
            .filter_map(|v| v.get("hook_event_name").and_then(|s| s.as_str()))
            .collect();
        assert_eq!(
            names,
            vec!["TurnBegin", "PreToolUse", "PostToolUse", "TurnEnd"]
        );
        // 找出 Pre/Post 两条做字段断言（顺序固定，但用名称查更稳）
        let pre = synth
            .iter()
            .find(|v| v.get("hook_event_name").and_then(|s| s.as_str()) == Some("PreToolUse"))
            .unwrap();
        let post = synth
            .iter()
            .find(|v| v.get("hook_event_name").and_then(|s| s.as_str()) == Some("PostToolUse"))
            .unwrap();
        assert_eq!(
            pre.get("tool_call_id").and_then(|v| v.as_str()),
            Some("fc-1")
        );
        assert_eq!(pre.get("tool_name").and_then(|v| v.as_str()), Some("Shell"));
        assert_eq!(
            post.get("tool_output").and_then(|v| v.as_str()),
            Some("file1\nfile2")
        );
    }

    #[test]
    fn rebuild_emits_failure_event_on_is_error() {
        let wire = write_temp_wire(&[
            r#"{"type":"metadata","protocol_version":"1.9"}"#,
            r#"{"timestamp":2.0,"message":{"type":"ToolCall","payload":{"id":"fc-2","function":{"name":"ReadFile","arguments":""}}}}"#,
            r#"{"timestamp":3.0,"message":{"type":"ToolResult","payload":{"tool_call_id":"fc-2","return_value":{"is_error":true,"output":"","message":"file missing"}}}}"#,
        ]);
        let synth = rebuild_hook_log_from_wire(&wire, "s", "/c").unwrap();
        let _ = std::fs::remove_file(&wire);

        assert_eq!(synth.len(), 2);
        assert_eq!(
            synth[1].get("hook_event_name").and_then(|v| v.as_str()),
            Some("PostToolUseFailure")
        );
        assert_eq!(
            synth[1].get("error").and_then(|v| v.as_str()),
            Some("file missing")
        );
        // 空 arguments 应退化为 {}，不是丢字段
        assert!(synth[0].get("tool_input").is_some());
    }

    #[test]
    fn rebuild_handles_orphan_result_with_unknown_tool_name() {
        // 只有 ToolResult，没有匹配的 ToolCall（边界场景：wire.jsonl 部分丢失）
        let wire = write_temp_wire(&[
            r#"{"timestamp":3.0,"message":{"type":"ToolResult","payload":{"tool_call_id":"fc-orphan","return_value":{"is_error":false,"output":"x"}}}}"#,
        ]);
        let synth = rebuild_hook_log_from_wire(&wire, "s", "/c").unwrap();
        let _ = std::fs::remove_file(&wire);

        assert_eq!(synth.len(), 1);
        assert_eq!(
            synth[0].get("tool_name").and_then(|v| v.as_str()),
            Some("unknown")
        );
    }

    /// Sprint 2: TurnBegin/TurnEnd 必须被合成且 user_input 数组被拼成字符串。
    #[test]
    fn rebuild_emits_turn_begin_end_with_user_input_text() {
        let wire = write_temp_wire(&[
            r#"{"type":"metadata","protocol_version":"1.9"}"#,
            r#"{"timestamp":1.0,"message":{"type":"TurnBegin","payload":{"user_input":[{"type":"text","text":"hello"}]}}}"#,
            r#"{"timestamp":2.0,"message":{"type":"TurnEnd","payload":{}}}"#,
        ]);
        let synth = rebuild_hook_log_from_wire(&wire, "sess-t", "/cwd").unwrap();
        let _ = std::fs::remove_file(&wire);

        assert_eq!(synth.len(), 2);
        assert_eq!(
            synth[0].get("hook_event_name").and_then(|v| v.as_str()),
            Some("TurnBegin")
        );
        assert_eq!(
            synth[0].get("user_input").and_then(|v| v.as_str()),
            Some("hello")
        );
        assert_eq!(
            synth[1].get("hook_event_name").and_then(|v| v.as_str()),
            Some("TurnEnd")
        );
    }

    /// Sprint 2: StepBegin + ContentPart(think/text) + StatusUpdate 应聚合成
    /// **单条** LLMStep，且 token_usage / message_id / thinking / output_text 都正确填充。
    #[test]
    fn rebuild_aggregates_step_into_single_llmstep_event() {
        let wire = write_temp_wire(&[
            r#"{"type":"metadata","protocol_version":"1.9"}"#,
            r#"{"timestamp":1.0,"message":{"type":"TurnBegin","payload":{"user_input":[{"type":"text","text":"hi"}]}}}"#,
            r#"{"timestamp":2.0,"message":{"type":"StepBegin","payload":{"n":1}}}"#,
            r#"{"timestamp":3.0,"message":{"type":"ContentPart","payload":{"type":"think","think":"reasoning here"}}}"#,
            r#"{"timestamp":4.0,"message":{"type":"ContentPart","payload":{"type":"text","text":"answer A"}}}"#,
            r#"{"timestamp":4.5,"message":{"type":"ContentPart","payload":{"type":"text","text":" + answer B"}}}"#,
            r#"{"timestamp":5.0,"message":{"type":"StatusUpdate","payload":{"token_usage":{"input_other":100,"output":20,"input_cache_read":0,"input_cache_creation":0},"message_id":"msg-xyz"}}}"#,
            r#"{"timestamp":6.0,"message":{"type":"TurnEnd","payload":{}}}"#,
        ]);
        let synth = rebuild_hook_log_from_wire(&wire, "s", "/c").unwrap();
        let _ = std::fs::remove_file(&wire);

        // 期望事件序列：TurnBegin / LLMStep / TurnEnd
        let names: Vec<&str> = synth
            .iter()
            .filter_map(|v| v.get("hook_event_name").and_then(|s| s.as_str()))
            .collect();
        assert_eq!(names, vec!["TurnBegin", "LLMStep", "TurnEnd"]);

        let step = &synth[1];
        assert_eq!(step.get("step_n").and_then(|v| v.as_i64()), Some(1));
        assert_eq!(
            step.get("thinking").and_then(|v| v.as_str()),
            Some("reasoning here")
        );
        // 多个 text ContentPart 应被串联
        assert_eq!(
            step.get("output_text").and_then(|v| v.as_str()),
            Some("answer A + answer B")
        );
        assert_eq!(
            step.get("message_id").and_then(|v| v.as_str()),
            Some("msg-xyz")
        );
        assert_eq!(
            step.get("token_usage")
                .and_then(|tu| tu.get("output"))
                .and_then(|v| v.as_i64()),
            Some(20)
        );
        // _start_ts 来自 StepBegin（2.0），_end_ts 来自 StatusUpdate（5.0）
        assert_eq!(step.get("_start_ts").and_then(|v| v.as_f64()), Some(2.0));
        assert_eq!(step.get("_end_ts").and_then(|v| v.as_f64()), Some(5.0));
    }

    /// Sprint 2: 同一 step 内出现 ToolCall 且 StatusUpdate 缺失时，LLMStep 在流末尾
    /// 兜底 flush，避免消息内容被吞掉。
    #[test]
    fn rebuild_force_flushes_step_when_toolcall_arrives_without_status_update() {
        let wire = write_temp_wire(&[
            r#"{"type":"metadata","protocol_version":"1.9"}"#,
            r#"{"timestamp":1.0,"message":{"type":"StepBegin","payload":{"n":1}}}"#,
            r#"{"timestamp":2.0,"message":{"type":"ContentPart","payload":{"type":"text","text":"will call tool"}}}"#,
            r#"{"timestamp":3.0,"message":{"type":"ToolCall","payload":{"id":"fc-x","function":{"name":"Shell","arguments":"{\"cmd\":\"ls\"}"}}}}"#,
            r#"{"timestamp":4.0,"message":{"type":"ToolResult","payload":{"tool_call_id":"fc-x","return_value":{"is_error":false,"output":"ok"}}}}"#,
        ]);
        let synth = rebuild_hook_log_from_wire(&wire, "s", "/c").unwrap();
        let _ = std::fs::remove_file(&wire);

        let names: Vec<&str> = synth
            .iter()
            .filter_map(|v| v.get("hook_event_name").and_then(|s| s.as_str()))
            .collect();
        // StatusUpdate 缺失时，ToolCall 不提前 flush（正常路径需要等待 StatusUpdate
        // 补齐 token_usage），最终在 finish() 兜底输出 LLMStep。
        assert_eq!(names, vec!["PreToolUse", "PostToolUse", "LLMStep"]);
        // flush 时 token_usage 缺失 → 输出 Null（降级而非崩溃）
        assert!(synth[2]
            .get("token_usage")
            .map(|v| v.is_null())
            .unwrap_or(false));
    }

    /// Sprint 2: SubagentEvent 应被合成且 nested_events 中包含递归 parse 出的子事件，
    /// 同时 parent_tool_call_id / agent_id / subagent_type 不丢字段。
    #[test]
    fn rebuild_emits_subagent_event_with_nested_events() {
        let wire = write_temp_wire(&[
            r#"{"type":"metadata","protocol_version":"1.9"}"#,
            r#"{"timestamp":10.0,"message":{"type":"SubagentEvent","payload":{"parent_tool_call_id":"fc-parent","agent_id":"agt-001","subagent_type":"coder","event":{"type":"TurnBegin","payload":{"user_input":"do work"}}}}}"#,
            r#"{"timestamp":11.0,"message":{"type":"SubagentEvent","payload":{"parent_tool_call_id":"fc-parent","agent_id":"agt-001","subagent_type":"coder","event":{"type":"ToolCall","payload":{"id":"fc-sub","function":{"name":"Shell","arguments":"{\"cmd\":\"uname\"}"}}}}}}"#,
            r#"{"timestamp":12.0,"message":{"type":"SubagentEvent","payload":{"parent_tool_call_id":"fc-parent","agent_id":"agt-001","subagent_type":"coder","event":{"type":"ToolResult","payload":{"tool_call_id":"fc-sub","return_value":{"is_error":false,"output":"Darwin"}}}}}}"#,
        ]);
        let synth = rebuild_hook_log_from_wire(&wire, "s", "/c").unwrap();
        let _ = std::fs::remove_file(&wire);

        // 同一 parent 的 SubagentEvent 会被合并，确保子 parser 的 ToolCall/ToolResult
        // 状态连续，后续 span 构建可以正确配对。
        let sub_events: Vec<&Value> = synth
            .iter()
            .filter(|v| v.get("hook_event_name").and_then(|s| s.as_str()) == Some("SubagentEvent"))
            .collect();
        assert_eq!(sub_events.len(), 1);

        let nested0 = sub_events[0]
            .get("nested_events")
            .and_then(|v| v.as_array())
            .expect("nested_events 应为数组");
        assert_eq!(nested0.len(), 3);
        assert_eq!(
            nested0[0].get("hook_event_name").and_then(|v| v.as_str()),
            Some("TurnBegin")
        );
        assert_eq!(
            nested0[0].get("user_input").and_then(|v| v.as_str()),
            Some("do work")
        );

        assert_eq!(
            nested0[1].get("hook_event_name").and_then(|v| v.as_str()),
            Some("PreToolUse")
        );
        assert_eq!(
            nested0[1].get("tool_name").and_then(|v| v.as_str()),
            Some("Shell")
        );
        assert_eq!(
            nested0[2].get("hook_event_name").and_then(|v| v.as_str()),
            Some("PostToolUse")
        );

        // parent_tool_call_id / agent_id / subagent_type 不丢
        assert_eq!(
            sub_events[0]
                .get("parent_tool_call_id")
                .and_then(|v| v.as_str()),
            Some("fc-parent")
        );
        assert_eq!(
            sub_events[0].get("agent_id").and_then(|v| v.as_str()),
            Some("agt-001")
        );
        assert_eq!(
            sub_events[0].get("subagent_type").and_then(|v| v.as_str()),
            Some("coder")
        );
    }

    /// v0.3: merge_with_realtime_state 把 state.json 中三类实时缓存合成为
    /// _synthetic 事件注入 synth，且 _source_timestamp 单位为秒（ts_ns / 1e9）。
    #[test]
    fn merge_with_realtime_state_emits_three_kinds_of_synthetic_events() {
        use crate::common::state::{
            SessionState, SubagentStartEntry, SubagentStopEntry, UserPromptEntry,
        };

        let sess = SessionState {
            wire_fingerprint: Some("100:5".to_string()),
            last_seen_ns: "1700000000000000000".to_string(),
            user_prompts: vec![UserPromptEntry {
                ts_ns: "1700000000000000000".to_string(),
                prompt: "raw user".to_string(),
                cwd: "/work".to_string(),
            }],
            subagent_starts: vec![SubagentStartEntry {
                ts_ns: "1700000001000000000".to_string(),
                agent_name: "coder".to_string(),
                prompt: "go".to_string(),
                cwd: String::new(),
            }],
            subagent_stops: vec![SubagentStopEntry {
                ts_ns: "1700000002500000000".to_string(),
                agent_name: "coder".to_string(),
                response: "done".to_string(),
                cwd: String::new(),
            }],
        };

        let mut synth: Vec<Value> = Vec::new();
        super::merge_with_realtime_state(&mut synth, &sess, "sess-merge", "/fallback-cwd");

        assert_eq!(synth.len(), 3);
        let names: Vec<&str> = synth
            .iter()
            .filter_map(|v| v.get("hook_event_name").and_then(|s| s.as_str()))
            .collect();
        assert!(names.contains(&"UserPromptSubmit"));
        assert!(names.contains(&"SubagentStart"));
        assert!(names.contains(&"SubagentStop"));

        let prompt_event = synth
            .iter()
            .find(|v| v.get("hook_event_name").and_then(|s| s.as_str()) == Some("UserPromptSubmit"))
            .unwrap();
        assert_eq!(prompt_event["prompt"].as_str(), Some("raw user"));
        assert_eq!(prompt_event["cwd"].as_str(), Some("/work"));
        assert_eq!(prompt_event["_synthetic"].as_bool(), Some(true));
        assert_eq!(prompt_event["_source"].as_str(), Some("kimihooks_state"));
        // _source_timestamp 应为 1.7e9（秒）
        let ts = prompt_event["_source_timestamp"].as_f64().unwrap();
        assert!((ts - 1_700_000_000.0).abs() < 1e-3, "ts={}", ts);

        // subagent_start.cwd 为空时回落到调用方传入的 fallback cwd
        let start_event = synth
            .iter()
            .find(|v| v.get("hook_event_name").and_then(|s| s.as_str()) == Some("SubagentStart"))
            .unwrap();
        assert_eq!(start_event["cwd"].as_str(), Some("/fallback-cwd"));
        assert_eq!(start_event["agent_name"].as_str(), Some("coder"));

        let stop_event = synth
            .iter()
            .find(|v| v.get("hook_event_name").and_then(|s| s.as_str()) == Some("SubagentStop"))
            .unwrap();
        assert_eq!(stop_event["response"].as_str(), Some("done"));
    }
}
