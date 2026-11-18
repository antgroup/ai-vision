//! Export-boundary payload hygiene and per-span size budgeting.
//!
//! The host transcript remains untouched. These helpers operate only on the
//! semantic projection that is about to be attached to an OTLP span.

use agentic_otel_common::otlp::{OtelAnyValue, OtelAttribute, OtelSpan};
use serde::Serialize;
use serde_json::Value;
use std::borrow::Cow;
use std::collections::HashSet;
use std::io::{self, Write};

/// Maximum serialized size of a bare [`OtelSpan`], excluding OTLP resource and
/// scope wrappers.
pub const MAX_SERIALIZED_SPAN_BYTES: usize = 4 * 1024 * 1024;

const TRUNCATED_ATTR: &str = "agentic.span.truncated";
const ORIGINAL_LENGTH_ATTR: &str = "agentic.span.original_length";
const SIZE_LIMIT_ATTR: &str = "agentic.span.size_limit";
const TRUNCATION_REASON_ATTR: &str = "agentic.span.truncation.reason";
const TRUNCATION_REASON: &str = "size_budget";
const TRUNCATED_VALUE: &str = "[TRUNCATED_TO_SPAN_BUDGET]";
const MAX_PROTECTED_STRING_BYTES: usize = 1024;
const MAX_MEDIA_TRANSPORT_BYTES: usize = 56 * 1024 * 1024;

// Media bytes have an independent 16 MiB/file limit; ordinary span text retains
// its 4 MiB budget. The bounded extra transport space disappears in SigNoz.
fn media_transport_allowance(span: &Value) -> usize {
    span.get("attributes").and_then(Value::as_array).into_iter().flatten()
        .filter(|a| a["key"].as_str().is_some_and(is_content_attribute))
        .filter_map(|a| a.pointer("/value/stringValue").and_then(Value::as_str))
        .filter_map(|s| serde_json::from_str::<Value>(s).ok())
        .map(|v| agentic_otel_common::media::transport_chars(&v))
        .fold(0usize, usize::saturating_add)
        .min(MAX_MEDIA_TRANSPORT_BYTES)
}

fn bound_media_transport(span: &mut OtelSpan) -> usize {
    fn visit(value: &mut Value, remaining: &mut usize, depth: usize) -> bool {
        if depth > 16 {
            return false;
        }
        if let Some(block) = agentic_otel_common::media::media_block(value) {
            let chars = block
                .pointer("/source/data")
                .and_then(Value::as_str)
                .map_or(0, str::len);
            if chars > *remaining {
                *value = serde_json::json!({"type":"aivision_media_error","reason":"budget"});
                return true;
            }
            *remaining -= chars;
            if block["type"] == "aivision_media_error" && *value != block {
                *value = block;
                return true;
            }
            return false;
        }
        let mut changed = false;
        match value {
            Value::Array(items) => {
                for item in items.iter_mut().take(100) {
                    changed |= visit(item, remaining, depth + 1);
                }
            }
            Value::Object(fields) => {
                for key in ["messages", "content", "parts", "input", "output", "data", "kwargs"] {
                    if let Some(item) = fields.get_mut(key) {
                        changed |= visit(item, remaining, depth + 1);
                    }
                }
            }
            _ => {}
        }
        changed
    }
    let mut remaining = MAX_MEDIA_TRANSPORT_BYTES;
    let mut replaced = 0;
    for attribute in &mut span.attributes {
        if !is_content_attribute(&attribute.key) {
            continue;
        }
        let OtelAnyValue::String { string_value } = &mut attribute.value else {
            continue;
        };
        if let Ok(mut value) = serde_json::from_str::<Value>(string_value) {
            if visit(&mut value, &mut remaining, 0) {
                *string_value = value.to_string();
                replaced += 1;
            }
        }
    }
    replaced
}

/// Aggregate evidence produced while removing inline image payloads.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct InlineImageFilterStats {
    /// Number of `data:image/*;base64,` values replaced.
    pub filtered_count: usize,
    /// Total number of Base64 payload characters omitted.
    pub original_chars: usize,
}

impl InlineImageFilterStats {
    pub(crate) fn add(&mut self, other: Self) {
        self.filtered_count = self.filtered_count.saturating_add(other.filtered_count);
        self.original_chars = self.original_chars.saturating_add(other.original_chars);
    }
}

/// Result of applying the serialized bare-span budget.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct SpanBudgetOutcome {
    pub truncated: bool,
    pub original_length: usize,
    pub final_length: usize,
    pub replaced_values: usize,
    pub removed_attributes: usize,
    pub within_budget: bool,
}

/// Reject an OTLP trace export body containing any bare span over the hard
/// serialized-size budget. This is the final fail-closed guard for paths that
/// assemble or enrich spans after typed budget enforcement.
pub fn validate_export_body_span_budget(body: &Value) -> Result<(), String> {
    let resources = body
        .get("resourceSpans")
        .and_then(Value::as_array)
        .ok_or("OTLP trace export has no resourceSpans array")?;
    for resource in resources {
        let scopes = resource
            .get("scopeSpans")
            .and_then(Value::as_array)
            .ok_or("OTLP trace resource has no scopeSpans array")?;
        for scope in scopes {
            let spans = scope
                .get("spans")
                .and_then(Value::as_array)
                .ok_or("OTLP trace scope has no spans array")?;
            for span in spans {
                let serialized_length = serde_json::to_vec(span)
                    .map_err(|error| format!("failed to serialize OTLP span: {error}"))?
                    .len();
                if serialized_length > MAX_SERIALIZED_SPAN_BYTES + media_transport_allowance(span) {
                    let span_id = span
                        .get("spanId")
                        .and_then(Value::as_str)
                        .unwrap_or("unknown");
                    return Err(format!(
                        "otel_span_oversize: span_id={span_id} bare_span_bytes={serialized_length} limit_bytes={MAX_SERIALIZED_SPAN_BYTES}"
                    ));
                }
            }
        }
    }
    Ok(())
}

