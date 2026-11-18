/**
 * Semantic convention constants and helpers for observ-dsh.
 *
 * Only the subset consumed by the dsh semantic engine is kept here. The
 * registry of record is docs/semconv/*.yaml; attribute names in this file
 * must stay in sync with that registry.
 */

import {
  SpanKind,
  context,
  type Counter,
  type Histogram,
  type Meter,
  type MetricOptions,
  type Span,
  type SpanOptions,
  type Context,
  type Tracer,
} from '@opentelemetry/api';
import type { Logger } from '@opentelemetry/api-logs';

// ── Span attribute constants ────────────────────────────────────────────────

export const AntAgentSpanAttributes = {
  GEN_AI_SPAN_KIND: 'gen_ai.span.kind',
  GEN_AI_AGENT_NAME: 'gen_ai.agent.name',
  GEN_AI_AGENT_ID: 'gen_ai.agent.id',
  GEN_AI_CONVERSATION_ID: 'gen_ai.conversation.id',
  GEN_AI_TURN_ID: 'agentic.turn.id',
  AGENTIC_TURN_NUMBER: 'agentic.turn.number',
  ANT_AGENT_INSTANCE: 'ant.agent.instance',
  ANT_AGENT_PRODUCT: 'ant.agent.product',
  ANT_AGENT_TYPE: 'ant.agent.type',
  ANT_USERNAME: 'ant.username',
  ANT_UID: 'ant.uid',
} as const;

export const AntChatSpanAttributes = {
  GEN_AI_SPAN_KIND: 'gen_ai.span.kind',
  GEN_AI_CHAT_ID: 'gen_ai.chat.id',
  GEN_AI_CONVERSATION_ID: 'gen_ai.conversation.id',
  GEN_AI_SESSION_ID: 'gen_ai.session.id',
  GEN_AI_SESSION_PARENT_ID: 'gen_ai.session.parent_id',
  GEN_AI_SESSION_SEED_LENGTH: 'gen_ai.session.seed_length',
  GEN_AI_AGENT_ORIGIN: 'gen_ai.agent.origin',
  AGENTIC_AGENT_PRESET: 'agentic.agent.preset',
  ANT_USERNAME: 'ant.username',
  ANT_UID: 'ant.uid',
} as const;

export const AntLlmSpanAttributes = {
  GEN_AI_SPAN_KIND: 'gen_ai.span.kind',
  GEN_AI_OPERATION_NAME: 'gen_ai.operation.name',
  GEN_AI_REQUEST_MODEL: 'gen_ai.request.model',
  GEN_AI_RESPONSE_MODEL: 'gen_ai.response.model',
  GEN_AI_PROVIDER_NAME: 'gen_ai.provider.name',
  GEN_AI_REQUEST_TEMPERATURE: 'gen_ai.request.temperature',
  GEN_AI_REQUEST_MAX_TOKENS: 'gen_ai.request.max_tokens',
  GEN_AI_REQUEST_STOP_SEQUENCES: 'gen_ai.request.stop_sequences',
  GEN_AI_RESPONSE_FINISH_REASONS: 'gen_ai.response.finish_reasons',
  GEN_AI_USAGE_INPUT_TOKENS: 'gen_ai.usage.input_tokens',
  GEN_AI_USAGE_OUTPUT_TOKENS: 'gen_ai.usage.output_tokens',
  GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS: 'gen_ai.usage.cache_read.input_tokens',
  GEN_AI_USAGE_CACHE_CREATION_INPUT_TOKENS: 'gen_ai.usage.cache_creation.input_tokens',
  GEN_AI_SYSTEM_INSTRUCTIONS: 'gen_ai.system_instructions',
  GEN_AI_INPUT_MESSAGES: 'gen_ai.input.messages',
  GEN_AI_OUTPUT_MESSAGES: 'gen_ai.output.messages',
  GEN_AI_TOOL_DEFINITIONS: 'gen_ai.tool.definitions',
  GEN_AI_CONVERSATION_ID: 'gen_ai.conversation.id',
  AGENTIC_TURN_NUMBER: 'agentic.turn.number',
  AGENTIC_STEP_NUMBER: 'agentic.step.number',
  AGENTIC_LLM_RETRY_COUNT: 'agentic.llm.retry.count',
  ANT_USERNAME: 'ant.username',
  ANT_UID: 'ant.uid',
} as const;

