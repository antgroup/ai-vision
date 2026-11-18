//! JSONL file reader for OTEL telemetry signals
//!
//! Reads JSONL (JSON Lines) files where each line contains an OTEL protocol
//! export request (traces, logs, or metrics). This enables lightweight local
//! validation of agent debug telemetry without requiring an HTTP proxy.

use anyhow::{Context, Result};
use serde_json::Value;
use std::fs;
use std::path::{Path, PathBuf};
use tracing::{debug, warn};
use walkdir::WalkDir;

/// Represents a parsed OTEL signal from a JSONL line
#[derive(Debug, Clone)]
pub struct OtelSignal {
    /// The signal type detected from the JSON structure
    pub signal_type: SignalType,
    /// The raw JSON value of the OTEL export request
    pub data: Value,
    /// Line number in the source file (1-based)
    pub line_number: usize,
    /// Source file path (populated when reading from files)
    pub source_file: Option<PathBuf>,
}

/// Type of OTEL signal detected from JSON structure
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SignalType {
    Traces,
    Logs,
    Metrics,
    Unknown,
}

impl std::fmt::Display for SignalType {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            SignalType::Traces => write!(f, "traces"),
            SignalType::Logs => write!(f, "logs"),
            SignalType::Metrics => write!(f, "metrics"),
            SignalType::Unknown => write!(f, "unknown"),
        }
    }
}

/// Read and parse a JSONL file containing OTEL telemetry signals
///
/// Each line in the file should be a valid JSON object representing an OTEL
/// export request. Lines are classified by signal type based on their JSON keys:
/// - `resourceSpans` -> Traces
/// - `resourceLogs` -> Logs
/// - `resourceMetrics` -> Metrics
///
/// Empty lines and lines that fail to parse are skipped with a warning.
pub fn read_jsonl_file(path: &Path) -> Result<Vec<OtelSignal>> {
    let content = fs::read_to_string(path)
        .with_context(|| format!("Failed to read JSONL file: {}", path.display()))?;

    let mut signals = parse_jsonl(&content)?;
    for signal in &mut signals {
        signal.source_file = Some(path.to_path_buf());
    }
    Ok(signals)
}

/// Parse JSONL content string into OTEL signals
pub fn parse_jsonl(content: &str) -> Result<Vec<OtelSignal>> {
    let mut signals = Vec::new();

    for (idx, line) in content.lines().enumerate() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }

        match serde_json::from_str::<Value>(line) {
            Ok(value) => {
                let signal_type = detect_signal_type(&value);
                if signal_type == SignalType::Unknown {
                    warn!(
                        line = idx + 1,
                        "Could not detect OTEL signal type, skipping line"
                    );
                    continue;
                }
                debug!(
                    line = idx + 1,
                    signal_type = %signal_type,
                    "Parsed OTEL signal"
                );
                signals.push(OtelSignal {
                    signal_type,
                    data: value,
                    line_number: idx + 1,
                    source_file: None,
                });
            }
            Err(e) => {
                warn!(
                    line = idx + 1,
                    error = %e,
                    "Failed to parse JSON line, skipping"
                );
            }
        }
    }

    debug!(total = signals.len(), "Parsed JSONL signals");
    Ok(signals)
}

/// Detect the OTEL signal type from JSON structure
fn detect_signal_type(value: &Value) -> SignalType {
    if value.get("resourceSpans").is_some() {
        SignalType::Traces
    } else if value.get("resourceLogs").is_some() {
        SignalType::Logs
    } else if value.get("resourceMetrics").is_some() {
        SignalType::Metrics
    } else {
        SignalType::Unknown
    }
}

/// Read all JSONL files from a directory recursively
///
/// Recursively walks the given directory and reads all files with `.jsonl` extension.
/// Follows symbolic links.
pub fn read_jsonl_dir(dir: &Path) -> Result<Vec<OtelSignal>> {
    let mut all_signals = Vec::new();

    for entry in WalkDir::new(dir).follow_links(true) {
        let entry =
            entry.with_context(|| format!("Failed to traverse directory: {}", dir.display()))?;
        let path = entry.path();
        if path.is_file() && path.extension().and_then(|e| e.to_str()) == Some("jsonl") {
            debug!(file = %path.display(), "Reading JSONL file");
            let signals = read_jsonl_file(path)?;
            all_signals.extend(signals);
        }
    }

    Ok(all_signals)
}

/// Extract individual samples from an OTEL signal for validation
///
/// Navigates the OTEL structure to extract individual spans/logs/metrics
/// in the format expected by the validation policies.
pub fn extract_samples(signal: &OtelSignal) -> Vec<Value> {
    match signal.signal_type {
        SignalType::Traces => extract_trace_samples(&signal.data),
        SignalType::Logs => extract_log_samples(&signal.data),
        SignalType::Metrics => extract_metric_samples(&signal.data),
        SignalType::Unknown => vec![],
    }
}

