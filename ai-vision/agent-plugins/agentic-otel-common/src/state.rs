use serde_json::{json, Value};
use std::fs;
use std::fs::OpenOptions;
use std::io::{self, Write};
#[cfg(unix)]
use std::os::unix::fs::OpenOptionsExt;
use std::path::Path;
use std::time::Duration;
#[cfg(any(unix, windows))]
use std::time::Instant;

// Windows minifilters may briefly reopen a freshly replaced state file
// without delete sharing. Keep the update atomic and absorb that bounded
// contention instead of falling back to an observable truncate/write window.
// The total delay is 505ms and is paid only after retryable rename failures.
const REPLACE_RETRY_DELAYS_MS: [u64; 10] = [0, 1, 2, 4, 8, 16, 32, 64, 128, 250];

#[cfg(unix)]
use std::os::fd::AsRawFd;
#[cfg(windows)]
use std::os::windows::io::AsRawHandle;

#[cfg(windows)]
#[repr(C)]
struct WindowsOverlapped {
    internal: usize,
    internal_high: usize,
    offset: u32,
    offset_high: u32,
    h_event: *mut std::ffi::c_void,
}

#[cfg(windows)]
impl Default for WindowsOverlapped {
    fn default() -> Self {
        Self {
            internal: 0,
            internal_high: 0,
            offset: 0,
            offset_high: 0,
            h_event: std::ptr::null_mut(),
        }
    }
}

#[cfg(windows)]
#[link(name = "kernel32")]
extern "system" {
    fn LockFileEx(
        h_file: *mut std::ffi::c_void,
        flags: u32,
        reserved: u32,
        bytes_low: u32,
        bytes_high: u32,
        overlapped: *mut WindowsOverlapped,
    ) -> i32;
    fn UnlockFileEx(
        h_file: *mut std::ffi::c_void,
        reserved: u32,
        bytes_low: u32,
        bytes_high: u32,
        overlapped: *mut WindowsOverlapped,
    ) -> i32;
    fn MoveFileExW(existing: *const u16, replacement: *const u16, flags: u32) -> i32;
}

// ── File locking (Unix flock / Windows LockFileEx) ────────────────

/// A bounded exclusive lock on `<state_file>.lock`.
///
/// Unix uses `flock`, Windows uses `LockFileEx`. Unsupported targets retain
/// the legacy best-effort behavior so existing embedded users still compile.
pub struct StateLock {
    #[cfg(any(unix, windows))]
    _file: Option<fs::File>,
    #[cfg(windows)]
    overlapped: Option<Box<WindowsOverlapped>>,
    acquired: bool,
}

impl StateLock {
    /// Acquire an exclusive lock on `<state_file>.lock`.
    /// Times out after `timeout_ms` milliseconds. Callers must check
    /// `is_acquired()` and fail closed before reading or mutating shared state.
    pub fn acquire(state_file: &Path, timeout_ms: u64) -> Self {
        #[cfg(any(unix, windows))]
        {
            let lock_path = format!("{}.lock", state_file.display());
            if let Some(parent) = state_file.parent() {
                let _ = fs::create_dir_all(parent);
            }

            let mut options = OpenOptions::new();
            options.create(true).write(true);
            #[cfg(unix)]
            {
                options.mode(0o600);
            }
            let file = match options.open(&lock_path) {
                Ok(f) => f,
                Err(_) => {
                    return StateLock {
                        _file: None,
                        #[cfg(windows)]
                        overlapped: None,
                        acquired: false,
                    }
                }
            };

            #[cfg(windows)]
            let mut overlapped = Box::new(WindowsOverlapped::default());
            let deadline = Instant::now() + Duration::from_millis(timeout_ms);
            loop {
                #[cfg(unix)]
                {
                    let result =
                        unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) };
                    if result == 0 {
                        return StateLock {
                            _file: Some(file),
                            acquired: true,
                        };
                    }
                    let error = std::io::Error::last_os_error();
                    if !matches!(
                        error.raw_os_error(),
                        Some(code) if code == libc::EWOULDBLOCK || code == libc::EAGAIN
                    ) {
                        return StateLock {
                            _file: Some(file),
                            acquired: false,
                        };
                    }
                }
                #[cfg(windows)]
                {
                    let result = unsafe {
                        const LOCKFILE_EXCLUSIVE_LOCK: u32 = 0x0000_0002;
                        const LOCKFILE_FAIL_IMMEDIATELY: u32 = 0x0000_0001;
                        LockFileEx(
                            file.as_raw_handle(),
                            LOCKFILE_EXCLUSIVE_LOCK | LOCKFILE_FAIL_IMMEDIATELY,
                            0,
                            u32::MAX,
                            u32::MAX,
                            overlapped.as_mut(),
                        )
                    };
                    if result != 0 {
                        return StateLock {
                            _file: Some(file),
                            overlapped: Some(overlapped),
                            acquired: true,
                        };
                    }
                    let error = std::io::Error::last_os_error();
                    if !matches!(error.raw_os_error(), Some(32 | 33 | 158 | 997)) {
                        return StateLock {
                            _file: Some(file),
                            overlapped: Some(overlapped),
                            acquired: false,
                        };
                    }
                }
                if Instant::now() >= deadline {
                    return StateLock {
                        _file: Some(file),
                        #[cfg(windows)]
                        overlapped: Some(overlapped),
                        acquired: false,
                    };
                }
                std::thread::sleep(Duration::from_millis(50));
            }
        }

        #[cfg(not(any(unix, windows)))]
        {
            StateLock { acquired: true }
        }
    }

    pub fn is_acquired(&self) -> bool {
        self.acquired
    }
}

