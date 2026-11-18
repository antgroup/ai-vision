use std::time::Duration;

use agentic_otel_common::otlp::OtelAttribute;
use serde_json::Value;

use crate::common::hook::{extract_session_id, is_subagent_orchestration_tool, HookEvent};
use crate::common::logger::now_iso8601_pub;
use crate::common::otel::{
    build_realtime_tool_export_request, inject_vcs_resource_attrs,
    post_otel_to_langfuse_once_with_context, LangfuseAttemptContext,
};
use crate::hooks::langfuse::realtime_state::{
    claim_failed_tool_exports, claim_tool_with_trace_snapshot, finish_child_deliveries,
    mark_tool_export_batch_emitted, mark_tool_export_batch_failed, otlp_payload_digest,
    otlp_span_end_time_unix_nano, prepare_child_delivery, reclaim_child_deliveries, rotate_current,
    tool_child_key, ChildDeliverySpec,
};
use crate::hooks::langfuse::state::{get_vcs_for_cwd_from_state, SessionStateStore, StateTxnError};
use crate::hooks::timing::load_timing_from_state;
use crate::hooks::{CommonContext, HandlerError, HookHandler, RetryPolicy};

const REALTIME_LOCK_TIMEOUT_MS: u64 = 500;
const REALTIME_HTTP_TIMEOUT: Duration = Duration::from_secs(5);
const FAILED_TOOL_EXPORT_LEASE_MS: u64 = 10_000;

#[derive(Default)]
pub struct RealtimeLangfuseHandler;

impl RealtimeLangfuseHandler {
    pub fn new() -> Self {
        Self
    }
}

impl HookHandler for RealtimeLangfuseHandler {
    fn name(&self) -> &str {
        "langfuse_realtime"
    }

    fn supported_events(&self) -> &[HookEvent] {
        &[
            HookEvent::UserPromptSubmit,
            HookEvent::PreToolUse,
            HookEvent::PostToolUse,
            HookEvent::PostToolUseFailure,
        ]
    }

    fn handle(
        &self,
        event: &HookEvent,
        payload: &Value,
        context: &CommonContext,
    ) -> Result<(), HandlerError> {
        if !context.export_mode.realtime_enabled() {
            return Ok(());
        }
        let session_id = extract_session_id(payload);
        if session_id.is_empty() {
            context
                .logger
                .debug("realtime: missing session_id, skipping");
            return Ok(());
        }

        let state_store = SessionStateStore::new(
            context.state_file.clone(),
            &session_id,
            REALTIME_LOCK_TIMEOUT_MS,
        );

        if matches!(event, HookEvent::UserPromptSubmit) {
            if let Err(error) = state_store.update(|state| {
                rotate_current(&context.state_file, state, &session_id)
                    .map_err(StateTxnError::Schema)
            }) {
                if matches!(error, StateTxnError::Busy { .. }) {
                    context
                        .logger
                        .warn("realtime: state lock unavailable; leaving current context intact");
                    return Ok(());
                }
                return Err(HandlerError::from(error.to_string()));
            }
            return Ok(());
        }

        let tool_use_id = payload
            .get("tool_use_id")
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty());
        let Some(tool_use_id) = tool_use_id else {
            context
                .logger
                .debug("realtime: tool hook missing tool_use_id, skipping");
            return Ok(());
        };
        let upstream_traceparent = crate::common::env::var("TRACEPARENT")
            .ok()
            .filter(|value| !value.is_empty());
        let sofa_traceid = crate::common::env::var("SOFA_TRACEID")
            .ok()
            .filter(|value| !value.is_empty());
        let sofa_rpcid = crate::common::env::var("SOFA_RPCID")
            .ok()
            .filter(|value| !value.is_empty());
        let realtime_context = match state_store.update(|state| {
            claim_tool_with_trace_snapshot(
                &context.state_file,
                state,
                &session_id,
                tool_use_id,
                &now_iso8601_pub(),
                upstream_traceparent.as_deref(),
                sofa_traceid.as_deref(),
                sofa_rpcid.as_deref(),
            )
            .map_err(StateTxnError::Schema)
        }) {
            Ok(realtime_context) => realtime_context,
            Err(StateTxnError::Busy { .. }) => {
                context
                    .logger
                    .warn("realtime: state lock unavailable; leaving tool retryable");
                return Ok(());
            }
            Err(error) => return Err(HandlerError::from(error.to_string())),
        };

