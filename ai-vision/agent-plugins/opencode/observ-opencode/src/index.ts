/**
 * OpenCode Observability Plugin
 *
 * A TypeScript plugin for OpenCode CLI that captures agent lifecycle events
 * and exports them via OpenTelemetry OTLP protocol to Langfuse or other
 * OTLP-compatible backends.
 *
 * @example
 * ```json
 * // opencode.json
 * {
 *   "plugin": [["observ-opencode", {
 *     "enableTelemetry": true,
 *     "otelExporter": {
 *       "endpoint": "http://localhost:4318"
 *     }
 *   }]]
 * }
 * ```
 *
 * @see https://opencode.ai/docs/plugins/
 */

import type { Plugin, PluginInput, Hooks } from '@opencode-ai/plugin';
import {
  parseConfig,
  telemetryConfigLogSummary,
  type ObservOpencodeConfig,
} from './config.js';
import { initObservabilityTelemetry, type TelemetryRuntime } from './http-runtime.js';
import { bindRuntimeProfileLease, claimRuntimeProfile, createHooks, releaseRuntimeProfile } from './core.js';
import { initLogger, log, logError } from './logger.js';
import { resolveStandaloneConfigPath } from './config-api.js';
import {
  loadStandaloneConfigFile,
  mergeConfigOptions,
} from './standalone-config.js';
import {
  discoverVcsResourceAttributes,
  mergeVcsResourceAttributes,
  resolveVcsProbeDirectory,
} from './vcs.js';
import { createFileTurnCounterStore } from './turn-counter-store.js';

/**
 * Load config from standalone JSON file when no inline options are provided.
 * Returns undefined if the file doesn't exist or can't be parsed.
 */
function loadStandaloneConfig(standaloneConfigPath: string): Record<string, unknown> | undefined {
  try {
    const parsed = loadStandaloneConfigFile(standaloneConfigPath);
    if (parsed) {
      log(`Loaded standalone config from ${standaloneConfigPath}`);
      return parsed;
    }
  } catch (err) {
    log(`WARNING: Failed to load standalone config from ${standaloneConfigPath}: ${err instanceof Error ? err.message : String(err)}`);
  }
  return undefined;
}


// OpenCode invokes every distinct function export as a Plugin factory. Keep
// runtime helper APIs in their dedicated config/core/http-runtime subpaths.
export type { ObservOpencodeConfig, TelemetryRuntime };

const HOOKS_CACHE_SLOT = Symbol.for('observ-opencode-loader.hooks.v1');

type HooksCacheValue = Hooks | Promise<Hooks>;

interface PluginGlobal {
  [key: symbol]: HooksCacheValue | undefined;
}

function pluginGlobal(): typeof globalThis & PluginGlobal {
  return globalThis as typeof globalThis & PluginGlobal;
}

/**
 * OpenCode Observability Plugin
 *
 * Captures agent lifecycle events (tool execution, message updates, session lifecycle)
 * and exports them as OpenTelemetry traces and metrics.
 *
 * Configuration options:
 * - `enableTelemetry`: Master switch (env: AGENT_ENABLE_TELEMETRY). Default: false
 * - `serviceName`: OpenTelemetry service name (env: OTEL_SERVICE_NAME). Default: "opencode"
 * - `otelExporter.attributes`: Common span/event/metric attributes; configure ant.username and ant.uid here
 * - `otelExporter.resourceAttributes`: Resource metadata; configure ant.agent.* here
 * - `otelExporter.endpoint`: OTLP endpoint URL (env: OTEL_EXPORTER_OTLP_ENDPOINT)
 * - `otelExporter.headers`: OTLP headers (env: OTEL_EXPORTER_OTLP_HEADERS)
 * - `log.spans.*`: Content logging controls (see config.ts for details)
 *
 * @param input - Plugin input from OpenCode (contains session info)
 * @param options - Plugin configuration options
 * @returns Hooks object with tool execution hooks
 */
