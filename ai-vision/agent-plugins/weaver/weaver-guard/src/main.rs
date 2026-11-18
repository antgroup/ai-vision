//! Weaver Guard - CLI validator for AI Agent OTLP telemetry
//!
//! Validates JSONL telemetry files against Rego policies and
//! semantic convention YAML registries.

use anyhow::anyhow;
use clap::Parser;
use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use tracing::info;
use tracing_subscriber::EnvFilter;
use weaver_guard::{
    compare::{self, CompareEngine},
    config::{FailOn, OutputFormat},
    distribution::{self, ManagedPaths},
    error::WeaverGuardError,
    jsonl as jsonl_mod,
    report::{self, ValidationReport},
    semconv,
    server::{self, ForwardPolicy, ServeConfig},
    validation::ValidationEngine,
};

#[derive(Parser)]
#[command(name = "weaver-guard")]
#[command(about = "Validate AI agent OTLP telemetry against policies and semantic conventions")]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Parser)]
enum Commands {
    /// Validate JSONL telemetry files
    Validate {
        /// Path to .jsonl file or directory (recursively scans for .jsonl files)
        path: PathBuf,

        /// Path to Rego policies directory
        #[arg(long)]
        policies_dir: Option<PathBuf>,

        /// Path to semconv YAML registry directory (can be specified multiple times)
        #[arg(long)]
        semconv_dir: Vec<PathBuf>,

        /// Output format (text or json)
        #[arg(long, default_value = "text")]
        format: OutputFormat,

        /// Exit code control: what severity triggers failure
        #[arg(long, default_value = "violation")]
        fail_on: FailOn,

        /// Stop after finding N violations (0 = no limit)
        #[arg(short = 'n', long = "max-violations", default_value = "0")]
        max_violations: usize,
    },

    /// Compare actual OTLP output against golden replay
    Compare {
        /// Path to golden replay file or directory
        golden: PathBuf,

        /// Path to actual replay file or directory
        #[arg(long)]
        replay: PathBuf,

        /// Path to config file (default: built-in defaults)
        #[arg(long)]
        config: Option<PathBuf>,

        /// Output diff only, skip TUI
        #[arg(long)]
        diff_only: bool,

        /// Output format (text or json)
        #[arg(long, default_value = "text")]
        format: OutputFormat,
    },

    /// Run local OTLP HTTP validation proxy and dashboard
    Serve {
        /// Local listen address for AiVision / OTLP HTTP
        #[arg(long, default_value = "127.0.0.1:4319")]
        listen: SocketAddr,

        /// Path to Rego policies directory
        #[arg(long)]
        policies_dir: Option<PathBuf>,

        /// Path to semconv YAML registry directory (can be specified multiple times)
        #[arg(long)]
        semconv_dir: Vec<PathBuf>,

        /// Local Victoria binaries/data home
        #[arg(long)]
        victoria_home: Option<PathBuf>,

        /// macOS arm64 Victoria bootstrap script
        #[arg(long)]
        victoria_script: Option<PathBuf>,

        /// VictoriaMetrics listen address
        #[arg(long, default_value = "127.0.0.1:8428")]
        victoria_metrics_addr: String,

        /// VictoriaLogs listen address
        #[arg(long, default_value = "127.0.0.1:9428")]
        victoria_logs_addr: String,

        /// Override VictoriaMetrics UI URL
        #[arg(long)]
        victoria_metrics_ui: Option<String>,

        /// Override VictoriaLogs UI URL
        #[arg(long)]
        victoria_logs_ui: Option<String>,

        /// Local Jaeger binaries/data home
        #[arg(long)]
        jaeger_home: Option<PathBuf>,

        /// macOS arm64 Jaeger bootstrap script
        #[arg(long)]
        jaeger_script: Option<PathBuf>,

        /// Jaeger UI listen address
        #[arg(long, default_value = "127.0.0.1:16686")]
        jaeger_ui_addr: String,

        /// Jaeger OTLP HTTP listen address
        #[arg(long, default_value = "127.0.0.1:14318")]
        jaeger_otlp_http_addr: String,

        /// Jaeger OTLP gRPC listen address
        #[arg(long, default_value = "127.0.0.1:14317")]
        jaeger_otlp_grpc_addr: String,

        /// Override Jaeger UI URL
        #[arg(long)]
        jaeger_ui: Option<String>,

        /// Append live diagnostic JSONL to this file
        #[arg(long)]
        diagnostic_log: Option<PathBuf>,

        /// Write received OTLP JSONL replay files to this directory
        #[arg(long)]
        replay_dir: Option<PathBuf>,

        /// Forwarding policy after validation
        #[arg(long, value_enum, default_value = "always")]
        forward_policy: ForwardPolicy,

        /// Validation severity used as rejection threshold
        #[arg(long, value_enum, default_value = "violation")]
        fail_on: FailOn,

        /// Maximum accepted request body bytes
        #[arg(long, default_value_t = 16 * 1024 * 1024)]
        max_body_bytes: usize,

        /// Default API page size; diagnostics are retained until clear/restart
        #[arg(long, default_value_t = 500)]
        recent_limit: usize,
    },
}

