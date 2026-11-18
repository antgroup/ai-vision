use serde_json::Value;
use std::fs;

/// Extract the first user query from transcript
pub fn extract_first_query(transcript_path: &str) -> String {
    // Read transcript file
    let content = match fs::read_to_string(transcript_path) {
        Ok(c) => c,
        Err(_) => return String::new(),
    };

    // Parse JSONL file
    for line in content.lines() {
        if line.trim().is_empty() {
            continue;
        }

        let json: Value = match serde_json::from_str(line) {
            Ok(v) => v,
            Err(_) => continue,
        };

        // Find first user message
        if json.get("type").and_then(|v| v.as_str()) == Some("user") {
            if let Some(message) = json.get("message") {
                if let Some(content) = message.get("content") {
                    // Extract text from content array
                    if let Some(content_array) = content.as_array() {
                        for item in content_array {
                            if item.get("type").and_then(|v| v.as_str()) == Some("text") {
                                if let Some(text) = item.get("text").and_then(|v| v.as_str()) {
                                    return text.to_string();
                                }
                            }
                        }
                    }
                    // Or if content is a string directly
                    else if let Some(text) = content.as_str() {
                        return text.to_string();
                    }
                }
            }
        }
    }

    String::new()
}

/// Extract the last assistant text response from transcript.
/// Returns the text content of the final assistant message, or empty string.
pub fn extract_last_assistant_response(transcript_path: &str) -> String {
    let content = match fs::read_to_string(transcript_path) {
        Ok(c) => c,
        Err(_) => return String::new(),
    };

    let mut last_text = String::new();

    for line in content.lines() {
        if line.trim().is_empty() {
            continue;
        }

        let json: Value = match serde_json::from_str(line) {
            Ok(v) => v,
            Err(_) => continue,
        };

        if json.get("type").and_then(|v| v.as_str()) == Some("assistant") {
            if let Some(message) = json.get("message") {
                if let Some(content) = message.get("content") {
                    if let Some(content_array) = content.as_array() {
                        // Collect text blocks from content array
                        let mut texts = Vec::new();
                        for item in content_array {
                            if item.get("type").and_then(|v| v.as_str()) == Some("text") {
                                if let Some(text) = item.get("text").and_then(|v| v.as_str()) {
                                    texts.push(text.to_string());
                                }
                            }
                        }
                        if !texts.is_empty() {
                            last_text = texts.join("\n");
                        }
                    } else if let Some(text) = content.as_str() {
                        last_text = text.to_string();
                    }
                }
            }
        }
    }

    last_text
}
