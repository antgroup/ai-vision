import base64
import binascii
import gzip
import hashlib
import json
import logging
import zlib
from io import BytesIO

import requests
from django.conf import settings
from django.http import HttpResponse
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from apps.core.services.agent_installation import active_ingest_agents
from apps.core.services.ingest_token import IngestTokenError, build_ingest_token
from apps.core.services.key_cache import key_cache
from apps.core.services.signoz_integration import current_ingest_org_id
from apps.core.trace_query.semantics.sdk_metadata import (
    OTLP_PROVENANCE_KEYS,
    OTLP_SCOPE_NAME,
    OTLP_SCOPE_VERSION,
    OTLP_SDK_LANGUAGE,
    langfuse_metadata_is_json,
)
from apps.core.trace_query.semantics.usage import STANDARD, normalize_usage
from apps.core.trace_query.config import resolve_configured_agent_product
from apps.core.trace_query.routing import get_trace_datasource
from apps.core.trace_query.types import TraceQueryServiceError

logger = logging.getLogger(__name__)

_MODEL_ATTRIBUTE = "langfuse.observation.model.name"
_USAGE_ATTRIBUTE = "langfuse.observation.usage_details"
_INPUT_ATTRIBUTE = "langfuse.observation.input"
_OUTPUT_ATTRIBUTE = "langfuse.observation.output"
_TYPE_ATTRIBUTE = "langfuse.observation.type"
_SESSION_ATTRIBUTE = "session.id"
_USER_ATTRIBUTE = "user.id"

# openinference 标准属性（CrewAI / Google ADK / Google GenAI / OpenAI Agents / Pydantic AI 等插桩产生）
_OI_INPUT_VALUE = "input.value"
_OI_OUTPUT_VALUE = "output.value"
_OI_SPAN_KIND = "openinference.span.kind"
_OI_MODEL_NAME = "llm.model_name"
_OI_TOOL_NAME = "tool.name"

# openinference token 用量属性（以 int 记录，非 langfuse.observation.usage_details）
_OI_TOKEN_PROMPT = "llm.token_count.prompt"
_OI_TOKEN_COMPLETION = "llm.token_count.completion"
_OI_TOKEN_TOTAL = "llm.token_count.total"

# langfuse observation 类型（见 langfuse/_client/constants.py）
_GENERATION_TYPES = frozenset({"generation", "embedding"})
_TOOL_TYPE = "tool"

_USAGE_ALIASES = {
    "gen_ai.usage.input_tokens": ("input", "prompt_tokens"),
    "gen_ai.usage.output_tokens": ("output", "completion_tokens"),
    "gen_ai.usage.total_tokens": ("total",),
}

# openinference token 用量 → gen_ai.usage.*（后端 usage 只读 gen_ai.usage.*，此处补齐）
_OI_TOKEN_USAGE_MAP = {
    "gen_ai.usage.input_tokens": _OI_TOKEN_PROMPT,
    "gen_ai.usage.output_tokens": _OI_TOKEN_COMPLETION,
    "gen_ai.usage.total_tokens": _OI_TOKEN_TOTAL,
}

# gen_ai.provider.name 由模型名推断：先取 litellm / init_chat_model 风格的 ``provider/model``
# 或 ``provider:model`` 前缀，再按已知模型名前缀识别；无法判定时保留 unknown。
# 该字段参与查询侧的模型定价发现（按 model+provider 分组），错标会污染配价，故不能无条件写死。
_PROVIDER_BY_PREFIX = {
    "ai21": "ai21",
    "anthropic": "anthropic",
    "aws": "aws.bedrock",
    "azure": "azure",
    "azure_openai": "azure",
    "bedrock": "aws.bedrock",
    "cohere": "cohere",
    "deepseek": "deepseek",
    "gemini": "gcp.gemini",
    "google": "gcp.gemini",
    "groq": "groq",
    "mistral": "mistral_ai",
    "mistral_ai": "mistral_ai",
    "ollama": "ollama",
    "openai": "openai",
    "qwen": "qwen",
    "vertex": "gcp.vertex_ai",
    "vertex_ai": "gcp.vertex_ai",
}

