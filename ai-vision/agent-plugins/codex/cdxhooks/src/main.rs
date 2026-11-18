use cdxhooks::{cli, common, hooks, worker};

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

use agentic_otel_common::metrics::{self, MetricsCollector};
use agentic_otel_common::otlp::OtelAttribute;

use common::dirs;
use common::hook::{env_is_truthy, read_hook_payload, resolve_username, HookEvent};
use common::logger::Logger;
#[cfg(feature = "dynamic")]
use hooks::dynamic::dynamic_handler::DynamicDataHandler;
use hooks::langfuse::langfuse_handler::{LangfuseHandler, StopDispatchOutcome};
use hooks::langfuse::LangfuseConfig;
use hooks::langfuse::{resolve_agent_product, resolve_agent_type};
use hooks::resource_context::ResourceContextHandler;
#[cfg(feature = "skill")]
use hooks::skill_tag_handler::SkillTagHandler;
#[cfg(feature = "tool_context")]
use hooks::tool_context::ToolContextHandler;
use hooks::{CommonContext, HookHandlerRegistry};

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
            println!("cdxhooks {} ({})", HOOK_VERSION, HOOK_COMMIT);
        }
        cli::SubCommand::Help => print_help(),
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
        cli::SubCommand::Worker(command) => {
            if let Err(e) = cli::worker::run(command) {
                eprintln!("Error: {}", e);
                std::process::exit(1);
            }
        }
        cli::SubCommand::InternalWorker => run_internal_worker_entry(),
        cli::SubCommand::Hook => run_parent_hook_entry(),
    }
}

fn run_parent_hook_entry() {
    clear_parent_panic_logger();
    let result = panic::catch_unwind(AssertUnwindSafe(run_parent_hook));
    if let Err(error) = result {
        let message = panic_message(error.as_ref());
        let logger = parent_panic_logger().unwrap_or_else(|| {
            Logger::new(
                dirs::log_dir().join("cdxhooks.log"),
                env_is_truthy("CDXHOOKS_DEBUG"),
            )
        });
        logger.error(&format!("parent panic: {message}"));
    }
    clear_parent_panic_logger();
}

fn run_parent_hook() {
    if !agentic_otel_common::privacy::validate_mode("CDXHOOKS_COLLECTION_MODE") { return; }
    if !agentic_otel_common::otlp::is_telemetry_enabled("CDXHOOKS_ENABLE_TELEMETRY") {
        return;
    }
    let started = std::time::Instant::now();
    let Some((mut payload, raw_payload)) = common::hook::read_hook_payload_with_raw() else {
        return;
    };
    if common::otel::is_privacy_mode() { agentic_otel_common::privacy::scrub_hook_payload(&mut payload); }
    let session_id = common::hook::extract_session_id(&payload);
    let hook_event_name = payload
        .get("hook_event_name")
        .and_then(serde_json::Value::as_str)
        .unwrap_or("")
        .to_string();
    let log_file =
        dirs::session_log_file(&session_id).unwrap_or_else(|| dirs::log_dir().join("cdxhooks.log"));
    if let Some(parent) = log_file.parent() {
        let _ = dirs::ensure_private_dir(parent);
    }
    let logger =
        Logger::new(log_file, env_is_truthy("CDXHOOKS_DEBUG")).with_event(&hook_event_name);
    install_parent_panic_logger(&logger);
    let identity = dirs::session_key(&session_id).unwrap_or_else(|| "missing-session".to_string());
    let mut timing =
        common::timing::PhaseRecorder::with_started("parent", &hook_event_name, &identity, started);
    timing.mark("stdin");

    let Some(event) = HookEvent::parse(&hook_event_name) else {
        logger.warn("invalid or missing hook_event_name; skipping");
        timing.mark("validate");
        timing.record(&logger, "invalid_event");
        return;
    };
    if session_id.trim().is_empty() {
        logger.warn("missing session_id; skipping persistent hook processing");
        timing.mark("validate");
        timing.record(&logger, "missing_session");
        return;
    }
    timing.mark("validate");

    if matches!(event, HookEvent::PreToolUse) {
        let context = parent_control_context(&logger, &session_id);
        #[cfg(feature = "tool_context")]
        if let Some(response) =
            hooks::tool_context::build_pretooluse_control_response(&payload, &context)
        {
            println!("{response}");
        }
    }
    timing.mark("updated_input");

    if matches!(event, HookEvent::UserPromptSubmit) {
        let upstream = std::env::var("TRACEPARENT")
            .ok()
            .filter(|value| !value.is_empty());
        if let Err(error) = common::turn_seed::replace_for_prompt(&session_id, &payload, upstream) {
            logger.warn(&format!("turn seed publish failed open: {error}"));
        }
    }
    if matches!(event, HookEvent::Stop | HookEvent::SessionEnd) {
        if let Some(path) = dirs::session_stop_intent_file(&session_id) {
            let intent = common::turn_seed::stop_intent(&hook_event_name, &payload);
            if let Err(error) = common::runtime_state::write_json_atomically(&path, &intent) {
                logger.warn(&format!("Stop intent publish failed open: {error}"));
            }
            if let (Some(payload), Some(intent)) = (payload.as_object_mut(), intent.as_object()) {
                for field in ["intent_id", "target_turn_id", "observed_file_len"] {
                    if let Some(value) = intent.get(field) {
                        payload.insert(field.to_string(), value.clone());
                    }
                }
            }
        }
    }
    timing.mark("state_publish");

    let queued_payload_len = serde_json::to_vec(&payload)
        .map(|bytes| bytes.len())
        .unwrap_or(raw_payload.len());
    let outcome = match worker::enqueue_and_wake(&session_id, &payload, queued_payload_len, &logger)
    {
        Ok(_) => "queued",
        Err(error) => {
            logger.warn(&format!("worker queue handoff failed open: {error}"));
            "queue_failed"
        }
    };
    timing.mark("spawn");
    timing.record(&logger, outcome);
}

