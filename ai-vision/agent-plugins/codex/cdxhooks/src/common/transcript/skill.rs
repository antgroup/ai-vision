//! skill ownership module.

use super::{ToolCall, Turn};
use super::Value;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SkillInvocationSource {
    CodexTextElement,
    OmxUserPromptSubmit,
    LegacyMarkdown,
    CommandPathFallback,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SkillInvocation {
    pub name: String,
    pub raw_placeholder: String,
    pub source: SkillInvocationSource,
    pub timestamp: String,
    pub byte_start: Option<u64>,
    pub byte_end: Option<u64>,
    pub resolved_workflow: Option<String>,
    pub definition_path: Option<String>,
    pub original_text_element_index: Option<usize>,
    pub ordinal: usize,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LoadedSkillDefinition {
    pub name: String,
    pub path: Option<String>,
    pub timestamp: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct SkillPathReference {
    name: String,
    path: String,
}

pub(super) fn is_valid_skill_name(name: &str) -> bool {
    !name.is_empty()
        && name != "."
        && name != ".."
        && name
            .chars()
            .all(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '-' | '_' | '.'))
}

pub(super) fn command_path_start(command: &str, marker_pos: usize) -> usize {
    command[..marker_pos]
        .char_indices()
        .rev()
        .find_map(|(index, ch)| {
            (!ch.is_ascii_alphanumeric()
                && !matches!(ch, '/' | '.' | '_' | '-' | '~' | '$' | '{' | '}'))
            .then_some(index + ch.len_utf8())
        })
        .unwrap_or(0)
}

pub(super) fn is_commented_path(command: &str, marker_pos: usize) -> bool {
    command[..marker_pos]
        .rsplit(['\n', ';'])
        .next()
        .is_some_and(|prefix| prefix.trim_start().starts_with('#'))
}

pub(super) fn extract_skill_reference_from_command_text(
    command: &str,
) -> Option<SkillPathReference> {
    const MARKERS: &[&str] = &[".codex/skills/", ".agents/skills/"];
    let mut found = None;

    for marker in MARKERS {
        let mut search_from = 0;
        while let Some(relative_pos) = command[search_from..].find(marker) {
            let marker_pos = search_from + relative_pos;
            let after_marker_pos = marker_pos + marker.len();
            let after_marker = &command[after_marker_pos..];
            let candidate_path = after_marker
                .strip_prefix(".system/")
                .unwrap_or(after_marker);
            let Some(name_end) = candidate_path.find('/') else {
                search_from = after_marker_pos;
                continue;
            };
            let name = &candidate_path[..name_end];
            let suffix = &candidate_path[name_end..];
            if !is_valid_skill_name(name) || !suffix.starts_with("/SKILL.md") {
                search_from = after_marker_pos;
                continue;
            }
            let reference_end = after_marker_pos
                + after_marker.len().saturating_sub(candidate_path.len())
                + name_end
                + "/SKILL.md".len();
            let has_valid_boundary = command[reference_end..]
                .chars()
                .next()
                .is_none_or(|ch| !ch.is_ascii_alphanumeric() && !matches!(ch, '.' | '/' | '_'));
            if !has_valid_boundary || is_commented_path(command, marker_pos) {
                search_from = after_marker_pos;
                continue;
            }
            let path_start = command_path_start(command, marker_pos);
            let reference = SkillPathReference {
                name: name.to_string(),
                path: command[path_start..reference_end].to_string(),
            };
            if found
                .as_ref()
                .is_some_and(|existing| existing != &reference)
            {
                return None;
            }
            found = Some(reference);
            search_from = reference_end;
        }
    }

    found
}

/// Extract the owning skill from any path below a supported skill root.
///
/// This is used for attributing an already-observed tool call to a skill and
/// intentionally accepts paths such as `skills/<name>/scripts/query.js`.
/// Invocation inference uses the stricter `extract_skill_reference_from_command_text`
/// helper and still requires a complete `SKILL.md` path.
pub fn extract_skill_name_from_command_text(command: &str) -> Option<String> {
    const MARKERS: &[&str] = &[".codex/skills/", ".agents/skills/"];
    for marker in MARKERS {
        let mut search = command;
        while let Some(position) = search.find(marker) {
            let after_marker = &search[position + marker.len()..];
            let candidate_path = after_marker
                .strip_prefix(".system/")
                .unwrap_or(after_marker);
            if let Some(separator) = candidate_path.find('/') {
                let name = &candidate_path[..separator];
                if is_valid_skill_name(name) {
                    return Some(name.to_string());
                }
            }
            search = after_marker;
        }
    }
    None
}

pub(super) fn parse_json_string_prefix(value: &str) -> Option<(String, usize)> {
    if !value.starts_with('"') {
        return None;
    }
    let mut escaped = false;
    for (index, ch) in value.char_indices().skip(1) {
        if escaped {
            escaped = false;
            continue;
        }
        match ch {
            '\\' => escaped = true,
            '"' => {
                let end = index + ch.len_utf8();
                let decoded = serde_json::from_str::<String>(&value[..end]).ok()?;
                return Some((decoded, end));
            }
            _ => {}
        }
    }
    None
}

pub(super) fn find_supported_exec_object(source: &str) -> Option<&str> {
    const CALL: &str = "tools.exec_command";
    let bytes = source.as_bytes();
    let mut index = 0;
    let mut quote = None;
    let mut escaped = false;
    while index < bytes.len() {
        let ch = source[index..].chars().next()?;
        let ch_len = ch.len_utf8();
        if let Some(active_quote) = quote {
            if escaped {
                escaped = false;
            } else if ch == '\\' {
                escaped = true;
            } else if ch == active_quote {
                quote = None;
            }
            index += ch_len;
            continue;
        }
        if matches!(ch, '"' | '\'' | '`') {
            quote = Some(ch);
            index += ch_len;
            continue;
        }
        if source[index..].starts_with("//") {
            index = source[index..]
                .find('\n')
                .map_or(bytes.len(), |offset| index + offset + 1);
            continue;
        }
        if source[index..].starts_with("/*") {
            index = source[index + 2..]
                .find("*/")
                .map_or(bytes.len(), |offset| index + offset + 4);
            continue;
        }
        let direct_member_boundary = source[..index].chars().next_back().is_none_or(|previous| {
            !previous.is_ascii_alphanumeric() && !matches!(previous, '_' | '$' | '.')
        });
        if direct_member_boundary && source[index..].starts_with(CALL) {
            let mut cursor = index + CALL.len();
            while source[cursor..].starts_with(char::is_whitespace) {
                cursor += source[cursor..].chars().next()?.len_utf8();
            }
            if !source[cursor..].starts_with('(') {
                index += CALL.len();
                continue;
            }
            cursor += 1;
            while source[cursor..].starts_with(char::is_whitespace) {
                cursor += source[cursor..].chars().next()?.len_utf8();
            }
            if !source[cursor..].starts_with('{') {
                index += CALL.len();
                continue;
            }

            let object_start = cursor + 1;
            let mut object_cursor = object_start;
            let mut depth = 1_u32;
            let mut object_quote = None;
            let mut object_escaped = false;
            while object_cursor < bytes.len() {
                let object_ch = source[object_cursor..].chars().next()?;
                let object_ch_len = object_ch.len_utf8();
                if let Some(active_quote) = object_quote {
                    if object_escaped {
                        object_escaped = false;
                    } else if object_ch == '\\' {
                        object_escaped = true;
                    } else if object_ch == active_quote {
                        object_quote = None;
                    }
                    object_cursor += object_ch_len;
                    continue;
                }
                if matches!(object_ch, '"' | '\'' | '`') {
                    object_quote = Some(object_ch);
                } else if source[object_cursor..].starts_with("//")
                    || source[object_cursor..].starts_with("/*")
                {
                    return None;
                } else if object_ch == '{' {
                    depth += 1;
                } else if object_ch == '}' {
                    depth -= 1;
                    if depth == 0 {
                        let mut after = object_cursor + 1;
                        while source[after..].starts_with(char::is_whitespace) {
                            after += source[after..].chars().next()?.len_utf8();
                        }
                        return source[after..]
                            .starts_with(')')
                            .then_some(&source[object_start..object_cursor]);
                    }
                }
                object_cursor += object_ch_len;
            }
            return None;
        }
        index += ch_len;
    }
    None
}

pub(super) fn split_top_level_object_properties(object: &str) -> Option<Vec<&str>> {
    let bytes = object.as_bytes();
    let mut properties = Vec::new();
    let mut start = 0;
    let mut depth = 0_i32;
    let mut quote = None;
    let mut escaped = false;
    for index in 0..bytes.len() {
        let ch = bytes[index] as char;
        if let Some(active_quote) = quote {
            if escaped {
                escaped = false;
            } else if ch == '\\' {
                escaped = true;
            } else if ch == active_quote {
                quote = None;
            }
            continue;
        }
        if matches!(ch, '"' | '\'' | '`') {
            quote = Some(ch);
            continue;
        }
        match ch {
            '{' | '[' | '(' => depth += 1,
            '}' | ']' | ')' => depth -= 1,
            ',' if depth == 0 => {
                properties.push(&object[start..index]);
                start = index + 1;
            }
            _ => {}
        }
        if depth < 0 {
            return None;
        }
    }
    if quote.is_some() || depth != 0 {
        return None;
    }
    properties.push(&object[start..]);
    Some(properties)
}

pub(super) fn parse_static_exec_command_wrapper(source: &str) -> Option<String> {
    let object = find_supported_exec_object(source)?;
    for property in split_top_level_object_properties(object)? {
        let property = property.trim();
        let (key, remainder) = if let Some((key, consumed)) = parse_json_string_prefix(property) {
            (key, &property[consumed..])
        } else {
            let key_end = property
                .find(|ch: char| !ch.is_ascii_alphanumeric() && ch != '_')
                .unwrap_or(property.len());
            (property[..key_end].to_string(), &property[key_end..])
        };
        let remainder = remainder.trim_start();
        let Some(value_source) = remainder.strip_prefix(':').map(str::trim_start) else {
            continue;
        };
        if key != "cmd" && key != "command" {
            continue;
        }
        let (command, consumed) = parse_json_string_prefix(value_source)?;
        if !value_source[consumed..].trim().is_empty() {
            return None;
        }
        return Some(command);
    }
    None
}

fn skill_read_from_argv(arguments: &[&str]) -> Option<String> {
    let (program, operands) = arguments.split_first()?;
    let program = program.rsplit('/').next()?;
    let paths = match program {
        "cat" => operands,
        "head" | "tail" => match operands {
            [_] | ["--", _] => operands,
            ["-n" | "-c" | "--lines" | "--bytes", count, path @ ..]
                if count.parse::<i64>().is_ok() =>
            {
                path
            }
            _ => return None,
        },
        // Recognize the usual range-print form, without interpreting sed scripts.
        "sed" => {
            let ["-n", expression, paths @ ..] = operands else {
                return None;
            };
            let range = expression.strip_suffix('p')?;
            if !range.chars().all(|c| c.is_ascii_digit() || matches!(c, ',' | '$'))
                || paths.iter().any(|path| path.starts_with('-') && *path != "--")
            {
                return None;
            }
            paths
        }
        _ => return None,
    };
    // A command's overall exit code cannot identify which of several files failed.
    let mut reading_options = true;
    let mut paths = paths.iter().filter(|path| {
        if reading_options && **path == "--" {
            reading_options = false;
            return false;
        }
        !reading_options || **path == "-" || !path.starts_with('-')
    });
    let path = paths.next()?;
    if !path.ends_with("/SKILL.md") || paths.next().is_some() {
        return None;
    }
    extract_skill_reference_from_command_text(path).map(|reference| reference.name)
}

fn skill_read_from_shell_text(command: &str) -> Option<String> {
    let command = command.trim();
    if command.contains([';', '|', '&', '<', '>', '\n', '`']) || command.contains("$(") {
        return None;
    }
    let arguments = shlex::split(command)?;
    let argv: Vec<_> = arguments.iter().map(String::as_str).collect();
    skill_read_from_argv(&argv)
}

pub(crate) fn extract_skill_read_name(tool: &ToolCall) -> Option<String> {
    if tool.name == "exec" {
        let command = parse_static_exec_command_wrapper(tool.input.as_str()?)?;
        return skill_read_from_shell_text(&command);
    }
    if !super::is_shell_tool(&tool.name) {
        return None;
    }
    let command = tool.input.get("command").or_else(|| tool.input.get("cmd"))?;
    match command {
        Value::String(text) => skill_read_from_shell_text(text),
        Value::Array(arguments) => {
            let argv: Vec<_> = arguments.iter().map(Value::as_str).collect::<Option<_>>()?;
            if let [shell, "-c" | "-lc", command] = argv.as_slice() {
                if matches!(shell.rsplit('/').next(), Some("sh" | "bash" | "zsh" | "dash" | "ksh")) {
                    return skill_read_from_shell_text(command);
                }
            }
            skill_read_from_argv(&argv)
        }
        _ => None,
    }
}

pub(super) fn detect_skill_from_tool_input(
    raw_name: &str,
    raw_args: &Value,
    parsed_args: &Value,
    timestamp: &str,
) -> Option<SkillInvocation> {
    let command = match raw_name {
        "exec_command" => parsed_args
            .get("cmd")
            .or_else(|| parsed_args.get("command"))
            .and_then(Value::as_str)
            .map(str::to_string),
        "exec" => raw_args
            .as_str()
            .and_then(parse_static_exec_command_wrapper),
        _ => None,
    }?;
    let reference = extract_skill_reference_from_command_text(&command)?;
    Some(SkillInvocation {
        raw_placeholder: format!("${}", reference.name),
        name: reference.name,
        source: SkillInvocationSource::CommandPathFallback,
        timestamp: timestamp.to_string(),
        byte_start: None,
        byte_end: None,
        resolved_workflow: None,
        definition_path: Some(reference.path),
        original_text_element_index: None,
        ordinal: 0,
    })
}

/// Pattern 2: detect skill from user message markdown link `[$name](SKILL.md)` or `[$name](/path/to/SKILL.md)`.
pub(super) fn detect_skill_from_user_message(user_text: &str) -> Option<String> {
    // Find pattern: [$<name>](<anything>SKILL.md)
    let mut search = user_text;
    loop {
        let start = search.find("[$")?;
        let after_dollar = &search[start + 2..];
        let close_bracket = after_dollar.find(']')?;
        let name = &after_dollar[..close_bracket];
        let after_bracket = &after_dollar[close_bracket + 1..];
        if after_bracket.starts_with('(') {
            if let Some(paren_end) = after_bracket.find(')') {
                let link_target = &after_bracket[1..paren_end];
                if link_target == "SKILL.md" || link_target.ends_with("/SKILL.md") {
                    if !name.is_empty() {
                        return Some(name.to_string());
                    }
                }
            }
        }
        // Advance past this `[$` to search for next occurrence
        search = &search[start + 2..];
    }
}

/// Pattern 3: detect skill from `<skill><name>xxx</name>` XML tag in message content.
pub(super) fn detect_skill_from_skill_tag(text: &str) -> Option<String> {
    let open = text.find("<skill>")?;
    let after_skill = &text[open + "<skill>".len()..];
    let name_open = after_skill.find("<name>")?;
    let after_name = &after_skill[name_open + "<name>".len()..];
    let name_close = after_name.find("</name>")?;
    let name = after_name[..name_close].trim();
    if name.is_empty() {
        return None;
    }
    Some(name.to_string())
}

pub(super) fn parse_skill_definition(text: &str, timestamp: &str) -> Option<LoadedSkillDefinition> {
    let trimmed = text.trim();
    if !trimmed.starts_with("<skill>") {
        return None;
    }
    let block_end = trimmed.find("</skill>")? + "</skill>".len();
    let block = &trimmed[..block_end];
    let name = detect_skill_from_skill_tag(block)?;
    let path = extract_xml_tag(block, "path");
    Some(LoadedSkillDefinition {
        name,
        path,
        timestamp: timestamp.to_string(),
    })
}

pub(super) fn extract_xml_tag(text: &str, tag: &str) -> Option<String> {
    let open = format!("<{tag}>");
    let close = format!("</{tag}>");
    let after_open = text.split_once(&open)?.1;
    let value = after_open.split_once(&close)?.0.trim();
    (!value.is_empty()).then(|| value.to_string())
}

pub(super) fn parse_skill_invocations_from_text_elements(
    user_text: &str,
    text_elements: Option<&Vec<Value>>,
    timestamp: &str,
) -> Vec<SkillInvocation> {
    let mut invocations = Vec::new();
    let Some(text_elements) = text_elements else {
        return invocations;
    };

    for (index, element) in text_elements.iter().enumerate() {
        let Some(placeholder) = element.get("placeholder").and_then(Value::as_str) else {
            continue;
        };
        let Some(name) = placeholder
            .strip_prefix('$')
            .filter(|name| is_valid_skill_name(name))
        else {
            continue;
        };
        let Some(byte_range) = element.get("byte_range") else {
            continue;
        };
        let Some(start) = byte_range.get("start").and_then(Value::as_u64) else {
            continue;
        };
        let Some(end) = byte_range.get("end").and_then(Value::as_u64) else {
            continue;
        };
        let (Ok(start_index), Ok(end_index)) = (usize::try_from(start), usize::try_from(end))
        else {
            continue;
        };
        if user_text.get(start_index..end_index) != Some(placeholder) {
            continue;
        }

        push_skill_invocation_dedup(
            &mut invocations,
            SkillInvocation {
                name: name.to_string(),
                raw_placeholder: placeholder.to_string(),
                source: SkillInvocationSource::CodexTextElement,
                timestamp: timestamp.to_string(),
                byte_start: Some(start),
                byte_end: Some(end),
                resolved_workflow: None,
                definition_path: None,
                original_text_element_index: Some(index),
                ordinal: 0,
            },
        );
    }

    invocations
}

#[derive(Debug, Clone, Default)]
pub(super) struct ParsedUserMessageEvidence {
    pub(super) text: String,
    pub(super) invocations: Vec<SkillInvocation>,
    pub(super) definitions: Vec<LoadedSkillDefinition>,
}

pub(super) fn parse_item_completed_user_message(
    payload: &Value,
    timestamp: &str,
) -> Option<ParsedUserMessageEvidence> {
    if payload.get("type").and_then(Value::as_str) != Some("item_completed") {
        return None;
    }
    let item = payload.get("item")?;
    if item.get("type").and_then(Value::as_str) != Some("UserMessage") {
        return None;
    }

    let mut evidence = ParsedUserMessageEvidence::default();
    for content in item.get("content").and_then(Value::as_array)? {
        match content.get("type").and_then(Value::as_str) {
            Some("text") => {
                let raw_text = content.get("text").and_then(Value::as_str).unwrap_or("");
                if raw_text.is_empty() {
                    continue;
                }
                let byte_offset = if evidence.text.is_empty() {
                    0
                } else {
                    evidence.text.push('\n');
                    evidence.text.len()
                };
                evidence.text.push_str(raw_text);
                let mut invocations = parse_skill_invocations_from_text_elements(
                    raw_text,
                    content.get("text_elements").and_then(Value::as_array),
                    timestamp,
                );
                for invocation in &mut invocations {
                    invocation.byte_start = invocation
                        .byte_start
                        .map(|value| value.saturating_add(byte_offset as u64));
                    invocation.byte_end = invocation
                        .byte_end
                        .map(|value| value.saturating_add(byte_offset as u64));
                }
                evidence.invocations.extend(invocations);
            }
            Some("skill") => {
                let Some(name) = content
                    .get("name")
                    .and_then(Value::as_str)
                    .filter(|name| is_valid_skill_name(name))
                else {
                    continue;
                };
                evidence.definitions.push(LoadedSkillDefinition {
                    name: name.to_string(),
                    path: content
                        .get("path")
                        .and_then(Value::as_str)
                        .filter(|path| !path.is_empty())
                        .map(str::to_string),
                    timestamp: timestamp.to_string(),
                });
            }
            _ => {}
        }
    }

    if !evidence.definitions.is_empty() {
        evidence.invocations.retain_mut(|invocation| {
            let Some(definition) = evidence
                .definitions
                .iter()
                .find(|definition| definition.name == invocation.name)
            else {
                return false;
            };
            invocation.definition_path = definition.path.clone();
            true
        });
    }
    Some(evidence)
}

pub(super) fn detect_leading_skill_placeholder(text: &str) -> Option<String> {
    let trimmed = text.trim_start();
    let remainder = trimmed.strip_prefix('$')?;
    let name_end = remainder
        .find(|ch: char| ch.is_whitespace())
        .unwrap_or(remainder.len());
    let name = &remainder[..name_end];
    is_valid_skill_name(name).then(|| name.to_string())
}

pub(super) fn parse_omx_user_prompt_submit_route(text: &str) -> Option<(String, String)> {
    const PREFIX: &str = "OMX native UserPromptSubmit detected workflow keyword \"";
    const ROUTING_SUFFIX: &str =
        " Follow AGENTS.md routing and preserve workflow transition and planning-safety rules.";
    let remainder = text.strip_prefix(PREFIX)?;
    let (raw_placeholder, remainder) = remainder.split_once("\" -> ")?;
    let route_and_optional_skill = remainder.strip_suffix(ROUTING_SUFFIX)?;
    let resolved_workflow = route_and_optional_skill
        .split_once(". skill: ")
        .map(|(workflow, _)| workflow)
        .or_else(|| route_and_optional_skill.strip_suffix('.'))?;
    let keyword = raw_placeholder
        .strip_prefix('$')
        .filter(|value| is_valid_skill_name(value))?;
    if !is_valid_skill_name(resolved_workflow) {
        return None;
    }
    Some((format!("${keyword}"), resolved_workflow.to_string()))
}

pub(super) fn push_skill_invocation_dedup(
    invocations: &mut Vec<SkillInvocation>,
    invocation: SkillInvocation,
) {
    let duplicate = invocations.iter().any(|existing| {
        existing.name == invocation.name
            && existing.byte_start == invocation.byte_start
            && existing.byte_end == invocation.byte_end
            && existing.source == invocation.source
    });
    if !duplicate {
        invocations.push(invocation);
    }
}

pub(super) fn apply_omx_route(
    invocations: &mut Vec<SkillInvocation>,
    raw_placeholder: &str,
    resolved_workflow: &str,
    timestamp: &str,
) {
    let keyword = raw_placeholder.strip_prefix('$').unwrap_or(raw_placeholder);
    if let Some(invocation) = invocations.iter_mut().find(|invocation| {
        invocation.source == SkillInvocationSource::CodexTextElement
            && (invocation.name == keyword || invocation.name == resolved_workflow)
            && invocation.resolved_workflow.is_none()
    }) {
        invocation.resolved_workflow = Some(resolved_workflow.to_string());
        return;
    }

    push_skill_invocation_dedup(
        invocations,
        SkillInvocation {
            name: resolved_workflow.to_string(),
            raw_placeholder: raw_placeholder.to_string(),
            source: SkillInvocationSource::OmxUserPromptSubmit,
            timestamp: timestamp.to_string(),
            byte_start: None,
            byte_end: None,
            resolved_workflow: Some(resolved_workflow.to_string()),
            definition_path: None,
            original_text_element_index: None,
            ordinal: 0,
        },
    );
}

pub(super) fn finalize_skill_invocations(
    mut invocations: Vec<SkillInvocation>,
    definitions: &[LoadedSkillDefinition],
) -> Vec<SkillInvocation> {
    let route_indices: Vec<usize> = invocations
        .iter()
        .enumerate()
        .filter_map(|(index, invocation)| {
            (invocation.source == SkillInvocationSource::OmxUserPromptSubmit).then_some(index)
        })
        .collect();
    let mut consumed_routes = Vec::new();
    for route_index in route_indices {
        let route = invocations[route_index].clone();
        let route_keyword = route
            .raw_placeholder
            .strip_prefix('$')
            .unwrap_or(&route.raw_placeholder);
        if let Some(explicit) = invocations.iter_mut().find(|invocation| {
            invocation.source == SkillInvocationSource::CodexTextElement
                && invocation.resolved_workflow.is_none()
                && (invocation.name == route_keyword || invocation.name == route.name)
        }) {
            explicit.resolved_workflow = route.resolved_workflow.clone();
            consumed_routes.push(route_index);
        }
    }
    for route_index in consumed_routes.into_iter().rev() {
        invocations.remove(route_index);
    }

    if invocations
        .iter()
        .any(|invocation| invocation.source == SkillInvocationSource::CodexTextElement)
    {
        invocations
            .retain(|invocation| invocation.source == SkillInvocationSource::CodexTextElement);
    } else if invocations
        .iter()
        .any(|invocation| invocation.source == SkillInvocationSource::OmxUserPromptSubmit)
    {
        invocations
            .retain(|invocation| invocation.source == SkillInvocationSource::OmxUserPromptSubmit);
    } else if invocations
        .iter()
        .any(|invocation| invocation.source == SkillInvocationSource::LegacyMarkdown)
    {
        invocations
            .retain(|invocation| invocation.source != SkillInvocationSource::CommandPathFallback);
    }

    invocations.sort_by(|left, right| {
        let left_key = (
            left.byte_start.unwrap_or(u64::MAX),
            left.byte_end.unwrap_or(u64::MAX),
            left.original_text_element_index.unwrap_or(usize::MAX),
        );
        let right_key = (
            right.byte_start.unwrap_or(u64::MAX),
            right.byte_end.unwrap_or(u64::MAX),
            right.original_text_element_index.unwrap_or(usize::MAX),
        );
        left_key.cmp(&right_key)
    });

    for (ordinal, invocation) in invocations.iter_mut().enumerate() {
        invocation.ordinal = ordinal;
        if invocation.definition_path.is_none() {
            invocation.definition_path = definitions
                .iter()
                .find(|definition| {
                    invocation.resolved_workflow.as_deref() == Some(definition.name.as_str())
                        || definition.name == invocation.name
                })
                .and_then(|definition| definition.path.clone());
        }
    }

    invocations
}

pub(super) fn merge_user_message_evidence_into_finalized_turn(
    turn: &mut Turn,
    evidence: ParsedUserMessageEvidence,
    timestamp: &str,
) {
    let text = evidence.text.trim();
    if turn.user_text.is_empty() && !text.is_empty() {
        turn.user_text = text.to_string();
        turn.user_timestamp = timestamp.to_string();
    }

    let mut invocations = std::mem::take(&mut turn.skill_invocations);
    for invocation in evidence.invocations {
        push_skill_invocation_dedup(&mut invocations, invocation);
    }
    turn.skill_invocations = finalize_skill_invocations(invocations, &evidence.definitions);
    turn.skill_name = turn
        .skill_invocations
        .first()
        .map(|invocation| invocation.name.clone())
        .or_else(|| turn.skill_name.take());
}
