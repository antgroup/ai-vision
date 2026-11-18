/**
 * Hook registration entry point — replaces the monolithic hooks.ts.
 *
 * createHooks() wires all handler modules to the OpenClaw plugin API
 * and returns a HookHandlers object + bindTelemetry callback for deferred init.
 */

import { SpanStatusCode, context, type Span } from '@opentelemetry/api';
import type { TelemetryRuntime } from '../telemetry.js';
import type { ObservOpenClawConfig } from '../config.js';
import type { HookContext, HookHandlers, ApiLogger, MetricCounter, MetricHistogram } from './types.js';
import {
  getActiveTurn,
  sessionMap,
  turnMap,
  runRegistry,
  finalizeTurn,
  resolveConversationId,
  clearSessionSkillCatalog,
  evictIdleSessions,
} from './state.js';
import {
  createMetricAgentSessionCount,
  createMetricAgentTokenUsage,
  createMetricAgentCostUsage,
  createMetricAgentActiveTimeTotal,
  createMetricAgentApiRequestCount,
  createMetricAgentApiRequestErrorCount,
  createMetricAgentApiRequestDuration,
  createMetricAgentToolCallCount,
  createMetricAgentToolCallErrorCount,
  createMetricAgentToolCallDuration,
} from '../semconv.js';

// Handler modules
import {
  handleBeforeModelResolve,
  handleBeforePromptBuild,
  handleBeforeAgentStart,
  handleAgentEnd,
} from './session.js';
import { handleLlmInput, handleLlmOutput } from './llm.js';
import { handleBeforeToolCall, handleAfterToolCall, handleToolResultPersist } from './tool.js';
import { handleSessionStart, handleSessionEnd, handleBeforeReset } from './session-lifecycle.js';
import { handleBeforeCompaction, handleAfterCompaction } from './compaction.js';
import { handleSubagentSpawned, handleSubagentEnded } from './subagent.js';
import { handleModelCallEnded, handleModelCallStarted } from './model-call.js';

// ─── OpenClaw Plugin API shape ──────────────────────────────────────────────

export interface OpenClawApi {
  on: (
    event: string,
    handler: (event: unknown, ctx: unknown) => unknown,
    opts?: { priority?: number },
  ) => void;
  registerHook: (
    events: string | string[],
    handler: (event: unknown) => void | Promise<void>,
    opts?: { name?: string; description?: string },
  ) => void;
  logger: ApiLogger;
}

// ─── Hook Registration Result ──────────────────────────────────────────────

export interface HookRegistration {
  handlers: HookHandlers;
  /**
   * Bind a TelemetryRuntime after deferred initialization.
   * Updates the mutable tracer/logger/metrics used by all registered hooks.
   * Safe to call multiple times — last binding wins.
   */
  bindTelemetry: (runtime: TelemetryRuntime) => void;
}

// ─── Noop implementations for pre-init period ──────────────────────────────

const noopSpan: Span = {
  setAttribute() { return this; },
  setAttributes() { return this; },
  setStatus() { return this; },
  end() {},
  addEvent() { return this; },
  isRecording() { return false; },
  recordException() { return this; },
  spanContext() { return { traceId: '0', spanId: '0', traceFlags: 0 }; },
  updateName() { return this; },
} as unknown as Span;

const noopTracer = {
  startSpan() { return noopSpan; },
  startActiveSpan(...args: unknown[]) {
    const callback = args[args.length - 1] as Function;
    return callback(noopSpan);
  },
} as HookContext['tracer'];

const noopOtelLogger = {
  emit() {},
} as unknown as HookContext['otelLogger'];

export function mergeSignalAttributes(
  commonAttributes: Record<string, string>,
  signalAttributes?: Record<string, unknown>,
): Record<string, unknown> {
  return { ...commonAttributes, ...signalAttributes };
}

/** Resolve common signal attributes at emit time so sandbox-provided identity can change. */
export function resolveSignalAttributes(config: ObservOpenClawConfig): Record<string, string> {
  const attributes = { ...config.otelExporter.attributes };
  if (config.dynamicUsername && process.env.AIVISION_ANT_USERNAME) {
    attributes['ant.username'] = process.env.AIVISION_ANT_USERNAME;
  }
  if (config.dynamicUid && process.env.AIVISION_ANT_UID) {
    attributes['ant.uid'] = process.env.AIVISION_ANT_UID;
  }
  return attributes;
}

