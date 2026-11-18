//! Standalone distribution support for the public `weaver-guard` binary.

use crate::error::WeaverGuardError;
use flate2::read::GzDecoder;
use serde::Deserialize;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::Once;

const RUNTIME_ASSET_ARCHIVE: &str = "runtime-assets.tar.gz";
const RUNTIME_MANIFEST: &str = "runtime-manifest.json";
const RUNTIME_ASSET_KIND: &str = "weaver-guard-runtime";
const LEGACY_RUNTIME_ASSET_KIND: &str = "aivision-guard-runtime";
const JAEGER_SCRIPT: &str = "jaeger-macos-arm64.sh";
const VICTORIA_SCRIPT: &str = "victoria-macos-arm64.sh";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ManagedPaths {
    pub home: PathBuf,
    pub guard_home: PathBuf,
    pub policies_dir: PathBuf,
    pub semconv_dir: PathBuf,
    pub scripts_dir: PathBuf,
    pub jaeger_script: PathBuf,
    pub victoria_script: PathBuf,
    pub jaeger_home: PathBuf,
    pub victoria_home: PathBuf,
    pub downloads_dir: PathBuf,
    pub jaeger_download_dir: PathBuf,
    pub victoria_download_dir: PathBuf,
    pub live_dir: PathBuf,
    pub diagnostic_log: PathBuf,
    pub replay_dir: PathBuf,
    pub manifest_path: PathBuf,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AssetUrls {
    pub latest: String,
    pub public_binary: String,
    pub runtime_assets: String,
}

#[derive(Debug, Deserialize)]
struct RuntimeManifest {
    version: String,
    asset_kind: String,
    required_directories: Vec<String>,
    required_files: Vec<String>,
    #[allow(dead_code)]
    sha256: Option<serde_json::Value>,
}

impl ManagedPaths {
    pub fn from_env() -> Result<Self, WeaverGuardError> {
        let home = match std::env::var_os("AIVISION_HOME") {
            Some(value) if !value.is_empty() => PathBuf::from(value),
            _ => {
                let home = std::env::var_os("HOME").ok_or_else(|| {
                    WeaverGuardError::Config(
                        "HOME is not set; set AIVISION_HOME for standalone guard data".to_string(),
                    )
                })?;
                PathBuf::from(home).join(".aivision")
            }
        };
        Ok(Self::from_home(home))
    }

    pub fn from_home(home: PathBuf) -> Self {
        let guard_home = home.join("guard");
        let scripts_dir = guard_home.join("scripts");
        let live_dir = guard_home.join("live");
        Self {
            home,
            guard_home: guard_home.clone(),
            policies_dir: guard_home.join("policies"),
            semconv_dir: guard_home.join("semconv"),
            scripts_dir: scripts_dir.clone(),
            jaeger_script: scripts_dir.join(JAEGER_SCRIPT),
            victoria_script: scripts_dir.join(VICTORIA_SCRIPT),
            jaeger_home: guard_home.join("backends").join("jaeger"),
            victoria_home: guard_home.join("backends").join("victoria"),
            downloads_dir: guard_home.join("downloads"),
            jaeger_download_dir: guard_home.join("downloads").join("jaeger"),
            victoria_download_dir: guard_home.join("downloads").join("victoria"),
            diagnostic_log: live_dir.join("diagnostics.jsonl"),
            replay_dir: live_dir.join("replays"),
            manifest_path: guard_home.join(RUNTIME_MANIFEST),
            live_dir,
        }
    }
}

pub fn asset_urls(prefix: &str, version: &str) -> AssetUrls {
    let prefix = normalize_prefix(prefix);
    AssetUrls {
        latest: format!("{prefix}latest.txt"),
        public_binary: format!("{prefix}{version}/weaver-guard"),
        runtime_assets: format!("{prefix}{version}/{RUNTIME_ASSET_ARCHIVE}"),
    }
}

pub async fn prepare_runtime_assets(
    paths: &ManagedPaths,
    version: &str,
) -> Result<(), WeaverGuardError> {
    remove_apple_metadata(paths)?;
    if runtime_ready_for_version(paths, version).is_ok() {
        return Ok(());
    }

    fs::create_dir_all(&paths.downloads_dir)?;
    fs::create_dir_all(&paths.jaeger_download_dir)?;
    fs::create_dir_all(&paths.victoria_download_dir)?;
    fs::create_dir_all(&paths.live_dir)?;
    fs::create_dir_all(&paths.replay_dir)?;
    fs::create_dir_all(&paths.jaeger_home)?;
    fs::create_dir_all(&paths.victoria_home)?;

    let archive = runtime_archive_path(paths, version);
    if let Some(local_archive) = std::env::var_os("WEAVER_GUARD_RUNTIME_ASSET_FILE")
        .or_else(|| std::env::var_os("AIVISION_GUARD_RUNTIME_ASSET_FILE"))
    {
        fs::copy(local_archive, &archive)?;
    } else {
        download_runtime_archive(&archive, version).await?;
    }

    install_runtime_archive(paths, &archive, version)?;
    runtime_ready_for_version(paths, version)
}

pub fn runtime_ready(paths: &ManagedPaths) -> Result<(), WeaverGuardError> {
    require_dir(&paths.policies_dir, "policies")?;
    require_dir(&paths.semconv_dir, "semconv")?;
    require_file(&paths.jaeger_script, "Jaeger script")?;
    require_file(&paths.victoria_script, "Victoria script")?;
    require_nonempty_extension(&paths.policies_dir, "rego", "policy")?;
    require_semconv_file(&paths.semconv_dir)?;
    Ok(())
}

fn runtime_ready_for_version(paths: &ManagedPaths, version: &str) -> Result<(), WeaverGuardError> {
    runtime_ready(paths)?;
    let manifest = read_runtime_manifest(&paths.manifest_path)?;
    validate_runtime_manifest(&manifest, version)
}

fn normalize_prefix(prefix: &str) -> String {
    let trimmed = prefix.trim();
    if trimmed.ends_with('/') {
        trimmed.to_string()
    } else {
        format!("{trimmed}/")
    }
}

fn runtime_archive_path(paths: &ManagedPaths, version: &str) -> PathBuf {
    paths
        .downloads_dir
        .join(format!("{version}-{RUNTIME_ASSET_ARCHIVE}"))
}

async fn download_runtime_archive(path: &Path, version: &str) -> Result<(), WeaverGuardError> {
    install_tls_provider();
    let prefix = std::env::var("WEAVER_GUARD_OSS_PREFIX")
        .or_else(|_| std::env::var("AIVISION_GUARD_OSS_PREFIX"))
        .ok()
        .filter(|value| !value.trim().is_empty())
        .ok_or_else(|| {
            WeaverGuardError::Config(
                "Set WEAVER_GUARD_OSS_PREFIX or WEAVER_GUARD_RUNTIME_ASSET_FILE".to_string(),
            )
        })?;
    let url = asset_urls(&prefix, version).runtime_assets;
    let tmp = path.with_extension("tmp");
    let response = reqwest::get(&url)
        .await
        .map_err(|e| WeaverGuardError::Config(format!("failed to download {url}: {e}")))?;
    if !response.status().is_success() {
        return Err(WeaverGuardError::Config(format!(
            "failed to download {url}: HTTP {}",
            response.status()
        )));
    }
    let bytes = response
        .bytes()
        .await
        .map_err(|e| WeaverGuardError::Config(format!("failed to read {url}: {e}")))?;
    fs::write(&tmp, &bytes)?;
    fs::rename(tmp, path)?;
    Ok(())
}

fn install_tls_provider() {
    static INSTALL: Once = Once::new();
    INSTALL.call_once(|| {
        let _ = rustls::crypto::ring::default_provider().install_default();
    });
}

fn install_runtime_archive(
    paths: &ManagedPaths,
    archive: &Path,
    version: &str,
) -> Result<(), WeaverGuardError> {
    let staging = paths.guard_home.join(format!(
        ".runtime-staging-{}-{}",
        std::process::id(),
        chrono::Utc::now().timestamp_millis()
    ));
    if staging.exists() {
        fs::remove_dir_all(&staging)?;
    }
    fs::create_dir_all(&staging)?;

    let result = extract_and_install(paths, archive, &staging, version);
    let cleanup = fs::remove_dir_all(&staging);
    match (result, cleanup) {
        (Err(err), _) => Err(err),
        (Ok(()), Err(err)) => Err(WeaverGuardError::Io(err)),
        (Ok(()), Ok(())) => Ok(()),
    }
}

fn extract_and_install(
    paths: &ManagedPaths,
    archive: &Path,
    staging: &Path,
    version: &str,
) -> Result<(), WeaverGuardError> {
    let file = fs::File::open(archive)?;
    let decoder = GzDecoder::new(file);
    let mut archive = tar::Archive::new(decoder);
    archive.unpack(staging)?;
    remove_apple_metadata_dir(staging)?;

    let manifest_path = staging.join(RUNTIME_MANIFEST);
    let manifest = read_runtime_manifest(&manifest_path)?;
    validate_runtime_manifest(&manifest, version)?;
    for dir in &manifest.required_directories {
        require_relative_path(dir)?;
        require_dir(&staging.join(dir), dir)?;
    }
    for file in &manifest.required_files {
        require_relative_path(file)?;
        require_file(&staging.join(file), file)?;
    }

    install_dir(&staging.join("policies"), &paths.policies_dir)?;
    install_dir(&staging.join("semconv"), &paths.semconv_dir)?;
    install_dir(&staging.join("scripts"), &paths.scripts_dir)?;
    fs::copy(&manifest_path, &paths.manifest_path)?;
    make_executable(&paths.jaeger_script)?;
    make_executable(&paths.victoria_script)?;
    Ok(())
}

fn read_runtime_manifest(path: &Path) -> Result<RuntimeManifest, WeaverGuardError> {
    let manifest_text = fs::read_to_string(path).map_err(|err| {
        WeaverGuardError::Config(format!(
            "runtime asset archive is missing {RUNTIME_MANIFEST}: {err}"
        ))
    })?;
    serde_json::from_str(&manifest_text)
        .map_err(|err| WeaverGuardError::Config(format!("invalid runtime asset manifest: {err}")))
}

fn remove_apple_metadata(paths: &ManagedPaths) -> Result<(), WeaverGuardError> {
    remove_apple_metadata_dir(&paths.policies_dir)?;
    remove_apple_metadata_dir(&paths.semconv_dir)?;
    remove_apple_metadata_dir(&paths.scripts_dir)?;
    Ok(())
}

fn remove_apple_metadata_dir(root: &Path) -> Result<(), WeaverGuardError> {
    if !root.exists() {
        return Ok(());
    }
    for entry in walkdir::WalkDir::new(root).contents_first(true) {
        let entry = entry.map_err(|e| WeaverGuardError::Config(e.to_string()))?;
        let name = entry.file_name().to_string_lossy();
        if name == ".DS_Store" || name.starts_with("._") {
            let path = entry.path();
            if entry.file_type().is_dir() {
                fs::remove_dir_all(path)?;
            } else {
                fs::remove_file(path)?;
            }
        }
    }
    Ok(())
}

fn validate_runtime_manifest(
    manifest: &RuntimeManifest,
    version: &str,
) -> Result<(), WeaverGuardError> {
    if manifest.asset_kind != RUNTIME_ASSET_KIND && manifest.asset_kind != LEGACY_RUNTIME_ASSET_KIND
    {
        return Err(WeaverGuardError::Config(format!(
            "invalid runtime asset kind: {}",
            manifest.asset_kind
        )));
    }
    if manifest.version != version {
        return Err(WeaverGuardError::Config(format!(
            "runtime asset version {} does not match binary version {version}",
            manifest.version
        )));
    }
    Ok(())
}

fn install_dir(source: &Path, dest: &Path) -> Result<(), WeaverGuardError> {
    require_dir(source, &source.display().to_string())?;
    if let Some(parent) = dest.parent() {
        fs::create_dir_all(parent)?;
    }
    if dest.exists() {
        fs::remove_dir_all(dest)?;
    }
    fs::rename(source, dest)?;
    Ok(())
}

fn require_relative_path(value: &str) -> Result<(), WeaverGuardError> {
    let path = Path::new(value);
    if path.is_absolute() || value.contains("..") {
        return Err(WeaverGuardError::Config(format!(
            "runtime manifest path must be relative and contained: {value}"
        )));
    }
    Ok(())
}

fn require_dir(path: &Path, label: &str) -> Result<(), WeaverGuardError> {
    if path.is_dir() {
        Ok(())
    } else {
        Err(WeaverGuardError::Config(format!(
            "missing required {label} directory: {}",
            path.display()
        )))
    }
}

fn require_file(path: &Path, label: &str) -> Result<(), WeaverGuardError> {
    if path.is_file() {
        Ok(())
    } else {
        Err(WeaverGuardError::Config(format!(
            "missing required {label}: {}",
            path.display()
        )))
    }
}

fn require_nonempty_extension(
    root: &Path,
    extension: &str,
    label: &str,
) -> Result<(), WeaverGuardError> {
    let mut found = false;
    for entry in walkdir::WalkDir::new(root) {
        let entry = entry.map_err(|e| WeaverGuardError::Config(e.to_string()))?;
        if entry.file_type().is_file()
            && entry.path().extension().is_some_and(|ext| ext == extension)
        {
            found = true;
            break;
        }
    }
    if found {
        Ok(())
    } else {
        Err(WeaverGuardError::Config(format!(
            "runtime {label} tree has no .{extension} files: {}",
            root.display()
        )))
    }
}

fn require_semconv_file(root: &Path) -> Result<(), WeaverGuardError> {
    for entry in walkdir::WalkDir::new(root) {
        let entry = entry.map_err(|e| WeaverGuardError::Config(e.to_string()))?;
        if !entry.file_type().is_file() {
            continue;
        }
        let Some(ext) = entry.path().extension() else {
            continue;
        };
        if ext == "yaml" || ext == "yml" {
            return Ok(());
        }
    }
    Err(WeaverGuardError::Config(format!(
        "runtime semconv tree has no YAML files: {}",
        root.display()
    )))
}

#[cfg(unix)]
fn make_executable(path: &Path) -> Result<(), WeaverGuardError> {
    use std::os::unix::fs::PermissionsExt;
    let mut perms = fs::metadata(path)?.permissions();
    perms.set_mode(perms.mode() | 0o755);
    fs::set_permissions(path, perms)?;
    Ok(())
}

#[cfg(not(unix))]
fn make_executable(_path: &Path) -> Result<(), WeaverGuardError> {
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use flate2::write::GzEncoder;
    use flate2::Compression;
    use std::io::Write;

    fn write_runtime_archive(path: &Path, source: &Path, version: &str) {
        fs::create_dir_all(source.join("policies")).unwrap();
        fs::create_dir_all(source.join("semconv")).unwrap();
        fs::create_dir_all(source.join("scripts")).unwrap();
        fs::write(
            source.join("policies").join("guard.rego"),
            "package guard\n",
        )
        .unwrap();
        fs::write(source.join("semconv").join("registry.yaml"), "groups: []\n").unwrap();
        fs::write(
            source.join("scripts").join("jaeger-macos-arm64.sh"),
            "#!/usr/bin/env bash\n",
        )
        .unwrap();
        fs::write(
            source.join("scripts").join("victoria-macos-arm64.sh"),
            "#!/usr/bin/env bash\n",
        )
        .unwrap();
        fs::write(
            source.join(RUNTIME_MANIFEST),
            format!(
                r#"{{
  "version": "{version}",
  "asset_kind": "weaver-guard-runtime",
  "required_directories": ["policies", "semconv", "scripts"],
  "required_files": ["scripts/victoria-macos-arm64.sh", "scripts/jaeger-macos-arm64.sh"]
}}"#
            ),
        )
        .unwrap();

        let file = fs::File::create(path).unwrap();
        let encoder = GzEncoder::new(file, Compression::default());
        let mut builder = tar::Builder::new(encoder);
        builder.append_dir_all(".", source).unwrap();
        builder.finish().unwrap();
        builder
            .into_inner()
            .unwrap()
            .finish()
            .unwrap()
            .flush()
            .unwrap();
    }

    #[test]
    fn managed_paths_use_guard_support_tree() {
        let paths = ManagedPaths::from_home(PathBuf::from("/tmp/aivision-test"));
        assert_eq!(paths.home, PathBuf::from("/tmp/aivision-test"));
        assert_eq!(paths.guard_home, PathBuf::from("/tmp/aivision-test/guard"));
        assert_eq!(
            paths.policies_dir,
            PathBuf::from("/tmp/aivision-test/guard/policies")
        );
        assert_eq!(
            paths.diagnostic_log,
            PathBuf::from("/tmp/aivision-test/guard/live/diagnostics.jsonl")
        );
        assert_eq!(
            paths.jaeger_home,
            PathBuf::from("/tmp/aivision-test/guard/backends/jaeger")
        );
        assert_eq!(
            paths.victoria_home,
            PathBuf::from("/tmp/aivision-test/guard/backends/victoria")
        );
        assert_eq!(
            paths.jaeger_download_dir,
            PathBuf::from("/tmp/aivision-test/guard/downloads/jaeger")
        );
        assert_eq!(
            paths.victoria_download_dir,
            PathBuf::from("/tmp/aivision-test/guard/downloads/victoria")
        );
        assert_eq!(
            paths.manifest_path,
            PathBuf::from("/tmp/aivision-test/guard/runtime-manifest.json")
        );
    }

    #[test]
    fn asset_urls_normalize_prefix() {
        let urls = asset_urls("https://example.test/base", "v1");
        assert_eq!(urls.latest, "https://example.test/base/latest.txt");
        assert_eq!(
            urls.public_binary,
            "https://example.test/base/v1/weaver-guard"
        );
        assert_eq!(
            urls.runtime_assets,
            "https://example.test/base/v1/runtime-assets.tar.gz"
        );
    }

    #[test]
    fn runtime_ready_rejects_empty_policy_tree() {
        let temp = tempfile::tempdir().unwrap();
        let paths = ManagedPaths::from_home(temp.path().join("home"));
        fs::create_dir_all(&paths.policies_dir).unwrap();
        fs::create_dir_all(&paths.semconv_dir).unwrap();
        fs::create_dir_all(&paths.scripts_dir).unwrap();
        fs::write(&paths.jaeger_script, "#!/usr/bin/env bash\n").unwrap();
        fs::write(&paths.victoria_script, "#!/usr/bin/env bash\n").unwrap();
        fs::write(paths.semconv_dir.join("registry.yaml"), "groups: []\n").unwrap();
        let err = runtime_ready(&paths).unwrap_err().to_string();
        assert!(err.contains("no .rego files"));
    }

    #[test]
    fn runtime_ready_for_version_rejects_stale_manifest() {
        let temp = tempfile::tempdir().unwrap();
        let paths = ManagedPaths::from_home(temp.path().join("home"));
        fs::create_dir_all(&paths.policies_dir).unwrap();
        fs::create_dir_all(&paths.semconv_dir).unwrap();
        fs::create_dir_all(&paths.scripts_dir).unwrap();
        fs::write(
            paths.policies_dir.join("guard.rego"),
            "package guard\nready := true\n",
        )
        .unwrap();
        fs::write(paths.semconv_dir.join("registry.yaml"), "groups: []\n").unwrap();
        fs::write(&paths.jaeger_script, "#!/usr/bin/env bash\n").unwrap();
        fs::write(&paths.victoria_script, "#!/usr/bin/env bash\n").unwrap();
        fs::write(
            &paths.manifest_path,
            r#"{
  "version": "0.0.1",
  "asset_kind": "weaver-guard-runtime",
  "required_directories": ["policies", "semconv", "scripts"],
  "required_files": ["scripts/victoria-macos-arm64.sh", "scripts/jaeger-macos-arm64.sh"]
}"#,
        )
        .unwrap();

        let err = runtime_ready_for_version(&paths, "0.0.2")
            .unwrap_err()
            .to_string();
        assert!(err.contains("does not match binary version 0.0.2"));
    }

    #[test]
    fn remove_apple_metadata_cleans_managed_runtime_trees() {
        let temp = tempfile::tempdir().unwrap();
        let paths = ManagedPaths::from_home(temp.path().join("home"));
        fs::create_dir_all(&paths.policies_dir).unwrap();
        fs::create_dir_all(&paths.semconv_dir).unwrap();
        fs::create_dir_all(&paths.scripts_dir).unwrap();
        let policy_metadata = paths.policies_dir.join("._rules.rego");
        let semconv_metadata = paths.semconv_dir.join("._spans.yaml");
        let ds_store = paths.scripts_dir.join(".DS_Store");
        fs::write(&policy_metadata, b"\0binary").unwrap();
        fs::write(&semconv_metadata, b"\0binary").unwrap();
        fs::write(&ds_store, b"\0binary").unwrap();

        remove_apple_metadata(&paths).unwrap();

        assert!(!policy_metadata.exists());
        assert!(!semconv_metadata.exists());
        assert!(!ds_store.exists());
    }

    #[test]
    fn install_runtime_archive_honors_manifest_contract() {
        let temp = tempfile::tempdir().unwrap();
        let paths = ManagedPaths::from_home(temp.path().join("home"));
        let archive = temp.path().join("runtime-assets.tar.gz");
        let version = env!("CARGO_PKG_VERSION");
        write_runtime_archive(&archive, &temp.path().join("source"), version);

        install_runtime_archive(&paths, &archive, version).unwrap();

        runtime_ready(&paths).unwrap();
        assert!(paths.policies_dir.join("guard.rego").is_file());
        assert!(paths.semconv_dir.join("registry.yaml").is_file());
        assert!(paths.jaeger_script.is_file());
        assert!(paths.victoria_script.is_file());
        assert!(paths.manifest_path.is_file());
    }
}