fn run_internal_worker_entry() {
    if !worker::is_worker_mode() {
        return;
    }

    if let Some(session_id) = worker::inherited_session_id() {
        run_queued_worker(&session_id);
        return;
    }

    // Compatibility for an old parent or a direct test/admin invocation that
    // still hands one envelope to `__worker` over stdin.
    let Some(payload) = read_hook_payload() else {
        return;
    };
    let session_id = common::hook::extract_session_id(&payload);
    let owner_started = std::time::Instant::now();
    let owner = match worker::acquire_session(&session_id) {
        Ok(owner) => owner,
        Err(error) => {
            log_worker_entry_error(&session_id, "ownership failed", &error);
            return;
        }
    };
    let ownership_ms = owner_started.elapsed().as_millis();
    run_internal_worker_payload(payload, 0, ownership_ms);
    drop(owner);
}

fn run_queued_worker(session_id: &str) {
    let owner = match worker::acquire_queued_worker(session_id) {
        Ok(owner) => owner,
        Err(error) => {
            log_worker_entry_error(session_id, "worker ownership failed", &error);
            return;
        }
    };
    if let Some(log_file) = dirs::session_log_file(session_id) {
        Logger::new(log_file, env_is_truthy("CDXHOOKS_DEBUG"))
            .with_event("state-worker")
            .info(&format!(
                "session worker active: worker_pid={}",
                std::process::id()
            ));
    }
    let result = worker::drain_queue_with_maintenance(
        session_id,
        Some(owner),
        |item| {
            let queue_ms = worker::queued_elapsed_ms(item.queued_at_ns);
            crate::common::env::with_worker_env(&item.env, || {
                run_internal_worker_payload(item.payload, queue_ms, 0)
            })
        },
        || run_scheduled_stop_recovery_slice(session_id),
    );
    match result {
        Ok(stats) if stats.runtime_exhausted => {
            let Some(log_file) = dirs::session_log_file(session_id) else {
                return;
            };
            let logger =
                Logger::new(log_file, env_is_truthy("CDXHOOKS_DEBUG")).with_event("state-worker");
            match worker::wake_session_confirmed(session_id, &logger) {
                Ok(_) => {
                    let _ = worker::clear_handoff_intent(session_id);
                }
                Err(error) => log_worker_entry_error(session_id, "runtime handoff failed", &error),
            }
        }
        Ok(_) => {}
        Err(error) => log_worker_entry_error(session_id, "queue drain failed", &error),
    }
}

fn run_scheduled_stop_recovery_slice(session_id: &str) -> worker::MaintenanceDisposition {
    use cli::worker::{ScheduledStopRecovery, ScheduledSubagentRecovery};

    let stop_disposition = match cli::worker::scheduled_stop_recovery_payload(session_id) {
        Ok(ScheduledStopRecovery::Idle) => worker::MaintenanceDisposition::Idle,
        Ok(ScheduledStopRecovery::Waiting(delay)) => worker::MaintenanceDisposition::Waiting(delay),
        Ok(ScheduledStopRecovery::Eligible(mut payload)) => {
            payload["recovery_source"] = serde_json::json!("stop_recovery");
            let disposition = run_internal_worker_payload(payload.clone(), 0, 0);
            if let worker::QueueDisposition::Retry(reason)
            | worker::QueueDisposition::RetryDurable(reason) = disposition
            {
                if let Some(state_file) = dirs::session_state_file(session_id) {
                    let mut state = hooks::langfuse::state::load_state(&state_file);
                    if let Err(error) = hooks::langfuse::state::record_stop_recovery_deferred(
                        &state_file,
                        &mut state,
                        session_id,
                        &payload,
                        &reason,
                        &common::logger::now_iso8601_pub(),
                    ) {
                        log_worker_entry_error(
                            session_id,
                            "Stop recovery checkpoint failed",
                            &error,
                        );
                        return worker::MaintenanceDisposition::Waiting(
                            std::time::Duration::from_millis(500),
                        );
                    }
                }
            }
            worker::MaintenanceDisposition::DidWork
        }
        Err(error) => {
            log_worker_entry_error(session_id, "Stop recovery eligibility failed", &error);
            worker::MaintenanceDisposition::Idle
        }
    };
    continue_recovery_maintenance(stop_disposition, || {
        match cli::worker::scheduled_subagent_recovery(session_id) {
            Ok(ScheduledSubagentRecovery::Idle) => worker::MaintenanceDisposition::Idle,
            Ok(ScheduledSubagentRecovery::Waiting(delay)) => {
                worker::MaintenanceDisposition::Waiting(delay)
            }
            Ok(ScheduledSubagentRecovery::Eligible(record)) => {
                match cli::worker::run_subagent_recovery(session_id, &record) {
                    Ok(true) => worker::MaintenanceDisposition::DidWork,
                    Ok(false) => {
                        worker::MaintenanceDisposition::Waiting(std::time::Duration::from_secs(1))
                    }
                    Err(reason) => {
                        log_worker_entry_error(
                            session_id,
                            "Subagent recovery dispatch deferred",
                            &reason,
                        );
                        worker::MaintenanceDisposition::Waiting(std::time::Duration::from_secs(1))
                    }
                }
            }
            Err(error) => {
                log_worker_entry_error(session_id, "Subagent recovery eligibility failed", &error);
                worker::MaintenanceDisposition::Idle
            }
        }
    })
}

/// A waiting Stop does not consume a maintenance slice. Execute at most one
/// recovery, and let the earliest pending deadline determine the next wakeup.
fn continue_recovery_maintenance(
    stop: worker::MaintenanceDisposition,
    child: impl FnOnce() -> worker::MaintenanceDisposition,
) -> worker::MaintenanceDisposition {
    use worker::MaintenanceDisposition::{DidWork, Idle, Waiting};
    if stop == DidWork {
        return DidWork;
    }
    match (stop, child()) {
        (_, DidWork) => DidWork,
        (Waiting(left), Waiting(right)) => Waiting(left.min(right)),
        (Waiting(delay), Idle) | (Idle, Waiting(delay)) => Waiting(delay),
        _ => Idle,
    }
}

