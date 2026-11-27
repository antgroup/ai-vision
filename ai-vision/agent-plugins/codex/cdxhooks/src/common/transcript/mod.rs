//! Stable transcript facade; implementations are split by semantic ownership.

mod boundary;
mod collab;
mod correlation;
mod correlation_discovery;
mod io;
mod parent_sync;
mod semantics;
mod session_meta;
mod skill;
mod tool_normalization;
mod turn_builder;
mod types;

use agentic_otel_common::iso_to_nanos;
use serde_json::{json, Value};
use std::collections::{HashMap, HashSet};
use std::fs::File;
use std::io::{BufRead, BufReader, Read, Seek, SeekFrom};
use std::path::Path;

use crate::common::logger::looks_like_iso_timestamp;

// Keep the pre-split `common::transcript::*` facade stable.
pub use boundary::{
    mark_turn_heuristic_complete, split_completed_messages, turn_looks_complete, TurnTerminalState,
};
pub(crate) use collab::is_internal_role_user_envelope;
use collab::{
    extract_response_message_text, non_empty_json_string, parse_collab_agent_tool_call,
    parse_subagent_activity, parse_subagent_source,
};
pub use collab::{
    CollabAgentEvent, CollabAgentInvocationEvidence, CollabInvocationSource, CollabInvocationState,
    SubagentActivationEvidence, SubagentDiscoveryDecision, SubagentDiscoveryDisposition,
    SubagentDiscoveryPlan, SubagentObservation,
};
pub(crate) use correlation::subagent_invocation_key;
pub use correlation::{
    collect_subagent_identities, collect_subagent_invocation_evidence,
    completed_subagent_identities, correlate_subagent, correlate_subagent_across_turns,
    correlate_subagent_invocations, normalized_subagent_activations,
    parent_correlation_needs_child_transcript, subagent_anchor_turn_index, CorrelationMatch,
    SubagentIdentity, SubagentIdentitySource, SubagentInterruptionEvidence,
    SubagentInvocationMatch, SubagentLifecycle, SubagentProtocol,
};
pub use correlation_discovery::subagent_discovery_plan;
pub use io::{read_new_jsonl, read_new_jsonl_limited, split_non_empty_lines};
pub use parent_sync::is_child_sync_in_parent;
pub(crate) use semantics::has_native_usage_record;
pub(crate) use semantics::normalize_turn_semantics_with_usage_mode;
use semantics::timestamp_nanos;
pub use semantics::{
    normalize_turn_semantics, InvocationEndSource, InvocationStartSource, InvocationTerminalSource,
    InvocationTiming, ModelInvocation, NormalizedTurnSemantics, ToolCallback,
};
pub use session_meta::{
    extract_session_meta_ids, extract_session_meta_ids_from_path, resolve_turn_trigger,
    try_extract_session_meta_ids_from_path, SessionMetaIds, SubagentSourceInfo,
};
#[cfg(test)]
use skill::detect_skill_from_skill_tag;
use skill::{
    apply_omx_route, detect_leading_skill_placeholder, detect_skill_from_tool_input,
    detect_skill_from_user_message, finalize_skill_invocations,
    merge_user_message_evidence_into_finalized_turn, parse_item_completed_user_message,
    parse_omx_user_prompt_submit_route, parse_skill_definition,
    parse_skill_invocations_from_text_elements, push_skill_invocation_dedup,
};
pub use skill::{
    extract_skill_name_from_command_text, LoadedSkillDefinition, SkillInvocation,
    SkillInvocationSource,
};
pub(crate) use skill::extract_skill_read_name;
use tool_normalization::{
    extract_agent_message_content, extract_agent_message_type, extract_pty_session_id,
    mcp_tool_result_output, normalize_tool_call, parse_jsonish_value, response_tool_name,
    upsert_assistant_message,
};
pub(crate) use tool_normalization::{is_shell_tool, shell_exit_code};
pub use turn_builder::build_turns;
#[cfg(test)]
pub(crate) use turn_builder::build_turns_with_initial_input_tokens;
pub(crate) use turn_builder::build_turns_with_usage_context;
pub use types::{
    AssistantMessage, CompactionEvent, TerminalError, TokenUsage, TokenUsageSource, ToolCall, Turn,
    TurnTrigger,
};

#[cfg(test)]
mod tests;
