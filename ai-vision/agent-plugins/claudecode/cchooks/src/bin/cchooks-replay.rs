//! Replay tool for visualizing JSONL transcript files as terminal trace trees.
//!
//! Usage:
//!   cchooks-replay <FILE> [OPTIONS]
//!
//! Options:
//!   --no-color    Disable ANSI color output
//!   --verbose     Show full text content (no truncation)
//!   --summary     Show summary statistics only
//!   --turn <N>    Show only the Nth turn (1-based)

use std::fs;
use std::io::{self, BufWriter, Write};
use std::process;

use cchooks::common::otel::build_otel_replay_export_requests_for_turn;
use cchooks::common::transcript::{
    build_turns, resolve_transcript_session_id, AssistantMessage, TokenUsage, ToolUse, Turn,
};
use cchooks::hooks::timing::{companion_timing_path, load_timing_data_from_path, TimingData};

// ─── ANSI color helpers ───

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

// ─── CLI argument parsing ───

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
                // Next arg is optional output path (if it doesn't start with --)
                let next_index = index + 2; // +1 for args[1..] offset, +1 for next
                if next_index < args.len() && !args[next_index].starts_with("--") {
                    otel_output = Some(Some(args[next_index].clone()));
                    skip_next = true;
                } else {
                    otel_output = Some(None); // stdout
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
    })
}

fn print_usage() {
    eprintln!("  --collection-mode full|privacy  Explicit replay mode (default: full; shell mode ignored)");
    eprintln!("Usage: cchooks-replay <FILE> [OPTIONS]");
    eprintln!();
    eprintln!("Options:");
    eprintln!("  --no-color          Disable ANSI color output");
    eprintln!("  --verbose           Show full text content (no truncation)");
    eprintln!("  --summary           Show summary statistics only");
    eprintln!("  --turn <N>          Show only the Nth turn (1-based)");
    eprintln!("  --otel [OUTPUT]     Convert to OTel JSONL (default: stdout)");
    eprintln!("  --session-id <ID>   Override session ID for OTel output");
    eprintln!("  --username <NAME>   Override username for OTel output");
}

// ─── Text truncation ───

const MAX_DISPLAY_LINES: usize = 3;

fn truncate_line(line: &str, max_len: usize) -> String {
    let char_count = line.chars().count();
    if char_count <= max_len {
        line.to_string()
    } else {
        let truncated: String = line.chars().take(max_len).collect();
        format!("{}…", truncated)
    }
}

/// Truncate text: each line capped at max_len chars, and at most MAX_DISPLAY_LINES lines
/// (unless verbose). Returns a Vec of display lines.
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
        result.push(format!("… ({} more lines)", total - MAX_DISPLAY_LINES));
    }

    result
}

/// Format multi-line text with tree-aligned indentation.
/// first_prefix: the prefix for the first line (e.g. "│   ├── ")
/// cont_prefix:  the prefix for continuation lines, aligned to first line's text start
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

// ─── Token usage formatting ───

fn format_usage(usage: &TokenUsage) -> String {
    let mut parts = Vec::new();
    if let Some(input) = usage.input {
        parts.push(format!("in:{}", input));
    }
    if let Some(output) = usage.output {
        parts.push(format!("out:{}", output));
    }
    if let Some(cache_create) = usage.cache_creation_input_tokens {
        if cache_create > 0 {
            parts.push(format!("cache_w:{}", cache_create));
        }
    }
    if let Some(cache_read) = usage.cache_read_input_tokens {
        if cache_read > 0 {
            parts.push(format!("cache_r:{}", cache_read));
        }
    }
    if parts.is_empty() {
        return String::new();
    }
    format!("[{}]", parts.join(" "))
}

