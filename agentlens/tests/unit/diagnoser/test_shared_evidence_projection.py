"""Source-positive SDK shapes and missing/ambiguous evidence regressions."""

import json

import pytest

from agentlens.service.datasource.projection.service import project_trace
from agentlens.service.datasource.trace import Trace


def row(span_id, kind, **attrs):
    return {
        "trace_id": "t",
        "span_id": span_id,
        "parent_span_id": "root" if span_id != "root" else "",
        "name": kind,
        "status_code": 1,
        "start_time_unix_nano": 100 if span_id == "root" else 200,
        "end_time_unix_nano": 300,
        "attributes.gen_ai.span.kind": kind,
        **{
            f"attributes.{key}": json.dumps(value) if isinstance(value, (dict, list)) else value
            for key, value in attrs.items()
        },
    }


@pytest.mark.parametrize("runtime", ["codex", "dsh"])
def test_coding_current_request_excludes_history_and_preserves_block_sources(runtime):
    spans = [
        row(
            "root",
            "CHAT",
            **{
                "gen_ai.input.messages": [
                    {"role": "user", "content": "previous request"},
                    {"role": "assistant", "content": "previous answer"},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "current request"},
                            {"type": "image", "url": "https://example.invalid/image"},
                        ],
                    },
                ],
                "gen_ai.output.messages": [
                    {
                        "role": "assistant",
                        "content": [
                            {"type": "reasoning", "text": "private reasoning"},
                            {"type": "output_text", "text": "final answer"},
                        ],
                    }
                ],
            },
        )
    ]
    projection = project_trace(
        Trace(trace_id="t", runtime_type=runtime, spans=spans)
    ).require_projection()
    assert projection.user_query == "current request"
    assert projection.agent_output == "final answer"
    assert projection.current_request.sources[0].trace_id == "t"
    assert (
        projection.current_request.sources[0].field_path
        == "attributes.gen_ai.input.messages[2].content[0].text"
    )
    assert [item.content for item in projection.conversation_history] == [
        "previous request",
        "previous answer",
    ]


@pytest.mark.parametrize("sdk", ["langfuse", "relay"])
@pytest.mark.parametrize("runtime", ["langchain", "deepagents", "langfuse-sdk"])
def test_framework_maps_supplied_tool_context_with_generation_relation(runtime, sdk):
    tool_message = (
        {"type": "tool", "content": "receipt 391", "tool_call_id": "call-1"}
        if sdk == "langfuse"
        else {
            "role": "tool",
            "parts": [{"type": "tool_call_response", "id": "call-1", "response": "receipt 391"}],
        }
    )
    input_key = "langfuse.observation.input" if sdk == "langfuse" else "gen_ai.input.messages"
    output_key = "langfuse.observation.output" if sdk == "langfuse" else "gen_ai.output.messages"
    spans = [
        row(
            "root",
            "AGENT",
            **{
                "aivision.request.input": {"request": "use receipt"},
                "aivision.request.input.encoding": "json",
                "aivision.request.output": "done",
            },
        ),
        row(
            "tool",
            "TOOL",
            **{
                "gen_ai.tool.call.id": "call-1",
                "gen_ai.tool.name": "lookup",
                "gen_ai.tool.call.result": "receipt 391",
            },
        ),
        row(
            "model",
            "LLM",
            **{
                input_key: [{"role": "user", "content": "use receipt"}, tool_message],
                output_key: [{"role": "assistant", "content": "done"}],
            },
        ),
    ]
    spans[1]["start_time_unix_nano"] = 120
    spans[1]["end_time_unix_nano"] = 180
    spans[2]["start_time_unix_nano"] = 200
    projection = project_trace(
        Trace(trace_id="t", runtime_type=runtime, spans=spans)
    ).require_projection()
    assert projection.current_request.content == "use receipt"
    assert projection.final_output.content == "done"
    material = projection.provided_context[0]
    assert material.content == "receipt 391"
    assert material.sources[0].span_id == "model"
    assert material.sources[0].call_id == "call-1"
    assert material.producer_source.span_id == "tool"


