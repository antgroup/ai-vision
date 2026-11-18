/** Versioned AI Vision exports. Converted Langfuse data is not lossless OTLP. */

import type {
  LangfuseTraceDetail,
  LangfuseObservation,
} from "@/agents/types/langfuse"
import type { OTelSpan, SpanTreeNode } from "@/components/traces-otel/types"

import { inspectTraceStructure } from "./traceIntegrity.ts"

/** CLI/native 对齐的 trace 导出结构。 */
export interface OTelTraceExport {
  schemaVersion: "2.0"
  representation: "native-span" | "normalized-langfuse"
  timeUnit: "unix_nanoseconds_string"
  timezone: "UTC"
  completeness: "complete" | "partial" | "unknown"
  query: ExportQuery | null
  missingParentIds: string[]
  integrityIssues: string[]
  traceId: string
  sessionId: string | null
  spans: OTelSpan[]
  tree: SpanTreeNode[]
}

/**
 * session 导出结构：在 trace 导出之上包一层。
 * traces[] 的每个元素就是 OTelTraceExport，与单独下载 trace 时逐字段一致。
 */
export interface OTelSessionExport {
  schemaVersion: "2.0"
  timeUnit: "unix_nanoseconds_string"
  timezone: "UTC"
  completeness: "complete" | "partial" | "unknown"
  exportedTraceCount: number
  failedTraceIds: string[]
  query: ExportQuery | null
  sessionId: string
  traceCount: number | null
  traces: OTelTraceExport[]
}

export interface ExportQuery {
  from?: string
  to?: string
  filters?: unknown[]
  agentProduct?: string
  agentUuid?: string | null
  timeFormat?: "iso8601_utc"
  timeBoundary?: "fromInclusiveToExclusive"
}

function queryBoundary(value: unknown): string | undefined {
  if (value === undefined || value === null || value === "") return undefined
  const date = new Date(
    typeof value === "number" || /^\d+$/.test(String(value))
      ? Number(value)
      : String(value)
  )
  return Number.isFinite(date.getTime()) ? date.toISOString() : undefined
}

function exportQuery(query?: ExportQuery | null): ExportQuery | null {
  return query
    ? {
        ...query,
        from: queryBoundary(query.from),
        to: queryBoundary(query.to),
        timeFormat: "iso8601_utc",
        timeBoundary: "fromInclusiveToExclusive",
      }
    : null
}

/**
 * 由 parent_span_id 关系重建 span 树。
 * 与后端 SigNoz provider 的 _build_tree 行为保持一致：
 * 父节点不存在于本 trace 时，该 span 视为根。
 */
export function buildSpanTree(spans: OTelSpan[]): SpanTreeNode[] {
  const spanIds = new Set<string>()
  for (const span of spans) {
    if (span.span_id) spanIds.add(span.span_id)
  }

  const childrenMap = new Map<string, string[]>()
  for (const span of spans) {
    const sid = span.span_id
    if (!sid) continue
    const pid = span.parent_span_id
    const key = pid && spanIds.has(pid) ? pid : "__root__"
    const list = childrenMap.get(key)
    if (list) {
      list.push(sid)
    } else {
      childrenMap.set(key, [sid])
    }
  }

  const visited = new Set<string>()
  const buildNode = (spanId: string): SpanTreeNode => {
    visited.add(spanId)
    return {
      spanId,
      children: (childrenMap.get(spanId) ?? [])
        .filter((id) => !visited.has(id))
        .map(buildNode),
    }
  }
  const roots = (childrenMap.get("__root__") ?? []).map(buildNode)
  // Invalid cycles remain visible as a finite tree without altering raw parents.
  for (const id of spanIds) if (!visited.has(id)) roots.push(buildNode(id))
  return roots
}

