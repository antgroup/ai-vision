use agentic_otel_common::iso_to_nanos;
use agentic_otel_common::otlp::{OtelAttribute, OtelLogRecord};

use crate::common::hook::{
    extract_session_id, extract_transcript_path, find_latest_transcript, wait_for_file_stable,
    HookEvent,
};
use crate::common::transcript::{build_turns, read_new_jsonl, Turn};
use crate::hooks::langfuse::state::{
    get_int_field, get_str_field, get_vcs_for_cwd_from_state, SessionStateStore, StateTxnError,
};
use crate::hooks::{CommonContext, HandlerError, HookHandler};

use super::upload;

const LOG_PREFIX: &str = "[turn_digest]";
const EVENT_NAME: &str = "dynamic.stop";

fn build_output_messages(turn: &Turn) -> String {
    let last = match turn.assistants.last() {
        Some(a) => a,
        None => return "[]".to_string(),
    };

    let mut msg = serde_json::Map::new();
    msg.insert("role".to_string(), serde_json::json!("assistant"));
    msg.insert("content".to_string(), serde_json::json!(last.text));

    // reasoning_content: merge thinking blocks (if any)
    if !last.thinking_blocks.is_empty() {
        let merged: String = last
            .thinking_blocks
            .iter()
            .filter_map(|b| b.get("text").and_then(|t| t.as_str()))
            .collect::<Vec<_>>()
            .join("\n");
        if !merged.is_empty() {
            msg.insert("reasoning_content".to_string(), serde_json::json!(merged));
        }
    }

    msg.insert(
        "finish_reason".to_string(),
        serde_json::json!(last.stop_reason),
    );

    serde_json::json!([msg]).to_string()
}

fn build_log_record(
    turn: &Turn,
    turn_number: usize,
    session_id: &str,
    username: &str,
    cwd: &str,
    remote_url: &str,
    branch: &str,
) -> OtelLogRecord {
    let time_nano = if let Some(last) = turn.assistants.last() {
        iso_to_nanos(&last.end_timestamp)
    } else {
        iso_to_nanos(&turn.user_timestamp)
    };

    let mut attrs = vec![
        OtelAttribute::string("event.name", EVENT_NAME),
        OtelAttribute::string("gen_ai.chat.id", session_id),
        OtelAttribute::int("agentic.turn.id", turn_number as i64),
        OtelAttribute::string("agentic.turn.query", &turn.user_text),
        OtelAttribute::string("agentic.runtime.cwd", cwd),
    ];

    if !remote_url.is_empty() {
        attrs.push(OtelAttribute::string("vcs.repository.url.full", remote_url));
    }
    if !branch.is_empty() {
        attrs.push(OtelAttribute::string("vcs.ref.head.name", branch));
    }

    if !username.is_empty() {
        attrs.push(OtelAttribute::string("ant.username", username));
    }

    // gen_ai.input.messages
    let input_messages = serde_json::json!([{"role": "user", "content": turn.user_text}]);
    attrs.push(OtelAttribute::string(
        "gen_ai.input.messages",
        input_messages.to_string(),
    ));

    // gen_ai.output.messages
    let output = build_output_messages(turn);
    attrs.push(OtelAttribute::string("gen_ai.output.messages", output));

    // gen_ai.request.model
    if let Some(last) = turn.assistants.last() {
        if !last.model.is_empty() {
            attrs.push(OtelAttribute::string("gen_ai.request.model", &last.model));
        }
    }

    OtelLogRecord::string_event(EVENT_NAME, &time_nano, attrs)
}

pub struct TurnDigestHandler;

impl TurnDigestHandler {
    pub fn new() -> Self {
        Self
    }
}

impl HookHandler for TurnDigestHandler {
    fn name(&self) -> &str {
        "turn_digest"
    }

    fn supported_events(&self) -> &[HookEvent] {
        &[HookEvent::Stop]
    }

    fn handle(
        &self,
        _event: &HookEvent,
        payload: &serde_json::Value,
        context: &CommonContext,
    ) -> Result<(), HandlerError> {
        if crate::common::otel::is_privacy_mode() {
            return Ok(());
        }

        if crate::hooks::active_stop_should_skip(payload) {
            return Ok(());
        }

        // Check if turn_digest endpoint is configured (compile-time)
        // prepare_log_records_send returns None if empty, but skip parsing work early
        #[cfg(not(feature = "aivision"))]
        {
            return Ok(());
        }

        #[cfg(feature = "aivision")]
        {
            self.handle_stop(payload, context)
        }
    }
}

