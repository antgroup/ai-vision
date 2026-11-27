"""Skill span recognition shared by the labs scanner and trace projection.

The recognition rules mirror the frontend resolver in
vfe/src/labs/skill/skillDisplay.ts: native plugin reports are authoritative
and TOOL spans are shape-derived from SKILL.md read payloads (reader-gated).
Kept free of Django imports so the trace projection can use it on hot paths.
"""

import json
import re

from .tool_outcome import tool_span_outcome

# Span attribute variants written by the plugins, in probe order. Mirrors
# ARGUMENT_KEYS in the frontend resolver.
ARGUMENT_KEYS = (
    "agentic.tool.call.arguments",
    "gen_ai.tool.call.arguments",
    "gen_ai.tool.call.input",
)
SKILL_MD_PATH = re.compile(r"skills/([A-Za-z0-9._-]+)/SKILL\.md")
# Reader-gate allowlists, mirroring the collector side (skill.rs
# READER_PROGRAMS / WRAPPER_PROGRAMS). A SKILL.md mention only counts as a
# read when some pipeline segment's first program is a reader.
SKILL_READER_PROGRAMS = frozenset(
    {
        "cat",
        "head",
        "tail",
        "sed",
        "awk",
        "grep",
        "rg",
        "less",
        "more",
        "bat",
        "read_file",
        "readfile",
    }
)
SKILL_WRAPPER_PROGRAMS = frozenset({"sh", "bash", "zsh", "dash", "ksh"})
# Native invocations the plugin inferred from command text: their arguments
# JSON carries only {skill, invocation, source, byte_range} — no command
# text — so the platform cannot re-judge them. The same event re-enters
# through the gated shape branch on the exec TOOL span instead.
SKILL_FALLBACK_SOURCES = frozenset({"command_path_fallback"})
# Tool names that maintain a SKILL.md rather than use the skill: a write
# shape never derives usage even when the path matches.
SKILL_WRITER_TOOL_NAMES = frozenset(
    {
        "write",
        "write_file",
        "writefile",
        "edit",
        "edit_file",
        "apply_patch",
        "multi_edit",
        "create_file",
        "notebook_edit",
        "str_replace",
    }
)
# Some serializers escape the slash itself (\/skills\/x\/SKILL.md).
ESCAPED_SLASH = re.compile(r"\\/")


def _attribute_text(attributes: dict, key: str) -> str | None:
    value = attributes.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _argument_gate_text(attributes: dict) -> str:
    """Raw argument values; the JSON-envelope channel needs intact quotes."""
    parts = []
    for key in ARGUMENT_KEYS:
        value = attributes.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value)
    return "\n".join(parts)


def _split_unquoted_operators(text: str) -> list[str]:
    """Split on ``; | &`` and newlines outside quotes, so a reader named
    inside an ``echo "..."`` string cannot pass the gate."""
    segments: list[str] = []
    start = 0
    quote: str | None = None
    escaped = False
    for index, ch in enumerate(text):
        if escaped:
            escaped = False
            continue
        if quote is not None:
            if ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch in "\"'":
            quote = ch
            continue
        if ch in ";|&\n":
            segments.append(text[start:index])
            start = index + 1
    segments.append(text[start:])
    return segments


def _shell_tokens(segment: str) -> list[str]:
    """Whitespace tokens that respect quoting, so ``"cat a; b"`` stays one."""
    tokens: list[str] = []
    start: int | None = None
    quote: str | None = None
    escaped = False
    for index, ch in enumerate(segment):
        if escaped:
            escaped = False
            continue
        if quote is not None:
            if ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
            continue
        if ch == "\\":
            escaped = True
            if start is None:
                start = index
            continue
        if ch in "\"'":
            quote = ch
            if start is None:
                start = index
            continue
        if ch.isspace():
            if start is not None:
                tokens.append(segment[start:index])
                start = None
            continue
        if start is None:
            start = index
    if start is not None:
        tokens.append(segment[start:])
    return tokens


def _unquote(token: str) -> str:
    trimmed = token.strip()
    if len(trimmed) >= 2 and trimmed[0] == trimmed[-1] and trimmed[0] in "\"'":
        return trimmed[1:-1]
    return trimmed


