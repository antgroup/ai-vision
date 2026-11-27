use std::collections::HashMap;
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::Path;
use std::process::{Command, Stdio};
use std::sync::{mpsc, Arc, Mutex, MutexGuard};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use serde_json::{json, Value};

static HOOK_PERFORMANCE_TEST_LOCK: Mutex<()> = Mutex::new(());

fn serialize_hook_performance_test() -> MutexGuard<'static, ()> {
    HOOK_PERFORMANCE_TEST_LOCK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
}

fn source(relative: &str) -> String {
    std::fs::read_to_string(Path::new(env!("CARGO_MANIFEST_DIR")).join(relative)).unwrap()
}

fn run_hook(root: &Path, payload: &Value) -> std::process::Output {
    run_hook_with_base_url(root, payload, "http://127.0.0.1:1")
}

fn run_hook_with_base_url(root: &Path, payload: &Value, base_url: &str) -> std::process::Output {
    run_hook_with_base_url_and_start_delay(root, payload, base_url, None)
}

fn run_hook_with_base_url_and_start_delay(
    root: &Path,
    payload: &Value,
    base_url: &str,
    worker_start_delay_ms: Option<u64>,
) -> std::process::Output {
    let mut command = Command::new(env!("CARGO_BIN_EXE_cdxhooks"));
    command
        .env("CDXHOOKS_ENABLE_TELEMETRY", "true")
        .env("CDXHOOKS_TOOL_CONTEXT", "true")
        .env("CDXHOOKS_STATE_DIR", root.join("state"))
        .env("CDXHOOKS_SPOOL_DIR", root.join("spool"))
        .env("CDXHOOKS_LOG_DIR", root.join("logs"))
        .env("CODEX_SESSIONS_DIR", root.join("sessions"))
        .env("CDXHOOKS_LANGFUSE_BASE_URL", base_url)
        .env("CDXHOOKS_LANGFUSE_PUBLIC_KEY", "pk-test")
        .env("CDXHOOKS_LANGFUSE_SECRET_KEY", "sk-test")
        .env("CDXHOOKS_TEST_ALLOW_ENV_EXPORTER", "true")
        .env("CDXHOOKS_METRICS_ENDPOINT", "http://127.0.0.1:1")
        .env("CDXHOOKS_REALTIME_LLM_EXPORT_BATCH_SIZE", "1")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    if let Some(delay_ms) = worker_start_delay_ms {
        command.env("CDXHOOKS_TEST_WORKER_START_DELAY_MS", delay_ms.to_string());
    }
    let mut child = command.spawn().unwrap();
    child
        .stdin
        .take()
        .unwrap()
        .write_all(serde_json::to_string(payload).unwrap().as_bytes())
        .unwrap();
    child.wait_with_output().unwrap()
}

fn run_internal_worker(root: &Path, payload: &Value) -> std::process::Output {
    let mut child = Command::new(env!("CARGO_BIN_EXE_cdxhooks"))
        .arg("__worker")
        .env("CDXHOOKS_INTERNAL_WORKER", "true")
        .env("CDXHOOKS_ENABLE_TELEMETRY", "true")
        .env("CDXHOOKS_TOOL_CONTEXT", "true")
        .env("CDXHOOKS_STATE_DIR", root.join("state"))
        .env("CDXHOOKS_SPOOL_DIR", root.join("spool"))
        .env("CDXHOOKS_LOG_DIR", root.join("logs"))
        .env("CDXHOOKS_SESSION_DIR", root.join("legacy-transcripts"))
        .env("CODEX_SESSIONS_DIR", root.join("sessions"))
        .env("CDXHOOKS_LANGFUSE_BASE_URL", "http://127.0.0.1:1")
        .env("CDXHOOKS_LANGFUSE_PUBLIC_KEY", "pk-test")
        .env("CDXHOOKS_LANGFUSE_SECRET_KEY", "sk-test")
        .env("CDXHOOKS_TEST_ALLOW_ENV_EXPORTER", "true")
        .env("CDXHOOKS_METRICS_ENDPOINT", "http://127.0.0.1:1")
        .env("CDXHOOKS_TEST_TRANSCRIPT_TERMINAL_TIMEOUT_MS", "20")
        .env("CDXHOOKS_STOP_BUDGET_MS", "1000")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    child
        .stdin
        .take()
        .unwrap()
        .write_all(serde_json::to_string(payload).unwrap().as_bytes())
        .unwrap();
    child.wait_with_output().unwrap()
}

fn run_worker_cli(root: &Path, args: &[&str]) -> std::process::Output {
    Command::new(env!("CARGO_BIN_EXE_cdxhooks"))
        .arg("worker")
        .args(args)
        .env("CDXHOOKS_ENABLE_TELEMETRY", "true")
        .env("CDXHOOKS_TOOL_CONTEXT", "true")
        .env("CDXHOOKS_STATE_DIR", root.join("state"))
        .env("CDXHOOKS_SPOOL_DIR", root.join("spool"))
        .env("CDXHOOKS_LOG_DIR", root.join("logs"))
        .env("CODEX_SESSIONS_DIR", root.join("sessions"))
        .env("CDXHOOKS_LANGFUSE_BASE_URL", "http://127.0.0.1:1")
        .env("CDXHOOKS_LANGFUSE_PUBLIC_KEY", "pk-test")
        .env("CDXHOOKS_LANGFUSE_SECRET_KEY", "sk-test")
        .env("CDXHOOKS_TEST_ALLOW_ENV_EXPORTER", "true")
        .env("CDXHOOKS_METRICS_ENDPOINT", "http://127.0.0.1:1")
        .output()
        .unwrap()
}

struct OtlpSink {
    base_url: String,
    bodies: Arc<Mutex<Vec<Value>>>,
    stop: mpsc::Sender<()>,
    handle: Option<JoinHandle<()>>,
}

impl OtlpSink {
    fn start() -> Self {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        listener.set_nonblocking(true).unwrap();
        let base_url = format!("http://{}", listener.local_addr().unwrap());
        let bodies = Arc::new(Mutex::new(Vec::new()));
        let server_bodies = Arc::clone(&bodies);
        let (stop, stop_rx) = mpsc::channel();
        let handle = std::thread::spawn(move || loop {
            if stop_rx.try_recv().is_ok() {
                break;
            }
            match listener.accept() {
                Ok((mut stream, _)) => {
                    stream.set_nonblocking(false).unwrap();
                    if let Some(body) = read_http_json_body(&mut stream) {
                        server_bodies
                            .lock()
                            .unwrap_or_else(|poisoned| poisoned.into_inner())
                            .push(body);
                    }
                    let _ = stream.write_all(
                        b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}",
                    );
                }
                Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                    std::thread::sleep(Duration::from_millis(10));
                }
                Err(error) => panic!("OTLP sink accept failed: {error}"),
            }
        });
        Self {
            base_url,
            bodies,
            stop,
            handle: Some(handle),
        }
    }

    fn snapshot(&self) -> Vec<Value> {
        self.bodies
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .clone()
    }
}

impl Drop for OtlpSink {
    fn drop(&mut self) {
        let _ = self.stop.send(());
        if let Some(handle) = self.handle.take() {
            handle.join().unwrap();
        }
    }
}

fn read_http_json_body(stream: &mut TcpStream) -> Option<Value> {
    stream
        .set_read_timeout(Some(Duration::from_secs(2)))
        .unwrap();
    let mut bytes = Vec::new();
    let mut buffer = [0_u8; 4096];
    let header_end = loop {
        let read = stream.read(&mut buffer).ok()?;
        if read == 0 {
            return None;
        }
        bytes.extend_from_slice(&buffer[..read]);
        if let Some(index) = bytes.windows(4).position(|window| window == b"\r\n\r\n") {
            break index + 4;
        }
    };
    let headers = std::str::from_utf8(&bytes[..header_end]).ok()?;
    let content_length = headers.lines().find_map(|line| {
        let (name, value) = line.split_once(':')?;
        name.eq_ignore_ascii_case("content-length")
            .then(|| value.trim().parse::<usize>().ok())?
    })?;
    while bytes.len().saturating_sub(header_end) < content_length {
        let read = stream.read(&mut buffer).ok()?;
        if read == 0 {
            return None;
        }
        bytes.extend_from_slice(&buffer[..read]);
    }
    serde_json::from_slice(&bytes[header_end..header_end + content_length]).ok()
}

