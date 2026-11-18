//! OTel LogRecord builders for dynamic data collection events.
//!
//! Constructs OTel LogRecords from session metadata and turn data,
//! used by dynamic handlers to upload state-capture events.

use super::transcript_parser::extract_last_assistant_response;
use crate::iso_to_nanos;
use crate::otlp::{OtelAttribute, OtelLogRecord};

const EVENT_NAME: &str = "dynamic_stop";

/// Build an OTel LogRecord for the dynamic.stop event.
///
/// `compress_fn` is a callback for compressing state strings (e.g., zstd+base64),
/// allowing the caller to supply its own compression implementation.
pub fn build_dynamic_stop_log_record(
    session_id: &str,
    username: &str,
    meta: &serde_json::Value,
    turn_data: &serde_json::Value,
    compress_fn: fn(&str) -> String,
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

    // username
    if !username.is_empty() {
        attrs.push(OtelAttribute::string("ant.username", username));
        attrs.push(OtelAttribute::string("user.id", username));
    }

    // transcript_path
    if let Some(tp) = meta.get("transcript_path").and_then(|v| v.as_str()) {
        if !tp.is_empty() {
            attrs.push(OtelAttribute::string("agentic.transcript.path", tp));
        }
    }

    // query + input messages
    if let Some(query) = turn_data.get("query").and_then(|v| v.as_str()) {
        attrs.push(OtelAttribute::string("agentic.turn.query", query));
        let input_messages = serde_json::json!([{"role": "user", "content": query}]);
        attrs.push(OtelAttribute::string(
            "gen_ai.input.messages",
            input_messages.to_string(),
        ));
    }

    // output messages from transcript
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

    // state: pre/post (compressed)
    if let Some(pre) = pre {
        attrs.push(OtelAttribute::string("agentic.state.pre", compress_fn(pre)));
    }
    if !unchanged {
        if let Some(post) = post {
            attrs.push(OtelAttribute::string(
                "agentic.state.post",
                compress_fn(post),
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
