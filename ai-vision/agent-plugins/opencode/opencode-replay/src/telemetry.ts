import { SimpleSpanProcessor } from '@opentelemetry/sdk-trace-base';
import { PeriodicExportingMetricReader } from '@opentelemetry/sdk-metrics';
import { SimpleLogRecordProcessor } from '@opentelemetry/sdk-logs';
import { type CollectionMode, type ObservOpencodeConfig } from 'observ-opencode/core';
import {
  initObservabilityTelemetry,
  type HttpRuntimeExtensions,
  type TelemetryRuntime,
} from 'observ-opencode/http-runtime';
import { FileLogExporter, FileMetricExporter, FileSpanExporter } from './file-exporter.js';
import { REPLAY_OTLP_HTTP_BASE } from './local-collector.js';

export interface FileOutputSet {
  spans: string;
  events?: string;
  metrics?: string;
}

export function replayOwnedConfig(
  profile: 'replay' | 'report',
  collectionMode: CollectionMode,
  outputs: { traces: boolean; metrics: boolean; events: boolean },
): ObservOpencodeConfig {
  const collectContent = collectionMode === 'full';
  const content = {
    userPrompts: collectContent,
    systemPrompts: collectContent,
    modelOutputs: collectContent,
    toolDetails: collectContent,
    toolOutputs: collectContent,
  };
  const endpoint = profile === 'report' ? REPLAY_OTLP_HTTP_BASE : undefined;
  return {
    enableTelemetry: true,
    serviceName: `opencode-${profile}`,
    collectionMode,
    debug: false,
    dynamicUsername: false,
    dynamicUid: false,
    log: { events: { ...content }, spans: { ...content } },
    otelExporter: {
      endpoint,
      protocol: 'http/json',
      headers: {},
      attributes: { 'ant.username': profile },
      resourceAttributes: {
        'ant.agent.type': 'opencode',
        'ant.agent.product': 'OpenCode',
        'ant.agent.instance': profile,
        'ant.agent.env': 'Default',
        'ant.agent.scene': 'default',
        'agentic.collection.mode': collectionMode,
      },
      metricsTemporalityPreference: 'delta',
    },
    tracesConfig: { enabled: outputs.traces, endpoint, headers: {} },
    metricsConfig: { enabled: outputs.metrics, endpoint: undefined, headers: {} },
    eventsConfig: { enabled: outputs.events, endpoint: undefined, headers: {} },
    metricExportInterval: 60_000,
    logsExportInterval: 5_000,
    logDir: '',
  };
}

export function fileRuntimeExtensions(outputs: FileOutputSet): HttpRuntimeExtensions {
  return {
    spanProcessors: [new SimpleSpanProcessor(new FileSpanExporter(outputs.spans))],
    ...(outputs.events
      ? { logRecordProcessors: [new SimpleLogRecordProcessor(new FileLogExporter(outputs.events))] }
      : {}),
    ...(outputs.metrics
      ? {
          metricReaders: [new PeriodicExportingMetricReader({
            exporter: new FileMetricExporter(outputs.metrics),
            exportIntervalMillis: 60_000,
          })],
        }
      : {}),
  };
}

export function initReplayTelemetry(
  outputs: FileOutputSet,
  collectionMode: CollectionMode = 'full',
): TelemetryRuntime {
  const config = replayOwnedConfig('replay', collectionMode, {
    traces: Boolean(outputs.spans), metrics: Boolean(outputs.metrics), events: Boolean(outputs.events),
  });
  return initObservabilityTelemetry(config, fileRuntimeExtensions(outputs));
}

export function replayReportConfig(collectionMode: CollectionMode = 'full'): ObservOpencodeConfig {
  return replayOwnedConfig('report', collectionMode, { traces: true, metrics: false, events: false });
}

export function initReportTelemetry(collectionMode: CollectionMode = 'full'): TelemetryRuntime {
  return initObservabilityTelemetry(replayReportConfig(collectionMode));
}
