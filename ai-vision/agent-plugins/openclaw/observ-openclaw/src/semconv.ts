/**
 * Semantic convention constants and helpers for observ-openclaw.
 *
 * Hand-curated from OTel Weaver auto-generated code — only the subset
 * actually consumed by hooks is kept here.
 *
 * @see docs/semconv/ for the full registry definitions
 */

import {
  SpanKind,
  ROOT_CONTEXT,
  context,
  trace,
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

// --- Severity Text -----------------------------------------------------------

export const SeverityText = {
  Trace: 'TRACE',
  Debug: 'DEBUG',
  Info:  'INFO',
  Warn:  'WARN',
  Error: 'ERROR',
} as const;
export type SeverityText = typeof SeverityText[keyof typeof SeverityText];

// --- Span Attribute Constants ------------------------------------------------

export const SpanAntAgentAttributes = {
  AGENTIC_RUNTIME_NAME: 'agentic.runtime.name',
  AGENTIC_RUNTIME_VERSION: 'agentic.runtime.version',
  AGENTIC_TURN_ID: 'agentic.turn.id',
  ANT_AGENT_INSTANCE: 'ant.agent.instance',
  ANT_AGENT_PRODUCT: 'ant.agent.product',
  ANT_AGENT_TYPE: 'ant.agent.type',
  ANT_USERNAME: 'ant.username',
  GEN_AI_AGENT_NAME: 'gen_ai.agent.name',
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
  GEN_AI_RESPONSE_ID: 'gen_ai.response.id',
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

/** Repository-owned attributes that callers must not override through config. */
export const InternalAttributes = {
  META_ROOT: '_internal.meta.root',
} as const;

export const INTERNAL_META_ROOT_VALUE = '1';

// --- Span Helpers ------------------------------------------------------------

export function startSpanAntAgentSpan(
  tracer: Tracer, name: string, options?: SpanOptions, ctx?: Context,
): Span {
  const attrs = { ...options?.attributes, 'gen_ai.span.kind': 'AGENT' };
  return tracer.startSpan(name, { ...options, attributes: attrs, kind: SpanKind.CLIENT }, ctx ?? context.active());
}

export function startSpanAntChatSpan(
  tracer: Tracer, name: string, options?: SpanOptions, ctx?: Context,
): Span {
  const attrs = { ...options?.attributes, 'gen_ai.span.kind': 'CHAT' };
  return tracer.startSpan(name, { ...options, attributes: attrs, kind: SpanKind.SERVER }, ctx ?? context.active());
}

export function startSpanAntLlmSpan(
  tracer: Tracer, name: string, options?: SpanOptions, ctx?: Context,
): Span {
  const attrs = { ...options?.attributes, 'gen_ai.span.kind': 'LLM' };
  return tracer.startSpan(name, { ...options, attributes: attrs, kind: SpanKind.CLIENT }, ctx ?? context.active());
}

export function startSpanAntToolSpan(
  tracer: Tracer, name: string, options?: SpanOptions, ctx?: Context,
): Span {
  const attrs = { ...options?.attributes, 'gen_ai.span.kind': 'TOOL' };
  return tracer.startSpan(name, { ...options, attributes: attrs, kind: SpanKind.INTERNAL }, ctx ?? context.active());
}

// --- Metric Constants --------------------------------------------------------

const MetricAgentSessionCountMetric = {
  name: 'agent.session.count', unit: '{count}',
  description: 'Agent session start count',
} as const;

const MetricAgentTokenUsageMetric = {
  name: 'agent.token.usage', unit: '{tokens}',
  description: 'Token usage counter (complementary to gen_ai.client.token.usage histogram)',
} as const;

const MetricAgentCostUsageMetric = {
  name: 'agent.cost.usage', unit: 'USD',
  description: 'Session cumulative cost (approximate)',
} as const;

const MetricAgentActiveTimeTotalMetric = {
  name: 'agent.active_time.total', unit: 's',
  description: 'Active time (excluding idle time)',
} as const;

const MetricAgentToolCallCountMetric = {
  name: 'agent.tool_call.count', unit: '{count}',
  description: 'Tool call count',
} as const;

const MetricAgentToolCallErrorCountMetric = {
  name: 'agent.tool_call.error.count', unit: '{count}',
  description: 'Tool call error count (RED Errors)',
} as const;

const MetricAgentToolCallDurationMetric = {
  name: 'agent.tool_call.duration', unit: 'ms',
  description: 'Tool call duration distribution (RED Duration)',
} as const;

const MetricAgentApiRequestCountMetric = {
  name: 'agent.api_request.count', unit: '{count}',
  description: 'API request count (RED Rate)',
} as const;

const MetricAgentApiRequestErrorCountMetric = {
  name: 'agent.api_request.error.count', unit: '{count}',
  description: 'API request error count (RED Errors)',
} as const;

const MetricAgentApiRequestDurationMetric = {
  name: 'agent.api_request.duration', unit: 'ms',
  description: 'API request duration distribution (RED Duration)',
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
  ANT_AGENT_INSTANCE: 'ant.agent.instance',
  ANT_AGENT_PRODUCT: 'ant.agent.product',
  ANT_AGENT_TYPE: 'ant.agent.type',
  ANT_USERNAME: 'ant.username',
} as const;

export const MetricAgentTokenUsageAttributes = {
  ANT_AGENT_INSTANCE: 'ant.agent.instance',
  ANT_AGENT_PRODUCT: 'ant.agent.product',
  ANT_AGENT_TYPE: 'ant.agent.type',
  ANT_USERNAME: 'ant.username',
  MODEL: 'gen_ai.request.model',
  TYPE: 'type',
} as const;

export const MetricAgentCostUsageAttributes = {
  ANT_AGENT_INSTANCE: 'ant.agent.instance',
  ANT_AGENT_PRODUCT: 'ant.agent.product',
  ANT_AGENT_TYPE: 'ant.agent.type',
  ANT_USERNAME: 'ant.username',
  MODEL: 'gen_ai.request.model',
} as const;

export const MetricAgentActiveTimeTotalAttributes = {
  ANT_AGENT_INSTANCE: 'ant.agent.instance',
  ANT_AGENT_PRODUCT: 'ant.agent.product',
  ANT_AGENT_TYPE: 'ant.agent.type',
  ANT_USERNAME: 'ant.username',
  TYPE: 'type',
} as const;

export const MetricAgentToolCallCountAttributes = {
  AGENTIC_TOOL_TYPE: 'agentic.tool.type',
  ANT_USERNAME: 'ant.username',
} as const;

export const MetricAgentToolCallErrorCountAttributes = {
  AGENTIC_TOOL_TYPE: 'agentic.tool.type',
  ANT_USERNAME: 'ant.username',
  AGENTIC_ERROR_CATEGORY: 'agentic.error.category',
} as const;

export const MetricAgentToolCallDurationAttributes = {
  AGENTIC_TOOL_TYPE: 'agentic.tool.type',
  ANT_USERNAME: 'ant.username',
} as const;

export const MetricAgentApiRequestCountAttributes = {
  GEN_AI_REQUEST_MODEL: 'gen_ai.request.model',
  ANT_USERNAME: 'ant.username',
} as const;

export const MetricAgentApiRequestErrorCountAttributes = {
  GEN_AI_REQUEST_MODEL: 'gen_ai.request.model',
  ANT_USERNAME: 'ant.username',
  AGENTIC_ERROR_CATEGORY: 'agentic.error.category',
} as const;

export const MetricAgentApiRequestDurationAttributes = {
  GEN_AI_REQUEST_MODEL: 'gen_ai.request.model',
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

export function createMetricAgentCostUsage(meter: Meter, options?: MetricOptions): Counter {
  return meter.createCounter(MetricAgentCostUsageMetric.name, {
    description: MetricAgentCostUsageMetric.description,
    unit: MetricAgentCostUsageMetric.unit, ...options,
  });
}

export function createMetricAgentActiveTimeTotal(meter: Meter, options?: MetricOptions): Counter {
  return meter.createCounter(MetricAgentActiveTimeTotalMetric.name, {
    description: MetricAgentActiveTimeTotalMetric.description,
    unit: MetricAgentActiveTimeTotalMetric.unit, ...options,
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

export function createMetricAgentToolCallDuration(meter: Meter, options?: MetricOptions): Histogram {
  return meter.createHistogram(MetricAgentToolCallDurationMetric.name, {
    description: MetricAgentToolCallDurationMetric.description,
    unit: MetricAgentToolCallDurationMetric.unit,
    advice: {
      explicitBucketBoundaries: [50, 100, 200, 500, 1000, 2000, 5000, 10000, 30000],
    },
    ...options,
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


// --- Error Category Classification -------------------------------------------

const ERROR_CATEGORY_MAP: Record<string, string> = {
  'timeout': 'timeout',
  'rate_limit': 'rate_limit',
  'rate_limited': 'rate_limit',
  'overloaded': 'rate_limit',
  'auth': 'auth',
  'authentication': 'auth',
  'unauthorized': 'auth',
  'permission': 'permission',
  'forbidden': 'permission',
  'permission_denied': 'permission',
  'validation': 'validation',
  'invalid_request': 'validation',
  'bad_request': 'validation',
  'tool_error': 'tool_error',
  'cancelled': 'cancelled',
  'aborted': 'cancelled',
  'network_error': 'network_error',
  'connection_error': 'network_error',
  'api_error': 'api_error',
  'llm_error': 'api_error',
  'server_error': 'api_error',
  'internal_error': 'api_error',
};

export function classifyErrorCategory(rawErrorType: string | undefined): string {
  if (!rawErrorType) return 'other';
  const lower = rawErrorType.toLowerCase().replace(/[^a-z_]/g, '_');
  for (const [pattern, category] of Object.entries(ERROR_CATEGORY_MAP)) {
    if (lower.includes(pattern)) return category;
  }
  return 'other';
}

// --- Event Constants ---------------------------------------------------------

export const EventAgentUserPromptEventName = 'user_prompt' as const;
export const EventAgentApiRequestEventName = 'api_request' as const;
export const EventAgentToolCallEventName = 'tool_call' as const;
export const EventAgentToolResultEventName = 'tool_result' as const;
export const EventAgentToolDecisionEventName = 'tool_decision' as const;
export const EventAgentSessionStartEventName = 'session_start' as const;
export const EventAgentSessionEndEventName = 'session_end' as const;
export const EventAgentSubagentSpawnedEventName = 'subagent_spawned' as const;
export const EventAgentSubagentEndedEventName = 'subagent_ended' as const;
export const EventAgentCompactionResultEventName = 'compaction_result' as const;

// --- Event Attribute Constants -----------------------------------------------
// All agent events share the same 6 common required attributes.

const CommonEventAttributes = {
  ANT_AGENT_PRODUCT: 'ant.agent.product',
  ANT_AGENT_TYPE: 'ant.agent.type',
  ANT_USERNAME: 'ant.username',
  EVENT_NAME: 'event.name',
  GEN_AI_CHAT_ID: 'gen_ai.chat.id',
  GEN_AI_CONVERSATION_ID: 'gen_ai.conversation.id',
  GEN_AI_OPERATION_NAME: 'gen_ai.operation.name',
  AGENTIC_TURN_NUMBER: 'agentic.turn.number',
  AGENTIC_RUNTIME_NAME: 'agentic.runtime.name',
} as const;

export const EventAgentUserPromptAttributes = { ...CommonEventAttributes } as const;
export const EventAgentApiRequestAttributes = { ...CommonEventAttributes } as const;
export const EventAgentToolCallAttributes = { ...CommonEventAttributes } as const;
export const EventAgentToolResultAttributes = { ...CommonEventAttributes } as const;
export const EventAgentToolDecisionAttributes = { ...CommonEventAttributes } as const;
export const EventAgentSessionStartAttributes = { ...CommonEventAttributes } as const;
export const EventAgentSessionEndAttributes = { ...CommonEventAttributes } as const;
export const EventAgentSubagentSpawnedAttributes = { ...CommonEventAttributes } as const;
export const EventAgentSubagentEndedAttributes = { ...CommonEventAttributes } as const;
export const EventAgentCompactionResultAttributes = { ...CommonEventAttributes } as const;

// --- Event Data Interfaces ---------------------------------------------------

export interface EventData {
  readonly eventName: string;
  attributes?: Record<string, unknown>;
  body?: Record<string, unknown>;
  /** Associated span; used to attach OTel log traceId/spanId and query attributes. */
  span?: Span;
  /** Associated context; used when a span object is not directly available. */
  context?: Context;
}

// --- Tool Classification -----------------------------------------------------

export interface ToolClassification {
  'agentic.tool.type': 'mcp' | 'skill' | 'builtin';
  'agentic.tool.name': string;
  'agentic.tool.raw_name': string;
  'agentic.tool.id': string;
}

export type InvocationKind = 'subagent' | 'tool';

/** Classify lifecycle ownership before physical tool classification. */
export function classifyInvocation(rawToolName: string | undefined): InvocationKind {
  return rawToolName === 'sessions_spawn' ? 'subagent' : 'tool';
}

/** Get display name for gen_ai.tool.name and span name: "tool read", "mcp server.tool", "skill name" */
export function toolDisplayName(tc: ToolClassification): string {
  return `${tc['agentic.tool.type']} ${tc['agentic.tool.name']}`;
}

/**
 * Classify a tool by its raw name from the transcript/hook event.
 *
 * Naming convention (aligned with skill_mcp.md):
 *
 * Logical classification is separate from the raw runtime name exported as
 * `gen_ai.tool.name`; `toolDisplayName()` owns the human-readable span name.
 *
 * `agentic.tool.raw_name` always holds the original transcript value.
 */
export function classifyTool(
  rawToolName: string,
  toolCallId?: string,
  logicalSkillName?: string,
): ToolClassification {
  // Claude Code pattern: mcp__server__tool
  if (rawToolName.startsWith('mcp__')) {
    const parts = rawToolName.slice(5).split('__');
    const server = parts[0] || rawToolName;
    const tool = parts.slice(1).join('.') || server;
    const logicalName = `${server}.${tool}`;
    return {
      'agentic.tool.type': 'mcp',
      'agentic.tool.name': logicalName,
      'agentic.tool.raw_name': rawToolName,
      'agentic.tool.id': rawToolName,
    };
  }
  if (rawToolName === 'Skill') {
    const skillName = logicalSkillName || 'unknown';
    return {
      'agentic.tool.type': 'skill',
      'agentic.tool.name': skillName,
      'agentic.tool.raw_name': rawToolName,
      'agentic.tool.id': toolCallId || `skill-${skillName}`,
    };
  }
  // Default: builtin tool
  return {
    'agentic.tool.type': 'builtin',
    'agentic.tool.name': rawToolName,
    'agentic.tool.raw_name': rawToolName,
    'agentic.tool.id': rawToolName,
  };
}

// --- Event Emitters ----------------------------------------------------------

function emitEvent(
  logger: Logger, eventName: string, data: EventData, severity?: SeverityText,
): void {
  const spanContext = data.span?.spanContext() ?? (data.context ? trace.getSpanContext(data.context) : undefined);
  const logContext = spanContext
    ? trace.setSpanContext(data.context ?? ROOT_CONTEXT, spanContext)
    : data.context;
  const attributes: Record<string, any> = {
    'event.name': eventName,
    ...(data.attributes ?? {}),
  };
  const body: Record<string, unknown> = { ...(data.body ?? {}) };
  delete body.event_name;
  body['event.name'] = eventName;

  logger.emit({
    context: logContext,
    severityText: severity,
    body: body as any,
    attributes,
  });
}

export function emitEventAgentUserPromptEvent(
  logger: Logger, data: EventData, severity?: SeverityText,
): void {
  emitEvent(logger, EventAgentUserPromptEventName, data, severity);
}

export function emitEventAgentApiRequestEvent(
  logger: Logger, data: EventData, severity?: SeverityText,
): void {
  emitEvent(logger, EventAgentApiRequestEventName, data, severity);
}

export function emitEventAgentToolCallEvent(
  logger: Logger, data: EventData, severity?: SeverityText,
): void {
  emitEvent(logger, EventAgentToolCallEventName, data, severity);
}

export function emitEventAgentToolResultEvent(
  logger: Logger, data: EventData, severity?: SeverityText,
): void {
  emitEvent(logger, EventAgentToolResultEventName, data, severity);
}



export function emitEventAgentToolDecisionEvent(
  logger: Logger, data: EventData, severity?: SeverityText,
): void {
  emitEvent(logger, EventAgentToolDecisionEventName, data, severity);
}

export function emitEventAgentSessionStartEvent(
  logger: Logger, data: EventData, severity?: SeverityText,
): void {
  emitEvent(logger, EventAgentSessionStartEventName, data, severity);
}

export function emitEventAgentSessionEndEvent(
  logger: Logger, data: EventData, severity?: SeverityText,
): void {
  emitEvent(logger, EventAgentSessionEndEventName, data, severity);
}

export function emitEventAgentSubagentSpawnedEvent(
  logger: Logger, data: EventData, severity?: SeverityText,
): void {
  emitEvent(logger, EventAgentSubagentSpawnedEventName, data, severity);
}

export function emitEventAgentSubagentEndedEvent(
  logger: Logger, data: EventData, severity?: SeverityText,
): void {
  emitEvent(logger, EventAgentSubagentEndedEventName, data, severity);
}

export function emitEventAgentCompactionResultEvent(
  logger: Logger, data: EventData, severity?: SeverityText,
): void {
  emitEvent(logger, EventAgentCompactionResultEventName, data, severity);
}
