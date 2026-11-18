import { privacyProcessor, privacyMetricExporter } from '../../../shared/collection-mode.mjs';
import { ingestHeaders } from '../../../shared/ingest-auth.mjs';
import { context, trace, SpanStatusCode, type Span, type SpanContext, type Tracer, type Meter, type Attributes } from '@opentelemetry/api';
import type { Logger } from '@opentelemetry/api-logs';
import { NodeTracerProvider } from '@opentelemetry/sdk-trace-node';
import { BatchSpanProcessor, SimpleSpanProcessor, type SpanProcessor, type ReadableSpan } from '@opentelemetry/sdk-trace-base';
import { MeterProvider, PeriodicExportingMetricReader, type ResourceMetrics } from '@opentelemetry/sdk-metrics';
import { LoggerProvider, BatchLogRecordProcessor, SimpleLogRecordProcessor, type LogRecordProcessor, type ReadableLogRecord } from '@opentelemetry/sdk-logs';
import { OTLPTraceExporter } from '@opentelemetry/exporter-trace-otlp-http';
import { AggregationTemporalityPreference, OTLPMetricExporter } from '@opentelemetry/exporter-metrics-otlp-http';
import { OTLPLogExporter } from '@opentelemetry/exporter-logs-otlp-http';
import { ExportResultCode, type ExportResult } from '@opentelemetry/core';
import { resourceFromAttributes } from '@opentelemetry/resources';
import { join } from 'node:path';
import { randomBytes } from 'node:crypto';
import type { PiagentConfig } from './config.js';
import type { SemanticObservation } from './types.js';
import { FileLogExporter, FileMetricExporter, FileSpanExporter } from './file-exporter.js';
import { safeArtifactPart } from './normalize.js';
import {
  ANT_META_TRIGGER_KEY,
  resolveMetaTrigger,
} from '../../../shared/meta-trigger.mjs';

function url(base: string, path: string): string {
  return base.includes('/v1/') ? base : `${base.replace(/\/+$/, '')}${path}`;
}
function attrs(input: Record<string, unknown>): Attributes {
  const output: Attributes = {};
  for (const [key, value] of Object.entries(input)) {
    if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') output[key] = value;
    else if (Array.isArray(value) && value.every(item => typeof item === 'string')) output[key] = value;
  }
  return output;
}

export interface TelemetryRuntime {
  consume(observation: SemanticObservation): void;
  forceFlush(): Promise<void>;
  shutdown(): Promise<void>;
}

export const MAX_RETAINED_LINK_CONTEXTS = 256;

export type TelemetryDiagnostic = (level: 'info' | 'warn', message: string) => void;

interface ExporterWithCallback<T> {
  export(data: T, resultCallback: (result: ExportResult) => void): void;
}

export function withExportDiagnostics<T, TExporter extends ExporterWithCallback<T>>(
  signal: 'traces' | 'metrics' | 'logs',
  exporter: TExporter,
  recordCount: (data: T) => number,
  diagnostic?: TelemetryDiagnostic,
): TExporter {
  if (!diagnostic) return exporter;
  const exportBatch = exporter.export.bind(exporter);
  exporter.export = (data, resultCallback) => {
    const records = recordCount(data);
    exportBatch(data, result => {
      const succeeded = result.code === ExportResultCode.SUCCESS;
      diagnostic(
        succeeded ? 'info' : 'warn',
        `telemetry export ${succeeded ? 'succeeded' : 'failed'} signal=${signal} records=${records}`,
      );
      resultCallback(result);
    });
  };
  return exporter;
}

function metricDataPointCount(metrics: ResourceMetrics): number {
  return metrics.scopeMetrics.reduce(
    (scopeTotal, scope) => scopeTotal + scope.metrics.reduce(
      (metricTotal, metric) => metricTotal + metric.dataPoints.length,
      0,
    ),
    0,
  );
}

