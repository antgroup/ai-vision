#[test]
fn realtime_handler_has_no_transcript_reader_dependency() {
    let source = include_str!("../src/hooks/langfuse/realtime.rs");
    for prohibited in [
        "find_latest_transcript",
        "read_new_jsonl",
        "build_turns",
        "copy_transcript_to_session_dir",
    ] {
        assert!(
            !source.contains(prohibited),
            "realtime handler must not depend on {prohibited}"
        );
    }
}

#[test]
fn generation_realtime_handler_uses_only_hook_transcript_and_is_separate_from_tool_path() {
    let tool_source = include_str!("../src/hooks/langfuse/realtime.rs");
    let generation_source = include_str!("../src/hooks/langfuse/realtime_generation.rs");
    assert!(
        generation_source.contains("extract_transcript_path(payload)"),
        "generation handler must use the transcript path supplied by the hook"
    );
    for prohibited in [
        "find_latest_transcript",
        "copy_transcript_to_session_dir",
        "wait_for_file_stable",
        "DEFAULT_STOP_TRANSCRIPT_SETTLE_MS",
    ] {
        assert!(
            !generation_source.contains(prohibited),
            "generation handler must not depend on {prohibited}"
        );
    }
    assert!(
        !tool_source.contains("realtime_generation"),
        "payload-only tool realtime path must stay independent"
    );
    assert!(
        !generation_source.contains("PostToolBatch"),
        "first-release generation correctness must not depend on PostToolBatch"
    );
}

#[test]
fn one_realtime_switch_registers_both_tool_and_generation_handlers() {
    let main_source = include_str!("../src/main.rs");
    let hooks_source = include_str!("../src/hooks/mod.rs");
    let realtime_block = main_source
        .split_once("if export_mode.realtime_enabled() {")
        .expect("realtime registration block")
        .1
        .split_once("\n    }")
        .expect("realtime registration block end")
        .0;
    assert!(
        realtime_block.contains("RealtimeLangfuseHandler::new()")
            && realtime_block.contains("RealtimeGenerationHandler::new()"),
        "CCHOOKS_REALTIME_EXPORT must register tool and generation handlers together"
    );
    assert!(
        !hooks_source.contains("REALTIME_LLM_EXPORT_ENV"),
        "generation realtime must not have a second enable switch"
    );
}

#[test]
fn stop_finalization_uses_persisted_realtime_state_not_current_env_mode() {
    let source = include_str!("../src/hooks/langfuse/langfuse_handler.rs");
    assert!(
        source.contains("realtime_state::contexts(&state, &session_id)"),
        "Stop must discover persisted realtime contexts"
    );
    assert!(
        !source.contains("context.export_mode.realtime_enabled()"),
        "Stop cleanup must not depend on the current realtime environment flag"
    );
}

#[test]
fn every_state_lock_call_site_checks_ownership_before_state_io() {
    for (name, source) in [
        ("timing", include_str!("../src/hooks/timing.rs")),
        ("tool_context", include_str!("../src/hooks/tool_context.rs")),
        (
            "subagent",
            include_str!("../src/hooks/subagent/subagent_handler.rs"),
        ),
        (
            "turn_digest",
            include_str!("../src/hooks/turn_digest/turn_digest_handler.rs"),
        ),
        (
            "stop",
            include_str!("../src/hooks/langfuse/langfuse_handler.rs"),
        ),
        (
            "realtime",
            include_str!("../src/hooks/langfuse/realtime.rs"),
        ),
        (
            "realtime_generation",
            include_str!("../src/hooks/langfuse/realtime_generation.rs"),
        ),
    ] {
        for (offset, _) in source.match_indices("StateLock::acquire") {
            let tail = &source[offset..];
            let next_load = tail.find("load_state");
            let next_save = tail.find("save_state");
            let next_state_io = match (next_load, next_save) {
                (Some(load), Some(save)) => load.min(save),
                (Some(load), None) => load,
                (None, Some(save)) => save,
                (None, None) => tail.len(),
            };
            let ownership_window = &tail[..next_state_io];
            assert!(
                ownership_window.contains("is_acquired()"),
                "{name} must check lock ownership before state I/O near byte {offset}"
            );
        }
    }
}

#[test]
fn stop_keeps_transcript_otel_and_http_outside_state_transactions() {
    let source = include_str!("../src/hooks/langfuse/langfuse_handler.rs");
    assert!(
        source.contains("const TRANSCRIPT_CAPTURE_ENV: &str = \"CCHOOKS_TRANSCRIPT_CAPTURE\"")
            && source.contains("BoolSetting::Unset => true"),
        "legacy transcript capture must stay enabled by default with an explicit opt-out"
    );
    for operation in [
        "poll_transcript_until_terminal(",
        "copy_transcript_to_session_dir(",
        "build_otel_export_request_for_turn_with_full_context(",
        "post_otel_to_langfuse_once_with_context(",
    ] {
        let call = source.find(operation).expect("Stop operation call");
        let nearest_update = source[..call].rfind("state_store.update(|");
        let nearest_close = source[..call].rfind("});");
        assert!(
            nearest_update.is_none() || nearest_close > nearest_update,
            "{operation} must not execute inside a state transaction"
        );
    }
}

