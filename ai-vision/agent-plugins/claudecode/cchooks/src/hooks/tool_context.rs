use std::collections::hash_map::DefaultHasher;
use std::hash::{Hash, Hasher};

use serde_json::{json, Value};

use crate::common::hook::{extract_session_id, HookEvent};
use crate::hooks::langfuse::domain_state::{DomainStateStore, StateDomain};
use crate::hooks::langfuse::state::{get_vcs_for_cwd_from_session, StateTxnError, VcsInfo};
use crate::hooks::langfuse::{resolve_agent_product, resolve_agent_type, LangfuseConfig};
use crate::hooks::turn_seed::{read_trace_context_for_tool, TurnTraceContext};
use crate::hooks::{CommonContext, ExportMode, HandlerError, HookHandler};

/// Handler that injects trace context, OTLP endpoint, and standard attributes
/// into Bash tool commands via `hookSpecificOutput.updatedInput`.
///
/// Injected env vars:
/// - `TRACEPARENT`                          — W3C traceparent (standard)
/// - `AGENTIC_TOOL_USE_ID`                  — tool call identifier
/// - `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`  — OTLP endpoint (OTel standard)
/// - `OTEL_EXPORTER_OTLP_AUTH`              — Basic auth (base64)
/// - `OTEL_EXPORTER_OTLP_HEADERS`          — custom headers (JSON)
/// - `AGENTIC_SPAN_ATTRIBUTES`              — span attributes (JSON)
/// - `OTEL_RESOURCE_ATTRIBUTES`   — JSON resource-level attributes
pub struct ToolContextHandler;

fn build_resource_attrs(
    agent_product: &str,
    agent_type: &str,
    agent_env: &str,
    agent_scene: &str,
    hostname: &str,
    vcs_info: Option<&VcsInfo>,
) -> Value {
    let mut attributes = json!({
        "service.name": agent_product,
        "ant.agent.product": agent_product,
        "ant.agent.type": agent_type,
        "ant.agent.env": agent_env,
        "ant.agent.scene": agent_scene,
        "ant.agent.instance": hostname,
        "deployment.environment": agent_env,
    });
    if let Some(vcs) = vcs_info {
        if !vcs.remote_url.trim().is_empty() {
            attributes["vcs.repository.url.full"] = json!(vcs.remote_url);
        }
        if !vcs.branch.trim().is_empty() {
            attributes["vcs.ref.head.name"] = json!(vcs.branch);
        }
    }
    attributes
}

impl Default for ToolContextHandler {
    fn default() -> Self {
        Self::new()
    }
}

impl ToolContextHandler {
    pub fn new() -> Self {
        Self
    }

    /// Shell-escape a string for safe interpolation.
    /// Unix: wraps in single quotes, escapes internal single quotes.
    /// Windows: wraps in double quotes, escapes internal double quotes.
    fn shell_escape(s: &str) -> String {
        format!("'{}'", s.replace('\'', "'\\''"))
    }