export function createTelemetry(
  config: PiagentConfig,
  files?: { spans: string; events: string; metrics: string },
  mode: 'realtime' | 'file-only' = 'realtime',
  diagnostic?: TelemetryDiagnostic,
): TelemetryRuntime {
  const resource = resourceFromAttributes(buildResourceAttributes(config));
  const spanProcessors: SpanProcessor[] = [];
  const logProcessors: LogRecordProcessor[] = [];
  const metricReaders: PeriodicExportingMetricReader[] = [];
  const tracesEnabled = mode === 'file-only' || config.tracesConfig.enabled;
  const eventsEnabled = mode === 'file-only' || config.eventsConfig.enabled;
  const metricsEnabled = mode === 'file-only' || config.metricsConfig.enabled;
  if (mode === 'realtime' && tracesEnabled && config.tracesConfig.endpoint) {
    spanProcessors.push(new BatchSpanProcessor(withExportDiagnostics<ReadableSpan[], OTLPTraceExporter>(
      'traces',
      new OTLPTraceExporter({
        url: url(config.tracesConfig.endpoint, '/v1/traces'),
        headers: ingestHeaders(config.ingestConfig, url(config.tracesConfig.endpoint, '/v1/traces'), config.tracesConfig.headers),
        timeoutMillis: 1000,
      }),
      spans => spans.length,
      diagnostic,
    )));
  }
  if (mode === 'realtime' && eventsEnabled && config.eventsConfig.endpoint) {
    logProcessors.push(new BatchLogRecordProcessor({
      exporter: withExportDiagnostics<ReadableLogRecord[], OTLPLogExporter>(
        'logs',
        new OTLPLogExporter({
          url: url(config.eventsConfig.endpoint, '/v1/logs'),
          headers: ingestHeaders(config.ingestConfig, url(config.eventsConfig.endpoint, '/v1/logs'), config.eventsConfig.headers),
          timeoutMillis: 1000,
        }),
        logs => logs.length,
        diagnostic,
      ),
      scheduledDelayMillis: config.logsExportInterval,
    }));
  }
  if (mode === 'realtime' && metricsEnabled && config.metricsConfig.endpoint) {
    metricReaders.push(new PeriodicExportingMetricReader({
      exporter: withExportDiagnostics<ResourceMetrics, OTLPMetricExporter>(
        'metrics',
        privacyMetricExporter(new OTLPMetricExporter({
          url: url(config.metricsConfig.endpoint, '/v1/metrics'),
          headers: ingestHeaders(config.ingestConfig, url(config.metricsConfig.endpoint, '/v1/metrics'), config.metricsConfig.headers),
          timeoutMillis: 1000,
          temporalityPreference: config.otelExporter.metricsTemporalityPreference === 'cumulative'
            ? AggregationTemporalityPreference.CUMULATIVE
            : AggregationTemporalityPreference.DELTA,
        }), config.collectionMode),
        metricDataPointCount,
        diagnostic,
      ),
      exportIntervalMillis: config.metricExportInterval,
    }));
  }
  if (files) {
    if (tracesEnabled) spanProcessors.push(new SimpleSpanProcessor(new FileSpanExporter(files.spans)));
    if (eventsEnabled) logProcessors.push(new SimpleLogRecordProcessor({ exporter: new FileLogExporter(files.events) }));
    if (metricsEnabled) {
      metricReaders.push(new PeriodicExportingMetricReader({
        exporter: privacyMetricExporter(new FileMetricExporter(files.metrics), config.collectionMode),
        exportIntervalMillis: config.metricExportInterval,
      }));
    }
  }
  if (mode === 'file-only' && (!spanProcessors.length || !logProcessors.length || !metricReaders.length)) {
    throw new Error('file-only telemetry requires trace, event, and metric file exporters');
  }
  const tracerProvider = spanProcessors.length
    ? new NodeTracerProvider({ resource, spanProcessors: spanProcessors.map(p => privacyProcessor(p, config.collectionMode, 'traces')) })
    : undefined;
  const meterProvider = metricReaders.length
    ? new MeterProvider({ resource, readers: metricReaders })
    : undefined;
  const loggerProvider = logProcessors.length
    ? new LoggerProvider({ resource, processors: logProcessors.map(p => privacyProcessor(p, config.collectionMode, 'logs')) })
    : undefined;
  const tracer: Tracer | undefined = tracerProvider?.getTracer('piagent', '0.2.1');
  const meter: Meter | undefined = meterProvider?.getMeter('piagent', '0.2.1');
  const logger: Logger | undefined = loggerProvider?.getLogger('piagent', '0.2.1');
  const spans = new Map<string, Span>();
  const completedLinkContexts = new Map<string, { context: SpanContext; turnKey?: string }>();
  const spanTurnKeys = new Map<string, string | undefined>();
  const counters = new Map<string, ReturnType<Meter['createCounter']>>();
  const histograms = new Map<string, ReturnType<Meter['createHistogram']>>();
  let closed = false;

  function consume(observation: SemanticObservation): void {
    if (closed) return;
    if (observation.type === 'span.start') {
      if (!tracer) return;
      const parent = observation.parentKey ? spans.get(observation.parentKey) : undefined;
      const parentContext = parent ? trace.setSpan(context.active(), parent) : context.active();
      const links = observation.linkKeys?.flatMap(key => {
        const linked = completedLinkContexts.get(key);
        completedLinkContexts.delete(key);
        return linked ? [{ context: linked.context }] : [];
      });
      const turnKey = observation.kind === 'CHAT'
        ? observation.key
        : observation.parentKey
          ? spanTurnKeys.get(observation.parentKey)
          : undefined;
      spanTurnKeys.set(observation.key, turnKey);
      spans.set(observation.key, tracer.startSpan(observation.name, {
        attributes: attrs(observation.attributes),
        startTime: new Date(observation.timestamp),
        ...(links?.length ? { links } : {}),
      }, parentContext));
      return;
    }
    if (observation.type === 'span.end') {
      const span = spans.get(observation.key);
      if (!span) return;
      span.setAttributes(attrs(observation.attributes));
      const turnKey = spanTurnKeys.get(observation.key);
      if (observation.retainForLink) {
        completedLinkContexts.delete(observation.key);
        // Preserve every pending causal link in the active Turn. Stale or
        // unscoped contexts remain bounded and Turn completion clears the
        // active set synchronously after derived spans consume it.
        while (completedLinkContexts.size >= MAX_RETAINED_LINK_CONTEXTS) {
          const evictable = [...completedLinkContexts].find(([, retained]) =>
            turnKey === undefined || retained.turnKey !== turnKey,
          );
          if (!evictable) break;
          completedLinkContexts.delete(evictable[0]);
        }
        completedLinkContexts.set(observation.key, { context: span.spanContext(), turnKey });
      }
      if (observation.error) {
        span.setAttributes({ 'error.type': observation.error.type, 'error.message': observation.error.message });
        span.setStatus({ code: SpanStatusCode.ERROR, message: observation.error.message });
      } else span.setStatus({ code: SpanStatusCode.OK });
      span.end(new Date(observation.timestamp));
      spans.delete(observation.key);
      spanTurnKeys.delete(observation.key);
      if (turnKey === observation.key) {
        for (const [key, retained] of completedLinkContexts) {
          if (retained.turnKey === turnKey) completedLinkContexts.delete(key);
        }
      }
      return;
    }
    if (observation.type === 'event') {
      if (!logger) return;
      const span = observation.spanKey ? spans.get(observation.spanKey) : undefined;
      const correlated = span ?? trace.wrapSpanContext({ traceId: randomBytes(16).toString('hex'), spanId: randomBytes(8).toString('hex'), traceFlags: 1 });
      const activeContext = trace.setSpan(context.active(), correlated);
      logger.emit({ body: observation.name, timestamp: new Date(observation.timestamp), attributes: attrs(observation.attributes), context: activeContext });
      return;
    }
    if (!meter) return;
    if (observation.metricKind === 'counter') {
      let instrument = counters.get(observation.name);
      if (!instrument) {
        const tokens = ['agent.token.usage', 'agent.cache.read_tokens', 'agent.cache.write_tokens'].includes(observation.name);
        instrument = meter.createCounter(observation.name, { unit: tokens ? '{token}' : '{count}' });
        counters.set(observation.name, instrument);
      }
      instrument.add(observation.value, observation.attributes);
    } else {
      let instrument = histograms.get(observation.name);
      if (!instrument) { instrument = meter.createHistogram(observation.name, { unit: 'ms' }); histograms.set(observation.name, instrument); }
      instrument.record(observation.value, observation.attributes);
    }
  }

  return {
    consume,
    async forceFlush() {
      await Promise.all([
        tracerProvider?.forceFlush(),
        meterProvider?.forceFlush(),
        loggerProvider?.forceFlush(),
      ]);
    },
    async shutdown() {
      if (closed) return;
      closed = true;
      for (const span of spans.values()) span.end();
      spans.clear();
      completedLinkContexts.clear();
      spanTurnKeys.clear();
      const providers = [
        ['traces', tracerProvider],
        ['metrics', meterProvider],
        ['logs', loggerProvider],
      ] as const;
      let flushFailures = 0;
      let shutdownFailures = 0;
      const flushResults = await Promise.allSettled(
        providers.map(([, provider]) => provider?.forceFlush()),
      );
      for (const [index, result] of flushResults.entries()) {
        if (result.status === 'rejected') {
          flushFailures++;
          diagnostic?.('warn', `telemetry flush failed signal=${providers[index][0]}`);
        }
      }
      const shutdownResults = await Promise.allSettled(
        providers.map(([, provider]) => provider?.shutdown()),
      );
      for (const [index, result] of shutdownResults.entries()) {
        if (result.status === 'rejected') {
          shutdownFailures++;
          diagnostic?.('warn', `telemetry shutdown failed signal=${providers[index][0]}`);
        }
      }
      diagnostic?.(
        flushFailures || shutdownFailures ? 'warn' : 'info',
        `telemetry shutdown complete flush_failures=${flushFailures} shutdown_failures=${shutdownFailures}`,
      );
    }
  };
}

export function buildResourceAttributes(config: PiagentConfig): Attributes {
  const resourceAttributes = attrs(config.resource);
  delete resourceAttributes.username;
  delete resourceAttributes['ant.username'];
  delete resourceAttributes['ant.uid'];

  resourceAttributes[ANT_META_TRIGGER_KEY] = resolveMetaTrigger(
    (config.resource as Record<string, unknown>)[ANT_META_TRIGGER_KEY],
    undefined,
  );
  return resourceAttributes;
}

export function debugFiles(config: PiagentConfig): { spans: string; events: string; metrics: string } | undefined {
  if (!config.debug || !config.replayDir) return undefined;
  const suffix = safeArtifactPart(config.sessionName ?? `${Date.now()}`);
  return { spans: join(config.replayDir, `spans_${suffix}.jsonl`), events: join(config.replayDir, `events_${suffix}.jsonl`), metrics: join(config.replayDir, `metrics_${suffix}.jsonl`) };
}
