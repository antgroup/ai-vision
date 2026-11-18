//! Local OTLP HTTP validation proxy and dashboard.
//!
//! `weaver-guard serve` accepts OTLP/HTTP JSON or protobuf requests, validates
//! them with the same policy/semconv pipeline used by replay validation, writes
//! local diagnostics, starts a local VictoriaMetrics/VictoriaLogs plus
//! Jaeger all-in-one stack, and forwards accepted telemetry to those local
//! OTLP ingest endpoints by default.

use crate::config::FailOn;
use crate::error::WeaverGuardError;
use crate::jsonl::{self, OtelSignal, SignalType};
use crate::report::ValidationReport;
use crate::semconv::{self, SemconvFinding, SemconvRegistry};
use crate::validation::{ValidationEngine, ValidationFinding};
use axum::body::{to_bytes, Body};
use axum::extract::{Path as AxumPath, State};
use axum::http::{header, HeaderMap, Request, StatusCode};
use axum::response::{Html, IntoResponse, Response};
use axum::routing::{any, get, post};
use axum::{Json, Router};
use bytes::Bytes;
use chrono::Utc;
use flate2::read::GzDecoder;
use opentelemetry_proto::tonic::collector::logs::v1::{
    ExportLogsPartialSuccess, ExportLogsServiceRequest, ExportLogsServiceResponse,
};
use opentelemetry_proto::tonic::collector::metrics::v1::{
    ExportMetricsPartialSuccess, ExportMetricsServiceRequest, ExportMetricsServiceResponse,
};
use opentelemetry_proto::tonic::collector::trace::v1::{
    ExportTracePartialSuccess, ExportTraceServiceRequest, ExportTraceServiceResponse,
};
use prost::Message;
use prost_types::Any;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::{BTreeMap, HashSet, VecDeque};
use std::fs::{self, OpenOptions};
use std::io::{Read, Write};
use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;
use tracing::{error, info, warn};

mod dashboard;

const DEFAULT_RECENT_LIMIT: usize = 500;
const DEFAULT_VIOLATION_LIMIT: usize = 10;
const MAX_VIOLATION_LIMIT: usize = 100;
const DEFAULT_FORWARD_TIMEOUT_SECS: u64 = 5;
const VICTORIA_READY_TIMEOUT_SECS: u64 = 30;
const DEFAULT_VICTORIA_METRICS_ADDR: &str = "127.0.0.1:8428";
const DEFAULT_VICTORIA_LOGS_ADDR: &str = "127.0.0.1:9428";
const DEFAULT_JAEGER_UI_ADDR: &str = "127.0.0.1:16686";
const DEFAULT_JAEGER_OTLP_HTTP_ADDR: &str = "127.0.0.1:14318";
const DEFAULT_JAEGER_OTLP_GRPC_ADDR: &str = "127.0.0.1:14317";
const JAEGER_LIGHT_THEME_INJECTION: &str = r#"<script data-weaver-jaeger-theme>
try { window.localStorage.setItem('jaeger-ui-theme', 'light'); } catch (_) {}
</script>
<style data-weaver-jaeger-theme>
:root { color-scheme: light; }
body { background: #fff !important; }
.Page--topNav,
.Page--topNav .ant-menu,
.Page--topNav .ant-menu-root,
.Page--topNav .ant-menu-horizontal,
.Page--topNav .ant-menu-item,
.Page--topNav .ant-menu-submenu-title {
  background: #fff !important;
  color: rgba(0, 0, 0, .88) !important;
}
.Page--topNav .ant-menu-item-selected,
.Page--topNav .ant-menu-item-active {
  background: #22b8bd !important;
  color: #fff !important;
}
.Page--topNav .ant-menu-item-selected .ant-menu-title-content,
.Page--topNav .ant-menu-item-active .ant-menu-title-content {
  color: #fff !important;
}
.Menu--item .ant-menu-title-content,
.Dropdown--icon-container,
.Dropdown--icon-container:hover,
.ThemeToggleButton {
  color: rgba(0, 0, 0, .88) !important;
}
.Page--topNav .ant-input-affix-wrapper {
  background: #fff !important;
  border-color: #d9d9d9 !important;
  color: rgba(0, 0, 0, .88) !important;
}
.Page--topNav input {
  color: rgba(0, 0, 0, .88) !important;
}
</style>
"#;

/// Forwarding policy for accepted/diagnosed requests.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[cfg_attr(feature = "cli", derive(clap::ValueEnum))]
pub enum ForwardPolicy {
    /// Forward only requests with zero findings.
    PassOnly,
    /// Forward accepted requests even if they only have warnings/improvements.
    PassOrImprovement,
    /// Forward regardless of validation result.
    Always,
    /// Never forward.
    Never,
}

impl ForwardPolicy {
    fn as_kebab(self) -> &'static str {
        match self {
            ForwardPolicy::PassOnly => "pass-only",
            ForwardPolicy::PassOrImprovement => "pass-or-improvement",
            ForwardPolicy::Always => "always",
            ForwardPolicy::Never => "never",
        }
    }

    fn label(self) -> &'static str {
        match self {
            ForwardPolicy::PassOnly => "Pass only",
            ForwardPolicy::PassOrImprovement => "Pass or improvement",
            ForwardPolicy::Always => "Always forward",
            ForwardPolicy::Never => "Never forward",
        }
    }

    fn from_kebab(value: &str) -> Option<Self> {
        match value.trim().to_ascii_lowercase().as_str() {
            "pass-only" | "pass_only" | "passonly" => Some(ForwardPolicy::PassOnly),
            "pass-or-improvement" | "pass_or_improvement" | "passorimprovement" => {
                Some(ForwardPolicy::PassOrImprovement)
            }
            "always" | "always-forward" | "always_forward" => Some(ForwardPolicy::Always),
            "never" | "never-forward" | "never_forward" => Some(ForwardPolicy::Never),
            _ => None,
        }
    }

    fn all_for_api() -> [serde_json::Value; 4] {
        [
            json!({
                "value": ForwardPolicy::Always.as_kebab(),
                "label": ForwardPolicy::Always.label(),
                "description": "默认：不管是否有 violation / improvement，都写入 Victoria endpoint。"
            }),
            json!({
                "value": ForwardPolicy::PassOrImprovement.as_kebab(),
                "label": ForwardPolicy::PassOrImprovement.label(),
                "description": "仅当没有 rejected violation 时写入；warning/improvement 仍会写入。"
            }),
            json!({
                "value": ForwardPolicy::PassOnly.as_kebab(),
                "label": ForwardPolicy::PassOnly.label(),
                "description": "只有完全通过且没有 finding 时才写入。"
            }),
            json!({
                "value": ForwardPolicy::Never.as_kebab(),
                "label": ForwardPolicy::Never.label(),
                "description": "只做本地校验和诊断，不写入后端。"
            }),
        ]
    }
}

/// Configuration for `weaver-guard serve`.
#[derive(Debug, Clone)]
pub struct ServeConfig {
    pub listen: SocketAddr,
    pub policies_dir: Option<PathBuf>,
    pub semconv_dirs: Vec<PathBuf>,
    pub victoria_home: PathBuf,
    pub victoria_download_dir: Option<PathBuf>,
    pub victoria_script: PathBuf,
    pub victoria_metrics_addr: String,
    pub victoria_logs_addr: String,
    pub victoria_metrics_ui: Option<String>,
    pub victoria_logs_ui: Option<String>,
    pub jaeger_home: PathBuf,
    pub jaeger_download_dir: Option<PathBuf>,
    pub jaeger_script: PathBuf,
    pub jaeger_ui_addr: String,
    pub jaeger_otlp_http_addr: String,
    pub jaeger_otlp_grpc_addr: String,
    pub jaeger_ui: Option<String>,
    pub diagnostic_log: Option<PathBuf>,
    pub replay_dir: Option<PathBuf>,
    pub forward_policy: ForwardPolicy,
    pub fail_on: FailOn,
    pub max_body_bytes: usize,
    pub recent_limit: usize,
}

#[derive(Debug, Clone)]
struct VictoriaConfig {
    home: PathBuf,
    download_dir: Option<PathBuf>,
    script: PathBuf,
    metrics_addr: String,
    logs_addr: String,
}

#[derive(Debug, Clone)]
struct VictoriaEndpoints {
    metrics_endpoint: Option<String>,
    logs_endpoint: Option<String>,
    metrics_ui: String,
    logs_ui: String,
}

#[derive(Debug, Clone)]
struct JaegerConfig {
    home: PathBuf,
    download_dir: Option<PathBuf>,
    script: PathBuf,
    ui_addr: String,
    otlp_http_addr: String,
    otlp_grpc_addr: String,
}

#[derive(Debug, Clone)]
struct JaegerEndpoints {
    traces_endpoint: Option<String>,
    ui: String,
}

struct VictoriaChild {
    name: &'static str,
    child: Child,
    pid_file: PathBuf,
}

struct VictoriaStack {
    children: Vec<VictoriaChild>,
}

struct JaegerStack {
    config: JaegerConfig,
    running: bool,
}

impl VictoriaConfig {
    fn from_serve_config(config: &ServeConfig) -> Self {
        Self {
            home: config.victoria_home.clone(),
            download_dir: config.victoria_download_dir.clone(),
            script: config.victoria_script.clone(),
            metrics_addr: non_empty_or_default(
                &config.victoria_metrics_addr,
                DEFAULT_VICTORIA_METRICS_ADDR,
            ),
            logs_addr: non_empty_or_default(&config.victoria_logs_addr, DEFAULT_VICTORIA_LOGS_ADDR),
        }
    }
}

impl VictoriaEndpoints {
    fn from_config(config: &VictoriaConfig) -> Self {
        let metrics_origin = http_origin_for_addr(&config.metrics_addr);
        let logs_origin = http_origin_for_addr(&config.logs_addr);
        Self {
            metrics_endpoint: Some(format!("{metrics_origin}/opentelemetry/v1/metrics")),
            logs_endpoint: Some(format!("{logs_origin}/insert/opentelemetry/v1/logs")),
            metrics_ui: format!("{metrics_origin}/vmui/"),
            logs_ui: format!("{logs_origin}/select/vmui/"),
        }
    }

    fn health_urls(config: &VictoriaConfig) -> [(&'static str, String); 2] {
        [
            (
                "metrics",
                format!("{}/health", http_origin_for_addr(&config.metrics_addr)),
            ),
            (
                "logs",
                format!("{}/health", http_origin_for_addr(&config.logs_addr)),
            ),
        ]
    }
}

impl JaegerConfig {
    fn from_serve_config(config: &ServeConfig) -> Self {
        Self {
            home: config.jaeger_home.clone(),
            download_dir: config.jaeger_download_dir.clone(),
            script: config.jaeger_script.clone(),
            ui_addr: non_empty_or_default(&config.jaeger_ui_addr, DEFAULT_JAEGER_UI_ADDR),
            otlp_http_addr: non_empty_or_default(
                &config.jaeger_otlp_http_addr,
                DEFAULT_JAEGER_OTLP_HTTP_ADDR,
            ),
            otlp_grpc_addr: non_empty_or_default(
                &config.jaeger_otlp_grpc_addr,
                DEFAULT_JAEGER_OTLP_GRPC_ADDR,
            ),
        }
    }
}

impl JaegerEndpoints {
    fn from_config(config: &JaegerConfig) -> Self {
        let otlp_http_origin = http_origin_for_addr(&config.otlp_http_addr);
        let ui_origin = http_origin_for_addr(&config.ui_addr);
        Self {
            traces_endpoint: Some(format!("{otlp_http_origin}/v1/traces")),
            ui: format!("{ui_origin}/jaeger/"),
        }
    }

    fn health_url(config: &JaegerConfig) -> String {
        format!("{}/jaeger/", http_origin_for_addr(&config.ui_addr))
    }
}

impl VictoriaStack {
    async fn start(config: &VictoriaConfig) -> Result<Self, WeaverGuardError> {
        require_macos_arm64("VictoriaMetrics/VictoriaLogs")?;
        run_victoria_installer(config)?;
        let mut stack = Self {
            children: vec![
                spawn_victoria_component(
                    config,
                    "metrics",
                    "victoria-metrics-prod",
                    &config.metrics_addr,
                )?,
                spawn_victoria_component(config, "logs", "victoria-logs-prod", &config.logs_addr)?,
            ],
        };
        stack.wait_ready(config).await?;
        Ok(stack)
    }

    async fn wait_ready(&mut self, config: &VictoriaConfig) -> Result<(), WeaverGuardError> {
        let client = reqwest::Client::builder()
            .timeout(Duration::from_secs(2))
            .build()
            .map_err(|e| {
                WeaverGuardError::Config(format!("failed to build Victoria health client: {e}"))
            })?;
        let deadline =
            tokio::time::Instant::now() + Duration::from_secs(VICTORIA_READY_TIMEOUT_SECS);
        loop {
            self.ensure_children_running()?;
            let mut all_ready = true;
            for (name, url) in VictoriaEndpoints::health_urls(config) {
                match client.get(&url).send().await {
                    Ok(resp) if resp.status().is_success() => {}
                    _ => {
                        all_ready = false;
                        tracing::debug!(component = name, url, "Victoria component not ready yet");
                    }
                }
            }
            if all_ready {
                info!("Victoria local stack is ready");
                return Ok(());
            }
            if tokio::time::Instant::now() >= deadline {
                return Err(WeaverGuardError::Config(format!(
                    "Victoria local stack did not become healthy within {VICTORIA_READY_TIMEOUT_SECS}s"
                )));
            }
            tokio::time::sleep(Duration::from_millis(250)).await;
        }
    }

    fn ensure_children_running(&mut self) -> Result<(), WeaverGuardError> {
        for child in &mut self.children {
            if let Some(status) = child.child.try_wait()? {
                return Err(WeaverGuardError::Config(format!(
                    "Victoria {} child exited before becoming healthy: {status}",
                    child.name
                )));
            }
        }
        Ok(())
    }

    fn stop(&mut self) {
        for child in self.children.iter_mut().rev() {
            if let Ok(None) = child.child.try_wait() {
                info!(component = child.name, "stopping Victoria child");
                terminate_child(&mut child.child);
            } else {
                let _ = child.child.wait();
            }
            let _ = fs::remove_file(&child.pid_file);
        }
    }
}

impl Drop for VictoriaStack {
    fn drop(&mut self) {
        self.stop();
    }
}

impl JaegerStack {
    async fn start(config: &JaegerConfig) -> Result<Self, WeaverGuardError> {
        require_macos_arm64("Jaeger all-in-one")?;
        run_jaeger_installer(config)?;
        let mut stack = Self {
            config: config.clone(),
            running: false,
        };
        run_jaeger_command(config, "start")?;
        stack.running = true;
        stack.wait_ready(config).await?;
        Ok(stack)
    }

    async fn wait_ready(&mut self, config: &JaegerConfig) -> Result<(), WeaverGuardError> {
        let client = reqwest::Client::builder()
            .timeout(Duration::from_secs(2))
            .build()
            .map_err(|e| {
                WeaverGuardError::Config(format!("failed to build Jaeger health client: {e}"))
            })?;
        let deadline =
            tokio::time::Instant::now() + Duration::from_secs(VICTORIA_READY_TIMEOUT_SECS);
        let url = JaegerEndpoints::health_url(config);
        loop {
            self.ensure_running()?;
            match client.get(&url).send().await {
                Ok(resp) if resp.status().is_success() => {
                    info!("Jaeger all-in-one is ready");
                    return Ok(());
                }
                _ => tracing::debug!(url, "Jaeger all-in-one not ready yet"),
            }
            if tokio::time::Instant::now() >= deadline {
                return Err(WeaverGuardError::Config(format!(
                    "Jaeger all-in-one did not become healthy within {VICTORIA_READY_TIMEOUT_SECS}s"
                )));
            }
            tokio::time::sleep(Duration::from_millis(250)).await;
        }
    }

    fn ensure_running(&mut self) -> Result<(), WeaverGuardError> {
        if self.running && !jaeger_pid_is_running(&self.config) {
            return Err(WeaverGuardError::Config(
                "Jaeger all-in-one exited before becoming healthy".to_string(),
            ));
        }
        Ok(())
    }

    fn stop(&mut self) {
        if self.running {
            info!("stopping Jaeger all-in-one child");
            if let Err(err) = run_jaeger_command(&self.config, "stop") {
                warn!(error = %err, "failed to stop Jaeger all-in-one via script");
            }
        }
        self.running = false;
    }
}

impl Drop for JaegerStack {
    fn drop(&mut self) {
        self.stop();
    }
}

fn non_empty_or_default(value: &str, default: &str) -> String {
    let trimmed = value.trim();
    if trimmed.is_empty() {
        default.to_string()
    } else {
        trimmed.to_string()
    }
}

fn http_origin_for_addr(addr: &str) -> String {
    let trimmed = addr.trim().trim_end_matches('/');
    if trimmed.starts_with("http://") || trimmed.starts_with("https://") {
        trimmed.to_string()
    } else {
        format!("http://{trimmed}")
    }
}

fn require_macos_arm64(component: &str) -> Result<(), WeaverGuardError> {
    if std::env::consts::OS == "macos" && std::env::consts::ARCH == "aarch64" {
        return Ok(());
    }
    Err(WeaverGuardError::Config(format!(
        "embedded {component} serve mode only supports macOS arm64; current platform is {}/{}",
        std::env::consts::OS,
        std::env::consts::ARCH
    )))
}

fn run_victoria_installer(config: &VictoriaConfig) -> Result<(), WeaverGuardError> {
    require_macos_arm64("VictoriaMetrics/VictoriaLogs")?;
    info!(
        script = %config.script.display(),
        home = %config.home.display(),
        "installing local Victoria binaries"
    );
    for component in ["metrics", "logs"] {
        let mut command = Command::new(&config.script);
        command
            .arg("install")
            .arg(component)
            .env("VICTORIA_HOME", &config.home);
        if let Some(download_dir) = &config.download_dir {
            command.env("VICTORIA_DOWNLOAD_DIR", download_dir);
        }
        let status = command.status().map_err(|e| {
            WeaverGuardError::Config(format!(
                "failed to run Victoria installer {} for {component}: {e}",
                config.script.display()
            ))
        })?;
        if !status.success() {
            return Err(WeaverGuardError::Config(format!(
                "Victoria installer {} for {component} failed with {status}",
                config.script.display()
            )));
        }
    }
    Ok(())
}

fn run_jaeger_installer(config: &JaegerConfig) -> Result<(), WeaverGuardError> {
    require_macos_arm64("Jaeger all-in-one")?;
    info!(
        script = %config.script.display(),
        home = %config.home.display(),
        "installing local Jaeger all-in-one binary"
    );
    run_jaeger_command(config, "install")
}

fn run_jaeger_command(config: &JaegerConfig, command: &str) -> Result<(), WeaverGuardError> {
    let mut process = Command::new(&config.script);
    process
        .arg(command)
        .env("JAEGER_HOME", &config.home)
        .env("JAEGER_UI_ADDR", &config.ui_addr)
        .env("JAEGER_OTLP_HTTP_ADDR", &config.otlp_http_addr)
        .env("JAEGER_OTLP_GRPC_ADDR", &config.otlp_grpc_addr);
    if let Some(download_dir) = &config.download_dir {
        process.env("JAEGER_DOWNLOAD_DIR", download_dir);
    }
    let status = process.status().map_err(|e| {
        WeaverGuardError::Config(format!(
            "failed to run Jaeger script {} {command}: {e}",
            config.script.display(),
        ))
    })?;
    if status.success() {
        Ok(())
    } else {
        Err(WeaverGuardError::Config(format!(
            "Jaeger script {} {command} failed with {status}",
            config.script.display(),
        )))
    }
}

fn jaeger_pid_is_running(config: &JaegerConfig) -> bool {
    let pid_path = config.home.join("pids").join("jaeger-all-in-one.pid");
    let Ok(pid) = fs::read_to_string(pid_path) else {
        return false;
    };
    let pid = pid.trim();
    if pid.is_empty() || !pid.chars().all(|ch| ch.is_ascii_digit()) {
        return false;
    }
    Command::new("kill")
        .arg("-0")
        .arg(pid)
        .status()
        .is_ok_and(|status| status.success())
}

fn terminate_child(child: &mut Child) {
    let pid = child.id();
    #[cfg(unix)]
    {
        let _ = Command::new("kill")
            .arg("-TERM")
            .arg(pid.to_string())
            .status();
        for _ in 0..30 {
            match child.try_wait() {
                Ok(Some(_)) => return,
                Ok(None) => std::thread::sleep(Duration::from_millis(100)),
                Err(_) => return,
            }
        }
    }
    let _ = child.kill();
    let _ = child.wait();
}

fn spawn_victoria_component(
    config: &VictoriaConfig,
    name: &'static str,
    binary_name: &str,
    addr: &str,
) -> Result<VictoriaChild, WeaverGuardError> {
    let bin = config.home.join("bin").join(binary_name);
    let data_dir = config.home.join("data").join(name);
    let logs_dir = config.home.join("logs");
    let pid_dir = config.home.join("pids");
    fs::create_dir_all(&data_dir)?;
    fs::create_dir_all(&logs_dir)?;
    fs::create_dir_all(&pid_dir)?;
    let log_path = logs_dir.join(format!("{name}.serve.log"));
    let pid_file = pid_dir.join(format!("{name}.pid"));
    let log = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&log_path)?;
    let stdout = log.try_clone()?;
    info!(
        component = name,
        binary = %bin.display(),
        addr,
        log = %log_path.display(),
        "starting Victoria child"
    );
    let child = Command::new(&bin)
        .arg(format!("-httpListenAddr={addr}"))
        .arg(format!("-storageDataPath={}", data_dir.display()))
        .stdout(Stdio::from(stdout))
        .stderr(Stdio::from(log))
        .spawn()
        .map_err(|e| {
            WeaverGuardError::Config(format!(
                "failed to spawn Victoria {name} binary {}: {e}",
                bin.display()
            ))
        })?;
    if let Err(err) = fs::write(&pid_file, format!("{}\n", child.id())) {
        let mut child = child;
        terminate_child(&mut child);
        return Err(WeaverGuardError::Io(err));
    }
    Ok(VictoriaChild {
        name,
        child,
        pid_file,
    })
}