/// Recursively replace inline Base64 image Data URLs with deterministic,
/// non-sensitive placeholders.
///
/// Ordinary Base64 strings, non-image Data URLs, and remote image URLs are
/// deliberately preserved. Matching of the Data URL header is ASCII case
/// insensitive; the MIME value in the placeholder is normalized to lowercase.
pub fn filter_inline_base64_images(value: &Value) -> (Cow<'_, Value>, InlineImageFilterStats) {
    if agentic_otel_common::media::media_block(value).is_some() || agentic_otel_common::media::content_blocks(value).is_some() {
        return (Cow::Borrowed(value), InlineImageFilterStats::default());
    }
    if !contains_inline_base64_image(value) {
        return (Cow::Borrowed(value), InlineImageFilterStats::default());
    }

    let mut filtered = value.clone();
    let mut stats = InlineImageFilterStats::default();
    filter_inline_base64_images_in_place(&mut filtered, &mut stats);
    (Cow::Owned(filtered), stats)
}

fn contains_inline_base64_image(value: &Value) -> bool {
    match value {
        Value::String(text) => inline_image_placeholder(text).is_some(),
        Value::Array(items) => items.iter().any(contains_inline_base64_image),
        Value::Object(fields) => fields.values().any(contains_inline_base64_image),
        _ => false,
    }
}

fn filter_inline_base64_images_in_place(value: &mut Value, stats: &mut InlineImageFilterStats) {
    if agentic_otel_common::media::media_block(value).is_some() { return; }
    match value {
        Value::String(text) => {
            if let Some((placeholder, encoded_chars)) = inline_image_placeholder(text) {
                *text = placeholder;
                stats.add(InlineImageFilterStats {
                    filtered_count: 1,
                    original_chars: encoded_chars,
                });
            }
        }
        Value::Array(items) => {
            for item in items {
                filter_inline_base64_images_in_place(item, stats);
            }
        }
        Value::Object(fields) => {
            for item in fields.values_mut() {
                filter_inline_base64_images_in_place(item, stats);
            }
        }
        _ => {}
    }
}

fn inline_image_placeholder(text: &str) -> Option<(String, usize)> {
    let prefix = text.get(.."data:".len())?;
    if !prefix.eq_ignore_ascii_case("data:") {
        return None;
    }
    let comma = text.find(',')?;
    let media_and_encoding = text.get("data:".len()..comma)?;
    let payload = &text[comma + 1..];
    let encoding_start = media_and_encoding.len().checked_sub(";base64".len())?;
    let encoding = media_and_encoding.get(encoding_start..)?;
    if !encoding.eq_ignore_ascii_case(";base64") {
        return None;
    }
    let mime = media_and_encoding.get(..encoding_start)?;
    let image_prefix = mime.get(.."image/".len())?;
    if !image_prefix.eq_ignore_ascii_case("image/") || mime.len() <= "image/".len() {
        return None;
    }
    let encoded_chars = payload.chars().count();
    let normalized_mime = mime.to_ascii_lowercase();

    Some((
        format!("[FILTERED_INLINE_IMAGE mime={normalized_mime} encoded_chars={encoded_chars}]"),
        encoded_chars,
    ))
}

