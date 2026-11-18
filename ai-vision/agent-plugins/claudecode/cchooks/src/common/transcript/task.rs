use serde_json::Value;

use super::fields::{get_obj_str, get_timestamp};
use super::types::TaskNotification;

/// Extract text between `<tag>` and `</tag>` (flat, non-nested tags).
fn extract_xml_tag(content: &str, tag: &str) -> Option<String> {
    let open = format!("<{}>", tag);
    let close = format!("</{}>", tag);
    let start = content.find(&open)? + open.len();
    let end = content[start..].find(&close)? + start;
    Some(content[start..end].to_string())
}

/// Extract the task result whose arbitrary text may itself contain `</result>`.
/// The protocol's real closing tag is the last one before the trailing usage block.
fn extract_task_result(content: &str) -> Option<String> {
    const OPEN: &str = "<result>";
    const CLOSE: &str = "</result>";
    const USAGE_OPEN: &str = "<usage>";

    let start = content.find(OPEN)? + OPEN.len();
    let search_end = content.rfind(USAGE_OPEN).unwrap_or(content.len());
    if search_end < start {
        return None;
    }
    let end = content[..search_end].rfind(CLOSE)?;
    if end < start {
        return None;
    }
    Some(content[start..end].to_string())
}

/// Parse a `queue-operation` line carrying a `<task-notification>` payload.
/// Returns None for other queue operations (e.g. dequeue, plain enqueues).
pub fn parse_task_notification(msg: &Value) -> Option<TaskNotification> {
    if get_obj_str(msg, "type") != "queue-operation" {
        return None;
    }
    let content = msg.get("content").and_then(|v| v.as_str())?;
    // Scope tag extraction to the inner <task-notification> block so tags
    // elsewhere in the queue payload cannot shadow notification fields.
    let block = extract_xml_tag(content, "task-notification")?;
    let tool_use_id = extract_xml_tag(&block, "tool-use-id")?;
    Some(TaskNotification {
        task_id: extract_xml_tag(&block, "task-id").unwrap_or_default(),
        tool_use_id,
        status: extract_xml_tag(&block, "status").unwrap_or_default(),
        result: extract_task_result(&block).unwrap_or_default(),
        subagent_tokens: extract_xml_tag(&block, "subagent_tokens")
            .and_then(|s| s.trim().parse().ok()),
        tool_uses: extract_xml_tag(&block, "tool_uses").and_then(|s| s.trim().parse().ok()),
        duration_ms: extract_xml_tag(&block, "duration_ms").and_then(|s| s.trim().parse().ok()),
        timestamp: get_timestamp(msg),
    })
}
