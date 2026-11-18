"""Coding protocol adapters: raw rows → CodingProjection.

Shared parsing extracts user-facing input/output, tool actions, and basic file/verification
facts from gen_ai.* semantic attributes. Every supported Coding runtime
produces one CodingProjection per source trace_id.

Each fact links to a source span_id. Missing identity stays None.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any, Literal

from agentlens.service.datasource.otel_mapper import (
    build_usage_details,
    resolve_semantic_value,
)
from agentlens.service.datasource.projection.models import ExecutionStep, TraceOutcome, TraceStep
from agentlens.service.datasource.projection.projectors.coding import (
    tool_actions as coding_tool_actions,
)
from agentlens.service.datasource.projection.projectors.coding.models import (
    CodingProjection,
    FileMutation,
    SubagentExecution,
    ToolAction,
    VerificationAction,
)

_CANCELLED_TURN_NAME_RE = re.compile(r"\(\s*cancelled\s*\)", re.IGNORECASE)


def _attr(row: dict[str, Any], key: str) -> Any:
    return row.get(f"attributes.{key}")


def _parse_role_messages(value: Any, role: str) -> str | None:
    """Read only the requested message role; never promote another role."""
    from agentlens.service.datasource.projection.evidence import (
        current_message,
        decode_message_container,
        messages,
    )

    value = decode_message_container(value)
    if isinstance(value, str):
        return value if value.strip() else None
    item = current_message(messages(value, {}, "", "messages"), role)
    return item.content if item else None


def _parse_ts(ts: Any) -> datetime | None:
    """Parse a timestamp that may be nanoseconds (int/str) or ISO-8601."""
    if ts is None or ts == "":
        return None
    try:
        return datetime.fromtimestamp(int(ts) / 1e9, tz=UTC)
    except (ValueError, TypeError, OSError):
        pass
    try:
        return datetime.fromisoformat(str(ts))
    except (ValueError, TypeError):
        pass
    return None


def _span_time_sort_key(value: Any) -> tuple[int, str]:
    parsed = _parse_ts(value)
    if parsed is None:
        return (1, "")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    else:
        parsed = parsed.astimezone(UTC)
    return (0, parsed.isoformat())


def _span_sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
    """Build the deterministic working order required by Coding projection."""
    normalized = json.dumps(
        row,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return (
        _span_time_sort_key(row.get("start_time_unix_nano")),
        _span_time_sort_key(row.get("end_time_unix_nano")),
        str(row.get("span_id") or ""),
        str(row.get("parent_span_id") or ""),
        str(row.get("name") or ""),
        normalized,
    )


def _ordered_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return a sorted working copy without changing the raw Trace rows."""
    return sorted(rows, key=_span_sort_key)


def _is_chat_span(row: dict[str, Any]) -> bool:
    """CHAT turn span: gen_ai.span.kind=CHAT preferred.

    Fallback: SERVER kind + name contains 'Turn' (for traces without
    gen_ai.span.kind). INTERNAL or CLIENT alone is NOT sufficient.
    """
    ai_kind = _attr(row, "gen_ai.span.kind")
    if ai_kind == "CHAT":
        return True
    kind = str(row.get("kind") or "")
    name = str(row.get("name") or "")
    if kind in ("SPAN_KIND_SERVER", "2") and "Turn" in name:
        return True
    return False


def _is_llm_span(row: dict[str, Any]) -> bool:
    return str(_attr(row, "gen_ai.span.kind") or "") == "LLM"


def _is_tool_span(row: dict[str, Any]) -> bool:
    """TOOL span: gen_ai.span.kind=TOOL preferred.

    SPAN_KIND_INTERNAL alone is NEVER sufficient — it includes LLM,
    wrapper, control, and lifecycle spans.

    Fallbacks require cross-evidence (call_id or arguments/result),
    not just a name prefix.
    """
    ai_kind = str(_attr(row, "gen_ai.span.kind") or "")
    if ai_kind:
        if ai_kind != "TOOL":
            return False
        op = _attr(row, "gen_ai.operation.name")
        if op is not None and op != "execute_tool":
            return False
        return True
    if _attr(row, "gen_ai.tool.call.id"):
        return True
    name = str(row.get("name") or "")
    if name.startswith("tool ") and (
        _attr(row, "gen_ai.tool.call.arguments") is not None
        or _attr(row, "gen_ai.tool.call.result") is not None
    ):
        return True
    return False


