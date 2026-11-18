use std::path::PathBuf;

const SESSION_KEY_HASH_HEX_CHARS: usize = 16;
const SESSION_KEY_SAFE_PREFIX_CHARS: usize = 80;

#[cfg(test)]
thread_local! {
    static TEST_STATE_DIR: std::cell::RefCell<Option<PathBuf>> = const { std::cell::RefCell::new(None) };
    static TEST_SPOOL_DIR: std::cell::RefCell<Option<PathBuf>> = const { std::cell::RefCell::new(None) };
}

#[cfg(test)]
#[allow(dead_code)]
pub(crate) struct TestRuntimeDirOverride {
    previous_state_dir: Option<PathBuf>,
    previous_spool_dir: Option<PathBuf>,
}

#[cfg(test)]
impl Drop for TestRuntimeDirOverride {
    fn drop(&mut self) {
        TEST_STATE_DIR.with(|value| {
            *value.borrow_mut() = self.previous_state_dir.take();
        });
        TEST_SPOOL_DIR.with(|value| {
            *value.borrow_mut() = self.previous_spool_dir.take();
        });
    }
}

#[cfg(test)]
#[allow(dead_code)]
pub(crate) fn override_runtime_dirs_for_test(
    state_dir: PathBuf,
    spool_dir: PathBuf,
) -> TestRuntimeDirOverride {
    let previous_state_dir = TEST_STATE_DIR.with(|value| value.replace(Some(state_dir)));
    let previous_spool_dir = TEST_SPOOL_DIR.with(|value| value.replace(Some(spool_dir)));
    TestRuntimeDirOverride {
        previous_state_dir,
        previous_spool_dir,
    }
}

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
    if let Ok(home) = std::env::var("HOME") {
        if !home.is_empty() {
            return PathBuf::from(home);
        }
    }
    if let Ok(profile) = std::env::var("USERPROFILE") {
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
/// - default:  CODEX_HOME > ~/.codex
/// - aivision: ~/.aivision/cdx
pub fn root_dir() -> PathBuf {
    let home = home_dir();
    #[cfg(feature = "aivision")]
    {
        home.join(".aivision").join("cdx")
    }
    #[cfg(not(feature = "aivision"))]
    {
        std::env::var_os("CODEX_HOME")
            .filter(|value| !value.is_empty())
            .map(PathBuf::from)
            .unwrap_or_else(|| home.join(".codex"))
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
    aivision_home_from(home_dir(), std::env::var("AIVISION_HOME").ok().as_deref())
}

/// AiVision-owned artifact root for Codex.
pub fn artifact_root_dir() -> PathBuf {
    aivision_home_dir().join("cdx")
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
                .join("cdx")
                .join(kind)
        })
}

/// Async export spool directory: CDXHOOKS_SPOOL_DIR > {AIVISION_HOME}/cdx/spool.
pub fn spool_dir() -> PathBuf {
    env_dir_or("CDXHOOKS_SPOOL_DIR", artifact_root_dir().join("spool"))
}

