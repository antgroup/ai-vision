//! Edition-aware configuration: `common`（默认） vs `aivision`（编译时内嵌 keys）。
//!
//! 设计与 `claudecode/cchooks/src/common/dirs.rs::edition_name()` 同构：
//! - **编译时切换**：通过 Cargo feature `aivision` 决定是否启用内嵌 keys
//! - **运行时优先级**：环境变量 > 用户配置文件 > 编译时内嵌默认值 > 硬编码默认值
//! - **keys/config 分离**：密钥（pk/sk/host）与非敏感配置（endpoints/headers/脱敏开关）
//!   分别从 `aivision.keys.json` 和 `configs/{edition}.config.json` 读取
//!
//! 环境变量约定（与 `AGENTS.md` Phase 0 表格一致，前缀 `KIMI_`）：
//!
//! | 用途                       | 环境变量                                       |
//! |----------------------------|------------------------------------------------|
//! | master switch              | `KIMI_ENABLE_TELEMETRY`                      |
//! | traces endpoint            | `KIMI_OTLP_TRACES_ENDPOINT`                 |
//! | metrics endpoint（业务）   | `KIMI_OTLP_METRICS_ENDPOINT`                |
//! | logs endpoint              | `KIMI_OTLP_LOGS_ENDPOINT`                   |
//! | status endpoint（心跳/up） | `KIMI_OTLP_STATUS_ENDPOINT`                 |
//! | basic auth (公钥)          | `KIMI_LANGFUSE_PUBLIC_KEY`                  |
//! | basic auth (私钥)          | `KIMI_LANGFUSE_SECRET_KEY`                  |
//! | traces 通用 headers        | `KIMI_OTLP_HEADERS`（JSON 对象）            |
//! | metrics 独立 headers       | `KIMI_OTLP_METRICS_HEADERS`（JSON 对象）    |
//! | logs 独立 headers          | `KIMI_OTLP_LOGS_HEADERS`（JSON 对象）       |
//! | status 独立 headers        | `KIMI_OTLP_STATUS_HEADERS`（JSON 对象）     |
//! | gray/prod 切换             | `KIMI_LANGFUSE_ENV` (= `gray`)              |
//!
//! 跨 agent 共用（与 cchooks/cdxhooks 对齐）：
//!
//! | 用途                       | 环境变量                                       |
//! |----------------------------|------------------------------------------------|
//! | OTel `ant.username`        | `AIVISION_ANT_USERNAME`（fallback `USER`）     |
//! | OTel `ant.agent.product`   | `AIVISION_AGENT_PRODUCT`                   |
//! | OTel `ant.agent.instance`  | `AIVISION_AGENT_INSTANCE`（fallback hostname） |
//! | OTel `ant.agent.type`      | `AIVISION_AGENT_TYPE`（默认 `kimicli`）    |
//! | TLS 校验关闭（排障）       | `AIVISION_TLS_NOVERIFY=true`                   |
//!
//! 内容采集开关（细粒度，替代旧的 `KIMIHOOKS_EMIT_TOOL_CONTENT`）：
//!
//! | 用途                       | 环境变量                                       |
//! |----------------------------|------------------------------------------------|
//! | Shorthand: spans+events    | `KIMI_LOG_TOOL_DETAILS`                      |
//! | Spans only                 | `KIMI_LOG_SPANS_TOOL_DETAILS`                |
//! | Events only                | `KIMI_LOG_EVENTS_TOOL_DETAILS`               |
//! | （其余类推：USER_PROMPTS / MODEL_OUTPUTS / TOOL_OUTPUTS） |

use std::collections::HashMap;

// ── Build-time injected env vars (see build.rs) ────────────────────

// Keys (only populated for aivision edition)
const BUILTIN_PROD_PUBLIC_KEY: &str = env!("KIMI_BUILTIN_PROD_PUBLIC_KEY");
const BUILTIN_PROD_SECRET_KEY: &str = env!("KIMI_BUILTIN_PROD_SECRET_KEY");
const BUILTIN_PROD_TRACES_ENDPOINT: &str = env!("KIMI_BUILTIN_PROD_TRACES_ENDPOINT");
const BUILTIN_GRAY_PUBLIC_KEY: &str = env!("KIMI_BUILTIN_GRAY_PUBLIC_KEY");
const BUILTIN_GRAY_SECRET_KEY: &str = env!("KIMI_BUILTIN_GRAY_SECRET_KEY");
const BUILTIN_GRAY_TRACES_ENDPOINT: &str = env!("KIMI_BUILTIN_GRAY_TRACES_ENDPOINT");