fn log_worker_entry_error(session_id: &str, operation: &str, error: &str) {
    let Some(log_file) = dirs::session_log_file(session_id) else {
        return;
    };
    if let Some(parent) = log_file.parent() {
        let _ = dirs::ensure_private_dir(parent);
    }
    Logger::new(log_file, env_is_truthy("CDXHOOKS_DEBUG"))
        .with_event("state-worker")
        .warn(&format!("worker {operation}: {error}"));
}

fn run_internal_worker_payload(
    payload: serde_json::Value,
    queue_ms: u128,
    ownership_ms: u128,
) -> worker::QueueDisposition {
    if !agentic_otel_common::privacy::validate_mode("CDXHOOKS_COLLECTION_MODE") {
        return worker::QueueDisposition::Ack;
    }
    let session_id = common::hook::extract_session_id(&payload);
    let hook_event_name = payload
        .get("hook_event_name")
        .and_then(serde_json::Value::as_str)
        .unwrap_or("")
        .to_string();
    let Some(log_file) = dirs::session_log_file(&session_id) else {
        return worker::QueueDisposition::Retry("missing session log path".to_string());
    };
    if let Some(parent) = log_file.parent() {
        let _ = dirs::ensure_private_dir(parent);
    }
    let logger =
        Logger::new(log_file, env_is_truthy("CDXHOOKS_DEBUG")).with_event(&hook_event_name);
    let identity = dirs::session_key(&session_id).unwrap_or_else(|| "missing-session".to_string());
    let mut timing = common::timing::PhaseRecorder::new("worker", &hook_event_name, &identity);
    timing.set_ms("queue", queue_ms);
    timing.set_ms("ownership", ownership_ms);

    // The durable queue is the ingress write-ahead log. Publish derived
    // identity/tool facts only after the sole session worker owns the record;
    // a crash before queue acknowledgement replays this idempotently. Keeping
    // these fsync-heavy writes out of the latency-sensitive hook parent also
    // prevents unrelated session workers from amplifying parent p95 latency.
    let ingress_started = std::time::Instant::now();
    let ingress_result = (|| -> Result<(), String> {
        if let Some(path) = dirs::session_identity_file(&session_id) {
            common::runtime_state::publish_immutable_json(
                &path,
                &serde_json::json!({"session_id": session_id}),
            )?;
        }
        common::tool_event::publish_ingress_fact(&payload)?;
        Ok(())
    })();
    timing.set_ms("ingress", ingress_started.elapsed().as_millis());
    if let Err(error) = ingress_result {
        logger.warn(&format!("durable ingress publication deferred: {error}"));
        timing.record(&logger, "retry");
        return worker::QueueDisposition::Retry(error);
    }

    match common::tool_event::expire_orphan_facts(
        &session_id,
        std::time::Duration::from_secs(24 * 60 * 60),
    ) {
        Ok(0) => {}
        Ok(expired) => logger.info(&format!("expired {expired} orphan Tool fact set(s)")),
        Err(error) => logger.warn(&format!("Tool orphan cleanup deferred: {error}")),
    }

    let mut metrics = create_metrics_collector();
    let mut panic_logger = Some(logger.clone());
    let handler_started = std::time::Instant::now();
    let result = panic::catch_unwind(AssertUnwindSafe(|| {
        run_worker_hook(payload, &mut metrics, &mut panic_logger)
    }));
    timing.set_ms("handlers", handler_started.elapsed().as_millis());
    let (outcome, disposition) = match result {
        Err(error) => {
            let message = panic_message(error.as_ref());
            if let Some(ref mut collector) = metrics {
                collector.inc(
                    "aivision.agentic.exec_error",
                    "Execution error",
                    "{count}",
                    vec![
                        OtelAttribute::string("error.type", "panic"),
                        OtelAttribute::string("error.message", &message),
                    ],
                );
            }
            persist_hook_panic(panic_logger.as_ref(), &message);
            ("panic", worker::QueueDisposition::Retry(message))
        }
        Ok(Some(reason)) => ("retry", worker::QueueDisposition::RetryDurable(reason)),
        Ok(None) => ("ok", worker::QueueDisposition::Ack),
    };
    let metrics_started = std::time::Instant::now();
    if let Some(ref collector) = metrics {
        collector.flush_silent();
    }
    let metrics_http_ms = metrics_started.elapsed().as_millis();
    timing.set_ms("metrics_http", metrics_http_ms);
    if metrics.is_some() {
        common::otel::observe_worker_http_elapsed(metrics_http_ms);
    }
    timing.set_ms("http_total", common::otel::current_worker_http_total_ms());
    timing.record(&logger, outcome);
    common::otel::clear_worker_log_context();
    disposition
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

fn create_metrics_collector() -> Option<MetricsCollector> {
    let username = resolve_username();
    resolve_metrics_endpoint().map(|endpoint| {
        metrics::MetricsCollector::with_headers(
            endpoint,
            metrics::ResourceAttrs {
                username,
                agent_product: resolve_agent_product(),
                agent_type: resolve_agent_type(),
                sdk_name: "cdxhooks".to_string(),
                agent_instance: agentic_otel_common::hostname_from_system()
                    .unwrap_or_else(|_| "unknown".to_string()),
                hook_version: HOOK_VERSION.to_string(),
                agent_scene: agentic_otel_common::env_or_default(
                    "CDXHOOKS_AGENT_SCENE",
                    "AIVISION_AGENT_SCENE",
                    "Default",
                ),
                agent_env: agentic_otel_common::env_or_default(
                    "CDXHOOKS_AGENT_ENV",
                    "AIVISION_AGENT_ENV",
                    "Default",
                ),
                collection_mode: agentic_otel_common::privacy::resolve_mode("CDXHOOKS_COLLECTION_MODE")
                    .unwrap_or("privacy").to_owned(),
                emit_meta_trigger: true,
            },
            resolve_metrics_headers(),
        )
    })
}

fn configured_retry_policy(logger: &Logger) -> hooks::RetryPolicy {
    match std::env::var(hooks::RETRY_POLICY_ENV) {
        Ok(value) => hooks::RetryPolicy::parse(&value).unwrap_or_else(|| {
            logger.warn(&format!(
                "Invalid {}={:?}; falling back to at_least_once",
                hooks::RETRY_POLICY_ENV,
                value
            ));
            hooks::RetryPolicy::AtLeastOnce
        }),
        Err(_) => hooks::RetryPolicy::AtLeastOnce,
    }
}

fn configured_export_mode(logger: &Logger) -> hooks::RealtimeExportMode {
    match std::env::var(hooks::REALTIME_EXPORT_ENV) {
        Ok(value) => hooks::RealtimeExportMode::parse(&value).unwrap_or_else(|| {
            logger.warn(&format!(
                "Invalid {}={:?}; falling back to realtime export",
                hooks::REALTIME_EXPORT_ENV,
                value
            ));
            hooks::RealtimeExportMode::default()
        }),
        Err(_) => hooks::RealtimeExportMode::default(),
    }
}

fn configured_realtime_llm_batch_size(logger: &Logger) -> usize {
    match std::env::var(hooks::REALTIME_LLM_EXPORT_BATCH_SIZE_ENV) {
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
    }
}

fn parent_control_context(logger: &Logger, session_id: &str) -> CommonContext {
    CommonContext {
        logger: logger.clone(),
        state_file: dirs::legacy_session_state_file(session_id)
            .unwrap_or_else(|| dirs::state_dir().join("invalid-state.json")),
        langfuse_config: resolve_langfuse_config(),
        username: resolve_username(),
        sessions_dir: dirs::sessions_dir().to_string_lossy().to_string(),
        hook_version: HOOK_VERSION.to_string(),
        stop_budget: None,
        retry_policy: configured_retry_policy(logger),
        export_mode: configured_export_mode(logger),
        realtime_llm_batch_size: configured_realtime_llm_batch_size(logger),
        metrics: None,
    }
}

fn panic_message(error: &(dyn std::any::Any + Send)) -> String {
    error
        .downcast_ref::<&str>()
        .map(|value| (*value).to_string())
        .or_else(|| error.downcast_ref::<String>().cloned())
        .unwrap_or_else(|| "unknown panic".to_string())
}

fn print_help() {
    println!("cdxhooks {} — Codex observability hooks", HOOK_VERSION);
    println!();
    println!("USAGE:");
    println!("  cdxhooks                    Run as Codex hook (reads stdin)");
    println!("  cdxhooks info               Show build info and paths (JSON)");
    println!("  cdxhooks version            Show version");
    println!("  cdxhooks report [OPTIONS]   Report session transcript to Langfuse");
    println!("  cdxhooks worker <COMMAND>   Inspect or wake durable state work");
    println!("  cdxhooks help               Show this help");
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
    println!("WORKER COMMANDS:");
    println!("  once                       Wake one session with pending durable state");
    println!("  run                        Wake sessions with pending durable state");
    println!("  recover-stale              Re-wake durable state (no lease lifecycle)");
    println!("  status                     Print pending state-domain counts");
}

fn run_worker_hook(
    payload: serde_json::Value,
    metrics: &mut Option<MetricsCollector>,
    panic_logger: &mut Option<Logger>,
) -> Option<String> {
    if !agentic_otel_common::otlp::is_telemetry_enabled("CDXHOOKS_ENABLE_TELEMETRY") {
        return None;
    }

    let session_id = common::hook::extract_session_id(&payload);
    let hook_event_name = payload
        .get("hook_event_name")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();

    let Some((log_file, state_file)) = resolve_session_runtime_paths(&session_id) else {
        eprintln!("cdxhooks: missing session id, skipping persistent hook state");
        return None;
    };
    if let Some(parent) = log_file.parent() {
        let _ = dirs::ensure_private_dir(parent);
    }
    if let Some(parent) = state_file.parent() {
        let _ = dirs::ensure_private_dir(parent);
    }
    let debug_enabled = env_is_truthy("CDXHOOKS_DEBUG");
    let logger = Logger::new(log_file, debug_enabled).with_event(&hook_event_name);
    *panic_logger = Some(logger.clone());
    common::otel::install_worker_log_context(
        &logger,
        &hook_event_name,
        &dirs::session_key(&session_id).unwrap_or_else(|| "missing-session".to_string()),
    );

    logger.info(&format!(
        "cdxhooks {} ({}) built {}",
        HOOK_VERSION, HOOK_COMMIT, HOOK_BUILD_TIME
    ));

    // Resolve Langfuse config
    let langfuse_config = resolve_langfuse_config();
    if let Some(ref config) = langfuse_config {
        logger.debug(&format!("Using Langfuse host: {}", config.host));
    }

    let username = resolve_username();
    let sessions_dir = dirs::sessions_dir().to_string_lossy().to_string();

    logger.debug(&format!(
        "worker envelope: bytes={} event={} session_key={}",
        serde_json::to_vec(&payload).map_or(0, |bytes| bytes.len()),
        hook_event_name,
        dirs::session_key(&session_id).unwrap_or_else(|| "missing-session".to_string())
    ));

    // Validate the hook event type parsed from the payload field above.
    if hook_event_name.is_empty() {
        logger.warn("Missing 'hook_event_name' in stdin payload, skipping");
        return None;
    }

    let event = match HookEvent::parse(&hook_event_name) {
        Some(event) => event,
        None => {
            logger.warn(&format!(
                "Unknown hook event type '{}', skipping",
                hook_event_name
            ));
            return None;
        }
    };

    logger.info(&format!("Hook event: {:?}", event));

    // Ordinary lifecycle/tool hooks must never execute recovered Stop work in
    // front of their own event. Durable Stop recovery is scheduled separately
    // by the state worker or by the next explicit Stop/SessionEnd envelope.
    let stop_intent_path = dirs::session_stop_intent_file(&session_id);

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

    let stop_dispatch =
        matches!(event, HookEvent::Stop | HookEvent::SessionEnd).then_some((&event, "envelope"));
    let stop_budget = if let Some((stop_event, source)) = stop_dispatch {
        let started_at = std::time::Instant::now();
        let budget = if payload
            .get("recovery_source")
            .and_then(serde_json::Value::as_str)
            == Some("stop_recovery")
        {
            hooks::StopBudget::new(
                started_at,
                std::time::Duration::from_millis(600),
                std::time::Duration::from_millis(400),
                std::time::Duration::from_millis(100),
            )
        } else if matches!(stop_event, HookEvent::SessionEnd) {
            hooks::StopBudget::from_session_end_env(started_at)
        } else {
            hooks::StopBudget::from_env(started_at)
        };
        logger.info(&format!(
            "Hook budget: event={:?} source={} total={}ms max_http={}ms checkpoint_reserve={}ms",
            stop_event,
            source,
            budget.total_budget().as_millis(),
            budget.max_http_timeout().as_millis(),
            budget.checkpoint_reserve().as_millis(),
        ));
        Some(budget)
    } else {
        None
    };
    let retry_policy = configured_retry_policy(&logger);
    logger.info(&format!(
        "Turn export retry policy: {}",
        retry_policy.as_str()
    ));
    let export_mode = configured_export_mode(&logger);
    logger.info(&format!("Trace export mode: {}", export_mode.as_str()));
    let realtime_llm_batch_size = configured_realtime_llm_batch_size(&logger);
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
        sessions_dir,
        hook_version: HOOK_VERSION.to_string(),
        stop_budget,
        retry_policy,
        export_mode,
        realtime_llm_batch_size,
        metrics: metrics_cell,
    };

    if let Err(error) = cli::worker::import_legacy_spool_once(&session_id, 10) {
        context
            .logger
            .warn(&format!("legacy spool import deferred: {error}"));
    }

    // Register handlers
    let mut registry = HookHandlerRegistry::new();
    registry.register(Box::new(ResourceContextHandler::new()));
    #[cfg(feature = "skill")]
    registry.register(Box::new(SkillTagHandler::new()));
    #[cfg(feature = "tool_context")]
    if export_mode.realtime_enabled() || env_is_truthy("CDXHOOKS_TOOL_CONTEXT") {
        registry.register(Box::new(ToolContextHandler::new()));
    }
    #[cfg(feature = "dynamic")]
    registry.register(Box::new(DynamicDataHandler::new()));
    let langfuse_handler = LangfuseHandler::new();

    // Reconcile immutable Tool facts while this process owns the session.
    // Post-before-Pre is held in `waiting_for_pre`; a later Pre (or Stop)
    // replays the durable Post payload after the Pre-side context is ready.
    let mut dispatches = Vec::new();
    match event {
        HookEvent::PreToolUse => {
            dispatches.push((HookEvent::PreToolUse, payload.clone(), None));
            if let Some(tool_use_id) = payload_tool_use_id(&payload) {
                match common::tool_event::begin_ready(&session_id, &tool_use_id) {
                    Ok(Some(ready)) => {
                        dispatches.push((
                            HookEvent::PostToolUse,
                            ready.payload.clone(),
                            Some(ready),
                        ));
                    }
                    Ok(None) => {}
                    Err(error) => context
                        .logger
                        .warn(&format!("tool event reconcile failed: {error}")),
                }
            }
        }
        HookEvent::PostToolUse => {
            if let Some(tool_use_id) = payload_tool_use_id(&payload) {
                match common::tool_event::begin_ready(&session_id, &tool_use_id) {
                    Ok(Some(ready)) => {
                        dispatches.push((
                            HookEvent::PostToolUse,
                            ready.payload.clone(),
                            Some(ready),
                        ));
                    }
                    Ok(None) => {}
                    Err(error) => context
                        .logger
                        .warn(&format!("tool event reconcile failed: {error}")),
                }
            }
        }
        HookEvent::Stop | HookEvent::SessionEnd => {
            match common::tool_event::pending_tool_ids(&session_id, usize::MAX) {
                Ok(tool_ids) => {
                    for tool_use_id in tool_ids {
                        match common::tool_event::begin_ready(&session_id, &tool_use_id) {
                            Ok(Some(ready)) => {
                                dispatches.push((
                                    HookEvent::PostToolUse,
                                    ready.payload.clone(),
                                    Some(ready),
                                ));
                            }
                            Ok(None) => {}
                            Err(error) => context
                                .logger
                                .warn(&format!("Stop tool recovery failed: {error}")),
                        }
                    }
                }
                Err(error) => context
                    .logger
                    .warn(&format!("Stop tool scan failed: {error}")),
            }
            dispatches.push((event.clone(), payload.clone(), None));
        }
        _ => dispatches.push((event.clone(), payload.clone(), None)),
    }

    let mut errors = Vec::new();
    let mut stop_outcome = None;
    let mut recovery_state_retry = None;
    for (dispatch_event, dispatch_payload, ready_tool) in dispatches {
        let mut dispatch_errors = registry.dispatch(&dispatch_event, &dispatch_payload, &context);
        if matches!(dispatch_event, HookEvent::Stop | HookEvent::SessionEnd) {
            match langfuse_handler.handle_stop_with_outcome(
                &dispatch_event,
                &dispatch_payload,
                &context,
            ) {
                Ok(outcome) => {
                    if let StopDispatchOutcome::Deferred(reason) = &outcome {
                        context
                            .logger
                            .warn(&format!("Stop export deferred: {reason}"));
                        let mut recovery_state =
                            hooks::langfuse::state::load_state(&context.state_file);
                        let updated_at = common::logger::now_iso8601_pub();
                        match hooks::langfuse::state::record_stop_recovery_deferred(
                            &context.state_file,
                            &mut recovery_state,
                            &session_id,
                            &dispatch_payload,
                            reason,
                            &updated_at,
                        ) {
                            Ok(record) => context.logger.info(&format!(
                                "Stop recovery checkpoint: phase={:?} attempt={} no_progress={} next_eligible_at_ms={}",
                                record.phase,
                                record.attempt,
                                record.no_progress_attempts,
                                record.next_eligible_at_ms,
                            )),
                            Err(error) => {
                                let reason = format!(
                                    "persist deferred Stop recovery state: {error}"
                                );
                                context.logger.warn(&reason);
                                recovery_state_retry = Some(reason);
                            }
                        }
                    }
                    stop_outcome = Some(outcome);
                }
                Err(error) => {
                    dispatch_errors.push(("langfuse".to_string(), error));
                    stop_outcome = None;
                }
            }
        }
        if let Some(ready) = ready_tool {
            if dispatch_errors.is_empty() {
                if let Err(error) = common::tool_event::mark_terminal(&session_id, &ready) {
                    context
                        .logger
                        .warn(&format!("tool terminal checkpoint failed: {error}"));
                }
            } else {
                let reason = dispatch_errors
                    .first()
                    .map(|(_, error)| error.message.clone())
                    .unwrap_or_else(|| "hook handler failure".to_string());
                if let Err(error) =
                    common::tool_event::mark_retryable_error(&session_id, &ready, &reason)
                {
                    context
                        .logger
                        .warn(&format!("tool retry checkpoint failed: {error}"));
                }
            }
        }
        errors.extend(dispatch_errors);
    }
    let stop_completed_export = stop_outcome
        .as_ref()
        .is_some_and(StopDispatchOutcome::completed_export);
    let flush_result = if stop_completed_export {
        cli::worker::finalize_pending_realtime_flushes(&session_id, &context.state_file).map(
            |cleaned| {
                if cleaned > 0 {
                    context.logger.info(&format!(
                        "Stop finalizer pruned {cleaned} pending realtime Flush trigger(s)"
                    ));
                }
                0
            },
        )
    } else {
        cli::worker::process_pending_realtime_flushes_with_budget(
            &session_id,
            &context.state_file,
            &context.logger,
            context.langfuse_config.as_ref(),
            10,
            context.stop_budget,
        )
    };
    if let Err(error) = &flush_result {
        context.logger.warn(&format!(
            "state-native realtime Flush reconcile failed: {error}"
        ));
    }

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

    if !errors.is_empty() {
        context.logger.warn(&format!(
            "{} handler(s) failed for event {:?}",
            errors.len(),
            event
        ));
    }
    if stop_completed_export {
        match common::tool_event::finalize_session_facts(&session_id) {
            Ok(0) => {}
            Ok(cleaned) => context.logger.info(&format!(
                "Stop finalizer accounted for {cleaned} remaining Tool fact set(s)"
            )),
            Err(error) => context
                .logger
                .warn(&format!("Stop Tool fact cleanup deferred: {error}")),
        }
    }
    let expected_intent_id = payload.get("intent_id").and_then(serde_json::Value::as_str);
    let recovery_state_cleared = if stop_outcome
        .as_ref()
        .is_some_and(StopDispatchOutcome::clears_intent)
    {
        let mut recovery_state = hooks::langfuse::state::load_state(&context.state_file);
        match hooks::langfuse::state::clear_stop_recovery(
            &context.state_file,
            &mut recovery_state,
            &session_id,
            expected_intent_id,
        ) {
            Ok(_) => true,
            Err(error) => {
                let reason = format!("clear completed Stop recovery state: {error}");
                context.logger.warn(&reason);
                recovery_state_retry = Some(reason);
                false
            }
        }
    } else {
        true
    };
    if recovery_state_cleared
        && stop_outcome
            .as_ref()
            .is_some_and(StopDispatchOutcome::clears_intent)
        && matches!(event, HookEvent::Stop | HookEvent::SessionEnd)
    {
        if let Some(path) = stop_intent_path {
            let current_intent_id = std::fs::read_to_string(&path)
                .ok()
                .and_then(|content| serde_json::from_str::<serde_json::Value>(&content).ok())
                .and_then(|intent| {
                    intent
                        .get("intent_id")
                        .and_then(serde_json::Value::as_str)
                        .map(str::to_string)
                });
            let matches_current = match expected_intent_id {
                Some(expected) => current_intent_id.as_deref() == Some(expected),
                None => current_intent_id.is_none(),
            };
            if matches_current {
                let _ = std::fs::remove_file(path);
            } else {
                context.logger.info(
                    "Stop intent advanced while this dispatch was running; preserving newer intent",
                );
            }
        }
        cli::worker::clear_recovery_file_len(&session_id);
    }

    let retry_reason = recovery_state_retry.or_else(|| {
        (!stop_completed_export)
            .then(|| retryable_handler_error_reason(&errors))
            .flatten()
    });

    // Move metrics back out of context for main() to flush
    if let Some(cell) = context.metrics {
        *metrics = Some(cell.into_inner());
    }
    retry_reason
}