# 无前缀时的裸模型名前缀 → provider（只按 startswith 匹配，避免子串误判）
_PROVIDER_BY_MODEL_PREFIX = (
    ("claude", "anthropic"),
    ("deepseek", "deepseek"),
    ("gemini", "gcp.gemini"),
    ("gpt", "openai"),
    ("llama", "meta"),
    ("mistral", "mistral_ai"),
    ("o1", "openai"),
    ("o3", "openai"),
    ("o4", "openai"),
    ("qwen", "qwen"),
    ("text-embedding", "openai"),
)

_PROVIDER_FALLBACK = None


def _json_response(message, status):
    return HttpResponse(
        json.dumps({"error": message}),
        content_type="application/json",
        status=status,
    )


def _basic_credentials(request):
    header = request.headers.get("Authorization", "")
    if not header.startswith("Basic "):
        return None, None
    try:
        decoded = base64.b64decode(header[6:], validate=True).decode("utf-8")
        public_key, secret_key = decoded.split(":", 1)
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None, None
    return public_key, secret_key


def _string_attribute(attributes, key):
    for item in attributes:
        if item.key == key and item.value.WhichOneof("value") == "string_value":
            return item.value.string_value
    return None


def _number_attribute(attributes, key):
    """读取数值属性（openinference 的 llm.token_count.* 以 int_value 上报）。"""
    for item in attributes:
        if item.key != key:
            continue
        which = item.value.WhichOneof("value")
        if which == "int_value":
            return item.value.int_value
        if which == "string_value":
            try:
                return int(item.value.string_value)
            except (TypeError, ValueError):
                return None
        return None
    return None


def _set_attribute(attributes, key, **value):
    for item in attributes:
        if item.key == key:
            item.value.Clear()
            for field, data in value.items():
                setattr(item.value, field, data)
            return
    item = attributes.add()
    item.key = key
    for field, data in value.items():
        setattr(item.value, field, data)


def _remove_provenance(attributes):
    # These are derived from the OTLP envelope, never trusted from a client's
    # flattened attributes. Remove duplicates too, before writing our values.
    for index in range(len(attributes) - 1, -1, -1):
        if attributes[index].key in OTLP_PROVENANCE_KEYS:
            del attributes[index]


def _normalize_messages(raw: str | None) -> str | None:
    """把 langfuse observation 的 input/output 规范化为 gen_ai.input.messages 需要的消息数组 JSON。

    FrameworkDashboard 的 extractPrompt 期望该字段是 JSON 数组（含 role/content 的消息），
    取 role=user 的消息内容作为 UserPrompt 明细。
    """
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        parsed = raw

    if isinstance(parsed, list):
        # 已是标准消息数组（元素含 role）
        if parsed and all(isinstance(m, dict) and m.get("role") for m in parsed):
            return json.dumps(parsed, ensure_ascii=False)
        # 空数组
        if not parsed:
            return None
        # 非消息结构数组 → 包装成单条 user 消息
        return json.dumps([{"role": "user", "content": parsed}], ensure_ascii=False)

    if isinstance(parsed, dict):
        # 带 messages 字段的容器
        messages = parsed.get("messages")
        if (
            isinstance(messages, list)
            and messages
            and all(isinstance(m, dict) and m.get("role") for m in messages)
        ):
            return json.dumps(messages, ensure_ascii=False)
        # 其他 dict（如工具入参）不适合作为用户消息 → 不映射
        return None

    # 字符串 / 标量 → 包装成单条 user 消息
    if isinstance(parsed, str):
        return json.dumps([{"role": "user", "content": parsed}], ensure_ascii=False)
    return None


# LangChain 序列化消息的 type 判别字段 → gen_ai role
_LC_MESSAGE_ROLES = {
    "human": "user",
    "chat": "user",
    "ai": "assistant",
    "tool": "tool",
    "function": "tool",
    "system": "system",
}


def _openinference_block_text(content):
    """从消息 content（字符串或内容块列表）提取可读文本。

    只取文本块（无 type 或 type 为 ``text`` / ``output_text``），忽略推理、工具调用等块。
    兼容 LangChain 消息块与 OpenAI Responses API 的 output_text 块。
    """
    if isinstance(content, str):
        return content.strip() or None
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") in (
                None,
                "text",
                "output_text",
            ):
                text = block.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text)
        joined = "\n".join(p.strip() for p in parts if p and p.strip())
        return joined or None
    return None