def test_retrieval_output_alone_does_not_prove_supplied_context():
    spans = [
        row(
            "root",
            "AGENT",
            **{
                "aivision.request.input": {"request": "question"},
                "aivision.request.input.encoding": "json",
            },
        ),
        row(
            "retrieve",
            "RETRIEVER",
            **{"langfuse.observation.output": [{"page_content": "unused fact"}]},
        ),
        row(
            "model",
            "LLM",
            **{
                "gen_ai.input.messages": [{"role": "user", "content": "question"}],
                "gen_ai.output.messages": [{"role": "assistant", "content": "answer"}],
            },
        ),
    ]
    projection = project_trace(
        Trace(trace_id="t", runtime_type="langchain", spans=spans)
    ).require_projection()
    assert projection.provided_context is None
    assert projection.retrieval_observations[0].content == "unused fact"


def test_missing_framework_facts_remain_unknown():
    projection = project_trace(
        Trace(trace_id="t", runtime_type="deepagents", spans=[row("root", "AGENT")])
    ).require_projection()
    assert projection.current_request is None
    assert projection.final_output is None
    assert projection.conversation_history is None
    assert projection.provided_context is None


def test_invalid_message_json_is_projection_failure():
    trace = project_trace(
        Trace(
            trace_id="t",
            runtime_type="codex",
            spans=[row("root", "CHAT", **{"gen_ai.input.messages": "[{broken"})],
        )
    )
    assert trace.projection is None
    assert "invalid" in trace.projection_error.lower()


def test_system_instruction_is_not_grounding_material():
    spans = [
        row("root", "AGENT"),
        row(
            "model",
            "LLM",
            **{
                "gen_ai.input.messages": [
                    {"role": "system", "content": "You are helpful"},
                    {"role": "user", "content": "question"},
                ],
                "gen_ai.output.messages": [{"role": "assistant", "content": "answer"}],
            },
        ),
    ]
    projection = project_trace(
        Trace(trace_id="t", runtime_type="langchain", spans=spans)
    ).require_projection()
    assert projection.provided_context is None
    assert projection.model_instructions[0].kind == "instruction"
    assert projection.model_instructions[0].content == "You are helpful"


def test_later_tool_result_cannot_be_attributed_to_earlier_generation():
    spans = [
        row("root", "AGENT"),
        row(
            "tool",
            "TOOL",
            **{"gen_ai.tool.call.id": "call-1", "gen_ai.tool.call.result": "receipt"},
        ),
        row(
            "model",
            "LLM",
            **{
                "gen_ai.input.messages": [
                    {"role": "user", "content": "question"},
                    {"role": "tool", "tool_call_id": "call-1", "content": "receipt"},
                ]
            },
        ),
    ]
    projection = project_trace(
        Trace(trace_id="t", runtime_type="langchain", spans=spans)
    ).require_projection()
    assert projection.provided_context[0].producer_source is None


def test_framework_tool_and_verification_sources_point_to_original_fields():
    spans = [
        row("root", "AGENT"),
        row(
            "tool",
            "TOOL",
            **{
                "langfuse.observation.type": "TOOL",
                "langfuse.observation.input": {"command": "pytest"},
                "langfuse.observation.output": "exit code 1",
                "gen_ai.tool.call.id": "call-1",
                "gen_ai.tool.name": "shell",
            },
        ),
    ]
    projection = project_trace(
        Trace(trace_id="t", runtime_type="deepagents", spans=spans)
    ).require_projection()
    action = projection.tool_actions[0]
    assert action.field_sources["arguments"].field_path == "attributes.langfuse.observation.input"
    assert action.field_sources["result"].call_id == "call-1"
    assert (
        projection.verification_actions[0].field_sources["result"].field_path
        == "attributes.langfuse.observation.output"
    )


