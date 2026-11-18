import type { ExtensionAPI, ExtensionContext } from '@earendil-works/pi-coding-agent';
import { existsSync } from 'node:fs';
import { loadConfig, resolvePiagentConfigPaths } from './config.js';
import { CanonicalNormalizer } from './normalize.js';
import { projectProviderUsage } from './provider-usage.js';
import { SemanticEngine } from './semantic.js';
import { createTelemetry, debugFiles } from './telemetry.js';
import { writePluginLog } from './logger.js';
import type { Correlation, PiHook } from './types.js';
import type { PiagentConfig } from './config.js';
import type { TelemetryRuntime } from './telemetry.js';
import { createFileTurnCounterStore } from './turn-counter-store.js';
import type { TurnCounterStore, TurnStateDiagnostic } from './turn-counter-store.js';

/**
 * piagent — a pi extension that emits AiVision-semantic OpenTelemetry
 * (traces / events / metrics) in real time to the local OTLP gateway.
 *
 * **Responsibility: telemetry only.** This extension normalizes pi lifecycle
 * hooks and feeds them to a `SemanticEngine` that produces OTLP observations
 * via the standard OpenTelemetry SDK. It does NOT record transcripts and does
 * NOT provide a replay CLI — those are the responsibility of the sibling
 * `pireplay` extension (see `pi/pireplay`).
 *
 * When `OBSERV_PI_DEBUG=true`, the same OTLP data is additionally dual-written
 * to JSONL files under `OBSERV_PI_REPLAY_DIR` (for golden parity comparison),
 * without interrupting HTTP export.
 *
 * Config:
 *   ~/.pi/agent/observ-pi.json    Global Pi configuration directory
 *   .pi/observ-pi.json            Trusted project configuration directory
 *   PI_CODING_AGENT_DIR                Relocates the Pi global configuration directory
 *
 * Env overrides:
 *   AGENT_ENABLE_TELEMETRY=true   Enable production telemetry
 *   OBSERV_PI_DEBUG=true            Enable local file dual-write (realtime OTLP JSONL)
 *   OBSERV_PI_REPLAY_DIR=<dir>      Debug file output directory
 *   OBSERV_PI_SESSION_NAME=<label>  Debug file artifact label
 *   OBSERV_PI_STATE_DIR=<dir>       Persisted resume high-water state directory
 *   AIVISION_COLLECTION_MODE      full | privacy
 *   OTEL_EXPORTER_OTLP_ENDPOINT   OTLP endpoint (default http://localhost:4319/)
 *   AIVISION_ANT_USERNAME         ant.username attribute
 */

const HOOKS: PiHook[] = [
  'session_start', 'session_shutdown', 'before_agent_start', 'agent_settled', 'turn_start',
  'before_provider_request', 'after_provider_response', 'provider_stream_event', 'message_update', 'message_end',
  'tool_execution_start', 'tool_execution_update', 'tool_execution_end',
  'session_before_compact', 'session_compact', 'model_select'
];

const HIGH_FREQUENCY_HOOKS = new Set<PiHook>(['message_update', 'tool_execution_update']);
const SENSITIVE_DIAGNOSTIC_KEY = /authorization|proxy-authorization|cookie|token|secret|password|api[-_]?key/i;
const EVENT_SEQUENCE_RESERVATION_SIZE = 1024;

interface HookSignalCounts {
  spansStarted: number;
  spansEnded: number;
  logs: number;
  metrics: number;
}

function signalSummary(counts: HookSignalCounts): string {
  const total = counts.spansStarted + counts.spansEnded + counts.logs + counts.metrics;
  return `observations=${total} spans_started=${counts.spansStarted} spans_ended=${counts.spansEnded} logs=${counts.logs} metrics=${counts.metrics}`;
}

function endpointSummary(endpoint: string | undefined): string {
  if (!endpoint) return 'unset';
  try {
    const parsed = new URL(endpoint);
    return `${parsed.origin}${parsed.pathname}`;
  } catch {
    return 'configured';
  }
}

function debugEndpoint(endpoint: string | undefined): string {
  if (!endpoint) return 'unset';
  try {
    const parsed = new URL(endpoint);
    parsed.username = '';
    parsed.password = '';
    for (const key of [...parsed.searchParams.keys()]) {
      if (SENSITIVE_DIAGNOSTIC_KEY.test(key)) parsed.searchParams.set(key, '[REDACTED]');
    }
    return parsed.toString();
  } catch {
    return 'configured';
  }
}

