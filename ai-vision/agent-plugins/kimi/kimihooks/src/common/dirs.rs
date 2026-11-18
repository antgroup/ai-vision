//! 目录与环境变量解析。
//!
//! 与 cchooks 对齐：所有目录都允许 `KIMI_*` 环境变量覆盖，未设置则落到
//! edition 特定的根目录下。
//!
//! | Edition  | 根目录                          |
//! |----------|---------------------------------|
//! | common   | `~/.kimi/hooks/kimihooks`       |
//! | aivision | `~/.aivision/km`                |
//!
//! 与 `AGENTS.md` 中 Phase 0 表格的对应关系：
//!
//! | env                       | 行为                                                |
//! | ------------------------- | --------------------------------------------------- |
//! | `KIMI_SESSION_DIR`     | transcript 复制目标目录                            |
//! | `KIMI_REPLAY_DIR`      | OTLP replay 输出目录                                |
//! | `KIMI_DEBUG`           | `true` 时启用 stderr/文件双路日志 + 自动写 replay   |
//! | `KIMI_HOOKS_DIR`       | 二进制安装目录覆盖                                  |
//! | `KIMI_CONFIG_DIR`      | 配置文件目录覆盖                                    |
//! | `KIMI_LOG_DIR`         | 日志目录覆盖                                        |
//! | `KIMI_STATE_DIR`       | 状态文件目录覆盖                                    |
//! | `KIMI_CONFIG_FILE`     | 用户配置文件路径覆盖                                |
//! | `KIMI_SESSIONS_ROOT`   | kimi-cli 会话根目录覆盖（默认 `~/.kimi/sessions`）  |

use std::path::PathBuf;

/// 用户 Home 目录。优先 `HOME`（Unix），回退 `USERPROFILE`（Windows），最终回退平台 fallback。
pub fn home_dir() -> PathBuf {
    if let Ok(home) = std::env::var("HOME") {
        if !home.is_empty() {
            return PathBuf::from(home);
        }
    }
    if let Ok(profile) = std::env::var("USERPROFILE") {
        if !profile.is_empty() {
            return PathBuf::from(profile);
        }
    }
    #[cfg(windows)]
    {
        PathBuf::from(r"C:\Temp")
    }
    #[cfg(not(windows))]
    {
        PathBuf::from("/tmp")
    }
}

/// kimihooks 自身工作根目录。
///
/// - common:   `~/.kimi/hooks/kimihooks`
/// - aivision: `~/.aivision/kimi`
pub fn root_dir() -> PathBuf {
    let home = home_dir();
    #[cfg(feature = "aivision")]
    {
        home.join(".aivision").join("kimi")
    }
    #[cfg(not(feature = "aivision"))]
    {
        home.join(".kimi").join("hooks").join("kimihooks")
    }
}

fn aivision_home_from(home: PathBuf, configured: Option<&str>) -> PathBuf {
    configured
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| home.join(".aivision"))
}

/// 共享 AiVision 数据根目录：`AIVISION_HOME` > `~/.aivision`。
pub fn aivision_home_dir() -> PathBuf {
    aivision_home_from(home_dir(), std::env::var("AIVISION_HOME").ok().as_deref())
}

/// Kimi 的 AiVision artifact 根目录。
pub fn artifact_root_dir() -> PathBuf {
    aivision_home_dir().join("kimi")
}

#[cfg(test)]
fn artifact_dir_from(
    home: PathBuf,
    aivision_home: Option<&str>,
    component_override: Option<&str>,
    kind: &str,
) -> PathBuf {
    component_override
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            aivision_home_from(home, aivision_home)
                .join("kimi")
                .join(kind)
        })
}

/// 二进制安装目录（hooks 子目录兼容）：`KIMI_HOOKS_DIR` > `{root}/hooks`。
pub fn hooks_dir() -> PathBuf {
    env_dir_or("KIMI_HOOKS_DIR", root_dir().join("hooks"))
}

/// 配置文件目录：`KIMI_CONFIG_DIR` > `{root}/config`。
pub fn config_dir() -> PathBuf {
    env_dir_or("KIMI_CONFIG_DIR", root_dir().join("config"))
}

/// 日志目录：`KIMI_LOG_DIR` > `{root}/logs`。
pub fn log_dir() -> PathBuf {
    env_dir_or("KIMI_LOG_DIR", root_dir().join("logs"))
}

/// 状态文件目录：`KIMI_STATE_DIR` > `{root}/state`。
pub fn state_dir() -> PathBuf {
    env_dir_or("KIMI_STATE_DIR", root_dir().join("state"))
}

