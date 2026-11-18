//! Durable child-only delivery progress, independent of parent Stop checkpoints.
use super::state::update_state_transaction;
use crate::common::{
    dirs,
    env::capture_worker_env,
    otel::resolve_parent_root_topology,
    session_discovery::{discover_child_session_transcripts, session_search_roots},
};
use crate::hooks::{langfuse::LangfuseConfig, RetryPolicy};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::{BTreeMap, HashSet};
use std::path::Path;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

const KEY: &str = "subagent_progress";
const DORMANT_MS: u64 = 30 * 60 * 1000;
#[derive(Clone, Debug, Default, Serialize, Deserialize, PartialEq, Eq)]
struct Snapshot {
    len: u64,
    modified_ns: u128,
    identity: u64,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct RecoveryDeliveryContext {
    #[serde(default)]
    pub worker_env: BTreeMap<String, String>,
    pub config_source: String,
    #[serde(default)]
    pub config_path: Option<String>,
    #[serde(default)]
    pub profile: Option<String>,
    pub config_fingerprint: String,
    pub retry_policy: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct RecoveryRecord {
    pub parent_turn_id: String,
    pub parent_transcript_path: String,
    pub trace_id: String,
    pub root_span_id: String,
    pub username: String,
    pub version: String,
    #[serde(default)]
    pub pending_child_ids: Vec<String>,
    #[serde(default)]
    pub accounted_span_ids: Vec<String>,
    #[serde(default)]
    pub dormant: bool,
    #[serde(default)]
    needs_delivery: bool,
    #[serde(default)]
    pub delivery_context: Option<RecoveryDeliveryContext>,
    #[serde(default)]
    attempt: usize,
    #[serde(default)]
    next_at_ms: u64,
    #[serde(default)]
    unchanged_since_ms: u64,
    #[serde(default)]
    child_paths: BTreeMap<String, String>,
    #[serde(default)]
    snapshots: BTreeMap<String, Option<Snapshot>>,
}

impl RecoveryRecord {
    pub fn has_recovery_work(&self) -> bool {
        self.needs_delivery || !self.pending_child_ids.is_empty()
    }

    pub fn has_ready_recovery_work(&self) -> bool {
        !self.dormant && self.has_recovery_work()
    }
}

fn config_fingerprint(config: &LangfuseConfig) -> String {
    use sha2::{Digest, Sha256};
    let mut hasher = Sha256::new();
    hasher.update(b"cdxhooks.subagent-recovery.config.v1");
    hasher.update([0]);
    hasher.update(config.host.as_bytes());
    hasher.update([0]);
    let endpoint_override =
        crate::common::env::var("CDXHOOKS_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT").unwrap_or_default();
    hasher.update(endpoint_override.as_bytes());
    hasher.update([0]);
    hasher.update(config.public_key.as_deref().unwrap_or("").as_bytes());
    hasher.update([0]);
    hasher.update(config.secret_key.as_deref().unwrap_or("").as_bytes());
    hasher.update([0]);
    let mut headers = config
        .headers
        .as_ref()
        .map(|headers| headers.iter().collect::<Vec<_>>())
        .unwrap_or_default();
    headers.sort_by(|(left, _), (right, _)| left.cmp(right));
    for (key, value) in headers {
        hasher.update(key.as_bytes());
        hasher.update([0]);
        hasher.update(value.as_bytes());
        hasher.update([0]);
    }
    hasher
        .finalize()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

pub fn delivery_context(
    config: &LangfuseConfig,
    retry_policy: RetryPolicy,
) -> RecoveryDeliveryContext {
    delivery_context_with_source(config, retry_policy, "hook", None, None)
}

pub fn delivery_context_with_source(
    config: &LangfuseConfig,
    retry_policy: RetryPolicy,
    config_source: &str,
    config_path: Option<&str>,
    profile: Option<&str>,
) -> RecoveryDeliveryContext {
    RecoveryDeliveryContext {
        worker_env: capture_worker_env(),
        config_source: config_source.to_string(),
        config_path: config_path.map(str::to_string),
        profile: profile.map(str::to_string),
        config_fingerprint: config_fingerprint(config),
        retry_policy: retry_policy.as_str().to_string(),
    }
}

pub fn config_matches_delivery_context(
    config: &LangfuseConfig,
    context: &RecoveryDeliveryContext,
) -> bool {
    config_fingerprint(config) == context.config_fingerprint
}
pub enum RecoverySchedule {
    Idle,
    Waiting(Duration),
    Ready(RecoveryRecord),
}

pub fn records(state: &Value, sid: &str) -> Vec<RecoveryRecord> {
    state
        .get(sid)
        .and_then(|s| s.get(KEY))
        .and_then(|s| s.get("turns"))
        .and_then(Value::as_object)
        .into_iter()
        .flat_map(|m| m.values())
        .filter_map(|v| serde_json::from_value(v.clone()).ok())
        .collect()
}
/// Read one turn without cloning/deserializing every delivery ledger.
pub fn record(state: &Value, sid: &str, turn: &str) -> Option<RecoveryRecord> {
    let value = state.get(sid)?.get(KEY)?.get("turns")?.get(turn)?;
    let record: RecoveryRecord = serde_json::from_value(value.clone()).ok()?;
    (record.parent_turn_id == turn).then_some(record)
}
pub fn pending_recovery_count(state: &Value, sid: &str) -> usize {
    records(state, sid)
        .iter()
        .filter(|r| r.has_ready_recovery_work())
        .count()
}
fn put(state: &mut Value, sid: &str, r: &RecoveryRecord) {
    if !state[sid].is_object() {
        state[sid] = json!({});
    }
    if !state[sid][KEY].is_object() {
        state[sid][KEY] = json!({"version":1,"turns":{}});
    }
    state[sid][KEY]["turns"][&r.parent_turn_id] =
        serde_json::to_value(r).expect("serializable progress");
}
fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}
fn span_ids(body: &Value) -> Vec<String> {
    body.get("resourceSpans")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .flat_map(|r| {
            r.get("scopeSpans")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
        })
        .flat_map(|s| {
            s.get("spans")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
        })
        .filter_map(|s| s.get("spanId").and_then(Value::as_str).map(str::to_owned))
        .collect()
}
fn topology(body: &Value) -> Result<(String, String), String> {
    resolve_parent_root_topology(body)
}
/// Register BEFORE delivery. A pending delivery remains eligible even when the
/// most recent semantic scan has found a terminal child.
#[allow(clippy::too_many_arguments)]
pub fn register(
    path: &Path,
    state: &mut Value,
    sid: &str,
    turn: &str,
    transcript: &str,
    body: &Value,
    pending: &[String],
    username: &str,
    version: &str,
    delivery_context: Option<RecoveryDeliveryContext>,
) -> Result<(), String> {
    let (trace, root) = topology(body)
        .map_err(|reason| format!("subagent progress requires exact parent topology: {reason}"))?;
    let mut context_conflict = false;
    update_state_transaction(path, state, |candidate| {
        let existing = record(candidate, sid, turn);
        let mut r = existing.unwrap_or_else(|| RecoveryRecord {
            parent_turn_id: turn.into(),
            parent_transcript_path: transcript.into(),
            trace_id: trace.clone(),
            root_span_id: root.clone(),
            username: username.into(),
            version: version.into(),
            pending_child_ids: vec![],
            accounted_span_ids: vec![],
            dormant: false,
            needs_delivery: true,
            delivery_context: delivery_context.clone(),
            attempt: 0,
            next_at_ms: now_ms() + 1000,
            unchanged_since_ms: now_ms(),
            child_paths: BTreeMap::new(),
            snapshots: BTreeMap::new(),
        });
        // Never silently replace topology after an earlier chunk was delivered.
        if r.trace_id != trace || r.root_span_id != root {
            return;
        }
        if let (Some(old), Some(new)) = (&r.delivery_context, &delivery_context) {
            if old.config_fingerprint != new.config_fingerprint
                || old.retry_policy != new.retry_policy
            {
                context_conflict = true;
                return;
            }
        }
        r.pending_child_ids = pending.to_vec();
        r.pending_child_ids.sort();
        r.pending_child_ids.dedup();
        r.needs_delivery = true;
        if r.delivery_context.is_none() {
            r.delivery_context = delivery_context.clone();
        }
        put(candidate, sid, &r);
    })?;
    if context_conflict {
        return Err("subagent recovery delivery context changed".into());
    }
    if record(state, sid, turn).is_some_and(|r| r.trace_id != trace || r.root_span_id != root) {
        return Err("subagent recovery topology changed".into());
    }
    Ok(())
}
pub fn suppress_accounted(body: &mut Value, r: &RecoveryRecord) -> usize {
    let ids: HashSet<&str> = r.accounted_span_ids.iter().map(String::as_str).collect();
    let mut removed = 0;
    for resource in body
        .get_mut("resourceSpans")
        .and_then(Value::as_array_mut)
        .into_iter()
        .flatten()
    {
        for scope in resource
            .get_mut("scopeSpans")
            .and_then(Value::as_array_mut)
            .into_iter()
            .flatten()
        {
            if let Some(spans) = scope.get_mut("spans").and_then(Value::as_array_mut) {
                let before = spans.len();
                spans.retain(|s| {
                    !s.get("spanId")
                        .and_then(Value::as_str)
                        .is_some_and(|id| ids.contains(id))
                });
                removed += before - spans.len();
            }
        }
    }
    removed
}
/// Direct finalizers retain their parent envelope; their existing delivery
/// checkpoint owns parent deduplication. Child-only recovery filters all IDs.
pub fn suppress_accounted_children(body: &mut Value, r: &RecoveryRecord, parent: &str) -> usize {
    let mut child_ids = HashSet::new();
    for resource in body
        .get("resourceSpans")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
    {
        for scope in resource
            .get("scopeSpans")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
        {
            for span in scope
                .get("spans")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
            {
                let child = span
                    .get("attributes")
                    .and_then(Value::as_array)
                    .into_iter()
                    .flatten()
                    .find(|a| {
                        a.get("key").and_then(Value::as_str) == Some("gen_ai.conversation.id")
                    })
                    .and_then(|a| a.pointer("/value/stringValue"))
                    .and_then(Value::as_str)
                    .is_some_and(|id| id != parent);
                if child {
                    if let Some(id) = span.get("spanId").and_then(Value::as_str) {
                        child_ids.insert(id.to_string());
                    }
                }
            }
        }
    }
    let mut child_record = r.clone();
    child_record
        .accounted_span_ids
        .retain(|id| child_ids.contains(id));
    suppress_accounted(body, &child_record)
}
pub fn account(
    path: &Path,
    state: &mut Value,
    sid: &str,
    turn: &str,
    body: &Value,
    pending: &[String],
) -> Result<(), String> {
    account_chunk(path, state, sid, turn, body, pending, false)
}
/// Commit the chunk cursor and continuation flag in one transaction.
#[allow(clippy::too_many_arguments)]
pub fn account_chunk(
    path: &Path,
    state: &mut Value,
    sid: &str,
    turn: &str,
    body: &Value,
    pending: &[String],
    more_chunks: bool,
) -> Result<(), String> {
    let delivered = span_ids(body);
    update_state_transaction(path, state, |candidate| {
        let Some(mut r) = record(candidate, sid, turn) else {
            return;
        };
        r.accounted_span_ids.extend(delivered.iter().cloned());
        r.accounted_span_ids.sort();
        r.accounted_span_ids.dedup();
        r.pending_child_ids = pending.to_vec();
        r.pending_child_ids.sort();
        r.pending_child_ids.dedup();
        r.needs_delivery = more_chunks;
        put(candidate, sid, &r);
    })
    .map(|_| ())
}
fn snapshot(path: &str) -> Option<Snapshot> {
    let m = std::fs::metadata(path).ok()?;
    #[cfg(unix)]
    let identity = {
        use std::os::unix::fs::MetadataExt;
        m.ino()
    };
    #[cfg(not(unix))]
    let identity = 0;
    Some(Snapshot {
        len: m.len(),
        modified_ns: m
            .modified()
            .ok()?
            .duration_since(UNIX_EPOCH)
            .ok()?
            .as_nanos(),
        identity,
    })
}
fn refresh_sources(r: &mut RecoveryRecord, sid: &str) -> bool {
    let missing: HashSet<String> = r
        .pending_child_ids
        .iter()
        .filter(|id| !r.child_paths.contains_key(*id))
        .cloned()
        .collect();
    if !missing.is_empty() {
        let root = dirs::sessions_dir();
        let paths = session_search_roots(
            Path::new(&r.parent_transcript_path)
                .parent()
                .unwrap_or(Path::new(".")),
            Some(&root),
        );
        for child in discover_child_session_transcripts(
            &paths,
            &missing,
            sid,
            Some(Path::new(&r.parent_transcript_path)),
            true,
        )
        .matches
        {
            r.child_paths
                .insert(child.identity_id, child.path.to_string_lossy().into_owned());
        }
    }
    let mut observed = BTreeMap::new();
    observed.insert(
        r.parent_transcript_path.clone(),
        snapshot(&r.parent_transcript_path),
    );
    for path in r.child_paths.values() {
        observed.insert(path.clone(), snapshot(path));
    }
    let changed = r.snapshots != observed;
    r.snapshots = observed;
    changed
}
fn mark_source_progress(r: &mut RecoveryRecord, now: u64) {
    r.attempt = 0;
    r.unchanged_since_ms = now;
    r.next_at_ms = now;
    r.dormant = false;
}
fn schedule_record(r: &mut RecoveryRecord, now: u64, changed: bool) -> bool {
    if changed {
        mark_source_progress(r, now);
    }
    if r.dormant || now < r.next_at_ms {
        return false;
    }
    if now.saturating_sub(r.unchanged_since_ms) >= DORMANT_MS {
        r.dormant = true;
        return false;
    }
    let delays = [1000, 2000, 5000, 10000, 30000];
    r.next_at_ms = now + delays[r.attempt.min(delays.len() - 1)];
    r.attempt = r.attempt.saturating_add(1);
    true
}
/// Explicit worker recovery can retry a dormant record even if no file grew.
pub fn wake(path: &Path, state: &mut Value, sid: &str) -> Result<(), String> {
    update_state_transaction(path, state, |candidate| {
        let now = now_ms();
        for mut record in records(candidate, sid) {
            if !record.has_recovery_work() {
                continue;
            }
            record.dormant = false;
            record.attempt = 0;
            record.next_at_ms = now;
            record.unchanged_since_ms = now;
            put(candidate, sid, &record);
        }
    })
    .map(|_| ())
}
pub fn due(path: &Path, state: &mut Value, sid: &str) -> Result<RecoverySchedule, String> {
    let mut selected = None;
    let mut wait = None;
    // Discovery/file IO occurs outside the transaction; commit only scheduling
    // fields so a concurrent delivery checkpoint cannot be overwritten.
    let mut observations = records(state, sid);
    let now = now_ms();
    observations.sort_by_key(|r| r.next_at_ms);
    let mut updates = Vec::new();
    for observation in &mut observations {
        if !observation.has_recovery_work() {
            continue;
        }
        if !observation.dormant && now < observation.next_at_ms {
            let delay = observation.next_at_ms - now;
            wait = Some(wait.map_or(delay, |d: u64| d.min(delay)));
            continue;
        }
        let changed = refresh_sources(observation, sid);
        updates.push((observation, changed));
    }
    // One transaction for the scheduling pass, with keyed lookup per turn.
    if !updates.is_empty() {
        update_state_transaction(path, state, |candidate| {
            for (observation, changed) in &updates {
                let Some(mut r) = record(candidate, sid, &observation.parent_turn_id) else {
                    continue;
                };
                r.child_paths = observation.child_paths.clone();
                r.snapshots = observation.snapshots.clone();
                if !r.has_recovery_work() {
                    continue;
                }
                // Preserve growth for every turn, including one not selected in
                // this slice; otherwise a dormant sibling loses its wakeup.
                if *changed {
                    mark_source_progress(&mut r, now);
                }
                if selected.is_none() && schedule_record(&mut r, now, false) {
                    selected = Some(r.clone());
                }
                if !r.dormant {
                    let delay = r.next_at_ms.saturating_sub(now).max(1);
                    wait = Some(wait.map_or(delay, |d: u64| d.min(delay)));
                }
                put(candidate, sid, &r);
            }
        })?;
    }
    Ok(if let Some(r) = selected {
        RecoverySchedule::Ready(r)
    } else if let Some(ms) = wait {
        RecoverySchedule::Waiting(Duration::from_millis(ms))
    } else {
        RecoverySchedule::Idle
    })
}
#[cfg(test)]
mod tests {
    use super::*;
    fn body(id: &str) -> Value {
        json!({"resourceSpans":[{"scopeSpans":[{"spans":[{"traceId":"trace","spanId":"root","parentSpanId":""},{"traceId":"trace","spanId":id,"parentSpanId":"root"}]}]}]})
    }

    fn body_with_marked_upstream_root(id: &str) -> Value {
        json!({"resourceSpans":[{"scopeSpans":[{"spans":[{"traceId":"trace","spanId":"root","parentSpanId":"upstream","attributes":[{"key":"_internal.meta.root","value":{"stringValue":"1"}}]},{"traceId":"trace","spanId":id,"parentSpanId":"root","attributes":[]}]}]}]})
    }

    #[test]
    fn simultaneous_source_growth_wakes_each_dormant_turn() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("state.json");
        let mut state = json!({});
        for turn in ["first", "second"] {
            let source = dir.path().join(format!("{turn}.jsonl"));
            std::fs::write(&source, "{}\n").unwrap();
            register(
                &path,
                &mut state,
                "p",
                turn,
                source.to_str().unwrap(),
                &body("child"),
                &[],
                "u",
                "v",
                None,
            )
            .unwrap();
        }
        update_state_transaction(&path, &mut state, |candidate| {
            for mut r in records(candidate, "p") {
                refresh_sources(&mut r, "p");
                r.dormant = true;
                r.next_at_ms = 0;
                put(candidate, "p", &r);
            }
        })
        .unwrap();
        for turn in ["first", "second"] {
            std::fs::write(dir.path().join(format!("{turn}.jsonl")), "{}\n{}\n").unwrap();
        }
        let RecoverySchedule::Ready(first) = due(&path, &mut state, "p").unwrap() else {
            panic!("one changed turn must be ready");
        };
        assert!(records(&state, "p").iter().all(|r| !r.dormant));
        let RecoverySchedule::Ready(second) = due(&path, &mut state, "p").unwrap() else {
            panic!("other changed turn must remain eligible without another source write");
        };
        assert_ne!(first.parent_turn_id, second.parent_turn_id);
    }

    #[test]
    fn delivery_context_fingerprints_resolved_destination_and_retry_policy() {
        let config = LangfuseConfig {
            public_key: Some("pk".to_string()),
            secret_key: Some("sk".to_string()),
            host: "http://collector-a".to_string(),
            headers: Some(
                BTreeMap::from([("x-route".to_string(), "a".to_string())])
                    .into_iter()
                    .collect(),
            ),
        };
        let context = delivery_context(&config, RetryPolicy::AtMostOnce);

        assert_eq!(context.retry_policy, "at_most_once");
        assert!(config_matches_delivery_context(&config, &context));

        let mut changed_host = config.clone();
        changed_host.host = "http://collector-b".to_string();
        assert!(!config_matches_delivery_context(&changed_host, &context));

        let mut changed_secret = config.clone();
        changed_secret.secret_key = Some("other".to_string());
        assert!(!config_matches_delivery_context(&changed_secret, &context));
    }

    #[test]
    fn topology_uses_root_marker_when_upstream_parent_exists() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("state.json");
        let mut state = json!({});
        register(
            &path,
            &mut state,
            "p",
            "t",
            "missing",
            &body_with_marked_upstream_root("child-span"),
            &["c".into()],
            "u",
            "v",
            None,
        )
        .unwrap();
        let record = records(&state, "p").remove(0);
        assert_eq!(record.trace_id, "trace");
        assert_eq!(record.root_span_id, "root");
    }

