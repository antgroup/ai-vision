/**
 * File exporters — write OTLP requests as JSONL to local files.
 *
 * FileSpanExporter:  ExportTraceServiceRequest  (spans)
 * FileLogExporter:   ExportLogsServiceRequest   (events/logs)
 *
 * Used only by replay-owned local artifact generation.
 */

import { mkdirSync, appendFileSync, chmodSync } from 'node:fs';
import { dirname } from 'node:path';
import type { ReadableSpan, SpanExporter } from '@opentelemetry/sdk-trace-base';
import type { ReadableLogRecord, LogRecordExporter } from '@opentelemetry/sdk-logs';
import { JsonTraceSerializer, JsonLogsSerializer } from '@opentelemetry/otlp-transformer';

// Inline ExportResultCode to avoid direct @opentelemetry/core dependency
// (removed as a top-level package in OTel SDK v2.x, only available as transitive dep)
const ExportResultCode = { SUCCESS: 0, FAILED: 1 } as const;

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
    } catch (err) {
      console.error('[openclaw-replay] FileSpanExporter export failed:', err);
      resultCallback({ code: ExportResultCode.FAILED, error: err instanceof Error ? err : new Error(String(err)) });
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
      const jsonStr = new TextDecoder().decode(jsonBytes);
      appendFileSync(this.filePath, jsonStr + '\n', { mode: 0o600 });
      resultCallback({ code: ExportResultCode.SUCCESS });
    } catch (err) {
      console.error('[openclaw-replay] FileLogExporter export failed:', err);
      resultCallback({ code: ExportResultCode.FAILED, error: err instanceof Error ? err : new Error(String(err)) });
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