// ─── Tree rendering ───

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

        // Turn header
        let turn_label = format!("Turn {} ", turn_index + 1);
        let timestamp_label = format!("[{}]", turn.user_timestamp);
        let skill_label = turn
            .skill_name
            .as_ref()
            .map(|s| format!(" (skill: {})", s))
            .unwrap_or_default();

        output.push_str(&format!(
            "{}{}{}{}",
            turn_connector,
            bold_color(GREEN, &turn_label, use_color),
            color(GRAY, &timestamp_label, use_color),
            color(CYAN, &skill_label, use_color),
        ));
        output.push('\n');

        // User message
        let user_label = color(GREEN, "👤 User:", use_color);
        let user_lines = truncate_text_lines(&turn.user_text, max_text_len, verbose);
        // "│   ├── 👤 User: " — continuation lines align to text after "👤 User: "
        let user_first = format!("{}├── {} ", turn_prefix, user_label);
        let user_cont = format!("{}│        ", turn_prefix);
        output.push_str(&format_multiline(&user_lines, &user_first, &user_cont));

        // Flatten assistants and their tool_uses into a single child list
        // Each item is either an assistant message or a tool use, all at the same level
        enum TurnChild<'a> {
            Assistant(&'a AssistantMessage),
            Tool(&'a ToolUse),
            ToolResults(usize),
        }

        let mut children: Vec<TurnChild> = Vec::new();
        for assistant in &turn.assistants {
            children.push(TurnChild::Assistant(assistant));
            for tool_use in &assistant.tool_uses {
                children.push(TurnChild::Tool(tool_use));
            }
        }
        if !turn.tool_results.is_empty() {
            let tool_result_count: usize = turn.tool_results.iter().map(|tr| tr.items.len()).sum();
            children.push(TurnChild::ToolResults(tool_result_count));
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
                        turn_prefix,
                        connector,
                        cont,
                        use_color,
                        max_text_len,
                        verbose,
                    );
                }
                TurnChild::Tool(tool_use) => {
                    output.push_str(&format!(
                        "{}{}{} {}",
                        turn_prefix,
                        connector,
                        color(YELLOW, "🔧", use_color),
                        color(YELLOW, &tool_use.name, use_color),
                    ));
                    output.push('\n');
                }
                TurnChild::ToolResults(count) => {
                    output.push_str(&format!(
                        "{}{}{} {} tool result(s)",
                        turn_prefix,
                        connector,
                        color(YELLOW, "🔧", use_color),
                        count,
                    ));
                    output.push('\n');
                }
            }
        }
    }

    output
}

#[allow(clippy::too_many_arguments)]
fn render_assistant(
    output: &mut String,
    assistant: &AssistantMessage,
    turn_prefix: &str,
    connector: &str,
    cont_prefix: &str,
    use_color: bool,
    max_text_len: usize,
    verbose: bool,
) {
    // Assistant header with model and usage
    let model_label = if assistant.model.is_empty() {
        String::new()
    } else {
        format!(" ({})", assistant.model)
    };
    let usage_label = if assistant.usage.has_any() {
        format!(" {}", format_usage(&assistant.usage))
    } else {
        String::new()
    };

    // Build the header: "turn_prefix + connector + 🤖 Assistant (model) [usage]"
    let header = format!(
        "{}{}{}{}{}",
        turn_prefix,
        connector,
        color(BLUE, "🤖 Assistant", use_color),
        color(GRAY, &model_label, use_color),
        color(GRAY, &usage_label, use_color),
    );

    if assistant.text.is_empty() {
        output.push_str(&header);
        output.push('\n');
    } else {
        // Inline text on the same line as header, multi-line continuation aligned
        let text_lines = truncate_text_lines(&assistant.text, max_text_len, verbose);
        let first = format!("{}: ", header);
        let text_indent = format!("{}{}  ", turn_prefix, cont_prefix);
        output.push_str(&format_multiline(&text_lines, &first, &text_indent));
    }
    // Tool uses are rendered by the caller at the same level as this assistant
}

// ─── Summary rendering ───