    fn resolve_endpoint(config_host: &str) -> String {
        if let Some(url) =
            crate::common::hook::otlp_traces_endpoint("CCHOOKS_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
        {
            return url;
        }
        let trimmed = config_host.trim_end_matches('/');
        if let Some(scheme_end) = trimmed.find("://") {
            let after_scheme = &trimmed[scheme_end + 3..];
            if after_scheme.contains('/') {
                return trimmed.to_string();
            }
        }
        format!("{}/api/public/otel/v1/traces", trimmed)
    }
}

pub fn build_pretooluse_control_response(
    payload: &Value,
    langfuse_config: Option<&LangfuseConfig>,
    username: &str,
    export_mode: ExportMode,
    logger: Option<&crate::common::logger::Logger>,
) -> Option<Value> {
    let tool_name = payload
        .get("tool_name")
        .and_then(|v| v.as_str())
        .unwrap_or("");

    if tool_name != "Bash" {
        return None;
    }

    let tool_use_id = payload
        .get("tool_use_id")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    if tool_use_id.is_empty() {
        return None;
    }

    let command = payload
        .get("tool_input")
        .and_then(|v| v.get("command"))
        .and_then(|v| v.as_str())
        .unwrap_or("");
    if command.is_empty() {
        return None;
    }

    let session_id = extract_session_id(payload);
    let cwd = payload.get("cwd").and_then(Value::as_str).unwrap_or("");
    let upstream_traceparent = crate::common::env::var("TRACEPARENT")
        .ok()
        .filter(|s| !s.is_empty());
    let sofa_traceid = crate::common::env::var("SOFA_TRACEID")
        .ok()
        .filter(|s| !s.is_empty());
    let sofa_rpcid = crate::common::env::var("SOFA_RPCID")
        .ok()
        .filter(|s| !s.is_empty());

    let trace_context =
        match read_trace_context_for_tool(&session_id, upstream_traceparent.as_deref()) {
            Ok(context) => context,
            Err(StateTxnError::Busy { .. }) => {
                if let Some(logger) = logger {
                    logger.warn(
                        "tool_context: turn seed lock unavailable; skipping context injection",
                    );
                }
                return None;
            }
            Err(error) => {
                if let Some(logger) = logger {
                    logger.warn(&format!(
                    "tool_context: failed to read turn seed; skipping context injection: {error}"
                ));
                }
                return None;
            }
        };
    let Some(trace_id) = trace_context.trace_id().map(str::to_string) else {
        if let Some(logger) = logger {
            logger.warn("tool_context: control_seed_missing; skipping context injection");
        }
        return None;
    };
    if matches!(trace_context, TurnTraceContext::Legacy { .. }) {
        if let Some(logger) = logger {
            logger.debug("tool_context: using legacy current_trace_context fallback");
        }
    }
    if matches!(trace_context, TurnTraceContext::Upstream { .. }) {
        if let Some(logger) = logger {
            logger.debug("tool_context: using upstream TRACEPARENT fallback");
        }
    }

    let resource_store = DomainStateStore::for_session(&session_id, StateDomain::Resource, 50);
    let vcs_info = match resource_store {
        Ok(store) => match store.read(|state| get_vcs_for_cwd_from_session(state, cwd)) {
            Ok(vcs_info) => vcs_info,
            Err(StateTxnError::Busy { .. }) => {
                if let Some(logger) = logger {
                    logger.debug("tool_context: resource state lock unavailable; omitting vcs");
                }
                None
            }
            Err(error) => {
                if let Some(logger) = logger {
                    logger.debug(&format!(
                        "tool_context: failed to read resource state: {error}"
                    ));
                }
                None
            }
        },
        Err(StateTxnError::Busy { .. }) => {
            if let Some(logger) = logger {
                logger.debug("tool_context: resource state lock unavailable; omitting vcs");
            }
            None
        }
        Err(error) => {
            if let Some(logger) = logger {
                logger.debug(&format!(
                    "tool_context: failed to initialize resource state: {error}"
                ));
            }
            None
        }
    };

    let span_id = if export_mode.realtime_enabled() {
        crate::hooks::langfuse::realtime_state::stable_tool_span_id(tool_use_id)
    } else {
        let mut h = DefaultHasher::new();
        tool_use_id.hash(&mut h);
        format!("{:016x}", h.finish())
    };
    let traceparent = format!("00-{}-{}-01", trace_id, span_id);

    let mut env_pairs: Vec<(String, String)> = vec![
        ("TRACEPARENT".into(), traceparent),
        ("AGENTIC_TOOL_USE_ID".into(), tool_use_id.to_string()),
    ];

    if let Some(ref st) = sofa_traceid {
        env_pairs.push(("SOFA_TRACEID".into(), st.clone()));
    }
    if let Some(ref sr) = sofa_rpcid {
        env_pairs.push(("SOFA_RPCID".into(), sr.clone()));
    }

    if let Some(config) = langfuse_config {
        let endpoint = ToolContextHandler::resolve_endpoint(&config.host);
        env_pairs.push(("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT".into(), endpoint));

        if let (Some(pk), Some(sk)) = (&config.public_key, &config.secret_key) {
            use base64::Engine;
            let auth = base64::engine::general_purpose::STANDARD.encode(format!("{}:{}", pk, sk));
            env_pairs.push(("OTEL_EXPORTER_OTLP_AUTH".into(), auth));
        }

        if let Some(ref headers) = config.headers {
            if !headers.is_empty() {
                if let Ok(hj) = serde_json::to_string(headers) {
                    env_pairs.push(("OTEL_EXPORTER_OTLP_HEADERS".into(), hj));
                }
            }
        }
    }

    let agent_product = resolve_agent_product();
    let agent_type = resolve_agent_type();
    let hostname = crate::common::env::var("HOSTNAME")
        .or_else(|_| agentic_otel_common::hostname_from_system())
        .unwrap_or_else(|_| "unknown".to_string());
    let agent_env =
        crate::common::hook::env_or_default("CCHOOKS_AGENT_ENV", "AIVISION_AGENT_ENV", "Default");
    let agent_scene = crate::common::hook::env_or_default(
        "CCHOOKS_AGENT_SCENE",
        "AIVISION_AGENT_SCENE",
        "Default",
    );

    let span_attrs = json!({
        "user.id": username,
        "ant.username": username,
        "gen_ai.conversation.id": session_id,
        "gen_ai.tool.call.id": tool_use_id,
    });
    let resource_attrs = build_resource_attrs(
        &agent_product,
        &agent_type,
        &agent_env,
        &agent_scene,
        &hostname,
        vcs_info.as_ref(),
    );
    if let Ok(sa) = serde_json::to_string(&span_attrs) {
        env_pairs.push(("AGENTIC_SPAN_ATTRIBUTES".into(), sa));
    }
    if let Ok(ra) = serde_json::to_string(&resource_attrs) {
        env_pairs.push(("OTEL_RESOURCE_ATTRIBUTES".into(), ra));
    }

    let env_block = env_pairs
        .iter()
        .map(|(key, value)| format!("export {}={}", key, ToolContextHandler::shell_escape(value)))
        .collect::<Vec<_>>()
        .join("\n");
    let updated_command = format!("{}\n{}", env_block, command);
    let mut updated_input = payload
        .get("tool_input")
        .cloned()
        .unwrap_or_else(|| json!({}));
    updated_input["command"] = Value::String(updated_command);

    Some(json!({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "updatedInput": updated_input
        }
    }))
}

