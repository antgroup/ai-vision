//! Diff computation and AI acceptance rate calculation.
//!
//! Uses git worktrees to compute unified diffs between pre/post states,
//! and calculates acceptance rates via multiset line matching.

use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

/// Outcome of a diff computation.
pub struct ComputeDiffOutcome {
    pub diff: Option<String>,
    pub error: Option<String>,
}

/// Result of AI diff with restoration of rejected tools.
pub struct DiffResult {
    pub ai_diff: Option<String>,
    pub ai_diff_error: Option<String>,
    pub restored_post_state: Option<String>,
}

/// Acceptance rate calculation result.
pub struct AcceptanceResult {
    pub acceptance_rate: Option<f64>,
    pub total_ai_lines: usize,
    pub rejected_lines: usize,
}

/// A tool invocation that was rejected by the user.
pub struct RejectedTool {
    pub tool: String, // "Write" or "Edit"
    pub file_path: String,
    pub input: serde_json::Value,
}

/// Compute diff between before and after patches using git worktree.
pub fn compute_diff(
    before: Option<&str>,
    after: Option<&str>,
    cwd: &Path,
    base_commit: &str,
) -> ComputeDiffOutcome {
    let (before, after) = match (before, after) {
        (Some(b), Some(a)) => (b, a),
        _ => {
            return ComputeDiffOutcome {
                diff: None,
                error: None,
            }
        }
    };
    if before == after {
        return ComputeDiffOutcome {
            diff: Some(String::new()),
            error: None,
        };
    }

    let temp_dir = match tempfile::TempDir::new() {
        Ok(d) => d,
        Err(e) => {
            return ComputeDiffOutcome {
                diff: None,
                error: Some(format!("TempDir::new failed: {}", e)),
            }
        }
    };
    let worktree_dir = temp_dir.path();

    let output = Command::new("git")
        .args(["worktree", "add", "--detach"])
        .arg(worktree_dir)
        .arg(base_commit)
        .current_dir(cwd)
        .output();

    match &output {
        Ok(o) if o.status.success() => {}
        Ok(o) => {
            return ComputeDiffOutcome {
                diff: None,
                error: Some(format!(
                    "git worktree add failed: {}",
                    String::from_utf8_lossy(&o.stderr)
                )),
            }
        }
        Err(e) => {
            return ComputeDiffOutcome {
                diff: None,
                error: Some(format!("git worktree add spawn failed: {}", e)),
            }
        }
    }

    let mut errors: Vec<String> = Vec::new();

    let do_compute = |errors: &mut Vec<String>| -> Option<String> {
        let pre_ok = apply_patch(worktree_dir, before);
        if !pre_ok {
            errors.push("git apply failed for before_state".to_string());
        }
        let pre_hash = commit_changes(worktree_dir, "pre")?;

        Command::new("git")
            .args(["reset", "--hard", base_commit])
            .current_dir(worktree_dir)
            .output()
            .ok()?;
        Command::new("git")
            .args(["clean", "-fd"])
            .current_dir(worktree_dir)
            .output()
            .ok()?;

        let post_ok = apply_patch(worktree_dir, after);
        if !post_ok {
            errors.push("git apply failed for after_state".to_string());
        }
        let post_hash = commit_changes(worktree_dir, "post")?;

        Command::new("git")
            .args(["diff", &pre_hash, &post_hash])
            .current_dir(worktree_dir)
            .output()
            .ok()
            .filter(|o| o.status.success())
            .map(|o| String::from_utf8_lossy(&o.stdout).to_string())
    };

    let result = do_compute(&mut errors);
    remove_worktree(cwd, worktree_dir);

    ComputeDiffOutcome {
        diff: result,
        error: if errors.is_empty() {
            None
        } else {
            Some(errors.join("; "))
        },
    }
}

