use serde_json::Value;
use std::fs::File;
use std::io::{Read, Seek, SeekFrom};
use std::path::Path;

use super::fields::{extract_text, get_content_value, get_role, is_tool_result};
use super::types::{AssistantMessage, Turn};

pub fn is_realtime_terminal_generation(assistant: &AssistantMessage) -> bool {
    !assistant.message_id.is_empty()
        && matches!(assistant.stop_reason.as_str(), "tool_use" | "pause_turn")
        && assistant.usage.has_any()
        && !assistant.usage.all_zero()
        && assistant.tool_uses.iter().any(|tool| !tool.id.is_empty())
}

/// Returns `(postable_prefix_len, incomplete_tail)` for the retained window.
/// A user-only tail does not remove the complete prefix; a tool-use-only or
/// otherwise non-terminal assistant tail removes the final built turn.
pub fn classify_postable_turns(parsed_lines: &[Value], turns: &[Turn]) -> (usize, bool) {
    classify_postable_turns_inner(parsed_lines, turns, false)
}

/// SessionEnd is an authoritative lifecycle boundary, so an otherwise
/// ambiguous empty-stop record or a completed `StructuredOutput` tool result
/// must be finalized instead of left retryable forever. Stop/StopFailure
/// callers use [`classify_postable_turns`] and keep waiting for continuation.
pub fn classify_postable_turns_at_session_end(
    parsed_lines: &[Value],
    turns: &[Turn],
) -> (usize, bool) {
    classify_postable_turns_inner(parsed_lines, turns, true)
}

fn classify_postable_turns_inner(
    parsed_lines: &[Value],
    turns: &[Turn],
    finalize_at_session_end: bool,
) -> (usize, bool) {
    let classification_lines = if finalize_at_session_end {
        strip_session_end_exit_control_suffix(parsed_lines)
    } else {
        parsed_lines
    };
    let last_conversation_role = classification_lines.iter().rev().find_map(|message| {
        if message
            .get("isMeta")
            .and_then(Value::as_bool)
            .unwrap_or(false)
            || is_tool_result(message)
        {
            return None;
        }
        match get_role(message) {
            "user" => Some("user"),
            "assistant" => Some("assistant"),
            _ => None,
        }
    });
    if last_conversation_role == Some("user") {
        return (turns.len(), true);
    }

    let Some(turn) = turns.last() else {
        return (0, !classification_lines.is_empty());
    };
    let Some(last_assistant) = turn.assistants.last() else {
        return (turns.len().saturating_sub(1), true);
    };

    let used: std::collections::HashSet<&str> = turn
        .assistants
        .iter()
        .flat_map(|assistant| assistant.tool_uses.iter().map(|tool| tool.id.as_str()))
        .filter(|id| !id.is_empty())
        .collect();
    let returned: std::collections::HashSet<&str> = turn
        .tool_results
        .iter()
        .flat_map(|result| result.items.iter().map(|item| item.tool_use_id.as_str()))
        .filter(|id| !id.is_empty())
        .collect();
    let has_unmatched_tool = used.iter().any(|id| !returned.contains(id));
    let completed_structured_output = finalize_at_session_end
        && !has_unmatched_tool
        && last_assistant.tool_uses.iter().any(|tool| {
            tool.name == "StructuredOutput"
                && !tool.id.is_empty()
                && returned.contains(tool.id.as_str())
        });

    let incomplete = match last_assistant.stop_reason.as_str() {
        "end_turn" | "stop_sequence" => has_unmatched_tool,
        "tool_use" | "pause_turn" => !completed_structured_output,
        "" => {
            // Streaming providers can emit text/thinking chunks with placeholder
            // usage={0,0} before appending the real terminal chunk for the same
            // message.id. Do not let that placeholder end transcript polling.
            // A legacy text-only message with no usage remains terminal-compatible.
            let ambiguous_empty_stop_record =
                last_assistant.usage.has_any() && last_assistant.usage.all_zero();
            let compatibility_terminal = turn.cancelled
                || last_assistant.is_synthetic
                || !turn.api_errors.is_empty()
                || ((!ambiguous_empty_stop_record || finalize_at_session_end)
                    && (!last_assistant.text.is_empty() || last_assistant.usage.has_any()));
            !compatibility_terminal || has_unmatched_tool
        }
        _ => has_unmatched_tool,
    };
    (
        turns.len().saturating_sub(usize::from(incomplete)),
        incomplete,
    )
}

