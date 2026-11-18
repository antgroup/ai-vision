//! Semconv module for YAML loading and attribute diffing
//!
//! Loads semantic convention YAML files and compares observed attributes
//! against expected attributes defined in the semconv registry.

use crate::error::WeaverGuardError;
use crate::jsonl::{OtelSignal, SignalType};
use serde::Deserialize;
use serde_json::Value;
use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};
use tracing::{debug, warn};

/// A loaded semconv registry for attribute diffing
pub struct SemconvRegistry {
    /// All known attributes by ID
    attributes: HashMap<String, AttributeDef>,
    /// Span definitions by span ID (without "span." prefix)
    spans: HashMap<String, SpanDef>,
    /// Log definitions
    logs: HashMap<String, LogDef>,
    /// Metric definitions
    metrics: HashMap<String, MetricDef>,
}

#[derive(Debug, Clone)]
pub struct AttributeDef {
    pub id: String,
    pub attr_type: String,
    pub requirement_level: RequirementLevel,
    pub brief: Option<String>,
    pub stability: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RequirementLevel {
    Required,
    ConditionallyRequired(String),
    Recommended,
    OptIn,
}

impl RequirementLevel {
    pub fn is_required(&self) -> bool {
        matches!(self, RequirementLevel::Required)
    }

    pub fn is_recommended(&self) -> bool {
        matches!(self, RequirementLevel::Recommended)
    }

    /// Convert from serde_yml::Value (handles both string and map)
    pub fn from_yaml_value(value: &serde_yml::Value) -> Self {
        match value {
            serde_yml::Value::Null => RequirementLevel::Recommended,
            serde_yml::Value::String(s) => RequirementLevel::from(s.as_str()),
            serde_yml::Value::Mapping(m) => {
                // Handle { conditionally_required: "condition" }
                if let Some(serde_yml::Value::String(cond)) = m.keys().next() {
                    RequirementLevel::ConditionallyRequired(cond.clone())
                } else {
                    RequirementLevel::Recommended
                }
            }
            _ => RequirementLevel::Recommended,
        }
    }
}

impl From<&str> for RequirementLevel {
    fn from(s: &str) -> Self {
        match s {
            "required" => RequirementLevel::Required,
            "recommended" => RequirementLevel::Recommended,
            "opt_in" => RequirementLevel::OptIn,
            _ => RequirementLevel::ConditionallyRequired(s.to_string()),
        }
    }
}

#[derive(Debug, Clone)]
pub struct SpanDef {
    pub id: String,
    pub span_kind: Option<String>,
    pub expected_attributes: Vec<SpanAttributeRef>,
}

#[derive(Debug, Clone)]
pub struct SpanAttributeRef {
    pub attr_ref: String,
    pub requirement_level: RequirementLevel,
}

#[derive(Debug, Clone)]
pub struct LogDef {
    pub id: String,
    pub expected_attributes: Vec<SpanAttributeRef>,
}

#[derive(Debug, Clone)]
pub struct MetricDef {
    pub id: String,
    pub expected_attributes: Vec<SpanAttributeRef>,
}

/// A finding from semconv attribute diff
#[derive(Debug, Clone)]
pub struct SemconvFinding {
    pub finding_type: SemconvFindingType,
    pub attribute_id: String,
    pub message: String,
    pub span_name: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SemconvFindingType {
    MissingRequired,
    MissingRecommended,
    UnknownAttribute,
    TypeMismatch,
}

impl std::fmt::Display for SemconvFindingType {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            SemconvFindingType::MissingRequired => write!(f, "missing_required"),
            SemconvFindingType::MissingRecommended => write!(f, "missing_recommended"),
            SemconvFindingType::UnknownAttribute => write!(f, "unknown_attribute"),
            SemconvFindingType::TypeMismatch => write!(f, "type_mismatch"),
        }
    }
}

