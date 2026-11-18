//! Dynamic data collection handler for Codex.
//!
//! Processes SessionStart, UserPromptSubmit, Stop, and SessionEnd events to collect
//! git state snapshots, manage turns, and upload session data.

use std::path::Path;

use agentic_otel_common::dynamic::git::{capture_state, get_base_commit};
use agentic_otel_common::dynamic::log_record::build_dynamic_stop_log_record;
use agentic_otel_common::dynamic::session;
use agentic_otel_common::dynamic::transcript_parser::extract_first_query;

use crate::common::compress::compress_and_encode;
use crate::common::git_state::{
    collect_git_repository, GitCollectionOutcome, RESOURCE_CONTEXT_GIT_TIMEOUT,
};
use crate::common::hook::HookEvent;
use crate::common::logger::now_iso8601_pub;
use crate::hooks::{CommonContext, HandlerError, HookHandler};

use super::upload;

const LOG_PREFIX: &str = "[dynamic]";

/// Dynamic data directory colocated with one resolved session state file.
fn data_root(context: &CommonContext) -> Result<std::path::PathBuf, HandlerError> {
    context
        .state_file
        .parent()
        .filter(|dir| !dir.as_os_str().is_empty())
        .map(|dir| dir.join("dynamic"))
        .ok_or_else(|| HandlerError::from(format!("{LOG_PREFIX} missing session state parent")))
}

/// Dynamic data collection handler.
pub struct DynamicDataHandler;

impl DynamicDataHandler {
    pub fn new() -> Self {
        Self
    }

    fn handle_session_start(
        &self,
        payload: &serde_json::Value,
        context: &CommonContext,
    ) -> Result<(), HandlerError> {
        let session_id = payload
            .get("session_id")
            .and_then(|v| v.as_str())
            .unwrap_or("");
        let cwd = payload.get("cwd").and_then(|v| v.as_str()).unwrap_or("");
        let transcript_path = payload
            .get("transcript_path")
            .and_then(|v| v.as_str())
            .unwrap_or("");

        if session_id.is_empty() || cwd.is_empty() {
            context.logger.debug(&format!(
                "{} SessionStart: missing session_id or cwd, skipping",
                LOG_PREFIX
            ));
            return Ok(());
        }

        let cwd_path = Path::new(cwd);
        let base_dir = data_root(context)?;

        let base_commit = match get_base_commit(cwd_path) {
            Some(bc) => bc,
            None => {
                context.logger.debug(&format!(
                    "{} SessionStart: no base_commit found, skipping",
                    LOG_PREFIX
                ));
                return Ok(());
            }
        };

        let GitCollectionOutcome::Captured(repository) =
            collect_git_repository(cwd_path, RESOURCE_CONTEXT_GIT_TIMEOUT)
        else {
            context.logger.debug(&format!(
                "{} SessionStart: repository metadata unavailable, skipping",
                LOG_PREFIX
            ));
            return Ok(());
        };
        let remote_url = repository.remote_url;
        let branch = repository.branch;

        if remote_url.is_empty() || branch.is_empty() {
            context.logger.debug(&format!(
                "{} SessionStart: missing remote_url or branch, skipping",
                LOG_PREFIX
            ));
            return Ok(());
        }

        let tp = if transcript_path.is_empty() {
            None
        } else {
            Some(transcript_path)
        };
        let now = now_iso8601_pub();
        session::init_session(
            &base_dir,
            session_id,
            cwd,
            tp,
            &remote_url,
            &branch,
            &base_commit,
            &now,
        );

        let capture = capture_state(cwd_path, &base_commit);
        if let Some(ref initial_state) = capture.state {
            session::write_initial_pre_state(&base_dir, session_id, initial_state);
        }

        context.logger.info(&format!(
            "{} SessionStart: base_commit={} cwd={}",
            LOG_PREFIX, base_commit, cwd
        ));

        Ok(())
    }

