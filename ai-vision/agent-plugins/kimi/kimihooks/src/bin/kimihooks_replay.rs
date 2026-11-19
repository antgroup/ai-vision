//! `kimihooks-replay`: 离线把 wire.jsonl 转换成 OTLP JSONL（一行一个
//! `ExportTraceServiceRequest`）。
//!
//! 消费 `wire.jsonl`（kimi-cli 原生 wire 协议），同时输出 3 类 OTLP 信号：
//!   - traces  → C1(Turn) + C2(LLM) + C3(Tool) + C6(Subagent) spans
//!   - logs    → tool_result events（events.yaml single-event model）
//!   - metrics → agent.tool_call.count + aivision.agentic.up + aivision.agentic.exec_error
//!
//! 自动扫描关联的 `context.jsonl`，注入 LLM input messages（C2/C9）。
//!
//! 用法（与 cchooks-replay 接口对齐，便于 Makefile 复用）：
//!
//! ```bash
//! kimihooks-replay <wire.jsonl> \
//!   --otel <spans.jsonl> \
//!   --logs <logs.jsonl> \
//!   --metrics <metrics.jsonl>
//! ```
//!
//! 行为：
//! - 每类信号各自写一个 OTLP ExportRequest（一行 JSON）到对应文件。
//! - 三个 --otel/--logs/--metrics 参数互相独立，未指定的信号不会产出文件。
//! - 找不到任何配对时仍写文件，但 spans/logs 为空、metrics 仅含 aivision.agentic.up=1。
//! - 退出码：0=成功，1=参数错误，2=IO/解析失败。

use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process;

use agentic_otel_common::{
    otlp::{wrap_logs_export, wrap_metrics_export, wrap_trace_export},
    ExportContext,
};
use serde_json::Value;

use kimihooks::common::edition::EditionConfig;
use kimihooks::common::otel::HookRecord;
use kimihooks::common::http_export;
use kimihooks::common::logger::Logger;
use kimihooks::common::otel as kimi_otel;
use kimihooks::hooks::stop::rebuild_hook_log_from_wire;

const VERSION: &str = env!("CARGO_PKG_VERSION");

/// `agentic.runtime.name`，必须命中 `valid_runtime_names` 白名单。
/// 注意：这与 `ant.agent.product`（OTel resource attr）不同——后者由 EditionConfig 解析，
/// 允许 env override；runtime.name 是 SDK 自报字段，固定为 "kimi-cli"。
const RUNTIME_NAME: &str = "kimi-cli";
const SDK_NAME: &str = "kimihooks";
/// `ant.agent.product` 兜底：当 EditionConfig 解析不到（common edition 且无 env）时使用。
const FALLBACK_AGENT_PRODUCT: &str = "kimi-cli";

#[derive(Debug, Default)]
struct Args {
    collection_mode: Option<String>,
    /// wire.jsonl 路径。
    log_path: Option<PathBuf>,
    /// 输出 OTLP traces JSONL 路径。
    otel_out: Option<PathBuf>,
    /// 输出 OTLP logs (events) JSONL 路径。
    logs_out: Option<PathBuf>,
    /// 输出 OTLP metrics JSONL 路径。
    metrics_out: Option<PathBuf>,
    /// report.config.toml 路径（与 cchooks 统一）。
    config_path: Option<PathBuf>,
    /// Langfuse profile 名称（对应 config 中 [profiles.xxx]）。
    profile: Option<String>,
    /// 是否将 span 时间戳偏移到当前时刻（用于 report-shift）。
    shift: bool,
}

fn print_usage_and_exit(code: i32) -> ! {
    eprintln!("  --collection-mode full|privacy  Explicit replay mode (default: full; shell mode ignored)");
    eprintln!(
        "kimihooks-replay {VERSION}\n\n\
         USAGE:\n  \
         kimihooks-replay <wire.jsonl> [--otel SPANS.jsonl] [--logs LOGS.jsonl] [--metrics METRICS.jsonl]\n\n\
         INPUT: wire_*.jsonl（kimi-cli 原生 wire 协议）。\n\n\
         OPTIONS:\n  \
         --config <path>     report.config.toml 路径（与 cchooks 统一，覆盖 EditionConfig）\n  \
         --profile <name>    Langfuse profile 名称（默认使用 config 中 default_profile）\n  \
         --shift             将 span 时间戳偏移到当前时刻（report-shift 用）\n\n\
         至少需要指定 --otel / --logs / --metrics 中的一个输出。"
    );
    process::exit(code);
}

