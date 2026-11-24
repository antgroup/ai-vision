use std::path::{Path, PathBuf};

fn manifest_source(relative: impl AsRef<Path>) -> String {
    std::fs::read_to_string(Path::new(env!("CARGO_MANIFEST_DIR")).join(relative))
        .unwrap()
        .replace("\r\n", "\n")
}

fn production_source(relative: impl AsRef<Path>) -> String {
    let source = manifest_source(relative);
    source
        .split_once("#[cfg(test)]\nmod tests")
        .map_or(source.clone(), |(production, _)| production.to_string())
}

fn rust_sources(root: &Path, output: &mut Vec<PathBuf>) {
    for entry in std::fs::read_dir(root).unwrap() {
        let path = entry.unwrap().path();
        if path.is_dir() {
            rust_sources(&path, output);
        } else if path.extension().is_some_and(|extension| extension == "rs") {
            output.push(path);
        }
    }
}

#[test]
fn resource_handler_is_unconditional_first_owner_of_prompt_setup() {
    let main = production_source("src/main.rs");
    let registry_start = main
        .find("let mut registry = HookHandlerRegistry::new()")
        .unwrap();
    let resource = main
        .find("registry.register(Box::new(ResourceContextHandler::new()))")
        .unwrap();
    let langfuse = main
        .find("let langfuse_handler = LangfuseHandler::new()")
        .unwrap();
    assert!(registry_start < resource && resource < langfuse);
    assert!(
        !main[registry_start..resource].contains("#[cfg("),
        "ResourceContextHandler must remain unconditional"
    );

    let tool_context = production_source("src/hooks/tool_context.rs");
    let supported = tool_context
        .split_once("fn supported_events(&self) -> &[HookEvent]")
        .unwrap()
        .1
        .split_once("fn handle(")
        .unwrap()
        .0;
    assert!(supported.contains("HookEvent::PreToolUse"));
    assert!(supported.contains("HookEvent::PostToolUse"));
    assert!(!supported.contains("HookEvent::UserPromptSubmit"));
}

#[test]
fn stop_intent_cleanup_depends_only_on_explicit_stop_outcome() {
    let main = production_source("src/main.rs");
    let cleanup = main
        .split_once("if stop_outcome")
        .unwrap()
        .1
        .split_once("// Move metrics back out of context")
        .unwrap()
        .0;
    assert!(cleanup.contains("StopDispatchOutcome::clears_intent"));
    assert!(!cleanup.contains("errors.is_empty()"));
    assert!(!cleanup.contains("flush_result.is_ok()"));
}

#[test]
fn completed_stop_prunes_pending_realtime_flushes_before_any_delivery_retry() {
    let main = production_source("src/main.rs");
    let completed = main.find("let stop_completed_export =").unwrap();
    let finalize = main[completed..]
        .find("finalize_pending_realtime_flushes")
        .map(|offset| completed + offset)
        .unwrap();
    let retry = main[completed..]
        .find("process_pending_realtime_flushes")
        .map(|offset| completed + offset)
        .unwrap();
    assert!(finalize < retry);

    let reconciliation = &main[completed..retry];
    assert!(reconciliation.contains("if stop_completed_export"));
    assert!(reconciliation.contains("finalize_pending_realtime_flushes"));
    assert!(reconciliation.contains("} else {"));
}

#[test]
fn stop_and_worker_have_no_git_discovery_fallback() {
    for relative in [
        "src/hooks/langfuse/langfuse_handler.rs",
        "src/cli/worker.rs",
    ] {
        let source = production_source(relative);
        for forbidden in [
            "collect_git_repository",
            "git_remote_url",
            "git_branch",
            "run_git(",
            "resolve_vcs_info",
        ] {
            assert!(
                !source.contains(forbidden),
                "{relative} must not contain Stop/worker Git fallback `{forbidden}`"
            );
        }
    }
}

#[test]
fn production_vcs_writes_are_owned_only_by_resource_context() {
    let manifest = Path::new(env!("CARGO_MANIFEST_DIR"));
    let mut sources = Vec::new();
    rust_sources(&manifest.join("src"), &mut sources);
    let owners = [
        Path::new("src/hooks/resource_context.rs"),
        Path::new("src/hooks/langfuse/state.rs"),
    ];
    for path in sources {
        let relative = path.strip_prefix(manifest).unwrap();
        if owners.contains(&relative) {
            continue;
        }
        let source = production_source(relative);
        assert!(
            !source.contains("set_vcs_in_state(") && !source.contains("[\"vcs\"] ="),
            "{} must not write session VCS state",
            relative.display()
        );
    }
}

#[test]
fn runtime_state_paths_are_session_sharded_without_monolithic_migration() {
    let dirs = production_source("src/common/dirs.rs");
    assert!(dirs.contains("pub fn session_state_file(session_id: &str)"));
    assert!(dirs.contains("pub fn legacy_session_state_file(session_id: &str)"));
    assert!(dirs.contains("pub fn session_domain_state_file(session_id: &str, file_name: &str)"));
    assert!(dirs.contains("session_key(session_id).map(|key| state_dir().join(key))"));
    assert!(!dirs.contains("cdxhooks_state.json"));

    let main = production_source("src/main.rs");
    assert!(main.contains("dirs::session_state_file(session_id)?"));
}