/// Start the local OTLP validation service.
pub async fn run(config: ServeConfig) -> Result<(), WeaverGuardError> {
    let listener = tokio::net::TcpListener::bind(config.listen)
        .await
        .map_err(WeaverGuardError::Io)?;
    let policy_rules = load_policy_rules(config.policies_dir.as_deref())?;
    let victoria_config = VictoriaConfig::from_serve_config(&config);
    let mut victoria_stack = VictoriaStack::start(&victoria_config).await?;
    let victoria_endpoints = VictoriaEndpoints::from_config(&victoria_config);
    let jaeger_config = JaegerConfig::from_serve_config(&config);
    let mut jaeger_stack = JaegerStack::start(&jaeger_config).await?;
    let jaeger_endpoints = JaegerEndpoints::from_config(&jaeger_config);

    let registry = if !config.semconv_dirs.is_empty() {
        Some(semconv::load_semconv_registry_from_dirs(
            &config.semconv_dirs,
        )?)
    } else {
        None
    };

    let state = Arc::new(AppState {
        policies_dir: config.policies_dir.clone(),
        semconv_registry: registry,
        policy_rules,
        disabled_rules: Mutex::new(HashSet::new()),
        disabled_policy_groups: Mutex::new(HashSet::new()),
        disabled_policy_sections: Mutex::new(HashSet::new()),
        victoria_metrics_endpoint: victoria_endpoints.metrics_endpoint,
        victoria_logs_endpoint: victoria_endpoints.logs_endpoint,
        victoria_metrics_ui: config
            .victoria_metrics_ui
            .clone()
            .or(Some(victoria_endpoints.metrics_ui)),
        victoria_logs_ui: config
            .victoria_logs_ui
            .clone()
            .or(Some(victoria_endpoints.logs_ui)),
        victoria_home: victoria_config.home.clone(),
        jaeger_traces_endpoint: jaeger_endpoints.traces_endpoint,
        jaeger_ui: config.jaeger_ui.clone().or(Some(jaeger_endpoints.ui)),
        jaeger_home: jaeger_config.home.clone(),
        diagnostic_log: config.diagnostic_log.clone(),
        replay_dir: config.replay_dir.clone(),
        forward_policy: Mutex::new(config.forward_policy),
        fail_on: config.fail_on,
        max_body_bytes: config.max_body_bytes,
        recent: Mutex::new(VecDeque::with_capacity(
            config.recent_limit.min(DEFAULT_RECENT_LIMIT),
        )),
        recent_limit: config.recent_limit.max(1),
        sequence: AtomicU64::new(1),
        http_client: reqwest::Client::builder()
            .timeout(Duration::from_secs(DEFAULT_FORWARD_TIMEOUT_SECS))
            .build()
            .map_err(|e| WeaverGuardError::Config(format!("failed to build HTTP client: {e}")))?,
    });

    let app = Router::new()
        .route("/v1/traces", post(handle_traces))
        .route("/v1/metrics", post(handle_metrics))
        .route("/v1/logs", post(handle_logs))
        .route("/traces", post(handle_traces))
        .route("/metrics", post(handle_metrics))
        .route("/logs", post(handle_logs))
        .route("/healthz", get(healthz))
        .route("/dashboard", get(dashboard_page))
        .route("/dashboard/assets/dashboard.css", get(dashboard_css))
        .route("/dashboard/assets/dashboard.js", get(dashboard_js))
        .route("/jaeger/", any(proxy_jaeger_root))
        .route("/jaeger/{*path}", any(proxy_jaeger))
        .route("/victoria/{backend}/{*path}", any(proxy_victoria))
        .route("/", get(dashboard_page))
        .route("/api/recent", get(api_recent))
        .route("/api/payload/{request_id}", get(api_payload))
        .route("/api/violations", get(api_violations))
        .route("/api/voilations", get(api_violations))
        .route("/api/clear", post(api_clear_data).delete(api_clear_data))
        .route("/api/stats", get(api_stats))
        .route("/api/settings", get(api_settings).post(api_update_settings))
        .route("/api/rules", get(api_rules).post(api_update_rule))
        .with_state(state);

    info!(listen = %config.listen, "AiVision Weaver Guard started");
    info!("Dashboard:  http://{}/dashboard", config.listen);
    info!(
        "OTLP HTTP:  http://{}/v1/traces | /v1/logs | /v1/metrics",
        config.listen
    );
    axum::serve(listener, app)
        .with_graceful_shutdown(shutdown_signal())
        .await
        .map_err(|e| WeaverGuardError::Anyhow(e.into()))?;
    jaeger_stack.stop();
    victoria_stack.stop();
    Ok(())
}

async fn shutdown_signal() {
    let ctrl_c = async {
        if let Err(err) = tokio::signal::ctrl_c().await {
            warn!(error = %err, "failed to install Ctrl-C handler");
        }
    };

    #[cfg(unix)]
    let terminate = async {
        match tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate()) {
            Ok(mut signal) => {
                signal.recv().await;
            }
            Err(err) => warn!(error = %err, "failed to install SIGTERM handler"),
        }
    };

    #[cfg(not(unix))]
    let terminate = std::future::pending::<()>();

    tokio::select! {
        _ = ctrl_c => {},
        _ = terminate => {},
    }
}

struct AppState {
    policies_dir: Option<PathBuf>,
    semconv_registry: Option<SemconvRegistry>,
    policy_rules: Vec<PolicyRule>,
    disabled_rules: Mutex<HashSet<String>>,
    disabled_policy_groups: Mutex<HashSet<String>>,
    disabled_policy_sections: Mutex<HashSet<String>>,
    victoria_metrics_endpoint: Option<String>,
    victoria_logs_endpoint: Option<String>,
    victoria_metrics_ui: Option<String>,
    victoria_logs_ui: Option<String>,
    victoria_home: PathBuf,
    jaeger_traces_endpoint: Option<String>,
    jaeger_ui: Option<String>,
    jaeger_home: PathBuf,
    diagnostic_log: Option<PathBuf>,
    replay_dir: Option<PathBuf>,
    forward_policy: Mutex<ForwardPolicy>,
    fail_on: FailOn,
    max_body_bytes: usize,
    recent: Mutex<VecDeque<DiagnosticRecord>>,
    recent_limit: usize,
    sequence: AtomicU64,
    http_client: reqwest::Client,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
enum SignalKind {
    Traces,
    Metrics,
    Logs,
}

impl SignalKind {
    fn signal_type(self) -> SignalType {
        match self {
            SignalKind::Traces => SignalType::Traces,
            SignalKind::Metrics => SignalType::Metrics,
            SignalKind::Logs => SignalType::Logs,
        }
    }

    fn signal_name(self) -> &'static str {
        match self {
            SignalKind::Traces => "traces",
            SignalKind::Metrics => "metrics",
            SignalKind::Logs => "logs",
        }
    }