fn parse_args() -> Args {
    let raw: Vec<String> = std::env::args().collect();
    let mut args = Args::default();
    let mut i = 1;
    while i < raw.len() {
        match raw[i].as_str() {
            "--collection-mode" => {
                i += 1;
                let mode = raw.get(i).map(|s| s.trim().to_ascii_lowercase()).unwrap_or_default();
                if mode != "full" && mode != "privacy" {
                    eprintln!("--collection-mode must be full or privacy");
                    process::exit(1);
                }
                args.collection_mode = Some(mode);
            }
            "-h" | "--help" => print_usage_and_exit(0),
            "--otel" => {
                i += 1;
                if i >= raw.len() {
                    eprintln!("--otel 需要路径参数");
                    print_usage_and_exit(1);
                }
                args.otel_out = Some(PathBuf::from(&raw[i]));
            }
            "--logs" => {
                i += 1;
                if i >= raw.len() {
                    eprintln!("--logs 需要路径参数");
                    print_usage_and_exit(1);
                }
                args.logs_out = Some(PathBuf::from(&raw[i]));
            }
            "--metrics" => {
                i += 1;
                if i >= raw.len() {
                    eprintln!("--metrics 需要路径参数");
                    print_usage_and_exit(1);
                }
                args.metrics_out = Some(PathBuf::from(&raw[i]));
            }
            "--config" => {
                i += 1;
                if i >= raw.len() {
                    eprintln!("--config 需要路径参数");
                    print_usage_and_exit(1);
                }
                args.config_path = Some(PathBuf::from(&raw[i]));
            }
            "--profile" => {
                i += 1;
                if i >= raw.len() {
                    eprintln!("--profile 需要名称参数");
                    print_usage_and_exit(1);
                }
                args.profile = Some(raw[i].clone());
            }
            "--shift" => {
                args.shift = true;
            }
            other => {
                if other.starts_with("--") {
                    eprintln!("未知参数: {}", other);
                    print_usage_and_exit(1);
                }
                if args.log_path.is_some() {
                    eprintln!("已提供输入路径，多余参数: {}", other);
                    print_usage_and_exit(1);
                }
                args.log_path = Some(PathBuf::from(other));
            }
        }
        i += 1;
    }
    args
}

