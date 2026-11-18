"""Resolve actual source leaves and ordered message containers without truncation."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime

from agentlens.diagnoser.base import ApplicabilitySkipped, ExecutorFailure
from agentlens.domain.specialist_policy import canonical_digest, canonical_json
from agentlens.service.datasource.projection.evidence import (
    decode_message_container,
    messages,
    source,
)
from agentlens.service.datasource.projection.models import SourceReference
from agentlens.service.datasource.projection.registry import get_dedicated_projector
from agentlens.service.datasource.runtime import supports_specialist_runtime

_TOKEN = re.compile(r"(?:\.([A-Za-z_][A-Za-z0-9_-]*))|(?:\[(0|[1-9][0-9]{0,4})\])")


@dataclass(frozen=True)
class SourceLeaf:
    text: str
    source: SourceReference
    row: dict
    root_key: str
    root_value: object
    tokens: tuple[str | int, ...]


def _skip(detail: str, *, privacy: bool = False):
    raise ApplicabilitySkipped(
        ("privacy_redacted" if privacy else "missing_required_evidence") + ": " + detail
    )


def resolve_source_leaf(trace, reference: SourceReference) -> SourceLeaf:
    if (
        not isinstance(reference, SourceReference)
        or reference.trace_id != trace.trace_id
        or not reference.span_id
    ):
        raise ExecutorFailure("invalid_material_source", "专项材料来源无效")
    path = reference.field_path
    if not isinstance(path, str) or not 1 <= len(path) <= 512:
        raise ExecutorFailure("invalid_material_source", "专项来源路径无效")
    rows = [
        row
        for row in trace.spans
        if str(row.get("span_id") or row.get("spanID") or "") == reference.span_id
    ]
    if len(rows) != 1:
        raise ExecutorFailure("invalid_material_source", "专项来源 span 不唯一")
    row = rows[0]
    if row.get("trace_id") not in (None, "", trace.trace_id):
        raise ExecutorFailure("invalid_material_source", "专项来源 Trace 不匹配")
    keys = [
        key
        for key in row
        if isinstance(key, str) and (key == path or path.startswith((key + ".", key + "[")))
    ]
    if not keys:
        raise ExecutorFailure("invalid_material_source", "专项来源字段不存在")
    root_key = max(keys, key=len)
    suffix = path[len(root_key) :]
    tokens, position = [], 0
    for match in _TOKEN.finditer(suffix):
        if match.start() != position:
            raise ExecutorFailure("invalid_material_source", "专项来源路径不受支持")
        position = match.end()
        token = int(match[2]) if match[2] is not None else match[1]
        if isinstance(token, int) and token > 10000:
            raise ExecutorFailure("invalid_material_source", "专项来源下标越界")
        tokens.append(token)
    if position != len(suffix) or len(tokens) > 12:
        raise ExecutorFailure("invalid_material_source", "专项来源路径不受支持")
    value = row[root_key]
    if isinstance(value, str):
        if root_key.startswith("attributes.aivision.request.") and (
            tokens or root_key + ".encoding" in row
        ):
            if row.get(root_key + ".encoding") != "json":
                raise ExecutorFailure("invalid_material_source", "应用正文未声明 JSON 编码")
            try:
                value = json.loads(value)
            except ValueError:
                raise ExecutorFailure("material_decode_failed", "专项材料解码失败") from None
        elif tokens and root_key.startswith(
            (
                "attributes.langfuse.",
                "attributes.gen_ai.",
                "attributes.input.",
                "attributes.output.",
            )
        ):
            try:
                value = decode_message_container(
                    value, declared_sdk_container=root_key.startswith("attributes.langfuse.")
                )
            except ValueError:
                raise ExecutorFailure("material_decode_failed", "专项材料解码失败") from None
        elif tokens:
            raise ExecutorFailure("invalid_material_source", "未声明的来源容器编码")
    root_value = value
    try:
        for token in tokens:
            if isinstance(token, int) and isinstance(value, list):
                value = value[token]
            elif isinstance(token, str) and isinstance(value, dict):
                value = value[token]
            else:
                raise KeyError("unsupported source structure")
    except (KeyError, IndexError):
        raise ExecutorFailure("invalid_material_source", "专项来源实际 leaf 不存在") from None
    if not isinstance(value, str):
        raise ExecutorFailure("invalid_material_source", "专项来源必须是实际文本 leaf")
    return SourceLeaf(value, reference, row, root_key, root_value, tuple(tokens))


def _has_unsupported_parts(leaf: SourceLeaf) -> bool:
    value = leaf.root_value
    # Inspect actual sibling parts, not the mapper's text-only projection.
    for token in leaf.tokens:
        if isinstance(value, list):
            if all(isinstance(v, (str, dict)) for v in value) and any(
                isinstance(v, dict)
                and v.get("type")
                not in (
                    None,
                    "text",
                    "input_text",
                    "output_text",
                    "user",
                    "assistant",
                    "human",
                    "ai",
                    "system",
                    "tool",
                    "constructor",
                )
                for v in value
            ):
                return True
        value = value[token]
    return False


def _text_item(trace, value, name: str, *, limit: int) -> tuple[dict, list[SourceLeaf]]:
    if (
        value is None
        or not isinstance(value.content, str)
        or not value.content.strip()
        or not value.sources
    ):
        _skip("missing_required_" + name)
    leaves = [resolve_source_leaf(trace, reference) for reference in value.sources]
    if len({(leaf.source.span_id, leaf.source.field_path) for leaf in leaves}) != len(leaves):
        raise ExecutorFailure("invalid_material_source", "重复专项材料来源")
    if value.content != "\n".join(leaf.text for leaf in leaves):
        raise ExecutorFailure("invalid_material_source", "专项材料与实际 leaf 不一致")
    if any("[REDACTED]" in leaf.text for leaf in leaves):
        _skip("required material is redacted", privacy=True)
    if any(_has_unsupported_parts(leaf) for leaf in leaves):
        _skip("unsupported_required_evidence")
    if len(value.content.encode("utf-8")) > limit:
        _skip("unsupported_material_size")
    return {"content": value.content, "sources": [asdict(leaf.source) for leaf in leaves]}, leaves


def _history(trace, request_leaves: list[SourceLeaf]) -> dict:
    first = request_leaves[0]
    if any(
        (leaf.source.span_id, leaf.root_key) != (first.source.span_id, first.root_key)
        for leaf in request_leaves
    ):
        _skip("missing_required_history")
    try:
        items = messages(first.root_value, first.row, trace.trace_id, first.root_key)
    except (TypeError, ValueError):
        raise ExecutorFailure("material_decode_failed", "专项历史解码失败") from None
    current_sources = {(leaf.source.span_id, leaf.source.field_path) for leaf in request_leaves}
    boundaries = [index for index, item in enumerate(items) if item.role == "user"]
    if not boundaries:
        _skip("missing_required_history")
    boundary = boundaries[-1]
    current = items[boundary]
    if {(ref.span_id, ref.field_path) for ref in current.sources} != current_sources:
        _skip("missing_required_history")
    history = items[:boundary]
    if not any(item.role == "user" and item.content for item in history) or not any(
        item.role == "assistant" and item.content for item in history
    ):
        _skip("missing_required_history")
    if len(history) > 40:
        _skip("history_limit_exceeded")
    bundle = {}
    for index, item in enumerate(history):
        text, _ = _text_item(trace, item, "history", limit=8192)
        bundle[f"history:{index}"] = {**text, "role": item.role, "message_index": index}
    return bundle


def build_specialist_materials(trace, spec: dict) -> dict:
    projection = _require_projection(trace)
    bundle = {}
    final_limit = 1024 * 1024 if spec["builtin_key"] == "protected_content_exposure" else 32768
    bundle["final"], _ = _text_item(trace, projection.final_output, "final", limit=final_limit)
    if spec["builtin_key"] != "protected_content_exposure":
        bundle["request"], request_leaves = _text_item(
            trace, projection.current_request, "request", limit=32768
        )
        if spec["canonical_variant"] == "recorded_conversation":
            bundle.update(_history(trace, request_leaves))
    if spec["builtin_key"] == "response_language":
        policy = spec["specialist_policy"]
        bundle["policy:language"] = {
            "content": canonical_json(policy),
            "policy_reference": policy_reference(spec, "required_language/scope/allowed_terms"),
        }
    if spec["executor"] == "specialist_llm" and len(canonical_json(bundle).encode("utf-8")) > 98304:
        _skip("unsupported_material_size")
    return bundle


def _require_projection(trace):
    if trace.projection is None:
        if get_dedicated_projector(trace.runtime_type) is None:
            _skip("runtime_mapping_unavailable")
        raise ExecutorFailure("projection_failed", "专项 Trace 投影失败")
    return trace.projection


def _incomplete_output(text: str) -> bool:
    return re.search(r"\.\.\.\[truncated\]", text, re.IGNORECASE) is not None


def structured_output_material(trace) -> tuple[str, SourceReference]:
    """Use the whole application JSON body or one actual final text leaf."""
    projector = get_dedicated_projector(trace.runtime_type)
    path = "attributes.aivision.request.output"
    roots = (
        [row for row in trace.spans if row.get("parent_span_id") in (None, "", "0" * 16)]
        if projector and projector.canonical_runtime == "langchain"
        else []
    )
    if len(roots) == 1 and roots[0].get(path + ".encoding") == "json":
        row = roots[0]
        content = row.get(path)
        if not isinstance(content, str) or not content.strip():
            _skip("missing_required_final")
        reference = source(row, trace.trace_id, path)
        if (
            not reference.span_id
            or reference.field_path != path
            or row.get("trace_id") not in (None, "", trace.trace_id)
            or sum(other.get("span_id") == reference.span_id for other in trace.spans) != 1
        ):
            raise ExecutorFailure("invalid_material_source", "专项应用输出来源无效")
    else:
        projection = _require_projection(trace)
        _, leaves = _text_item(trace, projection.final_output, "final", limit=32768)
        if len(leaves) != 1:
            _skip("ambiguous_final_output")
        leaf = leaves[0]
        content, reference, row = leaf.text, leaf.source, leaf.row
        if projector and projector.canonical_runtime == "langchain" and len(roots) != 1:
            _skip("ambiguous_final_output")

    if "[REDACTED]" in content:
        _skip("required material is redacted", privacy=True)
    if len(content.encode("utf-8")) > 32768:
        _skip("unsupported_material_size")
    # Providers report length cutoffs on the source generation span, even when
    # a prefix happens to parse as a complete JSON document.
    finish = row.get("attributes.gen_ai.response.finish_reasons")
    if isinstance(finish, str):
        try:
            finish = json.loads(finish)
        except ValueError:
            pass
    reasons = finish if isinstance(finish, list) else [finish]
    if _incomplete_output(content) or any(
        reason in ("length", "max_tokens", "max_output_tokens", "content_filter", "cancelled")
        for reason in reasons
    ):
        _skip("unsupported_incomplete_final_output")
    return content, reference


def _tool_terminal(row, action, identity: str) -> tuple[str, str, str] | None:
    raw_name = row.get("attributes.agentic.tool.raw_name")
    if (raw_name or action.raw_name) != identity or not action.started_at or not action.ended_at:
        return None
    if action.ended_at < action.started_at or action.output_truncated:
        return None
    terminal_error = (
        action.status == "error"
        or bool(action.error_type)
        or row.get("attributes.langfuse.observation.level") in ("error", "ERROR")
    )
    output_source = action.field_sources.get("result")
    if output_source is None:
        return None
    path = output_source.field_path
    output = row.get(path)
    if not isinstance(output, str) or not output.strip() or len(output) > 32768:
        return None
    if "[REDACTED]" in output or _incomplete_output(output):
        return None

    if path == "attributes.langfuse.observation.output":
        try:
            message = json.loads(output)
        except (ValueError, RecursionError):
            return None
        if not isinstance(message, dict):
            return None
        call_id = message.get("tool_call_id")
        name = message.get("name")
        status = message.get("status")
        metadata_call_id = row.get("attributes.langfuse.observation.metadata.tool_call_id")
        if (
            message.get("type") not in ("tool", "ToolMessage")
            or "content" not in message
            or not isinstance(name, str)
            or not name
            or name not in (action.raw_name, raw_name)
            or not isinstance(call_id, str)
            or not call_id.strip()
            or (action.call_id is not None and action.call_id != call_id)
            or (metadata_call_id is not None and metadata_call_id != call_id)
            or status not in ("success", "error")
        ):
            return None
        if (status == "success" and terminal_error) or (
            status == "error" and action.status == "success"
        ):
            status = "ambiguous"
        return call_id, status, path + ".status"

    if (
        path == "attributes.gen_ai.tool.call.result"
        and row.get("attributes.gen_ai.operation.name") == "execute_tool"
        and row.get("attributes.gen_ai.tool.name") == action.raw_name
        and action.call_id
        and output.strip() != "null"
        and action.status in ("success", "error")
        and row.get("status_code") in (1, 2, "1", "2")
        and not isinstance(row.get("status_code"), bool)
    ):
        # MCP transport may succeed even when the tool returns a protocol-level error.
        result = action.result
        mcp_error = (
            isinstance(result, dict)
            and result.get("isError") is True
            and isinstance(result.get("content"), list)
            and result.keys() <= {"content", "isError", "structuredContent", "_meta"}
        )
        status = (
            "ambiguous"
            if action.status == "success" and (terminal_error or mcp_error)
            else action.status
        )
        return action.call_id, status, path
    return None


def _tool_attempt_groups(attempts):
    """A logical tool call ID may be reused by sequential physical attempts."""
    end = None
    group = {}
    for started, ended, status, reference in sorted(attempts, key=lambda attempt: attempt[:2]):
        if end is not None and started > end:
            yield group
            group = {}
            end = None
        end = ended if end is None else max(end, ended)
        group.setdefault(status, reference)
    yield group


def required_tool_completion(trace, tool_name: str) -> SourceReference:
    """Prove a positive tool result. Span presence is never evidence of absence."""
    if not supports_specialist_runtime(trace.runtime_type, key="required_tool_execution"):
        _skip("unsupported_tool_runtime")
    projection = _require_projection(trace)
    spans = {str(row.get("span_id") or ""): row for row in trace.spans}
    counts = Counter(str(row.get("span_id") or "") for row in trace.spans)
    terminals: dict[str, list[tuple[datetime, datetime, str, SourceReference]]] = defaultdict(list)
    for action in projection.tool_actions:
        span_id = action.source_span_id
        if not span_id or counts[span_id] != 1:
            continue
        row = spans[span_id]
        if row.get("trace_id") not in (None, "", trace.trace_id):
            raise ExecutorFailure("invalid_material_source", "专项工具结果来源无效")
        terminal = _tool_terminal(row, action, tool_name)
        if terminal is not None:
            call_id, status, path = terminal
            reference = source(row, trace.trace_id, path, call_id)
            terminals[call_id].append((action.started_at, action.ended_at, status, reference))

    saw_failure = False
    for attempts in terminals.values():
        for results in _tool_attempt_groups(attempts):
            if len(results) != 1:
                continue
            if "success" in results:
                return results["success"]
            saw_failure = saw_failure or "error" in results
    _skip(
        "observed_tool_failure_without_confirmed_success"
        if saw_failure
        else "missing_required_tool_result"
    )


def policy_reference(spec: dict, field: str) -> dict:
    return {
        "kind": "configured_check_policy",
        "definition_digest": spec["definition_digest"],
        "policy_digest": canonical_digest(spec["specialist_policy"]),
        "policy_field": field,
    }
