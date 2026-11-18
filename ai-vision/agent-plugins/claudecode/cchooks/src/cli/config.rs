use serde::Deserialize;
use std::collections::HashMap;
use std::path::Path;

use crate::hooks::langfuse::LangfuseConfig;

/// Top-level config.toml structure.
#[derive(Debug, Deserialize)]
pub struct Config {
    #[allow(dead_code)]
    pub version: u32,
    pub default_profile: Option<String>,
    #[serde(default)]
    pub profiles: HashMap<String, ProfileEntry>,
}

/// A single profile entry in config.toml.
#[derive(Debug, Deserialize)]
pub struct ProfileEntry {
    pub host: String,
    pub public_key: Option<String>,
    pub public_key_env: Option<String>,
    pub secret_key: Option<String>,
    pub secret_key_env: Option<String>,
    #[allow(dead_code)]
    pub project_name: Option<String>,
}

/// Resolved profile with actual key values ready for use.
#[derive(Debug)]
pub struct ResolvedProfile {
    pub host: String,
    pub public_key: String,
    pub secret_key: String,
}

impl Config {
    pub fn load(path: &Path) -> Result<Self, String> {
        let content = std::fs::read_to_string(path)
            .map_err(|e| format!("Failed to read config '{}': {}", path.display(), e))?;
        basic_toml::from_str(&content)
            .map_err(|e| format!("Failed to parse config '{}': {}", path.display(), e))
    }

    pub fn resolve_profile(&self, name: Option<&str>) -> Result<ResolvedProfile, String> {
        let profile_name = match name {
            Some(n) => n,
            None => self
                .default_profile
                .as_deref()
                .ok_or("No --profile specified and no default_profile in config")?,
        };
        let entry = self
            .profiles
            .get(profile_name)
            .ok_or_else(|| format!("Profile '{}' not found in config", profile_name))?;
        entry.resolve(profile_name)
    }
}

impl ProfileEntry {
    fn resolve(&self, name: &str) -> Result<ResolvedProfile, String> {
        let public_key = resolve_key_field(
            &self.public_key,
            &self.public_key_env,
            &format!("profiles.{}.public_key", name),
        )?;
        let secret_key = resolve_key_field(
            &self.secret_key,
            &self.secret_key_env,
            &format!("profiles.{}.secret_key", name),
        )?;
        Ok(ResolvedProfile {
            host: self.host.clone(),
            public_key,
            secret_key,
        })
    }
}

impl From<ResolvedProfile> for LangfuseConfig {
    fn from(p: ResolvedProfile) -> Self {
        LangfuseConfig {
            public_key: Some(p.public_key),
            secret_key: Some(p.secret_key),
            host: p.host,
            headers: None,
        }
    }
}

/// Resolve a key: direct value takes precedence, then env var lookup.
fn resolve_key_field(
    direct: &Option<String>,
    env_name: &Option<String>,
    field_desc: &str,
) -> Result<String, String> {
    if let Some(val) = direct {
        if !val.is_empty() {
            return Ok(val.clone());
        }
    }
    if let Some(env_var) = env_name {
        if !env_var.is_empty() {
            return crate::common::env::var(env_var)
                .map_err(|_| format!("{}: env var '{}' not set or empty", field_desc, env_var));
        }
    }
    Err(format!(
        "{}: neither direct value nor _env specified",
        field_desc
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_config_toml() {
        let toml_str = r#"
version = 1
default_profile = "dev"

[profiles.dev]
host = "https://example.com"
public_key = "pk-test"
secret_key = "sk-test"
project_name = "my-project"

[profiles.env-based]
host = "https://example.com"
public_key_env = "TEST_PK_ENV"
secret_key_env = "TEST_SK_ENV"
"#;
        let config: Config = basic_toml::from_str(toml_str).unwrap();
        assert_eq!(config.version, 1);
        assert_eq!(config.default_profile.as_deref(), Some("dev"));
        assert_eq!(config.profiles.len(), 2);

        let dev = config.profiles.get("dev").unwrap();
        assert_eq!(dev.host, "https://example.com");
        assert_eq!(dev.public_key.as_deref(), Some("pk-test"));
    }

    #[test]
    fn test_resolve_profile_direct_keys() {
        let toml_str = r#"
version = 1
default_profile = "test"

[profiles.test]
host = "https://example.com"
public_key = "pk-direct"
secret_key = "sk-direct"
"#;
        let config: Config = basic_toml::from_str(toml_str).unwrap();
        let resolved = config.resolve_profile(None).unwrap();
        assert_eq!(resolved.host, "https://example.com");
        assert_eq!(resolved.public_key, "pk-direct");
        assert_eq!(resolved.secret_key, "sk-direct");
    }

    #[test]
    fn test_resolve_profile_env_keys() {
        std::env::set_var("_TEST_CCHOOKS_PK", "pk-from-env");
        std::env::set_var("_TEST_CCHOOKS_SK", "sk-from-env");

        let toml_str = r#"
version = 1

[profiles.envtest]
host = "https://example.com"
public_key_env = "_TEST_CCHOOKS_PK"
secret_key_env = "_TEST_CCHOOKS_SK"
"#;
        let config: Config = basic_toml::from_str(toml_str).unwrap();
        let resolved = config.resolve_profile(Some("envtest")).unwrap();
        assert_eq!(resolved.public_key, "pk-from-env");
        assert_eq!(resolved.secret_key, "sk-from-env");

        std::env::remove_var("_TEST_CCHOOKS_PK");
        std::env::remove_var("_TEST_CCHOOKS_SK");
    }

    #[test]
    fn test_resolve_profile_not_found() {
        let toml_str = r#"
version = 1

[profiles.existing]
host = "https://example.com"
public_key = "pk"
secret_key = "sk"
"#;
        let config: Config = basic_toml::from_str(toml_str).unwrap();
        let err = config.resolve_profile(Some("nonexistent")).unwrap_err();
        assert!(err.contains("not found"));
    }

    #[test]
    fn test_resolve_profile_missing_key() {
        let toml_str = r#"
version = 1

[profiles.broken]
host = "https://example.com"
"#;
        let config: Config = basic_toml::from_str(toml_str).unwrap();
        let err = config.resolve_profile(Some("broken")).unwrap_err();
        assert!(err.contains("neither direct value nor _env"));
    }

    #[test]
    fn test_into_langfuse_config() {
        let resolved = ResolvedProfile {
            host: "https://example.com".to_string(),
            public_key: "pk".to_string(),
            secret_key: "sk".to_string(),
        };
        let config: LangfuseConfig = resolved.into();
        assert_eq!(config.host, "https://example.com");
        assert_eq!(config.public_key, Some("pk".to_string()));
        assert_eq!(config.secret_key, Some("sk".to_string()));
        assert!(config.headers.is_none());
    }
}