fn main() {
    let args = parse_args();
    let mode = args.collection_mode.as_deref().unwrap_or("full");
    std::env::set_var("AIVISION_COLLECTION_MODE", mode);
    std::env::set_var("KIMI_COLLECTION_MODE", mode);

    if args.otel_out.is_none() && args.logs_out.is_none() && args.metrics_out.is_none() {
        eprintln!("必须至少指定 --otel / --logs / --metrics 中的一个");
        print_usage_and_exit(1);
    }

    let path = match args.log_path.as_ref() {
        Some(p) => p,
        None => {
            eprintln!("必须提供 wire.jsonl 路径");
            print_usage_and_exit(1);
        }
    };

    // 启动横幅仅报告 edition 和配置状态。
    let edition_cfg = EditionConfig::resolve();
    eprintln!("kimihooks-replay {} | {}", VERSION, edition_cfg.human_summary());

    // wire.jsonl 输入：与 Stop hook 走同一条 rebuild 路径。
    let session_id = infer_session_from_wire_filename(path)
        .or_else(|| infer_session_from_records(path))
        .unwrap_or_else(|| "unknown-session".to_string());
    let cwd = std::env::current_dir()
        .map(|p| p.to_string_lossy().to_string())
        .unwrap_or_else(|_| "/tmp".to_string());
    let mut synth = match rebuild_hook_log_from_wire(path, &session_id, &cwd) {
        Ok(v) => v,
        Err(e) => {
            eprintln!("解析 wire.jsonl {} 失败: {}", path.display(), e);
            process::exit(2);
        }
    };

    // 自动扫描关联 context.jsonl 并注入 LLM input messages（C2/C9）
    if let Some(ctx_path) = find_companion_context(path) {
        eprintln!(
            "kimihooks-replay: enriching from context.jsonl -> {}",
            ctx_path.display()
        );
        kimihooks::common::context_parser::enrich_with_context(&mut synth, &ctx_path);
    } else {
        eprintln!("kimihooks-replay: no companion context.jsonl found");
    }

    let records: Vec<HookRecord> = synth
        .iter()
        .map(|v| {
            // 时间戳解析优先级：
            // 1. recorded_at_ns（纳秒字符串，hook_log 原生格式）
            // 2. _source_timestamp（秒浮点数，wire.jsonl 合成事件）→ ×1e9 转纳秒
            // 3. 退化为 "0"
            let recorded_at_ns = v
                .get("recorded_at_ns")
                .and_then(|x| x.as_str())
                .filter(|s| *s != "0")
                .map(|s| s.to_string())
                .unwrap_or_else(|| {
                    v.get("_source_timestamp")
                        .and_then(|x| x.as_f64())
                        .filter(|ts| *ts > 0.0)
                        .map(|ts| format!("{}", (ts * 1_000_000_000.0) as u128))
                        .unwrap_or_else(|| "0".to_string())
                });
            HookRecord {
                recorded_at_ns,
                payload: v.clone(),
            }
        })
        .collect();

    // ant.agent.product / ant.agent.type 全部走 EditionConfig（env > builtin > 兜底）。
    // 这样 `AIVISION_AGENT_PRODUCT=foo kimihooks-replay …` 能直接覆盖 OTel resource。
    let agent_product: &str = edition_cfg
        .agent_product
        .as_deref()
        .filter(|s| !s.is_empty())
        .unwrap_or(FALLBACK_AGENT_PRODUCT);
    let agent_type: &str = edition_cfg.agent_type.as_str();

    let agent_env = agentic_otel_common::env_or_default(
        "KIMIHOOKS_AGENT_ENV", "AIVISION_AGENT_ENV", "Default",
    );
    let agent_scene = agentic_otel_common::env_or_default(
        "KIMIHOOKS_AGENT_SCENE", "AIVISION_AGENT_SCENE", "",
    );
    let ctx = ExportContext {
        agent_product,
        agent_type,
        sdk_name: SDK_NAME,
        build_version: VERSION,
        agent_env: &agent_env,
        agent_scene: &agent_scene,
        collection_mode: mode,
        emit_meta_trigger: false,
    };
    let log_cfg = kimi_otel::ContentLogConfig::from(&edition_cfg);
    let emit_ctx = kimi_otel::EmissionContext {
        agent_product,
        agent_type,
        runtime_name: RUNTIME_NAME,
        log: &log_cfg,
    };

    // replay 用 debug=true 直接打到 stderr；日志文件在 ~/.kimi/observ/logs/ 下与 hook 共用。
    let log_path = kimihooks::common::dirs::log_dir().join("kimihooks-replay.log");
    let logger = Logger::new(log_path, true);

    // ── traces ──
    // 多 turn 拆分：按 TurnBegin/TurnEnd 边界拆分 records，每个 turn 独立 trace。
    // 所有 turn 的 spans 合并到同一个 ExportTraceServiceRequest（共享 resource）。
    let turn_result = kimi_otel::build_all_turns_with_session(&records, &session_id, &log_cfg, "unknown");
    let mut traces_request: Option<Value> = None;
    if let Some(out) = args.otel_out.as_ref() {
        let mut all_spans: Vec<kimi_otel::OtelSpanExport> = Vec::new();
        let mut trace_info: Vec<String> = Vec::new();
        match turn_result.as_ref() {
            Some(result) => {
                for (trace_id, turn) in &result.turns {
                    let root_id = &turn.root_span_id;
                    trace_info.push(format!(
                        "trace_id={} root={} spans={}",
                        trace_id,
                        if root_id.is_empty() { "<flat>" } else { root_id },
                        turn.spans.len()
                    ));
                }
                for (_trace_id, turn) in &result.turns {
                    all_spans.extend(turn.spans.clone());
                }
            }
            None => {}
        }
        let n_spans = all_spans.len();
        let request = kimi_otel::finalize_export_resource(wrap_trace_export(all_spans, &ctx), &edition_cfg.agent_instance);
        write_export_jsonl(out, &request);
        eprintln!(
            "kimihooks-replay: traces -> {} spans={} turns={}",
            out.display(),
            n_spans,
            trace_info.len(),
        );
        for info in &trace_info {
            eprintln!("  turn: {}", info);
        }
        traces_request = Some(request);
    }

    // ── logs (events) ──
    let mut logs_request: Option<Value> = None;
    if let Some(out) = args.logs_out.as_ref() {
        let events = turn_result.as_ref().map(|result| kimi_otel::build_correlated_events(&records, &session_id, &emit_ctx, result, false)).unwrap_or_default();
        let n_events = events.len();
        let request = kimi_otel::finalize_export_resource(wrap_logs_export(events, &ctx), &edition_cfg.agent_instance);
        write_export_jsonl(out, &request);
        eprintln!(
            "kimihooks-replay: logs   -> {} events={}",
            out.display(),
            n_events
        );
        logs_request = Some(request);
    }

    // ── metrics ──
    let mut metrics_request: Option<Value> = None;
    if let Some(out) = args.metrics_out.as_ref() {
        let mut all_metrics = kimi_otel::build_tool_metrics(&records, &session_id, &emit_ctx);
        all_metrics.extend(kimi_otel::build_llm_metrics(&records, &session_id, &emit_ctx));
        all_metrics.extend(kimi_otel::build_tool_duration_metrics(&records, &session_id, &emit_ctx));
        all_metrics.extend(kimi_otel::build_session_token_metrics(&records, &session_id, &emit_ctx));
        let n_metrics = all_metrics.len();
        let request = kimi_otel::finalize_export_resource(wrap_metrics_export(all_metrics, &ctx), &edition_cfg.agent_instance);
        write_export_jsonl(out, &request);
        eprintln!(
            "kimihooks-replay: metrics -> {} instruments={}",
            out.display(),
            n_metrics
        );
        metrics_request = Some(request);
    }

    // ── Timestamp shift（--shift）──
    // 将所有 span 的 startTimeUnixNano / endTimeUnixNano 偏移到当前时刻，
    // 保持各 span 之间的相对时序不变。与 cchooks shift_timestamps 完全对齐。
    if args.shift {
        let mut all_requests: Vec<&mut Value> = Vec::new();
        if let Some(ref mut req) = traces_request {
            all_requests.push(req);
        }
        if let Err(e) = shift_timestamps(&mut all_requests) {
            eprintln!("kimihooks-replay: shift warning: {}", e);
        }
    }

    // ── HTTP 上报 ──
    // 当 --config 指定时，用 report.config.toml 的 host/pk/sk 覆盖 EditionConfig（与 cchooks 统一）。
    // 否则走原有 EditionConfig（编译时内嵌 + 环境变量）。
    // `KIMI_DISABLE_HTTP=true` 一键禁用。fail-open，永不影响退出码。
    let effective_cfg = if let Some(config_path) = args.config_path.as_ref() {
        match kimihooks::common::report_config::ReportConfig::load(config_path) {
            Ok(config) => {
                let profile_name = args.profile.as_deref();
                match config.resolve_profile(profile_name) {
                    Ok(resolved) => {
                        eprintln!(
                            "kimihooks-replay: using config {} (profile={}, host={})",
                            config_path.display(),
                            profile_name.unwrap_or("default"),
                            resolved.host,
                        );
                        let mut cfg = edition_cfg.clone();
                        cfg.traces_endpoint = Some(resolve_traces_url(&resolved.host));
                        cfg.public_key = Some(resolved.public_key);
                        cfg.secret_key = Some(resolved.secret_key);
                        cfg
                    }
                    Err(e) => {
                        eprintln!("kimihooks-replay: config profile error: {}", e);
                        edition_cfg
                    }
                }
            }
            Err(e) => {
                eprintln!("kimihooks-replay: config load error: {}", e);
                edition_cfg
            }
        }
    } else {
        edition_cfg
    };

    if let Some(req) = traces_request.as_ref() {
        http_export::post_traces(&effective_cfg, req, &logger);
    }
    if let Some(req) = logs_request.as_ref() {
        http_export::post_logs(&effective_cfg, req, &logger);
    }
    if let Some(req) = metrics_request.as_ref() {
        http_export::post_metrics(&effective_cfg, req, &logger);
    }
}

