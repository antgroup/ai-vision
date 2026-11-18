use std::path::Path;

use agentic_otel_common::iso_to_nanos;
use agentic_otel_common::otlp::{OtelAttribute, OtelLogRecord};

use crate::common::compress::compress_and_encode;
use crate::common::hook::HookEvent;
use crate::hooks::{CommonContext, HandlerError, HookHandler};

use super::git_state::{capture_state, get_base_commit, git_branch, git_remote_url};
use super::state::DYNAMIC_LOG_PREFIX;
use super::transcript_parser::{extract_first_query, extract_last_assistant_response};
use super::turn_manager::{
    create_synthetic_turn, create_turn, delete_turn, init_session, read_initial_pre_state,
    read_meta, read_session_state, read_turn, update_turn_post_state, write_initial_pre_state,
};
use super::upload;

const EVENT_NAME: &str = "dynamic_stop";

/// Build an OTel LogRecord for the dynamic.stop event.
fn build_stop_log_record(
    session_id: &str,
    username: &str,
    meta: &serde_json::Value,
    turn_data: &serde_json::Value,
) -> OtelLogRecord {
    let git = meta.get("git");
    let time_nano = iso_to_nanos(
        turn_data
            .get("ended_at")
            .and_then(|v| v.as_str())
            .unwrap_or(""),
    );

    let pre = turn_data.get("pre_state").and_then(|v| v.as_str());
    let post = turn_data.get("post_state").and_then(|v| v.as_str());
    let unchanged = pre.is_some() && post.is_some() && pre == post;
    let turn_id = turn_data
        .get("turn_id")
        .and_then(|v| v.as_i64())
        .unwrap_or(0);

    let mut attrs = vec![
        OtelAttribute::string("event.name", EVENT_NAME),
        OtelAttribute::string("gen_ai.chat.id", session_id),
        OtelAttribute::int("agentic.turn.id", turn_id),
        OtelAttribute::string(
            "agentic.turn.started_at",
            turn_data
                .get("started_at")
                .and_then(|v| v.as_str())
                .unwrap_or(""),
        ),
        OtelAttribute::string(
            "agentic.turn.ended_at",
            turn_data
                .get("ended_at")
                .and_then(|v| v.as_str())
                .unwrap_or(""),
        ),
        // VCS
        OtelAttribute::string(
            "vcs.repository.url.full",
            git.and_then(|g| g.get("remote_url"))
                .and_then(|v| v.as_str())
                .unwrap_or(""),
        ),
        OtelAttribute::string(
            "vcs.ref.head.name",
            git.and_then(|g| g.get("branch"))
                .and_then(|v| v.as_str())
                .unwrap_or(""),
        ),
        OtelAttribute::string(
            "vcs.ref.head.revision",
            git.and_then(|g| g.get("base_commit"))
                .and_then(|v| v.as_str())
                .unwrap_or(""),
        ),
        // runtime
        OtelAttribute::string(
            "agentic.runtime.cwd",
            meta.get("cwd").and_then(|v| v.as_str()).unwrap_or(""),
        ),
        // state
        OtelAttribute::bool("agentic.state.post_unchanged", unchanged),
    ];

    // username (skip if empty/unknown)
    if !username.is_empty() {
        attrs.push(OtelAttribute::string("ant.username", username));
        attrs.push(OtelAttribute::string("user.id", username));
    }

    // Optional: transcript_path
    if let Some(tp) = meta.get("transcript_path").and_then(|v| v.as_str()) {
        if !tp.is_empty() {
            attrs.push(OtelAttribute::string("agentic.transcript.path", tp));
        }
    }

    // query + input/output messages
    if let Some(query) = turn_data.get("query").and_then(|v| v.as_str()) {
        attrs.push(OtelAttribute::string("agentic.turn.query", query));
        let input_messages = serde_json::json!([{"role": "user", "content": query}]);
        attrs.push(OtelAttribute::string(
            "gen_ai.input.messages",
            input_messages.to_string(),
        ));
    }

    // output.messages: extract last assistant response from transcript
    let transcript_path = meta
        .get("transcript_path")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    if !transcript_path.is_empty() {
        let response = extract_last_assistant_response(transcript_path);
        if !response.is_empty() {
            let output_messages = serde_json::json!([{"role": "assistant", "content": response}]);
            attrs.push(OtelAttribute::string(
                "gen_ai.output.messages",
                output_messages.to_string(),
            ));
        }
    }

    // state: pre/post (zstd+base64)
    if let Some(pre) = pre {
        attrs.push(OtelAttribute::string(
            "agentic.state.pre",
            compress_and_encode(pre),
        ));
    }
    if !unchanged {
        if let Some(post) = post {
            attrs.push(OtelAttribute::string(
                "agentic.state.post",
                compress_and_encode(post),
            ));
        }
    }

    // skip_reason
    if let Some(skip) = turn_data.get("skip_reason").and_then(|v| v.as_str()) {
        attrs.push(OtelAttribute::string("agentic.state.skip_reason", skip));
    }

    // synthetic turn marker
    if turn_data
        .get("synthetic")
        .and_then(|v| v.as_bool())
        .unwrap_or(false)
    {
        attrs.push(OtelAttribute::bool("agentic.turn.synthetic", true));
    }

    OtelLogRecord::string_event(EVENT_NAME, &time_nano, attrs)
}

/// Dynamic data collection handler.
///
/// Processes three hook events (SessionStart, UserPromptSubmit, Stop)
/// to collect git state snapshots, manage turns, and upload session data.
pub struct DynamicDataHandler;

