//! `kimihooks_state.json` —— v0.3 引入的统一状态文件。
//!
//! 取代散落的 `hook_log_<sid>.ndjson` 与 `uploaded_<sid>.marker`。
//! 单一文件全局，按 `session_id` 分键，承载：
//!
//! 1. `wire_fingerprint`：`<size>:<line_count>` 字符串，替代旧 marker 的幂等比对
//! 2. `user_prompts`：UserPromptSubmit hook 触发时缓存的原始 prompt
//! 3. `subagent_starts` / `subagent_stops`：子 agent 调用边界缓存
//!
//! Stop handler 会在 wire.jsonl 重建后读取本文件，把缓存条目合并到 synth 事件流。
//!
//! ## 持久化策略
//!
//! 复用 [`agentic_otel_common::state::StateLock`] (fcntl flock + timeout fail-open)
//! 与 [`agentic_otel_common::state::load_state`] / [`save_state`]（atomic rename）。
//! 上层调用方应自行 `StateLock::acquire` 后再 [`load_or_default`] / [`save`]。

use std::collections::BTreeMap;
use std::path::Path;

use serde::{Deserialize, Serialize};

use agentic_otel_common::state::{load_state, save_state};

/// 单条 UserPromptSubmit 缓存。
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct UserPromptEntry {
    pub ts_ns: String,
    pub prompt: String,
    #[serde(default)]
    pub cwd: String,
}

/// 单条 SubagentStart 缓存。
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SubagentStartEntry {
    pub ts_ns: String,
    pub agent_name: String,
    #[serde(default)]
    pub prompt: String,
    #[serde(default)]
    pub cwd: String,
}

/// 单条 SubagentStop 缓存。
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SubagentStopEntry {
    pub ts_ns: String,
    pub agent_name: String,
    #[serde(default)]
    pub response: String,
    #[serde(default)]
    pub cwd: String,
}

/// 单 session 维度的状态切片。新字段需 `#[serde(default)]` 以保持向后兼容。
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct SessionState {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub wire_fingerprint: Option<String>,
    #[serde(default)]
    pub last_seen_ns: String,
    #[serde(default)]
    pub user_prompts: Vec<UserPromptEntry>,
    #[serde(default)]
    pub subagent_starts: Vec<SubagentStartEntry>,
    #[serde(default)]
    pub subagent_stops: Vec<SubagentStopEntry>,
}

const SCHEMA_VERSION: u32 = 1;

fn default_schema_version() -> u32 {
    SCHEMA_VERSION
}

/// 顶层状态结构。
///
/// 序列化形如：
/// ```json
/// {
///   "schema_version": 1,
///   "sessions": {
///     "<sid>": { "wire_fingerprint": "...", "user_prompts": [...] }
///   }
/// }
/// ```
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct KimihooksState {
    #[serde(default = "default_schema_version")]
    pub schema_version: u32,
    #[serde(default)]
    pub sessions: BTreeMap<String, SessionState>,
}

impl Default for KimihooksState {
    fn default() -> Self {
        Self {
            schema_version: SCHEMA_VERSION,
            sessions: BTreeMap::new(),
        }
    }
}

impl KimihooksState {
    /// 从磁盘读取；缺失或解析失败均回落到 [`Default`]。
    ///
    /// `agentic_otel_common::state::load_state` 在文件缺失/解析失败时返回 `{}`，此时
    /// 反序列化为 `KimihooksState` 也会成功（所有字段 `#[serde(default)]`），得到默认空状态。
    pub fn load_or_default(path: &Path) -> Self {
        let v = load_state(path);
        serde_json::from_value(v).unwrap_or_default()
    }

    /// 持久化（atomic rename，复用 `agentic_otel_common::state::save_state`）。
    pub fn save(&self, path: &Path) -> Result<(), String> {
        let v = serde_json::to_value(self).map_err(|e| e.to_string())?;
        save_state(path, &v)
    }

    /// 取/插 session 并执行 mutator。如果 session 不存在则用 [`SessionState::default`] 插入。
    pub fn upsert_session<F: FnOnce(&mut SessionState)>(&mut self, sid: &str, mutator: F) {
        let entry = self.sessions.entry(sid.to_string()).or_default();
        mutator(entry);
    }

    /// 只读访问。
    pub fn get_session(&self, sid: &str) -> Option<&SessionState> {
        self.sessions.get(sid)
    }

    /// 删除 `last_seen_ns` 早于 (now − ttl_ns) 的 session。
    /// `last_seen_ns` 解析失败按 0 处理（极旧），将被淘汰。
    pub fn prune_older_than(&mut self, ttl_ns: u128) {
        let now = parse_ns(&agentic_otel_common::now_nanos());
        let cutoff = now.saturating_sub(ttl_ns);
        self.sessions
            .retain(|_, s| parse_ns(&s.last_seen_ns) >= cutoff);
    }
}

fn parse_ns(s: &str) -> u128 {
    s.parse::<u128>().unwrap_or(0)
}