#[tokio::main]
async fn main() -> Result<(), WeaverGuardError> {
    // Initialize tracing
    tracing_subscriber::fmt()
        .with_writer(std::io::stderr)
        .with_env_filter(
            EnvFilter::from_default_env().add_directive("weaver_guard=debug".parse().unwrap()),
        )
        .init();

    // Parse CLI
    let cli = Cli::parse();

    match cli.command {
        Commands::Validate {
            path,
            policies_dir,
            semconv_dir,
            format,
            fail_on,
            max_violations,
        } => run_validate(
            path,
            policies_dir,
            semconv_dir,
            format,
            fail_on,
            max_violations,
        ),

        Commands::Compare {
            golden,
            replay,
            config,
            diff_only,
            format,
        } => run_compare(golden, replay, config, diff_only, format),
        Commands::Serve {
            listen,
            policies_dir,
            semconv_dir,
            victoria_home,
            victoria_script,
            victoria_metrics_addr,
            victoria_logs_addr,
            victoria_metrics_ui,
            victoria_logs_ui,
            jaeger_home,
            jaeger_script,
            jaeger_ui_addr,
            jaeger_otlp_http_addr,
            jaeger_otlp_grpc_addr,
            jaeger_ui,
            diagnostic_log,
            replay_dir,
            forward_policy,
            fail_on,
            max_body_bytes,
            recent_limit,
        } => {
            let paths = resolve_serve_paths(
                policies_dir,
                semconv_dir,
                victoria_home,
                victoria_script,
                jaeger_home,
                jaeger_script,
                diagnostic_log,
                replay_dir,
            )
            .await?;
            server::run(ServeConfig {
                listen,
                policies_dir: Some(paths.policies_dir),
                semconv_dirs: paths.semconv_dirs,
                victoria_home: paths.victoria_home,
                victoria_download_dir: paths.victoria_download_dir,
                victoria_script: paths.victoria_script,
                victoria_metrics_addr,
                victoria_logs_addr,
                victoria_metrics_ui,
                victoria_logs_ui,
                jaeger_home: paths.jaeger_home,
                jaeger_download_dir: paths.jaeger_download_dir,
                jaeger_script: paths.jaeger_script,
                jaeger_ui_addr,
                jaeger_otlp_http_addr,
                jaeger_otlp_grpc_addr,
                jaeger_ui,
                diagnostic_log: Some(paths.diagnostic_log),
                replay_dir: Some(paths.replay_dir),
                forward_policy,
                fail_on,
                max_body_bytes,
                recent_limit,
            })
            .await
        }
    }
}

