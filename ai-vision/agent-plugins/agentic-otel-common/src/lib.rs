//! Shared OTel utilities for agentic hooks (cchooks, cdxhooks, etc.)
//!
//! This crate extracts the common OTel building blocks that are identical
//! across multiple agent hook implementations, eliminating duplication and
//! ensuring consistent behavior.

pub mod dynamic;
pub mod hook;
pub mod ingest_auth;
pub mod logger;
pub mod message;
pub mod media;
pub mod metrics;
pub mod otlp;
pub mod privacy;
pub mod state;

pub use hook::*;
pub use logger::*;
pub use message::*;
pub use state::*;

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

// ── Internal semantic markers ───────────────────────────────────────

/// Attribute key used to mark the logical root span of an agent turn.
///
/// This internal marker is owned by the hook implementation and must not be
/// overridden by operator-provided OTEL attribute environment variables.
pub const INTERNAL_META_ROOT_KEY: &str = "_internal.meta.root";

/// Attribute value used with [`INTERNAL_META_ROOT_KEY`] for logical root spans.
pub const INTERNAL_META_ROOT_VALUE: &str = "1";

/// Canonical trigger mechanism observed for a logical agent turn.
///
/// Host-specific transcript parsers map their source values into this shared
/// semantic enum before span construction.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub enum TurnTrigger {
    Interactive,
    Programmatic,
    Queued,
    System,
    Subagent,
    #[default]
    Unknown,
}

impl TurnTrigger {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Interactive => "interactive",
            Self::Programmatic => "programmatic",
            Self::Queued => "queued",
            Self::System => "system",
            Self::Subagent => "subagent",
            Self::Unknown => "unknown",
        }
    }
}

// ── Random / ID generation ──────────────────────────────────────────

pub fn fill_random_bytes(buf: &mut [u8]) {
    #[cfg(unix)]
    {
        use std::fs::File;
        use std::io::Read;
        if let Ok(mut file) = File::open("/dev/urandom") {
            let _ = file.read_exact(buf);
            return;
        }
    }
    let seed = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .subsec_nanos();
    let mut state = seed as u64 ^ 0xdeadbeefcafe;
    for byte in buf.iter_mut() {
        state ^= state << 13;
        state ^= state >> 7;
        state ^= state << 17;
        *byte = (state & 0xff) as u8;
    }
}

/// Generate a 32-char hex trace ID (16 random bytes).
pub fn otel_trace_id() -> String {
    let mut bytes = [0u8; 16];
    fill_random_bytes(&mut bytes);
    bytes.iter().map(|b| format!("{:02x}", b)).collect()
}

/// Generate a 16-char hex span ID (8 random bytes).
pub fn otel_span_id() -> String {
    let mut bytes = [0u8; 8];
    fill_random_bytes(&mut bytes);
    bytes.iter().map(|b| format!("{:02x}", b)).collect()
}

/// Simple UUID v4 using system randomness via /dev/urandom on Unix.
pub fn uuid_v4() -> String {
    let mut bytes = [0u8; 16];
    fill_random_bytes(&mut bytes);
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    format!(
        "{:02x}{:02x}{:02x}{:02x}-{:02x}{:02x}-{:02x}{:02x}-{:02x}{:02x}-{:02x}{:02x}{:02x}{:02x}{:02x}{:02x}",
        bytes[0], bytes[1], bytes[2], bytes[3],
        bytes[4], bytes[5],
        bytes[6], bytes[7],
        bytes[8], bytes[9],
        bytes[10], bytes[11], bytes[12], bytes[13], bytes[14], bytes[15]
    )
}

// ── W3C TRACEPARENT parsing ─────────────────────────────────────────

/// Parsed W3C TRACEPARENT context.
/// Format: `{version}-{trace_id}-{parent_span_id}-{trace_flags}`
/// Example: `00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01`
#[derive(Debug, Clone)]
pub struct TraceParent {
    pub trace_id: String,
    pub parent_span_id: String,
    pub trace_flags: u8,
}

/// Parse W3C TRACEPARENT from the `TRACEPARENT` environment variable.
/// Returns `None` if the env var is absent or the value is malformed.
pub fn parse_traceparent_env() -> Option<TraceParent> {
    parse_traceparent(&std::env::var("TRACEPARENT").ok()?)
}

/// Parse a W3C TRACEPARENT string value.
pub fn parse_traceparent(val: &str) -> Option<TraceParent> {
    let parts: Vec<&str> = val.split('-').collect();
    if parts.len() != 4 {
        return None;
    }
    let (version, trace_id, parent_span_id, flags_str) = (parts[0], parts[1], parts[2], parts[3]);

    if version.len() != 2 {
        return None;
    }
    if trace_id.len() != 32 || !trace_id.bytes().all(|b| b.is_ascii_hexdigit()) {
        return None;
    }
    if parent_span_id.len() != 16 || !parent_span_id.bytes().all(|b| b.is_ascii_hexdigit()) {
        return None;
    }
    let trace_flags = u8::from_str_radix(flags_str, 16).ok()?;

    if trace_id.bytes().all(|b| b == b'0') || parent_span_id.bytes().all(|b| b == b'0') {
        return None;
    }

    Some(TraceParent {
        trace_id: trace_id.to_string(),
        parent_span_id: parent_span_id.to_string(),
        trace_flags,
    })
}