def _langchain_messages(parsed):
    """把 LangChain 序列化消息转成 ``[{role,content}]``。

    openinference-instrumentation-langchain 的信封有两代形态：
      - 旧版 ``{"messages":[{"type":role,"data":{...}}]}``（链级 span）
      - 新版 constructor ``{"messages":[[{"type":"constructor","kwargs":{"type":role,...}}]]}``
        （LLM span，且 messages 可能多包一层列表）
    真实文本在 ``data.content`` / ``kwargs.content``（AI 消息还可能是 text 块列表），
    constructor 的 ``type`` 是判别字段不是内容，直接递归提取会误抓 "constructor"。
    """
    raw_messages = parsed.get("messages") if isinstance(parsed, dict) else None
    if not isinstance(raw_messages, list):
        return None
    messages = []
    for item in raw_messages:
        # LLM span 信封把消息列表再包了一层：[[msg, ...]]
        if isinstance(item, list):
            for nested in item:
                converted = _langchain_message(nested)
                if converted:
                    messages.append(converted)
            continue
        converted = _langchain_message(item)
        if converted:
            messages.append(converted)
    return messages or None


def _langchain_message(item):
    if not isinstance(item, dict):
        return None
    data = item.get("data") if isinstance(item.get("data"), dict) else item
    message_type = data.get("type") or item.get("type")
    if message_type == "constructor":
        kwargs = data.get("kwargs") if isinstance(data.get("kwargs"), dict) else {}
        message_type = kwargs.get("type")
        content = _openinference_block_text(kwargs.get("content"))
    else:
        content = _openinference_block_text(data.get("content"))
    role = _LC_MESSAGE_ROLES.get(message_type)
    if role and content:
        return {"role": role, "content": content}
    return None


def _responses_api_output_text(parsed):
    """从 OpenAI Responses API 信封（``object=response``，含 ``output`` 数组）提取最终回答文本。

    openinference-instrumentation-openai-agents 把 response span 的 output.value 存成
    Responses API 对象：``output[]`` 里混有 reasoning / function_call 等项，真实回答在
    ``type=="message"`` 项的 ``content[].text``（块 type 为 ``output_text``）。
    通用递归会误抓顶层 ``text`` 格式配置的判别值（如 "text"），故需此专用分支。
    """
    if not (isinstance(parsed, dict) and parsed.get("object") == "response"):
        return None
    items = parsed.get("output")
    if not (
        isinstance(items, list) and items and all(isinstance(i, dict) for i in items)
    ):
        return None
    texts = []
    for item in items:
        if item.get("type") != "message":
            continue
        text = _openinference_block_text(item.get("content"))
        if text:
            texts.append(text)
    return "\n".join(texts) or None


def _single_message(text: str, role: str = "user") -> str:
    """包成 ``gen_ai.*.messages`` 需要的单条消息数组 JSON。

    前端两个消费方：trace 列表 Input/Output 列走 extractMessageContent（取末元素 content，与 role 无关）；
    FrameworkDashboard 的 UserPrompt 走 extractPrompt（仅读 input.messages 且按 role=="user" 取）。
    故 input 用 role="user"（两处都能取到），output 用 role="assistant"（列展示不受影响、语义更正）。
    """
    return json.dumps([{"role": role, "content": text}], ensure_ascii=False)


