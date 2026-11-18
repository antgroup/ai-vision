import gzip
import json

from django.test import SimpleTestCase, TestCase
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)

from apps.core.views.langfuse_ingest import (
    _normalize_langfuse_span,
    normalize_langfuse_otlp_payload,
)


def _make_span(name, attributes):
    request = ExportTraceServiceRequest()
    resource_spans = request.resource_spans.add()
    scope_spans = resource_spans.scope_spans.add()
    span = scope_spans.spans.add()
    span.trace_id = b"0123456789abcdef"
    span.span_id = b"01234567"
    span.name = name
    for key, value in attributes.items():
        item = span.attributes.add()
        item.key = key
        if isinstance(value, bool):
            item.value.bool_value = value
        elif isinstance(value, int):
            item.value.int_value = value
        else:
            item.value.string_value = str(value)
    return span


def _attr_dict(span):
    return {item.key: item.value for item in span.attributes}


class LangfuseNormalizeSpanMappingsTests(TestCase):
    """验证 _normalize_langfuse_span 补全的 5 个 gen_ai 映射。"""

    def test_authenticated_agent_product_stamps_storage_resource_for_plain_sdk(self):
        request = ExportTraceServiceRequest()
        for supplied in ([], ["Codex"], ["Codex", "forged-other-agent"]):
            resource_spans = request.resource_spans.add()
            for value in supplied:
                item = resource_spans.resource.attributes.add(key="ant.agent.product")
                item.value.string_value = value
            resource_spans.scope_spans.add().spans.add().name = "sdk-direct"
        normalized = normalize_langfuse_otlp_payload(
            request.SerializeToString(), None, agent_product="langfuse"
        )
        parsed = ExportTraceServiceRequest.FromString(normalized)
        for resource_spans in parsed.resource_spans:
            self.assertEqual(
                [
                    item.value.string_value
                    for item in resource_spans.resource.attributes
                    if item.key == "ant.agent.product"
                ],
                ["langfuse"],
            )

    def test_generation_maps_input_output_to_messages(self):
        span = _make_span(
            "Chat",
            {
                "langfuse.observation.type": "generation",
                "langfuse.observation.model.name": "glm-5.2",
                "langfuse.observation.input": json.dumps(
                    [
                        {"role": "system", "content": "You are an expert."},
                        {"role": "user", "content": "What is Langfuse?"},
                    ]
                ),
                "langfuse.observation.output": json.dumps(
                    [{"role": "assistant", "content": "Langfuse is..."}]
                ),
            },
        )
        _normalize_langfuse_span(span)
        attrs = _attr_dict(span)
        self.assertEqual(
            attrs["gen_ai.input.messages"].string_value,
            json.dumps(
                [
                    {"role": "system", "content": "You are an expert."},
                    {"role": "user", "content": "What is Langfuse?"},
                ],
                ensure_ascii=False,
            ),
        )
        self.assertEqual(
            attrs["gen_ai.output.messages"].string_value,
            json.dumps(
                [{"role": "assistant", "content": "Langfuse is..."}],
                ensure_ascii=False,
            ),
        )
        self.assertEqual(attrs["gen_ai.request.model"].string_value, "glm-5.2")
        self.assertEqual(attrs["gen_ai.operation.name"].string_value, "chat")

    def test_generation_string_input_wrapped_as_user_message(self):
        span = _make_span(
            "Chat",
            {
                "langfuse.observation.type": "generation",
                "langfuse.observation.input": "What is Langfuse?",
            },
        )
        _normalize_langfuse_span(span)
        attrs = _attr_dict(span)
        self.assertEqual(
            attrs["gen_ai.input.messages"].string_value,
            json.dumps(
                [{"role": "user", "content": "What is Langfuse?"}],
                ensure_ascii=False,
            ),
        )

    def test_tool_maps_tool_name(self):
        span = _make_span(
            "internet_search",
            {
                "langfuse.observation.type": "tool",
                "langfuse.observation.input": json.dumps(
                    {"query": "Langfuse", "max_results": 5}
                ),
            },
        )
        _normalize_langfuse_span(span)
        attrs = _attr_dict(span)
        self.assertEqual(attrs["gen_ai.tool.call.name"].string_value, "internet_search")
        self.assertEqual(attrs["gen_ai.tool.name"].string_value, "internet_search")
        self.assertEqual(attrs["gen_ai.operation.name"].string_value, "execute_tool")

    def test_tool_name_with_prefix_is_stripped(self):
        span = _make_span(
            "LangGraph:internet_search",
            {"langfuse.observation.type": "tool"},
        )
        _normalize_langfuse_span(span)
        attrs = _attr_dict(span)
        self.assertEqual(attrs["gen_ai.tool.call.name"].string_value, "internet_search")

    def test_tool_observation_promotes_arguments_and_result(self):
        """langfuse 原生 tool observation 的 input/output 应提升为 Tool 明细的 arguments/result。"""
        span = _make_span(
            "get_weather",
            {
                "langfuse.observation.type": "tool",
                "langfuse.observation.input": json.dumps({"city": "San Francisco"}),
                "langfuse.observation.output": json.dumps(
                    {"content": "It's always sunny in San Francisco!"}
                ),
            },
        )
        _normalize_langfuse_span(span)
        attrs = _attr_dict(span)
        self.assertEqual(
            attrs["gen_ai.tool.call.arguments"].string_value,
            json.dumps({"city": "San Francisco"}),
        )
        self.assertEqual(
            attrs["gen_ai.tool.call.result"].string_value,
            json.dumps({"content": "It's always sunny in San Francisco!"}),
        )

    def test_session_and_user_id_mapped(self):
        span = _make_span(
            "Chat",
            {
                "langfuse.observation.type": "generation",
                "session.id": "session-123",
                "user.id": "user-456",
            },
        )
        _normalize_langfuse_span(span)
        attrs = _attr_dict(span)
        self.assertEqual(attrs["gen_ai.conversation.id"].string_value, "session-123")
        self.assertEqual(attrs["gen_ai.user.id"].string_value, "user-456")

    def test_usage_still_mapped_independently(self):
        span = _make_span(
            "Chat",
            {
                "langfuse.observation.type": "generation",
                "langfuse.observation.usage_details": json.dumps(
                    {"input": 3, "output": 5, "total": 8}
                ),
            },
        )
        _normalize_langfuse_span(span)
        attrs = _attr_dict(span)
        self.assertEqual(attrs["gen_ai.usage.input_tokens"].int_value, 3)
        self.assertEqual(attrs["gen_ai.usage.output_tokens"].int_value, 5)
        self.assertEqual(attrs["gen_ai.usage.total_tokens"].int_value, 8)

    def test_span_with_existing_genai_attr_is_skipped(self):
        span = _make_span(
            "Chat",
            {
                "langfuse.observation.type": "generation",
                "gen_ai.request.model": "existing-model",
            },
        )
        _normalize_langfuse_span(span)
        attrs = _attr_dict(span)
        self.assertEqual(attrs["gen_ai.request.model"].string_value, "existing-model")
        self.assertNotIn("gen_ai.input.messages", attrs)

    def test_plain_span_gets_no_message_mapping(self):
        span = _make_span(
            "LangGraph",
            {"langfuse.observation.type": "span"},
        )
        _normalize_langfuse_span(span)
        attrs = _attr_dict(span)
        self.assertNotIn("gen_ai.input.messages", attrs)
        self.assertNotIn("gen_ai.tool.call.name", attrs)


