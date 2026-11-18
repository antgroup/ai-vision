//! Cross-platform exclusive file locks used by the session worker protocol.
//!
//! The lock is represented by an independently opened file handle. Worker
//! processes do not inherit this handle; parent and worker rendezvous through
//! a short-lived start marker.

use std::fs::{File, OpenOptions};
use std::io;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use crate::common::dirs;

#[cfg(unix)]
use std::os::fd::AsRawFd;
#[cfg(unix)]
use std::os::unix::fs::OpenOptionsExt;

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
}

#[derive(Debug)]
#[allow(dead_code)]
enum LockAttempt {
    Busy,
    Failed(String),
}

/// An exclusive lock held for the lifetime of an independently opened file.
#[allow(dead_code)]
pub(crate) struct ExclusiveFileLock {
    file: File,
    path: PathBuf,
    #[cfg(windows)]
    overlapped: Box<WindowsOverlapped>,
}

#[allow(dead_code)]
impl ExclusiveFileLock {
    pub(crate) fn acquire(path: &Path, timeout: Duration) -> Result<Self, String> {
        let mut lock = Self::open(path)?;
        let started = Instant::now();
        loop {
            match lock.try_lock() {
                Ok(()) => return Ok(lock),
                Err(LockAttempt::Failed(error)) => return Err(error),
                Err(LockAttempt::Busy) => {
                    if timeout.is_zero() || started.elapsed() >= timeout {
                        return Err(format!(
                            "file lock busy after {}ms: {}",
                            timeout.as_millis(),
                            path.display()
                        ));
                    }
                    let remaining = timeout.saturating_sub(started.elapsed());
                    std::thread::sleep(Duration::from_millis(10).min(remaining));
                }
            }
        }
    }

    pub(crate) fn try_acquire(path: &Path) -> Result<Option<Self>, String> {
        match Self::acquire(path, Duration::ZERO) {
            Ok(lock) => Ok(Some(lock)),
            Err(error) if error.starts_with("file lock busy") => Ok(None),
            Err(error) => Err(error),
        }
    }

    fn open(path: &Path) -> Result<Self, String> {
        dirs::ensure_session_state_parent(path).map_err(|error| error.to_string())?;
        let mut options = OpenOptions::new();
        options.read(true).write(true).create(true);
        #[cfg(unix)]
        options.mode(0o600);
        let file = options
            .open(path)
            .map_err(|error| format!("{}: {error}", path.display()))?;
        Ok(Self {
            file,
            path: path.to_path_buf(),
            #[cfg(windows)]
            overlapped: Box::new(WindowsOverlapped::default()),
        })
    }

    fn try_lock(&mut self) -> Result<(), LockAttempt> {
        #[cfg(unix)]
        {
            let result =
                unsafe { libc::flock(self.file.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) };
            if result == 0 {
                return Ok(());
            }
            let error = io::Error::last_os_error();
            if matches!(
                error.raw_os_error(),
                Some(code) if code == libc::EWOULDBLOCK || code == libc::EAGAIN
            ) {
                Err(LockAttempt::Busy)
            } else {
                Err(LockAttempt::Failed(format!(
                    "{}: {error}",
                    self.path.display()
                )))
            }
        }

        #[cfg(windows)]
        {
            const LOCKFILE_EXCLUSIVE_LOCK: u32 = 0x0000_0002;
            const LOCKFILE_FAIL_IMMEDIATELY: u32 = 0x0000_0001;
            let result = unsafe {
                LockFileEx(
                    self.file.as_raw_handle(),
                    LOCKFILE_EXCLUSIVE_LOCK | LOCKFILE_FAIL_IMMEDIATELY,
                    0,
                    u32::MAX,
                    u32::MAX,
                    self.overlapped.as_mut(),
                )
            };
            if result != 0 {
                return Ok(());
            }
            let error = io::Error::last_os_error();
            if matches!(error.raw_os_error(), Some(32 | 33 | 158 | 997)) {
                Err(LockAttempt::Busy)
            } else {
                Err(LockAttempt::Failed(format!(
                    "{}: {error}",
                    self.path.display()
                )))
            }
        }
    }
}

impl Drop for ExclusiveFileLock {
    fn drop(&mut self) {
        #[cfg(unix)]
        unsafe {
            // A concurrently forked process may inherit this open file
            // description. Unlock explicitly so dropping the owner cannot
            // leave the session lock held by that inherited descriptor.
            let _ = libc::flock(self.file.as_raw_fd(), libc::LOCK_UN);
        }
        #[cfg(windows)]
        unsafe {
            let _ = UnlockFileEx(
                self.file.as_raw_handle(),
                0,
                u32::MAX,
                u32::MAX,
                self.overlapped.as_mut(),
            );
        }
    }
}

#[cfg(test)]
mod tests {
    use super::ExclusiveFileLock;
    use std::time::Duration;

    #[test]
    fn independently_opened_handles_have_one_exclusive_owner() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("session.lock");
        let first = ExclusiveFileLock::acquire(&path, Duration::from_millis(100)).unwrap();
        assert!(ExclusiveFileLock::try_acquire(&path).unwrap().is_none());
        drop(first);
        assert!(ExclusiveFileLock::try_acquire(&path).unwrap().is_some());
    }
}