/// Claude Code appends `/exit` as a synthetic local-command triplet after the
/// final assistant turn. These records have `role=user`, but they are lifecycle
/// control data and never receive an assistant response. Ignore the complete
/// triplet only at SessionEnd so ordinary Stop still treats a real user-only
/// tail as incomplete.
fn strip_session_end_exit_control_suffix(parsed_lines: &[Value]) -> &[Value] {
    let mut end = parsed_lines.len();
    while end > 0 && is_local_command_output(&parsed_lines[end - 1]) {
        end -= 1;
    }
    if end == 0 || !is_exit_command(&parsed_lines[end - 1]) {
        return parsed_lines;
    }
    end -= 1;
    if end == 0 || !is_local_command_caveat(&parsed_lines[end - 1]) {
        return parsed_lines;
    }
    &parsed_lines[..end - 1]
}

fn is_local_command_caveat(message: &Value) -> bool {
    message
        .get("isMeta")
        .and_then(Value::as_bool)
        .unwrap_or(false)
        && get_content_value(message)
            .map(extract_text)
            .is_some_and(|text| {
                let text = text.trim();
                text.starts_with("<local-command-caveat>")
                    && text.ends_with("</local-command-caveat>")
            })
}

fn is_exit_command(message: &Value) -> bool {
    get_role(message) == "user"
        && get_content_value(message)
            .map(extract_text)
            .is_some_and(|text| {
                text.contains("<command-name>/exit</command-name>")
                    && text.contains("<command-message>exit</command-message>")
            })
}

fn is_local_command_output(message: &Value) -> bool {
    if get_role(message) != "user" {
        return false;
    }
    get_content_value(message)
        .map(extract_text)
        .is_some_and(|text| {
            let text = text.trim();
            (text.starts_with("<local-command-stdout>")
                && text.ends_with("</local-command-stdout>"))
                || (text.starts_with("<local-command-stderr>")
                    && text.ends_with("</local-command-stderr>"))
        })
}

const SESSION_END_EXIT_TAIL_PROBE_BYTES: u64 = 64 * 1024;

/// Checks only a bounded transcript suffix so legacy pending SessionEnd intents
/// can be retried after the `/exit` classification fix without rescanning the
/// conversation. A partial first line is discarded when the probe starts in
/// the middle of a large JSONL record.
pub fn transcript_ends_with_session_exit_control(transcript_path: &Path) -> Result<bool, String> {
    let mut file = File::open(transcript_path).map_err(|error| {
        format!(
            "open SessionEnd transcript tail '{}': {error}",
            transcript_path.display()
        )
    })?;
    let file_len = file
        .metadata()
        .map_err(|error| {
            format!(
                "stat SessionEnd transcript tail '{}': {error}",
                transcript_path.display()
            )
        })?
        .len();
    let start = file_len.saturating_sub(SESSION_END_EXIT_TAIL_PROBE_BYTES);
    file.seek(SeekFrom::Start(start)).map_err(|error| {
        format!(
            "seek SessionEnd transcript tail '{}': {error}",
            transcript_path.display()
        )
    })?;
    let mut tail = Vec::with_capacity((file_len - start) as usize);
    file.read_to_end(&mut tail).map_err(|error| {
        format!(
            "read SessionEnd transcript tail '{}': {error}",
            transcript_path.display()
        )
    })?;
    if start > 0 {
        let Some(first_newline) = tail.iter().position(|byte| *byte == b'\n') else {
            return Ok(false);
        };
        tail.drain(..=first_newline);
    }

    let mut records = Vec::new();
    for line in tail.split(|byte| *byte == b'\n') {
        let line = line.strip_suffix(b"\r").unwrap_or(line);
        if line.iter().all(u8::is_ascii_whitespace) {
            continue;
        }
        let record = match serde_json::from_slice::<Value>(line) {
            Ok(record) => record,
            Err(_) => return Ok(false),
        };
        records.push(record);
    }
    Ok(strip_session_end_exit_control_suffix(&records).len() < records.len())
}