fn retryable_handler_error_reason(errors: &[(String, hooks::HandlerError)]) -> Option<String> {
    errors
        .iter()
        .find(|(_, error)| error.is_retryable())
        .map(|(handler, error)| format!("retryable handler failure: {handler}: {}", error.message))
}

fn payload_tool_use_id(payload: &serde_json::Value) -> Option<String> {
    payload
        .get("tool_use_id")
        .or_else(|| payload.get("toolUseId"))
        .or_else(|| payload.get("call_id"))
        .or_else(|| payload.get("callId"))
        .and_then(serde_json::Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_string)
}

fn resolve_session_runtime_paths(
    session_id: &str,
) -> Option<(std::path::PathBuf, std::path::PathBuf)> {
    Some((
        dirs::session_log_file(session_id)?,
        dirs::session_state_file(session_id)?,
    ))
}

fn persist_hook_panic(logger: Option<&Logger>, message: &str) -> bool {
    let Some(logger) = logger else {
        return false;
    };
    logger.error(&format!("[PANIC] {message}"));
    true
}

/// Runtime credentials override built-ins; worker overlays read the renewed token cache.
fn resolve_metrics_headers() -> Vec<(String, String)> {
    #[cfg(feature = "aivision")]
    let builtin = env!("LANGFUSE_BUILTIN_PROD_HEADERS");
    #[cfg(not(feature = "aivision"))]
    let builtin = "";
    let runtime = crate::common::env::var("CDXHOOKS_OTEL_EXPORTER_OTLP_HEADERS")
        .unwrap_or_default();
    metrics::resolve_headers(builtin, &runtime)
}

