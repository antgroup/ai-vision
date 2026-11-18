//! Typed OTLP data structures for spans and log records.
//!
//! Provides compile-time safety for OTLP JSON construction, replacing
//! raw `serde_json::json!()` calls that are prone to field name typos
//! and missing required fields.

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::ExportContext;

pub(crate) const ANT_META_TRIGGER_KEY: &str = "ant.meta.trigger";
pub(crate) const AIVISION_META_TRIGGER_ENV: &str = "AIVISION_META_TRIGGER";

#[cfg(test)]
pub(crate) static META_TRIGGER_ENV_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

// ── SpanKind constants ─────────────────────────────────────────────

pub const SPAN_KIND_INTERNAL: u32 = 1;
pub const SPAN_KIND_SERVER: u32 = 2;
pub const SPAN_KIND_CLIENT: u32 = 3;

// ── OtelAnyValue ───────────────────────────────────────────────────

/// OTLP AnyValue — the value part of a key-value attribute.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(untagged)]
pub enum OtelAnyValue {
    String {
        #[serde(rename = "stringValue")]
        string_value: String,
    },
    Int {
        #[serde(rename = "intValue")]
        int_value: i64,
    },
    Bool {
        #[serde(rename = "boolValue")]
        bool_value: bool,
    },
    Array {
        #[serde(rename = "arrayValue")]
        array_value: OtelArrayValue,
    },
}

/// OTLP ArrayValue — wraps a list of AnyValue.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OtelArrayValue {
    pub values: Vec<OtelAnyValue>,
}

// ── OtelAttribute ──────────────────────────────────────────────────

/// OTLP KeyValue attribute.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OtelAttribute {
    pub key: String,
    pub value: OtelAnyValue,
}

impl OtelAttribute {
    /// String attribute: `{"key": k, "value": {"stringValue": v}}`
    pub fn string(key: impl Into<String>, value: impl Into<String>) -> Self {
        Self {
            key: key.into(),
            value: OtelAnyValue::String {
                string_value: value.into(),
            },
        }
    }

    /// Integer attribute: `{"key": k, "value": {"intValue": v}}`
    pub fn int(key: impl Into<String>, value: i64) -> Self {
        Self {
            key: key.into(),
            value: OtelAnyValue::Int { int_value: value },
        }
    }

    /// Integer encoded as stringValue for Langfuse OTel worker compatibility.
    /// `{"key": k, "value": {"stringValue": "42"}}`
    pub fn int_as_string(key: impl Into<String>, value: i64) -> Self {
        Self {
            key: key.into(),
            value: OtelAnyValue::String {
                string_value: value.to_string(),
            },
        }
    }

    /// Boolean attribute: `{"key": k, "value": {"boolValue": v}}`
    pub fn bool(key: impl Into<String>, value: bool) -> Self {
        Self {
            key: key.into(),
            value: OtelAnyValue::Bool { bool_value: value },
        }
    }

    /// String array attribute: `{"key": k, "value": {"arrayValue": {"values": [{"stringValue": v}, ...]}}}`
    pub fn string_array(key: impl Into<String>, values: &[String]) -> Self {
        Self {
            key: key.into(),
            value: OtelAnyValue::Array {
                array_value: OtelArrayValue {
                    values: values
                        .iter()
                        .map(|v| OtelAnyValue::String {
                            string_value: v.clone(),
                        })
                        .collect(),
                },
            },
        }
    }
}

// ── OtelStatus ─────────────────────────────────────────────────────

/// OTLP Span Status.
///
/// Codes per OTel spec: 0 = UNSET, 1 = OK, 2 = ERROR.
/// `message` is only meaningful for ERROR status.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OtelStatus {
    pub code: u32,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub message: Option<String>,
}

impl OtelStatus {
    pub fn ok() -> Self {
        Self {
            code: 1,
            message: None,
        }
    }

    pub fn error(msg: impl Into<String>) -> Self {
        Self {
            code: 2,
            message: Some(msg.into()),
        }
    }

    pub fn unset() -> Self {
        Self {
            code: 0,
            message: None,
        }
    }
}

// ── OtelSpan ───────────────────────────────────────────────────────

/// OTLP Span — a single trace span.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct OtelSpan {
    pub trace_id: String,
    pub span_id: String,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub parent_span_id: String,
    pub name: String,
    pub kind: u32,
    pub start_time_unix_nano: String,
    pub end_time_unix_nano: String,
    pub attributes: Vec<OtelAttribute>,
    pub status: OtelStatus,
}

// ── OtelLogRecord ──────────────────────────────────────────────────

/// OTLP Log Record body — supports string or kvlist format.
#[derive(Debug, Clone, Serialize)]
#[serde(untagged)]
pub enum OtelLogBody {
    String {
        #[serde(rename = "stringValue")]
        string_value: String,
    },
    KvList {
        #[serde(rename = "kvlistValue")]
        kvlist_value: OtelKvList,
    },
}

/// Key-value list for log record body.
#[derive(Debug, Clone, Serialize)]
pub struct OtelKvList {
    pub values: Vec<OtelAttribute>,
}

/// OTLP Log Record — used for event emission.
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct OtelLogRecord {
    pub time_unix_nano: String,
    pub observed_time_unix_nano: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub trace_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub span_id: Option<String>,
    pub body: OtelLogBody,
    pub attributes: Vec<OtelAttribute>,
    #[serde(rename = "droppedAttributesCount")]
    pub dropped_attributes_count: u32,
}

impl OtelLogRecord {
    /// Create a log record with kvlist body containing event_name.
    pub fn event(event_name: &str, time_nano: &str, attributes: Vec<OtelAttribute>) -> Self {
        Self {
            time_unix_nano: time_nano.to_string(),
            observed_time_unix_nano: time_nano.to_string(),
            trace_id: None,
            span_id: None,
            body: OtelLogBody::KvList {
                kvlist_value: OtelKvList {
                    values: vec![OtelAttribute::string("event_name", event_name)],
                },
            },
            attributes,
            dropped_attributes_count: 0,
        }
    }

    /// Create a log record with a plain string body.
    pub fn string_event(body: &str, time_nano: &str, attributes: Vec<OtelAttribute>) -> Self {
        Self {
            time_unix_nano: time_nano.to_string(),
            observed_time_unix_nano: time_nano.to_string(),
            trace_id: None,
            span_id: None,
            body: OtelLogBody::String {
                string_value: body.to_string(),
            },
            attributes,
            dropped_attributes_count: 0,
        }
    }

    /// Correlate this event with an existing trace span.
    pub fn with_correlation(mut self, trace_id: &str, span_id: &str) -> Self {
        self.trace_id = Some(trace_id.to_string());
        self.span_id = Some(span_id.to_string());
        self
    }
}

