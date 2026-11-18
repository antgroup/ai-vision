use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::fs::File;
use std::io::{BufRead, BufReader, Read, Seek, SeekFrom};
use std::path::Path;
use std::time::{Duration, Instant};

pub(crate) const TEMPORARY_TRANSCRIPT_IO_ERROR: &str = "temporary_transcript_io:";
pub const DEFAULT_GENERATION_SCAN_MAX_BYTES: usize = 1024 * 1024;
pub const GENERATION_SCAN_RECORD_HARD_LIMIT_BYTES: usize = 64 * 1024 * 1024;
pub const GENERATION_SEMANTIC_WINDOW_HARD_LIMIT_BYTES: usize = 64 * 1024 * 1024;
const GENERATION_SOURCE_PREFIX_MAX_BYTES: u64 = 4096;
pub const DEFAULT_GENERATION_SCAN_MAX_DURATION: Duration = Duration::from_millis(100);
pub const REALTIME_GENERATION_TRIGGER_SCHEMA_V1: u32 = 1;
pub const REALTIME_GENERATION_TRIGGER_SCHEMA_V2: u32 = 2;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RealtimeGenerationCorrelation {
    LegacyTool {
        tool_use_id: String,
    },
    NativeTurn {
        turn_id: String,
        hook_tool_use_id: String,
    },
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RealtimeGenerationTrigger {
    pub schema_version: u32,
    pub session_id: String,
    pub context_id: String,
    pub correlation: RealtimeGenerationCorrelation,
    pub transcript_path: String,
    pub scan_anchor: u64,
    pub generation_batch_size: usize,
}

impl RealtimeGenerationTrigger {
    pub fn v2(
        session_id: &str,
        context_id: &str,
        turn_id: &str,
        hook_tool_use_id: &str,
        transcript_path: &str,
        scan_anchor: u64,
        generation_batch_size: usize,
    ) -> Result<Self, String> {
        let trigger = Self {
            schema_version: REALTIME_GENERATION_TRIGGER_SCHEMA_V2,
            session_id: session_id.to_string(),
            context_id: context_id.to_string(),
            correlation: RealtimeGenerationCorrelation::NativeTurn {
                turn_id: turn_id.to_string(),
                hook_tool_use_id: hook_tool_use_id.to_string(),
            },
            transcript_path: transcript_path.to_string(),
            scan_anchor,
            generation_batch_size,
        };
        trigger.validate_required_fields()?;
        Ok(trigger)
    }

    pub fn parse(
        payload: &Value,
        fallback_session_id: Option<&str>,
        fallback_context_id: Option<&str>,
    ) -> Result<Self, String> {
        let schema_version = payload
            .get("schema_version")
            .and_then(parse_u64)
            .unwrap_or(REALTIME_GENERATION_TRIGGER_SCHEMA_V1 as u64);
        let schema_version = u32::try_from(schema_version)
            .map_err(|_| "generation trigger schema_version is out of range".to_string())?;
        let session_id = nonempty_string(payload, "session_id")
            .or_else(|| fallback_session_id.map(str::to_string))
            .ok_or_else(|| "generation trigger missing session_id".to_string())?;
        let context_id = nonempty_string(payload, "context_id")
            .or_else(|| fallback_context_id.map(str::to_string))
            .ok_or_else(|| "generation trigger missing context_id".to_string())?;
        let hook_tool_use_id = nonempty_string(payload, "trigger_tool_use_id")
            .ok_or_else(|| "generation trigger missing trigger_tool_use_id".to_string())?;
        let correlation = match schema_version {
            REALTIME_GENERATION_TRIGGER_SCHEMA_V1 => RealtimeGenerationCorrelation::LegacyTool {
                tool_use_id: hook_tool_use_id,
            },
            REALTIME_GENERATION_TRIGGER_SCHEMA_V2 => {
                let turn_id = nonempty_string(payload, "trigger_turn_id")
                    .ok_or_else(|| "generation trigger v2 missing trigger_turn_id".to_string())?;
                RealtimeGenerationCorrelation::NativeTurn {
                    turn_id,
                    hook_tool_use_id,
                }
            }
            other => {
                return Err(format!(
                    "unsupported generation trigger schema_version={other}"
                ))
            }
        };
        let transcript_path = nonempty_string(payload, "transcript_path")
            .ok_or_else(|| "generation trigger missing transcript_path".to_string())?;
        let scan_anchor = payload
            .get("scan_anchor")
            .and_then(parse_u64)
            .or_else(|| payload.get("cursor_bytes").and_then(parse_u64))
            .unwrap_or(0);
        if let (Some(anchor), Some(cursor)) = (
            payload.get("scan_anchor").and_then(parse_u64),
            payload.get("cursor_bytes").and_then(parse_u64),
        ) {
            if anchor != cursor {
                return Err("generation trigger cursor fields disagree".to_string());
            }
        }
        let generation_batch_size = payload
            .get("generation_batch_size")
            .and_then(parse_u64)
            .and_then(|value| usize::try_from(value).ok())
            .unwrap_or(crate::hooks::DEFAULT_REALTIME_LLM_EXPORT_BATCH_SIZE);
        let trigger = Self {
            schema_version,
            session_id,
            context_id,
            correlation,
            transcript_path,
            scan_anchor,
            generation_batch_size,
        };
        trigger.validate_required_fields()?;
        Ok(trigger)
    }

    pub fn subject_id(&self) -> String {
        match &self.correlation {
            RealtimeGenerationCorrelation::LegacyTool { tool_use_id } => {
                format!("scan:{tool_use_id}:{}", self.scan_anchor)
            }
            RealtimeGenerationCorrelation::NativeTurn {
                turn_id,
                hook_tool_use_id,
            } => format!(
                "scan:v{}:{turn_id}:{hook_tool_use_id}:{}",
                self.schema_version, self.scan_anchor
            ),
        }
    }

    pub fn hook_tool_use_id(&self) -> &str {
        match &self.correlation {
            RealtimeGenerationCorrelation::LegacyTool { tool_use_id } => tool_use_id,
            RealtimeGenerationCorrelation::NativeTurn {
                hook_tool_use_id, ..
            } => hook_tool_use_id,
        }
    }

    pub fn turn_id(&self) -> Option<&str> {
        match &self.correlation {
            RealtimeGenerationCorrelation::LegacyTool { .. } => None,
            RealtimeGenerationCorrelation::NativeTurn { turn_id, .. } => Some(turn_id),
        }
    }

    pub fn identity_matches(
        &self,
        job_session_id: &str,
        job_context_id: Option<&str>,
        subject_id: Option<&str>,
    ) -> bool {
        self.session_id == job_session_id
            && job_context_id == Some(self.context_id.as_str())
            && subject_id == Some(self.subject_id().as_str())
    }

    pub fn matches_context(&self, turn_id: Option<&str>, tool_use_ids: &[String]) -> bool {
        if !tool_use_ids
            .iter()
            .any(|tool_use_id| tool_use_id == self.hook_tool_use_id())
        {
            return false;
        }
        match &self.correlation {
            RealtimeGenerationCorrelation::LegacyTool { .. } => true,
            RealtimeGenerationCorrelation::NativeTurn {
                turn_id: trigger_turn_id,
                ..
            } => turn_id == Some(trigger_turn_id.as_str()),
        }
    }

    fn validate_required_fields(&self) -> Result<(), String> {
        if self.session_id.is_empty()
            || self.context_id.is_empty()
            || self.transcript_path.is_empty()
            || self.hook_tool_use_id().is_empty()
        {
            return Err("generation trigger has an empty required field".to_string());
        }
        if self.generation_batch_size == 0 {
            return Err("generation trigger batch size must be positive".to_string());
        }
        if self
            .turn_id()
            .is_some_and(|turn_id| turn_id.trim().is_empty())
        {
            return Err("generation trigger turn_id must be non-empty".to_string());
        }
        Ok(())
    }
}

fn nonempty_string(value: &Value, field: &str) -> Option<String> {
    value
        .get(field)
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_string)
}

