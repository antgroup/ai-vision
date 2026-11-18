use std::io::{Read, Write};
use std::net::TcpListener;
use std::process::{Command, Stdio};
use std::sync::{Mutex, OnceLock};
use std::thread;
use std::time::{Duration, Instant};

use serde_json::{json, Value};
#[cfg(unix)]
use sha2::{Digest, Sha256};

fn performance_test_lock() -> &'static Mutex<()> {
    static LOCK: OnceLock<Mutex<()>> = OnceLock::new();
    LOCK.get_or_init(|| Mutex::new(()))
}

fn run_cchooks_with_payload(home: &std::path::Path, payload: Value) -> std::process::Output {
    run_cchooks_with_env(home, payload, [])
}

fn run_cchooks_with_env<const N: usize>(
    home: &std::path::Path,
    payload: Value,
    extra_env: [(&str, String); N],
) -> std::process::Output {
    run_cchooks_with_wire_payload(home, payload.to_string().as_bytes(), extra_env)
}

fn run_cchooks_with_wire_payload<const N: usize>(
    home: &std::path::Path,
    payload: &[u8],
    extra_env: [(&str, String); N],
) -> std::process::Output {
    let mut command = Command::new(env!("CARGO_BIN_EXE_cchooks"));
    command
        .env_clear()
        .env("HOME", home)
        .env("USER", "async-contract-test")
        .env("CCHOOKS_ENABLE_TELEMETRY", "true")
        .env("CCHOOKS_TOOL_CONTEXT", "true")
        .env("CCHOOKS_REALTIME_EXPORT", "false")
        .env("CCHOOKS_LOG_DIR", home.join("logs"))
        .env("CCHOOKS_STATE_DIR", home.join("state"));

    for (key, value) in extra_env {
        command.env(key, value);
    }

    let mut child = command
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("spawn cchooks");

    {
        let stdin = child.stdin.as_mut().expect("stdin pipe");
        stdin.write_all(payload).expect("write hook payload");
    }

    child.wait_with_output().expect("wait for cchooks")
}

fn delayed_http_endpoint(delay: Duration) -> (String, std::sync::mpsc::Receiver<()>) {
    delayed_first_http_endpoint(delay, 1)
}

fn delayed_first_http_endpoint(
    delay: Duration,
    request_count: usize,
) -> (String, std::sync::mpsc::Receiver<()>) {
    let listener = TcpListener::bind("127.0.0.1:0").expect("bind fake collector");
    let addr = listener.local_addr().expect("fake collector addr");
    let (accepted_tx, accepted_rx) = std::sync::mpsc::channel();
    thread::spawn(move || {
        for index in 0..request_count {
            let Ok((mut stream, _)) = listener.accept() else {
                return;
            };
            if index == 0 {
                let _ = accepted_tx.send(());
            }
            let mut buf = [0_u8; 4096];
            let _ = stream.read(&mut buf);
            if index == 0 {
                thread::sleep(delay);
            }
            let response = b"HTTP/1.1 200 OK\r\ncontent-length: 0\r\nconnection: close\r\n\r\n";
            let _ = stream.write_all(response);
        }
    });
    (
        format!("http://{addr}/api/public/otel/v1/traces"),
        accepted_rx,
    )
}

fn capturing_http_endpoint() -> (String, std::sync::mpsc::Receiver<Value>) {
    let listener = TcpListener::bind("127.0.0.1:0").expect("bind fake collector");
    let addr = listener.local_addr().expect("fake collector addr");
    let (body_tx, body_rx) = std::sync::mpsc::channel();
    thread::spawn(move || {
        let (mut stream, _) = listener.accept().expect("accept trace export");
        stream
            .set_read_timeout(Some(Duration::from_secs(5)))
            .expect("set collector read timeout");
        let mut request = Vec::new();
        let mut chunk = [0_u8; 8192];
        let header_end = loop {
            let read = stream.read(&mut chunk).expect("read request headers");
            assert!(read > 0, "collector request closed before headers");
            request.extend_from_slice(&chunk[..read]);
            if let Some(index) = request.windows(4).position(|window| window == b"\r\n\r\n") {
                break index + 4;
            }
        };
        let headers = String::from_utf8_lossy(&request[..header_end]);
        let content_length = headers
            .lines()
            .find_map(|line| {
                let (name, value) = line.split_once(':')?;
                name.eq_ignore_ascii_case("content-length")
                    .then(|| value.trim().parse::<usize>().ok())
                    .flatten()
            })
            .expect("trace export Content-Length");
        while request.len() - header_end < content_length {
            let read = stream.read(&mut chunk).expect("read request body");
            assert!(read > 0, "collector request closed before body");
            request.extend_from_slice(&chunk[..read]);
        }
        let body: Value =
            serde_json::from_slice(&request[header_end..header_end.saturating_add(content_length)])
                .expect("OTLP JSON request body");
        body_tx.send(body).expect("publish trace body");
        stream
            .write_all(b"HTTP/1.1 200 OK\r\ncontent-length: 0\r\nconnection: close\r\n\r\n")
            .expect("write collector response");
    });
    (format!("http://{addr}/api/public/otel/v1/traces"), body_rx)
}

fn exported_spans(body: &Value) -> Vec<&Value> {
    body["resourceSpans"]
        .as_array()
        .into_iter()
        .flatten()
        .flat_map(|resource| resource["scopeSpans"].as_array().into_iter().flatten())
        .flat_map(|scope| scope["spans"].as_array().into_iter().flatten())
        .collect()
}

fn parent_total_from_log(path: &std::path::Path, event: &str) -> Duration {
    const MARKER: &str = "\"component\":\"parent\",\"phase\":\"total\",\"elapsed_ms\":";
    let contents = std::fs::read_to_string(path).expect("read per-session hook log");
    let event_tag = format!(" [{event}] ");
    let line = contents
        .lines()
        .rev()
        .find(|line| line.contains(&event_tag) && line.contains(MARKER))
        .expect("parent total timing log");
    let millis = line
        .split_once(MARKER)
        .expect("parent total marker")
        .1
        .chars()
        .take_while(char::is_ascii_digit)
        .collect::<String>()
        .parse::<u64>()
        .expect("parent total milliseconds");
    Duration::from_millis(millis)
}

