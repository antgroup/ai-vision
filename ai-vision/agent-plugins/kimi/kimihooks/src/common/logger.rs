//! 极简文件 + stderr 双写日志。`debug=true` 时同时输出到 stderr，否则只写文件。
//!
//! 设计原则：
//! - 失败永不 panic（hook 进程必须 exit 0）；写文件失败时静默丢弃。
//! - 不依赖外部 logger crate，避免额外二进制体积。

use std::fs::OpenOptions;
use std::io::Write;
use std::path::{Path, PathBuf};

/// 日志级别——保持与 cchooks::common::logger 行为一致的最小子集。
#[derive(Debug, Clone, Copy)]
pub enum Level {
    Debug,
    Info,
    Warn,
    Error,
}

impl Level {
    fn label(self) -> &'static str {
        match self {
            Level::Debug => "DEBUG",
            Level::Info => "INFO",
            Level::Warn => "WARN",
            Level::Error => "ERROR",
        }
    }
}

#[derive(Clone)]
pub struct Logger {
    file: PathBuf,
    debug: bool,
}

impl Logger {
    pub fn new(file: PathBuf, debug: bool) -> Self {
        Self { file, debug }
    }

    pub fn debug(&self, msg: &str) {
        if self.debug {
            self.write(Level::Debug, msg);
        }
    }
    pub fn info(&self, msg: &str) {
        self.write(Level::Info, msg);
    }
    pub fn warn(&self, msg: &str) {
        self.write(Level::Warn, msg);
    }
    pub fn error(&self, msg: &str) {
        self.write(Level::Error, msg);
    }

    fn write(&self, level: Level, msg: &str) {
        if agentic_otel_common::privacy::private_process() { return; }
        let ts = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default();
        let secs = ts.as_secs();
        let millis = ts.subsec_millis();
        // 转本地时间（UTC+8）
        let total_secs = (secs + 8 * 3600) as i64;
        let days = total_secs / 86400;
        let h = ((total_secs % 86400) / 3600) as u64;
        let m = ((total_secs % 3600) / 60) as u64;
        let s = (total_secs % 60) as u64;
        // 从 Unix epoch (1970-01-01) 算年月日
        let (year, month, day) = epoch_days_to_ymd(days as i64);
        let line = format!(
            "[{} {}-{:02}-{:02} {:02}:{:02}:{:02}.{:03}] {}\n",
            level.label(), year, month, day, h, m, s, millis, msg
        );
        // 同步追加到文件；目录不存在时尝试创建。
        if let Some(parent) = self.file.parent() {
            let _ = super::dirs::ensure_dir(parent);
        }
        let _ = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.file)
            .and_then(|mut f| f.write_all(line.as_bytes()));
        if self.debug {
            // debug 模式下同时打到 stderr，方便本地排查
            let _ = writeln!(std::io::stderr(), "[kimihooks] {}", line.trim_end());
        }
    }

    pub fn file(&self) -> &Path {
        &self.file
    }
}

/// 将 Unix epoch 天数转换为 (year, month, day)。
/// 算法来自 Howard Hinnant's `civil_from_days`。
fn epoch_days_to_ymd(days: i64) -> (i64, u32, u32) {
    let z = days + 719468;
    let era = if z >= 0 { z } else { z - 146096 } / 146097;
    let doe = (z - era * 146097) as u32;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let y = yoe as i64 + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if m <= 2 { y + 1 } else { y };
    (y, m, d)
}
