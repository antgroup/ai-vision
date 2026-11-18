/**
 * observ-dsh: the production cordis plugin entry.
 *
 * Loaded from a dsh profile's cordis.yml:
 *
 *   - id: observ-dsh
 *     name: 'observ-dsh'
 *
 * Configuration comes from a JSON file with the same schema as pi/opencode
 * (see configs/aivision.config.json). Resolution order: cordis
 * `config.configFile` → DSH_OBSERV_CONFIG → ~/.config/dsh/aivision.config.json
 * → the bundled configs/aivision.config.json. Cordis config entries deep-merge
 * over the file; DSH_OBSERV_ENABLED still overrides enableTelemetry.
 *
 * The plugin subscribes to the dsh session firehose and agent bus directly;
 * it deliberately does NOT depend on @deepseek-ai/dsh-session-telemetry.
 */

import type { Context } from '@deepseek-ai/cordis';
import type { Session } from '@deepseek-ai/dsh-session';
import type { Agent } from '@deepseek-ai/dsh-agent';
import z from '@deepseek-ai/schemastery';
import { createDshSemanticEngine, type DshSemanticEngine } from './core.js';
import { loadObservDshConfigFile, mergeConfigOptions, parseConfig } from './config.js';
import { initTelemetry, type TelemetryRuntime } from './telemetry.js';
import { initLogger, log, logError } from './logger.js';
import { hostImageReader, type DshImageReference } from './media.js';
import type {
  DshAgentErrorPayload,
  DshAgentRequestErrorPayload,
  DshAgentRequestPayload,
  DshLlmCallConfig,
  DshSessionLike,
} from './types.js';

export const name = 'observ-dsh';

export const inject = ['sessions', 'agents'];

export const Config = z.object({
  enableTelemetry: z.boolean().default(false),
  serviceName: z.string().default('dsh'),
  collectionMode: z.union(['full', 'privacy']).default('full'),
  debug: z.boolean().default(false),
  dynamicUsername: z.boolean().default(false),
  dynamicUid: z.boolean().default(false),
  configFile: z.string().default(''),
  log: z.any(),
  otelExporter: z.any(),
  tracesConfig: z.any(),
  metricsConfig: z.any(),
  eventsConfig: z.any(),
  metricExportInterval: z.number().default(60000),
  logsExportInterval: z.number().default(5000),
});

type _SessionRef = Session;
type _AgentRef = Agent;
void (null as unknown as _SessionRef);
void (null as unknown as _AgentRef);

function asSessionLike(session: unknown): DshSessionLike {
  // The live dsh Session object already satisfies the structural contract.
  return session as DshSessionLike;
}

export function apply(ctx: Context, rawConfig: unknown = {}): () => void {
  const cordisConfig = (rawConfig && typeof rawConfig === 'object' && !Array.isArray(rawConfig)
    ? rawConfig : {}) as Record<string, unknown>;
  const fileConfig = loadObservDshConfigFile(
    typeof cordisConfig.configFile === 'string' ? cordisConfig.configFile : undefined,
  );
  const config = parseConfig(mergeConfigOptions(fileConfig, cordisConfig));
  initLogger(config.debug, config.collectionMode);
  log('observ-dsh apply', config.enableTelemetry ? 'enabled' : 'disabled');

  const runtime: TelemetryRuntime = initTelemetry(config);
  const engine: DshSemanticEngine = createDshSemanticEngine(runtime, config, {
    readImage: (ref: DshImageReference) => {
      const provider = ctx.get('attachments') as { imageHostPath(ref: DshImageReference): string | undefined; fileHostPath?(ref: DshImageReference): string | undefined } | undefined;
      if (!provider) throw new Error('Attachment provider unavailable');
      return hostImageReader(provider)(ref);
    },
  });

  ctx.on('session/created', (session) => {
    try {
      engine.handleSessionCreated(asSessionLike(session));
    } catch (error) {
      logError('session/created handler failed', error);
    }
  });

  ctx.on('session/event', (session, event) => {
    try {
      engine.handleSessionEvent(asSessionLike(session), event as never);
    } catch (error) {
      logError(`session/event ${String((event as { type?: string }).type)} handler failed`, error);
    }
  });

  ctx.on('session/disposed', (session) => {
    try {
      engine.handleSessionDisposed(asSessionLike(session));
    } catch (error) {
      logError('session/disposed handler failed', error);
    }
  });

  ctx.on('agent/request', (payload, next) => {
    try {
      engine.handleAgentRequest(
        asSessionLike((payload.agent as { session?: unknown })?.session),
        payload as DshAgentRequestPayload,
        undefined,
      );
    } catch (error) {
      logError('agent/request handler failed', error);
    }
    return next();
  });

  ctx.on('agent/request-error', (payload, next) => {
    try {
      engine.handleAgentRequestError(
        asSessionLike((payload.agent as { session?: unknown })?.session),
        payload as DshAgentRequestErrorPayload,
      );
    } catch (error) {
      logError('agent/request-error handler failed', error);
    }
    return next();
  });

  ctx.on('agent/error', (payload) => {
    try {
      engine.handleAgentError(
        asSessionLike((payload.agent as { session?: unknown })?.session),
        payload as DshAgentErrorPayload,
      );
    } catch (error) {
      logError('agent/error handler failed', error);
    }
  });

  ctx.effect(() => async () => {
    try {
      engine.forceCloseAll();
      await runtime.shutdown();
    } catch (error) {
      logError('observ-dsh shutdown failed', error);
    }
  });

  return () => {
    engine.reset();
  };
}

export default apply;

export type { DshLlmCallConfig };
