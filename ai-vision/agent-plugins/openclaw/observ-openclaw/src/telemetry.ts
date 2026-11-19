import { privacyProcessor, privacyMetricExporter } from '../../../shared/collection-mode.mjs';
import { ingestHeaders } from '../../../shared/ingest-auth.mjs';
import { trace, type Tracer } from '@opentelemetry/api';
import { metrics, type Meter } from '@opentelemetry/api';
import { logs, type Logger } from '@opentelemetry/api-logs';
import { NodeTracerProvider } from '@opentelemetry/sdk-trace-node';
import { BatchSpanProcessor } from '@opentelemetry/sdk-trace-base';
import { OTLPTraceExporter } from '@opentelemetry/exporter-trace-otlp-http';
import { MeterProvider, PeriodicExportingMetricReader } from '@opentelemetry/sdk-metrics';
import { OTLPMetricExporter } from '@opentelemetry/exporter-metrics-otlp-http';
import { LoggerProvider, BatchLogRecordProcessor } from '@opentelemetry/sdk-logs';
import { OTLPLogExporter } from '@opentelemetry/exporter-logs-otlp-http';
import { resourceFromAttributes } from '@opentelemetry/resources';
import { ATTR_SERVICE_NAME, ATTR_SERVICE_VERSION } from '@opentelemetry/semantic-conventions';
import * as os from 'os';
import { telemetryConfigLogSummary, type ObservOpenClawConfig, type SignalEndpointConfig } from './config.js';
import { getTelemetryProcessorContributions } from './telemetry-extensions.js';
import { log, logError } from './logger.js';

declare const __PLUGIN_VERSION__: string;
const PLUGIN_VERSION = typeof __PLUGIN_VERSION__ !== 'undefined' ? __PLUGIN_VERSION__ : '0.0.0-dev';

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
}

/**
 * Resolve OTLP endpoint URL.
 * If `host` already contains a URI path (e.g. "https://proxy/v1/traces"),
 * use it as-is. Otherwise append `defaultPath`.
 * Consistent with cchooks resolve_otel_url() behavior.
 */
function resolveOtelUrl(host: string, defaultPath: string): string {
  let end = host.length;
  while (host[end - 1] === '/') end--;
  const trimmed = host.slice(0, end);
  const schemeEnd = trimmed.indexOf('://');
  if (schemeEnd >= 0) {
    const afterScheme = trimmed.slice(schemeEnd + 3);
    if (afterScheme.includes('/')) {
      // Host already has a path component — use as-is
      return trimmed;
    }
  }
  return `${trimmed}${defaultPath}`;
}

function getEndpointAndHeaders(
  signalConfig: SignalEndpointConfig | undefined,
  fallbackEndpoint: string | undefined,
  fallbackHeaders: Record<string, string>,
  path: string
): { url: string; headers: Record<string, string> } | null {
  const endpoint = signalConfig?.endpoint || fallbackEndpoint;
  if (!endpoint) return null;

  const headers = { ...(signalConfig?.headers || fallbackHeaders) };
  const url = resolveOtelUrl(endpoint, path);

  return { url, headers };
}