def _redirects_onto_skill_md(tokens: list[str]) -> bool:
    """True when a redirection inside the segment targets the SKILL.md path
    itself (``cat > x/SKILL.md``) — maintaining the file, not reading it.
    Handles attached forms (``>>file``, ``2>file``) via the quoted token
    stream, so a ``>`` inside an echo string never matches."""
    for position, token in enumerate(tokens):
        bare = _unquote(token)
        if bare in {">", ">>"}:
            if (
                position + 1 < len(tokens)
                and SKILL_MD_PATH.search(_unquote(tokens[position + 1]))
            ):
                return True
            continue
        while bare[:1].isdigit():
            bare = bare[1:]
        for operator in (">>", ">"):
            if bare.startswith(operator) and len(bare) > len(operator):
                if SKILL_MD_PATH.search(_unquote(bare[len(operator) :])):
                    return True
                break
    return False


def _edits_in_place(tokens: list[str]) -> bool:
    """True for ``sed -i`` / ``sed --in-place`` / ``sed -i.bak`` shapes."""
    for token in tokens[1:]:
        bare = _unquote(token)
        if not bare.startswith("-"):
            continue
        if bare.startswith("--in-place") or (
            not bare.startswith("--") and bare[1:2] == "i"
        ):
            return True
    return False


def _segment_skill_name(segment: str, depth: int = 0) -> str | None:
    """Return a skill path read by this segment's reader or shell wrapper.

    The path and reader must belong to the same command: a separate echo
    mentioning SKILL.md beside ``cat README.md`` is not skill usage.
    """
    trimmed = segment.strip()
    if not trimmed or trimmed.startswith("#") or depth >= 3:
        return None
    tokens = _shell_tokens(trimmed)
    if not tokens:
        return None
    program = tokens[0].rsplit("/", 1)[-1]
    if program in SKILL_READER_PROGRAMS:
        # A reader can also maintain the file: redirection onto the path
        # itself or an in-place edit is not usage.
        if _redirects_onto_skill_md(tokens) or (
            program == "sed" and _edits_in_place(tokens)
        ):
            return None
        match = SKILL_MD_PATH.search(ESCAPED_SLASH.sub("/", trimmed))
        return match.group(1) if match else None
    if program not in SKILL_WRAPPER_PROGRAMS:
        return None
    for position, token in enumerate(tokens[1:], 1):
        if _unquote(token) not in {"-c", "-lc"}:
            continue
        if position + 1 >= len(tokens):
            return None
        inner = tokens[position + 1]
        if inner.startswith("-"):
            return None
        return _shell_skill_name(_unquote(inner), depth + 1)
    return None


def _shell_skill_name(text: str, depth: int = 0) -> str | None:
    for segment in _split_unquoted_operators(text):
        name = _segment_skill_name(segment, depth)
        if name:
            return name
    return None


_JSON_STRING_ESCAPES = {
    '"': '"',
    "\\": "\\",
    "/": "/",
    "n": "\n",
    "t": "\t",
    "r": "\r",
    "'": "'",
    "b": "\b",
    "f": "\f",
}


def _unescape_json_string(text: str) -> str:
    out: list[str] = []
    index = 0
    while index < len(text):
        ch = text[index]
        if ch == "\\" and index + 1 < len(text):
            nxt = text[index + 1]
            out.append(_JSON_STRING_ESCAPES.get(nxt, "\\" + nxt))
            index += 2
            continue
        out.append(ch)
        index += 1
    return "".join(out)


def _string_literals(text: str) -> list[str]:
    """Quoted string contents, escape-aware, so a JSON envelope's values can
    be re-judged after one unescape round."""
    literals: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        ch = text[index]
        if ch == "\\":
            index += 2
            continue
        if ch in "\"'":
            quote = ch
            index += 1
            start = index
            while index < length:
                inner = text[index]
                if inner == "\\":
                    index += 2
                    continue
                if inner == quote:
                    break
                index += 1
            literals.append(text[start:index])
            index += 1
            continue
        index += 1
    return literals


def _is_pure_path_argument(value: str) -> bool:
    trimmed = value.strip()
    if not trimmed:
        return False
    return not any(ch.isspace() for ch in trimmed) and not any(
        ch in trimmed for ch in ";|&"
    )