export const AntToolSpanAttributes = {
  GEN_AI_SPAN_KIND: 'gen_ai.span.kind',
  GEN_AI_OPERATION_NAME: 'gen_ai.operation.name',
  GEN_AI_TOOL_CALL_ID: 'gen_ai.tool.call.id',
  GEN_AI_TOOL_NAME: 'gen_ai.tool.name',
  GEN_AI_TOOL_TYPE: 'gen_ai.tool.type',
  GEN_AI_TOOL_CALL_ARGUMENTS: 'gen_ai.tool.call.arguments',
  GEN_AI_TOOL_CALL_RESULT: 'gen_ai.tool.call.result',
  AGENTIC_TOOL_TYPE: 'agentic.tool.type',
  AGENTIC_TOOL_NAME: 'agentic.tool.name',
  AGENTIC_TOOL_RAW_NAME: 'agentic.tool.raw_name',
  AGENTIC_TOOL_ID: 'agentic.tool.id',
  AGENTIC_TOOL_DERIVED: 'agentic.tool.derived',
  AGENTIC_TOOL_CALL_ID_SYNTHETIC: 'agentic.tool.call_id.synthetic',
  AGENTIC_TOOL_DURATION_MS: 'agentic.tool.duration_ms',
  CODE_TOOL_NAME: 'code.tool.name',
  CODE_TOOL_EXIT_CODE: 'code.tool.exit_code',
  GEN_AI_CONVERSATION_ID: 'gen_ai.conversation.id',
  GEN_AI_AGENT_NAME: 'gen_ai.agent.name',
  AGENTIC_TURN_NUMBER: 'agentic.turn.number',
  AGENTIC_STEP_NUMBER: 'agentic.step.number',
  ANT_USERNAME: 'ant.username',
  ANT_UID: 'ant.uid',
} as const;

export const ErrorAttributes = {
  ERROR_TYPE: 'error.type',
} as const;

/** Repository-owned internal attributes that callers must not override. */
export const InternalAttributes = {
  META_ROOT: '_internal.meta.root',
} as const;
export const INTERNAL_META_ROOT_VALUE = '1';

// ── Span helpers ─────────────────────────────────────────────────────────────

export function startSpanAntChatSpan(
  tracer: Tracer, name: string, options?: SpanOptions, ctx?: Context,
): Span {
  return tracer.startSpan(name, { ...options, kind: SpanKind.SERVER }, ctx ?? context.active());
}

export function startSpanAntAgentSpan(
  tracer: Tracer, name: string, options?: SpanOptions, ctx?: Context,
): Span {
  return tracer.startSpan(name, { ...options, kind: SpanKind.CLIENT }, ctx ?? context.active());
}

export function startSpanAntLlmSpan(
  tracer: Tracer, name: string, options?: SpanOptions, ctx?: Context,
): Span {
  return tracer.startSpan(name, { ...options, kind: SpanKind.CLIENT }, ctx ?? context.active());
}

export function startSpanAntToolSpan(
  tracer: Tracer, name: string, options?: SpanOptions, ctx?: Context,
): Span {
  return tracer.startSpan(name, { ...options, kind: SpanKind.INTERNAL }, ctx ?? context.active());
}

// ── Metric constants and factories ───────────────────────────────────────────

export const AgenticErrorCategory = {
  API_ERROR: 'api_error',
  NETWORK_ERROR: 'network_error',
  TIMEOUT: 'timeout',
  RATE_LIMIT: 'rate_limit',
  AUTH: 'auth',
  PERMISSION: 'permission',
  VALIDATION: 'validation',
  TOOL_ERROR: 'tool_error',
  CANCELLED: 'cancelled',
  OTHER: 'other',
} as const;
export type AgenticErrorCategory = typeof AgenticErrorCategory[keyof typeof AgenticErrorCategory];

