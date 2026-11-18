use std::path::PathBuf;

use sha2::{Digest, Sha256};

/// Compile-time edition name derived from Cargo features.
pub fn edition_name() -> &'static str {
    #[cfg(feature = "aivision")]
    {
        "aivision"
    }
    #[cfg(not(feature = "aivision"))]
    {
        "default"
    }
}

/// User home directory, compatible with Unix (HOME) and Windows (USERPROFILE).
pub fn home_dir() -> PathBuf {
    if let Ok(home) = crate::common::env::var("HOME") {
        if !home.is_empty() {
            return PathBuf::from(home);
        }
    }
    if let Ok(profile) = crate::common::env::var("USERPROFILE") {
        if !profile.is_empty() {
            return PathBuf::from(profile);
        }
    }
    #[cfg(windows)]
    {
        PathBuf::from(r"C:\Temp")
    }
    #[cfg(not(windows))]
    {
        PathBuf::from("/tmp")
    }
}

/// Root directory determined at compile time by feature flags.
/// - default:  ~/.claude
/// - aivision: ~/.aivision/cc
pub fn root_dir() -> PathBuf {
    let home = home_dir();
    #[cfg(feature = "aivision")]
    {
        home.join(".aivision").join("cc")
    }
    #[cfg(not(feature = "aivision"))]
    {
        std::env::var_os("CLAUDE_CONFIG_DIR").filter(|value| !value.is_empty())
            .map(PathBuf::from).unwrap_or_else(|| home.join(".claude"))
    }
}

fn aivision_home_from(home: PathBuf, configured: Option<&str>) -> PathBuf {
    configured
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| home.join(".aivision"))
}

/// Shared AiVision data root: AIVISION_HOME > ~/.aivision.
pub fn aivision_home_dir() -> PathBuf {
    aivision_home_from(
        home_dir(),
        crate::common::env::var("AIVISION_HOME").ok().as_deref(),
    )
}

/// AiVision-owned artifact root for Claude Code.
pub fn artifact_root_dir() -> PathBuf {
    aivision_home_dir().join("cc")
}

#[cfg(test)]
fn artifact_dir_from(
    home: PathBuf,
    aivision_home: Option<&str>,
    component_override: Option<&str>,
    kind: &str,
) -> PathBuf {
    component_override
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            aivision_home_from(home, aivision_home)
                .join("cc")
                .join(kind)
        })
}

/// Transcript snapshot directory: CCHOOKS_SESSION_DIR > {AIVISION_HOME}/cc/transcripts.
pub fn transcript_dir() -> PathBuf {
    env_dir_or(
        "CCHOOKS_SESSION_DIR",
        artifact_root_dir().join("transcripts"),
    )
}

/// Debug replay directory: CCHOOKS_REPLAY_DIR > {AIVISION_HOME}/cc/replays.
pub fn replay_dir() -> PathBuf {
    env_dir_or("CCHOOKS_REPLAY_DIR", artifact_root_dir().join("replays"))
}

/// Create an artifact directory with user-only permissions where supported.
pub fn ensure_private_dir(path: &std::path::Path) -> std::io::Result<()> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::{DirBuilderExt, PermissionsExt};
        let mut builder = std::fs::DirBuilder::new();
        builder.recursive(true).mode(0o700).create(path)?;
        std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o700))
    }
    #[cfg(not(unix))]
    {
        std::fs::create_dir_all(path)
    }
}

/// Log directory: CCHOOKS_LOG_DIR > {root}/logs
pub fn log_dir() -> PathBuf {
    env_dir_or("CCHOOKS_LOG_DIR", root_dir().join("logs"))
}

/// Per-session log file: `{log_dir}/cchooks_{sanitized_session_id}.log`.
/// Returns `None` for an empty session id so callers fall back to the default
/// single log file.
pub fn session_log_file(session_id: &str) -> Option<PathBuf> {
    if session_id.trim().is_empty() {
        return None;
    }
    let safe = agentic_otel_common::hook::sanitize_session_id(session_id);
    Some(log_dir().join(format!("cchooks_{}.log", safe)))
}

/// State directory: CCHOOKS_STATE_DIR > {root}/state
pub fn state_dir() -> PathBuf {
    env_dir_or("CCHOOKS_STATE_DIR", root_dir().join("state"))
}

/// Collision-resistant directory key for one opaque Claude Code session ID.
///
/// The sanitized prefix keeps paths diagnosable while the hash suffix prevents
/// two IDs that sanitize to the same text from sharing mutable state.
fn opaque_state_key(value: &str, empty_label: &str) -> Option<String> {
    let value = value.trim();
    if value.is_empty() {
        return None;
    }
    let sanitized = agentic_otel_common::hook::sanitize_session_id(value);
    let prefix: String = sanitized.chars().take(48).collect();
    let prefix = if prefix.is_empty() {
        empty_label
    } else {
        &prefix
    };
    let digest = Sha256::digest(value.as_bytes());
    let suffix = digest[..8]
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect::<String>();
    Some(format!("{prefix}-{suffix}"))
}