    fn expected_key(self) -> &'static str {
        match self {
            SignalKind::Traces => "resourceSpans",
            SignalKind::Metrics => "resourceMetrics",
            SignalKind::Logs => "resourceLogs",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum OtlpEncoding {
    Json,
    Protobuf,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DiagnosticRecord {
    pub timestamp: String,
    pub request_id: String,
    pub path: String,
    pub method: String,
    pub signal: String,
    pub content_type: String,
    pub agent: Option<String>,
    pub source: Option<String>,
    pub sample_count: usize,
    pub rejected_count: usize,
    pub violations: usize,
    pub improvements: usize,
    pub semconv_issues: usize,
    pub status: String,
    pub http_status: u16,
    pub findings: Vec<FindingSummary>,
    /// Legacy primary forwarding decision. Kept for existing dashboard/log consumers.
    pub forward: ForwardDiagnostic,
    /// Per-backend forwarding decisions. Includes skipped/blocked entries so
    /// "unset endpoint" and "policy blocked" never look like silent omissions.
    #[serde(default)]
    pub forwards: Vec<ForwardDiagnostic>,
    pub replay_file: Option<String>,
    /// Full decoded OTLP payload for local diagnostics/dashboard display.
    pub payload: Option<Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FindingSummary {
    pub sample: String,
    pub level: String,
    pub id: String,
    pub rule: String,
    pub message: String,
    pub context: Option<Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct RuleSubject {
    pub kind: String,
    pub value: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PolicyRule {
    pub id: String,
    pub rule: String,
    pub rule_group: String,
    pub policy_dir: String,
    pub group: String,
    pub level: String,
    pub file: String,
    pub line: usize,
    pub title_zh: Option<String>,
    pub purpose_zh: String,
    pub check_zh: Option<String>,
    pub fix_zh: Option<String>,
    pub note_zh: Option<String>,
    pub entity: Option<String>,
    pub category_zh: Option<String>,
    pub fields: Vec<String>,
    pub subjects: Vec<RuleSubject>,
    pub placement: Option<String>,
    pub constraint_type: Option<String>,
    pub allowed_values: Vec<String>,
    pub expected_value: Option<String>,
    pub replacement: Option<String>,
    pub condition_key: Option<String>,
    pub condition_zh: Option<String>,
    pub span_kind: Option<String>,
    pub display_order: Option<i64>,
    pub spec_id: Option<String>,
    pub when_zh: Option<String>,
    pub branch: Option<String>,
    pub readme_context_zh: String,
    pub directory_path: String,
    pub directory_title: String,
    pub directory_readme_md: String,
    pub source: String,
}

impl PolicyRule {
    fn key(&self) -> String {
        let base = format!("{}:{}", self.rule_group, self.id);
        self.branch
            .as_deref()
            .filter(|branch| !branch.trim().is_empty())
            .map(|branch| format!("{base}:{}", sanitize_rule_key_segment(branch)))
            .unwrap_or(base)
    }

    fn section_key(&self) -> String {
        policy_section_key(&self.rule_group, &self.policy_dir)
    }
}

fn sanitize_rule_key_segment(value: &str) -> String {
    value
        .chars()
        .map(|ch| {
            if ch.is_ascii_alphanumeric() || ch == '-' || ch == '_' || ch == '.' {
                ch
            } else {
                '-'
            }
        })
        .collect()
}

#[derive(Debug, Clone, Serialize)]
pub struct PolicyRuleState {
    pub id: String,
    pub key: String,
    pub rule: String,
    pub rule_group: String,
    pub policy_dir: String,
    pub group: String,
    pub level: String,
    pub file: String,
    pub line: usize,
    pub title_zh: Option<String>,
    pub purpose_zh: String,
    pub check_zh: Option<String>,
    pub fix_zh: Option<String>,
    pub note_zh: Option<String>,
    pub entity: Option<String>,
    pub category_zh: Option<String>,
    pub fields: Vec<String>,
    pub subjects: Vec<RuleSubject>,
    pub placement: Option<String>,
    pub constraint_type: Option<String>,
    pub allowed_values: Vec<String>,
    pub expected_value: Option<String>,
    pub replacement: Option<String>,
    pub condition_key: Option<String>,
    pub condition_zh: Option<String>,
    pub span_kind: Option<String>,
    pub display_order: Option<i64>,
    pub spec_id: Option<String>,
    pub when_zh: Option<String>,
    pub branch: Option<String>,
    pub readme_context_zh: String,
    pub directory_path: String,
    pub directory_title: String,
    pub directory_readme_md: String,
    pub source: String,
    pub enabled: bool,
}

#[derive(Debug, Clone, Serialize)]
pub struct PolicyGroupState {
    pub name: String,
    pub readme_summary_zh: String,
    pub enabled: bool,
    pub total: usize,
    pub enabled_rules: usize,
    pub sections: BTreeMap<String, PolicySectionState>,
    pub groups: BTreeMap<String, Vec<PolicyRuleState>>,
}

#[derive(Debug, Clone, Serialize)]
pub struct PolicySectionState {
    pub name: String,
    pub key: String,
    pub readme_summary_zh: String,
    pub enabled: bool,
    pub total: usize,
    pub enabled_rules: usize,
    pub groups: BTreeMap<String, Vec<PolicyRuleState>>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ForwardDiagnostic {
    pub backend: Option<String>,
    pub signal: Option<String>,
    pub target: Option<String>,
    pub decision: String,
    pub status: Option<u16>,
    pub reason: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum BackendEncoding {
    /// Forward OTLP protobuf regardless of the incoming OTLP/HTTP encoding.
    OtlpProtobuf,
}

#[derive(Debug, Clone)]
struct BackendTarget {
    id: &'static str,
    kind: SignalKind,
    endpoint: Option<String>,
    encoding: BackendEncoding,
}

#[derive(Debug)]
struct LiveValidation {
    sample_count: usize,
    rejected_count: usize,
    violations: usize,
    improvements: usize,
    semconv_issues: usize,
    findings: Vec<FindingSummary>,
}

struct PolicySelection<'a> {
    rules: &'a [PolicyRule],
    disabled_rules: &'a HashSet<String>,
    disabled_policy_groups: &'a HashSet<String>,
    disabled_policy_sections: &'a HashSet<String>,
}

#[derive(Debug, Clone)]
struct RequestMeta {
    request_id: String,
    path: String,
    method: String,
    kind: SignalKind,
    content_type: String,
}

#[derive(Clone, PartialEq, Message)]
struct RpcStatus {
    #[prost(int32, tag = "1")]
    code: i32,
    #[prost(string, tag = "2")]
    message: String,
    #[prost(message, repeated, tag = "3")]
    details: Vec<Any>,
}

#[derive(Clone, PartialEq, Message)]
struct BadRequest {
    #[prost(message, repeated, tag = "1")]
    field_violations: Vec<FieldViolation>,
}

#[derive(Clone, PartialEq, Message)]
struct FieldViolation {
    #[prost(string, tag = "1")]
    field: String,
    #[prost(string, tag = "2")]
    description: String,
}

async fn handle_traces(State(state): State<Arc<AppState>>, req: Request<Body>) -> Response {
    handle_otlp(state, req, SignalKind::Traces).await
}

async fn handle_metrics(State(state): State<Arc<AppState>>, req: Request<Body>) -> Response {
    handle_otlp(state, req, SignalKind::Metrics).await
}

async fn handle_logs(State(state): State<Arc<AppState>>, req: Request<Body>) -> Response {
    handle_otlp(state, req, SignalKind::Logs).await
}

async fn handle_otlp(state: Arc<AppState>, req: Request<Body>, kind: SignalKind) -> Response {
    let request_id = format!("req-{}", state.sequence.fetch_add(1, Ordering::Relaxed));
    let path = req.uri().path().to_string();
    let method = req.method().to_string();
    let headers = req.headers().clone();
    let content_type = content_type(&headers)
        .unwrap_or("application/json")
        .to_string();
    let meta = RequestMeta {
        request_id: request_id.clone(),
        path: path.clone(),
        method: method.clone(),
        kind,
        content_type: content_type.clone(),
    };
    let encoding = match encoding_from_headers(&headers) {
        Ok(e) => e,
        Err(message) => {
            let diagnostic = state.record_diagnostic(error_diagnostic(
                &meta,
                StatusCode::BAD_REQUEST,
                "unsupported_content_type",
                &message,
            ));
            return error_response(StatusCode::BAD_REQUEST, &diagnostic.findings[0].message);
        }
    };

    let original_body = match to_bytes(req.into_body(), state.max_body_bytes).await {
        Ok(bytes) => bytes,
        Err(err) => {
            state.record_diagnostic(error_diagnostic(
                &meta,
                StatusCode::PAYLOAD_TOO_LARGE,
                "payload_too_large",
                &format!("failed to read request body within limit: {err}"),
            ));
            return error_response(StatusCode::PAYLOAD_TOO_LARGE, "payload too large");
        }
    };

    let decoded_body = match maybe_decompress(&headers, &original_body, state.max_body_bytes) {
        Ok(body) => body,
        Err(err) => {
            state.record_diagnostic(error_diagnostic(
                &meta,
                StatusCode::BAD_REQUEST,
                "gzip_decode_error",
                &err,
            ));
            return error_response(StatusCode::BAD_REQUEST, &err);
        }
    };

    let value = match decode_payload(kind, encoding, &decoded_body) {
        Ok(value) => value,
        Err(err) => {
            state.record_diagnostic(error_diagnostic(
                &meta,
                StatusCode::BAD_REQUEST,
                "decode_error",
                &err,
            ));
            return error_response(StatusCode::BAD_REQUEST, &err);
        }
    };

    if value.get(kind.expected_key()).is_none() {
        let message = format!(
            "request to {} must contain OTLP field '{}'",
            path,
            kind.expected_key()
        );
        state.record_diagnostic(error_diagnostic(
            &meta,
            StatusCode::BAD_REQUEST,
            "wrong_signal_payload",
            &message,
        ));
        return error_response(StatusCode::BAD_REQUEST, &message);
    }

    let replay_file = state.write_replay(kind, &value);
    let agent = extract_resource_attr(kind, &value, "service.name")
        .or_else(|| extract_resource_attr(kind, &value, "ant.agent.product"));
    let source = extract_first_sample_attr(kind, &value, "source")
        .or_else(|| extract_resource_attr(kind, &value, "ant.agent.type"));

    let disabled_rules = state.disabled_rule_ids();
    let disabled_policy_groups = state.disabled_policy_groups();
    let disabled_policy_sections = state.disabled_policy_sections();
    let policy_selection = PolicySelection {
        rules: &state.policy_rules,
        disabled_rules: &disabled_rules,
        disabled_policy_groups: &disabled_policy_groups,
        disabled_policy_sections: &disabled_policy_sections,
    };
    let validation = match validate_value(
        &value,
        kind.signal_type(),
        state.policies_dir.as_ref(),
        state.semconv_registry.as_ref(),
        state.fail_on,
        &policy_selection,
    ) {
        Ok(validation) => validation,
        Err(err) => {
            state.record_diagnostic(error_diagnostic(
                &meta,
                StatusCode::INTERNAL_SERVER_ERROR,
                "validation_runtime_error",
                &err.to_string(),
            ));
            return error_response(StatusCode::INTERNAL_SERVER_ERROR, &err.to_string());
        }
    };

    let has_findings =
        validation.violations + validation.improvements + validation.semconv_issues > 0;
    let forward_policy = state.forward_policy();
    let should_forward_by_policy =
        should_forward(forward_policy, validation.rejected_count, has_findings);
    let targets = state.backend_targets(kind);
    let mut forwards = Vec::with_capacity(targets.len());
    for target in targets {
        let should_attempt_forward = should_forward_by_policy && target.endpoint.is_some();
        let forward = if should_attempt_forward {
            forward_backend(&state, &target, encoding, &decoded_body, &value).await
        } else {
            no_forward_diagnostic_for_target(
                &target,
                forward_policy,
                validation.rejected_count,
                should_forward_by_policy,
            )
        };
        forwards.push(forward);
    }
    let forward = primary_forward(kind, &forwards);

    let status = if validation.rejected_count > 0 {
        "violation"
    } else if has_findings {
        "warning"
    } else {
        "pass"
    };
    let message = partial_message(&validation);

    let diagnostic = DiagnosticRecord {
        timestamp: Utc::now().to_rfc3339(),
        request_id,
        path,
        method,
        signal: kind.signal_name().to_string(),
        content_type: content_type.clone(),
        agent,
        source,
        sample_count: validation.sample_count,
        rejected_count: validation.rejected_count,
        violations: validation.violations,
        improvements: validation.improvements,
        semconv_issues: validation.semconv_issues,
        status: status.to_string(),
        http_status: StatusCode::OK.as_u16(),
        findings: validation.findings,
        forward,
        forwards,
        replay_file,
        payload: Some(value.clone()),
    };
    state.record_diagnostic(diagnostic);

    otlp_response(
        kind,
        encoding,
        validation.rejected_count,
        message.as_deref(),
    )
}

fn error_diagnostic(
    meta: &RequestMeta,
    status: StatusCode,
    id: &str,
    message: &str,
) -> DiagnosticRecord {
    let forward =
        ForwardDiagnostic::blocked(None, id).with_backend(primary_backend_id(meta.kind), meta.kind);
    DiagnosticRecord {
        timestamp: Utc::now().to_rfc3339(),
        request_id: meta.request_id.clone(),
        path: meta.path.clone(),
        method: meta.method.clone(),
        signal: meta.kind.signal_name().to_string(),
        content_type: meta.content_type.clone(),
        agent: None,
        source: None,
        sample_count: 0,
        rejected_count: 0,
        violations: 1,
        improvements: 0,
        semconv_issues: 0,
        status: "error".to_string(),
        http_status: status.as_u16(),
        findings: vec![FindingSummary {
            sample: meta.kind.signal_name().to_string(),
            level: "violation".to_string(),
            id: id.to_string(),
            rule: format!("server:{id}"),
            message: message.to_string(),
            context: None,
        }],
        forward: forward.clone(),
        forwards: vec![forward],
        replay_file: None,
        payload: None,
    }
}

impl ForwardDiagnostic {
    fn blocked(target: Option<String>, reason: &str) -> Self {
        Self {
            backend: None,
            signal: None,
            target,
            decision: "blocked".to_string(),
            status: None,
            reason: Some(reason.to_string()),
        }
    }

    fn skipped(target: Option<String>, reason: &str) -> Self {
        Self {
            backend: None,
            signal: None,
            target,
            decision: "skipped".to_string(),
            status: None,
            reason: Some(reason.to_string()),
        }
    }

    fn forwarded(target: String, status: u16) -> Self {
        Self {
            backend: None,
            signal: None,
            target: Some(target),
            decision: "forwarded".to_string(),
            status: Some(status),
            reason: None,
        }
    }

    fn failed(target: String, status: Option<u16>, reason: String) -> Self {
        Self {
            backend: None,
            signal: None,
            target: Some(target),
            decision: "failed".to_string(),
            status,
            reason: Some(reason),
        }
    }

    fn with_backend(mut self, backend: &str, kind: SignalKind) -> Self {
        self.backend = Some(backend.to_string());
        self.signal = Some(kind.signal_name().to_string());
        self
    }

    fn backend_id(&self) -> &str {
        self.backend.as_deref().unwrap_or("none")
    }
}

fn primary_backend_id(kind: SignalKind) -> &'static str {
    match kind {
        SignalKind::Traces => "jaeger",
        SignalKind::Metrics => "victoria-metrics",
        SignalKind::Logs => "victoria-logs",
    }
}

fn validate_value(
    value: &Value,
    signal_type: SignalType,
    policies_dir: Option<&PathBuf>,
    registry: Option<&SemconvRegistry>,
    fail_on: FailOn,
    policy_selection: &PolicySelection<'_>,
) -> Result<LiveValidation, WeaverGuardError> {
    let signal = OtelSignal {
        signal_type,
        data: value.clone(),
        line_number: 1,
        source_file: None,
    };
    let samples = jsonl::extract_samples(&signal);
    let mut engine = if let Some(dir) = policies_dir {
        Some(ValidationEngine::new(dir).map_err(|e| WeaverGuardError::Validation(e.to_string()))?)
    } else {
        None
    };
    let mut report = ValidationReport::new();
    let mut rejected_count = 0;
    let mut findings = Vec::new();

    for sample in &samples {
        let sample_name = jsonl::sample_name(sample);
        let policy_findings = if let Some(ref mut eng) = engine {
            eng.validate(sample)
                .map_err(|e| WeaverGuardError::Validation(e.to_string()))?
                .into_iter()
                .filter(|finding| policy_finding_enabled(finding, policy_selection))
                .collect::<Vec<_>>()
        } else {
            vec![]
        };
        let semconv_findings = if let Some(reg) = registry {
            semconv::diff_attributes_from_signal_type(reg, signal_type, sample)
        } else {
            vec![]
        };

        if sample_is_rejected(&policy_findings, &semconv_findings, fail_on) {
            rejected_count += rejected_units_for_sample(signal_type, sample);
        }

        for finding in &policy_findings {
            findings.push(FindingSummary {
                sample: sample_name.clone(),
                level: finding.advice_level.clone(),
                id: finding.advice_type.clone(),
                rule: format!("policy:{}", finding.advice_type),
                message: finding.message.clone(),
                context: Some(finding.context.clone()),
            });
        }
        for finding in &semconv_findings {
            findings.push(FindingSummary {
                sample: sample_name.clone(),
                level: "semconv".to_string(),
                id: finding.finding_type.to_string(),
                rule: format!("semconv:{}:{}", finding.finding_type, finding.attribute_id),
                message: finding.message.clone(),
                context: Some(json!({
                    "attribute_id": finding.attribute_id,
                    "span_name": finding.span_name,
                })),
            });
        }

        report.add_sample(
            "<http-request>",
            1,
            &signal_type.to_string(),
            &sample_name,
            policy_findings,
            semconv_findings,
        );
    }

    report.finalize();
    Ok(LiveValidation {
        sample_count: samples.len(),
        rejected_count,
        violations: report.summary.violations,
        improvements: report.summary.improvements,
        semconv_issues: report.summary.semconv_issues,
        findings,
    })
}

fn policy_finding_enabled(
    finding: &ValidationFinding,
    policy_selection: &PolicySelection<'_>,
) -> bool {
    let id = finding
        .advice_type
        .strip_prefix("policy:")
        .unwrap_or(&finding.advice_type);
    if policy_selection.disabled_rules.contains(id) {
        return false;
    }

    let matching = policy_selection
        .rules
        .iter()
        .filter(|rule| rule.id == id)
        .collect::<Vec<_>>();
    if matching.is_empty() {
        return true;
    }

    matching.iter().any(|rule| {
        !policy_selection
            .disabled_policy_groups
            .contains(&rule.rule_group)
            && !policy_selection
                .disabled_policy_sections
                .contains(&rule.section_key())
            && !policy_selection.disabled_rules.contains(&rule.key())
            && !policy_selection.disabled_rules.contains(&rule.id)
    })
}

fn sample_is_rejected(
    policy_findings: &[ValidationFinding],
    semconv_findings: &[SemconvFinding],
    fail_on: FailOn,
) -> bool {
    match fail_on {
        FailOn::Violation => policy_findings
            .iter()
            .any(|f| f.advice_level == "violation"),
        FailOn::Improvement => policy_findings
            .iter()
            .any(|f| f.advice_level == "violation" || f.advice_level == "improvement"),
        FailOn::Any => !policy_findings.is_empty() || !semconv_findings.is_empty(),
    }
}

fn rejected_units_for_sample(signal_type: SignalType, sample: &Value) -> usize {
    match signal_type {
        SignalType::Metrics => metric_datapoint_count(sample).max(1),
        _ => 1,
    }
}

fn metric_datapoint_count(sample: &Value) -> usize {
    let Some(metric) = sample.get("sample").and_then(|s| s.get("metric")) else {
        return 0;
    };
    [
        "sum",
        "gauge",
        "histogram",
        "exponentialHistogram",
        "summary",
    ]
    .iter()
    .filter_map(|key| metric.get(*key))
    .filter_map(|data| data.get("dataPoints").and_then(|v| v.as_array()))
    .map(|points| points.len())
    .sum()
}

fn should_forward(policy: ForwardPolicy, rejected_count: usize, has_findings: bool) -> bool {
    match policy {
        ForwardPolicy::Never => false,
        ForwardPolicy::Always => true,
        ForwardPolicy::PassOnly => rejected_count == 0 && !has_findings,
        ForwardPolicy::PassOrImprovement => rejected_count == 0,
    }
}

fn no_forward_diagnostic_for_target(
    target: &BackendTarget,
    policy: ForwardPolicy,
    rejected_count: usize,
    allow_forward: bool,
) -> ForwardDiagnostic {
    if target.endpoint.is_none() {
        return ForwardDiagnostic::skipped(None, "no_endpoint")
            .with_backend(target.id, target.kind);
    }
    if policy == ForwardPolicy::Never {
        return ForwardDiagnostic::blocked(target.endpoint.clone(), "forwarding_disabled")
            .with_backend(target.id, target.kind);
    }
    if rejected_count > 0 {
        return ForwardDiagnostic::blocked(target.endpoint.clone(), "validation_violation")
            .with_backend(target.id, target.kind);
    }
    if !allow_forward {
        return ForwardDiagnostic::blocked(target.endpoint.clone(), "forward_policy")
            .with_backend(target.id, target.kind);
    }
    ForwardDiagnostic::blocked(target.endpoint.clone(), "forward_policy")
        .with_backend(target.id, target.kind)
}

async fn forward_backend(
    state: &AppState,
    target: &BackendTarget,
    incoming_encoding: OtlpEncoding,
    decoded_body: &[u8],
    value: &Value,
) -> ForwardDiagnostic {
    let Some(endpoint) = target.endpoint.clone() else {
        return no_forward_diagnostic_for_target(target, state.forward_policy(), 0, false);
    };
    let (content_type, body) = match target.encoding {
        BackendEncoding::OtlpProtobuf => {
            match encode_otlp_protobuf(target.kind, incoming_encoding, decoded_body, value) {
                Ok(body) => ("application/x-protobuf".to_string(), body),
                Err(err) => {
                    return ForwardDiagnostic::failed(endpoint, None, err)
                        .with_backend(target.id, target.kind);
                }
            }
        }
    };
    let result = state
        .http_client
        .post(&endpoint)
        .header(header::CONTENT_TYPE, content_type)
        .body(body)
        .send()
        .await;
    match result {
        Ok(resp) => {
            let status = resp.status().as_u16();
            if resp.status().is_success() {
                ForwardDiagnostic::forwarded(endpoint, status).with_backend(target.id, target.kind)
            } else {
                ForwardDiagnostic::failed(
                    endpoint,
                    Some(status),
                    format!("{} returned non-success", target.id),
                )
                .with_backend(target.id, target.kind)
            }
        }
        Err(err) => ForwardDiagnostic::failed(endpoint, None, err.to_string())
            .with_backend(target.id, target.kind),
    }
}

fn encode_otlp_protobuf(
    kind: SignalKind,
    incoming_encoding: OtlpEncoding,
    decoded_body: &[u8],
    value: &Value,
) -> Result<Vec<u8>, String> {
    if incoming_encoding == OtlpEncoding::Protobuf {
        return Ok(decoded_body.to_vec());
    }
    match kind {
        SignalKind::Traces => {
            let request: ExportTraceServiceRequest = serde_json::from_value(value.clone())
                .map_err(|e| format!("failed to re-encode OTLP trace JSON as protobuf: {e}"))?;
            Ok(request.encode_to_vec())
        }
        SignalKind::Metrics => {
            let request: ExportMetricsServiceRequest = serde_json::from_value(value.clone())
                .map_err(|e| format!("failed to re-encode OTLP metrics JSON as protobuf: {e}"))?;
            Ok(request.encode_to_vec())
        }
        SignalKind::Logs => {
            let request: ExportLogsServiceRequest = serde_json::from_value(value.clone())
                .map_err(|e| format!("failed to re-encode OTLP logs JSON as protobuf: {e}"))?;
            Ok(request.encode_to_vec())
        }
    }
}

fn primary_forward(kind: SignalKind, forwards: &[ForwardDiagnostic]) -> ForwardDiagnostic {
    forwards.first().cloned().unwrap_or_else(|| {
        ForwardDiagnostic::skipped(None, "no_backend").with_backend("none", kind)
    })
}

fn encoding_from_headers(headers: &HeaderMap) -> Result<OtlpEncoding, String> {
    let content_type = content_type(headers).unwrap_or("application/json");
    let content_type = content_type
        .split(';')
        .next()
        .unwrap_or(content_type)
        .trim();
    match content_type {
        "application/json" | "application/json; charset=utf-8" => Ok(OtlpEncoding::Json),
        "application/x-protobuf" => Ok(OtlpEncoding::Protobuf),
        other => Err(format!(
            "unsupported content-type '{}'; expected application/json or application/x-protobuf",
            other
        )),
    }
}

fn content_type(headers: &HeaderMap) -> Option<&str> {
    headers
        .get(header::CONTENT_TYPE)
        .and_then(|v| v.to_str().ok())
}

fn maybe_decompress(
    headers: &HeaderMap,
    body: &Bytes,
    max_decoded_bytes: usize,
) -> Result<Vec<u8>, String> {
    let encoding = headers
        .get(header::CONTENT_ENCODING)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");
    if encoding.eq_ignore_ascii_case("gzip") {
        let read_limit = max_decoded_bytes.saturating_add(1) as u64;
        let mut decoder = GzDecoder::new(&body[..]);
        let mut decoded = Vec::new();
        decoder
            .by_ref()
            .take(read_limit)
            .read_to_end(&mut decoded)
            .map_err(|e| format!("failed to decode gzip request body: {e}"))?;
        if decoded.len() > max_decoded_bytes {
            return Err(format!(
                "decoded gzip request body exceeds limit of {max_decoded_bytes} bytes"
            ));
        }
        Ok(decoded)
    } else if encoding.is_empty() || encoding.eq_ignore_ascii_case("identity") {
        if body.len() > max_decoded_bytes {
            return Err(format!(
                "request body exceeds limit of {max_decoded_bytes} bytes"
            ));
        }
        Ok(body.to_vec())
    } else {
        Err(format!("unsupported content-encoding '{encoding}'"))
    }
}

fn decode_payload(kind: SignalKind, encoding: OtlpEncoding, body: &[u8]) -> Result<Value, String> {
    match (kind, encoding) {
        (_, OtlpEncoding::Json) => serde_json::from_slice(body)
            .map_err(|e| format!("failed to parse OTLP JSON request: {e}")),
        (SignalKind::Traces, OtlpEncoding::Protobuf) => {
            let req = ExportTraceServiceRequest::decode(body)
                .map_err(|e| format!("failed to decode OTLP trace protobuf: {e}"))?;
            serde_json::to_value(req)
                .map_err(|e| format!("failed to normalize trace protobuf: {e}"))
        }
        (SignalKind::Metrics, OtlpEncoding::Protobuf) => {
            let req = ExportMetricsServiceRequest::decode(body)
                .map_err(|e| format!("failed to decode OTLP metrics protobuf: {e}"))?;
            serde_json::to_value(req)
                .map_err(|e| format!("failed to normalize metrics protobuf: {e}"))
        }
        (SignalKind::Logs, OtlpEncoding::Protobuf) => {
            let req = ExportLogsServiceRequest::decode(body)
                .map_err(|e| format!("failed to decode OTLP logs protobuf: {e}"))?;
            serde_json::to_value(req).map_err(|e| format!("failed to normalize logs protobuf: {e}"))
        }
    }
}

fn otlp_response(
    kind: SignalKind,
    encoding: OtlpEncoding,
    rejected_count: usize,
    message: Option<&str>,
) -> Response {
    let has_partial = rejected_count > 0 || message.is_some_and(|m| !m.is_empty());
    match encoding {
        OtlpEncoding::Json => {
            let body = if has_partial {
                let partial = match kind {
                    SignalKind::Traces => {
                        json!({"rejectedSpans": rejected_count as i64, "errorMessage": message.unwrap_or("")})
                    }
                    SignalKind::Metrics => {
                        json!({"rejectedDataPoints": rejected_count as i64, "errorMessage": message.unwrap_or("")})
                    }
                    SignalKind::Logs => {
                        json!({"rejectedLogRecords": rejected_count as i64, "errorMessage": message.unwrap_or("")})
                    }
                };
                json!({"partialSuccess": partial})
            } else {
                json!({})
            };
            (
                StatusCode::OK,
                [(header::CONTENT_TYPE, "application/json")],
                body.to_string(),
            )
                .into_response()
        }
        OtlpEncoding::Protobuf => {
            let bytes = match kind {
                SignalKind::Traces => {
                    let response = ExportTraceServiceResponse {
                        partial_success: has_partial.then(|| ExportTracePartialSuccess {
                            rejected_spans: rejected_count as i64,
                            error_message: message.unwrap_or("").to_string(),
                        }),
                    };
                    response.encode_to_vec()
                }
                SignalKind::Metrics => {
                    let response = ExportMetricsServiceResponse {
                        partial_success: has_partial.then(|| ExportMetricsPartialSuccess {
                            rejected_data_points: rejected_count as i64,
                            error_message: message.unwrap_or("").to_string(),
                        }),
                    };
                    response.encode_to_vec()
                }
                SignalKind::Logs => {
                    let response = ExportLogsServiceResponse {
                        partial_success: has_partial.then(|| ExportLogsPartialSuccess {
                            rejected_log_records: rejected_count as i64,
                            error_message: message.unwrap_or("").to_string(),
                        }),
                    };
                    response.encode_to_vec()
                }
            };
            (
                StatusCode::OK,
                [(header::CONTENT_TYPE, "application/x-protobuf")],
                bytes,
            )
                .into_response()
        }
    }
}

fn partial_message(validation: &LiveValidation) -> Option<String> {
    if validation.rejected_count > 0 {
        Some(format!(
            "weaver validation rejected {} sample(s): {} violation(s), {} semconv issue(s)",
            validation.rejected_count, validation.violations, validation.semconv_issues
        ))
    } else if validation.improvements > 0 || validation.semconv_issues > 0 {
        Some(format!(
            "weaver validation warnings: {} improvement(s), {} semconv issue(s)",
            validation.improvements, validation.semconv_issues
        ))
    } else {
        None
    }
}

fn error_response(status: StatusCode, message: &str) -> Response {
    (
        status,
        [(header::CONTENT_TYPE, "application/x-protobuf")],
        otlp_status_body(status, message),
    )
        .into_response()
}

fn otlp_status_body(status: StatusCode, message: &str) -> Vec<u8> {
    let details = if status.is_client_error() {
        let detail = BadRequest {
            field_violations: vec![FieldViolation {
                field: "request".to_string(),
                description: message.to_string(),
            }],
        };
        vec![Any {
            type_url: "type.googleapis.com/google.rpc.BadRequest".to_string(),
            value: detail.encode_to_vec(),
        }]
    } else {
        Vec::new()
    };

    RpcStatus {
        code: grpc_code_for_http_status(status),
        message: message.to_string(),
        details,
    }
    .encode_to_vec()
}

fn grpc_code_for_http_status(status: StatusCode) -> i32 {
    match status {
        StatusCode::BAD_REQUEST => 3,            // INVALID_ARGUMENT
        StatusCode::PAYLOAD_TOO_LARGE => 8,      // RESOURCE_EXHAUSTED
        StatusCode::BAD_GATEWAY => 14,           // UNAVAILABLE
        StatusCode::SERVICE_UNAVAILABLE => 14,   // UNAVAILABLE
        StatusCode::GATEWAY_TIMEOUT => 14,       // UNAVAILABLE
        StatusCode::INTERNAL_SERVER_ERROR => 13, // INTERNAL
        s if s.is_client_error() => 3,           // INVALID_ARGUMENT
        s if s.is_server_error() => 13,          // INTERNAL
        _ => 2,                                  // UNKNOWN
    }
}

impl AppState {
    fn forward_policy(&self) -> ForwardPolicy {
        *self
            .forward_policy
            .lock()
            .unwrap_or_else(|e| e.into_inner())
    }

    fn set_forward_policy(&self, policy: ForwardPolicy) {
        *self
            .forward_policy
            .lock()
            .unwrap_or_else(|e| e.into_inner()) = policy;
    }

    fn victoria_proxy_origin(&self, backend: &str) -> Option<String> {
        let (ui, endpoint) = match backend {
            "metrics" | "victoria-metrics" => (
                self.victoria_metrics_ui.as_deref(),
                self.victoria_metrics_endpoint.as_deref(),
            ),
            "logs" | "events" | "victoria-logs" => (
                self.victoria_logs_ui.as_deref(),
                self.victoria_logs_endpoint.as_deref(),
            ),
            _ => return None,
        };
        ui.and_then(origin_from_url)
            .or_else(|| endpoint.and_then(origin_from_url))
    }

    fn jaeger_proxy_origin(&self) -> Option<String> {
        self.jaeger_ui.as_deref().and_then(origin_from_url)
    }

    fn backend_targets(&self, kind: SignalKind) -> Vec<BackendTarget> {
        match kind {
            SignalKind::Traces => vec![BackendTarget {
                id: "jaeger",
                kind,
                endpoint: self.jaeger_traces_endpoint.clone(),
                encoding: BackendEncoding::OtlpProtobuf,
            }],
            SignalKind::Metrics => vec![BackendTarget {
                id: "victoria-metrics",
                kind,
                endpoint: self.victoria_metrics_endpoint.clone(),
                encoding: BackendEncoding::OtlpProtobuf,
            }],
            SignalKind::Logs => vec![BackendTarget {
                id: "victoria-logs",
                kind,
                endpoint: self.victoria_logs_endpoint.clone(),
                encoding: BackendEncoding::OtlpProtobuf,
            }],
        }
    }

    fn record_diagnostic(&self, diagnostic: DiagnosticRecord) -> DiagnosticRecord {
        if let Some(path) = &self.diagnostic_log {
            if let Err(err) = append_jsonl(path, &diagnostic) {
                error!(path = %path.display(), error = %err, "failed to write diagnostic log");
            }
        }
        let mut recent = self.recent.lock().unwrap_or_else(|e| e.into_inner());
        recent.push_back(diagnostic.clone());
        diagnostic
    }

    fn recent_records(&self, limit: usize) -> Vec<DiagnosticRecord> {
        let recent = self.recent.lock().unwrap_or_else(|e| e.into_inner());
        recent.iter().rev().take(limit).cloned().collect::<Vec<_>>()
    }

    fn all_recent_records(&self) -> Vec<DiagnosticRecord> {
        let recent = self.recent.lock().unwrap_or_else(|e| e.into_inner());
        recent.iter().rev().cloned().collect::<Vec<_>>()
    }

    fn recent_len(&self) -> usize {
        let recent = self.recent.lock().unwrap_or_else(|e| e.into_inner());
        recent.len()
    }

    fn find_recent_record(&self, request_id: &str) -> Option<DiagnosticRecord> {
        let recent = self.recent.lock().unwrap_or_else(|e| e.into_inner());
        recent
            .iter()
            .rev()
            .find(|record| record.request_id == request_id)
            .cloned()
    }

    fn clear_recent_records(&self) -> usize {
        let mut recent = self.recent.lock().unwrap_or_else(|e| e.into_inner());
        let cleared = recent.len();
        recent.clear();
        cleared
    }

    fn disabled_rule_ids(&self) -> HashSet<String> {
        self.disabled_rules
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .clone()
    }

    fn disabled_policy_groups(&self) -> HashSet<String> {
        self.disabled_policy_groups
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .clone()
    }

    fn disabled_policy_sections(&self) -> HashSet<String> {
        self.disabled_policy_sections
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .clone()
    }

    fn set_rule_enabled(&self, id: &str, enabled: bool) {
        let mut disabled = self
            .disabled_rules
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        if enabled {
            disabled.remove(id);
        } else {
            disabled.insert(id.to_string());
        }
    }

    fn set_policy_group_enabled(&self, group: &str, enabled: bool) {
        let mut disabled = self
            .disabled_policy_groups
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        if enabled {
            disabled.remove(group);
        } else {
            disabled.insert(group.to_string());
        }
    }

    fn set_policy_section_enabled(&self, group: &str, policy_dir: &str, enabled: bool) {
        let key = policy_section_key(group, policy_dir);
        let mut disabled = self
            .disabled_policy_sections
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        if enabled {
            disabled.remove(&key);
        } else {
            disabled.insert(key);
        }
    }

    fn policy_rule_groups(&self) -> BTreeMap<String, Vec<PolicyRuleState>> {
        let disabled = self
            .disabled_rules
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        let disabled_policy_groups = self
            .disabled_policy_groups
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        let disabled_policy_sections = self
            .disabled_policy_sections
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        let mut groups: BTreeMap<String, Vec<PolicyRuleState>> = BTreeMap::new();
        groups.entry("spans".to_string()).or_default();
        groups.entry("metrics".to_string()).or_default();
        groups.entry("events".to_string()).or_default();

        for rule in &self.policy_rules {
            let rule_key = rule.key();
            let section_key = rule.section_key();
            groups
                .entry(rule.group.clone())
                .or_default()
                .push(PolicyRuleState {
                    id: rule.id.clone(),
                    key: rule_key.clone(),
                    rule: rule.rule.clone(),
                    rule_group: rule.rule_group.clone(),
                    policy_dir: rule.policy_dir.clone(),
                    group: rule.group.clone(),
                    level: rule.level.clone(),
                    file: rule.file.clone(),
                    line: rule.line,
                    title_zh: rule.title_zh.clone(),
                    purpose_zh: rule.purpose_zh.clone(),
                    check_zh: rule.check_zh.clone(),
                    fix_zh: rule.fix_zh.clone(),
                    note_zh: rule.note_zh.clone(),
                    entity: rule.entity.clone(),
                    category_zh: rule.category_zh.clone(),
                    fields: rule.fields.clone(),
                    subjects: rule.subjects.clone(),
                    placement: rule.placement.clone(),
                    constraint_type: rule.constraint_type.clone(),
                    allowed_values: rule.allowed_values.clone(),
                    expected_value: rule.expected_value.clone(),
                    replacement: rule.replacement.clone(),
                    condition_key: rule.condition_key.clone(),
                    condition_zh: rule.condition_zh.clone(),
                    span_kind: rule.span_kind.clone(),
                    display_order: rule.display_order,
                    spec_id: rule.spec_id.clone(),
                    when_zh: rule.when_zh.clone(),
                    branch: rule.branch.clone(),
                    readme_context_zh: rule.readme_context_zh.clone(),
                    directory_path: rule.directory_path.clone(),
                    directory_title: rule.directory_title.clone(),
                    directory_readme_md: rule.directory_readme_md.clone(),
                    source: rule.source.clone(),
                    enabled: !disabled_policy_groups.contains(&rule.rule_group)
                        && !disabled_policy_sections.contains(&section_key)
                        && !disabled.contains(&rule_key)
                        && !disabled.contains(&rule.id),
                });
        }
        groups
    }

    fn policy_rule_sets(&self) -> BTreeMap<String, PolicyGroupState> {
        let disabled = self
            .disabled_rules
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        let disabled_policy_groups = self
            .disabled_policy_groups
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        let disabled_policy_sections = self
            .disabled_policy_sections
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        let mut sets: BTreeMap<String, PolicyGroupState> = BTreeMap::new();

        for rule in &self.policy_rules {
            let entry = sets
                .entry(rule.rule_group.clone())
                .or_insert_with(|| PolicyGroupState {
                    name: rule.rule_group.clone(),
                    readme_summary_zh: policy_group_readme_summary_zh(
                        rule.readme_context_zh.as_str(),
                        &rule.rule_group,
                    ),
                    enabled: !disabled_policy_groups.contains(&rule.rule_group),
                    total: 0,
                    enabled_rules: 0,
                    sections: BTreeMap::new(),
                    groups: BTreeMap::from([
                        ("spans".to_string(), Vec::new()),
                        ("metrics".to_string(), Vec::new()),
                        ("events".to_string(), Vec::new()),
                    ]),
                });
            let rule_key = rule.key();
            let section_key = rule.section_key();
            let enabled = !disabled_policy_groups.contains(&rule.rule_group)
                && !disabled_policy_sections.contains(&section_key)
                && !disabled.contains(&rule_key)
                && !disabled.contains(&rule.id);
            let rule_state = PolicyRuleState {
                id: rule.id.clone(),
                key: rule_key,
                rule: rule.rule.clone(),
                rule_group: rule.rule_group.clone(),
                policy_dir: rule.policy_dir.clone(),
                group: rule.group.clone(),
                level: rule.level.clone(),
                file: rule.file.clone(),
                line: rule.line,
                title_zh: rule.title_zh.clone(),
                purpose_zh: rule.purpose_zh.clone(),
                check_zh: rule.check_zh.clone(),
                fix_zh: rule.fix_zh.clone(),
                note_zh: rule.note_zh.clone(),
                entity: rule.entity.clone(),
                category_zh: rule.category_zh.clone(),
                fields: rule.fields.clone(),
                subjects: rule.subjects.clone(),
                placement: rule.placement.clone(),
                constraint_type: rule.constraint_type.clone(),
                allowed_values: rule.allowed_values.clone(),
                expected_value: rule.expected_value.clone(),
                replacement: rule.replacement.clone(),
                condition_key: rule.condition_key.clone(),
                condition_zh: rule.condition_zh.clone(),
                span_kind: rule.span_kind.clone(),
                display_order: rule.display_order,
                spec_id: rule.spec_id.clone(),
                when_zh: rule.when_zh.clone(),
                branch: rule.branch.clone(),
                readme_context_zh: rule.readme_context_zh.clone(),
                directory_path: rule.directory_path.clone(),
                directory_title: rule.directory_title.clone(),
                directory_readme_md: rule.directory_readme_md.clone(),
                source: rule.source.clone(),
                enabled,
            };
            entry.total += 1;
            if enabled {
                entry.enabled_rules += 1;
            }
            let section = entry
                .sections
                .entry(rule.policy_dir.clone())
                .or_insert_with(|| PolicySectionState {
                    name: rule.policy_dir.clone(),
                    key: section_key.clone(),
                    readme_summary_zh: rule.readme_context_zh.clone(),
                    enabled: !disabled_policy_groups.contains(&rule.rule_group)
                        && !disabled_policy_sections.contains(&section_key),
                    total: 0,
                    enabled_rules: 0,
                    groups: BTreeMap::from([
                        ("spans".to_string(), Vec::new()),
                        ("metrics".to_string(), Vec::new()),
                        ("events".to_string(), Vec::new()),
                    ]),
                });
            section.total += 1;
            if enabled {
                section.enabled_rules += 1;
            }
            section
                .groups
                .entry(rule.group.clone())
                .or_default()
                .push(rule_state.clone());
            entry
                .groups
                .entry(rule.group.clone())
                .or_default()
                .push(rule_state);
        }

        sets
    }

    fn write_replay(&self, kind: SignalKind, value: &Value) -> Option<String> {
        let dir = self.replay_dir.as_ref()?;
        if let Err(err) = fs::create_dir_all(dir) {
            error!(dir = %dir.display(), error = %err, "failed to create replay dir");
            return None;
        }
        let file = dir.join(format!(
            "received_{}_{}.jsonl",
            kind.signal_name(),
            Utc::now().format("%Y%m%d")
        ));
        match append_jsonl(&file, value) {
            Ok(()) => Some(file.to_string_lossy().to_string()),
            Err(err) => {
                error!(file = %file.display(), error = %err, "failed to write replay jsonl");
                None
            }
        }
    }
}

fn load_policy_rules(policies_dir: Option<&Path>) -> Result<Vec<PolicyRule>, WeaverGuardError> {
    let Some(root) = policies_dir else {
        return Ok(Vec::new());
    };
    if !root.exists() {
        return Ok(Vec::new());
    }

    let mut files = Vec::new();
    collect_rego_files(root, &mut files).map_err(WeaverGuardError::Io)?;
    files.sort();

    let mut rules = Vec::new();
    for file in files {
        let Some(policy_path) = policy_path_info(root, &file) else {
            continue;
        };
        let text = fs::read_to_string(&file).map_err(WeaverGuardError::Io)?;
        let relative = file
            .strip_prefix(root)
            .unwrap_or(&file)
            .to_string_lossy()
            .trim_start_matches('/')
            .to_string();
        rules.extend(parse_policy_rules_from_text(
            &text,
            &relative,
            &policy_path.signal_group,
            &policy_path.rule_group,
            &policy_path.policy_dir,
            &policy_path.readme_context_zh,
            &policy_path.directory_path,
            &policy_path.directory_title,
            &policy_path.directory_readme_md,
        ));
    }
    let mut seen = HashSet::new();
    rules.retain(|rule| {
        seen.insert((
            rule.rule_group.clone(),
            rule.policy_dir.clone(),
            rule.group.clone(),
            rule.file.clone(),
            rule.line,
            rule.id.clone(),
            rule.branch.clone(),
        ))
    });
    rules.sort_by(|a, b| {
        a.rule_group
            .cmp(&b.rule_group)
            .then_with(|| a.policy_dir.cmp(&b.policy_dir))
            .then_with(|| a.group.cmp(&b.group))
            .then_with(|| a.file.cmp(&b.file))
            .then_with(|| a.line.cmp(&b.line))
            .then_with(|| a.id.cmp(&b.id))
    });
    Ok(rules)
}

fn collect_rego_files(path: &Path, files: &mut Vec<PathBuf>) -> std::io::Result<()> {
    if path.is_file() {
        if path.extension().is_some_and(|ext| ext == "rego") {
            files.push(path.to_path_buf());
        }
        return Ok(());
    }
    if path.file_name().and_then(|name| name.to_str()) == Some("generated") {
        return Ok(());
    }
    for entry in fs::read_dir(path)? {
        let entry = entry?;
        let child = entry.path();
        if child.is_dir() {
            collect_rego_files(&child, files)?;
        } else if child.extension().is_some_and(|ext| ext == "rego") {
            files.push(child);
        }
    }
    Ok(())
}

#[derive(Debug, Clone)]
struct PolicyPathInfo {
    rule_group: String,
    policy_dir: String,
    signal_group: String,
    readme_context_zh: String,
    directory_path: String,
    directory_title: String,
    directory_readme_md: String,
}

fn policy_path_info(root: &Path, file: &Path) -> Option<PolicyPathInfo> {
    let relative = file.strip_prefix(root).unwrap_or(file);
    let components = relative
        .components()
        .filter_map(|component| component.as_os_str().to_str().map(str::to_string))
        .collect::<Vec<_>>();
    let first = components.first()?;
    let first_is_signal = signal_group_from_components(std::slice::from_ref(first)).is_some();
    let rule_group = if first_is_signal {
        "default".to_string()
    } else {
        sanitize_policy_group(first)
    };
    let signal_components = if first_is_signal {
        components.as_slice()
    } else {
        components.get(1..).unwrap_or_default()
    };
    let policy_dir = policy_dir_from_components(signal_components)?;
    let signal_group = signal_group_from_policy_dir(&policy_dir);
    let readme_context_zh =
        policy_readme_context_zh(root, &rule_group, &policy_dir, signal_components);
    let directory_path = relative
        .parent()
        .map(|path| path.to_string_lossy().replace('\\', "/"))
        .unwrap_or_default();
    let directory_readme_md =
        read_policy_directory_readme(root, &directory_path).unwrap_or_default();
    let directory_title = readme_title(&directory_readme_md)
        .unwrap_or_else(|| policy_directory_label_zh(&directory_path, &policy_dir));
    Some(PolicyPathInfo {
        rule_group,
        policy_dir,
        signal_group,
        readme_context_zh,
        directory_path,
        directory_title,
        directory_readme_md,
    })
}

fn policy_dir_from_components(components: &[String]) -> Option<String> {
    if components
        .iter()
        .any(|c| matches!(c.as_str(), "traces" | "trace" | "spans" | "span"))
    {
        Some("traces".to_string())
    } else if components
        .iter()
        .any(|c| matches!(c.as_str(), "metrics" | "metric"))
    {
        Some("metrics".to_string())
    } else if components
        .iter()
        .any(|c| matches!(c.as_str(), "events" | "event" | "logs" | "log"))
    {
        Some("events".to_string())
    } else if components.iter().any(|c| c == "base") {
        Some("base".to_string())
    } else {
        None
    }
}

fn signal_group_from_components(components: &[String]) -> Option<String> {
    policy_dir_from_components(components).map(|dir| signal_group_from_policy_dir(&dir))
}

fn signal_group_from_policy_dir(policy_dir: &str) -> String {
    match policy_dir {
        "traces" => "spans".to_string(),
        "metrics" => "metrics".to_string(),
        "events" => "events".to_string(),
        _ => "base".to_string(),
    }
}

fn policy_section_key(rule_group: &str, policy_dir: &str) -> String {
    format!("{rule_group}:{policy_dir}")
}

fn policy_readme_context_zh(
    root: &Path,
    rule_group: &str,
    policy_dir: &str,
    signal_components: &[String],
) -> String {
    let group_root = if rule_group == "default" {
        root.to_path_buf()
    } else {
        root.join(rule_group)
    };
    if policy_dir != "base"
        && signal_components
            .iter()
            .any(|component| component == "base")
    {
        let signal_base_readme = group_root.join(policy_dir).join("base").join("README.md");
        if let Some(summary) = readme_summary_zh(&signal_base_readme) {
            return summary;
        }
    }
    let section_readme = group_root.join(policy_dir).join("README.md");
    if let Some(summary) = readme_summary_zh(&section_readme) {
        return summary;
    }
    policy_dir_default_summary_zh(policy_dir).to_string()
}

fn read_policy_directory_readme(root: &Path, directory_path: &str) -> Option<String> {
    if directory_path.is_empty() {
        return None;
    }
    fs::read_to_string(root.join(directory_path).join("README.md")).ok()
}

fn readme_title(text: &str) -> Option<String> {
    text.lines()
        .find_map(|line| line.trim().strip_prefix("# ").map(str::trim))
        .filter(|title| !title.is_empty())
        .map(str::to_string)
}

fn policy_directory_label_zh(directory_path: &str, policy_dir: &str) -> String {
    let leaf = directory_path
        .rsplit('/')
        .next()
        .filter(|value| !value.is_empty())
        .unwrap_or(policy_dir);
    match (policy_dir, leaf) {
        ("traces", "base") => "Trace/Span 基础兼容约束".to_string(),
        ("events", "base") => "Event/Log 基础兼容约束".to_string(),
        ("metrics", "base") => "Metric 基础兼容约束".to_string(),
        ("traces", value) => format!("{} Span 约束", value.replace('_', " ")),
        ("events", value) => format!("{value} 事件约束"),
        ("metrics", value) => format!("{value} Metric 约束"),
        (_, value) => format!("{value} 规则组"),
    }
}

fn readme_summary_zh(path: &Path) -> Option<String> {
    let text = fs::read_to_string(path).ok()?;
    let title = text
        .lines()
        .find_map(|line| line.strip_prefix("# ").map(str::trim))
        .unwrap_or("Policy");
    let title_zh = match title {
        "Base Policies" => "基础规则",
        "Trace/Span Base Policies" => "Trace/Span 基础规则",
        "Metric Base Policies" => "Metric 基础规则",
        "Event/Log Base Policies" => "Event/Log 基础规则",
        "Trace/Span Policies" => "Trace/Span 规则",
        "Metric Policies" => "Metric 规则",
        "Event/Log Policies" => "Event/Log 规则",
        "AI Vision Policy Group" => "AI Vision 规则组",
        other => other,
    };
    let body = text
        .lines()
        .map(str::trim)
        .find(|line| {
            !line.is_empty()
                && !line.starts_with('#')
                && !line.starts_with("```")
                && !line.starts_with("- ")
        })
        .unwrap_or("");
    Some(format!(
        "{}：{}",
        title_zh,
        translate_readme_sentence_zh(body)
    ))
}

fn translate_readme_sentence_zh(sentence: &str) -> String {
    if sentence.contains("aivision/traces/base/") {
        "提供 Trace/Span 信号专用的 Rego helper 和兼容性检查；不存在全局 base 规则层。".to_string()
    } else if sentence.contains("aivision/metrics/base/") {
        "提供 Metric 信号专用的 Rego helper 和兼容性检查；不存在全局 base 规则层。".to_string()
    } else if sentence.contains("aivision/events/base/") {
        "提供 Event/Log 信号专用的 Rego helper 和兼容性检查；不存在全局 base 规则层。".to_string()
    } else if sentence.contains("aivision/traces/") {
        "覆盖 trace/span 层面的 resource attributes、span attributes、turn、LLM、tool 和 compaction 检查。"
            .to_string()
    } else if sentence.contains("aivision/metrics/") {
        "覆盖 metric 和 data point 层面的 common、LLM 与 tool 指标检查。".to_string()
    } else if sentence.contains("aivision/events/") {
        "覆盖映射为语义事件的 OTLP log record，包括通用属性、Resource 属性和各 event.name 专属检查。".to_string()
    } else if sentence.contains("built-in Weaver Guard policy group") {
        "AI Vision agent 遥测的内置语义规则组，可与后续规则组独立展示和开关。".to_string()
    } else {
        sentence.to_string()
    }
}

fn policy_dir_default_summary_zh(policy_dir: &str) -> &'static str {
    match policy_dir {
        "base" => "基础规则：兼容旧外部布局；内置规则使用 traces/metrics/events 内部的 signal-local base。",
        "traces" => "Trace/Span 规则：校验 trace/span 的拓扑、LLM、tool、消息和 token 语义。",
        "metrics" => "Metric 规则：校验指标名称、类型、data point 属性和 LLM/tool 指标语义。",
        "events" => "Event/Log 规则：校验 OTLP log record 映射出的语义事件字段。",
        _ => "规则目录：校验该目录下定义的语义约定。",
    }
}

fn policy_group_readme_summary_zh(section_summary: &str, rule_group: &str) -> String {
    if rule_group == "aivision" {
        "AI Vision 规则组：面向 AI agent 遥测的内置语义规则集合。".to_string()
    } else if section_summary.is_empty() {
        format!("{rule_group} 规则组：自定义语义规则集合。")
    } else {
        section_summary.to_string()
    }
}

fn rule_purpose_zh(
    id: &str,
    level: &str,
    file: &str,
    source: &str,
    policy_dir: &str,
    signal_group: &str,
) -> String {
    let scope = rule_scope_zh(policy_dir, signal_group, file);
    let action = rule_action_zh(id, source);
    let severity = match level {
        "violation" => "不符合时会作为强制违规阻断或标红。",
        "improvement" => "不符合时作为改进建议提示，不一定阻断。",
        "information" => "用于补充诊断信息，帮助理解遥测质量。",
        _ => "用于语义诊断。",
    };
    format!("{scope}：{action}{severity}")
}

fn rule_scope_zh(policy_dir: &str, signal_group: &str, file: &str) -> &'static str {
    if file.contains("/common_attributes/") {
        "通用属性"
    } else if file.contains("/resource_attributes/") {
        "Resource 属性"
    } else if file.contains("/span_attributes/") {
        "Span 属性"
    } else if file.contains("/resource/") {
        "资源身份"
    } else if file.contains("/placement/") {
        "属性放置位置"
    } else if file.contains("/values/") {
        "属性取值"
    } else if file.contains("/compatibility/") || file.contains("_compatibility_") {
        "兼容性"
    } else if file.contains("/turn/") {
        "会话 turn span"
    } else if file.contains("/llm/") {
        "LLM 调用"
    } else if file.contains("/tool/") {
        "工具调用"
    } else if file.contains("/messages/") {
        "消息格式"
    } else if file.contains("/semconv/") {
        "语义约定"
    } else if file.contains("/identity/") {
        "用户身份"
    } else if file.contains("/compaction_result/") {
        "compaction_result 事件"
    } else if file.contains("/api_request/") {
        "api_request 事件"
    } else if file.contains("/tool_result/") {
        "tool_result 事件"
    } else if file.contains("/user_prompt/") {
        "user_prompt 事件"
    } else if file.contains("/session_start/") {
        "session_start 事件"
    } else if file.contains("/session_end/") {
        "session_end 事件"
    } else if file.contains("/compaction/") {
        "上下文压缩"
    } else if file.contains("/common/") {
        "通用事件/指标"
    } else {
        match (policy_dir, signal_group) {
            ("base", _) => "基础语义",
            ("traces", _) | (_, "spans") => "Trace/Span",
            ("metrics", _) | (_, "metrics") => "Metric",
            ("events", _) | (_, "events") => "Event/Log",
            _ => "语义规则",
        }
    }
}

fn rule_action_zh(id: &str, source: &str) -> &'static str {
    let text = format!("{} {}", id.to_lowercase(), source.to_lowercase());
    if text.contains("deprecated") || text.contains("forbidden") || text.contains("disallowed") {
        "禁止使用已废弃、短名或不允许的属性，推动遥测字段迁移到当前语义约定。"
    } else if text.contains("placement")
        || text.contains("resource_only")
        || text.contains("span_only")
    {
        "确认属性放在正确层级，避免 resource/span/data point 混放。"
    } else if text.contains("wrong_type")
        || text.contains("expected counter")
        || text.contains("expected histogram")
    {
        "校验指标类型是否符合约定，例如 counter 与 histogram 不能互换。"
    } else if text.contains("wrong_operation") {
        "校验 operation name 是否与事件或 span 类型匹配。"
    } else if text.contains("invalid") || text.contains("valid:") {
        "校验属性值是否落在允许枚举或格式范围内。"
    } else if text.contains("token") || text.contains("usage") {
        "校验 LLM token 用量字段是否完整、非负且输入/输出/总量关系正确。"
    } else if text.contains("duration") || text.contains("latency") {
        "校验耗时字段是否存在且可用于延迟分析。"
    } else if text.contains("message")
        || text.contains("input.messages")
        || text.contains("output.messages")
    {
        "校验 OpenAI 兼容消息数组格式，确保 prompt/tool payload 可被稳定分析。"
    } else if text.contains("compaction") || text.contains("compact") {
        "校验上下文压缩/摘要标记，确保压缩过程可追踪。"
    } else if text.contains("missing") {
        "检查必需或推荐字段是否存在，保证诊断、关联和聚合所需语义齐全。"
    } else {
        "校验该规则声明的语义约束，帮助发现遥测字段缺失、错放或不合规问题。"
    }
}

fn sanitize_policy_group(value: &str) -> String {
    value
        .chars()
        .map(|ch| {
            if ch.is_ascii_alphanumeric() || ch == '-' || ch == '_' {
                ch.to_ascii_lowercase()
            } else {
                '-'
            }
        })
        .collect()
}

fn parse_policy_rules_from_text(
    text: &str,
    file: &str,
    default_group: &str,
    rule_group: &str,
    policy_dir: &str,
    readme_context_zh: &str,
    directory_path: &str,
    directory_title: &str,
    directory_readme_md: &str,
) -> Vec<PolicyRule> {
    let mut rules = Vec::new();
    let mut search_from = 0;
    let needle = "deny contains make_advice(";
    while let Some(offset) = text[search_from..].find(needle) {
        let start = search_from + offset;
        let metadata = rule_comment_metadata(text, start);
        let after_start = start + needle.len();
        let next = text[after_start..]
            .find(needle)
            .map(|next_offset| after_start + next_offset)
            .unwrap_or(text.len());
        let source_end = if next < text.len() {
            rule_comment_metadata(text, next).source_start
        } else {
            text.len()
        };
        let block = &text[start..next];
        let source = &text[metadata.source_start..source_end];
        let line = text[..start].bytes().filter(|b| *b == b'\n').count() + 1;
        if let Some(strings) = first_string_literals(block, 2) {
            if strings.len() >= 2 {
                let id = strings[0].clone();
                for group in infer_policy_rule_groups(block, default_group) {
                    let purpose_zh = metadata.purpose_zh.clone().unwrap_or_else(|| {
                        rule_purpose_zh(&id, &strings[1], file, block, policy_dir, &group)
                    });
                    rules.push(PolicyRule {
                        rule: format!("policy:{id}"),
                        id: id.clone(),
                        rule_group: rule_group.to_string(),
                        policy_dir: policy_dir.to_string(),
                        group,
                        level: strings[1].clone(),
                        file: file.to_string(),
                        line,
                        title_zh: metadata.title_zh.clone(),
                        purpose_zh,
                        check_zh: metadata.check_zh.clone(),
                        fix_zh: metadata.fix_zh.clone(),
                        note_zh: metadata.note_zh.clone(),
                        entity: metadata.entity.clone(),
                        category_zh: metadata.category_zh.clone(),
                        fields: metadata.fields.clone(),
                        subjects: metadata.subjects.clone(),
                        placement: metadata.placement.clone(),
                        constraint_type: metadata.constraint_type.clone(),
                        allowed_values: metadata.allowed_values.clone(),
                        expected_value: metadata.expected_value.clone(),
                        replacement: metadata.replacement.clone(),
                        condition_key: metadata.condition_key.clone(),
                        condition_zh: metadata.condition_zh.clone(),
                        span_kind: metadata.span_kind.clone(),
                        display_order: metadata.display_order,
                        spec_id: metadata.spec_id.clone(),
                        when_zh: metadata.when_zh.clone(),
                        branch: metadata.branch.clone(),
                        readme_context_zh: readme_context_zh.to_string(),
                        directory_path: directory_path.to_string(),
                        directory_title: directory_title.to_string(),
                        directory_readme_md: directory_readme_md.to_string(),
                        source: source.trim().to_string(),
                    });
                }
            }
        }
        search_from = after_start;
    }
    rules
}

#[derive(Debug, Clone, Default)]
struct RuleCommentMetadata {
    title_zh: Option<String>,
    purpose_zh: Option<String>,
    check_zh: Option<String>,
    fix_zh: Option<String>,
    note_zh: Option<String>,
    entity: Option<String>,
    category_zh: Option<String>,
    fields: Vec<String>,
    subjects: Vec<RuleSubject>,
    placement: Option<String>,
    constraint_type: Option<String>,
    allowed_values: Vec<String>,
    expected_value: Option<String>,
    replacement: Option<String>,
    condition_key: Option<String>,
    condition_zh: Option<String>,
    span_kind: Option<String>,
    display_order: Option<i64>,
    spec_id: Option<String>,
    when_zh: Option<String>,
    branch: Option<String>,
    source_start: usize,
}

fn parse_rule_subject(value: &str) -> Option<RuleSubject> {
    let (kind, value) = value.split_once(':')?;
    let kind = kind.trim();
    let value = value.trim();
    if kind.is_empty() || value.is_empty() {
        return None;
    }
    match kind {
        "field" | "prefix" => Some(RuleSubject {
            kind: kind.to_string(),
            value: value.to_string(),
        }),
        _ => None,
    }
}

fn rule_comment_metadata(text: &str, rule_start: usize) -> RuleCommentMetadata {
    let mut line_spans = Vec::new();
    let mut offset = 0usize;
    for line in text[..rule_start].split_inclusive('\n') {
        let line_text = line.trim_end_matches('\n').trim_end_matches('\r');
        line_spans.push((offset, line_text));
        offset += line.len();
    }

    let mut lines = Vec::new();
    let mut source_start = rule_start;
    let mut saw_comment = false;

    for (line_start, line) in line_spans.into_iter().rev() {
        let trimmed = line.trim();
        if trimmed.is_empty() {
            if saw_comment {
                break;
            }
            source_start = line_start;
            continue;
        }
        if trimmed.starts_with('#') {
            saw_comment = true;
            source_start = line_start;
            lines.push(trimmed.to_string());
            continue;
        }
        break;
    }

    lines.reverse();
    let mut metadata = RuleCommentMetadata {
        source_start,
        ..RuleCommentMetadata::default()
    };
    for line in lines {
        let content = line.trim_start_matches('#').trim();
        let Some((key, value)) = content.split_once(':') else {
            continue;
        };
        let value = value.trim();
        if value.is_empty() {
            continue;
        }
        match key.trim() {
            "@weaver.rule.title.zh"
            | "@rule.title.zh"
            | "weaver.rule.title.zh"
            | "rule.title.zh" => metadata.title_zh = Some(value.to_string()),
            "@weaver.rule.zh" | "@rule.zh" | "weaver.rule.zh" | "rule.zh" => {
                let value = value.to_string();
                metadata.title_zh.get_or_insert_with(|| value.clone());
                metadata.purpose_zh = Some(value);
            }
            "@weaver.rule.check.zh"
            | "@rule.check.zh"
            | "weaver.rule.check.zh"
            | "rule.check.zh" => metadata.check_zh = Some(value.to_string()),
            "@weaver.rule.fix.zh" | "@rule.fix.zh" | "weaver.rule.fix.zh" | "rule.fix.zh" => {
                metadata.fix_zh = Some(value.to_string())
            }
            "@weaver.rule.note.zh" | "@rule.note.zh" | "weaver.rule.note.zh" | "rule.note.zh" => {
                metadata.note_zh = Some(value.to_string())
            }
            "@weaver.rule.entity" | "@weaver.entity" | "@rule.entity" | "weaver.rule.entity" => {
                metadata.entity = Some(value.to_string())
            }
            "@weaver.rule.category.zh"
            | "@weaver.category.zh"
            | "@rule.category.zh"
            | "weaver.rule.category.zh" => metadata.category_zh = Some(value.to_string()),
            "@weaver.rule.fields" | "@weaver.fields" | "@rule.fields" | "weaver.rule.fields" => {
                metadata.fields.extend(
                    value
                        .split(',')
                        .map(str::trim)
                        .filter(|field| !field.is_empty())
                        .map(str::to_string),
                );
            }
            "@weaver.rule.subject" | "@rule.subject" | "weaver.rule.subject" | "rule.subject" => {
                if let Some(subject) = parse_rule_subject(value) {
                    metadata.subjects.push(subject);
                }
            }
            "@weaver.rule.placement" | "@rule.placement" | "weaver.rule.placement" => {
                metadata.placement = Some(value.to_string())
            }
            "@weaver.rule.constraint.type"
            | "@rule.constraint.type"
            | "weaver.rule.constraint.type"
            | "rule.constraint.type" => metadata.constraint_type = Some(value.to_string()),
            "@weaver.rule.allowed_values"
            | "@rule.allowed_values"
            | "weaver.rule.allowed_values"
            | "rule.allowed_values" => {
                metadata.allowed_values.extend(
                    value
                        .split(',')
                        .map(str::trim)
                        .filter(|item| !item.is_empty())
                        .map(str::to_string),
                );
            }
            "@weaver.rule.expected_value"
            | "@rule.expected_value"
            | "weaver.rule.expected_value"
            | "rule.expected_value" => metadata.expected_value = Some(value.to_string()),
            "@weaver.rule.replacement"
            | "@rule.replacement"
            | "weaver.rule.replacement"
            | "rule.replacement" => metadata.replacement = Some(value.to_string()),
            "@weaver.rule.condition.key"
            | "@rule.condition.key"
            | "weaver.rule.condition.key"
            | "rule.condition.key" => metadata.condition_key = Some(value.to_string()),
            "@weaver.rule.condition.zh"
            | "@rule.condition.zh"
            | "weaver.rule.condition.zh"
            | "rule.condition.zh" => metadata.condition_zh = Some(value.to_string()),
            "@weaver.rule.span.kind"
            | "@rule.span.kind"
            | "weaver.rule.span.kind"
            | "rule.span.kind"
            | "@weaver.spec.span_kind"
            | "@weaver.spec.span.kind"
            | "weaver.spec.span_kind"
            | "weaver.spec.span.kind" => metadata.span_kind = Some(value.to_string()),
            "@weaver.rule.display.order"
            | "@rule.display.order"
            | "weaver.rule.display.order"
            | "rule.display.order" => {
                metadata.display_order = value.parse::<i64>().ok();
            }
            "@weaver.spec.id" | "@rule.spec.id" | "weaver.spec.id" | "rule.spec.id" => {
                metadata.spec_id = Some(value.to_string())
            }
            "@weaver.rule.when.zh" | "@rule.when.zh" | "weaver.rule.when.zh" => {
                metadata.when_zh = Some(value.to_string())
            }
            "@weaver.rule.branch" | "@rule.branch" | "weaver.rule.branch" => {
                metadata.branch = Some(value.to_string())
            }
            _ => {}
        }
    }
    metadata
}

fn infer_policy_rule_groups(block: &str, default_group: &str) -> Vec<String> {
    if default_group != "base" {
        return vec![default_group.to_string()];
    }

    let has_span_rule = block.contains("has_span")
        || block.contains("has_span_attr")
        || block.contains("get_span_attr")
        || block.contains("input.sample.span");
    let has_event_rule = block.contains("has_log")
        || block.contains("has_log_attr")
        || block.contains("get_log_attr")
        || block.contains("input.sample.log");
    let has_metric_rule = block.contains("has_metric")
        || block.contains("has_metric_dp_attr")
        || block.contains("get_metric")
        || block.contains("input.sample.metric");

    let mut groups = Vec::new();
    if has_span_rule {
        groups.push("spans".to_string());
    }
    if has_metric_rule {
        groups.push("metrics".to_string());
    }
    if has_event_rule {
        groups.push("events".to_string());
    }

    if groups.is_empty() && block.contains("has_resource") {
        groups.push("spans".to_string());
        groups.push("metrics".to_string());
        groups.push("events".to_string());
    }

    groups
}

fn first_string_literals(text: &str, limit: usize) -> Option<Vec<String>> {
    let mut values = Vec::new();
    let mut chars = text.char_indices().peekable();
    while let Some((_, ch)) = chars.next() {
        if ch != '"' {
            continue;
        }
        let mut value = String::new();
        let mut escaped = false;
        for (_, inner) in chars.by_ref() {
            if escaped {
                value.push(inner);
                escaped = false;
            } else if inner == '\\' {
                escaped = true;
            } else if inner == '"' {
                values.push(value);
                if values.len() == limit {
                    return Some(values);
                }
                break;
            } else {
                value.push(inner);
            }
        }
    }
    Some(values)
}

fn append_jsonl<T: Serialize>(path: &PathBuf, value: &T) -> std::io::Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let mut file = OpenOptions::new().create(true).append(true).open(path)?;
    let line = serde_json::to_string(value).map_err(std::io::Error::other)?;
    writeln!(file, "{}", line)
}

fn extract_resource_attr(kind: SignalKind, value: &Value, key: &str) -> Option<String> {
    let resource_key = kind.expected_key();
    value
        .get(resource_key)
        .and_then(|v| v.as_array())
        .and_then(|arr| arr.first())
        .and_then(|rs| rs.get("resource"))
        .and_then(|res| find_attr_value(res.get("attributes")?, key))
}

fn extract_first_sample_attr(kind: SignalKind, value: &Value, key: &str) -> Option<String> {
    match kind {
        SignalKind::Traces => value
            .get("resourceSpans")?
            .as_array()?
            .first()?
            .get("scopeSpans")?
            .as_array()?
            .first()?
            .get("spans")?
            .as_array()?
            .first()
            .and_then(|span| find_attr_value(span.get("attributes")?, key)),
        SignalKind::Metrics => value
            .get("resourceMetrics")?
            .as_array()?
            .first()?
            .get("scopeMetrics")?
            .as_array()?
            .first()?
            .get("metrics")?
            .as_array()?
            .first()
            .and_then(|metric| find_attr_value(metric.get("attributes")?, key)),
        SignalKind::Logs => value
            .get("resourceLogs")?
            .as_array()?
            .first()?
            .get("scopeLogs")?
            .as_array()?
            .first()?
            .get("logRecords")?
            .as_array()?
            .first()
            .and_then(|log| find_attr_value(log.get("attributes")?, key)),
    }
}

fn find_attr_value(attrs: &Value, key: &str) -> Option<String> {
    let attrs = attrs.as_array()?;
    for attr in attrs {
        let attr_key = attr.get("key").or_else(|| attr.get("name"))?.as_str()?;
        if attr_key == key {
            return otlp_value_to_string(attr.get("value")?);
        }
    }
    None
}

fn otlp_value_to_string(value: &Value) -> Option<String> {
    value
        .get("stringValue")
        .and_then(|v| v.as_str())
        .map(str::to_string)
        .or_else(|| value.get("intValue").map(|v| v.to_string()))
        .or_else(|| value.get("doubleValue").map(|v| v.to_string()))
        .or_else(|| value.get("boolValue").map(|v| v.to_string()))
}

#[derive(Debug, Deserialize)]
struct RuleUpdate {
    id: Option<String>,
    rule_group: Option<String>,
    policy_dir: Option<String>,
    enabled: bool,
}

async fn api_rules(State(state): State<Arc<AppState>>) -> impl IntoResponse {
    Json(json!({
        "groups": state.policy_rule_groups(),
        "policy_groups": state.policy_rule_sets(),
        "disabled": state.disabled_rule_ids(),
        "disabled_policy_groups": state.disabled_policy_groups(),
        "disabled_policy_sections": state.disabled_policy_sections(),
    }))
}

#[derive(Debug, Deserialize)]
struct SettingsUpdate {
    forward_policy: Option<String>,
}

fn settings_json(state: &AppState) -> Value {
    let policy = state.forward_policy();
    json!({
        "forward_policy": policy.as_kebab(),
        "forward_policy_label": policy.label(),
        "forward_policies": ForwardPolicy::all_for_api(),
    })
}

async fn api_settings(State(state): State<Arc<AppState>>) -> impl IntoResponse {
    Json(settings_json(&state))
}

async fn api_update_settings(
    State(state): State<Arc<AppState>>,
    Json(update): Json<SettingsUpdate>,
) -> Response {
    if let Some(value) = update.forward_policy.as_deref() {
        let Some(policy) = ForwardPolicy::from_kebab(value) else {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({
                    "error": "unknown_forward_policy",
                    "forward_policy": value,
                    "allowed": ForwardPolicy::all_for_api(),
                })),
            )
                .into_response();
        };
        state.set_forward_policy(policy);
    }
    Json(settings_json(&state)).into_response()
}

