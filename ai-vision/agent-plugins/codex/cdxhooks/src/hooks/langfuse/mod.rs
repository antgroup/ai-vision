pub mod langfuse_handler;
pub mod realtime;
pub mod realtime_generation;
pub mod realtime_state;
pub mod state;
pub mod subagent_progress;
pub mod turn_topology;
pub(crate) mod turn_trace_identity;

// --- Re-export langfuse config and utilities (merged from langfuse.rs) ---

use base64::Engine;
use serde_json::Value;
use std::collections::HashMap;

/// Export configuration reads the effective queue overlay, including when a
/// durable recovery restores it. Sensitive values remain runtime-only.
fn export_env_prefixed(primary: &str, fallback: &str) -> Option<String> {
    crate::common::env::var(primary)
        .ok()
        .filter(|v| !v.is_empty())
        .or_else(|| {
            crate::common::env::var(fallback)
                .ok()
                .filter(|v| !v.is_empty())
        })
}

/// Read a variable from ~/.codex/.env at call time. The Codex app-server injects
/// its startup-cached environment into hook processes, so a credential renewed
/// in .env would otherwise never reach a running session. Reading the file on
/// each export lets a refreshed token take effect without restarting Codex.
fn read_codex_dotenv_var(key: &str) -> Option<String> {
    let root = std::env::var("CODEX_HOME")
        .map(std::path::PathBuf::from)
        .or_else(|_| {
            crate::common::env::var("HOME").map(|h| std::path::PathBuf::from(h).join(".codex"))
        })
        .ok()?;
    let path = root.join(".env");
    let content = std::fs::read_to_string(path).ok()?;
    for line in content.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        if let Some((k, v)) = line.split_once('=') {
            if k.trim() == key {
                return Some(v.trim().trim_matches('"').to_string());
            }
        }
    }
    None
}

pub(crate) fn merge_export_headers(config: &mut LangfuseConfig) {
    // Managed installs read the launcher's refreshed private cache. Legacy
    // standalone installs retain their on-disk .env behavior.
    let managed = crate::common::env::var("AIVISION_MANAGED_INGEST")
        .ok()
        .as_deref()
        == Some("1");
    let raw = (if managed {
        crate::common::env::var("CDXHOOKS_OTEL_EXPORTER_OTLP_HEADERS").ok()
    } else {
        read_codex_dotenv_var("CDXHOOKS_OTEL_EXPORTER_OTLP_HEADERS")
    })
    .or_else(|| crate::common::env::var("CDXHOOKS_OTEL_EXPORTER_OTLP_HEADERS").ok())
    .unwrap_or_default();
    let extra = parse_otlp_headers(&raw);
    if !extra.is_empty() {
        config
            .headers
            .get_or_insert_with(HashMap::new)
            .extend(extra);
    }
}

/// Shared default selection for CLI environment exports and production hooks.
pub(crate) fn resolve_env_export_config() -> Option<LangfuseConfig> {
    #[cfg(feature = "aivision")]
    let mut config = {
        let gray = export_env_prefixed("CDXHOOKS_LANGFUSE_ENV", "LANGFUSE_ENV")
            .is_some_and(|v| v.eq_ignore_ascii_case("gray"))
            || crate::common::env::var("ANT_GRAY").is_ok_and(|v| v.eq_ignore_ascii_case("true"));
        Some(if gray {
            LangfuseConfig::gray()
        } else {
            LangfuseConfig::prod()
        })
    };
    #[cfg(not(feature = "aivision"))]
    let mut config = LangfuseConfig::from_env();
    if let Some(config) = config.as_mut() {
        merge_export_headers(config);
    }
    config
}

/// Preserve the hook-only debug override as well as production selection.
pub fn resolve_hook_export_config() -> Option<LangfuseConfig> {
    if cfg!(debug_assertions)
        && crate::common::env::var("CDXHOOKS_TEST_ALLOW_ENV_EXPORTER")
            .is_ok_and(|v| matches!(v.to_ascii_lowercase().as_str(), "true" | "1" | "yes"))
    {
        if let Some(config) = LangfuseConfig::from_env() {
            return Some(config);
        }
    }
    resolve_env_export_config()
}

// --- Constants ---

// The aivision edition loads Langfuse settings through build.rs.
// Common builds read exporter settings at runtime.
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

pub const AGENT_TYPE: &str = "codex";
#[cfg(feature = "aivision")]
pub const AGENT_PRODUCT: &str = env!("LANGFUSE_BUILTIN_AGENT_PRODUCT");
#[cfg(not(feature = "aivision"))]
pub const AGENT_PRODUCT: &str = "Codex";

pub fn resolve_agent_product() -> String {
    agentic_otel_common::env_or_default(
        "CDXHOOKS_AGENT_PRODUCT",
        "AIVISION_AGENT_PRODUCT",
        AGENT_PRODUCT,
    )
}

