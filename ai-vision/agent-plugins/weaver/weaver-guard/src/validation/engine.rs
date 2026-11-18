//! Validation engine wrapper around weaver_checker
//!
//! Provides policy evaluation using the weaver_checker Engine.

use anyhow::Result;
use std::path::Path;
use tracing::{debug, info};
use weaver_checker::{Engine, PolicyFinding, PolicyStage};

/// Wrapper around weaver_checker Engine for policy evaluation
pub struct ValidationEngine {
    engine: Engine,
}

impl ValidationEngine {
    /// Create a new validation engine loading policies from directory (recursively)
    pub fn new(policies_dir: &Path) -> Result<Self> {
        let mut engine = Engine::new();
        info!(
            policies_dir = %policies_dir.display(),
            "Loading validation policies"
        );
        Self::load_policies_recursive(&mut engine, policies_dir)?;
        Ok(Self { engine })
    }

    /// Recursively load .rego policies from a directory tree
    fn load_policies_recursive(engine: &mut Engine, dir: &Path) -> Result<()> {
        if !dir.is_dir() {
            return engine.add_policy_from_file_or_dir(dir).map_err(Into::into);
        }
        // Load .rego files in this directory
        let mut has_rego = false;
        for entry in std::fs::read_dir(dir)? {
            let entry = entry?;
            if entry.path().extension().is_some_and(|ext| ext == "rego") {
                has_rego = true;
            }
        }
        if has_rego {
            debug!(dir = %dir.display(), "Loading policies from directory");
            engine.add_policy_from_file_or_dir(dir)?;
        }
        // Recurse into subdirectories
        for entry in std::fs::read_dir(dir)? {
            let entry = entry?;
            let path = entry.path();
            if path.is_dir()
                && !path
                    .file_name()
                    .is_some_and(|n| n.to_string_lossy().starts_with('_'))
            {
                Self::load_policies_recursive(engine, &path)?;
            }
        }
        Ok(())
    }

    /// Validate a sample and return findings
    pub fn validate(&mut self, sample: &serde_json::Value) -> Result<Vec<ValidationFinding>> {
        self.engine.set_input(sample)?;
        let findings = self.engine.check(PolicyStage::LiveCheckAdvice)?;
        Ok(findings.into_iter().map(ValidationFinding::from).collect())
    }
}

/// A validation finding from policy evaluation
#[derive(Debug, Clone)]
pub struct ValidationFinding {
    /// Type of finding (e.g., "missing_session_id")
    pub advice_type: String,
    /// Level of finding (violation | improvement | information)
    pub advice_level: String,
    /// Human-readable message
    pub message: String,
    /// Context information about the finding
    pub context: serde_json::Value,
}

impl From<PolicyFinding> for ValidationFinding {
    fn from(finding: PolicyFinding) -> Self {
        Self {
            advice_type: finding.id,
            advice_level: finding.level.to_string(),
            message: finding.message,
            context: finding.context.unwrap_or(serde_json::Value::Null),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    fn get_policies_dir() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .unwrap()
            .join("policies")
    }

    #[test]
    fn test_validation_engine_new() {
        let policies_dir = get_policies_dir();
        if !policies_dir.exists() {
            eprintln!(
                "Skipping test: policies dir not found at {:?}",
                policies_dir
            );
            return;
        }
        let engine = ValidationEngine::new(&policies_dir);
        assert!(
            engine.is_ok(),
            "Failed to create engine: {:?}",
            engine.err()
        );
    }

    #[test]
    fn test_validate_missing_required_aivision_resource_identity() {
        let policies_dir = get_policies_dir();
        if !policies_dir.exists() {
            eprintln!(
                "Skipping test: policies dir not found at {:?}",
                policies_dir
            );
            return;
        }
        let mut engine = ValidationEngine::new(&policies_dir).unwrap();

        let sample = serde_json::json!({
            "sample": {
                "span": {
                    "name": "ai_agent.turn",
                    "attributes": []
                },
                "resource": {
                    "attributes": []
                }
            }
        });

        let findings = engine.validate(&sample).unwrap();

        let missing_agent_type = findings.iter().any(|f| {
            f.advice_type == "resource_missing_agent_type" && f.advice_level == "violation"
        });
        assert!(
            missing_agent_type,
            "Expected resource_missing_agent_type violation, got: {:?}",
            findings
        );
    }

    #[test]
    fn test_validate_valid_span() {
        let policies_dir = get_policies_dir();
        if !policies_dir.exists() {
            eprintln!(
                "Skipping test: policies dir not found at {:?}",
                policies_dir
            );
            return;
        }
        let mut engine = ValidationEngine::new(&policies_dir).unwrap();

        let sample = serde_json::json!({
            "sample": {
                "span": {
                    "name": "custom span",
                    "attributes": [
                        {"key": "user.id", "value": {"stringValue": "user-1"}},
                        {"key": "ant.username", "value": {"stringValue": "test-user"}},
                        {"key": "gen_ai.conversation.id", "value": {"stringValue": "conversation-1"}},
                        {"key": "gen_ai.chat.id", "value": {"stringValue": "chat-1"}},
                        {"key": "gen_ai.operation.name", "value": {"stringValue": "chat"}},
                        {"key": "gen_ai.span.kind", "value": {"stringValue": "COMMON"}}
                    ]
                },
                "resource": {
                    "attributes": [
                        {"key": "ant.agent.type", "value": {"stringValue": "opencode"}},
                        {"key": "service.name", "value": {"stringValue": "observ-opencode"}},
                        {"key": "ant.agent.env", "value": {"stringValue": "test"}},
                        {"key": "ant.agent.scene", "value": {"stringValue": "default"}},
                        {"key": "agentic.collection.mode", "value": {"stringValue": "full"}}
                    ]
                }
            }
        });

        let findings = engine.validate(&sample).unwrap();

        let violations: Vec<_> = findings
            .iter()
            .filter(|f| f.advice_level == "violation")
            .collect();
        assert!(
            violations.is_empty(),
            "Unexpected violations: {:?}",
            violations
        );
    }
}
