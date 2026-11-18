/**
 * OpenClaw Observability Plugin
 *
 * OpenTelemetry-based observability for OpenClaw with semantic conventions for AI agents.
 * Records all events from a single question/interaction in OpenClaw as distributed traces.
 *
 * Requires OpenClaw 3.22+ (uses plugin SDK types).
 *
 * Telemetry initialization is deferred to service.start() to avoid the first-config-wins
 * race in multi-worker environments. Hooks are registered eagerly in
 * register() with noop tracer/logger; real telemetry is bound via bindTelemetry() once
 * initialized. If start() is never called (worker-only instances), telemetry is lazily
 * initialized on the first event that requires it.
 *
 * Usage in openclaw config:
 * {
 *   "plugins": {
 *     "entries": {
 *       "observ-openclaw": {
 *         "enabled": true,
 *         "config": {
 *           "otelExporter": {
 *             "attributes": { "ant.username": "your-username" }
 *           }
 *         }
 *       }
 *     }
 *   }
 * }
 */

import {
  parseConfig,
  telemetryConfigLogSummary,
  type ObservOpenClawConfig,
} from './src/config.js';
import { initObservabilityTelemetry, startObservabilityTelemetry, type TelemetryRuntime } from './src/telemetry.js';
import { createHooks, type HookRegistration, type OpenClawApi } from './src/hooks/index.js';
import { getActiveTurn } from './src/hooks/state.js';
import { initLogger, log, logError, pluginFileLogger } from './src/logger.js';
import { installTraceInterceptor } from './src/trace-bridge.js';

import type { OpenClawPluginApi, OpenClawPluginServiceContext } from 'openclaw/plugin-sdk/plugin-entry';

// ─── Singleton state ─────────────────────────────────────────────────────────

// Module-level singleton: telemetry is initialized once and shared across all
// plugin instances. A host can create separate plugin instances for its gateway and
// each agent worker — all call register(), but OTel providers (tracer, meter,
// logger) must only be registered globally once to avoid conflicts.
let sharedTelemetry: TelemetryRuntime | null = null;
let telemetryStarted = false;

// Deferred init state: config is stored during register(), telemetry is
// initialized in start() or lazily on first event.
let pendingConfig: ObservOpenClawConfig | null = null;
const pendingBindings: Array<(rt: TelemetryRuntime) => void> = [];

/** stateDir from ServiceContext, retained for runtime initialization compatibility. */
let resolvedStateDir: string | undefined;

/**
 * Ensure telemetry is initialized — called by start() and as lazy fallback.
 * Returns the initialized runtime, or null if no config is available.
 */
function ensureTelemetryInit(): TelemetryRuntime | null {
  if (sharedTelemetry) return sharedTelemetry;
  if (!pendingConfig) return null;

  log('Deferred telemetry initialization...');
  try {
    sharedTelemetry = initObservabilityTelemetry(pendingConfig, resolvedStateDir);
    // Bind telemetry to all registered hook instances
    for (const bind of pendingBindings) {
      bind(sharedTelemetry);
    }
    pendingBindings.length = 0;
    log('Deferred telemetry initialization complete');
  } catch (err) {
    logError('Failed to initialize deferred telemetry', err);
    return null;
  }
  return sharedTelemetry;
}

async function flushLocalTask(event: unknown, context: unknown): Promise<void> {
  // Local CLI runs do not start/stop gateway services. Flush their final spans
  // before the host exits, with the same bounded wait as service shutdown.
  if (telemetryStarted || !sharedTelemetry) return;
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    const e = event as Record<string, unknown> | undefined;
    const c = context as Record<string, unknown> | undefined;
    const turn = getActiveTurn(String(e?.sessionKey || c?.sessionKey || 'unknown'));
    // Preserve the semantic engine's 5s grace period for a missing llm_output
    // (including model errors). Its unref'd timer alone cannot keep a CLI alive.
    const deadline = Date.now() + 5250;
    while (turn && turn.phase !== 'finalized' && !(turn.pendingSubagents?.size) && Date.now() < deadline) {
      await new Promise(resolve => setTimeout(resolve, 25));
    }
    await Promise.race([
      sharedTelemetry.forceFlush(),
      new Promise<void>(resolve => { timer = setTimeout(resolve, 3000); }),
    ]);
  } catch {
    log('Local task telemetry flush failed');
  } finally {
    if (timer) clearTimeout(timer);
  }
}

// ─── Plugin definition ───────────────────────────────────────────────────────

