use serde_json::Value;
use std::fs::File;
use std::io::{BufRead, BufReader, Read, Seek, SeekFrom};
use std::path::Path;
use std::time::{Duration, Instant};

use sha2::{Digest, Sha256};

use super::fields::{extract_text, get_prompt_chat_id, get_role, is_tool_result};
use super::types::{
    BoundedJsonlScan, JsonlRecord, TranscriptSourceEpoch, TranscriptSourceSnapshot,
    TurnTranscriptInspection,
};

const TRANSCRIPT_SOURCE_PREFIX_MAX_BYTES: u64 = 64 * 1024;
pub const TRANSCRIPT_RECORD_HARD_LIMIT_BYTES: usize = 64 * 1024 * 1024;

#[cfg(windows)]
fn windows_file_identity(file: &File) -> Option<(u32, u64)> {
    use std::ffi::c_void;
    use std::mem::MaybeUninit;
    use std::os::windows::io::AsRawHandle;

    #[repr(C)]
    struct FileTime {
        low: u32,
        high: u32,
    }

    #[repr(C)]
    struct ByHandleFileInformation {
        file_attributes: u32,
        creation_time: FileTime,
        last_access_time: FileTime,
        last_write_time: FileTime,
        volume_serial_number: u32,
        file_size_high: u32,
        file_size_low: u32,
        number_of_links: u32,
        file_index_high: u32,
        file_index_low: u32,
    }

    #[link(name = "kernel32")]
    extern "system" {
        fn GetFileInformationByHandle(
            file: *mut c_void,
            information: *mut ByHandleFileInformation,
        ) -> i32;
    }

    let mut information = MaybeUninit::<ByHandleFileInformation>::uninit();
    if unsafe { GetFileInformationByHandle(file.as_raw_handle().cast(), information.as_mut_ptr()) }
        == 0
    {
        return None;
    }
    let information = unsafe { information.assume_init() };
    Some((
        information.volume_serial_number,
        (u64::from(information.file_index_high) << 32) | u64::from(information.file_index_low),
    ))
}

pub fn inspect_transcript_source(
    transcript_path: &Path,
    prefix_len_hint: Option<u64>,
) -> Result<TranscriptSourceSnapshot, String> {
    let mut file = File::open(transcript_path)
        .map_err(|error| format!("open transcript '{}': {error}", transcript_path.display()))?;
    let metadata = file
        .metadata()
        .map_err(|error| format!("stat transcript '{}': {error}", transcript_path.display()))?;
    let canonical_path = canonical_transcript_path(transcript_path);
    inspect_transcript_source_from_file(
        transcript_path,
        &mut file,
        &metadata,
        &canonical_path,
        prefix_len_hint,
    )
}

fn canonical_transcript_path(transcript_path: &Path) -> String {
    std::fs::canonicalize(transcript_path)
        .unwrap_or_else(|_| transcript_path.to_path_buf())
        .to_string_lossy()
        .into_owned()
}

fn inspect_transcript_source_from_file(
    transcript_path: &Path,
    file: &mut File,
    metadata: &std::fs::Metadata,
    canonical_path: &str,
    prefix_len_hint: Option<u64>,
) -> Result<TranscriptSourceSnapshot, String> {
    let file_len = metadata.len();
    let prefix_len =
        prefix_len_hint.unwrap_or_else(|| file_len.min(TRANSCRIPT_SOURCE_PREFIX_MAX_BYTES));
    if prefix_len > file_len {
        return Err("transcript_replaced_or_truncated".to_string());
    }
    let mut prefix = vec![0_u8; prefix_len as usize];
    file.read_exact(&mut prefix).map_err(|error| {
        format!(
            "read transcript source prefix '{}': {error}",
            transcript_path.display()
        )
    })?;
    let mut identity = Sha256::new();
    identity.update(b"cchooks.transcript-source-epoch.v1\0");
    identity.update(canonical_path.as_bytes());
    identity.update(b"\0");
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        identity.update(metadata.dev().to_le_bytes());
        identity.update(metadata.ino().to_le_bytes());
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        if let Some((volume_serial, file_index)) = windows_file_identity(&file) {
            identity.update(volume_serial.to_le_bytes());
            identity.update(file_index.to_le_bytes());
        } else {
            // Stable fallback for unusual filesystems/handles that do not
            // expose a volume serial and file index.
            identity.update(metadata.creation_time().to_le_bytes());
        }
    }
    identity.update(prefix_len.to_le_bytes());
    identity.update(Sha256::digest(&prefix));
    let identity_digest = identity
        .finalize()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect::<String>();
    Ok(TranscriptSourceSnapshot {
        epoch: TranscriptSourceEpoch {
            identity_digest,
            prefix_len,
        },
        file_len,
    })
}

