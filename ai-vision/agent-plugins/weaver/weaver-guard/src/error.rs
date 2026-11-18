//! Error types for weaver-guard

use thiserror::Error;

/// Main error type for weaver-guard operations
#[derive(Debug, Error)]
pub enum WeaverGuardError {
    /// Configuration related errors
    #[error("Configuration error: {0}")]
    Config(String),

    /// I/O related errors
    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),

    /// JSONL parsing errors
    #[error("JSONL parsing error: {0}")]
    Jsonl(String),

    /// Validation engine errors
    #[error("Validation error: {0}")]
    Validation(String),

    /// Semconv loading/parsing errors
    #[error("Semconv error: {0}")]
    Semconv(String),

    /// Report formatting errors
    #[error("Report error: {0}")]
    Report(String),

    /// YAML parsing errors
    #[error("YAML parsing error: {0}")]
    Yaml(String),

    /// Environment variable expansion errors
    #[error("Environment variable error: {0}")]
    EnvVar(String),

    /// General errors (anyhow)
    #[error("Error: {0}")]
    Anyhow(#[from] anyhow::Error),
}

// Implement From for shellexpand errors
impl From<shellexpand::LookupError<std::env::VarError>> for WeaverGuardError {
    fn from(err: shellexpand::LookupError<std::env::VarError>) -> Self {
        WeaverGuardError::EnvVar(err.to_string())
    }
}

// Implement From for serde_yml errors
impl From<serde_yml::Error> for WeaverGuardError {
    fn from(err: serde_yml::Error) -> Self {
        WeaverGuardError::Yaml(err.to_string())
    }
}