// ── Agentic tool attribute helpers ──────────────────────────────────

/// Append agentic.tool.* attributes to a typed attribute list.
/// `agentic.tool.raw_name` is always emitted.
/// `agentic.tool.type/name/id` only for non-builtin tools (mcp/skill).
pub fn append_agentic_tool_attrs_typed(
    attributes: &mut Vec<OtelAttribute>,
    info: &crate::AgenticToolInfo,
) {
    attributes.push(OtelAttribute::string(
        "agentic.tool.raw_name",
        &info.raw_name,
    ));
    if info.tool_type != "builtin" {
        attributes.push(OtelAttribute::string("agentic.tool.type", info.tool_type));
        attributes.push(OtelAttribute::string("agentic.tool.name", &info.name));
        attributes.push(OtelAttribute::string("agentic.tool.id", &info.id));
    }
}

// ── OTLP Metrics ──────────────────────────────────────────────────

/// OTLP NumberDataPoint — a single metric data point with integer value.
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct OtelNumberDataPoint {
    pub attributes: Vec<OtelAttribute>,
    pub time_unix_nano: String,
    pub start_time_unix_nano: String,
    pub as_int: i64,
}

/// OTLP Sum — monotonic cumulative counter data.
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct OtelSum {
    pub data_points: Vec<OtelNumberDataPoint>,
    /// 2 = CUMULATIVE (delta not used for push counters)
    pub aggregation_temporality: u32,
    pub is_monotonic: bool,
}

/// OTLP Histogram data point.
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct OtelHistogramDataPoint {
    pub attributes: Vec<OtelAttribute>,
    pub start_time_unix_nano: String,
    pub time_unix_nano: String,
    pub count: u64,
    pub sum: f64,
    pub min: Option<f64>,
    pub max: Option<f64>,
    pub explicit_bounds: Vec<f64>,
    pub bucket_counts: Vec<u64>,
}

/// OTLP Histogram — distribution metric with configurable bucket boundaries.
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct OtelHistogram {
    pub data_points: Vec<OtelHistogramDataPoint>,
    /// 2 = CUMULATIVE
    pub aggregation_temporality: u32,
}

/// OTLP Metric — wraps name + description + unit + data (Sum or Histogram).
///
/// Only one of `sum` or `histogram` should be `Some`; the other must be `None`.
/// Serialization skips the `None` variant automatically.
#[derive(Debug, Clone, Serialize)]
pub struct OtelMetric {
    pub name: String,
    pub description: String,
    pub unit: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub sum: Option<OtelSum>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub histogram: Option<OtelHistogram>,
}

impl OtelMetric {
    /// Create a counter (Sum) metric.
    pub fn counter(
        name: impl Into<String>,
        description: impl Into<String>,
        unit: impl Into<String>,
        sum: OtelSum,
    ) -> Self {
        Self {
            name: name.into(),
            description: description.into(),
            unit: unit.into(),
            sum: Some(sum),
            histogram: None,
        }
    }

    /// Create a histogram metric.
    pub fn histogram(
        name: impl Into<String>,
        description: impl Into<String>,
        unit: impl Into<String>,
        hist: OtelHistogram,
    ) -> Self {
        Self {
            name: name.into(),
            description: description.into(),
            unit: unit.into(),
            sum: None,
            histogram: Some(hist),
        }
    }
}

/// OtelMetricData enum for convenient pattern matching.
#[derive(Debug, Clone)]
pub enum OtelMetricData {
    Sum(OtelSum),
    Histogram(OtelHistogram),
}

// ── Export request wrappers ─────────────────────────────────────────

fn build_resource_attributes(ctx: &ExportContext) -> Vec<OtelAttribute> {
    let hostname = std::env::var("HOSTNAME")
        .or_else(|_| crate::hostname_from_system())
        .unwrap_or_else(|_| "unknown".to_string());

    let mut attrs = vec![
        OtelAttribute::string("service.name", ctx.agent_product),
        OtelAttribute::string("service.version", ctx.build_version),
        OtelAttribute::string("ant.agent.instance", &hostname),
        OtelAttribute::string("ant.agent.product", ctx.agent_product),
        OtelAttribute::string("ant.agent.type", ctx.agent_type),
        OtelAttribute::string("telemetry.sdk.language", "rust"),
        OtelAttribute::string("telemetry.sdk.name", ctx.sdk_name),
        OtelAttribute::string("telemetry.sdk.version", ctx.build_version),
    ];
    attrs.push(OtelAttribute::string("ant.agent.env", ctx.agent_env));
    attrs.push(OtelAttribute::string(
        "deployment.environment",
        ctx.agent_env,
    ));
    attrs.push(OtelAttribute::string("ant.agent.scene", ctx.agent_scene));
    let (overrides, meta_trigger) = resolve_resource_overrides(ctx);
    if !overrides.is_empty() {
        let override_keys: Vec<&str> = overrides.iter().map(|(k, _)| k.as_str()).collect();
        attrs.retain(|a| !override_keys.contains(&a.key.as_str()));
        for (key, value) in overrides {
            attrs.push(OtelAttribute::string(&key, &value));
        }
    }
    if let Some(trigger) = meta_trigger {
        attrs.retain(|attr| attr.key != ANT_META_TRIGGER_KEY);
        attrs.push(OtelAttribute::string(ANT_META_TRIGGER_KEY, trigger));
    }
    // Collection mode is derived from the agent's final privacy configuration,
    // so generic Resource overrides must not make it disagree with filtering.
    attrs.retain(|attr| attr.key != "agentic.collection.mode");
    attrs.push(OtelAttribute::string(
        "agentic.collection.mode",
        ctx.collection_mode,
    ));
    attrs
}

/// Resolve component Resource overrides and consume reserved trigger input.
///
/// `ant.meta.trigger` is returned separately so callers cannot accidentally
/// apply an unvalidated value or bypass the dedicated/default emission rules.
pub(crate) fn resolve_resource_overrides(
    ctx: &ExportContext,
) -> (Vec<(String, String)>, Option<String>) {
    let agent_var = format!("{}_OTEL_RESOURCE_ATTRIBUTES", ctx.sdk_name.to_uppercase());
    let mut overrides = parse_otel_resource_attributes(&agent_var);
    let meta_trigger = resolve_meta_trigger(ctx, &overrides);
    overrides.retain(|(key, _)| key != ANT_META_TRIGGER_KEY);
    (overrides, meta_trigger)
}