def test_coding_plaintext_request_and_output_have_sources():
    projection = project_trace(
        Trace(
            "t",
            "dsh",
            [
                row(
                    "root",
                    "CHAT",
                    **{"gen_ai.input.messages": "question", "gen_ai.output.messages": "answer"},
                )
            ],
        )
    ).require_projection()
    assert projection.current_request.content == "question"
    assert projection.final_output.content == "answer"
    assert projection.final_output.sources[0].field_path == "attributes.gen_ai.output.messages"


def test_relay_structured_tool_response_retains_actual_model_input_source():
    spans = [
        row("root", "AGENT"),
        row(
            "model",
            "LLM",
            **{
                "gen_ai.input.messages": [
                    {"role": "user", "parts": [{"type": "text", "content": "question"}]},
                    {
                        "role": "tool",
                        "parts": [
                            {"type": "tool_call_response", "id": "c1", "response": {"receipt": 391}}
                        ],
                    },
                ]
            },
        ),
    ]
    projection = project_trace(Trace("t", "deepagents", spans)).require_projection()
    assert projection.provided_context[0].content == '{"receipt": 391}'
    assert projection.provided_context[0].sources[0].field_path.endswith("parts[0].response")


def test_call_identity_without_matching_content_does_not_prove_producer():
    spans = [
        row("root", "AGENT"),
        row(
            "tool",
            "TOOL",
            **{"gen_ai.tool.call.id": "c1", "gen_ai.tool.call.result": "different receipt"},
        ),
        row(
            "model",
            "LLM",
            **{
                "gen_ai.input.messages": [
                    {"role": "user", "content": "question"},
                    {"role": "tool", "tool_call_id": "c1", "content": "receipt 391"},
                ]
            },
        ),
    ]
    spans[1]["end_time_unix_nano"] = 150
    projection = project_trace(Trace("t", "langchain", spans)).require_projection()
    assert projection.provided_context[0].producer_source is None


def test_normalized_nested_message_source_retains_original_sdk_field():
    from agentlens.service.datasource.projection.evidence import source

    observed = {
        "span_id": "model",
        "attributes.aivision.normalization.sources": {
            "gen_ai.input.messages": "langfuse.observation.metadata.inputs"
        },
    }
    ref = source(observed, "t", "attributes.gen_ai.input.messages.messages[0].content")
    assert ref.field_path == "attributes.langfuse.observation.metadata.inputs.messages[0].content"


@pytest.mark.parametrize("runtime", ["codex", "dsh", "langchain", "deepagents"])
@pytest.mark.parametrize(
    "current_content",
    [
        [],
        [{"type": "image", "url": "https://example.invalid/i"}],
        [{"type": "reasoning", "text": "hidden"}],
        "",
    ],
)
def test_latest_role_boundary_never_promotes_historical_text(runtime, current_content):
    from agentlens.diagnoser.executors.llm.custom.executor import LlmCustomExecutor

    spans = [
        row(
            "root",
            "CHAT" if runtime in {"codex", "dsh"} else "AGENT",
            **{
                "gen_ai.input.messages": [
                    {"role": "user", "content": "OLD REQUEST"},
                    {"role": "assistant", "content": "OLD ANSWER"},
                    {"role": "user", "content": current_content},
                ],
                "gen_ai.output.messages": [
                    {"role": "assistant", "content": "OLD ANSWER"},
                    {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]},
                ],
            },
        ),
        row(
            "earlier-model",
            "LLM",
            **{
                "gen_ai.input.messages": [{"role": "user", "content": "OLD REQUEST"}],
                "gen_ai.output.messages": [{"role": "assistant", "content": "OLD ANSWER"}],
            },
        ),
    ]
    trace = project_trace(Trace("t", runtime, spans))
    projection = trace.require_projection()
    assert projection.current_request is None
    assert projection.final_output is None
    assert projection.user_query == ""
    assert projection.agent_output == ""
    assert [item.content for item in projection.conversation_history] == [
        "OLD REQUEST",
        "OLD ANSWER",
    ]
    assert projection.conversation_history[0].sources[0].field_path.endswith("[0].content")
    assert trace.spans == spans
    executor = LlmCustomExecutor(
        {
            "prompt": "{{user_query}} {{agent_output}}",
            "required_vars": ["user_query", "agent_output"],
            "metrics": [{"name": "quality"}],
        }
    )
    assert (
        executor.applicability_skip_reason(trace)
        == "missing_required_evidence: user_query, agent_output"
    )


