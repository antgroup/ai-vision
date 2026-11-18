use std::fs;
use std::path::Path;
use std::time::Duration;

// Re-export shared git utilities
pub use crate::common::git_state::{git_branch, git_remote_url, run_cmd_with_timeout};

// Default size limits. Can be overridden at runtime via env vars:
//   CCHOOKS_DYNAMIC_MAX_STATE_SIZE          — total pre/post state cap (bytes)
//   CCHOOKS_DYNAMIC_MAX_UNTRACKED_FILE_SIZE — per-untracked-file cap (bytes)
//   CCHOOKS_DYNAMIC_MAX_UNTRACKED_TOTAL     — aggregate untracked cap (bytes)
const DEFAULT_MAX_STATE_SIZE: usize = 1024 * 1024; // 1MB
const DEFAULT_MAX_UNTRACKED_FILE_SIZE: u64 = 100 * 1024; // 100KB
const DEFAULT_MAX_UNTRACKED_TOTAL: u64 = 400 * 1024; // 400KB
const GIT_DIFF_TIMEOUT: Duration = Duration::from_secs(10);

fn env_size<T: std::str::FromStr>(key: &str, default: T) -> T {
    crate::common::env::var(key)
        .ok()
        .and_then(|v| v.trim().parse::<T>().ok())
        .unwrap_or(default)
}

fn max_state_size() -> usize {
    env_size("CCHOOKS_DYNAMIC_MAX_STATE_SIZE", DEFAULT_MAX_STATE_SIZE)
}

fn max_untracked_file_size() -> u64 {
    env_size(
        "CCHOOKS_DYNAMIC_MAX_UNTRACKED_FILE_SIZE",
        DEFAULT_MAX_UNTRACKED_FILE_SIZE,
    )
}

fn max_untracked_total() -> u64 {
    env_size(
        "CCHOOKS_DYNAMIC_MAX_UNTRACKED_TOTAL",
        DEFAULT_MAX_UNTRACKED_TOTAL,
    )
}

/// Result of capturing git state
#[derive(Debug, Clone)]
pub struct CaptureResult {
    pub state: Option<String>,
    pub skip_reason: Option<String>,
}

impl CaptureResult {
    pub fn success(state: String) -> Self {
        Self {
            state: Some(state),
            skip_reason: None,
        }
    }

    pub fn skipped(reason: String) -> Self {
        Self {
            state: None,
            skip_reason: Some(reason),
        }
    }
}

/// Get the base commit for diffing
/// Tries multiple strategies in order of preference:
/// 1. git rev-parse @{u} (upstream branch)
/// 2. git merge-base HEAD origin/HEAD
/// 3. git merge-base HEAD origin/main
/// 4. git merge-base HEAD origin/master
/// Returns None if all strategies fail
pub fn get_base_commit(cwd: &Path) -> Option<String> {
    // Try git rev-parse @{u}
    if let Some(commit) = run_git(cwd, &["rev-parse", "@{u}"]) {
        return Some(commit);
    }

    // Try git merge-base HEAD origin/HEAD
    if let Some(commit) = run_git(cwd, &["merge-base", "HEAD", "origin/HEAD"]) {
        return Some(commit);
    }

    // Try git merge-base HEAD origin/main
    if let Some(commit) = run_git(cwd, &["merge-base", "HEAD", "origin/main"]) {
        return Some(commit);
    }

    // Try git merge-base HEAD origin/master
    if let Some(commit) = run_git(cwd, &["merge-base", "HEAD", "origin/master"]) {
        return Some(commit);
    }

    None
}