/// Locate the current prompt record near the tail of a host-owned transcript.
///
/// Claude Code invokes `UserPromptSubmit` before any assistant output for the
/// turn, so the current user record is either the last matching JSONL record or
/// has not been appended yet. Scanning only one maximum-sized record keeps the
/// parent hook bounded even when the host transcript contains a long history.
#[allow(dead_code)] // Kept as the standalone compatibility entry point.
pub fn find_latest_user_prompt_start(
    transcript_path: &Path,
    expected_turn_id: &str,
    expected_prompt: Option<&str>,
) -> Result<Option<i64>, String> {
    let mut file = File::open(transcript_path)
        .map_err(|error| format!("open transcript '{}': {error}", transcript_path.display()))?;
    let file_len = file
        .metadata()
        .map_err(|error| format!("stat transcript '{}': {error}", transcript_path.display()))?
        .len();
    find_latest_user_prompt_start_in_file(
        transcript_path,
        &mut file,
        file_len,
        expected_turn_id,
        expected_prompt,
    )
}

pub fn inspect_turn_transcript(
    transcript_path: &Path,
    expected_turn_id: &str,
    expected_prompt: Option<&str>,
) -> Result<TurnTranscriptInspection, String> {
    let mut file = File::open(transcript_path)
        .map_err(|error| format!("open transcript '{}': {error}", transcript_path.display()))?;
    let metadata = file
        .metadata()
        .map_err(|error| format!("stat transcript '{}': {error}", transcript_path.display()))?;
    let canonical_path = canonical_transcript_path(transcript_path);
    let snapshot = inspect_transcript_source_from_file(
        transcript_path,
        &mut file,
        &metadata,
        &canonical_path,
        None,
    )?;
    let prompt_start = find_latest_user_prompt_start_in_file(
        transcript_path,
        &mut file,
        metadata.len(),
        expected_turn_id,
        expected_prompt,
    )?;
    Ok(TurnTranscriptInspection {
        snapshot,
        canonical_path,
        prompt_start,
    })
}

fn find_latest_user_prompt_start_in_file(
    transcript_path: &Path,
    file: &mut File,
    file_len: u64,
    expected_turn_id: &str,
    expected_prompt: Option<&str>,
) -> Result<Option<i64>, String> {
    let scan_bytes = (TRANSCRIPT_RECORD_HARD_LIMIT_BYTES as u64).min(file_len);
    let mut scan_start = file_len.saturating_sub(scan_bytes);
    file.seek(SeekFrom::Start(scan_start))
        .map_err(|error| format!("seek transcript '{}': {error}", transcript_path.display()))?;
    let mut reader = BufReader::with_capacity(64 * 1024, file);
    if scan_start > 0 {
        let mut partial = Vec::new();
        let skipped = reader.read_until(b'\n', &mut partial).map_err(|error| {
            format!("align transcript '{}': {error}", transcript_path.display())
        })?;
        scan_start = scan_start.saturating_add(skipped as u64);
    }

    let mut offset = scan_start;
    let mut id_match = None;
    let mut prompt_match = None;
    loop {
        let record_start = offset;
        let mut record = Vec::new();
        let read = reader
            .by_ref()
            .take(TRANSCRIPT_RECORD_HARD_LIMIT_BYTES as u64 + 1)
            .read_until(b'\n', &mut record)
            .map_err(|error| format!("read transcript '{}': {error}", transcript_path.display()))?;
        if read == 0 {
            break;
        }
        offset = offset.saturating_add(read as u64);
        if record.len() > TRANSCRIPT_RECORD_HARD_LIMIT_BYTES {
            continue;
        }
        while matches!(record.last(), Some(b'\n' | b'\r')) {
            record.pop();
        }
        let Ok(line) = std::str::from_utf8(&record) else {
            continue;
        };
        let Ok(value) = serde_json::from_str::<Value>(line.trim()) else {
            continue;
        };
        if get_role(&value) != "user"
            || is_tool_result(&value)
            || value.get("isMeta").and_then(Value::as_bool) == Some(true)
        {
            continue;
        }
        let start_offset = i64::try_from(record_start).unwrap_or(i64::MAX);
        if !expected_turn_id.is_empty()
            && get_prompt_chat_id(&value).as_deref() == Some(expected_turn_id)
        {
            id_match = Some(start_offset);
        }
        if let Some(expected_prompt) = expected_prompt {
            let content = value
                .get("message")
                .and_then(|message| message.get("content"))
                .or_else(|| value.get("content"));
            if content.is_some_and(|content| extract_text(content) == expected_prompt) {
                prompt_match = Some(start_offset);
            }
        }
    }
    Ok(id_match.or(prompt_match))
}

