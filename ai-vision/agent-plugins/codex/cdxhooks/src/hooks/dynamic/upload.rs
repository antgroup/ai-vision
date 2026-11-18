//! Upload dynamic data collection results via OTel LogRecord.

use agentic_otel_common::otlp::{wrap_logs_export, OtelLogRecord};
use agentic_otel_common::ExportContext;

use crate::common::logger::Logger;
use crate::hooks::langfuse::{resolve_agent_product, resolve_agent_type};

/// Resolve upload URL from environment variable.
fn upload_url() -> String {
    if let Ok(url) = std::env::var("CDXHOOKS_DYNAMIC_UPLOAD_URL") {
        if !url.is_empty() {
            return url;
        }
    }
    String::new()
}

/// POST a single OTel LogRecord wrapped in ExportLogsServiceRequest.
/// Errors are logged but never propagated (fire-and-forget).
pub fn post_log_record(record: &OtelLogRecord, logger: &Logger) {
    let url = upload_url();
    if url.is_empty() {
        return;
    }

    let build_version = env!("HOOK_VERSION");
    let agent_env =
        agentic_otel_common::env_or_default("CDXHOOKS_AGENT_ENV", "AIVISION_AGENT_ENV", "Default");
    let agent_scene = agentic_otel_common::env_or_default(
        "CDXHOOKS_AGENT_SCENE",
        "AIVISION_AGENT_SCENE",
        "Default",
    );
    let agent_product = resolve_agent_product();
    let agent_type = resolve_agent_type();
    let ctx = ExportContext {
        agent_product: &agent_product,
        agent_type: &agent_type,
        sdk_name: "cdxhooks",
        build_version,
        agent_env: &agent_env,
        agent_scene: &agent_scene,
        collection_mode: if crate::common::otel::is_privacy_mode() {
            "privacy"
        } else {
            "full"
        },
        emit_meta_trigger: true,
    };

    let export_request = wrap_logs_export(vec![record.clone()], &ctx);

    let body = match serde_json::to_vec(&export_request) {
        Ok(b) => b,
        Err(e) => {
            logger.debug(&format!("[dynamic] serialize error: {}", e));
            return;
        }
    };

    let agent = crate::common::hook::build_http_agent(10);

    let response = crate::common::hook::retry_once(
        || {
            agent
                .post(&url)
                .header("Content-Type", "application/json")
                .send(&body[..])
        },
        |error| {
            logger.warn(&format!(
                "[dynamic] upload failed, retrying once: {}",
                error
            ))
        },
    );

    match response {
        Ok(_) => logger.debug(&format!("[dynamic] uploaded to {}", url)),
        Err(e) => logger.debug(&format!("[dynamic] upload failed: {}", e)),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use agentic_otel_common::otlp::OtelAttribute;
    use serde_json::Value;

    #[test]
    fn cdxhooks_dynamic_log_resource_emits_meta_trigger_from_component_fallback() {
        let _guard = crate::test_sync::env_lock();
        let old_resource_attrs = std::env::var("CDXHOOKS_OTEL_RESOURCE_ATTRIBUTES").ok();
        let old_meta_trigger = std::env::var("AIVISION_META_TRIGGER").ok();
        std::env::remove_var("AIVISION_META_TRIGGER");
        std::env::set_var(
            "CDXHOOKS_OTEL_RESOURCE_ATTRIBUTES",
            "ant.meta.trigger=interactive,ant.meta.trigger=automation",
        );

        let ctx = ExportContext {
            agent_product: "codex",
            agent_type: "cdxhooks",
            sdk_name: "cdxhooks",
            build_version: "1.0.0",
            agent_env: "Default",
            agent_scene: "Default",
            collection_mode: "full",
            emit_meta_trigger: true,
        };
        let record = OtelLogRecord::event(
            "dynamic.snapshot",
            "100",
            vec![OtelAttribute::string("event.domain", "dynamic")],
        );
        let payload = wrap_logs_export(vec![record], &ctx);
        let attrs = resource_attrs(&payload);
        let triggers = trigger_values(attrs);

        restore_env("CDXHOOKS_OTEL_RESOURCE_ATTRIBUTES", old_resource_attrs);
        restore_env("AIVISION_META_TRIGGER", old_meta_trigger);

        assert_eq!(triggers, vec!["automation"]);
        assert_eq!(attr_string(attrs, "telemetry.sdk.name"), Some("cdxhooks"));
    }

    fn restore_env(key: &str, value: Option<String>) {
        match value {
            Some(value) => std::env::set_var(key, value),
            None => std::env::remove_var(key),
        }
    }

    fn resource_attrs(payload: &Value) -> &[Value] {
        payload["resourceLogs"][0]["resource"]["attributes"]
            .as_array()
            .expect("resource attrs")
    }

    fn trigger_values(attrs: &[Value]) -> Vec<&str> {
        attrs
            .iter()
            .filter(|attr| attr["key"].as_str() == Some("ant.meta.trigger"))
            .filter_map(|attr| attr["value"]["stringValue"].as_str())
            .collect()
    }

    fn attr_string<'a>(attrs: &'a [Value], key: &str) -> Option<&'a str> {
        attrs
            .iter()
            .find(|attr| attr["key"].as_str() == Some(key))
            .and_then(|attr| attr["value"]["stringValue"].as_str())
    }
}