#[cfg(test)]
mod tests {
    use super::super::turns::build_turns;
    use super::super::types::{TokenUsage, ToolUse};
    use super::*;
    use serde_json::{json, Value};

    #[test]
    fn realtime_terminality_requires_native_id_usage_and_tool_stop() {
        let assistant = AssistantMessage {
            timestamp: "2026-01-01T00:00:00Z".to_string(),
            end_timestamp: "2026-01-01T00:00:01Z".to_string(),
            text: String::new(),
            media_content: serde_json::Value::Null,
            model: "claude".to_string(),
            usage: TokenUsage {
                input: Some(1),
                output: Some(1),
                cache_creation_input_tokens: None,
                cache_read_input_tokens: None,
            },
            tool_uses: vec![ToolUse {
                name: "Read".to_string(),
                id: "tool-1".to_string(),
                input: Value::Null,
            }],
            message_id: "msg-1".to_string(),
            stop_reason: "tool_use".to_string(),
            thinking_blocks: Vec::new(),
            is_synthetic: false,
        };
        assert!(is_realtime_terminal_generation(&assistant));
        let mut legacy = assistant.clone();
        legacy.message_id.clear();
        assert!(!is_realtime_terminal_generation(&legacy));
        let mut streaming = assistant;
        streaming.usage.output = Some(0);
        streaming.usage.input = Some(0);
        assert!(!is_realtime_terminal_generation(&streaming));
    }

    #[test]
    fn postable_classification_retains_user_and_tool_use_tails() {
        let user_tail = vec![
            json!({"type":"user","uuid":"turn-1","message":{"role":"user","content":"one"},"timestamp":"2026-07-13T00:00:00.000Z"}),
            json!({"type":"assistant","message":{"role":"assistant","id":"msg-1","content":[{"type":"text","text":"done"}],"stop_reason":"end_turn"},"timestamp":"2026-07-13T00:00:01.000Z"}),
            json!({"type":"user","uuid":"turn-2","message":{"role":"user","content":"two"},"timestamp":"2026-07-13T00:00:02.000Z"}),
        ];
        let turns = build_turns(&user_tail, None);
        assert_eq!(classify_postable_turns(&user_tail, &turns), (1, true));

        let tool_tail = vec![
            json!({"type":"user","uuid":"turn-1","message":{"role":"user","content":"one"},"timestamp":"2026-07-13T00:00:00.000Z"}),
            json!({"type":"assistant","message":{"role":"assistant","id":"msg-1","content":[{"type":"tool_use","id":"tool-1","name":"Bash","input":{"command":"true"}}],"stop_reason":"tool_use"},"timestamp":"2026-07-13T00:00:01.000Z"}),
        ];
        let turns = build_turns(&tool_tail, None);
        assert_eq!(classify_postable_turns(&tool_tail, &turns), (0, true));

        let legacy_terminal = vec![
            json!({"type":"user","message":{"role":"user","content":"legacy"},"timestamp":"2026-07-13T00:00:00.000Z"}),
            json!({"type":"assistant","message":{"role":"assistant","id":"msg-legacy","content":[{"type":"text","text":"done"}]},"timestamp":"2026-07-13T00:00:01.000Z"}),
        ];
        let turns = build_turns(&legacy_terminal, None);
        assert_eq!(
            classify_postable_turns(&legacy_terminal, &turns),
            (1, false)
        );

        let legacy_unmatched_tool = vec![
            json!({"type":"user","message":{"role":"user","content":"legacy tool"},"timestamp":"2026-07-13T00:00:00.000Z"}),
            json!({"type":"assistant","message":{"role":"assistant","id":"msg-legacy","content":[{"type":"tool_use","id":"tool-legacy","name":"Bash","input":{"command":"true"}}]},"timestamp":"2026-07-13T00:00:01.000Z"}),
        ];
        let turns = build_turns(&legacy_unmatched_tool, None);
        assert_eq!(
            classify_postable_turns(&legacy_unmatched_tool, &turns),
            (0, true)
        );
    }

