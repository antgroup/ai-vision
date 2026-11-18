//! Skill tag registry: scans skill files and extracts `aivision:*` tags from YAML frontmatter.
//!
//! At session start, scans skill directories for SKILL.md files, parses their
//! YAML frontmatter `tags` field, and builds a mapping from skill name to tags.
//! Only tags with the `aivision:` prefix are extracted; the prefix is stripped.

use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};

use crate::common::dirs;

/// Tag prefix that triggers extraction. Tags without this prefix are ignored.
const TAG_PREFIX: &str = "aivision:";

/// Mapping from skill name to its extracted tags.
pub type SkillTagRegistry = HashMap<String, Vec<String>>;

/// Scan all skill directories and build a tag registry.
///
/// Directories scanned:
/// 1. `{root_dir}/skills/*/SKILL.md`
/// 2. `{root_dir}/plugins/marketplaces/*/skills/*/SKILL.md`
/// 3. `{cwd}/.claude/skills/*/SKILL.md`
pub fn scan_skill_tags(cwd: &str) -> SkillTagRegistry {
    let root = dirs::root_dir();
    let mut registry = SkillTagRegistry::new();

    let patterns: Vec<PathBuf> = vec![
        root.join("skills"),
        root.join("plugins").join("marketplaces"),
        PathBuf::from(cwd).join(".claude").join("skills"),
    ];

    for base in &patterns {
        if !base.exists() {
            continue;
        }
        scan_directory(base, &mut registry);
    }

    registry
}

