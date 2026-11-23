use crate::cli::config::Config;
use crate::common::dirs;
use crate::common::logger::Logger;
use crate::common::otel::{build_otel_replay_export_requests_for_turn, post_otel_to_langfuse};
use crate::common::transcript::{build_turns, resolve_transcript_session_id};
use crate::hooks::langfuse::LangfuseConfig;
use crate::hooks::timing::{companion_timing_path, load_timing_data_from_path, TimingData};

const EXPORT_FAILURE: &str =
    "Failed to export OTLP data. Check the endpoint, network connection, and credentials.";

// Return fixed CLI diagnostics because config and exporter errors can contain sensitive data.
pub fn run(
    profile: Option<String>,
    config_path: String,
    session_path: Option<String>,
    otel_json: Option<String>,
    shift: bool,
    debug: bool,
) -> Result<(), &'static str> {
    let log_file = dirs::log_dir().join("cchooks.log");
    let logger = Logger::new(log_file, debug);

    // 1. Load config.toml
    if debug {
        eprintln!("[debug] Loading config from '{}'", config_path);
    }
    let config = Config::load(std::path::Path::new(&config_path)).map_err(|_| {
        "Failed to load report configuration. Check --config, file permissions, and TOML syntax."
    })?;
    let resolved = config.resolve_profile(profile.as_deref()).map_err(|_| {
        "Failed to resolve report profile. Check --profile and its credential settings."
    })?;
    let langfuse_config: LangfuseConfig = resolved.into();

    if let Some(otel_path) = otel_json {
        run_otel_json(&langfuse_config, &logger, &otel_path, shift, debug)
    } else {
        let session_path = session_path.expect("session_path guaranteed by CLI parsing");
        run_session_path(&langfuse_config, &logger, &session_path, debug)
    }
}

/// --session-path mode: parse transcript → build OTel → POST (original flow).
fn run_session_path(
    langfuse_config: &LangfuseConfig,
    logger: &Logger,
    session_path: &str,
    debug: bool,
) -> Result<(), &'static str> {
    if debug {
        eprintln!("[debug] Reading transcript from '{}'", session_path);
    }
    let content = std::fs::read_to_string(session_path)
        .map_err(|_| "Failed to read the transcript. Check --session-path and file permissions.")?;

    let parsed_lines: Vec<serde_json::Value> = content
        .lines()
        .filter(|line| !line.trim().is_empty())
        .filter_map(|line| serde_json::from_str(line).ok())
        .collect();

    if parsed_lines.is_empty() {
        return Err("No valid JSONL lines in the transcript.");
    }

    let turns = build_turns(&parsed_lines, None);
    if turns.is_empty() {
        return Err("No turns found in the transcript.");
    }

    let session_id = resolve_transcript_session_id(&parsed_lines);

    let version = env!("CARGO_PKG_VERSION");

    // Load companion timing file if present (e.g. foo.timing.jsonl next to foo.jsonl)
    let timing_path = companion_timing_path(session_path);
    let timing = if timing_path.exists() {
        let t = load_timing_data_from_path(&timing_path);
        if debug {
            eprintln!(
                "[debug] Loaded {} timing entries from '{}'",
                t.len(),
                timing_path.display()
            );
        }
        t
    } else {
        if debug {
            eprintln!(
                "[debug] No companion timing file at '{}'",
                timing_path.display()
            );
        }
        TimingData::new()
    };

    eprintln!("Reporting {} transcript turns", turns.len());

    let mut request_index = 0usize;
    for (i, turn) in turns.iter().enumerate() {
        let turn_number = i + 1;
        let requests = build_otel_replay_export_requests_for_turn(
            turn,
            turn_number,
            &session_id,
            &crate::common::env::var("USER").unwrap_or_else(|_| "unknown".to_string()),
            version,
            session_path,
            &timing,
        );
        for body in requests {
            let span_count = count_spans(&body);
            let has_api_error = body.to_string().contains("api_error");
            if debug {
                let span_names: Vec<String> = body["resourceSpans"][0]["scopeSpans"][0]["spans"]
                    .as_array()
                    .map(|arr| {
                        arr.iter()
                            .filter_map(|s| s["name"].as_str().map(String::from))
                            .collect()
                    })
                    .unwrap_or_default();
                eprintln!("[debug] Turn {}: {} spans", turn_number, span_count);
                eprintln!("[debug] Turn {} spans: {:?}", turn_number, span_names);
            }
            post_otel_to_langfuse(
                langfuse_config,
                &body,
                logger,
                request_index == 0,
                &session_id,
                session_path,
            )
            .map_err(|_| EXPORT_FAILURE)?;
            request_index += 1;
            eprintln!(
                "  Turn {}/{} sent ({} spans{})",
                turn_number,
                turns.len(),
                span_count,
                if has_api_error { ", has api_error" } else { "" },
            );
        }
    }

    eprintln!("Done. {} turns reported successfully.", turns.len());
    Ok(())
}