/// 用户配置文件路径：`KIMI_CONFIG_FILE` > `{config_dir}/config.json`。
pub fn user_config_path() -> PathBuf {
    env_dir_or("KIMI_CONFIG_FILE", config_dir().join("config.json"))
}

/// kimi-cli 自身的会话根目录：`KIMI_SESSIONS_ROOT` > `~/.kimi/sessions`。
///
/// 取值依据：经源码核查 `kimi-cli` 把会话写到 `~/.kimi/sessions/<digest>/<session_id>/`，
/// 其中 `digest = md5(work_dir_path).hexdigest()`（来源 `src/kimi_cli/metadata.py`）。
pub fn kimi_sessions_root() -> PathBuf {
    env_dir_or(
        "KIMI_SESSIONS_ROOT",
        home_dir().join(".kimi").join("sessions"),
    )
}

/// transcript 落盘目标：`KIMI_SESSION_DIR` > `{AIVISION_HOME}/kimi/transcripts`。
///
/// 支持两种路径模式：
/// - **目录模式**（默认）：`KIMI_SESSION_DIR=/some/path/`
///   → 文件按命名规则写入该目录下
/// - **文件模式**：`KIMI_SESSION_DIR=/some/path/file.jsonl`
///   → wire.jsonl 直接写到该路径，其他文件（context.jsonl / state.json）写到同目录
pub fn session_copy_dir() -> Option<PathBuf> {
    Some(env_dir_or(
        "KIMI_SESSION_DIR",
        artifact_root_dir().join("transcripts"),
    ))
}

/// 解析 `KIMI_SESSION_DIR` 路径模式。
///
/// 返回 `(parent_dir, wire_filename_override)`：
/// - 目录模式：`(dir, None)` — 调用方自行命名
/// - 文件模式：`(parent, Some("file.jsonl"))` — wire.jsonl 直接用该名称
pub fn parse_session_copy_target() -> Option<SessionCopyTarget> {
    let raw = session_copy_dir()?;
    let raw_str = raw.to_string_lossy();
    if raw_str.ends_with(".jsonl") || raw_str.ends_with(".json") {
        // 文件模式：路径指向一个具体文件
        let parent = raw
            .parent()
            .map(|p| p.to_path_buf())
            .unwrap_or_else(|| PathBuf::from("."));
        let filename = raw
            .file_name()
            .map(|f| f.to_string_lossy().to_string())
            .unwrap_or_default();
        Some(SessionCopyTarget {
            directory: parent,
            wire_filename_override: Some(filename),
        })
    } else {
        // 目录模式
        Some(SessionCopyTarget {
            directory: raw,
            wire_filename_override: None,
        })
    }
}

/// `KIMI_SESSION_DIR` 解析后的目标描述。
#[derive(Debug, Clone)]
pub struct SessionCopyTarget {
    /// 所有文件写入的父目录。
    pub directory: PathBuf,
    /// 文件模式下 wire.jsonl 的目标文件名（如 `"session_xxx.jsonl"`）。
    /// `None` 表示目录模式，由调用方自行决定命名。
    pub wire_filename_override: Option<String>,
}

/// OTLP replay 输出目录：`KIMI_REPLAY_DIR` > `{AIVISION_HOME}/kimi/replays`。
pub fn replay_dir() -> Option<PathBuf> {
    Some(env_dir_or(
        "KIMI_REPLAY_DIR",
        artifact_root_dir().join("replays"),
    ))
}

/// 是否开启 debug 双写模式（`KIMI_DEBUG=true`，大小写不敏感）。
pub fn debug_enabled() -> bool {
    std::env::var("KIMI_DEBUG")
        .map(|v| v.eq_ignore_ascii_case("true"))
        .unwrap_or(false)
}

fn env_dir_or(var: &str, default: PathBuf) -> PathBuf {
    env_dir_opt(var).unwrap_or(default)
}

fn env_dir_opt(var: &str) -> Option<PathBuf> {
    std::env::var(var)
        .ok()
        .filter(|v| !v.is_empty())
        .map(PathBuf::from)
}

/// 确保普通运行时目录存在，不修改已有目录权限。
pub fn ensure_dir(path: &std::path::Path) -> std::io::Result<()> {
    if path.exists() {
        Ok(())
    } else {
        std::fs::create_dir_all(path)
    }
}

