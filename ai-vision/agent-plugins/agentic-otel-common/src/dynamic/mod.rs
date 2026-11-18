//! Dynamic data collection utilities.
//!
//! Agent-agnostic modules for session/turn lifecycle, git state capture,
//! diff computation, transcript parsing, and OTel LogRecord building.

pub mod diff;
pub mod git;
pub mod log_record;
pub mod session;
pub mod transcript_parser;
