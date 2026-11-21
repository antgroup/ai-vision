//! Kimi Code durable wire records -> the shared Python Kimi wire engine.
//! Source contract: MoonshotAI/kimi-code@f67e6398 (wire 1.1-1.5).
//! Records use milliseconds, while the shared engine takes seconds.
use serde_json::{json, Value};

pub(super) fn read_stop_snapshot(
    path: &std::path::Path,
    timeout: std::time::Duration,
) -> std::io::Result<String> {
    let deadline = std::time::Instant::now() + timeout;
    let mut snapshot = std::fs::read_to_string(path);
    loop {
        let expired = std::time::Instant::now() >= deadline;
        match &snapshot {
            Ok(body) if stop_snapshot_complete(body) || expired => return snapshot,
            Err(error) if error.kind() != std::io::ErrorKind::InvalidData || expired => return snapshot,
            _ => {}
        }
        std::thread::sleep(std::time::Duration::from_millis(10)
            .min(deadline.saturating_duration_since(std::time::Instant::now())));
        let next = std::fs::read_to_string(path);
        // A read may cut an appended UTF-8 character. Keep the last real valid
        // snapshot until the pending write completes; never replace its bytes.
        if next.is_ok() || snapshot.is_err()
            || next.as_ref().is_err_and(|error| error.kind() != std::io::ErrorKind::InvalidData) {
            snapshot = next;
        }
    }
}

fn stop_snapshot_complete(body: &str) -> bool {
    // The native append store encodes complete batches as newline-ended JSONL.
    if !body.ends_with('\n') {
        return false;
    }
    let mut has_turn = false;
    let mut step = None;
    let mut ended = false;
    let mut tools = std::collections::HashSet::new();
    for line in body.lines().filter(|line| !line.trim().is_empty()) {
        let record: Value = match serde_json::from_str(line) {
            Ok(record) => record,
            Err(_) => return false,
        };
        if record["type"] == "turn.prompt" {
            has_turn = true;
            step = None;
            ended = false;
            tools.clear();
        } else if has_turn && record["type"] == "context.append_loop_event" {
            let event = &record["event"];
            match event["type"].as_str() {
                Some("step.begin") => {
                    step = event["uuid"].as_str().map(str::to_owned);
                    ended = false;
                }
                Some("step.end") if step.as_deref().is_some_and(|id| event["uuid"] == id) => {
                    ended = event["finishReason"] == "end_turn";
                }
                Some("tool.call") => {
                    tools.insert(event["toolCallId"].to_string());
                }
                Some("tool.result") => {
                    tools.remove(&event["toolCallId"].to_string());
                }
                _ => {}
            }
        }
    }
    has_turn && ended && tools.is_empty()
}

#[cfg(test)]
mod snapshot_tests {
    use super::*;
    use std::io::Write;
    use std::time::{Duration, Instant};

    fn native_snapshot() -> String {
        include_str!("../../tests/fixtures/kimi-code-2.1.1/native-tool-loop.wire.jsonl")
            .lines()
            .filter(|line| serde_json::from_str::<Value>(line).unwrap()["type"] != "turn.ended")
            .map(|line| format!("{line}\n"))
            .collect()
    }

    #[test]
    fn stop_waits_for_buffered_tool_result_and_final_model_step() {
        let complete = native_snapshot();
        let lines: Vec<_> = complete.lines().collect();
        let split = lines.iter().position(|line| {
            serde_json::from_str::<Value>(line).unwrap()["event"]["type"] == "tool.result"
        }).unwrap();
        let prefix = lines[..split].join("\n") + "\n";
        let suffix = lines[split..].join("\n") + "\n";
        let path = std::env::temp_dir().join(format!("kimi-buffered-stop-{}.jsonl", std::process::id()));
        std::fs::write(&path, prefix).unwrap();
        let appended_path = path.clone();
        let writer = std::thread::spawn(move || {
            std::thread::sleep(Duration::from_millis(30));
            std::fs::OpenOptions::new().append(true).open(appended_path)
                .unwrap().write_all(suffix.as_bytes()).unwrap();
        });
        let observed = read_stop_snapshot(&path, Duration::from_secs(1)).unwrap();
        writer.join().unwrap();
        std::fs::remove_file(path).unwrap();
        assert_eq!(observed.lines().count(), complete.lines().count(),
            "Stop must consume the actual late native receipts, usage and answer");
        assert!(observed == complete, "the returned snapshot must contain the exact native bytes");
    }

    #[test]
    fn completed_step_does_not_wait_for_post_hook_turn_ended() {
        let complete = native_snapshot();
        let path = std::env::temp_dir().join(format!("kimi-complete-stop-{}.jsonl", std::process::id()));
        std::fs::write(&path, &complete).unwrap();
        let started = Instant::now();
        let observed = read_stop_snapshot(&path, Duration::from_secs(5)).unwrap();
        std::fs::remove_file(path).unwrap();
        assert_eq!(observed, complete);
        assert!(started.elapsed() < Duration::from_secs(1));
    }

