pub mod domain_state;
pub mod langfuse_handler;
pub mod realtime;
pub mod realtime_generation;
pub mod realtime_state;
pub mod state;

// --- Re-export langfuse config and utilities (merged from langfuse.rs) ---

use base64::Engine;
use serde_json::Value;
use std::collections::HashMap;

// --- Constants ---

// build.rs loads Langfuse defaults at compile time for the AI Vision edition.
// Common builds read their configuration from the environment.
#[cfg(feature = "aivision")]
pub const LANGFUSE_PUBLIC_KEY_PROD: &str = env!("LANGFUSE_BUILTIN_PROD_PUBLIC_KEY");
#[cfg(feature = "aivision")]
pub const LANGFUSE_SECRET_KEY_PROD: &str = env!("LANGFUSE_BUILTIN_PROD_SECRET_KEY");
#[cfg(feature = "aivision")]
pub const LANGFUSE_HOST_PROD: &str = env!("LANGFUSE_BUILTIN_PROD_HOST");
#[cfg(feature = "aivision")]
pub const LANGFUSE_HEADERS_PROD: &str = env!("LANGFUSE_BUILTIN_PROD_HEADERS");

#[cfg(feature = "aivision")]
pub const LANGFUSE_PUBLIC_KEY_GRAY: &str = env!("LANGFUSE_BUILTIN_GRAY_PUBLIC_KEY");
#[cfg(feature = "aivision")]
pub const LANGFUSE_SECRET_KEY_GRAY: &str = env!("LANGFUSE_BUILTIN_GRAY_SECRET_KEY");
#[cfg(feature = "aivision")]
pub const LANGFUSE_HOST_GRAY: &str = env!("LANGFUSE_BUILTIN_GRAY_HOST");

pub const ANT_AGENT_TYPE: &str = "claudecode";
#[cfg(feature = "aivision")]
pub const ANT_AGENT_PRODUCT: &str = env!("LANGFUSE_BUILTIN_AGENT_PRODUCT");
#[cfg(not(feature = "aivision"))]
pub const ANT_AGENT_PRODUCT: &str = "ClaudeCode";

pub fn resolve_agent_product() -> String {
    crate::common::hook::env_or_default(
        "CCHOOKS_AGENT_PRODUCT",
        "AIVISION_AGENT_PRODUCT",
        ANT_AGENT_PRODUCT,
    )
}

pub fn resolve_agent_type() -> String {
    crate::common::hook::env_or_default("CCHOOKS_AGENT_TYPE", "AIVISION_AGENT_TYPE", ANT_AGENT_TYPE)
}

// --- Config ---