/// Load and merge semconv registries from multiple directories
///
/// Later directories can override/extend definitions from earlier ones,
/// so you can layer a base semconv with vendor-specific extensions.
pub fn load_semconv_registry_from_dirs(
    dirs: &[PathBuf],
) -> Result<SemconvRegistry, WeaverGuardError> {
    let mut merged = SemconvRegistry {
        attributes: HashMap::new(),
        spans: HashMap::new(),
        logs: HashMap::new(),
        metrics: HashMap::new(),
    };

    for dir in dirs {
        let partial = load_semconv_registry(dir)?;
        merged.attributes.extend(partial.attributes);
        merged.spans.extend(partial.spans);
        merged.logs.extend(partial.logs);
        merged.metrics.extend(partial.metrics);
    }

    debug!(
        attributes = merged.attributes.len(),
        spans = merged.spans.len(),
        logs = merged.logs.len(),
        metrics = merged.metrics.len(),
        dirs = dirs.len(),
        "Merged semconv registries"
    );

    Ok(merged)
}

/// Load semconv registry from a directory containing YAML files
pub fn load_semconv_registry(dir: &Path) -> Result<SemconvRegistry, WeaverGuardError> {
    let mut registry = SemconvRegistry {
        attributes: HashMap::new(),
        spans: HashMap::new(),
        logs: HashMap::new(),
        metrics: HashMap::new(),
    };

    let entries = fs::read_dir(dir).map_err(|e| {
        WeaverGuardError::Semconv(format!("Failed to read semconv directory: {}", e))
    })?;

    for entry in entries.flatten() {
        let path = entry.path();
        if path.extension().and_then(|e| e.to_str()) == Some("yaml")
            || path.extension().and_then(|e| e.to_str()) == Some("yml")
        {
            debug!(file = %path.display(), "Loading semconv YAML");
            let content = fs::read_to_string(&path).map_err(|e| {
                WeaverGuardError::Semconv(format!("Failed to read {}: {}", path.display(), e))
            })?;

            let yaml: SemconvYaml = serde_yml::from_str(&content).map_err(|e| {
                WeaverGuardError::Semconv(format!("Failed to parse {}: {}", path.display(), e))
            })?;

            for group in yaml.groups {
                process_group(&mut registry, group);
            }
        }
    }

    debug!(
        attributes = registry.attributes.len(),
        spans = registry.spans.len(),
        "Loaded semconv registry"
    );

    Ok(registry)
}