fn wait_until(label: &str, timeout: Duration, mut predicate: impl FnMut() -> bool) {
    let deadline = Instant::now() + timeout;
    while !predicate() {
        assert!(Instant::now() < deadline, "timed out waiting for {label}");
        std::thread::sleep(Duration::from_millis(20));
    }
}

fn session_runtime_dir(root: &Path, session_id: &str) -> std::path::PathBuf {
    root.join("state")
        .join(cdxhooks::common::dirs::session_key(session_id).unwrap())
}

fn read_session_state(root: &Path, session_id: &str) -> Value {
    let dir = session_runtime_dir(root, session_id);
    let mut session = serde_json::Map::new();
    if let Ok(entries) = std::fs::read_dir(dir) {
        for entry in entries.flatten() {
            let path = entry.path();
            if !path
                .file_name()
                .and_then(|name| name.to_str())
                .is_some_and(|name| name.starts_with("state_") && name.ends_with(".json"))
            {
                continue;
            }
            let Some(value) = std::fs::read_to_string(path)
                .ok()
                .and_then(|text| serde_json::from_str::<Value>(&text).ok())
            else {
                continue;
            };
            if let Some(fields) = value.as_object() {
                for (key, value) in fields {
                    session.insert(key.clone(), value.clone());
                }
            }
        }
    }
    json!({(session_id): Value::Object(session)})
}

fn seed_multiple_published_roots(root: &Path, session_id: &str, turn_id: &str) {
    use cdxhooks::hooks::langfuse::realtime_state;
    use cdxhooks::hooks::RetryPolicy;

    let mut state = json!({});
    for (tool_id, trace_id, child_id) in [
        (
            "tool-poison-a",
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "child-poison-a",
        ),
        (
            "tool-poison-b",
            "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "child-poison-b",
        ),
    ] {
        let context = realtime_state::claim_tool_in_state(
            &mut state,
            session_id,
            tool_id,
            "2026-08-17T00:00:00Z",
            trace_id,
            None,
            None,
            None,
            None,
            1,
            RetryPolicy::AtLeastOnce,
        );
        realtime_state::bind_turn_id_in_state(
            &mut state,
            session_id,
            &context.context_id,
            Some(turn_id),
        )
        .unwrap();
        let batch = realtime_state::prepare_realtime_batch_in_state(
            &mut state,
            session_id,
            &context.context_id,
            "tool",
            &[child_id.to_string()],
            child_id,
        )
        .unwrap();
        realtime_state::publish_realtime_batch_in_state(&mut state, session_id, &batch.batch_id)
            .unwrap();
        realtime_state::succeed_realtime_batch_in_state(&mut state, session_id, &batch.batch_id)
            .unwrap();
        realtime_state::rotate_current_in_state(&mut state, session_id);
    }

    let state_dir = session_runtime_dir(root, session_id);
    std::fs::create_dir_all(&state_dir).unwrap();
    cdxhooks::hooks::langfuse::state::save_state(&state_dir.join("state_compat.json"), &state)
        .unwrap();
}

fn seed_successful_realtime_residue(root: &Path, session_id: &str, historical_turn_id: &str) {
    use cdxhooks::hooks::langfuse::realtime_state;
    use cdxhooks::hooks::RetryPolicy;

    let mut state = json!({
        session_id: {
            "offset": 0,
            "buffer": "",
            "pending_messages": [],
            "posted_turn_ids": [historical_turn_id],
            "turn_count": 1
        }
    });
    let context = realtime_state::claim_tool_in_state(
        &mut state,
        session_id,
        "call-historical",
        "2026-08-17T00:00:00Z",
        "11111111111111111111111111111111",
        None,
        None,
        None,
        None,
        1,
        RetryPolicy::AtLeastOnce,
    );
    realtime_state::bind_turn_id_in_state(
        &mut state,
        session_id,
        &context.context_id,
        Some(historical_turn_id),
    )
    .unwrap();
    let batch = realtime_state::prepare_realtime_batch_in_state(
        &mut state,
        session_id,
        &context.context_id,
        "tool",
        &["child-historical".to_string()],
        "child-historical",
    )
    .unwrap();
    realtime_state::publish_realtime_batch_in_state(&mut state, session_id, &batch.batch_id)
        .unwrap();
    realtime_state::succeed_realtime_batch_in_state(&mut state, session_id, &batch.batch_id)
        .unwrap();

    let state_dir = session_runtime_dir(root, session_id);
    std::fs::create_dir_all(&state_dir).unwrap();
    cdxhooks::hooks::langfuse::state::save_state(&state_dir.join("state_compat.json"), &state)
        .unwrap();
}

fn otlp_spans(bodies: &[Value]) -> Vec<Value> {
    bodies
        .iter()
        .flat_map(|body| {
            body.get("resourceSpans")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
        })
        .flat_map(|resource| {
            resource
                .get("scopeSpans")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
        })
        .flat_map(|scope| {
            scope
                .get("spans")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
        })
        .cloned()
        .collect()
}

fn span_attr<'a>(span: &'a Value, key: &str) -> Option<&'a str> {
    span.get("attributes")?
        .as_array()?
        .iter()
        .find(|attribute| attribute.get("key").and_then(Value::as_str) == Some(key))?
        .pointer("/value/stringValue")?
        .as_str()
}

fn assert_complete_single_root_topology(bodies: &[Value]) {
    let spans = otlp_spans(bodies);
    let chat_emissions = spans
        .iter()
        .filter(|span| span_attr(span, "gen_ai.span.kind") == Some("CHAT"))
        .count();
    assert_eq!(chat_emissions, 1, "CHAT root was exported more than once");
    let unique = spans
        .into_iter()
        .filter_map(|span| {
            let span_id = span.get("spanId")?.as_str()?.to_string();
            Some((span_id, span))
        })
        .collect::<HashMap<_, _>>();
    let roots = unique
        .values()
        .filter(|span| span_attr(span, "gen_ai.span.kind") == Some("CHAT"))
        .collect::<Vec<_>>();
    assert_eq!(roots.len(), 1, "expected one CHAT root: {unique:#?}");
    let root_id = roots[0]["spanId"].as_str().unwrap();
    let trace_id = roots[0]["traceId"].as_str().unwrap();
    for kind in ["LLM", "TOOL"] {
        let children = unique
            .values()
            .filter(|span| span_attr(span, "gen_ai.span.kind") == Some(kind))
            .collect::<Vec<_>>();
        assert!(!children.is_empty(), "missing {kind} span: {unique:#?}");
        for child in children {
            assert_eq!(child["traceId"], trace_id);
            assert_eq!(child["parentSpanId"], root_id);
        }
    }
    for span in unique.values() {
        if let Some(parent_id) = span.get("parentSpanId").and_then(Value::as_str) {
            assert!(
                unique.contains_key(parent_id),
                "unresolved parent {parent_id} for span {span:#?}"
            );
        }
    }
}

fn wait_for_complete_topology(sink: &OtlpSink, root: &Path, session_id: &str) {
    let deadline = Instant::now() + Duration::from_secs(10);
    loop {
        let bodies = sink.snapshot();
        let spans = otlp_spans(&bodies);
        if ["CHAT", "LLM", "TOOL"].iter().all(|kind| {
            spans
                .iter()
                .any(|span| span_attr(span, "gen_ai.span.kind") == Some(kind))
        }) {
            return;
        }
        assert!(
            Instant::now() < deadline,
            "timed out waiting for complete topology; spans={:#?}; posted={:?}; pending_reason={:?}; logs={}",
            spans
                .iter()
                .map(|span| (
                    span.get("name").and_then(Value::as_str),
                    span_attr(span, "gen_ai.span.kind"),
                    span.get("spanId").and_then(Value::as_str),
                    span.get("parentSpanId").and_then(Value::as_str),
                    span.get("traceId").and_then(Value::as_str),
                ))
                .collect::<Vec<_>>(),
            read_session_state(root, session_id)[session_id]["posted_turn_ids"],
            read_session_state(root, session_id)[session_id]["pending_export_reason"],
            std::fs::read_dir(root.join("logs"))
                .ok()
                .into_iter()
                .flatten()
                .flatten()
                .filter_map(|entry| std::fs::read_to_string(entry.path()).ok())
                .collect::<Vec<_>>()
                .join("\n"),
        );
        std::thread::sleep(Duration::from_millis(20));
    }
}