function isoToNano(value: string | null | undefined): string | undefined {
  if (!value) return undefined
  if (/^-?\d+$/.test(value)) return BigInt(value).toString()
  const match =
    /^(.*T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})$/.exec(value)
  if (!match) throw new Error(`Invalid observation timestamp: ${value}`)
  const secondsMs = Date.parse(match[1] + match[3])
  if (!Number.isFinite(secondsMs))
    throw new Error("Invalid observation timestamp")
  return (
    BigInt(secondsMs) * 1000000n +
    BigInt((match[2] ?? "").padEnd(9, "0"))
  ).toString()
}

/** 仅保留有意义的值，避免把 null/undefined 写进 attributes。 */
function setIfPresent(
  target: Record<string, unknown>,
  key: string,
  value: unknown
): void {
  if (value !== null && value !== undefined) {
    target[key] = value
  }
}

/**
 * 将单条 langfuse observation 反向映射为 OTel span。
 * IO/model/usage/metadata 统一回填到 attributes.gen_ai.* 命名空间，
 * 与 native 上报的 key 对齐。
 */
function observationToSpan(
  obs: LangfuseObservation,
  traceId: string
): OTelSpan {
  const attributes: Record<string, unknown> = {}
  setIfPresent(attributes, "gen_ai.span.kind", obs.type)
  setIfPresent(attributes, "gen_ai.request.model", obs.model)
  setIfPresent(attributes, "gen_ai.input.messages", obs.input)
  setIfPresent(attributes, "gen_ai.output.messages", obs.output)
  setIfPresent(attributes, "langfuse.level", obs.level)
  setIfPresent(attributes, "langfuse.observation.type", obs.type)
  // Keep the source object so conversion does not silently discard metadata.
  attributes["aivision.source.langfuse.observation"] = obs
  if (obs.usage) {
    setIfPresent(attributes, "gen_ai.usage.input_tokens", obs.usage.input)
    setIfPresent(attributes, "gen_ai.usage.output_tokens", obs.usage.output)
    setIfPresent(attributes, "gen_ai.usage.total_tokens", obs.usage.total)
  }
  // metadata 为对象时平铺进 attributes，否则保留在单独 key。
  if (obs.metadata && typeof obs.metadata === "object") {
    for (const [key, value] of Object.entries(
      obs.metadata as Record<string, unknown>
    )) {
      setIfPresent(attributes, `langfuse.observation.metadata.${key}`, value)
    }
  } else {
    setIfPresent(attributes, "metadata", obs.metadata)
  }

  return {
    trace_id: traceId,
    span_id: obs.id,
    parent_span_id: obs.parentObservationId ?? "",
    name: obs.name ?? "",
    kind: "1",
    status: { code: obs.level === "ERROR" ? "2" : "0", message: "" },
    start_time_unix_nano: isoToNano(obs.startTime),
    end_time_unix_nano: isoToNano(obs.endTime),
    attributes,
    resource: {},
  }
}

/**
 * native（SigNoz）详情 → 导出结构。
 * detail=1 返回的 data 已是目标格式，直接透传。
 */
export function buildNativeTraceExport(detail: {
  traceId: string
  sessionId?: string | null
  spans: OTelSpan[]
  tree?: SpanTreeNode[]
  meta?: {
    complete: boolean
    expectedSpanCount?: number
    from?: string
    to?: string
  }
}): OTelTraceExport {
  const { spanCount, missingParentIds, integrityIssues } =
    inspectTraceStructure(detail.spans)
  return {
    schemaVersion: "2.0",
    representation: "native-span",
    timeUnit: "unix_nanoseconds_string",
    timezone: "UTC",
    completeness:
      missingParentIds.length || integrityIssues.length
        ? "partial"
        : detail.meta?.complete && detail.meta.expectedSpanCount === spanCount
          ? "complete"
          : "unknown",
    query: exportQuery(
      detail.meta ? { from: detail.meta.from, to: detail.meta.to } : null
    ),
    missingParentIds,
    integrityIssues: [...new Set(integrityIssues)],
    traceId: detail.traceId,
    sessionId: detail.sessionId ?? null,
    spans: detail.spans.map((span) => ({
      ...span,
      start_time_unix_nano: isoToNano(span.start_time_unix_nano),
      end_time_unix_nano: isoToNano(span.end_time_unix_nano),
      ...(span.events
        ? {
            events: span.events.map((event) => ({
              ...event,
              time_unix_nano: isoToNano(event.time_unix_nano),
            })),
          }
        : {}),
    })),
    tree: buildSpanTree(detail.spans ?? []),
  }
}