@pytest.mark.parametrize("runtime", ["codex", "dsh", "langchain", "deepagents"])
def test_latest_generation_without_assistant_text_blocks_older_generation_output(runtime):
    spans = [row("root", "CHAT" if runtime in {"codex", "dsh"} else "AGENT")]
    for span_id, body in [("first", "OLD ANSWER"), ("last", "")]:
        spans.append(
            row(
                span_id,
                "LLM",
                **{
                    "gen_ai.output.messages": [
                        {"role": "assistant", "content": body, "tool_calls": []}
                    ]
                },
            )
        )
    spans[1]["start_time_unix_nano"] = 120
    spans[2]["start_time_unix_nano"] = 200
    projection = project_trace(Trace("t", runtime, spans)).require_projection()
    assert projection.final_output is None
    assert projection.agent_output == ""


@pytest.mark.parametrize("runtime", ["codex", "dsh", "langchain", "deepagents"])
@pytest.mark.parametrize(
    "body",
    [
        "42",
        "true",
        "null",
        '{"answer":42}',
        "[reference](https://example.invalid)",
        "{name} is the placeholder",
        '"hello" world',
        "[1,2]",
    ],
)
def test_literal_application_bodies_preserve_text_and_sources(runtime, body):
    coding = runtime in {"codex", "dsh"}
    input_key = "gen_ai.input.messages" if coding else "aivision.request.input"
    output_key = "gen_ai.output.messages" if coding else "aivision.request.output"
    projection = project_trace(
        Trace(
            "t",
            runtime,
            [row("root", "CHAT" if coding else "AGENT", **{input_key: body, output_key: body})],
        )
    ).require_projection()
    assert projection.current_request.content == body
    assert projection.final_output.content == body
    assert projection.user_query == body
    assert projection.agent_output == body
    assert projection.current_request.sources[0].field_path == f"attributes.{input_key}"
    assert projection.final_output.sources[0].field_path == f"attributes.{output_key}"


@pytest.mark.parametrize("runtime", ["codex", "dsh", "langchain", "deepagents"])
@pytest.mark.parametrize(
    "body",
    [
        "42",
        "true",
        "null",
        '{"answer":42}',
        "[reference](https://example.invalid)",
        "{name} is the placeholder",
        '"hello" world',
    ],
)
def test_sdk_message_content_remains_literal_body(runtime, body):
    projection = project_trace(
        Trace(
            "t",
            runtime,
            [
                row(
                    "root",
                    "CHAT" if runtime in {"codex", "dsh"} else "AGENT",
                    **{
                        "gen_ai.input.messages": [{"role": "user", "content": body}],
                        "gen_ai.output.messages": [{"role": "assistant", "content": body}],
                    },
                )
            ],
        )
    ).require_projection()
    assert projection.current_request.content == projection.final_output.content == body
    assert projection.current_request.sources[0].field_path.endswith("[0].content")


@pytest.mark.parametrize("runtime", ["codex", "dsh", "langchain", "deepagents"])
@pytest.mark.parametrize("malformed", ["[{broken", '{"messages": [broken}'])
def test_declared_serialized_sdk_envelope_error_stays_failed(runtime, malformed):
    trace = project_trace(
        Trace(
            "t",
            runtime,
            [
                row(
                    "root",
                    "CHAT" if runtime in {"codex", "dsh"} else "AGENT",
                    **{"gen_ai.input.messages": malformed},
                )
            ],
        )
    )
    assert trace.projection is None
    assert trace.projection_error