fn wait_for_chat_export(sink: &OtlpSink, root: &Path, session_id: &str) {
    let deadline = Instant::now() + Duration::from_secs(10);
    loop {
        let bodies = sink.snapshot();
        let spans = otlp_spans(&bodies);
        if spans
            .iter()
            .any(|span| span_attr(span, "gen_ai.span.kind") == Some("CHAT"))
        {
            return;
        }
        assert!(
            Instant::now() < deadline,
            "timed out waiting for CHAT export; spans={:#?}; posted={:?}; pending_reason={:?}; logs={}",
            spans
                .iter()
                .map(|span| (
                    span.get("name").and_then(Value::as_str),
                    span_attr(span, "gen_ai.span.kind"),
                    span.get("spanId").and_then(Value::as_str),
                    span.get("parentSpanId").and_then(Value::as_str),
                    span.get("traceId").and_then(Value::as_str),
                ))
                .collect::<Vec<_>>(),
            read_session_state(root, session_id)[session_id]["posted_turn_ids"],
            read_session_state(root, session_id)[session_id]["pending_export_reason"],
            std::fs::read_dir(root.join("logs"))
                .ok()
                .into_iter()
                .flatten()
                .flatten()
                .filter_map(|entry| std::fs::read_to_string(entry.path()).ok())
                .collect::<Vec<_>>()
                .join("\n"),
        );
        std::thread::sleep(Duration::from_millis(20));
    }
}

#[test]
fn parent_source_has_no_handler_http_metrics_or_payload_logging() {
    let main = source("src/main.rs");
    let parent = main
        .split_once("fn run_parent_hook()")
        .unwrap()
        .1
        .split_once("fn run_internal_worker_entry()")
        .unwrap()
        .0;
    for forbidden in [
        "HookHandlerRegistry",
        ".dispatch(",
        "MetricsCollector",
        "flush_silent",
        "payload_for_log",
        "StateLock",
        "common::spool",
    ] {
        assert!(
            !parent.contains(forbidden),
            "parent fast path must not contain {forbidden}"
        );
    }
    assert!(parent.contains("build_pretooluse_control_response"));
    assert!(parent.contains("enqueue_and_wake"));
    assert!(!parent.contains("spawn_detached_raw"));
    assert!(!parent.contains("publish_ingress_fact"));
    assert!(parent.contains("session_stop_intent_file"));
}

#[test]
fn production_source_has_no_transcript_snapshot_contract() {
    let handler = source("src/hooks/langfuse/langfuse_handler.rs");
    let dirs = source("src/common/dirs.rs");
    assert!(!handler.contains("copy_transcript_to_session_dir"));
    assert!(!handler.contains("session_codex_"));
    assert!(!dirs.contains("CDXHOOKS_SESSION_DIR"));

    let stop_prologue = handler
        .split_once("fn handle_stop_event(")
        .unwrap()
        .1
        .split_once("let mut state = load_state")
        .unwrap()
        .0;
    assert!(stop_prologue.contains("extract_session_meta_ids_from_path"));
    assert!(
        !stop_prologue.contains("read_to_string(&transcript_path)"),
        "Stop identity lookup must stay bounded to the transcript prefix"
    );
}

