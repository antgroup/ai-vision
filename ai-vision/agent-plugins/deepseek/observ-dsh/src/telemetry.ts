import { privacyProcessor, privacyMetricExporter } from '../../../shared/collection-mode.mjs';
import { ingestHeaders } from '../../../shared/ingest-auth.mjs';
/**
 * OTel SDK construction for observ-dsh.
 *
 * Traces, metrics, and events each have independent endpoint switches. When
 * `enableTelemetry` is false no provider/exporter/processor is constructed. The
 * semantic engine works with a no-op runtime in that case so handlers stay
 * uniform; replay uses an in-memory runtime constructed here as well.
 */

import { trace, metrics, type Tracer, type Meter } from '@opentelemetry/api';
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
import os from 'node:os';
import path from 'node:path';
import type { ObservDshConfig } from './config.js';
import { telemetryConfigLogSummary } from './config.js';
import { log, logError } from './logger.js';
import { resolveVcsAttributes } from './vcs.js';
import { BoundedTraceExporter } from './trace-exporter.js';

export interface TelemetryRuntime {
  tracer: Tracer;
  meter: Meter;
  logger: Logger;
  shutdown: () => Promise<void>;
  forceFlush: () => Promise<void>;
  tracesEnabled: boolean;
  metricsEnabled: boolean;
  eventsEnabled: boolean;
  resolvedUsername: string;
  resolvedAgentScene: string;
  config: ObservDshConfig;
}

export interface TelemetryExtensions {
  spanProcessors?: SpanProcessor[];
  metricReaders?: MetricReader[];
  logRecordProcessors?: LogRecordProcessor[];
}

const INSTRUMENTATION_NAME = 'observ-dsh';
declare const __OBSERV_DSH_VERSION__: string;
const VERSION = __OBSERV_DSH_VERSION__;

export function buildResourceAttributes(config: ObservDshConfig, probeCwd?: string): Record<string, string> {
  const configured = { ...config.otelExporter.resourceAttributes };
  // Identity is signal-scoped, never resource-scoped.
  delete configured['ant.username'];
  delete configured['ant.uid'];
  delete configured.username;
  // Empty strings (e.g. "ant.agent.instance": "" in the JSON config) mean
  // "unset", not "explicitly empty": without stripping them the spread below
  // would overwrite the computed fallbacks with "".
  for (const key of Object.keys(configured)) {
    if (configured[key] !== undefined && configured[key] !== null && String(configured[key]).trim() === '') {
      delete configured[key];
    }
  }

  const attributes: Record<string, string> = {
    [ATTR_SERVICE_NAME]: config.serviceName,
    'ant.agent.type': configured['ant.agent.type'] || 'dsh',
    'ant.agent.product': configured['ant.agent.product'] || 'DeepSeekHarness',
    'ant.agent.instance': configured['ant.agent.instance'] || (() => { try { return os.hostname(); } catch { return 'unknown'; } })(),
    'ant.agent.env': configured['ant.agent.env'] || 'Default',
    'ant.agent.scene': configured['ant.agent.scene'] || 'default',
    'deployment.environment': configured['deployment.environment'] || configured['ant.agent.env'] || 'Default',
    'agentic.collection.mode': config.collectionMode,
    'service.version': VERSION,
    ...configured,
  };
  // Re-assert canonical values so config cannot accidentally override them.
  attributes['ant.agent.type'] = configured['ant.agent.type'] || 'dsh';
  attributes['ant.agent.product'] = configured['ant.agent.product'] || 'DeepSeekHarness';

  // Git repository info, cchooks-style: configured values always win; only
  // missing keys get the best-effort probe (missing Git never breaks startup).
  if (!configured['vcs.repository.url.full'] || !configured['vcs.ref.head.name'] || !configured['vcs.ref.head.revision']) {
    const detected = resolveVcsAttributes(path.resolve(probeCwd ?? process.cwd()));
    if (!configured['vcs.repository.url.full'] && detected['vcs.repository.url.full']) {
      attributes['vcs.repository.url.full'] = detected['vcs.repository.url.full'];
    }
    if (!configured['vcs.ref.head.name'] && detected['vcs.ref.head.name']) {
      attributes['vcs.ref.head.name'] = detected['vcs.ref.head.name'];
    }
    if (!configured['vcs.ref.head.revision'] && detected['vcs.ref.head.revision']) {
      attributes['vcs.ref.head.revision'] = detected['vcs.ref.head.revision'];
    }
  }
  return attributes;
}

function appendSignalPath(endpoint: string, path: string): string {
  return endpoint.includes('/v1/') ? endpoint : `${endpoint.replace(/\/$/, '')}${path}`;
}