fn worker_status(home: &std::path::Path, session_id: &str) -> Value {
    let output = Command::new(env!("CARGO_BIN_EXE_cchooks"))
        .env_clear()
        .env("HOME", home)
        .env("CCHOOKS_STATE_DIR", home.join("state"))
        .args(["worker", "status", "--session-id", session_id])
        .output()
        .expect("run worker status");
    assert!(
        output.status.success(),
        "worker status failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    serde_json::from_slice(&output.stdout).expect("parse worker status")
}

fn wait_for_worker_idle(home: &std::path::Path, session_id: &str) {
    let deadline = Instant::now() + Duration::from_secs(5);
    loop {
        let status = worker_status(home, session_id);
        if status["status"]["pending_queue_items"] == 0
            && status["status"]["workers_active"] == 0
            && status["status"]["worker_starting"] == 0
        {
            return;
        }
        assert!(
            Instant::now() < deadline,
            "timed out waiting for session worker to become idle: {status}"
        );
        thread::sleep(Duration::from_millis(20));
    }
}

fn wait_for_stop_state(home: &std::path::Path) -> (std::path::PathBuf, Value) {
    let sessions = home.join("state");
    let deadline = Instant::now() + Duration::from_secs(3);
    loop {
        if let Ok(entries) = std::fs::read_dir(&sessions) {
            for entry in entries.flatten() {
                let path = entry.path().join("state_stop.json");
                if let Ok(contents) = std::fs::read_to_string(&path) {
                    if let Ok(state) = serde_json::from_str::<Value>(&contents) {
                        if state
                            .get("pending_final_export")
                            .is_some_and(Value::is_object)
                        {
                            return (path, state);
                        }
                    }
                }
            }
        }
        assert!(
            Instant::now() < deadline,
            "timed out waiting for Stop state"
        );
        thread::sleep(Duration::from_millis(10));
    }
}

fn wait_for_stop_state_cleared(home: &std::path::Path) {
    let sessions = home.join("state");
    let deadline = Instant::now() + Duration::from_secs(5);
    loop {
        let mut pending = Vec::new();
        if let Ok(entries) = std::fs::read_dir(&sessions) {
            for entry in entries.flatten() {
                let path = entry.path().join("state_stop.json");
                if let Ok(contents) = std::fs::read_to_string(&path) {
                    if let Ok(state) = serde_json::from_str::<Value>(&contents) {
                        if state
                            .get("pending_final_export")
                            .is_some_and(Value::is_object)
                        {
                            pending.push(path);
                        }
                    }
                }
            }
        }
        if pending.is_empty() {
            return;
        }
        assert!(
            Instant::now() < deadline,
            "timed out waiting for Stop state to clear: {pending:?}"
        );
        thread::sleep(Duration::from_millis(20));
    }
}

fn wait_for_replay_with_root_turn(replay_dir: &std::path::Path) -> Value {
    let deadline = Instant::now() + Duration::from_secs(5);
    loop {
        if let Ok(entries) = std::fs::read_dir(replay_dir) {
            for entry in entries.flatten() {
                let path = entry.path();
                if path
                    .extension()
                    .is_some_and(|extension| extension == "jsonl")
                {
                    let contents = std::fs::read_to_string(&path).expect("read replay JSONL");
                    for line in contents.lines().filter(|line| !line.trim().is_empty()) {
                        let body: Value = serde_json::from_str(line).expect("parse replay JSONL");
                        let has_root_turn = body["resourceSpans"]
                            .as_array()
                            .into_iter()
                            .flatten()
                            .flat_map(|resource| {
                                resource["scopeSpans"].as_array().into_iter().flatten()
                            })
                            .flat_map(|scope| scope["spans"].as_array().into_iter().flatten())
                            .any(|span| {
                                span["name"] == "Turn 1"
                                    && span.get("parentSpanId").is_none_or(|parent| {
                                        parent.as_str().unwrap_or("").is_empty()
                                    })
                            });
                        if has_root_turn {
                            return body;
                        }
                    }
                }
            }
        }
        assert!(
            Instant::now() < deadline,
            "timed out waiting for replay with root Turn span under {}",
            replay_dir.display()
        );
        thread::sleep(Duration::from_millis(20));
    }
}

fn replay_span_name_counts(
    replay_dir: &std::path::Path,
) -> std::collections::BTreeMap<String, usize> {
    let mut counts = std::collections::BTreeMap::new();
    for entry in std::fs::read_dir(replay_dir)
        .expect("read replay directory")
        .flatten()
    {
        let path = entry.path();
        if path
            .extension()
            .is_none_or(|extension| extension != "jsonl")
        {
            continue;
        }
        let contents = std::fs::read_to_string(path).expect("read replay JSONL");
        for line in contents.lines().filter(|line| !line.trim().is_empty()) {
            let body: Value = serde_json::from_str(line).expect("parse replay JSONL");
            for name in body["resourceSpans"]
                .as_array()
                .into_iter()
                .flatten()
                .flat_map(|resource| resource["scopeSpans"].as_array().into_iter().flatten())
                .flat_map(|scope| scope["spans"].as_array().into_iter().flatten())
                .filter_map(|span| span["name"].as_str())
            {
                *counts.entry(name.to_string()).or_insert(0) += 1;
            }
        }
    }
    counts
}

fn worker_process_ids(root: &std::path::Path) -> std::collections::BTreeSet<u32> {
    let mut text = String::new();
    collect_text(root, &mut text);
    text.lines()
        .filter_map(|line| {
            let (_, tail) = line.split_once("worker_pid=")?;
            tail.chars()
                .take_while(char::is_ascii_digit)
                .collect::<String>()
                .parse::<u32>()
                .ok()
        })
        .collect()
}

#[cfg(unix)]
fn session_runtime_dir(root: &std::path::Path, session_id: &str) -> std::path::PathBuf {
    let digest = Sha256::digest(session_id.as_bytes());
    let suffix = digest[..8]
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect::<String>();
    root.join("state").join(format!("{session_id}-{suffix}"))
}

fn collect_text(root: &std::path::Path, output: &mut String) {
    if let Ok(entries) = std::fs::read_dir(root) {
        for entry in entries.flatten() {
            let path = entry.path();
            if path.is_dir() {
                collect_text(&path, output);
            } else if let Ok(contents) = std::fs::read_to_string(path) {
                output.push_str(&contents);
            }
        }
    }
}

#[test]
fn structured_false_stop_can_be_finalized_by_session_end_without_active_stop() {
    let _guard = performance_test_lock()
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let dir = tempfile::tempdir().expect("tempdir");
    let session_id = "session-structured-session-end-finalizer";
    let transcript_path = dir.path().join("session.jsonl");
    let replay_dir = dir.path().join("replays");
    let (endpoint, exported) = capturing_http_endpoint();
    std::fs::copy(
        std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/simple_conversation.jsonl"),
        &transcript_path,
    )
    .expect("copy transcript fixture");

    let first_stop = run_cchooks_with_env(
        dir.path(),
        json!({
            "hook_event_name": "Stop",
            "session_id": session_id,
            "transcript_path": transcript_path,
            "stop_hook_active": false
        }),
        [
            ("CCHOOKS_STRUCTURED_OUTPUT", "true".to_string()),
            ("CCHOOKS_DEBUG", "true".to_string()),
            ("CCHOOKS_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", endpoint.clone()),
            (
                "CCHOOKS_REPLAY_DIR",
                replay_dir.to_string_lossy().into_owned(),
            ),
        ],
    );
    assert!(
        first_stop.status.success(),
        "provisional Stop must succeed: stderr={}",
        String::from_utf8_lossy(&first_stop.stderr)
    );
    assert!(
        first_stop.stdout.is_empty(),
        "provisional Stop must not emit hook protocol output"
    );
    let (_state_path, state) = wait_for_stop_state(dir.path());
    assert_eq!(
        state["pending_final_export"]["phase"],
        "awaiting_continuation"
    );

    let session_end = run_cchooks_with_env(
        dir.path(),
        json!({
            "hook_event_name": "SessionEnd",
            "session_id": session_id,
            "transcript_path": transcript_path
        }),
        [
            ("CCHOOKS_STRUCTURED_OUTPUT", "false".to_string()),
            ("CCHOOKS_DEBUG", "true".to_string()),
            ("CCHOOKS_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", endpoint),
            (
                "CCHOOKS_REPLAY_DIR",
                replay_dir.to_string_lossy().into_owned(),
            ),
        ],
    );
    assert!(
        session_end.status.success(),
        "SessionEnd fallback finalizer must succeed: stderr={}",
        String::from_utf8_lossy(&session_end.stderr)
    );
    assert!(
        session_end.stdout.is_empty(),
        "SessionEnd fallback finalizer must not emit hook protocol output"
    );

    wait_for_stop_state_cleared(dir.path());
    exported
        .recv_timeout(Duration::from_secs(5))
        .expect("SessionEnd exports to the explicit local collector");
    let replay = wait_for_replay_with_root_turn(&replay_dir);
    let span_names = replay["resourceSpans"]
        .as_array()
        .into_iter()
        .flatten()
        .flat_map(|resource| resource["scopeSpans"].as_array().into_iter().flatten())
        .flat_map(|scope| scope["spans"].as_array().into_iter().flatten())
        .filter_map(|span| span["name"].as_str())
        .collect::<std::collections::BTreeSet<_>>();
    assert!(
        span_names.contains("Turn 1") && span_names.contains("chat claude-sonnet-4-20250514"),
        "SessionEnd fallback must export a complete turn, got spans {span_names:?}"
    );
}

#[test]
fn structured_first_stop_persists_awaiting_without_worker_side_effect_state() {
    let _guard = performance_test_lock()
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let dir = tempfile::tempdir().expect("tempdir");
    let output = run_cchooks_with_env(
        dir.path(),
        json!({
            "hook_event_name": "Stop",
            "session_id": "session-structured-first-stop",
            "transcript_path": dir.path().join("session.jsonl"),
            "stop_hook_active": false
        }),
        [("CCHOOKS_STRUCTURED_OUTPUT", "true".to_string())],
    );
    assert!(output.status.success());
    assert!(
        output.stdout.is_empty(),
        "Stop must not emit hook protocol output"
    );

    let (state_path, state) = wait_for_stop_state(dir.path());
    let intent = &state["pending_final_export"];
    assert_eq!(intent["phase"], "awaiting_continuation");
    assert_eq!(intent["structured_output"], true);
    assert_eq!(intent["revision"], 1);

    let session_dir = state_path.parent().expect("session state directory");
    for forbidden in [
        "state_turn.json",
        "state_generation.json",
        "state_subagents.json",
        "state_digest.json",
        "state_tools.json",
    ] {
        assert!(
            !session_dir.join(forbidden).exists(),
            "provisional Stop must not create {forbidden}"
        );
    }
    assert!(
        !dir.path().join("state").join("dynamic_sessions").exists(),
        "provisional Stop must not create dynamic finalization state"
    );
}

#[test]
fn structured_final_stop_advances_the_same_intent_revision_without_protocol_output() {
    let _guard = performance_test_lock()
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let dir = tempfile::tempdir().expect("tempdir");
    let session_id = "session-structured-final-stop";
    let transcript_path = dir.path().join("session.jsonl");
    std::fs::copy(
        std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/simple_conversation.jsonl"),
        &transcript_path,
    )
    .expect("copy transcript fixture");
    let first = run_cchooks_with_env(
        dir.path(),
        json!({
            "hook_event_name": "Stop",
            "session_id": session_id,
            "transcript_path": transcript_path,
            "stop_hook_active": false
        }),
        [("CCHOOKS_STRUCTURED_OUTPUT", "1".to_string())],
    );
    assert!(first.status.success());
    let (state_path, provisional) = wait_for_stop_state(dir.path());
    let provisional_id = provisional["pending_final_export"]["intent_id"]
        .as_str()
        .expect("provisional intent id")
        .to_string();

    let final_stop = run_cchooks_with_env(
        dir.path(),
        json!({
            "hook_event_name": "Stop",
            "session_id": session_id,
            "transcript_path": transcript_path,
            "stop_hook_active": true
        }),
        [
            ("CCHOOKS_STRUCTURED_OUTPUT", "true".to_string()),
            (
                "CCHOOKS_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
                "http://127.0.0.1:9/v1/traces".to_string(),
            ),
        ],
    );
    assert!(final_stop.status.success());
    assert!(
        final_stop.stdout.is_empty(),
        "authorized final Stop must not emit hook protocol output"
    );

    let deadline = Instant::now() + Duration::from_secs(3);
    loop {
        let final_state: Value =
            serde_json::from_slice(&std::fs::read(&state_path).expect("read final Stop state"))
                .expect("parse final Stop state");
        let intent = &final_state["pending_final_export"];
        if intent["revision"] == 2 {
            assert_eq!(intent["intent_id"], provisional_id);
            assert_eq!(intent["phase"], "finalizing");
            assert_eq!(intent["structured_output"], true);
            break;
        }
        assert!(
            Instant::now() < deadline,
            "timed out waiting for final Stop revision"
        );
        thread::sleep(Duration::from_millis(10));
    }
}

#[cfg(unix)]
#[test]
fn structured_session_end_waits_for_active_stop_and_drains_the_session_queue() {
    let _guard = performance_test_lock()
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let dir = tempfile::tempdir().expect("tempdir");
    let session_id = "session-structured-session-end-barrier";
    let transcript_path = dir.path().join("session.jsonl");
    let replay_dir = dir.path().join("replays");
    std::fs::copy(
        std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/multi_turn_with_tools.jsonl"),
        &transcript_path,
    )
    .expect("copy transcript fixture");

    let provisional = run_cchooks_with_env(
        dir.path(),
        json!({
            "hook_event_name": "Stop",
            "session_id": session_id,
            "transcript_path": transcript_path,
            "stop_hook_active": false
        }),
        [("CCHOOKS_STRUCTURED_OUTPUT", "true".to_string())],
    );
    assert!(provisional.status.success());
    let (_state_path, state) = wait_for_stop_state(dir.path());
    assert_eq!(
        state["pending_final_export"]["phase"],
        "awaiting_continuation"
    );
    wait_for_worker_idle(dir.path(), session_id);

    let (endpoint, accepted) = delayed_first_http_endpoint(Duration::ZERO, 2);
    let active = run_cchooks_with_env(
        dir.path(),
        json!({
            "hook_event_name": "Stop",
            "session_id": session_id,
            "transcript_path": transcript_path,
            "stop_hook_active": true
        }),
        [
            ("CCHOOKS_STRUCTURED_OUTPUT", "true".to_string()),
            ("CCHOOKS_DEBUG", "true".to_string()),
            ("CCHOOKS_TEST_WORKER_START_DELAY_MS", "2200".to_string()),
            (
                "CCHOOKS_REPLAY_DIR",
                replay_dir.to_string_lossy().into_owned(),
            ),
            (
                "CCHOOKS_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
                endpoint.clone(),
            ),
        ],
    );
    assert!(active.status.success());

    let barrier_started = Instant::now();
    let session_end = run_cchooks_with_env(
        dir.path(),
        json!({
            "hook_event_name": "SessionEnd",
            "session_id": session_id,
            "transcript_path": transcript_path
        }),
        [
            ("CCHOOKS_STRUCTURED_OUTPUT", "true".to_string()),
            ("CCHOOKS_DEBUG", "true".to_string()),
            ("CCHOOKS_SESSION_END_BUDGET_MS", "8000".to_string()),
            (
                "CCHOOKS_REPLAY_DIR",
                replay_dir.to_string_lossy().into_owned(),
            ),
            ("CCHOOKS_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", endpoint),
        ],
    );
    let barrier_elapsed = barrier_started.elapsed();
    assert!(
        session_end.status.success(),
        "SessionEnd barrier must fail open: stderr={}",
        String::from_utf8_lossy(&session_end.stderr)
    );
    assert!(
        barrier_elapsed >= Duration::from_millis(1_800),
        "SessionEnd returned before the active Stop completed: {barrier_elapsed:?}"
    );
    accepted
        .recv_timeout(Duration::from_secs(1))
        .expect("active Stop worker must export before SessionEnd returns");

    let status = worker_status(dir.path(), session_id);
    assert_eq!(status["status"]["pending_queue_items"], 0);
    assert_eq!(status["status"]["workers_active"], 0);
    assert_eq!(status["status"]["worker_starting"], 0);
    assert_eq!(status["status"]["pending_stops"], 0);

    let session_dir = session_runtime_dir(dir.path(), session_id);
    let stop_state: Value = serde_json::from_slice(
        &std::fs::read(session_dir.join("state_stop.json")).expect("read Stop state"),
    )
    .expect("parse Stop state");
    assert!(stop_state.get("pending_final_export").is_none());
    assert_eq!(
        stop_state["offset"].as_u64(),
        Some(std::fs::metadata(&transcript_path).unwrap().len()),
        "final checkpoint cursor must reach the transcript boundary"
    );

    let _ = wait_for_replay_with_root_turn(&replay_dir);
    let span_counts = replay_span_name_counts(&replay_dir);
    assert_eq!(
        span_counts.get("Turn 1"),
        Some(&1),
        "first turn must be exported exactly once: {span_counts:?}"
    );
    assert_eq!(
        span_counts.get("Turn 2"),
        Some(&1),
        "final turn must be exported exactly once: {span_counts:?}"
    );

    let log = std::fs::read_to_string(
        dir.path()
            .join("logs")
            .join("cchooks_session-structured-session-end-barrier.log"),
    )
    .expect("read session log");
    assert!(log.contains("structured SessionEnd queue barrier: result=drained"));
}

#[cfg(unix)]
#[test]
fn structured_session_end_finalizes_tail_deferred_by_the_in_flight_active_stop() {
    let _guard = performance_test_lock()
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let dir = tempfile::tempdir().expect("tempdir");
    let session_id = "session-structured-deferred-tail";
    let transcript_path = dir.path().join("session.jsonl");
    let replay_dir = dir.path().join("replays");
    let records = [
        json!({"type":"user","uuid":"turn-1","message":{"role":"user","content":"return structured output"},"timestamp":"2026-08-24T00:00:00.000Z"}),
        json!({"type":"assistant","uuid":"assistant-1","message":{"role":"assistant","id":"msg-1","model":"GLM-5.2","content":[{"type":"tool_use","id":"tool-structured","name":"StructuredOutput","input":{"visible_markdown":"done"}}],"stop_reason":"tool_use","usage":{"input_tokens":10,"output_tokens":5}},"timestamp":"2026-08-24T00:00:01.000Z"}),
        json!({"type":"user","uuid":"result-1","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"tool-structured","content":"{\"visible_markdown\":\"done\"}"}]},"timestamp":"2026-08-24T00:00:02.000Z"}),
        json!({"type":"last-prompt","sessionId":session_id}),
    ];
    let mut transcript = records
        .iter()
        .map(Value::to_string)
        .collect::<Vec<_>>()
        .join("\n");
    transcript.push('\n');
    std::fs::write(&transcript_path, transcript).expect("write structured transcript");

    let provisional = run_cchooks_with_env(
        dir.path(),
        json!({
            "hook_event_name": "Stop",
            "session_id": session_id,
            "transcript_path": transcript_path,
            "stop_hook_active": false
        }),
        [("CCHOOKS_STRUCTURED_OUTPUT", "true".to_string())],
    );
    assert!(provisional.status.success());
    let _ = wait_for_stop_state(dir.path());
    wait_for_worker_idle(dir.path(), session_id);

    let (endpoint, accepted) = delayed_http_endpoint(Duration::ZERO);
    let active = run_cchooks_with_env(
        dir.path(),
        json!({
            "hook_event_name": "Stop",
            "session_id": session_id,
            "transcript_path": transcript_path,
            "stop_hook_active": true
        }),
        [
            ("CCHOOKS_STRUCTURED_OUTPUT", "true".to_string()),
            ("CCHOOKS_DEBUG", "true".to_string()),
            ("CCHOOKS_TEST_WORKER_START_DELAY_MS", "2200".to_string()),
            ("CCHOOKS_STOP_TRANSCRIPT_SETTLE_MS", "2100".to_string()),
            (
                "CCHOOKS_REPLAY_DIR",
                replay_dir.to_string_lossy().into_owned(),
            ),
            (
                "CCHOOKS_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
                endpoint.clone(),
            ),
        ],
    );
    assert!(active.status.success());

    let barrier_started = Instant::now();
    let session_end = run_cchooks_with_env(
        dir.path(),
        json!({
            "hook_event_name": "SessionEnd",
            "session_id": session_id,
            "transcript_path": transcript_path
        }),
        [
            ("CCHOOKS_STRUCTURED_OUTPUT", "true".to_string()),
            ("CCHOOKS_DEBUG", "true".to_string()),
            ("CCHOOKS_SESSION_END_BUDGET_MS", "8000".to_string()),
            (
                "CCHOOKS_REPLAY_DIR",
                replay_dir.to_string_lossy().into_owned(),
            ),
            ("CCHOOKS_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", endpoint),
        ],
    );
    let barrier_elapsed = barrier_started.elapsed();
    assert!(session_end.status.success());
    assert!(
        barrier_elapsed >= Duration::from_millis(1_800),
        "SessionEnd must wait for the active Stop's incomplete-tail poll: {barrier_elapsed:?}"
    );
    accepted
        .recv_timeout(Duration::from_secs(1))
        .expect("SessionEnd finalizer must export the structured turn");

    let status = worker_status(dir.path(), session_id);
    assert_eq!(status["status"]["pending_queue_items"], 0);
    assert_eq!(status["status"]["workers_active"], 0);
    assert_eq!(status["status"]["pending_stops"], 0);
    let session_dir = session_runtime_dir(dir.path(), session_id);
    let stop_state: Value = serde_json::from_slice(
        &std::fs::read(session_dir.join("state_stop.json")).expect("read Stop state"),
    )
    .expect("parse Stop state");
    assert!(stop_state.get("pending_final_export").is_none());
    assert_eq!(
        stop_state["offset"].as_u64(),
        Some(std::fs::metadata(&transcript_path).unwrap().len())
    );
    let _ = wait_for_replay_with_root_turn(&replay_dir);
    let span_counts = replay_span_name_counts(&replay_dir);
    assert_eq!(
        span_counts.get("Turn 1"),
        Some(&1),
        "structured final turn must be exported exactly once: {span_counts:?}"
    );
}