def _maybe_parse_json(value: Any) -> Any:
    if isinstance(value, str) and value.strip():
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return value
    return value


def _span_latency_ms(row: dict[str, Any]) -> int | None:
    raw_duration: object = _attr(row, "agentic.tool.duration_ms")
    if isinstance(raw_duration, (str, int, float)):
        try:
            return int(raw_duration)
        except (ValueError, TypeError):
            pass
    started = _parse_ts(row.get("start_time_unix_nano"))
    ended = _parse_ts(row.get("end_time_unix_nano"))
    if started and ended:
        return round((ended - started).total_seconds() * 1000)
    return None


def _llm_model(row: dict[str, Any]) -> str | None:
    model = resolve_semantic_value(row, "model")
    if model in (None, ""):
        return None
    value = str(model).strip()
    return value or None


def _extract_tool_action(row: dict[str, Any]) -> ToolAction | None:
    if not _is_tool_span(row):
        return None
    raw_call_id = _attr(row, "gen_ai.tool.call.id")
    call_id = str(raw_call_id) if raw_call_id not in (None, "") else None
    raw_name = _attr(row, "gen_ai.tool.name") or str(row.get("name") or "")
    tool_name = raw_name
    if tool_name.startswith("tool "):
        tool_name = tool_name[5:]
    args = _maybe_parse_json(_attr(row, "gen_ai.tool.call.arguments"))
    result = _maybe_parse_json(_attr(row, "gen_ai.tool.call.result"))
    if call_id is None and tool_name is None and args is None:
        return None
    span_id = str(row.get("span_id") or "")
    raw_status_code: object = row.get("status_code")
    try:
        status_code = (
            int(raw_status_code) if isinstance(raw_status_code, (str, int, float)) else None
        )
    except (TypeError, ValueError):
        status_code = None
    status: Literal["success", "error", "unknown"]
    if status_code == 2:
        status = "error"
    elif status_code == 1:
        status = "success"
    else:
        status = "unknown"
    error_type = _attr(row, "error.type") or _attr(row, "gen_ai.tool.error.type")
    op_name = _attr(row, "gen_ai.operation.name")
    tool_type = _attr(row, "agentic.tool.type")
    input_trunc = _attr(row, "agentic.tool.input.truncated")
    output_trunc = _attr(row, "agentic.tool.output.truncated")
    started = _parse_ts(row.get("start_time_unix_nano"))
    ended = _parse_ts(row.get("end_time_unix_nano"))
    duration_ms = _span_latency_ms(row)
    from agentlens.service.datasource.projection.evidence import source

    return ToolAction(
        action_id=call_id or span_id,
        source_span_id=span_id,
        call_id=call_id,
        name=tool_name or None,
        raw_name=raw_name or None,
        tool_type=tool_type,
        operation_name=op_name,
        identity_source="tool_call" if call_id else "span",
        arguments=args,
        result=result,
        status=status,
        error_type=error_type,
        duration_ms=duration_ms,
        input_truncated=bool(input_trunc) if input_trunc is not None else None,
        output_truncated=bool(output_trunc) if output_trunc is not None else None,
        started_at=started,
        ended_at=ended,
        field_sources={
            field: source(row, str(row.get("trace_id") or ""), key, call_id)
            for field, key in {
                "arguments": "attributes.gen_ai.tool.call.arguments",
                "result": "attributes.gen_ai.tool.call.result",
                "status": "status_code",
                "name": "attributes.gen_ai.tool.name",
            }.items()
            if key in row
        },
    )


def _classified_action(action: ToolAction) -> coding_tool_actions.ToolAction:
    return coding_tool_actions.classify_tool_action(
        step_index=0,
        tool_name=action.name or "",
        arguments=action.arguments,
        result=action.result,
        status=action.status,
    )


