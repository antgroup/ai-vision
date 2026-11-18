// Re-export shared hook utilities from agentic-otel-common
pub use agentic_otel_common::hook::{
    env_equals_ignore_case, env_is_truthy, env_or_default, env_prefixed, extract_session_id,
    extract_transcript_path, read_hook_payload, read_hook_payload_with_raw, wait_for_file_stable,
};

pub fn resolve_username() -> String {
    std::env::var("AIVISION_ANT_USERNAME")
        .ok()
        .filter(|value| !value.is_empty())
        .or_else(|| env_prefixed("CDXHOOKS_USERNAME", "LANGFUSE_ATTR_USERNAME"))
        .or_else(|| {
            std::env::var("LANGFUSE_ATTR_ANT_USERNAME")
                .ok()
                .filter(|value| !value.is_empty())
        })
        .or_else(|| agentic_otel_common::otlp::parse_otel_span_username("CDXHOOKS_OTEL_ATTRIBUTES"))
        .unwrap_or_else(|| env_or_default("USER", "USERNAME", "unknown"))
}

/// Build a ureq Agent with the given timeout.
/// When `AIVISION_TLS_NOVERIFY=true`, TLS certificate verification is disabled.
#[cfg_attr(not(feature = "dynamic"), allow(dead_code))]
pub fn build_http_agent(timeout_secs: u64) -> ureq::Agent {
    build_http_agent_with_timeout(std::time::Duration::from_secs(timeout_secs))
}

/// Build a ureq Agent with a sub-second-capable global timeout.
pub fn build_http_agent_with_timeout(timeout: std::time::Duration) -> ureq::Agent {
    build_http_agent_with_timeout_and_redirects(timeout, true)
}

/// Build an HTTP agent that exposes redirect handling as an explicit policy.
pub fn build_http_agent_with_timeout_and_redirects(
    timeout: std::time::Duration,
    follow_redirects: bool,
) -> ureq::Agent {
    let mut builder = ureq::Agent::config_builder().timeout_global(Some(timeout));
    if !follow_redirects {
        builder = builder.max_redirects(0);
    }
    // Unit tests use loopback sinks and must not inherit a developer machine's
    // HTTP(S)_PROXY settings. Production keeps ureq's normal proxy behavior.
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
#[cfg_attr(not(feature = "dynamic"), allow(dead_code))]
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

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub enum HookEvent {
    SessionStart,
    SessionEnd,
    PreToolUse,
    PostToolUse,
    PreCompact,
    PostCompact,
    UserPromptSubmit,
    Stop,
}

impl HookEvent {
    pub fn parse(name: &str) -> Option<Self> {
        // `SubagentStart` / `SubagentStop` (Codex protocol events) are
        // intentionally unparsed: cdxhooks routes all sub-agent data through
        // the parent's Stop event, so receiving these from Codex is a no-op
        // (the dispatcher returns `None` here and skips dispatching).
        // Removing the variants keeps the enum honest about what we handle.
        match name {
            "SessionStart" => Some(Self::SessionStart),
            "SessionEnd" => Some(Self::SessionEnd),
            "PreToolUse" => Some(Self::PreToolUse),
            "PostToolUse" => Some(Self::PostToolUse),
            "PreCompact" => Some(Self::PreCompact),
            "PostCompact" => Some(Self::PostCompact),
            "UserPromptSubmit" => Some(Self::UserPromptSubmit),
            "Stop" => Some(Self::Stop),
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
    fn test_extract_session_id_snake_case() {
        let payload = json!({"session_id": "sess-123"});
        assert_eq!(extract_session_id(&payload), "sess-123");
    }

    #[test]
    fn test_extract_transcript_path_snake_case() {
        let payload = json!({"transcript_path": "/tmp/session.jsonl"});
        assert_eq!(extract_transcript_path(&payload), "/tmp/session.jsonl");
    }

    #[test]
    fn test_expand_home_path() {
        // Serialize against other HOME-reading tests via the shared env lock,
        // and save+restore HOME so we don't leak state to subsequent tests.
        let _guard = crate::test_sync::env_lock();
        let saved_home = std::env::var("HOME").ok();
        std::env::set_var("HOME", "/Users/tester");
        assert_eq!(expand_home_path("~/demo.jsonl"), "/Users/tester/demo.jsonl");
        assert_eq!(expand_home_path("/tmp/demo.jsonl"), "/tmp/demo.jsonl");
        match saved_home {
            Some(h) => std::env::set_var("HOME", h),
            None => std::env::remove_var("HOME"),
        }
    }

    #[test]
    fn test_hook_event_parse() {
        assert_eq!(HookEvent::parse("Stop"), Some(HookEvent::Stop));
        assert_eq!(
            HookEvent::parse("SessionStart"),
            Some(HookEvent::SessionStart)
        );
        assert_eq!(HookEvent::parse("SessionEnd"), Some(HookEvent::SessionEnd));
        // SubagentStart / SubagentStop are intentionally unparsed —
        // sub-agent data is captured via the parent's Stop event.
        assert_eq!(HookEvent::parse("SubagentStart"), None);
        assert_eq!(HookEvent::parse("SubagentStop"), None);
        assert_eq!(HookEvent::parse("Unknown"), None);
    }

    #[test]
    fn test_retry_once_contract() {
        let mut successful_attempts = 0;
        let result = retry_once(
            || {
                successful_attempts += 1;
                Ok::<_, &str>("ok")
            },
            |_| panic!("successful operation must not retry"),
        );
        assert_eq!(result, Ok("ok"));
        assert_eq!(successful_attempts, 1);

        let mut attempts = 0;
        let result = retry_once(
            || {
                attempts += 1;
                if attempts == 1 {
                    Err("temporary")
                } else {
                    Ok("ok")
                }
            },
            |_| {},
        );
        assert_eq!(result, Ok("ok"));
        assert_eq!(attempts, 2);

        let mut failed_attempts = 0;
        let result = retry_once(
            || {
                failed_attempts += 1;
                Err::<(), _>(failed_attempts)
            },
            |_| {},
        );
        assert_eq!(result, Err(2));
        assert_eq!(failed_attempts, 2);
    }
}
