use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};
use std::fs;
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

use crate::common::dirs::tool_state_key;
use crate::hooks::langfuse::domain_state::{
    DomainStateStore, StateDomain, ToolHookPhase, ToolHookStateStore,
};
use crate::hooks::langfuse::state::StateTxnError;

pub const TOOL_EVENT_SCHEMA_VERSION: u64 = 1;
pub const TOOL_ORPHAN_TTL_MS: u64 = 24 * 60 * 60 * 1_000;
pub const MAX_STOP_DRAIN_EVENTS: usize = 16;
pub const MAX_TOOL_TOMBSTONES: usize = 256;
pub const MAX_TOOL_EXPORT_ATTEMPTS: u64 = 3;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ToolEventPhase {
    Pre,
    Post,
}

impl ToolEventPhase {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Pre => "pre",
            Self::Post => "post",
        }
    }
}

impl From<ToolEventPhase> for ToolHookPhase {
    fn from(value: ToolEventPhase) -> Self {
        match value {
            ToolEventPhase::Pre => ToolHookPhase::Pre,
            ToolEventPhase::Post => ToolHookPhase::Post,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PostExportStatus {
    Pending,
    Exporting,
    Exported,
    FailedPermanent,
}

impl PostExportStatus {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Pending => "pending",
            Self::Exporting => "exporting",
            Self::Exported => "exported",
            Self::FailedPermanent => "failed_permanent",
        }
    }

    fn parse(value: &Value) -> Self {
        match value.as_str() {
            Some("exporting") => Self::Exporting,
            Some("exported") => Self::Exported,
            Some("failed_permanent") => Self::FailedPermanent,
            _ => Self::Pending,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ToolEventIdentity {
    pub session_id: String,
    pub tool_id: String,
    pub phase: ToolEventPhase,
    pub event_id: String,
}

impl ToolEventIdentity {
    pub fn new(session_id: &str, tool_id: &str, phase: ToolEventPhase, payload: &Value) -> Self {
        let mut hasher = Sha256::new();
        hasher.update(session_id.as_bytes());
        hasher.update([0]);
        hasher.update(tool_id.as_bytes());
        hasher.update([0]);
        hasher.update(phase.as_str().as_bytes());
        hasher.update([0]);
        hasher.update(canonical_json(payload).as_bytes());
        let digest = hasher.finalize();
        let suffix = digest[..16]
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect::<String>();
        Self {
            session_id: session_id.to_string(),
            tool_id: tool_id.to_string(),
            phase,
            event_id: format!("tool-event-{suffix}"),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RecordOutcome {
    Recorded { event_id: String },
    Duplicate { event_id: String },
    AlreadyExported { event_id: Option<String> },
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ClaimOutcome {
    Claimed {
        claim_id: String,
        attempt: u64,
    },
    Leased {
        claim_id: String,
        lease_until_ms: u64,
    },
    MissingPost,
    AlreadyExported {
        event_id: Option<String>,
    },
    FailedPermanent {
        event_id: Option<String>,
    },
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ReconcileOutcome {
    MissingPost,
    WaitingForPre {
        post_event_id: String,
    },
    ReadyToClaim {
        post_event_id: String,
    },
    Exporting {
        claim_id: String,
        lease_until_ms: u64,
    },
    AlreadyExported {
        event_id: Option<String>,
    },
    FailedPermanent {
        event_id: Option<String>,
    },
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CleanupOutcome {
    Cleaned { event_id: Option<String> },
    AlreadyCleaned { event_id: Option<String> },
    NotExported,
}

#[derive(Debug, Clone)]
pub struct ToolEventStateStore {
    session_id: String,
    tool_id: String,
    tool_key: String,
    pre_store: ToolHookStateStore,
    post_store: ToolHookStateStore,
    tools_store: DomainStateStore,
}

impl ToolEventStateStore {
    pub fn for_tool(
        session_id: &str,
        tool_id: &str,
        lock_timeout_ms: u64,
    ) -> Result<Self, StateTxnError> {
        let tool_key = tool_state_key(tool_id)
            .ok_or_else(|| StateTxnError::Init("missing tool_id".to_string()))?;
        Ok(Self {
            session_id: session_id.to_string(),
            tool_id: tool_id.to_string(),
            tool_key,
            pre_store: ToolHookStateStore::for_tool(
                session_id,
                tool_id,
                ToolHookPhase::Pre,
                lock_timeout_ms,
            )?,
            post_store: ToolHookStateStore::for_tool(
                session_id,
                tool_id,
                ToolHookPhase::Post,
                lock_timeout_ms,
            )?,
            tools_store: DomainStateStore::for_session(
                session_id,
                StateDomain::Tools,
                lock_timeout_ms,
            )?,
        })
    }

    // The binary uses `for_tool`; integration tests inject isolated shard paths through `new`.
    #[allow(dead_code)]
    pub fn new(
        session_id: &str,
        tool_id: &str,
        pre_store: ToolHookStateStore,
        post_store: ToolHookStateStore,
        tools_store: DomainStateStore,
    ) -> Result<Self, StateTxnError> {
        let tool_key = tool_state_key(tool_id)
            .ok_or_else(|| StateTxnError::Init("missing tool_id".to_string()))?;
        Ok(Self {
            session_id: session_id.to_string(),
            tool_id: tool_id.to_string(),
            tool_key,
            pre_store,
            post_store,
            tools_store,
        })
    }

    pub fn record_pre_event(
        &self,
        payload: &Value,
        now_ms: u64,
    ) -> Result<RecordOutcome, StateTxnError> {
        self.ensure_cutover(now_ms)?;
        let identity = ToolEventIdentity::new(
            &self.session_id,
            &self.tool_id,
            ToolEventPhase::Pre,
            payload,
        );
        let event_id = identity.event_id.clone();
        self.pre_store.update(|state| {
            if is_event_state(state) {
                return Ok(RecordOutcome::Duplicate {
                    event_id: state_event_id(state).unwrap_or_else(|| event_id.clone()),
                });
            }
            *state = event_state(&identity, payload, now_ms);
            Ok(RecordOutcome::Recorded {
                event_id: event_id.clone(),
            })
        })
    }

    pub fn record_post_event(
        &self,
        payload: &Value,
        now_ms: u64,
    ) -> Result<RecordOutcome, StateTxnError> {
        self.ensure_cutover(now_ms)?;
        if let Some(event_id) = self.exported_tombstone()? {
            return Ok(RecordOutcome::AlreadyExported { event_id });
        }
        let identity = ToolEventIdentity::new(
            &self.session_id,
            &self.tool_id,
            ToolEventPhase::Post,
            payload,
        );
        let event_id = identity.event_id.clone();
        self.post_store.update(|state| {
            if is_event_state(state) {
                if PostExportStatus::parse(&state["status"]) == PostExportStatus::Exported {
                    return Ok(RecordOutcome::AlreadyExported {
                        event_id: state_event_id(state),
                    });
                }
                return Ok(RecordOutcome::Duplicate {
                    event_id: state_event_id(state).unwrap_or_else(|| event_id.clone()),
                });
            }
            let mut value = event_state(&identity, payload, now_ms);
            value["status"] = json!(PostExportStatus::Pending.as_str());
            value["attempt"] = json!(0);
            value["outcome"] = json!(post_outcome(payload));
            *state = value;
            Ok(RecordOutcome::Recorded {
                event_id: event_id.clone(),
            })
        })
    }

    pub fn reconcile(&self, now_ms: u64) -> Result<ReconcileOutcome, StateTxnError> {
        if let Some(event_id) = self.exported_tombstone()? {
            return Ok(ReconcileOutcome::AlreadyExported { event_id });
        }
        let post = self.post_store.read(Clone::clone)?;
        if !is_event_state(&post) {
            return Ok(ReconcileOutcome::MissingPost);
        }
        match PostExportStatus::parse(&post["status"]) {
            PostExportStatus::Exported => Ok(ReconcileOutcome::AlreadyExported {
                event_id: state_event_id(&post),
            }),
            PostExportStatus::FailedPermanent => Ok(ReconcileOutcome::FailedPermanent {
                event_id: state_event_id(&post),
            }),
            PostExportStatus::Exporting => {
                let claim_id = post
                    .get("claim_id")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_string();
                let lease_until_ms = post
                    .get("lease_until_ms")
                    .and_then(Value::as_u64)
                    .unwrap_or(0);
                if lease_until_ms > now_ms && !claim_id.is_empty() {
                    Ok(ReconcileOutcome::Exporting {
                        claim_id,
                        lease_until_ms,
                    })
                } else if self.has_pre_event()? {
                    Ok(ReconcileOutcome::ReadyToClaim {
                        post_event_id: state_event_id(&post).unwrap_or_default(),
                    })
                } else {
                    Ok(ReconcileOutcome::WaitingForPre {
                        post_event_id: state_event_id(&post).unwrap_or_default(),
                    })
                }
            }
            PostExportStatus::Pending => {
                if self.has_pre_event()? {
                    Ok(ReconcileOutcome::ReadyToClaim {
                        post_event_id: state_event_id(&post).unwrap_or_default(),
                    })
                } else {
                    Ok(ReconcileOutcome::WaitingForPre {
                        post_event_id: state_event_id(&post).unwrap_or_default(),
                    })
                }
            }
        }
    }

    pub fn claim_post(
        &self,
        claim_id: &str,
        now_ms: u64,
        lease_ms: u64,
    ) -> Result<ClaimOutcome, StateTxnError> {
        if claim_id.trim().is_empty() {
            return Err(StateTxnError::Schema(
                "claim_id must not be empty".to_string(),
            ));
        }
        if let Some(event_id) = self.exported_tombstone()? {
            return Ok(ClaimOutcome::AlreadyExported { event_id });
        }
        self.post_store.update(|state| {
            if !is_event_state(state) {
                return Ok(ClaimOutcome::MissingPost);
            }
            let status = PostExportStatus::parse(&state["status"]);
            if status == PostExportStatus::Exported {
                return Ok(ClaimOutcome::AlreadyExported {
                    event_id: state_event_id(state),
                });
            }
            if status == PostExportStatus::FailedPermanent {
                return Ok(ClaimOutcome::FailedPermanent {
                    event_id: state_event_id(state),
                });
            }
            if status == PostExportStatus::Exporting {
                let existing_claim = state
                    .get("claim_id")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_string();
                let lease_until_ms = state
                    .get("lease_until_ms")
                    .and_then(Value::as_u64)
                    .unwrap_or(0);
                if lease_until_ms > now_ms && existing_claim != claim_id {
                    return Ok(ClaimOutcome::Leased {
                        claim_id: existing_claim,
                        lease_until_ms,
                    });
                }
                if lease_until_ms > now_ms && existing_claim == claim_id {
                    return Ok(ClaimOutcome::Claimed {
                        claim_id: claim_id.to_string(),
                        attempt: state.get("attempt").and_then(Value::as_u64).unwrap_or(1),
                    });
                }
            }
            let completed_attempts = state.get("attempt").and_then(Value::as_u64).unwrap_or(0);
            if completed_attempts >= MAX_TOOL_EXPORT_ATTEMPTS {
                state["status"] = json!(PostExportStatus::FailedPermanent.as_str());
                state["failed_at_ms"] = json!(now_ms);
                state["failure_reason"] = json!("tool export attempt limit exhausted");
                state["claim_id"] = Value::Null;
                state["lease_until_ms"] = Value::Null;
                return Ok(ClaimOutcome::FailedPermanent {
                    event_id: state_event_id(state),
                });
            }
            let attempt = completed_attempts + 1;
            state["status"] = json!(PostExportStatus::Exporting.as_str());
            state["claim_id"] = json!(claim_id);
            state["lease_until_ms"] = json!(now_ms.saturating_add(lease_ms));
            state["attempt"] = json!(attempt);
            Ok(ClaimOutcome::Claimed {
                claim_id: claim_id.to_string(),
                attempt,
            })
        })
    }

    pub fn mark_exported(&self, claim_id: &str, now_ms: u64) -> Result<bool, StateTxnError> {
        self.post_store.update(|state| {
            if !claim_owns_state(state, claim_id) {
                return Ok(false);
            }
            state["status"] = json!(PostExportStatus::Exported.as_str());
            state["exported_at_ms"] = json!(now_ms);
            state["lease_until_ms"] = Value::Null;
            Ok(true)
        })
    }

    // Kept as the terminal non-retryable transition; current production exporters only
    // report retryable success/failure, while contract tests lock ownership semantics.
    #[allow(dead_code)]
    pub fn mark_failed_permanent(
        &self,
        claim_id: &str,
        reason: &str,
        now_ms: u64,
    ) -> Result<bool, StateTxnError> {
        self.post_store.update(|state| {
            if !claim_owns_state(state, claim_id) {
                return Ok(false);
            }
            state["status"] = json!(PostExportStatus::FailedPermanent.as_str());
            state["failed_at_ms"] = json!(now_ms);
            state["failure_reason"] = json!(reason);
            state["lease_until_ms"] = Value::Null;
            Ok(true)
        })
    }

    pub fn cleanup_if_exported(&self, now_ms: u64) -> Result<CleanupOutcome, StateTxnError> {
        if let Some(event_id) = self.exported_tombstone()? {
            return Ok(CleanupOutcome::AlreadyCleaned { event_id });
        }
        let post = self.post_store.read(Clone::clone)?;
        if PostExportStatus::parse(&post["status"]) != PostExportStatus::Exported {
            return Ok(CleanupOutcome::NotExported);
        }
        let event_id = state_event_id(&post);
        self.write_exported_tombstone(event_id.clone(), now_ms)?;
        remove_if_exists(self.pre_store.state_file())?;
        remove_if_exists(self.post_store.state_file())?;
        Ok(CleanupOutcome::Cleaned { event_id })
    }

    pub fn expire_orphan_if_safe(&self, now_ms: u64, ttl_ms: u64) -> Result<bool, StateTxnError> {
        let pre = self.pre_store.read(Clone::clone)?;
        let post = self.post_store.read(Clone::clone)?;
        let created_at_ms = [pre.get("recorded_at_ms"), post.get("recorded_at_ms")]
            .into_iter()
            .flatten()
            .filter_map(Value::as_u64)
            .min();
        let Some(created_at_ms) = created_at_ms else {
            return Ok(false);
        };
        if now_ms.saturating_sub(created_at_ms) < ttl_ms {
            return Ok(false);
        }
        let status = PostExportStatus::parse(&post["status"]);
        if status == PostExportStatus::Exported {
            return Ok(matches!(
                self.cleanup_if_exported(now_ms)?,
                CleanupOutcome::Cleaned { .. } | CleanupOutcome::AlreadyCleaned { .. }
            ));
        }
        if status == PostExportStatus::Exporting
            && post
                .get("lease_until_ms")
                .and_then(Value::as_u64)
                .is_some_and(|lease_until_ms| lease_until_ms > now_ms)
        {
            return Ok(false);
        }
        self.write_terminal_tombstone("expired", state_event_id(&post), now_ms)?;
        remove_if_exists(self.pre_store.state_file())?;
        remove_if_exists(self.post_store.state_file())?;
        Ok(true)
    }

    pub fn pending_post_payload_if_ready(
        &self,
        now_ms: u64,
    ) -> Result<Option<Value>, StateTxnError> {
        match self.reconcile(now_ms)? {
            ReconcileOutcome::ReadyToClaim { .. } => {
                let post = self.post_store.read(Clone::clone)?;
                Ok(post.get("payload").cloned())
            }
            _ => Ok(None),
        }
    }

    fn ensure_cutover(&self, now_ms: u64) -> Result<(), StateTxnError> {
        self.tools_store.update(|state| {
            ensure_object(state)?;
            state["schema_version"] = json!(TOOL_EVENT_SCHEMA_VERSION);
            state["tool_state_cutover"] = json!({
                "schema_version": TOOL_EVENT_SCHEMA_VERSION,
                "layout": "state/<session>/tool_<tool>/pre|post.json",
                "updated_at_ms": now_ms,
                "legacy_state_json_writes": false,
                "legacy_timing_import": "complete"
            });
            Ok(())
        })
    }

    fn exported_tombstone(&self) -> Result<Option<Option<String>>, StateTxnError> {
        self.tools_store.read(|state| {
            state
                .get("tools")
                .and_then(|tools| tools.get(&self.tool_key))
                .filter(|tool| {
                    matches!(
                        tool.get("status").and_then(Value::as_str),
                        Some("exported" | "expired")
                    )
                })
                .map(|tool| {
                    tool.get("event_id")
                        .and_then(Value::as_str)
                        .map(str::to_string)
                })
        })
    }

    fn write_exported_tombstone(
        &self,
        event_id: Option<String>,
        now_ms: u64,
    ) -> Result<(), StateTxnError> {
        self.write_terminal_tombstone("exported", event_id, now_ms)
    }

    fn write_terminal_tombstone(
        &self,
        status: &str,
        event_id: Option<String>,
        now_ms: u64,
    ) -> Result<(), StateTxnError> {
        self.tools_store.update(|state| {
            ensure_object(state)?;
            state["schema_version"] = json!(TOOL_EVENT_SCHEMA_VERSION);
            state["tool_state_cutover"] = json!({
                "schema_version": TOOL_EVENT_SCHEMA_VERSION,
                "layout": "state/<session>/tool_<tool>/pre|post.json",
                "updated_at_ms": now_ms,
                "legacy_state_json_writes": false,
                "legacy_timing_import": "complete"
            });
            let object = state.as_object_mut().expect("checked object");
            {
                let tools = object
                    .entry("tools")
                    .or_insert_with(|| json!({}))
                    .as_object_mut()
                    .ok_or_else(|| StateTxnError::Schema("tools must be an object".to_string()))?;
                tools.insert(
                    self.tool_key.clone(),
                    json!({
                        "status": status,
                        "event_id": event_id,
                        "cleaned_at_ms": now_ms
                    }),
                );
                while tools.len() > MAX_TOOL_TOMBSTONES {
                    let oldest = tools
                        .iter()
                        .min_by_key(|(_, tool)| {
                            tool.get("cleaned_at_ms")
                                .and_then(Value::as_u64)
                                .unwrap_or(0)
                        })
                        .map(|(key, _)| key.clone());
                    let Some(oldest) = oldest else {
                        break;
                    };
                    tools.remove(&oldest);
                }
            }
            if let Some(timing) = object.get_mut("timing").and_then(Value::as_object_mut) {
                timing.remove(&self.tool_id);
            }
            Ok(())
        })
    }

    fn has_pre_event(&self) -> Result<bool, StateTxnError> {
        if self.pre_store.read(is_event_state)? {
            return Ok(true);
        }
        self.tools_store.read(|state| {
            state
                .get("timing")
                .and_then(|timing| timing.get(&self.tool_id))
                .and_then(|timing| timing.get("start_ns"))
                .is_some()
        })
    }
}

fn post_outcome(payload: &Value) -> &'static str {
    if payload.get("hook_event_name").and_then(Value::as_str) == Some("PostToolUseFailure") {
        "failure"
    } else {
        "success"
    }
}

pub fn now_millis() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .try_into()
        .unwrap_or(u64::MAX)
}

fn event_state(identity: &ToolEventIdentity, payload: &Value, now_ms: u64) -> Value {
    json!({
        "schema_version": TOOL_EVENT_SCHEMA_VERSION,
        "session_id": identity.session_id,
        "tool_id": identity.tool_id,
        "phase": identity.phase.as_str(),
        "event_id": identity.event_id,
        "recorded_at_ms": now_ms,
        "payload": payload
    })
}

fn is_event_state(state: &Value) -> bool {
    state.is_object() && state.get("event_id").and_then(Value::as_str).is_some()
}

fn state_event_id(state: &Value) -> Option<String> {
    state
        .get("event_id")
        .and_then(Value::as_str)
        .map(str::to_string)
}

fn claim_owns_state(state: &Value, claim_id: &str) -> bool {
    is_event_state(state)
        && PostExportStatus::parse(&state["status"]) == PostExportStatus::Exporting
        && state.get("claim_id").and_then(Value::as_str) == Some(claim_id)
}

fn ensure_object(state: &mut Value) -> Result<(), StateTxnError> {
    if state.is_null() {
        *state = json!({});
    }
    if state.is_object() {
        Ok(())
    } else {
        Err(StateTxnError::Schema(
            "top-level tools state must be an object".to_string(),
        ))
    }
}

fn remove_if_exists(path: &Path) -> Result<(), StateTxnError> {
    match fs::remove_file(path) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(StateTxnError::Write(format!("{}: {error}", path.display()))),
    }
}

fn canonical_json(value: &Value) -> String {
    match value {
        Value::Null => "null".to_string(),
        Value::Bool(value) => value.to_string(),
        Value::Number(value) => value.to_string(),
        Value::String(value) => serde_json::to_string(value).unwrap_or_else(|_| "\"\"".to_string()),
        Value::Array(values) => {
            let items = values.iter().map(canonical_json).collect::<Vec<_>>();
            format!("[{}]", items.join(","))
        }
        Value::Object(values) => {
            let mut sorted = values.iter().collect::<Vec<_>>();
            sorted.sort_by(|left, right| left.0.cmp(right.0));
            let mut object = Map::new();
            for (key, value) in sorted {
                object.insert(key.clone(), Value::String(canonical_json(value)));
            }
            let fields = object
                .iter()
                .map(|(key, value)| {
                    format!(
                        "{}:{}",
                        serde_json::to_string(key).unwrap_or_else(|_| "\"\"".to_string()),
                        value.as_str().unwrap_or("")
                    )
                })
                .collect::<Vec<_>>();
            format!("{{{}}}", fields.join(","))
        }
    }
}
