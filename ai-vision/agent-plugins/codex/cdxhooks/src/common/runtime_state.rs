use serde_json::{json, Map, Value};
use std::fs::{self, OpenOptions};
use std::io::{self, Seek, Write};
use std::path::{Path, PathBuf};
use std::time::Duration;

#[cfg(unix)]
use std::os::unix::fs::OpenOptionsExt;

use crate::common::{dirs, file_lock::ExclusiveFileLock};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StateDomain {
    Stop,
    Turn,
    Generation,
    Resource,
    Tools,
    Attribution,
}

impl StateDomain {
    pub const ALL: [Self; 6] = [
        Self::Stop,
        Self::Turn,
        Self::Generation,
        Self::Resource,
        Self::Tools,
        Self::Attribution,
    ];

    pub fn file_name(self) -> &'static str {
        match self {
            Self::Stop => "state_stop.json",
            Self::Turn => "state_turn.json",
            Self::Generation => "state_generation.json",
            Self::Resource => "state_resource.json",
            Self::Tools => "state_tools.json",
            Self::Attribution => "state_attribution.json",
        }
    }

    pub fn legacy_fields(self) -> &'static [&'static str] {
        match self {
            Self::Stop => &[
                "offset",
                "buffer",
                "pending_messages",
                "turn_count",
                "updated",
                "posted_turn_ids",
                "pending_export_reason",
                "pending_window_end_offset",
                "pending_final_export",
                "pending_subagent_ids",
                "merged_subagent_ids",
                "merged_subagent_invocation_ids",
                "terminal_export_error",
                "quarantined_turns",
                "export_accounting",
                "transcript_path",
                "parent_stopped",
                "subagent_progress",
            ],
            Self::Turn => &[
                "current_trace_context",
                "trace_context_history",
                "turn_billing_modes",
                "turn_id",
                "trace_id",
                "upstream_traceparent",
                "created_at",
            ],
            Self::Generation => &["realtime_export"],
            Self::Resource => &["vcs"],
            Self::Tools => &[
                "timing",
                "events",
                "records",
                "tool_cutover",
                "terminal_tombstones",
            ],
            Self::Attribution => &[],
        }
    }

    pub fn for_field(field: &str) -> Self {
        Self::ALL
            .into_iter()
            .find(|domain| domain.legacy_fields().contains(&field))
            .unwrap_or(Self::Attribution)
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ImmutablePublish {
    Created,
    ExistingIdentical,
}

#[derive(Debug, Clone)]
pub struct DomainStateStore {
    session_id: String,
    domain: StateDomain,
    state_file: PathBuf,
    legacy_file: PathBuf,
}

impl DomainStateStore {
    pub fn for_session(session_id: &str, domain: StateDomain) -> Result<Self, String> {
        let state_file = dirs::session_domain_state_file(session_id, domain.file_name())
            .ok_or_else(|| "missing session id".to_string())?;
        let legacy_file = dirs::legacy_session_state_file(session_id)
            .ok_or_else(|| "missing session id".to_string())?;
        Ok(Self {
            session_id: session_id.to_string(),
            domain,
            state_file,
            legacy_file,
        })
    }

    /// Build a domain store next to an existing composite compatibility path.
    /// This keeps tests and legacy CLI callers isolated while production uses
    /// the same flat session directory as `state_compat.json`.
    pub fn for_compat_state_file(
        session_id: &str,
        domain: StateDomain,
        compat_state_file: &Path,
    ) -> Result<Self, String> {
        let parent = compat_state_file
            .parent()
            .filter(|path| !path.as_os_str().is_empty())
            .ok_or_else(|| "compat state path has no parent".to_string())?;
        let legacy_file = if compat_state_file.file_name().and_then(|name| name.to_str())
            == Some("state_compat.json")
        {
            dirs::legacy_session_state_file(session_id)
                .ok_or_else(|| "missing session id".to_string())?
        } else {
            compat_state_file.to_path_buf()
        };
        Ok(Self {
            session_id: session_id.to_string(),
            domain,
            state_file: parent.join(domain.file_name()),
            legacy_file,
        })
    }

    pub fn state_file(&self) -> &Path {
        &self.state_file
    }

    pub fn load(&self) -> Result<Value, String> {
        if self.state_file.exists() {
            return read_object_or_empty(&self.state_file);
        }
        let legacy = match fs::read_to_string(&self.legacy_file) {
            Ok(contents) => contents,
            Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(json!({})),
            Err(error) => return Err(format!("{}: {error}", self.legacy_file.display())),
        };
        if legacy.trim().is_empty() {
            return Ok(json!({}));
        }
        let legacy: Value = serde_json::from_str(&legacy)
            .map_err(|error| format!("{}: {error}", self.legacy_file.display()))?;
        let session = legacy
            .get(&self.session_id)
            .filter(|value| value.is_object())
            .cloned()
            .unwrap_or_else(|| json!({}));
        Ok(extract_legacy_domain(&session, self.domain))
    }

    /// Update one worker-owned domain and write only when its value changed.
    ///
    /// Callers must hold the per-session `SessionOwner`; this store deliberately
    /// has no per-domain lock.
    pub fn update<R>(
        &self,
        update: impl FnOnce(&mut Value) -> Result<R, String>,
    ) -> Result<(R, bool), String> {
        let mut state = self.load()?;
        let before = state.clone();
        let result = update(&mut state)?;
        if state == before {
            return Ok((result, false));
        }
        if !state.is_object() {
            return Err(format!(
                "{}: top-level domain state must be an object",
                self.state_file.display()
            ));
        }
        write_json_atomically(&self.state_file, &state)?;
        Ok((result, true))
    }
}

pub fn write_json_atomically(path: &Path, value: &Value) -> Result<(), String> {
    let parent = path
        .parent()
        .ok_or_else(|| format!("{} has no parent", path.display()))?;
    dirs::ensure_private_dir(parent).map_err(|error| error.to_string())?;
    let bytes = serde_json::to_vec(value).map_err(|error| error.to_string())?;
    let temporary = temporary_path(path);
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    options.mode(0o600);
    let mut file = options
        .open(&temporary)
        .map_err(|error| format!("{}: {error}", temporary.display()))?;
    let write_result = (|| -> io::Result<()> {
        file.write_all(&bytes)?;
        file.flush()?;
        file.sync_all()?;
        drop(file);
        agentic_otel_common::state::replace_file_durable(&temporary, path)
    })();
    if let Err(error) = write_result {
        let _ = fs::remove_file(&temporary);
        return Err(format!("{}: {error}", path.display()));
    }
    Ok(())
}

pub fn publish_immutable_json(path: &Path, value: &Value) -> Result<ImmutablePublish, String> {
    if path.exists() {
        return compare_existing(path, value);
    }
    let parent = path
        .parent()
        .ok_or_else(|| format!("{} has no parent", path.display()))?;
    dirs::ensure_private_dir(parent).map_err(|error| error.to_string())?;
    let bytes = serde_json::to_vec(value).map_err(|error| error.to_string())?;
    let temporary = temporary_path(path);
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    options.mode(0o600);
    let mut file = options
        .open(&temporary)
        .map_err(|error| format!("{}: {error}", temporary.display()))?;
    if let Err(error) = file
        .write_all(&bytes)
        .and_then(|_| file.flush())
        .and_then(|_| file.sync_all())
    {
        let _ = fs::remove_file(&temporary);
        return Err(format!("{}: {error}", path.display()));
    }
    drop(file);
    match fs::hard_link(&temporary, path) {
        Ok(()) => {
            let _ = fs::remove_file(&temporary);
            agentic_otel_common::state::sync_parent_directory(path)
                .map_err(|error| format!("{}: {error}", path.display()))?;
            Ok(ImmutablePublish::Created)
        }
        Err(error) if error.kind() == io::ErrorKind::AlreadyExists => {
            let _ = fs::remove_file(&temporary);
            compare_existing(path, value)
        }
        Err(error) => {
            let _ = fs::remove_file(&temporary);
            Err(format!("{}: {error}", path.display()))
        }
    }
}

fn compare_existing(path: &Path, value: &Value) -> Result<ImmutablePublish, String> {
    let existing = read_object_or_empty(path)?;
    if &existing == value {
        Ok(ImmutablePublish::ExistingIdentical)
    } else {
        Err(format!("immutable state conflict: {}", path.display()))
    }
}

fn read_object_or_empty(path: &Path) -> Result<Value, String> {
    let contents = match fs::read_to_string(path) {
        Ok(contents) => contents,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(json!({})),
        Err(error) => return Err(format!("{}: {error}", path.display())),
    };
    if contents.trim().is_empty() {
        return Ok(json!({}));
    }
    let value: Value =
        serde_json::from_str(&contents).map_err(|error| format!("{}: {error}", path.display()))?;
    if !value.is_object() {
        return Err(format!("{}: expected JSON object", path.display()));
    }
    Ok(value)
}

fn temporary_path(path: &Path) -> PathBuf {
    let name = path
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or("state.json");
    path.with_file_name(format!(
        ".{name}.{}.{}.tmp",
        std::process::id(),
        agentic_otel_common::otel_span_id()
    ))
}

fn extract_legacy_domain(session: &Value, domain: StateDomain) -> Value {
    let mut output = Map::new();
    if domain == StateDomain::Attribution {
        if let Some(session) = session.as_object() {
            for (field, value) in session {
                let known = StateDomain::ALL
                    .iter()
                    .any(|candidate| candidate.legacy_fields().contains(&field.as_str()));
                if !known {
                    output.insert(field.clone(), value.clone());
                }
            }
        }
        return Value::Object(output);
    }
    for field in domain.legacy_fields() {
        if let Some(value) = session.get(*field) {
            output.insert((*field).to_string(), value.clone());
        }
    }
    Value::Object(output)
}

const SESSION_OWNER_WAIT: Duration = Duration::from_secs(30);

pub struct SessionOwner {
    // Holds the exclusive worker lock for the session; released on drop.
    #[allow(dead_code)]
    lock: ExclusiveFileLock,
}

impl SessionOwner {
    pub fn acquire(session_id: &str, timeout: Duration) -> Result<Self, String> {
        let path = dirs::session_worker_lock_file(session_id)
            .ok_or_else(|| "missing session id".to_string())?;
        Self::acquire_path(path, timeout)
    }

    /// Acquire the per-session worker ownership with a bounded default wait.
    /// No caller can remain blocked forever on a stale worker.
    pub fn acquire_blocking(session_id: &str) -> Result<Self, String> {
        let path = dirs::session_worker_lock_file(session_id)
            .ok_or_else(|| "missing session id".to_string())?;
        Self::acquire_path(path, SESSION_OWNER_WAIT)
    }

    /// Try to become the sole worker for this session without waiting.
    ///
    /// Hook parents use this before spawning so a busy session appends to its
    /// durable queue without creating another blocked operating-system process.
    pub fn try_acquire(session_id: &str) -> Result<Option<Self>, String> {
        let path = dirs::session_worker_lock_file(session_id)
            .ok_or_else(|| "missing session id".to_string())?;
        match ExclusiveFileLock::try_acquire(&path)? {
            Some(lock) => Ok(Some(Self::record_owner(lock, path)?)),
            None => Ok(None),
        }
    }

    fn acquire_path(path: PathBuf, timeout: Duration) -> Result<Self, String> {
        let lock = ExclusiveFileLock::acquire(&path, timeout)?;
        Self::record_owner(lock, path)
    }

    fn record_owner(mut lock: ExclusiveFileLock, path: PathBuf) -> Result<Self, String> {
        lock.file_mut()
            .set_len(0)
            .and_then(|_| lock.file_mut().rewind())
            .and_then(|_| {
                lock.file_mut()
                    .write_all(std::process::id().to_string().as_bytes())
            })
            .map_err(|error| format!("{}: {error}", path.display()))?;
        Ok(Self { lock })
    }
}
