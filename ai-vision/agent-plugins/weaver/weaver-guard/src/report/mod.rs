//! Report module for validation output
//!
//! Provides terminal (colored text) and JSON output formatters.

use crate::semconv::SemconvFinding;
use crate::validation::ValidationFinding;
use chrono::Utc;
#[cfg(feature = "cli")]
use colored::{Color, Colorize};
use serde::{Deserialize, Serialize};
use std::io::Write;

/// Validation report data structure
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ValidationReport {
    /// Report generation timestamp
    pub timestamp: String,
    /// Per-file results
    pub files: Vec<FileReport>,
    /// Summary statistics
    pub summary: ReportSummary,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FileReport {
    /// Relative or absolute file path
    pub file_path: String,
    /// Signals in this file
    pub signals: Vec<SignalReport>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SignalReport {
    /// Line number in source file
    pub line_number: usize,
    /// Signal type (traces/logs/metrics)
    pub signal_type: String,
    /// Samples in this signal
    pub samples: Vec<SampleReport>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SampleReport {
    /// Sample name (span name, metric name, etc.)
    pub name: String,
    /// Policy validation findings
    pub policy_findings: Vec<PolicyFindingReport>,
    /// Semconv diff findings
    pub semconv_findings: Vec<SemconvFindingReport>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PolicyFindingReport {
    /// Finding ID/type
    pub id: String,
    /// Level: violation/improvement/information
    pub level: String,
    /// Human-readable message
    pub message: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SemconvFindingReport {
    /// Finding type
    pub finding_type: String,
    /// Attribute ID
    pub attribute: String,
    /// Message
    pub message: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct ReportSummary {
    pub total_files: usize,
    pub total_signals: usize,
    pub total_samples: usize,
    pub violations: usize,
    pub improvements: usize,
    pub semconv_issues: usize,
    pub pass: bool,
}

impl ValidationReport {
    pub fn new() -> Self {
        Self {
            timestamp: Utc::now().to_rfc3339(),
            files: Vec::new(),
            summary: ReportSummary::default(),
        }
    }

    pub fn add_sample(
        &mut self,
        file_path: &str,
        line_number: usize,
        signal_type: &str,
        sample_name: &str,
        policy_findings: Vec<ValidationFinding>,
        semconv_findings: Vec<SemconvFinding>,
    ) {
        // Find or create file entry
        let file = self.files.iter_mut().find(|f| f.file_path == file_path);

        if let Some(file) = file {
            // Find or create signal entry
            let signal = file
                .signals
                .iter_mut()
                .find(|s| s.line_number == line_number);

            if let Some(signal) = signal {
                signal.samples.push(SampleReport {
                    name: sample_name.to_string(),
                    policy_findings: policy_findings
                        .into_iter()
                        .map(|f| PolicyFindingReport {
                            id: f.advice_type,
                            level: f.advice_level,
                            message: f.message,
                        })
                        .collect(),
                    semconv_findings: semconv_findings
                        .into_iter()
                        .map(|f| SemconvFindingReport {
                            finding_type: f.finding_type.to_string(),
                            attribute: f.attribute_id,
                            message: f.message,
                        })
                        .collect(),
                });
            } else {
                file.signals.push(SignalReport {
                    line_number,
                    signal_type: signal_type.to_string(),
                    samples: vec![SampleReport {
                        name: sample_name.to_string(),
                        policy_findings: policy_findings
                            .into_iter()
                            .map(|f| PolicyFindingReport {
                                id: f.advice_type,
                                level: f.advice_level,
                                message: f.message,
                            })
                            .collect(),
                        semconv_findings: semconv_findings
                            .into_iter()
                            .map(|f| SemconvFindingReport {
                                finding_type: f.finding_type.to_string(),
                                attribute: f.attribute_id,
                                message: f.message,
                            })
                            .collect(),
                    }],
                });
            }
        } else {
            self.files.push(FileReport {
                file_path: file_path.to_string(),
                signals: vec![SignalReport {
                    line_number,
                    signal_type: signal_type.to_string(),
                    samples: vec![SampleReport {
                        name: sample_name.to_string(),
                        policy_findings: policy_findings
                            .into_iter()
                            .map(|f| PolicyFindingReport {
                                id: f.advice_type,
                                level: f.advice_level,
                                message: f.message,
                            })
                            .collect(),
                        semconv_findings: semconv_findings
                            .into_iter()
                            .map(|f| SemconvFindingReport {
                                finding_type: f.finding_type.to_string(),
                                attribute: f.attribute_id,
                                message: f.message,
                            })
                            .collect(),
                    }],
                }],
            });
        }
    }

    pub fn finalize(&mut self) {
        self.summary.total_files = self.files.len();
        self.summary.total_signals = self.files.iter().map(|f| f.signals.len()).sum();
        self.summary.total_samples = self
            .files
            .iter()
            .map(|f| f.signals.iter().map(|s| s.samples.len()).sum::<usize>())
            .sum();

        for file in &self.files {
            for signal in &file.signals {
                for sample in &signal.samples {
                    for finding in &sample.policy_findings {
                        match finding.level.as_str() {
                            "violation" => self.summary.violations += 1,
                            "improvement" => self.summary.improvements += 1,
                            _ => {}
                        }
                    }
                    self.summary.semconv_issues += sample.semconv_findings.len();
                }
            }
        }

        self.summary.pass = self.summary.violations == 0;
    }
}

impl Default for ValidationReport {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(feature = "cli")]
/// Render report as colored terminal output
pub fn render_text(report: &ValidationReport, writer: &mut impl Write) -> std::io::Result<()> {
    writeln!(
        writer,
        "{}",
        "======================================================================".cyan()
    )?;
    writeln!(
        writer,
        "{}",
        "  AI Agent Telemetry Validation Report".cyan().bold()
    )?;
    writeln!(
        writer,
        "{}",
        "======================================================================".cyan()
    )?;
    writeln!(writer)?;

    // Summary
    writeln!(writer, "  Files: {}", report.summary.total_files)?;
    writeln!(writer, "  Signals: {}", report.summary.total_signals)?;
    writeln!(writer, "  Samples: {}", report.summary.total_samples)?;
    writeln!(
        writer,
        "  {}: {}",
        "Violations".red().bold(),
        report.summary.violations
    )?;
    writeln!(
        writer,
        "  {}: {}",
        "Improvements".yellow(),
        report.summary.improvements
    )?;
    writeln!(
        writer,
        "  {}: {}",
        "Semconv Issues".cyan(),
        report.summary.semconv_issues
    )?;
    writeln!(writer)?;

    // Per-file details
    for file in &report.files {
        writeln!(
            writer,
            "{}",
            "----------------------------------------------------------------------".dimmed()
        )?;
        writeln!(writer, "  FILE: {}", file.file_path.bold())?;
        writeln!(
            writer,
            "{}",
            "----------------------------------------------------------------------".dimmed()
        )?;

        for signal in &file.signals {
            for sample in &signal.samples {
                let sample_header = format!(
                    "  Line {} [{}] \"{}\"",
                    signal.line_number, signal.signal_type, sample.name
                );
                writeln!(writer, "{}", sample_header.bold())?;

                // Policy findings
                for finding in &sample.policy_findings {
                    let (msg, color) = match finding.level.as_str() {
                        "violation" => (
                            format!("  VIOLATION  {}: {}", finding.id, finding.message),
                            Color::Red,
                        ),
                        "improvement" => (
                            format!("  IMPROVEMENT  {}: {}", finding.id, finding.message),
                            Color::Yellow,
                        ),
                        _ => (
                            format!("  INFO  {}: {}", finding.id, finding.message),
                            Color::Blue,
                        ),
                    };
                    writeln!(writer, "{}", msg.color(color))?;
                }

                // Semconv findings
                for finding in &sample.semconv_findings {
                    let msg = format!("  SEMCONV  {}: {}", finding.finding_type, finding.message);
                    writeln!(writer, "{}", msg.cyan())?;
                }

                // Skip empty samples
                if sample.policy_findings.is_empty() && sample.semconv_findings.is_empty() {
                    writeln!(writer, "{}", "    (no issues)".green())?;
                }

                writeln!(writer)?;
            }
        }
    }

    // Final result
    writeln!(
        writer,
        "{}",
        "======================================================================".cyan()
    )?;
    let result_msg = if report.summary.violations > 0 {
        format!(
            "  RESULT: {} VIOLATIONS, {} IMPROVEMENTS, {} SEMCONV ISSUES",
            report.summary.violations, report.summary.improvements, report.summary.semconv_issues
        )
    } else if report.summary.improvements > 0 {
        format!(
            "  RESULT: {} IMPROVEMENTS, {} SEMCONV ISSUES (PASS)",
            report.summary.improvements, report.summary.semconv_issues
        )
    } else {
        "  RESULT: PASS".green().bold().to_string()
    };

    if report.summary.violations > 0 {
        writeln!(writer, "{}", result_msg.red().bold())?;
    } else if report.summary.improvements > 0 {
        writeln!(writer, "{}", result_msg.yellow())?;
    } else {
        writeln!(writer, "{}", result_msg.green())?;
    }
    writeln!(
        writer,
        "{}",
        "======================================================================".cyan()
    )?;

    Ok(())
}

/// Render report as JSON
pub fn render_json(report: &ValidationReport, writer: &mut impl Write) -> std::io::Result<()> {
    let json = serde_json::to_string_pretty(report)
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::Other, e))?;
    writeln!(writer, "{}", json)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_validation_report_new() {
        let report = ValidationReport::new();
        assert_eq!(report.files.len(), 0);
        assert_eq!(report.summary.total_files, 0);
    }

    #[test]
    fn test_validation_report_add_sample() {
        let mut report = ValidationReport::new();
        report.add_sample(
            "test.jsonl",
            1,
            "traces",
            "ai_agent.turn",
            vec![ValidationFinding {
                advice_type: "missing_session_id".to_string(),
                advice_level: "violation".to_string(),
                message: "Missing required attribute".to_string(),
                context: serde_json::Value::Null,
            }],
            vec![],
        );
        assert_eq!(report.files.len(), 1);
        assert_eq!(report.files[0].signals.len(), 1);
    }

    #[test]
    fn test_validation_report_finalize() {
        let mut report = ValidationReport::new();
        report.add_sample("test.jsonl", 1, "traces", "test-span", vec![], vec![]);
        report.add_sample("test.jsonl", 2, "traces", "test-span2", vec![], vec![]);
        report.add_sample("test2.jsonl", 1, "traces", "test-span", vec![], vec![]);
        report.finalize();

        assert_eq!(report.summary.total_files, 2);
        assert_eq!(report.summary.total_signals, 3);
        assert_eq!(report.summary.total_samples, 3);
    }
}