/// --otel-json mode: read pre-built OTLP JSONL → (optional shift) → POST each line.
fn run_otel_json(
    langfuse_config: &LangfuseConfig,
    logger: &Logger,
    otel_path: &str,
    shift: bool,
    debug: bool,
) -> Result<(), &'static str> {
    if debug {
        eprintln!("[debug] Reading OTLP JSONL from '{}'", otel_path);
    }
    let content = std::fs::read_to_string(otel_path)
        .map_err(|_| "Failed to read the OTLP input. Check --otel-json and file permissions.")?;

    let mut lines: Vec<serde_json::Value> = content
        .lines()
        .filter(|line| !line.trim().is_empty())
        .filter_map(|line| serde_json::from_str(line).ok())
        .collect();

    if lines.is_empty() {
        return Err("No valid JSONL lines in the OTLP input.");
    }

    // Apply timestamp shift if requested
    if shift {
        shift_timestamps(&mut lines, debug)?;
    }

    // Try to extract session_id from the first span's trace attributes
    let session_id =
        extract_session_id_from_otel(&lines).unwrap_or_else(|| "otel-report".to_string());

    eprintln!(
        "Posting {} OTLP request(s){}",
        lines.len(),
        if shift { " (shifted)" } else { "" },
    );

    for (i, body) in lines.iter().enumerate() {
        if debug {
            eprintln!(
                "[debug] Request {}/{}: {} spans",
                i + 1,
                lines.len(),
                count_spans(body),
            );
        }
        let is_new_session = i == 0;
        post_otel_to_langfuse(
            langfuse_config,
            body,
            logger,
            is_new_session,
            &session_id,
            otel_path,
        )
        .map_err(|_| EXPORT_FAILURE)?;
        eprintln!("  Request {}/{} sent", i + 1, lines.len());
    }

    eprintln!("Done. {} request(s) posted successfully.", lines.len());
    Ok(())
}

// ─── Timestamp shifting ──────────────────────────────────────────────────

/// Parse a nanosecond timestamp string (e.g. "1712345678000000000") to u128.
fn parse_nano(v: &serde_json::Value) -> Option<u128> {
    v.as_str().and_then(|s| s.parse::<u128>().ok())
}