def _derive_file_mutation(action: ToolAction) -> FileMutation | None:
    classified = _classified_action(action)
    if not classified.is_code_change:
        return None
    path = next((path for path in classified.paths if coding_tool_actions.is_code_path(path)), None)
    if path is None:
        return None
    source = f"{action.name or ''} {classified.command or ''}".lower()
    operation: Literal["edit", "write", "patch", "delete", "create"]
    if "patch" in source or "git apply" in source:
        operation = "patch"
    elif "delete" in source or " rm " in f" {source} ":
        operation = "delete"
    elif "create" in source or "touch" in source:
        operation = "create"
    elif "edit" in source or "replace" in source:
        operation = "edit"
    else:
        operation = "write"
    exact_content = classified.old_content is not None and classified.new_content is not None
    return FileMutation(
        source_action_id=action.action_id,
        source_span_id=action.source_span_id,
        path=path,
        operation=operation,
        before=classified.old_content,
        after=classified.new_content,
        evidence_level="exact" if exact_content else "file_only",
    )


def _derive_verification(action: ToolAction) -> VerificationAction | None:
    classified = _classified_action(action)
    if "test" not in classified.operations:
        return None
    return VerificationAction(
        source_action_id=action.action_id,
        source_span_id=action.source_span_id,
        command=classified.command or action.name,
        result=(
            "passed"
            if classified.verification_succeeded is True
            and (
                classified.has_verification_result
                or coding_tool_actions.verification_result_is_unmasked(classified.command)
            )
            else "failed"
            if classified.verification_succeeded is False
            and (
                classified.has_verification_result
                or coding_tool_actions.verification_result_is_unmasked(classified.command)
            )
            else "unknown"
        ),
        started_at=action.started_at,
        ended_at=action.ended_at,
        field_sources=action.field_sources,
    )


def derive_file_mutations(actions: list[ToolAction]) -> list[FileMutation]:
    return [mutation for action in actions if (mutation := _derive_file_mutation(action))]


def derive_verification_actions(actions: list[ToolAction]) -> list[VerificationAction]:
    return [verification for action in actions if (verification := _derive_verification(action))]


def _extract_subagent(row: dict[str, Any]) -> SubagentExecution | None:
    sid = _attr(row, "agentic.subagent.id")
    if sid is None:
        return None
    return SubagentExecution(
        source_span_id=str(row.get("span_id") or ""),
        subagent_id=str(sid),
        description=_attr(row, "agentic.subagent.description"),
        started_at=_parse_ts(row.get("start_time_unix_nano")),
        ended_at=_parse_ts(row.get("end_time_unix_nano")),
    )


def _extract_primary_chat_content(
    rows: list[dict[str, Any]], turn_span_id: str
) -> tuple[str, str, datetime | None, str | None, str | None]:
    user_query: str | None = None
    user_query_span_id: str | None = None
    message_query: tuple[str, str | None] | None = None
    delta_query: tuple[str, str | None] | None = None
    agent_output: str | None = None
    agent_output_span_id: str | None = None
    turn_output: tuple[str, str, datetime, int] | None = None
    llm_outputs: list[tuple[str, str, datetime, int]] = []
    started: datetime | None = None
    for row_index, row in enumerate(rows):
        if str(row.get("span_id") or "") != turn_span_id and _is_chat_span(row):
            continue
        if _is_llm_span(row) or _is_chat_span(row):
            inp = _parse_role_messages(_attr(row, "gen_ai.input.messages"), "user")
            out = _parse_role_messages(_attr(row, "gen_ai.output.messages"), "assistant")
            span_id = str(row.get("span_id") or "") or None
            if inp and message_query is None:
                message_query = (inp, span_id)
            delta = _attr(row, "agentic.input.delta")
            if isinstance(delta, str) and delta.strip() and delta_query is None:
                delta_query = (delta.strip(), span_id)
            if out and not agent_output:
                candidate_time = _parse_ts(row.get("start_time_unix_nano"))
                if candidate_time is None:
                    candidate_time = datetime.min.replace(tzinfo=UTC)
                elif candidate_time.tzinfo is None:
                    candidate_time = candidate_time.replace(tzinfo=UTC)
                else:
                    candidate_time = candidate_time.astimezone(UTC)
                candidate = (out, span_id or "", candidate_time, row_index)
                if str(row.get("span_id") or "") == turn_span_id and _is_chat_span(row):
                    turn_output = candidate
                elif _is_llm_span(row):
                    llm_outputs.append(candidate)
        ts = _parse_ts(row.get("start_time_unix_nano"))
        if ts and (started is None or ts < started):
            started = ts
    selected_query = message_query or delta_query
    if selected_query is not None:
        user_query, user_query_span_id = selected_query
    selected_output = turn_output or (
        max(llm_outputs, key=lambda candidate: (candidate[2], candidate[3]))
        if llm_outputs
        else None
    )
    if selected_output is not None:
        agent_output, agent_output_span_id, _, _ = selected_output
    return (
        user_query or "",
        agent_output or "",
        started,
        user_query_span_id,
        agent_output_span_id,
    )


