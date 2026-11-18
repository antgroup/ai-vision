use serde_json::{json, Map, Value};
use std::collections::BTreeSet;
use std::fmt;
use std::path::{Path, PathBuf};

pub const PARTIAL_EXPORT_REASON: &str = "partial_export_in_progress";
const STATE_SHARD_SCHEMA_VERSION: u64 = 1;
const STATE_TRANSACTION_FILE: &str = "state_transaction.json";

pub use agentic_otel_common::state::{
    get_int_field, get_session_state, get_str_field, get_string_array_field, StateLock,
};

use crate::hooks::langfuse::domain_state::{extract_legacy_domain, DomainStateStore, StateDomain};

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum StateTxnError {
    Busy { waited_ms: u64 },
    Init(String),
    Read(String),
    Schema(String),
    Conflict(String),
    Write(String),
}

impl fmt::Display for StateTxnError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Busy { waited_ms } => {
                write!(formatter, "state lock unavailable after {waited_ms}ms")
            }
            Self::Init(message) => write!(formatter, "state initialization failed: {message}"),
            Self::Read(message) => write!(formatter, "state read failed: {message}"),
            Self::Schema(message) => write!(formatter, "state schema error: {message}"),
            Self::Conflict(message) => write!(formatter, "state conflict: {message}"),
            Self::Write(message) => write!(formatter, "state write failed: {message}"),
        }
    }
}

impl std::error::Error for StateTxnError {}

/// Session-owned state access. All reads and writes verify lock ownership.
#[derive(Debug, Clone)]
pub struct SessionStateStore {
    state_file: PathBuf,
    session_id: String,
    lock_timeout_ms: u64,
}

impl SessionStateStore {
    pub fn for_session(session_id: &str, lock_timeout_ms: u64) -> Result<Self, StateTxnError> {
        let state_file = crate::common::dirs::session_state_file(session_id)
            .ok_or_else(|| StateTxnError::Init("missing session_id".to_string()))?;
        let domain_file = crate::common::dirs::session_domain_state_file(
            session_id,
            StateDomain::Stop.file_name(),
        )
        .ok_or_else(|| StateTxnError::Init("missing session_id".to_string()))?;
        crate::common::dirs::ensure_session_state_parent(&domain_file)
            .map_err(|error| StateTxnError::Init(error.to_string()))?;
        Ok(Self::new(state_file, session_id, lock_timeout_ms))
    }

    pub fn new(state_file: PathBuf, session_id: &str, lock_timeout_ms: u64) -> Self {
        Self {
            state_file,
            session_id: session_id.to_string(),
            lock_timeout_ms,
        }
    }

    pub fn state_file(&self) -> &Path {
        &self.state_file
    }

    pub fn composite_lock_file(&self) -> Option<PathBuf> {
        self.runtime_layout()
            .map(|layout| layout.domain_dir.join(".state_transaction.lock"))
    }

    pub fn read<R>(&self, read: impl FnOnce(&Value) -> R) -> Result<R, StateTxnError> {
        let _state_lock = self.acquire()?;
        let state = self.load_for_transaction()?;
        let result = read(&state);
        Ok(result)
    }

    pub fn update<R>(
        &self,
        update: impl FnOnce(&mut Value) -> Result<R, StateTxnError>,
    ) -> Result<R, StateTxnError> {
        let started = std::time::Instant::now();
        let _state_lock = self.acquire()?;
        let base_state = self.load_for_transaction()?;
        let mut state = base_state.clone();
        let result = update(&mut state)?;
        self.persist_transaction_state(&base_state, &state)?;
        crate::common::otel::observe_current_worker_phase(
            "checkpoint",
            started.elapsed().as_millis(),
        );
        Ok(result)
    }

    pub fn update_session<R>(
        &self,
        update: impl FnOnce(&mut Value) -> Result<R, StateTxnError>,
    ) -> Result<R, StateTxnError> {
        let session_id = self.session_id.clone();
        self.update(|state| {
            let sessions = state.as_object_mut().ok_or_else(|| {
                StateTxnError::Schema("top-level state must be an object".to_string())
            })?;
            let session = sessions
                .entry(session_id.clone())
                .or_insert_with(|| json!({}));
            if !session.is_object() {
                return Err(StateTxnError::Schema(format!(
                    "session {session_id} must be an object"
                )));
            }
            update(session)
        })
    }

    fn acquire(&self) -> Result<StateLock, StateTxnError> {
        let started = std::time::Instant::now();
        let lock_file = self
            .composite_lock_file()
            .unwrap_or_else(|| self.state_file.clone());
        let state_lock = StateLock::acquire(&lock_file, self.lock_timeout_ms);
        crate::common::otel::observe_current_worker_phase("lock", started.elapsed().as_millis());
        if !state_lock.is_acquired() {
            return Err(StateTxnError::Busy {
                waited_ms: self.lock_timeout_ms,
            });
        }
        Ok(state_lock)
    }