/// Collect all startTimeUnixNano / endTimeUnixNano from all spans across all JSONL lines.
/// Returns (min_start, max_end).
fn find_time_range(lines: &[serde_json::Value]) -> Option<(u128, u128)> {
    let mut min_start: Option<u128> = None;
    let mut max_end: Option<u128> = None;

    for line in lines {
        let resource_spans = line.get("resourceSpans")?.as_array()?;
        for rs in resource_spans {
            let scope_spans = rs.get("scopeSpans").and_then(|s| s.as_array());
            if let Some(sss) = scope_spans {
                for ss in sss {
                    if let Some(spans) = ss.get("spans").and_then(|s| s.as_array()) {
                        for span in spans {
                            if let Some(start) = parse_nano(
                                span.get("startTimeUnixNano")
                                    .unwrap_or(&serde_json::Value::Null),
                            ) {
                                min_start = Some(min_start.map_or(start, |m: u128| m.min(start)));
                            }
                            if let Some(end) = parse_nano(
                                span.get("endTimeUnixNano")
                                    .unwrap_or(&serde_json::Value::Null),
                            ) {
                                max_end = Some(max_end.map_or(end, |m: u128| m.max(end)));
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

/// Shift all span timestamps so the session ends at "now", preserving relative intervals.
fn shift_timestamps(lines: &mut [serde_json::Value], debug: bool) -> Result<(), &'static str> {
    let (min_start, max_end) = find_time_range(lines)
        .ok_or("No valid timestamps found in OTLP data")?;

    let duration = max_end - min_start;
    let now_ns = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let target_end = now_ns;
    let target_start = target_end.saturating_sub(duration);
    // offset to add to each timestamp: target_start - min_start
    // Use i128 since offset can be negative (though unlikely for replay files)
    let offset = target_start as i128 - min_start as i128;

    if debug {
        let dur_secs = duration as f64 / 1_000_000_000.0;
        eprintln!("[debug:shift] Session duration: {:.1}s", dur_secs);
        eprintln!(
            "[debug:shift] Offset: {:.1}s",
            offset as f64 / 1_000_000_000.0
        );
    }

    eprintln!(
        "[shift] Shifting {} OTLP line(s), duration {:.1}min",
        lines.len(),
        duration as f64 / 60_000_000_000.0,
    );

    for line in lines.iter_mut() {
        if let Some(resource_spans) = line.get_mut("resourceSpans").and_then(|v| v.as_array_mut()) {
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

/// Shift a single nanosecond timestamp field by the given offset.
fn shift_nano_field(span: &mut serde_json::Value, field: &str, offset: i128) {
    if let Some(val) = span
        .get(field)
        .and_then(|v| v.as_str())
        .and_then(|s| s.parse::<u128>().ok())
    {
        let shifted = (val as i128 + offset).max(0) as u128;
        span[field] = serde_json::Value::String(shifted.to_string());
    }
}

/// Count spans in an OTLP ExportTraceServiceRequest body.
fn count_spans(body: &serde_json::Value) -> usize {
    body["resourceSpans"]
        .as_array()
        .and_then(|rs| rs.first())
        .and_then(|r| r["scopeSpans"].as_array())
        .and_then(|ss| ss.first())
        .and_then(|s| s["spans"].as_array())
        .map(|s| s.len())
        .unwrap_or(0)
}

/// Extract session_id from OTLP spans (look for session.id attribute).
fn extract_session_id_from_otel(lines: &[serde_json::Value]) -> Option<String> {
    for line in lines {
        let spans = line
            .pointer("/resourceSpans/0/scopeSpans/0/spans")
            .and_then(|s| s.as_array())?;
        for span in spans {
            if let Some(attrs) = span.get("attributes").and_then(|a| a.as_array()) {
                for attr in attrs {
                    if attr.get("key").and_then(|k| k.as_str()) == Some("session.id") {
                        if let Some(val) =
                            attr.pointer("/value/stringValue").and_then(|v| v.as_str())
                        {
                            if !val.is_empty() {
                                return Some(val.to_string());
                            }
                        }
                    }
                }
            }
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn report_errors_describe_configuration_and_input_problems() {
        let directory = tempfile::tempdir().unwrap();
        let config = directory.path().join("report.config.toml");
        let missing_input = directory.path().join("missing.jsonl");
        let run_report = |profile: Option<&str>| {
            run(
                profile.map(str::to_string),
                config.to_string_lossy().into_owned(),
                Some(missing_input.to_string_lossy().into_owned()),
                None,
                false,
                false,
            )
            .unwrap_err()
        };

        std::fs::write(&config, "fixture-only-config-entry").unwrap();
        assert!(
            run_report(None)
                == "Failed to load report configuration. Check --config, file permissions, and TOML syntax."
        );

        std::fs::write(
            &config,
            "version = 1\n[profiles.local]\nhost = 'https://example.test'\npublic_key = 'fixture-public'\nsecret_key = 'fixture-secret'\n",
        )
        .unwrap();
        assert!(
            run_report(Some("missing-profile"))
                == "Failed to resolve report profile. Check --profile and its credential settings."
        );
        assert_eq!(
            run_report(Some("local")),
            "Failed to read the transcript. Check --session-path and file permissions."
        );
    }
}
