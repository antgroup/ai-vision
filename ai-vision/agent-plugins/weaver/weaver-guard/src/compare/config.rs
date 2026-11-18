//! Configuration for golden replay compare

use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::path::PathBuf;

/// Configuration for golden replay comparison
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct CompareConfig {
    /// Fields to ignore during comparison
    #[serde(default)]
    pub ignore: IgnoreConfig,

    /// Fields that must match exactly
    #[serde(default)]
    pub exact: ExactConfig,

    /// Tolerance for numeric fields (percentage as decimal, e.g. 0.05 = 5%)
    #[serde(default)]
    pub tolerance: HashMap<String, f64>,
}

impl CompareConfig {
    /// Load config from a TOML file
    pub fn from_file(path: &PathBuf) -> anyhow::Result<Self> {
        let content = std::fs::read_to_string(path)?;
        let config: CompareConfig = toml::from_str(&content)?;
        Ok(config)
    }

    /// Get default config with common ignored fields
    pub fn default_config() -> Self {
        let mut ignore = IgnoreConfig::default();
        ignore.timestamp_fields = vec![
            "startTimeUnixNano".to_string(),
            "endTimeUnixNano".to_string(),
            "timeUnixNano".to_string(),
            "observedTimeUnixNano".to_string(),
            "*.timeUnixNano".to_string(),
        ];
        ignore.id_fields = vec![
            "traceId".to_string(),
            "spanId".to_string(),
            "parentSpanId".to_string(),
            "session.id".to_string(),
            "*.id".to_string(),
        ];
        ignore.resource_fields = vec![
            "service.name".to_string(),
            "service.version".to_string(),
            "service.instance.id".to_string(),
        ];

        let mut tolerance = HashMap::new();
        tolerance.insert("gen_ai.usage.input_tokens".to_string(), 0.05);
        tolerance.insert("gen_ai.usage.output_tokens".to_string(), 0.05);
        tolerance.insert("gen_ai.usage.total_tokens".to_string(), 0.05);

        CompareConfig {
            ignore,
            exact: ExactConfig::default(),
            tolerance,
        }
    }
}

/// Configuration for fields to ignore
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct IgnoreConfig {
    /// Timestamp fields to ignore
    #[serde(default, rename = "timestamp_fields")]
    pub timestamp_fields: Vec<String>,

    /// ID fields to ignore
    #[serde(default, rename = "id_fields")]
    pub id_fields: Vec<String>,

    /// Resource fields to ignore
    #[serde(default, rename = "resource_fields")]
    pub resource_fields: Vec<String>,
}

impl IgnoreConfig {
    /// Get all ignore patterns as a flat vector
    pub fn all_patterns(&self) -> Vec<String> {
        let mut patterns = Vec::new();
        patterns.extend(self.timestamp_fields.clone());
        patterns.extend(self.id_fields.clone());
        patterns.extend(self.resource_fields.clone());
        patterns
    }

    /// Check if a field path matches any ignore pattern
    pub fn matches(&self, field_path: &str) -> bool {
        let patterns = self.all_patterns();
        for pattern in patterns {
            if self::match_pattern(field_path, &pattern) {
                return true;
            }
        }
        false
    }
}

/// Configuration for exact match fields
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct ExactConfig {
    /// Fields that must match exactly
    #[serde(default, rename = "fields")]
    pub fields: Vec<String>,
}

impl ExactConfig {
    /// Check if a field must match exactly
    pub fn is_exact(&self, field_path: &str) -> bool {
        self.fields.iter().any(|f| {
            if f.contains('*') {
                self::match_pattern(field_path, f)
            } else {
                field_path == f
            }
        })
    }
}

/// Match a field path against a pattern (supports * wildcard)
fn match_pattern(field_path: &str, pattern: &str) -> bool {
    if pattern.contains('*') {
        let field_path = field_path.to_ascii_lowercase();
        let pattern = pattern.to_ascii_lowercase();
        let pattern_parts: Vec<&str> = pattern.split('*').collect();
        let mut pos = 0;
        for part in pattern_parts {
            if part.is_empty() {
                continue;
            }
            let fallback = part.strip_prefix('.');
            let found = field_path[pos..]
                .find(part)
                .map(|index| (index, part.len()))
                .or_else(|| {
                    fallback.and_then(|candidate| {
                        field_path[pos..]
                            .find(candidate)
                            .map(|index| (index, candidate.len()))
                    })
                });
            if let Some((found, len)) = found {
                pos += found + len;
            } else {
                return false;
            }
        }
        true
    } else {
        field_path == pattern
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_match_pattern_exact() {
        assert!(match_pattern("traceId", "traceId"));
        assert!(!match_pattern("spanId", "traceId"));
    }

    #[test]
    fn test_match_pattern_wildcard() {
        assert!(match_pattern("startTimeUnixNano", "*.timeUnixNano"));
        assert!(match_pattern("endTimeUnixNano", "*.timeUnixNano"));
        assert!(!match_pattern("traceId", "*.timeUnixNano"));
    }

    #[test]
    fn test_ignore_config_matches() {
        let config = IgnoreConfig {
            timestamp_fields: vec!["startTimeUnixNano".to_string()],
            id_fields: vec!["traceId".to_string()],
            resource_fields: vec![],
        };

        assert!(config.matches("startTimeUnixNano"));
        assert!(config.matches("traceId"));
        assert!(!config.matches("session.id"));
    }

    #[test]
    fn test_exact_config_is_exact() {
        let config = ExactConfig {
            fields: vec!["gen_ai.system".to_string(), "source".to_string()],
        };

        assert!(config.is_exact("gen_ai.system"));
        assert!(config.is_exact("source"));
        assert!(!config.is_exact("session.id"));
    }
}
