//! 用户配置文件加载。
//!
//! 读取 `{root}/config/config.json`（路径可通过 `KIMI_CONFIG_FILE` 覆盖），
//! 解析为 `UserConfig` 结构体。
//!
//! `UserConfig` 所有字段都是 `Option<>`，只覆盖需要修改的字段。
//! 在 `edition.rs::EditionDefaults::merge_user_config()` 中合并到编译时默认值。
//!
//! 配置文件格式与 `configs/{edition}.config.json` 相同（嵌套 JSON），
//! 反序列化时展平为可直接合并的字段。

use std::collections::HashMap;

/// 用户配置文件顶层结构。所有字段可选，缺失字段表示保持默认值。
///
/// 反序列化时从嵌套 JSON（`metricsConfig.endpoint`）展平到扁平字段（`metrics_endpoint`）。
#[derive(Debug, Default)]
pub struct UserConfig {
    pub enable_telemetry: Option<bool>,
    pub service_name: Option<String>,
    pub agent_type: Option<String>,
    pub agent_product: Option<String>,
    pub agent_instance: Option<String>,
    pub username: Option<String>,
    pub otel_protocol: Option<String>,
    pub metrics_endpoint: Option<String>,
    pub logs_endpoint: Option<String>,
    pub status_endpoint: Option<String>,
    pub traces_headers: Option<HashMap<String, String>>,
    pub metrics_headers: Option<HashMap<String, String>>,
    pub logs_headers: Option<HashMap<String, String>>,
    pub status_headers: Option<HashMap<String, String>>,
    pub log: Option<LogConfig>,
}

/// 内容采集控制。
#[derive(Debug, Default)]
pub struct LogConfig {
    /// Shorthands（同时应用于 spans 和 events，除非被具体项覆盖）
    pub user_prompts: Option<bool>,
    pub model_outputs: Option<bool>,
    pub tool_details: Option<bool>,
    pub tool_outputs: Option<bool>,
    /// 细粒度：spans
    pub spans: Option<LogSignalConfig>,
    /// 细粒度：events
    pub events: Option<LogSignalConfig>,
}

#[derive(Debug, Default)]
pub struct LogSignalConfig {
    pub user_prompts: Option<bool>,
    pub model_outputs: Option<bool>,
    pub tool_details: Option<bool>,
    pub tool_outputs: Option<bool>,
}

// ── Deserialization from nested JSON (matching config.json format) ───

/// Intermediate struct matching the JSON structure for deserialization.
#[derive(Debug, serde::Deserialize)]
struct RawConfig {
    enable_telemetry: Option<bool>,
    service_name: Option<String>,
    ant: Option<RawAntConfig>,
    otel_exporter: Option<RawOtelExporterConfig>,
    traces_config: Option<RawSignalConfig>,
    metrics_config: Option<RawSignalConfig>,
    logs_config: Option<RawSignalConfig>,
    status_config: Option<RawSignalConfig>,
    log: Option<RawLogConfig>,
}

#[derive(Debug, serde::Deserialize)]
struct RawAntConfig {
    agent_type: Option<String>,
    agent_product: Option<String>,
    agent_instance: Option<String>,
    username: Option<String>,
}

#[derive(Debug, serde::Deserialize)]
struct RawOtelExporterConfig {
    protocol: Option<String>,
}

#[derive(Debug, serde::Deserialize)]
struct RawSignalConfig {
    #[allow(dead_code)]
    enabled: Option<bool>,
    endpoint: Option<String>,
    headers: Option<HashMap<String, String>>,
}

#[derive(Debug, serde::Deserialize)]
struct RawLogConfig {
    user_prompts: Option<bool>,
    model_outputs: Option<bool>,
    tool_details: Option<bool>,
    tool_outputs: Option<bool>,
    spans: Option<RawLogSignalConfig>,
    events: Option<RawLogSignalConfig>,
}

#[derive(Debug, serde::Deserialize)]
struct RawLogSignalConfig {
    user_prompts: Option<bool>,
    model_outputs: Option<bool>,
    tool_details: Option<bool>,
    tool_outputs: Option<bool>,
}

impl From<RawConfig> for UserConfig {
    fn from(raw: RawConfig) -> Self {
        let (agent_type, agent_product, agent_instance, username) = raw
            .ant
            .map(|a| (a.agent_type, a.agent_product, a.agent_instance, a.username))
            .unwrap_or_default();

        let otel_protocol = raw.otel_exporter.and_then(|e| e.protocol);

        let metrics_endpoint = raw.metrics_config.as_ref().and_then(|c| c.endpoint.clone());
        let metrics_headers = raw.metrics_config.and_then(|c| c.headers);
        let logs_endpoint = raw.logs_config.as_ref().and_then(|c| c.endpoint.clone());
        let logs_headers = raw.logs_config.and_then(|c| c.headers);
        let status_endpoint = raw.status_config.as_ref().and_then(|c| c.endpoint.clone());
        let status_headers = raw.status_config.and_then(|c| c.headers);
        let traces_headers = raw.traces_config.and_then(|c| c.headers);

        let log = raw.log.map(|l| LogConfig {
            user_prompts: l.user_prompts,
            model_outputs: l.model_outputs,
            tool_details: l.tool_details,
            tool_outputs: l.tool_outputs,
            spans: l.spans.map(|s| LogSignalConfig {
                user_prompts: s.user_prompts,
                model_outputs: s.model_outputs,
                tool_details: s.tool_details,
                tool_outputs: s.tool_outputs,
            }),
            events: l.events.map(|e| LogSignalConfig {
                user_prompts: e.user_prompts,
                model_outputs: e.model_outputs,
                tool_details: e.tool_details,
                tool_outputs: e.tool_outputs,
            }),
        });

        Self {
            enable_telemetry: raw.enable_telemetry,
            service_name: raw.service_name,
            agent_type,
            agent_product,
            agent_instance,
            username,
            otel_protocol,
            metrics_endpoint,
            logs_endpoint,
            status_endpoint,
            traces_headers,
            metrics_headers,
            logs_headers,
            status_headers,
            log,
        }
    }
}

/// 加载用户配置文件。
///
/// 文件不存在时不报错（返回空 `UserConfig`），解析失败时打印警告到 stderr。
pub fn load_user_config() -> Result<UserConfig, String> {
    let path = crate::common::dirs::user_config_path();

    if !path.exists() {
        return Ok(UserConfig::default());
    }

    let content = std::fs::read_to_string(&path)
        .map_err(|e| format!("Failed to read {}: {}", path.display(), e))?;

    let raw: RawConfig = serde_json::from_str(&content)
        .map_err(|e| format!("Failed to parse {}: {}", path.display(), e))?;

    Ok(raw.into())
}