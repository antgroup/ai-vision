//! boundary ownership module.

use super::{iso_to_nanos, Turn, Value};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TurnTerminalState {
    Completed,
    Failed,
    Aborted,
    Superseded,
    HeuristicComplete,
    Open,
}

impl Default for TurnTerminalState {
    fn default() -> Self {
        Self::Open
    }
}

/// Heuristic: is this turn "essentially complete" even without `task_complete`?
///
/// Codex Desktop can wait 1–8+ seconds between writing the final
/// `agent_message` and writing `task_complete`. Forcing the hook to wait for
/// `task_complete` means the trace only flushes on the *next* Stop event,
/// which the user perceives as "the previous turn shows up after I send the
/// next prompt." This heuristic lets us flush as soon as the visible work is
/// done.
///
/// A turn looks complete when **both** hold:
/// 1. Every `tool_call` has an `ended_timestamp` (no in-flight tool such as a
///    `wait_agent` still blocking on a sub-agent).
/// 2. There exists an assistant message with `phase == "final_answer"` and
///    non-empty `text` (the model has produced a final answer, not just
///    `reasoning` or `commentary`).
///
/// Turns that use sub-agent coordination are excluded even after `wait_agent`
/// returns. Their transcript can contain a visible final answer before the
/// parent/child lifecycle is durably settled, so they require an authoritative
/// `task_complete` / `turn_aborted` marker.
///
/// Note: we cannot simply check `assistants.last()` because Codex's
/// `token_count` event inserts an empty placeholder assistant entry after the
/// real final answer. We also cannot use "any assistant has text" because
/// `commentary` messages produced mid-task have text but precede the final
/// answer — that would re-introduce the premature-flush bug.
pub fn turn_looks_complete(turn: &Turn) -> bool {
    let has_subagent_coordination = turn.tool_calls.iter().any(|tool_call| {
        matches!(
            tool_call.name.as_str(),
            "spawn_agent"
                | "wait"
                | "wait_agent"
                | "interrupt_agent"
                | "send_input"
                | "resume_agent"
                | "close_agent"
                | "followup_task"
                | "send_message"
                | "list_agents"
        )
    });
    let all_tools_done = turn
        .tool_calls
        .iter()
        .all(|tc| !tc.ended_timestamp.is_empty());
    let has_final_answer = turn
        .assistants
        .iter()
        .any(|a| a.phase == "final_answer" && !a.text.is_empty());
    !has_subagent_coordination && all_tools_done && has_final_answer
}

pub fn mark_turn_heuristic_complete(turn: &mut Turn) {
    turn.completed = false;
    turn.terminal_state = TurnTerminalState::HeuristicComplete;
    turn.completion_timestamp = turn
        .assistants
        .iter()
        .flat_map(|assistant| {
            [
                assistant.timestamp.as_str(),
                assistant.token_count_timestamp.as_str(),
            ]
        })
        .chain(turn.tool_calls.iter().flat_map(|tool| {
            [
                tool.started_timestamp.as_str(),
                tool.ended_timestamp.as_str(),
            ]
        }))
        .filter(|timestamp| !timestamp.is_empty())
        .max_by(|left, right| {
            let left_ns = iso_to_nanos(left).parse::<u128>().unwrap_or(0);
            let right_ns = iso_to_nanos(right).parse::<u128>().unwrap_or(0);
            left_ns.cmp(&right_ns).then_with(|| left.cmp(right))
        })
        .unwrap_or(&turn.user_timestamp)
        .to_string();
}

pub fn split_completed_messages(parsed_lines: &[Value]) -> (Vec<Value>, Vec<Value>) {
    let mut open_turn_start: Option<usize> = None;
    let mut open_turn_id: Option<String> = None;

    for (index, entry) in parsed_lines.iter().enumerate() {
        let entry_type = entry
            .get("type")
            .and_then(|value| value.as_str())
            .unwrap_or("");
        if entry_type != "event_msg" {
            continue;
        }

        let payload = entry.get("payload").cloned().unwrap_or(Value::Null);
        let payload_type = payload
            .get("type")
            .and_then(|value| value.as_str())
            .unwrap_or("");

        let payload_turn_id = payload
            .get("turn_id")
            .and_then(|value| value.as_str())
            .filter(|value| !value.is_empty());

        match payload_type {
            "task_started" => {
                if payload_turn_id.is_some() && payload_turn_id == open_turn_id.as_deref() {
                    continue;
                }
                open_turn_start = Some(index);
                open_turn_id = payload_turn_id.map(str::to_string);
            }
            "task_complete" | "turn_aborted" => {
                if open_turn_id.as_deref().is_none_or(|turn_id| {
                    payload_turn_id.is_none_or(|payload_id| payload_id == turn_id)
                }) {
                    open_turn_start = None;
                    open_turn_id = None;
                }
            }
            _ => {}
        }
    }

    match open_turn_start {
        Some(index) => (
            parsed_lines[..index].to_vec(),
            parsed_lines[index..].to_vec(),
        ),
        None => (parsed_lines.to_vec(), Vec::new()),
    }
}
