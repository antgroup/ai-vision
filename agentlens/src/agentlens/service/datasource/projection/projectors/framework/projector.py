"""LangChain/LangGraph/Deep Agents actual Langfuse and Relay SDK rows."""

from __future__ import annotations

import json
from typing import Any

from agentlens.service.datasource.otel_mapper import build_observation_type
from agentlens.service.datasource.projection.contracts import ProjectionInputError, Projector
from agentlens.service.datasource.projection.evidence import (
    INPUT_KEYS,
    OUTPUT_KEYS,
    current_message,
    decode,
    decode_message_container,
    field_messages,
    select_field,
    source,
    supplied_context,
    text_parts,
)
from agentlens.service.datasource.projection.models import ContextEvidence, TextEvidence
from agentlens.service.datasource.projection.projectors.coding.adapter import (
    _build_outcome,
    _build_steps,
    _extract_tool_action,
    _ordered_rows,
    _parse_ts,
)
from agentlens.service.datasource.projection.projectors.coding.projector import (
    CodingProjector,
    _resolve_var,
)
from agentlens.service.datasource.projection.projectors.framework.models import FrameworkProjection


class FrameworkProjector(Projector):
    canonical_runtime = "langchain"
    available_vars = {
        **CodingProjector.available_vars,
        "history": "当前请求之前实际提供的消息",
        "provided_context": "生成输入中实际提供的工具资料，含来源",
        "current_request": "有来源的当前请求",
        "final_output": "有来源的最终输出",
    }

    def project(self, raw_rows: list[dict[str, Any]], trace_id: str) -> FrameworkProjection | None:
        rows = _ordered_rows(raw_rows)
        if not rows:
            return None
        roots = [
            row
            for row in rows
            if not row.get("parent_span_id") or row.get("parent_span_id") == "0" * 16
        ]
        if len(roots) != 1:
            raise ProjectionInputError(
                "ambiguous framework request root",
                cause="framework_root_missing" if not roots else "framework_root_ambiguous",
            )
        root = roots[0]
        normalized = []
        for row in rows:
            # A copy is only the working projection adapter; Trace.spans stays exact.
            kind = build_observation_type(row)
            mapped = {"GENERATION": "LLM", "TOOL": "TOOL", "RETRIEVER": "RETRIEVER"}.get(kind)
            normalized.append(
                {**row, **({"attributes.gen_ai.span.kind": mapped} if mapped else {})}
            )
        tool_rows = [row for row in normalized if row.get("attributes.gen_ai.span.kind") == "TOOL"]
        tools = []
        for row in tool_rows:
            inputs, input_path = select_field(
                row,
                (
                    "attributes.gen_ai.tool.call.arguments",
                    "attributes.langfuse.observation.metadata.inputs",
                    "attributes.langfuse.observation.input",
                ),
            )
            outputs, output_path = select_field(
                row,
                ("attributes.gen_ai.tool.call.result", "attributes.langfuse.observation.output"),
            )
            action = _extract_tool_action(
                {
                    **row,
                    "attributes.gen_ai.tool.call.arguments": inputs,
                    "attributes.gen_ai.tool.call.result": outputs,
                    "attributes.gen_ai.operation.name": "execute_tool",
                }
            )
            if action:
                from dataclasses import replace

                refs = dict(action.field_sources)
                for field, path in (("arguments", input_path), ("result", output_path)):
                    if path:
                        refs[field] = source(row, trace_id, path, action.call_id)
                    else:
                        refs.pop(field, None)
                action = replace(action, field_sources=refs)
                tools.append(action)
        # Prefer application-owned current input/output, not accumulated chain state.
        request_value, request_path = self._root_field(
            root,
            "attributes.aivision.request.input",
            ("attributes.langfuse.trace.input", *INPUT_KEYS),
        )
        request, request_observed, history = self._root_text(
            request_value, root, trace_id, request_path, "user"
        )
        output_value, output_path = self._root_field(
            root,
            "attributes.aivision.request.output",
            ("attributes.langfuse.trace.output", *OUTPUT_KEYS),
        )
        output, output_observed, _ = self._root_text(
            output_value, root, trace_id, output_path, "assistant"
        )
        models = [row for row in normalized if row.get("attributes.gen_ai.span.kind") == "LLM"]
        # Subagent generation is never silently promoted to the parent final answer.
        parent_models = [row for row in models if not self._under_tool(row, normalized)]
        for row in parent_models:
            inputs = field_messages(row, trace_id, INPUT_KEYS)
            candidate = current_message(inputs, "user")
            if not request_observed and candidate is not None:
                request = (
                    TextEvidence(candidate.content, candidate.sources)
                    if candidate.content
                    else None
                )
                history = inputs[: inputs.index(candidate)] or None
                request_observed = True
                break
            if candidate and request and candidate.content == request.content:
                history = history or inputs[: inputs.index(candidate)] or None
                break
        if not output_observed:
            for row in reversed(parent_models):
                candidate = current_message(field_messages(row, trace_id, OUTPUT_KEYS), "assistant")
                if candidate is not None:
                    output = (
                        TextEvidence(candidate.content, candidate.sources)
                        if candidate.content
                        else None
                    )
                    break
        contexts = [
            context
            for row in parent_models
            for context in supplied_context(row, trace_id, tool_rows)
        ]
        retrieval = []
        for row in normalized:
            if row.get("attributes.gen_ai.span.kind") != "RETRIEVER":
                continue
            value, path = select_field(row, OUTPUT_KEYS)
            # RETRIEVER output is an explicit SDK serialized document container.
            value = decode(value)
            if path and isinstance(value, list):
                for index, item in enumerate(value):
                    if isinstance(item, dict) and isinstance(item.get("page_content"), str):
                        retrieval.append(
                            ContextEvidence(
                                item["page_content"],
                                [source(row, trace_id, f"{path}[{index}].page_content")],
                                "retriever_output",
                            )
                        )
        started = _parse_ts(root.get("start_time_unix_nano"))
        return FrameworkProjection(
            timestamp=started.isoformat() if started else None,
            user_query=request.content if request else "",
            agent_output=output.content if output else "",
            outcome=_build_outcome(normalized),
            labels={},
            steps=_build_steps(normalized),
            tool_actions=tools,
            current_request=request,
            final_output=output,
            conversation_history=history,
            provided_context=[context for context in contexts if context.kind != "instruction"]
            or None,
            model_instructions=[context for context in contexts if context.kind == "instruction"]
            or None,
            retrieval_observations=retrieval or None,
            user_query_span_id=request.sources[0].span_id if request else None,
            agent_output_span_id=output.sources[0].span_id if output else None,
        )

    @staticmethod
    def _root_field(row, application_path, fallback_keys):
        """Presence establishes the application boundary before body validation."""
        if application_path in row or application_path + ".encoding" in row:
            return row.get(application_path), application_path
        return select_field(row, fallback_keys)

    @staticmethod
    def _root_text(value, row, trace_id, path, role):
        """Return visible text, observed role/body boundary, and input history."""
        if not path:
            return None, False, None
        application_body = path in {
            "attributes.aivision.request.input",
            "attributes.aivision.request.output",
        }
        if application_body:
            # Only the producer's explicit per-direction marker declares JSON.
            # Unmarked application strings remain literal, whatever their value.
            encoding_path = path + ".encoding"
            if encoding_path in row:
                encoding = row[encoding_path]
                if encoding != "json" or not isinstance(value, str):
                    raise ValueError("invalid application body encoding")

                def reject_constant(_value):
                    raise ValueError("invalid JSON constant")

                try:
                    value = json.loads(value, parse_constant=reject_constant)
                except ValueError as exc:
                    raise ValueError("invalid serialized application body") from exc
        else:
            value = decode_message_container(
                value, declared_sdk_container=path.startswith("attributes.langfuse.")
            )
        if isinstance(value, dict) and isinstance(value.get("request"), str) and role == "user":
            return text_parts(value["request"], row, trace_id, path + ".request"), True, None
        if isinstance(value, str):
            return text_parts(value, row, trace_id, path), True, None
        # Explicit role messages are safe; arbitrary output dicts/state are not answers.
        from agentlens.service.datasource.projection.evidence import messages

        items = messages(value, row, trace_id, path)
        candidate = current_message(items, role)
        if candidate is None:
            return None, application_body, None
        text = TextEvidence(candidate.content, candidate.sources) if candidate.content else None
        history = (items[: items.index(candidate)] or None) if role == "user" else None
        return text, True, history

    @staticmethod
    def _under_tool(row, rows):
        by_id = {str(item.get("span_id")): item for item in rows}
        seen = set()
        parent = row.get("parent_span_id")
        while parent in by_id and parent not in seen:
            seen.add(parent)
            ancestor = by_id[parent]
            if ancestor.get("attributes.gen_ai.span.kind") == "TOOL":
                return True
            parent = ancestor.get("parent_span_id")
        return False

    def resolve_var(self, projection, var):
        from dataclasses import asdict

        if var in {"current_request", "final_output", "provided_context"}:
            from dataclasses import asdict

            value = getattr(projection, var)
            return (
                [asdict(item) for item in value]
                if isinstance(value, list)
                else asdict(value)
                if value
                else None
            )
        return _resolve_var(projection, var)
