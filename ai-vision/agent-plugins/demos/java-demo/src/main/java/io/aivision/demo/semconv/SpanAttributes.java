package io.aivision.demo.semconv;

import io.opentelemetry.api.common.AttributeKey;

/**
 * Span attribute constants derived from docs/semconv/attribute_groups.yaml and spans.yaml.
 *
 * Covers three namespaces: gen_ai.*, agentic.*, ant.*
 */
public final class SpanAttributes {

    private SpanAttributes() {}

    // ── gen_ai.span.kind values (spans.yaml) ──

    public static final String SPAN_KIND_CHAT = "CHAT";
    public static final String SPAN_KIND_LLM = "LLM";
    public static final String SPAN_KIND_TOOL = "TOOL";
    public static final String SPAN_KIND_AGENT = "AGENT";
    public static final String SPAN_KIND_MCP = "MCP";

    // ── gen_ai.operation.name values ──

    public static final String OP_INVOKE_AGENT = "invoke_agent";
    public static final String OP_CHAT = "chat";
    public static final String OP_EXECUTE_TOOL = "execute_tool";

    // ── agentic.tool.type values ──

    public static final String TOOL_TYPE_BUILTIN = "builtin";
    public static final String TOOL_TYPE_MCP = "mcp";
    public static final String TOOL_TYPE_SKILL = "skill";

    // ── Ant Identity (optional, at least one should be set) ──

    /** 用户标识，如 "user-001"。可选。 */
    public static final AttributeKey<String> ANT_USERNAME =
            AttributeKey.stringKey("ant.username");

    public static final AttributeKey<String> ANT_AGENT_SCENE =
            AttributeKey.stringKey("ant.agent.scene");

    /** 业务用户 ID，用于标记终端用户身份。可选。 */
    public static final AttributeKey<String> ANT_UID =
            AttributeKey.stringKey("ant.uid");

    // ── GenAI Core ──

    public static final AttributeKey<String> GEN_AI_SPAN_KIND =
            AttributeKey.stringKey("gen_ai.span.kind");

    public static final AttributeKey<String> GEN_AI_OPERATION_NAME =
            AttributeKey.stringKey("gen_ai.operation.name");

    public static final AttributeKey<String> GEN_AI_SYSTEM =
            AttributeKey.stringKey("gen_ai.system");

    // ── GenAI Conversation ──

    public static final AttributeKey<String> GEN_AI_CONVERSATION_ID =
            AttributeKey.stringKey("gen_ai.conversation.id");

    public static final AttributeKey<String> GEN_AI_CHAT_ID =
            AttributeKey.stringKey("gen_ai.chat.id");

    // ── GenAI Request ──

    public static final AttributeKey<String> GEN_AI_REQUEST_MODEL =
            AttributeKey.stringKey("gen_ai.request.model");

    public static final AttributeKey<Double> GEN_AI_REQUEST_TEMPERATURE =
            AttributeKey.doubleKey("gen_ai.request.temperature");

    public static final AttributeKey<Long> GEN_AI_REQUEST_MAX_TOKENS =
            AttributeKey.longKey("gen_ai.request.max_tokens");

    // ── GenAI Response ──

    public static final AttributeKey<String> GEN_AI_RESPONSE_MODEL =
            AttributeKey.stringKey("gen_ai.response.model");

    public static final AttributeKey<String> GEN_AI_RESPONSE_ID =
            AttributeKey.stringKey("gen_ai.response.id");

    public static final AttributeKey<Long> GEN_AI_RESPONSE_TTFT =
            AttributeKey.longKey("gen_ai.response.time_to_first_token");

    // ── GenAI Usage (L1 Normalized) ──

    public static final AttributeKey<Long> GEN_AI_USAGE_INPUT_TOKENS =
            AttributeKey.longKey("gen_ai.usage.input_tokens");

    public static final AttributeKey<Long> GEN_AI_USAGE_OUTPUT_TOKENS =
            AttributeKey.longKey("gen_ai.usage.output_tokens");

    public static final AttributeKey<Long> GEN_AI_USAGE_TOTAL_TOKENS =
            AttributeKey.longKey("gen_ai.usage.total_tokens");

    public static final AttributeKey<Long> GEN_AI_USAGE_CACHE_READ =
            AttributeKey.longKey("gen_ai.usage.cache_read.input_tokens");

    public static final AttributeKey<Long> GEN_AI_USAGE_CACHE_CREATION =
            AttributeKey.longKey("gen_ai.usage.cache_creation.input_tokens");

    // ── GenAI Usage Details (Ant extended) ──

    public static final AttributeKey<Long> GEN_AI_USAGE_DETAILS_CACHE_READ =
            AttributeKey.longKey("gen_ai.usage.details.cache_read_input_tokens");

    public static final AttributeKey<Long> GEN_AI_USAGE_DETAILS_CACHE_CREATION =
            AttributeKey.longKey("gen_ai.usage.details.cache_creation_input_tokens");

    public static final AttributeKey<Double> GEN_AI_USAGE_DETAILS_CACHE_HIT_RATE =
            AttributeKey.doubleKey("gen_ai.usage.details.cache_hit_rate");

    // ── GenAI Tool ──

    public static final AttributeKey<String> GEN_AI_TOOL_NAME =
            AttributeKey.stringKey("gen_ai.tool.name");

    public static final AttributeKey<String> GEN_AI_TOOL_CALL_ID =
            AttributeKey.stringKey("gen_ai.tool.call.id");