/// Compute AI diff with restoration of rejected tools.
pub fn compute_ai_diff_with_restore(
    pre_state: Option<&str>,
    approx_post: Option<&str>,
    rejected_tools: &[RejectedTool],
    cwd: &Path,
    base_commit: &str,
) -> DiffResult {
    if pre_state.is_none() || approx_post.is_none() {
        return DiffResult {
            ai_diff: None,
            ai_diff_error: None,
            restored_post_state: None,
        };
    }

    let pre_state = pre_state.unwrap();
    let approx_post = approx_post.unwrap();

    let temp_dir = match tempfile::TempDir::new() {
        Ok(dir) => dir,
        Err(e) => {
            return DiffResult {
                ai_diff: None,
                ai_diff_error: Some(format!("TempDir::new failed: {}", e)),
                restored_post_state: None,
            }
        }
    };
    let worktree_dir = temp_dir.path();

    let output = Command::new("git")
        .args(["worktree", "add", "--detach"])
        .arg(worktree_dir)
        .arg(base_commit)
        .current_dir(cwd)
        .output();
    match &output {
        Ok(o) if o.status.success() => {}
        _ => {
            return DiffResult {
                ai_diff: None,
                ai_diff_error: Some("git worktree add failed".to_string()),
                restored_post_state: None,
            }
        }
    }

    let mut errors: Vec<String> = Vec::new();

    let do_compute = |errors: &mut Vec<String>| -> Option<(String, String)> {
        let pre_ok = apply_patch(worktree_dir, pre_state);
        if !pre_ok {
            errors.push("git apply failed for pre_state".to_string());
        }
        let pre_hash = commit_changes(worktree_dir, "pre")?;

        Command::new("git")
            .args(["reset", "--hard", base_commit])
            .current_dir(worktree_dir)
            .output()
            .ok()?;
        Command::new("git")
            .args(["clean", "-fd"])
            .current_dir(worktree_dir)
            .output()
            .ok()?;

        let post_ok = apply_patch(worktree_dir, approx_post);
        if !post_ok {
            errors.push("git apply failed for approx_post".to_string());
        }

        for tool in rejected_tools {
            apply_rejected_tool(worktree_dir, cwd, tool);
        }

        let restored_hash = commit_changes(worktree_dir, "restored_post")?;

        let ai_diff = Command::new("git")
            .args(["diff", &pre_hash, &restored_hash])
            .current_dir(worktree_dir)
            .output()
            .ok()
            .filter(|o| o.status.success())
            .map(|o| String::from_utf8_lossy(&o.stdout).to_string())?;

        let restored_post_state = Command::new("git")
            .args(["diff", base_commit, &restored_hash])
            .current_dir(worktree_dir)
            .output()
            .ok()
            .filter(|o| o.status.success())
            .map(|o| String::from_utf8_lossy(&o.stdout).to_string())?;

        Some((ai_diff, restored_post_state))
    };

    let result = match do_compute(&mut errors) {
        Some((ai_diff, restored_post_state)) => DiffResult {
            ai_diff: Some(ai_diff),
            ai_diff_error: if errors.is_empty() {
                None
            } else {
                Some(errors.join("; "))
            },
            restored_post_state: Some(restored_post_state),
        },
        None => DiffResult {
            ai_diff: None,
            ai_diff_error: if errors.is_empty() {
                Some("compute failed".to_string())
            } else {
                Some(errors.join("; "))
            },
            restored_post_state: None,
        },
    };

    remove_worktree(cwd, worktree_dir);
    result
}

/// Calculate acceptance rate by comparing AI diff with human diff.
///
/// Uses multiset matching: human removed ∩ AI added → rejected additions,
/// human added ∩ AI removed → rejected deletions.
pub fn calculate_acceptance_rate(
    ai_diff: Option<&str>,
    human_diff: Option<&str>,
) -> AcceptanceResult {
    let ai_diff = match ai_diff {
        Some(d) => d,
        None => {
            return AcceptanceResult {
                acceptance_rate: None,
                total_ai_lines: 0,
                rejected_lines: 0,
            }
        }
    };

    let (ai_added, ai_removed) = extract_diff_lines(ai_diff);
    let total_ai_lines = ai_added.len() + ai_removed.len();

    if total_ai_lines == 0 {
        return AcceptanceResult {
            acceptance_rate: None,
            total_ai_lines: 0,
            rejected_lines: 0,
        };
    }

    let human_diff = match human_diff {
        Some(d) => d,
        None => {
            return AcceptanceResult {
                acceptance_rate: Some(1.0),
                total_ai_lines,
                rejected_lines: 0,
            }
        }
    };

    let (human_added, human_removed) = extract_diff_lines(human_diff);
    let mut rejected = 0usize;

    let mut ai_added_bag: HashMap<&str, usize> = HashMap::new();
    for line in &ai_added {
        *ai_added_bag.entry(line.as_str()).or_insert(0) += 1;
    }
    for line in &human_removed {
        if let Some(count) = ai_added_bag.get_mut(line.as_str()) {
            if *count > 0 {
                rejected += 1;
                *count -= 1;
            }
        }
    }

    let mut ai_removed_bag: HashMap<&str, usize> = HashMap::new();
    for line in &ai_removed {
        *ai_removed_bag.entry(line.as_str()).or_insert(0) += 1;
    }
    for line in &human_added {
        if let Some(count) = ai_removed_bag.get_mut(line.as_str()) {
            if *count > 0 {
                rejected += 1;
                *count -= 1;
            }
        }
    }

    let rate = 1.0 - (rejected as f64 / total_ai_lines as f64);
    let rounded_rate = (rate * 1000.0).round() / 1000.0;

    AcceptanceResult {
        acceptance_rate: Some(rounded_rate),
        total_ai_lines,
        rejected_lines: rejected,
    }
}

/// Extract added and removed lines from a unified diff.
pub fn extract_diff_lines(diff: &str) -> (Vec<String>, Vec<String>) {
    let mut added_lines = Vec::new();
    let mut removed_lines = Vec::new();

    for line in diff.lines() {
        let line = line.trim();

        if line.starts_with("diff ")
            || line.starts_with("index ")
            || line.starts_with("--- ")
            || line.starts_with("+++ ")
            || line.starts_with("@@")
            || line.starts_with("new file")
            || line.starts_with("new mode")
            || line.starts_with("old mode")
            || line.starts_with("deleted file")
            || line.starts_with("similarity")
            || line.starts_with("rename")
            || line.starts_with("Binary")
            || line.is_empty()
        {
            continue;
        }

        if line.starts_with('+') && !line.starts_with("++") {
            added_lines.push(line[1..].to_string());
        } else if line.starts_with('-') && !line.starts_with("--") {
            removed_lines.push(line[1..].to_string());
        }
    }

    (added_lines, removed_lines)
}

