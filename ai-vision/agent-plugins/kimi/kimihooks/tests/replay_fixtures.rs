//! Integration test: exercises the full replay pipeline on wire_*.jsonl fixtures,
//! producing OTLP JSONL output in isolated temporary directories. Avoids endpoint protection terminating the process by
//! running via `cargo test` instead of a standalone binary.

use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};

use agentic_otel_common::{
    otlp::{wrap_logs_export, wrap_metrics_export, wrap_trace_export},
    ExportContext,
};
use serde_json::Value;

use kimihooks::common::edition::EditionConfig;
use kimihooks::common::otel::{self as kimi_otel, HookRecord};
use kimihooks::hooks::stop::rebuild_hook_log_from_wire;

const SDK_NAME: &str = "kimihooks";
const VERSION: &str = env!("CARGO_PKG_VERSION");
const RUNTIME_NAME: &str = "kimi-cli";
const FALLBACK_AGENT_PRODUCT: &str = "kimi-cli";

fn fixtures_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/legacy-wire")
}

fn replays_dir() -> PathBuf {
    static NEXT: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
    std::env::temp_dir().join(format!("kimihooks-replay-{}-{}", std::process::id(),
        NEXT.fetch_add(1, std::sync::atomic::Ordering::Relaxed)))
}

fn write_export_jsonl(out: &Path, request: &Value) {
    if let Some(parent) = out.parent() {
        fs::create_dir_all(parent).expect("create output dir");
    }
    let mut file = fs::File::create(out).expect("create output file");
    let line = serde_json::to_string(request).expect("serialize OTLP request");
    writeln!(file, "{}", line).expect("write output");
}

fn infer_session_from_wire_filename(path: &Path) -> String {
    let name = path.file_name().unwrap().to_str().unwrap();
    if name.ends_with(".wire.jsonl") {
        return name.strip_suffix(".wire.jsonl").unwrap().to_string();
    }
    let stem = path.file_stem().unwrap().to_str().unwrap();
    stem.strip_prefix("wire_")
        .unwrap_or(stem)
        .to_string()
}

