use serde::{Deserialize, Serialize};
use std::collections::HashMap;

use serde_json::Value;

use crate::common::transcript::TranscriptSourceEpoch;

pub const SUBAGENT_REGISTRY_FIELD: &str = "subagent_registry";

/// Per-subagent completion record.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SubagentRecord {
    pub agent_id: String,
    pub agent_type: String,
    pub transcript_path: String,
    pub stopped_at: String,
    pub processed: bool,
    pub last_assistant_message: String,
    pub description: String,
    /// Whether this agent was launched with run_in_background=true.
    /// None means unknown (SubagentStop arrived before parent Stop scanned the transcript).
    #[serde(default)]
    pub is_background: Option<bool>,
    /// Stable turn acknowledgement IDs accepted by the remote exporter.
    ///
    /// A contiguous cursor is insufficient because batching can change when a
    /// payload grows or the byte limit changes. Stable IDs let retries skip
    /// acknowledged turns without depending on the previous chunk layout.
    #[serde(default)]
    pub exported_turn_ids: Vec<String>,
}

/// Registry tracking subagent completions for a single parent session.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct SubagentRegistry {
    pub parent_stopped: bool,
    pub parent_stop_time: Option<String>,
    pub agents: HashMap<String, SubagentRecord>,
    #[serde(default)]
    pub parent_discovery_offset: i64,
    #[serde(default)]
    pub parent_observed_file_len: u64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub parent_source_epoch: Option<TranscriptSourceEpoch>,
    #[serde(default)]
    pub pending_agent_tool_uses: HashMap<String, bool>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub parent_scan_terminal_reason: Option<String>,
}

/// Load registry from a session state object. Returns default if absent or malformed.
pub fn registry_from_session(session: &Value) -> SubagentRegistry {
    session
        .get(SUBAGENT_REGISTRY_FIELD)
        .cloned()
        .and_then(|value| serde_json::from_value(value).ok())
        .unwrap_or_default()
}

/// Save registry under the session state object.
pub fn registry_to_session(session: &mut Value, registry: &SubagentRegistry) {
    session[SUBAGENT_REGISTRY_FIELD] =
        serde_json::to_value(registry).unwrap_or_else(|_| serde_json::json!({}));
}

/// Register a subagent completion. Returns true if this is a new registration.
pub fn register_agent_stop(
    registry: &mut SubagentRegistry,
    agent_id: &str,
    agent_type: &str,
    transcript_path: &str,
    stopped_at: &str,
    last_assistant_message: &str,
    description: &str,
) -> bool {
    if registry.agents.contains_key(agent_id) {
        return false; // idempotent
    }
    registry.agents.insert(
        agent_id.to_string(),
        SubagentRecord {
            agent_id: agent_id.to_string(),
            agent_type: agent_type.to_string(),
            transcript_path: transcript_path.to_string(),
            stopped_at: stopped_at.to_string(),
            processed: false,
            last_assistant_message: last_assistant_message.to_string(),
            description: description.to_string(),
            is_background: None, // unknown until parent Stop scans transcript
            exported_turn_ids: Vec::new(),
        },
    );
    true
}

/// Set the is_background flag for an agent (called after parent Stop scans transcript).
pub fn set_agent_background(registry: &mut SubagentRegistry, agent_id: &str, is_background: bool) {
    if let Some(record) = registry.agents.get_mut(agent_id) {
        record.is_background = Some(is_background);
    }
}

/// Mark a subagent as processed.
pub fn mark_agent_processed(registry: &mut SubagentRegistry, agent_id: &str) {
    if let Some(record) = registry.agents.get_mut(agent_id) {
        record.processed = true;
    }
}

/// Persist remotely accepted async turns and mark the agent complete when the
/// caller confirms the full parsed transcript has been delivered.
pub fn record_agent_export_progress(
    registry: &mut SubagentRegistry,
    agent_id: &str,
    accepted_turn_ids: &[String],
    complete: bool,
) {
    let Some(record) = registry.agents.get_mut(agent_id) else {
        return;
    };
    for turn_id in accepted_turn_ids {
        if !record
            .exported_turn_ids
            .iter()
            .any(|known| known == turn_id)
        {
            record.exported_turn_ids.push(turn_id.clone());
        }
    }
    if complete {
        record.processed = true;
    }
}

/// Start a new parent turn without discarding unresolved work from the prior turn.
///
/// Processed records are safe to remove. Active agents, stopped agents whose mode is
/// still unknown, and failed background exports must survive until a parent Stop can
/// classify or retry them.
pub fn begin_parent_turn(registry: &mut SubagentRegistry) -> Vec<SubagentRecord> {
    registry.parent_stopped = false;
    registry.parent_stop_time = None;
    remove_agents_matching(registry, |record| record.processed)
}

/// Remove terminal records after a parent Stop has completed classification/export.
///
/// Unprocessed background and unclassified records remain retryable. Agents
/// without a stop event remain because they may still be running across turns.
pub fn prune_terminal_agents_after_stop(
    registry: &mut SubagentRegistry,
    candidates: &[String],
) -> Vec<SubagentRecord> {
    remove_agents_matching(registry, |record| {
        if !candidates.contains(&record.agent_id) {
            return false;
        }
        if record.stopped_at.is_empty() {
            return false;
        }
        record.processed || record.is_background == Some(false)
    })
}

