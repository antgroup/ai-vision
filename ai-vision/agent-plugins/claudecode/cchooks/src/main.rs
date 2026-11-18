mod cli;
mod common;
mod hooks;
mod worker;

#[cfg(test)]
mod test_sync {
    use std::sync::{Mutex, MutexGuard, OnceLock};

    static ENV_LOCK: OnceLock<Mutex<()>> = OnceLock::new();

    pub fn env_lock() -> MutexGuard<'static, ()> {
        ENV_LOCK
            .get_or_init(|| Mutex::new(()))
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
    }
}

use std::panic::{self, AssertUnwindSafe};
use std::time::Instant;

use agentic_otel_common::metrics::{self, MetricsCollector};
use agentic_otel_common::otlp::OtelAttribute;

use common::dirs;
#[cfg(feature = "aivision")]
use common::hook::env_equals_ignore_case;
use common::hook::{env_cc, env_is_truthy, env_or_default, read_hook_payload_with_raw, HookEvent};
use common::logger::Logger;
#[cfg(feature = "dynamic")]
use hooks::dynamic::dynamic_handler::DynamicDataHandler;
use hooks::langfuse::langfuse_handler::LangfuseHandler;
use hooks::langfuse::realtime::RealtimeLangfuseHandler;
use hooks::langfuse::realtime_generation::RealtimeGenerationHandler;
use hooks::langfuse::LangfuseConfig;
use hooks::langfuse::{resolve_agent_product, resolve_agent_type};
use hooks::resource_context::ResourceContextHandler;
#[cfg(feature = "skill")]
use hooks::skill_tag_handler::SkillTagHandler;
use hooks::subagent::subagent_handler::SubagentHandler;
use hooks::timing::TimingHandler;
#[cfg(all(feature = "tool_context", not(windows)))]
use hooks::tool_context::ToolContextHandler;
#[cfg(feature = "turn_digest")]
use hooks::turn_digest::turn_digest_handler::TurnDigestHandler;
use hooks::{CommonContext, HandlerError, HookHandlerRegistry, StopOutcome};

thread_local! {
    static PARENT_PANIC_LOGGER: std::cell::RefCell<Option<Logger>> = const {
        std::cell::RefCell::new(None)
    };
}

// Build-time variables injected via build.rs
const HOOK_VERSION: &str = env!("HOOK_VERSION");
const HOOK_COMMIT: &str = env!("HOOK_COMMIT");
const HOOK_BUILD_TIME: &str = env!("HOOK_BUILD_TIME");

fn main() {
    match cli::parse_args() {
        cli::SubCommand::Info => cli::info::run(),
        cli::SubCommand::Version => {
            println!("cchooks {} ({})", HOOK_VERSION, HOOK_COMMIT);
        }
        cli::SubCommand::Help => print_help(),
        cli::SubCommand::InternalWorker => run_worker_entry(),
        cli::SubCommand::WorkerStatus { session_id } => {
            match worker::worker_status(session_id.as_deref()) {
                Ok(status) => println!("{}", serde_json::json!({"status": status})),
                Err(error) => {
                    eprintln!("Error: {error}");
                    std::process::exit(1);
                }
            }
        }
        cli::SubCommand::Report {
            profile,
            config_path,
            session_path,
            otel_json,
            shift,
            debug,
        } => {
            if let Err(e) =
                cli::report::run(profile, config_path, session_path, otel_json, shift, debug)
            {
                eprintln!("Error: {}", e);
                std::process::exit(1);
            }
        }
        cli::SubCommand::Hook => {
            if worker::is_worker_mode() {
                run_worker_entry();
            } else {
                run_parent_hook_entry();
            }
        }
    }
}

fn print_help() {
    println!("cchooks {} — Claude Code observability hooks", HOOK_VERSION);
    println!();
    println!("USAGE:");
    println!("  cchooks                    Run as Claude Code hook (reads stdin)");
    println!("  cchooks info               Show build info and paths (JSON)");
    println!("  cchooks version            Show version");
    println!("  cchooks report [OPTIONS]   Report session transcript to Langfuse");
    println!("  cchooks worker status      Show durable worker backlog/status (JSON)");
    println!("  cchooks help               Show this help");
    println!();
    println!("REPORT OPTIONS:");
    println!("  --session-path <path>      Path to session transcript (mutually exclusive with --otel-json)");
    println!("  --otel-json <path>         Path to OTLP JSONL replay file (mutually exclusive with --session-path)");
    println!(
        "  --shift                    Shift timestamps to current time (requires --otel-json)"
    );
    println!("  --config <path>            Path to config.toml (default: report.config.toml)");
    println!(
        "  --profile <name>           Langfuse profile name (default: config default_profile)"
    );
    println!("  --debug                    Print debug info to stderr");
    println!();
    println!("WORKER STATUS OPTIONS:");
    println!("  --session-id <id>          Restrict status to one Claude Code session");
}

