//! `report.config.toml` 解析 —— 与 `claudecode/cchooks/src/cli/config.rs` 完全对齐。
//!
//! TOML 结构：
//! ```toml
//! version = 1
//! default_profile = "test"
//!
//! [profiles.test]
//! host = "https://aivision.example.com"
//! public_key = "pk-lf-xxx"
//! secret_key = "sk-lf-xxx"
//! ```

use serde::Deserialize;
use std::collections::HashMap;
use std::path::Path;

/// Top-level report.config.toml structure.
#[derive(Debug, Deserialize)]
pub struct ReportConfig {
    #[allow(dead_code)]
    pub version: u32,
    pub default_profile: Option<String>,
    #[serde(default)]
    pub profiles: HashMap<String, ProfileEntry>,
}

/// A single profile entry.
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

impl ReportConfig {
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
            return std::env::var(env_var).map_err(|_| {
                format!("{}: env var '{}' not set or empty", field_desc, env_var)
            });
        }
    }
    Err(format!(
        "{}: neither direct value nor _env specified",
        field_desc
    ))
}
