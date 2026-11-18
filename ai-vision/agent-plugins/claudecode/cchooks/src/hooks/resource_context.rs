use std::path::Path;

use serde_json::Value;

use crate::common::git_state::{git_branch, git_remote_url};
use crate::common::hook::{env_equals_ignore_case, extract_session_id, HookEvent};
use crate::hooks::langfuse::state::{
    get_vcs_from_state, replace_vcs_snapshot, SessionStateStore, StateTxnError, VcsInfo,
};
use crate::hooks::{CommonContext, HandlerError, HookHandler};

const RESOURCE_CONTEXT_LOCK_TIMEOUT_MS: u64 = 500;

#[derive(Default)]
pub struct ResourceContextHandler;

impl ResourceContextHandler {
    pub fn new() -> Self {
        Self
    }
}

impl HookHandler for ResourceContextHandler {
    fn name(&self) -> &str {
        "resource_context"
    }

    fn supported_events(&self) -> &[HookEvent] {
        &[HookEvent::UserPromptSubmit]
    }

    fn handle(
        &self,
        event: &HookEvent,
        payload: &Value,
        context: &CommonContext,
    ) -> Result<(), HandlerError> {
        if !matches!(event, HookEvent::UserPromptSubmit) {
            return Ok(());
        }

        let session_id = extract_session_id(payload);
        let cwd = payload
            .get("cwd")
            .and_then(Value::as_str)
            .map(str::trim)
            .unwrap_or("");
        if session_id.is_empty() || cwd.is_empty() {
            context
                .logger
                .debug("resource_context: missing session_id or cwd; skipping capture");
            return Ok(());
        }

        let store = SessionStateStore::new(
            context.state_file.clone(),
            &session_id,
            RESOURCE_CONTEXT_LOCK_TIMEOUT_MS,
        );
        let cached = match store.read(|state| get_vcs_from_state(state, &session_id)) {
            Ok(cached) => cached,
            Err(StateTxnError::Busy { .. }) => {
                context
                    .logger
                    .warn("resource_context: state lock unavailable; skipping capture");
                return Ok(());
            }
            Err(error) => {
                context.logger.warn(&format!(
                    "resource_context: failed to read state; skipping capture: {error}"
                ));
                return Ok(());
            }
        };

        let force_remote_refresh = env_equals_ignore_case("CCHOOKS_VCS_NO_CACHE", "true");
        let Some(snapshot) = collect_vcs_snapshot(
            cached.as_ref(),
            cwd,
            force_remote_refresh,
            git_remote_url,
            git_branch,
        ) else {
            context
                .logger
                .debug("resource_context: Git snapshot unavailable; keeping existing state");
            return Ok(());
        };

        match store.update_session(|session| {
            replace_vcs_snapshot(session, &snapshot);
            Ok(())
        }) {
            Ok(()) => {
                context.logger.debug(&format!(
                    "resource_context: captured turn-start VCS snapshot for branch={}",
                    snapshot.branch
                ));
                Ok(())
            }
            Err(StateTxnError::Busy { .. }) => {
                context
                    .logger
                    .warn("resource_context: state lock unavailable; snapshot not persisted");
                Ok(())
            }
            Err(error) => Err(HandlerError::from(format!(
                "resource_context: failed to persist snapshot: {error}"
            ))),
        }
    }
}

