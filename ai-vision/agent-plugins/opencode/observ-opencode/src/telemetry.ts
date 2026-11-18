import { privacyProcessor, privacyMetricExporter } from '../../../shared/collection-mode.mjs';
import { ingestHeaders } from '../../../shared/ingest-auth.mjs';
import { trace, type Tracer } from '@opentelemetry/api';
import { metrics, type Meter } from '@opentelemetry/api';
import { logs, type Logger } from '@opentelemetry/api-logs';
import { NodeTracerProvider } from '@opentelemetry/sdk-trace-node';
import { BatchSpanProcessor, type SpanProcessor } from '@opentelemetry/sdk-trace-base';
import { OTLPTraceExporter } from '@opentelemetry/exporter-trace-otlp-http';
import { MeterProvider, PeriodicExportingMetricReader, type MetricReader } from '@opentelemetry/sdk-metrics';
import { AggregationTemporalityPreference, OTLPMetricExporter } from '@opentelemetry/exporter-metrics-otlp-http';
import { LoggerProvider, SimpleLogRecordProcessor, type LogRecordProcessor } from '@opentelemetry/sdk-logs';
import { OTLPLogExporter } from '@opentelemetry/exporter-logs-otlp-http';
import { defaultResource, resourceFromAttributes } from '@opentelemetry/resources';
import { ATTR_SERVICE_NAME } from '@opentelemetry/semantic-conventions';
import os from 'os';
import { telemetryConfigLogSummary, type ObservOpencodeConfig, type SignalEndpointConfig } from './config.js';
import { log, logError } from './logger.js';
import packageJson from '../package.json' with { type: 'json' };
import {
  ANT_META_TRIGGER_KEY,
  resolveMetaTrigger,
} from '../../../shared/meta-trigger.mjs';

// PLUGIN_VERSION is injected by esbuild at build time (see scripts/build.ts)
// Direct tsx/replay execution reads the same canonical package version.
declare const PLUGIN_VERSION: string;
const VERSION = typeof PLUGIN_VERSION !== 'undefined' ? PLUGIN_VERSION : packageJson.version;

export interface TelemetryRuntime {
  tracer: Tracer;
  meter: Meter;
  logger: Logger;
  shutdown: () => Promise<void>;
  forceFlush: () => Promise<void>;
  tracesEnabled: boolean;
  metricsEnabled: boolean;
  eventsEnabled: boolean;
  /** Resolved ant.username at startup time (used for signal attribute injection) */
  resolvedUsername: string;
  /** Resolved ant.agent.scene at startup time (used for span attribute injection) */
  resolvedAgentScene: string;
}

/** Optional processors/readers supplied by a local adapter such as opencode-replay. */
export interface HttpRuntimeExtensions {
  spanProcessors?: SpanProcessor[];
  metricReaders?: MetricReader[];
  logRecordProcessors?: LogRecordProcessor[];
}

export function buildResourceAttributes(
  config: ObservOpencodeConfig,
  overrides: { serviceName?: string; agentInstance?: string } = {},
): Record<string, string> {
  const configured = { ...config.otelExporter.resourceAttributes };
  delete configured['ant.username'];
  delete configured['ant.uid'];
  configured[ANT_META_TRIGGER_KEY] = resolveMetaTrigger(
    configured[ANT_META_TRIGGER_KEY],
    undefined,
  );

  const agentType = configured['ant.agent.type'] || 'opencode';
  const agentProduct = configured['ant.agent.product'] || 'OpenCode';
  const agentInstance = overrides.agentInstance
    || configured['ant.agent.instance']
    || (() => { try { return os.hostname(); } catch { return ''; } })()
    || 'unknown';
  const agentEnv = configured['ant.agent.env'] || 'Default';
  const agentScene = configured['ant.agent.scene'] || 'default';

  const attributes: Record<string, string> = {
    [ATTR_SERVICE_NAME]: overrides.serviceName || config.serviceName,
    'service.version': VERSION,
    'agentic.collection.mode': config.collectionMode,
    'ant.agent.type': agentType,
    'ant.agent.product': agentProduct,
    'ant.agent.instance': agentInstance,
    'ant.agent.env': agentEnv,
    'deployment.environment': agentEnv,
    'ant.agent.scene': agentScene,
    ...configured,
  };

  attributes['ant.agent.type'] = agentType;
  attributes['ant.agent.product'] = agentProduct;
  attributes['ant.agent.instance'] = agentInstance;
  attributes['ant.agent.env'] = agentEnv;
  attributes['ant.agent.scene'] = agentScene;
  attributes['agentic.collection.mode'] = config.collectionMode;
  // Final boundary guard for callers that construct ObservOpencodeConfig directly.
  delete attributes.username;
  delete attributes['ant.username'];
  delete attributes['ant.uid'];
  return attributes;
}