#[test]
fn progressive_turn_builder_records_build_phase_timing() {
    let source = include_str!("../src/common/otel.rs");
    let start = source
        .find("pub fn build_otel_export_request_for_turn_with_full_context(")
        .expect("progressive turn builder");
    let body = &source[start..];
    let end = body.find("\n}\n\n// --- HTTP POST").expect("builder end");
    assert!(
        body[..end].contains("WorkerPhaseGuard::new(\"build\")"),
        "progressive Stop-family builder must retain build-phase timing"
    );
}

#[test]
fn resource_context_is_unconditional_and_precedes_all_user_prompt_consumers() {
    let source = include_str!("../src/main.rs");
    let resource = source
        .find("registry.register(Box::new(ResourceContextHandler::new()))")
        .expect("resource context registration");
    let realtime_gate = source
        .find("if export_mode.realtime_enabled() {")
        .expect("realtime feature gate");
    let realtime = source
        .find("registry.register(Box::new(RealtimeLangfuseHandler::new()))")
        .expect("realtime registration");
    let tool_context = source
        .find("registry.register(Box::new(ToolContextHandler::new()))")
        .expect("tool-context registration");
    let stop = source
        .find("registry.register(Box::new(LangfuseHandler::new()))")
        .expect("Stop registration");

    assert!(
        resource < realtime_gate
            && resource < realtime
            && resource < tool_context
            && resource < stop,
        "resource capture must be unconditional and run before every consumer"
    );
}

#[test]
fn stop_family_and_turn_digest_have_no_git_fallback() {
    for (name, source) in [
        (
            "langfuse Stop",
            include_str!("../src/hooks/langfuse/langfuse_handler.rs"),
        ),
        (
            "turn digest",
            include_str!("../src/hooks/turn_digest/turn_digest_handler.rs"),
        ),
    ] {
        for prohibited in [
            "common::git_state",
            "git_remote_url",
            "git_branch",
            "Command::new(\"git\")",
            "git remote",
            "resolve_vcs_info",
        ] {
            assert!(
                !source.contains(prohibited),
                "{name} must not contain Stop-time Git fallback {prohibited}"
            );
        }
    }
}

#[test]
fn resource_context_is_the_only_runtime_vcs_writer() {
    let resource = include_str!("../src/hooks/resource_context.rs")
        .split("#[cfg(test)]")
        .next()
        .unwrap();
    assert!(
        resource.contains("replace_vcs_snapshot(session, &snapshot)"),
        "resource context must own the session VCS write"
    );

    for (name, source) in [
        (
            "Stop",
            include_str!("../src/hooks/langfuse/langfuse_handler.rs"),
        ),
        (
            "realtime tool",
            include_str!("../src/hooks/langfuse/realtime.rs"),
        ),
        (
            "realtime generation",
            include_str!("../src/hooks/langfuse/realtime_generation.rs"),
        ),
        ("tool context", include_str!("../src/hooks/tool_context.rs")),
        (
            "subagent",
            include_str!("../src/hooks/subagent/subagent_handler.rs"),
        ),
        (
            "turn digest",
            include_str!("../src/hooks/turn_digest/turn_digest_handler.rs"),
        ),
    ] {
        let runtime = source.split("#[cfg(test)]").next().unwrap_or(source);
        for prohibited in ["replace_vcs_snapshot(", "set_vcs("] {
            assert!(
                !runtime.contains(prohibited),
                "{name} must be a read-only VCS consumer, found {prohibited}"
            );
        }
    }

    let state = include_str!("../src/hooks/langfuse/state.rs");
    for signature in [
        "checkpoint_turn_export_success",
        "checkpoint_turn_export_attempt",
        "commit_export_success",
    ] {
        let marker = format!("pub fn {signature}(");
        let start = state.find(&marker).expect("state helper");
        let end = state[start..]
            .find('{')
            .map(|offset| start + offset)
            .expect("state helper signature end");
        assert!(
            !state[start..end].contains("VcsInfo"),
            "{signature} must not accept VCS write ownership"
        );
    }
}

