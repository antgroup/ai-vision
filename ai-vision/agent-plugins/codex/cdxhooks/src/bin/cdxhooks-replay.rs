//! Replay tool for visualizing Codex JSONL transcript files as terminal trace trees.
//!
//! Usage:
//!   cdxhooks-replay <FILE> [OPTIONS]
//!
//! Options:
//!   --no-color          Disable ANSI color output
//!   --verbose           Show full text content (no truncation)
//!   --summary           Show summary statistics only
//!   --turn <N>          Show only the Nth turn (1-based)
//!   --otel [OUTPUT]     Convert to OTel JSONL (default: stdout)
//!   --session-id <ID>   Override session ID for OTel output
//!   --username <NAME>   Override username for OTel output
//!   --subagents [DIR]   Also load child sessions from DIR (default: same dir as FILE).
//!                       Requires --otel. Keeps one trace per parent turn.

use std::collections::{HashMap, HashSet};
use std::fs;
use std::io::{self, BufWriter, Write};
use std::path::{Path, PathBuf};
use std::process;

use agentic_otel_common::otel_trace_id;
use cdxhooks::common::otel::{build_otel_export_request_for_turn, TurnReplayOpts};
use cdxhooks::common::payload_budget::validate_export_body_span_budget;
use cdxhooks::common::session_discovery::discover_child_session_transcripts;
#[cfg(test)]
use cdxhooks::common::transcript::CollabAgentEvent;
use cdxhooks::common::transcript::{
    build_turns, extract_session_meta_ids, subagent_discovery_plan, AssistantMessage,
    CollabInvocationState, SubagentDiscoveryDisposition, SubagentDiscoveryPlan, TokenUsage,
    ToolCall, Turn,
};

// --- ANSI color helpers ---

const GREEN: &str = "\x1b[32m";
const BLUE: &str = "\x1b[34m";
const YELLOW: &str = "\x1b[33m";
const CYAN: &str = "\x1b[36m";
const GRAY: &str = "\x1b[90m";
const BOLD: &str = "\x1b[1m";
const RESET: &str = "\x1b[0m";

fn color(code: &str, text: &str, use_color: bool) -> String {
    if use_color {
        format!("{}{}{}", code, text, RESET)
    } else {
        text.to_string()
    }
}

fn bold_color(color_code: &str, text: &str, use_color: bool) -> String {
    if use_color {
        format!("{}{}{}{}", BOLD, color_code, text, RESET)
    } else {
        text.to_string()
    }
}

// --- CLI argument parsing ---

#[derive(Debug)]
struct CliArgs {
    collection_mode: String,
    file_path: String,
    no_color: bool,
    verbose: bool,
    summary: bool,
    turn_filter: Option<usize>,
    /// --otel mode: None = disabled, Some(None) = stdout, Some(Some(path)) = file
    otel_output: Option<Option<String>>,
    session_id_override: Option<String>,
    username_override: Option<String>,
    /// --subagents mode: None = disabled, Some(None) = auto-detect dir, Some(Some(path)) = explicit dir
    subagents_dir: Option<Option<String>>,
}

fn parse_args(args: &[String]) -> Result<CliArgs, String> {
    if args.len() < 2 {
        return Err("Missing required argument: <FILE>".to_string());
    }

    let mut collection_mode = "full".to_string();
    let mut file_path: Option<String> = None;
    let mut no_color = false;
    let mut verbose = false;
    let mut summary = false;
    let mut turn_filter: Option<usize> = None;
    let mut otel_output: Option<Option<String>> = None;
    let mut session_id_override: Option<String> = None;
    let mut username_override: Option<String> = None;
    let mut subagents_dir: Option<Option<String>> = None;
    let mut skip_next = false;

    for (index, arg) in args[1..].iter().enumerate() {
        if skip_next {
            skip_next = false;
            continue;
        }
        match arg.as_str() {
            "--collection-mode" => {
                collection_mode = args.get(index + 2).ok_or("--collection-mode requires full or privacy")?.trim().to_ascii_lowercase();
                if collection_mode != "full" && collection_mode != "privacy" { return Err("--collection-mode must be full or privacy".into()); }
                skip_next = true;
            }
            "--no-color" => no_color = true,
            "--verbose" => verbose = true,
            "--summary" => summary = true,
            "--turn" => {
                let next_index = index + 1;
                if next_index >= args.len() - 1 {
                    return Err("--turn requires a number argument".to_string());
                }
                let num_str = &args[next_index + 1];
                turn_filter = Some(
                    num_str
                        .parse::<usize>()
                        .map_err(|_| format!("Invalid turn number: {}", num_str))?,
                );
                skip_next = true;
            }
            "--otel" => {
                let next_index = index + 2;
                if next_index < args.len() && !args[next_index].starts_with("--") {
                    otel_output = Some(Some(args[next_index].clone()));
                    skip_next = true;
                } else {
                    otel_output = Some(None);
                }
            }
            "--session-id" => {
                let next_index = index + 2;
                if next_index >= args.len() {
                    return Err("--session-id requires a value".to_string());
                }
                session_id_override = Some(args[next_index].clone());
                skip_next = true;
            }
            "--username" => {
                let next_index = index + 2;
                if next_index >= args.len() {
                    return Err("--username requires a value".to_string());
                }
                username_override = Some(args[next_index].clone());
                skip_next = true;
            }
            "--subagents" => {
                let next_index = index + 2;
                if next_index < args.len() && !args[next_index].starts_with("--") {
                    subagents_dir = Some(Some(args[next_index].clone()));
                    skip_next = true;
                } else {
                    subagents_dir = Some(None); // auto-detect: same dir as main file
                }
            }
            other => {
                if other.starts_with('-') {
                    return Err(format!("Unknown option: {}", other));
                }
                if file_path.is_some() {
                    return Err("Multiple file arguments provided".to_string());
                }
                file_path = Some(other.to_string());
            }
        }
    }

    let file_path = file_path.ok_or_else(|| "Missing required argument: <FILE>".to_string())?;
    Ok(CliArgs {
        collection_mode,
        file_path,
        no_color,
        verbose,
        summary,
        turn_filter,
        otel_output,
        session_id_override,
        username_override,
        subagents_dir,
    })
}

fn print_usage() {
    eprintln!("  --collection-mode full|privacy  Explicit replay mode (default: full; shell mode ignored)");
    eprintln!("Usage: cdxhooks-replay <FILE> [OPTIONS]");
    eprintln!();
    eprintln!("Options:");
    eprintln!("  --no-color          Disable ANSI color output");
    eprintln!("  --verbose           Show full text content (no truncation)");
    eprintln!("  --summary           Show summary statistics only");
    eprintln!("  --turn <N>          Show only the Nth turn (1-based)");
    eprintln!("  --otel [OUTPUT]     Convert to OTel JSONL (default: stdout)");
    eprintln!("  --session-id <ID>   Override session ID for OTel output");
    eprintln!("  --username <NAME>   Override username for OTel output");
    eprintln!("  --subagents [DIR]   Load child sessions from DIR (default: FILE's directory).");
    eprintln!("                      Requires --otel. Keeps each parent turn in its own");
    eprintln!("                      trace and attaches matching child spans inside it.");
}

// --- Text truncation ---

const MAX_DISPLAY_LINES: usize = 3;

fn truncate_line(line: &str, max_len: usize) -> String {
    let char_count = line.chars().count();
    if char_count <= max_len {
        line.to_string()
    } else {
        let truncated: String = line.chars().take(max_len).collect();
        format!("{}...", truncated)
    }
}

fn truncate_text_lines(text: &str, max_len: usize, verbose: bool) -> Vec<String> {
    let lines: Vec<&str> = text
        .lines()
        .map(|l| l.trim())
        .filter(|l| !l.is_empty())
        .collect();

    if lines.is_empty() {
        return vec!["(empty)".to_string()];
    }

    if verbose {
        return lines.iter().map(|l| truncate_line(l, max_len)).collect();
    }

    let total = lines.len();
    let take_count = total.min(MAX_DISPLAY_LINES);
    let mut result: Vec<String> = lines[..take_count]
        .iter()
        .map(|l| truncate_line(l, max_len))
        .collect();

    if total > MAX_DISPLAY_LINES {
        result.push(format!("... ({} more lines)", total - MAX_DISPLAY_LINES));
    }

    result
}