#[test]
fn ordinary_hook_does_not_recover_stop_or_consume_stop_budget() {
    let _guard = serialize_hook_performance_test();
    let root = tempfile::tempdir().unwrap();
    let session_id = "recovered-stop-session";
    let transcript = root.path().join("sessions/recovered.jsonl");
    std::fs::create_dir_all(transcript.parent().unwrap()).unwrap();
    std::fs::write(
        &transcript,
        format!(
            "{{\"timestamp\":\"2026-08-14T00:00:00Z\",\"type\":\"session_meta\",\"payload\":{{\"id\":\"{session_id}\",\"thread_source\":\"user\"}}}}\n"
        ),
    )
    .unwrap();

    let key = cdxhooks::common::dirs::session_key(session_id).unwrap();
    let state_dir = root.path().join("state").join(key);
    std::fs::create_dir_all(&state_dir).unwrap();
    let intent = state_dir.join("state_stop_intent.json");
    std::fs::write(
        &intent,
        serde_json::to_vec(&json!({
            "schema_version": 1,
            "hook_event_name": "Stop",
            "session_id": session_id,
            "transcript_path": transcript,
            "created_at": "2026-08-14T00:00:00Z",
            "status": "pending"
        }))
        .unwrap(),
    )
    .unwrap();

    let output = run_internal_worker(
        root.path(),
        &json!({
            "hook_event_name": "PreToolUse",
            "session_id": session_id,
            "tool_name": "Bash",
            "tool_use_id": "tool-recovery",
            "tool_input": {"command": "true"}
        }),
    );
    assert!(
        output.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(
        intent.exists(),
        "ordinary hook consumed the pending Stop intent"
    );
    let logs = walk_text(&root.path().join("logs"));
    assert!(!logs.contains("source=recovered"));
    assert!(!logs.contains("Hook budget: event=Stop"));
    assert!(!logs.contains("Hook event: Stop"));
    let timing = logs
        .lines()
        .filter_map(|line| serde_json::from_str::<Value>(line).ok())
        .find(|value| value["kind"] == "cdxhooks.phase_timing" && value["component"] == "worker")
        .expect("missing worker phase timing");
    let handlers_ms = timing["elapsed_ms"]["handlers"]
        .as_u64()
        .expect("missing handlers timing");
    assert!(
        handlers_ms < 1_000,
        "ordinary hook handlers were delayed without a recovered Stop: {handlers_ms}ms\n{logs}"
    );
}

#[test]
fn production_stop_does_not_copy_host_transcript() {
    let _guard = serialize_hook_performance_test();
    let root = tempfile::tempdir().unwrap();
    let session_id = "no-transcript-copy-session";
    let transcript = root.path().join("sessions/source.jsonl");
    std::fs::create_dir_all(transcript.parent().unwrap()).unwrap();
    std::fs::write(
        &transcript,
        format!(
            "{{\"timestamp\":\"2026-08-14T00:00:00Z\",\"type\":\"session_meta\",\"payload\":{{\"id\":\"{session_id}\",\"thread_source\":\"user\"}}}}\n"
        ),
    )
    .unwrap();

    let output = run_internal_worker(
        root.path(),
        &json!({
            "hook_event_name": "Stop",
            "session_id": session_id,
            "transcript_path": transcript
        }),
    );
    assert!(
        output.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(
        !root.path().join("legacy-transcripts").exists(),
        "production Stop created a transcript snapshot directory"
    );
}

#[test]
fn worker_handoff_is_a_durable_state_queue_not_per_event_stdin() {
    let worker = source("src/worker.rs");
    assert!(worker.contains("64 * 1024 * 1024"));
    assert!(worker.contains("worker.queue.jsonl") || worker.contains("session_worker_queue_file"));
    assert!(worker.contains("append_queue"));
    assert!(worker.contains("SessionOwner::try_acquire"));
    assert!(worker.contains("drain_queue"));
    assert!(!worker.contains("tempfile::tempfile()"));
    assert!(!worker.contains("Stdio::piped"));
}

#[test]
fn worker_startup_is_portable_and_does_not_use_business_fork_handoff() {
    let worker = source("src/worker.rs");
    let lock = source("src/common/file_lock.rs");
    assert!(worker.contains("Command::new"));
    assert!(worker.contains("Stdio::null"));
    assert!(worker.contains("command.process_group(0)"));
    assert!(worker.contains("START_TOKEN_ENV"));
    assert!(worker.contains("acquire_queued_worker"));
    assert!(!worker.contains("libc::fork"));
    assert!(!worker.contains("libc::setsid"));
    assert!(!worker.contains("/dev/null"));
    assert!(lock.contains("LockFileEx"));
    assert!(lock.contains("cfg(windows)"));
    assert!(lock.contains("cfg(unix)"));

    let parent_wake = worker
        .split_once("pub fn wake_session(")
        .unwrap()
        .1
        .split_once("pub fn wake_session_confirmed")
        .unwrap()
        .0;
    let confirmed_handoff = worker
        .split_once("pub fn wake_session_confirmed")
        .unwrap()
        .1
        .split_once("fn fail_unowned_start_marker")
        .unwrap()
        .0;
    assert!(!parent_wake.contains("wait_for_spawned_worker_start"));
    assert!(confirmed_handoff.contains("wait_for_spawned_worker_start"));
}

#[cfg(unix)]
#[test]
fn concurrent_same_session_hooks_append_one_queue_without_spawning_waiters() {
    let _guard = serialize_hook_performance_test();
    let root = tempfile::tempdir().unwrap();
    let session_id = "single-worker-queue-session";
    let previous_state_dir = std::env::var("CDXHOOKS_STATE_DIR").ok();
    std::env::set_var("CDXHOOKS_STATE_DIR", root.path().join("state"));
    let owner = cdxhooks::common::runtime_state::SessionOwner::acquire(
        session_id,
        Duration::from_millis(100),
    )
    .unwrap();

    const EVENTS: usize = 16;
    let handles = (0..EVENTS)
        .map(|sequence| {
            let root = root.path().to_path_buf();
            std::thread::spawn(move || {
                run_hook(
                    &root,
                    &json!({
                        "hook_event_name": "PreCompact",
                        "session_id": session_id,
                        "sequence": sequence
                    }),
                )
            })
        })
        .collect::<Vec<_>>();
    for handle in handles {
        let output = handle.join().unwrap();
        assert!(
            output.status.success(),
            "hook failed: {}",
            String::from_utf8_lossy(&output.stderr)
        );
    }

    let runtime_dir = session_runtime_dir(root.path(), session_id);
    let queue = runtime_dir.join("worker.queue.jsonl");
    let queued = std::fs::read_to_string(&queue).unwrap();
    let mut sequences = queued
        .lines()
        .map(|line| {
            serde_json::from_str::<Value>(line).unwrap()["payload"]["sequence"]
                .as_u64()
                .unwrap()
        })
        .collect::<Vec<_>>();
    sequences.sort_unstable();
    assert_eq!(sequences, (0..EVENTS as u64).collect::<Vec<_>>());
    assert_eq!(
        phase_event_count(&root.path().join("logs"), "worker", "PreCompact"),
        0,
        "busy session spawned blocked per-event workers"
    );

    drop(owner);
    let wake = run_hook(
        root.path(),
        &json!({
            "hook_event_name": "PreCompact",
            "session_id": session_id,
            "sequence": EVENTS
        }),
    );
    assert!(wake.status.success());
    wait_until("single worker queue drain", Duration::from_secs(10), || {
        phase_event_count(&root.path().join("logs"), "worker", "PreCompact") == EVENTS + 1
            && std::fs::metadata(&queue).is_ok_and(|metadata| metadata.len() == 0)
    });
    let worker_pids = worker_process_ids(&root.path().join("logs"));
    assert_eq!(
        worker_pids.len(),
        1,
        "the burst was drained by more than one real OS worker process: {worker_pids:?}"
    );

    match previous_state_dir {
        Some(value) => std::env::set_var("CDXHOOKS_STATE_DIR", value),
        None => std::env::remove_var("CDXHOOKS_STATE_DIR"),
    }
}

#[cfg(unix)]
#[test]
fn worker_cli_reports_and_recovers_an_orphaned_disk_queue() {
    let _guard = serialize_hook_performance_test();
    let root = tempfile::tempdir().unwrap();
    let session_id = "worker-cli-queue-recovery";
    let previous_state_dir = std::env::var("CDXHOOKS_STATE_DIR").ok();
    std::env::set_var("CDXHOOKS_STATE_DIR", root.path().join("state"));
    let owner = cdxhooks::common::runtime_state::SessionOwner::acquire(
        session_id,
        Duration::from_millis(100),
    )
    .unwrap();
    let output = run_hook(
        root.path(),
        &json!({
            "hook_event_name": "PreCompact",
            "session_id": session_id,
            "sequence": 1
        }),
    );
    assert!(output.status.success());
    drop(owner);

    let status = run_worker_cli(root.path(), &["status", "--session-id", session_id]);
    assert!(status.status.success());
    let status_json: Value = serde_json::from_slice(&status.stdout).unwrap();
    assert_eq!(status_json["status"]["pending_queue_items"], 1);
    assert_eq!(status_json["status"]["dropped_queue_items"], 0);

    let wake = run_worker_cli(root.path(), &["once", "--session-id", session_id]);
    assert!(
        wake.status.success(),
        "worker once failed: {}",
        String::from_utf8_lossy(&wake.stderr)
    );
    assert_eq!(
        serde_json::from_slice::<Value>(&wake.stdout).unwrap()["scheduled"],
        1
    );
    let queue = session_runtime_dir(root.path(), session_id).join("worker.queue.jsonl");
    wait_until("worker CLI queue recovery", Duration::from_secs(10), || {
        phase_event_count(&root.path().join("logs"), "worker", "PreCompact") == 1
            && std::fs::metadata(&queue).is_ok_and(|metadata| metadata.len() == 0)
    });

    match previous_state_dir {
        Some(value) => std::env::set_var("CDXHOOKS_STATE_DIR", value),
        None => std::env::remove_var("CDXHOOKS_STATE_DIR"),
    }
}

#[test]
fn prompt_seed_is_reused_by_pretool_control_response() {
    let _guard = serialize_hook_performance_test();
    let root = tempfile::tempdir().unwrap();
    let trace_id = "0af7651916cd43dd8448eb211c80319c";
    let upstream = format!("00-{trace_id}-b7ad6b7169203331-01");
    let prompt = json!({
        "hook_event_name": "UserPromptSubmit",
        "session_id": "session/one",
        "turn_id": "turn-1",
        "prompt": "CANARY_PROMPT_MUST_NOT_BE_LOGGED"
    });
    let mut prompt_child = Command::new(env!("CARGO_BIN_EXE_cdxhooks"))
        .env("CDXHOOKS_ENABLE_TELEMETRY", "true")
        .env("CDXHOOKS_TOOL_CONTEXT", "true")
        .env("CDXHOOKS_STATE_DIR", root.path().join("state"))
        .env("CDXHOOKS_SPOOL_DIR", root.path().join("spool"))
        .env("CDXHOOKS_LOG_DIR", root.path().join("logs"))
        .env("CODEX_SESSIONS_DIR", root.path().join("sessions"))
        .env("TRACEPARENT", &upstream)
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .unwrap();
    prompt_child
        .stdin
        .take()
        .unwrap()
        .write_all(serde_json::to_string(&prompt).unwrap().as_bytes())
        .unwrap();
    assert!(prompt_child.wait().unwrap().success());

    let pre = json!({
        "hook_event_name": "PreToolUse",
        "session_id": "session/one",
        "tool_name": "Bash",
        "tool_use_id": "tool-1",
        "tool_input": {"command": "echo CANARY_TOOL_INPUT"}
    });
    let output = run_hook(root.path(), &pre);
    assert!(
        output.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );
    let response: Value = serde_json::from_slice(&output.stdout).unwrap();
    let command = response["hookSpecificOutput"]["updatedInput"]["command"]
        .as_str()
        .unwrap();
    assert!(command.contains(&format!("TRACEPARENT='00-{trace_id}-")));
    assert!(command.ends_with("echo CANARY_TOOL_INPUT"));
    let pre_parent_total_ms = parent_total_ms(&root.path().join("logs"), "PreToolUse")
        .expect("PreToolUse parent timing");
    let windows_release_budget = cfg!(windows)
        && std::env::var("AIVISION_WINDOWS_RELEASE_PROMPT_SEED_BUDGET").as_deref() == Ok("true");
    let budget_ms = if windows_release_budget { 200 } else { 50 };
    eprintln!("Codex Prompt seed PreToolUse parent={pre_parent_total_ms} ms, budget < {budget_ms} ms");
    assert!(
        pre_parent_total_ms < budget_ms,
        "PreToolUse parent fast path took {pre_parent_total_ms}ms (budget < {budget_ms}ms)"
    );

    let key = cdxhooks::common::dirs::session_key("session/one").unwrap();
    let session_state = root.path().join("state").join(key);
    let seed = session_state.join("state_turn_seed.json");
    assert!(seed.is_file());
    let deadline = Instant::now() + Duration::from_secs(10);
    loop {
        let tool_trace_id = std::fs::read_to_string(session_state.join("state_tools.json"))
            .ok()
            .and_then(|contents| serde_json::from_str::<Value>(&contents).ok())
            .and_then(|state| {
                state["records"]["tool-1"]["trace_id"]
                    .as_str()
                    .map(str::to_string)
            });
        let generation_trace_id =
            std::fs::read_to_string(session_state.join("state_generation.json"))
                .ok()
                .and_then(|contents| serde_json::from_str::<Value>(&contents).ok())
                .and_then(|state| {
                    state["realtime_export"]["current"]["trace_id"]
                        .as_str()
                        .map(str::to_string)
                });
        if tool_trace_id.as_deref() == Some(trace_id)
            && generation_trace_id.as_deref() == Some(trace_id)
        {
            break;
        }
        assert!(
            Instant::now() < deadline,
            "worker state did not converge on parent turn seed: tool={tool_trace_id:?} generation={generation_trace_id:?}"
        );
        std::thread::sleep(Duration::from_millis(20));
    }
    let logs = walk_text(&root.path().join("logs"));
    assert!(!logs.contains("CANARY_PROMPT_MUST_NOT_BE_LOGGED"));
    assert!(!logs.contains("CANARY_TOOL_INPUT"));
    assert!(!logs.contains("stdin payload:"));
    assert!(logs.contains("cdxhooks.phase_timing"));
}

#[test]
fn post_parent_returns_without_waiting_for_remote_work() {
    let _guard = serialize_hook_performance_test();
    let root = tempfile::tempdir().unwrap();
    let payload = json!({
        "hook_event_name": "PostToolUse",
        "session_id": "latency-session",
        "tool_name": "Bash",
        "tool_use_id": "tool-1",
        "tool_input": {"command": "true"},
        "tool_response": {"output": "ok"}
    });
    let started = Instant::now();
    let output = run_hook(root.path(), &payload);
    let elapsed = started.elapsed();
    assert!(output.status.success());
    assert!(
        elapsed < Duration::from_secs(1),
        "debug binary did not return promptly: {elapsed:?}"
    );
    let post_parent_total_ms = parent_total_ms(&root.path().join("logs"), "PostToolUse")
        .expect("PostToolUse parent timing");
    let budget_ms = if cfg!(all(windows, debug_assertions)) {
        400
    } else {
        50
    };
    assert!(
        post_parent_total_ms < budget_ms,
        "PostToolUse parent fast path took {post_parent_total_ms}ms (budget < {budget_ms}ms)"
    );
}

#[test]
fn pretool_parent_timing_p95_stays_below_50_ms() {
    let _guard = serialize_hook_performance_test();
    let root = tempfile::tempdir().unwrap();
    let session_id = "p95-session";
    for index in 0..22 {
        let payload = json!({
            "hook_event_name": "PreToolUse",
            "session_id": session_id,
            "tool_name": "Bash",
            "tool_use_id": format!("tool-{index}"),
            "tool_input": {"command": "true"}
        });
        let output = run_hook(root.path(), &payload);
        assert!(
            output.status.success(),
            "stderr={}",
            String::from_utf8_lossy(&output.stderr)
        );
    }

    let records = parent_phase_records(&root.path().join("logs"), "PreToolUse");
    assert_eq!(records.len(), 22);
    assert!(
        records.iter().all(|record| record["outcome"] == "queued"),
        "each hook must accept its unique event into the durable queue"
    );
    // Keep the real consumer and exclude the first two invocations from p95.
    let phase_samples = records[2..]
        .iter()
        .map(|record| &record["elapsed_ms"])
        .collect::<Vec<_>>();
    let mut samples = phase_samples
        .iter()
        .map(|phases| phases["total"].as_u64().expect("parent total milliseconds"))
        .collect::<Vec<_>>();
    samples.sort_unstable();
    let p95 = samples[(samples.len() * 95).div_ceil(100) - 1];
    let budget_ms = 50;
    let within_budget = p95 < budget_ms;
    let report_only = cfg!(windows)
        && std::env::var("AIVISION_WINDOWS_HOOK_P95_REPORT_ONLY").as_deref() == Ok("true");
    let status = if within_budget {
        "within-budget"
    } else {
        "over-budget"
    };
    let mode = if report_only { "report-only" } else { "strict" };
    let report = format!(
        "Codex PreToolUse p95: parent={p95} ms, budget < {budget_ms} ms, status={status}, mode={mode}"
    );
    eprintln!("{report}");
    if let Some(path) = std::env::var_os("GITHUB_STEP_SUMMARY").filter(|path| !path.is_empty()) {
        let mut summary = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(path)
            .expect("open GitHub Actions summary for Codex PreToolUse p95");
        writeln!(summary, "{report}").expect("write GitHub Actions summary for Codex PreToolUse p95");
    }
    assert!(
        within_budget || report_only,
        "PreToolUse parent p95 took {p95}ms; ordered phase samples (ms)={:?}",
        phase_samples
    );
    std::thread::sleep(Duration::from_millis(200));
}

#[test]
fn delayed_worker_start_does_not_delay_parent_or_strand_the_queue() {
    let _guard = serialize_hook_performance_test();
    let root = tempfile::tempdir().unwrap();
    let session_id = "delayed-worker-start";
    let payload = json!({
        "hook_event_name": "PostToolUse",
        "session_id": session_id,
        "tool_name": "Bash",
        "tool_use_id": "tool-delayed",
        "tool_input": {"command": "true"},
        "tool_response": {"output": "ok"}
    });
    let output = run_hook_with_base_url_and_start_delay(
        root.path(),
        &payload,
        "http://127.0.0.1:1",
        Some(150),
    );
    assert!(output.status.success());
    let parent_total_ms = parent_total_ms(&root.path().join("logs"), "PostToolUse").unwrap();
    // The release budget must remain below the worker's injected 150 ms delay.
    let budget_ms = if cfg!(all(windows, debug_assertions)) {
        100
    } else if cfg!(target_os = "macos")
        && std::env::var("AIVISION_MACOS_RELEASE_HOOK_BUDGET").as_deref() == Ok("true")
    {
        120
    } else {
        50
    };
    eprintln!("Codex delayed-worker startup: parent={parent_total_ms} ms, budget < {budget_ms} ms");

    let queue = session_runtime_dir(root.path(), session_id).join("worker.queue.jsonl");
    wait_until("delayed worker queue drain", Duration::from_secs(5), || {
        phase_event_count(&root.path().join("logs"), "worker", "PostToolUse") == 1
            && std::fs::metadata(&queue).is_ok_and(|metadata| metadata.len() == 0)
    });
    assert!(
        parent_total_ms < budget_ms,
        "delayed worker blocked the parent for {parent_total_ms} ms, budget < {budget_ms} ms"
    );
}

#[test]
fn detached_worker_does_not_keep_hook_output_pipes_open() {
    let _guard = serialize_hook_performance_test();
    let root = tempfile::tempdir().unwrap();
    let session_id = "windows-detached-pipe-eof";
    let payload = json!({
        "hook_event_name": "PostToolUse",
        "session_id": session_id,
        "tool_name": "Bash",
        "tool_use_id": "tool-windows-pipe-eof",
        "tool_input": {"command": "true"},
        "tool_response": {"output": "ok"}
    });

    let started = Instant::now();
    let output = run_hook_with_base_url_and_start_delay(
        root.path(),
        &payload,
        "http://127.0.0.1:1",
        Some(3_000),
    );
    let elapsed = started.elapsed();

    assert!(
        output.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(
        elapsed < Duration::from_millis(1_500),
        "hook output pipes stayed open for the detached worker: elapsed={elapsed:?}"
    );

    let queue = session_runtime_dir(root.path(), session_id).join("worker.queue.jsonl");
    wait_until(
        "detached worker queue drain",
        Duration::from_secs(8),
        || {
            phase_event_count(&root.path().join("logs"), "worker", "PostToolUse") == 1
                && std::fs::metadata(&queue).is_ok_and(|metadata| metadata.len() == 0)
        },
    );
}

#[test]
fn one_second_collector_does_not_delay_post_parent() {
    let _guard = serialize_hook_performance_test();
    let root = tempfile::tempdir().unwrap();
    let transcript = root.path().join("sessions/realtime.jsonl");
    std::fs::create_dir_all(transcript.parent().unwrap()).unwrap();
    std::fs::write(
        &transcript,
        source("tests/fixtures/codex_realtime_open_turn_exec_call.jsonl"),
    )
    .unwrap();
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    listener.set_nonblocking(true).unwrap();
    let base_url = format!("http://{}", listener.local_addr().unwrap());

    let pre = json!({
        "hook_event_name": "PreToolUse",
        "session_id": "session-redacted",
        "turn_id": "turn-redacted",
        "tool_name": "Bash",
        "tool_use_id": "call_redacted",
        "tool_input": {"command": "true"}
    });
    assert!(run_hook_with_base_url(root.path(), &pre, &base_url)
        .status
        .success());

    let post = json!({
        "hook_event_name": "PostToolUse",
        "session_id": "session-redacted",
        "turn_id": "turn-redacted",
        "tool_name": "Bash",
        "tool_use_id": "call_redacted",
        "transcript_path": transcript,
        "tool_input": {"command": "true"},
        "tool_response": {"output": "ok"}
    });
    // Bind before PreToolUse so the worker gets the collector address. Start
    // the accept budget for PostToolUse after the setup hook has returned.
    let slow_collector = move || -> Result<Instant, String> {
        let deadline = Instant::now() + Duration::from_secs(8);
        let mut stream = loop {
            match listener.accept() {
                Ok((stream, _)) => break stream,
                Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                    if Instant::now() >= deadline {
                        return Err("timed out accepting the PostToolUse request".into());
                    }
                    std::thread::sleep(Duration::from_millis(10));
                }
                Err(error) => return Err(format!("accept failed: {error}")),
            }
        };
        stream
            .set_nonblocking(false)
            .map_err(|error| format!("configure collector connection: {error}"))?;
        stream
            .set_write_timeout(Some(Duration::from_secs(2)))
            .map_err(|error| format!("configure collector response timeout: {error}"))?;
        let body = read_http_json_body(&mut stream).ok_or("expected an HTTP JSON request")?;
        if otlp_spans(&[body]).is_empty() {
            return Err("expected OTLP trace spans".into());
        }
        std::thread::sleep(Duration::from_secs(1));
        let response_started = Instant::now();
        stream
            .write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}")
            .map_err(|error| format!("send slow collector response: {error}"))?;
        Ok(response_started)
    };
    let (output, parent_returned, elapsed, collector) = std::thread::scope(|scope| {
        let server = scope.spawn(slow_collector);
        let started = Instant::now();
        let output = run_hook_with_base_url(root.path(), &post, &base_url);
        let parent_returned = Instant::now();
        let collector = server.join().expect("slow collector thread panicked");
        let elapsed = parent_returned.duration_since(started);
        (output, parent_returned, elapsed, collector)
    });
    assert!(output.status.success());
    assert!(
        elapsed < Duration::from_millis(250),
        "PostToolUse parent waited for slow collector: {elapsed:?}"
    );
    let post_parent_total_ms = parent_total_ms(&root.path().join("logs"), "PostToolUse")
        .expect("PostToolUse parent timing");
    assert!(
        post_parent_total_ms < 50,
        "PostToolUse parent phase took {post_parent_total_ms}ms"
    );
    let response_started = collector.unwrap_or_else(|error| {
        panic!(
            "detached worker did not complete the slow collector exchange: {error}; logs={}",
            walk_text(&root.path().join("logs"))
        )
    });
    assert!(
        parent_returned < response_started,
        "PostToolUse parent returned after the slow collector responded"
    );
}

#[test]
fn production_parent_worker_exports_one_complete_same_turn_topology() {
    let _guard = serialize_hook_performance_test();
    let root = tempfile::tempdir().unwrap();
    let sink = OtlpSink::start();
    let session_id = "topology-e2e-session";
    let turn_id = "turn-fresh";
    let transcript = root.path().join("sessions/topology.jsonl");
    std::fs::create_dir_all(transcript.parent().unwrap()).unwrap();
    std::fs::write(
        &transcript,
        format!(
            r#"{{"timestamp":"2026-08-17T00:00:00Z","type":"session_meta","payload":{{"id":"{session_id}","thread_source":"user"}}}}
{{"timestamp":"2026-08-17T00:00:01Z","type":"event_msg","payload":{{"type":"task_started","turn_id":"{turn_id}"}}}}
{{"timestamp":"2026-08-17T00:00:01.100Z","type":"event_msg","payload":{{"type":"user_message","message":"run one check"}}}}
{{"timestamp":"2026-08-17T00:00:01.200Z","type":"turn_context","payload":{{"turn_id":"{turn_id}","cwd":"/repo","model":"gpt-5.5"}}}}
{{"timestamp":"2026-08-17T00:00:01.300Z","type":"event_msg","payload":{{"type":"agent_message","phase":"commentary","message":"running"}}}}
{{"timestamp":"2026-08-17T00:00:01.400Z","type":"response_item","payload":{{"type":"function_call","name":"exec_command","arguments":"{{\"cmd\":\"true\"}}","call_id":"call-fresh"}}}}
{{"timestamp":"2026-08-17T00:00:01.500Z","type":"response_item","payload":{{"type":"function_call_output","call_id":"call-fresh","output":"ok"}}}}
{{"timestamp":"2026-08-17T00:00:01.600Z","type":"event_msg","payload":{{"type":"agent_message","phase":"final_answer","message":"done"}}}}
{{"timestamp":"2026-08-17T00:00:01.700Z","type":"event_msg","payload":{{"type":"task_complete","turn_id":"{turn_id}","last_agent_message":"done"}}}}
"#
        ),
    )
    .unwrap();

    for payload in [
        json!({
            "hook_event_name": "UserPromptSubmit",
            "session_id": session_id,
            "turn_id": turn_id,
            "prompt": "run one check",
            "cwd": "/repo"
        }),
        json!({
            "hook_event_name": "PreToolUse",
            "session_id": session_id,
            "turn_id": turn_id,
            "transcript_path": transcript,
            "tool_name": "Bash",
            "tool_use_id": "call-fresh",
            "tool_input": {"command": "true"}
        }),
        json!({
            "hook_event_name": "PostToolUse",
            "session_id": session_id,
            "turn_id": turn_id,
            "transcript_path": transcript,
            "tool_name": "Bash",
            "tool_use_id": "call-fresh",
            "tool_input": {"command": "true"},
            "tool_response": {"output": "ok"}
        }),
    ] {
        let event = payload["hook_event_name"].as_str().unwrap().to_string();
        let output = run_hook_with_base_url(root.path(), &payload, &sink.base_url);
        assert!(
            output.status.success(),
            "hook failed: {}",
            String::from_utf8_lossy(&output.stderr)
        );
        wait_until(
            &format!("fresh {event} worker completion"),
            Duration::from_secs(5),
            || phase_event_count(&root.path().join("logs"), "worker", &event) > 0,
        );
    }

    wait_until(
        "completed fresh tool record",
        Duration::from_secs(5),
        || {
            read_session_state(root.path(), session_id)[session_id]["records"]["call-fresh"]
                ["completed_at_unix_nano"]
                .is_string()
        },
    );
    let stop = run_hook_with_base_url(
        root.path(),
        &json!({
            "hook_event_name": "Stop",
            "session_id": session_id,
            "transcript_path": transcript
        }),
        &sink.base_url,
    );
    assert!(stop.status.success());
    wait_until(
        "fresh turn success checkpoint",
        Duration::from_secs(5),
        || {
            read_session_state(root.path(), session_id)[session_id]["posted_turn_ids"]
                .as_array()
                .is_some_and(|ids| ids.iter().any(|id| id == turn_id))
        },
    );
    wait_for_complete_topology(&sink, root.path(), session_id);
    assert_complete_single_root_topology(&sink.snapshot());
}

#[test]
fn production_parent_worker_ignores_successful_realtime_residue_for_later_toolless_turn() {
    let _guard = serialize_hook_performance_test();
    let root = tempfile::tempdir().unwrap();
    let sink = OtlpSink::start();
    let session_id = "historical-residue-session";
    let historical_turn_id = "turn-historical";
    let clean_turn_id = "turn-clean-toolless";
    let transcript = root.path().join("sessions/historical-residue.jsonl");
    std::fs::create_dir_all(transcript.parent().unwrap()).unwrap();
    std::fs::write(
        &transcript,
        format!(
            r#"{{"timestamp":"2026-08-20T03:10:00Z","type":"session_meta","payload":{{"id":"{session_id}","thread_source":"user"}}}}
{{"timestamp":"2026-08-20T03:10:01Z","type":"event_msg","payload":{{"type":"task_started","turn_id":"{historical_turn_id}"}}}}
{{"timestamp":"2026-08-20T03:10:01.100Z","type":"event_msg","payload":{{"type":"user_message","message":"historical already exported"}}}}
{{"timestamp":"2026-08-20T03:10:01.200Z","type":"response_item","payload":{{"type":"function_call","name":"exec_command","arguments":"{{\"cmd\":\"true\"}}","call_id":"call-historical"}}}}
{{"timestamp":"2026-08-20T03:10:01.300Z","type":"response_item","payload":{{"type":"function_call_output","call_id":"call-historical","output":"ok"}}}}
{{"timestamp":"2026-08-20T03:10:01.400Z","type":"event_msg","payload":{{"type":"agent_message","phase":"final_answer","message":"historical done"}}}}
{{"timestamp":"2026-08-20T03:10:01.500Z","type":"event_msg","payload":{{"type":"task_complete","turn_id":"{historical_turn_id}","last_agent_message":"historical done"}}}}
{{"timestamp":"2026-08-20T03:11:00Z","type":"event_msg","payload":{{"type":"task_started","turn_id":"{clean_turn_id}"}}}}
{{"timestamp":"2026-08-20T03:11:00.100Z","type":"event_msg","payload":{{"type":"user_message","message":"summarize status"}}}}
{{"timestamp":"2026-08-20T03:11:00.200Z","type":"event_msg","payload":{{"type":"agent_message","phase":"final_answer","message":"status summarized"}}}}
{{"timestamp":"2026-08-20T03:11:00.300Z","type":"event_msg","payload":{{"type":"task_complete","turn_id":"{clean_turn_id}","last_agent_message":"status summarized"}}}}
"#
        ),
    )
    .unwrap();
    seed_successful_realtime_residue(root.path(), session_id, historical_turn_id);

    let stop = run_hook_with_base_url(
        root.path(),
        &json!({
            "hook_event_name": "Stop",
            "session_id": session_id,
            "transcript_path": transcript
        }),
        &sink.base_url,
    );
    assert!(
        stop.status.success(),
        "hook failed: status={:?} stdout={} stderr={} logs={}",
        stop.status.code(),
        String::from_utf8_lossy(&stop.stdout),
        String::from_utf8_lossy(&stop.stderr),
        walk_text(&root.path().join("logs"))
    );
    wait_until(
        "tool-less clean turn final commit",
        Duration::from_secs(5),
        || {
            let state = read_session_state(root.path(), session_id);
            state[session_id]["posted_turn_ids"]
                .as_array()
                .is_some_and(|ids| ids.iter().any(|id| id == clean_turn_id))
                && state[session_id].get("pending_export_reason").is_none()
        },
    );
    wait_for_chat_export(&sink, root.path(), session_id);

    let state = read_session_state(root.path(), session_id);
    assert_eq!(
        state[session_id]["posted_turn_ids"],
        json!([historical_turn_id, clean_turn_id])
    );
    assert!(state[session_id].get("pending_export_reason").is_none());
    assert!(state[session_id]
        .get("quarantined_turns")
        .and_then(Value::as_array)
        .is_none_or(Vec::is_empty));
    let spans = otlp_spans(&sink.snapshot());
    assert_eq!(
        spans
            .iter()
            .filter(|span| span_attr(span, "gen_ai.span.kind") == Some("CHAT"))
            .count(),
        1,
        "tool-less clean turn exported more than once: {spans:#?}"
    );
    assert!(
        !walk_text(&root.path().join("logs")).contains("realtime_evidence_without_unique_match"),
        "historical realtime residue was still classified as current-turn ambiguity"
    );

    let session_end = run_hook_with_base_url(
        root.path(),
        &json!({
            "hook_event_name": "SessionEnd",
            "session_id": session_id,
            "transcript_path": transcript
        }),
        &sink.base_url,
    );
    assert!(session_end.status.success());
    wait_until(
        "duplicate SessionEnd worker completion",
        Duration::from_secs(5),
        || phase_event_count(&root.path().join("logs"), "worker", "SessionEnd") > 0,
    );

    let ordinary = run_hook_with_base_url(
        root.path(),
        &json!({
            "hook_event_name": "UserPromptSubmit",
            "session_id": session_id,
            "turn_id": "turn-after-finalizer",
            "prompt": "next turn",
            "cwd": "/repo"
        }),
        &sink.base_url,
    );
    assert!(ordinary.status.success());
    wait_until(
        "ordinary hook worker completion",
        Duration::from_secs(5),
        || phase_event_count(&root.path().join("logs"), "worker", "UserPromptSubmit") > 0,
    );

    let final_state = read_session_state(root.path(), session_id);
    assert!(final_state[session_id]
        .get("pending_export_reason")
        .is_none());
    assert!(final_state[session_id]
        .get("legacy_finalizer_recovery")
        .is_none());
    let final_spans = otlp_spans(&sink.snapshot());
    assert_eq!(
        final_spans
            .iter()
            .filter(|span| span_attr(span, "gen_ai.span.kind") == Some("CHAT"))
            .count(),
        1,
        "SessionEnd or ordinary hook replayed the completed turn: {final_spans:#?}"
    );
    assert!(
        walk_text(&root.path().join("logs")).contains("LegacyFallback"),
        "expected positive LegacyFallback evidence in production worker logs"
    );
}

#[test]
fn production_parent_worker_quarantines_poison_and_keeps_later_turn_live() {
    let _guard = serialize_hook_performance_test();
    let root = tempfile::tempdir().unwrap();
    let sink = OtlpSink::start();
    let session_id = "poison-e2e-session";
    let bad_turn_id = "turn-bad";
    let clean_turn_id = "turn-clean";
    let transcript = root.path().join("sessions/poison.jsonl");
    std::fs::create_dir_all(transcript.parent().unwrap()).unwrap();
    let bad_transcript = format!(
        r#"{{"timestamp":"2026-08-17T00:00:00Z","type":"session_meta","payload":{{"id":"{session_id}","thread_source":"user"}}}}
{{"timestamp":"2026-08-17T00:00:01Z","type":"event_msg","payload":{{"type":"task_started","turn_id":"{bad_turn_id}"}}}}
{{"timestamp":"2026-08-17T00:00:01.100Z","type":"event_msg","payload":{{"type":"user_message","message":"bad turn"}}}}
{{"timestamp":"2026-08-17T00:00:01.200Z","type":"event_msg","payload":{{"type":"agent_message","phase":"final_answer","message":"bad done"}}}}
{{"timestamp":"2026-08-17T00:00:01.300Z","type":"event_msg","payload":{{"type":"task_complete","turn_id":"{bad_turn_id}","last_agent_message":"bad done"}}}}
"#
    );
    std::fs::write(&transcript, &bad_transcript).unwrap();
    seed_multiple_published_roots(root.path(), session_id, bad_turn_id);

    let first_stop = run_hook_with_base_url(
        root.path(),
        &json!({
            "hook_event_name": "Stop",
            "session_id": session_id,
            "transcript_path": transcript
        }),
        &sink.base_url,
    );
    assert!(first_stop.status.success());
    wait_until(
        "terminal quarantine checkpoint",
        Duration::from_secs(5),
        || {
            let state = read_session_state(root.path(), session_id);
            state[session_id]["quarantined_turns"]
                .as_array()
                .is_some_and(|items| items.len() == 1)
                && state[session_id]["export_accounting"]["terminal_turn_ids"]
                    .as_array()
                    .is_some_and(|ids| ids.iter().any(|id| id == bad_turn_id))
        },
    );
    let runtime_dir = session_runtime_dir(root.path(), session_id);
    let stop_intent = runtime_dir.join("state_stop_intent.json");
    wait_until(
        "initial Stop intent cleanup",
        Duration::from_secs(5),
        || !stop_intent.exists(),
    );
    wait_until(
        "initial Stop worker completion",
        Duration::from_secs(5),
        || phase_event_count(&root.path().join("logs"), "worker", "Stop") == 1,
    );
    let quarantined = read_session_state(root.path(), session_id);
    let terminal_high_water = quarantined[session_id]["export_accounting"]
        ["terminal_high_water_offset"]
        .as_i64()
        .unwrap();
    assert_eq!(quarantined[session_id]["posted_turn_ids"], json!([]));

    std::fs::write(
        &stop_intent,
        json!({
            "schema_version": 1,
            "hook_event_name": "Stop",
            "session_id": session_id,
            "transcript_path": transcript,
            "status": "pending"
        })
        .to_string(),
    )
    .unwrap();
    let prompt = run_hook_with_base_url(
        root.path(),
        &json!({
            "hook_event_name": "UserPromptSubmit",
            "session_id": session_id,
            "turn_id": clean_turn_id,
            "prompt": "clean turn",
            "cwd": "/repo"
        }),
        &sink.base_url,
    );
    assert!(prompt.status.success());
    wait_until(
        "clean prompt worker completion",
        Duration::from_secs(5),
        || phase_event_count(&root.path().join("logs"), "worker", "UserPromptSubmit") > 0,
    );
    assert!(
        stop_intent.exists(),
        "ordinary hook consumed the pending Stop intent"
    );
    assert_eq!(
        read_session_state(root.path(), session_id)[session_id]["quarantined_turns"]
            .as_array()
            .unwrap()
            .len(),
        1
    );

    let clean_transcript = format!(
        r#"{{"timestamp":"2026-08-17T00:00:02Z","type":"event_msg","payload":{{"type":"task_started","turn_id":"{clean_turn_id}"}}}}
{{"timestamp":"2026-08-17T00:00:02.100Z","type":"event_msg","payload":{{"type":"user_message","message":"clean turn"}}}}
{{"timestamp":"2026-08-17T00:00:02.200Z","type":"turn_context","payload":{{"turn_id":"{clean_turn_id}","cwd":"/repo","model":"gpt-5.5"}}}}
{{"timestamp":"2026-08-17T00:00:02.300Z","type":"event_msg","payload":{{"type":"agent_message","phase":"commentary","message":"running clean"}}}}
{{"timestamp":"2026-08-17T00:00:02.400Z","type":"response_item","payload":{{"type":"function_call","name":"exec_command","arguments":"{{\"cmd\":\"true\"}}","call_id":"call-clean"}}}}
{{"timestamp":"2026-08-17T00:00:02.500Z","type":"response_item","payload":{{"type":"function_call_output","call_id":"call-clean","output":"ok"}}}}
{{"timestamp":"2026-08-17T00:00:02.600Z","type":"event_msg","payload":{{"type":"agent_message","phase":"final_answer","message":"clean done"}}}}
{{"timestamp":"2026-08-17T00:00:02.700Z","type":"event_msg","payload":{{"type":"task_complete","turn_id":"{clean_turn_id}","last_agent_message":"clean done"}}}}
"#
    );
    std::fs::OpenOptions::new()
        .append(true)
        .open(&transcript)
        .unwrap()
        .write_all(clean_transcript.as_bytes())
        .unwrap();
    for payload in [
        json!({
            "hook_event_name": "PreToolUse",
            "session_id": session_id,
            "turn_id": clean_turn_id,
            "transcript_path": transcript,
            "tool_name": "Bash",
            "tool_use_id": "call-clean",
            "tool_input": {"command": "true"}
        }),
        json!({
            "hook_event_name": "PostToolUse",
            "session_id": session_id,
            "turn_id": clean_turn_id,
            "transcript_path": transcript,
            "tool_name": "Bash",
            "tool_use_id": "call-clean",
            "tool_input": {"command": "true"},
            "tool_response": {"output": "ok"}
        }),
    ] {
        let event = payload["hook_event_name"].as_str().unwrap().to_string();
        let output = run_hook_with_base_url(root.path(), &payload, &sink.base_url);
        assert!(output.status.success());
        wait_until(
            &format!("clean {event} worker completion"),
            Duration::from_secs(5),
            || phase_event_count(&root.path().join("logs"), "worker", &event) > 0,
        );
    }
    wait_until(
        "completed clean tool record",
        Duration::from_secs(5),
        || {
            read_session_state(root.path(), session_id)[session_id]["records"]["call-clean"]
                ["completed_at_unix_nano"]
                .is_string()
        },
    );
    let clean_stop = run_hook_with_base_url(
        root.path(),
        &json!({
            "hook_event_name": "Stop",
            "session_id": session_id,
            "transcript_path": transcript
        }),
        &sink.base_url,
    );
    assert!(clean_stop.status.success());
    wait_until(
        "clean turn success checkpoint",
        Duration::from_secs(5),
        || {
            read_session_state(root.path(), session_id)[session_id]["posted_turn_ids"]
                .as_array()
                .is_some_and(|ids| ids.iter().any(|id| id == clean_turn_id))
        },
    );
    wait_for_complete_topology(&sink, root.path(), session_id);
    wait_until(
        "both Stop workers to release session ownership",
        Duration::from_secs(5),
        || phase_event_count(&root.path().join("logs"), "worker", "Stop") >= 2,
    );
    let before_duplicate = sink.snapshot();
    assert_complete_single_root_topology(&before_duplicate);

    let duplicate = run_hook_with_base_url(
        root.path(),
        &json!({
            "hook_event_name": "SessionEnd",
            "session_id": session_id,
            "transcript_path": transcript
        }),
        &sink.base_url,
    );
    assert!(duplicate.status.success());
    wait_until(
        "duplicate SessionEnd completion",
        Duration::from_secs(5),
        || {
            !stop_intent.exists()
                && phase_event_count(&root.path().join("logs"), "worker", "SessionEnd") > 0
        },
    );
    let after_duplicate = sink.snapshot();
    assert_eq!(after_duplicate.len(), before_duplicate.len());
    assert_eq!(
        otlp_spans(&after_duplicate).len(),
        otlp_spans(&before_duplicate).len()
    );
    let final_state = read_session_state(root.path(), session_id);
    assert_eq!(
        final_state[session_id]["quarantined_turns"]
            .as_array()
            .unwrap()
            .len(),
        1
    );
    assert_eq!(
        final_state[session_id]["export_accounting"]["terminal_high_water_offset"],
        terminal_high_water
    );
    assert_eq!(
        final_state[session_id]["posted_turn_ids"],
        json!([clean_turn_id])
    );
    assert!(final_state[session_id]
        .get("pending_export_reason")
        .is_none());
}

fn parent_total_ms(root: &Path, event: &str) -> Option<u64> {
    parent_phase_records(root, event)
        .iter()
        .find_map(|record| record["elapsed_ms"]["total"].as_u64())
}

fn phase_event_count(root: &Path, component: &str, event: &str) -> usize {
    walk_text(root)
        .lines()
        .filter_map(|line| serde_json::from_str::<Value>(line).ok())
        .filter(|record| {
            record["kind"] == "cdxhooks.phase_timing"
                && record["component"] == component
                && record["event"] == event
        })
        .count()
}

fn worker_process_ids(root: &Path) -> std::collections::BTreeSet<u32> {
    const MARKER: &str = "session worker active: worker_pid=";
    walk_text(root)
        .lines()
        .filter_map(|line| {
            let (_, tail) = line.split_once(MARKER)?;
            tail.chars()
                .take_while(char::is_ascii_digit)
                .collect::<String>()
                .parse::<u32>()
                .ok()
        })
        .collect()
}

fn parent_phase_records(root: &Path, event: &str) -> Vec<Value> {
    walk_text(root)
        .lines()
        .filter_map(|line| {
            let record: Value = serde_json::from_str(line).ok()?;
            (record["kind"] == "cdxhooks.phase_timing"
                && record["component"] == "parent"
                && record["event"] == event)
                .then_some(record)
        })
        .collect()
}

fn walk_text(root: &Path) -> String {
    let mut output = String::new();
    if let Ok(entries) = std::fs::read_dir(root) {
        for entry in entries.flatten() {
            let path = entry.path();
            if path.is_dir() {
                output.push_str(&walk_text(&path));
            } else if let Ok(contents) = std::fs::read_to_string(path) {
                output.push_str(&contents);
            }
        }
    }
    output
}