export function initTelemetry(
  config: ObservDshConfig,
  extensions: TelemetryExtensions = {},
  probeCwd?: string,
): TelemetryRuntime {
  log('initTelemetry', telemetryConfigLogSummary(config));

  if (config.enableTelemetry) {
    trace.disable();
    metrics.disable();
    logs.disable();
  }

  if (!config.enableTelemetry) {
    log('Telemetry master switch disabled; constructing no providers');
    return {
      tracer: trace.getTracer(INSTRUMENTATION_NAME),
      meter: metrics.getMeter(INSTRUMENTATION_NAME),
      logger: logs.getLogger(INSTRUMENTATION_NAME),
      shutdown: async () => {},
      forceFlush: async () => {},
      tracesEnabled: false,
      metricsEnabled: false,
      eventsEnabled: false,
      resolvedUsername: config.otelExporter.attributes['ant.username'] || 'dsh',
      resolvedAgentScene: config.otelExporter.resourceAttributes['ant.agent.scene'] || 'default',
      config,
    };
  }

  const resourceAttributes = buildResourceAttributes(config, probeCwd);
  const resource = defaultResource().merge(resourceFromAttributes(resourceAttributes));
  const providers: { shutdown: () => Promise<void>; forceFlush?: () => Promise<void> }[] = [];

  let tracerProvider: NodeTracerProvider | undefined;
  const spanProcessors = config.tracesConfig.enabled ? [...(extensions.spanProcessors ?? [])] : [];
  if (config.tracesConfig.enabled && config.tracesConfig.endpoint) {
    spanProcessors.unshift(new BatchSpanProcessor(new BoundedTraceExporter(new OTLPTraceExporter({
      url: appendSignalPath(config.tracesConfig.endpoint, '/v1/traces'),
      headers: ingestHeaders(config.ingestConfig, appendSignalPath(config.tracesConfig.endpoint, '/v1/traces'), config.tracesConfig.headers),
      timeoutMillis: 30000,
    })), {
      maxExportBatchSize: 64,
      scheduledDelayMillis: 2000,
    }));
  }
  if (spanProcessors.length > 0) {
    tracerProvider = new NodeTracerProvider({ resource, spanProcessors: spanProcessors.map(p => privacyProcessor(p, config.collectionMode, 'traces')) });
    providers.push(tracerProvider);
  }

  let meterProvider: MeterProvider | undefined;
  const metricReaders = config.metricsConfig.enabled ? [...(extensions.metricReaders ?? [])] : [];
  if (config.metricsConfig.enabled && config.metricsConfig.endpoint) {
    metricReaders.unshift(new PeriodicExportingMetricReader({
      exporter: privacyMetricExporter(new OTLPMetricExporter({
        url: appendSignalPath(config.metricsConfig.endpoint, '/v1/metrics'),
        headers: ingestHeaders(config.ingestConfig, appendSignalPath(config.metricsConfig.endpoint, '/v1/metrics'), config.metricsConfig.headers),
        timeoutMillis: 10000,
        temporalityPreference: ({
          delta: AggregationTemporalityPreference.DELTA,
          cumulative: AggregationTemporalityPreference.CUMULATIVE,
          lowmemory: AggregationTemporalityPreference.LOWMEMORY,
        } as Record<string, AggregationTemporalityPreference>)[config.otelExporter.metricsTemporalityPreference.trim().toLowerCase()],
      }), config.collectionMode),
      exportIntervalMillis: config.metricExportInterval,
    }));
  }
  if (metricReaders.length > 0) {
    meterProvider = new MeterProvider({ resource, readers: metricReaders });
    providers.push(meterProvider);
  }

  let loggerProvider: LoggerProvider | undefined;
  const logRecordProcessors = config.eventsConfig.enabled ? [...(extensions.logRecordProcessors ?? [])] : [];
  if (config.eventsConfig.enabled && config.eventsConfig.endpoint) {
    logRecordProcessors.unshift(new SimpleLogRecordProcessor({ exporter: new OTLPLogExporter({
      url: appendSignalPath(config.eventsConfig.endpoint, '/v1/logs'),
      headers: ingestHeaders(config.ingestConfig, appendSignalPath(config.eventsConfig.endpoint, '/v1/logs'), config.eventsConfig.headers),
      timeoutMillis: 10000,
    }) }));
  }
  if (logRecordProcessors.length > 0) {
    loggerProvider = new LoggerProvider({ resource, processors: logRecordProcessors.map(p => privacyProcessor(p, config.collectionMode, 'logs')) });
    providers.push(loggerProvider);
  }

  tracerProvider?.register();
  if (meterProvider) metrics.setGlobalMeterProvider(meterProvider);
  if (loggerProvider) logs.setGlobalLoggerProvider(loggerProvider);

  const tracer = trace.getTracer(INSTRUMENTATION_NAME);
  const meter = metrics.getMeter(INSTRUMENTATION_NAME);
  const otelLogger = logs.getLogger(INSTRUMENTATION_NAME);

  return {
    tracer,
    meter,
    logger: otelLogger,
    tracesEnabled: !!tracerProvider,
    metricsEnabled: !!meterProvider,
    eventsEnabled: !!loggerProvider,
    resolvedUsername: config.otelExporter.attributes['ant.username'] || 'dsh',
    resolvedAgentScene: resourceAttributes['ant.agent.scene'] || 'default',
    config,
    forceFlush: async () => {
      const flushTimeout = 15000;
      const start = Date.now();
      const warnings: string[] = [];
      const flushOne = async (name: string, provider: { forceFlush?: () => Promise<void> } | undefined) => {
        if (!provider?.forceFlush) return;
        try {
          await provider.forceFlush();
        } catch (error) {
          warnings.push(`${name}: ${error instanceof Error ? error.message : String(error)}`);
        }
      };
      try {
        await Promise.race([
          Promise.all([
            flushOne('traces', tracerProvider),
            flushOne('metrics', meterProvider),
            flushOne('events', loggerProvider),
          ]),
          new Promise<void>((_, reject) => {
            const timer = setTimeout(() => reject(new Error(`flush timed out after ${flushTimeout}ms`)), flushTimeout);
            timer.unref();
          }),
        ]);
      } catch (error) {
        warnings.push(error instanceof Error ? error.message : String(error));
      }
      if (warnings.length) {
        const error = new AggregateError(warnings.map(message => new Error(message)), 'Telemetry forceFlush failed');
        logError(`Flush done in ${Date.now() - start}ms with warnings`, error);
        throw error;
      }
      log(`Flush done in ${Date.now() - start}ms`);
    },
    shutdown: async () => {
      await Promise.all(providers.map(provider => provider.shutdown()));
    },
  };
}