struct ResolvedServePaths {
    policies_dir: PathBuf,
    semconv_dirs: Vec<PathBuf>,
    victoria_home: PathBuf,
    victoria_download_dir: Option<PathBuf>,
    victoria_script: PathBuf,
    jaeger_home: PathBuf,
    jaeger_download_dir: Option<PathBuf>,
    jaeger_script: PathBuf,
    diagnostic_log: PathBuf,
    replay_dir: PathBuf,
}

#[derive(Debug, Clone)]
struct DevelopmentServePaths {
    policies_dir: PathBuf,
    semconv_dir: PathBuf,
    victoria_home: PathBuf,
    victoria_download_dir: PathBuf,
    victoria_script: PathBuf,
    jaeger_home: PathBuf,
    jaeger_download_dir: PathBuf,
    jaeger_script: PathBuf,
    diagnostic_log: PathBuf,
    replay_dir: PathBuf,
}

async fn resolve_serve_paths(
    policies_dir: Option<PathBuf>,
    semconv_dirs: Vec<PathBuf>,
    victoria_home: Option<PathBuf>,
    victoria_script: Option<PathBuf>,
    jaeger_home: Option<PathBuf>,
    jaeger_script: Option<PathBuf>,
    diagnostic_log: Option<PathBuf>,
    replay_dir: Option<PathBuf>,
) -> Result<ResolvedServePaths, WeaverGuardError> {
    let needs_managed = policies_dir.is_none()
        || semconv_dirs.is_empty()
        || victoria_home.is_none()
        || victoria_script.is_none()
        || jaeger_home.is_none()
        || jaeger_script.is_none()
        || diagnostic_log.is_none()
        || replay_dir.is_none();
    let development = if needs_managed && cfg!(debug_assertions) {
        development_serve_paths_from_current_dir()
    } else {
        None
    };
    let managed = if needs_managed && development.is_none() {
        Some(ManagedPaths::from_env()?)
    } else {
        None
    };

    let needs_runtime_assets = policies_dir.is_none()
        || semconv_dirs.is_empty()
        || victoria_script.is_none()
        || jaeger_script.is_none();
    if needs_runtime_assets && development.is_none() {
        let managed = managed.as_ref().ok_or_else(|| {
            WeaverGuardError::Config(
                "standalone runtime assets requested without managed paths".to_string(),
            )
        })?;
        distribution::prepare_runtime_assets(managed, env!("CARGO_PKG_VERSION")).await?;
    }

    let managed = managed.as_ref();
    let development = development.as_ref();
    Ok(ResolvedServePaths {
        policies_dir: default_serve_path(
            policies_dir,
            development,
            managed,
            "policies",
            |paths| paths.policies_dir.clone(),
            |paths| paths.policies_dir.clone(),
        )?,
        semconv_dirs: if semconv_dirs.is_empty() {
            vec![default_serve_path(
                None,
                development,
                managed,
                "semconv",
                |paths| paths.semconv_dir.clone(),
                |paths| paths.semconv_dir.clone(),
            )?]
        } else {
            semconv_dirs
        },
        victoria_home: default_serve_path(
            victoria_home,
            development,
            managed,
            "Victoria home",
            |paths| paths.victoria_home.clone(),
            |paths| paths.victoria_home.clone(),
        )?,
        victoria_download_dir: development
            .map(|paths| paths.victoria_download_dir.clone())
            .or_else(|| managed.map(|paths| paths.victoria_download_dir.clone())),
        victoria_script: default_serve_path(
            victoria_script,
            development,
            managed,
            "Victoria script",
            |paths| paths.victoria_script.clone(),
            |paths| paths.victoria_script.clone(),
        )?,
        jaeger_home: default_serve_path(
            jaeger_home,
            development,
            managed,
            "Jaeger home",
            |paths| paths.jaeger_home.clone(),
            |paths| paths.jaeger_home.clone(),
        )?,
        jaeger_download_dir: development
            .map(|paths| paths.jaeger_download_dir.clone())
            .or_else(|| managed.map(|paths| paths.jaeger_download_dir.clone())),
        jaeger_script: default_serve_path(
            jaeger_script,
            development,
            managed,
            "Jaeger script",
            |paths| paths.jaeger_script.clone(),
            |paths| paths.jaeger_script.clone(),
        )?,
        diagnostic_log: default_serve_path(
            diagnostic_log,
            development,
            managed,
            "diagnostic log",
            |paths| paths.diagnostic_log.clone(),
            |paths| paths.diagnostic_log.clone(),
        )?,
        replay_dir: default_serve_path(
            replay_dir,
            development,
            managed,
            "replay directory",
            |paths| paths.replay_dir.clone(),
            |paths| paths.replay_dir.clone(),
        )?,
    })
}

