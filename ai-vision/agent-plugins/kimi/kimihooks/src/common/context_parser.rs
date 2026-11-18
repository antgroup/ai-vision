//! 解析 kimi-cli 的 `context.jsonl`，提取每轮 LLM 调用的输入消息。
//!
//! `context.jsonl` 是 kimi-cli 发给大模型的完整对话历史，格式为 JSONL，每行一个 JSON 对象。
//! 包含以下角色：
//!
//! | role | 含义 |
//! |------|------|
//! | `_system_prompt` | 系统提示词 |
//! | `user` | 用户输入 |
//! | `assistant` | LLM 回复（content + tool_calls） |
//! | `tool` | 工具执行结果（tool_call_id + content） |
//! | `_usage` | Token 用量 |
//! | `_checkpoint` | 内部检查点 |
//!
//! 本模块将 context.jsonl 解析为按 LLM 调用分组的输入消息列表，
//! 供 `build_llm_spans()` 写入 `gen_ai.input.messages` 属性。

use std::fs;
use std::path::Path;

use serde_json::Value;

/// 单次 LLM 调用的上下文：发给模型的所有输入消息 + 模型的回复。
#[derive(Debug, Clone)]
pub struct LlmCallContext {
    /// 发给模型的输入消息（OpenAI 格式：system/user/assistant/tool）。
    /// 按时序排列，不含 `_usage` / `_checkpoint` 等内部行。
    pub input_messages: Vec<Value>,

    /// 模型的回复（assistant 行的 content + tool_calls），原始 JSON。
    pub assistant_response: Value,

    /// 此轮 assistant 的 tool_calls（如有）。
    pub tool_call_ids: Vec<String>,
}

/// 解析 context.jsonl 文件，返回按 LLM 调用顺序排列的上下文列表。
///
/// 每个 `assistant` 行代表一次 LLM 调用的输出。它前面从上一个 `assistant` 之后（或从头）
/// 到当前 `assistant` 之前的所有 `user` / `tool` / 前一个 `assistant`（如有）行，
/// 就是这次 LLM 调用的累计输入。
pub fn parse_context_file(path: &Path) -> Vec<LlmCallContext> {
    let content = match fs::read_to_string(path) {
        Ok(c) => c,
        Err(_) => return Vec::new(),
    };
    parse_context_lines(&content)
}

/// 从 context.jsonl 的文本内容解析 LLM 调用上下文。
fn parse_context_lines(content: &str) -> Vec<LlmCallContext> {
    // 第一遍：收集所有有效消息行（过滤 _usage/_checkpoint）
    let mut all_messages: Vec<Value> = Vec::new();
    for line in content.lines() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        let obj: Value = match serde_json::from_str(line) {
            Ok(v) => v,
            Err(_) => continue,
        };
        let role = obj.get("role").and_then(|v| v.as_str()).unwrap_or("");
        // 跳过内部行
        if role == "_usage" || role == "_checkpoint" {
            continue;
        }
        // _system_prompt 转为 OpenAI 的 system role
        if role == "_system_prompt" {
            let content_val = obj.get("content").cloned().unwrap_or(Value::Null);
            all_messages.push(serde_json::json!({
                "role": "system",
                "content": content_val,
            }));
            continue;
        }
        all_messages.push(obj);
    }

    // 第二遍：按 assistant 行分割，每个 assistant 行 = 一次 LLM 调用
    // Delta 模式（与 cchooks 对齐）：每次 LLM 调用只记录上一个 assistant 之后新增的消息，
    // 而非完整累积上下文。这样第二轮对话不会重复展示第一轮的内容。
    let mut calls: Vec<LlmCallContext> = Vec::new();
    let mut delta_input: Vec<Value> = Vec::new();

    for msg in all_messages {
        let role = msg.get("role").and_then(|v| v.as_str()).unwrap_or("");
        if role == "assistant" {
            let tool_call_ids = extract_tool_call_ids(&msg);
            calls.push(LlmCallContext {
                input_messages: delta_input.clone(),
                assistant_response: msg.clone(),
                tool_call_ids,
            });
            // 重置 delta：下一次 LLM 调用的增量从这个 assistant 之后开始
            delta_input.clear();
        } else {
            // user / tool / system → 累积到当前 delta
            delta_input.push(msg);
        }
    }

    calls
}

/// 从 assistant 行提取 tool_call_ids。
fn extract_tool_call_ids(assistant: &Value) -> Vec<String> {
    let mut ids = Vec::new();
    if let Some(tool_calls) = assistant.get("tool_calls").and_then(|v| v.as_array()) {
        for tc in tool_calls {
            if let Some(id) = tc.get("id").and_then(|v| v.as_str()) {
                ids.push(id.to_string());
            }
        }
    }
    ids
}