pub fn read_bounded_jsonl_records(
    transcript_path: &Path,
    offset: i64,
    max_bytes: usize,
    max_duration: Duration,
) -> Result<BoundedJsonlScan, String> {
    if offset < 0 || max_bytes == 0 || max_duration.is_zero() {
        return Ok(BoundedJsonlScan::CaughtUp {
            next_offset: offset,
        });
    }
    let started = Instant::now();
    let mut file = File::open(transcript_path)
        .map_err(|error| format!("open transcript '{}': {error}", transcript_path.display()))?;
    let file_len = file
        .metadata()
        .map_err(|error| format!("stat transcript '{}': {error}", transcript_path.display()))?
        .len();
    let offset_u64 = offset as u64;
    if offset_u64 > file_len {
        return Err("cursor_beyond_eof".to_string());
    }
    if offset_u64 == file_len {
        return Ok(BoundedJsonlScan::CaughtUp {
            next_offset: offset,
        });
    }
    file.seek(SeekFrom::Start(offset_u64))
        .map_err(|error| format!("seek transcript '{}': {error}", transcript_path.display()))?;
    let mut reader = BufReader::with_capacity(64 * 1024, file);
    let mut records = Vec::new();
    let mut next_offset = offset;
    loop {
        let progressed = next_offset > offset;
        if progressed
            && ((next_offset - offset) as usize >= max_bytes || started.elapsed() >= max_duration)
        {
            return Ok(BoundedJsonlScan::Ready {
                records,
                next_offset,
            });
        }
        let record_start = next_offset;
        let mut record = Vec::new();
        let read = reader
            .by_ref()
            .take(TRANSCRIPT_RECORD_HARD_LIMIT_BYTES as u64 + 1)
            .read_until(b'\n', &mut record)
            .map_err(|error| format!("read transcript '{}': {error}", transcript_path.display()))?;
        if read == 0 {
            return if progressed {
                Ok(BoundedJsonlScan::Ready {
                    records,
                    next_offset,
                })
            } else {
                Ok(BoundedJsonlScan::CaughtUp { next_offset })
            };
        }
        if record.len() > TRANSCRIPT_RECORD_HARD_LIMIT_BYTES {
            return Ok(BoundedJsonlScan::Terminal {
                reason: "record_too_large",
                record_start,
                observed_bytes: record.len() as u64,
            });
        }
        let terminated = record.last() == Some(&b'\n');
        while matches!(record.last(), Some(b'\n' | b'\r')) {
            record.pop();
        }
        let line = match std::str::from_utf8(&record) {
            Ok(line) => line,
            Err(_) => {
                return Ok(BoundedJsonlScan::Terminal {
                    reason: "invalid_utf8",
                    record_start,
                    observed_bytes: record.len() as u64,
                })
            }
        };
        if line.trim().is_empty() {
            next_offset = next_offset.saturating_add(read as i64);
            if next_offset as u64 >= file_len {
                return Ok(BoundedJsonlScan::Ready {
                    records,
                    next_offset,
                });
            }
            continue;
        }
        let value = match serde_json::from_str::<Value>(line.trim()) {
            Ok(value) => value,
            Err(_) if !terminated => {
                return if progressed {
                    Ok(BoundedJsonlScan::Ready {
                        records,
                        next_offset,
                    })
                } else {
                    Ok(BoundedJsonlScan::WaitingForMoreData { record_start })
                }
            }
            Err(_) => {
                return Ok(BoundedJsonlScan::Terminal {
                    reason: "invalid_jsonl",
                    record_start,
                    observed_bytes: record.len() as u64,
                })
            }
        };
        next_offset = next_offset.saturating_add(read as i64);
        records.push(JsonlRecord {
            value,
            start_offset: record_start,
            end_offset: next_offset,
        });
        if next_offset as u64 >= file_len {
            return Ok(BoundedJsonlScan::Ready {
                records,
                next_offset,
            });
        }
    }
}

