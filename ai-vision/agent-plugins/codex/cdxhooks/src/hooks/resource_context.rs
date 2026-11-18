use serde_json::Value;

use crate::common::git_state::{
    collect_git_repository, GitCollectionOutcome, RESOURCE_CONTEXT_GIT_TIMEOUT,
};
use crate::common::hook::{extract_session_id, HookEvent};
use crate::common::logger::now_iso8601_pub;
use crate::hooks::langfuse::realtime_state;
use crate::hooks::langfuse::state::{
    clear_vcs_in_state, get_vcs_from_state, record_turn_billing_mode, rotate_current_trace_context,
    set_vcs_in_state, update_state_transaction_with_timeout, VcsInfo,
    FOREGROUND_STATE_LOCK_TIMEOUT_MS,
};
use crate::hooks::{CommonContext, HandlerError, HookHandler};

#[derive(Default)]
pub struct ResourceContextHandler;

impl ResourceContextHandler {
    pub fn new() -> Self {
        Self
    }

    fn handle_user_prompt_submit(
        &self,
        payload: &Value,
        context: &CommonContext,
    ) -> Result<(), HandlerError> {
        let session_id = extract_session_id(payload);
        if session_id.is_empty() {
            return Ok(());
        }

        let Some(cwd) = payload
            .get("cwd")
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|cwd| !cwd.is_empty())
            .map(str::to_string)
        else {
            return Ok(());
        };
        let repository =
            collect_git_repository(std::path::Path::new(&cwd), RESOURCE_CONTEXT_GIT_TIMEOUT);
        let captured_at = now_iso8601_pub();
        let turn_id = payload.get("turn_id").and_then(Value::as_str);
        let billing_mode = crate::common::env::var("CDXHOOKS_BILLING_MODE").ok();
        let mut state = serde_json::json!({});
        let update_result = update_state_transaction_with_timeout(
            &context.state_file,
            &mut state,
            FOREGROUND_STATE_LOCK_TIMEOUT_MS,
            |candidate| {
                if let (Some(turn_id), Some(mode)) = (turn_id, billing_mode.as_deref()) {
                    record_turn_billing_mode(candidate, &session_id, turn_id, mode);
                }
                rotate_current_trace_context(candidate, &session_id);
                if context.export_mode.realtime_enabled() {
                    realtime_state::rotate_current_in_state(candidate, &session_id);
                }
                apply_repository_outcome(candidate, &session_id, &cwd, &repository, &captured_at);
            },
        );
        if let Err(error) = update_result {
            context.logger.warn(&format!(
                "ResourceContext: state busy during UserPromptSubmit; continuing without persistence: {error}"
            ));
            return Ok(());
        }

        match repository {
            GitCollectionOutcome::Captured(ref snapshot) => context.logger.debug(&format!(
                "ResourceContext: captured repository remote={} match={} branch_present={}",
                snapshot.remote_name,
                snapshot
                    .selected_remote_match
                    .map(|value| value.as_str())
                    .unwrap_or("none"),
                !snapshot.branch.is_empty()
            )),
            GitCollectionOutcome::NotRepositoryOrNoMetadata => context
                .logger
                .debug("ResourceContext: no repository metadata; cleared cached VCS"),
            GitCollectionOutcome::Unavailable => context
                .logger
                .debug("ResourceContext: repository metadata temporarily unavailable"),
        }
        Ok(())
    }
}

impl HookHandler for ResourceContextHandler {
    fn name(&self) -> &str {
        "resource_context"
    }

    fn supported_events(&self) -> &[HookEvent] {
        &[HookEvent::UserPromptSubmit]
    }

    fn handle(
        &self,
        event: &HookEvent,
        payload: &Value,
        context: &CommonContext,
    ) -> Result<(), HandlerError> {
        match event {
            HookEvent::UserPromptSubmit => self.handle_user_prompt_submit(payload, context),
            _ => Ok(()),
        }
    }
}