fn extract_trace_samples(data: &Value) -> Vec<Value> {
    let mut samples = Vec::new();
    if let Some(resource_spans) = data.get("resourceSpans").and_then(|v| v.as_array()) {
        for rs in resource_spans {
            let resource = rs.get("resource").cloned().unwrap_or(serde_json::json!({}));
            if let Some(scope_spans) = rs.get("scopeSpans").and_then(|v| v.as_array()) {
                for ss in scope_spans {
                    if let Some(spans) = ss.get("spans").and_then(|v| v.as_array()) {
                        for span in spans {
                            samples.push(serde_json::json!({
                                "sample": {
                                    "span": span,
                                    "resource": resource
                                }
                            }));
                        }
                    }
                }
            }
        }
    }
    samples
}

fn extract_log_samples(data: &Value) -> Vec<Value> {
    let mut samples = Vec::new();
    if let Some(resource_logs) = data.get("resourceLogs").and_then(|v| v.as_array()) {
        for rl in resource_logs {
            let resource = rl.get("resource").cloned().unwrap_or(serde_json::json!({}));
            if let Some(scope_logs) = rl.get("scopeLogs").and_then(|v| v.as_array()) {
                for sl in scope_logs {
                    if let Some(log_records) = sl.get("logRecords").and_then(|v| v.as_array()) {
                        for log in log_records {
                            samples.push(serde_json::json!({
                                "sample": {
                                    "log": log,
                                    "resource": resource
                                }
                            }));
                        }
                    }
                }
            }
        }
    }
    samples
}

fn extract_metric_samples(data: &Value) -> Vec<Value> {
    let mut samples = Vec::new();
    if let Some(resource_metrics) = data.get("resourceMetrics").and_then(|v| v.as_array()) {
        for rm in resource_metrics {
            let resource = rm.get("resource").cloned().unwrap_or(serde_json::json!({}));
            if let Some(scope_metrics) = rm.get("scopeMetrics").and_then(|v| v.as_array()) {
                for sm in scope_metrics {
                    if let Some(metrics) = sm.get("metrics").and_then(|v| v.as_array()) {
                        for metric in metrics {
                            samples.push(serde_json::json!({
                                "sample": {
                                    "metric": metric,
                                    "resource": resource
                                }
                            }));
                        }
                    }
                }
            }
        }
    }
    samples
}

/// Extract the name from a sample (span name, metric name, log body, etc.)
pub fn sample_name(sample: &Value) -> String {
    if let Some(span) = sample.get("sample").and_then(|s| s.get("span")) {
        span.get("name")
            .and_then(|n| n.as_str())
            .unwrap_or("<unnamed span>")
            .to_string()
    } else if let Some(metric) = sample.get("sample").and_then(|s| s.get("metric")) {
        metric
            .get("name")
            .and_then(|n| n.as_str())
            .unwrap_or("<unnamed metric>")
            .to_string()
    } else if let Some(log) = sample.get("sample").and_then(|s| s.get("log")) {
        log.get("body")
            .and_then(|b| b.as_str())
            .map(|s| {
                if s.len() > 50 {
                    format!("{}...", &s[..50])
                } else {
                    s.to_string()
                }
            })
            .unwrap_or_else(|| "<log>".to_string())
    } else {
        "<unknown>".to_string()
    }
}