#[test]
fn non_true_structured_settings_do_not_enable_the_session_end_barrier() {
    let _guard = performance_test_lock()
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let dir = tempfile::tempdir().expect("tempdir");
    let _ = run_cchooks_with_payload(
        dir.path(),
        json!({
            "hook_event_name": "PreCompact",
            "session_id": "session-barrier-warmup"
        }),
    );

    for (index, setting) in [Some("false"), Some("0"), None, Some(""), Some("invalid")]
        .into_iter()
        .enumerate()
    {
        let session_id = format!("session-unstructured-session-end-{index}");
        let payload = json!({
            "hook_event_name": "SessionEnd",
            "session_id": session_id
        });
        let started = Instant::now();
        let output = match setting {
            Some(value) => run_cchooks_with_env(
                dir.path(),
                payload,
                [
                    ("CCHOOKS_STRUCTURED_OUTPUT", value.to_string()),
                    ("CCHOOKS_DEBUG", "true".to_string()),
                    ("CCHOOKS_TEST_WORKER_START_DELAY_MS", "300".to_string()),
                    ("CCHOOKS_SESSION_END_BUDGET_MS", "2000".to_string()),
                ],
            ),
            None => run_cchooks_with_env(
                dir.path(),
                payload,
                [
                    ("CCHOOKS_DEBUG", "true".to_string()),
                    ("CCHOOKS_TEST_WORKER_START_DELAY_MS", "300".to_string()),
                    ("CCHOOKS_SESSION_END_BUDGET_MS", "2000".to_string()),
                ],
            ),
        };
        let elapsed = started.elapsed();
        assert!(output.status.success());
        assert!(
            elapsed < Duration::from_millis(200),
            "setting {setting:?} unexpectedly enabled the SessionEnd barrier: {elapsed:?}"
        );
        let log = std::fs::read_to_string(
            dir.path()
                .join("logs")
                .join(format!("cchooks_{session_id}.log")),
        )
        .expect("read unstructured SessionEnd log");
        assert!(
            !log.contains("structured SessionEnd queue barrier"),
            "setting {setting:?} must not enter the structured barrier"
        );
    }

    thread::sleep(Duration::from_millis(400));
}

