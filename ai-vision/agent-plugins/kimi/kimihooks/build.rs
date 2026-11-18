//! Compile-time configuration injection.
//!
//! 双文件架构（keys/config 分离）：
//! - **keys 文件**（`aivision.keys.json`）：只含密钥（pk/sk/host），仅 aivision edition 读取
//! - **config 文件**（`configs/{edition}.config.json`）：非敏感默认值（endpoints/headers/脱敏开关等）
//!
//! 注入的环境变量（`env!()` 在 `edition.rs` 侧消费）：
//!
//! **Keys（仅 aivision）**：
//! | 变量名                                    | 来源 keys.json 字段        |
//! |-------------------------------------------|----------------------------|
//! | `KIMI_BUILTIN_PROD_PUBLIC_KEY`         | `prod_public_key`          |
//! | `KIMI_BUILTIN_PROD_SECRET_KEY`         | `prod_secret_key`          |
//! | `KIMI_BUILTIN_PROD_TRACES_ENDPOINT`    | `prod_host`                |
//! | `KIMI_BUILTIN_GRAY_PUBLIC_KEY`         | `gray_public_key`          |
//! | `KIMI_BUILTIN_GRAY_SECRET_KEY`         | `gray_secret_key`          |
//! | `KIMI_BUILTIN_GRAY_TRACES_ENDPOINT`    | `gray_host`                |
//!
//! **Config（两个 edition 都读）**：
//! | 变量名                                    | 来源 config.json 字段                  |
//! |-------------------------------------------|----------------------------------------|
//! | `KIMI_BUILTIN_ENABLE_TELEMETRY`        | `enableTelemetry`                      |
//! | `KIMI_BUILTIN_SERVICE_NAME`            | `serviceName`                          |
//! | `KIMI_BUILTIN_AGENT_TYPE`              | `ant.agentType`                        |
//! | `KIMI_BUILTIN_AGENT_PRODUCT`           | `ant.agentProduct`                     |
//! | `KIMI_BUILTIN_OTEL_PROTOCOL`           | `otelExporter.protocol`                |
//! | `KIMI_BUILTIN_METRICS_ENDPOINT`        | `metricsConfig.endpoint`               |
//! | `KIMI_BUILTIN_LOGS_ENDPOINT`           | `logsConfig.endpoint`                  |
//! | `KIMI_BUILTIN_STATUS_ENDPOINT`         | `statusConfig.endpoint`                |
//! | `KIMI_BUILTIN_METRICS_HEADERS`         | `metricsConfig.headers` (JSON)         |
//! | `KIMI_BUILTIN_LOGS_HEADERS`            | `logsConfig.headers` (JSON)            |
//! | `KIMI_BUILTIN_STATUS_HEADERS`          | `statusConfig.headers` (JSON)          |
//! | `KIMI_BUILTIN_LOG_SPANS_*`             | `log.spans.*` (bool)                   |
//! | `KIMI_BUILTIN_LOG_EVENTS_*`            | `log.events.*` (bool)                  |

use std::collections::HashMap;