#[test]
fn tool_realtime_path_is_state_native_and_session_owned() {
    let tool_context = production_source("src/hooks/tool_context.rs");
    let tool_records = tool_context
        .split_once("pub(crate) mod tool_records")
        .unwrap()
        .1
        .split_once("impl ToolContextHandler")
        .unwrap()
        .0;
    for forbidden in ["StateLock", "session_tool_record_file", "claim", "lease"] {
        assert!(
            !tool_records.contains(forbidden),
            "Tool records must not use per-tool mutable lifecycle `{forbidden}`"
        );
    }
    for forbidden in [
        "publish_realtime_flush_job",
        "session_spool_dir",
        "Command::new",
        "worker run",
    ] {
        assert!(
            !tool_context.contains(forbidden),
            "ToolContext must register state, not produce spool/process lifecycle `{forbidden}`"
        );
    }

    let main = production_source("src/main.rs");
    let acquire = main.find("worker::acquire_session(&session_id)").unwrap();
    let handlers = main.find("run_worker_hook(payload").unwrap();
    assert!(
        acquire < handlers,
        "session ownership must precede handlers"
    );
    // worker.rs keeps portable startup tests next to the handshake helpers,
    // while drain_queue remains production code below that test module.
    // Inspect the complete module so the architecture gate covers both sides.
    let worker = manifest_source("src/worker.rs");
    assert!(worker.contains("SessionOwner::acquire_blocking(session_id)"));
    assert!(worker.contains("SessionOwner::try_acquire(session_id)"));
    assert!(worker.contains("drain_queue"));
    assert!(!worker.contains("Duration::from_secs(300)"));

    let stop = production_source("src/hooks/langfuse/langfuse_handler.rs");
    assert!(stop.contains("SessionOwner::acquire_blocking(parent_tid)"));
    assert!(!stop.contains("Duration::from_secs(300)"));
    assert!(!stop.contains("SessionLock"));
    assert!(!stop.contains("spool::"));
}

#[test]
fn direct_tool_delivery_has_no_spool_file_transition() {
    let worker = production_source("src/cli/worker.rs");
    let direct = worker
        .split_once("fn process_realtime_flush_inline_with_outcome")
        .unwrap()
        .1
        .split_once("pub fn process_pending_realtime_flushes")
        .unwrap()
        .0;
    assert!(direct.contains("process_realtime_flush("));
    assert!(direct.contains("remove_pending_flush_in_state"));
    for forbidden in ["publish_realtime", "claim_next", "spool::complete", "lease"] {
        assert!(
            !direct.contains(forbidden),
            "direct Tool delivery must not contain `{forbidden}`"
        );
    }
}

#[test]
fn worker_cli_is_legacy_drain_only_and_cannot_publish_new_jobs() {
    let worker = production_source("src/cli/worker.rs");
    let command_surface = worker
        .split_once("pub fn import_legacy_spool_once")
        .unwrap()
        .0;
    assert!(command_surface.contains("wake_pending_state"));
    assert!(command_surface.contains("state_worker_status"));
    for forbidden in [
        "WorkerCommand::Enqueue",
        "EnqueueOptions",
        "enqueue_pending_state_jobs",
        "spool::default_root",
        "process_once_session",
        "spool::claim_next",
        "spool::recover_stale",
        "spool::status(",
        "spool::enqueue(",
        "spool::enqueue_transcript",
    ] {
        assert!(
            !command_surface.contains(forbidden),
            "legacy worker CLI must not expose a new spool producer `{forbidden}`"
        );
    }
    assert!(
        !worker.contains("spool::publish_realtime_batch_job("),
        "production worker/importer must not publish derived realtime batch jobs"
    );
    assert!(!worker.contains("spool::publish_realtime_flush_job("));
    assert!(!worker.contains("spool::publish_realtime_job("));
}

#[test]
fn state_worker_recovery_routes_realtime_flushes_through_session_queue() {
    let worker = production_source("src/cli/worker.rs");
    let recovery_payload = worker
        .split_once("fn recovery_payload")
        .unwrap()
        .1
        .split_once("fn wake_pending_state")
        .unwrap()
        .0;
    assert!(recovery_payload.contains("pending_flushes > 0"));
    assert!(recovery_payload.contains("\"recovery_source\": \"state_worker\""));
    assert!(!recovery_payload.contains("process_pending_realtime_flushes"));
    assert!(!recovery_payload.contains("process_realtime_flush"));
    assert!(!recovery_payload.contains("spool::claim_next"));

    let wake_pending = worker
        .split_once("fn wake_pending_state")
        .unwrap()
        .1
        .split_once("/// Consume upgrade leftovers")
        .unwrap()
        .0;
    assert!(wake_pending.contains("crate::worker::pending_count"));
    assert!(wake_pending.contains("recovery_payload"));
    assert!(wake_pending.contains("crate::worker::wake_session"));
    assert!(wake_pending.contains("crate::worker::spawn_detached_raw"));
    for forbidden in [
        "process_once_session",
        "process_pending_realtime_flushes",
        "process_realtime_flush",
        "spool::claim_next",
        "spool::publish_realtime",
    ] {
        assert!(
            !wake_pending.contains(forbidden),
            "state-worker recovery must enqueue/wake the session queue, not inline `{forbidden}`"
        );
    }
}

