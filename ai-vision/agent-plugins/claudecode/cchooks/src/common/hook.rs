use serde_json::Value;

use crate::common::transcript::split_non_empty_lines;

// Re-export shared hook utilities from agentic-otel-common
#[allow(unused_imports)]
pub use agentic_otel_common::hook::{
    extract_session_id, extract_transcript_path, payload_for_log, read_hook_payload,
    read_hook_payload_with_raw, wait_for_file_stable,
};

pub fn env_or_default(primary: &str, secondary: &str, default_value: &str) -> String {
    env_prefixed(primary, secondary).unwrap_or_else(|| default_value.to_string())
}

pub fn env_prefixed(primary: &str, fallback: &str) -> Option<String> {
    crate::common::env::var(primary)
        .ok()
        .filter(|value| !value.is_empty())
        .or_else(|| {
            crate::common::env::var(fallback)
                .ok()
                .filter(|value| !value.is_empty())
        })
}

pub fn env_equals_ignore_case(key: &str, expected: &str) -> bool {
    crate::common::env::var(key)
        .map(|value| value.eq_ignore_ascii_case(expected))
        .unwrap_or(false)
}

pub fn env_is_truthy(key: &str) -> bool {
    crate::common::env::var(key)
        .map(|value| matches!(value.to_ascii_lowercase().as_str(), "true" | "1" | "yes"))
        .unwrap_or(false)
}

pub fn is_telemetry_enabled(key: &str) -> bool {
    match crate::common::env::var(key)
        .ok()
        .filter(|value| !value.is_empty())
    {
        Some(value) => !value.eq_ignore_ascii_case("false") && value != "0",
        None => true,
    }
}

pub fn otlp_traces_endpoint(key: &str) -> Option<String> {
    crate::common::env::var(key)
        .ok()
        .filter(|value| !value.is_empty())
}

/// Read env var with CC_ prefix priority: try `cc_key` first, fall back to `fallback_key`.
pub fn env_cc(cc_key: &str, fallback_key: &str) -> Option<String> {
    env_prefixed(cc_key, fallback_key)
}

/// Return whether a Claude Code tool name denotes subagent orchestration.
pub fn is_subagent_orchestration_tool(tool_name: &str) -> bool {
    matches!(tool_name, "Agent" | "Task")
}

/// Build a ureq Agent with the given timeout.
/// When `AIVISION_TLS_NOVERIFY=true`, TLS certificate verification is disabled.
#[allow(dead_code)]
pub fn build_http_agent(timeout_secs: u64) -> ureq::Agent {
    build_http_agent_with_timeout_and_redirects(std::time::Duration::from_secs(timeout_secs), true)
}

pub fn build_http_agent_with_timeout_and_redirects(
    timeout: std::time::Duration,
    follow_redirects: bool,
) -> ureq::Agent {
    let mut builder = ureq::Agent::config_builder().timeout_global(Some(timeout));
    if !follow_redirects {
        builder = builder.max_redirects(0);
    }
    #[cfg(test)]
    {
        builder = builder.proxy(None);
    }
    let mut tls_builder =
        ureq::tls::TlsConfig::builder().root_certs(ureq::tls::RootCerts::PlatformVerifier);
    if env_equals_ignore_case("AIVISION_TLS_NOVERIFY", "true") {
        tls_builder = tls_builder.disable_verification(true);
    }
    builder = builder.tls_config(tls_builder.build());
    builder.build().new_agent()
}

/// Run an operation once, retrying it exactly once after the first failure.
///
/// The callback runs before the retry so callers can record the first failure
/// without treating it as the final outcome.
pub fn retry_once<T, E, F, R>(mut operation: F, on_retry: R) -> Result<T, E>
where
    F: FnMut() -> Result<T, E>,
    R: FnOnce(&E),
{
    match operation() {
        Ok(value) => Ok(value),
        Err(first_error) => {
            on_retry(&first_error);
            operation()
        }
    }
}

#[allow(dead_code)]
pub fn env_non_empty(key: &str) -> String {
    crate::common::env::var(key).unwrap_or_default()
}

/// Find the latest transcript file by scanning the projects directory.
pub struct LatestTranscript {
    pub session_id: String,
    pub transcript_path: String,
}