fn render_summary(turns: &[Turn], use_color: bool) -> String {
    let mut output = String::new();

    let total_turns = turns.len();
    let total_assistants: usize = turns.iter().map(|t| t.assistants.len()).sum();
    let total_tool_uses: usize = turns
        .iter()
        .flat_map(|t| &t.assistants)
        .map(|a| a.tool_uses.len())
        .sum();
    let total_tool_results: usize = turns
        .iter()
        .flat_map(|t| &t.tool_results)
        .map(|tr| tr.items.len())
        .sum();

    // Token totals
    let mut total_input_tokens: i64 = 0;
    let mut total_output_tokens: i64 = 0;
    let mut total_cache_write: i64 = 0;
    let mut total_cache_read: i64 = 0;

    // Model set and tool name counts
    let mut models: Vec<String> = Vec::new();
    let mut tool_counts: Vec<(String, usize)> = Vec::new();

    for turn in turns {
        for assistant in &turn.assistants {
            if let Some(input) = assistant.usage.input {
                total_input_tokens += input;
            }
            if let Some(out) = assistant.usage.output {
                total_output_tokens += out;
            }
            if let Some(cw) = assistant.usage.cache_creation_input_tokens {
                total_cache_write += cw;
            }
            if let Some(cr) = assistant.usage.cache_read_input_tokens {
                total_cache_read += cr;
            }

            if !assistant.model.is_empty() && !models.contains(&assistant.model) {
                models.push(assistant.model.clone());
            }

            for tool_use in &assistant.tool_uses {
                if let Some(entry) = tool_counts
                    .iter_mut()
                    .find(|(name, _)| name == &tool_use.name)
                {
                    entry.1 += 1;
                } else {
                    tool_counts.push((tool_use.name.clone(), 1));
                }
            }
        }
    }

    // Time span
    let first_timestamp = turns
        .first()
        .map(|t| t.user_timestamp.as_str())
        .unwrap_or("N/A");
    let last_timestamp = turns
        .last()
        .and_then(|t| {
            t.assistants
                .last()
                .map(|a| {
                    if !a.end_timestamp.is_empty() {
                        a.end_timestamp.as_str()
                    } else {
                        a.timestamp.as_str()
                    }
                })
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
        total_tool_uses
    ));
    output.push_str(&format!(
        "  {} {}\n",
        color(BOLD, "Tool results:", use_color),
        total_tool_results
    ));

    // Token usage (input = raw + cache_write + cache_read per semconv [13])
    let total_input_all = total_input_tokens + total_cache_write + total_cache_read;
    output.push_str(&format!(
        "  {} input={} output={} (raw={} cache_write={} cache_read={})\n",
        color(BOLD, "Tokens:", use_color),
        total_input_all,
        total_output_tokens,
        total_input_tokens,
        total_cache_write,
        total_cache_read,
    ));

    // Models
    output.push_str(&format!(
        "  {} {}\n",
        color(BOLD, "Models:", use_color),
        if models.is_empty() {
            "N/A".to_string()
        } else {
            models.join(", ")
        }
    ));

    // Tool breakdown
    if !tool_counts.is_empty() {
        output.push_str(&format!(
            "  {}\n",
            color(BOLD, "Tool breakdown:", use_color)
        ));
        tool_counts.sort_by_key(|entry| std::cmp::Reverse(entry.1));
        for (name, count) in &tool_counts {
            output.push_str(&format!(
                "    {} {} ({})\n",
                color(YELLOW, "🔧", use_color),
                name,
                count
            ));
        }
    }

    // Time span
    output.push_str(&format!(
        "  {} {} → {}\n",
        color(BOLD, "Time span:", use_color),
        first_timestamp,
        last_timestamp,
    ));

    output
}

// ─── OTel JSONL output ───

fn replay_otel(turns: &[Turn], cli: &CliArgs, parsed_lines: &[serde_json::Value]) {
    let session_id_owned = cli
        .session_id_override
        .clone()
        .unwrap_or_else(|| resolve_transcript_session_id(parsed_lines));
    let session_id = session_id_owned.as_str();

    let username_owned = cli
        .username_override
        .clone()
        .or_else(|| std::env::var("USER").ok())
        .unwrap_or_else(|| "replay".to_string());
    let username = username_owned.as_str();

    let version = env!("CARGO_PKG_VERSION");

    let mut writer: Box<dyn Write> = match &cli.otel_output {
        Some(Some(path)) => {
            let file = fs::File::create(path).unwrap_or_else(|e| {
                eprintln!("Error creating '{}': {}", path, e);
                process::exit(1);
            });
            Box::new(BufWriter::new(file))
        }
        _ => Box::new(BufWriter::new(io::stdout().lock())),
    };

    // Load companion timing file if present
    let timing_path = companion_timing_path(&cli.file_path);
    let timing = if timing_path.exists() {
        let t = load_timing_data_from_path(&timing_path);
        eprintln!(
            "Loaded {} timing entries from '{}'",
            t.len(),
            timing_path.display()
        );
        t
    } else {
        TimingData::new()
    };

    for (i, turn) in turns.iter().enumerate() {
        let requests = build_otel_replay_export_requests_for_turn(
            turn,
            i + 1,
            session_id,
            username,
            version,
            &cli.file_path,
            &timing,
        );
        for request in requests {
            let line = serde_json::to_string(&request).unwrap();
            writeln!(writer, "{}", line).unwrap_or_else(|e| {
                eprintln!("Write error: {}", e);
                process::exit(1);
            });
        }
    }

    eprintln!("Replayed {} turns → OTel JSONL", turns.len());
}