// Config (populated for both editions from configs/{edition}.config.json)
const BUILTIN_ENABLE_TELEMETRY: &str = env!("KIMI_BUILTIN_ENABLE_TELEMETRY");
const BUILTIN_SERVICE_NAME: &str = env!("KIMI_BUILTIN_SERVICE_NAME");
const BUILTIN_AGENT_TYPE: &str = env!("KIMI_BUILTIN_AGENT_TYPE");
const BUILTIN_AGENT_PRODUCT: &str = env!("KIMI_BUILTIN_AGENT_PRODUCT");
const BUILTIN_OTEL_PROTOCOL: &str = env!("KIMI_BUILTIN_OTEL_PROTOCOL");
const BUILTIN_METRICS_ENDPOINT: &str = env!("KIMI_BUILTIN_METRICS_ENDPOINT");
const BUILTIN_LOGS_ENDPOINT: &str = env!("KIMI_BUILTIN_LOGS_ENDPOINT");
const BUILTIN_STATUS_ENDPOINT: &str = env!("KIMI_BUILTIN_STATUS_ENDPOINT");
const BUILTIN_TRACES_HEADERS: &str = env!("KIMI_BUILTIN_TRACES_HEADERS");
const BUILTIN_METRICS_HEADERS: &str = env!("KIMI_BUILTIN_METRICS_HEADERS");
const BUILTIN_LOGS_HEADERS: &str = env!("KIMI_BUILTIN_LOGS_HEADERS");
const BUILTIN_STATUS_HEADERS: &str = env!("KIMI_BUILTIN_STATUS_HEADERS");
const BUILTIN_LOG_SPANS_USER_PROMPTS: &str = env!("KIMI_BUILTIN_LOG_SPANS_USER_PROMPTS");
const BUILTIN_LOG_SPANS_MODEL_OUTPUTS: &str = env!("KIMI_BUILTIN_LOG_SPANS_MODEL_OUTPUTS");
const BUILTIN_LOG_SPANS_TOOL_DETAILS: &str = env!("KIMI_BUILTIN_LOG_SPANS_TOOL_DETAILS");
const BUILTIN_LOG_SPANS_TOOL_OUTPUTS: &str = env!("KIMI_BUILTIN_LOG_SPANS_TOOL_OUTPUTS");
const BUILTIN_LOG_EVENTS_USER_PROMPTS: &str = env!("KIMI_BUILTIN_LOG_EVENTS_USER_PROMPTS");
const BUILTIN_LOG_EVENTS_MODEL_OUTPUTS: &str = env!("KIMI_BUILTIN_LOG_EVENTS_MODEL_OUTPUTS");
const BUILTIN_LOG_EVENTS_TOOL_DETAILS: &str = env!("KIMI_BUILTIN_LOG_EVENTS_TOOL_DETAILS");
const BUILTIN_LOG_EVENTS_TOOL_OUTPUTS: &str = env!("KIMI_BUILTIN_LOG_EVENTS_TOOL_OUTPUTS");

/// 默认 ant.agent.type。命中 `weaver/policies/base/value_validation.rego::valid_agent_types`。
const DEFAULT_AGENT_TYPE: &str = "kimicli";

/// 编译时确定的 edition 名称。
pub fn edition_name() -> &'static str {
    #[cfg(feature = "aivision")]
    {
        "aivision"
    }
    #[cfg(not(feature = "aivision"))]
    {
        "common"
    }
}

/// gray / prod 双环境标识（与 cchooks 一致）。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Profile {
    Prod,
    Gray,
}

impl Default for Profile {
    fn default() -> Self {
        Self::Prod
    }
}

impl Profile {
    /// 优先级：环境变量 `KIMI_LANGFUSE_ENV=gray` > 默认 prod。
    pub fn from_env() -> Self {
        match std::env::var("KIMI_LANGFUSE_ENV") {
            Ok(v) if v.eq_ignore_ascii_case("gray") => Self::Gray,
            _ => Self::Prod,
        }
    }
}

// ── EditionDefaults: non-secret, compile-time embedded config ──────

