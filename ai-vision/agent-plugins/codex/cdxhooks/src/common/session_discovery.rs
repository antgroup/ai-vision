use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};

use crate::common::transcript::{extract_session_meta_ids_from_path, SessionMetaIds};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DiscoveredSessionTranscript {
    pub identity_id: String,
    pub session_id: String,
    pub path: PathBuf,
}

#[derive(Debug, Default)]
pub struct SessionDiscoveryResult {
    pub matches: Vec<DiscoveredSessionTranscript>,
    pub directories_visited: usize,
    pub metadata_files_read: usize,
    pub exact_filename_matches: usize,
}

pub fn session_search_roots(preferred_dir: &Path, sessions_root: Option<&Path>) -> Vec<PathBuf> {
    let preferred_canonical = canonical_or_owned(preferred_dir);
    let Some(root) = sessions_root.filter(|root| root.exists()) else {
        return vec![preferred_dir.to_path_buf()];
    };
    let root_canonical = canonical_or_owned(root);
    if preferred_canonical.starts_with(&root_canonical) || preferred_canonical == root_canonical {
        vec![root.to_path_buf()]
    } else {
        vec![preferred_dir.to_path_buf(), root.to_path_buf()]
    }
}

pub fn discover_child_session_transcripts(
    roots: &[PathBuf],
    expected_ids: &HashSet<String>,
    parent_session_id: &str,
    excluded_path: Option<&Path>,
    include_metadata_fallback: bool,
) -> SessionDiscoveryResult {
    discover_session_transcripts(
        roots,
        expected_ids,
        excluded_path,
        include_metadata_fallback,
        |meta| meta.parent_thread_id.as_deref() == Some(parent_session_id),
    )
}

pub fn discover_root_session_transcript(
    roots: &[PathBuf],
    session_id: &str,
    excluded_path: Option<&Path>,
) -> SessionDiscoveryResult {
    discover_session_transcripts(
        roots,
        &HashSet::from([session_id.to_string()]),
        excluded_path,
        true,
        |meta| meta.parent_thread_id.is_none(),
    )
}

fn discover_session_transcripts<F>(
    roots: &[PathBuf],
    expected_ids: &HashSet<String>,
    excluded_path: Option<&Path>,
    include_metadata_fallback: bool,
    parent_matches: F,
) -> SessionDiscoveryResult
where
    F: Fn(&SessionMetaIds) -> bool + Copy,
{
    if expected_ids.is_empty() && !include_metadata_fallback {
        return SessionDiscoveryResult::default();
    }

    let excluded = excluded_path.map(canonical_or_owned);
    let mut result = SessionDiscoveryResult::default();
    let mut matches = HashMap::<String, DiscoveredSessionTranscript>::new();
    let mut matched_paths = HashSet::<PathBuf>::new();
    let mut seen_files = HashSet::<PathBuf>::new();

    if !expected_ids.is_empty() {
        visit_jsonl_newest_first(roots, &mut result.directories_visited, |path| {
            let canonical = canonical_or_owned(path);
            if excluded.as_ref() == Some(&canonical) || !seen_files.insert(canonical) {
                return false;
            }
            let Some(identity_id) = expected_id_from_filename(path, expected_ids) else {
                return false;
            };
            result.metadata_files_read += 1;
            let meta = extract_session_meta_ids_from_path(path);
            if !parent_matches(&meta) || !meta_matches_identity(&meta, &identity_id) {
                return false;
            }
            result.exact_filename_matches += 1;
            matched_paths.insert(canonical_or_owned(path));
            matches
                .entry(identity_id.clone())
                .or_insert_with(|| discovered_transcript(path, identity_id, &meta));
            matches.len() == expected_ids.len()
        });
    }

    if include_metadata_fallback && (expected_ids.is_empty() || matches.len() < expected_ids.len())
    {
        seen_files.clear();
        visit_jsonl_newest_first(roots, &mut result.directories_visited, |path| {
            let canonical = canonical_or_owned(path);
            if excluded.as_ref() == Some(&canonical)
                || matched_paths.contains(&canonical)
                || !seen_files.insert(canonical)
            {
                return false;
            }
            result.metadata_files_read += 1;
            let meta = extract_session_meta_ids_from_path(path);
            if !parent_matches(&meta) {
                return false;
            }
            let identity_id = if expected_ids.is_empty() {
                meta.thread_id.clone().or_else(|| meta.session_id.clone())
            } else {
                expected_ids
                    .iter()
                    .find(|id| !matches.contains_key(*id) && meta_matches_identity(&meta, id))
                    .cloned()
            };
            let Some(identity_id) = identity_id else {
                return false;
            };
            matches
                .entry(identity_id.clone())
                .or_insert_with(|| discovered_transcript(path, identity_id, &meta));
            !expected_ids.is_empty() && matches.len() == expected_ids.len()
        });
    }

    let mut ordered: Vec<_> = matches.into_values().collect();
    ordered.sort_by(|left, right| left.identity_id.cmp(&right.identity_id));
    result.matches = ordered;
    result
}