pub fn find_latest_transcript(projects_dir: &str) -> Option<LatestTranscript> {
    use std::fs;
    use std::path::Path;
    use std::time::SystemTime;

    let projects_path = Path::new(projects_dir);
    let Ok(entries) = fs::read_dir(projects_path) else {
        return None;
    };

    let mut latest_path = String::new();
    let mut latest_mtime = SystemTime::UNIX_EPOCH;

    for entry in entries.flatten() {
        if !entry.file_type().map(|t| t.is_dir()).unwrap_or(false) {
            continue;
        }
        let sub_dir = entry.path();
        let Ok(sub_entries) = fs::read_dir(&sub_dir) else {
            continue;
        };
        for sub_entry in sub_entries.flatten() {
            let sub_path = sub_entry.path();
            if sub_path.extension().and_then(|e| e.to_str()) != Some("jsonl") {
                continue;
            }
            let Ok(metadata) = sub_entry.metadata() else {
                continue;
            };
            let Ok(mtime) = metadata.modified() else {
                continue;
            };
            if latest_path.is_empty() || mtime > latest_mtime {
                latest_mtime = mtime;
                latest_path = sub_path.to_string_lossy().to_string();
            }
        }
    }

    if latest_path.is_empty() {
        return None;
    }

    let Ok(data) = std::fs::read_to_string(&latest_path) else {
        return None;
    };
    let lines = split_non_empty_lines(&data);
    let first_line = lines.first()?;
    let first: Value = serde_json::from_str(first_line).ok()?;

    let session_id = extract_session_id(&first);
    let session_id = if session_id.is_empty() {
        // Fallback: use filename without extension
        Path::new(&latest_path)
            .file_stem()
            .and_then(|s| s.to_str())
            .unwrap_or("unknown")
            .to_string()
    } else {
        session_id
    };

    Some(LatestTranscript {
        session_id,
        transcript_path: latest_path,
    })
}

/// All Claude Code hook event types.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub enum HookEvent {
    SessionStart,
    Stop,
    StopFailure,
    PreToolUse,
    PostToolUse,
    PostToolUseFailure,
    Notification,
    SubagentStart,
    SubagentStop,
    TaskCreated,
    TaskCompleted,
    TeammateIdle,
    ConfigChange,
    CwdChanged,
    FileChanged,
    WorktreeCreate,
    WorktreeRemove,
    PreCompact,
    PostCompact,
    SessionEnd,
    Elicitation,
    ElicitationResult,
    UserPromptSubmit,
    PermissionRequest,
    InstructionsLoaded,
}