/// Atomically replace `destination` with `source` on supported platforms.
///
/// `std::fs::rename` does not replace an existing destination on Windows, so
/// state/offset/queue writers must use the native replace flag there.
pub fn replace_file(source: &Path, destination: &Path) -> io::Result<()> {
    #[cfg(windows)]
    {
        use std::iter::once;
        use std::os::windows::ffi::OsStrExt;

        const MOVEFILE_REPLACE_EXISTING: u32 = 0x0000_0001;
        const MOVEFILE_WRITE_THROUGH: u32 = 0x0000_0008;
        let source = source
            .as_os_str()
            .encode_wide()
            .chain(once(0))
            .collect::<Vec<_>>();
        let destination = destination
            .as_os_str()
            .encode_wide()
            .chain(once(0))
            .collect::<Vec<_>>();
        retry_replace_operation(
            || {
                let result = unsafe {
                    MoveFileExW(
                        source.as_ptr(),
                        destination.as_ptr(),
                        MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
                    )
                };
                if result != 0 {
                    Ok(())
                } else {
                    Err(io::Error::last_os_error())
                }
            },
            is_retryable_replace_error,
        )
    }

    #[cfg(not(windows))]
    {
        retry_replace_operation(
            || fs::rename(source, destination),
            is_retryable_replace_error,
        )
    }
}

fn retry_replace_operation(
    mut operation: impl FnMut() -> io::Result<()>,
    is_retryable: impl Fn(&io::Error) -> bool,
) -> io::Result<()> {
    let mut retry_index = 0usize;
    loop {
        match operation() {
            Ok(()) => return Ok(()),
            Err(error) if is_retryable(&error) && retry_index < REPLACE_RETRY_DELAYS_MS.len() => {
                let delay_ms = REPLACE_RETRY_DELAYS_MS[retry_index];
                retry_index += 1;
                if delay_ms == 0 {
                    std::thread::yield_now();
                } else {
                    std::thread::sleep(Duration::from_millis(delay_ms));
                }
            }
            Err(error) => return Err(error),
        }
    }
}

#[cfg(windows)]
fn is_retryable_replace_error(error: &io::Error) -> bool {
    matches!(error.raw_os_error(), Some(5 | 32 | 33))
}

#[cfg(unix)]
fn is_retryable_replace_error(error: &io::Error) -> bool {
    matches!(
        error.raw_os_error(),
        Some(code) if code == libc::EINTR || code == libc::EBUSY
    )
}

#[cfg(not(any(unix, windows)))]
fn is_retryable_replace_error(_error: &io::Error) -> bool {
    false
}

/// Atomically replace a file and persist the containing directory entry.
pub fn replace_file_durable(source: &Path, destination: &Path) -> io::Result<()> {
    replace_file(source, destination)?;
    sync_parent_directory(destination)
}

