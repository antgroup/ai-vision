use crate::common::dirs;
use crate::common::logger::now_iso8601_pub;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::ffi::OsStr;
use std::fs;
use std::fs::OpenOptions;
use std::io::Write;
#[cfg(unix)]
use std::os::unix::fs::OpenOptionsExt;
use std::path::{Path, PathBuf};
use std::sync::{
    atomic::{AtomicBool, Ordering},
    Arc,
};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

#[cfg(test)]
const SCHEMA_VERSION: u32 = 1;
pub const REALTIME_SCHEMA_VERSION: u32 = 2;
pub const LEGACY_REALTIME_SESSION_SCHEMA_VERSION: u32 = 1;
pub const CURRENT_REALTIME_SESSION_SCHEMA_VERSION: u32 = 2;
pub const REALTIME_BATCH_MANIFEST_SCHEMA_VERSION: u32 = 1;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RealtimeSpoolKind {
    Tool,
    Generation,
    Finalizer,
    Flush,
    Batch,
}

impl RealtimeSpoolKind {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Tool => "tool",
            Self::Generation => "generation",
            Self::Finalizer => "finalizer",
            Self::Flush => "flush",
            Self::Batch => "batch",
        }
    }
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SpoolJobKind {
    #[default]
    Legacy,
    Realtime(RealtimeSpoolKind),
}