impl HookEvent {
    /// Parse a hook event name string into a HookEvent enum variant.
    pub fn parse(name: &str) -> Option<Self> {
        match name {
            "SessionStart" => Some(Self::SessionStart),
            "Stop" => Some(Self::Stop),
            "StopFailure" => Some(Self::StopFailure),
            "PreToolUse" => Some(Self::PreToolUse),
            "PostToolUse" => Some(Self::PostToolUse),
            "PostToolUseFailure" => Some(Self::PostToolUseFailure),
            "Notification" => Some(Self::Notification),
            "SubagentStart" => Some(Self::SubagentStart),
            "SubagentStop" => Some(Self::SubagentStop),
            "TaskCreated" => Some(Self::TaskCreated),
            "TaskCompleted" => Some(Self::TaskCompleted),
            "TeammateIdle" => Some(Self::TeammateIdle),
            "ConfigChange" => Some(Self::ConfigChange),
            "CwdChanged" => Some(Self::CwdChanged),
            "FileChanged" => Some(Self::FileChanged),
            "WorktreeCreate" => Some(Self::WorktreeCreate),
            "WorktreeRemove" => Some(Self::WorktreeRemove),
            "PreCompact" => Some(Self::PreCompact),
            "PostCompact" => Some(Self::PostCompact),
            "SessionEnd" => Some(Self::SessionEnd),
            "Elicitation" => Some(Self::Elicitation),
            "ElicitationResult" => Some(Self::ElicitationResult),
            "UserPromptSubmit" => Some(Self::UserPromptSubmit),
            "PermissionRequest" => Some(Self::PermissionRequest),
            "InstructionsLoaded" => Some(Self::InstructionsLoaded),
            _ => None,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use agentic_otel_common::hook::expand_home_path;
    use serde_json::json;

    #[test]
    fn test_extract_session_id_direct() {
        let payload = json!({"sessionId": "abc-123"});
        assert_eq!(extract_session_id(&payload), "abc-123");
    }

    #[test]
    fn test_extract_session_id_snake_case() {
        let payload = json!({"session_id": "def-456"});
        assert_eq!(extract_session_id(&payload), "def-456");
    }

    #[test]
    fn test_extract_session_id_nested() {
        let payload = json!({"session": {"id": "ghi-789"}});
        assert_eq!(extract_session_id(&payload), "ghi-789");
    }

    #[test]
    fn test_extract_session_id_empty() {
        let payload = json!({"other": "field"});
        assert_eq!(extract_session_id(&payload), "");
    }

    #[test]
    fn test_extract_transcript_path_direct() {
        let payload = json!({"transcriptPath": "/tmp/test.jsonl"});
        assert_eq!(extract_transcript_path(&payload), "/tmp/test.jsonl");
    }

    #[test]
    fn test_extract_transcript_path_snake_case() {
        let payload = json!({"transcript_path": "/tmp/test2.jsonl"});
        assert_eq!(extract_transcript_path(&payload), "/tmp/test2.jsonl");
    }

    #[test]
    fn test_extract_transcript_path_nested() {
        let payload = json!({"transcript": {"path": "/tmp/test3.jsonl"}});
        assert_eq!(extract_transcript_path(&payload), "/tmp/test3.jsonl");
    }

    #[test]
    fn test_expand_home_path_tilde() {
        let home = expand_home_path("~");
        let result = expand_home_path("~/documents/file.txt");
        assert_eq!(
            result,
            std::path::Path::new(&home)
                .join("documents/file.txt")
                .to_string_lossy()
        );
    }

    #[test]
    fn test_expand_home_path_no_tilde() {
        let result = expand_home_path("/absolute/path/file.txt");
        assert_eq!(result, "/absolute/path/file.txt");
    }

    #[test]
    fn test_env_or_default() {
        let unique_key = "CCHOOKS_TEST_ENV_PRIMARY_12345";
        let unique_key2 = "CCHOOKS_TEST_ENV_SECONDARY_12345";
        // Neither set -> default
        std::env::remove_var(unique_key);
        std::env::remove_var(unique_key2);
        assert_eq!(
            env_or_default(unique_key, unique_key2, "fallback"),
            "fallback"
        );

        // Secondary set -> secondary
        std::env::set_var(unique_key2, "secondary_val");
        assert_eq!(
            env_or_default(unique_key, unique_key2, "fallback"),
            "secondary_val"
        );

        // Primary set -> primary wins
        std::env::set_var(unique_key, "primary_val");
        assert_eq!(
            env_or_default(unique_key, unique_key2, "fallback"),
            "primary_val"
        );

        std::env::remove_var(unique_key);
        std::env::remove_var(unique_key2);
    }

    #[test]
    fn test_env_equals_ignore_case() {
        let unique_key = "CCHOOKS_TEST_IGNORE_CASE_12345";
        std::env::set_var(unique_key, "TrUe");
        assert!(env_equals_ignore_case(unique_key, "true"));
        assert!(env_equals_ignore_case(unique_key, "TRUE"));
        assert!(!env_equals_ignore_case(unique_key, "false"));

        std::env::remove_var(unique_key);
        assert!(!env_equals_ignore_case(unique_key, "true"));
    }

    #[test]
    fn test_hook_event_parse() {
        assert_eq!(
            HookEvent::parse("SessionStart"),
            Some(HookEvent::SessionStart)
        );
        assert_eq!(HookEvent::parse("Stop"), Some(HookEvent::Stop));
        assert_eq!(HookEvent::parse("InvalidEvent"), None);
    }

    #[test]
    fn subagent_orchestration_tool_names_are_centralized() {
        assert!(is_subagent_orchestration_tool("Agent"));
        assert!(is_subagent_orchestration_tool("Task"));
        assert!(!is_subagent_orchestration_tool("Workflow"));
    }

    #[test]
    fn test_retry_once_does_not_retry_success() {
        let mut attempts = 0;
        let result = retry_once(
            || {
                attempts += 1;
                Ok::<_, &str>("ok")
            },
            |_| panic!("successful operation must not retry"),
        );

        assert_eq!(result, Ok("ok"));
        assert_eq!(attempts, 1);
    }

    #[test]
    fn test_retry_once_retries_one_failure() {
        let mut attempts = 0;
        let mut observed_error = None;
        let result = retry_once(
            || {
                attempts += 1;
                if attempts == 1 {
                    Err("temporary")
                } else {
                    Ok("ok")
                }
            },
            |error| observed_error = Some(*error),
        );

        assert_eq!(result, Ok("ok"));
        assert_eq!(attempts, 2);
        assert_eq!(observed_error, Some("temporary"));
    }

    #[test]
    fn test_retry_once_stops_after_second_failure() {
        let mut attempts = 0;
        let result = retry_once(
            || {
                attempts += 1;
                Err::<(), _>(attempts)
            },
            |_| {},
        );

        assert_eq!(result, Err(2));
        assert_eq!(attempts, 2);
    }
}