// ── Timestamp conversion ────────────────────────────────────────────

/// Compatibility offset for older space-separated timestamps missing a zone.
const DEFAULT_LOCAL_OFFSET_SECONDS: i64 = 8 * 3600;

/// Convert ISO 8601 timestamp string to nanoseconds since epoch (as string).
/// Falls back to current time if parsing fails.
///
/// Handles three input shapes:
/// - `"2026-01-01T00:00:00.123Z"` — ISO 8601 with explicit UTC marker; used as UTC directly.
/// - `"2026-01-01T00:00:00+08:00"` — ISO 8601 with explicit offset; offset is honored.
/// - `"2026-05-06 11:06:55"` — legacy local time, interpreted as UTC+8.
pub fn iso_to_nanos(iso: &str) -> String {
    if iso.is_empty() {
        return now_nanos();
    }
    if iso.bytes().all(|byte| byte.is_ascii_digit()) {
        return iso.to_string();
    }
    let cleaned = iso.trim_end_matches('Z');
    let has_zulu = iso.ends_with('Z');
    let parts: Vec<&str> = cleaned.split('T').collect();
    let (parts, has_t_separator): (Vec<&str>, bool) = if parts.len() == 2 {
        (parts, true)
    } else {
        // Older transcripts separate the date and time with a space.
        (cleaned.splitn(2, ' ').collect(), false)
    };
    if parts.len() != 2 {
        return now_nanos();
    }

    let date_parts: Vec<&str> = parts[0].split('-').collect();
    if date_parts.len() != 3 {
        return now_nanos();
    }

    let year: i64 = date_parts[0].parse().unwrap_or(0);
    let month: i64 = date_parts[1].parse().unwrap_or(0);
    let day: i64 = date_parts[2].parse().unwrap_or(0);

    // Strip explicit numeric TZ offset (e.g. "+08:00", "-05:30") before parsing time.
    // We treat the remaining string as wall-clock, then subtract the offset at the end.
    let (time_str, explicit_offset_seconds): (&str, Option<i64>) = {
        // Look for '+' or '-' after the time portion (skip leading '-' that's part of the date).
        let bytes = parts[1].as_bytes();
        let sign_pos = bytes
            .iter()
            .enumerate()
            .skip(1)
            .find(|(_, &b)| b == b'+' || b == b'-')
            .map(|(i, _)| i);
        if let Some(idx) = sign_pos {
            let tz_str = &parts[1][idx..];
            let sign: i64 = if tz_str.starts_with('-') { -1 } else { 1 };
            let hm: Vec<&str> = tz_str[1..].splitn(2, ':').collect();
            let tz_hours: i64 = hm.first().and_then(|s| s.parse().ok()).unwrap_or(0);
            let tz_mins: i64 = hm.get(1).and_then(|s| s.parse().ok()).unwrap_or(0);
            let off = sign * (tz_hours * 3600 + tz_mins * 60);
            (&parts[1][..idx], Some(off))
        } else {
            (parts[1], None)
        }
    };

    let time_and_frac: Vec<&str> = time_str.split('.').collect();
    let time_parts: Vec<&str> = time_and_frac[0].split(':').collect();
    if time_parts.len() != 3 {
        return now_nanos();
    }

    let hour: i64 = time_parts[0].parse().unwrap_or(0);
    let minute: i64 = time_parts[1].parse().unwrap_or(0);
    let second: i64 = time_parts[2].parse().unwrap_or(0);

    let frac_nanos: i64 = if time_and_frac.len() > 1 {
        let frac_str = time_and_frac[1];
        let padded = format!("{:0<9}", &frac_str[..frac_str.len().min(9)]);
        padded.parse().unwrap_or(0)
    } else {
        0
    };

    let days = days_from_epoch(year, month, day);
    let mut total_secs = days * 86400 + hour * 3600 + minute * 60 + second;

    // Apply timezone correction so the result is always true UTC nanoseconds.
    // Priority: explicit "+08:00" offset > "Z" marker > local-time fallback.
    if let Some(off) = explicit_offset_seconds {
        // "13:01:58+08:00" → UTC is "05:01:58", so subtract offset.
        total_secs -= off;
    } else if !has_zulu && !has_t_separator {
        // Space-separated, no Z, no offset → treat as local time, convert to UTC.
        total_secs -= DEFAULT_LOCAL_OFFSET_SECONDS;
    }
    // else: had 'Z' (or 'T'-separated without offset) → already UTC, no shift.

    let total_nanos = total_secs * 1_000_000_000 + frac_nanos;

    total_nanos.to_string()
}

