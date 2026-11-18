use crate::common::dirs;

const HOOK_VERSION: &str = env!("HOOK_VERSION");
const HOOK_COMMIT: &str = env!("HOOK_COMMIT");
const HOOK_BUILD_TIME: &str = env!("HOOK_BUILD_TIME");

pub fn run() {
    let info = serde_json::json!({
        "version": HOOK_VERSION,
        "commit": HOOK_COMMIT,
        "build_time": HOOK_BUILD_TIME,
        "edition": dirs::edition_name(),
        "root_dir": dirs::root_dir().to_string_lossy(),
        "log_dir": dirs::log_dir().to_string_lossy(),
        "state_dir": dirs::state_dir().to_string_lossy(),
        "sessions_dir": dirs::sessions_dir().to_string_lossy(),
        "platform": std::env::consts::OS,
        "arch": std::env::consts::ARCH,
    });
    println!("{}", serde_json::to_string_pretty(&info).unwrap());
}