#[cfg(test)]
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PublishedRealtimeJob {
    pub path: PathBuf,
    pub job_id: String,
    pub payload_digest: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RealtimeBatchManifest {
    pub schema_version: u32,
    pub batch_id: String,
    pub subject_id: String,
    pub session_schema_version: u32,
    #[serde(default)]
    pub semantic_generation_boundary: Option<Value>,
    #[serde(default)]
    pub member_ids: Vec<String>,
    #[serde(default)]
    pub span_ids: Vec<String>,
    #[serde(default)]
    pub digests: Vec<String>,
    pub compensation_round: u32,
    pub delivery_policy: String,
    pub item_count: usize,
    pub otlp_digest: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RealtimeFlushManifest {
    pub schema_version: u32,
    pub trigger_id: String,
    pub turn_id: String,
    pub transcript_path: String,
    pub scan_anchor: u64,
    pub generation_batch_size: usize,
    pub subject_id: String,
    #[serde(default)]
    pub cwd: Option<String>,
    #[serde(default)]
    pub username: Option<String>,
    #[serde(default)]
    pub hook_version: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SpoolJob {
    pub schema_version: u32,
    #[serde(default = "default_session_schema_version")]
    pub session_schema_version: u32,
    #[serde(default)]
    pub job_kind: SpoolJobKind,
    pub job_id: String,
    pub session_id: String,
    pub turn_id: Option<String>,
    pub created_at: String,
    pub claimed_at: Option<String>,
    pub attempts: u32,
    pub last_error: Option<String>,
    pub otel: Value,
    #[serde(default)]
    pub transcript_path: Option<String>,
    #[serde(default)]
    pub cwd: Option<String>,
    #[serde(default)]
    pub username: Option<String>,
    #[serde(default)]
    pub hook_version: Option<String>,
    #[serde(default)]
    pub subject_id: Option<String>,
    #[serde(default)]
    pub context_id: Option<String>,
    #[serde(default)]
    pub payload_digest: Option<String>,
    #[serde(default)]
    pub batch_manifest: Option<RealtimeBatchManifest>,
    #[serde(default)]
    pub flush_manifest: Option<RealtimeFlushManifest>,
}

#[derive(Debug)]
pub struct ClaimedJob {
    pub job: SpoolJob,
    pub path: PathBuf,
    root: PathBuf,
    pub _session_lock: SessionLock,
}

#[derive(Debug, Clone, Serialize)]
pub struct SpoolStatus {
    pub pending: usize,
    pub claimed: usize,
    pub done: usize,
    pub failed: usize,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PendingDisposition {
    Keep,
    Done,
    Failed,
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct PendingSettlement {
    pub done: usize,
    pub failed: usize,
}

#[derive(Debug)]
pub struct SessionLock {
    path: PathBuf,
    stop: Arc<AtomicBool>,
    heartbeat: Option<std::thread::JoinHandle<()>>,
}

impl Drop for SessionLock {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Release);
        if let Some(handle) = self.heartbeat.take() {
            handle.thread().unpark();
            let _ = handle.join();
        }
        let _ = fs::remove_dir_all(&self.path);
    }
}

pub fn default_root() -> PathBuf {
    dirs::spool_dir()
}

pub fn session_root(root: &Path, session_id: &str) -> Option<PathBuf> {
    dirs::session_key(session_id).map(|key| root.join("sessions").join(key))
}

#[cfg(test)]
pub fn session_roots(root: &Path) -> Result<Vec<PathBuf>, String> {
    read_sorted_dirs(&root.join("sessions"))
}

#[cfg(test)]
pub fn enqueue(
    root: &Path,
    session_id: &str,
    turn_id: Option<&str>,
    otel: Value,
) -> Result<PathBuf, String> {
    let created_at = now_iso8601_pub();
    let job_id = job_id(session_id, turn_id, &otel);
    let job = SpoolJob {
        schema_version: SCHEMA_VERSION,
        session_schema_version: LEGACY_REALTIME_SESSION_SCHEMA_VERSION,
        job_kind: SpoolJobKind::Legacy,
        job_id: job_id.clone(),
        session_id: session_id.to_string(),
        turn_id: turn_id.map(ToOwned::to_owned),
        created_at,
        claimed_at: None,
        attempts: 0,
        last_error: None,
        otel,
        transcript_path: None,
        cwd: None,
        username: None,
        hook_version: None,
        subject_id: None,
        context_id: None,
        payload_digest: None,
        batch_manifest: None,
        flush_manifest: None,
    };

    let pending_dir = root.join("pending");
    dirs::ensure_private_dir(&pending_dir)
        .map_err(|err| format!("failed to create pending spool dir: {err}"))?;
    let final_path = pending_dir.join(format!("{job_id}.json"));
    write_json_atomically(&final_path, &job)?;
    Ok(final_path)
}

#[cfg(test)]
pub fn enqueue_transcript(
    root: &Path,
    session_id: &str,
    transcript_path: &str,
) -> Result<PathBuf, String> {
    enqueue_transcript_with_context(root, session_id, transcript_path, None, None, None)
}

#[cfg(test)]
pub fn enqueue_transcript_with_context(
    root: &Path,
    session_id: &str,
    transcript_path: &str,
    cwd: Option<&str>,
    username: Option<&str>,
    hook_version: Option<&str>,
) -> Result<PathBuf, String> {
    let job_id = stable_job_id(session_id, transcript_path);
    let job = SpoolJob {
        schema_version: SCHEMA_VERSION,
        session_schema_version: LEGACY_REALTIME_SESSION_SCHEMA_VERSION,
        job_kind: SpoolJobKind::Legacy,
        job_id: job_id.clone(),
        session_id: session_id.to_string(),
        turn_id: None,
        created_at: now_iso8601_pub(),
        claimed_at: None,
        attempts: 0,
        last_error: None,
        otel: Value::Null,
        transcript_path: Some(transcript_path.to_string()),
        cwd: cwd.map(str::to_string),
        username: username.map(str::to_string),
        hook_version: hook_version.map(str::to_string),
        subject_id: None,
        context_id: None,
        payload_digest: None,
        batch_manifest: None,
        flush_manifest: None,
    };
    let dir = root.join("pending");
    dirs::ensure_private_dir(&dir)
        .map_err(|e| format!("failed to create pending spool dir: {e}"))?;
    let path = dir.join(format!("{job_id}.json"));
    // Only an outstanding job is a duplicate. A transcript is append-only, so
    // a later Stop for the same path must enqueue a new generation after the
    // previous job reached done/failed.
    for state_dir in ["pending", "claimed"] {
        let candidate = root.join(state_dir).join(format!("{job_id}.json"));
        if candidate.exists() {
            return Ok(candidate);
        }
    }
    write_json_atomically(&path, &job)?;
    Ok(path)
}

#[cfg(test)]
fn stable_job_id(session_id: &str, transcript_path: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(session_id.as_bytes());
    hasher.update([0u8]);
    hasher.update(transcript_path.as_bytes());
    let digest = hasher.finalize();
    let hex = digest
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect::<String>();
    format!("transcript-{hex}")
}

#[cfg(test)]
pub fn publish_realtime_job(
    root: &Path,
    kind: RealtimeSpoolKind,
    session_id: &str,
    context_id: &str,
    subject_id: &str,
    payload: Value,
) -> Result<PublishedRealtimeJob, String> {
    publish_realtime_job_with_session_schema(
        root,
        kind,
        LEGACY_REALTIME_SESSION_SCHEMA_VERSION,
        session_id,
        context_id,
        subject_id,
        payload,
        None,
        None,
    )
}

#[cfg(test)]
pub fn publish_realtime_flush_job(
    root: &Path,
    session_id: &str,
    context_id: &str,
    flush_manifest: RealtimeFlushManifest,
) -> Result<PublishedRealtimeJob, String> {
    let subject_id = flush_manifest.trigger_id.clone();
    publish_realtime_job_with_session_schema(
        root,
        RealtimeSpoolKind::Flush,
        CURRENT_REALTIME_SESSION_SCHEMA_VERSION,
        session_id,
        context_id,
        &subject_id,
        Value::Null,
        None,
        Some(flush_manifest),
    )
}

#[cfg(test)]
pub fn publish_realtime_batch_job(
    root: &Path,
    session_id: &str,
    context_id: &str,
    subject_id: &str,
    payload: Value,
    member_ids: Vec<String>,
    span_ids: Vec<String>,
    digests: Vec<String>,
    semantic_generation_boundary: Option<Value>,
    compensation_round: u32,
    delivery_policy: &str,
) -> Result<PublishedRealtimeJob, String> {
    if payload.is_null() {
        return Err("realtime batch job must carry a non-null OTEL payload".to_string());
    }
    if member_ids.len() != span_ids.len() || member_ids.len() != digests.len() {
        return Err(format!(
            "realtime batch manifest arrays must have equal lengths: members={} spans={} digests={}",
            member_ids.len(),
            span_ids.len(),
            digests.len()
        ));
    }
    let payload_digest = payload_digest(&payload)?;
    let mut members = member_ids
        .into_iter()
        .zip(span_ids)
        .zip(digests)
        .map(|((member_id, span_id), digest)| (member_id, span_id, digest))
        .collect::<Vec<_>>();
    members.sort_by(|left, right| left.0.cmp(&right.0));
    let mut member_ids = Vec::with_capacity(members.len());
    let mut span_ids = Vec::with_capacity(members.len());
    let mut digests = Vec::with_capacity(members.len());
    for (member_id, span_id, digest) in members {
        member_ids.push(member_id);
        span_ids.push(span_id);
        digests.push(digest);
    }
    let batch_id = deterministic_realtime_job_id_for_session_schema(
        RealtimeSpoolKind::Batch,
        CURRENT_REALTIME_SESSION_SCHEMA_VERSION,
        session_id,
        context_id,
        subject_id,
        &payload_digest,
    );
    let manifest = RealtimeBatchManifest {
        schema_version: REALTIME_BATCH_MANIFEST_SCHEMA_VERSION,
        batch_id,
        subject_id: subject_id.to_string(),
        session_schema_version: CURRENT_REALTIME_SESSION_SCHEMA_VERSION,
        semantic_generation_boundary,
        item_count: member_ids.len(),
        member_ids,
        span_ids,
        digests,
        compensation_round,
        delivery_policy: delivery_policy.to_string(),
        otlp_digest: payload_digest.clone(),
    };
    publish_realtime_job_with_session_schema(
        root,
        RealtimeSpoolKind::Batch,
        CURRENT_REALTIME_SESSION_SCHEMA_VERSION,
        session_id,
        context_id,
        subject_id,
        payload,
        Some(manifest),
        None,
    )
}

#[cfg(test)]
pub fn publish_realtime_job_with_session_schema(
    root: &Path,
    kind: RealtimeSpoolKind,
    session_schema_version: u32,
    session_id: &str,
    context_id: &str,
    subject_id: &str,
    payload: Value,
    batch_manifest: Option<RealtimeBatchManifest>,
    flush_manifest: Option<RealtimeFlushManifest>,
) -> Result<PublishedRealtimeJob, String> {
    if kind == RealtimeSpoolKind::Flush && !payload.is_null() {
        return Err("realtime flush job must carry otel:null".to_string());
    }
    if kind == RealtimeSpoolKind::Batch && payload.is_null() {
        return Err("realtime batch job must carry a non-null OTEL payload".to_string());
    }
    if kind == RealtimeSpoolKind::Batch && batch_manifest.is_none() {
        return Err("realtime batch job must carry a batch manifest".to_string());
    }
    if kind != RealtimeSpoolKind::Batch && batch_manifest.is_some() {
        return Err("only realtime batch jobs may carry a batch manifest".to_string());
    }
    if kind == RealtimeSpoolKind::Flush && flush_manifest.is_none() {
        return Err("realtime flush job must carry a flush manifest".to_string());
    }
    if kind != RealtimeSpoolKind::Flush && flush_manifest.is_some() {
        return Err("only realtime flush jobs may carry a flush manifest".to_string());
    }
    let payload_digest = payload_digest(&payload)?;
    if let Some(manifest) = batch_manifest.as_ref() {
        if manifest.otlp_digest != payload_digest {
            return Err("realtime batch manifest digest mismatch".to_string());
        }
        if manifest.session_schema_version != session_schema_version {
            return Err("realtime batch manifest session schema mismatch".to_string());
        }
        if manifest.subject_id != subject_id {
            return Err("realtime batch manifest subject mismatch".to_string());
        }
    }
    if let Some(manifest) = flush_manifest.as_ref() {
        if manifest.trigger_id != subject_id {
            return Err("realtime flush manifest trigger mismatch".to_string());
        }
        if manifest.schema_version == 0 {
            return Err("realtime flush manifest schema version must be positive".to_string());
        }
        if manifest.generation_batch_size == 0 {
            return Err(
                "realtime flush manifest generation batch size must be positive".to_string(),
            );
        }
    }
    let deterministic_job_id = deterministic_realtime_job_id_for_session_schema(
        kind,
        session_schema_version,
        session_id,
        context_id,
        subject_id,
        &payload_digest,
    );
    let job_id = if let Some(manifest) = batch_manifest.as_ref() {
        if manifest.batch_id != deterministic_job_id {
            return Err("realtime batch manifest batch_id mismatch".to_string());
        }
        manifest.batch_id.clone()
    } else {
        deterministic_job_id
    };
    let job = SpoolJob {
        schema_version: REALTIME_SCHEMA_VERSION,
        session_schema_version,
        job_kind: SpoolJobKind::Realtime(kind),
        job_id: job_id.clone(),
        session_id: session_id.to_string(),
        turn_id: None,
        created_at: now_iso8601_pub(),
        claimed_at: None,
        attempts: 0,
        last_error: None,
        otel: payload,
        transcript_path: None,
        cwd: None,
        username: None,
        hook_version: None,
        subject_id: Some(subject_id.to_string()),
        context_id: Some(context_id.to_string()),
        payload_digest: Some(payload_digest.clone()),
        batch_manifest,
        flush_manifest,
    };

    let pending_dir = root.join("pending");
    dirs::ensure_private_dir(&pending_dir)
        .map_err(|err| format!("failed to create pending spool dir: {err}"))?;
    let path = pending_dir.join(format!("{job_id}.json"));
    if let Some(existing) =
        find_existing_realtime_job(root, kind, session_schema_version, &job_id, &payload_digest)?
    {
        return Ok(existing);
    }

    write_json_atomically(&path, &job)?;
    Ok(PublishedRealtimeJob {
        path,
        job_id,
        payload_digest,
    })
}

#[cfg(test)]
fn find_existing_realtime_job(
    root: &Path,
    kind: RealtimeSpoolKind,
    session_schema_version: u32,
    job_id: &str,
    payload_digest: &str,
) -> Result<Option<PublishedRealtimeJob>, String> {
    for state_dir in ["pending", "claimed", "done"] {
        let candidate = root.join(state_dir).join(format!("{job_id}.json"));
        if !candidate.exists() {
            continue;
        }
        let existing = read_job(&candidate)?;
        validate_existing_realtime_job(
            &existing,
            kind,
            session_schema_version,
            job_id,
            payload_digest,
        )?;
        return Ok(Some(PublishedRealtimeJob {
            path: candidate,
            job_id: job_id.to_string(),
            payload_digest: payload_digest.to_string(),
        }));
    }

    let failed_dir = root.join("failed");
    let pending_dir = root.join("pending");
    let direct_failed_path = failed_dir.join(format!("{job_id}.json"));
    if direct_failed_path.exists() {
        return recover_failed_realtime_job(
            &direct_failed_path,
            &pending_dir,
            kind,
            session_schema_version,
            job_id,
            payload_digest,
        )
        .map(Some);
    }

    for failed_path in read_sorted_json_files(&failed_dir)? {
        let failed = read_job(&failed_path)?;
        if failed.job_id != job_id {
            continue;
        }
        return recover_failed_realtime_job(
            &failed_path,
            &pending_dir,
            kind,
            session_schema_version,
            job_id,
            payload_digest,
        )
        .map(Some);
    }

    Ok(None)
}

#[cfg(test)]
fn recover_failed_realtime_job(
    failed_path: &Path,
    pending_dir: &Path,
    kind: RealtimeSpoolKind,
    session_schema_version: u32,
    job_id: &str,
    payload_digest: &str,
) -> Result<PublishedRealtimeJob, String> {
    let mut failed = read_job(failed_path)?;
    validate_existing_realtime_job(
        &failed,
        kind,
        session_schema_version,
        job_id,
        payload_digest,
    )?;
    dirs::ensure_private_dir(pending_dir)
        .map_err(|err| format!("failed to create pending spool dir: {err}"))?;
    let pending_path = pending_dir.join(format!("{job_id}.json"));
    failed.claimed_at = None;
    failed.attempts = 0;
    failed.last_error = None;
    write_json_atomically(failed_path, &failed)?;
    if pending_path.exists() {
        let pending = read_job(&pending_path)?;
        validate_existing_realtime_job(
            &pending,
            kind,
            session_schema_version,
            job_id,
            payload_digest,
        )?;
        fs::remove_file(failed_path).map_err(|err| {
            format!(
                "failed to remove duplicate failed realtime job '{}': {err}",
                failed_path.display()
            )
        })?;
    } else {
        fs::rename(failed_path, &pending_path).map_err(|err| {
            format!(
                "failed to recover failed realtime job '{}' to '{}': {err}",
                failed_path.display(),
                pending_path.display()
            )
        })?;
    }
    Ok(PublishedRealtimeJob {
        path: pending_path,
        job_id: job_id.to_string(),
        payload_digest: payload_digest.to_string(),
    })
}

#[cfg(test)]
fn validate_existing_realtime_job(
    existing: &SpoolJob,
    kind: RealtimeSpoolKind,
    session_schema_version: u32,
    job_id: &str,
    payload_digest: &str,
) -> Result<(), String> {
    if existing.job_kind != SpoolJobKind::Realtime(kind)
        || existing.session_schema_version != session_schema_version
        || existing.job_id != job_id
        || existing.payload_digest.as_deref() != Some(payload_digest)
    {
        return Err(format!(
            "realtime job id collision for {job_id}: identity mismatch"
        ));
    }
    Ok(())
}

#[cfg(test)]
pub fn claim_next(root: &Path, stale_after: Duration) -> Result<Option<ClaimedJob>, String> {
    claim_next_eligible(root, stale_after, |_| true)
}

pub fn pending_realtime_jobs(root: &Path, stale_after: Duration) -> Result<Vec<SpoolJob>, String> {
    let mut jobs = Vec::new();
    let Some(_session_lock) = SessionLock::try_acquire(root, stale_after)? else {
        return Ok(jobs);
    };
    for pending_path in read_sorted_json_files(&root.join("pending"))? {
        let job = match read_job(&pending_path) {
            Ok(job) => job,
            Err(error) => {
                move_pending_to_failed(
                    root,
                    &pending_path,
                    Some(&format!("invalid spool job: {error}")),
                )?;
                continue;
            }
        };
        if job.schema_version > REALTIME_SCHEMA_VERSION {
            move_pending_to_failed(
                root,
                &pending_path,
                Some(&format!(
                    "unsupported spool schema version {}",
                    job.schema_version
                )),
            )?;
            continue;
        }
        if matches!(job.job_kind, SpoolJobKind::Realtime(_)) {
            jobs.push(job);
        }
    }
    Ok(jobs)
}

pub fn claim_next_eligible<F>(
    root: &Path,
    stale_after: Duration,
    mut eligible: F,
) -> Result<Option<ClaimedJob>, String>
where
    F: FnMut(&SpoolJob) -> bool,
{
    let pending_root = root.join("pending");
    let claimed_root = root.join("claimed");
    dirs::ensure_private_dir(&claimed_root)
        .map_err(|err| format!("failed to create claimed spool dir: {err}"))?;
    let Some(session_lock) = SessionLock::try_acquire(root, stale_after)? else {
        return Ok(None);
    };
    let jobs = read_sorted_json_files(&pending_root)?;
    for pending_path in jobs {
        let job = match read_job(&pending_path) {
            Ok(job) => job,
            Err(error) => {
                move_pending_to_failed(
                    root,
                    &pending_path,
                    Some(&format!("invalid spool job: {error}")),
                )?;
                continue;
            }
        };
        if job.schema_version > REALTIME_SCHEMA_VERSION {
            move_pending_to_failed(
                root,
                &pending_path,
                Some(&format!(
                    "unsupported spool schema version {}",
                    job.schema_version
                )),
            )?;
            continue;
        }
        if !eligible(&job) {
            continue;
        }
        let claimed_dir = claimed_root.clone();
        dirs::ensure_private_dir(&claimed_dir)
            .map_err(|err| format!("failed to create session claimed dir: {err}"))?;
        let claimed_path = claimed_dir.join(
            pending_path
                .file_name()
                .ok_or_else(|| "pending job has no filename".to_string())?,
        );
        match fs::rename(&pending_path, &claimed_path) {
            Ok(()) => {
                let mut job = read_job(&claimed_path)?;
                job.claimed_at = Some(now_iso8601_pub());
                job.attempts = job.attempts.saturating_add(1);
                job.last_error = None;
                write_json_atomically(&claimed_path, &job)?;
                return Ok(Some(ClaimedJob {
                    job,
                    path: claimed_path,
                    root: root.to_path_buf(),
                    _session_lock: session_lock,
                }));
            }
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => continue,
            Err(err) => return Err(format!("failed to claim spool job: {err}")),
        }
    }

    Ok(None)
}

pub fn complete(claimed: ClaimedJob) -> Result<PathBuf, String> {
    let done_dir = claimed.path_root().join("done");
    dirs::ensure_private_dir(&done_dir)
        .map_err(|err| format!("failed to create done dir: {err}"))?;
    let done_path = done_dir.join(
        claimed
            .path
            .file_name()
            .ok_or_else(|| "claimed job has no filename".to_string())?,
    );
    fs::rename(&claimed.path, &done_path)
        .map_err(|err| format!("failed to move spool job to done: {err}"))?;
    Ok(done_path)
}

pub fn fail(mut claimed: ClaimedJob, error: &str, retry: bool) -> Result<PathBuf, String> {
    claimed.job.last_error = Some(error.to_string());
    claimed.job.claimed_at = None;
    let status_dir = if retry { "pending" } else { "failed" };
    let dest_dir = claimed.path_root().join(status_dir);
    dirs::ensure_private_dir(&dest_dir)
        .map_err(|err| format!("failed to create {status_dir} dir: {err}"))?;
    let dest_path = dest_dir.join(
        claimed
            .path
            .file_name()
            .ok_or_else(|| "claimed job has no filename".to_string())?,
    );
    write_json_atomically(&claimed.path, &claimed.job)?;
    fs::rename(&claimed.path, &dest_path)
        .map_err(|err| format!("failed to move spool job to {status_dir}: {err}"))?;
    Ok(dest_path)
}

#[cfg(test)]
pub fn requeue_failed_realtime_tool_jobs(
    root: &Path,
    session_id: &str,
    eligible_job_ids: &std::collections::HashSet<String>,
) -> Result<Vec<SpoolJob>, String> {
    if eligible_job_ids.is_empty() {
        return Ok(Vec::new());
    }
    let failed_dir = root.join("failed");
    let pending_dir = root.join("pending");
    dirs::ensure_private_dir(&pending_dir)
        .map_err(|err| format!("failed to create pending spool dir: {err}"))?;
    let mut requeued = Vec::new();
    for failed_path in read_sorted_json_files(&failed_dir)? {
        let mut job = read_job(&failed_path)?;
        if job.session_id != session_id
            || job.job_kind != SpoolJobKind::Realtime(RealtimeSpoolKind::Tool)
            || !eligible_job_ids.contains(&job.job_id)
        {
            continue;
        }
        job.claimed_at = None;
        job.attempts = 0;
        write_json_atomically(&failed_path, &job)?;
        let pending_path = pending_dir.join(
            failed_path
                .file_name()
                .ok_or_else(|| "failed realtime job has no filename".to_string())?,
        );
        if pending_path.exists() {
            let pending = read_job(&pending_path)?;
            if pending.payload_digest != job.payload_digest {
                return Err(format!(
                    "cannot requeue realtime tool job {}: pending digest mismatch",
                    job.job_id
                ));
            }
            fs::remove_file(&failed_path).map_err(|err| {
                format!(
                    "failed to remove duplicate failed realtime job '{}': {err}",
                    failed_path.display()
                )
            })?;
        } else {
            fs::rename(&failed_path, &pending_path).map_err(|err| {
                format!(
                    "failed to requeue realtime tool job '{}' to '{}': {err}",
                    failed_path.display(),
                    pending_path.display()
                )
            })?;
        }
        requeued.push(job);
    }
    Ok(requeued)
}

#[cfg(test)]
pub fn failed_realtime_batch_jobs(root: &Path, session_id: &str) -> Result<Vec<SpoolJob>, String> {
    let mut jobs = Vec::new();
    for path in read_sorted_json_files(&root.join("failed"))? {
        let job = read_job(&path)?;
        if job.session_id == session_id
            && job.job_kind == SpoolJobKind::Realtime(RealtimeSpoolKind::Batch)
        {
            jobs.push(job);
        }
    }
    Ok(jobs)
}

pub fn defer_claimed(mut claimed: ClaimedJob, reason: &str) -> Result<PathBuf, String> {
    claimed.job.last_error = Some(reason.to_string());
    claimed.job.claimed_at = None;
    claimed.job.attempts = claimed.job.attempts.saturating_sub(1);
    let dest_dir = claimed.path_root().join("pending");
    dirs::ensure_private_dir(&dest_dir)
        .map_err(|err| format!("failed to create pending dir: {err}"))?;
    let dest_path = dest_dir.join(
        claimed
            .path
            .file_name()
            .ok_or_else(|| "claimed job has no filename".to_string())?,
    );
    write_json_atomically(&claimed.path, &claimed.job)?;
    fs::rename(&claimed.path, &dest_path)
        .map_err(|err| format!("failed to defer spool job to pending: {err}"))?;
    Ok(dest_path)
}

pub fn recover_stale(root: &Path, stale_after: Duration) -> Result<usize, String> {
    let mut recovered = 0;
    for claimed_path in read_claimed_job_paths(root)? {
        if !is_stale(&claimed_path, stale_after)? {
            continue;
        }
        let mut job = read_job(&claimed_path)?;
        let session_lock = root.join("locks").join("worker.lock");
        if session_lock.exists() && !lock_is_stale(&session_lock, stale_after)? {
            continue;
        }
        job.claimed_at = None;
        job.last_error = Some("recovered stale claim".to_string());
        let pending_dir = root.join("pending");
        dirs::ensure_private_dir(&pending_dir)
            .map_err(|err| format!("failed to create pending dir: {err}"))?;
        let dest = pending_dir.join(
            claimed_path
                .file_name()
                .ok_or_else(|| "claimed job has no filename".to_string())?,
        );
        write_json_atomically(&claimed_path, &job)?;
        fs::rename(&claimed_path, dest)
            .map_err(|err| format!("failed to recover stale spool job: {err}"))?;
        recovered += 1;
    }
    recover_stale_locks(root, stale_after)?;
    Ok(recovered)
}

pub fn status(root: &Path) -> Result<SpoolStatus, String> {
    Ok(SpoolStatus {
        pending: count_jobs(&root.join("pending"))?,
        claimed: count_jobs(&root.join("claimed"))?,
        done: count_jobs(&root.join("done"))?,
        failed: count_jobs(&root.join("failed"))?,
    })
}

impl ClaimedJob {
    fn path_root(&self) -> &Path {
        &self.root
    }
}

impl SessionLock {
    pub fn try_acquire(root: &Path, stale_after: Duration) -> Result<Option<Self>, String> {
        let lock_root = root.join("locks");
        dirs::ensure_private_dir(&lock_root)
            .map_err(|err| format!("failed to create lock dir: {err}"))?;
        let path = lock_root.join("worker.lock");
        loop {
            match fs::create_dir(&path) {
                Ok(()) => {
                    let owner = json!({
                        "pid": std::process::id(),
                        "created_at": now_iso8601_pub(),
                    });
                    let owner_path = path.join("owner.json");
                    write_json_atomically(&owner_path, &owner)?;
                    let stop = Arc::new(AtomicBool::new(false));
                    let heartbeat_stop = Arc::clone(&stop);
                    let heartbeat_interval = std::cmp::min(
                        Duration::from_secs(1),
                        std::cmp::max(Duration::from_millis(25), stale_after / 3),
                    );
                    let heartbeat = std::thread::spawn(move || {
                        while !heartbeat_stop.load(Ordering::Acquire) {
                            std::thread::park_timeout(heartbeat_interval);
                            if heartbeat_stop.load(Ordering::Acquire) {
                                break;
                            }
                            let _ = fs::write(
                                &owner_path,
                                serde_json::to_vec(&owner).unwrap_or_default(),
                            );
                        }
                    });
                    return Ok(Some(Self {
                        path,
                        stop,
                        heartbeat: Some(heartbeat),
                    }));
                }
                Err(err) if err.kind() == std::io::ErrorKind::AlreadyExists => {
                    if !lock_is_stale(&path, stale_after)? {
                        return Ok(None);
                    }
                    match fs::remove_dir_all(&path) {
                        Ok(()) => continue,
                        Err(err) if err.kind() == std::io::ErrorKind::NotFound => continue,
                        Err(err) => {
                            return Err(format!("failed to remove stale session lock: {err}"));
                        }
                    }
                }
                Err(err) => return Err(format!("failed to acquire session lock: {err}")),
            }
        }
    }
}

fn recover_stale_locks(root: &Path, stale_after: Duration) -> Result<usize, String> {
    let mut recovered = 0;
    for lock_path in read_sorted_dirs(&root.join("locks"))? {
        if lock_is_stale(&lock_path, stale_after)? {
            fs::remove_dir_all(&lock_path)
                .map_err(|err| format!("failed to remove stale lock: {err}"))?;
            recovered += 1;
        }
    }
    Ok(recovered)
}

fn lock_is_stale(path: &Path, stale_after: Duration) -> Result<bool, String> {
    let owner = path.join("owner.json");
    if owner.exists() {
        is_stale(&owner, stale_after)
    } else {
        is_stale(path, stale_after)
    }
}

pub(crate) fn read_job(path: &Path) -> Result<SpoolJob, String> {
    let content = fs::read_to_string(path)
        .map_err(|err| format!("failed to read spool job '{}': {err}", path.display()))?;
    serde_json::from_str(&content)
        .map_err(|err| format!("failed to parse spool job '{}': {err}", path.display()))
}

fn write_json_atomically<T: Serialize>(path: &Path, value: &T) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        dirs::ensure_private_dir(parent)
            .map_err(|err| format!("failed to create '{}': {err}", parent.display()))?;
    }
    let tmp = path.with_extension(format!("tmp.{}.{}", std::process::id(), now_millis()));
    let data = serde_json::to_vec(value).map_err(|err| err.to_string())?;
    let mut options = OpenOptions::new();
    options.write(true).create(true).truncate(true);
    #[cfg(unix)]
    {
        options.mode(0o600);
    }
    let mut tmp_file = options
        .open(&tmp)
        .map_err(|err| format!("failed to write '{}': {err}", tmp.display()))?;
    tmp_file
        .write_all(&data)
        .map_err(|err| format!("failed to write '{}': {err}", tmp.display()))?;
    fs::rename(&tmp, path).map_err(|err| {
        format!(
            "failed to rename '{}' to '{}': {err}",
            tmp.display(),
            path.display()
        )
    })
}

fn read_sorted_dirs(path: &Path) -> Result<Vec<PathBuf>, String> {
    let mut dirs = Vec::new();
    let entries = match fs::read_dir(path) {
        Ok(entries) => entries,
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => return Ok(dirs),
        Err(err) => return Err(format!("failed to read '{}': {err}", path.display())),
    };
    for entry in entries {
        let entry = entry.map_err(|err| format!("failed to read dir entry: {err}"))?;
        if entry.file_type().map(|ft| ft.is_dir()).unwrap_or(false) {
            dirs.push(entry.path());
        }
    }
    dirs.sort();
    Ok(dirs)
}

fn read_sorted_json_files(path: &Path) -> Result<Vec<PathBuf>, String> {
    let mut files = Vec::new();
    let entries = match fs::read_dir(path) {
        Ok(entries) => entries,
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => return Ok(files),
        Err(err) => return Err(format!("failed to read '{}': {err}", path.display())),
    };
    for entry in entries {
        let entry = entry.map_err(|err| format!("failed to read dir entry: {err}"))?;
        if entry.file_type().map(|ft| ft.is_file()).unwrap_or(false)
            && entry.path().extension().and_then(OsStr::to_str) == Some("json")
        {
            files.push(entry.path());
        }
    }
    files.sort();
    Ok(files)
}

fn read_claimed_job_paths(root: &Path) -> Result<Vec<PathBuf>, String> {
    read_sorted_json_files(&root.join("claimed"))
}

fn count_jobs(path: &Path) -> Result<usize, String> {
    Ok(read_sorted_json_files(path)?.len())
}

fn is_stale(path: &Path, stale_after: Duration) -> Result<bool, String> {
    let modified = fs::metadata(path)
        .and_then(|metadata| metadata.modified())
        .map_err(|err| format!("failed to stat '{}': {err}", path.display()))?;
    Ok(modified.elapsed().unwrap_or(Duration::ZERO) >= stale_after)
}

pub fn payload_digest(value: &Value) -> Result<String, String> {
    let data = serde_json::to_vec(value).map_err(|err| err.to_string())?;
    Ok(sha256_hex(&data))
}

fn default_session_schema_version() -> u32 {
    LEGACY_REALTIME_SESSION_SCHEMA_VERSION
}

#[cfg(test)]
pub fn deterministic_realtime_job_id(
    kind: RealtimeSpoolKind,
    session_id: &str,
    context_id: &str,
    subject_id: &str,
    payload_digest: &str,
) -> String {
    deterministic_realtime_job_id_for_session_schema(
        kind,
        LEGACY_REALTIME_SESSION_SCHEMA_VERSION,
        session_id,
        context_id,
        subject_id,
        payload_digest,
    )
}

pub fn deterministic_realtime_job_id_for_session_schema(
    kind: RealtimeSpoolKind,
    session_schema_version: u32,
    session_id: &str,
    context_id: &str,
    subject_id: &str,
    payload_digest: &str,
) -> String {
    let mut input = Vec::new();
    if session_schema_version == LEGACY_REALTIME_SESSION_SCHEMA_VERSION {
        input.extend_from_slice(b"cdxhooks.realtime.spool.v2");
    } else {
        input.extend_from_slice(b"cdxhooks.realtime.spool.v2.session");
    }
    input.push(0);
    input.extend_from_slice(kind.as_str().as_bytes());
    input.push(0);
    if session_schema_version != LEGACY_REALTIME_SESSION_SCHEMA_VERSION {
        input.extend_from_slice(session_schema_version.to_string().as_bytes());
        input.push(0);
    }
    input.extend_from_slice(session_id.as_bytes());
    input.push(0);
    input.extend_from_slice(context_id.as_bytes());
    input.push(0);
    input.extend_from_slice(subject_id.as_bytes());
    input.push(0);
    input.extend_from_slice(payload_digest.as_bytes());
    format!("realtime-{}-{}", kind.as_str(), sha256_hex(&input))
}

pub fn realtime_job_identity_matches(job: &SpoolJob) -> Result<bool, String> {
    let SpoolJobKind::Realtime(kind) = job.job_kind else {
        return Ok(false);
    };
    if job.schema_version != REALTIME_SCHEMA_VERSION {
        return Ok(false);
    }
    let (Some(context_id), Some(subject_id), Some(stored_digest)) = (
        job.context_id.as_deref(),
        job.subject_id.as_deref(),
        job.payload_digest.as_deref(),
    ) else {
        return Ok(false);
    };
    let actual_digest = payload_digest(&job.otel)?;
    if stored_digest != actual_digest {
        return Ok(false);
    }
    if kind == RealtimeSpoolKind::Flush && !job.otel.is_null() {
        return Ok(false);
    }
    if kind == RealtimeSpoolKind::Flush {
        let Some(manifest) = job.flush_manifest.as_ref() else {
            return Ok(false);
        };
        if manifest.trigger_id != subject_id
            || manifest.schema_version == 0
            || manifest.generation_batch_size == 0
        {
            return Ok(false);
        }
    } else if job.flush_manifest.is_some() {
        return Ok(false);
    }
    if kind == RealtimeSpoolKind::Batch {
        if job.otel.is_null() {
            return Ok(false);
        }
        let Some(manifest) = job.batch_manifest.as_ref() else {
            return Ok(false);
        };
        if manifest.session_schema_version != job.session_schema_version
            || manifest.otlp_digest != actual_digest
            || manifest.subject_id != subject_id
            || manifest.batch_id
                != deterministic_realtime_job_id_for_session_schema(
                    RealtimeSpoolKind::Batch,
                    job.session_schema_version,
                    &job.session_id,
                    context_id,
                    subject_id,
                    &actual_digest,
                )
        {
            return Ok(false);
        }
    } else if job.batch_manifest.is_some() {
        return Ok(false);
    }
    let expected_job_id = deterministic_realtime_job_id_for_session_schema(
        kind,
        job.session_schema_version,
        &job.session_id,
        context_id,
        subject_id,
        &actual_digest,
    );
    Ok(job.job_id == expected_job_id)
}

#[cfg(test)]
pub fn fail_ineligible_jobs<F>(
    root: &Path,
    stale_after: Duration,
    reason: &str,
    mut eligible: F,
) -> Result<usize, String>
where
    F: FnMut(&SpoolJob) -> bool,
{
    let settlement = settle_pending_jobs(root, stale_after, reason, |job, stale| {
        if stale && job.job_kind != SpoolJobKind::Legacy && !eligible(job) {
            PendingDisposition::Failed
        } else {
            PendingDisposition::Keep
        }
    })?;
    Ok(settlement.failed)
}

pub fn settle_pending_jobs<F>(
    root: &Path,
    stale_after: Duration,
    failure_reason: &str,
    mut disposition: F,
) -> Result<PendingSettlement, String>
where
    F: FnMut(&SpoolJob, bool) -> PendingDisposition,
{
    let mut settlement = PendingSettlement::default();
    let Some(_session_lock) = SessionLock::try_acquire(root, stale_after)? else {
        return Ok(settlement);
    };
    for pending_path in read_sorted_json_files(&root.join("pending"))? {
        let stale = is_stale(&pending_path, stale_after)?;
        let job = match read_job(&pending_path) {
            Ok(job) => job,
            Err(error) => {
                move_pending_to_status(
                    root,
                    &pending_path,
                    "failed",
                    Some(&format!("invalid spool job: {error}")),
                )?;
                settlement.failed += 1;
                continue;
            }
        };
        match disposition(&job, stale) {
            PendingDisposition::Keep => {}
            PendingDisposition::Done => {
                move_pending_to_status(root, &pending_path, "done", None)?;
                settlement.done += 1;
            }
            PendingDisposition::Failed => {
                move_pending_to_failed(root, &pending_path, Some(failure_reason))?;
                settlement.failed += 1;
            }
        }
    }
    Ok(settlement)
}

fn move_pending_to_failed(
    root: &Path,
    pending_path: &Path,
    error: Option<&str>,
) -> Result<PathBuf, String> {
    move_pending_to_status(root, pending_path, "failed", error)
}

fn move_pending_to_status(
    root: &Path,
    pending_path: &Path,
    status: &str,
    error: Option<&str>,
) -> Result<PathBuf, String> {
    if let Some(error) = error {
        if let Ok(mut job) = read_job(pending_path) {
            job.last_error = Some(error.to_string());
            write_json_atomically(pending_path, &job)?;
        }
    }
    let status_dir = root.join(status);
    dirs::ensure_private_dir(&status_dir)
        .map_err(|err| format!("failed to create {status} dir: {err}"))?;
    let file_name = pending_path
        .file_name()
        .ok_or_else(|| "pending job has no filename".to_string())?;
    let mut status_path = status_dir.join(file_name);
    if status_path.exists() {
        status_path = status_dir.join(format!("{}-{}", now_millis(), file_name.to_string_lossy()));
    }
    fs::rename(pending_path, &status_path)
        .map_err(|err| format!("failed to move pending job to {status}: {err}"))?;
    Ok(status_path)
}

fn sha256_hex(data: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(data);
    hasher
        .finalize()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

#[cfg(test)]
fn job_id(session_id: &str, turn_id: Option<&str>, otel: &Value) -> String {
    let mut hasher = Sha256::new();
    hasher.update(session_id.as_bytes());
    if let Some(turn_id) = turn_id {
        hasher.update(turn_id.as_bytes());
    }
    hasher.update(serde_json::to_vec(otel).unwrap_or_default());
    let digest = hasher.finalize();
    let hex = digest
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect::<String>();
    format!("{}-{hex}", now_millis())
}

fn now_millis() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
}

#[cfg(test)]
mod tests {
    // These lifecycle tests protect the bounded legacy importer. Production
    // builds cannot compile the producer helpers they exercise.
    use super::*;

    #[test]
    fn enqueue_claim_complete_updates_counts() {
        let dir = tempfile::tempdir().unwrap();
        enqueue(
            dir.path(),
            "sess/1",
            Some("turn-1"),
            json!({"resourceSpans": []}),
        )
        .unwrap();
        assert_eq!(status(dir.path()).unwrap().pending, 1);

        let claimed = claim_next(dir.path(), Duration::from_secs(60))
            .unwrap()
            .expect("job should be claimed");
        assert_eq!(claimed.job.session_id, "sess/1");
        assert_eq!(claimed.job.attempts, 1);
        assert_eq!(status(dir.path()).unwrap().claimed, 1);

        complete(claimed).unwrap();
        let counts = status(dir.path()).unwrap();
        assert_eq!(counts.pending, 0);
        assert_eq!(counts.claimed, 0);
        assert_eq!(counts.done, 1);
    }

    #[test]
    fn stale_claim_is_recovered_to_pending() {
        let dir = tempfile::tempdir().unwrap();
        enqueue(dir.path(), "sess-1", None, json!({"resourceSpans": []})).unwrap();
        let claimed = claim_next(dir.path(), Duration::from_secs(60))
            .unwrap()
            .expect("job should be claimed");
        std::mem::forget(claimed);

        let recovered = recover_stale(dir.path(), Duration::ZERO).unwrap();
        assert_eq!(recovered, 1);
        let counts = status(dir.path()).unwrap();
        assert_eq!(counts.pending, 1);
        assert_eq!(counts.claimed, 0);
    }

    #[test]
    fn pending_realtime_jobs_returns_matching_jobs() {
        let dir = tempfile::tempdir().unwrap();
        let published = publish_realtime_job(
            dir.path(),
            RealtimeSpoolKind::Tool,
            "sess-1",
            "ctx-1",
            "tool-1",
            json!({"resourceSpans": [{"scopeSpans": []}]}),
        )
        .unwrap();
        enqueue(dir.path(), "sess-1", None, json!({"resourceSpans": []})).unwrap();

        let pending = pending_realtime_jobs(dir.path(), Duration::from_secs(60)).unwrap();
        assert_eq!(pending.len(), 1);
        assert_eq!(pending[0].job_id, published.job_id);
        assert!(realtime_job_identity_matches(&pending[0]).unwrap());
    }

    #[cfg(unix)]
    #[test]
    fn new_realtime_spool_file_is_private() {
        use std::os::unix::fs::PermissionsExt;

        let dir = tempfile::tempdir().unwrap();
        let published = publish_realtime_job(
            dir.path(),
            RealtimeSpoolKind::Tool,
            "sess-1",
            "ctx-1",
            "tool-private",
            json!({"resourceSpans": [{"scopeSpans": []}]}),
        )
        .unwrap();

        assert_eq!(
            fs::metadata(published.path).unwrap().permissions().mode() & 0o777,
            0o600
        );
    }

    #[test]
    fn canonical_failed_job_is_recovered_before_scanning_prefixed_entries() {
        let dir = tempfile::tempdir().unwrap();
        let payload = json!({"resourceSpans": [{"scopeSpans": []}]});
        let published = publish_realtime_job(
            dir.path(),
            RealtimeSpoolKind::Tool,
            "sess-1",
            "ctx-1",
            "tool-direct",
            payload.clone(),
        )
        .unwrap();
        let claimed = claim_next(dir.path(), Duration::from_secs(60))
            .unwrap()
            .unwrap();
        fail(claimed, "timeout", false).unwrap();
        fs::write(
            dir.path()
                .join("failed")
                .join(format!("000-{}.json", published.job_id)),
            b"{not-json",
        )
        .unwrap();

        let recovered = publish_realtime_job(
            dir.path(),
            RealtimeSpoolKind::Tool,
            "sess-1",
            "ctx-1",
            "tool-direct",
            payload,
        )
        .unwrap();

        assert_eq!(recovered.job_id, published.job_id);
        assert!(recovered.path.exists());
    }

    #[test]
    fn prefixed_failed_job_is_recovered_by_scan_fallback() {
        let dir = tempfile::tempdir().unwrap();
        let payload = json!({"resourceSpans": [{"scopeSpans": []}]});
        let published = publish_realtime_job(
            dir.path(),
            RealtimeSpoolKind::Tool,
            "sess-1",
            "ctx-1",
            "tool-prefixed",
            payload.clone(),
        )
        .unwrap();
        let claimed = claim_next(dir.path(), Duration::from_secs(60))
            .unwrap()
            .unwrap();
        fail(claimed, "timeout", false).unwrap();
        let canonical = dir
            .path()
            .join("failed")
            .join(format!("{}.json", published.job_id));
        let prefixed = dir
            .path()
            .join("failed")
            .join(format!("123-{}.json", published.job_id));
        fs::rename(canonical, &prefixed).unwrap();

        let recovered = publish_realtime_job(
            dir.path(),
            RealtimeSpoolKind::Tool,
            "sess-1",
            "ctx-1",
            "tool-prefixed",
            payload,
        )
        .unwrap();

        assert_eq!(recovered.job_id, published.job_id);
        assert!(recovered.path.exists());
        assert!(!prefixed.exists());
    }

    #[test]
    fn failed_realtime_tool_is_requeued_only_when_a_later_hook_selects_it() {
        let dir = tempfile::tempdir().unwrap();
        let published = publish_realtime_job(
            dir.path(),
            RealtimeSpoolKind::Tool,
            "sess-1",
            "ctx-1",
            "tool-1",
            json!({"resourceSpans": [{"scopeSpans": []}]}),
        )
        .unwrap();
        let claimed = claim_next(dir.path(), Duration::from_secs(60))
            .unwrap()
            .unwrap();
        fail(claimed, "timeout", false).unwrap();
        assert_eq!(status(dir.path()).unwrap().failed, 1);
        assert_eq!(status(dir.path()).unwrap().pending, 0);

        let requeued = requeue_failed_realtime_tool_jobs(
            dir.path(),
            "sess-1",
            &std::collections::HashSet::from([published.job_id.clone()]),
        )
        .unwrap();
        assert_eq!(requeued.len(), 1);
        assert_eq!(requeued[0].attempts, 0);
        assert_eq!(requeued[0].last_error.as_deref(), Some("timeout"));
        assert_eq!(status(dir.path()).unwrap().failed, 0);
        assert_eq!(status(dir.path()).unwrap().pending, 1);
    }

    #[test]
    fn pending_realtime_jobs_quarantines_corrupt_jobs_without_blocking_valid_jobs() {
        let dir = tempfile::tempdir().unwrap();
        let published = publish_realtime_job(
            dir.path(),
            RealtimeSpoolKind::Tool,
            "sess-1",
            "ctx-1",
            "tool-1",
            json!({"resourceSpans": [{"scopeSpans": []}]}),
        )
        .unwrap();
        let corrupt_path = published.path.parent().unwrap().join("000-corrupt.json");
        std::fs::write(&corrupt_path, b"{not-json").unwrap();

        let pending = pending_realtime_jobs(dir.path(), Duration::from_secs(60)).unwrap();

        assert_eq!(pending.len(), 1);
        assert_eq!(pending[0].job_id, published.job_id);
        assert!(!corrupt_path.exists());
        assert!(dir.path().join("failed").join("000-corrupt.json").exists());
    }

    #[test]
    fn realtime_job_identity_rejects_tampered_digest() {
        let dir = tempfile::tempdir().unwrap();
        let published = publish_realtime_job(
            dir.path(),
            RealtimeSpoolKind::Generation,
            "sess-1",
            "ctx-1",
            "generation-1",
            json!({"resourceSpans": [{"scopeSpans": []}]}),
        )
        .unwrap();
        let mut job = read_job(&published.path).unwrap();
        job.payload_digest = Some("bad-digest".to_string());

        assert!(!realtime_job_identity_matches(&job).unwrap());
    }

    #[test]
    fn realtime_job_identity_rejects_tampered_job_id() {
        let dir = tempfile::tempdir().unwrap();
        let published = publish_realtime_job(
            dir.path(),
            RealtimeSpoolKind::Finalizer,
            "sess-1",
            "ctx-1",
            "finalizer-1",
            json!({"resourceSpans": [{"scopeSpans": []}]}),
        )
        .unwrap();
        let mut job = read_job(&published.path).unwrap();
        job.job_id = "realtime-finalizer-tampered".to_string();

        assert!(!realtime_job_identity_matches(&job).unwrap());
    }

    #[test]
    fn realtime_batch_manifest_sorts_correlated_rows_and_rejects_length_mismatch() {
        let dir = tempfile::tempdir().unwrap();
        let published = publish_realtime_batch_job(
            dir.path(),
            "sess-1",
            "ctx-1",
            "completed-1",
            json!({"resourceSpans": [{"scopeSpans": []}]}),
            vec!["tool:z".to_string(), "generation:a".to_string()],
            vec!["span-z".to_string(), "span-a".to_string()],
            vec!["digest-z".to_string(), "digest-a".to_string()],
            None,
            0,
            "at_least_once",
        )
        .unwrap();
        let manifest = read_job(&published.path)
            .unwrap()
            .batch_manifest
            .expect("Batch manifest");
        assert_eq!(manifest.member_ids, ["generation:a", "tool:z"]);
        assert_eq!(manifest.span_ids, ["span-a", "span-z"]);
        assert_eq!(manifest.digests, ["digest-a", "digest-z"]);

        let error = publish_realtime_batch_job(
            dir.path(),
            "sess-1",
            "ctx-1",
            "completed-2",
            json!({"resourceSpans": [{"scopeSpans": []}]}),
            vec!["tool:z".to_string()],
            Vec::new(),
            vec!["digest-z".to_string()],
            None,
            0,
            "at_least_once",
        )
        .unwrap_err();
        assert!(error.contains("equal lengths"));
    }
}