def _normalize_openinference_io(raw: str | None, field: str = "input") -> str | None:
    """从 openinference 的 input.value / output.value 提取可读内容，规范化为消息数组 JSON。

    openinference 各插桩包的 input.value / output.value 信封结构不统一，故按信封分层处理：
      1. LangChain 消息信封 ``{"messages":[{"type":role,"data":...}]}`` → 据 field 取末条 user/assistant
      2. OpenAI Responses API 信封 ``object=response`` → 只取 message 项的 output_text
      3. 其余 → 递归挑首个可读文本（通用兜底）
    """
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        parsed = raw

    if isinstance(parsed, dict):
        messages = _langchain_messages(parsed)
        if messages:
            want = "assistant" if field == "output" else "user"
            selected = [m for m in messages if m["role"] == want] or messages
            return json.dumps(selected[-1:], ensure_ascii=False)

        # 单条 LangChain constructor 消息（openinference-langchain LLM span 的 output.value）
        single = _langchain_message(parsed)
        if single:
            return json.dumps([single], ensure_ascii=False)

        # LLMResult 信封 ``{"generations":[[{"message":<constructor>,...}]]}``（部分插桩版本）
        generations = parsed.get("generations")
        if isinstance(generations, list):
            for group in reversed(generations):
                items = group if isinstance(group, list) else [group]
                for gen in reversed(items):
                    if not isinstance(gen, dict):
                        continue
                    message = gen.get("message")
                    extracted = _langchain_message(
                        message if isinstance(message, dict) else gen
                    )
                    if extracted:
                        return json.dumps([extracted], ensure_ascii=False)

        # LangChain lc 序列化对象（含纯 tool 调用轮的空 content）提不出可读文本时直接不映射，
        # 避免通用兜底递归误抓 type/constructor 等判别字段当内容
        is_lc_serialized = (
            parsed.get("lc") == 1
            or parsed.get("type") == "constructor"
            or "generations" in parsed
        )
        if is_lc_serialized:
            return None

        # OpenAI Responses API 信封（openai-agents 的 response span）：只取 message 项的 output_text
        answer = _responses_api_output_text(parsed)
        if answer:
            return _single_message(answer, "assistant" if field == "output" else "user")

    def _pick_text(obj):
        """递归提取对象中的可读文本片段。"""
        if isinstance(obj, str):
            return obj.strip() or None
        if isinstance(obj, list):
            texts = [t for item in obj for t in [_pick_text(item)] if t]
            return texts[0] if texts else None
        if isinstance(obj, dict):
            # 优先取常见文本字段
            for key in ("content", "raw", "text", "description", "task", "message"):
                if key in obj:
                    t = _pick_text(obj[key])
                    if t:
                        return t
            # 否则遍历所有值
            for v in obj.values():
                t = _pick_text(v)
                if t:
                    return t
        return None

    text = _pick_text(parsed)
    if not text:
        return None
    return _single_message(text, "assistant" if field == "output" else "user")


def _extract_tool_name(span) -> str | None:
    """从 tool observation 提取工具名。优先 span.name，回退 input 里的 name/tool_name 字段。"""
    name = (span.name or "").strip()
    if name:
        # 去掉常见前缀（如 "LangGraph:internet_search"、"tool:xxx"），取最后一段
        if ":" in name:
            parts = [p.strip() for p in name.split(":") if p.strip()]
            if parts:
                name = parts[-1]
        return name or None

    input_raw = _string_attribute(span.attributes, _INPUT_ATTRIBUTE)
    if not input_raw:
        return None
    try:
        parsed = json.loads(input_raw)
    except (TypeError, ValueError):
        return input_raw.strip() or None
    if isinstance(parsed, dict):
        for key in ("name", "tool_name", "tool"):
            value = parsed.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _tool_message_text(raw):
    """openinference TOOL span 的 output.value 常是 ToolMessage 信封
    ``{"type": "tool", "data": {"content": ...}}``，提纯 content；其余形态原样返回。"""
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return raw
    if isinstance(parsed, dict) and parsed.get("type") == "tool":
        data = parsed.get("data")
        if isinstance(data, dict):
            content = _openinference_block_text(data.get("content"))
            if content:
                return content
    return raw


def _event_content(span, event_names):
    """取 span 上首个匹配事件里 body 合并进来的 ``content`` 属性值。"""
    for event in span.events:
        if event.name in event_names:
            content = _string_attribute(event.attributes, "content")
            if content:
                return content
    return None


_GR_USER_MESSAGE_EVENTS = ("guardrails.user_message",)
_GR_LLM_PROMPT_EVENTS = (
    "gen_ai.content.prompt",
    "gen_ai.user.message",
    "gen_ai.system.message",
)
_GR_LLM_COMPLETION_EVENTS = (
    "gen_ai.content.completion",
    "gen_ai.assistant.message",
    "gen_ai.choice",
)