/// 编译时内嵌的非敏感配置默认值。
///
/// 来源：`configs/{edition}.config.json`，由 `build.rs` 解析后注入。
/// 运行时用户配置文件可覆盖这些值。
#[derive(Debug, Clone)]
pub struct EditionDefaults {
    pub enable_telemetry: bool,
    pub service_name: String,
    pub agent_type: String,
    pub agent_product: Option<String>,
    pub otel_protocol: String,
    pub metrics_endpoint: Option<String>,
    pub logs_endpoint: Option<String>,
    pub status_endpoint: Option<String>,
    pub traces_headers: HashMap<String, String>,
    pub metrics_headers: HashMap<String, String>,
    pub logs_headers: HashMap<String, String>,
    pub status_headers: HashMap<String, String>,
    pub log_spans_user_prompts: bool,
    pub log_spans_model_outputs: bool,
    pub log_spans_tool_details: bool,
    pub log_spans_tool_outputs: bool,
    pub log_events_user_prompts: bool,
    pub log_events_model_outputs: bool,
    pub log_events_tool_details: bool,
    pub log_events_tool_outputs: bool,
}

impl EditionDefaults {
    /// 从编译时注入的常量构建默认值。
    pub fn from_builtin() -> Self {
        Self {
            enable_telemetry: BUILTIN_ENABLE_TELEMETRY == "true",
            service_name: nonempty(BUILTIN_SERVICE_NAME.to_string())
                .unwrap_or_else(|| "kimi-cli".to_string()),
            agent_type: nonempty(BUILTIN_AGENT_TYPE.to_string())
                .unwrap_or_else(|| DEFAULT_AGENT_TYPE.to_string()),
            agent_product: nonempty(BUILTIN_AGENT_PRODUCT.to_string()),
            otel_protocol: nonempty(BUILTIN_OTEL_PROTOCOL.to_string())
                .unwrap_or_else(|| "http/json".to_string()),
            metrics_endpoint: nonempty(BUILTIN_METRICS_ENDPOINT.to_string()),
            logs_endpoint: nonempty(BUILTIN_LOGS_ENDPOINT.to_string()),
            status_endpoint: nonempty(BUILTIN_STATUS_ENDPOINT.to_string()),
            traces_headers: parse_headers_builtin(BUILTIN_TRACES_HEADERS),
            metrics_headers: parse_headers_builtin(BUILTIN_METRICS_HEADERS),
            logs_headers: parse_headers_builtin(BUILTIN_LOGS_HEADERS),
            status_headers: parse_headers_builtin(BUILTIN_STATUS_HEADERS),
            log_spans_user_prompts: BUILTIN_LOG_SPANS_USER_PROMPTS == "true",
            log_spans_model_outputs: BUILTIN_LOG_SPANS_MODEL_OUTPUTS == "true",
            log_spans_tool_details: BUILTIN_LOG_SPANS_TOOL_DETAILS == "true",
            log_spans_tool_outputs: BUILTIN_LOG_SPANS_TOOL_OUTPUTS == "true",
            log_events_user_prompts: BUILTIN_LOG_EVENTS_USER_PROMPTS == "true",
            log_events_model_outputs: BUILTIN_LOG_EVENTS_MODEL_OUTPUTS == "true",
            log_events_tool_details: BUILTIN_LOG_EVENTS_TOOL_DETAILS == "true",
            log_events_tool_outputs: BUILTIN_LOG_EVENTS_TOOL_OUTPUTS == "true",
        }
    }

