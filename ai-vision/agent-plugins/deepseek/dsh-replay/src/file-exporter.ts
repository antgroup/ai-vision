/**
 * Replay-owned file exporters — write OTLP requests as JSONL to local files.
 *
 * These exporters are used only by dsh-replay. The production plugin never
 * imports this module and never writes transcripts or replay artifacts.
 */

import { mkdirSync, appendFileSync, chmodSync } from 'node:fs';
import { dirname } from 'node:path';
import type { ReadableSpan, SpanExporter } from '@opentelemetry/sdk-trace-base';
import type { ReadableLogRecord, LogRecordExporter } from '@opentelemetry/sdk-logs';
import type { PushMetricExporter, ResourceMetrics } from '@opentelemetry/sdk-metrics';
import { JsonTraceSerializer, JsonLogsSerializer, JsonMetricsSerializer } from '@opentelemetry/otlp-transformer';
import { ExportResultCode } from '@opentelemetry/core';

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
      resultCallback({ code: ExportResultCode.SUCCESS });
    } catch (error) {
      resultCallback({ code: ExportResultCode.FAILED, error: error instanceof Error ? error : new Error(String(error)) });
    }
  }

  async shutdown(): Promise<void> {}
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
      appendFileSync(this.filePath, new TextDecoder().decode(jsonBytes) + '\n', { mode: 0o600 });
      resultCallback({ code: ExportResultCode.SUCCESS });
    } catch (error) {
      resultCallback({ code: ExportResultCode.FAILED, error: error instanceof Error ? error : new Error(String(error)) });
    }
  }

  async shutdown(): Promise<void> {}
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
      appendFileSync(this.filePath, new TextDecoder().decode(jsonBytes) + '\n', { mode: 0o600 });
      resultCallback({ code: ExportResultCode.SUCCESS });
    } catch (error) {
      resultCallback({ code: ExportResultCode.FAILED, error: error instanceof Error ? error : new Error(String(error)) });
    }
  }

  async shutdown(): Promise<void> {}
  async forceFlush(): Promise<void> {}

  private ensureDir(): void {
    if (!this.dirEnsured) {
      ensurePrivateParent(this.filePath);
      this.dirEnsured = true;
    }
  }
}