/// Scan a base directory for SKILL.md files and extract tags.
///
/// Handles two layouts:
/// - `{base}/*/SKILL.md` (user skills, project skills)
/// - `{base}/*/skills/*/SKILL.md` (marketplace plugins)
fn scan_directory(base: &Path, registry: &mut SkillTagRegistry) {
    let Ok(entries) = fs::read_dir(base) else {
        return;
    };

    for entry in entries.flatten() {
        let path = entry.path();
        if !path.is_dir() {
            continue;
        }

        // Direct: {base}/{skill}/SKILL.md
        let skill_md = path.join("SKILL.md");
        if skill_md.is_file() {
            if let Some((name, tags)) = parse_skill_file(&skill_md) {
                if !tags.is_empty() {
                    registry.insert(name, tags);
                }
            }
            continue;
        }

        // Marketplace: {base}/{plugin}/skills/*/SKILL.md
        let skills_dir = path.join("skills");
        if skills_dir.is_dir() {
            if let Ok(sub_entries) = fs::read_dir(&skills_dir) {
                for sub_entry in sub_entries.flatten() {
                    let sub_path = sub_entry.path();
                    if sub_path.is_dir() {
                        let sub_skill_md = sub_path.join("SKILL.md");
                        if sub_skill_md.is_file() {
                            if let Some((name, tags)) = parse_skill_file(&sub_skill_md) {
                                if !tags.is_empty() {
                                    registry.insert(name, tags);
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

/// Parse a SKILL.md file: extract `name` and `tags` from YAML frontmatter.
///
/// Returns `None` if the file cannot be read or has no valid frontmatter.
fn parse_skill_file(path: &Path) -> Option<(String, Vec<String>)> {
    let content = fs::read_to_string(path).ok()?;
    let frontmatter = extract_frontmatter(&content)?;
    let name = extract_field(&frontmatter, "name")?;
    let tags = extract_tags(&frontmatter);
    let aivision_tags = extract_aivision_tags(&tags);
    Some((name, aivision_tags))
}

/// Extract YAML frontmatter from between `---` delimiters.
fn extract_frontmatter(content: &str) -> Option<String> {
    let trimmed = content.trim_start();
    if !trimmed.starts_with("---") {
        return None;
    }
    // Skip the opening ---
    let after_open = &trimmed[3..];
    let end_idx = after_open.find("\n---")?;
    Some(after_open[..end_idx].to_string())
}

/// Extract a simple string field value from YAML text.
/// Handles: `key: value`, `key: "value"`, `key: 'value'`
fn extract_field(yaml: &str, key: &str) -> Option<String> {
    let prefix = format!("{}:", key);
    for line in yaml.lines() {
        let trimmed = line.trim();
        if trimmed.starts_with(&prefix) {
            let value = trimmed[prefix.len()..].trim();
            // Strip quotes
            let value = value
                .trim_start_matches('"')
                .trim_end_matches('"')
                .trim_start_matches('\'')
                .trim_end_matches('\'');
            if !value.is_empty() {
                return Some(value.to_string());
            }
        }
    }
    None
}

/// Extract `tags` field as a list of strings from YAML text.
/// Handles inline array: `tags: ["a", "b"]` or `tags: [a, b]`
fn extract_tags(yaml: &str) -> Vec<String> {
    let prefix = "tags:";
    for line in yaml.lines() {
        let trimmed = line.trim();
        if let Some(stripped) = trimmed.strip_prefix(prefix) {
            let value = stripped.trim();
            return parse_yaml_inline_array(value);
        }
    }
    Vec::new()
}

/// Parse a YAML inline array like `["a", "b"]` or `[a, b]`.
fn parse_yaml_inline_array(s: &str) -> Vec<String> {
    let s = s.trim();
    if !s.starts_with('[') || !s.ends_with(']') {
        return Vec::new();
    }
    let inner = &s[1..s.len() - 1];
    inner
        .split(',')
        .map(|item| item.trim().trim_matches('"').trim_matches('\'').to_string())
        .filter(|item| !item.is_empty())
        .collect()
}

/// Filter tags with `aivision:` prefix and strip the prefix.
fn extract_aivision_tags(tags: &[String]) -> Vec<String> {
    tags.iter()
        .filter_map(|tag| tag.strip_prefix(TAG_PREFIX).map(|v| v.to_string()))
        .filter(|v| !v.is_empty())
        .collect()
}

/// Canonical path for the skill tag registry state file.
pub fn state_file_path() -> PathBuf {
    dirs::state_dir().join("skill_tags.json")
}

/// Serialize registry to JSON and write to file.
pub fn save_registry(registry: &SkillTagRegistry, path: &Path) -> Result<(), String> {
    let json = serde_json::to_string(registry).map_err(|e| e.to_string())?;
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    fs::write(path, json).map_err(|e| e.to_string())
}

/// Load registry from JSON file.
pub fn load_registry(path: &Path) -> Option<SkillTagRegistry> {
    let content = fs::read_to_string(path).ok()?;
    serde_json::from_str(&content).ok()
}

/// Look up tags for a skill name. Tries exact match first, then short name.
pub fn lookup_tags<'a>(
    registry: &'a SkillTagRegistry,
    skill_name: &str,
) -> Option<&'a Vec<String>> {
    // Exact match
    if let Some(tags) = registry.get(skill_name) {
        return Some(tags);
    }
    // Try short name: "publisher:plugin:name" → "plugin:name" → "name"
    if let Some(idx) = skill_name.rfind(':') {
        let short = &skill_name[idx + 1..];
        if let Some(tags) = registry.get(short) {
            return Some(tags);
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_extract_frontmatter() {
        let content = "---\nname: test\ntags: [\"aivision:foo\"]\n---\n# Body";
        let fm = extract_frontmatter(content).unwrap();
        assert!(fm.contains("name: test"));
        assert!(fm.contains("tags:"));
    }

    #[test]
    fn test_extract_frontmatter_missing() {
        assert!(extract_frontmatter("# No frontmatter").is_none());
    }

    #[test]
    fn test_extract_field() {
        let yaml = "name: my-skill\ndescription: \"some desc\"";
        assert_eq!(extract_field(yaml, "name"), Some("my-skill".to_string()));
        assert_eq!(
            extract_field(yaml, "description"),
            Some("some desc".to_string())
        );
        assert_eq!(extract_field(yaml, "missing"), None);
    }

    #[test]
    fn test_extract_tags_inline() {
        let yaml = "name: test\ntags: [\"aivision:theta\", \"aivision:beta\", \"other:ignore\"]";
        let tags = extract_tags(yaml);
        assert_eq!(
            tags,
            vec!["aivision:theta", "aivision:beta", "other:ignore"]
        );
    }

    #[test]
    fn test_extract_tags_no_quotes() {
        let yaml = "tags: [aivision:theta, other:x]";
        let tags = extract_tags(yaml);
        assert_eq!(tags, vec!["aivision:theta", "other:x"]);
    }

    #[test]
    fn test_extract_aivision_tags() {
        let tags = vec![
            "aivision:theta".to_string(),
            "aivision:beta".to_string(),
            "other:ignore".to_string(),
            "aivision:".to_string(), // empty after prefix
        ];
        let result = extract_aivision_tags(&tags);
        assert_eq!(result, vec!["theta", "beta"]);
    }

    #[test]
    fn test_lookup_tags_exact() {
        let mut reg = SkillTagRegistry::new();
        reg.insert("my-skill".to_string(), vec!["theta".to_string()]);
        assert_eq!(
            lookup_tags(&reg, "my-skill").unwrap(),
            &vec!["theta".to_string()]
        );
    }

    #[test]
    fn test_lookup_tags_short_name() {
        let mut reg = SkillTagRegistry::new();
        reg.insert("plan".to_string(), vec!["theta".to_string()]);
        // "omc:plan" should match "plan" via short name fallback
        assert_eq!(
            lookup_tags(&reg, "omc:plan").unwrap(),
            &vec!["theta".to_string()]
        );
    }

    #[test]
    fn test_lookup_tags_miss() {
        let reg = SkillTagRegistry::new();
        assert!(lookup_tags(&reg, "unknown").is_none());
    }

    #[test]
    fn test_parse_yaml_inline_array_empty() {
        assert!(parse_yaml_inline_array("[]").is_empty());
        assert!(parse_yaml_inline_array("not-array").is_empty());
    }

    #[test]
    fn test_save_and_load_registry() {
        let mut reg = SkillTagRegistry::new();
        reg.insert(
            "skill-a".to_string(),
            vec!["tag1".to_string(), "tag2".to_string()],
        );

        let tmp = std::env::temp_dir().join("cchooks_test_skill_tags.json");
        save_registry(&reg, &tmp).unwrap();
        let loaded = load_registry(&tmp).unwrap();
        assert_eq!(loaded, reg);
        let _ = fs::remove_file(&tmp);
    }
}