pub fn resolve_agent_type() -> String {
    agentic_otel_common::env_or_default("CDXHOOKS_AGENT_TYPE", "AIVISION_AGENT_TYPE", AGENT_TYPE)
}

// --- Config ---

/// Parse OTLP-style headers: "key1=value1,key2=value2"
pub(crate) fn parse_otlp_headers(s: &str) -> HashMap<String, String> {
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
    /// Create config from environment variables.
    /// Returns Some when a Langfuse base URL is set or when
    /// CDXHOOKS_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT provides the full export URL.
    /// PK/SK are optional — when absent, no Authorization header is sent.
    /// Supports CDXHOOKS_ prefix with fallback: CDXHOOKS_LANGFUSE_BASE_URL > LANGFUSE_BASE_URL, etc.
    pub fn from_env() -> Option<Self> {
        use crate::hooks::langfuse::export_env_prefixed;
        let host = export_env_prefixed("CDXHOOKS_LANGFUSE_BASE_URL", "LANGFUSE_BASE_URL").or_else(
            || {
                crate::common::env::var("CDXHOOKS_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
                    .ok()
                    .filter(|value| !value.is_empty())
            },
        )?;
        let public_key = export_env_prefixed("CDXHOOKS_LANGFUSE_PUBLIC_KEY", "LANGFUSE_PUBLIC_KEY");
        let secret_key = export_env_prefixed("CDXHOOKS_LANGFUSE_SECRET_KEY", "LANGFUSE_SECRET_KEY");
        let headers = export_env_prefixed("CDXHOOKS_LANGFUSE_HEADERS", "LANGFUSE_HEADERS")
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
            serde_json::from_str(LANGFUSE_HEADERS_PROD).ok()
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
    use crate::common::hook::env_prefixed;
    let mut metadata: HashMap<String, Value> = HashMap::new();

    // CDXHOOKS_LANGFUSE_METADATA > LANGFUSE_METADATA
    if let Some(json_str) = env_prefixed("CDXHOOKS_LANGFUSE_METADATA", "LANGFUSE_METADATA") {
        if let Ok(parsed) = serde_json::from_str::<HashMap<String, Value>>(&json_str) {
            metadata.extend(parsed);
        }
    }

    // CDXHOOKS_LANGFUSE_TAGS > LANGFUSE_TAGS
    if let Some(tags_str) = env_prefixed("CDXHOOKS_LANGFUSE_TAGS", "LANGFUSE_TAGS") {
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

    // CDXHOOKS_ATTR_* > LANGFUSE_ATTR_* (collect both, CDXHOOKS_ takes precedence)
    let mut attr_map: HashMap<String, String> = HashMap::new();
    for (key, value) in std::env::vars() {
        if key.starts_with("LANGFUSE_ATTR_") {
            if key == "LANGFUSE_ATTR_USERNAME" || key == "LANGFUSE_ATTR_ANT_USERNAME" {
                continue;
            }
            let suffix = key["LANGFUSE_ATTR_".len()..].to_lowercase();
            attr_map.entry(suffix).or_insert(value);
        }
    }
    for (key, value) in std::env::vars() {
        if key.starts_with("CDXHOOKS_ATTR_") {
            let suffix = key["CDXHOOKS_ATTR_".len()..].to_lowercase();
            attr_map.insert(suffix, value); // CDXHOOKS_ overrides LANGFUSE_ATTR_
        }
    }
    for (suffix, value) in attr_map {
        metadata.insert(suffix, Value::String(value));
    }

    // Remove reserved keys
    for reserved in &[
        "username",
        "ant.username",
        "codex.username",
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

    #[cfg(not(feature = "aivision"))]
    #[test]
    fn test_from_env_accepts_standalone_otlp_traces_endpoint() {
        let _guard = crate::test_sync::env_lock();
        let agent_base_url = std::env::var("CDXHOOKS_LANGFUSE_BASE_URL").ok();
        let shared_base_url = std::env::var("LANGFUSE_BASE_URL").ok();
        let otlp_endpoint = std::env::var("CDXHOOKS_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT").ok();

        std::env::remove_var("CDXHOOKS_LANGFUSE_BASE_URL");
        std::env::remove_var("LANGFUSE_BASE_URL");
        std::env::set_var(
            "CDXHOOKS_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
            "https://collector.example.test/v1/traces",
        );

        let config = LangfuseConfig::from_env()
            .expect("standalone OTLP traces endpoint should create common config");
        assert_eq!(config.host, "https://collector.example.test/v1/traces");

        restore_env("CDXHOOKS_LANGFUSE_BASE_URL", agent_base_url);
        restore_env("LANGFUSE_BASE_URL", shared_base_url);
        restore_env("CDXHOOKS_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", otlp_endpoint);
    }

    #[cfg(not(feature = "aivision"))]
    fn restore_env(key: &str, value: Option<String>) {
        match value {
            Some(value) => std::env::set_var(key, value),
            None => std::env::remove_var(key),
        }
    }

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
