use serde_json::Value;
use sha2::{Digest, Sha256};

use crate::common::skill_keywords::SkillKeywordConfig;

use super::fields::{
    build_assistant_from_merged, build_terminal_llm_error, extract_skill_name_from_meta,
    extract_text, get_content_value, get_message_id, get_model, get_obj_str, get_prompt_chat_id,
    get_role, get_timestamp, is_stop_hook_summary, is_tool_result,
    is_user_interruption_system_message, merge_assistant_messages, parse_tool_result_message,
};
use super::task::parse_task_notification;
use super::types::{
    ApiError, AssistantMessage, CompactionEvent, TaskNotification, ToolResultMessage, Turn,
    TurnBuildResult, TurnTrigger,
};

pub fn build_turns(
    parsed_lines: &[Value],
    skill_kw_config: Option<&SkillKeywordConfig>,
) -> Vec<Turn> {
    build_turns_detailed(parsed_lines, skill_kw_config).turns
}

pub fn build_turns_detailed(
    parsed_lines: &[Value],
    _skill_kw_config: Option<&SkillKeywordConfig>,
) -> TurnBuildResult {
    let mut turns: Vec<Turn> = Vec::new();
    let mut unattached_compactions: Vec<CompactionEvent> = Vec::new();

    let mut current_user_ts = String::new();
    let mut current_user_content = Value::Null;
    let mut current_user_text: Option<String> = None;
    let mut current_chat_id: Option<String> = None;
    let mut current_trigger = TurnTrigger::Unknown;
    let mut current_tool_results: Vec<ToolResultMessage> = Vec::new();

    // Preserve insertion order while merging same-id assistant messages
    let mut assistant_order: Vec<String> = Vec::new();
    let mut assistant_merged: std::collections::HashMap<String, Value> =
        std::collections::HashMap::new();

    // Track skill name extracted from isMeta user messages for the current turn
    let mut current_skill_name: Option<String> = None;
    // Whether skill_name was set by an isMeta message (vs keyword detection).
    // Prevents sub-skill isMeta from overwriting the entry-point skill name,
    // while still allowing isMeta to override keyword-detected names.
    let mut skill_name_from_ismeta = false;

    let flush_turn = |turns: &mut Vec<Turn>,
                      user_ts: &str,
                      user_text: &Option<String>,
                      user_content: &Value,
                      order: &[String],
                      merged: &std::collections::HashMap<String, Value>,
                      tool_results: &[ToolResultMessage],
                      chat_id: &Option<String>,
                      trigger: TurnTrigger,
                      skill_name: &Option<String>,
                      api_errors: &[ApiError],
                      compactions: &[CompactionEvent],
                      cancelled: bool|
     -> bool {
        let Some(user_text) = user_text else {
            return false;
        };
        if merged.is_empty() {
            return false;
        }
        let parsed_assistants: Vec<AssistantMessage> = order
            .iter()
            .filter_map(|key| merged.get(key))
            .map(build_assistant_from_merged)
            .collect();
        let last_synthetic_index = parsed_assistants
            .last()
            .filter(|assistant| assistant.is_synthetic)
            .map(|_| parsed_assistants.len().saturating_sub(1));
        let assistants: Vec<AssistantMessage> = parsed_assistants
            .into_iter()
            .enumerate()
            .filter_map(|(index, assistant)| {
                (!assistant.is_synthetic || Some(index) == last_synthetic_index)
                    .then_some(assistant)
            })
            .collect();
        if assistants.is_empty() {
            return false;
        }
        let terminal_llm_error = build_terminal_llm_error(&assistants, api_errors);
        let tool_cancelled = tool_results.iter().any(|result| {
            result
                .items
                .iter()
                .any(|item| item.cancellation_reason.is_some())
        });
        turns.push(Turn {
            chat_id: chat_id.clone(),
            trigger,
            user_timestamp: user_ts.to_string(),
            user_text: user_text.clone(),
            user_content: user_content.clone(),
            assistants,
            tool_results: tool_results.to_vec(),
            skill_name: skill_name.clone(),
            api_errors: api_errors.to_vec(),
            terminal_llm_error,
            compactions: compactions.to_vec(),
            cancelled: cancelled || tool_cancelled,
            task_notifications: Vec::new(),
        });
        true
    };

    let mut current_api_errors: Vec<ApiError> = Vec::new();
    let mut current_compactions: Vec<CompactionEvent> = Vec::new();
    let mut current_cancelled = false;
    let mut session_compact_count: u32 = 0;
    let mut pending_compaction_backfill: bool = false;

    // Task notifications (async subagent completions): collected session-wide,
    // then distributed to the turn that launched each tool_use_id.
    let mut pending_notifications: Vec<TaskNotification> = Vec::new();
    let mut seen_notification_ids: std::collections::HashSet<String> =
        std::collections::HashSet::new();
    let mut tool_launch_turn: std::collections::HashMap<String, usize> =
        std::collections::HashMap::new();

    for msg in parsed_lines {
        if is_user_interruption_system_message(msg) {
            if current_user_text.is_some() && !assistant_merged.is_empty() {
                current_cancelled = true;
            } else if let Some(last_turn) = turns.last_mut() {
                last_turn.cancelled = true;
            }
            continue;
        }

        // Collect task notifications (type=queue-operation with <task-notification>).
        // The same task-id may notify more than once; keep the first per tool_use_id.
        if let Some(notification) = parse_task_notification(msg) {
            if seen_notification_ids.insert(notification.tool_use_id.clone()) {
                pending_notifications.push(notification);
            }
            continue;
        }
        // Collect api_error events (type=system, subtype=api_error)
        if get_obj_str(msg, "type") == "system" && get_obj_str(msg, "subtype") == "api_error" {
            current_api_errors.push(ApiError {
                timestamp: get_timestamp(msg),
                error: msg.get("error").cloned().unwrap_or(Value::Null),
                retry_attempt: msg
                    .get("retryAttempt")
                    .and_then(|v| v.as_u64())
                    .unwrap_or(0) as u32,
                max_retries: msg.get("maxRetries").and_then(|v| v.as_u64()).unwrap_or(0) as u32,
                retry_in_ms: msg.get("retryInMs").and_then(|v| v.as_f64()).unwrap_or(0.0),
            });
            continue;
        }

        // Collect compact_boundary events (type=system, subtype=compact_boundary)
        if get_obj_str(msg, "type") == "system" && get_obj_str(msg, "subtype") == "compact_boundary"
        {
            session_compact_count += 1;
            let metadata = msg.get("compactMetadata").unwrap_or(&Value::Null);
            let post_tokens = metadata.get("postTokens").and_then(|v| v.as_u64());
            let timestamp = get_timestamp(msg);
            current_compactions.push(CompactionEvent {
                identity: compaction_identity(msg, &timestamp, session_compact_count),
                timestamp: timestamp.clone(),
                trigger: metadata
                    .get("trigger")
                    .and_then(|v| v.as_str())
                    .unwrap_or("auto")
                    .to_string(),
                pre_tokens: metadata
                    .get("preTokens")
                    .and_then(|v| v.as_u64())
                    .unwrap_or(0),
                post_tokens: post_tokens.unwrap_or(0),
                end_timestamp: post_tokens
                    .map(|_| timestamp.clone())
                    .unwrap_or_else(String::new),
                sequence: session_compact_count,
            });
            pending_compaction_backfill = post_tokens.is_none();
            continue;
        }

        if is_tool_result(msg) {
            current_tool_results.push(parse_tool_result_message(msg));
            continue;
        }

        let role = get_role(msg);

        if role == "user" {
            // Extract skill name from isMeta user messages (command-style Skill invocation).
            // Content starts with "Base directory for this skill: /path/to/<skill-name>"
            if msg.get("isMeta").and_then(|v| v.as_bool()).unwrap_or(false) {
                // Only set skill_name from the first isMeta in a turn (the entry-point skill).
                // Sub-skill isMeta messages arrive later and must not overwrite it.
                // isMeta CAN override keyword-detected names (it's more authoritative).
                if !skill_name_from_ismeta {
                    if let Some(content) = get_content_value(msg) {
                        let text = extract_text(content);
                        if let Some(skill) = extract_skill_name_from_meta(&text) {
                            current_skill_name = Some(skill);
                            skill_name_from_ismeta = true;
                        }
                    }
                }
                continue;
            }

            let attached_compactions = flush_turn(
                &mut turns,
                &current_user_ts,
                &current_user_text,
                &current_user_content,
                &assistant_order,
                &assistant_merged,
                &current_tool_results,
                &current_chat_id,
                current_trigger,
                &current_skill_name,
                &current_api_errors,
                &current_compactions,
                current_cancelled,
            );
            if !attached_compactions && !current_compactions.is_empty() {
                unattached_compactions.append(&mut current_compactions);
            }
            current_user_ts = get_timestamp(msg);
            current_chat_id = get_prompt_chat_id(msg);
            current_trigger = normalize_turn_trigger(msg);
            let content = get_content_value(msg);
            let user_text_str = content.map(extract_text).unwrap_or_default();
            current_user_text = Some(user_text_str);
            current_user_content = content.filter(|v| agentic_otel_common::media::content_blocks(v).is_some()).cloned().unwrap_or(Value::Null);
            assistant_order.clear();
            assistant_merged.clear();
            current_tool_results.clear();
            current_api_errors.clear();
            current_compactions.clear();
            current_cancelled = false;
            current_skill_name = None;
            skill_name_from_ismeta = false;
            // Note: Keyword-based skill detection removed.
            // Skill names are now extracted directly from Skill tool_use input.
            continue;
        }

        if role == "assistant" {
            // Backfill post_tokens for the last compaction in the previously flushed turn.
            // compact_boundary is flushed into Turn N, but the first assistant after compaction
            // arrives in Turn N+1 — so we reach back into turns.last_mut().
            if pending_compaction_backfill {
                let usage = assistant_input_usage(msg);
                let timestamp = get_timestamp(msg);
                if let Some(last_compact) = turns
                    .last_mut()
                    .and_then(|turn| turn.compactions.last_mut())
                    .or_else(|| unattached_compactions.last_mut())
                {
                    backfill_compaction_post_tokens(last_compact, usage, &timestamp);
                }
                pending_compaction_backfill = false;
            }

            if current_user_text.is_none() {
                continue;
            }
            // Skip synthetic assistant messages inserted by Claude Code on user interrupt,
            // but keep isApiErrorMessage=true synthetics (API retry exhaustion).
            if get_model(msg) == "<synthetic>"
                && !msg
                    .get("isApiErrorMessage")
                    .and_then(|v| v.as_bool())
                    .unwrap_or(false)
            {
                continue;
            }
            let msg_id = get_message_id(msg);
            let key = if msg_id.is_empty() {
                format!("noid:{}", assistant_order.len())
            } else {
                msg_id
            };

            if let Some(existing) = assistant_merged.get(&key) {
                // Merge content arrays instead of overwriting
                let merged = merge_assistant_messages(existing, msg);
                assistant_merged.insert(key, merged);
            } else {
                assistant_order.push(key.clone());
                assistant_merged.insert(key, msg.clone());
            }
            // Record tool_use ids → owning turn index (turns.len() = the turn
            // currently being accumulated), for task notification distribution.
            if let Some(content) = msg
                .get("message")
                .and_then(|m| m.get("content"))
                .and_then(|c| c.as_array())
            {
                for item in content {
                    if item.get("type").and_then(|v| v.as_str()) != Some("tool_use") {
                        continue;
                    }
                    if let Some(id) = item.get("id").and_then(|v| v.as_str()) {
                        tool_launch_turn
                            .entry(id.to_string())
                            .or_insert(turns.len());
                    }
                }
            }
            continue;
        }

        if is_stop_hook_summary(msg) {
            continue;
        }
    }

    // Flush last turn
    let attached_compactions = flush_turn(
        &mut turns,
        &current_user_ts,
        &current_user_text,
                &current_user_content,
        &assistant_order,
        &assistant_merged,
        &current_tool_results,
        &current_chat_id,
        current_trigger,
        &current_skill_name,
        &current_api_errors,
        &current_compactions,
        current_cancelled,
    );
    if !attached_compactions && !current_compactions.is_empty() {
        unattached_compactions.append(&mut current_compactions);
    }

    // Distribute task notifications to the turn that launched each tool_use_id.
    // Notifications whose tool was never seen (or whose turn was dropped) go to
    // the last turn so the data is not lost.
    for notification in pending_notifications {
        match tool_launch_turn.get(&notification.tool_use_id) {
            Some(&idx) if idx < turns.len() => {
                turns[idx].task_notifications.push(notification);
            }
            _ => {
                if let Some(last) = turns.last_mut() {
                    last.task_notifications.push(notification);
                }
            }
        }
    }

    TurnBuildResult {
        turns,
        unattached_compactions,
    }
}