#[test]
fn every_resource_consumer_reads_the_session_snapshot() {
    for (name, source, expected) in [
        (
            "Stop",
            include_str!("../src/hooks/langfuse/langfuse_handler.rs"),
            "get_vcs_for_cwd_from_state",
        ),
        (
            "realtime tool",
            include_str!("../src/hooks/langfuse/realtime.rs"),
            "get_vcs_for_cwd_from_state",
        ),
        (
            "realtime generation",
            include_str!("../src/hooks/langfuse/realtime_generation.rs"),
            "get_vcs_for_cwd_from_state",
        ),
        (
            "tool context",
            include_str!("../src/hooks/tool_context.rs"),
            "get_vcs_for_cwd_from_session",
        ),
        (
            "subagent",
            include_str!("../src/hooks/subagent/subagent_handler.rs"),
            "get_vcs_for_cwd_from_session",
        ),
        (
            "turn digest",
            include_str!("../src/hooks/turn_digest/turn_digest_handler.rs"),
            "get_vcs_for_cwd_from_state",
        ),
    ] {
        assert!(
            source.contains(expected),
            "{name} must consume the cwd-validated session VCS snapshot"
        );
    }
}

#[test]
fn at_most_once_claim_follows_body_build_and_budget_check() {
    let source = include_str!("../src/hooks/langfuse/langfuse_handler.rs");
    let body = source
        .find("build_otel_export_request_for_turn_with_full_context(")
        .expect("turn body construction");
    let timeout = source[body..]
        .find("budget.http_timeout()")
        .map(|offset| body + offset)
        .expect("HTTP budget check");
    let claim = source[timeout..]
        .find("claim_at_most_once_turn(")
        .map(|offset| timeout + offset)
        .expect("at-most-once claim");
    let post = source[claim..]
        .find("post_otel_to_langfuse_once_with_context(")
        .map(|offset| claim + offset)
        .expect("turn POST");

    assert!(
        body < timeout && timeout < claim && claim < post,
        "at-most-once must build the body and confirm HTTP budget before checkpointing immediately before POST"
    );
}

#[test]
fn langfuse_lifecycle_has_one_transport_owner() {
    let transport = include_str!("../src/common/otel.rs");
    let callers = [
        include_str!("../src/hooks/langfuse/langfuse_handler.rs"),
        include_str!("../src/hooks/langfuse/realtime.rs"),
        include_str!("../src/hooks/langfuse/realtime_generation.rs"),
    ];
    let prepared_start = transport
        .find("impl PreparedLangfuseSend")
        .expect("prepared implementation");
    let prepared_end = transport[prepared_start..]
        .find("/// Prepare (but do not send)")
        .map(|offset| prepared_start + offset)
        .expect("prepared implementation end");
    let prepared = &transport[prepared_start..prepared_end];

    assert_eq!(transport.matches("event=langfuse_send_start").count(), 1);
    assert_eq!(transport.matches("event=langfuse_send_end").count(), 3);
    assert!(!prepared.contains("event=langfuse_send_"));
    for caller in callers {
        assert!(!caller.contains("event=langfuse_send_"));
        assert!(caller.contains("post_otel_to_langfuse_once_with_context"));
    }
}

#[test]
fn realtime_mutations_do_not_clone_the_session_envelope() {
    let source = include_str!("../src/hooks/langfuse/realtime_state.rs");
    let start = source
        .find("fn update_realtime<")
        .expect("realtime mutation helper");
    let end = source[start..]
        .find("pub fn contexts(")
        .map(|offset| start + offset)
        .expect("next realtime function");
    let helper = &source[start..end];

    assert!(
        !helper.contains("state.clone()"),
        "SessionStateStore already owns transaction atomicity; realtime mutation must be in place"
    );
}

#[test]
fn subagent_stop_scans_transcript_and_metadata_before_state_transaction() {
    let source = include_str!("../src/hooks/subagent/subagent_handler.rs");
    let start = source
        .find("fn handle_parent_stop(")
        .expect("parent Stop handler");
    let end = source[start..]
        .find("fn finalize_parent_stop(")
        .map(|offset| start + offset)
        .expect("next handler method");
    let handler = &source[start..end];
    let update = handler
        .find("store.update_session(|")
        .expect("registry state transaction");

    for operation in [
        "scan_transcript_for_agent_background(&transcript_path,",
        "read_subagent_meta(&subagent_transcript)",
    ] {
        let call = handler
            .find(operation)
            .expect("external filesystem operation");
        assert!(
            call < update,
            "{operation} must execute before the registry state transaction"
        );
    }
}

#[test]
fn subagent_parent_stop_batches_registry_finalization() {
    let source = include_str!("../src/hooks/subagent/subagent_handler.rs");
    let start = source
        .find("fn handle_parent_stop(")
        .expect("parent Stop handler");
    let end = source[start..]
        .find("fn build_and_upload_async_turn(")
        .map(|offset| start + offset)
        .expect("async export helper");
    let handler = &source[start..end];

    assert!(
        handler.contains("finalize_parent_stop("),
        "parent Stop must finalize successful exports and cleanup in one transaction"
    );
    assert!(
        !handler.contains("self.mark_agent_processed(")
            && !handler.contains("self.cleanup_agent_timing("),
        "parent Stop must not acquire one state transaction per agent"
    );
}

