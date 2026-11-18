use serde_json::{json, Map, Value};
use std::fmt;
use std::path::{Path, PathBuf};

use crate::hooks::langfuse::state::{save_state, StateLock, StateTxnError};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StateDomain {
    Stop,
    Turn,
    Generation,
    Resource,
    Subagents,
    Digest,
    Attribution,
    Tools,
}

impl StateDomain {
    pub const ALL: [StateDomain; 8] = [
        StateDomain::Stop,
        StateDomain::Turn,
        StateDomain::Generation,
        StateDomain::Resource,
        StateDomain::Subagents,
        StateDomain::Digest,
        StateDomain::Attribution,
        StateDomain::Tools,
    ];

    pub fn file_name(self) -> &'static str {
        match self {
            StateDomain::Stop => "state_stop.json",
            StateDomain::Turn => "state_turn.json",
            StateDomain::Generation => "state_generation.json",
            StateDomain::Resource => "state_resource.json",
            StateDomain::Subagents => "state_subagents.json",
            StateDomain::Digest => "state_digest.json",
            StateDomain::Attribution => "state_attribution.json",
            StateDomain::Tools => "state_tools.json",
        }
    }

    pub(crate) fn legacy_fields(self) -> &'static [&'static str] {
        match self {
            StateDomain::Stop => &[
                "offset",
                "buffer",
                "turn_count",
                "updated",
                "posted_turn_ids",
                "pending_export_reason",
                "pending_window_end_offset",
                "pending_final_export",
                "stop_recovery",
                "transcript_path",
                "stop_failure_ts",
                "last_exported_turn_anchor",
                "compaction_count",
                "posted_compaction_ids",
                "cursor_transcript_path",
                "cursor_source_epoch",
                "active_turn_id",
            ],
            StateDomain::Turn => &[
                "current_trace_context",
                "trace_context_history",
                "turn_id",
                "trace_id",
                "upstream_traceparent",
                "prompt_timestamp",
                "prompt_digest",
                "transcript_boundary",
                "created_at",
            ],
            StateDomain::Generation => &["realtime_export"],
            StateDomain::Resource => &["vcs"],
            StateDomain::Subagents => &["subagents", "subagent_registry", "subagent_timing"],
            StateDomain::Digest => &["turn_digest"],
            StateDomain::Attribution => &[],
            StateDomain::Tools => &["timing"],
        }
    }
}

impl fmt::Display for StateDomain {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.file_name())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ToolHookPhase {
    Pre,
    Post,
}

impl ToolHookPhase {
    pub fn as_str(self) -> &'static str {
        match self {
            ToolHookPhase::Pre => "pre",
            ToolHookPhase::Post => "post",
        }
    }
}

#[derive(Debug, Clone)]
pub struct DomainStateStore {
    state_file: PathBuf,
    legacy_state_file: Option<PathBuf>,
    session_id: String,
    domain: StateDomain,
    lock_timeout_ms: u64,
}

impl DomainStateStore {
    pub fn for_session(
        session_id: &str,
        domain: StateDomain,
        lock_timeout_ms: u64,
    ) -> Result<Self, StateTxnError> {
        let state_file =
            crate::common::dirs::session_domain_state_file(session_id, domain.file_name())
                .ok_or_else(|| StateTxnError::Init("missing session_id".to_string()))?;
        crate::common::dirs::ensure_session_state_parent(&state_file)
            .map_err(|error| StateTxnError::Init(error.to_string()))?;
        Ok(Self::new(
            state_file,
            crate::common::dirs::session_state_file(session_id),
            session_id,
            domain,
            lock_timeout_ms,
        ))
    }

    pub fn new(
        state_file: PathBuf,
        legacy_state_file: Option<PathBuf>,
        session_id: &str,
        domain: StateDomain,
        lock_timeout_ms: u64,
    ) -> Self {
        Self {
            state_file,
            legacy_state_file,
            session_id: session_id.to_string(),
            domain,
            lock_timeout_ms,
        }
    }

    pub fn state_file(&self) -> &Path {
        &self.state_file
    }

    pub fn read<R>(&self, read: impl FnOnce(&Value) -> R) -> Result<R, StateTxnError> {
        let state_lock = self.acquire()?;
        let state = self.load_for_transaction()?;
        let result = read(&state);
        drop(state_lock);
        Ok(result)
    }

    pub fn update<R>(
        &self,
        update: impl FnOnce(&mut Value) -> Result<R, StateTxnError>,
    ) -> Result<R, StateTxnError> {
        let started = std::time::Instant::now();
        let state_lock = self.acquire()?;
        let mut state = self.load_for_transaction()?;
        let result = update(&mut state)?;
        save_state(&self.state_file, &state).map_err(StateTxnError::Write)?;
        drop(state_lock);
        crate::common::otel::observe_current_worker_phase(
            "checkpoint",
            started.elapsed().as_millis(),
        );
        Ok(result)
    }

    fn acquire(&self) -> Result<StateLock, StateTxnError> {
        let started = std::time::Instant::now();
        let state_lock = StateLock::acquire(&self.state_file, self.lock_timeout_ms);
        crate::common::otel::observe_current_worker_phase("lock", started.elapsed().as_millis());
        if !state_lock.is_acquired() {
            return Err(StateTxnError::Busy {
                waited_ms: self.lock_timeout_ms,
            });
        }
        Ok(state_lock)
    }

