use std::path::Path;
use std::process::Command;
use std::time::Duration;

pub const RESOURCE_CONTEXT_GIT_TIMEOUT: Duration = Duration::from_millis(250);

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum GitRemoteKind {
    Fetch,
    Push,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum GitRemoteMatch {
    Origin,
    Fallback,
}

impl GitRemoteMatch {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Origin => "origin",
            Self::Fallback => "fallback",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GitRemoteCandidate {
    pub name: String,
    pub url: String,
    pub kind: GitRemoteKind,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SelectedGitRemote {
    pub name: String,
    pub url: String,
    pub kind: GitRemoteKind,
    pub matched: GitRemoteMatch,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GitRepositorySnapshot {
    pub remote_name: String,
    pub remote_url: String,
    pub branch: String,
    pub selected_remote_match: Option<GitRemoteMatch>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum GitCollectionOutcome {
    Captured(GitRepositorySnapshot),
    NotRepositoryOrNoMetadata,
    Unavailable,
}

pub fn collect_git_repository(cwd: &Path, timeout: Duration) -> GitCollectionOutcome {
    let remotes = run_git_probe(cwd, &["remote", "-v"], timeout);
    let branch_probe = run_git_probe(cwd, &["rev-parse", "--abbrev-ref", "HEAD"], timeout);

    let selected_remote = match &remotes {
        GitProbeOutcome::Success(output) => select_git_remote(&parse_git_remote_verbose(output)),
        GitProbeOutcome::Absent | GitProbeOutcome::Unavailable => None,
    };
    let branch = match &branch_probe {
        GitProbeOutcome::Success(value) => {
            let value = value.trim();
            (!value.is_empty()).then(|| value.to_string())
        }
        GitProbeOutcome::Absent | GitProbeOutcome::Unavailable => None,
    };

    if selected_remote.is_some() || branch.is_some() {
        let selected_remote_match = selected_remote.as_ref().map(|remote| remote.matched);
        return GitCollectionOutcome::Captured(GitRepositorySnapshot {
            remote_name: selected_remote
                .as_ref()
                .map(|remote| remote.name.clone())
                .unwrap_or_default(),
            remote_url: selected_remote
                .as_ref()
                .map(|remote| remote.url.clone())
                .unwrap_or_default(),
            branch: branch.unwrap_or_default(),
            selected_remote_match,
        });
    }

    if matches!(remotes, GitProbeOutcome::Unavailable)
        || matches!(branch_probe, GitProbeOutcome::Unavailable)
    {
        GitCollectionOutcome::Unavailable
    } else {
        GitCollectionOutcome::NotRepositoryOrNoMetadata
    }
}

pub fn parse_git_remote_verbose(output: &str) -> Vec<GitRemoteCandidate> {
    output
        .lines()
        .filter_map(|line| {
            let mut fields = line.split_whitespace();
            let name = fields.next()?;
            let url = fields.next()?;
            let kind = match fields.next()? {
                "(fetch)" => GitRemoteKind::Fetch,
                "(push)" => GitRemoteKind::Push,
                _ => return None,
            };
            if name.is_empty() || url.is_empty() {
                return None;
            }
            Some(GitRemoteCandidate {
                name: name.to_string(),
                url: url.to_string(),
                kind,
            })
        })
        .collect()
}

pub fn select_git_remote(candidates: &[GitRemoteCandidate]) -> Option<SelectedGitRemote> {
    let mut candidates = candidates.to_vec();
    candidates.sort_by_key(remote_sort_key);
    candidates.first().map(|candidate| SelectedGitRemote {
        name: candidate.name.clone(),
        url: candidate.url.clone(),
        kind: candidate.kind,
        matched: remote_match(candidate),
    })
}

fn remote_match(candidate: &GitRemoteCandidate) -> GitRemoteMatch {
    if candidate.name.eq_ignore_ascii_case("origin") {
        GitRemoteMatch::Origin
    } else {
        GitRemoteMatch::Fallback
    }
}

fn remote_sort_key(candidate: &GitRemoteCandidate) -> (u8, u8, String, String, String, String) {
    let origin = u8::from(!candidate.name.eq_ignore_ascii_case("origin"));
    let kind = match candidate.kind {
        GitRemoteKind::Fetch => 0,
        GitRemoteKind::Push => 1,
    };
    (
        origin,
        kind,
        candidate.name.to_ascii_lowercase(),
        candidate.url.to_ascii_lowercase(),
        candidate.name.clone(),
        candidate.url.clone(),
    )
}

#[derive(Debug, Clone, PartialEq, Eq)]
enum GitProbeOutcome {
    Success(String),
    Absent,
    Unavailable,
}

fn run_git_probe(cwd: &Path, args: &[&str], timeout: Duration) -> GitProbeOutcome {
    match execute_command_with_timeout("git", args, cwd, timeout) {
        CommandExecution::Exited {
            code: Some(0),
            stdout,
        } => GitProbeOutcome::Success(stdout.trim().to_string()),
        CommandExecution::Exited { code: Some(_), .. } => GitProbeOutcome::Absent,
        CommandExecution::Exited { code: None, .. } | CommandExecution::Unavailable => {
            GitProbeOutcome::Unavailable
        }
    }
}

enum CommandExecution {
    Exited { code: Option<i32>, stdout: String },
    Unavailable,
}

fn execute_command_with_timeout(
    program: &str,
    args: &[&str],
    cwd: &Path,
    timeout: Duration,
) -> CommandExecution {
    use std::io::Read;
    use std::time::Instant;

    let Ok(mut child) = Command::new(program)
        .args(args)
        .current_dir(cwd)
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::null())
        .spawn()
    else {
        return CommandExecution::Unavailable;
    };

    // Drain stdout in a background thread so the child never blocks on a full pipe buffer
    // (~64 KB on macOS/Linux). The reader returns when the pipe closes — either naturally
    // after the child exits, or after we kill it below.
    let stdout_handle = child.stdout.take().map(|mut s| {
        std::thread::spawn(move || {
            let mut buf = Vec::new();
            let _ = s.read_to_end(&mut buf);
            buf
        })
    });

    let deadline = Instant::now() + timeout;
    let poll = Duration::from_millis(20);
    let status = loop {
        match child.try_wait() {
            Ok(Some(s)) => break s,
            Ok(None) => {
                let now = Instant::now();
                if now >= deadline {
                    let _ = child.kill();
                    let _ = child.wait();
                    return CommandExecution::Unavailable;
                }
                std::thread::sleep(poll.min(deadline.saturating_duration_since(now)));
            }
            Err(_) => {
                let _ = child.kill();
                let _ = child.wait();
                return CommandExecution::Unavailable;
            }
        }
    };

    let stdout_buf = stdout_handle
        .and_then(|h| h.join().ok())
        .unwrap_or_default();

    CommandExecution::Exited {
        code: status.code(),
        stdout: String::from_utf8_lossy(&stdout_buf).to_string(),
    }
}

#[cfg(test)]
pub(crate) fn run_test_git(cwd: &Path, args: &[&str]) -> bool {
    Command::new("git")
        .args(args)
        .current_dir(cwd)
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status()
        .is_ok_and(|status| status.success())
}

#[cfg(test)]
mod tests {
    use super::*;

    const TEST_GIT_TIMEOUT: Duration = Duration::from_secs(5);

    fn candidate(name: &str, url: &str, kind: GitRemoteKind) -> GitRemoteCandidate {
        GitRemoteCandidate {
            name: name.to_string(),
            url: url.to_string(),
            kind,
        }
    }

    #[test]
    fn parses_verbose_remote_rows_and_ignores_malformed_input() {
        let rows = parse_git_remote_verbose(
            "origin git@example.com:repo.git (fetch)\n\
             origin git@example.com:repo.git (push)\n\
             malformed\n\
             upstream https://example.com/repo.git (fetch)\n",
        );
        assert_eq!(
            rows,
            vec![
                candidate("origin", "git@example.com:repo.git", GitRemoteKind::Fetch),
                candidate("origin", "git@example.com:repo.git", GitRemoteKind::Push),
                candidate(
                    "upstream",
                    "https://example.com/repo.git",
                    GitRemoteKind::Fetch
                ),
            ]
        );
    }

    #[test]
    fn origin_outranks_other_hosts_and_remote_names() {
        let candidates = vec![
            candidate(
                "origin",
                "https://example.com/repo.git",
                GitRemoteKind::Fetch,
            ),
            candidate(
                "Mirror",
                "https://example.com/mirror/repo.git",
                GitRemoteKind::Fetch,
            ),
            candidate(
                "corp",
                "ssh://git@git.example/repo.git",
                GitRemoteKind::Fetch,
            ),
            candidate(
                "mirror-main",
                "ssh://git@example.com/repo.git",
                GitRemoteKind::Fetch,
            ),
        ];
        let selected = select_git_remote(&candidates).unwrap();
        assert_eq!(selected.name, "origin");
        assert_eq!(selected.matched, GitRemoteMatch::Origin);
    }

    #[test]
    fn origin_then_fetch_then_lexical_order_are_stable_fallbacks() {
        let candidates = vec![
            candidate("zeta", "ssh://zeta/push", GitRemoteKind::Push),
            candidate("beta", "ssh://beta/fetch", GitRemoteKind::Fetch),
            candidate("origin", "ssh://origin/push", GitRemoteKind::Push),
            candidate("origin", "ssh://origin/fetch", GitRemoteKind::Fetch),
        ];
        let selected = select_git_remote(&candidates).unwrap();
        assert_eq!(selected.url, "ssh://origin/fetch");
        assert_eq!(selected.kind, GitRemoteKind::Fetch);
        assert_eq!(selected.matched, GitRemoteMatch::Origin);
    }

    #[test]
    fn selection_does_not_depend_on_input_order() {
        let first = vec![
            candidate("zeta", "ssh://zeta/repo", GitRemoteKind::Fetch),
            candidate("alpha", "ssh://alpha/repo", GitRemoteKind::Fetch),
        ];
        let mut reversed = first.clone();
        reversed.reverse();
        assert_eq!(select_git_remote(&first), select_git_remote(&reversed));
        assert_eq!(select_git_remote(&first).unwrap().name, "alpha");
    }

    #[test]
    fn collection_reports_definitive_absence_for_non_repository() {
        let dir = tempfile::tempdir().unwrap();
        assert_eq!(
            collect_git_repository(dir.path(), TEST_GIT_TIMEOUT),
            GitCollectionOutcome::NotRepositoryOrNoMetadata
        );
    }

    #[test]
    fn collection_supports_repository_without_origin() {
        let dir = tempfile::tempdir().unwrap();
        assert!(run_test_git(dir.path(), &["init"]));
        assert!(run_test_git(dir.path(), &["config", "user.name", "Test"]));
        assert!(run_test_git(
            dir.path(),
            &["config", "user.email", "test@example.com"]
        ));
        assert!(run_test_git(dir.path(), &["checkout", "-b", "feature"]));
        assert!(run_test_git(
            dir.path(),
            &["commit", "--allow-empty", "-m", "init"]
        ));
        assert!(run_test_git(
            dir.path(),
            &["remote", "add", "upstream", "ssh://example/upstream.git"]
        ));

        let GitCollectionOutcome::Captured(snapshot) =
            collect_git_repository(dir.path(), TEST_GIT_TIMEOUT)
        else {
            panic!("expected captured Git repository");
        };
        assert_eq!(snapshot.remote_name, "upstream");
        assert_eq!(snapshot.remote_url, "ssh://example/upstream.git");
        assert_eq!(snapshot.branch, "feature");
        assert_eq!(
            snapshot.selected_remote_match,
            Some(GitRemoteMatch::Fallback)
        );
    }
}