fn process_group(registry: &mut SemconvRegistry, group: SemconvGroup) {
    let group_type = group.r#type.as_deref().unwrap_or("");

    match group_type {
        "attribute_group" => {
            for attr in group.attributes.unwrap_or_default() {
                if let Some(attr_id) = attr.id {
                    let attr_def = AttributeDef {
                        id: attr_id.clone(),
                        attr_type: attr.r#type.unwrap_or_else(|| "string".to_string()),
                        requirement_level: attr
                            .requirement_level
                            .as_ref()
                            .map(RequirementLevel::from_yaml_value)
                            .unwrap_or(RequirementLevel::Recommended),
                        brief: attr.brief,
                        stability: attr.stability,
                    };
                    registry.attributes.insert(attr_id, attr_def);
                }
            }
        }
        "span" => {
            let span_id = group.id.trim_start_matches("span.").to_string();
            let span_def = SpanDef {
                id: span_id.clone(),
                span_kind: group.span_kind,
                expected_attributes: group
                    .attributes
                    .unwrap_or_default()
                    .into_iter()
                    .map(|attr| SpanAttributeRef {
                        attr_ref: attr.ref_id.or(attr.id).unwrap_or_default(),
                        requirement_level: attr
                            .requirement_level
                            .as_ref()
                            .map(RequirementLevel::from_yaml_value)
                            .unwrap_or(RequirementLevel::Recommended),
                    })
                    .collect(),
            };
            registry.spans.insert(span_id, span_def);
        }
        "event" => {
            let log_id = group.id.trim_start_matches("event.").to_string();
            let log_def = LogDef {
                id: log_id.clone(),
                expected_attributes: group
                    .attributes
                    .unwrap_or_default()
                    .into_iter()
                    .map(|attr| SpanAttributeRef {
                        attr_ref: attr.ref_id.or(attr.id).unwrap_or_default(),
                        requirement_level: attr
                            .requirement_level
                            .as_ref()
                            .map(RequirementLevel::from_yaml_value)
                            .unwrap_or(RequirementLevel::Recommended),
                    })
                    .collect(),
            };
            registry.logs.insert(log_id, log_def);
        }
        "metric" => {
            let metric_id = group.id.trim_start_matches("metric.").to_string();
            let metric_def = MetricDef {
                id: metric_id.clone(),
                expected_attributes: group
                    .attributes
                    .unwrap_or_default()
                    .into_iter()
                    .map(|attr| SpanAttributeRef {
                        attr_ref: attr.ref_id.or(attr.id).unwrap_or_default(),
                        requirement_level: attr
                            .requirement_level
                            .as_ref()
                            .map(RequirementLevel::from_yaml_value)
                            .unwrap_or(RequirementLevel::Recommended),
                    })
                    .collect(),
            };
            registry.metrics.insert(metric_id, metric_def);
        }
        // entity — extract attributes into the registry (same as attribute_group)
        "entity" => {
            for attr in group.attributes.unwrap_or_default() {
                if let Some(attr_id) = attr.id {
                    let attr_def = AttributeDef {
                        id: attr_id.clone(),
                        attr_type: attr.r#type.unwrap_or_else(|| "string".to_string()),
                        requirement_level: attr
                            .requirement_level
                            .as_ref()
                            .map(RequirementLevel::from_yaml_value)
                            .unwrap_or(RequirementLevel::Recommended),
                        brief: attr.brief,
                        stability: attr.stability,
                    };
                    registry.attributes.insert(attr_id, attr_def);
                }
            }
            debug!(id = %group.id, "Loaded entity attributes into registry");
        }
        // resource, etc. — valid semconv types not used for validation
        "resource" => {
            debug!(id = %group.id, type = %group_type, "Skipping non-signal semconv group");
        }
        _ => {
            warn!(id = %group.id, type = %group_type, "Unknown semconv group type");
        }
    }
}

/// Diff attributes using a SignalType directly (no OtelSignal wrapper needed).
///
/// This is useful when the caller already knows the signal type (e.g. from an
/// API response) and has constructed the OTLP sample without going through
/// JSONL file parsing.
pub fn diff_attributes_from_signal_type(
    registry: &SemconvRegistry,
    signal_type: SignalType,
    sample: &Value,
) -> Vec<SemconvFinding> {
    let signal = OtelSignal {
        signal_type,
        data: Value::Null,
        line_number: 0,
        source_file: None,
    };
    diff_attributes(registry, &signal, sample)
}