class OpenInferenceTokenUsageTests(TestCase):
    """openinference 的 llm.token_count.* 提升为 gen_ai.usage.*。"""

    def test_token_count_promoted_to_genai_usage(self):
        span = _make_span(
            "ChatCompletion",
            {
                "llm.token_count.prompt": 802,
                "llm.token_count.completion": 376,
                "llm.token_count.total": 1178,
            },
        )
        _normalize_langfuse_span(span)
        attrs = _attr_dict(span)
        self.assertEqual(attrs["gen_ai.usage.input_tokens"].int_value, 802)
        self.assertEqual(attrs["gen_ai.usage.output_tokens"].int_value, 376)
        self.assertEqual(attrs["gen_ai.usage.total_tokens"].int_value, 1178)

    def test_total_summed_when_missing(self):
        span = _make_span(
            "ChatCompletion",
            {"llm.token_count.prompt": 340, "llm.token_count.completion": 194},
        )
        _normalize_langfuse_span(span)
        attrs = _attr_dict(span)
        self.assertEqual(attrs["gen_ai.usage.total_tokens"].int_value, 534)

    def test_usage_details_wins_over_token_count(self):
        """langfuse 原生 usage_details 已写值时不被 openinference 属性覆盖。"""
        span = _make_span(
            "ChatCompletion",
            {
                "langfuse.observation.usage_details": json.dumps({"input": 11}),
                "llm.token_count.prompt": 999,
            },
        )
        _normalize_langfuse_span(span)
        attrs = _attr_dict(span)
        self.assertNotIn("gen_ai.usage.input_tokens", attrs)
        self.assertNotIn("gen_ai.usage.total_tokens", attrs)
        self.assertEqual(
            json.loads(attrs["langfuse.observation.usage_details"].string_value),
            {"input": 11},
        )