/// Capture git state including tracked and untracked changes
pub fn capture_state(cwd: &Path, base_commit: &str) -> CaptureResult {
    let mut state = String::new();

    // Get tracked diff (may be slow on large repos)
    if let Some(tracked_diff) = run_git_raw(cwd, &["diff", base_commit]) {
        if !tracked_diff.is_empty() {
            state.push_str(&tracked_diff);
        }
    }

    // Get untracked files
    if let Some(untracked_output) =
        run_git_raw(cwd, &["ls-files", "--others", "--exclude-standard"])
    {
        let untracked_files: Vec<&str> = untracked_output.lines().collect();
        let mut untracked_total: u64 = 0;
        let untracked_file_cap = max_untracked_file_size();
        let untracked_total_cap = max_untracked_total();

        for file in untracked_files {
            let file_path = cwd.join(file);

            // Check file size
            if let Ok(metadata) = fs::metadata(&file_path) {
                let file_size = metadata.len();

                // Skip if single file exceeds max size
                if file_size > untracked_file_cap {
                    continue;
                }

                // Skip if total exceeds max
                if untracked_total + file_size > untracked_total_cap {
                    break;
                }

                // Check if it's a text file
                if !is_text_file(&file_path) {
                    continue;
                }

                // Generate unified diff for untracked file
                if let Some(diff) = run_git_diff(cwd, &["diff", "--no-index", "/dev/null", file]) {
                    if !diff.is_empty() {
                        state.push_str(&diff);
                    }
                    untracked_total += file_size;
                }
            }
        }
    }

    // Sort diff sections by file path so the output is deterministic
    // regardless of whether files are tracked or untracked.
    // Without sorting, a file moving from untracked → tracked (e.g. after
    // git commit) changes the section order, causing a false pre/post diff.
    let state = sort_diff_sections(&state);

    // Check total size
    let state_cap = max_state_size();
    if state.len() > state_cap {
        return CaptureResult::skipped(format!(
            "State size {} exceeds limit {}",
            state.len(),
            state_cap
        ));
    }

    CaptureResult::success(state)
}

/// Run a git command with default timeout. Returns trimmed stdout on success (exit 0).
fn run_git(cwd: &Path, args: &[&str]) -> Option<String> {
    crate::common::git_state::run_git(cwd, args)
}

/// Run a git command with extended timeout, returning raw (untrimmed) stdout.
/// Suitable for diff / ls-files output where content must be preserved verbatim.
fn run_git_raw(cwd: &Path, args: &[&str]) -> Option<String> {
    run_cmd_with_timeout("git", args, cwd, false, GIT_DIFF_TIMEOUT, false)
}

/// Like run_git_raw but also accepts exit code 1 (for `git diff --no-index`).
fn run_git_diff(cwd: &Path, args: &[&str]) -> Option<String> {
    run_cmd_with_timeout("git", args, cwd, true, GIT_DIFF_TIMEOUT, false)
}

/// Sort unified diff sections by file path so that the same set of changes
/// always produces the same string, regardless of tracked/untracked ordering.
fn sort_diff_sections(state: &str) -> String {
    let mut sections: Vec<&str> = Vec::new();
    let mut last = 0;

    // Split on "diff --git " boundaries (keeping each section intact)
    for (i, _) in state.match_indices("diff --git ") {
        if i > last {
            sections.push(&state[last..i]);
        }
        last = i;
    }
    if last < state.len() {
        sections.push(&state[last..]);
    }

    // Nothing to sort if 0 or 1 section
    if sections.len() <= 1 {
        return state.to_string();
    }

    sections.sort();

    let mut result = sections.join("");
    // Preserve trailing newline if the original had one
    if state.ends_with('\n') && !result.ends_with('\n') {
        result.push('\n');
    }
    result
}

/// Check if a file is a text file using file --mime-type
fn is_text_file(path: &Path) -> bool {
    let path_str = match path.to_str() {
        Some(s) => s,
        None => return false,
    };
    let output = std::process::Command::new("file")
        .args(["--mime-type", "-b", path_str])
        .output()
        .ok();

    if let Some(output) = output {
        if output.status.success() {
            let mime_type = String::from_utf8_lossy(&output.stdout).trim().to_string();
            return mime_type.starts_with("text/") || mime_type == "application/json";
        }
    }

    false
}