/// Resolve metrics endpoint: env var CDXHOOKS_METRICS_ENDPOINT overrides built-in.
fn resolve_metrics_endpoint() -> Option<String> {
    if let Some(endpoint) = std::env::var("CDXHOOKS_METRICS_ENDPOINT")
        .ok()
        .filter(|v| !v.is_empty())
    {
        return Some(endpoint);
    }
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
    hooks::langfuse::resolve_hook_export_config()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    #[test]
    fn metrics_headers_read_runtime_worker_context() {
        let env = std::collections::BTreeMap::from([
            ("AIVISION_MANAGED_INGEST".to_string(), "0".to_string()),
            (
                "CDXHOOKS_OTEL_EXPORTER_OTLP_HEADERS".to_string(),
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
                "CDXHOOKS_OTEL_EXPORTER_OTLP_HEADERS".to_string(),
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

    #[test]
    fn recovery_maintenance_runs_child_during_stop_backoff() {
        use worker::MaintenanceDisposition::{DidWork, Waiting};
        let mut called = false;
        assert_eq!(
            continue_recovery_maintenance(Waiting(std::time::Duration::from_secs(30)), || {
                called = true;
                DidWork
            }),
            DidWork
        );
        assert!(called);
    }

    #[test]
    fn recovery_maintenance_stop_work_keeps_priority_and_single_slice() {
        use worker::MaintenanceDisposition::DidWork;
        assert_eq!(
            continue_recovery_maintenance(DidWork, || panic!("second recovery in one slice")),
            DidWork
        );
    }

    #[test]
    fn recovery_maintenance_preserves_earliest_wait_and_idle() {
        use worker::MaintenanceDisposition::{Idle, Waiting};
        let short = Waiting(std::time::Duration::from_secs(1));
        let long = Waiting(std::time::Duration::from_secs(30));
        for (stop, child, expected) in [
            (short, long, short),
            (long, short, short),
            (long, Idle, long),
            (Idle, short, short),
            (Idle, Idle, Idle),
        ] {
            assert_eq!(continue_recovery_maintenance(stop, || child), expected);
        }
    }

    #[test]
    fn retryable_handler_errors_request_queue_retry() {
        let errors = vec![(
            "tool_context".to_string(),
            hooks::HandlerError::retryable("state lock unavailable after 25ms"),
        )];

        assert_eq!(
            retryable_handler_error_reason(&errors).as_deref(),
            Some("retryable handler failure: tool_context: state lock unavailable after 25ms")
        );
    }

    #[test]
    fn ordinary_handler_errors_do_not_retry_queue_records() {
        let errors = vec![(
            "langfuse".to_string(),
            hooks::HandlerError::from("terminal export error".to_string()),
        )];

        assert!(retryable_handler_error_reason(&errors).is_none());
    }

    #[test]
    fn resolve_session_runtime_paths_requires_session_id() {
        assert!(resolve_session_runtime_paths("").is_none());
        assert!(resolve_session_runtime_paths("   ").is_none());
    }

    #[test]
    fn resolve_session_runtime_paths_uses_flat_log_and_keyed_state() {
        let _guard = crate::test_sync::env_lock();
        let saved_state_dir = std::env::var("CDXHOOKS_STATE_DIR").ok();
        let saved_log_dir = std::env::var("CDXHOOKS_LOG_DIR").ok();
        std::env::set_var("CDXHOOKS_STATE_DIR", "/tmp/cdxhooks-main-state");
        std::env::set_var("CDXHOOKS_LOG_DIR", "/tmp/cdxhooks-main-log");

        let key = dirs::session_key("main/session").unwrap();
        let (log_file, state_file) = resolve_session_runtime_paths("main/session").unwrap();
        assert_eq!(
            state_file,
            PathBuf::from("/tmp/cdxhooks-main-state")
                .join(&key)
                .join("state_compat.json")
        );
        assert_eq!(
            log_file,
            PathBuf::from("/tmp/cdxhooks-main-log").join("cdxhooks_main_session.log")
        );

        match saved_state_dir {
            Some(value) => std::env::set_var("CDXHOOKS_STATE_DIR", value),
            None => std::env::remove_var("CDXHOOKS_STATE_DIR"),
        }
        match saved_log_dir {
            Some(value) => std::env::set_var("CDXHOOKS_LOG_DIR", value),
            None => std::env::remove_var("CDXHOOKS_LOG_DIR"),
        }
    }

    #[test]
    fn persist_hook_panic_writes_session_log_when_available() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("session").join("cdxhooks.log");
        let logger = Logger::new(path.clone(), false).with_event("PostToolUse");

        install_parent_panic_logger(&logger);
        let routed_logger = parent_panic_logger().unwrap();
        assert!(persist_hook_panic(Some(&routed_logger), "boom"));
        clear_parent_panic_logger();
        let content = std::fs::read_to_string(path).unwrap();
        assert!(content.contains("[ERROR] [PostToolUse] [PANIC] boom"));
        assert!(!persist_hook_panic(None, "stderr only"));
    }

    #[test]
    fn same_session_parent_and_worker_loggers_share_one_file() {
        let _guard = crate::test_sync::env_lock();
        let root = tempfile::tempdir().unwrap();
        let saved_log_dir = std::env::var("CDXHOOKS_LOG_DIR").ok();
        std::env::set_var("CDXHOOKS_LOG_DIR", root.path());

        let parent_path = dirs::session_log_file("shared/session").unwrap();
        let worker_path = dirs::session_log_file("shared/session").unwrap();
        assert_eq!(parent_path, worker_path);
        assert_eq!(parent_path, root.path().join("cdxhooks_shared_session.log"));
        Logger::new(parent_path.clone(), true)
            .with_event("PreToolUse")
            .info("parent");
        Logger::new(worker_path, true)
            .with_event("PostToolUse")
            .info("worker");

        assert!(!root.path().join("sessions").exists());
        let contents = std::fs::read_to_string(&parent_path).unwrap();
        assert!(contents.contains("[PreToolUse] parent"));
        assert!(contents.contains("[PostToolUse] worker"));
        assert_eq!(
            std::fs::read_dir(parent_path.parent().unwrap())
                .unwrap()
                .filter_map(Result::ok)
                .filter(|entry| entry.path().extension().is_some_and(|ext| ext == "log"))
                .count(),
            1
        );

        match saved_log_dir {
            Some(value) => std::env::set_var("CDXHOOKS_LOG_DIR", value),
            None => std::env::remove_var("CDXHOOKS_LOG_DIR"),
        }
    }

    #[test]
    fn cdxhooks_metrics_resource_uses_cdxhooks_identity_and_meta_trigger_fallback() {
        let _guard = crate::test_sync::env_lock();
        let saved_endpoint = std::env::var("CDXHOOKS_METRICS_ENDPOINT").ok();
        let saved_resource_attrs = std::env::var("CDXHOOKS_OTEL_RESOURCE_ATTRIBUTES").ok();
        let saved_meta_trigger = std::env::var("AIVISION_META_TRIGGER").ok();
        let saved_agent_env = std::env::var("CDXHOOKS_AGENT_ENV").ok();
        let saved_agent_scene = std::env::var("CDXHOOKS_AGENT_SCENE").ok();
        let saved_collection_mode = std::env::var("CDXHOOKS_COLLECTION_MODE").ok();

        std::env::set_var("CDXHOOKS_METRICS_ENDPOINT", "http://127.0.0.1:1");
        std::env::remove_var("AIVISION_META_TRIGGER");
        std::env::set_var(
            "CDXHOOKS_OTEL_RESOURCE_ATTRIBUTES",
            "ant.meta.trigger=interactive,custom.cdxhooks.resource=resource-value,ant.meta.trigger=automation",
        );
        std::env::set_var("CDXHOOKS_AGENT_ENV", "test-env");
        std::env::set_var("CDXHOOKS_AGENT_SCENE", "test-scene");
        std::env::set_var("CDXHOOKS_COLLECTION_MODE", "privacy");

        let mut collector = create_metrics_collector().expect("metrics collector");
        collector.inc(
            "aivision.agentic.up",
            "Component invocation",
            "{count}",
            vec![],
        );
        let payload = collector.build_payload();
        let attrs = payload["resourceMetrics"][0]["resource"]["attributes"]
            .as_array()
            .expect("resource attrs");
        let keys: Vec<&str> = attrs
            .iter()
            .filter_map(|attr| attr["key"].as_str())
            .collect();
        let trigger_count = keys
            .iter()
            .filter(|key| **key == "ant.meta.trigger")
            .count();

        restore_env("CDXHOOKS_METRICS_ENDPOINT", saved_endpoint);
        restore_env("CDXHOOKS_OTEL_RESOURCE_ATTRIBUTES", saved_resource_attrs);
        restore_env("AIVISION_META_TRIGGER", saved_meta_trigger);
        restore_env("CDXHOOKS_AGENT_ENV", saved_agent_env);
        restore_env("CDXHOOKS_AGENT_SCENE", saved_agent_scene);
        restore_env("CDXHOOKS_COLLECTION_MODE", saved_collection_mode);

        assert_eq!(attr_string(attrs, "telemetry.sdk.name"), Some("cdxhooks"));
        assert_eq!(attr_string(attrs, "ant.meta.trigger"), Some("automation"));
        assert_eq!(
            attr_string(attrs, "custom.cdxhooks.resource"),
            Some("[REDACTED]")
        );
        assert_eq!(
            attr_string(attrs, "agentic.collection.mode"),
            Some("privacy")
        );
        assert_eq!(trigger_count, 1);
        assert_eq!(
            keys,
            vec![
                "service.name",
                "service.version",
                "ant.agent.instance",
                "ant.agent.product",
                "ant.agent.type",
                "telemetry.sdk.language",
                "telemetry.sdk.name",
                "telemetry.sdk.version",
                "ant.agent.env",
                "deployment.environment",
                "ant.agent.scene",
                "custom.cdxhooks.resource",
                "ant.meta.trigger",
                "agentic.collection.mode",
            ],
            "metrics Resource key set should drift only for cdxhooks sdk identity, trigger, and explicit fallback overrides"
        );
    }

    fn restore_env(key: &str, value: Option<String>) {
        match value {
            Some(value) => std::env::set_var(key, value),
            None => std::env::remove_var(key),
        }
    }

    fn attr_string<'a>(attrs: &'a [serde_json::Value], key: &str) -> Option<&'a str> {
        attrs
            .iter()
            .find(|attr| attr["key"].as_str() == Some(key))
            .and_then(|attr| attr["value"]["stringValue"].as_str())
    }
}