#[test]
fn structured_session_end_wake_failure_returns_and_preserves_recovery_state() {
    let _guard = performance_test_lock()
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let dir = tempfile::tempdir().expect("tempdir");
    let session_id = "session-structured-wake-failure";
    let output = run_cchooks_with_env(
        dir.path(),
        json!({
            "hook_event_name": "SessionEnd",
            "session_id": session_id,
            "transcript_path": dir.path().join("session.jsonl")
        }),
        [
            ("CCHOOKS_STRUCTURED_OUTPUT", "true".to_string()),
            ("CCHOOKS_DEBUG", "true".to_string()),
            ("CCHOOKS_TEST_WORKER_SPAWN_FAILURE", "true".to_string()),
            ("CCHOOKS_SESSION_END_BUDGET_MS", "2000".to_string()),
        ],
    );
    assert!(
        output.status.success(),
        "wake failure must fail open: stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );

    let status = worker_status(dir.path(), session_id);
    assert_eq!(status["status"]["pending_queue_items"], 1);
    assert_eq!(status["status"]["pending_stops"], 1);
    let log = std::fs::read_to_string(
        dir.path()
            .join("logs")
            .join("cchooks_session-structured-wake-failure.log"),
    )
    .expect("read wake failure log");
    assert!(log.contains("structured SessionEnd queue barrier: result=wake_failed"));
}

#[test]
fn structured_session_end_timeout_is_bounded_and_leaves_work_recoverable() {
    let _guard = performance_test_lock()
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let dir = tempfile::tempdir().expect("tempdir");
    let session_id = "session-structured-barrier-timeout";
    let _ = run_cchooks_with_payload(
        dir.path(),
        json!({
            "hook_event_name": "PreCompact",
            "session_id": "session-structured-timeout-warmup"
        }),
    );
    let started = Instant::now();
    let output = run_cchooks_with_env(
        dir.path(),
        json!({
            "hook_event_name": "SessionEnd",
            "session_id": session_id,
            "transcript_path": dir.path().join("session.jsonl")
        }),
        [
            ("CCHOOKS_STRUCTURED_OUTPUT", "1".to_string()),
            ("CCHOOKS_DEBUG", "true".to_string()),
            ("CCHOOKS_TEST_WORKER_START_DELAY_MS", "600".to_string()),
            ("CCHOOKS_SESSION_END_BUDGET_MS", "180".to_string()),
            ("CCHOOKS_STOP_CHECKPOINT_RESERVE_MS", "20".to_string()),
        ],
    );
    let elapsed = started.elapsed();
    assert!(output.status.success());
    let parent_total = parent_total_from_log(
        &dir.path()
            .join("logs")
            .join("cchooks_session-structured-barrier-timeout.log"),
        "SessionEnd",
    );
    assert!(
        parent_total >= Duration::from_millis(100)
            && parent_total < Duration::from_millis(350)
            && elapsed < Duration::from_secs(1),
        "SessionEnd timeout must honor its bounded budget: internal={parent_total:?} wall={elapsed:?}"
    );

    let status = worker_status(dir.path(), session_id);
    assert_eq!(status["status"]["pending_queue_items"], 1);
    assert_eq!(status["status"]["pending_stops"], 1);
    let log = std::fs::read_to_string(
        dir.path()
            .join("logs")
            .join("cchooks_session-structured-barrier-timeout.log"),
    )
    .expect("read timeout log");
    assert!(log.contains("structured SessionEnd queue barrier: result=timeout"));

    thread::sleep(Duration::from_millis(700));
}

#[test]
fn transcript_ingest_barrier_returns_after_local_read_before_delayed_export() {
    let _guard = performance_test_lock()
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    let dir = tempfile::tempdir().expect("tempdir");
    let session_id = "session-local-ingest-before-export";
    let turn_id = "turn-local-ingest-before-export";
    let transcript = dir.path().join("session.jsonl");
    let contents = [
        json!({
            "type": "user",
            "uuid": turn_id,
            "message": {"role": "user", "content": "hello"},
            "timestamp": "2026-08-31T00:00:00Z"
        }),
        json!({
            "type": "assistant",
            "message": {
                "role": "assistant",
                "id": "msg-local-ingest-before-export",
                "model": "claude-test",
                "content": [{"type": "text", "text": "done"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 1}
            },
            "timestamp": "2026-08-31T00:00:01Z"
        }),
    ]
    .into_iter()
    .map(|line| line.to_string())
    .collect::<Vec<_>>()
    .join("\n");
    std::fs::write(&transcript, format!("{contents}\n")).expect("write transcript");

    let seed = run_cchooks_with_payload(
        dir.path(),
        json!({
            "hook_event_name": "UserPromptSubmit",
            "session_id": session_id,
            "uuid": turn_id,
            "prompt": "hello",
            "timestamp": "2026-08-31T00:00:00Z",
            "transcript_path": transcript
        }),
    );
    assert!(seed.status.success());

    let (endpoint, accepted) = delayed_http_endpoint(Duration::from_secs(2));
    let started = Instant::now();
    let output = run_cchooks_with_env(
        dir.path(),
        json!({
            "hook_event_name": "Stop",
            "session_id": session_id,
            "transcript_path": transcript
        }),
        [
            ("CCHOOKS_WAIT_FOR_TRANSCRIPT_INGEST", "true".to_string()),
            ("CCHOOKS_TRANSCRIPT_INGEST_WAIT_MS", "1500".to_string()),
            ("CCHOOKS_DEBUG", "true".to_string()),
            ("CCHOOKS_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", endpoint),
        ],
    );
    let elapsed = started.elapsed();

    assert!(output.status.success());
    assert!(
        elapsed < Duration::from_millis(1500),
        "parent must return after local ingest rather than delayed HTTP; elapsed={elapsed:?}"
    );
    let log = std::fs::read_to_string(
        dir.path()
            .join("logs")
            .join(format!("cchooks_{session_id}.log")),
    )
    .expect("read session log");
    assert!(log.contains("transcript ingest barrier: result=acked"));
    accepted
        .recv_timeout(Duration::from_secs(3))
        .expect("worker must export after acknowledging local ingest");
    wait_for_worker_idle(dir.path(), session_id);
}

#[test]
fn transcript_ingest_barrier_timeout_fails_open_without_late_ack_file() {
    let _guard = performance_test_lock()
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    let dir = tempfile::tempdir().expect("tempdir");
    let session_id = "session-local-ingest-timeout";
    let transcript = dir.path().join("session.jsonl");
    std::fs::write(&transcript, "").expect("write transcript");

    let wait_ms = 50;
    let worker_delay = Duration::from_secs(1);
    let started = Instant::now();
    let output = run_cchooks_with_env(
        dir.path(),
        json!({
            "hook_event_name": "Stop",
            "session_id": session_id,
            "transcript_path": transcript
        }),
        [
            ("CCHOOKS_WAIT_FOR_TRANSCRIPT_INGEST", "true".to_string()),
            ("CCHOOKS_TRANSCRIPT_INGEST_WAIT_MS", wait_ms.to_string()),
            (
                "CCHOOKS_TEST_WORKER_START_DELAY_MS",
                worker_delay.as_millis().to_string(),
            ),
            ("CCHOOKS_STOP_TRANSCRIPT_SETTLE_MS", "1".to_string()),
            ("CCHOOKS_DEBUG", "true".to_string()),
        ],
    );
    let elapsed = started.elapsed();

    assert!(
        output.status.success(),
        "hook must succeed after timeout: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(
        elapsed < worker_delay,
        "hook must return before the delayed worker; elapsed={elapsed:?}"
    );
    let log = std::fs::read_to_string(
        dir.path()
            .join("logs")
            .join(format!("cchooks_{session_id}.log")),
    )
    .expect("read session log");
    let outcome = log
        .lines()
        .find_map(|line| line.split_once("transcript ingest barrier: "))
        .expect("transcript ingest barrier log")
        .1;
    // Measure the hook's wait separately from process startup and teardown.
    let waited_ms = outcome
        .strip_prefix("result=timeout elapsed_ms=")
        .and_then(|value| value.strip_suffix("; fail-open"))
        .unwrap_or_else(|| panic!("expected timeout and fail-open, got {outcome}"))
        .parse::<u64>()
        .expect("barrier wait in milliseconds");
    assert!(
        (wait_ms..180).contains(&waited_ms),
        "barrier must honor its wait budget; waited={waited_ms}ms"
    );
    wait_for_worker_idle(dir.path(), session_id);
    let receipt_files = std::fs::read_dir(dir.path().join("state"))
        .into_iter()
        .flatten()
        .flatten()
        .flat_map(|entry| {
            std::fs::read_dir(entry.path())
                .into_iter()
                .flatten()
                .flatten()
        })
        .filter_map(|entry| entry.file_name().to_str().map(str::to_string))
        .filter(|name| name.starts_with("transcript.ingest."))
        .collect::<Vec<_>>();
    assert!(
        receipt_files.is_empty(),
        "timed-out receipt must not be recreated: {receipt_files:?}"
    );
}

#[test]
fn transcript_ingest_barrier_acknowledges_early_handler_return() {
    let _guard = performance_test_lock()
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    let dir = tempfile::tempdir().expect("tempdir");
    let session_id = "session-local-ingest-early-return";
    let transcript = dir.path().join("session.jsonl");
    std::fs::write(&transcript, "").expect("write transcript");

    let started = Instant::now();
    let output = run_cchooks_with_env(
        dir.path(),
        json!({
            "hook_event_name": "Stop",
            "session_id": session_id,
            "transcript_path": transcript,
            "stop_hook_active": true
        }),
        [
            ("CCHOOKS_WAIT_FOR_TRANSCRIPT_INGEST", "true".to_string()),
            ("CCHOOKS_TRANSCRIPT_INGEST_WAIT_MS", "1500".to_string()),
            ("CCHOOKS_DEBUG", "true".to_string()),
            // The common edition has no embedded exporter configuration.
            // This Stop must reach the skip branch without sending HTTP.
            (
                "CCHOOKS_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
                "http://127.0.0.1:1/v1/traces".to_string(),
            ),
        ],
    );
    let elapsed = started.elapsed();

    assert!(output.status.success());
    assert!(
        elapsed < Duration::from_millis(1500),
        "handler early return must acknowledge instead of timing out; elapsed={elapsed:?}"
    );
    let log = std::fs::read_to_string(
        dir.path()
            .join("logs")
            .join(format!("cchooks_{session_id}.log")),
    )
    .expect("read session log");
    assert!(log.contains("transcript ingest barrier: result=acked"));
    assert!(log.contains("stop_hook_active=true, skipping"));
    wait_for_worker_idle(dir.path(), session_id);
}

#[test]
fn transcript_ingest_fallback_runs_before_panic_result_handling() {
    let main_source = include_str!("../src/main.rs");
    let worker_payload = main_source
        .split_once("fn run_worker_payload")
        .expect("worker payload function")
        .1
        .split_once("struct HookRunResult")
        .expect("worker payload end")
        .0;
    let catch = worker_payload
        .find("let result = panic::catch_unwind")
        .expect("worker panic guard");
    let fallback = worker_payload[catch..]
        .find("worker::acknowledge_transcript_ingest")
        .map(|offset| catch + offset)
        .expect("receipt fallback acknowledgement");
    let result_match = worker_payload[catch..]
        .find("let (success, stop_outcome) = match result")
        .map(|offset| catch + offset)
        .expect("panic result handling");

    assert!(
        fallback < result_match,
        "receipt fallback must run for both normal and panic catch results"
    );
}

#[test]
fn pretooluse_bash_returns_updated_input_control_response() {
    let dir = tempfile::tempdir().expect("tempdir");
    let user_prompt = json!({
        "hook_event_name": "UserPromptSubmit",
        "session_id": "session-control-response",
        "uuid": "prompt-control-response",
        "timestamp": "2026-08-24T00:00:00Z",
        "prompt": "seed this turn"
    });
    let seed_output = run_cchooks_with_payload(dir.path(), user_prompt);
    assert!(
        seed_output.status.success(),
        "UserPromptSubmit seed must succeed: stderr={}",
        String::from_utf8_lossy(&seed_output.stderr)
    );

    let payload = json!({
        "hook_event_name": "PreToolUse",
        "session_id": "session-control-response",
        "cwd": dir.path(),
        "tool_name": "Bash",
        "tool_use_id": "toolu_control_response",
        "tool_input": {
            "command": "printf ready",
            "description": "preserved description",
            "timeout": 1234
        }
    });

    let output = run_cchooks_with_payload(dir.path(), payload);
    assert!(
        output.status.success(),
        "cchooks must exit successfully: stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );

    let stdout = String::from_utf8(output.stdout).expect("stdout utf8");
    let response: Value = serde_json::from_str(stdout.trim()).expect("hook JSON response");
    let hook_output = &response["hookSpecificOutput"];
    assert_eq!(hook_output["hookEventName"], "PreToolUse");
    assert_eq!(hook_output["permissionDecision"], "allow");

    let updated_input = &hook_output["updatedInput"];
    assert_eq!(updated_input["description"], "preserved description");
    assert_eq!(updated_input["timeout"], 1234);
    let command = updated_input["command"].as_str().expect("updated command");
    assert!(command.contains("export TRACEPARENT="));
    assert!(command.contains("export AGENTIC_TOOL_USE_ID='toolu_control_response'"));
    assert!(
        command.ends_with("printf ready"),
        "original command must remain the executable tail: {command}"
    );
}

#[test]
fn tool_hook_parent_contract_does_not_join_network_send_flush() {
    let main_source = include_str!("../src/main.rs");
    let parent_start = main_source
        .find("fn run_parent_hook()")
        .expect("parent hook function");
    let parent_end = main_source[parent_start..]
        .find("fn run_worker_entry()")
        .map(|offset| parent_start + offset)
        .expect("worker entry follows parent");
    let parent = &main_source[parent_start..parent_end];

    assert!(
        !parent.contains("run_send_tasks")
            && !parent.contains("registry.dispatch")
            && !parent.contains("create_metrics_collector")
            && !parent.contains("MetricsCollector::with_headers"),
        "PreToolUse/PostToolUse parent must not run handlers, metrics, or joined network flushes"
    );
}

#[test]
fn worker_handoff_uses_session_queue_instead_of_per_event_stdin() {
    let main_source = include_str!("../src/main.rs");
    let worker_source = include_str!("../src/worker.rs");
    let file_lock_source = include_str!("../src/common/file_lock.rs");
    let dirs_source = include_str!("../src/common/dirs.rs");
    let spawn_start = worker_source
        .find("pub fn spawn_detached_raw")
        .expect("raw worker handoff");
    let spawn_end = worker_source[spawn_start..]
        .find("pub fn write_stop_intent")
        .map(|offset| spawn_start + offset)
        .expect("next worker function");
    let spawn = &worker_source[spawn_start..spawn_end];
    let entry_start = main_source
        .find("fn run_worker_entry()")
        .expect("worker entry");
    let entry_end = main_source[entry_start..]
        .find("fn run_worker_payload")
        .map(|offset| entry_start + offset)
        .expect("worker payload follows entry");
    let entry = &main_source[entry_start..entry_end];

    assert!(
        spawn.contains("enqueue_and_wake")
            && dirs_source.contains("worker.lock")
            && dirs_source.contains("worker.queue.lock")
            && worker_source.contains("OpenOptions::new()")
            && worker_source.contains(".append(true)")
            && worker_source.contains("file.sync_data()"),
        "parent handoff must durably append one JSONL record to the session queue"
    );
    assert!(
        !spawn.contains("Stdio::piped()")
            && !spawn.contains("child.stdin")
            && !spawn.contains("stdin.write_all")
            && !worker_source.contains("tempfile::tempfile()")
            && !worker_source.contains("Stdio::from(envelope)")
            && !worker_source.contains("setsid"),
        "parent/worker handoff must not use blocking stdin, per-event tempfile stdin, or setsid"
    );
    assert!(
        entry.contains("inherited_session_id")
            && entry.contains("missing session queue identity")
            && !entry.contains("read_hook_payload"),
        "internal workers must require a session queue identity instead of falling back to one stdin event"
    );
    assert!(
        worker_source.contains(".stdin(Stdio::null())")
            && worker_source.contains(".stdout(Stdio::null())")
            && worker_source.contains(".stderr(Stdio::null())")
            && worker_source.contains("process_group(0)")
            && worker_source.contains("CREATE_NEW_PROCESS_GROUP")
            && worker_source.contains("CREATE_NO_WINDOW")
            && worker_source.contains("DETACHED_PROCESS")
            && worker_source.contains("clear_inheritable_hook_stdio()?")
            && worker_source.contains("SetHandleInformation(handle, HANDLE_FLAG_INHERIT, 0)"),
        "worker process must use null stdio, platform process-group adapter, and Windows handle isolation"
    );
    assert!(
        file_lock_source.contains("LockFileEx")
            && file_lock_source.contains("libc::flock")
            && !file_lock_source.contains("lock-dir")
            && !file_lock_source.contains("no-op"),
        "worker locks must use OS file locking on Windows and Unix without fallback locks"
    );
    assert!(
        dirs_source.contains("session_worker_lock_file")
            && dirs_source.contains("session_worker_queue_lock_file")
            && dirs_source.contains("session_worker_queue_offset_file"),
        "worker queue paths must expose separate owner, queue, and offset artifacts"
    );

    let parent_wake = worker_source
        .split_once("pub fn wake_session(")
        .expect("parent wake function")
        .1
        .split_once("pub fn wake_session_confirmed")
        .expect("confirmed handoff follows parent wake")
        .0;
    let confirmed_handoff = worker_source
        .split_once("pub fn wake_session_confirmed")
        .expect("confirmed handoff function")
        .1
        .split_once("fn fail_unowned_start_marker")
        .expect("startup failure helper follows confirmed handoff")
        .0;
    assert!(
        !parent_wake.contains("wait_for_spawned_worker_start")
            && confirmed_handoff.contains("wait_for_spawned_worker_start"),
        "parent wake must return after spawn while runtime handoff retains strict ownership acknowledgement"
    );
}

#[test]
fn internal_worker_rejects_missing_session_queue_identity() {
    let output = Command::new(env!("CARGO_BIN_EXE_cchooks"))
        .arg("__worker")
        .env_remove("CCHOOKS_WORKER_SESSION_ID")
        .output()
        .expect("run internal worker without queue identity");

    assert_eq!(output.status.code(), Some(2));
    assert!(String::from_utf8_lossy(&output.stderr).contains("missing session queue identity"));
}

#[cfg(unix)]
#[test]
fn same_session_burst_is_drained_by_one_real_worker_process() {
    use std::fs::OpenOptions;
    use std::os::fd::AsRawFd;
    use std::os::unix::fs::OpenOptionsExt;

    let _guard = performance_test_lock()
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let root = tempfile::tempdir().expect("tempdir");
    let session_id = "single-worker-process-evidence";
    let runtime_dir = session_runtime_dir(root.path(), session_id);
    std::fs::create_dir_all(&runtime_dir).expect("runtime dir");
    let owner_file = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .mode(0o600)
        .open(runtime_dir.join("worker.lock"))
        .expect("owner lock file");
    assert_eq!(
        unsafe { libc::flock(owner_file.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) },
        0,
        "test must hold session ownership before the burst"
    );

    const EVENTS: usize = 16;
    let hooks = (0..EVENTS)
        .map(|sequence| {
            let home = root.path().to_path_buf();
            thread::spawn(move || {
                run_cchooks_with_payload(
                    &home,
                    json!({
                        "hook_event_name": "PreCompact",
                        "session_id": session_id,
                        "sequence": sequence
                    }),
                )
            })
        })
        .collect::<Vec<_>>();
    for hook in hooks {
        let output = hook.join().expect("hook thread");
        assert!(
            output.status.success(),
            "hook failed: {}",
            String::from_utf8_lossy(&output.stderr)
        );
    }
    let queue = runtime_dir.join("worker.queue.jsonl");
    assert_eq!(
        std::fs::read_to_string(&queue)
            .expect("queued burst")
            .lines()
            .count(),
        EVENTS
    );
    assert!(
        worker_process_ids(&root.path().join("logs")).is_empty(),
        "busy-session hooks must not create owner-waiting workers"
    );

    drop(owner_file);
    let wake = run_cchooks_with_payload(
        root.path(),
        json!({
            "hook_event_name": "PreCompact",
            "session_id": session_id,
            "sequence": EVENTS
        }),
    );
    assert!(wake.status.success());
    let deadline = Instant::now() + Duration::from_secs(10);
    while Instant::now() < deadline {
        if std::fs::metadata(&queue).is_ok_and(|metadata| metadata.len() == 0)
            && worker_process_ids(&root.path().join("logs")).len() == 1
        {
            break;
        }
        thread::sleep(Duration::from_millis(20));
    }
    let worker_pids = worker_process_ids(&root.path().join("logs"));
    assert_eq!(
        worker_pids.len(),
        1,
        "the queued burst was not drained by exactly one real OS process: {worker_pids:?}"
    );
    assert_eq!(std::fs::metadata(queue).expect("queue metadata").len(), 0);
}

#[cfg(unix)]
#[test]
fn delayed_worker_start_does_not_delay_parent_or_strand_queue() {
    let _guard = performance_test_lock()
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let root = tempfile::tempdir().expect("tempdir");

    // Warm executable loading while keeping the actual session worker cold.
    let _ = run_cchooks_with_payload(
        root.path(),
        json!({
            "hook_event_name": "PreCompact",
            "session_id": "delayed-worker-warmup"
        }),
    );

    let session_id = "delayed-worker-start";
    let started = Instant::now();
    let output = run_cchooks_with_env(
        root.path(),
        json!({
            "hook_event_name": "PreCompact",
            "session_id": session_id
        }),
        [
            ("CCHOOKS_DEBUG", "true".to_string()),
            ("CCHOOKS_TEST_WORKER_START_DELAY_MS", "150".to_string()),
        ],
    );
    let wall = started.elapsed();
    assert!(output.status.success());
    let parent_total = parent_total_from_log(
        &root
            .path()
            .join("logs")
            .join("cchooks_delayed-worker-start.log"),
        "PreCompact",
    );
    assert!(
        parent_total < Duration::from_millis(50) && wall < Duration::from_millis(100),
        "delayed worker start blocked parent: internal={parent_total:?} wall={wall:?}"
    );

    let queue = session_runtime_dir(root.path(), session_id).join("worker.queue.jsonl");
    let deadline = Instant::now() + Duration::from_secs(5);
    while Instant::now() < deadline {
        if std::fs::metadata(&queue).is_ok_and(|metadata| metadata.len() == 0) {
            break;
        }
        thread::sleep(Duration::from_millis(20));
    }
    assert_eq!(std::fs::metadata(queue).expect("queue metadata").len(), 0);
}

#[test]
fn detached_worker_does_not_keep_hook_output_pipes_open() {
    let _guard = performance_test_lock()
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let root = tempfile::tempdir().expect("tempdir");
    let session_id = "windows-detached-pipe-eof";

    let started = Instant::now();
    let output = run_cchooks_with_env(
        root.path(),
        json!({
            "hook_event_name": "PreCompact",
            "session_id": session_id
        }),
        [
            ("CCHOOKS_DEBUG", "true".to_string()),
            ("CCHOOKS_TEST_WORKER_START_DELAY_MS", "3000".to_string()),
        ],
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
    let deadline = Instant::now() + Duration::from_secs(8);
    while Instant::now() < deadline {
        if std::fs::metadata(&queue).is_ok_and(|metadata| metadata.len() == 0) {
            break;
        }
        thread::sleep(Duration::from_millis(20));
    }
    assert_eq!(std::fs::metadata(queue).expect("queue metadata").len(), 0);
}

#[test]
fn tool_hook_runtime_contract_moves_http_out_of_parent_handlers() {
    let main_source = include_str!("../src/main.rs");
    let parent_start = main_source
        .find("fn run_parent_hook()")
        .expect("parent hook function");
    let parent_end = main_source[parent_start..]
        .find("fn run_worker_entry()")
        .map(|offset| parent_start + offset)
        .expect("worker entry follows parent");
    let parent = &main_source[parent_start..parent_end];
    for prohibited in [
        "post_otel_to_langfuse(",
        "post_otel_to_langfuse_once(",
        "post_otel_to_langfuse_once_classified(",
    ] {
        assert!(
            !parent.contains(prohibited),
            "parent runtime must not perform HTTP directly; found {prohibited}"
        );
    }
}

#[test]
fn pretooluse_control_response_uses_the_tool_context_platform_gate() {
    let main_source = include_str!("../src/main.rs");
    assert!(
        main_source.contains(
            "#[cfg(all(feature = \"tool_context\", not(windows)))]\n    if matches!(event, HookEvent::PreToolUse)"
        ),
        "the synchronous PreToolUse control response must be compiled only when the tool_context module is available"
    );
}

#[test]
fn post_worker_reconciles_before_claiming_and_uses_long_lease() {
    let worker_source = include_str!("../src/worker.rs");
    let reconcile = worker_source
        .find("store.reconcile")
        .expect("post worker must reconcile before claim");
    let claim = worker_source
        .find("store.claim_post")
        .expect("post worker must claim after reconcile");
    assert!(
        reconcile < claim,
        "Post worker must not claim/export before PreToolUse has been recorded"
    );
    assert!(
        worker_source.contains("TOOL_POST_EXPORT_LEASE_MS")
            && worker_source.contains("120_000")
            && !worker_source.contains("claim_post(&claim_id, now_millis(), 5_000)"),
        "Post worker claim lease must cover slow final export windows, not the old 5s smoke value"
    );
}

#[test]
fn sharded_state_contract_uses_domain_files_in_state_root() {
    let dirs_source = include_str!("../src/common/dirs.rs");
    let domain_source = include_str!("../src/hooks/langfuse/domain_state.rs");
    let state_sources = format!("{dirs_source}\n{domain_source}");
    assert!(
        state_sources.contains("state_turn.json")
            && state_sources.contains("state_tools.json")
            && dirs_source.contains("tool_{tool_key}")
            && dirs_source.contains("format!(\"{phase}.json\")"),
        "state shards must be modeled as state/<session>/state_*.json plus state/<session>/tool_<tool>/pre|post.json"
    );
    assert!(
        domain_source.contains("if self.state_file.exists()")
            && domain_source.contains("self.load_legacy_domain()")
            && domain_source.contains("save_state(&self.state_file, &state)"),
        "new domain files must be authoritative, with legacy state.json used only as a missing-file read fallback"
    );
}

#[test]
fn stop_parent_persists_durable_intent_and_worker_clears_it_only_after_success() {
    let main_source = include_str!("../src/main.rs");
    let worker_source = include_str!("../src/worker.rs");

    let parent_start = main_source
        .find("fn run_parent_hook()")
        .expect("parent hook function");
    let parent_end = main_source[parent_start..]
        .find("fn run_worker_entry()")
        .map(|offset| parent_start + offset)
        .expect("worker entry follows parent");
    let parent = &main_source[parent_start..parent_end];
    assert!(
        parent.contains("let handoff = worker::write_stop_intent_with_turn_seed")
            && parent.contains("handoff.apply_to_payload(&mut payload)")
            && parent
                .find("worker::write_stop_intent_with_turn_seed")
                .expect("stop intent")
                < parent
                    .find("worker::spawn_detached_with_stop_snapshot")
                    .expect("worker queue"),
        "Stop-family parent must persist the recovery intent before detaching the worker"
    );
    assert!(
        worker_source.contains("sync_file_and_parent(store.state_file())")
            && worker_source.contains(".and_then(|file| file.sync_all())")
            && worker_source.contains(".and_then(|directory| directory.sync_all())"),
        "Stop intent must be fsynced as a file and directory entry before parent return"
    );

    let cleanup_start = worker_source
        .find("pub fn finish_stop_worker")
        .expect("stop cleanup function");
    let cleanup_end = worker_source[cleanup_start..]
        .find("pub fn prepare_pre_worker")
        .map(|offset| cleanup_start + offset)
        .expect("next worker function");
    let cleanup = &worker_source[cleanup_start..cleanup_end];
    assert!(
        cleanup.contains("StopOutcome::Completed | StopOutcome::IntentionallySkipped")
            && cleanup.contains("StopOutcome::Deferred { reason }")
            && cleanup.contains("object.remove(\"pending_final_export\")"),
        "deferred Stop workers must retain the intent; only terminal outcomes may clear it"
    );
}

#[test]
fn structured_stop_coordinator_precedes_every_worker_side_effect() {
    let main_source = include_str!("../src/main.rs");
    let worker_start = main_source
        .find("fn run_worker_payload")
        .expect("worker payload function");
    let worker_end = main_source[worker_start..]
        .find("struct HookRunResult")
        .map(|offset| worker_start + offset)
        .expect("worker result follows payload function");
    let worker_payload = &main_source[worker_start..worker_end];

    let coordinate = worker_payload
        .find("worker::prepare_stop_worker")
        .expect("Stop worker coordinator");
    for side_effect in [
        "worker::drain_tool_events",
        "create_metrics_collector",
        "run_hook_with_payload",
        "finish_stop_worker",
    ] {
        assert!(
            coordinate < worker_payload.find(side_effect).expect(side_effect),
            "Stop coordination must happen before {side_effect}"
        );
    }
}

#[test]
fn structured_stop_recovery_and_active_guards_follow_authorized_intent_contract() {
    let worker_source = include_str!("../src/worker.rs");
    assert!(
        worker_source.contains("PHASE_AWAITING_CONTINUATION")
            && worker_source.contains("return Ok(None)")
            && worker_source.contains("INTERNAL_FINALIZER_AUTHORIZED")
            && worker_source.contains("stop_intent_matches"),
        "awaiting intents must not be recovered and finalization must be owner-authorized"
    );

    for handler_source in [
        include_str!("../src/hooks/langfuse/langfuse_handler.rs"),
        include_str!("../src/hooks/turn_digest/turn_digest_handler.rs"),
        include_str!("../src/hooks/dynamic/dynamic_handler.rs"),
        include_str!("../src/hooks/subagent/subagent_handler.rs"),
    ] {
        assert!(
            handler_source.contains("crate::hooks::active_stop_should_skip(payload)"),
            "every Stop finalizer must preserve the active-loop guard while admitting authorized finalizers"
        );
    }
}

#[test]
fn stop_recovery_runs_only_in_bounded_maintenance_and_tool_drain_stays_bounded() {
    let main_source = include_str!("../src/main.rs");
    let worker_source = include_str!("../src/worker.rs");
    assert!(
        main_source.contains("worker::drain_queue_with_maintenance")
            && main_source.contains("run_scheduled_stop_recovery_slice")
            && main_source.contains("worker::scheduled_stop_recovery")
            && main_source.contains("worker::drain_tool_events")
    );
    let payload_start = main_source.find("fn run_worker_payload(").unwrap();
    let payload_end = main_source[payload_start..]
        .find("fn run_hook_with_payload(")
        .map(|offset| payload_start + offset)
        .unwrap();
    assert!(
        !main_source[payload_start..payload_end].contains("recover_pending_stop_intent"),
        "ordinary hook payload processing must not recover an old Stop intent"
    );
    assert!(
        worker_source.contains("MAX_STOP_DRAIN_EVENTS")
            && worker_source.contains("TOOL_ORPHAN_TTL_MS")
            && worker_source.contains("pending_final_export")
            && worker_source.contains("cchooks_recovered_stop_intent")
    );
    assert!(
        worker_source.contains("spawn_detached(&payload, logger)")
            && worker_source.contains("pending_post_payload_if_ready")
            && worker_source.contains("retrigger_post_worker(&post_payload, logger)")
            && worker_source.contains("drain_queue_with_maintenance"),
        "Stop recovery maintenance, tool drain, and Pre-triggered Post retry must share session ownership"
    );
}

#[test]
fn compatibility_composite_state_store_is_worker_only() {
    let main_source = include_str!("../src/main.rs");
    let parent_start = main_source.find("fn run_parent_hook()").unwrap();
    let parent_end = main_source[parent_start..]
        .find("fn run_worker_entry()")
        .map(|offset| parent_start + offset)
        .unwrap();
    let parent = &main_source[parent_start..parent_end];
    assert!(!parent.contains("SessionStateStore"));
    assert!(!parent.contains(".state_transaction.lock"));
}

#[test]
fn truncation_logging_contract_records_cost_without_payload_content() {
    let otel_source = include_str!("../src/common/otel.rs");
    for required in [
        "elapsed_ms",
        "original_bytes",
        "kept_bytes",
        "limit_bytes",
        "hash_present",
    ] {
        assert!(
            otel_source.contains(required),
            "truncation logs must include {required}"
        );
    }
    for prohibited in ["tool_input", "tool_output", "request_body", "Authorization"] {
        assert!(
            !otel_source.contains(&format!("truncation {prohibited}")),
            "truncation logs must not emit sensitive payload field {prohibited}"
        );
    }
}

#[test]
fn worker_logs_only_payload_metadata_not_raw_hook_content() {
    let main_source = include_str!("../src/main.rs");
    assert!(main_source.contains("stdin payload accepted"));
    assert!(
        !main_source.contains("payload_for_log(&payload)")
            && !main_source.contains("stdin payload: {}")
            && !main_source.contains("payload={}"),
        "worker logs must not include raw or merely truncated hook payload content"
    );
}

#[test]
fn segmented_timing_contract_covers_parent_and_worker_boundaries() {
    let main_source = include_str!("../src/main.rs");
    let worker_source = include_str!("../src/worker.rs");
    let otel_source = include_str!("../src/common/otel.rs");
    let state_source = include_str!("../src/hooks/langfuse/state.rs");
    let domain_source = include_str!("../src/hooks/langfuse/domain_state.rs");
    let sources =
        format!("{main_source}\n{worker_source}\n{otel_source}\n{state_source}\n{domain_source}");
    for phase in [
        "stdin",
        "queue",
        "lock",
        "build",
        "http",
        "http_total",
        "checkpoint",
        "total",
    ] {
        assert!(
            sources.contains(&format!("phase\\\":\\\"{phase}"))
                || sources.contains(&format!("WorkerPhaseGuard::new(\"{phase}\")"))
                || sources.contains(&format!("log_parent_phase(&logger, \"{phase}\""))
                || sources.contains(&format!("log_parent_phase(logger, \"{phase}\""))
                || sources.contains(&format!("log_worker_phase(&logger, \"{phase}\""))
                || sources.contains(&format!("log_worker_phase(logger, \"{phase}\""))
                || sources.contains(&format!(
                    "observe_current_worker_phase(\n            \"{phase}\""
                ))
                || sources.contains(&format!("observe_current_worker_phase(\"{phase}\"")),
            "missing structured timing phase {phase}"
        );
    }
    assert!(
        otel_source.contains("truncate_value_observed")
            && otel_source.contains("serialize_messages_observed")
            && !otel_source.contains("truncate_value(&"),
        "real OTLP build call sites must use observed truncation wrappers"
    );
    assert!(
        otel_source.contains("log_hook_phase(logger, \"parent\", \"sync\"")
            && otel_source.contains("log_hook_phase(logger, \"worker\", \"async\"")
            && otel_source.contains("\\\"execution\\\":\\\"{}\\\""),
        "structured timing logs must explicitly distinguish sync parent and async worker phases"
    );
    assert!(
        !sources.contains("elapsed_us") && sources.contains("elapsed_ms"),
        "cchooks timing logs must use milliseconds consistently"
    );
    assert!(
        main_source.contains("log_worker_http_total(&logger)")
            && otel_source.contains("context.http_total_ms.saturating_add(elapsed_ms)"),
        "async workers must report aggregate backend HTTP wall time"
    );
    let hooks_source = include_str!("../src/hooks/mod.rs");
    assert!(
        hooks_source.contains("execution=async") && main_source.contains("execution=async"),
        "worker handler and aggregate timing logs must be explicitly marked async"
    );
}

#[test]
fn posttooluse_parent_returns_before_one_second_fake_collector() {
    let _guard = performance_test_lock()
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    let dir = tempfile::tempdir().expect("tempdir");
    // Warm the just-linked test binary once. The first execution on managed macOS
    // can include code-signing/security inspection and is not hook work.
    let _ = run_cchooks_with_payload(
        dir.path(),
        json!({
            "hook_event_name": "PreToolUse",
            "session_id": "session-warmup",
            "tool_name": "Read",
            "tool_use_id": "toolu_warmup",
            "tool_input": {}
        }),
    );

    let endpoint_and_signal = delayed_http_endpoint(Duration::from_secs(1));
    let endpoint = endpoint_and_signal.0;
    let accepted = endpoint_and_signal.1;
    let pre = json!({
        "hook_event_name": "PreToolUse",
        "session_id": "session-fake-collector-delay",
        "cwd": dir.path(),
        "tool_name": "Read",
        "tool_use_id": "toolu_fake_delay",
        "tool_input": {"file_path": "Cargo.toml"}
    });
    let pre_output = run_cchooks_with_payload(dir.path(), pre);
    assert!(pre_output.status.success());
    thread::sleep(Duration::from_millis(100));
    let payload = json!({
        "hook_event_name": "PostToolUse",
        "session_id": "session-fake-collector-delay",
        "cwd": dir.path(),
        "tool_name": "Read",
        "tool_use_id": "toolu_fake_delay",
        "tool_input": {"file_path": "Cargo.toml"},
        "tool_response": {"content": "ok"}
    });

    let started = Instant::now();
    let output = run_cchooks_with_env(
        dir.path(),
        payload,
        [
            ("CCHOOKS_REALTIME_EXPORT", "true".to_string()),
            (
                "CCHOOKS_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
                endpoint.to_string(),
            ),
        ],
    );
    let elapsed = started.elapsed();

    assert!(
        output.status.success(),
        "parent must fail open even when worker/export is delayed: stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(
        elapsed < Duration::from_millis(100),
        "PostToolUse parent must return before fake collector delay; elapsed={elapsed:?}"
    );
    accepted
        .recv_timeout(Duration::from_secs(2))
        .expect("detached worker must reach the delayed collector");
}

#[test]
fn stop_family_live_exports_preserve_prompt_traceparent() {
    let _guard = performance_test_lock()
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    let trace_id = "41049c989ea3510bafe6d446672d7b1c";
    let upstream_parent = "61c42769ba435f72";
    let traceparent = format!("00-{trace_id}-{upstream_parent}-01");

    for event in ["Stop", "StopFailure", "SessionEnd"] {
        let dir = tempfile::tempdir().expect("tempdir");
        let session_id = format!("session-live-{}", event.to_ascii_lowercase());
        let turn_id = format!("turn-live-{}", event.to_ascii_lowercase());
        let transcript = dir.path().join(format!("{session_id}.jsonl"));
        let prompt_timestamp = agentic_otel_common::now_iso8601_pub();
        let contents = [
            json!({"type":"user","uuid":turn_id.clone(),"message":{"role":"user","content":"hello"},"timestamp":prompt_timestamp}),
            json!({"type":"assistant","message":{"role":"assistant","id":format!("msg-{event}"),"content":[{"type":"text","text":"done"}],"stop_reason":"end_turn","model":"claude-test","usage":{"input_tokens":1,"output_tokens":1}},"timestamp":agentic_otel_common::now_iso8601_pub()}),
        ]
        .into_iter()
        .map(|line| line.to_string())
        .collect::<Vec<_>>()
        .join("\n");
        std::fs::write(&transcript, format!("{contents}\n")).expect("write transcript");

        let prompt_output = run_cchooks_with_env(
            dir.path(),
            json!({
                "hook_event_name": "UserPromptSubmit",
                "session_id": session_id.clone(),
                "prompt": "hello"
            }),
            [("TRACEPARENT", traceparent.clone())],
        );
        #[cfg(target_os = "macos")]
        {
            use std::os::unix::process::ExitStatusExt;
            if prompt_output.status.signal() == Some(9) {
                eprintln!(
                    "managed macOS security software killed the child binary; skipping live child-process assertion"
                );
                return;
            }
        }
        assert!(
            prompt_output.status.success(),
            "seed {event} turn status={:?}: {}",
            prompt_output.status,
            String::from_utf8_lossy(&prompt_output.stderr)
        );
        // Finish the prompt worker's state transaction before exercising the
        // trace binding. A contended turn lock has a separate fail-open test.
        wait_for_worker_idle(dir.path(), &session_id);

        let (endpoint, body_rx) = capturing_http_endpoint();
        let stop_output = run_cchooks_with_env(
            dir.path(),
            json!({
                "hook_event_name": event,
                "session_id": session_id.clone(),
                "transcript_path": transcript.clone(),
                "cwd": dir.path()
            }),
            [
                ("CCHOOKS_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", endpoint),
                ("CCHOOKS_STOP_TRANSCRIPT_SETTLE_MS", "0".to_string()),
            ],
        );
        assert!(
            stop_output.status.success(),
            "{event} parent failed: {}",
            String::from_utf8_lossy(&stop_output.stderr)
        );
        let body = body_rx
            .recv_timeout(Duration::from_secs(10))
            .unwrap_or_else(|error| {
                let mut diagnostics = String::new();
                collect_text(dir.path(), &mut diagnostics);
                panic!("{event} trace export not received: {error}\n{diagnostics}")
            });
        let spans = exported_spans(&body);
        assert!(!spans.is_empty(), "{event} exported no spans");
        assert!(
            spans.iter().all(|span| span["traceId"] == trace_id),
            "{event} rewrote injected trace-id: {spans:?}"
        );
        let root = spans
            .iter()
            .find(|span| span["parentSpanId"] == upstream_parent)
            .unwrap_or_else(|| panic!("{event} root lost upstream parent: {spans:?}"));
        assert!(
            spans.iter().any(|span| {
                span["spanId"] != root["spanId"] && span["parentSpanId"] == root["spanId"]
            }),
            "{event} generation topology changed: {spans:?}"
        );
    }
}

#[test]
fn pretooluse_parent_latency_stays_under_fifty_milliseconds() {
    let _guard = performance_test_lock()
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    let dir = tempfile::tempdir().expect("tempdir");
    let mut internal_samples = Vec::new();
    let mut wall_samples = Vec::new();
    for index in 0..22 {
        let session_id = "session-parent-latency";
        let payload = json!({
            "hook_event_name": "PreToolUse",
            "session_id": session_id,
            "cwd": dir.path(),
            "tool_name": "Read",
            "tool_use_id": format!("toolu_parent_latency_{index}"),
            "tool_input": {"file_path": "Cargo.toml"}
        });
        let started = Instant::now();
        let output =
            run_cchooks_with_env(dir.path(), payload, [("CCHOOKS_DEBUG", "true".to_string())]);
        let elapsed = started.elapsed();
        assert!(
            output.status.success(),
            "PreToolUse parent must fail open: stderr={}",
            String::from_utf8_lossy(&output.stderr)
        );
        // Exclude two cold-start/security-inspection warmups from the hook p95.
        if index >= 2 {
            wall_samples.push(elapsed);
            internal_samples.push(parent_total_from_log(
                &dir.path()
                    .join("logs")
                    .join("cchooks_session-parent-latency.log"),
                "PreToolUse",
            ));
        }
    }
    internal_samples.sort_unstable();
    wall_samples.sort_unstable();
    let p95 = internal_samples[(internal_samples.len() * 95).div_ceil(100) - 1];
    // Process wall time includes host scheduling and endpoint-security scans;
    // use the same p95 window as the in-process timing so one external stall
    // cannot turn this into a max-latency assertion over only 20 samples.
    let wall_p95 = wall_samples[(wall_samples.len() * 95).div_ceil(100) - 1];
    assert!(
        p95 < Duration::from_millis(50) && wall_p95 < Duration::from_millis(50),
        "PreToolUse parent latency budget exceeded: internal_p95={p95:?} wall_p95={wall_p95:?} internal_samples={internal_samples:?} wall_samples={wall_samples:?}"
    );
}

#[test]
fn large_posttool_payload_handoff_stays_below_parent_budget() {
    let _guard = performance_test_lock()
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    let dir = tempfile::tempdir().expect("tempdir");
    let _ = run_cchooks_with_payload(
        dir.path(),
        json!({
            "hook_event_name": "PreToolUse",
            "session_id": "large-payload-warmup",
            "tool_name": "Read",
            "tool_use_id": "large-payload-warmup",
            "tool_input": {}
        }),
    );
    let pre = json!({
        "hook_event_name": "PreToolUse",
        "session_id": "large-payload-session",
        "tool_name": "Read",
        "tool_use_id": "large-payload-tool",
        "tool_input": {}
    });
    assert!(run_cchooks_with_payload(dir.path(), pre).status.success());
    thread::sleep(Duration::from_millis(50));
    // Prepare the 1 MiB wire message before launching the process. Otherwise
    // the hook's stdin read includes serialization in the test process.
    let payload = json!({
        "hook_event_name": "PostToolUse",
        "session_id": "large-payload-session",
        "tool_name": "Read",
        "tool_use_id": "large-payload-tool",
        "tool_input": {},
        "tool_response": {"content": "x".repeat(1024 * 1024)}
    })
    .to_string();
    let started = Instant::now();
    let output = run_cchooks_with_wire_payload(
        dir.path(),
        payload.as_bytes(),
        [("CCHOOKS_DEBUG", "true".to_string())],
    );
    let wall = started.elapsed();
    assert!(output.status.success());
    let log = dir.path().join("logs/cchooks_large-payload-session.log");
    let contents = std::fs::read_to_string(&log).expect("read large payload handoff log");
    let envelope = format!("envelope_bytes={}", payload.len());
    assert!(
        contents.lines().any(|line| {
            line.contains(" [PostToolUse] ")
                && line.contains("worker queue handoff accepted:")
                && line.ends_with(&envelope)
        }),
        "large PostToolUse payload was not accepted by the worker queue"
    );
    // The parent total covers stdin read, parsing and durable handoff. Keep
    // process launch and exit wall time as diagnostics for hosted-runner stalls.
    let parent = parent_total_from_log(&log, "PostToolUse");
    eprintln!("large PostToolUse parent={parent:?} wall={wall:?}");
    assert!(
        parent < Duration::from_millis(100),
        "large payload handoff exceeded parent budget: parent={parent:?} wall={wall:?}"
    );
}
