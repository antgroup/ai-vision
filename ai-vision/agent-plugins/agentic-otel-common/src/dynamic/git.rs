//! Git state capture: base commit resolution, tracked/untracked diff collection.
//!
//! Agent-agnostic utilities for capturing git working tree state as unified diffs.
//! Used by dynamic data collection to record pre/post state of each turn.

use std::fs;
use std::path::Path;
use std::time::Duration;

use crate::hook::run_cmd_with_timeout;

const MAX_STATE_SIZE: usize = 512 * 1024; // 512KB
const MAX_UNTRACKED_FILE_SIZE: u64 = 50 * 1024; // 50KB
const MAX_UNTRACKED_TOTAL: u64 = 200 * 1024; // 200KB
const GIT_DIFF_TIMEOUT: Duration = Duration::from_secs(10);

/// Result of capturing git state.
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

/// Get the base commit for diffing.
///
/// Tries multiple strategies in order of preference:
/// 1. `git rev-parse @{u}` (upstream branch)
/// 2. `git merge-base HEAD origin/HEAD`
/// 3. `git merge-base HEAD origin/main`
/// 4. `git merge-base HEAD origin/master`
///
/// Returns None if all strategies fail.
pub fn get_base_commit(cwd: &Path) -> Option<String> {
    if let Some(commit) = run_git(cwd, &["rev-parse", "@{u}"]) {
        return Some(commit);
    }
    if let Some(commit) = run_git(cwd, &["merge-base", "HEAD", "origin/HEAD"]) {
        return Some(commit);
    }
    if let Some(commit) = run_git(cwd, &["merge-base", "HEAD", "origin/main"]) {
        return Some(commit);
    }
    if let Some(commit) = run_git(cwd, &["merge-base", "HEAD", "origin/master"]) {
        return Some(commit);
    }
    None
}

/// Capture git state including tracked and untracked changes.
///
/// - Tracked: `git diff base_commit`
/// - Untracked: per-file `git diff --no-index /dev/null <file>` (with size limits)
/// - Sections sorted by file path for deterministic output
pub fn capture_state(cwd: &Path, base_commit: &str) -> CaptureResult {
    let mut state = String::new();

    // Get tracked diff
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

        for file in untracked_files {
            let file_path = cwd.join(file);

            if let Ok(metadata) = fs::metadata(&file_path) {
                let file_size = metadata.len();

                if file_size > MAX_UNTRACKED_FILE_SIZE {
                    continue;
                }
                if untracked_total + file_size > MAX_UNTRACKED_TOTAL {
                    break;
                }
                if !is_text_file(&file_path) {
                    continue;
                }

                if let Some(diff) = run_git_diff(cwd, &["diff", "--no-index", "/dev/null", file]) {
                    if !diff.is_empty() {
                        state.push_str(&diff);
                    }
                    untracked_total += file_size;
                }
            }
        }
    }

    // Sort diff sections for determinism
    let state = sort_diff_sections(&state);

    if state.len() > MAX_STATE_SIZE {
        return CaptureResult::skipped(format!(
            "State size {} exceeds limit {}",
            state.len(),
            MAX_STATE_SIZE
        ));
    }

    CaptureResult::success(state)
}

/// Sort unified diff sections by file path for deterministic output.
pub fn sort_diff_sections(state: &str) -> String {
    let mut sections: Vec<&str> = Vec::new();
    let mut last = 0;

    for (i, _) in state.match_indices("diff --git ") {
        if i > last {
            sections.push(&state[last..i]);
        }
        last = i;
    }
    if last < state.len() {
        sections.push(&state[last..]);
    }

    if sections.len() <= 1 {
        return state.to_string();
    }

    sections.sort();

    let mut result = sections.join("");
    if state.ends_with('\n') && !result.ends_with('\n') {
        result.push('\n');
    }
    result
}

/// Run a git command with default timeout. Returns trimmed stdout on success (exit 0).
pub fn run_git(cwd: &Path, args: &[&str]) -> Option<String> {
    run_cmd_with_timeout(
        "git",
        args,
        cwd,
        false,
        Duration::from_secs(5),
        true, // trim
    )
}

/// Run a git command with extended timeout, returning raw (untrimmed) stdout.
pub fn run_git_raw(cwd: &Path, args: &[&str]) -> Option<String> {
    run_cmd_with_timeout("git", args, cwd, false, GIT_DIFF_TIMEOUT, false)
}

/// Like run_git_raw but also accepts exit code 1 (for `git diff --no-index`).
pub fn run_git_diff(cwd: &Path, args: &[&str]) -> Option<String> {
    run_cmd_with_timeout("git", args, cwd, true, GIT_DIFF_TIMEOUT, false)
}

/// Check if a file is a text file using `file --mime-type`.
pub fn is_text_file(path: &Path) -> bool {
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_sort_diff_sections_empty() {
        assert_eq!(sort_diff_sections(""), "");
    }

    #[test]
    fn test_sort_diff_sections_single() {
        let s = "diff --git a/foo b/foo\n+line\n";
        assert_eq!(sort_diff_sections(s), s);
    }

    #[test]
    fn test_sort_diff_sections_multiple() {
        let s = "diff --git a/z b/z\n+z\ndiff --git a/a b/a\n+a\n";
        let sorted = sort_diff_sections(s);
        assert!(sorted.starts_with("diff --git a/a b/a"));
    }

    #[test]
    fn test_capture_result_constructors() {
        let ok = CaptureResult::success("diff".to_string());
        assert!(ok.state.is_some());
        assert!(ok.skip_reason.is_none());

        let skip = CaptureResult::skipped("too big".to_string());
        assert!(skip.state.is_none());
        assert_eq!(skip.skip_reason.unwrap(), "too big");
    }
}