impl HookHandler for ToolContextHandler {
    fn name(&self) -> &str {
        "tool_context"
    }

    fn supported_events(&self) -> &[HookEvent] {
        &[HookEvent::PreToolUse]
    }

    fn handle(
        &self,
        _event: &HookEvent,
        payload: &Value,
        context: &CommonContext,
    ) -> Result<(), HandlerError> {
        if !matches!(_event, HookEvent::PreToolUse) {
            return Ok(());
        }

        let Some(hook_output) = build_pretooluse_control_response(
            payload,
            context.langfuse_config.as_ref(),
            &context.username,
            context.export_mode,
            Some(&context.logger),
        ) else {
            return Ok(());
        };
        println!("{}", hook_output);

        if let Some(tool_use_id) = payload.get("tool_use_id").and_then(Value::as_str) {
            context.logger.debug(&format!(
                "tool_context: injected trace context for tool_use_id={}",
                tool_use_id
            ));
        }

        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::hooks::HookHandler;

    #[test]
    fn tool_context_handler_only_supports_pretooluse() {
        let handler = ToolContextHandler::new();
        assert_eq!(handler.supported_events(), &[HookEvent::PreToolUse]);
    }

    #[test]
    fn test_span_id_deterministic() {
        let id = "toolu_vrtx_01XU1jomQb9WwV5iZcuSBnuE";
        let mut h1 = DefaultHasher::new();
        id.hash(&mut h1);
        let s1 = format!("{:016x}", h1.finish());
        let mut h2 = DefaultHasher::new();
        id.hash(&mut h2);
        let s2 = format!("{:016x}", h2.finish());
        assert_eq!(s1, s2);
        assert_eq!(s1.len(), 16);
    }

    #[test]
    fn test_trace_id_32_hex() {
        let sid = "abc-123";
        let mut h = DefaultHasher::new();
        sid.hash(&mut h);
        let hi = h.finish();
        let mut h2 = DefaultHasher::new();
        hi.hash(&mut h2);
        let lo = h2.finish();
        assert_eq!(format!("{:016x}{:016x}", hi, lo).len(), 32);
    }

    #[test]
    fn test_resolve_endpoint_with_path() {
        assert_eq!(
            ToolContextHandler::resolve_endpoint("https://x.com/ns/foo/traces"),
            "https://x.com/ns/foo/traces"
        );
    }

    #[test]
    fn test_shell_escape_single_quote() {
        let result = ToolContextHandler::shell_escape("O'Brien");
        assert_eq!(result, "'O'\\''Brien'");
    }

    #[test]
    fn test_shell_escape_normal() {
        let result = ToolContextHandler::shell_escape("hello world");
        assert_eq!(result, "'hello world'");
    }

    #[test]
    fn test_shell_escape_empty() {
        let result = ToolContextHandler::shell_escape("");
        assert_eq!(result, "''");
    }

    #[test]
    fn test_resolve_endpoint_without_path() {
        assert_eq!(
            ToolContextHandler::resolve_endpoint("https://x.com"),
            "https://x.com/api/public/otel/v1/traces"
        );
    }

    #[test]
    fn resource_attrs_include_vcs_without_losing_agent_attributes() {
        for remote_url in [
            "git@git.example.com:team/repo.git",
            "https://git.example.com/team/repo.git",
        ] {
            let vcs = VcsInfo {
                remote_url: remote_url.to_string(),
                branch: "feature/realtime".to_string(),
                cwd: "/repo".to_string(),
            };
            let attributes = build_resource_attrs(
                "Claude Code",
                "claudecode",
                "test",
                "default",
                "host",
                Some(&vcs),
            );
            assert_eq!(attributes["service.name"], "Claude Code");
            assert_eq!(attributes["ant.agent.type"], "claudecode");
            assert_eq!(attributes["vcs.repository.url.full"], remote_url);
            assert_eq!(attributes["vcs.ref.head.name"], "feature/realtime");
            let encoded = serde_json::to_string(&attributes).unwrap();
            assert_eq!(serde_json::from_str::<Value>(&encoded).unwrap(), attributes);
        }
    }

    #[test]
    fn resource_attrs_without_vcs_preserve_existing_shape() {
        let attributes =
            build_resource_attrs("Claude Code", "claudecode", "test", "default", "host", None);
        assert!(attributes.get("vcs.repository.url.full").is_none());
        assert!(attributes.get("vcs.ref.head.name").is_none());
        assert_eq!(attributes["deployment.environment"], "test");
    }

    #[test]
    fn pretooluse_skips_context_injection_when_turn_seed_is_missing() {
        let _guard = crate::test_sync::env_lock();
        let dir = tempfile::tempdir().unwrap();
        let previous_state_dir = crate::common::env::var("CCHOOKS_STATE_DIR").ok();
        let previous_traceparent = crate::common::env::var("TRACEPARENT").ok();
        std::env::set_var("CCHOOKS_STATE_DIR", dir.path().join("state"));
        std::env::remove_var("TRACEPARENT");

        let payload = json!({
            "hook_event_name": "PreToolUse",
            "session_id": "session-missing-seed",
            "tool_name": "Bash",
            "tool_use_id": "tool-missing-seed",
            "cwd": "/repo",
            "tool_input": {"command": "echo ok"}
        });
        let response = build_pretooluse_control_response(
            &payload,
            None,
            "user",
            ExportMode::RealtimeWithStopFinalizer,
            None,
        );
        assert!(response.is_none());

        match previous_state_dir {
            Some(value) => std::env::set_var("CCHOOKS_STATE_DIR", value),
            None => std::env::remove_var("CCHOOKS_STATE_DIR"),
        }
        match previous_traceparent {
            Some(value) => std::env::set_var("TRACEPARENT", value),
            None => std::env::remove_var("TRACEPARENT"),
        }
    }
}