/// Persist the directory entry containing `path` after creating, replacing,
/// or removing a file. Windows replacements already use
/// `MOVEFILE_WRITE_THROUGH`; Unix requires an explicit directory fsync.
pub fn sync_parent_directory(path: &Path) -> io::Result<()> {
    #[cfg(unix)]
    {
        let parent = path
            .parent()
            .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "path has no parent"))?;
        let result = fs::File::open(parent)?.sync_all();
        #[cfg(target_os = "macos")]
        if result.as_ref().is_err_and(|error| {
            matches!(
                error.raw_os_error(),
                Some(code) if code == libc::EINVAL || code == libc::ENOTSUP
            )
        }) {
            // APFS/HFS may reject fsync on an opened directory even though the
            // same-directory rename is already atomically visible. The temp
            // file itself was synced before rename, so treat unsupported
            // directory sync as a platform capability rather than a failed
            // state transition.
            return Ok(());
        }
        result
    }

    #[cfg(not(unix))]
    {
        let _ = path;
        Ok(())
    }
}

#[cfg(any(unix, windows))]
impl Drop for StateLock {
    fn drop(&mut self) {
        if self.acquired {
            let Some(ref file) = self._file else {
                return;
            };
            #[cfg(unix)]
            unsafe {
                libc::flock(file.as_raw_fd(), libc::LOCK_UN);
            }
            #[cfg(windows)]
            if let Some(overlapped) = self.overlapped.as_mut() {
                unsafe {
                    let _ = UnlockFileEx(
                        file.as_raw_handle(),
                        0,
                        u32::MAX,
                        u32::MAX,
                        overlapped.as_mut(),
                    );
                }
            }
        }
    }
}

/// Load state from JSON file. Returns empty object on any error.
pub fn load_state(state_file: &Path) -> Value {
    let Ok(data) = fs::read_to_string(state_file) else {
        return json!({});
    };
    serde_json::from_str(&data).unwrap_or_else(|_| json!({}))
}

/// Save state to JSON file using atomic write (tmp → rename).
pub fn save_state(state_file: &Path, state: &Value) -> Result<(), String> {
    if let Some(parent) = state_file.parent() {
        fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    let data = serde_json::to_string_pretty(state).map_err(|e| e.to_string())?;
    let tmp_path = format!("{}.tmp", state_file.display());
    let mut file = OpenOptions::new();
    file.write(true).create(true).truncate(true);
    #[cfg(unix)]
    {
        file.mode(0o600);
    }
    let mut tmp = file.open(&tmp_path).map_err(|e| e.to_string())?;
    tmp.write_all(data.as_bytes()).map_err(|e| e.to_string())?;
    tmp.flush().map_err(|e| e.to_string())?;
    tmp.sync_all().map_err(|e| e.to_string())?;
    drop(tmp);
    replace_file(Path::new(&tmp_path), state_file).map_err(|e| e.to_string())
}

/// Get session-specific state object.
pub fn get_session_state<'a>(state: &'a Value, session_id: &str) -> Option<&'a Value> {
    state.get(session_id)
}

pub fn get_int_field(obj: &Value, key: &str) -> i64 {
    obj.get(key).and_then(|v| v.as_i64()).unwrap_or(0)
}

pub fn get_str_field<'a>(obj: &'a Value, key: &str) -> &'a str {
    obj.get(key).and_then(|v| v.as_str()).unwrap_or("")
}

pub fn get_array_field(obj: &Value, key: &str) -> Vec<Value> {
    obj.get(key)
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default()
}

