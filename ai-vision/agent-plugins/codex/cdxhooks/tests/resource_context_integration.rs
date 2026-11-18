use cdxhooks::common::hook::HookEvent;
use cdxhooks::common::logger::Logger;
use cdxhooks::hooks::langfuse::state::{get_vcs_from_state, load_state};
use cdxhooks::hooks::resource_context::ResourceContextHandler;
use cdxhooks::hooks::{
    CommonContext, HookHandler, RealtimeExportMode, RetryPolicy,
    DEFAULT_REALTIME_LLM_EXPORT_BATCH_SIZE,
};
use serde_json::json;
use std::path::{Path, PathBuf};

fn run_test_git(path: &Path, args: &[&str]) -> bool {
    std::process::Command::new("git")
        .args(args)
        .current_dir(path)
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status()
        .is_ok_and(|status| status.success())
}

fn init_repository(path: &Path, remote_name: &str, remote_url: &str, branch: &str) {
    std::fs::create_dir_all(path).unwrap();
    assert!(run_test_git(path, &["init"]));
    assert!(run_test_git(path, &["config", "user.name", "Test"]));
    assert!(run_test_git(
        path,
        &["config", "user.email", "test@example.com"]
    ));
    assert!(run_test_git(path, &["checkout", "-b", branch]));
    assert!(run_test_git(
        path,
        &["commit", "--allow-empty", "-m", "init"]
    ));
    assert!(run_test_git(
        path,
        &["remote", "add", remote_name, remote_url]
    ));
}

fn context(state_file: PathBuf) -> CommonContext {
    CommonContext {
        logger: Logger::new(state_file.with_extension("log"), false),
        state_file,
        langfuse_config: None,
        username: "tester".to_string(),
        sessions_dir: String::new(),
        hook_version: "test".to_string(),
        stop_budget: None,
        retry_policy: RetryPolicy::AtLeastOnce,
        export_mode: RealtimeExportMode::RealtimeWithStopFinalizer,
        realtime_llm_batch_size: DEFAULT_REALTIME_LLM_EXPORT_BATCH_SIZE,
        metrics: None,
    }
}

fn submit(session_id: &str, cwd: &Path, state_file: PathBuf) {
    ResourceContextHandler::new()
        .handle(
            &HookEvent::UserPromptSubmit,
            &json!({"session_id": session_id, "cwd": cwd}),
            &context(state_file),
        )
        .unwrap();
}

#[test]
fn concurrent_prompt_submissions_keep_repository_state_session_local() {
    let dir = tempfile::tempdir().unwrap();
    let repo_a = dir.path().join("repo-a");
    let repo_b = dir.path().join("repo-b");
    init_repository(
        &repo_a,
        "upstream-a",
        "ssh://example/session-a.git",
        "branch-a",
    );
    init_repository(
        &repo_b,
        "upstream-b",
        "ssh://example/session-b.git",
        "branch-b",
    );

    let state_a = dir.path().join("state/sessions/session-a/state.json");
    let state_b = dir.path().join("state/sessions/session-b/state.json");
    let thread_a = std::thread::spawn({
        let repo_a = repo_a.clone();
        let state_a = state_a.clone();
        move || submit("session-a", &repo_a, state_a)
    });
    let thread_b = std::thread::spawn({
        let repo_b = repo_b.clone();
        let state_b = state_b.clone();
        move || submit("session-b", &repo_b, state_b)
    });
    thread_a.join().unwrap();
    thread_b.join().unwrap();

    let snapshot_a = load_state(&state_a);
    let snapshot_b = load_state(&state_b);
    assert_eq!(
        get_vcs_from_state(&snapshot_a, "session-a")
            .unwrap()
            .remote_url,
        "ssh://example/session-a.git"
    );
    assert_eq!(
        get_vcs_from_state(&snapshot_b, "session-b")
            .unwrap()
            .remote_url,
        "ssh://example/session-b.git"
    );
    assert!(snapshot_a.get("session-b").is_none());
    assert!(snapshot_b.get("session-a").is_none());
    assert_ne!(state_a, state_b);
}

#[test]
fn later_prompt_refreshes_branch_and_non_repository_clears_stale_snapshot() {
    let dir = tempfile::tempdir().unwrap();
    let repo = dir.path().join("repo");
    let non_repo = dir.path().join("not-a-repository");
    std::fs::create_dir_all(&non_repo).unwrap();
    init_repository(&repo, "upstream", "ssh://example/refresh.git", "branch-one");
    let state_file = dir.path().join("state/sessions/session/state.json");

    submit("session", &repo, state_file.clone());
    assert_eq!(
        get_vcs_from_state(&load_state(&state_file), "session")
            .unwrap()
            .branch,
        "branch-one"
    );

    assert!(run_test_git(&repo, &["checkout", "-b", "branch-two"]));
    submit("session", &repo, state_file.clone());
    assert_eq!(
        get_vcs_from_state(&load_state(&state_file), "session")
            .unwrap()
            .branch,
        "branch-two"
    );

    submit("session", &non_repo, state_file.clone());
    assert!(get_vcs_from_state(&load_state(&state_file), "session").is_none());
}
