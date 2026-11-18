//! HTTP exporter — 把 OTLP traces / logs / metrics 通过 HTTP POST 上报到远端。
//!
//! 设计原则
//! --------
//! 1. **fail-open**：HTTP 失败永不影响 hook 退出码，仅写日志放弃；超时 10s + 1 次重试。
//! 2. **复用 cchooks 模式**：`ureq` agent + `PlatformVerifier` 根证书 + `AIVISION_TLS_NOVERIFY=true` 兜底。
//! 3. **复用 otel-common**：调用方负责构造 `wrap_trace_export / wrap_logs_export / wrap_metrics_export`，
//!    本模块只负责拿到 JSON Value 后 POST 出去。
//! 4. **一个总开关**：`KIMI_DISABLE_HTTP=true` → 全部 HTTP 上报跳过（本地排障常用）。
//!
//! 信号 → 鉴权策略（**完全独立，禁止共享 Authorization**）
//! ---------------------------------------------------------
//! - **traces**（langfuse 兼容 endpoint）：`Authorization: Basic base64(public_key:secret_key)`
//!   + 强制 `x-langfuse-ingestion-version: 4`（langfuse 协议必需，与 cchooks 一致）
//!   + `cfg.headers`（来自 `prod_headers`，用于自定义通用请求头）
//! - **logs**（单独配置的 OTLP collector）：仅用 `cfg.logs_headers`
//!   （来自 `prod_logs_headers`，按目标服务配置认证请求头）
//! - **metrics**（同上）：仅用 `cfg.metrics_headers`
//!
//! 历史教训：traces 和 logs/metrics 的 Authorization 不能共享 —— 把 langfuse 的
//! pk/sk Basic auth 套到 OpenObserve 会 401，反过来一样。所以三套 headers 严格隔离。
//!
//! URL 解析（与 cchooks `resolve_otel_url` 同源）
//! ----------------------------------------------
//! - `traces_endpoint = "https://langfuse.example.com"` → 自动追加 `/api/public/otel/v1/traces`
//! - `traces_endpoint = "https://proxy/v1/traces"`（已带 path）→ 原样使用
//! - logs / metrics 永远原样使用（OpenObserve 等 endpoint 路径已完整指定）
//!
//! Debug payload dump
//! ------------------
//! `KIMI_DEBUG=true` 时，每次 traces/logs/metrics 上报会把 outgoing body 写到
//! `~/.kimi/observ/logs/otel_body_debug_<signal>.json`，可直接用 curl 重放调试。
//! 失败的请求即使没开 DEBUG 也会被 dump（命名加 `_failed_<status>`）。

use std::path::PathBuf;
use std::time::Duration;

use base64::Engine;
use serde_json::Value;

use crate::common::dirs;
use crate::common::edition::EditionConfig;
use crate::common::logger::Logger;

/// HTTP 总超时（秒）。kimi-cli 的 Stop hook timeout 为 30s，
/// 三个信号（traces/logs/metrics）串行上报，每个最多 5s × 2 尝试 = 10s，
/// 总计最多 30s，确保不超过 hook timeout。
const HTTP_TIMEOUT_SECS: u64 = 5;

/// 失败重试次数（总尝试次数 = 1 + RETRIES）。
const RETRIES: u32 = 1;

/// 控制是否完全禁用 HTTP 上报（环境变量优先）。
pub fn http_disabled() -> bool {
    std::env::var("KIMI_DISABLE_HTTP")
        .ok()
        .map(|v| v.eq_ignore_ascii_case("true") || v == "1")
        .unwrap_or(false)
}

/// 构造一个带 PlatformVerifier TLS + 全局 timeout 的 ureq Agent。
/// 当 `AIVISION_TLS_NOVERIFY=true` 时关闭证书校验（与 cchooks 行为完全一致）。
///
/// 关键：`http_status_as_error(false)` 让 4xx/5xx 进入 `Ok(resp)` 分支，
/// 我们才能读到 response body（langfuse / OpenObserve 在 body 里给具体错误说明）。
/// ureq 3.x 默认会把 4xx/5xx 当作 `Err` 直接抛，会丢掉 response body。
fn build_agent() -> ureq::Agent {
    let mut tls_builder =
        ureq::tls::TlsConfig::builder().root_certs(ureq::tls::RootCerts::PlatformVerifier);
    if std::env::var("AIVISION_TLS_NOVERIFY")
        .ok()
        .map(|v| v.eq_ignore_ascii_case("true"))
        .unwrap_or(false)
    {
        tls_builder = tls_builder.disable_verification(true);
    }
    let config = ureq::Agent::config_builder()
        .timeout_global(Some(Duration::from_secs(HTTP_TIMEOUT_SECS)))
        .http_status_as_error(false)
        .tls_config(tls_builder.build())
        .build();
    ureq::Agent::new_with_config(config)
}

