//! Phase 1: transcript 采集。
//!
//! 行为：把 kimi-cli 当前会话的 `wire.jsonl` / `context.jsonl` 复制到
//! `KIMI_SESSION_DIR` 或默认的 `~/.aivision/kimi/transcripts`，文件名遵循约定：
//!
//! ```text
//! session_{context}_{hash}.jsonl
//! ```
//!
//! kimi 的 `{context}` 取 cwd 的最后一段（与 cchooks 的 project 名等价），`{hash}` 取
//! `cwd_digest` 前 8 位 + `session_id` 前 8 位的拼接，保证不同 cwd / 不同 session 不冲突。
//!
//! 复制策略：
//! - **追加复制**：每次 hook 触发都把 source 全文 ( wire / context ) 复制到目标。文件较大时
//!   仍是常数级 IO（hook 执行期 < 30s），符合 cchooks 同款"重复复制 = 最新快照"约定。
//! - **目标不存在的 source 跳过**：例如 `SessionStart` 时 `wire.jsonl` 还未生成，直接 skip。
//!
//! 后续 Phase 2 由 `kimihooks-replay` 在 `KIMI_REPLAY_DIR` 中产出 `spans_{context}_{hash}.jsonl`。

use std::fs;
use std::path::Path;

use crate::common::dirs;
use crate::common::logger::Logger;
use crate::common::session_locator::SessionPaths;

/// 单次复制结果。`copied` 表示真实写盘的字节数，`skipped` 表示因 source 不存在等原因跳过。
#[derive(Debug, Default)]
pub struct CopyReport {
    pub copied: u64,
    pub skipped: usize,
}

/// 把 session 目录下的 `wire.jsonl` + `context.jsonl` + `state.json` 复制到 `KIMI_SESSION_DIR`。
///
/// 支持两种路径模式（由 `dirs::parse_session_copy_target()` 解析）：
/// - **目录模式**：文件按 `session_{context}_{hash}.{kind}.jsonl` 命名
/// - **文件模式**（`.jsonl` 后缀）：wire 用指定文件名，其他文件按后缀放同目录
pub fn snapshot_to_session_dir(paths: &SessionPaths, logger: &Logger) -> CopyReport {
    let Some(target) = dirs::parse_session_copy_target() else {
        return CopyReport::default();
    };
    if let Err(e) = dirs::ensure_private_dir(&target.directory) {
        logger.warn(&format!(
            "无法创建 KIMI_SESSION_DIR={}: {}",
            target.directory.display(),
            e
        ));
        return CopyReport::default();
    }

    let context_label = derive_context_label(&paths.session_dir);
    let hash_token = derive_hash_token(&paths.cwd_digest, &paths.session_id);

    let mut report = CopyReport::default();

    // 需要迁移的文件列表：(源路径, kind 标签)
    let sources: Vec<(&std::path::Path, &str)> = vec![
        (&paths.wire_jsonl, "wire"),
        (&paths.context_jsonl, "context"),
    ];

    for (source, kind) in sources {
        if !source.exists() {
            report.skipped += 1;
            logger.debug(&format!(
                "snapshot skip: 源文件 {} 尚未生成",
                source.display()
            ));
            continue;
        }

        let dest = if target.wire_filename_override.is_some() {
            // 文件模式：所有文件统一用 {stem}.{kind}.{ext} 命名
            let wire_name = target.wire_filename_override.as_ref().unwrap();
            let stem = wire_name.strip_suffix(".jsonl").unwrap_or(wire_name);
            let ext = if kind == "state" { "json" } else { "jsonl" };
            target.directory.join(format!("{}.{}.{}", stem, kind, ext))
        } else {
            // 目录模式：按约定命名
            let ext = if kind == "state" { "json" } else { "jsonl" };
            target.directory.join(format!(
                "session_{}_{}.{}.{}",
                context_label, hash_token, kind, ext
            ))
        };

        match fs::copy(source, &dest) {
            Ok(n) => {
                report.copied = report.copied.saturating_add(n);
                logger.debug(&format!(
                    "snapshot ok: {} -> {} ({} bytes)",
                    source.display(),
                    dest.display(),
                    n
                ));
            }
            Err(e) => {
                report.skipped += 1;
                logger.warn(&format!(
                    "snapshot fail: {} -> {}: {}",
                    source.display(),
                    dest.display(),
                    e
                ));
            }
        }
    }

    // subagents 目录整体复制（如果存在）
    let subagents_src = paths.session_dir.join("subagents");
    if subagents_src.is_dir() {
        let subagents_dest = if target.wire_filename_override.is_some() {
            let wire_name = target.wire_filename_override.as_ref().unwrap();
            let stem = wire_name.strip_suffix(".jsonl").unwrap_or(wire_name);
            target.directory.join(format!("{}.subagents", stem))
        } else {
            target.directory.join(format!(
                "session_{}_{}.subagents",
                context_label, hash_token
            ))
        };
        match copy_dir_recursive(&subagents_src, &subagents_dest) {
            Ok(n) => {
                report.copied = report.copied.saturating_add(n);
                logger.debug(&format!(
                    "snapshot subagents ok: {} -> {} ({} bytes)",
                    subagents_src.display(),
                    subagents_dest.display(),
                    n
                ));
            }
            Err(e) => {
                report.skipped += 1;
                logger.warn(&format!(
                    "snapshot subagents fail: {} -> {}: {}",
                    subagents_src.display(),
                    subagents_dest.display(),
                    e
                ));
            }
        }
    }

    report
}