fn run_parent_hook() {
    if !agentic_otel_common::privacy::validate_mode("CCHOOKS_COLLECTION_MODE") { return; }
    if !common::hook::is_telemetry_enabled("CCHOOKS_ENABLE_TELEMETRY") {
        return;
    }

    let t_start = Instant::now();
    let log_file = dirs::log_dir().join("cchooks.log");
    let debug_enabled = env_is_truthy("CCHOOKS_DEBUG");
    let logger = Logger::new(log_file, debug_enabled);
    logger.debug(&format!(
        "cchooks {} ({}) built {}",
        HOOK_VERSION, HOOK_COMMIT, HOOK_BUILD_TIME
    ));

    let t_stdin = Instant::now();
    let (mut payload, raw_payload) =
        read_hook_payload_with_raw().unwrap_or_else(|| (serde_json::json!({}), Vec::new()));
    let session_id = common::hook::extract_session_id(&payload);
    let hook_event_name = payload
        .get("hook_event_name")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let logger = match dirs::session_log_file(&session_id) {
        Some(path) => logger.with_log_file(path),
        None => logger,
    }
    .with_event(&hook_event_name);
    install_parent_panic_logger(&logger);

    common::otel::log_parent_phase(&logger, "stdin", t_stdin.elapsed().as_millis());

    if hook_event_name.is_empty() {
        logger.warn("Missing 'hook_event_name' in stdin payload, skipping");
        return;
    }

    let event = match HookEvent::parse(&hook_event_name) {
        Some(event) => event,
        None => {
            logger.warn(&format!(
                "Unknown hook event type '{}', skipping",
                hook_event_name
            ));
            return;
        }
    };

    if session_id.trim().is_empty() {
        logger.warn("Missing session_id in stdin payload; skipping hook processing");
        return;
    }

    if matches!(event, HookEvent::UserPromptSubmit) {
        let upstream_traceparent = crate::common::env::var("TRACEPARENT")
            .ok()
            .filter(|value| !value.is_empty());
        match hooks::turn_seed::replace_turn_seed_for_prompt(
            &session_id,
            &payload,
            upstream_traceparent,
        ) {
            Ok(seed) => logger.debug(&format!(
                "parent turn seed replaced: turn_id={} trace_id={}",
                seed.turn_id, seed.trace_id
            )),
            Err(hooks::langfuse::state::StateTxnError::Busy { .. }) => {
                logger.warn("parent turn seed lock unavailable; fail-open")
            }
            Err(error) => logger.warn(&format!("parent turn seed failed: {error}")),
        }
    }
    // Build the content digest from the original prompt before redaction;
    // the seed stores only IDs, timestamps and digests, never prompt text.
    if common::otel::is_privacy_mode() { agentic_otel_common::privacy::scrub_hook_payload(&mut payload); }
    let raw_payload = if common::otel::is_privacy_mode() { serde_json::to_vec(&payload).unwrap_or_default() } else { raw_payload };
    let stop_family = matches!(
        event,
        HookEvent::Stop | HookEvent::StopFailure | HookEvent::SessionEnd
    );
    let structured_output = if stop_family {
        match hooks::resolve_bool_setting(
            crate::common::env::var(hooks::STRUCTURED_OUTPUT_ENV).ok(),
        ) {
            hooks::BoolSetting::Valid(value) => value,
            hooks::BoolSetting::Unset => false,
            hooks::BoolSetting::Invalid(value) => {
                logger.warn(&format!(
                    "Invalid {}={:?}; structured-output Stop coordination disabled",
                    hooks::STRUCTURED_OUTPUT_ENV,
                    value
                ));
                false
            }
        }
    } else {
        false
    };

    let stop_turn_seed = if stop_family {
        match hooks::turn_seed::read_current_turn_seed(&session_id) {
            Ok(seed) => seed,
            Err(error) => {
                logger.warn(&format!(
                    "parent turn seed snapshot failed; fail-open: {error}"
                ));
                None
            }
        }
    } else {
        None
    };
    let stop_transcript_end_offset = if stop_family {
        worker::transcript_end_offset(&payload)
    } else {
        None
    };
    if stop_family {
        let handoff = worker::write_stop_intent_with_turn_seed(
            &event,
            &payload,
            structured_output,
            stop_turn_seed.as_ref(),
            stop_transcript_end_offset,
            &logger,
        );
        handoff.apply_to_payload(&mut payload);
    }
    #[cfg(all(feature = "tool_context", not(windows)))]
    if matches!(event, HookEvent::PreToolUse)
        && matches!(
            crate::common::hook::env_or_default(
                "CCHOOKS_TOOL_CONTEXT",
                "CCHOOKS_TOOL_CONTEXT",
                "false"
            )
            .as_str(),
            "true" | "1"
        )
    {
        let langfuse_config = resolve_langfuse_config();
        let username = resolve_username();
        if let Some(response) = hooks::tool_context::build_pretooluse_control_response(
            &payload,
            langfuse_config.as_ref(),
            &username,
            resolve_export_mode_for_parent(),
            Some(&logger),
        ) {
            println!("{response}");
        }
    }

    let queue_started = Instant::now();
    let transcript_ingest_receipt = if stop_family && worker::transcript_ingest_wait_enabled() {
        match worker::prepare_transcript_ingest_receipt(&session_id) {
            Ok(receipt) => Some(receipt),
            Err(error) => {
                logger.warn(&format!(
                    "transcript ingest receipt unavailable; fail-open: {error}"
                ));
                None
            }
        }
    } else {
        None
    };
    if stop_family {
        let queued = worker::spawn_detached_with_stop_snapshot_and_receipt(
            &payload,
            stop_turn_seed,
            stop_transcript_end_offset,
            transcript_ingest_receipt.clone(),
            &logger,
        );
        if let Some(receipt) = transcript_ingest_receipt.as_deref() {
            let wait_started = Instant::now();
            let timeout = if queued {
                worker::transcript_ingest_wait_timeout()
            } else {
                std::time::Duration::ZERO
            };
            match worker::wait_for_transcript_ingest(&session_id, receipt, timeout) {
                worker::TranscriptIngestWaitOutcome::Acked => logger.debug(&format!(
                    "transcript ingest barrier: result=acked elapsed_ms={}",
                    wait_started.elapsed().as_millis()
                )),
                worker::TranscriptIngestWaitOutcome::TimedOut => logger.warn(&format!(
                    "transcript ingest barrier: result=timeout elapsed_ms={}; fail-open",
                    wait_started.elapsed().as_millis()
                )),
                worker::TranscriptIngestWaitOutcome::StateFailed(error) => logger.warn(&format!(
                    "transcript ingest barrier: result=state_failed elapsed_ms={} error={}; fail-open",
                    wait_started.elapsed().as_millis(),
                    error.chars().take(512).collect::<String>()
                )),
            }
        }
    } else {
        worker::spawn_detached_raw(&raw_payload, &logger);
    }
    common::otel::log_parent_phase(&logger, "queue", queue_started.elapsed().as_millis());
    if matches!(event, HookEvent::SessionEnd) && structured_output {
        let barrier_started = Instant::now();
        let wait_timeout = hooks::StopBudget::from_session_end_env(t_start)
            .processing_time_remaining()
            .unwrap_or_default();
        match worker::wait_for_session_idle(&session_id, wait_timeout, &logger) {
            worker::SessionIdleWaitOutcome::Drained => logger.info(&format!(
                "structured SessionEnd queue barrier: result=drained elapsed_ms={}",
                barrier_started.elapsed().as_millis()
            )),
            worker::SessionIdleWaitOutcome::TimedOut => logger.warn(&format!(
                "structured SessionEnd queue barrier: result=timeout elapsed_ms={}",
                barrier_started.elapsed().as_millis()
            )),
            worker::SessionIdleWaitOutcome::WakeFailed(error) => logger.warn(&format!(
                "structured SessionEnd queue barrier: result=wake_failed elapsed_ms={} error={}",
                barrier_started.elapsed().as_millis(),
                error.chars().take(512).collect::<String>()
            )),
            worker::SessionIdleWaitOutcome::StateFailed(error) => logger.warn(&format!(
                "structured SessionEnd queue barrier: result=state_failed elapsed_ms={} error={}",
                barrier_started.elapsed().as_millis(),
                error.chars().take(512).collect::<String>()
            )),
        }
    }
    common::otel::log_parent_phase(&logger, "total", t_start.elapsed().as_millis());
}