fn collect_vcs_snapshot<Remote, Branch>(
    cached: Option<&VcsInfo>,
    cwd: &str,
    force_remote_refresh: bool,
    remote: Remote,
    branch: Branch,
) -> Option<VcsInfo>
where
    Remote: FnOnce(&Path) -> Option<String>,
    Branch: FnOnce(&Path) -> Option<String>,
{
    let cwd_path = Path::new(cwd);
    let remote_url = cached
        .filter(|vcs| !force_remote_refresh && vcs.cwd == cwd && !vcs.remote_url.trim().is_empty())
        .map(|vcs| vcs.remote_url.clone())
        .or_else(|| remote(cwd_path))?;
    let branch = branch(cwd_path)?;

    Some(VcsInfo {
        remote_url,
        branch,
        cwd: cwd.to_string(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::common::logger::Logger;
    use crate::hooks::langfuse::state::get_vcs_for_cwd_from_state;
    use crate::hooks::{ExportMode, RetryPolicy};
    use std::cell::Cell;
    use std::process::Command;

    fn git(cwd: &Path, args: &[&str]) {
        let status = Command::new("git")
            .args(args)
            .current_dir(cwd)
            .status()
            .unwrap();
        assert!(status.success(), "git {args:?} failed");
    }

    fn context(dir: &tempfile::TempDir) -> CommonContext {
        CommonContext {
            logger: Logger::new(dir.path().join("resource-context.log"), true),
            state_file: dir.path().join("state.json"),
            langfuse_config: None,
            username: "user".to_string(),
            projects_dir: String::new(),
            debug_enabled: false,
            hook_version: "test".to_string(),
            turn_seed: None,
            stop_transcript_end_offset: None,
            transcript_ingest_receipt: None,
            stop_budget: None,
            retry_policy: RetryPolicy::AtLeastOnce,
            export_mode: ExportMode::StopOnly,
            realtime_llm_batch_size: crate::hooks::DEFAULT_REALTIME_LLM_EXPORT_BATCH_SIZE,
            metrics: None,
            send_queue: std::cell::RefCell::new(Vec::new()),
            send_errors: std::sync::Arc::new(std::sync::Mutex::new(Vec::new())),
            stop_outcome: std::cell::RefCell::new(crate::hooks::StopOutcome::IntentionallySkipped),
        }
    }

    fn init_repository() -> tempfile::TempDir {
        let dir = tempfile::tempdir().unwrap();
        git(dir.path(), &["init", "-q"]);
        git(dir.path(), &["config", "user.email", "test@example.com"]);
        git(dir.path(), &["config", "user.name", "Test"]);
        git(dir.path(), &["commit", "--allow-empty", "-q", "-m", "init"]);
        git(
            dir.path(),
            &[
                "remote",
                "add",
                "upstream",
                "git@git.example.com:team/repo.git",
            ],
        );
        dir
    }

    fn cached_vcs(cwd: &str) -> VcsInfo {
        VcsInfo {
            remote_url: "git@example.com:cached/repo.git".to_string(),
            branch: "old".to_string(),
            cwd: cwd.to_string(),
        }
    }

    #[test]
    fn same_cwd_reuses_remote_but_refreshes_branch() {
        let remote_called = Cell::new(false);
        let branch_called = Cell::new(false);
        let snapshot = collect_vcs_snapshot(
            Some(&cached_vcs("/repo")),
            "/repo",
            false,
            |_| {
                remote_called.set(true);
                None
            },
            |_| {
                branch_called.set(true);
                Some("new-branch".to_string())
            },
        )
        .unwrap();

        assert!(!remote_called.get());
        assert!(branch_called.get());
        assert_eq!(snapshot.remote_url, "git@example.com:cached/repo.git");
        assert_eq!(snapshot.branch, "new-branch");
    }

    #[test]
    fn changed_cwd_or_no_cache_forces_remote_refresh() {
        for (cached_cwd, cwd, force) in [("/old", "/new", false), ("/repo", "/repo", true)] {
            let remote_called = Cell::new(false);
            let snapshot = collect_vcs_snapshot(
                Some(&cached_vcs(cached_cwd)),
                cwd,
                force,
                |_| {
                    remote_called.set(true);
                    Some("git@example.com:fresh/repo.git".to_string())
                },
                |_| Some("main".to_string()),
            )
            .unwrap();

            assert!(remote_called.get());
            assert_eq!(snapshot.remote_url, "git@example.com:fresh/repo.git");
            assert_eq!(snapshot.cwd, cwd);
        }
    }

    #[test]
    fn missing_remote_or_branch_does_not_create_partial_snapshot() {
        assert!(collect_vcs_snapshot(
            None,
            "/repo",
            false,
            |_| None,
            |_| { Some("main".to_string()) }
        )
        .is_none());
        assert!(collect_vcs_snapshot(
            None,
            "/repo",
            false,
            |_| Some("git@example.com:repo.git".to_string()),
            |_| None,
        )
        .is_none());
    }

    #[test]
    fn handler_persists_and_refreshes_only_the_current_session_snapshot() {
        let repo = init_repository();
        let state_dir = tempfile::tempdir().unwrap();
        let context = context(&state_dir);
        let cwd = repo.path().to_string_lossy().to_string();
        let payload = serde_json::json!({
            "session_id": "session-a",
            "cwd": cwd
        });

        ResourceContextHandler::new()
            .handle(&HookEvent::UserPromptSubmit, &payload, &context)
            .unwrap();
        let first = agentic_otel_common::state::load_state(&context.state_file);
        let first_vcs = get_vcs_for_cwd_from_state(&first, "session-a", &cwd).unwrap();
        assert_eq!(first_vcs.remote_url, "git@git.example.com:team/repo.git");
        assert!(first.get("session-b").is_none());

        git(repo.path(), &["checkout", "-q", "-b", "refreshed"]);
        ResourceContextHandler::new()
            .handle(&HookEvent::UserPromptSubmit, &payload, &context)
            .unwrap();
        let second = agentic_otel_common::state::load_state(&context.state_file);
        assert_eq!(
            get_vcs_for_cwd_from_state(&second, "session-a", &cwd)
                .unwrap()
                .branch,
            "refreshed"
        );
    }

    #[cfg(unix)]
    #[test]
    fn busy_state_lock_is_fail_open_and_leaves_state_unchanged() {
        let repo = init_repository();
        let state_dir = tempfile::tempdir().unwrap();
        let context = context(&state_dir);
        let original = serde_json::json!({"session-a": {"custom": "keep"}});
        crate::hooks::langfuse::state::save_state(&context.state_file, &original).unwrap();
        let lock = crate::hooks::langfuse::state::StateLock::acquire(&context.state_file, 100);
        assert!(lock.is_acquired());

        ResourceContextHandler::new()
            .handle(
                &HookEvent::UserPromptSubmit,
                &serde_json::json!({
                    "session_id": "session-a",
                    "cwd": repo.path().to_string_lossy()
                }),
                &context,
            )
            .unwrap();

        assert_eq!(
            agentic_otel_common::state::load_state(&context.state_file),
            original
        );
    }

    #[test]
    fn missing_session_or_cwd_does_not_create_state() {
        let state_dir = tempfile::tempdir().unwrap();
        let context = context(&state_dir);
        let handler = ResourceContextHandler::new();
        for payload in [
            serde_json::json!({"cwd": "/repo"}),
            serde_json::json!({"session_id": "session-a"}),
        ] {
            handler
                .handle(&HookEvent::UserPromptSubmit, &payload, &context)
                .unwrap();
        }
        assert!(!context.state_file.exists());
    }

    #[test]
    fn corrupt_state_is_fail_open_and_preserved_for_diagnosis() {
        let repo = init_repository();
        let state_dir = tempfile::tempdir().unwrap();
        let context = context(&state_dir);
        let corrupt = "{not valid json";
        std::fs::write(&context.state_file, corrupt).unwrap();

        ResourceContextHandler::new()
            .handle(
                &HookEvent::UserPromptSubmit,
                &serde_json::json!({
                    "session_id": "session-a",
                    "cwd": repo.path().to_string_lossy()
                }),
                &context,
            )
            .unwrap();

        assert_eq!(
            std::fs::read_to_string(&context.state_file).unwrap(),
            corrupt
        );
        let log = std::fs::read_to_string(state_dir.path().join("resource-context.log")).unwrap();
        assert!(log.contains("failed to read state; skipping capture"));
    }
}