    fn load_for_transaction(&self) -> Result<Value, StateTxnError> {
        if let Some(layout) = self.runtime_layout() {
            return load_runtime_composite_state(&layout, &self.session_id, self.lock_timeout_ms);
        }
        let contents = match std::fs::read_to_string(&self.state_file) {
            Ok(contents) => contents,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                return Ok(json!({}));
            }
            Err(error) => {
                return Err(StateTxnError::Read(format!(
                    "{}: {error}",
                    self.state_file.display()
                )));
            }
        };
        let state: Value = serde_json::from_str(&contents).map_err(|error| {
            StateTxnError::Schema(format!(
                "{}: invalid JSON: {error}",
                self.state_file.display()
            ))
        })?;
        if !state.is_object() {
            return Err(StateTxnError::Schema(format!(
                "{}: top-level state must be an object",
                self.state_file.display()
            )));
        }
        Ok(state)
    }

    fn persist_transaction_state(
        &self,
        base_state: &Value,
        state: &Value,
    ) -> Result<(), StateTxnError> {
        if let Some(layout) = self.runtime_layout() {
            return persist_runtime_composite_state(
                &layout,
                &self.session_id,
                Some(base_state),
                state,
                self.lock_timeout_ms,
            );
        }
        save_state(&self.state_file, state).map_err(StateTxnError::Write)
    }

    fn runtime_layout(&self) -> Option<RuntimeStateLayout> {
        runtime_layout_for_legacy_state_file(&self.state_file, &self.session_id)
    }
}

#[derive(Debug, Clone)]
struct RuntimeStateLayout {
    legacy_file: PathBuf,
    domain_dir: PathBuf,
}

fn runtime_layout_for_legacy_state_file(
    state_file: &Path,
    session_id: &str,
) -> Option<RuntimeStateLayout> {
    if state_file.file_name()?.to_str()? != "state.json" {
        return None;
    }
    let session_key = crate::common::dirs::session_state_key(session_id)?;
    let legacy_session_dir = state_file.parent()?;
    if legacy_session_dir.file_name()?.to_str()? != session_key {
        return None;
    }
    let sessions_dir = legacy_session_dir.parent()?;
    if sessions_dir.file_name()?.to_str()? != "sessions" {
        return None;
    }
    let state_root = sessions_dir.parent()?.to_path_buf();
    Some(RuntimeStateLayout {
        legacy_file: state_file.to_path_buf(),
        domain_dir: state_root.join(session_key),
    })
}

fn runtime_layout_for_save_state(
    state_file: &Path,
    state: &Value,
) -> Option<(RuntimeStateLayout, String)> {
    if state_file.file_name()?.to_str()? != "state.json" {
        return None;
    }
    let legacy_session_dir = state_file.parent()?;
    let session_key = legacy_session_dir.file_name()?.to_str()?;
    let sessions_dir = legacy_session_dir.parent()?;
    if sessions_dir.file_name()?.to_str()? != "sessions" {
        return None;
    }
    let sessions = state.as_object()?;
    let mut matched = sessions
        .keys()
        .filter(|session_id| {
            crate::common::dirs::session_state_key(session_id)
                .as_deref()
                .is_some_and(|key| key == session_key)
        })
        .cloned()
        .collect::<Vec<_>>();
    if matched.len() != 1 {
        return None;
    }
    let state_root = sessions_dir.parent()?.to_path_buf();
    Some((
        RuntimeStateLayout {
            legacy_file: state_file.to_path_buf(),
            domain_dir: state_root.join(session_key),
        },
        matched.remove(0),
    ))
}

fn load_runtime_composite_state(
    layout: &RuntimeStateLayout,
    session_id: &str,
    lock_timeout_ms: u64,
) -> Result<Value, StateTxnError> {
    recover_pending_runtime_transaction(layout, session_id, lock_timeout_ms)?;
    let any_domain_file = StateDomain::ALL
        .iter()
        .any(|domain| layout.domain_dir.join(domain.file_name()).exists());
    if !any_domain_file {
        return load_legacy_state_file(&layout.legacy_file);
    }

    let mut session = Map::new();
    for domain in StateDomain::ALL {
        let domain_state_file = layout.domain_dir.join(domain.file_name());
        let store = DomainStateStore::new(
            domain_state_file,
            Some(layout.legacy_file.clone()),
            session_id,
            domain,
            lock_timeout_ms,
        );
        let domain_state = store.read(Clone::clone)?;
        merge_object_fields(&mut session, domain_state)?;
    }
    Ok(json!({ session_id: Value::Object(session) }))
}