/// 递归复制目录，返回总字节数。
fn copy_dir_recursive(src: &Path, dest: &Path) -> std::io::Result<u64> {
    fs::create_dir_all(dest)?;
    let mut total: u64 = 0;
    for entry in fs::read_dir(src)? {
        let entry = entry?;
        let src_path = entry.path();
        let dest_path = dest.join(entry.file_name());
        if src_path.is_dir() {
            total += copy_dir_recursive(&src_path, &dest_path)?;
        } else {
            total += fs::copy(&src_path, &dest_path)?;
        }
    }
    Ok(total)
}

/// 从 `~/.kimi/sessions/<digest>/<session_id>/` 反推一个易读的 context 标签。
///
/// 由于 kimi 不在路径里携带 cwd 字符串，只能用 `session_id` 前 12 位兜底；当未来 metadata.json
/// 写入时（kimi 上游有 PR），可以升级为读取 `metadata.json::cwd` 的 basename。
fn derive_context_label(session_dir: &Path) -> String {
    session_dir
        .file_name()
        .and_then(|s| s.to_str())
        .map(|s| sanitize(&s.chars().take(12).collect::<String>()))
        .unwrap_or_else(|| "kimi".to_string())
}

fn derive_hash_token(cwd_digest: &str, session_id: &str) -> String {
    let cwd_part: String = cwd_digest.chars().take(8).collect();
    let sess_part: String = session_id.chars().take(8).collect();
    format!("{}{}", cwd_part, sanitize(&sess_part))
}

/// 仅保留 [A-Za-z0-9_-]，其他字符替换为 '-'，避免文件名跨平台问题。
fn sanitize(s: &str) -> String {
    s.chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || c == '_' || c == '-' {
                c
            } else {
                '-'
            }
        })
        .collect()
}

/// 提供给 `kimihooks-replay` 使用：把单个 session 目录路径下的 `wire.jsonl` 复制到指定目标。
/// 当前阶段只是薄壳，Phase 2 后会由真正的 OTLP 转换流程替代。
pub fn copy_wire_for_replay(session_dir: &Path, target: &Path) -> std::io::Result<u64> {
    let wire = session_dir.join("wire.jsonl");
    fs::copy(&wire, target)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    #[test]
    fn sanitize_replaces_unsafe_chars() {
        assert_eq!(sanitize("a/b c.d"), "a-b-c-d");
        assert_eq!(sanitize("Abc_123-XY"), "Abc_123-XY");
    }

    #[test]
    fn derive_hash_token_combines_cwd_and_session() {
        let token = derive_hash_token("0123456789abcdef0123456789abcdef", "sess-abc-xyz");
        assert_eq!(token, "01234567sess-abc"); // 前 8 + 前 8（'sess-abc'）
    }

    #[test]
    fn snapshot_uses_canonical_default_when_session_dir_unset() {
        let prev = std::env::var("KIMI_SESSION_DIR").ok();
        let prev_aivision_home = std::env::var("AIVISION_HOME").ok();
        std::env::remove_var("KIMI_SESSION_DIR");

        let tmp = tempdir_like();
        let output_root = tmp.join("aivision-home");
        std::env::set_var("AIVISION_HOME", &output_root);
        std::fs::write(tmp.join("wire.jsonl"), "{}\n").unwrap();
        let paths = SessionPaths {
            session_id: "s1".to_string(),
            cwd_digest: "d".repeat(32),
            session_dir: tmp.clone(),
            context_jsonl: tmp.join("context.jsonl"),
            wire_jsonl: tmp.join("wire.jsonl"),
        };
        let logger = Logger::new(tmp.join("log"), false);
        let report = snapshot_to_session_dir(&paths, &logger);
        assert!(report.copied > 0);
        let transcript_root = output_root.join("kimi/transcripts");
        assert!(transcript_root.is_dir());
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(
                std::fs::metadata(transcript_root)
                    .unwrap()
                    .permissions()
                    .mode()
                    & 0o777,
                0o700
            );
        }

        if let Some(v) = prev {
            std::env::set_var("KIMI_SESSION_DIR", v);
        } else {
            std::env::remove_var("KIMI_SESSION_DIR");
        }
        match prev_aivision_home {
            Some(v) => std::env::set_var("AIVISION_HOME", v),
            None => std::env::remove_var("AIVISION_HOME"),
        }
    }

    fn tempdir_like() -> PathBuf {
        let p = std::env::temp_dir().join(format!("kimihooks-test-{}", std::process::id()));
        let _ = std::fs::create_dir_all(&p);
        p
    }
}