@pytest.mark.parametrize("runtime", ["codex", "dsh", "langchain", "deepagents"])
def test_history_root_and_nested_aliases_read_same_sourced_messages(runtime):
    from agentlens.service.datasource.variables import resolve_prompt_vars

    trace = project_trace(
        Trace(
            "t",
            runtime,
            [
                row(
                    "root",
                    "CHAT" if runtime in {"codex", "dsh"} else "AGENT",
                    **{
                        "gen_ai.input.messages": [
                            {"role": "user", "content": "old"},
                            {"role": "assistant", "content": "old answer"},
                            {"role": "user", "content": "current"},
                        ]
                    },
                )
            ],
        )
    )
    values = resolve_prompt_vars(
        trace, {"history", "history[0].content", "history[0].sources[0].field_path"}
    )
    assert values["history[0].content"] == values["history"][0]["content"] == "old"
    assert (
        values["history[0].sources[0].field_path"]
        == values["history"][0]["sources"][0]["field_path"]
    )


@pytest.mark.parametrize("runtime", ["langchain", "deepagents"])
def test_declared_sdk_business_state_is_not_promoted_to_literal_application_answer(runtime):
    projection = project_trace(
        Trace(
            "t",
            runtime,
            [
                row(
                    "root",
                    "AGENT",
                    **{
                        "langfuse.observation.output": {
                            "arbitrary_checkpoint_state": "not a final answer"
                        },
                    },
                )
            ],
        )
    ).require_projection()
    assert projection.final_output is None
    assert projection.agent_output == ""


@pytest.mark.parametrize("runtime", ["langchain", "deepagents"])
@pytest.mark.parametrize(
    "body", ["[{broken", '{"messages": [broken}', '{"role":"user","content":"literal JSON body"}']
)
def test_explicit_application_body_never_becomes_an_sdk_envelope(runtime, body):
    projection = project_trace(
        Trace(
            "t",
            runtime,
            [
                row(
                    "root",
                    "AGENT",
                    **{
                        "aivision.request.input": body,
                        "aivision.request.output": body,
                    },
                )
            ],
        )
    ).require_projection()
    assert projection.current_request.content == projection.final_output.content == body
    assert projection.current_request.sources[0].field_path == "attributes.aivision.request.input"
    assert projection.final_output.sources[0].field_path == "attributes.aivision.request.output"


@pytest.mark.parametrize("runtime", ["codex", "dsh", "langchain", "deepagents"])
def test_history_root_and_nested_use_same_bounded_public_value(runtime):
    from agentlens.service.datasource.variables import resolve_prompt_vars

    trace = project_trace(
        Trace(
            "t",
            runtime,
            [
                row(
                    "root",
                    "CHAT" if runtime in {"codex", "dsh"} else "AGENT",
                    **{
                        "gen_ai.input.messages": [
                            {"role": "user", "content": "x" * 25_000},
                            {"role": "user", "content": "current"},
                        ]
                    },
                )
            ],
        )
    )
    values = resolve_prompt_vars(trace, {"history", "history[0].content"})
    assert values["history"][0]["content"] == values["history[0].content"]
    assert trace.projection.conversation_history[0].content == "x" * 25_000


@pytest.mark.parametrize("runtime", ["codex", "dsh", "langchain", "deepagents"])
@pytest.mark.parametrize("sdk", ["constructor", "data"])
@pytest.mark.parametrize("has_text", [False, True])
def test_sdk_outer_role_identity_survives_content_parsing(runtime, sdk, has_text):
    def message(role, content):
        if sdk == "constructor":
            return {
                "lc": 1,
                "type": "constructor",
                "id": [
                    "langchain_core",
                    "messages",
                    "HumanMessage" if role == "user" else "AIMessage",
                ],
                "kwargs": {"content": content},
            }
        return {"type": "human" if role == "user" else "ai", "data": {"content": content}}

    spans = [
        row(
            "root",
            "CHAT" if runtime in {"codex", "dsh"} else "AGENT",
            **{
                "gen_ai.input.messages": [
                    {"role": "user", "content": "OLD REQUEST"},
                    message("user", "current" if has_text else []),
                ],
                "gen_ai.output.messages": [
                    {"role": "assistant", "content": "OLD ANSWER"},
                    message("assistant", "final" if has_text else ""),
                ],
            },
        )
    ]
    projection = project_trace(Trace("t", runtime, spans)).require_projection()
    assert projection.user_query == ("current" if has_text else "")
    assert projection.agent_output == ("final" if has_text else "")
    if has_text:
        assert projection.current_request.sources[0].field_path.endswith(
            f"[1].{'kwargs' if sdk == 'constructor' else 'data'}.content"
        )
    else:
        assert projection.current_request is None
        assert projection.final_output is None


