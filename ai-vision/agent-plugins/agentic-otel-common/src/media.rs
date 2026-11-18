//! Typed media projection shared by binary Agent integrations.
//! No URL fetching or path reads: SigNoz is the persistence boundary.
use base64::Engine;
use serde_json::{json, Value};
pub const MAX_MEDIA_BYTES: usize = 16 * 1024 * 1024;

pub fn media_block(value: &Value) -> Option<Value> {
    let kind = value.get("type")?.as_str()?;
    if kind == "aivision_media_error"
        && matches!(
            value.get("reason").and_then(Value::as_str),
            Some("invalid" | "budget")
        )
    {
        return Some(json!({"type":kind,"reason":value["reason"]}));
    }
    if !matches!(
        kind,
        "image"
            | "audio"
            | "video"
            | "document"
            | "file"
            | "media"
            | "base64"
            | "text"
            | "image_url"
            | "input_image"
            | "audio_url"
            | "video_url"
            | "input_audio"
            | "input_file"
            | "resource"
    ) {
        return None;
    }
    let source = value
        .get("source")
        .or_else(|| value.get("resource"))
        .or_else(|| value.get("file"))
        .unwrap_or(value);
    let mut mime = ["media_type", "mime_type", "mimeType", "mime"]
        .iter()
        .find_map(|key| source.get(key).and_then(Value::as_str))
        .unwrap_or("");
    let mut data = ["data", "blob", "file_data"]
        .iter()
        .find_map(|key| source.get(key).and_then(Value::as_str));
    let mut url = ["url", "file_url", "image_url", "audio_url", "video_url"]
        .iter()
        .find_map(|key| {
            source
                .get(key)
                .or_else(|| value.get(key))
                .and_then(|v| v.as_str().or_else(|| v.get("url").and_then(Value::as_str)))
        });
    if kind == "input_audio" {
        let audio = value.get("input_audio")?;
        mime = match audio.get("format").and_then(Value::as_str)? {
            "wav" => "audio/wav",
            "mp3" => "audio/mpeg",
            "ogg" => "audio/ogg",
            "flac" => "audio/flac",
            _ => return None,
        };
        data = audio.get("data").and_then(Value::as_str);
    }
    if data.is_some_and(|d| d.starts_with("data:")) {
        url = data;
    }
    if let Some(u) = url.filter(|u| u.starts_with("data:")) {
        let (header, body) = u[5..].split_once(";base64,")?;
        if !mime.is_empty() && !mime.eq_ignore_ascii_case(header) {
            return Some(json!({"type":"aivision_media_error","reason":"invalid"}));
        }
        mime = header;
        data = Some(body);
    }
    let supported = mime.starts_with("image/")
        || mime.starts_with("audio/")
        || mime.starts_with("video/")
        || matches!(
            mime,
            "application/pdf" | "text/plain" | "text/markdown" | "text/x-markdown"
        );
    if !supported {
        if matches!(
            kind,
            "image_url" | "input_image" | "audio_url" | "video_url"
        ) {
            if let Some(url) = url.filter(|u| u.starts_with("https://") || u.starts_with("http://"))
            {
                let (media_kind, field) = match kind {
                    "audio_url" => ("audio_url", "audio_url"),
                    "video_url" => ("video_url", "video_url"),
                    _ => ("image_url", "image_url"),
                };
                return Some(json!({"type":media_kind,field:{"url":url}}));
            }
        }
        return None;
    }
    if let Some(encoded) = data {
        if encoded.len() > 4 * MAX_MEDIA_BYTES.div_ceil(3) {
            return Some(json!({"type":"aivision_media_error","reason":"budget"}));
        }
        match base64::engine::general_purpose::STANDARD.decode(encoded) {
            Ok(bytes) if !bytes.is_empty() && bytes.len() <= MAX_MEDIA_BYTES => Some(
                json!({"type":"media","source":{"type":"base64","media_type":mime,"data":encoded}}),
            ),
            Ok(bytes) if bytes.len() > MAX_MEDIA_BYTES => {
                Some(json!({"type":"aivision_media_error","reason":"budget"}))
            }
            _ => Some(json!({"type":"aivision_media_error","reason":"invalid"})),
        }
    } else if let Some(url) = url.filter(|u| u.starts_with("https://") || u.starts_with("http://"))
    {
        Some(json!({"type":"media","mime_type":mime,"url":url}))
    } else {
        None
    }
}