    /// 从用户配置文件（`config.json`）加载覆盖。只覆盖非 None 的字段。
    pub fn merge_user_config(&mut self, user: &crate::common::config::UserConfig) {
        if let Some(v) = user.enable_telemetry {
            self.enable_telemetry = v;
        }
        if let Some(ref v) = user.service_name {
            self.service_name = v.clone();
        }
        if let Some(ref v) = user.agent_type {
            self.agent_type = v.clone();
        }
        if let Some(ref v) = user.agent_product {
            self.agent_product = Some(v.clone());
        }
        if let Some(ref v) = user.otel_protocol {
            self.otel_protocol = v.clone();
        }
        if let Some(ref v) = user.metrics_endpoint {
            self.metrics_endpoint = Some(v.clone());
        }
        if let Some(ref v) = user.logs_endpoint {
            self.logs_endpoint = Some(v.clone());
        }
        if let Some(ref v) = user.status_endpoint {
            self.status_endpoint = Some(v.clone());
        }
        if let Some(ref v) = user.traces_headers {
            self.traces_headers = v.clone();
        }
        if let Some(ref v) = user.metrics_headers {
            self.metrics_headers = v.clone();
        }
        if let Some(ref v) = user.logs_headers {
            self.logs_headers = v.clone();
        }
        if let Some(ref v) = user.status_headers {
            self.status_headers = v.clone();
        }
        // log.* — apply shorthands then specific overrides
        if let Some(ref log) = user.log {
            // Shorthand: applies to both spans and events unless overridden
            if let Some(v) = log.user_prompts {
                self.log_spans_user_prompts = v;
                self.log_events_user_prompts = v;
            }
            if let Some(v) = log.model_outputs {
                self.log_spans_model_outputs = v;
                self.log_events_model_outputs = v;
            }
            if let Some(v) = log.tool_details {
                self.log_spans_tool_details = v;
                self.log_events_tool_details = v;
            }
            if let Some(v) = log.tool_outputs {
                self.log_spans_tool_outputs = v;
                self.log_events_tool_outputs = v;
            }
            // Specific: spans overrides
            if let Some(ref spans) = log.spans {
                if let Some(v) = spans.user_prompts {
                    self.log_spans_user_prompts = v;
                }
                if let Some(v) = spans.model_outputs {
                    self.log_spans_model_outputs = v;
                }
                if let Some(v) = spans.tool_details {
                    self.log_spans_tool_details = v;
                }
                if let Some(v) = spans.tool_outputs {
                    self.log_spans_tool_outputs = v;
                }
            }
            // Specific: events overrides
            if let Some(ref events) = log.events {
                if let Some(v) = events.user_prompts {
                    self.log_events_user_prompts = v;
                }
                if let Some(v) = events.model_outputs {
                    self.log_events_model_outputs = v;
                }
                if let Some(v) = events.tool_details {
                    self.log_events_tool_details = v;
                }
                if let Some(v) = events.tool_outputs {
                    self.log_events_tool_outputs = v;
                }
            }
        }
    }
}

// ── EditionConfig: resolved runtime config (unchanged public API) ───

/// 运行时解析后的完整配置。
///
/// **解析规则（每个字段独立）**：
/// 1. 对应的 `KIMI_*` 环境变量非空 → 用环境变量值
/// 2. 否则用编译时内嵌值（`aivision` edition 才有，`common` edition 为空）
/// 3. 仍为空 → `Option::None`，调用方决定是否跳过该信号上报
#[derive(Debug, Clone, Default)]
pub struct EditionConfig {
    pub edition: &'static str,
    pub profile: Profile,
    /// `ant.agent.product`（如 `Kimi`）。来源优先级：
    /// `AIVISION_AGENT_PRODUCT` env > config.json > `None`
    pub agent_product: Option<String>,
    /// `ant.agent.type`（如 `kimicli`，必须命中 valid_agent_types 白名单）。
    /// 来源优先级：`AIVISION_AGENT_TYPE` env > config.json > `DEFAULT_AGENT_TYPE`
    pub agent_type: String,
    /// `ant.agent.instance`（如主机名）。
    /// 来源优先级：`AIVISION_AGENT_INSTANCE` env > 系统 hostname > `"unknown"`
    pub agent_instance: String,
    /// `ant.username` / `user.id`。
    /// 来源优先级：`AIVISION_ANT_USERNAME` env > `USER` env > `"unknown"`
    pub username: String,
    /// Master switch: telemetry collection enabled.
    /// 来源优先级：`KIMI_ENABLE_TELEMETRY` env > config.json > false
    pub enable_telemetry: bool,
    /// OTLP protocol (http/json, grpc, http/protobuf).
    pub otel_protocol: String,
    pub traces_endpoint: Option<String>,
    /// 业务指标 endpoint（counter / histogram，例如 `agent.tool_call.count`）
    pub metrics_endpoint: Option<String>,
    pub logs_endpoint: Option<String>,
    /// 状态/心跳指标 endpoint（例如 `aivision.agentic.up`）。可与 metrics 同址。
    pub status_endpoint: Option<String>,
    pub public_key: Option<String>,
    pub secret_key: Option<String>,
    /// traces 用的"通用 headers"。
    /// 不应包含 Authorization —— traces 的 Authorization 由 `public_key`/`secret_key` 算出。
    pub headers: HashMap<String, String>,
    /// metrics 信号独立 headers。缺省时回退 `headers`。
    pub metrics_headers: HashMap<String, String>,
    /// logs 信号独立 headers。缺省时回退 `headers`。
    pub logs_headers: HashMap<String, String>,
    /// status 信号独立 headers。缺省时回退 `metrics_headers`，再回退 `headers`。
    pub status_headers: HashMap<String, String>,
    // ── Content logging controls ──
    pub log_spans_user_prompts: bool,
    pub log_spans_model_outputs: bool,
    pub log_spans_tool_details: bool,
    pub log_spans_tool_outputs: bool,
    pub log_events_user_prompts: bool,
    pub log_events_model_outputs: bool,
    pub log_events_tool_details: bool,
    pub log_events_tool_outputs: bool,
}

