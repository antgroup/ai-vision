use std::collections::HashMap;
use std::process::Command;

fn main() {
    // Inject version info at compile time via environment variables,
    // equivalent to Go's -ldflags -X main.buildVersion
    let version = std::env::var("HOOK_VERSION").unwrap_or_else(|_| "dev".to_string());
    let commit = git_commit().unwrap_or_else(|| "unknown".to_string());
    let build_time = build_time();

    println!("cargo:rustc-env=HOOK_VERSION={}", version);
    println!("cargo:rustc-env=HOOK_COMMIT={}", commit);
    println!("cargo:rustc-env=HOOK_BUILD_TIME={}", build_time);

    if std::env::var_os("CARGO_FEATURE_AIVISION").is_some() {
        let keys_file = "aivision.keys.json";
        println!("cargo:rerun-if-changed={}", keys_file);

        let keys_content = std::fs::read_to_string(keys_file).unwrap_or_else(|_| {
            panic!(
                "{} is required for the enabled feature. See README.",
                keys_file
            )
        });

        // Parse JSON using serde_json for reliability
        let keys_json: serde_json::Value = serde_json::from_str(&keys_content)
            .unwrap_or_else(|e| panic!("Failed to parse {}: {}", keys_file, e));

        let extract = |key: &str| -> String {
            keys_json[key]
                .as_str()
                .unwrap_or_else(|| panic!("{} missing key: {}", keys_file, key))
                .to_string()
        };

        println!(
            "cargo:rustc-env=LANGFUSE_BUILTIN_AGENT_PRODUCT={}",
            extract("agent_product")
        );
        println!(
            "cargo:rustc-env=LANGFUSE_BUILTIN_PROD_PUBLIC_KEY={}",
            extract("prod_public_key")
        );
        println!(
            "cargo:rustc-env=LANGFUSE_BUILTIN_PROD_SECRET_KEY={}",
            extract("prod_secret_key")
        );
        println!(
            "cargo:rustc-env=LANGFUSE_BUILTIN_PROD_HOST={}",
            extract("prod_host")
        );
        println!(
            "cargo:rustc-env=LANGFUSE_BUILTIN_GRAY_PUBLIC_KEY={}",
            extract("gray_public_key")
        );
        println!(
            "cargo:rustc-env=LANGFUSE_BUILTIN_GRAY_SECRET_KEY={}",
            extract("gray_secret_key")
        );
        println!(
            "cargo:rustc-env=LANGFUSE_BUILTIN_GRAY_HOST={}",
            extract("gray_host")
        );

        // Extract optional headers as JSON string
        let prod_headers = keys_json["prod_headers"]
            .as_object()
            .map(|obj| {
                let map: HashMap<String, String> = obj
                    .iter()
                    .filter_map(|(k, v)| v.as_str().map(|s| (k.clone(), s.to_string())))
                    .collect();
                serde_json::to_string(&map).unwrap_or_default()
            })
            .unwrap_or_default();
        println!(
            "cargo:rustc-env=LANGFUSE_BUILTIN_PROD_HEADERS={}",
            prod_headers
        );

        // Metrics endpoint (optional)
        let metrics_endpoint = keys_json["prod_status_metrics_endpoint"]
            .as_str()
            .unwrap_or("")
            .to_string();
        println!(
            "cargo:rustc-env=METRICS_BUILTIN_ENDPOINT={}",
            metrics_endpoint
        );
    }

    // Re-run if these change
    println!("cargo:rerun-if-env-changed=HOOK_VERSION");
    println!("cargo:rerun-if-changed=.git/HEAD");
    println!("cargo:rerun-if-changed=.git/refs/heads");
}

fn git_commit() -> Option<String> {
    let output = Command::new("git")
        .args(["rev-parse", "--short", "HEAD"])
        .output()
        .ok()?;
    if output.status.success() {
        let commit = String::from_utf8(output.stdout).ok()?;
        Some(commit.trim().to_string())
    } else {
        None
    }
}

fn build_time() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    // Simple UTC timestamp
    let (year, month, day, hour, minute, second) = unix_secs_to_datetime(secs);
    format!(
        "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}Z",
        year, month, day, hour, minute, second
    )
}

fn unix_secs_to_datetime(secs: u64) -> (u64, u64, u64, u64, u64, u64) {
    let second = secs % 60;
    let minutes = secs / 60;
    let minute = minutes % 60;
    let hours = minutes / 60;
    let hour = hours % 24;
    let days = hours / 24;

    let mut year = 1970u64;
    let mut remaining_days = days;

    loop {
        let days_in_year = if is_leap_year(year) { 366 } else { 365 };
        if remaining_days < days_in_year {
            break;
        }
        remaining_days -= days_in_year;
        year += 1;
    }

    let month_days: [u64; 12] = if is_leap_year(year) {
        [31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    } else {
        [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    };

    let mut month = 1u64;
    for &days_in_month in &month_days {
        if remaining_days < days_in_month {
            break;
        }
        remaining_days -= days_in_month;
        month += 1;
    }

    let day = remaining_days + 1;
    (year, month, day, hour, minute, second)
}

fn is_leap_year(year: u64) -> bool {
    (year.is_multiple_of(4) && !year.is_multiple_of(100)) || year.is_multiple_of(400)
}