    #[test]
    fn postable_classification_waits_for_nonzero_stream_terminal() {
        let zero_usage_chunk = vec![
            json!({"type":"user","uuid":"turn-1","message":{"role":"user","content":"streamed"},"timestamp":"2026-07-17T09:55:14.016Z"}),
            json!({"type":"assistant","message":{"role":"assistant","id":"msg-stream","model":"GLM-5.2","content":[{"type":"text","text":"partial"}],"usage":{"input_tokens":0,"output_tokens":0}},"timestamp":"2026-07-17T09:55:19.850Z"}),
        ];
        let turns = build_turns(&zero_usage_chunk, None);
        assert_eq!(
            classify_postable_turns(&zero_usage_chunk, &turns),
            (0, true)
        );
        assert_eq!(
            classify_postable_turns_at_session_end(&zero_usage_chunk, &turns),
            (1, false),
            "SessionEnd must finalize an ambiguous zero-usage terminal"
        );

        let mut completed_stream = zero_usage_chunk;
        completed_stream.push(json!({"type":"assistant","message":{"role":"assistant","id":"msg-stream","model":"GLM-5.2","content":[{"type":"text","text":"done"}],"stop_reason":"end_turn","usage":{"input_tokens":1682,"cache_creation_input_tokens":0,"cache_read_input_tokens":78464,"output_tokens":956}},"timestamp":"2026-07-17T09:55:22.226Z"}));
        let turns = build_turns(&completed_stream, None);
        assert_eq!(
            classify_postable_turns(&completed_stream, &turns),
            (1, false)
        );
        assert_eq!(turns[0].assistants[0].usage.input, Some(1682));
        assert_eq!(
            turns[0].assistants[0].usage.cache_read_input_tokens,
            Some(78464)
        );
        assert_eq!(turns[0].assistants[0].usage.output, Some(956));
    }

    #[test]
    fn session_end_ignores_complete_exit_control_suffix() {
        let exit_suffix = [
            json!({"type":"user","message":{"role":"user","content":"<local-command-caveat>Caveat: local command records follow.</local-command-caveat>"},"isMeta":true,"timestamp":"2026-08-18T06:14:53.263Z"}),
            json!({"type":"user","message":{"role":"user","content":"<command-name>/exit</command-name>\n<command-message>exit</command-message>\n<command-args></command-args>"},"timestamp":"2026-08-18T06:14:53.263Z"}),
            json!({"type":"user","message":{"role":"user","content":"<local-command-stdout>Catch you later!</local-command-stdout>"},"timestamp":"2026-08-18T06:14:53.263Z"}),
        ];
        let mut completed = vec![
            json!({"type":"user","uuid":"turn-1","message":{"role":"user","content":"one"},"timestamp":"2026-08-18T06:14:50.000Z"}),
            json!({"type":"assistant","message":{"role":"assistant","id":"msg-1","content":[{"type":"text","text":"done"}],"stop_reason":"end_turn"},"timestamp":"2026-08-18T06:14:51.000Z"}),
        ];
        completed.extend(exit_suffix.clone());
        let turns = build_turns(&completed, None);

        assert_eq!(turns.len(), 1);
        assert_eq!(classify_postable_turns(&completed, &turns), (1, true));
        assert_eq!(
            classify_postable_turns_at_session_end(&completed, &turns),
            (1, false)
        );

        let exit_only = exit_suffix.to_vec();
        assert_eq!(
            classify_postable_turns_at_session_end(&exit_only, &build_turns(&exit_only, None)),
            (0, false)
        );
    }

    #[test]
    fn session_end_keeps_real_user_tail_before_exit_incomplete() {
        let lines = vec![
            json!({"type":"user","uuid":"turn-1","message":{"role":"user","content":"one"},"timestamp":"2026-08-18T06:14:50.000Z"}),
            json!({"type":"assistant","message":{"role":"assistant","id":"msg-1","content":[{"type":"text","text":"done"}],"stop_reason":"end_turn"},"timestamp":"2026-08-18T06:14:51.000Z"}),
            json!({"type":"user","uuid":"turn-2","message":{"role":"user","content":"unanswered"},"timestamp":"2026-08-18T06:14:52.000Z"}),
            json!({"type":"user","message":{"role":"user","content":"<local-command-caveat>Caveat: local command records follow.</local-command-caveat>"},"isMeta":true,"timestamp":"2026-08-18T06:14:53.263Z"}),
            json!({"type":"user","message":{"role":"user","content":"<command-name>/exit</command-name>\n<command-message>exit</command-message>\n<command-args></command-args>"},"timestamp":"2026-08-18T06:14:53.263Z"}),
            json!({"type":"user","message":{"role":"user","content":"<local-command-stdout>Bye!</local-command-stdout>"},"timestamp":"2026-08-18T06:14:53.263Z"}),
        ];
        let turns = build_turns(&lines, None);
        assert_eq!(
            classify_postable_turns_at_session_end(&lines, &turns),
            (1, true)
        );
    }

