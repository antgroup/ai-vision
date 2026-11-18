//! Configuration type definitions for weaver-guard

use std::path::PathBuf;

/// CLI-resolved configuration for a validation run
#[derive(Debug, Clone)]
pub struct Config {
    /// Path(s) to JSONL input (file or directory)
    pub input_path: PathBuf,
    /// Path to Rego policies directory
    pub policies_dir: Option<PathBuf>,
    /// Paths to semconv YAML registry directories (merged in order)
    pub semconv_dirs: Vec<PathBuf>,
    /// Output format
    pub format: OutputFormat,
    /// What severity triggers non-zero exit
    pub fail_on: FailOn,
}

/// Output format for validation report
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[cfg_attr(feature = "cli", derive(clap::ValueEnum))]
pub enum OutputFormat {
    /// Colored terminal text output
    Text,
    /// Structured JSON output
    Json,
}

/// What severity level triggers non-zero exit code
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[cfg_attr(feature = "cli", derive(clap::ValueEnum))]
pub enum FailOn {
    /// Fail only on violations
    Violation,
    /// Fail on violations or improvements
    Improvement,
    /// Fail on any finding
    Any,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_output_format_values() {
        assert_eq!(OutputFormat::Text, OutputFormat::Text);
        assert_ne!(OutputFormat::Text, OutputFormat::Json);
    }

    #[test]
    fn test_fail_on_values() {
        assert_eq!(FailOn::Violation, FailOn::Violation);
        assert_ne!(FailOn::Violation, FailOn::Any);
    }
}