async fn api_update_rule(
    State(state): State<Arc<AppState>>,
    Json(update): Json<RuleUpdate>,
) -> Response {
    if update.id.is_none() && update.rule_group.is_some() && update.policy_dir.is_some() {
        let rule_group = update.rule_group.as_deref().unwrap();
        let policy_dir = update.policy_dir.as_deref().unwrap();
        if !state
            .policy_rules
            .iter()
            .any(|rule| rule.rule_group == rule_group && rule.policy_dir == policy_dir)
        {
            return (
                StatusCode::NOT_FOUND,
                Json(json!({
                    "error": "unknown_policy_section",
                    "rule_group": rule_group,
                    "policy_dir": policy_dir,
                })),
            )
                .into_response();
        }
        state.set_policy_section_enabled(rule_group, policy_dir, update.enabled);
        return Json(json!({
            "rule_group": rule_group,
            "policy_dir": policy_dir,
            "key": policy_section_key(rule_group, policy_dir),
            "enabled": update.enabled,
            "groups": state.policy_rule_groups(),
            "policy_groups": state.policy_rule_sets(),
            "disabled_policy_sections": state.disabled_policy_sections(),
        }))
        .into_response();
    }

    if let Some(rule_group) = update.rule_group.as_deref().filter(|_| update.id.is_none()) {
        if !state
            .policy_rules
            .iter()
            .any(|rule| rule.rule_group == rule_group)
        {
            return (
                StatusCode::NOT_FOUND,
                Json(json!({"error": "unknown_policy_group", "rule_group": rule_group})),
            )
                .into_response();
        }
        state.set_policy_group_enabled(rule_group, update.enabled);
        return Json(json!({
            "rule_group": rule_group,
            "enabled": update.enabled,
            "groups": state.policy_rule_groups(),
            "policy_groups": state.policy_rule_sets(),
            "disabled_policy_groups": state.disabled_policy_groups(),
            "disabled_policy_sections": state.disabled_policy_sections(),
        }))
        .into_response();
    }

    let Some(raw_id) = update.id.as_deref() else {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"error": "missing_rule_or_group"})),
        )
            .into_response();
    };
    let stripped = raw_id.strip_prefix("policy:").unwrap_or(raw_id);
    let (rule_group, id) = if let Some((group, id)) = stripped.split_once(':') {
        (Some(group.to_string()), id.to_string())
    } else {
        (update.rule_group.clone(), stripped.to_string())
    };
    let exact_match = state.policy_rules.iter().find(|rule| {
        (rule.key() == stripped || rule.rule == raw_id || rule.rule == stripped)
            && update
                .rule_group
                .as_ref()
                .is_none_or(|candidate| candidate == &rule.rule_group)
            && update
                .policy_dir
                .as_ref()
                .is_none_or(|candidate| candidate == &rule.policy_dir)
    });
    let legacy_id_match = || {
        state.policy_rules.iter().find(|rule| {
            rule.id == id
                && rule_group
                    .as_ref()
                    .is_none_or(|candidate| candidate == &rule.rule_group)
                && update
                    .policy_dir
                    .as_ref()
                    .is_none_or(|candidate| candidate == &rule.policy_dir)
        })
    };
    let Some(rule) = exact_match.or_else(legacy_id_match) else {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({"error": "unknown_rule", "id": raw_id, "rule_group": rule_group})),
        )
            .into_response();
    };

    let rule_key = rule.key();
    let rule_group = rule.rule_group.clone();
    state.set_rule_enabled(&rule_key, update.enabled);
    Json(json!({
        "id": rule.id.clone(),
        "key": rule_key,
        "rule_group": rule_group,
        "policy_dir": rule.policy_dir.clone(),
        "enabled": update.enabled,
        "groups": state.policy_rule_groups(),
        "policy_groups": state.policy_rule_sets(),
    }))
    .into_response()
}

