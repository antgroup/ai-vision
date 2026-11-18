use std::path::Path;
use std::process::Command;
use std::time::Duration;

pub const GIT_TIMEOUT: Duration = Duration::from_millis(1000);

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
enum RemoteKind {
    Fetch,
    Push,
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct RemoteCandidate {
    name: String,
    url: String,
    kind: RemoteKind,
}

/// Get the preferred git remote URL from one bounded snapshot of all remotes.
pub fn git_remote_url(cwd: &Path) -> Option<String> {
    let snapshot = run_git(cwd, &["remote", "-v"])?;
    select_remote_candidate(&snapshot).map(|candidate| candidate.url)
}

/// Get current git branch
pub fn git_branch(cwd: &Path) -> Option<String> {
    run_git(cwd, &["rev-parse", "--abbrev-ref", "HEAD"])
}

/// Run a git command with default timeout. Returns trimmed stdout on success (exit 0).
/// Suitable for single-value outputs (commit hashes, branch names, URLs).
pub fn run_git(cwd: &Path, args: &[&str]) -> Option<String> {
    run_cmd_with_timeout("git", args, cwd, false, GIT_TIMEOUT, true)
}

fn select_remote_candidate(snapshot: &str) -> Option<RemoteCandidate> {
    let mut candidates = parse_remote_candidates(snapshot);
    candidates.sort_by_key(remote_rank);
    candidates.into_iter().next()
}

fn parse_remote_candidates(snapshot: &str) -> Vec<RemoteCandidate> {
    snapshot.lines().filter_map(parse_remote_line).collect()
}

fn parse_remote_line(line: &str) -> Option<RemoteCandidate> {
    let mut fields = line.split_whitespace();
    let name = fields.next()?.trim();
    let url = fields.next()?.trim();
    let kind = match fields.next()? {
        "(fetch)" => RemoteKind::Fetch,
        "(push)" => RemoteKind::Push,
        _ => return None,
    };

    if name.is_empty() || url.is_empty() || fields.next().is_some() {
        return None;
    }

    Some(RemoteCandidate {
        name: name.to_string(),
        url: url.to_string(),
        kind,
    })
}

fn remote_rank(candidate: &RemoteCandidate) -> (u8, RemoteKind, String, String, String) {
    let name = candidate.name.to_lowercase();
    let url = candidate.url.to_lowercase();
    let origin_rank = u8::from(name != "origin");

    (
        origin_rank,
        candidate.kind,
        name,
        url,
        candidate.url.clone(),
    )
}

/// Spawn a command and wait up to `timeout` for it to complete.
/// On timeout the child is sent SIGKILL and reaped so this function never blocks longer
/// than `timeout` + the time it takes the OS to deliver the signal.
/// When `trim` is true, leading/trailing whitespace is stripped (for single-value
/// outputs like commit hashes). When false, stdout is returned verbatim so that
/// diff/patch content is never corrupted.
pub fn run_cmd_with_timeout(
    program: &str,
    args: &[&str],
    cwd: &Path,
    allow_exit_1: bool,
    timeout: Duration,
    trim: bool,
) -> Option<String> {
    use std::io::Read;
    use std::time::Instant;

    let mut child = Command::new(program)
        .args(args)
        .current_dir(cwd)
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::null())
        .spawn()
        .ok()?;

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
            Ok(Some(s)) => break Some(s),
            Ok(None) => {
                let now = Instant::now();
                if now >= deadline {
                    let _ = child.kill();
                    let _ = child.wait();
                    break None;
                }
                std::thread::sleep(poll.min(deadline.saturating_duration_since(now)));
            }
            Err(_) => {
                let _ = child.kill();
                let _ = child.wait();
                break None;
            }
        }
    };

    let stdout_buf = stdout_handle
        .and_then(|h| h.join().ok())
        .unwrap_or_default();

    let status = status?;
    let code = status.code().unwrap_or(-1);
    if code == 0 || (allow_exit_1 && code == 1) {
        let s = String::from_utf8_lossy(&stdout_buf).to_string();
        if trim {
            Some(s.trim().to_string())
        } else {
            Some(s)
        }
    } else {
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::process::Command;
    use std::time::Instant;

    fn git(cwd: &Path, args: &[&str]) {
        let status = Command::new("git")
            .args(args)
            .current_dir(cwd)
            .status()
            .unwrap();
        assert!(status.success(), "git {args:?} failed");
    }

    #[test]
    fn parses_remote_snapshot_and_ignores_malformed_lines() {
        let candidates = parse_remote_candidates(
            "origin\tgit@example.com:team/repo.git (fetch)\n\
             origin\tgit@example.com:team/repo.git (push)\n\
             malformed\n\
             upstream\tgit@example.com:team/upstream.git (unknown)\n",
        );

        assert_eq!(
            candidates,
            vec![
                RemoteCandidate {
                    name: "origin".to_string(),
                    url: "git@example.com:team/repo.git".to_string(),
                    kind: RemoteKind::Fetch,
                },
                RemoteCandidate {
                    name: "origin".to_string(),
                    url: "git@example.com:team/repo.git".to_string(),
                    kind: RemoteKind::Push,
                },
            ]
        );
    }

    #[test]
    fn selects_origin_independent_of_host_and_input_order() {
        let lines = [
            "public https://github.com/team/repo.git (fetch)",
            "ant git@git.example.com:team/repo.git (fetch)",
            "ali git@git.example.org:team/repo.git (fetch)",
            "corp git@git.example.com:team/repo.git (fetch)",
            "origin https://github.com/team/repo.git (fetch)",
        ];

        for snapshot in [
            lines.join("\n"),
            lines.iter().rev().copied().collect::<Vec<_>>().join("\n"),
            [lines[2], lines[0], lines[4], lines[1], lines[3]].join("\n"),
        ] {
            let selected = select_remote_candidate(&snapshot).unwrap();
            assert_eq!(selected.name, "origin");
            assert_eq!(selected.url, "https://github.com/team/repo.git");
        }
    }

    #[test]
    fn recognizes_origin_case_insensitively() {
        let snapshot = "MIRROR https://example.com/team/repo.git (fetch)\n\
                        ORIGIN https://git.example.com/team/repo.git (fetch)";

        let selected = select_remote_candidate(snapshot).unwrap();
        assert_eq!(selected.name, "ORIGIN");
    }

    #[test]
    fn selects_non_origin_remote_when_origin_is_absent() {
        let snapshot = "upstream git@example.com:team/repo.git (fetch)";

        let selected = select_remote_candidate(snapshot).unwrap();
        assert_eq!(selected.name, "upstream");
    }

    #[test]
    fn prefers_origin_then_fetch_then_lexical_fallback() {
        let origin_snapshot = "zeta git@example.com:zeta/repo.git (fetch)\n\
                               origin git@example.com:origin/repo.git (push)";
        assert_eq!(
            select_remote_candidate(origin_snapshot).unwrap().name,
            "origin"
        );

        let fetch_snapshot = "upstream git@example.com:team/push.git (push)\n\
                              upstream git@example.com:team/fetch.git (fetch)";
        assert_eq!(
            select_remote_candidate(fetch_snapshot).unwrap().url,
            "git@example.com:team/fetch.git"
        );

        let lexical_snapshot = "zeta git@example.com:zeta/repo.git (fetch)\n\
                                alpha git@example.com:alpha/repo.git (fetch)";
        assert_eq!(
            select_remote_candidate(lexical_snapshot).unwrap().name,
            "alpha"
        );
    }

    #[test]
    fn empty_remote_snapshot_returns_none() {
        assert!(select_remote_candidate("").is_none());
        assert!(select_remote_candidate("malformed").is_none());
    }

    #[test]
    fn discovers_repository_with_only_a_non_origin_remote() {
        let dir = tempfile::tempdir().unwrap();
        git(dir.path(), &["init", "-q"]);
        git(
            dir.path(),
            &["remote", "add", "upstream", "git@example.com:team/repo.git"],
        );

        assert_eq!(
            git_remote_url(dir.path()).as_deref(),
            Some("git@example.com:team/repo.git")
        );
    }

    #[test]
    fn real_git_snapshot_prefers_origin_regardless_of_creation_order() {
        for remotes in [
            [
                ("origin", "https://github.com/team/repo.git"),
                ("upstream", "https://git.example.com/team/repo.git"),
                ("corp", "git@git.example.com:team/repo.git"),
                ("mirror", "https://git.example.org/team/repo.git"),
            ],
            [
                ("mirror", "https://git.example.org/team/repo.git"),
                ("corp", "git@git.example.com:team/repo.git"),
                ("upstream", "https://git.example.com/team/repo.git"),
                ("origin", "https://github.com/team/repo.git"),
            ],
        ] {
            let dir = tempfile::tempdir().unwrap();
            git(dir.path(), &["init", "-q"]);
            for (name, url) in remotes {
                git(dir.path(), &["remote", "add", name, url]);
            }

            assert_eq!(
                git_remote_url(dir.path()).as_deref(),
                Some("https://github.com/team/repo.git")
            );
        }
    }

    #[test]
    fn returns_quickly_on_success() {
        let cwd = std::env::current_dir().unwrap();
        let timeout = Duration::from_secs(5);
        let started = Instant::now();
        let out = run_cmd_with_timeout("echo", &["hi"], &cwd, false, timeout, true);
        let elapsed = started.elapsed();
        assert_eq!(out.as_deref(), Some("hi"));
        assert!(
            elapsed < Duration::from_secs(4),
            "successful command should return before timeout, took {:?}",
            elapsed
        );
    }

    #[test]
    fn kills_child_on_timeout() {
        let cwd = std::env::current_dir().unwrap();
        let started = Instant::now();
        let out = run_cmd_with_timeout(
            "sleep",
            &["10"],
            &cwd,
            false,
            Duration::from_millis(200),
            true,
        );
        let elapsed = started.elapsed();
        assert!(out.is_none(), "timed-out command should return None");
        // Must return within timeout + reasonable slack; absolutely never wait for the full 10s.
        assert!(
            elapsed < Duration::from_secs(2),
            "expected bounded wait, took {:?}",
            elapsed
        );
    }
}