export function classifyAgenticErrorCategory(errorType: string): AgenticErrorCategory {
  const normalized = errorType.trim().toLowerCase();
  if (!normalized) return AgenticErrorCategory.OTHER;
  if (normalized === 'network_error' || normalized.includes('network') || normalized.includes('econnreset') || normalized.includes('enotfound')) {
    return AgenticErrorCategory.NETWORK_ERROR;
  }
  if (normalized.includes('timeout') || normalized.includes('timedout') || normalized.includes('etimedout')) {
    return AgenticErrorCategory.TIMEOUT;
  }
  if (normalized === '429' || normalized.includes('rate') || normalized.includes('quota')) {
    return AgenticErrorCategory.RATE_LIMIT;
  }
  if (normalized === '401' || normalized.includes('auth') || normalized.includes('credential') || normalized.includes('unauthorized')) {
    return AgenticErrorCategory.AUTH;
  }
  if (normalized === '403' || normalized.includes('permission') || normalized.includes('forbidden') || normalized.includes('denied')) {
    return AgenticErrorCategory.PERMISSION;
  }
  if (normalized === 'tool_error' || normalized === 'invalid_tool' || normalized === 'exit_code' || normalized.includes('tool')) {
    return AgenticErrorCategory.TOOL_ERROR;
  }
  if (['400', '404', '409', '422'].includes(normalized) || normalized.includes('validation') || normalized.includes('invalid')) {
    return AgenticErrorCategory.VALIDATION;
  }
  if (normalized.includes('cancel') || normalized.includes('abort')) {
    return AgenticErrorCategory.CANCELLED;
  }
  if (/^5\d\d$/.test(normalized) || normalized === 'apierror' || normalized.includes('api_error')) {
    return AgenticErrorCategory.API_ERROR;
  }
  return AgenticErrorCategory.OTHER;
}

const TOKEN_HISTOGRAM_BOUNDARIES = [1, 4, 16, 64, 256, 1024, 4096, 16384, 65536, 262144, 1048576, 4194304, 16777216, 67108864];

export function createMetricGenAiClientTokenUsage(meter: Meter, options?: MetricOptions): Histogram {
  return meter.createHistogram('gen_ai.client.token.usage', {
    description: 'GenAI client token usage - for each model call',
    unit: '{token}',
    advice: { explicitBucketBoundaries: TOKEN_HISTOGRAM_BOUNDARIES },
    ...options,
  });
}

export function createMetricGenAiClientOperationDuration(meter: Meter, options?: MetricOptions): Histogram {
  return meter.createHistogram('gen_ai.client.operation.duration', {
    description: 'GenAI client operation duration',
    unit: 'ms',
    advice: { explicitBucketBoundaries: [500, 1000, 2000, 5000, 10000, 20000, 30000, 60000] },
    ...options,
  });
}

export function createMetricAgentSessionCount(meter: Meter, options?: MetricOptions): Counter {
  return meter.createCounter('agent.session.count', {
    description: 'Agent session start count', unit: '{count}', ...options,
  });
}

export function createMetricAgentTokenUsage(meter: Meter, options?: MetricOptions): Histogram {
  return meter.createHistogram('agent.token.usage', {
    description: 'Token usage histogram', unit: '{token}',
    advice: { explicitBucketBoundaries: TOKEN_HISTOGRAM_BOUNDARIES },
    ...options,
  });
}

export function createMetricAgentToolCallCount(meter: Meter, options?: MetricOptions): Counter {
  return meter.createCounter('agent.tool_call.count', {
    description: 'Tool call count', unit: '{count}', ...options,
  });
}

export function createMetricAgentToolCallErrorCount(meter: Meter, options?: MetricOptions): Counter {
  return meter.createCounter('agent.tool_call.error.count', {
    description: 'Tool call error count', unit: '{count}', ...options,
  });
}

export function createMetricAgentToolCallDuration(meter: Meter, options?: MetricOptions): Histogram {
  return meter.createHistogram('agent.tool_call.duration', {
    description: 'Tool call duration distribution', unit: 'ms',
    advice: { explicitBucketBoundaries: [10, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000, 60000, 120000] },
    ...options,
  });
}

export function createMetricAgentApiRequestCount(meter: Meter, options?: MetricOptions): Counter {
  return meter.createCounter('agent.api_request.count', {
    description: 'API request count', unit: '{count}', ...options,
  });
}

export function createMetricAgentApiRequestErrorCount(meter: Meter, options?: MetricOptions): Counter {
  return meter.createCounter('agent.api_request.error.count', {
    description: 'API request error count', unit: '{count}', ...options,
  });
}

export function createMetricAgentApiRequestDuration(meter: Meter, options?: MetricOptions): Histogram {
  return meter.createHistogram('agent.api_request.duration', {
    description: 'API request duration distribution', unit: 'ms',
    advice: { explicitBucketBoundaries: [500, 1000, 2000, 5000, 10000, 20000, 30000, 60000] },
    ...options,
  });
}

