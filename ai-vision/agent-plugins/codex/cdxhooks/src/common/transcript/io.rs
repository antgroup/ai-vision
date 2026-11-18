//! io ownership module.

use super::{File, Path, Read, Seek, SeekFrom, Value};

pub fn split_non_empty_lines(content: &str) -> Vec<&str> {
    content
        .split('\n')
        .map(|line| line.trim_end_matches('\r'))
        .filter(|line| !line.is_empty())
        .collect()
}

pub fn read_new_jsonl(
    transcript_path: &Path,
    offset: i64,
    buffer: &str,
) -> (Vec<Value>, i64, String) {
    let (messages, offset, buffer, _) =
        read_new_jsonl_with_limit(transcript_path, offset, buffer, None);
    (messages, offset, buffer)
}

/// Read at most `max_bytes` from the current transcript window.
///
/// The returned boolean is true when unread bytes remain. The limited reader
/// backs up to a valid UTF-8 boundary so a recovery slice never corrupts a JSON
/// line merely because the byte limit split a multibyte character.
pub fn read_new_jsonl_limited(
    transcript_path: &Path,
    offset: i64,
    buffer: &str,
    max_bytes: usize,
) -> (Vec<Value>, i64, String, bool) {
    read_new_jsonl_with_limit(transcript_path, offset, buffer, Some(max_bytes.max(4)))
}

fn read_new_jsonl_with_limit(
    transcript_path: &Path,
    offset: i64,
    buffer: &str,
    max_bytes: Option<usize>,
) -> (Vec<Value>, i64, String, bool) {
    let Ok(mut file) = File::open(transcript_path) else {
        return (Vec::new(), offset, buffer.to_string(), false);
    };

    if file.seek(SeekFrom::Start(offset as u64)).is_err() {
        return (Vec::new(), offset, buffer.to_string(), false);
    }

    let mut chunk = Vec::new();
    let read_result = match max_bytes {
        Some(max_bytes) => (&mut file).take(max_bytes as u64).read_to_end(&mut chunk),
        None => file.read_to_end(&mut chunk),
    };
    if read_result.is_err() || chunk.is_empty() {
        return (Vec::new(), offset, buffer.to_string(), false);
    }

    if let Err(error) = std::str::from_utf8(&chunk) {
        if error.error_len().is_none() {
            chunk.truncate(error.valid_up_to());
        }
    }
    if chunk.is_empty() {
        return (Vec::new(), offset, buffer.to_string(), true);
    }

    let new_offset = offset + chunk.len() as i64;
    let has_more = file
        .metadata()
        .map(|metadata| metadata.len() > new_offset.max(0) as u64)
        .unwrap_or(false);
    let text = String::from_utf8_lossy(&chunk);
    let combined = format!("{}{}", buffer, text);
    let parts: Vec<&str> = combined.split('\n').collect();

    let last_part = parts.last().copied().unwrap_or("");
    let mut new_buffer = String::new();
    let mut messages = Vec::new();

    for line in &parts[..parts.len().saturating_sub(1)] {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        if let Ok(parsed) = serde_json::from_str::<Value>(line) {
            messages.push(parsed);
        }
    }

    let trimmed_last = last_part.trim();
    if !trimmed_last.is_empty() {
        if let Ok(parsed) = serde_json::from_str::<Value>(trimmed_last) {
            messages.push(parsed);
        } else {
            new_buffer = last_part.to_string();
        }
    }

    (messages, new_offset, new_buffer, has_more)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn limited_reader_resumes_without_losing_utf8_or_json_lines() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("session.jsonl");
        let content = "{\"value\":\"é\"}\n{\"value\":2}\n";
        std::fs::write(&path, content.as_bytes()).unwrap();

        let mut offset = 0;
        let mut buffer = String::new();
        let mut messages = Vec::new();
        loop {
            let (batch, next_offset, next_buffer, has_more) =
                read_new_jsonl_limited(&path, offset, &buffer, 5);
            assert!(next_offset > offset, "limited read must make byte progress");
            messages.extend(batch);
            offset = next_offset;
            buffer = next_buffer;
            if !has_more {
                break;
            }
        }

        assert_eq!(offset as usize, content.len());
        assert!(buffer.is_empty());
        assert_eq!(messages.len(), 2);
        assert_eq!(messages[0]["value"], "é");
        assert_eq!(messages[1]["value"], 2);
    }
}
