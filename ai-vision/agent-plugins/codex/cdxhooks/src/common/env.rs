use std::cell::RefCell;
use std::collections::BTreeMap;

thread_local! {
    static WORKER_ENV: RefCell<Option<BTreeMap<String, String>>> = const { RefCell::new(None) };
}

struct WorkerEnvGuard(Option<BTreeMap<String, String>>);

impl Drop for WorkerEnvGuard {
    fn drop(&mut self) {
        let previous = self.0.take();
        WORKER_ENV.with(|current| *current.borrow_mut() = previous);
    }
}

/// Run one queued item with its captured environment as a thread-local,
/// read-only overlay. Never mutates the process environment.
pub fn with_worker_env<R>(env: &BTreeMap<String, String>, run: impl FnOnce() -> R) -> R {
    let previous = WORKER_ENV.with(|current| current.replace(Some(env.clone())));
    let _guard = WorkerEnvGuard(previous);
    let mode = env.get("AIVISION_COLLECTION_MODE")
        .or_else(|| env.get("CDXHOOKS_COLLECTION_MODE"))
        .map(String::as_str).unwrap_or("full");
    agentic_otel_common::privacy::with_worker_mode(mode, run)
}

pub fn var(key: &str) -> Result<String, std::env::VarError> {
    if matches!(
        key,
        "CDXHOOKS_LANGFUSE_HEADERS" | "CDXHOOKS_OTEL_EXPORTER_OTLP_HEADERS"
    ) && raw_var("AIVISION_MANAGED_INGEST").ok().as_deref() == Some("1")
    {
        let header = raw_var("AIVISION_INGEST_CONFIG").ok().and_then(|path| {
            agentic_otel_common::ingest_auth::current_header(std::path::Path::new(&path))
        });
        return Ok(header.unwrap_or_default());
    }
    raw_var(key)
}

fn raw_var(key: &str) -> Result<String, std::env::VarError> {
    let worker_value =
        WORKER_ENV.with(|current| current.borrow().as_ref().map(|env| env.get(key).cloned()));
    match worker_value {
        Some(Some(value)) => Ok(value),
        Some(None) if should_capture_worker_env(key) => Err(std::env::VarError::NotPresent),
        _ => std::env::var(key),
    }
}

pub fn vars() -> std::vec::IntoIter<(String, String)> {
    let overlay = WORKER_ENV.with(|current| current.borrow().clone());
    let Some(overlay) = overlay else {
        return std::env::vars().collect::<Vec<_>>().into_iter();
    };
    let mut merged = std::env::vars()
        .filter(|(key, _)| !should_capture_worker_env(key))
        .collect::<BTreeMap<_, _>>();
    merged.extend(overlay);
    merged.into_iter().collect::<Vec<_>>().into_iter()
}

pub fn capture_worker_env() -> BTreeMap<String, String> {
    vars()
        .filter(|(key, _)| should_capture_worker_env(key))
        .collect()
}

pub fn should_capture_worker_env(key: &str) -> bool {
    if matches!(
        key,
        "CDXHOOKS_INTERNAL_WORKER"
            | "CDXHOOKS_WORKER_SESSION_ID"
            | "CDXHOOKS_WORKER_START_TOKEN"
            | "CDXHOOKS_WORKER_OWNER_FD"
    ) || is_sensitive_env_key(key)
    {
        return false;
    }
    key == "HOME"
        || key == "USERPROFILE"
        || key == "USER"
        || key == "USERNAME"
        || key == "HOSTNAME"
        || key == "TRACEPARENT"
        || key == "SOFA_TRACEID"
        || key == "SOFA_RPCID"
        || key.starts_with("CDXHOOKS_")
        || key.starts_with("AIVISION_")
        || key.starts_with("LANGFUSE_")
        || key.starts_with("OTEL_")
        || key == "ANT_GRAY"
}

fn is_sensitive_env_key(key: &str) -> bool {
    let upper = key.to_ascii_uppercase();
    [
        "SECRET",
        "TOKEN",
        "PASSWORD",
        "PASSWD",
        "_KEY",
        "API_KEY",
        "PRIVATE_KEY",
        "CREDENTIAL",
        "AUTH",
        "COOKIE",
        "HEADER",
    ]
    .iter()
    .any(|marker| upper.contains(marker))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn worker_env_overlay_does_not_mutate_process_environment() {
        let key = "CDXHOOKS_WORKER_ENV_OVERLAY_TEST_UNSET_7F83D";
        assert!(std::env::var_os(key).is_none());
        let env = BTreeMap::from([(key.to_string(), "queued-value".to_string())]);

        with_worker_env(&env, || {
            assert_eq!(var(key).unwrap(), "queued-value");
            assert!(std::env::var_os(key).is_none());
        });

        assert!(var(key).is_err());
        assert!(std::env::var_os(key).is_none());
    }

    #[test]
    fn capture_worker_env_reads_effective_overlay() {
        let key = "CDXHOOKS_WORKER_ENV_CAPTURE_TEST_UNSET_9B02";
        assert!(std::env::var_os(key).is_none());
        let env = BTreeMap::from([(key.to_string(), "queued-value".to_string())]);

        with_worker_env(&env, || {
            let captured = capture_worker_env();
            assert_eq!(captured.get(key).map(String::as_str), Some("queued-value"));
            assert!(std::env::var_os(key).is_none());
        });

        assert!(!capture_worker_env().contains_key(key));
    }

    #[test]
    fn worker_env_overlay_restores_after_panic() {
        let key = "CDXHOOKS_WORKER_ENV_OVERLAY_PANIC_TEST_UNSET_5A91";
        let env = BTreeMap::from([(key.to_string(), "queued-value".to_string())]);
        let _ = std::panic::catch_unwind(|| {
            with_worker_env(&env, || {
                assert_eq!(var(key).unwrap(), "queued-value");
                panic!("exercise guard cleanup");
            });
        });
        assert!(var(key).is_err());
        assert!(std::env::var_os(key).is_none());
    }

    #[test]
    fn sensitive_keys_excluded_from_capture() {
        assert!(!should_capture_worker_env("LANGFUSE_SECRET_KEY"));
        assert!(!should_capture_worker_env("AIVISION_API_TOKEN"));
        assert!(!should_capture_worker_env("OTEL_EXPORTER_OTLP_HEADERS"));
        assert!(!should_capture_worker_env("CDXHOOKS_WORKER_START_TOKEN"));
        assert!(!should_capture_worker_env("CDXHOOKS_WORKER_SESSION_ID"));
        assert!(!should_capture_worker_env("CDXHOOKS_INTERNAL_WORKER"));
    }

    #[test]
    fn observable_keys_included_in_capture() {
        assert!(should_capture_worker_env("TRACEPARENT"));
        assert!(should_capture_worker_env("SOFA_TRACEID"));
        assert!(should_capture_worker_env("CDXHOOKS_DEBUG"));
        assert!(should_capture_worker_env("AIVISION_HOME"));
        assert!(should_capture_worker_env("OTEL_EXPORTER_OTLP_ENDPOINT"));
        assert!(should_capture_worker_env("LANGFUSE_HOST"));
    }
}