fn load_legacy_state_file(state_file: &Path) -> Result<Value, StateTxnError> {
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

fn persist_runtime_composite_state(
    layout: &RuntimeStateLayout,
    session_id: &str,
    base_state: Option<&Value>,
    state: &Value,
    lock_timeout_ms: u64,
) -> Result<(), StateTxnError> {
    let base_session = base_state
        .and_then(|state| state.get(session_id))
        .cloned()
        .filter(Value::is_object)
        .unwrap_or_else(|| json!({}));
    let session = state
        .get(session_id)
        .cloned()
        .filter(Value::is_object)
        .unwrap_or_else(|| json!({}));
    std::fs::create_dir_all(&layout.domain_dir).map_err(|error| {
        StateTxnError::Write(format!("{}: {error}", layout.domain_dir.display()))
    })?;
    let changes = changed_domain_updates(&base_session, &session);
    let transaction_file = layout.domain_dir.join(STATE_TRANSACTION_FILE);
    if changes.len() > 1 {
        agentic_otel_common::state::save_state(
            &transaction_file,
            &json!({
                "schema_version": STATE_SHARD_SCHEMA_VERSION,
                "session_id": session_id,
                "base_session": base_session,
                "candidate_session": session,
            }),
        )
        .map_err(StateTxnError::Write)?;
    }
    apply_domain_changes(layout, session_id, changes, lock_timeout_ms, false)?;
    if transaction_file.exists() {
        remove_transaction_file(&transaction_file)?;
    }
    Ok(())
}

fn changed_domain_updates(
    base_session: &Value,
    session: &Value,
) -> Vec<(StateDomain, Value, Value)> {
    split_session_into_domains(base_session)
        .into_iter()
        .zip(split_session_into_domains(session))
        .filter_map(|((domain, base_domain), (update_domain, domain_update))| {
            debug_assert_eq!(domain, update_domain);
            (base_domain != domain_update).then_some((domain, base_domain, domain_update))
        })
        .collect()
}

fn apply_domain_changes(
    layout: &RuntimeStateLayout,
    session_id: &str,
    changes: Vec<(StateDomain, Value, Value)>,
    lock_timeout_ms: u64,
    preserve_conflicts: bool,
) -> Result<(), StateTxnError> {
    for (domain, base_domain, domain_update) in changes {
        let domain_state_file = layout.domain_dir.join(domain.file_name());
        let store = DomainStateStore::new(
            domain_state_file,
            Some(layout.legacy_file.clone()),
            session_id,
            domain,
            lock_timeout_ms,
        );
        store.update(|existing| {
            apply_domain_update(
                existing,
                domain,
                &base_domain,
                &domain_update,
                preserve_conflicts,
            )?;
            Ok(())
        })?;
    }
    Ok(())
}

fn recover_pending_runtime_transaction(
    layout: &RuntimeStateLayout,
    session_id: &str,
    lock_timeout_ms: u64,
) -> Result<(), StateTxnError> {
    let transaction_file = layout.domain_dir.join(STATE_TRANSACTION_FILE);
    let bytes = match std::fs::read(&transaction_file) {
        Ok(bytes) => bytes,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(()),
        Err(error) => {
            return Err(StateTxnError::Read(format!(
                "{}: {error}",
                transaction_file.display()
            )))
        }
    };
    let transaction: Value = serde_json::from_slice(&bytes).map_err(|error| {
        StateTxnError::Schema(format!(
            "{}: invalid JSON: {error}",
            transaction_file.display()
        ))
    })?;
    if transaction.get("session_id").and_then(Value::as_str) != Some(session_id) {
        return Err(StateTxnError::Schema(format!(
            "{}: transaction session mismatch",
            transaction_file.display()
        )));
    }
    let base_session = transaction
        .get("base_session")
        .filter(|value| value.is_object())
        .ok_or_else(|| {
            StateTxnError::Schema(format!(
                "{}: missing base_session",
                transaction_file.display()
            ))
        })?;
    let candidate_session = transaction
        .get("candidate_session")
        .filter(|value| value.is_object())
        .ok_or_else(|| {
            StateTxnError::Schema(format!(
                "{}: missing candidate_session",
                transaction_file.display()
            ))
        })?;
    apply_domain_changes(
        layout,
        session_id,
        changed_domain_updates(base_session, candidate_session),
        lock_timeout_ms,
        true,
    )?;
    remove_transaction_file(&transaction_file)
}

fn remove_transaction_file(transaction_file: &Path) -> Result<(), StateTxnError> {
    match std::fs::remove_file(transaction_file) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(StateTxnError::Write(format!(
            "{}: {error}",
            transaction_file.display()
        ))),
    }
}

fn split_session_into_domains(session: &Value) -> Vec<(StateDomain, Value)> {
    let mut domains = StateDomain::ALL
        .iter()
        .map(|domain| (*domain, extract_legacy_domain(session, *domain)))
        .collect::<Vec<_>>();
    let mut attribution = Map::new();
    if let Some(object) = session.as_object() {
        for (key, value) in object {
            if !is_known_domain_field(key) {
                attribution.insert(key.clone(), value.clone());
            }
        }
    }
    if let Some((_, attribution_state)) = domains
        .iter_mut()
        .find(|(domain, _)| *domain == StateDomain::Attribution)
    {
        *attribution_state = Value::Object(attribution);
    }
    domains
}

fn apply_domain_update(
    existing: &mut Value,
    domain: StateDomain,
    base: &Value,
    update: &Value,
    preserve_conflicts: bool,
) -> Result<(), StateTxnError> {
    ensure_json_object(existing, domain)?;
    let existing_object = existing.as_object_mut().expect("checked object");
    existing_object.insert(
        "schema_version".to_string(),
        json!(STATE_SHARD_SCHEMA_VERSION),
    );
    if domain == StateDomain::Attribution {
        let fields = base
            .as_object()
            .into_iter()
            .flat_map(|object| object.keys())
            .chain(
                update
                    .as_object()
                    .into_iter()
                    .flat_map(|object| object.keys()),
            )
            .filter(|field| field.as_str() != "schema_version")
            .cloned()
            .collect::<BTreeSet<_>>();
        for field in fields {
            apply_field_update(
                existing_object,
                domain,
                &field,
                base,
                update,
                preserve_conflicts,
            )?;
        }
        return Ok(());
    }
    for field in domain.legacy_fields() {
        apply_field_update(
            existing_object,
            domain,
            field,
            base,
            update,
            preserve_conflicts,
        )?;
    }
    Ok(())
}

