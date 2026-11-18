//! Diff algorithm for comparing OTEL signals

use serde_json::Value;
use std::collections::HashMap;

/// Type of difference detected
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DiffType {
    /// Field exists in golden but not in actual
    Removed,
    /// Field exists in actual but not in golden
    Added,
    /// Field exists in both but values differ
    Modified,
    /// Field is ignored (not compared)
    Ignored,
    /// Field values match
    Unchanged,
}

impl DiffType {
    pub fn is_changed(&self) -> bool {
        matches!(
            self,
            DiffType::Removed | DiffType::Added | DiffType::Modified
        )
    }

    pub fn label(&self) -> &'static str {
        match self {
            DiffType::Removed => "Removed",
            DiffType::Added => "Added",
            DiffType::Modified => "Modified",
            DiffType::Ignored => "Ignored",
            DiffType::Unchanged => "Unchanged",
        }
    }
}

/// Result of a single diff comparison
#[derive(Debug, Clone)]
pub struct DiffResult {
    /// Path to the field (e.g., "span.attributes[session.id]")
    pub path: String,
    /// Value in the golden file
    pub golden_value: Option<String>,
    /// Value in the actual file
    pub actual_value: Option<String>,
    /// Type of difference
    pub diff_type: DiffType,
}

impl DiffResult {
    pub fn new(
        path: String,
        golden: Option<String>,
        actual: Option<String>,
        diff_type: DiffType,
    ) -> Self {
        Self {
            path,
            golden_value: golden,
            actual_value: actual,
            diff_type,
        }
    }

    /// Check if this is a meaningful difference (not ignored)
    pub fn is_meaningful(&self) -> bool {
        self.diff_type.is_changed()
    }
}

/// Compare two OTEL signals and return list of differences
pub fn compare_signals(
    golden: &Value,
    actual: &Value,
    ignore_fields: &[String],
    exact_fields: &[String],
    tolerance_map: &HashMap<String, f64>,
) -> Vec<DiffResult> {
    let mut results = Vec::new();

    // Extract all attributes from both signals
    let golden_attrs = extract_all_attributes(golden);
    let actual_attrs = extract_all_attributes(actual);

    // Get all unique keys
    let mut all_keys: Vec<String> = golden_attrs.keys().cloned().collect();
    for key in actual_attrs.keys() {
        if !all_keys.contains(&key.to_string()) {
            all_keys.push(key.clone());
        }
    }
    all_keys.sort();

    // Compare each attribute
    for key in all_keys {
        let key_ref = &key;
        let golden_val = golden_attrs.get(key_ref);
        let actual_val = actual_attrs.get(key_ref);

        // Check if field should be ignored
        if should_ignore(key_ref, ignore_fields) {
            results.push(DiffResult::new(
                key.clone(),
                golden_val.cloned(),
                actual_val.cloned(),
                DiffType::Ignored,
            ));
            continue;
        }

        match (golden_val, actual_val) {
            (Some(g), Some(a)) => {
                // Both exist - compare values
                let diff_type = if should_ignore(key_ref, ignore_fields) {
                    DiffType::Ignored
                } else if is_exact_field(key_ref, exact_fields) {
                    if g == a {
                        DiffType::Unchanged
                    } else {
                        DiffType::Modified
                    }
                } else if let Some(tolerance) = tolerance_map.get(key_ref) {
                    // Numeric tolerance comparison
                    if compare_with_tolerance(g, a, *tolerance) {
                        DiffType::Unchanged
                    } else {
                        DiffType::Modified
                    }
                } else {
                    // Default string comparison
                    if g == a {
                        DiffType::Unchanged
                    } else {
                        DiffType::Modified
                    }
                };

                results.push(DiffResult::new(
                    key.clone(),
                    Some(g.clone()),
                    Some(a.clone()),
                    diff_type,
                ));
            }
            (Some(g), None) => {
                results.push(DiffResult::new(
                    key.clone(),
                    Some(g.clone()),
                    None,
                    DiffType::Removed,
                ));
            }
            (None, Some(a)) => {
                results.push(DiffResult::new(key, None, Some(a.clone()), DiffType::Added));
            }
            (None, None) => {}
        }
    }

    results
}

/// Extract all attributes from an OTEL signal as a flat map
fn extract_all_attributes(value: &Value) -> HashMap<String, String> {
    let mut attrs = HashMap::new();
    extract_attributes_recursive(value, "", &mut attrs);
    attrs
}