/// Diff attributes in a sample against the semconv registry
pub fn diff_attributes(
    registry: &SemconvRegistry,
    signal: &OtelSignal,
    sample: &Value,
) -> Vec<SemconvFinding> {
    let mut findings = Vec::new();

    // Extract attribute keys from the sample
    let attr_keys = extract_attribute_keys_from_sample(sample);
    let sample_name = sample_name_from_sample(sample);

    // Determine what definition to check against based on signal type
    match signal.signal_type {
        SignalType::Traces => {
            if let Some(span_def) = registry.spans.get(&sample_name) {
                for expected in &span_def.expected_attributes {
                    if !attr_keys.contains(&expected.attr_ref) {
                        if expected.requirement_level.is_required() {
                            findings.push(SemconvFinding {
                                finding_type: SemconvFindingType::MissingRequired,
                                attribute_id: expected.attr_ref.clone(),
                                message: format!(
                                    "Required attribute '{}' is missing",
                                    expected.attr_ref
                                ),
                                span_name: Some(sample_name.clone()),
                            });
                        } else if expected.requirement_level.is_recommended() {
                            findings.push(SemconvFinding {
                                finding_type: SemconvFindingType::MissingRecommended,
                                attribute_id: expected.attr_ref.clone(),
                                message: format!(
                                    "Recommended attribute '{}' is missing",
                                    expected.attr_ref
                                ),
                                span_name: Some(sample_name.clone()),
                            });
                        }
                    }
                }
            }
            // Check for unknown attributes
            for key in &attr_keys {
                if !key.starts_with("custom.")
                    && !registry.attributes.contains_key(key)
                    && !key.starts_with("telemetry.")
                    && !key.starts_with("service.")
                    && !key.starts_with("gen_ai.")
                    && !key.starts_with("error.")
                    && !key.starts_with("tool.")
                    && !key.starts_with("langfuse.")
                {
                    findings.push(SemconvFinding {
                        finding_type: SemconvFindingType::UnknownAttribute,
                        attribute_id: key.clone(),
                        message: format!("Attribute '{}' is not defined in semconv registry", key),
                        span_name: Some(sample_name.clone()),
                    });
                }
            }
        }
        _ => {
            // For logs and metrics, just check for unknown attributes for now
            for key in &attr_keys {
                if !key.starts_with("custom.") && !registry.attributes.contains_key(key) {
                    findings.push(SemconvFinding {
                        finding_type: SemconvFindingType::UnknownAttribute,
                        attribute_id: key.clone(),
                        message: format!("Attribute '{}' is not defined in semconv registry", key),
                        span_name: None,
                    });
                }
            }
        }
    }

    findings
}

