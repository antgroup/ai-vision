"""Parse documented SDK message shapes without inventing missing semantics."""

from __future__ import annotations

import json
import re
from typing import Any

from agentlens.service.datasource.projection.models import (
    ContextEvidence,
    MessageEvidence,
    SourceReference,
    TextEvidence,
)

INPUT_KEYS = (
    "attributes.langfuse.observation.input",
    "attributes.gen_ai.input.messages",
    "attributes.input.value",
)
OUTPUT_KEYS = (
    "attributes.langfuse.observation.output",
    "attributes.gen_ai.output.messages",
    "attributes.output.value",
)
_ROLES = {"human": "user", "ai": "assistant", "function": "tool"}


def decode(value: Any) -> Any:
    """Decode an explicitly selected SDK metadata/container, never a body field."""
    if not isinstance(value, str):
        return value
    if not value.strip():
        return None
    if value == "[REDACTED]":
        return value
    try:
        return json.loads(value)
    except ValueError as exc:
        if value.lstrip().startswith(("{", "[", '"')):
            raise ValueError("invalid serialized message input/output") from exc
        return value


def source(row: dict, trace_id: str, path: str, call_id: str | None = None) -> SourceReference:
    # Ingest normalization may add a gen_ai alias; report its recorded original
    # field rather than falsely attributing that alias to the SDK.
    aliases = decode(row.get("attributes.aivision.normalization.sources"))
    if isinstance(aliases, dict):
        for key, original in aliases.items():
            alias = f"attributes.{key}"
            if isinstance(original, str) and (
                path == alias or path.startswith((alias + "[", alias + "."))
            ):
                path = f"attributes.{original}" + path[len(alias) :]
                break
    return SourceReference(trace_id, str(row.get("span_id") or "") or None, path, call_id)


def select_field(row: dict, keys: tuple[str, ...]) -> tuple[Any, str | None]:
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key], key
    return None, None


def decode_message_container(value: Any, *, declared_sdk_container: bool = False) -> Any:
    """Decode SDK message envelopes while keeping legacy literal bodies exact.

    A role/message envelope identifies the structured protocol. Application body
    fields never pass through this function; their JSON-looking text is literal.
    """
    if not isinstance(value, str):
        return value
    try:
        parsed = json.loads(value)
    except ValueError as exc:
        declared = re.match(
            r'\s*(?:\[\s*\{|\{\s*"(?:messages|role|type|data|kwargs|__nv_pydantic__)"\s*:)', value
        )
        if declared:
            raise ValueError("invalid serialized message input/output") from exc
        return value
    envelope_keys = {"role", "messages", "parts", "data", "kwargs", "__nv_pydantic__"}

    def envelope(item):
        return isinstance(item, dict) and (
            bool(envelope_keys.intersection(item))
            or (
                isinstance(item.get("type"), str)
                and item["type"]
                in {"user", "human", "assistant", "ai", "system", "tool", "function", "constructor"}
            )
        )

    if (
        (declared_sdk_container and isinstance(parsed, (dict, list)))
        or envelope(parsed)
        or (isinstance(parsed, list) and (not parsed or any(envelope(item) for item in parsed)))
    ):
        return parsed
    return value


def message_field(row: dict, keys: tuple[str, ...]) -> tuple[Any, str | None]:
    value, key = select_field(row, keys)
    original = source(row, "", key).field_path if key else ""
    return decode_message_container(
        value, declared_sdk_container=original.startswith("attributes.langfuse.")
    ), key


def text_parts(
    value: Any, row: dict, trace_id: str, path: str, call_id: str | None = None
) -> TextEvidence | None:
    if isinstance(value, str):
        return (
            TextEvidence(value, [source(row, trace_id, path, call_id)]) if value.strip() else None
        )
    if not isinstance(value, list):
        return None
    parts, refs = [], []
    for index, block in enumerate(value):
        if isinstance(block, str):
            text, location = block, f"{path}[{index}]"
        elif isinstance(block, dict) and block.get("type") in (
            None,
            "text",
            "input_text",
            "output_text",
        ):
            key = "text" if "text" in block else "content"
            text, location = block.get(key), f"{path}[{index}].{key}"
        else:
            continue
        if isinstance(text, str) and text.strip():
            parts.append(text)
            refs.append(source(row, trace_id, location, call_id))
    return TextEvidence("\n".join(parts), refs) if parts else None


