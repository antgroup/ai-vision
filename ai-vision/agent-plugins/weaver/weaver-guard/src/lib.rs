//! Weaver Guard - CLI validator for AI Agent OTLP telemetry
//!
//! Validates JSONL telemetry files against Rego policies and
//! semantic convention YAML registries.

pub mod compare;
pub mod config;
pub mod distribution;
pub mod error;
pub mod jsonl;
pub mod report;
pub mod semconv;
pub mod server;
pub mod validation;

pub use config::{Config, FailOn, OutputFormat};
pub use error::WeaverGuardError;
pub use validation::{ValidationEngine, ValidationFinding};