fn compaction_identity(msg: &Value, timestamp: &str, _sequence: u32) -> String {
    if let Some(uuid) = msg
        .get("uuid")
        .and_then(|v| v.as_str())
        .filter(|v| !v.is_empty())
    {
        return uuid.to_string();
    }

    let metadata = msg.get("compactMetadata").unwrap_or(&Value::Null);
    let trigger = metadata
        .get("trigger")
        .and_then(|v| v.as_str())
        .unwrap_or("auto");
    let pre_tokens = metadata
        .get("preTokens")
        .and_then(|v| v.as_u64())
        .unwrap_or(0)
        .to_string();
    let session_id = msg.get("sessionId").and_then(|v| v.as_str()).unwrap_or("");
    let mut hasher = Sha256::new();
    for value in [
        "cchooks-compaction-v1",
        session_id,
        timestamp,
        trigger,
        &pre_tokens,
    ] {
        hasher.update((value.len() as u64).to_be_bytes());
        hasher.update(value.as_bytes());
    }
    let digest = hasher.finalize();
    let hex = digest
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect::<String>();
    format!("cchooks-compaction-v1:{hex}")
}

fn assistant_input_usage(msg: &Value) -> Option<u64> {
    let usage = msg
        .get("message")
        .and_then(|m| m.get("usage"))
        .or_else(|| msg.get("usage"))?;
    let input = usage
        .get("input_tokens")
        .and_then(|v| v.as_u64())
        .unwrap_or(0);
    let cache_read = usage
        .get("cache_read_input_tokens")
        .and_then(|v| v.as_u64())
        .unwrap_or(0);
    let cache_creation = usage
        .get("cache_creation_input_tokens")
        .and_then(|v| v.as_u64())
        .unwrap_or(0);
    Some(input + cache_read + cache_creation)
}