function debugHeaders(headers: Record<string, string>): string {
  return JSON.stringify(Object.fromEntries(
    Object.entries(headers)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, value]) => [key, SENSITIVE_DIAGNOSTIC_KEY.test(key) ? '[REDACTED]' : value]),
  ));
}

function headerNames(headers: Record<string, string>): string {
  return JSON.stringify(Object.keys(headers).sort());
}

function validTurnIndex(raw: unknown, fallback: number): number {
  if (!raw || typeof raw !== 'object') return fallback;
  const value = (raw as { turnIndex?: unknown }).turnIndex;
  return typeof value === 'number' && Number.isInteger(value) && value >= 0
    ? value
    : fallback;
}

interface PiagentDependencies {
  createTelemetry: typeof createTelemetry;
  createTurnCounterStore: (diagnostic: TurnStateDiagnostic) => TurnCounterStore;
}

interface EventSequenceAllocator {
  bind(sessionId: string): void;
  next(): number | undefined;
}

function createEventSequenceAllocator(
  store: TurnCounterStore,
  diagnostic: TurnStateDiagnostic,
): EventSequenceAllocator {
  let sessionId: string | undefined;
  let lastIssued = 0;
  let reservedThrough = 0;

  const reserve = (): boolean => {
    if (!sessionId) return false;
    try {
      const reservation = store.reserveEventSequence(
        sessionId,
        lastIssued,
        EVENT_SEQUENCE_RESERVATION_SIZE,
      );
      if (!reservation
        || !Number.isSafeInteger(reservation.startExclusive)
        || !Number.isSafeInteger(reservation.endInclusive)
        || reservation.startExclusive < lastIssued
        || reservation.endInclusive <= reservation.startExclusive) {
        return false;
      }
      lastIssued = reservation.startExclusive;
      reservedThrough = reservation.endInclusive;
      return true;
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      diagnostic(`failed to reserve event sequence: ${detail}`);
      return false;
    }
  };

  return {
    bind(nextSessionId: string) {
      if (sessionId === nextSessionId) return;
      sessionId = nextSessionId;
      reserve();
    },
    next() {
      if (!sessionId) return undefined;
      if (lastIssued >= reservedThrough && !reserve()) return undefined;
      return ++lastIssued;
    },
  };
}