#[cfg(not(feature = "aivision"))]
fn parse_otlp_headers(s: &str) -> HashMap<String, String> {
    s.split(',')
        .filter_map(|pair| {
            let pair = pair.trim();
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

#[derive(Debug, Clone)]
pub struct LangfuseConfig {
    pub public_key: Option<String>,
    pub secret_key: Option<String>,
    pub host: String,
    /// Optional additional headers to send with requests (e.g., {"x-header": "value"})
    pub headers: Option<std::collections::HashMap<String, String>>,
}

impl LangfuseConfig {
    #[cfg(feature = "aivision")]
    fn log_to_file(msg: &str) {
        let log_file = crate::common::dirs::log_dir().join("cchooks.log");
        if let Ok(mut f) = std::fs::OpenOptions::new()
            .append(true)
            .create(true)
            .open(&log_file)
        {
            use std::io::Write;
            let _ = writeln!(f, "[WARN] {}", msg);
        }
    }

    /// Create config from environment variables.
    /// Returns Some when LANGFUSE_BASE_URL is set OR CCHOOKS_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT
    /// provides a full override URL (in which case host is unused but we still need a config).
    /// PK/SK are optional — when absent, no Authorization header is sent.
    /// Supports CCHOOKS_ prefix with fallback: CCHOOKS_LANGFUSE_BASE_URL > LANGFUSE_BASE_URL, etc.
    #[cfg(not(feature = "aivision"))]
    pub fn from_env() -> Option<Self> {
        use crate::common::hook::env_cc;
        let host = env_cc("CCHOOKS_LANGFUSE_BASE_URL", "LANGFUSE_BASE_URL").or_else(|| {
            crate::common::hook::otlp_traces_endpoint("CCHOOKS_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
        })?;
        let public_key = env_cc("CCHOOKS_LANGFUSE_PUBLIC_KEY", "LANGFUSE_PUBLIC_KEY");
        let secret_key = env_cc("CCHOOKS_LANGFUSE_SECRET_KEY", "LANGFUSE_SECRET_KEY");
        let headers = env_cc("CCHOOKS_LANGFUSE_HEADERS", "LANGFUSE_HEADERS")
            .map(|s| parse_otlp_headers(&s))
            .filter(|m| !m.is_empty());
        Some(Self {
            public_key,
            secret_key,
            host,
            headers,
        })
    }

    #[cfg(feature = "aivision")]
    pub fn prod() -> Self {
        let headers = if LANGFUSE_HEADERS_PROD.is_empty() {
            None
        } else {
            match serde_json::from_str::<std::collections::HashMap<String, String>>(
                LANGFUSE_HEADERS_PROD,
            ) {
                Ok(h) => Some(h),
                Err(e) => {
                    Self::log_to_file(&format!("Failed to parse LANGFUSE_HEADERS_PROD: {}", e));
                    None
                }
            }
        };
        Self {
            public_key: Some(LANGFUSE_PUBLIC_KEY_PROD.to_string()),
            secret_key: Some(LANGFUSE_SECRET_KEY_PROD.to_string()),
            host: LANGFUSE_HOST_PROD.to_string(),
            headers,
        }
    }

    #[cfg(feature = "aivision")]
    pub fn gray() -> Self {
        Self {
            public_key: Some(LANGFUSE_PUBLIC_KEY_GRAY.to_string()),
            secret_key: Some(LANGFUSE_SECRET_KEY_GRAY.to_string()),
            host: LANGFUSE_HOST_GRAY.to_string(),
            headers: None,
        }
    }
}

// --- Auth ---

pub fn build_basic_auth_header(public_key: &str, secret_key: &str) -> String {
    let raw = format!("{}:{}", public_key, secret_key);
    let encoded = base64::engine::general_purpose::STANDARD.encode(raw.as_bytes());
    format!("Basic {}", encoded)
}

// --- Custom metadata ---

pub fn build_custom_metadata() -> HashMap<String, Value> {
    let mut metadata: HashMap<String, Value> = HashMap::new();

    // CCHOOKS_LANGFUSE_METADATA > LANGFUSE_METADATA
    if let Some(json_str) =
        crate::common::hook::env_cc("CCHOOKS_LANGFUSE_METADATA", "LANGFUSE_METADATA")
    {
        if let Ok(parsed) = serde_json::from_str::<HashMap<String, Value>>(&json_str) {
            metadata.extend(parsed);
        }
    }

    // CCHOOKS_LANGFUSE_TAGS > LANGFUSE_TAGS
    if let Some(tags_str) = crate::common::hook::env_cc("CCHOOKS_LANGFUSE_TAGS", "LANGFUSE_TAGS") {
        let tag_arr: Vec<Value> = tags_str
            .split(',')
            .map(|t| t.trim())
            .filter(|t| !t.is_empty())
            .map(|t| Value::String(t.to_string()))
            .collect();
        if !tag_arr.is_empty() {
            metadata.insert("tags".to_string(), Value::Array(tag_arr));
        }
    }

    // CCHOOKS_ATTR_* > LANGFUSE_ATTR_* (collect both, CCHOOKS_ takes precedence)
    let mut attr_map: HashMap<String, String> = HashMap::new();
    for (key, value) in crate::common::env::vars() {
        if let Some(stripped) = key.strip_prefix("LANGFUSE_ATTR_") {
            let suffix = stripped.to_lowercase();
            attr_map.entry(suffix).or_insert(value);
        }
    }
    for (key, value) in crate::common::env::vars() {
        if let Some(stripped) = key.strip_prefix("CCHOOKS_ATTR_") {
            let suffix = stripped.to_lowercase();
            attr_map.insert(suffix, value); // CCHOOKS_ overrides LANGFUSE_ATTR_
        }
    }
    for (suffix, value) in attr_map {
        if suffix == "username" || suffix == "ant_username" {
            continue;
        }
        metadata.insert(suffix, Value::String(value));
    }

    // Remove reserved keys
    for reserved in &[
        "username",
        "ant.username",
        "agent_type",
        "agent_product",
        "agent_instance",
    ] {
        metadata.remove(*reserved);
    }

    metadata
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_build_basic_auth_header() {
        let header = build_basic_auth_header("pk-lf-test", "sk-lf-secret");
        assert!(header.starts_with("Basic "));
        let encoded = &header["Basic ".len()..];
        let decoded = base64::engine::general_purpose::STANDARD
            .decode(encoded)
            .unwrap();
        let decoded_str = String::from_utf8(decoded).unwrap();
        assert_eq!(decoded_str, "pk-lf-test:sk-lf-secret");
    }

    #[test]
    fn test_build_custom_metadata_from_env() {
        // Set test env vars
        std::env::set_var("LANGFUSE_ATTR_MY_KEY", "my_value");
        let metadata = build_custom_metadata();
        assert_eq!(
            metadata.get("my_key").and_then(|v| v.as_str()),
            Some("my_value")
        );
        std::env::remove_var("LANGFUSE_ATTR_MY_KEY");
    }

    #[test]
    fn test_build_custom_metadata_reserved_keys() {
        std::env::set_var("LANGFUSE_ATTR_USERNAME", "should_be_filtered");
        let metadata = build_custom_metadata();
        assert!(!metadata.contains_key("username"));
        std::env::remove_var("LANGFUSE_ATTR_USERNAME");
    }
}
