"""Mask only a new protected Record copy; original Trace/history stay immutable."""

import hashlib
from copy import deepcopy
from typing import Any

# These are public protocol identifiers, not private data just because a short
# configured literal happens to occur in their spelling.
_SCHEMA_KEYS = frozenset(
    """
trace_id span_id parent_span_id call_id owner_id runtime_type source_type source_meta
schema_version projection spans trace_snapshot timestamp name service_name kind type
content text value role data kwargs parts messages request response answer_text user_query
agent_output current_request final_output conversation_history provided_context
model_instructions retrieval_observations sources source source_refs field_path field_sources
producer_source arguments result status status_code status_message start_time end_time
start_time_unix_nano end_time_unix_nano outcome has_error result_state total_latency_ms
interrupted labels node_seq steps tool_actions file_mutations verification_actions
subagents tool_calls ref action_id source_action_id source_span_id target path command
before after input output usage model latency_ms index depth tool_call_id id
""".split()
)
_PUBLIC_ATTRIBUTES = frozenset(
    """
attributes.input.value attributes.output.value attributes.langfuse.observation.input
attributes.langfuse.observation.output attributes.langfuse.trace.input
attributes.langfuse.trace.output attributes.gen_ai.input.messages
attributes.gen_ai.output.messages attributes.aivision.request.input
attributes.aivision.request.output attributes.aivision.request.input.encoding
attributes.aivision.request.output.encoding attributes.agentic.input.delta
attributes.agentic.output.final attributes.gen_ai.tool.call.arguments
attributes.gen_ai.tool.call.result attributes.gen_ai.tool.call.id
attributes.gen_ai.tool.name attributes.gen_ai.operation.name attributes.gen_ai.span.kind
attributes.aivision.normalization.sources resource.attributes.service.name
""".split()
)
_IDENTIFIER_VALUES = frozenset(
    """
trace_id span_id parent_span_id call_id owner_id runtime_type source_type schema_version
field_path action_id source_action_id source_span_id ref projection_kind
status kind type operation operation_name tool_type evidence_level identity_source result_state
""".split()
)
_PROTOCOL_CHILDREN = frozenset(
    """
projection spans trace_snapshot outcome current_request final_output sources source source_refs
field_sources producer_source conversation_history provided_context model_instructions
retrieval_observations tool_actions file_mutations verification_actions subagents steps
""".split()
)


def protected_snapshot_copy(snapshot: dict, spec: dict) -> dict:
    """Return a separate derived copy, masking raw and semantic duplicate values.

    Callers invoke this only for a new protected specialist Record, never for
    another diagnoser or a saved historical Record. Policy literals are not
    included in the return metadata; source offsets remain separate evidence.
    """
    policy = spec.get("specialist_policy")
    if not isinstance(policy, dict) or policy.get("kind") != "protected_content_exposure_v1":
        return deepcopy(snapshot)
    values = sorted(
        {
            marker["value"]
            for marker in policy.get("markers", [])
            if isinstance(marker, dict)
            and marker.get("visibility") == "protected"
            and isinstance(marker.get("value"), str)
            and marker["value"]
        },
        key=len,
        reverse=True,
    )
    placeholder = next(
        (
            candidate
            for candidate in (
                "[PROTECTED]",
                "[REDACTED]",
                "\ufffd",
                "\u2588",
                *(chr(code) for code in range(0xE000, 0xE041)),
            )
            if not any(literal in candidate for literal in values)
        ),
        None,
    )
    if placeholder is None:
        raise ValueError("invalid protected marker configuration")

    def mask(value: Any, *, protocol: bool = False, raw_row: bool = False):
        if isinstance(value, str):
            for literal in values:
                value = value.replace(literal, placeholder)
            return value
        if isinstance(value, dict):
            output = {}
            for key, item in value.items():
                safe_key = key
                dynamic_key = isinstance(key, str) and (
                    not protocol
                    and key not in _SCHEMA_KEYS
                    or raw_row
                    and key.startswith("attributes.")
                    and key not in _PUBLIC_ATTRIBUTES
                )
                if dynamic_key:
                    # Attribute envelopes have a public prefix; inspect only the
                    # dynamic name. Use opaque unique keys rather than replacing
                    # two private names with the same placeholder.
                    dynamic = key.removeprefix("attributes.")
                    if any(literal in dynamic for literal in values):
                        safe_key = (
                            "redacted_field_" + hashlib.sha256(key.encode("utf-8")).hexdigest()
                        )
                        suffix = 0
                        while safe_key in value or safe_key in output:
                            suffix += 1
                            safe_key = (
                                "redacted_field_"
                                + hashlib.sha256(key.encode("utf-8")).hexdigest()
                                + f"_{suffix}"
                            )
                if protocol and key in _IDENTIFIER_VALUES:
                    output[safe_key] = deepcopy(item)
                else:
                    output[safe_key] = mask(
                        item,
                        protocol=protocol and key in _PROTOCOL_CHILDREN,
                        raw_row=protocol and key == "spans",
                    )
            return output
        if isinstance(value, list):
            return [mask(item, protocol=protocol, raw_row=raw_row) for item in value]
        return deepcopy(value)

    return mask(snapshot, protocol=True)
