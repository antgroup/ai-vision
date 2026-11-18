use serde_json::Value;
use std::io::{self, Read};
use std::path::{Path, PathBuf};
use std::sync::mpsc;
use std::thread;
use std::time::Duration;

const STDIN_TIMEOUT_MS: u64 = 1000;

/// Read hook payload from stdin with a 1000ms timeout.
/// Uses std::thread + mpsc::channel + recv_timeout — no tokio required.
/// Returns None if stdin is a TTY, times out, or payload is invalid JSON.
pub fn read_hook_payload() -> Option<Value> {
    read_hook_payload_with_raw().map(|(payload, _)| payload)
}

/// Read and parse the hook payload while retaining its original bytes.
///
/// The raw bytes let short-lived hook parents hand the envelope to a worker
/// without serializing a potentially large tool result a second time.
pub fn read_hook_payload_with_raw() -> Option<(Value, Vec<u8>)> {
    #[cfg(unix)]
    {
        use std::os::unix::io::AsRawFd;
        let stdin_fd = io::stdin().as_raw_fd();
        let is_tty = libc_isatty(stdin_fd);
        if is_tty {
            return None;
        }
    }

    let (sender, receiver) = mpsc::channel::<io::Result<Vec<u8>>>();

    thread::spawn(move || {
        let mut buffer = Vec::new();
        let result = io::stdin().read_to_end(&mut buffer);
        let _ = sender.send(result.map(|_| buffer));
    });

    match receiver.recv_timeout(Duration::from_millis(STDIN_TIMEOUT_MS)) {
        Ok(Ok(data)) => {
            if data.iter().all(u8::is_ascii_whitespace) {
                return None;
            }
            parse_hook_payload_bytes(data)
        }
        Ok(Err(_)) | Err(_) => None,
    }
}

fn parse_hook_payload_bytes(data: Vec<u8>) -> Option<(Value, Vec<u8>)> {
    match serde_json::from_slice(&data) {
        Ok(payload) => Some((payload, data)),
        Err(_) => {
            let lossy = String::from_utf8_lossy(&data).into_owned();
            let payload = serde_json::from_str(&lossy).ok()?;
            Some((payload, lossy.into_bytes()))
        }
    }
}

/// Check if stdin file descriptor is a TTY (Unix only).
#[cfg(unix)]
fn libc_isatty(fd: i32) -> bool {
    extern "C" {
        fn isatty(fd: i32) -> i32;
    }
    unsafe { isatty(fd) != 0 }
}

/// Extract session ID from hook payload, trying multiple key formats.
pub fn extract_session_id(payload: &Value) -> String {
    if let Some(s) = payload.get("sessionId").and_then(|v| v.as_str()) {
        if !s.is_empty() {
            return s.to_string();
        }
    }
    if let Some(s) = payload.get("session_id").and_then(|v| v.as_str()) {
        if !s.is_empty() {
            return s.to_string();
        }
    }
    if let Some(session) = payload.get("session").and_then(|v| v.as_object()) {
        if let Some(s) = session.get("id").and_then(|v| v.as_str()) {
            if !s.is_empty() {
                return s.to_string();
            }
        }
    }
    String::new()
}

/// Extract transcript path from hook payload, expanding `~/` home directory prefix.
pub fn extract_transcript_path(payload: &Value) -> String {
    let raw = payload
        .get("transcriptPath")
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
        .or_else(|| {
            payload
                .get("transcript_path")
                .and_then(|v| v.as_str())
                .filter(|s| !s.is_empty())
        })
        .or_else(|| {
            payload
                .get("transcript")
                .and_then(|v| v.as_object())
                .and_then(|obj| obj.get("path"))
                .and_then(|v| v.as_str())
                .filter(|s| !s.is_empty())
        });

    match raw {
        Some(path) => expand_home_path(path),
        None => String::new(),
    }
}