type CommonAttributesProvider = () => Record<string, string>;

function withCommonSpanAttributes(
  baseTracer: HookContext['tracer'],
  getCommonAttributes: CommonAttributesProvider,
): HookContext['tracer'] {
  const mergeOptions = (options: unknown): unknown => {
    const spanOptions = options && typeof options === 'object'
      ? { ...(options as Record<string, unknown>) }
      : {};
    const existingAttributes = spanOptions.attributes && typeof spanOptions.attributes === 'object'
      ? spanOptions.attributes as Record<string, unknown>
      : {};
    return {
      ...spanOptions,
      attributes: mergeSignalAttributes(getCommonAttributes(), existingAttributes),
    };
  };

  return {
    startSpan(name: string, options?: unknown, ctx?: unknown): Span {
      return baseTracer.startSpan(name, mergeOptions(options) as never, ctx as never);
    },
    startActiveSpan(...args: unknown[]) {
      if (
        args.length >= 3 &&
        args[1] &&
        typeof args[1] === 'object' &&
        typeof args[args.length - 1] === 'function'
      ) {
        return (baseTracer as any).startActiveSpan(args[0], mergeOptions(args[1]), ...args.slice(2));
      }
      return (baseTracer as any).startActiveSpan(...args);
    },
  } as HookContext['tracer'];
}

function withCommonEventAttributes(
  baseLogger: HookContext['otelLogger'],
  getCommonAttributes: CommonAttributesProvider,
): HookContext['otelLogger'] {
  return {
    emit(record: unknown) {
      if (!record || typeof record !== 'object') {
        (baseLogger as any).emit(record);
        return;
      }
      const logRecord = record as Record<string, unknown>;
      const existingAttributes = logRecord.attributes && typeof logRecord.attributes === 'object'
        ? logRecord.attributes as Record<string, unknown>
        : {};
      (baseLogger as any).emit({
        ...logRecord,
        attributes: mergeSignalAttributes(getCommonAttributes(), existingAttributes),
      });
    },
  } as HookContext['otelLogger'];
}

/** Resolve hook-level uid from the unified signal-attribute map. */
export function resolveHookUid(
  config: ObservOpenClawConfig,
): string {
  return resolveSignalAttributes(config)['ant.uid'] || '';
}

// ─── createHooks ────────────────────────────────────────────────────────────