def _read_skill_name(raw_text: str) -> str | None:
    """Recognize command text, a command in a JSON envelope, or a reader
    tool's pure-path argument, keeping the name tied to its read evidence."""
    name = _shell_skill_name(raw_text)
    if name:
        return name
    if not raw_text.lstrip().startswith("{"):
        return None
    for literal in _string_literals(raw_text):
        value = _unescape_json_string(literal)
        name = _shell_skill_name(value)
        if name:
            return name
        if _is_pure_path_argument(value):
            match = SKILL_MD_PATH.search(value)
            if match:
                return match.group(1)
    return None


def _is_fallback_invocation(attributes: dict) -> bool:
    """True when the native span's arguments JSON marks the invocation as
    plugin-inferred from command text. Missing, unparsable, or redacted
    payloads mean a semantic signal — cchooks writes no source at all."""
    for key in ARGUMENT_KEYS:
        value = attributes.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        try:
            parsed = json.loads(value)
        except ValueError:
            continue
        if isinstance(parsed, dict) and parsed.get("source") in SKILL_FALLBACK_SOURCES:
            return True
    return False


def _skill_span_info(span: dict) -> tuple[str, bool] | None:
    """(name, derived) for a skill call span; None otherwise.

    Native attributes are authoritative; the shape branch reads only this
    span's own arguments, never its result, and is gated to TOOL spans so
    middleware narration never matches.
    """
    attributes = span.get("attributes") if isinstance(span, dict) else None
    if not isinstance(attributes, dict):
        return None

    tool_type = _attribute_text(attributes, "agentic.tool.type")
    if tool_type and tool_type.lower() == "skill":
        if _is_fallback_invocation(attributes):
            return None
        name = _attribute_text(attributes, "agentic.tool.name") or _attribute_text(
            attributes, "agentic.skill.name"
        )
        derived = str(attributes.get("agentic.tool.derived", "")).strip().lower() in {
            "true",
            "1",
        }
        return name, derived

    stored_name = _attribute_text(attributes, "agentic.skill.name")
    if stored_name:
        if _is_fallback_invocation(attributes):
            return None
        return stored_name, False

    kind = str(attributes.get("gen_ai.span.kind", "")).upper()

    if kind == "TOOL":
        tool_name = (
            _attribute_text(attributes, "gen_ai.tool.call.name")
            or _attribute_text(attributes, "agentic.tool.raw_name")
            or _attribute_text(attributes, "agentic.tool.name")
            or _attribute_text(attributes, "gen_ai.tool.name")
            or ""
        ).lower()
        if tool_name not in SKILL_WRITER_TOOL_NAMES:
            name = _read_skill_name(_argument_gate_text(attributes))
            if name:
                return name, True

    return None


def _reported_skill_name(attributes: object) -> str | None:
    if not isinstance(attributes, dict):
        return None
    tool_type = _attribute_text(attributes, "agentic.tool.type")
    if tool_type and tool_type.lower() == "skill":
        return _attribute_text(attributes, "agentic.tool.name") or _attribute_text(
            attributes, "agentic.skill.name"
        )
    return _attribute_text(attributes, "agentic.skill.name")


def _native_skill_name(attributes: object) -> str | None:
    """Exclude synthetic command-path reports whose source tool carries the evidence."""
    name = _reported_skill_name(attributes)
    return name if name is not None and not _is_fallback_invocation(attributes) else None


def _is_codex_shell(span: dict, attributes: dict, agent_product: str) -> bool:
    name = (
        _attribute_text(attributes, "agentic.tool.raw_name")
        or _attribute_text(attributes, "gen_ai.tool.call.name")
        or _attribute_text(attributes, "gen_ai.tool.name")
        or ""
    ).lower()
    if name == "exec_command":
        return True
    if name not in {"bash", "shell_command"}:
        return False
    resource = span.get("resource")
    resource = resource.get("attributes", resource) if isinstance(resource, dict) else None
    sources = (attributes, resource, span.get("resource_attributes"))
    identities = [span.get("agent_product"), agent_product]
    for key in ("aivision.agent.product", "ant.agent.product", "ant.agent.type", "agentic.runtime.name"):
        identities.extend(source.get(key) for source in sources if isinstance(source, dict))
        identities.append(span.get(f"resource.attributes.{key}"))
    return any(isinstance(value, str) and value.strip().lower() == "codex" for value in identities)


