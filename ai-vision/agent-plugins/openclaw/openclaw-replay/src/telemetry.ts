import { metrics, trace } from '@opentelemetry/api';
import { logs } from '@opentelemetry/api-logs';
import { OTLPTraceExporter } from '@opentelemetry/exporter-trace-otlp-http';
import { resourceFromAttributes } from '@opentelemetry/resources';
import { BatchLogRecordProcessor, LoggerProvider } from '@opentelemetry/sdk-logs';
import { BatchSpanProcessor, SimpleSpanProcessor } from '@opentelemetry/sdk-trace-base';
import { NodeTracerProvider } from '@opentelemetry/sdk-trace-node';
import {
  buildResourceAttributes,
  type ObservOpenClawConfig,
  type TelemetryRuntime,
} from '@aivision/observ-openclaw';
import { FileLogExporter, FileSpanExporter } from './file-exporter.js';
import { REPLAY_OTLP_HTTP_BASE, replayOtlpUrl } from './local-collector.js';

export function replaySemanticConfig(collectionMode: 'full' | 'privacy'): ObservOpenClawConfig {
  const collectContent = collectionMode === 'full';
  const content = {
    userPrompts: collectContent,
    systemPrompts: collectContent,
    modelOutputs: collectContent,
    toolDetails: collectContent,
    toolOutputs: collectContent,
  };
  return {
    enableTelemetry: true,
    serviceName: 'openclaw-replay',
    collectionMode,
    debug: false,
    dynamicUsername: false,
    dynamicUid: false,
    log: { events: { ...content }, spans: { ...content } },
    otelExporter: {
      endpoint: undefined,
      protocol: 'http/json',
      headers: {},
      attributes: { 'ant.username': 'replay' },
      resourceAttributes: {
        'ant.agent.type': 'openclaw',
        'ant.agent.product': 'OpenClaw',
        'ant.agent.instance': 'replay',
        'ant.agent.env': 'Default',
        'ant.agent.scene': 'default',
        'agentic.collection.mode': collectionMode,
      },
      metricsTemporalityPreference: 'delta',
    },
    tracesConfig: { enabled: false, endpoint: undefined, headers: {} },
    metricsConfig: { enabled: false, endpoint: undefined, headers: {} },
    eventsConfig: { enabled: false, endpoint: undefined, headers: {} },
    metricExportInterval: 60_000,
    logsExportInterval: 5_000,
    logDir: '',
  };
}

export function initReplayTelemetry(
  outputFile: string,
  eventsFile: string | undefined,
  collectionMode: 'full' | 'privacy' = 'full',
): TelemetryRuntime {
  const config = replaySemanticConfig(collectionMode);
  const resource = resourceFromAttributes(buildResourceAttributes(config, {
    serviceName: config.serviceName || 'openclaw-replay',
    agentInstance: config.otelExporter.resourceAttributes['ant.agent.instance'] || 'replay',
  }));
  const tracerProvider = new NodeTracerProvider({
    resource,
    spanProcessors: [new SimpleSpanProcessor(new FileSpanExporter(outputFile))],
  });
  tracerProvider.register();

  const providers: Array<{ shutdown: () => Promise<void>; forceFlush?: () => Promise<void> }> = [tracerProvider];
  let loggerProvider: LoggerProvider | undefined;
  if (eventsFile) {
    loggerProvider = new LoggerProvider({
      resource,
      processors: [new BatchLogRecordProcessor(new FileLogExporter(eventsFile))],
    });
    logs.setGlobalLoggerProvider(loggerProvider);
    providers.push(loggerProvider);
  }

  return {
    tracer: trace.getTracer('openclaw-replay'),
    meter: metrics.getMeter('openclaw-replay'),
    logger: logs.getLogger('openclaw-replay'),
    tracesEnabled: true,
    metricsEnabled: false,
    eventsEnabled: Boolean(loggerProvider),
    resolvedUsername: config.otelExporter.attributes['ant.username'] || 'replay',
    forceFlush: async () => { await Promise.all(providers.map((provider) => provider.forceFlush?.())); },
    shutdown: async () => { await Promise.all(providers.map((provider) => provider.shutdown())); },
  };
}

export function initReportTelemetry(collectionMode: 'full' | 'privacy' = 'full'): TelemetryRuntime {
  const config = replaySemanticConfig(collectionMode);
  const resource = resourceFromAttributes(buildResourceAttributes(config, {
    serviceName: config.serviceName || 'openclaw-report',
    agentInstance: config.otelExporter.resourceAttributes['ant.agent.instance'] || 'report',
  }));
  const exporter = new OTLPTraceExporter({
    url: replayOtlpUrl('traces'),
    headers: {},
    timeoutMillis: 30_000,
  });
  const tracerProvider = new NodeTracerProvider({
    resource,
    spanProcessors: [new BatchSpanProcessor(exporter, { maxExportBatchSize: 64, scheduledDelayMillis: 1000 })],
  });
  tracerProvider.register();

  return {
    tracer: trace.getTracer('openclaw-report'),
    meter: metrics.getMeter('openclaw-report'),
    logger: logs.getLogger('openclaw-report'),
    tracesEnabled: true,
    metricsEnabled: false,
    eventsEnabled: false,
    resolvedUsername: config.otelExporter.attributes['ant.username'] || 'openclaw',
    forceFlush: () => tracerProvider.forceFlush(),
    shutdown: () => tracerProvider.shutdown(),
  };
}

export { REPLAY_OTLP_HTTP_BASE };