function getEndpointAndHeaders(
  signalConfig: SignalEndpointConfig | undefined,
  path: string
): { url: string; headers: Record<string, string> } | null {
  const endpoint = signalConfig?.endpoint;
  if (!endpoint) return null;

  const headers = signalConfig?.headers ?? {};
  const url = endpoint.includes('/v1/') ? endpoint : `${endpoint.replace(/\/$/, '')}${path}`;

  return { url, headers };
}

export function initObservabilityTelemetry(
  config: ObservOpencodeConfig,
  extensions: HttpRuntimeExtensions = {},
): TelemetryRuntime {
  log('initObservabilityTelemetry called');
  log('Config:', telemetryConfigLogSummary(config));

  const resourceAttributes = buildResourceAttributes(config);
  const resolvedUsername = config.otelExporter.attributes['ant.username'] || 'opencode';
  const resolvedAgentScene = resourceAttributes['ant.agent.scene'];

  const resource = defaultResource().merge(resourceFromAttributes(resourceAttributes));

  const tracesEnabled = config.tracesConfig.enabled;
  const metricsEnabled = config.metricsConfig.enabled;
  const eventsEnabled = config.eventsConfig.enabled;

  log('Signal config:', { tracesEnabled, metricsEnabled, eventsEnabled });

  const providers: { shutdown: () => Promise<void> }[] = [];

  let tracerProvider: NodeTracerProvider | undefined;

  const spanProcessors = tracesEnabled ? [...(extensions.spanProcessors ?? [])] : [];
  if (tracesEnabled) {
    const tracesConfig = getEndpointAndHeaders(
      config.tracesConfig,
      '/v1/traces'
    );

    if (tracesConfig) {
      log(`Creating OTLP trace exporter with URL: ${tracesConfig.url}`);

      const traceExporter = new OTLPTraceExporter({
        url: tracesConfig.url,
        headers: ingestHeaders(config.ingestConfig, tracesConfig.url, tracesConfig.headers),
        timeoutMillis: 30000, // 30 seconds timeout for Langfuse
      });

      spanProcessors.unshift(
        new BatchSpanProcessor(traceExporter, {
          maxExportBatchSize: 64,      // Export after 64 spans
          scheduledDelayMillis: 2000,  // Or after 2 seconds, whichever comes first
        }),
      );
      log(`Traces initialized: ${tracesConfig.url}`);
    } else {
      log('Traces disabled: no endpoint configured');
    }
  } else {
    log('Traces disabled by config');
  }

  if (spanProcessors.length > 0) {
    tracerProvider = new NodeTracerProvider({ resource, spanProcessors: spanProcessors.map(p => privacyProcessor(p, config.collectionMode, 'traces')) });
    providers.push(tracerProvider);
  }

  let meterProvider: MeterProvider | undefined;
  const metricReaders = metricsEnabled ? [...(extensions.metricReaders ?? [])] : [];
  if (metricsEnabled) {
    const metricsConfig = getEndpointAndHeaders(
      config.metricsConfig,
      '/v1/metrics'
    );

    if (metricsConfig) {
      const metricExporter = new OTLPMetricExporter({
        url: metricsConfig.url,
        headers: ingestHeaders(config.ingestConfig, metricsConfig.url, metricsConfig.headers),
        timeoutMillis: 10000,
        temporalityPreference: ({
          delta: AggregationTemporalityPreference.DELTA,
          cumulative: AggregationTemporalityPreference.CUMULATIVE,
          lowmemory: AggregationTemporalityPreference.LOWMEMORY,
        } as Record<string, AggregationTemporalityPreference>)[config.otelExporter.metricsTemporalityPreference.trim().toLowerCase()],
      });

      metricReaders.unshift(new PeriodicExportingMetricReader({
          exporter: privacyMetricExporter(metricExporter, config.collectionMode),
          exportIntervalMillis: config.metricExportInterval,
        }));
      log(`Metrics initialized: ${metricsConfig.url} (interval=${config.metricExportInterval}ms)`);
    } else {
      log('Metrics disabled: no endpoint configured');
    }
  } else {
    log('Metrics disabled by config');
  }


  if (metricReaders.length > 0) {
    meterProvider = new MeterProvider({ resource, readers: metricReaders });
    providers.push(meterProvider);
  }

  let loggerProvider: LoggerProvider | undefined;
  const logRecordProcessors = eventsEnabled ? [...(extensions.logRecordProcessors ?? [])] : [];
  if (eventsEnabled) {
    const eventsConfig = getEndpointAndHeaders(
      config.eventsConfig,
      '/v1/logs'
    );

    if (eventsConfig) {
      const logExporter = new OTLPLogExporter({
        url: eventsConfig.url,
        headers: ingestHeaders(config.ingestConfig, eventsConfig.url, eventsConfig.headers),
        timeoutMillis: 10000,
      });

      logRecordProcessors.unshift(new SimpleLogRecordProcessor(logExporter));
      log(`Events initialized: ${eventsConfig.url} (immediate export)`);
    } else {
      log('Events disabled: no endpoint configured');
    }
  } else {
    log('Events disabled by config');
  }


  if (logRecordProcessors.length > 0) {
    loggerProvider = new LoggerProvider({ resource, processors: logRecordProcessors.map(p => privacyProcessor(p, config.collectionMode, 'logs')) });
    providers.push(loggerProvider);
  }

  // Register only after every configured provider has been constructed, so a
  // later construction failure cannot leave a partially active signal set.
  tracerProvider?.register();
  if (meterProvider) metrics.setGlobalMeterProvider(meterProvider);
  if (loggerProvider) logs.setGlobalLoggerProvider(loggerProvider);

  const tracer = trace.getTracer('observ-opencode', VERSION);
  const meter = metrics.getMeter('observ-opencode', VERSION);
  const otelLogger = logs.getLogger('observ-opencode', VERSION);

  const hasAnyProvider = providers.length > 0;
  if (!hasAnyProvider) {
    log('No endpoints configured, telemetry disabled');
  }

  log('OTel identity attribute summary:', {
    hasUsername: Boolean(config.otelExporter.attributes['ant.username']),
    hasUid: Boolean(config.otelExporter.attributes['ant.uid']),
    agentType: resourceAttributes['ant.agent.type'],
    agentProduct: resourceAttributes['ant.agent.product'],
  });
  log(`Content logging: events.userPrompts=${config.log.events.userPrompts}, spans.userPrompts=${config.log.spans.userPrompts}`);

  return {
    tracer,
    meter,
    logger: otelLogger,
    tracesEnabled: !!tracerProvider,
    metricsEnabled: !!meterProvider,
    eventsEnabled: !!loggerProvider,
    resolvedUsername,
    resolvedAgentScene,
    forceFlush: async () => {
      const flushTimeout = 15000;
      const start = Date.now();
      const warnings: string[] = [];

      const flushOne = async (name: string, provider: { forceFlush: () => Promise<void> } | undefined) => {
        if (!provider || !('forceFlush' in provider)) return;
        try {
          await provider.forceFlush();
        } catch (err) {
          warnings.push(`${name}: ${err instanceof Error ? err.message : err}`);
        }
      };

      try {
        let timeout: NodeJS.Timeout | undefined;
        try {
          await Promise.race([
            Promise.all([
            flushOne('traces', tracerProvider as { forceFlush: () => Promise<void> } | undefined),
            flushOne('metrics', meterProvider as { forceFlush: () => Promise<void> } | undefined),
            flushOne('events', loggerProvider as { forceFlush: () => Promise<void> } | undefined),
            ]),
            new Promise<void>((_, reject) => {
              timeout = setTimeout(() => reject(new Error('timeout')), flushTimeout);
            }),
          ]);
        } finally {
          if (timeout) clearTimeout(timeout);
        }
        if (warnings.length) {
          const error = new AggregateError(warnings.map(message => new Error(message)), 'Telemetry forceFlush failed');
          logError(`Flush done (${Date.now() - start}ms) with warnings`, error);
          throw error;
        } else {
          log(`Flush done (${Date.now() - start}ms)`);
        }
      } catch (err) {
        logError(`Flush failed (${Date.now() - start}ms)`, err);
        throw err;
      }
    },
    shutdown: async () => {
      log('Shutting down telemetry...');
      await Promise.all(providers.map(p => p.shutdown()));
      log('Telemetry shutdown completed');
    },
  };
}

export async function startObservabilityTelemetry(_runtime: TelemetryRuntime): Promise<void> {
  log('Telemetry started');
}