pub fn days_from_epoch(year: i64, month: i64, day: i64) -> i64 {
    let mut y = year;
    let mut m = month;
    if m <= 2 {
        y -= 1;
        m += 9;
    } else {
        m -= 3;
    }
    let era = if y >= 0 { y } else { y - 399 } / 400;
    let yoe = y - era * 400;
    let doy = (153 * m + 2) / 5 + day - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146097 + doe - 719468
}

pub fn now_nanos() -> String {
    let duration = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default();
    let nanos = duration.as_secs() as i128 * 1_000_000_000 + duration.subsec_nanos() as i128;
    nanos.to_string()
}

// ── OTLP JSON attribute helpers ─────────────────────────────────────

pub fn str_attr(key: &str, value: &str) -> Value {
    json!({"key": key, "value": {"stringValue": value}})
}

/// Integer attribute encoded as stringValue for Langfuse OTel worker compatibility.
pub fn int_attr(key: &str, value: i64) -> Value {
    json!({"key": key, "value": {"stringValue": value.to_string()}})
}

// ── Agentic tool classification ─────────────────────────────────────

/// Agentic tool classification derived from gen_ai.tool.name.
pub struct AgenticToolInfo {
    pub tool_type: &'static str, // "builtin" | "mcp" | "skill" | "subagent"
    pub name: String,            // parsed logical name
    pub id: String,              // tool invocation identifier
    pub raw_name: String,        // original tool name from agent runtime
}

/// Parse gen_ai.tool.name to derive agentic.tool.* fields.
///
/// Rules:
/// - `mcp__<server>__<method>` -> type=mcp, name=`<server>.<method>`
/// - `Agent` -> type=subagent, name unchanged
/// - `Skill` -> type=skill, name from skill_name param
/// - otherwise -> type=builtin, name unchanged
pub fn parse_agentic_tool(
    raw_tool_name: &str,
    tool_id: &str,
    skill_name: Option<&str>,
) -> AgenticToolInfo {
    let raw = raw_tool_name.to_string();
    if let Some(stripped) = raw_tool_name.strip_prefix("mcp__") {
        if stripped.is_empty() {
            return AgenticToolInfo {
                tool_type: "builtin",
                name: raw.clone(),
                id: tool_id.to_string(),
                raw_name: raw,
            };
        }
        let logical_name = if let Some(pos) = stripped.find("__") {
            format!("{}.{}", &stripped[..pos], &stripped[pos + 2..])
        } else {
            stripped.to_string()
        };
        AgenticToolInfo {
            tool_type: "mcp",
            name: logical_name,
            id: tool_id.to_string(),
            raw_name: raw,
        }
    } else if raw_tool_name == "Agent" {
        AgenticToolInfo {
            tool_type: "subagent",
            name: "Agent".to_string(),
            id: tool_id.to_string(),
            raw_name: raw,
        }
    } else if raw_tool_name == "Skill" || (raw_tool_name.is_empty() && skill_name.is_some()) {
        let sn = skill_name.unwrap_or("unknown");
        AgenticToolInfo {
            tool_type: "skill",
            name: sn.to_string(),
            id: if tool_id.is_empty() {
                format!("skill-{}", sn)
            } else {
                tool_id.to_string()
            },
            raw_name: raw,
        }
    } else {
        AgenticToolInfo {
            tool_type: "builtin",
            name: if raw_tool_name.is_empty() {
                "unknown".to_string()
            } else {
                raw.clone()
            },
            id: tool_id.to_string(),
            raw_name: raw,
        }
    }
}

/// Derive span name prefix from tool classification.
/// - mcp -> `mcp {name}`, skill -> `skill {name}`, subagent -> `subagent {name}`, builtin -> `tool {name}`
pub fn tool_span_name(info: &AgenticToolInfo) -> String {
    match info.tool_type {
        "mcp" => format!("mcp {}", info.name),
        "skill" => format!("skill {}", info.name),
        "subagent" => format!("subagent {}", info.name),
        _ => format!("tool {}", info.name),
    }
}

/// Append agentic.tool.* attributes to a span's attribute list.
/// `agentic.tool.raw_name` is always emitted.
/// `agentic.tool.type/name/id` only for non-builtin tools (mcp/skill).
pub fn append_agentic_tool_attrs(attributes: &mut Vec<Value>, info: &AgenticToolInfo) {
    attributes.push(str_attr("agentic.tool.raw_name", &info.raw_name));
    if info.tool_type != "builtin" {
        attributes.push(str_attr("agentic.tool.type", info.tool_type));
        attributes.push(str_attr("agentic.tool.name", &info.name));
        attributes.push(str_attr("agentic.tool.id", &info.id));
    }
}