#[cfg(feature = "aivision")]
impl TurnDigestHandler {
    fn handle_stop(
        &self,
        payload: &serde_json::Value,
        context: &CommonContext,
    ) -> Result<(), HandlerError> {
        let session_id = extract_session_id(payload);
        let transcript_path = extract_transcript_path(payload);

        let (session_id, transcript_path) = if !session_id.is_empty() && !transcript_path.is_empty()
        {
            (session_id, transcript_path)
        } else {
            match find_latest_transcript(&context.projects_dir) {
                Some(latest) => (latest.session_id, latest.transcript_path),
                None => return Ok(()),
            }
        };

        if session_id.is_empty() || transcript_path.is_empty() {
            return Ok(());
        }

        wait_for_file_stable(&transcript_path, 50, 500);

        let store = SessionStateStore::new(context.state_file.clone(), &session_id, 2000);
        let cwd = payload.get("cwd").and_then(|v| v.as_str()).unwrap_or("");
        let (prev_offset, prev_buffer, prev_turn_count, vcs_info) = match store.read(|state| {
            let digest_state = state
                .get(&session_id)
                .and_then(|session| session.get("turn_digest"));
            let prev_offset = digest_state
                .map(|s| get_int_field(s, "offset"))
                .unwrap_or(0);
            let prev_buffer = digest_state
                .map(|s| get_str_field(s, "buffer").to_string())
                .unwrap_or_default();
            let prev_turn_count = digest_state
                .map(|s| get_int_field(s, "turn_count") as usize)
                .unwrap_or(0);
            let vcs_info = get_vcs_for_cwd_from_state(state, &session_id, cwd);
            (prev_offset, prev_buffer, prev_turn_count, vcs_info)
        }) {
            Ok(state) => state,
            Err(StateTxnError::Busy { .. }) => {
                context
                    .logger
                    .warn("turn_digest: state lock unavailable; skipping digest update");
                return Ok(());
            }
            Err(error) => {
                context.logger.warn(&format!(
                    "turn_digest: failed to read digest state: {error}"
                ));
                return Ok(());
            }
        };

        let (new_messages, new_offset, new_buffer) = read_new_jsonl(
            std::path::Path::new(&transcript_path),
            prev_offset,
            &prev_buffer,
        );

        if new_messages.is_empty() {
            return Ok(());
        }

        let turns = build_turns(&new_messages, None);
        if turns.is_empty() {
            save_turn_digest_state(&store, context, new_offset, &new_buffer, prev_turn_count);
            return Ok(());
        }

        let remote_url = vcs_info
            .as_ref()
            .map(|vcs| vcs.remote_url.as_str())
            .unwrap_or("");
        let branch = vcs_info
            .as_ref()
            .map(|vcs| vcs.branch.as_str())
            .unwrap_or("");

        let records: Vec<OtelLogRecord> = turns
            .iter()
            .enumerate()
            .map(|(i, turn)| {
                build_log_record(
                    turn,
                    prev_turn_count + i + 1,
                    &session_id,
                    &context.username,
                    cwd,
                    remote_url,
                    branch,
                )
            })
            .collect();

        if let Some(task) = upload::prepare_log_records_send(&records, &context.logger) {
            let send_logger = context.logger.clone();
            context
                .send_queue
                .borrow_mut()
                .push(Box::new(move || task(&send_logger)));
        }

        let new_turn_count = prev_turn_count + turns.len();
        save_turn_digest_state(&store, context, new_offset, &new_buffer, new_turn_count);

        context.logger.info(&format!(
            "{} uploaded {} turns (total={})",
            LOG_PREFIX,
            turns.len(),
            new_turn_count
        ));

        Ok(())
    }
}

fn save_turn_digest_state(
    store: &SessionStateStore,
    context: &CommonContext,
    offset: i64,
    buffer: &str,
    turn_count: usize,
) {
    let result = store.update_session(|session| {
        let current = session.get("turn_digest");
        let current_offset = current
            .map(|state| get_int_field(state, "offset"))
            .unwrap_or(0);
        let current_turn_count = current
            .map(|state| get_int_field(state, "turn_count") as usize)
            .unwrap_or(0);
        if offset < current_offset || turn_count < current_turn_count {
            return Ok(false);
        }
        session["turn_digest"] = serde_json::json!({
            "offset": offset,
            "buffer": buffer,
            "turn_count": turn_count,
        });
        Ok(true)
    });

    match result {
        Ok(true) => {}
        Ok(false) => context
            .logger
            .debug("turn_digest: ignored stale digest checkpoint"),
        Err(e) => context
            .logger
            .warn(&format!("turn_digest: failed to save digest state: {e}")),
    }
}