fn apply_field_update(
    existing: &mut Map<String, Value>,
    domain: StateDomain,
    field: &str,
    base: &Value,
    update: &Value,
    preserve_conflicts: bool,
) -> Result<(), StateTxnError> {
    let base_value = base.get(field);
    let update_value = update.get(field);
    if base_value == update_value {
        return Ok(());
    }

    let existing_value = existing.get(field);
    if existing_value == update_value {
        return Ok(());
    }
    if existing_value != base_value {
        if preserve_conflicts {
            return Ok(());
        }
        return Err(StateTxnError::Conflict(format!(
            "{} field {field} changed after composite snapshot",
            domain.file_name()
        )));
    }

    if let Some(value) = update_value {
        existing.insert(field.to_string(), value.clone());
    } else {
        existing.remove(field);
    }
    Ok(())
}

fn ensure_json_object(value: &mut Value, domain: StateDomain) -> Result<(), StateTxnError> {
    if value.is_null() {
        *value = json!({});
    }
    if !value.is_object() {
        return Err(StateTxnError::Schema(format!(
            "{domain} state must be an object"
        )));
    }
    Ok(())
}

fn merge_object_fields(target: &mut Map<String, Value>, value: Value) -> Result<(), StateTxnError> {
    let object = value
        .as_object()
        .ok_or_else(|| StateTxnError::Schema("domain state must be an object".to_string()))?;
    for (key, value) in object {
        if key == "schema_version" {
            continue;
        }
        target.insert(key.clone(), value.clone());
    }
    Ok(())
}

fn is_known_domain_field(key: &str) -> bool {
    if key == "schema_version" {
        return true;
    }
    StateDomain::ALL
        .iter()
        .any(|domain| domain.legacy_fields().contains(&key))
}

pub fn save_state(state_file: &Path, state: &Value) -> Result<(), String> {
    if let Some((layout, session_id)) = runtime_layout_for_save_state(state_file, state) {
        let lock_file = layout.domain_dir.join(".state_transaction.lock");
        let state_lock = StateLock::acquire(&lock_file, 500);
        if !state_lock.is_acquired() {
            return Err(StateTxnError::Busy { waited_ms: 500 }.to_string());
        }
        let base_state = load_runtime_composite_state(&layout, &session_id, 500)
            .map_err(|error| error.to_string())?;
        persist_runtime_composite_state(&layout, &session_id, Some(&base_state), state, 500)
            .map_err(|error| error.to_string())?;
        return Ok(());
    }
    agentic_otel_common::state::save_state(state_file, state)
}