fn run_parent_hook_entry() {
    clear_parent_panic_logger();
    let result = panic::catch_unwind(AssertUnwindSafe(run_parent_hook));
    if let Err(error) = result {
        let message = if let Some(message) = error.downcast_ref::<&str>() {
            (*message).to_string()
        } else if let Some(message) = error.downcast_ref::<String>() {
            message.clone()
        } else {
            "unknown panic".to_string()
        };
        let logger = parent_panic_logger().unwrap_or_else(|| {
            Logger::new(
                dirs::log_dir().join("cchooks.log"),
                env_is_truthy("CCHOOKS_DEBUG"),
            )
        });
        logger.error(&format!("[PARENT PANIC] {message}"));
    }
    clear_parent_panic_logger();
}

fn install_parent_panic_logger(logger: &Logger) {
    PARENT_PANIC_LOGGER.with(|slot| *slot.borrow_mut() = Some(logger.clone()));
}

fn parent_panic_logger() -> Option<Logger> {
    PARENT_PANIC_LOGGER.with(|slot| slot.borrow().clone())
}

fn clear_parent_panic_logger() {
    PARENT_PANIC_LOGGER.with(|slot| *slot.borrow_mut() = None);
}

fn run_worker_entry() {
    let Some(session_id) = worker::inherited_session_id() else {
        eprintln!("cchooks internal worker missing session queue identity");
        std::process::exit(2);
    };
    let base_logger = Logger::new(
        dirs::log_dir().join("cchooks.log"),
        env_is_truthy("CCHOOKS_DEBUG"),
    );
    let owner = match worker::acquire_queued_worker(&session_id) {
        Ok(owner) => owner,
        Err(error) => {
            base_logger.warn(&format!("worker ownership unavailable: {error}"));
            return;
        }
    };
    if let Err(error) = worker::record_session_identity(&session_id) {
        base_logger.warn(&format!("worker identity publication failed: {error}"));
    }
    base_logger.info(&format!(
        "session worker active: session_id={} worker_pid={}",
        session_id,
        std::process::id()
    ));
    match worker::drain_queue_with_maintenance(
        &session_id,
        Some(owner),
        |item| {
            let worker::QueueItem {
                payload,
                queued_at_ns,
                env,
                turn_seed,
                transcript_end_offset,
                transcript_ingest_receipt,
            } = item;
            if worker::with_worker_env(&env, || {
                run_worker_payload(
                    payload,
                    queued_at_ns,
                    turn_seed,
                    transcript_end_offset,
                    transcript_ingest_receipt,
                )
            }) {
                worker::QueueDisposition::Ack
            } else {
                worker::QueueDisposition::Retry("worker payload processing failed".to_string())
            }
        },
        || run_scheduled_stop_recovery_slice(&session_id, &base_logger),
    ) {
        Ok(stats) => {
            if stats.runtime_exhausted {
                match worker::wake_session_confirmed(&session_id, &base_logger) {
                    Ok(true) => base_logger.debug("worker runtime cap reached; successor spawned"),
                    Ok(false) => {
                        base_logger.debug("worker runtime cap reached; successor coalesced")
                    }
                    Err(error) => base_logger.warn(&format!(
                        "worker runtime cap reached; successor wake failed: {error}"
                    )),
                }
            }
        }
        Err(error) => base_logger.warn(&format!("worker queue drain failed: {error}")),
    }
}