fn backfill_compaction_post_tokens(
    compaction: &mut CompactionEvent,
    usage: Option<u64>,
    timestamp: &str,
) {
    if compaction.post_tokens != 0 {
        return;
    }
    if let Some(post_tokens) = usage {
        compaction.post_tokens = post_tokens;
        compaction.end_timestamp = timestamp.to_string();
    }
}

fn normalize_turn_trigger(msg: &Value) -> TurnTrigger {
    if let Some(prompt_source) = msg.get("promptSource").and_then(|value| value.as_str()) {
        return match prompt_source {
            "typed" => TurnTrigger::Interactive,
            "sdk" => TurnTrigger::Programmatic,
            "queued" => TurnTrigger::Queued,
            "system" => TurnTrigger::System,
            _ => TurnTrigger::Unknown,
        };
    }

    if let Some(origin_kind) = msg
        .get("origin")
        .and_then(|origin| origin.get("kind"))
        .and_then(|value| value.as_str())
    {
        return match origin_kind {
            "human" => TurnTrigger::Interactive,
            "task-notification" => TurnTrigger::System,
            "peer" | "coordinator" => TurnTrigger::Subagent,
            _ => TurnTrigger::Unknown,
        };
    }

    if msg
        .get("entrypoint")
        .and_then(|value| value.as_str())
        .is_some_and(|entrypoint| entrypoint.starts_with("sdk-"))
    {
        return TurnTrigger::Programmatic;
    }

    TurnTrigger::Unknown
}