fn discovered_transcript(
    path: &Path,
    identity_id: String,
    meta: &SessionMetaIds,
) -> DiscoveredSessionTranscript {
    DiscoveredSessionTranscript {
        session_id: meta
            .session_id
            .clone()
            .unwrap_or_else(|| identity_id.clone()),
        identity_id,
        path: path.to_path_buf(),
    }
}

fn meta_matches_identity(meta: &SessionMetaIds, identity_id: &str) -> bool {
    meta.session_id.as_deref() == Some(identity_id)
        || meta.thread_id.as_deref() == Some(identity_id)
}

fn expected_id_from_filename(path: &Path, expected_ids: &HashSet<String>) -> Option<String> {
    let stem = path.file_stem()?.to_str()?;
    expected_ids
        .iter()
        .filter(|id| stem == id.as_str() || stem.ends_with(&format!("-{id}")))
        .max_by(|left, right| left.len().cmp(&right.len()).then_with(|| right.cmp(left)))
        .cloned()
}

fn canonical_or_owned(path: &Path) -> PathBuf {
    path.canonicalize().unwrap_or_else(|_| path.to_path_buf())
}

fn visit_jsonl_newest_first<F>(
    roots: &[PathBuf],
    directories_visited: &mut usize,
    mut visitor: F,
) -> bool
where
    F: FnMut(&Path) -> bool,
{
    enum PendingVisit {
        Directory(PathBuf),
        Files(Vec<PathBuf>),
    }

    let mut seen_roots = HashSet::new();
    for root in roots {
        let canonical = canonical_or_owned(root);
        if !seen_roots.insert(canonical) {
            continue;
        }
        let mut pending = vec![PendingVisit::Directory(root.clone())];
        while let Some(next) = pending.pop() {
            match next {
                PendingVisit::Directory(directory) => {
                    let Ok(entries) = std::fs::read_dir(&directory) else {
                        continue;
                    };
                    *directories_visited += 1;

                    let mut directories = Vec::new();
                    let mut jsonl_files = Vec::new();
                    for entry in entries.flatten() {
                        let path = entry.path();
                        let Ok(file_type) = entry.file_type() else {
                            continue;
                        };
                        // DirEntry::file_type does not follow symlinks, so
                        // directory symlinks are intentionally skipped.
                        if file_type.is_dir() {
                            directories.push(path);
                        } else if file_type.is_file()
                            && path.extension().and_then(|extension| extension.to_str())
                                == Some("jsonl")
                        {
                            jsonl_files.push(path);
                        }
                    }

                    jsonl_files.sort_by(|left, right| right.file_name().cmp(&left.file_name()));
                    if !jsonl_files.is_empty() {
                        pending.push(PendingVisit::Files(jsonl_files));
                    }

                    // Stack order is reversed so the lexicographically newest
                    // directory is visited first, matching the recursive walk.
                    directories.sort_by(|left, right| left.file_name().cmp(&right.file_name()));
                    for child in directories {
                        pending.push(PendingVisit::Directory(child));
                    }
                }
                PendingVisit::Files(files) => {
                    if files.into_iter().any(|path| visitor(&path)) {
                        return true;
                    }
                }
            }
        }
    }
    false
}