/// Reduce a serialized bare span toward [`MAX_SERIALIZED_SPAN_BYTES`].
///
/// Duplicate attributes are canonicalized first using first-wins semantics.
/// Content-bearing values are then reduced largest first, followed by other
/// oversized values. Attribute keys are retained; their values are bounded only
/// as a last resort. Ordering is deterministic for equal-size inputs. Callers
/// must honor `within_budget`; the export boundary independently validates and
/// rejects any mathematically irreducible oversized span.
pub fn enforce_span_budget(span: &mut OtelSpan) -> SpanBudgetOutcome {
    let original_length = serialized_len(span);
    let media_replaced = if original_length > MAX_SERIALIZED_SPAN_BYTES {
        bound_media_transport(span)
    } else {
        0
    };
    let size_limit = MAX_SERIALIZED_SPAN_BYTES + media_transport_allowance(&serde_json::to_value(&*span).unwrap_or(Value::Null));
    if media_replaced > 0 {
        upsert_budget_metadata(span, original_length);
    }
    let bounded_length = if media_replaced > 0 {
        serialized_len(span)
    } else {
        original_length
    };
    if bounded_length <= size_limit {
        return SpanBudgetOutcome {
            truncated: media_replaced > 0,
            original_length,
            final_length: bounded_length,
            replaced_values: media_replaced,
            removed_attributes: 0,
            within_budget: true,
        };
    }

    let attribute_count = span.attributes.len();
    let mut seen_keys = HashSet::with_capacity(attribute_count);
    span.attributes
        .retain(|attribute| seen_keys.insert(attribute.key.clone()));
    let removed_attributes = attribute_count.saturating_sub(span.attributes.len());

    upsert_budget_metadata(span, original_length);
    let mut replaced_values = media_replaced;
    let mut current_length = serialized_len(span);

    let mut content_indices: Vec<(usize, usize)> = span
        .attributes
        .iter()
        .enumerate()
        .filter_map(|(index, attribute)| {
            is_content_attribute(&attribute.key).then_some((index, attribute_len(attribute)))
        })
        .collect();
    content_indices.sort_by(|(left_index, left_len), (right_index, right_len)| {
        right_len
            .cmp(left_len)
            .then_with(|| {
                span.attributes[*left_index]
                    .key
                    .cmp(&span.attributes[*right_index].key)
            })
            .then_with(|| left_index.cmp(right_index))
    });
    for (index, _) in content_indices {
        if current_length <= size_limit {
            break;
        }
        let excess = current_length.saturating_sub(size_limit);
        let value_len = serialized_len_value(&span.attributes[index].value);
        let target_len = value_len
            .saturating_sub(excess.saturating_add(256))
            .max(128);
        if reduce_content_value(&mut span.attributes[index], target_len) {
            replaced_values += 1;
            current_length = serialized_len(span);
        }
    }

    if current_length > size_limit {
        let mut value_indices = span
            .attributes
            .iter()
            .enumerate()
            .filter_map(|(index, attribute)| {
                (!is_budget_metadata(&attribute.key)
                    && !is_content_attribute(&attribute.key)
                    && serialized_len_value(&attribute.value) > MAX_PROTECTED_STRING_BYTES)
                    .then_some((index, serialized_len_value(&attribute.value)))
            })
            .collect::<Vec<_>>();
        value_indices
            .sort_by(|left, right| right.1.cmp(&left.1).then_with(|| left.0.cmp(&right.0)));
        for (index, _) in value_indices {
            if current_length <= size_limit {
                break;
            }
            let old_value_len = serialized_len_value(&span.attributes[index].value);
            if truncate_any_value(
                &mut span.attributes[index].value,
                MAX_PROTECTED_STRING_BYTES,
            ) {
                replaced_values += 1;
                let new_value_len = serialized_len_value(&span.attributes[index].value);
                current_length = current_length
                    .saturating_sub(old_value_len)
                    .saturating_add(new_value_len);
            }
        }
    }

    if current_length > size_limit {
        let mut value_indices = span
            .attributes
            .iter()
            .enumerate()
            .filter_map(|(index, attribute)| {
                (!is_budget_metadata(&attribute.key)
                    && !is_content_attribute(&attribute.key)
                    && can_reduce_any_value(&attribute.value, 64))
                .then_some((index, serialized_len_value(&attribute.value)))
            })
            .collect::<Vec<_>>();
        value_indices
            .sort_by(|left, right| right.1.cmp(&left.1).then_with(|| left.0.cmp(&right.0)));
        for (index, old_value_len) in value_indices {
            if current_length <= size_limit {
                break;
            }
            if truncate_any_value(&mut span.attributes[index].value, 64) {
                replaced_values += 1;
                let new_value_len = serialized_len_value(&span.attributes[index].value);
                current_length = current_length
                    .saturating_sub(old_value_len)
                    .saturating_add(new_value_len);
            }
        }
    }

    if current_length > size_limit {
        span.name = truncate_utf8(&span.name, MAX_PROTECTED_STRING_BYTES);
        current_length = serialized_len(span);
    }
    if current_length > size_limit {
        if let Some(message) = span.status.message.as_mut() {
            *message = truncate_utf8(message, MAX_PROTECTED_STRING_BYTES);
        }
    }

    let final_length = serialized_len(span);
    SpanBudgetOutcome {
        truncated: true,
        original_length,
        final_length,
        replaced_values,
        removed_attributes,
        within_budget: final_length <= size_limit,
    }
}

fn serialized_len(span: &OtelSpan) -> usize {
    serde_json::to_vec(span).map_or(usize::MAX, |bytes| bytes.len())
}

fn attribute_len(attribute: &OtelAttribute) -> usize {
    serde_json::to_vec(attribute).map_or(usize::MAX, |bytes| bytes.len())
}

fn serialized_len_value(value: &OtelAnyValue) -> usize {
    serde_json::to_vec(value).map_or(usize::MAX, |bytes| bytes.len())
}

fn is_content_attribute(key: &str) -> bool {
    matches!(
        key,
        "gen_ai.input.messages"
            | "gen_ai.output.messages"
            | "gen_ai.system_instructions"
            | "agentic.input.delta"
            | "gen_ai.tool.call.arguments"
            | "gen_ai.tool.call.result"
    )
}

fn is_budget_metadata(key: &str) -> bool {
    matches!(
        key,
        TRUNCATED_ATTR | ORIGINAL_LENGTH_ATTR | SIZE_LIMIT_ATTR | TRUNCATION_REASON_ATTR
    )
}

fn reduce_content_value(attribute: &mut OtelAttribute, target_value_bytes: usize) -> bool {
    let OtelAnyValue::String { string_value } = &mut attribute.value else {
        return truncate_any_value(&mut attribute.value, target_value_bytes);
    };
    let replacement = if matches!(
        attribute.key.as_str(),
        "gen_ai.input.messages"
            | "gen_ai.output.messages"
            | "gen_ai.system_instructions"
            | "agentic.input.delta"
    ) {
        reduce_message_json_to_budget(string_value, target_value_bytes, &attribute.key)
    } else {
        TRUNCATED_VALUE.to_string()
    };
    if replacement == *string_value {
        return false;
    }
    *string_value = replacement;
    true
}