/// `Authorization: Basic base64(public_key:secret_key)` —— Langfuse OTLP endpoint 用。
fn basic_auth(public_key: &str, secret_key: &str) -> String {
    let token = format!("{}:{}", public_key, secret_key);
    let b64 = base64::engine::general_purpose::STANDARD.encode(token.as_bytes());
    format!("Basic {}", b64)
}

/// 截断过长的 response body，避免日志被冲爆。保留首部 + 截断标记。
fn truncate_for_log(s: &str, max_chars: usize) -> String {
    if s.chars().count() <= max_chars {
        return s.to_string();
    }
    let head: String = s.chars().take(max_chars).collect();
    format!("{}...[TRUNCATED total_chars={}]", head, s.chars().count())
}

/// 与 cchooks `resolve_otel_url` 完全同源：
/// - host 已带 URI path（scheme://.../...）→ 原样使用
/// - host 是裸域名（scheme://host[:port]）→ 追加 `default_path`
///
/// 这是 langfuse OTLP endpoint 的关键约定：
/// 直接 POST 到 langfuse 域名根路径会被网关 400 拒绝，必须命中 `/api/public/otel/v1/traces`。
pub(crate) fn resolve_otel_url(host: &str, default_path: &str) -> String {
    let trimmed = host.trim_end_matches('/');
    if let Some(scheme_end) = trimmed.find("://") {
        let after_scheme = &trimmed[scheme_end + 3..];
        if after_scheme.contains('/') {
            return trimmed.to_string();
        }
    }
    format!("{}{}", trimmed, default_path)
}

/// `KIMI_DEBUG=true` 时为 true。控制 dump 行为。
fn debug_enabled() -> bool {
    std::env::var("KIMI_DEBUG")
        .ok()
        .map(|v| v.eq_ignore_ascii_case("true") || v == "1")
        .unwrap_or(false)
}

/// 把一个 OTLP request 写到 `~/.kimi/observ/logs/otel_body_debug_<tag>.json`。
/// 用于：(a) DEBUG 模式正常上报；(b) 失败重放（不依赖 DEBUG）。
fn dump_payload(body: &[u8], tag: &str, logger: &Logger) -> Option<PathBuf> {
    let dir = dirs::log_dir();
    if let Err(e) = dirs::ensure_dir(&dir) {
        logger.warn(&format!("[http] dump 目录创建失败: {}", e));
        return None;
    }
    let path = dir.join(format!("otel_body_debug_{}.json", tag));
    // 漂亮打印一下，方便人眼读
    let pretty = serde_json::from_slice::<Value>(body)
        .ok()
        .and_then(|v| serde_json::to_vec_pretty(&v).ok())
        .unwrap_or_else(|| body.to_vec());
    if let Err(e) = std::fs::write(&path, &pretty) {
        logger.warn(&format!("[http] dump 写入 {} 失败: {}", path.display(), e));
        return None;
    }
    Some(path)
}