    #[test]
    fn session_end_finalizes_completed_structured_output_tool_result() {
        let lines = vec![
            json!({"type":"user","uuid":"turn-1","message":{"role":"user","content":"return structured output"},"timestamp":"2026-08-24T00:00:00.000Z"}),
            json!({"type":"assistant","uuid":"assistant-1","message":{"role":"assistant","id":"msg-1","content":[{"type":"tool_use","id":"tool-structured","name":"StructuredOutput","input":{"visible_markdown":"done"}}],"stop_reason":"tool_use","usage":{"input_tokens":10,"output_tokens":5}},"timestamp":"2026-08-24T00:00:01.000Z"}),
            json!({"type":"user","uuid":"result-1","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"tool-structured","content":"{\"visible_markdown\":\"done\"}"}]},"timestamp":"2026-08-24T00:00:02.000Z"}),
            json!({"type":"last-prompt","sessionId":"session-structured"}),
        ];
        let turns = build_turns(&lines, None);

        assert_eq!(turns.len(), 1);
        assert_eq!(classify_postable_turns(&lines, &turns), (0, true));
        assert_eq!(
            classify_postable_turns_at_session_end(&lines, &turns),
            (1, false)
        );
    }

    #[test]
    fn session_end_finalizes_sdk_structured_output_with_attachment_tail() {
        let lines = vec![
            json!({"type":"queue-operation"}),
            json!({"type":"queue-operation"}),
            json!({"type":"user","uuid":"turn-1","message":{"role":"user","content":"return structured output"},"timestamp":"2026-08-24T00:00:00.000Z"}),
            json!({"type":"assistant","uuid":"assistant-1","message":{"role":"assistant","id":"msg-1","model":"k3","content":[{"type":"tool_use","id":"tool-weather","name":"mcp__demo__weather","input":{"city":"上海"}}],"stop_reason":"tool_use","usage":{"input_tokens":10,"output_tokens":5}},"timestamp":"2026-08-24T00:00:01.000Z"}),
            json!({"type":"user","uuid":"result-1","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"tool-weather","content":[{"type":"text","text":"上海今天小雨"}]}]},"toolUseResult":[{"type":"text","text":"上海今天小雨"}],"timestamp":"2026-08-24T00:00:02.000Z"}),
            json!({"type":"assistant","uuid":"assistant-2","message":{"role":"assistant","id":"msg-2","model":"k3","content":[{"type":"tool_use","id":"tool-commute","name":"mcp__demo__commute_advice","input":{"weather":"上海今天小雨"}}],"stop_reason":"tool_use","usage":{"input_tokens":10,"output_tokens":5}},"timestamp":"2026-08-24T00:00:03.000Z"}),
            json!({"type":"user","uuid":"result-2","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"tool-commute","content":[{"type":"text","text":"携带雨具"}]}]},"toolUseResult":[{"type":"text","text":"携带雨具"}],"timestamp":"2026-08-24T00:00:04.000Z"}),
            json!({"type":"assistant","uuid":"assistant-3","message":{"role":"assistant","id":"msg-3","model":"k3","content":[{"type":"tool_use","id":"tool-structured","name":"StructuredOutput","input":{"visible_markdown":"done"}}],"stop_reason":"tool_use","usage":{"input_tokens":10,"output_tokens":5}},"timestamp":"2026-08-24T00:00:05.000Z"}),
            json!({"type":"attachment","uuid":"structured-attachment","parentUuid":"assistant-3","attachment":{"type":"structured_output","data":{"visible_markdown":"done"},"toolUseID":"tool-structured"},"timestamp":"2026-08-24T00:00:06.000Z"}),
            json!({"type":"user","uuid":"result-3","parentUuid":"assistant-3","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"tool-structured","content":"Structured output provided successfully"}]},"toolUseResult":"Structured output provided successfully","toolEndsTurn":true,"sourceToolAssistantUUID":"assistant-3","timestamp":"2026-08-24T00:00:07.000Z"}),
            json!({"type":"last-prompt","lastPrompt":"return structured output","leafUuid":"result-3","sessionId":"session-structured"}),
        ];
        let turns = build_turns(&lines, None);

        assert_eq!(turns.len(), 1);
        assert_eq!(turns[0].assistants.len(), 3);
        assert_eq!(turns[0].tool_results.len(), 3);
        assert_eq!(classify_postable_turns(&lines, &turns), (0, true));
        assert_eq!(
            classify_postable_turns_at_session_end(&lines, &turns),
            (1, false)
        );
    }

    #[test]
    fn session_end_does_not_finalize_ordinary_tool_result_tail() {
        let lines = vec![
            json!({"type":"user","uuid":"turn-1","message":{"role":"user","content":"run a tool"},"timestamp":"2026-08-24T00:00:00.000Z"}),
            json!({"type":"assistant","uuid":"assistant-1","message":{"role":"assistant","id":"msg-1","content":[{"type":"tool_use","id":"tool-1","name":"Bash","input":{"command":"true"}}],"stop_reason":"tool_use","usage":{"input_tokens":10,"output_tokens":5}},"timestamp":"2026-08-24T00:00:01.000Z"}),
            json!({"type":"user","uuid":"result-1","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"tool-1","content":"ok"}]},"timestamp":"2026-08-24T00:00:02.000Z"}),
        ];
        let turns = build_turns(&lines, None);

        assert_eq!(turns.len(), 1);
        assert_eq!(
            classify_postable_turns_at_session_end(&lines, &turns),
            (0, true)
        );
    }

    #[test]
    fn session_end_requires_caveat_before_ignoring_exit_like_text() {
        let lines = vec![
            json!({"type":"user","uuid":"turn-1","message":{"role":"user","content":"one"},"timestamp":"2026-08-18T06:14:50.000Z"}),
            json!({"type":"assistant","message":{"role":"assistant","id":"msg-1","content":[{"type":"text","text":"done"}],"stop_reason":"end_turn"},"timestamp":"2026-08-18T06:14:51.000Z"}),
            json!({"type":"user","message":{"role":"user","content":"<command-name>/exit</command-name>\n<command-message>exit</command-message>"},"timestamp":"2026-08-18T06:14:53.263Z"}),
            json!({"type":"user","message":{"role":"user","content":"<local-command-stdout>quoted output</local-command-stdout>"},"timestamp":"2026-08-18T06:14:53.263Z"}),
        ];
        let turns = build_turns(&lines, None);
        assert_eq!(
            classify_postable_turns_at_session_end(&lines, &turns),
            (1, true)
        );
    }

    #[test]
    fn bounded_session_end_tail_probe_detects_exit_after_large_record() {
        let dir = tempfile::tempdir().unwrap();
        let transcript_path = dir.path().join("session.jsonl");
        let lines = [
            json!({"type":"user","message":{"role":"user","content":"x".repeat(70 * 1024)}}),
            json!({"type":"user","message":{"role":"user","content":"<local-command-caveat>Caveat: local command records follow.</local-command-caveat>"},"isMeta":true}),
            json!({"type":"user","message":{"role":"user","content":"<command-name>/exit</command-name>\n<command-message>exit</command-message>\n<command-args></command-args>"}}),
            json!({"type":"user","message":{"role":"user","content":"<local-command-stdout>Bye!</local-command-stdout>"}}),
        ];
        let content = lines
            .iter()
            .map(Value::to_string)
            .collect::<Vec<_>>()
            .join("\n")
            + "\n";
        std::fs::write(&transcript_path, content).unwrap();
        assert!(transcript_ends_with_session_exit_control(&transcript_path).unwrap());

        std::fs::write(&transcript_path, format!("{}\n{}\n", lines[0], lines[2])).unwrap();
        assert!(!transcript_ends_with_session_exit_control(&transcript_path).unwrap());
    }
}