def messages(value: Any, row: dict, trace_id: str, path: str) -> list[MessageEvidence]:
    """Langfuse, LangChain data/constructor and Relay parts/pydantic envelopes."""
    if isinstance(value, dict) and isinstance(value.get("messages"), list):
        return messages(value["messages"], row, trace_id, path + ".messages")
    if isinstance(value, list):
        result = []
        for index, item in enumerate(value):
            result.extend(messages(item, row, trace_id, f"{path}[{index}]"))
        return result
    if not isinstance(value, dict):
        return []
    role = _ROLES.get(value.get("role", value.get("type")), value.get("role", value.get("type")))
    roles = {"user", "assistant", "system", "tool"}

    def nested_messages(key, inherited_role):
        nested = value[key]
        nested_role = _ROLES.get(
            nested.get("role", nested.get("type")), nested.get("role", nested.get("type"))
        )
        if inherited_role in roles and nested_role not in roles:
            # The role belongs to the SDK envelope, not to its body kwargs/data.
            nested = {**nested, "role": inherited_role}
        return messages(nested, row, trace_id, path + "." + key)

    if isinstance(value.get("data"), dict):
        return nested_messages("data", role)
    if value.get("type") == "constructor" and isinstance(value.get("kwargs"), dict):
        identifier = value.get("id")
        class_name = identifier[-1] if isinstance(identifier, list) and identifier else None
        constructor_roles = {
            "HumanMessage": "user",
            "AIMessage": "assistant",
            "SystemMessage": "system",
            "ToolMessage": "tool",
            "FunctionMessage": "tool",
        }
        return nested_messages("kwargs", constructor_roles.get(class_name))
    if role not in {"user", "assistant", "system", "tool"}:
        return []
    call_id = value.get("tool_call_id")
    call_id = str(call_id) if call_id not in (None, "") else None
    if isinstance(value.get("parts"), list):
        responses = [
            (i, p)
            for i, p in enumerate(value["parts"])
            if isinstance(p, dict) and p.get("type") == "tool_call_response"
        ]
        if len(responses) == 1:
            index, response = responses[0]
            call_id = str(response.get("id") or "") or None
            body = response.get("response")
            if isinstance(body, (dict, list)):
                body = json.dumps(body, ensure_ascii=False, allow_nan=False)
            evidence = text_parts(body, row, trace_id, f"{path}.parts[{index}].response", call_id)
        else:
            evidence = text_parts(value["parts"], row, trace_id, path + ".parts", call_id)
    else:
        content = value.get("content")
        # Bound function schemas masquerading as tool messages are not ToolMessages.
        if (
            role == "tool"
            and not call_id
            and isinstance(content, dict)
            and content.get("type") == "function"
        ):
            return []
        evidence = text_parts(content, row, trace_id, path + ".content", call_id)
    # Keep role identity even when the latest message has only images/reasoning/
    # tool calls. Filtering here would promote an earlier message to current.
    if evidence:
        return [MessageEvidence(evidence.content, evidence.sources, role)]
    body_path = path + (".parts" if "parts" in value else ".content" if "content" in value else "")
    return [MessageEvidence(None, [source(row, trace_id, body_path, call_id)], role)]


def field_messages(row: dict, trace_id: str, keys: tuple[str, ...]) -> list[MessageEvidence]:
    value, key = message_field(row, keys)
    return messages(value, row, trace_id, key) if key else []


def current_message(items: list[MessageEvidence], role: str) -> MessageEvidence | None:
    """Latest observed role boundary, including messages without visible text."""
    return next((item for item in reversed(items) if item.role == role), None)


def supplied_context(row: dict, trace_id: str, tool_rows: list[dict]) -> list[ContextEvidence]:
    result = []
    for message in field_messages(row, trace_id, INPUT_KEYS):
        if message.role not in {"tool", "system"} or not message.content:
            continue
        producer = None
        call_id = message.sources[0].call_id
        if call_id:
            matches = [
                tool
                for tool in tool_rows
                if str(tool.get("attributes.gen_ai.tool.call.id") or "") == call_id
            ]
            if len(matches) == 1:
                tool = matches[0]
                # Identity plus observed ordering is required for producer attribution.
                from agentlens.service.datasource.projection.projectors.coding.adapter import (
                    _parse_ts,
                )

                ended, started = (
                    _parse_ts(tool.get("end_time_unix_nano")),
                    _parse_ts(row.get("start_time_unix_nano")),
                )
                try:
                    precedes = int(tool.get("end_time_unix_nano")) <= int(
                        row.get("start_time_unix_nano")
                    )
                except (TypeError, ValueError):
                    precedes = ended is not None and started is not None and ended <= started
                if precedes:
                    output, field = select_field(
                        tool,
                        (
                            "attributes.gen_ai.tool.call.result",
                            "attributes.langfuse.observation.output",
                        ),
                    )
                    output_text = (
                        output
                        if isinstance(output, str)
                        else json.dumps(output, ensure_ascii=False, allow_nan=False)
                    )
                    if field and output_text == message.content:
                        producer = source(tool, trace_id, field, call_id)
        result.append(
            ContextEvidence(
                message.content,
                message.sources,
                "instruction" if message.role == "system" else "tool_result",
                producer,
            )
        )
    return result