#[cfg(test)]
mod tests {
    use super::*;

    fn write_session(path: &Path, session_id: &str, parent_id: &str) {
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(
            path,
            format!(
                "{{\"type\":\"session_meta\",\"payload\":{{\"id\":\"{session_id}\",\"source\":{{\"subagent\":{{\"thread_spawn\":{{\"parent_thread_id\":\"{parent_id}\"}}}}}}}}}}\n"
            ),
        )
        .unwrap();
    }

    #[test]
    fn exact_rollout_filename_stops_before_older_dates() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path().join("sessions");
        write_session(
            &root.join("2026/07/16/rollout-2026-07-16T10-00-00-child-new.jsonl"),
            "child-new",
            "parent",
        );
        write_session(
            &root.join("2026/07/15/rollout-2026-07-15T10-00-00-unrelated.jsonl"),
            "unrelated",
            "other-parent",
        );

        let result = discover_child_session_transcripts(
            &[root],
            &HashSet::from(["child-new".to_string()]),
            "parent",
            None,
            true,
        );

        assert_eq!(result.matches.len(), 1);
        assert_eq!(result.exact_filename_matches, 1);
        assert_eq!(result.metadata_files_read, 1);
    }

    #[test]
    fn metadata_fallback_supports_nonstandard_legacy_filename() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path().join("sessions");
        write_session(
            &root.join("2026/05/24/async_subagent.jsonl"),
            "legacy-child",
            "legacy-parent",
        );

        let result = discover_child_session_transcripts(
            &[root],
            &HashSet::from(["legacy-child".to_string()]),
            "legacy-parent",
            None,
            true,
        );

        assert_eq!(result.matches.len(), 1);
        assert_eq!(result.exact_filename_matches, 0);
        assert_eq!(result.metadata_files_read, 1);
        assert!(result.matches[0].path.ends_with("async_subagent.jsonl"));
    }

    #[test]
    fn parent_down_thread_id_matches_expected_legacy_identity() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path().join("sessions");
        let path = root.join("2026/05/24/legacy.jsonl");
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(
            &path,
            "{\"type\":\"session_meta\",\"payload\":{\"session_id\":\"child-session\",\"thread_id\":\"legacy-thread\",\"source\":{\"sub_agent\":{\"thread_spawn\":{\"parent_thread_id\":\"legacy-parent\"}}}}}\n",
        )
        .unwrap();

        let result = discover_child_session_transcripts(
            &[root],
            &HashSet::from(["legacy-thread".to_string()]),
            "legacy-parent",
            None,
            true,
        );

        assert_eq!(result.matches[0].identity_id, "legacy-thread");
        assert_eq!(result.matches[0].session_id, "child-session");
    }

    #[test]
    fn overlapping_filename_suffix_prefers_longest_expected_id() {
        let path = Path::new("rollout-2026-07-16-sub-thread-1.jsonl");
        let expected_ids = HashSet::from(["thread-1".to_string(), "sub-thread-1".to_string()]);

        assert_eq!(
            expected_id_from_filename(path, &expected_ids).as_deref(),
            Some("sub-thread-1")
        );
    }

    #[test]
    fn deeply_nested_session_tree_is_walked_iteratively() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path().join("sessions");
        let mut nested = root.clone();
        for index in 0..128 {
            nested.push(format!("d{index:03}"));
        }
        write_session(
            &nested.join("rollout-2026-07-16-child-deep.jsonl"),
            "child-deep",
            "parent",
        );

        let result = discover_child_session_transcripts(
            &[root],
            &HashSet::from(["child-deep".to_string()]),
            "parent",
            None,
            true,
        );

        assert_eq!(result.matches.len(), 1);
        assert_eq!(result.matches[0].identity_id, "child-deep");
        assert!(result.directories_visited >= 129);
    }
}