export default {
  id: 'observ-openclaw',
  name: 'OpenClaw Observability',
  description: 'OpenTelemetry-based observability with semantic conventions for AI agents',

  // NOTE: configSchema intentionally omitted.
  // api.config is the FULL openclaw.json at runtime (not plugin-specific config).
  // api.pluginConfig is the correct plugin-scoped config source.

  register(api: OpenClawPluginApi) {
    // Always use api.pluginConfig — it contains the plugin-scoped config from
    // plugins.entries["observ-openclaw"].config in openclaw.json.
    // runtime.env exists at runtime for gray/prod detection but is not in SDK types.
    const runtimeEnv = (api.runtime as Record<string, unknown>)?.env as string | undefined;
    const config: ObservOpenClawConfig = parseConfig(api.pluginConfig, runtimeEnv);
    initLogger(config.logDir, config.debug, config.collectionMode);
    log('register() called, mode:', api.registrationMode,
        'pluginConfigKeys:', Object.keys((api.pluginConfig ?? {}) as Record<string, unknown>));
    log('Resolved plugin log directory:', config.logDir);
    log('Resolved telemetry config:', telemetryConfigLogSummary(config));
    const logger = pluginFileLogger;

    log('enableTelemetry:', config.enableTelemetry);
    if (!config.enableTelemetry) {
      log('Telemetry DISABLED by config — skipping initialization');
      return;
    }

    // Install HTTP trace interceptor (idempotent — safe to call multiple times)
    installTraceInterceptor();
    log('Trace interceptor installed');

    // Store config for deferred init — latest register() with enableTelemetry wins.
    // Multiple workers may call register() with different configs;
    // the last one (typically the user-facing instance) takes precedence.
    pendingConfig = config;

    // Register hooks eagerly with lazy telemetry binding.
    // Hooks use noop tracer/logger until bindTelemetry() is called.
    log('Creating hooks (deferred telemetry)...');
    // Cast to OpenClawApi: SDK's typed on() is stricter than hooks' string-based on()
    const { handlers, bindTelemetry } = createHooks(
      api as unknown as OpenClawApi,
      null,
      config,
      pluginFileLogger,
      { beforeHook: ensureTelemetryInit, afterAgentEnd: flushLocalTask },
    );
    pendingBindings.push(bindTelemetry);

    // If telemetry was already initialized by a prior register() → start() cycle,
    // bind it immediately to this hook instance.
    if (sharedTelemetry) {
      bindTelemetry(sharedTelemetry);
      log('Hooks bound to existing shared telemetry');
    }

    log('Hooks created successfully');
    logger.debug?.(`[observ-openclaw] Hooks registered (mode=${api.registrationMode})`);

    api.registerService({
      id: 'observ-openclaw',

      start: async (ctx: OpenClawPluginServiceContext) => {
        log('Service start() called, telemetryStarted:', telemetryStarted,
            'stateDir:', ctx.stateDir);

        resolvedStateDir = ctx.stateDir;

        if (!telemetryStarted) {
          const rt = ensureTelemetryInit();
          if (rt) {
            await startObservabilityTelemetry(rt);
            telemetryStarted = true;
            log('Telemetry started');
            logger.debug?.(`[observ-openclaw] Telemetry initialized: endpoint=${pendingConfig?.otelExporter.endpoint || 'edition-default'}`);
          }
        }
      },

      stop: async (_ctx: OpenClawPluginServiceContext) => {
        log('Service stop() called');
        if (sharedTelemetry) {
          await sharedTelemetry.shutdown();
          sharedTelemetry = null;
          telemetryStarted = false;
          pendingConfig = null;
          pendingBindings.length = 0;
          log('Telemetry shut down');
        }
      },
    });
  },
};

export * from './src/semconv.js';

export { parseConfig, type ObservOpenClawConfig } from './src/config.js';
export {
  initObservabilityTelemetry,
  startObservabilityTelemetry,
  buildResourceAttributes,
  type TelemetryRuntime,
} from './src/telemetry.js';
export {
  registerTelemetryProcessorContribution,
  type TelemetryProcessorContribution,
} from './src/telemetry-extensions.js';
export { createHooks, type HookHandlers, type HookContext, type ApiLogger, type HookRegistration, type OpenClawApi } from './src/hooks/index.js';
export { installTraceInterceptor, getTraceContext, getSofaTraceId, getSofaRpcId } from './src/trace-bridge.js';
export { parseSofaTraceContext, buildSofaSpanLink, type SofaTraceContext } from './src/sofa-otel.js';