// ─── Main ───

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
    std::env::set_var("CCHOOKS_COLLECTION_MODE", &cli.collection_mode);
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

    // Note: skill keywords config no longer used - skills extracted from tool_use input
    let turns = build_turns(&parsed_lines, None);

    if turns.is_empty() {
        eprintln!("No turns found in '{}'", cli.file_path);
        process::exit(1);
    }

    // OTel JSONL mode
    if cli.otel_output.is_some() {
        replay_otel(&turns, &cli, &parsed_lines);
        return;
    }

    let use_color = !cli.no_color;

    // Apply turn filter
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
        let owned_turns: Vec<Turn> = display_turns.into_iter().cloned().collect();
        print!("{}", render_summary(&owned_turns, use_color));
    } else {
        let owned_turns: Vec<Turn> = display_turns.into_iter().cloned().collect();
        print!(
            "{}",
            render_trace_tree(&owned_turns, use_color, cli.verbose)
        );
    }
}

// ─── Tests ───

#[cfg(test)]
mod tests {
    use super::*;
    use cchooks::common::transcript::{
        AssistantMessage, TokenUsage, ToolResultMessage, ToolUse, Turn,
    };

    fn make_usage(input: i64, output: i64) -> TokenUsage {
        TokenUsage {
            input: Some(input),
            output: Some(output),
            cache_creation_input_tokens: None,
            cache_read_input_tokens: None,
        }
    }

    fn make_simple_turn(user_text: &str, assistant_text: &str, model: &str) -> Turn {
        Turn {
            chat_id: None,
            trigger: Default::default(),
            user_timestamp: "2026-03-27T10:00:00.000Z".to_string(),
            user_text: user_text.to_string(),
            user_content: serde_json::Value::Null,
            assistants: vec![AssistantMessage {
                timestamp: "2026-03-27T10:00:01.000Z".to_string(),
                end_timestamp: "2026-03-27T10:00:02.000Z".to_string(),
                text: assistant_text.to_string(),
                media_content: serde_json::Value::Null,
                model: model.to_string(),
                usage: make_usage(10, 8),
                tool_uses: vec![],
                message_id: "msg_001".to_string(),
                stop_reason: "end_turn".to_string(),
                thinking_blocks: vec![],
                is_synthetic: false,
            }],
            tool_results: vec![],
            skill_name: None,
            api_errors: vec![],
            terminal_llm_error: None,
            compactions: vec![],
            cancelled: false,
            task_notifications: Vec::new(),
        }
    }

    fn make_tool_turn() -> Turn {
        Turn {
            chat_id: None,
            trigger: Default::default(),
            user_timestamp: "2026-03-27T10:01:00.000Z".to_string(),
            user_text: "Read config.json".to_string(),
            user_content: serde_json::Value::Null,
            assistants: vec![AssistantMessage {
                timestamp: "2026-03-27T10:01:01.000Z".to_string(),
                end_timestamp: "2026-03-27T10:01:02.000Z".to_string(),
                text: "Let me read that.".to_string(),
                media_content: serde_json::Value::Null,
                model: "claude-sonnet-4-20250514".to_string(),
                usage: make_usage(15, 20),
                tool_uses: vec![
                    ToolUse {
                        name: "Read".to_string(),
                        id: "toolu_001".to_string(),
                        input: serde_json::json!({}),
                    },
                    ToolUse {
                        name: "Write".to_string(),
                        id: "toolu_002".to_string(),
                        input: serde_json::json!({}),
                    },
                ],
                message_id: "msg_002".to_string(),
                stop_reason: "tool_use".to_string(),
                thinking_blocks: vec![],
                is_synthetic: false,
            }],
            tool_results: vec![ToolResultMessage {
                timestamp: "2026-03-27T10:01:02.000Z".to_string(),
                items: vec![],
            }],
            skill_name: None,
            api_errors: vec![],
            terminal_llm_error: None,
            compactions: vec![],
            cancelled: false,
            task_notifications: Vec::new(),
        }
    }