fn run_scheduled_stop_recovery_slice(
    session_id: &str,
    logger: &Logger,
) -> worker::MaintenanceDisposition {
    match worker::scheduled_stop_recovery(session_id, logger) {
        Ok(worker::PendingStopRecovery::Idle) => worker::MaintenanceDisposition::Idle,
        Ok(worker::PendingStopRecovery::Waiting(delay)) => {
            worker::MaintenanceDisposition::Waiting(delay)
        }
        Ok(worker::PendingStopRecovery::Ready(recovery)) => {
            logger.warn("recovering pending Stop-family export intent in worker maintenance");
            let worker::RecoveredStop {
                payload,
                env,
                turn_seed,
                transcript_end_offset,
                queued_at_ns,
            } = *recovery;
            let _ = worker::with_worker_env(&env, || {
                run_worker_payload(
                    payload,
                    queued_at_ns,
                    turn_seed,
                    transcript_end_offset,
                    None,
                )
            });
            worker::MaintenanceDisposition::DidWork
        }
        Err(error) => {
            logger.warn(&format!("Stop recovery maintenance failed: {error}"));
            worker::MaintenanceDisposition::Idle
        }
    }
}

fn run_worker_payload(
    payload: serde_json::Value,
    queued_at_ns: u128,
    turn_seed: Option<hooks::turn_seed::TurnSeed>,
    transcript_end_offset: Option<u64>,
    transcript_ingest_receipt: Option<String>,
) -> bool {
    let worker_started = Instant::now();
    let t_stdin = Instant::now();
    let logger = worker_logger_for_payload(&payload);
    let hook_event_name = payload
        .get("hook_event_name")
        .and_then(|value| value.as_str())
        .unwrap_or("");
    let event_id = payload
        .get("tool_use_id")
        .or_else(|| payload.get("uuid"))
        .and_then(|value| value.as_str())
        .unwrap_or("");
    let worker_id = worker::current_worker_id();
    common::otel::install_worker_log_context(&logger, hook_event_name, event_id, &worker_id);
    common::otel::log_worker_phase(&logger, "queue", worker::queued_elapsed_ms(queued_at_ns));

    let event = payload
        .get("hook_event_name")
        .and_then(|value| value.as_str())
        .and_then(HookEvent::parse);
    let stop_owner = if matches!(
        event,
        Some(HookEvent::Stop | HookEvent::StopFailure | HookEvent::SessionEnd)
    ) {
        match worker::prepare_stop_worker(&payload, &logger) {
            worker::StopWorkerDecision::Run { owner } => {
                worker::drain_tool_events(&common::hook::extract_session_id(&payload), &logger);
                owner
            }
            worker::StopWorkerDecision::AwaitingContinuation
            | worker::StopWorkerDecision::Stale => {
                if let Err(error) = worker::acknowledge_transcript_ingest(
                    &common::hook::extract_session_id(&payload),
                    transcript_ingest_receipt.as_deref(),
                ) {
                    logger.warn(&format!(
                        "transcript ingest skip acknowledgement failed: {error}"
                    ));
                }
                common::otel::log_worker_http_total(&logger);
                common::otel::log_worker_phase(
                    &logger,
                    "total",
                    worker_started.elapsed().as_millis(),
                );
                common::otel::clear_worker_log_context();
                return true;
            }
        }
    } else {
        None
    };
    let post_claim = match event {
        Some(HookEvent::PreToolUse) => {
            worker::prepare_pre_worker(&payload, &logger);
            None
        }
        Some(HookEvent::PostToolUse | HookEvent::PostToolUseFailure) => {
            match worker::prepare_post_worker(&payload, &logger) {
                worker::PostWorkerDecision::Run { store, claim_id } => Some((store, claim_id)),
                worker::PostWorkerDecision::Skip => {
                    common::otel::log_worker_http_total(&logger);
                    common::otel::log_worker_phase(
                        &logger,
                        "total",
                        worker_started.elapsed().as_millis(),
                    );
                    common::otel::clear_worker_log_context();
                    return true;
                }
            }
        }
        _ => None,
    };
    let mut metrics = create_metrics_collector();

    let worker_session_id = common::hook::extract_session_id(&payload);
    let worker_payload = payload.clone();
    let result = panic::catch_unwind(AssertUnwindSafe(|| {
        run_hook_with_payload(
            &mut metrics,
            payload,
            t_stdin.elapsed(),
            turn_seed,
            transcript_end_offset,
            transcript_ingest_receipt.clone(),
        )
    }));
    if let Err(error) = worker::acknowledge_transcript_ingest(
        &worker_session_id,
        transcript_ingest_receipt.as_deref(),
    ) {
        logger.warn(&format!(
            "transcript ingest fallback acknowledgement failed: {error}"
        ));
    }
    let (success, stop_outcome) = match result {
        Ok(result) => (result.success, result.stop_outcome),
        Err(e) => {
            let msg = if let Some(s) = e.downcast_ref::<&str>() {
                s.to_string()
            } else if let Some(s) = e.downcast_ref::<String>() {
                s.clone()
            } else {
                "unknown panic".to_string()
            };
            if let Some(ref mut m) = metrics {
                m.inc(
                    "aivision.agentic.exec_error",
                    "Execution error",
                    "{count}",
                    vec![
                        OtelAttribute::string("error.type", "panic"),
                        OtelAttribute::string("error.message", &msg),
                    ],
                );
            }
            logger.error(&format!("[PANIC] {}", msg));
            (
                false,
                StopOutcome::deferred("Stop-family worker panicked before completion"),
            )
        }
    };
    if let Some((store, claim_id)) = post_claim {
        let checkpoint_started = Instant::now();
        worker::finish_post_worker(store, &claim_id, success, &logger);
        common::otel::log_worker_phase(
            &logger,
            "checkpoint",
            checkpoint_started.elapsed().as_millis(),
        );
    }
    if matches!(
        event,
        Some(HookEvent::Stop | HookEvent::StopFailure | HookEvent::SessionEnd)
    ) {
        let outcome = if success {
            stop_outcome
        } else {
            StopOutcome::deferred("Stop-family dispatch or auxiliary send failed")
        };
        worker::finish_stop_worker(
            &worker_session_id,
            stop_owner.as_ref(),
            &worker_payload,
            &outcome,
            queued_at_ns,
            &logger,
        );
    }
    if let Some(ref m) = metrics {
        let metrics_started = Instant::now();
        m.flush_silent();
        common::otel::log_worker_phase(
            &logger,
            "metrics_http",
            metrics_started.elapsed().as_millis(),
        );
    }
    common::otel::log_worker_http_total(&logger);
    common::otel::log_worker_phase(&logger, "total", worker_started.elapsed().as_millis());
    common::otel::clear_worker_log_context();
    success
}