fn remove_agents_matching(
    registry: &mut SubagentRegistry,
    remove: impl Fn(&SubagentRecord) -> bool,
) -> Vec<SubagentRecord> {
    let removed_ids: Vec<String> = registry
        .agents
        .iter()
        .filter(|(_, record)| remove(record))
        .map(|(agent_id, _)| agent_id.clone())
        .collect();
    removed_ids
        .into_iter()
        .filter_map(|agent_id| registry.agents.remove(&agent_id))
        .collect()
}

/// Read the .meta.json file for a subagent to get description.
/// Expected path: same directory as transcript, agent-{id}.meta.json
pub fn read_subagent_meta(transcript_path: &str) -> (String, String) {
    // transcript_path: .../subagents/agent-{id}.jsonl
    // meta_path:       .../subagents/agent-{id}.meta.json
    let meta_path = transcript_path.replace(".jsonl", ".meta.json");
    match std::fs::read_to_string(&meta_path) {
        Ok(data) => {
            if let Ok(meta) = serde_json::from_str::<serde_json::Value>(&data) {
                let agent_type = meta
                    .get("agentType")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
                let description = meta
                    .get("description")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
                (agent_type, description)
            } else {
                (String::new(), String::new())
            }
        }
        Err(_) => (String::new(), String::new()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_registry_default() {
        let reg = SubagentRegistry::default();
        assert!(!reg.parent_stopped);
        assert!(reg.agents.is_empty());
    }

    #[test]
    fn test_register_agent_stop_new() {
        let mut reg = SubagentRegistry::default();
        let added = register_agent_stop(
            &mut reg,
            "agent-abc",
            "Explore",
            "/tmp/agent-abc.jsonl",
            "2026-04-18T10:00:00Z",
            "Done",
            "search code",
        );
        assert!(added);
        assert_eq!(reg.agents.len(), 1);
        assert_eq!(reg.agents["agent-abc"].agent_type, "Explore");
    }

    #[test]
    fn test_register_agent_stop_idempotent() {
        let mut reg = SubagentRegistry::default();
        register_agent_stop(
            &mut reg,
            "agent-abc",
            "Explore",
            "/tmp/agent-abc.jsonl",
            "2026-04-18T10:00:00Z",
            "Done",
            "search code",
        );
        let added = register_agent_stop(
            &mut reg,
            "agent-abc",
            "Explore",
            "/tmp/agent-abc.jsonl",
            "2026-04-18T10:01:00Z",
            "Done again",
            "search code",
        );
        assert!(!added);
        assert_eq!(reg.agents.len(), 1);
    }

    #[test]
    fn test_mark_agent_processed_and_background() {
        let mut reg = SubagentRegistry::default();
        register_agent_stop(
            &mut reg,
            "agent-a",
            "Explore",
            "/tmp/a.jsonl",
            "2026-04-18T10:00:00Z",
            "Done",
            "",
        );
        register_agent_stop(
            &mut reg,
            "agent-b",
            "Plan",
            "/tmp/b.jsonl",
            "2026-04-18T10:01:00Z",
            "Done",
            "",
        );
        // Initially is_background is None
        assert_eq!(reg.agents["agent-a"].is_background, None);

        set_agent_background(&mut reg, "agent-a", false);
        set_agent_background(&mut reg, "agent-b", true);
        mark_agent_processed(&mut reg, "agent-a");

        assert_eq!(reg.agents["agent-a"].is_background, Some(false));
        assert!(reg.agents["agent-a"].processed);
        assert_eq!(reg.agents["agent-b"].is_background, Some(true));
        assert!(!reg.agents["agent-b"].processed);
    }

    #[test]
    fn test_serialize_roundtrip() {
        let mut reg = SubagentRegistry::default();
        register_agent_stop(
            &mut reg,
            "agent-x",
            "Explore",
            "/tmp/x.jsonl",
            "2026-04-18T10:00:00Z",
            "result text",
            "find auth module",
        );
        reg.parent_stopped = true;
        reg.parent_stop_time = Some("2026-04-18T10:05:00Z".to_string());
        set_agent_background(&mut reg, "agent-x", true);

        let json = serde_json::to_string(&reg).unwrap();
        let restored: SubagentRegistry = serde_json::from_str(&json).unwrap();
        assert!(restored.parent_stopped);
        assert_eq!(restored.agents.len(), 1);
        assert_eq!(restored.agents["agent-x"].description, "find auth module");
        assert_eq!(restored.agents["agent-x"].is_background, Some(true));
    }

    #[test]
    fn begin_parent_turn_resets_boundary_and_only_removes_processed_records() {
        let mut reg = SubagentRegistry {
            parent_stopped: true,
            parent_stop_time: Some("2026-04-18T10:05:00Z".to_string()),
            ..SubagentRegistry::default()
        };
        for agent_id in ["processed", "unknown", "background", "active"] {
            register_agent_stop(
                &mut reg,
                agent_id,
                "Explore",
                &format!("/tmp/{agent_id}.jsonl"),
                if agent_id == "active" {
                    ""
                } else {
                    "2026-04-18T10:00:00Z"
                },
                "Done",
                "",
            );
        }
        mark_agent_processed(&mut reg, "processed");
        set_agent_background(&mut reg, "background", true);

        let removed = begin_parent_turn(&mut reg);

        assert!(!reg.parent_stopped);
        assert_eq!(reg.parent_stop_time, None);
        assert_eq!(removed.len(), 1);
        assert_eq!(removed[0].agent_id, "processed");
        assert!(reg.agents.contains_key("unknown"));
        assert!(reg.agents.contains_key("background"));
        assert!(reg.agents.contains_key("active"));
    }

    #[test]
    fn stop_cleanup_retains_only_active_and_retryable_background_records() {
        let mut reg = SubagentRegistry::default();
        for agent_id in ["processed", "unknown", "sync", "background-retry", "active"] {
            register_agent_stop(
                &mut reg,
                agent_id,
                "Explore",
                &format!("/tmp/{agent_id}.jsonl"),
                if agent_id == "active" {
                    ""
                } else {
                    "2026-04-18T10:00:00Z"
                },
                "Done",
                "",
            );
        }
        mark_agent_processed(&mut reg, "processed");
        set_agent_background(&mut reg, "sync", false);
        set_agent_background(&mut reg, "background-retry", true);

        let candidates = reg.agents.keys().cloned().collect::<Vec<_>>();
        register_agent_stop(
            &mut reg,
            "arrived-during-stop",
            "Explore",
            "/tmp/arrived-during-stop.jsonl",
            "2026-04-18T10:00:01Z",
            "Done",
            "",
        );

        let removed = prune_terminal_agents_after_stop(&mut reg, &candidates);
        let mut removed_ids: Vec<&str> = removed
            .iter()
            .map(|record| record.agent_id.as_str())
            .collect();
        removed_ids.sort_unstable();

        assert_eq!(removed_ids, vec!["processed", "sync"]);
        assert!(reg.agents.contains_key("unknown"));
        assert!(reg.agents.contains_key("background-retry"));
        assert!(reg.agents.contains_key("active"));
        assert!(reg.agents.contains_key("arrived-during-stop"));
    }

    #[test]
    fn stop_cleanup_does_not_remove_agent_that_became_terminal_after_snapshot() {
        let mut reg = SubagentRegistry::default();
        register_agent_stop(
            &mut reg,
            "active-at-snapshot",
            "Explore",
            "/tmp/active-at-snapshot.jsonl",
            "",
            "",
            "",
        );
        let candidates = reg
            .agents
            .values()
            .filter(|record| !record.stopped_at.is_empty())
            .map(|record| record.agent_id.clone())
            .collect::<Vec<_>>();

        let record = reg
            .agents
            .get_mut("active-at-snapshot")
            .expect("registered active agent");
        record.stopped_at = "2026-04-18T10:00:01Z".to_string();
        record.last_assistant_message = "Done".to_string();

        let removed = prune_terminal_agents_after_stop(&mut reg, &candidates);

        assert!(removed.is_empty());
        assert!(reg.agents.contains_key("active-at-snapshot"));
    }

    #[test]
    fn export_progress_is_idempotent_and_completes_only_after_all_turns() {
        let mut reg = SubagentRegistry::default();
        register_agent_stop(
            &mut reg,
            "background",
            "Explore",
            "/tmp/background.jsonl",
            "2026-04-18T10:00:00Z",
            "Done",
            "",
        );
        set_agent_background(&mut reg, "background", true);
        let turns = ["turn-1".to_string(), "turn-2".to_string()];

        record_agent_export_progress(&mut reg, "background", &[turns[0].clone()], false);
        record_agent_export_progress(&mut reg, "background", &[turns[0].clone()], false);

        assert!(!reg.agents["background"].processed);
        assert_eq!(reg.agents["background"].exported_turn_ids, vec!["turn-1"]);

        record_agent_export_progress(&mut reg, "background", &[turns[1].clone()], true);

        assert!(reg.agents["background"].processed);
        assert_eq!(
            reg.agents["background"].exported_turn_ids,
            vec!["turn-1", "turn-2"]
        );
    }

    #[test]
    fn test_registry_session_roundtrip_preserves_sibling_fields() {
        let mut session = serde_json::json!({
            "timing": {"tool-a": {"start_ns": "1"}}
        });
        let mut reg = SubagentRegistry::default();
        register_agent_stop(
            &mut reg,
            "agent-x",
            "Explore",
            "/tmp/x.jsonl",
            "2026-04-18T10:00:00Z",
            "result text",
            "find auth module",
        );

        registry_to_session(&mut session, &reg);
        let restored = registry_from_session(&session);

        assert!(session.get("timing").is_some());
        assert_eq!(restored.agents.len(), 1);
        assert_eq!(restored.agents["agent-x"].agent_type, "Explore");
    }
}