class OpenInferenceIOMappingTests(TestCase):
    """openinference input.value / output.value 的信封分层提取。"""

    def test_plain_string_value_wrapped(self):
        span = _make_span("crewai-index-trace", {"input.value": "Analyze this code"})
        _normalize_langfuse_span(span)
        attrs = _attr_dict(span)
        self.assertEqual(
            attrs["gen_ai.input.messages"].string_value,
            json.dumps(
                [{"role": "user", "content": "Analyze this code"}], ensure_ascii=False
            ),
        )

    def test_langchain_envelope_picks_by_field(self):
        envelope = json.dumps(
            {
                "messages": [
                    {
                        "type": "human",
                        "data": {"content": "What is deep agents?"},
                    },
                    {
                        "type": "ai",
                        "data": {
                            "content": [{"type": "text", "text": "Deep agents are..."}]
                        },
                    },
                ]
            }
        )
        span = _make_span(
            "LangGraph", {"input.value": envelope, "output.value": envelope}
        )
        _normalize_langfuse_span(span)
        attrs = _attr_dict(span)
        self.assertEqual(
            attrs["gen_ai.input.messages"].string_value,
            json.dumps(
                [{"role": "user", "content": "What is deep agents?"}],
                ensure_ascii=False,
            ),
        )
        self.assertEqual(
            attrs["gen_ai.output.messages"].string_value,
            json.dumps(
                [{"role": "assistant", "content": "Deep agents are..."}],
                ensure_ascii=False,
            ),
        )

    def test_responses_api_envelope_extracts_output_text(self):
        """openai-agents 的 Responses API 信封：不得误抓顶层 text 格式配置的判别值。"""
        envelope = json.dumps(
            {
                "object": "response",
                "model": "gpt-6-sol",
                "text": {"format": {"type": "text"}},
                "output": [
                    {"type": "reasoning", "summary": [], "content": []},
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "Division by zero is undefined.",
                            }
                        ],
                    },
                ],
            }
        )
        span = _make_span("response", {"output.value": envelope})
        _normalize_langfuse_span(span)
        attrs = _attr_dict(span)
        message = json.loads(attrs["gen_ai.output.messages"].string_value)[0]
        self.assertEqual(message["content"], "Division by zero is undefined.")
        self.assertEqual(message["role"], "assistant")

    def test_langchain_constructor_envelope_nested_messages(self):
        """新版 openinference-langchain LLM span 信封：constructor 形态 + 双嵌套 messages，
        不得误抓判别字段 \"constructor\" 作为内容。"""
        envelope = json.dumps(
            {
                "messages": [
                    [
                        {
                            "lc": 1,
                            "type": "constructor",
                            "id": ["langchain", "schema", "messages", "SystemMessage"],
                            "kwargs": {
                                "content": "You are a research assistant.",
                                "type": "system",
                            },
                        },
                        {
                            "lc": 1,
                            "type": "constructor",
                            "id": ["langchain", "schema", "messages", "HumanMessage"],
                            "kwargs": {
                                "content": "What is the refund policy?",
                                "type": "human",
                            },
                        },
                    ]
                ]
            }
        )
        span = _make_span("ChatOpenAI", {"input.value": envelope})
        _normalize_langfuse_span(span)
        attrs = _attr_dict(span)
        self.assertEqual(
            attrs["gen_ai.input.messages"].string_value,
            json.dumps(
                [{"role": "user", "content": "What is the refund policy?"}],
                ensure_ascii=False,
            ),
        )

    def test_langchain_single_constructor_output(self):
        """output.value 是单条 constructor AIMessage（无 messages 包裹）时取 kwargs.content。"""
        envelope = json.dumps(
            {
                "lc": 1,
                "type": "constructor",
                "id": ["langchain", "schema", "messages", "AIMessage"],
                "kwargs": {"content": "How can I help?", "type": "ai"},
            }
        )
        span = _make_span("ChatOpenAI", {"output.value": envelope})
        _normalize_langfuse_span(span)
        attrs = _attr_dict(span)
        self.assertEqual(
            attrs["gen_ai.output.messages"].string_value,
            json.dumps(
                [{"role": "assistant", "content": "How can I help?"}],
                ensure_ascii=False,
            ),
        )

    def test_langchain_llmresult_generations_envelope(self):
        """LLMResult 信封：从 generations 项的 message 取内容；优先最后一组，跳过空 content。"""
        envelope = json.dumps(
            {
                "generations": [
                    [
                        {
                            "text": "",
                            "type": "ChatGeneration",
                            "message": {
                                "lc": 1,
                                "type": "constructor",
                                "kwargs": {"content": "", "type": "ai"},
                            },
                        }
                    ],
                    [
                        {
                            "type": "ChatGeneration",
                            "message": {
                                "lc": 1,
                                "type": "constructor",
                                "kwargs": {
                                    "content": "The final answer.",
                                    "type": "ai",
                                },
                            },
                        }
                    ],
                ]
            }
        )
        span = _make_span("ChatOpenAI", {"output.value": envelope})
        _normalize_langfuse_span(span)
        attrs = _attr_dict(span)
        self.assertEqual(
            attrs["gen_ai.output.messages"].string_value,
            json.dumps(
                [{"role": "assistant", "content": "The final answer."}],
                ensure_ascii=False,
            ),
        )

    def test_langchain_llmresult_empty_content_not_mapped(self):
        """纯 tool 调用轮（content 全空）不写 gen_ai.output.messages，不得误抓 constructor。"""
        envelope = json.dumps(
            {
                "generations": [
                    [
                        {
                            "text": "",
                            "type": "ChatGeneration",
                            "message": {
                                "lc": 1,
                                "type": "constructor",
                                "kwargs": {
                                    "content": "",
                                    "type": "ai",
                                    "tool_calls": [{"name": "search_docs"}],
                                },
                            },
                        }
                    ]
                ]
            }
        )
        span = _make_span("ChatOpenAI", {"output.value": envelope})
        _normalize_langfuse_span(span)
        attrs = _attr_dict(span)
        self.assertNotIn("gen_ai.output.messages", attrs)

    def test_plain_output_value_wrapped_as_assistant(self):
        """output.value 纯文本兜底包为 assistant（列表列取末元素 content，与 role 无关）。"""
        span = _make_span("ChatCompletion", {"output.value": "The answer is 42."})
        _normalize_langfuse_span(span)
        attrs = _attr_dict(span)
        self.assertEqual(
            attrs["gen_ai.output.messages"].string_value,
            json.dumps(
                [{"role": "assistant", "content": "The answer is 42."}],
                ensure_ascii=False,
            ),
        )

    def test_langfuse_observation_output_not_overwritten_by_output_value(self):
        """两个来源同时存在时，保留 generation 已写入的 gen_ai.output.messages。"""
        span = _make_span(
            "Chat",
            {
                "langfuse.observation.type": "generation",
                "langfuse.observation.output": json.dumps(
                    [{"role": "assistant", "content": "authoritative"}]
                ),
                "output.value": "fallback text",
            },
        )
        _normalize_langfuse_span(span)
        attrs = _attr_dict(span)
        self.assertEqual(
            attrs["gen_ai.output.messages"].string_value,
            json.dumps(
                [{"role": "assistant", "content": "authoritative"}], ensure_ascii=False
            ),
        )


