/**
 * Semantic convention constants and helpers for observ-opencode.
 *
 * Extracted from OTel Weaver auto-generated code -- only the subset
 * actually consumed by hooks is kept here.
 *
 * @see docs/semconv/ for the full registry definitions
 */

import {
  SpanKind,
  context,
  type Tracer,
  type Span,
  type SpanOptions,
  type Context,
  type Counter,
  type Histogram,
  type Meter,
  type MetricOptions,
} from '@opentelemetry/api';
import type { Logger } from '@opentelemetry/api-logs';

// --- Span Attribute Constants ------------------------------------------------

export const SpanAntAgentAttributes = {
  AGENTIC_RUNTIME_NAME: 'agentic.runtime.name',
  AGENTIC_RUNTIME_VERSION: 'agentic.runtime.version',
  AGENTIC_TURN_ID: 'agentic.turn.id',
  ANT_AGENT_INSTANCE: 'ant.agent.instance',
  ANT_AGENT_PRODUCT: 'ant.agent.product',
  ANT_AGENT_TYPE: 'ant.agent.type',
  ANT_USERNAME: 'ant.username',
  GEN_AI_SPAN_KIND: 'gen_ai.span.kind',
} as const;

export const SpanAntChatAttributes = {
  ANT_AGENT_INSTANCE: 'ant.agent.instance',
  ANT_AGENT_PRODUCT: 'ant.agent.product',
  ANT_AGENT_TYPE: 'ant.agent.type',
  ANT_USERNAME: 'ant.username',
  GEN_AI_CHAT_ID: 'gen_ai.chat.id',
  GEN_AI_CONVERSATION_ID: 'gen_ai.conversation.id',
  GEN_AI_INPUT_MESSAGES: 'gen_ai.input.messages',
  GEN_AI_OUTPUT_MESSAGES: 'gen_ai.output.messages',
  GEN_AI_SPAN_KIND: 'gen_ai.span.kind',
  GEN_AI_USAGE_INPUT_TOKENS: 'gen_ai.usage.input_tokens',
  GEN_AI_USAGE_OUTPUT_TOKENS: 'gen_ai.usage.output_tokens',
  GEN_AI_USAGE_TOTAL_TOKENS: 'gen_ai.usage.total_tokens',
} as const;

export const SpanAntLlmAttributes = {
  GEN_AI_REQUEST_MODEL: 'gen_ai.request.model',
  GEN_AI_RESPONSE_MODEL: 'gen_ai.response.model',
  GEN_AI_SPAN_KIND: 'gen_ai.span.kind',
  GEN_AI_PROVIDER_NAME: 'gen_ai.provider.name',
  GEN_AI_USAGE_INPUT_TOKENS: 'gen_ai.usage.input_tokens',
  GEN_AI_USAGE_OUTPUT_TOKENS: 'gen_ai.usage.output_tokens',
  GEN_AI_USAGE_TOTAL_TOKENS: 'gen_ai.usage.total_tokens',
  GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS: 'gen_ai.usage.cache_read.input_tokens',
  GEN_AI_USAGE_CACHE_CREATION_INPUT_TOKENS: 'gen_ai.usage.cache_creation.input_tokens',
} as const;

export const SpanAntToolAttributes = {
  GEN_AI_SPAN_KIND: 'gen_ai.span.kind',
  GEN_AI_TOOL_CALL_ID: 'gen_ai.tool.call.id',
  GEN_AI_TOOL_NAME: 'gen_ai.tool.name',
} as const;

export const RegistryGenAiOptIn = {
  GEN_AI_INPUT_MESSAGES: 'gen_ai.input.messages',
  GEN_AI_OUTPUT_MESSAGES: 'gen_ai.output.messages',
  GEN_AI_SYSTEM_INSTRUCTIONS: 'gen_ai.system_instructions',
} as const;

/** Repository-owned attributes that callers must not override through config. */
export const InternalAttributes = {
  META_ROOT: '_internal.meta.root',
} as const;

export const INTERNAL_META_ROOT_VALUE = '1';

// --- Span Helpers ------------------------------------------------------------

export function startSpanAntAgentSpan(
  tracer: Tracer, name: string, options?: SpanOptions, ctx?: Context,
): Span {
  const attributes = { 'gen_ai.span.kind': 'AGENT', ...options?.attributes };
  return tracer.startSpan(
    name,
    { ...options, attributes, kind: SpanKind.CLIENT },
    ctx ?? context.active(),
  );
}