fn parse_u64(value: &Value) -> Option<u64> {
    value
        .as_u64()
        .or_else(|| value.as_str().and_then(|text| text.trim().parse().ok()))
}

#[derive(Debug, Clone, PartialEq)]
pub enum BoundedScanOutcome {
    Ready {
        entries: Vec<Value>,
        next_offset: u64,
    },
    CaughtUp {
        next_offset: u64,
    },
    WaitingForMoreData {
        record_start: u64,
    },
    FinalizerOnly {
        reason: &'static str,
        record_start: u64,
        observed_bytes: u64,
    },
    Deferred {
        reason: &'static str,
    },
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TranscriptSourceEpoch {
    pub identity_digest: String,
    pub prefix_len: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TranscriptSourceSnapshot {
    pub epoch: TranscriptSourceEpoch,
    pub file_len: u64,
}

fn transcript_io_error(action: &str, transcript_path: &Path, error: std::io::Error) -> String {
    let prefix = if matches!(
        error.kind(),
        std::io::ErrorKind::NotFound
            | std::io::ErrorKind::Interrupted
            | std::io::ErrorKind::WouldBlock
            | std::io::ErrorKind::TimedOut
    ) {
        TEMPORARY_TRANSCRIPT_IO_ERROR
    } else {
        ""
    };
    format!(
        "{prefix}{action} transcript '{}': {error}",
        transcript_path.display()
    )
}

pub fn inspect_transcript_source(
    transcript_path: &Path,
    prefix_len_hint: Option<u64>,
) -> Result<TranscriptSourceSnapshot, String> {
    let mut file = File::open(transcript_path)
        .map_err(|error| transcript_io_error("open", transcript_path, error))?;
    let metadata = file
        .metadata()
        .map_err(|error| transcript_io_error("stat", transcript_path, error))?;
    let file_len = metadata.len();
    let prefix_len =
        prefix_len_hint.unwrap_or_else(|| file_len.min(GENERATION_SOURCE_PREFIX_MAX_BYTES));
    if prefix_len > file_len {
        return Err("transcript_replaced_or_truncated".to_string());
    }
    let mut prefix = vec![0_u8; prefix_len as usize];
    file.read_exact(&mut prefix)
        .map_err(|error| transcript_io_error("read source prefix", transcript_path, error))?;
    let canonical = std::fs::canonicalize(transcript_path)
        .unwrap_or_else(|_| transcript_path.to_path_buf())
        .to_string_lossy()
        .into_owned();
    let mut identity = Sha256::new();
    identity.update(b"cdxhooks.generation-source-epoch.v1\0");
    identity.update(canonical.as_bytes());
    identity.update(b"\0");
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        identity.update(metadata.dev().to_le_bytes());
        identity.update(metadata.ino().to_le_bytes());
    }
    #[cfg(not(unix))]
    {
        identity.update(metadata.permissions().readonly().to_string().as_bytes());
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

pub fn read_complete_transcript_range(
    transcript_path: &Path,
    start_offset: u64,
    end_offset: u64,
) -> Result<Vec<Value>, String> {
    if end_offset < start_offset {
        return Err("semantic window offsets are reversed".to_string());
    }
    let byte_len = end_offset.saturating_sub(start_offset);
    if byte_len > GENERATION_SEMANTIC_WINDOW_HARD_LIMIT_BYTES as u64 {
        return Err("semantic_window_too_large".to_string());
    }
    if byte_len == 0 {
        return Ok(Vec::new());
    }
    let mut file = File::open(transcript_path)
        .map_err(|error| transcript_io_error("open", transcript_path, error))?;
    file.seek(SeekFrom::Start(start_offset))
        .map_err(|error| transcript_io_error("seek", transcript_path, error))?;
    let mut bytes = vec![0_u8; byte_len as usize];
    file.read_exact(&mut bytes)
        .map_err(|error| transcript_io_error("read", transcript_path, error))?;
    if bytes.last() != Some(&b'\n') {
        return Err("semantic window does not end at a complete record".to_string());
    }
    let text =
        std::str::from_utf8(&bytes).map_err(|_| "semantic window invalid_utf8".to_string())?;
    text.lines()
        .filter(|line| !line.trim().is_empty())
        .map(|line| {
            serde_json::from_str::<Value>(line)
                .map_err(|_| "semantic window invalid_jsonl".to_string())
        })
        .collect()
}

pub fn read_bounded_transcript_window(
    transcript_path: &Path,
    offset: u64,
) -> Result<BoundedScanOutcome, String> {
    read_bounded_transcript_window_with_limits(
        transcript_path,
        offset,
        DEFAULT_GENERATION_SCAN_MAX_BYTES,
        DEFAULT_GENERATION_SCAN_MAX_DURATION,
    )
}

pub fn read_bounded_transcript_window_with_limits(
    transcript_path: &Path,
    offset: u64,
    max_bytes: usize,
    max_duration: Duration,
) -> Result<BoundedScanOutcome, String> {
    read_bounded_transcript_window_with_all_limits(
        transcript_path,
        offset,
        max_bytes,
        max_duration,
        GENERATION_SCAN_RECORD_HARD_LIMIT_BYTES,
    )
}

fn read_bounded_transcript_window_with_all_limits(
    transcript_path: &Path,
    offset: u64,
    max_bytes: usize,
    max_duration: Duration,
    record_hard_limit: usize,
) -> Result<BoundedScanOutcome, String> {
    let started = Instant::now();
    if max_bytes == 0 || record_hard_limit == 0 || max_duration.is_zero() {
        return Ok(BoundedScanOutcome::Deferred {
            reason: "scan_budget_exhausted",
        });
    }

    let mut file = File::open(transcript_path)
        .map_err(|error| transcript_io_error("open", transcript_path, error))?;
    let file_len = file
        .metadata()
        .map_err(|error| transcript_io_error("stat", transcript_path, error))?
        .len();
    if offset > file_len {
        return Ok(BoundedScanOutcome::Deferred {
            reason: "cursor_beyond_eof",
        });
    }
    if offset == file_len {
        return Ok(BoundedScanOutcome::CaughtUp {
            next_offset: offset,
        });
    }
    file.seek(SeekFrom::Start(offset))
        .map_err(|error| transcript_io_error("seek", transcript_path, error))?;

    let mut reader = BufReader::with_capacity(64 * 1024, file);
    let mut entries = Vec::new();
    let mut next_offset = offset;
    loop {
        let progressed = next_offset > offset;
        if progressed
            && (next_offset.saturating_sub(offset) >= max_bytes as u64
                || started.elapsed() >= max_duration)
        {
            return Ok(BoundedScanOutcome::Ready {
                entries,
                next_offset,
            });
        }

        let record_start = next_offset;
        let mut record = Vec::new();
        let read = reader
            .by_ref()
            .take(record_hard_limit as u64 + 1)
            .read_until(b'\n', &mut record)
            .map_err(|error| transcript_io_error("read", transcript_path, error))?;
        if read == 0 {
            return if progressed {
                Ok(BoundedScanOutcome::Ready {
                    entries,
                    next_offset,
                })
            } else {
                Ok(BoundedScanOutcome::CaughtUp { next_offset })
            };
        }
        if record.len() > record_hard_limit {
            return Ok(BoundedScanOutcome::FinalizerOnly {
                reason: "record_too_large",
                record_start,
                observed_bytes: record.len() as u64,
            });
        }
        if record.last() != Some(&b'\n') {
            return if progressed {
                Ok(BoundedScanOutcome::Ready {
                    entries,
                    next_offset,
                })
            } else {
                Ok(BoundedScanOutcome::WaitingForMoreData { record_start })
            };
        }
        next_offset = next_offset.saturating_add(record.len() as u64);

        while matches!(record.last(), Some(b'\n' | b'\r')) {
            record.pop();
        }
        let line = match std::str::from_utf8(&record) {
            Ok(line) => line,
            Err(_) => {
                return Ok(BoundedScanOutcome::FinalizerOnly {
                    reason: "invalid_utf8",
                    record_start,
                    observed_bytes: record.len() as u64,
                })
            }
        };
        if line.trim().is_empty() {
            continue;
        }
        let entry = match serde_json::from_str::<Value>(line) {
            Ok(entry) => entry,
            Err(_) => {
                return Ok(BoundedScanOutcome::FinalizerOnly {
                    reason: "invalid_jsonl",
                    record_start,
                    observed_bytes: record.len() as u64,
                })
            }
        };
        entries.push(entry);
        if next_offset >= file_len {
            return Ok(BoundedScanOutcome::Ready {
                entries,
                next_offset,
            });
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::io::Write;

    #[test]
    fn transcript_io_errors_mark_only_temporary_kinds() {
        let path = Path::new("session.jsonl");
        for kind in [
            std::io::ErrorKind::NotFound,
            std::io::ErrorKind::Interrupted,
            std::io::ErrorKind::WouldBlock,
            std::io::ErrorKind::TimedOut,
        ] {
            let error = transcript_io_error("open", path, std::io::Error::from(kind));
            assert!(error.starts_with(TEMPORARY_TRANSCRIPT_IO_ERROR));
        }

        let permission_denied = transcript_io_error(
            "open",
            path,
            std::io::Error::from(std::io::ErrorKind::PermissionDenied),
        );
        assert!(!permission_denied.starts_with(TEMPORARY_TRANSCRIPT_IO_ERROR));
    }

    #[test]
    fn typed_trigger_validates_v2_and_legacy_subjects() {
        let v2 = RealtimeGenerationTrigger::parse(
            &json!({
                "schema_version": 2,
                "session_id": "session",
                "context_id": "context",
                "trigger_turn_id": "turn-1",
                "trigger_tool_use_id": "exec-1",
                "transcript_path": "/tmp/session.jsonl",
                "scan_anchor": "42",
                "cursor_bytes": 42,
                "generation_batch_size": "3"
            }),
            None,
            None,
        )
        .unwrap();
        assert_eq!(v2.subject_id(), "scan:v2:turn-1:exec-1:42");
        assert!(v2.identity_matches("session", Some("context"), Some("scan:v2:turn-1:exec-1:42")));

        let v1 = RealtimeGenerationTrigger::parse(
            &json!({
                "schema_version": 1,
                "trigger_tool_use_id": "call-1",
                "transcript_path": "/tmp/session.jsonl",
                "scan_anchor": 7
            }),
            Some("session"),
            Some("context"),
        )
        .unwrap();
        assert_eq!(v1.subject_id(), "scan:call-1:7");
        assert_eq!(v1.turn_id(), None);
    }

    #[test]
    fn typed_trigger_rejects_unknown_schema_and_conflicting_cursor() {
        let base = json!({
            "schema_version": 3,
            "session_id": "session",
            "context_id": "context",
            "trigger_tool_use_id": "exec-1",
            "transcript_path": "/tmp/session.jsonl"
        });
        assert!(RealtimeGenerationTrigger::parse(&base, None, None)
            .unwrap_err()
            .contains("unsupported"));

        let conflict = json!({
            "schema_version": 2,
            "session_id": "session",
            "context_id": "context",
            "trigger_turn_id": "turn-1",
            "trigger_tool_use_id": "exec-1",
            "transcript_path": "/tmp/session.jsonl",
            "scan_anchor": 1,
            "cursor_bytes": 2
        });
        assert!(RealtimeGenerationTrigger::parse(&conflict, None, None)
            .unwrap_err()
            .contains("disagree"));
    }

    #[test]
    fn bounded_reader_returns_complete_jsonl_without_settling() {
        let mut file = tempfile::NamedTempFile::new().unwrap();
        writeln!(file, r#"{{"type":"one"}}"#).unwrap();
        writeln!(file, r#"{{"type":"two"}}"#).unwrap();

        let outcome = read_bounded_transcript_window(file.path(), 0).unwrap();
        let BoundedScanOutcome::Ready {
            entries,
            next_offset,
        } = outcome
        else {
            panic!("complete window should be ready");
        };
        assert_eq!(entries.len(), 2);
        assert_eq!(next_offset, file.as_file().metadata().unwrap().len());
    }

    #[test]
    fn remaining_beyond_soft_budget_still_makes_complete_record_progress() {
        let mut transcript = tempfile::NamedTempFile::new().unwrap();
        for index in 0..32 {
            writeln!(
                transcript,
                "{}",
                serde_json::to_string(&json!({
                    "index": index,
                    "payload": "x".repeat(128),
                }))
                .unwrap()
            )
            .unwrap();
        }
        let file_len = transcript.as_file().metadata().unwrap().len();
        let outcome = read_bounded_transcript_window_with_limits(
            transcript.path(),
            0,
            512,
            Duration::from_millis(100),
        )
        .unwrap();
        let BoundedScanOutcome::Ready {
            entries,
            next_offset,
        } = outcome
        else {
            panic!("a soft budget must yield after complete records, not reject the backlog");
        };
        assert!(!entries.is_empty());
        assert!(next_offset > 0);
        assert!(next_offset < file_len);
    }

    #[test]
    fn one_complete_record_may_cross_the_soft_budget() {
        let mut transcript = tempfile::NamedTempFile::new().unwrap();
        writeln!(
            transcript,
            "{}",
            serde_json::to_string(&json!({ "payload": "x".repeat(2 * 1024 * 1024) })).unwrap()
        )
        .unwrap();
        let file_len = transcript.as_file().metadata().unwrap().len();
        let outcome = read_bounded_transcript_window_with_limits(
            transcript.path(),
            0,
            1024 * 1024,
            Duration::from_secs(1),
        )
        .unwrap();
        let BoundedScanOutcome::Ready {
            entries,
            next_offset,
        } = outcome
        else {
            panic!("one complete record may overflow the soft byte target");
        };
        assert_eq!(entries.len(), 1);
        assert_eq!(next_offset, file_len);
    }

    #[test]
    fn incomplete_live_record_waits_at_its_start() {
        let mut incomplete = tempfile::NamedTempFile::new().unwrap();
        write!(incomplete, r#"{{"type":"partial""#).unwrap();
        assert_eq!(
            read_bounded_transcript_window(incomplete.path(), 0).unwrap(),
            BoundedScanOutcome::WaitingForMoreData { record_start: 0 }
        );
    }

    #[test]
    fn record_beyond_hard_ceiling_is_finalizer_only() {
        let mut transcript = tempfile::NamedTempFile::new().unwrap();
        writeln!(
            transcript,
            "{}",
            serde_json::to_string(&json!({ "payload": "x".repeat(256) })).unwrap()
        )
        .unwrap();
        let outcome = read_bounded_transcript_window_with_all_limits(
            transcript.path(),
            0,
            32,
            Duration::from_secs(1),
            64,
        )
        .unwrap();
        assert!(matches!(
            outcome,
            BoundedScanOutcome::FinalizerOnly {
                reason: "record_too_large",
                record_start: 0,
                observed_bytes: 65,
            }
        ));
    }

    #[test]
    fn source_epoch_is_append_stable_and_detects_replacement() {
        let mut transcript = tempfile::NamedTempFile::new().unwrap();
        writeln!(transcript, "{}", "x".repeat(5000)).unwrap();
        let initial = inspect_transcript_source(transcript.path(), None).unwrap();
        writeln!(transcript, "append").unwrap();
        let appended =
            inspect_transcript_source(transcript.path(), Some(initial.epoch.prefix_len)).unwrap();
        assert_eq!(initial.epoch, appended.epoch);
        transcript.as_file_mut().set_len(0).unwrap();
        transcript.as_file_mut().seek(SeekFrom::Start(0)).unwrap();
        writeln!(transcript, "{}", "y".repeat(5000)).unwrap();
        let replaced =
            inspect_transcript_source(transcript.path(), Some(initial.epoch.prefix_len)).unwrap();
        assert_ne!(initial.epoch, replaced.epoch);
    }

    #[test]
    fn transcript_source_epoch_serde_shape_is_stable() {
        let epoch = TranscriptSourceEpoch {
            identity_digest: "digest-a".to_string(),
            prefix_len: 4,
        };
        assert_eq!(
            serde_json::to_string(&epoch).unwrap(),
            r#"{"identity_digest":"digest-a","prefix_len":4}"#
        );
    }

    #[test]
    fn transcript_source_epoch_round_trips_without_field_drift() {
        let encoded = json!({
            "identity_digest": "digest-a",
            "prefix_len": 4
        });
        let decoded: TranscriptSourceEpoch = serde_json::from_value(encoded.clone()).unwrap();
        assert_eq!(serde_json::to_value(decoded).unwrap(), encoded);
    }

    #[test]
    fn zero_time_budget_defers_before_io() {
        let file = tempfile::NamedTempFile::new().unwrap();
        assert_eq!(
            read_bounded_transcript_window_with_limits(
                file.path(),
                0,
                DEFAULT_GENERATION_SCAN_MAX_BYTES,
                Duration::ZERO
            )
            .unwrap(),
            BoundedScanOutcome::Deferred {
                reason: "scan_budget_exhausted"
            }
        );
    }
}