    // ─── parse_args tests ───

    #[test]
    fn test_parse_args_minimal() {
        let args = vec!["cchooks-replay".to_string(), "test.jsonl".to_string()];
        let cli = parse_args(&args).unwrap();
        assert_eq!(cli.file_path, "test.jsonl");
        assert!(!cli.no_color);
        assert!(!cli.verbose);
        assert!(!cli.summary);
        assert!(cli.turn_filter.is_none());
    }

    #[test]
    fn test_parse_args_all_flags() {
        let args = vec![
            "cchooks-replay".to_string(),
            "test.jsonl".to_string(),
            "--no-color".to_string(),
            "--verbose".to_string(),
            "--summary".to_string(),
        ];
        let cli = parse_args(&args).unwrap();
        assert_eq!(cli.file_path, "test.jsonl");
        assert!(cli.no_color);
        assert!(cli.verbose);
        assert!(cli.summary);
    }

    #[test]
    fn test_parse_args_turn_filter() {
        let args = vec![
            "cchooks-replay".to_string(),
            "test.jsonl".to_string(),
            "--turn".to_string(),
            "3".to_string(),
        ];
        let cli = parse_args(&args).unwrap();
        assert_eq!(cli.turn_filter, Some(3));
    }

    #[test]
    fn test_parse_args_missing_file() {
        let args = vec!["cchooks-replay".to_string()];
        assert!(parse_args(&args).is_err());
    }

    #[test]
    fn test_parse_args_unknown_option() {
        let args = vec![
            "cchooks-replay".to_string(),
            "test.jsonl".to_string(),
            "--unknown".to_string(),
        ];
        assert!(parse_args(&args).is_err());
    }

    #[test]
    fn test_parse_args_otel_stdout() {
        let args = vec![
            "cchooks-replay".to_string(),
            "test.jsonl".to_string(),
            "--otel".to_string(),
        ];
        let cli = parse_args(&args).unwrap();
        assert_eq!(cli.otel_output, Some(None));
    }

    #[test]
    fn test_parse_args_otel_file() {
        let args = vec![
            "cchooks-replay".to_string(),
            "test.jsonl".to_string(),
            "--otel".to_string(),
            "/tmp/out.jsonl".to_string(),
        ];
        let cli = parse_args(&args).unwrap();
        assert_eq!(cli.otel_output, Some(Some("/tmp/out.jsonl".to_string())));
    }

    #[test]
    fn test_parse_args_session_id_and_username() {
        let args = vec![
            "cchooks-replay".to_string(),
            "test.jsonl".to_string(),
            "--otel".to_string(),
            "--session-id".to_string(),
            "my-session".to_string(),
            "--username".to_string(),
            "alice".to_string(),
        ];
        let cli = parse_args(&args).unwrap();
        assert_eq!(cli.otel_output, Some(None));
        assert_eq!(cli.session_id_override, Some("my-session".to_string()));
        assert_eq!(cli.username_override, Some("alice".to_string()));
    }

    // ─── truncate_line tests ───
    #[test]
    fn test_truncate_line_short() {
        assert_eq!(truncate_line("hello", 80), "hello");
    }

    #[test]
    fn test_truncate_line_long() {
        let long_text = "a".repeat(100);
        let result = truncate_line(&long_text, 80);
        assert_eq!(result.chars().count(), 81); // 80 chars + "…"
        assert!(result.ends_with('…'));
    }

    #[test]
    fn test_truncate_line_unicode() {
        let chinese = "你好世界这是一段很长的中文文本需要被截断处理";
        let result = truncate_line(chinese, 10);
        assert_eq!(result.chars().count(), 11); // 10 chars + "…"
        assert!(result.ends_with('…'));
    }

    // ─── truncate_text_lines tests ───

    #[test]
    fn test_truncate_text_lines_single() {
        let lines = truncate_text_lines("hello world", 80, false);
        assert_eq!(lines, vec!["hello world"]);
    }

    #[test]
    fn test_truncate_text_lines_multiline_within_limit() {
        let text = "line 1\nline 2\nline 3";
        let lines = truncate_text_lines(text, 80, false);
        assert_eq!(lines, vec!["line 1", "line 2", "line 3"]);
    }

