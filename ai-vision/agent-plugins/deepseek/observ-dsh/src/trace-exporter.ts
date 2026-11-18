import type { Attributes } from '@opentelemetry/api';
import type { ReadableSpan, SpanExporter } from '@opentelemetry/sdk-trace-base';
import { JsonTraceSerializer } from '@opentelemetry/otlp-transformer';
import { mediaBlock } from '../../../shared/media-content.mjs';
import { truncateText } from './privacy.js';

// Leave room below the data plane's 64 MiB request limit for proxy differences.
const MAX_REQUEST_BYTES = 60 * 1024 * 1024;
const MAX_TEXT_BYTES = 65536;
type ExportCallback = Parameters<SpanExporter['export']>[1];

function serializedBytes(spans: ReadableSpan[]): number {
  return JsonTraceSerializer.serializeRequest(spans)?.byteLength ?? Number.POSITIVE_INFINITY;
}

function omitMedia(value: unknown, depth = 0): unknown {
  if (typeof value === 'string') return truncateText(value, MAX_TEXT_BYTES);
  if (depth > 16 || value === null || typeof value !== 'object') return value;
  const media = mediaBlock(value);
  const source = media?.source as Record<string, unknown> | undefined;
  if (typeof source?.data === 'string') return { type: 'aivision_media_error', reason: 'budget' };
  if (Array.isArray(value)) return value.map(item => omitMedia(item, depth + 1));
  return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, omitMedia(item, depth + 1)]));
}

function boundedAttributes(attributes: Attributes): Attributes {
  return Object.fromEntries(Object.entries(attributes).map(([key, value]) => {
    if (typeof value !== 'string' || Buffer.byteLength(value) <= MAX_TEXT_BYTES) return [key, value];
    try {
      const projected = JSON.stringify(omitMedia(JSON.parse(value)));
      if (projected !== value) return [key, projected];
    } catch {
      // Ordinary attributes may contain non-JSON text.
    }
    return [key, truncateText(value, MAX_TEXT_BYTES)];
  }));
}

function boundedSpan(span: ReadableSpan, originalBytes?: number): ReadableSpan {
  return Object.assign(Object.create(span), {
    attributes: {
      ...boundedAttributes(span.attributes),
      'agentic.span.truncated': true,
      ...(originalBytes === undefined ? {} : { 'agentic.span.original_length': originalBytes }),
      'agentic.span.size_limit': MAX_REQUEST_BYTES,
      'agentic.span.truncation.reason': 'size_budget',
    },
  });
}

/** Preserve count-based batching for small spans and split large wire requests. */
export class BoundedTraceExporter implements SpanExporter {
  constructor(private readonly exporter: SpanExporter) {}

  export(spans: ReadableSpan[], callback: ExportCallback): void {
    this.send(spans).then(
      error => callback(error ? { code: 1, error } : { code: 0 }),
      error => callback({ code: 1, error: error instanceof Error ? error : new Error(String(error)) }),
    );
  }

  private async send(spans: ReadableSpan[]): Promise<Error | undefined> {
    let batch: ReadableSpan[] = [];
    let batchBytes = 0;
    let failure: Error | undefined;
    for (let span of spans) {
      // Avoid building a giant temporary JSON string just to discover overflow.
      const contentBytes = Object.values(span.attributes).reduce<number>((total, value) =>
        total + (typeof value === 'string' ? Buffer.byteLength(value) : 0), 0);
      let size = contentBytes > MAX_REQUEST_BYTES ? Number.POSITIVE_INFINITY : serializedBytes([span]);
      if (size > MAX_REQUEST_BYTES) {
        span = boundedSpan(span, Number.isFinite(size) ? size : undefined);
        size = serializedBytes([span]);
        if (size > MAX_REQUEST_BYTES) {
          failure ??= new Error('Trace span exceeds the serialized request limit');
          continue;
        }
      }
      // Singleton request sizes include repeated wrappers, so their sum bounds
      // the shared resource/scope request without serializing a huge batch.
      if (batch.length && batchBytes + size > MAX_REQUEST_BYTES) {
        const error = await this.exportBatch(batch);
        failure ??= error;
        batch = [];
        batchBytes = 0;
      }
      batch.push(span);
      batchBytes += size;
    }
    if (batch.length) {
      const error = await this.exportBatch(batch);
      failure ??= error;
    }
    return failure;
  }

  private exportBatch(spans: ReadableSpan[]): Promise<Error | undefined> {
    if (serializedBytes(spans) > MAX_REQUEST_BYTES) return Promise.resolve(new Error('Trace batch exceeds the serialized request limit'));
    return new Promise(resolve => {
      this.exporter.export(spans, result => resolve(result.code === 0 ? undefined : result.error ?? new Error('Trace export failed')));
    });
  }

  forceFlush(): Promise<void> {
    return this.exporter.forceFlush?.() ?? Promise.resolve();
  }

  shutdown(): Promise<void> {
    return this.exporter.shutdown();
  }
}
