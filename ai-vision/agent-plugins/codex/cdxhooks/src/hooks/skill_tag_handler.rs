//! Skill tag handler: scans skill directories at SessionStart and persists the tag registry.

use crate::common::hook::HookEvent;
use crate::common::skill_tags;
use crate::hooks::{CommonContext, HandlerError, HookHandler};

/// Handler that scans skill files at session start and writes the tag registry to state.
pub struct SkillTagHandler;

impl SkillTagHandler {
    pub fn new() -> Self {
        Self
    }
}

impl HookHandler for SkillTagHandler {
    fn name(&self) -> &str {
        "skill-tag"
    }

    fn supported_events(&self) -> &[HookEvent] {
        &[HookEvent::SessionStart]
    }

    fn handle(
        &self,
        _event: &HookEvent,
        payload: &serde_json::Value,
        context: &CommonContext,
    ) -> Result<(), HandlerError> {
        let cwd = payload.get("cwd").and_then(|v| v.as_str()).unwrap_or(".");

        context.logger.info(&format!(
            "[skill-tag] scanning skill directories (cwd={})",
            cwd
        ));

        let registry = skill_tags::scan_skill_tags(cwd);

        if registry.is_empty() {
            context.logger.debug("[skill-tag] no aivision tags found");
            return Ok(());
        }

        context.logger.info(&format!(
            "[skill-tag] found {} skills with tags: {:?}",
            registry.len(),
            registry.keys().collect::<Vec<_>>()
        ));

        let Some(state_path) = skill_tags::state_file_path_from_session_state(&context.state_file)
        else {
            context
                .logger
                .warn("[skill-tag] no session state parent, skipping registry persistence");
            return Ok(());
        };

        skill_tags::save_registry(&registry, &state_path).map_err(|e| {
            HandlerError::from(format!("[skill-tag] failed to save registry: {}", e))
        })?;

        context.logger.info(&format!(
            "[skill-tag] registry saved to {}",
            state_path.display()
        ));

        Ok(())
    }
}