/// Create an artifact directory with user-only permissions where supported.
pub fn ensure_private_dir(path: &std::path::Path) -> std::io::Result<()> {
    if path.exists() {
        return Ok(());
    }
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

/// Log directory: CDXHOOKS_LOG_DIR > {root}/logs
pub fn log_dir() -> PathBuf {
    env_dir_or("CDXHOOKS_LOG_DIR", root_dir().join("logs"))
}

/// Deterministic, collision-resistant path segment for a session id.
///
/// The human-readable prefix is sanitized for filesystem safety, and a stable
/// SHA-256 suffix prevents two distinct raw ids from sharing one runtime tree.
/// Empty/missing ids intentionally return `None` so hook paths can stay
/// stderr-only without creating persistent cross-session artifacts.
pub fn session_key(session_id: &str) -> Option<String> {
    let raw = session_id.trim();
    if raw.is_empty() {
        return None;
    }

    let sanitized = agentic_otel_common::hook::sanitize_session_id(raw);
    let readable = if sanitized.is_empty() || sanitized.chars().all(|value| value == '.') {
        "session".to_string()
    } else {
        sanitized
            .chars()
            .take(SESSION_KEY_SAFE_PREFIX_CHARS)
            .collect::<String>()
    };

    use sha2::{Digest, Sha256};
    let mut hasher = Sha256::new();
    hasher.update(b"cdxhooks-session-key:");
    hasher.update(raw.as_bytes());
    let suffix = hasher
        .finalize()
        .iter()
        .take(SESSION_KEY_HASH_HEX_CHARS / 2)
        .map(|byte| format!("{:02x}", byte))
        .collect::<String>();

    Some(format!("{}-{}", readable, suffix))
}

/// Canonical per-session runtime state directory: `{state_dir}/{session_key}`.
///
/// New writers must use this flat tree. The nested `sessions/` layout is a
/// read-only migration input exposed through `legacy_session_state_*`.
pub fn session_state_dir(session_id: &str) -> Option<PathBuf> {
    session_key(session_id).map(|key| state_dir().join(key))
}

/// Legacy nested per-session state directory.
pub fn legacy_session_state_dir(session_id: &str) -> Option<PathBuf> {
    session_key(session_id).map(|key| state_dir().join("sessions").join(key))
}

/// Legacy monolithic state file, used only for compatibility reads/import.
pub fn legacy_session_state_file(session_id: &str) -> Option<PathBuf> {
    legacy_session_state_dir(session_id).map(|dir| dir.join("state.json"))
}

/// Compatibility alias for existing code that has not yet migrated.
///
/// New code must call `session_domain_state_file` or another typed helper.
pub fn session_state_file(session_id: &str) -> Option<PathBuf> {
    session_domain_state_file(session_id, "state_compat.json")
}

pub fn session_identity_file(session_id: &str) -> Option<PathBuf> {
    session_domain_state_file(session_id, "state_identity.json")
}

/// New per-session domain file: `{state_dir}/{session_key}/state_<domain>.json`.
pub fn session_domain_state_file(session_id: &str, file_name: &str) -> Option<PathBuf> {
    if !is_safe_domain_file_name(file_name) {
        return None;
    }
    session_state_dir(session_id).map(|dir| dir.join(file_name))
}

pub fn session_turn_seed_file(session_id: &str) -> Option<PathBuf> {
    session_domain_state_file(session_id, "state_turn_seed.json")
}

pub fn session_stop_intent_file(session_id: &str) -> Option<PathBuf> {
    session_domain_state_file(session_id, "state_stop_intent.json")
}

pub fn session_migration_file(session_id: &str) -> Option<PathBuf> {
    session_domain_state_file(session_id, "state_migration.json")
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

pub fn tool_key(tool_use_id: &str) -> Option<String> {
    let raw = tool_use_id.trim();
    if raw.is_empty() {
        return None;
    }
    let sanitized = agentic_otel_common::hook::sanitize_session_id(raw);
    let readable = if sanitized.is_empty() || sanitized.chars().all(|value| value == '.') {
        "tool".to_string()
    } else {
        sanitized
            .chars()
            .take(SESSION_KEY_SAFE_PREFIX_CHARS)
            .collect::<String>()
    };
    use sha2::{Digest, Sha256};
    let mut hasher = Sha256::new();
    hasher.update(b"cdxhooks-tool-key:");
    hasher.update(raw.as_bytes());
    let suffix = hasher
        .finalize()
        .iter()
        .take(SESSION_KEY_HASH_HEX_CHARS / 2)
        .map(|byte| format!("{:02x}", byte))
        .collect::<String>();
    Some(format!("{}-{}", readable, suffix))
}

pub fn tool_event_dir(session_id: &str, tool_use_id: &str) -> Option<PathBuf> {
    let tool_key = tool_key(tool_use_id)?;
    session_state_dir(session_id).map(|dir| dir.join(format!("tool_{tool_key}")))
}

pub fn tool_event_file(session_id: &str, tool_use_id: &str, phase: &str) -> Option<PathBuf> {
    if phase != "pre" && phase != "post" {
        return None;
    }
    tool_event_dir(session_id, tool_use_id).map(|dir| dir.join(format!("{phase}.json")))
}

/// Per-session log file: `{log_dir}/cdxhooks_{sanitized_session_id}.log`.
/// Returns `None` for an empty session id so callers can stay stderr-only.
pub fn session_log_file(session_id: &str) -> Option<PathBuf> {
    if session_id.trim().is_empty() {
        return None;
    }
    let safe = agentic_otel_common::hook::sanitize_session_id(session_id);
    Some(log_dir().join(format!("cdxhooks_{safe}.log")))
}

/// State directory: CDXHOOKS_STATE_DIR > {root}/state
pub fn state_dir() -> PathBuf {
    env_dir_or("CDXHOOKS_STATE_DIR", root_dir().join("state"))
}

fn is_safe_domain_file_name(file_name: &str) -> bool {
    file_name.starts_with("state_")
        && file_name.ends_with(".json")
        && !file_name.contains("..")
        && !file_name.contains('/')
        && !file_name.contains('\\')
        && file_name.chars().all(|value| {
            value.is_ascii_lowercase() || value.is_ascii_digit() || matches!(value, '_' | '.')
        })
}

/// Sessions directory: CODEX_SESSIONS_DIR > ~/.codex/sessions.
pub fn sessions_dir() -> PathBuf {
    env_dir_or(
        "CODEX_SESSIONS_DIR",
        home_dir().join(".codex").join("sessions"),
    )
}

/// Helper: read directory path from env var, fallback to default.
fn env_dir_or(var: &str, default: PathBuf) -> PathBuf {
    #[cfg(test)]
    {
        let override_dir = match var {
            "CDXHOOKS_STATE_DIR" => TEST_STATE_DIR.with(|value| value.borrow().clone()),
            "CDXHOOKS_SPOOL_DIR" => TEST_SPOOL_DIR.with(|value| value.borrow().clone()),
            _ => None,
        };
        if let Some(path) = override_dir {
            return path;
        }
    }

    std::env::var(var)
        .ok()
        .filter(|v| !v.is_empty())
        .map(PathBuf::from)
        .unwrap_or(default)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_edition_name_matches_active_feature() {
        #[cfg(feature = "aivision")]
        assert_eq!(edition_name(), "aivision");
        #[cfg(not(feature = "aivision"))]
        assert_eq!(edition_name(), "default");
    }

    #[test]
    fn test_root_dir_default_and_codex_home() {
        let _guard = crate::test_sync::env_lock();
        let previous = std::env::var_os("CODEX_HOME");
        std::env::remove_var("CODEX_HOME");
        #[cfg(feature = "aivision")]
        let expected = home_dir().join(".aivision").join("cdx");
        #[cfg(not(feature = "aivision"))]
        let expected = home_dir().join(".codex");
        let default = root_dir();
        let override_path = home_dir().join("custom-codex-home");
        std::env::set_var("CODEX_HOME", &override_path);
        let overridden = root_dir();
        match previous {
            Some(value) => std::env::set_var("CODEX_HOME", value),
            None => std::env::remove_var("CODEX_HOME"),
        }
        assert_eq!(default, expected);
        #[cfg(feature = "aivision")]
        assert_eq!(overridden, expected);
        #[cfg(not(feature = "aivision"))]
        assert_eq!(overridden, override_path);
    }

    #[test]
    fn test_log_dir_env_override() {
        let _guard = crate::test_sync::env_lock();
        let key = "CDXHOOKS_LOG_DIR";
        let prev = std::env::var(key).ok();
        std::env::set_var(key, "/custom/logs");
        assert_eq!(log_dir(), PathBuf::from("/custom/logs"));
        match prev {
            Some(v) => std::env::set_var(key, v),
            None => std::env::remove_var(key),
        }
    }

    #[test]
    fn test_session_key_adds_stable_hash_suffix() {
        let first = session_key("abc/def").unwrap();
        let second = session_key("abc:def").unwrap();
        assert!(first.starts_with("abc_def-"), "key={}", first);
        assert!(second.starts_with("abc_def-"), "key={}", second);
        assert_ne!(first, second, "distinct raw ids must not collide");
        assert_eq!(first, session_key("abc/def").unwrap());
        assert!(session_key("").is_none());
        assert!(session_key("   ").is_none());
    }

    #[test]
    fn test_session_paths_are_session_scoped() {
        let _guard = crate::test_sync::env_lock();
        let saved_state_dir = std::env::var("CDXHOOKS_STATE_DIR").ok();
        let saved_log_dir = std::env::var("CDXHOOKS_LOG_DIR").ok();
        std::env::set_var("CDXHOOKS_STATE_DIR", "/tmp/cdxhooks-state-test");
        std::env::set_var("CDXHOOKS_LOG_DIR", "/tmp/cdxhooks-log-test");

        let key = session_key("session-1").unwrap();
        assert_eq!(
            session_state_dir("session-1").unwrap(),
            PathBuf::from("/tmp/cdxhooks-state-test").join(&key)
        );
        assert_eq!(
            session_state_file("session-1").unwrap(),
            PathBuf::from("/tmp/cdxhooks-state-test")
                .join(&key)
                .join("state_compat.json")
        );
        assert_eq!(
            session_domain_state_file("session-1", "state_turn.json").unwrap(),
            PathBuf::from("/tmp/cdxhooks-state-test")
                .join(&key)
                .join("state_turn.json")
        );
        assert_eq!(
            session_worker_lock_file("session-1").unwrap(),
            PathBuf::from("/tmp/cdxhooks-state-test")
                .join(&key)
                .join("worker.lock")
        );
        assert!(session_domain_state_file("session-1", "state.json").is_none());
        assert!(session_domain_state_file("session-1", "state_../bad.json").is_none());
        let tool_event = tool_event_file("session-1", "call/1", "pre").unwrap();
        assert_eq!(
            tool_event.parent().unwrap().parent().unwrap(),
            PathBuf::from("/tmp/cdxhooks-state-test").join(&key)
        );
        assert!(tool_event
            .parent()
            .unwrap()
            .file_name()
            .unwrap()
            .to_string_lossy()
            .starts_with("tool_call_1-"));
        assert!(tool_event_file("session-1", "call/1", "failure").is_none());
        assert_eq!(
            session_log_file("session-1").unwrap(),
            PathBuf::from("/tmp/cdxhooks-log-test").join("cdxhooks_session-1.log")
        );
        assert_eq!(
            session_log_file(r#"windows/session\name:a*b"#).unwrap(),
            PathBuf::from("/tmp/cdxhooks-log-test").join("cdxhooks_windows_session_name_a_b.log")
        );
        assert!(session_state_file("").is_none());
        assert!(session_domain_state_file("", "state_turn.json").is_none());
        assert!(session_log_file("").is_none());
        assert!(session_log_file("   ").is_none());

        match saved_state_dir {
            Some(value) => std::env::set_var("CDXHOOKS_STATE_DIR", value),
            None => std::env::remove_var("CDXHOOKS_STATE_DIR"),
        }
        match saved_log_dir {
            Some(value) => std::env::set_var("CDXHOOKS_LOG_DIR", value),
            None => std::env::remove_var("CDXHOOKS_LOG_DIR"),
        }
    }

    #[test]
    fn test_state_dir_env_override() {
        let _guard = crate::test_sync::env_lock();
        let key = "CDXHOOKS_STATE_DIR";
        let prev = std::env::var(key).ok();
        std::env::set_var(key, "/custom/state");
        assert_eq!(state_dir(), PathBuf::from("/custom/state"));
        match prev {
            Some(v) => std::env::set_var(key, v),
            None => std::env::remove_var(key),
        }
    }

    #[test]
    fn test_sessions_dir() {
        let _guard = crate::test_sync::env_lock();
        let key = "CODEX_SESSIONS_DIR";
        let prev = std::env::var(key).ok();

        // Test env override
        std::env::set_var(key, "/custom/sessions");
        assert_eq!(sessions_dir(), PathBuf::from("/custom/sessions"));

        // Test default (env unset)
        std::env::remove_var(key);
        let default = sessions_dir();
        match prev {
            Some(v) => std::env::set_var(key, v),
            None => {}
        }
        assert_eq!(default, home_dir().join(".codex").join("sessions"));
    }

    #[test]
    fn test_home_dir_fallback() {
        // Serialize against other HOME-mutating tests (e.g.
        // hook::test_expand_home_path) via the shared env lock so this read
        // doesn't race with a concurrent set_var.
        let _guard = crate::test_sync::env_lock();
        if let Ok(home) = std::env::var("HOME") {
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
            PathBuf::from("/fake/home/.aivision/cdx/transcripts")
        );
        assert_eq!(
            artifact_dir_from(
                PathBuf::from("/fake/home"),
                Some("/shared/aivision"),
                Some(""),
                "replays",
            ),
            PathBuf::from("/shared/aivision/cdx/replays")
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

    #[cfg(unix)]
    #[test]
    fn test_ensure_private_dir_preserves_existing_parent_mode() {
        use std::os::unix::fs::PermissionsExt;
        let root = tempfile::tempdir().unwrap();
        let parent = root.path().join("existing");
        std::fs::create_dir(&parent).unwrap();
        std::fs::set_permissions(&parent, std::fs::Permissions::from_mode(0o755)).unwrap();

        let target = parent.join("session-key");
        ensure_private_dir(&target).unwrap();

        assert_eq!(
            std::fs::metadata(&parent).unwrap().permissions().mode() & 0o777,
            0o755
        );
        assert_eq!(
            std::fs::metadata(&target).unwrap().permissions().mode() & 0o777,
            0o700
        );
    }
}
