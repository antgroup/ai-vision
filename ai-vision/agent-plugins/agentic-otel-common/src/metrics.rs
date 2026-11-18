//! Lightweight in-memory metrics collector with OTLP JSON push.
//!
//! Designed for short-lived processes (hooks): collect counters during
//! execution, then flush once before process exit via HTTP POST.

use crate::otlp::{wrap_metrics_export, OtelAttribute, OtelMetric, OtelNumberDataPoint, OtelSum};
use crate::ExportContext;

#[cfg(feature = "metrics")]
const HTTP_TIMEOUT_SECS: u64 = 5;

/// Merge built-in JSON headers with runtime OTLP headers. Runtime values win;
/// HTTP header names are case-insensitive, including Authorization.
pub fn resolve_headers(builtin_json: &str, runtime_headers: &str) -> Vec<(String, String)> {
    let builtin = serde_json::from_str::<std::collections::BTreeMap<String, String>>(builtin_json)
        .unwrap_or_default();
    builtin
        .into_iter()
        .chain(crate::otlp::parse_http_headers(runtime_headers))
        .map(|(key, value)| (key.to_ascii_lowercase(), value))
        .collect::<std::collections::BTreeMap<_, _>>()
        .into_iter()
        .collect()
}

#[cfg(feature = "metrics")]
fn build_metrics_agent() -> ureq::Agent {
    let mut builder = ureq::Agent::config_builder()
        .timeout_global(Some(std::time::Duration::from_secs(HTTP_TIMEOUT_SECS)));
    let mut tls_builder =
        ureq::tls::TlsConfig::builder().root_certs(ureq::tls::RootCerts::PlatformVerifier);
    if crate::hook::env_equals_ignore_case("AIVISION_TLS_NOVERIFY", "true") {
        tls_builder = tls_builder.disable_verification(true);
    }
    builder = builder.tls_config(tls_builder.build());
    builder.build().new_agent()
}

/// Resource-level attributes attached to every metric data point.
pub struct ResourceAttrs {
    pub username: String,
    pub agent_product: String,
    pub agent_type: String,
    pub sdk_name: String,
    pub agent_instance: String,
    pub hook_version: String,
    pub agent_scene: String,
    pub agent_env: String,
    pub collection_mode: String,
    pub emit_meta_trigger: bool,
}

/// A single counter increment record.
struct CounterRecord {
    name: String,
    description: String,
    unit: String,
    value: i64,
    attributes: Vec<OtelAttribute>,
}

/// Collects counter increments in memory, flushes as OTLP Metrics JSON.
pub struct MetricsCollector {
    #[cfg_attr(not(feature = "metrics"), allow(dead_code))]
    endpoint: String,
    counters: Vec<CounterRecord>,
    resource: ResourceAttrs,
    /// Optional HTTP headers to include in the metrics POST (e.g. prod_headers from keys.json).
    #[cfg_attr(not(feature = "metrics"), allow(dead_code))]
    headers: Vec<(String, String)>,
}

impl MetricsCollector {
    pub fn new(endpoint: String, resource: ResourceAttrs) -> Self {
        Self {
            endpoint,
            counters: Vec::new(),
            resource,
            headers: Vec::new(),
        }
    }

    /// Create a new MetricsCollector with custom HTTP headers.
    pub fn with_headers(
        endpoint: String,
        resource: ResourceAttrs,
        headers: Vec<(String, String)>,
    ) -> Self {
        Self {
            endpoint,
            counters: Vec::new(),
            resource,
            headers,
        }
    }

    /// Record a counter increment with the given attributes.
    /// Cheap: just pushes to an in-memory Vec.
    pub fn inc(&mut self, name: &str, desc: &str, unit: &str, attrs: Vec<OtelAttribute>) {
        self.counters.push(CounterRecord {
            name: name.to_string(),
            description: desc.to_string(),
            unit: unit.to_string(),
            value: 1,
            attributes: attrs,
        });
    }