export function startSpanAntChatSpan(
  tracer: Tracer, name: string, options?: SpanOptions, ctx?: Context,
): Span {
  return tracer.startSpan(name, { ...options, kind: SpanKind.SERVER }, ctx ?? context.active());
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

/** CLIENT span for API error events (C14) — sibling of generation, child of turn root */
export function startSpanAntClientSpan(
  tracer: Tracer, name: string, options?: SpanOptions, ctx?: Context,
): Span {
  return tracer.startSpan(name, { ...options, kind: SpanKind.CLIENT }, ctx ?? context.active());
}

// --- Metric Constants --------------------------------------------------------

const MetricAgentSessionCountMetric = {
  name: 'agent.session.count', unit: '{count}',
  description: 'Agent session start count',
} as const;

const MetricAgentTokenUsageMetric = {
  name: 'agent.token.usage', unit: '{token}',
  description: 'Token usage counter (complementary to gen_ai.client.token.usage histogram)',
} as const;

const MetricAgentToolCallCountMetric = {
  name: 'agent.tool_call.count', unit: '{count}',
  description: 'Tool call count',
} as const;

const MetricAgentToolCallErrorCountMetric = {
  name: 'agent.tool_call.error.count', unit: '{count}',
  description: 'Tool call error count',
} as const;

const MetricAgentApiRequestCountMetric = {
  name: 'agent.api_request.count', unit: '{count}',
  description: 'API request count',
} as const;

const MetricAgentApiRequestErrorCountMetric = {
  name: 'agent.api_request.error.count', unit: '{count}',
  description: 'API request error count',
} as const;

const MetricAgentApiRequestDurationMetric = {
  name: 'agent.api_request.duration', unit: 'ms',
  description: 'API request duration distribution',
} as const;

const MetricAgentCacheReadTokensMetric = {
  name: 'agent.cache.read_tokens', unit: '{token}',
  description: 'Cache read token count (cache hits)',
} as const;

const MetricAgentCacheWriteTokensMetric = {
  name: 'agent.cache.write_tokens', unit: '{token}',
  description: 'Cache write token count (cache misses)',
} as const;

// --- Metric Attribute Constants ----------------------------------------------

export const MetricAgentSessionCountAttributes = {
  ANT_AGENT_TYPE: 'ant.agent.type',
} as const;

export const MetricAgentTokenUsageAttributes = {
  ANT_AGENT_TYPE: 'ant.agent.type',
  TYPE: 'type',
} as const;

export const MetricAgentToolCallCountAttributes = {
  AGENTIC_TOOL_TYPE: 'agentic.tool.type',
  ANT_AGENT_TYPE: 'ant.agent.type',
  ANT_USERNAME: 'ant.username',
} as const;

export const MetricAgentToolCallErrorCountAttributes = {
  AGENTIC_TOOL_TYPE: 'agentic.tool.type',
  ANT_USERNAME: 'ant.username',
  AGENTIC_ERROR_CATEGORY: 'agentic.error.category',
} as const;

export const MetricAgentApiRequestCountAttributes = {
  GEN_AI_REQUEST_MODEL: 'gen_ai.request.model',
  GEN_AI_PROVIDER_NAME: 'gen_ai.provider.name',
  ANT_AGENT_TYPE: 'ant.agent.type',
  ANT_USERNAME: 'ant.username',
} as const;

export const MetricAgentApiRequestErrorCountAttributes = {
  GEN_AI_REQUEST_MODEL: 'gen_ai.request.model',
  ANT_USERNAME: 'ant.username',
  AGENTIC_ERROR_CATEGORY: 'agentic.error.category',
} as const;

export const MetricAgentApiRequestDurationAttributes = {
  GEN_AI_REQUEST_MODEL: 'gen_ai.request.model',
  GEN_AI_PROVIDER_NAME: 'gen_ai.provider.name',
  ANT_AGENT_TYPE: 'ant.agent.type',
  ANT_USERNAME: 'ant.username',
} as const;

export const MetricAgentCacheTokensAttributes = {
  GEN_AI_REQUEST_MODEL: 'gen_ai.request.model',
  ANT_AGENT_TYPE: 'ant.agent.type',
} as const;

// --- Metric Factories --------------------------------------------------------

export function createMetricAgentSessionCount(meter: Meter, options?: MetricOptions): Counter {
  return meter.createCounter(MetricAgentSessionCountMetric.name, {
    description: MetricAgentSessionCountMetric.description,
    unit: MetricAgentSessionCountMetric.unit, ...options,
  });
}

export function createMetricAgentTokenUsage(meter: Meter, options?: MetricOptions): Counter {
  return meter.createCounter(MetricAgentTokenUsageMetric.name, {
    description: MetricAgentTokenUsageMetric.description,
    unit: MetricAgentTokenUsageMetric.unit, ...options,
  });
}

export function createMetricAgentToolCallCount(meter: Meter, options?: MetricOptions): Counter {
  return meter.createCounter(MetricAgentToolCallCountMetric.name, {
    description: MetricAgentToolCallCountMetric.description,
    unit: MetricAgentToolCallCountMetric.unit, ...options,
  });
}

export function createMetricAgentToolCallErrorCount(meter: Meter, options?: MetricOptions): Counter {
  return meter.createCounter(MetricAgentToolCallErrorCountMetric.name, {
    description: MetricAgentToolCallErrorCountMetric.description,
    unit: MetricAgentToolCallErrorCountMetric.unit, ...options,
  });
}

export function createMetricAgentApiRequestCount(meter: Meter, options?: MetricOptions): Counter {
  return meter.createCounter(MetricAgentApiRequestCountMetric.name, {
    description: MetricAgentApiRequestCountMetric.description,
    unit: MetricAgentApiRequestCountMetric.unit, ...options,
  });
}

export function createMetricAgentApiRequestErrorCount(meter: Meter, options?: MetricOptions): Counter {
  return meter.createCounter(MetricAgentApiRequestErrorCountMetric.name, {
    description: MetricAgentApiRequestErrorCountMetric.description,
    unit: MetricAgentApiRequestErrorCountMetric.unit, ...options,
  });
}

export function createMetricAgentApiRequestDuration(meter: Meter, options?: MetricOptions): Histogram {
  return meter.createHistogram(MetricAgentApiRequestDurationMetric.name, {
    description: MetricAgentApiRequestDurationMetric.description,
    unit: MetricAgentApiRequestDurationMetric.unit,
    advice: {
      explicitBucketBoundaries: [500, 1000, 2000, 5000, 10000, 20000, 30000, 60000],
    },
    ...options,
  });
}

export function createMetricAgentCacheReadTokens(meter: Meter, options?: MetricOptions): Counter {
  return meter.createCounter(MetricAgentCacheReadTokensMetric.name, {
    description: MetricAgentCacheReadTokensMetric.description,
    unit: MetricAgentCacheReadTokensMetric.unit, ...options,
  });
}

export function createMetricAgentCacheWriteTokens(meter: Meter, options?: MetricOptions): Counter {
  return meter.createCounter(MetricAgentCacheWriteTokensMetric.name, {
    description: MetricAgentCacheWriteTokensMetric.description,
    unit: MetricAgentCacheWriteTokensMetric.unit, ...options,
  });
}

// --- Event Constants ---------------------------------------------------------

export const EventAgentUserPromptEventName = 'user_prompt' as const;
export const EventAgentApiRequestEventName = 'api_request' as const;  // v4: renamed from llm_request, merged llm_error
export const EventAgentToolCallEventName = 'tool_call' as const;
export const EventAgentToolResultEventName = 'tool_result' as const;
export const EventAgentToolDecisionEventName = 'tool_decision' as const;
export const EventAgentSessionStartEventName = 'session_start' as const;
export const EventAgentSessionEndEventName = 'session_end' as const;
export const EventAgentCompactionResultEventName = 'compaction_result' as const;

// --- Event Data Interfaces ---------------------------------------------------

export interface EventData {
  readonly eventName: string;
  context?: Context;
  attributes?: Record<string, unknown>;
}

// --- Tool Classification -----------------------------------------------------

export interface ToolClassification {
  'agentic.tool.type': 'mcp' | 'skill' | 'builtin' | 'subagent';
  'agentic.tool.name': string;
  'agentic.tool.raw_name': string;
  'agentic.tool.id': string;
}

/** Canonical display/span name for a classified OpenCode tool. */
export function toolDisplayName(tool: ToolClassification): string {
  if (tool['agentic.tool.type'] === 'subagent') return 'agent SyncAgent';
  return `${tool['agentic.tool.type'] === 'builtin' ? 'tool' : tool['agentic.tool.type']} ${tool['agentic.tool.name']}`;
}

export type AgenticErrorCategory =
  | 'api_error'
  | 'network_error'
  | 'timeout'
  | 'rate_limit'
  | 'auth'
  | 'permission'
  | 'validation'
  | 'tool_error'
  | 'cancelled'
  | 'other';

export function classifyAgenticErrorCategory(errorType: string): AgenticErrorCategory {
  const normalized = errorType.trim().toLowerCase();
  if (!normalized) return 'other';
  if (normalized === 'network_error' || normalized.includes('network') || normalized.includes('econnreset') || normalized.includes('enotfound')) {
    return 'network_error';
  }
  if (normalized.includes('timeout') || normalized.includes('timedout') || normalized.includes('etimedout')) {
    return 'timeout';
  }
  if (normalized === '429' || normalized.includes('rate') || normalized.includes('quota')) {
    return 'rate_limit';
  }
  if (normalized === '401' || normalized.includes('auth') || normalized.includes('credential') || normalized.includes('unauthorized')) {
    return 'auth';
  }
  if (normalized === '403' || normalized.includes('permission') || normalized.includes('forbidden') || normalized.includes('denied')) {
    return 'permission';
  }
  if (normalized === 'tool_error' || normalized === 'invalid_tool' || normalized === 'exit_code' || normalized.includes('tool')) {
    return 'tool_error';
  }
  if (['400', '404', '409', '422'].includes(normalized) || normalized.includes('validation') || normalized.includes('invalid')) {
    return 'validation';
  }
  if (normalized.includes('cancel') || normalized.includes('abort')) {
    return 'cancelled';
  }
  if (/^5\d\d$/.test(normalized) || normalized === 'apierror' || normalized.includes('api_error')) {
    return 'api_error';
  }
  return 'other';
}

/**
 * Classify a tool by its raw `gen_ai.tool.name`.
 *
 * - `mcp__<server>__<tool>` -> type=mcp, name=server/tool, id=mcp__server__tool  (Claude Code)
 * - `Skill`                 -> type=skill, name=<toolName>, id=skill__<toolName>
 * - OpenCode builtins       -> type=builtin, name=<toolName>, id=builtin__<toolName>
 * - everything else         -> type=mcp, name=<toolName>, id=mcp__<toolName>  (OpenCode MCP)
 */

// OpenCode builtin tools — everything not in this set is classified as MCP
const OPENCODE_BUILTINS = new Set([
  'bash', 'read', 'write', 'edit', 'glob', 'grep',
  'webfetch', 'websearch', 'task', 'sourcegraph',
  'background_output', 'background_cancel',
  'invalid',
]);

export function classifyTool(rawToolName: string): ToolClassification {
  // Claude Code pattern: mcp__server__tool
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
  if (rawToolName === 'Skill') {
    return {
      'agentic.tool.type': 'skill',
      'agentic.tool.name': rawToolName,
      'agentic.tool.raw_name': rawToolName,
      'agentic.tool.id': `skill__${rawToolName}`,
    };
  }
  if (rawToolName === 'task') {
    return {
      'agentic.tool.type': 'subagent',
      'agentic.tool.name': rawToolName,
      'agentic.tool.raw_name': rawToolName,
      'agentic.tool.id': `subagent__${rawToolName}`,
    };
  }
  // OpenCode: known builtins vs MCP tools (no mcp__ prefix)
  if (!OPENCODE_BUILTINS.has(rawToolName)) {
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

// --- Event Emitters ----------------------------------------------------------

/** Generic event emitter: body contains only event.name; all data is also available in attributes. */
function emitEvent(logger: Logger, eventName: string, data: EventData): void {
  logger.emit({
    body: { 'event.name': eventName },
    context: data.context,
    attributes: {
      'event.name': eventName,
      ...(data.attributes ?? {}),
    },
  });
}

export function emitEventAgentUserPromptEvent(logger: Logger, data: EventData): void {
  emitEvent(logger, EventAgentUserPromptEventName, data);
}

/** v4: renamed from LlmRequest, handles success+error */
export function emitEventAgentApiRequestEvent(logger: Logger, data: EventData): void {
  emitEvent(logger, EventAgentApiRequestEventName, data);
}

export function emitEventAgentToolCallEvent(logger: Logger, data: EventData): void {
  emitEvent(logger, EventAgentToolCallEventName, data);
}

export function emitEventAgentToolResultEvent(logger: Logger, data: EventData): void {
  emitEvent(logger, EventAgentToolResultEventName, data);
}

export function emitEventAgentToolDecisionEvent(logger: Logger, data: EventData): void {
  emitEvent(logger, EventAgentToolDecisionEventName, data);
}

export function emitEventAgentSessionStartEvent(logger: Logger, data: EventData): void {
  emitEvent(logger, EventAgentSessionStartEventName, data);
}

export function emitEventAgentSessionEndEvent(logger: Logger, data: EventData): void {
  emitEvent(logger, EventAgentSessionEndEventName, data);
}

export function emitEventAgentCompactionResultEvent(logger: Logger, data: EventData): void {
  emitEvent(logger, EventAgentCompactionResultEventName, data);
}