pub fn get_string_array_field(obj: &Value, key: &str) -> Vec<String> {
    obj.get(key)
        .and_then(|v| v.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|v| v.as_str().map(|s| s.to_string()))
                .collect()
        })
        .unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::path::PathBuf;

    #[test]
    fn replace_retry_recovers_from_transient_failure() {
        let mut attempts = 0usize;
        retry_replace_operation(
            || {
                attempts += 1;
                if attempts == 1 {
                    Err(io::Error::from_raw_os_error(5))
                } else {
                    Ok(())
                }
            },
            |error| error.raw_os_error() == Some(5),
        )
        .unwrap();
        assert_eq!(attempts, 2);
    }

    #[test]
    fn replace_retry_does_not_retry_permanent_failure() {
        let mut attempts = 0usize;
        let error = retry_replace_operation(
            || {
                attempts += 1;
                Err(io::Error::from_raw_os_error(87))
            },
            |candidate| candidate.raw_os_error() == Some(5),
        )
        .unwrap_err();
        assert_eq!(attempts, 1);
        assert_eq!(error.raw_os_error(), Some(87));
    }

    #[test]
    fn test_load_state_nonexistent() {
        let path = PathBuf::from("/tmp/agentic_otel_test_nonexistent_state.json");
        let state = load_state(&path);
        assert!(state.is_object());
        assert_eq!(state, json!({}));
    }

    #[test]
    fn test_load_state_invalid_json() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("invalid.json");
        std::fs::write(&path, "not valid json").unwrap();
        let state = load_state(&path);
        assert_eq!(state, json!({}));
    }

    #[test]
    fn test_load_state_valid() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("valid.json");
        let expected = json!({"session1": {"offset": 100, "turn_count": 5}});
        std::fs::write(&path, serde_json::to_string(&expected).unwrap()).unwrap();
        let state = load_state(&path);
        assert_eq!(state, expected);
    }

    #[test]
    fn test_save_state_and_load() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("state.json");
        let state = json!({"session1": {"offset": 200, "buffer": "", "turn_count": 3}});
        save_state(&path, &state).unwrap();
        let loaded = load_state(&path);
        assert_eq!(loaded, state);
    }

    #[test]
    fn test_save_state_atomic() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("atomic.json");
        save_state(&path, &json!({"key": "first"})).unwrap();
        let state = json!({"key": "replacement"});
        save_state(&path, &state).unwrap();
        let tmp_path = format!("{}.tmp", path.display());
        assert!(!std::path::Path::new(&tmp_path).exists());
        assert!(path.exists());
        assert_eq!(load_state(&path), state);
    }

    #[test]
    fn test_get_session_state() {
        let state = json!({"sess-1": {"offset": 100}, "sess-2": {"offset": 200}});
        let s1 = get_session_state(&state, "sess-1");
        assert!(s1.is_some());
        assert_eq!(s1.unwrap()["offset"], 100);

        let s3 = get_session_state(&state, "sess-3");
        assert!(s3.is_none());
    }

    #[test]
    fn test_get_int_field() {
        let obj = json!({"count": 42, "name": "test"});
        assert_eq!(get_int_field(&obj, "count"), 42);
        assert_eq!(get_int_field(&obj, "missing"), 0);
        assert_eq!(get_int_field(&obj, "name"), 0);
    }

    #[test]
    fn test_get_str_field() {
        let obj = json!({"name": "hello", "count": 42});
        assert_eq!(get_str_field(&obj, "name"), "hello");
        assert_eq!(get_str_field(&obj, "missing"), "");
        assert_eq!(get_str_field(&obj, "count"), "");
    }

    #[test]
    fn test_get_array_field() {
        let obj = json!({"pending_messages": [{"type": "event_msg"}]});
        assert_eq!(
            get_array_field(&obj, "pending_messages"),
            vec![json!({"type": "event_msg"})]
        );
        assert!(get_array_field(&obj, "missing").is_empty());
    }

    #[test]
    fn test_get_string_array_field() {
        let obj = json!({"posted_turn_ids": ["turn-1", "turn-2", 3]});
        assert_eq!(
            get_string_array_field(&obj, "posted_turn_ids"),
            vec!["turn-1".to_string(), "turn-2".to_string()]
        );
        assert!(get_string_array_field(&obj, "missing").is_empty());
    }

    #[cfg(unix)]
    #[test]
    fn test_state_lock_timeout_reports_not_acquired() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("state.json");
        let first = StateLock::acquire(&path, 10);
        assert!(first.is_acquired());

        let second = StateLock::acquire(&path, 0);
        assert!(!second.is_acquired());

        drop(first);
        let third = StateLock::acquire(&path, 10);
        assert!(third.is_acquired());
    }

    #[cfg(unix)]
    #[test]
    fn test_new_state_and_lock_files_are_private() {
        use std::os::unix::fs::PermissionsExt;

        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("state.json");
        save_state(&path, &json!({"secret": true})).unwrap();
        let lock = StateLock::acquire(&path, 10);
        assert!(lock.is_acquired());

        assert_eq!(
            fs::metadata(&path).unwrap().permissions().mode() & 0o777,
            0o600
        );
        assert_eq!(
            fs::metadata(format!("{}.lock", path.display()))
                .unwrap()
                .permissions()
                .mode()
                & 0o777,
            0o600
        );
    }
}