export function createHooks(
  api: OpenClawApi,
  telemetry: TelemetryRuntime | null,
  config: ObservOpenClawConfig,
  loggerOverride?: ApiLogger,
  lifecycle?: { beforeHook: () => void; afterAgentEnd: (event: unknown, ctx: unknown) => Promise<void> },
): HookRegistration {
  // Mutable telemetry bindings — updated by bindTelemetry() when runtime becomes available.
  let tracer: HookContext['tracer'] = telemetry?.tracer ?? noopTracer;
  let otelLogger: HookContext['otelLogger'] = telemetry?.logger ?? noopOtelLogger;
  let metricsEnabled = telemetry?.metricsEnabled ?? false;
  let eventsEnabled = telemetry?.eventsEnabled ?? false;
  let resolvedUsername = telemetry?.resolvedUsername ?? '';
  const logger = loggerOverride ?? api.logger;
  const getCommonAttributes = (): Record<string, string> => resolveSignalAttributes(config);

  const withCommonCounterAttributes = (counter: MetricCounter): MetricCounter => ({
    add(value, attributes) {
      counter.add(value, mergeSignalAttributes(getCommonAttributes(), attributes));
    },
  });
  const withCommonHistogramAttributes = (histogram: MetricHistogram): MetricHistogram => ({
    record(value, attributes) {
      histogram.record(value, mergeSignalAttributes(getCommonAttributes(), attributes));
    },
  });

  const resolveUsername = (): string => {
    return resolveSignalAttributes(config)['ant.username'] || resolvedUsername || '';
  };

  const resolveUid = (): string => resolveHookUid(config);

  // ── Metric counters ───────────────────────────────────────────────────────
  let sessionCounter: MetricCounter | null = null;
  let tokenCounter: MetricCounter | null = null;
  let costCounter: MetricCounter | null = null;
  let activeTimeCounter: MetricCounter | null = null;
  let llmDurationHistogram: MetricHistogram | null = null;
  let apiRequestCounter: MetricCounter | null = null;
  let apiRequestErrorCounter: MetricCounter | null = null;
  let apiRequestDurationHistogram: MetricHistogram | null = null;
  let toolCallCounter: MetricCounter | null = null;
  let toolCallErrorCounter: MetricCounter | null = null;
  let toolCallDurationHistogram: MetricHistogram | null = null;

  // If telemetry was provided eagerly (backward compat / replay passthrough), create metrics now
  if (telemetry?.metricsEnabled) {
    sessionCounter = withCommonCounterAttributes(createMetricAgentSessionCount(telemetry.meter));
    tokenCounter = withCommonCounterAttributes(createMetricAgentTokenUsage(telemetry.meter));
    costCounter = withCommonCounterAttributes(createMetricAgentCostUsage(telemetry.meter));
    activeTimeCounter = withCommonCounterAttributes(createMetricAgentActiveTimeTotal(telemetry.meter));
    llmDurationHistogram = withCommonHistogramAttributes(createMetricAgentApiRequestDuration(telemetry.meter));
    apiRequestCounter = withCommonCounterAttributes(createMetricAgentApiRequestCount(telemetry.meter));
    apiRequestErrorCounter = withCommonCounterAttributes(createMetricAgentApiRequestErrorCount(telemetry.meter));
    apiRequestDurationHistogram = withCommonHistogramAttributes(createMetricAgentApiRequestDuration(telemetry.meter));
    toolCallCounter = withCommonCounterAttributes(createMetricAgentToolCallCount(telemetry.meter));
    toolCallErrorCounter = withCommonCounterAttributes(createMetricAgentToolCallErrorCount(telemetry.meter));
    toolCallDurationHistogram = withCommonHistogramAttributes(createMetricAgentToolCallDuration(telemetry.meter));
  }

  // ── bindTelemetry — called when deferred init completes ─────────────────
  function bindTelemetry(rt: TelemetryRuntime): void {
    tracer = rt.tracer;
    otelLogger = rt.logger;
    metricsEnabled = rt.metricsEnabled;
    eventsEnabled = rt.eventsEnabled;
    resolvedUsername = rt.resolvedUsername;

    // Create real metric instruments
    if (rt.metricsEnabled) {
      sessionCounter = withCommonCounterAttributes(createMetricAgentSessionCount(rt.meter));
      tokenCounter = withCommonCounterAttributes(createMetricAgentTokenUsage(rt.meter));
      costCounter = withCommonCounterAttributes(createMetricAgentCostUsage(rt.meter));
      activeTimeCounter = withCommonCounterAttributes(createMetricAgentActiveTimeTotal(rt.meter));
      llmDurationHistogram = withCommonHistogramAttributes(createMetricAgentApiRequestDuration(rt.meter));
      apiRequestCounter = withCommonCounterAttributes(createMetricAgentApiRequestCount(rt.meter));
      apiRequestErrorCounter = withCommonCounterAttributes(createMetricAgentApiRequestErrorCount(rt.meter));
      apiRequestDurationHistogram = withCommonHistogramAttributes(createMetricAgentApiRequestDuration(rt.meter));
      toolCallCounter = withCommonCounterAttributes(createMetricAgentToolCallCount(rt.meter));
      toolCallErrorCounter = withCommonCounterAttributes(createMetricAgentToolCallErrorCount(rt.meter));
      toolCallDurationHistogram = withCommonHistogramAttributes(createMetricAgentToolCallDuration(rt.meter));
    }

    logger.debug?.('[observ-openclaw] Telemetry bound to hooks');
  }

  // ── Common signal attributes and resource-backed agent metadata ─────────
  const agentType = config.otelExporter.resourceAttributes['ant.agent.type'] || 'openclaw';
  const agentProduct = config.otelExporter.resourceAttributes['ant.agent.product'] || 'OpenClaw';
  const on: OpenClawApi['on'] = (name, handler, options) => api.on(name, (event, ctx) => {
    lifecycle?.beforeHook();
    const result = handler(event, ctx);
    if (name === 'agent_end' && lifecycle) {
      return Promise.resolve(result).then(async value => {
        await lifecycle.afterAgentEnd(event, ctx);
        return value;
      });
    }
    // Preserve synchronous hook return values, notably tool_result_persist.
    return result;
  }, options);
  const registerHook = api.registerHook.bind(api) as OpenClawApi['registerHook'];

  // ── Build HookContext with getters for mutable fields ────────────────────
  const hctx: HookContext = {
    get tracer() { return withCommonSpanAttributes(tracer, getCommonAttributes); },
    get otelLogger() { return withCommonEventAttributes(otelLogger, getCommonAttributes); },
    config,
    logger,
    get eventsEnabled() { return eventsEnabled; },
    get metricsEnabled() { return metricsEnabled; },
    resolveUsername,
    resolveUid,
    agentType,
    agentProduct,
    get sessionCounter() { return sessionCounter; },
    get tokenCounter() { return tokenCounter; },
    get costCounter() { return costCounter; },
    get activeTimeCounter() { return activeTimeCounter; },
    get llmDurationHistogram() { return llmDurationHistogram; },
    get apiRequestCounter() { return apiRequestCounter; },
    get apiRequestErrorCounter() { return apiRequestErrorCounter; },
    get apiRequestDurationHistogram() { return apiRequestDurationHistogram; },
    get toolCallCounter() { return toolCallCounter; },
    get toolCallErrorCounter() { return toolCallErrorCounter; },
    get toolCallDurationHistogram() { return toolCallDurationHistogram; },
    eventSequenceMap: new Map<string, number>(),
  };

  // ── Wire handlers to API ──────────────────────────────────────────────────
  on('before_model_resolve', (ev, cx) => handleBeforeModelResolve(hctx, ev, cx), { priority: 90 });
  on('before_prompt_build', (ev, cx) => handleBeforePromptBuild(hctx, ev, cx), { priority: 90 });
  on('before_agent_start', (ev, cx) => handleBeforeAgentStart(hctx, ev, cx), { priority: 90 });
  on('llm_input', (ev, cx) => handleLlmInput(hctx, ev, cx));
  on('llm_output', (ev, cx) => handleLlmOutput(hctx, ev, cx));
  on('before_tool_call', (ev, cx) => handleBeforeToolCall(hctx, ev, cx));
  on('after_tool_call', (ev, cx) => handleAfterToolCall(hctx, ev, cx));
  on('tool_result_persist', (ev, cx) => handleToolResultPersist(hctx, ev, cx));
  on('agent_end', (ev, cx) => handleAgentEnd(hctx, ev, cx));

  on('session_start', (ev, cx) => handleSessionStart(hctx, ev, cx), { priority: 90 });
  on('session_end', (ev, cx) => handleSessionEnd(hctx, ev, cx), { priority: 90 });
  on('before_reset', (ev, cx) => handleBeforeReset(hctx, ev, cx), { priority: 90 });
  on('before_compaction', (ev, cx) => handleBeforeCompaction(hctx, ev, cx), { priority: 90 });
  on('after_compaction', (ev, cx) => handleAfterCompaction(hctx, ev, cx), { priority: 90 });
  on('subagent_spawned', (ev, cx) => handleSubagentSpawned(hctx, ev, cx));
  on('subagent_ended', (ev, cx) => handleSubagentEnded(hctx, ev, cx));
  on('model_call_started', (ev, cx) => handleModelCallStarted(hctx, ev, cx));
  on('model_call_ended', (ev, cx) => handleModelCallEnded(hctx, ev, cx));

  logger.debug?.('[observ-openclaw] Registered all event hooks');

  // ── Command event hooks ───────────────────────────────────────────────────
  registerHook(
    ['command:new', 'command:reset', 'command:stop'],
    async (event: unknown) => {
      try {
        lifecycle?.beforeHook();
        const e = event as Record<string, unknown>;
        const action = (e?.action || 'unknown') as string;
        const sessionKey = (e?.sessionKey || 'unknown') as string;

        if (action === 'new' || action === 'reset') {
          clearSessionSkillCatalog(sessionKey);
        }

        // OpenClaw 2026.7.1 emits command:new for the TUI /new lifecycle.
        // It is session control rather than an agent turn, so exporting it would
        // create an empty root trace whenever no turn is active.
        if (action === 'new') return;

        const turn = getActiveTurn(sessionKey);
        const parentContext = turn?.requestContext || context.active();

        // Uses hctx.tracer getter — resolves to real or noop tracer
        const span = hctx.tracer.startSpan(
          `command.${action}`,
          {
            attributes: {
              'agentic.command.action': action,
              'gen_ai.conversation.id': resolveConversationId(sessionKey),
              'agentic.command.source': String(
                (e?.context as Record<string, unknown>)?.commandSource || 'unknown',
              ),
            },
          },
          parentContext,
        );

        span.setStatus({ code: SpanStatusCode.OK });
        span.end();
      } catch (err) {
        logger.warn?.(
          `[observ-openclaw] command event hook error: ${err instanceof Error ? err.message : String(err)}`,
        );
      }
    },
    { name: 'otel-command-events', description: 'Records session command spans via OpenTelemetry' },
  );

  logger.debug?.('[observ-openclaw] Registered command event hooks');

  // ── Periodic cleanup ──────────────────────────────────────────────────────
  const cleanupInterval = setInterval(() => {
    const now = Date.now();
    const maxAge = 5 * 60 * 1000; // 5 minutes

    for (const [turnId, turn] of turnMap) {
      if (now - turn.createdAt > maxAge) {
        try {
          for (const run of turn.llmRuns.values()) {
            if (!run.spanEnded) {
              if (run.status === 'active') {
                run.span.setStatus({
                  code: SpanStatusCode.ERROR,
                  message: 'Stale LLM run cleaned up',
                });
                run.status = 'error';
              }
              run.span.end();
              run.spanEnded = true;
              runRegistry.delete(run.runKey);
            }
          }
          for (const toolRun of turn.toolRuns.values()) {
            if (toolRun.status === 'active') {
              toolRun.span.end();
            }
          }
          finalizeTurn(turn, logger);
        } catch (cleanupErr) {
          logger.warn?.(
            `[observ-openclaw] Error cleaning up stale turn=${turnId}: ${cleanupErr instanceof Error ? cleanupErr.message : String(cleanupErr)}`,
          );
        }
        logger.debug?.(
          `[observ-openclaw] Cleaned up stale turn: turnId=${turnId}, session=${turn.sessionKey}`,
        );
      }
    }

    const sessionMaxIdle = 30 * 60 * 1000; // 30 minutes — protect sessions between turns
    evictIdleSessions(now, sessionMaxIdle, logger);
  }, 60_000);
  cleanupInterval.unref();

  // ── Return handlers + binding function ────────────────────────────────────
  const handlers: HookHandlers = {
    handleBeforeAgentStart: (ev, cx) => handleBeforeAgentStart(hctx, ev, cx),
    handleLlmInput: (ev, cx) => handleLlmInput(hctx, ev, cx),
    handleLlmOutput: (ev, cx) => handleLlmOutput(hctx, ev, cx),
    handleBeforeToolCall: (ev, cx) => handleBeforeToolCall(hctx, ev, cx),
    handleAfterToolCall: (ev, cx) => handleAfterToolCall(hctx, ev, cx),
    handleToolResultPersist: (ev, cx) => handleToolResultPersist(hctx, ev, cx),
    handleAgentEnd: (ev, cx) => handleAgentEnd(hctx, ev, cx),
    // Session, compaction, and subagent hooks
    handleSessionStart: (ev, cx) => handleSessionStart(hctx, ev, cx),
    handleSessionEnd: (ev, cx) => handleSessionEnd(hctx, ev, cx),
    handleBeforeReset: (ev, cx) => handleBeforeReset(hctx, ev, cx),
    handleBeforeCompaction: (ev, cx) => handleBeforeCompaction(hctx, ev, cx),
    handleAfterCompaction: (ev, cx) => handleAfterCompaction(hctx, ev, cx),
    handleSubagentSpawned: (ev, cx) => handleSubagentSpawned(hctx, ev, cx),
    handleSubagentEnded: (ev, cx) => handleSubagentEnded(hctx, ev, cx),
    handleModelCallStarted: (ev, cx) => handleModelCallStarted(hctx, ev, cx),
    handleModelCallEnded: (ev, cx) => handleModelCallEnded(hctx, ev, cx),
  };

  return { handlers, bindTelemetry };
}

// Re-export types needed by consumers
export type { HookHandlers, HookContext, ApiLogger } from './types.js';
export type { OpenClawApi as PluginApi };