impl EditionConfig {
    /// 综合「环境变量」「用户配置文件」「编译时内嵌」生成最终配置。永不 panic。
    pub fn resolve() -> Self {
        let profile = Profile::from_env();

        // ── Keys (aivision only, from aivision.keys.json) ──────────
        let (builtin_pk, builtin_sk, builtin_traces) = match profile {
            Profile::Prod => (
                BUILTIN_PROD_PUBLIC_KEY,
                BUILTIN_PROD_SECRET_KEY,
                BUILTIN_PROD_TRACES_ENDPOINT,
            ),
            Profile::Gray => (
                BUILTIN_GRAY_PUBLIC_KEY,
                BUILTIN_GRAY_SECRET_KEY,
                BUILTIN_GRAY_TRACES_ENDPOINT,
            ),
        };

        // ── Config defaults (from configs/{edition}.config.json) ───
        let mut defaults = EditionDefaults::from_builtin();

        // Merge user config file
        if let Ok(user_config) = crate::common::config::load_user_config() {
            defaults.merge_user_config(&user_config);
        }

        // ── Resolve: ENV > user config > builtin defaults > hardcoded ──
        let enable_telemetry = env_bool_or("KIMI_ENABLE_TELEMETRY", defaults.enable_telemetry);
        let agent_product = env_or_builtin("AIVISION_AGENT_PRODUCT", defaults.agent_product.as_deref().unwrap_or(""));
        let agent_type = env_or_builtin("AIVISION_AGENT_TYPE", &defaults.agent_type)
            .unwrap_or_else(|| DEFAULT_AGENT_TYPE.to_string());
        let agent_instance = resolve_agent_instance();
        let username = resolve_username();

        let traces_endpoint = env_or_builtin("KIMI_OTLP_TRACES_ENDPOINT", builtin_traces);
        let metrics_endpoint = env_or_builtin("KIMI_OTLP_METRICS_ENDPOINT", defaults.metrics_endpoint.as_deref().unwrap_or(""));
        let logs_endpoint = env_or_builtin("KIMI_OTLP_LOGS_ENDPOINT", defaults.logs_endpoint.as_deref().unwrap_or(""));
        let status_endpoint = env_or_builtin("KIMI_OTLP_STATUS_ENDPOINT", defaults.status_endpoint.as_deref().unwrap_or(""));

        let public_key = env_or_builtin("KIMI_LANGFUSE_PUBLIC_KEY", builtin_pk);
        let secret_key = env_or_builtin("KIMI_LANGFUSE_SECRET_KEY", builtin_sk);

        // Headers: env > user config > builtin
        let headers = resolve_signal_headers_with_fallback(
            "KIMI_OTLP_HEADERS",
            &defaults.traces_headers,
        );
        let metrics_headers_raw = resolve_signal_headers_with_fallback(
            "KIMI_OTLP_METRICS_HEADERS",
            &defaults.metrics_headers,
        );
        let logs_headers_raw = resolve_signal_headers_with_fallback(
            "KIMI_OTLP_LOGS_HEADERS",
            &defaults.logs_headers,
        );
        let status_headers_raw = resolve_signal_headers_with_fallback(
            "KIMI_OTLP_STATUS_HEADERS",
            &defaults.status_headers,
        );

        // Fallback chain: metrics -> traces, logs -> traces, status -> metrics -> traces
        let metrics_headers = if metrics_headers_raw.is_empty() {
            headers.clone()
        } else {
            metrics_headers_raw
        };
        let logs_headers = if logs_headers_raw.is_empty() {
            headers.clone()
        } else {
            logs_headers_raw
        };
        let status_headers = if status_headers_raw.is_empty() {
            metrics_headers.clone()
        } else {
            status_headers_raw
        };

        // Content logging: env > user config (merged into defaults) > builtin
        let log_spans_user_prompts = env_bool_or("KIMI_LOG_SPANS_USER_PROMPTS", defaults.log_spans_user_prompts);
        let log_spans_model_outputs = env_bool_or("KIMI_LOG_SPANS_MODEL_OUTPUTS", defaults.log_spans_model_outputs);
        let log_spans_tool_details = env_bool_or("KIMI_LOG_SPANS_TOOL_DETAILS", defaults.log_spans_tool_details);
        let log_spans_tool_outputs = env_bool_or("KIMI_LOG_SPANS_TOOL_OUTPUTS", defaults.log_spans_tool_outputs);
        let log_events_user_prompts = env_bool_or("KIMI_LOG_EVENTS_USER_PROMPTS", defaults.log_events_user_prompts);
        let log_events_model_outputs = env_bool_or("KIMI_LOG_EVENTS_MODEL_OUTPUTS", defaults.log_events_model_outputs);
        let log_events_tool_details = env_bool_or("KIMI_LOG_EVENTS_TOOL_DETAILS", defaults.log_events_tool_details);
        let log_events_tool_outputs = env_bool_or("KIMI_LOG_EVENTS_TOOL_OUTPUTS", defaults.log_events_tool_outputs);

        Self {
            edition: edition_name(),
            profile,
            agent_product,
            agent_type,
            agent_instance,
            username,
            enable_telemetry,
            otel_protocol: defaults.otel_protocol,
            traces_endpoint,
            metrics_endpoint,
            logs_endpoint,
            status_endpoint,
            public_key,
            secret_key,
            headers,
            metrics_headers,
            logs_headers,
            status_headers,
            log_spans_user_prompts,
            log_spans_model_outputs,
            log_spans_tool_details,
            log_spans_tool_outputs,
            log_events_user_prompts,
            log_events_model_outputs,
            log_events_tool_details,
            log_events_tool_outputs,
        }
    }

