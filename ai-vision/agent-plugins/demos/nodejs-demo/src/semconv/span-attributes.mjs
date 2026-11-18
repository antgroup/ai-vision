/**
 * Span attribute constants derived from docs/semconv/attribute_groups.yaml and spans.yaml.
 *
 * Covers three namespaces: gen_ai.*, agentic.*, ant.*
 *
 * In the JS OTel SDK, attribute keys are plain strings (no typed AttributeKey).
 */

// ── gen_ai.span.kind values (spans.yaml) ──

export const SPAN_KIND_CHAT = 'CHAT';
export const SPAN_KIND_LLM = 'LLM';
export const SPAN_KIND_TOOL = 'TOOL';
export const SPAN_KIND_AGENT = 'AGENT';
export const SPAN_KIND_MCP = 'MCP';

// ── gen_ai.operation.name values ──

export const OP_INVOKE_AGENT = 'invoke_agent';
export const OP_CHAT = 'chat';
export const OP_EXECUTE_TOOL = 'execute_tool';

// ── agentic.tool.type values ──

export const TOOL_TYPE_BUILTIN = 'builtin';
export const TOOL_TYPE_MCP = 'mcp';
export const TOOL_TYPE_SKILL = 'skill';

// ── Ant Identity ──

export const ANT_USERNAME = 'ant.username';
export const ANT_UID = 'ant.uid';
export const ANT_AGENT_SCENE = 'ant.agent.scene';

// ── GenAI Core ──

export const GEN_AI_SPAN_KIND = 'gen_ai.span.kind';
export const GEN_AI_OPERATION_NAME = 'gen_ai.operation.name';
export const GEN_AI_SYSTEM = 'gen_ai.system';

// ── GenAI Conversation ──

export const GEN_AI_CONVERSATION_ID = 'gen_ai.conversation.id';
export const GEN_AI_CHAT_ID = 'gen_ai.chat.id';

// ── GenAI Request ──

export const GEN_AI_REQUEST_MODEL = 'gen_ai.request.model';
export const GEN_AI_REQUEST_TEMPERATURE = 'gen_ai.request.temperature';
export const GEN_AI_REQUEST_MAX_TOKENS = 'gen_ai.request.max_tokens';

// ── GenAI Response ──

export const GEN_AI_RESPONSE_MODEL = 'gen_ai.response.model';
export const GEN_AI_RESPONSE_ID = 'gen_ai.response.id';
export const GEN_AI_RESPONSE_TTFT = 'gen_ai.response.time_to_first_token';

// ── GenAI Usage (L1 Normalized) ──

export const GEN_AI_USAGE_INPUT_TOKENS = 'gen_ai.usage.input_tokens';
export const GEN_AI_USAGE_OUTPUT_TOKENS = 'gen_ai.usage.output_tokens';
export const GEN_AI_USAGE_TOTAL_TOKENS = 'gen_ai.usage.total_tokens';
export const GEN_AI_USAGE_CACHE_READ = 'gen_ai.usage.cache_read.input_tokens';
export const GEN_AI_USAGE_CACHE_CREATION = 'gen_ai.usage.cache_creation.input_tokens';

// ── GenAI Tool ──

export const GEN_AI_TOOL_NAME = 'gen_ai.tool.name';
export const GEN_AI_TOOL_CALL_ID = 'gen_ai.tool.call.id';
export const GEN_AI_TOOL_TYPE = 'gen_ai.tool.type';
export const GEN_AI_TOOL_CALL_ARGUMENTS = 'gen_ai.tool.call.arguments';
export const GEN_AI_TOOL_CALL_RESULT = 'gen_ai.tool.call.result';

// ── GenAI Content ──

export const GEN_AI_INPUT_MESSAGES = 'gen_ai.input.messages';
export const GEN_AI_OUTPUT_MESSAGES = 'gen_ai.output.messages';

// ── Agentic Runtime ──

export const AGENTIC_RUNTIME_NAME = 'agentic.runtime.name';
export const AGENTIC_RUNTIME_VERSION = 'agentic.runtime.version';
export const AGENTIC_RUNTIME_CWD = 'agentic.runtime.cwd';

// ── Agentic Turn ──

export const AGENTIC_TURN_ID = 'agentic.turn.id';
export const AGENTIC_TURN_NUMBER = 'agentic.turn.number';
export const AGENTIC_TURN_INDEX = 'agentic.turn.index';
export const AGENTIC_RESPONSE_INDEX = 'agentic.response.index';

// ── Agentic Tool ──

export const AGENTIC_TOOL_TYPE = 'agentic.tool.type';
export const AGENTIC_TOOL_NAME = 'agentic.tool.name';
export const AGENTIC_TOOL_RAW_NAME = 'agentic.tool.raw_name';
export const AGENTIC_TOOL_ID = 'agentic.tool.id';
export const AGENTIC_TOOL_DURATION_MS = 'agentic.tool.duration_ms';
export const AGENTIC_TOOL_COUNT = 'agentic.tool.count';
export const AGENTIC_TOOL_PARALLEL_GROUP = 'agentic.tool.parallel_group';
export const AGENTIC_TOOL_DECISION = 'agentic.tool.decision';
export const AGENTIC_TOOL_DECISION_SOURCE = 'agentic.tool.decision.source';

// ── Agentic LLM ──

export const AGENTIC_LLM_DURATION_MS = 'agentic.llm.duration_ms';
export const AGENTIC_LLM_STEP = 'agentic.llm.step';

// ── Agentic Usage L2 Breakdown ──

export const AGENTIC_USAGE_FRESH_INPUT = 'agentic.usage.fresh_input_tokens';
export const AGENTIC_USAGE_CACHE_READ = 'agentic.usage.cache_read_tokens';
export const AGENTIC_USAGE_CACHE_WRITE = 'agentic.usage.cache_write_tokens';
export const AGENTIC_USAGE_REASONING = 'agentic.usage.reasoning_tokens';

// ── Agentic Compaction ──

export const AGENTIC_COMPACTION_TRIGGER = 'agentic.compaction.trigger';
export const AGENTIC_COMPACTION_TOKENS_BEFORE = 'agentic.compaction.tokens.before';
export const AGENTIC_COMPACTION_TOKENS_AFTER = 'agentic.compaction.tokens.after';
export const AGENTIC_COMPACTION_TOKENS_SAVED = 'agentic.compaction.tokens.saved';

// ── Coding Agent ──

export const CODE_TOOL_NAME = 'code.tool.name';
export const CODE_TOOL_EXIT_CODE = 'code.tool.exit_code';
export const CODE_FILE_OPERATION = 'code.file.operation';
export const CODE_FILE_PATH = 'code.file.path';

// ── Error ──

export const ERROR_TYPE = 'error.type';