def _normalize_guardrails_span(span):
    """NeMo Guardrails 原生 OTel：内容只落在 span 事件里（需 enable_content_capture），
    而查询侧只读 span 属性，故把事件内容提升为 gen_ai.input/output.messages：
      - guardrails.request：user_message 事件 → gen_ai.input.messages（干净的原始用户输入）
      - LLM span：gen_ai.content.prompt/completion 事件 → gen_ai.input/output.messages
    仅在对应属性缺失时写入，不覆盖其它来源已有的值。
    """
    name = span.name or ""
    if name.startswith("guardrails."):
        if not _string_attribute(span.attributes, "gen_ai.input.messages"):
            content = _event_content(span, _GR_USER_MESSAGE_EVENTS)
            if content:
                _set_attribute(
                    span.attributes,
                    "gen_ai.input.messages",
                    string_value=_single_message(content, "user"),
                )
        return

    if _string_attribute(span.attributes, "gen_ai.request.model"):
        if not _string_attribute(span.attributes, "gen_ai.input.messages"):
            content = _event_content(span, _GR_LLM_PROMPT_EVENTS)
            if content:
                _set_attribute(
                    span.attributes,
                    "gen_ai.input.messages",
                    string_value=_normalize_messages(content) or content,
                )
        if not _string_attribute(span.attributes, "gen_ai.output.messages"):
            content = _event_content(span, _GR_LLM_COMPLETION_EVENTS)
            if content:
                _set_attribute(
                    span.attributes,
                    "gen_ai.output.messages",
                    string_value=_normalize_messages(content) or content,
                )


def _apply_openinference_io(span):
    """openinference 的 input.value / output.value → gen_ai.input/output.messages。

    CrewAI / Google ADK / Google GenAI / OpenAI Agents / Pydantic AI 等经 openinference 插桩的
    框架只写 openinference 标准属性，不写 langfuse.observation.*；而查询侧只读 gen_ai.*，
    故在此提取可读内容补齐。仅在目标缺失时写入，不覆盖更精确的来源。
    """
    oi_input = _string_attribute(span.attributes, _OI_INPUT_VALUE)
    has_langfuse_input = _string_attribute(span.attributes, _INPUT_ATTRIBUTE)
    if (
        oi_input
        and not has_langfuse_input
        and not _string_attribute(span.attributes, "gen_ai.input.messages")
    ):
        input_messages = _normalize_openinference_io(oi_input, field="input")
        if input_messages is not None:
            _set_missing_attribute(
                span.attributes,
                "gen_ai.input.messages",
                source=_OI_INPUT_VALUE,
                string_value=input_messages,
            )

    oi_output = _string_attribute(span.attributes, _OI_OUTPUT_VALUE)
    if oi_output and not _string_attribute(span.attributes, "gen_ai.output.messages"):
        output_messages = _normalize_openinference_io(oi_output, field="output")
        if output_messages is not None:
            _set_missing_attribute(
                span.attributes,
                "gen_ai.output.messages",
                source=_OI_OUTPUT_VALUE,
                string_value=output_messages,
            )


def _attribute_values(attributes):
    values = {}
    for item in attributes:
        kind = item.value.WhichOneof("value")
        if kind in {"string_value", "int_value", "double_value", "bool_value"}:
            values[item.key] = getattr(item.value, kind)
    return values


def _set_missing_attribute(attributes, key, *, source, **value):
    """Explicit source fields win, including zero and empty values."""
    if any(item.key == key for item in attributes):
        return
    _set_attribute(attributes, key, **value)
    raw = _string_attribute(attributes, "aivision.normalization.sources")
    try:
        sources = json.loads(raw) if raw else {}
    except (ValueError, TypeError):
        sources = {}
    if not isinstance(sources, dict):
        sources = {}
    sources[key] = source
    _set_attribute(
        attributes,
        "aivision.normalization.sources",
        string_value=json.dumps(sources, sort_keys=True),
    )


def _normalize_token_usage(span):
    """Promote the shared inclusive usage contract without changing raw usage."""
    usage = normalize_usage(_attribute_values(span.attributes))
    for field, targets in STANDARD.items():
        value = usage[field]
        if value is not None:
            _set_missing_attribute(
                span.attributes,
                targets[0],
                source=usage["source"].get(field, "derived"),
                int_value=value,
            )