fn reduce_message_json_to_budget(input: &str, target_value_bytes: usize, key: &str) -> String {
    let marker_role = if key == "gen_ai.output.messages" {
        "assistant"
    } else {
        "system"
    };
    let mut messages = serde_json::from_str::<Value>(input).unwrap_or_else(|_| {
        serde_json::json!([{
            "role": marker_role,
            "content": TRUNCATED_VALUE
        }])
    });
    if !messages.is_array() {
        messages = serde_json::json!([{
            "role": marker_role,
            "content": TRUNCATED_VALUE
        }]);
    }

    let initial_message_count = messages.as_array().map_or(0, Vec::len);
    let mut omitted_messages = 0usize;
    let mut rendered = serde_json::to_string(&messages).unwrap_or_else(|_| "[]".to_string());
    let mut rendered_value_len = serialized_string_value_len(&rendered);
    while rendered_value_len > target_value_bytes {
        let largest = largest_message_payload_len(&messages);
        if largest == 0 {
            break;
        }
        let excess = rendered_value_len.saturating_sub(target_value_bytes);
        let target = largest
            .saturating_sub(excess.saturating_add(128))
            .max(TRUNCATED_VALUE.len());
        if !truncate_largest_message_payload(&mut messages, largest, target) {
            break;
        }
        let next = serde_json::to_string(&messages).unwrap_or_else(|_| "[]".to_string());
        if next.len() >= rendered.len() {
            break;
        }
        rendered = next;
        rendered_value_len = serialized_string_value_len(&rendered);
    }

    while rendered_value_len > target_value_bytes {
        let Some(items) = messages.as_array_mut() else {
            break;
        };
        let marker_count = usize::from(omitted_messages > 0);
        let remaining_messages = items.len().saturating_sub(marker_count);
        if remaining_messages <= 1 {
            omitted_messages = omitted_messages.saturating_add(remaining_messages);
            *items = vec![truncated_messages_marker(marker_role, omitted_messages)];
        } else {
            let remove_count = (remaining_messages / 2).max(1);
            if marker_count > 0 {
                items.remove(0);
            }
            items.drain(..remove_count);
            omitted_messages = omitted_messages.saturating_add(remove_count);
            items.insert(0, truncated_messages_marker(marker_role, omitted_messages));
        }
        let next = serde_json::to_string(&messages).unwrap_or_else(|_| "[]".to_string());
        if next == rendered {
            break;
        }
        rendered = next;
        rendered_value_len = serialized_string_value_len(&rendered);
    }
    debug_assert!(omitted_messages <= initial_message_count);
    rendered
}

fn truncated_messages_marker(role: &str, omitted_messages: usize) -> Value {
    serde_json::json!({
        "role": role,
        "content": format!(
            "[TRUNCATED_MESSAGES_TO_SPAN_BUDGET omitted={omitted_messages}]"
        )
    })
}

fn serialized_string_value_len(value: &str) -> usize {
    #[derive(Serialize)]
    struct BorrowedStringValue<'a> {
        #[serde(rename = "stringValue")]
        string_value: &'a str,
    }

    let mut counter = ByteCounter::default();
    serde_json::to_writer(
        &mut counter,
        &BorrowedStringValue {
            string_value: value,
        },
    )
    .map_or(usize::MAX, |()| counter.bytes)
}

#[derive(Default)]
struct ByteCounter {
    bytes: usize,
}

impl Write for ByteCounter {
    fn write(&mut self, buffer: &[u8]) -> io::Result<usize> {
        self.bytes = self.bytes.saturating_add(buffer.len());
        Ok(buffer.len())
    }

    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

fn largest_message_payload_len(value: &Value) -> usize {
    match value {
        Value::Array(items) => items
            .iter()
            .map(largest_message_payload_len)
            .max()
            .unwrap_or(0),
        Value::Object(fields) => fields
            .iter()
            .map(|(key, value)| {
                if is_message_payload_key(key) {
                    value.as_str().map(str::len).unwrap_or(0)
                } else {
                    largest_message_payload_len(value)
                }
            })
            .max()
            .unwrap_or(0),
        _ => 0,
    }
}

fn truncate_largest_message_payload(
    value: &mut Value,
    largest_len: usize,
    target_bytes: usize,
) -> bool {
    match value {
        Value::Array(items) => items
            .iter_mut()
            .any(|item| truncate_largest_message_payload(item, largest_len, target_bytes)),
        Value::Object(fields) => {
            for (key, value) in fields {
                if is_message_payload_key(key) {
                    if let Some(text) = value.as_str() {
                        if text.len() == largest_len {
                            *value = Value::String(truncate_utf8_with_marker(text, target_bytes));
                            return true;
                        }
                    }
                }
                if truncate_largest_message_payload(value, largest_len, target_bytes) {
                    return true;
                }
            }
            false
        }
        _ => false,
    }
}

fn is_message_payload_key(key: &str) -> bool {
    matches!(key, "content" | "reasoning_content" | "arguments")
}

fn truncate_any_value(value: &mut OtelAnyValue, max_bytes: usize) -> bool {
    match value {
        OtelAnyValue::String { string_value } if string_value.len() > max_bytes => {
            *string_value = truncate_utf8_with_marker(string_value, max_bytes);
            true
        }
        OtelAnyValue::Array { array_value }
            if serde_json::to_vec(array_value).map_or(usize::MAX, |bytes| bytes.len())
                > max_bytes =>
        {
            array_value.values.clear();
            true
        }
        _ => false,
    }
}

fn can_reduce_any_value(value: &OtelAnyValue, max_bytes: usize) -> bool {
    match value {
        OtelAnyValue::String { string_value } => string_value.len() > max_bytes,
        OtelAnyValue::Array { array_value } => !array_value.values.is_empty(),
        _ => false,
    }
}

fn truncate_utf8_with_marker(value: &str, max_bytes: usize) -> String {
    let marker = format!(
        "...[TRUNCATED_TO_SPAN_BUDGET original_bytes={}]",
        value.len()
    );
    if max_bytes <= marker.len() {
        return TRUNCATED_VALUE.to_string();
    }
    let prefix_limit = max_bytes - marker.len();
    format!("{}{}", truncate_utf8(value, prefix_limit), marker)
}

fn truncate_utf8(value: &str, max_bytes: usize) -> String {
    if value.len() <= max_bytes {
        return value.to_string();
    }
    let mut end = max_bytes;
    while !value.is_char_boundary(end) {
        end -= 1;
    }
    value[..end].to_string()
}

fn upsert_budget_metadata(span: &mut OtelSpan, original_length: usize) {
    upsert_string_attribute(span, TRUNCATED_ATTR, "true");
    upsert_string_attribute(span, ORIGINAL_LENGTH_ATTR, &original_length.to_string());
    upsert_string_attribute(
        span,
        SIZE_LIMIT_ATTR,
        &MAX_SERIALIZED_SPAN_BYTES.to_string(),
    );
    upsert_string_attribute(span, TRUNCATION_REASON_ATTR, TRUNCATION_REASON);
}

fn upsert_string_attribute(span: &mut OtelSpan, key: &str, value: &str) {
    if let Some(attribute) = span
        .attributes
        .iter_mut()
        .find(|attribute| attribute.key == key)
    {
        attribute.value = OtelAnyValue::String {
            string_value: value.to_string(),
        };
    } else {
        span.attributes.push(OtelAttribute::string(key, value));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use agentic_otel_common::otlp::{OtelStatus, SPAN_KIND_INTERNAL};
    use serde_json::json;
    use std::borrow::Cow;

    fn test_span(attributes: Vec<OtelAttribute>) -> OtelSpan {
        OtelSpan {
            trace_id: "0123456789abcdef0123456789abcdef".to_string(),
            span_id: "0123456789abcdef".to_string(),
            parent_span_id: "fedcba9876543210".to_string(),
            name: "chat test-model".to_string(),
            kind: SPAN_KIND_INTERNAL,
            start_time_unix_nano: "1".to_string(),
            end_time_unix_nano: "2".to_string(),
            attributes,
            status: OtelStatus::ok(),
        }
    }

    fn attr_string<'a>(span: &'a OtelSpan, key: &str) -> Option<&'a str> {
        span.attributes.iter().find_map(|attribute| {
            if attribute.key != key {
                return None;
            }
            match &attribute.value {
                OtelAnyValue::String { string_value } => Some(string_value.as_str()),
                _ => None,
            }
        })
    }

