use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::SystemTime;

#[cfg(unix)]
use std::os::unix::fs::OpenOptionsExt;

const LOG_MAX_BYTES: u64 = 10 * 1024 * 1024;
const LOG_BACKUP_COUNT: u32 = 10;

/// Process-wide lock serializing all log writes and rotation.
///
/// `Logger` is `Clone` and may be shared across the concurrent send threads
/// spawned during the hook's deferred-flush phase. Without serialization, two
/// threads can (a) interleave the multiple `write()` syscalls of a single
/// `writeln!`, garbling lines, or (b) both pass the rotation size check and
/// race the backup renames, losing the active log. Holding this lock across the
/// whole rotation-and-write critical section prevents both. Logging is
/// low-volume and off the hot compute path, so global serialization is cheap.
static LOG_LOCK: Mutex<()> = Mutex::new(());

#[derive(Clone)]
pub struct Logger {
    log_file: PathBuf,
    pub debug_enabled: bool,
    /// Optional hook event name; when set, every line includes `[{event}]`
    /// after the level. `None` (the default) omits it.
    event: Option<String>,
}

impl Logger {
    pub fn new(log_file: PathBuf, debug_enabled: bool) -> Self {
        Self {
            log_file,
            debug_enabled,
            event: None,
        }
    }

    /// Redirect output to a different log file (e.g. a per-session file once the
    /// session id is known). Consumes and returns self for chaining; preserves
    /// `debug_enabled` and any attached `event`.
    pub fn with_log_file(mut self, log_file: PathBuf) -> Self {
        self.log_file = log_file;
        self
    }

    /// Attach the current hook event name so subsequent lines are tagged
    /// `[{event}]` (after the level). Consumes and returns self.
    pub fn with_event(mut self, event: impl Into<String>) -> Self {
        let ev = event.into();
        self.event = if ev.is_empty() { None } else { Some(ev) };
        self
    }

    pub fn log(&self, level: &str, message: &str) {
        // Local-time (UTC+8) timestamp for human-readable log output only.
        // Data paths (OTLP/Langfuse turn timestamps) keep UTC ISO8601 via
        // `now_iso8601_pub()` — do NOT route those through this formatter.
        let timestamp = now_local_log_timestamp();
        let mut line = format!("{} [{}]", timestamp, level);
        if let Some(ev) = &self.event {
            line.push_str(&format!(" [{}]", ev));
        }
        line.push(' ');
        line.push_str(message);
        self.log_raw(&line);
    }

    /// Write a single pre-formatted line under LOG_LOCK + rotation. Splits the
    /// rotation-and-append critical section out of `log` so all writers share
    /// one serialization point regardless of how the line was formatted.
    pub fn log_raw(&self, line: &str) {
        if crate::privacy::private_process() { return; }
        if let Some(parent) = self.log_file.parent() {
            let _ = fs::create_dir_all(parent);
        }

        // Serialize rotation + write across threads. Recover from a poisoned
        // lock rather than dropping the line — the guarded data is just `()`.
        let _guard = LOG_LOCK.lock().unwrap_or_else(|e| e.into_inner());

        rotate_log_if_needed(&self.log_file);

        let mut options = OpenOptions::new();
        options.append(true).create(true);
        #[cfg(unix)]
        {
            options.mode(0o600);
        }
        let Ok(mut file) = options.open(&self.log_file) else {
            return;
        };

        let _ = writeln!(file, "{}", line);
    }

    pub fn debug(&self, message: &str) {
        if self.debug_enabled {
            self.log("DEBUG", message);
        }
    }

    pub fn info(&self, message: &str) {
        self.log("INFO", message);
    }

    pub fn warn(&self, message: &str) {
        self.log("WARN", message);
    }

    pub fn error(&self, message: &str) {
        self.log("ERROR", message);
    }

    #[allow(dead_code)]
    pub fn error_stderr(&self, message: &str) {
        self.log("ERROR", message);
        eprintln!("{}", message);
    }
}

fn rotate_log_if_needed(log_file: &Path) {
    let Ok(metadata) = fs::metadata(log_file) else {
        return;
    };
    if metadata.len() < LOG_MAX_BYTES {
        return;
    }

    // Remove oldest backup
    let oldest = format!("{}.{}", log_file.display(), LOG_BACKUP_COUNT);
    let _ = fs::remove_file(&oldest);

    // Shift existing backups
    for index in (1..LOG_BACKUP_COUNT).rev() {
        let old_path = format!("{}.{}", log_file.display(), index);
        let new_path = format!("{}.{}", log_file.display(), index + 1);
        let _ = fs::rename(&old_path, &new_path);
    }

    // Rotate current log to .1
    let rotated = format!("{}.1", log_file.display());
    let _ = fs::rename(log_file, &rotated);
}