fn apply_repository_outcome(
    state: &mut Value,
    session_id: &str,
    cwd: &str,
    outcome: &GitCollectionOutcome,
    captured_at: &str,
) {
    match outcome {
        GitCollectionOutcome::Captured(snapshot) => {
            let vcs = VcsInfo {
                remote_url: snapshot.remote_url.clone(),
                branch: snapshot.branch.clone(),
                cwd: cwd.to_string(),
            };
            set_vcs_in_state(
                state,
                session_id,
                &vcs,
                Some(&snapshot.remote_name),
                snapshot.selected_remote_match.map(|value| value.as_str()),
                captured_at,
            );
        }
        GitCollectionOutcome::NotRepositoryOrNoMetadata => {
            clear_vcs_in_state(state, session_id);
        }
        GitCollectionOutcome::Unavailable
            if get_vcs_from_state(state, session_id).is_some_and(|cached| cached.cwd != cwd) =>
        {
            clear_vcs_in_state(state, session_id);
        }
        GitCollectionOutcome::Unavailable => {}
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::common::git_state::{GitRemoteMatch, GitRepositorySnapshot};
    use crate::hooks::langfuse::state::{get_vcs_from_state, load_state, save_state, StateLock};
    use serde_json::json;

    fn test_context(state_file: std::path::PathBuf) -> CommonContext {
        CommonContext {
            logger: crate::common::logger::Logger::new(state_file.with_extension("log"), false),
            state_file,
            langfuse_config: None,
            username: "tester".to_string(),
            sessions_dir: String::new(),
            hook_version: "test".to_string(),
            stop_budget: None,
            retry_policy: crate::hooks::RetryPolicy::AtLeastOnce,
            export_mode: crate::hooks::RealtimeExportMode::RealtimeWithStopFinalizer,
            realtime_llm_batch_size: crate::hooks::DEFAULT_REALTIME_LLM_EXPORT_BATCH_SIZE,
            metrics: None,
        }
    }

    #[test]
    fn apply_outcome_keeps_same_cwd_on_unavailable_and_clears_changed_cwd() {
        let mut state = json!({
            "session": {
                "vcs": {
                    "remote_url": "ssh://old/repo.git",
                    "branch": "main",
                    "cwd": "/old"
                }
            }
        });
        apply_repository_outcome(
            &mut state,
            "session",
            "/old",
            &GitCollectionOutcome::Unavailable,
            "now",
        );
        assert!(get_vcs_from_state(&state, "session").is_some());

        apply_repository_outcome(
            &mut state,
            "session",
            "/new",
            &GitCollectionOutcome::Unavailable,
            "now",
        );
        assert!(get_vcs_from_state(&state, "session").is_none());
    }

    #[test]
    fn apply_outcome_persists_compatible_vcs_and_metadata() {
        let mut state = json!({});
        apply_repository_outcome(
            &mut state,
            "session",
            "/repo",
            &GitCollectionOutcome::Captured(GitRepositorySnapshot {
                remote_name: "corp".to_string(),
                remote_url: "ssh://git@git.example/repo.git".to_string(),
                branch: "feature".to_string(),
                selected_remote_match: Some(GitRemoteMatch::Fallback),
            }),
            "2026-07-30T00:00:00Z",
        );
        let vcs = get_vcs_from_state(&state, "session").unwrap();
        assert_eq!(vcs.cwd, "/repo");
        assert_eq!(vcs.branch, "feature");
        assert_eq!(state["session"]["vcs"]["remote_name"], "corp");
        assert_eq!(state["session"]["vcs"]["selected_remote_match"], "fallback");
    }

    #[test]
    fn user_prompt_submit_rotates_trace_and_captures_repository() {
        let dir = tempfile::tempdir().unwrap();
        let repo = dir.path().join("repo");
        std::fs::create_dir_all(&repo).unwrap();
        assert!(crate::common::git_state::run_test_git(&repo, &["init"]));
        assert!(crate::common::git_state::run_test_git(
            &repo,
            &["remote", "add", "upstream", "ssh://example/repo.git"]
        ));
        let state_file = dir.path().join("state/state.json");
        save_state(
            &state_file,
            &json!({
                "session": {
                    "current_trace_context": {
                        "trace_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                        "traceparent": "00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-01"
                    }
                }
            }),
        )
        .unwrap();
        let context = test_context(state_file.clone());
        ResourceContextHandler::new()
            .handle(
                &HookEvent::UserPromptSubmit,
                &json!({"session_id": "session", "cwd": repo}),
                &context,
            )
            .unwrap();

        let state = load_state(&state_file);
        assert!(state["session"].get("current_trace_context").is_none());
        assert_eq!(
            state["session"]["trace_context_history"]
                .as_array()
                .unwrap()
                .len(),
            1
        );
        assert_eq!(
            get_vcs_from_state(&state, "session").unwrap().remote_url,
            "ssh://example/repo.git"
        );
    }

    #[test]
    fn user_prompt_submit_fails_open_when_state_is_busy() {
        let dir = tempfile::tempdir().unwrap();
        let state_file = dir.path().join("state/state.json");
        let context = test_context(state_file.clone());
        let _lock = StateLock::acquire(&state_file, 100);
        let started = std::time::Instant::now();
        let result = ResourceContextHandler::new().handle(
            &HookEvent::UserPromptSubmit,
            &json!({"session_id": "session", "cwd": dir.path()}),
            &context,
        );
        assert!(result.is_ok());
        assert!(
            started.elapsed() < std::time::Duration::from_millis(700),
            "two bounded Git probes plus the foreground lock timeout must fail open"
        );
        assert!(!state_file.exists());
    }

    #[test]
    fn user_prompt_submit_records_only_valid_billing_for_its_exact_turn() {
        use crate::hooks::langfuse::state::turn_billing_mode;
        let dir = tempfile::tempdir().unwrap();
        let state_file = dir.path().join("state/state.json");
        let context = test_context(state_file.clone());
        for (turn, mode) in [
            ("plan", Some("subscription")),
            ("metered", Some("pay_as_you_go")),
            ("plan", Some("pay_as_you_go")),
            ("invalid", Some("chatgpt")),
            ("unspecified", None),
        ] {
            let env = mode
                .into_iter()
                .map(|mode| ("CDXHOOKS_BILLING_MODE".to_string(), mode.to_string()))
                .collect();
            crate::common::env::with_worker_env(&env, || {
                ResourceContextHandler::new()
                    .handle(
                        &HookEvent::UserPromptSubmit,
                        &json!({"session_id": "session", "cwd": dir.path(), "turn_id": turn}),
                        &context,
                    )
                    .unwrap();
            });
        }
        let state = load_state(&state_file);
        assert_eq!(
            turn_billing_mode(&state, "session", "plan"),
            Some("subscription")
        );
        assert_eq!(
            turn_billing_mode(&state, "session", "metered"),
            Some("pay_as_you_go")
        );
        assert!(turn_billing_mode(&state, "session", "invalid").is_none());
        assert!(turn_billing_mode(&state, "session", "unspecified").is_none());
        assert!(turn_billing_mode(&state, "different-session", "plan").is_none());
    }

    #[test]
    fn user_prompt_submit_without_session_or_cwd_does_not_write_state() {
        let dir = tempfile::tempdir().unwrap();

        for (name, payload) in [
            ("missing-session", json!({"cwd": dir.path()})),
            ("missing-cwd", json!({"session_id": "session"})),
            ("blank-cwd", json!({"session_id": "session", "cwd": "   "})),
        ] {
            let state_file = dir.path().join(name).join("state.json");
            let context = test_context(state_file.clone());
            ResourceContextHandler::new()
                .handle(&HookEvent::UserPromptSubmit, &payload, &context)
                .unwrap();
            assert!(
                !state_file.exists(),
                "{name} must not create or mutate session state"
            );
        }
    }
}