        if matches!(event, HookEvent::PreToolUse) {
            return Ok(());
        }

        // Subagent-internal hooks and Agent/Task orchestration retain the
        // existing Stop/SubagentStop topology in the first realtime release.
        let tool_name = payload
            .get("tool_name")
            .and_then(Value::as_str)
            .unwrap_or("");
        let is_subagent_internal = payload
            .get("agent_id")
            .and_then(Value::as_str)
            .map(|value| !value.is_empty())
            .unwrap_or(false);
        if is_subagent_internal || is_subagent_orchestration_tool(tool_name) {
            context
                .logger
                .debug("realtime: deferring subagent tool topology to Stop/SubagentStop");
            return Ok(());
        }

        if realtime_context
            .emitted_tool_use_ids
            .iter()
            .any(|id| id == tool_use_id)
        {
            context
                .logger
                .debug("realtime: tool already accounted, skipping duplicate hook");
            return Ok(());
        }
        let Some(tool_span_id) = realtime_context.tool_span_ids.get(tool_use_id) else {
            return Err(HandlerError::from(
                "realtime: persisted context missing tool span id".to_string(),
            ));
        };
        let config = context
            .langfuse_config
            .as_ref()
            .ok_or_else(|| HandlerError::from("Missing Langfuse configuration".to_string()))?;
        let success = matches!(event, HookEvent::PostToolUse);
        let cwd = payload.get("cwd").and_then(Value::as_str).unwrap_or("");
        let (timing, failed_claim, vcs_info) = match state_store.update(|state| {
            let timing = load_timing_from_state(state, &session_id);
            let failed_claim = claim_failed_tool_exports(
                &context.state_file,
                state,
                &session_id,
                unix_millis(),
                FAILED_TOOL_EXPORT_LEASE_MS,
            )
            .map_err(StateTxnError::Schema)?;
            let vcs_info = get_vcs_for_cwd_from_state(state, &session_id, cwd);
            Ok((timing, failed_claim, vcs_info))
        }) {
            Ok(result) => result,
            Err(StateTxnError::Busy { .. }) => {
                context
                    .logger
                    .warn("realtime: state lock unavailable; leaving tool retryable");
                return Ok(());
            }
            Err(error) => return Err(HandlerError::from(error.to_string())),
        };
        let body = build_realtime_tool_export_request(
            payload,
            &realtime_context.trace_id,
            &realtime_context.root_span_id,
            tool_span_id,
            &session_id,
            &context.username,
            &context.hook_version,
            &timing,
            success,
        )
        .map_err(HandlerError::from)?;
        let claimed_exports = failed_claim
            .as_ref()
            .map(|claim| claim.exports.as_slice())
            .unwrap_or_default();
        let mut bodies = claimed_exports
            .iter()
            .filter(|export| {
                export.context_id != realtime_context.context_id
                    || export.tool_use_id != tool_use_id
            })
            .map(|export| export.body.clone())
            .collect::<Vec<_>>();
        bodies.push(body.clone());
        let mut export_body = merge_tool_export_bodies(&bodies).map_err(HandlerError::from)?;
        if let Some(vcs) = vcs_info.as_ref() {
            inject_vcs_resource_attrs(&mut export_body, &vcs.remote_url, &vcs.branch);
        }
        let claimed_identities = claimed_exports
            .iter()
            .map(|export| (export.context_id.clone(), export.tool_use_id.clone()))
            .collect::<Vec<_>>();
        let payload_digest = otlp_payload_digest(&export_body).map_err(HandlerError::from)?;
        let transport_unit_id = format!("tool-batch:{payload_digest}");
        let current_child_key = tool_child_key(&realtime_context.context_id, tool_use_id);
        let current_end_time_unix_nano = otlp_span_end_time_unix_nano(&body, tool_span_id);
        let retried_child_keys = claimed_identities
            .iter()
            .map(|(context_id, tool_use_id)| tool_child_key(context_id, tool_use_id))
            .filter(|child_key| child_key != &current_child_key)
            .collect::<Vec<_>>();
        let mut child_keys = retried_child_keys.clone();
        child_keys.push(current_child_key.clone());
        let prepared = state_store.update(|latest_state| {
            reclaim_child_deliveries(
                latest_state,
                &session_id,
                &retried_child_keys,
                &transport_unit_id,
                &payload_digest,
            )
            .map_err(StateTxnError::Schema)?;
            prepare_child_delivery(
                latest_state,
                &session_id,
                ChildDeliverySpec {
                    child_key: &current_child_key,
                    kind: "tool",
                    context_id: &realtime_context.context_id,
                    turn_evidence: serde_json::json!({"tool_use_id": tool_use_id}),
                    trace_id: &realtime_context.trace_id,
                    root_span_id: &realtime_context.root_span_id,
                    span_id: tool_span_id,
                    end_time_unix_nano: current_end_time_unix_nano.clone(),
                },
                context.retry_policy,
                &transport_unit_id,
                &payload_digest,
                &now_iso8601_pub(),
            )
            .map_err(StateTxnError::Schema)
        });
        match prepared {
            Ok(true) => {}
            Ok(false) => {
                context.logger.debug(
                    "realtime: durable child ledger already terminal; skipping duplicate POST",
                );
                return Ok(());
            }
            Err(error) => {
                context.logger.error(&format!(
                    "Realtime tool delivery checkpoint failed before POST: {error}"
                ));
                return Ok(());
            }
        }

