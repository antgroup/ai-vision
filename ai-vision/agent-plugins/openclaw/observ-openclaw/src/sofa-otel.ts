/**
 * SOFA-to-OTel trace context conversion.
 *
 * Converts SOFA TraceId / RpcId into OTel-compatible trace/span IDs
 * and builds OTel span links for upstream SOFA traces.
 *
 * Reference: Python sofaaipy trace propagation implementation.
 */

import { TraceFlags, type Link, type SpanContext } from '@opentelemetry/api';
import { createHash } from 'node:crypto';

// ─── Constants ──────────────────────────────────────────────────────────────

/** Span link role: current span is a child of the linked span */
export const SPAN_LINK_ROLE_AS_CHILD = 'child' as const;
/** Span link role: current span is a parent of the linked span */
export const SPAN_LINK_ROLE_AS_PARENT = 'parent' as const;

// ─── SOFA Trace Context ─────────────────────────────────────────────────────

export interface SofaTraceContext {
  /** Original SOFA trace ID */
  sofaTraceId: string;
  /** Original SOFA RPC ID */
  sofaRpcId: string;
  /** OTel-compatible 32-hex-char trace ID (derived from sofaTraceId) */
  otelTraceId: string;
  /** OTel-compatible 16-hex-char span ID (derived from sofaRpcId) */
  otelSpanId: string;
}

// ─── Conversion helpers ─────────────────────────────────────────────────────

/**
 * Convert a SOFA TraceId to an OTel trace ID (32 hex chars).
 *
 * If the SOFA TraceId is already a valid 32-char hex string, use it directly.
 * Otherwise, hash it with MD5 to produce a deterministic 32-char hex ID.
 */
export function sofaTraceIdToOtel(sofaTraceId: string): string {
  const cleaned = sofaTraceId.trim();

  // Already a valid OTel trace ID?
  if (/^[0-9a-f]{32}$/i.test(cleaned)) {
    return cleaned.toLowerCase();
  }

  // Hash to produce a deterministic 32-char hex ID
  return createHash('md5').update(cleaned).digest('hex');
}

/**
 * Convert a SOFA RpcId to an OTel span ID (16 hex chars).
 *
 * SOFA RpcId is a dot-separated path like "0.1.2". We hash it with MD5
 * and take the first 16 hex chars to produce a deterministic span ID.
 */
export function sofaRpcIdToOtel(sofaRpcId: string): string {
  const cleaned = sofaRpcId.trim();

  // Already a valid OTel span ID?
  if (/^[0-9a-f]{16}$/i.test(cleaned)) {
    return cleaned.toLowerCase();
  }

  // Hash and take first 16 hex chars
  return createHash('md5').update(cleaned).digest('hex').slice(0, 16);
}

// ─── Public API ─────────────────────────────────────────────────────────────

/**
 * Parse SOFA trace headers into a SofaTraceContext with OTel-compatible IDs.
 *
 * Returns `undefined` if either sofaTraceId or sofaRpcId is missing/empty.
 */
export function parseSofaTraceContext(
  sofaTraceId: string | undefined,
  sofaRpcId: string | undefined,
): SofaTraceContext | undefined {
  if (!sofaTraceId?.trim() || !sofaRpcId?.trim()) {
    return undefined;
  }

  return {
    sofaTraceId: sofaTraceId.trim(),
    sofaRpcId: sofaRpcId.trim(),
    otelTraceId: sofaTraceIdToOtel(sofaTraceId),
    otelSpanId: sofaRpcIdToOtel(sofaRpcId),
  };
}

/**
 * Build an OTel span link from a SofaTraceContext.
 *
 * The link contains only the upstream SpanContext (traceId + spanId)
 * with TraceFlags.SAMPLED set, matching the Python reference implementation.
 */
export function buildSofaSpanLink(
  stc: SofaTraceContext,
  role:
    | typeof SPAN_LINK_ROLE_AS_CHILD
    | typeof SPAN_LINK_ROLE_AS_PARENT = SPAN_LINK_ROLE_AS_CHILD,
): Link {
  const spanContext: SpanContext = {
    traceId: stc.otelTraceId,
    spanId: stc.otelSpanId,
    traceFlags: TraceFlags.SAMPLED,
    isRemote: false,
  };

  return {
    context: spanContext,
    attributes: {
      '_internal.sofa.trace_id': stc.sofaTraceId,
      '_internal.sofa.span_id': stc.sofaRpcId,
      '_internal.span_link.role': role,
    },
  };
}