async fn healthz(State(state): State<Arc<AppState>>) -> impl IntoResponse {
    Json(json!({
        "status": "ok",
        "service": "weaver-guard",
        "victoria_home": state.victoria_home.clone(),
        "jaeger_home": state.jaeger_home.clone(),
        "victoria_metrics_endpoint": state.victoria_metrics_endpoint.clone(),
        "victoria_logs_endpoint": state.victoria_logs_endpoint.clone(),
        "jaeger_traces_endpoint": state.jaeger_traces_endpoint.clone(),
        "jaeger_ui": state.jaeger_ui.clone(),
        "forwarding_enabled": state.victoria_metrics_endpoint.is_some()
            || state.victoria_logs_endpoint.is_some()
            || state.jaeger_traces_endpoint.is_some(),
        "forward_policy": state.forward_policy().as_kebab(),
    }))
}

async fn dashboard_page() -> impl IntoResponse {
    (
        [
            (header::CACHE_CONTROL, "no-store, no-cache, max-age=0"),
            (header::PRAGMA, "no-cache"),
            (header::EXPIRES, "0"),
        ],
        Html(dashboard::HTML),
    )
}

async fn dashboard_css() -> impl IntoResponse {
    (
        [
            (header::CONTENT_TYPE, "text/css; charset=utf-8"),
            (header::CACHE_CONTROL, "no-store, no-cache, max-age=0"),
            (header::PRAGMA, "no-cache"),
            (header::EXPIRES, "0"),
        ],
        dashboard::CSS,
    )
}

async fn dashboard_js() -> impl IntoResponse {
    (
        [
            (
                header::CONTENT_TYPE,
                "application/javascript; charset=utf-8",
            ),
            (header::CACHE_CONTROL, "no-store, no-cache, max-age=0"),
            (header::PRAGMA, "no-cache"),
            (header::EXPIRES, "0"),
        ],
        dashboard::JS,
    )
}

async fn proxy_victoria(
    State(state): State<Arc<AppState>>,
    AxumPath((backend, path)): AxumPath<(String, String)>,
    req: Request<Body>,
) -> Response {
    // Trust model: this proxy is intentionally same-origin so the local
    // Weaver dashboard can iframe Victoria UIs. Treat configured Victoria
    // endpoints as trusted local developer backends, not arbitrary internet
    // targets. Credential-bearing browser headers are stripped below so a
    // dashboard session cannot accidentally leak Weaver credentials onward.
    let Some(origin) = state.victoria_proxy_origin(&backend) else {
        return (
            StatusCode::SERVICE_UNAVAILABLE,
            format!("victoria backend '{backend}' is not configured for dashboard proxy"),
        )
            .into_response();
    };
    proxy_to_origin(&state, &origin, &path, req, "victoria").await
}

async fn proxy_jaeger_root(State(state): State<Arc<AppState>>, req: Request<Body>) -> Response {
    proxy_jaeger_path(state, String::new(), req).await
}

async fn proxy_jaeger(
    State(state): State<Arc<AppState>>,
    AxumPath(path): AxumPath<String>,
    req: Request<Body>,
) -> Response {
    proxy_jaeger_path(state, path, req).await
}

async fn proxy_jaeger_path(state: Arc<AppState>, path: String, req: Request<Body>) -> Response {
    let Some(origin) = state.jaeger_proxy_origin() else {
        return (
            StatusCode::SERVICE_UNAVAILABLE,
            "jaeger backend is not configured for dashboard proxy",
        )
            .into_response();
    };
    let target_path = if path.trim().is_empty() {
        "jaeger/".to_string()
    } else {
        format!("jaeger/{}", path.trim_start_matches('/'))
    };
    proxy_to_origin(&state, &origin, &target_path, req, "jaeger").await
}

async fn proxy_to_origin(
    state: &AppState,
    origin: &str,
    path: &str,
    req: Request<Body>,
    label: &str,
) -> Response {
    let method = req.method().clone();
    let uri = req.uri().clone();
    let headers = req.headers().clone();
    let target_url = victoria_proxy_target_url(origin, path, uri.query());
    let body = match to_bytes(req.into_body(), state.max_body_bytes).await {
        Ok(body) => body,
        Err(err) => {
            return (
                StatusCode::PAYLOAD_TOO_LARGE,
                format!("{label} proxy request body too large: {err}"),
            )
                .into_response();
        }
    };
    let reqwest_method = match reqwest::Method::from_bytes(method.as_str().as_bytes()) {
        Ok(method) => method,
        Err(err) => {
            return (
                StatusCode::BAD_REQUEST,
                format!("unsupported proxy method {method}: {err}"),
            )
                .into_response();
        }
    };
    let mut builder = state.http_client.request(reqwest_method, &target_url);
    for (name, value) in headers.iter() {
        if proxy_request_header_allowed(name.as_str()) {
            builder = builder.header(name.as_str(), value.as_bytes());
        }
    }

    let response = match builder.body(body.to_vec()).send().await {
        Ok(response) => response,
        Err(err) => {
            return (
                StatusCode::BAD_GATEWAY,
                format!("{label} proxy failed for {target_url}: {err}"),
            )
                .into_response();
        }
    };
    let status =
        StatusCode::from_u16(response.status().as_u16()).unwrap_or(StatusCode::BAD_GATEWAY);
    let headers = response.headers().clone();
    let mut bytes = match response.bytes().await {
        Ok(bytes) => bytes,
        Err(err) => {
            return (
                StatusCode::BAD_GATEWAY,
                format!("{label} proxy failed to read response from {target_url}: {err}"),
            )
                .into_response();
        }
    };
    bytes = maybe_rewrite_jaeger_html(label, &headers, bytes);
    let mut response_builder = Response::builder().status(status);
    for (name, value) in headers.iter() {
        if proxy_response_header_allowed(name.as_str()) {
            response_builder = response_builder.header(name.as_str(), value.as_bytes());
        }
    }
    response_builder
        .body(Body::from(bytes))
        .unwrap_or_else(|err| {
            (
                StatusCode::BAD_GATEWAY,
                format!("{label} proxy response build failed: {err}"),
            )
                .into_response()
        })
}

fn maybe_rewrite_jaeger_html(label: &str, headers: &HeaderMap, bytes: Bytes) -> Bytes {
    if label != "jaeger" || !proxy_response_is_html(headers) {
        return bytes;
    }
    let Ok(html) = std::str::from_utf8(&bytes) else {
        return bytes;
    };
    if html.contains("data-weaver-jaeger-theme") {
        return bytes;
    }

    let Some(insert_at) = find_ascii_case_insensitive(html, "</head>") else {
        return bytes;
    };
    let mut rewritten = String::with_capacity(html.len() + JAEGER_LIGHT_THEME_INJECTION.len());
    rewritten.push_str(&html[..insert_at]);
    rewritten.push_str(JAEGER_LIGHT_THEME_INJECTION);
    rewritten.push_str(&html[insert_at..]);
    Bytes::from(rewritten)
}

fn proxy_response_is_html(headers: &HeaderMap) -> bool {
    headers
        .get(header::CONTENT_TYPE)
        .and_then(|value| value.to_str().ok())
        .map(|value| value.to_ascii_lowercase().contains("text/html"))
        .unwrap_or(false)
}

fn find_ascii_case_insensitive(haystack: &str, needle: &str) -> Option<usize> {
    haystack
        .to_ascii_lowercase()
        .find(&needle.to_ascii_lowercase())
}

fn proxy_request_header_allowed(name: &str) -> bool {
    !matches!(
        name.to_ascii_lowercase().as_str(),
        "host"
            | "connection"
            | "content-length"
            | "transfer-encoding"
            | "authorization"
            | "proxy-authorization"
            | "cookie"
    )
}

fn proxy_response_header_allowed(name: &str) -> bool {
    !matches!(
        name.to_ascii_lowercase().as_str(),
        "connection"
            | "content-length"
            | "transfer-encoding"
            | "x-frame-options"
            | "content-security-policy"
    )
}

fn victoria_proxy_target_url(origin: &str, path: &str, query: Option<&str>) -> String {
    let mut url = format!(
        "{}/{}",
        origin.trim_end_matches('/'),
        path.trim_start_matches('/')
    );
    if let Some(query) = query.filter(|query| !query.is_empty()) {
        url.push('?');
        url.push_str(query);
    }
    url
}

fn origin_from_url(url: &str) -> Option<String> {
    let parsed = reqwest::Url::parse(url).ok()?;
    let host = parsed.host_str()?;
    let mut origin = format!("{}://{}", parsed.scheme(), host);
    if let Some(port) = parsed.port() {
        origin.push(':');
        origin.push_str(&port.to_string());
    }
    Some(origin)
}

#[derive(Debug, Deserialize)]
struct RecentQuery {
    limit: Option<usize>,
    all: Option<bool>,
    include_payload: Option<bool>,
}

#[derive(Debug, Deserialize)]
struct ViolationsQuery {
    limit: Option<usize>,
    signal: Option<String>,
    level: Option<String>,
    levels: Option<String>,
    include_payload: Option<bool>,
}

async fn api_recent(
    State(state): State<Arc<AppState>>,
    axum::extract::Query(query): axum::extract::Query<RecentQuery>,
) -> impl IntoResponse {
    let include_payload = query.include_payload.unwrap_or(true);
    let all = query.all.unwrap_or(false);
    let records = if all {
        state.all_recent_records()
    } else {
        let limit = query
            .limit
            .unwrap_or(100)
            .clamp(1, state.recent_limit.max(1));
        state.recent_records(limit)
    };
    let total_records = state.recent_len();
    let records = records
        .into_iter()
        .map(|record| diagnostic_record_for_api(record, include_payload))
        .collect::<Vec<_>>();
    let returned = records.len();
    Json(json!({
        "records": records,
        "returned": returned,
        "total_records": total_records,
        "all": all,
        "include_payload": include_payload,
    }))
}

async fn api_payload(
    State(state): State<Arc<AppState>>,
    AxumPath(request_id): AxumPath<String>,
) -> Response {
    match state.find_recent_record(&request_id) {
        Some(record) => Json(json!({
            "request_id": record.request_id,
            "payload": record.payload,
            "payload_included": true,
        }))
        .into_response(),
        None => (
            StatusCode::NOT_FOUND,
            Json(json!({
                "error": "payload_not_found",
                "request_id": request_id,
            })),
        )
            .into_response(),
    }
}

async fn api_violations(
    State(state): State<Arc<AppState>>,
    axum::extract::Query(query): axum::extract::Query<ViolationsQuery>,
) -> impl IntoResponse {
    let max_limit = state.recent_limit.min(MAX_VIOLATION_LIMIT).max(1);
    let limit = query
        .limit
        .unwrap_or(DEFAULT_VIOLATION_LIMIT)
        .clamp(1, max_limit);
    let include_payload = query.include_payload.unwrap_or(false);
    let signal_filter = query.signal.as_deref().map(normalize_signal_group);
    let level_filter =
        FindingLevelFilter::from_query(query.level.as_deref(), query.levels.as_deref());
    let records = state.all_recent_records();
    let mut total_recent_findings = 0usize;
    let mut total_recent_violations = 0usize;
    let mut findings = Vec::new();

    for record in &records {
        let signal_group = normalize_signal_group(&record.signal);
        if signal_filter
            .as_deref()
            .is_some_and(|filter| filter != signal_group)
        {
            continue;
        }

        for (index, finding) in record.findings.iter().enumerate() {
            if is_violation_finding(finding) {
                total_recent_violations += 1;
            }
            if !level_filter.matches(&finding.level) {
                continue;
            }
            total_recent_findings += 1;
            if findings.len() >= limit {
                continue;
            }
            let rule = matching_policy_rule(&state.policy_rules, finding, signal_group);
            findings.push(finding_item_json(
                record,
                finding,
                index,
                signal_group,
                rule,
                include_payload,
            ));
        }
    }

    let findings_alias = findings.clone();
    Json(json!({
        "findings": findings_alias,
        "violations": findings,
        "returned": findings_alias.len(),
        "limit": limit,
        "max_limit": max_limit,
        "default_limit": DEFAULT_VIOLATION_LIMIT,
        "total_recent_findings": total_recent_findings,
        "total_recent_violations": total_recent_violations,
        "truncated": total_recent_findings > findings_alias.len(),
        "signal": query.signal,
        "level": query.level,
        "levels": query.levels,
        "filters": {
            "signal": signal_filter,
            "levels": level_filter.levels,
            "include_all_levels": level_filter.include_all,
            "default_level_filter": level_filter.defaulted,
            "ignored_levels": level_filter.ignored,
        },
        "supported_levels": ["violation", "improvement", "information", "semconv"],
        "include_payload": include_payload,
        "records_scanned": records.len(),
    }))
}

async fn api_clear_data(State(state): State<Arc<AppState>>) -> impl IntoResponse {
    let cleared = state.clear_recent_records();
    Json(json!({
        "cleared_records": cleared,
        "remaining_records": 0,
        "scope": "recent_diagnostics",
    }))
}