export default function piagent(
  pi: ExtensionAPI,
  dependencies: Partial<PiagentDependencies> = {},
): void {
  const createTelemetryRuntime = dependencies.createTelemetry ?? createTelemetry;
  const createTurnCounterStoreRuntime = dependencies.createTurnCounterStore
    ?? ((diagnostic: TurnStateDiagnostic) => createFileTurnCounterStore(undefined, diagnostic));
  let runtime: {
    config: PiagentConfig;
    normalizer: CanonicalNormalizer;
    telemetry: TelemetryRuntime;
    engine: SemanticEngine;
    sessionId?: string;
    turnCounterStore: TurnCounterStore;
    eventSequenceAllocator: EventSequenceAllocator;
  } | undefined;
  let initializationComplete = false;
  let turn = 0;
  let piTurn = 0;
  let closed = false;
  let diagnosticConfig: PiagentConfig | undefined;
  let currentHookSignals: HookSignalCounts | undefined;

  function resolveSessionId(context: ExtensionContext): string {
    try {
      const sessionId = context.sessionManager?.getSessionId?.();
      return typeof sessionId === 'string' && sessionId.trim() ? sessionId : 'unknown';
    } catch {
      return 'unknown';
    }
  }

  function loadTurnCounter(store: TurnCounterStore, sessionId: string, config: PiagentConfig): number {
    try {
      return store.load(sessionId);
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      writePluginLog(config, 'warn', `failed to load persisted turn counter: ${detail}`);
      return 0;
    }
  }

  function bindSessionState(
    active: NonNullable<typeof runtime>,
    context: ExtensionContext,
  ): void {
    if (active.sessionId) return;
    const sessionId = resolveSessionId(context);
    if (sessionId === 'unknown') return;
    const restoredTurn = loadTurnCounter(active.turnCounterStore, sessionId, active.config);
    turn = Math.max(turn, restoredTurn);
    active.engine.restoreTurnNumber(turn);
    active.eventSequenceAllocator.bind(sessionId);
    active.sessionId = sessionId;
  }

  function initialize(ctx: ExtensionContext): typeof runtime {
    if (initializationComplete) return runtime;
    const projectTrusted = ctx.isProjectTrusted();
    const configPaths = resolvePiagentConfigPaths({ cwd: ctx.cwd });
    const config = loadConfig(undefined, {
      cwd: ctx.cwd,
      projectTrusted,
    });
    diagnosticConfig = config;
    const globalLoaded = existsSync(configPaths.global);
    const projectExists = existsSync(configPaths.project);
    writePluginLog(
      config,
      'info',
      `config loaded global_path=${JSON.stringify(configPaths.global)} global_loaded=${globalLoaded} project_path=${JSON.stringify(configPaths.project)} project_exists=${projectExists} project_trusted=${projectTrusted} project_loaded=${projectTrusted && projectExists}`,
    );
    writePluginLog(
      config,
      'info',
      `config resolved telemetry_enabled=${config.enableTelemetry} service=${config.serviceName} collection_mode=${config.collectionMode} protocol=${config.otelExporter.protocol} debug=${config.debug}`,
    );
    writePluginLog(config, 'info', `signal configured name=traces enabled=${config.tracesConfig.enabled} endpoint=${endpointSummary(config.tracesConfig.endpoint)} header_count=${Object.keys(config.tracesConfig.headers).length} header_names=${headerNames(config.tracesConfig.headers)}`);
    writePluginLog(config, 'info', `signal configured name=metrics enabled=${config.metricsConfig.enabled} endpoint=${endpointSummary(config.metricsConfig.endpoint)} export_interval_ms=${config.metricExportInterval} temporality=${config.otelExporter.metricsTemporalityPreference} header_count=${Object.keys(config.metricsConfig.headers).length} header_names=${headerNames(config.metricsConfig.headers)}`);
    writePluginLog(config, 'info', `signal configured name=logs enabled=${config.eventsConfig.enabled} endpoint=${endpointSummary(config.eventsConfig.endpoint)} export_interval_ms=${config.logsExportInterval} header_count=${Object.keys(config.eventsConfig.headers).length} header_names=${headerNames(config.eventsConfig.headers)}`);
    if (!config.enableTelemetry) {
      initializationComplete = true;
      writePluginLog(config, 'info', 'telemetry disabled by configuration');
      return undefined;
    }
    const resolvedSessionId = resolveSessionId(ctx);
    const sessionId = resolvedSessionId === 'unknown' ? undefined : resolvedSessionId;
    const turnCounterStore = createTurnCounterStoreRuntime(
      message => writePluginLog(config, 'warn', message),
    );
    turn = sessionId ? loadTurnCounter(turnCounterStore, sessionId, config) : 0;
    const eventSequenceAllocator = createEventSequenceAllocator(
      turnCounterStore,
      message => writePluginLog(config, 'warn', message),
    );
    if (sessionId) eventSequenceAllocator.bind(sessionId);
    const normalizer = new CanonicalNormalizer(config.collectionMode);
    const telemetry = createTelemetryRuntime(
      config,
      debugFiles(config),
      'realtime',
      (level, message) => writePluginLog(config, level, message),
    );
    runtime = {
      config,
      normalizer,
      telemetry,
      sessionId,
      turnCounterStore,
      eventSequenceAllocator,
      engine: new SemanticEngine(config, observation => {
        telemetry.consume(observation);
        if (!currentHookSignals) return;
        if (observation.type === 'span.start') currentHookSignals.spansStarted++;
        else if (observation.type === 'span.end') currentHookSignals.spansEnded++;
        else if (observation.type === 'event') currentHookSignals.logs++;
        else currentHookSignals.metrics++;
      }, turn, () => eventSequenceAllocator.next()),
    };
    initializationComplete = true;
    writePluginLog(
      config,
      'info',
      `telemetry initialized service=${config.serviceName} collection_mode=${config.collectionMode} debug=${config.debug}`,
    );
    writePluginLog(config, 'debug', `otel exporter signal=traces endpoint=${debugEndpoint(config.tracesConfig.endpoint)} headers=${debugHeaders(config.tracesConfig.headers)}`);
    writePluginLog(config, 'debug', `otel exporter signal=metrics endpoint=${debugEndpoint(config.metricsConfig.endpoint)} headers=${debugHeaders(config.metricsConfig.headers)}`);
    writePluginLog(config, 'debug', `otel exporter signal=logs endpoint=${debugEndpoint(config.eventsConfig.endpoint)} headers=${debugHeaders(config.eventsConfig.headers)}`);
    return runtime;
  }

  for (const hook of HOOKS) {
    pi.on(hook as never, async (raw: unknown, _ctx: ExtensionContext) => {
      if (closed) return;
      try {
        const active = initialize(_ctx);
        if (!active) return;
        bindSessionState(active, _ctx);
        if (HIGH_FREQUENCY_HOOKS.has(hook)) {
          writePluginLog(active.config, 'debug', `hook ignored name=${hook} reason=no_semantic_observation`);
          return;
        }
        if (hook === 'provider_stream_event'
          && (!raw || typeof raw !== 'object'
            || projectProviderUsage(raw as Record<string, unknown>) === undefined)) {
          writePluginLog(active.config, 'debug', `hook ignored name=${hook} reason=no_usage_measurement`);
          return;
        }
        if (hook === 'before_agent_start') {
          turn++;
          if (active.sessionId) {
            try {
              active.turnCounterStore.save(active.sessionId, turn);
            } catch (error) {
              const detail = error instanceof Error ? error.message : String(error);
              writePluginLog(active.config, 'warn', `failed to persist turn counter: ${detail}`);
            }
          }
          piTurn = 0;
        }
        if (hook === 'turn_start') piTurn = validTurnIndex(raw, piTurn);
        const correlation: Correlation = { user_turn: turn, pi_turn_index: piTurn };
        if ('toolCallId' in ((raw as object) ?? {})) correlation.tool_call_id = String((raw as { toolCallId?: string }).toolCallId ?? '');
        const event = active.normalizer.normalize(hook, raw, _ctx, correlation);
        currentHookSignals = { spansStarted: 0, spansEnded: 0, logs: 0, metrics: 0 };
        active.engine.apply(event);
        writePluginLog(
          active.config,
          'info',
          `hook processed name=${hook} sequence=${event.sequence} user_turn=${turn} pi_turn_index=${piTurn} ${signalSummary(currentHookSignals)} redacted=${event.projection.redacted} truncated=${event.projection.truncated}`,
        );
        currentHookSignals = undefined;
      } catch (error) {
        currentHookSignals = undefined;
        if (!runtime || runtime.config.debug) {
          console.warn(`[piagent] ${hook} failed: ${error instanceof Error ? error.message : String(error)}`);
        }
        const config = runtime?.config ?? diagnosticConfig;
        if (config) {
          writePluginLog(
            config,
            'warn',
            `${hook} failed: ${error instanceof Error ? error.message : String(error)}`,
          );
        }
      } finally {
        if (hook === 'session_shutdown') {
          closed = true;
          try {
            await runtime?.telemetry.shutdown();
          } catch (error) {
            const message = error instanceof Error ? error.message : String(error);
            if (!runtime || runtime.config.debug) console.warn(`[piagent] telemetry shutdown failed: ${message}`);
            const config = runtime?.config ?? diagnosticConfig;
            if (config) writePluginLog(config, 'warn', `telemetry shutdown failed: ${message}`);
          }
        }
      }
    });
  }
}