export function buildResourceAttributes(
  config: ObservOpenClawConfig,
  overrides: { serviceName?: string; agentInstance?: string } = {},
): Record<string, string> {
  const configuredResourceAttributes = { ...config.otelExporter.resourceAttributes };
  delete configuredResourceAttributes['ant.uid'];
  delete configuredResourceAttributes['ant.username'];

  const resolvedAgentType = configuredResourceAttributes['ant.agent.type'] || 'openclaw';
  const resolvedAgentProduct = configuredResourceAttributes['ant.agent.product'] || 'OpenClaw';
  const resolvedAgentInstance = overrides.agentInstance
    || configuredResourceAttributes['ant.agent.instance']
    || (() => { try { return os.hostname(); } catch { return ''; } })()
    || 'unknown';
  const resolvedAgentEnv = configuredResourceAttributes['ant.agent.env'] || 'Default';
  const resolvedAgentScene = configuredResourceAttributes['ant.agent.scene'] || 'default';

  const runtimeResourceAttributes: Record<string, string> = {
    [ATTR_SERVICE_NAME]: overrides.serviceName || config.serviceName,
    [ATTR_SERVICE_VERSION]: PLUGIN_VERSION,
    'ant.agent.type': resolvedAgentType,
    'ant.agent.product': resolvedAgentProduct,
    'ant.agent.instance': resolvedAgentInstance,
    'ant.agent.env': resolvedAgentEnv,
    'ant.agent.scene': resolvedAgentScene,
    'deployment.environment': resolvedAgentEnv,
    'deployment.environment.name': resolvedAgentEnv,
    'telemetry.sdk.language': 'nodejs',
    'telemetry.sdk.name': 'observ-openclaw',
    'telemetry.sdk.version': PLUGIN_VERSION,
    'agentic.collection.mode': config.collectionMode,
  };

  const mergedAttributes: Record<string, string> = {
    ...runtimeResourceAttributes,
    ...configuredResourceAttributes,
    'ant.agent.type': resolvedAgentType,
    'ant.agent.product': resolvedAgentProduct,
    'ant.agent.instance': resolvedAgentInstance,
    'ant.agent.env': resolvedAgentEnv,
    'ant.agent.scene': resolvedAgentScene,
    'agentic.collection.mode': config.collectionMode,
  };

  // Remove bare resource attributes that use disallowed namespace prefixes.
  // User identity attributes are signal-scoped and never belong on Resource.
  delete mergedAttributes['username'];
  delete mergedAttributes['ant.username'];
  delete mergedAttributes['ant.uid'];

  return mergedAttributes;
}

