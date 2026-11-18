/**
 * HTTP trace header interceptor using AsyncLocalStorage.
 *
 * Monkey-patches `http.Server.prototype.emit` to capture incoming
 * SOFA / W3C / B3 trace headers from each HTTP request and stores them
 * in AsyncLocalStorage for zero-invasion access from hooks.
 */

import http from 'node:http';
import { AsyncLocalStorage } from 'node:async_hooks';

// ─── Types ──────────────────────────────────────────────────────────────────

export interface UpstreamTraceHeaders {
  // SOFA
  'sofa-traceid'?: string;
  'sofa-rpcid'?: string;
  'sofa-penattrs'?: string;

  // W3C Trace Context
  traceparent?: string;
  tracestate?: string;

  // B3 (Zipkin)
  'x-b3-traceid'?: string;
  'x-b3-spanid'?: string;
  'x-b3-parentspanid'?: string;
  'x-b3-sampled'?: string;
  'x-b3-flags'?: string;

  // Generic
  'x-request-id'?: string;
}

// Header keys we capture (lowercased for case-insensitive matching)
const TRACE_HEADER_KEYS: ReadonlyArray<keyof UpstreamTraceHeaders> = [
  'sofa-traceid',
  'sofa-rpcid',
  'sofa-penattrs',
  'traceparent',
  'tracestate',
  'x-b3-traceid',
  'x-b3-spanid',
  'x-b3-parentspanid',
  'x-b3-sampled',
  'x-b3-flags',
  'x-request-id',
];

// ─── Storage ────────────────────────────────────────────────────────────────

const traceStore = new AsyncLocalStorage<UpstreamTraceHeaders>();

let installed = false;

// ─── Public API ─────────────────────────────────────────────────────────────

/**
 * Install the HTTP request trace interceptor.
 *
 * Safe to call multiple times — only installs once.
 */
export function installTraceInterceptor(): void {
  if (installed) return;
  installed = true;

  const originalEmit = http.Server.prototype.emit;

  // eslint-disable-next-line func-names -- monkey-patch requires named function for stack traces
  http.Server.prototype.emit = function (
    this: http.Server,
    event: string,
    ...args: unknown[]
  ): boolean {
    if (event !== 'request') {
      return originalEmit.apply(this, [event, ...args] as Parameters<typeof originalEmit>);
    }

    const req = args[0] as http.IncomingMessage;
    const headers: UpstreamTraceHeaders = {};
    let hasAny = false;

    for (const key of TRACE_HEADER_KEYS) {
      const value = req.headers[key];
      if (typeof value === 'string' && value.length > 0) {
        headers[key] = value;
        hasAny = true;
      }
    }

    if (hasAny) {
      const self = this;
      return traceStore.run(headers, () =>
        originalEmit.apply(self, [event, ...args] as Parameters<typeof originalEmit>),
      );
    }

    return originalEmit.apply(this, [event, ...args] as Parameters<typeof originalEmit>);
  } as typeof originalEmit;
}

/**
 * Get upstream trace headers captured from the current HTTP request.
 * Returns `undefined` if no trace headers were found or outside request scope.
 */
export function getTraceContext(): UpstreamTraceHeaders | undefined {
  return traceStore.getStore();
}

/** Convenience: get SOFA trace ID from current request context */
export function getSofaTraceId(): string | undefined {
  return traceStore.getStore()?.['sofa-traceid'];
}

/** Convenience: get SOFA RPC ID from current request context */
export function getSofaRpcId(): string | undefined {
  return traceStore.getStore()?.['sofa-rpcid'];
}