        // Network I/O must not serialize independent tool hooks behind the shared
        // state lock. Every PostToolUse performs exactly one HTTP attempt. Failed
        // spans remain durable for the next PostToolUse or Stop finalization.
        let result = post_otel_to_langfuse_once_with_context(
            config,
            &export_body,
            &context.logger,
            false,
            &session_id,
            "",
            REALTIME_HTTP_TIMEOUT,
            LangfuseAttemptContext::realtime_tool(&realtime_context.context_id, tool_use_id),
        );
        if let Err(error) = result {
            let non_retryable = error.is_non_retryable_http_status();
            let error = error.to_string();
            context
                .logger
                .error(&format!("Realtime tool export failed: {error}"));
            if let Some(metrics) = context.metrics.as_ref() {
                metrics.borrow_mut().inc(
                    "aivision.agentic.export_error",
                    "Export error",
                    "{count}",
                    vec![OtelAttribute::string("error.type", &error)],
                );
            }
            if let Err(checkpoint_error) = state_store.update(|latest_state| {
                if context.retry_policy == RetryPolicy::AtLeastOnce {
                    mark_tool_export_batch_failed(
                        &context.state_file,
                        latest_state,
                        &session_id,
                        &claimed_identities,
                        &realtime_context.context_id,
                        tool_use_id,
                        &body,
                        &error,
                        non_retryable,
                    )
                    .map_err(StateTxnError::Schema)?;
                }
                finish_child_deliveries(
                    latest_state,
                    &session_id,
                    &child_keys,
                    context.retry_policy,
                    false,
                    &now_iso8601_pub(),
                    Some(&error),
                )
                .map_err(StateTxnError::Schema)
            }) {
                context.logger.error(&format!(
                    "Realtime tool failure checkpoint failed; Stop will compensate: {checkpoint_error}"
                ));
            }
            return Ok(());
        }