fn resolve_meta_trigger(
    ctx: &ExportContext,
    resource_overrides: &[(String, String)],
) -> Option<String> {
    let resource_trigger = resource_overrides
        .iter()
        .rev()
        .find(|(key, _)| key == ANT_META_TRIGGER_KEY)
        .map(|(_, value)| value.as_str());
    if !ctx.emit_meta_trigger {
        return resource_trigger.map(normalize_meta_trigger_value);
    }
    if let Some(value) = std::env::var_os(AIVISION_META_TRIGGER_ENV) {
        return Some(normalize_meta_trigger_value(&value.to_string_lossy()));
    }
    match resource_trigger {
        Some(value) => Some(normalize_meta_trigger_value(value)),
        // Absence is distinct from an explicitly empty value: the former is
        // unspecified, while the latter is invalid caller input.
        None => Some(String::new()),
    }
}

fn normalize_meta_trigger_value(value: &str) -> String {
    match value.trim() {
        "automation" | "interactive" | "unknown" => value.trim().to_string(),
        _ => "unknown".to_string(),
    }
}

/// Parse `{AGENT}_OTEL_RESOURCE_ATTRIBUTES` into resource-level key-value pairs.
pub fn parse_otel_resource_attributes(agent_var: &str) -> Vec<(String, String)> {
    let raw = match std::env::var(agent_var).ok().filter(|v| !v.is_empty()) {
        Some(v) => v,
        None => return vec![],
    };
    raw.split(',')
        .filter_map(|pair| {
            let (k, v) = pair.split_once('=')?;
            let k = k.trim();
            let v = v.trim();
            if k.is_empty() {
                return None;
            }
            Some((k.to_string(), v.to_string()))
        })
        .collect()
}

/// Extract username from `{AGENT}_OTEL_ATTRIBUTES` for span-level `ant.username`.
/// Recognizes both `username=xxx` and `ant.username=xxx` keys.
pub fn parse_otel_span_username(agent_attr_var: &str) -> Option<String> {
    let raw = std::env::var(agent_attr_var)
        .ok()
        .filter(|v| !v.is_empty())?;
    raw.split(',')
        .filter_map(|pair| {
            let (k, v) = pair.split_once('=')?;
            let k = k.trim();
            let v = v.trim();
            if (k == "username" || k == "ant.username") && !v.is_empty() {
                Some(v.to_string())
            } else {
                None
            }
        })
        .next()
}

/// Parse `{AGENT}_OTEL_ATTRIBUTES` into span-level key-value pairs.
pub fn parse_otel_span_attributes(agent_attr_var: &str) -> Vec<(String, String)> {
    let raw = match std::env::var(agent_attr_var).ok().filter(|v| !v.is_empty()) {
        Some(v) => v,
        None => return vec![],
    };
    raw.split(',')
        .filter_map(|pair| {
            let (k, v) = pair.split_once('=')?;
            let k = k.trim();
            let v = v.trim();
            if k.is_empty() {
                return None;
            }
            Some((k.to_string(), v.to_string()))
        })
        .collect()
}

/// Canonicalize span attribute keys parsed from `{AGENT}_OTEL_ATTRIBUTES`.
///
/// `username` is kept as a compatibility alias for the AI Vision username key.
pub fn canonical_otel_span_attr_key(key: &str) -> &str {
    if key == "username" {
        "ant.username"
    } else {
        key
    }
}

/// Append parsed `{AGENT}_OTEL_ATTRIBUTES` to typed OTLP span attributes.
///
/// Existing span keys win: this helper never overwrites or duplicates a key
/// already present on the span after canonicalization.
pub fn append_otel_span_attributes(
    attributes: &mut Vec<OtelAttribute>,
    extra_attrs: &[(String, String)],
) {
    for (key, value) in extra_attrs {
        let key = canonical_otel_span_attr_key(key);
        if attributes.iter().any(|attr| attr.key == key) {
            continue;
        }
        attributes.push(OtelAttribute::string(key, value.as_str()));
    }
}

/// Append parsed `{AGENT}_OTEL_ATTRIBUTES` to JSON OTLP span attributes.
///
/// This is used by code paths that transform already-serialized OTLP spans.
/// Existing span keys win, matching `append_otel_span_attributes`.
pub fn append_otel_span_attributes_json(
    attributes: &mut Vec<Value>,
    extra_attrs: &[(String, String)],
) {
    for (key, value) in extra_attrs {
        let key = canonical_otel_span_attr_key(key);
        if attributes
            .iter()
            .any(|attr| attr.get("key").and_then(|v| v.as_str()) == Some(key))
        {
            continue;
        }
        attributes.push(json!({"key": key, "value": {"stringValue": value}}));
    }
}

/// Parse `{AGENT}_OTEL_EXPORTER_OTLP_HEADERS` into HTTP header key-value pairs.
pub fn parse_otel_exporter_headers(agent_header_var: &str) -> Vec<(String, String)> {
    let managed = matches!(agent_header_var, "CDXHOOKS_OTEL_EXPORTER_OTLP_HEADERS" | "CCHOOKS_OTEL_EXPORTER_OTLP_HEADERS")
        && std::env::var("AIVISION_MANAGED_INGEST").ok().as_deref() == Some("1");
    let header = if managed {
        std::env::var("AIVISION_INGEST_CONFIG").ok().and_then(|path|
            crate::ingest_auth::current_header(std::path::Path::new(&path)))
    } else { std::env::var(agent_header_var).ok() };
    let raw = match header
        .filter(|v| !v.is_empty())
    {
        Some(v) => v,
        None => return vec![],
    };
    parse_http_headers(&raw)
}

/// Parse comma-separated `key=value` HTTP headers, preserving `=` in values.
pub fn parse_http_headers(raw: &str) -> Vec<(String, String)> {
    raw.split(',')
        .filter_map(|pair| {
            let (k, v) = pair.split_once('=')?;
            let k = k.trim();
            let v = v.trim();
            if k.is_empty() {
                return None;
            }
            Some((k.to_string(), v.to_string()))
        })
        .collect()
}

/// Read `{AGENT}_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` as an override URL for trace export.
pub fn parse_otel_traces_endpoint(agent_var: &str) -> Option<String> {
    std::env::var(agent_var).ok().filter(|v| !v.is_empty())
}

/// Check `{AGENT}_ENABLE_TELEMETRY`. Returns false only when explicitly set to "false" or "0".
pub fn is_telemetry_enabled(agent_var: &str) -> bool {
    match std::env::var(agent_var).ok().filter(|v| !v.is_empty()) {
        Some(v) => !v.eq_ignore_ascii_case("false") && v != "0",
        None => true,
    }
}