def _build_steps(rows: list[dict[str, Any]]) -> list[TraceStep]:
    steps: list[TraceStep] = []
    for i, row in enumerate(rows):
        if not (_is_llm_span(row) or _is_tool_span(row)):
            continue
        is_llm = _is_llm_span(row)
        steps.append(
            ExecutionStep(
                ref=str(row.get("span_id") or ""),
                name=str(row.get("name") or ""),
                type="LLM" if is_llm else "TOOL",
                status="OK" if row.get("status_code") != 2 else "ERROR",
                latency_ms=_span_latency_ms(row),
                input=(
                    _attr(row, "gen_ai.input.messages")
                    if is_llm
                    else _attr(row, "gen_ai.tool.call.arguments")
                ),
                output=(
                    _attr(row, "gen_ai.output.messages")
                    if is_llm
                    else _attr(row, "gen_ai.tool.call.result")
                ),
                index=i,
                model=_llm_model(row) if is_llm else None,
                usage=build_usage_details(row) if is_llm else None,
            )
        )
    return steps


def _is_explicitly_cancelled_turn(row: dict[str, Any]) -> bool:
    """Recognize runtime-confirmed user cancellation on a CHAT turn only."""
    if not _is_chat_span(row):
        return False
    if _CANCELLED_TURN_NAME_RE.search(str(row.get("name") or "")):
        return True
    if str(_attr(row, "error.type") or "").lower() == "cancelled":
        return True
    # DSH confirms user cancellation as an aborted CHAT turn. A tool abort
    # or an unrelated runtime's error is not evidence of user interruption.
    if (
        str(_attr(row, "agentic.runtime.name") or "").lower() == "dsh"
        and str(_attr(row, "error.type") or "").lower() == "aborted"
    ):
        return True
    status_message = row.get("status_message") or row.get("status.message")
    return "cancelled" in str(status_message or "").lower()


def _build_outcome(rows: list[dict[str, Any]]) -> TraceOutcome:
    has_error = any(row.get("status_code") == 2 for row in rows)
    interrupted = any(_is_explicitly_cancelled_turn(row) for row in rows)
    latency_ms: int | None = None
    starts = [t for t in (_parse_ts(row.get("start_time_unix_nano")) for row in rows) if t]
    ends = [t for t in (_parse_ts(row.get("end_time_unix_nano")) for row in rows) if t]
    if starts and ends:
        delta = (max(ends) - min(starts)).total_seconds()
        latency_ms = round(delta * 1000)
    return TraceOutcome(
        has_error=has_error,
        result_state="ERROR" if has_error else "SUCCESS",
        total_latency_ms=latency_ms,
        interrupted=interrupted,
    )