fn normalize_signal_group(signal: &str) -> &'static str {
    match signal.trim().to_ascii_lowercase().as_str() {
        "trace" | "traces" | "span" | "spans" => "traces",
        "metric" | "metrics" => "metrics",
        "event" | "events" | "log" | "logs" => "events",
        _ => "unknown",
    }
}

fn is_violation_finding(finding: &FindingSummary) -> bool {
    finding.level.eq_ignore_ascii_case("violation")
}

#[derive(Debug, Clone)]
struct FindingLevelFilter {
    levels: Vec<&'static str>,
    include_all: bool,
    defaulted: bool,
    ignored: Vec<String>,
}

impl FindingLevelFilter {
    fn from_query(level: Option<&str>, levels: Option<&str>) -> Self {
        let raw_parts = level
            .into_iter()
            .chain(levels)
            .flat_map(|value| value.split(','))
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .collect::<Vec<_>>();

        if raw_parts.is_empty() {
            return Self {
                levels: vec!["violation"],
                include_all: false,
                defaulted: true,
                ignored: vec![],
            };
        }

        let mut normalized = Vec::new();
        let mut ignored = Vec::new();
        let mut include_all = false;
        for raw in raw_parts {
            if is_all_level_filter(raw) {
                include_all = true;
                continue;
            }
            match normalize_finding_level(raw) {
                Some(level) if !normalized.contains(&level) => normalized.push(level),
                Some(_) => {}
                None => ignored.push(raw.to_string()),
            }
        }

        Self {
            levels: normalized,
            include_all,
            defaulted: false,
            ignored,
        }
    }

    fn matches(&self, finding_level: &str) -> bool {
        if self.include_all {
            return true;
        }
        normalize_finding_level(finding_level).is_some_and(|level| self.levels.contains(&level))
    }
}

fn is_all_level_filter(raw: &str) -> bool {
    matches!(
        raw.trim().to_ascii_lowercase().as_str(),
        "all" | "*" | "any" | "全部" | "所有"
    )
}

fn normalize_finding_level(raw: &str) -> Option<&'static str> {
    match raw.trim().to_ascii_lowercase().as_str() {
        "violation" | "violations" | "error" | "errors" | "deny" | "denied" | "违规" => {
            Some("violation")
        }
        "improvement" | "improvements" | "recommend" | "recommends" | "recommended"
        | "recommendation" | "recommendations" | "recommand" | "recommands" | "recommanded"
        | "recommandation" | "recommandations" | "warning" | "warnings" | "warn" | "建议"
        | "推荐" => Some("improvement"),
        "information" | "informational" | "info" | "infos" | "notice" | "notices" | "信息" => {
            Some("information")
        }
        "semconv"
        | "semantic"
        | "semantic_convention"
        | "semantic_conventions"
        | "semconv_issue"
        | "semconv_issues"
        | "unknown_attribute"
        | "unknown_attributes" => Some("semconv"),
        _ => None,
    }
}

fn matching_policy_rule<'a>(
    rules: &'a [PolicyRule],
    finding: &FindingSummary,
    signal_group: &str,
) -> Option<&'a PolicyRule> {
    let target = finding
        .id
        .strip_prefix("policy:")
        .unwrap_or(&finding.id)
        .to_string();
    rules
        .iter()
        .find(|rule| {
            rule.group == signal_group
                && (rule.id == target
                    || rule.rule == finding.rule
                    || rule.rule == format!("policy:{target}"))
        })
        .or_else(|| {
            rules.iter().find(|rule| {
                rule.id == target
                    || rule.rule == finding.rule
                    || rule.rule == format!("policy:{target}")
            })
        })
}

fn policy_rule_json(rule: &PolicyRule) -> Value {
    json!({
        "id": &rule.id,
        "key": rule.key(),
        "rule": &rule.rule,
        "rule_group": &rule.rule_group,
        "policy_dir": &rule.policy_dir,
        "group": &rule.group,
        "level": &rule.level,
        "file": &rule.file,
        "line": rule.line,
        "title_zh": &rule.title_zh,
        "purpose_zh": &rule.purpose_zh,
        "check_zh": &rule.check_zh,
        "fix_zh": &rule.fix_zh,
        "note_zh": &rule.note_zh,
        "entity": &rule.entity,
        "category_zh": &rule.category_zh,
        "fields": &rule.fields,
        "subjects": &rule.subjects,
        "placement": &rule.placement,
        "constraint_type": &rule.constraint_type,
        "allowed_values": &rule.allowed_values,
        "expected_value": &rule.expected_value,
        "replacement": &rule.replacement,
        "condition_key": &rule.condition_key,
        "condition_zh": &rule.condition_zh,
        "span_kind": &rule.span_kind,
        "display_order": rule.display_order,
        "spec_id": &rule.spec_id,
        "when_zh": &rule.when_zh,
        "branch": &rule.branch,
        "readme_context_zh": &rule.readme_context_zh,
        "directory_path": &rule.directory_path,
        "directory_title": &rule.directory_title,
        "directory_readme_md": &rule.directory_readme_md,
        "source": &rule.source,
    })
}

fn otlp_path_for_finding(record: &DiagnosticRecord, finding: &FindingSummary) -> String {
    let sample = if finding.sample.is_empty() {
        "sample[0]"
    } else {
        &finding.sample
    };
    match normalize_signal_group(&record.signal) {
        "metrics" => format!("resourceMetrics[*].scopeMetrics[*].metrics[{sample:?}]"),
        "events" => format!("resourceLogs[*].scopeLogs[*].logRecords[{sample:?}]"),
        _ => format!("resourceSpans[*].scopeSpans[*].spans[{sample:?}]"),
    }
}

fn finding_item_json(
    record: &DiagnosticRecord,
    finding: &FindingSummary,
    index: usize,
    signal_group: &str,
    rule: Option<&PolicyRule>,
    include_payload: bool,
) -> Value {
    json!({
        "id": format!("V-{}-{}-{}", record.request_id, index, finding.id),
        "index": index,
        "request": {
            "timestamp": &record.timestamp,
            "request_id": &record.request_id,
            "path": &record.path,
            "method": &record.method,
            "signal": &record.signal,
            "signal_group": signal_group,
            "content_type": &record.content_type,
            "agent": &record.agent,
            "source": &record.source,
            "sample_count": record.sample_count,
            "rejected_count": record.rejected_count,
            "status": &record.status,
            "http_status": record.http_status,
            "replay_file": &record.replay_file,
        },
        "finding": finding,
        "rule": rule.map(policy_rule_json),
        "otlp_path": otlp_path_for_finding(record, finding),
        "payload_included": include_payload,
        "payload": if include_payload { record.payload.clone() } else { None },
    })
}

fn diagnostic_record_for_api(
    mut record: DiagnosticRecord,
    include_payload: bool,
) -> DiagnosticRecord {
    if !include_payload {
        record.payload = None;
    }
    record
}

async fn api_stats(State(state): State<Arc<AppState>>) -> impl IntoResponse {
    let records = state.all_recent_records();
    let total = records.len();
    let violations: usize = records.iter().map(|r| r.violations).sum();
    let improvements: usize = records.iter().map(|r| r.improvements).sum();
    let semconv_issues: usize = records.iter().map(|r| r.semconv_issues).sum();
    let forwarded = count_forward_decisions(&records, "forwarded");
    let blocked = count_forward_decisions(&records, "blocked");
    let failed = count_forward_decisions(&records, "failed");
    let skipped = count_forward_decisions(&records, "skipped");
    let backends = backend_stats(&state, &records);
    Json(json!({
        "total_requests": total,
        "violations": violations,
        "improvements": improvements,
        "semconv_issues": semconv_issues,
        "forwarded": forwarded,
        "blocked": blocked,
        "failed": failed,
        "skipped": skipped,
        "recent_limit": state.recent_limit,
        "recent_records": total,
        "forward_policy": state.forward_policy().as_kebab(),
        "forward_policy_label": state.forward_policy().label(),
        "backends": backends,
    }))
}

fn count_forward_decisions(records: &[DiagnosticRecord], decision: &str) -> usize {
    records
        .iter()
        .map(|record| {
            if record.forwards.is_empty() {
                usize::from(record.forward.decision == decision)
            } else {
                record
                    .forwards
                    .iter()
                    .filter(|forward| forward.decision == decision)
                    .count()
            }
        })
        .sum()
}