    /// 当前是否拥有有效的 traces 上报凭据（endpoint + auth 全齐）。
    pub fn can_export_traces(&self) -> bool {
        self.traces_endpoint.is_some()
            && self.public_key.is_some()
            && self.secret_key.is_some()
    }

    /// metrics 上报：要求 endpoint 存在；auth 与 traces 共用即可。
    pub fn can_export_metrics(&self) -> bool {
        self.metrics_endpoint.is_some()
    }

    /// logs 上报：要求 endpoint 存在；auth 与 traces 共用即可。
    pub fn can_export_logs(&self) -> bool {
        self.logs_endpoint.is_some()
    }

    /// 状态/心跳指标上报：要求 status endpoint 存在。
    /// 缺失时建议回退到 `metrics_endpoint`（由调用方决定）。
    pub fn can_export_status(&self) -> bool {
        self.status_endpoint.is_some()
    }

    /// 状态指标实际使用的 endpoint：优先 `status_endpoint`，回退 `metrics_endpoint`。
    pub fn effective_status_endpoint(&self) -> Option<&str> {
        self.status_endpoint
            .as_deref()
            .or(self.metrics_endpoint.as_deref())
    }

    /// 输出配置状态、计数和日志开关，避免将凭据或身份写入诊断日志。
    pub fn human_summary(&self) -> String {
        format!(
            "edition={} profile={:?} enable_telemetry={} \
             traces_configured={} metrics_configured={} logs_configured={} status_configured={} \
             public_key_configured={} secret_key_configured={} \
             headers={} metrics_headers={} logs_headers={} status_headers={} \
             log_spans=[u={},m={},t={},o={}] log_events=[u={},m={},t={},o={}]",
            self.edition,
            self.profile,
            self.enable_telemetry,
            self.traces_endpoint.is_some(),
            self.metrics_endpoint.is_some(),
            self.logs_endpoint.is_some(),
            self.status_endpoint.is_some(),
            self.public_key.is_some(),
            self.secret_key.is_some(),
            self.headers.len(),
            self.metrics_headers.len(),
            self.logs_headers.len(),
            self.status_headers.len(),
            self.log_spans_user_prompts, self.log_spans_model_outputs,
            self.log_spans_tool_details, self.log_spans_tool_outputs,
            self.log_events_user_prompts, self.log_events_model_outputs,
            self.log_events_tool_details, self.log_events_tool_outputs,
        )
    }
}