/// Read new JSONL lines from transcript file using offset-based incremental reading.
pub fn read_new_jsonl(
    transcript_path: &Path,
    offset: i64,
    buffer: &str,
) -> (Vec<Value>, i64, String) {
    read_new_jsonl_bounded(transcript_path, offset, buffer, None)
}

/// Read new JSONL records without crossing an event-bound transcript snapshot.
/// A missing bound retains the legacy read-to-EOF behavior for replay and old
/// queue records.
pub fn read_new_jsonl_bounded(
    transcript_path: &Path,
    offset: i64,
    buffer: &str,
    end_offset: Option<u64>,
) -> (Vec<Value>, i64, String) {
    let Ok(mut file) = File::open(transcript_path) else {
        return (Vec::new(), offset, buffer.to_string());
    };

    let start_offset = offset.max(0) as u64;
    if file.seek(SeekFrom::Start(start_offset)).is_err() {
        return (Vec::new(), offset, buffer.to_string());
    }

    let mut chunk = Vec::new();
    let read_result = match end_offset {
        Some(end_offset) if end_offset <= start_offset => {
            return (Vec::new(), offset, buffer.into())
        }
        Some(end_offset) => file
            .take(end_offset.saturating_sub(start_offset))
            .read_to_end(&mut chunk),
        None => file.read_to_end(&mut chunk),
    };
    if read_result.is_err() || chunk.is_empty() {
        return (Vec::new(), offset, buffer.to_string());
    }

    let new_offset = offset + chunk.len() as i64;
    let text = String::from_utf8_lossy(&chunk);
    let combined = format!("{}{}", buffer, text);
    let parts: Vec<&str> = combined.split('\n').collect();

    let last_part = parts.last().copied().unwrap_or("");
    let mut new_buffer = String::new();
    let mut messages: Vec<Value> = Vec::new();

    for line in &parts[..parts.len().saturating_sub(1)] {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        if let Ok(parsed) = serde_json::from_str::<Value>(line) {
            messages.push(parsed);
        }
    }

    // Try to parse the last part; if valid JSON, include it
    let trimmed_last = last_part.trim();
    if !trimmed_last.is_empty() {
        if let Ok(parsed) = serde_json::from_str::<Value>(trimmed_last) {
            messages.push(parsed);
        } else {
            new_buffer = last_part.to_string();
        }
    }

    (messages, new_offset, new_buffer)
}