fn backend_stats(state: &AppState, records: &[DiagnosticRecord]) -> BTreeMap<String, Value> {
    let configs = [
        (
            "jaeger",
            "traces",
            state.jaeger_traces_endpoint.clone(),
            state.jaeger_ui.clone(),
            Some("/jaeger/".to_string()),
            Some("/jaeger/"),
        ),
        (
            "victoria-metrics",
            "metrics",
            state.victoria_metrics_endpoint.clone(),
            state.victoria_metrics_ui.clone(),
            None,
            Some("/vmui/"),
        ),
        (
            "victoria-logs",
            "logs",
            state.victoria_logs_endpoint.clone(),
            state.victoria_logs_ui.clone(),
            None,
            Some("/select/vmui/"),
        ),
    ];

    configs
        .into_iter()
        .map(|(id, signal, endpoint, ui_url, iframe_url, ui_path)| {
            let proxy_available = endpoint.is_some() || ui_url.is_some();
            let direct_url = ui_url.clone().or_else(|| {
                endpoint
                    .as_deref()
                    .and_then(origin_from_url)
                    .zip(ui_path)
                    .map(|(origin, path)| format!("{}{}", origin.trim_end_matches('/'), path))
            });
            let iframe_url = if matches!(id, "victoria-metrics" | "victoria-logs") {
                direct_url.clone()
            } else if proxy_available {
                iframe_url
            } else {
                None
            };
            let mut forwarded = 0usize;
            let mut failed = 0usize;
            let mut blocked = 0usize;
            let mut skipped = 0usize;
            let mut last: Option<Value> = None;

            for record in records {
                let decisions: Vec<ForwardDiagnostic> = if record.forwards.is_empty() {
                    vec![record.forward.clone()]
                } else {
                    record.forwards.clone()
                };
                for forward in decisions
                    .into_iter()
                    .filter(|forward| forward.backend_id() == id)
                {
                    match forward.decision.as_str() {
                        "forwarded" => forwarded += 1,
                        "failed" => failed += 1,
                        "blocked" => blocked += 1,
                        "skipped" => skipped += 1,
                        _ => {}
                    }
                    if last.is_none() {
                        last = Some(json!({
                            "timestamp": record.timestamp,
                            "request_id": record.request_id,
                            "decision": forward.decision,
                            "status": forward.status,
                            "reason": forward.reason,
                        }));
                    }
                }
            }

            (
                id.to_string(),
                json!({
                    "id": id,
                    "signal": signal,
                    "configured": endpoint.is_some(),
                    "proxy_available": proxy_available,
                    "endpoint": endpoint,
                    "ui_url": ui_url,
                    "direct_url": direct_url,
                    "iframe_url": iframe_url,
                    "counts": {
                        "forwarded": forwarded,
                        "failed": failed,
                        "blocked": blocked,
                        "skipped": skipped,
                    },
                    "last": last,
                }),
            )
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::http::HeaderValue;

    fn test_state(
        victoria_metrics_endpoint: Option<String>,
        victoria_logs_endpoint: Option<String>,
        jaeger_traces_endpoint: Option<String>,
        victoria_metrics_ui: Option<String>,
        victoria_logs_ui: Option<String>,
        jaeger_ui: Option<String>,
    ) -> AppState {
        AppState {
            policies_dir: None,
            semconv_registry: None,
            policy_rules: Vec::new(),
            disabled_rules: Mutex::new(HashSet::new()),
            disabled_policy_groups: Mutex::new(HashSet::new()),
            disabled_policy_sections: Mutex::new(HashSet::new()),
            victoria_metrics_endpoint,
            victoria_logs_endpoint,
            victoria_metrics_ui,
            victoria_logs_ui,
            victoria_home: PathBuf::from(".victoria"),
            jaeger_traces_endpoint,
            jaeger_ui,
            jaeger_home: PathBuf::from(".jaeger"),
            diagnostic_log: None,
            replay_dir: None,
            forward_policy: Mutex::new(ForwardPolicy::Always),
            fail_on: FailOn::Violation,
            max_body_bytes: 1024 * 1024,
            recent: Mutex::new(VecDeque::new()),
            recent_limit: 500,
            sequence: AtomicU64::new(1),
            http_client: reqwest::Client::new(),
        }
    }

    #[test]
    fn json_trace_decode_detects_resource_spans() {
        let body = br#"{"resourceSpans":[]}"#;
        let value = decode_payload(SignalKind::Traces, OtlpEncoding::Json, body).unwrap();
        assert!(value.get("resourceSpans").is_some());
    }

    #[test]
    fn policy_rule_loader_ignores_generated_rego() {
        let temp = tempfile::tempdir().unwrap();
        let policies = temp.path().join("policies");
        let generated = policies.join("aivision/events/generated/spec.rego");
        fs::create_dir_all(generated.parent().unwrap()).unwrap();
        fs::write(
            &generated,
            r#"package generated

deny contains make_advice(
    "generated_should_not_load",
    "violation",
    {},
    "generated"
) if { true }
"#,
        )
        .unwrap();

        let normal = policies.join("aivision/events/api_request/violations_required.rego");
        fs::create_dir_all(normal.parent().unwrap()).unwrap();
        fs::write(
            &normal,
            r#"# @weaver.rule.zh: 测试正常规则会被加载。
deny contains make_advice(
    "normal_rule_loads",
    "violation",
    {},
    "normal"
) if { true }
"#,
        )
        .unwrap();

        let rules = load_policy_rules(Some(&policies)).unwrap();
        assert!(rules.iter().any(|rule| rule.id == "normal_rule_loads"));
        assert!(!rules
            .iter()
            .any(|rule| rule.id == "generated_should_not_load"));
    }

    #[test]
    fn policy_rule_loader_preserves_branch_distinct_rule_blocks() {
        let temp = tempfile::tempdir().unwrap();
        let policies = temp.path().join("policies");
        let file = policies.join("aivision/events/api_request/violations_forbidden.rego");
        fs::create_dir_all(file.parent().unwrap()).unwrap();
        fs::write(
            &file,
            r#"# @weaver.rule.zh: 第一条重复 id 规则。
# @weaver.rule.entity: event:api_request
# @weaver.rule.category.zh: 禁用字段
# @weaver.rule.fields: gen_ai.usage.input_tokens
# @weaver.rule.branch: occurrence-1
deny contains make_advice(
    "duplicate_branch_rule",
    "violation",
    {},
    "first"
) if { true }

# @weaver.rule.zh: 第二条重复 id 规则。
# @weaver.rule.entity: event:api_request
# @weaver.rule.category.zh: 禁用字段
# @weaver.rule.fields: gen_ai.usage.input_tokens
# @weaver.rule.branch: occurrence-2
deny contains make_advice(
    "duplicate_branch_rule",
    "violation",
    {},
    "second"
) if { true }
"#,
        )
        .unwrap();

        let rules = load_policy_rules(Some(&policies)).unwrap();
        let matching = rules
            .iter()
            .filter(|rule| rule.id == "duplicate_branch_rule")
            .collect::<Vec<_>>();
        assert_eq!(matching.len(), 2);
        assert_eq!(matching[0].branch.as_deref(), Some("occurrence-1"));
        assert_eq!(matching[1].branch.as_deref(), Some("occurrence-2"));
        assert_eq!(
            matching[0].key(),
            "aivision:duplicate_branch_rule:occurrence-1"
        );
        assert_eq!(
            matching[1].key(),
            "aivision:duplicate_branch_rule:occurrence-2"
        );
    }

    #[test]
    fn protobuf_trace_response_has_partial_success() {
        let response = otlp_response(
            SignalKind::Traces,
            OtlpEncoding::Protobuf,
            2,
            Some("bad spans"),
        );
        assert_eq!(response.status(), StatusCode::OK);
    }

    #[test]
    fn error_response_is_otlp_protobuf_status() {
        let response = error_response(StatusCode::BAD_REQUEST, "bad request body");
        assert_eq!(response.status(), StatusCode::BAD_REQUEST);
        assert_eq!(
            response.headers().get(header::CONTENT_TYPE).unwrap(),
            "application/x-protobuf"
        );

        let body = otlp_status_body(StatusCode::BAD_REQUEST, "bad request body");
        let status = RpcStatus::decode(body.as_slice()).unwrap();
        assert_eq!(status.code, 3);
        assert_eq!(status.message, "bad request body");
        assert_eq!(status.details.len(), 1);
        assert_eq!(
            status.details[0].type_url,
            "type.googleapis.com/google.rpc.BadRequest"
        );
        let bad_request = BadRequest::decode(status.details[0].value.as_slice()).unwrap();
        assert_eq!(bad_request.field_violations[0].field, "request");
    }

    #[test]
    fn gzip_body_decodes() {
        use flate2::write::GzEncoder;
        use flate2::Compression;
        let mut encoder = GzEncoder::new(Vec::new(), Compression::default());
        encoder.write_all(b"{}").unwrap();
        let compressed = encoder.finish().unwrap();
        let mut headers = HeaderMap::new();
        headers.insert(header::CONTENT_ENCODING, HeaderValue::from_static("gzip"));
        let decoded = maybe_decompress(&headers, &Bytes::from(compressed), 16).unwrap();
        assert_eq!(decoded, b"{}");
    }

    #[test]
    fn gzip_body_decode_is_bounded() {
        use flate2::write::GzEncoder;
        use flate2::Compression;
        let mut encoder = GzEncoder::new(Vec::new(), Compression::default());
        encoder.write_all(b"0123456789").unwrap();
        let compressed = encoder.finish().unwrap();
        let mut headers = HeaderMap::new();
        headers.insert(header::CONTENT_ENCODING, HeaderValue::from_static("gzip"));
        let err = maybe_decompress(&headers, &Bytes::from(compressed), 4).unwrap_err();
        assert!(err.contains("exceeds limit"));
    }

    #[test]
    fn local_backend_default_endpoints_are_derived_from_local_addrs() {
        let victoria_config = VictoriaConfig {
            home: PathBuf::from(".victoria"),
            download_dir: None,
            script: PathBuf::from("scripts/victoria-macos-arm64.sh"),
            metrics_addr: DEFAULT_VICTORIA_METRICS_ADDR.to_string(),
            logs_addr: DEFAULT_VICTORIA_LOGS_ADDR.to_string(),
        };
        let victoria = VictoriaEndpoints::from_config(&victoria_config);
        assert_eq!(
            victoria.metrics_endpoint.as_deref(),
            Some("http://127.0.0.1:8428/opentelemetry/v1/metrics")
        );
        assert_eq!(
            victoria.logs_endpoint.as_deref(),
            Some("http://127.0.0.1:9428/insert/opentelemetry/v1/logs")
        );
        assert_eq!(victoria.metrics_ui, "http://127.0.0.1:8428/vmui/");
        assert_eq!(victoria.logs_ui, "http://127.0.0.1:9428/select/vmui/");

        let jaeger_config = JaegerConfig {
            home: PathBuf::from(".jaeger"),
            download_dir: None,
            script: PathBuf::from("scripts/jaeger-macos-arm64.sh"),
            ui_addr: DEFAULT_JAEGER_UI_ADDR.to_string(),
            otlp_http_addr: DEFAULT_JAEGER_OTLP_HTTP_ADDR.to_string(),
            otlp_grpc_addr: DEFAULT_JAEGER_OTLP_GRPC_ADDR.to_string(),
        };
        let jaeger = JaegerEndpoints::from_config(&jaeger_config);
        assert_eq!(
            jaeger.traces_endpoint.as_deref(),
            Some("http://127.0.0.1:14318/v1/traces")
        );
        assert_eq!(jaeger.ui, "http://127.0.0.1:16686/jaeger/");
    }

    #[test]
    fn jaeger_trace_endpoint_missing_skips_trace_forwarding() {
        let target = BackendTarget {
            id: "jaeger",
            kind: SignalKind::Traces,
            endpoint: None,
            encoding: BackendEncoding::OtlpProtobuf,
        };
        let diagnostic = no_forward_diagnostic_for_target(&target, ForwardPolicy::Always, 0, true);
        assert_eq!(diagnostic.decision, "skipped");
        assert_eq!(diagnostic.reason.as_deref(), Some("no_endpoint"));
        assert_eq!(diagnostic.backend.as_deref(), Some("jaeger"));
        assert_eq!(diagnostic.signal.as_deref(), Some("traces"));
        assert!(diagnostic.target.is_none());
    }

    #[test]
    fn victoria_metrics_endpoint_missing_skips_metrics_forwarding() {
        let target = BackendTarget {
            id: "victoria-metrics",
            kind: SignalKind::Metrics,
            endpoint: None,
            encoding: BackendEncoding::OtlpProtobuf,
        };
        let diagnostic = no_forward_diagnostic_for_target(&target, ForwardPolicy::Always, 0, true);
        assert_eq!(diagnostic.decision, "skipped");
        assert_eq!(diagnostic.reason.as_deref(), Some("no_endpoint"));
        assert_eq!(diagnostic.backend.as_deref(), Some("victoria-metrics"));
        assert_eq!(diagnostic.signal.as_deref(), Some("metrics"));
        assert!(diagnostic.target.is_none());
    }

    #[test]
    fn metrics_are_forwardable_when_policy_allows() {
        assert!(should_forward(ForwardPolicy::Always, 0, true));
        assert!(should_forward(ForwardPolicy::Always, 3, true));
        assert!(should_forward(ForwardPolicy::PassOrImprovement, 0, true));
        assert!(!should_forward(ForwardPolicy::PassOnly, 0, true));
        assert!(!should_forward(ForwardPolicy::Never, 0, false));
    }

    #[test]
    fn forward_policy_api_names_round_trip() {
        for policy in [
            ForwardPolicy::Always,
            ForwardPolicy::PassOrImprovement,
            ForwardPolicy::PassOnly,
            ForwardPolicy::Never,
        ] {
            assert_eq!(ForwardPolicy::from_kebab(policy.as_kebab()), Some(policy));
        }
    }

    #[test]
    fn primary_forward_uses_first_backend_decision() {
        let jaeger =
            ForwardDiagnostic::forwarded("http://127.0.0.1:14318/v1/traces".to_string(), 200)
                .with_backend("jaeger", SignalKind::Traces);
        let selected = primary_forward(SignalKind::Traces, std::slice::from_ref(&jaeger));
        assert_eq!(selected.backend.as_deref(), Some("jaeger"));
        assert_eq!(selected.decision, jaeger.decision);
    }

    #[test]
    fn otlp_json_payloads_reencode_to_protobuf_for_victoria_backends() {
        let cases = [
            (
                SignalKind::Traces,
                br#"{"resourceSpans":[]}"#.as_slice(),
                "resourceSpans",
            ),
            (
                SignalKind::Metrics,
                br#"{"resourceMetrics":[]}"#.as_slice(),
                "resourceMetrics",
            ),
            (
                SignalKind::Logs,
                br#"{"resourceLogs":[]}"#.as_slice(),
                "resourceLogs",
            ),
        ];

        for (kind, body, expected_key) in cases {
            let value = decode_payload(kind, OtlpEncoding::Json, body).unwrap();
            assert!(value.get(expected_key).is_some());
            let encoded = encode_otlp_protobuf(kind, OtlpEncoding::Json, body, &value).unwrap();
            match kind {
                SignalKind::Traces => {
                    let decoded = ExportTraceServiceRequest::decode(encoded.as_slice()).unwrap();
                    assert_eq!(decoded.resource_spans.len(), 0);
                }
                SignalKind::Metrics => {
                    let decoded = ExportMetricsServiceRequest::decode(encoded.as_slice()).unwrap();
                    assert_eq!(decoded.resource_metrics.len(), 0);
                }
                SignalKind::Logs => {
                    let decoded = ExportLogsServiceRequest::decode(encoded.as_slice()).unwrap();
                    assert_eq!(decoded.resource_logs.len(), 0);
                }
            }
        }
    }

    #[test]
    fn validation_violation_blocks_only_when_policy_disallows_it() {
        let target = BackendTarget {
            id: "victoria-metrics",
            kind: SignalKind::Metrics,
            endpoint: Some("http://127.0.0.1:8428/opentelemetry/v1/metrics".to_string()),
            encoding: BackendEncoding::OtlpProtobuf,
        };
        let diagnostic =
            no_forward_diagnostic_for_target(&target, ForwardPolicy::PassOnly, 3, false);
        assert_eq!(diagnostic.decision, "blocked");
        assert_eq!(diagnostic.reason.as_deref(), Some("validation_violation"));
        assert_eq!(diagnostic.backend.as_deref(), Some("victoria-metrics"));
        assert_eq!(diagnostic.target.as_deref(), target.endpoint.as_deref());
    }

    #[test]
    fn trace_backend_targets_use_jaeger_only() {
        let state = test_state(
            None,
            None,
            Some("http://127.0.0.1:14318/v1/traces".to_string()),
            None,
            None,
            None,
        );
        let targets = state.backend_targets(SignalKind::Traces);
        assert_eq!(targets.len(), 1);
        let jaeger = &targets[0];
        assert_eq!(jaeger.id, "jaeger");
        assert_eq!(jaeger.encoding, BackendEncoding::OtlpProtobuf);
    }

    #[test]
    fn clear_recent_records_removes_dashboard_data_only() {
        let state = test_state(None, None, None, None, None, None);
        state.set_forward_policy(ForwardPolicy::Never);
        let meta = RequestMeta {
            request_id: "req-1".to_string(),
            path: "/v1/traces".to_string(),
            method: "POST".to_string(),
            kind: SignalKind::Traces,
            content_type: "application/json".to_string(),
        };

        state.record_diagnostic(error_diagnostic(
            &meta,
            StatusCode::BAD_REQUEST,
            "decode_error",
            "bad payload",
        ));

        assert_eq!(state.recent_records(10).len(), 1);
        assert_eq!(state.clear_recent_records(), 1);
        assert!(state.recent_records(10).is_empty());
        assert_eq!(state.clear_recent_records(), 0);
        assert_eq!(state.forward_policy(), ForwardPolicy::Never);
    }

    #[test]
    fn recent_records_are_not_evicted_without_clear() {
        let mut state = test_state(None, None, None, None, None, None);
        state.recent_limit = 1;

        for request_id in ["req-1", "req-2"] {
            let meta = RequestMeta {
                request_id: request_id.to_string(),
                path: "/v1/traces".to_string(),
                method: "POST".to_string(),
                kind: SignalKind::Traces,
                content_type: "application/json".to_string(),
            };
            state.record_diagnostic(error_diagnostic(
                &meta,
                StatusCode::BAD_REQUEST,
                "decode_error",
                "bad payload",
            ));
        }

        assert_eq!(
            state.all_recent_records().len(),
            2,
            "running service must not discard diagnostics unless clear/restart happens"
        );
        assert_eq!(
            state.recent_records(1).len(),
            1,
            "explicit small query limits should still page the response"
        );
        assert_eq!(state.clear_recent_records(), 2);
        assert!(state.all_recent_records().is_empty());
    }

    #[tokio::test]
    async fn violations_api_returns_limited_violation_batch_with_rule_context() {
        let mut state = test_state(None, None, None, None, None, None);
        state.policy_rules.push(PolicyRule {
            id: "missing_foo".to_string(),
            rule: "policy:missing_foo".to_string(),
            rule_group: "aivision".to_string(),
            policy_dir: "traces".to_string(),
            group: "traces".to_string(),
            level: "violation".to_string(),
            file: "aivision/traces/base/violations_required.rego".to_string(),
            line: 12,
            title_zh: Some("测试规则标题".to_string()),
            purpose_zh: "测试规则用途".to_string(),
            check_zh: Some("检查 foo 是否存在".to_string()),
            fix_zh: Some("补充 foo".to_string()),
            note_zh: None,
            entity: Some("span:base".to_string()),
            category_zh: Some("必须属性".to_string()),
            fields: vec!["foo".to_string()],
            subjects: vec![],
            placement: None,
            constraint_type: None,
            allowed_values: vec![],
            expected_value: None,
            replacement: None,
            condition_key: Some("common".to_string()),
            condition_zh: Some("通用约束".to_string()),
            span_kind: Some("CHAT".to_string()),
            display_order: Some(10),
            spec_id: Some("test-required-foo".to_string()),
            when_zh: None,
            branch: None,
            readme_context_zh: "README 上下文".to_string(),
            directory_path: "aivision/traces/base".to_string(),
            directory_title: "Trace/Span 基础兼容约束".to_string(),
            directory_readme_md: "# Trace/Span 基础兼容约束\n\n测试目录说明。".to_string(),
            source: "deny contains make_advice(...)".to_string(),
        });
        state.record_diagnostic(DiagnosticRecord {
            timestamp: "2026-07-03T00:00:00Z".to_string(),
            request_id: "req-1".to_string(),
            path: "/v1/traces".to_string(),
            method: "POST".to_string(),
            signal: "traces".to_string(),
            content_type: "application/json".to_string(),
            agent: Some("agent".to_string()),
            source: Some("source".to_string()),
            sample_count: 1,
            rejected_count: 2,
            violations: 2,
            improvements: 0,
            semconv_issues: 0,
            status: "violation".to_string(),
            http_status: 200,
            findings: vec![
                FindingSummary {
                    sample: "Turn 1".to_string(),
                    level: "violation".to_string(),
                    id: "missing_foo".to_string(),
                    rule: "policy:missing_foo".to_string(),
                    message: "missing foo".to_string(),
                    context: Some(json!({"field": "foo"})),
                },
                FindingSummary {
                    sample: "Turn 2".to_string(),
                    level: "violation".to_string(),
                    id: "missing_bar".to_string(),
                    rule: "policy:missing_bar".to_string(),
                    message: "missing bar".to_string(),
                    context: None,
                },
            ],
            forward: ForwardDiagnostic::skipped(None, "test"),
            forwards: vec![ForwardDiagnostic::skipped(None, "test")],
            replay_file: Some("live/replays/received_traces_20260703.jsonl".to_string()),
            payload: Some(json!({"resourceSpans": []})),
        });

        let response = api_violations(
            State(Arc::new(state)),
            axum::extract::Query(ViolationsQuery {
                limit: Some(1),
                signal: Some("traces".to_string()),
                level: None,
                levels: None,
                include_payload: Some(false),
            }),
        )
        .await
        .into_response();
        assert_eq!(response.status(), StatusCode::OK);

        let body = axum::body::to_bytes(response.into_body(), usize::MAX)
            .await
            .unwrap();
        let value: Value = serde_json::from_slice(&body).unwrap();
        assert_eq!(value["limit"], json!(1));
        assert_eq!(value["returned"], json!(1));
        assert_eq!(value["total_recent_violations"], json!(2));
        assert_eq!(value["truncated"], json!(true));
        assert_eq!(
            value["violations"][0]["finding"]["id"],
            json!("missing_foo")
        );
        assert_eq!(
            value["violations"][0]["rule"]["key"],
            json!("aivision:missing_foo")
        );
        assert_eq!(
            value["violations"][0]["rule"]["condition_zh"],
            json!("通用约束")
        );
        assert_eq!(value["violations"][0]["rule"]["span_kind"], json!("CHAT"));
        assert_eq!(value["violations"][0]["rule"]["display_order"], json!(10));
        assert_eq!(
            value["violations"][0]["rule"]["spec_id"],
            json!("test-required-foo")
        );
        assert_eq!(value["violations"][0]["payload_included"], json!(false));
        assert_eq!(value["violations"][0]["payload"], Value::Null);
    }

    #[tokio::test]
    async fn violations_api_filters_by_signal_and_recommend_level_alias() {
        let state = test_state(None, None, None, None, None, None);
        state.record_diagnostic(DiagnosticRecord {
            timestamp: "2026-07-03T00:00:00Z".to_string(),
            request_id: "req-trace".to_string(),
            path: "/v1/traces".to_string(),
            method: "POST".to_string(),
            signal: "traces".to_string(),
            content_type: "application/json".to_string(),
            agent: None,
            source: None,
            sample_count: 1,
            rejected_count: 1,
            violations: 1,
            improvements: 0,
            semconv_issues: 0,
            status: "violation".to_string(),
            http_status: 200,
            findings: vec![FindingSummary {
                sample: "Turn 1".to_string(),
                level: "violation".to_string(),
                id: "trace_violation".to_string(),
                rule: "policy:trace_violation".to_string(),
                message: "trace violation".to_string(),
                context: None,
            }],
            forward: ForwardDiagnostic::skipped(None, "test"),
            forwards: vec![ForwardDiagnostic::skipped(None, "test")],
            replay_file: None,
            payload: None,
        });
        state.record_diagnostic(DiagnosticRecord {
            timestamp: "2026-07-03T00:00:01Z".to_string(),
            request_id: "req-metric".to_string(),
            path: "/v1/metrics".to_string(),
            method: "POST".to_string(),
            signal: "metrics".to_string(),
            content_type: "application/json".to_string(),
            agent: None,
            source: None,
            sample_count: 1,
            rejected_count: 0,
            violations: 0,
            improvements: 1,
            semconv_issues: 0,
            status: "improvement".to_string(),
            http_status: 200,
            findings: vec![FindingSummary {
                sample: "agent.tool_call.count".to_string(),
                level: "improvement".to_string(),
                id: "metric_recommendation".to_string(),
                rule: "policy:metric_recommendation".to_string(),
                message: "metric recommendation".to_string(),
                context: None,
            }],
            forward: ForwardDiagnostic::skipped(None, "test"),
            forwards: vec![ForwardDiagnostic::skipped(None, "test")],
            replay_file: None,
            payload: None,
        });

        let response = api_violations(
            State(Arc::new(state)),
            axum::extract::Query(ViolationsQuery {
                limit: Some(10),
                signal: Some("metric".to_string()),
                level: Some("recommand".to_string()),
                levels: None,
                include_payload: Some(false),
            }),
        )
        .await
        .into_response();
        assert_eq!(response.status(), StatusCode::OK);

        let body = axum::body::to_bytes(response.into_body(), usize::MAX)
            .await
            .unwrap();
        let value: Value = serde_json::from_slice(&body).unwrap();
        assert_eq!(value["returned"], json!(1));
        assert_eq!(value["total_recent_findings"], json!(1));
        assert_eq!(value["total_recent_violations"], json!(0));
        assert_eq!(value["filters"]["signal"], json!("metrics"));
        assert_eq!(value["filters"]["levels"], json!(["improvement"]));
        assert_eq!(
            value["findings"][0]["finding"]["id"],
            json!("metric_recommendation")
        );
        assert_eq!(value["violations"], value["findings"]);
    }

    #[test]
    fn backend_stats_hide_proxy_if_victoria_backend_is_unconfigured() {
        let state = test_state(None, None, None, None, None, None);
        let record = DiagnosticRecord {
            timestamp: "2026-07-02T00:00:00Z".to_string(),
            request_id: "req-1".to_string(),
            path: "/v1/metrics".to_string(),
            method: "POST".to_string(),
            signal: "metrics".to_string(),
            content_type: "application/json".to_string(),
            agent: None,
            source: None,
            sample_count: 0,
            rejected_count: 0,
            violations: 0,
            improvements: 0,
            semconv_issues: 0,
            status: "pass".to_string(),
            http_status: 200,
            findings: Vec::new(),
            forward: ForwardDiagnostic::skipped(None, "no_endpoint")
                .with_backend("victoria-metrics", SignalKind::Metrics),
            forwards: vec![ForwardDiagnostic::skipped(None, "no_endpoint")
                .with_backend("victoria-metrics", SignalKind::Metrics)],
            replay_file: None,
            payload: None,
        };
        let stats = backend_stats(&state, &[record]);
        let metrics = &stats["victoria-metrics"];
        assert_eq!(metrics["configured"], json!(false));
        assert_eq!(metrics["proxy_available"], json!(false));
        assert_eq!(metrics["iframe_url"], Value::Null);
        assert_eq!(metrics["counts"]["skipped"], json!(1));
        assert_eq!(metrics["last"]["reason"], json!("no_endpoint"));
    }

    #[test]
    fn backend_stats_expose_direct_victoria_iframe_when_configured() {
        let state = test_state(
            Some("http://127.0.0.1:8428/opentelemetry/v1/metrics".to_string()),
            None,
            None,
            Some("http://127.0.0.1:8428/vmui/".to_string()),
            None,
            None,
        );
        let stats = backend_stats(&state, &[]);
        let metrics = &stats["victoria-metrics"];
        assert_eq!(metrics["configured"], json!(true));
        assert_eq!(metrics["proxy_available"], json!(true));
        assert_eq!(metrics["ui_url"], json!("http://127.0.0.1:8428/vmui/"));
        assert_eq!(metrics["direct_url"], json!("http://127.0.0.1:8428/vmui/"));
        assert_eq!(metrics["iframe_url"], json!("http://127.0.0.1:8428/vmui/"));
    }

    #[test]
    fn backend_stats_expose_jaeger_ui_when_configured() {
        let state = test_state(
            None,
            None,
            Some("http://127.0.0.1:14318/v1/traces".to_string()),
            None,
            None,
            Some("http://127.0.0.1:16686/jaeger/".to_string()),
        );
        let stats = backend_stats(&state, &[]);
        let traces = &stats["jaeger"];
        assert_eq!(traces["configured"], json!(true));
        assert_eq!(traces["proxy_available"], json!(true));
        assert_eq!(
            traces["direct_url"],
            json!("http://127.0.0.1:16686/jaeger/")
        );
        assert_eq!(traces["iframe_url"], json!("/jaeger/"));
    }

    #[test]
    fn victoria_proxy_origin_prefers_ui_url_then_endpoint() {
        let with_ui = test_state(
            Some("http://127.0.0.1:8428/opentelemetry/v1/metrics".to_string()),
            None,
            None,
            Some("http://localhost:18428/vmui/".to_string()),
            None,
            None,
        );
        assert_eq!(
            with_ui.victoria_proxy_origin("victoria-metrics").as_deref(),
            Some("http://localhost:18428")
        );
        let endpoint_only = test_state(
            Some("http://127.0.0.1:8428/opentelemetry/v1/metrics".to_string()),
            None,
            None,
            None,
            None,
            None,
        );
        assert_eq!(
            endpoint_only
                .victoria_proxy_origin("victoria-metrics")
                .as_deref(),
            Some("http://127.0.0.1:8428")
        );
    }

    #[test]
    fn proxy_header_safety_strips_hop_by_hop_and_frame_blockers() {
        assert!(!proxy_request_header_allowed("host"));
        assert!(!proxy_request_header_allowed("content-length"));
        assert!(!proxy_request_header_allowed("authorization"));
        assert!(!proxy_request_header_allowed("proxy-authorization"));
        assert!(!proxy_request_header_allowed("cookie"));
        assert!(proxy_request_header_allowed("accept"));
        assert!(!proxy_response_header_allowed("x-frame-options"));
        assert!(!proxy_response_header_allowed("content-security-policy"));
        assert!(!proxy_response_header_allowed("transfer-encoding"));
        assert!(proxy_response_header_allowed("content-type"));
    }

    #[test]
    fn jaeger_proxy_html_forces_light_theme_before_app_boot() {
        let mut headers = HeaderMap::new();
        headers.insert(header::CONTENT_TYPE, HeaderValue::from_static("text/html"));
        let rewritten = maybe_rewrite_jaeger_html(
            "jaeger",
            &headers,
            Bytes::from_static(b"<html><head><title>Jaeger</title></head><body></body></html>"),
        );
        let text = std::str::from_utf8(&rewritten).unwrap();
        assert!(text.contains("jaeger-ui-theme"));
        assert!(text.find("jaeger-ui-theme").unwrap() < text.find("</head>").unwrap());
    }

    #[test]
    fn jaeger_proxy_does_not_rewrite_non_html_assets() {
        let mut headers = HeaderMap::new();
        headers.insert(
            header::CONTENT_TYPE,
            HeaderValue::from_static("application/javascript"),
        );
        let body = Bytes::from_static(b"console.log('jaeger')");
        assert_eq!(
            maybe_rewrite_jaeger_html("jaeger", &headers, body.clone()),
            body
        );
    }

    #[test]
    fn victoria_proxy_target_preserves_path_and_query() {
        let url = victoria_proxy_target_url(
            "http://127.0.0.1:9428/",
            "/select/vmui/",
            Some("tenant=local&limit=20"),
        );
        assert_eq!(
            url,
            "http://127.0.0.1:9428/select/vmui/?tenant=local&limit=20"
        );
    }

    #[test]
    fn jaeger_proxy_target_preserves_base_path_and_query() {
        let url = victoria_proxy_target_url(
            "http://127.0.0.1:16686/",
            "jaeger/search",
            Some("service=agent"),
        );
        assert_eq!(url, "http://127.0.0.1:16686/jaeger/search?service=agent");
    }

    #[test]
    fn origin_from_victoria_urls_keeps_scheme_host_and_port() {
        assert_eq!(
            origin_from_url("http://127.0.0.1:9428/select/vmui/").as_deref(),
            Some("http://127.0.0.1:9428")
        );
        assert_eq!(
            origin_from_url("https://example.test/insert/opentelemetry/v1/traces").as_deref(),
            Some("https://example.test")
        );
    }

    #[test]
    fn policy_path_info_extracts_policy_group_and_signal_group() {
        let root = std::path::Path::new("/repo/weaver/policies");
        let grouped = policy_path_info(
            root,
            &root.join("aivision/traces/resource_attributes/violations_required.rego"),
        )
        .unwrap();
        assert_eq!(grouped.rule_group, "aivision");
        assert_eq!(grouped.policy_dir, "traces");
        assert_eq!(grouped.signal_group, "spans");
        assert_eq!(
            grouped.directory_path,
            "aivision/traces/resource_attributes"
        );
        assert_eq!(grouped.directory_title, "resource attributes Span 约束");

        let trace = policy_path_info(
            root,
            &root.join("aivision/traces/turn/violations_required.rego"),
        )
        .unwrap();
        assert_eq!(trace.rule_group, "aivision");
        assert_eq!(trace.policy_dir, "traces");
        assert_eq!(trace.signal_group, "spans");
        assert_eq!(trace.directory_path, "aivision/traces/turn");

        let trace_base = policy_path_info(
            root,
            &root.join("aivision/traces/base/violations_compatibility_deprecated_genai.rego"),
        )
        .unwrap();
        assert_eq!(trace_base.rule_group, "aivision");
        assert_eq!(trace_base.policy_dir, "traces");
        assert_eq!(trace_base.signal_group, "spans");
        assert_eq!(trace_base.directory_path, "aivision/traces/base");

        let legacy =
            policy_path_info(root, &root.join("traces/turn/violations_required.rego")).unwrap();
        assert_eq!(legacy.rule_group, "default");
        assert_eq!(legacy.policy_dir, "traces");
        assert_eq!(legacy.signal_group, "spans");
        assert_eq!(legacy.directory_path, "traces/turn");
    }

    #[test]
    fn legacy_base_policy_rule_parser_infers_signal_groups() {
        let text = r#"deny contains make_advice(
            "deprecated_gen_ai_prompt",
            "violation",
            {},
            "deprecated"
        ) if {
            has_metric
            has_metric_dp_attr("gen_ai.prompt")
        }

        deny contains make_advice(
            "resource_invalid_agent_type",
            "violation",
            {},
            "bad resource"
        ) if {
            has_resource
        }"#;
        let rules = parse_policy_rules_from_text(
            text,
            "legacy/base/deprecated_genai.rego",
            "base",
            "legacy",
            "base",
            "基础规则：兼容旧外部布局。",
            "legacy/base",
            "基础规则",
            "# 基础规则\n\n测试目录说明。",
        );
        assert!(rules
            .iter()
            .any(|r| r.id == "deprecated_gen_ai_prompt" && r.group == "metrics"));
        assert!(rules
            .iter()
            .any(|r| r.id == "resource_invalid_agent_type" && r.group == "spans"));
        assert!(rules
            .iter()
            .any(|r| r.id == "resource_invalid_agent_type" && r.group == "metrics"));
        assert!(rules
            .iter()
            .any(|r| r.id == "resource_invalid_agent_type" && r.group == "events"));
    }

    #[test]
    fn policy_rule_parser_extracts_make_advice_rules() {
        let rules = parse_policy_rules_from_text(
            r#"deny contains make_advice(
                "span_missing_conversation_id",
                "violation",
                {"span_name": input.sample.span.name},
                "missing conversation"
            ) if { true }"#,
            "aivision/traces/span_attributes/violations_required.rego",
            "spans",
            "aivision",
            "traces",
            "Trace/Span 规则：测试 README 摘要。",
            "aivision/traces/span_attributes",
            "通用 Span 属性约束",
            "# 通用 Span 属性约束\n\n测试目录说明。",
        );
        assert_eq!(rules.len(), 1);
        assert_eq!(rules[0].id, "span_missing_conversation_id");
        assert_eq!(rules[0].rule, "policy:span_missing_conversation_id");
        assert_eq!(rules[0].rule_group, "aivision");
        assert_eq!(rules[0].policy_dir, "traces");
        assert!(rules[0].purpose_zh.contains("检查"));
        assert!(rules[0].readme_context_zh.contains("Trace/Span"));
        assert_eq!(rules[0].directory_path, "aivision/traces/span_attributes");
        assert_eq!(rules[0].directory_title, "通用 Span 属性约束");
        assert!(rules[0].directory_readme_md.contains("通用 Span"));
        assert_eq!(rules[0].level, "violation");
        assert_eq!(rules[0].group, "spans");
        assert_eq!(
            rules[0].file,
            "aivision/traces/span_attributes/violations_required.rego"
        );
        assert_eq!(rules[0].line, 1);
        assert!(rules[0].source.contains("make_advice"));
    }

    #[test]
    fn policy_rule_parser_prefers_weaver_rule_comment_metadata() {
        let rules = parse_policy_rules_from_text(
            r#"# @weaver.rule.zh: 禁止使用裸字段 `username`，必须迁移到 `ant.username`。
# @weaver.rule.check.zh: 当 span 属性中出现 `username` 时命中。
# @weaver.rule.fix.zh: 将 `username` 重命名为 `ant.username`。
# @weaver.rule.note.zh: 默认不阻断上报。
# @weaver.rule.entity: span:llm
# @weaver.rule.category.zh: 禁用字段
# @weaver.rule.fields: username, ant.username
# @weaver.rule.subject: field:username
# @weaver.rule.subject: prefix:llm.*
# @weaver.rule.placement: span_attributes
# @weaver.rule.constraint.type: forbidden_presence
# @weaver.rule.allowed_values: full, privacy
# @weaver.rule.expected_value: full
# @weaver.rule.replacement: field:ant.username
# @weaver.rule.condition.key: common
# @weaver.rule.condition.zh: 通用约束
# @weaver.rule.span.kind: LLM
# @weaver.rule.display.order: 30
# @weaver.spec.id: span-bare-username
# @weaver.rule.when.zh: LLM span 属性中出现裸字段时。
# @weaver.rule.branch: span-attr
deny contains make_advice(
    "bare_attr_username",
    "improvement",
    {"deprecated": "username", "replacement": "ant.username"},
    "Bare attribute"
) if {
    has_span
    has_span_attr("username")
}

# @weaver.rule.zh: 第二条规则说明不应混入第一条源码。
deny contains make_advice(
    "bare_attr_username",
    "improvement",
    {"deprecated": "username", "replacement": "ant.username"},
    "Bare attribute"
) if {
    has_span
    has_span_attr("username")
}"#,
            "aivision/traces/base/improvements_compatibility_deprecated_genai.rego",
            "spans",
            "aivision",
            "traces",
            "Trace/Span 基础规则：测试 README 摘要。",
            "aivision/traces/base",
            "Trace/Span 基础兼容约束",
            "# Trace/Span 基础兼容约束\n\n测试目录说明。",
        );
        assert_eq!(rules.len(), 2);
        assert_eq!(
            rules[0].purpose_zh,
            "禁止使用裸字段 `username`，必须迁移到 `ant.username`。"
        );
        assert_eq!(
            rules[0].title_zh.as_deref(),
            Some("禁止使用裸字段 `username`，必须迁移到 `ant.username`。")
        );
        assert_eq!(
            rules[0].check_zh.as_deref(),
            Some("当 span 属性中出现 `username` 时命中。")
        );
        assert_eq!(
            rules[0].fix_zh.as_deref(),
            Some("将 `username` 重命名为 `ant.username`。")
        );
        assert_eq!(rules[0].note_zh.as_deref(), Some("默认不阻断上报。"));
        assert_eq!(rules[0].entity.as_deref(), Some("span:llm"));
        assert_eq!(rules[0].category_zh.as_deref(), Some("禁用字段"));
        assert_eq!(rules[0].fields, vec!["username", "ant.username"]);
        assert_eq!(
            rules[0].subjects,
            vec![
                RuleSubject {
                    kind: "field".to_string(),
                    value: "username".to_string(),
                },
                RuleSubject {
                    kind: "prefix".to_string(),
                    value: "llm.*".to_string(),
                },
            ]
        );
        assert_eq!(rules[0].placement.as_deref(), Some("span_attributes"));
        assert_eq!(
            rules[0].constraint_type.as_deref(),
            Some("forbidden_presence")
        );
        assert_eq!(rules[0].allowed_values, vec!["full", "privacy"]);
        assert_eq!(rules[0].expected_value.as_deref(), Some("full"));
        assert_eq!(rules[0].replacement.as_deref(), Some("field:ant.username"));
        assert_eq!(rules[0].condition_key.as_deref(), Some("common"));
        assert_eq!(rules[0].condition_zh.as_deref(), Some("通用约束"));
        assert_eq!(rules[0].span_kind.as_deref(), Some("LLM"));
        assert_eq!(rules[0].display_order, Some(30));
        assert_eq!(rules[0].spec_id.as_deref(), Some("span-bare-username"));
        assert_eq!(
            rules[0].when_zh.as_deref(),
            Some("LLM span 属性中出现裸字段时。")
        );
        assert_eq!(rules[0].branch.as_deref(), Some("span-attr"));
        assert!(rules[0].source.contains("@weaver.rule.zh"));
        assert!(rules[0].source.contains("deny contains make_advice"));
        assert!(!rules[0].source.contains("第二条规则说明"));
        assert_eq!(rules[1].purpose_zh, "第二条规则说明不应混入第一条源码。");
    }

    #[test]
    fn dashboard_mentions_recent_api() {
        assert!(dashboard::JS.contains("/api/recent"));
        assert!(dashboard::JS.contains("/api/clear"));
        assert!(dashboard::JS.contains("/api/rules"));
        assert!(!dashboard::JS.contains("fetch('/api/spec')"));
        assert!(dashboard::JS.contains("/api/stats"));
        assert!(dashboard::JS.contains("Debug: Rego source"));
        assert!(dashboard::JS.contains("RULE_LOCATION"));
        assert!(dashboard::JS.contains("FINDING_FIELD_BUCKET_ORDER"));
        assert!(dashboard::JS.contains("必须字段"));
        assert!(dashboard::JS.contains("推荐字段"));
        assert!(dashboard::JS.contains("禁止字段"));
        assert!(!dashboard::JS.contains("RULE_SOURCE"));
        assert!(!dashboard::JS.contains("RULE_EXCERPT"));
        assert!(dashboard::HTML.contains("/dashboard/assets/dashboard.css"));
        assert!(dashboard::HTML.contains("/dashboard/assets/dashboard.js"));
        assert!(dashboard::HTML.contains("AiVision Weaver Guard"));
        assert!(!dashboard::HTML.contains("Local OTLP Semantic Validation"));
        assert!(!dashboard::HTML.contains("Weaver Guard Semantic Console"));
        assert!(!dashboard::HTML.contains("id=\"copyAiFixPromptBtn\""));
        assert!(dashboard::HTML.contains("id=\"openPromptEngineeringBtn\""));
        assert!(dashboard::HTML.contains("id=\"openGuideBtn\""));
        assert!(dashboard::HTML.contains("id=\"openApiDocsBtn\""));
        assert!(dashboard::HTML.contains("id=\"clearDataBtn\""));
        assert!(dashboard::HTML.contains("清理数据"));
        assert!(!dashboard::HTML.contains("复制 AI 修复 Prompt"));
        assert!(dashboard::HTML.contains(">AI 工作流</button>"));
        assert!(dashboard::HTML.contains("id=\"promptEngineeringBackdrop\""));
        assert!(dashboard::JS.contains("promptEngineeringBackdrop"));
        assert!(!dashboard::JS.contains("bootstrapPromptBackdrop"));
        assert!(!dashboard::JS.contains("loopPromptBackdrop"));
        assert!(dashboard::HTML.contains("id=\"promptEngineeringTitle\""));
        assert!(dashboard::HTML.contains("id=\"copyPromptEngineeringBtn\""));
        assert!(dashboard::HTML.contains("data-prompt-tab=\"bootstrap\""));
        assert!(dashboard::HTML.contains("data-prompt-tab=\"loop\""));
        assert!(dashboard::HTML.contains("data-prompt-tab=\"golden\""));
        assert!(dashboard::HTML.contains("新项目接入"));
        assert!(dashboard::HTML.contains("语义修正"));
        assert!(dashboard::HTML.contains("Golden Replay"));
        assert!(dashboard::HTML.contains("id=\"bootstrapSignalTraces\""));
        assert!(dashboard::HTML.contains("id=\"bootstrapLevelSelect\""));
        assert!(dashboard::HTML.contains("value=\"lightweight\""));
        assert!(dashboard::HTML.contains("value=\"standard\""));
        assert!(dashboard::HTML.contains("id=\"bootstrapPromptPreview\""));
        assert!(dashboard::HTML.contains("id=\"loopSignalTraces\""));
        assert!(dashboard::HTML.contains("value=\"traces\" checked"));
        assert!(dashboard::HTML.contains("id=\"loopFixLevelSelect\""));
        assert!(dashboard::HTML.contains("value=\"violation\""));
        assert!(dashboard::HTML.contains("value=\"blocking\""));
        assert!(dashboard::HTML.contains("id=\"loopIncludeSemanticDoc\""));
        assert!(dashboard::HTML.contains("id=\"loopPromptPreview\""));
        assert!(dashboard::HTML.contains("id=\"goldenSignalTraces\""));
        assert!(dashboard::HTML.contains("id=\"goldenReplayStepList\""));
        assert!(dashboard::HTML.contains("API 文档"));
        let prompt_eng_pos = dashboard::HTML
            .find("id=\"openPromptEngineeringBtn\"")
            .expect("Prompt engineering button exists");
        let api_docs_pos = dashboard::HTML
            .find("id=\"openApiDocsBtn\"")
            .expect("API docs button exists");
        assert!(
            prompt_eng_pos < api_docs_pos,
            "Prompt engineering button should be left of API docs button"
        );
        assert!(dashboard::HTML.contains("id=\"apiDocsBackdrop\""));
        assert!(dashboard::HTML.contains("/api/violations"));
        assert!(dashboard::HTML.contains("/api/clear"));
        assert!(dashboard::HTML.contains("接入/修复指南"));
        assert!(dashboard::HTML.contains("id=\"guideBackdrop\""));
        assert!(!dashboard::HTML.contains("id=\"violationGroups\""));
        assert!(!dashboard::HTML.contains("id=\"copyAllViolationsBtn\""));
        assert!(dashboard::HTML.contains("Trace Findings 分类"));
        assert!(dashboard::HTML.contains("data-copy-signal-finding-prompts"));
        assert!(dashboard::HTML.contains("AiVision 接入/修复指南"));
        assert!(!dashboard::HTML.contains("class=\"hero-copy\""));
        assert!(dashboard::HTML.contains("顶级按信号类型切换"));
        assert!(dashboard::HTML.contains("新项目或新 agent 优先点击 <b>AI 工作流</b>"));
        assert!(dashboard::HTML.contains("优先点击 <b>AI 工作流</b>"));
        assert!(dashboard::HTML.contains("语义出现问题后的操作"));
        assert!(dashboard::HTML.contains("忽略语义"));
        assert!(dashboard::JS.contains("openPromptEngineeringModal"));
        assert!(dashboard::JS.contains("closePromptEngineeringModal"));
        assert!(dashboard::JS.contains("renderBootstrapPrompt"));
        assert!(dashboard::JS.contains("promptEngCopyText"));
        assert!(dashboard::JS.contains("bootstrapSelectedSignals"));
        assert!(dashboard::JS.contains("BOOTSTRAP_LEVELS"));
        assert!(dashboard::JS.contains("bootstrapSemanticRequirementLines"));
        assert!(dashboard::JS.contains("semanticRequirementLinesFromGroups"));
        assert!(dashboard::JS.contains("semanticFieldPromptLine"));
        assert!(dashboard::JS.contains("semanticRequirementLines"));
        assert!(dashboard::JS.contains("按具体语义对象分类"));
        assert!(dashboard::JS.contains("按具体 metric 分类"));
        assert!(dashboard::JS.contains("semanticMetricRequirementLines"));
        assert!(dashboard::JS.contains("resourceAttributes 必须字段"));
        assert!(dashboard::JS.contains("标准 OpenTelemetry SDK"));
        assert!(dashboard::JS.contains("OTLP HTTP exporter"));
        assert!(dashboard::JS
            .contains("禁止修改 AiVision / Weaver 侧 Rego、semconv、dashboard、校验规则"));
        assert!(
            dashboard::JS.contains("禁止补 transcript / replay / Makefile / Golden / CAPABILITIES")
        );
        assert!(dashboard::JS.contains("语义修正Loop"));
        assert!(dashboard::JS.contains("setPromptEngTab"));
        assert!(dashboard::JS.contains("promptEngActiveTab"));
        assert!(dashboard::JS.contains("renderLoopPrompt"));
        assert!(dashboard::JS.contains("renderPromptEngPreview"));
        assert!(dashboard::JS.contains("loopFixLevelSelect"));
        assert!(dashboard::JS.contains("loopConfirmSelect"));
        assert!(dashboard::JS.contains("includeSemanticDoc"));
        assert!(dashboard::JS.contains("loopSelectedSignals"));
        assert!(dashboard::JS.contains("LOOP_SIGNAL_ORDER"));
        assert!(dashboard::JS.contains("LOOP_SIGNAL_CONFIG"));
        assert!(dashboard::JS.contains("LOOP_FIX_LEVELS"));
        assert!(dashboard::JS.contains("LOOP_CONFIRMATION"));
        assert!(dashboard::JS.contains("OPENCLAW_REPLAY_DEBUG=true"));
        assert!(dashboard::JS.contains("OPENCLAW_REPLAY_DIR"));
        assert!(dashboard::JS.contains("OPENCLAW_REPLAY_SESSION_DIR"));
        assert!(dashboard::JS.contains("OPENCLAW_REPLAY_SESSION_NAME=golden-superloop"));
        assert!(!dashboard::JS.contains("export OPENCLAW_DEBUG=true"));
        assert!(!dashboard::JS
            .contains("plugins.entries.observ-openclaw.config.sessionDir"));
        assert!(dashboard::JS.contains("hooks.allowConversationAccess true"));
        assert!(dashboard::JS.contains("openclaw-replay.config.backup.json"));
        assert!(!dashboard::JS.contains("openclaw gateway start --background"));
        assert!(dashboard::JS.contains("LOOP_SIGNAL_ORDER.forEach"));
        let ai_fix_prompt_start = dashboard::JS
            .find("const LOOP_FIX_LEVELS")
            .expect("AI fix prompt function exists");
        let ai_fix_prompt_after = &dashboard::JS[ai_fix_prompt_start..];
        let ai_fix_prompt_end = ai_fix_prompt_after
            .find("function apiDocsText()")
            .expect("API docs function follows AI fix prompt function");
        let ai_fix_prompt_body = &ai_fix_prompt_after[..ai_fix_prompt_end];
        assert!(ai_fix_prompt_body.contains("AiVision telemetry instrumentation 修复代理"));
        assert!(ai_fix_prompt_body.contains("修复信号类型"));
        assert!(ai_fix_prompt_body.contains("只修复所选信号类型"));
        assert!(ai_fix_prompt_body.contains("不要处理未选择 signal"));
        assert!(ai_fix_prompt_body.contains("未选择任何信号类型"));
        assert!(ai_fix_prompt_body.contains("不要为了变绿而降低语义标准"));
        assert!(ai_fix_prompt_body.contains("清空当前 recent diagnostics"));
        assert!(ai_fix_prompt_body.contains("signal=traces"));
        assert!(ai_fix_prompt_body.contains("signal=events"));
        assert!(ai_fix_prompt_body.contains("signal=metrics"));
        assert!(ai_fix_prompt_body.contains("level=violation"));
        assert!(ai_fix_prompt_body.contains("level=semconv"));
        assert!(ai_fix_prompt_body.contains("level=improvement"));
        assert!(ai_fix_prompt_body.contains("level=information"));
        assert!(ai_fix_prompt_body.contains("level=violation,semconv"));
        assert!(ai_fix_prompt_body.contains("levels=all"));
        assert!(ai_fix_prompt_body.contains("每一轮准备修改前都先给用户列出计划"));
        assert!(ai_fix_prompt_body.contains("不需要逐步确认"));
        assert!(ai_fix_prompt_body.contains("/v1/traces"));
        assert!(ai_fix_prompt_body.contains("/v1/logs"));
        assert!(ai_fix_prompt_body.contains("/v1/metrics"));
        assert!(!dashboard::HTML.contains("<style>"));
        assert!(!dashboard::HTML.contains("<script>"));
        assert!(dashboard::HTML.contains("Rego Policy Rules"));
        assert!(dashboard::HTML.contains("Policy Groups"));
        assert!(dashboard::HTML.contains("aivision"));
        assert!(dashboard::HTML.contains("data-primary-tab=\"traces\""));
        assert!(dashboard::HTML.contains("data-primary-tab=\"events\""));
        assert!(dashboard::HTML.contains("data-primary-tab=\"metrics\""));
        assert!(dashboard::HTML.contains("data-secondary-tab=\"overview\""));
        assert!(dashboard::HTML.contains("data-secondary-tab=\"findings\""));
        assert!(dashboard::HTML.contains("data-secondary-tab=\"diagnostics\""));
        assert!(!dashboard::HTML.contains("data-secondary-tab=\"spec\""));
        assert!(dashboard::HTML.contains("data-secondary-tab=\"rules\""));
        assert!(dashboard::HTML.contains("data-secondary-tab=\"payloads\""));
        assert!(!dashboard::HTML.contains("Field Spec"));
        assert!(!dashboard::HTML.contains("id=\"tracesSpec\""));
        assert!(!dashboard::HTML.contains("id=\"eventsSpec\""));
        assert!(dashboard::HTML.contains("id=\"tracesRulesInline\""));
        assert!(dashboard::HTML.contains("id=\"eventsRulesInline\""));
        assert!(dashboard::HTML.contains("id=\"metricsRulesInline\""));
        assert!(dashboard::HTML.contains("id=\"tracesFindingsList\""));
        assert!(dashboard::HTML.contains("id=\"eventsFindingsList\""));
        assert!(dashboard::HTML.contains("id=\"metricsFindingsList\""));
        assert!(dashboard::HTML.contains("rulesBackdrop"));
        assert!(dashboard::CSS.contains("rule-accordion"));
        assert!(dashboard::CSS.contains("rule-source"));
        assert!(dashboard::CSS.contains("policy-chip"));
        assert!(dashboard::CSS.contains("switch-control"));
        assert!(dashboard::CSS.contains("rule-tabs"));
        assert!(dashboard::CSS.contains("rule-doc"));
        assert!(dashboard::CSS.contains("rule-purpose"));
        assert!(dashboard::CSS.contains("guide-card"));
        assert!(dashboard::CSS.contains("guide-code"));
        assert!(dashboard::CSS.contains("loop-signal-option"));
        assert!(dashboard::CSS.contains("loop-signal-choices"));
        assert!(dashboard::CSS.contains("loop-checkbox"));
        assert!(dashboard::CSS.contains("split-browser"));
        assert!(dashboard::CSS.contains("split-tree"));
        assert!(dashboard::CSS.contains("tree-leaf"));
        assert!(dashboard::CSS.contains("tree-domain"));
        assert!(dashboard::CSS.contains("markdown-doc"));
        assert!(dashboard::CSS.contains("detail-panel-head"));
        assert!(dashboard::CSS.contains("rule-tree"));
        assert!(dashboard::CSS.contains("finding-tree"));
        assert!(dashboard::CSS.contains("semantic-bucket-node"));
        assert!(dashboard::CSS.contains("field-page-rule-list"));
        assert!(dashboard::CSS.contains("field-rule-row"));
        assert!(dashboard::CSS.contains("metadata-warning"));
        assert!(dashboard::CSS.contains("finding-group-row-actions"));
        assert!(dashboard::CSS.contains("--background: #ffffff"));
        assert!(dashboard::CSS.contains("--foreground: #09090b"));
        assert!(dashboard::CSS.contains("--primary: #18181b"));
        assert!(dashboard::CSS.contains("color-scheme: light"));
        assert!(dashboard::CSS.contains(".dark"));
        assert!(!dashboard::CSS.contains("prefers-color-scheme"));
        assert!(!dashboard::CSS.contains("radial-gradient"));
        assert!(!dashboard::CSS.contains("backdrop-filter"));
        assert!(dashboard::CSS.contains("signal-panel"));
        assert!(dashboard::CSS.contains("secondary-panel"));
        assert!(dashboard::HTML.contains("Diagnostics"));
        assert!(dashboard::HTML.contains("Payload"));
        assert!(dashboard::HTML.contains("payloadBackdrop"));
        assert!(dashboard::JS.contains("openFinding"));
        assert!(dashboard::JS.contains("openPayload"));
        assert!(dashboard::JS.contains("refreshBackendFrame"));
        assert!(dashboard::JS.contains("data-refresh-backend-frame"));
        assert!(dashboard::JS.contains("backendFrameIsReady"));
        assert!(dashboard::JS.contains("scheduleBackendFrameActivation"));
        assert!(dashboard::JS.contains("data-backend-frame-url"));
        assert!(dashboard::JS.contains("src=\"about:blank\""));
        assert!(dashboard::JS.contains("rect.width >= 320"));
        assert!(dashboard::JS.contains("刷新页面"));
        assert!(dashboard::JS.contains("backend-row"));
        assert!(dashboard::CSS.contains("backend-main"));
        assert!(dashboard::JS.contains("openGuideModal"));
        assert!(dashboard::JS.contains("closeGuideModal"));
        assert!(dashboard::JS.contains("openApiDocsModal"));
        assert!(dashboard::JS.contains("closeApiDocsModal"));
        assert!(dashboard::JS.contains("apiDocsText"));
        assert!(dashboard::JS.contains("guideText"));
        assert!(dashboard::JS.contains("renderFindingGroups"));
        assert!(dashboard::JS.contains("expandedFindingTrees"));
        assert!(dashboard::JS.contains("activeFindingGroups"));
        assert!(dashboard::JS.contains("data-select-finding-group"));
        assert!(dashboard::JS.contains("data-select-rule-tree"));
        assert!(!dashboard::JS.contains("data-select-rule-directory"));
        assert!(dashboard::JS.contains("data-tree-open-key"));
        assert!(dashboard::JS.contains("findingPrompt"));
        assert!(dashboard::JS.contains("data-copy-finding-group"));
        assert!(dashboard::JS.contains("data-copy-finding-single"));
        assert!(dashboard::JS.contains("TRACE_INSTRUMENTATION_FIX"));
        assert!(dashboard::JS.contains("RULE_FIX"));
        assert!(dashboard::JS.contains("const SIGNALS"));
        assert!(dashboard::JS.contains("policy_groups"));
        assert!(dashboard::JS.contains("policy_dir"));
        assert!(dashboard::JS.contains("purpose_zh"));
        assert!(dashboard::JS.contains("check_zh"));
        assert!(dashboard::JS.contains("fix_zh"));
        assert!(dashboard::JS.contains("condition_zh"));
        assert!(dashboard::JS.contains("span_kind"));
        assert!(dashboard::JS.contains("spec_id"));
        assert!(!dashboard::JS.contains("directory_title"));
        assert!(!dashboard::JS.contains("directory_readme_md"));
        assert!(dashboard::JS.contains("markdownToHtml"));
        assert!(dashboard::JS.contains("RULE_PURPOSE_ZH"));
        assert!(dashboard::JS.contains("RULE_CHECK_ZH"));
        assert!(dashboard::JS.contains("RULE_FIX_ZH"));
        assert!(dashboard::JS.contains("RULE_CONDITION_ZH"));
        assert!(dashboard::JS.contains("RULE_SPAN_KIND"));
        assert!(dashboard::JS.contains("RULE_SPEC_ID"));
        assert!(dashboard::JS.contains("policyGroupEntries"));
        assert!(dashboard::JS.contains("togglePolicyGroup"));
        assert!(!dashboard::JS.contains("togglePolicySection"));
        assert!(dashboard::JS.contains("expandedRules"));
        assert!(!dashboard::JS.contains("rememberExpandedRules"));
        assert!(dashboard::JS.contains("setHtmlIfChanged"));
        assert!(dashboard::JS.contains("deferWhileScrolling"));
        assert!(dashboard::JS.contains("AUTO_REFRESH_MS"));
        assert!(!dashboard::JS.contains("ruleDetailKey"));
        assert!(dashboard::JS.contains("data-rules-policy-tab"));
        assert!(!dashboard::JS.contains("data-rules-section-tab"));
        assert!(!dashboard::JS.contains("data-policy-section-toggle"));
        assert!(dashboard::JS.contains("renderSignalOverview"));
        assert!(dashboard::JS.contains("renderSignalFindings"));
        assert!(dashboard::JS.contains("renderDiagnosticRows"));
        assert!(!dashboard::JS.contains("renderSignalSpec"));
        assert!(dashboard::JS.contains("buildSemanticRuleMatrix"));
        assert!(dashboard::JS.contains("renderSemanticRuleDetail"));
        assert!(dashboard::JS.contains("semanticConditionForRule"));
        assert!(dashboard::JS.contains("正确请求"));
        assert!(dashboard::JS.contains("错误请求"));
        assert!(dashboard::JS.contains("通用约束"));
        assert!(dashboard::JS.contains("需要补注释"));
        assert!(dashboard::JS.contains("span.kind"));
        assert!(dashboard::JS.contains("renderSignalPayloads"));
        assert!(dashboard::JS.contains("renderRules"));
        assert!(!dashboard::HTML.contains("查看语义字段矩阵"));
        assert!(!dashboard::HTML.contains("openRulesBtn2"));
        assert!(dashboard::HTML.contains("Trace 语义约束"));
        assert!(dashboard::HTML.contains("Event 语义约束"));
        assert!(dashboard::HTML.contains("Metric 语义约束"));
        assert!(dashboard::HTML.contains("Trace Findings"));
        assert!(dashboard::HTML.contains("Trace Findings 分类"));
        assert!(dashboard::HTML.contains("Event Findings 分类"));
        assert!(dashboard::HTML.contains("Metric Findings 分类"));
        assert!(dashboard::HTML.contains("复制全部 Prompt"));
        assert!(dashboard::HTML.contains("复制全部 Payload"));
        assert!(dashboard::HTML.contains("复制 Prompt + Payload"));
        assert!(dashboard::JS.contains("copyPromptPayloadBtn"));
        assert!(dashboard::JS.contains("data-rule-id"));
        assert!(dashboard::JS.contains("data-policy-group-toggle"));
        assert!(dashboard::JS.contains("openRulesModal"));
    }
}