#[test]
fn realtime_spool_delivery_is_session_scoped_and_worker_owned() {
    let worker = production_source("src/cli/worker.rs");
    let process_once = worker
        .split_once("fn process_once_session")
        .unwrap()
        .1
        .split_once("fn reconcile_pending_realtime_jobs")
        .unwrap()
        .0;
    assert!(process_once.contains("session_spool_root(&options.spool_dir, session_id)"));
    assert!(process_once.contains("spool::claim_next_eligible(&session_spool"));
    assert!(process_once.contains("job.session_id != session_id"));
    assert!(!process_once.contains("crate::worker::wake_session("));
    assert!(!process_once.contains("crate::worker::spawn_detached_raw"));
    assert!(!process_once.contains("crate::worker::enqueue_and_wake"));
}

#[test]
fn cdx_realtime_claim_bounds_and_checkpoint_order_are_structural_invariants() {
    let worker = production_source("src/cli/worker.rs");
    assert!(worker.contains("const REALTIME_TOOL_HTTP_TIMEOUT_MS: u64 = 5_000"));
    assert!(worker.contains(
        "const REALTIME_CHUNK_SAFE_START_BUDGET_MS: u64 = REALTIME_TOOL_HTTP_TIMEOUT_MS"
    ));
    assert!(worker.contains("const REALTIME_CLAIM_HARD_CAP_MS: u64 = 20_000"));

    let flush = worker
        .split_once("fn process_realtime_flush(")
        .unwrap()
        .1
        .split_once("fn effective_realtime_claim_budget(")
        .unwrap()
        .0;
    let plan_checkpoint = flush
        .find("prepare_realtime_delivery_plan_in_state")
        .unwrap();
    let first_post = flush.find("post_otel_to_langfuse_once_classified").unwrap();
    assert!(plan_checkpoint < first_post);
    assert!(flush.contains("REALTIME_MAX_CHUNKS_PER_CLAIM"));
    assert!(flush.contains("REALTIME_TARGET_SERIALIZED_BODY_BYTES"));
    let safe_start_check = flush.find("realtime_chunk_timeout(remaining)").unwrap();
    let first_chunk_begin = flush
        .find("checkpoint_realtime_delivery_plan_chunk_begin")
        .unwrap();
    assert!(safe_start_check < first_chunk_begin);
    assert!(flush.contains("reason=insufficient_claim_budget"));

    let budget = worker
        .split_once("fn effective_realtime_claim_budget(")
        .unwrap()
        .1
        .split_once("fn checkpoint_realtime_delivery_plan_chunk_begin(")
        .unwrap()
        .0;
    assert!(budget.contains("budget.processing_time_remaining().unwrap_or_default()"));
    assert!(budget.contains("None => hard_cap"));
    assert!(budget.contains("if remaining < safe_start_budget"));
    assert!(budget.contains("Duration::from_millis(REALTIME_TOOL_HTTP_TIMEOUT_MS)"));

    let main = production_source("src/main.rs");
    assert!(main.contains("process_pending_realtime_flushes_with_budget"));
    assert!(main.contains("context.stop_budget"));
}

#[test]
fn cross_host_lifecycle_taxonomy_is_shared_without_conflating_body_or_transport_shape() {
    let cdx = production_source("src/common/otel.rs");
    let cdx_worker = production_source("src/cli/worker.rs");
    let cch = manifest_source("../../claudecode/cchooks/src/common/otel.rs");
    let cch_generation =
        manifest_source("../../claudecode/cchooks/src/hooks/langfuse/realtime_generation.rs");
    for source in [&cdx, &cch] {
        for field in [
            "event=langfuse_send_start",
            "event=langfuse_send_end",
            "attempt_id=",
            "operation=",
            "timeout_ms=",
            "outcome=success",
            "outcome=failure",
            "error_kind=",
            "elapsed_ms=",
            "http_status=",
            "http_status_retryable=",
            "body_bytes=",
            "Adding custom header name:",
        ] {
            assert!(
                source.contains(field),
                "missing shared lifecycle field `{field}`"
            );
        }
    }

    assert!(cdx.contains("operation=other"));
    assert!(cch.contains("context.operation.as_str()"));
    assert!(cdx_worker.contains("plan_realtime_payload_chunks"));
    assert!(cch_generation.contains("MAX_REALTIME_GENERATION_REQUEST_CHUNKS"));
    assert!(!cdx.contains("PreparedLangfuseSend"));
    assert!(!cch_generation.contains("RealtimeDeliveryPlan"));
}