def _project_turn(
    rows: list[dict[str, Any]],
    turn_span_id: str,
    trace_id: str,
) -> CodingProjection:
    (
        user_query,
        agent_output,
        started_at,
        user_query_span_id,
        agent_output_span_id,
    ) = _extract_primary_chat_content(rows, turn_span_id)
    steps = _build_steps(rows)
    turn_name = next(
        (
            str(row.get("name") or "CHAT")
            for row in rows
            if str(row.get("span_id") or "") == turn_span_id
        ),
        "CHAT",
    )
    tool_actions: list[ToolAction] = []
    subagents: list[SubagentExecution] = []
    for row in rows:
        sub = _extract_subagent(row)
        if sub:
            subagents.append(sub)
        if not _is_tool_span(row):
            continue
        action = _extract_tool_action(row)
        if action:
            tool_actions.append(action)
    projection = CodingProjection(
        timestamp=started_at.isoformat() if started_at else None,
        user_query=user_query,
        agent_output=agent_output,
        outcome=_build_outcome(rows),
        labels={},
        node_seq=[turn_name, *(step.name or step.type for step in steps)],
        steps=steps,
        user_query_span_id=user_query_span_id,
        agent_output_span_id=agent_output_span_id,
        tool_actions=tool_actions,
        subagents=subagents,
    )
    from agentlens.service.datasource.projection.evidence import (
        INPUT_KEYS,
        OUTPUT_KEYS,
        current_message,
        field_messages,
        message_field,
        source,
        supplied_context,
        text_parts,
    )
    from agentlens.service.datasource.projection.models import TextEvidence

    # These compatibility aliases must describe the same current evidence as the
    # sourced facts. The older primary scan must not leak a historical fallback.
    projection.user_query = ""
    projection.agent_output = ""
    projection.user_query_span_id = None
    projection.agent_output_span_id = None
    input_rows = sorted(
        [row for row in rows if _is_chat_span(row) or _is_llm_span(row)],
        key=lambda row: str(row.get("span_id") or "") != turn_span_id,
    )
    for row in input_rows:
        inputs = field_messages(row, trace_id, INPUT_KEYS)
        request = current_message(inputs, "user")
        raw_input, input_path = message_field(row, INPUT_KEYS)
        if request is not None:
            projection.conversation_history = inputs[: inputs.index(request)] or None
            if request.content:
                projection.current_request = TextEvidence(request.content, request.sources)
            # The current role boundary is authoritative even without text.
            break
        if isinstance(raw_input, str) and input_path:
            projection.current_request = text_parts(raw_input, row, trace_id, input_path)
        elif (
            isinstance(_attr(row, "agentic.input.delta"), str)
            and _attr(row, "agentic.input.delta").strip()
        ):
            projection.current_request = TextEvidence(
                _attr(row, "agentic.input.delta"),
                [source(row, trace_id, "attributes.agentic.input.delta")],
            )
        if projection.current_request:
            break
    if projection.current_request:
        projection.user_query = projection.current_request.content
        projection.user_query_span_id = projection.current_request.sources[0].span_id
    # CHAT output is authoritative; otherwise inspect the latest generation,
    # including a tool-call-only assistant that has no final visible text.
    output_rows = [
        row for row in rows if str(row.get("span_id") or "") == turn_span_id and _is_chat_span(row)
    ]
    output_rows += list(reversed([row for row in rows if _is_llm_span(row)]))
    for row in output_rows:
        output = current_message(field_messages(row, trace_id, OUTPUT_KEYS), "assistant")
        if output is not None:
            if output.content:
                projection.final_output = TextEvidence(output.content, output.sources)
            break
        raw_output, output_path = message_field(row, OUTPUT_KEYS)
        if isinstance(raw_output, str) and output_path:
            projection.final_output = text_parts(raw_output, row, trace_id, output_path)
            break
    if projection.final_output:
        projection.agent_output = projection.final_output.content
        projection.agent_output_span_id = projection.final_output.sources[0].span_id
    contexts = [
        context
        for row in rows
        if _is_llm_span(row)
        for context in supplied_context(row, trace_id, rows)
    ]
    projection.provided_context = [
        context for context in contexts if context.kind != "instruction"
    ] or None
    projection.model_instructions = [
        context for context in contexts if context.kind == "instruction"
    ] or None
    projection.observed_user_interruption = True if projection.outcome.interrupted else None
    return projection


def project_coding_raw(
    raw_rows: list[dict[str, Any]],
    trace_id: str,
) -> list[CodingProjection]:
    """Project one source trace, which is contractually one Coding turn.

    Multiple CHAT spans mean the source violated the trace-per-turn contract;
    do not silently combine their query/output with one shared action list.
    """
    ordered_rows = _ordered_rows(raw_rows)
    turn_span_ids = [
        str(row.get("span_id") or "")
        for row in ordered_rows
        if _is_chat_span(row) and row.get("span_id")
    ]
    if len(turn_span_ids) > 1:
        return []
    if turn_span_ids:
        return [_project_turn(ordered_rows, turn_span_ids[0], trace_id)]
    fallback = str(ordered_rows[0].get("span_id") or trace_id) if ordered_rows else trace_id
    return [_project_turn(ordered_rows, fallback, trace_id)]