#[derive(Debug, Clone)]
pub struct VcsInfo {
    pub remote_url: String,
    pub branch: String,
    pub cwd: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct TurnCheckpoint {
    pub turn_count: usize,
    pub inserted: bool,
}

pub fn get_vcs_from_state(state: &Value, session_id: &str) -> Option<VcsInfo> {
    get_vcs_from_session(state.get(session_id)?)
}

pub fn get_vcs_for_cwd_from_state(state: &Value, session_id: &str, cwd: &str) -> Option<VcsInfo> {
    get_vcs_for_cwd_from_session(state.get(session_id)?, cwd)
}

pub fn get_vcs_for_cwd_from_session(session: &Value, cwd: &str) -> Option<VcsInfo> {
    let cwd = cwd.trim();
    if cwd.is_empty() {
        return None;
    }
    get_vcs_from_session(session).filter(|vcs| vcs.cwd == cwd)
}

fn get_vcs_from_session(session: &Value) -> Option<VcsInfo> {
    let vcs = session.get("vcs")?;
    let remote_url = vcs.get("remote_url")?.as_str()?.to_string();
    let branch = vcs.get("branch")?.as_str()?.to_string();
    let cwd = vcs.get("cwd")?.as_str()?.to_string();
    if remote_url.is_empty() && branch.is_empty() {
        return None;
    }
    Some(VcsInfo {
        remote_url,
        branch,
        cwd,
    })
}

pub(super) fn save_candidate_state<F>(
    state_file: &Path,
    state: &mut Value,
    update: F,
) -> Result<(), String>
where
    F: FnOnce(&mut Value),
{
    let mut candidate = state.clone();
    update(&mut candidate);
    save_state(state_file, &candidate)?;
    *state = candidate;
    Ok(())
}

fn session_object(state: &Value, session_id: &str) -> Value {
    state
        .get(session_id)
        .cloned()
        .filter(Value::is_object)
        .unwrap_or_else(|| json!({}))
}

pub fn replace_vcs_snapshot(session: &mut Value, vcs: &VcsInfo) {
    session["vcs"] = json!({
        "remote_url": vcs.remote_url,
        "branch": vcs.branch,
        "cwd": vcs.cwd,
    });
}

/// Compatibility cursor update used by non-progressive call sites/tests.
/// The candidate is installed in memory only after atomic persistence succeeds.
#[allow(dead_code)]
pub fn update_state_and_save(
    state_file: &Path,
    state: &mut Value,
    session_id: &str,
    offset: i64,
    buffer: &str,
    turn_count: usize,
    now: &str,
) {
    if let Err(error) = save_candidate_state(state_file, state, |candidate| {
        let mut session = session_object(candidate, session_id);
        session["offset"] = json!(offset);
        session["buffer"] = json!(buffer);
        session["turn_count"] = json!(turn_count);
        session["updated"] = json!(now);
        candidate[session_id] = session;
    }) {
        let log_file = crate::common::dirs::log_dir().join("cchooks.log");
        if let Ok(mut file) = std::fs::OpenOptions::new()
            .append(true)
            .create(true)
            .open(log_file)
        {
            use std::io::Write;
            let _ = writeln!(file, "[WARN] save_state failed: {error}");
        }
    }
}

/// Persist a retry barrier without changing the cursor, turn count, or posted IDs.
#[allow(dead_code)]
pub fn record_pending_export(
    state_file: &Path,
    state: &mut Value,
    session_id: &str,
    reason: &str,
    now: &str,
    transcript_path: &str,
) -> Result<(), String> {
    save_candidate_state(state_file, state, |candidate| {
        record_pending_export_in_memory(candidate, session_id, reason, now, transcript_path);
    })
}

pub fn record_pending_export_in_memory(
    state: &mut Value,
    session_id: &str,
    reason: &str,
    now: &str,
    transcript_path: &str,
) {
    let mut session = session_object(state, session_id);
    session["pending_export_reason"] = json!(reason);
    session["updated"] = json!(now);
    session["transcript_path"] = json!(transcript_path);
    state[session_id] = session;
}

/// Checkpoint one remotely accepted turn. This is idempotent by stable turn ID.
/// Offset, buffer, timing, trace context, subagent timing, and unknown fields are
/// deliberately preserved from the old unread window.
#[allow(dead_code)]
pub fn checkpoint_turn_export_success(
    state_file: &Path,
    state: &mut Value,
    session_id: &str,
    turn_id: &str,
    now: &str,
    transcript_path: &str,
) -> Result<usize, String> {
    checkpoint_turn_accounted(state_file, state, session_id, turn_id, now, transcript_path)
}

/// Persist a turn before its single at-most-once transport attempt.
///
/// If this checkpoint fails, the caller must not send the turn. Once it
/// succeeds, the turn remains accounted for even if the HTTP request fails or
/// the hook exits before receiving a response.
#[allow(dead_code)]
pub fn checkpoint_turn_export_attempt(
    state_file: &Path,
    state: &mut Value,
    session_id: &str,
    turn_id: &str,
    now: &str,
    transcript_path: &str,
) -> Result<usize, String> {
    checkpoint_turn_accounted(state_file, state, session_id, turn_id, now, transcript_path)
}

#[allow(dead_code)]
fn checkpoint_turn_accounted(
    state_file: &Path,
    state: &mut Value,
    session_id: &str,
    turn_id: &str,
    now: &str,
    transcript_path: &str,
) -> Result<usize, String> {
    let mut checkpoint = None;
    save_candidate_state(state_file, state, |candidate| {
        checkpoint = Some(checkpoint_turn_accounted_in_memory(
            candidate,
            session_id,
            turn_id,
            now,
            transcript_path,
            None,
        ));
    })?;
    Ok(checkpoint.expect("checkpoint mutation must run").turn_count)
}

/// Account for one turn in memory. When `window_end_offset` is provided, a
/// cursor already at or beyond that boundary proves another Stop invocation
/// finalized the same retained window, so the turn is treated as accounted
/// without changing `turn_count`.
pub fn checkpoint_turn_accounted_in_memory(
    state: &mut Value,
    session_id: &str,
    turn_id: &str,
    now: &str,
    transcript_path: &str,
    window_end_offset: Option<i64>,
) -> TurnCheckpoint {
    let session = get_session_state(state, session_id);
    let existing_ids = session
        .map(|value| get_string_array_field(value, "posted_turn_ids"))
        .unwrap_or_default();
    let current_count = session
        .map(|value| get_int_field(value, "turn_count") as usize)
        .unwrap_or(0);
    let finalized = window_end_offset.is_some_and(|window_end| {
        session
            .map(|value| get_int_field(value, "offset"))
            .unwrap_or(0)
            >= window_end
    });
    if finalized || existing_ids.iter().any(|posted| posted == turn_id) {
        return TurnCheckpoint {
            turn_count: current_count,
            inserted: false,
        };
    }

    let new_count = current_count + 1;
    let mut session = session_object(state, session_id);
    if session.get("offset").is_none() {
        session["offset"] = json!(0);
    }
    if session.get("buffer").is_none() {
        session["buffer"] = json!("");
    }
    let mut ids = get_string_array_field(&session, "posted_turn_ids");
    ids.push(turn_id.to_string());
    session["posted_turn_ids"] = json!(ids);
    session["turn_count"] = json!(new_count);
    session["updated"] = json!(now);
    session["pending_export_reason"] = json!(PARTIAL_EXPORT_REASON);
    session["transcript_path"] = json!(transcript_path);
    if let Some(window_end_offset) = window_end_offset {
        session["pending_window_end_offset"] = json!(window_end_offset);
    }
    state[session_id] = session;
    TurnCheckpoint {
        turn_count: new_count,
        inserted: true,
    }
}

/// Final cursor/timing transaction. Temporary checkpoint fields are cleared only
/// when the candidate save succeeds; archive emission is subordinate best effort.
#[allow(clippy::too_many_arguments)]
#[allow(dead_code)]
pub fn commit_export_success(
    state_file: &Path,
    state: &mut Value,
    session_id: &str,
    offset: i64,
    buffer: &str,
    now: &str,
    archive_path: Option<&Path>,
    matched_realtime_context_ids: Option<&[String]>,
) -> Result<(), String> {
    let archive_state = archive_path.map(|_| state.clone());
    save_candidate_state(state_file, state, |candidate| {
        let _ = commit_export_success_in_memory(
            candidate,
            session_id,
            offset,
            buffer,
            now,
            matched_realtime_context_ids,
        );
    })?;

    if let (Some(path), Some(mut archive_state)) = (archive_path, archive_state) {
        crate::hooks::timing::cleanup_timing(&mut archive_state, session_id, Some(path));
    }
    Ok(())
}

/// Finalize an export window without allowing an older Stop invocation to move
/// the cursor backwards or overwrite the buffer belonging to a newer cursor.
pub fn commit_export_success_in_memory(
    state: &mut Value,
    session_id: &str,
    offset: i64,
    buffer: &str,
    now: &str,
    matched_realtime_context_ids: Option<&[String]>,
) -> bool {
    let mut session = session_object(state, session_id);
    let persisted_offset = get_int_field(&session, "offset");
    let pending_window_end = get_int_field(&session, "pending_window_end_offset");
    if persisted_offset > offset || pending_window_end > offset {
        return false;
    }
    session["offset"] = json!(offset);
    session["buffer"] = json!(buffer);
    session["updated"] = json!(now);
    if let Some(object) = session.as_object_mut() {
        object.remove("posted_turn_ids");
        object.remove("posted_compaction_ids");
        object.remove("pending_export_reason");
        object.remove("transcript_path");
        object.remove("pending_window_end_offset");
    }
    if let Some(matched_context_ids) = matched_realtime_context_ids {
        if let Some(realtime) = session.get_mut("realtime_export") {
            crate::hooks::langfuse::realtime_state::remove_contexts_from_realtime(
                realtime,
                matched_context_ids,
            );
        }
    }
    state[session_id] = session;
    crate::hooks::timing::cleanup_timing(state, session_id, None);
    true
}

#[cfg(test)]
mod tests {
    use super::*;
    use agentic_otel_common::state::load_state;
    use std::sync::atomic::{AtomicBool, Ordering};