/// Wrap typed spans into an OTLP ExportTraceServiceRequest JSON.
pub fn wrap_trace_export(spans: Vec<OtelSpan>, ctx: &ExportContext) -> Value {
    let resource_attributes = build_resource_attributes(ctx);
    crate::privacy::protect_export(json!({
        "resourceSpans": [{
            "resource": {
                "env": ctx.agent_env,
                "attributes": resource_attributes
            },
            "scopeSpans": [{
                "scope": {
                    "name": ctx.sdk_name,
                    "version": ctx.build_version
                },
                "spans": spans
            }]
        }]
    }), ctx.collection_mode)
}

/// Wrap typed log records into an OTLP ExportLogsServiceRequest JSON.
pub fn wrap_logs_export(log_records: Vec<OtelLogRecord>, ctx: &ExportContext) -> Value {
    let resource_attributes = build_resource_attributes(ctx);
    crate::privacy::protect_export(json!({
        "resourceLogs": [{
            "resource": {
                "env": ctx.agent_env,
                "attributes": resource_attributes
            },
            "scopeLogs": [{
                "scope": {
                    "name": ctx.sdk_name,
                    "version": ctx.build_version
                },
                "logRecords": log_records
            }]
        }]
    }), ctx.collection_mode)
}

/// Wrap typed metrics into an OTLP ExportMetricsServiceRequest JSON.
pub fn wrap_metrics_export(metrics: Vec<OtelMetric>, ctx: &ExportContext) -> Value {
    let resource_attributes = build_resource_attributes(ctx);
    crate::privacy::protect_export(json!({
        "resourceMetrics": [{
            "resource": {
                "env": ctx.agent_env,
                "attributes": resource_attributes
            },
            "scopeMetrics": [{
                "scope": {
                    "name": ctx.sdk_name,
                    "version": ctx.build_version
                },
                "metrics": metrics
            }]
        }]
    }), ctx.collection_mode)
}