export function initObservabilityTelemetry(config: ObservOpenClawConfig, _stateDir?: string): TelemetryRuntime {
  log('initObservabilityTelemetry called');
  log('Config summary:', telemetryConfigLogSummary(config));

  const fallbackEndpoint = config.otelExporter.endpoint;
  const fallbackHeaders = config.otelExporter.headers;

  const resolvedUsername = config.otelExporter.attributes['ant.username'] || 'openclaw';
  const resourceAttributes = buildResourceAttributes(config);

  log('Resource attribute keys:', Object.keys(resourceAttributes));
  const resource = resourceFromAttributes(resourceAttributes);

  const tracesEnabled = config.tracesConfig.enabled;
  const metricsEnabled = config.metricsConfig.enabled;
  const eventsEnabled = config.eventsConfig.enabled;

  const providers: { shutdown: () => Promise<void> }[] = [];

  let tracerProvider: NodeTracerProvider | undefined;

  if (tracesEnabled) {
    const spanProcessors = getTelemetryProcessorContributions()
      .flatMap((contribution) => contribution.spanProcessors || []);
    const tracesConfig = getEndpointAndHeaders(
      config.tracesConfig,
      fallbackEndpoint,
      fallbackHeaders,
      '/api/public/otel/v1/traces'
    );

    if (tracesConfig) {
      log('Creating OTLPTraceExporter:', { url: tracesConfig.url, headerKeys: Object.keys(tracesConfig.headers) });
      const traceExporter = new OTLPTraceExporter({
        url: tracesConfig.url,
        headers: ingestHeaders(config.ingestConfig, tracesConfig.url, tracesConfig.headers),
      });

      spanProcessors.unshift(new BatchSpanProcessor(traceExporter));
    }

    if (spanProcessors.length > 0) {
      tracerProvider = new NodeTracerProvider({
        resource,
        spanProcessors: spanProcessors.map(p => privacyProcessor(p, config.collectionMode, 'traces')),
      });

      tracerProvider.register();
      providers.push(tracerProvider);
      log('TracerProvider registered, span processors:', spanProcessors.length);
      if (tracesConfig) log(`Traces initialized: ${tracesConfig.url}`);
    } else {
      log('Traces disabled: no endpoint or contributed processor configured');
    }
  } else {
    log('Traces disabled by config');
  }

  let meterProvider: MeterProvider | undefined;
  if (metricsEnabled) {
    const metricsConfig = getEndpointAndHeaders(
      config.metricsConfig,
      fallbackEndpoint,
      fallbackHeaders,
      '/v1/metrics'
    );

    if (metricsConfig) {
      log('Creating OTLPMetricExporter:', { url: metricsConfig.url, headerKeys: Object.keys(metricsConfig.headers) });
      const metricExporter = new OTLPMetricExporter({
        url: metricsConfig.url,
        headers: ingestHeaders(config.ingestConfig, metricsConfig.url, metricsConfig.headers),
      });

      meterProvider = new MeterProvider({
        resource,
        readers: [new PeriodicExportingMetricReader({
          exporter: privacyMetricExporter(metricExporter, config.collectionMode),
          exportIntervalMillis: config.metricExportInterval,
        })],
      });

      metrics.setGlobalMeterProvider(meterProvider);
      providers.push(meterProvider);
      log(`Metrics initialized: ${metricsConfig.url} (interval=${config.metricExportInterval}ms)`);
    } else {
      log('Metrics disabled: no endpoint configured');
    }
  } else {
    log('Metrics disabled by config');
  }

  let loggerProvider: LoggerProvider | undefined;
  if (eventsEnabled) {
    const logRecordProcessors = getTelemetryProcessorContributions()
      .flatMap((contribution) => contribution.logRecordProcessors || []);
    const eventsConfig = getEndpointAndHeaders(
      config.eventsConfig,
      fallbackEndpoint,
      fallbackHeaders,
      '/api/public/otel/v1/logs'
    );

    if (eventsConfig) {
      log('Creating OTLPLogExporter:', { url: eventsConfig.url, headerKeys: Object.keys(eventsConfig.headers) });
      const logExporter = new OTLPLogExporter({
        url: eventsConfig.url,
        headers: ingestHeaders(config.ingestConfig, eventsConfig.url, eventsConfig.headers),
      });

      logRecordProcessors.unshift(new BatchLogRecordProcessor(logExporter, {
          scheduledDelayMillis: config.logsExportInterval,
        }));
    }
    if (logRecordProcessors.length > 0) {
      loggerProvider = new LoggerProvider({ resource, processors: logRecordProcessors.map(p => privacyProcessor(p, config.collectionMode, 'logs')) });
      logs.setGlobalLoggerProvider(loggerProvider);
      providers.push(loggerProvider);
      if (eventsConfig) log(`Events initialized: ${eventsConfig.url} (interval=${config.logsExportInterval}ms)`);
    } else {
      log('Events disabled: no endpoint or contributed processor configured');
    }
  } else {
    log('Events disabled by config');
  }

  const tracer = trace.getTracer('observ-openclaw', PLUGIN_VERSION);
  const meter = metrics.getMeter('observ-openclaw', PLUGIN_VERSION);
  const otelLogger = logs.getLogger('observ-openclaw', PLUGIN_VERSION);

  const hasAnyProvider = providers.length > 0;
  log('Providers registered:', providers.length, 'tracesEnabled:', tracesEnabled && !!tracerProvider, 'metricsEnabled:', metricsEnabled && !!meterProvider, 'eventsEnabled:', eventsEnabled && !!loggerProvider);
  if (!hasAnyProvider) {
    log('WARNING: No providers registered — all telemetry disabled');
  }

  log('OTel identity attribute summary:', {
    hasUsername: Boolean(config.otelExporter.attributes['ant.username']),
    hasUid: Boolean(config.otelExporter.attributes['ant.uid']),
    agentType: config.otelExporter.resourceAttributes['ant.agent.type'],
    agentProduct: config.otelExporter.resourceAttributes['ant.agent.product'],
  });
  log(`Collection mode: ${config.collectionMode}`);

  return {
    tracer,
    meter,
    logger: otelLogger,
    tracesEnabled: tracesEnabled && !!tracerProvider,
    metricsEnabled: metricsEnabled && !!meterProvider,
    eventsEnabled: eventsEnabled && !!loggerProvider,
    resolvedUsername,
    forceFlush: async () => {
      await Promise.all(providers.map(p => 'forceFlush' in p ? (p as { forceFlush: () => Promise<void> }).forceFlush() : Promise.resolve()));
    },
    shutdown: async () => {
      log('Shutting down telemetry...');
      const shutdownTimeout = 3000;
      await Promise.race([
        Promise.all(providers.map(p => p.shutdown())),
        new Promise<void>(resolve => {
          const timer = setTimeout(() => {
            console.warn('[observ-openclaw] Telemetry shutdown timed out, forcing exit');
            resolve();
          }, shutdownTimeout);
          timer.unref();
        }),
      ]);
    },
  };
}

export async function startObservabilityTelemetry(runtime: TelemetryRuntime): Promise<void> {
  log('Telemetry started');
}