fn write_export_jsonl(out: &Path, request: &Value) {
    if let Some(parent) = out.parent() {
        if !parent.as_os_str().is_empty() {
            // CLI output paths are caller-controlled and may live in shared/project
            // directories. Create missing parents without changing existing permissions.
            if let Err(e) = kimihooks::common::dirs::ensure_dir(parent) {
                eprintln!("创建输出目录 {} 失败: {}", parent.display(), e);
                process::exit(2);
            }
        }
    }
    let mut file = match fs::File::create(out) {
        Ok(f) => f,
        Err(e) => {
            eprintln!("创建 {} 失败: {}", out.display(), e);
            process::exit(2);
        }
    };
    let line = match serde_json::to_string(request) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("序列化 OTLP request 失败: {}", e);
            process::exit(2);
        }
    };
    if let Err(e) = writeln!(file, "{}", line) {
        eprintln!("写入 {} 失败: {}", out.display(), e);
        process::exit(2);
    }
}

/// 从 `wire_<session>.jsonl` 或 `<stem>.wire.jsonl` 文件名反推 session_id。
fn infer_session_from_wire_filename(path: &Path) -> Option<String> {
    let name = path.file_name()?.to_str()?;

    // 新格式：`<stem>.wire.jsonl` → stem 即 session 标识
    if name.ends_with(".wire.jsonl") {
        let stem = name.strip_suffix(".wire.jsonl")?;
        return Some(stem.to_string());
    }

    // 旧格式：`wire_<session>.jsonl`
    let stem = path.file_stem()?.to_str()?;
    stem.strip_prefix("wire_").map(|s| s.to_string())
}