    fn handle_user_prompt_submit(
        &self,
        payload: &serde_json::Value,
        context: &CommonContext,
    ) -> Result<(), HandlerError> {
        let session_id = payload
            .get("session_id")
            .and_then(|v| v.as_str())
            .unwrap_or("");
        let cwd = payload.get("cwd").and_then(|v| v.as_str()).unwrap_or("");
        let query = payload.get("prompt").and_then(|v| v.as_str()).unwrap_or("");

        if session_id.is_empty() {
            return Ok(());
        }

        let base_dir = data_root(context)?;

        let session_state = match session::read_session_state(&base_dir, session_id) {
            Some(s) => s,
            None => return Ok(()),
        };

        let meta = match session::read_meta(&base_dir, session_id) {
            Some(m) => m,
            None => return Ok(()),
        };
        let base_commit = match meta
            .get("git")
            .and_then(|g| g.get("base_commit"))
            .and_then(|v| v.as_str())
        {
            Some(bc) => bc.to_string(),
            None => return Ok(()),
        };

        let cwd_path = Path::new(cwd);
        let capture = capture_state(cwd_path, &base_commit);

        let current_turn = session_state
            .get("current_turn")
            .and_then(|v| v.as_i64())
            .unwrap_or(0);
        let new_turn = current_turn + 1;
        let now = now_iso8601_pub();

        session::create_turn(
            &base_dir,
            session_id,
            new_turn,
            query,
            capture.state.as_deref(),
            capture.skip_reason.as_deref(),
            &now,
        );

        context.logger.info(&format!(
            "{} UserPromptSubmit: turn={}",
            LOG_PREFIX, new_turn
        ));

        Ok(())
    }

    fn handle_stop(
        &self,
        payload: &serde_json::Value,
        context: &CommonContext,
    ) -> Result<(), HandlerError> {
        if payload
            .get("stop_hook_active")
            .and_then(|v| v.as_bool())
            .unwrap_or(false)
        {
            context.logger.debug(&format!(
                "{} Stop: stop_hook_active=true, skipping",
                LOG_PREFIX
            ));
            return Ok(());
        }

        let session_id = payload
            .get("session_id")
            .and_then(|v| v.as_str())
            .unwrap_or("");
        let payload_cwd = payload.get("cwd").and_then(|v| v.as_str()).unwrap_or("");

        if session_id.is_empty() {
            return Ok(());
        }

        let base_dir = data_root(context)?;

        let session_state = match session::read_session_state(&base_dir, session_id) {
            Some(s) => s,
            None => return Ok(()),
        };

        let meta = match session::read_meta(&base_dir, session_id) {
            Some(m) => m,
            None => return Ok(()),
        };
        let cwd = if payload_cwd.is_empty() {
            meta.get("cwd")
                .and_then(|value| value.as_str())
                .unwrap_or("")
        } else {
            payload_cwd
        };
        let base_commit = match meta
            .get("git")
            .and_then(|g| g.get("base_commit"))
            .and_then(|v| v.as_str())
        {
            Some(bc) => bc.to_string(),
            None => return Ok(()),
        };

        let current_turn = session_state
            .get("current_turn")
            .and_then(|v| v.as_i64())
            .unwrap_or(0);

        let cwd_path = Path::new(cwd);

        if current_turn == 0 {
            // Plan Mode fallback
            let pre_state = match session::read_initial_pre_state(&base_dir, session_id) {
                Some(s) => s,
                None => {
                    context.logger.debug(&format!(
                        "{} Stop: Plan Mode but no initial_pre_state, skipping",
                        LOG_PREFIX
                    ));
                    return Ok(());
                }
            };

            let capture = capture_state(cwd_path, &base_commit);
            let transcript_path = meta
                .get("transcript_path")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let query = extract_first_query(transcript_path);
            let started_at = meta
                .get("started_at")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let now = now_iso8601_pub();

            session::create_synthetic_turn(
                &base_dir,
                session_id,
                started_at,
                &query,
                &pre_state,
                capture.state.as_deref(),
                capture.skip_reason.as_deref(),
                &now,
            );

            if let Some(turn) = session::read_turn(&base_dir, session_id, 1) {
                let record = build_dynamic_stop_log_record(
                    session_id,
                    &context.username,
                    &meta,
                    &turn,
                    compress_and_encode,
                );
                upload::post_log_record(&record, &context.logger);
                session::delete_turn(&base_dir, session_id, 1);
            }

            context
                .logger
                .info(&format!("{} Stop: Plan Mode synthetic turn", LOG_PREFIX));
        } else {
            // Normal path
            let capture = capture_state(cwd_path, &base_commit);
            let now = now_iso8601_pub();
            session::update_turn_post_state(
                &base_dir,
                session_id,
                current_turn,
                capture.state.as_deref(),
                capture.skip_reason.as_deref(),
                &now,
            );

            if let Some(turn) = session::read_turn(&base_dir, session_id, current_turn) {
                let record = build_dynamic_stop_log_record(
                    session_id,
                    &context.username,
                    &meta,
                    &turn,
                    compress_and_encode,
                );
                upload::post_log_record(&record, &context.logger);
                session::delete_turn(&base_dir, session_id, current_turn);
            }

            context.logger.info(&format!(
                "{} Stop: turn {} post_state captured",
                LOG_PREFIX, current_turn
            ));
        }

        Ok(())
    }
}

impl HookHandler for DynamicDataHandler {
    fn name(&self) -> &str {
        "dynamic"
    }