        let mut exported = claimed_identities;
        exported.push((realtime_context.context_id.clone(), tool_use_id.to_string()));
        if let Err(error) = state_store.update(|latest_state| {
            mark_tool_export_batch_emitted(
                &context.state_file,
                latest_state,
                &session_id,
                &exported,
            )
            .map_err(StateTxnError::Schema)?;
            finish_child_deliveries(
                latest_state,
                &session_id,
                &child_keys,
                context.retry_policy,
                true,
                &now_iso8601_pub(),
                None,
            )
            .map_err(StateTxnError::Schema)
        }) {
            context.logger.error(&format!(
                "Realtime tool accepted but checkpoint failed; Stop may compensate: {error}"
            ));
            return Ok(());
        }
        context.logger.info(&format!(
            "Realtime tool export accepted tool_use_id={tool_use_id} trace_id={}",
            realtime_context.trace_id
        ));
        Ok(())
    }
}

fn merge_tool_export_bodies(bodies: &[Value]) -> Result<Value, String> {
    let mut resource_spans = Vec::new();
    for body in bodies {
        let spans = body
            .get("resourceSpans")
            .and_then(Value::as_array)
            .ok_or("realtime tool OTLP body has no resourceSpans array")?;
        resource_spans.extend(spans.iter().cloned());
    }
    Ok(serde_json::json!({ "resourceSpans": resource_spans }))
}

fn unix_millis() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .try_into()
        .unwrap_or(u64::MAX)
}

#[cfg(test)]
mod tests {
    use super::*;
    use agentic_otel_common::state::{load_state, save_state, StateLock};
    use std::io::{Read, Write};
    use std::net::{TcpListener, TcpStream};
    use std::sync::mpsc;

    use serde_json::json;

    use crate::common::logger::Logger;
    use crate::hooks::langfuse::LangfuseConfig;
    use crate::hooks::{ExportMode, RetryPolicy};

    fn read_request(stream: &mut TcpStream) -> Value {
        let mut bytes = Vec::new();
        let mut buffer = [0_u8; 4096];
        let header_end = loop {
            let read = stream.read(&mut buffer).unwrap();
            assert!(read > 0);
            bytes.extend_from_slice(&buffer[..read]);
            if let Some(position) = bytes.windows(4).position(|part| part == b"\r\n\r\n") {
                break position + 4;
            }
        };
        let headers = String::from_utf8_lossy(&bytes[..header_end]);
        let content_length = headers
            .lines()
            .find_map(|line| {
                line.split_once(':').and_then(|(name, value)| {
                    name.eq_ignore_ascii_case("content-length")
                        .then(|| value.trim().parse::<usize>().unwrap())
                })
            })
            .unwrap();
        while bytes.len() < header_end + content_length {
            let read = stream.read(&mut buffer).unwrap();
            assert!(read > 0);
            bytes.extend_from_slice(&buffer[..read]);
        }
        serde_json::from_slice(&bytes[header_end..header_end + content_length]).unwrap()
    }

    fn write_response(stream: &mut TcpStream, status: u16) {
        let reason = if (200..300).contains(&status) {
            "No Content"
        } else {
            "Server Error"
        };
        stream
            .write_all(
                format!("HTTP/1.1 {status} {reason}\r\nContent-Length: 0\r\n\r\n").as_bytes(),
            )
            .unwrap();
    }