fn default_serve_path(
    explicit: Option<PathBuf>,
    development: Option<&DevelopmentServePaths>,
    managed: Option<&ManagedPaths>,
    label: &str,
    development_default: impl FnOnce(&DevelopmentServePaths) -> PathBuf,
    managed_default: impl FnOnce(&ManagedPaths) -> PathBuf,
) -> Result<PathBuf, WeaverGuardError> {
    if let Some(path) = explicit {
        return Ok(path);
    }
    if let Some(paths) = development {
        return Ok(development_default(paths));
    }
    if let Some(paths) = managed {
        return Ok(managed_default(paths));
    }
    Err(WeaverGuardError::Config(format!(
        "missing {label}; pass it explicitly or allow standalone managed paths"
    )))
}

fn development_serve_paths_from_current_dir() -> Option<DevelopmentServePaths> {
    let cwd = std::env::current_dir().ok()?;
    development_serve_paths_from(&cwd)
}

fn development_serve_paths_from(start: &Path) -> Option<DevelopmentServePaths> {
    for dir in start.ancestors() {
        if let Some(paths) = development_serve_paths_for_weaver_dir(dir) {
            return Some(paths);
        }
        if let Some(paths) = development_serve_paths_for_weaver_dir(&dir.join("weaver")) {
            return Some(paths);
        }
    }
    None
}

fn development_serve_paths_for_weaver_dir(weaver_dir: &Path) -> Option<DevelopmentServePaths> {
    let semconv_dir = weaver_dir.parent()?.join("docs").join("semconv");
    let policies_dir = weaver_dir.join("policies");
    let scripts_dir = weaver_dir.join("scripts");
    let victoria_script = scripts_dir.join("victoria-macos-arm64.sh");
    let jaeger_script = scripts_dir.join("jaeger-macos-arm64.sh");
    if !policies_dir.is_dir()
        || !semconv_dir.is_dir()
        || !victoria_script.is_file()
        || !jaeger_script.is_file()
    {
        return None;
    }

    Some(DevelopmentServePaths {
        policies_dir,
        semconv_dir,
        victoria_home: weaver_dir.join(".victoria"),
        victoria_download_dir: weaver_dir.join(".downloads").join("victoria"),
        victoria_script,
        jaeger_home: weaver_dir.join(".jaeger"),
        jaeger_download_dir: weaver_dir.join(".downloads").join("jaeger"),
        jaeger_script,
        diagnostic_log: weaver_dir.join("live").join("diagnostics.jsonl"),
        replay_dir: weaver_dir.join("live").join("replays"),
    })
}