    #[test]
    fn preserves_typed_media_and_filters_untyped_inline_images() {
        let value = json!({
            "output": [
                {"type": "input_image", "image_url": "data:image/png;base64,AAAA"},
                {"nested": {"url": "DATA:IMAGE/JPEG;BASE64,123456"}}
            ]
        });

        let (filtered, stats) = filter_inline_base64_images(&value);

        assert_eq!(stats.filtered_count, 1);
        assert_eq!(stats.original_chars, 6);
        assert_eq!(
            filtered["output"][0]["image_url"],
            "data:image/png;base64,AAAA"
        );
        assert_eq!(
            filtered["output"][1]["nested"]["url"],
            "[FILTERED_INLINE_IMAGE mime=image/jpeg encoded_chars=6]"
        );
    }

    #[test]
    fn preserves_remote_non_image_and_plain_base64_values() {
        let long_non_data_header = format!("{}{},payload", "x".repeat(1024 * 1024), "header");
        let value = json!([
            "https://example.test/image.png",
            "data:text/plain;base64,AAAA",
            "AAAA",
            "data:image/png,not-base64",
            "prefix data:image/png;base64,AAAA",
            long_non_data_header
        ]);

        let (filtered, stats) = filter_inline_base64_images(&value);

        assert_eq!(filtered.as_ref(), &value);
        assert!(matches!(filtered, Cow::Borrowed(_)));
        assert_eq!(stats, InlineImageFilterStats::default());
    }

    #[test]
    fn filters_empty_and_svg_image_payloads() {
        let value = json!([
            "data:image/svg+xml;base64,PHN2Zz4=",
            "data:image/gif;base64,"
        ]);

        let (filtered, stats) = filter_inline_base64_images(&value);

        assert_eq!(stats.filtered_count, 2);
        assert_eq!(stats.original_chars, 8);
        assert_eq!(
            filtered[0],
            "[FILTERED_INLINE_IMAGE mime=image/svg+xml encoded_chars=8]"
        );
        assert_eq!(
            filtered[1],
            "[FILTERED_INLINE_IMAGE mime=image/gif encoded_chars=0]"
        );
    }

    #[test]
    fn span_within_budget_is_unchanged() {
        let mut span = test_span(vec![OtelAttribute::string("gen_ai.operation.name", "chat")]);
        let before = serde_json::to_value(&span).unwrap();

        let outcome = enforce_span_budget(&mut span);

        assert!(!outcome.truncated);
        assert!(outcome.within_budget);
        assert_eq!(serde_json::to_value(&span).unwrap(), before);
    }

