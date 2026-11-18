/**
 * Replay-owned telemetry construction.
 *
 * The offline replay reuses observ-dsh's provider construction with
 * file exporters instead of remote OTLP exporters. The only network path a
 * replay may ever touch is the fixed local collector (see local-collector.ts).
 */

import { SimpleSpanProcessor } from '@opentelemetry/sdk-trace-base';
import { PeriodicExportingMetricReader } from '@opentelemetry/sdk-metrics';
import { SimpleLogRecordProcessor } from '@opentelemetry/sdk-logs';
import type { ObservDshConfig } from 'observ-dsh/core';
import { initTelemetry, type TelemetryExtensions, type TelemetryRuntime } from 'observ-dsh/http-runtime';
import { FileLogExporter, FileMetricExporter, FileSpanExporter } from './file-exporter.js';

export interface FileOutputSet {
  spans: string;
  events?: string;
  metrics?: string;
}

export function replayOwnedConfig(
  collectionMode: 'full' | 'privacy',
  outputs: { traces: boolean; metrics: boolean; events: boolean },
): ObservDshConfig {
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
    serviceName: 'dsh-replay',
    collectionMode,
    debug: false,
    dynamicUsername: false,
    dynamicUid: false,
    log: { events: { ...content }, spans: { ...content } },
    otelExporter: {
      protocol: 'http/json',
      headers: {},
      attributes: { 'ant.username': 'replay' },
      resourceAttributes: {
        'ant.agent.type': 'dsh',
        'ant.agent.product': 'DeepSeekHarness',
        'ant.agent.instance': 'replay',
        'ant.agent.env': 'Default',
        'ant.agent.scene': 'default',
        'agentic.collection.mode': collectionMode,
      },
      metricsTemporalityPreference: 'delta',
    },
    tracesConfig: { enabled: outputs.traces, headers: {} },
    metricsConfig: { enabled: outputs.metrics, headers: {} },
    eventsConfig: { enabled: outputs.events, headers: {} },
    metricExportInterval: 60000,
    logsExportInterval: 5000,
  };
}

export function fileRuntimeExtensions(outputs: FileOutputSet): TelemetryExtensions {
  return {
    spanProcessors: [new SimpleSpanProcessor(new FileSpanExporter(outputs.spans))],
    ...(outputs.events
      ? { logRecordProcessors: [new SimpleLogRecordProcessor({ exporter: new FileLogExporter(outputs.events) })] }
      : {}),
    ...(outputs.metrics
      ? {
          metricReaders: [new PeriodicExportingMetricReader({
            exporter: new FileMetricExporter(outputs.metrics),
            exportIntervalMillis: 60000,
          })],
        }
      : {}),
  };
}

export function initReplayTelemetry(
  outputs: FileOutputSet,
  collectionMode: 'full' | 'privacy' = 'full',
  transcriptCwd?: string,
): TelemetryRuntime {
  const config = replayOwnedConfig(collectionMode, {
    traces: Boolean(outputs.spans),
    metrics: Boolean(outputs.metrics),
    events: Boolean(outputs.events),
  });
  return initTelemetry(config, fileRuntimeExtensions(outputs), transcriptCwd);
}