// ── Tests ───────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use std::ffi::OsString;

    #[test]
    fn test_otel_attribute_string() {
        let attr = OtelAttribute::string("key1", "val1");
        let json = serde_json::to_value(&attr).unwrap();
        assert_eq!(json["key"], "key1");
        assert_eq!(json["value"]["stringValue"], "val1");
    }

    #[test]
    fn test_otel_attribute_int() {
        let attr = OtelAttribute::int("count", 42);
        let json = serde_json::to_value(&attr).unwrap();
        assert_eq!(json["key"], "count");
        assert_eq!(json["value"]["intValue"], 42);
    }

    #[test]
    fn test_otel_attribute_int_as_string() {
        let attr = OtelAttribute::int_as_string("tokens", 100);
        let json = serde_json::to_value(&attr).unwrap();
        assert_eq!(json["key"], "tokens");
        assert_eq!(json["value"]["stringValue"], "100");
    }

    #[test]
    fn test_otel_attribute_bool() {
        let attr = OtelAttribute::bool("enabled", true);
        let json = serde_json::to_value(&attr).unwrap();
        assert_eq!(json["key"], "enabled");
        assert_eq!(json["value"]["boolValue"], true);
    }

    #[test]
    fn test_otel_status_ok() {
        let status = OtelStatus::ok();
        let json = serde_json::to_value(&status).unwrap();
        assert_eq!(json["code"], 1);
        assert!(json.get("message").is_none());
    }

    #[test]
    fn test_otel_status_error() {
        let status = OtelStatus::error("something failed");
        let json = serde_json::to_value(&status).unwrap();
        assert_eq!(json["code"], 2);
        assert_eq!(json["message"], "something failed");
    }

    #[test]
    fn test_otel_status_unset() {
        let status = OtelStatus::unset();
        let json = serde_json::to_value(&status).unwrap();
        assert_eq!(json["code"], 0);
        assert!(json.get("message").is_none());
    }

    #[test]
    fn test_otel_span_serialization() {
        let span = OtelSpan {
            trace_id: "abcd1234".to_string(),
            span_id: "span0001".to_string(),
            parent_span_id: "parent01".to_string(),
            name: "ai_agent.turn".to_string(),
            kind: SPAN_KIND_SERVER,
            start_time_unix_nano: "1000000000".to_string(),
            end_time_unix_nano: "2000000000".to_string(),
            attributes: vec![
                OtelAttribute::string("session.id", "sess-1"),
                OtelAttribute::int_as_string("turn_number", 1),
            ],
            status: OtelStatus::ok(),
        };
        let json = serde_json::to_value(&span).unwrap();

        // Verify camelCase field names
        assert_eq!(json["traceId"], "abcd1234");
        assert_eq!(json["spanId"], "span0001");
        assert_eq!(json["parentSpanId"], "parent01");
        assert_eq!(json["startTimeUnixNano"], "1000000000");
        assert_eq!(json["endTimeUnixNano"], "2000000000");
        assert_eq!(json["name"], "ai_agent.turn");
        assert_eq!(json["kind"], SPAN_KIND_SERVER);
        assert_eq!(json["status"]["code"], 1);
        assert_eq!(json["attributes"][0]["key"], "session.id");

        let decoded: OtelSpan = serde_json::from_value(json).unwrap();
        assert_eq!(decoded.parent_span_id, "parent01");
    }

    #[test]
    fn test_root_otel_span_roundtrip_defaults_missing_parent_span_id() {
        let span = OtelSpan {
            trace_id: "abcd1234".to_string(),
            span_id: "span0001".to_string(),
            parent_span_id: String::new(),
            name: "ai_agent.turn".to_string(),
            kind: SPAN_KIND_SERVER,
            start_time_unix_nano: "1000000000".to_string(),
            end_time_unix_nano: "2000000000".to_string(),
            attributes: vec![],
            status: OtelStatus::ok(),
        };

        let json = serde_json::to_value(&span).unwrap();
        assert!(json.get("parentSpanId").is_none());

        let decoded: OtelSpan = serde_json::from_value(json).unwrap();
        assert!(decoded.parent_span_id.is_empty());
    }

    #[test]
    fn test_otel_log_record_event() {
        let record = OtelLogRecord::event(
            "user_prompt",
            "1000000000",
            vec![
                OtelAttribute::string("gen_ai.operation.name", "chat"),
                OtelAttribute::string("ant.username", "test-user"),
            ],
        );
        let json = serde_json::to_value(&record).unwrap();

        // Verify camelCase field names
        assert_eq!(json["timeUnixNano"], "1000000000");
        assert_eq!(json["observedTimeUnixNano"], "1000000000");
        assert_eq!(json["droppedAttributesCount"], 0);

        // Verify body structure
        let body = &json["body"];
        assert_eq!(body["kvlistValue"]["values"][0]["key"], "event_name");
        assert_eq!(
            body["kvlistValue"]["values"][0]["value"]["stringValue"],
            "user_prompt"
        );

        // Verify attributes
        assert_eq!(json["attributes"][0]["key"], "gen_ai.operation.name");
    }

    #[test]
    fn test_wrap_trace_export() {
        let spans = vec![OtelSpan {
            trace_id: "t1".to_string(),
            span_id: "s1".to_string(),
            parent_span_id: "".to_string(),
            name: "test".to_string(),
            kind: SPAN_KIND_INTERNAL,
            start_time_unix_nano: "100".to_string(),
            end_time_unix_nano: "200".to_string(),
            attributes: vec![],
            status: OtelStatus::ok(),
        }];
        let ctx = ExportContext {
            agent_product: "TestAgent",
            agent_type: "test",
            sdk_name: "test-hooks",
            build_version: "0.1.0",
            agent_env: "",
            agent_scene: "",
            collection_mode: "full",
            emit_meta_trigger: false,
        };
        let req = wrap_trace_export(spans, &ctx);

        assert!(req["resourceSpans"].is_array());
        let rs = &req["resourceSpans"][0];
        assert!(rs["resource"]["attributes"].is_array());
        let scope = &rs["scopeSpans"][0];
        assert_eq!(scope["scope"]["name"], "test-hooks");
        assert_eq!(scope["spans"][0]["traceId"], "t1");
    }

    #[test]
    fn test_collection_mode_cannot_be_overridden_by_resource_attributes() {
        const ENV_KEY: &str = "COLLECTIONTEST_OTEL_RESOURCE_ATTRIBUTES";
        std::env::set_var(
            ENV_KEY,
            "agentic.collection.mode=privacy,custom.resource=value",
        );
        let ctx = ExportContext {
            agent_product: "TestAgent",
            agent_type: "test",
            sdk_name: "collectiontest",
            build_version: "0.1.0",
            agent_env: "",
            agent_scene: "",
            collection_mode: "full",
            emit_meta_trigger: false,
        };
        let req = wrap_trace_export(vec![], &ctx);
        std::env::remove_var(ENV_KEY);

        let attrs = req["resourceSpans"][0]["resource"]["attributes"]
            .as_array()
            .unwrap();
        let collection_modes: Vec<_> = attrs
            .iter()
            .filter(|attr| attr["key"] == "agentic.collection.mode")
            .collect();
        assert_eq!(collection_modes.len(), 1);
        assert_eq!(
            collection_modes[0]["value"]["stringValue"],
            serde_json::json!("full")
        );
        assert!(attrs.iter().any(|attr| {
            attr["key"] == "custom.resource" && attr["value"]["stringValue"] == "value"
        }));
    }

    fn set_or_remove_env(key: &str, value: Option<&str>) {
        if let Some(value) = value {
            std::env::set_var(key, value);
        } else {
            std::env::remove_var(key);
        }
    }

    fn restore_env(key: &str, value: Option<OsString>) {
        if let Some(value) = value {
            std::env::set_var(key, value);
        } else {
            std::env::remove_var(key);
        }
    }

    fn with_meta_trigger_env<T>(
        sdk_resource_env: &str,
        dedicated: Option<&str>,
        resource_attrs: Option<&str>,
        f: impl FnOnce() -> T,
    ) -> T {
        let _guard = META_TRIGGER_ENV_LOCK.lock().unwrap();
        let old_dedicated = std::env::var_os(AIVISION_META_TRIGGER_ENV);
        let old_resource = std::env::var_os(sdk_resource_env);
        set_or_remove_env(AIVISION_META_TRIGGER_ENV, dedicated);
        set_or_remove_env(sdk_resource_env, resource_attrs);
        let result = f();
        restore_env(AIVISION_META_TRIGGER_ENV, old_dedicated);
        restore_env(sdk_resource_env, old_resource);
        result
    }

    fn trigger_values(attrs: &[Value]) -> Vec<&str> {
        attrs
            .iter()
            .filter(|attr| attr["key"] == ANT_META_TRIGGER_KEY)
            .filter_map(|attr| attr["value"]["stringValue"].as_str())
            .collect()
    }

    fn trace_resource_attrs(req: &Value) -> &[Value] {
        req["resourceSpans"][0]["resource"]["attributes"]
            .as_array()
            .unwrap()
    }

    fn logs_resource_attrs(req: &Value) -> &[Value] {
        req["resourceLogs"][0]["resource"]["attributes"]
            .as_array()
            .unwrap()
    }

    fn metrics_resource_attrs(req: &Value) -> &[Value] {
        req["resourceMetrics"][0]["resource"]["attributes"]
            .as_array()
            .unwrap()
    }

    fn meta_trigger_test_context<'a>(
        sdk_name: &'a str,
        emit_meta_trigger: bool,
    ) -> ExportContext<'a> {
        ExportContext {
            agent_product: "TestAgent",
            agent_type: "test",
            sdk_name,
            build_version: "0.1.0",
            agent_env: "",
            agent_scene: "",
            collection_mode: "full",
            emit_meta_trigger,
        }
    }

    #[test]
    fn test_meta_trigger_dedicated_env_accepts_canonical_values() {
        with_meta_trigger_env(
            "METATRIGGERVALUES_OTEL_RESOURCE_ATTRIBUTES",
            None,
            None,
            || {
                let ctx = meta_trigger_test_context("metatriggervalues", true);
                for value in ["automation", "interactive", "unknown"] {
                    std::env::set_var(AIVISION_META_TRIGGER_ENV, value);
                    let req = wrap_trace_export(vec![], &ctx);
                    assert_eq!(trigger_values(trace_resource_attrs(&req)), vec![value]);
                }
            },
        );
    }

    #[test]
    fn test_meta_trigger_trims_dedicated_env_value() {
        with_meta_trigger_env(
            "METATRIGGERTRIM_OTEL_RESOURCE_ATTRIBUTES",
            Some(" automation "),
            None,
            || {
                let ctx = meta_trigger_test_context("metatriggertrim", true);
                let req = wrap_trace_export(vec![], &ctx);
                assert_eq!(
                    trigger_values(trace_resource_attrs(&req)),
                    vec!["automation"]
                );
            },
        );
    }

    #[test]
    fn test_meta_trigger_falls_back_to_component_resource_attribute() {
        with_meta_trigger_env(
            "METATRIGGERFALLBACK_OTEL_RESOURCE_ATTRIBUTES",
            None,
            Some("ant.meta.trigger=automation"),
            || {
                let ctx = meta_trigger_test_context("metatriggerfallback", true);
                let req = wrap_trace_export(vec![], &ctx);
                assert_eq!(
                    trigger_values(trace_resource_attrs(&req)),
                    vec!["automation"]
                );
            },
        );
    }

    #[test]
    fn test_invalid_dedicated_meta_trigger_does_not_fallback_to_resource_attr() {
        with_meta_trigger_env(
            "METATRIGGERDEDICATEDINVALID_OTEL_RESOURCE_ATTRIBUTES",
            Some("scheduled"),
            Some("ant.meta.trigger=automation"),
            || {
                let ctx = meta_trigger_test_context("metatriggerdedicatedinvalid", true);
                let req = wrap_trace_export(vec![], &ctx);
                assert_eq!(trigger_values(trace_resource_attrs(&req)), vec!["unknown"]);
            },
        );
    }

    #[test]
    fn test_meta_trigger_missing_resolves_empty_and_invalid_resolves_unknown() {
        with_meta_trigger_env(
            "METATRIGGERUNKNOWN_OTEL_RESOURCE_ATTRIBUTES",
            None,
            None,
            || {
                let ctx = meta_trigger_test_context("metatriggerunknown", true);
                let req = wrap_trace_export(vec![], &ctx);
                assert_eq!(trigger_values(trace_resource_attrs(&req)), vec![""]);
            },
        );
        with_meta_trigger_env(
            "METATRIGGERINVALID_OTEL_RESOURCE_ATTRIBUTES",
            None,
            Some("ant.meta.trigger=HUMAN"),
            || {
                let ctx = meta_trigger_test_context("metatriggerinvalid", true);
                let req = wrap_trace_export(vec![], &ctx);
                assert_eq!(trigger_values(trace_resource_attrs(&req)), vec!["unknown"]);
            },
        );
    }

    #[test]
    fn test_explicit_empty_meta_trigger_resolves_unknown() {
        with_meta_trigger_env(
            "METATRIGGEREMPTYENV_OTEL_RESOURCE_ATTRIBUTES",
            Some(""),
            None,
            || {
                let ctx = meta_trigger_test_context("metatriggeremptyenv", true);
                let req = wrap_trace_export(vec![], &ctx);
                assert_eq!(trigger_values(trace_resource_attrs(&req)), vec!["unknown"]);
            },
        );
        with_meta_trigger_env(
            "METATRIGGEREMPTYRESOURCE_OTEL_RESOURCE_ATTRIBUTES",
            None,
            Some("ant.meta.trigger="),
            || {
                let ctx = meta_trigger_test_context("metatriggeremptyresource", true);
                let req = wrap_trace_export(vec![], &ctx);
                assert_eq!(trigger_values(trace_resource_attrs(&req)), vec!["unknown"]);
            },
        );
    }

    #[test]
    fn test_meta_trigger_duplicate_component_key_uses_last_occurrence() {
        with_meta_trigger_env(
            "METATRIGGERDUPVALID_OTEL_RESOURCE_ATTRIBUTES",
            None,
            Some("ant.meta.trigger=interactive,custom.resource=value,ant.meta.trigger=automation"),
            || {
                let ctx = meta_trigger_test_context("metatriggerdupvalid", true);
                let req = wrap_trace_export(vec![], &ctx);
                assert_eq!(
                    trigger_values(trace_resource_attrs(&req)),
                    vec!["automation"]
                );
            },
        );
        with_meta_trigger_env(
            "METATRIGGERDUPINVALID_OTEL_RESOURCE_ATTRIBUTES",
            None,
            Some("ant.meta.trigger=automation,ant.meta.trigger=scheduled"),
            || {
                let ctx = meta_trigger_test_context("metatriggerdupinvalid", true);
                let req = wrap_trace_export(vec![], &ctx);
                assert_eq!(trigger_values(trace_resource_attrs(&req)), vec!["unknown"]);
            },
        );
    }

    #[test]
    fn test_meta_trigger_is_exactly_once_across_typed_signals() {
        with_meta_trigger_env(
            "METATRIGGERSIGNALS_OTEL_RESOURCE_ATTRIBUTES",
            Some("automation"),
            Some("ant.meta.trigger=interactive,ant.meta.trigger=unknown"),
            || {
                let ctx = meta_trigger_test_context("metatriggersignals", true);
                let trace_req = wrap_trace_export(vec![], &ctx);
                let logs_req = wrap_logs_export(vec![], &ctx);
                let metrics_req = wrap_metrics_export(vec![], &ctx);
                assert_eq!(
                    trigger_values(trace_resource_attrs(&trace_req)),
                    vec!["automation"]
                );
                assert_eq!(
                    trigger_values(logs_resource_attrs(&logs_req)),
                    vec!["automation"]
                );
                assert_eq!(
                    trigger_values(metrics_resource_attrs(&metrics_req)),
                    vec!["automation"]
                );
            },
        );
    }

    #[test]
    fn test_missing_meta_trigger_is_empty_exactly_once_across_typed_signals() {
        with_meta_trigger_env(
            "METATRIGGERDEFAULTSIGNALS_OTEL_RESOURCE_ATTRIBUTES",
            None,
            None,
            || {
                let ctx = meta_trigger_test_context("metatriggerdefaultsignals", true);
                let trace_req = wrap_trace_export(vec![], &ctx);
                let logs_req = wrap_logs_export(vec![], &ctx);
                let metrics_req = wrap_metrics_export(vec![], &ctx);
                assert_eq!(trigger_values(trace_resource_attrs(&trace_req)), vec![""]);
                assert_eq!(trigger_values(logs_resource_attrs(&logs_req)), vec![""]);
                assert_eq!(
                    trigger_values(metrics_resource_attrs(&metrics_req)),
                    vec![""]
                );
            },
        );
    }

    #[test]
    fn test_meta_trigger_opt_out_ignores_dedicated_env() {
        with_meta_trigger_env(
            "METATRIGGEROPTOUT_OTEL_RESOURCE_ATTRIBUTES",
            Some("automation"),
            None,
            || {
                let ctx = meta_trigger_test_context("metatriggeroptout", false);
                let typed_req = wrap_trace_export(vec![], &ctx);
                let legacy_req = crate::wrap_export_request(vec![], &ctx);
                assert!(trigger_values(trace_resource_attrs(&typed_req)).is_empty());
                assert!(trigger_values(trace_resource_attrs(&legacy_req)).is_empty());
            },
        );
    }

    #[test]
    fn test_meta_trigger_opt_out_normalizes_explicit_resource_override() {
        with_meta_trigger_env(
            "METATRIGGEROPTOUTOVERRIDE_OTEL_RESOURCE_ATTRIBUTES",
            Some("automation"),
            Some("custom.resource=value,ant.meta.trigger=scheduled"),
            || {
                let ctx = meta_trigger_test_context("metatriggeroptoutoverride", false);
                let typed_req = wrap_trace_export(vec![], &ctx);
                let legacy_req = crate::wrap_export_request(vec![], &ctx);
                assert_eq!(
                    trigger_values(trace_resource_attrs(&typed_req)),
                    vec!["unknown"]
                );
                assert_eq!(
                    trigger_values(trace_resource_attrs(&legacy_req)),
                    vec!["unknown"]
                );
                for attrs in [
                    trace_resource_attrs(&typed_req),
                    trace_resource_attrs(&legacy_req),
                ] {
                    assert!(attrs.iter().any(|attr| {
                        attr["key"] == "custom.resource" && attr["value"]["stringValue"] == "value"
                    }));
                }
            },
        );
    }

    #[test]
    fn test_meta_trigger_opt_out_preserves_valid_explicit_resource_override() {
        with_meta_trigger_env(
            "METATRIGGEROPTOUTVALID_OTEL_RESOURCE_ATTRIBUTES",
            Some("interactive"),
            Some("ant.meta.trigger=automation"),
            || {
                let ctx = meta_trigger_test_context("metatriggeroptoutvalid", false);
                let typed_req = wrap_trace_export(vec![], &ctx);
                let legacy_req = crate::wrap_export_request(vec![], &ctx);
                assert_eq!(
                    trigger_values(trace_resource_attrs(&typed_req)),
                    vec!["automation"]
                );
                assert_eq!(
                    trigger_values(trace_resource_attrs(&legacy_req)),
                    vec!["automation"]
                );
            },
        );
    }

    #[test]
    fn test_meta_trigger_typed_and_legacy_wrappers_share_resolution() {
        with_meta_trigger_env(
            "METATRIGGERPARITY_OTEL_RESOURCE_ATTRIBUTES",
            None,
            Some("ant.meta.trigger=interactive,ant.meta.trigger=automation"),
            || {
                let ctx = meta_trigger_test_context("metatriggerparity", true);
                let typed_req = wrap_trace_export(vec![], &ctx);
                let legacy_req = crate::wrap_export_request(vec![], &ctx);
                assert_eq!(
                    trigger_values(trace_resource_attrs(&typed_req)),
                    trigger_values(trace_resource_attrs(&legacy_req))
                );
                assert_eq!(
                    trigger_values(trace_resource_attrs(&legacy_req)),
                    vec!["automation"]
                );
            },
        );
    }

    #[test]
    fn test_wrap_logs_export() {
        let records = vec![OtelLogRecord::event(
            "tool_result",
            "300",
            vec![OtelAttribute::string("gen_ai.tool.name", "Read")],
        )];
        let ctx = ExportContext {
            agent_product: "TestAgent",
            agent_type: "test",
            sdk_name: "test-hooks",
            build_version: "0.1.0",
            agent_env: "",
            agent_scene: "",
            collection_mode: "full",
            emit_meta_trigger: false,
        };
        let req = wrap_logs_export(records, &ctx);

        assert!(req["resourceLogs"].is_array());
        let rl = &req["resourceLogs"][0];
        assert!(rl["resource"]["attributes"].is_array());
        let scope = &rl["scopeLogs"][0];
        assert_eq!(scope["scope"]["name"], "test-hooks");
        assert_eq!(
            scope["logRecords"][0]["body"]["kvlistValue"]["values"][0]["value"]["stringValue"],
            "tool_result"
        );
    }

    #[test]
    fn test_otel_metric_serialization() {
        let metric = OtelMetric::counter(
            "aivision.agentic.up",
            "Component invocation count",
            "{count}",
            OtelSum {
                data_points: vec![OtelNumberDataPoint {
                    attributes: vec![],
                    time_unix_nano: "1000000000".to_string(),
                    start_time_unix_nano: "1000000000".to_string(),
                    as_int: 1,
                }],
                aggregation_temporality: 2,
                is_monotonic: true,
            },
        );
        let json = serde_json::to_value(&metric).unwrap();

        assert_eq!(json["name"], "aivision.agentic.up");
        assert_eq!(json["unit"], "{count}");
        assert_eq!(json["sum"]["aggregationTemporality"], 2);
        assert_eq!(json["sum"]["isMonotonic"], true);
        assert_eq!(json["sum"]["dataPoints"][0]["asInt"], 1);
        assert_eq!(json["sum"]["dataPoints"][0]["timeUnixNano"], "1000000000");
    }

    #[test]
    fn test_otel_metric_histogram_serialization() {
        let metric = OtelMetric::histogram(
            "agent.tool_call.duration",
            "Tool call duration",
            "ms",
            OtelHistogram {
                data_points: vec![OtelHistogramDataPoint {
                    attributes: vec![OtelAttribute::string("agentic.tool.type", "builtin")],
                    start_time_unix_nano: "1000".to_string(),
                    time_unix_nano: "2000".to_string(),
                    count: 3,
                    sum: 450.0,
                    min: Some(50.0),
                    max: Some(300.0),
                    explicit_bounds: vec![100.0, 500.0, 1000.0, f64::INFINITY],
                    bucket_counts: vec![1, 2, 0, 0],
                }],
                aggregation_temporality: 2,
            },
        );
        let json = serde_json::to_value(&metric).unwrap();

        assert_eq!(json["name"], "agent.tool_call.duration");
        assert!(json.get("histogram").is_some());
        assert!(json.get("sum").is_none());
        assert_eq!(json["histogram"]["aggregationTemporality"], 2);
        assert_eq!(json["histogram"]["dataPoints"][0]["count"], 3);
        assert_eq!(json["histogram"]["dataPoints"][0]["sum"], 450.0);
    }

    #[test]
    fn test_wrap_metrics_export() {
        let metrics = vec![OtelMetric::counter(
            "aivision.agentic.up",
            "test",
            "{count}",
            OtelSum {
                data_points: vec![OtelNumberDataPoint {
                    attributes: vec![],
                    time_unix_nano: "100".to_string(),
                    start_time_unix_nano: "100".to_string(),
                    as_int: 1,
                }],
                aggregation_temporality: 2,
                is_monotonic: true,
            },
        )];
        let ctx = ExportContext {
            agent_product: "TestAgent",
            agent_type: "test",
            sdk_name: "test-hooks",
            build_version: "0.1.0",
            agent_env: "",
            agent_scene: "",
            collection_mode: "full",
            emit_meta_trigger: false,
        };
        let req = wrap_metrics_export(metrics, &ctx);

        assert!(req["resourceMetrics"].is_array());
        let rm = &req["resourceMetrics"][0];
        assert!(rm["resource"]["attributes"].is_array());
        let scope = &rm["scopeMetrics"][0];
        assert_eq!(scope["scope"]["name"], "test-hooks");
        assert_eq!(scope["metrics"][0]["name"], "aivision.agentic.up");
        assert_eq!(scope["metrics"][0]["sum"]["dataPoints"][0]["asInt"], 1);
    }

    #[test]
    fn test_parse_otel_resource_attributes_basic() {
        std::env::set_var(
            "TEST_OTEL_RESOURCE_ATTRIBUTES",
            "ant.agent.type=claudecode,ant.agent.product=ClaudeCode",
        );
        let parsed = parse_otel_resource_attributes("TEST_OTEL_RESOURCE_ATTRIBUTES");
        assert_eq!(parsed.len(), 2);
        assert_eq!(parsed[0], ("ant.agent.type".into(), "claudecode".into()));
        assert_eq!(parsed[1], ("ant.agent.product".into(), "ClaudeCode".into()));
        std::env::remove_var("TEST_OTEL_RESOURCE_ATTRIBUTES");
    }

    #[test]
    fn test_parse_otel_resource_attributes_empty() {
        std::env::remove_var("TEST_OTEL_RESOURCE_ATTRIBUTES");
        let parsed = parse_otel_resource_attributes("TEST_OTEL_RESOURCE_ATTRIBUTES");
        assert!(parsed.is_empty());
    }

    #[test]
    fn test_parse_otel_resource_attributes_value_with_equals() {
        std::env::set_var("TEST_OTEL_RESOURCE_ATTRIBUTES", "auth=Basic dXNlcjpwYXNz");
        let parsed = parse_otel_resource_attributes("TEST_OTEL_RESOURCE_ATTRIBUTES");
        assert_eq!(parsed.len(), 1);
        assert_eq!(parsed[0], ("auth".into(), "Basic dXNlcjpwYXNz".into()));
        std::env::remove_var("TEST_OTEL_RESOURCE_ATTRIBUTES");
    }

    #[test]
    fn test_parse_otel_span_username_short_key() {
        std::env::set_var("TEST_OTEL_ATTRIBUTES", "username=100001,foo=bar");
        let username = parse_otel_span_username("TEST_OTEL_ATTRIBUTES");
        assert_eq!(username, Some("100001".into()));
        std::env::remove_var("TEST_OTEL_ATTRIBUTES");
    }

    #[test]
    fn test_parse_otel_span_username_dotted_key() {
        std::env::set_var("TEST_OTEL_ATTRIBUTES", "ant.username=100001");
        let username = parse_otel_span_username("TEST_OTEL_ATTRIBUTES");
        assert_eq!(username, Some("100001".into()));
        std::env::remove_var("TEST_OTEL_ATTRIBUTES");
    }

    #[test]
    fn test_parse_otel_span_username_missing() {
        std::env::remove_var("TEST_OTEL_ATTRIBUTES");
        let username = parse_otel_span_username("TEST_OTEL_ATTRIBUTES");
        assert_eq!(username, None);
    }

    #[test]
    fn test_parse_otel_span_attributes() {
        std::env::set_var(
            "TEST_OTEL_ATTRIBUTES",
            "ant.username=100001,custom.tag=value",
        );
        let attrs = parse_otel_span_attributes("TEST_OTEL_ATTRIBUTES");
        assert_eq!(attrs.len(), 2);
        assert_eq!(attrs[0], ("ant.username".into(), "100001".into()));
        assert_eq!(attrs[1], ("custom.tag".into(), "value".into()));
        std::env::remove_var("TEST_OTEL_ATTRIBUTES");
    }

    #[test]
    fn test_parse_otel_span_attributes_empty() {
        std::env::remove_var("TEST_OTEL_ATTRIBUTES");
        let attrs = parse_otel_span_attributes("TEST_OTEL_ATTRIBUTES");
        assert!(attrs.is_empty());
    }

    #[test]
    fn test_append_otel_span_attributes_canonicalizes_username_and_skips_existing_keys() {
        let extra_attrs = vec![
            ("username".to_string(), "100001".to_string()),
            ("custom.tag".to_string(), "value".to_string()),
            (
                "gen_ai.operation.name".to_string(),
                "override-attempt".to_string(),
            ),
        ];
        let mut attrs = vec![OtelAttribute::string("gen_ai.operation.name", "chat")];

        append_otel_span_attributes(&mut attrs, &extra_attrs);

        assert!(attrs.iter().any(|attr| attr.key == "ant.username"));
        assert!(attrs.iter().any(|attr| attr.key == "custom.tag"));
        assert!(!attrs.iter().any(|attr| attr.key == "username"));
        assert_eq!(
            attrs
                .iter()
                .filter(|attr| attr.key == "gen_ai.operation.name")
                .count(),
            1,
            "existing span keys must not be duplicated"
        );
    }

    #[test]
    fn test_append_otel_span_attributes_json_canonicalizes_username_and_skips_existing_keys() {
        let extra_attrs = vec![
            ("username".to_string(), "100001".to_string()),
            ("custom.tag".to_string(), "value".to_string()),
            (
                "gen_ai.operation.name".to_string(),
                "override-attempt".to_string(),
            ),
        ];
        let mut attrs = vec![json!({
            "key": "gen_ai.operation.name",
            "value": {"stringValue": "chat"}
        })];

        append_otel_span_attributes_json(&mut attrs, &extra_attrs);

        assert!(attrs
            .iter()
            .any(|attr| attr["key"].as_str() == Some("ant.username")));
        assert!(attrs
            .iter()
            .any(|attr| attr["key"].as_str() == Some("custom.tag")));
        assert!(!attrs
            .iter()
            .any(|attr| attr["key"].as_str() == Some("username")));
        assert_eq!(
            attrs
                .iter()
                .filter(|attr| attr["key"].as_str() == Some("gen_ai.operation.name"))
                .count(),
            1,
            "existing JSON span keys must not be duplicated"
        );
    }

    #[test]
    fn test_parse_otel_exporter_headers() {
        std::env::set_var(
            "TEST_OTEL_EXPORTER_OTLP_HEADERS",
            "x-forward-service=gw-local,x-token=abc123",
        );
        let headers = parse_otel_exporter_headers("TEST_OTEL_EXPORTER_OTLP_HEADERS");
        assert_eq!(headers.len(), 2);
        assert_eq!(headers[0], ("x-forward-service".into(), "gw-local".into()));
        assert_eq!(headers[1], ("x-token".into(), "abc123".into()));
        std::env::remove_var("TEST_OTEL_EXPORTER_OTLP_HEADERS");
    }

    #[test]
    fn test_parse_otel_exporter_headers_empty() {
        std::env::remove_var("TEST_OTEL_EXPORTER_OTLP_HEADERS");
        let headers = parse_otel_exporter_headers("TEST_OTEL_EXPORTER_OTLP_HEADERS");
        assert!(headers.is_empty());
    }
}