// ── helpers ────────────────────────────────────────────────────────

fn nonempty(s: String) -> Option<String> {
    if s.is_empty() {
        None
    } else {
        Some(s)
    }
}

fn env_or_builtin(env_key: &str, builtin: &str) -> Option<String> {
    if let Ok(v) = std::env::var(env_key) {
        if !v.is_empty() {
            return Some(v);
        }
    }
    nonempty(builtin.to_string())
}

/// Bool from env var, falling back to a default.
fn env_bool_or(env_key: &str, default: bool) -> bool {
    match std::env::var(env_key) {
        Ok(v) if v.eq_ignore_ascii_case("true") || v == "1" => true,
        Ok(v) if v.eq_ignore_ascii_case("false") || v == "0" => false,
        _ => default,
    }
}

/// Parse builtin headers JSON (compile-time injected).
fn parse_headers_builtin(builtin: &str) -> HashMap<String, String> {
    if builtin.is_empty() {
        return HashMap::new();
    }
    serde_json::from_str::<HashMap<String, String>>(builtin).unwrap_or_default()
}

/// 解析 OTLP 标准 header 格式：`key=value,key2=value2`。
///
/// 与 `OTEL_EXPORTER_OTLP_HEADERS` 标准语义一致（cchooks 通过
/// `agentic_otel_common::otlp::parse_otel_exporter_headers` 调用同款逻辑）。
/// 每对按**第一个** `=` 切分，允许 value 中包含 `=`（如 Base64 token）。
fn parse_otlp_headers_kv(raw: &str) -> HashMap<String, String> {
    raw.split(',')
        .filter_map(|pair| {
            let pair = pair.trim();
            let eq_pos = pair.find('=')?;
            let key = pair[..eq_pos].trim().to_string();
            let value = pair[eq_pos + 1..].trim().to_string();
            if key.is_empty() {
                return None;
            }
            Some((key, value))
        })
        .collect()
}

/// 合并某个信号的「编译时/用户配置 headers」+「环境变量 headers」。
/// 环境变量优先级更高（同 key 覆盖）。
///
/// 格式：OTLP 标准 `key=value,key2=value2`（与 cchooks `CCHOOKS_OTEL_EXPORTER_OTLP_HEADERS` 对齐）。
fn resolve_signal_headers_with_fallback(
    env_key: &str,
    defaults: &HashMap<String, String>,
) -> HashMap<String, String> {
    let mut out: HashMap<String, String> = defaults.clone();
    if let Ok(env_headers) = std::env::var(env_key) {
        let env_headers = env_headers.trim();
        if !env_headers.is_empty() {
            out.extend(parse_otlp_headers_kv(env_headers));
        }
    }
    out
}

/// 解析 `ant.username`：`AIVISION_ANT_USERNAME` > `USER` > `"unknown"`。
/// 与 cchooks `resolve_username()` 同源（cchooks/main.rs:260）。
fn resolve_username() -> String {
    if let Ok(v) = std::env::var("AIVISION_ANT_USERNAME") {
        if !v.is_empty() {
            return v;
        }
    }
    if let Ok(v) = std::env::var("USER") {
        if !v.is_empty() {
            return v;
        }
    }
    "unknown".to_string()
}