def _single_read_tokens(command: object) -> list[str]:
    if isinstance(command, list) and all(isinstance(token, str) for token in command):
        return command
    if not isinstance(command, str) or re.search(r"[;|&<>\n`]|\$\(", command.strip()):
        return []
    return [_unquote(token) for token in _shell_tokens(command)]


def _single_codex_read_name(attributes: dict) -> str | None:
    """Attribute a shell outcome only to a single, direct SKILL.md operand."""
    for key in ARGUMENT_KEYS:
        arguments = attributes.get(key)
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except ValueError:
                pass
        if isinstance(arguments, dict):
            arguments = arguments.get("command", arguments.get("cmd"))
        tokens = _single_read_tokens(arguments)
        if (len(tokens) == 3 and tokens[0].rsplit("/", 1)[-1] in SKILL_WRAPPER_PROGRAMS
                and tokens[1] in {"-c", "-lc"}):
            tokens = _single_read_tokens(tokens[2])
        if not tokens:
            continue
        program, *paths = tokens
        program = program.rsplit("/", 1)[-1]
        if program in {"head", "tail"}:
            if len(paths) >= 3 and paths[0] in {"-n", "-c", "--lines", "--bytes"} and re.fullmatch(r"[+-]?[0-9]+", paths[1]):
                paths = paths[2:]
            elif len(paths) > 1 and not (len(paths) == 2 and paths[0] == "--"):
                continue
        elif program == "sed":
            if len(paths) < 3 or paths[0] != "-n" or not re.fullmatch(r"[0-9,$]*p", paths[1]):
                continue
            paths = paths[2:]
            if any(path.startswith("-") and path != "--" for path in paths):
                continue
        elif program != "cat":
            continue
        option_end = paths.index("--") if "--" in paths else len(paths)
        operands = [
            path for index, path in enumerate(paths)
            if index > option_end or path == "-" or not path.startswith("-")
        ]
        if len(operands) == 1 and operands[0].endswith("/SKILL.md"):
            match = SKILL_MD_PATH.search(operands[0])
            if match:
                return match.group(1)
    return None


def _failed_skill_read(
    span: dict, info: tuple[str, bool], *, reported: bool, agent_product: str = ""
) -> bool:
    """Detect failure on the source read, preserving native Skill call outcomes."""
    name, derived = info
    attributes = span["attributes"]
    if str(attributes.get("gen_ai.span.kind", "")).upper() != "TOOL":
        return False
    codex_shell = _is_codex_shell(span, attributes, agent_product)
    if reported and not codex_shell:
        return False
    if tool_span_outcome(span, codex_shell=codex_shell) != "error":
        return False
    if not codex_shell:
        return True
    if _single_codex_read_name(attributes) == name:
        return True
    # Older hooks can label the physical Bash span as a Skill. Its reader
    # arguments must match that Skill name. When arguments are unavailable,
    # the derived-read marker distinguishes a read from an attributed script.
    arguments = (attributes[key] for key in ARGUMENT_KEYS if attributes.get(key) is not None)
    return (
        reported
        and derived
        and all(
            isinstance(part, str)
            and part.strip() in {"", "[REDACTED]", '"[REDACTED]"', "[TRUNCATED]", '"[TRUNCATED]"'}
            for part in arguments
        )
        and tool_span_outcome(span) == "error"
    )


def trace_skill_tag_changes(spans: object) -> tuple[list[str], set[str]]:
    """Derive Skill tags and identify stale ones written by a failed-read fallback."""
    if not isinstance(spans, list):
        return [], set()
    calls: set[str] = set()
    failed: set[str] = set()
    tagged_fallbacks: set[str] = set()
    for span in spans:
        info = _skill_span_info(span)
        attributes = span.get("attributes") if isinstance(span, dict) else None
        if not info or not info[0]:
            name = _reported_skill_name(attributes)
            if name and _is_fallback_invocation(attributes):
                tagged_fallbacks.add(name)
            continue
        name = info[0]
        reported = _native_skill_name(attributes) is not None
        if _failed_skill_read(span, info, reported=reported):
            failed.add(name)
            if reported:
                tagged_fallbacks.add(name)
        else:
            calls.add(name)
    stale = failed.intersection(tagged_fallbacks).difference(calls)
    return sorted(f"skill:{name}" for name in calls), {f"skill:{name}" for name in stale}