/// 从 wire.jsonl 内的 metadata 行提取 session_id 作为 fallback。
fn infer_session_from_records(path: &Path) -> Option<String> {
    let body = fs::read_to_string(path).ok()?;
    for line in body.lines() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        let v: serde_json::Value = serde_json::from_str(line).ok()?;
        // wire metadata 行可能包含 session_id
        if let Some(s) = v.get("session_id").and_then(|v| v.as_str()) {
            if !s.is_empty() {
                return Some(s.to_string());
            }
        }
        // 只需读第一行带 session_id 的
        if v.get("type").is_some() {
            // wire 行，继续扫描
            continue;
        }
    }
    None
}

/// 从 wire.jsonl 路径推导关联的 context.jsonl 路径。
///
/// 扫描规则（按优先级）：
/// 1. **同目录 `{stem}.context.jsonl`**（文件模式 SESSION_DIR 输出）
///    例：`my_session.wire.jsonl` → `my_session.context.jsonl`
/// 2. **同目录 `session_*_{hash}.context.jsonl`**（目录模式 SESSION_DIR 输出）
///    例：`session_abc_deadbeef.wire.jsonl` → `session_abc_deadbeef.context.jsonl`
/// 3. **同目录 `context.jsonl`**（原生 kimi-cli session 目录布局）
///    例：`~/.kimi/sessions/<digest>/<sid>/wire.jsonl` → 同目录 `context.jsonl`
fn find_companion_context(wire_path: &Path) -> Option<PathBuf> {
    let parent = wire_path.parent()?;
    let name = wire_path.file_name()?.to_str()?;

    // 规则 1/2：从 wire 文件名推导 context 文件名
    // `xxx.wire.jsonl` → `xxx.context.jsonl`
    if name.ends_with(".wire.jsonl") {
        let stem = name.strip_suffix(".wire.jsonl")?;
        let context_path = parent.join(format!("{}.context.jsonl", stem));
        if context_path.exists() {
            return Some(context_path);
        }
    }

    // 规则 3：同目录的 `context.jsonl`（原生 kimi-cli session 目录）
    let fallback = parent.join("context.jsonl");
    if fallback.exists() {
        return Some(fallback);
    }

    None
}