    #[test]
    fn oversized_messages_preserve_openai_roles_and_tool_call_links() {
        let large = "x".repeat(MAX_SERIALIZED_SPAN_BYTES + 4096);
        let messages = json!([
            {"role": "user", "content": large},
            {
                "role": "assistant",
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "lookup", "arguments": "{\"q\":\"x\"}"}
                }]
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "ok"}
        ]);
        let mut span = test_span(vec![
            OtelAttribute::string("gen_ai.operation.name", "chat"),
            OtelAttribute::string("gen_ai.conversation.id", "conversation"),
            OtelAttribute::string("gen_ai.chat.id", "chat"),
            OtelAttribute::string("gen_ai.span.kind", "LLM"),
            OtelAttribute::string("gen_ai.input.messages", messages.to_string()),
        ]);

        let outcome = enforce_span_budget(&mut span);

        assert!(outcome.truncated);
        assert!(outcome.within_budget);
        assert!(outcome.final_length <= MAX_SERIALIZED_SPAN_BYTES);
        let reduced: Value =
            serde_json::from_str(attr_string(&span, "gen_ai.input.messages").unwrap()).unwrap();
        let reduced = reduced.as_array().unwrap();
        assert_eq!(reduced.len(), 3);
        assert_eq!(reduced[0]["role"], "user");
        assert!(reduced[0]["content"]
            .as_str()
            .unwrap()
            .contains("TRUNCATED_TO_SPAN_BUDGET"));
        assert_eq!(reduced[1]["role"], "assistant");
        assert_eq!(reduced[1]["tool_calls"][0]["id"], "call_1");
        assert_eq!(reduced[1]["tool_calls"][0]["type"], "function");
        assert_eq!(reduced[1]["tool_calls"][0]["function"]["name"], "lookup");
        assert_eq!(
            reduced[1]["tool_calls"][0]["function"]["arguments"],
            "{\"q\":\"x\"}"
        );
        assert_eq!(reduced[2]["role"], "tool");
        assert_eq!(reduced[2]["tool_call_id"], "call_1");
        assert_eq!(attr_string(&span, "gen_ai.operation.name"), Some("chat"));
        assert_eq!(
            attr_string(&span, "gen_ai.conversation.id"),
            Some("conversation")
        );
        assert_eq!(attr_string(&span, TRUNCATED_ATTR), Some("true"));
        let expected_size_limit = MAX_SERIALIZED_SPAN_BYTES.to_string();
        assert_eq!(
            attr_string(&span, SIZE_LIMIT_ATTR),
            Some(expected_size_limit.as_str())
        );
        assert_eq!(
            attr_string(&span, TRUNCATION_REASON_ATTR),
            Some("size_budget")
        );
    }

    #[test]
    fn minimum_payload_truncation_makes_progress_before_message_pruning() {
        let messages = json!([{
            "role": "user",
            "content": "x".repeat(64)
        }]);

        let reduced =
            reduce_message_json_to_budget(&messages.to_string(), 100, "gen_ai.input.messages");
        let reduced: Value = serde_json::from_str(&reduced).unwrap();

        assert!(matches!(
            reduced[0]["role"].as_str(),
            Some("user" | "system")
        ));
        assert!(reduced[0]["content"]
            .as_str()
            .unwrap()
            .contains("TRUNCATED_TO_SPAN_BUDGET"));
    }

    #[test]
    fn message_pruning_reports_cumulative_omitted_count() {
        let messages = Value::Array(
            (0..8)
                .map(|index| {
                    json!({
                        "role": "user",
                        "metadata": format!("{index}:{}", "x".repeat(200))
                    })
                })
                .collect(),
        );

        let reduced =
            reduce_message_json_to_budget(&messages.to_string(), 400, "gen_ai.input.messages");
        let reduced: Value = serde_json::from_str(&reduced).unwrap();
        let reduced = reduced.as_array().unwrap();
        let remaining_messages = reduced.len().saturating_sub(1);
        let expected_omitted = 8 - remaining_messages;

        assert!(
            expected_omitted > 4,
            "test must exercise multiple prune rounds"
        );
        assert_eq!(
            reduced[0]["content"],
            format!("[TRUNCATED_MESSAGES_TO_SPAN_BUDGET omitted={expected_omitted}]")
        );
    }

    #[test]
    fn borrowed_string_length_matches_otel_json_serialization() {
        for value in ["plain", "quote: \"", "slash: \\n", "你好🙂"] {
            let expected = serialized_len_value(&OtelAnyValue::String {
                string_value: value.to_string(),
            });
            assert_eq!(serialized_string_value_len(value), expected);
        }
    }

    #[test]
    fn oversized_span_preserves_semantic_attribute_keys() {
        let mut span = test_span(vec![
            OtelAttribute::string("gen_ai.operation.name", "chat"),
            OtelAttribute::string("gen_ai.span.kind", "LLM"),
            OtelAttribute::string("ant.username", "tester"),
            OtelAttribute::int_as_string("gen_ai.usage.input_tokens", 10),
            OtelAttribute::int_as_string("gen_ai.usage.output_tokens", 5),
            OtelAttribute::int_as_string("gen_ai.usage.total_tokens", 15),
            OtelAttribute::int_as_string("agentic.llm.duration_ms", 42),
            OtelAttribute::int_as_string("agentic.tool.duration_ms", 21),
            OtelAttribute::string("error.type", "tool_error"),
            OtelAttribute::string("error.message", "failed"),
        ]);
        span.name = "n".repeat(MAX_SERIALIZED_SPAN_BYTES + 4096);

        let outcome = enforce_span_budget(&mut span);

        assert!(outcome.within_budget);
        assert_eq!(outcome.removed_attributes, 0);
        for key in [
            "gen_ai.operation.name",
            "gen_ai.span.kind",
            "ant.username",
            "gen_ai.usage.input_tokens",
            "gen_ai.usage.output_tokens",
            "gen_ai.usage.total_tokens",
            "agentic.llm.duration_ms",
            "agentic.tool.duration_ms",
            "error.type",
            "error.message",
        ] {
            assert!(
                span.attributes.iter().any(|attribute| attribute.key == key),
                "missing semantic attribute {key}"
            );
        }
    }

    #[test]
    fn oversized_system_instructions_remain_valid_openai_json() {
        let instructions = json!([{
            "role": "system",
            "content": "s".repeat(MAX_SERIALIZED_SPAN_BYTES + 4096)
        }]);
        let mut span = test_span(vec![
            OtelAttribute::string("gen_ai.operation.name", "chat"),
            OtelAttribute::string("gen_ai.span.kind", "LLM"),
            OtelAttribute::string("ant.username", "tester"),
            OtelAttribute::string("gen_ai.system_instructions", instructions.to_string()),
        ]);

        let outcome = enforce_span_budget(&mut span);

        assert!(outcome.within_budget);
        let reduced: Value = serde_json::from_str(
            attr_string(&span, "gen_ai.system_instructions").expect("system instructions kept"),
        )
        .unwrap();
        assert_eq!(reduced[0]["role"], "system");
        assert!(reduced[0]["content"]
            .as_str()
            .unwrap()
            .contains("TRUNCATED_TO_SPAN_BUDGET"));
    }

    #[test]
    fn caps_pathological_protected_values_without_dropping_the_keys() {
        let mut span = test_span(vec![
            OtelAttribute::string(
                "gen_ai.operation.name",
                "x".repeat(MAX_SERIALIZED_SPAN_BYTES),
            ),
            OtelAttribute::string("gen_ai.conversation.id", "conversation"),
        ]);

        let outcome = enforce_span_budget(&mut span);

        assert!(outcome.within_budget);
        assert_eq!(
            attr_string(&span, "gen_ai.operation.name").unwrap().len(),
            MAX_PROTECTED_STRING_BYTES
        );
        assert_eq!(
            attr_string(&span, "gen_ai.conversation.id"),
            Some("conversation")
        );
    }

    #[test]
    fn duplicate_semantic_attributes_are_canonicalized_first_wins() {
        let mut attributes = Vec::with_capacity(70_001);
        attributes.push(OtelAttribute::string("gen_ai.operation.name", "chat"));
        attributes.extend(
            (0..70_000).map(|_| OtelAttribute::string("gen_ai.operation.name", "duplicate")),
        );
        let mut span = test_span(attributes);
        assert!(serialized_len(&span) > MAX_SERIALIZED_SPAN_BYTES);

        let outcome = enforce_span_budget(&mut span);

        assert!(outcome.within_budget);
        assert_eq!(outcome.removed_attributes, 70_000);
        assert_eq!(attr_string(&span, "gen_ai.operation.name"), Some("chat"));
        assert_eq!(
            span.attributes
                .iter()
                .filter(|attribute| attribute.key == "gen_ai.operation.name")
                .count(),
            1
        );
    }

    #[test]
    fn many_small_attribute_values_are_reduced_without_dropping_keys() {
        let mut attributes = vec![
            OtelAttribute::string("gen_ai.operation.name", "chat"),
            OtelAttribute::string("gen_ai.span.kind", "LLM"),
            OtelAttribute::string("ant.username", "tester"),
            OtelAttribute::int_as_string("gen_ai.usage.input_tokens", 10),
            OtelAttribute::int_as_string("agentic.tool.duration_ms", 21),
            OtelAttribute::string("error.type", "tool_error"),
        ];
        attributes.extend((0..5_000).map(|index| {
            OtelAttribute::string(format!("custom.attr.{index:04}"), "x".repeat(900))
        }));
        let original_attribute_count = attributes.len();
        let mut span = test_span(attributes);
        assert!(serialized_len(&span) > MAX_SERIALIZED_SPAN_BYTES);

        let outcome = enforce_span_budget(&mut span);

        assert!(outcome.within_budget);
        assert!(serialized_len(&span) <= MAX_SERIALIZED_SPAN_BYTES);
        assert_eq!(outcome.removed_attributes, 0);
        assert_eq!(span.attributes.len(), original_attribute_count + 4);
        for (key, value) in [
            ("gen_ai.operation.name", "chat"),
            ("gen_ai.span.kind", "LLM"),
            ("ant.username", "tester"),
            ("gen_ai.usage.input_tokens", "10"),
            ("agentic.tool.duration_ms", "21"),
            ("error.type", "tool_error"),
        ] {
            assert_eq!(attr_string(&span, key), Some(value));
        }
    }

    #[test]
    fn export_body_validation_rejects_an_oversized_bare_span() {
        let body = json!({
            "resourceSpans": [{
                "scopeSpans": [{
                    "spans": [{
                        "spanId": "oversized",
                        "name": "x".repeat(MAX_SERIALIZED_SPAN_BYTES)
                    }]
                }]
            }]
        });

        let error = validate_export_body_span_budget(&body).unwrap_err();

        assert!(error.contains("otel_span_oversize"));
        assert!(error.contains("span_id=oversized"));
        assert!(error.contains("limit_bytes=4194304"));
    }
}