struct HookRunResult {
    success: bool,
    stop_outcome: StopOutcome,
}

fn worker_logger_for_payload(payload: &serde_json::Value) -> Logger {
    let debug_enabled = env_is_truthy("CCHOOKS_DEBUG");
    let base = Logger::new(dirs::log_dir().join("cchooks.log"), debug_enabled);
    let session_id = common::hook::extract_session_id(payload);
    let hook_event_name = payload
        .get("hook_event_name")
        .and_then(|value| value.as_str())
        .unwrap_or("");
    match dirs::session_log_file(&session_id) {
        Some(path) => base.with_log_file(path),
        None => base,
    }
    .with_event(hook_event_name)
}

fn create_metrics_collector() -> Option<MetricsCollector> {
    let username = resolve_username();
    let metrics_endpoint = resolve_metrics_endpoint();
    metrics_endpoint.map(|endpoint| {
        metrics::MetricsCollector::with_headers(
            endpoint,
            metrics::ResourceAttrs {
                username: username.clone(),
                agent_product: resolve_agent_product(),
                agent_type: resolve_agent_type(),
                sdk_name: "cchooks".to_string(),
                agent_instance: agentic_otel_common::hostname_from_system()
                    .unwrap_or_else(|_| "unknown".to_string()),
                hook_version: HOOK_VERSION.to_string(),
                agent_scene: crate::common::hook::env_or_default(
                    "CCHOOKS_AGENT_SCENE",
                    "AIVISION_AGENT_SCENE",
                    "Default",
                ),
                agent_env: crate::common::hook::env_or_default(
                    "CCHOOKS_AGENT_ENV",
                    "AIVISION_AGENT_ENV",
                    "Default",
                ),
                collection_mode: agentic_otel_common::privacy::resolve_mode("CCHOOKS_COLLECTION_MODE")
                    .unwrap_or("privacy").to_owned(),
                emit_meta_trigger: true,
            },
            resolve_metrics_headers(),
        )
    })
}