/// 将 report.config.toml 的 `host` 转换为 traces endpoint URL。
/// 与 cchooks `resolve_otel_url` 同源：
/// - 无 path（如 `https://aivision.example.com`）→ 追加 `/api/public/otel/v1/traces`
/// - 已有 path（如 `https://proxy/v1/traces`）→ 原样使用
fn resolve_traces_url(host: &str) -> String {
    let trimmed = host.trim_end_matches('/');
    // 简单检测：scheme://authority 之后是否还有非空 path。
    // 例如 "https://example.com" → path 为空 → 追加标准 Langfuse OTLP 路径。
    // "https://example.com/v1/traces" → 已有 path → 原样使用。
    let after_scheme = trimmed
        .find("://")
        .map(|i| &trimmed[i + 3..])
        .unwrap_or(trimmed);
    let has_path = after_scheme.contains('/');
    if has_path {
        trimmed.to_string()
    } else {
        format!("{}/api/public/otel/v1/traces", trimmed)
    }
}

// ─── Timestamp shifting（与 cchooks cli/report.rs 对齐）──────────────────

/// 将所有 span 的时间戳偏移到当前时刻，保持各 span 之间的相对时序不变。
fn shift_timestamps(requests: &mut [&mut Value]) -> Result<(), String> {
    let (min_start, max_end) = find_time_range(requests)
        .ok_or_else(|| "No valid timestamps found in OTLP data".to_string())?;

    let duration = max_end - min_start;
    let now_ns = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let target_end = now_ns;
    let target_start = target_end.saturating_sub(duration);
    let offset = target_start as i128 - min_start as i128;

    eprintln!(
        "[shift] duration={:.1}s offset={:.1}s",
        duration as f64 / 1_000_000_000.0,
        offset as f64 / 1_000_000_000.0,
    );

    for request in requests.iter_mut() {
        if let Some(resource_spans) = request.get_mut("resourceSpans").and_then(|v| v.as_array_mut()) {
            for rs in resource_spans.iter_mut() {
                if let Some(scope_spans) = rs.get_mut("scopeSpans").and_then(|v| v.as_array_mut()) {
                    for ss in scope_spans.iter_mut() {
                        if let Some(spans) = ss.get_mut("spans").and_then(|v| v.as_array_mut()) {
                            for span in spans.iter_mut() {
                                shift_nano_field(span, "startTimeUnixNano", offset);
                                shift_nano_field(span, "endTimeUnixNano", offset);
                            }
                        }
                    }
                }
            }
        }
    }
    Ok(())
}

fn find_time_range(requests: &[&mut Value]) -> Option<(u128, u128)> {
    let mut min_start: Option<u128> = None;
    let mut max_end: Option<u128> = None;

    for request in requests {
        let resource_spans = request.get("resourceSpans")?.as_array()?;
        for rs in resource_spans {
            if let Some(scope_spans) = rs.get("scopeSpans").and_then(|s| s.as_array()) {
                for ss in scope_spans {
                    if let Some(spans) = ss.get("spans").and_then(|s| s.as_array()) {
                        for span in spans {
                            // 跳过时间戳为 0 的 span（tool span 无原始时间戳时兜底为 0）
                            if let Some(start) = span.get("startTimeUnixNano").and_then(parse_nano) {
                                if start > 0 {
                                    min_start = Some(min_start.map_or(start, |m: u128| m.min(start)));
                                }
                            }
                            if let Some(end) = span.get("endTimeUnixNano").and_then(parse_nano) {
                                if end > 0 {
                                    max_end = Some(max_end.map_or(end, |m: u128| m.max(end)));
                                }
                            }
                        }
                    }
                }
            }
        }
    }
    match (min_start, max_end) {
        (Some(s), Some(e)) => Some((s, e)),
        _ => None,
    }
}

fn parse_nano(v: &Value) -> Option<u128> {
    v.as_str().and_then(|s| s.parse::<u128>().ok())
}

fn shift_nano_field(span: &mut Value, field: &str, offset: i128) {
    if let Some(val) = span.get(field).and_then(parse_nano) {
        if val == 0 {
            return; // 时间戳为 0 表示"无原始数据"，不偏移
        }
        let shifted = (val as i128 + offset).max(0) as u128;
        span[field] = Value::String(shifted.to_string());
    }
}