/// 通用 POST 入口。fail-open：失败只写日志，不返回错误。
///
/// `auth_header` 为 `None` 时不附加 Authorization；为 `Some(value)` 时按字面量写入。
/// `extra_headers` 在 Authorization 之后写入（同 key 覆盖之）。
/// `mandatory_headers` 是信号特定的强制 header（如 langfuse 的 `x-langfuse-ingestion-version: 4`），
///   优先级最高，**最后写入**确保不被 extra_headers 覆盖。
fn post_json(
    agent: &ureq::Agent,
    url: &str,
    body: &[u8],
    auth_header: Option<&str>,
    extra_headers: &std::collections::HashMap<String, String>,
    mandatory_headers: &[(&str, &str)],
    logger: &Logger,
    signal: &str,
) {
    // DEBUG 模式：每次都 dump outgoing payload，便于 curl 重放。
    if debug_enabled() {
        if let Some(p) = dump_payload(body, signal, logger) {
            logger.info(&format!("[http] {} payload dumped: {}", signal, p.display()));
        }
    }

    for attempt in 0..=RETRIES {
        let mut req = agent.post(url).header("Content-Type", "application/json");
        if let Some(value) = auth_header {
            req = req.header("Authorization", value);
        }
        for (k, v) in extra_headers {
            // 不允许 extra_headers 覆盖已设的 Content-Type；但 Authorization 允许覆盖
            // （prod_headers 中的 Basic ... 是 metrics/logs 的合法鉴权来源）。
            if k.eq_ignore_ascii_case("content-type") {
                continue;
            }
            req = req.header(k.as_str(), v.as_str());
        }
        // 信号强制 header 最后写：覆盖任何同名 extra_headers 值。
        for (k, v) in mandatory_headers {
            req = req.header(*k, *v);
        }
        match req.send(body) {
            Ok(mut resp) => {
                let status = resp.status();
                if status.is_success() {
                    logger.info(&format!(
                        "[http] {} POST {} -> {} (bytes={}, attempt={})",
                        signal,
                        url,
                        status.as_u16(),
                        body.len(),
                        attempt + 1
                    ));
                    return;
                } else {
                    // 非 2xx：尽力把 response body 读出来一并记录，方便排查
                    // （langfuse / OpenObserve 出错时通常会在 body 里给 JSON message）。
                    let body_snippet = match resp.body_mut().read_to_string() {
                        Ok(s) => truncate_for_log(&s, 1024),
                        Err(e) => format!("<read body failed: {}>", e),
                    };
                    // 失败时无条件 dump outgoing payload（带状态码 tag），便于 curl 重放。
                    let dump_tag = format!("{}_failed_{}", signal, status.as_u16());
                    let dump_path = dump_payload(body, &dump_tag, logger);
                    logger.warn(&format!(
                        "[http] {} POST {} -> non-2xx status={} (attempt={}) body={} dump={}",
                        signal,
                        url,
                        status.as_u16(),
                        attempt + 1,
                        body_snippet,
                        dump_path.as_ref().map(|p| p.display().to_string()).unwrap_or_else(|| "<none>".to_string())
                    ));
                    // 4xx 不重试（鉴权/请求错误）；5xx 才重试。
                    if status.is_client_error() {
                        return;
                    }
                }
            }
            Err(e) => {
                // 兜底 dump：transport 层错误（DNS / TLS / connect refused 等）
                // 也把 outgoing payload 落盘，便于离线 curl 重放。
                let dump_tag = format!("{}_transport_err", signal);
                let dump_path = dump_payload(body, &dump_tag, logger);
                logger.warn(&format!(
                    "[http] {} POST {} failed: {} (attempt={}) dump={}",
                    signal,
                    url,
                    e,
                    attempt + 1,
                    dump_path
                        .as_ref()
                        .map(|p| p.display().to_string())
                        .unwrap_or_else(|| "<none>".to_string())
                ));
            }
        }
    }
    logger.warn(&format!(
        "[http] {} POST {} 全部重试失败，放弃本次上报（fail-open）",
        signal, url
    ));
}

// ── 三个公开入口 ───────────────────────────────────────────────────

/// POST traces (`ExportTraceServiceRequest` JSON value) 到 langfuse-compatible endpoint。
///
/// 关键行为（与 cchooks `post_otel_to_langfuse` 完全对齐）：
/// 1. URL 经 `resolve_otel_url` 处理：裸 host 自动追加 `/api/public/otel/v1/traces`
/// 2. 鉴权：`Authorization: Basic base64(public_key:secret_key)`
/// 3. 强制 header：`x-langfuse-ingestion-version: 4`（langfuse 协议要求）
/// 4. 额外 header：`EditionConfig.headers`（自定义通用请求头）
pub fn post_traces(cfg: &EditionConfig, request: &Value, logger: &Logger) {
    if http_disabled() {
        logger.info("[http] traces skipped: KIMI_DISABLE_HTTP=true");
        return;
    }
    let raw_host = match cfg.traces_endpoint.as_deref() {
        Some(u) if !u.is_empty() => u,
        _ => {
            logger.info("[http] traces skipped: traces_endpoint 未配置");
            return;
        }
    };
    // 关键修复：resolve URL（host 不带 path 自动追加 /api/public/otel/v1/traces）。
    let url = resolve_otel_url(raw_host, "/api/public/otel/v1/traces");
    if url != raw_host {
        logger.info(&format!("[http] traces URL resolved: {} -> {}", raw_host, url));
    }
    let auth = match (cfg.public_key.as_deref(), cfg.secret_key.as_deref()) {
        (Some(pk), Some(sk)) if !pk.is_empty() && !sk.is_empty() => Some(basic_auth(pk, sk)),
        _ => {
            // 没有 Langfuse 双密钥时可使用已配置的 prod_headers.Authorization。
            None
        }
    };
    let body = match serde_json::to_vec(request) {
        Ok(b) => b,
        Err(e) => {
            logger.warn(&format!("[http] traces serialize failed: {}", e));
            return;
        }
    };
    let agent = build_agent();
    // langfuse 强制版本 header（与 cchooks 一致）。
    let mandatory: &[(&str, &str)] = &[("x-langfuse-ingestion-version", "4")];
    post_json(
        &agent,
        &url,
        &body,
        auth.as_deref(),
        &cfg.headers,
        mandatory,
        logger,
        "traces",
    );
}

