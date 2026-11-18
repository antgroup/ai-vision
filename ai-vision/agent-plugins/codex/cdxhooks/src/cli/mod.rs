pub mod config;
pub mod info;
pub mod report;
pub mod worker;

/// CLI subcommands.
pub enum SubCommand {
    /// No arguments: stdin hook main flow (default behavior).
    Hook,
    /// `cdxhooks info` — show build info and paths.
    Info,
    /// `cdxhooks report (--session-path <path> | --otel-json <path>) [--config <path>] [--profile <name>] [--shift] [--debug]`
    Report {
        profile: Option<String>,
        config_path: String,
        session_path: Option<String>,
        otel_json: Option<String>,
        shift: bool,
        debug: bool,
    },
    /// `cdxhooks worker ...` — inspect or wake durable state work.
    Worker(worker::WorkerCommand),
    /// Hidden detached hook worker entry.
    InternalWorker,
    /// `cdxhooks help` / `cdxhooks -h`
    Help,
    /// `cdxhooks version` / `cdxhooks -V`
    Version,
}

const REPORT_USAGE: &str =
    "Usage: cdxhooks report (--session-path <path> | --otel-json <path>) [--config <path>] [--profile <name>] [--shift] [--debug]";

pub fn parse_args() -> SubCommand {
    let mut pargs = pico_args::Arguments::from_env();

    if pargs.contains(["-h", "--help"]) {
        return SubCommand::Help;
    }
    if pargs.contains(["-V", "--version"]) {
        return SubCommand::Version;
    }

    match pargs.subcommand().ok().flatten().as_deref() {
        Some("info") => SubCommand::Info,
        Some("version") => SubCommand::Version,
        Some("help") => SubCommand::Help,
        Some("report") => {
            let debug = pargs.contains("--debug");
            let shift = pargs.contains("--shift");
            let profile: Option<String> = pargs.opt_value_from_str("--profile").ok().flatten();
            let config_path: Option<String> = pargs.opt_value_from_str("--config").ok().flatten();
            let session_path: Option<String> =
                pargs.opt_value_from_str("--session-path").ok().flatten();
            let otel_json: Option<String> = pargs.opt_value_from_str("--otel-json").ok().flatten();
            // --session-path and --otel-json are mutually exclusive; exactly one required
            match (&session_path, &otel_json) {
                (Some(_), Some(_)) => {
                    eprintln!("Error: --session-path and --otel-json are mutually exclusive");
                    eprintln!("{}", REPORT_USAGE);
                    std::process::exit(1);
                }
                (None, None) => {
                    eprintln!("Error: one of --session-path or --otel-json is required");
                    eprintln!("{}", REPORT_USAGE);
                    std::process::exit(1);
                }
                _ => {}
            }
            if shift && otel_json.is_none() {
                eprintln!("Error: --shift requires --otel-json");
                eprintln!("{}", REPORT_USAGE);
                std::process::exit(1);
            }
            let config_path = config_path.unwrap_or_else(|| {
                let default_config = std::path::PathBuf::from("report.config.toml");
                if default_config.exists() {
                    default_config.to_string_lossy().to_string()
                } else {
                    eprintln!("Error: --config not specified and report.config.toml not found in current directory");
                    eprintln!("{}", REPORT_USAGE);
                    std::process::exit(1);
                }
            });
            SubCommand::Report {
                profile,
                config_path,
                session_path,
                otel_json,
                shift,
                debug,
            }
        }
        Some("worker") => SubCommand::Worker(worker::parse_args(pargs)),
        Some("__worker") => SubCommand::InternalWorker,
        Some(unknown) => {
            eprintln!("Unknown command: {}", unknown);
            eprintln!("Run 'cdxhooks help' for usage");
            std::process::exit(1);
        }
        None => SubCommand::Hook,
    }
}