/// 确保 artifact 目录存在，并在支持的平台上限制为当前用户可访问。
pub fn ensure_private_dir(path: &std::path::Path) -> std::io::Result<()> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::{DirBuilderExt, PermissionsExt};
        let mut builder = std::fs::DirBuilder::new();
        builder.recursive(true).mode(0o700).create(path)?;
        std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o700))
    }
    #[cfg(not(unix))]
    {
        std::fs::create_dir_all(path)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn root_dir_edition_specific() {
        let root = root_dir();
        let s = root.to_string_lossy();
        #[cfg(feature = "aivision")]
        assert!(s.contains(".aivision"), "root={}", s);
        #[cfg(not(feature = "aivision"))]
        assert!(s.contains(".kimi"), "root={}", s);
    }

    #[test]
    fn log_dir_env_override() {
        let prev = std::env::var("KIMI_LOG_DIR").ok();
        std::env::set_var("KIMI_LOG_DIR", "/tmp/kimihooks-logs-test");
        assert_eq!(log_dir(), PathBuf::from("/tmp/kimihooks-logs-test"));
        match prev {
            Some(v) => std::env::set_var("KIMI_LOG_DIR", v),
            None => std::env::remove_var("KIMI_LOG_DIR"),
        }
    }

    #[test]
    fn config_dir_under_root() {
        let cdir = config_dir();
        assert!(cdir.to_string_lossy().ends_with("config"));
    }

    #[test]
    fn user_config_path_defaults() {
        let prev = std::env::var("KIMI_CONFIG_FILE").ok();
        std::env::remove_var("KIMI_CONFIG_FILE");
        let path = user_config_path();
        assert!(path.to_string_lossy().ends_with("config.json"));
        match prev {
            Some(v) => std::env::set_var("KIMI_CONFIG_FILE", v),
            None => std::env::remove_var("KIMI_CONFIG_FILE"),
        }
    }

    #[test]
    fn artifact_paths_use_canonical_root_and_overrides() {
        let home = PathBuf::from("/fake/home");
        assert_eq!(
            aivision_home_from(home.clone(), None),
            home.join(".aivision")
        );
        assert_eq!(
            aivision_home_from(home.clone(), Some("")),
            home.join(".aivision")
        );
        assert_eq!(
            aivision_home_from(home, Some("/shared/aivision")),
            PathBuf::from("/shared/aivision")
        );
        assert_eq!(
            artifact_dir_from(PathBuf::from("/fake/home"), None, None, "transcripts"),
            PathBuf::from("/fake/home/.aivision/kimi/transcripts")
        );
        assert_eq!(
            artifact_dir_from(
                PathBuf::from("/fake/home"),
                Some("/shared/aivision"),
                Some(""),
                "replays",
            ),
            PathBuf::from("/shared/aivision/kimi/replays")
        );
        assert_eq!(
            artifact_dir_from(
                PathBuf::from("/fake/home"),
                Some("/shared/aivision"),
                Some("/custom/replays"),
                "replays",
            ),
            PathBuf::from("/custom/replays")
        );
    }

    #[test]
    fn debug_disabled_by_default() {
        let prev = std::env::var("KIMI_DEBUG").ok();
        std::env::remove_var("KIMI_DEBUG");
        assert!(!debug_enabled());
        std::env::set_var("KIMI_DEBUG", "TrUe");
        assert!(debug_enabled());
        std::env::set_var("KIMI_DEBUG", "no");
        assert!(!debug_enabled());
        match prev {
            Some(v) => std::env::set_var("KIMI_DEBUG", v),
            None => std::env::remove_var("KIMI_DEBUG"),
        }
    }

    #[cfg(unix)]
    #[test]
    fn directory_helpers_preserve_general_permissions_and_harden_artifacts() {
        use std::os::unix::fs::PermissionsExt;

        let root = std::env::temp_dir().join(format!(
            "kimihooks-dir-permissions-{}-{:?}",
            std::process::id(),
            std::thread::current().id()
        ));
        let general = root.join("general");
        let private = root.join("private");
        std::fs::create_dir_all(&general).unwrap();
        std::fs::set_permissions(&general, std::fs::Permissions::from_mode(0o755)).unwrap();

        ensure_dir(&general).unwrap();
        assert_eq!(
            std::fs::metadata(&general).unwrap().permissions().mode() & 0o777,
            0o755
        );

        std::fs::create_dir_all(&private).unwrap();
        std::fs::set_permissions(&private, std::fs::Permissions::from_mode(0o755)).unwrap();
        ensure_private_dir(&private).unwrap();
        assert_eq!(
            std::fs::metadata(&private).unwrap().permissions().mode() & 0o777,
            0o700
        );

        let _ = std::fs::remove_dir_all(root);
    }
}