def _infer_provider(model: str | None) -> str | None:
    """Infer only recognized model prefixes; unknown providers stay unknown."""
    name = (model or "").strip().lower()
    if not name:
        return _PROVIDER_FALLBACK

    for separator in ("/", ":"):
        if separator in name:
            prefix = name.split(separator, 1)[0].strip()
            provider = _PROVIDER_BY_PREFIX.get(prefix)
            if provider:
                return provider
            name = name.split(separator, 1)[1].strip() or name
            break

    for hint, provider in _PROVIDER_BY_MODEL_PREFIX:
        if name.startswith(hint):
            return provider
    return _PROVIDER_FALLBACK


def _normalize_billing_metadata(attributes):
    """Promote explicit connection metadata on this model call."""
    fields = {
        "langfuse.observation.metadata.aivisionBillingMode": "aivision.billing.mode",
        "langfuse.observation.metadata.aivisionBillingProvider": "gen_ai.provider.name",
    }
    values = {key: _string_attribute(attributes, key) for key in fields}
    if all(value is None for value in values.values()):
        return
    decode = langfuse_metadata_is_json(_attribute_values(attributes)) is True
    for key, target in fields.items():
        value = values[key]
        if value is None:
            continue
        if decode:
            # These fields accept only JSON strings. Skip other JSON types
            # before decoding so nested metadata cannot reject the OTLP batch.
            if not value.lstrip().startswith('"'):
                continue
            try:
                value = json.loads(value)
            except ValueError:
                continue
        if isinstance(value, str) and 0 < len(value) <= 128 and not any(ord(c) < 32 for c in value):
            _set_missing_attribute(attributes, target, source=key, string_value=value)


def _normalize_langfuse_span(span):
    obs_type = _string_attribute(span.attributes, _TYPE_ATTRIBUTE)
    oi_type = (_string_attribute(span.attributes, _OI_SPAN_KIND) or "").strip().lower()
    normalized_type = (obs_type or oi_type).strip().lower()
    is_embedding = normalized_type == "embedding"
    is_generation = normalized_type == "generation" or oi_type == "llm"
    model = _string_attribute(span.attributes, _MODEL_ATTRIBUTE) or _string_attribute(
        span.attributes, _OI_MODEL_NAME
    )
    if model:
        _set_missing_attribute(
            span.attributes,
            "gen_ai.request.model",
            source=(
                _MODEL_ATTRIBUTE
                if _string_attribute(span.attributes, _MODEL_ATTRIBUTE)
                else _OI_MODEL_NAME
            ),
            string_value=model,
        )

    if is_generation or is_embedding:
        _normalize_billing_metadata(span.attributes)
        _set_missing_attribute(
            span.attributes,
            "gen_ai.operation.name",
            source=_TYPE_ATTRIBUTE if obs_type else _OI_SPAN_KIND,
            string_value="embeddings" if is_embedding else "chat",
        )
        provider = _infer_provider(
            _string_attribute(span.attributes, "gen_ai.request.model")
        )
        if provider:
            _set_missing_attribute(
                span.attributes,
                "gen_ai.provider.name",
                source="model_prefix_inference",
                string_value=provider,
            )

    if not is_embedding:
        for source, target in (
            (_INPUT_ATTRIBUTE, "gen_ai.input.messages"),
            (_OUTPUT_ATTRIBUTE, "gen_ai.output.messages"),
        ):
            if is_generation:
                messages = _normalize_messages(
                    _string_attribute(span.attributes, source)
                )
                if messages is not None:
                    if source == _OUTPUT_ATTRIBUTE:
                        parsed = json.loads(messages)
                        for message in parsed:
                            if message.get("role") == "user":
                                message["role"] = "assistant"
                        messages = json.dumps(parsed, ensure_ascii=False)
                    _set_missing_attribute(
                        span.attributes, target, source=source, string_value=messages
                    )
        _apply_openinference_io(span)

    tool_name = (
        _extract_tool_name(span)
        if obs_type
        else _string_attribute(span.attributes, _OI_TOOL_NAME)
    )
    if normalized_type == "tool" and tool_name:
        _set_missing_attribute(
            span.attributes,
            "gen_ai.operation.name",
            source=_TYPE_ATTRIBUTE if obs_type else _OI_SPAN_KIND,
            string_value="execute_tool",
        )
        if tool_name:
            for target in ("gen_ai.tool.call.name", "gen_ai.tool.name"):
                _set_missing_attribute(
                    span.attributes,
                    target,
                    source="span.name" if obs_type else _OI_TOOL_NAME,
                    string_value=tool_name,
                )
        for source, target in (
            (
                (_INPUT_ATTRIBUTE if obs_type else _OI_INPUT_VALUE),
                "gen_ai.tool.call.arguments",
            ),
            (
                (_OUTPUT_ATTRIBUTE if obs_type else _OI_OUTPUT_VALUE),
                "gen_ai.tool.call.result",
            ),
        ):
            raw = _string_attribute(span.attributes, source)
            if raw is not None:
                _set_missing_attribute(
                    span.attributes,
                    target,
                    source=source,
                    string_value=(
                        _tool_message_text(raw) if target.endswith("result") else raw
                    ),
                )

    # The collector strips untrusted user.id before stamping platform identity.
    # Keep the SDK's application end-user in a distinct, non-authentication key.
    source_user_id = _string_attribute(span.attributes, _USER_ATTRIBUTE)
    if source_user_id:
        _set_missing_attribute(
            span.attributes,
            "langfuse.user.id",
            source=_USER_ATTRIBUTE,
            string_value=source_user_id,
        )
    for candidates, target in (
        (("langfuse.session.id", _SESSION_ATTRIBUTE), "gen_ai.conversation.id"),
        (("langfuse.user.id", _USER_ATTRIBUTE), "gen_ai.user.id"),
    ):
        for source in candidates:
            value = _string_attribute(span.attributes, source)
            if value:
                _set_missing_attribute(
                    span.attributes, target, source=source, string_value=value
                )
                break
    _normalize_token_usage(span)


