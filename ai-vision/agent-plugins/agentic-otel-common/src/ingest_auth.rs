//! Read credentials refreshed by the native hook launcher, including in workers.
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

/// No network calls or SpaceKey persistence in workers. The launcher refreshes
/// this private cache before enqueueing each hook; exports read its current token.
pub fn current_header(config_path: &Path) -> Option<String> {
    let config: serde_json::Value =
        serde_json::from_slice(&std::fs::read(config_path).ok()?).ok()?;
    let token: serde_json::Value = serde_json::from_slice(
        &std::fs::read(config_path.with_file_name("ingest-token.json")).ok()?,
    )
    .ok()?;
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .ok()?
        .as_secs_f64();
    if token.get("space_id")?.as_str()? != config.get("space_id")?.as_str()?
        || token.get("endpoint")?.as_str()?.trim_end_matches('/')
            != config.get("endpoint")?.as_str()?.trim_end_matches('/')
        || token.get("valid_until")?.as_f64()? <= now
    {
        return None;
    }
    let value = token.get("token")?.as_str()?;
    if value.is_empty() || value.contains(['\r', '\n']) {
        return None;
    }
    Some(format!("Authorization=Bearer {value}"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn export_reads_renewed_token_and_rejects_stale_or_mismatched_cache() {
        let directory = tempfile::tempdir().unwrap();
        let config = directory.path().join("ingest.json");
        let cache = directory.path().join("ingest-token.json");
        std::fs::write(
            &config,
            json!({"space_id":"space", "endpoint":"http://collector/v1/traces"}).to_string(),
        )
        .unwrap();
        let mut token = json!({"space_id":"space", "endpoint":"http://collector/v1/traces", "valid_until":4102444800_u64, "token":"first"});
        std::fs::write(&cache, token.to_string()).unwrap();
        assert_eq!(
            current_header(&config).as_deref(),
            Some("Authorization=Bearer first")
        );
        token["token"] = json!("renewed");
        std::fs::write(&cache, token.to_string()).unwrap();
        assert_eq!(
            current_header(&config).as_deref(),
            Some("Authorization=Bearer renewed")
        );
        for (key, value) in [
            ("space_id", json!("different")),
            ("endpoint", json!("http://other/v1/traces")),
            ("valid_until", json!(0)),
        ] {
            let mut invalid = token.clone();
            invalid[key] = value;
            std::fs::write(&cache, invalid.to_string()).unwrap();
            assert_eq!(current_header(&config), None);
        }
    }
}