/// Sanitize a session id into a safe filename component: keep only
/// `[A-Za-z0-9._-]`, replace everything else (incl. path separators and `..`
/// components) with `_`. Prevents path traversal / invalid filenames when a
/// session id is embedded in a log file name.
pub fn sanitize_session_id(session_id: &str) -> String {
    session_id
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '-') {
                c
            } else {
                '_'
            }
        })
        .collect()
}

/// Render a hook payload for log output, truncating every long string value
/// (anywhere in the structure) so verbose tool arguments/responses (a multi-KB
/// file write, an `old_string`/`new_string`, a large `tool_response`, etc.) do
/// not flood the log. Returns a JSON string of a copy — the original payload is
/// never mutated. Any string longer than `MAX` chars is cut to the first `MAX`
/// chars plus a `...[truncated N chars]` marker. Char-boundary safe (never
/// splits a multi-byte UTF-8 char).
pub fn payload_for_log(payload: &Value) -> String {
    let mut clone = payload.clone();
    truncate_long_strings(&mut clone);
    clone.to_string()
}

/// Maximum length (in chars) for any single string value in a logged payload.
const PAYLOAD_LOG_MAX_CHARS: usize = 200;

/// Recursively truncate every overlong string value in a JSON tree in place.
fn truncate_long_strings(value: &mut Value) {
    match value {
        Value::String(s) => {
            let total = s.chars().count();
            if total > PAYLOAD_LOG_MAX_CHARS {
                let head: String = s.chars().take(PAYLOAD_LOG_MAX_CHARS).collect();
                *s = format!(
                    "{}...[truncated {} chars]",
                    head,
                    total - PAYLOAD_LOG_MAX_CHARS
                );
            }
        }
        Value::Array(arr) => {
            for v in arr.iter_mut() {
                truncate_long_strings(v);
            }
        }
        Value::Object(map) => {
            for (_, v) in map.iter_mut() {
                truncate_long_strings(v);
            }
        }
        _ => {}
    }
}

/// Expand `~/` prefix to the user's home directory.
pub fn expand_home_path(raw: &str) -> String {
    if raw.starts_with("~/") {
        let home = home_dir();
        return home.join(&raw[2..]).to_string_lossy().to_string();
    }
    if raw == "~" {
        return home_dir().to_string_lossy().to_string();
    }
    raw.to_string()
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

// ── Environment variable helpers ───────────────────────────────────

/// Try primary env var, fall back to secondary, then default.
pub fn env_or_default(primary: &str, secondary: &str, default_value: &str) -> String {
    std::env::var(primary)
        .ok()
        .filter(|v| !v.is_empty())
        .or_else(|| std::env::var(secondary).ok().filter(|v| !v.is_empty()))
        .unwrap_or_else(|| default_value.to_string())
}

/// Try primary env var first, then fallback. Returns None if both are unset or empty.
pub fn env_prefixed(primary: &str, fallback: &str) -> Option<String> {
    std::env::var(primary)
        .ok()
        .filter(|v| !v.is_empty())
        .or_else(|| std::env::var(fallback).ok().filter(|v| !v.is_empty()))
}

/// Check if env var equals expected value (case-insensitive).
pub fn env_equals_ignore_case(key: &str, expected: &str) -> bool {
    std::env::var(key)
        .map(|v| v.eq_ignore_ascii_case(expected))
        .unwrap_or(false)
}

/// Check if env var is set to a truthy value (true/1/yes, case-insensitive).
pub fn env_is_truthy(key: &str) -> bool {
    std::env::var(key)
        .map(|v| matches!(v.to_ascii_lowercase().as_str(), "true" | "1" | "yes"))
        .unwrap_or(false)
}

/// Spawn a command and wait up to `timeout` for it to complete.
/// On timeout the child is sent SIGKILL and reaped so this function never blocks longer
/// than `timeout` + the time it takes the OS to deliver the signal.
/// When `trim` is true, leading/trailing whitespace is stripped.
/// When false, stdout is returned verbatim (for diff/patch content).
pub fn run_cmd_with_timeout(
    program: &str,
    args: &[&str],
    cwd: &Path,
    allow_exit_1: bool,
    timeout: Duration,
    trim: bool,
) -> Option<String> {
    use std::io::Read;
    use std::time::Instant;

    let mut child = std::process::Command::new(program)
        .args(args)
        .current_dir(cwd)
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::null())
        .spawn()
        .ok()?;

    // Drain stdout in a background thread so the child never blocks on a full pipe buffer
    // (~64 KB on macOS/Linux). The reader returns when the pipe closes — either naturally
    // after the child exits, or after we kill it below.
    let stdout_handle = child.stdout.take().map(|mut s| {
        std::thread::spawn(move || {
            let mut buf = Vec::new();
            let _ = s.read_to_end(&mut buf);
            buf
        })
    });

    let deadline = Instant::now() + timeout;
    let poll = Duration::from_millis(20);
    let status = loop {
        match child.try_wait() {
            Ok(Some(s)) => break Some(s),
            Ok(None) => {
                let now = Instant::now();
                if now >= deadline {
                    let _ = child.kill();
                    let _ = child.wait();
                    break None;
                }
                std::thread::sleep(poll.min(deadline.saturating_duration_since(now)));
            }
            Err(_) => {
                let _ = child.kill();
                let _ = child.wait();
                break None;
            }
        }
    };

    let stdout_buf = stdout_handle
        .and_then(|h| h.join().ok())
        .unwrap_or_default();

    let status = status?;
    let code = status.code().unwrap_or(-1);
    if code == 0 || (allow_exit_1 && code == 1) {
        let s = String::from_utf8_lossy(&stdout_buf).to_string();
        if trim {
            Some(s.trim().to_string())
        } else {
            Some(s)
        }
    } else {
        None
    }
}