    #[test]
    fn test_truncate_text_lines_multiline_truncated() {
        let text = "line 1\nline 2\nline 3\nline 4\nline 5";
        let lines = truncate_text_lines(text, 80, false);
        assert_eq!(lines.len(), 4); // 3 lines + "… (2 more lines)"
        assert_eq!(lines[3], "… (2 more lines)");
    }

    #[test]
    fn test_truncate_text_lines_verbose_no_truncation() {
        let text = "line 1\nline 2\nline 3\nline 4\nline 5";
        let lines = truncate_text_lines(text, 80, true);
        assert_eq!(lines.len(), 5);
    }

    #[test]
    fn test_truncate_text_lines_empty() {
        let lines = truncate_text_lines("", 80, false);
        assert_eq!(lines, vec!["(empty)"]);
    }

    // ─── format_usage tests ───

    #[test]
    fn test_format_usage_basic() {
        let usage = make_usage(100, 50);
        assert_eq!(format_usage(&usage), "[in:100 out:50]");
    }

    #[test]
    fn test_format_usage_with_cache() {
        let usage = TokenUsage {
            input: Some(100),
            output: Some(50),
            cache_creation_input_tokens: Some(200),
            cache_read_input_tokens: Some(300),
        };
        assert_eq!(
            format_usage(&usage),
            "[in:100 out:50 cache_w:200 cache_r:300]"
        );
    }

    #[test]
    fn test_format_usage_empty() {
        let usage = TokenUsage {
            input: None,
            output: None,
            cache_creation_input_tokens: None,
            cache_read_input_tokens: None,
        };
        assert_eq!(format_usage(&usage), "");
    }

    // ─── render_trace_tree tests ───

    #[test]
    fn test_render_tree_simple() {
        let turns = vec![make_simple_turn(
            "Hello",
            "Hi there!",
            "claude-sonnet-4-20250514",
        )];
        let output = render_trace_tree(&turns, false, false);
        assert!(output.contains("Turn 1"));
        assert!(output.contains("User:"));
        assert!(output.contains("Hello"));
        assert!(output.contains("Assistant"));
        assert!(output.contains("Hi there!"));
        assert!(output.contains("claude-sonnet-4-20250514"));
    }

    #[test]
    fn test_render_tree_with_tools() {
        let turns = vec![make_tool_turn()];
        let output = render_trace_tree(&turns, false, false);
        assert!(output.contains("Read"));
        assert!(output.contains("Write"));
        assert!(output.contains("tool result"));
    }

    #[test]
    fn test_render_tree_no_color() {
        let turns = vec![make_simple_turn("Hello", "Hi", "model")];
        let output = render_trace_tree(&turns, false, false);
        assert!(!output.contains("\x1b["));
    }

    #[test]
    fn test_render_tree_with_color() {
        let turns = vec![make_simple_turn("Hello", "Hi", "model")];
        let output = render_trace_tree(&turns, true, false);
        assert!(output.contains("\x1b["));
    }

    #[test]
    fn test_render_tree_with_skill() {
        let mut turn = make_simple_turn("Hello", "Hi", "model");
        turn.skill_name = Some("my-skill".to_string());
        let output = render_trace_tree(&[turn], false, false);
        assert!(output.contains("skill: my-skill"));
    }

    // ─── render_summary tests ───

    #[test]
    fn test_render_summary_basic() {
        let turns = vec![
            make_simple_turn("Hello", "Hi", "claude-sonnet-4-20250514"),
            make_tool_turn(),
        ];
        let output = render_summary(&turns, false);
        assert!(output.contains("Turns:"));
        assert!(output.contains("2"));
        assert!(output.contains("Assistant messages:"));
        assert!(output.contains("Tool calls:"));
        assert!(output.contains("Tokens:"));
        assert!(output.contains("Models:"));
        assert!(output.contains("claude-sonnet-4-20250514"));
    }

    #[test]
    fn test_render_summary_tool_breakdown() {
        let turns = vec![make_tool_turn()];
        let output = render_summary(&turns, false);
        assert!(output.contains("Tool breakdown:"));
        assert!(output.contains("Read"));
        assert!(output.contains("Write"));
    }

    #[test]
    fn test_render_summary_time_span() {
        let turns = vec![make_simple_turn("Hello", "Hi", "model")];
        let output = render_summary(&turns, false);
        assert!(output.contains("Time span:"));
        assert!(output.contains("2026-03-27T10:00:00.000Z"));
    }
}