fn run_hook_with_payload(
    metrics: &mut Option<MetricsCollector>,
    payload: serde_json::Value,
    stdin_elapsed: std::time::Duration,
    turn_seed: Option<hooks::turn_seed::TurnSeed>,
    transcript_end_offset: Option<u64>,
    transcript_ingest_receipt: Option<String>,
) -> HookRunResult {
    if !agentic_otel_common::privacy::validate_mode("CCHOOKS_COLLECTION_MODE") {
        return HookRunResult { success: true, stop_outcome: StopOutcome::IntentionallySkipped };
    }
    if !common::hook::is_telemetry_enabled("CCHOOKS_ENABLE_TELEMETRY") {
        return HookRunResult {
            success: true,
            stop_outcome: StopOutcome::IntentionallySkipped,
        };
    }

    let t_start = Instant::now();
    let log_file = dirs::log_dir().join("cchooks.log");

    let debug_enabled = env_is_truthy("CCHOOKS_DEBUG");
    let logger = Logger::new(log_file, debug_enabled);

    logger.info(&format!(
        "cchooks worker {} ({}) built {}",
        HOOK_VERSION, HOOK_COMMIT, HOOK_BUILD_TIME
    ));

    // Resolve Langfuse config
    let langfuse_config = resolve_langfuse_config();
    if let Some(ref config) = langfuse_config {
        logger.debug(&format!("Using Langfuse host: {}", config.host));
    }

    let username = resolve_username();
    let projects_dir = dirs::projects_dir().to_string_lossy().to_string();

    let session_id = common::hook::extract_session_id(&payload);
    let hook_event_name = payload
        .get("hook_event_name")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let logger = match dirs::session_log_file(&session_id) {
        Some(path) => logger.with_log_file(path),
        None => logger,
    }
    .with_event(&hook_event_name);

    logger.info(&format!("stdin read: {}ms", stdin_elapsed.as_millis()));
    logger.debug("stdin payload accepted");

    // Validate the hook event type parsed from the payload field above.
    if hook_event_name.is_empty() {
        logger.warn("Missing 'hook_event_name' in stdin payload, skipping");
        return HookRunResult {
            success: true,
            stop_outcome: StopOutcome::IntentionallySkipped,
        };
    }

    let event = match HookEvent::parse(&hook_event_name) {
        Some(event) => event,
        None => {
            logger.warn(&format!(
                "Unknown hook event type '{}', skipping",
                hook_event_name
            ));
            return HookRunResult {
                success: true,
                stop_outcome: StopOutcome::IntentionallySkipped,
            };
        }
    };

    if session_id.trim().is_empty() {
        logger.warn("Missing session_id in stdin payload; skipping stateful hook processing");
        return HookRunResult {
            success: true,
            stop_outcome: StopOutcome::IntentionallySkipped,
        };
    }

    let state_store = match hooks::langfuse::state::SessionStateStore::for_session(&session_id, 500)
    {
        Ok(store) => store,
        Err(error) => {
            logger.warn(&format!(
                "Unable to initialize session state for session_id={}: {}",
                session_id, error
            ));
            return HookRunResult {
                success: false,
                stop_outcome: StopOutcome::deferred("session state initialization failed"),
            };
        }
    };
    let state_file = state_store.state_file().to_path_buf();

    logger.info(&format!("Hook event: {:?}", event));

    // ── aivision.agentic.up ──
    if let Some(ref mut m) = metrics {
        m.inc(
            "aivision.agentic.up",
            "Component invocation",
            "{count}",
            vec![],
        );
    }

    // Take metrics out to wrap in RefCell for CommonContext
    let metrics_cell = metrics.take().map(std::cell::RefCell::new);

    let stop_budget = if matches!(
        event,
        HookEvent::Stop | HookEvent::StopFailure | HookEvent::SessionEnd
    ) {
        let scheduled_recovery = payload
            .get("cchooks_recovered_stop_intent")
            .and_then(serde_json::Value::as_bool)
            .unwrap_or(false);
        let budget = if scheduled_recovery {
            hooks::StopBudget::for_scheduled_recovery(t_start)
        } else if matches!(event, HookEvent::SessionEnd) {
            hooks::StopBudget::from_session_end_env(t_start)
        } else {
            hooks::StopBudget::from_env(t_start)
        };
        logger.info(&format!(
            "Hook budget: event={:?} total={}ms max_http={}ms checkpoint_reserve={}ms",
            event,
            budget.total_budget().as_millis(),
            budget.max_http_timeout().as_millis(),
            budget.checkpoint_reserve().as_millis(),
        ));
        Some(budget)
    } else {
        None
    };
    let retry_policy = match crate::common::env::var(hooks::RETRY_POLICY_ENV) {
        Ok(value) => hooks::RetryPolicy::parse(&value).unwrap_or_else(|| {
            logger.warn(&format!(
                "Invalid {}={:?}; falling back to at_least_once",
                hooks::RETRY_POLICY_ENV,
                value
            ));
            hooks::RetryPolicy::AtLeastOnce
        }),
        Err(_) => hooks::RetryPolicy::AtLeastOnce,
    };
    logger.info(&format!(
        "Turn export retry policy: {}",
        retry_policy.as_str()
    ));
    let export_mode = match crate::common::env::var(hooks::REALTIME_EXPORT_ENV) {
        Ok(value) => hooks::ExportMode::parse(&value).unwrap_or_else(|| {
            logger.warn(&format!(
                "Invalid {}={:?}; falling back to realtime export",
                hooks::REALTIME_EXPORT_ENV,
                value
            ));
            hooks::ExportMode::default()
        }),
        Err(_) => hooks::ExportMode::default(),
    };
    logger.info(&format!("Trace export mode: {}", export_mode.as_str()));
    let realtime_llm_batch_size =
        match crate::common::env::var(hooks::REALTIME_LLM_EXPORT_BATCH_SIZE_ENV) {
            Ok(value) => hooks::parse_positive_batch_size(&value).unwrap_or_else(|| {
                logger.warn(&format!(
                    "Invalid {}={:?}; falling back to {}",
                    hooks::REALTIME_LLM_EXPORT_BATCH_SIZE_ENV,
                    value,
                    hooks::DEFAULT_REALTIME_LLM_EXPORT_BATCH_SIZE
                ));
                hooks::DEFAULT_REALTIME_LLM_EXPORT_BATCH_SIZE
            }),
            Err(_) => hooks::DEFAULT_REALTIME_LLM_EXPORT_BATCH_SIZE,
        };
    logger.info(&format!(
        "Realtime LLM export: enabled={} configured_batch_size={}",
        export_mode.realtime_enabled(),
        realtime_llm_batch_size
    ));

    // Build shared context
    let context = CommonContext {
        logger,
        state_file,
        langfuse_config,
        username,
        projects_dir,
        debug_enabled,
        hook_version: HOOK_VERSION.to_string(),
        turn_seed,
        stop_transcript_end_offset: transcript_end_offset,
        transcript_ingest_receipt,
        stop_budget,
        retry_policy,
        export_mode,
        realtime_llm_batch_size,
        metrics: metrics_cell,
        send_queue: std::cell::RefCell::new(Vec::new()),
        send_errors: std::sync::Arc::new(std::sync::Mutex::new(Vec::new())),
        stop_outcome: std::cell::RefCell::new(StopOutcome::deferred(
            "Stop handler did not reach a terminal outcome",
        )),
    };

    // Register handlers
    let mut registry = HookHandlerRegistry::new();
    // TimingHandler first: records precise timestamps before other handlers process
    registry.register(Box::new(TimingHandler::new()));
    // Capture session-owned repository context before realtime/tool consumers.
    registry.register(Box::new(ResourceContextHandler::new()));
    if export_mode.realtime_enabled() {
        registry.register(Box::new(RealtimeLangfuseHandler::new()));
        registry.register(Box::new(RealtimeGenerationHandler::new()));
    }
    #[cfg(all(feature = "tool_context", not(windows)))]
    if matches!(
        crate::common::hook::env_or_default(
            "CCHOOKS_TOOL_CONTEXT",
            "CCHOOKS_TOOL_CONTEXT",
            "false"
        )
        .as_str(),
        "true" | "1"
    ) {
        registry.register(Box::new(ToolContextHandler::new()));
    }
    registry.register(Box::new(LangfuseHandler::new()));
    #[cfg(feature = "skill")]
    registry.register(Box::new(SkillTagHandler::new()));
    #[cfg(feature = "dynamic")]
    registry.register(Box::new(DynamicDataHandler::new()));
    #[cfg(feature = "turn_digest")]
    registry.register(Box::new(TurnDigestHandler::new()));
    registry.register(Box::new(SubagentHandler::new()));

    // Dispatch event to matching handlers, isolated from the send flush below.
    //
    // Pre-refactor each handler POSTed inline, so an earlier handler's send was
    // already on the wire before a later handler (e.g. SubagentHandler) could
    // panic. Now sends are deferred to run_send_tasks at the end of run_hook.
    // Without panic isolation here, a panic in dispatch would unwind through
    // run_hook (caught by the outer catch_unwind in main) and skip the send
    // flush, dropping every queued POST. Wrap dispatch so a panic is downgraded
    // to an error entry and the flush still runs.
    let dispatch_started = Instant::now();
    let errors = match panic::catch_unwind(AssertUnwindSafe(|| {
        registry.dispatch(&event, &payload, &context)
    })) {
        Ok(errors) => errors,
        Err(panic_payload) => {
            let msg = if let Some(s) = panic_payload.downcast_ref::<&str>() {
                s.to_string()
            } else if let Some(s) = panic_payload.downcast_ref::<String>() {
                s.clone()
            } else {
                "unknown panic".to_string()
            };
            context.logger.error(&format!(
                "Handler dispatch panicked after {}ms: {}",
                dispatch_started.elapsed().as_millis(),
                msg
            ));
            vec![("dispatch".to_string(), HandlerError::from(msg))]
        }
    };

    // ── aivision.agentic.exec_error (handler failures) ──
    if let Some(ref metrics_cell) = context.metrics {
        let mut m = metrics_cell.borrow_mut();
        for (handler_name, err) in &errors {
            m.inc(
                "aivision.agentic.exec_error",
                "Execution error",
                "{count}",
                vec![
                    OtelAttribute::string("handler.name", handler_name),
                    OtelAttribute::string("error.type", &err.message),
                ],
            );
        }
    }

    let mut success = errors.is_empty();
    if !errors.is_empty() {
        context.logger.warn(&format!(
            "{} handler(s) failed for event {:?}",
            errors.len(),
            event
        ));
    }

    context.logger.info(&format!(
        "total: {}ms (event={:?}) execution=async",
        t_start.elapsed().as_millis(),
        event
    ));

    // Concurrently flush auxiliary fire-and-forget network sends. Langfuse
    // Stop/StopFailure/SessionEnd exports synchronously checkpoint each turn
    // inside the handler and never enter this queue.
    // Metrics flush is deliberately NOT part of this — it stays serial in main()
    // after catch_unwind so panic-path metrics are preserved.
    let send_tasks = context
        .send_queue
        .borrow_mut()
        .drain(..)
        .collect::<Vec<_>>();
    if !send_tasks.is_empty() {
        let t_send = Instant::now();
        let n = send_tasks.len();
        let durations = crate::common::concurrent::run_send_tasks(send_tasks);
        let wall_elapsed = t_send.elapsed();
        let wall_ms = wall_elapsed.as_millis();
        crate::common::otel::observe_current_worker_phase("http", wall_elapsed.as_millis());
        // Concurrency benefit: serial total would be sum(durations); parallel
        // wall is ~max(durations). saved = serial - wall.
        let serial_ms: u128 = durations.iter().map(|d| d.as_millis()).sum();
        let saved_ms = serial_ms.saturating_sub(wall_ms);
        context.logger.info(&format!(
            "concurrent send flush: {} task(s) in {}ms (serial≈{}ms, saved≈{}ms)",
            n, wall_ms, serial_ms, saved_ms
        ));
    }

    // Drain send failures recorded by worker threads and emit metrics on the
    // MAIN thread (the metrics collector is kept single-owner on the main
    // thread). This preserves the pre-refactor export_error semantics.
    if let Some(ref metrics_cell) = context.metrics {
        if let Ok(mut errs) = context.send_errors.lock() {
            if !errs.is_empty() {
                success = false;
            }
            let mut m = metrics_cell.borrow_mut();
            for e in errs.drain(..) {
                m.inc(
                    e.metric,
                    "Export error",
                    "{count}",
                    vec![OtelAttribute::string("error.type", &e.error_type)],
                );
            }
        }
    }

    // Move metrics back out of context for main() to flush
    let stop_outcome = context.stop_outcome();
    if let Some(cell) = context.metrics {
        *metrics = Some(cell.into_inner());
    }
    HookRunResult {
        success,
        stop_outcome,
    }
}