// ── Text truncation ─────────────────────────────────────────────────

/// Truncation threshold: 4 MiB (4 * 1024 * 1024 chars).
/// Applies to tool call arguments/result and single-value truncation.
const MAX_CHARS: usize = 4 * 1024 * 1024;

/// Cost and outcome metadata for a truncation decision.
///
/// The fields are intentionally safe for logs: they contain operation labels,
/// byte counts, timing, and a hash-presence flag, but never payload content,
/// request bodies, credentials, tool arguments, or digest values.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct TruncationStats {
    pub operation: String,
    pub hook_event: String,
    pub event_id: String,
    pub worker_id: String,
    pub field_kind: String,
    pub elapsed_ms: u128,
    pub original_bytes: usize,
    pub kept_bytes: usize,
    pub limit_bytes: usize,
    pub truncated: bool,
    pub reason: String,
    pub iterations: usize,
    pub hash_present: bool,
}

impl TruncationStats {
    // This constructor mirrors the safe serialized record so call sites name every
    // measured field explicitly; grouping them would only move the same data around.
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        operation: impl Into<String>,
        field_kind: impl Into<String>,
        original_bytes: usize,
        kept_bytes: usize,
        limit_bytes: usize,
        truncated: bool,
        reason: impl Into<String>,
        iterations: usize,
        hash_present: bool,
        elapsed_ms: u128,
    ) -> Self {
        Self {
            operation: operation.into(),
            hook_event: String::new(),
            event_id: String::new(),
            worker_id: String::new(),
            field_kind: field_kind.into(),
            elapsed_ms,
            original_bytes,
            kept_bytes,
            limit_bytes,
            truncated,
            reason: reason.into(),
            iterations,
            hash_present,
        }
    }

    pub fn with_log_context(
        mut self,
        operation: impl Into<String>,
        hook_event: impl Into<String>,
        event_id: impl Into<String>,
        worker_id: impl Into<String>,
        field_kind: impl Into<String>,
    ) -> Self {
        self.operation = operation.into();
        self.hook_event = hook_event.into();
        self.event_id = event_id.into();
        self.worker_id = worker_id.into();
        self.field_kind = field_kind.into();
        self
    }

    pub fn log_level(&self) -> &'static str {
        if self.elapsed_ms > 10 {
            "WARN"
        } else if self.truncated {
            "INFO"
        } else {
            "DEBUG"
        }
    }

    pub fn to_safe_log_fields(&self) -> String {
        serde_json::to_string(self).unwrap_or_else(|_| {
            format!(
                "operation={} hook_event={} event_id={} worker_id={} field_kind={} elapsed_ms={} original_bytes={} kept_bytes={} limit_bytes={} truncated={} reason={} iterations={} hash_present={}",
                self.operation,
                self.hook_event,
                self.event_id,
                self.worker_id,
                self.field_kind,
                self.elapsed_ms,
                self.original_bytes,
                self.kept_bytes,
                self.limit_bytes,
                self.truncated,
                self.reason,
                self.iterations,
                self.hash_present
            )
        })
    }
}

/// Truncate text to MAX_CHARS, returning (truncated_text, metadata).
/// Metadata includes truncation info and sha256 of original if truncated.
pub fn truncate_text(text: &str) -> (String, Value) {
    let (truncated, meta, _) = truncate_text_with_stats(text, "text");
    (truncated, meta)
}

/// Truncate text to MAX_CHARS, also returning safe observability stats.
pub fn truncate_text_with_stats(text: &str, field_kind: &str) -> (String, Value, TruncationStats) {
    let started = std::time::Instant::now();
    let orig_len = text.len();
    if orig_len <= MAX_CHARS {
        let stats = TruncationStats::new(
            "truncate_text",
            field_kind,
            orig_len,
            orig_len,
            MAX_CHARS,
            false,
            "within_limit",
            0,
            false,
            started.elapsed().as_millis(),
        );
        return (
            text.to_string(),
            json!({"truncated": false, "orig_len": orig_len}),
            stats,
        );
    }
    // Find a valid UTF-8 char boundary at or before MAX_CHARS
    let mut end = MAX_CHARS;
    let mut iterations = 0usize;
    while !text.is_char_boundary(end) {
        end -= 1;
        iterations += 1;
    }
    let head = &text[..end];
    let mut hasher = sha2::Sha256::new();
    use sha2::Digest;
    hasher.update(text.as_bytes());
    let digest = hasher.finalize();
    let hash = digest
        .iter()
        .map(|b| format!("{:02x}", b))
        .collect::<String>();
    let stats = TruncationStats::new(
        "truncate_text",
        field_kind,
        orig_len,
        head.len(),
        MAX_CHARS,
        true,
        "hard_limit",
        iterations,
        true,
        started.elapsed().as_millis(),
    );
    (
        head.to_string(),
        json!({
            "truncated": true,
            "orig_len": orig_len,
            "kept_len": head.len(),
            "sha256": hash,
        }),
        stats,
    )
}

