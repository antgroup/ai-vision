/**
 * Replay-owned file exporters — write OTLP requests as JSONL to local files.
 *
 * FileSpanExporter:  ExportTraceServiceRequest  (spans)
 * FileLogExporter:   ExportLogsServiceRequest   (events/logs)
 * FileMetricExporter: ExportMetricsServiceRequest (metrics)
 *
 * Used only by replay-owned local artifact generation.
 */

import { mkdirSync, appendFileSync, chmodSync } from 'node:fs';
import { dirname } from 'node:path';
import type { ReadableSpan, SpanExporter } from '@opentelemetry/sdk-trace-base';
import type { ReadableLogRecord, LogRecordExporter } from '@opentelemetry/sdk-logs';
import type { PushMetricExporter, ResourceMetrics } from '@opentelemetry/sdk-metrics';
import { JsonTraceSerializer, JsonLogsSerializer, JsonMetricsSerializer } from '@opentelemetry/otlp-transformer';
import { ExportResultCode } from '@opentelemetry/core';
import { log, logError } from './logger.js';

function ensurePrivateParent(filePath: string): void {
  const dir = dirname(filePath);
  const created = mkdirSync(dir, { recursive: true, mode: 0o700 });
  if (created !== undefined) chmodSync(dir, 0o700);
}

export class FileSpanExporter implements SpanExporter {
  private readonly filePath: string;
  private dirEnsured = false;

  constructor(filePath: string) {
    this.filePath = filePath;
  }

  export(
    spans: ReadableSpan[],
    resultCallback: (result: { code: number; error?: Error }) => void,
  ): void {
    try {
      this.ensureDir();
      const jsonBytes = JsonTraceSerializer.serializeRequest(spans);
      const jsonStr = new TextDecoder().decode(jsonBytes);
      appendFileSync(this.filePath, jsonStr + '\n', { mode: 0o600 });
      log(`FileSpanExporter: wrote ${spans.length} span(s) to ${this.filePath}`);
      resultCallback({ code: 0 });
    } catch (err) {
      logError('FileSpanExporter export failed', err);
      resultCallback({ code: 1, error: err instanceof Error ? err : new Error(String(err)) });
    }
  }

  async shutdown(): Promise<void> {
    log('FileSpanExporter: shutdown');
  }

  async forceFlush(): Promise<void> {}

  private ensureDir(): void {
    if (!this.dirEnsured) {
      ensurePrivateParent(this.filePath);
      this.dirEnsured = true;
    }
  }
}

export class FileLogExporter implements LogRecordExporter {
  private readonly filePath: string;
  private dirEnsured = false;

  constructor(filePath: string) {
    this.filePath = filePath;
  }

  export(
    logRecords: ReadableLogRecord[],
    resultCallback: (result: { code: number; error?: Error }) => void,
  ): void {
    try {
      this.ensureDir();
      const jsonBytes = JsonLogsSerializer.serializeRequest(logRecords);
      const jsonStr = new TextDecoder().decode(jsonBytes);
      appendFileSync(this.filePath, jsonStr + '\n', { mode: 0o600 });
      log(`FileLogExporter: wrote ${logRecords.length} log record(s) to ${this.filePath}`);
      resultCallback({ code: ExportResultCode.SUCCESS });
    } catch (err) {
      logError('FileLogExporter export failed', err);
      resultCallback({ code: ExportResultCode.FAILED, error: err instanceof Error ? err : new Error(String(err)) });
    }
  }

  async shutdown(): Promise<void> {
    log('FileLogExporter: shutdown');
  }

  async forceFlush(): Promise<void> {}

  private ensureDir(): void {
    if (!this.dirEnsured) {
      ensurePrivateParent(this.filePath);
      this.dirEnsured = true;
    }
  }
}

export class FileMetricExporter implements PushMetricExporter {
  private readonly filePath: string;
  private dirEnsured = false;

  constructor(filePath: string) {
    this.filePath = filePath;
  }

  export(
    metrics: ResourceMetrics,
    resultCallback: (result: { code: number; error?: Error }) => void,
  ): void {
    try {
      this.ensureDir();
      const jsonBytes = JsonMetricsSerializer.serializeRequest(metrics);
      const jsonStr = new TextDecoder().decode(jsonBytes);
      appendFileSync(this.filePath, jsonStr + '\n', { mode: 0o600 });
      log(`FileMetricExporter: wrote metrics to ${this.filePath}`);
      resultCallback({ code: ExportResultCode.SUCCESS });
    } catch (err) {
      logError('FileMetricExporter export failed', err);
      resultCallback({ code: ExportResultCode.FAILED, error: err instanceof Error ? err : new Error(String(err)) });
    }
  }

  async shutdown(): Promise<void> {
    log('FileMetricExporter: shutdown');
  }

  async forceFlush(): Promise<void> {}

  private ensureDir(): void {
    if (!this.dirEnsured) {
      ensurePrivateParent(this.filePath);
      this.dirEnsured = true;
    }
  }
}
