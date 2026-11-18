use serde_json::Value;
use sha2::{Digest, Sha256};

use super::types::Turn;

impl Turn {
    pub fn tool_use_ids(&self) -> Vec<&str> {
        let mut ids: Vec<&str> = self
            .assistants
            .iter()
            .flat_map(|assistant| assistant.tool_uses.iter().map(|tool| tool.id.as_str()))
            .collect();
        ids.extend(self.tool_results.iter().flat_map(|message| {
            message
                .items
                .iter()
                .map(|result| result.tool_use_id.as_str())
        }));
        ids
    }
}

/// Stable acknowledgement identity for one retained transcript-window turn.
/// Native Claude prompt UUIDs win; fixtures/legacy transcripts fall back to a
/// versioned length-prefixed SHA-256 digest that is stable across restarts.
pub fn stable_turn_id(turn: &Turn, session_id: &str, retained_ordinal: usize) -> String {
    if let Some(native) = turn.chat_id.as_deref().filter(|value| !value.is_empty()) {
        return native.to_string();
    }

    let first_assistant_id = turn
        .assistants
        .iter()
        .find_map(|assistant| {
            (!assistant.message_id.is_empty()).then_some(assistant.message_id.as_str())
        })
        .unwrap_or("");
    let retained_ordinal = retained_ordinal.to_string();
    let mut hasher = Sha256::new();
    for value in [
        "cchooks-turn-v1",
        session_id,
        &turn.user_timestamp,
        &turn.user_text,
        first_assistant_id,
        &retained_ordinal,
    ] {
        hasher.update((value.len() as u64).to_be_bytes());
        hasher.update(value.as_bytes());
    }
    let digest = hasher.finalize();
    let hex = digest
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect::<String>();
    format!("cchooks-turn-v1:{hex}")
}

/// Resolve one stable parent session identity for every transcript-backed entrypoint.
/// Native camelCase, snake_case, and nested hook shapes win. Legacy transcripts
/// without an ID use an opaque content digest so replay and report still agree.
pub fn resolve_transcript_session_id(lines: &[Value]) -> String {
    if let Some(session_id) = lines.iter().find_map(|line| {
        let session_id = crate::common::hook::extract_session_id(line);
        (!session_id.is_empty()).then_some(session_id)
    }) {
        return session_id;
    }

    let mut hasher = Sha256::new();
    hasher.update(b"cchooks.transcript-session.v1");
    for line in lines {
        let encoded = serde_json::to_vec(line).unwrap_or_default();
        hasher.update((encoded.len() as u64).to_be_bytes());
        hasher.update(encoded);
    }
    let hex = hasher
        .finalize()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect::<String>();
    format!("cc-transcript-{hex}")
}

#[cfg(test)]
mod tests {
    use super::super::turns::build_turns;
    use super::*;
    use serde_json::json;

    #[test]
    fn stable_turn_id_prefers_native_and_fallback_is_deterministic() {
        let native_lines = vec![
            json!({"type":"user","uuid":"native-turn","message":{"role":"user","content":"same"},"timestamp":"2026-07-13T00:00:00.000Z"}),
            json!({"type":"assistant","message":{"role":"assistant","id":"msg-1","content":[{"type":"text","text":"done"}],"stop_reason":"end_turn"},"timestamp":"2026-07-13T00:00:01.000Z"}),
        ];
        let native_turns = build_turns(&native_lines, None);
        assert_eq!(
            stable_turn_id(&native_turns[0], "session", 0),
            "native-turn"
        );

        let fallback_lines = vec![
            json!({"type":"user","message":{"role":"user","content":"same"},"timestamp":"2026-07-13T00:00:00.000Z"}),
            json!({"type":"assistant","message":{"role":"assistant","id":"msg-1","content":[{"type":"text","text":"done"}],"stop_reason":"end_turn"},"timestamp":"2026-07-13T00:00:01.000Z"}),
        ];
        let fallback_turns = build_turns(&fallback_lines, None);
        let first = stable_turn_id(&fallback_turns[0], "session", 0);
        let reread = stable_turn_id(&build_turns(&fallback_lines, None)[0], "session", 0);
        assert_eq!(first, reread);
        assert_ne!(first, stable_turn_id(&fallback_turns[0], "session", 1));
        assert!(first.starts_with("cchooks-turn-v1:"));
    }

    #[test]
    fn transcript_session_id_accepts_all_hook_shapes_and_has_stable_fallback() {
        for line in [
            serde_json::json!({"sessionId": "session-1"}),
            serde_json::json!({"session_id": "session-1"}),
            serde_json::json!({"session": {"id": "session-1"}}),
        ] {
            assert_eq!(resolve_transcript_session_id(&[line]), "session-1");
        }

        let missing = vec![serde_json::json!({"type": "user", "message": "hello"})];
        let first = resolve_transcript_session_id(&missing);
        assert_eq!(first, resolve_transcript_session_id(&missing));
        assert!(first.starts_with("cc-transcript-"));
        assert_ne!(
            first,
            resolve_transcript_session_id(&[
                serde_json::json!({"type": "user", "message": "other"})
            ])
        );
    }
}