/// POST logs (`ExportLogsServiceRequest` JSON value) 到 logs endpoint。
/// 鉴权策略：使用 `cfg.logs_headers["Authorization"]`（OpenObserve 的 Basic auth），
/// 与 traces 的 langfuse pk/sk 完全独立，禁止共享。
pub fn post_logs(cfg: &EditionConfig, request: &Value, logger: &Logger) {
    if http_disabled() {
        logger.info("[http] logs skipped: KIMI_DISABLE_HTTP=true");
        return;
    }
    let url = match cfg.logs_endpoint.as_deref() {
        Some(u) if !u.is_empty() => u.to_string(),
        _ => {
            logger.info("[http] logs skipped: logs_endpoint 未配置");
            return;
        }
    };
    let body = match serde_json::to_vec(request) {
        Ok(b) => b,
        Err(e) => {
            logger.warn(&format!("[http] logs serialize failed: {}", e));
            return;
        }
    };
    let agent = build_agent();
    post_json(
        &agent,
        &url,
        &body,
        None,
        &cfg.logs_headers,
        &[],
        logger,
        "logs",
    );
}

/// POST metrics (`ExportMetricsServiceRequest` JSON value) 到 metrics endpoint。
/// 鉴权策略：使用 `cfg.metrics_headers["Authorization"]`（OpenObserve 的 Basic auth）。
pub fn post_metrics(cfg: &EditionConfig, request: &Value, logger: &Logger) {
    if http_disabled() {
        logger.info("[http] metrics skipped: KIMI_DISABLE_HTTP=true");
        return;
    }
    let url = match cfg.metrics_endpoint.as_deref() {
        Some(u) if !u.is_empty() => u.to_string(),
        _ => {
            logger.info("[http] metrics skipped: metrics_endpoint 未配置");
            return;
        }
    };
    let body = match serde_json::to_vec(request) {
        Ok(b) => b,
        Err(e) => {
            logger.warn(&format!("[http] metrics serialize failed: {}", e));
            return;
        }
    };
    let agent = build_agent();
    post_json(
        &agent,
        &url,
        &body,
        None,
        &cfg.metrics_headers,
        &[],
        logger,
        "metrics",
    );
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn basic_auth_encodes_pk_sk() {
        // base64("pk-x:sk-y") = "cGsteDpzay15"
        assert_eq!(basic_auth("pk-x", "sk-y"), "Basic cGsteDpzay15");
    }

    #[test]
    fn http_disabled_respects_env() {
        let key = "KIMI_DISABLE_HTTP";
        let prev = std::env::var(key).ok();
        std::env::set_var(key, "true");
        assert!(http_disabled());
        std::env::set_var(key, "1");
        assert!(http_disabled());
        std::env::set_var(key, "false");
        assert!(!http_disabled());
        std::env::remove_var(key);
        assert!(!http_disabled());
        if let Some(v) = prev {
            std::env::set_var(key, v);
        }
    }

    #[test]
    fn resolve_otel_url_appends_path_for_bare_host() {
        // 裸 host：必须追加 default_path
        assert_eq!(
            resolve_otel_url("https://langfuse.example.com", "/api/public/otel/v1/traces"),
            "https://langfuse.example.com/api/public/otel/v1/traces"
        );
        // 末尾斜杠应被裁掉
        assert_eq!(
            resolve_otel_url("https://langfuse.example.com/", "/api/public/otel/v1/traces"),
            "https://langfuse.example.com/api/public/otel/v1/traces"
        );
    }

    #[test]
    fn resolve_otel_url_keeps_host_with_existing_path() {
        // 已带 path：原样使用，不再追加
        assert_eq!(
            resolve_otel_url(
                "https://langfuse.example.com/api/public/otel/v1/traces",
                "/api/public/otel/v1/traces"
            ),
            "https://langfuse.example.com/api/public/otel/v1/traces"
        );
        assert_eq!(
            resolve_otel_url("https://proxy.internal/v1/traces", "/api/public/otel/v1/traces"),
            "https://proxy.internal/v1/traces"
        );
        // 真实 aivision.keys.json::prod_host 走的就是这条分支
        assert_eq!(
            resolve_otel_url(
                "http://localhost:4318/v1/traces",
                "/api/public/otel/v1/traces"
            ),
            "http://localhost:4318/v1/traces"
        );
    }
}