#[cfg(test)]
mod typed_media_tests {
    use super::*;
    use base64::Engine;

    #[test]
    fn aggregate_media_overflow_keeps_the_message_caption_and_a_budget_descriptor() {
        let data = base64::engine::general_purpose::STANDARD.encode(vec![42; 16 * 1024 * 1024]);
        let file = serde_json::json!({"type":"media","mime_type":"audio/wav","data":data});
        let content = serde_json::json!([{"role":"user","content":[{"type":"text","text":"Keep the caption"},file,file,file]}]).to_string();
        let mut span = OtelSpan {
            trace_id: "0123456789abcdef0123456789abcdef".into(), span_id: "0123456789abcdef".into(),
            parent_span_id: String::new(), name: "aggregate media".into(), kind: 1,
            start_time_unix_nano: "1".into(), end_time_unix_nano: "2".into(),
            attributes: vec![OtelAttribute::string("gen_ai.input.messages", content)],
            status: agentic_otel_common::otlp::OtelStatus::ok(),
        };
        let outcome = enforce_span_budget(&mut span);
        assert!(outcome.truncated && outcome.within_budget);
        let OtelAnyValue::String { string_value } = &span.attributes[0].value else { panic!("message stays a string") };
        let value: Value = serde_json::from_str(string_value).unwrap();
        assert_eq!(value[0]["role"], "user");
        assert_eq!(value[0]["content"][0]["text"], "Keep the caption");
        assert_eq!(value[0]["content"][3]["type"], "aivision_media_error");
        assert_eq!(value[0]["content"][3]["reason"], "budget");
        let body = serde_json::json!({"resourceSpans":[{"scopeSpans":[{"spans":[span]}]}]});
        assert!(validate_export_body_span_budget(&body).is_ok());
    }