fn extract_attributes_recursive(value: &Value, prefix: &str, attrs: &mut HashMap<String, String>) {
    match value {
        Value::Object(map) => {
            // Check if this is an attribute with key/value
            if let (Some(key), Some(val)) = (map.get("key"), map.get("value")) {
                let key_str = key.as_str().unwrap_or("");
                let val_str = extract_value_string(val);
                let path = if prefix.is_empty() {
                    key_str.to_string()
                } else {
                    format!("{}.{}", prefix, key_str)
                };
                attrs.insert(path, val_str);
                return;
            }

            // Also check for "name" format
            if let (Some(name), Some(val)) = (map.get("name"), map.get("value")) {
                let name_str = name.as_str().unwrap_or("");
                let val_str = extract_value_string(val);
                let path = if prefix.is_empty() {
                    name_str.to_string()
                } else {
                    format!("{}.{}", prefix, name_str)
                };
                attrs.insert(path, val_str);
                return;
            }

            // Recurse into object fields
            for (k, v) in map {
                let new_prefix = if prefix.is_empty() {
                    k.clone()
                } else {
                    format!("{}.{}", prefix, k)
                };
                extract_attributes_recursive(v, &new_prefix, attrs);
            }
        }
        Value::Array(arr) => {
            // Detect OTLP keyed arrays: [{key: "...", value: ...}, ...]
            // Use attribute key as path component instead of array index
            // to avoid index-shift noise when attributes are reordered or inserted.
            let is_keyed_array = !arr.is_empty()
                && arr.iter().all(|v| {
                    v.as_object().map_or(false, |m| {
                        (m.contains_key("key") || m.contains_key("name")) && m.contains_key("value")
                    })
                });

            if is_keyed_array {
                for v in arr {
                    extract_attributes_recursive(v, prefix, attrs);
                }
            } else {
                for (i, v) in arr.iter().enumerate() {
                    let new_prefix = format!("{}[{}]", prefix, i);
                    extract_attributes_recursive(v, &new_prefix, attrs);
                }
            }
        }
        _ => {
            // Leaf value - store it
            if !prefix.is_empty() {
                attrs.insert(prefix.to_string(), extract_value_string(value));
            }
        }
    }
}

/// Extract string representation of an OTEL attribute value
fn extract_value_string(value: &Value) -> String {
    match value {
        Value::String(s) => s.clone(),
        Value::Number(n) => n.to_string(),
        Value::Bool(b) => b.to_string(),
        Value::Null => "null".to_string(),
        Value::Object(map) => {
            // OTEL value types: stringValue, intValue, doubleValue, boolValue, arrayValue, kvlistValue
            for key in &["stringValue", "intValue", "doubleValue", "boolValue"] {
                if let Some(v) = map.get(*key) {
                    return extract_value_string(v);
                }
            }
            // For arrayValue
            if let Some(arr) = map.get("arrayValue") {
                if let Some(values) = arr.as_array() {
                    let items: Vec<String> = values.iter().map(extract_value_string).collect();
                    return format!("[{}]", items.join(", "));
                }
            }
            // For kvlistValue
            if let Some(kv) = map.get("kvlistValue") {
                if let Some(values) = kv.get("values").and_then(|v| v.as_array()) {
                    let items: Vec<String> = values
                        .iter()
                        .map(|v| {
                            let k = v.get("key").and_then(|k| k.as_str()).unwrap_or("");
                            let val = extract_value_string(v.get("value").unwrap_or(&Value::Null));
                            format!("{}={}", k, val)
                        })
                        .collect();
                    return format!("{{{}}}", items.join(", "));
                }
            }
            value.to_string()
        }
        Value::Array(arr) => {
            let items: Vec<String> = arr.iter().map(extract_value_string).collect();
            format!("[{}]", items.join(", "))
        }
    }
}

/// Check if a field should be ignored
fn should_ignore(field_path: &str, ignore_fields: &[String]) -> bool {
    for pattern in ignore_fields {
        if pattern.contains('*') {
            if matches_wildcard_pattern(field_path, pattern) {
                return true;
            }
        } else if field_path == pattern || field_path.ends_with(&format!(".{}", pattern)) {
            return true;
        }
    }
    false
}