fn run_validate(
    path: PathBuf,
    policies_dir: Option<PathBuf>,
    semconv_dirs: Vec<PathBuf>,
    format: OutputFormat,
    fail_on: FailOn,
    max_violations: usize,
) -> Result<(), WeaverGuardError> {
    info!(path = %path.display(), "Starting validation");

    // 1. Load JSONL signals
    let signals = if path.is_dir() {
        jsonl_mod::read_jsonl_dir(&path)?
    } else {
        jsonl_mod::read_jsonl_file(&path)?
    };

    if signals.is_empty() {
        eprintln!("No signals found in {:?}", path);
        return Ok(());
    }

    info!(signals = signals.len(), "Loaded signals");

    // 2. Initialize validation engine (if policies provided)
    let mut engine = if let Some(ref dir) = policies_dir {
        let expanded = shellexpand::tilde(&dir.to_string_lossy()).into_owned();
        Some(ValidationEngine::new(Path::new(&expanded))?)
    } else {
        None
    };

    if engine.is_some() {
        info!("Validation engine initialized");
    }

    // 3. Load semconv registry (merge all --semconv-dir directories)
    let registry = if !semconv_dirs.is_empty() {
        let dirs: Vec<PathBuf> = semconv_dirs
            .iter()
            .map(|d| PathBuf::from(shellexpand::tilde(&d.to_string_lossy()).into_owned()))
            .collect();
        Some(semconv::load_semconv_registry_from_dirs(&dirs)?)
    } else {
        None
    };

    if registry.is_some() {
        info!("Semconv registry loaded");
    }

    // 4. Validate each signal/sample, building report
    let mut report = ValidationReport::new();
    let mut violation_count: usize = 0;
    let limit = max_violations;

    'outer: for signal in &signals {
        let samples = jsonl_mod::extract_samples(signal);
        let file_path = signal
            .source_file
            .as_ref()
            .map(|p| p.to_string_lossy().to_string())
            .unwrap_or_else(|| "<unknown>".to_string());

        for sample in &samples {
            let sample_name = jsonl_mod::sample_name(sample);

            // 4a. Rego policy validation
            let policy_findings = if let Some(ref mut eng) = engine {
                match eng.validate(sample) {
                    Ok(findings) => findings,
                    Err(e) => {
                        tracing::error!(error = %e, "Validation error");
                        vec![]
                    }
                }
            } else {
                vec![]
            };

            // 4b. Semconv attribute diff
            let semconv_findings = if let Some(ref reg) = registry {
                semconv::diff_attributes(reg, signal, sample)
            } else {
                vec![]
            };

            // Count violations in this sample
            violation_count += policy_findings
                .iter()
                .filter(|f| f.advice_level == "violation")
                .count();

            report.add_sample(
                &file_path,
                signal.line_number,
                &signal.signal_type.to_string(),
                &sample_name,
                policy_findings,
                semconv_findings,
            );

            // Early exit if limit reached
            if limit > 0 && violation_count >= limit {
                info!(
                    violations = violation_count,
                    limit, "Violation limit reached, stopping scan"
                );
                break 'outer;
            }
        }
    }

    report.finalize();

    // 5. Output report
    let stdout = std::io::stdout();
    let mut writer = stdout.lock();
    match format {
        OutputFormat::Text => {
            report::render_text(&report, &mut writer)?;
        }
        OutputFormat::Json => {
            report::render_json(&report, &mut writer)?;
        }
    }

    // 6. Exit code
    let should_fail = match fail_on {
        FailOn::Violation => report.summary.violations > 0,
        FailOn::Improvement => report.summary.violations > 0 || report.summary.improvements > 0,
        FailOn::Any => {
            report.summary.violations > 0
                || report.summary.improvements > 0
                || report.summary.semconv_issues > 0
        }
    };

    if should_fail {
        std::process::exit(1);
    }

    Ok(())
}