class ProviderInferenceTests(TestCase):
    """gen_ai.provider.name 由模型名推断（参与定价发现，不能无条件写死 openai）。"""

    def _provider_for(self, model):
        span = _make_span(
            "Chat",
            {
                "langfuse.observation.type": "generation",
                "langfuse.observation.model.name": model,
            },
        )
        _normalize_langfuse_span(span)
        return _attr_dict(span)["gen_ai.provider.name"].string_value

    def test_prefix_slash_and_colon(self):
        self.assertEqual(self._provider_for("anthropic:claude-3-7-sonnet"), "anthropic")
        self.assertEqual(self._provider_for("openai/gpt-6-sol"), "openai")

    def test_bare_model_prefix_heuristic(self):
        self.assertEqual(self._provider_for("claude-3-5-sonnet"), "anthropic")
        self.assertEqual(self._provider_for("gemini-2.0-flash"), "gcp.gemini")
        self.assertEqual(self._provider_for("gpt-6-sol"), "openai")

    def test_unknown_provider_remains_unknown(self):
        span = _make_span(
            "Chat",
            {
                "langfuse.observation.type": "generation",
                "langfuse.observation.model.name": "private-model",
            },
        )
        _normalize_langfuse_span(span)
        self.assertNotIn("gen_ai.provider.name", _attr_dict(span))

    def test_openinference_llm_span_also_gets_provider(self):
        """openinference LLM span（只有 llm.model_name / openinference.span.kind）同样补齐模型与 provider。"""
        span = _make_span(
            "ChatCompletion",
            {
                "openinference.span.kind": "LLM",
                "llm.model_name": "gpt-6-sol",
            },
        )
        _normalize_langfuse_span(span)
        attrs = _attr_dict(span)
        self.assertEqual(attrs["gen_ai.request.model"].string_value, "gpt-6-sol")
        self.assertEqual(attrs["gen_ai.provider.name"].string_value, "openai")
        self.assertEqual(attrs["gen_ai.operation.name"].string_value, "chat")

    def test_openinference_agent_span_not_treated_as_generation(self):
        """非 LLM 的 openinference span（AGENT/CHAIN）不写 provider，有模型名也只提升模型。"""
        span = _make_span(
            "Captain Obvious",
            {"openinference.span.kind": "AGENT", "llm.model_name": "gpt-6-sol"},
        )
        _normalize_langfuse_span(span)
        attrs = _attr_dict(span)
        self.assertEqual(attrs["gen_ai.request.model"].string_value, "gpt-6-sol")
        self.assertNotIn("gen_ai.provider.name", attrs)
        self.assertNotIn("gen_ai.operation.name", attrs)

    def test_openinference_tool_span_maps_tool_call_fields(self):
        """openinference TOOL span（kind=TOOL + tool.name）补齐看板 Tool 明细字段。"""
        span = _make_span(
            "search_docs",
            {
                "openinference.span.kind": "TOOL",
                "tool.name": "search_docs",
                "input.value": "refund policy",
                "output.value": "Example result for: refund policy",
            },
        )
        _normalize_langfuse_span(span)
        attrs = _attr_dict(span)
        self.assertEqual(attrs["gen_ai.operation.name"].string_value, "execute_tool")
        self.assertEqual(attrs["gen_ai.tool.call.name"].string_value, "search_docs")
        self.assertEqual(attrs["gen_ai.tool.name"].string_value, "search_docs")
        self.assertEqual(
            attrs["gen_ai.tool.call.arguments"].string_value, "refund policy"
        )
        self.assertEqual(
            attrs["gen_ai.tool.call.result"].string_value,
            "Example result for: refund policy",
        )

    def test_openinference_tool_span_without_name_skipped(self):
        """langgraph 聚合 tools 节点 kind=TOOL 但无 tool.name，不写残缺 Tool 明细。"""
        span = _make_span(
            "tools",
            {
                "openinference.span.kind": "TOOL",
                "input.value": "refund policy",
            },
        )
        _normalize_langfuse_span(span)
        attrs = _attr_dict(span)
        self.assertNotIn("gen_ai.tool.call.name", attrs)
        self.assertNotIn("gen_ai.tool.call.arguments", attrs)

    def test_openinference_tool_result_envelope_unwrapped(self):
        """ToolMessage 信封的 output.value 应提纯为 content 而非原始 JSON。"""
        envelope = (
            '{"type": "tool", "data": {"content": "Example result for: refund policy", '
            '"name": "search_docs", "status": "success"}}'
        )
        span = _make_span(
            "search_docs",
            {
                "openinference.span.kind": "TOOL",
                "tool.name": "search_docs",
                "output.value": envelope,
            },
        )
        _normalize_langfuse_span(span)
        attrs = _attr_dict(span)
        self.assertEqual(
            attrs["gen_ai.tool.call.result"].string_value,
            "Example result for: refund policy",
        )


