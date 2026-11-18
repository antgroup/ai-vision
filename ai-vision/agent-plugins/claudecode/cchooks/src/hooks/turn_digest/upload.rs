use agentic_otel_common::otlp::{wrap_logs_export, OtelLogRecord};
use agentic_otel_common::ExportContext;

use crate::common::logger::Logger;
use crate::hooks::langfuse::{resolve_agent_product, resolve_agent_type};

// ── Compile-time constants ──────────────────────────────────────────

#[cfg(feature = "aivision")]
const TURN_DIGEST_HOST_PROD: &str = env!("TURN_DIGEST_BUILTIN_PROD_HOST");

#[cfg(feature = "aivision")]
const TURN_DIGEST_HEADERS_PROD: &str = env!("TURN_DIGEST_BUILTIN_PROD_HEADERS");

// ── URL resolution ──────────────────────────────────────────────────

fn upload_url() -> String {
    #[cfg(feature = "aivision")]
    {
        if !TURN_DIGEST_HOST_PROD.is_empty() {
            return TURN_DIGEST_HOST_PROD.to_string();
        }
    }

    String::new()
}

#[cfg(feature = "aivision")]
fn compiled_headers() -> Option<std::collections::HashMap<String, String>> {
    if TURN_DIGEST_HEADERS_PROD.is_empty() {
        return None;
    }
    serde_json::from_str(TURN_DIGEST_HEADERS_PROD).ok()
}

// ── Public API ──────────────────────────────────────────────────────

/// Build (but do not send) the turn-digest upload. Returns a closure that
/// performs the blocking POST, capturing only owned data so it can run on a
/// worker thread. Returns None when upload is disabled or serialization fails.
///
/// Errors during serialization are emitted to `logger` on the calling (main)
/// thread. The returned closure takes a `&Logger` (cheap clone owned by the
/// worker) and writes send-phase diagnostics directly to disk under LOG_LOCK.
pub fn prepare_log_records_send(
    records: &[OtelLogRecord],
    logger: &Logger,
) -> Option<Box<dyn FnOnce(&Logger) + Send + 'static>> {
    let url = upload_url();
    if url.is_empty() {
        return None;
    }

    let build_version = env!("HOOK_VERSION");
    let agent_env =
        crate::common::hook::env_or_default("CCHOOKS_AGENT_ENV", "AIVISION_AGENT_ENV", "Default");
    let agent_scene = crate::common::hook::env_or_default(
        "CCHOOKS_AGENT_SCENE",
        "AIVISION_AGENT_SCENE",
        "Default",
    );
    let agent_product = resolve_agent_product();
    let agent_type = resolve_agent_type();
    let ctx = ExportContext {
        agent_product: &agent_product,
        agent_type: &agent_type,
        sdk_name: "cchooks",
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

    let record_count = records.len();
    let export_request = wrap_logs_export(records.to_vec(), &ctx);

    let body = match serde_json::to_vec(&export_request) {
        Ok(b) => b,
        Err(e) => {
            logger.debug(&format!("[turn_digest] serialize error: {}", e));
            return None;
        }
    };

    // Resolve owned headers up front.
    let mut headers: Vec<(String, String)> =
        vec![("Content-Type".to_string(), "application/json".to_string())];
    #[cfg(feature = "aivision")]
    {
        if let Some(custom) = compiled_headers() {
            for (key, value) in &custom {
                headers.push((key.clone(), value.clone()));
            }
        }
    }

    let agent = crate::common::hook::build_http_agent(5);

    Some(Box::new(move |logger: &Logger| {
        let started = std::time::Instant::now();
        let result = crate::common::hook::retry_once(
            || {
                let mut req = agent.post(&url);
                for (k, v) in &headers {
                    req = req.header(k, v);
                }
                req.send(&body[..])
            },
            |error| {
                logger.warn(&format!(
                    "[turn_digest] upload failed, retrying once: {}",
                    error
                ))
            },
        );
        let ms = started.elapsed().as_millis();
        match result {
            Ok(_) => logger.info(&format!(
                "[turn_digest] uploaded {} records in {}ms to {}",
                record_count, ms, url
            )),
            Err(e) => logger.info(&format!(
                "[turn_digest] upload failed after {}ms: {}",
                ms, e
            )),
        }
    }))
}