fn now_iso8601() -> String {
    use std::time::UNIX_EPOCH;
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    // Format as UTC ISO 8601 using manual calculation (no chrono dependency)
    let (year, month, day, hour, minute, second) = unix_secs_to_datetime(secs);
    format!(
        "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}Z",
        year, month, day, hour, minute, second
    )
}

/// Offset applied to log-display timestamps (China Standard Time, UTC+8).
const LOG_TZ_OFFSET_SECS: u64 = 8 * 3600;

/// Human-readable local-time (UTC+8) timestamp for LOG OUTPUT ONLY:
/// `YYYY-MM-DD HH:MM:SS`. Do not use for data paths (OTLP/Langfuse turn
/// timestamps) — those must stay UTC ISO 8601 via `now_iso8601_pub()`.
fn now_local_log_timestamp() -> String {
    use std::time::UNIX_EPOCH;
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
        + LOG_TZ_OFFSET_SECS;
    let (year, month, day, hour, minute, second) = unix_secs_to_datetime(secs);
    format!(
        "{:04}-{:02}-{:02} {:02}:{:02}:{:02}",
        year, month, day, hour, minute, second
    )
}

fn unix_secs_to_datetime(secs: u64) -> (u64, u64, u64, u64, u64, u64) {
    let second = secs % 60;
    let minutes = secs / 60;
    let minute = minutes % 60;
    let hours = minutes / 60;
    let hour = hours % 24;
    let days = hours / 24;

    // Calculate year/month/day from days since epoch (1970-01-01)
    let mut year = 1970u64;
    let mut remaining_days = days;

    loop {
        let days_in_year = if is_leap_year(year) { 366 } else { 365 };
        if remaining_days < days_in_year {
            break;
        }
        remaining_days -= days_in_year;
        year += 1;
    }

    let month_days: [u64; 12] = if is_leap_year(year) {
        [31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    } else {
        [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    };

    let mut month = 1u64;
    for &days_in_month in &month_days {
        if remaining_days < days_in_month {
            break;
        }
        remaining_days -= days_in_month;
        month += 1;
    }

    let day = remaining_days + 1;
    (year, month, day, hour, minute, second)
}

fn is_leap_year(year: u64) -> bool {
    (year % 4 == 0 && year % 100 != 0) || (year % 400 == 0)
}

pub fn now_iso8601_pub() -> String {
    now_iso8601()
}

/// Check if a string looks like an ISO 8601 timestamp
pub fn looks_like_iso_timestamp(s: &str) -> bool {
    if s.len() < 20 {
        return false;
    }
    let bytes = s.as_bytes();
    if bytes[4] != b'-' || bytes[7] != b'-' {
        return false;
    }
    if !s.contains('T') || !s.contains(':') {
        return false;
    }
    bytes[..4].iter().all(|b| b.is_ascii_digit())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_with_log_file_redirects_and_event_tag_no_sid() {
        // with_log_file switches the output path; with_event tags lines with
        // [event]; lines must NOT contain any session-id tag (that concept is
        // gone — session is encoded in the filename instead).
        let dir = std::env::temp_dir().join(format!(
            "cchooks-logfile-redirect-{}",
            SystemTime::now()
                .duration_since(SystemTime::UNIX_EPOCH)
                .unwrap_or_default()
                .as_nanos()
        ));
        let default_file = dir.join("default.log");
        let session_file = dir.join("cchooks_sess-123.log");

        let logger = Logger::new(default_file.clone(), true)
            .with_log_file(session_file.clone())
            .with_event("Stop");
        logger.info("hello");

        // Output went to the redirected file, not the original.
        let data = fs::read_to_string(&session_file).expect("session log should exist");
        assert!(
            !default_file.exists(),
            "must not write to the pre-redirect path"
        );
        assert_eq!(data.lines().count(), 1);
        let line = data.lines().next().unwrap();
        assert!(line.contains(" [INFO] "), "level tag present: {:?}", line);
        assert!(line.contains(" [Stop] "), "event tag present: {:?}", line);
        assert!(line.ends_with(" hello"), "message present: {:?}", line);

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_concurrent_logging_lines_intact() {
        // Each Logger clone writes to the same file from many threads. With the
        // process-wide LOG_LOCK, every line must land whole and uncorrupted.
        let dir = std::env::temp_dir().join(format!(
            "cchooks-log-concurrency-{}",
            SystemTime::now()
                .duration_since(SystemTime::UNIX_EPOCH)
                .unwrap_or_default()
                .as_nanos()
        ));
        let log_file = dir.join("concurrent.log");
        let logger = Logger::new(log_file.clone(), true);

        const THREADS: usize = 8;
        const LINES_PER_THREAD: usize = 200;

        std::thread::scope(|scope| {
            for t in 0..THREADS {
                let l = logger.clone();
                scope.spawn(move || {
                    for i in 0..LINES_PER_THREAD {
                        // Distinct, long-ish payload to stress sub-line interleaving.
                        l.info(&format!(
                            "thread={} seq={} payload={}",
                            t,
                            i,
                            "x".repeat(256)
                        ));
                    }
                });
            }
        });

        let data = fs::read_to_string(&log_file).expect("log file should exist");
        let lines: Vec<&str> = data.lines().filter(|l| !l.is_empty()).collect();

        assert_eq!(
            lines.len(),
            THREADS * LINES_PER_THREAD,
            "every log line must be present exactly once (no lost or split lines)"
        );
        // No line may contain a second timestamp/level marker, which would
        // indicate two writes interleaved into one physical line.
        for line in &lines {
            assert!(
                line.matches(" [INFO] ").count() == 1,
                "line shows interleaved writes: {:?}",
                line
            );
        }

        let _ = fs::remove_dir_all(&dir);
    }

    #[cfg(unix)]
    #[test]
    fn test_new_log_file_is_private() {
        use std::os::unix::fs::PermissionsExt;

        let dir = tempfile::tempdir().unwrap();
        let log_file = dir.path().join("private.log");
        Logger::new(log_file.clone(), false).info("secret");

        assert_eq!(
            fs::metadata(log_file).unwrap().permissions().mode() & 0o777,
            0o600
        );
    }

    #[test]
    fn test_looks_like_iso_timestamp_valid() {
        assert!(looks_like_iso_timestamp("2026-03-27T14:30:00.123Z"));
        assert!(looks_like_iso_timestamp("2024-01-01T00:00:00Z"));
        assert!(looks_like_iso_timestamp("1970-01-01T00:00:00.000Z"));
    }

    #[test]
    fn test_looks_like_iso_timestamp_invalid() {
        assert!(!looks_like_iso_timestamp("not a timestamp"));
        assert!(!looks_like_iso_timestamp("2026/03/27 14:30:00"));
        assert!(!looks_like_iso_timestamp("hello world this is long enough"));
    }

    #[test]
    fn test_looks_like_iso_timestamp_too_short() {
        assert!(!looks_like_iso_timestamp("2026"));
        assert!(!looks_like_iso_timestamp(""));
        assert!(!looks_like_iso_timestamp("short"));
    }

    #[test]
    fn test_now_iso8601_format() {
        let result = now_iso8601_pub();
        assert!(result.len() >= 20, "ISO 8601 string too short: {}", result);
        assert!(result.contains('T'), "Missing T separator: {}", result);
        assert!(result.ends_with('Z'), "Missing Z suffix: {}", result);
        assert_eq!(result.as_bytes()[4], b'-');
        assert_eq!(result.as_bytes()[7], b'-');
    }

    #[test]
    fn test_unix_secs_to_datetime_epoch() {
        let (year, month, day, hour, minute, second) = unix_secs_to_datetime(0);
        assert_eq!(
            (year, month, day, hour, minute, second),
            (1970, 1, 1, 0, 0, 0)
        );
    }

    #[test]
    fn test_unix_secs_to_datetime_known_date() {
        // 2024-01-01T00:00:00Z = 1704067200 seconds since epoch
        let (year, month, day, hour, minute, second) = unix_secs_to_datetime(1704067200);
        assert_eq!(year, 2024);
        assert_eq!(month, 1);
        assert_eq!(day, 1);
        assert_eq!(hour, 0);
        assert_eq!(minute, 0);
        assert_eq!(second, 0);
    }

    #[test]
    fn test_is_leap_year() {
        assert!(is_leap_year(2000)); // divisible by 400
        assert!(is_leap_year(2024)); // divisible by 4, not by 100
        assert!(!is_leap_year(1900)); // divisible by 100, not by 400
        assert!(!is_leap_year(2023)); // not divisible by 4
        assert!(is_leap_year(2400)); // divisible by 400
    }
}