// ── Internal helpers ───────────────────────────────────────────────

fn apply_patch(worktree_dir: &Path, patch: &str) -> bool {
    let patch_file = worktree_dir.join("temp.patch");
    if fs::write(&patch_file, patch).is_err() {
        return false;
    }

    let ok = Command::new("git")
        .args(["apply", "--reject"])
        .arg(&patch_file)
        .current_dir(worktree_dir)
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false);

    let _ = fs::remove_file(&patch_file);
    cleanup_rej_files(worktree_dir);
    ok
}

fn cleanup_rej_files(dir: &Path) {
    let Ok(entries) = fs::read_dir(dir) else {
        return;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_dir() {
            cleanup_rej_files(&path);
        } else if path.extension().map_or(false, |ext| ext == "rej") {
            let _ = fs::remove_file(&path);
        }
    }
}

fn commit_changes(worktree_dir: &Path, message: &str) -> Option<String> {
    let stage_output = Command::new("git")
        .args(["add", "-A"])
        .current_dir(worktree_dir)
        .output()
        .ok()?;

    if !stage_output.status.success() {
        return None;
    }

    let commit_output = Command::new("git")
        .args([
            "-c",
            "user.email=x@x.x",
            "-c",
            "user.name=x",
            "commit",
            "--allow-empty",
            "-m",
            message,
        ])
        .current_dir(worktree_dir)
        .output()
        .ok()?;

    if !commit_output.status.success() {
        return None;
    }

    let rev_parse_output = Command::new("git")
        .args(["rev-parse", "HEAD"])
        .current_dir(worktree_dir)
        .output()
        .ok()?;

    if rev_parse_output.status.success() {
        Some(
            String::from_utf8_lossy(&rev_parse_output.stdout)
                .trim()
                .to_string(),
        )
    } else {
        None
    }
}

fn resolve_tool_path(worktree_dir: &Path, cwd: &Path, file_path: &str) -> PathBuf {
    let rel = if Path::new(file_path).is_absolute() {
        Path::new(file_path)
            .strip_prefix(cwd)
            .unwrap_or(Path::new(file_path))
            .to_path_buf()
    } else {
        PathBuf::from(file_path)
    };
    worktree_dir.join(rel)
}

fn apply_rejected_tool(worktree_dir: &Path, cwd: &Path, tool: &RejectedTool) {
    let fp_str = tool
        .input
        .get("file_path")
        .or_else(|| tool.input.get("path"))
        .and_then(|v| v.as_str())
        .unwrap_or(&tool.file_path);
    let abs_path = resolve_tool_path(worktree_dir, cwd, fp_str);

    match tool.tool.as_str() {
        "Write" => {
            if let Some(content) = tool.input.get("content").and_then(|v| v.as_str()) {
                if let Some(parent) = abs_path.parent() {
                    let _ = fs::create_dir_all(parent);
                }
                let _ = fs::write(&abs_path, content);
            }
        }
        "Edit" => {
            if let (Some(old_string), Some(new_string)) = (
                tool.input.get("old_string").and_then(|v| v.as_str()),
                tool.input.get("new_string").and_then(|v| v.as_str()),
            ) {
                if let Ok(content) = fs::read_to_string(&abs_path) {
                    let new_content = content.replace(old_string, new_string);
                    let _ = fs::write(&abs_path, new_content);
                }
            }
        }
        _ => {}
    }
}

fn remove_worktree(cwd: &Path, worktree_dir: &Path) {
    let result = Command::new("git")
        .args(["worktree", "remove", "--force"])
        .arg(worktree_dir)
        .current_dir(cwd)
        .output();

    if result.is_err() || !result.unwrap().status.success() {
        let _ = fs::remove_dir_all(worktree_dir);
        let _ = Command::new("git")
            .args(["worktree", "prune"])
            .current_dir(cwd)
            .output();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_extract_diff_lines() {
        let diff = "diff --git a/foo b/foo\nindex abc..def\n--- a/foo\n+++ b/foo\n@@ -1 +1 @@\n-old\n+new\n";
        let (added, removed) = extract_diff_lines(diff);
        assert_eq!(added, vec!["new"]);
        assert_eq!(removed, vec!["old"]);
    }

    #[test]
    fn test_extract_diff_lines_empty() {
        let (added, removed) = extract_diff_lines("");
        assert!(added.is_empty());
        assert!(removed.is_empty());
    }

    #[test]
    fn test_acceptance_rate_full() {
        let result = calculate_acceptance_rate(Some("+new\n-old\n"), None);
        assert_eq!(result.acceptance_rate, Some(1.0));
    }

    #[test]
    fn test_acceptance_rate_none() {
        let result = calculate_acceptance_rate(None, None);
        assert!(result.acceptance_rate.is_none());
    }
}