#[cfg(all(feature = "tool_context", not(windows)))]
fn resolve_export_mode_for_parent() -> hooks::ExportMode {
    match crate::common::env::var(hooks::REALTIME_EXPORT_ENV) {
        Ok(value) => hooks::ExportMode::parse(&value).unwrap_or_default(),
        Err(_) => hooks::ExportMode::default(),
    }
}

/// Resolve username from env chain: AIVISION_ANT_USERNAME > CCHOOKS_USERNAME > LANGFUSE_ATTR_USERNAME > LANGFUSE_ATTR_ANT_USERNAME > USER
fn resolve_username() -> String {
    crate::common::env::var("AIVISION_ANT_USERNAME")
        .ok()
        .filter(|v| !v.is_empty())
        .or_else(|| env_cc("CCHOOKS_USERNAME", "LANGFUSE_ATTR_USERNAME"))
        .or_else(|| {
            crate::common::env::var("LANGFUSE_ATTR_ANT_USERNAME")
                .ok()
                .filter(|v| !v.is_empty())
        })
        .or_else(|| agentic_otel_common::otlp::parse_otel_span_username("CCHOOKS_OTEL_ATTRIBUTES"))
        .unwrap_or_else(|| env_or_default("USER", "USERNAME", "unknown"))
}

/// Runtime credentials override built-ins; worker overlays read the renewed token cache.
fn resolve_metrics_headers() -> Vec<(String, String)> {
    #[cfg(feature = "aivision")]
    let builtin = env!("LANGFUSE_BUILTIN_PROD_HEADERS");
    #[cfg(not(feature = "aivision"))]
    let builtin = "";
    let runtime = crate::common::env::var("CCHOOKS_OTEL_EXPORTER_OTLP_HEADERS")
        .unwrap_or_default();
    metrics::resolve_headers(builtin, &runtime)
}