    /// Build the OTLP JSON payload from collected counters.
    pub fn build_payload(&self) -> serde_json::Value {
        let now = crate::now_nanos();
        let metrics: Vec<OtelMetric> = self
            .counters
            .iter()
            .map(|c| {
                let mut attrs = vec![
                    OtelAttribute::string("ant.username", &self.resource.username),
                    OtelAttribute::string("ant.agent.product", &self.resource.agent_product),
                    OtelAttribute::string("ant.agent.type", &self.resource.agent_type),
                    OtelAttribute::string("ant.agent.instance", &self.resource.agent_instance),
                    OtelAttribute::string("hook.version", &self.resource.hook_version),
                    OtelAttribute::string("ant.agent.scene", &self.resource.agent_scene),
                ];
                attrs.push(OtelAttribute::string(
                    "ant.agent.env",
                    &self.resource.agent_env,
                ));
                attrs.push(OtelAttribute::string(
                    "deployment.environment",
                    &self.resource.agent_env,
                ));
                attrs.extend(c.attributes.clone());
                OtelMetric::counter(
                    c.name.clone(),
                    c.description.clone(),
                    c.unit.clone(),
                    OtelSum {
                        data_points: vec![OtelNumberDataPoint {
                            attributes: attrs,
                            time_unix_nano: now.clone(),
                            start_time_unix_nano: now.clone(),
                            as_int: c.value,
                        }],
                        aggregation_temporality: 2, // CUMULATIVE
                        is_monotonic: true,
                    },
                )
            })
            .collect();

        let ctx = ExportContext {
            agent_product: &self.resource.agent_product,
            agent_type: &self.resource.agent_type,
            sdk_name: &self.resource.sdk_name,
            build_version: &self.resource.hook_version,
            agent_env: &self.resource.agent_env,
            agent_scene: &self.resource.agent_scene,
            collection_mode: &self.resource.collection_mode,
            emit_meta_trigger: self.resource.emit_meta_trigger,
        };
        wrap_metrics_export(metrics, &ctx)
    }

    /// Flush all collected metrics via HTTP POST to the configured endpoint.
    /// Uses the provided logger for diagnostics.
    #[cfg(feature = "metrics")]
    pub fn flush(&self, logger: &crate::logger::Logger) {
        if self.counters.is_empty() {
            return;
        }
        let payload = self.build_payload();
        let body = match serde_json::to_string(&payload) {
            Ok(b) => b,
            Err(e) => {
                logger.warn(&format!("metrics: failed to serialize: {}", e));
                return;
            }
        };
        logger.debug(&format!(
            "metrics: flushing {} counters to {}",
            self.counters.len(),
            self.endpoint
        ));
        let agent = build_metrics_agent();
        let mut request = agent
            .post(&self.endpoint)
            .header("Content-Type", "application/json");
        for (k, v) in &self.headers {
            request = request.header(k, v);
        }
        let result = request.send(body.as_bytes());
        match result {
            Ok(resp) => {
                logger.debug(&format!("metrics: sent ok, status={}", resp.status()));
            }
            Err(e) => {
                logger.warn(&format!("metrics: send failed: {}", e));
            }
        }
    }

    /// Flush with minimal stderr logging (for use when Logger may not be available,
    /// e.g. after a panic in main).
    #[cfg(feature = "metrics")]
    pub fn flush_silent(&self) {
        if self.counters.is_empty() {
            return;
        }
        let payload = self.build_payload();
        let body = match serde_json::to_string(&payload) {
            Ok(b) => b,
            Err(_) => return,
        };
        let agent = build_metrics_agent();
        let mut request = agent
            .post(&self.endpoint)
            .header("Content-Type", "application/json");
        for (k, v) in &self.headers {
            request = request.header(k, v);
        }
        let _ = request.send(body.as_bytes());
    }

    /// No-op flush when metrics feature is not enabled.
    #[cfg(not(feature = "metrics"))]
    pub fn flush(&self, _logger: &crate::logger::Logger) {}

    /// No-op flush_silent when metrics feature is not enabled.
    #[cfg(not(feature = "metrics"))]
    pub fn flush_silent(&self) {}

    /// Returns the number of collected counter records.
    pub fn len(&self) -> usize {
        self.counters.len()
    }

    /// Returns true if no counters have been collected.
    pub fn is_empty(&self) -> bool {
        self.counters.is_empty()
    }
}