/// Truncate a serde_json::Value (if it's a string). Returns (truncated_value, metadata).
pub fn truncate_value(val: &Value) -> (Value, Value) {
    let (truncated, meta, _) = truncate_value_with_stats(val, "value");
    (truncated, meta)
}

/// Truncate a serde_json::Value and return safe observability stats.
pub fn truncate_value_with_stats(val: &Value, field_kind: &str) -> (Value, Value, TruncationStats) {
    let original_size = serde_json::to_vec(val).map_or(0, |v| v.len());
    if let Some(projected) = (original_size > MAX_CHARS).then(|| media::project_tool_media(val)).flatten() {
        let size = serde_json::to_vec(&projected).map_or(0, |v| v.len());
        let omitted_fields = &projected != val;
        let stats = TruncationStats::new("truncate_value", field_kind, original_size, size, MAX_CHARS, omitted_fields, "typed_media", 0, false, 0);
        return (projected, json!({"truncated":omitted_fields,"orig_len":original_size}), stats);
    }
    match val {
        Value::String(s) => {
            let (truncated, meta, stats) = truncate_text_with_stats(s, field_kind);
            (
                Value::String(truncated),
                meta,
                stats.with_log_context("truncate_value", "", "", "", field_kind.to_string()),
            )
        }
        _ => {
            let started = std::time::Instant::now();
            let serialized = serde_json::to_string(val).unwrap_or_default();
            if serialized.len() <= MAX_CHARS {
                let stats = TruncationStats::new(
                    "truncate_value",
                    field_kind,
                    serialized.len(),
                    serialized.len(),
                    MAX_CHARS,
                    false,
                    "within_limit",
                    0,
                    false,
                    started.elapsed().as_millis(),
                );
                (
                    val.clone(),
                    json!({"truncated": false, "orig_len": serialized.len()}),
                    stats,
                )
            } else {
                let (truncated, meta, mut stats) =
                    truncate_text_with_stats(&serialized, field_kind);
                stats.operation = "truncate_value".to_string();
                stats.elapsed_ms = started.elapsed().as_millis();
                (Value::String(truncated), meta, stats)
            }
        }
    }
}

// ── Auth ────────────────────────────────────────────────────────────

pub fn build_basic_auth_header(public_key: &str, secret_key: &str) -> String {
    use base64::Engine;
    let raw = format!("{}:{}", public_key, secret_key);
    let encoded = base64::engine::general_purpose::STANDARD.encode(raw.as_bytes());
    format!("Basic {}", encoded)
}

// ── Hostname ────────────────────────────────────────────────────────

pub fn hostname_from_system() -> Result<String, String> {
    #[cfg(unix)]
    {
        let mut buf = [0u8; 256];
        let ret = unsafe { libc::gethostname(buf.as_mut_ptr() as *mut libc::c_char, buf.len()) };
        if ret == 0 {
            let len = buf.iter().position(|&b| b == 0).unwrap_or(buf.len());
            return Ok(String::from_utf8_lossy(&buf[..len]).to_string());
        }
    }
    Err("gethostname failed".to_string())
}

// ── OTLP export request wrapping ────────────────────────────────────

/// Context for wrapping spans into an OTLP ExportTraceServiceRequest.
pub struct ExportContext<'a> {
    pub agent_product: &'a str,
    pub agent_type: &'a str,
    pub sdk_name: &'a str,
    pub build_version: &'a str,
    pub agent_env: &'a str,
    pub agent_scene: &'a str,
    pub collection_mode: &'a str,
    pub emit_meta_trigger: bool,
}