/// Read at most `max_bytes` from the current transcript window. The returned
/// boolean is true when unread bytes remain inside that window. The cursor is
/// backed up to a UTF-8 boundary so a split multibyte character is retried by
/// the next slice instead of being replaced.
pub fn read_new_jsonl_limited_bounded(
    transcript_path: &Path,
    offset: i64,
    buffer: &str,
    max_bytes: usize,
    end_offset: Option<u64>,
) -> (Vec<Value>, i64, String, bool, Option<&'static str>) {
    let Ok(mut file) = File::open(transcript_path) else {
        return (Vec::new(), offset, buffer.to_string(), false, None);
    };
    let start_offset = offset.max(0) as u64;
    if file.seek(SeekFrom::Start(start_offset)).is_err() {
        return (Vec::new(), offset, buffer.to_string(), false, None);
    }
    let file_len = file.metadata().map(|metadata| metadata.len()).unwrap_or(0);
    let window_end = end_offset.unwrap_or(file_len).min(file_len);
    if window_end <= start_offset {
        return (Vec::new(), offset, buffer.to_string(), false, None);
    }
    let read_limit = (max_bytes.max(4) as u64).min(window_end - start_offset);
    let mut chunk = Vec::new();
    if (&mut file)
        .take(read_limit)
        .read_to_end(&mut chunk)
        .is_err()
        || chunk.is_empty()
    {
        return (Vec::new(), offset, buffer.to_string(), false, None);
    }
    loop {
        match std::str::from_utf8(&chunk) {
            Ok(_) => break,
            Err(error) if error.error_len().is_none() => {
                let valid_up_to = error.valid_up_to();
                let incomplete_len = chunk.len().saturating_sub(valid_up_to);
                let extra_limit = 4usize.saturating_sub(incomplete_len).min(3);
                let available = file_len.saturating_sub(
                    start_offset.saturating_add(chunk.len().try_into().unwrap_or(u64::MAX)),
                );
                if extra_limit > 0 && available > 0 {
                    let previous_len = chunk.len();
                    let _ = (&mut file).take(1).read_to_end(&mut chunk);
                    if chunk.len() > previous_len {
                        continue;
                    }
                }
                chunk.truncate(valid_up_to);
                break;
            }
            Err(_) => {
                return (
                    Vec::new(),
                    offset,
                    buffer.to_string(),
                    false,
                    Some("invalid_utf8"),
                );
            }
        }
    }
    if chunk.is_empty() {
        return (
            Vec::new(),
            offset,
            buffer.to_string(),
            false,
            Some("incomplete_utf8"),
        );
    }

    let new_offset = offset.saturating_add(chunk.len() as i64);
    let has_more = (new_offset.max(0) as u64) < window_end;
    let text = std::str::from_utf8(&chunk).expect("validated transcript chunk must remain UTF-8");
    let combined = format!("{buffer}{text}");
    let parts = combined.split('\n').collect::<Vec<_>>();
    let last_part = parts.last().copied().unwrap_or("");
    let mut new_buffer = String::new();
    let mut messages = Vec::new();
    for line in &parts[..parts.len().saturating_sub(1)] {
        let line = line.trim();
        if !line.is_empty() {
            match serde_json::from_str::<Value>(line) {
                Ok(parsed) => messages.push(parsed),
                Err(_) => {
                    return (
                        messages,
                        new_offset,
                        String::new(),
                        has_more,
                        Some("invalid_jsonl"),
                    )
                }
            }
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
    (messages, new_offset, new_buffer, has_more, None)
}

/// Read at most one additional JSONL record. This is used after a Stop event's
/// initial EOF snapshot so delayed bytes may finish that Turn without consuming
/// a later user Turn already appended to the same transcript.
pub fn read_next_jsonl_record(
    transcript_path: &Path,
    offset: i64,
    buffer: &str,
) -> (Vec<Value>, i64, String) {
    let Ok(mut file) = File::open(transcript_path) else {
        return (Vec::new(), offset, buffer.to_string());
    };
    if file.seek(SeekFrom::Start(offset.max(0) as u64)).is_err() {
        return (Vec::new(), offset, buffer.to_string());
    }
    let mut bytes = Vec::new();
    if BufReader::new(file).read_until(b'\n', &mut bytes).is_err() || bytes.is_empty() {
        return (Vec::new(), offset, buffer.to_string());
    }
    let new_offset = offset.saturating_add(bytes.len() as i64);
    let combined = format!("{buffer}{}", String::from_utf8_lossy(&bytes));
    match serde_json::from_str::<Value>(combined.trim()) {
        Ok(value) => (vec![value], new_offset, String::new()),
        Err(_) => (
            Vec::new(),
            new_offset,
            combined.trim_end_matches('\n').to_string(),
        ),
    }
}

/// Split content into non-empty lines.
pub fn split_non_empty_lines(content: &str) -> Vec<&str> {
    content
        .split('\n')
        .map(|line| line.trim_end_matches('\r'))
        .filter(|line| !line.is_empty())
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn limited_reader_resumes_without_losing_utf8_or_json_lines() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("limited.jsonl");
        let content = "{\"value\":\"é\"}\n{\"value\":2}\n";
        std::fs::write(&path, content.as_bytes()).unwrap();
        let mut offset = 0;
        let mut buffer = String::new();
        let mut messages = Vec::new();
        loop {
            let (batch, next_offset, next_buffer, has_more, terminal) =
                read_new_jsonl_limited_bounded(&path, offset, &buffer, 5, None);
            assert_eq!(terminal, None);
            assert!(next_offset >= offset);
            messages.extend(batch);
            offset = next_offset;
            buffer = next_buffer;
            if !has_more {
                break;
            }
        }
        assert_eq!(messages, vec![json!({"value": "é"}), json!({"value": 2})]);
        assert!(buffer.is_empty());
        assert_eq!(offset as usize, content.len());
    }

    #[test]
    fn limited_reader_marks_complete_invalid_jsonl_as_terminal() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("invalid-jsonl.jsonl");
        std::fs::write(&path, b"{not-json}\n").unwrap();

        let (messages, offset, buffer, has_more, terminal) =
            read_new_jsonl_limited_bounded(&path, 0, "", 1024, None);

        assert!(messages.is_empty());
        assert_eq!(offset, 11);
        assert!(buffer.is_empty());
        assert!(!has_more);
        assert_eq!(terminal, Some("invalid_jsonl"));
    }

    #[test]
    fn limited_reader_marks_invalid_utf8_as_terminal() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("invalid-utf8.jsonl");
        std::fs::write(&path, [0xff, b'\n']).unwrap();

        let (messages, offset, buffer, has_more, terminal) =
            read_new_jsonl_limited_bounded(&path, 0, "", 1024, None);

        assert!(messages.is_empty());
        assert_eq!(offset, 0);
        assert!(buffer.is_empty());
        assert!(!has_more);
        assert_eq!(terminal, Some("invalid_utf8"));
    }

    #[test]
    fn limited_reader_stops_at_partial_utf8_eof_and_resumes_after_growth() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("partial-utf8.jsonl");
        let content = "{\"value\":\"é\"}\n".as_bytes();
        let split = content.iter().position(|byte| *byte == 0xc3).unwrap() + 1;
        std::fs::write(&path, &content[..split]).unwrap();

        let (first, first_offset, first_buffer, first_has_more, first_terminal) =
            read_new_jsonl_limited_bounded(&path, 0, "", 1024, Some(split as u64));
        assert!(first.is_empty());
        assert_eq!(first_offset as usize, split - 1);
        assert!(!first_buffer.is_empty());
        assert!(first_has_more);
        assert_eq!(first_terminal, None);

        let (stalled, stalled_offset, stalled_buffer, stalled_has_more, stalled_terminal) =
            read_new_jsonl_limited_bounded(
                &path,
                first_offset,
                &first_buffer,
                1024,
                Some(split as u64),
            );
        assert!(stalled.is_empty());
        assert_eq!(stalled_offset, first_offset);
        assert_eq!(stalled_buffer, first_buffer);
        assert!(!stalled_has_more);
        assert_eq!(stalled_terminal, Some("incomplete_utf8"));

        std::fs::write(&path, content).unwrap();
        let (resumed, resumed_offset, resumed_buffer, resumed_has_more, resumed_terminal) =
            read_new_jsonl_limited_bounded(
                &path,
                stalled_offset,
                &stalled_buffer,
                1024,
                Some(split as u64),
            );
        assert!(resumed.is_empty());
        assert!(resumed_offset > stalled_offset);
        assert!(!resumed_has_more);
        assert_eq!(resumed_terminal, None);

        let (completed, final_offset, final_buffer) =
            read_next_jsonl_record(&path, resumed_offset, &resumed_buffer);
        assert_eq!(completed, vec![json!({"value": "é"})]);
        assert!(final_buffer.is_empty());
        assert_eq!(final_offset as usize, content.len());
    }

    #[test]
    fn bounded_jsonl_scanner_advances_complete_records_and_retries_partial_tail() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("session.jsonl");
        std::fs::write(&path, b"{\"a\":1}\n{\"b\":2}\n{\"c\":").unwrap();
        let scan = read_bounded_jsonl_records(&path, 0, 1024, Duration::from_millis(100)).unwrap();
        let BoundedJsonlScan::Ready {
            records,
            next_offset,
        } = scan
        else {
            panic!("expected complete prefix");
        };
        assert_eq!(records.len(), 2);
        assert_eq!(records[0].start_offset, 0);
        assert_eq!(records[0].end_offset, 8);
        assert_eq!(next_offset, 16);

        assert!(matches!(
            read_bounded_jsonl_records(&path, next_offset, 1024, Duration::from_millis(100))
                .unwrap(),
            BoundedJsonlScan::WaitingForMoreData { record_start: 16 }
        ));

        std::fs::write(&path, b"{\"a\":1}\n{\"b\":2}\n{\"c\":3}\n").unwrap();
        let retry =
            read_bounded_jsonl_records(&path, next_offset, 1024, Duration::from_millis(100))
                .unwrap();
        let BoundedJsonlScan::Ready { records, .. } = retry else {
            panic!("expected repaired tail");
        };
        assert_eq!(records.len(), 1);
        assert_eq!(records[0].value["c"], 3);
    }

    #[test]
    fn bounded_jsonl_scanner_allows_one_record_over_the_soft_byte_limit() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("large-record.jsonl");
        let value = json!({"payload": "x".repeat(2 * 1024 * 1024)});
        std::fs::write(&path, format!("{value}\n")).unwrap();

        let scan =
            read_bounded_jsonl_records(&path, 0, 1024 * 1024, Duration::from_secs(1)).unwrap();
        let BoundedJsonlScan::Ready {
            records,
            next_offset,
        } = scan
        else {
            panic!("large valid record must make progress");
        };
        assert_eq!(records.len(), 1);
        assert_eq!(next_offset as u64, std::fs::metadata(&path).unwrap().len());
    }

    #[test]
    fn transcript_source_epoch_is_stable_on_append_and_changes_on_replacement() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("epoch.jsonl");
        std::fs::write(&path, b"{\"a\":1}\n").unwrap();
        let first = inspect_transcript_source(&path, None).unwrap();

        use std::io::Write;
        let mut file = std::fs::OpenOptions::new()
            .append(true)
            .open(&path)
            .unwrap();
        file.write_all(b"{\"b\":2}\n").unwrap();
        let appended = inspect_transcript_source(&path, Some(first.epoch.prefix_len)).unwrap();
        assert_eq!(appended.epoch, first.epoch);
        assert!(appended.file_len > first.file_len);

        let replacement = dir.path().join("replacement.jsonl");
        std::fs::write(&replacement, b"{\"z\":9}\n").unwrap();
        std::fs::remove_file(&path).unwrap();
        std::fs::rename(&replacement, &path).unwrap();
        let replaced = inspect_transcript_source(&path, Some(first.epoch.prefix_len)).unwrap();
        assert_ne!(replaced.epoch, first.epoch);
    }

    #[test]
    fn latest_user_prompt_start_skips_rebuilt_history() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("turn-boundary.jsonl");
        let history = serde_json::json!({
            "type": "user",
            "uuid": "history-user",
            "message": {"role": "user", "content": "old prompt"}
        });
        let current = serde_json::json!({
            "type": "user",
            "uuid": "current-user",
            "message": {"role": "user", "content": "current prompt"}
        });
        let history_line = format!("{history}\n");
        std::fs::write(&path, format!("{history_line}{current}\n")).unwrap();

        let start =
            find_latest_user_prompt_start(&path, "current-user", Some("current prompt")).unwrap();

        assert_eq!(start, Some(history_line.len() as i64));
    }

    #[test]
    fn turn_transcript_inspection_reuses_snapshot_and_prompt_scan() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("turn-inspection.jsonl");
        let history = serde_json::json!({
            "type": "user",
            "uuid": "history-user",
            "message": {"role": "user", "content": "old prompt"}
        });
        let current = serde_json::json!({
            "type": "user",
            "uuid": "current-user",
            "message": {"role": "user", "content": "current prompt"}
        });
        let history_line = format!("{history}\n");
        std::fs::write(&path, format!("{history_line}{current}\n")).unwrap();

        let inspection =
            inspect_turn_transcript(&path, "current-user", Some("current prompt")).unwrap();

        assert_eq!(
            inspection.canonical_path,
            std::fs::canonicalize(&path)
                .unwrap()
                .to_string_lossy()
                .into_owned()
        );
        assert_eq!(inspection.prompt_start, Some(history_line.len() as i64));
        assert_eq!(
            inspection.snapshot.file_len,
            std::fs::metadata(&path).unwrap().len()
        );
        assert_eq!(
            inspection.snapshot,
            inspect_transcript_source(&path, None).unwrap()
        );
    }
    #[test]
    fn test_split_non_empty_lines_basic() {
        let result = split_non_empty_lines("hello\n\nworld\n");
        assert_eq!(result, vec!["hello", "world"]);
    }

    #[test]
    fn test_split_non_empty_lines_empty() {
        let result = split_non_empty_lines("");
        assert!(result.is_empty());
    }

    #[test]
    fn test_split_non_empty_lines_crlf() {
        let result = split_non_empty_lines("line1\r\nline2\r\n");
        assert_eq!(result, vec!["line1", "line2"]);
    }
}