#[cfg(test)]
mod metrics_header_tests {
    use super::*;

    #[test]
    fn metrics_headers_read_runtime_worker_context() {
        let env = std::collections::BTreeMap::from([
            ("AIVISION_MANAGED_INGEST".to_string(), "0".to_string()),
            (
                "CCHOOKS_OTEL_EXPORTER_OTLP_HEADERS".to_string(),
                "Authorization=Bearer runtime".to_string(),
            ),
        ]);
        common::env::with_worker_env(&env, || {
            assert!(resolve_metrics_headers()
                .contains(&("authorization".to_string(), "Bearer runtime".to_string())));
        });
    }

    #[test]
    fn metrics_headers_read_renewed_managed_token() {
        let directory = tempfile::tempdir().unwrap();
        let config = directory.path().join("ingest.json");
        let cache = directory.path().join("ingest-token.json");
        std::fs::write(
            &config,
            r#"{"space_id":"space","endpoint":"http://collector"}"#,
        )
        .unwrap();
        let env = std::collections::BTreeMap::from([
            ("AIVISION_MANAGED_INGEST".to_string(), "1".to_string()),
            (
                "AIVISION_INGEST_CONFIG".to_string(),
                config.to_str().unwrap().to_string(),
            ),
            (
                "CCHOOKS_OTEL_EXPORTER_OTLP_HEADERS".to_string(),
                "Authorization=Bearer stale-launcher-token".to_string(),
            ),
        ]);
        common::env::with_worker_env(&env, || {
            for token in ["first", "renewed"] {
                std::fs::write(
                    &cache,
                    serde_json::json!({
                        "space_id": "space", "endpoint": "http://collector",
                        "valid_until": 4102444800_u64, "token": token,
                    })
                    .to_string(),
                )
                .unwrap();
                assert!(resolve_metrics_headers()
                    .contains(&("authorization".to_string(), format!("Bearer {token}"))));
            }
        });
    }
}

/// Resolve metrics endpoint: env var CCHOOKS_METRICS_ENDPOINT overrides built-in.
fn resolve_metrics_endpoint() -> Option<String> {
    // Env var takes priority
    if let Some(endpoint) = crate::common::env::var("CCHOOKS_METRICS_ENDPOINT")
        .ok()
        .filter(|v| !v.is_empty())
    {
        return Some(endpoint);
    }
    // Fall back to compile-time built-in from keys.json (empty = not configured)
    #[cfg(feature = "aivision")]
    {
        let builtin = env!("METRICS_BUILTIN_ENDPOINT");
        if !builtin.is_empty() {
            return Some(builtin.to_string());
        }
    }
    None
}

fn resolve_langfuse_config() -> Option<LangfuseConfig> {
    #[cfg(feature = "aivision")]
    let mut config = {
        let env_val = env_cc("CCHOOKS_LANGFUSE_ENV", "LANGFUSE_ENV").unwrap_or_default();
        let is_gray =
            env_val.eq_ignore_ascii_case("gray") || env_equals_ignore_case("ANT_GRAY", "true");

        Some(if is_gray {
            LangfuseConfig::gray()
        } else {
            LangfuseConfig::prod()
        })
    };

    #[cfg(not(feature = "aivision"))]
    let mut config = LangfuseConfig::from_env();

    if let Some(ref mut c) = config {
        let extra = agentic_otel_common::otlp::parse_otel_exporter_headers(
            "CCHOOKS_OTEL_EXPORTER_OTLP_HEADERS",
        );
        if !extra.is_empty() {
            let headers = c.headers.get_or_insert_with(std::collections::HashMap::new);
            for (k, v) in extra {
                headers.insert(k, v);
            }
        }
    }

    config
}