export function createMetricAgentCacheReadTokens(meter: Meter, options?: MetricOptions): Counter {
  return meter.createCounter('agent.cache.read_tokens', {
    description: 'Cache read token count (cache hits)', unit: '{token}', ...options,
  });
}

export function createMetricAgentCacheWriteTokens(meter: Meter, options?: MetricOptions): Counter {
  return meter.createCounter('agent.cache.write_tokens', {
    description: 'Cache write token count (cache misses)', unit: '{token}', ...options,
  });
}

// ── Tool classification ──────────────────────────────────────────────────────

export type AgenticToolType = 'builtin' | 'skill' | 'mcp' | 'subagent';

export interface ToolClassification {
  'agentic.tool.type': AgenticToolType;
  'agentic.tool.name': string;
  'agentic.tool.raw_name': string;
  'agentic.tool.id': string;
}

/** dsh builtin tool names; tools outside this set are classified as MCP. */
const DSH_BUILTINS = new Set([
  'bash', 'pwsh', 'read', 'write', 'edit', 'glob', 'grep', 'fs', 'fs_search',
  'web_search', 'web_fetch', 'todo_write', 'skill', 'subagent', 'subagent_fork',
  'send_message', 'goal', 'ask_user', 'workflow', 'job', 'jobs',
  'str_replace_editor', 'list_agents', 'task',
]);

const SKILL_NAMES = new Set(['skill', 'use_skill', 'skills']);

export function classifyTool(rawToolName: string): ToolClassification {
  if (rawToolName.startsWith('mcp__')) {
    const parts = rawToolName.slice(5).split('__');
    const server = parts[0] || rawToolName;
    const tool = parts.slice(1).join('/') || server;
    return {
      'agentic.tool.type': 'mcp',
      'agentic.tool.name': `${server}/${tool}`,
      'agentic.tool.raw_name': rawToolName,
      'agentic.tool.id': rawToolName,
    };
  }
  if (SKILL_NAMES.has(rawToolName) || rawToolName.toLowerCase().startsWith('skill')) {
    return {
      'agentic.tool.type': 'skill',
      'agentic.tool.name': rawToolName,
      'agentic.tool.raw_name': rawToolName,
      'agentic.tool.id': `skill__${rawToolName}`,
    };
  }
  if (rawToolName === 'subagent' || rawToolName === 'subagent_fork' || rawToolName === 'task') {
    return {
      'agentic.tool.type': 'subagent',
      'agentic.tool.name': rawToolName,
      'agentic.tool.raw_name': rawToolName,
      'agentic.tool.id': `subagent__${rawToolName}`,
    };
  }
  if (!DSH_BUILTINS.has(rawToolName)) {
    return {
      'agentic.tool.type': 'mcp',
      'agentic.tool.name': rawToolName,
      'agentic.tool.raw_name': rawToolName,
      'agentic.tool.id': `mcp__${rawToolName}`,
    };
  }
  return {
    'agentic.tool.type': 'builtin',
    'agentic.tool.name': rawToolName,
    'agentic.tool.raw_name': rawToolName,
    'agentic.tool.id': `builtin__${rawToolName}`,
  };
}

export function toolSpanName(classification: ToolClassification): string {
  const { 'agentic.tool.type': type, 'agentic.tool.name': name } = classification;
  if (type === 'subagent') return `subagent ${name}`;
  return `${type} ${name}`;
}

// ── Event emission ───────────────────────────────────────────────────────────

export const EventAgentSessionStartEventName = 'session_start';
export const EventAgentSessionEndEventName = 'session_end';
export const EventAgentUserPromptEventName = 'user_prompt';
export const EventAgentApiRequestEventName = 'api_request';
export const EventAgentToolCallEventName = 'tool_call';
export const EventAgentToolResultEventName = 'tool_result';
export const EventAgentCompactionResultEventName = 'compaction_result';
export const EventAgentErrorEventName = 'agent_error';

export function emitEvent(
  logger: Logger,
  eventName: string,
  data: { context?: Context; attributes?: Record<string, unknown> },
): void {
  logger.emit({
    body: { 'event.name': eventName },
    context: data.context,
    attributes: {
      'event.name': eventName,
      ...(data.attributes ?? {}),
    },
  });
}
