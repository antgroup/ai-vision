//! Golden Replay Compare Module
//!
//! Compares actual OTLP output against golden replay files to detect
//! unintended attribute value changes.

pub mod config;
pub mod diff;
pub mod engine;
#[cfg(feature = "cli")]
pub mod tui;

pub use config::{CompareConfig, IgnoreConfig};
pub use diff::{DiffResult, DiffType};
pub use engine::CompareEngine;