#[cfg(test)]
mod tests {
    use super::super::fields::extract_tool_name_from_result;
    use super::*;
    use serde_json::json;
    #[test]
    fn test_build_turns_simple() {
        let lines = vec![
            json!({"type": "user", "message": {"role": "user", "content": "Hello"}, "timestamp": "2026-03-27T10:00:00.000Z"}),
            json!({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Hi there!"}], "id": "msg_001", "model": "claude-sonnet-4-20250514", "usage": {"input_tokens": 10, "output_tokens": 5}}, "timestamp": "2026-03-27T10:00:01.000Z"}),
        ];
        let turns = build_turns(&lines, None);
        assert_eq!(turns.len(), 1);
        assert_eq!(turns[0].chat_id, None);
        assert_eq!(turns[0].user_text, "Hello");
        assert_eq!(turns[0].assistants.len(), 1);
        assert!(turns[0].assistants[0].text.contains("Hi there!"));
    }

    fn turn_for_user(user: Value) -> Turn {
        build_turns(
            &[
                user,
                json!({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "ok"}], "id": "msg_001", "model": "claude-sonnet-4-20250514", "usage": {"input_tokens": 10, "output_tokens": 5}}, "timestamp": "2026-03-27T10:00:01.000Z"}),
            ],
            None,
        )
        .remove(0)
    }

    #[test]
    fn test_turn_trigger_normalizes_prompt_source() {
        let cases = [
            ("typed", TurnTrigger::Interactive),
            ("sdk", TurnTrigger::Programmatic),
            ("queued", TurnTrigger::Queued),
            ("system", TurnTrigger::System),
            ("other", TurnTrigger::Unknown),
        ];
        for (prompt_source, expected) in cases {
            let turn = turn_for_user(json!({
                "type": "user",
                "message": {"role": "user", "content": "Hello"},
                "promptSource": prompt_source,
                "origin": {"kind": "human"},
                "entrypoint": "sdk-py",
                "timestamp": "2026-03-27T10:00:00.000Z"
            }));
            assert_eq!(turn.trigger, expected, "promptSource={prompt_source}");
        }
    }

    #[test]
    fn test_turn_trigger_uses_origin_when_prompt_source_missing() {
        let cases = [
            ("human", TurnTrigger::Interactive),
            ("task-notification", TurnTrigger::System),
            ("peer", TurnTrigger::Subagent),
            ("coordinator", TurnTrigger::Subagent),
            ("unsupported", TurnTrigger::Unknown),
        ];
        for (origin_kind, expected) in cases {
            let turn = turn_for_user(json!({
                "type": "user",
                "message": {"role": "user", "content": "Hello"},
                "origin": {"kind": origin_kind},
                "entrypoint": "sdk-cli",
                "timestamp": "2026-03-27T10:00:00.000Z"
            }));
            assert_eq!(turn.trigger, expected, "origin.kind={origin_kind}");
        }
    }

    #[test]
    fn test_turn_trigger_entrypoint_is_last_resort() {
        let sdk_turn = turn_for_user(json!({
            "type": "user",
            "message": {"role": "user", "content": "Hello"},
            "entrypoint": "sdk-ts",
            "timestamp": "2026-03-27T10:00:00.000Z"
        }));
        assert_eq!(sdk_turn.trigger, TurnTrigger::Programmatic);

        let cli_turn = turn_for_user(json!({
            "type": "user",
            "message": {"role": "user", "content": "Hello"},
            "entrypoint": "cli",
            "timestamp": "2026-03-27T10:00:00.000Z"
        }));
        assert_eq!(cli_turn.trigger, TurnTrigger::Unknown);
    }

    #[test]
    fn test_turn_trigger_ismeta_does_not_open_or_overwrite_turn() {
        let lines = vec![
            json!({"type": "user", "message": {"role": "user", "content": "Base directory for this skill: /Users/x/.claude/skills/s\nInstructions"}, "isMeta": true, "promptSource": "system", "timestamp": "2026-03-27T09:59:59.000Z"}),
            json!({"type": "user", "message": {"role": "user", "content": "Hello"}, "promptSource": "typed", "timestamp": "2026-03-27T10:00:00.000Z"}),
            json!({"type": "user", "message": {"role": "user", "content": "Base directory for this skill: /Users/x/.claude/skills/other\nInstructions"}, "isMeta": true, "promptSource": "system", "timestamp": "2026-03-27T10:00:00.500Z"}),
            json!({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "ok"}], "id": "msg_001", "model": "claude-sonnet-4-20250514", "usage": {"input_tokens": 10, "output_tokens": 5}}, "timestamp": "2026-03-27T10:00:01.000Z"}),
        ];
        let turns = build_turns(&lines, None);
        assert_eq!(turns.len(), 1);
        assert_eq!(turns[0].trigger, TurnTrigger::Interactive);
    }

    #[test]
    fn test_turn_trigger_flushes_independently_per_turn() {
        let lines = vec![
            json!({"type": "user", "message": {"role": "user", "content": "Q1"}, "promptSource": "typed", "timestamp": "2026-03-27T10:00:00.000Z"}),
            json!({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "A1"}], "id": "msg_001", "model": "claude-sonnet-4-20250514", "usage": {"input_tokens": 5, "output_tokens": 3}}, "timestamp": "2026-03-27T10:00:01.000Z"}),
            json!({"type": "user", "message": {"role": "user", "content": "Q2"}, "promptSource": "queued", "timestamp": "2026-03-27T10:01:00.000Z"}),
            json!({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "A2"}], "id": "msg_002", "model": "claude-sonnet-4-20250514", "usage": {"input_tokens": 8, "output_tokens": 4}}, "timestamp": "2026-03-27T10:01:01.000Z"}),
        ];
        let turns = build_turns(&lines, None);
        assert_eq!(turns.len(), 2);
        assert_eq!(turns[0].trigger, TurnTrigger::Interactive);
        assert_eq!(turns[1].trigger, TurnTrigger::Queued);
    }

    #[test]
    fn test_build_turns_uses_prompt_chat_id_when_available() {
        let lines = vec![
            json!({"type": "user", "uuid": "prompt-abc", "message": {"role": "user", "content": "Hello"}, "timestamp": "2026-03-27T10:00:00.000Z"}),
            json!({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Hi there!"}], "id": "msg_001", "model": "claude-sonnet-4-20250514", "usage": {"input_tokens": 10, "output_tokens": 5}}, "timestamp": "2026-03-27T10:00:01.000Z"}),
        ];
        let turns = build_turns(&lines, None);
        assert_eq!(turns.len(), 1);
        assert_eq!(turns[0].chat_id.as_deref(), Some("prompt-abc"));
    }

    #[test]
    fn test_build_turns_with_tool_result() {
        let lines = vec![
            json!({"type": "user", "message": {"role": "user", "content": "Read file"}, "timestamp": "2026-03-27T10:00:00.000Z"}),
            json!({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Reading..."}, {"type": "tool_use", "id": "toolu_001", "name": "Read", "input": {"path": "test.txt"}}], "id": "msg_001", "model": "claude-sonnet-4-20250514", "usage": {"input_tokens": 10, "output_tokens": 15}}, "timestamp": "2026-03-27T10:00:01.000Z"}),
            json!({"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_001", "content": "file contents"}]}, "timestamp": "2026-03-27T10:00:02.000Z"}),
        ];
        let turns = build_turns(&lines, None);
        assert_eq!(turns.len(), 1);
        assert!(!turns[0].tool_results.is_empty());
        assert_eq!(turns[0].assistants[0].tool_uses.len(), 1);
        assert_eq!(turns[0].assistants[0].tool_uses[0].name, "Read");
    }

    #[test]
    fn test_build_turns_skip_stop_hook() {
        let lines = vec![
            json!({"type": "user", "message": {"role": "user", "content": "Hello"}, "timestamp": "2026-03-27T10:00:00.000Z"}),
            json!({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Hi!"}], "id": "msg_001", "model": "claude-sonnet-4-20250514", "usage": {"input_tokens": 5, "output_tokens": 3}}, "timestamp": "2026-03-27T10:00:01.000Z"}),
            json!({"type": "system", "subtype": "stop_hook_summary", "timestamp": "2026-03-27T10:00:02.000Z"}),
        ];
        let turns = build_turns(&lines, None);
        assert_eq!(turns.len(), 1);
    }

    #[test]
    fn test_build_turns_multi_turn() {
        let lines = vec![
            json!({"type": "user", "message": {"role": "user", "content": "Q1"}, "timestamp": "2026-03-27T10:00:00.000Z"}),
            json!({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "A1"}], "id": "msg_001", "model": "claude-sonnet-4-20250514", "usage": {"input_tokens": 5, "output_tokens": 3}}, "timestamp": "2026-03-27T10:00:01.000Z"}),
            json!({"type": "user", "message": {"role": "user", "content": "Q2"}, "timestamp": "2026-03-27T10:01:00.000Z"}),
            json!({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "A2"}], "id": "msg_002", "model": "claude-sonnet-4-20250514", "usage": {"input_tokens": 8, "output_tokens": 4}}, "timestamp": "2026-03-27T10:01:01.000Z"}),
        ];
        let turns = build_turns(&lines, None);
        assert_eq!(turns.len(), 2);
        assert_eq!(turns[0].user_text, "Q1");
        assert_eq!(turns[1].user_text, "Q2");
    }

    #[test]
    fn test_build_turns_merged_assistant() {
        let lines = vec![
            json!({"type": "user", "message": {"role": "user", "content": "Tell me a story"}, "timestamp": "2026-03-27T10:00:00.000Z"}),
            json!({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Once upon a time"}], "id": "msg_001", "model": "claude-sonnet-4-20250514"}, "timestamp": "2026-03-27T10:00:01.000Z"}),
            json!({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "there was a brave knight."}], "id": "msg_001", "model": "claude-sonnet-4-20250514", "usage": {"input_tokens": 10, "output_tokens": 30}}, "timestamp": "2026-03-27T10:00:02.000Z"}),
        ];
        let turns = build_turns(&lines, None);
        assert_eq!(turns.len(), 1);
        assert_eq!(turns[0].assistants.len(), 1);
        let assistant_text = &turns[0].assistants[0].text;
        assert!(assistant_text.contains("Once upon a time"));
        assert!(assistant_text.contains("brave knight"));
    }

    #[test]
    #[ignore] // Deprecated: keyword-based skill detection removed
    fn test_build_turns_keyword_skill_detection() {
        let config = crate::common::skill_keywords::load_skill_keywords();
        let lines = vec![
            json!({"type": "user", "message": {"role": "user", "content": "帮我跑一下eve评测"}, "timestamp": "2026-03-27T10:00:00.000Z"}),
            json!({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "好的"}], "id": "msg_001", "model": "claude", "usage": {"input_tokens": 5, "output_tokens": 3}}, "timestamp": "2026-03-27T10:00:01.000Z"}),
        ];
        let turns = build_turns(&lines, Some(&config));
        assert_eq!(turns.len(), 1);
        assert_eq!(turns[0].skill_name, Some("eve-beta-skill".to_string()));
    }

    #[test]
    #[ignore] // Deprecated: keyword-based skill detection removed
    fn test_build_turns_keyword_does_not_override_ismeta() {
        let config = crate::common::skill_keywords::load_skill_keywords();
        let lines = vec![
            json!({"type": "user", "message": {"role": "user", "content": "帮我跑一下eve评测"}, "timestamp": "2026-03-27T10:00:00.000Z"}),
            // isMeta message sets a different skill
            json!({"type": "user", "message": {"role": "user", "content": "Base directory for this skill: /Users/x/.claude/skills/other-skill\nInstructions"}, "isMeta": true, "timestamp": "2026-03-27T10:00:00.500Z"}),
            json!({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "好的"}], "id": "msg_001", "model": "claude", "usage": {"input_tokens": 5, "output_tokens": 3}}, "timestamp": "2026-03-27T10:00:01.000Z"}),
        ];
        let turns = build_turns(&lines, Some(&config));
        assert_eq!(turns.len(), 1);
        // isMeta should override keyword detection
        assert_eq!(turns[0].skill_name, Some("other-skill".to_string()));
    }

    #[test]
    fn test_build_turns_first_ismeta_wins_over_subsequent() {
        // When multiple isMeta messages arrive in the same turn (entry-point + sub-skills),
        // only the first one should set skill_name.
        let lines = vec![
            json!({"type": "user", "message": {"role": "user", "content": "/medeval:task-one-shot 200478"}, "timestamp": "2026-04-07T03:25:00.000Z"}),
            // First isMeta: entry-point skill
            json!({"type": "user", "message": {"role": "user", "content": "Base directory for this skill: /Users/x/.claude/plugins/medeval/skills/medeval:task-one-shot\nInstructions"}, "isMeta": true, "timestamp": "2026-04-07T03:25:00.100Z"}),
            json!({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Starting pipeline"}, {"type": "tool_use", "id": "fc-001", "name": "Skill", "input": {"skill": "medeval:medeval:init-plugin"}}], "id": "msg_001", "model": "GLM-5", "usage": {"input_tokens": 10, "output_tokens": 5}}, "timestamp": "2026-04-07T03:25:01.000Z"}),
            // tool_result for the Skill call
            json!({"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "fc-001", "content": "Launching skill"}]}, "timestamp": "2026-04-07T03:25:02.000Z"}),
            // Second isMeta: sub-skill (should NOT overwrite)
            json!({"type": "user", "message": {"role": "user", "content": "Base directory for this skill: /Users/x/.claude/plugins/medeval/skills/medeval:init-plugin\nSub-skill instructions"}, "isMeta": true, "timestamp": "2026-04-07T03:25:02.100Z"}),
            json!({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Done"}], "id": "msg_002", "model": "GLM-5", "usage": {"input_tokens": 10, "output_tokens": 3}}, "timestamp": "2026-04-07T03:25:03.000Z"}),
        ];
        let turns = build_turns(&lines, None);
        assert_eq!(turns.len(), 1);
        // First isMeta (entry-point) should win, not the sub-skill
        assert_eq!(
            turns[0].skill_name,
            Some("medeval:task-one-shot".to_string())
        );
    }

    #[test]
    fn test_build_turns_no_keyword_match() {
        let config = crate::common::skill_keywords::load_skill_keywords();
        let lines = vec![
            json!({"type": "user", "message": {"role": "user", "content": "今天天气怎么样"}, "timestamp": "2026-03-27T10:00:00.000Z"}),
            json!({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "晴天"}], "id": "msg_001", "model": "claude", "usage": {"input_tokens": 5, "output_tokens": 3}}, "timestamp": "2026-03-27T10:00:01.000Z"}),
        ];
        let turns = build_turns(&lines, Some(&config));
        assert_eq!(turns.len(), 1);
        assert_eq!(turns[0].skill_name, None);
    }

    // --- extract_tool_name_from_result ---

    #[test]
    fn test_extract_tool_name_object_with_tool_name() {
        let msg = json!({
            "toolUseResult": {"toolName": "Read", "stdout": "hello"}
        });
        assert_eq!(
            extract_tool_name_from_result(&msg),
            Some("Read".to_string())
        );
    }

    #[test]
    fn test_extract_tool_name_object_without_tool_name() {
        // Built-in tool result (object format, no toolName field)
        let msg = json!({
            "toolUseResult": {"stdout": "output", "stderr": ""}
        });
        assert_eq!(extract_tool_name_from_result(&msg), None);
    }

    #[test]
    fn test_extract_tool_name_mcp_with_meta_tool() {
        // MCP tool result with meta.tool in response JSON
        let msg = json!({
            "toolUseResult": [
                {
                    "type": "text",
                    "text": "{\"ok\":true,\"data\":[],\"meta\":{\"tool\":\"skylark_user_recent\",\"pagination\":{}}}"
                }
            ]
        });
        assert_eq!(
            extract_tool_name_from_result(&msg),
            Some("mcp__skylark_user_recent".to_string())
        );
    }

    #[test]
    fn test_extract_tool_name_mcp_without_meta_tool() {
        // MCP tool result without meta.tool — fallback to "mcp_tool"
        let msg = json!({
            "toolUseResult": [
                {"type": "text", "text": "plain text response without JSON"}
            ]
        });
        assert_eq!(
            extract_tool_name_from_result(&msg),
            Some("mcp__mcp_tool".to_string())
        );
    }

    #[test]
    fn test_extract_tool_name_mcp_json_without_meta() {
        // MCP result is valid JSON but has no meta.tool field
        let msg = json!({
            "toolUseResult": [
                {"type": "text", "text": "{\"result\":\"success\",\"count\":42}"}
            ]
        });
        assert_eq!(
            extract_tool_name_from_result(&msg),
            Some("mcp__mcp_tool".to_string())
        );
    }

    #[test]
    fn test_extract_tool_name_no_tool_use_result() {
        let msg = json!({"type": "user"});
        assert_eq!(extract_tool_name_from_result(&msg), None);
    }

    // --- api_error collection in build_turns ---

    #[test]
    fn test_build_turns_with_api_errors() {
        let lines = vec![
            json!({"type": "user", "message": {"role": "user", "content": "Hello"}, "timestamp": "2026-03-27T10:00:00.000Z"}),
            json!({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Thinking..."}], "id": "msg_001", "model": "claude-sonnet-4-20250514", "usage": {"input_tokens": 10, "output_tokens": 5}}, "timestamp": "2026-03-27T10:00:01.000Z"}),
            // api_error events
            json!({"type": "system", "subtype": "api_error", "level": "error", "error": {"status": 502, "error": {"error": "Bad Gateway", "message": "proxy failed"}}, "retryAttempt": 1, "maxRetries": 10, "retryInMs": 568.69, "timestamp": "2026-03-27T10:00:02.000Z"}),
            json!({"type": "system", "subtype": "api_error", "level": "error", "error": {"status": 502, "error": {"error": "Bad Gateway", "message": "proxy failed"}}, "retryAttempt": 2, "maxRetries": 10, "retryInMs": 1200.0, "timestamp": "2026-03-27T10:00:03.000Z"}),
            // Successful assistant after retries
            json!({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Here is the answer."}], "id": "msg_002", "model": "claude-sonnet-4-20250514", "usage": {"input_tokens": 20, "output_tokens": 15}}, "timestamp": "2026-03-27T10:00:05.000Z"}),
        ];
        let turns = build_turns(&lines, None);
        assert_eq!(turns.len(), 1);
        assert_eq!(turns[0].api_errors.len(), 2);
        assert_eq!(turns[0].api_errors[0].retry_attempt, 1);
        assert_eq!(turns[0].api_errors[1].retry_attempt, 2);
        assert_eq!(turns[0].api_errors[0].max_retries, 10);
        assert!((turns[0].api_errors[0].retry_in_ms - 568.69).abs() < 0.01);
        // Error object preserved
        assert_eq!(
            turns[0].api_errors[0]
                .error
                .get("status")
                .unwrap()
                .as_i64()
                .unwrap(),
            502
        );
    }

    #[test]
    fn test_build_turns_synthetic_assistant() {
        let lines = vec![
            json!({"type": "user", "message": {"role": "user", "content": "Hello"}, "timestamp": "2026-03-27T10:00:00.000Z"}),
            json!({"type": "system", "subtype": "api_error", "level": "error", "error": {"status": 400, "error": {"type": "invalid_request_error", "message": "model: claude-regression-invalid-20260824 is not valid", "model": "claude-regression-invalid-20260824"}}, "retryAttempt": 1, "maxRetries": 3, "retryInMs": 500.0, "timestamp": "2026-03-27T10:00:01.000Z"}),
            // Synthetic assistant (isApiErrorMessage=true)
            json!({"type": "assistant", "isApiErrorMessage": true, "message": {"role": "assistant", "content": [{"type": "text", "text": "I'm having trouble..."}], "id": "msg_synth", "model": "<synthetic>", "usage": {"input_tokens": 0, "output_tokens": 0}}, "timestamp": "2026-03-27T10:00:02.000Z"}),
        ];
        let turns = build_turns(&lines, None);
        assert_eq!(turns.len(), 1);
        assert_eq!(turns[0].assistants.len(), 1);
        assert!(turns[0].assistants[0].is_synthetic);
        assert_eq!(turns[0].assistants[0].model, "<synthetic>");
        assert_eq!(turns[0].api_errors.len(), 1);
        let terminal = turns[0].terminal_llm_error.as_ref().unwrap();
        assert_eq!(terminal.error_type, "invalid_model");
        assert_eq!(terminal.status_code, Some(400));
        assert_eq!(
            terminal.request_model.as_deref(),
            Some("claude-regression-invalid-20260824")
        );
    }

    #[test]
    fn test_build_turns_retry_recovery_is_not_terminal() {
        let lines = vec![
            json!({"type": "user", "message": {"role": "user", "content": "Hello"}, "timestamp": "2026-03-27T10:00:00.000Z"}),
            json!({"type": "system", "subtype": "api_error", "level": "error", "error": {"status": 502, "error": {"message": "proxy failed"}}, "retryAttempt": 1, "maxRetries": 3, "retryInMs": 500.0, "timestamp": "2026-03-27T10:00:01.000Z"}),
            json!({"type": "assistant", "isApiErrorMessage": true, "message": {"role": "assistant", "content": [{"type": "text", "text": "Retrying..."}], "id": "msg_synth", "model": "<synthetic>", "usage": {"input_tokens": 0, "output_tokens": 0}}, "timestamp": "2026-03-27T10:00:01.500Z"}),
            json!({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Recovered"}], "id": "msg_ok", "model": "claude-sonnet-4-20250514", "usage": {"input_tokens": 10, "output_tokens": 2}}, "timestamp": "2026-03-27T10:00:02.000Z"}),
        ];
        let turns = build_turns(&lines, None);
        assert_eq!(turns.len(), 1);
        assert_eq!(turns[0].api_errors.len(), 1);
        assert!(turns[0].terminal_llm_error.is_none());
        assert_eq!(turns[0].assistants.len(), 1);
        assert!(!turns[0].assistants[0].is_synthetic);
    }

    #[test]
    fn test_build_turns_api_errors_reset_between_turns() {
        let lines = vec![
            // Turn 1 with api_errors
            json!({"type": "user", "message": {"role": "user", "content": "Q1"}, "timestamp": "2026-03-27T10:00:00.000Z"}),
            json!({"type": "system", "subtype": "api_error", "level": "error", "error": {}, "retryAttempt": 1, "maxRetries": 3, "retryInMs": 500.0, "timestamp": "2026-03-27T10:00:01.000Z"}),
            json!({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "A1"}], "id": "msg_001", "model": "claude", "usage": {"input_tokens": 5, "output_tokens": 3}}, "timestamp": "2026-03-27T10:00:02.000Z"}),
            // Turn 2 without api_errors
            json!({"type": "user", "message": {"role": "user", "content": "Q2"}, "timestamp": "2026-03-27T10:01:00.000Z"}),
            json!({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "A2"}], "id": "msg_002", "model": "claude", "usage": {"input_tokens": 5, "output_tokens": 3}}, "timestamp": "2026-03-27T10:01:01.000Z"}),
        ];
        let turns = build_turns(&lines, None);
        assert_eq!(turns.len(), 2);
        assert_eq!(
            turns[0].api_errors.len(),
            1,
            "turn 1 should have 1 api_error"
        );
        assert!(
            turns[1].api_errors.is_empty(),
            "turn 2 should have no api_errors"
        );
    }

    #[test]
    fn test_build_turns_api_error_with_empty_error_object() {
        let lines = vec![
            json!({"type": "user", "message": {"role": "user", "content": "Hello"}, "timestamp": "2026-03-27T10:00:00.000Z"}),
            json!({"type": "system", "subtype": "api_error", "level": "error", "error": {}, "retryAttempt": 1, "maxRetries": 10, "retryInMs": 100.0, "timestamp": "2026-03-27T10:00:01.000Z"}),
            json!({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "OK"}], "id": "msg_001", "model": "claude", "usage": {"input_tokens": 5, "output_tokens": 3}}, "timestamp": "2026-03-27T10:00:02.000Z"}),
        ];
        let turns = build_turns(&lines, None);
        assert_eq!(turns[0].api_errors.len(), 1);
        // Empty error object should be handled gracefully
        assert!(turns[0].api_errors[0].error.as_object().unwrap().is_empty());
    }

    #[test]
    fn test_build_turns_cancelled_from_tool_denial() {
        let lines = vec![
            json!({"type": "user", "message": {"role": "user", "content": "Run tool"}, "timestamp": "2026-03-27T10:00:00.000Z"}),
            json!({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_cancel", "name": "Bash", "input": {"command": "sleep 60 && echo should-not-complete"}}], "id": "msg_001", "model": "claude-sonnet-4-20250514", "usage": {"input_tokens": 10, "output_tokens": 5}}, "timestamp": "2026-03-27T10:00:01.000Z"}),
            json!({"type": "user", "toolDenialKind": "user-rejected", "toolUseResult": "User rejected tool use", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_cancel", "is_error": true, "content": "User rejected tool use"}]}, "timestamp": "2026-03-27T10:00:02.000Z"}),
        ];
        let turns = build_turns(&lines, None);
        let item = &turns[0].tool_results[0].items[0];
        assert!(item.is_error);
        assert_eq!(item.tool_denial_kind.as_deref(), Some("user-rejected"));
        assert_eq!(item.cancellation_reason.as_deref(), Some("user_rejected"));
    }

    #[test]
    fn test_build_turns_cancelled_from_interruption_system_message() {
        let lines = vec![
            json!({"type": "user", "message": {"role": "user", "content": "Run tool"}, "timestamp": "2026-03-27T10:00:00.000Z"}),
            json!({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Running"}], "id": "msg_001", "model": "claude-sonnet-4-20250514", "usage": {"input_tokens": 10, "output_tokens": 5}}, "timestamp": "2026-03-27T10:00:01.000Z"}),
            json!({"type": "system", "content": "[Request interrupted by user for tool use]", "interruptedMessageId": "msg_001", "timestamp": "2026-03-27T10:00:02.000Z"}),
        ];
        let turns = build_turns(&lines, None);
        assert!(turns[0].cancelled);
    }

    #[test]
    fn test_build_turns_plain_tool_error_is_not_cancelled() {
        let lines = vec![
            json!({"type": "user", "message": {"role": "user", "content": "Read missing"}, "timestamp": "2026-03-27T10:00:00.000Z"}),
            json!({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_err", "name": "Read", "input": {"file_path": "missing.txt"}}], "id": "msg_001", "model": "claude-sonnet-4-20250514", "usage": {"input_tokens": 10, "output_tokens": 5}}, "timestamp": "2026-03-27T10:00:01.000Z"}),
            json!({"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_err", "is_error": true, "content": "Error: file not found"}]}, "timestamp": "2026-03-27T10:00:02.000Z"}),
        ];
        let turns = build_turns(&lines, None);
        let item = &turns[0].tool_results[0].items[0];
        assert!(item.is_error);
        assert_eq!(item.cancellation_reason, None);
        assert!(!turns[0].cancelled);
    }

    #[test]
    fn test_build_turns_with_compaction() {
        // Turn 1: user → assistant → compact_boundary
        // Then: isCompactSummary user → assistant (with usage for backfill)
        let lines = vec![
            json!({"type": "human", "message": {"role": "user", "content": [{"type": "text", "text": "Hello"}]}, "timestamp": "2026-04-11T16:00:00.000Z"}),
            json!({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Hi there"}], "id": "msg_001", "model": "claude-sonnet-4-20250514", "usage": {"input_tokens": 100, "output_tokens": 20}}, "timestamp": "2026-04-11T16:00:01.000Z"}),
            // compact_boundary event
            json!({"type": "system", "subtype": "compact_boundary", "content": "Conversation compacted", "timestamp": "2026-04-11T16:18:31.889Z", "compactMetadata": {"trigger": "auto", "preTokens": 169477}}),
            // isCompactSummary user message starts new turn
            json!({"type": "human", "message": {"role": "user", "content": [{"type": "text", "text": "Summary of prior conversation..."}]}, "timestamp": "2026-04-11T16:18:31.889Z", "isCompactSummary": true}),
            // First assistant after compaction — usage used for backfill
            json!({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Continuing..."}], "id": "msg_002", "model": "claude-sonnet-4-20250514", "usage": {"input_tokens": 3, "output_tokens": 10, "cache_read_input_tokens": 63872, "cache_creation_input_tokens": 36672}}, "timestamp": "2026-04-11T16:18:42.530Z"}),
        ];
        let turns = build_turns(&lines, None);
        assert_eq!(
            turns.len(),
            2,
            "should have 2 turns (before and after compaction)"
        );

        // Compaction should be on Turn 1 (the preceding turn)
        assert_eq!(
            turns[0].compactions.len(),
            1,
            "Turn 1 should have 1 compaction"
        );
        let compact = &turns[0].compactions[0];
        assert!(compact.identity.starts_with("cchooks-compaction-v1:"));
        assert_eq!(compact.trigger, "auto");
        assert_eq!(compact.pre_tokens, 169477);
        assert_eq!(compact.sequence, 1);
        assert_eq!(compact.timestamp, "2026-04-11T16:18:31.889Z");

        // post_tokens should be backfilled from Turn 2's first assistant
        assert_eq!(
            compact.post_tokens,
            3 + 63872 + 36672,
            "post_tokens = input + cache_read + cache_creation"
        );
        assert_eq!(compact.end_timestamp, "2026-04-11T16:18:42.530Z");

        // Turn 2 should have no compactions
        assert!(
            turns[1].compactions.is_empty(),
            "Turn 2 should have no compactions"
        );
    }

    #[test]
    fn test_compaction_sequence_increments() {
        // Two compactions in the same session
        let lines = vec![
            json!({"type": "human", "message": {"role": "user", "content": [{"type": "text", "text": "Q1"}]}, "timestamp": "2026-04-11T16:00:00.000Z"}),
            json!({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "A1"}], "id": "msg_001", "model": "claude", "usage": {"input_tokens": 100, "output_tokens": 20}}, "timestamp": "2026-04-11T16:00:01.000Z"}),
            json!({"type": "system", "subtype": "compact_boundary", "timestamp": "2026-04-11T16:18:00.000Z", "compactMetadata": {"trigger": "auto", "preTokens": 170000}}),
            json!({"type": "human", "message": {"role": "user", "content": [{"type": "text", "text": "Summary 1"}]}, "timestamp": "2026-04-11T16:18:00.000Z"}),
            json!({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "A2"}], "id": "msg_002", "model": "claude", "usage": {"input_tokens": 5, "output_tokens": 10, "cache_read_input_tokens": 50000, "cache_creation_input_tokens": 50000}}, "timestamp": "2026-04-11T16:18:10.000Z"}),
            // Second compaction
            json!({"type": "system", "subtype": "compact_boundary", "timestamp": "2026-04-11T16:30:00.000Z", "compactMetadata": {"trigger": "manual", "preTokens": 160000}}),
            json!({"type": "human", "message": {"role": "user", "content": [{"type": "text", "text": "Summary 2"}]}, "timestamp": "2026-04-11T16:30:00.000Z"}),
            json!({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "A3"}], "id": "msg_003", "model": "claude", "usage": {"input_tokens": 2, "output_tokens": 8, "cache_read_input_tokens": 90000, "cache_creation_input_tokens": 0}}, "timestamp": "2026-04-11T16:30:09.000Z"}),
        ];
        let turns = build_turns(&lines, None);
        assert_eq!(turns.len(), 3);

        assert_eq!(turns[0].compactions.len(), 1);
        assert_eq!(turns[0].compactions[0].sequence, 1);
        assert_eq!(turns[0].compactions[0].trigger, "auto");

        assert_eq!(turns[1].compactions.len(), 1);
        assert_eq!(turns[1].compactions[0].sequence, 2);
        assert_eq!(turns[1].compactions[0].trigger, "manual");
        assert_eq!(turns[1].compactions[0].post_tokens, (2 + 90000));
    }

    #[test]
    fn test_build_turns_detailed_returns_unattached_suffix_compaction() {
        let lines = vec![
            json!({"type": "system", "subtype": "compact_boundary", "timestamp": "2026-04-11T16:18:31.889Z", "uuid": "compact-uuid", "compactMetadata": {"trigger": "manual", "preTokens": 72409, "postTokens": 4703}}),
            json!({"type": "human", "message": {"role": "user", "content": [{"type": "text", "text": "Summary of prior conversation..."}]}, "timestamp": "2026-04-11T16:18:31.889Z", "isCompactSummary": true}),
            json!({"type": "user", "message": {"role": "user", "content": "/exit"}, "timestamp": "2026-04-11T16:18:40.000Z"}),
        ];
        let result = build_turns_detailed(&lines, None);
        assert!(result.turns.is_empty());
        assert_eq!(result.unattached_compactions.len(), 1);
        let compact = &result.unattached_compactions[0];
        assert_eq!(compact.identity, "compact-uuid");
        assert_eq!(compact.trigger, "manual");
        assert_eq!(compact.pre_tokens, 72409);
        assert_eq!(compact.post_tokens, 4703);
        assert_eq!(compact.end_timestamp, "2026-04-11T16:18:31.889Z");
    }

    #[test]
    fn test_compaction_post_tokens_prefer_metadata_over_assistant_usage() {
        let lines = vec![
            json!({"type": "human", "message": {"role": "user", "content": [{"type": "text", "text": "Hello"}]}, "timestamp": "2026-04-11T16:00:00.000Z"}),
            json!({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Hi there"}], "id": "msg_001", "model": "claude-sonnet-4-20250514", "usage": {"input_tokens": 100, "output_tokens": 20}}, "timestamp": "2026-04-11T16:00:01.000Z"}),
            json!({"type": "system", "subtype": "compact_boundary", "timestamp": "2026-04-11T16:18:31.889Z", "compactMetadata": {"trigger": "manual", "preTokens": 72409, "postTokens": 4703}}),
            json!({"type": "human", "message": {"role": "user", "content": [{"type": "text", "text": "Summary of prior conversation..."}]}, "timestamp": "2026-04-11T16:18:31.889Z", "isCompactSummary": true}),
            json!({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Continuing..."}], "id": "msg_002", "model": "claude-sonnet-4-20250514", "usage": {"input_tokens": 10, "cache_read_input_tokens": 20, "cache_creation_input_tokens": 30}}, "timestamp": "2026-04-11T16:18:42.530Z"}),
        ];
        let turns = build_turns(&lines, None);
        let compact = &turns[0].compactions[0];
        assert_eq!(compact.post_tokens, 4703);
        assert_eq!(compact.end_timestamp, "2026-04-11T16:18:31.889Z");
    }

    #[test]
    fn test_unattached_compaction_falls_back_to_next_assistant_usage() {
        let lines = vec![
            json!({"type": "system", "subtype": "compact_boundary", "timestamp": "2026-04-11T16:18:31.889Z", "compactMetadata": {"trigger": "auto", "preTokens": 169477}}),
            json!({"type": "human", "message": {"role": "user", "content": [{"type": "text", "text": "Summary of prior conversation..."}]}, "timestamp": "2026-04-11T16:18:31.889Z", "isCompactSummary": true}),
            json!({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Continuing..."}], "id": "msg_002", "model": "claude-sonnet-4-20250514", "usage": {"input_tokens": 3, "cache_read_input_tokens": 63872, "cache_creation_input_tokens": 36672}}, "timestamp": "2026-04-11T16:18:42.530Z"}),
        ];
        let result = build_turns_detailed(&lines, None);
        assert_eq!(result.turns.len(), 1);
        assert!(result.turns[0].compactions.is_empty());
        assert_eq!(result.unattached_compactions.len(), 1);
        let compact = &result.unattached_compactions[0];
        assert_eq!(compact.post_tokens, 3 + 63872 + 36672);
        assert_eq!(compact.end_timestamp, "2026-04-11T16:18:42.530Z");
    }

    #[test]
    fn test_compaction_fallback_identity_is_window_stable() {
        let compact_boundary = json!({"type": "system", "subtype": "compact_boundary", "timestamp": "2026-04-11T16:30:00.000Z", "sessionId": "s1", "compactMetadata": {"trigger": "manual", "preTokens": 160000, "postTokens": 4703}});
        let full_lines = vec![
            json!({"type": "human", "message": {"role": "user", "content": [{"type": "text", "text": "Q1"}]}, "timestamp": "2026-04-11T16:00:00.000Z"}),
            json!({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "A1"}], "id": "msg_001", "model": "claude", "usage": {"input_tokens": 100, "output_tokens": 20}}, "timestamp": "2026-04-11T16:00:01.000Z"}),
            json!({"type": "system", "subtype": "compact_boundary", "timestamp": "2026-04-11T16:18:00.000Z", "sessionId": "s1", "compactMetadata": {"trigger": "auto", "preTokens": 170000, "postTokens": 5000}}),
            json!({"type": "human", "message": {"role": "user", "content": [{"type": "text", "text": "Summary 1"}]}, "timestamp": "2026-04-11T16:18:00.000Z", "isCompactSummary": true}),
            json!({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "A2"}], "id": "msg_002", "model": "claude", "usage": {"input_tokens": 5, "output_tokens": 10}}, "timestamp": "2026-04-11T16:18:10.000Z"}),
            compact_boundary.clone(),
            json!({"type": "human", "message": {"role": "user", "content": [{"type": "text", "text": "Summary 2"}]}, "timestamp": "2026-04-11T16:30:00.000Z", "isCompactSummary": true}),
        ];
        let suffix_lines = vec![
            compact_boundary,
            json!({"type": "human", "message": {"role": "user", "content": [{"type": "text", "text": "Summary 2"}]}, "timestamp": "2026-04-11T16:30:00.000Z", "isCompactSummary": true}),
        ];

        let full_identity =
            &build_turns_detailed(&full_lines, None).turns[1].compactions[0].identity;
        let suffix_identity =
            &build_turns_detailed(&suffix_lines, None).unattached_compactions[0].identity;

        assert_eq!(full_identity, suffix_identity);
    }
}