/// Collision-resistant directory key for one opaque Claude Code session ID.
///
/// The sanitized prefix keeps paths diagnosable while the hash suffix prevents
/// two IDs that sanitize to the same text from sharing mutable state.
pub fn session_state_key(session_id: &str) -> Option<String> {
    opaque_state_key(session_id, "session")
}

/// Collision-resistant directory key for one opaque Claude Code tool call ID.
#[allow(dead_code)]
pub fn tool_state_key(tool_id: &str) -> Option<String> {
    opaque_state_key(tool_id, "tool")
}

fn legacy_session_state_file_in(root: &std::path::Path, session_id: &str) -> Option<PathBuf> {
    let key = session_state_key(session_id)?;
    Some(root.join("sessions").join(key).join("state.json"))
}

#[allow(dead_code)]
fn session_state_dir_in(root: &std::path::Path, session_id: &str) -> Option<PathBuf> {
    let key = session_state_key(session_id)?;
    Some(root.join(key))
}

#[allow(dead_code)]
fn session_domain_state_file_in(
    root: &std::path::Path,
    session_id: &str,
    file_name: &str,
) -> Option<PathBuf> {
    if !is_safe_state_file_name(file_name) {
        return None;
    }
    Some(session_state_dir_in(root, session_id)?.join(file_name))
}

#[allow(dead_code)]
fn tool_hook_state_file_in(
    root: &std::path::Path,
    session_id: &str,
    tool_id: &str,
    phase: &str,
) -> Option<PathBuf> {
    if phase != "pre" && phase != "post" {
        return None;
    }
    let tool_key = tool_state_key(tool_id)?;
    Some(
        session_state_dir_in(root, session_id)?
            .join(format!("tool_{tool_key}"))
            .join(format!("{phase}.json")),
    )
}

#[allow(dead_code)]
fn is_safe_state_file_name(file_name: &str) -> bool {
    file_name.starts_with("state_")
        && file_name.ends_with(".json")
        && file_name
            .chars()
            .all(|ch| ch.is_ascii_lowercase() || ch.is_ascii_digit() || ch == '_' || ch == '.')
        && !file_name.contains("..")
        && !file_name.contains('/')
        && !file_name.contains('\\')
}

/// Legacy per-session mutable state file used only for fallback migration.
pub fn session_state_file(session_id: &str) -> Option<PathBuf> {
    legacy_session_state_file_in(&state_dir(), session_id)
}

/// New per-session state directory: `{state}/{session-key}`.
#[allow(dead_code)]
pub fn session_state_dir(session_id: &str) -> Option<PathBuf> {
    session_state_dir_in(&state_dir(), session_id)
}

pub fn session_worker_lock_file(session_id: &str) -> Option<PathBuf> {
    session_state_dir(session_id).map(|dir| dir.join("worker.lock"))
}

pub fn session_worker_start_file(session_id: &str) -> Option<PathBuf> {
    session_state_dir(session_id).map(|dir| dir.join("worker.starting.json"))
}

pub fn session_worker_queue_file(session_id: &str) -> Option<PathBuf> {
    session_state_dir(session_id).map(|dir| dir.join("worker.queue.jsonl"))
}

pub fn session_worker_queue_lock_file(session_id: &str) -> Option<PathBuf> {
    session_state_dir(session_id).map(|dir| dir.join("worker.queue.lock"))
}

pub fn session_worker_queue_offset_file(session_id: &str) -> Option<PathBuf> {
    session_state_dir(session_id).map(|dir| dir.join("worker.queue.offset.json"))
}

pub fn session_transcript_ingest_receipt_file(
    session_id: &str,
    receipt: &str,
    suffix: &str,
) -> Option<PathBuf> {
    if receipt.is_empty()
        || !receipt
            .chars()
            .all(|character| character.is_ascii_alphanumeric() || character == '-')
        || !matches!(suffix, "request" | "ack")
    {
        return None;
    }
    session_state_dir(session_id)
        .map(|dir| dir.join(format!("transcript.ingest.{receipt}.{suffix}")))
}

pub fn session_identity_file(session_id: &str) -> Option<PathBuf> {
    session_domain_state_file(session_id, "state_identity.json")
}

/// New per-session domain file: `{state}/{session-key}/state_<domain>.json`.
#[allow(dead_code)]
pub fn session_domain_state_file(session_id: &str, file_name: &str) -> Option<PathBuf> {
    session_domain_state_file_in(&state_dir(), session_id, file_name)
}

