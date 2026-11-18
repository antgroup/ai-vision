//! Comparison engine for golden replay

use crate::compare::config::CompareConfig;
use crate::compare::diff::{compare_signals, DiffResult, DiffType};
use crate::jsonl::{self, OtelSignal};
use std::path::{Path, PathBuf};

/// Result of comparing a golden file against actual output
#[derive(Debug, Clone)]
pub struct CompareReport {
    /// Path to the golden file
    pub golden_path: PathBuf,
    /// Path to the actual replay file
    pub actual_path: PathBuf,
    /// List of differences found
    pub diffs: Vec<DiffResult>,
    /// Whether the comparison passed (no meaningful differences)
    pub passed: bool,
}

impl CompareReport {
    pub fn new(golden_path: PathBuf, actual_path: PathBuf) -> Self {
        Self {
            golden_path,
            actual_path,
            diffs: Vec::new(),
            passed: false,
        }
    }

    /// Count differences by type
    pub fn summary(&self) -> CompareSummary {
        let mut summary = CompareSummary::default();
        for diff in &self.diffs {
            match diff.diff_type {
                DiffType::Removed => summary.removed += 1,
                DiffType::Added => summary.added += 1,
                DiffType::Modified => summary.modified += 1,
                DiffType::Ignored => summary.ignored += 1,
                DiffType::Unchanged => summary.unchanged += 1,
            }
        }
        summary
    }
}

/// Summary statistics for a comparison
#[derive(Debug, Clone, Default)]
pub struct CompareSummary {
    pub removed: usize,
    pub added: usize,
    pub modified: usize,
    pub ignored: usize,
    pub unchanged: usize,
}

impl CompareSummary {
    pub fn total_changes(&self) -> usize {
        self.removed + self.added + self.modified
    }

    pub fn has_changes(&self) -> bool {
        self.total_changes() > 0
    }
}

/// Engine for comparing OTEL signals against golden replays
pub struct CompareEngine {
    config: CompareConfig,
}

impl CompareEngine {
    /// Create a new compare engine with the given config
    pub fn new(config: CompareConfig) -> Self {
        Self { config }
    }

    /// Create engine with default config
    pub fn with_defaults() -> Self {
        Self {
            config: CompareConfig::default_config(),
        }
    }

    /// Load config from file, or use defaults if not found
    pub fn from_config_file(path: Option<&Path>) -> Self {
        let config = if let Some(p) = path {
            CompareConfig::from_file(&p.to_path_buf())
                .unwrap_or_else(|_| CompareConfig::default_config())
        } else {
            CompareConfig::default_config()
        };
        Self { config }
    }

    /// Compare a golden file against an actual replay file
    pub fn compare_files(
        &self,
        golden_path: &Path,
        actual_path: &Path,
    ) -> anyhow::Result<CompareReport> {
        let golden_signals = jsonl::read_jsonl_file(golden_path)?;
        let actual_signals = jsonl::read_jsonl_file(actual_path)?;

        let mut report = CompareReport::new(golden_path.to_path_buf(), actual_path.to_path_buf());

        // Compare each signal line by line
        let max_lines = golden_signals.len().max(actual_signals.len());
        for i in 0..max_lines {
            let golden = golden_signals.get(i);
            let actual = actual_signals.get(i);

            match (golden, actual) {
                (Some(g), Some(a)) => {
                    let diffs = self.compare_signals(g, a);
                    report.diffs.extend(diffs);
                }
                (Some(g), None) => {
                    // Golden has more lines
                    let samples = jsonl::extract_samples(g);
                    for sample in samples {
                        report.diffs.push(DiffResult::new(
                            format!("line[{}]", i + 1),
                            Some(sample.to_string()),
                            None,
                            DiffType::Removed,
                        ));
                    }
                }
                (None, Some(a)) => {
                    // Actual has more lines
                    let samples = jsonl::extract_samples(a);
                    for sample in samples {
                        report.diffs.push(DiffResult::new(
                            format!("line[{}]", i + 1),
                            None,
                            Some(sample.to_string()),
                            DiffType::Added,
                        ));
                    }
                }
                (None, None) => {}
            }
        }

        report.passed = !report.summary().has_changes();
        Ok(report)
    }

    /// Compare two OTEL signals
    pub fn compare_signals(&self, golden: &OtelSignal, actual: &OtelSignal) -> Vec<DiffResult> {
        let ignore_fields = self.config.ignore.all_patterns();
        let exact_fields = self.config.exact.fields.clone();
        let tolerance_map = self.config.tolerance.clone();

        // Compare at the signal level (whole JSON line)
        compare_signals(
            &golden.data,
            &actual.data,
            &ignore_fields,
            &exact_fields,
            &tolerance_map,
        )
    }

    /// Compare a directory of golden files against a directory of actual files
    pub fn compare_dirs(
        &self,
        golden_dir: &Path,
        actual_dir: &Path,
    ) -> anyhow::Result<Vec<CompareReport>> {
        let mut reports = Vec::new();

        // Find all golden files
        let golden_files = self::find_jsonl_files(golden_dir)?;
        for golden_path in golden_files {
            // Derive actual file path
            let relative = golden_path.strip_prefix(golden_dir).unwrap();
            let actual_path = actual_dir.join(relative);

            if actual_path.exists() {
                match self.compare_files(&golden_path, &actual_path) {
                    Ok(report) => reports.push(report),
                    Err(e) => {
                        tracing::warn!(error = %e, golden = %golden_path.display(), "Failed to compare files");
                    }
                }
            } else {
                tracing::warn!(actual = %actual_path.display(), "Actual file not found");
            }
        }

        Ok(reports)
    }

    /// Get the configuration
    pub fn config(&self) -> &CompareConfig {
        &self.config
    }
}

/// Find all JSONL files in a directory recursively
fn find_jsonl_files(dir: &Path) -> anyhow::Result<Vec<PathBuf>> {
    let mut files = Vec::new();
    for entry in walkdir::WalkDir::new(dir).follow_links(true) {
        let entry = entry?;
        let path = entry.path();
        if path.is_file() && path.extension().and_then(|e| e.to_str()) == Some("jsonl") {
            files.push(path.to_path_buf());
        }
    }
    files.sort();
    Ok(files)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_compare_engine_default_config() {
        let engine = CompareEngine::with_defaults();
        let config = engine.config();

        // Should have default ignored fields
        assert!(!config.ignore.timestamp_fields.is_empty());
        assert!(!config.tolerance.is_empty());
    }

    #[test]
    fn test_compare_summary() {
        let summary = CompareSummary {
            removed: 2,
            added: 1,
            modified: 3,
            ignored: 5,
            unchanged: 10,
        };

        assert_eq!(summary.total_changes(), 6);
        assert!(summary.has_changes());
    }
}