fn format_multiline(lines: &[String], first_prefix: &str, cont_prefix: &str) -> String {
    let mut output = String::new();
    for (i, line) in lines.iter().enumerate() {
        if i == 0 {
            output.push_str(&format!("{}{}\n", first_prefix, line));
        } else {
            output.push_str(&format!("{}{}\n", cont_prefix, line));
        }
    }
    output
}

// --- Token usage formatting ---

fn format_usage(usage: &TokenUsage) -> String {
    let mut parts = Vec::new();
    if let Some(input) = usage.input {
        parts.push(format!("in:{}", input));
    }
    if let Some(output) = usage.output {
        parts.push(format!("out:{}", output));
    }
    if let Some(cached_input) = usage.cached_input {
        if cached_input > 0 {
            parts.push(format!("cached:{}", cached_input));
        }
    }
    if let Some(reasoning_output) = usage.reasoning_output {
        if reasoning_output > 0 {
            parts.push(format!("reasoning:{}", reasoning_output));
        }
    }
    if parts.is_empty() {
        return String::new();
    }
    format!("[{}]", parts.join(" "))
}

// --- Tree rendering ---

fn render_trace_tree(turns: &[Turn], use_color: bool, verbose: bool) -> String {
    let mut output = String::new();
    let max_text_len = if verbose { usize::MAX } else { 80 };

    output.push_str(&bold_color("", "Trace Tree", use_color));
    output.push('\n');

    for (turn_index, turn) in turns.iter().enumerate() {
        let is_last_turn = turn_index == turns.len() - 1;
        let turn_connector = if is_last_turn {
            "└── "
        } else {
            "├── "
        };
        let turn_prefix = if is_last_turn { "    " } else { "│   " };

        let turn_label = format!("Turn {} ", turn_index + 1);
        let timestamp_label = format!("[{}]", &turn.user_timestamp);
        let model_label = if turn.model.is_empty() {
            String::new()
        } else {
            format!(" ({})", turn.model)
        };

        output.push_str(&format!(
            "{}{}{}{}",
            turn_connector,
            bold_color(GREEN, &turn_label, use_color),
            color(GRAY, &timestamp_label, use_color),
            color(CYAN, &model_label, use_color),
        ));
        output.push('\n');

        let user_label = color(GREEN, "User:", use_color);
        let user_lines = truncate_text_lines(&turn.user_text, max_text_len, verbose);
        let user_first = format!("{}├── {} ", turn_prefix, user_label);
        let user_cont = format!("{}│        ", turn_prefix);
        output.push_str(&format_multiline(&user_lines, &user_first, &user_cont));

        enum TurnChild<'a> {
            Assistant(&'a AssistantMessage),
            Tool(&'a ToolCall),
        }

        let mut children: Vec<TurnChild> = Vec::new();
        for assistant in &turn.assistants {
            children.push(TurnChild::Assistant(assistant));
        }
        for tool_call in &turn.tool_calls {
            children.push(TurnChild::Tool(tool_call));
        }

        for (child_index, child) in children.iter().enumerate() {
            let is_last = child_index == children.len() - 1;
            let connector = if is_last { "└── " } else { "├── " };
            let cont = if is_last { "    " } else { "│   " };

            match child {
                TurnChild::Assistant(assistant) => {
                    render_assistant(
                        &mut output,
                        assistant,
                        turn,
                        turn_prefix,
                        connector,
                        cont,
                        use_color,
                        max_text_len,
                        verbose,
                    );
                }
                TurnChild::Tool(tool_call) => {
                    let tool_label = format!("{} ({})", tool_call.name, tool_call.call_id);
                    output.push_str(&format!(
                        "{}{}{}\n",
                        turn_prefix,
                        connector,
                        color(YELLOW, &tool_label, use_color),
                    ));
                    if verbose {
                        let input_str = serde_json::to_string(&tool_call.input).unwrap_or_default();
                        let input_lines = truncate_text_lines(&input_str, max_text_len, true);
                        let input_first = format!("{}{}  input: ", turn_prefix, cont);
                        let input_cont = format!("{}{}         ", turn_prefix, cont);
                        output.push_str(&format_multiline(&input_lines, &input_first, &input_cont));
                    }
                }
            }
        }
    }

    output
}

fn render_assistant(
    output: &mut String,
    assistant: &AssistantMessage,
    turn: &Turn,
    turn_prefix: &str,
    connector: &str,
    cont_prefix: &str,
    use_color: bool,
    max_text_len: usize,
    verbose: bool,
) {
    let model_label = if turn.model.is_empty() {
        String::new()
    } else {
        format!(" ({})", turn.model)
    };
    let usage_label = if assistant.usage.has_any() {
        format!(" {}", format_usage(&assistant.usage))
    } else {
        String::new()
    };
    let phase_label = if assistant.phase.is_empty() || assistant.phase == "final_answer" {
        String::new()
    } else {
        format!(" [{}]", assistant.phase)
    };

    let header = format!(
        "{}{}{}{}{}{}",
        turn_prefix,
        connector,
        color(BLUE, "Assistant", use_color),
        color(GRAY, &model_label, use_color),
        color(GRAY, &usage_label, use_color),
        color(CYAN, &phase_label, use_color),
    );

    if assistant.text.is_empty() {
        output.push_str(&header);
        output.push('\n');
    } else {
        let text_lines = truncate_text_lines(&assistant.text, max_text_len, verbose);
        let first = format!("{}: ", header);
        let text_indent = format!("{}{}  ", turn_prefix, cont_prefix);
        output.push_str(&format_multiline(&text_lines, &first, &text_indent));
    }
}

// --- Summary rendering ---

fn render_summary(turns: &[Turn], use_color: bool) -> String {
    let mut output = String::new();

    let total_turns = turns.len();
    let total_assistants: usize = turns.iter().map(|t| t.assistants.len()).sum();
    let total_tool_calls: usize = turns.iter().map(|t| t.tool_calls.len()).sum();

    let mut total_input_tokens: i64 = 0;
    let mut total_output_tokens: i64 = 0;
    let mut total_cached: i64 = 0;
    let mut total_reasoning: i64 = 0;

    let mut models: Vec<String> = Vec::new();
    let mut tool_counts: Vec<(String, usize)> = Vec::new();

    for turn in turns {
        if !turn.model.is_empty() && !models.contains(&turn.model) {
            models.push(turn.model.clone());
        }
        for assistant in &turn.assistants {
            if let Some(input) = assistant.usage.input {
                total_input_tokens += input;
            }
            if let Some(out) = assistant.usage.output {
                total_output_tokens += out;
            }
            if let Some(cached) = assistant.usage.cached_input {
                total_cached += cached;
            }
            if let Some(reasoning) = assistant.usage.reasoning_output {
                total_reasoning += reasoning;
            }
        }
        for tool_call in &turn.tool_calls {
            if let Some(entry) = tool_counts
                .iter_mut()
                .find(|(name, _)| name == &tool_call.name)
            {
                entry.1 += 1;
            } else {
                tool_counts.push((tool_call.name.clone(), 1));
            }
        }
    }

    let first_timestamp = turns
        .first()
        .map(|t| t.user_timestamp.as_str())
        .unwrap_or("N/A");
    let last_timestamp = turns
        .last()
        .and_then(|t| {
            t.assistants
                .last()
                .map(|a| a.timestamp.as_str())
                .or(Some(t.user_timestamp.as_str()))
        })
        .unwrap_or("N/A");

    output.push_str(&bold_color("", "Summary", use_color));
    output.push('\n');
    output.push_str(&format!(
        "  {} {}\n",
        color(BOLD, "Turns:", use_color),
        total_turns
    ));
    output.push_str(&format!(
        "  {} {}\n",
        color(BOLD, "Assistant messages:", use_color),
        total_assistants
    ));
    output.push_str(&format!(
        "  {} {}\n",
        color(BOLD, "Tool calls:", use_color),
        total_tool_calls
    ));
    output.push_str(&format!(
        "  {} input={} output={} cached={} reasoning={}\n",
        color(BOLD, "Tokens:", use_color),
        total_input_tokens,
        total_output_tokens,
        total_cached,
        total_reasoning,
    ));
    output.push_str(&format!(
        "  {} {}\n",
        color(BOLD, "Models:", use_color),
        if models.is_empty() {
            "N/A".to_string()
        } else {
            models.join(", ")
        }
    ));

    if !tool_counts.is_empty() {
        output.push_str(&format!(
            "  {}\n",
            color(BOLD, "Tool breakdown:", use_color)
        ));
        tool_counts.sort_by(|a, b| b.1.cmp(&a.1));
        for (name, count) in &tool_counts {
            output.push_str(&format!("    {} ({})\n", name, count));
        }
    }

    output.push_str(&format!(
        "  {} {} -> {}\n",
        color(BOLD, "Time span:", use_color),
        first_timestamp,
        last_timestamp,
    ));

    output
}

// --- OTel JSONL output ---

fn extract_session_id_from_content(content: &str) -> Option<String> {
    content
        .lines()
        .filter(|l| !l.trim().is_empty())
        .filter_map(|l| serde_json::from_str::<serde_json::Value>(l).ok())
        .find_map(|v| {
            if v.get("type").and_then(|t| t.as_str()) == Some("session_meta") {
                v["payload"]["id"]
                    .as_str()
                    .or_else(|| v["payload"]["session_id"].as_str())
                    .map(|s| s.to_string())
            } else {
                v["sessionId"]
                    .as_str()
                    .or_else(|| v["session_id"].as_str())
                    .map(|s| s.to_string())
            }
        })
}

fn replay_otel(turns: &[Turn], cli: &CliArgs, content: &str) {
    let session_id = cli
        .session_id_override
        .clone()
        .or_else(|| extract_session_id_from_content(content))
        .unwrap_or_else(|| "replay".to_string());

    let username = cli
        .username_override
        .clone()
        .or_else(|| std::env::var("USER").ok())
        .unwrap_or_else(|| "replay".to_string());

    let version = env!("CARGO_PKG_VERSION");

    let mut writer = open_otel_writer(&cli.otel_output);

    for (i, turn) in turns.iter().enumerate() {
        let otel =
            build_otel_export_request_for_turn(turn, i + 1, &session_id, &username, version, None);
        write_otel_export(writer.as_mut(), &otel).unwrap_or_else(|e| {
            eprintln!("Write error: {}", e);
            process::exit(1);
        });
    }

    eprintln!("Replayed {} turns -> OTel JSONL", turns.len());
}

// --- Subagent session discovery ---

#[derive(Clone)]
struct ChildSession {
    /// Codex thread_id of this child session (matches parent's new_thread_id)
    thread_id: String,
    turns: Vec<Turn>,
    nickname: String,
    role: String,
    agent_path: Option<String>,
}

#[derive(Default)]
struct ReplayDiscoverySummary {
    expected_ids: HashSet<String>,
    error_shell_ids: HashSet<String>,
    turn_plans: Vec<SubagentDiscoveryPlan>,
}

fn replay_discovery_summary(main_turns: &[Turn]) -> ReplayDiscoverySummary {
    let mut summary = ReplayDiscoverySummary::default();
    for anchor_turn_index in 0..main_turns.len() {
        let plan = subagent_discovery_plan(main_turns, anchor_turn_index);
        for decision in &plan.decisions {
            if decision.disposition != SubagentDiscoveryDisposition::Eligible
                && decision.reason != "native_terminal_pending"
            {
                continue;
            }
            let Some(child) = decision.child.as_ref() else {
                continue;
            };
            summary.expected_ids.insert(child.session_id.clone());
            if decision
                .activation
                .as_ref()
                .is_some_and(|activation| activation.state == CollabInvocationState::Error)
            {
                summary.error_shell_ids.insert(child.session_id.clone());
            }
        }
        summary.turn_plans.push(plan);
    }
    summary
}

/// Collect terminal and exactly identified pending child identities.
/// Ambiguous identities remain excluded from transcript discovery.
#[cfg(test)]
fn collect_expected_child_thread_ids(main_turns: &[Turn]) -> HashSet<String> {
    replay_discovery_summary(main_turns).expected_ids
}

/// Find child transcripts newest-first, preferring exact rollout filename IDs
/// before falling back to session_meta for legacy/nonstandard filenames.
fn discover_child_sessions(
    main_path: &Path,
    dir: &Path,
    expected_ids: &HashSet<String>,
    main_session_id: &str,
) -> Vec<ChildSession> {
    if expected_ids.is_empty() && main_session_id.is_empty() {
        return vec![];
    }

    let mut sessions = Vec::new();
    let discovery = discover_child_session_transcripts(
        &[dir.to_path_buf()],
        expected_ids,
        main_session_id,
        Some(main_path),
        true,
    );
    eprintln!(
        "  Discovery: found={}, exact={}, metadata_reads={}, directories={}",
        discovery.matches.len(),
        discovery.exact_filename_matches,
        discovery.metadata_files_read,
        discovery.directories_visited,
    );

    for discovered in discovery.matches {
        let Ok(content) = fs::read_to_string(&discovered.path) else {
            continue;
        };

        let parsed: Vec<serde_json::Value> = content
            .lines()
            .filter(|l| !l.trim().is_empty())
            .filter_map(|l| serde_json::from_str(l).ok())
            .collect();

        let turns = build_turns(&parsed);
        if turns.is_empty() {
            eprintln!(
                "Warning: child session '{}' (thread={}) has no turns, skipping",
                discovered.path.display(),
                discovered.identity_id
            );
            continue;
        }

        eprintln!(
            "  Found child session: {} ({} turns, thread={})",
            discovered
                .path
                .file_name()
                .unwrap_or_default()
                .to_string_lossy(),
            turns.len(),
            discovered.identity_id,
        );

        let (nickname, role, agent_path) = turns
            .first()
            .and_then(|turn| turn.subagent_source.as_ref())
            .map(|source| {
                (
                    source.agent_nickname.clone().unwrap_or_default(),
                    source.agent_role.clone().unwrap_or_default(),
                    source.agent_path.clone(),
                )
            })
            .unwrap_or_default();
        sessions.push(ChildSession {
            thread_id: discovered.identity_id,
            turns,
            nickname,
            role,
            agent_path,
        });
    }

    sessions
}

fn infer_sessions_root(path: &Path) -> Option<PathBuf> {
    for ancestor in path.ancestors() {
        if ancestor.file_name().and_then(|name| name.to_str()) == Some("sessions") {
            return Some(ancestor.to_path_buf());
        }
    }
    None
}

/// Merge child sessions into the main turn's OTel export using the shared function.
fn merge_subagent_spans(
    otel: &mut serde_json::Value,
    parent_turns: &[Turn],
    anchor_turn_index: usize,
    _trace_id: &str,
    child_sessions: &[ChildSession],
    _subagent_span_ids: &HashMap<String, String>,
    username: &str,
    version: &str,
) {
    use cdxhooks::common::otel::{merge_subagent_sessions_into_otel_across_turns, SubagentChild};

    let children: Vec<SubagentChild> = child_sessions
        .iter()
        .map(|cs| {
            let src = cs.turns.first().and_then(|t| t.subagent_source.as_ref());
            SubagentChild {
                session_id: cs.thread_id.clone(),
                turns: cs.turns.clone(),
                nickname: (!cs.nickname.trim().is_empty())
                    .then(|| cs.nickname.clone())
                    .or_else(|| src.and_then(|s| s.agent_nickname.clone()))
                    .unwrap_or_default(),
                role: (!cs.role.trim().is_empty())
                    .then(|| cs.role.clone())
                    .or_else(|| src.and_then(|s| s.agent_role.clone()))
                    .unwrap_or_default(),
                agent_path: cs
                    .agent_path
                    .clone()
                    .or_else(|| src.and_then(|s| s.agent_path.clone())),
            }
        })
        .collect();

    let result = merge_subagent_sessions_into_otel_across_turns(
        otel,
        parent_turns,
        anchor_turn_index,
        &children,
        username,
        version,
    );
    for (subject_id, reason) in result.correlation_reasons {
        eprintln!(
            "  Subagent correlation: subject={} reason={}",
            subject_id, reason
        );
    }
}

// --- Multi-session OTel replay ---

fn build_otel_exports_with_subagents(
    main_turns: &[Turn],
    main_session_id: &str,
    username: &str,
    version: &str,
    child_sessions: &[ChildSession],
) -> Vec<serde_json::Value> {
    let mut otels = Vec::new();
    let discovery = replay_discovery_summary(main_turns);

    // Replay and the live hook must use the same activation-aware merge path.
    // Child identity discovery happens once at session scope, while the shared
    // merge assigns child turns to spawn_agent/followup_task activation windows.
    for (i, turn) in main_turns.iter().enumerate() {
        let trace_id = otel_trace_id();
        let main_opts = TurnReplayOpts {
            trace_id: Some(trace_id.clone()),
            root_span_id: None,
            parent_span_id: None,
            sofa_traceid: None,
            sofa_rpcid: None,
            billing_mode: None,
            generation_span_ids: HashMap::new(),
            subagent_span_ids: HashMap::new(),
        };
        let mut otel = build_otel_export_request_for_turn(
            turn,
            i + 1,
            main_session_id,
            username,
            version,
            Some(&main_opts),
        );

        let plan = &discovery.turn_plans[i];
        {
            let eligible_ids = plan
                .decisions
                .iter()
                .filter(|decision| {
                    decision.disposition == SubagentDiscoveryDisposition::Eligible
                        || decision.reason == "native_terminal_pending"
                })
                .filter_map(|decision| decision.child.as_ref())
                .map(|child| child.session_id.as_str())
                .collect::<HashSet<_>>();
            let mut turn_children = child_sessions
                .iter()
                .filter(|child| eligible_ids.contains(child.thread_id.as_str()))
                .cloned()
                .collect::<Vec<_>>();
            for decision in &plan.decisions {
                let Some(activation) = decision.activation.as_ref() else {
                    continue;
                };
                if decision.disposition != SubagentDiscoveryDisposition::Eligible
                    || activation.state != CollabInvocationState::Error
                {
                    continue;
                }
                let Some(child) = decision.child.as_ref() else {
                    continue;
                };
                if turn_children
                    .iter()
                    .any(|existing| existing.thread_id == child.session_id)
                {
                    continue;
                }
                turn_children.push(ChildSession {
                    thread_id: child.session_id.clone(),
                    turns: vec![],
                    nickname: activation.nickname.clone().unwrap_or_default(),
                    role: activation.role.clone().unwrap_or_default(),
                    agent_path: child.agent_path.clone(),
                });
            }

            merge_subagent_spans(
                &mut otel,
                main_turns,
                i,
                &trace_id,
                &turn_children,
                &HashMap::new(),
                username,
                version,
            );
        }

        otels.push(otel);
    }

    otels
}

fn replay_otel_with_subagents(main_turns: &[Turn], main_content: &str, dir: &Path, cli: &CliArgs) {
    let main_session_id = cli
        .session_id_override
        .clone()
        .or_else(|| extract_session_id_from_content(main_content))
        .unwrap_or_else(|| "replay".to_string());

    let username = cli
        .username_override
        .clone()
        .or_else(|| std::env::var("USER").ok())
        .unwrap_or_else(|| "replay".to_string());

    let version = env!("CARGO_PKG_VERSION");

    // Discover only classifier-eligible child sessions. Canonical decisions
    // are logged without prompt/output payloads for live/replay parity.
    let discovery = replay_discovery_summary(main_turns);
    let expected_ids = &discovery.expected_ids;
    for (turn_index, plan) in discovery.turn_plans.iter().enumerate() {
        for decision in &plan.decisions {
            eprintln!(
                "  Subagent discovery: turn={} disposition={:?} reason={}",
                turn_index + 1,
                decision.disposition,
                decision.reason
            );
        }
        for observation in &plan.observations {
            eprintln!(
                "  Subagent discovery: turn={} disposition=ObservedNoMerge reason={}",
                turn_index + 1,
                observation.reason
            );
        }
    }
    if expected_ids.is_empty() {
        eprintln!("Note: no classifier-eligible child activations; replay remains parent-only.");
    }

    eprintln!("Looking for child session(s) in '{}'...", dir.display());
    let child_sessions = discover_child_sessions(
        &PathBuf::from(cli.file_path.as_str()),
        dir,
        expected_ids,
        &main_session_id,
    );

    if child_sessions.is_empty() && discovery.error_shell_ids.is_empty() {
        eprintln!("  No child sessions found. Single-session replay.");
        replay_otel(main_turns, cli, main_content);
        return;
    }

    let mut writer = open_otel_writer(&cli.otel_output);

    let otels = build_otel_exports_with_subagents(
        main_turns,
        &main_session_id,
        &username,
        version,
        &child_sessions,
    );

    for otel in &otels {
        write_otel_export(writer.as_mut(), otel).unwrap_or_else(|e| {
            eprintln!("Write error: {}", e);
            process::exit(1);
        });
    }

    let found_count = child_sessions.len() + discovery.error_shell_ids.len();
    let mut found_expected_ids = child_sessions
        .iter()
        .filter(|child| expected_ids.contains(&child.thread_id))
        .map(|child| child.thread_id.clone())
        .collect::<HashSet<_>>();
    found_expected_ids.extend(discovery.error_shell_ids.iter().cloned());
    let missing_ids = expected_ids
        .difference(&found_expected_ids)
        .cloned()
        .collect::<HashSet<_>>();
    let missing = missing_ids.len();
    eprintln!(
        "Replayed {} main turns + {} child session(s) -> OTel JSONL ({} trace(s), split by parent turn)",
        main_turns.len(),
        found_count,
        main_turns.len(),
    );
    if missing > 0 {
        eprintln!(
            "Warning: {} child session(s) not found in '{}'. \
             Missing transcript files for: {:?}",
            missing,
            dir.display(),
            missing_ids,
        );
    }
}

// --- Shared writer helper ---

fn write_otel_export(writer: &mut dyn Write, otel: &serde_json::Value) -> Result<(), String> {
    validate_export_body_span_budget(otel)?;
    let serialized = serde_json::to_string(otel).map_err(|error| error.to_string())?;
    writeln!(writer, "{serialized}").map_err(|error| error.to_string())
}

fn open_otel_writer(otel_output: &Option<Option<String>>) -> Box<dyn Write> {
    match otel_output {
        Some(Some(path)) => {
            let file = fs::File::create(path).unwrap_or_else(|e| {
                eprintln!("Error creating '{}': {}", path, e);
                process::exit(1);
            });
            Box::new(BufWriter::new(file))
        }
        _ => Box::new(BufWriter::new(io::stdout().lock())),
    }
}

fn write_empty_otel_output(otel_output: &Option<Option<String>>) -> Result<(), String> {
    let Some(Some(path)) = otel_output else {
        return Ok(());
    };
    fs::write(path, "").map_err(|error| format!("Error creating '{}': {}", path, error))
}

// --- Main ---

fn main() {
    let args: Vec<String> = std::env::args().collect();

    let cli = match parse_args(&args) {
        Ok(cli) => cli,
        Err(err) => {
            eprintln!("Error: {}", err);
            eprintln!();
            print_usage();
            process::exit(1);
        }
    };

    // Historical input/explicit replay options, not the invoking shell, own mode.
    std::env::set_var("AIVISION_COLLECTION_MODE", &cli.collection_mode);
    std::env::set_var("CDXHOOKS_COLLECTION_MODE", &cli.collection_mode);
    if cli.collection_mode == "privacy" && cli.otel_output.is_none() {
        eprintln!("Privacy replay requires --otel; raw transcript display is disabled.");
        process::exit(1);
    }

    let content = match fs::read_to_string(&cli.file_path) {
        Ok(content) => content,
        Err(err) => {
            eprintln!("Error reading file '{}': {}", cli.file_path, err);
            process::exit(1);
        }
    };

    let parsed_lines: Vec<serde_json::Value> = content
        .lines()
        .filter(|line| !line.trim().is_empty())
        .filter_map(|line| serde_json::from_str(line).ok())
        .collect();

    if parsed_lines.is_empty() {
        eprintln!("No valid JSONL lines found in '{}'", cli.file_path);
        process::exit(1);
    }

    let turns = build_turns(&parsed_lines);

    if turns.is_empty() {
        if cli.otel_output.is_some() {
            eprintln!(
                "Skipping transcript '{}' with no observable turns",
                cli.file_path
            );
            if let Err(error) = write_empty_otel_output(&cli.otel_output) {
                eprintln!("{error}");
                process::exit(1);
            }
            return;
        }
        eprintln!("No turns found in '{}'", cli.file_path);
        process::exit(1);
    }

    // --subagents requires --otel
    if cli.subagents_dir.is_some() && cli.otel_output.is_none() {
        eprintln!("Error: --subagents requires --otel");
        process::exit(1);
    }

    // If --subagents is active and this file IS a child session, skip it —
    // the parent's replay will inline this child via merge_subagent_spans.
    // Codex sub-agents are always sync (spawn_agent + wait_agent), so there
    // is no separate "async" path to take here.
    if cli.subagents_dir.is_some() {
        let meta = extract_session_meta_ids(&content);
        if meta.parent_thread_id.is_some() {
            eprintln!(
                "Skipping child session '{}' (inlined by parent via --subagents)",
                cli.file_path
            );
            if let Some(ref otel_path) = cli.otel_output {
                if let Some(path) = otel_path {
                    let _ = fs::write(path, "");
                }
            }
            return;
        }
    }

    // OTel JSONL mode with subagent loading
    if cli.otel_output.is_some() {
        if let Some(ref sub_dir_opt) = cli.subagents_dir {
            let dir: PathBuf = match sub_dir_opt {
                Some(d) => PathBuf::from(d),
                None => {
                    let file_path = PathBuf::from(&cli.file_path);
                    infer_sessions_root(&file_path).unwrap_or_else(|| {
                        // Fallback for fixtures outside the Codex sessions tree.
                        file_path.parent().unwrap_or(Path::new(".")).to_path_buf()
                    })
                }
            };
            replay_otel_with_subagents(&turns, &content, &dir, &cli);
            return;
        }
        replay_otel(&turns, &cli, &content);
        return;
    }

    // Terminal tree / summary mode
    let use_color = !cli.no_color;

    let display_turns: Vec<&Turn> = if let Some(turn_num) = cli.turn_filter {
        if turn_num == 0 || turn_num > turns.len() {
            eprintln!("Turn {} out of range (1-{})", turn_num, turns.len());
            process::exit(1);
        }
        vec![&turns[turn_num - 1]]
    } else {
        turns.iter().collect()
    };

    if cli.summary {
        let owned: Vec<Turn> = display_turns.into_iter().cloned().collect();
        print!("{}", render_summary(&owned, use_color));
    } else {
        let owned: Vec<Turn> = display_turns.into_iter().cloned().collect();
        print!("{}", render_trace_tree(&owned, use_color, cli.verbose));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use cdxhooks::common::transcript::TurnTerminalState;
    use serde_json::json;

    #[test]
    fn empty_otel_output_truncates_metadata_only_replay_target() {
        let dir = tempfile::tempdir().unwrap();
        let output = dir.path().join("empty.jsonl");
        fs::write(&output, "stale payload").unwrap();

        write_empty_otel_output(&Some(Some(output.to_string_lossy().to_string()))).unwrap();

        assert_eq!(fs::read_to_string(output).unwrap(), "");
    }

    #[test]
    fn replay_writer_fails_closed_for_an_oversized_bare_span() {
        let body = json!({
            "resourceSpans": [{
                "scopeSpans": [{
                    "spans": [{
                        "spanId": "oversized",
                        "name": "x".repeat(cdxhooks::common::payload_budget::MAX_SERIALIZED_SPAN_BYTES)
                    }]
                }]
            }]
        });
        let mut output = Vec::new();

        let error = write_otel_export(&mut output, &body).unwrap_err();

        assert!(error.contains("otel_span_oversize"));
        assert!(output.is_empty());
    }

    fn make_turn(
        turn_id: &str,
        user_timestamp: &str,
        tool_calls: Vec<ToolCall>,
        collab_agent_events: Vec<CollabAgentEvent>,
    ) -> Turn {
        Turn {
            chat_id: None,
            turn_id: turn_id.to_string(),
            user_timestamp: user_timestamp.to_string(),
            user_text: format!("user {}", turn_id),
            user_content: serde_json::Value::Null,
            assistants: vec![AssistantMessage {
                timestamp: user_timestamp.to_string(),
                text: format!("assistant {}", turn_id),
                media_content: serde_json::Value::Null,
                phase: "final_answer".to_string(),
                usage: TokenUsage::default(),
                token_count_timestamp: String::new(),
                invocation_started_timestamp: String::new(),
                invocation_end_source: None,
            }],
            tool_calls,
            model: "gpt-5.5".to_string(),
            cwd: "/tmp".to_string(),
            system_instructions: String::new(),
            completed: true,
            completion_timestamp: String::new(),
            terminal_state: TurnTerminalState::Completed,
            terminal_error: None,
            trigger: Default::default(),
            skill_invocations: vec![],
            skill_name: None,
            compactions: vec![],
            collab_agent_events,
            subagent_source: None,
        }
    }

    fn make_child_session(child_id: &str, ts: &str) -> ChildSession {
        ChildSession {
            thread_id: child_id.to_string(),
            turns: vec![make_turn(&format!("turn-{}", child_id), ts, vec![], vec![])],
            nickname: String::new(),
            role: String::new(),
            agent_path: None,
        }
    }

    fn subagent_tool(
        name: &str,
        call_id: &str,
        child_id: &str,
        started_timestamp: &str,
        ended_timestamp: &str,
    ) -> ToolCall {
        let (input, output) = if name == "spawn_agent" {
            (
                json!({"message": "work in child"}),
                json!({"agent_id": child_id}),
            )
        } else {
            (
                json!({"targets": [child_id]}),
                serde_json::Value::String("completed".to_string()),
            )
        };

        ToolCall {
            call_id: call_id.to_string(),
            name: name.to_string(),
            input,
            output,
            started_timestamp: started_timestamp.to_string(),
            ended_timestamp: ended_timestamp.to_string(),
            assistant_index: 0,
            success: Some(true),
        }
    }

    fn root_span(body: &serde_json::Value) -> &serde_json::Value {
        let spans = body
            .pointer("/resourceSpans/0/scopeSpans/0/spans")
            .and_then(|v| v.as_array())
            .expect("spans");

        spans
            .iter()
            .find(|span| {
                span.get("parentSpanId")
                    .and_then(|v| v.as_str())
                    .map_or(true, |parent| parent.is_empty())
            })
            .or_else(|| spans.first())
            .expect("root span")
    }

    fn count_spans_named(body: &serde_json::Value, name: &str) -> usize {
        body.pointer("/resourceSpans/0/scopeSpans/0/spans")
            .and_then(|v| v.as_array())
            .map(|spans| {
                spans
                    .iter()
                    .filter(|span| span.get("name").and_then(|v| v.as_str()) == Some(name))
                    .count()
            })
            .unwrap_or(0)
    }

    fn trace_ids_in_body(body: &serde_json::Value) -> HashSet<String> {
        body.pointer("/resourceSpans/0/scopeSpans/0/spans")
            .and_then(|v| v.as_array())
            .map(|spans| {
                spans
                    .iter()
                    .filter_map(|span| span.get("traceId").and_then(|v| v.as_str()))
                    .map(String::from)
                    .collect()
            })
            .unwrap_or_default()
    }

    fn all_spans(body: &serde_json::Value) -> Vec<&serde_json::Value> {
        body["resourceSpans"]
            .as_array()
            .into_iter()
            .flatten()
            .flat_map(|resource| resource["scopeSpans"].as_array().into_iter().flatten())
            .flat_map(|scope| scope["spans"].as_array().into_iter().flatten())
            .collect()
    }

    fn span_attr(span: &serde_json::Value, key: &str) -> Option<String> {
        span["attributes"].as_array()?.iter().find_map(|attribute| {
            (attribute["key"].as_str() == Some(key)).then(|| {
                attribute["value"]["stringValue"]
                    .as_str()
                    .unwrap_or_default()
                    .to_string()
            })
        })
    }

    fn native_fixture_turns() -> (Vec<Turn>, ChildSession) {
        let parent_lines =
            include_str!("../../tests/fixtures/codex_collab_tool_call_same_turn_parent.jsonl")
                .lines()
                .map(|line| serde_json::from_str::<serde_json::Value>(line).unwrap())
                .collect::<Vec<_>>();
        let child_lines =
            include_str!("../../tests/fixtures/codex_collab_tool_call_same_turn_child.jsonl")
                .lines()
                .map(|line| serde_json::from_str::<serde_json::Value>(line).unwrap())
                .collect::<Vec<_>>();
        let child_turns = build_turns(&child_lines);
        let source = child_turns[0].subagent_source.as_ref().unwrap();
        let child = ChildSession {
            thread_id: "child-collab".to_string(),
            turns: child_turns.clone(),
            nickname: source.agent_nickname.clone().unwrap_or_default(),
            role: source.agent_role.clone().unwrap_or_default(),
            agent_path: source.agent_path.clone(),
        };
        (build_turns(&parent_lines), child)
    }

    #[test]
    fn native_replay_uses_shared_classifier_and_preserves_semantic_topology() {
        let (parent_turns, child) = native_fixture_turns();
        let discovery = replay_discovery_summary(&parent_turns);
        assert_eq!(
            discovery.expected_ids,
            HashSet::from(["child-collab".to_string()])
        );
        assert_eq!(discovery.turn_plans[0].decisions.len(), 1);
        assert_eq!(
            discovery.turn_plans[0].decisions[0].reason,
            "native_terminal"
        );

        let otels = build_otel_exports_with_subagents(
            &parent_turns,
            "parent-collab",
            "tester",
            "test-version",
            &[child],
        );
        assert_eq!(otels.len(), 1);
        let spans = all_spans(&otels[0]);
        let root_spans = spans
            .iter()
            .filter(|span| span["parentSpanId"].as_str().is_none_or(str::is_empty))
            .collect::<Vec<_>>();
        assert_eq!(root_spans.len(), 1);
        let root_span_id = root_spans[0]["spanId"].as_str().unwrap();
        let agents = spans
            .iter()
            .filter(|span| span_attr(span, "gen_ai.span.kind").as_deref() == Some("AGENT"))
            .collect::<Vec<_>>();
        assert_eq!(agents.len(), 1);
        assert_eq!(agents[0]["name"], "agent Archimedes");
        assert_eq!(agents[0]["parentSpanId"], root_span_id);
        assert_eq!(
            span_attr(agents[0], "agentic.subagent.parent_tool_call_id").as_deref(),
            Some("collab-spawn-1")
        );
        assert_eq!(
            spans
                .iter()
                .filter(|span| {
                    span_attr(span, "agentic.tool.raw_name").as_deref() == Some("exec")
                })
                .count(),
            2,
            "opaque outer exec calls remain observable"
        );
        assert!(!spans.iter().any(|span| {
            matches!(
                span_attr(span, "agentic.tool.raw_name").as_deref(),
                Some("spawn_agent" | "wait" | "wait_agent")
            )
        }));
        let agent_span_id = agents[0]["spanId"].as_str().unwrap();
        assert!(spans.iter().any(|span| {
            span["parentSpanId"].as_str() == Some(agent_span_id)
                && span_attr(span, "gen_ai.span.kind").as_deref() != Some("AGENT")
        }));
    }

    #[test]
    fn native_replay_error_uses_exact_metadata_shell_without_transcript() {
        let lines = vec![
            json!({"timestamp":"2026-08-17T00:00:00Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn"}}),
            json!({"timestamp":"2026-08-17T00:00:00.100Z","type":"event_msg","payload":{"type":"user_message","message":"collab"}}),
            json!({"timestamp":"2026-08-17T00:00:01Z","type":"event_msg","payload":{"type":"item_completed","item":{"type":"CollabAgentToolCall","id":"spawn-error","tool":"spawn_agent","sender_thread_id":"parent","receiver_thread_ids":["child-error"],"receiver_agents":[{"thread_id":"child-error","agent_nickname":"Hume","agent_role":"debugger"}],"prompt":"diagnose","agents_states":{"child-error":"pending_init"}},"started_at_ms":1000,"completed_at_ms":1100}}),
            json!({"timestamp":"2026-08-17T00:00:02Z","type":"event_msg","payload":{"type":"item_completed","item":{"type":"CollabAgentToolCall","id":"wait-error","tool":"wait","sender_thread_id":"parent","receiver_thread_ids":["child-error"],"agents_states":{"child-error":{"error":"boom"}}},"started_at_ms":1500,"completed_at_ms":2000}}),
            json!({"timestamp":"2026-08-17T00:00:03Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn","last_agent_message":"failed"}}),
        ];
        let turns = build_turns(&lines);
        let discovery = replay_discovery_summary(&turns);
        assert_eq!(
            discovery.error_shell_ids,
            HashSet::from(["child-error".to_string()])
        );

        let otels =
            build_otel_exports_with_subagents(&turns, "parent", "tester", "test-version", &[]);
        let agent = all_spans(&otels[0])
            .into_iter()
            .find(|span| span["name"] == "agent Hume")
            .expect("error shell must emit AGENT");
        assert_eq!(agent["status"]["code"], 2);
        assert_eq!(
            span_attr(agent, "agentic.subagent.parent_tool_call_id").as_deref(),
            Some("spawn-error")
        );
    }

    #[test]
    fn native_replay_discovers_pending_and_exports_ready_sibling() {
        let lines = vec![
            json!({"timestamp":"2026-08-17T00:00:00Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn"}}),
            json!({"timestamp":"2026-08-17T00:00:00.100Z","type":"event_msg","payload":{"type":"user_message","message":"collab"}}),
            json!({"timestamp":"2026-08-17T00:00:00.200Z","type":"event_msg","payload":{"type":"item_completed","item":{"type":"CollabAgentToolCall","id":"spawn-a","tool":"spawn_agent","sender_thread_id":"parent","receiver_thread_ids":["child-a"],"agents_states":{"child-a":"pending_init"}},"started_at_ms":200,"completed_at_ms":300}}),
            json!({"timestamp":"2026-08-17T00:00:00.400Z","type":"event_msg","payload":{"type":"item_completed","item":{"type":"CollabAgentToolCall","id":"spawn-b","tool":"spawn_agent","sender_thread_id":"parent","receiver_thread_ids":["child-b"],"agents_states":{"child-b":"pending_init"}},"started_at_ms":400,"completed_at_ms":500}}),
            json!({"timestamp":"2026-08-17T00:00:01Z","type":"event_msg","payload":{"type":"item_completed","item":{"type":"CollabAgentToolCall","id":"wait-many","tool":"wait","sender_thread_id":"parent","receiver_thread_ids":["child-a","child-b"],"agents_states":{"child-a":{"completed":"done"},"child-b":{"timed_out":true}}},"started_at_ms":1000,"completed_at_ms":2000}}),
            json!({"timestamp":"2026-08-17T00:00:03Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn","last_agent_message":"done"}}),
        ];
        let turns = build_turns(&lines);
        let discovery = replay_discovery_summary(&turns);
        assert_eq!(
            discovery.expected_ids,
            HashSet::from(["child-a".to_string(), "child-b".to_string()])
        );
        assert!(discovery.turn_plans[0].decisions.iter().any(|decision| {
            decision.reason == "native_terminal_pending"
                && decision.disposition == SubagentDiscoveryDisposition::Deferred
        }));

        let otels = build_otel_exports_with_subagents(
            &turns,
            "parent",
            "tester",
            "test-version",
            &[make_child_session("child-a", "1970-01-01T00:00:00.500Z")],
        );
        assert!(all_spans(&otels[0])
            .iter()
            .any(|span| { span_attr(span, "gen_ai.span.kind").as_deref() == Some("AGENT") }));
    }

    #[test]
    fn native_replay_spawn_only_discovers_child_without_fabricating_completion() {
        let lines = vec![
            json!({"timestamp":"2026-08-17T00:00:00Z","type":"event_msg","payload":{"type":"task_started","turn_id":"turn"}}),
            json!({"timestamp":"2026-08-17T00:00:00.100Z","type":"event_msg","payload":{"type":"user_message","message":"collab"}}),
            json!({"timestamp":"2026-08-17T00:00:01Z","type":"event_msg","payload":{"type":"item_completed","item":{"type":"CollabAgentToolCall","id":"spawn-async","tool":"spawn_agent","sender_thread_id":"parent","receiver_thread_ids":["child-async"],"agents_states":{"child-async":"pending_init"}},"started_at_ms":1000,"completed_at_ms":1100}}),
            json!({"timestamp":"2026-08-17T00:00:02Z","type":"event_msg","payload":{"type":"task_complete","turn_id":"turn","last_agent_message":"started"}}),
        ];
        let turns = build_turns(&lines);
        let discovery = replay_discovery_summary(&turns);
        assert_eq!(
            discovery.expected_ids,
            HashSet::from(["child-async".to_string()])
        );
        assert_eq!(discovery.turn_plans[0].decisions.len(), 1);
        assert_eq!(
            discovery.turn_plans[0].decisions[0].reason,
            "native_terminal_pending"
        );
        let otels = build_otel_exports_with_subagents(
            &turns,
            "parent",
            "tester",
            "test-version",
            &[make_child_session(
                "child-async",
                "1970-01-01T00:00:01.050Z",
            )],
        );
        assert!(!all_spans(&otels[0])
            .iter()
            .any(|span| { span_attr(span, "gen_ai.span.kind").as_deref() == Some("AGENT") }));
    }

    #[test]
    fn test_subagent_replay_child_up_keeps_main_turns_in_separate_traces() {
        let main_turns = vec![
            make_turn(
                "turn-1",
                "2026-06-30T00:00:00.000Z",
                vec![
                    subagent_tool(
                        "spawn_agent",
                        "spawn-a",
                        "child-a",
                        "2026-06-30T00:00:01.000Z",
                        "2026-06-30T00:00:02.000Z",
                    ),
                    subagent_tool(
                        "wait_agent",
                        "wait-a",
                        "child-a",
                        "2026-06-30T00:00:03.000Z",
                        "2026-06-30T00:00:04.000Z",
                    ),
                ],
                vec![],
            ),
            make_turn(
                "turn-2",
                "2026-06-30T00:01:00.000Z",
                vec![
                    subagent_tool(
                        "spawn_agent",
                        "spawn-b",
                        "child-b",
                        "2026-06-30T00:01:01.000Z",
                        "2026-06-30T00:01:02.000Z",
                    ),
                    subagent_tool(
                        "wait_agent",
                        "wait-b",
                        "child-b",
                        "2026-06-30T00:01:03.000Z",
                        "2026-06-30T00:01:04.000Z",
                    ),
                ],
                vec![],
            ),
        ];
        let child_sessions = vec![
            make_child_session("child-a", "2026-06-30T00:00:02.500Z"),
            make_child_session("child-b", "2026-06-30T00:01:02.500Z"),
        ];
        let otels = build_otel_exports_with_subagents(
            &main_turns,
            "parent-session",
            "tester",
            "test-version",
            &child_sessions,
        );

        assert_eq!(otels.len(), 2);
        assert_eq!(root_span(&otels[0])["name"], "Turn 1");
        assert_eq!(root_span(&otels[1])["name"], "Turn 2");
        assert_ne!(
            root_span(&otels[0])["traceId"],
            root_span(&otels[1])["traceId"]
        );
        assert_eq!(trace_ids_in_body(&otels[0]).len(), 1);
        assert_eq!(trace_ids_in_body(&otels[1]).len(), 1);
        assert_eq!(count_spans_named(&otels[0], "agent SyncAgent"), 1);
        assert_eq!(count_spans_named(&otels[1], "agent SyncAgent"), 1);
    }

    #[test]
    fn test_subagent_replay_correlates_cross_turn_followup_to_spawn_trace() {
        let child_id = "child-reviewer";
        let mut spawn_turn = make_turn(
            "parent-spawn",
            "2026-07-16T09:46:30.000Z",
            vec![
                ToolCall {
                    call_id: "call-spawn".to_string(),
                    name: "spawn_agent".to_string(),
                    input: json!({"task_name":"reviewer","message":"review"}),
                    output: json!({"task_name":"/root/reviewer"}),
                    started_timestamp: "2026-07-16T09:46:31.000Z".to_string(),
                    ended_timestamp: "2026-07-16T09:46:31.200Z".to_string(),
                    assistant_index: 0,
                    success: Some(true),
                },
                ToolCall {
                    call_id: "wait-spawn".to_string(),
                    name: "wait_agent".to_string(),
                    input: json!({"timeout_ms":1000}),
                    output: json!({"timed_out":true}),
                    started_timestamp: "2026-07-16T09:46:32.000Z".to_string(),
                    ended_timestamp: "2026-07-16T09:46:33.000Z".to_string(),
                    assistant_index: 0,
                    success: Some(true),
                },
            ],
            vec![CollabAgentEvent::SubagentActivity {
                event_id: "call-spawn".to_string(),
                agent_thread_id: child_id.to_string(),
                agent_path: Some("/root/reviewer".to_string()),
                kind: "started".to_string(),
                timestamp: "2026-07-16T09:46:31.100Z".to_string(),
            }],
        );
        spawn_turn.completion_timestamp = "2026-07-16T09:58:36.000Z".to_string();

        let followup_turn = make_turn(
            "parent-followup",
            "2026-07-16T09:58:53.000Z",
            vec![
                ToolCall {
                    call_id: "call-followup".to_string(),
                    name: "followup_task".to_string(),
                    input: json!({"target":"/root/reviewer","message":"recheck"}),
                    output: json!({"delivered":true}),
                    started_timestamp: "2026-07-16T10:07:44.000Z".to_string(),
                    ended_timestamp: "2026-07-16T10:07:44.200Z".to_string(),
                    assistant_index: 0,
                    success: Some(true),
                },
                ToolCall {
                    call_id: "wait-followup".to_string(),
                    name: "wait_agent".to_string(),
                    input: json!({"timeout_ms":1000}),
                    output: json!({"timed_out":false}),
                    started_timestamp: "2026-07-16T10:07:45.000Z".to_string(),
                    ended_timestamp: "2026-07-16T10:07:46.000Z".to_string(),
                    assistant_index: 0,
                    success: Some(true),
                },
            ],
            vec![
                CollabAgentEvent::SubagentActivity {
                    event_id: "call-followup".to_string(),
                    agent_thread_id: child_id.to_string(),
                    agent_path: Some("/root/reviewer".to_string()),
                    kind: "interacted".to_string(),
                    timestamp: "2026-07-16T10:07:44.100Z".to_string(),
                },
                CollabAgentEvent::AgentMessage {
                    author: "/root/reviewer".to_string(),
                    recipient: "/root".to_string(),
                    message_type: Some("FINAL_ANSWER".to_string()),
                    content: "approved".to_string(),
                    timestamp: "2026-07-16T10:07:47.000Z".to_string(),
                },
            ],
        );
        let child_sessions = vec![ChildSession {
            thread_id: child_id.to_string(),
            turns: vec![
                make_turn("child-review-1", "2026-07-16T09:46:35.000Z", vec![], vec![]),
                make_turn("child-review-2", "2026-07-16T10:07:44.300Z", vec![], vec![]),
            ],
            nickname: String::new(),
            role: String::new(),
            agent_path: None,
        }];

        let main_turns = [spawn_turn, followup_turn];
        let expected_ids = collect_expected_child_thread_ids(&main_turns);
        assert_eq!(expected_ids, HashSet::from([child_id.to_string()]));
        let otels = build_otel_exports_with_subagents(
            &main_turns,
            "parent-session",
            "tester",
            "test-version",
            &child_sessions,
        );

        assert_eq!(count_spans_named(&otels[0], "agent SyncAgent"), 1);
        assert_eq!(count_spans_named(&otels[1], "agent SyncAgent"), 1);
        for otel in &otels {
            assert_eq!(
                otel.pointer("/resourceSpans/0/scopeSpans/0/spans")
                    .and_then(|value| value.as_array())
                    .unwrap()
                    .iter()
                    .filter(|span| {
                        span.get("name")
                            .and_then(|value| value.as_str())
                            .is_some_and(|name| name.starts_with("chat "))
                    })
                    .count(),
                2,
                "each activation trace should contain its parent generation plus one child turn"
            );
        }
        let followup_agent = otels[1]
            .pointer("/resourceSpans/0/scopeSpans/0/spans")
            .and_then(|value| value.as_array())
            .unwrap()
            .iter()
            .find(|span| {
                span.get("name").and_then(|value| value.as_str()) == Some("agent SyncAgent")
            })
            .unwrap();
        assert!(followup_agent["attributes"]
            .as_array()
            .unwrap()
            .iter()
            .any(|attr| {
                attr["key"] == "agentic.subagent.parent_tool_call_id"
                    && attr["value"]["stringValue"] == "call-followup"
            }));
    }

    #[test]
    fn test_subagent_replay_collab_child_is_inlined_into_spawning_turn_only() {
        let child_id = "thread-child-1";
        let main_turns = vec![
            make_turn(
                "turn-1",
                "2026-06-30T00:00:00.000Z",
                vec![
                    ToolCall {
                        call_id: "call-spawn-1".to_string(),
                        name: "spawn_agent".to_string(),
                        input: json!({"message":"inspect"}),
                        output: json!({"new_thread_id":child_id}),
                        started_timestamp: "2026-06-30T00:00:01.000Z".to_string(),
                        ended_timestamp: "2026-06-30T00:00:02.000Z".to_string(),
                        assistant_index: 0,
                        success: Some(true),
                    },
                    ToolCall {
                        call_id: "call-wait-1".to_string(),
                        name: "wait_agent".to_string(),
                        input: json!({"targets":[child_id]}),
                        output: json!({"timed_out":false}),
                        started_timestamp: "2026-06-30T00:00:02.000Z".to_string(),
                        ended_timestamp: "2026-06-30T00:00:04.000Z".to_string(),
                        assistant_index: 0,
                        success: Some(true),
                    },
                ],
                vec![CollabAgentEvent::SpawnEnd {
                    call_id: "call-spawn-1".to_string(),
                    sender_thread_id: "parent-session".to_string(),
                    new_thread_id: Some(child_id.to_string()),
                    nickname: Some("worker".to_string()),
                    role: Some("coder".to_string()),
                    prompt: "inspect".to_string(),
                    model: "gpt-5.5".to_string(),
                    status: "completed".to_string(),
                }],
            ),
            make_turn("turn-2", "2026-06-30T00:01:00.000Z", vec![], vec![]),
        ];
        let child_sessions = vec![make_child_session(child_id, "2026-06-30T00:00:03.000Z")];
        let expected_ids = collect_expected_child_thread_ids(&main_turns);
        assert_eq!(expected_ids, HashSet::from([child_id.to_string()]));

        let otels = build_otel_exports_with_subagents(
            &main_turns,
            "parent-session",
            "tester",
            "test-version",
            &child_sessions,
        );

        assert_eq!(otels.len(), 2);
        assert_eq!(root_span(&otels[0])["name"], "Turn 1");
        assert_eq!(root_span(&otels[1])["name"], "Turn 2");
        assert_ne!(
            root_span(&otels[0])["traceId"],
            root_span(&otels[1])["traceId"]
        );
        assert_eq!(trace_ids_in_body(&otels[0]).len(), 1);
        assert_eq!(trace_ids_in_body(&otels[1]).len(), 1);
        assert_eq!(count_spans_named(&otels[0], "agent SyncAgent"), 1);
        assert_eq!(count_spans_named(&otels[1], "agent SyncAgent"), 0);
    }

    #[test]
    fn merged_agent_span_applies_budget_after_copying_child_messages() {
        let child_id = "thread-large-child";
        let oversized_prompt =
            "x".repeat(cdxhooks::common::payload_budget::MAX_SERIALIZED_SPAN_BYTES + 4096);
        let main_turns = vec![make_turn(
            "turn-large",
            "2026-06-30T00:00:00.000Z",
            vec![
                ToolCall {
                    call_id: "call-large-spawn".to_string(),
                    name: "spawn_agent".to_string(),
                    input: json!({"message": oversized_prompt}),
                    output: json!({"new_thread_id": child_id}),
                    started_timestamp: "2026-06-30T00:00:01.000Z".to_string(),
                    ended_timestamp: "2026-06-30T00:00:02.000Z".to_string(),
                    assistant_index: 0,
                    success: Some(true),
                },
                ToolCall {
                    call_id: "call-large-wait".to_string(),
                    name: "wait_agent".to_string(),
                    input: json!({"targets": [child_id]}),
                    output: json!({"timed_out": false}),
                    started_timestamp: "2026-06-30T00:00:02.000Z".to_string(),
                    ended_timestamp: "2026-06-30T00:00:04.000Z".to_string(),
                    assistant_index: 0,
                    success: Some(true),
                },
            ],
            vec![CollabAgentEvent::SpawnEnd {
                call_id: "call-large-spawn".to_string(),
                sender_thread_id: "parent-session".to_string(),
                new_thread_id: Some(child_id.to_string()),
                nickname: Some("worker".to_string()),
                role: Some("coder".to_string()),
                prompt: "x"
                    .repeat(cdxhooks::common::payload_budget::MAX_SERIALIZED_SPAN_BYTES + 4096),
                model: "gpt-5.5".to_string(),
                status: "completed".to_string(),
            }],
        )];
        let mut child_session = make_child_session(child_id, "2026-06-30T00:00:03.000Z");
        child_session.turns[0].user_text =
            "y".repeat(cdxhooks::common::payload_budget::MAX_SERIALIZED_SPAN_BYTES + 4096);
        let child_sessions = vec![child_session];

        let otels = build_otel_exports_with_subagents(
            &main_turns,
            "parent-session",
            "tester",
            "test-version",
            &child_sessions,
        );
        let agent = otels[0]
            .pointer("/resourceSpans/0/scopeSpans/0/spans")
            .and_then(serde_json::Value::as_array)
            .unwrap()
            .iter()
            .find(|span| {
                span["attributes"].as_array().is_some_and(|attributes| {
                    attributes.iter().any(|attribute| {
                        attribute["key"] == "gen_ai.span.kind"
                            && attribute["value"]["stringValue"] == "AGENT"
                    })
                })
            })
            .unwrap();

        assert!(
            serde_json::to_vec(agent).unwrap().len()
                <= cdxhooks::common::payload_budget::MAX_SERIALIZED_SPAN_BYTES
        );
        assert!(agent["attributes"]
            .as_array()
            .unwrap()
            .iter()
            .any(|attribute| {
                attribute["key"] == "gen_ai.tool.call.arguments"
                    && attribute["value"]["stringValue"]
                        .as_str()
                        .is_some_and(|value| value.contains("TRUNCATED_TO_SPAN_BUDGET"))
            }));

        let agent_span_id = agent["spanId"].as_str().unwrap();
        let child_generation = otels[0]
            .pointer("/resourceSpans/0/scopeSpans/0/spans")
            .and_then(serde_json::Value::as_array)
            .unwrap()
            .iter()
            .find(|span| {
                span["parentSpanId"] == agent_span_id
                    && span["attributes"].as_array().is_some_and(|attributes| {
                        attributes.iter().any(|attribute| {
                            attribute["key"] == "gen_ai.span.kind"
                                && attribute["value"]["stringValue"] == "LLM"
                        })
                    })
            })
            .unwrap();
        assert!(
            serde_json::to_vec(child_generation).unwrap().len()
                <= cdxhooks::common::payload_budget::MAX_SERIALIZED_SPAN_BYTES
        );
        for (key, value) in [
            ("agentic.subagent", "true"),
            ("agentic.subagent.id", child_id),
        ] {
            assert!(child_generation["attributes"]
                .as_array()
                .unwrap()
                .iter()
                .any(|attribute| {
                    attribute["key"] == key && attribute["value"]["stringValue"] == value
                }));
        }
    }
}