fn extract_attribute_keys_from_sample(sample: &Value) -> Vec<String> {
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

fn sample_name_from_sample(sample: &Value) -> String {
    if let Some(span) = sample.get("sample").and_then(|s| s.get("span")) {
        span.get("name")
            .and_then(|n| n.as_str())
            .unwrap_or("<unnamed>")
            .to_string()
    } else if let Some(metric) = sample.get("sample").and_then(|s| s.get("metric")) {
        metric
            .get("name")
            .and_then(|n| n.as_str())
            .unwrap_or("<unnamed>")
            .to_string()
    } else if let Some(log) = sample.get("sample").and_then(|s| s.get("log")) {
        log.get("body")
            .and_then(|b| b.as_str())
            .map(|s| {
                if s.len() > 30 {
                    format!("{}...", &s[..30])
                } else {
                    s.to_string()
                }
            })
            .unwrap_or_else(|| "<log>".to_string())
    } else {
        "<unknown>".to_string()
    }
}

// YAML parsing structs
#[derive(Debug, Deserialize)]
struct SemconvYaml {
    groups: Vec<SemconvGroup>,
}

#[derive(Debug, Deserialize)]
#[allow(dead_code)]
struct SemconvGroup {
    id: String,
    #[serde(rename = "type")]
    r#type: Option<String>,
    #[serde(default)]
    span_kind: Option<String>,
    #[serde(default)]
    attributes: Option<Vec<SemconvAttribute>>,
    // Extra YAML fields — kept for forward-compatible deserialization
    #[serde(default)]
    display_name: Option<String>,
    #[serde(default)]
    brief: Option<String>,
    #[serde(default)]
    note: Option<String>,
    #[serde(default)]
    stability: Option<String>,
    #[serde(default)]
    instrument: Option<String>,
    #[serde(default)]
    unit: Option<String>,
    #[serde(default)]
    metric_name: Option<String>,
    #[serde(default)]
    body: Option<serde_yml::Value>,
}

#[derive(Debug, Deserialize)]
#[allow(dead_code)]
struct SemconvAttribute {
    #[serde(default)]
    id: Option<String>,
    #[serde(default, deserialize_with = "deserialize_attr_type")]
    r#type: Option<String>,
    #[serde(default)]
    brief: Option<String>,
    #[serde(default)]
    requirement_level: Option<serde_yml::Value>,
    #[serde(rename = "ref", default)]
    ref_id: Option<String>,
    #[serde(default)]
    note: Option<String>,
    #[serde(default)]
    stability: Option<String>,
    #[serde(default)]
    examples: Option<serde_yml::Value>,
}

/// Deserialize attribute type: can be a plain string ("string", "int") or
/// an enum map with `members` and optional `allow_custom_values`.
fn deserialize_attr_type<'de, D>(deserializer: D) -> Result<Option<String>, D::Error>
where
    D: serde::Deserializer<'de>,
{
    let value: Option<serde_yml::Value> = Option::deserialize(deserializer)?;
    match value {
        None => Ok(None),
        Some(serde_yml::Value::String(s)) => Ok(Some(s)),
        Some(serde_yml::Value::Mapping(_)) => {
            // Enum type definition - treat as "enum" for type tracking
            Ok(Some("enum".to_string()))
        }
        Some(other) => Ok(Some(format!("{:?}", other))),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_requirement_level_from_str() {
        assert_eq!(
            RequirementLevel::from("required"),
            RequirementLevel::Required
        );
        assert_eq!(
            RequirementLevel::from("recommended"),
            RequirementLevel::Recommended
        );
        assert_eq!(RequirementLevel::from("opt_in"), RequirementLevel::OptIn);
        assert_eq!(
            RequirementLevel::from("conditionally_required: foo"),
            RequirementLevel::ConditionallyRequired("conditionally_required: foo".to_string())
        );
    }

    #[test]
    fn test_agent_identity_attributes_are_unbounded_strings() {
        let registry = load_semconv_registry(
            &PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .parent()
                .unwrap()
                .parent()
                .unwrap()
                .join("docs/semconv"),
        )
        .unwrap();

        assert_eq!(registry.attributes["ant.agent.type"].attr_type, "string");
        assert_eq!(registry.attributes["ant.agent.product"].attr_type, "string");
    }

    #[test]
    fn test_internal_meta_root_is_an_opt_in_string() {
        let registry = load_semconv_registry(
            &PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .parent()
                .unwrap()
                .parent()
                .unwrap()
                .join("docs/semconv"),
        )
        .unwrap();

        let root = &registry.attributes["_internal.meta.root"];
        assert_eq!(root.attr_type, "string");
        assert_eq!(root.requirement_level, RequirementLevel::OptIn);
        assert_eq!(root.stability.as_deref(), Some("experimental"));

        let chat = &registry.spans["ant.chat"];
        let root_refs: Vec<_> = chat
            .expected_attributes
            .iter()
            .filter(|attribute| attribute.attr_ref == "_internal.meta.root")
            .collect();
        assert_eq!(root_refs.len(), 1);
        assert_eq!(root_refs[0].requirement_level, RequirementLevel::OptIn);
    }

    #[test]
    fn test_trigger_metadata_attributes_are_registered() {
        let registry = load_semconv_registry(
            &PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .parent()
                .unwrap()
                .parent()
                .unwrap()
                .join("docs/semconv"),
        )
        .unwrap();

        let ant_meta_trigger = &registry.attributes["ant.meta.trigger"];
        assert_eq!(ant_meta_trigger.attr_type, "enum");
        assert_eq!(
            ant_meta_trigger.requirement_level,
            RequirementLevel::ConditionallyRequired("conditionally_required".to_string())
        );
        assert_eq!(ant_meta_trigger.stability.as_deref(), Some("experimental"));

        let turn_trigger = &registry.attributes["agentic.turn.trigger"];
        assert_eq!(turn_trigger.attr_type, "enum");
        assert_eq!(
            turn_trigger.requirement_level,
            RequirementLevel::ConditionallyRequired("conditionally_required".to_string())
        );
        assert_eq!(turn_trigger.stability.as_deref(), Some("experimental"));

        let chat = &registry.spans["ant.chat"];
        let trigger_refs: Vec<_> = chat
            .expected_attributes
            .iter()
            .filter(|attribute| attribute.attr_ref == "agentic.turn.trigger")
            .collect();
        assert_eq!(trigger_refs.len(), 1);
        assert_eq!(
            trigger_refs[0].requirement_level,
            RequirementLevel::ConditionallyRequired("conditionally_required".to_string())
        );
    }
}