/**
 * langfuse（lf）trace 详情 → CLI 对齐的导出结构。
 */
export function buildLangfuseTraceExport(
  trace: LangfuseTraceDetail
): OTelTraceExport {
  const spans = (trace.observations ?? []).map((obs) =>
    observationToSpan(obs, trace.id)
  )
  return {
    ...buildNativeTraceExport({
      traceId: trace.id,
      sessionId: trace.sessionId,
      spans,
    }),
    representation: "normalized-langfuse",
  }
}

/** 去除文件名中的非法/危险字符，仅保留安全集合。 */
export function sanitizeTraceFilename(traceId: string): string {
  const safe = traceId.replace(/[^a-zA-Z0-9._-]/g, "_")
  return safe || "trace"
}

/**
 * 在 trace 导出之上组装 session 导出结构。
 * traceExports 由各套 build*TraceExport 逐 trace 产出（失败的已在外层跳过）。
 */
export function buildSessionExport(
  sessionId: string,
  traceCount: number | null,
  traceExports: OTelTraceExport[],
  context: { requestedTraceIds?: string[]; query?: ExportQuery } = {}
): OTelSessionExport {
  if (
    !traceExports.length &&
    (traceCount !== 0 || context.requestedTraceIds?.length)
  ) {
    throw new Error("No traces could be exported")
  }
  const ids = new Set(traceExports.map((trace) => trace.traceId))
  const failedTraceIds = [...new Set(context.requestedTraceIds ?? [])].filter(
    (id) => !ids.has(id)
  )
  const partial =
    failedTraceIds.length > 0 ||
    (traceCount !== null && traceCount !== ids.size) ||
    traceExports.some((trace) => trace.completeness === "partial")
  return {
    schemaVersion: "2.0",
    timeUnit: "unix_nanoseconds_string",
    timezone: "UTC",
    completeness: partial
      ? "partial"
      : traceCount === null ||
          traceExports.some((trace) => trace.completeness === "unknown")
        ? "unknown"
        : "complete",
    exportedTraceCount: ids.size,
    failedTraceIds,
    query: exportQuery(context.query),
    sessionId,
    traceCount,
    traces: traceExports,
  }
}

/**
 * 受限并发地对 items 执行异步 fn，保持结果与输入同序。
 * 单项抛错时该项解析为 null（容错跳过），不影响其余项。
 */
export async function mapWithConcurrency<T, R>(
  items: T[],
  limit: number,
  fn: (item: T, index: number) => Promise<R>
): Promise<(R | null)[]> {
  const results: (R | null)[] = new Array(items.length).fill(null)
  const concurrency = Math.max(1, Math.min(limit, items.length))
  let cursor = 0

  const worker = async (): Promise<void> => {
    while (true) {
      const current = cursor
      cursor += 1
      if (current >= items.length) return
      try {
        results[current] = await fn(items[current], current)
      } catch {
        results[current] = null
      }
    }
  }

  await Promise.all(Array.from({ length: concurrency }, () => worker()))
  return results
}

/**
 * 触发浏览器下载 JSON 文件（原生 Blob API，无第三方依赖）。
 */
export function downloadJson(filename: string, data: unknown): void {
  const json = JSON.stringify(data, null, 2)
  const blob = new Blob([json], { type: "application/json" })
  const url = URL.createObjectURL(blob)
  try {
    const anchor = document.createElement("a")
    anchor.href = url
    anchor.download = filename
    document.body.appendChild(anchor)
    anchor.click()
    document.body.removeChild(anchor)
  } finally {
    URL.revokeObjectURL(url)
  }
}