/// Poll file size until stable (for fast-starting binaries that may read before write completes).
/// Requires 3 consecutive reads with the same size before considering stable.
pub fn wait_for_file_stable(path: &str, poll_interval_ms: u64, max_wait_ms: u64) -> bool {
    use std::path::Path;
    use std::time::Instant;

    let deadline = Instant::now() + Duration::from_millis(max_wait_ms);
    let mut prev_size: i64 = -1;
    let mut stable_reads = 0u32;
    let required_stable = 3;

    while Instant::now() < deadline {
        let current_size = std::fs::metadata(Path::new(path))
            .map(|m| m.len() as i64)
            .unwrap_or(-1);
        if current_size == prev_size && current_size >= 0 {
            stable_reads += 1;
            if stable_reads >= required_stable {
                return true;
            }
        } else {
            stable_reads = 0;
        }
        prev_size = current_size;
        thread::sleep(Duration::from_millis(poll_interval_ms));
    }
    false
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn test_payload_for_log_truncates_long_content() {
        let long = "x".repeat(500);
        let payload = json!({
            "hook_event_name": "PostToolUse",
            "tool_input": {"content": long, "path": "/a/b"}
        });
        let out = payload_for_log(&payload);
        // Re-parse to inspect the truncated field.
        let v: Value = serde_json::from_str(&out).unwrap();
        let c = v["tool_input"]["content"].as_str().unwrap();
        assert!(c.starts_with(&"x".repeat(200)), "keeps first 200 chars");
        assert!(
            c.contains("...[truncated 300 chars]"),
            "marker present: {}",
            c
        );
        // Other fields untouched.
        assert_eq!(v["tool_input"]["path"], "/a/b");
        assert_eq!(v["hook_event_name"], "PostToolUse");
    }

    #[test]
    fn payload_parser_accepts_non_utf8_string_bytes_with_lossy_fallback() {
        let payload =
            b"{\"hook_event_name\":\"PostToolUse\",\"tool_response\":{\"content\":\"\xff\"}}"
                .to_vec();

        let (parsed, worker_bytes) = parse_hook_payload_bytes(payload).unwrap();

        assert_eq!(parsed["hook_event_name"], "PostToolUse");
        assert_eq!(parsed["tool_response"]["content"], "\u{fffd}");
        assert!(serde_json::from_slice::<Value>(&worker_bytes).is_ok());
    }

    #[test]
    fn test_wait_for_file_stable_reports_settled_file() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("settled.jsonl");
        std::fs::write(&path, "{}\n").unwrap();

        assert!(wait_for_file_stable(path.to_str().unwrap(), 1, 50));
    }

    #[test]
    fn test_wait_for_file_stable_times_out_for_missing_file() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("missing.jsonl");

        assert!(!wait_for_file_stable(path.to_str().unwrap(), 1, 10));
    }

    #[test]
    fn test_payload_for_log_short_content_unchanged() {
        let payload = json!({"tool_input": {"content": "short"}});
        let out = payload_for_log(&payload);
        let v: Value = serde_json::from_str(&out).unwrap();
        assert_eq!(v["tool_input"]["content"], "short");
    }

    #[test]
    fn test_payload_for_log_no_content_field() {
        // No tool_input.content → serialized unchanged.
        let payload = json!({"hook_event_name": "Stop", "session_id": "abc"});
        let v: Value = serde_json::from_str(&payload_for_log(&payload)).unwrap();
        assert_eq!(v, payload);
    }

    #[test]
    fn test_payload_for_log_does_not_mutate_input() {
        let long = "y".repeat(300);
        let payload = json!({"tool_input": {"content": long}});
        let _ = payload_for_log(&payload);
        // Original still has full 300 chars.
        assert_eq!(
            payload["tool_input"]["content"]
                .as_str()
                .unwrap()
                .chars()
                .count(),
            300
        );
    }

    #[test]
    fn test_payload_for_log_multibyte_safe() {
        // 300 Chinese chars (3 bytes each) — must not split a char boundary.
        let long = "中".repeat(300);
        let payload = json!({"tool_input": {"content": long}});
        let out = payload_for_log(&payload); // must not panic
        let v: Value = serde_json::from_str(&out).unwrap();
        let c = v["tool_input"]["content"].as_str().unwrap();
        assert!(c.starts_with(&"中".repeat(200)));
        assert!(c.contains("...[truncated 100 chars]"));
    }

    #[test]
    fn test_payload_for_log_truncates_everywhere() {
        // Long strings at top level, in tool_input (new_string/old_string), in
        // a nested tool_response object, and inside an array must ALL truncate.
        let long = "z".repeat(400);
        let payload = json!({
            "session_id": "short-keep",
            "tool_input": {"old_string": long, "new_string": long, "replace_all": false},
            "tool_response": {"stdout": long, "interrupted": false, "lines": [long]},
        });
        let v: Value = serde_json::from_str(&payload_for_log(&payload)).unwrap();
        let marker = "...[truncated 200 chars]";
        assert!(v["tool_input"]["old_string"]
            .as_str()
            .unwrap()
            .ends_with(marker));
        assert!(v["tool_input"]["new_string"]
            .as_str()
            .unwrap()
            .ends_with(marker));
        assert!(v["tool_response"]["stdout"]
            .as_str()
            .unwrap()
            .ends_with(marker));
        assert!(v["tool_response"]["lines"][0]
            .as_str()
            .unwrap()
            .ends_with(marker));
        // Short strings, bools, and unrelated keys are untouched.
        assert_eq!(v["session_id"], "short-keep");
        assert_eq!(v["tool_input"]["replace_all"], false);
        assert_eq!(v["tool_response"]["interrupted"], false);
    }

    #[test]
    fn test_sanitize_session_id() {
        // Allowed chars pass through.
        assert_eq!(sanitize_session_id("abc-123_DEF.4"), "abc-123_DEF.4");
        // Path separators and traversal cannot survive (no '/' left → flat name).
        assert_eq!(sanitize_session_id("test/../x"), "test_.._x");
        assert_eq!(sanitize_session_id("a/b\\c"), "a_b_c");
        // Other non-whitelist chars become '_'.
        assert_eq!(sanitize_session_id("a b:c*d"), "a_b_c_d");
        assert!(!sanitize_session_id("any/thing").contains('/'));
    }

    #[test]
    fn test_extract_session_id_camel_case() {
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
    fn test_extract_transcript_path_camel_case() {
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
        std::env::set_var("HOME", "/Users/testuser");
        let result = expand_home_path("~/documents/file.txt");
        assert_eq!(result, "/Users/testuser/documents/file.txt");
    }

    #[test]
    fn test_expand_home_path_no_tilde() {
        let result = expand_home_path("/absolute/path/file.txt");
        assert_eq!(result, "/absolute/path/file.txt");
    }

    #[test]
    fn test_home_dir_from_env() {
        if let Ok(home) = std::env::var("HOME") {
            if !home.is_empty() {
                assert_eq!(home_dir(), PathBuf::from(&home));
            }
        }
    }

    #[test]
    fn test_env_or_default() {
        let unique_key = "AGENTIC_TEST_ENV_PRIMARY_12345";
        let unique_key2 = "AGENTIC_TEST_ENV_SECONDARY_12345";
        std::env::remove_var(unique_key);
        std::env::remove_var(unique_key2);
        assert_eq!(
            env_or_default(unique_key, unique_key2, "fallback"),
            "fallback"
        );

        std::env::set_var(unique_key2, "secondary_val");
        assert_eq!(
            env_or_default(unique_key, unique_key2, "fallback"),
            "secondary_val"
        );

        std::env::set_var(unique_key, "primary_val");
        assert_eq!(
            env_or_default(unique_key, unique_key2, "fallback"),
            "primary_val"
        );

        std::env::remove_var(unique_key);
        std::env::remove_var(unique_key2);
    }

    #[test]
    fn test_env_prefixed() {
        let key1 = "AGENTIC_TEST_PREFIXED_1";
        let key2 = "AGENTIC_TEST_PREFIXED_2";
        std::env::remove_var(key1);
        std::env::remove_var(key2);
        assert_eq!(env_prefixed(key1, key2), None);

        std::env::set_var(key2, "fallback_val");
        assert_eq!(env_prefixed(key1, key2), Some("fallback_val".to_string()));

        std::env::set_var(key1, "primary_val");
        assert_eq!(env_prefixed(key1, key2), Some("primary_val".to_string()));

        std::env::remove_var(key1);
        std::env::remove_var(key2);
    }

    #[test]
    fn test_env_equals_ignore_case() {
        let unique_key = "AGENTIC_TEST_IGNORE_CASE_12345";
        std::env::set_var(unique_key, "TrUe");
        assert!(env_equals_ignore_case(unique_key, "true"));
        assert!(env_equals_ignore_case(unique_key, "TRUE"));
        assert!(!env_equals_ignore_case(unique_key, "false"));

        std::env::remove_var(unique_key);
        assert!(!env_equals_ignore_case(unique_key, "true"));
    }

    #[test]
    fn test_env_is_truthy() {
        let key = "AGENTIC_TEST_TRUTHY_12345";

        std::env::set_var(key, "true");
        assert!(env_is_truthy(key));

        std::env::set_var(key, "TRUE");
        assert!(env_is_truthy(key));

        std::env::set_var(key, "1");
        assert!(env_is_truthy(key));

        std::env::set_var(key, "yes");
        assert!(env_is_truthy(key));

        std::env::set_var(key, "YES");
        assert!(env_is_truthy(key));

        std::env::set_var(key, "false");
        assert!(!env_is_truthy(key));

        std::env::set_var(key, "0");
        assert!(!env_is_truthy(key));

        std::env::remove_var(key);
        assert!(!env_is_truthy(key));
    }
}