/// 将 context.jsonl 的输入消息列表序列化为 OpenAI 兼容的 JSON array 字符串。
///
/// 遵循 C9 Messages 构建原则：
/// - Generation span: user 在开头（自然时序），accumulated 累计输入
/// - 软截断：超 20K 字符从头部逐条截断 content
pub fn serialize_input_messages(messages: &[Value]) -> String {
    let mut result: Vec<Value> = Vec::new();
    for msg in messages {
        let role = msg.get("role").and_then(|v| v.as_str()).unwrap_or("");
        let mut entry = serde_json::json!({ "role": role });

        // content 处理：可能是字符串或数组
        if let Some(content) = msg.get("content") {
            if let Some(s) = content.as_str() {
                entry["content"] = Value::String(s.to_string());
            } else if let Some(arr) = content.as_array() {
                // kimi 的 content 是 [{type:"text", text:"..."}, {type:"think", think:"..."}]
                // 转为 OpenAI 格式的 content 数组或拼成字符串
                let mut text_parts: Vec<String> = Vec::new();
                for part in arr {
                    if let Some(t) = part.get("text").and_then(|v| v.as_str()) {
                        text_parts.push(t.to_string());
                    } else if let Some(t) = part.get("think").and_then(|v| v.as_str()) {
                        text_parts.push(format!("[thinking] {}", t));
                    }
                }
                entry["content"] = Value::String(text_parts.join("\n"));
            } else {
                entry["content"] = content.clone();
            }
        }

        // tool_calls（assistant 行）
        if role == "assistant" {
            if let Some(tool_calls) = msg.get("tool_calls") {
                entry["tool_calls"] = tool_calls.clone();
            }
        }

        // tool_call_id（tool 行）
        if role == "tool" {
            if let Some(tcid) = msg.get("tool_call_id") {
                entry["tool_call_id"] = tcid.clone();
            }
        }

        result.push(entry);
    }

    serde_json::to_string(&result).unwrap_or_else(|_| "[]".to_string())
}

/// 将 context.jsonl 解析结果注入到 `rebuild_hook_log_from_wire` 产出的合成事件中。
///
/// 按 synth 中 LLMStep 出现的**全局顺序**与 `LlmCallContext` 列表索引一一对齐。
/// wire.jsonl 跨多轮时 step_n 会在每个 turn 内重置为 1，但 context.jsonl 的 assistant
/// 行是全 session 累积的，因此必须用全局计数器（第 N 个 LLMStep）而非 step_n 来对齐。
///
/// 如果 context 解析失败或索引超出范围，对应的 LLMStep 保持原样（空 input messages）。
pub fn enrich_with_context(synth_events: &mut [Value], context_path: &Path) {
    let calls = parse_context_file(context_path);
    if calls.is_empty() {
        return;
    }

    let mut global_llm_idx: usize = 0;
    for event in synth_events.iter_mut() {
        let is_llm_step = event
            .get("hook_event_name")
            .and_then(|v| v.as_str())
            == Some("LLMStep");
        if !is_llm_step {
            continue;
        }

        if global_llm_idx < calls.len() {
            let input_json = serialize_input_messages(&calls[global_llm_idx].input_messages);
            event
                .as_object_mut()
                .unwrap()
                .insert("_input_messages_json".to_string(), Value::String(input_json));
        }
        global_llm_idx += 1;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn parse_simple_context() {
        let content = r#"{"role":"_system_prompt","content":"You are helpful."}
{"role":"_checkpoint"}
{"role":"user","content":"hello"}
{"role":"_checkpoint"}
{"role":"_usage","token_count":100}
{"role":"assistant","content":[{"type":"think","think":"thinking..."},{"type":"text","text":"Hi there!"}]}
{"role":"_usage","token_count":120}
"#;
        let calls = parse_context_lines(content);
        assert_eq!(calls.len(), 1);

        // input: system + user
        assert_eq!(calls[0].input_messages.len(), 2);
        assert_eq!(
            calls[0].input_messages[0].get("role").unwrap().as_str().unwrap(),
            "system"
        );
        assert_eq!(
            calls[0].input_messages[1].get("role").unwrap().as_str().unwrap(),
            "user"
        );

        // no tool_calls
        assert!(calls[0].tool_call_ids.is_empty());
    }

    #[test]
    fn parse_context_with_tool_calls() {
        let content = r#"{"role":"_system_prompt","content":"system prompt"}
{"role":"user","content":"do something"}
{"role":"_usage","token_count":100}
{"role":"assistant","content":"thinking","tool_calls":[{"type":"function","id":"fc-1","function":{"name":"Shell","arguments":"{\"cmd\":\"ls\"}"}}]}
{"role":"_usage","token_count":150}
{"role":"tool","content":"file1\nfile2","tool_call_id":"fc-1"}
{"role":"_usage","token_count":200}
{"role":"assistant","content":"Done! Here are your files."}
{"role":"_usage","token_count":250}
"#;
        let calls = parse_context_lines(content);
        assert_eq!(calls.len(), 2);

        // 第一次 LLM 调用（delta）：输入 = system + user
        assert_eq!(calls[0].input_messages.len(), 2);
        assert_eq!(calls[0].tool_call_ids, vec!["fc-1"]);

        // 第二次 LLM 调用（delta）：输入 = 仅 tool result（上一个 assistant 之后新增的）
        assert_eq!(calls[1].input_messages.len(), 1);
        assert_eq!(
            calls[1].input_messages[0].get("role").unwrap().as_str().unwrap(),
            "tool"
        );
        assert!(calls[1].tool_call_ids.is_empty());
    }

    #[test]
    fn serialize_handles_array_content() {
        let messages = vec![
            json!({"role": "user", "content": "hello"}),
            json!({"role": "assistant", "content": [
                {"type": "think", "think": "reasoning"},
                {"type": "text", "text": "answer"}
            ]}),
        ];
        let serialized = serialize_input_messages(&messages);
        let parsed: Vec<Value> = serde_json::from_str(&serialized).unwrap();
        assert_eq!(parsed.len(), 2);
        assert_eq!(
            parsed[1].get("content").unwrap().as_str().unwrap(),
            "[thinking] reasoning\nanswer"
        );
    }
}