def normalize_langfuse_otlp_payload(
    payload, content_encoding, *, agent_product=None, agent=None
):
    encoding = (content_encoding or "identity").strip().lower()
    if encoding not in {"", "identity", "gzip"}:
        raise ValueError("unsupported content encoding")

    try:
        if encoding == "gzip":
            limit = settings.AIVISION_OTLP_MEDIA_REQUEST_MAX_BYTES
            with gzip.GzipFile(fileobj=BytesIO(payload)) as stream:
                decoded = stream.read(limit + 1)
            if len(decoded) > limit:
                raise ValueError("decompressed OTLP payload exceeds limit")
        else:
            decoded = payload
    except (OSError, EOFError, zlib.error) as exc:
        raise ValueError("invalid gzip OTLP payload") from exc
    if not decoded:
        raise ValueError("empty OTLP payload")
    request = ExportTraceServiceRequest()
    try:
        request.ParseFromString(decoded)
    except Exception as exc:
        raise ValueError("invalid OTLP payload") from exc

    if agent is not None:
        datasource = get_trace_datasource(
            agent.space,
            agent_product=agent.agent_type.agent_product,
            agent_uuid=agent.uuid,
            source_system="signoz",
        )
        agent_product = (
            resolve_configured_agent_product(dict(datasource.config or {}))
            if datasource
            else ""
        )
        if not agent_product:
            raise TraceQueryServiceError(
                "Native OTLP ingestion requires a configured SigNoz agent_product_filter",
                3038,
            )

    for resource_spans in request.resource_spans:
        resource_attributes = resource_spans.resource.attributes
        _remove_provenance(resource_attributes)
        sdk_language = _string_attribute(resource_attributes, "telemetry.sdk.language")
        if agent_product:
            # The supported storage image indexes this legacy resource key and
            # otherwise defaults a plain SDK resource to Codex. Stamp only the
            # authenticated Agent's datasource filter; remove client duplicates.
            attributes = resource_spans.resource.attributes
            for index in range(len(attributes) - 1, -1, -1):
                if attributes[index].key == "ant.agent.product":
                    del attributes[index]
            _set_attribute(attributes, "ant.agent.product", string_value=agent_product)
        for scope_spans in resource_spans.scope_spans:
            for span in scope_spans.spans:
                _remove_provenance(span.attributes)
                for key, value in (
                    (OTLP_SCOPE_NAME, scope_spans.scope.name),
                    (OTLP_SCOPE_VERSION, scope_spans.scope.version),
                    (OTLP_SDK_LANGUAGE, sdk_language),
                ):
                    if value:
                        _set_attribute(span.attributes, key, string_value=value)
                _normalize_langfuse_span(span)
                _normalize_guardrails_span(span)

    normalized = request.SerializeToString()
    return gzip.compress(normalized) if encoding == "gzip" else normalized