    #[test]
    fn topology_rejects_ambiguous_root_markers() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("state.json");
        let mut state = json!({});
        let mut body = body_with_marked_upstream_root("child-span");
        body["resourceSpans"][0]["scopeSpans"][0]["spans"][1]["attributes"]
            .as_array_mut()
            .unwrap()
            .push(json!({"key":"_internal.meta.root","value":{"stringValue":"1"}}));

        let err = register(
            &path,
            &mut state,
            "p",
            "t",
            "missing",
            &body,
            &["c".into()],
            "u",
            "v",
            None,
        )
        .unwrap_err();
        assert!(err.contains("parent export body has 2 root markers"));
        assert!(records(&state, "p").is_empty());
    }

    #[test]
    fn ledger_survives_parent_checkpoint_and_keeps_turns_independent() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("state.json");
        let mut s = json!({});
        for turn in ["t1", "t2"] {
            register(
                &path,
                &mut s,
                "p",
                turn,
                "missing",
                &body("first"),
                &["c".into()],
                "u",
                "v",
                None,
            )
            .unwrap();
        }
        account(&path, &mut s, "p", "t1", &body("first"), &["c".into()]).unwrap();
        let r = records(&s, "p");
        assert_eq!(r.len(), 2);
        let a = r.iter().find(|r| r.parent_turn_id == "t1").unwrap();
        let mut next = body("second");
        assert_eq!(suppress_accounted(&mut next, a), 1);
        assert_eq!(span_ids(&next), vec!["second"]);
        assert!(r
            .iter()
            .find(|r| r.parent_turn_id == "t2")
            .unwrap()
            .accounted_span_ids
            .is_empty());
        super::super::state::checkpoint_turn_export_success(
            &path,
            &mut s,
            "p",
            "t1",
            "now",
            Some("missing"),
            &[],
            &[],
        )
        .unwrap();
        assert_eq!(records(&s, "p").len(), 2);
    }
    #[test]
    fn backoff_becomes_dormant_and_child_change_wakes_it() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("s");
        let mut s = json!({});
        register(
            &path,
            &mut s,
            "p",
            "t",
            "missing",
            &body("one"),
            &["c".into()],
            "u",
            "v",
            None,
        )
        .unwrap();
        let mut r = records(&s, "p").remove(0);
        r.unchanged_since_ms = 100;
        r.next_at_ms = 100;
        for (now, delay) in [
            (100, 1000),
            (1100, 2000),
            (3100, 5000),
            (8100, 10000),
            (18100, 30000),
        ] {
            assert!(schedule_record(&mut r, now, false));
            assert_eq!(r.next_at_ms, now + delay);
        }
        assert!(!schedule_record(&mut r, 100 + DORMANT_MS, false));
        assert!(r.dormant);
        assert!(schedule_record(&mut r, 101 + DORMANT_MS, true));
        assert!(!r.dormant);
    }
    #[test]
    fn completion_keeps_ledger_for_duplicate_finalizer() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("s");
        let mut s = json!({});
        register(
            &path,
            &mut s,
            "p",
            "t",
            "missing",
            &body("one"),
            &["c".into()],
            "u",
            "v",
            None,
        )
        .unwrap();
        account(&path, &mut s, "p", "t", &body("one"), &[]).unwrap();
        assert_eq!(pending_recovery_count(&s, "p"), 0);
        let mut repeated = body("one");
        assert_eq!(suppress_accounted(&mut repeated, &records(&s, "p")[0]), 2);
    }
    #[test]
    fn child_file_growth_wakes_dormant_without_parent_change() {
        let dir = tempfile::tempdir().unwrap();
        let parent = dir.path().join("parent.jsonl");
        let child = dir.path().join("child.jsonl");
        std::fs::write(&parent, "{}\n").unwrap();
        std::fs::write(&child, "{}\n").unwrap();
        let path = dir.path().join("state.json");
        let mut state = json!({});
        register(
            &path,
            &mut state,
            "p",
            "t",
            parent.to_str().unwrap(),
            &body("one"),
            &["c".into()],
            "u",
            "v",
            None,
        )
        .unwrap();
        let mut r = records(&state, "p").remove(0);
        r.child_paths
            .insert("c".into(), child.to_string_lossy().into_owned());
        assert!(refresh_sources(&mut r, "p"));
        r.dormant = true;
        r.unchanged_since_ms = 1;
        r.next_at_ms = 1;
        put(&mut state, "p", &r);
        super::super::state::update_state_transaction(&path, &mut state, |candidate| {
            put(candidate, "p", &r)
        })
        .unwrap();
        std::fs::write(&child, "{}\n{}\n").unwrap();
        assert!(matches!(
            due(&path, &mut state, "p").unwrap(),
            RecoverySchedule::Ready(_)
        ));
        assert!(!records(&state, "p")[0].dormant);
    }
    #[test]
    fn unknown_state_fields_and_delivery_ids_survive_registration() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("state.json");
        let mut state = json!({});
        register(
            &path,
            &mut state,
            "p",
            "t",
            "missing",
            &body("one"),
            &["c".into()],
            "u",
            "v",
            None,
        )
        .unwrap();
        account(&path, &mut state, "p", "t", &body("one"), &["c".into()]).unwrap();
        super::super::state::update_state_transaction(&path, &mut state, |s| {
            s["p"]["foreign"] = json!(42)
        })
        .unwrap();
        register(
            &path,
            &mut state,
            "p",
            "t",
            "missing",
            &body("two"),
            &["c".into()],
            "u",
            "v",
            None,
        )
        .unwrap();
        assert_eq!(state["p"]["foreign"], 42);
        assert!(records(&state, "p")[0]
            .accounted_span_ids
            .contains(&"one".to_string()));
    }
    #[test]
    fn unfinished_delivery_survives_chunk_checkpoint_with_no_pending_child() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("state.json");
        let mut state = json!({});
        register(
            &path,
            &mut state,
            "p",
            "t",
            "missing",
            &body("one"),
            &["c".into()],
            "u",
            "v",
            None,
        )
        .unwrap();
        account_chunk(&path, &mut state, "p", "t", &body("one"), &[], true).unwrap();
        state = super::super::state::load_state(&path);
        assert_eq!(pending_recovery_count(&state, "p"), 1);
        assert!(records(&state, "p")[0].needs_delivery);
        account_chunk(&path, &mut state, "p", "t", &body("two"), &[], false).unwrap();
        assert_eq!(pending_recovery_count(&state, "p"), 0);
    }
}