    fn supported_events(&self) -> &[HookEvent] {
        &[
            HookEvent::SessionStart,
            HookEvent::UserPromptSubmit,
            HookEvent::Stop,
            HookEvent::SessionEnd,
        ]
    }

    fn handle(
        &self,
        event: &HookEvent,
        payload: &serde_json::Value,
        context: &CommonContext,
    ) -> Result<(), HandlerError> {
        if crate::common::otel::is_privacy_mode() {
            return Ok(());
        }
        match event {
            HookEvent::SessionStart => self.handle_session_start(payload, context),
            HookEvent::UserPromptSubmit => self.handle_user_prompt_submit(payload, context),
            HookEvent::Stop | HookEvent::SessionEnd => self.handle_stop(payload, context),
            _ => Ok(()),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn test_context(state_file: std::path::PathBuf, log_path: std::path::PathBuf) -> CommonContext {
        CommonContext {
            logger: crate::common::logger::Logger::new(log_path, true),
            state_file,
            langfuse_config: None,
            username: "tester".to_string(),
            sessions_dir: String::new(),
            hook_version: "test-version".to_string(),
            stop_budget: None,
            retry_policy: crate::hooks::RetryPolicy::AtLeastOnce,
            export_mode: crate::hooks::RealtimeExportMode::RealtimeWithStopFinalizer,
            realtime_llm_batch_size: crate::hooks::DEFAULT_REALTIME_LLM_EXPORT_BATCH_SIZE,
            metrics: None,
        }
    }

    #[test]
    fn test_data_root_requires_session_state_parent() {
        let context = test_context(
            std::path::PathBuf::from("state.json"),
            std::path::PathBuf::from("dynamic.log"),
        );
        assert!(data_root(&context).is_err());
    }

    #[test]
    fn test_session_end_flushes_and_cleans_current_turn() {
        let _guard = crate::test_sync::env_lock();
        let saved_state_dir = std::env::var("CDXHOOKS_STATE_DIR").ok();
        let saved_collection_mode = std::env::var("CDXHOOKS_COLLECTION_MODE").ok();
        let saved_upload_url = std::env::var("CDXHOOKS_DYNAMIC_UPLOAD_URL").ok();

        let dir = tempfile::tempdir().unwrap();
        std::env::set_var("CDXHOOKS_STATE_DIR", dir.path());
        std::env::set_var("CDXHOOKS_COLLECTION_MODE", "full");
        std::env::remove_var("CDXHOOKS_DYNAMIC_UPLOAD_URL");

        let session_id = "session-end-dynamic";
        let log_path = dir.path().join("dynamic.log");
        let context = test_context(
            dir.path().join("sessions/session-key/state.json"),
            log_path.clone(),
        );
        let base_dir = data_root(&context).unwrap();
        session::init_session(
            &base_dir,
            session_id,
            dir.path().to_str().unwrap(),
            None,
            "https://example.invalid/repo.git",
            "main",
            "deadbeef",
            "2026-07-13T00:00:00Z",
        );
        session::create_turn(
            &base_dir,
            session_id,
            1,
            "finish the task",
            Some("pre-state"),
            None,
            "2026-07-13T00:00:01Z",
        );
        let turn_path = session::turn_path(&base_dir, session_id, 1);
        assert!(turn_path.exists());

        let payload = serde_json::json!({
            "hook_event_name": "SessionEnd",
            "session_id": session_id,
        });
        let handler = DynamicDataHandler::new();

        let supported = handler.supported_events().contains(&HookEvent::SessionEnd);
        let result = handler.handle(&HookEvent::SessionEnd, &payload, &context);
        let turn_was_cleaned = !turn_path.exists();
        let log = std::fs::read_to_string(log_path).unwrap_or_default();

        match saved_state_dir {
            Some(value) => std::env::set_var("CDXHOOKS_STATE_DIR", value),
            None => std::env::remove_var("CDXHOOKS_STATE_DIR"),
        }
        match saved_collection_mode {
            Some(value) => std::env::set_var("CDXHOOKS_COLLECTION_MODE", value),
            None => std::env::remove_var("CDXHOOKS_COLLECTION_MODE"),
        }
        match saved_upload_url {
            Some(value) => std::env::set_var("CDXHOOKS_DYNAMIC_UPLOAD_URL", value),
            None => std::env::remove_var("CDXHOOKS_DYNAMIC_UPLOAD_URL"),
        }

        result.unwrap();
        assert!(
            supported,
            "SessionEnd must be routed to the dynamic handler"
        );
        assert!(turn_was_cleaned, "SessionEnd must flush the pending turn");
        assert!(
            log.contains("turn 1 post_state captured"),
            "SessionEnd must execute the same dynamic finalization path as Stop"
        );
    }
}
