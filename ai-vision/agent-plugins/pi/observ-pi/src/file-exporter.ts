import { appendFileSync, chmodSync, mkdirSync } from 'node:fs';
import { dirname } from 'node:path';
import type { ReadableSpan, SpanExporter } from '@opentelemetry/sdk-trace-base';
import type { ReadableLogRecord, LogRecordExporter } from '@opentelemetry/sdk-logs';
import type { PushMetricExporter, ResourceMetrics } from '@opentelemetry/sdk-metrics';
import { JsonTraceSerializer, JsonLogsSerializer, JsonMetricsSerializer } from '@opentelemetry/otlp-transformer';
import { ExportResultCode } from '@opentelemetry/core';

abstract class FileExporter {
  private directoryReady = false;
  private readonly decoder = new TextDecoder();

  constructor(protected readonly file: string) {}

  private ensureDirectory(): void {
    if (this.directoryReady) return;
    const directory = dirname(this.file);
    mkdirSync(directory, { recursive: true, mode: 0o700 });
    chmodSync(directory, 0o700);
    this.directoryReady = true;
  }

  protected write(bytes: Uint8Array): void {
    const append = () => appendFileSync(this.file, `${this.decoder.decode(bytes)}\n`, { mode: 0o600 });
    this.ensureDirectory();
    try {
      append();
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
      this.directoryReady = false;
      this.ensureDirectory();
      append();
    }
  }
  async shutdown(): Promise<void> {}
  async forceFlush(): Promise<void> {}
}
function required(bytes: Uint8Array | undefined): Uint8Array {
  if (!bytes) throw new Error('OTLP serializer returned no payload');
  return bytes;
}
export class FileSpanExporter extends FileExporter implements SpanExporter {
  export(items: ReadableSpan[], done: (result: { code: number; error?: Error }) => void): void { try { this.write(required(JsonTraceSerializer.serializeRequest(items))); done({ code: ExportResultCode.SUCCESS }); } catch (error) { done({ code: ExportResultCode.FAILED, error: error as Error }); } }
}
export class FileLogExporter extends FileExporter implements LogRecordExporter {
  export(items: ReadableLogRecord[], done: (result: { code: number; error?: Error }) => void): void { try { this.write(required(JsonLogsSerializer.serializeRequest(items))); done({ code: ExportResultCode.SUCCESS }); } catch (error) { done({ code: ExportResultCode.FAILED, error: error as Error }); } }
}
export class FileMetricExporter extends FileExporter implements PushMetricExporter {
  export(items: ResourceMetrics, done: (result: { code: number; error?: Error }) => void): void { try { this.write(required(JsonMetricsSerializer.serializeRequest(items))); done({ code: ExportResultCode.SUCCESS }); } catch (error) { done({ code: ExportResultCode.FAILED, error: error as Error }); } }
}
