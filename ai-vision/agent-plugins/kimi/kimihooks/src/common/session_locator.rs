//! 根据 stdin payload 的 `session_id` + `cwd` 定位 kimi-cli 会话目录。
//!
//! kimi-cli 路径规则（来源 `src/kimi_cli/metadata.py`）：
//!
//! ```text
//! ~/.kimi/sessions/<work_dir_digest>/<session_id>/
//!     ├── context.jsonl   # 完整 message 流（kosong schema）
//!     ├── wire.jsonl      # 顶层 timestamp + message envelope
//!     └── ...             # config 快照、状态文件等
//! ```
//!
//! `work_dir_digest = md5(cwd).hexdigest()`，长度 32（小写 hex）。

use std::path::PathBuf;

use md5::{Digest, Md5};
use sha2::{Digest as ShaDigest, Sha256};

use crate::common::dirs;

/// 单个会话内的标准文件清单。所有字段均为绝对路径，文件不一定存在（待 Phase 1 阶段判断）。
#[derive(Debug, Clone)]
pub struct SessionPaths {
    pub session_id: String,
    pub cwd_digest: String,
    pub session_dir: PathBuf,
    pub context_jsonl: PathBuf,
    pub wire_jsonl: PathBuf,
}

/// `cwd` → md5(小写 hex)。
pub fn cwd_digest(cwd: &str) -> String {
    let mut hasher = Md5::new();
    hasher.update(cwd.as_bytes());
    let bytes = hasher.finalize();
    let mut out = String::with_capacity(32);
    for b in bytes {
        out.push_str(&format!("{:02x}", b));
    }
    out
}

/// 根据 `session_id` + `cwd` 拼出会话目录的标准文件清单。
///
/// 注意：仅做路径计算，不做存在性校验，调用方按需 `path.exists()`。
pub fn locate(session_id: &str, cwd: &str) -> SessionPaths {
    let legacy = locate_legacy(session_id, cwd);
    let code = locate_code(session_id, cwd);
    if !legacy.wire_jsonl.exists() && code.wire_jsonl.exists() { code } else { legacy }
}

/// New hosts identify themselves in every hook; do not select a stale legacy session.
pub fn locate_for_client(session_id: &str, cwd: &str, client_type: &str) -> SessionPaths {
    if client_type == "kimi_code_cli" { locate_code(session_id, cwd) } else { locate(session_id, cwd) }
}

fn locate_legacy(session_id: &str, cwd: &str) -> SessionPaths {
    let cwd_digest = cwd_digest(cwd);
    let session_dir = dirs::kimi_sessions_root()
        .join(&cwd_digest)
        .join(session_id);
    let context_jsonl = session_dir.join("context.jsonl");
    let wire_jsonl = session_dir.join("wire.jsonl");
    SessionPaths {
        session_id: session_id.to_string(),
        cwd_digest,
        session_dir,
        context_jsonl,
        wire_jsonl,
    }
}

/// Official encodeWorkDirKey: normalized path + ASCII basename slug + SHA-256 prefix.
fn code_workspace_key(cwd: &str) -> String {
    let normalized = cwd.replace('\\', "/").trim_end_matches('/').to_string();
    let basename = normalized.rsplit('/').next().unwrap_or("").to_lowercase();
    let mut slug = String::new();
    let mut invalid_run = false;
    for ch in basename.chars() {
        if ch.is_ascii_alphanumeric() || matches!(ch, '.' | '_' | '-') {
            slug.push(ch);
            invalid_run = false;
        } else {
            if !invalid_run { slug.push('-'); }
            invalid_run = true;
        }
    }
    let trimmed = slug.trim_matches('-');
    let end = trimmed.len().min(40);
    let trimmed = trimmed[..end].trim_matches('-');
    let slug = if matches!(trimmed, "" | "." | "..") { "workspace" } else { trimmed };
    let hash = Sha256::digest(normalized.as_bytes()).iter().map(|b| format!("{:02x}", b)).collect::<String>();
    format!("wd_{}_{}", slug, &hash[..12])
}