/// Wrap collected spans into an OTLP ExportTraceServiceRequest JSON.
pub fn wrap_export_request(spans: Vec<Value>, ctx: &ExportContext) -> Value {
    let hostname = std::env::var("HOSTNAME")
        .or_else(|_| hostname_from_system())
        .unwrap_or_else(|_| "unknown".to_string());

    let mut resource_attributes = vec![
        str_attr("service.name", ctx.agent_product),
        str_attr("service.version", ctx.build_version),
        str_attr("ant.agent.instance", &hostname),
        str_attr("ant.agent.product", ctx.agent_product),
        str_attr("ant.agent.type", ctx.agent_type),
        str_attr("telemetry.sdk.language", "rust"),
        str_attr("telemetry.sdk.name", ctx.sdk_name),
        str_attr("telemetry.sdk.version", ctx.build_version),
    ];
    resource_attributes.push(str_attr("ant.agent.env", ctx.agent_env));
    resource_attributes.push(str_attr("deployment.environment", ctx.agent_env));
    resource_attributes.push(str_attr("ant.agent.scene", ctx.agent_scene));

    let (overrides, meta_trigger) = otlp::resolve_resource_overrides(ctx);
    if !overrides.is_empty() {
        let override_keys: Vec<&str> = overrides.iter().map(|(k, _)| k.as_str()).collect();
        resource_attributes.retain(|a| {
            a.get("key")
                .and_then(|k| k.as_str())
                .map(|k| !override_keys.contains(&k))
                .unwrap_or(true)
        });
        for (key, value) in overrides {
            resource_attributes.push(str_attr(&key, &value));
        }
    }
    if let Some(trigger) = meta_trigger {
        resource_attributes.retain(|a| {
            a.get("key")
                .and_then(|k| k.as_str())
                .map(|k| k != otlp::ANT_META_TRIGGER_KEY)
                .unwrap_or(true)
        });
        resource_attributes.push(str_attr(otlp::ANT_META_TRIGGER_KEY, &trigger));
    }
    // Collection mode comes from the caller's resolved privacy configuration.
    // Generic Resource overrides must not claim a different collection mode.
    resource_attributes.retain(|attribute| {
        attribute.get("key").and_then(Value::as_str) != Some("agentic.collection.mode")
    });
    resource_attributes.push(str_attr("agentic.collection.mode", ctx.collection_mode));

    json!({
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
    })
}

// ── Provider name extraction ────────────────────────────────────────

/// Extract provider name from a model string containing '/'.
/// e.g. "antchat/GLM-5.1" → Some("antchat"), "claude-opus-4-6" → None
pub fn extract_provider_name(model: &str) -> Option<&str> {
    model
        .split_once('/')
        .map(|(provider, _)| provider)
        .filter(|p| !p.is_empty())
}

// ── Skill name extraction ───────────────────────────────────────────