/// Preserve order and explicit types; unknown fields remain in the raw source.
pub fn content_blocks(content: &Value) -> Option<Vec<Value>> {
    let items = content.as_array()?;
    let mut found = false;
    let mut result = Vec::new();
    for item in items.iter().take(100) {
        if let Some(media) = media_block(item) {
            found = true;
            result.push(media);
        } else if matches!(
            item.get("type").and_then(Value::as_str),
            Some("text" | "input_text" | "output_text")
        ) {
            if let Some(text) = item.get("text").and_then(Value::as_str) {
                result.push(json!({"type":"text","text":crate::truncate_text(text).0}));
            }
        }
    }
    if found && items.len() > 100 {
        result.push(json!({"type":"aivision_media_error","reason":"budget"}));
    }
    found.then_some(result)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn error_descriptors_and_nested_sources_are_stable() {
        let error = json!({"type":"aivision_media_error","reason":"budget"});
        assert_eq!(media_block(&error), Some(error.clone()));
        assert_eq!(content_blocks(&json!([error.clone()])), Some(vec![error]));
        assert!(media_block(
            &json!({"type":"file","file":{"file_data":"data:application/pdf;base64,YWJj"}})
        )
        .is_some());
        let mut nested = json!({"type":"audio","mime_type":"audio/wav","data":"YWJj"});
        for _ in 0..18 {
            nested = json!({"content":nested});
        }
        assert!(project_tool_media(&nested).is_none());
    }

    #[test]
    fn preserves_all_media_and_order_without_guessing() {
        for mime in [
            "image/avif",
            "audio/ogg",
            "video/mp4",
            "application/pdf",
            "text/markdown",
        ] {
            let raw = json!([{"type":"text","text":"before"},{"type":"media","mime_type":mime,"data":"YWJj"},{"type":"text","text":"after"}]);
            let result = content_blocks(&raw).unwrap();
            assert_eq!(result[1]["source"]["data"], "YWJj");
            assert_eq!(result[1]["source"]["media_type"], mime);
            assert_eq!(result[2]["text"], "after");
        }
        assert!(media_block(&json!({"data":"YWJj"})).is_none());
        assert!(
            media_block(&json!({"type":"file","mime_type":"audio/wav","url":"/tmp/a.wav"}))
                .is_none()
        );
    }
}

/// Count only validated typed binary payload characters, with bounded recursion.
/// This allowance is added to text budgets only during transport to SigNoz.
pub fn transport_chars(value: &Value) -> usize {
    fn visit(value: &Value, depth: usize) -> usize {
        if depth > 16 {
            return 0;
        }
        if let Some(block) = media_block(value) {
            return block
                .pointer("/source/data")
                .and_then(Value::as_str)
                .map_or(0, str::len);
        }
        match value {
            Value::Array(items) => items.iter().take(100).map(|v| visit(v, depth + 1)).sum(),
            Value::Object(fields) => [
                "messages", "content", "parts", "input", "output", "data", "kwargs",
            ]
            .iter()
            .filter_map(|key| fields.get(*key))
            .map(|v| visit(v, depth + 1))
            .sum(),
            _ => 0,
        }
    }
    visit(value, 0)
}

/// Project recognized tool payload containers without retaining arbitrary fields.
pub fn project_tool_media(value: &Value) -> Option<Value> {
    fn visit(value: &Value, depth: usize) -> Option<Value> {
        if depth > 16 {
            return None;
        }
        if let Some(blocks) = content_blocks(value) {
            return Some(Value::Array(blocks));
        }
        if let Some(block) = media_block(value) {
            return Some(block);
        }
        let object = value.as_object()?;
        for key in ["content", "parts", "output", "data"] {
            if let Some(nested) = object.get(key).and_then(|v| visit(v, depth + 1)) {
                return Some(json!({key:nested}));
            }
        }
        None
    }
    visit(value, 0)
}