// Re-exports for sibling packages (e.g. pi/pireplay) that need to reuse the
// canonical normalizer / semantic engine / OTLP telemetry stack.
export { CanonicalNormalizer, canonicalChecksum, safeArtifactPart } from './normalize.js';
export { InputMaterializer } from './input-materializer.js';
export { SemanticEngine } from './semantic.js';
export {
  loadConfig,
  loadPiagentConfigFiles,
  mergeConfigOptions,
  parseConfig,
  parseKeyValueString,
  DEFAULT_OTLP_HEADERS,
  OBSERV_PI_CONFIG_FILE,
  resolvePiagentConfigPaths,
  resolveSignalAttributes,
} from './config.js';
export { createTelemetry, debugFiles } from './telemetry.js';
export { isAivisionEdition, resolveEditionConfig, resolveEditionName } from './edition-config.js';
export type { EditionName } from './edition-config.js';
export type {
  ConfigLoadContext,
  MetricsTemporalityPreference,
  OtelExporterConfig,
  OtlpProtocol,
  PiagentConfig,
  PiagentConfigPaths,
  SignalEndpointConfig,
} from './config.js';
export type { TelemetryRuntime } from './telemetry.js';
export type {
  CanonicalEvent,
  CanonicalSupplementalEvent,
  Correlation,
  InputMaterialization,
  InputMaterializationRelation,
  InputMaterializationScope,
  PiHook,
  SemanticObservation,
  SpanKindName,
  CollectionIntegrity
} from './types.js';