#[test]
fn async_subagent_export_failure_remains_retryable() {
    let source = include_str!("../src/hooks/subagent/subagent_handler.rs");
    let start = source
        .find("fn build_and_upload_async_turn(")
        .expect("async export helper");
    let end = source[start..]
        .find("impl HookHandler for SubagentHandler")
        .map(|offset| start + offset)
        .expect("handler implementation boundary");
    let export = &source[start..end];

    assert!(
        export.contains("failed_async_export(")
            && export.contains("accepted_turn_ids")
            && export.matches("post_otel_to_langfuse(").count() == 1,
        "HTTP failures must return partial progress so accepted turns remain checkpointable"
    );
    assert!(
        !export.contains("cleanup_agent_timing"),
        "export helper must not delete retry state before the caller records success"
    );
    assert!(
        export.contains("split_async_turn_body(")
            && export.contains("pack_async_fragments(")
            && export.contains("max_body_bytes"),
        "async subagent export must enforce bounded HTTP chunks"
    );
}

#[test]
fn subagent_async_export_uses_state_snapshot_captured_before_http() {
    let source = include_str!("../src/hooks/subagent/subagent_handler.rs");
    let export_start = source
        .find("fn build_and_upload_async_turn(")
        .expect("async export handler");
    let export_end = source[export_start..]
        .find("impl HookHandler for SubagentHandler")
        .map(|offset| export_start + offset)
        .expect("handler implementation boundary");
    let export = &source[export_start..export_end];

    assert!(
        !export.contains("SessionStateStore"),
        "blocking HTTP export must consume an owned state snapshot without reacquiring locks"
    );

    let parent_start = source
        .find("fn handle_parent_stop(")
        .expect("parent Stop handler");
    let parent_end = source[parent_start..]
        .find("fn finalize_parent_stop(")
        .map(|offset| parent_start + offset)
        .expect("parent Stop finalizer");
    let parent = &source[parent_start..parent_end];
    assert!(
        parent.contains("load_subagent_timing_for_agent_from_session")
            && parent.contains("get_vcs_for_cwd_from_session")
            && parent.contains("Err(StateTxnError::Busy { .. })"),
        "parent Stop must capture timing/VCS under the classification transaction and skip export on contention"
    );
}

#[test]
fn session_owned_handlers_use_session_state_store_transactions() {
    for (name, source) in [
        ("timing", include_str!("../src/hooks/timing.rs")),
        ("tool_context", include_str!("../src/hooks/tool_context.rs")),
        (
            "subagent",
            include_str!("../src/hooks/subagent/subagent_handler.rs"),
        ),
        (
            "turn_digest",
            include_str!("../src/hooks/turn_digest/turn_digest_handler.rs"),
        ),
        (
            "realtime",
            include_str!("../src/hooks/langfuse/realtime.rs"),
        ),
        (
            "realtime_generation",
            include_str!("../src/hooks/langfuse/realtime_generation.rs"),
        ),
    ] {
        let runtime_source = source.split("#[cfg(test)]").next().unwrap_or(source);
        if name == "tool_context" {
            assert!(
                runtime_source.contains("DomainStateStore")
                    && runtime_source.contains("StateDomain::Resource"),
                "tool_context must route VCS snapshots through the resource domain store"
            );
        } else {
            assert!(
                runtime_source.contains("SessionStateStore"),
                "{name} must route session-owned mutable state through SessionStateStore"
            );
        }
        for prohibited in ["StateLock::acquire", "load_state(", "save_state("] {
            assert!(
                !runtime_source.contains(prohibited),
                "{name} runtime path must not call {prohibited} directly"
            );
        }
    }
}

#[test]
fn dynamic_session_paths_use_the_collision_resistant_session_key() {
    let source = include_str!("../src/hooks/dynamic/turn_manager.rs");
    assert!(
        source.contains("session_state_key(session_id)"),
        "dynamic session artifacts must use the same collision-resistant session key"
    );
    assert!(
        !source.contains("data_root().join(session_id)"),
        "dynamic session artifacts must not use the raw session id as a path component"
    );
}

#[test]
fn runtime_state_path_is_resolved_only_after_session_id_parsing() {
    let source = include_str!("../src/main.rs");
    let session_parse = source
        .find("let session_id = common::hook::extract_session_id(&payload);")
        .expect("session id parsing");
    let state_resolution = source
        .find("SessionStateStore::for_session(&session_id")
        .expect("session state resolution");
    assert!(
        state_resolution > session_parse,
        "session state path must be resolved after session_id parsing"
    );
    assert!(
        !source.contains("join(\"cchooks_state.json\")"),
        "runtime hooks must not route mutable state to the legacy global file"
    );
}