    fn spawn_sink(status: u16) -> (std::net::SocketAddr, std::thread::JoinHandle<Value>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let server = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let request = read_request(&mut stream);
            write_response(&mut stream, status);
            request
        });
        (address, server)
    }

    fn spawn_two_request_sink(
        status: u16,
    ) -> (
        std::net::SocketAddr,
        mpsc::Receiver<()>,
        std::thread::JoinHandle<Vec<Value>>,
    ) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let (first_request_tx, first_request_rx) = mpsc::channel();
        let server = std::thread::spawn(move || {
            let (mut first_stream, _) = listener.accept().unwrap();
            let first_request = read_request(&mut first_stream);
            first_request_tx.send(()).unwrap();

            listener.set_nonblocking(true).unwrap();
            let deadline = std::time::Instant::now() + Duration::from_secs(1);
            let mut second = None;
            while std::time::Instant::now() < deadline {
                match listener.accept() {
                    Ok((mut stream, _)) => {
                        let request = read_request(&mut stream);
                        second = Some((stream, request));
                        break;
                    }
                    Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                        std::thread::sleep(Duration::from_millis(10));
                    }
                    Err(error) => panic!("accept second request: {error}"),
                }
            }

            write_response(&mut first_stream, status);
            let mut requests = vec![first_request];
            if let Some((mut stream, request)) = second {
                write_response(&mut stream, status);
                requests.push(request);
            }
            requests
        });
        (address, first_request_rx, server)
    }

    fn spawn_status_sequence_sink(
        statuses: Vec<u16>,
    ) -> (std::net::SocketAddr, std::thread::JoinHandle<Vec<Value>>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let server = std::thread::spawn(move || {
            let mut requests = Vec::new();
            for status in statuses {
                let (mut stream, _) = listener.accept().unwrap();
                requests.push(read_request(&mut stream));
                write_response(&mut stream, status);
            }
            requests
        });
        (address, server)
    }

    fn test_context(
        dir: &tempfile::TempDir,
        address: std::net::SocketAddr,
        retry_policy: RetryPolicy,
    ) -> CommonContext {
        CommonContext {
            logger: Logger::new(dir.path().join("realtime.log"), true),
            state_file: dir.path().join("state.json"),
            langfuse_config: Some(LangfuseConfig {
                public_key: None,
                secret_key: None,
                host: format!("http://{address}"),
                headers: None,
            }),
            username: "user".to_string(),
            projects_dir: String::new(),
            debug_enabled: false,
            hook_version: "test".to_string(),
            turn_seed: None,
            stop_transcript_end_offset: None,
            transcript_ingest_receipt: None,
            stop_budget: None,
            retry_policy,
            export_mode: ExportMode::RealtimeWithStopFinalizer,
            realtime_llm_batch_size: crate::hooks::DEFAULT_REALTIME_LLM_EXPORT_BATCH_SIZE,
            metrics: None,
            send_queue: std::cell::RefCell::new(Vec::new()),
            send_errors: std::sync::Arc::new(std::sync::Mutex::new(Vec::new())),
            stop_outcome: std::cell::RefCell::new(crate::hooks::StopOutcome::IntentionallySkipped),
        }
    }

    fn tool_payload(tool_use_id: &str) -> Value {
        json!({
            "session_id": "session-1",
            "cwd": "/repo",
            "hook_event_name": "PostToolUse",
            "tool_name": "Write",
            "tool_use_id": tool_use_id,
            "tool_input": {"file_path": "/tmp/a"},
            "tool_response": {"success": true}
        })
    }

    fn failed_tool_payload(tool_use_id: &str) -> Value {
        json!({
            "session_id": "session-1",
            "cwd": "/repo",
            "hook_event_name": "PostToolUseFailure",
            "tool_name": "Write",
            "tool_use_id": tool_use_id,
            "tool_input": {"file_path": "/tmp/a"},
            "error": "permission denied",
            "is_interrupt": false
        })
    }

    #[test]
    fn supports_only_realtime_overlay_events() {
        assert_eq!(
            RealtimeLangfuseHandler::new().supported_events(),
            &[
                HookEvent::UserPromptSubmit,
                HookEvent::PreToolUse,
                HookEvent::PostToolUse,
                HookEvent::PostToolUseFailure,
            ]
        );
    }

    #[test]
    fn realtime_tool_http_timeout_is_five_seconds() {
        assert_eq!(REALTIME_HTTP_TIMEOUT, Duration::from_secs(5));
    }

    #[test]
    fn successful_post_is_checkpointed_and_repeated_hook_is_idempotent() {
        let (address, server) = spawn_sink(204);
        let dir = tempfile::tempdir().unwrap();
        let context = test_context(&dir, address, RetryPolicy::AtLeastOnce);
        let payload = tool_payload("tool-1");
        let handler = RealtimeLangfuseHandler::new();
        handler
            .handle(&HookEvent::PostToolUse, &payload, &context)
            .unwrap();
        let request = server.join().unwrap();
        assert_eq!(
            request["resourceSpans"][0]["scopeSpans"][0]["spans"]
                .as_array()
                .unwrap()
                .len(),
            1
        );
        let state = load_state(&context.state_file);
        assert_eq!(
            state["session-1"]["realtime_export"]["current"]["emitted_tool_use_ids"],
            json!(["tool-1"])
        );

        // No listener remains. A duplicate send would fail the assertion below
        // by adding an export error to the log; the durable emitted checkpoint
        // makes this path a no-op.
        handler
            .handle(&HookEvent::PostToolUse, &payload, &context)
            .unwrap();
        let log = std::fs::read_to_string(dir.path().join("realtime.log")).unwrap();
        assert!(!log.contains("Realtime tool export failed"));
    }

    #[test]
    fn realtime_tool_uses_persisted_vcs_for_every_resource_span() {
        let (address, server) = spawn_sink(204);
        let dir = tempfile::tempdir().unwrap();
        let context = test_context(&dir, address, RetryPolicy::AtLeastOnce);
        save_state(
            &context.state_file,
            &json!({"session-1": {"vcs": {
                "remote_url": "git@git.example.com:team/repo.git",
                "branch": "realtime",
                "cwd": "/repo"
            }}}),
        )
        .unwrap();

        RealtimeLangfuseHandler::new()
            .handle(
                &HookEvent::PostToolUse,
                &tool_payload("tool-with-vcs"),
                &context,
            )
            .unwrap();

        let request = server.join().unwrap();
        let attributes = request["resourceSpans"][0]["resource"]["attributes"]
            .as_array()
            .unwrap();
        assert!(attributes.iter().any(|attribute| {
            attribute["key"] == "vcs.repository.url.full"
                && attribute["value"]["stringValue"] == "git@git.example.com:team/repo.git"
        }));
        assert!(attributes.iter().any(|attribute| {
            attribute["key"] == "vcs.ref.head.name"
                && attribute["value"]["stringValue"] == "realtime"
        }));
    }

    #[test]
    fn every_2xx_status_checkpoints_success() {
        for status in [200, 204, 299] {
            let (address, server) = spawn_sink(status);
            let dir = tempfile::tempdir().unwrap();
            let context = test_context(&dir, address, RetryPolicy::AtLeastOnce);
            RealtimeLangfuseHandler::new()
                .handle(
                    &HookEvent::PostToolUse,
                    &tool_payload(&format!("tool-{status}")),
                    &context,
                )
                .unwrap();
            server.join().unwrap();
            let state = load_state(&context.state_file);
            assert_eq!(
                state["session-1"]["realtime_export"]["current"]["emitted_tool_use_ids"],
                json!([format!("tool-{status}")])
            );
        }
    }

    #[test]
    fn slow_post_does_not_block_an_independent_tool_hook() {
        let (address, first_request_rx, server) = spawn_two_request_sink(204);
        let dir = tempfile::tempdir().unwrap();
        let first_context = test_context(&dir, address, RetryPolicy::AtLeastOnce);
        let second_context = test_context(&dir, address, RetryPolicy::AtLeastOnce);

        let first = std::thread::spawn(move || {
            RealtimeLangfuseHandler::new()
                .handle(
                    &HookEvent::PostToolUse,
                    &tool_payload("tool-concurrent-1"),
                    &first_context,
                )
                .unwrap();
        });
        first_request_rx
            .recv_timeout(Duration::from_secs(1))
            .expect("first request must reach the sink");
        let second = std::thread::spawn(move || {
            RealtimeLangfuseHandler::new()
                .handle(
                    &HookEvent::PostToolUse,
                    &tool_payload("tool-concurrent-2"),
                    &second_context,
                )
                .unwrap();
        });

        first.join().unwrap();
        second.join().unwrap();
        let requests = server.join().unwrap();
        assert_eq!(
            requests.len(),
            2,
            "both tool hooks must POST while the first request is awaiting its response"
        );
        let state = load_state(&dir.path().join("state.json"));
        let emitted = state["session-1"]["realtime_export"]["current"]["emitted_tool_use_ids"]
            .as_array()
            .unwrap();
        assert!(emitted.iter().any(|id| id == "tool-concurrent-1"));
        assert!(emitted.iter().any(|id| id == "tool-concurrent-2"));
    }

    #[test]
    fn failed_tool_hook_exports_error_with_persisted_identity() {
        let (address, server) = spawn_sink(204);
        let dir = tempfile::tempdir().unwrap();
        let context = test_context(&dir, address, RetryPolicy::AtLeastOnce);
        RealtimeLangfuseHandler::new()
            .handle(
                &HookEvent::PostToolUseFailure,
                &failed_tool_payload("tool-failure"),
                &context,
            )
            .unwrap();
        let request = server.join().unwrap();
        let span = &request["resourceSpans"][0]["scopeSpans"][0]["spans"][0];
        let state = load_state(&context.state_file);
        let realtime = &state["session-1"]["realtime_export"]["current"];

        assert_eq!(span["status"]["code"], 2);
        assert_eq!(span["traceId"], realtime["trace_id"]);
        assert_eq!(
            span["parentSpanId"], realtime["root_span_id"],
            "Stop must later reuse the persisted parent identity"
        );
        assert_eq!(
            span["spanId"], realtime["tool_span_ids"]["tool-failure"],
            "Stop filtering is keyed by the same persisted tool identity"
        );
        assert_eq!(realtime["emitted_tool_use_ids"], json!(["tool-failure"]));
        assert!(span["attributes"]
            .as_array()
            .unwrap()
            .iter()
            .any(|attribute| {
                attribute["key"] == "error.type"
                    && attribute["value"]["stringValue"] == "tool_error"
            }));

        let mut stop_state = load_state(&context.state_file);
        let context_id = realtime["context_id"].as_str().unwrap().to_string();
        crate::hooks::langfuse::state::commit_export_success(
            &context.state_file,
            &mut stop_state,
            "session-1",
            7,
            "",
            "2026-07-24T00:00:00.000Z",
            None,
            Some(&[context_id]),
        )
        .unwrap();
        assert!(stop_state["session-1"]["realtime_export"]
            .get("current")
            .is_none());
        assert_eq!(
            stop_state["session-1"]["realtime_export"]["pending"],
            json!([])
        );
    }

    #[test]
    fn failed_delivery_is_never_checkpointed_as_emitted() {
        for (status, policy, expected_status) in [
            (302, RetryPolicy::AtLeastOnce, "non_retryable"),
            (400, RetryPolicy::AtLeastOnce, "non_retryable"),
            (500, RetryPolicy::AtLeastOnce, "non_retryable"),
            (503, RetryPolicy::AtLeastOnce, "failed"),
            (500, RetryPolicy::AtMostOnce, "non_retryable"),
        ] {
            let (address, server) = spawn_sink(status);
            let dir = tempfile::tempdir().unwrap();
            let context = test_context(&dir, address, policy);
            RealtimeLangfuseHandler::new()
                .handle(
                    &HookEvent::PostToolUse,
                    &tool_payload("tool-failed"),
                    &context,
                )
                .unwrap();
            server.join().unwrap();
            let state = load_state(&context.state_file);
            let emitted = state["session-1"]["realtime_export"]["current"]["emitted_tool_use_ids"]
                .as_array()
                .map(|items| items.iter().any(|item| item == "tool-failed"))
                .unwrap_or(false);
            assert!(!emitted, "status={status} policy={policy:?}");
            let context_id = state["session-1"]["realtime_export"]["current"]["context_id"]
                .as_str()
                .unwrap();
            let child_key = tool_child_key(context_id, "tool-failed");
            let ledger = &state["session-1"]["realtime_export"]["child_ledger"][&child_key];
            if policy == RetryPolicy::AtMostOnce {
                assert!(
                    state["session-1"]["realtime_export"]["current"]["failed_tool_exports"]
                        .get("tool-failed")
                        .is_none()
                );
                assert_eq!(ledger["status"], json!("policy_terminal"));
                assert_eq!(
                    ledger["terminal_reason"],
                    json!("post_claim_transport_failure")
                );
            } else {
                assert!(
                    state["session-1"]["realtime_export"]["current"]["failed_tool_exports"]
                        ["tool-failed"]
                        .is_object()
                );
                assert_eq!(
                    state["session-1"]["realtime_export"]["current"]["failed_tool_exports"]
                        ["tool-failed"]["status"],
                    expected_status,
                    "status={status} policy={policy:?}"
                );
                assert_eq!(ledger["status"], json!("failed_retryable"));
            }
        }
    }

    #[test]
    fn next_post_tool_use_batches_prior_failed_tool_for_delayed_compensation() {
        let (address, server) = spawn_status_sequence_sink(vec![503, 204]);
        let dir = tempfile::tempdir().unwrap();
        let context = test_context(&dir, address, RetryPolicy::AtLeastOnce);
        let handler = RealtimeLangfuseHandler::new();

        handler
            .handle(
                &HookEvent::PostToolUse,
                &tool_payload("tool-failed"),
                &context,
            )
            .unwrap();
        let failed_state = load_state(&context.state_file);
        assert!(
            failed_state["session-1"]["realtime_export"]["current"]["failed_tool_exports"]
                ["tool-failed"]
                .is_object()
        );

        handler
            .handle(
                &HookEvent::PostToolUse,
                &tool_payload("tool-next"),
                &context,
            )
            .unwrap();
        let requests = server.join().unwrap();
        assert_eq!(requests.len(), 2);
        assert_eq!(
            requests[1]["resourceSpans"].as_array().unwrap().len(),
            2,
            "the next PostToolUse request must carry the failed and current tool spans"
        );
        let state = load_state(&context.state_file);
        let realtime = &state["session-1"]["realtime_export"]["current"];
        assert_eq!(
            realtime["emitted_tool_use_ids"],
            json!(["tool-failed", "tool-next"])
        );
        assert_eq!(realtime["failed_tool_exports"], json!({}));
    }

    #[test]
    fn next_post_tool_use_does_not_retry_non_retryable_http_failure() {
        let (address, server) = spawn_status_sequence_sink(vec![400, 204]);
        let dir = tempfile::tempdir().unwrap();
        let context = test_context(&dir, address, RetryPolicy::AtLeastOnce);
        let handler = RealtimeLangfuseHandler::new();

        handler
            .handle(
                &HookEvent::PostToolUse,
                &tool_payload("tool-permanent"),
                &context,
            )
            .unwrap();
        handler
            .handle(
                &HookEvent::PostToolUse,
                &tool_payload("tool-next"),
                &context,
            )
            .unwrap();

        let requests = server.join().unwrap();
        assert_eq!(requests.len(), 2);
        assert_eq!(
            requests[1]["resourceSpans"].as_array().unwrap().len(),
            1,
            "the next PostToolUse request must carry only the current tool span"
        );
        let state = load_state(&context.state_file);
        let realtime = &state["session-1"]["realtime_export"]["current"];
        assert_eq!(realtime["emitted_tool_use_ids"], json!(["tool-next"]));
        assert_eq!(
            realtime["failed_tool_exports"]["tool-permanent"]["status"],
            "non_retryable"
        );
    }

    #[test]
    fn unavailable_state_lock_sends_nothing_and_keeps_state_empty() {
        let dir = tempfile::tempdir().unwrap();
        let state_file = dir.path().join("state.json");
        let held_lock = StateLock::acquire(&state_file, 10);
        assert!(held_lock.is_acquired());
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        listener.set_nonblocking(true).unwrap();
        let context = test_context(
            &dir,
            listener.local_addr().unwrap(),
            RetryPolicy::AtLeastOnce,
        );

        RealtimeLangfuseHandler::new()
            .handle(
                &HookEvent::PostToolUse,
                &tool_payload("tool-locked"),
                &context,
            )
            .unwrap();

        assert!(listener.accept().is_err());
        assert_eq!(load_state(&context.state_file), json!({}));
    }
}