class TraceSemanticsNormalizationTests(SimpleTestCase):
    def test_real_sdk_cache_aliases_and_user_identity_survive_collector_sanitization(
        self,
    ):
        span = _make_span(
            "generation",
            {
                "langfuse.observation.type": "generation",
                "user.id": "end-user",
                "langfuse.observation.usage_details": '{"input":474,"output":62,"total":792,"input_cache_read":256}',
            },
        )
        _normalize_langfuse_span(span)
        attrs = _attr_dict(span)
        self.assertEqual(attrs["gen_ai.usage.input_tokens"].int_value, 730)
        self.assertEqual(attrs["gen_ai.usage.cache_read.input_tokens"].int_value, 256)
        self.assertEqual(attrs["langfuse.user.id"].string_value, "end-user")

    def test_mixed_attributes_fill_missing_fields_without_overwriting_explicit_values(
        self,
    ):
        span = _make_span(
            "Chat",
            {
                "langfuse.observation.type": "generation",
                "gen_ai.request.model": "explicit-model",
                "gen_ai.usage.input_tokens": 0,
                "langfuse.observation.model.name": "gpt-4o",
                "langfuse.observation.usage_details": json.dumps(
                    {"input": 100, "output": 3}
                ),
                "langfuse.observation.input": "question",
                "session.id": "s-one",
            },
        )
        _normalize_langfuse_span(span)
        attrs = _attr_dict(span)
        self.assertEqual(attrs["gen_ai.request.model"].string_value, "explicit-model")
        self.assertEqual(attrs["gen_ai.usage.input_tokens"].int_value, 0)
        self.assertNotIn("gen_ai.usage.output_tokens", attrs)
        self.assertNotIn("gen_ai.usage.total_tokens", attrs)
        self.assertEqual(
            json.loads(attrs["langfuse.observation.usage_details"].string_value),
            {"input": 100, "output": 3},
        )
        self.assertEqual(attrs["gen_ai.conversation.id"].string_value, "s-one")
        self.assertEqual(
            json.loads(attrs["gen_ai.input.messages"].string_value)[0]["content"],
            "question",
        )

    def test_embedding_retains_operation_and_raw_io(self):
        span = _make_span(
            "embed",
            {
                "langfuse.observation.type": "embedding",
                "langfuse.observation.model.name": "text-embedding-3-small",
                "langfuse.observation.input": '["a", "b"]',
            },
        )
        _normalize_langfuse_span(span)
        attrs = _attr_dict(span)
        self.assertEqual(attrs["gen_ai.operation.name"].string_value, "embeddings")
        self.assertEqual(attrs["langfuse.observation.input"].string_value, '["a", "b"]')
        self.assertNotIn("gen_ai.input.messages", attrs)

    def test_repeated_normalization_is_idempotent(self):
        span = _make_span(
            "Chat",
            {
                "langfuse.observation.type": "generation",
                "langfuse.observation.model.name": "gpt-4o",
                "langfuse.observation.usage_details": '{"input":3,"output":2}',
            },
        )
        _normalize_langfuse_span(span)
        once = span.SerializeToString()
        _normalize_langfuse_span(span)
        self.assertEqual(span.SerializeToString(), once)

    def test_bad_gzip_is_a_validation_error(self):
        valid = gzip.compress(ExportTraceServiceRequest().SerializeToString())
        crc = bytearray(valid)
        crc[-8] ^= 255
        damaged = [
            b"invalid-gzip-header" + valid,
            valid[:-3],
            bytes(crc),
            b"\x1f\x8b\x08\x00" + b"\x00" * 6 + b"\xff" * 16,
        ]
        for payload in damaged:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                normalize_langfuse_otlp_payload(payload, "gzip")

    def test_valid_gzip_round_trips(self):
        request = ExportTraceServiceRequest()
        request.resource_spans.add().scope_spans.add().spans.add().name = "root"
        result = normalize_langfuse_otlp_payload(
            gzip.compress(request.SerializeToString()), "gzip"
        )
        decoded = ExportTraceServiceRequest.FromString(gzip.decompress(result))
        self.assertEqual(decoded.resource_spans[0].scope_spans[0].spans[0].name, "root")