/// Check if field matches a wildcard pattern
fn matches_wildcard_pattern(field_path: &str, pattern: &str) -> bool {
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
}

/// Check if field must match exactly
fn is_exact_field(field_path: &str, exact_fields: &[String]) -> bool {
    exact_fields.iter().any(|f| {
        if f.contains('*') {
            matches_wildcard_pattern(field_path, f)
        } else {
            field_path == f
        }
    })
}

/// Compare two values with numeric tolerance
fn compare_with_tolerance(golden: &str, actual: &str, tolerance: f64) -> bool {
    let golden_num: Option<f64> = golden.parse().ok();
    let actual_num: Option<f64> = actual.parse().ok();

    if let (Some(g), Some(a)) = (golden_num, actual_num) {
        if g == 0.0 {
            return a == 0.0;
        }
        let diff = (a - g).abs() / g;
        return diff <= tolerance;
    }

    // If not both numbers, do string comparison
    golden == actual
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_extract_value_string_string() {
        let value = serde_json::json!({"stringValue": "test"});
        assert_eq!(extract_value_string(&value), "test");
    }

    #[test]
    fn test_extract_value_string_int() {
        let value = serde_json::json!({"intValue": "42"});
        assert_eq!(extract_value_string(&value), "42");
    }

    #[test]
    fn test_extract_value_string_nested() {
        let value = serde_json::json!({
            "attributes": [
                {"key": "session.id", "value": {"stringValue": "abc123"}},
                {"key": "turn_number", "value": {"intValue": "1"}}
            ]
        });
        let attrs = extract_all_attributes(&value);
        // OTLP keyed arrays use attribute key as path, not array index
        assert_eq!(
            attrs.get("attributes.session.id"),
            Some(&"abc123".to_string())
        );
        assert_eq!(attrs.get("attributes.turn_number"), Some(&"1".to_string()));
    }

    #[test]
    fn test_keyed_array_index_independent() {
        // Inserting a new attribute should not shift other attribute paths
        let golden = serde_json::json!({
            "attributes": [
                {"key": "a", "value": {"stringValue": "1"}},
                {"key": "b", "value": {"stringValue": "2"}}
            ]
        });
        let actual = serde_json::json!({
            "attributes": [
                {"key": "a", "value": {"stringValue": "1"}},
                {"key": "new_attr", "value": {"stringValue": "x"}},
                {"key": "b", "value": {"stringValue": "2"}}
            ]
        });
        let golden_attrs = extract_all_attributes(&golden);
        let actual_attrs = extract_all_attributes(&actual);

        // "a" and "b" should match regardless of position
        assert_eq!(
            golden_attrs.get("attributes.a"),
            actual_attrs.get("attributes.a")
        );
        assert_eq!(
            golden_attrs.get("attributes.b"),
            actual_attrs.get("attributes.b")
        );
        // "new_attr" only in actual
        assert!(golden_attrs.get("attributes.new_attr").is_none());
        assert_eq!(
            actual_attrs.get("attributes.new_attr"),
            Some(&"x".to_string())
        );
    }

    #[test]
    fn test_regular_array_still_uses_index() {
        // Non-keyed arrays should still use index-based paths
        let value = serde_json::json!({
            "items": ["a", "b", "c"]
        });
        let attrs = extract_all_attributes(&value);
        assert_eq!(attrs.get("items[0]"), Some(&"a".to_string()));
        assert_eq!(attrs.get("items[1]"), Some(&"b".to_string()));
        assert_eq!(attrs.get("items[2]"), Some(&"c".to_string()));
    }

    #[test]
    fn test_should_ignore() {
        let ignore_fields = vec!["traceId".to_string(), "*.timeUnixNano".to_string()];
        assert!(should_ignore("traceId", &ignore_fields));
        assert!(should_ignore("startTimeUnixNano", &ignore_fields));
        assert!(!should_ignore("session.id", &ignore_fields));
    }

    #[test]
    fn test_compare_with_tolerance() {
        // Within 5% tolerance
        assert!(compare_with_tolerance("100", "102", 0.05));
        assert!(compare_with_tolerance("100", "105", 0.05));
        // Outside tolerance
        assert!(!compare_with_tolerance("100", "110", 0.05));
        // String comparison for non-numbers
        assert!(compare_with_tolerance("abc", "abc", 0.05));
        assert!(!compare_with_tolerance("abc", "def", 0.05));
    }
}