def _upstream_endpoint():
    # Prefer the network-internal forward target; the public ingest endpoint is
    # only guaranteed reachable from outside the docker network, so falling back
    # to it keeps single-host deployments working without breaking containers.
    endpoint = (
        settings.AIVISION_OTLP_UPSTREAM_ENDPOINT
        or settings.AIVISION_OTLP_INGEST_ENDPOINT
        or ""
    ).rstrip("/")
    if not endpoint:
        return None
    return endpoint if endpoint.endswith("/v1/traces") else f"{endpoint}/v1/traces"


def forward_otlp_traces(
    upstream, payload, ingest_token, *, content_type, content_encoding=None
):
    headers = {
        "Authorization": f"Bearer {ingest_token}",
        "Content-Type": content_type,
    }
    if content_encoding:
        headers["Content-Encoding"] = content_encoding
    return requests.post(upstream, data=payload, headers=headers, timeout=30)


class LangfuseOtelTraceView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        agent_uuid, secret_key = _basic_credentials(request)
        if not agent_uuid or not secret_key:
            return _json_response("invalid Basic credentials", 401)

        key_hash = hashlib.sha256(secret_key.encode()).hexdigest()
        space_key = key_cache.get_space_key(key_hash)
        if space_key is None:
            return _json_response("invalid Langfuse credentials", 401)
        key_cache.mark_space_key_used(space_key.id)

        agent = active_ingest_agents(space_key.space).filter(uuid=agent_uuid).first()
        if agent is None:
            return _json_response("agent not found", 404)

        # Limit only this ingestion endpoint; do not relax Django's global
        # request budget for unrelated endpoints. Base64 expands file bytes.
        max_body = settings.AIVISION_OTLP_MEDIA_REQUEST_MAX_BYTES
        length = request.META.get("CONTENT_LENGTH", "")
        if not length.isdigit() or int(length) > max_body:
            return _json_response("OTLP request exceeds media ingestion limit", 413)
        body = request.stream.read(int(length))
        if len(body) != int(length):
            return _json_response("incomplete OTLP request", 400)
        try:
            payload = normalize_langfuse_otlp_payload(
                body,
                request.headers.get("Content-Encoding"),
                agent=agent,
            )
        except ValueError:
            return _json_response("unsupported or invalid OTLP payload", 400)
        except TraceQueryServiceError as exc:
            return _json_response(exc.message, 503)

        binding = agent.observation_subject_binding
        key_id = space_key.key_alias or str(space_key.id)
        try:
            ingest_token, _ = build_ingest_token(
                space_uuid=space_key.space.uuid,
                org_id=current_ingest_org_id(),
                subject_id=binding.subject_id,
                key_id=key_id,
                agent_uuid=agent.uuid,
                agent_product=agent.agent_product,
                ttl_seconds=settings.AIVISION_INGEST_TOKEN_TTL_SECONDS,
            )
        except IngestTokenError:
            logger.exception("Failed to mint Langfuse compatibility ingest token")
            return _json_response("ingest token unavailable", 503)

        upstream = _upstream_endpoint()
        if upstream is None:
            return _json_response("OTLP ingest endpoint is not configured", 503)

        try:
            upstream_response = forward_otlp_traces(
                upstream,
                payload,
                ingest_token,
                content_type=request.headers.get(
                    "Content-Type", "application/x-protobuf"
                ),
                content_encoding=request.headers.get("Content-Encoding"),
            )
        except requests.RequestException:
            logger.exception("Langfuse OTLP compatibility upstream request failed")
            return _json_response("OTLP upstream unavailable", 502)

        return HttpResponse(
            upstream_response.content,
            status=upstream_response.status_code,
            content_type=upstream_response.headers.get(
                "Content-Type", "application/x-protobuf"
            ),
        )
