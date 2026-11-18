//! DEPRECATED: This module is no longer used.
//! Skill names are now extracted directly from Skill tool_use input.
//! This module is kept for backward compatibility only.

use serde::Deserialize;

/// Configuration for keyword-based skill recognition.
/// DEPRECATED: No longer used.
#[derive(Debug, Clone, Deserialize)]
#[allow(dead_code)]
pub struct SkillKeywordConfig {
    pub skills: Vec<SkillKeywordEntry>,
}

/// A single skill entry with its name and trigger keywords.
/// DEPRECATED: No longer used.
#[derive(Debug, Clone, Deserialize)]
#[allow(dead_code)]
pub struct SkillKeywordEntry {
    pub name: String,
    pub keywords: Vec<String>,
}

/// Load skill keyword configuration.
/// DEPRECATED: Always returns empty config.
#[allow(dead_code)]
pub fn load_skill_keywords() -> SkillKeywordConfig {
    SkillKeywordConfig { skills: vec![] }
}

/// Match user text against keyword config.
/// DEPRECATED: Always returns None.
#[allow(dead_code)]
pub fn match_skill_by_keywords(_user_text: &str, _config: &SkillKeywordConfig) -> Option<String> {
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_load_skill_keywords_returns_empty() {
        let config = load_skill_keywords();
        assert!(config.skills.is_empty());
    }

    #[test]
    fn test_match_returns_none() {
        let config = load_skill_keywords();
        assert_eq!(match_skill_by_keywords("test", &config), None);
    }
}
