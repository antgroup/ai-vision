pub mod cli;
pub mod common;
pub mod hooks;
pub mod worker;

/// Test-only support for serializing tests that mutate process-global env
/// vars. Cargo runs unit tests in parallel by default, and HOME-mutating
/// tests in this crate (`hook::test_expand_home_path` /
/// `dirs::test_home_dir_fallback`) would otherwise observe each other's
/// changes and flake. Call `env_lock()` at the top of any test that
/// reads or writes a shared env var.
#[cfg(test)]
pub(crate) mod test_sync {
    use std::sync::{Mutex, MutexGuard, OnceLock};

    static ENV_LOCK: OnceLock<Mutex<()>> = OnceLock::new();

    pub fn env_lock() -> MutexGuard<'static, ()> {
        ENV_LOCK
            .get_or_init(|| Mutex::new(()))
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
    }
}