impl DynamicDataHandler {
    pub fn new() -> Self {
        Self
    }

    /// Handle SessionStart: find base_commit, init session, capture initial pre_state
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
                DYNAMIC_LOG_PREFIX
            ));
            return Ok(());
        }

        let cwd_path = Path::new(cwd);

        // Find base commit
        let base_commit = match get_base_commit(cwd_path) {
            Some(bc) => bc,
            None => {
                context.logger.debug(&format!(
                    "{} SessionStart: no base_commit found, skipping",
                    DYNAMIC_LOG_PREFIX
                ));
                return Ok(());
            }
        };

        // Get git info
        let remote_url = git_remote_url(cwd_path).unwrap_or_default();
        let branch = git_branch(cwd_path).unwrap_or_default();

        if remote_url.is_empty() || branch.is_empty() {
            context.logger.debug(&format!(
                "{} SessionStart: missing remote_url or branch, skipping",
                DYNAMIC_LOG_PREFIX
            ));
            return Ok(());
        }

        // Initialize session
        let tp = if transcript_path.is_empty() {
            None
        } else {
            Some(transcript_path)
        };
        init_session(session_id, cwd, tp, &remote_url, &branch, &base_commit);

        // Capture initial pre_state for Plan Mode fallback
        let capture = capture_state(cwd_path, &base_commit);
        if let Some(ref initial_state) = capture.state {
            write_initial_pre_state(session_id, initial_state);
        }

        context.logger.info(&format!(
            "{} SessionStart: base_commit={} cwd={}",
            DYNAMIC_LOG_PREFIX, base_commit, cwd
        ));

        Ok(())
    }

    /// Handle UserPromptSubmit: capture pre_state, create turn record
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

        // Read session state
        let session_state = match read_session_state(session_id) {
            Some(s) => s,
            None => return Ok(()),
        };

        // Read meta to get base_commit
        let meta = match read_meta(session_id) {
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

        create_turn(
            session_id,
            new_turn,
            query,
            capture.state.as_deref(),
            capture.skip_reason.as_deref(),
        );

        context.logger.info(&format!(
            "{} UserPromptSubmit: turn={}",
            DYNAMIC_LOG_PREFIX, new_turn
        ));

        Ok(())
    }

    /// Handle Stop: capture post_state, handle Plan Mode fallback
    fn handle_stop(
        &self,
        payload: &serde_json::Value,
        context: &CommonContext,
    ) -> Result<(), HandlerError> {
        // Guard against infinite loop
        if crate::hooks::active_stop_should_skip(payload) {
            context.logger.debug(&format!(
                "{} Stop: stop_hook_active=true, skipping",
                DYNAMIC_LOG_PREFIX
            ));
            return Ok(());
        }

        let session_id = payload
            .get("session_id")
            .and_then(|v| v.as_str())
            .unwrap_or("");
        let cwd = payload.get("cwd").and_then(|v| v.as_str()).unwrap_or("");

        if session_id.is_empty() {
            return Ok(());
        }

        let session_state = match read_session_state(session_id) {
            Some(s) => s,
            None => return Ok(()),
        };

        let meta = match read_meta(session_id) {
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

        let current_turn = session_state
            .get("current_turn")
            .and_then(|v| v.as_i64())
            .unwrap_or(0);

        let cwd_path = Path::new(cwd);

        if current_turn == 0 {
            // Plan Mode fallback: UserPromptSubmit was never triggered
            let pre_state = match read_initial_pre_state(session_id) {
                Some(s) => s,
                None => {
                    context.logger.debug(&format!(
                        "{} Stop: Plan Mode but no initial_pre_state, skipping",
                        DYNAMIC_LOG_PREFIX
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

            create_synthetic_turn(
                session_id,
                started_at,
                &query,
                &pre_state,
                capture.state.as_deref(),
                capture.skip_reason.as_deref(),
            );

            if let Some(turn) = read_turn(session_id, 1) {
                let record = build_stop_log_record(session_id, &context.username, &meta, &turn);
                if let Some(task) = upload::prepare_log_record_send(&record, &context.logger) {
                    let send_logger = context.logger.clone();
                    context
                        .send_queue
                        .borrow_mut()
                        .push(Box::new(move || task(&send_logger)));
                }
                delete_turn(session_id, 1);
            }

            context.logger.info(&format!(
                "{} Stop: Plan Mode synthetic turn created",
                DYNAMIC_LOG_PREFIX
            ));
        } else {
            // Normal path: update existing turn with post_state
            let capture = capture_state(cwd_path, &base_commit);
            update_turn_post_state(
                session_id,
                current_turn,
                capture.state.as_deref(),
                capture.skip_reason.as_deref(),
            );

            if let Some(turn) = read_turn(session_id, current_turn) {
                let record = build_stop_log_record(session_id, &context.username, &meta, &turn);
                if let Some(task) = upload::prepare_log_record_send(&record, &context.logger) {
                    let send_logger = context.logger.clone();
                    context
                        .send_queue
                        .borrow_mut()
                        .push(Box::new(move || task(&send_logger)));
                }
                delete_turn(session_id, current_turn);
            }

            context.logger.info(&format!(
                "{} Stop: turn {} post_state captured",
                DYNAMIC_LOG_PREFIX, current_turn
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
            HookEvent::Stop => self.handle_stop(payload, context),
            _ => Ok(()),
        }
    }
}