/// Extract attribute keys from an OTLP sample
///
/// OTLP attributes are stored as `[{"key": "name", "value": {...}}, ...]`
pub fn extract_attribute_keys(sample: &Value) -> Vec<String> {
    let inner = sample
        .get("sample")
        .and_then(|s| s.get("span").or(s.get("log")).or(s.get("metric")));

    let Some(inner) = inner else {
        return vec![];
    };

    inner
        .get("attributes")
        .and_then(|a| a.as_array())
        .map(|attrs| {
            attrs
                .iter()
                .filter_map(|attr| {
                    attr.get("key")
                        .or(attr.get("name"))
                        .and_then(|k| k.as_str())
                        .map(|s| s.to_string())
                })
                .collect()
        })
        .unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_detect_signal_type_traces() {
        let json = serde_json::json!({"resourceSpans": []});
        assert_eq!(detect_signal_type(&json), SignalType::Traces);
    }

    #[test]
    fn test_detect_signal_type_logs() {
        let json = serde_json::json!({"resourceLogs": []});
        assert_eq!(detect_signal_type(&json), SignalType::Logs);
    }

    #[test]
    fn test_detect_signal_type_metrics() {
        let json = serde_json::json!({"resourceMetrics": []});
        assert_eq!(detect_signal_type(&json), SignalType::Metrics);
    }

    #[test]
    fn test_detect_signal_type_unknown() {
        let json = serde_json::json!({"something": "else"});
        assert_eq!(detect_signal_type(&json), SignalType::Unknown);
    }

    #[test]
    fn test_parse_jsonl_mixed_signals() {
        let content = r#"{"resourceSpans":[{"resource":{},"scopeSpans":[{"spans":[{"name":"test-span","attributes":[]}]}]}]}
{"resourceLogs":[{"resource":{},"scopeLogs":[{"logRecords":[{"body":"test log"}]}]}]}
{"resourceMetrics":[{"resource":{},"scopeMetrics":[{"metrics":[{"name":"test.metric"}]}]}]}
"#;
        let signals = parse_jsonl(content).unwrap();
        assert_eq!(signals.len(), 3);
        assert_eq!(signals[0].signal_type, SignalType::Traces);
        assert_eq!(signals[0].line_number, 1);
        assert!(signals[0].source_file.is_none());
        assert_eq!(signals[1].signal_type, SignalType::Logs);
        assert_eq!(signals[2].signal_type, SignalType::Metrics);
    }

    #[test]
    fn test_parse_jsonl_skips_empty_lines() {
        let content = r#"{"resourceSpans":[]}

{"resourceLogs":[]}
"#;
        let signals = parse_jsonl(content).unwrap();
        assert_eq!(signals.len(), 2);
    }

    #[test]
    fn test_parse_jsonl_skips_invalid_lines() {
        let content = r#"{"resourceSpans":[]}
not valid json
{"resourceLogs":[]}
"#;
        let signals = parse_jsonl(content).unwrap();
        assert_eq!(signals.len(), 2);
    }

    #[test]
    fn test_parse_jsonl_skips_unknown_signal() {
        let content = r#"{"resourceSpans":[]}
{"unknownField": true}
"#;
        let signals = parse_jsonl(content).unwrap();
        assert_eq!(signals.len(), 1);
    }

    #[test]
    fn test_extract_trace_samples() {
        let data = serde_json::json!({
            "resourceSpans": [{
                "resource": {},
                "scopeSpans": [{
                    "spans": [
                        {"name": "span1", "attributes": []},
                        {"name": "span2", "attributes": []}
                    ]
                }]
            }]
        });
        let signal = OtelSignal {
            signal_type: SignalType::Traces,
            data,
            line_number: 1,
            source_file: None,
        };
        let samples = extract_samples(&signal);
        assert_eq!(samples.len(), 2);
        assert_eq!(samples[0]["sample"]["span"]["name"], "span1");
        assert_eq!(samples[1]["sample"]["span"]["name"], "span2");
    }

    #[test]
    fn test_extract_log_samples() {
        let data = serde_json::json!({
            "resourceLogs": [{
                "resource": {},
                "scopeLogs": [{
                    "logRecords": [
                        {"body": "log1"},
                        {"body": "log2"}
                    ]
                }]
            }]
        });
        let signal = OtelSignal {
            signal_type: SignalType::Logs,
            data,
            line_number: 1,
            source_file: None,
        };
        let samples = extract_samples(&signal);
        assert_eq!(samples.len(), 2);
    }

    #[test]
    fn test_extract_metric_samples() {
        let data = serde_json::json!({
            "resourceMetrics": [{
                "resource": {},
                "scopeMetrics": [{
                    "metrics": [
                        {"name": "metric1"},
                        {"name": "metric2"}
                    ]
                }]
            }]
        });
        let signal = OtelSignal {
            signal_type: SignalType::Metrics,
            data,
            line_number: 1,
            source_file: None,
        };
        let samples = extract_samples(&signal);
        assert_eq!(samples.len(), 2);
    }

    #[test]
    fn test_read_jsonl_file_not_found() {
        let result = read_jsonl_file(Path::new("/nonexistent/file.jsonl"));
        assert!(result.is_err());
    }

    #[test]
    fn test_signal_type_display() {
        assert_eq!(format!("{}", SignalType::Traces), "traces");
        assert_eq!(format!("{}", SignalType::Logs), "logs");
        assert_eq!(format!("{}", SignalType::Metrics), "metrics");
        assert_eq!(format!("{}", SignalType::Unknown), "unknown");
    }

    #[test]
    fn test_sample_name_span() {
        let sample = serde_json::json!({"sample": {"span": {"name": "ai_agent.turn"}}});
        assert_eq!(sample_name(&sample), "ai_agent.turn");
    }

    #[test]
    fn test_sample_name_metric() {
        let sample = serde_json::json!({"sample": {"metric": {"name": "request.count"}}});
        assert_eq!(sample_name(&sample), "request.count");
    }

    #[test]
    fn test_extract_attribute_keys_otlp_format() {
        let sample = serde_json::json!({
            "sample": {
                "span": {
                    "name": "test",
                    "attributes": [
                        {"key": "session.id", "value": {"stringValue": "abc"}},
                        {"key": "turn_number", "value": {"intValue": "1"}}
                    ]
                }
            }
        });
        let keys = extract_attribute_keys(&sample);
        assert_eq!(keys, vec!["session.id", "turn_number"]);
    }

    #[test]
    fn test_extract_attribute_keys_name_format() {
        let sample = serde_json::json!({
            "sample": {
                "span": {
                    "name": "test",
                    "attributes": [
                        {"name": "session.id", "value": "abc"},
                        {"name": "source", "value": "codex"}
                    ]
                }
            }
        });
        let keys = extract_attribute_keys(&sample);
        assert_eq!(keys, vec!["session.id", "source"]);
    }
}