async function initializeObservOpencodePlugin(
  input: PluginInput,
  options?: Record<string, unknown>
): Promise<{ hooks: Hooks; cache: boolean }> {
  // Parse configuration — merge standalone JSON file with inline options.
  // Standalone config (e.g. ~/.config/opencode/observ-opencode.json) provides
  // user-level settings like ant.username. Inline options from the host take
  // precedence via deep merge so that host-provided values win while
  // preserving unset fields from the standalone file.
  const standaloneConfigPath = resolveStandaloneConfigPath();
  const standalone = loadStandaloneConfig(standaloneConfigPath);
  const effectiveOptions = standalone
    ? mergeConfigOptions(standalone, (options ?? {}) as Record<string, unknown>)
    : (options ?? undefined);
  const config = parseConfig(effectiveOptions);
  initLogger(config.logDir, config.debug, config.collectionMode);

  log('========== PLUGIN LOADED ==========');
  log('Host options keys:', options ? Object.keys(options).join(', ') : '(none)');
  log(`Standalone config path: ${standaloneConfigPath} (${standalone ? 'loaded' : 'not found'})`);
  log('Standalone config keys:', standalone ? Object.keys(standalone).join(', ') : '(none)');
  log('Input:', JSON.stringify({
    hasClient: !!input?.client,
    hasProject: !!input?.project,
    directory: input?.directory,
  }));
  log(`Resolved plugin log directory: ${config.logDir}`);

  log('Resolved telemetry config:', telemetryConfigLogSummary(config));

  // Master switch check
  if (!config.enableTelemetry) {
    log('Telemetry disabled by config, returning empty hooks');
    return { hooks: {}, cache: false };
  }

  const runtimeProfileLease = claimRuntimeProfile('production');

  log('Initializing telemetry plugin...');

  // Initialize OTLP telemetry
  let runtime: TelemetryRuntime | undefined;
  try {
    let effectiveConfig = config;
    const configuredResource = config.otelExporter.resourceAttributes;
    const hasConfiguredRepository = Boolean(configuredResource['vcs.repository.url.full']?.trim());
    const hasConfiguredBranch = Boolean(configuredResource['vcs.ref.head.name']?.trim());
    if (!hasConfiguredRepository || !hasConfiguredBranch) {
      try {
        const vcsDirectory = resolveVcsProbeDirectory(input);
        if (vcsDirectory) {
          const detectedVcsAttributes = await discoverVcsResourceAttributes(vcsDirectory);
          effectiveConfig = mergeVcsResourceAttributes(config, detectedVcsAttributes);
        }
      } catch (error) {
        logError('VCS Resource discovery failed unexpectedly; continuing without automatic attributes', error);
      }
    }

    runtime = bindRuntimeProfileLease(initObservabilityTelemetry(effectiveConfig), runtimeProfileLease);
    const hooks = createHooks(runtime, config, {
      turnCounterStore: createFileTurnCounterStore(),
    });

    log('Hooks created:', Object.keys(hooks));
    log('========== PLUGIN INITIALIZED ==========');

    return { hooks: hooks as unknown as Hooks, cache: true };
  } catch (error) {
    try {
      if (runtime) await runtime.shutdown();
      else releaseRuntimeProfile(runtimeProfileLease);
    } catch (cleanupError) {
      throw new AggregateError([error, cleanupError], 'Production plugin initialization and cleanup failed');
    }
    throw error;
  }
}

/**
 * OpenCode Desktop may request the same plugin from multiple workspace
 * directories in one process. Share the first live runtime instead of
 * registering duplicate OpenTelemetry providers. Failed and disabled starts
 * are not retained, so a later valid configuration can retry initialization.
 */
export const ObservOpencodePlugin: Plugin = (
  input: PluginInput,
  options?: Record<string, unknown>,
): Promise<Hooks> => {
  const host = pluginGlobal();
  const cached = host[HOOKS_CACHE_SLOT];
  if (cached) return Promise.resolve(cached);

  const pending = initializeObservOpencodePlugin(input, options)
    .then(({ hooks, cache }) => {
      if (host[HOOKS_CACHE_SLOT] === pending) {
        if (cache) host[HOOKS_CACHE_SLOT] = hooks;
        else delete host[HOOKS_CACHE_SLOT];
      }
      return hooks;
    }, error => {
      if (host[HOOKS_CACHE_SLOT] === pending) delete host[HOOKS_CACHE_SLOT];
      throw error;
    });
  host[HOOKS_CACHE_SLOT] = pending;
  return pending;
};

export default ObservOpencodePlugin;