fn replay_one_fixture(fixture: &Path) -> PathBuf {
    // ant.username is span-only (must NOT be in resource per weaver policy).
    // Set AIVISION_ANT_USERNAME so resolve_username() picks it up for span attrs.
    std::env::set_var("AIVISION_ANT_USERNAME", "100001");
    // Remove any leftover resource override that would inject ant.username into resource
    std::env::remove_var("KIMIHOOKS_OTEL_RESOURCE_ATTRIBUTES");

    let session_id = infer_session_from_wire_filename(fixture);
    let cwd = "/tmp/test";

    let synth = rebuild_hook_log_from_wire(fixture, &session_id, cwd)
        .unwrap_or_else(|e| panic!("rebuild_hook_log_from_wire failed for {}: {}", fixture.display(), e));

    let records: Vec<HookRecord> = synth
        .iter()
        .map(|v| {
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

    assert!(!records.is_empty(), "fixture {} produced no records", fixture.display());

    let edition_cfg = EditionConfig::resolve();
    let agent_product: &str = edition_cfg
        .agent_product
        .as_deref()
        .filter(|s| !s.is_empty())
        .unwrap_or(FALLBACK_AGENT_PRODUCT);
    let agent_type: &str = edition_cfg.agent_type.as_str();

    let ctx = ExportContext {
        agent_product,
        agent_type,
        sdk_name: SDK_NAME,
        build_version: VERSION,
        agent_env: "dev",
        agent_scene: "default",
        collection_mode: "full",
        emit_meta_trigger: false,
    };

    let log_cfg = kimi_otel::ContentLogConfig::from(&edition_cfg);
    let emit_ctx = kimi_otel::EmissionContext {
        agent_product,
        agent_type,
        runtime_name: RUNTIME_NAME,
        log: &log_cfg,
    };

    let out_dir = replays_dir();
    let stem = fixture.file_stem().unwrap().to_str().unwrap();

    // ── traces ──
    let mut all_spans: Vec<kimi_otel::OtelSpanExport> = Vec::new();
    let turn_result = kimi_otel::build_all_turns_with_session(&records, &session_id, &log_cfg, "");
    if let Some(result) = &turn_result {
        for (_trace_id, turn) in &result.turns {
            all_spans.extend(turn.spans.clone());
        }
    }
    let traces_request = kimi_otel::finalize_export_resource(wrap_trace_export(all_spans.clone(), &ctx), "fixture-instance");
    write_export_jsonl(&out_dir.join(format!("spans_{}.jsonl", stem)), &traces_request);

    // ── logs (events) ──
    let events = kimi_otel::build_correlated_events(&records, &session_id, &emit_ctx, turn_result.as_ref().unwrap(), false);
    let logs_request = kimi_otel::finalize_export_resource(wrap_logs_export(events, &ctx), "fixture-instance");
    write_export_jsonl(&out_dir.join(format!("logs_{}.jsonl", stem)), &logs_request);

    // ── metrics ──
    let mut all_metrics = kimi_otel::build_tool_metrics(&records, &session_id, &emit_ctx);
    all_metrics.extend(kimi_otel::build_llm_metrics(&records, &session_id, &emit_ctx));
    all_metrics.extend(kimi_otel::build_tool_duration_metrics(&records, &session_id, &emit_ctx));
    all_metrics.extend(kimi_otel::build_session_token_metrics(&records, &session_id, &emit_ctx));
    let metrics_request = kimi_otel::finalize_export_resource(wrap_metrics_export(all_metrics, &ctx), "fixture-instance");
    write_export_jsonl(&out_dir.join(format!("metrics_{}.jsonl", stem)), &metrics_request);

    // Basic sanity: traces should have at least 1 span for any valid fixture
    assert!(
        !all_spans.is_empty(),
        "fixture {} produced 0 spans — replay logic likely broken",
        fixture.display()
    );
    out_dir
}

#[test]
fn replay_wire_basic() {
    let fixture = fixtures_dir().join("wire_basic.jsonl");
    if !fixture.exists() {
        panic!("fixture not found: {}", fixture.display());
    }
    let out = replay_one_fixture(&fixture);

    let spans_file = out.join("spans_wire_basic.jsonl");
    assert!(spans_file.exists(), "spans output not written");
    let content = fs::read_to_string(&spans_file).unwrap();
    assert!(content.contains("resourceSpans"), "traces missing resourceSpans");
    fs::remove_dir_all(out).unwrap();
}

#[test]
fn replay_wire_tools() {
    let fixture = fixtures_dir().join("wire_tools.jsonl");
    if !fixture.exists() {
        panic!("fixture not found: {}", fixture.display());
    }
    let out = replay_one_fixture(&fixture);

    let spans_file = out.join("spans_wire_tools.jsonl");
    assert!(spans_file.exists(), "spans output not written");
    let content = fs::read_to_string(&spans_file).unwrap();
    assert!(content.contains("resourceSpans"), "traces missing resourceSpans");
    // Should contain tool spans
    assert!(content.contains("execute_tool") || content.contains("tool"), "no tool spans found");
    fs::remove_dir_all(out).unwrap();
}

#[test]
fn replay_wire_advanced() {
    let fixture = fixtures_dir().join("wire_advanced.jsonl");
    if !fixture.exists() {
        panic!("fixture not found: {}", fixture.display());
    }
    let out = replay_one_fixture(&fixture);

    let spans_file = out.join("spans_wire_advanced.jsonl");
    assert!(spans_file.exists(), "spans output not written");
    let content = fs::read_to_string(&spans_file).unwrap();
    assert!(content.contains("resourceSpans"), "traces missing resourceSpans");
    assert!(content.contains("child-1") && content.contains("compaction") && content.contains("APIStatusError"));
    fs::remove_dir_all(out).unwrap();
}

#[test]
fn replay_all_fixtures_produce_three_signals() {
    let fixtures: Vec<PathBuf> = ["wire_basic.jsonl", "wire_tools.jsonl", "wire_advanced.jsonl"]
        .iter()
        .map(|f| fixtures_dir().join(f))
        .collect();

    for fixture in &fixtures {
        let out = replay_one_fixture(fixture);
        let stem = fixture.file_stem().unwrap().to_str().unwrap();
        assert!(out.join(format!("spans_{}.jsonl", stem)).exists(), "missing spans for {}", stem);
        assert!(out.join(format!("logs_{}.jsonl", stem)).exists(), "missing logs for {}", stem);
        assert!(out.join(format!("metrics_{}.jsonl", stem)).exists(), "missing metrics for {}", stem);
        fs::remove_dir_all(out).unwrap();
    }
}

#[test]
fn replay_kimi_code_native_tool_loop_has_two_model_calls_and_one_receipt() {
    let fixture = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/kimi-code-2.1.1/native-tool-loop.wire.jsonl");
    let out = replay_one_fixture(&fixture);
    let traces: Value = serde_json::from_str(&fs::read_to_string(out.join("spans_native-tool-loop.wire.jsonl")).unwrap()).unwrap();
    let spans: Vec<&Value> = traces["resourceSpans"].as_array().unwrap().iter()
        .flat_map(|r| r["scopeSpans"].as_array().unwrap())
        .flat_map(|s| s["spans"].as_array().unwrap()).collect();
    let attr = |span: &Value, key: &str| span["attributes"].as_array().unwrap().iter()
        .find(|a| a["key"] == key).map(|a| a["value"]["stringValue"].as_str().map(str::to_string).unwrap_or_else(|| a["value"]["intValue"].to_string())).unwrap_or_default();
    let llms: Vec<_> = spans.iter().filter(|s| attr(s, "gen_ai.span.kind") == "LLM").collect();
    assert_eq!(llms.len(), 2);
    assert_eq!(spans.iter().filter(|s| attr(s, "gen_ai.span.kind") == "TOOL").count(), 1);
    for span in llms {
        assert_eq!(attr(span, "gen_ai.request.model"), "native-e2e-chat");
        assert_eq!(attr(span, "gen_ai.provider.name"), "openai");
        assert_eq!(attr(span, "gen_ai.usage.input_tokens"), "16");
        assert_eq!(attr(span, "gen_ai.usage.output_tokens"), "4");
        assert_eq!(attr(span, "gen_ai.usage.total_tokens"), "20");
    }
    let logs: Value = serde_json::from_str(&fs::read_to_string(out.join("logs_native-tool-loop.wire.jsonl")).unwrap()).unwrap();
    let logs: Vec<&Value> = logs["resourceLogs"].as_array().unwrap().iter()
        .flat_map(|r| r["scopeLogs"].as_array().unwrap())
        .flat_map(|s| s["logRecords"].as_array().unwrap()).collect();
    assert_eq!(logs.iter().filter(|s| attr(s, "event.name") == "api_request").count(), 2);
    let metrics = fs::read_to_string(out.join("metrics_native-tool-loop.wire.jsonl")).unwrap();
    assert!(metrics.contains("agent.llm_request.count") && metrics.contains("agent.token.usage"));
    fs::remove_dir_all(out).unwrap();
}

#[test]
fn replay_logs_reference_the_exported_span_and_keep_private_content_redacted() {
    for fixture in [fixtures_dir().join("wire_tools.jsonl"), fixtures_dir().join("wire_advanced.jsonl"), PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/kimi-code-2.1.1/native-tool-loop.wire.jsonl")] {
        let out = replay_one_fixture(&fixture);
        let stem = fixture.file_stem().unwrap().to_str().unwrap();
        let traces: Value = serde_json::from_str(&fs::read_to_string(out.join(format!("spans_{stem}.jsonl"))).unwrap()).unwrap();
        assert!(traces["resourceSpans"][0]["resource"].get("env").is_none());
        assert!(traces["resourceSpans"][0]["resource"]["attributes"].as_array().unwrap().iter().any(|a| a["key"] == "ant.agent.instance" && a["value"]["stringValue"] == "fixture-instance"));
        let spans: Vec<&Value> = traces["resourceSpans"].as_array().unwrap().iter().flat_map(|r| r["scopeSpans"].as_array().unwrap()).flat_map(|s| s["spans"].as_array().unwrap()).collect();
        let logs: Value = serde_json::from_str(&fs::read_to_string(out.join(format!("logs_{stem}.jsonl"))).unwrap()).unwrap();
        for log in logs["resourceLogs"].as_array().unwrap().iter().flat_map(|r| r["scopeLogs"].as_array().unwrap()).flat_map(|s| s["logRecords"].as_array().unwrap()) {
            let span = spans.iter().find(|s| s["traceId"] == log["traceId"] && s["spanId"] == log["spanId"]).expect("every log must reference a span from this exact export");
            let attrs = log["attributes"].as_array().unwrap();
            let attr = |key: &str| attrs.iter().find(|a| a["key"] == key).map(|a| a["value"]["stringValue"].as_str().unwrap_or("")).unwrap_or("");
            assert!(!attrs.iter().any(|a| a["key"] == "ant.agent.type" || a["key"] == "ant.agent.product"));
            assert!(attrs.iter().any(|a| a["key"] == "event.sequence"));
            let span_attr = |key: &str| span["attributes"].as_array().unwrap().iter().find(|a| a["key"] == key).map(|a| a["value"]["stringValue"].as_str().unwrap_or("")).unwrap_or("");
            assert_eq!(attr("gen_ai.chat.id"), span_attr("gen_ai.chat.id"));
            match attr("event.name") {
                "api_request" => {
                    assert_eq!(span_attr("gen_ai.span.kind"), "LLM");
                    assert_eq!(attr("gen_ai.response.id"), span_attr("gen_ai.response.id"));
                    for key in ["gen_ai.input.messages", "gen_ai.output.messages", "gen_ai.system_instructions"] { assert_eq!(attr(key), "[REDACTED]"); }
                    assert!(!attr("gen_ai.response.model").is_empty());
                }
                "tool_result" | "tool_decision" => {
                    assert_eq!(attr("gen_ai.tool.call.id"), if span_attr("gen_ai.tool.call.id").is_empty() { span_attr("agentic.subagent.parent_tool_call_id") } else { span_attr("gen_ai.tool.call.id") });
                    if attr("event.name") == "tool_result" { assert_eq!(attr("gen_ai.tool.call.result"), "[REDACTED]"); }
                }
                _ => {}
            }
        }
        fs::remove_dir_all(out).unwrap();
    }
}