    #[test]
    fn incomplete_snapshot_is_bounded_without_fabricating_a_terminal_record() {
        let prefix = native_snapshot().lines().take(9).collect::<Vec<_>>().join("\n") + "\n";
        let path = std::env::temp_dir().join(format!("kimi-incomplete-stop-{}.jsonl", std::process::id()));
        std::fs::write(&path, &prefix).unwrap();
        let started = Instant::now();
        let observed = read_stop_snapshot(&path, Duration::from_millis(20)).unwrap();
        std::fs::remove_file(path).unwrap();
        assert_eq!(observed, prefix);
        assert!(started.elapsed() < Duration::from_secs(1));
    }

    #[test]
    fn an_old_terminal_step_does_not_close_a_new_turn_or_step() {
        let complete = native_snapshot();
        assert!(stop_snapshot_complete(&complete));
        assert!(!stop_snapshot_complete(&(complete.clone()
            + "{\"type\":\"turn.prompt\",\"input\":[]}\n")));
        let pending = complete + "{\"type\":\"context.append_loop_event\",\"event\":{\"type\":\"step.begin\",\"uuid\":\"new-step\"}}\n";
        assert!(!stop_snapshot_complete(&pending));
        assert!(!stop_snapshot_complete(&(pending
            + "{\"type\":\"context.append_loop_event\",\"event\":{\"type\":\"step.end\",\"uuid\":\"old-step\",\"finishReason\":\"end_turn\"}}\n")));
    }

    #[test]
    fn terminal_model_step_cannot_replace_a_missing_native_tool_result() {
        let without_result = native_snapshot().lines().filter(|line| {
            serde_json::from_str::<Value>(line).unwrap()["event"]["type"] != "tool.result"
        }).map(|line| format!("{line}\n")).collect::<String>();
        assert!(!stop_snapshot_complete(&without_result));
    }

    #[test]
    fn incomplete_json_tail_cannot_be_hidden_by_a_prior_completed_step() {
        for tail in ["{\"type\":\"turn.prompt\"", "{\"type\":\"context.append_loop_event\",\"event\":{\"type\":\"step.begin\""] {
            assert!(!stop_snapshot_complete(&(native_snapshot() + tail)));
        }
        assert!(!stop_snapshot_complete(native_snapshot().trim_end_matches('\n')));
    }
}

pub(super) fn normalize(record: &Value) -> Option<(String, Value)> {
    let kind = record.get("type")?.as_str()?;
    let pair = match kind {
        "turn.prompt" => ("TurnBegin", json!({"user_input": record.get("input"), "source": "kimi-code"})),
        "turn.ended" => ("TurnEnd", json!({"reason": record.get("reason"), "source": "kimi-code"})),
        "llm.request" if record["kind"] == "loop" => ("CodeRequest", json!({
            "model": record.get("model"), "provider": record.get("provider"), "system_prompt_hash": record.get("systemPromptHash")})),
        "profile.bind" | "config.update" if record["systemPrompt"].is_string() => ("CodeProfile", record["systemPrompt"].clone()),
        "context.append_message" => ("CodeContext", record.get("message")?.clone()),
        "usage.record" if record["usageScope"] == "turn" => ("CodeUsage", json!({
            "agent_id": record.get("agentId"), "model": record.get("model"), "usage": record.get("usage")})),
        "context.append_loop_event" => {
            let event = record.get("event")?;
            match event.get("type")?.as_str()? {
                "step.begin" => ("StepBegin", json!({"n": event.get("step"), "request_id": event.get("uuid"),
                    "agent_id": record.get("agentId"), "source": "kimi-code"})),
                "content.part" => ("ContentPart", event.get("part")?.clone()),
                "step.end" => ("StatusUpdate", json!({"message_id": event.get("messageId"), "request_id": event.get("uuid"),
                    "finish_reason": event.get("finishReason"),
                    "llm_duration_ms": event["llmFirstTokenLatencyMs"].as_u64().zip(event["llmStreamDurationMs"].as_u64()).map(|(a,b)| a.saturating_add(b))})),
                "tool.call" => ("ToolCall", json!({"id": event.get("toolCallId"), "step_uuid": event.get("stepUuid"), "function": {
                    "name": event.get("name"), "arguments": serde_json::to_string(&event["args"]).ok()?}})),
                "tool.result" => {
                    let result = event.get("result")?;
                    ("ToolResult", json!({"tool_call_id": event.get("toolCallId"), "return_value": {
                        "is_error": result.get("isError"), "output": result.get("output"), "message": result.get("message"), "note": result.get("note"), "duration_ms": result.get("durationMs")}}))
                }
                _ => return None,
            }
        }
        _ => return None,
    };
    Some((pair.0.to_string(), pair.1))
}