/// New per-tool hook state file: `{state}/{session-key}/tool_<tool-key>/{pre|post}.json`.
#[allow(dead_code)]
pub fn tool_hook_state_file(session_id: &str, tool_id: &str, phase: &str) -> Option<PathBuf> {
    tool_hook_state_file_in(&state_dir(), session_id, tool_id, phase)
}

/// Create only the new session directory with user-only permissions.
///
/// Existing parents are left unchanged.
pub fn ensure_session_state_parent(state_file: &std::path::Path) -> std::io::Result<()> {
    let parent = state_file.parent().ok_or_else(|| {
        std::io::Error::new(
            std::io::ErrorKind::InvalidInput,
            "session state file has no parent",
        )
    })?;
    if parent.is_dir() {
        return Ok(());
    }
    ensure_private_dir(parent)
}

/// Projects directory: CLAUDE_PROJECTS_DIR > ~/.claude/projects.
pub fn projects_dir() -> PathBuf {
    env_dir_or(
        "CLAUDE_PROJECTS_DIR",
        home_dir().join(".claude").join("projects"),
    )
}

/// Dynamic sessions directory: {state}/dynamic_sessions
pub fn dynamic_sessions_dir() -> PathBuf {
    state_dir().join("dynamic_sessions")
}

/// Helper: read directory path from env var, fallback to default.
fn env_dir_or(var: &str, default: PathBuf) -> PathBuf {
    crate::common::env::var(var)
        .ok()
        .filter(|v| !v.is_empty())
        .map(PathBuf::from)
        .unwrap_or(default)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_edition_name_is_known() {
        let name = edition_name();
        assert!(
            ["default", "aivision"].contains(&name),
            "unexpected edition: {}",
            name
        );
    }

    #[test]
    fn test_root_dir_contains_edition_marker() {
        let root = root_dir();
        let root_str = root.to_string_lossy();
        match edition_name() {
            "aivision" => assert!(root_str.contains(".aivision"), "root={}", root_str),
            "default" => assert!(root_str.contains(".claude"), "root={}", root_str),
            _ => panic!("unknown edition"),
        }
    }

    #[test]
    fn test_log_dir_env_override() {
        let key = "CCHOOKS_LOG_DIR";
        let prev = crate::common::env::var(key).ok();
        std::env::set_var(key, "/custom/logs");
        assert_eq!(log_dir(), PathBuf::from("/custom/logs"));
        match prev {
            Some(v) => std::env::set_var(key, v),
            None => std::env::remove_var(key),
        }
    }

    #[test]
    fn test_state_dir_env_override() {
        let _guard = crate::test_sync::env_lock();
        let key = "CCHOOKS_STATE_DIR";
        let prev = crate::common::env::var(key).ok();
        std::env::set_var(key, "/custom/state");
        assert_eq!(state_dir(), PathBuf::from("/custom/state"));
        match prev {
            Some(v) => std::env::set_var(key, v),
            None => std::env::remove_var(key),
        }
    }

    #[test]
    fn test_dynamic_sessions_dir_under_state() {
        // Verify structural relationship: dynamic_sessions_dir is always
        // state_dir()/dynamic_sessions. We check the suffix to avoid
        // env var races with test_state_dir_env_override in parallel.
        let ds = dynamic_sessions_dir();
        let ds_str = ds.to_string_lossy();
        assert!(
            ds_str.ends_with("/dynamic_sessions") || ds_str.ends_with("\\dynamic_sessions"),
            "dynamic_sessions_dir should end with /dynamic_sessions, got={}",
            ds_str
        );
    }

    #[test]
    fn test_session_state_key_is_stable_and_collision_resistant() {
        let first = session_state_key("a/b").unwrap();
        assert_eq!(first, session_state_key("a/b").unwrap());
        assert_ne!(first, session_state_key("a\\b").unwrap());
        assert!(first.starts_with("a_b-"));
    }

    #[test]
    fn test_session_state_path_blocks_traversal_and_is_session_local() {
        let root = PathBuf::from("/tmp/cchooks-state-test");
        let first = legacy_session_state_file_in(&root, "../../session").unwrap();
        let second = legacy_session_state_file_in(&root, "other-session").unwrap();
        assert!(first.starts_with(root.join("sessions")));
        assert_eq!(first.file_name().unwrap(), "state.json");
        assert_ne!(first.parent(), second.parent());
        assert!(!first.to_string_lossy().contains("/../"));
        assert!(legacy_session_state_file_in(&root, "").is_none());
    }

    #[test]
    fn test_domain_state_path_is_shallow_and_session_local() {
        let root = PathBuf::from("/tmp/cchooks-state-test");
        let path = session_domain_state_file_in(&root, "../../session", "state_turn.json").unwrap();
        assert!(path.starts_with(&root));
        assert_eq!(path.file_name().unwrap(), "state_turn.json");
        assert_eq!(path.parent().unwrap().parent().unwrap(), root);
        assert!(!path.to_string_lossy().contains("/../"));
        assert!(session_domain_state_file_in(&root, "s", "../state_turn.json").is_none());
        assert!(session_domain_state_file_in(&root, "s", "state_turn.tmp").is_none());
    }

    #[test]
    fn test_tool_state_path_blocks_traversal_and_collisions() {
        let root = PathBuf::from("/tmp/cchooks-state-test");
        let first = tool_hook_state_file_in(&root, "s/../x", "a/b", "pre").unwrap();
        let second = tool_hook_state_file_in(&root, "s/../x", "a\\b", "pre").unwrap();
        assert!(first.starts_with(&root));
        assert_eq!(first.file_name().unwrap(), "pre.json");
        assert_eq!(
            first.parent().unwrap().parent().unwrap().parent().unwrap(),
            root
        );
        assert_ne!(first.parent(), second.parent());
        assert!(!first.to_string_lossy().contains("/../"));
        assert!(tool_hook_state_file_in(&root, "s", "tool", "during").is_none());
        assert!(tool_hook_state_file_in(&root, "s", "", "pre").is_none());
    }

    #[cfg(unix)]
    #[test]
    fn test_new_session_state_directory_is_private_without_changing_parent() {
        use std::os::unix::fs::PermissionsExt;

        let temp = tempfile::tempdir().unwrap();
        let root = temp.path().join("existing-state-root");
        std::fs::create_dir(&root).unwrap();
        std::fs::set_permissions(&root, std::fs::Permissions::from_mode(0o755)).unwrap();
        let state_file = legacy_session_state_file_in(&root, "session-1").unwrap();

        ensure_session_state_parent(&state_file).unwrap();

        assert_eq!(
            std::fs::metadata(state_file.parent().unwrap())
                .unwrap()
                .permissions()
                .mode()
                & 0o777,
            0o700
        );
        assert_eq!(
            std::fs::metadata(&root).unwrap().permissions().mode() & 0o777,
            0o755
        );
    }

    #[test]
    fn test_projects_dir_default() {
        let _guard = crate::test_sync::env_lock();
        // Ensure CLAUDE_PROJECTS_DIR is not set for this test
        let key = "CLAUDE_PROJECTS_DIR";
        let prev = crate::common::env::var(key).ok();
        std::env::remove_var(key);

        assert_eq!(projects_dir(), home_dir().join(".claude").join("projects"));

        if let Some(v) = prev {
            std::env::set_var(key, v)
        }
    }

    #[test]
    fn test_home_dir_fallback() {
        // With HOME set (typical Unix), should return it
        if let Ok(home) = crate::common::env::var("HOME") {
            if !home.is_empty() {
                assert_eq!(home_dir(), PathBuf::from(&home));
            }
        }
    }

    #[test]
    fn test_artifact_paths_use_canonical_root_and_overrides() {
        let home = PathBuf::from("/fake/home");
        assert_eq!(
            aivision_home_from(home.clone(), None),
            home.join(".aivision")
        );
        assert_eq!(
            aivision_home_from(home.clone(), Some("")),
            home.join(".aivision")
        );
        assert_eq!(
            aivision_home_from(home, Some("/shared/aivision")),
            PathBuf::from("/shared/aivision")
        );
        assert_eq!(
            artifact_dir_from(PathBuf::from("/fake/home"), None, None, "transcripts"),
            PathBuf::from("/fake/home/.aivision/cc/transcripts")
        );
        assert_eq!(
            artifact_dir_from(
                PathBuf::from("/fake/home"),
                Some("/shared/aivision"),
                Some(""),
                "replays",
            ),
            PathBuf::from("/shared/aivision/cc/replays")
        );
        assert_eq!(
            artifact_dir_from(
                PathBuf::from("/fake/home"),
                Some("/shared/aivision"),
                Some("/custom/replays"),
                "replays",
            ),
            PathBuf::from("/custom/replays")
        );
    }

    #[cfg(unix)]
    #[test]
    fn test_ensure_private_dir_enforces_user_only_mode() {
        use std::os::unix::fs::PermissionsExt;
        let root = tempfile::tempdir().unwrap();
        let target = root.path().join("nested/artifacts");
        ensure_private_dir(&target).unwrap();
        assert_eq!(
            std::fs::metadata(target).unwrap().permissions().mode() & 0o777,
            0o700
        );
    }
}
