use std::time::Instant;

use serde_json::{json, Map, Value};

use crate::common::logger::Logger;

pub struct PhaseRecorder {
    started: Instant,
    previous: Instant,
    component: &'static str,
    event: String,
    identity: String,
    phases: Map<String, Value>,
}

impl PhaseRecorder {
    pub fn new(component: &'static str, event: &str, identity: &str) -> Self {
        Self::with_started(component, event, identity, Instant::now())
    }

    pub fn with_started(
        component: &'static str,
        event: &str,
        identity: &str,
        started: Instant,
    ) -> Self {
        Self {
            started,
            previous: started,
            component,
            event: event.to_string(),
            identity: identity.to_string(),
            phases: Map::new(),
        }
    }

    pub fn set_ms(&mut self, phase: &'static str, elapsed_ms: u128) {
        self.phases.insert(phase.to_string(), json!(elapsed_ms));
    }

    pub fn mark(&mut self, phase: &'static str) {
        let now = Instant::now();
        self.phases.insert(
            phase.to_string(),
            json!(now.duration_since(self.previous).as_millis()),
        );
        self.previous = now;
    }

    pub fn record(mut self, logger: &Logger, outcome: &str) {
        self.phases.insert(
            "total".to_string(),
            json!(self.started.elapsed().as_millis()),
        );
        let execution = if self.component == "parent" {
            "sync"
        } else {
            "async"
        };
        let record = json!({
            "kind": "cdxhooks.phase_timing",
            "component": self.component,
            "execution": execution,
            "event": self.event,
            "identity": self.identity,
            "outcome": outcome,
            "elapsed_ms": self.phases,
        });
        logger.log_raw(&record.to_string());
    }
}