/// 24 小时 TTL（纳秒），供 handler 调用 [`KimihooksState::prune_older_than`] 时使用。
pub const DEFAULT_TTL_NS: u128 = 24 * 60 * 60 * 1_000_000_000;

#[cfg(test)]
mod tests {
    use super::*;

    fn unique_temp_path() -> std::path::PathBuf {
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        std::env::temp_dir().join(format!(
            "kimihooks-state-test-{}-{}.json",
            std::process::id(),
            nanos
        ))
    }

    #[test]
    fn load_or_default_returns_default_when_missing() {
        let path = unique_temp_path();
        let _ = std::fs::remove_file(&path);
        let s = KimihooksState::load_or_default(&path);
        assert_eq!(s.schema_version, 1);
        assert!(s.sessions.is_empty());
    }

    #[test]
    fn save_and_load_roundtrip() {
        let path = unique_temp_path();
        let mut s = KimihooksState::default();
        s.upsert_session("sess-1", |sess| {
            sess.wire_fingerprint = Some("100:5".to_string());
            sess.last_seen_ns = "1700000000000000000".to_string();
            sess.user_prompts.push(UserPromptEntry {
                ts_ns: "1700000000000000000".to_string(),
                prompt: "hello".to_string(),
                cwd: "/tmp".to_string(),
            });
        });
        s.save(&path).unwrap();

        let loaded = KimihooksState::load_or_default(&path);
        assert_eq!(loaded.schema_version, 1);
        let sess = loaded.get_session("sess-1").expect("session must exist");
        assert_eq!(sess.wire_fingerprint.as_deref(), Some("100:5"));
        assert_eq!(sess.user_prompts.len(), 1);
        assert_eq!(sess.user_prompts[0].prompt, "hello");

        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn upsert_inserts_default_then_mutates() {
        let mut s = KimihooksState::default();
        assert!(s.get_session("new").is_none());

        s.upsert_session("new", |sess| {
            sess.subagent_starts.push(SubagentStartEntry {
                ts_ns: "1".to_string(),
                agent_name: "coder".to_string(),
                prompt: "go".to_string(),
                cwd: String::new(),
            });
        });

        let sess = s.get_session("new").unwrap();
        assert_eq!(sess.subagent_starts.len(), 1);
        assert_eq!(sess.subagent_starts[0].agent_name, "coder");

        // Mutate again — should not duplicate the session entry.
        s.upsert_session("new", |sess| {
            sess.subagent_stops.push(SubagentStopEntry {
                ts_ns: "2".to_string(),
                agent_name: "coder".to_string(),
                response: "done".to_string(),
                cwd: String::new(),
            });
        });

        assert_eq!(s.sessions.len(), 1);
        let sess = s.get_session("new").unwrap();
        assert_eq!(sess.subagent_starts.len(), 1);
        assert_eq!(sess.subagent_stops.len(), 1);
    }

    #[test]
    fn prune_drops_stale_sessions_and_keeps_fresh() {
        let now = agentic_otel_common::now_nanos().parse::<u128>().unwrap();
        let mut s = KimihooksState::default();

        // Stale: last_seen 2 days ago
        let stale_ns = now.saturating_sub(48 * 60 * 60 * 1_000_000_000);
        s.upsert_session("old", |sess| {
            sess.last_seen_ns = stale_ns.to_string();
        });

        // Fresh: just now
        s.upsert_session("fresh", |sess| {
            sess.last_seen_ns = now.to_string();
        });

        // No timestamp at all — treated as 0, should be pruned.
        s.upsert_session("zero", |sess| {
            sess.last_seen_ns = "".to_string();
        });

        s.prune_older_than(DEFAULT_TTL_NS);

        assert!(s.get_session("old").is_none());
        assert!(s.get_session("zero").is_none());
        assert!(s.get_session("fresh").is_some());
    }

    #[test]
    fn invalid_json_falls_back_to_default() {
        let path = unique_temp_path();
        std::fs::write(&path, "{ this is not json").unwrap();
        let s = KimihooksState::load_or_default(&path);
        assert_eq!(s.schema_version, 1);
        assert!(s.sessions.is_empty());
        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn forward_compatible_unknown_fields_ignored() {
        let path = unique_temp_path();
        let raw = r#"{
            "schema_version": 1,
            "sessions": {
                "abc": {
                    "wire_fingerprint": "9:9",
                    "last_seen_ns": "100",
                    "user_prompts": [],
                    "future_field": "ignore-me"
                }
            },
            "another_top_level": 42
        }"#;
        std::fs::write(&path, raw).unwrap();
        let s = KimihooksState::load_or_default(&path);
        let sess = s.get_session("abc").unwrap_or_else(|| {
            panic!(
                "session 'abc' missing after load_or_default — sessions = {:?}",
                s.sessions.keys().collect::<Vec<_>>()
            )
        });
        assert_eq!(sess.wire_fingerprint.as_deref(), Some("9:9"));
        let _ = std::fs::remove_file(&path);
    }
}