    public static final AttributeKey<String> GEN_AI_TOOL_TYPE =
            AttributeKey.stringKey("gen_ai.tool.type");

    public static final AttributeKey<String> GEN_AI_TOOL_CALL_ARGUMENTS =
            AttributeKey.stringKey("gen_ai.tool.call.arguments");

    public static final AttributeKey<String> GEN_AI_TOOL_CALL_RESULT =
            AttributeKey.stringKey("gen_ai.tool.call.result");

    // ── GenAI Content ──

    public static final AttributeKey<String> GEN_AI_INPUT_MESSAGES =
            AttributeKey.stringKey("gen_ai.input.messages");

    public static final AttributeKey<String> GEN_AI_OUTPUT_MESSAGES =
            AttributeKey.stringKey("gen_ai.output.messages");

    // ── Agentic Runtime ──

    public static final AttributeKey<String> AGENTIC_RUNTIME_NAME =
            AttributeKey.stringKey("agentic.runtime.name");

    public static final AttributeKey<String> AGENTIC_RUNTIME_VERSION =
            AttributeKey.stringKey("agentic.runtime.version");

    public static final AttributeKey<String> AGENTIC_RUNTIME_CWD =
            AttributeKey.stringKey("agentic.runtime.cwd");

    // ── Agentic Turn ──

    public static final AttributeKey<String> AGENTIC_TURN_ID =
            AttributeKey.stringKey("agentic.turn.id");

    public static final AttributeKey<Long> AGENTIC_TURN_NUMBER =
            AttributeKey.longKey("agentic.turn.number");

    public static final AttributeKey<Long> AGENTIC_TURN_INDEX =
            AttributeKey.longKey("agentic.turn.index");

    public static final AttributeKey<Long> AGENTIC_RESPONSE_INDEX =
            AttributeKey.longKey("agentic.response.index");

    // ── Agentic Tool ──

    public static final AttributeKey<String> AGENTIC_TOOL_TYPE =
            AttributeKey.stringKey("agentic.tool.type");

    public static final AttributeKey<String> AGENTIC_TOOL_NAME =
            AttributeKey.stringKey("agentic.tool.name");

    public static final AttributeKey<String> AGENTIC_TOOL_RAW_NAME =
            AttributeKey.stringKey("agentic.tool.raw_name");

    public static final AttributeKey<String> AGENTIC_TOOL_ID =
            AttributeKey.stringKey("agentic.tool.id");

    public static final AttributeKey<Long> AGENTIC_TOOL_DURATION_MS =
            AttributeKey.longKey("agentic.tool.duration_ms");

    public static final AttributeKey<Long> AGENTIC_TOOL_COUNT =
            AttributeKey.longKey("agentic.tool.count");

    public static final AttributeKey<String> AGENTIC_TOOL_PARALLEL_GROUP =
            AttributeKey.stringKey("agentic.tool.parallel_group");

    public static final AttributeKey<String> AGENTIC_TOOL_DECISION =
            AttributeKey.stringKey("agentic.tool.decision");

    public static final AttributeKey<String> AGENTIC_TOOL_DECISION_SOURCE =
            AttributeKey.stringKey("agentic.tool.decision.source");

    // ── Agentic LLM ──

    public static final AttributeKey<Long> AGENTIC_LLM_DURATION_MS =
            AttributeKey.longKey("agentic.llm.duration_ms");

    public static final AttributeKey<Long> AGENTIC_LLM_STEP =
            AttributeKey.longKey("agentic.llm.step");

    // ── Agentic Usage L2 Breakdown ──

    public static final AttributeKey<Long> AGENTIC_USAGE_FRESH_INPUT =
            AttributeKey.longKey("agentic.usage.fresh_input_tokens");

    public static final AttributeKey<Long> AGENTIC_USAGE_CACHE_READ =
            AttributeKey.longKey("agentic.usage.cache_read_tokens");

    public static final AttributeKey<Long> AGENTIC_USAGE_CACHE_WRITE =
            AttributeKey.longKey("agentic.usage.cache_write_tokens");

    public static final AttributeKey<Long> AGENTIC_USAGE_REASONING =
            AttributeKey.longKey("agentic.usage.reasoning_tokens");

    // ── Agentic Compaction ──

    public static final AttributeKey<String> AGENTIC_COMPACTION_TRIGGER =
            AttributeKey.stringKey("agentic.compaction.trigger");

    public static final AttributeKey<Long> AGENTIC_COMPACTION_TOKENS_BEFORE =
            AttributeKey.longKey("agentic.compaction.tokens.before");

    public static final AttributeKey<Long> AGENTIC_COMPACTION_TOKENS_AFTER =
            AttributeKey.longKey("agentic.compaction.tokens.after");

    public static final AttributeKey<Long> AGENTIC_COMPACTION_TOKENS_SAVED =
            AttributeKey.longKey("agentic.compaction.tokens.saved");

    // ── Coding Agent ──

    public static final AttributeKey<String> CODE_TOOL_NAME =
            AttributeKey.stringKey("code.tool.name");

    public static final AttributeKey<Long> CODE_TOOL_EXIT_CODE =
            AttributeKey.longKey("code.tool.exit_code");

    public static final AttributeKey<String> CODE_FILE_OPERATION =
            AttributeKey.stringKey("code.file.operation");

    public static final AttributeKey<String> CODE_FILE_PATH =
            AttributeKey.stringKey("code.file.path");

    // ── Error ──

    public static final AttributeKey<String> ERROR_TYPE =
            AttributeKey.stringKey("error.type");
}