    #[test]
    fn tool_results_and_both_message_directions_keep_valid_media_below_file_limit() {
        let data = base64::engine::general_purpose::STANDARD.encode(vec![42; 5 * 1024 * 1024]);
        for key in ["gen_ai.input.messages", "gen_ai.output.messages", "gen_ai.tool.call.result"] {
            for mime in ["image/png", "audio/wav", "video/webm", "application/pdf"] {
                let content = serde_json::json!({"content":[
                    {"type":"text","text":"Keep this caption"},
                    {"type":"media","source":{"type":"base64","media_type":mime,"data":data}}
                ]}).to_string();
                let mut span = OtelSpan {
                    trace_id: "0123456789abcdef0123456789abcdef".into(),
                    span_id: "0123456789abcdef".into(), parent_span_id: String::new(),
                    name: "media output".into(), kind: 1,
                    start_time_unix_nano: "1".into(), end_time_unix_nano: "2".into(),
                    attributes: vec![OtelAttribute::string(key, &content)],
                    status: agentic_otel_common::otlp::OtelStatus::ok(),
                };
                let outcome = enforce_span_budget(&mut span);
                assert!(!outcome.truncated, "{key} {mime} should have its media allowance");
                let OtelAnyValue::String { string_value } = &span.attributes[0].value else { panic!("content remains a string") };
                assert_eq!(string_value, &content);
                let body = serde_json::json!({"resourceSpans":[{"scopeSpans":[{"spans":[span]}]}]});
                assert!(validate_export_body_span_budget(&body).is_ok());
            }
        }
    }

    #[test]
    fn oversized_tool_file_does_not_bypass_the_ordinary_span_limit() {
        let data = base64::engine::general_purpose::STANDARD.encode(vec![42; 16 * 1024 * 1024 + 1]);
        let content = serde_json::json!({"type":"media","mime_type":"text/plain","data":data}).to_string();
        let mut span = OtelSpan {
            trace_id: "0123456789abcdef0123456789abcdef".into(), span_id: "0123456789abcdef".into(),
            parent_span_id: String::new(), name: "oversized tool file".into(), kind: 1,
            start_time_unix_nano: "1".into(), end_time_unix_nano: "2".into(),
            attributes: vec![OtelAttribute::string("gen_ai.tool.call.result", content), OtelAttribute::string("gen_ai.tool.name", "read_file")],
            status: agentic_otel_common::otlp::OtelStatus::ok(),
        };
        let outcome = enforce_span_budget(&mut span);
        assert!(outcome.truncated && outcome.within_budget);
        assert!(outcome.final_length <= MAX_SERIALIZED_SPAN_BYTES);
        let OtelAnyValue::String { string_value } = &span.attributes[1].value else { panic!("name stays a string") };
        assert_eq!(string_value, "read_file");
    }
    #[test]
    fn typed_twenty_mib_has_transport_allowance_but_untyped_text_does_not() {
        let data = base64::engine::general_purpose::STANDARD.encode(vec![42; 16 * 1024 * 1024]);
        let messages = serde_json::json!([{"role":"user","content":[{"type":"audio","source":{"type":"base64","media_type":"audio/wav","data":data}}]}]).to_string();
        let mut span = OtelSpan {
            trace_id: "0123456789abcdef0123456789abcdef".into(), span_id:"0123456789abcdef".into(), parent_span_id:String::new(), name:"media".into(), kind:1,
            start_time_unix_nano:"1".into(), end_time_unix_nano:"2".into(), attributes:vec![OtelAttribute::string("gen_ai.input.messages", &messages)], status:agentic_otel_common::otlp::OtelStatus::ok(),
        };
        let outcome = enforce_span_budget(&mut span);
        assert!(!outcome.truncated);
        assert!(outcome.within_budget);
        let body=serde_json::json!({"resourceSpans":[{"scopeSpans":[{"spans":[span]}]}]});
        assert!(validate_export_body_span_budget(&body).is_ok());
        let children = crate::hooks::langfuse::realtime::extract_realtime_semantic_children(&body, &[("media-child".into(), "0123456789abcdef".into())]).unwrap();
        let plan = crate::hooks::langfuse::realtime::plan_realtime_payload_chunks(children, MAX_SERIALIZED_SPAN_BYTES).unwrap();
        assert!(plan.oversized_children.is_empty());
        assert_eq!(plan.chunks.len(), 1);
        let plain=serde_json::json!({"attributes":[{"key":"gen_ai.input.messages","value":{"stringValue":data}}]});
        assert_eq!(media_transport_allowance(&plain),0);
    }
}