@pytest.mark.parametrize("runtime", ["langchain", "deepagents"])
@pytest.mark.parametrize(
    "body",
    [
        "42",
        "true",
        "null",
        "[link](https://example.invalid)",
        '{"role":"user","content":"literal"}',
        0,
        False,
        None,
    ],
)
def test_declared_application_json_preserves_type_and_blocks_old_answer(runtime, body):
    spans = [
        row(
            "root",
            "AGENT",
            **{
                "aivision.request.input": json.dumps(body),
                "aivision.request.input.encoding": "json",
                "aivision.request.output": json.dumps(body),
                "aivision.request.output.encoding": "json",
            },
        ),
        row(
            "older",
            "LLM",
            **{
                "gen_ai.input.messages": [{"role": "user", "content": "OLD REQUEST"}],
                "gen_ai.output.messages": [{"role": "assistant", "content": "OLD ANSWER"}],
            },
        ),
    ]
    projection = project_trace(Trace("t", runtime, spans)).require_projection()
    expected = body if isinstance(body, str) else ""
    assert projection.user_query == projection.agent_output == expected
    if isinstance(body, str):
        assert projection.current_request.content == projection.final_output.content == body
        assert (
            projection.current_request.sources[0].field_path == "attributes.aivision.request.input"
        )
        assert projection.final_output.sources[0].field_path == "attributes.aivision.request.output"
    else:
        assert projection.current_request is projection.final_output is None
    assert spans[0]["attributes.aivision.request.output"] == json.dumps(body)


@pytest.mark.parametrize("runtime", ["langchain", "deepagents"])
@pytest.mark.parametrize("direction", ["input", "output"])
@pytest.mark.parametrize("encoding,body", [("json", "[{broken"), ("xml", "42")])
def test_invalid_declared_application_encoding_is_projection_failure(
    runtime, direction, encoding, body
):
    trace = project_trace(
        Trace(
            "t",
            runtime,
            [
                row(
                    "root",
                    "AGENT",
                    **{
                        f"aivision.request.{direction}": body,
                        f"aivision.request.{direction}.encoding": encoding,
                    },
                )
            ],
        )
    )
    assert trace.projection is None
    assert trace.projection_error is not None


def _framework_application_boundary_trace(runtime, direction, *, body, carrier, encoding):
    attrs = {
        "gen_ai.input.messages": [{"role": "user", "content": "OLD REQUEST"}],
        "gen_ai.output.messages": [{"role": "assistant", "content": "OLD ANSWER"}],
    }
    if carrier:
        attrs[f"aivision.request.{direction}"] = body
    attrs.update(encoding)
    return project_trace(
        Trace(
            "t",
            runtime,
            [
                row("root", "AGENT", **attrs),
                row(
                    "older",
                    "LLM",
                    **{
                        "gen_ai.input.messages": [{"role": "user", "content": "OLD REQUEST"}],
                        "gen_ai.output.messages": [{"role": "assistant", "content": "OLD ANSWER"}],
                    },
                ),
            ],
        )
    )