/// Create a MetricsCollector if the given env var is set and non-empty.
/// Returns None if the env var is missing or empty (zero overhead).
pub fn create_metrics_collector(
    env_var: &str,
    agent_product: &str,
    agent_type: &str,
    username: &str,
    hook_version: &str,
) -> Option<MetricsCollector> {
    let endpoint = std::env::var(env_var).ok().filter(|v| !v.is_empty())?;
    let hostname = std::env::var("HOSTNAME")
        .or_else(|_| crate::hostname_from_system())
        .unwrap_or_else(|_| "unknown".to_string());
    Some(MetricsCollector::new(
        endpoint,
        ResourceAttrs {
            username: username.to_string(),
            agent_product: agent_product.to_string(),
            agent_type: agent_type.to_string(),
            sdk_name: "agentic-otel-common".to_string(),
            agent_instance: hostname,
            hook_version: hook_version.to_string(),
            agent_scene: "Default".to_string(),
            agent_env: String::new(),
            collection_mode: "full".to_string(),
            emit_meta_trigger: false,
        },
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn metrics_headers_runtime_overrides_builtin_case_insensitively() {
        assert_eq!(
            resolve_headers(
                r#"{"Authorization":"Bearer builtin","X-Tenant":"tenant"}"#,
                " authorization = Bearer renewed==,x-request-id=request,invalid,=ignored",
            ),
            vec![
                ("authorization".to_string(), "Bearer renewed==".to_string()),
                ("x-request-id".to_string(), "request".to_string()),
                ("x-tenant".to_string(), "tenant".to_string()),
            ]
        );
    }

    #[test]
    fn metrics_headers_preserve_builtin_without_runtime_config() {
        assert_eq!(
            resolve_headers(r#"{"Authorization":"Bearer builtin"}"#, ""),
            vec![("authorization".to_string(), "Bearer builtin".to_string())]
        );
        assert!(resolve_headers("", "").is_empty());
    }

    #[test]
    fn metrics_headers_runtime_works_without_valid_builtin_config() {
        for builtin in ["", "invalid json"] {
            assert_eq!(
                resolve_headers(builtin, "Authorization=Bearer runtime"),
                vec![("authorization".to_string(), "Bearer runtime".to_string())]
            );
        }
    }

    #[test]
    fn test_collector_inc_and_len() {
        let mut collector = MetricsCollector::new(
            "http://localhost:4318/v1/metrics".to_string(),
            ResourceAttrs {
                username: "test-user".to_string(),
                agent_product: "claudecode".to_string(),
                agent_type: "cchooks".to_string(),
                sdk_name: "agentic-otel-common".to_string(),
                agent_instance: "test-host".to_string(),
                hook_version: "0.1.0".to_string(),
                agent_scene: "Default".to_string(),
                agent_env: "Default".to_string(),
                collection_mode: "full".to_string(),
                emit_meta_trigger: false,
            },
        );
        assert!(collector.is_empty());

        collector.inc(
            "aivision.agentic.up",
            "Component invocation",
            "{count}",
            vec![],
        );
        assert_eq!(collector.len(), 1);

        collector.inc(
            "aivision.agentic.exec_error",
            "Execution error",
            "{count}",
            vec![
                OtelAttribute::string("handler.name", "LangfuseHandler"),
                OtelAttribute::string("error.type", "timeout"),
            ],
        );
        assert_eq!(collector.len(), 2);
    }

    #[test]
    fn test_build_payload_structure() {
        let mut collector = MetricsCollector::new(
            "http://localhost/metrics".to_string(),
            ResourceAttrs {
                username: "alice".to_string(),
                agent_product: "claudecode".to_string(),
                agent_type: "cchooks".to_string(),
                sdk_name: "agentic-otel-common".to_string(),
                agent_instance: "host1".to_string(),
                hook_version: "1.0.0".to_string(),
                agent_scene: "Default".to_string(),
                agent_env: "Default".to_string(),
                collection_mode: "full".to_string(),
                emit_meta_trigger: false,
            },
        );
        collector.inc(
            "aivision.agentic.up",
            "Component invocation",
            "{count}",
            vec![],
        );

        let payload = collector.build_payload();

        // Top-level structure
        assert!(payload["resourceMetrics"].is_array());
        let rm = &payload["resourceMetrics"][0];

        // Resource attributes present
        let res_attrs = &rm["resource"]["attributes"];
        assert!(res_attrs.is_array());

        // Scope metrics
        let sm = &rm["scopeMetrics"][0];
        assert_eq!(sm["scope"]["name"], "agentic-otel-common");

        // Metric data
        let metric = &sm["metrics"][0];
        assert_eq!(metric["name"], "aivision.agentic.up");
        assert_eq!(metric["unit"], "{count}");
        assert_eq!(metric["sum"]["isMonotonic"], true);
        assert_eq!(metric["sum"]["aggregationTemporality"], 2);

        // Data point attributes include resource attrs + custom attrs
        let dp = &metric["sum"]["dataPoints"][0];
        assert_eq!(dp["asInt"], 1);
        let dp_attrs = dp["attributes"].as_array().unwrap();
        // 8 resource attrs (including ant.agent.scene + ant.agent.env + deployment.environment) + 0 custom attrs = 8
        assert_eq!(dp_attrs.len(), 8);
        // First attr is ant.username
        assert_eq!(dp_attrs[0]["key"], "ant.username");
        assert_eq!(dp_attrs[0]["value"]["stringValue"], "alice");
    }

    #[test]
    fn test_cchooks_metrics_emit_meta_trigger_exactly_once() {
        let _guard = crate::otlp::META_TRIGGER_ENV_LOCK.lock().unwrap();
        let old_dedicated = std::env::var_os(crate::otlp::AIVISION_META_TRIGGER_ENV);
        let old_resource = std::env::var_os("CCHOOKS_OTEL_RESOURCE_ATTRIBUTES");
        std::env::set_var(crate::otlp::AIVISION_META_TRIGGER_ENV, "automation");
        std::env::set_var(
            "CCHOOKS_OTEL_RESOURCE_ATTRIBUTES",
            "ant.meta.trigger=interactive,ant.meta.trigger=unknown",
        );

        let collector = MetricsCollector::new(
            "http://localhost/metrics".to_string(),
            ResourceAttrs {
                username: "alice".to_string(),
                agent_product: "claudecode".to_string(),
                agent_type: "cchooks".to_string(),
                sdk_name: "cchooks".to_string(),
                agent_instance: "host1".to_string(),
                hook_version: "1.0.0".to_string(),
                agent_scene: "Default".to_string(),
                agent_env: "Default".to_string(),
                collection_mode: "full".to_string(),
                emit_meta_trigger: true,
            },
        );
        let payload = collector.build_payload();
        let trigger_values: Vec<_> = payload["resourceMetrics"][0]["resource"]["attributes"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|attr| attr["key"] == crate::otlp::ANT_META_TRIGGER_KEY)
            .filter_map(|attr| attr["value"]["stringValue"].as_str())
            .map(str::to_string)
            .collect();

        if let Some(value) = old_dedicated {
            std::env::set_var(crate::otlp::AIVISION_META_TRIGGER_ENV, value);
        } else {
            std::env::remove_var(crate::otlp::AIVISION_META_TRIGGER_ENV);
        }
        if let Some(value) = old_resource {
            std::env::set_var("CCHOOKS_OTEL_RESOURCE_ATTRIBUTES", value);
        } else {
            std::env::remove_var("CCHOOKS_OTEL_RESOURCE_ATTRIBUTES");
        }

        assert_eq!(trigger_values, vec!["automation"]);
    }

    #[test]
    fn test_build_payload_empty() {
        let collector = MetricsCollector::new(
            "http://localhost/metrics".to_string(),
            ResourceAttrs {
                username: "bob".to_string(),
                agent_product: "codex".to_string(),
                agent_type: "cdxhooks".to_string(),
                sdk_name: "agentic-otel-common".to_string(),
                agent_instance: "host2".to_string(),
                hook_version: "0.1.0".to_string(),
                agent_scene: "Default".to_string(),
                agent_env: "Default".to_string(),
                collection_mode: "full".to_string(),
                emit_meta_trigger: false,
            },
        );
        let payload = collector.build_payload();
        let metrics = payload["resourceMetrics"][0]["scopeMetrics"][0]["metrics"]
            .as_array()
            .unwrap();
        assert!(metrics.is_empty());
    }

    #[test]
    fn test_create_metrics_collector_missing_env() {
        // Ensure the env var is not set
        std::env::remove_var("TEST_METRICS_ENDPOINT_MISSING");
        let result = create_metrics_collector(
            "TEST_METRICS_ENDPOINT_MISSING",
            "test",
            "test",
            "user",
            "0.1.0",
        );
        assert!(result.is_none());
    }

    #[test]
    fn test_create_metrics_collector_empty_env() {
        std::env::set_var("TEST_METRICS_ENDPOINT_EMPTY", "");
        let result = create_metrics_collector(
            "TEST_METRICS_ENDPOINT_EMPTY",
            "test",
            "test",
            "user",
            "0.1.0",
        );
        assert!(result.is_none());
        std::env::remove_var("TEST_METRICS_ENDPOINT_EMPTY");
    }

    #[test]
    fn test_create_metrics_collector_with_endpoint() {
        std::env::set_var(
            "TEST_METRICS_ENDPOINT_OK",
            "http://localhost:4318/v1/metrics",
        );
        let result = create_metrics_collector(
            "TEST_METRICS_ENDPOINT_OK",
            "claudecode",
            "cchooks",
            "user",
            "1.0.0",
        );
        assert!(result.is_some());
        let c = result.unwrap();
        assert_eq!(c.endpoint, "http://localhost:4318/v1/metrics");
        assert!(c.is_empty());
        std::env::remove_var("TEST_METRICS_ENDPOINT_OK");
    }
}