fn run_compare(
    golden: PathBuf,
    replay: PathBuf,
    config: Option<PathBuf>,
    diff_only: bool,
    format: OutputFormat,
) -> Result<(), WeaverGuardError> {
    info!(golden = %golden.display(), replay = %replay.display(), "Starting comparison");

    // Initialize compare engine
    let engine = CompareEngine::from_config_file(config.as_deref());

    // Run comparison
    let reports = if golden.is_dir() && replay.is_dir() {
        engine.compare_dirs(&golden, &replay)?
    } else if golden.is_file() && replay.is_file() {
        vec![engine.compare_files(&golden, &replay)?]
    } else {
        return Err(WeaverGuardError::Anyhow(anyhow!(
            "Golden and replay must both be files or both be directories"
        )));
    };

    if reports.is_empty() {
        eprintln!("No comparison reports generated");
        return Ok(());
    }

    // Output results
    match format {
        OutputFormat::Text => {
            if diff_only {
                println!("{}", compare::tui::render_text_diff(&reports));
            } else {
                // Run interactive TUI
                let result = compare::tui::run_tui(reports.clone())?;
                eprintln!(
                    "[golden-check] {} of {} report(s) fully accepted",
                    result.accepted_reports.len(),
                    reports.len()
                );
                if result.accepted_reports.is_empty() {
                    eprintln!("[golden-check] Hint: press 'A' to accept ALL diffs in a file, then 'q' to quit and write back.");
                    println!("No changes accepted.");
                } else {
                    for idx in &result.accepted_reports {
                        let report = &reports[*idx];
                        std::fs::copy(&report.actual_path, &report.golden_path)?;
                        println!("Updated: {}", report.golden_path.display());
                    }
                    println!("{} golden file(s) updated.", result.accepted_reports.len());
                }
            }
        }
        OutputFormat::Json => {
            println!("{}", compare::tui::render_json_diff(&reports));
        }
    }

    // Exit code: fail if any report has changes
    let has_changes = reports.iter().any(|r| !r.passed);
    if has_changes {
        std::process::exit(1);
    }

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    #[test]
    fn test_cli_default_values() {
        let cli = Cli::try_parse_from(["weaver-guard", "validate", "test.jsonl"]);
        assert!(cli.is_ok());
    }

    #[test]
    fn test_cli_with_policies_dir() {
        let cli = Cli::try_parse_from([
            "weaver-guard",
            "validate",
            "test.jsonl",
            "--policies-dir",
            "./policies",
        ]);
        assert!(cli.is_ok());
    }

    #[test]
    fn test_cli_with_semconv_dir() {
        let cli = Cli::try_parse_from([
            "weaver-guard",
            "validate",
            "test.jsonl",
            "--semconv-dir",
            "./semconv",
        ]);
        assert!(cli.is_ok());
    }

    #[test]
    fn test_cli_with_multiple_semconv_dirs() {
        let cli = Cli::try_parse_from([
            "weaver-guard",
            "validate",
            "test.jsonl",
            "--semconv-dir",
            "./base-semconv",
            "--semconv-dir",
            "./langfuse-semconv",
        ]);
        assert!(cli.is_ok());
    }

    #[test]
    fn test_cli_json_format() {
        let cli =
            Cli::try_parse_from(["weaver-guard", "validate", "test.jsonl", "--format", "json"]);
        assert!(cli.is_ok());
    }

    #[test]
    fn test_cli_public_help_name() {
        use clap::CommandFactory;
        let help = Cli::command().render_long_help().to_string();
        assert!(help.contains("weaver-guard"));
    }

    #[test]
    fn test_serve_without_path_flags_preserves_omission_for_standalone_resolution() {
        let cli = Cli::try_parse_from(["weaver-guard", "serve"]).unwrap();
        let Commands::Serve {
            policies_dir,
            semconv_dir,
            victoria_home,
            victoria_script,
            jaeger_home,
            jaeger_script,
            diagnostic_log,
            replay_dir,
            ..
        } = cli.command
        else {
            panic!("expected serve command");
        };
        assert!(policies_dir.is_none());
        assert!(semconv_dir.is_empty());
        assert!(victoria_home.is_none());
        assert!(victoria_script.is_none());
        assert!(jaeger_home.is_none());
        assert!(jaeger_script.is_none());
        assert!(diagnostic_log.is_none());
        assert!(replay_dir.is_none());
    }

    #[test]
    fn test_serve_with_explicit_repo_paths_parses() {
        let cli = Cli::try_parse_from([
            "weaver-guard",
            "serve",
            "--policies-dir",
            "policies",
            "--semconv-dir",
            "../docs/semconv",
            "--victoria-home",
            ".victoria",
            "--victoria-script",
            "scripts/victoria-macos-arm64.sh",
            "--jaeger-home",
            ".jaeger",
            "--jaeger-script",
            "scripts/jaeger-macos-arm64.sh",
            "--diagnostic-log",
            "live/diagnostics.jsonl",
            "--replay-dir",
            "live/replays",
        ])
        .unwrap();
        let Commands::Serve {
            policies_dir,
            semconv_dir,
            victoria_home,
            victoria_script,
            jaeger_home,
            jaeger_script,
            diagnostic_log,
            replay_dir,
            ..
        } = cli.command
        else {
            panic!("expected serve command");
        };
        assert_eq!(policies_dir, Some(PathBuf::from("policies")));
        assert_eq!(semconv_dir, vec![PathBuf::from("../docs/semconv")]);
        assert_eq!(victoria_home, Some(PathBuf::from(".victoria")));
        assert_eq!(
            victoria_script,
            Some(PathBuf::from("scripts/victoria-macos-arm64.sh"))
        );
        assert_eq!(jaeger_home, Some(PathBuf::from(".jaeger")));
        assert_eq!(
            jaeger_script,
            Some(PathBuf::from("scripts/jaeger-macos-arm64.sh"))
        );
        assert_eq!(
            diagnostic_log,
            Some(PathBuf::from("live/diagnostics.jsonl"))
        );
        assert_eq!(replay_dir, Some(PathBuf::from("live/replays")));
    }

    #[test]
    fn test_development_serve_paths_detect_weaver_dir() {
        let temp = tempfile::tempdir().unwrap();
        let repo = temp.path();
        let weaver = repo.join("weaver");
        fs::create_dir_all(weaver.join("policies")).unwrap();
        fs::create_dir_all(repo.join("docs").join("semconv")).unwrap();
        fs::create_dir_all(weaver.join("scripts")).unwrap();
        fs::write(
            weaver.join("scripts").join("victoria-macos-arm64.sh"),
            "#!/usr/bin/env bash\n",
        )
        .unwrap();
        fs::write(
            weaver.join("scripts").join("jaeger-macos-arm64.sh"),
            "#!/usr/bin/env bash\n",
        )
        .unwrap();

        let paths = development_serve_paths_from(&weaver).unwrap();
        assert_eq!(paths.policies_dir, weaver.join("policies"));
        assert_eq!(paths.semconv_dir, repo.join("docs").join("semconv"));
        assert_eq!(paths.victoria_home, weaver.join(".victoria"));
        assert_eq!(
            paths.victoria_download_dir,
            weaver.join(".downloads").join("victoria")
        );
        assert_eq!(paths.jaeger_home, weaver.join(".jaeger"));
        assert_eq!(
            paths.jaeger_download_dir,
            weaver.join(".downloads").join("jaeger")
        );
    }

    #[test]
    fn test_development_serve_paths_detect_from_child_dir() {
        let temp = tempfile::tempdir().unwrap();
        let repo = temp.path();
        let weaver = repo.join("weaver");
        let child = weaver.join("weaver-guard").join("src");
        fs::create_dir_all(weaver.join("policies")).unwrap();
        fs::create_dir_all(repo.join("docs").join("semconv")).unwrap();
        fs::create_dir_all(weaver.join("scripts")).unwrap();
        fs::create_dir_all(&child).unwrap();
        fs::write(
            weaver.join("scripts").join("victoria-macos-arm64.sh"),
            "#!/usr/bin/env bash\n",
        )
        .unwrap();
        fs::write(
            weaver.join("scripts").join("jaeger-macos-arm64.sh"),
            "#!/usr/bin/env bash\n",
        )
        .unwrap();

        let paths = development_serve_paths_from(&child).unwrap();
        assert_eq!(paths.policies_dir, weaver.join("policies"));
        assert_eq!(paths.semconv_dir, repo.join("docs").join("semconv"));
    }
}