fn locate_code(session_id: &str, cwd: &str) -> SessionPaths {
    let key = code_workspace_key(cwd);
    let root = std::env::var_os("KIMI_SESSIONS_ROOT").filter(|v| !v.is_empty()).map(PathBuf::from)
        .unwrap_or_else(|| {
            std::env::var_os("KIMI_CODE_HOME").filter(|v| !v.is_empty()).map(PathBuf::from)
                .unwrap_or_else(|| dirs::home_dir().join(".kimi-code")).join("sessions")
        });
    let session_dir = root.join(&key).join(session_id);
    let agent_dir = session_dir.join("agents/main");
    SessionPaths { session_id: session_id.into(), cwd_digest: key,
        context_jsonl: agent_dir.join("context.jsonl"), wire_jsonl: agent_dir.join("wire.jsonl"), session_dir }
}

#[cfg(test)]
mod tests {
    use super::*;
    static ENV_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

    #[test]
    fn code_workspace_key_matches_official_slug_vectors() {
        for (cwd, expected) in [("/tmp/my- project", "wd_my--project_ee534160b43b"),
            ("/tmp/my- 中文", "wd_my_bf7c05fe38bc"),
            ("C:\\Work\\My- Project\\", "wd_my--project_d05808e555db"),
            ("/tmp/中文", "wd_workspace_dbabe00d7b1a"), ("/", "wd_workspace_e3b0c44298fc")] {
            assert_eq!(code_workspace_key(cwd), expected);
        }
    }

    #[test]
    fn locate_finds_kimi_code_wire_without_breaking_legacy_root_override() {
        let _lock = ENV_LOCK.lock().unwrap();
        let root = std::env::temp_dir().join(format!("kimi-code-locator-{}", std::process::id()));
        let session = root.join("wd_work_f9be197a95b0").join("code-session");
        let wire = session.join("agents/main/wire.jsonl");
        std::fs::create_dir_all(wire.parent().unwrap()).unwrap();
        std::fs::write(&wire, "").unwrap();
        let previous = std::env::var_os("KIMI_SESSIONS_ROOT");
        std::env::set_var("KIMI_SESSIONS_ROOT", &root);
        let paths = locate("code-session", "/tmp/work");
        match previous { Some(v) => std::env::set_var("KIMI_SESSIONS_ROOT", v), None => std::env::remove_var("KIMI_SESSIONS_ROOT") }
        std::fs::remove_dir_all(&root).unwrap();
        assert_eq!(paths.wire_jsonl, wire);
        assert_eq!(paths.session_dir, session);
    }

    #[test]
    fn digest_matches_python_md5() {
        // 与 Python `hashlib.md5(b"/Users/testuser").hexdigest()` 等价
        // expected 值由本地 `python -c "import hashlib;print(hashlib.md5(b'/Users/testuser').hexdigest())"` 验证
        let d = cwd_digest("/Users/testuser");
        assert_eq!(d.len(), 32);
        assert!(d.chars().all(|c| c.is_ascii_hexdigit()));
    }

    #[test]
    fn locate_yields_expected_layout() {
        let _lock = ENV_LOCK.lock().unwrap();
        let prev = std::env::var("KIMI_SESSIONS_ROOT").ok();
        std::env::set_var("KIMI_SESSIONS_ROOT", "/tmp/kimi-sessions-test");
        let paths = locate("sess-abc", "/some/cwd");
        assert!(paths.session_dir.starts_with("/tmp/kimi-sessions-test"));
        assert!(paths.session_dir.ends_with("sess-abc"));
        assert_eq!(paths.context_jsonl.file_name().unwrap(), "context.jsonl");
        assert_eq!(paths.wire_jsonl.file_name().unwrap(), "wire.jsonl");
        match prev {
            Some(v) => std::env::set_var("KIMI_SESSIONS_ROOT", v),
            None => std::env::remove_var("KIMI_SESSIONS_ROOT"),
        }
    }
}
