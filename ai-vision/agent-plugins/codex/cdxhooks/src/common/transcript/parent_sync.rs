//! parent sync ownership module.

use super::{
    build_turns, correlate_subagent, extract_session_meta_ids, Path, SubagentIdentity,
    SubagentLifecycle, Value,
};

/// Check whether a child session is sync (parent has exact child identity plus
/// wait intent). Scans sibling `.jsonl` files in `dir` to
/// locate the parent transcript by matching `session_meta.payload.id` against
/// `parent_session_id`, then searches that file's content for the wait_agent
/// pattern.
///
/// Returns `true` (safe default) when `child_session_id` is empty or the
/// directory is unreadable, so callers err on the side of skipping uploads
/// rather than producing duplicate traces.
pub fn is_child_sync_in_parent(
    dir: &Path,
    parent_session_id: &str,
    child_session_id: &str,
    child_agent_path: Option<&str>,
) -> bool {
    if child_session_id.is_empty() {
        return true;
    }
    let entries = match std::fs::read_dir(dir) {
        Ok(e) => e,
        Err(_) => return true,
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.extension().and_then(|e| e.to_str()) != Some("jsonl") {
            continue;
        }
        let Ok(content) = std::fs::read_to_string(&path) else {
            continue;
        };
        let meta = extract_session_meta_ids(&content);
        let sid = meta.session_id.as_deref().unwrap_or("");
        if sid != parent_session_id {
            continue;
        }
        let parsed: Vec<Value> = content
            .lines()
            .filter_map(|line| serde_json::from_str(line).ok())
            .collect();
        let child = SubagentIdentity {
            session_id: child_session_id.to_string(),
            agent_path: child_agent_path.map(str::to_string),
        };
        return build_turns(&parsed).iter().rev().any(|turn| {
            let correlation = correlate_subagent(turn, &child, std::slice::from_ref(&child));
            correlation.spawn_call_id.is_some()
                && !correlation.wait_call_ids.is_empty()
                && !matches!(correlation.lifecycle, SubagentLifecycle::AmbiguousOrOrphan)
        });
    }
    true
}