    #[test]
    fn old_state_checkpoint_preserves_extensions_and_cursor() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("state.json");
        let mut state = json!({"s": {
            "offset": 123, "buffer": "tail", "turn_count": 7,
            "timing": {"tool": {"start_ns": "1"}},
            "custom": {"keep": true}
        }});
        checkpoint_turn_export_success(&path, &mut state, "s", "turn-1", "now", "/tmp/t.jsonl")
            .unwrap();
        assert_eq!(state["s"]["offset"], 123);
        assert_eq!(state["s"]["buffer"], "tail");
        assert_eq!(state["s"]["turn_count"], 8);
        assert_eq!(state["s"]["posted_turn_ids"], json!(["turn-1"]));
        assert_eq!(state["s"]["custom"]["keep"], true);
        assert_eq!(load_state(&path), state);
    }

    #[test]
    fn session_store_preserves_envelope_and_unknown_fields() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("state.json");
        save_state(
            &path,
            &json!({"session-1": {"offset": 10, "extension": {"keep": true}}}),
        )
        .unwrap();
        let store = SessionStateStore::new(path.clone(), "session-1", 100);

        store
            .update_session(|session| {
                session["offset"] = json!(20);
                Ok(())
            })
            .unwrap();

        let state = load_state(&path);
        assert_eq!(state["session-1"]["offset"], 20);
        assert_eq!(state["session-1"]["extension"]["keep"], true);
        assert_eq!(state.as_object().unwrap().len(), 1);
    }

    #[cfg(unix)]
    #[test]
    fn busy_session_store_never_invokes_update_or_saves() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("state.json");
        let original = json!({"session-1": {"offset": 10}});
        save_state(&path, &original).unwrap();
        let held_lock = StateLock::acquire(&path, 100);
        assert!(held_lock.is_acquired());
        let invoked = AtomicBool::new(false);
        let store = SessionStateStore::new(path.clone(), "session-1", 10);

        let error = store
            .update_session(|session| {
                invoked.store(true, Ordering::SeqCst);
                session["offset"] = json!(99);
                Ok(())
            })
            .unwrap_err();

        assert_eq!(error, StateTxnError::Busy { waited_ms: 10 });
        assert!(!invoked.load(Ordering::SeqCst));
        assert_eq!(load_state(&path), original);
    }

    #[test]
    fn session_store_rejects_non_object_session_without_overwriting() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("state.json");
        let original = json!({"session-1": "invalid"});
        save_state(&path, &original).unwrap();
        let store = SessionStateStore::new(path.clone(), "session-1", 100);

        assert!(matches!(
            store.update_session(|_| Ok(())),
            Err(StateTxnError::Schema(_))
        ));
        assert_eq!(load_state(&path), original);
    }

    #[test]
    fn checkpoint_is_idempotent() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("state.json");
        let mut state = json!({});
        for _ in 0..2 {
            checkpoint_turn_export_success(&path, &mut state, "s", "turn-1", "now", "/tmp/t.jsonl")
                .unwrap();
        }
        assert_eq!(state["s"]["posted_turn_ids"], json!(["turn-1"]));
        assert_eq!(state["s"]["turn_count"], 1);
    }

    #[test]
    fn stop_checkpoints_preserve_resource_context_without_write_ownership() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("state.json");
        let vcs = json!({
            "remote_url": "git@git.example.com:team/repo.git",
            "branch": "realtime",
            "cwd": "/repo"
        });
        let mut state = json!({"s": {
            "offset": 0,
            "buffer": "",
            "turn_count": 0,
            "vcs": vcs.clone()
        }});

        checkpoint_turn_export_success(&path, &mut state, "s", "turn-1", "now", "/tmp/t.jsonl")
            .unwrap();
        assert_eq!(state["s"]["vcs"], vcs);

        commit_export_success(&path, &mut state, "s", 100, "", "later", None, None).unwrap();
        assert_eq!(state["s"]["vcs"], vcs);
    }

    #[test]
    fn vcs_lookup_accepts_legacy_shape_and_rejects_cwd_mismatch() {
        let state = json!({"s": {
            "vcs": {
                "remote_url": "git@git.example.com:team/repo.git",
                "branch": "realtime",
                "cwd": "/repo",
                "optional_future_metadata": "ignored"
            }
        }});

        let vcs = get_vcs_for_cwd_from_state(&state, "s", " /repo ").unwrap();
        assert_eq!(vcs.remote_url, "git@git.example.com:team/repo.git");
        assert_eq!(vcs.branch, "realtime");
        assert!(get_vcs_for_cwd_from_state(&state, "s", "/other").is_none());
        assert!(get_vcs_for_cwd_from_state(&state, "s", " ").is_none());
    }

    #[test]
    fn replacing_vcs_preserves_all_session_siblings() {
        let mut session = json!({
            "offset": 7,
            "timing": {"tool": true},
            "realtime_export": {"current": {"trace_id": "abc"}},
            "turn_digest": {"offset": 3},
            "subagents": {"parent_stopped": false},
            "unknown": {"keep": true}
        });
        replace_vcs_snapshot(
            &mut session,
            &VcsInfo {
                remote_url: "ssh://repo".to_string(),
                branch: "main".to_string(),
                cwd: "/repo".to_string(),
            },
        );

        assert_eq!(session["offset"], 7);
        assert_eq!(session["timing"]["tool"], true);
        assert_eq!(session["realtime_export"]["current"]["trace_id"], "abc");
        assert_eq!(session["turn_digest"]["offset"], 3);
        assert_eq!(session["subagents"]["parent_stopped"], false);
        assert_eq!(session["unknown"]["keep"], true);
        assert_eq!(session["vcs"]["remote_url"], "ssh://repo");
    }

    #[test]
    fn attempt_checkpoint_is_idempotent_and_preserves_cursor() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("state.json");
        let mut state = json!({"s": {
            "offset": 123,
            "buffer": "tail",
            "turn_count": 7,
            "custom": true
        }});
        for _ in 0..2 {
            checkpoint_turn_export_attempt(&path, &mut state, "s", "turn-1", "now", "/tmp/t.jsonl")
                .unwrap();
        }
        assert_eq!(state["s"]["offset"], 123);
        assert_eq!(state["s"]["buffer"], "tail");
        assert_eq!(state["s"]["turn_count"], 8);
        assert_eq!(state["s"]["posted_turn_ids"], json!(["turn-1"]));
        assert_eq!(state["s"]["custom"], true);
        assert_eq!(load_state(&path), state);
    }

    #[test]
    fn failed_candidate_save_does_not_install() {
        let dir = tempfile::tempdir().unwrap();
        let blocker = dir.path().join("file");
        std::fs::write(&blocker, "x").unwrap();
        let state_path = blocker.join("state.json");
        let original = json!({"s": {"offset": 1, "custom": true}});
        let mut state = original.clone();
        assert!(checkpoint_turn_export_success(
            &state_path,
            &mut state,
            "s",
            "turn-1",
            "now",
            "/tmp/t.jsonl",
        )
        .is_err());
        assert_eq!(state, original);
    }

    #[test]
    fn failed_final_commit_does_not_clear_checkpoint_or_timing() {
        let dir = tempfile::tempdir().unwrap();
        let blocker = dir.path().join("file");
        std::fs::write(&blocker, "x").unwrap();
        let state_path = blocker.join("state.json");
        let original = json!({"s": {
            "offset": 1,
            "buffer": "tail",
            "turn_count": 1,
            "posted_turn_ids": ["turn-1"],
            "posted_compaction_ids": ["compact-1"],
            "compaction_count": 1,
            "last_exported_turn_anchor": {
                "turn_id": "turn-1", "turn_number": 1,
                "trace_id": "trace", "root_span_id": "root",
                "chat_id": "chat", "transcript_path": "/tmp/t.jsonl",
                "source_end_offset": 1
            },
            "pending_export_reason": PARTIAL_EXPORT_REASON,
            "transcript_path": "/tmp/t.jsonl",
            "timing": {"tool": {"start_ns": "0"}}
        }});
        let mut state = original.clone();
        assert!(
            commit_export_success(&state_path, &mut state, "s", 999, "", "now", None, None,)
                .is_err()
        );
        assert_eq!(state, original);
    }

    #[test]
    fn final_commit_clears_checkpoint_and_parent_timing_only() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("state.json");
        let archive = dir.path().join("timing.json");
        let mut state = json!({"s": {
            "offset": 0, "buffer": "", "turn_count": 2,
            "posted_turn_ids": ["a", "b"],
            "posted_compaction_ids": ["compact-1"],
            "compaction_count": 1,
            "last_exported_turn_anchor": {
                "turn_id": "b", "turn_number": 2,
                "trace_id": "trace", "root_span_id": "root",
                "chat_id": "chat", "transcript_path": "/tmp/t.jsonl",
                "source_end_offset": 42
            },
            "pending_export_reason": PARTIAL_EXPORT_REASON,
            "transcript_path": "/tmp/t.jsonl",
            "timing": {"tool": {"start_ns": "0"}}, "stop_failure_ts": "1",
            "subagent_timing": {"child": {}},
            "realtime_export": {"current": {"context_id": "untouched"}},
            "custom": true
        }});
        commit_export_success(
            &path,
            &mut state,
            "s",
            999,
            "buf",
            "now",
            Some(&archive),
            None,
        )
        .unwrap();
        assert_eq!(state["s"]["offset"], 999);
        assert_eq!(state["s"]["turn_count"], 2);
        assert!(state["s"].get("posted_turn_ids").is_none());
        assert!(state["s"].get("posted_compaction_ids").is_none());
        assert_eq!(state["s"]["compaction_count"], 1);
        assert_eq!(state["s"]["last_exported_turn_anchor"]["turn_id"], "b");
        assert!(state["s"].get("timing").is_none());
        assert!(state["s"].get("stop_failure_ts").is_none());
        assert!(state["s"].get("subagent_timing").is_some());
        assert_eq!(
            state["s"]["realtime_export"]["current"]["context_id"],
            "untouched"
        );
        assert_eq!(state["s"]["custom"], true);
        let archived: Value = serde_json::from_slice(&std::fs::read(archive).unwrap()).unwrap();
        assert_eq!(archived["tool"]["end_ns"], "1");
        assert_eq!(archived["tool"]["ok"], false);
    }

    #[test]
    fn final_commit_removes_only_explicitly_matched_realtime_contexts() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("state.json");
        let mut state = json!({"s": {
            "offset": 0,
            "buffer": "",
            "realtime_export": {
                "current": {"context_id": "current"},
                "pending": [
                    {"context_id": "matched"},
                    {"context_id": "keep"}
                ]
            }
        }});
        commit_export_success(
            &path,
            &mut state,
            "s",
            1,
            "",
            "now",
            None,
            Some(&["matched".to_string()]),
        )
        .unwrap();
        assert_eq!(
            state["s"]["realtime_export"]["current"]["context_id"],
            "current"
        );
        assert_eq!(
            state["s"]["realtime_export"]["pending"],
            json!([{"context_id": "keep"}])
        );
    }

    #[test]
    fn finalized_window_rejects_late_duplicate_checkpoint() {
        let mut state = json!({"s": {
            "offset": 0,
            "buffer": "",
            "turn_count": 0
        }});
        let first = checkpoint_turn_accounted_in_memory(
            &mut state,
            "s",
            "turn-1",
            "now",
            "/tmp/t.jsonl",
            Some(100),
        );
        assert_eq!(
            first,
            TurnCheckpoint {
                turn_count: 1,
                inserted: true
            }
        );
        assert!(commit_export_success_in_memory(
            &mut state, "s", 100, "", "now", None,
        ));

        let duplicate = checkpoint_turn_accounted_in_memory(
            &mut state,
            "s",
            "turn-1",
            "later",
            "/tmp/t.jsonl",
            Some(100),
        );
        assert_eq!(
            duplicate,
            TurnCheckpoint {
                turn_count: 1,
                inserted: false
            }
        );
        assert!(state["s"].get("posted_turn_ids").is_none());
    }

    #[test]
    fn stale_final_commit_cannot_clear_a_newer_pending_window() {
        let mut state = json!({"s": {
            "offset": 100,
            "buffer": "",
            "turn_count": 1,
            "custom": {"keep": true}
        }});
        let next = checkpoint_turn_accounted_in_memory(
            &mut state,
            "s",
            "turn-2",
            "now",
            "/tmp/t.jsonl",
            Some(200),
        );
        assert!(next.inserted);

        assert!(!commit_export_success_in_memory(
            &mut state, "s", 100, "stale", "later", None,
        ));
        assert_eq!(state["s"]["offset"], 100);
        assert_eq!(state["s"]["buffer"], "");
        assert_eq!(state["s"]["posted_turn_ids"], json!(["turn-2"]));
        assert_eq!(state["s"]["pending_window_end_offset"], 200);
        assert_eq!(state["s"]["custom"]["keep"], true);
    }
}