    fn load_for_transaction(&self) -> Result<Value, StateTxnError> {
        if self.state_file.exists() {
            return load_new_domain_state(&self.state_file);
        }
        self.load_legacy_domain()
    }

    fn load_legacy_domain(&self) -> Result<Value, StateTxnError> {
        let Some(legacy_state_file) = self.legacy_state_file.as_deref() else {
            return Ok(json!({}));
        };
        let legacy = match std::fs::read_to_string(legacy_state_file) {
            Ok(contents) => contents,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(json!({})),
            Err(error) => {
                return Err(StateTxnError::Read(format!(
                    "{}: {error}",
                    legacy_state_file.display()
                )))
            }
        };
        if legacy.trim().is_empty() {
            return Ok(json!({}));
        }
        let legacy: Value = serde_json::from_str(&legacy).map_err(|error| {
            StateTxnError::Schema(format!(
                "{}: invalid JSON: {error}",
                legacy_state_file.display()
            ))
        })?;
        let session = legacy
            .get(&self.session_id)
            .filter(|value| value.is_object())
            .cloned()
            .unwrap_or_else(|| json!({}));
        Ok(extract_legacy_domain(&session, self.domain))
    }
}

#[derive(Debug, Clone)]
pub struct ToolHookStateStore {
    state_file: PathBuf,
    lock_timeout_ms: u64,
}

impl ToolHookStateStore {
    pub fn for_tool(
        session_id: &str,
        tool_id: &str,
        phase: ToolHookPhase,
        lock_timeout_ms: u64,
    ) -> Result<Self, StateTxnError> {
        let state_file =
            crate::common::dirs::tool_hook_state_file(session_id, tool_id, phase.as_str())
                .ok_or_else(|| StateTxnError::Init("missing session_id or tool_id".to_string()))?;
        crate::common::dirs::ensure_session_state_parent(&state_file)
            .map_err(|error| StateTxnError::Init(error.to_string()))?;
        Ok(Self::new(state_file, lock_timeout_ms))
    }

    pub fn new(state_file: PathBuf, lock_timeout_ms: u64) -> Self {
        Self {
            state_file,
            lock_timeout_ms,
        }
    }

    pub fn state_file(&self) -> &Path {
        &self.state_file
    }

    pub fn read<R>(&self, read: impl FnOnce(&Value) -> R) -> Result<R, StateTxnError> {
        let state_lock = self.acquire()?;
        let state = load_new_domain_state(&self.state_file)?;
        let result = read(&state);
        drop(state_lock);
        Ok(result)
    }

    pub fn update<R>(
        &self,
        update: impl FnOnce(&mut Value) -> Result<R, StateTxnError>,
    ) -> Result<R, StateTxnError> {
        let started = std::time::Instant::now();
        let state_lock = self.acquire()?;
        let mut state = load_new_domain_state(&self.state_file)?;
        let result = update(&mut state)?;
        save_state(&self.state_file, &state).map_err(StateTxnError::Write)?;
        drop(state_lock);
        crate::common::otel::observe_current_worker_phase(
            "checkpoint",
            started.elapsed().as_millis(),
        );
        Ok(result)
    }

    fn acquire(&self) -> Result<StateLock, StateTxnError> {
        let started = std::time::Instant::now();
        let state_lock = StateLock::acquire(&self.state_file, self.lock_timeout_ms);
        crate::common::otel::observe_current_worker_phase("lock", started.elapsed().as_millis());
        if !state_lock.is_acquired() {
            return Err(StateTxnError::Busy {
                waited_ms: self.lock_timeout_ms,
            });
        }
        Ok(state_lock)
    }
}

fn load_new_domain_state(state_file: &Path) -> Result<Value, StateTxnError> {
    let contents = match std::fs::read_to_string(state_file) {
        Ok(contents) => contents,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(json!({})),
        Err(error) => {
            return Err(StateTxnError::Read(format!(
                "{}: {error}",
                state_file.display()
            )))
        }
    };
    if contents.trim().is_empty() {
        return Ok(json!({}));
    }
    let state: Value = serde_json::from_str(&contents).map_err(|error| {
        StateTxnError::Schema(format!("{}: invalid JSON: {error}", state_file.display()))
    })?;
    if !state.is_object() {
        return Err(StateTxnError::Schema(format!(
            "{}: top-level state must be an object",
            state_file.display()
        )));
    }
    Ok(state)
}

pub(crate) fn extract_legacy_domain(session: &Value, domain: StateDomain) -> Value {
    let mut object = Map::new();
    if domain == StateDomain::Attribution {
        if let Some(session) = session.as_object() {
            for (field, value) in session {
                let known = field == "schema_version"
                    || StateDomain::ALL
                        .iter()
                        .any(|candidate| candidate.legacy_fields().contains(&field.as_str()));
                if !known {
                    object.insert(field.clone(), value.clone());
                }
            }
        }
        return Value::Object(object);
    }
    for field in domain.legacy_fields() {
        if let Some(value) = session.get(*field) {
            object.insert((*field).to_string(), value.clone());
        }
    }
    Value::Object(object)
}