/// Extract skill name from a Skill tool_use's input JSON.
/// The input has `{"skill": "publisher:plugin:name"}` format.
/// Normalizes 3-part names (`publisher:plugin:name` -> `plugin:name`) to match
/// the format from isMeta paths.
pub fn extract_skill_name_from_tool_input(input: &Value) -> Option<String> {
    let raw = input.get("skill").and_then(|v| v.as_str())?;
    if raw.is_empty() {
        return None;
    }
    let parts: Vec<&str> = raw.splitn(3, ':').collect();
    if parts.len() >= 3 {
        Some(format!("{}:{}", parts[1], parts[2]))
    } else {
        Some(raw.to_string())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_extract_provider_name() {
        assert_eq!(extract_provider_name("antchat/GLM-5.1"), Some("antchat"));
        assert_eq!(extract_provider_name("openai/gpt-4o"), Some("openai"));
        assert_eq!(extract_provider_name("claude-opus-4-6"), None);
        assert_eq!(extract_provider_name(""), None);
        assert_eq!(extract_provider_name("/model"), None);
        assert_eq!(extract_provider_name("provider/"), Some("provider"));
    }

    #[test]
    fn test_otel_trace_id_length() {
        assert_eq!(otel_trace_id().len(), 32);
    }

    #[test]
    fn test_otel_span_id_length() {
        assert_eq!(otel_span_id().len(), 16);
    }

    #[test]
    fn test_iso_to_nanos_valid() {
        let ns = iso_to_nanos("2026-01-01T00:00:00.000Z");
        assert!(!ns.is_empty());
        assert!(ns.parse::<i64>().unwrap() > 0);
    }

    #[test]
    fn test_iso_to_nanos_accepts_unix_nanoseconds() {
        assert_eq!(iso_to_nanos("1767225600525000000"), "1767225600525000000");
    }

    #[test]
    fn test_iso_to_nanos_empty_fallback() {
        let ns = iso_to_nanos("");
        assert!(!ns.is_empty());
    }

    // Timezone conversion regressions.

    #[test]
    fn test_iso_to_nanos_zulu_is_utc_unchanged() {
        // "2026-01-01T00:00:00.000Z" → exactly 1767225600_000_000_000 ns
        let ns = iso_to_nanos("2026-01-01T00:00:00.000Z");
        assert_eq!(ns, "1767225600000000000");
    }

    #[test]
    fn test_iso_to_nanos_local_shifts_to_utc() {
        // Local "2026-01-01 08:00:00" corresponds to "2026-01-01 00:00:00Z".
        let ns = iso_to_nanos("2026-01-01 08:00:00");
        assert_eq!(ns, "1767225600000000000");
    }

    #[test]
    fn test_iso_to_nanos_local_with_fractional() {
        // Local "2026-01-01 08:00:00.500" → UTC "2026-01-01 00:00:00.500Z".
        let ns = iso_to_nanos("2026-01-01 08:00:00.500");
        assert_eq!(ns, "1767225600500000000");
    }

    #[test]
    fn test_iso_to_nanos_explicit_positive_offset() {
        // "+08:00" wall-clock 13:01:58 → UTC 05:01:58
        let ns = iso_to_nanos("2026-06-02T13:01:58+08:00");
        let expected = iso_to_nanos("2026-06-02T05:01:58Z");
        assert_eq!(ns, expected);
    }

    #[test]
    fn test_iso_to_nanos_explicit_negative_offset() {
        // "-05:00" wall-clock 19:00:00 → UTC 00:00:00 next day
        let ns = iso_to_nanos("2026-01-01T19:00:00-05:00");
        let expected = iso_to_nanos("2026-01-02T00:00:00Z");
        assert_eq!(ns, expected);
    }

    #[test]
    fn test_iso_to_nanos_zulu_with_space_separator_treated_as_utc() {
        // Belt-and-suspenders: even if someone writes "2026-01-01 08:00:00Z",
        // the trailing 'Z' should win and the result is UTC (no shift).
        let ns = iso_to_nanos("2026-01-01 08:00:00Z");
        let expected = iso_to_nanos("2026-01-01T08:00:00Z");
        assert_eq!(ns, expected);
    }

    #[test]
    fn test_str_attr() {
        let attr = str_attr("key", "val");
        assert_eq!(attr["key"], "key");
        assert_eq!(attr["value"]["stringValue"], "val");
    }

    #[test]
    fn test_int_attr_uses_string_value() {
        let attr = int_attr("count", 42);
        assert_eq!(attr["value"]["stringValue"], "42");
    }

    #[test]
    fn test_parse_agentic_tool_builtin() {
        let info = parse_agentic_tool("Read", "id1", None);
        assert_eq!(info.tool_type, "builtin");
        assert_eq!(info.name, "Read");
    }

    #[test]
    fn test_parse_agentic_tool_mcp() {
        let info = parse_agentic_tool("mcp__yuque__search", "id2", None);
        assert_eq!(info.tool_type, "mcp");
        assert_eq!(info.name, "yuque.search");
    }

    #[test]
    fn test_parse_agentic_tool_skill() {
        let info = parse_agentic_tool("Skill", "id3", Some("my-skill"));
        assert_eq!(info.tool_type, "skill");
        assert_eq!(info.name, "my-skill");
    }

    #[test]
    fn test_parse_agentic_tool_agent() {
        let info = parse_agentic_tool("Agent", "toolu_agent1", None);
        assert_eq!(info.tool_type, "subagent");
        assert_eq!(info.name, "Agent");
        assert_eq!(info.id, "toolu_agent1");
    }

    #[test]
    fn test_tool_span_name() {
        let info = parse_agentic_tool("Read", "id1", None);
        assert_eq!(tool_span_name(&info), "tool Read");

        let info = parse_agentic_tool("mcp__yuque__search", "id2", None);
        assert_eq!(tool_span_name(&info), "mcp yuque.search");

        let info = parse_agentic_tool("Agent", "id3", None);
        assert_eq!(tool_span_name(&info), "subagent Agent");
    }

    #[test]
    fn test_truncate_text_short() {
        let (text, meta) = truncate_text("hello");
        assert_eq!(text, "hello");
        assert_eq!(meta["truncated"], false);
    }

    #[test]
    fn test_truncate_text_utf8_boundary() {
        // Create a string with multi-byte chars near the boundary
        let s = "a".repeat(MAX_CHARS - 1) + "\u{1F600}"; // emoji is 4 bytes
        let (truncated, meta) = truncate_text(&s);
        assert_eq!(meta["truncated"], true);
        // Should not panic on UTF-8 boundary
        assert!(truncated.len() <= MAX_CHARS);
    }

    #[test]
    fn test_truncate_text_stats_are_byte_precise_and_safe() {
        let secret = format!("token={}", "s".repeat(MAX_CHARS + 16));
        let (_truncated, meta, stats) = truncate_text_with_stats(&secret, "credential");

        assert_eq!(meta["truncated"], true);
        assert_eq!(stats.original_bytes, secret.len());
        assert_eq!(stats.kept_bytes, MAX_CHARS);
        assert_eq!(stats.limit_bytes, MAX_CHARS);
        assert!(stats.hash_present);
        assert_eq!(stats.reason, "hard_limit");

        let line = stats
            .with_log_context(
                "tool_export",
                "PostToolUse",
                "toolu_sensitive",
                "worker-1",
                "credential",
            )
            .to_safe_log_fields();
        assert!(line.contains("\"operation\":\"tool_export\""));
        assert!(line.contains("\"hash_present\":true"));
        assert!(!line.contains("token="));
        assert!(!line.contains(&"s".repeat(64)));
        assert!(!line.contains(meta["sha256"].as_str().unwrap()));
    }

    #[test]
    fn test_truncate_stats_log_level_policy() {
        let mut stats = TruncationStats::new(
            "truncate_text",
            "text",
            10,
            10,
            MAX_CHARS,
            false,
            "within_limit",
            0,
            false,
            1,
        );
        assert_eq!(stats.log_level(), "DEBUG");
        stats.truncated = true;
        assert_eq!(stats.log_level(), "INFO");
        stats.elapsed_ms = 11;
        assert_eq!(stats.log_level(), "WARN");
    }

    #[test]
    fn test_build_basic_auth_header() {
        let header = build_basic_auth_header("pub", "sec");
        assert!(header.starts_with("Basic "));
    }

    #[test]
    fn test_uuid_v4_format() {
        let id = uuid_v4();
        assert_eq!(id.len(), 36);
        assert_eq!(&id[8..9], "-");
        assert_eq!(&id[13..14], "-");
    }

    #[test]
    fn test_wrap_export_request() {
        let spans = vec![json!({"name": "test"})];
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
        let req = wrap_export_request(spans, &ctx);
        assert!(req["resourceSpans"].is_array());
        let scope = &req["resourceSpans"][0]["scopeSpans"][0];
        assert_eq!(scope["scope"]["name"], "test-hooks");
    }

    #[test]
    fn test_legacy_wrap_export_request_protects_collection_mode_authority() {
        let _guard = otlp::META_TRIGGER_ENV_LOCK.lock().unwrap();
        let env_key = "LEGACYCOLLECTIONTEST_OTEL_RESOURCE_ATTRIBUTES";
        let previous = std::env::var_os(env_key);
        std::env::set_var(
            env_key,
            "agentic.collection.mode=privacy,custom.resource=value",
        );

        let ctx = ExportContext {
            agent_product: "TestAgent",
            agent_type: "test",
            sdk_name: "legacycollectiontest",
            build_version: "0.1.0",
            agent_env: "",
            agent_scene: "",
            collection_mode: "full",
            emit_meta_trigger: false,
        };
        let req = wrap_export_request(vec![], &ctx);

        if let Some(value) = previous {
            std::env::set_var(env_key, value);
        } else {
            std::env::remove_var(env_key);
        }

        let attributes = req["resourceSpans"][0]["resource"]["attributes"]
            .as_array()
            .unwrap();
        let collection_modes: Vec<_> = attributes
            .iter()
            .filter(|attribute| attribute["key"] == "agentic.collection.mode")
            .filter_map(|attribute| attribute["value"]["stringValue"].as_str())
            .collect();
        assert_eq!(collection_modes, vec!["full"]);
        assert!(attributes.iter().any(|attribute| {
            attribute["key"] == "custom.resource" && attribute["value"]["stringValue"] == "value"
        }));
    }

    #[test]
    fn test_turn_trigger_canonical_values_and_default() {
        assert_eq!(TurnTrigger::Interactive.as_str(), "interactive");
        assert_eq!(TurnTrigger::Programmatic.as_str(), "programmatic");
        assert_eq!(TurnTrigger::Queued.as_str(), "queued");
        assert_eq!(TurnTrigger::System.as_str(), "system");
        assert_eq!(TurnTrigger::Subagent.as_str(), "subagent");
        assert_eq!(TurnTrigger::default(), TurnTrigger::Unknown);
        assert_eq!(TurnTrigger::Unknown.as_str(), "unknown");
    }

    #[test]
    fn test_extract_skill_name_three_parts() {
        let input = json!({"skill": "publisher:plugin:name"});
        assert_eq!(
            extract_skill_name_from_tool_input(&input),
            Some("plugin:name".to_string())
        );
    }

    #[test]
    fn test_extract_skill_name_two_parts() {
        let input = json!({"skill": "plugin:name"});
        assert_eq!(
            extract_skill_name_from_tool_input(&input),
            Some("plugin:name".to_string())
        );
    }

    #[test]
    fn test_parse_traceparent_valid() {
        let tp = parse_traceparent("00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01");
        assert!(tp.is_some());
        let tp = tp.unwrap();
        assert_eq!(tp.trace_id, "0af7651916cd43dd8448eb211c80319c");
        assert_eq!(tp.parent_span_id, "b7ad6b7169203331");
        assert_eq!(tp.trace_flags, 1);
    }

    #[test]
    fn test_parse_traceparent_invalid_format() {
        assert!(parse_traceparent("").is_none());
        assert!(parse_traceparent("00-abc-def-01").is_none());
        assert!(parse_traceparent("not-a-traceparent").is_none());
    }

    #[test]
    fn test_parse_traceparent_all_zeros_rejected() {
        assert!(
            parse_traceparent("00-00000000000000000000000000000000-b7ad6b7169203331-01").is_none()
        );
        assert!(
            parse_traceparent("00-0af7651916cd43dd8448eb211c80319c-0000000000000000-01").is_none()
        );
    }
}