@pytest.mark.parametrize("runtime", ["langchain", "deepagents"])
@pytest.mark.parametrize("direction", ["input", "output"])
@pytest.mark.parametrize(
    "encoding,body,carrier",
    [
        ("json", "", True),
        ("json", None, True),
        ("json", 0, True),
        ("json", False, True),
        ("json", None, False),
        ("xml", "", True),
        ("xml", None, True),
        ("xml", None, False),
        (None, "", True),
        (None, None, False),
    ],
)
def test_application_empty_or_missing_declared_carrier_is_failed(
    runtime, direction, encoding, body, carrier, monkeypatch
):
    from agentlens.diagnoser.base import execute_with_applicability
    from agentlens.diagnoser.executors.llm.custom.executor import LlmCustomExecutor
    from agentlens.service.datasource.trace import ProjectionUnavailable

    trace = _framework_application_boundary_trace(
        runtime,
        direction,
        body=body,
        carrier=carrier,
        encoding={f"aivision.request.{direction}.encoding": encoding},
    )
    assert trace.projection is None
    assert trace.projection_error is not None
    var = "user_query" if direction == "input" else "agent_output"
    executor = LlmCustomExecutor(
        {"prompt": "{{" + var + "}}", "required_vars": [var], "metrics": [{"name": "quality"}]}
    )
    monkeypatch.setattr(
        executor, "evaluate", lambda _trace: pytest.fail("failed projection cannot evaluate")
    )
    with pytest.raises(ProjectionUnavailable):
        execute_with_applicability(executor, trace)


@pytest.mark.parametrize("runtime", ["langchain", "deepagents"])
@pytest.mark.parametrize("direction", ["input", "output"])
@pytest.mark.parametrize("body,marked", [("", False), (None, False), ('""', True), ("null", True)])
def test_application_explicit_empty_body_blocks_legacy_fallback_and_skips_gate(
    runtime, direction, body, marked, monkeypatch
):
    from agentlens.diagnoser.base import ApplicabilitySkipped, execute_with_applicability
    from agentlens.diagnoser.executors.llm.custom.executor import LlmCustomExecutor

    trace = _framework_application_boundary_trace(
        runtime,
        direction,
        body=body,
        carrier=True,
        encoding={f"aivision.request.{direction}.encoding": "json"} if marked else {},
    )
    projection = trace.require_projection()
    assert trace.projection_error is None
    assert (
        getattr(projection, "current_request" if direction == "input" else "final_output") is None
    )
    var = "user_query" if direction == "input" else "agent_output"
    assert getattr(projection, var) == ""
    assert getattr(projection, "agent_output" if direction == "input" else "user_query") == (
        "OLD ANSWER" if direction == "input" else "OLD REQUEST"
    )
    assert trace.spans[0][f"attributes.aivision.request.{direction}"] == body
    executor = LlmCustomExecutor(
        {"prompt": "{{" + var + "}}", "required_vars": [var], "metrics": [{"name": "quality"}]}
    )
    monkeypatch.setattr(
        executor, "evaluate", lambda _trace: pytest.fail("missing current body cannot evaluate")
    )
    result = execute_with_applicability(executor, trace)
    assert isinstance(result, ApplicabilitySkipped)
    assert result.reason == "missing_required_evidence: " + var


@pytest.mark.parametrize("runtime", ["langchain", "deepagents"])
@pytest.mark.parametrize("direction", ["input", "output"])
def test_application_field_and_marker_absence_allows_real_sdk_fallback(runtime, direction):
    from agentlens.diagnoser.executors.llm.custom.executor import LlmCustomExecutor

    trace = _framework_application_boundary_trace(
        runtime, direction, body=None, carrier=False, encoding={}
    )
    projection = trace.require_projection()
    assert projection.current_request.content == projection.user_query == "OLD REQUEST"
    assert projection.final_output.content == projection.agent_output == "OLD ANSWER"
    assert (
        projection.current_request.sources[0].field_path
        == "attributes.gen_ai.input.messages[0].content"
    )
    assert (
        projection.final_output.sources[0].field_path
        == "attributes.gen_ai.output.messages[0].content"
    )
    executor = LlmCustomExecutor(
        {
            "prompt": "{{user_query}} {{agent_output}}",
            "required_vars": ["user_query", "agent_output"],
            "metrics": [{"name": "quality"}],
        }
    )
    assert executor.applicability_skip_reason(trace) is None