fn main() {
    println!("cargo:rerun-if-env-changed=CARGO_FEATURE_AIVISION");

    let has_aivision = std::env::var("CARGO_FEATURE_AIVISION").is_ok();

    // ── 1. Keys file (aivision only) ──────────────────────────────────
    if has_aivision {
        let keys_file = "aivision.keys.json";
        println!("cargo:rerun-if-changed={}", keys_file);

        let keys_content = std::fs::read_to_string(keys_file).unwrap_or_else(|e| {
            panic!(
                "{} is required for the `aivision` feature ({}). \
                 See kimi/kimihooks/aivision.keys.json.",
                keys_file, e
            )
        });

        let keys_json: serde_json::Value = serde_json::from_str(&keys_content)
            .unwrap_or_else(|e| panic!("Failed to parse {}: {}", keys_file, e));

        // Keys: only pk/sk/host
        let extract_required = |key: &str| -> String {
            keys_json[key]
                .as_str()
                .unwrap_or_else(|| panic!("{} missing required key: {}", keys_file, key))
                .to_string()
        };
        let extract_optional = |key: &str| -> String {
            keys_json[key].as_str().unwrap_or("").to_string()
        };

        emit_env(
            "KIMI_BUILTIN_PROD_PUBLIC_KEY",
            &extract_optional("prod_public_key"),
        );
        emit_env(
            "KIMI_BUILTIN_PROD_SECRET_KEY",
            &extract_optional("prod_secret_key"),
        );
        emit_env(
            "KIMI_BUILTIN_PROD_TRACES_ENDPOINT",
            &extract_required("prod_host"),
        );
        emit_env(
            "KIMI_BUILTIN_GRAY_PUBLIC_KEY",
            &extract_optional("gray_public_key"),
        );
        emit_env(
            "KIMI_BUILTIN_GRAY_SECRET_KEY",
            &extract_optional("gray_secret_key"),
        );
        emit_env(
            "KIMI_BUILTIN_GRAY_TRACES_ENDPOINT",
            &extract_optional("gray_host"),
        );
    } else {
        // common edition: no keys
        for var in [
            "KIMI_BUILTIN_PROD_PUBLIC_KEY",
            "KIMI_BUILTIN_PROD_SECRET_KEY",
            "KIMI_BUILTIN_PROD_TRACES_ENDPOINT",
            "KIMI_BUILTIN_GRAY_PUBLIC_KEY",
            "KIMI_BUILTIN_GRAY_SECRET_KEY",
            "KIMI_BUILTIN_GRAY_TRACES_ENDPOINT",
        ] {
            emit_env(var, "");
        }
    }

    // ── 2. Config file (both editions) ────────────────────────────────
    let config_file = if has_aivision {
        "configs/aivision.config.json"
    } else {
        "configs/common.config.json"
    };
    println!("cargo:rerun-if-changed={}", config_file);

    let config_content = std::fs::read_to_string(config_file)
        .unwrap_or_else(|e| panic!("Failed to read {}: {}", config_file, e));
    let config_json: serde_json::Value = serde_json::from_str(&config_content)
        .unwrap_or_else(|e| panic!("Failed to parse {}: {}", config_file, e));

    // Simple field extraction helpers
    let get_bool = |path: &[&str]| -> bool {
        let mut node = &config_json;
        for key in path {
            node = node.get(key).unwrap_or(&serde_json::Value::Null);
        }
        node.as_bool().unwrap_or(false)
    };
    let get_str = |path: &[&str]| -> String {
        let mut node = &config_json;
        for key in path {
            node = node.get(key).unwrap_or(&serde_json::Value::Null);
        }
        node.as_str().unwrap_or("").to_string()
    };
    let get_headers_json = |path: &[&str]| -> String {
        let mut node = &config_json;
        for key in path {
            node = node.get(key).unwrap_or(&serde_json::Value::Null);
        }
        if node.is_object() {
            let map: HashMap<String, String> = node
                .as_object()
                .unwrap()
                .iter()
                .filter_map(|(k, v)| v.as_str().map(|s| (k.clone(), s.to_string())))
                .collect();
            serde_json::to_string(&map).unwrap_or_default()
        } else {
            String::new()
        }
    };

    // Top-level
    emit_env("KIMI_BUILTIN_ENABLE_TELEMETRY", if get_bool(&["enableTelemetry"]) { "true" } else { "false" });
    emit_env("KIMI_BUILTIN_SERVICE_NAME", &get_str(&["serviceName"]));

    // ant.*
    emit_env("KIMI_BUILTIN_AGENT_TYPE", &get_str(&["ant", "agentType"]));
    emit_env("KIMI_BUILTIN_AGENT_PRODUCT", &get_str(&["ant", "agentProduct"]));

    // otelExporter.*
    emit_env("KIMI_BUILTIN_OTEL_PROTOCOL", &get_str(&["otelExporter", "protocol"]));

    // Per-signal endpoints + headers
    emit_env("KIMI_BUILTIN_METRICS_ENDPOINT", &get_str(&["metricsConfig", "endpoint"]));
    emit_env("KIMI_BUILTIN_LOGS_ENDPOINT", &get_str(&["logsConfig", "endpoint"]));
    emit_env("KIMI_BUILTIN_STATUS_ENDPOINT", &get_str(&["statusConfig", "endpoint"]));

    emit_env("KIMI_BUILTIN_METRICS_HEADERS", &get_headers_json(&["metricsConfig", "headers"]));
    emit_env("KIMI_BUILTIN_LOGS_HEADERS", &get_headers_json(&["logsConfig", "headers"]));
    emit_env("KIMI_BUILTIN_STATUS_HEADERS", &get_headers_json(&["statusConfig", "headers"]));

    // traces headers: aivision has prod_headers in config (under tracesConfig or top-level)
    // For aivision, traces auth comes from langfuse pk/sk (basic auth), plus any forwarding headers.
    // The "prod_headers" that was in the old keys.json for tracing is now under tracesConfig.headers
    // or defaults to the same forwarding header as metrics/logs.
    emit_env("KIMI_BUILTIN_TRACES_HEADERS", &get_headers_json(&["tracesConfig", "headers"]));

    // log.spans.*
    emit_env("KIMI_BUILTIN_LOG_SPANS_USER_PROMPTS", if get_bool(&["log", "spans", "userPrompts"]) { "true" } else { "false" });
    emit_env("KIMI_BUILTIN_LOG_SPANS_MODEL_OUTPUTS", if get_bool(&["log", "spans", "modelOutputs"]) { "true" } else { "false" });
    emit_env("KIMI_BUILTIN_LOG_SPANS_TOOL_DETAILS", if get_bool(&["log", "spans", "toolDetails"]) { "true" } else { "false" });
    emit_env("KIMI_BUILTIN_LOG_SPANS_TOOL_OUTPUTS", if get_bool(&["log", "spans", "toolOutputs"]) { "true" } else { "false" });

    // log.events.*
    emit_env("KIMI_BUILTIN_LOG_EVENTS_USER_PROMPTS", if get_bool(&["log", "events", "userPrompts"]) { "true" } else { "false" });
    emit_env("KIMI_BUILTIN_LOG_EVENTS_MODEL_OUTPUTS", if get_bool(&["log", "events", "modelOutputs"]) { "true" } else { "false" });
    emit_env("KIMI_BUILTIN_LOG_EVENTS_TOOL_DETAILS", if get_bool(&["log", "events", "toolDetails"]) { "true" } else { "false" });
    emit_env("KIMI_BUILTIN_LOG_EVENTS_TOOL_OUTPUTS", if get_bool(&["log", "events", "toolOutputs"]) { "true" } else { "false" });
}

fn emit_env(key: &str, value: &str) {
    // cargo:rustc-env 不允许 value 含换行；JSON 序列化结果天然单行，OK。
    println!("cargo:rustc-env={}={}", key, value);
}