/// 解析 `ant.agent.instance`：`AIVISION_AGENT_INSTANCE` > 系统 hostname > `"unknown"`。
fn resolve_agent_instance() -> String {
    if let Ok(v) = std::env::var("AIVISION_AGENT_INSTANCE") {
        if !v.is_empty() {
            return v;
        }
    }
    if let Ok(v) = agentic_otel_common::hostname_from_system() {
        if !v.is_empty() {
            return v;
        }
    }
    if let Ok(v) = std::env::var("HOSTNAME") {
        if !v.is_empty() {
            return v;
        }
    }
    "unknown".to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn human_summary_uses_configuration_status() {
        let config = EditionConfig {
            edition: "common",
            username: "fixture-user".into(),
            agent_instance: "fixture-host".into(),
            traces_endpoint: Some("https://collector.example/v1/traces".into()),
            public_key: Some("fixture-public-key".into()),
            secret_key: Some("fixture-secret-key".into()),
            ..EditionConfig::default()
        };
        let summary = config.human_summary();
        assert!(summary.contains("edition=common"));
        assert!(summary.contains("traces_configured=true"));
        assert!(summary.contains("metrics_configured=false"));
        assert!(summary.contains("public_key_configured=true"));
        assert!(summary.contains("secret_key_configured=true"));
        assert!(!summary.contains("fixture-"));
        assert!(!summary.contains("collector.example"));
    }

    #[test]
    fn edition_name_known() {
        assert!(matches!(edition_name(), "aivision" | "common"));
    }

    #[test]
    fn common_edition_has_no_builtin_keys() {
        #[cfg(not(feature = "aivision"))]
        {
            assert!(BUILTIN_PROD_TRACES_ENDPOINT.is_empty());
            assert!(BUILTIN_PROD_PUBLIC_KEY.is_empty());
        }
    }

    #[test]
    fn aivision_edition_has_builtin_keys() {
        #[cfg(feature = "aivision")]
        {
            assert!(!BUILTIN_PROD_TRACES_ENDPOINT.is_empty());
            assert!(!BUILTIN_PROD_PUBLIC_KEY.is_empty());
        }
    }

    #[test]
    fn env_var_overrides_builtin() {
        let key = "KIMI_OTLP_TRACES_ENDPOINT";
        let prev = std::env::var(key).ok();
        std::env::set_var(key, "https://override.example/v1/traces");
        let cfg = EditionConfig::resolve();
        assert_eq!(
            cfg.traces_endpoint.as_deref(),
            Some("https://override.example/v1/traces")
        );
        match prev {
            Some(v) => std::env::set_var(key, v),
            None => std::env::remove_var(key),
        }
    }

    #[test]
    fn resolve_handles_empty_env() {
        let key = "KIMI_OTLP_METRICS_ENDPOINT";
        let prev = std::env::var(key).ok();
        std::env::set_var(key, "");
        let cfg = EditionConfig::resolve();
        if cfg.metrics_endpoint.is_some() {
            assert!(!cfg.metrics_endpoint.as_ref().unwrap().is_empty());
        }
        match prev {
            Some(v) => std::env::set_var(key, v),
            None => std::env::remove_var(key),
        }
    }

    #[test]
    fn profile_from_env_default_prod() {
        let key = "KIMI_LANGFUSE_ENV";
        let prev = std::env::var(key).ok();
        std::env::set_var(key, "prod");
        assert_eq!(Profile::from_env(), Profile::Prod);
        match prev {
            Some(v) => std::env::set_var(key, v),
            None => std::env::remove_var(key),
        }
    }

    #[test]
    fn profile_from_env_gray() {
        let key = "KIMI_LANGFUSE_ENV";
        let prev = std::env::var(key).ok();
        std::env::set_var(key, "gray");
        assert_eq!(Profile::from_env(), Profile::Gray);
        match prev {
            Some(v) => std::env::set_var(key, v),
            None => std::env::remove_var(key),
        }
    }

    #[test]
    fn env_bool_or_works() {
        let key = "KIMI_TEST_BOOL";
        let prev = std::env::var(key).ok();
        assert!(!env_bool_or(key, false));
        assert!(env_bool_or(key, true));
        std::env::set_var(key, "true");
        assert!(env_bool_or(key, false));
        std::env::set_var(key, "1");
        assert!(env_bool_or(key, false));
        std::env::set_var(key, "false");
        assert!(!env_bool_or(key, true));
        std::env::set_var(key, "0");
        assert!(!env_bool_or(key, true));
        match prev {
            Some(v) => std::env::set_var(key, v),
            None => std::env::remove_var(key),
        }
    }

    #[test]
    fn defaults_from_builtin() {
        let defaults = EditionDefaults::from_builtin();
        // Common edition: enable_telemetry should be false per common.config.json
        // Aivision edition: enable_telemetry should be true per aivision.config.json
        #[cfg(not(feature = "aivision"))]
        assert!(!defaults.enable_telemetry);
        #[cfg(feature = "aivision")]
        assert!(defaults.enable_telemetry);
    }
}
